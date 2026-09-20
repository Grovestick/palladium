#!/usr/bin/env python3
"""Following another Palladium: keeping copies of what the main server is watching.

One server is the main server - the one with the library on it. Another can follow it: it
asks what is being played, copies those files while the main server is on, and serves them
itself when it is not. For a series it copies ahead, so that finishing an episode at
one in the morning does not mean finding the next one gone.

Nothing is pushed. The cache asks, and takes what it is given: the main server needs no
knowledge of who is following it beyond a key it can revoke.
"""
import io
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request

#: how often to ask the main server what is happening. A film is two hours; a minute is
#: often enough to catch it, and quiet enough to leave a sleeping machine alone.
ASK_EVERY = 60

#: read in lumps, so a stopped copy leaves something to carry on from
LUMP = 4 * 1024 * 1024

#: what to tell the main server about this machine, so a viewer whose server is off can
#: be pointed here. Filled in by start().
ME = {"port": 8765, "name": "", "static": "", "build": "", "settings": ""}

#: what to do with the main server's invitations, set by start(). A guest whose server is
#: off reaches this one with the link they already hold, or not at all.
KEYS = {"learn": None, "last": 0.0, "stamp": 0}

#: Where the main server had got to, and when we last asked. Copying the films without the
#: places in them means a shelf of things that all start at the beginning.
PLACES = {"since": 0, "at": 0.0, "gave": 0.0, "gavesince": 0}

#: what the main server calls its owner. Their places arrive under that name and are filed
#: here against whoever owns this machine - which is a different person, or none.
HOUSE = {"owner": ""}

STATE = {"on": False, "why": "", "copying": "", "at": 0.0, "kept": 0, "files": 0,
         "last": 0, "stocking": False, "whose": "",
         # what is in flight: how big it is, and whether a person put it at the front
         "size": 0, "pinned": False}
LOCK = threading.Lock()
STARTED = False

#: set the moment following is turned off, and read between lumps of a file. A copy
#: that cannot be interrupted is a copy that goes on reading somebody's disk for
#: another hour after they asked it to stop.
STOP = threading.Event()

#: The last few files that would not come, newest first: name, what happened, when,
#: and how many times. One error string was kept before this, and the next success
#: wrote over it - so a file that failed every pass all night left no trace by
#: morning, and the only way to ask why was to watch it live.
TROUBLE = []
TROUBLE_MANY = 12

#: one pass at a time. Sync now and the round that comes every minute both start one,
#: and two of them read the same disk, write the same part files and copy the same
#: film twice.
PASS = threading.Lock()


def for_whom(one):
    """The viewer this cache is kept for, or nothing for the whole house.

    A machine in one person's room that fills with the rest of the main server's evening
    is using their disk for somebody else, so this is the way round it starts.
    """
    if str(one.get("cacheFor") or "user") != "user":
        return ""
    return str(one.get("cacheWho") or HOUSE.get("ownerName") or "").strip()


def look():
    """What the cache is doing, for the page that set it up."""
    with LOCK:
        said = dict(STATE)
    # who the main server has, as it last said: the page offers the names rather than
    # asking somebody to spell one, and a name spelled wrong copies nothing at all
    said["house"] = list(HOUSE.get("people") or [])
    said["forWhom"] = HOUSE.get("ownerName") or ""
    return said


def settings(cfg):
    """The part of the library's settings that belongs here."""
    one = dict(cfg.get("follow") or {})
    one.setdefault("on", False)
    one.setdefault("master", "")          # http://address:8765
    one.setdefault("key", "")             # an invitation from that server
    one.setdefault("folder", "")          # where copies are kept
    one.setdefault("hours", 4)            # of episodes to keep ahead, at most
    one.setdefault("episodes", 6)         # and no more than this many of them
    one.setdefault("cap", 200)            # gigabytes to use at most
    # each kind kept always unless somebody says otherwise: off, night, or always
    one.setdefault("kinds", {})
    one.setdefault("coverNight", True)    # enough to last the hours the main server sleeps
    one.setdefault("wholeList", False)    # every unwatched episode of a watchlisted
                                          # programme, rather than the night's worth
    one.setdefault("deleteBy", "oldest")  # oldest untouched first, or biggest first
    # listByDay was a switch under all of the kinds saying nothing moves before
    # night. Each kind says when it may be taken now; the setting is left in
    # place so an older machine reading this file is not surprised by its absence.
    one.setdefault("listByDay", True)
    one.setdefault("allowRemote", False)  # the main server may change this machine's own
    one.setdefault("nightFrom", 22)       # the hours this server is the one awake
    one.setdefault("nightTo", 8)
    # what to tell the main server to hand out to viewers from outside the main server. Empty
    # means this network's own address with this machine's port on it, which is
    # right whenever both servers sit behind the one router.
    one.setdefault("outside", "")
    # hours of the shuffle to keep ahead for whoever asked for it, alongside the
    # films and the series. Nought means the shuffle is not copied at all.
    one.setdefault("casualHours", 0)      # the shuffle, only if asked for
    # whether this machine replaces itself when the one it follows is newer. Off
    # unless asked for: replacing a server is not something to do behind somebody.
    one.setdefault("updateWith", False)
    # Whose cache this is. A user cache holds what one person is watching - the
    # machine in their own room, filling with their own evening. A server cache
    # holds the whole house's, for a machine that stands in for the library itself.
    # A machine already set up and keeping copies for a house was keeping them for
    # the main server; it is not for a new setting to decide otherwise behind everybody.
    # Only a following that has not been set up yet starts as one person's.
    one.setdefault("cacheFor", "server" if one.get("master") else "user")
    one.setdefault("cacheWho", "")        # which viewer, when it is a user cache
    # Whether the cap may delete on its own. Off unless asked for: a program that
    # deletes files in a folder somebody typed the name of should be told to.
    one.setdefault("clearBy", "manual")   # "manual" or "auto"
    return one


def night_length(one):
    """How many hours the main server server is asleep for, by its own settings."""
    frm = int(one.get("nightFrom", 22)) % 24
    to = int(one.get("nightTo", 8)) % 24
    return float((to - frm) % 24 or 24)


def hours_wanted(one):
    """How far ahead to keep: enough to last the hours the main server is asleep.

    The number in the settings is a floor. What decides it is how long this machine
    is the only one awake - four hours of episodes is no use across a ten-hour night,
    and somebody who starts a series at midnight should not run out at four.
    """
    asked = float(one.get("hours") or 4)
    if not one.get("coverNight", True):
        return asked
    return max(asked, night_length(one))


#: What this machine keeps a copy of, each of which is off, kept only inside the
#: hours this machine is the one awake, or kept always. The main server is told which
#: of them apply this minute and leaves the rest out of its answer: it was all or
#: nothing before, so a machine wanting one person's watchlist took everybody's
#: half-watched series along with it.
KINDS = ("partway", "watchlist", "lately", "shuffle", "screen")
KIND_OTHERWISE = "always"


def kinds_now(one, now=None):
    """The kinds this machine will take at this moment, as the main server names them.

    A kind nobody has set is kept always, which is what every machine did before there
    was anything to set.
    """
    modes = one.get("kinds") or {}
    # Early counts as night: it is the way to say "the hours are now", and a kind set
    # to wait for them should not go on waiting through it.
    night = stocking_up(one, now)
    out = []
    for kind in KINDS:
        mode = str(modes.get(kind) or KIND_OTHERWISE).lower()
        if mode == "always" or (mode == "night" and night):
            out.append(kind)
    return out


def in_the_night(one, now=None):
    """Whether this is one of the hours the cache is the server that is awake."""
    hour = (now or time.localtime()).tm_hour
    start, end = int(one.get("nightFrom", 22)), int(one.get("nightTo", 8))
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


#: The pass being worked through, if there is one. One at a time and no more: two
#: passes share the link, and both take twice as long for no gain.
PASS_NOW = {"thread": None}


def a_pass_is_running():
    return PASS_NOW["thread"] is not None and PASS_NOW["thread"].is_alive()


#: Somebody going to bed early. Until this time, take copies as though the night
#: had started - it is the same work, done when it is wanted rather than at an hour.
EARLY = {"until": 0.0}


def start_the_night(one):
    """Behave as though the night had begun, until it actually ends."""
    EARLY["until"] = time.time() + max(1, int(night_length(one))) * 3600
    return EARLY["until"]


def stocking_up(one, now=None):
    """Whether to also copy what is on a screen: inside the set sync hours, or after
    Early was pressed."""
    if EARLY["until"] > time.time():
        return True
    return in_the_night(one, now or time.localtime())


def ask(one, path, patience=30):
    """One request to the main server, with the key it gave us."""
    url = one["master"].rstrip("/") + path
    url += ("&" if "?" in path else "?") + "t=" + urllib.parse.quote(one["key"])
    req = urllib.request.Request(url, headers={"X-Palladium-App": "follower"})
    with urllib.request.urlopen(req, timeout=patience) as answer:
        return json.loads(answer.read().decode("utf-8", "replace"))


#: What a film may be. The other server is trusted to be Palladium, not trusted to be
#: honest: a name it hands over lands on this disk, and the only names worth taking are
#: the ones a player can open.
FILMS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".mpg",
         ".mpeg", ".wmv", ".flv", ".srt", ".ass", ".vtt", ".sub", ".idx")

#: How each of those begins. A file called .mkv that starts like a program is not a
#: film, whatever it is called.
MAGIC = (
    bytes([0x1A, 0x45, 0xDF, 0xA3]),      # matroska: mkv and webm
    b"RIFF",                              # avi
    bytes([0, 0, 1, 0xBA]),               # mpeg program stream
    bytes([0, 0, 1, 0xB3]),               # mpeg video
    b"FLV",
    bytes([0x30, 0x26, 0xB2, 0x75]),      # asf, which is wmv
)


def a_safe_name(name):
    """The bare file name, or None if it has no business being written here.

    A following server takes what another machine says to take. That machine names
    the file, and a name is a place: one with folders in it points somewhere else
    on this disk.
    Only the last part of it is kept, and only if it is a film or a subtitle.
    """
    said = str(name or "").replace("\\", "/").split("/")[-1].strip()
    if not said or said in (".", "..") or said.startswith("."):
        return None
    if ":" in said or chr(0) in said:
        return None
    if not said.lower().endswith(FILMS):
        return None
    return said


