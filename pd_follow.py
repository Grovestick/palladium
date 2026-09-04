#!/usr/bin/env python3
"""Following another Palladium: keeping copies of what the house is watching.

One server is the master - the one with the library on it. Another can follow it: it
asks what is being played, copies those files while the master is on, and serves them
itself when it is not. For a series it copies ahead, so that finishing an episode at
one in the morning does not mean finding the next one gone.

Nothing is pushed. The follower asks, and takes what it is given: the master needs no
knowledge of who is following it beyond a key it can revoke.
"""
import json
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request

#: how often to ask the master what is happening. A film is two hours; a minute is
#: often enough to catch it, and quiet enough to leave a sleeping machine alone.
ASK_EVERY = 60

#: read in lumps, so a stopped copy leaves something to carry on from
LUMP = 4 * 1024 * 1024

#: what to tell the master about this machine, so a viewer whose server is off can
#: be pointed here. Filled in by start().
ME = {"port": 8765, "name": "", "static": "", "build": ""}

#: what to do with the master's invitations, set by start(). A guest whose server is
#: off reaches this one with the link they already hold, or not at all.
KEYS = {"learn": None, "last": 0.0}

#: Where the house had got to, and when we last asked. Copying the films without the
#: places in them means a shelf of things that all start at the beginning.
PLACES = {"since": 0, "at": 0.0, "gave": 0.0}

#: what the house calls its owner. Their places arrive under that name and are filed
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


def look():
    """What the follower is doing, for the page that set it up."""
    with LOCK:
        return dict(STATE)


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
    one.setdefault("coverNight", True)    # enough to last the hours the house sleeps
    one.setdefault("wholeList", False)    # every unwatched episode of a watchlisted
                                          # programme, rather than the night's worth
    one.setdefault("deleteBy", "oldest")  # oldest untouched first, or biggest first
    one.setdefault("listByDay", True)     # watchlists may fill in daylight
    one.setdefault("allowRemote", False)  # the house may change this machine's own
    one.setdefault("nightFrom", 22)       # the hours this server is the one awake
    one.setdefault("nightTo", 8)
    # what to tell the master to hand out to viewers from outside the house. Empty
    # means this network's own address with this machine's port on it, which is
    # right whenever both servers sit behind the one router.
    one.setdefault("outside", "")
    # hours of the shuffle to keep ahead for whoever asked for it, alongside the
    # films and the series. Nought means the shuffle is not copied at all.
    one.setdefault("casualHours", 2)
    # whether this machine replaces itself when the one it follows is newer. Off
    # unless asked for: replacing a server is not something to do behind somebody.
    one.setdefault("updateWith", False)
    return one


def night_length(one):
    """How many hours the house server is asleep for, by its own settings."""
    frm = int(one.get("nightFrom", 22)) % 24
    to = int(one.get("nightTo", 8)) % 24
    return float((to - frm) % 24 or 24)


def hours_wanted(one):
    """How far ahead to keep: enough to last the hours the house is asleep.

    The number in the settings is a floor. What decides it is how long this machine
    is the only one awake - four hours of episodes is no use across a ten-hour night,
    and somebody who starts a series at midnight should not run out at four.
    """
    asked = float(one.get("hours") or 4)
    if not one.get("coverNight", True):
        return asked
    return max(asked, night_length(one))


def in_the_night(one, now=None):
    """Whether this is one of the hours the follower is the server that is awake."""
    hour = (now or time.localtime()).tm_hour
    start, end = int(one.get("nightFrom", 22)), int(one.get("nightTo", 8))
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


def stocking_up(one, sleeps, now=None):
    """Whether to be taking copies of everything half-watched.

    From an hour before the master goes to sleep until the night is over: that is
    when what somebody left half-watched has to be here rather than there.
    """
    at = now or time.localtime()
    if in_the_night(one, at):
        return True
    try:
        hour = int(str(sleeps).split(":")[0])
    except (ValueError, AttributeError, IndexError):
        return False
    return at.tm_hour == (hour - 1) % 24


