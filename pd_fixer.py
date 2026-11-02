"""File fixer: films in the library that are broken, mended where they can be.

Broken here means the empty shell of a download that never finished: the right name,
the right size, the film's first pieces and nothing after them. It reads as the film and
plays a moment. Each library file is sampled once a month, one at a time, as background
work. A broken one is mended, in this order:

    a whole copy of the same file elsewhere in the library   a hard link to it in its place
    qBittorrent holding its torrent                          checked again, then the missing
                                                             parts fetched
    the same release on the tracker                          added over the file, so only the
                                                             missing parts are fetched

What was found and done is written to fixer.json and the debug log.
"""
import json
import os
import threading
import time
import urllib.request

#: how long before a file that was whole is sampled again
CHECK_AGAIN = 30 * 86400
#: seconds between one file and the next: a sweep is days, and the disks hardly notice
BETWEEN = 20

STATE = {"root": "", "lib": None, "wait": None, "log": None, "paper": None}


def _paper_path():
    return os.path.join(STATE["root"] or ".", "fixer.json")


def paper():
    """{"checked": {path: when}, "found": {path: {"why", "did", "at", ...}}}"""
    if STATE["paper"] is None:
        try:
            with open(_paper_path(), encoding="utf-8") as f:
                STATE["paper"] = json.load(f)
        except (OSError, ValueError):
            STATE["paper"] = {"checked": {}, "found": {}}
    return STATE["paper"]


