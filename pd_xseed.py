"""Cross-seed worker: the library's own files seeded on every upload of the same release.

No file is renamed, linked, copied or deleted. In order, the disk read only at the third:
  1. the tracker asked for a release by its name; same name and about the same size go on
  2. the upload's file list: every video at exactly that path with exactly that size
  3. qBittorrent checks it, one per drive, behind viewers, downloads and copies
A check that passes seeds; one that fails is taken out of the client, its files kept.
"""
import json
import os
import re
import threading
import time
import urllib.request

import pd_beat
import pd_disk
import pd_torrents
import pd_tracker

VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov")
#: category in qBittorrent, the one the race page's seeding rules already know
CATEGORY = "cross-seed"
#: seconds between searches: both servers share one tracker account
ASK_EVERY = 60
#: seconds between looks at the checks under way
TICK = 15
#: asked again after this long: a new upload can match, and a file that did not fit may
ASK_AGAIN = 30 * 86400
#: an upload failed to fetch: asked again after this long
ERROR_AGAIN = 86400
#: uploads tried for one name
MOST_HITS = 5
#: the check found all but this many bytes: edge pieces shared with a small file not here,
#: fetched by qBittorrent. More than this is another file, not an edge
EDGE_BYTES = 64 * 1000 * 1000
#: seconds before a finished check is judged, so a state from before it is not read
JUDGE_AFTER = 10
CHECKING = ("checkingUP", "checkingDL", "checkingResumeData", "queuedForChecking", "moving")
SEASON = re.compile(r"^(s\d{1,3}|season[ ._-]*\d{1,3})$", re.I)

STATE = {"root": "", "lib": None, "wait": None, "log": None, "lock": threading.Lock(),
         "paper": None, "asked_at": 0.0}


def plain(name):
    """A release name without its separators and case: the tracker writes dots as spaces."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def candidates(files, skip):
    """Names to ask about, each {local, name, size, at}: release folders first, newest
    first, then each file under a name of its own. files: [(path, size, mtime)]; skip(path)
    is true for a file that may not be seeded from."""
    folders, single = {}, {}
    for path, size, mtime in files:
        if not str(path).lower().endswith(VIDEO) or skip(path):
            continue
        here = os.path.dirname(path)
        if SEASON.match(os.path.basename(here)):
            here = os.path.dirname(here)
        one = folders.setdefault(here, {"local": here, "name": os.path.basename(here),
                                        "size": 0, "at": 0})
        one["size"] += int(size or 0)
        one["at"] = max(one["at"], int(mtime or 0))
        if plain(stem(path)) != plain(one["name"]):
            single[path] = {"local": path, "name": stem(path), "size": int(size or 0),
                            "at": int(mtime or 0)}
    def order(row):
        return -row["at"], row["local"]
    return sorted(folders.values(), key=order) + sorted(single.values(), key=order)


def same_release(row, want):
    """Whether a tracker row is this release: same name and about the same size."""
    if plain(row.get("name")) != plain(want["name"]):
        return False
    size = int(row.get("size") or 0)
    return not size or abs(size - want["size"]) <= max(want["size"] * 0.05, 300 * 1000 * 1000)


def place(local, tfiles, name):
    """Where this torrent's files lie in the library: (root, layout) with every video on the
    disk at that exact name and size, or None. Nothing is ever renamed to make one fit.
    tfiles and name as parse_torrent gives them: the paths are below the torrent's own folder
    and do not name it; a torrent of one file has that file's name for both."""
    single = len(tfiles) == 1 and tfiles[0]["path"] == name
    above = os.path.dirname(local)
    if os.path.isdir(local):
        ways = [(local, "Original", "")] if single else [(above, "Original", name), (local, "NoSubfolder", "")]
    else:
        ways = [(above, "Original", "")] if single else [(above, "Original", name), (above, "NoSubfolder", "")]
    best = None
    for root, layout, folder in ways:
        have = videos = whole = 0
        for f in tfiles:
            rel = os.path.join(folder, f["path"].replace("/", os.sep))
            at = os.path.join(root, rel)
            video = rel.lower().endswith(VIDEO)
            videos += video
            if os.path.isfile(at) and os.path.getsize(at) == f["size"]:
                have += 1
                whole += video
        if videos and whole == videos and (best is None or have > best[2]):
            best = (root, layout, have)
    return best[:2] if best else None


def edges_only(files, pieces):
    """Whether every piece a wanted file is short of is shared with a file switched off: those
    can never read as whole, and fetching them writes back what is there. Any other piece
    short is a different file, of the same size. pieces qBittorrent's states, 2 for held."""
    off = set()
    for f in files:
        if int(f.get("priority", 1)) == 0 and f.get("piece_range"):
            off.update(range(int(f["piece_range"][0]), int(f["piece_range"][1]) + 1))
    for f in files:
        if int(f.get("priority", 1)) > 0:
            first, last = f.get("piece_range") or (0, -1)
            for at in range(int(first), int(last) + 1):
                if at >= len(pieces) or (int(pieces[at]) != 2 and at not in off):
                    return False
    return True