def looks_like_film(path, called=""):
    """Whether what arrived begins the way a film begins.

    `called` is the name it will have once it lands. What is being read is a .part
    file, so asking the temporary name whether it ends in .srt answered no every time
    - and every subtitle was deleted as "not a film" and asked for again, for ever.
    """
    if (called or path).lower().endswith((".srt", ".ass", ".vtt", ".sub", ".idx")):
        return True                        # text, and read as text by everything here
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if any(head.startswith(mark) for mark in MAGIC):
        return True
    # mp4 and its relatives put the size first and "ftyp" after it
    return head[4:8] in (b"ftyp", b"moov", b"mdat", b"free")


def quick_mark(path):
    """The same fingerprint the other machine sends: the size and both ends.

    Cheap enough to check every copy, and exact enough that a file which arrived
    changed, truncated or substituted does not match. Hashing the whole of a film
    would cost a minute each and answer the same question.
    """
    import hashlib
    try:
        size = os.path.getsize(path)
        digest = hashlib.sha256()
        digest.update(str(size).encode())
        with open(path, "rb") as f:
            digest.update(f.read(65536))
            if size > 131072:
                f.seek(-65536, os.SEEK_END)
                digest.update(f.read(65536))
        return digest.hexdigest()
    except OSError:
        return ""


def tell(one, path, what, patience=20):
    """Say something to the main server. Same key, a body rather than a question."""
    url = one["master"].rstrip("/") + path
    url += ("&" if "?" in path else "?") + "t=" + urllib.parse.quote(one["key"])
    body = json.dumps(what).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Palladium-App": "follower"})
    with urllib.request.urlopen(req, timeout=patience) as answer:
        return json.loads(answer.read().decode("utf-8", "replace") or "{}")


#: when the app the main server carries was last looked at
APP = {"at": 0.0}
APP_EVERY = 1800


def mirror_app(one):
    """Keep the main server's app beside this server's own pages.

    A viewer whose server is off reaches this one, and the update banner asks every
    server it knows for a newer app. This machine would offer whatever its installer
    happened to carry - which is older than the main server's the moment the main server is
    built again. Two small files, half-hourly, and it mirrors the current app.
    """
    where = ME.get("static") or ""
    if not where or not os.path.isdir(where):
        return
    if time.time() - APP["at"] < APP_EVERY:
        return
    APP["at"] = time.time()
    said = ask(one, "/app/version", 20)
    theirs = int(said.get("versionCode") or 0)
    if theirs <= 0:
        return
    mine = 0
    try:
        with open(os.path.join(where, "version.json"), encoding="utf-8") as f:
            mine = int((json.load(f) or {}).get("versionCode") or 0)
    except (OSError, ValueError):
        mine = 0
    if theirs <= mine:
        return
    url = (one["master"].rstrip("/") + "/palladium.apk?t="
           + urllib.parse.quote(one["key"]))
    req = urllib.request.Request(url, headers={"X-Palladium-App": "follower"})
    part = os.path.join(where, "palladium.apk.part")
    with urllib.request.urlopen(req, timeout=120) as answer:
        with open(part, "wb") as f:
            while True:
                lump = answer.read(LUMP)
                if not lump:
                    break
                f.write(lump)
    # an APK is a zip, and one that is not did not arrive whole
    with open(part, "rb") as f:
        if f.read(2) != b"PK":
            os.remove(part)
            raise ValueError("what arrived is not an app")
    os.replace(part, os.path.join(where, "palladium.apk"))
    tmp = os.path.join(where, "version.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: said.get(k) for k in
                   ("versionCode", "versionName", "sizeMb", "built")}, f, indent=2)
    os.replace(tmp, os.path.join(where, "version.json"))
    return said.get("versionName") or ""


#: when this machine last thought about replacing itself
#: Both ways in to the machine this one follows, as that machine names them. Asked
#: while it can be reached, because the point of holding them is the hour it cannot:
#: somebody who finds this machine first should be able to find the main server from here.
HOUSE = {"at": 0.0, "lan": "", "outside": "", "name": ""}
HOUSE_EVERY = 900


#: where the doors are kept between runs, so a copy that has just started still
#: knows them. Read once; written whenever the round learns something new.
DOORS_FILE = {"at": 0.0}


def doors_path():
    # beside the settings file, which is the one path this module is handed
    where = os.path.dirname(ME.get("settings") or "") or "."
    return os.path.join(where, "house.json")


def remember_doors():
    """Write down both ways in to the machine this one follows.

    The round asks for them every quarter of an hour. Between a restart and the first
    round, a copy answered every screen that it had no way back to the main server at
    all - and a phone away from the house, handed nothing, had nowhere to go when the
    shelf it was on could not be reached.
    """
    try:
        with open(doors_path(), "w", encoding="utf-8") as f:
            json.dump({"lan": HOUSE["lan"], "outside": HOUSE["outside"],
                       "name": HOUSE["name"]}, f)
    except OSError:
        pass


def recall_doors():
    try:
        with open(doors_path(), encoding="utf-8") as f:
            said = json.load(f) or {}
    except (OSError, ValueError):
        return
    with LOCK:
        for k in ("lan", "outside", "name"):
            if not HOUSE.get(k):
                HOUSE[k] = str(said.get(k) or "")


def house_doors():
    with LOCK:
        got = {"lan": HOUSE["lan"], "outside": HOUSE["outside"],
               "name": HOUSE["name"]}
    if got["lan"] or got["outside"]:
        return got
    recall_doors()
    with LOCK:
        return {"lan": HOUSE["lan"], "outside": HOUSE["outside"],
                "name": HOUSE["name"]}


GROWN = {"at": 0.0}
GROWN_EVERY = 900


def newer(a, b):
    """True when version a is later than version b, place by place."""
    def bits(v):
        out = []
        for part in str(v or "").split("."):
            try:
                out.append(int(part))
            except ValueError:
                out.append(0)
        return out
    x, y = bits(a), bits(b)
    for i in range(max(len(x), len(y))):
        one, two = (x[i] if i < len(x) else 0), (y[i] if i < len(y) else 0)
        if one != two:
            return one > two
    return False


#: how often to fetch what viewers keep. Their own lists, not the library: it
#: changes when somebody presses something, not while a film plays.
#: Watchlists and shelves change slowly; where somebody is in a shuffle changes
#: every twenty minutes, and it now travels both ways on this same round.
VIEWERS_EVERY = 120
VIEWERS_AT = [0.0]


def round_stamp(rnd):
    """When a shuffle round last moved; nought for none."""
    return int(rnd.get("casualStamp") or 0) if isinstance(rnd, dict) else 0


#: what was last named to the server this machine follows, so the same list is not
#: sent every round
TOLD_FETCHED = set()


def tell_what_we_fetched(one):
    """Name the films this machine went and got itself, for the other one to take back.

    Only what this machine fetched - never what it was sent - and only once each. The
    other machine may already have it, may not want it, and is the one that decides:
    all that happens here is that it is told.
    """
    if not (one.get("on") and one.get("master") and one.get("key")):
        return 0
    try:
        import pd_torrents
        mine = pd_torrents.my_own_downloads()
    except Exception:
        return 0
    fresh = [d for d in mine
             if (str(d.get("hash")), int(d.get("index", -1))) not in TOLD_FETCHED]
    if not fresh:
        return 0
    # where to come and take them from is not said here: the server this machine
    # follows already knows the address it answers on, and works it out from the
    # request rather than believing what it is told.
    said = tell(one, "/follow/fetched", {"items": fresh[:100]}, 30)
    if said is None:
        return 0                      # not answering: tell it again next round
    for d in fresh[:100]:
        TOLD_FETCHED.add((str(d.get("hash")), int(d.get("index", -1))))
    if len(TOLD_FETCHED) > 4000:
        TOLD_FETCHED.clear()
    return int((said or {}).get("taking") or 0)


