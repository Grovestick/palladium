"""The settings file and what is kept in it: read and written, watchlists filled, keys carried when titles move, invites learned.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import functools
import json
import os
import re
import time
from typing import Any

import pd_h
import pd_log
import pd_safe

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def carry_shifts(keys):
    """Subtitle timing corrections, filed as key or key|subtitle, onto the episodes their
    files moved to. keys: old episode key -> new."""
    try:
        stored = S.read_settings() or {}
    except Exception:
        return
    held = stored.get("subShift")
    if not isinstance(held, dict):
        return
    out = {}
    for name, v in held.items():
        k, bar, sub = str(name).partition("|")
        out[keys.get(k, k) + bar + sub] = v
    stored["subShift"] = out
    S.write_settings(stored)


def carry_the_keys(moved):
    """Rewrite every key kept outside the library, on the library's own list.

    The shelves, the two marks, each shuffle's round and what was learned about a
    subtitle are all filed under a title's key. They live in the settings file because
    they belong to a person rather than to the library - which is exactly why they do
    not move when it does.
    """
    try:
        stored = S.read_settings() or {}
    except Exception:
        return

    def one(k):
        return S.move_key(moved, k)

    def keyed(box):
        return {one(k): v for k, v in box.items()} if isinstance(box, dict) else box

    def listed(box):
        # two old keys can be one title now: kept once, in the order first met
        if not isinstance(box, list):
            return box
        out, seen = [], set()
        for k in box:
            k = one(k)
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    # Filed by key, at the top and under each viewer alike: a subtitle tried, picked
    # or verified, when one was last seen, a card put aside. These were moved only at the top, and each viewer's own stayed on the old
    # row numbers - a picked subtitle forgotten, a put-aside card back on the shelf.
    by_key = ("subsTried", "subsPick", "subsOk", "subsFor", "deckAside",
              "subSince", "soundPick")

    def per_title(name):
        # a per-title subtitle look is filed as device|l<key> or device|show|<key>
        device, _, scope = str(name).partition("|")
        if scope.startswith("show|"):
            return device + "|show|" + one(scope[5:])
        if scope.startswith("l"):
            return device + "|l" + one(scope[1:])
        return name

    for holder in [stored] + list((stored.get("users") or {}).values()):
        if isinstance(holder, dict):
            for f in by_key:
                if isinstance(holder.get(f), dict):
                    holder[f] = keyed(holder[f])
            if isinstance(holder.get("perTitle"), dict):
                holder["perTitle"] = {per_title(k): v for k, v in holder["perTitle"].items()}
    for mine in list((stored.get("users") or {}).values()) + [stored]:
        if not isinstance(mine, dict):
            continue
        for f in ("watchlist", "favorites"):
            if f in mine:
                mine[f] = listed(mine[f])
        for shelf in (mine.get("collections") or []):
            if isinstance(shelf, dict):
                for f in ("hidden", "pinned"):
                    if f in shelf:
                        shelf[f] = listed(shelf[f])
                if shelf.get("cover"):
                    shelf["cover"] = one(shelf["cover"])
        for round_now in (mine.get("shuffles") or {}).values():
            if not isinstance(round_now, dict):
                continue
            for f in ("queue", "played", "order"):
                if f in round_now:
                    round_now[f] = listed(round_now[f])
            if isinstance(round_now.get("at"), dict):
                round_now["at"] = keyed(round_now["at"])
    try:
        S.write_settings(stored)
    except Exception:
        return                       # left for the next start to finish
    # and the list of what the cache is holding, which is in this server's keys
    try:
        S.Handler.remember_copies(sorted({one(k)
                                        for k in (S.Handler.COPIES.get("keys") or [])}))
    except Exception:
        pass
    # done: the list is only needed until everything beside the library has followed
    try:
        os.remove(os.path.join(S.ROOT, "keys-moved.json"))
    except OSError:
        pass


def settings_path():
    return os.path.join(S.ROOT, "settings.json")


def languages_read(stored):
    """Every subtitle language somebody here reads, first and second choices, in two
    letters; "off" is none. English when nobody has said."""
    rows = [stored] + [u for u in (stored.get("users") or {}).values()
                       if isinstance(u, dict)]
    out = []
    for row in rows:
        for field in ("subLang", "subLang2"):
            said = str(row.get(field) or "").lower()
            code = "" if said == "off" else said[:2]
            if code and code not in out:
                out.append(code)
    return out or ["en"]


@functools.lru_cache(maxsize=50000)
def plain_words(said):
    """A title as a shelf rule reads it: lower case, punctuation dropped, single spaces.

    Dropped, not turned into a gap: an apostrophe spaced out leaves "it s", which the
    word somebody typed is no more inside than it was before. Kept per title, because
    every shelf on the collections page read the whole library through this again -
    fourteen thousand times for nineteen shelves.
    """
    kept = "".join(c for c in str(said or "").lower() if c.isalnum() or c.isspace())
    return " ".join(kept.split())


def read_settings():
    """The settings file, or the last version of it that could be read.

    Never an empty dictionary because of a failure: that is how a file gets emptied.
    A caller that gets None must not write.
    """
    held = getattr(S._HELD, "stored", None)
    if held is not None:
        return held
    path = S.settings_path()
    for attempt in range(4):
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.loads(f.read())
            if isinstance(stored, dict):
                S.SETTINGS_LAST["good"] = stored
                return stored
        except FileNotFoundError:
            return {}
        except Exception:
            time.sleep(0.05)          # a write in progress; it will be over shortly
    # unreadable four times running: the cache in hand is better than nothing, and
    # nothing is better than overwriting what could not be read
    return S.SETTINGS_LAST["good"]


def watch_key_of(key, release=""):
    """What goes on a watchlist for something fetched: a film's own key; a programme's when a
    season or the whole of it was fetched; nothing for a single episode (owner, 2026-11-06)."""
    import pd_torrents
    release = str(release or "")
    whole = bool(release) and not re.search(r"[sS]\d{1,2}[eE]\d{1,3}", release) and bool(
        re.search(r"(?i)[._ \-](s\d{1,2}|season[._ ]?\d{1,2}|complete)([._ \-]|$)", release))
    if release and not whole and re.search(r"[sS]\d{1,2}[eE]\d{1,3}", release):
        return ""
    key = "" if whole else (pd_torrents.title_of(key) if key else "")
    lib = S.local().lib
    con = lib.db()
    try:
        if whole:
            # read as its first episode would be, and that episode's programme taken
            stem = re.sub(r"\s+", ".", release)
            if re.search(r"(?i)[._ \-]s\d{1,2}(?=[._ \-]|$)", stem):
                stem = re.sub(r"(?i)([._ \-]s\d{1,2})(?=[._ \-]|$)", r"\1E01", stem, count=1)
            else:
                stem = re.sub(r"(?i)[._ \-](complete|season[._ ]?\d{1,2})(?=[._ \-]|$)",
                              ".S01E01", stem, count=1)
            ep = lib.key_of_file(con, "show", "X:/fetched/%s.mkv" % stem) or ""
            if not S.is_episode(ep):
                return ""
            row = con.execute("SELECT item_id FROM episode WHERE id=?", (ep,)).fetchone()
            if row:
                return str(row[0])
            name = re.split(r"(?i)\.s\d{1,2}e\d", stem)[0].replace(".", " ").strip()
            return pd_torrents.programme_id(name) if name else ""
        if not key and release:
            stem = re.sub(r"\s+", ".", release)
            key = lib.key_of_file(con, "movie", "X:/fetched/%s.mkv" % stem) or ""
        # one episode, out of a pack or on its own: nothing
        if not key or S.is_episode(key):
            return ""
    finally:
        con.close()
    return "" if key.startswith(("dl-", "sp")) else key


def fill_watchlists():
    """Once: what each person asked to fetch themselves - from a pack or from the tracker - on
    their watchlist, as fetching puts it from now on (owner, 2026-11-06). Fetches the house
    made by itself (episodes ahead, a shuffle's draw, a copy) and what they have watched
    since are left out. How many added."""
    import pd_torrents
    import pd_tracker
    stored = S.read_settings() or {}
    if not stored or stored.get("watchFilled"):
        return 0
    users = stored.setdefault("users", {})
    by_name = {}
    for token, mine in users.items():
        if isinstance(mine, dict):
            for n in (mine.get("myName"), S.Handler.name_of_token(token)):
                if n:
                    by_name.setdefault(str(n).strip().lower(), token)
    for n in (stored.get("myName"), "you", "me"):
        if n:
            by_name[str(n).strip().lower()] = "me"
    owner_names = {str(stored.get("ownerName") or "").strip().lower()} - {""}
    for n in owner_names:
        by_name.setdefault(n, "me")
    wanted = []                                   # (when, token, key)
    for d in pd_torrents.load()["downloads"]:
        if d.get("auto") or not d.get("token"):
            continue
        wanted.append((float(d.get("when") or 0), str(d["token"]), S.watch_key_of(d.get("titleId") or d.get("key"))))
    with pd_tracker.LOCK:
        log = list(pd_tracker.STATE.get("log") or [])
    for t in log:
        if t.get("auto"):
            continue
        token = by_name.get(str(t.get("who") or "").strip().lower())
        if token:
            wanted.append((float(t.get("at") or 0), token, S.watch_key_of("", t.get("name"))))
    added = 0
    api = S.local()
    con = api.lib.db()
    was_who = api.who

    def seen(token, key):
        # finished already: a film, or every episode held of a programme
        api.who = (str(stored.get("ownerIs") or "") or "me") if token == "me" else token
        try:
            eps = [str(r[0]) for r in con.execute(
                "SELECT e.id FROM episode e WHERE e.item_id=? AND EXISTS "
                "(SELECT 1 FROM file f WHERE f.episode_id = e.id)", (key,))]
            return all(api._watched(con, k) for k in eps) if eps else api._watched(con, key)
        except Exception:
            return False
        finally:
            api.who = was_who

    try:
        wanted = [(when, token, key) for when, token, key in wanted if key and not seen(token, key)]
    finally:
        con.close()
    for _, token, key in sorted(wanted):
        if not key:
            continue
        mine = stored if token == "me" else users.get(token)
        if not isinstance(mine, dict):
            continue
        had = [str(k) for k in (mine.get("watchlist") or [])]
        if key not in had:
            S.put_on_watchlist(mine, [key])
            added += 1
    stored["watchFilled"] = int(time.time())
    S.write_settings(stored)
    pd_log.raw("%s put %d fetched title(s) on the watchlists of whoever asked%s"
               % (time.strftime("%H:%M:%S"), added, chr(10)))
    return added


def retire_pack_keys():
    """Every pack key written down - a shuffle's order, a collection, a watchlist - moved onto
    the title it names: one set of keys (owner, 2026-11-06). How many were moved."""
    import pd_torrents
    return S.carry_keys(pd_torrents.pack_keys_moved())


def keep_pack_keys_retired():
    """Once the packs are named, the pack keys written down are moved onto their titles; and
    every hour after, for an older app that still sends one."""
    import pd_torrents
    for _ in range(120):
        if pd_torrents.all_named():
            break
        time.sleep(5)
    try:
        S.fill_watchlists()
    except Exception as e:
        pd_log.raw("%s watchlists: %s%s" % (time.strftime("%H:%M:%S"), str(e)[:200], chr(10)))
    while True:
        try:
            moved = S.retire_pack_keys()
            if moved:
                pd_log.raw("%s moved %d pack key(s) onto their titles%s"
                           % (time.strftime("%H:%M:%S"), moved, chr(10)))
        except Exception as e:
            pd_log.raw("%s pack keys: %s%s" % (time.strftime("%H:%M:%S"), str(e)[:200], chr(10)))
        time.sleep(3600)


def carry_keys(moved):
    """Whatever was written down by key follows the title that moved.

    A merge folds two rows into one and drops a key; the index carries its own
    (places, the watch log), but a watchlist, a shuffle's queue and the titles pinned
    to a collection live here, and they went on naming a key that no longer answers.
    Every string in the settings is looked up in the map, because that is exactly what
    a key is - anything that is not one is left alone.
    """
    if not moved or not (moved.get("titles") or moved.get("episodes")):
        return 0
    count = [0]

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            now = S.move_key(moved, o)
            if now and now != o:
                count[0] += 1
                return now
        return o

    stored = S.read_settings() or {}
    fixed = walk(stored)
    if count[0]:
        S.write_settings(fixed, merge=False)
        pd_log.raw("%s carried %d written-down key(s) onto titles that moved%s"
                % (time.strftime("%H:%M:%S"), count[0], chr(10)))
    return count[0]


def put_on_watchlist(mine, keys):
    """Put titles at the front of a viewer's watchlist, with when: the watchlist sorts by the
    later of last watched and added. Returns the list."""
    keys = [str(k) for k in keys]
    had = [str(k) for k in (mine.get("watchlist") or []) if str(k) not in keys]
    mine["watchlist"] = (keys + had)[:400]
    at = mine.get("watchlistAt") if isinstance(mine.get("watchlistAt"), dict) else {}
    now = int(time.time())
    at.update({k: now for k in keys})
    mine["watchlistAt"] = {k: v for k, v in at.items() if k in set(mine["watchlist"])}
    return mine["watchlist"]


def write_settings(stored, merge=True):
    """Write the settings whole, or not at all.

    `merge` keeps whatever another writer added since this caller read the file. A
    caller that has *removed* a key must say merge=False, or the merge puts it back:
    that is how clearing a personal colour left the colour exactly where it was.
    """
    if not isinstance(stored, dict) or not stored:
        return                        # never write an empty file over a full one
    path = S.settings_path()
    with S.SETTINGS_LOCK:
        # the spare, and one dated copy a day: both only from a file that reads, and
        # both on the disk before they carry the name. A spare copied from a file
        # the power had just zeroed was the second copy of nothing.
        pd_safe.spare(path)
        pd_safe.daily(path)
        # What somebody else wrote since this caller read the file survives. A caller
        # holds a snapshot and changes one thing in it; writing the snapshot back
        # would undo anything that arrived in between. Merging by top-level key means
        # the most that can be lost is one person's one setting, rather than the file.
        if merge:
            try:
                with open(path, encoding="utf-8") as f:
                    current = json.loads(f.read())
                if isinstance(current, dict):
                    merged = dict(current)
                    merged.update(stored)
                    stored = merged
            except Exception:
                pass                  # nothing readable to merge with; write ours
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
            pd_safe.settle(f)
        # Windows refuses the swap while anybody else holds the file open - a reader
        # in another thread, or the scanner that opens every file as it is written -
        # and it had been throwing the write away: six settings writes were lost in a
        # day. The lock is gone in a moment, so the swap is simply tried again.
        # Six tries at 50 ms was not enough while the scanner was reading: the swap
        # kept failing and the write was thrown away with somebody's setting in it.
        # It waits longer each time, up to about two seconds, and if the swap is
        # still refused the file is written in place rather than lost.
        wrote = False
        for again in range(12):
            try:
                os.replace(tmp, path)
                wrote = True
                break
            except PermissionError:
                time.sleep(min(0.5, 0.05 * (again + 1)))
        if not wrote:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(stored, f, indent=2)
                    pd_safe.settle(f)
                wrote = True
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        S.SETTINGS_LAST["good"] = stored


def note_download():
    stored = S.read_settings()
    stored["subsLastDownload"] = int(time.time())
    S.write_settings(stored)


def learn_invites(said, owner=None, master="", look=None):
    """Take the main server's invitations, so its guests are known here too.

    A following server holds copies of what the main server watches; the people who may
    watch them are the people the main server invited. Their links are written into this
    server's own invitations, marked as borrowed so they can be told apart from the
    ones this machine issued and dropped when the main server stops sharing them.
    """
    if not isinstance(said, list):
        return
    theirs = {str(r.get("token") or ""): r for r in said if r.get("token")}
    rows = S.INVITES.load()
    keep = []
    for row in rows:
        if row.get("borrowed") and row["token"] not in theirs:
            continue                      # withdrawn over there, gone from here
        keep.append(row)
    known = {r["token"] for r in keep}
    for token, r in theirs.items():
        if token in known:
            continue
        keep.append({"token": token, "name": r.get("name") or "guest",
                     "email": "", "created": int(time.time()),
                     "expires": int(r.get("expires") or 0),
                     "language": r.get("language") or "",
                     "role": str(r.get("role") or ""),
                     "lastSeen": 0, "hits": 0, "borrowed": True})
    # what the main server keeps for each of them, refreshed every time: it is the
    # sentence this machine puts at the top of its own page
    for row in keep:
        theirs_row = theirs.get(row.get("token") or "")
        if theirs_row:
            for name in ("cacheDeck", "cacheList", "cacheCasual", "cacheLately"):
                row[name] = bool(theirs_row.get(name))
            # role, refreshed each round, so a key promoted or demoted on the main
            # server changes here too. Cache keys are filtered out before sending.
            if row.get("borrowed"):
                row["role"] = str(theirs_row.get("role") or "")
    S.INVITES.save(keep)
    if isinstance(look, dict) and look:
        # How the main server draws each person's subtitles, kept here under the same key.
        # Only the look and the language: what they have watched is theirs and is
        # already carried the other way.
        stored = S.read_settings()
        if stored is not None:
            for who, said in look.items():
                if not isinstance(said, dict):
                    continue
                where = stored if who == "me" else (
                    stored.setdefault("users", {}).setdefault(str(who), {}))
                for name in ("subtitles", "perTitle", "subLanguage", "language"):
                    if said.get(name) is not None:
                        where[name] = said[name]
            S.write_settings(stored)
    if isinstance(owner, dict):
        stored = S.read_settings()
        if stored is not None:
            stored["copyOf"] = {"master": master, "owner": owner,
                                "when": int(time.time())}
            # Who the main server calls its owner is written down but not adopted: this
            # machine is not that person, and saying it is would hand a name to a
            # box in a cupboard. The places that arrive under that name are filed
            # against this machine's own owner instead.
            S.write_settings(stored)


def flush_invites():
    """Put the invite counters on disk every half minute.

    They were written only on a clean shutdown, and the server is usually killed, so
    "never used" could mean "used, and the count died with the process".
    """
    while True:
        time.sleep(30)
        try:
            S.INVITES.flush()
        except Exception:
            pass


def titles_in_use():
    """Every title somebody is part way through.

    Being part way through a film is the only thing that makes an unverified note
    worth keeping: somebody will come back to that exact minute and want the
    subtitle where they left it. Marking a film to watch one day is not the same
    thing - it says nothing about a subtitle, and a list of intentions would keep
    these notes for ever.
    """
    live = set()
    try:
        con = S.local().lib.db()
        try:
            for r in con.execute("SELECT DISTINCT key FROM progress"):
                live.add(str(r["key"]))
        finally:
            con.close()
    except Exception:
        return None            # unreadable: keep everything rather than guess
    return live


def forget_old_casual():
    """Drop what the old Casual list left behind.

    The marks were turned into a Casual collection long ago and are read by nothing.
    A round filed under "casual" belonged to that collection; where the collection is
    gone the round is too, and left in place the cache took it for a shuffle of the
    whole library and stocked for it.
    """
    stored = S.read_settings()
    if not stored:
        return 0
    dropped = 0
    for mine in list((stored.get("users") or {}).values()) + [stored]:
        if not isinstance(mine, dict):
            continue
        for name in S.OLD_CASUAL:
            if name in mine:
                mine.pop(name, None)
                dropped += 1
        rounds = mine.get("shuffles")
        has_shelf = any(isinstance(c, dict) and str(c.get("id")) == "casual"
                        for c in (mine.get("collections") or []))
        if isinstance(rounds, dict) and "casual" in rounds and not has_shelf:
            rounds.pop("casual", None)
            dropped += 1
    if dropped:
        S.write_settings(stored, merge=False)
    return dropped