def ask(one, path, patience=30):
    """One request to the master, with the key it gave us."""
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


def looks_like_film(path):
    """Whether what arrived begins the way a film begins."""
    if path.lower().endswith((".srt", ".ass", ".vtt", ".sub", ".idx")):
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
    """Say something to the master. Same key, a body rather than a question."""
    url = one["master"].rstrip("/") + path
    url += ("&" if "?" in path else "?") + "t=" + urllib.parse.quote(one["key"])
    body = json.dumps(what).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Palladium-App": "follower"})
    with urllib.request.urlopen(req, timeout=patience) as answer:
        return json.loads(answer.read().decode("utf-8", "replace") or "{}")


#: when the app the master carries was last looked at
APP = {"at": 0.0}
APP_EVERY = 1800


def mirror_app(one):
    """Keep the master's app beside this server's own pages.

    A viewer whose server is off reaches this one, and the update banner asks every
    server it knows for a newer app. This machine would offer whatever its installer
    happened to carry - which is older than the master's the moment the master is
    built again. Two small files, half-hourly, and it mirrors the current app.
    """
    where = ME.get("static") or ""
    if not where or not os.path.isdir(where):
        return
    if time.time() - APP["at"] < APP_EVERY:
        return
    APP["at"] = time.time()
    said = ask(one, "/app/version", 20)
    # the same answer says which server the house is running; this machine's own
    # number comes from itself, which is the one place that cannot be wrong
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/app/version" % ME["port"], timeout=15) as answer:
            said["mine"] = (json.loads(answer.read().decode("utf-8", "replace"))
                            .get("serverVersion") or "")
    except Exception:
        said["mine"] = ""
    try:
        follow_the_build(one, said)
    except Exception:
        pass
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
#: somebody who finds this machine first should be able to find the house from here.
HOUSE = {"at": 0.0, "lan": "", "outside": "", "name": ""}
HOUSE_EVERY = 900


def house_doors():
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


def follow_the_build(one, said):
    """Replace this server when the one it follows has moved on.

    The copy is only as good as the server it copies: a machine two months behind
    speaks a different language to the same app. This asks its own server to take the
    update it would have taken from the settings page - the same fetch, from the same
    place, checked against the same hash - and only when the house is on something
    newer than this.
    """
    if not one.get("updateWith"):
        return
    if time.time() - HOUSE["at"] > HOUSE_EVERY:
        HOUSE["at"] = time.time()
        try:
            told = ask(one, "/where", 15)
            with LOCK:
                HOUSE["lan"] = str(told.get("lan") or "")
                HOUSE["outside"] = str(told.get("outside") or "")
                HOUSE["name"] = str(told.get("name") or "")
        except Exception:
            pass                          # an older house, or one that is off
    if time.time() - GROWN["at"] < GROWN_EVERY:
        return
    GROWN["at"] = time.time()
    theirs = str(said.get("serverVersion") or "")
    mine = str(said.get("mine") or "")
    if not theirs or not mine or not newer(theirs, mine):
        return
    req = urllib.request.Request("http://127.0.0.1:%d/update/install" % ME["port"],
                                 data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as answer:
        return json.loads(answer.read().decode("utf-8", "replace") or "{}")


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
                       answer.get("whose") or "the house", room)}


def try_follower(where, patience=10):
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


def note_trouble(name, why, size=0):
    """Write down a file that would not come. Called with the lock held."""
    text = str(why)[:160]
    for row in TROUBLE:
        if row["name"] == name and row["why"] == text:
            row["times"] += 1
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