def learn_the_viewers(one, settings_path, api=None):
    """Take a copy of what each viewer keeps, so this machine knows them too.

    A watchlist, the shelves somebody arranged, the collections they made: these live
    beside the library and were never copied, so the one moment a viewer needs this
    machine - the main server being off - was the moment their own list was empty and the
    shelves were somebody's defaults.

    Written under the same key each viewer watches by, and only where this machine has
    nothing of its own for them: what somebody set here, sitting here, is theirs and is
    not overwritten by the main server.
    """
    if not settings_path:
        return 0
    if time.time() - VIEWERS_AT[0] < VIEWERS_EVERY:
        return 0
    VIEWERS_AT[0] = time.time()
    said = tell(one, "/follow/viewers", {}, 30)
    if not said or not isinstance(said.get("viewers"), dict):
        return 0
    mine = _read_settings_file(settings_path)
    if mine is None:
        return 0                  # unreadable: writing what is in hand would empty it
    before = json.loads(json.dumps(mine))
    users = mine.setdefault("users", {})
    filled = 0
    mine_to_send = []
    for who, theirs in said["viewers"].items():
        theirs = theirs or {}
        here = users.setdefault(str(who), {})
        for name, value in theirs.items():
            if name in ("watchlistIs", "shuffles", "collections"):
                continue                  # settled below
            if not here.get(name):
                here[name] = value
                filled += 1
        # Collections, by the one a viewer made rather than by the list as a whole.
        # Taken once and never again, the list froze at whatever the machine copied
        # first: a collection made afterwards was not here at all, so the evening the
        # main server was off it was missing along with the shuffle that goes with it.
        # One made sitting here is kept - only the main server's own are replaced.
        house_colls = [c for c in (theirs.get("collections") or [])
                       if isinstance(c, dict) and c.get("id")]
        if house_colls:
            ours = [c for c in (here.get("collections") or [])
                    if isinstance(c, dict) and c.get("id")]
            theirs_by_id = {str(c["id"]): c for c in house_colls}
            merged, seen = [], set()
            for c in ours:
                cid = str(c["id"])
                seen.add(cid)
                merged.append(theirs_by_id.get(cid, c))
            for c in house_colls:
                if str(c["id"]) not in seen:
                    merged.append(c)
            if merged != ours:
                here["collections"] = merged
                filled += 1
        # shuffle rounds, per shelf: the newer round is taken whole, and one moved
        # here while the main server was off goes back up
        rounds = here.get("shuffles") if isinstance(here.get("shuffles"), dict) else {}
        house = theirs.get("shuffles") if isinstance(theirs.get("shuffles"), dict) else {}
        for cid, rnd in house.items():
            if isinstance(rnd, dict) and round_stamp(rnd) > round_stamp(rounds.get(cid)):
                rounds[cid] = rnd
                filled += 1
        if rounds:
            here["shuffles"] = rounds
        newer = {cid: rnd for cid, rnd in rounds.items()
                 if round_stamp(rnd) > round_stamp(house.get(cid))}
        if newer:
            mine_to_send.append((str(who), newer))
        # A watchlist is nobody's round and is not stamped, so what somebody made
        # sitting at this machine is left alone. What was numbered by the other
        # library is not a watchlist at all - none of it can be placed here - and
        # that is replaced.
        if api is not None and (theirs or {}).get("watchlistIs") is not None:
            con = api.lib.db()
            try:
                theirs_here = [k for k in (api.key_of(con, w) for w in
                                           (theirs.get("watchlistIs") or [])) if k]
                ours = [str(k) for k in (here.get("watchlist") or [])]
                usable = [k for k in ours if api.what_it_is(con, k)]
            except Exception:
                theirs_here, usable, ours = [], [], []
            finally:
                con.close()
            if theirs_here and not usable and ours != theirs_here:
                here["watchlist"] = theirs_here
                filled += 1
    for who, rounds in mine_to_send:
        try:
            tell(one, "/follow/round", {"who": who, "shuffles": rounds}, 20)
        except Exception:
            pass                          # the main server will hear on the next round
    # and who the main server calls its owner, so the person who owns the library is the
    # same person here rather than a stranger with no key
    if said.get("ownerIs") and not mine.get("ownerIs"):
        mine["ownerIs"] = said["ownerIs"]
    if said.get("ownerName") and not mine.get("ownerName"):
        mine["ownerName"] = said["ownerName"]
    if mine != before:
        # laid over the file as it is now: the server writes it too, and only what
        # changed here replaces anything
        fresh = _read_settings_file(settings_path)
        if fresh is None or (not fresh and before):
            return filled
        for name in ("ownerIs", "ownerName"):
            if mine.get(name) != before.get(name):
                fresh[name] = mine.get(name)
        users = fresh.setdefault("users", {})
        for who, one in (mine.get("users") or {}).items():
            was = (before.get("users") or {}).get(who) or {}
            there = users.setdefault(who, {})
            for name, value in (one or {}).items():
                if was.get(name) != value:
                    there[name] = value
        tmp = settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fresh, f, indent=2)
        os.replace(tmp, settings_path)
    return filled


def _read_settings_file(path):
    """The settings file, {} when there is none, or None when it cannot be read."""
    for _ in range(4):
        try:
            with open(path, encoding="utf-8") as f:
                got = json.loads(f.read())
            return got if isinstance(got, dict) else None
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            time.sleep(0.05)      # a write in progress
    return None


def check_the_build(one):
    """Ask whether the main server is running something newer than this machine.

    Its own round. It used to be asked inside the app-mirroring round, which does
    nothing at all unless there is a folder to mirror the app into - so a machine with
    no such folder never looked for a newer build and stayed where it was installed.
    """
    if time.time() - GROWN["at"] < GROWN_EVERY:
        return
    said = ask(one, "/app/version", 20)
    # what the main server is running, and what this machine is running: its own number
    # comes from itself, which is the one place that cannot be wrong
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/app/version" % ME["port"], timeout=15) as answer:
            said["mine"] = (json.loads(answer.read().decode("utf-8", "replace"))
                            .get("serverVersion") or "")
    except Exception:
        said["mine"] = ""
    follow_the_build(one, said)


def where_is_the_house(one):
    """Ask the machine this one follows what it is called and how to reach it.

    Its own round, because a page opened on this machine has no other way back: the
    list of servers a browser keeps belongs to the address it is on, and on this one
    that list is empty. It used to be asked inside the app-mirroring round, which
    returns without doing anything unless there is a folder to mirror into and half
    an hour has gone by - so a machine with nothing to mirror never found out where
    the main server was, and never looked for a newer build either.
    """
    if time.time() - HOUSE["at"] < HOUSE_EVERY:
        return
    HOUSE["at"] = time.time()
    try:
        told = ask(one, "/where", 15)
    except Exception:
        HOUSE["at"] = 0.0                 # ask again next round rather than in an hour
        return
    with LOCK:
        HOUSE["lan"] = str(told.get("lan") or "")
        HOUSE["outside"] = str(told.get("outside") or "")
        HOUSE["name"] = str(told.get("name") or "")
    remember_doors()


def follow_the_build(one, said):
    """Replace this server when the one it follows has moved on.

    The cache is only as good as the server it copies: a machine two months behind
    speaks a different language to the same app. This asks its own server to take the
    update it would have taken from the settings page - the same fetch, from the same
    place, checked against the same hash - and only when the main server is on something
    newer than this.
    """
    if not one.get("updateWith"):
        return
    if time.time() - GROWN["at"] < GROWN_EVERY:
        return
    GROWN["at"] = time.time()
    theirs = str(said.get("serverVersion") or "")
    # what this machine is running, as this machine knows it. Reading it back out of
    # the other one's answer meant that for the first minutes after the main server
    # restarted - before this machine had announced itself again - it was comparing
    # against nothing and declining to update.
    mine = str(ME.get("build") or said.get("mine") or "")
    if not theirs or not mine or not newer(theirs, mine):
        return
    req = urllib.request.Request("http://127.0.0.1:%d/update/install" % ME["port"],
                                 data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as answer:
        return json.loads(answer.read().decode("utf-8", "replace") or "{}")


def build_from_master(one):
    """The installer the other machine is running, if it has one to give.

    Returns (path, what it said) or ("", {}). Checked against the hash that machine
    sent with it: the file comes off a disk in the same house, but a file is a file
    and one that arrived wrong should not be run.
    """
    import hashlib
    import tempfile
    if not (one.get("master") and one.get("key")):
        return "", {}
    said = ask(one, "/server/build", 20) or {}
    if not said.get("version") or not said.get("sha256"):
        return "", {}
    url = (one["master"].rstrip("/") + "/server/build?file=1&t="
           + urllib.parse.quote(one["key"]))
    req = urllib.request.Request(url, headers={"X-Palladium-App": "follower"})
    onto = os.path.join(tempfile.gettempdir(),
                        "Palladium-Setup-%s.exe" % said["version"])
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=600) as answer, open(onto, "wb") as f:
        while True:
            lump = answer.read(LUMP)
            if not lump:
                break
            digest.update(lump)
            f.write(lump)
    if digest.hexdigest() != said["sha256"]:
        try:
            os.remove(onto)
        except OSError:
            pass
        raise ValueError("what arrived from the other machine does not match its hash")
    return onto, said


def try_master(one):
    """Ask the other server one question, and say plainly what came back.

    Setting two servers up is an address, a key and a folder, and getting any of the
    three wrong looks the same from the outside: nothing happens. This says which.
    """
    import urllib.error
    if not one.get("master"):
        return {"ok": False, "said": "No address for the other server."}
    if not one.get("key"):
        return {"ok": False, "said": "No key for the other server."}
    began = time.time()
    try:
        answer = ask(one, "/follow/playing?hours=1&eps=1", 20)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False,
                    "said": "That server answered, but would not take the key. "
                            "Make a new one there and paste the line again."}
        return {"ok": False, "said": "That server answered %d." % e.code}
    except urllib.error.URLError as e:
        return {"ok": False,
                "said": "Nothing answered at that address (%s)." % (
                    str(getattr(e, "reason", e))[:60],)}
    except Exception as e:
        return {"ok": False, "said": str(e)[:120]}
    took = int((time.time() - began) * 1000)
    wanted = answer.get("wanted") or []
    room = ""
    folder = one.get("folder") or ""
    if not folder:
        room = " Nowhere to keep copies yet."
    elif not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            room = " The folder cannot be made: %s." % str(e)[:60]
    return {"ok": True, "ms": took, "whose": answer.get("whose") or "",
            "wanted": len(wanted),
            "said": "Answered in %d ms. %d file%s to keep for %s.%s"
                    % (took, len(wanted), "" if len(wanted) == 1 else "s",
                       answer.get("whose") or "the main server", room)}


def try_standby(where, patience=10):
    """Knock on the machine that follows this one, and say whether it is there."""
    import urllib.error
    if not where:
        return {"ok": False, "said": "No machine has said it is following this one."}
    began = time.time()
    try:
        req = urllib.request.Request(where.rstrip("/") + "/app/version",
                                     headers={"X-Palladium-App": "server"})
        with urllib.request.urlopen(req, timeout=patience) as answer:
            answer.read(4096)
    except urllib.error.HTTPError as e:
        # it answered, which is the question being asked
        return {"ok": True, "ms": int((time.time() - began) * 1000),
                "said": "Answered %d - it is there." % e.code}
    except Exception as e:
        return {"ok": False, "said": "No answer from %s (%s)."
                                     % (where, str(e)[:60])}
    return {"ok": True, "ms": int((time.time() - began) * 1000),
            "said": "Answered in %d ms." % int((time.time() - began) * 1000)}


#: the last count of the cache, and when it was made. Walking a folder of films is
#: thousands of files; the settings page asks every time it is drawn, and the number
#: does not change between two of those.
KEPT = {"at": 0.0, "folder": "", "said": None}
KEPT_FOR = 30


def kept_here(one, fresh=False):
    """How much of the cache is used: gigabytes, files, and the share of the cap."""
    folder = one.get("folder") or ""
    if (not fresh and KEPT["said"] and KEPT["folder"] == folder
            and time.time() - KEPT["at"] < KEPT_FOR):
        said = dict(KEPT["said"])
        said["cap"] = cap_now(one)
        said["share"] = round(min(1.0, said["gb"] / said["cap"]), 3)
        return said
    cap = cap_now(one)
    if not folder or not os.path.isdir(folder):
        return {"gb": 0.0, "files": 0, "cap": cap, "share": 0.0, "free": 0.0}
    total, files = 0, 0
    for here, dirs, names in os.walk(folder):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(here, name))
                files += 1
            except OSError:
                pass
    gb = total / 1e9
    try:
        free = shutil.disk_usage(folder).free / 1e9
    except OSError:
        free = 0.0
    said = {"gb": round(gb, 1), "files": files, "cap": cap,
            "share": round(min(1.0, gb / cap), 3), "free": round(free, 1)}
    KEPT.update({"at": time.time(), "folder": folder, "said": dict(said)})
    return said


