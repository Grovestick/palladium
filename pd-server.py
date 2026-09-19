#!/usr/bin/env python3
"""Palladium - a media server for the folders you already have.

Reads films and series off disk, indexes them, fetches artwork and subtitles, and
serves the lot to a browser, a phone and a television. Serving the client from
http://localhost:8765 also gives the Google Cast SDK the secure origin it insists on.

    python pd-server.py              # serve, and open a browser
    python pd-server.py --no-open    # serve only
    python pd-server.py --port 9000 --root PATH
"""
import hashlib
import hmac
import math
from pd_library import is_episode, is_title, move_key
import http.server
import shutil
import json
import os
import re
import subprocess
import time
import socket
import socketserver
import urllib.parse
# at the top, not inside a handler: importing it in one branch of a handler makes
# the name local to the whole of that handler, and every other branch that used
# urllib.parse then failed on a name it could see perfectly well from here
import urllib.request
import sys
import threading
import webbrowser

from pd_invites import Invites
from pd_watching import WATCHING

def _flag(name, fallback=""):
    """One value from the command line: --name value, or --name=value.

    Two flags, both for running a second copy that disturbs nothing: --port to keep off
    the one already serving, and --root to put its settings, library and logs somewhere
    of their own. That is what the smoke test starts, and what a fresh install looks
    like from the inside.
    """
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return fallback


#: Where the program is, and where its papers are.
#:
#: Run from a folder of source, the two are the same and always have been. Installed,
#: the program lives somewhere read-only and everything it writes - the settings, the
#: library, the invitations, the logs, the artwork cache - belongs in this account's own
#: application data, where an installer may not tread and an uninstaller must not sweep.
CODE = os.path.dirname(os.path.abspath(__file__))
PACKAGED = bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))
ROOT = _flag("--root") or (os.path.join(os.environ.get("APPDATA") or CODE, "Palladium")
                           if PACKAGED else CODE)
if PACKAGED or _flag("--root"):
    os.makedirs(ROOT, exist_ok=True)
STATIC = os.path.join(CODE, "static")


def _port_wanted():
    """The port to serve on: the flag, the environment, the settings file, or 8765.

    Two Palladiums in one house need two ports, because one router forwards to both:
    the one with the library on 8765, the one keeping copies on 8764. The port is
    written in the settings so the second machine does not need a shortcut with a
    flag in it.
    """
    said = _flag("--port") or os.environ.get("PALLADIUM_PORT") or ""
    if not said:
        try:
            with open(os.path.join(ROOT, "settings.json"), encoding="utf-8") as f:
                said = (json.load(f) or {}).get("port") or ""
        except Exception:
            said = ""
    try:
        port = int(said)
    except (TypeError, ValueError):
        return 8765
    return port if 1 <= port <= 65535 else 8765


PORT = _port_wanted()


ENGINE = None            # created on first use: probing NVENC takes a moment
LOCAL = None             # our own library and the endpoints it answers
INVITES = Invites(ROOT)  # the people who may watch from outside the main server
WAN = {"ip": "", "at": 0}
#: a backlog of faults being handed to palladium.video: how many are left, how many
#: went, and why the last one would not
SENDING = {"busy": False, "left": 0, "sent": 0, "failed": 0, "why": ""}
#: A line for the screens in the house: what it says, which one it is, and when it
#: stops being said. Not written down - a notice nobody was in the room for is not
#: worth keeping until tomorrow.
#:
#: This is the channel a watch party will speak over: one line to every screen is the
#: hard half, and what that needs on top of it is a sender's name, a message per
#: viewer rather than one for the main server, and a guest allowed to send.
NOTICE = {"id": 0, "text": "", "until": 0, "to": "", "play": "", "at": 0}
#: Raised the moment a notice is written, so the screens waiting on one are answered
#: at once rather than on their next visit. Cleared straight after: the flag is the
#: knock on the door, and the id is what says whether it was already heard.
NOTICE_RUNG = threading.Event()

#: The evening's conversation: everybody in the house and every guest, one room. Held
#: in memory and capped, because this is for "start it" and "who is that" rather than
#: for keeping - and because a chat written to disk is a chat somebody has to be able
#: to delete.
CHAT = []
CHAT_KEEP = 200
CHAT_RUNG = threading.Event()

#: The watch party: whether one is on, when it started, and who is in it.
#:
#: Chat belongs to a party rather than to the server. A box that is always there is a
#: box somebody types into on a Tuesday afternoon and nobody reads; and a line meant
#: for the four people watching together should not reach a guest three countries away
#: who happens to be part-way through something else.
#:
#: Somebody is in it by reading it: a watch party is who is watching along, and asking
#: them to press Join as well would be asking twice.
PARTY = {"on": False, "since": 0, "who": {}, "title": "", "key": "",
         # who has been asked, and by whom: an invitation waits until it is answered
         # or the party ends
         "asked": {}}


def wan_ip():
    """Our address as the rest of the internet sees it, for building invite links.

    Asked of an outside service because a home router hides it from us, and cached:
    it changes rarely and the settings page must not stall on it.
    """
    if WAN["ip"] and time.time() - WAN["at"] < 3600:
        return WAN["ip"]
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=4) as r:
            ip = r.read().decode().strip()
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
            WAN.update(ip=ip, at=time.time())
    except Exception:
        pass
    return WAN["ip"]


def local():
    global LOCAL
    if LOCAL is None:
        import pd_library as library
        import pd_localapi as localapi
        # where this server keeps its papers, which is not where its code sits
        localapi.use_data_dir(ROOT)
        import pd_streaming
        pd_streaming.use_data_dir(ROOT)
        made = library.Library(ROOT)
        LOCAL = localapi.LocalAPI(made)
        # a conversion to title keys that failed leaves the library on its old keys,
        # working but unable to agree with the other machine: said out loud
        if getattr(made, "keys_failed", ""):
            said = "converting the library to title keys failed" + chr(10) + made.keys_failed
            try:
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write(time.strftime("%H:%M:%S") + " fault " + said + chr(10))
            except Exception:
                pass
            try:
                Handler.note_fault(None, said)      # onto the reports page as well
            except Exception:
                pass
        # the library has just turned its row numbers into keys; everything kept
        # beside it names titles by those numbers and has to come too
        moved = getattr(made, "keys_moved", None)
        if not moved:
            # left behind by a start that converted the library and then stopped
            # before it could carry the rest: the tables will not be converted twice,
            # so this is the only trace of what moved where
            try:
                with open(os.path.join(ROOT, "keys-moved.json"), encoding="utf-8") as f:
                    moved = json.load(f)
            except Exception:
                moved = None
        if moved:
            carry_the_keys(moved)
    return LOCAL


def carry_the_keys(moved):
    """Rewrite every key kept outside the library, on the library's own list.

    The shelves, the two marks, each shuffle's round and what was learned about a
    subtitle are all filed under a title's key. They live in the settings file because
    they belong to a person rather than to the library - which is exactly why they do
    not move when it does.
    """
    try:
        stored = read_settings() or {}
    except Exception:
        return

    def one(k):
        return move_key(moved, k)

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
              "subSince")

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
        for f in ("watchlist", "favorites", "casual"):
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
            for f in ("queue", "played"):
                if f in round_now:
                    round_now[f] = listed(round_now[f])
            if isinstance(round_now.get("at"), dict):
                round_now["at"] = keyed(round_now["at"])
    try:
        write_settings(stored)
    except Exception:
        return                       # left for the next start to finish
    # and the list of what the cache is holding, which is in this server's keys
    try:
        Handler.remember_copies(sorted({one(k)
                                        for k in (Handler.COPIES.get("keys") or [])}))
    except Exception:
        pass
    # done: the list is only needed until everything beside the library has followed
    try:
        os.remove(os.path.join(ROOT, "keys-moved.json"))
    except OSError:
        pass


def lan_ip():
    """The address the Chromecast should fetch from - the one that routes off-box."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # nothing is sent: connecting a UDP socket only makes the kernel pick the
        # interface it would route out of, and that interface's address is the one
        # a television on this network can reach
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


LAN_IP = lan_ip()


def reachable_from_outside(port, timeout=14):
    """Ask machines elsewhere to open a connection to us. Returns (ok, nodes, note).

    check-host.net runs the attempt from several countries and reports each one. It
    refuses requests that do not look like a browser, hence the user agent.
    """

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

    def api(url):
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": ua})
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read())

    ip = wan_ip()
    if not ip:
        return False, [], "Could not work out this network's address from outside."
    try:
        started = api("https://check-host.net/check-tcp?host=%s:%d&max_nodes=4"
                      % (ip, port))
        deadline = time.time() + timeout
        result = {}
        while time.time() < deadline:
            time.sleep(4)
            result = api("https://check-host.net/check-result/" + started["request_id"])
            if result and all(v is not None for v in result.values()):
                break
    except Exception as e:
        return False, [], "The checking service did not answer (%s)." % str(e)[:60]

    nodes, good = [], 0
    for name, out in sorted((result or {}).items()):
        where = name.split(".")[0]
        entry = (out or [{}])[0] if isinstance(out, list) else {}
        ok = isinstance(entry, dict) and "error" not in entry and entry
        if ok:
            good += 1
        nodes.append({"where": where, "ok": bool(ok),
                      "detail": ("%.0f ms" % (float(entry.get("time", 0)) * 1000))
                                if ok else (entry.get("error", "no answer")
                                            if isinstance(entry, dict) else "no answer")})
    if good:
        note = ("Reachable from %d of %d places. Invitation links work."
                % (good, len(nodes)))
    else:
        note = ("Nothing outside can open a connection to %s:%d. With a forwarding rule "
                "in the router, that means the ISP is keeping this connection behind "
                "its own NAT - only a public address from them can fix it." % (ip, port))
    return bool(good), nodes, note


def gateway():
    """The router's address, for telling somebody where to go and change a setting."""
    try:
        out = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True,
                             timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             ).stdout.decode("utf-8", "replace")
        m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else "your router"
    except Exception:
        return "your router"


def engine():
    """The transcoder. Works with no config.json at all - which is what a new install
    has, and what every page that mentions the engine used to fall over."""
    global ENGINE
    if ENGINE is None:
        import pd_gpu as gpu
        try:
            with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
                cfg = json.loads(f.read())
        except (OSError, ValueError):
            cfg = {}
        ENGINE = gpu.Engine(os.path.join(os.environ.get("TEMP", ROOT),
                                         "palladium-gpu"))
    return ENGINE


# OpenSubtitles purges a consumer that downloads nothing for sixty days. Forty leaves
# twenty days of margin - enough for a holiday, or for a server that was switched off.
KEEPALIVE_DAYS = 40


#: Where a fetched ffmpeg goes, and what to fetch. Static builds, no installer, and
#: the only two the wizard offers - anybody on anything else has ffmpeg already.
FFMPEG_HOME = os.path.join(ROOT, "ffmpeg")
FFMPEG_FROM = {
    "nt": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "posix": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
}

#: What the first run is waiting for, kept here so the page and the server agree.
FETCHING = {"busy": False, "said": "", "done": False}

#: Which subtitle each title is being watched with, and the position it was chosen
#: at: {key: (sub, seconds)}. Written through to settings.json, because a restart in
#: the middle of an episode used to look exactly like a subtitle chosen at the end -
#: and the episode then refused to verify however far it was watched.
SUB_SINCE = {}

#: The colours on offer for the accent, and the one everything starts with. Every one
#: of them carries dark text at 5.7 to 1 or better and reads as text on the dark ground
#: at 5.8 or better, so nothing here can be chosen into illegibility.
ACCENTS = [
    ("#4a90f0", "Blue"),      ("#38b6e8", "Sky"),      ("#2fbfa8", "Teal"),
    ("#5cc15c", "Green"),     ("#9ccb3b", "Lime"),     ("#f0b429", "Amber"),
    ("#f0813c", "Orange"),    ("#f05a56", "Red"),      ("#f072b0", "Pink"),
    ("#a98bf5", "Violet"),    ("#9fb3c8", "Slate"),
    # kept only so nobody wonders where it went
    ("#e5a00d", "Gold, don't use this"),
]
ACCENT = "#4a90f0"

#: Wrong passwords lately, by address: [how many, when the last one was]. In memory
#: only - a restart forgives, which is the right amount of forgiveness for something
#: whose real defence is that the password is long.
GUESSES = {}


def engine_name():
    """What the transcoder is, in the words the pages use."""
    try:
        from pd_gpu import ENGINE_NAME
        return ENGINE_NAME
    except Exception:
        return ""


def ffmpeg_now():
    """The ffmpeg in use, asking pd_gpu rather than guessing."""
    try:
        from pd_gpu import FFMPEG
        return FFMPEG or ""
    except Exception:
        return ""


def tools_on():
    """Whether this server runs the subtitle tools it has. Let go of, it does not."""
    return not (read_settings() or {}).get("toolsOff")


def ffmpeg_in_home():
    """The ffmpeg this server has already fetched, in use or not."""
    want = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for where, _, files in os.walk(FFMPEG_HOME):
        if want in files:
            return os.path.join(where, want)
    return ""


def use_ffmpeg(found):
    """Start or stop using the cache that is here. The files are not touched."""
    import pd_gpu
    stored = read_settings() or {}
    if found:
        os.environ["PALLADIUM_FFMPEG"] = found
        stored["ffmpeg"] = found
    else:
        os.environ.pop("PALLADIUM_FFMPEG", None)
        stored.pop("ffmpeg", None)
    write_settings(stored)
    pd_gpu.rescan()
    return pd_gpu.FFMPEG or ""


def fetch_ffmpeg():
    """Download a static ffmpeg into the papers folder and use it.

    About 80 MB, over a minute on a home line. Nothing else in the program does this:
    it happens once, because somebody pressed a button that said it would.
    """
    import tarfile
    import zipfile
    url = FFMPEG_FROM.get(os.name)
    if not url:
        FETCHING.update(busy=False, said="No build for this system - install ffmpeg "
                                         "yourself and it will be found.")
        return
    FETCHING.update(busy=True, said="Fetching ffmpeg\u2026", done=False,
                    # where it comes from and how far along, so a slow line looks
                    # like a slow line rather than like nothing happening
                    what="ffmpeg", where=url, got=0, size=0, part=0.0)
    try:
        os.makedirs(FFMPEG_HOME, exist_ok=True)
        into = os.path.join(FFMPEG_HOME, "download" + (".zip" if os.name == "nt"
                                                       else ".tar.xz"))
        with urllib.request.urlopen(url, timeout=120) as r, open(into, "wb") as f:
            size = int(r.headers.get("Content-Length") or 0)
            FETCHING["size"] = size
            got = 0
            while True:
                lump = r.read(262144)
                if not lump:
                    break
                f.write(lump)
                got += len(lump)
                FETCHING["got"] = got
                FETCHING["part"] = round(got / size, 3) if size else 0.0
                FETCHING["said"] = ("Fetching ffmpeg - %d%% of %d MB"
                                    % (round(100 * got / size), round(size / 1e6))
                                    if size else
                                    "Fetching ffmpeg - %d MB so far" % round(got / 1e6))
        FETCHING["said"] = "Unpacking\u2026"
        if into.endswith(".zip"):
            with zipfile.ZipFile(into) as z:
                z.extractall(FFMPEG_HOME)
        else:
            with tarfile.open(into) as t:
                t.extractall(FFMPEG_HOME)
        os.remove(into)
        # the archives put the program a few folders down, under a name that carries
        # the version: find it rather than hoping
        want = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        found = ""
        for where, _, files in os.walk(FFMPEG_HOME):
            if want in files:
                found = os.path.join(where, want)
                break
        if not found:
            FETCHING.update(busy=False, said="The download had no ffmpeg in it.")
            return
        os.environ["PALLADIUM_FFMPEG"] = found
        import pd_gpu
        pd_gpu.rescan()
        stored = read_settings() or {}
        stored["ffmpeg"] = found
        write_settings(stored)
        FETCHING.update(busy=False, done=True,
                        said="ffmpeg is ready: " + pd_gpu.ENGINE_NAME)
    except Exception as e:
        FETCHING.update(busy=False, said="Could not fetch ffmpeg: %s" % str(e)[:160])


def sub_cache_path(video, index):
    """Where the text of one track inside one film is kept.

    Named for the film, its size and when it was last written, so a re-encode or a
    replacement of the file is a different question with a different answer.
    """
    import hashlib
    try:
        stamp = "%d|%d" % (os.path.getsize(video), int(os.path.getmtime(video)))
    except OSError:
        stamp = "0"
    mark = hashlib.sha1(("%s|%s|%s" % (video, stamp, index)).encode("utf-8",
                                                                   "replace"))
    return os.path.join(ROOT, "subcache", mark.hexdigest() + ".vtt")


#: How much of a track is lifted out first, while the whole of it is still being read.
#: A container has no index for subtitles, so the whole file has to be read to collect
#: them - minutes, on a large one. Asking for twenty minutes' worth stops ffmpeg once it
#: is past that timestamp, which takes about as long as reading twenty minutes of film,
#: and that is enough to be watching while the rest arrives.
SUB_HEAD = 20 * 60

#: One extraction per track at a time. The player, a seek and a measurement all ask
#: for the same subtitle within a second of each other, and three ffmpegs reading the
#: same eight-gigabyte file take three times as long as one.
PULLING = {}
PULLING_LOCK = threading.Lock()

#: And one extraction at a time across the whole server. Each reads a container end to
#: end; five at once on the same disk made every one of them five times slower, with a
#: viewer waiting on one of the five.
PULL_ONE = threading.Semaphore(1)


def cached_subtitle(where):
    try:
        if os.path.getsize(where) > 40:
            with open(where, encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return None


def head_subtitle(video, index, seconds=SUB_HEAD, patience=180):
    """The first stretch of a track, quickly, for somebody who has pressed play.

    Kept beside the whole one under its own name so a later request can tell them
    apart: this one stops at a timestamp and the film goes on past it.
    """
    where = sub_cache_path(video, index) + ".head"
    said = cached_subtitle(where)
    if said is not None:
        return said
    from pd_gpu import FFMPEG
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", video, "-map", "0:%d" % int(index), "-vn", "-an",
           # an output limit, so ffmpeg stops reading once it is past the mark
           "-t", str(int(seconds)), "-f", "webvtt", "pipe:1"]
    proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    try:
        out, _ = proc.communicate(timeout=patience)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None
    said = whole_stamps((out or b"").decode("utf-8", "replace"))
    if said.count("-->") < 1:
        return None
    try:
        os.makedirs(os.path.dirname(where), exist_ok=True)
        with open(where, "w", encoding="utf-8") as f:
            f.write(said)
    except OSError:
        pass
    return said


def pulling_now(video, index):
    """Whether somebody is already reading the whole of this track."""
    where = sub_cache_path(video, index)
    with PULLING_LOCK:
        lock = PULLING.get(where)
    return bool(lock and lock.locked())


def start_pull(video, index):
    """Read the whole track in the background, for whoever asks next."""
    if pulling_now(video, index) or cached_subtitle(sub_cache_path(video, index)):
        return
    threading.Thread(target=lambda: _quietly(pull_subtitle, video, index),
                     daemon=True).start()


def _quietly(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


def pull_subtitle(video, index, patience=900):
    """The whole of one embedded track as WebVTT, from the cache or from ffmpeg.

    The whole of it, not the part after some offset: one copy then answers for every
    seek, and cutting it to a live encode is arithmetic rather than another five
    minutes of reading an eight-gigabyte file. Fifteen minutes of patience because
    that read competes with the film being streamed off the same disk; it only ever
    happens once, and nothing else is waiting on the process that does it.

    Whoever asks first does the reading; anybody who asks while that is happening
    waits for it and takes the same answer.
    """
    where = sub_cache_path(video, index)
    said = cached_subtitle(where)
    if said is not None:
        return said
    with PULLING_LOCK:
        mine = PULLING.get(where)
        if mine is None:
            mine = PULLING[where] = threading.Lock()
    with mine:
        said = cached_subtitle(where)      # somebody may have just finished it
        if said is not None:
            return said
        with PULL_ONE:
            said = cached_subtitle(where)
            if said is None:
                said = _pull_subtitle(video, index, where, patience)
    with PULLING_LOCK:
        PULLING.pop(where, None)
    return said


def _pull_subtitle(video, index, where, patience):
    from pd_gpu import FFMPEG
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", video, "-map", "0:%d" % int(index), "-vn", "-an",
           "-f", "webvtt", "pipe:1"]
    proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=0)
    try:
        out, _ = proc.communicate(timeout=patience)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    said = whole_stamps((out or b"").decode("utf-8", "replace"))
    if said.count("-->") >= 1:
        try:
            os.makedirs(os.path.dirname(where), exist_ok=True)
            with open(where, "w", encoding="utf-8") as f:
                f.write(said)
        except OSError:
            pass                      # a cache that cannot be written is not a fault
    return said


def settings_path():
    return os.path.join(ROOT, "settings.json")


def read_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.loads(f.read())
    except Exception:
        return {}


#: One change at a time. Settings are read whole, altered and written whole, so two
#: at once lose one of them - and a reader arriving mid-write used to see a broken file
#: and conclude there were no settings at all.
SETTINGS_LOCK = threading.Lock()
SETTINGS_LAST = {"good": None}

#: What 100% means, on every screen: the text is drawn this much of the height of the
#: surface it sits on, whatever the resolution of the film or of the screen. Each
#: client draws its own text, so the number appears in three places and must be the
#: same in all of them - the browser in vh (static/app.js), the app as a fraction of
#: the subtitle view (PlayerActivity.kt), and ffmpeg scaling a bitmap subtitle to
#: match (pd_gpu.py, which starts from the ~4.5% such a subtitle is authored at).
#: 5% is where both standards sit: WebVTT's own default, and a hair under the 5.33%
#: of CEA-608 captioning.
SUB_BASE = 0.05

#: What each screen used to mean by it. The browser drew at 2.7% of the window and
#: the app took Media3's default of 5.33%, so one setting came out nearly twice the
#: size on a television as in a browser, and a burned-in subtitle was matched to the
#: browser. Sizes already chosen are rescaled once, so nothing changes on screen and
#: the number underneath finally means what it says.
SUB_BASE_WAS = {"web": 0.027, "tv": 0.0533, "phone": 0.0533}


def read_settings():
    """The settings file, or the last version of it that could be read.

    Never an empty dictionary because of a failure: that is how a file gets emptied.
    A caller that gets None must not write.
    """
    path = settings_path()
    for attempt in range(4):
        try:
            with open(path, encoding="utf-8") as f:
                stored = json.loads(f.read())
            if isinstance(stored, dict):
                SETTINGS_LAST["good"] = stored
                return stored
        except FileNotFoundError:
            return {}
        except Exception:
            time.sleep(0.05)          # a write in progress; it will be over shortly
    # unreadable four times running: the cache in hand is better than nothing, and
    # nothing is better than overwriting what could not be read
    return SETTINGS_LAST["good"]


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
            now = move_key(moved, o)
            if now and now != o:
                count[0] += 1
                return now
        return o

    stored = read_settings() or {}
    fixed = walk(stored)
    if count[0]:
        write_settings(fixed, merge=False)
        with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s carried %d written-down key(s) onto titles that moved%s"
                    % (time.strftime("%H:%M:%S"), count[0], chr(10)))
    return count[0]


def write_settings(stored, merge=True):
    """Write the settings whole, or not at all.

    `merge` keeps whatever another writer added since this caller read the file. A
    caller that has *removed* a key must say merge=False, or the merge puts it back:
    that is how clearing a personal colour left the colour exactly where it was.
    """
    if not isinstance(stored, dict) or not stored:
        return                        # never write an empty file over a full one
    path = settings_path()
    with SETTINGS_LOCK:
        try:
            if os.path.exists(path):
                shutil.copyfile(path, path + ".bak")
        except OSError:
            pass                      # a missing spare copy is not worth failing over
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
                wrote = True
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        SETTINGS_LAST["good"] = stored


def note_download():
    stored = read_settings()
    stored["subsLastDownload"] = int(time.time())
    write_settings(stored)


def subtitle_without_one():
    """A film in the library with no subtitles at all: the best use of a keep-alive."""
    import pd_subs
    con = local().lib.db()
    try:
        rows = con.execute(
            """SELECT f.path, i.id, i.title, i.year FROM file f JOIN item i ON i.id=f.item_id
               WHERE i.type='movie' AND f.episode_id IS NULL AND i.title IS NOT NULL
               ORDER BY i.added DESC LIMIT 200""").fetchall()
    finally:
        con.close()
    for r in rows:
        try:
            embedded = json.loads(
                local().lib.db().execute("SELECT streams FROM file WHERE path=?",
                                         (r["path"],)).fetchone()["streams"] or "[]")
        except Exception:
            embedded = []
        text_tracks = [x for x in embedded
                       if (x.get("codec") or "") in ("subrip", "ass", "mov_text")]
        import pd_localapi
        if text_tracks or pd_localapi.sidecars(r["path"]):
            continue
        return r
    return None


def subtitle_keepalive(language="en"):
    """Download one subtitle if it has been too long since the last one."""
    cfg = local().lib.config()
    if not (cfg.get("opensubtitles_key") or "").strip():
        return "no API key"
    stored = read_settings()
    last = stored.get("subsLastDownload", 0)
    days = (time.time() - last) / 86400.0 if last else 999
    if days < KEEPALIVE_DAYS:
        return "last download %.0f days ago - nothing to do" % days

    pick = subtitle_without_one()
    if not pick:
        return "nothing in the library needs subtitles"
    import pd_subs
    client = pd_subs.OpenSubtitles(cfg.get("opensubtitles_key"),
                                   cfg.get("opensubtitles_user"),
                                   cfg.get("opensubtitles_pass"))
    results, err = client.search(pick["title"], pick["year"], language, pick["path"])
    if not results:
        return "keep-alive found nothing for %s (%s)" % (pick["title"], err)
    # a few entries have empty files behind them; one bad one must not waste the
    # keep-alive, and this runs so rarely that three attempts cost nothing
    text = None
    for candidate in results[:3]:
        text, err = client.download(candidate["id"])
        if text:
            break
    if not text:
        return "keep-alive could not download: %s" % (err or "no reason given")
    out = pd_subs.sidecar_path(pick["path"], language,
                               (results[0].get("name") or ""))
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        return "keep-alive could not write: %s" % e
    note_download()
    return "keep-alive fetched subtitles for %s" % pick["title"]


def folder_fingerprint(folders):
    """A cheap summary of what the library's folders look like right now.

    Directory timestamps and names, two levels down - and the timestamp of every folder
    at the second level as well, which is the one that matters for a series: an episode
    dropped into Season 4 changes nothing about the programme's own folder, nor the list
    of seasons inside it. Only Season 4's own timestamp moves.

    Names and timestamps only, never the files' contents: quick enough to do every
    minute on a spinning disk.
    """
    marks = []
    for root in folders:
        if not os.path.isdir(root):
            continue
        try:
            for name in sorted(os.listdir(root)):
                here = os.path.join(root, name)
                try:
                    marks.append("%s:%d" % (name, int(os.path.getmtime(here))))
                    if os.path.isdir(here):
                        for inner in sorted(os.listdir(here))[:400]:
                            below = os.path.join(here, inner)
                            try:
                                if os.path.isdir(below):
                                    marks.append("%s:%d" % (inner,
                                                            int(os.path.getmtime(below))))
                                else:
                                    marks.append(inner)
                            except OSError:
                                marks.append(inner)
                except OSError:
                    continue
        except OSError:
            continue
    return hash(tuple(marks))


def watch_folders():
    """Notice new files, and scan once they have stopped arriving.

    A download writes for as long as it takes; scanning halfway through reads a
    truncated file and has to be done again. So a change is noted, and the scan waits
    until nothing has changed for two minutes.
    """
    settled_after = 120
    was, changed_at = None, 0
    while True:
        try:
            if Handler.game_holds("scans"):
                # the machine is wanted for something else: reading a library is
                # exactly the kind of work that can wait an hour
                time.sleep(60)
                continue
            cfg = local().lib.config()
            every = int(cfg.get("scanEvery") or 0)
            watching = bool(cfg.get("scanOnChange"))
            folders = (cfg.get("movies") or []) + (cfg.get("tv") or []) + \
                      (cfg.get("mixed") or [])
            now = time.time()

            if watching and folders:
                mark = folder_fingerprint(folders)
                if was is None:
                    was = mark
                elif mark != was:
                    was, changed_at = mark, now
                elif changed_at and now - changed_at >= settled_after:
                    changed_at = 0
                    if not local().lib.scan_state.get("running"):
                        local().lib.scan()

            if every:
                last = local().lib.config().get("scannedAt", 0)
                if now - last >= every * 60 and not local().lib.scan_state.get("running"):
                    local().lib.scan()
                    cfg = local().lib.config()
                    cfg["scannedAt"] = int(now)
                    local().lib.save_config(cfg)
        except Exception:
            pass                      # a watcher is never worth taking the server down
        time.sleep(60)


def keepalive_loop():
    """Twice a day is plenty to notice that forty days have gone by."""
    while True:
        try:
            outcome = subtitle_keepalive()
            if "nothing to do" not in outcome:
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write("%s subtitles: %s%s"
                            % (time.strftime("%Y-%m-%d %H:%M"), outcome, chr(10)))
        except Exception:
            pass                       # a keep-alive is never worth taking the server down
        time.sleep(12 * 3600)


#: A WebVTT timestamp, with or without its hour. ffmpeg leaves the hour off for the
#: first hour of a film, which is legal and was quietly fatal here.
STAMP = r"(?:\d+:)?\d{1,2}:\d\d[.,]\d+"


# faults the server itself hit, by signature and when it was last written down
FAULTS_SEEN = {}


def stamp_secs(stamp):
    """Seconds from a WebVTT or SubRip timestamp, in either shape."""
    bits = stamp.replace(",", ".").split(":")
    if len(bits) == 3:
        return int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
    if len(bits) == 2:
        return int(bits[0]) * 60 + float(bits[1])
    return float(bits[0])


def stamp_of(seconds):
    """And back, always with the hour, so nothing downstream has to wonder."""
    seconds = max(0.0, seconds)
    return "%02d:%02d:%06.3f" % (int(seconds // 3600), int(seconds % 3600 // 60),
                                 seconds % 60)


def whole_stamps(text):
    """Every timestamp written out in full, hour included.

    Done once, where a subtitle enters the program, so that everything after it can
    read cues without asking which shape they are in.
    """
    def both(m):
        return stamp_of(stamp_secs(m.group(1))) + " --> " + stamp_of(
            stamp_secs(m.group(2)))
    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), both, text or "")


def shift_vtt(text, seconds):
    """Move every cue in a WebVTT document by so many seconds.

    Anything that would land before the start is clamped there rather than dropped: a
    line at the very beginning is worth keeping even when the offset is negative.
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return text
    if abs(seconds) < 0.01:
        return text

    def moved(stamp):
        return stamp_of(stamp_secs(stamp) + seconds)

    def line(match):
        return moved(match.group(1)) + " --> " + moved(match.group(2))

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), line, text)


def mend_vtt(text, parts, base=0.0):
    """Move each cue by whatever its part of the film asks for.

    A plan is [[from, rate, shift], ...] in order. A subtitle that is simply late has
    one part; one made for a master without the recap this release carries has two, and
    the second is later than the first by however long that recap is.

    A plan is always written in the film's own clock: the rate is measured from the
    first frame and a part begins at a minute of the film. Cues pulled out of a live
    encode count from the encode instead, so `base` says what o'clock their zero is -
    without it a drift is applied as though the viewer had started at the beginning,
    and a film resumed twenty minutes in comes out that much under-corrected.
    """
    if not parts:
        return text
    pieces = []
    for row in parts:
        try:
            pieces.append((float(row[0]), float(row[1]), float(row[2])))
        except (TypeError, ValueError, IndexError):
            continue
    if not pieces:
        return text
    pieces.sort()

    def at(seconds):
        rate, shift = pieces[0][1], pieces[0][2]
        for begins, r, sh in pieces:
            if seconds >= begins:
                rate, shift = r, sh
            else:
                break
        return max(0.0, seconds * rate + shift)

    try:
        base = float(base or 0.0)
    except (TypeError, ValueError):
        base = 0.0

    def moved(stamp):
        # into the film's clock, corrected there, and back to the cues' own
        seconds = stamp_secs(stamp) + base
        return _stamp(max(0.0, at(seconds) - base))

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP),
                  lambda m: moved(m.group(1)) + " --> " + moved(m.group(2)), text)


def _stamp(at):
    return "%02d:%02d:%06.3f" % (int(at // 3600), int(at % 3600 // 60), at % 60)


def stretch_vtt(text, rate, shift):
    """Every timestamp becomes rate * t + shift.

    For a subtitle that drifts - one made for another framerate, or for an edit with
    different breaks in it. That is a second out at the start and four at the end, and
    no single offset is right at both.
    """
    try:
        rate, shift = float(rate), float(shift)
    except (TypeError, ValueError):
        return text
    if abs(rate - 1.0) < 1e-6 and abs(shift) < 0.01:
        return text

    def moved(stamp):
        h, m, rest = stamp.split(":")
        at = (int(h) * 3600 + int(m) * 60 + float(rest)) * rate + shift
        at = max(0.0, at)
        return "%02d:%02d:%06.3f" % (int(at // 3600), int(at % 3600 // 60), at % 60)

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP),
                  lambda m: moved(m.group(1)) + " --> " + moved(m.group(2)), text)


def cut_vtt(text, offset):
    """Everything from `offset` seconds onward, with the clock reset to zero.

    A subtitle file beside a film is cut to the film. A live encode starting at
    twenty-nine minutes has its own clock starting at zero, so handing it the whole
    file leaves the text half an hour ahead of the picture - which on screen is one
    line sitting there while the scene moves on.

    Shifting is not enough on its own: everything before the cut has to go, rather
    than pile up at the beginning.
    """
    try:
        offset = float(offset)
    except (TypeError, ValueError):
        return text
    if offset <= 0.01:
        return text

    secs, stamp = stamp_secs, stamp_of

    out = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), block)
        if not m:
            continue                      # the header, or a stray blank
        start, end = secs(m.group(1)), secs(m.group(2))
        if end <= offset:
            continue                      # already gone by the time this starts
        block = block.replace(m.group(0),
                              stamp(start - offset) + " --> " + stamp(end - offset))
        out.append(block.strip())
        out.append("")
    return chr(10).join(out)


#: Which writing system a line of subtitle is in. Only enough of them to tell two
#: languages apart in one file; anything unrecognised counts as nothing at all.
SCRIPTS = (
    ("han", "一-鿿㐀-䶿豈-﫿"),
    ("kana", "぀-ヿ"),
    ("hangul", "가-힯ᄀ-ᇿ"),
    ("cyrillic", "Ѐ-ӿ"),
    ("greek", "Ͱ-Ͽ"),
    ("arabic", "؀-ۿ"),
    ("hebrew", "֐-׿"),
    ("thai", "฀-๿"),
    ("latin", "A-Za-zÀ-ɏ"),
)

#: What a language code is written in, when it is not the Latin alphabet.
WRITTEN_IN = {"zh": "han", "cmn": "han", "yue": "han", "chi": "han", "zho": "han",
              "ja": "kana", "jpn": "kana", "ko": "hangul", "kor": "hangul",
              "ru": "cyrillic", "rus": "cyrillic", "uk": "cyrillic",
              "bg": "cyrillic", "sr": "cyrillic", "mk": "cyrillic",
              "el": "greek", "gre": "greek", "ell": "greek",
              "ar": "arabic", "ara": "arabic", "fa": "arabic", "ur": "arabic",
              "he": "hebrew", "heb": "hebrew", "iw": "hebrew",
              "th": "thai", "tha": "thai"}


def script_of(said):
    """Which of the writing systems above this text is mostly in."""
    best, most = "", 0
    for name, chars in SCRIPTS:
        n = len(re.findall("[" + chars + "]", said))
        if n > most:
            best, most = name, n
    return best


def last_cue(body):
    """The second the last cue in this WebVTT ends, so a player knows what it holds."""
    last = 0.0
    for m in re.finditer(rb"--> (\d+):(\d\d):(\d\d)[.,](\d\d\d)", body):
        h, mi, sec, ms = (int(x) for x in m.groups())
        last = max(last, h * 3600 + mi * 60 + sec + ms / 1000.0)
    return last


def one_language(text, want="en"):
    """Keep one language when a subtitle file holds two.

    Subtitles are handed out with two languages in one file more often than anybody
    would guess: the English cues, and then the whole thing again in Chinese on the
    same timestamps. Both are live at the same instant, so both are drawn - one line
    of English with a line of Chinese underneath it.

    The language the file claims to be wins. If it claims nothing recognisable, the
    one with the most cues wins. A handful of foreign lines - a sign, a song, a name -
    is not a second language and is left alone.
    """
    blocks = re.split(r"\n\s*\n", text)
    kinds, counted = [], {}
    for block in blocks:
        said = re.sub(r"^\s*\d+\s*$", "", block, flags=re.M)
        said = re.sub(r"\d+:\d\d:\d\d[.,]\d+.*", "", said)
        kind = script_of(said) if said.strip() else ""
        kinds.append(kind)
        if kind:
            counted[kind] = counted.get(kind, 0) + 1
    if len(counted) < 2:
        return text
    order = sorted(counted.items(), key=lambda kv: -kv[1])
    keep = WRITTEN_IN.get((want or "").lower()[:3], "latin")
    if keep not in counted:
        keep = order[0][0]
    other = sum(n for k, n in counted.items() if k != keep)
    # a few stray lines in another alphabet are part of this subtitle, not a second one
    if other < 8 or other * 5 < counted.get(keep, 0):
        return text
    out = [b for b, kind in zip(blocks, kinds) if kind in ("", keep)]
    gap = chr(10) + chr(10)
    body = gap.join(b.strip() for b in out if b.strip()).strip()
    if not re.search(r"\d+:\d\d:\d\d", body):
        return text                       # filtered everything away: leave it be
    return ("WEBVTT" + gap + re.sub(r"^WEBVTT\s*", "", body)).strip() + chr(10)


def flatten_rollup(text):
    """Captions written to roll up the screen, made into what is new in each one.

    Live captioning does not write a line and take it away: it writes a line, and the
    next cue carries that line again with another under it. Turned into WebVTT that
    reads as the next line of dialogue appearing long before it is spoken - one cue
    held from twelve seconds to sixty-eight while the room is silent, because the
    caption that began at twelve was still on the screen at sixty-eight.

    Each cue keeps what is new in it and ends where the next one starts. The overlap
    is a run of lines rather than one, and the case changes as a line rolls up, so
    both are read loosely. Left alone unless the track is plainly of this kind: half
    the cues repeating the lines above is not something a written subtitle does.
    """
    def bare(line):
        return re.sub(r"[^a-z0-9]+", "", line.lower())

    blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        m = re.search(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), block)
        if not m:
            continue
        lines = [l for l in block.split(chr(10))[1:] if l.strip()]
        if lines:
            blocks.append([stamp_secs(m.group(1)), stamp_secs(m.group(2)), lines])
    if len(blocks) < 8:
        return text

    def carried(before, now):
        """How many of this cue's first lines the one before it already had."""
        most = min(len(before), len(now))
        for k in range(most, 0, -1):
            if [bare(x) for x in before[-k:]] == [bare(x) for x in now[:k]]:
                return k
        return 0

    rolling = sum(1 for a, b in zip(blocks, blocks[1:]) if carried(a[2], b[2]))
    if rolling < len(blocks) * 0.4:
        return text                       # an ordinary file: nothing to flatten

    out = ["WEBVTT", ""]
    for n, (began, ended, lines) in enumerate(blocks):
        if n:
            k = carried(blocks[n - 1][2], lines)
            fresh = lines[k:] or lines[-1:]
        else:
            fresh = lines
        stop = min(ended, blocks[n + 1][0]) if n + 1 < len(blocks) else ended
        # A caption stands while it is being spoken, not while the room is silent:
        # the cue that opened this kind of track can otherwise hold three lines for a
        # minute because that is how long it was on the screen.
        stop = min(stop, began + 8.0)
        if stop - began < 0.2:
            stop = began + 0.2
        out.append(stamp_of(began) + " --> " + stamp_of(stop))
        out.extend(fresh)
        out.append("")
    return chr(10).join(out)


def vtt_span(text):
    """When the first cue starts and the last one ends, in seconds."""
    stamps = re.findall(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), text)
    if not stamps:
        return None

    def secs(stamp):
        h, m, rest = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    return secs(stamps[0][0]), max(secs(b) for _, b in stamps)


def film_length(path):
    """How long a video is, asked of ffprobe. Nought when it cannot be told."""
    try:
        from pd_gpu import FFMPEG
        # only the program's own name, not every "ffmpeg" in the path it lives under -
        # the build folder is called ffmpeg-9.0-full_build and renaming that finds
        # nothing at all
        probe = (FFMPEG[:-len("ffmpeg.exe")] + "ffprobe.exe"
                 if FFMPEG.endswith("ffmpeg.exe")
                 else FFMPEG[:-len("ffmpeg")] + "ffprobe"
                 if FFMPEG.endswith("ffmpeg") else "ffprobe")
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return float(out.split()[0]) if out else 0.0
    except Exception:
        return 0.0


def black_after(path, after, window=120):
    """Where the picture goes black next - the join between two parts of an episode.

    A double episode is one file with two programmes in it, and the second one starts
    after a few black frames. Nothing else in the file says where: there are no
    chapters, and the subtitle for the second half is timed from its own zero.
    Returns the second the picture comes back, or nought if it never goes black.
    """
    start = max(0.0, after - 4.0)
    try:
        from pd_gpu import FFMPEG
        out = subprocess.run(
            [FFMPEG, "-hide_banner", "-ss", "%.2f" % start, "-t", "%d" % window,
             "-i", path, "-vf", "blackdetect=d=0.3:pix_th=0.10", "-an",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stderr
    except Exception:
        return 0.0
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", out):
        began, ended = float(m.group(1)), float(m.group(2))
        # the black must come after the last line of the previous part, not be the
        # fade the part itself ended on
        if start + began >= after - 1.0:
            return round(start + ended, 2)
    return 0.0


def as_vtt(path):
    """One subtitle file beside a video, read and turned into WebVTT."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return srt_to_vtt(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return ""


def srt_to_vtt(text):
    """SubRip to WebVTT: a header, and commas in timestamps become full stops."""
    body = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", text.replace("\r", ""))
    return "WEBVTT" + chr(10) + chr(10) + body


#: How long a browser is trusted to remember an invitation. A year, and renewed on
#: every visit, so only somebody who has genuinely stopped using the library is ever
#: asked for their link again.
COOKIE_LIFE = 365 * 86400


#: Measured loudness per part, cached: measuring costs 180 s of ffmpeg and a file
#: does not change. {part id: {"lufs": float, "size": int, "when": int}}
LOUDNESS = {}
LOUDNESS_LOCK = threading.Lock()
#: parts being measured now, so repeated requests do not start repeated measurements
MEASURING = set()


def anybody_watching():
    """Whether a film is being read for somebody this minute, copying aside.

    Asked of the live list rather than of Handler.WATCHING_NOW, which is written while
    a file is being served and never taken down again: measuring stopped at the first
    viewing after a restart and never started again, which is why a library of three
    thousand files had twenty-five measured.
    """
    try:
        return any(r.get("how") != "syncing" for r in WATCHING.snapshot())
    except Exception:
        return True                    # unanswerable: leave the disk alone
#: the file being measured this minute, for Now playing to show
MEASURE_NOW = {"name": "", "since": 0}

#: Target loudness. Not EBU's -23: this library holds two copies of one episode 13 dB
#: apart, and the usual level here is nearer streaming's -16 to -18.
VOLUME_TARGET = -18.0
#: and the maximum correction applied
VOLUME_MOST = 8.0


def loudness_file():
    return os.path.join(ROOT, "loudness.json")


def read_loudness():
    """The cached measurements, read from disk once."""
    if LOUDNESS:
        return LOUDNESS
    try:
        with open(loudness_file(), encoding="utf-8") as f:
            LOUDNESS.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    return LOUDNESS


def write_loudness():
    try:
        with open(loudness_file(), "w", encoding="utf-8") as f:
            json.dump(LOUDNESS, f)
    except OSError:
        pass


def measure_loudness(part, path, size, playing=False):
    """Measure one file on a background thread and cache the result.

    `playing` is the one exception to waiting for a quiet machine: the file somebody is
    watching this minute. It is already being read off that disk, and it is the only
    file whose measurement anybody is waiting for - left to the quiet rule it could
    never be measured at all, because the rule is "not while anybody is watching" and
    the file in question is the one being watched.
    """
    def work():
        MEASURE_NOW["name"] = os.path.basename(path or "")
        MEASURE_NOW["since"] = int(time.time())
        try:
            import pd_gpu
            said = pd_gpu.loudness(path)
        except Exception:
            said = None
        MEASURE_NOW["name"] = ""
        with LOUDNESS_LOCK:
            MEASURING.discard(str(part))
            if said is not None:
                LOUDNESS[str(part)] = {"lufs": said, "size": int(size or 0),
                                       "when": int(time.time())}
                write_loudness()
    # Not while anything is playing or copying: measuring is a 180 s read off the
    # same disks. Two concurrent measurements held a copy at 41 Mbit with the drive
    # at 57 ms latency. An unmeasured file plays uncorrected until the next quiet
    # moment.
    if (anybody_watching() and not playing) or Handler.disk_reading_slow():
        return
    with LOUDNESS_LOCK:
        read_loudness()
        known = LOUDNESS.get(str(part))
        if known and int(known.get("size") or 0) == int(size or 0):
            return
        if str(part) in MEASURING or len(MEASURING) >= 1:
            return               # one at a time: this is ffmpeg, not a lookup
        MEASURING.add(str(part))
    threading.Thread(target=work, name="palladium-loudness", daemon=True).start()


#: When each title fetched before anybody asked for it was first named, so one
#: nobody ever got to can be dropped rather than held for ever. Kept on disk: the
#: server is restarted often enough that a clock living in memory would never run out.
AHEAD_SINCE = {}
AHEAD_DAYS = 30.0


def ahead_file():
    return os.path.join(ROOT, "ahead.json")


def read_ahead():
    """When each fetched-ahead title was first named, read from disk once."""
    if AHEAD_SINCE:
        return AHEAD_SINCE
    try:
        with open(ahead_file(), encoding="utf-8") as f:
            AHEAD_SINCE.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    return AHEAD_SINCE


def write_ahead():
    try:
        with open(ahead_file(), "w", encoding="utf-8") as f:
            json.dump(AHEAD_SINCE, f)
    except OSError:
        pass


#: the sweep through everything not measured yet, and when it last looked
MEASURE_SWEEP = {"at": 0.0, "left": None}
MEASURE_EVERY = 20.0


def measure_the_backlog():
    """Measure one unmeasured file, whenever nothing is playing and the disk is easy.

    Called from the housekeeping round. One file at a time and only while the machine
    is quiet, so it costs nothing anybody can feel; a library of a few thousand files
    works through itself over a few nights, and a file that arrives tomorrow is picked
    up the same way without anybody asking.
    """
    now = time.time()
    if now - MEASURE_SWEEP["at"] < MEASURE_EVERY:
        return
    MEASURE_SWEEP["at"] = now
    if anybody_watching() or Handler.disk_reading_slow():
        return
    with LOUDNESS_LOCK:
        read_loudness()
        if MEASURING:
            return                     # one is running: it is ffmpeg, not a lookup
        known = dict(LOUDNESS)
    left = MEASURE_SWEEP["left"]
    if not left:
        # Read once and worked through, rather than a query per file: the point is to
        # be cheap enough to run every twenty seconds for a week.
        con = local().lib.db()
        try:
            rows = con.execute(
                "SELECT id, path, size FROM file WHERE path IS NOT NULL "
                "ORDER BY id DESC").fetchall()
        finally:
            con.close()
        left = [(r["id"], r["path"], r["size"]) for r in rows
                if str(r["id"]) not in known]
        MEASURE_SWEEP["left"] = left
    while left:
        part, path, size = left.pop()
        if str(part) in known:
            continue
        measure_loudness(part, path, size or 0)
        return


def keep_measuring():
    """Work through the unmeasured files, one at a time, for as long as the server runs.

    Its own loop rather than a turn in the folder watcher: that one walks the media
    folders to notice new files, and asking it to tick three times as often to measure
    faster would have tripled the walking as well.
    """
    while True:
        try:
            measure_the_backlog()
        except Exception:
            pass                      # measuring is never worth taking the server down
        time.sleep(MEASURE_EVERY)


def volume_gain(part, path=None, size=0, cfg=None, playing=False):
    """Correction in dB for this file, or 0.

    Every measured file is corrected, to the cap. A dead band left anything within
    4 dB of the target alone, which put two files measuring 0.2 dB apart 3.9 dB apart
    out of the speakers - the opposite of what evening out is for.
    """
    stored = cfg if cfg is not None else (read_settings() or {})
    if not stored.get("evenVolume"):
        return 0.0
    with LOUDNESS_LOCK:
        known = read_loudness().get(str(part))
    if not known:
        if path:
            measure_loudness(part, path, size, playing)
        return 0.0
    try:
        target = float(stored.get("volumeTarget") or VOLUME_TARGET)
    except (TypeError, ValueError):
        target = VOLUME_TARGET
    off = target - float(known.get("lufs") or target)
    return round(max(-VOLUME_MOST, min(VOLUME_MOST, off)), 1)


def hand_over_the_gain():
    """Give pd_localapi the gain and loudness lookups. Called once at startup."""
    try:
        import pd_localapi
        pd_localapi.GAIN_OF[0] = lambda part, path, size: volume_gain(part, path, size)
        pd_localapi.LOUDNESS_OF[0] = lambda part: (
            (read_loudness().get(str(part)) or {}).get("lufs"))
    except Exception:
        pass


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
    rows = INVITES.load()
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
            for name in ("cacheDeck", "cacheList", "cacheCasual"):
                row[name] = bool(theirs_row.get(name))
            # role, refreshed each round, so a key promoted or demoted on the main
            # server changes here too. Cache keys are filtered out before sending.
            if row.get("borrowed"):
                row["role"] = str(theirs_row.get("role") or "")
    INVITES.save(keep)
    if isinstance(look, dict) and look:
        # How the main server draws each person's subtitles, kept here under the same key.
        # Only the look and the language: what they have watched is theirs and is
        # already carried the other way.
        stored = read_settings()
        if stored is not None:
            for who, said in look.items():
                if not isinstance(said, dict):
                    continue
                where = stored if who == "me" else (
                    stored.setdefault("users", {}).setdefault(str(who), {}))
                for name in ("subtitles", "perTitle", "subLanguage", "language"):
                    if said.get(name) is not None:
                        where[name] = said[name]
            write_settings(stored)
    if isinstance(owner, dict):
        stored = read_settings()
        if stored is not None:
            stored["copyOf"] = {"master": master, "owner": owner,
                                "when": int(time.time())}
            # Who the main server calls its owner is written down but not adopted: this
            # machine is not that person, and saying it is would hand a name to a
            # box in a cupboard. The places that arrive under that name are filed
            # against this machine's own owner instead.
            write_settings(stored)



class Handler(http.server.SimpleHTTPRequestHandler):
    role = "owner"          # set per request by who(); the default matters only if a
    guest_name = ""         # handler somehow answers before the guard has run

    #: Headers and body leave as two writes, so with Nagle on, the second one waits
    #: for the first to be acknowledged. On this network that wait was two seconds,
    #: exactly, on every reply longer than a single segment - a shelf listing took
    #: 2.15s to send 3.4kB while the server itself had answered in 17ms. Small
    #: replies fitted in one segment and were instant, which is why it looked like a
    #: slow machine rather than a stalled socket.
    disable_nagle_algorithm = True

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC, **kw)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return {}

    #: Everything that has talked to this server since it started: one row per
    #: address and build, so the same television on two builds shows as two until the
    #: old one stops asking. Held in memory - it is a picture of now, not a record.
    SEEN = {}
    SEEN_MAX = 40

    def note_client(self):
        """Who is asking, and what build they are.

        The app says so in a header; a browser is told to say so by the page it
        loaded, which is how a tab left open on last week's build can be told from
        one that has been reloaded since.
        """
        said = self.app_name()
        if not said:
            return
        where = self.client_address[0]
        key = "%s|%s" % (where, said)
        row = self.SEEN.get(key)
        if row is None:
            if len(self.SEEN) >= self.SEEN_MAX:
                # the oldest silence goes first
                for old in sorted(self.SEEN, key=lambda k: self.SEEN[k]["when"])[:5]:
                    self.SEEN.pop(old, None)
            bits = said.split()
            row = {"said": said, "kind": self.device_kind() or "",
                   "version": next((b for b in bits if b[:1].isdigit()), ""),
                   "where": where}
            self.SEEN[key] = row
        # A guest is known by the name on their invitation; anybody in the house is
        # known by the address of the screen they are on. What kind of device it is
        # says nothing about who is watching. This runs before the guard has decided
        # the role, so the token is read here rather than waiting for self.guest_name.
        now = time.time()
        if now - row.get("named", 0) > 60:
            # a film is dozens of requests a second: the list is read once a minute
            # per screen, not once per request
            row["named"] = now
            token = self.bearer()
            row["name"] = ""
            if token:
                for one in INVITES.load():
                    if hmac.compare_digest(str(one.get("token", "")), token):
                        row["name"] = one.get("name", "")
                        break
            # and whoever sits at this machine is a person too. With no key of their
            # own there was no name to find, so the owner's own screens stood in the
            # list as bare addresses among named guests - the one person the server
            # certainly knows, shown as a number.
            if not row["name"] and self.at_home():
                row["name"] = str((read_settings() or {}).get("ownerName") or "")
        row["when"] = time.time()

    def safely(self, what):
        """Run one request, and answer even when it goes wrong.

        A handler that raises leaves the browser with a closed connection and no
        status - it looks like the server died, and with enough of them it has. This
        turns any fault into a 500 with a line of explanation, and writes the whole of
        it to the log where it can be read afterwards.
        """
        try:
            self.note_client()
        except Exception:
            pass                       # knowing who called is never worth a 500
        try:
            what()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                       # the other end went away mid-answer; not ours
        except Exception as e:
            import traceback
            # print_exc goes to stdout, and the server runs under pythonw, which has
            # none: every fault the server itself hit was written to nowhere. It goes
            # to the log, and onto the noticeboard beside the faults the app and the
            # browser report, so there is one place to look rather than three.
            trail = traceback.format_exc()
            try:
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write("%s fault %s %s%s%s%s" % (
                        time.strftime("%H:%M:%S"), self.command,
                        self.path.split("?")[0], chr(10), trail, chr(10)))
            except Exception:
                pass
            self.note_fault(trail)
            try:
                self.send_error(500, "%s: %s" % (type(e).__name__, str(e)[:160]))
            except Exception:
                pass                   # the answer had already begun; the log has it

    def do_POST(self):
        self.safely(self._do_POST)

    def _do_POST(self):
        path = self.path.split("?")[0]
        self.role = self.who(path)
        # What a guest may write is what belongs to them: their own subtitle settings,
        # the track they chose, a subtitle fetched for a film they are watching, and a
        # report. Everything else here changes the library, the invitations, or files
        # on disk, and belongs to whoever owns the server.
        GUEST_MAY_POST = ("/applog", "/log", "/trace", "/feedback",
                          "/feedback/cancel", "/settings",
                          "/subs/pick", "/copy/pick",
                          "/subs/find", "/subs/get", "/subs/auto", "/subs/sync",
                          # making one out of the soundtrack is the same kind of act
                          # as fetching one: it is a subtitle for what they are watching
                          "/subs/make", "/subs/stop",
                          "/subs/plan",
                          # vouching for a subtitle is a fact about the file, and the
                          # person who just watched it is the one who knows. Removing
                          # or hiding one is not on this list: those are the owner's.
                          "/subs/verify", "/subs/reset",
                          # their own shelves, and trying a rule before saving it
                          "/watchlist", "/favorites", "/collections", "/collections/test",
                          "/collections/season",
                          # a title on or off a shelf, and a shelf played in shuffle: the
                          # round belongs to the person, and the owner is a guest from outside
                          "/collections/for", "/collections/mark",
                          # a download they asked for, stopped by them
                          "/torrents/cancel",
                          "/collections/shuffle", "/collections/shuffle/queue",
                          "/collections/shuffle/reset",
                          # a guest is a person in the room, and a room where only the
                          # owner may speak is not a conversation
                          "/chat", "/party",
                          "/ondeck/aside",
                          # what a screen has left to play, which is the screen's own
                          # business and the one thing only it knows
                          "/stream/buffer",
                          # one film from a torrent pack, within their week's limit
                          "/torrents/get")
        # Giving the password is how somebody who is nobody becomes the owner, so
        # it cannot be behind the check for being the owner. Its own rate limit is
        # what guards it.
        if path in ("/login", "/logout"):
            pass
        elif self.role != "owner" and not (self.role == "guest" and
                                           path in GUEST_MAY_POST):
            self.refused(path, "not something a guest may write")
            self.send_error(403, "not allowed")
            return
        # and one thing a guest must not do even on a path they may use: change what
        # every viewer gets
        if self.role == "guest" and path == "/settings":
            peek = self.read_json() or {}
            if ("autoNext" in peek or "quality" in peek or "nextDelay" in peek):
                self.send_error(403, "that setting belongs to the server")
                return
            self.settings_body = peek
        if path == "/invites/role":
            # What one key is for, changed on the key itself. The flags underneath it
            # are kept in step so everything that asks "does this key follow us" goes
            # on working; the role is the thing somebody sets.
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            role = str(body.get("role") or "")
            if role not in Handler.ROLES:
                self.reply_json({"error": "no such role"}, 400)
                return
            if role == "cache":
                # it has to be a machine: one that has announced itself to this server
                # with that key and is not stopped. A person's key cannot be a cache.
                known = any(str(v.get("token") or "") == token
                            for v in Handler.FOLLOWERS.values())
                if not known:
                    self.reply_json({"error": "That key has not been used by a server "
                                              "yet. Point the other machine at this one "
                                              "with that invitation first."}, 400)
                    return
            rows = INVITES.load()
            for row in rows:
                if row.get("token") != token:
                    continue
                row["role"] = role
                row["follows"] = role == "cache"
                if not row["follows"]:
                    row.pop("follows", None)
                INVITES.save(rows)
                self.reply_json({"ok": True, "role": role})
                return
            self.reply_json({"error": "no such key"}, 404)
            return
        if path == "/invites":
            body = self.read_json()
            row = INVITES.create((body.get("name") or "").strip()[:40],
                                 int(body.get("days") or 0),
                                 (body.get("email") or "").strip()[:120])
            # Two kinds of key from one door. A guest is a person; a machine key lets
            # another server read this library and keep copies of it.
            kind = str(body.get("kind") or "guest")
            if kind == "machine":
                rows = INVITES.load()
                for r in rows:
                    if r["token"] != row["token"]:
                        continue
                    r["follows"] = True
                    if body.get("cap") is not None:
                        try:
                            r["cap"] = max(0.0, float(body.get("cap") or 0))
                        except (TypeError, ValueError):
                            r["cap"] = 0.0
                    if body.get("mayCopy") is not None:
                        r["mayCopy"] = bool(body.get("mayCopy"))
                    INVITES.save(rows)
                    row = r
                    break
            self.reply_json(dict(self.with_link(row), kind=kind))
            return
        if path == "/ondeck/aside":
            # "I am done with this": the programme leaves Continue watching and stays
            # off until it is played again, which is the only thing that should put it
            # back there
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            if not key:
                self.reply_json({"error": "which one?"}, 400)
                return
            local().who = self.viewer()
            con = local().lib.db()
            cleared = 0
            try:
                family = local().deck_family(con, key)
                if body.get("on", True):
                    # Done with it means done with it: the place is dropped, not
                    # remembered behind a date. A film left at eighty-nine per cent
                    # went on drawing a bar across its poster and went on being
                    # copied to the other machine as something part-way through -
                    # both reading a row the shelf had already been told to ignore.
                    # A tick made by hand says something else and is left alone.
                    keys = [family]
                    if not family.startswith("e"):
                        keys += [str(r["id"]) for r in con.execute(
                            "SELECT id FROM episode WHERE item_id=?", (family,))]
                    for one in keys:
                        cleared += con.execute(
                            "DELETE FROM progress WHERE who=? AND key=? "
                            "AND COALESCE(marked, 0) = 0",
                            (local().who, str(one))).rowcount
                    con.commit()
            finally:
                con.close()
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            aside = dict(mine.get("deckAside") or {})
            if body.get("on", True):
                aside[family] = int(time.time())
            else:
                aside.pop(family, None)
            mine["deckAside"] = aside
            write_settings(stored)
            self.reply_json({"aside": list(aside.keys()), "cleared": cleared})
            return
        if path == "/party":
            # Starting one is the owner's; joining is anybody's, and is done by
            # reading rather than by pressing.
            body = self.read_json() or {}
            if "on" in body:
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                PARTY["on"] = bool(body["on"])
                PARTY["since"] = int(time.time()) if PARTY["on"] else 0
                if PARTY["on"]:
                    # what everybody is here to watch, if it was said
                    PARTY["key"] = str(body.get("key") or "")[:40]
                    PARTY["title"] = str(body.get("title") or "")[:120]
                if not PARTY["on"]:
                    PARTY["who"].clear()
                    PARTY["asked"].clear()
                    PARTY["title"] = PARTY["key"] = ""
                    # the party's own room goes; the lobby is not an evening and stays
                    CHAT[:] = [m for m in CHAT if m.get("room") == "lobby"][-CHAT_KEEP:]
                else:
                    self.join_party()
                CHAT_RUNG.set()
                CHAT_RUNG.clear()
            elif body.get("invite"):
                # asking somebody to watch: they see it wherever they are, and it
                # waits until they answer or the evening ends
                whom = body.get("invite")
                whom = whom if isinstance(whom, list) else [whom]
                for one in whom:
                    key = str(one or "").strip()[:80]
                    if key:
                        PARTY["asked"][key] = {
                            "from": self.watcher(),
                            "title": PARTY.get("title", "") or str(body.get("title") or ""),
                            "key": PARTY.get("key", "") or str(body.get("key") or ""),
                            "when": int(time.time())}
                CHAT_RUNG.set()
                CHAT_RUNG.clear()
            elif body.get("accept"):
                PARTY["asked"].pop(self.party_key(), None)
                self.join_party()
            elif body.get("decline"):
                PARTY["asked"].pop(self.party_key(), None)
            elif body.get("leave"):
                PARTY["who"].pop(self.party_key(), None)
            self.reply_json({"on": bool(PARTY["on"]), "since": PARTY["since"],
                             "people": len(PARTY["who"])})
            return
        if path == "/chat":
            # Anybody who may watch may speak: a guest is a person in the room, and a
            # room where only the owner talks is not a conversation.
            body = self.read_json() or {}
            if not self.wants_a_party():
                self.reply_json({"ok": False,
                                 "why": "Watch parties are off for you"}, 409)
                return
            room = str(body.get("room") or "party")
            if room != "lobby":
                if not PARTY["on"]:
                    self.reply_json({"ok": False,
                                     "why": "There is no watch party on"}, 409)
                    return
                self.join_party()
            words = str(body.get("text") or "").strip()[:300]
            if not words:
                self.reply_json({"ok": False, "why": "nothing to say"}, 400)
                return
            # Whose voice it is. A guest is known by their invitation; the owner
            # may say who they are, which is also how a screen with no name of its
            # own - a television in the front room - signs what it sends.
            from_who = self.watcher()
            if self.role == "owner":
                asked = str(body.get("from") or "").strip()[:40]
                if asked:
                    from_who = asked
            CHAT.append({"id": (CHAT[-1]["id"] + 1) if CHAT else 1,
                         "who": from_who, "from": from_who, "text": words,
                         # which room it was said in
                         "room": "lobby" if room == "lobby" else "party",
                         # the party by default; an address when somebody is
                         # talking to one screen, which is what a test is
                         "to": str(body.get("to") or
                                   ("all" if room == "lobby" else "party")).strip()[:60],
                         "when": int(time.time())})
            del CHAT[:-CHAT_KEEP]
            CHAT_RUNG.set()
            CHAT_RUNG.clear()
            self.reply_json({"ok": True, "id": CHAT[-1]["id"]})
            return
        if path == "/notice":
            # Sent to every screen in the house, and gone when it expires. The owner's:
            # nobody else's words belong on somebody else's television.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            words = str(body.get("text") or "").strip()[:200]
            hold = max(10, min(int(body.get("seconds") or 600), 86400))
            NOTICE["id"] = NOTICE.get("id", 0) + 1
            NOTICE["text"] = words
            # who it is for: nothing means the main server, an address means one screen,
            # "all" means everybody including whoever is watching from away
            NOTICE["to"] = str(body.get("to") or "").strip()[:60]
            NOTICE["until"] = time.time() + hold if words else 0
            # a line of words is not an instruction: whatever was to be played has
            # been played, and must not be started again by the next screen to ask
            NOTICE["play"] = ""
            NOTICE["at"] = 0
            # everyone waiting hears it now
            NOTICE_RUNG.set()
            NOTICE_RUNG.clear()
            self.reply_json({"id": NOTICE["id"], "text": words, "seconds": hold})
            return
        if path == "/screen/play":
            # Open a title on a screen in the house, from here. There was no way to
            # do it at all: a television could be told a line of words and nothing
            # else, so anything that had to be played on it had to be found on it,
            # with the remote. Carried on the same held connection the notice uses,
            # so it lands the moment it is sent.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = str(body.get("key") or "").strip()[:40]
            if not key:
                self.reply_json({"error": "which one?"}, 400)
                return
            NOTICE["id"] = NOTICE.get("id", 0) + 1
            NOTICE["play"] = key
            try:
                NOTICE["at"] = max(0, int(body.get("at") or 0))
            except (TypeError, ValueError):
                NOTICE["at"] = 0
            # a word with it, or none: "playing this on your television" is worth
            # saying when somebody else set it going
            NOTICE["text"] = str(body.get("text") or "").strip()[:200]
            NOTICE["until"] = time.time() + 30 if NOTICE["text"] else 0
            NOTICE["to"] = str(body.get("to") or "").strip()[:60]
            NOTICE_RUNG.set()
            NOTICE_RUNG.clear()
            self.reply_json({"id": NOTICE["id"], "play": key, "at": NOTICE["at"],
                             "to": NOTICE["to"]})
            return
        if path == "/machine":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            body = self.read_json() or {}
            if "startup" in body:
                pd_machine.set_start_at_login(bool(body["startup"]))
            if "firewall" in body:
                # these put up Windows' own prompt, and wait for it
                if body.get("firewall"):
                    ok, why = pd_machine.open_firewall(PORT)
                else:
                    ok, why = pd_machine.close_firewall(PORT)
                if not ok:
                    self.reply_json(dict(pd_machine.state(PORT, LAN_IP), why=why), 200)
                    return
            if body.get("private"):
                # a network Windows calls public is shut to the main server whatever rule
                # is written, and this is the one thing that changes that
                ok, why = pd_machine.make_private()
                if not ok:
                    self.reply_json(dict(pd_machine.state(PORT, LAN_IP), why=why), 200)
                    return
            self.reply_json(pd_machine.state(PORT, LAN_IP))
            return
        if path == "/update/install":
            # Fetch it, check it against the hash the site published, and hand it to
            # the installer. The answer goes out before the program is replaced,
            # because the program answering is the one about to be shut.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            if not self.in_the_house():
                self.reply_json({"ok": False,
                                 "why": "The server is replaced from a screen in the "
                                        "same house as it"}, 403)
                return
            import pd_update
            stored = self.settings_file()
            if not pd_update.packaged():
                self.reply_json({"ok": False,
                                 "why": "This copy runs from source; update it with git"},
                                400)
                return
            # one already on this machine, from a build made here: nothing to fetch,
            # nothing to check against a published hash - the file is the file
            near, path = pd_update.beside(self.build_version(),
                                          self.installer_folders())
            if near:
                self.reply_json({"ok": True, "version": near,
                                 "installer": os.path.basename(path)})
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                pd_update.install(path)
                return
            # The machine this one follows, if it has the installer and this one is
            # allowed to take it from there: the same file over the network the two
            # share, rather than 34 MB across the internet - and it guarantees the two
            # end up on the same build rather than both chasing what is published.
            body = self.read_json() or {}
            onto = ""
            said = {}
            # The library's settings, read so that a library which will not open does
            # not also stop the one thing that could mend it. The cache's first key
            # conversion left a half-made table behind, every start after that fell
            # over opening the library, and this door asked the library for its
            # settings before it did anything - so the build with the fix could not
            # be taken, by this door, by the tray, or by the cache asking it.
            cfg = self.library_settings_plain()
            if not body.get("fromSite"):
                try:
                    import pd_follow
                    onto, said = pd_follow.build_from_master(pd_follow.settings(cfg))
                except Exception:
                    onto, said = "", {}
            if not onto:
                if cfg.get("fetchFromSite") is False:
                    self.reply_json({"ok": False,
                                     "why": "This server is set not to fetch anything "
                                            "from palladium.video, and the machine it "
                                            "follows has no installer to give it"}, 409)
                    return
                said = pd_update.latest(force=True) or {}
                # Never backwards, and only here. A site left announcing an old build
                # had this server replace itself with one from weeks ago - a working
                # install of the wrong thing, so nothing afterwards notices. An
                # installer made on this machine is a different matter and is checked
                # where it is found; putting this at the top of the door refused every
                # install there is, including the ones that were newer.
                here_now = self.build_version()
                offered = str(said.get("version") or "")
                if (offered and here_now and offered != here_now
                        and not pd_update.newer(offered, here_now)):
                    self.reply_json({"ok": False,
                                     "why": "The site offers %s and this server is "
                                            "%s. It will not go backwards."
                                            % (offered, here_now)}, 400)
                    return
                if not said.get("version"):
                    self.reply_json({"ok": False,
                                     "why": "palladium.video did not answer"}, 503)
                    return
            try:
                if not onto:
                    onto = pd_update.fetch(stored.get("betaKey", ""),
                                           said.get("sha256"))
            except Exception as e:
                Handler.note_fault(None, traceback.format_exc())
                self.reply_json({"ok": False, "why": "Could not fetch it: %s" % e}, 502)
                return
            self.reply_json({"ok": True, "version": said.get("version", ""),
                             "installer": os.path.basename(onto)})
            try:
                self.wfile.flush()
            except Exception:
                pass
            pd_update.install(onto)
            return
        if path == "/requests":
            # Somebody asking for a film this house has not got. It is written down
            # rather than acted on: what comes into the house is the owner's to decide,
            # and a request that fetched by itself would be a download button by
            # another name.
            body = self.read_json() or {}
            key = str(body.get("key") or "")[:40]
            if not key:
                self.reply_json({"error": "which title?"}, 400)
                return
            book = self.read_requests()
            mine = book.get(key) or {}
            if mine and not mine.get("done"):
                self.reply_json({"already": True, "request": mine})
                return
            title = str(body.get("title") or mine.get("title") or "")[:200]
            who = self.guest_name if self.role == "guest" else "you"
            mine = {"key": key, "title": title,
                    "year": body.get("year") or mine.get("year"),
                    "who": who, "when": int(time.time()),
                    "where": str(body.get("where") or "")[:300],
                    "done": False}
            book[key] = mine
            self.write_requests(book)
            # and on the noticeboard, where requests are already answered
            try:
                self.file_report("request",
                                 ("%s asked for %s" % (who, title or key))
                                 + (chr(10) + str(mine.get("where") or "")).rstrip(),
                                 self.app_name())
            except Exception:
                pass
            self.reply_json({"asked": True, "request": mine})
            return
        if path == "/requests/done":
            # marked as dealt with, so the poster stops saying it is being asked for
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            book = self.read_requests()
            if key in book:
                book[key]["done"] = bool(body.get("done", True))
                book[key]["doneWhen"] = int(time.time())
                self.write_requests(book)
            self.reply_json({"requests": self.read_requests()})
            return
        if path == "/collections/shuffle":
            # Putting a shelf on: a draw, a look, a step back, or carrying on with
            # what was left. The main server owns the round, so a machine keeping copies
            # asks there first and hands the answer back.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            self.reply_json(self.shuffle_draw(
                str(body.get("id") or ""), bool(body.get("peek")),
                bool(body.get("resume")), bool(body.get("back"))))
            return
        if path == "/collections/shuffle/queue":
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            self.reply_json(self.shuffle_lists(str(body.get("id") or "")))
            return
        if path == "/collections/shuffle/reset":
            # The hat back to full, the history empty, and nothing kept half-watched.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            one = self.shuffle_round(mine, str(body.get("id") or ""))
            one.update({"queue": [], "played": [], "at": {},
                        "run": int(one.get("run") or 1) + 1})
            Handler.round_moved(one)
            write_settings(stored)
            self.reply_json({"ok": True, "run": one["run"]})
            return
        if path == "/collections/test":
            # What a rule would take, without saving it. The form sends what is typed
            # in its boxes: what the include words catch is green, what the exclude
            # words or the hand then knock out is red.
            body = self.read_json() or {}
            shelf = {"mode": str(body.get("mode") or "filter"),
                     "rule": body.get("rule") or {},
                     "pinned": body.get("pinned") or [],
                     "hidden": body.get("hidden") or []}
            # the same reckoning a save makes: a pin the words already catch, or a
            # strike on something they never caught, is no override at all
            if shelf["mode"] != "manual":
                con = local().lib.db()
                try:
                    byrule = set(self.collection_keys(
                        con, dict(shelf, pinned=[], hidden=[])))
                finally:
                    con.close()
                if byrule:
                    shelf["pinned"] = [k for k in shelf["pinned"]
                                       if str(k) not in byrule]
                    shelf["hidden"] = [k for k in shelf["hidden"]
                                       if str(k) in byrule]
            wide = dict(shelf, hidden=[],
                        rule=dict(shelf["rule"], without=[], seasonWithout=""))
            con = local().lib.db()
            try:
                took = self.collection_keys(con, shelf)
                held = set(took)
                # which of them are there by hand rather than by the rule, so the
                # test can say so and the mark can be taken back
                pinned = set(str(k) for k in (shelf["pinned"] or []))
                struck = set(str(k) for k in (shelf["hidden"] or []))
                rows, out = [], []
                # Read the same way the saved shelf is, or the test says one thing
                # and the shelf it makes says another: a programme as its seasons,
                # and part of a season as that season with a count on it.
                def cards(keys):
                    seasoned, rest = self.read_as_seasons(con, [str(k) for k in keys])
                    return seasoned + [one for one in
                                       (local().metadata_for(con, k, brief=True)
                                        for k in rest) if one]
                for one in cards(took):
                    one["hand"] = str(one.get("ratingKey")) in pinned
                    rows.append(one)
                # and what the rule would take with both excludes lifted, less
                # whatever is already above: those are the ones that are out
                shown = set(str(m.get("ratingKey")) for m in rows)
                for one in cards(self.collection_keys(con, wide)):
                    key = str(one.get("ratingKey"))
                    if key in shown:
                        continue
                    one["hand"] = key in pinned or key in struck
                    out.append(one)
            finally:
                con.close()
            # by programme and then by number, or "Season 10" sorts before "Season 2"
            order = lambda m: (int(m.get("year") or 0),
                               (m.get("titleSort")
                                or (m.get("parentTitle") if m.get("type") == "season"
                                    else "")
                                or m.get("title") or ""),
                               int(m.get("index") or 0))
            rows.sort(key=order)
            out.sort(key=order)
            self.reply_json({"Metadata": rows, "Excluded": out})
            return
        if path == "/collections":
            # make, change or remove one. The shelves are the viewer's own, like the
            # watchlist they sit beside.
            body = self.read_json() or {}
            shelves = self.collections()
            want = str(body.get("id") or "")
            # Two shelves with one name cannot be told apart on the row of buttons,
            # and "Watchlist" is the button that stands for having no collection
            # open at all.
            asked = str(body.get("name") or "").strip()
            if asked:
                taken = [c for c in shelves
                         if c.get("id") != want
                         and (c.get("name") or "").strip().lower() == asked.lower()]
                if taken or asked.lower() == "watchlist":
                    self.reply_json({
                        "error": ("There is already a collection called %s." % asked)
                                 if taken else
                                 "Watchlist is the name of the shelf itself.",
                        "collections": shelves}, 409)
                    return
            if body.get("remove") and want:
                self.save_collections([c for c in shelves if c.get("id") != want])
                self.reply_json({"collections": self.collections()})
                return
            if want:
                for c in shelves:
                    if c.get("id") != want:
                        continue
                    for field in ("name", "rule", "pinned", "hidden", "cover",
                                  "mode", "view"):
                        if field in body:
                            c[field] = body[field]
                    # What the rule says on its own, so that a mark records only
                    # what the rule does not already say: putting back something the
                    # words catch is lifting the strike, not pinning it, and taking
                    # out something they never caught is lifting the pin. Otherwise
                    # a shelf fills with overrides that agree with the filter, and
                    # every poster claims to have been edited by hand.
                    byrule = set()
                    if (str(c.get("mode") or "filter") != "manual"
                            and (body.get("add") or body.get("drop")
                                 or "rule" in body)):
                        con = local().lib.db()
                        try:
                            byrule = set(self.collection_keys(
                                con, dict(c, pinned=[], hidden=[])))
                        finally:
                            con.close()
                    # One title in or out. Said as add and drop rather than as two
                    # lists, because a title struck out has to leave the pinned list
                    # as well as join the struck-out one - a rule may catch it, and
                    # then it is only out if it is out of both.
                    for key in ([body["add"]] if body.get("add") else []):
                        key = str(key)
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) != key]
                        if key not in byrule and key not in [
                                str(k) for k in (c.get("pinned") or [])]:
                            c["pinned"] = (c.get("pinned") or []) + [key]
                    # Taking a manual edit back: the title stops being pinned or
                    # struck out and the rule decides it again, whichever way that
                    # falls. Said as its own verb because neither add nor drop means
                    # "no opinion".
                    for key in ([body["clear"]] if body.get("clear") else []):
                        key = str(key)
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) != key]
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) != key]
                    for key in ([body["drop"]] if body.get("drop") else []):
                        key = str(key)
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) != key]
                        if key in byrule and key not in [
                                str(k) for k in (c.get("hidden") or [])]:
                            c["hidden"] = (c.get("hidden") or []) + [key]
                    # and the same reckoning over the whole shelf: an override the
                    # rule now agrees with is no override at all, and editing the
                    # words is the usual way one stops meaning anything
                    if byrule:
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) not in byrule]
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) in byrule]
                    break
            else:
                shelves.append({
                    "cover": str(body.get("cover") or ""),
                    "mode": ("manual" if str(body.get("mode") or "") == "manual"
                             else "filter"),
                    "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFF),
                    "name": str(body.get("name") or "Collection")[:60],
                    "rule": body.get("rule") or {},
                    "pinned": body.get("pinned") or [],
                    "hidden": body.get("hidden") or [],
                })
            self.save_collections(shelves)
            self.reply_json({"collections": self.collections()})
            return
        if path == "/watchlist":
            # "that one, later": a film, an episode or a whole series, kept in this
            # viewer's corner rather than in the library, which belongs to everyone
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            if not key:
                held = self.watchlist()
                full, part = self.rollup(held)
                self.reply_json({"watchlist": held, "covers": full, "part": part})
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            # the same as the shuffle: a programme or a season is its episodes
            keys = self.spread(key)
            marked = [str(k) for k in (mine.get("watchlist") or [])
                      if str(k) != key and str(k) not in keys]
            if body.get("on", True):
                # newest first: the order it was thought of
                marked = keys + marked
            mine["watchlist"] = marked[:400]
            write_settings(stored)
            full, part = self.rollup(marked)
            self.reply_json({"watchlist": marked, "on": key in marked,
                             "covers": full, "part": part})
            return
        if path == "/favorites":
            # kept on both machines: a favourite is copied whatever else is wanted.
            # A programme or a season is its episodes, as on the watchlist.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            held = [str(k) for k in (mine.get("favorites") or [])]
            key = str(body.get("key") or "")
            if key:
                keys = self.spread(key)
                held = [k for k in held if k != key and k not in keys]
                if body.get("on", True):
                    held = keys + held
                mine["favorites"] = held
                write_settings(stored)
            full, part = self.rollup(held)
            self.reply_json({"favorites": held, "on": key in held,
                             "covers": full, "part": part})
            return
        if path == "/collections/for":
            # which shelves hold this title: all of it, some of it, or none
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            self.reply_json({"collections": self.shelves_holding(str(body.get("key") or ""))})
            return
        if path == "/collections/mark":
            # one title on or off one shelf; a programme or a season is its episodes
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            key = str(body.get("key") or "")
            want = str(body.get("id") or "")
            if want.startswith("coll:"):
                want = want[5:]
            self.collections()                     # old marks become a shelf first
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            shelf = next((c for c in (mine.get("collections") or [])
                          if isinstance(c, dict) and str(c.get("id")) == want), None)
            if not key or shelf is None:
                self.reply_json({"error": "there is no such shelf"}, 404)
                return
            self.shelf_mark(shelf, key, bool(body.get("on", True)))
            write_settings(stored)
            self.reply_json({"collections": self.shelves_holding(key)})
            return
        if path == "/stream/buffer":
            self.stream_buffer()
            return
        if path == "/feedback/seen":
            # the owner has the page open: everything up to now has been looked at
            stored = self.settings_file()
            stored["reportsSeen"] = int(time.time())
            write_settings(stored, merge=False)
            self.reply_json({"ok": True, "seen": stored["reportsSeen"]})
            return
        if path == "/feedback":
            # anyone using this server may report a fault or ask for something; it is
            # the one thing a guest is allowed to write here
            # (and if this machine has been told it may, a fault - not a request -
            # goes on to palladium.video, with everything identifying taken out)
            body = self.read_json()
            row = {
                # a stable name for the row, so it can be hidden later; the file is
                # append-only text and positions shift when it is cleared
                "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFFFFF),
                "when": int(time.time()),
                "who": self.guest_name if self.role == "guest" else "you",
                "kind": (body.get("kind") or "problem")[:20],
                "text": (body.get("text") or "").strip()[:2000],
                "from": self.client_address[0],
                "app": (body.get("app") or "")[:40],
            }
            # a stack trace is a fault whoever hands it in: the kind decides where it
            # is shelved, not the client, which could say anything
            row["source"] = "auto" if row["kind"] in ("error", "crash") else "person"
            if row["text"]:
                with open(os.path.join(ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + chr(10))
                # and on to palladium.video, if this machine has been told it may. A
                # fault only - a request is somebody talking to the person who runs
                # this server, and it is theirs.
                if row["source"] == "auto":
                    self.tell_the_site(row)
            self.reply_json({"ok": True})
            return
        if path == "/settings":
            # a guest's body has already been read, to check what it was asking for
            body = getattr(self, "settings_body", None) or self.read_json() or {}
            self.settings_body = None
            key = body.get("key") or ""
            device = self.device_of(body.get("device"))
            # What somebody is called, set by them. A name was something only the
            # person who wrote the invitation could give, so everybody wore whatever
            # they were christened at the moment they were invited - and the owner
            # wore "me", which is what a server calls whoever installed it before
            # anybody has said who that is.
            for name in ("splitPlay", "failover", "dropWatched"):
                if name in body:
                    stored = self.settings_file()
                    self.viewer_settings(stored)[name] = bool(body[name])
                    write_settings(stored, merge=False)
                    self.reply_json({name: bool(body[name])})
                    return
            if "myName" in body:
                called = str(body.get("myName") or "").strip()[:40]
                if not called:
                    self.reply_json({"error": "a name, please"}, 400)
                    return
                # Their own, not the one on the invitation. What the owner wrote
                # when they made the key stays as it was written: it is how the owner
                # knows who they gave it to, and somebody renaming themselves must not
                # quietly rewrite that.
                stored = self.settings_file()
                self.viewer_settings(stored)["myName"] = called
                write_settings(stored, merge=False)
                # the owner has no key to keep a name on, so theirs is the one the
                # server keeps for whoever installed it
                if self.viewer() == "me":
                    write_settings({"ownerName": called})
                self.reply_json({"name": called})
                return
            if key and body.get("reset"):
                self.reply_json({"subtitles": self.clear_override(key, device),
                                 "override": False, "device": device})
                return
            if body.get("useServerDefault"):
                # the owner's settings for this screen, adopted as this viewer's own
                device = self.device_of(body.get("device"))
                stored = self.settings_file()
                theirs = (stored.get("subtitles", {}) or {}).get(device) \
                         or dict(self.SUBTITLE_DEFAULTS[device])
                mine = self.viewer_settings(stored)
                kept = mine.get("subtitles", {}) or {}
                kept[device] = dict(theirs)
                mine["subtitles"] = kept
                write_settings(stored, merge=False)
                self.reply_json({"subtitles": self.subtitle_settings(None, device),
                                 "device": device,
                                 "devices": {d: self.subtitle_settings(None, d)
                                             for d in self.DEVICES}})
                return
            if "accent" in body:
                # "everyone" is the owner setting what a viewer meets before they
                # have chosen; without it a viewer is choosing for themselves
                self.reply_json({
                    "accent": self.set_accent(body["accent"],
                                              bool(body.get("everyone"))),
                    "accentDefault": self.accent_default(),
                })
                return
            if "backdrop" in body:
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                was = mine.get("myBackdrop")
                now = dict(was) if isinstance(was, dict) else {
                    d: self.backdrop_word(was) for d in self.DEVICES}
                which = self.device_of(body.get("device") or self.screen_now())
                now[which] = self.backdrop_word(body["backdrop"])
                mine["myBackdrop"] = now
                write_settings(stored, merge=False)
                self.reply_json({"backdrop": self.backdrop_all(),
                                 "backdropHere": self.backdrop_now()})
                return
            if "language" in body:
                self.reply_json({"language": self.set_viewer_language(body["language"])})
                return
            if "subShift" in body:
                one = body["subShift"] or {}
                said = self.set_sub_shift(one.get("key") or "", one.get("seconds") or 0,
                                          one.get("sub") or "")
                # Moving a subtitle does not vouch for it. A file can be out by a
                # constant amount and still be the wrong translation, or drift, or be
                # cut for an entirely different edit - and nudging it once proves
                # none of that. The verified mark is still earned the two ways it
                # always was: said by hand, or watched far enough to have been read.
                self.reply_json({"subShift": said})
                return
            if "mine" in body:
                # a viewer's own ceiling, kept with the rest of their settings so it
                # follows them from the television to the telephone
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                one = body["mine"] or {}
                mine["quality"] = {"height": max(0, int(one.get("height") or 0)),
                                   "mbit": max(0, int(one.get("mbit") or 0))}
                write_settings(stored)
                self.reply_json({"mine": self.my_quality()})
                return
            if "quality" in body:
                stored = self.settings_file()
                caps = stored.get("quality") or {}
                for where in ("home", "away"):
                    one = (body["quality"] or {}).get(where)
                    if one is None:
                        continue
                    caps[where] = {"height": max(0, int(one.get("height") or 0)),
                                   "mbit": max(0, int(one.get("mbit") or 0))}
                stored["quality"] = caps
                write_settings(stored)
                self.reply_json({"quality": self.quality_caps()})
                return
            if "nextDelay" in body:
                stored = self.settings_file()
                # nought to five: nought is "no pause at all", five is long enough to
                # reach for the remote and stop it
                stored["nextDelay"] = max(0, min(5, int(body["nextDelay"] or 0)))
                write_settings(stored)
                self.reply_json({"nextDelay": stored["nextDelay"]})
                return
            if "clock" in body:
                # 24 hours or 12, for everything that prints a time
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                mine["clock"] = "12" if str(body["clock"]) == "12" else "24"
                write_settings(stored)
                self.reply_json({"clock": mine["clock"]})
                return
            if "burnFor" in body:
                # the owner's: it is their machine and their line being spent
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = str(body["burnFor"] or "").lower()
                if want not in self.BURN_FOR:
                    self.send_error(400, "not one of the three")
                    return
                stored = self.settings_file()
                stored["burnFor"] = want
                stored.pop("burnAway", None)          # replaced by the three-state one
                write_settings(stored, merge=False)
                self.reply_json({"burnFor": want})
                return
            if "betaKey" in body:
                # The key the installer was fetched with. Updates come through the
                # same gate, so a key that is revoked stops both - which is what
                # closing a beta means.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = re.sub(r"[^0-9A-Za-z-]", "", str(body["betaKey"] or "")).upper()
                stored = self.settings_file()
                if want:
                    stored["betaKey"] = want[:16]
                else:
                    stored.pop("betaKey", None)
                write_settings(stored, merge=False)
                self.reply_json({"betaKey": stored.get("betaKey", "")})
                return
            if "ownerName" in body:
                # what the owner is called in the log and the figures
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                stored["ownerName"] = str(body["ownerName"] or "").strip()[:40]
                write_settings(stored, merge=False)
                self.reply_json({"ownerName": stored["ownerName"]})
                return
            if "homeRows" in body:
                # Which shelves the front page carries, and in what order. Named
                # rather than numbered, so a shelf added to the program later is
                # simply absent from an old list and takes its default place.
                want = [str(x)[:40] for x in (body["homeRows"] or [])][:20]
                stored = self.settings_file()
                if body.get("everyone"):
                    # the arrangement anybody meets before they have made one
                    if self.role != "owner":
                        self.send_error(403, "not allowed")
                        return
                    stored["homeRowsDefault"] = want
                else:
                    mine = self.viewer_settings(stored)
                    mine["homeRows"] = want
                write_settings(stored, merge=False)
                self.reply_json({"homeRows": self.home_rows(),
                                 "homeRowsDefault": self.home_default()})
                return
            if "autoScan" in body:
                # the default for anybody who has not chosen for themselves
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                self.reply_json({"autoScan": self.set_auto_scan(body["autoScan"])})
                return
            if "autoSync" in body:
                # whether a subtitle should be put in step with the film by itself,
                # which is this viewer's choice and not the server's
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                mine["autoSync"] = bool(body["autoSync"])
                write_settings(stored)
                self.reply_json({"autoSync": mine["autoSync"]})
                return
            if "autoFetch" in body:
                # whether an episode's subtitle is fetched before it starts. The
                # owner's, because it is the owner's OpenSubtitles allowance being
                # spent and their disk the files land on.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                stored["autoFetch"] = bool(body["autoFetch"])
                write_settings(stored, merge=False)
                self.reply_json({"autoFetch": stored["autoFetch"]})
                return
            if "watchParty" in body:
                # theirs alone: an owner turning it off for everybody is a different
                # thing, and that is what ending the party is for
                stored = self.settings_file()
                self.viewer_settings(stored)["watchParty"] = bool(body["watchParty"])
                write_settings(stored, merge=False)
                self.reply_json({"watchParty": bool(body["watchParty"])})
                return
            if "autoNext" in body:
                stored = self.settings_file()
                stored["autoNext"] = bool(body["autoNext"])
                write_settings(stored, merge=False)
                self.reply_json({"autoNext": stored["autoNext"]})
                return
            if "remoteAdmin" in body:
                # Only from the machine or this network, and only by the owner: turning
                # it off from away would be the last thing that key could do, and
                # turning it on from away is what somebody holding a stolen key wants.
                if self.role != "owner" or not self.at_home():
                    self.send_error(403, "not allowed from outside the house")
                    return
                stored = self.settings_file()
                stored["remoteAdmin"] = bool(body["remoteAdmin"])
                write_settings(stored, merge=False)
                self.reply_json({"remoteAdmin": stored["remoteAdmin"]})
                return
            # volume normalisation on/off, and the target in LUFS
            if "evenVolume" in body or "volumeTarget" in body:
                stored = self.settings_file()
                if "evenVolume" in body:
                    stored["evenVolume"] = bool(body["evenVolume"])
                if "volumeTarget" in body:
                    try:
                        stored["volumeTarget"] = max(-31.0, min(-9.0,
                                                     float(body["volumeTarget"])))
                    except (TypeError, ValueError):
                        pass
                write_settings(stored, merge=False)
                self.reply_json({"evenVolume": bool(stored.get("evenVolume")),
                                 "volumeTarget": stored.get("volumeTarget",
                                                            VOLUME_TARGET)})
                return
            saved = self.save_subtitle_settings(body.get("subtitles", {}), key, device)
            self.reply_json({
                "subtitles": saved, "device": device,
                "override": bool(key) and self.has_override(key, device),
                "devices": {d: self.subtitle_settings(None, d) for d in self.DEVICES},
            })
            return
        if path == "/subs/reset":
            # Put a subtitle back where its own file has it: the plan the server was
            # applying, and whatever was nudged on top of it. What the file says is
            # always recoverable; a correction is not, which is why this is a button
            # somebody presses rather than something that happens by itself.
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            index = int(body.get("index") or 0)
            found = local().file_for(key, int(body.get("mi") or 0)) or {}
            if found.get("file"):
                self.set_sub_fit(self.fit_name(found["file"], index), [])
            self.set_sub_shift(body.get("skey") or ("l" + key), 0.0,
                               body.get("sub") or "")
            self.reply_json({"ok": True, "offset": 0.0, "parts": []})
            return
        if path == "/subs/verify":
            # said by hand rather than earned by watching: the same record either way
            body = self.read_json() or {}
            self.reply_json(self.verify_subtitle(
                body.get("key") or "", body.get("name") or "",
                body.get("language") or "en",
                None if body.get("index") is None else int(body["index"])))
            return
        if path == "/subs/remove":
            body = self.read_json() or {}
            if body.get("index") is not None and int(body["index"]) >= 0:
                # a track inside the video is never cut out of it: the film is left
                # alone and the track is simply not offered any more
                self.reply_json(self.hide_track(body.get("key") or "",
                                                int(body["index"]),
                                                int(body.get("mi") or 0)))
                return
            self.reply_json(self.remove_sidecar(body.get("key") or "",
                                                body.get("name") or "",
                                                int(body.get("mi") or 0)))
            return
        if path == "/subs/hide":
            # out of sight without touching the film: for when there is no room to
            # rewrite it, or no wish to
            body = self.read_json() or {}
            self.reply_json(self.hide_track(body.get("key") or "",
                                            int(body.get("index") or 0),
                                            int(body.get("mi") or 0),
                                            bool(body.get("show"))))
            return
        if path == "/copy/pick":
            # which file of a title a viewer settled on. Nothing is stored for the
            # best copy: that is the default everywhere, and a stored default would
            # outlive the day a better rip is added.
            body = self.read_json() or {}
            self.remember_copy(body.get("key") or "", body.get("file") or "")
            self.reply_json({"ok": True})
            return
        if path == "/subs/pick":
            # which subtitle a viewer settled on for one title: offered first from now
            body = self.read_json() or {}
            self.remember_pick(body.get("key") or "", body.get("name") or "")
            self.reply_json({"ok": True})
            return
        if path == "/subs/get":
            body = self.read_json() or {}
            self.reply_json(self.fetch_subtitle(body.get("key") or "",
                                                body.get("id"),
                                                body.get("language") or "en",
                                                bool(body.get("remember", True)),
                                                body.get("release") or ""))
            return
        if path == "/machine/port":
            # Which port this server answers on. Two Palladiums in one house are one
            # router forwarding two ports: the library on 8765, the cache on 8764.
            # Taken at the next start - a server cannot move while it is answering.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            try:
                want = int(body.get("port") or 0)
            except (TypeError, ValueError):
                want = 0
            if not (1 <= want <= 65535):
                self.reply_json({"error": "A port is a number from 1 to 65535."})
                return
            stored = read_settings()
            if stored is None:
                self.reply_json({"error": "The settings could not be read."})
                return
            stored["port"] = want
            write_settings(stored)
            self.reply_json({"port": want, "now": PORT,
                             "restart": want != PORT})
            return
        if path == "/follow/watched":
            # An evening watched on the machine keeping copies belongs in the same
            # book as the rest: it hands back where each viewing got to, and the
            # later note wins.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            rows = (self.read_json() or {}).get("progress") or []
            self.reply_json({"taken": local().take_progress(rows[:200])})
            return
        if path == "/follow/fetched":
            # A machine that keeps copies saying what it fetched for itself while this
            # one was off. The films are not asked for here - only named. What to do
            # about them is decided on a thread of its own, because the answer is
            # several gigabytes long and nothing should wait for it.
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            items = body.get("items")
            # the address it answers on, as this machine already knows it, matched to
            # where the request came from - not whatever the request claims to be
            here = self.client_address[0]
            where = ""
            for known in list(Handler.FOLLOWERS):
                # the host out of "http://address:port" without urllib: this method
                # imports it further down, which makes the name local to the whole of
                # it and unusable here
                host = str(known).split("//")[-1].split(":")[0].split("/")[0]
                if host == here:
                    where = str(known).rstrip("/")
                    break
            if not isinstance(items, list) or not where:
                self.reply_json({"taking": 0})
                return
            fresh = []
            with Handler.FROM_COPY_LOCK:
                known = {(str(x.get("hash")), int(x.get("index") or -1))
                         for x in Handler.FROM_COPY}
                for one in items[:200]:
                    if not isinstance(one, dict):
                        continue
                    mark = (str(one.get("hash") or ""), int(one.get("index") or -1))
                    if not mark[0] or mark[1] < 0 or mark in known:
                        continue
                    known.add(mark)
                    fresh.append(dict(one, where=where))
                Handler.FROM_COPY += fresh
            if fresh:
                Handler.take_from_the_copy()
            self.reply_json({"taking": len(fresh)})
            return
        if path == "/follow/round":
            # shuffle rounds the cache moved while this machine was off: per shelf,
            # the newer round is taken whole
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            who = str(body.get("who") or "")
            theirs = body.get("shuffles")
            if not who or not isinstance(theirs, dict):
                self.reply_json({"took": [], "why": "nothing to take"})
                return
            stored = self.settings_file()
            here = (stored.setdefault("users", {}).setdefault(who, {})
                    if who != "me" else stored)
            rounds = here.get("shuffles")
            if not isinstance(rounds, dict):
                rounds = {}
            took = []
            for cid, one in theirs.items():
                if (isinstance(one, dict)
                        and self.round_stamp(one) > self.round_stamp(rounds.get(str(cid)))):
                    rounds[str(cid)] = one
                    took.append(str(cid))
            if took:
                here["shuffles"] = rounds
                write_settings(stored)
            self.reply_json({"took": took})
            return
        if path == "/follow/viewers":
            # What each viewer keeps, for the machine that copies this library.
            #
            # A watchlist, the shelves somebody arranged, the collections they made:
            # these live beside the library, and a copy held none of them. So the one
            # moment a viewer needs the other machine - this one being off - is the
            # moment their own list is empty and the shelves are somebody's defaults.
            # Nothing about anybody's password or key goes over; only what they would
            # see on a screen.
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            stored = read_settings() or {}
            # the shuffle rounds travel too, so a phone on the cache carries on the round
            keep = ("watchlist", "favorites", "collections", "homeRows", "skin", "myAccent",
                    "subLang", "subtitles", "perTitle", "quality",
                    "shuffles", "casualMoved")
            out = {}
            con = local().lib.db()
            try:
                for who, one in (stored.get("users") or {}).items():
                    mine = {k: v for k, v in (one or {}).items() if k in keep}
                    if mine:
                        mine.update(local().round_in_words(con, one or {}))
                        out[who] = mine
            finally:
                con.close()
            # and the owner's own, under the key they watch by, so the person who
            # owns the library is not the one viewer it forgets
            owner = str(stored.get("ownerIs") or "")
            if owner and owner not in out:
                mine = {k: v for k, v in stored.items() if k in keep}
                if mine:
                    out[owner] = mine
            self.reply_json({"viewers": out,
                             "ownerIs": owner, "ownerName": stored.get("ownerName")})
            return
        if path == "/follow/whatis":
            # What this library calls these files. The machine keeping copies knows
            # them by name and nothing else - it was copying before it started
            # writing down what each one is - and a file it cannot name is a film on
            # its disk that nobody is told about.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            names = [str(n) for n in ((self.read_json() or {}).get("names") or [])][:400]
            found, facts, items, titles = {}, {}, {}, {}
            if names:
                con = local().lib.db()
                try:
                    wanted = {n.lower(): n for n in names}
                    for row in con.execute(
                            """SELECT f.path, f.item_id, f.episode_id, f.duration,
                                      f.container, f.vcodec, f.acodec, f.width,
                                      f.height, f.channels, f.bitrate,
                                      e.aired AS aired,
                                      i.title AS title, i.year AS year,
                                      i.sort_title AS sort_title,
                                      i.genres AS genres
                               FROM file f
                               LEFT JOIN episode e ON e.id = f.episode_id
                               LEFT JOIN item i ON i.id = f.item_id"""):
                        low = os.path.basename(row["path"] or "").lower()
                        if low not in wanted:
                            continue
                        name = wanted[low]
                        found[name] = (str(row["episode_id"]) if row["episode_id"]
                                       else str(row["item_id"]))
                        items[name] = str(row["item_id"])      # the title, for a copy to file under
                        # and this server's title for it. A follower names titles
                        # from file names, so any title with punctuation differed and
                        # lookups by name between the two returned nothing.
                        # and what it is about. A copy names its titles off file
                        # names and never learns a category, so every collection made
                        # by category was empty there - which is the one place they
                        # are read, the evening this machine is off.
                        titles[name] = {"title": row["title"], "year": row["year"],
                                        "sort": row["sort_title"],
                                        "genres": row["genres"]}
                        # and what was measured when it arrived here, so the cache
                        # does not have to open the file to know what is in it
                        facts[name] = {k: row[k] for k in
                                       ("duration", "container", "vcodec", "acodec",
                                        "width", "height", "channels", "bitrate")}
                        # and when the episode went out. A copy looks its titles up
                        # for itself, but nothing tells it the air date of an episode
                        # it holds - so the shelf of recently released episodes was
                        # empty on the machine holding the newest of them.
                        if row["aired"]:
                            facts[name]["aired"] = row["aired"]
                finally:
                    con.close()
            self.reply_json({"keys": found, "facts": facts, "items": items,
                             "titles": titles})
            return
        if path == "/follow/holding":
            # What the following server actually has, by this library's own numbers.
            # Not what it was asked to keep: a copy that failed half way is not a
            # copy, and a poster that says otherwise is a promise nobody can keep.
            if not self.follows_here():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            keys = [str(k) for k in (body.get("keys") or [])][:2000]
            # and what would not come, so the queue can say so. A copy that sets a
            # file aside after it fails carries on down the list, and the page had no
            # way of knowing - it showed rows waiting while something below them was
            # being fetched, which reads as the queue being ignored.
            bad = body.get("trouble")
            if isinstance(bad, list):
                Handler.FOLLOWER_TROUBLE = [
                    {"name": str(r.get("name") or "")[:120],
                     "why": str(r.get("why") or "")[:160],
                     "key": str(r.get("key") or ""),
                     "when": int(r.get("when") or time.time())}
                    for r in bad[:40] if isinstance(r, dict)]
            # The whole of what it holds replaces; a part of it is added to. Both
            # were added to, because there was no way to tell them apart - so a film
            # swept out of the cache's cache stayed on this list for good, the dot
            # stayed on its poster, and with this machine off somebody chose it and
            # there was nothing at the other end.
            if body.get("whole"):
                Handler.remember_copies(sorted(set(keys)))
            else:
                Handler.remember_copies(sorted(set(Handler.COPIES.get("keys") or [])
                                               | set(keys)))
            self.note_cache(kept=len(keys),
                               bad=len(Handler.FOLLOWER_TROUBLE))
            # and what this library allows that machine to use, in gigabytes. Lending
            # somebody a copy of a library is not lending them the whole disk, and the
            # setting that says so belongs here rather than on their machine. Zero
            # means their own setting stands.
            # A ceiling belongs to the key, the way an invitation for a person
            # carries what that person may do. One number for every machine meant
            # lending a second machine a copy re-lent the first one's allowance, and
            # taking it away from one took it from all of them. The library's old
            # single figure stands for keys made before this.
            mine = INVITES.check(self.bearer(), self.app_name()) or {}
            cap = mine.get("cap")
            # when the invitations last changed. The cache takes them a quarter of an
            # hour at a time, which is a long while to wait after handing somebody a
            # key - so this says whether there is anything new to take, and the cache
            # comes back for them at once rather than on its own slow clock.
            try:
                changed = int(os.path.getmtime(INVITES.path))
            except OSError:
                changed = 0
            if cap is None:
                cap = local().lib.config().get("cacheCap") or 0
            self.reply_json({"kept": len(keys), "cap": float(cap or 0),
                             # and what it is allowed to do here
                             "mayCopy": bool(mine.get("mayCopy", True)),
                             "invitesAt": changed})
            return
        if path == "/follow/key":
            # A key for another Palladium, not for a person: it may read the library,
            # take copies of the files, and ask what the main server is watching. Revoked
            # like any other invitation.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            asked = self.read_json() or {}
            rows = INVITES.load()
            mine = next((r for r in rows if r.get("follows")), None)
            if asked.get("remove"):
                # Taking it away rather than replacing it. A new key stops the old
                # one but leaves a key on the table; this leaves none, and nothing
                # may read this library or take copies of it until one is made.
                #
                # One machine's key when one is named, every follow key when none is:
                # revoking used to take the key away from every machine that had one,
                # which is not what "stop this one" means.
                only = str(asked.get("only") or "").strip()
                if only:
                    INVITES.save([r for r in rows if not (
                        r.get("follows") and r.get("token") == only)])
                else:
                    INVITES.save([r for r in rows if not r.get("follows")])
                gone = Handler.STANDBY.get("where") or ""
                Handler.STANDBY.clear()
                cfg = local().lib.config()
                cfg["cache"] = {}
                local().lib.save_config(cfg)
                if gone and gone in Handler.FOLLOWERS:
                    # kept in the record, marked for what it is: a machine that used
                    # to copy from here is worth remembering after the key is gone
                    Handler.FOLLOWERS[gone]["revoked"] = int(time.time())
                    Handler.remember_followers(force=True)
                self.reply_json({"key": "", "where": ""})
                return
            # A key per machine. There used to be one for all of them - a new key
            # took the old one away from every cache at once, and stopping one
            # machine stopped the lot - so a key is made for each, named after the
            # machine it was made for, and revoked on its own.
            # What this key allows, set when it is made or changed later: a
            # ceiling in gigabytes, nought for as much as that machine allows itself,
            # and whether it may take copies at all or only read.
            forWhom = str(asked.get("for") or "").strip()
            if asked.get("set"):
                token = str(asked.get("set") or "")
                for row in rows:
                    if row.get("token") != token or not row.get("follows"):
                        continue
                    if "cap" in asked:
                        try:
                            row["cap"] = max(0.0, float(asked.get("cap") or 0))
                        except (TypeError, ValueError):
                            row["cap"] = 0.0
                    if "mayCopy" in asked:
                        row["mayCopy"] = bool(asked.get("mayCopy"))
                    INVITES.save(rows)
                    self.reply_json({"ok": True})
                    return
                self.send_error(404, "no such key")
                return
            if forWhom:
                mine = next((r for r in rows if r.get("follows")
                             and str(r.get("name") or "") == forWhom), None)
            if not mine or asked.get("again"):
                mine = INVITES.create(forWhom or "a following server", 0, "")
                rows = INVITES.load()
                for row in rows:
                    if row["token"] == mine["token"]:
                        row["follows"] = True
                    elif not forWhom:
                        # no machine named: the old behaviour, one key for the main server,
                        # and a new one stops whatever was there
                        row.pop("follows", None)
                    elif str(row.get("name") or "") == forWhom:
                        # the one this machine was using before: replaced, not kept -
                        # and every other machine keeps the key it has
                        row.pop("follows", None)
                INVITES.save(rows)
                mine = next(r for r in INVITES.load()
                            if r["token"] == mine["token"])
            self.reply_json({"key": mine["token"], "name": mine.get("name") or "",
                             "where": "http://%s:%d" % (LAN_IP, PORT)})
            return
        if path == "/app/fetch":
            # Take a copy of the app so this server can hand it to a phone or a
            # television. Owner only, and only when pressed: it is the one request
            # this program makes to palladium.video that nobody asked for otherwise.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            stored = read_settings() or {}
            if body.get("off") or body.get("on"):
                # handed out or not. The file stays either way: this is whether the
                # server offers it, not whether it has it.
                stored["appOff"] = bool(body.get("off"))
                write_settings(stored)
                self.reply_json({"off": bool(body.get("off")),
                                 "have": bool(self.app_beside_us())})
                return
            if self.app_beside_us() and not body.get("overwrite"):
                self.reply_json({"ask": True, "have": True,
                                 "version": self.app_version(),
                                 "where": Handler.APP_FROM})
                return
            self.fetch_app(force=True)
            self.reply_json({"getting": True})
            return
        if path == "/follow/clear":
            # What clearing the cache would delete, and doing it. Read before it is
            # done and before automatic clearing is switched on: "what will this do"
            # has to be answerable before it is answered by it happening.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            folder = (one.get("folder") or "").strip()
            cap = int(pd_follow.cap_now(one) * (1000 ** 3))
            how = str(one.get("deleteBy") or "oldest")
            keeping = set(pd_follow.KEEPING.get("set") or ())
            body = self.read_json() or {}
            said = pd_follow.could_go(folder, keeping, cap, how)
            said["cap"] = round(cap / 1e9, 2)
            # nothing has been copied yet this run, so nothing is known to be worth
            # keeping - and a clear that does not know that would take tonight's
            said["ready"] = bool(pd_follow.KEEPING.get("when"))
            if body.get("now") and said["sane"] and said["ready"]:
                pd_follow.make_room(folder, cap, keeping, how)
                said["cleared"] = said["gb"]
                after = pd_follow.could_go(folder, keeping, cap, how)
                said.update({"files": after["files"], "gb": after["gb"],
                             "rows": after["rows"], "used": after["used"]})
            self.reply_json(said)
            return
        if path == "/follow/claim":
            # Once, by hand: everything already in the cache folder is this
            # machine's own. The only way back for copies made before there was a
            # record of them - and it takes films and subtitles, nothing else.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            folder = (one.get("folder") or "").strip()
            if not (self.read_json() or {}).get("confirm"):
                self.reply_json({"ok": False, "why": "not confirmed"})
                return
            said = pd_follow.claim_folder(folder)
            said["ok"] = bool(said.get("taken"))
            said["folder"] = folder
            self.reply_json(said)
            return
        if path == "/follow/extras":
            # What is in the cache folder that this machine did not fetch. It is
            # never deleted by the program - only what it wrote is - so this is the
            # only way any of it goes: somebody reading the list and saying so.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            folder = (one.get("folder") or "").strip()
            # read only. A file this machine did not fetch is not its to delete,
            # whoever asks: the folder is a path typed by hand, and the whole point
            # of the list is that these are the files nothing here can account for.
            said = {}
            found = pd_follow.extras(folder)
            said.update({"folder": folder,
                         "sane": pd_follow.a_sane_folder(folder),
                         "extras": found[:200],
                         "count": len(found),
                         "gb": round(sum(f["bytes"] for f in found) / 1e9, 2)})
            self.reply_json(said)
            return
        if path in ("/proxy/fetch", "/proxy/config", "/proxy/run", "/proxy/stop",
                    "/proxy/login"):
            # the way in from outside is the owner's arrangement with their own router
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_proxy
            body = self.read_json() or {}
            if path == "/proxy/fetch":
                self.reply_json(pd_proxy.fetch())
            elif path == "/proxy/config":
                self.reply_json(pd_proxy.set_config(body))
            elif path == "/proxy/login":
                self.reply_json(pd_proxy.set_at_login(bool(body.get("on"))))
            elif path == "/proxy/run":
                said = pd_proxy.run()
                said.update(pd_proxy.state())
                self.reply_json(said)
            else:
                said = pd_proxy.stop()
                said.update(pd_proxy.state())
                self.reply_json(said)
            return
        if path in ("/torrents/config", "/torrents/add", "/torrents/remove"):
            # the owner's: how qBittorrent is reached, and which packs are offered
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import base64
            import pd_torrents
            body = self.read_json() or {}
            if path == "/torrents/config":
                self.reply_json(pd_torrents.set_config(body))
            elif path == "/torrents/add":
                raw = None
                if body.get("data"):
                    try:
                        raw = base64.b64decode(str(body["data"]).split(",")[-1])
                    except (ValueError, TypeError):
                        raw = None
                given = str(body.get("path") or "").strip().strip('"')
                if given and not os.path.isfile(given):
                    self.reply_json({"ok": False, "why": "No such file: " + given})
                    return
                self.reply_json(pd_torrents.add_pack(raw=raw, path=given or None))
            else:
                self.reply_json(pd_torrents.remove_pack(str(body.get("hash") or "")))
            return
        if path == "/torrents/get":
            # one film from a pack, for whoever asks, within their week's download limit
            import pd_torrents
            body = self.read_json() or {}
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            self.reply_json(pd_torrents.request(str(body.get("key") or ""), token,
                                                self.watcher(), cap))
            return
        if path == "/torrents/cancel":
            # a download stopped: by the owner, or by whoever asked for it
            import pd_torrents
            body = self.read_json() or {}
            self.reply_json(pd_torrents.cancel(str(body.get("key") or ""), self.bearer() or "me",
                                               self.role == "owner"))
            return
        if path == "/follow/managed":
            # A cache's copying settings, read and set from here: only a machine
            # that follows this one, at the address it announced, and only paths about
            # its copying. The cache refuses unless it allows the main server to.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import urllib.request as _ureq
            import urllib.error as _uerr
            body = self.read_json() or {}
            there = str(body.get("where") or "").rstrip("/")
            known = Handler.FOLLOWERS.get(there) or {}
            asked = str(body.get("path") or "")
            method = "POST" if body.get("method") == "POST" else "GET"
            allowed = {("GET", "/follow"), ("POST", "/follow"), ("POST", "/follow/clear"),
                       ("POST", "/follow/extras"), ("POST", "/follow/claim"),
                       ("GET", "/follow/test"), ("GET", "/follow/now")}
            if not known or (method, asked.split("?", 1)[0]) not in allowed:
                self.reply_json({"error": "Not a machine following this server, "
                                          "or not one of its copying settings."})
                return
            called = known.get("name") or there
            req = _ureq.Request(
                there + asked, method=method,
                data=(json.dumps(body.get("body") or {}).encode("utf-8")
                      if method == "POST" else None),
                headers={"X-Palladium-House": "1", "Content-Type": "application/json"})
            try:
                with _ureq.urlopen(req, timeout=60) as answer:
                    self.reply_json(json.loads(
                        answer.read().decode("utf-8", "replace") or "{}"))
            except _uerr.HTTPError as e:
                self.reply_json({"error": "%s refused (%d)%s" % (
                    called, e.code, ": Managed from the main server is off there"
                    if e.code in (401, 403) else "")})
            except Exception as e:
                self.reply_json({"error": "%s did not answer: %s" % (called, str(e)[:100])})
            return
        if path == "/follow/tonight":
            # Take tonight's copies now. This machine cannot tell the other one
            # anything - it is the other one that asks - so the answer is left here
            # and handed over the next time it does, which is within the minute.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            Handler.EARLY_UNTIL = time.time() + 10 * 3600
            self.reply_json({"ok": True,
                             "said": "The other machine takes tonight's copies from "
                                     "its next round, within a minute."})
            return
        if path == "/follow/stop":
            # Stop one machine that follows this one, or let it back in. The key is
            # untouched: this is about a machine, not about the key it holds, and
            # rotating the key would stop every other cache with it.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            where = str(body.get("where") or "").strip().rstrip("/")
            cfg = local().lib.config()
            blocked = [str(x) for x in (cfg.get("blockedCaches") or [])]
            if where:
                if body.get("allow"):
                    blocked = [x for x in blocked if x.rstrip("/") != where]
                elif where not in blocked:
                    blocked.append(where)
            cfg["blockedCaches"] = blocked
            local().lib.save_config(cfg)
            self.reply_json({"blocked": blocked})
            return
        if path == "/follow/now":
            # Build the list again and take what is missing, now: what is worth
            # keeping changed the moment somebody finished an episode.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            early = (args.get("early") or [""])[0] in ("1", "true", "yes")
            self.reply_json(pd_follow.sync_now(lambda: local().lib.config(),
                                               local().lib, local(), early=early))
            return
        if path == "/follow":
            # this server following another one
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            body = self.read_json() or {}
            cfg = local().lib.config()
            one = pd_follow.settings(cfg)
            for name in ("on", "updateWith", "wholeList", "coverNight",
                         "listByDay", "allowRemote"):
                if name in body:
                    one[name] = bool(body[name])
            for name in ("master", "key", "folder", "outside"):
                if name in body:
                    one[name] = str(body[name]).strip()
            # what goes first when there is no room left: the one nobody has touched
            # for longest, or the one that frees the most in a single deletion
            if body.get("deleteBy") in ("oldest", "largest"):
                one["deleteBy"] = body["deleteBy"]
            for name in ("hours", "cap", "casualHours"):
                if name in body:
                    try:
                        one[name] = max(0, float(body[name]))
                    except (TypeError, ValueError):
                        pass
            if "episodes" in body:
                try:
                    one["episodes"] = max(0, min(int(body["episodes"]), 40))
                except (TypeError, ValueError):
                    pass
            # per-kind mode: off, night, or always. Sent one at a time so a page
            # cannot overwrite a setting it was not displaying.
            if isinstance(body.get("kinds"), dict):
                import pd_follow as _f
                kinds = dict(one.get("kinds") or {})
                for kind, mode in body["kinds"].items():
                    if kind in _f.KINDS and str(mode) in ("off", "night", "always"):
                        kinds[str(kind)] = str(mode)
                one["kinds"] = kinds
            # The hours this machine is the one awake. The page has always offered
            # them and the server has always dropped them, so an owner who typed 21
            # was left with 22 and nothing on screen to say otherwise.
            for name in ("nightFrom", "nightTo"):
                if name in body:
                    try:
                        one[name] = int(body[name]) % 24
                    except (TypeError, ValueError):
                        pass
            # whose evening this cache is for, and whether the cap may delete on its
            # own. Both are set from the page and neither was ever written down: a
            # setting the page can change and the server drops is a switch that
            # moves and does nothing.
            if body.get("cacheFor") in ("user", "server"):
                one["cacheFor"] = body["cacheFor"]
            if "cacheWho" in body:
                one["cacheWho"] = str(body["cacheWho"]).strip()[:60]
            if body.get("clearBy") in ("manual", "auto"):
                one["clearBy"] = body["clearBy"]
            cfg["follow"] = one
            # A folder of copies nothing scans is a folder of copies nobody can
            # watch: the cache joins the library as a mixed folder, films and
            # episodes together, which is how they arrive.
            if one.get("folder"):
                mixed = [f for f in (cfg.get("mixed") or [])]
                here = os.path.normcase(os.path.abspath(one["folder"]))
                known = any(os.path.normcase(os.path.abspath(f)) == here
                            for f in (list(mixed) + list(cfg.get("movies") or [])
                                      + list(cfg.get("tv") or [])))
                if not known:
                    mixed.append(one["folder"])
                    cfg["mixed"] = mixed
            local().lib.save_config(cfg)
            # what is being read this minute, so the sweep leaves it alone
            pd_follow.BUSY = WATCHING.paths
            pd_follow.start(lambda: local().lib.config(), local().lib,
                            lambda: Handler.game_holds("copies"),
                            (PORT, socket.gethostname(), STATIC,
                             Handler.build_version(), settings_path()),
                            learn_invites, local(), carry_the_keys)
            self.reply_json({"follow": one, "state": pd_follow.look()})
            return
        if path == "/performance":
            # Game mode: the machine is wanted for something else. Nothing that can
            # wait is started, and what is running that can be stopped is stopped.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            if "game" in body or "does" in body:
                stored = read_settings() or {}
                game = self.game_mode()
                for name in self.GAME_DOES:
                    if name in (body.get("does") or {}):
                        game[name] = bool(body["does"][name])
                if "game" in body:
                    game["on"] = bool(body["game"])
                stored["gameMode"] = game
                write_settings(stored)
                if game["on"] and game.get("subs"):
                    try:
                        import pd_ai_subs
                        pd_ai_subs.stop()      # the card is wanted elsewhere
                    except Exception:
                        pass
            self.reply_json(self.how_busy())
            return
        if path == "/machine/addons":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_ai_subs
            os.environ["PALLADIUM_DATA"] = ROOT
            body = self.read_json() or {}
            which = str(body.get("id") or "")
            if "on" in body:
                stored = read_settings() or {}
                switched = dict(stored.get("addons") or {})
                switched[which] = bool(body["on"])
                stored["addons"] = switched
                write_settings(stored)
            if body.get("tools") in ("off", "on"):
                # let go of, or taken up again. The scripts stay on the disk: this
                # is whether this server runs them, not whether it has them.
                stored = read_settings() or {}
                stored["toolsOff"] = body["tools"] == "off"
                write_settings(stored)
            if body.get("tools") == "fetch":
                # over a copy that is already here only when asked twice
                have = (pd_ai_subs.tools() or {}).get("version")
                if have and not body.get("overwrite"):
                    self.reply_json({"ask": True, "have": have,
                                     "where": "palladium.video"})
                    return
                said = pd_ai_subs.get_tools()
                if said.get("error"):
                    self.reply_json(said, 409)
                    return
            elif body.get("fetch"):
                said = pd_ai_subs.get_addon(which)
                if said.get("error"):
                    self.reply_json(said, 409)
                    return
            if body.get("remove"):
                pd_ai_subs.drop_addon(which)
            stored = read_settings() or {}
            self.reply_json({"addons": pd_ai_subs.addons(stored.get("addons") or {}),
                             "can": pd_ai_subs.ready() and tools_on(),
                             "toolsOff": bool(stored.get("toolsOff")),
                             "tools": pd_ai_subs.tools()})
            return
        if path == "/subs/make":
            # Hear the film and write down what is said. The file lands beside the
            # video with "ai-gen" in its name, so nobody mistakes it for one a person
            # wrote and published.
            import pd_ai_subs
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            want = str(body.get("language") or "en").lower()[:2]
            src = local().file_for(key, int(body.get("mi") or 0))
            if not src:
                self.reply_json({"error": "no file for that title"}, 404)
                return
            if not (pd_ai_subs.ready() and tools_on()):
                # This machine cannot hear a film - the speech model is three
                # gigabytes and the machine that keeps copies has no card to run it
                # on. The one it copies from has both, and the subtitle written there
                # arrives here on the next pass like any other file beside a video.
                handed = self.hand_subs_over(key, want)
                if handed is not None:
                    self.reply_json(handed)
                    return
                self.reply_json({"error": "This server has no speech model installed."},
                                409)
                return
            if self.game_holds("subs"):
                self.reply_json({"error": "Game mode is on: the graphics card is "
                                          "wanted for something else."}, 409)
                return
            switched = (read_settings() or {}).get("addons") or {}
            if not switched.get("speech", True):
                self.reply_json({"error": "Speech to text is turned off in Settings, "
                                          "This computer."}, 409)
                return
            if want == "sv" and not switched.get("translate", True):
                self.reply_json({"error": "Translation is turned off in Settings, "
                                          "This computer."}, 409)
                return
            os.environ["PALLADIUM_DATA"] = ROOT      # where the model is kept
            # every copy of this film: one listening, and the answer written beside
            # each of them, because which copy plays is the library's decision
            copies = []
            con = local().lib.db()
            try:
                where = ("episode_id=?" if str(key).startswith("e")
                         else "item_id=? AND episode_id IS NULL")
                # the key as it is stored: an episode's with its e, a title's as hex.
                # This took the number out of it, which a hex key has none of - the
                # ValueError was swallowed below and no copy was ever found to listen to.
                number = str(key)
                for row in con.execute("SELECT path FROM file WHERE " + where, (number,)):
                    if os.path.exists(row["path"]):
                        copies.append(row["path"])
            except (TypeError, ValueError):
                pass
            finally:
                con.close()
            self.reply_json(pd_ai_subs.ask(src["file"], want,
                                           local().now_playing_fields(key)[0] or "",
                                           copies, key))
            return
        if path == "/subs/stop":
            import pd_ai_subs
            body = self.read_json() or {}
            self.reply_json(pd_ai_subs.stop(str(body.get("out") or "")))
            return
        if path == "/subs/keepalive":
            self.reply_json({"outcome": subtitle_keepalive()})
            return
        if path == "/subs/auto":
            body = self.read_json() or {}
            self.reply_json(self.auto_subtitle(body.get("key") or "",
                                               body.get("after") or ""))
            return
        if path == "/network/check":
            ok, nodes, note = reachable_from_outside(PORT)
            self.reply_json({"ok": ok, "address": "%s:%d" % (wan_ip(), PORT),
                             "nodes": nodes, "message": note})
            return
        if path == "/network/open":
            # asking the router to forward the port is an outward-facing thing, so it
            # happens on a press rather than at startup
            import pd_upnp as upnp
            ok, message = upnp.forward(LAN_IP, PORT)
            if not ok:
                message += (" Forward TCP %d to %s in the router at %s, and the links "
                            "will work." % (PORT, LAN_IP, gateway()))
            self.reply_json({"ok": ok, "message": message})
            return
        if path == "/feedback/delete":
            ident = (self.read_json() or {}).get("id") or ""
            self.file_away(lambda row: row.get("id") == ident)
            self.reply_json({"reports": self.reports()})
            return
        if path == "/feedback/restore":
            # a tick pressed by mistake, or a row cleared too soon
            self.unfile((self.read_json() or {}).get("id") or "")
            self.reply_json({"reports": self.reports(), "filed": self.filed()})
            return
        if path == "/feedback/cancel":
            # Anybody may take back what they wrote, and nobody else's. A guest is
            # known by the name on their invitation, which is what was written down
            # with the report; the owner's rows say "you".
            body = self.read_json() or {}
            ident = str(body.get("id") or "")
            mine = "you" if self.role == "owner" else (self.guest_name or "")
            row = next((r for r in self.reports() if r.get("id") == ident), None)
            if not row:
                self.reply_json({"error": "no such report"}, 404)
                return
            if (row.get("who") or "") != mine:
                self.refused(path, "that report belongs to somebody else")
                self.reply_json({"error": "that one is not yours"}, 403)
                return
            self.amend_report(ident, {"done": True, "doneWhen": int(time.time()),
                                      "withdrawn": True,
                                      "solution": "Withdrawn by " + mine})
            self.reply_json({"reports": self.reports()})
            return
        if path == "/follow/keys":
            # Hand this house's catalogue keys to the machine that keeps copies.
            #
            # It fetches subtitles for the films it takes and looks their posters up
            # for itself when the main server cannot be reached; without keys of its own it
            # holds films half the main server cannot read and a shelf of grey rectangles.
            # Sent rather than copied by hand because the address and the invitation
            # are already here. The owner's, and only to this house's own cache.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            one = self.standby_now()
            if not one.get("where"):
                self.reply_json({"ok": False, "why": "nothing follows this server"})
                return
            cfg = local().lib.config()
            #: which of them to send: named, so a page can offer one button or two
            want = [k for k in ("opensubtitles_key", "tmdb_key")
                    if not body.get("only") or body.get("only") == k]
            # and the login that goes with the subtitle key. A key searches; only a
            # login downloads, and it carries the daily allowance. Sending one without
            # the other gave the other machine a list of subtitles it could see and
            # could not fetch, which is the same as no subtitles at all.
            if "opensubtitles_key" in want:
                want += ["opensubtitles_user", "opensubtitles_pass"]
            send = {k: (cfg.get(k) or "").strip() for k in want}
            send = {k: v for k, v in send.items() if v}
            if not send:
                self.reply_json({"ok": False, "why": "no key here to send"})
                return
            token = ""
            for row in INVITES.load():
                if row.get("follows") and row.get("token"):
                    token = row["token"]
                    break
            if not token:
                self.reply_json({"ok": False, "why": "that machine has no key here"})
                return
            try:
                req = urllib.request.Request(
                    one["where"].rstrip("/") + "/library/config?t=" +
                    urllib.parse.quote(token),
                    data=json.dumps(send).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as answer:
                    answer.read()
                # written down: which of this house's catalogue keys that machine
                # has been given, so nobody has to remember whether they sent them
                self.note_cache(sent=dict(
                    (Handler.FOLLOWERS.get(Handler.STANDBY.get("where") or "") or {})
                    .get("sent") or {},
                    **{k: int(time.time()) for k in send}))
                self.reply_json({"ok": True, "sent": sorted(send)})
            except Exception as e:
                self.reply_json({"ok": False, "why": str(e)[:120]})
            return
        if path == "/follow/subkey":
            # Hand the subtitle key to the machine that keeps copies.
            #
            # It fetches subtitles for the films it takes, and without a key of its
            # own it takes films half the main server cannot read. Sent rather than copied
            # by hand because the address and the invitation are already here; only
            # the owner may, and only to this house's own cache.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            one = self.standby_now()
            key = (local().lib.config().get("opensubtitles_key") or "").strip()
            if not one.get("where"):
                self.reply_json({"ok": False, "why": "nothing follows this server"})
                return
            if not key:
                self.reply_json({"ok": False, "why": "no key to send"})
                return
            token = ""
            for row in INVITES.load():
                if row.get("follows") and row.get("token"):
                    token = row["token"]
                    break
            if not token:
                self.reply_json({"ok": False, "why": "that machine has no key here"})
                return
            try:
                body = json.dumps({
                    "opensubtitles_key": key,
                    # the login too: a key alone can search and cannot download
                    "opensubtitles_user": (local().lib.config()
                                           .get("opensubtitles_user") or "").strip(),
                    "opensubtitles_pass": (local().lib.config()
                                           .get("opensubtitles_pass") or "").strip(),
                }).encode()
                req = urllib.request.Request(
                    one["where"].rstrip("/") + "/library/config?t=" +
                    urllib.parse.quote(token),
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as answer:
                    answer.read()
                self.reply_json({"ok": True})
            except Exception as e:
                self.reply_json({"ok": False, "why": str(e)[:120]})
            return
        if path == "/skins":
            # a look is this viewer's own business, guest or owner
            import pd_skins
            body = self.read_json() or {}
            stored = read_settings() or {}
            mine = self.viewer_settings(stored)
            mine["skin"] = pd_skins.known(body.get("skin"))
            write_settings(stored, merge=False)
            self.reply_json({"chosen": mine["skin"]})
            return
        if path == "/feedback/send":
            # One report, handed over by hand. This is how a fault reaches
            # palladium.video on a machine that sends nothing of its own accord: the
            # owner reads it, decides, and presses send - which is a different act
            # from a machine reporting on itself, and is allowed either way.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            ident = str((self.read_json() or {}).get("id") or "")
            row = next((r for r in self.reports() if r.get("id") == ident), None)
            if not row:
                self.reply_json({"ok": False, "why": "no such report"}, 404)
                return
            sent, why = self.tell_the_site(row, forced=True)
            self.reply_json({"ok": bool(sent),
                             "why": "" if sent else (why or "could not be sent")})
            return
        if path == "/feedback/sendall":
            # The backlog. A machine told to send faults that had no way out keeps
            # them, and they are worth nothing sitting here. Oldest first, one a
            # second, and each row marked with what came of it.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            waiting = [r for r in self.reports()
                       if (r.get("source") == "auto"
                           or r.get("kind") in ("error", "crash"))
                       and not r.get("sentAway") and not r.get("hidden")]
            waiting.sort(key=lambda r: r.get("when") or 0)
            if SENDING["busy"]:
                self.reply_json({"ok": True, "already": True, "left": SENDING["left"]})
                return
            if not waiting:
                self.reply_json({"ok": True, "queued": 0})
                return
            SENDING.update(busy=True, left=len(waiting), sent=0, failed=0, why="")

            def hand_over(rows, me):
                try:
                    for one in rows:
                        ok, why = me.tell_the_site(one, forced=True)
                        SENDING["left"] = max(0, SENDING["left"] - 1)
                        if ok:
                            SENDING["sent"] += 1
                        else:
                            SENDING["failed"] += 1
                            SENDING["why"] = str(why or "")[:160]
                        time.sleep(1.0)
                finally:
                    SENDING["busy"] = False

            threading.Thread(target=hand_over, args=(waiting, self),
                             daemon=True).start()
            self.reply_json({"ok": True, "queued": len(waiting)})
            return
        if path == "/feedback/fix":
            # ticking one off, and the two sentences worth keeping: what caused it and
            # what was done. The owner writes these - it is a statement about the
            # machinery, not about how annoying the fault was.
            body = self.read_json()
            changes = {}
            if "done" in body:
                changes["done"] = bool(body["done"])
                changes["doneWhen"] = int(time.time()) if body["done"] else 0
            for field in ("reason", "solution"):
                if field in body:
                    changes[field] = (body.get(field) or "").strip()[:500]
            # and the wording itself, which the owner may tidy: a request typed on a
            # television remote arrives misspelt, and one typed on a phone that lost
            # its accents arrives unreadable. What was meant is worth keeping legibly.
            if "text" in body:
                changes["text"] = (body.get("text") or "").strip()[:2000]
            self.amend_report(body.get("id") or "", changes)
            self.reply_json({"reports": self.reports()})
            return
        if path == "/library/numbering":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            # twelve hex digits, letters and all. Keeping only the digits named a
            # different title or none at all, so choosing how a season is numbered
            # quietly did nothing - the same fault this page's rematch already carries
            # a note about.
            key = re.sub(r"[^0-9a-f]", "", str(body.get("key") or "").lower())
            season = re.sub(r"[^0-9]", "", str(body.get("season") or ""))
            mode = str(body.get("mode") or "auto")
            if not key or not season or mode not in ("auto", "files", "database"):
                self.reply_json({"error": "which season, and numbered how?"}, 400)
                return
            stored = self.settings_file()
            chosen = stored.get("numbering") or {}
            if mode == "auto":
                chosen.pop("%s-s%s" % (key, season), None)
            else:
                chosen["%s-s%s" % (key, season)] = mode
            stored["numbering"] = chosen
            write_settings(stored, merge=False)
            moved = local().lib.renumber(str(key), int(season), mode)
            self.reply_json({"moved": moved, "mode": mode})
            return
        if path == "/library/rematch":
            # what this actually is. Identification takes TMDB's first answer, and
            # where two unrelated things share a title that answer is the popular one
            # rather than the right one - a rescan would only choose it again.
            body = self.read_json() or {}
            # the library's keys are twelve hex digits; keeping only the digits named a
            # different title, or none
            key = re.sub(r"[^0-9a-f]", "", str(body.get("key") or "").lower())
            tmdb = re.sub(r"[^0-9]", "", str(body.get("tmdbId") or ""))
            if not key or not tmdb:
                self.reply_json({"error": "which title, and which entry?"}, 400)
                return
            try:
                title = local().lib.rematch(str(key), int(tmdb))
            except Exception as e:
                self.reply_json({"error": str(e)[:200]}, 500)
                return
            self.reply_json({"title": title})
            return
        if path == "/feedback/hide":
            body = self.read_json()
            self.hide_report(body.get("id") or "", bool(body.get("hidden", True)))
            self.reply_json({"reports": self.reports()})
            return
        if path == "/feedback/clear":
            # what is still open stays: the board is for what is still wrong, and
            # clearing it should not be a way to lose that by accident
            everything = bool((self.read_json() or {}).get("everything"))
            moved = self.file_away(lambda row: everything or bool(row.get("done")))
            self.reply_json({"reports": self.reports(), "filed": moved})
            return
        if path == "/invites/update":
            body = self.read_json()
            INVITES.set_email(body.get("token") or "",
                              (body.get("email") or "").strip()[:120])
            self.reply_json({"people": [self.with_link(r) for r in INVITES.load()]})
            return
        if path == "/invites/owner":
            # Who the person at this machine is. Owning it is still a matter of where
            # a request comes from - this says whose viewing it is, so one person is
            # not a viewer in a browser away from home and the machine itself on
            # their own network.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            rows = INVITES.load()
            row = next((r for r in rows if r.get("token") == token), None)
            if token and not row:
                self.reply_json({"error": "No such person."})
                return
            if row and row.get("follows"):
                self.reply_json({"error": "That key belongs to a server, not a "
                                          "person."})
                return
            stored = read_settings()
            if stored is None:
                self.reply_json({"error": "The settings could not be read."})
                return
            was = str(stored.get("ownerIs") or "") or "me"
            if token:
                stored["ownerIs"] = token
                stored["ownerName"] = row.get("name") or "you"
            else:
                stored.pop("ownerIs", None)
            write_settings(stored, merge=False)
            moved = self.hand_over_history(was, token or "me")
            self.reply_json({"ownerIs": token, "name": stored.get("ownerName", ""),
                             "moved": moved,
                             "people": [self.with_link(r) for r in INVITES.load()]})
            return
        if path == "/invites/cache":
            # What a following server should keep for this person: what they are in
            # the middle of, and what they mean to watch. Off by default for
            # everybody but whoever owns the machine.
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            if token == "me":
                stored = read_settings() or {}
                for name in ("cacheDeck", "cacheList", "cacheCasual",
                             "shareLan"):
                    if name in body:
                        stored[name] = bool(body[name])
                # gigabytes a week copied for them, and downloaded by them; nought is
                # no limit
                for name in ("syncGbWeek", "downloadGbWeek"):
                    if name in body:
                        try:
                            stored[name] = max(0.0, float(body[name] or 0))
                        except (TypeError, ValueError):
                            pass
                write_settings(stored)
            else:
                rows = INVITES.load()
                for row in rows:
                    if row.get("token") == token:
                        # and whether this person is handed the address
                        # this machine answers to on its own network
                        for name in ("cacheDeck", "cacheList", "cacheCasual",
                                     "shareLan"):
                            if name in body:
                                row[name] = bool(body[name])
                        for name in ("syncGbWeek", "downloadGbWeek"):
                            if name in body:
                                try:
                                    row[name] = max(0.0, float(body[name] or 0))
                                except (TypeError, ValueError):
                                    pass
                INVITES.save(rows)
            self.reply_json({"people": [self.with_link(r) for r in INVITES.load()],
                             "me": self.owner_caching()})
            return
        if path == "/invites/revoke":
            INVITES.revoke(self.read_json().get("token") or "")
            self.reply_json({"people": [self.with_link(r) for r in INVITES.load()]})
            return
        if path == "/library/config":
            # The machine's own settings: its folders, its keys, its name. Guests are
            # already turned away at the door by the list of what an invitation may
            # ask for, but this one writes where the films are kept and what this
            # server searches with, and that is worth refusing here as well.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            cfg = local().lib.config()
            body = self.read_json()
            changed = None
            for key in ("movies", "tv", "mixed", "tmdb_key", "language",
                        "opensubtitles_key", "opensubtitles_user",
                        "opensubtitles_pass", "scanEvery", "scanOnChange",
                        # nothing, faults, or faults with a description of this
                        # computer: off unless somebody says otherwise
                        "sendFaults",
                        # whether this machine may fetch anything from palladium.video
                        "fetchFromSite",
                        # the most a machine following this one may use, in gigabytes
                        "cacheCap",
                        # the megabits a film is encoded at for somebody outside the
                        # house when neither they nor the server has said. Eight
                        # unless changed: what is out there is usually a phone.
                        "awayMbit",
                        # whether the shuffle's next picks are copied at all
                        "copyCasual",
                        # per cent of a film's blocks read off the machine that keeps
                        # copies while this one hands the film to a screen
                        "shareWithCopy",
                        # the megabits a copy may take while somebody is watching.
                        # Twenty unless changed; nought lets it run flat out.
                        "syncMbitWhileWatching",
                        # what to call this machine, wherever it is named. Empty
                        # means the computer's own name, which is what it was
                        # before anybody thought to ask.
                        "serverName",
                        # where encodes are made: "here", or "connected" - the
                        # computer this one copies from, or the one copying from it
                        "encodeOn"):
                if key in body:
                    cfg[key] = body[key]
                    if key in ("movies", "tv", "mixed"):
                        changed = key
            cfg.setdefault("mixed", [])
            # a folder in two lists was read twice and typed by whichever came first
            local().lib.exclusive(cfg, changed)
            local().lib.save_config(cfg)
            # tell the client which of the folders actually exist
            cfg["exists"] = {p: os.path.isdir(p)
                             for p in cfg["movies"] + cfg["tv"] + cfg["mixed"]}
            self.reply_json(cfg)
            return
        if path == "/login":
            # Slow on purpose, and slower still after a wrong one: the password is the
            # only thing between a stranger on the internet and the whole library.
            said = self.read_json() or {}
            here = self.client_address[0]
            waited = GUESSES.get(here) or [0, 0.0]
            if waited[0] >= 8 and time.time() - waited[1] < 300:
                self.reply_json({"ok": False,
                                 "error": "Too many tries. Wait five minutes."}, 429)
                return
            if not self.password_set():
                self.reply_json({"ok": False, "error": "No password is set."}, 400)
                return
            if not self.password_ok(said.get("password") or ""):
                GUESSES[here] = [waited[0] + 1, time.time()]
                time.sleep(1.0)
                self.reply_json({"ok": False, "error": "That is not the password."},
                                403)
                return
            GUESSES.pop(here, None)
            token = self.open_session()
            body = json.dumps({"ok": True, "token": token}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", self.cookie_for(token))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/logout":
            token = self.bearer()
            stored = read_settings() or {}
            live = stored.get("ownerSessions") or {}
            if token in live:
                live.pop(token, None)
                stored["ownerSessions"] = live
                write_settings(stored)
            self.reply_json({"ok": True})
            return

        if path == "/password":
            # Setting it, or clearing it, is the owner's own business: this route is
            # inside do_POST, which only an owner reaches at all.
            said = self.read_json() or {}
            if self.password_set() and not self.password_ok(said.get("old") or ""):
                # once one exists, changing it means knowing it - otherwise anybody
                # who is already through the door can lock everyone else out
                self.reply_json({"ok": False, "error": "The old password is wrong."},
                                403)
                return
            on = self.set_password(said.get("password") or "")
            self.reply_json({"ok": True, "password": on})
            return

        if path == "/setup/ffmpeg":
            # Fetching, letting go of, or taking up again the cache on this disk.
            # Letting go is not deleting: the files stay where they are and it is
            # taken up again without asking anybody for anything.
            body = self.read_json() or {}
            if body.get("off"):
                use_ffmpeg("")
                self.reply_json({"using": "", "have": ffmpeg_in_home()})
                return
            if body.get("on"):
                found = ffmpeg_in_home()
                self.reply_json({"using": use_ffmpeg(found) if found else "",
                                 "have": found})
                return
            # a fetch over a copy that is already here is a question, not a default
            have = ffmpeg_in_home()
            if have and not body.get("overwrite"):
                self.reply_json({"ask": True, "have": have,
                                 "where": FFMPEG_FROM.get(os.name) or ""})
                return
            # 80 MB from the internet, so only ever because somebody pressed for it
            if not FETCHING["busy"]:
                threading.Thread(target=fetch_ffmpeg, daemon=True).start()
            self.reply_json({"started": True, "fetching": dict(FETCHING)})
            return
        if path == "/setup/done":
            stored = read_settings() or {}
            stored["setupDone"] = True
            write_settings(stored)
            # A server that does not come back after a restart reads as broken, and
            # the first restart of a new machine is usually the same evening. So it
            # is arranged when the setting up is finished rather than asked for in a
            # wizard - the switch is in Settings, This computer, for whoever wants it
            # off. Only the first time: somebody who turned it off meant it.
            try:
                import pd_machine
                if not stored.get("askedStartup") and not pd_machine.starts_at_login():
                    pd_machine.set_start_at_login(True)
                stored["askedStartup"] = True
                write_settings(stored)
            except Exception:
                pass
            self.reply_json({"ok": True})
            return
        if path == "/library/scan":
            body = self.read_json()
            lib = local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"error": "a scan is already running", **lib.stats()}, 409)
                return
            threading.Thread(
                target=lambda: lib.scan(probe=body.get("probe", True),
                                        identify=body.get("identify", True)),
                daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/library/reparse":
            # Read every filename again with today's rules. The files have not
            # changed, so a scan skips them, and a title the old parser got wrong
            # stays wrong until this is run.
            lib = local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"error": "a scan is already running"}, 409)
                return
            threading.Thread(target=lib.reparse, daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/library/browse":
            # a small directory picker: list the folders inside a path, and the files
            # in it that end a given way - a torrent is chosen the same way a folder
            # is, because a browser's own file box hands back a sandboxed name rather
            # than a path this machine could open.
            body = self.read_json() or {}
            here = (body.get("path") or "").strip()
            ending = str(body.get("files") or "")[:12].lower()
            try:
                if not here:
                    if os.name == "nt":
                        import string
                        roots = ["%s:%s" % (d, os.sep) for d in string.ascii_uppercase
                                 if os.path.isdir("%s:%s" % (d, os.sep))]
                    else:
                        # where films live on a machine without drive letters, and
                        # the mount points a container is given them through
                        roots = [p for p in ("/media", "/mnt", "/srv", "/data",
                                             os.path.expanduser("~"), "/")
                                 if os.path.isdir(p)]
                    self.reply_json({"path": "", "parent": None, "dirs": roots})
                    return
                inside = os.listdir(here)
                entries = sorted([os.path.join(here, d) for d in inside
                                  if os.path.isdir(os.path.join(here, d))
                                  and not d.startswith(("$", "."))])
                files = sorted([os.path.join(here, d) for d in inside
                                if ending and d.lower().endswith(ending)
                                and os.path.isfile(os.path.join(here, d))]) if ending else []
                parent = os.path.dirname(here.rstrip(os.sep + "/")) or ""
                self.reply_json({"path": here, "parent": parent, "dirs": entries,
                                 "files": files})
            except Exception as e:
                self.reply_json({"error": str(e)[:120], "path": here, "dirs": []}, 400)
            return
        if path == "/trace":
            # a line from a client about what it is actually doing - what the player
            # holds, what it was given, what it makes of it. Straight into the log:
            # this is for reading here, not for the noticeboard.
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace").strip()
            with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                f.write("%s trace %s: %s\n" % (time.strftime("%H:%M:%S"),
                                                self.client_address[0], body[:1500]))
            self.reply_json({"noted": True})
            return
        if path == "/applog":
            # the Android app posts its crashes here: no store, no cable, no other way
            # to learn why it died on the sofa
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace")
            with open(os.path.join(ROOT, "app-crash.log"), "a", encoding="utf-8") as f:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write("---- " + stamp + " from " + self.client_address[0]
                        + " ----\n" + body + "\n")
            # and into the reports, where somebody will actually see it: the whole
            # trace stays in the log, the first lines are what identifies it
            head = [ln for ln in body.splitlines() if ln.strip()][:8]
            # the device says what it is in the first lines; the server knows who sent
            # it, which the device cannot say for itself
            self.file_report("crash", "\n".join(head), "android")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path != "/log":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(n, 32768)).decode("utf-8", "replace")
        if self.role == "guest":
            # whose browser this was: a trail is worth little if it cannot be
            # told from everybody else's, and a guest cannot flood the file
            who = self.guest_name or "guest"
            body = "\n".join(
                (who + ": " + ln) for ln in body.splitlines()[:120])
        body = self.not_again(body)
        if body:
            with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                f.write(body.rstrip("\n") + "\n")
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    #: last time each browser log line was written, keyed by the line with digits
    #: replaced. {line: when}
    SAID_BEFORE = {}
    #: and how long before the same line is written again
    SAY_AGAIN = 900

    @classmethod
    def not_again(cls, body):
        """Drop browser log lines repeated within SAY_AGAIN seconds.

        A stale page posts the same line every 15 s until reloaded, which buried the
        rest of the log. The page reports once now, but pages already open run the old
        code.
        """
        now = time.time()
        if len(cls.SAID_BEFORE) > 400:
            cls.SAID_BEFORE.clear()
        keep = []
        for line in body.splitlines():
            # the same line without whatever counts up in it: times, sizes, places
            bare = re.sub(r"\d+", "#", line)
            if line.strip() and now - (cls.SAID_BEFORE.get(bare) or 0) < cls.SAY_AGAIN:
                continue
            cls.SAID_BEFORE[bare] = now
            keep.append(line)
        return "\n".join(keep)

    @staticmethod
    def wanted_audio(q, src=None):
        """Which soundtrack to encode: the one asked for, or the file's English one.

        ffmpeg takes the first soundtrack when nobody says otherwise, and on a
        release with a dub in front of it that is not the one anybody wanted.
        """
        raw = (q.get("atrack") or [""])[0]
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = -1
        if n >= 0:
            return n
        got = (src or {}).get("audio")
        return int(got) if got is not None else None

    #: No cap at all until somebody sets one. Two of them, because a house has a
    #: gigabit of network in it and an upload of rather less, and the film that plays
    #: perfectly in the next room is the one that stutters at a friend's.
    QUALITY_DEFAULTS = {"home": {"height": 0, "mbit": 0},
                        "away": {"height": 0, "mbit": 0}}

    @staticmethod
    def shift_name(key, sub):
        """One correction belongs to one subtitle of one title.

        Two files for the same episode are out by two different amounts - that is
        usually why there are two - so the timing is filed against the subtitle
        itself: its stream number for a track inside the film, its file name for one
        beside it. A title with nothing named is the old form, kept so a correction
        made before this still applies.
        """
        return str(key) + ("|" + str(sub) if sub else "")

    @staticmethod
    def fit_name(video, index):
        """What a fit is filed under: the subtitle itself.

        A correction for drift belongs to the file, not to the viewer or the screen -
        the same file is out by the same amount for everybody, and it is the server
        that puts it right before anybody sees it.
        """
        if index is not None and int(index) < 0:
            import pd_localapi as _la
            try:
                return _la.sidecars(video)[-int(index) - 1]["file"]
            except Exception:
                return "%s|s%d" % (video, int(index))
        return "%s|t%s" % (video, index)

    def sub_fit(self, name):
        """The plan worked out for that subtitle: [[from, rate, shift], ...] or none."""
        held = (self.settings_file() or {}).get("subFit") or {}
        got = held.get(name)
        if isinstance(got, dict):
            parts = got.get("parts")
            return parts if isinstance(parts, list) and parts else None
        # the older form, one rate and one offset for the whole film
        if isinstance(got, list) and len(got) == 2:
            try:
                return [[0.0, float(got[0]), float(got[1])]]
            except (TypeError, ValueError):
                return None
        return None

    def set_sub_fit(self, name, parts):
        """Write it down for everybody, as a correction by hand is written down."""
        stored = self.settings_file()
        held = stored.get("subFit") or {}
        wanted = [[round(float(a), 1), round(float(r), 6), round(float(sh), 2)]
                  for a, r, sh in (parts or [])]
        # one piece that moves nothing is not a plan
        if not wanted or (len(wanted) == 1 and abs(wanted[0][1] - 1.0) < 1e-6
                          and abs(wanted[0][2]) < 0.05):
            held.pop(name, None)
        else:
            held[name] = {"parts": wanted, "when": int(time.time())}
        stored["subFit"] = held
        write_settings(stored)
        return held.get(name)

    def sub_shift(self, key, sub=""):
        """How far this subtitle has been moved, in seconds.

        Kept for the server rather than for one viewer: a file cut for another release
        is out by the same amount for everybody who plays it, and whoever notices
        first should be the last to have to correct it.
        """
        if not key:
            return 0.0
        held = (self.settings_file() or {}).get("subShift") or {}
        for name in (self.shift_name(key, sub), str(key)):
            if name in held:
                try:
                    return float(held.get(name) or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def shift_source(self, key, sub=""):
        """Who put that correction there: the film, or a person."""
        held = (self.settings_file() or {}).get("subShiftBy") or {}
        for name in (self.shift_name(key, sub), str(key)):
            if name in held:
                return str(held[name])
        return ""

    def set_sub_shift(self, key, seconds, sub="", how="hand"):
        """Anybody may correct it, because it is a fact about the file.

        It says nothing about whether the subtitle is any good: a file can be out by
        a constant amount and still be the wrong translation, or drift apart later,
        or belong to another edit entirely. So this writes down the offset and
        nothing else - the verified mark is earned by hand or by watching.
        """
        stored = self.settings_file()
        held = stored.get("subShift") or {}
        seconds = round(max(-1800.0, min(1800.0, float(seconds))), 2)
        name = self.shift_name(key, sub)
        # and who said so: a number the film asked for reads differently from one
        # somebody dialled in, even though they are the same number
        source = stored.get("subShiftBy") or {}
        if abs(seconds) < 0.05:
            held.pop(name, None)              # back where the file has it: forget it
            held.pop(str(key), None)          # and the old form, if it was there
            source.pop(name, None)
            source.pop(str(key), None)
        else:
            held[name] = seconds
            source[name] = how
        stored["subShift"] = held
        stored["subShiftBy"] = source
        write_settings(stored)
        return seconds

    def my_quality(self):
        """What this viewer has asked for, for themselves. Zero means no preference."""
        mine = self.viewer_settings(self.settings_file()) or {}
        one = mine.get("quality") or {}
        return {"height": max(0, int(one.get("height") or 0)),
                "mbit": max(0, int(one.get("mbit") or 0))}

    def quality_caps(self):
        """The ceiling on each side of the front door, as whole numbers."""
        stored = (self.settings_file() or {}).get("quality") or {}
        out = {}
        for where in ("home", "away"):
            one = stored.get(where) or {}
            out[where] = {"height": max(0, int(one.get("height") or 0)),
                          "mbit": max(0, int(one.get("mbit") or 0))}
        return out

    def too_big_to_send(self, file_id):
        """Why this file may not be sent as it is, or nothing.

        Only when a ceiling applies to this viewer: a file smaller than the limit is
        sent untouched, as it always was, because re-encoding it would cost work and
        gain nothing.
        """
        cap = self.quality_caps()["home" if self.at_home() else "away"]
        own = self.my_quality()
        height = min([x for x in (cap["height"], own["height"]) if x] or [0])
        mbit = min([x for x in (cap["mbit"], own["mbit"]) if x] or [0])
        if not height and not mbit:
            return ""
        facts = local().file_facts(file_id) or {}
        if height and (facts.get("height") or 0) > height:
            return ("this copy is %dp and %dp is the most allowed here"
                    % (facts.get("height") or 0, height))
        # the library keeps a bitrate in kilobits
        rate = (facts.get("bitrate") or 0) / 1000.0
        if mbit and rate > mbit:
            return ("this copy runs at %.1f Mbit/s and %d is the most allowed here"
                    % (rate, mbit))
        return ""

    #: What a sound track of this kind costs, in megabits, at six channels. Taken off
    #: the top before the picture is measured: the library keeps the whole container's
    #: rate, and a film with DTS carries a megabit and a half that is not picture - and
    #: is re-encoded to something far smaller anyway.
    SOUND_MBIT = {"truehd": 4.0, "mlp": 4.0, "pcm": 4.6, "flac": 1.0,
                  "dtshd": 3.0, "dts-hd": 3.0, "dts": 1.5,
                  "eac3": 0.64, "ac3": 0.64, "opus": 0.25, "vorbis": 0.25,
                  "aac": 0.25, "mp3": 0.2, "mp2": 0.2}
    SOUND_OTHERWISE = 0.4

    #: How much of an old codec's bitrate a modern one needs for the same picture.
    #: MPEG-2 spends three times what h264 does, and the DVD rips spend about half
    #: again; asking for the old number would re-encode a 21 megabit broadcast at 21.
    CODEC_COST = {"mpeg2video": 0.4, "mpeg1video": 0.4, "msmpeg4v3": 0.6,
                  "mpeg4": 0.6, "msmpeg4v2": 0.6, "wmv3": 0.8, "vc1": 0.8,
                  "h264": 1.0, "avc": 1.0, "hevc": 1.0, "h265": 1.0,
                  "vp9": 1.0, "av1": 1.0}

    #: x265 only: 1.5x the source rate
    HEVC_HEADROOM = 1.5

    @classmethod
    def sound_mbit(cls, src):
        """Audio bitrate of this file, in Mbit."""
        try:
            name = str((src or {})["acodec"] or "").lower()
        except (KeyError, IndexError, TypeError):
            return 0.0
        if not name:
            return 0.0
        cost = cls.SOUND_OTHERWISE
        for word, mbit in cls.SOUND_MBIT.items():
            if word in name:
                cost = mbit
                break
        try:
            channels = int((src or {})["channels"] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            channels = 0
        # table is for 6 channels; 8 cost ~30% more, 2 cost ~60% less
        if channels >= 8:
            cost *= 1.3
        elif 0 < channels <= 2:
            cost *= 0.4
        return cost

    @classmethod
    def source_mbit(cls, src, hevc=False):
        """Encode ceiling derived from the source file, in Mbit, or 0.

        Container rate minus audio, scaled by CODEC_COST for the source codec (MPEG-2
        spends ~3x h264 for the same picture), then HEVC_HEADROOM for x265. Minimum 1.
        """
        try:
            rate = float((src or {})["bitrate"] or 0) / 1000.0
        except (KeyError, IndexError, TypeError, ValueError):
            return 0
        if rate <= 0:
            return 0
        picture = max(0.1, rate - cls.sound_mbit(src))
        try:
            vcodec = str((src or {})["vcodec"] or "").lower()
        except (KeyError, IndexError, TypeError):
            vcodec = ""
        cost = cls.CODEC_COST.get(vcodec, 1.0)
        want = picture * cost * (cls.HEVC_HEADROOM if hevc else 1.0)
        return max(1, int(math.ceil(want)))

    def capped(self, height, mbit, src=None, hevc=False):
        """What a viewer gets, given what they asked for and where they are.

        Three things have a say, in this order: what the player asked for, what this
        viewer has set for themselves, and the ceiling the server keeps for their side
        of the front door. A player that asks for nothing in particular gets the
        viewer's own setting; whatever comes out of that is then held to the ceiling.
        """
        own = self.my_quality()
        if not height:
            height = own["height"]
        if not mbit:
            mbit = own["mbit"]
        cap = self.quality_caps()["home" if self.at_home() else "away"]
        if cap["height"] and (not height or height > cap["height"]):
            height = cap["height"]
        if cap["mbit"] and (not mbit or mbit > cap["mbit"]):
            mbit = cap["mbit"]
        # And a sensible ceiling for somebody outside the main server when nobody has set
        # one. With no number at all the encoder is given a quality to hit and a
        # ceiling meant for this network - twenty-four megabits, or forty for a 4K
        # source - and it will use them: an 800p picture went out at more megabits
        # than the film it was made from, to a phone on mobile data. Inside the main server
        # that is right, and nothing here changes it.
        if not self.at_home() and not mbit:
            try:
                mbit = int(local().lib.config().get("awayMbit") or 8)
            except (TypeError, ValueError):
                mbit = 8
        # capped at what the source carries: the rules above measure the line, not
        # the file. A 528x384 DVD rip at 1 Mbit was re-encoded at 8 Mbit, three times
        # its own size, with no detail to gain.
        own = self.source_mbit(src, hevc)
        if own and (not mbit or mbit > own):
            mbit = own
        return int(height or 0), int(mbit or 0)

    def wanted_rate(self, q):
        """How many megabits the player asked for, or none."""
        try:
            return max(0, int(float(q.get("mbit", ["0"])[0])))
        except (TypeError, ValueError):
            return 0

    def gpu_hls(self, q):
        """Start an HLS session for a client that cannot take the pipe, and redirect.

        Safari is that client, and on an iPhone there is no Media Source to work round
        it with. The playlist and its segments are served from the session folder by
        gpu_hls_file below.
        """
        if self.game_holds("transcode"):
            # asked to leave the card alone, and encoding is what it would be doing.
            # A film that can be played as it is still plays; one that needs encoding
            # says why rather than starting quietly and stuttering.
            self.send_error(503, "Game mode is on: this server is not encoding just now")
            return
        engine().sweep_hls()             # tidy what earlier viewers left behind
        try:
            key = q.get("key", [""])[0]
            mi = int(q.get("mi", ["0"])[0])
            src = local().file_for(key, mi) if q.get("src", [""])[0] == "local" else None
            height, mbit = self.capped(int(q.get("height", ["0"])[0]),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            st = engine().start_hls(
                key,
                audio_index=self.wanted_audio(q, src),
                offset=int(float(q.get("offset", ["0"])[0])),
                height=height, mbit=mbit,
                media_index=mi,
                # the same rule as the pipe: a receiver cannot decode a bitmap
                # track either, and this is the path it plays through
                burn_index=(self.burn_for(q, src)
                            if self.burn_allowed_here(q) else None),
                src=src,
                sub_look=self.shifted_look(
                    self.subtitle_settings(key, q.get("device", ["web"])[0]),
                    q.get("subshift", ["0"])[0]))
        except Exception as e:
            self.send_error(500, str(e)[:200])
            return
        self.note_cast("playlist asked for", "key=%s offset=%s"
                       % (q.get("key", [""])[0], q.get("offset", ["0"])[0]))
        # The playlist itself, not a redirect to it, and with whole addresses for the
        # segments. A player that resolves a relative name against the address it
        # first asked for was looking for /s00000.ts and finding nothing.
        sid = st.info["id"]
        # the whole film, written out before it is encoded, so the receiver knows how
        # long it is and can draw a timeline
        text = engine().hls_playlist(st)
        if text is None:
            # duration unknown: hand over what ffmpeg has written so far instead
            path = engine().hls_file(sid, "index.m3u8")
            if not path:
                self.send_error(500, "the playlist did not appear")
                return
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                self.send_error(500, "the playlist could not be read")
                return
        lines = text.splitlines()
        token = self.bearer()
        host = self.headers.get("Host") or ("%s:%d" % (LAN_IP, PORT))
        base = "http://%s/gpu/hls/%s/" % (host, sid)
        body = chr(10).join(
            (base + line + ("?t=" + token if token else "")) if line.endswith(".ts")
            else line
            for line in lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Palladium-Engine", st.info["engine"])
        self.send_header("X-Palladium-Session", sid)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def gpu_hls_file(self, sid, name):
        """A playlist or a segment out of one session's folder."""
        seg = re.match(r"^s(\d{5})\.ts$", name or "")
        if seg:
            # the playlist names every segment of the film, so one of them can be
            # asked for before it exists - either because the encoder has not reached
            # it yet, or because somebody seeked past it. Both are waited for.
            path = engine().hls_segment(sid, int(seg.group(1)))
        else:
            path = engine().hls_file(sid, name)
        if not path:
            self.note_cast("MISSING " + name, sid)
            self.send_error(404, "no such segment")
            return
        # the playlist is the moment a receiver commits; segments after it are only
        # noise, so only the first of them is worth a line
        if name.endswith(".m3u8") or name.endswith("00000.ts"):
            self.note_cast("took " + name, sid)
        kind = ("application/vnd.apple.mpegurl" if name.endswith(".m3u8")
                else "video/mp2t")
        try:
            with open(path, "rb") as f:
                body = f.read()
            # A television fetching the segments is a machine of its own: it holds no
            # cookie, and the names in a playlist are relative, so the invitation has
            # to be written into each line or every segment is refused.
            token = self.bearer()
            if name.endswith(".m3u8") and token:
                body = chr(10).join(
                    (line + "?t=" + token) if line.endswith(".ts") else line
                    for line in body.decode("utf-8", "replace").splitlines()
                ).encode()
        except OSError:
            # a segment being written this instant: ask again rather than fail
            self.send_error(404, "not yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    # How long an encoder may take to produce its first block. It is a wait, not a
    # deadline for the film: NVENC on a 4K burn needs a few seconds before anything
    # exists. What it stops is the other case - a muxer that will never write - which
    # left the request hanging with no answer at all rather than failing.
    FIRST_BLOCK_WAIT = 15.0

    def no_dolby_here(self, path):
        """Has this file already been shown to produce nothing as Dolby?

        Written down against its size, so a file replaced by a better rip is asked
        again rather than judged by what the old one did.
        """
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        return (self.settings_file().get("noDolby", {}) or {}).get(path) == size

    def note_no_dolby(self, path):
        """Remember it, so the next viewing does not spend the wait discovering it."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        stored = self.settings_file()
        known = stored.get("noDolby", {}) or {}
        known[path] = size
        # a few hundred is far more than any library will ever produce; the cap is
        # there so a fault that fires on everything cannot grow the file for ever
        if len(known) > 400:
            known.pop(next(iter(known)), None)
        stored["noDolby"] = known
        write_settings(stored, merge=False)

    @classmethod
    def first_block(cls, st):
        """The first bytes of a stream, or nothing if it never manages any."""
        gate = threading.Timer(cls.FIRST_BLOCK_WAIT, st.stop)
        gate.daemon = True
        gate.start()
        try:
            return st.proc.stdout.read(65536)
        except Exception:
            return b""
        finally:
            gate.cancel()

    #: what each file's picture is, asked once: a copy is only for a picture the device
    #: plays as it is
    VIDEO_FORMATS = {}

    def video_format(self, path):
        """The first video track's codec and pixel format."""
        if path in Handler.VIDEO_FORMATS:
            return Handler.VIDEO_FORMATS[path]
        out = {}
        try:
            import pd_gpu
            from pd_library import ffprobe_beside
            said = subprocess.run(
                [ffprobe_beside(pd_gpu.FFMPEG), "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=20, creationflags=pd_gpu.NO_WINDOW)
            bits = ((said.stdout or "").strip().splitlines() or [""])[0].split(",")
            out = {"codec": bits[0].strip().lower(),
                   "pix": bits[1].strip().lower() if len(bits) > 1 else ""}
        except Exception:
            out = {}
        Handler.VIDEO_FORMATS[path] = out
        return out

    def video_copies(self, src, q, height, mbit, burn):
        """Whether an encode can pass the picture through: nothing burned into it, no
        size or rate asked for, and a picture the device decodes as it is."""
        if not src or not src.get("file") or burn is not None or mbit:
            return False
        if height and (src.get("height") or 0) > height:
            return False
        fmt = self.video_format(src["file"])
        pix = fmt.get("pix", "")
        if fmt.get("codec") == "h264":
            return pix in ("yuv420p", "yuvj420p")
        if fmt.get("codec") == "hevc":
            return (q.get("hevc", ["0"])[0] in ("1", "true", "yes")
                    and pix in ("yuv420p", "yuvj420p", "yuv420p10le"))
        return False

    @staticmethod
    def copy_start(path, offset):
        """Where a copied picture starts for a seek to this second. The keyframe list
        and ffmpeg's seek do not always agree, so ffmpeg is asked, with the stream's
        own flags, for the first frame it would copy."""
        import pd_gpu
        from fractions import Fraction
        tag = ("%.3f" % float(offset)).rstrip("0").rstrip(".")
        try:
            said = subprocess.run(
                [pd_gpu.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-noaccurate_seek", "-ss", tag, "-copyts", "-i", path,
                 "-map", "0:v:0", "-c", "copy", "-frames:v", "1", "-f", "framecrc", "-"],
                capture_output=True, text=True, timeout=20, creationflags=pd_gpu.NO_WINDOW)
        except Exception:
            return None
        base = None
        for line in (said.stdout or "").splitlines():
            if line.startswith("#tb 0:"):
                try:
                    base = Fraction(line.split(":", 1)[1].strip())
                except (ValueError, ZeroDivisionError):
                    return None
            elif line and not line.startswith("#") and base is not None:
                try:
                    return round(float(base * int(line.split(",")[2].strip())), 3)
                except (IndexError, ValueError):
                    return None
        return None

    def encode_begins(self, q):
        """Where an encode asked for with these settings will start, in film time, and
        whether its picture is copied. An app asks before it opens the stream."""
        offset = float(q.get("offset", ["0"])[0] or 0)
        try:
            key = q.get("key", [""])[0]
            src = (local().file_for(key, int(q.get("mi", ["0"])[0] or 0))
                   or self.house_source(q)) \
                if q.get("src", [""])[0] == "local" else None
            height, mbit = self.capped(int(q.get("height", ["0"])[0] or 0),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            burn = self.burn_for(q, src) if self.burn_allowed_here(q) else None
            if not self.video_copies(src, q, height, mbit, burn):
                return {"at": offset, "copy": False}
            if offset <= 0:
                return {"at": 0, "copy": True}
            start = self.copy_start(src["file"], int(offset))
            # a start after the second asked for, or far before it, is not a seek
            # anybody should be handed
            if start is None or start > int(offset) + 0.01 or int(offset) - start > 30:
                return {"at": offset, "copy": False}
            return {"at": start, "copy": True}
        except Exception as e:
            return {"at": offset, "copy": False, "why": str(e)[:120]}

    #: whether a connected computer answered lately: address -> (when, answered)
    CONNECTED_UP = {}

    def encode_elsewhere(self, q):
        """Sent to the connected computer to encode, when this one is set to and it answers.

        The computer this one copies from, or the one copying from it. When it does not
        answer, this one encodes. A request already handed over is made where it lands,
        so two machines both set to hand over do not pass it back and forth. True when
        sent.
        """
        if q.get("handed", ["0"])[0] == "1":
            return False
        cfg = self.library_settings_plain() or {}
        if cfg.get("encodeOn") != "connected":
            return False
        import pd_follow
        import urllib.request as _ureq
        import urllib.error as _uerr
        one = pd_follow.settings(cfg)
        home = self.at_home()
        if one.get("on") and one.get("master"):
            doors = pd_follow.house_doors()
            check = one["master"]
            door = (doors.get("lan") or one["master"]) if home else (doors.get("outside") or "")
        else:
            other = self.standby_now() or {}
            check = other.get("where") or ""
            door = check if home else (other.get("outside") or "")
        if not (check and door):
            return False
        was = Handler.CONNECTED_UP.get(check)
        if was and time.time() - was[0] < 30:
            up = was[1]
        else:
            try:
                with _ureq.urlopen(check.rstrip("/") + "/where", timeout=2) as answer:
                    up = answer.status == 200
            except _uerr.HTTPError:
                up = True                 # it answered, if not to this question
            except Exception:
                up = False
            Handler.CONNECTED_UP[check] = (time.time(), up)
        if not up:
            return False
        self.send_response(307)
        self.send_header("Location", door.rstrip("/") + self.path +
                         ("&" if "?" in self.path else "?") + "handed=1")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    #: what the main server said about files this copy holds none of: (when, answer)
    HOUSE_SOURCES = {}

    def house_source(self, q):
        """A title this copy holds no file for, as the main server's file over the network.

        Played from this machine, it is encoded by this machine: the main server hands over
        the file, not the work. None when the file is here, when this machine follows
        nothing, or when the main server does not answer.
        """
        key = q.get("key", [""])[0]
        try:
            mi = int(q.get("mi", ["0"])[0] or 0)
            if local().file_for(key, mi):
                return None
        except Exception:
            return None
        import pd_follow
        one = pd_follow.settings(self.library_settings_plain() or {})
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        mark = (key, mi)
        was = Handler.HOUSE_SOURCES.get(mark)
        # an answer is good for ten minutes; no answer is asked again in thirty seconds
        if was and time.time() - was[0] < (600 if was[1] else 30):
            said = was[1]
        else:
            try:
                said = pd_follow.ask(one, "/follow/source?key=%s&mi=%d"
                                     % (urllib.parse.quote(key), mi), 10)
            except Exception:
                said = None
            Handler.HOUSE_SOURCES[mark] = (time.time(), said)
        if not said or not said.get("part"):
            return None
        src = dict(said)
        src["file"] = "%s/local/parts/%d?t=%s" % (one["master"].rstrip("/"), int(said["part"]),
                                                  urllib.parse.quote(one["key"]))
        src["remote"] = True
        return src

    def send_to_the_house(self, q):
        """A title this copy holds no file for, sent on to the main server.

        For subtitles, and for an encode when the main server's file cannot be read from
        here. An app that moved here while the main server was away keeps asking here.
        Redirected with the same query and key, by the door the viewer can reach;
        True when sent.
        """
        try:
            if local().file_for(q.get("key", [""])[0], int(q.get("mi", ["0"])[0] or 0)):
                return False
        except Exception:
            return False
        import pd_follow
        one = pd_follow.settings(self.library_settings_plain() or {})
        if not (one.get("on") and one.get("master")):
            return False
        doors = pd_follow.house_doors()
        house = ((doors.get("lan") or one["master"]) if self.at_home()
                 else doors.get("outside") or "")
        if not house:
            return False
        self.send_response(307)
        self.send_header("Location", house.rstrip("/") + self.path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def gpu_stream(self, q):
        """Pipe one ffmpeg's fragmented MP4 straight to the browser."""
        # a television asking for the pipe is a mistake somewhere: it is the one thing
        # a receiver cannot reliably play, and knowing it asked explains the failure
        if self.game_holds("transcode"):
            self.send_error(503, "Game mode is on: this server is not encoding just now")
            return
        self.note_cast("asked for the PIPE, which it cannot play",
                       "key=%s" % q.get("key", [""])[0])
        try:
            key = q.get("key", [""])[0]
            mi = int(q.get("mi", ["0"])[0])
            # the library hands us the file itself; there is nothing else to ask
            src = ((local().file_for(key, mi) or self.house_source(q))
                   if q.get("src", [""])[0] == "local" else None)
            height, mbit = self.capped(int(q.get("height", ["0"])[0]),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            burn = self.burn_for(q, src) if self.burn_allowed_here(q) else None
            # the picture as it is, for an app that asked where such a stream starts
            copy = (q.get("copyv", ["0"])[0] == "1"
                    and self.video_copies(src, q, height, mbit, burn))
            begin = lambda mode: engine().start(
                                key,
                                audio_index=self.wanted_audio(q, src),
                                # a client that knows it cannot play 5.1 - headphones
                                # on a television box - asks for two channels
                                channels=max(0, int(q.get("ch", ["0"])[0] or 0)),
                                offset=int(float(q.get("offset", ["0"])[0])),
                                height=height, mbit=mbit,
                                media_index=mi,
                                burn_index=burn,
                                copy_video=copy,
                                src=src,
                                audio_mode=mode,
                                # loudness correction, applied by the encoder
                                gain_db=volume_gain(src.get("part") or src.get("id"),
                                                    src.get("file"),
                                                    src.get("size") or 0),
                                # the client saying it can decode HEVC, which means
                                # nothing has to be made smaller to reach it
                                hevc=q.get("hevc", ["0"])[0] in ("1", "true", "yes"),
                                # and whether the sound can go through as it lies:
                                # a set with a DTS decoder wants the track, not our
                                # AC-3 made out of it
                                dts=q.get("dts", ["0"])[0] in ("1", "true", "yes"),
                                # a burned track is part of the picture, so the size
                                # and position have to be applied while drawing it
                                # a burned track is part of the picture, so the look
                                # has to be applied while drawing it - this film's own
                                # a client that has zoomed the picture asks for the
                                # burned subtitles to be raised by what zoom will crop
                                sub_look=self.shifted_look(
                                    self.subtitle_settings(q.get("key", [""])[0],
                                                           q.get("device", ["web"])[0]),
                                    q.get("subshift", ["0"])[0]))
            wanted = q.get("audio", ["aac"])[0]
            # A file whose Dolby track has already come out empty is not asked twice.
            # The discovery costs the whole first-block wait and then a cold start -
            # fifteen seconds before a frame - and it would cost that on every play.
            if wanted == "passthrough" and self.no_dolby_here((src or {}).get("file", "")):
                wanted = "aac"
            # Sound as it is, or picture and sound both made. Never one copied
            # beside the other.
            #
            # Every torn picture seen here has been on that one path - the picture
            # handed over as it stands while the sound is remade beside it, the two
            # put back together into fragmented MP4 on the way out. A film sent as
            # the file itself has never torn, and one encoded whole has never torn.
            # The same episode plays perfectly and then tears, from the same file,
            # at the same second: it is not the bit depth, not the resolution, not
            # the container, and not where it is started from - all of those were
            # tried and each was the same on a file that was fine.
            #
            # So the path is not used. It is reached only when the sound has to be
            # remade and the picture need not be - a small part of any library, and
            # untouched for everything that goes out as the file itself, which is
            # what can be read off two machines at once and handed over when one goes.
            if wanted != "passthrough":
                copy = False
            # "Decoder: processor" on the settings page. The card is quicker and is
            # what this uses by default; the processor is there for a card that is
            # full, or busy with something else, or making a mess of a particular
            # file. It costs nothing to offer and it is always present.
            if q.get("engine", [""])[0] == "cpu":
                import pd_gpu
                with pd_gpu.on_the_cpu():
                    st = begin(wanted)
            else:
                st = begin(wanted)
        except Exception as e:
            # An encoder that will not start is the end of the evening on a machine
            # that has no encoder - the machine that keeps copies is a spare box with
            # no card in it, and a viewer handed to it got a five hundred and a
            # stopped picture. The file is there and most of what asks for an encode
            # could have played it as it stands, so send it: a player that truly
            # cannot decode it will say so, which is a better answer than nothing.
            if src and src.get("file") and os.path.exists(src["file"]):
                self.log_message("no encoder here (%s); sending the file as it is",
                                 str(e)[:120])
                self.send_file_ranged(src["file"])
                return
            # the main server's file, and this machine could not encode it: the main server can
            if src and src.get("remote") and self.send_to_the_house(q):
                return
            self.send_error(500, str(e)[:200])
            return
        # One device, one encode. A player that restarts - a change of soundtrack, a
        # seek, an error it recovers from - opens a new stream while the old one is
        # still being written; the old ffmpeg only stops when its connection dies,
        # which can be half a minute. Four of those at once had the card busy enough
        # that the next request timed out before its first byte, which is what a
        # casual episode refusing to start turned out to be.
        engine().stop_others(self.client_address[0],
                             q.get("device", ["web"])[0], st.info["id"])
        # The first block, before a word of the answer is written. An encoder that
        # will not start - a filter graph that refuses to configure, a stream asked
        # for that the file does not hold - used to be answered with 200 and nothing
        # behind it, which the player can only read as a broken container. Read the
        # reason off ffmpeg's own output instead and say it.
        first = self.first_block(st)
        # Dolby is asked for by name and cannot always be given. On one file the muxer
        # took the AC-3 track and never wrote a byte - with delay_moov it waits for a
        # header it never manages, without it the header cannot be written at all -
        # and the episode simply did not start. AAC is the plain answer that every
        # device takes, so it is tried before giving up on the film.
        if not first and wanted == "passthrough":
            stalled = st.why()
            st.stop()
            try:
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write("%s no Dolby out of %s, trying plain sound: %s%s" % (
                        time.strftime("%H:%M:%S"), key, stalled, chr(10)))
            except Exception:
                pass
            self.note_no_dolby((src or {}).get("file", ""))
            try:
                st = begin("aac")
            except Exception as e:
                self.send_error(500, str(e)[:200])
                return
            first = self.first_block(st)
        if not first:
            said = st.why() or "the encoder stopped without saying why"
            st.stop()
            # The card may simply have had no room: a subtitle being written holds
            # eight gigabytes of it, and NVENC then refuses to start with nothing much
            # to say. The processor is slower and always available, so it is tried once
            # before anybody is told the film will not play.
            import pd_gpu
            if pd_gpu.HW_OK:
                try:
                    with pd_gpu.on_the_cpu():
                        st = begin("passthrough")
                        first = self.first_block(st)
                except Exception:
                    first = None
                if first:
                    said = ""
        if not first:
            self.file_report("error", "nothing came out of the encoder" + chr(10)
                             + "key=%s offset=%s burn=%s" % (
                                 key, q.get("offset", ["0"])[0],
                                 (q.get("burn") or ["none"])[0]) + chr(10) + said,
                             "server")
            try:
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write("%s encoder said nothing for %s: %s%s" % (
                        time.strftime("%H:%M:%S"), key, said, chr(10)))
            except Exception:
                pass
            self.send_error(502, "the encoder produced nothing: " + said[:120])
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Palladium-Engine", st.info["engine"])
        self.end_headers()
        sid = WATCHING.start(
            self.watcher(),
            local().title_for(key) if q.get("src", [""])[0] == "local" else key,
            self.quality(src),
            ("direct video, sound encoded" if st.info.get("copyVideo")
             else "transcode (%s)" % st.info.get("engine", "?")),
            self.client_address[0], key, self.app_name(), self.device_kind())
        try:
            self.wfile.write(first)
            WATCHING.sent(sid, len(first))
            while True:
                chunk = st.proc.stdout.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                WATCHING.sent(sid, len(chunk))
        except Exception:
            pass                      # the browser closed the connection: normal on seek
        finally:
            WATCHING.stop(sid)
            st.stop()

    @staticmethod
    def burn_for(q, src):
        """The subtitle to burn in, but only if this copy holds it.

        A client asks by stream number, and a number means something different in
        another file. Two copies of one film, a player left running while the list
        was reordered, or a rescan: the request arrives for stream 3 of a file whose
        only subtitle is stream 2, ffmpeg finds nothing to overlay and dies, and the
        television reports a container it cannot parse. Playing without the subtitle
        is the better answer.
        """
        if not q.get("burn"):
            return None
        try:
            want = int(q["burn"][0])
        except (TypeError, ValueError):
            return None
        held = (src or {}).get("subs")
        if held is not None and want not in held:
            return None
        return want

    def burn_allowed_here(self, q):
        """A burn asked for from outside the main server, when the owner has not allowed it.

        Refused rather than obeyed: the film plays without the subtitle, which is the
        same answer the picker gives, and a page opened before the setting changed
        cannot spend an encode on it.
        """
        if self.may_burn():
            return True
        # the app draws these itself and never asks for a burn; anything that does
        # ask from away is the browser
        return False

    def watcher(self):
        """The name to show against a stream.

        What somebody calls themselves comes first: the name on their invitation is
        what the owner wrote when they made the key, and a person may be known by
        another.

        A guest by the name on their invitation, whoever sits at this machine by the
        owner's own name, and anybody else by the address they came from. The owner
        stood in the watching list as a bare address among named guests - the one
        person the server is certain of, shown as a number.
        """
        mine = self.viewer_settings(self.settings_file()).get("myName")
        if mine:
            return str(mine)
        if self.role == "guest" and self.guest_name:
            return self.guest_name
        host = self.client_address[0]
        if self.at_home():
            named = str((read_settings() or {}).get("ownerName") or "")
            if named:
                return named
        return "you" if host in ("127.0.0.1", "::1") else host

    @staticmethod
    def quality(src):
        if not src:
            return ""
        h = src.get("height") or 0
        label = "4K" if h >= 1700 else ("%dp" % h if h else "")
        return " ".join(x for x in [label, (src.get("videoCodec") or "").upper()] if x)

    # How subtitles are drawn, per kind of screen. A phone needs proportionally larger
    # text than a television: the screen is smaller but so is the viewing distance, and
    # the two do not cancel out. These are starting points, not opinions - all four
    # values are adjustable per device and per title.
    DEVICES = ("tv", "web", "phone")
    # every screen starts in step; the offset is a per-title correction, not a taste
    #: "base" says what a height is measured from: the picture, or the whole screen.
    #: The picture, by default: a film wider than the television is drawn with black
    #: under it, and "one line up" inside that bar is still on the bottom edge of the
    #: screen - which is what "Bottom" was set to and what everybody read it as.
    SUBTITLE_DEFAULTS = {
        "tv":    {"size": 1.0,  "position": 0.08, "colour": "white",
                  "background": "shadow", "font": "sans", "base": "picture"},
        "web":   {"size": 1.0,  "position": 0.08, "colour": "white",
                  "background": "shadow", "font": "sans", "base": "picture"},
        # 0.05 was between two of the four heights the menus offer, so a phone opened
        # its settings with none of them marked; a height is a row of text now, and
        # this is the first row up - which is where it was drawn anyway.
        "phone": {"size": 1.25, "position": 0.08, "base": "picture", "colour": "white",
                  "background": "shadow", "font": "sans"},
    }

    @classmethod
    def device_of(cls, name):
        name = (name or "web").lower()
        return name if name in cls.DEVICES else "web"

    def settings_file(self):
        """Everyone's settings. Empty only when there genuinely are none."""
        stored = read_settings()
        return stored if stored is not None else {}

    def viewer(self):
        """Whose settings these are: the owner, or the guest's invitation.

        The owner may have a key of their own - the invitation they use on the
        television and away from the main server. Then that is who they are on every
        screen, this one included, and one person has one history instead of two.
        Owning the machine is still a matter of where the request comes from; the
        key says who is watching, not what they may change.
        """
        if self.role == "owner":
            return str((read_settings() or {}).get("ownerIs") or "") or "me"
        return self.bearer() or "guest"

    def viewer_settings(self, stored):
        """The part of settings.json that belongs to whoever is asking.

        The owner keeps the top level - that is where these settings have always been
        - and everyone else gets a corner of their own, started from the defaults.

        The owner may take a key of their own, so that they are the same person on the
        television as at the desk. From that moment they are filed under it like
        anybody else, and everything they had at the top level - their shelves, their
        watchlist, how their subtitles look - was still sitting there and no longer
        being read. It is carried over the first time they are asked for, once.
        """
        who = self.viewer()
        if who == "me":
            return stored
        mine = stored.setdefault("users", {}).setdefault(who, {})
        if who == str(stored.get("ownerIs") or ""):
            for name in Handler.VIEWER_KEYS:
                if name in stored and name not in mine:
                    mine[name] = stored.pop(name)
            Handler.own_the_old_history(who)
        return mine

    #: done once per run: it is a rename of a handful of rows, not a thing to try on
    #: every request
    HISTORY_MOVED = set()

    @classmethod
    def own_the_old_history(cls, who):
        """Put what the owner watched before they had a key under that key.

        "me" is the name a server gives its owner before anybody has said who they
        are. Taking an invitation of their own makes them a named person, and
        everything they had watched stayed behind under the old name - so their own
        Continue watching emptied itself on the day they became somebody.
        """
        if not who or who == "me" or who in cls.HISTORY_MOVED:
            return
        cls.HISTORY_MOVED.add(who)
        try:
            con = local().lib.db()
            for table in ("progress", "watchlog"):
                # anything the named person already has wins: they watched it later
                con.execute(
                    "DELETE FROM %s WHERE who='me' AND key IN "
                    "(SELECT key FROM %s WHERE who=?)" % (table, table), (who,))
                con.execute("UPDATE %s SET who=? WHERE who='me'" % table, (who,))
            con.commit()
        except Exception:
            pass

    @staticmethod
    def round_moved(mine):
        """Say when this shuffle last moved, so two machines can tell whose is newer.

        The round is one thing wherever it is played. Merging two of them makes a
        queue nobody drew, so the newer one is taken whole and the older dropped.
        """
        mine["casualStamp"] = int(time.time())

    @staticmethod
    def round_stamp(one):
        """When a round last moved; nought for none."""
        return int(one.get("casualStamp") or 0) if isinstance(one, dict) else 0

    def casual_item(self, key):
        """One key as a thing with a poster, for a client to name and show."""
        local().who = self.viewer()
        con = local().lib.db()
        try:
            return local().metadata_for(con, key)
        finally:
            con.close()

    def ask_the_pack(self, keys):
        """Start fetching anything here that only a pack has, quietly.

        A shuffle can now draw a title the library does not hold. Nothing waits for
        this: the draw answers at once, saying the title is still coming, and the
        download runs behind it.
        """
        wanted = [str(k) for k in keys if str(k).startswith("o")]
        if not wanted:
            return

        def work():
            import pd_torrents
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            for key in wanted:
                try:
                    pd_torrents.request(key, token, self.watcher(), cap)
                except Exception:
                    pass               # a shuffle is not held up by a download
        threading.Thread(target=work, daemon=True).start()

    def fetch_ahead(self, keys):
        """Fetch subtitles for what is coming, quietly and in the background.

        The same trick that makes an episode start with its subtitles already beside
        it, applied to a queue rather than to one series. Failures are silent: this is
        a courtesy, and nothing waits for it.
        """
        def work():
            for key in keys:
                try:
                    self.auto_subtitle(key, "")
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def seasons_of(self, item):
        """The season keys of one programme, in order."""
        con = local().lib.db()
        try:
            rows = con.execute(
                "SELECT DISTINCT season FROM episode WHERE item_id=? ORDER BY season",
                (str(item),)).fetchall()
            return ["%s-s%d" % (item, r["season"]) for r in rows]
        except Exception:
            return []
        finally:
            con.close()


    def read_as_seasons(self, con, raw):
        """A shelf's keys as the cards they are read as: seasons, not episodes.

        Returns (cards, keys) - the season cards, and the keys that are not part of
        any of them and stand for themselves.
        """
        # Episodes stand as the seasons they belong to. A shelf holding four
        # episodes of a fifty-six episode programme showed four episode cards
        # and said nothing about the programme; one season card saying "4 of
        # 56 episodes" is what somebody reading the shelf wants to know. The
        # episodes are still what the shelf holds - this is how it is read,
        # not what it is.
        keys, seasons, whole = [], {}, set()
        for key in raw:
            if key.startswith("e"):
                seat = con.execute(
                    "SELECT item_id, season, number FROM episode WHERE id=?",
                    (key,)).fetchone() if is_episode(key) else None
                if seat:
                    where = "%s-s%d" % (seat["item_id"], seat["season"] or 0)
                    seasons.setdefault(where, [])
                    seasons[where].append((seat["number"] or 0, key))
                    continue
            elif re.match(r"^[0-9a-f]{12}-s\d+$", key):
                whole.add(key)
                continue
            keys.append(key)
        # A programme is its seasons too. One card for a thirty-five season
        # series said nothing about which part of it is on the shelf, and
        # there was nothing to open to take a single episode off.
        mine = [k for k in keys if re.fullmatch(r"[0-9a-f]{12}", k)]
        if mine:
            shows = set(str(r["id"]) for r in con.execute(
                "SELECT id FROM item WHERE type='show' AND id IN (%s)"
                % ",".join("?" * len(mine)), mine))
            drawn = set()
            for show in shows:
                mine = set()
                for r in con.execute(
                        "SELECT DISTINCT season FROM episode WHERE item_id=?",
                        (show,)):
                    whole.add("%s-s%d" % (show, r["season"] or 0))
                    mine.add(int(r["season"] or 0))
                    drawn.add(show)
                # and the seasons only a pack has. A programme somebody holds a
                # dozen episodes of drew nine tiles of one and two episodes each
                # and said nothing about the rest of the series, which is most of
                # it - the same merge the programme's own page already makes.
                row = con.execute("SELECT title FROM item WHERE id=?",
                                  (show,)).fetchone()
                for extra in local()._offered_seasons_of(
                        row["title"] if row else "", mine):
                    whole.add("%s-s%d" % (show, int(extra.get("index") or 0)))
                    drawn.add(show)
            # a programme with no episodes here has no seasons to draw, so it
            # keeps its own card rather than disappearing off the shelf
            keys = [k for k in keys if k not in drawn]
        # what each of those seasons holds - one query per programme, not one
        # per episode: a shelf of long-running series is thousands of them
        here = {}
        for show in sorted(set(k.partition("-s")[0] for k in whole)):
            for r in con.execute(
                    "SELECT id, season, number FROM episode WHERE item_id=? "
                    "ORDER BY season, number", (show,)):
                where = "%s-s%d" % (show, r["season"] or 0)
                if where in whole:
                    seasons.setdefault(where, [])
                    seasons[where].append((r["number"] or 0, str(r["id"])))
                    here.setdefault(where, set()).add(int(r["number"] or 0))
        # A season a pack can give and this machine has none of has no rows at all,
        # so it had no tile; a season it has two episodes of is not two episodes
        # long. How big each one really is comes from the pack, the way the
        # programme's own page already counts them.
        sizes = {}
        for where in whole:
            seasons.setdefault(where, [])
            show, _, number = where.partition("-s")
            row = con.execute("SELECT title FROM item WHERE id=?", (show,)).fetchone()
            try:
                number = int(number)
            except ValueError:
                continue
            extra = local()._offered_episodes_of(
                row["title"] if row else "", number, here.get(where) or set())
            if extra:
                sizes[where] = len(here.get(where) or ()) + len(extra)
        rows = []
        for where, inside in seasons.items():
            one = self.season_card(con, where, inside)
            if one:
                # what is here, against the whole of it - the rest is downloadable
                one["leafCount"] = max(int(one.get("leafCount") or 0),
                                       int(sizes.get(where) or 0))
                # nothing of it here yet: greyed like any other thing a pack can
                # give, so a series mostly on offer reads as one at a glance
                if not inside:
                    one["offered"] = True
                rows.append(one)
        return rows, keys

    def season_card(self, con, where, inside):
        """One season key as the card that is read for it, or None.

        Named as the season it is. The key for a season answers with the
        programme, so nineteen cards all read as the same programme and
        counted their episodes against the whole of it - "7 of 179" for
        a season of twelve, nineteen times over.
        """
        one = local().metadata_for(con, where, brief=True)
        if not one:
            return None
        show, _, number = str(where).partition("-s")
        try:
            number = int(number)
        except ValueError:
            number = 0
        seat = con.execute(
            "SELECT COUNT(*) c FROM episode WHERE item_id=? AND season=?",
            (show, number)).fetchone()
        one = dict(one)
        one["type"] = "season"
        one["ratingKey"] = str(where)
        # the programme it hangs off, so pressing the card opens the
        # show at this season rather than at the first one
        one["parentRatingKey"] = show
        one["parentTitle"] = one.get("title") or ""
        one["grandparentTitle"] = one.get("title") or ""
        one["title"] = "Season %d" % number if number else "Specials"
        one["index"] = number
        # how much of it is here, against how much of it there is: the
        # difference between a season on a shelf and part of one
        seen = set()
        mine = [k for _, k in sorted(inside)
                if not (k in seen or seen.add(k))]
        one["leafCount"] = int((seat and seat["c"]) or len(mine))
        one["shelfCount"] = len(mine)
        # and what it stands for, in order. A card rolled up out of
        # episodes is a way of reading the shelf; playing it has to play
        # the episodes, and playing the season's own key opened the
        # programme's page instead - Play took somebody to the show.
        one["holds"] = mine
        return one

    def season_of(self, ekey):
        """The season key one episode belongs to, or an empty string."""
        con = local().lib.db()
        try:
            row = con.execute("SELECT item_id, season FROM episode WHERE id=?",
                              (str(ekey),)).fetchone()
            return "%s-s%d" % (row["item_id"], row["season"]) if row else ""
        except Exception:
            return ""
        finally:
            con.close()

    def covers(self, mark, key):
        """Whether a mark stands above a key: a programme over one of its seasons or
        episodes, or a season over one of its episodes."""
        mark, key = str(mark), str(key)
        if mark == key:
            return True
        if key.startswith("e"):
            season = self.season_of(key)
            if mark == season:
                return True
            return bool(season) and mark == season.split("-s")[0]
        one = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
        return bool(one) and mark == one.group(1)

    def without(self, mark, key):
        """What is left of a mark when one thing under it is taken off.

        A programme becomes its seasons, and a season its episodes - one step at a
        time, because the shelf is drawn by season and a mark per episode of a
        long-running programme is a settings file nobody can read.
        """
        mark, key = str(mark), str(key)
        if mark == key:
            return []
        one = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
        if is_title(mark) and one and one.group(1) == mark:
            return [s for s in self.seasons_of(mark) if s != key]
        if key.startswith("e"):
            season = self.season_of(key)
            rest = [e for e in self.spread(season) if e != key] if season else []
            if mark == season:
                return rest
            if is_title(mark) and season.startswith(mark + "-s"):
                return [s for s in self.seasons_of(mark) if s != season] + rest
        return []

    def spread(self, key):
        """The keys a mark on this thing should actually set.

        A programme and a season are not shelves of their own: marking one means
        marking what is under it, so that an episode can then be taken off on its own
        without arguing with a mark somewhere above it. A film, or a single episode,
        stands for itself.
        """
        key = str(key or "")
        if not key or key.startswith("e"):
            return [key] if key else []
        con = local().lib.db()
        try:
            season = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
            if season:
                rows = con.execute(
                    "SELECT id FROM episode WHERE item_id=? AND season=? ORDER BY number",
                    (season.group(1), int(season.group(2)))).fetchall()
                return [str(r["id"]) for r in rows] or [key]
            if not is_title(key):
                return [key]
            row = con.execute("SELECT type FROM item WHERE id=?", (str(key),)).fetchone()
            if not row or row["type"] == "movie":
                return [key]
            rows = con.execute(
                "SELECT id FROM episode WHERE item_id=? ORDER BY season, number",
                (str(key),)).fetchall()
            return [str(r["id"]) for r in rows] or [key]
        except Exception:
            return [key]
        finally:
            con.close()

    def rollup(self, keys):
        """Which seasons and programmes a list of marked episodes amounts to.

        A card for a programme or a season carries no key of its own on these shelves
        any more - marking one marks its episodes - so a grid asking "is this marked?"
        about a series would always hear no. This answers for the things above the
        episodes: fully marked, and partly marked, kept apart so a half-marked season
        can look different from a whole one.
        """
        ids = [k for k in map(str, keys)
               if is_episode(k)]
        if not ids:
            return [], []
        con = local().lib.db()
        try:
            marks = ",".join("?" * len(ids))
            held = {}
            for r in con.execute(
                    "SELECT item_id, season, COUNT(*) c FROM episode WHERE id IN (%s) "
                    "GROUP BY item_id, season" % marks, ids):
                held[(r["item_id"], r["season"])] = r["c"]
            shows = set(item for item, _ in held)
            whole = {}
            for r in con.execute(
                    "SELECT item_id, season, COUNT(*) c FROM episode WHERE item_id IN (%s) "
                    "GROUP BY item_id, season" % ",".join("?" * len(shows)),
                    sorted(shows)):
                whole[(r["item_id"], r["season"])] = r["c"]
        except Exception:
            return [], []
        finally:
            con.close()
        full, part = [], []
        for where, count in held.items():
            key = "%s-s%d" % where
            (full if count >= whole.get(where, count) else part).append(key)
        for item in shows:
            mine = sum(c for w, c in held.items() if w[0] == item)
            all_of = sum(c for w, c in whole.items() if w[0] == item)
            (full if mine >= all_of else part).append(str(item))
        return full, part

    def shelves_holding(self, key):
        """Each of this viewer's shelves, with whether it holds all, some or none of a title."""
        want = self.spread(key)
        con = local().lib.db()
        try:
            above = {}                             # episode -> (its season, its programme)
            eps = [k for k in want if k.startswith("e")]
            for at in range(0, len(eps), 500):
                part = eps[at:at + 500]
                for r in con.execute("SELECT id, item_id, season FROM episode WHERE id IN (%s)"
                                     % ",".join("?" * len(part)), part):
                    above[str(r["id"])] = ("%s-s%d" % (r["item_id"], r["season"]),
                                           str(r["item_id"]))
            out = []
            for shelf in self.collections():
                held = set(str(k) for k in self.collection_keys(con, shelf))
                if key in held:
                    hit = len(want)
                else:
                    hit = sum(1 for k in want if k in held or (
                        k in above and (above[k][0] in held or above[k][1] in held)))
                out.append({"id": str(shelf.get("id")), "name": str(shelf.get("name") or ""),
                            "mode": str(shelf.get("mode") or "filter"),
                            "state": ("all" if want and hit >= len(want)
                                      else "some" if hit else "none")})
            return out
        finally:
            con.close()

    def shelf_mark(self, shelf, key, on):
        """Put a title on a shelf or take it off. A programme or a season is its episodes,
        and on a filter shelf what the rule already catches is struck out, not unpinned."""
        con = local().lib.db()
        try:
            byrule = (set() if str(shelf.get("mode") or "filter") == "manual"
                      else set(str(k) for k in self.collection_keys(
                          con, dict(shelf, pinned=[], hidden=[]))))
            held = [str(k) for k in self.collection_keys(con, shelf)]
        finally:
            con.close()
        keys = self.spread(key)
        gone = set([key] + keys)
        marked = [k for k in held if k not in gone]
        if on:
            marked = keys + marked
        else:
            # one season off a programme held whole: the mark above splits into its parts
            out = []
            for k in marked:
                if self.covers(k, key):
                    out += [x for x in self.without(k, key) if x not in gone]
                else:
                    out.append(k)
            marked = out
        wanted = set(marked)
        shelf["pinned"] = [k for k in marked if k not in byrule]
        shelf["hidden"] = [k for k in byrule if k not in wanted]

    def marks_state(self, shelf, key):
        """Whether all, some or none of what this key stands for is on that shelf."""
        mine = self.viewer_settings(self.settings_file())
        held = set(str(k) for k in (mine.get(shelf) or []))
        want = self.spread(key)
        if not want:
            return "none"
        hit = sum(1 for k in want if k in held)
        return "all" if hit == len(want) else ("some" if hit else "none")

    @staticmethod
    def requests_file():
        return os.path.join(ROOT, "requests.json")

    @staticmethod
    def read_requests():
        """Everything anybody has asked for, by the key they asked for it under."""
        try:
            with open(Handler.requests_file(), encoding="utf-8") as f:
                said = json.load(f)
            return said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def write_requests(book):
        tmp = Handler.requests_file() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(book, f, indent=2)
            os.replace(tmp, Handler.requests_file())
        except OSError:
            pass

    def asked_for(self, key):
        """What is known about a request for this title, or nothing."""
        return self.read_requests().get(str(key)) or None

    def collections(self):
        """This viewer's named shelves, in the order they were made."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        self.casual_becomes_a_shelf(stored, mine)
        out = []
        for one in (mine.get("collections") or []):
            if isinstance(one, dict) and one.get("id"):
                out.append(one)
        return out

    def casual_becomes_a_shelf(self, stored, mine):
        """What somebody marked for casual watching, as a shelf of its own.

        Casual was a second list beside the collections and is now a way of playing
        one. Somebody who had marked a hundred and eighty episodes should find them
        where the shelves are rather than be asked to mark them again, so the marks
        become a shelf once, keeping the round that went with them.
        """
        marks = [str(k) for k in (mine.get("casual") or [])]
        if not marks or mine.get("casualMoved"):
            return
        shelves = [c for c in (mine.get("collections") or [])
                   if isinstance(c, dict) and c.get("id")]
        cid = "casual"
        if not any(str(c.get("id")) == cid for c in shelves):
            shelves.append({"id": cid, "name": "Casual", "mode": "manual",
                            "pinned": marks, "hidden": []})
        rounds = mine.get("shuffles")
        if not isinstance(rounds, dict):
            rounds = {}
        if cid not in rounds:
            # the round travels with the shelf: the hat it was drawn from is the same
            # hat, and starting somebody over because the name changed is a loss
            order = mine.get("casualOrder") or "random"
            rounds[cid] = {
                "queue": [str(k) for k in
                          ((mine.get("casualQueue") or {}).get(order) or [])],
                "played": [str(k) for k in
                           ((mine.get("casualPlayed") or {}).get(order) or [])],
                "at": dict(mine.get("casualAt") or {}),
                "run": int((mine.get("casualRun") or {}).get(order) or 1),
                "casualStamp": int(mine.get("casualStamp") or 0)}
        mine["collections"] = shelves
        mine["shuffles"] = rounds
        mine["casualMoved"] = True
        # Written down, not only handed back. Reading the settings a second time to
        # write them fetches a fresh copy without the shelf in it, so the file kept
        # being written exactly as it was - the shelf existed in every answer and in
        # no file, and the row on Continue watching had no name to show for it.
        write_settings(stored)

    def save_collections(self, shelves):
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        mine["collections"] = shelves[:60]
        write_settings(stored, merge=False)

    @staticmethod
    def collection_keys(con, shelf):
        """The keys a shelf holds: what its rule matches, plus and minus by hand.

        A rule is words in the title, and any of the usual narrowings - films or
        programmes, a genre, a span of years. "alien" catches four films and misses
        the two that do not say so, which is what the by-hand list is for.
        """
        rule = shelf.get("rule") or {}
        # Two kinds of shelf. A filter one keeps looking: whatever the library gains
        # that answers the rule joins it without being asked. A manual one holds
        # what it was given and nothing else - the rule is not consulted at all, so
        # a film added tomorrow cannot wander into it.
        if str(shelf.get("mode") or "filter") == "manual":
            hidden = set(str(k) for k in (shelf.get("hidden") or []))
            return [str(k) for k in (shelf.get("pinned") or [])
                    if str(k) not in hidden]
        # Each entry is one rule; a title joins the shelf if it answers any of them.
        # Within a rule every word must appear, in any order and anywhere in the
        # title - so "star wars" is narrower than "star", and "alien | predator" is
        # two rules rather than one long one.
        # Punctuation is dropped from both sides before they are compared. A rule
        # typed as "Its Always Sunny" found nothing at all, because the apostrophe in
        # the title means "its" is not inside "it's" - and nobody types the
        # apostrophe when they are naming a shelf.
        def plain(said):
            # dropped, not turned into a gap: an apostrophe spaced out leaves "it s",
            # which the word somebody typed is no more inside than it was before
            kept = "".join(c for c in str(said or "").lower()
                           if c.isalnum() or c.isspace())
            return " ".join(kept.split())

        words = [plain(w) for w in (rule.get("words") or []) if str(w).strip()]
        rules = [[part for part in one.split() if part] for one in words if one]
        # and the other half of the rule: what the words above catch and should not.
        # Read the same way - every word in a rule must appear - and a title answering
        # any of them stays out, whatever the include rules said.
        bans = [plain(w) for w in (rule.get("without") or []) if str(w).strip()]
        blocked = [[part for part in one.split() if part] for one in bans if one]
        # A programme joins whole unless a span says which part of it. The span is
        # read per episode, and the seasons it touches are what the shelf shows.
        import pd_seasons
        spans = pd_seasons.parse(rule.get("season") or "")
        # and the part of that span to leave out, so "all of it except season 2" is
        # one rule rather than nine
        nope = pd_seasons.parse(rule.get("seasonWithout") or "")
        kind = (rule.get("type") or "").strip()
        genres = {g.strip().lower() for g in str(rule.get("genre") or "").split(",")
                  if g.strip()}
        early = int(rule.get("from") or 0)
        late = int(rule.get("to") or 0)
        found = []
        here = set()
        if words or kind or genres or early or late:
            sql = "SELECT id, title, sort_title, year, genres, type FROM item WHERE 1=1"
            args = []
            if kind in ("movie", "show"):
                sql += " AND type=?"
                args.append(kind)
            if early:
                sql += " AND year >= ?"
                args.append(early)
            if late:
                sql += " AND year <= ?"
                args.append(late)
            for row in con.execute(sql + " ORDER BY sort_title", args):
                name = plain(row["title"])
                if rules and not any(all(part in name for part in one)
                                     for one in rules):
                    continue
                if any(all(part in name for part in one) for one in blocked):
                    continue
                # several genres, comma-joined: the title carries all of them
                if not genres <= {g.strip().lower()
                                  for g in (row["genres"] or "").split(",")}:
                    continue
                # A span only means anything to a programme; a film has no
                # seasons and is left exactly as it was.
                if (spans or nope) and row["type"] == "show":
                    def inside(sn, n):
                        if spans and not pd_seasons.holds(spans, sn, n):
                            return False
                        return not (nope and pd_seasons.holds(nope, sn, n))
                    bysn = {}
                    for r in con.execute(
                            "SELECT id, season, number FROM episode WHERE item_id=? "
                            "ORDER BY season, number", (row["id"],)):
                        bysn.setdefault(int(r["season"] or 0), []).append(
                            (int(r["number"] or 0), str(r["id"])))
                    kept = False
                    for sn in sorted(bysn):
                        eps = bysn[sn]
                        mine = [key for n, key in eps if inside(sn, n)]
                        if not mine:
                            continue
                        kept = True
                        if len(mine) == len(eps):
                            # the shape the rest of the program already uses for a
                            # season - spread(), covers() and the marks all read it
                            found.append("%s-s%d" % (row["id"], sn))
                        else:
                            # part of a season is the episodes themselves: a span
                            # ending at s6e2 that put the whole of season six on the
                            # shelf is not the span that was written
                            found += mine
                    # and the seasons only a pack has: a span over a programme
                    # this machine holds a dozen episodes of was read against those
                    # twelve alone, so excluding the last few seasons left seven
                    # tiles rather than the rest of the series.
                    for s_o in local()._offered_seasons_of(row["title"], set(bysn)):
                        sn = int(s_o.get("index") or 0)
                        nums = [int(e.get("index") or 0) for e in
                                local()._offered_episodes_of(row["title"], sn, set())]
                        if any(inside(sn, n) for n in nums):
                            found.append("%s-s%d" % (row["id"], sn))
                            kept = True
                    if not kept:
                        continue
                    here.add((name, int(row["year"] or 0)))
                    continue
                found.append(str(row["id"]))
                # what the library already holds, so a pack offering the same
                # programme does not put it on the shelf a second time
                here.add((name, int(row["year"] or 0)))
            # films on offer from a torrent pack, by the same rule: they join the shelf
            # greyed until they are here
            if kind in ("", "movie", "show"):
                try:
                    import pd_torrents
                    offers = []
                    if kind in ("", "movie"):
                        offers += pd_torrents.offered()
                    # and the programmes a pack can give. A shelf matched the library
                    # and the films on offer, so a series that only a pack has could
                    # not join one - it is on the shelf everywhere else it is listed.
                    if kind in ("", "show"):
                        offers += pd_torrents.offered_shows()
                except Exception:
                    offers = []
                for one in offers:
                    name = plain(one.get("title"))
                    year = int(one.get("year") or 0)
                    if (early and (not year or year < early)) or (late and (not year or year > late)):
                        continue
                    if rules and not any(all(part in name for part in r) for r in rules):
                        continue
                    if any(all(part in name for part in r) for r in blocked):
                        continue
                    if not genres <= {g.strip().lower() for g in one.get("genres") or []}:
                        continue
                    # The Simpsons stood on a shelf twice: once as the programme in
                    # the library and once as the same programme a pack was offering.
                    # An offer is only worth showing for something not already here.
                    if (name, year) in here or any(n == name for n, _y in here):
                        continue
                    found.append(str(one["ratingKey"]))
        # by hand: what the rule missed, and what it should not have caught
        hidden = set(str(k) for k in (shelf.get("hidden") or []))
        for key in (shelf.get("pinned") or []):
            if str(key) not in found:
                found.append(str(key))
        return [k for k in found if k not in hidden]

    def watchlist(self):
        """The keys this viewer has marked, newest first."""
        mine = self.viewer_settings(self.settings_file())
        return [str(k) for k in (mine.get("watchlist") or [])]

    def viewer_language(self):
        """Which language this viewer wants subtitles in."""
        mine = self.viewer_settings(self.settings_file())
        return (mine.get("subLang") or "en").lower()[:5]

    def set_viewer_language(self, code):
        code = re.sub(r"[^a-z-]", "", (code or "en").lower())[:5] or "en"
        stored = self.settings_file()
        self.viewer_settings(stored)["subLang"] = code
        write_settings(stored, merge=False)
        return code

    def scope_key(self, key):
        """Which title an exception is really about.

        Clients name a title in their own way - "le11155" from the app, a metadata
        path from the browser - and an episode always stands for its series here.
        """
        text = str(key or "")
        found = re.search(r"(?:^|[/l])e([0-9a-f]{12})", text)
        if found:
            show = self.show_of("e" + found.group(1))
            if show:
                return "show|" + show
        season = re.search(r"([0-9a-f]{12})-s\d+", text)
        if season:
            return "show|" + season.group(1)
        # a programme's own page names the programme, which is the scope its episodes read
        whole = re.search(r"(?:^|[/l])([0-9a-f]{12})$", text)
        if whole:
            con = local().lib.db()
            try:
                row = con.execute("SELECT type FROM item WHERE id=?",
                                  (whole.group(1),)).fetchone()
            finally:
                con.close()
            if row and row["type"] == "show":
                return "show|" + whole.group(1)
        return text

    def everyones_subtitles(self):
        """How each viewer here has their subtitles drawn, by the key they are known by.

        The owner under "me", everybody else under their token. Sent to the machine
        that keeps copies so an evening carried over mid-film looks the same.
        """
        stored = read_settings() or {}
        out = {}

        def bit(one):
            said = {}
            for name in ("subtitles", "perTitle", "subLanguage", "language"):
                if one.get(name):
                    said[name] = one[name]
            return said

        mine = bit(stored)
        if mine:
            out["me"] = mine
        for token, one in (stored.get("users") or {}).items():
            said = bit(one or {})
            if said:
                out[str(token)] = said
        return out

    def subtitle_settings(self, key=None, device="web"):
        """The look for one screen, with that title's exceptions on top of it."""
        device = self.device_of(device)
        mine = self.viewer_settings(self.settings_file())
        out = dict(self.SUBTITLE_DEFAULTS[device])
        kept = mine.get("subtitles", {}) or {}
        # an older file held one flat set for everything; it becomes the starting
        # point for all three rather than being thrown away
        out.update(kept.get(device, kept if "size" in kept else {}))
        if key:
            out.update((mine.get("perTitle", {}) or {})
                       .get(device + "|" + self.scope_key(key), {}))
        return out

    def has_override(self, key, device="web"):
        per = self.viewer_settings(self.settings_file()).get("perTitle", {}) or {}
        return (self.device_of(device) + "|" + self.scope_key(key)) in per

    def save_subtitle_settings(self, body, key=None, device="web"):
        """Clamped on the way in: a client cannot ask for text three screens tall.

        With a key, only what differs from that screen's default is written - so
        raising the default size later still raises it for a film that only disagreed
        about colour.
        """
        device = self.device_of(device)
        cur = self.subtitle_settings(key, device)
        try:
            if "size" in body:
                cur["size"] = round(min(1.8, max(0.7, float(body["size"]))), 2)
            if "position" in body:
                asked = float(body["position"])
                # 0.9 and over is "very bottom", which both players draw at the floor
                cur["position"] = (0.99 if asked >= 0.9
                                   else round(min(0.4, max(0.0, asked)), 3))
            if "font" in body:
                # the faces on offer; anything else is somebody guessing
                name = str(body["font"]).lower()
                if name in ("sans", "serif", "condensed", "rounded", "mono"):
                    cur["font"] = name
            if "shift" in body:
                # seconds, either way: a subtitle cut for another release is usually
                # out by a constant amount rather than drifting
                cur["shift"] = round(min(120.0, max(-120.0, float(body["shift"]))), 2)
            if "colour" in body:
                name = str(body["colour"]).lower()
                if name in ("white", "yellow", "cyan", "green", "grey"):
                    cur["colour"] = name
            if "base" in body:
                # what a height is measured from. Only these two, and anything else is
                # a client with an idea of its own.
                name = str(body["base"]).lower()
                if name in ("screen", "picture"):
                    cur["base"] = name
            if "background" in body:
                # the four every player settles on: nothing, a shadow, a dark box,
                # or a solid one for a bright picture
                name = str(body["background"]).lower()
                if name in ("none", "shadow", "dark", "black"):
                    cur["background"] = name
        except (TypeError, ValueError):
            pass
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        kept = mine.get("subtitles", {}) or {}
        if "size" in kept:                        # migrate the old flat shape
            kept = {d: dict(kept) for d in self.DEVICES}
        if key:
            base = dict(self.SUBTITLE_DEFAULTS[device])
            base.update(kept.get(device, {}))
            differs = {k: v for k, v in cur.items() if base.get(k) != v}
            # a height means nothing without what it is measured from: kept as a pair
            if "position" in differs or "base" in differs:
                differs["position"], differs["base"] = cur["position"], cur["base"]
            per = mine.get("perTitle", {}) or {}
            slot = device + "|" + self.scope_key(key)
            if differs:
                per[slot] = differs
            else:
                per.pop(slot, None)               # back in step with the default
            mine["perTitle"] = per
            mine["subtitles"] = kept
        else:
            kept[device] = cur
            mine["subtitles"] = kept
        write_settings(stored, merge=False)
        return cur

    @staticmethod
    def shifted_look(look, shift):
        """The same look, raised - for a client that is cropping the bottom away."""
        try:
            extra = max(0.0, min(0.4, float(shift)))
        except (TypeError, ValueError):
            return look
        if extra <= 0:
            return look
        out = dict(look)
        out["position"] = min(0.45, float(out.get("position", 0.08)) + extra)
        return out

    def clear_override(self, key, device="web"):
        """Forget a title's exceptions on this screen; it follows the default again."""
        device = self.device_of(device)
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        per = mine.get("perTitle", {}) or {}
        per.pop(device + "|" + self.scope_key(key), None)
        mine["perTitle"] = per
        write_settings(stored, merge=False)
        return self.subtitle_settings(None, device)

    # ---- subtitles from OpenSubtitles ---------------------------------------

    def opensubtitles(self):
        """A client, or None with a reason - the key is the application's, not a user's."""
        cfg = local().lib.config()
        if not (cfg.get("opensubtitles_key") or "").strip():
            return None, ("No OpenSubtitles API key yet. One is needed for the "
                          "application as a whole - Settings, Library.")
        import pd_subs
        return pd_subs.OpenSubtitles(cfg.get("opensubtitles_key"),
                                     cfg.get("opensubtitles_user"),
                                     cfg.get("opensubtitles_pass")), ""

    def _title_for_search(self, key):
        """What to ask OpenSubtitles for, and which file it is about."""
        src = local().file_for(key, 0)
        con = local().lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT e.season, e.number, i.title, i.year FROM episode e "
                    "JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (str(key),)).fetchone()
                if not row:
                    return None, None, None, None, None
                # Asked for by the number the release carries, not by ours. A season
                # whose premiere is one double-length episode is numbered one behind
                # everything the world outside says: our 28 is their 29, and asking
                # for 28 fetched the subtitle of the episode before this one - which
                # is exactly the fault the renumbering exists to prevent, arriving by
                # another door.
                import pd_localapi
                shift = pd_localapi.LocalAPI.numbering_shift(
                    (src or {}).get("file"), row["season"], row["number"]) or 0
                return (src, row["title"], row["year"], row["season"],
                        row["number"] + shift)
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return None, None, None, None, None
            return src, row["title"], row["year"], None, None
        except (TypeError, ValueError):
            return None, None, None, None, None
        finally:
            con.close()

    def imdb_for(self, key):
        """The IMDb number of the title behind this key - the film, or the series."""
        con = local().lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT i.imdb_id FROM episode e JOIN item i ON i.id = e.item_id "
                    "WHERE e.id=?", (str(key),)).fetchone()
            else:
                row = con.execute("SELECT imdb_id FROM item WHERE id=?",
                                  (str(key),)).fetchone()
            return (row["imdb_id"] or "") if row else ""
        except (TypeError, ValueError):
            return ""
        finally:
            con.close()

    def whole_episode(self, video, sides, lang):
        """Two subtitle files for one double episode, joined into one.

        Two programmes in one file are subtitled as two files, each timed from its own
        zero. Whichever is chosen, half the episode has no subtitles and the other half
        has them at the wrong time - which looks like the subtitle simply stopping.

        The join is found by looking for the black frames between the two parts, and
        remembered, because looking costs a few seconds of ffmpeg. Anything that does
        not look plainly like a split episode is left alone: this returns nothing and
        the file is served as it is.
        """
        wanted = [s for s in sides
                  if (s.get("lang") or "und") == lang or lang == "und"]
        if len(wanted) < 2 or len(wanted) > 4:
            return None
        texts, spans = [], []
        for s in wanted:
            text = one_language(as_vtt(s["file"]), lang)
            span = vtt_span(text)
            if not span:
                return None
            texts.append(text)
            spans.append(span)
        length = film_length(video)
        if length < 60:
            return None
        # every part must start near its own beginning and cover a fraction of the
        # film, and together they must come to roughly the whole of it
        if any(a > 120 for a, _ in spans):
            return None
        if any(b > length * 0.8 for _, b in spans):
            return None
        covered = sum(b - a for a, b in spans)
        if not (length * 0.55 < covered < length * 1.05):
            return None
        held = (self.settings_file() or {}).get("splitSubs") or {}
        mark = video + "|" + str(int(len(texts)))
        offsets = held.get(mark)
        if not isinstance(offsets, list) or len(offsets) != len(texts):
            offsets, at = [0.0], spans[0][1]
            for i in range(1, len(texts)):
                join = black_after(video, at)
                if not join:
                    return None               # nothing found: do not guess
                offsets.append(join)
                at = join + spans[i][1]
            stored = self.settings_file()
            held = stored.get("splitSubs") or {}
            held[mark] = offsets
            stored["splitSubs"] = held
            write_settings(stored)
        out = []
        for text, offset in zip(texts, offsets):
            body = shift_vtt(text, float(offset))
            out.append(re.sub(r"^WEBVTT\s*", "", body).strip())
        gap = chr(10) + chr(10)
        return "WEBVTT" + gap + gap.join(b for b in out if b) + chr(10)

    def serve_sidecar(self, video, n, shift=0.0, offset=0.0):
        """The nth subtitle file beside a video, as WebVTT - the one form every
        player accepts, browser and television alike.

        A shift moves every cue by that many seconds, for a subtitle cut to a different
        release: the browser can do this as it draws, but a player being handed a file
        needs the file itself to be right.
        """
        try:
            import pd_localapi as _la
            side = _la.sidecars(video)[n]
            with open(side["file"], "rb") as f:
                raw = f.read()
        except Exception:
            self.send_error(404, "no such subtitle file")
            return
        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        # a file with two languages in it draws both at once, one line under the
        # other: keep the one it says it is
        said = one_language(srt_to_vtt(text or ""), side.get("lang") or "en")
        # two files for one double episode are served as one, in step with the picture,
        # rather than half an episode of subtitles and then nothing
        try:
            whole = self.whole_episode(video, _la.sidecars(video),
                                       side.get("lang") or "und")
        except Exception:
            whole = None
        if whole:
            said = whole
        # a subtitle that drifts is served already straightened: a client holds one
        # number, and one number cannot describe a drift. This comes after the joining,
        # which builds its text from the files afresh - applying it first threw it away.
        fitted = self.sub_fit(side["file"])
        if fitted:
            said = mend_vtt(said, fitted)
        # the shift is about this release; the offset is about where the stream
        # being watched begins
        body = cut_vtt(shift_vtt(said, shift), offset).encode("utf-8")
        if not body.strip():
            self.send_error(404, "that subtitle file is empty")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Still being written: the machine is listening to the film and the file grows
        # as it goes. Both clients know this header - it is what they are told while a
        # track is being lifted out of a container - and they ask again as the picture
        # nears the end of what they hold, so a subtitle can be watched from the start
        # while the rest of it is still being made.
        if self.being_written(side.get("file", "")):
            self.send_header("X-Palladium-Subs", "partial")
            self.send_header("X-Palladium-Subs-Until", str(int(last_cue(body))))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def being_written(path):
        """Whether a subtitle file is the one a job is writing at this moment."""
        if not path:
            return False
        try:
            import pd_ai_subs
            on = pd_ai_subs.look().get("on") or {}
            return os.path.normcase(on.get("out", "")) == os.path.normcase(path)
        except Exception:
            return False

    #: What the last look at a file's sound produced, so a second subtitle for the same
    #: film is placed without decoding it again. Small: a film is a few thousand bits.
    HEARD = {}
    HEARD_LOCK = threading.Lock()

    def subtitle_text(self, video, index, key="", mi=0):
        """One subtitle as WebVTT, wherever it lives - beside the film or inside it."""
        if index < 0:
            import pd_localapi as _la
            side = _la.sidecars(video)[-index - 1]
            with open(side["file"], "rb") as f:
                raw = f.read()
            text = ""
            for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            said = one_language(srt_to_vtt(text), side.get("lang") or "en")
            try:
                whole = self.whole_episode(video, _la.sidecars(video),
                                           side.get("lang") or "und")
            except Exception:
                whole = None
            return whole or said
        # Lifting a track out of a film means reading the whole film: a minute and a
        # half for a UHD disc rip. Worth remembering, since placing a second subtitle
        # of the same film would pay it again.
        # the same copy the player is given, kept on disk and pulled once
        return pull_subtitle(video, index)

    @staticmethod
    def owner_caching():
        """The owner's own answers, kept where the owner's settings are.

        An owner with a key of their own is a row in the invitations like anybody
        else, and their answers are kept there - otherwise the same person appears
        twice on the page and is counted twice by the machine keeping copies.
        """
        stored = read_settings() or {}
        mine = str(stored.get("ownerIs") or "")
        if mine:
            row = next((r for r in INVITES.load() if r.get("token") == mine), {})
            return {"cacheDeck": bool(row.get("cacheDeck")),
                    "cacheList": bool(row.get("cacheList")),
                    "cacheCasual": bool(row.get("cacheCasual")),
                    "syncGbWeek": float(row.get("syncGbWeek") or 0),
                    "downloadGbWeek": float(row.get("downloadGbWeek") or 0),
                    "token": mine}
        return {"cacheDeck": bool(stored.get("cacheDeck", True)),
                "cacheList": bool(stored.get("cacheList", False)),
                # what the shuffle would put on next: an evening of casual watching
                # is exactly the evening nobody chooses a film for
                "cacheCasual": bool(stored.get("cacheCasual", False)),
                "syncGbWeek": float(stored.get("syncGbWeek") or 0),
                "downloadGbWeek": float(stored.get("downloadGbWeek") or 0)}

    #: What game mode may hold back, and what it holds back unless told otherwise.
    #: Encoding is off by default: stopping it turns somebody's film off, which is a
    #: heavier thing than making them wait for a subtitle.
    GAME_DOES = {"subs": True, "scans": True, "copies": True, "transcode": False}

    @classmethod
    def game_mode(cls):
        """What the machine has been asked to leave alone, as {on, subs, scans, ...}.

        An older setting was a plain yes or no; it is read as everything but encoding.
        """
        said = (read_settings() or {}).get("gameMode")
        if isinstance(said, dict):
            out = dict(cls.GAME_DOES, **{k: bool(v) for k, v in said.items()
                                         if k in cls.GAME_DOES})
            out["on"] = bool(said.get("on"))
            return out
        return dict(cls.GAME_DOES, on=bool(said))

    #: this machine, as a name that survives a change of address. Made once from
    #: the settings and kept: two addresses answering with the same id are two doors
    #: into one house, and a client that cannot tell is a client showing it twice.
    ID = {"was": ""}

    @classmethod
    def machine_id(cls):
        if cls.ID["was"]:
            return cls.ID["was"]
        stored = read_settings() or {}
        was = str(stored.get("machineId") or "")
        if not was:
            was = hashlib.sha256(
                    ("%s|%s" % (socket.gethostname(), PORT)).encode()
                ).hexdigest()[:12]
            stored["machineId"] = was
            write_settings(stored)
        cls.ID["was"] = was
        return was

    def imposed_skin(self):
        """The look this machine puts on by itself, if any, and why.

        Neither is a preference. "arcade" beats "night": a machine that has given its
        card to a game is doing something else entirely, and that is worth saying at
        any hour.
        """
        stored = read_settings() or {}
        if self.game_mode().get("on"):
            return "arcade", "the card is busy"
        one = stored.get("follow") or {}
        if one.get("on") and one.get("master"):
            # a machine that follows another is a machine that keeps copies
            frm = int(one.get("nightFrom") or 22)
            to = int(one.get("nightTo") or 8)
            hour = time.localtime().tm_hour
            dark = (frm <= hour or hour < to) if frm > to else (frm <= hour < to)
            if dark:
                return "night", "the night server, after dark"
        return "", ""

    def hand_subs_over(self, key, want):
        """Ask the server this one follows to write the subtitle instead.

        Returns its answer, or None if there is nobody to ask. The film is named by
        what it is rather than by a number, because the other machine files it under
        its own; and the file it writes lands beside its own copy, which is where
        this machine fetches everything else from.
        """
        one = (read_settings() or {}).get("follow") or {}
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        con = local().lib.db()
        try:
            what = local().what_it_is(con, key)
        finally:
            con.close()
        if not what:
            return None
        try:
            where = one["master"].rstrip("/")
            # by key first: both machines derive the same key from title and year,
            # while names differ because a follower names files from their filenames
            theirs = ""
            try:
                with urllib.request.urlopen(
                        where + "/local/library/metadata/" + urllib.parse.quote(str(key))
                        + "?t=" + urllib.parse.quote(one["key"]), timeout=20) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace"))
                if ((said.get("MediaContainer") or {}).get("Metadata") or []):
                    theirs = str(key)
            except Exception:
                theirs = ""
            if theirs:
                found = {}
            else:
                ask = urllib.parse.urlencode(
                    {k: v for k, v in what.items() if v not in (None, "")})
                with urllib.request.urlopen(
                        where + "/local/library/find?" + ask + "&t=" +
                        urllib.parse.quote(one["key"]), timeout=20) as answer:
                    found = json.loads(answer.read().decode("utf-8", "replace"))
                theirs = ((found.get("MediaContainer") or {}).get("key") or "")
            if not theirs:
                return None
            req = urllib.request.Request(
                where + "/subs/make?t=" + urllib.parse.quote(one["key"]),
                data=json.dumps({"key": theirs, "language": want}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace"))
        except Exception as e:
            return {"error": "Asked the main server server and it did not answer: " +
                             str(e)[:90]}
        said["handed"] = True
        said["where"] = one["master"]
        return said

    def may_have_the_lan(self):
        """Whether this asker is told the address on this network.

        The owner always. A guest only if their invitation says so: the address of a
        machine inside somebody's house is not a secret worth guarding hard, but it
        is theirs, and handing it to everyone who ever received a link is a decision
        rather than an accident. A guest without it still has the way in from
        outside, which is the one that works from where they are.
        """
        if self.role == "owner":
            return True
        invite = INVITES.check(self.bearer(), self.app_name())
        if not invite:
            return False
        if invite.get("follows"):
            return True                    # the main server's own second machine
        return bool(invite.get("shareLan", True))

    def follower_stopped(self):
        """Whether this caller is a machine the owner has stopped.

        Matched on the address the request arrives from rather than the one the
        machine claims, because a claim is the one thing a stopped machine would
        change.
        """
        try:
            blocked = local().lib.config().get("blockedCaches") or []
        except Exception:
            return False
        if not blocked:
            return False
        here = self.client_address[0]
        for one in blocked:
            host = urllib.parse.urlsplit(str(one)).hostname or str(one).strip()
            if host and host == here:
                return True
        return False

    def follows_here(self):
        """The invitation of a machine allowed to follow this one, or None."""
        invite = INVITES.check(self.bearer(), self.app_name())
        if not (invite and invite.get("follows")):
            return None
        return None if self.follower_stopped() else invite

    def everyone_here(self):
        """Every name this house knows, for taking out of anything that leaves it."""
        stored = read_settings() or {}
        names = [str(stored.get("ownerName") or "")]
        try:
            names += [str(r.get("name") or "") for r in INVITES.load()]
        except Exception:
            pass
        # once each: the owner's name is also an invitation name when somebody has
        # a key of their own, and the list is read by people
        seen, out = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def tell_the_site(self, row, forced=False):
        """Hand one fault to palladium.video, and write down what came of it.

        (sent, why). The row is marked sent only once the site has taken it, and
        carries the reason when it would not go - a machine with no way out says so
        on the report rather than showing 115 faults it thinks it has sent.
        """
        ident = str(row.get("id") or "")

        def wrote(ok, why):
            if not ident:
                return
            if ok:
                self.amend_report(ident, {"sentAway": int(time.time()), "sendWhy": ""})
            else:
                self.amend_report(ident, {"sendWhy": str(why or "")[:160],
                                          "sendTried": int(time.time())})

        try:
            import pd_faults
            # read where the page writes it: with the machine's own settings, not
            # the viewer's - what a computer sends about itself is the computer's
            how = str(local().lib.config().get("sendFaults") or pd_faults.OFF)
            ok, why = pd_faults.send(row, how, build=self.build_version(),
                                     app=str(row.get("app") or ""),
                                     called=self.server_name(), forced=forced,
                                     people=self.everyone_here(), then=wrote)
        except Exception as e:
            ok, why = False, str(e)[:160]
        if forced:
            wrote(ok, why)
        return ok, why

    @staticmethod
    def server_name():  # noqa: D401
        """What this machine calls itself.

        The same answer /config gives, which is the computer's own name: it was only
        ever put together there, so everything else that asked - the drawing, the
        server list on a phone - looked in the settings file, found nothing, and fell
        back to printing an address at somebody.
        """
        told = ""
        try:
            told = str(local().lib.config().get("serverName") or "").strip()
        except Exception:
            told = ""
        if not told:
            told = str((read_settings() or {}).get("serverName") or "").strip()
        if told:
            return told
        try:
            return socket.gethostname()
        except Exception:
            return ""

    def sharing_with_copy(self, path):
        """Whether to read part of this file off the other machine, and how.

        Off unless somebody sets a share. Worth having when the two machines are
        comparable and worth nothing when they are not - a copy a tenth the speed
        adds a tenth - so it is a number somebody chooses, not a guess this makes.
        """
        try:
            share = int(local().lib.config().get("shareWithCopy") or 0)
        except (TypeError, ValueError):
            return None
        if share <= 0:
            return None
        one = self.standby_now()
        where = (one.get("where") or "").rstrip("/")
        if not where or not one.get("alive"):
            return None
        token = ""
        for row in INVITES.load():
            if row.get("follows") and row.get("token"):
                token = row["token"]
                break
        if not token:
            return None
        mark = self.quick_mark(path)
        if not mark:
            return None
        return (min(share, 90), where, token, os.path.basename(path), mark)

    #: The cache each following machine is being sent this moment, by address. One
    #: file at a time is what the cache asks for, but a fetch it gives up on leaves
    #: this side still sending - so two, three, four files went out at once, each
    #: taking its share of the line and the air, for a machine that had stopped
    #: listening to all but the last.
    SYNC_NOW = {}
    #: What each machine has carried for a viewer, so the screen can say so:
    #: {who: (bytes from here, bytes from the cache, when)}
    CARRIED = {}
    #: Above this share of the time spent getting blocks off the disk rather than
    #: handing them on, the disk is what is holding the film up. Below the lower
    #: figure it is comfortably ahead and the cache is worth nothing.
    DISK_BUSY = 0.35
    DISK_EASY = 0.15

    #: share of read time spent waiting on disk, and when it was measured
    DISK = {"busy": 0.0, "when": 0.0}

    def share_by_reads(self, now, most, disk, wrote):
        """How much of the reading the cache should do, from how this machine is faring.

        Measured here rather than asked of the viewer. What a screen has left to play
        was the old answer and it is not one: a browser keeps about two seconds in
        front of itself however well it is fed, so it read as running out for the
        whole of every film - and nothing ever sent the figure anyway.

        What does say something is time. Getting a block off this disk against handing
        it on: a disk keeping well ahead of the line is worth nothing to share, and one
        the line is waiting on is worth every block the cache will take.
        """
        both = disk + wrote
        if both <= 0:
            return now
        busy = disk / both
        # recorded for the sync ceiling: a copy reads the same disks, so this is an
        # earlier signal than a viewer's buffer running down
        Handler.DISK = {"busy": busy, "when": time.time()}
        if busy > Handler.DISK_BUSY:
            return min(most, now + 10)
        if busy < Handler.DISK_EASY:
            return max(0, now - 5)
        return now

    def block_from_copy(self, share, at, want):
        """One block off the other machine, or nothing if it is not quick about it.

        Nothing is retried and nothing is waited for: this is a way of spreading the
        reading, not a way of getting the film. A block that does not come is read
        off this disk instead, which is where it would have come from anyway.
        """
        _, where, token, name, mark = share
        url = ("%s/copy/read?name=%s&mark=%s&t=%s"
               % (where, urllib.parse.quote(name), urllib.parse.quote(mark),
                  urllib.parse.quote(token)))
        try:
            req = urllib.request.Request(
                url, headers={"Range": "bytes=%d-%d" % (at, at + want - 1),
                              "X-Palladium-App": "house"})
            with urllib.request.urlopen(req, timeout=4) as answer:
                got = answer.read(want)
            return got if got else None
        except Exception:
            return None

    def copy_holds(self, name, mark):
        """The file of that name in the cache folder, if it is the same file.

        The mark is the size and both ends: two rips never agree on it, and a copy
        that arrived half-written does not agree with itself. Without this a request
        for a name could be answered with a different film of the same name.
        """
        import pd_follow
        try:
            one = pd_follow.settings(local().lib.config())
        except Exception:
            return ""
        folder = (one.get("folder") or "").strip()
        safe = pd_follow.a_safe_name(name)
        if not folder or not safe or not pd_follow.a_sane_folder(folder):
            return ""
        here = os.path.join(folder, safe)
        if not pd_follow.inside(folder, here) or not os.path.exists(here):
            return ""
        if mark and self.quick_mark(here) != mark:
            return ""
        return here

    def house_doors(self):
        """Both addresses of the machine this one follows, if it follows one.

        Read from the library's settings, where the following is set up. It was read
        from this viewer's own settings file, which has never held it - so a machine
        that follows another answered that it followed nobody, and a page opened on
        it had no way back to the main server.
        """
        try:
            one = local().lib.config().get("follow") or {}
        except Exception:
            one = (read_settings() or {}).get("follow") or {}
        if not (one.get("on") and one.get("master")):
            return {}
        try:
            import pd_follow
            said = pd_follow.house_doors()
        except Exception:
            said = {}
        # what it was told, or failing that the address it was set up with
        if not (said.get("lan") or said.get("outside")):
            return {"lan": str(one.get("master") or ""), "outside": "", "name": ""}
        return said

    def engine_name(self):
        """What this machine encodes with, in two words, or nothing at all.

        A spare box with no card and no ffmpeg encodes with nothing, and whatever is
        playing from it should not be told otherwise.
        """
        try:
            said = engine().status() or {}
        except Exception:
            return ""
        if not said.get("ffmpeg"):
            return ""
        if said.get("nvenc"):
            return "NVENC"
        return str(said.get("engine") or "CPU")

    def mood_now(self):
        """The whole look a screen should wear, in one answer.

        The colours travel rather than being written into three clients in two
        languages: a screen asks what to paint with and paints. `chosen` is what this
        viewer picked and `look` is what is actually in force - they differ when the
        machine has put something on by itself, and a look nobody chose should say so.
        """
        import pd_skins
        stored = read_settings() or {}
        mine = self.viewer_settings(stored)
        chosen = pd_skins.known(mine.get("skin") or pd_skins.DEFAULT)
        put_on, why = self.imposed_skin()
        wearing = pd_skins.one(put_on or chosen)
        # The colour this viewer draws with stands where a skin does not name one -
        # the same one the rest of the settings answer with, not the machine's
        # default, or a viewer who picked their own would lose it to a look that
        # asked for nothing.
        if not wearing.get("accent"):
            wearing["accent"] = self.accent_now()
        return dict(wearing, look=wearing["id"], chosen=chosen, imposed=bool(put_on),
                    why=why, name=self.server_name())

    @classmethod
    def game_holds(cls, what):
        """Whether game mode is on and holding this kind of work back."""
        game = cls.game_mode()
        return bool(game["on"] and game.get(what))

    #: Films the machine keeping copies fetched for itself while this one was off,
    #: waiting to be copied back. Named by the pack and the file inside it, because
    #: that is what decides where it has to sit for qBittorrent to know it.
    FROM_COPY = []
    FROM_COPY_LOCK = threading.Lock()
    FROM_COPY_BUSY = [False]

    @classmethod
    def take_from_the_copy(cls):
        """Fetch back what the other machine downloaded while this one was away.

        Over the house network rather than off the internet a second time, into the
        place this pack expects, and then qBittorrent is asked to look again - so the
        film is seeded from here too instead of being fetched all over again. One at a
        time and never more than one thread: this is several gigabytes of somebody
        else's evening and nothing is waiting on it.
        """
        with cls.FROM_COPY_LOCK:
            if cls.FROM_COPY_BUSY[0]:
                return
            cls.FROM_COPY_BUSY[0] = True

        def work():
            import pd_torrents
            try:
                while True:
                    with cls.FROM_COPY_LOCK:
                        if not cls.FROM_COPY:
                            return
                        one = cls.FROM_COPY.pop(0)
                    where = str(one.get("where") or "").rstrip("/")
                    mark, at = str(one.get("hash") or ""), int(one.get("index") or -1)
                    if not where or not mark or at < 0:
                        continue
                    # already here and the right length: nothing to fetch
                    mine = pd_torrents.file_of(mark, at)
                    size = int(one.get("size") or 0)
                    if mine and os.path.exists(mine) and size and                             os.path.getsize(mine) == size:
                        continue
                    url = ("%s/follow/file?hash=%s&index=%d"
                           % (where, urllib.parse.quote(mark), at))
                    try:
                        req = urllib.request.Request(
                            url, headers={"X-Palladium-App": "house"})
                        with urllib.request.urlopen(req, timeout=1800) as answer:
                            got = pd_torrents.place_and_recheck(
                                mark, at, str(one.get("name") or ""), answer, size)
                    except Exception as e:
                        Handler.note_fault(
                            None, "taking back %s: %s" % (one.get("title"), e))
                        continue
                    if got:
                        Handler.note_fault(
                            None, "took back from the copy: %s" % (one.get("title"),))
            finally:
                with cls.FROM_COPY_LOCK:
                    cls.FROM_COPY_BUSY[0] = False

        threading.Thread(target=work, daemon=True).start()

    #: what the graphics card last said about itself, and when
    CARD = {"when": 0.0, "said": {}}

    @classmethod
    def card_now(cls):
        """What the graphics card is doing, if it will say. Asked twice a minute."""
        now = time.time()
        if now - cls.CARD["when"] < 4:
            return cls.CARD["said"]
        said = {}
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,"
                 "temperature.gpu,name", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            bits = (out.stdout or b"").decode("utf-8", "replace").strip().split(",")
            if len(bits) >= 5:
                said = {"busy": int(bits[0]), "used": int(bits[1]),
                        "total": int(bits[2]), "hot": int(bits[3]),
                        "name": bits[4].strip()}
        except Exception:
            said = {}
        cls.CARD.update(when=now, said=said)
        return said

    def how_busy(self):
        """Everything running on this machine that a person would feel."""
        import pd_ai_subs
        streams = []
        try:
            for one in (engine().status() or {}).get("streams", []):
                streams.append({"key": one.get("key"), "height": one.get("height"),
                                "running": one.get("running"),
                                "burn": bool(one.get("burn_index") is not None)})
        except Exception:
            pass
        try:
            import pd_follow
            copying = pd_follow.look()
        except Exception:
            copying = {}
        writing = pd_ai_subs.look()
        lib = local().lib
        return {
            "game": self.game_mode(),
            "does": self.GAME_DOES,
            "card": self.card_now(),
            "streams": streams,
            "writing": {"on": writing.get("on"), "queued": len(writing.get("queued") or [])},
            "scan": dict(lib.scan_state),
            "copying": {"on": copying.get("on"), "file": copying.get("copying"),
                        "at": copying.get("at")},
            "watching": len(local().now_playing().get("Metadata", []) or []),
        }

    #: what belongs to a viewer rather than to the machine. The owner's are kept at
    #: the top level of the settings, mixed in with the server's own, so they move by
    #: name - the port and the library folders are not viewing.
    #: Everything filed under one viewer. Anything missing from this list is left
    #: behind when somebody is named the owner, still in the file and read by
    #: nothing - which is how twelve collections and a front page arrangement came
    #: to vanish on the day their owner took a key of their own.
    VIEWER_KEYS = ("watchlist", "favorites", "casual", "casualMoved", "shuffles",
                   "subtitles", "perTitle", "myAccent", "autoNext", "autoFetch",
                   "autoSync", "watchParty", "subLanguage", "mine", "deckAside",
                   "collections", "homeRows", "skin",
                   # whether a film may be read off two machines at once, and whether
                   # it may move to another machine when the one serving it stops.
                   # Both on unless somebody says otherwise; both belong to the person
                   # watching, not to the machine.
                   "splitPlay", "failover", "dropWatched",
                   # what somebody calls themselves, which is theirs. The name on the
                   # invitation is the owner's record of who they gave a key to and
                   # stays as they wrote it.
                   "myName",
                   # whether a poster stands behind the shelves. Theirs, on every
                   # screen they watch on: the browser, the phone and the television.
                   "myBackdrop")

    def hand_over_history(self, frm, to):
        """Move one viewer's viewing onto another. Returns what moved.

        Naming somebody the owner is saying they are the person who has been using
        this machine; leaving their history behind under the machine's own name would
        make that a lie the first time they opened Continue watching.
        """
        if not frm or not to or frm == to:
            return {"rows": 0, "places": 0}
        con = local().lib.db()
        rows = places = 0
        try:
            rows = con.execute("UPDATE watchlog SET who=? WHERE who=?",
                               (to, frm)).rowcount
            for key, pos, dur, upd, cas in con.execute(
                    "SELECT key, position, duration, updated, "
                    "COALESCE(casual, 0) casual FROM progress "
                    "WHERE who=?", (frm,)).fetchall():
                had = con.execute("SELECT updated FROM progress WHERE who=? AND key=?",
                                  (to, key)).fetchone()
                if had and int(had["updated"] or 0) >= int(upd or 0):
                    continue          # the later note wins, whichever side it is on
                con.execute(
                    """INSERT INTO progress (key, position, duration, updated, who,
                                                casual)
                       VALUES (?,?,?,?,?,?) ON CONFLICT(who, key) DO UPDATE SET
                       position=excluded.position, duration=excluded.duration,
                       updated=excluded.updated, casual=excluded.casual""",
                    (key, pos, dur, upd, to, cas))
                places += 1
            con.execute("DELETE FROM progress WHERE who=?", (frm,))
            con.commit()
        finally:
            con.close()
        stored = read_settings()
        if stored is None:
            return {"rows": rows, "places": places}
        users = stored.setdefault("users", {})
        mine = stored if frm == "me" else (users.get(frm) or {})
        theirs = stored if to == "me" else users.setdefault(to, {})
        for name in self.VIEWER_KEYS:
            if name not in mine:
                continue
            was, now = mine.get(name), theirs.get(name)
            if isinstance(was, list):
                theirs[name] = list(dict.fromkeys(list(now or []) + list(was)))
            elif isinstance(was, dict) and isinstance(now, dict):
                joined = dict(now)
                for k, v in was.items():
                    if isinstance(v, list) and isinstance(joined.get(k), list):
                        joined[k] = list(dict.fromkeys(joined[k] + v))
                    elif isinstance(v, (int, float)) and isinstance(joined.get(k),
                                                                    (int, float)):
                        joined[k] = max(v, joined[k])
                    else:
                        joined[k] = v
                theirs[name] = joined
            else:
                theirs[name] = was
            if frm == "me":
                stored.pop(name, None)
        if frm != "me":
            users.pop(frm, None)
        write_settings(stored, merge=False)
        return {"rows": rows, "places": places}

    def follower_cost(self, who):
        """How many files, and how many gigabytes, keeping this person costs.

        Counted the way the cacheing counts it: what they are half-way through, and
        what is on their list. It is what makes one person's two ticks visibly dearer
        than another's.
        """
        import pd_localapi
        con = local().lib.db()
        seen, files, size = set(), 0, 0
        keys, listed, shuffled = [], [], []
        try:
            keys = [str(r["key"]) for r in con.execute(
                """SELECT key FROM progress WHERE who = ? AND position > 30
                   AND position < """ + pd_localapi.FINISHED_SQL + """
                     AND COALESCE(casual, 0) = 0
                   ORDER BY updated DESC LIMIT 40""",
                (who,))]
            listed = [str(k) for k in self.watchlist_of(who)[:40]]
            # and the shuffle, which is a shelf of its own and costs what it costs
            shuffled = [str(k) for k in self.casual_ahead(who)]
            for key in keys + listed + shuffled:
                if key in seen:
                    continue
                seen.add(key)
                found = local().file_for(key, 0) or {}
                path = found.get("file")
                if not path:
                    continue
                row = con.execute("SELECT size FROM file WHERE path=?",
                                  (path,)).fetchone()
                if not row:
                    continue
                files += 1
                size += row["size"] or 0
        finally:
            con.close()
        return {"deck": len(keys), "list": len(listed), "casual": len(shuffled),
                "files": files, "gb": round(size / 1e9, 1)}

    def cached_for(self):
        """Whose Continue watching, whose watchlist and whose shuffle to keep.

        Nobody's by default but the owner's: a house with six guests would otherwise
        ask the other machine to hold the whole library.
        """
        mine = self.owner_caching()
        # The owner, by whichever name they go under - their own key if they have
        # taken one, and the placeholder a fresh install starts with if not. It read
        # "only if they are still called me", so the moment the owner took a key of
        # their own their deck, their list and their shuffle stopped being kept at
        # all: the one person certain to be watching, left out of their own cache.
        me = mine.get("token") or "me"
        keyed = set()
        decks = [me] if mine["cacheDeck"] else []
        lists = [me] if mine["cacheList"] else []
        shuffles = [me] if mine["cacheCasual"] else []
        if me != "me":
            keyed.add(me)                 # counted once, not twice
        for row in INVITES.load():
            if row.get("token") in keyed:
                continue
            if row.get("cacheDeck"):
                decks.append(row["token"])
            if row.get("cacheList"):
                lists.append(row["token"])
            if row.get("cacheCasual"):
                shuffles.append(row["token"])
        return decks, lists, shuffles

    def watchlist_of(self, who):
        """One viewer's watchlist, by the name their settings are filed under.

        In the order it will be watched rather than the order it was marked. A list
        built over a year holds episodes in the order somebody pressed the button, so
        five of one series arrived from three different seasons at once.
        """
        stored = read_settings() or {}
        if who == "me":
            marked = list(stored.get("watchlist") or [])
        else:
            marked = list(((stored.get("users") or {}).get(who) or {})
                          .get("watchlist") or [])
        return self.in_watching_order(marked)

    def in_watching_order(self, keys):
        """Episodes of one series together and in order; everything else left alone.

        A series keeps the place its first marked episode had, so somebody's list
        still reads in the order they built it - it is only the inside of each series
        that is put right.
        """
        keys = [str(k) for k in keys]
        wanted = [k for k in keys if k.startswith("e")]
        if len(wanted) < 2:
            return keys
        facts = {}
        try:
            con = local().lib.db()
            rows = con.execute(
                "SELECT id, item_id, season, number FROM episode WHERE id IN (%s)"
                % ",".join("?" * len(wanted)),
                [k for k in wanted if is_episode(k)]).fetchall()
            for r in rows:
                facts[str(r["id"])] = (r["item_id"], r["season"] or 0,
                                          r["number"] or 0)
        except Exception:
            return keys                    # nothing known: leave the list as it is
        seen, out = set(), []
        for k in keys:
            if k in seen:
                continue
            if k not in facts:
                seen.add(k)
                out.append(k)
                continue
            show = facts[k][0]
            same = sorted((x for x in keys if facts.get(x, (None,))[0] == show),
                          key=lambda x: facts[x][1:])
            for x in same:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
        return out

    #: A file's mark, kept against its size and time so it is worked out once. Reading
    #: two ends of a film is a fraction of a second; reading the whole of a hundred of
    #: them, every minute, would be a disk full of work for nothing.
    MARKS = {}

    @classmethod
    def quick_mark(cls, path):
        """A cheap, exact-enough fingerprint: the size and both ends of the file.

        The same scheme subtitle sites use to recognise a film. Two different rips
        never agree on it, and a file that arrives changed - or half - does not agree
        with itself. Hashing twenty gigabytes to answer the same question would cost
        a minute a film.
        """
        try:
            stat = os.stat(path)
        except OSError:
            return ""
        seen = cls.MARKS.get(path)
        if seen and seen[0] == (stat.st_size, int(stat.st_mtime)):
            return seen[1]
        import hashlib
        digest = hashlib.sha256()
        digest.update(str(stat.st_size).encode())
        try:
            with open(path, "rb") as f:
                digest.update(f.read(65536))
                if stat.st_size > 131072:
                    f.seek(-65536, os.SEEK_END)
                    digest.update(f.read(65536))
        except OSError:
            return ""
        mark = digest.hexdigest()
        cls.MARKS[path] = ((stat.st_size, int(stat.st_mtime)), mark)
        if len(cls.MARKS) > 400:
            cls.MARKS.clear()
        return mark

    #: the following server, as it last announced itself. Kept here as well as in
    #: the settings file so that a machine announcing every minute is not a write
    #: every minute.
    STANDBY = {"where": "", "outside": "", "name": "", "when": 0}

    #: Every machine that has announced itself as following this one, by address:
    #: its name, its address from outside, and when it last spoke. More than one is
    #: allowed - a house may keep a copy in the cupboard and another at a friend's -
    #: and the freshest is the one clients are sent to.
    FOLLOWERS = {}
    #: written at most once a minute: an announcement arrives after every file
    FOLLOWERS_WRITTEN = [0.0]

    #: What the following server says it holds, by this library's keys. Kept in
    #: memory: it is a fact about another machine, true until it says otherwise, and
    #: worth nothing after a restart of either.
    COPIES = {"keys": [], "when": 0}

    #: The question the machine keeping copies last asked, so the queue on the page
    #: can be answered with the same one. The page used to ask for the night's list
    #: whatever the hour, so what it showed and what was actually being fetched were
    #: two different lists - and the one on screen was the longer of them.
    #: Why each file is in the queue, so the cache log can say who wanted it
    WHY_BY_KEY = {}

    #: What the other machine could not fetch, as it last said
    FOLLOWER_TROUBLE = []

    LAST_ASK = {"hours": 4.0, "deck": False, "episodes": 6, "casual": 0.0,
                "whole": False, "when": 0}

    #: Somebody going to bed before the hour this machine sleeps at. The other machine
    #: asks every minute; this is what it is told when it does.
    EARLY_UNTIL = 0.0

    @staticmethod
    def build_beside_us():
        """The installer this machine can hand to another, or nothing.

        Not every server has one. The installer does not pack a copy of itself - that
        would double every download - so this is the machine a build was made on, or
        one that kept the file it was installed from.
        """
        try:
            found = [f for f in os.listdir(STATIC)
                     if f.lower().startswith("palladium-setup")
                     and f.lower().endswith(".exe")]
        except OSError:
            return ""
        if not found:
            return ""
        found.sort()
        return os.path.join(STATIC, found[-1])

    #: the hash of that file, worked out once: it is 34 MB and it does not change
    BUILD_MARK = {"path": "", "sha256": "", "size": 0, "version": ""}

    @classmethod
    def build_on_offer(cls):
        """What this machine offers, with its hash. Empty when it has no installer."""
        path = cls.build_beside_us()
        if not path:
            return {}
        if cls.BUILD_MARK.get("path") != path:
            import hashlib
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as f:
                    for lump in iter(lambda: f.read(262144), b""):
                        digest.update(lump)
            except OSError:
                return {}
            name = os.path.basename(path)
            cls.BUILD_MARK = {"path": path, "sha256": digest.hexdigest(),
                              "size": os.path.getsize(path),
                              "version": name[len("Palladium-Setup-"):-len(".exe")]}
        return {k: v for k, v in cls.BUILD_MARK.items() if k != "path"}

    @staticmethod
    def copies_file():
        return os.path.join(ROOT, "copies.json")

    @classmethod
    def remember_copies(cls, keys):
        """What the other machine says it holds, kept across a restart.

        It was held in memory only, so restarting this server blanked the mark on
        every poster until the other one next spoke - a quarter of an hour of a
        library that looked like it had no copies at all.
        """
        cls.COPIES = {"keys": keys, "when": int(time.time())}
        try:
            with open(cls.copies_file(), "w", encoding="utf-8") as f:
                f.write(json.dumps(cls.COPIES))
        except OSError:
            pass

    @classmethod
    def followers_file(cls):
        return os.path.join(os.path.dirname(cls.copies_file()), "followers.json")

    @classmethod
    def remember_followers(cls, force=False):
        """Keep the record of machines that follow this one across a restart.

        Held in memory only, the list emptied on every restart - a machine that had
        been keeping copies for a month showed as though it had never been here.
        """
        if not force and time.time() - cls.FOLLOWERS_WRITTEN[0] < 60:
            return
        cls.FOLLOWERS_WRITTEN[0] = time.time()
        try:
            with open(cls.followers_file(), "w", encoding="utf-8") as f:
                f.write(json.dumps(cls.FOLLOWERS))
        except OSError:
            pass

    @classmethod
    def recall_followers(cls):
        try:
            with open(cls.followers_file(), encoding="utf-8") as f:
                said = json.loads(f.read())
            if isinstance(said, dict):
                cls.FOLLOWERS = {str(k): v for k, v in said.items()
                                 if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            pass

    def note_cache(self, **stats):
        """Write something down about the machine that is asking, if it is known."""
        here = self.client_address[0]
        where = ""
        for key, one in Handler.FOLLOWERS.items():
            host = urllib.parse.urlsplit(str(key)).hostname or str(key)
            if host == here:
                where = key
                break
        if not where:
            where = Handler.STANDBY.get("where") or ""
        if not where:
            return
        row = dict(Handler.FOLLOWERS.get(where) or {"where": where})
        row.update(stats)
        # and the key it came in with, so its own key can be shown against it and
        # taken away on its own
        was = self.bearer()
        if was:
            row["token"] = was
        row["when"] = int(time.time())
        Handler.FOLLOWERS[where] = row
        Handler.remember_followers()

    @classmethod
    def recall_copies(cls):
        """Read it back at startup. Old news is better than no news: the other
        machine confirms or corrects it within the minute."""
        try:
            with open(cls.copies_file(), encoding="utf-8") as f:
                said = json.loads(f.read())
            if isinstance(said.get("keys"), list):
                cls.COPIES = {"keys": [str(k) for k in said["keys"]],
                              "when": int(said.get("when") or 0)}
        except (OSError, ValueError, AttributeError):
            pass

    def remember_cache(self, where, name, outside="", build="", room=None):
        """Write down where the following server is, if it has moved."""
        moved = (where != Handler.STANDBY.get("where")
                 or name != Handler.STANDBY.get("name")
                 or outside != Handler.STANDBY.get("outside"))
        # and what it is running, which is worth seeing beside its name: two machines
        # on different builds speak slightly different languages to the same app
        # how full that machine is: it is the only one that can measure its own
        # disk, and "is there room for tonight" is the question this answers
        room = dict(room or {})
        Handler.STANDBY.update({"where": where, "outside": outside, "name": name,
                                "build": build, "room": room,
                                "when": int(time.time())})
        # merged, not replaced: what this machine holds and what was sent to it are
        # learned from other requests and would be wiped by every announcement
        was = dict(Handler.FOLLOWERS.get(where) or {})
        was.update({"where": where, "outside": outside, "name": name,
                    "build": build, "room": room, "when": int(time.time())})
        held = self.bearer()
        if held:
            was["token"] = held
            # and the key takes the machine's name, once the machine has said what it
            # is called. A key made before anybody knew which computer would use it is
            # called "a following server", and that is what the drawing and the list
            # of keys then called the machine itself.
            if name:
                try:
                    rows = INVITES.load()
                    for row in rows:
                        if (row.get("token") == held and row.get("follows")
                                and str(row.get("name") or "") in
                                ("", "a following server")):
                            row["name"] = name
                            INVITES.save(rows)
                            break
                except Exception:
                    pass
        Handler.FOLLOWERS[where] = was
        # same machine and key under an older address (it moved port): one row, not two
        host = urllib.parse.urlsplit(where).hostname
        if held and host:
            for old in [k for k, v in Handler.FOLLOWERS.items()
                        if k != where and v.get("token") == held
                        and urllib.parse.urlsplit(str(k)).hostname == host]:
                Handler.FOLLOWERS.pop(old, None)
        Handler.remember_followers()
        if not moved:
            return
        cfg = local().lib.config()
        cfg["cache"] = {"where": where, "outside": outside, "name": name,
                          "when": int(time.time())}
        local().lib.save_config(cfg)

    #: What the cache last said it was doing, and when we asked: (screens, when)
    COPY_BUSY = None

    @staticmethod
    def host_of(where):
        return urllib.parse.urlsplit(where).netloc or where

    def copy_is_busy(self):
        """Which screens the machine that keeps copies is serving, by address.

        Asked of it, because this machine cannot see it. A player reading part of a
        film off the cache talks to the cache directly and says nothing here, so the
        drawing called a machine carrying half a film "standing by".

        Kept for five seconds: the drawing is made again every two, and a round trip
        to the machine next to it takes milliseconds. At half a minute the picture was
        a different moment from the list of viewers printed under it - a film had been
        coming off both machines for twenty seconds before the line appeared.
        """
        now = time.time()
        had = Handler.COPY_BUSY
        if had and now - had[1] < 5:
            return had[0]
        Handler.COPY_BUSY = ([], now)
        one = self.standby_now()
        where = (one.get("where") or "").rstrip("/")
        if not where or not one.get("alive"):
            return []
        token = ""
        for row in INVITES.load():
            if row.get("follows") and row.get("token"):
                token = row["token"]
                break
        if not token:
            return []
        try:
            with urllib.request.urlopen(
                    "%s/watching?t=%s" % (where, urllib.parse.quote(token)),
                    timeout=3) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace"))
            rows = said.get("live") or []
            # who is reading off it and how fast, not merely how many: the drawing
            # runs a line from a machine to the screen it is feeding, and says what
            # is going down it. A count cannot say either.
            busy = [{"address": str((r or {}).get("address") or ""),
                     "mbit": round(float((r or {}).get("mbit") or 0), 1),
                     "title": str((r or {}).get("title") or "")}
                    for r in rows if (r or {}).get("how") != "syncing"]
        except Exception:
            busy = []
        Handler.COPY_BUSY = (busy, now)
        return busy

    #: what the followed server is serving, and when it was asked
    HOUSE_BUSY = ([], 0.0)

    def house_is_busy(self):
        """Screens the followed server is serving, by address.

        Mirror of copy_is_busy for a follower: the diagram drew a line only from
        this machine, so a film read off both showed the main server idle while it
        carried half. Asked with the follow key: a browser's key here is local and
        unknown to the main server.
        """
        now = time.time()
        had = Handler.HOUSE_BUSY
        if had and now - had[1] < 5:
            return had[0]
        Handler.HOUSE_BUSY = ([], now)
        busy = []
        try:
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            where = str(one.get("master") or "").rstrip("/")
            key = str(one.get("key") or "")
            if one.get("on") and where and key:
                with urllib.request.urlopen(
                        "%s/watching?t=%s" % (where, urllib.parse.quote(key)),
                        timeout=3) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace"))
                busy = [{"address": str((r or {}).get("address") or ""),
                         "mbit": round(float((r or {}).get("mbit") or 0), 1),
                         "title": str((r or {}).get("title") or "")}
                        for r in (said.get("live") or [])
                        if (r or {}).get("how") != "syncing"]
        except Exception:
            busy = []
        Handler.HOUSE_BUSY = (busy, now)
        return busy

    def standby_now(self):
        """The following server for a client to fall back on, or nothing.

        An address that has not announced itself for a day is not offered: the
        machine has been taken away, and sending viewers there wastes their time
        every time this server is slow.
        """
        one = dict(Handler.STANDBY)
        if not one.get("where"):
            one = dict((local().lib.config().get("standby") or {}))
        # What this machine is and where else it can be reached, and the machine it
        # follows if it follows one. Facts about the machine answering, not about any
        # cache of its own - and they were left out of the answer given by a
        # machine that has no cache, which is every copy. A page opened on the cache
        # was told nothing about anywhere else and had no way back to the main server.
        about_me = {
            "mine": {"lan": (("http://%s:%d" % (LAN_IP, PORT))
                             if LAN_IP and self.may_have_the_lan() else ""),
                     "outside": (("http://%s:%d" % (wan_ip(), PORT))
                                 if wan_ip() else ""),
                     "name": self.server_name()},
            "follows": self.house_doors(),
        }
        where, when = one.get("where") or "", int(one.get("when") or 0)
        if not where or (when and time.time() - when > 86400):
            return dict(about_me, where="", outside="", name="", seen=0)
        # a viewer on this network uses the first, one outside the second. Whether
        # it is awake is this machine's business to know: it is on the same network,
        # and a client that has to find out for itself only finds out too late.
        # Whichever of the two the asker can actually reach. Being allowed the
        # address on this network is one question and being able to use it is
        # another: somebody away from the main server was handed 192.168.x for the other
        # machine, asked it, and waited for an answer that could never come - so from
        # away the second machine had no version, no build, and no way to be reached
        # at all.
        if not self.at_home() or not self.may_have_the_lan():
            # the way in from outside is the one that works from where they are
            where = one.get("outside") or where
        return {"where": where, "outside": one.get("outside") or "",
                "name": one.get("name") or "", "seen": when,
                "alive": time.time() - when < 180,
                "build": one.get("build") or "",
                "room": one.get("room") or {},
                # and this machine's own two addresses, handed over while a screen
                # can still reach it. A client away from home cannot ask a server it
                # cannot reach where else it lives, so it has to have been told.
                "mine": about_me["mine"],
                "follows": about_me["follows"],
                # This machine would rather the main server watched from the other one.
                # Game mode is the case: the card has been handed to something else,
                # and a film asked of it now is a film nobody enjoys. The cache holds
                # what the main server is in the middle of, so everyone goes there until
                # the game is over.
                "prefer": bool(self.game_mode().get("on")),
                # and a link to it that carries the key, so the other machine opens
                # rather than asking who this is. A cookie belongs to the machine that
                # set it, so arriving at the cache with one for the main server is arriving
                # with nothing - which is why the owner, who has every right to be
                # there, was shown the way in for a stranger.
                "link": self.copy_link(one),
                # the key itself, so a page that has picked an address of its own -
                # the one on the home network rather than the one from outside - can
                # put the two together
                "key": self.bearer() or ""}

    def copy_link(self, one):
        """The way in to the other machine, carrying this viewer's own key."""
        token = self.bearer()
        if not token:
            return ""
        where = (one.get("outside") or one.get("where") or "").rstrip("/")
        return (where + "/s/" + token) if where else ""

    def art_of(self, key):
        """The catalogue's own answer for a title: its number and its pictures.

        A film and an episode both answer with the programme's, because that is what
        a poster is: the episode still is a wide picture and no shelf wants it.
        """
        con = local().lib.db()
        try:
            key = str(key)
            if is_episode(key):
                row = con.execute(
                    "SELECT i.id, i.tmdb_id, i.poster, i.backdrop, i.title, i.year "
                    "FROM episode e JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (key,)).fetchone()
            elif is_title(key):
                row = con.execute("SELECT id, tmdb_id, poster, backdrop, title, year "
                                  "FROM item WHERE id=?", (str(key),)).fetchone()
            else:
                return None
            if not row or not (row["poster"] or row["tmdb_id"]):
                return None
            # the number the picture is filed under here, which is the programme's
            # for an episode and the film's own for a film
            return {"tmdb": row["tmdb_id"], "poster": row["poster"],
                    "backdrop": row["backdrop"], "title": row["title"],
                    "year": row["year"], "owner": str(row["id"])}
        except Exception:
            return None
        finally:
            con.close()

    def languages_kept(self, mine=None):
        """The subtitle languages worth copying: those of the people being kept for.

        A copy is for the people whose viewing it holds, and each of them reads in
        one language. Nobody else's is fetched - a house of six would otherwise send
        six subtitles for every film.
        """
        stored = read_settings() or {}

        def theirs(who):
            if who == "me":
                said = stored
            else:
                said = (stored.get("users") or {}).get(who) or {}
            return (said.get("subLang") or "").lower()[:5]

        if mine:
            return {theirs(mine) or "en"}
        decks, lists, shuffles = self.cached_for()
        langs = {theirs(who) for who in set(decks + lists + shuffles)}
        langs.discard("")
        return langs or {"en"}

    def sides_worth_copying(self, path, key, langs, part_id=None, every=False):
        """Subtitle files beside a video that the other machine should have too.

        The ones in the languages being kept for, and whichever was actually chosen
        for this title - a subtitle somebody picked is the one they will look for.

        For a film, [every] one of them: downloaded and already there alike. A film is
        watched once and the subtitle wanted on the night is not always the one the
        languages say, and a subtitle is a few kilobytes against a few gigabytes. What
        changes on this machine follows: each carries the size and both its ends, and
        the other machine fetches again whenever that no longer matches what it holds.
        """
        import pd_localapi as _la
        out = []
        try:
            beside = _la.sidecars(path)
        except Exception:
            return out
        picked = ""
        try:
            picked = os.path.basename(local().picked_subtitle(str(key)) or "")
        except Exception:
            picked = ""
        for n, side in enumerate(beside):
            name = os.path.basename(side.get("file") or "")
            if not name:
                continue
            if not every and side.get("lang") not in langs and name != picked:
                continue
            try:
                size = os.path.getsize(side["file"])
            except OSError:
                continue
            out.append({"key": key, "side": n, "part": part_id,
                        "name": name, "size": size,
                        "mark": self.quick_mark(side["file"]),
                        "title": name})
        return out

    @classmethod
    def link_seen(cls, total, side="home"):
        """High-water mark per side, halved after 10 quiet minutes.

        A mark that never falls measures the best night this machine ever had. One
        mark per side: LAN and WAN differ by an order of magnitude, and a single
        number took the larger of the two.
        """
        now = time.time()
        mark = cls.LINK.setdefault(side, {"seen": 0.0, "when": 0.0})
        if total > mark["seen"]:
            cls.LINK[side] = {"seen": float(total), "when": now}
        elif now - (mark["when"] or 0) > 600:
            cls.LINK[side] = {"seen": max(0.0, mark["seen"] * 0.5), "when": now}
        return cls.LINK[side]["seen"]

    #: seconds of buffer each viewer reports, and when. The one measure that reads
    #: the same for direct play and for a transcode.
    AHEAD = {}
    #: A fed player holds ~50 s. Under 40 the ceiling stops climbing, under 30 it
    #: halves, under 20 it drops to the floor.
    FED_AHEAD = 50.0
    EASE_AHEAD = 40.0
    THIN = 30.0
    PANIC_AHEAD = 20.0
    #: A transcode holds whatever the encoder is ahead by, so a steady 8 s is
    #: healthy. Direction separates the two; below this, falling or not, it is starved.
    STALLED_AHEAD = 5.0

    @staticmethod
    def how_thin(now=None, side=None):
        """Worst recent report: "", "ease", "back" or "panic".

        Filtered to one side when given: a thin buffer on the WAN says nothing about
        a copy between two machines on the LAN.
        """
        now = now or time.time()
        worst = ""
        for said in list(Handler.AHEAD.values()):
            ahead, when = said[0], said[1]
            where = said[2] if len(said) > 2 else None
            if side and where and where != side:
                continue
            if now - when >= 12 or ahead < 0:
                continue
            before = said[3] if len(said) > 3 else -1.0
            # the player reports a stall: no measurement overrides that
            if len(said) > 4 and said[4]:
                return "panic"
            # steady or rising and above the floor: fed at whatever height this way
            # of watching holds, and taking megabits off a copy would not raise it
            if (ahead > Handler.STALLED_AHEAD
                    and not (before >= 0 and ahead < before - 1.0)):
                continue
            if ahead < Handler.PANIC_AHEAD:
                return "panic"
            if ahead < Handler.THIN:
                worst = "back"
            elif ahead < Handler.EASE_AHEAD and worst != "back":
                worst = "ease"
        return worst

    @staticmethod
    def running_dry(now=None, side=None):
        """True when a report is at "back" or "panic"."""
        return Handler.how_thin(now, side) in ("back", "panic")

    @classmethod
    def disk_reading_slow(cls, now=None):
        """True when reads were lately spending their time waiting on disk.

        Share of each block's time spent reading rather than sending, measured while
        serving. An earlier signal than a viewer's buffer, and it costs nothing.
        """
        now = now or time.time()
        return (now - (cls.DISK.get("when") or 0) < 12
                and (cls.DISK.get("busy") or 0) > cls.DISK_BUSY)

    @staticmethod
    def a_film_is_struggling(rows, side=None):
        """Whether anybody watching is getting less than they were getting.

        Measured against each viewer's own average rather than against the bitrate of
        what they are watching: a direct play reads in bursts and idles between them,
        so "below what the film needs" is true of a healthy one half the time. A rate
        that has fallen away from its own average has not.

        A viewer with nothing open is holding a full buffer, which is the opposite of
        starved, and the first seconds of a viewing have no average worth the name.
        """
        # the players' own reports first: true before the picture stops, and it
        # reads the same for direct play and for a transcode
        if Handler.running_dry(side=side):
            return True
        for r in rows:
            if r.get("how") == "syncing" or r.get("holding"):
                continue
            if int(r.get("seconds") or 0) < 20:
                continue                  # too new to have an average
            average = float(r.get("average") or 0) * 8
            if average <= 0.5:
                continue
            if float(r.get("mbit") or 0) < average * Handler.STARVED:
                return True
        return False

    @classmethod
    def sync_ceiling(cls, cfg, streaming, syncing, side="home"):
        """What a copy may take this second, in megabits.

        Nought from the settings means no ceiling and is left alone. A number means
        that number. Otherwise it is worked out: what the line has been seen to carry,
        less what the films are drawing, less a margin for the bursts they ask in.
        """
        # 0 means no ceiling chosen, not a ceiling of zero: returned as a number it
        # read downstream as unlimited, and a copy took 300 Mbit off the disks a film
        # was being read from.
        said = cfg.get("syncMbitWhileWatching")
        if said not in (None, "", 0, "0"):
            try:
                return float(said)
            except (TypeError, ValueError):
                pass
        if not cfg.get("syncAdaptive", True):
            return 20.0
        line = cls.link_seen(float(streaming) + float(syncing), side)
        # a viewer's own report is enough: without this it was ignored until a copy
        # had run flat out long enough to measure the line
        # under PANIC_AHEAD the copy drops to LEAST_MBIT
        if cls.how_thin(side=side) == "panic":
            return cls.LEAST_MBIT
        if line <= 0:
            return 0.0                    # nothing measured yet: no ceiling to give
        # what is left on the line once the films have what they are drawing
        spare = max(cls.LEAST_MBIT, line * cls.SPARE - float(streaming))
        # Room to probe above the mark. What has been seen is what this ceiling has
        # allowed so far, so a ceiling capped by its own measurements can never learn
        # a higher one: a copy sat at 16 Mbit on a 100 Mbit LAN. It may climb to twice
        # what the side has carried, doubling every few seconds until something real
        # stops it - a struggling viewer halves it.
        room = max(spare, line * 2)
        now = time.time()
        held = cls.PACE.setdefault(side, {"mbit": 0.0, "when": 0.0})
        pace = held["mbit"] or spare
        if now - (held["when"] or 0) > 30:
            pace = spare                  # nobody has watched for a while: start again
        elif cls.a_film_is_struggling(cls.WATCHING_NOW) or cls.disk_reading_slow():
            # either side: the two lines are separate but the disks are shared, so a
            # WAN viewer can be starved by a LAN copy taking none of their megabits.
            # The side decides how much room there is; struggle decides whether to
            # take it.
            pace = pace * cls.BACK_OFF
        elif cls.how_thin(side=side) == "ease":
            pass                  # under forty: hold here, do not climb further
        else:
            pace = pace + cls.STEP_UP
        pace = max(cls.LEAST_MBIT, min(room, pace))
        cls.PACE[side] = {"mbit": pace, "when": now}
        return pace

    @classmethod
    def settled_numbers(cls):
        """The server's built-in constants, grouped, for Settings > Advanced.

        Read from the values themselves so the page cannot disagree with the server.
        Read-only: these are the defaults, not settings.
        """
        import pd_follow
        import pd_torrents

        def row(name, value, what):
            return {"name": name, "value": str(value), "what": what}

        sound = ", ".join("%s %.2g" % (k, v) for k, v in
                          sorted(cls.SOUND_MBIT.items(), key=lambda kv: -kv[1]))
        seen, costs = set(), []
        for name, cost in sorted(cls.CODEC_COST.items(), key=lambda kv: kv[1]):
            if cost not in seen or cost == 1.0:
                costs.append("%s %.2g" % (name, cost))
                seen.add(cost)
        return [
            {"title": "Making a film smaller",
             "why": "What an encoder is asked for when a film cannot be sent as it is. "
                    "Each of these is a ceiling, never a target to fill.",
             "rows": [
                 row("Away, with nothing set", "8 Mbit",
                     "Ceiling for a viewer outside this network when none is set. On "
                     "this network there is no ceiling."),
                 row("x265 headroom", "%.2g times" % cls.HEVC_HEADROOM,
                     "The only encode allowed above the source rate, since a re-encode "
                     "costs bitrate. Every other codec is capped at what came in."),
                 row("Sound taken off the top", sound + " Mbit",
                     "The library keeps the whole container's rate, so the sound comes "
                     "off before the picture is measured - it is re-encoded to a "
                     "fraction of this anyway. Figures are for 6 channels; 8 cost about "
                     "a third more, 2 about half; an unnamed codec counts as %.2g."
                     % cls.SOUND_OTHERWISE),
                 row("What an old codec costs", ", ".join(costs),
                     "How much of the old bitrate a modern encoder needs for the same "
                     "picture. MPEG-2 spends about 3x h264, so a 21 Mbit broadcast is "
                     "re-encoded at 8."),
                 row("Least given to a picture", "1 Mbit",
                     "Floor on the video ceiling, however small the source."),
                 row("Sound aimed at", "%.4g LUFS" % VOLUME_TARGET,
                     "Where a file's loudness is put when evening out is on. LUFS "
                     "measures loudness over the whole file rather than at its peak: "
                     "broadcast works to -23, streaming to about -18, a cinema mix to "
                     "about -27. Two files of one episode here measure 15 dB apart."),
                 row("Most ever added or taken", "%.3g dB" % VOLUME_MOST,
                     "Cap on the correction: a file far below the target is brought "
                     "most of the way up, not all of it."),
                 row("Measured over", "180 seconds from 5:00",
                     "How much of the file is analysed: past the titles, a few seconds "
                     "of ffmpeg, once per file."),
             ]},
            {"title": "Sharing the line",
             "why": "How a background copy gives way to playback. Derived from "
                    "measured throughput, not from a configured figure.",
             "rows": [
                 row("Left clear for the films", "%.0f per cent" % ((1 - cls.SPARE) * 100),
                     "Of measured throughput, kept clear above what playback is "
                     "drawing. A player reads in bursts, and a filling buffer wants "
                     "more than its average."),
                 row("Least a copy may have", "%.3g Mbit" % cls.LEAST_MBIT,
                     "A copy is slowed, never stopped outright."),
                 row("Backing off", "times %.3g" % cls.BACK_OFF,
                     "What the ceiling is multiplied by while somebody watching is "
                     "struggling."),
                 row("Creeping back up", "plus %.3g Mbit" % cls.STEP_UP,
                     "Added each second while nobody is."),
                 row("Struggling", "under %.0f per cent of its own average"
                     % (cls.STARVED * 100),
                     "Measured against each viewer's own average, not the film's "
                     "bitrate: direct play reads in bursts and idles between them, so a "
                     "healthy session sits under the film's bitrate half the time."),
                 row("Seconds a player holds",
                     "%.3g fed, %.3g hold, %.3g give back, %.3g floor"
                     % (cls.FED_AHEAD, cls.EASE_AHEAD, cls.THIN, cls.PANIC_AHEAD),
                     "A fed player holds about 50 s. Under 40 the ceiling stops "
                     "climbing, under 30 it halves, under 20 it drops to the floor - but "
                     "only while the buffer is falling, or under %.3g s. A transcode "
                     "arrives as it is made, so its player holds whatever the encoder is "
                     "ahead by and a steady 8 s is healthy. A player reporting a stall "
                     "goes to the floor whatever its depth." % cls.STALLED_AHEAD),
                 row("How far it may climb", "twice what that side has carried",
                     "Measured throughput is what this ceiling has allowed so far, so "
                     "a ceiling capped by its own measurements can never learn a higher "
                     "one. It doubles every few seconds until the link, a disk or a "
                     "viewer stops it."),
                 row("Two lines, not one", "the network here, and the one out",
                     "A stream going out and a copy between two machines here take no "
                     "megabits from each other, so each side is measured separately and "
                     "a copy gives way only to traffic on its own path. One combined "
                     "figure held a local copy to what was left of the upload."),
             ]},
            {"title": "Keeping copies",
             "why": "Limits on what a second machine holds. Hours ahead and episodes "
                    "ahead are per-machine settings, under the cache machine in "
                    "Settings; these are the bounds around them.",
             "rows": [
                 row("Part-way, each person", "%d days" % cls.PARTWAY_DAYS,
                     "Everything one person is part-way through, however many that "
                     "is - the episode they are on and the one before it, for each "
                     "programme. Only the age of a stored position bounds it now, "
                     "and the disk cap decides what falls off. Six programmes each "
                     "meant a series watched last week was not among them, so its "
                     "next episode was on no list and the copy did not have it."),
                 row("Kept after it leaves the screen", "the last %d each"
                     % cls.SCREEN_EACH,
                     "A title drops off the keep list the moment its player stops "
                     "reporting, and a copy deletes what the list no longer names - "
                     "which took a file out from under somebody with a minute of it "
                     "still to run. The one just finished is held while the next is "
                     "watched, and is there to go back to; the disk cap decides when "
                     "it goes."),
                 row("Least kept ahead", "%d episodes" % cls.LEAST_AHEAD,
                     "Floor on the episode count, whatever the hours setting works out "
                     "to."),
                 row("The shuffle's next", "%d" % cls.SHUFFLE_DEEP,
                     "How far down the shuffle queue is copied, counting the title "
                     "playing: drawing a title marks it played, and it was swept while "
                     "it was on screen."),
                 row("Asked how it is getting on", "every %d s" % pd_follow.ASK_EVERY,
                     "How often the machine keeping copies asks the main server what it "
                     "should be holding."),
                 row("Read in lumps of", "%d MB" % (pd_follow.LUMP // (1024 * 1024)),
                     "What a copy asks for at a time."),
                 row("Given up on after", "%d tries" % pd_follow.GIVE_UP_AFTER,
                     "A file that will not come across."),
                 row("A file being read is kept", "%d s" % pd_follow.KEPT_FOR,
                     "After the last request, so a sweep does not delete a file "
                     "between two requests for it."),
             ]},
            {"title": "Films from a pack",
             "why": "What counts as a film inside a torrent, and how it is fetched.",
             "rows": [
                 row("Smallest thing called a film",
                     "%d MB" % (pd_torrents.FILM_BYTES // (1000 * 1000)),
                     "Anything smaller in a pack is a sample, a trailer or an extra."),
                 row("Asked for in blocks of", "%d kB" % (pd_torrents.BLOCK // 1024),
                     "The unit a torrent client reads in."),
             ]},
            {"title": "Reading and waiting",
             "why": "How long this server waits for things, and when it decides a disk "
                    "is too busy to be given more to do.",
             "rows": [
                 row("First block of a film", "%.3g s" % cls.FIRST_BLOCK_WAIT,
                     "How long a stream is given to send anything at all before it is "
                     "taken to have failed."),
                 row("A disk is busy at", "%.0f per cent of a block's time"
                     % (cls.DISK_BUSY * 100),
                     "How much of each block's time went on reading rather than "
                     "sending. Above this the disk is the bottleneck: more reads are "
                     "handed to the cache machine, and a background copy backs off "
                     "before any viewer's buffer falls."),
                 row("And easy again at", "%.3g s a read" % cls.DISK_EASY,
                     "Below this it is taken up again."),
                 row("Artwork worth keeping", "5 kB",
                     "A poster smaller than this is not an image; it is fetched again "
                     "rather than cached."),
             ]},
        ]

    def stream_buffer(self):
        """Buffer report from a player, and the split of what carried the film.

        Answered on GET and POST. It existed on GET only and read the seconds from a
        request body a GET does not have, so every POST returned 404 into the caller's
        catch and AHEAD stayed empty. Seconds come from the body, else the query.
        """
        said = self.read_json() or {}
        args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                if "?" in self.path else {})
        # any player can report it, direct or transcoded; the sync ceiling backs off
        # while anybody is low
        try:
            ahead = float(said.get("ahead", (args.get("ahead") or [None])[0]))
        except (TypeError, ValueError):
            ahead = -1.0
        if ahead >= 0:
            if len(Handler.AHEAD) > 200:
                Handler.AHEAD.clear()
            now = time.time()
            # the previous report from the same screen: a buffer that is not moving is
            # being held, at whatever height that way of watching holds
            # keyed per screen: under the viewer's name alone, one person watching on
            # two devices overwrote their own last report
            who = self.watcher() + "/" + str(self.client_address[0])
            had = Handler.AHEAD.get(who)
            before = had[0] if (had and now - had[1] < 30) else -1.0
            # and whether it stalled: depth is a guess, a stall is not
            stalled = bool(said.get("stalled")
                           or (args.get("stalled") or [""])[0] in ("1", "true"))
            Handler.AHEAD[who] = (
                ahead, now, Handler.side_of(self.client_address[0]), before, stalled)
        if len(Handler.CARRIED) > 200:
            Handler.CARRIED.clear()
        # and the per-machine split, for the player's info line: the client cannot
        # tell which server a block came from
        mine, theirs, _ = Handler.CARRIED.get(self.watcher()) or (0, 0, 0)
        out = {"ok": True, "ahead": ahead}
        if theirs > 0:
            whole = float(mine + theirs)
            out["split"] = [int(round(mine * 100 / whole)),
                            int(round(theirs * 100 / whole))]
            out["with"] = (self.standby_now() or {}).get("name") or ""
        self.reply_json(out)

    def name_of(self, who):
        """What to call a viewer in a list somebody reads. "me" is the owner."""
        who = str(who or "")
        if not who or who == "me":
            return str((read_settings() or {}).get("ownerName") or "you")
        try:
            for row in INVITES.load():
                if row.get("token") == who or row.get("name") == who:
                    return str(row.get("name") or who)
        except Exception:
            pass
        return who

    def house_answers(self, path, body):
        """Put a question about somebody's shuffle to the machine that owns it.

        Only from a machine that follows another, and only while that machine can be
        reached: a copy answering for itself is a second evening, and two evenings is
        the thing this is here to prevent. Under the same key the person is using
        here, so the main server knows whose round it is - not this machine's own key,
        which would make every viewer look like the cache.

        Nothing is retried and nothing waits long. If the main server does not answer, this
        machine answers from what it has, which is what it is for.
        """
        import pd_follow
        try:
            one = pd_follow.settings(local().lib.config())
        except Exception:
            return None
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None                   # this machine is the main server
        token = self.bearer()
        if not token:
            return None
        url = (one["master"].rstrip("/") + path + "?t="
               + urllib.parse.quote(token))
        try:
            asked = urllib.request.Request(
                url, data=json.dumps(body or {}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "X-Palladium-App": "follower"})
            with urllib.request.urlopen(asked, timeout=6) as answer:
                return json.loads(answer.read().decode("utf-8", "replace"))
        except Exception:
            return None                   # the main server is off: answer for ourselves

    # ---- a collection, played in shuffle -------------------------------------

    #: How far ahead the hat is drawn. Ten is what makes "what is next" answerable,
    #: and answerable is what lets a subtitle be fetched for it and a copy kept of it.
    SHUFFLE_DEEP = 10

    def shuffle_round(self, mine, cid, make=True):
        """This person's round for one shelf: the hat, the history and the places."""
        rounds = mine.get("shuffles")
        if not isinstance(rounds, dict):
            rounds = {}
            if make:
                mine["shuffles"] = rounds
        one = rounds.get(str(cid))
        if not isinstance(one, dict):
            one = {"queue": [], "played": [], "at": {}, "run": 1, "casualStamp": 0}
            if make:
                rounds[str(cid)] = one
        return one

    def shuffle_resume(self, cid):
        """Where the title this person's round on one shelf is on was left, in seconds.

        Only that title. Other titles left part-way on the shelf start from their
        beginning when they are drawn. Nought when it has not been started or has been
        finished - so a button can say Resume when there is something to resume.
        """
        stored = read_settings() or {}
        who = self.viewer()
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        one = ((mine.get("shuffles") or {}).get(str(cid)) or {})
        key = self.shuffle_current(one)
        if not key:
            return 0
        value = (one.get("at") or {}).get(key)
        at = int(value.get("at") or 0) if isinstance(value, dict) else int(value or 0)
        return at if at > 30 else 0

    @staticmethod
    def shuffle_current(one):
        """The title a round is on: the last one drawn, unless it has been finished."""
        played = [str(k) for k in (one.get("played") or [])]
        if not played or played[-1] == str(one.get("done") or ""):
            return ""
        return played[-1]

    def shuffle_shelf(self, cid):
        """The keys one shelf holds, in library order, or nothing if there is no shelf.

        The name a shelf goes by on one page is "coll:f592640" and on another it is
        "f592640" - the first is how the library API numbers everything it hands out,
        so that a shelf and a film cannot collide. Both are the same shelf, and being
        strict about which was sent is how a television came to say there was nothing
        to play on a shelf full of films.
        """
        cid = str(cid or "")
        if cid.startswith("coll:"):
            cid = cid[5:]
        shelf = next((c for c in self.collections()
                      if str(c.get("id")) == str(cid)), None)
        if not shelf:
            return None, []
        con = local().lib.db()
        try:
            # What a pack can give belongs in the hat too. It cannot be played the
            # second it is drawn - it has to be fetched first - so the draw still
            # prefers anything already here, and asks for the rest in the background.
            #
            # And a programme is drawn an episode at a time. A shelf matches titles,
            # so a rule that catches a series put the series on the shelf and the hat
            # had one thing in it that could not be played at all. Every episode of it
            # goes in instead - the ones here, and the ones a pack has.
            return shelf, self.shelf_pool(con, shelf)
        finally:
            con.close()

    def shelf_pool(self, con, shelf):
        """Everything one collection holds, an episode at a time for a programme."""
        pool = []
        for key in self.collection_keys(con, shelf):
            key = str(key)
            if is_episode(key):
                pool.append(key)
                continue
            if key.startswith("os"):
                pool += self.offered_episode_keys(key)
                continue
            if re.match(r"^[0-9a-f]{12}-s\d+$", key):
                # a season stands for its episodes; its own key plays nothing
                pool += self.spread(key)
                continue
            rows = con.execute(
                """SELECT e.id FROM episode e JOIN file f ON f.episode_id = e.id
                   WHERE e.item_id = ? GROUP BY e.id
                   ORDER BY e.season, e.number""", (key,)).fetchall()
            pool += [str(r["id"]) for r in rows] if rows else [key]
        seen, out = set(), []
        for key in pool:
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out

    @staticmethod
    def offered_episode_keys(show_key):
        """Every episode one pack can give of one offered programme."""
        try:
            import pd_torrents
            out = []
            for season in pd_torrents.offered_seasons(show_key):
                for one in pd_torrents.offered_episodes(show_key,
                                                        int(season.get("index") or 0)):
                    key = str(one.get("ratingKey") or "")
                    if key:
                        out.append(key)
            return out
        except Exception:
            return []


    def shuffle_draw(self, cid, peek=False, resume=False, back=False):
        """Draw the next thing from one shelf, or look at it without drawing.

        Nothing comes up twice until everything has, and then the hat is refilled and
        the round counted - it starts again rather than stopping. Resume means carry
        on with what was left part-way, which is what somebody means far more often
        than "give me another one".
        """
        import random
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        shelf, pool = self.shuffle_shelf(cid)
        if shelf is None:
            return {"error": "there is no such shelf"}
        if not pool:
            return {"error": "that shelf is empty"}
        one = self.shuffle_round(mine, cid)
        # the title the round is on, before the hat is refilled under it
        current = self.shuffle_current(one) if resume else ""
        # What the round has drawn, whole, and the part of it this shelf still holds.
        # Only the second decides what is left to draw - but the whole is what gets
        # written back. Keeping the filtered list was throwing the rest away: a shelf
        # is a rule over a library, so a machine holding part of one answers with a
        # smaller pool, and one draw served there forgot every title the pool did not
        # name. Episodes watched a fortnight ago came round again the same evening.
        whole = [str(k) for k in (one.get("played") or [])]
        played = [k for k in whole if k in pool]
        left = [k for k in pool if k not in played]
        if not left:                      # the hat is empty: fill it, count the round
            whole, played, left = [], [], list(pool)
            one["run"] = int(one.get("run") or 1) + 1

        if back:
            # the one before this: somebody pressing previous means what they were
            # just watching, not another draw
            if len(played) < 2:
                return {"error": "nothing before this one"}
            # Back past anything this machine has not got. A copy holds part of the
            # shelf and its history names titles it never had, so previous handed back
            # a key it could not resolve at all - a dead end rather than the episode
            # before. Forward already drew only from what is here.
            step = played[:-1]
            while step and not self.can_be_played(step[-1]):
                step = step[:-1]
            if not step:
                return {"error": "nothing before this one"}
            # off the end of both: the whole list is what is kept, and the tail
            # being stepped past has to leave it too
            dropped = set(played[len(step):])
            played = step
            key = played[-1]
            one["played"] = [k for k in whole if k not in dropped]
            Handler.round_moved(one)
            write_settings(stored)
            return self.shuffle_said(one, key, pool, left)

        if resume and current in pool:
            # the title the round is on, where it was left - and only that one. Other
            # titles left part-way on the shelf start from their beginning when drawn.
            value = (one.get("at") or {}).get(current)
            at = int(value.get("at") or 0) if isinstance(value, dict) else int(value or 0)
            return self.shuffle_said(one, current, pool, left, at if at > 30 else 0)

        # the hat itself, drawn ten deep and kept: asking twice has to give the same
        # answer, or "what is next" means nothing and nothing can be got ready
        queue = [k for k in (one.get("queue") or []) if k in left]
        if len(queue) < min(self.SHUFFLE_DEEP, len(left)):
            rest = [k for k in left if k not in queue]
            random.shuffle(rest)
            queue = queue + rest[:max(0, self.SHUFFLE_DEEP - len(queue))]
        # Drawn from what this machine can actually play. Every title on a shelf has
        # a file on the main server and this changes nothing there; on a copy holding
        # part of the shelf it is the difference between carrying on and a shuffle
        # that stops at the first title the copy has not got yet.
        # Nothing the hat has drawn is here: draw again from the rest of the shelf
        # that is, and keep that as the hat. Looking only inside the ten it had drawn
        # and falling back to the first of them handed back a title this machine
        # cannot play - a shuffle that stops on a copy while the main server is off,
        # which is the one time it has to work.
        if not any(self.can_be_played(k) for k in queue):
            here = [k for k in left if self.can_be_played(k) and k not in queue]
            if here:
                random.shuffle(here)
                # One added, not ten drawn again. The hat is what the other machine is
                # fetching: replacing it wholesale every time this machine could not
                # play any of it had the copy chasing a list that changed under it -
                # in one day, 381 copies of 207 files, 174 of them fetched a second
                # time or a sixth. The ten already drawn stay drawn, and something
                # this machine can actually play goes on the end of them.
                queue = queue + here[:1]
        playable = [k for k in queue if self.can_be_played(k)]
        key = (playable[0] if playable
               else (queue[0] if queue else random.choice(left)))
        if peek:
            one["queue"] = queue
            Handler.round_moved(one)
            write_settings(stored)
            return self.shuffle_said(one, key, pool, left)
        one["played"] = whole + [key] if key not in whole else list(whole)
        one["queue"] = [k for k in queue if k != key]
        Handler.round_moved(one)
        write_settings(stored)
        self.fetch_ahead(one["queue"][:3])
        # and the ones a pack has to give, asked for before they are wanted: the one
        # drawn first, then the next few in the hat. A title that is still coming is
        # handed back saying so, and the player waits on it rather than failing.
        self.ask_the_pack([key] + [k for k in one["queue"][:3]])
        return self.shuffle_said(one, key, pool, left, drawn=True)

    def can_be_played(self, key):
        """Whether this machine holds a file for one title.

        True for everything on the main server. A copy holds part of a shelf, and
        drawing a title it has not got yet stops playback: the film cannot be fetched
        from a main server that is off, which is the only time a copy is answering a
        draw at all.
        """
        try:
            return bool(local().file_for(str(key), 0))
        except Exception:
            return True               # unanswerable is not the same as missing

    def shuffle_said(self, one, key, pool, left, at=0, drawn=False):
        """One answer about a shelf: what to play, where to start, and how far in."""
        local().who = self.viewer()
        con = local().lib.db()
        try:
            item = local().metadata_for(con, key)
        finally:
            con.close()
        return {"item": item, "key": str(key), "resumeAt": int(at or 0),
                "run": int(one.get("run") or 1), "pool": len(pool),
                "left": len(left) - (1 if drawn else 0),
                "queue": list(one.get("queue") or [])[:self.SHUFFLE_DEEP]}

    def shuffle_note(self, cid, key, position, duration):
        """Remember where a shuffled playing got to, on the shelf it came from.

        Near the end is finished and the note is dropped, so coming back does not
        offer to resume something four seconds from its credits.
        """
        import pd_localapi
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        one = self.shuffle_round(mine, cid)
        places = dict(one.get("at") or {})
        # Only a key the library holds. A player reported a file id once and it went
        # into the round as the title being played, which left the shelf with no row
        # on Continue watching at all: nothing could be looked up for it.
        con = local().lib.db()
        try:
            if not local().metadata_for(con, str(key), brief=True):
                return
        finally:
            con.close()
        # whatever plays off the shelf is drawn, however it was started: a row pressed
        # on Continue watching plays its episode without asking the hat
        played = [str(k) for k in (one.get("played") or [])]
        drawn = str(key) not in played
        if drawn:
            one["played"] = played + [str(key)]
            one["queue"] = [k for k in (one.get("queue") or []) if str(k) != str(key)]
        done = pd_localapi.LocalAPI.watched_through(position, duration)
        if done or position < 30:
            if str(key) not in places and not drawn:
                return
            places.pop(str(key), None)
        else:
            # One place per shelf, and it is this one. A shuffle draws a title, some
            # of it is watched, and the next draw leaves that place behind - eight of
            # them had built up, every one an episode half watched that the copy was
            # still holding and Continue watching could still offer. A shuffle is one
            # thing at a time: where it is now is the only place worth keeping.
            places = {str(key): {"at": int(position), "when": int(time.time())}}
        one["at"] = places
        Handler.round_moved(one)
        write_settings(stored)

    def shuffle_forget(self, key):
        """Drop one title's place from every shelf this viewer has a round on.

        A shelf keeps where a playing got to so it can be carried on. Only a playing
        started off the shelf says which shelf it is on, so anything else - the title's
        own page, another screen, marking it watched - finished the episode and left
        the place standing. The row then offered to resume something already watched
        and could not be got rid of by watching it again.
        """
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        moved = False
        for cid, one in (mine.get("shuffles") or {}).items():
            if not isinstance(one, dict):
                continue
            places = one.get("at") or {}
            if str(key) in places:
                places.pop(str(key), None)
                one["at"] = places
                Handler.round_moved(one)
                moved = True
            # the title the round was on is finished: Resume draws the next one
            if [str(k) for k in (one.get("played") or [])][-1:] == [str(key)]:
                one["done"] = str(key)
                Handler.round_moved(one)
                moved = True
        if moved:
            write_settings(stored)

    #: keys that have already asked for what follows them, so a report every ten
    #: seconds asks once. Cleared when it grows: this is a note, not a record.
    ASKED_NEXT = {}

    def keep_the_next_one_ready(self, key):
        """Somebody is half way through an episode: fetch the one after it from a pack.

        Only where the setting is on, only for an episode, and only where a pack has
        the next one and this machine does not. Asked once per episode: the player
        reports its place every few seconds, and every one of those reports is past the
        middle once the first one is.
        """
        key = str(key or "")
        import pd_torrents
        if not key.startswith("e") or not pd_torrents.next_wanted():
            return
        now = time.time()
        if now - (Handler.ASKED_NEXT.get(key) or 0) < 3600:
            return
        if len(Handler.ASKED_NEXT) > 200:
            Handler.ASKED_NEXT.clear()
        Handler.ASKED_NEXT[key] = now
        try:
            con = local().lib.db()
            try:
                row = con.execute(
                    """SELECT i.title AS show, e.season AS season, e.number AS number
                       FROM episode e JOIN item i ON i.id = e.item_id
                       WHERE e.id = ?""", (key,)).fetchone()
            finally:
                con.close()
            if not row or row["season"] is None or row["number"] is None:
                return
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            said = pd_torrents.fetch_next(row["show"], row["season"], row["number"],
                                          token, self.watcher(), cap)
            if said and said.get("ok") and not said.get("already"):
                with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                    f.write("%s fetching %s from a pack - %s is half way through %s%s"
                            % (time.strftime("%H:%M:%S"), said.get("episode"),
                               self.watcher(),
                               "S%02dE%02d" % (int(row["season"]), int(row["number"])),
                               chr(10)))
        except Exception:
            pass                    # a film being watched is not held up by this

    def title_finished(self, key):
        """A title finished or marked watched: its shelf places go, and it leaves the
        viewer's watchlist unless it is one of their favorites."""
        self.shuffle_forget(key)
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        # a favorite stays on the watchlist watched or not - that is what makes it one -
        # and an episode of a favorite programme with it
        kept = {str(k) for k in (mine.get("favorites") or [])}
        show = ""
        if str(key).startswith("e"):
            con = local().lib.db()
            try:
                row = con.execute("SELECT item_id FROM episode WHERE id=?",
                                  (str(key),)).fetchone()
                show = str(row["item_id"]) if row else ""
            finally:
                con.close()
        if str(key) in kept or (show and show in kept):
            return
        listed = [str(k) for k in (mine.get("watchlist") or [])]
        if str(key) in listed:
            mine["watchlist"] = [k for k in listed if k != str(key)]
            write_settings(stored)

    def shuffle_lists(self, cid, most=40):
        """What is coming, what was left part-way, and what the hat has used."""
        stored = read_settings() or {}
        who = self.viewer()
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        one = self.shuffle_round(mine, cid, make=False)
        _, pool = self.shuffle_shelf(cid)
        places = one.get("at") or {}

        def seconds(v):
            return int(v.get("at") or 0) if isinstance(v, dict) else int(v or 0)

        def when(v):
            return int(v.get("when") or 0) if isinstance(v, dict) else 0

        carry = []
        for key in sorted(places, key=lambda k: -when(places[k])):
            item = self.casual_item(key)
            if item:
                carry.append({"item": item, "key": str(key),
                              "at": seconds(places[key]), "when": when(places[key])})
        played = [str(k) for k in reversed(one.get("played") or [])][:most]
        return {"queue": [i for i in (self.casual_item(k)
                                      for k in (one.get("queue") or [])[:10]) if i],
                "carryOn": carry,
                "seen": [i for i in (self.casual_item(k) for k in played) if i],
                "played": len(one.get("played") or []),
                "run": int(one.get("run") or 1), "pool": len(pool)}

    def casual_places(self, who):
        """What this person left part-way in any shelf's shuffle, newest first, five at most."""
        stored = read_settings() or {}
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        places = []
        for one in (mine.get("shuffles") or {}).values():
            if not isinstance(one, dict):
                continue
            for key, value in (one.get("at") or {}).items():
                when = int(value.get("when") or 0) if isinstance(value, dict) else 0
                places.append((when, str(key)))
        return [k for _, k in sorted(places, reverse=True)][:5]

    def casual_ahead(self, who):
        """What this person's shuffle rounds draw next, most recently moved round first.

        Only queues already drawn: peeking does not take from the hat, and an empty
        queue means nothing is copied.
        """
        stored = read_settings() or {}
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        rounds = [r for r in (mine.get("shuffles") or {}).values() if isinstance(r, dict)]
        ahead = []
        for one in sorted(rounds, key=lambda r: -self.round_stamp(r)):
            played = set(str(k) for k in (one.get("played") or []))
            # The one the round is on comes first. Drawing a title writes it down as
            # played there and then, so a copy stopped wanting it at the very moment
            # somebody started watching it - and swept it while they were. It is the
            # one thing on the list that is certainly about to be read.
            now_on = self.shuffle_current(one)
            if now_on and now_on not in ahead:
                ahead.append(now_on)
            ahead += [str(k) for k in (one.get("queue") or [])
                      if str(k) not in played and str(k) not in ahead]
        # The hat's depth plus the one being watched. Ten counted both, so a round on
        # a title plus a queue ten deep came to eleven and the last of the hat was
        # dropped - the one the copy then had no file for.
        return ahead[:self.SHUFFLE_DEEP + 1]

    #: however few hours are asked for, a series is copied this far ahead: an
    #: evening is at least three episodes of anything.
    LEAST_AHEAD = 3

    #: What is on a screen this minute without anybody having chosen it. Kept where it
    #: is, never fetched, and never the start of a run of episodes.
    CASUAL_NOW = set()

    #: What the shuffle has drawn for somebody. Copied as it stands, with nothing
    #: taken after it: the next thing a shuffle plays is the next thing it draws.
    DRAWN = set()

    #: Keys last seen playing unchosen, and when. CASUAL_NOW holds only what a player
    #: is reporting this second, and the screen list below outlives that by design -
    #: so the episode a shuffle played a minute ago read as an ordinary one, and the
    #: run of episodes after it was stocked in season order for a shelf that is never
    #: played in season order. A key is dropped again the moment somebody chooses it.
    CASUAL_SEEN = {}
    CASUAL_KEEP = 200

    #: The most this machine has been seen to shift at once, and when that was seen.
    #: Nobody can ask a network how fast it is; what it has actually carried is the
    #: only honest answer, and it is only learnt while something is pushing it.
    LINK = {"home": {"seen": 0.0, "when": 0.0},
            "away": {"seen": 0.0, "when": 0.0}}

    #: How much of the line to leave alone above what the films are drawing. A film
    #: asks in bursts and a buffer that is filling wants more than its average.
    SPARE = 0.85

    #: And the least a copy may have, so it is never stopped outright.
    LEAST_MBIT = 4.0

    #: What a copy is allowed this second. Steered rather than calculated: the line is
    #: wireless and the number it carried an hour ago is not the number it carries
    #: when somebody walks between the aerials.
    PACE = {"home": {"mbit": 0.0, "when": 0.0},
            "away": {"mbit": 0.0, "when": 0.0}}

    #: The live rows the steering last saw. Handed in rather than fetched again: the
    #: caller has just taken the snapshot and taking a second one costs the lock.
    WATCHING_NOW = []

    #: Given up in one step when a film is struggling, and taken back in small ones.
    #: The other way round ends with the cache winning, which is the wrong way round.
    BACK_OFF = 0.55
    STEP_UP = 8.0

    #: A viewer getting this much less than its own running average, with a socket
    #: still open, is being starved rather than sitting on a full buffer.
    STARVED = 0.75

    #: Why a thing is copied, gathered under the name the settings use for it. A copy
    #: says which of these it will take this minute - each can be off, kept only inside
    #: its night hours, or kept always - and what it does not ask for is left out here
    #: rather than fetched and swept later.
    #: How many part-way films one person brings to a copy, and how old a place may
    #: be before it stops counting as being in the middle of something. Forty apiece
    #: with no age at all put a hundred and ten films in the queue - a guest who
    #: sampled fifty things last winter was still "part-way through" every one.
    PARTWAY_EACH = 6
    PARTWAY_DAYS = 30

    #: The title each viewer has on a screen and the one before it, newest first. A
    #: copy is asked to hold them until newer ones push them off, rather than for so
    #: many hours: an episode stopped being wanted the moment its player went quiet,
    #: and the copy deleted it with a minute of it still to run. The one before is
    #: kept so somebody going back an episode to remind themselves what happened does
    #: not wait for it to be fetched again. What finally takes them off the disk is
    #: the cap, which deletes the oldest first.
    SCREEN_LATELY = {}
    SCREEN_EACH = 2

    #: keys put on the list because they follow something, rather than because anybody
    #: asked for them; rebuilt every time the list is made
    FETCHED_AHEAD = set()

    KINDS = {
        "partway": ("part-way through", "the one before it"),
        "watchlist": ("on their watchlist", "a favourite", "on a shelf"),
        "lately": ("watched lately",),
        "shuffle": ("the shuffle's next", "left part-way in the shuffle"),
        "screen": ("on a screen now",),
    }

    @classmethod
    def kind_of(cls, why):
        """Which kind a reason belongs to, or none when it belongs to none."""
        for kind, reasons in cls.KINDS.items():
            if why in reasons:
                return kind
        return ""

    def partway_keys(self, con, who):
        """What one viewer is part-way through, by programme rather than by episode.

        The question asked here used to be whether a progress row had been stopped in
        the middle, which is part-way through an *episode* - so somebody working
        through a series and finishing each one was never part-way through anything.
        A programme watched that way had nothing kept ahead of it at all, and the
        episode Continue watching was offering was the one episode the copy did not
        hold: press play with the main server off and there was nothing to read.

        Continue watching has always answered this properly - a finished episode hands
        over to the first unwatched one after it - so the same two steps are taken
        here. Stopped in the middle still counts, and is preferred: it is where
        somebody actually is.
        """
        out, seen, shows = [], set(), set()          # (key, why) for each
        # What this viewer has cleared off Continue watching. Saying "I am done with
        # this" took it off the shelf and left the copy holding it: ninety-eight
        # titles were being kept part-way through while the shelf showed two. Aside
        # is the same answer to both questions.
        # Both places it can be written: the owner's own settings sit at the top of
        # the file and every other viewer's under their key, and the same programme
        # can be in either. The later of the two stands.
        aside = {}
        stored = self.settings_file()
        for holder in (stored, (stored.get("users") or {}).get(who) or {}):
            for k, when in (holder.get("deckAside") or {}).items():
                try:
                    if float(when or 0) > float(aside.get(k, 0) or 0):
                        aside[str(k)] = float(when or 0)
                except (TypeError, ValueError):
                    continue

        def take(key, why="part-way through"):
            key = str(key or "")
            if key and key not in seen:
                seen.add(key)
                out.append((key, why))

        since = int(time.time()) - self.PARTWAY_DAYS * 86400
        for row in con.execute(
                """SELECT key, position, duration, updated,
                          COALESCE(marked, 0) marked FROM progress WHERE who = ?
                   AND COALESCE(casual, 0) = 0 AND updated > ? AND position > 30
                   ORDER BY updated DESC LIMIT 400""", (who, since)):
            key = str(row["key"])
            # One programme once, as the shelf itself shows: a series watched out of
            # order would otherwise name a key for every gap in it.
            family = local().deck_family(con, key)
            if family in shows:
                continue
            shows.add(family)
            # put aside by hand, and nothing watched since: the same test the shelf
            # itself makes, so the two cannot disagree about what is still going on
            if float(aside.get(family, 0) or 0) >= float(row["updated"] or 0):
                continue
            # A mark made by hand is finished however little of it was played - the
            # shelf says so, and the two must not disagree about what is still going
            # on. Ninety-five titles were being kept part-way through while the shelf
            # showed two, and most of them had been ticked off by hand.
            done = bool(row["marked"]) or local().watched_through(
                row["position"], row["duration"])
            if not done:
                take(key)                  # stopped in the middle of this one
                # and the one before it, the same as for an episode just finished:
                # being part-way through one is the commoner way to be part-way
                # through a programme, and it was the branch that stopped here.
                if key.startswith("e"):
                    take(local().prev_episode_key(con, key), "the one before it")
                continue
            # Finished. What follows it is what somebody reaches for next - and only
            # an episode; a film that is over leads nowhere.
            if not key.startswith("e"):
                continue
            after = key
            for _ in range(400):           # a season is not longer than this
                after = local().next_episode_key(con, after)
                if not after or not local()._watched(con, after):
                    break
            take(after)
            # and the one just finished. It is what somebody goes back to - the end of
            # it missed, or the whole of it again - and holding only what comes next
            # meant the copy had the episode ahead and not the one behind, which is
            # the one they had actually been watching.
            take(key, "the one before it")
        return out

    def wanted_keys(self, con, hours, deck, episodes, mine, casual, whole, watched=None,
                    kinds=None):
        """What is worth copying, in the order to fetch it, as (key, why, who, live).

        One list of sources, asked in order. Nothing here reads a file or decides a
        rank: it says what the main server wants and why, and the order it says it in is
        the order it is wanted. Whether a thing is on a screen this minute travels
        with it, because that is the one fact that moves something to the front.

        `kinds` is what the copy will take this minute, by the names in KINDS. None
        means all of them, which is what every copy asked for before it could say.
        """
        import pd_localapi
        out = []
        said = set()
        taking = None if kinds is None else set(kinds)

        def want(key, why, who, live=False):
            key = str(key or "")
            if not key:
                return
            # a kind this copy is not taking now: off, or outside its hours
            if taking is not None:
                kind = self.kind_of(why)
                if kind and kind not in taking:
                    return
            if key in said:
                # reached twice - a watchlist and a screen, say. It is the same file
                # either way and it is wanted at the sooner of the two moments.
                for had in out:
                    if had[0] != key:
                        continue
                    if live:
                        had[3] = True
                    # and a watchlist or a favourite keeps its reason: those are copied
                    # watched or not, and the first reason reached was dropped as watched
                    # A reason that survives being watched wins over one that does
                    # not. "Watched lately" is reached first and is dropped again by
                    # the watched test below, so the episode just finished - named to
                    # be kept precisely because it has been watched - was thrown out
                    # by the reason it had already been given.
                    if (why in ("on their watchlist", "a favourite",
                                "the one before it")
                            and had[1] not in ("on their watchlist", "a favourite",
                                               "the one before it")):
                        had[1], had[2] = why, who
                return
            said.add(key)
            out.append([key, why, who, live])

        if mine:
            # One person's own cache on somebody else's machine: what they are in the
            # middle of, then what they mean to watch. Nothing about anybody else in
            # the main server is theirs to copy.
            for row in con.execute(
                    """SELECT key FROM progress WHERE who = ?
                       AND position > 30
                       AND position < """ + pd_localapi.FINISHED_SQL + """
                       AND COALESCE(casual, 0) = 0 AND updated > ?
                       ORDER BY updated DESC LIMIT ?""",
                    (mine, int(time.time()) - self.PARTWAY_DAYS * 86400,
                     self.PARTWAY_EACH)):
                want(row["key"], "part-way through", self.name_of(mine))
            for key in self.watchlist_of(mine)[:40]:
                want(key, "on their watchlist", self.name_of(mine))
            return out, set()

        # What is on a screen this minute. Somebody is sitting in front of it and the
        # next episode is wanted in twenty minutes, which nothing else on this list is.
        Handler.CASUAL_NOW = set()
        Handler.DRAWN = set()
        for one in (local().playing_now() or {}).values():
            if one.get("casual"):
                # On a screen, and nobody picked it. It is named all the same, because
                # the list is what keeps a file on the other machine's disk and the
                # film being read off it is the last one to delete. The mark below
                # stops it being fetched and stops the six episodes after it: what
                # comes next in a shuffle is whatever the shuffle draws.
                Handler.CASUAL_NOW.add(str(one.get("key") or ""))
                Handler.CASUAL_SEEN[str(one.get("key") or "")] = time.time()
                if len(Handler.CASUAL_SEEN) > Handler.CASUAL_KEEP:
                    for old_key in sorted(Handler.CASUAL_SEEN,
                                          key=lambda k: Handler.CASUAL_SEEN[k]
                                          )[:len(Handler.CASUAL_SEEN)
                                            - Handler.CASUAL_KEEP]:
                        Handler.CASUAL_SEEN.pop(old_key, None)
            elif one.get("key"):
                # chosen: it is an ordinary viewing again, and what follows it is
                # worth having
                Handler.CASUAL_SEEN.pop(str(one.get("key")), None)
            mark = str(one.get("key") or "")
            # Filed under the viewer, not under what they are called. playing_now()
            # lists a viewing twice - once under its key and once under its title -
            # and a name is not one thing either, so the same person ended up with
            # several lists of their own: seven episodes were named "on a screen now"
            # for one guest, where the most that can be on a screen is the one playing
            # and the one before it.
            whose = str(one.get("who") or one.get("name") or "")
            seen_by = self.name_of(whose)
            if mark:
                had = [k for k in (Handler.SCREEN_LATELY.get(whose) or [])
                       if k != mark]
                Handler.SCREEN_LATELY[whose] = ([mark] + had)[:Handler.SCREEN_EACH]
                if len(Handler.SCREEN_LATELY) > 40:
                    Handler.SCREEN_LATELY.clear()
            want(one.get("key"), "on a screen now", seen_by, True)
        # And the one before it, per viewer. A title leaves this list the second its
        # player stops reporting - the end of an episode, a pause long enough to be
        # closed, somebody stepping out - and the copy sweeps whatever the list no
        # longer names, which took an episode away with a minute of it still to run.
        # Holding the last two means the one just finished is still there while the
        # next is watched, and is there to go back to. The disk cap decides when it
        # finally goes.
        for whose, keys in list(Handler.SCREEN_LATELY.items()):
            seen_by = self.name_of(whose)
            for mark in keys[:Handler.SCREEN_EACH]:
                want(mark, "on a screen now", seen_by)
                # and it is still a shuffled one a minute after the player went
                # quiet, so nothing sequential is taken behind it
                if mark in Handler.CASUAL_SEEN:
                    Handler.CASUAL_NOW.add(mark)
            # And the episode before whatever is on now, worked out rather than
            # remembered. The list above is this server's memory of what it has seen
            # playing, so it is empty every time the server starts - and the one just
            # finished, the one somebody goes back to, was gone with it. The episode
            # before is the same episode however often this is restarted.
            first = keys[0] if keys else ""
            # Not for a shuffle. The episode before a drawn one is not the one that
            # was just watched and not one anybody is going back to - it is simply
            # the episode with the number below it, and naming it put an ordinary
            # key on the list, which the run of episodes below then followed: ten
            # files in season order per viewer, per draw, for a shelf being played
            # in no order at all. What a shuffle leaves part-way is kept by
            # casual_places; what it will play next is the hat.
            if str(first).startswith("e") and first not in Handler.CASUAL_SEEN:
                before = local().prev_episode_key(con, first)
                if before:
                    want(before, "on a screen now", seen_by)
        # The shuffle is left out here too. A casual playing writes a log line like
        # any other - it has to, or Now playing and the watch log would lie - but
        # "the house watched this lately" is a reason to copy the rest of the series,
        # and nobody watched it. One evening of shuffled episodes put five of a
        # season on the other machine at eight in the evening.
        # Named, because somebody watched it. "The main server" is not a viewer: it has no
        # invitation, it is on nobody's cache, and a queue that says it cannot be
        # filtered down to one person's shelf.
        for row in con.execute(
                "SELECT key, who FROM watchlog WHERE COALESCE(casual, 0) = 0 "
                "ORDER BY updated DESC LIMIT 12"):
            want(row["key"], "watched lately", self.name_of(row["who"]))

        listed = set()
        if deck:
            # What the people this is kept for are part-way through, and what they
            # mean to watch. Before this machine sleeps that is what somebody reaches
            # for next, and the other machine is the one that will be awake.
            decks, lists, shuffles = self.cached_for()
            shelved = {}             # each shelf read once, however many people have it
            for who in decks:
                for key, why in self.partway_keys(con, who):
                    want(key, why, self.name_of(who))
            for who in lists:
                marked = self.watchlist_of(who)[:40]
                # Whole list off: a programme's night's worth, not every marked episode
                if not whole:
                    marked = self.first_unwatched(con, marked, episodes, watched,
                                                  self.name_of(who))
                for key in marked:
                    listed.add(str(key))
                    want(key, "on their watchlist", self.name_of(who))
                # A programme on a shelf somebody made is a programme meant to be
                # watched. Only the programmes: a shelf is mostly films, and naming
                # every one of those had the copy fetching hundreds of titles nobody
                # had asked for. Only the next unwatched episodes of each, as many as
                # the episodes setting allows - the bound the watchlist already uses.
                stored = self.settings_file()
                theirs = (self.viewer_settings(stored) if who == self.viewer()
                          else ((stored.get("users") or {}).get(who) or {}))
                for shelf in (theirs.get("collections") or []):
                    if not (isinstance(shelf, dict) and shelf.get("id")):
                        continue
                    at = str(shelf.get("id"))
                    shows = shelved.get(at)
                    if shows is None:
                        # A shelf is a rule over the library, so reading one is a pass
                        # over every title: thirteen of them, per person, took the
                        # round from under a second to eight and a half.
                        keys = [str(k) for k in self.collection_keys(con, shelf)
                                if not str(k).startswith("o")]
                        shows = []
                        if keys:
                            rows = con.execute(
                                "SELECT id FROM item WHERE type='show' AND id IN (%s)"
                                % ",".join("?" * len(keys)), keys).fetchall()
                            shows = [str(r["id"]) for r in rows]
                        shelved[at] = shows
                    if not shows:
                        continue
                    # A shelf somebody plays shuffled is not one they are working
                    # through, and stocking it in order fills the copy with episodes
                    # the hat will almost never draw next: a shelf of a long series
                    # took ten slots with the first unwatched episodes in order while
                    # its own queue - the episodes actually coming up - got four.
                    # The round's queue is kept below, by casual_ahead, so this is
                    # not the shelf going uncopied; it is the same shelf read the way
                    # it is actually played.
                    round_here = (theirs.get("shuffles") or {}).get(at) or {}
                    if who in shuffles and (round_here.get("queue") or []):
                        continue
                    for key in self.first_unwatched(con, shows, episodes, watched,
                                                    self.name_of(who)):
                        listed.add(str(key))
                        want(key, "on a shelf", self.name_of(who))
            # favourites, everybody's and all of them: marked to be on both machines
            everyone = read_settings() or {}
            for who, one in (list((everyone.get("users") or {}).items())
                             + [("me", everyone)]):
                for key in ((one or {}).get("favorites") or []):
                    want(key, "a favourite", self.name_of(who))
            for who in shuffles:
                if casual <= 0:
                    continue          # "do not copy", said on the machine keeping them
                # An evening of casual watching is the one nobody picks a film for.
                # What was left part-way comes first, because carrying on with what
                # was actually on is the thing the other machine could not do at all:
                # a casual playing writes no row Continue watching reads, so nothing
                # ever put a half-watched shuffled episode over there.
                # Ten in all, which is the depth of the queue - not ten and then some.
                # Everything left part-way, and the whole hat behind it. Ten counted
                # across both lists meant three part-way places left seven slots for a
                # queue ten deep, so the copy never held the whole of it - and a hat
                # held in part is one a copy cannot draw from once those few are
                # watched. The ten is the hat's own depth; it is not a budget the
                # part-way places spend first.
                theirs, said_here = [], set()
                for key in self.casual_places(who):
                    key = str(key)
                    if key in said_here:
                        continue
                    said_here.add(key)
                    theirs.append((key, "left part-way in the shuffle"))
                taken_ahead = 0
                for key in self.casual_ahead(who):
                    key = str(key)
                    if key in said_here:
                        continue
                    if taken_ahead >= self.SHUFFLE_DEEP:
                        break
                    said_here.add(key)
                    theirs.append((key, "the shuffle's next"))
                    taken_ahead += 1
                # How many, and how much of an evening, are the same two numbers
                # that govern everything else kept ahead: Episodes ahead, and Hours
                # ahead at most. A shelf of half-hour comedies and one of hour-long
                # drama are not the same number of files, and the settings already
                # say so.
                # Ten is the ceiling whatever the settings say, because ten is all
                # the shuffle knows: the queue is drawn ten deep and the eleventh
                # has not been decided. Asking for twenty would mean inventing ten.
                # The whole hat, however long it comes to. The hours setting cut it
                # to about seven of the ten - and the estimate runs high for a file
                # nobody has measured, so it cut it further - which left the copy
                # holding part of a queue that moves every time somebody presses next.
                # A hat the copy holds only part of is a hat it cannot draw from at
                # all once those few have been watched, measured this morning: of 153
                # episodes the shuffle could still draw, the copy had none.
                for key, why_it in theirs:
                    Handler.DRAWN.add(key)
                    want(key, why_it, self.name_of(who))
        return out, listed

    @staticmethod
    def first_unwatched(con, keys, episodes, watched, who):
        """Each series' episodes cut to the first `episodes` unwatched, in order; other keys kept."""
        eps = [str(k) for k in keys if is_episode(k)]
        shows = {}
        if eps:
            try:
                rows = con.execute("SELECT id, item_id FROM episode WHERE id IN (%s)"
                                   % ",".join("?" * len(eps)), eps).fetchall()
                shows = {str(r["id"]): r["item_id"] for r in rows}
            except Exception:
                return list(keys)
        taken, out = {}, []
        for k in keys:
            show = shows.get(str(k))
            if show is None:
                out.append(k)
                continue
            if watched and watched(str(k), who):
                continue
            if taken.get(show, 0) >= episodes:
                continue
            taken[show] = taken.get(show, 0) + 1
            out.append(k)
        return out

    def episodes_after(self, con, plan, listed, hours, episodes, whole, watched):
        """The episodes following each series already wanted, in order.

        Walked in the order the series were asked for, so the one somebody is
        watching now is walked first and its next episode is second on the list
        rather than fortieth. Walking in the order a set happens to hold things in is
        what makes a queue of episodes read as random when each series is in order.
        """
        after = []
        said = set(k for k, _, _, _ in plan)
        # one window per programme and viewer: each place a series was reached from (screen,
        # watch log, part-way) walked its own, so Episodes ahead 10 queued 21 of one series
        ahead = {}
        for key, why, who, live in list(plan):
            # Nothing follows a shuffled draw. What comes after one of those is
            # whatever the shuffle draws next, which is already on this list - and
            # taking six episodes after each of ten draws made seventy files out of
            # ten, in season order, for a shelf whose whole point is that it is not.
            if (not key.startswith("e") or key in Handler.CASUAL_NOW
                    or key in Handler.DRAWN):
                continue
            # Every unwatched episode of a programme somebody put on a list is a
            # different question from enough for tonight: they mean to watch all of
            # it. Otherwise, forward until either limit is reached.
            enough = 9999 if (whole and key in listed) else episodes
            try:
                row = con.execute("SELECT item_id FROM episode WHERE id=?", (str(key),)).fetchone()
                show = row["item_id"] if row else key
            except Exception:
                show = key
            slot = ahead.setdefault((show, who), [0.0, 0])     # seconds covered, episodes ahead
            at, tried = key, 0
            while (slot[1] < enough and tried < 200
                   and (slot[0] < hours * 3600 or slot[1] < self.LEAST_AHEAD
                        or enough > episodes)):
                nxt = local().next_episode_key(con, at)
                if not nxt:
                    break
                tried += 1
                at = nxt
                if watched(nxt, who):
                    continue          # walked past, not copied and not counted
                if nxt not in said:
                    said.add(nxt)
                    after.append([nxt, why, who, live])
                    # asked for by nobody: it follows something somebody watched. Held
                    # to the age rule below, where a title somebody chose is not.
                    Handler.FETCHED_AHEAD.add(str(nxt))
                # already listed from another source still fills this viewer's window
                slot[1] += 1
                slot[0] += self.how_long(con, nxt)
        return after

    @staticmethod
    def in_series_order(con, plan):
        """Each programme's episodes in season and episode order, where it stands.

        Episodes reach the list from three places - a watchlist, somebody part-way
        through, and the walk ahead of both - and each added where it landed, so a
        series read as S01E01, S01E04, S01E02. A programme keeps the place of its
        earliest entry, so the list still reads in the order the main server wants things;
        it is only the inside of each series that is put right.
        """
        want = [k for k, _, _, _ in plan
                if is_episode(k)]
        if len(want) < 2:
            return plan
        facts = {}
        try:
            rows = con.execute(
                "SELECT id, item_id, season, number FROM episode WHERE id IN (%s)"
                % ",".join("?" * len(want)), [str(k) for k in want]).fetchall()
            for r in rows:
                facts[str(r["id"])] = (r["item_id"], r["season"] or 0,
                                          r["number"] or 0)
        except Exception:
            return plan                    # nothing known: leave it as it is
        at = {}
        for i, one in enumerate(plan):
            at[one[0]] = i
        done, out = set(), []
        for one in plan:
            key = one[0]
            if key in done:
                continue
            if key not in facts:
                done.add(key)
                out.append(one)
                continue
            show = facts[key][0]
            group = [x for x in plan if facts.get(x[0], (None,))[0] == show]
            # whatever is on a screen now stays at the front of its own series: it is
            # what somebody is watching, not the earliest episode they own
            group.sort(key=lambda x: (0 if x[3] else 1, facts[x[0]][1:]))
            for x in group:
                if x[0] not in done:
                    done.add(x[0])
                    out.append(x)
        return out

    @staticmethod
    def how_long(con, key):
        """Episode runtime in seconds, for filling the hours window.

        Falls back to the programme's average where this episode has no measured file.
        Counted as zero, an unmeasured episode never filled the window, so "four hours
        ahead" took ten episodes of a programme with 23 of its 55 unmeasured.
        """
        if not is_episode(key):
            return 0.0
        try:
            row = con.execute("SELECT duration FROM file WHERE episode_id=? "
                              "AND duration > 0 LIMIT 1", (str(key),)).fetchone()
            if row and row["duration"]:
                return float(row["duration"])
            row = con.execute(
                """SELECT AVG(f.duration) d FROM file f
                   JOIN episode e ON e.id = f.episode_id
                   WHERE f.duration > 0 AND e.item_id =
                         (SELECT item_id FROM episode WHERE id = ?)""",
                (str(key),)).fetchone()
            return float((row and row["d"]) or 0)
        except Exception:
            return 0.0

    def worth_copying(self, hours=4.0, deck=False, episodes=6, mine=None,
                      casual=0.0, whole=False, only="", kinds=None):
        """The files a machine keeping copies should have, in the order to fetch them.

        Three steps, and each is somewhere else: what the main server wants and why, the
        episodes that follow it, and the rows a copy can act on. The order is the
        order the sources were asked in, with one exception - whatever is on a screen
        this minute leads, because it is wanted in twenty minutes and nothing else on
        the list is. A queue with several kinds of priority in it is a queue nobody
        can predict, and predicting it is the whole point of having one.
        """
        con = local().lib.db()
        local().who = mine or self.viewer()
        langs = self.languages_kept(mine)
        try:
            # watched by the person a title is kept for, not by whoever asked: the
            # owner having seen a film is no reason to drop it from a guest's list
            asking = local().who
            tokens = {}
            for row in INVITES.load():
                if row.get("token"):
                    tokens.setdefault(self.name_of(row["token"]), row["token"])

            def watched(key, who=None):
                local().who = tokens.get(who, asking) if who else asking
                try:
                    return local()._watched(con, key)
                except Exception:
                    return False
                finally:
                    local().who = asking

            now_named = time.time()
            wrote_ahead = set()
            Handler.FETCHED_AHEAD = set()
            plan, listed = self.wanted_keys(con, hours, deck, episodes, mine,
                                            casual, whole, watched, kinds)
            if not mine:
                plan += self.episodes_after(con, plan, listed, hours, episodes,
                                            whole, watched)
            plan = self.in_series_order(con, plan)
            want, sided = [], set()
            for key, why, who, live in plan:
                # A watchlist is kept whole, watched or not: it is what somebody asked
                # for. So is whatever is on a screen this minute - a film passes the
                # watched mark with minutes still to run, and dropping it there had the
                # copy delete the file under somebody who was still watching it. The
                # one machine that could have carried on was the one being turned off.
                if (why not in ("on their watchlist", "a favourite", "on a screen now",
                                "the one before it")
                        and watched(key, who)):
                    continue
                # Fetched before anybody asked - the next episodes of something
                # watched, the shuffle's next draws - and still not watched a month
                # later. Nobody is going to: it has been on the disk for thirty days
                # waiting. What somebody chose by name is not held to this.
                if (str(key) in Handler.FETCHED_AHEAD
                        or why == "the shuffle's next"):
                    first = read_ahead().get(str(key))
                    if first is None:
                        AHEAD_SINCE[str(key)] = now_named
                        wrote_ahead.add(str(key))
                    elif now_named - float(first) > AHEAD_DAYS * 86400:
                        continue
                want += self.rows_for(con, key, why, who, live, langs, sided)
            # and the book written back when it gained anything, with what has long
            # since fallen off the list dropped from it: a season watched last winter
            # is not worth remembering the first sight of for ever
            if wrote_ahead:
                for old_key in [k for k, when in list(AHEAD_SINCE.items())
                                if now_named - float(when)
                                > AHEAD_DAYS * 86400 * 2]:
                    AHEAD_SINCE.pop(old_key, None)
                write_ahead()
        finally:
            con.close()

        # A cache kept for one person holds what that person is watching. The machine
        # in somebody's own room filled up with the rest of the main server's evening.
        if only:
            wanted_by = only.strip().lower()
            want = [w for w in want
                    if str(w.get("who") or "").strip().lower() == wanted_by
                    or self.name_of(w.get("who")).strip().lower() == wanted_by]

        # what each person may have copied to the other machine in a week
        want = self.within_weekly_sync(want)

        # One thing lifts anything above the list: a screen that is on this minute,
        # because what it needs is needed in twenty minutes. Everything else keeps the
        # place the sources gave it. Moving a title to the front by hand used to be a
        # second kind of priority, and two kinds of priority make a queue nobody can
        # predict - which is the whole of what a queue is for.
        want.sort(key=lambda w: 0 if w.get("hot") else 1)

        # Why each of these is wanted, where the serving side can find it: by the time
        # a file is being sent the reason is three functions away, and a log of what
        # moved says nothing about who wanted it.
        # The first reason for a file, not the last. One episode is often wanted by
        # two people at once - somebody's shelf and somebody else's shuffle - and
        # writing each in turn left whoever came last owning it: a guest's shuffle
        # picks were filed in the copy log under the owner, who had only ever
        # matched them with a shelf rule. The list is already sorted with what is
        # wanted soonest first, so the first entry is the reason worth keeping.
        fresh = {}
        for w in want:
            mark = str(w.get("key") or "")
            if mark:
                fresh.setdefault(mark, (w.get("why") or "", w.get("who") or ""))
        # what this pass says wins, and a file still going out under the last pass
        # keeps the reason it started with
        merged = dict(Handler.WHY_BY_KEY)
        merged.update(fresh)
        Handler.WHY_BY_KEY = fresh if len(merged) > 4000 else merged
        return want

    def weekly_limits(self, name):
        """Gigabytes a week per person, by the name the cache list files them under."""
        caps = {}
        stored = read_settings() or {}
        for row in INVITES.load():
            try:
                gb = float(row.get(name) or 0)
            except (TypeError, ValueError):
                gb = 0.0
            if gb > 0 and row.get("token"):
                caps[self.name_of(row["token"]).strip().lower()] = gb
        try:
            mine = float(stored.get(name) or 0)
        except (TypeError, ValueError):
            mine = 0.0
        if mine > 0 and not stored.get("ownerIs"):
            caps[self.name_of("me").strip().lower()] = mine
        return caps

    def within_weekly_sync(self, want):
        """The cache list, less what would take a person past their week's gigabytes.

        What is on a screen now is taken regardless, and so is what the other machine
        already holds: neither costs anything more.
        """
        caps = self.weekly_limits("syncGbWeek")
        if not caps:
            return want
        import pd_traffic
        since = time.time() - 7 * 86400
        spent = {}
        for row in pd_traffic.copies(100000):
            if int(row.get("when") or 0) < since:
                continue
            who = str(row.get("for") or "").strip().lower()
            spent[who] = spent.get(who, 0.0) + float(row.get("gb") or 0)
        held = set(Handler.COPIES.get("keys") or [])
        left = {who: cap - spent.get(who, 0.0) for who, cap in caps.items()}
        out, dropped = [], set()
        for w in want:
            who = str(w.get("who") or "").strip().lower()
            key = str(w.get("key") or "")
            if who not in left or w.get("hot") or key in held:
                out.append(w)
                continue
            if w.get("side") is not None:
                if key not in dropped:
                    out.append(w)
                continue
            gb = float(w.get("size") or 0) / 1e9
            if gb > left[who]:
                dropped.add(key)
                continue
            left[who] -= gb
            out.append(w)
        return out

    def rows_for(self, con, key, why, who, live, langs, sided, mi=0):
        """One wanted title as the rows a copy can act on: the file, then its subtitles.

        Nothing decides anything here. It is the film, what is inside it, what it is
        called and what sits beside it - everything the other machine would otherwise
        have to open the file or reach the internet to find out.
        """
        found = local().file_for(key, mi) or {}
        path = found.get("file")
        if not path or not os.path.exists(path):
            return []
        row = con.execute(
            """SELECT id, size, duration, container, vcodec, acodec, width,
                      height, channels, bitrate FROM file WHERE path=?""",
            (path,)).fetchone()
        if not row:
            return []
        title, subtitle, _ = local().now_playing_fields(key)
        out = [{
            "key": key, "part": row["id"], "art": self.art_of(key), "hot": live,
            "why": why, "who": who,
            # keep it where it is, but do not go and get it
            "casual": str(key) in Handler.CASUAL_NOW,
            "name": os.path.basename(path),
            "size": row["size"] or os.path.getsize(path),
            # what the other machine should find when it has it
            "mark": self.quick_mark(path),
            # and what is inside it, so a copy does not read as an unknown container -
            # which a player answers by asking for a transcode of a file it could
            # have played as it stands
            "facts": {k: row[k] for k in
                      ("duration", "container", "vcodec", "acodec",
                       "width", "height", "channels", "bitrate")},
            # Series and numbering first: a queue is read down a column of series,
            # not of episode names.
            "title": (" - ".join(
                x for x in ((subtitle, title) if str(key).startswith("e")
                            else (title, subtitle)) if x).replace("  ", " ")),
        }]
        # and the subtitles beside it, in the languages the people this is kept for
        # read. A film on the other machine with no subtitle is a film half the main server
        # cannot watch, and a subtitle is a few kilobytes.
        # a film takes all of its subtitles; an episode takes the languages being
        # kept for, because a series is hundreds of files and a film is one
        for side in self.sides_worth_copying(path, key, langs, row["id"],
                                             every=not str(key).startswith("e")):
            if side["name"] in sided:
                continue
            sided.add(side["name"])
            out.append(dict(side, hot=live))
        return out

    def sync_subtitle(self, key, mi=0, index=0, skey="", sub="", save=True):
        """Put this subtitle in step with the film, by listening to the film.

        Returns what it worked out and whether it was sure enough to write down. A
        subtitle that belongs to another release comes back with an offset; one that
        belongs to another film comes back saying so, and nothing is written.
        """
        import pd_sync
        found = local().file_for(key, mi) or {}
        video = found.get("file")
        if not video or not os.path.exists(video):
            return {"error": "that film is not here"}
        # A subtitle still being written from this film's sound covers the first
        # stretch and no more. Sliding that against the whole film finds agreement
        # where there is none - one such measurement put a film sixty seconds out.
        # Finished, it can be measured like any other.
        try:
            import pd_localapi as _la
            if index < 0:
                beside = _la.sidecars(video)
                mine = beside[-index - 1] if -index - 1 < len(beside) else None
                if mine and "ai-gen" in mine["file"]:
                    length = float((local().file_for(key, mi) or {}).get("duration") or 0)
                    reaches = _la.sidecar_end(mine["file"])
                    if self.being_written(mine["file"]) or (
                            length and reaches and reaches < length * 0.8):
                        return {"error": "This subtitle is still being written from "
                                         "the film's sound. It can be put in step "
                                         "once it is finished.",
                                "offset": 0.0, "rate": 1.0, "parts": [],
                                "kind": "none", "sure": False, "saved": False}
        except Exception:
            pass
        try:
            vtt = self.subtitle_text(video, index, key, mi)
        except IndexError:
            # the files beside the video have changed under this request: one was
            # written while the film was playing, and every place after it moved
            return {"error": "that subtitle has moved - open the list again"}
        except Exception as e:
            return {"error": "could not read that subtitle: %s" % e}
        if not vtt or vtt.count("-->") < 20:
            return {"error": "that subtitle has too little in it to place"}
        mark = video + "|sweep|" + str(os.path.getmtime(video))
        with self.HEARD_LOCK:
            heard = self.HEARD.get(mark)
        if heard is None:
            # several windows across the whole film: one answer says how late the
            # subtitle is, several say whether it stays that way
            heard = pd_sync.sweep(video)
            with self.HEARD_LOCK:
                if len(self.HEARD) > 8:
                    self.HEARD.clear()      # a handful of films, not a memory leak
                self.HEARD[mark] = heard
        if not heard:
            return {"error": "could not listen to that film"}
        length = pd_sync.film_length(video)
        seen = pd_sync.profile(heard, vtt)
        parts, sure = pd_sync.plan(
            seen, length, corroborate=lambda by: pd_sync.seconded(heard, vtt, by))
        if not parts:
            # No window was sure on its own. They may still agree with each other: take
            # the best few peaks from each and look for a line that most of them sit on.
            # A subtitle drifting through a film gives exactly that, and no single
            # window can see it.
            offers = pd_sync.offered(heard, vtt)
            line = pd_sync.through(offers)
            if line:
                rate, base, support, weight = line
                sure = round(min(0.9, (support / float(max(1, len(offers))))
                                 * (weight / support) * 3), 3)
                # A line can be drawn through any scatter of peaks, and a subtitle cut
                # for another edit gives nothing but scatter: windows saying 23, 28,
                # -95, 26, 41, 53 seconds, none of them matching more than a tenth of
                # what was heard. That was being fitted and written down as a drift of
                # half a per cent, which is a correction nobody asked for on a file
                # that simply does not belong to this cut. The windows themselves have
                # to have heard something.
                heard_well = [w[3] for w in seen if w[3] > 0]
                if not heard_well or (sum(heard_well) / len(heard_well)) < 0.2:
                    line, sure = None, 0.0
                else:
                    parts = [(0.0, rate, base)]
        if not parts:
            # Rather than give up, listen in
            # more places for less time at each: eleven windows of three minutes find a
            # signal where five of four minutes could not, and give any answer more
            # chances to be seconded.
            close = video + "|sweep11|" + str(os.path.getmtime(video))
            with self.HEARD_LOCK:
                denser = self.HEARD.get(close)
            if denser is None:
                denser = pd_sync.sweep(video, length, count=11, span=180)
                with self.HEARD_LOCK:
                    if len(self.HEARD) > 8:
                        self.HEARD.clear()
                    self.HEARD[close] = denser
            if denser:
                closer = pd_sync.profile(denser, vtt)
                found, how = pd_sync.plan(
                    closer, length,
                    corroborate=lambda by: pd_sync.seconded(denser, vtt, by))
                if not found:
                    offers = pd_sync.offered(denser, vtt)
                    line = pd_sync.through(offers)
                    if line:
                        rate, base, support, weight = line
                        how = round(min(0.9, (support / float(max(1, len(offers))))
                                        * (weight / support) * 3), 3)
                        # same guard as above: only fit a line when the windows
                        # behind it matched something
                        strong = [w[3] for w in closer if w[3] > 0]
                        if strong and (sum(strong) / len(strong)) >= 0.2:
                            found = [(0.0, rate, base)]
                        else:
                            found, how = [], 0.0
                if found:
                    heard, seen, parts, sure = denser, closer, found, how
        if not parts:
            # Nothing could be placed. That is what a subtitle made at another
            # framerate looks like - four per cent adrift is ten seconds inside a
            # four-minute window, so every window reads as noise - and there are only
            # a handful of ratios anybody ships, so they are tried by name.
            found = pd_sync.rated(heard, vtt)
            if found and abs(found[0] - 1.0) > 1e-9:
                parts, sure = [(0.0, found[0], found[1])], found[2]
        if len(parts) > 1:
            # the sweep only says the step is somewhere in the gap between two windows;
            # listening inside that gap says where, to under a minute
            tight = [parts[0]]
            for i in range(1, len(parts)):
                before, after = parts[i - 1][2], parts[i][2]
                # the last window that ended before the step, and the first that
                # began after it: the change is somewhere between those two
                lo = max([w[1] for w in seen if w[1] <= parts[i][0]]
                         or [parts[i][0] - 300])
                hi = min([w[0] for w in seen if w[0] >= parts[i][0]]
                         or [parts[i][0] + 300])
                at = pd_sync.pin_step(video, vtt, lo, hi, before, after)
                tight.append((at, parts[i][1], parts[i][2]))
            parts = tight
        # a rate that moves the end of the film by a fraction of a second is a flat
        # line with rounding on it: say so plainly rather than calling it a drift
        parts = [(a, 1.0 if pd_sync.flat_enough(r, length) else r, sh)
                 for a, r, sh in parts]
        offset = parts[0][2] if parts else 0.0
        out = {"offset": offset, "rate": parts[0][1] if parts else 1.0,
               "parts": [[a, r, sh] for a, r, sh in parts],
               "kind": ("parts" if len(parts) > 1
                        else "drift" if parts and abs(parts[0][1] - 1.0) > 1e-9
                        else "static"),
               "drift": len(parts) > 1,
               "windows": [[round(a), round(b), off, s] for a, b, off, s in seen],
               "confidence": sure, "sure": sure >= pd_sync.SURE_ENOUGH, "saved": False}
        if not out["sure"]:
            # Say which way it failed, and where it can be shown rather than guessed.
            # Another subtitle beside the same film, cut for this release, settles it:
            # a file that is merely late says the same number all the way through.
            proof = None
            try:
                proof = self.another_cut(video, index)
            except Exception:
                proof = None
            if proof and proof.get("another_cut"):
                out["why"] = (
                    "this is a subtitle for another cut of the episode. Against \"%s\", "
                    "which matches this file, %d lines in common are out by anything "
                    "from %+.0f to %+.0f seconds - so no single correction can fit it. "
                    "Use that one instead."
                    % (proof["against"], proof["lines"],
                       proof["middle"] - proof["spread"] / 2.0,
                       proof["middle"] + proof["spread"] / 2.0))
                out["another_cut"] = proof
            elif proof:
                out["why"] = (
                    "the film could not be listened to well enough to be sure, but "
                    "against \"%s\" this file is steady at %+.1f seconds - that is "
                    "probably the correction it wants."
                    % (proof["against"], proof["middle"]))
                out["against"] = proof
            else:
                # the one named after this very release has nothing better to be
                # compared against - it is the thing others are compared against
                own = None
                try:
                    import pd_localapi as _la
                    beside = _la.sidecars(video)
                    fits = self.best_beside(video)
                    own = (index < 0 and fits and beside and
                           beside[-index - 1].get("file") == fits.get("file"))
                except Exception:
                    own = None
                if own:
                    out["why"] = ("this is the subtitle cut for the very release this "
                                  "file is - the film itself is too talkative to line "
                                  "anything up against, but this is the one to use")
                    out["release_match"] = True
                else:
                    out["why"] = ("this subtitle does not line up with the film "
                                  "anywhere in particular - it may belong to another "
                                  "release")
            return out
        if save and out["sure"]:
            shelf = skey or ("l" + str(key))
            name = self.fit_name(video, index)
            # The correction lives in the file the server hands out whenever it is more
            # than one number, and in the viewer's own shift when it is exactly one -
            # so a plain "two seconds late" is still a number anybody can see and undo.
            # Either way, whatever was nudged by hand was measured against the old
            # timing and no longer applies.
            # A rate is a plan whether or not it comes in parts: stored as a plain
            # number, the rate would be thrown away and the drift come back.
            if len(parts) > 1 or abs(parts[0][1] - 1.0) > 1e-9:
                self.set_sub_fit(name, parts)
                self.set_sub_shift(shelf, 0.0, sub)
                out["offset"] = 0.0
            else:
                self.set_sub_fit(name, [])
                self.set_sub_shift(shelf, offset, sub, how="sync")
            out["saved"] = True
        return out

    def wants_a_party(self):
        """Whether this viewer takes part in watch parties at all.

        Their own answer, not the owner's: a chat box on somebody's screen while they
        are watching something is a thing they should be able to say no to, and saying
        no should mean nothing arrives rather than merely nothing being shown. The
        owner can still send a notice - that is the server speaking to its own
        screens, not a room full of people.
        """
        return self.viewer_settings(self.settings_file()).get("watchParty") is not False

    #: What a screen is called to somebody who may not know where it is.
    A_SCREEN = "a screen in the house"

    def named_for_reader(self, name):
        """A name safe to show to whoever is asking.

        An address identifies a machine and, in a house, a person. The owner is
        looking at their own network and may see them; a guest is looking at other
        people, and gets the name on an invitation or nothing at all.
        """
        said = str(name or "")
        if self.role == "owner":
            return said
        looks_like_address = (said.replace(".", "").isdigit() and said.count(".") == 3
                              or ":" in said)
        return self.A_SCREEN if looks_like_address or not said else said

    def party_key(self):
        """Who this caller is, as far as a party is concerned.

        A guest is their invitation, wherever they are watching from; anybody in the
        house is the screen they are on, because a house has no names for its
        televisions.
        """
        if self.role == "guest" and self.guest_name:
            return "guest:" + self.guest_name
        return "here:" + self.client_address[0]

    def said_safely(self, rows):
        """Messages as this reader may see them: names, never addresses."""
        if self.role == "owner":
            return rows
        out = []
        for m in rows:
            said = dict(m)
            said["who"] = said["from"] = self.named_for_reader(
                m.get("from") or m.get("who"))
            said.pop("to", None)
            out.append(said)
        return out

    def join_party(self):
        """Mark this caller present. Reading the room is being in it."""
        if not PARTY["on"]:
            return
        key = self.party_key()
        row = PARTY["who"].setdefault(key, {
            "name": self.watcher(), "where": self.client_address[0],
            "kind": self.device_kind() or ""})
        row["seen"] = int(time.time())

    def addressed_to_me(self, whom):
        """Whether a message marked for somebody is for the screen now asking.

        Empty is the main server: everybody on this network. An address is one screen -
        which is how a test reaches the television and nothing else. "all" is the
        house and everybody watching from outside it, which is what a room is.
        """
        want = str(whom or "").strip().lower()
        if not want:
            return self.in_the_house()
        if want == "all":
            return True
        if want == "party":
            return PARTY["on"] and self.party_key() in PARTY["who"]
        return self.client_address[0].lower() == want

    def managed_from_the_house(self):
        """Whether this request is the server this machine follows, managing it.

        Off unless somebody turns it on here. A machine that keeps copies usually
        lives in a cupboard, and walking to it to change a number is the thing this
        avoids; the address it follows is the only one it will take orders from.
        """
        # the library's settings, where the following is set up: the settings file
        # never held it, so this answered no whatever the switch said
        one = (self.library_settings_plain() or {}).get("follow") or {}
        if not (one.get("on") and one.get("allowRemote") and one.get("master")):
            return False
        master = str(one.get("master") or "")
        host = master.split("//")[-1].split(":")[0].split("/")[0]
        return bool(host) and self.client_address[0] == host

    def in_the_house(self):
        """Whether this request came from the same network as the server.

        Replacing the program is not something to offer somebody watching from a
        train: they cannot see what happens next, and the machine they would be
        restarting is not in front of them.
        """
        where = self.client_address[0]
        if where in ("127.0.0.1", "::1", "localhost"):
            return True
        try:
            import ipaddress
            return ipaddress.ip_address(where).is_private
        except Exception:
            return False

    def library_settings_plain(self):
        """The library's settings, from the library if it opens and from its file if not.

        Library.config() is a read of library.json, but getting to it opens the
        library first - and a library that cannot open then takes the settings down
        with it.
        """
        try:
            return local().lib.config()
        except Exception:
            try:
                with open(os.path.join(ROOT, "library.json"), encoding="utf-8") as f:
                    got = json.load(f)
                return got if isinstance(got, dict) else {}
            except Exception:
                return {}

    def installer_folders(self):
        """Where an installer made on this machine would be sitting."""
        return (STATIC, os.path.join(CODE, "build"))

    def installer_here(self):
        """The newest Palladium-Setup beside this program, if there is one.

        Two places, because there are two ways to be running: a build keeps it in
        static/ next to the web client, and a tree that has just built one has it in
        build/. Newest wins - a stale installer is worse than none.
        """
        looked = []
        for where in (STATIC, os.path.join(CODE, "build")):
            try:
                for name in os.listdir(where):
                    low = name.lower()
                    if low.startswith("palladium-setup") and low.endswith(".exe"):
                        full = os.path.join(where, name)
                        looked.append((os.path.getmtime(full), full))
            except OSError:
                continue
        return max(looked)[1] if looked else ""

    def send_installer(self):
        """Hand over the installer, named for the version it is."""
        where = self.installer_here()
        if not where:
            self.send_error(404, "No installer on this machine")
            return
        try:
            size = os.path.getsize(where)
            f = open(where, "rb")
        except OSError:
            self.send_error(404, "No installer on this machine")
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.microsoft.portable-executable")
            self.send_header("Content-Length", str(size))
            # named for the build, so a Downloads folder says which one it holds
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(where))
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    def find_subtitles(self, key, language):
        client, why = self.opensubtitles()
        if not client:
            return {"error": why, "results": []}
        src, title, year, season, episode = self._title_for_search(key)
        if not title:
            return {"error": "Nothing here to search for.", "results": []}
        results, err = client.search(title, year, language,
                                     (src or {}).get("file"), season, episode,
                                     imdb=self.imdb_for(key))
        # A season the scanner has renumbered has two numbers for the same episode:
        # ours, and the one the release carries. Subtitle sites are numbered both ways
        # by different uploaders, so both are asked and the answers are put together -
        # the release's own first among candidates that are equally this episode.
        import pd_localapi
        shift = (pd_localapi.LocalAPI.numbering_shift(
            (src or {}).get("file"), season, episode) if episode is not None else 0) or 0
        if shift:
            other, _ = client.search(title, year, language, (src or {}).get("file"),
                                     season, episode + shift, imdb=self.imdb_for(key))
            seen = {r.get("id") for r in (results or [])}
            for r in other or []:
                if r.get("id") in seen:
                    continue
                r["shifted"] = True
                results = (results or []) + [r]
                seen.add(r.get("id"))
        if not results and self.imdb_for(key):
            # The id is the better question when it is answered, and no question at all
            # when the two databases disagree about which film this is - a remake takes
            # the name and the old id keeps the subtitles. Ask by name before giving up.
            byname, err2 = client.search(title, year, language,
                                         (src or {}).get("file"), season, episode)
            if byname:
                results, err = byname, ""
            elif not err:
                err = err2
        # Numbering is not agreed between databases: one series' E31 was our E32 for
        # a whole season, so each episode carried the next one's title
        # sitting one place out. So when nothing the numbered search returned mentions
        # this episode by name, the neighbours are asked as well - the right subtitle
        # is usually one number away, and it can be recognised by its title.
        here = self.episode_facts(key)
        own = (here.get("title") or "").strip()
        mine_words = self._words(own)

        def about_this_episode(r):
            words = self._words(r.get("name") or "")
            enough = (len(mine_words) if len(mine_words) <= 2
                      else max(2, (len(mine_words) + 1) // 2))
            return bool(mine_words) and len(mine_words & words) >= enough

        if own and season is not None and episode is not None and                 not any(about_this_episode(r) for r in (results or [])):
            seen = {r.get("id") for r in (results or [])}
            for nudge in (-1, 1, -2, 2):
                if episode + nudge < 1:
                    continue
                near, _ = client.search(title, year, language,
                                        (src or {}).get("file"), season,
                                        episode + nudge, imdb=self.imdb_for(key))
                keep = [r for r in (near or [])
                        if r.get("id") not in seen and about_this_episode(r)]
                if keep:
                    results = (results or []) + keep
                    seen |= {r.get("id") for r in keep}
                    break
        if own and season is not None:
            named, _ = client.search("%s %s" % (title, own), year, language,
                                     (src or {}).get("file"), None, None)
            if named:
                # A search by name has no season to hold it down, so a title and
                # an episode name together also find unrelated programmes.
                # Only what mentions this programme is worth adding.
                show_words = self._words(title)
                seen = {r.get("id") for r in (results or [])}
                results = (results or []) + [
                    r for r in named
                    if r.get("id") not in seen
                    and len(show_words & self._words(r.get("name") or "")) >=
                        max(1, (len(show_words) + 1) // 2)]
        # ordered by how likely each is to be in time, so the first one offered is the
        # first one worth trying
        if results:
            results = self.rank_subtitles(results, self.series_release(key),
                                          here.get("title", ""), here.get("season"),
                                          here.get("release", here.get("number")),
                                          (src or {}).get("file", ""))
            # and each one says whether it is the release this episode was proved on -
            # the same episode, so the names match outright rather than by family
            proved = (self.confirmed_release(key, language) or "").strip().lower()
            # whether this candidate is even for this episode. Judged by the episode's
            # own title, because that is the thing two databases agree on when their
            # numbering does not.
            mine = self._words(own)
            for r in results:
                name = (r.get("name") or "").strip().lower()
                r["confirmed"] = bool(proved) and (
                    name == proved or name.rstrip(".srt") == proved.rstrip(".srt"))
                # This episode, or another one. A short title has to appear whole:
                # "Diary" matched Diary of a Wimpy Kid on one word out of one, which
                # is how a film ends up offered as an episode of a cartoon.
                words = self._words(name)
                enough = len(mine) if len(mine) <= 2 else max(2, (len(mine) + 1) // 2)
                # A number in the name settles it before any words do. Both halves
                # of a two-parter carry the same title, so the second half passed the
                # word test for the first and - once the release decided the order -
                # led the list. A name that says which episode it is, and says a
                # different one, is not this episode whatever it is called.
                said = re.search(r"s(\d{1,2})[\s._-]?e(\d{1,3})|(\d{1,2})x(\d{1,3})",
                                 name)
                if said and season is not None and episode is not None:
                    got = ((int(said.group(1)), int(said.group(2))) if said.group(1)
                           else (int(said.group(3)), int(said.group(4))))
                    if got != (int(season), int(episode)):
                        r["episode"] = False
                        continue
                r["episode"] = (bool(mine)
                                and len(mine & words) >= enough
                                and len(self._words(title) & words) >=
                                    max(1, (len(self._words(title)) + 1) // 2))
            # what this file is called, and which of these are named after it
            common = self.title_words((src or {}).get("file") or "")
            mine = (src or {}).get("file") or ""
            for r in results:
                # named after this very file: whoever made it had this release open
                r["sameName"] = self.same_name(mine, r.get("name") or "")
                # A hash match whose name is for a different release. The moviehash
                # is filesize plus the first and last 64 kB, attached by the uploader,
                # so it is wrong often enough to matter: a DVDRip and a one-word
                # upload both claimed a 1080p BluRay. Still listed, marked, and ranked
                # below candidates whose name agrees with the release.
                # A hash match is doubted when its name is for another release -
                # but a name carrying this episode's own title is not another
                # release, whatever else it shares. Subtitle sites number and name
                # episodes their own way, and a name that says which episode it is
                # can share not one word with the file it belongs to.
                names_it = bool(mine_words) and len(
                    mine_words & self._words(r.get("name") or "")) >= max(
                    1, (len(mine_words) + 1) // 2)
                r["hashOdd"] = (bool(r.get("fromHash")) and not r["sameName"]
                                and not names_it
                                and self.release_agrees(
                                    mine, r.get("name") or "", common) < 2)
            # Being the right episode outranks everything. A subtitle cut to this
            # very release is worthless if it is a different episode, which is what
            # the numbered search offers first when the databases disagree.
            #
            # A hash match is the right episode by definition: it was uploaded against
            # a file byte for byte the same as this one. It used to be demoted here
            # for not spelling the episode's title out in its name, which release
            # names rarely do.
            for r in results:
                if r.get("fromHash") and not r.get("hashOdd"):
                    r["episode"] = True
            # and inside that, the release. A subtitle is cut to one release and the
            # one named after this file's is the one whose timing fits - which the
            # download list was leaving wherever the provider happened to put it.
            def order(r):
                return (0 if r.get("episode") else 1,
                        # found by the number the file itself carries: asked for
                        # second, offered first among answers that are equally this
                        # episode
                        0 if r.get("shifted") else 1,
                        # Above the hash. A hash match proves the same file; the same
                        # name proves the same release, and when both are on offer the
                        # one that says so is the one somebody looking at the list
                        # expects to be first.
                        0 if r.get("sameName") else 1,
                        0 if (r.get("fromHash") and not r.get("hashOdd")) else 1,
                        -self.matches_release(mine, r.get("name") or "", common),
                        -int(r.get("downloads") or 0))
            results.sort(key=order)
            fitting = sum(1 for r in results if r.get("episode"))
            if own and not fitting:
                # every result is for some other episode: worth saying before somebody
                # downloads one and watches an episode out of step
                err = (err or "") + ("These all look like a different episode - "
                                     "nothing here mentions \"%s\". The numbering at "
                                     "OpenSubtitles may not match this library."
                                     % own)
        return {"results": results, "error": err, "title": title}

    def episode_facts(self, key):
        """An episode's own title and numbering, for judging a subtitle by.

        Two numbers: ours, and the one the releases use. A candidate's name carries
        the second, so that is what its "s04e29" has to be read against.
        """
        if not str(key).startswith("e"):
            return {}
        con = local().lib.db()
        try:
            row = con.execute("SELECT title, season, number FROM episode WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return {}
            said = dict(row)
            import pd_localapi
            src = local().file_for(key, 0) or {}
            said["release"] = said["number"] + (pd_localapi.LocalAPI.numbering_shift(
                src.get("file"), said["season"], said["number"]) or 0)
            return said
        except (TypeError, ValueError):
            return {}
        finally:
            con.close()

    # words in every release name, which say nothing about which release it is
    COMMON_WORDS = {"the", "web", "dvd", "bluray", "brrip", "webrip", "hdtv", "srt",
                    "eng", "english", "subs", "subtitles", "season", "episode"}

    def same_family(self, one, two, show_title="", episode_title=""):
        """Are these two names the same release, one episode apart?

        What they share, once the show, the episode and the words every release uses
        are set aside, is the group and the encode - and that is what carries timing
        from one episode to the next.
        """
        if not one or not two:
            return False
        aside = (self._words(show_title) | self._words(episode_title)
                 | self.COMMON_WORDS)
        left = (self._words(one) & self._words(two)) - aside
        return len(left) >= 2

    def confirmed_release(self, key, language="en"):
        """The release verified for this title in one language, if any.

        Per title and per language: subtitles are cut to a particular file, the episode
        after this one is frequently somebody else's rip, and an English subtitle says
        nothing about the Swedish one.
        """
        marked = self.verified_for(
            (self.settings_file().get("subsOk", {}) or {}).get(str(key)))
        row = marked.get(language) or marked.get((language or "")[:2]) or {}
        return row.get("release", "") if isinstance(row, dict) else (row or "")

    def series_release(self, key):
        """The release this series has settled on, if it has settled on one."""
        show = self.show_of(key) if str(key).startswith("e") else None
        if not show:
            return ""
        chosen = (self.settings_file().get("subsFor", {}) or {}).get(show) or {}
        return chosen.get("release", "")

    def fetch_subtitle(self, key, file_id, language, remember, release=""):
        client, why = self.opensubtitles()
        if not client:
            return {"ok": False, "error": why}
        src, title, year, season, episode = self._title_for_search(key)
        if not src:
            return {"ok": False, "error": "No file behind that."}
        text, err = client.download(file_id)
        if not text:
            return {"ok": False, "error": err}
        import pd_subs
        # The same file may already be there under another name: fetched automatically
        # for the next episode, and then asked for by hand while watching it. Two
        # identical files beside one video are two entries in the menu that do the same
        # thing, and nobody can tell which is which.
        same = self.same_subtitle_beside(src["file"], text)
        if same:
            if remember:
                self.note_subtitle_attempt(key, language, release)
            if release:
                self.remember_pick(key, release)
            return {"ok": True, "file": os.path.basename(same), "already": True}
        # named for the release, so trying another variant keeps this one
        out = pd_subs.sidecar_path(src["file"], language, release)
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            return {"ok": False, "error": "Could not write beside the film: %s" % e}
        # recorded as an attempt only: it becomes the series' choice if this episode
        # is watched to the end with it, which is the only evidence that it fits
        if remember:
            self.note_subtitle_attempt(key, language, release)
        # taking a subtitle is choosing it: it leads the list from now on
        if release:
            self.remember_pick(key, release)
        if release:
            # the file is named for the film, so the release name would otherwise be
            # lost - and it is the only thing that tells one variant from another
            stored = read_settings()
            names = stored.setdefault("subsRelease", {})
            names[out] = release
            if len(names) > 400:                 # a cap, so it cannot grow forever
                for old in list(names)[:100]:
                    names.pop(old, None)
            write_settings(stored)
        note_download()
        return {"ok": True, "file": os.path.basename(out)}

    @staticmethod
    def same_subtitle_beside(video, text):
        """A file beside this video holding exactly this, or nothing.

        Compared on what is in them rather than on their names: the same subtitle
        arrives named for the release when it is asked for by hand and named for the
        film when it is fetched by itself.
        """
        import pd_localapi
        want = (text or "").replace(chr(13) + chr(10), chr(10)).strip()
        if not want:
            return ""
        for side in pd_localapi.sidecars(video):
            where = side.get("file") or ""
            try:
                with open(where, encoding="utf-8", errors="replace") as f:
                    if f.read().replace(chr(13) + chr(10), chr(10)).strip() == want:
                        return where
            except OSError:
                continue
        return ""

    def show_of(self, episode_key):
        con = local().lib.db()
        try:
            row = con.execute("SELECT item_id FROM episode WHERE id=?",
                              (str(episode_key),)).fetchone()
            return str(row["item_id"]) if row else None
        except (TypeError, ValueError):
            return None
        finally:
            con.close()

    @staticmethod
    def verified_for(record):
        """A title's verified subtitles as a map by language, whatever shape it is in."""
        record = record or {}
        if "release" in record:                 # the older single-record shape
            return {(record.get("language") or "en"): {"release": record["release"]}}
        return {k: v for k, v in record.items()}

    def verify_subtitle(self, key, name, language="en", index=None):
        """Mark one subtitle as the right one for this title, by hand - or unmark it.

        The same record an episode watched to the end would have written: a statement
        about this file's timing, which a person watching knows in seconds. An empty
        name takes the statement back, because the wrong file vouched for once would
        otherwise lead the list for ever.
        """
        if not key:
            return {"ok": False, "error": "Nothing named."}
        if name and index is None:
            # only a subtitle that is actually beside the film can be vouched for
            import pd_localapi
            src = local().file_for(key, 0) or {}
            beside = [x["name"] for x in pd_localapi.sidecars(src.get("file") or "")]
            if name not in beside:
                return {"ok": False,
                        "error": "That subtitle is not beside the film any more."}
        stored = self.settings_file()
        done = stored.get("subsOk", {}) or {}
        done[str(key)] = self.verified_for(done.get(str(key)))
        if index is not None:
            # a track inside the film: its stream number is what identifies it, since
            # the name in a container is whatever the person who made it typed
            done[str(key)][language] = {"index": index}
        elif name:
            done[str(key)][language] = {"release": name}
        else:
            done[str(key)].pop(language, None)
            if not done[str(key)]:
                done.pop(str(key), None)
        stored["subsOk"] = done
        # a series keeps the last confirmed release as its guess for what to try next
        show = self.show_of(key) if str(key).startswith("e") else None
        if show:
            subs = stored.get("subsFor", {}) or {}
            subs[show] = {"language": language, "release": name, "confirmed": True}
            stored["subsFor"] = subs
        # the series' guess follows the last thing actually verified
        show = self.show_of(key) if str(key).startswith("e") else None
        if show and not name:
            (stored.get("subsFor", {}) or {}).pop(show, None)
        write_settings(stored, merge=False)
        if name and index is None:
            self.remember_pick(key, name)      # verified is also chosen
        return {"ok": True, "verified": name or ("track %d" % index
                                                 if index is not None else "")}

    def remove_sidecar(self, key, name, mi=0):
        """Delete one subtitle file from beside its video, and forget it."""
        import pd_localapi
        src = local().file_for(key, mi) or {}
        video = src.get("file")
        if not video:
            return {"ok": False, "error": "No file behind that."}
        wanted = (name or "").strip().lower()
        for side in pd_localapi.sidecars(video):
            if (side["name"] or "").strip().lower() != wanted:
                continue
            try:
                os.remove(side["file"])
            except OSError as e:
                return {"ok": False, "error": str(e)[:120]}
            stored = self.settings_file()
            (stored.get("subsRelease", {}) or {}).pop(side["file"], None)
            # and any note that pointed at it, so nothing offers it again
            for shelf in ("subsOk", "subsPick"):
                rows = stored.get(shelf, {}) or {}
                for at, value in list(rows.items()):
                    said = value.get("release") if isinstance(value, dict) else value
                    if (said or "").strip().lower() == wanted:
                        rows.pop(at, None)
                stored[shelf] = rows
            write_settings(stored, merge=False)
            return {"ok": True, "removed": os.path.basename(side["file"])}
        return {"ok": False, "error": "No such subtitle beside that film."}

    def hide_track(self, key, index, mi=0, show=False):
        """Stop offering one of a video's own subtitle tracks. Reversible."""
        con = local().lib.db()
        try:
            src = local().file_for(key, mi) or {}
            row = con.execute("SELECT id FROM file WHERE path=?",
                              (src.get("file") or "",)).fetchone()
        finally:
            con.close()
        if not row:
            return {"ok": False, "error": "No file behind that."}
        stored = self.settings_file()
        shelf = stored.get("subsHidden", {}) or {}
        rows = set(int(n) for n in (shelf.get(str(row["id"])) or []))
        rows.discard(index) if show else rows.add(index)
        if rows:
            shelf[str(row["id"])] = sorted(rows)
        else:
            shelf.pop(str(row["id"]), None)
        stored["subsHidden"] = shelf
        write_settings(stored, merge=False)
        return {"ok": True, "hidden": sorted(rows)}

    def remember_copy(self, key, path):
        """Note which file of a title was chosen, for whoever chose it."""
        if not key:
            return
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        picks = mine.get("copyPick", {}) or {}
        if path:
            picks[str(key)] = path
        else:
            picks.pop(str(key), None)          # back to the best one
        mine["copyPick"] = picks
        write_settings(stored, merge=False)

    def remember_pick(self, key, name):
        """Note which subtitle was chosen for a title, for whoever chose it."""
        if not key:
            return
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        picks = mine.get("subsPick", {}) or {}
        if name:
            picks[str(key)] = name
        else:
            picks.pop(str(key), None)          # subtitles off is also a choice
        mine["subsPick"] = picks
        write_settings(stored, merge=False)

    def note_subtitle_attempt(self, episode_key, language, release):
        """What is currently being tried on this episode."""
        stored = self.settings_file()
        tried = stored.get("subsTried", {}) or {}
        tried[str(episode_key)] = {"language": language, "release": release or ""}
        stored["subsTried"] = tried
        write_settings(stored, merge=False)

    def accent_default(self, stored=None):
        """The colour the server draws with, for anybody who has not chosen."""
        stored = self.settings_file() if stored is None else stored
        want = str(stored.get("accent", "") or "").lower()
        return want if want in [c for c, _ in ACCENTS] else ACCENT

    def screen_now(self):
        """Which kind of screen is asking: the television app, a phone, or a browser."""
        said = (self.headers.get("X-Palladium-App") or "").lower()
        if said.startswith("android"):
            return "tv" if said.endswith(" tv") else "phone"
        return "web"

    def backdrop_word(self, said):
        """One of on, poster, off. True and False are what older clients stored.

        Nobody having said: a poster stands on a title's own page and nowhere else.
        Behind the shelves as well is a taste, to be asked for rather than given.
        """
        if said is None:
            return "poster"
        if said is True:
            return "on"
        if said is False:
            return "off"
        word = str(said).strip().lower()
        return word if word in ("on", "poster", "off") else "poster"

    def backdrop_now(self, device=None):
        """Where a poster stands on one kind of screen: on, poster, or off.

        on is behind the shelves as well, poster is a title's own page and nowhere
        else. Per viewer and per screen: a television across the room and a phone at
        arm's length are not the same picture, and one person's answer is not another's.
        """
        which = self.device_of(device or self.screen_now())
        mine = self.viewer_settings(self.settings_file()).get("myBackdrop")
        if isinstance(mine, dict):
            return self.backdrop_word(mine.get(which))
        # what it used to be: one answer for every screen
        return self.backdrop_word(mine)

    def backdrop_all(self):
        """Every screen's answer, for a settings page that draws one row each."""
        return {d: self.backdrop_now(d) for d in self.DEVICES}

    def accent_now(self):
        """The colour this viewer draws with, on every screen they use.

        The server's answer is the default and nothing more: somebody who wants their
        own library green should have it green on the television and on the phone
        alike, without deciding it for the rest of the main server.
        """
        stored = self.settings_file()
        mine = str(self.viewer_settings(stored).get("myAccent", "") or "").lower()
        if mine in [c for c, _ in ACCENTS]:
            return mine
        return self.accent_default(stored)

    #: Who a browser may be sent a burned-in bitmap subtitle for. Burning is a full
    #: re-encode of the video, so it is worth deciding rather than assuming. The app
    #: is unaffected either way: it decodes PGS and VobSub itself.
    BURN_FOR = ("all", "home", "none")

    def home_default(self, stored=None):
        """The front page anybody meets before they arrange their own."""
        stored = self.settings_file() if stored is None else stored
        return [str(x) for x in (stored.get("homeRowsDefault") or [])]

    def home_rows(self):
        """This viewer's front page: their own arrangement, or the server's."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored).get("homeRows") or []
        return [str(x) for x in mine] or self.home_default(stored)

    def burn_policy(self, stored=None):
        """Who may be sent a burn: "all", "home" (the default) or "none".

        Named apart from burn_for(), which answers a different question - whether one
        particular track can be burned out of this file.
        """
        stored = self.settings_file() if stored is None else stored
        want = str(stored.get("burnFor", "") or "").lower()
        if want in self.BURN_FOR:
            return want
        # the older two-state setting, before "none" existed
        return "all" if stored.get("burnAway") else "home"

    def may_burn(self):
        """Whether this caller may be sent one."""
        mode = self.burn_policy()
        return bool(mode == "all" or (mode == "home" and self.at_home()))

    def auto_scan(self, stored=None):
        """Whether the server puts subtitles in step by itself, unless told not to."""
        return bool((stored if stored is not None
                     else self.settings_file()).get("autoScan", True))

    def scans_for(self, stored=None):
        """The answer for the viewer this request belongs to."""
        stored = self.settings_file() if stored is None else stored
        mine = self.viewer_settings(stored)
        return bool(mine.get("autoSync", self.auto_scan(stored)))

    def set_auto_scan(self, on):
        """Set the server's answer: the default for anybody who has not chosen.

        Whoever has turned it off for themselves in the player keeps that. Their
        answer was given about their own screen and this one is not an instruction to
        forget it.
        """
        stored = self.settings_file()
        stored["autoScan"] = bool(on)
        write_settings(stored, merge=False)
        return bool(on)

    def set_accent(self, code, everyone=False):
        """Choose a colour: this viewer's, or the server's answer for everybody.

        An empty code gives a viewer back the server's colour rather than pinning
        them to whatever it happens to be today.
        """
        want = str(code or "").lower().strip()
        stored = self.settings_file()
        if everyone:
            # the owner's to set, and only theirs: it is what a new viewer meets
            if self.role != "owner" or want not in [c for c, _ in ACCENTS]:
                return self.accent_now()
            stored["accent"] = want
        else:
            mine = self.viewer_settings(stored)
            if want in [c for c, _ in ACCENTS]:
                mine["myAccent"] = want
            else:
                mine.pop("myAccent", None)        # back to the server's
        write_settings(stored, merge=False)
        return self.accent_now()

    def subtitle_since(self, key):
        """When the subtitle on screen came on, as last written down.

        The memory of it is lost with the server; the file is not. An episode watched
        across a restart - which is every episode watched while this is being worked on -
        otherwise reported the position of the first request after it and was refused.
        """
        one = (self.settings_file().get("subSince", {}) or {}).get(str(key))
        if not one or len(one) < 2:
            return None
        # the fourth field says whether the position means anything; older rows have
        # none, and those were all written by a first sighting
        sure = bool(one[3]) if len(one) > 3 else False
        return (one[0], float(one[1]) if sure else None)

    def remember_since(self, key, using, at, sure=True):
        stored = self.settings_file()
        kept = stored.get("subSince", {}) or {}
        kept[str(key)] = [using, round(float(at), 1), int(time.time()), bool(sure)]
        # a few evenings' worth; the oldest go first
        if len(kept) > 40:
            for old in sorted(kept, key=lambda k: kept[k][2])[:20]:
                kept.pop(old, None)
        stored["subSince"] = kept
        write_settings(stored, merge=False)

    def promote_subtitle_choice(self, episode_key, using="", since=None):
        """The episode was finished: whatever it was watched with is what fits.

        Called as progress is reported, so trying three subtitles and watching the
        third teaches the series about the third and forgets the other two.

        Watching an episode through with subtitles turned off is the same statement in
        reverse - this series does not want them - and it stops the fetching. Unless the
        choice has been confirmed: a subtitle that has carried an episode to the end is
        known to fit, and one quiet evening without it is no reason to lose it.
        """
        stored = self.settings_file()
        if using == "off":
            show = self.show_of(episode_key)
            if show:
                subs = stored.get("subsFor", {}) or {}
                chosen = subs.get(show) or {}
                if chosen.get("confirmed"):
                    pass                     # proved itself once; it stays
                elif subs.pop(show, None) is not None:
                    stored["subsFor"] = subs
            (stored.get("subsTried", {}) or {}).pop(str(episode_key), None)
            stored["subsTried"] = stored.get("subsTried", {}) or {}
            write_settings(stored, merge=False)
            return
        tried = (stored.get("subsTried", {}) or {})
        want = tried.get(str(episode_key))
        # Some clients name the track they are drawing; the players only say that
        # something is on. Comparing "on" with the name of the file that was fetched
        # said they were different things and refused every automatic verification -
        # which is exactly what it looked like from the sofa.
        named = using not in ("", "on", "off", "none")
        if want and named and want.get("release") and using != want.get("release"):
            # something else was on screen at the end, so the fetched file is not what
            # this watching is evidence about
            want = None
        if not want and named:
            # nothing fetched for this one, or something else was watched with: the
            # track named in the reports is the evidence. Anything can earn the mark -
            # a file that was already beside the film, or one inside it.
            want = self.watched_with(episode_key, using)
        if not want:
            return
        # and it has to have been on for the whole of it. A track chosen with ten
        # minutes to go is not what the last two hours were watched with.
        con = local().lib.db()
        try:
            row = con.execute(
                "SELECT position, duration FROM progress WHERE key=? AND who=?",
                (str(episode_key), self.viewer())).fetchone()
        finally:
            con.close()
        length = (row["duration"] if row else 0) or 0
        if since is not None and length and since > 0.3 * length:
            return
        fetched = tried.pop(str(episode_key), None)
        tried = fetched or want
        # a film has no series to teach, but the subtitle it was watched through with
        # is verified just the same
        show = self.show_of(episode_key) if str(episode_key).startswith("e") else None
        # Confirmed by the only evidence there is - this episode, watched through with
        # this subtitle. It is recorded against the episode because that is what it is
        # true of: the next episode may well be somebody else's rip.
        done = stored.get("subsOk", {}) or {}
        done[str(episode_key)] = self.verified_for(done.get(str(episode_key)))
        # a file is known by its name, a track inside the film by its number
        lang = tried.get("language") or "en"
        mark = ({"index": tried["index"]} if tried.get("index") is not None
                else {"release": tried.get("release", "")})
        # Progress is reported every few seconds, and every report after the credits
        # comes through here. The mark is written each time, harmlessly - but the news
        # is the moment it goes from unverified to verified, and nothing after that.
        # Comparing the whole record was not enough: the first report knows the file
        # that was fetched and later ones know the track on screen, so the two forms
        # differed and announced themselves in turn.
        news = lang not in done[str(episode_key)]
        done[str(episode_key)][lang] = mark
        stored["subsOk"] = done
        # The series keeps it as a starting guess for what to fetch next time - only
        # from a file that was fetched, since a stream number means nothing on the next
        # episode's release.
        subs = stored.get("subsFor", {}) or {}
        if show and fetched:
            subs[show] = dict(fetched, confirmed=True)
        stored["subsFor"] = subs
        stored["subsTried"] = stored.get("subsTried", {}) or {}
        write_settings(stored, merge=False)
        # what to tell the client, the once: a panel that is open turns its tick green
        # as this happens rather than the next time somebody opens it
        if not news:
            return None
        return dict(mark, key=str(episode_key), language=lang)

    def watched_with(self, key, using):
        """Which track the reports name, as a record to verify - or nothing.

        Clients name a file beside the film by its name and a track inside it as
        "t" and the stream number, which is what the timing corrections are filed
        under too.
        """
        if not using or using in ("off", "none"):
            return None
        src = local().file_for(key, 0) or {}
        video = src.get("file") or ""
        if not video:
            return None
        if str(using).startswith("t") and str(using)[1:].isdigit():
            index = str(using)
            con = local().lib.db()
            try:
                row = con.execute(
                    "SELECT streams FROM file WHERE path=?", (video,)).fetchone()
                tracks = json.loads((row["streams"] if row else None) or "[]")
            except Exception:
                tracks = []
            finally:
                con.close()
            for t in tracks:
                if t.get("index") == index:
                    return {"language": (t.get("lang") or "en")[:2], "index": index}
            return None
        import pd_localapi
        for side in pd_localapi.sidecars(video):
            if side.get("name") == using:
                return {"language": (side.get("lang") or "en")[:2],
                        "release": side.get("name")}
        return None

    TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")

    def title_words(self, video):
        """The words that name the programme and the episode, rather than the release."""
        con = local().lib.db()
        try:
            row = con.execute(
                """SELECT i.title show, e.title episode FROM file f
                   LEFT JOIN item i ON i.id = f.item_id
                   LEFT JOIN episode e ON e.id = f.episode_id
                   LEFT JOIN item si ON si.id = e.item_id
                   WHERE f.path = ?""", (video,)).fetchone()
        except Exception:
            row = None
        finally:
            con.close()
        said = " ".join(x for x in [(row["show"] if row else "") or "",
                                    (row["episode"] if row else "") or ""] if x)
        # the series' own name reaches the file through the episode when the item row
        # is the programme rather than the film
        return self._words(said)

    def picked_for(self, key):
        """What this viewer settled on for this title, if anything."""
        mine = self.viewer_settings(self.settings_file())
        return (mine.get("subsPick", {}) or {}).get(str(key), "")

    @staticmethod
    def bare_name(name):
        """A name with nothing in it but letters and numbers, for comparing two.

        Releases are written with dots, spaces, brackets and dashes at the whim of
        whoever packed them, and the same release is written every one of those ways.
        """
        import re as _re
        text = _re.sub(r"\.(srt|vtt|ass|ssa|sub|mkv|mp4|avi|m4v|ts)$", "",
                       (name or "").strip(), flags=_re.I)
        return _re.sub(r"[^a-z0-9]+", "", text.lower())

    def same_name(self, video, name):
        """Whether this subtitle is named after this very file.

        The strongest evidence there is short of a hash: whoever made it had this
        release in front of them. A trailing language or a "-en" is still the same
        name - what is left over has to be short enough to be a tag rather than a
        different release.
        """
        ours = self.bare_name(os.path.basename(video or ""))
        theirs = self.bare_name(name)
        if not ours or not theirs:
            return False
        if ours == theirs:
            return True
        longer, shorter = (theirs, ours) if len(theirs) > len(ours) else (ours, theirs)
        return longer.startswith(shorter) and len(longer) - len(shorter) <= 6

    def release_agrees(self, video, name, ignore=None):
        """How much of the release these two names share, ignoring bare numbers.

        A year is in every name for a film and says nothing about which rip this is,
        so it is not evidence of anything - and it was the whole of the agreement
        between a 1080p BluRay and a DVDRip that claimed to be the same file.
        """
        theirs = {w for w in self._words(name) if not w.isdigit()} - (ignore or set())
        ours = {w for w in self._words(
            os.path.splitext(os.path.basename(video or ""))[0])
            if not w.isdigit()} - (ignore or set())
        return len(ours & theirs)

    def matches_release(self, video, name, ignore=None):
        """How much a subtitle's name has in common with the video's own name.

        A subtitle is cut to one release; the one named after the same release as the
        file is the one whose timing fits. Words every episode shares - the programme,
        the numbering - say nothing, so what is left is the release itself: the source,
        the group, the encoder.
        """
        theirs = self._words(name) - (ignore or set())
        ours = self._words(os.path.splitext(os.path.basename(video))[0]) - (ignore or set())
        return len(ours & theirs)

    def best_beside(self, video, language=""):
        """The file beside this video most likely to be in time with it.

        Scored on the release alone. The programme's name and the episode's number are
        in every one of these names and say nothing about which is in time - counting
        them made a subtitle for another edit look like a perfect match, because it
        agreed about the name of the programme.
        """
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        if not beside:
            return None
        # the programme's name and the episode's, which every one of these carries and
        # none of which says anything about timing
        common = self.title_words(video)
        want = (language or "").lower()[:2]
        def score(side):
            same = (side.get("lang") or "").lower()[:2] == want if want else False
            return (self.matches_release(video, side.get("name") or "", common), same)
        best = max(beside, key=score)
        return best if score(best)[0] >= 2 else None

    def another_cut(self, video, index):
        """Whether this subtitle is for a different edit of the film - and the proof.

        Listening to the film cannot tell a late subtitle from one made for another
        cut. Another subtitle beside the same file can: if the two say the same lines
        at times that differ by a steady amount, one is simply late; if the difference
        wanders by half a minute across the film, they are different edits and no
        single correction can help.
        """
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        # A file beside the video is addressed by its place in this list, and the list
        # is sorted by name: a subtitle arriving while the film plays - one being
        # written from the sound - shifts every place after it. A number that no
        # longer points at anything is a question about a subtitle that has moved,
        # not a fault.
        if index >= 0 or not beside or -index - 1 >= len(beside):
            return None
        mine = beside[-index - 1]
        fits = self.best_beside(video)
        if not fits or fits.get("file") == mine.get("file"):
            return None

        def lines(path):
            out = {}
            try:
                with open(path, "rb") as f:
                    raw = f.read().decode("utf-8", "replace")
            except OSError:
                return out
            for block in re.split(r"\n\s*\n", raw):
                m = re.search(r"(\d\d):(\d\d):(\d\d)[,.](\d+)\s*-->", block)
                if not m:
                    continue
                at = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 +
                      int(m.group(3)) + int(m.group(4)[:3]) / 1000.0)
                said = re.sub(r"[^a-z ]", " ", block[m.end():].lower())
                said = " ".join(said.split())
                if len(said) > 12:
                    out.setdefault(said, []).append(at)
            return out

        ours, theirs = lines(mine.get("file")), lines(fits.get("file"))
        both = [(ours[said][0] - theirs[said][0]) for said in ours
                if said in theirs and len(ours[said]) == 1 and len(theirs[said]) == 1]
        if len(both) < 20:
            return None                     # too little in common to say anything
        both.sort()
        middle = both[len(both) // 2]
        spread = both[-1] - both[0]
        near = sum(1 for d in both if abs(d - middle) < 1.0)
        return {"lines": len(both), "middle": round(middle, 2),
                "spread": round(spread, 1), "steady": near,
                "against": fits.get("name") or "",
                # a file that is merely late says the same number everywhere; one for
                # another edit wanders, because the breaks fall in other places
                "another_cut": spread > 5.0 and near < len(both) * 0.7}

    def already_subtitled(self, key, path, language):
        """Has this episode got subtitles already, from any source?

        A file beside the video counts however it is named, and so does a text track
        inside the video in the language being asked for. Either way there is nothing
        to fetch, and a download saved is a download left in the day's allowance.
        """
        import pd_localapi
        if pd_localapi.sidecars(path):
            return True
        con = local().lib.db()
        try:
            row = con.execute("SELECT streams FROM file WHERE episode_id=?",
                              (str(key),)).fetchone()
            tracks = json.loads((row["streams"] if row else None) or "[]")
        except Exception:
            tracks = []
        finally:
            con.close()
        wanted = (language or "en").lower()[:2]
        for t in tracks:
            if (t.get("codec") or "").lower() not in self.TEXT_CODECS:
                continue
            if (t.get("lang") or "").lower()[:2] == wanted:
                return True
        return False

    @staticmethod
    def _words(text):
        """Words worth comparing: no punctuation, no numbers, nothing tiny."""
        return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
                if len(w) > 2 and not w.isdigit()}

    def rank_subtitles(self, results, release, title, season, number, filename=""):
        """Best first - and "best" means most likely to be in time with the picture.

        The words rarely differ between variants; the timing does. A subtitle is cut to
        one particular release, so the one named after the same release as the video is
        the one whose timing fits, and that outweighs everything except a hash of the
        file itself. Then the episode's own identity, then the release this series has
        been watching with, and downloads only to break a tie.
        """
        tags = []
        if season is not None and number is not None:
            tags = ["s%02de%02d" % (season, number), "%dx%02d" % (season, number),
                    "%dx%d" % (season, number)]
        episode_words = self._words(title)
        release_words = self._words(release)
        # the video's own name, minus the words every episode of the show shares
        file_words = self._words(os.path.splitext(os.path.basename(filename))[0])
        file_words -= episode_words

        def score(r):
            name = r.get("name") or ""
            low = name.lower()
            words = self._words(name)
            points = 0
            # the hash is of the video itself: nothing else comes close as evidence
            if r.get("fromHash"):
                points += 6
            # cut for the same release as the file in hand, which is what timing is
            points += min(8, 2 * len(file_words & words))
            if tags and any(t in low for t in tags):
                points += 3
            points += 2 * len(episode_words & words)
            points += len(release_words & words)
            # Tiers, not a total. Sharing the release is the one thing that predicts
            # timing, and as points it could be outweighed by a subtitle that merely
            # agreed about the episode's title and carried its number - which every
            # candidate for the right episode does. So it is asked first, and only
            # what is left of the scoring settles the order inside each tier.
            return (1 if self.same_name(filename, name) else 0,   # this very release
                    1 if r.get("fromHash") else 0,     # the same bytes: nothing beats it
                    len(file_words & words),           # then how much of the release
                    points,
                    r.get("downloads", 0))

        return sorted(results, key=score, reverse=True)

    def auto_subtitle(self, key, after=""):
        """Fetch what this series has been having, if it has been having anything.

        Silent about most failures on purpose: this runs when an episode starts, and
        an episode that plays without subtitles is better than one that refuses to.
        """
        if self.settings_file().get("autoFetch") is False:
            return {"ok": False, "error": "Fetching subtitles ahead is turned off."}
        episode = str(key).startswith("e")
        con = local().lib.db()
        try:
            row = (con.execute("SELECT item_id FROM episode WHERE id=?",
                               (str(key),)).fetchone() if episode else None)
            # the same question for whatever is playing now, so a remembered choice is
            # only carried across when both belong to the same programme
            before = (con.execute("SELECT item_id FROM episode WHERE id=?",
                                  (str(after),)).fetchone()
                      if str(after).startswith("e") else None)
        except (TypeError, ValueError):
            row, before = None, None
        finally:
            con.close()
        if episode and not row:
            return {"ok": False, "error": "No such episode."}
        family = str(row["item_id"]) if row else str(key)
        stored = self.settings_file()
        want = (stored.get("subsFor", {}) or {}).get(family)
        if not want and after and before and str(before["item_id"]) == family:
            # fetching happens before the episode in hand has finished, so the series
            # has not been taught yet - but the episode being watched knows what it is
            # using, and that is the choice being carried forward. Only within the same
            # programme: in a shuffle the last episode says nothing about the next film.
            want = (stored.get("subsTried", {}) or {}).get(str(after))
        if not want:
            # nothing remembered for this one - which is most of what a shuffle plays.
            # The language this viewer reads is enough to go on.
            language = self.viewer_language()
            if not language:
                return {"ok": False, "error": "This series has no remembered choice."}
            want = {"language": language}

        src = local().file_for(key, 0)
        if src and self.already_subtitled(key, src["file"], want["language"]):
            # Nothing to fetch - but there may be several beside the film, and one of
            # them may be cut for this very release. That one is in time with the
            # picture and the others are somebody else's edit, so it leads the list
            # unless a person has already chosen for themselves.
            fits = self.best_beside(src["file"], want["language"])
            if fits and not self.picked_for(key):
                self.remember_pick(key, fits.get("name") or "")
                return {"ok": True, "already": True, "chose": fits.get("name") or ""}
            return {"ok": True, "already": True}

        found = self.find_subtitles(key, want["language"])
        results = found.get("results") or []
        if not results:
            return {"ok": False, "error": found.get("error") or "Nothing found."}
        # what this episode is called, which is half of what makes a candidate right
        con = local().lib.db()
        try:
            here = (con.execute("SELECT title, season, number FROM episode WHERE id=?",
                                (str(key),)).fetchone() if episode else None)
        except (TypeError, ValueError):
            here = None
        finally:
            con.close()
        ranked = self.rank_subtitles(results, want.get("release") or "",
                                     here["title"] if here else "",
                                     here["season"] if here else None,
                                     here["number"] if here else None,
                                     (src or {}).get("file", ""))
        # Being the right episode comes first, and find_subtitles has already worked
        # out which those are. Ranking by release alone fetched a subtitle cut to
        # exactly this encode - of the following episode - because the two databases
        # number this season differently.
        fitting = [r for r in ranked if r.get("episode")]
        if here and not fitting and any("episode" in r for r in ranked):
            return {"ok": False,
                    "error": "Nothing found for this episode - the numbering at "
                             "OpenSubtitles does not match this library."}
        pick = (fitting or ranked)[0]
        # remembered like any other fetch: this is the file that episode will be
        # watched with, and watching it through is what verifies it. Fetched ahead
        # rather than by hand is no reason for it never to earn the mark.
        got = self.fetch_subtitle(key, pick["id"], want["language"], True,
                                  pick.get("name", ""))
        if got.get("ok"):
            self.place_later(key, got.get("file", ""))
        return got

    def place_later(self, key, filename):
        """Measure a just-fetched subtitle against the film, in the background.

        Only when this viewer has asked for subtitles to be placed for them. It happens
        here, five minutes before the episode is reached, rather than as it starts: the
        first line is then already in step, and no screen shows anything happening.
        Failures are silent, as everything about fetching ahead is.
        """
        if not filename:
            return
        if not self.scans_for():
            return
        found = local().file_for(key, 0) or {}
        video = found.get("file")
        if not video:
            return
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        at = next((i for i, one in enumerate(beside)
                   if os.path.basename(one["file"]) == filename), None)
        if at is None:
            return
        # named the way a client names it, so what is written here is what the player
        # reads when the episode starts, rather than a second copy under another name
        sub = beside[at].get("name") or ""

        def work():
            try:
                self.sync_subtitle(key, 0, -(at + 1), "l" + str(key), sub, True)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def build_version():
        """What build this is, taken from the changelog it ships with.

        Not server_version: that name belongs to BaseHTTPRequestHandler, which puts it
        in the Server header - shadowing it with a method broke every reply the server
        made.
        """
        # What this program was built as, baked in when it was built. A file beside
        # the program is not the program: building an installer rewrites the
        # changelog there, and the server then claimed a version it was not running
        # until somebody restarted it - which is how a machine one build behind
        # reported itself up to date.
        if PACKAGED:
            try:
                import pd_built
                if pd_built.VERSION:
                    return str(pd_built.VERSION)
            except Exception:
                pass
        try:
            with open(os.path.join(CODE, "changes.json"), encoding="utf-8") as f:
                return str(json.loads(f.read())[0].get("version") or "0.0.0")
        except Exception:
            return "0.0.0"

    def owner_name(self):
        """What to call the owner in a list that names everybody else.

        Guests are named by their invitation, so "you" beside them read as a fourth
        kind of person rather than as the one reading the page.
        """
        return str(self.settings_file().get("ownerName") or "").strip() or "you"

    def watch_stats(self):
        """Hours, viewings and titles over three windows, and who did the watching.

        Counted from the watch log, which is written from what the players report, so
        it covers the browser, the phone and the television alike. Time is the length
        of each viewing - from its first report to its last - rather than the length
        of the film, because a film left running for ten minutes is ten minutes.
        """
        now = int(time.time())
        windows = (("week", now - 7 * 86400), ("month", now - 30 * 86400),
                   ("total", 0))
        names = {r["token"]: r["name"] for r in INVITES.load()}
        # not "mine": that name is taken further down for the rows in the window
        ownName = self.owner_name()
        con = local().lib.db()
        try:
            rows = con.execute(
                "SELECT who, key, title, started, updated, position, duration, casual "
                "FROM watchlog").fetchall()
        finally:
            con.close()
        out = {}
        for name, since in windows:
            mine = [r for r in rows if (r["started"] or 0) >= since]
            people, titles = {}, {}
            seconds = 0
            for r in mine:
                # A viewing lasts from its first report to its last - but a row grows
                # for as long as reports keep arriving, and a player left running
                # reports for hours. Nobody watches a twenty-minute episode for
                # thirty-five hours, which is what one of them claimed: so a viewing
                # counts for no more than the length of the thing being watched.
                took = max(0, int(r["updated"] or 0) - int(r["started"] or 0))
                length = int((r["duration"] or 0) / 1000) if (r["duration"] or 0) > 10000                     else int(r["duration"] or 0)
                if length > 0:
                    took = min(took, length)
                seconds += took
                # str, because this is a key: one invitation held a list where its
                # name should be, and the whole figures page answered 500 rather than
                # counting the hours of everybody else
                who = str(ownName if r["who"] == "me"
                          else names.get(r["who"], "a guest") or "a guest")
                people[who] = people.get(who, 0) + took
                if r["title"]:
                    titles[r["title"]] = titles.get(r["title"], 0) + took
            ranked = sorted(people.items(), key=lambda kv: -kv[1])
            out[name] = {
                "hours": round(seconds / 3600.0, 1),
                "viewings": len(mine),
                "titles": len({r["key"] for r in mine if r["key"]}),
                "casual": len([r for r in mine if r["casual"]]),
                "people": [{"who": w, "hours": round(sec / 3600.0, 1)}
                           for w, sec in ranked],
                # the three longest sittings, which is what "what have we been
                # watching" actually means
                "top": [{"title": t, "hours": round(sec / 3600.0, 1)}
                        for t, sec in sorted(titles.items(),
                                             key=lambda kv: -kv[1])[:5]],
            }
        return out

    def note_fault(self, trail):
        """File a fault the server itself hit, once.

        A broken page asks again every few seconds, and a hundred identical rows on
        the noticeboard hide everything else - so the same fault is filed once an
        hour and counted in the log the rest of the time.
        """
        lines = [ln.strip() for ln in (trail or "").strip().splitlines() if ln.strip()]
        if not lines:
            return
        # the last line names the fault; the line above it is where in the program
        signature = " | ".join(lines[-2:])[:200]
        now = time.time()
        if now - FAULTS_SEEN.get(signature, 0) < 3600:
            return
        FAULTS_SEEN[signature] = now
        head = lines[-1]
        where = "%s %s" % (self.command, self.path.split("?")[0])
        try:
            self.file_report("error", head + chr(10) + where + chr(10)
                             + chr(10).join(lines[-6:]), "server")
        except Exception:
            pass                       # a fault while filing a fault is not worth one

    def file_report(self, kind, text, app=""):
        """Put one report on the noticeboard, as though somebody had written it.

        Used for the faults the machinery notices - a crash posted by the app, a fault
        the page caught - which is why the person is whoever the request came from
        rather than whoever is reading.
        """
        row = {
            "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFFFFF),
            "when": int(time.time()),
            "who": self.guest_name if self.role == "guest" else "you",
            "kind": kind,
            "text": (text or "").strip()[:2000],
            "from": self.client_address[0],
            "app": app,
            "source": "auto" if kind in ("error", "crash") else "person",
        }
        if not row["text"]:
            return
        with open(os.path.join(ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + chr(10))

    def reports(self):
        """What people have written in, newest first.

        Everyone using the server can read this, so that two people do not report the
        same fault twice - everyone except for the rows the owner has hidden, which are
        not sent at all rather than merely marked.
        """
        path = os.path.join(ROOT, "feedback.jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        out = list(reversed(out))[:200]
        # what is still wrong comes first: a sorted report is worth reading - somebody
        # hitting the same fault should see it is known - but it is not news
        out.sort(key=lambda r: 1 if r.get("done") else 0)
        if self.role == "owner":
            return out
        # a guest sees the words, not the addresses they came from
        return [{k: v for k, v in r.items() if k != "from"}
                for r in out if not r.get("hidden")]

    #: wrong setup codes lately, by address. Five characters is enough only because
    #: they cannot be tried in bulk.
    GUESSES = {}

    def too_many_guesses(self):
        now = time.time()
        tries = [t for t in Handler.GUESSES.get(self.client_address[0], [])
                 if now - t < 3600]
        Handler.GUESSES[self.client_address[0]] = tries
        return len(tries) >= 20

    def wrong_guess(self):
        Handler.GUESSES.setdefault(self.client_address[0], []).append(time.time())

    def app_version(self):
        """What the app beside this server is, or empty if there is no app.

        Two places, because there are two ways it got here: the installer puts it
        beside the program, and a server that fetched one for itself keeps it with
        its own things.
        """
        for where in (STATIC, ROOT):
            try:
                with open(os.path.join(where, "version.json"), encoding="utf-8") as f:
                    return str(json.loads(f.read()).get("versionName") or "")
            except Exception:
                continue
        return ""

    #: Where a server with no app beside it can fetch one, and what it is told is
    #: current. Read, never written to: nothing about this machine goes with the
    #: request.
    APP_FROM = "https://get.palladium.video/app"
    APP_SAYS = "https://palladium.video/version.json"

    #: One fetch at a time, and not again for an hour if it failed
    APP_FETCH = {"busy": False, "tried": 0.0, "where": "", "got": 0, "size": 0,
                 "part": 0.0}

    @staticmethod
    def app_beside_us():
        """The app file this server hands out: the one it was installed with, or the
        one it fetched for itself."""
        near = os.path.join(STATIC, "palladium.apk")
        if os.path.exists(near):
            return near
        mine = os.path.join(ROOT, "palladium.apk")
        return mine if os.path.exists(mine) else ""

    @classmethod
    def fetch_app(cls, force=False):
        """Take a copy of the app, once, in the background.

        A container is built from source and source does not include an APK, so a
        server can be running perfectly well with no app to hand anybody. It is a
        download, not a decision: the same file the site offers, written where this
        server keeps its own things.
        """
        if cls.APP_FETCH["busy"]:
            return
        if not force and time.time() - cls.APP_FETCH["tried"] < 3600:
            return
        try:
            if local().lib.config().get("fetchFromSite") is False:
                return                    # this house asks the site for nothing
        except Exception:
            pass
        if cls.app_beside_us():
            return

        def work():
            cls.APP_FETCH.update(busy=True, tried=time.time())
            try:
                req = urllib.request.Request(cls.APP_FROM,
                                             headers={"User-Agent": "palladium"})
                cls.APP_FETCH.update(where=cls.APP_FROM, got=0, size=0, part=0.0)
                with urllib.request.urlopen(req, timeout=180) as r:
                    size = int(r.headers.get("Content-Length") or 0)
                    cls.APP_FETCH["size"] = size
                    parts, got = [], 0
                    while True:
                        lump = r.read(262144)
                        if not lump:
                            break
                        parts.append(lump)
                        got += len(lump)
                        cls.APP_FETCH["got"] = got
                        cls.APP_FETCH["part"] = round(got / size, 3) if size else 0.0
                    body = b"".join(parts)
                if len(body) > 1_000_000 and body[:2] == b"PK":
                    part = os.path.join(ROOT, "palladium.apk.part")
                    with open(part, "wb") as f:
                        f.write(body)
                    os.replace(part, os.path.join(ROOT, "palladium.apk"))
                    # and what it is, so the page and the file never disagree
                    try:
                        said = urllib.request.Request(
                            cls.APP_SAYS, headers={"User-Agent": "palladium"})
                        with urllib.request.urlopen(said, timeout=30) as r:
                            facts = r.read(4000)
                        json.loads(facts)          # refuse anything that is not it
                        with open(os.path.join(ROOT, "version.json"), "wb") as f:
                            f.write(facts)
                    except Exception:
                        pass
            except Exception:
                pass                  # no app to hand out is a state, not a fault
            finally:
                cls.APP_FETCH["busy"] = False

        threading.Thread(target=work, daemon=True).start()

    def send_app(self, head_only=False, who=""):
        """Hand over the app itself.

        Named for its version, because Downloader keeps what it fetches under a name
        taken from the address: ask twice for palladium.apk and the second answer may
        never be looked at, the cache already on the television being installed instead.
        That is what being stuck on an old version looks like.
        """
        # let go of in Settings means not handed out, whatever is on the disk
        apk = "" if (read_settings() or {}).get("appOff") else self.app_beside_us()
        if not apk:
            # A server built from source has no app beside it. It can fetch one, but
            # not because a stranger asked for a file: whoever owns this machine says
            # so, in Settings, and until then there is honestly nothing to hand over.
            self.send_error(404, "no app on this server yet - its owner can fetch one "
                                 "from Settings")
            return
        size = os.path.getsize(apk)
        version = self.app_version()
        name = ("palladium-%s.apk" % version) if version else "palladium.apk"
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename=" + name)
        self.send_header("X-Palladium-Version", version)
        # never a stored copy: the whole point of asking again is to get the new one
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if head_only:
            return
        sent = 0
        with open(apk, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except Exception:
                    break                       # gave up part way
                sent += len(chunk)
        # a successful download used to leave no trace, so "has he got it?" could not
        # be answered by anybody
        with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s app %s -> %s%s (%.1f of %.1f MB)%s"
                    % (time.strftime("%H:%M:%S"), version or "?",
                       self.client_address[0], (" " + who) if who else "",
                       sent / 1e6, size / 1e6, chr(10)))

    def server_id(self):
        """This machine's name for itself: generated once, then kept.

        Two addresses reaching the same server give the same answer, which is the only
        way a client holding both can tell they are one server rather than two.
        """
        path = os.path.join(ROOT, "config.json")
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.loads(f.read())
        except Exception:
            cfg = {}
        ident = cfg.get("serverId") or ""
        if not ident:
            import uuid
            ident = uuid.uuid4().hex[:16]
            cfg["serverId"] = ident
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, path)
        return ident

    def filed(self):
        """What has been cleared off the board, newest first.

        Everyone sees it. The point of writing down what caused a fault and what fixed
        it is that the next person to hit the same thing reads it instead of reporting
        it again - and that person is usually not the owner. What a guest does not get
        is the addresses the rows came from, and anything the owner has hidden.
        """
        path = os.path.join(ROOT, "feedback-archive.jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                row["archived"] = True
                out.append(row)
        out = list(reversed(out))[:200]
        if self.role == "owner":
            return out
        return [{k: v for k, v in r.items() if k != "from"}
                for r in out if not r.get("hidden")]

    def unfile(self, ident):
        """Put one archived row back on the board - a tick pressed by mistake."""
        path = os.path.join(ROOT, "feedback-archive.jsonl")
        if not os.path.exists(path) or not ident:
            return None
        kept, back = [], None
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("id") == ident and back is None:
                    back = row
                else:
                    kept.append(row)
        if back is None:
            return None
        back.pop("filed", None)
        back["done"] = False              # back on the board means back to answer
        with open(path, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row) + chr(10))
        with open(os.path.join(ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(back) + chr(10))
        return back

    def file_away(self, wanted):
        """Move the rows `wanted` picks out of the board and into the archive.

        Nothing written on the noticeboard is ever destroyed: a sorted report carries
        what caused the fault and what was done about it, which outlives any interest
        in the fault itself. feedback-archive.jsonl is the same format, so a row can be
        read back or pasted in again by hand.
        """
        path = os.path.join(ROOT, "feedback.jsonl")
        if not os.path.exists(path):
            return 0
        kept, gone = [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                (gone if wanted(row) else kept).append(row)
        if not gone:
            return 0
        stamp = int(time.time())
        with open(os.path.join(ROOT, "feedback-archive.jsonl"), "a",
                  encoding="utf-8") as f:
            for row in gone:
                row["filed"] = stamp
                f.write(json.dumps(row) + chr(10))
        with open(path, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row) + chr(10))
        return len(gone)

    def amend_report(self, ident, changes):
        """Change one row and write its file back out.

        The board and the archive are both a line per report, rewritten whole, so
        hiding a row, ticking it off and writing down what fixed it are all the same
        operation - and the archive has to be included, because the row somebody wants
        to explain is often one that has already been filed away.
        """
        if not ident or not changes:
            return
        for name in ("feedback.jsonl", "feedback-archive.jsonl"):
            path = os.path.join(ROOT, name)
            if not os.path.exists(path):
                continue
            rows, hit = [], False
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("id") == ident:
                        row.update(changes)
                        hit = True
                    rows.append(row)
            if not hit:
                continue
            with open(path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row) + chr(10))
            return

    def hide_report(self, ident, hidden):
        """Take one row out of everyone else's sight, or put it back."""
        self.amend_report(ident, {"hidden": bool(hidden)})

    def watched_at(self, token):
        """When this invitation last watched something, from the watch log.

        The hit counter has been unreliable - it was lost when the process was killed,
        and overwritten when another invitation was created - so an invite can read
        "never used" while the log has an hour of a film against it.
        """
        try:
            con = local().lib.db()
            try:
                row = con.execute("SELECT MAX(updated) w FROM watchlog WHERE who=?",
                                  (token,)).fetchone()
            finally:
                con.close()
            return int(row["w"] or 0) if row else 0
        except Exception:
            return 0

    def with_link(self, row):
        """An invite as the settings page wants it: the row plus the link to send."""
        host = wan_ip() or LAN_IP
        out = dict(row)
        out["link"] = "http://%s:%d/s/%s" % (host, PORT, row["token"])
        out["lanLink"] = "http://%s:%d/s/%s" % (LAN_IP, PORT, row["token"])
        # And the same key on the machine that keeps copies, for when this one is
        # off. A guest cannot be expected to know there is a second address, and a
        # browser cannot find one for itself: the page it would ask comes from here.
        other = (self.standby_now() or {}).get("outside") or ""
        if other:
            out["copyLink"] = "%s/s/%s" % (other.rstrip("/"), row["token"])
        # the five characters that stand for this invitation: typed into a television,
        # read out over the telephone, and revoked with the invitation itself
        out["code"] = Invites.code_for(row["token"])
        # what the watch log knows, which outlives a counter that has been lost twice
        watched = self.watched_at(row["token"])
        if watched:
            out["watchedAt"] = watched
            if watched > int(out.get("lastSeen") or 0):
                out["lastSeen"] = watched
        return out

    def wants_a_page(self, path):
        """True when a browser is asking for something to look at."""
        if path not in ("/", "/index.html"):
            return False
        return "text/html" in (self.headers.get("Accept") or "")

    def locked_page(self):
        """What a stranger sees: what this is, and how to get in."""
        body = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Palladium</title>"
                "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                "text-align:center;padding:12vh 20px;margin:0'>"
                "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                "margin:10px 0 6px'>PALLADIUM</h1>"
                "<p style='color:#93a0b0;margin:0 0 26px'>This library is private. "
                "Open the invitation link you were sent, and this browser will "
                "remember you.</p>"
                "<form method='get' action='/s/' id='f' "
                "style='display:flex;gap:8px;max-width:460px;margin:0 auto'>"
                "<input id='t' placeholder='paste the link, or the 5-letter code' "
                "style='flex:1;background:#161b22;border:1px solid #232a33;"
                "color:#e8ecf1;border-radius:9px;padding:12px 14px;font:inherit'>"
                "<button style='background:#4a90f0;color:#111;font-weight:600;"
                "border:0;border-radius:9px;padding:12px 20px;font:inherit;"
                "cursor:pointer'>Go</button></form>"
                "<p style='color:#6b7683;font-size:13px;margin-top:22px'>"
                "Lost the link? Ask whoever invited you to send it again.</p>"
                + (
                    # the same door, for the person who owns the place
                    "<form id='p' style='display:flex;gap:8px;max-width:460px;"
                    "margin:26px auto 0'>"
                    "<input id='pw' type='password' placeholder='owner password' "
                    "style='flex:1;background:#161b22;border:1px solid #232a33;"
                    "color:#e8ecf1;border-radius:9px;padding:12px 14px;font:inherit'>"
                    "<button style='background:#232a33;color:#e8ecf1;border:0;"
                    "border-radius:9px;padding:12px 20px;font:inherit;cursor:pointer'>"
                    "Sign in</button></form>"
                    "<p id='pe' style='color:#f0704f;font-size:13px;min-height:18px'>"
                    "</p>"
                    if self.password_set() else "")
                + "<script>var pf=document.getElementById('p');if(pf)pf.onsubmit="
                "function(e){e.preventDefault();"
                "fetch('/login',{method:'POST',headers:{'Content-Type':"
                "'application/json'},body:JSON.stringify({password:"
                "document.getElementById('pw').value})}).then(function(r){"
                "return r.json();}).then(function(d){"
                "if(d.ok){location.href='/';}else{"
                "document.getElementById('pe').textContent=d.error||'no';}});};"
                "document.getElementById('f').onsubmit=function(e){"
                "e.preventDefault();var v=document.getElementById('t').value.trim();"
                "var m=v.match(/\\/s\\/([A-Za-z0-9_-]{8,})/);"
                "if(m){location.href='/s/'+m[1];return;}"
                "if(/^[A-Za-z0-9]{5}$/.test(v)){location.href='/i/'+v+'/open';return;}"
                "if(/^[A-Za-z0-9_-]{8,}$/.test(v)){location.href='/s/'+v;return;}"
                "alert('That does not look like an invitation link or a code.');};"
                "</script></body>").replace("#4a90f0", self.accent_now()).encode()
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def landing(self, token):
        """What a guest sees when they open the link they were sent.

        The token moves out of the address and into a cookie here, so the rest of the
        session is ordinary browsing: posters, subtitle tracks and video segments all
        carry it without every URL having to.
        """
        invite = INVITES.check(token)
        if not invite:
            body = ("<title>Palladium</title><body style='background:#0b0d10;"
                    "color:#e8ecf1;font:16px system-ui;text-align:center;padding:14vh'>"
                    "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                    "<h1 style='font-weight:300;letter-spacing:3px;font-size:20px'>"
                    "PALLADIUM</h1><p style='color:#93a0b0'>This invitation is no longer "
                    "valid. Ask for a new link.</p></body>").encode()
            self.send_response(410)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        app = {"versionName": "?"}
        try:
            with open(os.path.join(STATIC, "version.json"), encoding="utf-8") as f:
                app.update(json.loads(f.read()))
        except Exception:
            pass
        # what a chat app puts in its preview card: without these it invents
        # something from the address, which reads as junk
        card = ("<meta property='og:title' content='Palladium'>"
                "<meta property='og:site_name' content='Palladium'>"
                "<meta property='og:type' content='website'>"
                "<meta property='og:description' content='%s, this link is your key "
                "to a private film library. Watch in a browser, or install the app "
                "for a phone or a Google TV.'>"
                "<meta property='og:image' content='http://%s:%d/invite.png'>"
                "<meta name='description' content='A private film library.'>"
                % (invite["name"], wan_ip() or LAN_IP, PORT))
        html = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Palladium</title>" + card +
                "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                "text-align:center;padding:9vh 20px;margin:0'>"
                "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                "margin:10px 0 2px'>PALLADIUM</h1>"
                "<p style='color:#93a0b0;margin:0 0 30px'>Welcome, %(name)s.</p>"
                "<a href='/' style='display:inline-block;background:#4a90f0;color:#111;"
                "font-weight:600;padding:15px 34px;border-radius:9px;text-decoration:none;"
                "font-size:17px'>Watch in the browser</a>"
                "<div style='margin:22px 0 0'><a href='/app?t=%(token)s' "
                "style='color:#4a90f0;font-size:15px'>Install the Android app "
                "(%(versionName)s)</a></div>"
                "<p style='color:#6b7683;font-size:13px;margin-top:34px;line-height:1.7;"
                "max-width:430px;margin-left:auto;margin-right:auto'>"
                "Phones, tablets and Google TV can install the app. If it asks for a "
                "server, paste this whole link into it.</p>"
                # A television has no browser, so none of the above can be pressed
                # there. Downloader is how anything gets onto a Google TV, and it wants
                # an address typed with a remote - hence the short code.
                # An iPhone cannot install an app from a link - Apple does not
                # allow it - but it can keep this page on the home screen, where it
                # opens full screen with its own icon and no browser around it.
                "<div style='color:#6b7683;font-size:13px;margin:26px auto 0;"
                "max-width:430px;line-height:1.8;border-top:1px solid #1e242c;"
                "padding-top:22px;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On an iPhone or iPad</b><br>"
                "Open this page in Safari, press <b style='color:#c3ccd6'>Share</b>, "
                "then <b style='color:#c3ccd6'>Add to Home Screen</b>. It gets its own "
                "icon and opens full screen, already signed in as you."
                "</div>"
                "<div style='color:#6b7683;font-size:13px;margin:18px auto 0;"
                "max-width:430px;line-height:1.8;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On a Google TV</b><br>"
                "1. Install <b style='color:#c3ccd6'>Downloader</b> from the Play "
                "Store.<br>"
                "2. Settings &rsaquo; Apps &rsaquo; Security &amp; restrictions "
                "&rsaquo; Unknown sources: turn <b style='color:#c3ccd6'>Downloader"
                "</b> on.<br>"
                "3. Open Downloader and type this in the address box, exactly "
                "as it reads - no http, nothing on the end:<br>"
                "<span style='display:inline-block;margin:8px 0 4px;font:600 18px "
                "ui-monospace,monospace;color:#4a90f0;letter-spacing:1px'>"
                "%(host)s/i/%(code)s</span><br>"
                "4. When the app asks for a server, give it "
                "<span style='color:#c3ccd6'>%(host)s</span> and the code "
                "<span style='color:#c3ccd6'>%(code)s</span>."
                "</div>"
                # A Samsung set has a browser of its own, and the client is a web
                # page: that is the whole of it for a guest. An installed app needs
                # a certificate made on their own machine, which is a job rather than
                # a step, so it is offered second and plainly.
                "<div style='color:#6b7683;font-size:13px;margin:18px auto 0;"
                "max-width:430px;line-height:1.8;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On a Samsung TV</b><br>"
                "Open the television's own web browser and go to<br>"
                "<span style='display:inline-block;margin:8px 0 4px;font:600 18px "
                "ui-monospace,monospace;color:#4a90f0;letter-spacing:1px'>"
                "%(host)s/i/%(code)s/open</span><br>"
                "then press <b style='color:#c3ccd6'>Watch in the browser</b>. That is "
                "the whole library, in the set's own browser, signed in as you - add it "
                "to the favourites and it is one press from then on."
                "<br><br>"
                "For an icon on the home row instead, the app has to be built and "
                "signed on a computer of your own - Samsung installs nothing signed "
                "by anybody else. <a href='/tizen.zip?t=%(token)s' download "
                "style='color:#4a90f0'>The source is here</a>, with this address "
                "already written into it; Tizen Studio and a free Samsung account do "
                "the rest. It is an afternoon, not a step."
                "</div>"
                "</body>") % {"name": invite["name"], "token": token,
                              "versionName": app["versionName"],
                              "host": self.headers.get("Host") or (
                                  "%s:%d" % (wan_ip() or LAN_IP, PORT)),
                              "code": Invites.code_for(token)}
        body = html.replace("#4a90f0", self.accent_now()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # the invite is the session: a year, renewed on every visit, and only back
        # to this server
        self.send_header("Set-Cookie", self.cookie_for(token))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def side_of(host):
        """"home" for an address on this network, "away" for one out in the world.

        The two do not share a pipe: a film going out to somebody's phone is on the
        line to the world, and a copy to a machine in the next room is on the network
        here. Counting them together held a copy between two machines three metres
        apart to what was left of an upload.
        """
        host = str(host or "")
        if host in ("127.0.0.1", "::1"):
            return "home"
        parts = host.split(".")
        if len(parts) == 4 and parts[0].isdigit():
            a, b = int(parts[0]), int(parts[1])
            # 169 alone was the whole of 169.0.0.0/8, which is mostly ordinary
            # public address space - only 169.254 is link-local. Any visitor whose
            # address began 169 was therefore taken for someone in the house and
            # given the owner's rights, from anywhere.
            if (a == 10 or (a == 192 and b == 168)
                    or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)):
                return "home"
        return "away"

    def at_home(self):
        """True for a caller on this machine or this home network."""
        return self.side_of(self.client_address[0]) == "home"

    def remote_admin_ok(self):
        """Whether the run of the place travels beyond this network.

        Off, an owner's key is a guest's key from away: films yes, settings no. The
        machine itself and this network are never affected - shutting somebody out of
        their own server from the sofa is not a security setting, it is a fault. On
        until somebody says otherwise, which is how it has always behaved.
        """
        return self.settings_file().get("remoteAdmin") is not False

    # ---- the owner's own password -------------------------------------------
    #
    # Optional, and off until somebody sets one: a server on a home network with one
    # person on it needs no password, and demanding one would be theatre. Set one and
    # it is required from everywhere except the machine itself.

    @staticmethod
    def hash_password(word, salt=None):
        """A password, as something that cannot be turned back into the password."""
        import binascii
        import hashlib
        salt = salt or binascii.hexlify(os.urandom(16)).decode()
        made = hashlib.pbkdf2_hmac("sha256", word.encode("utf-8"),
                                   salt.encode("utf-8"), 200000)
        return salt, binascii.hexlify(made).decode()

    @staticmethod
    def password_set():
        return bool((read_settings() or {}).get("ownerHash"))

    @staticmethod
    def untouched():
        """A server nobody has set up yet: no password, no invitations, no folders.

        There is nothing here to protect at that point - no library, no settings, no
        keys - and whoever reaches it is the person who has just installed it.
        """
        try:
            stored = read_settings() or {}
            if stored.get("setupDone") or stored.get("ownerHash"):
                return False
            if INVITES.load():
                return False
            lib = local().lib.config() or {}
            return not (lib.get("movies") or lib.get("tv") or lib.get("mixed"))
        except Exception:
            return False

    def password_ok(self, word):
        import hmac
        stored = read_settings() or {}
        salt, want = stored.get("ownerSalt") or "", stored.get("ownerHash") or ""
        if not want:
            return False
        _, got = self.hash_password(word or "", salt)
        return hmac.compare_digest(got, want)

    def set_password(self, word):
        """Set it, or clear it with an empty one. Every session ends either way."""
        stored = read_settings() or {}
        if not (word or "").strip():
            # emptied rather than removed: settings are written by merging keys, so a
            # key that is missing from what is written keeps whatever was there before
            # - which meant a password could be set and never taken off again
            stored["ownerHash"], stored["ownerSalt"] = "", ""
        else:
            salt, made = self.hash_password(word.strip())
            stored["ownerSalt"], stored["ownerHash"] = salt, made
        stored["ownerSessions"] = {}
        write_settings(stored)
        return self.password_set()

    def open_session(self):
        """A token that says this caller has given the password."""
        import binascii
        token = binascii.hexlify(os.urandom(24)).decode()
        stored = read_settings() or {}
        live = {t: when for t, when in (stored.get("ownerSessions") or {}).items()
                if when > time.time()}
        live[token] = time.time() + 180 * 24 * 3600
        stored["ownerSessions"] = live
        write_settings(stored)
        return token

    def session_ok(self):
        token = self.bearer()
        if not token:
            return False
        when = (read_settings() or {}).get("ownerSessions", {}).get(token)
        return bool(when and when > time.time())

    def through_a_proxy(self):
        """Did this request come by way of something else?

        A proxy makes every caller look local. Two headers say so, and either one is
        enough to stop the address being taken as proof of anything.
        """
        return bool(self.headers.get("X-Forwarded-For")
                    or self.headers.get("Forwarded")
                    or self.headers.get("X-Real-IP"))

    @staticmethod
    def cookie_for(token):
        return "pal=%s; Path=/; Max-Age=%d; SameSite=Lax" % (token, COOKIE_LIFE)

    def device_kind(self):
        """What kind of thing is watching: an app on a television, a browser, a phone.

        The client says its own name - "Streamer", "SM-S931B" - which says nothing
        about what it is. This is worked out from what it says it is and how it asks.
        """
        said = (self.headers.get("X-Palladium-App") or "").lower()
        if said.startswith("android"):
            return "Google TV app" if said.endswith(" tv") else "Android app"
        agent = (self.headers.get("User-Agent") or "")
        low = agent.lower()
        if "crkey" in low:
            return "Chromecast"
        if "web0s" in low or "webos" in low:
            return "LG TV browser"
        if "tizen" in low or "smart-tv" in low or "smarttv" in low:
            return "Samsung TV browser"
        if "iphone" in low:
            return "iPhone"
        if "ipad" in low:
            return "iPad"
        if "android" in low:
            return "Android browser"
        if "macintosh" in low or "mac os" in low:
            return "Mac browser"
        if "windows" in low:
            return "Windows browser"
        if "mozilla" in low:
            return "browser"
        return ""

    def app_name(self):
        """What the client calls itself: "android 0.11.4 tv", or the browser.

        The app says so in a header. A browser says nothing, and does not need to -
        it is whatever the page it loaded is, which the server knows already.
        """
        said = (self.headers.get("X-Palladium-App") or "").strip()[:60]
        if said:
            return said
        agent = (self.headers.get("User-Agent") or "").lower()
        if "mozilla" in agent:
            return "web"
        return ""

    def bearer(self):
        """An invite token from the URL, a header, or the cookie the landing page set.

        Three places because three kinds of caller: the Android app sends a header,
        a shared link carries the token in the query, and once a browser has been to
        /s/<token> every later request for a poster or a video segment carries only
        the cookie.
        """
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        if q.get("t"):
            return q["t"][0]
        head = self.headers.get("X-Palladium-Token")
        if head:
            return head
        for bit in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = bit.strip().partition("=")
            if k == "pal":
                return v
        return ""

    #: What a *receiver* calls itself. CrKey is the Cast receiver on every Chromecast
    #: and Google TV; the rest are televisions casting natively.
    RECEIVERS = ("crkey", "cast_sender", "appletv", "airplay", "smarttv", "tizen",
                 "web0s", "aftv")

    #: And what our own app calls itself. A Google TV Streamer's build string contains
    #: "Chromecast", so the app running on one looked exactly like a receiver - every
    #: ordinary play from that device was logged as a cast asking for the pipe, which
    #: sent me looking for a fault that was not there.
    OURSELVES = ("dalvik", "okhttp", "palladium")

    def receiver(self):
        """The name a television gives itself, or empty for anything else."""
        ua = (self.headers.get("User-Agent") or "").lower()
        if any(mark in ua for mark in self.OURSELVES):
            return ""
        for mark in self.RECEIVERS:
            if mark in ua:
                return (self.headers.get("User-Agent") or "")[:70]
        return ""

    def note_cast(self, what, extra=""):
        """Write down a television asking for something, with what it asked for.

        Only televisions: a browser fetching the same playlist is ordinary traffic and
        would drown this. What is wanted here is the answer to "did the cast even
        reach us", which was previously unanswerable.
        """
        who = self.receiver()
        if not who:
            return
        with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s cast %s from %s%s [%s]%s"
                    % (time.strftime("%H:%M:%S"), what, self.client_address[0],
                       (" " + extra) if extra else "", who, chr(10)))

    def refused(self, path, why):
        """Note a refusal: what was asked for, by whom, and what they carried."""
        token = self.bearer()
        known = INVITES.check(token) if token else None
        who = self.receiver()
        with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s refused %s to %s (%s)%s%s"
                    % (time.strftime("%H:%M:%S"), path, self.client_address[0],
                       ("no token" if not token else
                        ("unknown token " + token[:8]) if not known else
                        known["name"] + ": " + why),
                       # which matters most for a television: it cannot show an error,
                       # it just sits there
                       (" [%s]" % who) if who else "",
                       chr(10)))

    #: What a key is for. "user" watches; "owner" may change the library as though
    #: sitting at the machine; "cache" is another server keeping copies of this one.
    #: Kept on the key itself, so one list of keys answers "who has a way in here,
    #: and what may they do".
    ROLES = ("user", "owner", "admin", "cache")

    @staticmethod
    def role_of(row):
        """The role on a key, or the one its old flags imply."""
        role = str((row or {}).get("role") or "")
        if role in Handler.ROLES:
            return role
        if (row or {}).get("follows"):
            return "cache"
        return "user"

    def who(self, path):
        """'owner', 'guest' or None - the answer every request starts with."""
        if self.headers.get("X-Palladium-House"):
            # the server this machine follows, setting how it copies: only while
            # somebody here allows it, from that server's address, and its copying only
            return ("owner" if self.managed_from_the_house()
                    and str(path).startswith("/follow") else None)
        # The machine itself needs no key: somebody sitting at it can read the files
        # anyway. Every other caller carries one - being on the home network is not a
        # credential, and a television in the kitchen is as much a stranger as a phone
        # on the train until it holds an invitation.
        here = (self.client_address[0] in ("127.0.0.1", "::1")
                and not self.through_a_proxy())
        # In a container there is no sitting at the machine: every request arrives off
        # the network, including the first one. So a server in one could not be set up
        # at all - no password to give, no invitation to hold, and the only address
        # that counted was inside the container. Until somebody has set it up, being
        # on the same network is enough; finishing the wizard is what closes it.
        if not here and self.untouched() and self.in_the_house():
            here = True
        if self.password_set():
            # A session is the password given once and remembered, so it opens the
            # place from wherever it was given - which is the thing the setting is
            # about. Sitting at the machine is not affected by it.
            if here or (self.session_ok() and (self.at_home()
                                               or self.remote_admin_ok())):
                return "owner"
        elif here:
            return "owner"
        if self.managed_from_the_house():
            # This machine keeps copies for another and has been told that machine
            # may change its settings. Only that machine, only by its address, and
            # only because somebody sitting here turned it on.
            return "owner"
        invite = INVITES.check(self.bearer(), self.app_name())
        if invite and Handler.role_of(invite) == "owner":
            # a key that carries the run of the place, for somebody who has it and is
            # not sitting at the machine
            self.guest_name = invite["name"]
            if self.at_home() or self.remote_admin_ok():
                return "owner"
            # From away with the setting off: the same key, read as a guest's. It
            # falls through to the guest handling below rather than being refused,
            # so what it is mostly used for - watching - goes on working.
            self.guest_name = invite["name"]
        # A machine that keeps copies is not a guest and never was: it was owner here
        # only because it sat on the same network, and requiring a key of every screen
        # took that away - so its own rounds were refused at the door, and caching
        # stopped. The handlers behind /follow check the key themselves, that it
        # follows this server and that it has not been stopped; the door defers to
        # them rather than deciding it twice.
        # and the build this machine is running: a machine that follows this one
        # replaces itself with what this one has, which means fetching the installer.
        # Being on the same network stopped being a credential, and its key was good
        # for /follow alone - so following the build broke at the door.
        low = str(path).lower()
        build_file = low.startswith("/palladium-setup") and low.endswith(".exe")
        # and who is watching, which is the one thing the pair cannot work out
        # separately: a film read off both machines is two streams, and each machine
        # can see only its own. Neither draws the pair honestly without asking the
        # other, and this is a list of what is going out of a house to itself.
        # /server/build as well as /server: the first is what a follower asks for
        # the version and hash, and it was refused at the door while the handler
        # behind it was waiting to check the same key itself - so a machine that
        # follows this one could never take its build, fell back to the site, and
        # refused to go backwards to what the site was still announcing.
        if (invite and Handler.role_of(invite) == "cache"
                and (str(path).startswith("/follow")
                     or path == "/server" or path == "/server/build"
                     or path == "/watching" or build_file)):
            self.guest_name = invite["name"]
            return "owner"
        if invite and Handler.role_of(invite) == "admin":
            # A panel on the wall rather than a person: it reads whatever the owner
            # reads - who is watching, who holds a key, what is arriving - and writes
            # nothing. Asking is a GET; everything that changes anything is refused,
            # because only the owner's own key answers "owner" to those.
            self.guest_name = invite["name"]
            if self.command in ("GET", "HEAD"):
                return "owner"
            return "guest" if Invites.allowed(path) else None
        if invite and Invites.allowed(path):
            self.guest_name = invite["name"]
            return "guest"
        # the app and its install page: no invitation needed to hold a client that
        # cannot see anything until it is given one
        if Invites.public(path):
            return "guest"
        return None

    def send_file_ranged(self, path, sid=None):
        """Serve a media file with byte ranges, so the browser can seek in it."""
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error(404)
            return
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        ctype = {"mkv": "video/x-matroska", "mp4": "video/mp4", "m4v": "video/mp4",
                 "webm": "video/webm", "avi": "video/x-msvideo"}.get(ext, "video/mp4")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            if a:
                start = int(a)
            if b:
                end = min(int(b), size - 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        remaining = end - start + 1
        asked = remaining
        trouble = None
        # A share of the blocks read off the other machine instead of this disk, if
        # somebody has asked for that and the other machine holds the same file. The
        # viewer sees one unbroken answer either way: this is where the block came
        # from, not what the answer looks like.
        share = self.sharing_with_copy(path)
        # How much of the reading the cache is doing at this moment. It starts at
        # nothing: this machine is used for the whole of an evening while it can keep
        # up. What says it cannot is its own reading falling behind what the viewer
        # is taking - the disk is busy, something else is being written to it - and
        # that is the same thing the viewer sees as a buffer going down, seen from
        # the side that can do something about it.
        now, at, taken, slow = 0, start, 0, 0
        # time spent getting blocks off this disk, and time spent handing them on,
        # since the share was last worked out
        disk, wrote = 0.0, 0.0
        # Whether this is a copy going to another machine rather than a film going to
        # somebody. A copy is never in a hurry: it is for an evening that has not
        # happened yet, and it was taking its share of the disk, the line and the
        # router from a film somebody was watching at the time.
        copying_out = bool(sid is not None
                           and (WATCHING.live.get(sid) or {}).get("how") == "syncing")
        held_back = 0.0
        # One file at a time to any one machine. Whoever asked last is the one still
        # listening; anything older is being sent to nobody.
        whom = self.client_address[0]
        if copying_out:
            Handler.SYNC_NOW[whom] = sid
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                want = min(262144, remaining)
                chunk = None
                # evenly spread rather than the first so-many of every hundred:
                # one block in five for a fifth, not twenty then eighty
                if now and (taken * now) % 100 < now:
                    # its turn: ask the cache, and fall to this disk the moment it is
                    # slow or unwilling. A block late is worse than a block read here.
                    chunk = self.block_from_copy(share, at, want)
                    if chunk:
                        f.seek(at + len(chunk))
                        Handler.CARRIED[self.watcher()] = (
                            lambda c: (c[0], c[1] + len(chunk), time.time()))(
                                Handler.CARRIED.get(self.watcher()) or (0, 0, 0))
                if chunk is None:
                    try:
                        began = time.monotonic()
                        chunk = f.read(want)
                        disk += time.monotonic() - began
                        if chunk:
                            Handler.CARRIED[self.watcher()] = (
                                lambda c: (c[0] + len(chunk), c[1], time.time()))(
                                    Handler.CARRIED.get(self.watcher()) or (0, 0, 0))
                    except OSError as e:
                        trouble = "the disk stopped answering: %s" % e
                        break
                # How this machine is faring, over the last eight blocks: a disk
                # holding the line up hands work to the cache, a disk well ahead of it
                # takes the work back.
                if share and taken and taken % 8 == 0:
                    now = self.share_by_reads(now, share[0], disk, wrote)
                    disk, wrote = 0.0, 0.0
                if not chunk:
                    trouble = "the file ended %d bytes early" % remaining
                    break
                at += len(chunk)
                taken += 1
                # and held back while anybody is watching. Nothing is stopped: it
                # goes on at a walk instead of a run, and has the line to itself again
                # the moment the last film ends.
                if copying_out and taken % 4 == 0:
                    # given up on: that machine has asked for something else since
                    if Handler.SYNC_NOW.get(whom) != sid:
                        trouble = ""
                        break
                    seen = WATCHING.snapshot()
                    # Only what shares this copy's own path. A film going out to the
                    # world and a copy to the machine in the next room do not take
                    # megabits from each other, and counting them together held a copy
                    # between two machines three metres apart to what was left of an
                    # upload.
                    side = Handler.side_of(self.client_address[0])
                    mine_side = [r for r in seen
                                 if Handler.side_of(r.get("address")) == side]
                    # Anybody watching anywhere brings a ceiling into it, because the
                    # disk is shared even where the line is not; how big that ceiling
                    # is comes from this copy's own side alone.
                    watching = sum(1 for r in seen if r.get("how") != "syncing")
                    if not watching:
                        # put down, not left standing: written once and never cleared,
                        # it read as "somebody is watching" for the life of the server
                        Handler.WATCHING_NOW = []
                    if watching:
                        # What the films are drawing and what the cacheing is, both
                        # measured this second. The ceiling follows from the two.
                        streaming = sum(float(r.get("mbit") or 0) for r in mine_side
                                        if r.get("how") != "syncing")
                        syncing = sum(float(r.get("mbit") or 0) for r in mine_side
                                      if r.get("how") == "syncing")
                        Handler.WATCHING_NOW = seen
                        ceiling = Handler.sync_ceiling(local().lib.config(),
                                                       streaming, syncing, side)
                        if ceiling > 0:
                            # how long these four blocks should have taken at the
                            # ceiling, less what they did take
                            owed = (4 * 262144 * 8 / (ceiling * 1e6)) - held_back
                            if owed > 0:
                                time.sleep(min(owed, 1.0))
                            held_back = 0.0
                try:
                    began = time.monotonic()
                    self.wfile.write(chunk)
                    wrote += time.monotonic() - began
                    held_back += time.monotonic() - began
                except Exception as e:
                    # ordinarily the player seeked away or closed, which is not worth
                    # a line; anything else is what we have been guessing about
                    if remaining > 4_000_000:
                        trouble = "the connection went: %s" % type(e).__name__
                    break
                if sid is not None:
                    WATCHING.sent(sid, len(chunk))
                remaining -= len(chunk)
        if trouble:
            # A film that stops mid-stream reads as ERROR_CODE_IO_UNSPECIFIED on the
            # device and as nothing at all here, which is how it stayed a mystery.
            with open(os.path.join(ROOT, "debug.log"), "a", encoding="utf-8") as f:
                # and what asked for it. A film arrived from an address with no
                # program name against it and nobody could say what had fetched it -
                # the name it calls itself is the one thing that answers that, and it
                # costs a few characters on a line that is already being written.
                said = (self.headers.get("X-Palladium-App")
                        or (self.headers.get("User-Agent") or "")[:60] or "says nothing")
                f.write("%s stream cut %s to %s [%s] after %.1f of %.1f MB - %s%s"
                        % (time.strftime("%H:%M:%S"), os.path.basename(path)[:40],
                           self.client_address[0], said, (asked - remaining) / 1e6,
                           asked / 1e6, trouble, chr(10)))

    def do_HEAD(self):
        """The same answers as GET, without the body.

        A downloader asks this first - how big, what kind, am I allowed - and until now
        it was answered by the plain file handler, which knows nothing about the
        addresses this server invents, and which never asked who was calling.
        """
        path = self.path.split("?")[0]
        self.role = self.who(path)
        if self.role is None:
            self.refused(path, "not a path a guest may open")
            self.send_error(403, "not allowed")
            return
        m = re.match(r"^/i/([A-Za-z0-9]{5})(?:\.apk)?$", path)
        if m:
            if not INVITES.by_code(m.group(1)):
                self.send_error(404, "no such code")
                return
            self.send_app(head_only=True)
            return
        if path == "/palladium.apk":
            self.send_app(head_only=True)
            return
        super().do_HEAD()

    def do_GET(self):
        self.safely(self._do_GET)

    def _do_GET(self):
        path = self.path.split("?")[0]
        # the invite link carries its token in the path rather than the query, so the
        # landing page has to answer before the guard could possibly recognise anyone
        m = re.match(r"^/s/([A-Za-z0-9_-]{8,})$", path)
        if m:
            self.landing(m.group(1))
            return
        # A page opened with a token in the address becomes a session, the way the
        # invitation page does it. This is how the home-screen app on an iPhone gets
        # in: it starts at /?t=<token> in a cookie jar of its own, and without this
        # everything the page went on to ask for would be refused.
        if path in ("/", "/index.html") and "t=" in (self.path.split("?", 1) + [""])[1]:
            token = self.bearer()
            if token and INVITES.check(token):
                self.send_response(302)
                self.send_header("Location", path)
                self.send_header("Set-Cookie", self.cookie_for(token))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
        # the same code, exchanged for the invitation it stands for. A television
        # cannot reasonably be made to type a token, so it types five characters and
        # asks what they mean.
        m = re.match(r"^/i/([A-Za-z0-9]{5})/setup$", path)
        if m:
            if self.too_many_guesses():
                self.send_error(429, "too many tries; wait an hour")
                return
            invite = INVITES.by_code(m.group(1))
            if not invite:
                self.wrong_guess()
                self.refused(path, "no such setup code")
                self.send_error(404, "no such code")
                return
            self.reply_json({"token": invite["token"], "name": invite["name"],
                             "serverId": self.server_id()})
            return
        # the app, fetched by a code short enough to type with a remote. It is the one
        # thing this address does: no library, no streams, only the file.
        m = re.match(r"^/i/([A-Za-z0-9]{5})/open$", path)
        if m:
            # the same code that fetches the app also opens the library, for somebody
            # typing on a television or reading a code off a screen
            invite = INVITES.by_code(m.group(1))
            if not invite:
                self.locked_page()
                return
            self.landing(invite["token"])
            return
        m = re.match(r"^/i/([A-Za-z0-9]{5})(?:\.apk)?$", path)
        if m:
            invite = INVITES.by_code(m.group(1))
            if not invite:
                self.send_error(404, "no such code")
                return
            self.role = "guest"
            self.guest_name = invite["name"]
            self.send_app(who=invite["name"])
            return
        self.role = self.who(path)
        if self.role == "guest" and self.wants_a_page(path):
            # count the year from this visit, not from the day the link was opened:
            # somebody who watches every week should never be asked for it again
            for bit in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = bit.strip().partition("=")
                if k == "pal" and v:
                    self.renew = v
        if self.role is None and self.wants_a_page(path):
            # an invitation that has expired, or a cookie that is gone: say what to do
            # rather than refusing in the abstract
            self.refused(path, "no invitation - shown the door")
            self.locked_page()
            return
        if self.role is None:
            self.refused(path, "not a path a guest may open")
            self.send_error(403, "not allowed")
            return
        if path == "/invites":
            people = [self.with_link(r) for r in INVITES.load()]
            mine = self.owner_caching()
            for row in people:
                row["cost"] = self.follower_cost(row["token"])
                # What sort of key this is. They were all drawn as people, so a key
                # made for a machine sat in the list of viewers being asked whose
                # half-watched films it should keep.
                row["role"] = Handler.role_of(row)
                # and what they call themselves, if they have said. The name on the
                # invitation is the owner's record of who they gave a key to; this is
                # the name that shows on a screen.
                row["shown"] = str(((read_settings() or {}).get("users") or {})
                                   .get(row["token"], {}).get("myName") or "")
                row["kind"] = "machine" if row["role"] == "cache" else "guest"
                # the owner's own key: one person, one row
                row["you"] = bool(mine.get("token")
                                  and row["token"] == mine["token"])
            self.reply_json({"people": people,
                             # what the owner is called, so no screen shows "me" -
                             # which is a placeholder a server wears until somebody
                             # says who they are, not a person's name
                             "ownerName": (read_settings() or {}).get("ownerName") or "",
                             "me": dict(mine,
                                        cost=self.follower_cost(mine.get("token")
                                                             or "me")),
                             "lan": LAN_IP, "wan": wan_ip(), "port": PORT})
            return
        if path == "/nowplaying":
            # the AV panel polls this while its Media app is open: plain JSON, no auth
            # beyond being on this network, and an answer well inside its 2.5s poll
            self.reply_json(local().now_playing())
            return
        # clients resolve a Part key against the library's own base, so it arrives
        # with /local in front of it; both spellings mean the same file
        if path in ("/subs/side", "/local/subs/side"):
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            self.serve_sidecar(local().part((args.get("file") or ["0"])[0]),
                               int((args.get("n") or ["0"])[0]),
                               (args.get("shift") or ["0"])[0])
            return

        if path == "/subs/plan":
            # what is already known about this subtitle's timing, without measuring
            # anything: a player asks on the way in so it can say what is in force
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            one = lambda name, fallback="": (args.get(name) or [fallback])[0]
            found = local().file_for(one("key"), int(one("mi", "0") or 0)) or {}
            parts = []
            if found.get("file"):
                parts = self.sub_fit(self.fit_name(found["file"],
                                                   int(one("index", "0") or 0))) or []
            self.reply_json({
                "parts": parts,
                "kind": ("parts" if len(parts) > 1
                         else "drift" if parts and abs(float(parts[0][1]) - 1.0) > 1e-9
                         else "static" if parts else "none")})
            return

        if path == "/subs/sync":
            # Work out how far a subtitle is out and, if it is sure, write it down for
            # everybody. The film's own sound is the reference: nothing is fetched, and
            # no subtitle is trusted to be what it claims.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            one = lambda name, fallback="": (args.get(name) or [fallback])[0]
            self.reply_json(self.sync_subtitle(
                one("key"), int(one("mi", "0") or 0), int(one("index", "0") or 0),
                one("skey"), one("sub"), one("save", "1") not in ("0", "false", "")))
            return

        if path == "/follow/now":
            # Build the list again and take what is missing, now: what is worth
            # keeping changed the moment somebody started watching something.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            self.reply_json(pd_follow.sync_now(lambda: local().lib.config(),
                                               local().lib, local()))
            return
        if path == "/follow/log":
            # every file that has gone to a machine keeping copies, newest first,
            # with where it went
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                many = max(1, min(int((args.get("limit") or ["200"])[0]), 2000))
            except ValueError:
                many = 200
            import pd_traffic
            self.reply_json({"copies": pd_traffic.copies(many)})
            return
        if path == "/follow/queue":
            # What the machine keeping copies will take, in the order it will take
            # it, and what it already holds.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            held = set(Handler.COPIES.get("keys") or [])
            # Only what the other machine says it holds. Having sent a file once is
            # not the same as it being there - it can arrive wrong, be swept away to
            # stay under a cap, or never finish - and treating "sent" as "here" hid
            # forty-six rows the other machine was still asking for, which is how a
            # transfer comes to have no line anywhere on this page.
            # The same question the other machine last asked, rather than one of this
            # page's own. Asking for the night's list at four in the afternoon showed
            # a queue nobody was working through.
            ask = Handler.LAST_ASK
            fresh = ask.get("when") and time.time() - ask["when"] < 900
            rows = self.worth_copying(
                float(ask["hours"]) if fresh else float(one.get("hours") or 4),
                bool(ask["deck"]) if fresh else False,
                int(ask["episodes"]) if fresh else int(one.get("episodes") or 6),
                None,
                float(ask["casual"]) if fresh else float(one.get("casualHours") or 0),
                bool(ask["whole"]) if fresh else bool(one.get("wholeList")))
            queue = [{"key": r.get("key"),
                      "title": r.get("title") or r.get("name"),
                      # why it is in the list, and whose viewing put it there
                      "why": r.get("why") or "",
                      "who": r.get("who") or "",
                      "gb": round((r.get("size") or 0) / 1e9, 2),
                      "here": r.get("key") in held,
                      # nothing is moved to the front by hand any more: a screen
                      # that is on is the only thing that lifts a row
                      "pinned": False,
                      # somebody is watching this, or the episode before it
                      "hot": bool(r.get("hot")),
                      "side": r.get("side") is not None}
                     for r in rows]
            # What is still to come stands above what has already arrived. The order
            # inside each half is the order the other machine will work in; a list
            # that reads top to bottom should not put a dozen finished rows between
            # the thing being fetched and the thing after it.
            queue.sort(key=lambda r: 1 if r["here"] else 0)
            # And the file actually being fetched this minute goes at the top, whether
            # or not the list as it stands now would still ask for it. The other
            # machine works from the list it was given a minute ago and finishes what
            # it started; a queue that leaves that file out is describing a different
            # evening from the one happening. It is the first thing anybody looks for.
            # What would not come stands above what has not been tried: a file that
            # failed is the thing holding the queue up, and it was being reported in a
            # list of its own underneath while the queue above pretended it was next.
            import pd_follow
            stuck = {}
            for bad in (list(Handler.FOLLOWER_TROUBLE)
                        + list(pd_follow.troubles() or [])):
                mark = str(bad.get("name") or "")
                if mark:
                    stuck[mark] = bad
            if stuck:
                def failed(r):
                    title = str(r.get("title") or "")
                    for name, bad in stuck.items():
                        if name == r.get("name") or (title and
                                                     name.startswith(title)):
                            return bad
                    return None
                first, rest = [], []
                for r in queue:
                    bad = failed(r)
                    if bad:
                        r["stuck"] = str(bad.get("said") or "would not come")
                        r["tries"] = int(bad.get("tries") or 1)
                        first.append(r)
                    else:
                        rest.append(r)
                queue = first + rest
            busy = next((r for r in WATCHING.snapshot()
                         if r.get("how") == "syncing"), None)
            if busy:
                mark = str(busy.get("key") or "")
                already = next((r for r in queue if str(r["key"]) == mark), None)
                if already:
                    queue.remove(already)
                    already["now"] = True
                    queue.insert(0, already)
                else:
                    queue.insert(0, {
                        "key": mark,
                        "title": str(busy.get("title") or ""),
                        "gb": round(float(busy.get("size") or 0) / 1e9, 2),
                        "here": False, "pinned": False, "hot": False,
                        "side": False, "now": True})
            self.reply_json({"queue": queue})
            return
        if path == "/follow/test":
            # Both halves of the question, from whichever machine is asked: can this
            # one reach the server it follows, and can it reach the machine that
            # follows it. Nothing is changed - it is a knock on a door.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(local().lib.config())
            said = {"master": pd_follow.try_master(one) if one.get("master") else None,
                    "follower": pd_follow.try_standby(
                        (self.standby_now() or {}).get("where") or ""),
                    "kept": pd_follow.kept_here(one)}
            self.reply_json(said)
            return
        if path == "/follow":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            # the key this server hands to a machine that follows it, if one has
            # been made: the page shows it rather than making somebody press a
            # button to find out whether there is one
            # a key is a machine's because somebody marked it Cache under Users;
            # the flag underneath it is set at the same time, but the role is what
            # was chosen and what this list is of
            machines = [r for r in INVITES.load()
                        if Handler.role_of(r) == "cache"]
            mine = next(iter(machines), None)
            self.reply_json({"follow": pd_follow.settings(local().lib.config()),
                             "state": pd_follow.look(),
                             # how much of the reading is handed to the cache
                             "share": int(local().lib.config()
                                          .get("shareWithCopy") or 0),
                             # every key made for a machine, with what each one
                             # allows. There was one key for the main server and one
                             # ceiling for whoever held it.
                             "keys": [
                                 {"token": r.get("token") or "",
                                  "name": r.get("name") or "",
                                  "created": int(r.get("created") or 0),
                                  "lastSeen": int(r.get("lastSeen") or 0),
                                  "cap": float(r.get("cap") or 0),
                                  "mayCopy": bool(r.get("mayCopy", True)),
                                  "role": Handler.role_of(r)}
                                 for r in machines],
                             "mine": {"key": (mine or {}).get("token") or "",
                                      "where": "http://%s:%d" % (LAN_IP, PORT),
                                      # the ceiling this library puts on a machine
                                      # that follows it; 0 means it sets its own
                                      "cap": float(local().lib.config()
                                                   .get("cacheCap") or 0)},
                             # and the machine following this one, as it announced
                             # itself: the address viewers are sent to when this
                             # server is off
                             # what this machine is called, and what it would be
                             # called if nobody had said: the page shows both, so a
                             # name is chosen rather than guessed at
                             "name": self.server_name(),
                             "hostname": socket.gethostname(),
                             # what the main server's key allows, the main server's name, and
                             # whether this request is the main server managing this machine
                             "houseCap": float(pd_follow.ALLOWED.get("gb") or 0),
                             "houseName": pd_follow.house_doors().get("name") or "",
                             "fromHouse": bool(self.headers.get("X-Palladium-House")
                                               and self.managed_from_the_house()),
                             # which catalogue keys this house holds: a button
                             # offering to send one it has not got can only fail
                             "have": {k: bool((local().lib.config().get(k) or "").strip())
                                      for k in ("opensubtitles_key", "tmdb_key")},
                             "standby": self.standby_now(),
                             # every machine that follows this one, freshest first,
                             # with whether it has spoken lately
                             "followers": sorted(
                                 [dict(f, alive=time.time() - f["when"] < 180,
                                       ago=int(time.time() - f["when"]))
                                  for f in Handler.FOLLOWERS.values()],
                                 key=lambda f: -f["when"]),
                             # the port this one answers on, and what the settings
                             # file asks for if that is a different number
                             # how much of the cache is used, and of what
                             "kept": pd_follow.kept_here(
                                 pd_follow.settings(local().lib.config())),
                             # and what would not come, if anything: one error at a
                             # time was written over by the next success
                             "trouble": pd_follow.troubles(),
                             # machines the owner has stopped, which keep their key
                             # and are refused anyway until they are let back in
                             "blocked": list(local().lib.config()
                                             .get("blockedCaches") or []),
                             "port": PORT,
                             "portWanted": int((read_settings() or {}).get("port")
                                               or PORT)})
            return
        if path == "/performance":
            # What this machine is doing that a person would feel: films being
            # encoded, a subtitle being written, the library being read, copies being
            # taken. And whether game mode is on, which stops the ones that can wait.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.reply_json(self.how_busy())
            return
        if path == "/machine/addons":
            # the big pieces of machinery: what they are for, whether they are on this
            # computer, and whether they are turned on
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_ai_subs
            os.environ["PALLADIUM_DATA"] = ROOT
            stored = read_settings() or {}
            self.reply_json({"addons": pd_ai_subs.addons(stored.get("addons") or {}),
                             "can": pd_ai_subs.ready() and tools_on(),
                             "toolsOff": bool(stored.get("toolsOff")),
                             # the scripts that drive the models, which are ours and
                             # come from our own site rather than from a model's
                             "tools": pd_ai_subs.tools()})
            return
        if path == "/stream/buffer":
            self.stream_buffer()
            return
        if path == "/copy/read":
            # One stretch of a file this machine holds, for the server it copies from
            # to hand on as part of its own answer. Named by the file and its mark
            # rather than by a number: a part id belongs to the library that issued
            # it, and these are two libraries.
            if not self.follows_here():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            name = (args.get("name") or [""])[0]
            mark = (args.get("mark") or [""])[0]
            here = self.copy_holds(name, mark)
            if not here:
                self.send_error(404, "not that file")
                return
            self.send_file_ranged(here)
            return
        if path == "/follow/side":
            # A subtitle file beside a video, as it lies on disk. Named by the video
            # it belongs to and its place in that list, never by a path: the machine
            # asking is another server, and a path is a thing to be careful with.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                which = int((args.get("n") or ["0"])[0])
            except ValueError:
                which = 0
            video = local().part((args.get("part") or ["0"])[0])
            if not video:
                self.send_error(404)
                return
            import pd_localapi as _la
            beside = _la.sidecars(video)
            if not (0 <= which < len(beside)):
                self.send_error(404)
                return
            self.send_file_ranged(beside[which]["file"])
            return
        if path == "/follow/progress":
            # Where the people this machine keeps copies for had got to. Sent as what
            # the thing is rather than as a number: the other library files it under
            # its own. Without this, a house whose server sleeps at ten finds every
            # film starting from the beginning at five past.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            decks, lists, shuffles = self.cached_for()
            whom = list(dict.fromkeys(decks + lists + shuffles))
            self.reply_json({"progress": local().progress_of(whom, since),
                             "now": int(time.time())})
            return
        if path == "/follow/here":
            # The following server saying where it can be reached. Written down so
            # that a viewer whose server does not answer has somewhere else to ask.
            # Only the main server's own cache: an ordinary invitation is somebody
            # else's machine, and its address is theirs, not ours to hand out.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                port = max(1, min(int((args.get("port") or ["8765"])[0]), 65535))
            except ValueError:
                port = 8765
            # the address it came from, not one it asks us to believe
            where = "http://%s:%d" % (self.client_address[0], port)
            # and the address from outside the main server, which is this network's own
            # with that machine's port forwarded to it - unless the owner set one
            # by hand, for a cache that lives somewhere else entirely
            said = (args.get("outside") or [""])[0][:120].strip().rstrip("/")
            # an address typed without http:// in front of it is still an address
            if said and not said.startswith(("http://", "https://")):
                said = "http://" + said
            outside = said if re.match(r"^https?://[\w.\-]+(:\d+)?$", said) else (
                "http://%s:%d" % (wan_ip(), port) if wan_ip() else "")
            def number(name):
                try:
                    return round(float((args.get(name) or ["0"])[0]), 1)
                except (ValueError, TypeError):
                    return 0.0
            self.remember_cache(where, (args.get("name") or [""])[0][:40],
                                  outside.rstrip("/"),
                                  (args.get("build") or [""])[0][:20],
                                  {"gb": number("gb"), "free": number("free"),
                                   "cap": number("cap"),
                                   "files": int(number("files"))})
            # whether that machine lets this one set how it copies
            if where in Handler.FOLLOWERS:
                Handler.FOLLOWERS[where]["managed"] = \
                    (args.get("managed") or ["0"])[0] == "1"
                Handler.remember_followers()
            self.reply_json({"ok": True, "where": where, "outside": outside})
            return
        if path == "/copies":
            # which titles the other machine is holding, so a shelf can mark them
            if not (self.role == "owner" or INVITES.check(self.bearer(),
                                                          self.app_name())):
                self.send_error(403, "not allowed")
                return
            said = dict(Handler.COPIES)
            # an answer nobody has refreshed for a day is not worth drawing
            if said.get("when") and time.time() - said["when"] > 86400:
                said = {"keys": [], "when": 0}
            self.reply_json(said)
            return
        if path == "/follow/file":
            # One file this machine fetched, for the server it follows to take a copy
            # of. Only that server may ask, and only for something this machine went
            # and got itself.
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            where = pd_torrents.file_of((args.get("hash") or [""])[0],
                                        (args.get("index") or ["-1"])[0])
            if not where or not os.path.exists(where):
                self.send_error(404, "not here")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(where)))
            self.end_headers()
            with open(where, "rb") as f:
                while True:
                    lump = f.read(262144)
                    if not lump:
                        break
                    try:
                        self.wfile.write(lump)
                    except Exception:
                        break
            return
        if path == "/server/build":
            # The installer this machine is running, for the one that follows it. Only
            # a cache may ask: it is 34 MB off this machine's disk, and the hash
            # goes with it so what arrives is checked rather than trusted.
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            said = Handler.build_on_offer()
            if not said:
                self.reply_json({"version": ""})
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            if (args.get("file") or [""])[0] not in ("1", "true", "yes"):
                self.reply_json(said)
                return
            where = Handler.BUILD_MARK.get("path") or ""
            if not where or not os.path.exists(where):
                self.send_error(404, "no installer here")
                return
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.microsoft.portable-executable")
            self.send_header("Content-Length", str(os.path.getsize(where)))
            self.send_header("X-Palladium-Version", said.get("version", ""))
            self.end_headers()
            with open(where, "rb") as f:
                while True:
                    lump = f.read(262144)
                    if not lump:
                        break
                    try:
                        self.wfile.write(lump)
                    except Exception:
                        break
            return
        if path == "/standby":
            # The other machine that holds copies of what this house watches, for a
            # client to fall back on when this server is off. Answered to anybody who
            # may read the library at all - it is this house's second machine.
            if not (self.role == "owner" or INVITES.check(self.bearer(),
                                                          self.app_name())):
                self.send_error(403, "not allowed")
                return
            self.reply_json(self.standby_now())
            return
        if path == "/follow/invites":
            # The main server's invitations, for the cache to honour: a guest whose
            # server is off reaches the cache with the link they already have.
            invite = INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            self.reply_json({"invites": [
                {"token": r.get("token"), "name": r.get("name"),
                 "expires": r.get("expires"), "language": r.get("language"),
                 # and what the key is for. Sent as a bare guest, the owner's own key
                 # opened nothing of theirs on their own second machine, and the panel
                 # on the wall was refused the one thing it asks for.
                 "role": r.get("role") or "",
                 # and what is kept for them, so the other machine can say on its
                 # own front page why its shelves are short
                 "shareLan": bool(r.get("shareLan", True)),
                 "cacheDeck": bool(r.get("cacheDeck")),
                 "cacheList": bool(r.get("cacheList")),
                 "cacheCasual": bool(r.get("cacheCasual"))}
                for r in INVITES.load() if not r.get("follows")],
                "owner": self.owner_caching(),
                # and who the person at that machine is, so the cache files their
                # viewing under the same name: the places travel under it, and a
                # machine that calls them somebody else shows an empty shelf
                "ownerIs": str((read_settings() or {}).get("ownerIs") or ""),
                "ownerName": str((read_settings() or {}).get("ownerName") or ""),
                "name": socket.gethostname(),
                # the catalogue key, so what the other machine copies arrives as
                # films with posters rather than as a list of file names. It is the
                # house's own key, going to the main server's own second machine.
                "tmdb": (local().lib.config().get("tmdb_key") or ""),
                # How each person has their subtitles drawn, and in what language.
                # An evening that moves to the cache mid-film should not change size,
                # colour or language halfway through - and the menus over there had
                # this machine's defaults behind them rather than that person's.
                "look": self.everyones_subtitles(),
                "language": (local().lib.config().get("language") or "en-US")})
            return
        if path == "/follow/playing":
            # What another server should keep a copy of: whatever is being watched
            # here, and the episodes after it. Only a key marked as a cache may
            # ask - it is the main server's viewing, not a guest's business.
            # Two kinds of cache. A key marked as one is another server of this
            # house: it may know what the main server is watching. An ordinary invitation
            # is a person keeping their own copies on their own machine - they get
            # what is theirs and nothing else, which is no more than they can already
            # see in Continue watching.
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (self.role == "owner" or invite):
                self.send_error(403, "not allowed")
                return
            mine = None if (self.role == "owner" or invite.get("follows"))                 else invite["token"]
            try:
                hours = max(0.0, min(float(
                    (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                     .get("hours") or ["4"])[0]), 24.0))
            except (ValueError, IndexError):
                hours = 4.0
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            deck = (args.get("deck") or ["0"])[0] in ("1", "true", "yes")
            whole = (args.get("whole") or ["0"])[0] in ("1", "true", "yes")
            try:
                episodes = max(0, min(int((args.get("eps") or ["6"])[0]), 40))
            except ValueError:
                episodes = 6
            try:
                # hours of casual watching to keep ahead, for whoever asked for it
                casual = max(0.0, min(float((args.get("casual") or ["0"])[0]), 24.0))
            except ValueError:
                casual = 0.0
            # Which kinds of thing this copy will take this minute. Each kind is off,
            # inside its hours, or always, and the machine keeping the copies works out
            # which of them apply now - it is the one that knows what time it is there.
            # A copy that says nothing gets all of them, as every copy did before.
            these = (args.get("these") or [""])[0].strip()
            kinds = ([k for k in these.split(",") if k in Handler.KINDS]
                     if these else None)
            cfg = local().lib.config()
            # What the shuffle would play next is copied when the machine keeping the
            # copies asks for it, and not otherwise. It used to be asked and then
            # overruled here, so the setting on that machine's own page did nothing
            # anybody could see. It is that machine's disk and its own hours.
            if mine is None:
                # The fuller question wins. A cache asks twice a round - once for
                # what is on a screen, once for the watchlists as well - and asks the
                # lighter one every minute while a copy is running. Remembering
                # whichever came last meant the page reproduced the short list and
                # left out everything actually being fetched from a watchlist.
                held = Handler.LAST_ASK
                fresher = (deck or not held.get("deck")
                           or time.time() - (held.get("when") or 0) > 900)
                if fresher:
                    Handler.LAST_ASK = {"hours": hours, "deck": deck,
                                        "episodes": episodes, "casual": casual,
                                        "whole": whole, "when": int(time.time())}
            # whose cache this is: a name means only that viewer's own viewing is
            # worth copying, and nothing means the whole house's
            only = (args.get("for") or [""])[0][:60].strip()
            # Two lists, from one pass. "holding" is everything worth keeping whatever
            # the hour; "wanted" is the part this copy will take this minute. The copy
            # sweeps against the first and fetches from the second - asked with the
            # hour applied, the only list it had was the short one, so a kind set to
            # night was fetched at nine and deleted at eight the next morning.
            holding = self.worth_copying(hours, deck, episodes, mine,
                                         casual, whole, only, None)
            keep = None if kinds is None else set(kinds)
            # A reason belonging to no kind is taken whatever the hour, which is what
            # the filter inside wanted_keys did: the episodes after a programme and the
            # subtitles riding with a film are named by neither of the settings.
            wanted = ([w for w in holding
                       if not Handler.kind_of(w.get("why") or "")
                       or Handler.kind_of(w.get("why") or "") in keep]
                      if keep is not None else holding)
            self.reply_json({"wanted": wanted, "holding": holding,
                             # who this house has, so the other machine can offer
                             # the names rather than asking somebody to type one
                             "house": self.everyone_here()[:40],
                             # what this machine is running, and what it thinks
                             # the asking one is. A cache set to replace itself
                             # when the main server moves on reads both from here - and
                             # neither was ever sent, so it compared nothing against
                             # nothing and stayed on the build it was installed with.
                             "serverVersion": self.build_version(),
                             "mine": (Handler.STANDBY.get("build") or ""),
                             # whose viewing this answer is about, so the other
                             # machine can say so on its own page
                             "whose": ("the main server" if mine is None
                                       else (invite.get("name") or "you")),
                             # and whether anybody is watching this minute: the disk
                             # and the line belong to them, not to a copy of a film
                             # nobody has started
                             "watching": len(local().playing_now() or {}) > 0,
                             # somebody here is going to bed early: take everything
                             # anybody is half way through, whatever the clock says
                             "earlyUntil": (Handler.EARLY_UNTIL
                                            if Handler.EARLY_UNTIL > time.time()
                                            else 0)})
            return
        if path == "/subs/making":
            # what is being written down at the moment, and what is waiting
            import pd_ai_subs
            self.reply_json(pd_ai_subs.look())
            return
        if path == "/subs/find":
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            self.reply_json(self.find_subtitles(
                (args.get("key") or [""])[0],
                (args.get("lang") or [self.viewer_language()])[0]))
            return

        if path == "/settings":
            # the query is parsed further down for the media paths; this branch sits
            # above that, so it reads its own
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            key = (args.get("key") or [""])[0]
            device = self.device_of((args.get("device") or ["web"])[0])
            self.reply_json({
                # what this viewer is called, so a page can show it and let them
                # change it - guests included
                "myName": (self.viewer_settings(self.settings_file()).get("myName")
                           or self.guest_name
                           or (read_settings() or {}).get("ownerName") or ""),
                # how a film should be fetched for this viewer
                "splitPlay": bool(self.viewer_settings(self.settings_file())
                                  .get("splitPlay", True)),
                "failover": bool(self.viewer_settings(self.settings_file())
                                 .get("failover", True)),
                # whether finishing a title takes it off this viewer's watchlist
                "dropWatched": bool(self.viewer_settings(self.settings_file())
                                    .get("dropWatched", True)),
                "subtitles": self.subtitle_settings(key, device),
                "override": bool(key) and self.has_override(key, device),
                "device": device,
                # every screen at once, for a settings page that shows all three
                "devices": {d: self.subtitle_settings(None, d) for d in self.DEVICES},
                "language": self.viewer_language(),
                # the accent, and the colours on offer, so a settings page anywhere
                # can draw the row without knowing the list itself
                "accent": self.accent_now(),
                # a poster behind the shelves, per screen: the row for each, and
                # the answer for whichever screen is asking
                "backdrop": self.backdrop_all(),
                "backdropHere": self.backdrop_now(),
                # what the server draws with, so a settings page can show which
                # swatch is merely the default and which one this viewer chose
                "accentDefault": self.accent_default(),
                "accentMine": str(self.viewer_settings(self.settings_file())
                                  .get("myAccent", "") or ""),
                "accents": [{"code": c, "name": n} for c, n in ACCENTS],
                # a stored null is not an answer: the key has been written empty,
                # and "not false" is what everybody who never chose should get -
                # otherwise the next episode quietly stops following this one
                "autoNext": self.settings_file().get("autoNext") is not False,
                # whether one release is brought to the level of the next, and where
                # that level is
                "evenVolume": bool(self.settings_file().get("evenVolume")),
                "volumeTarget": self.settings_file().get("volumeTarget", VOLUME_TARGET),
                # whether this viewer takes part in watch parties at all
                "watchParty": self.wants_a_party(),
                # whether subtitles are put in step by themselves: the server's
                # answer unless this viewer has one of their own
                # whether the next episode's subtitle is fetched before it starts;
                # anything but an explicit no means yes, as it always behaved
                "autoFetch": self.settings_file().get("autoFetch") is not False,
                # whether an owner's key still runs the place from outside the house
                "remoteAdmin": self.settings_file().get("remoteAdmin") is not False,
                "autoSync": self.scans_for(),
                "autoScan": self.auto_scan(),
                # the front page: what this viewer sees, what they chose for
                # themselves, and what the server sets for anybody who has not
                "homeRows": self.home_rows(),
                "homeRowsMine": (self.viewer_settings(self.settings_file())
                                 .get("homeRows") or []),
                "homeRowsDefault": self.home_default(),
                "ownerName": self.owner_name(),
                # whether a picture subtitle can be painted in for this caller, and
                # the owner's setting behind it
                "burnAllowed": self.may_burn(),
                "burnFor": self.burn_policy(),
                # and which clock they read: 24 hours unless they say otherwise
                "clock": str(self.viewer_settings(self.settings_file())
                             .get("clock", "24")),
                "nextDelay": int(self.settings_file().get("nextDelay", 5)),
                # the ceiling on each side of the front door, and which side this
                # viewer is on, so a player can say what it is actually going to get
                "quality": self.quality_caps(),
                "here": "home" if self.at_home() else "away",
                # how far this subtitle has been moved, when one was asked about: a
                # correction belongs to the file, not to the screen it was made on
                "subShift": self.sub_shift(key, (args.get("sub") or [""])[0]),
                # and whether the film asked for it or somebody did
                "shiftBy": self.shift_source(key, (args.get("sub") or [""])[0]),
                # and this viewer's own preference, which everybody may set
                "mine": self.my_quality(),
            })
            return
        if path == "/marks":
            # all, some or none of what this key stands for, per shelf: what the
            # buttons on a programme and a season have to draw themselves from now
            # that marking one means marking its episodes
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            key = (args.get("key") or [""])[0]
            self.reply_json({"key": key,
                             "watchlist": self.marks_state("watchlist", key),
                             "favorites": self.marks_state("favorites", key)})
            return
        if path == "/mystream":
            # what this caller is being sent, for its own statistics line
            mine = [w for w in WATCHING.snapshot() if w["address"] == self.client_address[0]]
            self.reply_json(mine[0] if mine else {})
            return
        if path == "/watching":
            # a stream knows the address it goes to; the player knows what the device
            # calls itself. Match them on the title and show the friendlier one.
            live = WATCHING.snapshot()
            names = local().devices()
            doing = local().playing_now()
            matched = set()          # clients whose report belongs to an open stream
            for row in live:
                # a file going to the machine that keeps copies is not a viewing:
                # nobody is at the other end of it, so it borrows neither a name from
                # the main server nor a state from a player
                if row.get("how") == "syncing":
                    row["state"] = "syncing"
                    continue
                named = names.get(row["title"])
                if named and row["who"] == row["address"]:
                    row["who"] = named
                # what the player itself says: running or held, and how far in. The
                # stream knows the bytes; only the client knows the film.
                said = doing.get(row.get("key") or "") or doing.get(row["title"]) or {}
                if said:
                    matched.add((said.get("who") or "",
                                 said.get("device") or said.get("title")))
                for k in ("state", "position", "duration", "episode",
                          "client", "device", "app", "kind", "info"):
                    if said.get(k) not in (None, ""):
                        row[k] = said[k]
                # and whether it was put on rather than chosen. Now playing read the
                # same for a film somebody sat down to and an episode a shuffle dealt
                # them, which are not the same evening - and it is the difference
                # that decides what is kept ahead for them.
                row["casual"] = bool(said.get("casual"))
            # Somebody paused has no connection open - a direct play closes it, and a
            # transcode is stopped to save the GPU - but they are still watching, and
            # their player says so. They belong in the list.
            for said in local().playing_reports():
                if (said.get("who") or "",
                        said.get("device") or said.get("title")) in matched:
                    continue
                live.append({
                    # the person, and the machine they are on only if the report
                    # cannot say who they are: a row drawn from a player's report
                    # took the device's own model name, so the same person watching
                    # one film appeared as themselves on one machine and as a
                    # telephone's part number on the other. A report says who by
                    # their key, which is not a thing to print at somebody.
                    "who": (self.name_of(said.get("who")) if said.get("who")
                            else said.get("device") or "someone"),
                    "title": said["title"], "episode": said.get("episode") or "",
                    "quality": "", "how": "waiting", "address": "",
                    "started": said.get("began") or 0,
                    "seconds": 0, "mb": 0, "mbps": 0.0, "mbit": 0.0,
                    "average": 0.0, "peak": 0.0,
                    "state": said.get("state") or "paused",
                    "position": said.get("position"), "duration": said.get("duration"),
                    "client": said.get("client") or "", "key": said.get("key") or "",
                    # what is playing it, for a row with no stream of its own
                    "app": said.get("app") or "", "kind": said.get("kind") or "",
                })
            # One shape for every row: the film and its year in brackets, the way a
            # file is named. A player reports the name and the year apart, so the same
            # person watching the same film read as two different films.
            #
            # The year is dropped whether or not the title had to be rewritten. Cleared
            # only on the rewrite, a row whose title already carried its year kept the
            # label - and rows are gathered per machine under key plus that label, so
            # one film read off both machines was two rows on the page: the machine the
            # player reports to had the year, the other had nothing.
            for row in live:
                said = str(row.get("title") or "")
                year = str(row.get("episode") or "").strip()
                if year.isdigit() and len(year) == 4:
                    if not said.endswith("(%s)" % year):
                        row["title"] = "%s (%s)" % (said, year)
                    row["episode"] = ""
            # How many seconds of film each screen says it is holding. The server has
            # had this from every player since the reports started arriving, and used
            # it only to decide how hard to hold a copy back - nothing ever showed it,
            # so a viewer running thin was invisible to anybody but the throttle.
            now = time.time()
            for row in live:
                mark = str(row.get("who") or "") + "/" + str(row.get("address") or "")
                said = Handler.AHEAD.get(mark)
                if not said or now - (said[1] or 0) > 60:
                    continue
                row["ahead"] = round(float(said[0]), 1)
                row["aheadAt"] = int(said[1])
                if len(said) > 4 and said[4]:
                    row["stalled"] = True
            live.sort(key=lambda r: r.get("started") or 0)
            # and what the machine is listening to for its loudness, which is work
            # somebody watching a page should be able to see happening
            said = {"live": live}
            if MEASURE_NOW.get("name"):
                said["measuring"] = {
                    "name": MEASURE_NOW["name"],
                    "since": MEASURE_NOW.get("since") or 0,
                    "done": len(read_loudness()),
                    "left": len(MEASURE_SWEEP.get("left") or [])}
            self.reply_json(said)
            return
        if path == "/collections/season":
            # what the box under the Season field shows: the span read back, so a
            # typo is visible before it is saved rather than after it has quietly
            # taken nothing
            import pd_seasons
            asked = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            spec = str(asked.get("spec", [""])[0])
            nope = str(asked.get("without", [""])[0])
            spans = pd_seasons.parse(spec)
            out = pd_seasons.parse(nope)
            self.reply_json({"said": pd_seasons.describe(spans),
                             "saidWithout": pd_seasons.describe(out),
                             "spans": len(spans), "without": len(out)})
            return
        if path in ("/collections", "/collections/items"):
            shelves = self.collections()
            con = local().lib.db()
            try:
                if path == "/collections":
                    out = []
                    for c in shelves:
                        keys = self.collection_keys(con, c)
                        # the poster the shelf shows: the one chosen for it, or the
                        # first title it holds, so a new shelf has a face at once
                        face = str(c.get("cover") or "")
                        if face not in keys:
                            face = next((k for k in keys if not str(k).startswith("o")), keys[0] if keys else "")
                        art = None
                        if face:
                            one = local().metadata_for(con, face, brief=True) or {}
                            art = one.get("thumb") or one.get("grandparentThumb")
                        out.append(dict(c, count=len(keys), cover=face, art=art,
                                        resumeAt=self.shuffle_resume(c.get("id"))))
                    self.reply_json({"collections": out})
                    return
                args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                        if "?" in self.path else {})
                want = (args.get("id") or [""])[0]
                shelf = next((c for c in shelves if c.get("id") == want), None)
                if not shelf:
                    self.reply_json({"error": "no such collection", "Metadata": []}, 404)
                    return
                # what the shelf holds, read as seasons rather than as episodes
                raw = [str(k) for k in self.collection_keys(con, shelf)]
                seasoned, keys = self.read_as_seasons(con, raw)
                rows = []
                for key in keys:
                    one = local().metadata_for(con, key, brief=True)
                    if one:
                        rows.append(one)
                rows += seasoned

                # In the order they were made. A collection is usually a series of
                # films, and the year is how anybody reads one - alphabetical put
                # Resurrection before Aliens, and anything added by hand at the end.
                # A season's own title is "Season 10", which sorts before "Season 2"
                # and says nothing about which programme it belongs to - so a season
                # is placed by its programme and then by its number.
                def order(m):
                    name = (m.get("titleSort")
                            or (m.get("parentTitle") if m.get("type") == "season"
                                else "")
                            or m.get("title") or "")
                    return (int(m.get("year") or 0), name, int(m.get("index") or 0))
                rows.sort(key=order)
                # What is out: struck out by hand, or caught by the include words
                # and taken out again by the exclude ones. Both are shown while
                # editing, so either can be put back without searching for it.
                pinned = set(str(k) for k in (shelf.get("pinned") or []))
                hidden = set(str(k) for k in (shelf.get("hidden") or []))
                for one in rows:
                    one["hand"] = str(one.get("ratingKey")) in pinned
                # the keys the shelf actually holds, and the cards they were read
                # as: a programme drawn as its seasons is still on the shelf, and
                # comparing only the cards had it listed as struck out.
                held = set(raw) | set(str(m.get("ratingKey")) for m in rows)
                # Both excludes lifted: the title words that knocked something out,
                # and the season span that did. What either took is shown struck
                # out, so it can be put back without searching for it.
                wide = dict(shelf, hidden=[],
                            rule=dict(shelf.get("rule") or {}, without=[],
                                      seasonWithout=""))
                def strike(k):
                    """What is really out, for one key the rule or the hand took.

                    A programme drawn as its seasons: only the seasons that are not
                    on the shelf are out. Its own key put a whole series in the
                    struck row over one episode taken off one season.
                    """
                    k = str(k)
                    if k in held:
                        return []
                    if re.fullmatch(r"[0-9a-f]{12}", k):
                        mine = set(int(r["season"] or 0) for r in con.execute(
                            "SELECT DISTINCT season FROM episode WHERE item_id=?", (k,)))
                        # the pack's seasons too, or a season struck out by a span
                        # that this machine holds no file of is out of both rows
                        row = con.execute("SELECT title FROM item WHERE id=?",
                                          (k,)).fetchone()
                        mine |= set(int(o.get("index") or 0) for o in
                                    local()._offered_seasons_of(
                                        row["title"] if row else "", mine))
                        parts = ["%s-s%d" % (k, n) for n in sorted(mine)]
                        if parts:
                            return [p for p in parts if p not in held]
                    return [k]
                gone = []
                for k in (list(self.collection_keys(con, wide))
                          + list(shelf.get("hidden") or [])):
                    gone += [x for x in strike(k) if x not in gone]
                struck = []
                for key in gone:
                    key = str(key)
                    # a struck season is read as a season too, or eighteen cards in
                    # this row all carry the programme's name and nothing else
                    one = (self.season_card(con, key, [])
                           if re.match(r"^[0-9a-f]{12}-s\d+$", key)
                           else local().metadata_for(con, key, brief=True))
                    if one:
                        one["hand"] = key in hidden or key in pinned
                        struck.append(one)
                struck.sort(key=order)
                # which title is its face, resolved the same way the list is, so
                # an editing screen can mark it without asking twice
                face = str(shelf.get("cover") or "")
                if face not in [str(m.get("ratingKey")) for m in rows]:
                    face = str(rows[0].get("ratingKey")) if rows else ""
                self.reply_json({"name": shelf.get("name", ""), "id": shelf["id"],
                                 "rule": shelf.get("rule") or {}, "cover": face,
                                 "Metadata": rows, "Excluded": struck})
                return
            finally:
                con.close()
        if path == "/machine":
            # what this computer is set to do about Palladium: start it at sign-in,
            # and let the rest of the main server reach it
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            now = time.time()
            self.reply_json(dict(
                pd_machine.state(PORT, LAN_IP),
                server=self.build_version(),
                # newest first, and only what has spoken in the last day: a phone that
                # was here on Tuesday says nothing about what is running now
                clients=[dict(row, ago=int(now - row["when"]))
                         for row in sorted(self.SEEN.values(), key=lambda r: -r["when"])
                         if now - row["when"] < 86400]))
            return
        if path == "/library/numbering":
            # Seasons where the files count the episodes differently from the season,
            # and what each is set to do about it. The owner's: it moves rows.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            chosen = self.settings_file().get("numbering") or {}
            rows = local().lib.seasons_shifted()
            for r in rows:
                r["mode"] = str(chosen.get("%s-s%s" % (r["key"], r["season"]), "auto"))
            self.reply_json({"seasons": rows})
            return
        if path == "/party":
            # Whether there is one, who is in it, and whether the asker is.
            now = int(time.time())
            here = [dict(v, key=k,
                         name=self.named_for_reader(v.get("name")),
                         where=(v.get("where") if self.role == "owner" else ""))
                    for k, v in PARTY["who"].items()
                    if now - v.get("seen", 0) < 300]
            if not self.wants_a_party():
                self.reply_json({"on": False, "off": True, "in": False,
                                 "people": []})
                return
            # Who is about: the screens that have asked this server for anything in
            # the last few minutes, with what each has playing. This is what makes the
            # lobby a room rather than a message list - somebody arriving can see that
            # three people are here and two of them are already watching.
            now = int(time.time())
            playing = {}
            try:
                for row in WATCHING.snapshot():
                    playing[row.get("address") or ""] = row.get("title") or ""
            except Exception:
                playing = {}
            # One line per screen, not one per build it has run today: the client
            # register keeps a row for each address and version, and a phone that
            # updated twice was three people in the lobby.
            about, seen_here = [], set()
            for row in sorted(self.SEEN.values(), key=lambda r: -r["when"]):
                where = row.get("where", "")
                if now - row["when"] > 300 or where in seen_here:
                    continue
                seen_here.add(where)
                about.append({"name": row.get("name") or where,
                              "where": where,
                              "guest": bool(row.get("name")),
                              "version": row.get("version", ""),
                              "watching": playing.get(where, ""),
                              "ago": max(0, int(now - row["when"]))})
            asked = PARTY["asked"].get(self.party_key())
            owner = self.role == "owner"
            # a guest sees who is here, not where they are. Screens the server has no
            # name for are numbered, so two of them read as two people rather than as
            # the same line twice.
            counted = 0
            for row in about:
                if not owner:
                    named = self.named_for_reader(row["name"])
                    if named == self.A_SCREEN:
                        counted += 1
                        named = "Screen %d" % counted
                    row["name"] = named
                    row["where"] = ""
            self.reply_json({"on": bool(PARTY["on"]), "since": PARTY["since"],
                             "in": self.party_key() in PARTY["who"],
                             "title": PARTY.get("title", ""),
                             "key": PARTY.get("key", ""),
                             # who might be asked: the screens that have spoken to
                             # this server today, and everybody with an invitation
                             # the same screens, one line each, for asking
                             # Asking a screen by address is the owner's to do:
                             # it is their network. A guest may ask the other guests.
                             "screens": ([{"key": "here:" + r["where"],
                                           "name": r["where"], "where": r["where"]}
                                          for r in about if not r.get("guest")]
                                         if owner else []),
                             # only people who are actually here: an invitation
                             # nobody is holding open is not somebody to ask
                             "guests": [{"key": "guest:" + r["name"], "name": r["name"]}
                                        for r in about if r.get("guest")],
                             "invited": asked,
                             "here": about if self.in_the_house() else [],
                             "people": here})
            return
        if path == "/chat":
            # What has been said since the caller last looked. Held open until there
            # is something, the same way a notice is: a conversation that arrives half
            # a minute late is not one.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
                wait = max(0, min(int((args.get("wait") or ["0"])[0]), 55))
            except ValueError:
                since, wait = 0, 0
            if not self.wants_a_party():
                # said plainly rather than silently: the box knows to put itself away
                self.reply_json({"party": False, "off": True, "messages": [], "id": 0})
                return
            room = (args.get("room") or ["party"])[0]
            if room == "lobby":
                # The lobby is always open: anybody who may watch may sit in it,
                # whether or not there is a party on. It is where a party is arranged
                # in the first place.
                # a line addressed to one screen is for that screen, in the lobby as
                # much as in the party: a test sent to a television is not everybody's
                def lobby_lines():
                    return [m for m in CHAT if m.get("room") == "lobby"
                            and self.addressed_to_me(m.get("to", "all"))]
                fresh = [m for m in lobby_lines() if m["id"] > since]
                if not since and not fresh:
                    fresh = lobby_lines()[-20:]
                until = time.time() + wait
                while wait and not fresh and time.time() < until:
                    CHAT_RUNG.wait(min(5.0, max(0.5, until - time.time())))
                    fresh = [m for m in lobby_lines() if m["id"] > since]
                self.reply_json({"party": bool(PARTY["on"]), "room": "lobby",
                                 "messages": self.said_safely(fresh),
                                 "id": CHAT[-1]["id"] if CHAT else 0})
                return
            if not PARTY["on"]:
                # no party, no room: said plainly, so a client can put its box away
                self.reply_json({"party": False, "messages": [], "id": 0})
                return
            self.join_party()
            until = time.time() + wait
            while wait and not [m for m in CHAT if m["id"] > since
                                and self.addressed_to_me(m.get("to", "all"))] \
                    and time.time() < until:
                CHAT_RUNG.wait(min(5.0, max(0.5, until - time.time())))
            mine = [m for m in CHAT if m.get("room", "party") == "party"
                    and self.addressed_to_me(m.get("to", "all"))]
            fresh = [m for m in mine if m["id"] > since]
            # a reader arriving in the middle is given the last of it rather than
            # nothing at all
            if not since and not fresh:
                fresh = mine[-20:]
            self.reply_json({"party": True, "messages": self.said_safely(fresh),
                             "id": CHAT[-1]["id"] if CHAT else 0})
            return
        if path == "/notice":
            # A line the owner wants the screens in the house to see. Held in memory
            # and only for a while: this is a word in passing, not a message anybody
            # should find again tomorrow.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
                wait = max(0, min(int((args.get("wait") or ["0"])[0]), 55))
            except ValueError:
                since, wait = 0, 0

            def whats_said():
                words = NOTICE.get("text") or ""
                if words and time.time() > NOTICE.get("until", 0):
                    words = ""
                return words

            # Held open until there is something to say. A screen asks with the id it
            # already has and the answer comes the moment somebody writes - which is
            # what "push" means over an ordinary connection, and needs nothing on the
            # television but a longer read timeout.
            until = time.time() + wait
            while (wait and NOTICE.get("id", 0) <= since
                   and self.addressed_to_me(NOTICE.get("to"))
                   and time.time() < until):
                NOTICE_RUNG.wait(min(5.0, max(0.5, until - time.time())))
                if NOTICE.get("id", 0) > since:
                    break
            mine = self.addressed_to_me(NOTICE.get("to"))
            said = whats_said() if mine else ""
            self.reply_json({"id": NOTICE.get("id", 0), "text": said,
                             # what to open, for a screen this was addressed to
                             "play": (NOTICE.get("play") or "") if mine else "",
                             "at": int(NOTICE.get("at") or 0) if mine else 0})
            return
        if path == "/wiring":
            # Everything the drawing on the settings page shows, in one answer. It
            # asked three times over, every few seconds; on a machine at the end of a
            # slow link that is three round trips for one picture, and the page felt
            # like treacle for want of a single endpoint.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            import pd_follow
            import pd_torrents
            now = time.time()
            rows = WATCHING.snapshot()
            # which side the copying is on, so the line and the ceiling shown are the
            # ones that decide it: the network here and the line to the world are two
            # different pipes and only one of them is holding a copy back
            copy_side = next((Handler.side_of(r.get("address")) for r in rows
                              if r.get("how") == "syncing"), "home")
            self.reply_json({
                "name": self.server_name(),
                "machine": dict(pd_machine.state(PORT, LAN_IP),
                                server=self.build_version(),
                                # its own address on this network, for the drawing
                                lan=("http://%s:%d" % (LAN_IP, PORT)) if LAN_IP else "",
                                # this machine's own way in from outside, so the
                                # drawing can name both doors rather than the
                                # cache's alone
                                outside=(("http://%s:%d" % (wan_ip(), PORT))
                                         if wan_ip() else "")),
                "standby": dict(self.standby_now(), busy=self.copy_is_busy()),
                # and the machine this one follows, for a drawing made on the
                # cache: the other half of the pair is the main server, not a cache
                # of its own that it does not have - with what that machine is
                # feeding, so the line from it to a screen can be drawn here too
                "follows": dict(self.house_doors(), busy=self.house_is_busy()),
                "state": dict(pd_follow.STATE),
                "clients": [dict(row, ago=int(now - row["when"]))
                            for row in sorted(self.SEEN.values(),
                                              key=lambda r: -r["when"])
                            if now - row["when"] < 86400],
                # films coming in from a torrent pack, the one downloading first: the
                # machine is busy even when nothing is going out
                "downloads": [{k: d.get(k) for k in ("title", "year", "state", "progress",
                                                     "mbit", "eta", "who")}
                              for d in sorted(pd_torrents.downloads(), key=lambda d: (
                                  d.get("state") != "downloading", d.get("when") or 0))
                              if d.get("state") in ("queued", "downloading")],
                "live": [r for r in rows if r.get("how") != "syncing"],
                "syncing": sum(1 for r in rows if r.get("how") == "syncing"),
                # and what the cacheing is taking off this machine. Only the number of
                # them was sent, so what a machine was actually shifting left the
                # copying out - which on a night of syncing is most of it.
                "syncingMbit": round(sum(float(r.get("mbit") or 0) for r in rows
                                         if r.get("how") == "syncing"), 1),
                # what the line has been seen to carry, and what a copy is allowed
                # against it this second. Both are worked out rather than set, so
                # there is nowhere else to read them.
                # Per side, because they are two lines: the network here and the one
                # to the world. Shown for the side the copying is actually on, which is
                # the number that decides anything.
                "linkMbit": round((Handler.LINK.get(copy_side) or {}).get("seen", 0), 1),
                "linkSide": copy_side,
                "syncGivingWay": bool(Handler.a_film_is_struggling(
                    [r for r in rows if Handler.side_of(r.get("address")) == copy_side],
                    copy_side)),
                "syncCeiling": round(Handler.sync_ceiling(
                    local().lib.config(),
                    sum(float(r.get("mbit") or 0) for r in rows
                        if r.get("how") != "syncing"
                        and Handler.side_of(r.get("address")) == copy_side),
                    sum(float(r.get("mbit") or 0) for r in rows
                        if r.get("how") == "syncing"
                        and Handler.side_of(r.get("address")) == copy_side),
                    copy_side), 1),
            })
            return
        if path == "/skins":
            # every look a screen may wear, and which one this viewer picked. The
            # colours live on the server so that adding one is an entry in a file
            # rather than an edit to three clients in two languages.
            import pd_skins
            stored = read_settings() or {}
            mine = self.viewer_settings(stored)
            put_on, why = self.imposed_skin()
            self.reply_json({"skins": pd_skins.all_of(),
                             "chosen": pd_skins.known(mine.get("skin")),
                             "imposed": put_on, "why": why})
            return
        if path == "/where":
            # Both ways in to this machine, and the name it answers to.
            #
            # A screen that only knows the address it happened to be opened with has
            # one way in: a page opened at home cannot reach the main server from a train,
            # and one opened from away goes out to the router and back in to reach a
            # machine three feet from it. Neither is a secret - anybody who can ask
            # this question already has one of the two - and knowing both is what
            # lets a client keep working when one of them stops.
            self.reply_json({
                "lan": (("http://%s:%d" % (LAN_IP, PORT))
                        if LAN_IP and self.may_have_the_lan() else ""),
                "outside": ("http://%s:%d" % (wan_ip(), PORT)) if wan_ip() else "",
                "name": self.server_name(),
                # the same machine under two addresses answers with one id, which is
                # how a list of servers keeps from showing it twice
                "id": self.machine_id(),
                # and what it can encode with, if anything. A machine with no card
                # and no ffmpeg was still being labelled "transcode (NVENC)" by
                # whatever was playing from it, which is a sentence about somebody
                # else's computer.
                "engine": self.engine_name(),
                # And the machine this one follows, both of its ways in. Whichever of
                # the two machines somebody adds, they get all four addresses: this
                # one answers for itself and repeats what the main server told it while the
                # house could still be asked.
                "follows": self.house_doors(),
            })
            return
        if path == "/copying":
            # What the machine that keeps copies is fetching this minute, if
            # anything. Anybody may ask: it is one line about this house's own second
            # machine, and a viewer watching the film being fetched is the person it
            # matters to.
            row = next((r for r in WATCHING.snapshot()
                        if r.get("how") == "syncing"), None)
            if not row:
                self.reply_json({"key": "", "title": "", "at": 0.0})
                return
            self.reply_json({"key": str(row.get("key") or ""),
                             "title": str(row.get("title") or ""),
                             "at": float(row.get("position") or 0.0)})
            return
        if path == "/mood":
            # What this machine should be dressed as. Two things change it and
            # neither is a preference: a server that only holds copies, after dark,
            # is not the one the main server usually watches - and a machine whose card has
            # been handed to a game is not serving films at all. Anyone may ask; it
            # decides nothing, it only says what is already true.
            self.reply_json(dict(self.mood_now(),
                                 backdrop=self.backdrop_now()))
            return
        if path == "/build":
            # what this server is, for a page that wants to say which build drew it -
            # and whether the asker is in the same house, which is who may say
            # anything about it
            self.reply_json({"version": self.build_version(),
                             "lan": self.in_the_house()})
            return
        if path == "/server":
            # The installer, from this machine rather than from the site. Somebody on
            # this network who wants their own server should not have to go through
            # the beta gate to get the same file that is sitting here.
            self.send_installer()
            return
        if path == "/update":
            # What build the site is carrying, against the one running. Anybody
            # holding a key may ask: it is a version number, and a screen that cannot
            # ask shows nothing at all where the machines should be - which is what a
            # viewer away from the main server saw, an empty box rather than two servers.
            #
            # Replacing the program is a different matter and stays where it was: the
            # owner, from the same house as the machine. "lan" below says which of the
            # two the asker is, so a screen that may not press knows not to offer.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_update
            stored = self.settings_file()
            said = pd_update.state(
                self.build_version(), stored.get("betaKey", ""),
                force=(args.get("force") or [""])[0] in ("1", "true", "yes"),
                folders=self.installer_folders())
            said["lan"] = self.in_the_house()
            self.reply_json(said)
            return
        if path == "/copied":
            # The cacheing, asked the way the watching is asked: this week, this
            # month, altogether. Two different questions about the same machine, and
            # they read best side by side.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_traffic
            self.reply_json(pd_traffic.copied_stats())
            return
        if path == "/traffic":
            # How much has left this machine, by month and by year, kept apart by
            # what it left as: a film somebody watched, or a copy going to the other
            # server. Added together they answer nothing.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_traffic
            self.reply_json(pd_traffic.read())
            return
        if path == "/stats":
            # What the main server has watched: this week, this month, and since the
            # library was built. The owner's, like the log it is counted from.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.reply_json(self.watch_stats())
            return
        if path == "/watchlog":
            # who watched what, and when. The owner's: it is everybody's viewing
            # history, which is not a guest's business.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            # the newest few hundred by default - a page nobody scrolls to the end
            # of - and the whole book when it is asked for
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                want = int((args.get("limit") or ["400"])[0])
            except ValueError:
                want = 400
            want = max(1, min(want, 20000))
            con = local().lib.db()
            try:
                held = con.execute("SELECT COUNT(*) c FROM watchlog").fetchone()["c"]
                rows = con.execute("""SELECT * FROM watchlog
                                      ORDER BY started DESC LIMIT ?""",
                                   (want,)).fetchall()
            finally:
                con.close()
            names = {r["token"]: r["name"] for r in INVITES.load()}
            out = []
            for r in rows:
                out.append({
                    "who": (self.owner_name() if r["who"] == "me"
                            else names.get(r["who"], "a guest")),
                    "title": r["title"], "device": r["device"], "client": r["client"],
                    "started": r["started"], "updated": r["updated"],
                    "seconds": max(0, int(r["updated"] or 0) - int(r["started"] or 0)),
                    "position": r["position"], "duration": r["duration"],
                    "casual": bool(r["casual"]), "key": r["key"],
                    # which build was watching, for rows recorded since that was kept
                    "app": (r["app"] if "app" in r.keys() else "") or "",
                })
            # how many there are altogether, so a page showing the newest few can
            # say what it is not showing
            self.reply_json({"watched": out, "held": held})
            return
        if path == "/changes":
            # What has been added, newest first. Kept beside the code in changes.json
            # so it is written when the thing is written, rather than remembered
            # afterwards by whoever is asked.
            try:
                with open(os.path.join(CODE, "changes.json"), encoding="utf-8") as f:
                    entries = json.loads(f.read())
            except Exception:
                entries = []
            self.reply_json({"changes": entries,
                             "app": self.app_version()})
            return
        if path == "/feedback":
            rows = self.reports()
            # what has come in since the owner last opened the page. Their own writing
            # does not count: nobody needs telling about what they just wrote.
            since = self.settings_file().get("reportsSeen", 0)
            unseen = sum(1 for r in rows
                         if r.get("when", 0) > since and r.get("who") != "you"
                         and not r.get("done"))
            self.reply_json({"reports": rows, "owner": self.role == "owner",
                             # what has been cleared away, so a mistake can be undone
                             # without going to look in a file
                             "filed": self.filed(),
                             # and how a backlog being handed over is getting on
                             "sending": dict(SENDING),
                             "unseen": unseen})
            return
        if path == "/manifest.webmanifest":
            # Written per person, not served from disk. A home-screen web app on iOS
            # has its own cookie jar, so the invitation cookie does not follow it in -
            # the address it starts at has to carry the invitation itself, or the icon
            # opens on a locked server.
            token = self.bearer()
            body = json.dumps({
                "name": "Palladium", "short_name": "Palladium",
                "description": "A private film library.",
                "start_url": ("/?t=" + token) if token else "/",
                "scope": "/",
                "display": "standalone",
                "orientation": "any",
                "background_color": "#0b0d10",
                "theme_color": "#0b0d10",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {"src": "/icon-1024.png", "sizes": "1024x1024",
                     "type": "image/png", "purpose": "any maskable"},
                ],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            # For whatever is watching the process: a container's healthcheck, a
            # reverse proxy, a monitor. It says the server is answering and nothing
            # else - not the machine's name, not who is watching, not the library.
            self.reply_json({"ok": True})
            return
        if path in ("/app", "/app/version"):
            # version.json is written by publish_apk.py straight from the APK, so the
            # page, the update check and the file itself can never disagree
            info = {"versionName": "?", "versionCode": 0, "sizeMb": 0, "built": ""}
            # beside the program, where the installer puts it, or with this server's
            # own things, where a fetched one lands
            for where in (STATIC, ROOT):
                try:
                    with open(os.path.join(where, "version.json"), encoding="utf-8") as f:
                        info.update(json.loads(f.read()))
                    break
                except Exception:
                    continue
            # and whether there is actually a file to go with the number
            # what is on the disk, and whether this server hands it out
            info["have"] = bool(self.app_beside_us())
            info["off"] = bool((read_settings() or {}).get("appOff"))
            info["here"] = info["have"] and not info["off"]
            info["getting"] = bool(Handler.APP_FETCH["busy"])
            info["from"] = Handler.APP_FETCH.get("where") or Handler.APP_FROM
            info["got"] = Handler.APP_FETCH.get("got") or 0
            info["size"] = Handler.APP_FETCH.get("size") or 0
            info["part"] = Handler.APP_FETCH.get("part") or 0.0
            if path == "/app/version":
                # The server's own release as well as the app's. They were one number
                # while both were built together and have since drifted - the browser
                # is the server's own client and was labelling itself with the phone's
                # number, which is a version it does not run.
                # What this server is actually running, which is the number a
                # machine that follows it compares itself against. It was read out
                # of the changelog beside the program - a file the installer
                # rewrites, and one that is missing entirely on a machine running
                # from source - so the answer was often empty, and a cache
                # comparing itself against nothing never updated at all.
                info["serverVersion"] = self.build_version()
                self.reply_json(info)
                return
            # the invitation this page was opened with travels on to the file, or the
            # button is refused the moment it is pressed from outside the main server
            token = self.bearer()
            info["carry"] = ("?t=" + token) if token else ""
            # A television has no browser: the way anything is installed on a Google TV
            # is the Downloader app, which wants an address typed with a remote. Hence
            # the short code - and the .apk on the end, so Downloader knows what it has.
            info["tv"] = ""
            if token:
                code = Invites.code_for(token)
                info["tv"] = (
                    "<p style='color:#93a0b0;font-size:13px;margin:22px auto 0;"
                    "max-width:420px;line-height:1.7;border-top:1px solid #1e242c;"
                    "padding-top:20px'>On a <b style='color:#e8ecf1'>Google TV</b> "
                    "there is no browser. Install <b style='color:#e8ecf1'>Downloader"
                    "</b> from the Play Store, allow it to install unknown apps, and "
                    "type this in the address box - no http, nothing on the end:"
                    "<br><span style='display:inline-block;margin-top:10px;"
                    "font:600 19px ui-monospace,monospace;color:#4a90f0;letter-spacing:"
                    "1px'>%s/i/%s</span></p>"
                    % (self.headers.get("Host") or LAN_IP, code))
            html = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>Palladium</title>"
                    "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                    "text-align:center;padding:11vh 20px;margin:0'>"
                    "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                    "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                    "margin:10px 0 4px'>PALLADIUM</h1>"
                    "<p style='color:#93a0b0;margin:0 0 4px'>Android app for phones, "
                    "tablets and Google TV</p>"
                    "<p style='color:#4a90f0;font-size:15px;margin:0 0 26px'>"
                    "version %(versionName)s <span style='color:#6b7683'>&middot; "
                    "%(sizeMb).1f MB &middot; built %(built)s</span></p>"
                    "<a href='/palladium.apk%(carry)s' style='display:inline-block;"
                    "background:#4a90f0;"
                    "color:#111;font-weight:600;padding:15px 34px;border-radius:9px;"
                    "text-decoration:none;font-size:17px'>Download and install</a>"
                    "<p style='color:#6b7683;font-size:13px;margin-top:28px;line-height:1.6;"
                    "max-width:420px;margin-left:auto;margin-right:auto'>"
                    "Installing over an existing copy updates it and keeps your settings. "
                    "Android asks permission to install from your browser the first time."
                    "</p>%(tv)s</body>") % info
            body = html.replace("#4a90f0", self.accent_now()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/palladium.apk":
            self.send_app(who=self.guest_name)
            return
        if path == "/invite.png":
            # a chat app fetching a preview has no token; this is a logo and nothing
            # else, so it is the one thing served to anybody who asks
            pic = os.path.join(STATIC, "invite.png")
            if not os.path.exists(pic):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(os.path.getsize(pic)))
            self.end_headers()
            with open(pic, "rb") as f:
                self.wfile.write(f.read())
            return
        if path == "/palladium.apk":
            apk = os.path.join(STATIC, "palladium.apk")
            if not os.path.exists(apk):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Length", str(os.path.getsize(apk)))
            self.send_header("Content-Disposition", "attachment; filename=palladium.apk")
            self.end_headers()
            with open(apk, "rb") as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        if path.startswith("/local/"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            # everyone keeps their own place in a film; a guest is known by the token
            # they arrived with, which is the only name the server can be sure of -
            # and an owner with a key of their own is known by that, on the
            # television at home as much as in a browser away from it
            local().who = self.viewer()
            # what this viewer has put aside from Continue watching
            local().aside = (self.viewer_settings(self.settings_file())
                             .get("deckAside") or {})
            # and the shelves being shuffled, so Continue watching can carry a row
            # for each of them: what is being carried on with, or what is next
            mine_now = self.viewer_settings(self.settings_file())
            local().shuffles = mine_now.get("shuffles") or {}
            local().shelf_names = {str(c.get("id")): str(c.get("name") or "")
                                   for c in (mine_now.get("collections") or [])
                                   if isinstance(c, dict) and c.get("id")}
            # where a shuffled playing got to, kept on the shelf's round
            local().shelf_note = self.shuffle_note
            local().shelf_forget = self.title_finished
            # and the next episode fetched from a pack while this one is still on
            local().half_way = self.keep_the_next_one_ready
            # and what the client calls itself, so the watch log can say which build
            # was watching - a fault from a three-week-old one is a different
            # conversation from a fault on today's
            local().app_now = self.app_name()
            sub = path[len("/local"):]
            # a watch log row records what kind of thing was watching, not only the
            # name it calls itself by - and this is the request it is written from
            if sub.startswith("/:/timeline"):
                kind = self.device_kind()
                if kind:
                    q["client"] = [kind]
            if sub == "/library/watchlist":
                # what this viewer marked. It is theirs, so it comes from their
                # settings and not from anything the caller can ask for.
                q["keys"] = [",".join(self.watchlist())]
                # and gathered into seasons for the same reason the shuffle is:
                # starring a season writes a mark on each of its episodes
                q["byseason"] = ["1"]
            if sub == "/library/favorites":
                # the same shelf drawing as the watchlist, over this viewer's favourites
                sub = "/library/watchlist"
                mine = self.viewer_settings(self.settings_file())
                q["keys"] = [",".join(str(k) for k in (mine.get("favorites") or []))]
                q["byseason"] = ["1"]
            if sub == "/library/collections":
                # The shelves themselves, drawn as things with posters so a client can
                # show a row of them the way it shows a row of films. The key says
                # which shelf rather than which title: nothing in the library has one
                # like it, so nothing can open it by mistake.
                rows = []
                con = local().lib.db()
                try:
                    for c in self.collections():
                        keys = self.collection_keys(con, c)
                        face = str(c.get("cover") or "")
                        if face not in keys:
                            face = next((k for k in keys if not str(k).startswith("o")), keys[0] if keys else "")
                        art = None
                        if face:
                            one = local().metadata_for(con, face, brief=True) or {}
                            art = one.get("thumb") or one.get("grandparentThumb")
                        rows.append({"ratingKey": "coll:" + str(c.get("id") or ""),
                                     "type": "collection",
                                     "title": str(c.get("name") or ""),
                                     "thumb": art, "childCount": len(keys),
                                     # where the round on this shelf was left, so the
                                     # button can say Resume rather than Play
                                     "resumeAt": self.shuffle_resume(c.get("id")),
                                     # the sort and filters it opens with
                                     "view": c.get("view") or {},
                                     "leafCount": len(keys)})
                finally:
                    con.close()
                self.reply_json({"MediaContainer": {"size": len(rows),
                                                    "Metadata": rows}})
                return
            if sub == "/library/collection":
                # what one shelf holds, in the order it reads: oldest first, because a
                # collection is usually a series of films
                want = (q.get("id") or [""])[0]
                want = want[5:] if want.startswith("coll:") else want
                shelf = next((c for c in self.collections()
                              if str(c.get("id")) == want), None)
                con = local().lib.db()
                try:
                    # the same list the shuffle draws from: a programme on a
                    # collection is its episodes, the ones here and the ones a pack
                    # can give, rather than one row standing for all of them
                    keys = self.shelf_pool(con, shelf) if shelf else []
                    # films oldest first, because a collection of them is usually a
                    # series; a programme's episodes in season and episode order after
                    # them, each programme whole. Asking every key for its year meant
                    # six hundred lookups through the packs to sort a list that is
                    # about to be folded into seasons anyway.
                    places, _ = local().episode_places(con, keys)
                    shows = {}

                    def in_order(key):
                        where = places.get(key)
                        if not where:
                            return (0, int((local().metadata_for(con, key, brief=True)
                                            or {}).get("year") or 0), 0, 0)
                        shows.setdefault(where[0], len(shows))
                        return (1, shows[where[0]], where[1], where[2])

                    keys.sort(key=in_order)
                finally:
                    con.close()
                sub = "/library/watchlist"
                q["keys"] = [",".join(keys)]
                # By season, now that what a collection holds is episodes. Grouping
                # was turned off when the keys were programmes, because it broke one
                # programme into "Season 28" and "Season 36" standing on their own;
                # over episodes it does the opposite, and folds six hundred of them
                # back into the seasons they belong to.
                q["byseason"] = ["1"]
            m = re.match(r"^/parts/(\d+)$", sub)
            if m:
                real = local().part(m.group(1))
                if not real:
                    self.send_error(404)
                    return
                # The file itself, which is the one way round a ceiling: a page
                # opened before the limit was set knows nothing about it and asks
                # for the original. A ceiling that only the client honours is not a
                # ceiling, so this is refused here and the client falls back to the
                # transcoder, which is held to the limit like everything else.
                too_big = self.too_big_to_send(m.group(1))
                if too_big:
                    self.refused(path, too_big)
                    self.send_error(403, too_big)
                    return
                # a direct play is the cheapest thing this server does, and still the
                # heaviest thing on the line, so it belongs in the list too.
                # The machine that keeps copies reads files the same way, and read as
                # a viewing it says somebody is watching a film in another room.
                # It calls itself a follower; this asked whether it called itself a
                # cache, so every file it pulled was written down as somebody watching
                # - at the speed of a copy, and never giving way to a real viewer.
                said = self.app_name().lower()
                its_key = INVITES.check(self.bearer(), self.app_name())
                taking = (said.startswith(("cache", "follower"))
                          or (its_key is not None
                              and Handler.role_of(its_key) == "cache"))
                # and it is named as the machine it is, not as an address in the
                # house that appears to be watching something
                who = self.watcher()
                if taking:
                    who = (Handler.STANDBY.get("name")
                           or Handler.host_of(Handler.STANDBY.get("where") or "")
                           or "another server")
                # why the cache wanted this one, worked out when the queue was
                # made and kept against the key until the file goes out
                fileKey = local().key_for_file(m.group(1))
                why, asked = Handler.WHY_BY_KEY.get(str(fileKey or ""), ("", ""))
                sid = WATCHING.start(who, local().title_for_file(m.group(1)),
                                     local().facts_for_file(m.group(1)),
                                     "syncing" if taking else "direct play",
                                     self.client_address[0],
                                     fileKey,
                                     self.app_name(), self.device_kind(),
                                     why=why, asked_for=self.name_of(asked),
                                     path=m.group(1))
                try:
                    self.send_file_ranged(real, sid)
                finally:
                    WATCHING.stop(sid)
                return
            status, ctype, body = local().handle(sub, q)
            # the title a progress report is about, and nothing for every other route
            # through here: read for all of them, it was a name that did not exist yet
            watched_key = ""
            # a subtitle that carried an episode to the end is one that fits - and
            # "the end" is where the credits start, not the last second of them
            if sub == "/:/timeline":
                watched_key = (q.get("ratingKey") or [""])[0]
                using = (q.get("sub") or [""])[0]
                try:
                    at = float((q.get("time") or ["0"])[0]) / 1000.0
                except (TypeError, ValueError):
                    at = 0.0
                if watched_key:
                    # when this subtitle came on. A different one from a moment ago
                    # starts the clock again, which is what stops a track chosen in
                    # the last minute from being credited with the whole film.
                    was = SUB_SINCE.get(watched_key) or self.subtitle_since(watched_key)
                    if not was or was[0] != using:
                        # A subtitle swapped for another during a watching we were
                        # following is a choice, and its position counts. The first
                        # sighting of one - a resume, or the first request after a
                        # restart - says nothing about when it came on, and taking the
                        # position it happened to arrive at as the moment it was chosen
                        # is what refused a whole episode watched with subtitles on.
                        sure = bool(was)
                        SUB_SINCE[watched_key] = (using, at if sure else None)
                        self.remember_since(watched_key, using, at, sure)
                        if len(SUB_SINCE) > 40:
                            for old in list(SUB_SINCE)[:20]:
                                SUB_SINCE.pop(old, None)
                    else:
                        SUB_SINCE[watched_key] = was
                if watched_key and local().seen_through(watched_key):
                    known = SUB_SINCE.get(watched_key) or self.subtitle_since(watched_key)
                    told = self.promote_subtitle_choice(
                        watched_key, using,
                        since=known[1] if known and known[0] == using else None)
                    # and the answer carries the news
                    if told and ctype.startswith("application/json"):
                        try:
                            doc = json.loads(body.decode("utf-8"))
                            doc.setdefault("MediaContainer", {})["subsVerified"] = told
                            body = json.dumps(doc).encode("utf-8")
                        except Exception:
                            pass
            # What the sound of this file needs now, so a player can be corrected
            # without being restarted. The gain used to be settled when the film was
            # opened, so a file measured while it was playing stayed uncorrected to
            # the end and a player that had never been told could not be told.
            if watched_key and ctype.startswith("application/json"):
                try:
                    src = local().file_for(watched_key, 0) or {}
                    if src.get("path"):
                        doc = json.loads(body.decode("utf-8"))
                        doc.setdefault("MediaContainer", {})["gainDb"] = volume_gain(
                            src.get("id"), src.get("path"), src.get("size") or 0,
                            playing=True)
                        # and how loud it was found to be, for the line along the top:
                        # a file measured while it was playing had the number on the
                        # server and nothing on the screen
                        known = read_loudness().get(str(src.get("id")))
                        if known and known.get("lufs") is not None:
                            doc["MediaContainer"]["lufs"] = float(known["lufs"])
                        body = json.dumps(doc).encode("utf-8")
                except Exception:
                    pass
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if ctype.startswith("application")
                             else "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/library/scan":
            # An address somebody can open to start a scan - and anything that opens
            # an address can open it again. Without this it started another scan on
            # every visit, and several walks of the same folders writing to one index
            # is where "database is locked" comes from.
            lib = local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"started": False, "why": "a scan is already running",
                                 "scan": lib.scan_state})
                return
            import threading as _t
            _t.Thread(target=lambda: lib.scan(), daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/advanced":
            self.reply_json({"groups": self.settled_numbers()})
            return

        if path == "/library/status":
            said = local().lib.stats()
            try:
                import pd_torrents
                said["offered"] = len(pd_torrents.offered())
            except Exception:
                said["offered"] = 0
            self.reply_json(said)
            return
        if path == "/tizen.zip":
            # The source of a Samsung TV app, with this server's address written into
            # it. Not a signed package: Samsung will not install one that has not been
            # signed with a certificate belonging to a person, so the last two steps
            # are Tizen Studio's and cannot be done here.
            q = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                 if "?" in self.path else {})
            token = (q.get("t") or [""])[0]
            where = "http://%s:%d" % (LAN_IP, PORT)
            if token:
                where += "/s/" + token
            import io as _io
            import zipfile
            out = _io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for name in ("config.xml", "index.html"):
                    with open(os.path.join(STATIC, "tizen", name),
                              encoding="utf-8") as f:
                        z.writestr(name, f.read().replace("__SERVER__", where))
                with open(os.path.join(STATIC, "tizen", "icon.png"), "rb") as f:
                    z.writestr("icon.png", f.read())
            body = out.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             "attachment; filename=palladium-tizen.zip")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/auth/state":
            self.reply_json({
                "password": self.password_set(),
                "owner": self.role == "owner",
                "local": self.client_address[0] in ("127.0.0.1", "::1"),
                "proxied": self.through_a_proxy(),
            })
            return
        if path == "/setup/state":
            # What a new install is still waiting for. One answer rather than four
            # requests, because the page asks for it every second while it works.
            cfg = local().lib.config()
            folders = ((cfg.get("movies") or []) + (cfg.get("tv") or [])
                       + (cfg.get("mixed") or []))
            stats = local().lib.stats()
            stored = read_settings() or {}
            self.reply_json({
                "folders": folders,
                "tmdb": bool((cfg.get("tmdb_key") or "").strip()),
                "ffmpeg": ffmpeg_now(),
                # the cache on this disk, in use or let go of: a row that vanished
                # when it was fetched left nowhere to say either
                "ffmpegHave": ffmpeg_in_home(),
                "ffmpegFrom": FFMPEG_FROM.get(os.name) or "",
                "engine": engine_name(),
                "fetching": dict(FETCHING),
                "films": stats.get("movies", 0),
                "episodes": stats.get("episodes", 0),
                "scanning": bool(local().lib.scan_state.get("running")),
                "done": bool(stored.get("setupDone")),
                "password": self.password_set(),
                "packaged": PACKAGED,
            })
            return
        if path == "/library/config":
            # with the name this machine answers to, so a settings page opened
            # against it can say whose settings are on the screen
            self.reply_json(dict(local().lib.config(),
                                 serverName=self.server_name()))
            return
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        # Safari's route: a playlist and segments rather than one long response
        m = re.match(r"^/gpu/hls/(hls\d+)/([A-Za-z0-9_.-]+)$", path)
        if m:
            self.gpu_hls_file(m.group(1), m.group(2))
            return
        if path == "/gpu/hls":
            self.gpu_hls(q)
            return
        if path == "/torrents/active":
            # what is coming in now, for the line in the menu: everyone's for the owner,
            # a guest's own otherwise
            import pd_torrents
            got = pd_torrents.active(None if self.role == "owner" else (self.bearer() or "me"))
            self.reply_json({"MediaContainer": {"size": len(got), "Metadata": got}})
            return
        if path == "/proxy":
            # how this server is reached from outside: the port forwarded in the
            # router, which is the default, or Caddy with a name and a certificate
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_proxy
            self.reply_json(pd_proxy.state())
            return
        if path in ("/torrents", "/torrents/log"):
            # films offered from torrent packs, and every download somebody asked for
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json(pd_torrents.status() if path == "/torrents"
                            else {"downloads": pd_torrents.downloads()})
            return
        if path == "/follow/torrents":
            # the packs offered here, for a cache to offer the same films while this
            # machine is off; the torrent itself only for a pack it does not hold yet
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            if q.get("ping"):
                self.reply_json({"ok": True})
            else:
                self.reply_json(pd_torrents.mirror(
                    set(h for h in q.get("have", [""])[0].split(",") if h)))
            return
        if path == "/follow/source":
            # what a file is and the part it is read by, for a cache asked to play a
            # title it holds no copy of: it encodes with its own encoder, off this file
            invite = INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            found = local().file_for(q.get("key", [""])[0], int(q.get("mi", ["0"])[0] or 0))
            if not found or not found.get("part"):
                self.send_error(404, "no such file here")
                return
            self.reply_json({k: v for k, v in found.items() if k != "file"})
            return
        if path in ("/gpu/begins", "/gpu/stream", "/gpu/subs") and q.get("src", [""])[0] == "local":
            # made on the connected computer when this one is set to, while it answers
            if path != "/gpu/subs" and self.encode_elsewhere(q):
                return
            # a title this copy holds no file for: its own encoder reads the main server's
            # file. Subtitles, which mean reading the whole file, are cut by the main server.
            if (path == "/gpu/subs" or not self.house_source(q)) and self.send_to_the_house(q):
                return
        if path == "/gpu/begins":
            # where an app's encode will start, and whether its picture goes through
            self.reply_json(self.encode_begins(q))
            return
        if path == "/gpu/stream":
            self.gpu_stream(q)
            return
        if path == "/gpu/subs":
            # a whole film's subtitles extract in about a second, so the response is
            # buffered and sent with a length: a <track> element will not render a
            # stream that never declares an end
            index = int(q.get("index", ["0"])[0])
            # a negative number is a file beside the video rather than a track inside
            # it: ffmpeg has nothing to extract, and would hand back an empty track
            if index < 0 and q.get("src", [""])[0] == "local":
                # file_for describes the video; the sidecar reader wants its path
                found = local().file_for(q.get("key", [""])[0],
                                         int(q.get("mi", ["0"])[0])) or {}
                self.serve_sidecar(found.get("file"), -index - 1,
                                   q.get("shift", ["0"])[0],
                                   q.get("offset", ["0"])[0])
                return
            try:
                offset = float(q.get("offset", ["0"])[0])
                video = (local().file_for(q.get("key", [""])[0],
                                          int(q.get("mi", ["0"])[0])) or {}).get("file", "")
                partial = False
                if q.get("src", [""])[0] == "local" and video:
                    ready = cached_subtitle(sub_cache_path(video, index))
                    if ready is not None:
                        said = ready
                    else:
                        # Nobody has lifted this track out yet, and doing the whole of
                        # it means reading the film end to end. Take the first twenty
                        # minutes, which stops as soon as ffmpeg is past that mark, and
                        # set the whole reading going behind it. There is text on
                        # screen in a few seconds, and the rest arrives before it is
                        # needed.
                        head = head_subtitle(video, index)
                        start_pull(video, index)
                        if head:
                            said, partial = head, True
                        else:
                            said = pull_subtitle(video, index)
                elif video:
                    # a track inside a file the cache has not been asked for yet
                    proc = engine().subtitles({"file": video}, index, offset=offset)
                    out, _ = proc.communicate(timeout=300)
                    said = (out or b"").decode("utf-8", "replace")
                    offset = 0        # that one was asked for from the offset already
                else:
                    self.send_error(404, "no such file")
                    return
                # The whole track is in hand, in the film's own clock: correct it there,
                # then cut it to where this stream begins. One extraction answers for
                # every seek in the film.
                # a track written to roll up the screen says the next line before
                # it is spoken; flattened before anything else is done to it
                if said:
                    said = flatten_rollup(said)
                mended = self.sub_fit(self.fit_name(video, index))
                if mended and said:
                    said = mend_vtt(said, mended)
                shift = q.get("shift", ["0"])[0]
                if said and shift not in ("", "0", "0.0"):
                    said = shift_vtt(said, shift)
                if said and offset > 0:
                    said = cut_vtt(said, offset)
                body = said.encode("utf-8")
            except Exception as e:
                self.send_error(500, str(e)[:200])
                return
            if not body.strip():
                # better a clear refusal than an empty track: a player given nothing
                # to draw reports a broken file and stops
                self.send_error(404, "no subtitles on that track")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/vtt; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # how much of the film this covers, so a player knows to ask again when it
            # gets near the end of it rather than running out of subtitles
            if partial:
                self.send_header("X-Palladium-Subs", "partial")
                self.send_header("X-Palladium-Subs-Until", str(SUB_HEAD))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/gpu/status":
            body = json.dumps(engine().status()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0] == "/config":
            # A new install has no config.json, and this is the first thing any page
            # asks for: answering with a 500 stopped the client before it could draw
            # anything at all, including the page that sets the server up.
            try:
                with open(os.path.join(ROOT, "config.json"), "rb") as f:
                    cfg = json.loads(f.read().decode("utf-8"))
            except (OSError, ValueError):
                cfg = {}
            import socket as _s
            cfg["serverName"] = _s.gethostname()
            ident = self.server_id()
            if self.role == "guest":
                # a guest browses this server's own library and is told so; nothing
                # about how it is configured crosses the door
                cfg = {"guest": True,
                       "name": self.guest_name, "serverName": cfg["serverName"],
                       # the invitation this page arrived with. It is held as a
                       # cookie for this address and nowhere else, and a cookie is
                       # no use against the machine keeping copies of this library -
                       # which is where this page goes when this server is off.
                       "key": self.bearer() or "",
                       "targets": cfg.get("targets", {})}
            # which machine this is, whatever address was used to ask. A client that
            # holds this server twice - once on the network, once from outside - can
            # see that the two are one and ask only once.
            cfg["serverId"] = ident
            # This machine keeps copies of another one. A viewer who lands here
            # should be told so, and told what of theirs is here: a short shelf with
            # no explanation reads as a library that has lost half its films.
            import pd_follow
            following = pd_follow.settings(local().lib.config())
            if following.get("on") and following.get("master"):
                borrowed = (read_settings() or {}).get("copyOf") or {}
                if self.role == "guest":
                    mine = next((r for r in INVITES.load()
                                 if r.get("token") == self.bearer()), {})
                else:
                    mine = borrowed.get("owner") or {}
                held = local().lib.stats()
                cfg["copyOf"] = {
                    "name": borrowed.get("master") or "the other server",
                    "deck": bool(mine.get("cacheDeck")),
                    "list": bool(mine.get("cacheList")),
                    "casual": bool(mine.get("cacheCasual")),
                    # everything on this disk plays for anybody who may watch here:
                    # it was copied for one person and it is on the shelf for all
                    "films": int(held.get("movies") or 0),
                    "episodes": int(held.get("episodes") or 0),
                }
            # the client polls this: a page whose build differs from the server's is
            # running stale code and says so instead of silently misbehaving
            cfg["build"] = int(os.path.getmtime(os.path.join(STATIC, "app.js")))
            cfg["lan"] = "http://%s:%d" % (LAN_IP, PORT)   # what the Chromecast can reach
            body = json.dumps(cfg).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    #: set when a guest's cookie should be given a fresh year on the way out
    renew = ""

    def end_headers(self):
        if self.renew:
            self.send_header("Set-Cookie", self.cookie_for(self.renew))
            self.renew = ""
        self.send_header("Cache-Control", "no-store")
        # a friend's page lives on a different origin; the token in the URL is what
        # authorises the read, so the origin itself need not be restricted
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Length, Content-Range, X-Palladium-Engine, "
                         "X-Palladium-Subs, X-Palladium-Subs-Until")
        super().end_headers()

    def do_OPTIONS(self):
        """Preflight, for the few requests that carry a header of our own."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "X-Palladium-Token, Content-Type, Range, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    #: How many connections may be waiting to be accepted. Python's own answer is
    #: five, which was plenty while a film was one long connection: it is one stretch
    #: at a time from one screen. Reading a film off several machines asks for every
    #: two megabytes on a connection of its own, so a 4K film is several a second from
    #: each screen, and a copy taking files and a settings page asking how things are
    #: going are on top of that. Past five, the rest are refused - and what the player
    #: sees is a machine that will not answer, waits five seconds, and drops it. The
    #: film stopped for that long, now and then, on a server that was perfectly well.
    request_queue_size = 128


def flush_invites():
    """Put the invite counters on disk every half minute.

    They were written only on a clean shutdown, and the server is usually killed, so
    "never used" could mean "used, and the count died with the process".
    """
    while True:
        time.sleep(30)
        try:
            INVITES.flush()
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
        con = local().lib.db()
        try:
            for r in con.execute("SELECT DISTINCT key FROM progress"):
                live.add(str(r["key"]))
        finally:
            con.close()
    except Exception:
        return None            # unreadable: keep everything rather than guess
    return live


def prune_subtitle_notes():
    """Drop the timing and the chosen file for titles nobody is part way through.

    Kept while a film is resumable, and kept for good once a subtitle has been
    verified for it. Everything else is a note about something finished with, and
    the file should not grow a line per title watched once.
    """
    live = titles_in_use()
    if live is None:
        return 0
    proved = set((read_settings() or {}).get("subsOk") or {})
    def wanted(name):
        # "le11804|Something.srt" and "l608" both name a title with an l in front
        title = re.sub(r"^[lp]", "", str(name).split("|", 1)[0])
        # A verified subtitle is settled: somebody said so, or watched far enough
        # for it to have been read. Its timing is worth keeping for good, whether
        # or not anybody is part way through the film today.
        return title in live or title in proved
    stored = read_settings() or {}
    gone = 0
    held = stored.get("subShift") or {}
    for name in [n for n in held if not wanted(n)]:
        held.pop(name, None)
        gone += 1
    stored["subShift"] = held
    for mine in [stored] + list((stored.get("users") or {}).values()):
        picks = mine.get("subsPick") or {}
        for name in [n for n in picks if not wanted(n)]:
            picks.pop(name, None)
            gone += 1
        if picks or "subsPick" in mine:
            mine["subsPick"] = picks
    if gone:
        write_settings(stored)
    return gone


def sweep_subtitle_notes():
    """Once a day: forget the timing and the choice for films nobody is watching.

    Kept while a film is resumable or on a list, and kept for good once a subtitle
    has been verified for it. Everything else is a note about something watched and
    finished with, and the settings file should not grow a line per title.
    """
    while True:
        time.sleep(86400)
        try:
            prune_subtitle_notes()
        except Exception:
            pass


def sweep_segments():
    """Tidy up after HLS sessions nobody is watching any more, once a minute."""
    while True:
        time.sleep(60)
        try:
            engine().sweep_hls()
        except Exception:
            pass


def rescale_subtitle_sizes():
    """Move every size already chosen onto the one meaning of 100%.

    Runs once: the file records the base it was written for, and a file already on
    this base is left alone.
    """
    stored = read_settings()
    if not isinstance(stored, dict) or not stored:
        return
    if abs(float(stored.get("subSizeBase") or 0) - SUB_BASE) < 1e-9:
        return

    def move(look, device):
        was = SUB_BASE_WAS.get(device)
        if not was or not isinstance(look, dict) or "size" not in look:
            return
        try:
            look["size"] = round(float(look["size"]) * was / SUB_BASE, 3)
        except (TypeError, ValueError):
            pass

    def walk(mine):
        for device, look in (mine.get("subtitles") or {}).items():
            move(look, device)
        for name, look in (mine.get("perTitle") or {}).items():
            move(look, str(name).split("|", 1)[0])

    walk(stored)
    for one in (stored.get("users") or {}).values():
        if isinstance(one, dict):
            walk(one)
    stored["subSizeBase"] = SUB_BASE
    write_settings(stored)


#: Text tracks worth lifting out. A picture subtitle has no text in it and is burned
#: into the stream instead.
TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")


def quiet_now():
    """True when nothing is playing and no encode is running.

    Reading a film end to end competes with reading it for playback, so this work
    waits its turn. It has no deadline: the point is that it happens before somebody
    asks, not that it happens quickly.
    """
    try:
        if NOW:
            return False
    except Exception:
        pass
    try:
        return not (engine().status().get("streams") or [])
    except Exception:
        return True


def warm_subtitles():
    """Pull every embedded text track into the cache, slowly, when the machine is idle.

    One at a time, newest films first - what somebody just added is what they are
    about to watch. Failures are passed over: a file that cannot be read now will be
    tried again next time the server starts.
    """
    import json as _json
    time.sleep(90)                      # let the server settle before touching a disk
    while True:
        try:
            if not quiet_now():
                time.sleep(60)
                continue
            con = local().lib.db()
            try:
                rows = con.execute(
                    "SELECT path, streams FROM file ORDER BY COALESCE(ctime, mtime) DESC"
                ).fetchall()
            finally:
                con.close()
            work = []
            for row in rows:
                try:
                    tracks = _json.loads(row["streams"] or "[]")
                except Exception:
                    continue
                for track in tracks:
                    if (track.get("codec") or "").lower() not in TEXT_CODECS:
                        continue
                    where = sub_cache_path(row["path"], track.get("index"))
                    if not os.path.exists(where) and os.path.exists(row["path"]):
                        work.append((row["path"], track.get("index")))
            for video, index in work:
                if not quiet_now():
                    break               # somebody started watching; the rest can wait
                try:
                    pull_subtitle(video, index)
                except Exception:
                    pass
                time.sleep(2)           # a gap between films, so the disk is not ours
            time.sleep(600 if not work else 30)
        except Exception:
            time.sleep(300)


def already_serving():
    """Whether a Palladium is already answering on this port.

    Windows lets a second program bind a port another one holds, so two servers can
    run with only one of them reachable - and the other goes on scanning, copying and
    writing to the same files. One is asked before the second starts.
    """
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/config" % PORT,
                                     headers={"X-Palladium-App": "server"})
        with urllib.request.urlopen(req, timeout=2) as answer:
            return bool(json.loads(answer.read().decode("utf-8", "replace")))
    except Exception:
        return False


def main():
    # One at a time. An installer that starts the program while the Startup shortcut
    # is bringing it up leaves two, and Windows lets the second bind a port the first
    # already holds - so it answers nobody while scanning the same library and
    # writing the same files.
    if already_serving():
        print("Palladium is already running on port %d" % PORT)
        return
    # what the first run fetched, if it did. Named before anything imports the engine,
    # which looks for ffmpeg once and remembers what it found.
    told = (read_settings() or {}).get("ffmpeg") or ""
    if told and os.path.exists(told):
        os.environ["PALLADIUM_FFMPEG"] = told
    url = f"http://localhost:{PORT}/"
    # bound to every interface so the Chromecast can fetch the GPU stream; the
    # local_only() check keeps everything else on loopback
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Palladium serving {url}  (Ctrl-C to stop)")
        print(f"  video reachable on the network at http://{LAN_IP}:{PORT}/gpu/stream")
        # the library can ask how far off a file's sound is
        hand_over_the_gain()
        # what somebody set when 100% meant three different things
        rescale_subtitle_sizes()
        # and the subtitles inside films, lifted out while nobody is watching, so that
        # choosing one is instant rather than eight minutes of reading a container
        threading.Thread(target=warm_subtitles, daemon=True).start()
        # the API key is purged if it goes unused; this notices before that happens
        threading.Thread(target=keepalive_loop, daemon=True).start()
        # new films appear on their own; the server can notice without being asked
        threading.Thread(target=watch_folders, daemon=True).start()
        # and every file's loudness, measured while nothing is playing
        threading.Thread(target=keep_measuring, name="palladium-measuring",
                         daemon=True).start()
        # segments left by an iPhone that has stopped watching: gigabytes a film, so
        # they are swept rather than left for the temp folder to accumulate
        threading.Thread(target=sweep_segments, daemon=True).start()
        threading.Thread(target=flush_invites, daemon=True).start()
        # films offered from torrent packs: matched to TMDB, and followed while fetching
        import pd_torrents

        def scan_after_download():
            lib = local().lib
            if lib.scan_state.get("running"):
                return False
            threading.Thread(target=lambda: lib.scan(probe=True, identify=True),
                             daemon=True).start()
            return True
        pd_torrents.start(ROOT, lambda: local().lib, scan_after_download)
        # the way in from outside: nothing runs unless somebody set up the other way
        import pd_proxy
        pd_proxy.start(ROOT, PORT)
        import pd_library
        pd_library.UNFINISHED = pd_torrents.unfinished
        # a title that moves onto another key takes what was written down about it
        pd_library.CARRY = carry_keys
        # what has gone out of this machine, month by month: what people watched and
        # what the cache cost, in rows of their own
        import pd_traffic
        pd_traffic.use(os.path.join(ROOT, "traffic.json"))
        pd_traffic.use_log(os.path.join(ROOT, "copies.jsonl"))
        WATCHING.ledger = pd_traffic.note
        # and, if this server follows another, the copying of what that house is
        # watching - so a film is here when the machine holding it is not
        try:
            import pd_follow
            if pd_follow.settings(local().lib.config()).get("on"):
                # what is being read this minute, so the sweep leaves it alone
                pd_follow.BUSY = WATCHING.paths
                pd_follow.start(lambda: local().lib.config(), local().lib,
                                lambda: Handler.game_holds("copies"),
                                (PORT, socket.gethostname(), STATIC,
                                 Handler.build_version(), settings_path()),
                                learn_invites, local(), carry_the_keys)
        except Exception:
            pass
        # what the other machine said it was holding last time this one was running,
        # so the marks on the posters are there before it next speaks
        Handler.recall_copies()
        Handler.recall_followers()
        # and the notes about films nobody is watching any more
        threading.Thread(target=sweep_subtitle_notes, daemon=True).start()
        try:
            engine().sweep_old()          # and whatever an earlier run left behind
        except Exception:
            pass
        # The icon in the notification area, shown by the server itself. It was a
        # separate program that started this one hidden at sign-in, and Defender's
        # machine learning took that launcher for malware and quarantined it.
        packaged = bool(globals().get("__compiled__")) or getattr(sys, "frozen", False)
        if packaged and os.name == "nt" and "--no-tray" not in sys.argv:
            try:
                import pd_tray
                pd_tray.start_inside()
            except Exception:
                pass                       # no icon is no reason not to serve
        if "--no-open" not in sys.argv:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            # a guest's seen-at and hit count live in memory between writes, and so
            # does the last minute of what went out of here
            INVITES.flush()
            try:
                import pd_traffic
                pd_traffic.flush()
            except Exception:
                pass


if __name__ == "__main__":
    main()