def make_room(folder, cap_bytes, keeping, how="oldest"):
    """Delete until the folder is inside its cap, in the order asked for.

    Nothing on the list is deleted, whatever its age: the point of the copy is that
    it is there when the master is not. What goes is chosen from the rest, either the
    one nobody has touched for longest - which is the honest default, since it is the
    one nobody will miss - or the largest, which empties the disk in fewer deletions
    and is what somebody wants when one film is in the way of ten episodes.
    """
    files = []
    for here, dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(here, name)
            if path in keeping:
                continue
            try:
                files.append((os.path.getatime(path), os.path.getsize(path), path))
            except OSError:
                pass
    if how == "largest":
        files.sort(key=lambda f: -f[1])
    else:
        files.sort()
    total = size_of(folder)
    for when, size, path in files:
        if total <= cap_bytes:
            return
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


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
    if not looks_like_film(part):
        os.remove(part)
        raise ValueError("what arrived is not a film")
    # and it is the film that was offered, not another one of the same length
    offered = str(item.get("mark") or "")
    if offered and quick_mark(part) != offered:
        os.remove(part)
        raise ValueError("what arrived is not the file that was offered")
    os.replace(part, into)
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
    """The house's owner, read as whoever owns this machine.

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
ALLOWED = {"gb": 0.0}


def cap_now(one):
    """The smaller of what this machine was set to and what the other one allows."""
    mine = max(1.0, float(one.get("cap") or 200))
    theirs = float(ALLOWED.get("gb") or 0)
    return min(mine, theirs) if theirs > 0 else mine


def learn_the_facts(one, lib):
    """Ask the house what is inside the files here that nobody has measured.

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


def dress_the_copies(one, lib, folder, wanted):
    """Give what has arrived the catalogue's own name and pictures.

    This machine can hold a film without being able to say what it is: it reaches the
    house over the network and TMDB over the internet, and the second is not always
    there. The house already knows - it is the machine the film came from - so the
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
            # what the house measured, written down here as measured rather than as
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
    """Which number the master files the picture under.

    The master answers /local/art/<key>/poster for a film by its own number and for
    an episode by its programme's - which is the same answer it gives its own pages.
    """
    return str((item.get("art") or {}).get("owner") or item.get("key") or "")


def name_the_strangers(one, folder, known):
    """Ask the master what the files it never listed are called.

    Copying began before this machine started writing down what each file is, so a
    disk full of films answered to nothing: they were here, they played, and the
    house was never told it had them. The master knows every one of them by name.
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
    """Tell the master everything on this disk, by the numbers it files them under.

    Not only what is on today's list. A film copied last week for somebody who has
    since finished it is still here and still plays - for anyone, since what is on
    this machine is on it for the house - and the house should know that. So the
    names it has been given are remembered, and everything still on the disk is
    reported whether it was asked for this time or not.

    Said before the copying starts as well as after it: a pass that fetches two
    seven-gigabyte films takes an hour, and until it ended the house was told nothing.
    """
    known = name_the_strangers(one, folder, _remember_keys(folder, wanted))
    _keep_book(folder, known)
    holding = []
    for name, key in known.items():
        here = os.path.join(folder, name)
        if os.path.exists(here) and os.path.getsize(here) > 0:
            holding.append(key)
    try:
        said = tell(one, "/follow/holding", {"keys": holding}) or {}
        if said.get("cap") is not None:
            ALLOWED["gb"] = max(0.0, float(said.get("cap") or 0))
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
        pass                          # the copy still works without the book