def size_of(folder):
    total = 0
    for here, dirs, names in os.walk(folder):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    return total


def size_of_ours(folder):
    """How much of that folder is this machine's own copies.

    The cap has to be measured against what this program put there, not against the
    folder. Measured against the folder, one large file somebody else left in it
    counts towards the cap, and this machine deletes its own copies for ever to make
    room it can never make.
    """
    mine = ours(folder)
    total = 0
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if not inside(folder, path):
                continue
            try:
                if os.path.relpath(path, folder).replace("\\", "/") not in mine:
                    continue
                total += os.path.getsize(path)
            except (OSError, ValueError):
                pass
    return total


#: How many times the same file may fail the same way before this machine stops
#: asking for it. Three is enough to tell a bad minute from a bad file.
GIVE_UP_AFTER = 3
#: Files not worth asking for again, by name: what was wrong, and the size and mark
#: they had when it went wrong. A file replaced at the other end is a different file
#: and is tried afresh.
GIVEN_UP = {}


def worth_asking_again(item):
    """Whether to fetch this again, or whether it has failed the same way too often.

    A file that fails the check on arrival - it is not the film that was offered, or
    not a film at all - fails it again every time, because nothing about it has
    changed. Asking anyway meant fetching the same broken files every pass, all night,
    and deleting each one as it landed: twenty-three files and seven gigabytes an hour
    for nothing.
    """
    had = GIVEN_UP.get(str(item.get("name") or ""))
    if not had:
        return True
    if (had.get("size") != item.get("size")
            or had.get("mark") != (item.get("mark") or "")):
        GIVEN_UP.pop(str(item.get("name") or ""), None)   # a different file now
        return True
    return False


def note_trouble(name, why, size=0, item=None):
    """Write down a file that would not come. Called with the lock held."""
    text = str(why)[:160]
    for row in TROUBLE:
        if row["name"] == name and row["why"] == text:
            row["times"] += 1
            if row["times"] >= GIVE_UP_AFTER and item is not None:
                # the same file, the same complaint, three times over. It is the file
                GIVEN_UP[str(name)] = {"size": item.get("size"),
                                       "mark": item.get("mark") or "",
                                       "why": text, "when": int(time.time())}
                row["given_up"] = True
            row["when"] = int(time.time())
            TROUBLE.remove(row)
            TROUBLE.insert(0, row)
            return
    TROUBLE.insert(0, {"name": str(name)[:120], "why": text, "when": int(time.time()),
                       "gb": round((size or 0) / 1e9, 2), "times": 1})
    del TROUBLE[TROUBLE_MANY:]


def troubles():
    """What has been failing, newest first."""
    with LOCK:
        return [dict(r) for r in TROUBLE]


def inside(folder, path):
    """Whether that file really is under that folder, after every link is followed.

    os.walk does not leave a folder, but a junction or a symlink inside one is a way
    out of it, and the folder itself is a path somebody typed. Nothing outside is
    looked at, listed or deleted.
    """
    try:
        root = os.path.realpath(folder)
        real = os.path.realpath(path)
    except OSError:
        return False
    return (os.path.normcase(real).startswith(os.path.normcase(root) + os.sep)
            and os.path.normcase(real) != os.path.normcase(root))


def a_sane_folder(folder):
    """Whether that path is a folder this program may keep copies in.

    A drive root, a profile, or a folder somebody's documents are in is not one. The
    setting is a path typed by hand, and the cost of a wrong one is somebody's disk.
    """
    folder = (folder or "").strip()
    if not folder or not os.path.isdir(folder):
        return False
    here = os.path.normcase(os.path.realpath(folder)).rstrip("\\/")
    if len(here.split(os.sep)) < 2 or os.path.dirname(here) == here:
        return False                       # a drive root, or the root of a disk
    no = [os.environ.get(v) for v in
          ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "ProgramFiles",
           "ProgramFiles(x86)", "ProgramData", "SystemRoot", "windir", "HOME")]
    for one in no:
        if one and os.path.normcase(os.path.realpath(one)).rstrip("\\/") == here:
            return False
    for name in ("Documents", "Desktop", "Downloads", "Pictures", "OneDrive"):
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        if os.path.normcase(os.path.join(os.path.realpath(home), name)) == here:
            return False
    return True


def ledger_file(folder):
    return os.path.join(folder, ".palladium-copies.json")


def ours(folder):
    """The files in that folder this machine fetched, by name.

    Deletion used to be by exclusion: everything under the folder that was not
    wanted tonight. Anything else somebody had in there went with it, and the
    setting is a path typed by hand - one wrong folder and it empties a disk. A file
    is deleted now only if it is written here, which is to say only if this machine
    put it there.
    """
    try:
        with io.open(ledger_file(folder), encoding="utf-8") as f:
            said = json.loads(f.read())
        return set(str(n) for n in (said.get("files") or []))
    except (OSError, ValueError, AttributeError):
        return set()


def note_ours(folder, path):
    """Write down that this machine wrote that file, before it can be deleted."""
    try:
        name = os.path.relpath(path, folder).replace("\\", "/")
    except ValueError:
        return
    mine = ours(folder)
    if name in mine:
        return
    mine.add(name)
    try:
        with io.open(ledger_file(folder), "w", encoding="utf-8") as f:
            f.write(json.dumps({"files": sorted(mine)}))
    except OSError:
        pass


def claim_folder(folder):
    """Say once that everything already in this folder is Palladium's own cache.

    Copies made before there was a record of what came from where cannot be told
    apart from somebody's own files, so the cap will not touch them and the folder
    grows past its cap for ever. This is the way back, and it is asked for by hand,
    once, with the folder named.

    Only files a player could open are taken. A folder of archives and disk images is
    not a cache whatever anybody presses, and anything that is not a film or a
    subtitle stays untracked and untouchable.
    """
    if not a_sane_folder(folder):
        return {"taken": 0, "skipped": 0, "why": "not a folder to keep copies in"}
    mine = ours(folder)
    taken, skipped, others = 0, 0, []
    for one in extras(folder):
        if one["name"].lower().endswith(FILMS):
            mine.add(one["name"])
            taken += 1
        else:
            skipped += 1
            if len(others) < 20:
                others.append(one["name"])
    try:
        with io.open(ledger_file(folder), "w", encoding="utf-8") as f:
            f.write(json.dumps({"files": sorted(mine)}))
    except OSError:
        return {"taken": 0, "skipped": 0, "why": "could not write the record"}
    return {"taken": taken, "skipped": skipped, "others": others}


def extras(folder):
    """Files in the cache folder this machine did not fetch, largest first.

    They are never deleted by this program - it deletes only what it wrote - so the
    only way they go is somebody looking at the list and saying so.
    """
    if not a_sane_folder(folder):
        return []
    mine = ours(folder)
    out = []
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if name == os.path.basename(ledger_file(folder)):
                continue
            if not inside(folder, path):
                continue
            try:
                known = os.path.relpath(path, folder).replace("\\", "/")
            except ValueError:
                continue
            if known in mine:
                continue
            try:
                out.append({"name": known, "bytes": os.path.getsize(path)})
            except OSError:
                pass
    out.sort(key=lambda f: -f["bytes"])
    return out


#: what the last pass wanted kept. A clear by hand is the same rule as the cap's
#: own, and neither may take what somebody is going to watch tonight.
KEEPING = {"set": set(), "when": 0.0}


def could_go(folder, keeping, cap_bytes=0, how="oldest"):
    """What a clear would delete, in the order it would go, and how much it frees.

    Read before anything is deleted and before automatic clearing is switched on:
    the answer to "what will this do" has to be available before it is done.
    """
    if not a_sane_folder(folder):
        return {"files": 0, "gb": 0.0, "rows": [], "folder": folder, "sane": False}
    mine = ours(folder)
    files, total = 0, 0
    rows = []
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if path in keeping or not inside(folder, path):
                continue
            try:
                known = os.path.relpath(path, folder).replace("\\", "/")
                if known not in mine:
                    continue
                rows.append((os.path.getatime(path), os.path.getsize(path), known))
            except (OSError, ValueError):
                pass
    rows.sort(key=(lambda r: -r[1]) if how == "largest" else None)
    # only as far down the list as the cap actually reaches. Everything under it
    # stays, and saying otherwise would overstate what switching this on does.
    have = size_of_ours(folder)
    going = []
    for when, size, known in rows:
        if cap_bytes and have <= cap_bytes:
            break
        going.append({"name": known, "bytes": size,
                      "idle": int(max(0, time.time() - when))})
        have -= size
        total += size
        files += 1
    return {"files": files, "gb": round(total / 1e9, 2), "rows": going[:200],
            "folder": os.path.abspath(folder), "sane": True,
            "used": round(size_of_ours(folder) / 1e9, 2)}


def make_room(folder, cap_bytes, keeping, how="oldest"):
    """Delete until the folder is inside its cap, in the order asked for.

    Nothing on the list is deleted, whatever its age: the point of the cache is that
    it is there when the main server is not. What goes is chosen from the rest, either the
    one nobody has touched for longest - which is the honest default, since it is the
    one nobody will miss - or the largest, which empties the disk in fewer deletions
    and is what somebody wants when one film is in the way of ten episodes.

    And only ever a file this machine fetched. The folder is a path somebody typed.
    """
    if not a_sane_folder(folder):
        return                             # not a folder to be deleting things in
    mine = ours(folder)
    files = []
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if path in keeping or not inside(folder, path):
                continue
            try:
                known = os.path.relpath(path, folder).replace("\\", "/")
            except ValueError:
                continue
            if known not in mine:
                continue                   # not this machine's to delete
            try:
                files.append((os.path.getatime(path), os.path.getsize(path), path))
            except OSError:
                pass
    if how == "largest":
        files.sort(key=lambda f: -f[1])
    else:
        files.sort()
    total = size_of_ours(folder)
    for when, size, path in files:
        if total <= cap_bytes:
            return
        try:
            os.remove(path)
            total -= size
            mine.discard(os.path.relpath(path, folder).replace("\\", "/"))
        except OSError:
            pass
    try:
        with io.open(ledger_file(folder), "w", encoding="utf-8") as f:
            f.write(json.dumps({"files": sorted(mine)}))
    except OSError:
        pass