def verdict(files, retried, pieces=None):
    """What a finished check found, from the torrent's wanted files [{progress, size}]:
    ready, edge (seeds once a few MB are fetched), again (the check never ran) or mismatch."""
    want = [f for f in files if int(f.get("priority", 1)) > 0]
    if want and all(float(f["progress"]) >= 1 for f in want):
        return "ready"
    got = sum(float(f["progress"]) * int(f["size"]) for f in want)
    if not got and not retried:
        return "again"
    left = sum((1 - float(f["progress"])) * int(f["size"]) for f in want)
    near = want and got and left <= EDGE_BYTES
    return "edge" if near and (pieces is None or edges_only(files, pieces)) else "mismatch"


def next_checks(rows, busy):
    """Which added torrents to check now: the fewest seeds on each drive not busy."""
    out = {}
    for h, row in sorted(rows.items(), key=lambda kv: int(kv[1].get("seeds") or 0)):
        drive = pd_disk.drive_of(row.get("root"))
        if row.get("state") == "added" and drive not in busy and drive not in out:
            out[drive] = h
    return out


def due(asked, want, now):
    """Whether this name is to be asked about: never asked, or asked long enough ago."""
    was = asked.get(want["local"])
    if not was:
        return True
    return now - float(was.get("at") or 0) >= (ERROR_AGAIN if was.get("error") else ASK_AGAIN)


def _path():
    return os.path.join(STATE["root"], "xseed.json")


def paper():
    """What was asked and tried, read once. A file that cannot be read stops the worker
    rather than starting empty and doing it all again."""
    with STATE["lock"]:
        if STATE["paper"] is None:
            try:
                with open(_path(), encoding="utf-8") as f:
                    said = json.load(f)
            except FileNotFoundError:
                said = {}
            STATE["paper"] = {"asked": said.get("asked") or {}, "torrents": said.get("torrents") or {}}
        return STATE["paper"]


def _write():
    with STATE["lock"]:
        keep = _path() + ".new"
        with open(keep, "w", encoding="utf-8") as f:
            json.dump(STATE["paper"], f)
        os.replace(keep, _path())


def _say(text):
    if STATE["log"]:
        STATE["log"]("cross-seed: " + text)


def library_files():
    """[(path, size, mtime)] of whole files here that may be seeded from."""
    con = STATE["lib"]().db()
    try:
        return [(r["path"], r["size"], r["mtime"]) for r in con.execute(
            "SELECT path, size, mtime FROM file WHERE COALESCE(hollow, 0) = 0")]
    finally:
        con.close()


def skipper(data, copies):
    """skip(path): files another job owns. A copy can be cleared, a race deleted when
    retired, and a pack's film is seeded by its pack and had no upload of its own (0 of
    318, 2026-10-29)."""
    packs = [os.sep + str(p.get("name") or "") + os.sep for p in data.get("packs") or [] if p.get("name")]
    copies = os.path.normcase(str(copies or ""))

    def skip(path):
        low = os.path.normcase(str(path))
        if copies and low.startswith(copies):
            return True
        if os.sep + "seed" + os.sep in low:
            return True
        return any(p in path for p in packs)
    return skip


def _add(qb, raw, want, row, now):
    """One upload's torrent: added stopped over the files where it fits. The torrent's row,
    or None when it is already in the client."""
    h, name, tfiles = pd_torrents.parse_torrent(raw)
    h = h.lower()
    rows = paper()["torrents"]
    if rows.get(h, {}).get("state") == "mismatch":
        return None                      # read once and it was another file
    if qb.info(h):
        rows.setdefault(h, {"state": "already", "name": row["name"], "at": now})
        return None
    where = place(want["local"], tfiles, name)
    if not where:
        rows[h] = {"state": "no-fit", "name": row["name"], "at": now}
        return None
    root, layout = where
    # on paper first: added and not written, a restart left it stopped in the client for good
    rows[h] = {"state": "added", "name": row["name"], "root": root, "layout": layout,
               "seeds": int(row.get("seeds") or 0), "at": now}
    _write()
    qb.add_at(raw, root, CATEGORY, layout)
    got = None
    for _ in range(20):
        got = qb.files(h) if qb.info(h) else None
        if got:
            break
        time.sleep(1)
    if not got:
        rows[h] = {"state": "no-add", "name": row["name"], "at": now}
        return None
    # what is not on the disk at that very size is never fetched: a subtitle of another size
    # stayed wanted, and would have been written over the library's own
    def here(f):
        at = os.path.join(root, str(f["name"]).replace("/", os.sep))
        return os.path.isfile(at) and os.path.getsize(at) == int(f.get("size") or 0)
    off = [f["index"] for f in got if not here(f)]
    if off:
        qb.priority(h, off, 0)
    _say("added %s" % row["name"][:70])
    return rows[h]