def light_round(one, lib=None, api=None):
    """The quick half, for while a long copy is in flight.

    Fetching two seven-gigabyte films takes an hour, and nothing else used to happen
    in that hour: the master heard nothing about this machine, the places did not
    travel, and the posters wore no dot for films that were already here. None of
    that needs to wait for a disk.
    """
    folder = one.get("folder") or ""
    if not folder:
        return
    try:
        # and how full it is, which is the one thing about this machine the house
        # cannot work out for itself
        room = kept_here(one)
        ask(one, "/follow/here?port=%d&name=%s&outside=%s&build=%s"
                 "&gb=%.1f&free=%.1f&cap=%.1f&files=%d"
                 % (int(ME["port"]), urllib.parse.quote(ME["name"][:40]),
                    urllib.parse.quote((one.get("outside") or "").strip()),
                    urllib.parse.quote(ME.get("build") or ""),
                    room.get("gb") or 0.0, room.get("free") or 0.0,
                    room.get("cap") or 0.0, int(room.get("files") or 0)), 15)
    except Exception:
        pass
    if api and time.time() - PLACES["at"] > 120:
        PLACES["at"] = time.time()
        try:
            said = ask(one, "/follow/progress?since=%d" % PLACES["since"], 30)
            api.take_progress(as_ours(said.get("progress") or []))
            PLACES["since"] = int(said.get("now") or 0) - 300
        except Exception:
            pass
    try:
        forward = "hours=%s&eps=%s&casual=%s&whole=%d" % (
            hours_wanted(one), one.get("episodes") or 6,
            one.get("casualHours") or 0, 1 if one.get("wholeList") else 0)
        said = ask(one, "/follow/playing?" + forward, 30)
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
    # say where this machine can be reached, so the master can hand the address to
    # its viewers: when it is off, they have somewhere to go
    try:
        # and how full it is, which is the one thing about this machine the house
        # cannot work out for itself
        room = kept_here(one)
        ask(one, "/follow/here?port=%d&name=%s&outside=%s&build=%s"
                 "&gb=%.1f&free=%.1f&cap=%.1f&files=%d"
                 % (int(ME["port"]), urllib.parse.quote(ME["name"][:40]),
                    urllib.parse.quote((one.get("outside") or "").strip()),
                    urllib.parse.quote(ME.get("build") or ""),
                    room.get("gb") or 0.0, room.get("free") or 0.0,
                    room.get("cap") or 0.0, int(room.get("files") or 0)), 15)
    except Exception:
        pass                               # an older master has no such door
    # and take the house's invitations, so the links guests already hold work here
    if KEYS["learn"] and time.time() - KEYS["last"] > 900:
        KEYS["last"] = time.time()
        try:
            said = ask(one, "/follow/invites", 20)
            told = dict(said.get("owner") or {})
            told["ownerIs"] = said.get("ownerIs") or ""
            told["ownerName"] = said.get("ownerName") or ""
            HOUSE["owner"] = told["ownerIs"]
            KEYS["learn"](said.get("invites") or [], told,
                          said.get("name") or one.get("master") or "")
            # the house's catalogue key, so copies arrive as films with posters
            # rather than as file names
            if lib and said.get("tmdb"):
                cfg = lib.config()
                if not (cfg.get("tmdb_key") or "").strip():
                    cfg["tmdb_key"] = said["tmdb"]
                    cfg.setdefault("language", said.get("language") or "en-US")
                    lib.save_config(cfg)
        except Exception:
            pass
    forward = "hours=%s&eps=%s&casual=%s&whole=%d" % (
        hours_wanted(one), one.get("episodes") or 6,
        one.get("casualHours") or 0, 1 if one.get("wholeList") else 0)
    said = ask(one, "/follow/playing?" + forward)
    # an hour before the other machine sleeps, and through the night, take copies of
    # everything anybody is in the middle of - not only what is on at this moment
    stock = stocking_up(one, said.get("sleeps"))
    if stock:
        said = ask(one, "/follow/playing?deck=1&" + forward, 60)
    with LOCK:
        STATE["stocking"] = stock
        # a key of one's own on somebody else's server keeps that person's films and
        # nobody else's; a key marked as a following server keeps the house's
        STATE["whose"] = said.get("whose") or ""
    wanted = said.get("wanted") or []
    say_what_is_here(one, folder, wanted)
    # The master lists what is on a screen now first. A pass that is half way through
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
    # one on screen has to be here before the house sleeps. By day the house is
    # answering for itself, and chasing every episode somebody starts spends the link
    # on a copy nobody is going to need for hours. The rest of the list - watchlists,
    # what people are part-way through - goes on quietly either way.
    if not stock:
        wanted = [w for w in wanted if not w.get("hot")]
        # and a watchlist can be told to wait for the night as well: a house on a
        # thin line would rather nothing at all moved while people are up.
        if not one.get("listByDay", True):
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

    taken_now = 0
    for item in wanted:
        if taken_now >= 40:
            break
        if not item.get("part"):
            continue
        safe = a_safe_name(item.get("name"))
        if not safe:
            with LOCK:
                STATE["why"] = "refused a file named %.40s" % (item.get("name") or "")
            continue
        here = os.path.join(folder, safe)
        keeping.add(here)
        if (os.path.exists(here) and os.path.getsize(here) == item.get("size")
                and (not item.get("mark") or quick_mark(here) == item["mark"])):
            continue
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
                             item.get("size") or 0)
            continue
    # where the house had got to in what it is watching, so Continue watching on
    # this machine is the same shelf rather than an empty one
    if api and time.time() - PLACES["at"] > 120:
        PLACES["at"] = time.time()
        try:
            said = ask(one, "/follow/progress?since=%d" % PLACES["since"], 30)
            api.take_progress(as_ours(said.get("progress") or []))
            PLACES["since"] = int(said.get("now") or 0) - 300
        except Exception:
            pass
    # and back the other way: an evening watched here belongs in the same book
    if api and time.time() - PLACES["gave"] > 300:
        PLACES["gave"] = time.time()
        try:
            # this machine's own viewers, from the last week: an evening here is
            # the house's evening, and the master keeps the book
            mine = api.progress_of(["me"], int(time.time()) - 86400 * 7)
            if mine:
                # sent back under the name the house files them under, or they
                # would arrive there belonging to a machine nobody watches on
                for row in mine:
                    if HOUSE["owner"]:
                        row["who"] = HOUSE["owner"]
                tell(one, "/follow/watched", {"progress": mine})
        except Exception:
            pass
    # and the app itself, so a phone that reaches this machine is offered the same
    # version the house is running rather than whatever this installer carried
    try:
        got = mirror_app(one)
        if got:
            with LOCK:
                STATE["why"] = ""
    except Exception:
        pass
    say_what_is_here(one, folder, wanted)
    make_room(folder, int(cap_now(one) * (1000 ** 3)), keeping,
              str(one.get("deleteBy") or "oldest"))
    with LOCK:
        STATE["kept"] = round(size_of(folder) / 1e9, 1)
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