#: Which files this machine is serving this minute, whatever the main server wants.
#: Set by the server at startup. A player between two range requests holds no handle,
#: so the lock this used to rely on is not there for most of a film.
BUSY = None

#: How long a file this machine fetched is kept after the main server stops naming it.
#: A shuffle's queue moves as it is watched, so what is wanted now is wanted again
#: shortly; the cap still frees room when room is short.
KEEP_UNWANTED_HOURS = 12


def clear_unwanted(folder, qualified, keeping):
    """Delete what this machine fetched and the main server has stopped wanting.

    Everything the main server named is safe, whether tonight's hour lets it move or not:
    the list to test against is what the main server wants at all, never the shorter list
    of what may be fetched this minute.

    Only ever a file this machine fetched - the folder is a path somebody typed - and
    only when the main server actually named something. An empty list is an answer nobody
    should act on by emptying a disk, and a file somebody is watching refuses to be
    deleted and stays.
    """
    if not a_sane_folder(folder) or not qualified:
        return {"files": 0, "gb": 0.0}
    mine = ours(folder)
    gone, freed = 0, 0
    # Nothing fetched in the last few hours is deleted for being unwanted.
    #
    # A shuffle's next ten change as it is watched, so a file leaves the list and
    # joins it again a little later - and deleting the moment it left had the same
    # episodes fetched over and over: in one day, 381 copies of 207 files, 86 of them
    # fetched twice and some six times, 332 GB moved to hold 207 files. Room is not
    # the reason to hurry: make_room() frees what the cap needs, when the cap needs
    # it. This is only about what is no longer named, and that can wait.
    young = time.time() - KEEP_UNWANTED_HOURS * 3600
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if path in qualified or path in keeping or not inside(folder, path):
                continue
            if BUSY:
                try:
                    if os.path.normcase(path) in BUSY():
                        continue          # somebody is watching this one right now
                except Exception:
                    pass
            if name.endswith(".part"):
                continue                   # arriving now, not left over
            try:
                if os.path.getmtime(path) > young:
                    continue               # fetched lately: it may be wanted again
            except OSError:
                pass
            try:
                known = os.path.relpath(path, folder).replace("\\", "/")
            except ValueError:
                continue
            if known not in mine:
                continue                   # not this machine's to delete
            try:
                size = os.path.getsize(path)
                os.remove(path)
            except OSError:
                continue                   # open, or gone already: leave it
            mine.discard(known)
            gone += 1
            freed += size
    if gone:
        try:
            with io.open(ledger_file(folder), "w", encoding="utf-8") as f:
                f.write(json.dumps({"files": sorted(mine)}))
        except OSError:
            pass
    return {"files": gone, "gb": round(freed / 1e9, 2)}


def copy_file(one, item, folder):
    """Fetch one film or episode, carrying on from whatever is already here."""
    name = a_safe_name(item.get("name"))
    if not name:
        raise ValueError("refused the name %r" % (item.get("name"),)[:80])
    into = os.path.join(folder, name)
    # and it lands inside the folder that was asked for, whatever the name says
    if os.path.dirname(os.path.abspath(into)) != os.path.abspath(folder):
        raise ValueError("refused a name that points outside the folder")
    if os.path.exists(into) and os.path.getsize(into) == item.get("size"):
        return into                        # already here, whole
    part = into + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    # a subtitle beside a video is asked for by that video and its number; the film
    # itself by the part it is
    if item.get("side") is not None:
        url = (one["master"].rstrip("/") + "/follow/side?part=%s&n=%s&t=%s"
               % (item.get("part"), item["side"],
                  urllib.parse.quote(one["key"])))
    else:
        url = (one["master"].rstrip("/") + "/local/parts/" + str(item["part"]) +
               "?t=" + urllib.parse.quote(one["key"]))
    req = urllib.request.Request(url, headers={"X-Palladium-App": "follower"})
    if have:
        req.add_header("Range", "bytes=%d-" % have)
    said = int(item.get("size") or 0)
    with urllib.request.urlopen(req, timeout=60) as answer:
        with open(part, "ab" if have else "wb") as f:
            while True:
                if STOP.is_set():
                    raise InterruptedError("asked to stop")
                lump = answer.read(LUMP)
                if not lump:
                    break
                f.write(lump)
                have += len(lump)
                # a file that keeps coming after the size it was said to be is not
                # the file that was offered
                if said and have > said + LUMP:
                    raise ValueError("the file is bigger than it was said to be")
                with LOCK:
                    STATE["copying"] = name
                    STATE["at"] = (have / said) if said else 0.0
                    STATE["size"] = said
                    STATE["pinned"] = bool(item.get("pinned"))
    if os.path.getsize(part) < 16:
        # nothing came down at all - the other machine was restarting, or the
        # connection went before the first block. Not a bad file: an absent one.
        os.remove(part)
        raise ValueError("nothing arrived; will ask again")
    if not looks_like_film(part, into):
        os.remove(part)
        raise ValueError("what arrived is not a film")
    # and it is the film that was offered, not another one of the same length
    offered = str(item.get("mark") or "")
    if offered and quick_mark(part) != offered:
        os.remove(part)
        raise ValueError("what arrived is not the file that was offered")
    os.replace(part, into)
    note_ours(folder, into)
    with LOCK:
        STATE["copying"] = ""
        STATE["size"] = 0
        STATE["pinned"] = False
        STATE["at"] = 0.0
    return into


def adopt_folder(one, lib):
    """Make sure the folder the copies land in is one this library reads.

    A folder nothing scans is a folder nobody can watch: the copies arrive, the
    shelves stay empty, and the machine looks broken on exactly the evening it is
    needed. Set through the settings page this happens when the folder is chosen;
    a folder set before that, or by hand, is adopted here.
    """
    folder = (one.get("folder") or "").strip()
    if not folder or not lib:
        return
    cfg = lib.config()
    here = os.path.normcase(os.path.abspath(folder))
    known = [f for name in ("movies", "tv", "mixed") for f in (cfg.get(name) or [])]
    if any(os.path.normcase(os.path.abspath(f)) == here for f in known):
        return
    cfg["mixed"] = list(cfg.get("mixed") or []) + [folder]
    lib.save_config(cfg)


def as_ours(rows):
    """The main server's owner, read as whoever owns this machine.

    This is a second server, not a second person: the places that arrive under the
    house's own name belong, here, to whoever sits at this one.
    """
    out = []
    for row in rows or []:
        row = dict(row)
        if HOUSE["owner"] and row.get("who") == HOUSE["owner"]:
            row["who"] = "me"
        out.append(row)
    return out


#: What the other machine allows this one to use, in gigabytes, as it last said.
#: Zero until it says anything - an older master says nothing, and then the setting
#: on this machine is the only one there is.
#: What the machine this one follows allows it: a ceiling in gigabytes, nought
#: for "as much as you allow yourself", and whether it may take copies at all. Both
#: belong to the key this machine came in with, so one house may lend a library to
#: two machines on different terms.
ALLOWED = {"gb": 0.0, "mayCopy": True}


def cap_now(one):
    """The smaller of what this machine was set to and what the other one allows."""
    mine = max(1.0, float(one.get("cap") or 200))
    theirs = float(ALLOWED.get("gb") or 0)
    return min(mine, theirs) if theirs > 0 else mine


def learn_the_facts(one, lib):
    """Ask the main server what is inside the files here that nobody has measured.

    A file that arrives by copy is never opened: this machine has no encoder and no
    reason to read twenty gigabytes to find out what the other one already knows. But
    a file with no codec and no duration is one a player will not risk playing as it
    stands - it asks for a transcode instead, of a file it could have sent straight
    through, and on a machine with nothing to transcode with that is a five hundred
    and a stopped picture.

    So the names go up and the measurements come back. One pass, only for rows that
    have none, at most eighty at a time.
    """
    if not lib:
        return 0
    con = lib.db()
    try:
        rows = con.execute(
            "SELECT id, path FROM file WHERE vcodec IS NULL OR duration IS NULL "
            "OR duration <= 0 LIMIT 80").fetchall()
        if not rows:
            return 0
        by_name = {}
        for row in rows:
            by_name.setdefault(os.path.basename(row["path"] or ""), []).append(row["id"])
        said = tell(one, "/follow/whatis", {"names": list(by_name)}, 40) or {}
        facts = said.get("facts") or {}
        filled = 0
        for name, ids in by_name.items():
            keep = {k: v for k, v in (facts.get(name) or {}).items() if v is not None}
            # when the episode went out belongs to the episode, not to the file
            aired = keep.pop("aired", None)
            if aired:
                try:
                    con.execute(
                        "UPDATE episode SET aired=? WHERE aired IS NULL AND id IN "
                        "(SELECT episode_id FROM file WHERE id IN (%s))"
                        % ",".join("?" * len(ids)), [aired] + list(ids))
                except Exception:
                    pass
            if not keep:
                continue
            for one_id in ids:
                con.execute(
                    "UPDATE file SET " + ", ".join(k + "=?" for k in keep) +
                    ", probed=1 WHERE id=?", list(keep.values()) + [one_id])
                filled += 1
        if filled:
            con.commit()
        return filled
    except Exception:
        return 0
    finally:
        con.close()


#: file names whose key here already matches the main server's, and what moves the settings
HOUSE_KEYS = {"ok": set(), "carry": None}