def ask_next(qb, now):
    """One search: the next name due, and every upload of that release tried. False when
    there was nothing to ask, or nothing could be."""
    book = paper()
    if not pd_tracker.jackett()[1] or not pd_tracker.room("search"):
        return False
    data = pd_torrents.load()
    import pd_follow
    copies = pd_follow.settings(STATE["lib"]().config()).get("folder") or ""
    want = next((w for w in candidates(library_files(), skipper(data, copies))
                 if due(book["asked"], w, now)), None)
    if not want:
        return False
    pd_beat.beat("xseed", "asking: " + want["name"][:50], 300)
    rows = pd_tracker.quietly(pd_tracker.ask_jackett)(re.sub(r"[._]+", " ", want["name"]))
    if pd_tracker.STATE.get("why"):
        return False                     # the indexer did not answer: not a no
    hits = [r for r in rows if same_release(r, want)][:MOST_HITS]
    said = {"at": now, "hits": len(hits)}
    for row in hits:
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    row["url"], headers={"User-Agent": "Palladium"}), timeout=60) as answer:
                _add(qb, answer.read(), want, row, now)
        except Exception as why:
            said["error"] = str(why)[:120]
            _say("could not fetch %s: %s" % (row["name"][:50], str(why)[:80]))
    book["asked"][want["local"]] = said
    _write()
    return True


def _busy(states):
    """Drives with a check already reading: one torrent per drive."""
    return {pd_disk.drive_of(t.get("save_path")) for t in states.values()
            if str(t.get("state")) in CHECKING}


def judge(qb, states, now):
    """Finished checks read, and what they found done. Files are never touched."""
    rows = paper()["torrents"]
    changed = False
    for h, row in rows.items():
        if row.get("state") != "checking" or now - float(row.get("checkAt") or 0) < JUDGE_AFTER:
            continue
        t = states.get(h)
        if not t:
            row["state"] = "gone"
            changed = True
            continue
        if str(t.get("state")) in CHECKING:
            continue
        found = verdict(qb.files(h), row.get("retried"), qb.pieces(h))
        changed = True
        if found == "again":
            row.update(state="added", retried=1)
        elif found in ("ready", "edge"):
            qb._call("/api/v2/torrents/setShareLimits", fields={
                "hashes": h, "ratioLimit": "-1", "seedingTimeLimit": "-1",
                "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Default"})
            qb.start(h)
            row["state"] = "seeding"
            _say("seeding %s" % str(row.get("name"))[:70])
        else:
            qb.remove(h, files=False)
            row["state"] = "mismatch"
            _say("not the same file, taken out, files kept: %s" % str(row.get("name"))[:70])
    if changed:
        _write()


def start_checks(qb, states, now):
    """The next check on each drive free for one. A check waits for the disk's line, and
    for a viewer short of disk on the drive it would read."""
    rows = paper()["torrents"]
    for drive, h in next_checks(rows, _busy(states)).items():
        row = rows[h]
        if STATE["wait"](drive):
            continue
        ident, why = pd_disk.ask(row["root"], "check", "Cross-seed: " + str(row.get("name"))[:50])
        if why:
            pd_disk.done(ident)
            continue
        try:
            if h in states:
                qb.recheck(h)
                row.update(state="checking", checkAt=now)
            else:
                row["state"] = "gone"
            _write()
        finally:
            pd_disk.done(ident)            # qBittorrent's check is counted from outside now


def one_round(qb, now):
    states = {str(t.get("hash") or "").lower(): t for t in qb.all_info()}
    judge(qb, states, now)
    start_checks(qb, states, now)
    if now - STATE["asked_at"] >= ASK_EVERY and not STATE["wait"](""):
        STATE["asked_at"] = now
        ask_next(qb, now)


def tally():
    """(seeding, waiting to be checked) for the Workers box."""
    rows = paper()["torrents"].values()
    return (sum(1 for r in rows if r.get("state") == "seeding"),
            sum(1 for r in rows if r.get("state") in ("added", "checking")))


def start(root, lib, wait, log):
    """root: where xseed.json lives; lib() the library; wait(drive) a reason to hold off;
    log(text) a line in the debug log."""
    STATE.update(root=root, lib=lib, wait=wait, log=log)

    def work():
        pd_beat.sleep("xseed", 300, "first look")
        while True:
            try:
                why = wait("")
                if why == "off":
                    pd_beat.sleep("xseed", 300, "off")
                    continue
                pd_beat.beat("xseed", "checks", 600)
                one_round(pd_torrents.QB(pd_torrents.load()["config"]), time.time())
                pd_beat.sleep("xseed", TICK)
            except Exception as why:
                _say("failed: %s" % str(why)[:150])
                pd_beat.sleep("xseed", 300, "failed, again soon")

    pd_beat.counter("xseed", tally)
    threading.Thread(target=work, name="palladium-xseed", daemon=True).start()