def sync_now(config, library=None, api=None):
    """Ask the master again and take what is missing, without waiting for the round.

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

    def work():
        try:
            round_of(one, library, api)
            with LOCK:
                STATE["why"] = ""
        except Exception as e:
            with LOCK:
                STATE["why"] = str(e)[:160]

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "said": "Asking the other server what is wanted."}


def start(config, library=None, quiet=None, me=None, keys=None, api=None):
    """Follow, for as long as this server runs. Safe to call more than once.

    `me` is (port, name): what to tell the master about this machine.
    """
    global STARTED
    if me:
        ME["port"], ME["name"] = int(me[0]), str(me[1])
        if len(me) > 2:
            ME["static"] = str(me[2])
        if len(me) > 3:
            # what this machine is running, said when it announces itself: the house
            # shows it beside the name, and two machines on different builds speak
            # slightly different languages to the same app
            ME["build"] = str(me[3])
    if keys:
        KEYS["learn"] = keys
    with LOCK:
        if STARTED:
            return
        STARTED = True

    pass_now = {"thread": None}

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
                busy = pass_now["thread"] is not None and pass_now["thread"].is_alive()
                if busy:
                    # a copy is running: keep talking to the master anyway
                    try:
                        light_round(one, library, api)
                    except Exception as e:
                        with LOCK:
                            STATE["why"] = str(e)[:160]
                else:
                    pass_now["thread"] = threading.Thread(
                        target=one_pass, args=(one,), daemon=True)
                    pass_now["thread"].start()
            time.sleep(ASK_EVERY)

    threading.Thread(target=work, daemon=True).start()