def take_the_house_keys(one, lib, api=None, carry=None):
    """File what is here under the main server's keys.

    A key comes from the title and year as each library holds them, and a copy often
    has no year, so a programme here had a key the main server did not know. The main server says
    per file what it calls it; what differs moves, rows and settings alike.
    """
    if not lib:
        return 0
    # Keys being watched this minute are left alone: moving one strands the place.
    # Only those - anything playing at all used to stop the whole round, so a paused
    # tab left open meant a file fetched today never took the main server's key for it,
    # and the key that machine drew for itself answered nothing the main server asked for.
    busy = set()
    try:
        if api is not None:
            for key, said in (api.playing_now() or {}).items():
                busy.add(str(key))
                if said.get("key"):
                    busy.add(str(said["key"]))
    except Exception:
        return 0
    con = lib.db()
    try:
        rows = con.execute("SELECT path, item_id, episode_id FROM file").fetchall()
    finally:
        con.close()
    by_name = {}
    for row in rows:
        name = os.path.basename(row["path"] or "")
        if str(row["item_id"] or "") in busy or str(row["episode_id"] or "") in busy:
            continue              # asked again next round, when it has finished
        if name and name.lower() not in HOUSE_KEYS["ok"]:
            by_name[name] = row
    if not by_name:
        return 0
    titles, episodes, owner, clash, sure = {}, {}, {}, set(), []
    named = {}
    names = list(by_name)
    for at in range(0, len(names), 400):
        said = tell(one, "/follow/whatis", {"names": names[at:at + 400]}, 40) or {}
        if "items" not in said:
            return 0              # an older house cannot say
        for name in names[at:at + 400]:
            item = str((said.get("items") or {}).get(name) or "")
            if not item:
                continue          # not the main server's: asked again next round
            row = by_name[name]
            here = str(row["item_id"] or "")
            if titles.get(here, item) != item:
                clash.add(here)
            titles[here] = item
            told = (said.get("titles") or {}).get(name)
            if told and item:
                named[item] = told
            key = str((said.get("keys") or {}).get(name) or "")
            if row["episode_id"] and key.startswith("e"):
                episodes[str(row["episode_id"])] = key
                owner[str(row["episode_id"])] = here
            sure.append(name.lower())
    titles = {k: v for k, v in titles.items() if k and k != v and k not in clash}
    episodes = {k: v for k, v in episodes.items() if k != v and owner.get(k) not in clash}
    moved = lib.rekey(titles, episodes) if (titles or episodes) else {}
    # and the words, under whichever key the title ended up with. Only the words: the
    # keys were settled above, by file name, which is the one thing both machines see
    # the same.
    if named:
        # under whichever key the title ended up with. They were collected against the
        # key each file had before the round, and the round moves them: written under
        # the old one, the rename found no such title and did nothing - so the titles
        # that had just been moved, which are the ones most likely to be misnamed, kept
        # their file names until some later round happened to need no move at all.
        settled = {titles.get(k, k): v for k, v in named.items()}
        try:
            lib.name_as_told(settled)
        except Exception:
            pass                      # a name is not worth a failed round
    if carry and (moved.get("titles") or moved.get("episodes")):
        carry(moved)
    HOUSE_KEYS["ok"].update(sure)
    return len(moved.get("titles") or {}) + len(moved.get("episodes") or {})


def dress_the_copies(one, lib, folder, wanted):
    """Give what has arrived the catalogue's own name and pictures.

    This machine can hold a film without being able to say what it is: it reaches the
    house over the network and TMDB over the internet, and the second is not always
    there. The main server already knows - it is the machine the film came from - so the
    number, the poster and the backdrop come with the file, and the pictures
    themselves are fetched from it rather than from the catalogue.
    """
    if not lib:
        return
    con = lib.db()
    dressed = 0
    try:
        # By name, not by path. The same file is "D:/Palladium\x.mkv" to whoever
        # typed the folder with one kind of slash and "D:\Palladium\x.mkv" to
        # whoever typed the other, and comparing the two as text finds nothing.
        beside = {}
        for row in con.execute("SELECT id, path, item_id, episode_id FROM file"):
            beside[os.path.basename(row["path"] or "").lower()] = row
        for item in wanted or []:
            art = item.get("art") or {}
            # a title dressed once stays dressed; this is only for what is new
            name = a_safe_name(item.get("name"))
            if not (art.get("poster") or art.get("tmdb")) or not name:
                continue
            if not os.path.exists(os.path.join(folder, name)):
                continue
            found = beside.get(name.lower())
            if not found:
                continue
            # what the main server measured, written down here as measured rather than as
            # unknown: an unprobed file has no codec, no size on screen and no
            # duration, and a player handed one of those asks for a transcode
            facts = item.get("facts") or {}
            if facts:
                keep = {k: facts.get(k) for k in
                        ("duration", "container", "vcodec", "acodec", "width",
                         "height", "channels", "bitrate")
                        if facts.get(k) is not None}
                if keep:
                    con.execute(
                        "UPDATE file SET " +
                        ", ".join(k + "=?" for k in keep) + ", probed=1 WHERE id=?",
                        list(keep.values()) + [found["id"]])
            if found["episode_id"]:
                row = con.execute("SELECT item_id FROM episode WHERE id=?",
                                  (found["episode_id"],)).fetchone()
            else:
                row = {"item_id": found["item_id"]}
            if not row or not row["item_id"]:
                continue
            said = con.execute("SELECT poster, tmdb_id FROM item WHERE id=?",
                               (row["item_id"],)).fetchone()
            if said and said["poster"]:
                continue                  # already dressed
            con.execute("""UPDATE item SET tmdb_id = COALESCE(?, tmdb_id),
                                           poster = COALESCE(?, poster),
                                           backdrop = COALESCE(?, backdrop),
                                           identified = 1
                           WHERE id = ?""",
                        (art.get("tmdb"), art.get("poster"), art.get("backdrop"),
                         row["item_id"]))
            dressed += 1
            # and the picture itself, from the machine that has it already
            for which, size in (("poster", "w500"), ("backdrop", "w780")):
                path = art.get(which)
                if not path:
                    continue
                spot = os.path.join(lib.root, "cache",
                                    size + "_" + path.strip("/").replace("/", "_"))
                if os.path.exists(spot) and os.path.getsize(spot):
                    continue
                try:
                    url = (one["master"].rstrip("/") + "/local/art/%s/%s?t=%s"
                           % (_art_key(item), which,
                              urllib.parse.quote(one["key"])))
                    req = urllib.request.Request(
                        url, headers={"X-Palladium-App": "follower"})
                    with urllib.request.urlopen(req, timeout=30) as answer:
                        blob = answer.read()
                    # a picture, not an error page: two bytes are enough
                    if blob[:2] == bytes([0xFF, 0xD8]) or blob[:4] == bytes([0x89, 80, 78, 71]):
                        os.makedirs(os.path.dirname(spot), exist_ok=True)
                        with open(spot, "wb") as f:
                            f.write(blob)
                except Exception:
                    pass                  # a missing picture is not worth a fault
        con.commit()
    finally:
        con.close()
    return dressed


def _art_key(item):
    """Which number the main server files the picture under.

    The main server answers /local/art/<key>/poster for a film by its own number and for
    an episode by its programme's - which is the same answer it gives its own pages.
    """
    return str((item.get("art") or {}).get("owner") or item.get("key") or "")


def name_the_strangers(one, folder, known):
    """Ask the main server what the files it never listed are called.

    Copying began before this machine started writing down what each file is, so a
    disk full of films answered to nothing: they were here, they played, and the
    house was never told it had them. The main server knows every one of them by name.
    """
    try:
        here = [n for n in os.listdir(folder)
                if not n.startswith(".") and not n.endswith(".part")]
    except OSError:
        return known
    strangers = [n for n in here if n not in known][:400]
    if not strangers:
        return known
    try:
        said = tell(one, "/follow/whatis", {"names": strangers}, 40)
    except Exception:
        return known                  # an older master cannot answer this
    for name, key in (said.get("keys") or {}).items():
        if key:
            known[name] = key
    return known


def say_what_is_here(one, folder, wanted):
    """Tell the main server everything on this disk, by the numbers it files them under.

    Not only what is on today's list. A film copied last week for somebody who has
    since finished it is still here and still plays - for anyone, since what is on
    this machine is on it for the main server - and the main server should know that. So the
    names it has been given are remembered, and everything still on the disk is
    reported whether it was asked for this time or not.

    Said before the cacheing starts as well as after it: a pass that fetches two
    seven-gigabyte films takes an hour, and until it ended the main server was told nothing.
    """
    known = name_the_strangers(one, folder, _remember_keys(folder, wanted))
    _keep_book(folder, known)
    holding = []
    for name, key in known.items():
        here = os.path.join(folder, name)
        if os.path.exists(here) and os.path.getsize(here) > 0:
            holding.append(key)
    try:
        # whole: this list was built by looking at the disk, so it is everything
        # this machine holds and anything missing from it has gone. The short report
        # sent when copying starts carries no such claim.
        said = tell(one, "/follow/holding",
                    {"keys": holding, "whole": True,
                     "trouble": troubles()[:40]}) or {}
        if said.get("cap") is not None:
            ALLOWED["gb"] = max(0.0, float(said.get("cap") or 0))
        if said.get("mayCopy") is not None:
            ALLOWED["mayCopy"] = bool(said.get("mayCopy"))
        # and if the main server's keys have changed since the ones here were taken, come
        # back for them now rather than on the quarter-hour: a key handed to somebody
        # is no use to them until this machine has it too.
        told = int(said.get("invitesAt") or 0)
        if told and told != KEYS.get("stamp"):
            KEYS["stamp"] = told
            KEYS["last"] = 0.0
    except Exception:
        pass                          # an older master has no such door
    return holding


def _remember_keys(folder, wanted):
    """What each file here is called on the machine it came from.

    Kept beside the copies, because the list of what is wanted changes every minute
    and a file that has fallen off it has not fallen off the disk.
    """
    book = os.path.join(folder, ".palladium-keys.json")
    known = {}
    try:
        with open(book, encoding="utf-8") as f:
            known = json.load(f) or {}
    except (OSError, ValueError):
        known = {}
    before = len(known)
    for item in wanted or []:
        name = a_safe_name(item.get("name"))
        if name and item.get("key"):
            known[name] = item["key"]
    # anything gone from the disk is gone from the book as well
    for name in list(known):
        if not os.path.exists(os.path.join(folder, name)):
            known.pop(name, None)
    if len(known) != before:
        _keep_book(folder, known)
    return known


def _keep_book(folder, known):
    """Write the names down, so a restart does not start the asking again."""
    book = os.path.join(folder, ".palladium-keys.json")
    try:
        tmp = book + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
        os.replace(tmp, book)
    except OSError:
        pass                          # the cache still works without the book