def save():
    try:
        with open(_paper_path() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(paper(), f)
        os.replace(_paper_path() + ".tmp", _paper_path())
    except OSError:
        pass


def say(text):
    if STATE["log"]:
        try:
            STATE["log"]("file fixer: " + text)
        except Exception:
            pass


def next_to_check(rows, checked, now):
    """The first file not sampled within CHECK_AGAIN, from (path, size) rows."""
    for path, size in rows:
        if now - float(checked.get(path) or 0) > CHECK_AGAIN:
            return path, size
    return None


def twin_of(rows, path, size, hollow):
    """Another file of the same name and size that is whole: the same release."""
    name = os.path.basename(path).lower()
    for other, other_size in rows:
        if (other != path and other_size == size and os.path.basename(other).lower() == name
                and os.path.exists(other) and not hollow(other)):
            return other
    return ""


def same_drive(a, b):
    return os.path.splitdrive(a)[0].upper() == os.path.splitdrive(b)[0].upper()


def save_path_for(path, rel):
    """Where a torrent must be saved for its file `rel` to land on `path`, or ""."""
    rel = rel.replace("/", os.sep)
    if not os.path.normcase(path).endswith(os.path.normcase(os.sep + rel)):
        return ""
    return path[:len(path) - len(rel) - 1]


def torrents(qb):
    return json.loads(qb._call("/api/v2/torrents/info", timeout=20) or b"[]")


def in_client(qb, path):
    """(hash, file index, drive checking) of the torrent in qBittorrent holding `path`."""
    want = os.path.normcase(os.path.normpath(path))
    for t in torrents(qb):
        root = str(t.get("save_path") or "")
        if not want.startswith(os.path.normcase(os.path.normpath(root))):
            continue
        try:
            files = qb.files(t["hash"])
        except Exception:
            continue
        for n, f in enumerate(files):
            got = os.path.normcase(os.path.normpath(os.path.join(root, str(f.get("name") or ""))))
            if got == want:
                return t["hash"], int(f.get("index", n))
    return None


def drive_checking(qb, path):
    """Whether qBittorrent is already checking something on that file's drive."""
    drive = os.path.splitdrive(path)[0].upper()
    return any(str(t.get("state") or "").startswith("checking")
               and str(t.get("save_path") or "")[:2].upper() == drive
               for t in torrents(qb))


def fix(path, size, rows, hollow, qb, tracker_rows, fetch):
    """Mend one broken file. Returns what was done, in a few words."""
    other = twin_of(rows, path, size, hollow)
    if other and same_drive(other, path):
        os.remove(path)
        os.link(other, path)
        return "replaced by a link to the whole copy at " + other
    held = in_client(qb, path) if qb else None
    if held:
        if drive_checking(qb, path):
            return ""                                  # its drive is busy: next time
        info_hash, index = held
        qb.recheck(info_hash)
        return "rechecking in qBittorrent; the missing parts follow: %s/%d" % (info_hash, index)
    import pd_torrents
    import pd_tracker
    flat_file = pd_tracker.release_flat(os.path.basename(path))
    flat_dir = pd_tracker.release_flat(os.path.basename(os.path.dirname(path)))
    for row in tracker_rows:
        flat = pd_tracker.release_flat(row.get("name"))
        if flat not in (flat_file, flat_dir) or not row.get("url"):
            continue
        raw = fetch(row["url"])
        _, name, files = pd_torrents.parse_torrent(raw)
        one = next((f for f in files if f["size"] == size
                    and os.path.basename(f["path"]).lower() == os.path.basename(path).lower()), None)
        rel = (name + "/" + one["path"]) if one and len(files) > 1 else (one or {}).get("path", "")
        where = save_path_for(path, rel) if one else ""
        if not where:
            continue
        qb._call("/api/v2/torrents/add", fields={
            "savepath": where, "category": "fixer", "contentLayout": "Original",
            "stopped": "false", "paused": "false"}, upload=raw, timeout=60)
        return "added from the tracker over it; qBittorrent fetches the missing parts"
    return "nothing to mend it from"


def follow_up(qb):
    """A recheck finished: turn the file on and start, so only its missing parts come."""
    found = paper()["found"]
    for path, said in list(found.items()):
        did = str(said.get("did") or "")
        if not did.startswith("rechecking"):
            continue
        info_hash, index = did.rsplit(": ", 1)[1].split("/")
        try:
            t = qb.info(info_hash) or {}
        except Exception:
            continue
        if str(t.get("state") or "").startswith("checking"):
            continue
        qb.priority(info_hash, [int(index)], 1)
        qb.start(info_hash)
        said["did"] = "fetching the missing parts in qBittorrent"
        say("%s: fetching the missing parts" % os.path.basename(path))
        save()


def one_round(now=None):
    """Sample one file, and mend it if it is broken."""
    import pd_library
    import pd_torrents
    import pd_tracker
    now = now or time.time()
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return ""
    con = lib.db()
    try:
        rows = [(r[0], int(r[1] or 0)) for r in
                con.execute("SELECT path, size FROM file WHERE path IS NOT NULL ORDER BY id")]
    finally:
        con.close()
    try:
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        follow_up(qb)
    except Exception:
        qb = None
    got = next_to_check(rows, paper()["checked"], now)
    if not got:
        return ""
    path, size = got
    paper()["checked"][path] = now
    if not os.path.exists(path) or not pd_library.hollow_file(path):
        save()
        return os.path.basename(path)
    why = "an empty shell of a download"
    try:
        did = fix(path, size, rows, pd_library.hollow_file, qb,
                  list((pd_tracker.read() or {}).values()),
                  lambda url: urllib.request.urlopen(urllib.request.Request(
                      url, headers={"User-Agent": "Palladium"}), timeout=60).read())
    except Exception as e:
        did = "could not mend it: %s" % str(e)[:120]
    if not did:
        paper()["checked"].pop(path, None)            # its drive was busy: again soon
    else:
        paper()["found"][path] = {"why": why, "did": did, "at": int(now)}
        say("%s: %s - %s" % (os.path.basename(path), why, did))
    save()
    return os.path.basename(path)


def tally():
    """(sampled within the month, still to sample) for the Workers box."""
    done = len(paper()["checked"])
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return done, 0
    con = lib.db()
    try:
        total = con.execute("SELECT COUNT(*) FROM file WHERE path IS NOT NULL").fetchone()[0]
    finally:
        con.close()
    return done, max(0, int(total) - done)


def start(root, lib, wait, log):
    """root: where fixer.json lives; lib() the library; wait() a reason to hold off, or
    ""; log(text) a line in the debug log."""
    import pd_beat
    STATE.update(root=root, lib=lib, wait=wait, log=log)

    def work():
        pd_beat.sleep("fixer", 300, "first check")
        while True:
            try:
                why = wait()
                if why:
                    pd_beat.sleep("fixer", 60, "waiting: " + why)
                    continue
                pd_beat.beat("fixer", "sampling", 600)
                one_round()
                pd_beat.sleep("fixer", BETWEEN)
            except Exception:
                pd_beat.sleep("fixer", 300, "failed, again soon")

    pd_beat.counter("fixer", tally)
    threading.Thread(target=work, name="palladium-fixer", daemon=True).start()