def light_round(one, lib=None, api=None):
    """The quick half, for while a long copy is in flight.

    Fetching two seven-gigabyte films takes an hour, and nothing else used to happen
    in that hour: the main server heard nothing about this machine, the places did not
    travel, and the posters wore no dot for films that were already here. None of
    that needs to wait for a disk.
    """
    folder = one.get("folder") or ""
    if not folder:
        return
    try:
        # and how full it is, which is the one thing about this machine the main server
        # cannot work out for itself
        room = kept_here(one)
        ask(one, "/follow/here?port=%d&name=%s&outside=%s&build=%s"
                 "&gb=%.1f&free=%.1f&cap=%.1f&files=%d&managed=%d"
                 % (int(ME["port"]), urllib.parse.quote(ME["name"][:40]),
                    urllib.parse.quote((one.get("outside") or "").strip()),
                    urllib.parse.quote(ME.get("build") or ""),
                    room.get("gb") or 0.0, room.get("free") or 0.0,
                    room.get("cap") or 0.0, int(room.get("files") or 0),
                    1 if one.get("allowRemote") else 0), 15)
    except Exception:
        pass
    if api and time.time() - PLACES["at"] > 120:
        PLACES["at"] = time.time()
        try:
            said = ask(one, "/follow/progress?since=%d" % PLACES["since"], 30)
            api.take_progress(as_ours(said.get("progress") or []))
            # the five minutes of overlap are so a place written while this was asked
            # is not missed. Not when there was more than fitted in one answer: then
            # "now" is the last place sent, and going back before it never gets past.
            mark = int(said.get("now") or 0)
            PLACES["since"] = mark if said.get("more") else mark - 300
        except Exception:
            pass
    try:
        forward = "hours=%s&eps=%s&casual=%s&whole=%d&these=%s&for=%s" % (
            hours_wanted(one), one.get("episodes") or 6,
            one.get("casualHours") or 0, 1 if one.get("wholeList") else 0,
            ",".join(kinds_now(one)),
            urllib.parse.quote(for_whom(one)))
        said = ask(one, "/follow/playing?deck=1&" + forward, 30)
        wanted = said.get("wanted") or []
        say_what_is_here(one, folder, wanted)
        if lib:
            dress_the_copies(one, lib, folder, wanted)
            learn_the_facts(one, lib)
    except Exception:
        pass


def round_of(one, lib=None, api=None):
    """One pass: ask what is being watched, and copy what is not here yet."""
    folder = one["folder"]
    if not folder:
        return
    if not PASS.acquire(blocking=False):
        return                            # one already running: it will see this too
    try:
        _round(one, lib, api, folder)
    finally:
        PASS.release()


def _round(one, lib, api, folder):
    """The pass itself, with the door held by round_of."""
    os.makedirs(folder, exist_ok=True)
    adopt_folder(one, lib)
    # say where this machine can be reached, so the main server can hand the address to
    # its viewers: when it is off, they have somewhere to go
    try:
        # and how full it is, which is the one thing about this machine the main server
        # cannot work out for itself
        room = kept_here(one)
        ask(one, "/follow/here?port=%d&name=%s&outside=%s&build=%s"
                 "&gb=%.1f&free=%.1f&cap=%.1f&files=%d&managed=%d"
                 % (int(ME["port"]), urllib.parse.quote(ME["name"][:40]),
                    urllib.parse.quote((one.get("outside") or "").strip()),
                    urllib.parse.quote(ME.get("build") or ""),
                    room.get("gb") or 0.0, room.get("free") or 0.0,
                    room.get("cap") or 0.0, int(room.get("files") or 0),
                    1 if one.get("allowRemote") else 0), 15)
    except Exception:
        pass                               # an older master has no such door
    # and take the main server's invitations, so the links guests already hold work here
    if KEYS["learn"] and time.time() - KEYS["last"] > 900:
        KEYS["last"] = time.time()
        try:
            said = ask(one, "/follow/invites", 20)
            told = dict(said.get("owner") or {})
            told["ownerIs"] = said.get("ownerIs") or ""
            told["ownerName"] = said.get("ownerName") or ""
            HOUSE["owner"] = told["ownerIs"]
            HOUSE["ownerName"] = told["ownerName"]
            KEYS["learn"](said.get("invites") or [], told,
                          said.get("name") or one.get("master") or "",
                          # and how the main server draws each person's subtitles, so an
                          # evening that moves here mid-film looks the same
                          said.get("look") or {})
            # the main server's catalogue key, so copies arrive as films with posters
            # rather than as file names
            if lib and said.get("tmdb"):
                cfg = lib.config()
                if not (cfg.get("tmdb_key") or "").strip():
                    cfg["tmdb_key"] = said["tmdb"]
                    cfg.setdefault("language", said.get("language") or "en-US")
                    lib.save_config(cfg)
        except Exception:
            pass
    forward = "hours=%s&eps=%s&casual=%s&whole=%d&these=%s&for=%s" % (
        hours_wanted(one), one.get("episodes") or 6,
        one.get("casualHours") or 0, 1 if one.get("wholeList") else 0,
        ",".join(kinds_now(one)),
        urllib.parse.quote(for_whom(one)))
    # One question, always the whole of it: what is on a screen, what people are
    # part-way through, and every watchlist. Asking a short question first and a
    # fuller one afterwards gave the main server two different lists - and the page could
    # only ever show one of them, so whatever was being fetched from a watchlist was
    # missing from the queue while it arrived. What the hour decides is what is acted
    # on, below, not what is asked for.
    said = ask(one, "/follow/playing?deck=1&" + forward, 60)
    names = said.get("house")
    if isinstance(names, list):
        HOUSE["people"] = [str(n)[:60] for n in names][:40]
    # inside the set night hours, or after Early, take copies of everything anybody
    # is in the middle of - not only what is on at this moment
    # the main server said it is going to bed early, on the machine with the library on it
    try:
        told = float(said.get("earlyUntil") or 0)
        if told > time.time():
            EARLY["until"] = max(EARLY["until"], told)
    except (TypeError, ValueError):
        pass
    stock = stocking_up(one)
    with LOCK:
        STATE["stocking"] = stock
        # a key of one's own on somebody else's server keeps that person's films and
        # nobody else's; a key marked as a following server keeps the main server's
        STATE["whose"] = said.get("whose") or ""
    wanted = said.get("wanted") or []
    # What there is a reason to hold, films and subtitles alike, before the hour
    # below decides what may move tonight. A file is swept when it is on neither
    # this list nor the shorter one, and testing against the shorter one alone would
    # delete the whole cache every morning.
    # Everything the main server would keep, whatever the hour - not the shorter list
    # of what may be fetched this minute. A main server too old to send it falls back
    # to the short list, which is what this did before.
    holding = said.get("holding")
    if not isinstance(holding, list) or not holding:
        holding = wanted
    qualified = set()
    for item in holding:
        safe = a_safe_name(item.get("name"))
        if safe:
            qualified.add(os.path.join(folder, safe))
    say_what_is_here(one, folder, wanted)
    # The main server lists what is on a screen now first. A pass that is half way through
    # a seventeen-gigabyte film nobody is watching should not make somebody wait for
    # their next episode, so anything small enough to arrive in a minute or two is
    # taken first - the big one carries on from where it stopped, next round.
    # Taken in the order it was given. The machine with the library on it knows who
    # is sitting in front of a screen and what they will want in twenty minutes; this
    # one knows only what it has. Sorting the list again here put files somebody had
    # moved up by hand back on top of the person watching - the list arrived right
    # and was spoiled on arrival.
    keeping = set()
    # Forty fetched, not forty looked at. The list carries everything worth having
    # and most of it is already on this disk; counting those against the window meant
    # a machine holding a hundred and twenty files never reached the films at the
    # bottom of it, and the queue read as a list of things that were never taken.
    # What somebody started watching this minute is fetched at night, not by day.
    #
    # At night this machine is the one that will be awake, so the episode after the
    # one on screen has to be here before the main server sleeps. By day the main server is
    # answering for itself, and chasing every episode somebody starts spends the link
    # on a copy nobody is going to need for hours.
    #
    # When a kind may be taken is the kind's own setting now - off, inside the hours,
    # or always - and the main server has already left out whatever this machine is
    # not taking this minute. There used to be a switch under all of them that said
    # nothing at all before night, whatever the kinds said: two answers to one
    # question, and the quieter one won without saying so.
    # A key may be for reading only. Then this machine still knows the library and
    # still answers for it, and takes no copies of anything.
    if not ALLOWED.get("mayCopy", True):
        STATE["why"] = "this key may read the library but not copy it"
        wanted = []

    # Everything on the list is kept, whether this pass gets to it or not.
    #
    # What is protected from deletion used to be only what the loop below reached,
    # and the loop stops after forty files - so on a long list the far end was
    # unprotected and swept away at the end of the pass to stay under the cap. That
    # is where a watchlist went: named as wanted, never reached, deleted the same
    # night, and asked for again the next one.
    for item in wanted:
        safe = a_safe_name(item.get("name"))
        if item.get("part") and safe:
            keeping.add(os.path.join(folder, safe))

    # Everything on the list this machine already has, by the main server's own numbers.
    # Saying so needs no matching of filenames back to keys - the list gives both -
    # and it is the only reliable way the main server learns what is here, which is what a
    # queue needs to stop offering files that arrived days ago.
    got_already = []

    def still_wanted(item):
        """Whether this one is worth fetching: named, a file, not here, not hopeless."""
        if not item.get("part"):
            return False
        if item.get("casual"):
            return False              # on a screen, unchosen: kept, not fetched
        if not worth_asking_again(item):
            return False
        safe = a_safe_name(item.get("name"))
        if not safe:
            with LOCK:
                STATE["why"] = "refused a file named %.40s" % (item.get("name") or "")
            return False
        here = os.path.join(folder, safe)
        keeping.add(here)
        if (os.path.exists(here) and os.path.getsize(here) == item.get("size")
                and (not item.get("mark") or quick_mark(here) == item["mark"])):
            if item.get("key") and item["key"] not in got_already:
                got_already.append(item["key"])
            return False
        return True

    # said before any copying starts, so the queue is right from the first minute
    for w in wanted:
        still_wanted(w)
    if got_already:
        try:
            tell(one, "/follow/holding", {"keys": got_already})
        except Exception:
            pass

    # What has no reason to be here goes before anything is fetched, not after. At
    # the end of the pass it ran once a pass - and a pass with a backlog runs for
    # hours, so on a busy night it never ran at all. It belongs here anyway: free the
    # disk, then fill it. A read-only key copies nothing and has nothing to delete.
    if ALLOWED.get("mayCopy", True):
        swept = clear_unwanted(folder, qualified, keeping)
        if swept["files"]:
            with LOCK:
                STATE["swept"] = swept

    taken_now = 0
    tried_and_failed = set()
    while taken_now < 40:
        # The top of the list as it stands, every time. Working through a list taken
        # at the start of the pass meant fetching what was wanted an hour ago while
        # the queue on screen had moved on - and the two could never agree.
        item = next((w for w in wanted
                     if str(w.get("name") or "") not in tried_and_failed
                     and still_wanted(w)), None)
        if item is None:
            break
        try:
            copy_file(one, item, folder)
            # A subtitle is a few kilobytes riding along with its film, not one of
            # the forty files a pass is worth. Counting them meant a pass could spend
            # its whole allowance on subtitles and stop before reaching the film at
            # the top of the queue - which is where a five-gigabyte film sat for a
            # day while the queue said it was next.
            if item.get("side") is None:
                taken_now += 1
            with LOCK:
                STATE["files"] += 1
            # A pass fetching a dozen files runs for an hour, and until it ended
            # nothing that had arrived had a name or a poster. Every few files, the
            # library is brought up to date and what is here is dressed.
            if lib and STATE["files"] % 4 == 0:
                try:
                    lib.scan(probe=True, identify=False)
                    dress_the_copies(one, lib, folder, wanted)
                    learn_the_facts(one, lib)
                except Exception:
                    pass
        except InterruptedError:
            with LOCK:
                STATE["copying"], STATE["at"] = "", 0.0
                STATE["size"], STATE["pinned"] = 0, False
            return                        # asked to stop: leave the rest for later
        except Exception as e:
            with LOCK:
                STATE["why"] = str(e)[:160]
                note_trouble(item.get("name") or item.get("title") or "?", e,
                             item.get("size") or 0, item)
            # one that will not come is set aside for this pass rather than tried
            # again immediately: without this the loop asks for it for ever and
            # nothing below it in the queue is ever reached
            tried_and_failed.add(str(item.get("name") or ""))
            continue
        # and the list again, because it has had a whole file's worth of time to
        # change: somebody has started an episode, or moved something up
        try:
            said = ask(one, "/follow/playing?deck=1&" + forward, 60)
            fresh = said.get("wanted") or []
            if fresh:
                wanted = fresh
                for w in wanted:
                    safe = a_safe_name(w.get("name"))
                    if w.get("part") and safe:
                        keeping.add(os.path.join(folder, safe))
        except Exception:
            pass                          # the main server is busy: carry on with this list
    # where the main server had got to in what it is watching, so Continue watching on
    # this machine is the same shelf rather than an empty one
    if api and time.time() - PLACES["at"] > 120:
        PLACES["at"] = time.time()
        try:
            said = ask(one, "/follow/progress?since=%d" % PLACES["since"], 30)
            api.take_progress(as_ours(said.get("progress") or []))
            # the five minutes of overlap are so a place written while this was asked
            # is not missed. Not when there was more than fitted in one answer: then
            # "now" is the last place sent, and going back before it never gets past.
            mark = int(said.get("now") or 0)
            PLACES["since"] = mark if said.get("more") else mark - 300
        except Exception:
            pass
    # and back the other way: an evening watched here belongs in the same book
    if api and time.time() - PLACES["gave"] > 300:
        PLACES["gave"] = time.time()
        try:
            # this machine's own viewers, from the last week: an evening here is
            # the main server's evening, and the main server keeps the book
            mine, mark = api.progress_of(["me"], PLACES["gavesince"] or
                                         int(time.time()) - 86400 * 7,
                                         with_mark=True)
            if mine:
                # sent back under the name the main server files them under, or they
                # would arrive there belonging to a machine nobody watches on
                for row in mine:
                    if HOUSE["owner"]:
                        row["who"] = HOUSE["owner"]
                tell(one, "/follow/watched", {"progress": mine})
                # only as far as was actually sent, or the ones past the end of a
                # full page are stepped over and never travel
                PLACES["gavesince"] = mark
        except Exception:
            pass
    # where the main server is, so a page opened on this machine has a way back to it
    try:
        where_is_the_house(one)
    except Exception:
        pass
    # and whether the main server has moved on to a newer build than this machine
    try:
        check_the_build(one)
    except Exception:
        pass
    # and anything this machine fetched for itself while the other was off, so it
    # does not stay here alone. Named, not sent: the server it follows decides what
    # it wants and comes and takes it over the house network.
    try:
        tell_what_we_fetched(one)
    except Exception as e:
        STATE["why"] = "fetched: %s: %s" % (type(e).__name__, str(e)[:120])
    # and what the people watching keep - their watchlists, their shelves - so this
    # machine knows them when it is the one answering
    try:
        learn_the_viewers(one, ME.get("settings") or "", api)
    except Exception as e:
        # Said out loud rather than swallowed. This was a bare pass, and when it
        # started raising nothing anywhere showed it: the shuffle rounds simply
        # never arrived on this machine, every part of the path read correctly, and
        # the one evening the main server was off a viewer's round was not here.
        import traceback
        STATE["why"] = "viewers: %s: %s | %s" % (
            type(e).__name__, str(e)[:100],
            traceback.format_exc().strip().splitlines()[-2].strip()[:120])
    # and the app itself, so a phone that reaches this machine is offered the same
    # version the main server is running rather than whatever this installer carried
    try:
        got = mirror_app(one)
        if got:
            with LOCK:
                STATE["why"] = ""
    except Exception:
        pass
    say_what_is_here(one, folder, wanted)
    KEEPING["set"] = set(keeping)
    KEEPING["when"] = time.time()
    if str(one.get("clearBy") or "manual") == "auto":
        make_room(folder, int(cap_now(one) * (1000 ** 3)), keeping,
                  str(one.get("deleteBy") or "oldest"))
    with LOCK:
        STATE["kept"] = round(size_of_ours(folder) / 1e9, 1)
        STATE["last"] = int(time.time())
    if lib:
        # what has arrived is only worth having if the library knows about it
        try:
            lib.scan(probe=True, identify=True)
        except Exception:
            pass
        # and it is only worth looking at if it has a name and a picture, which the
        # house knows already
        try:
            dress_the_copies(one, lib, folder, wanted)
            learn_the_facts(one, lib)
        except Exception as e:
            with LOCK:
                STATE["why"] = "could not name what is here: " + str(e)[:120]
        # and filed under the main server's keys, so both machines name a title alike
        try:
            take_the_house_keys(one, lib, api, HOUSE_KEYS["carry"])
        except Exception as e:
            with LOCK:
                STATE["why"] = "could not take the main server's keys: " + str(e)[:120]


def sync_now(config, library=None, api=None, early=False):
    """Ask the main server again and take what is missing, without waiting for the round.

    What is worth keeping changes the moment somebody watches something: a run of
    episodes ahead is a different run once one of them is seen. This is the button
    for that, rather than a minute of wondering whether anything is happening.
    """
    one = settings(config())
    if not (one.get("on") and one.get("master") and one.get("key")):
        return {"ok": False, "said": "Not following anything."}
    # the questions that are only asked now and then are worth asking again too
    APP["at"] = 0.0
    KEYS["last"] = 0.0
    PLACES["at"] = 0.0
    STOP.clear()
    if early:
        start_the_night(one)

    if a_pass_is_running():
        # The list is rebuilt at the start of every pass, and the one running will
        # reach it within the minute. Starting another here is how two files ended up
        # arriving at once, each at half the speed.
        return {"ok": True,
                "said": "A pass is already running - it takes the new list next round."}

    def work():
        try:
            round_of(one, library, api)
            with LOCK:
                STATE["why"] = ""
        except Exception as e:
            with LOCK:
                STATE["why"] = str(e)[:160]

    PASS_NOW["thread"] = threading.Thread(target=work, daemon=True)
    PASS_NOW["thread"].start()
    return {"ok": True, "said": "Asking the other server what is wanted."}


def start(config, library=None, quiet=None, me=None, keys=None, api=None, carry=None):
    """Follow, for as long as this server runs. Safe to call more than once.

    `me` is (port, name): what to tell the main server about this machine.
    """
    global STARTED
    if me:
        ME["port"], ME["name"] = int(me[0]), str(me[1])
        if len(me) > 2:
            ME["static"] = str(me[2])
        if len(me) > 3:
            # what this machine is running, said when it announces itself: the main server
            # shows it beside the name, and two machines on different builds speak
            # slightly different languages to the same app
            ME["build"] = str(me[3])
        if len(me) > 4:
            # where this machine keeps what viewers have set, so what the main server knows
            # about them can be written down here as well
            ME["settings"] = str(me[4])
    if keys:
        KEYS["learn"] = keys
    if carry:
        HOUSE_KEYS["carry"] = carry
    with LOCK:
        if STARTED:
            return
        STARTED = True



    def one_pass(one):
        try:
            round_of(one, library, api)
            with LOCK:
                STATE["why"] = ""
        except Exception as e:
            with LOCK:
                STATE["why"] = str(e)[:160]

    def work():
        while True:
            one = settings(config())
            asleep = bool(quiet and quiet())    # game mode: leave the machine alone
            running = bool(one["on"] and one["master"] and one["key"] and not asleep)
            with LOCK:
                STATE["on"] = running
            # turned off, or the machine is wanted for something else: put the file
            # down where it is. What is written stays, and the next pass asks for the
            # rest of it rather than the whole thing again.
            if running:
                STOP.clear()
            else:
                STOP.set()
            if STATE["on"]:
                if a_pass_is_running():
                    # a copy is running: keep talking to the main server anyway
                    try:
                        light_round(one, library, api)
                    except Exception as e:
                        with LOCK:
                            STATE["why"] = str(e)[:160]
                else:
                    PASS_NOW["thread"] = threading.Thread(
                        target=one_pass, args=(one,), daemon=True)
                    PASS_NOW["thread"].start()
            time.sleep(ASK_EVERY)

    threading.Thread(target=work, daemon=True).start()
