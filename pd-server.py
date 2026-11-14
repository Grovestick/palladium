#!/usr/bin/env python3
"""Palladium - a media server for the folders you already have.

Reads films and series off disk, indexes them, fetches artwork and subtitles, and
serves the lot to a browser, a phone and a television. Serving the client from
http://localhost:8765 also gives the Google Cast SDK the secure origin it insists on.

    python pd-server.py              # serve, and open a browser
    python pd-server.py --no-open    # serve only
    python pd-server.py --port 9000 --root PATH
"""
import functools
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
import urllib.error

import pd_repair
import pd_safe
import sys
import threading
import webbrowser

from pd_invites import Invites
from pd_watching import WATCHING

# The server's parts kept in their own files (pd_h_*.py, pd_srv_*.py) reach this module's
# names through S, which is this module: handed over before any of them is imported
import pd_h
pd_h.SERVER = sys.modules[__name__]


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
#: run as the Start fronts' own process: on a copy of the state under --root, the fronts
#: written to this file, then gone. Nothing is served and nothing of the plugins starts.
FRONTS_CHILD = _flag("--make-fronts")
#: and only these viewers', comma-separated: the ones who watched something since
FRONTS_FOR = _flag("--fronts-for")
#: the debug log is moved aside at start past this size, one older one kept: it had
#: grown to 50 MB and 434,000 lines with nothing ever taken off it
LOG_KEPT_MB = 20


def rotate_log(where, most_mb=LOG_KEPT_MB):
    """debug.log to debug.log.1 when it is past `most_mb`; True when it was moved."""
    try:
        if os.path.getsize(where) < most_mb * 1_000_000:
            return False
        os.replace(where, where + ".1")
        return True
    except OSError:
        return False


rotate_log(os.path.join(ROOT, "debug.log"))
import pd_log  # noqa: E402
import pd_subtap
pd_log.setup(ROOT)
# A crash in native code - an access violation, twice in a day - leaves no Python trace
# and no log line: every thread's stack is written down when it happens.
try:
    import faulthandler
    _FAULTS = open(os.path.join(ROOT, "fault.log"), "a", buffering=1, encoding="utf-8")
    _FAULTS.write("---- started %s, pid %d" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()) + chr(10))
    faulthandler.enable(file=_FAULTS, all_threads=True)
except Exception:
    pass

#: A stop without warning can leave a state file the right length and all zeros. Each
#: is put back from its newest copy that reads before anything - the port first of
#: all - is taken from it. {file: the copy used}
RECOVERED = {}
for _name in ("settings.json", "invites.json", "library.json"):
    try:
        _from = pd_safe.recover(os.path.join(ROOT, _name))
        if _from:
            RECOVERED[_name] = os.path.basename(_from)
            pd_log.raw("%s %s could not be read and was put back from %s; the "
                     "unreadable file is kept beside it%s"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), _name,
                        os.path.basename(_from), chr(10)))
    except Exception:
        pass                          # a recovery that fails leaves things as they were

#: the tray icon this server shows, so an update can take it down before the restart
TRAY = {"icon": None}
#: no settings yet: the first start on this machine, the one that opens a browser tab
FIRST_START = not os.path.exists(os.path.join(ROOT, "settings.json"))
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
# the notice, the watchlist's messages and blinks: pd_notify, the notifications' handler
import pd_notify  # noqa: E402
from pd_notify import NOTICE, NOTICE_RUNG  # noqa: E402
#: how long a title sent to the screens is played by one that asks: the ones listening
PLAY_FOR = 60.0

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


def outside_host():
    """The name the house is reached by from outside: the router's DDNS name when one
    is set in Settings, the bare address otherwise - which the provider changes."""
    try:
        name = str((read_settings() or {}).get("outsideName") or "").strip()
    except Exception:
        name = ""
    return name or wan_ip()


def outside_url(port=None):
    """The house's address from outside with its scheme: https once a certificate is here
    and the outside name is set (the certificate is for that name, not for the bare
    address), http otherwise. "" when nothing is known."""
    host = outside_host()
    if not host:
        return ""
    named = not re.match(r"^\d+\.\d+\.\d+\.\d+$", host)
    scheme = "https" if named and tls_context() is not None else "http"
    return "%s://%s:%d" % (scheme, host, PORT if port is None else port)


def local():
    global LOCAL
    if LOCAL is None:
        import pd_library as library
        import pd_localapi as localapi
        # where this server keeps its papers, which is not where its code sits
        localapi.use_data_dir(ROOT)
        # what each sitting has taken, carried through a restart
        WATCHING.keep_in(ROOT)
        import pd_streaming
        pd_streaming.use_data_dir(ROOT)
        import pd_svtplay
        pd_svtplay.use_data_dir(ROOT)
        import pd_live
        pd_live.use_data_dir(ROOT)
        # the plugins at start: a plugin's channel asked for before anything else had
        # loaded them was "no such channel"
        import pd_plugins
        if not FRONTS_CHILD:
            pd_plugins.start()
        import pd_holidays
        pd_holidays.use_data_dir(ROOT)
        made = library.Library(ROOT)
        # a file still downloading is no film yet
        try:
            import pd_torrents as _pt
            setattr(made, "still_coming", _pt.unfinished)
            # a file from a pack added as one series: the pack says what it is - and
            # a race or a game from the tracker is filed under its sport
            import pd_sports as _sp
            setattr(made, "told_by_pack", lambda path: _pt.told(path) or _sp.told(path))
        except Exception:
            pass
        LOCAL = localapi.LocalAPI(made)
        # what is coming in straight from the tracker, for the shelves and pages
        setattr(LOCAL, "coming_from_tracker", tracker_coming)
        # the viewer's collections that are series, for the Start tab's Shuffle row
        setattr(LOCAL, "series_shelves", series_shelves)
        setattr(LOCAL, "lead_rules", lead_rules)
        # a conversion to title keys that failed leaves the library on its old keys,
        # working but unable to agree with the other machine: said out loud
        if getattr(made, "keys_failed", ""):
            said = "converting the library to title keys failed" + chr(10) + made.keys_failed
            try:
                pd_log.raw(time.strftime("%H:%M:%S") + " fault " + said + chr(10))
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


def ffmpeg_is_ours(command, ffmpeg, root):
    """Whether a running ffmpeg's command line is one this program started: our ffmpeg
    as its first word, or writing into our data folder."""
    said = str(command or "").strip().lower()
    if not said:
        return False
    first = said[1:].split('"', 1)[0] if said.startswith('"') else said.split(" ", 1)[0]
    mine = str(ffmpeg or "").strip().lower()
    return bool((mine and first == mine) or (root and str(root).lower() in said))


def stop_left_behind_ffmpeg():
    """Stop the ffmpeg a server before this one started and never stopped.

    A server that is installed over or restarted leaves its children running: a
    loudness measurement of a 2160p film was started again by every server after it,
    seven of them reading one hard drive at once and none of them ever finishing.
    Only ours, and only those whose server is gone."""
    if os.name != "nt":
        return 0
    script = ("$alive=@(Get-Process|ForEach-Object{$_.Id}); "
              "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
              "Where-Object { $alive -notcontains [int]$_.ParentProcessId } | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    try:
        said = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True, timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return 0
    stopped = 0
    for line in said.splitlines():
        pid, _, command = line.partition("\t")
        if pid.strip().isdigit() and ffmpeg_is_ours(command, ffmpeg_now(), ROOT):
            try:
                os.kill(int(pid), 9)
                stopped += 1
            except OSError:
                pass
    if stopped:
        try:
            pd_log.raw("%s stopped %d ffmpeg left behind by an earlier server%s"
                    % (time.strftime("%H:%M:%S"), stopped, chr(10)))
        except OSError:
            pass
    return stopped


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


#: first stretches being read behind a request that stopped waiting, by cache name
HEADS = {}


from pd_release import (
    AUDIO_MBIT, H265_WORTH, MISS_NAMES, RELEASE_WORDS, TRUSTED_GROUPS, audio_mbit,
    close_call, for_series, group_of, height_of, meets_all, misses, per_gb, picture_lines,
    preference, quality_of, rank_key, rate_band, rate_fits, reasons_of, release_codec,
    release_group, release_is, video_mbit, which_side,
)


#: the bitrate bands a house can set, Mbit/s of picture
RATES = ("2-4", "3-6", "4-8", "6-12", "8-16", "12-25", "any")


#: what the Upgrade tab calls each reason a file is outside Choosing
REASON_NAMES = {"res-higher": "Resolution higher", "res-lower": "Resolution lower",
                "res": "Resolution", "rate-higher": "Bitrate higher",
                "rate-lower": "Bitrate lower", "source": "Release", "codec": "Encoding",
                "sound": "Sound", "group": "Release group"}


#: IMDb numbers worked out from TMDB, by TMDB number: asked once each
IMDB_OF = {}


#: the last upgrade list worked out, and when: it reads the whole library
UPGRADES = {"at": 0.0, "list": []}


#: title|year -> seconds, from the catalogue, for films the library does not hold
RUNTIMES = {}


#: An episode is not fetched the moment the first release of it is posted. The good
#: ones often come an hour or two behind the fast ones, and taking the first is how a
#: shelf fills with whatever was quickest rather than what was wanted.
SETTLE_FOR = 60 * 60
#: the house's night, when nobody is watching and a new episode has till morning to
#: find its good release; the same hours the machine keeping copies works through
NIGHT_FROM, NIGHT_TO = 22, 8


#: how long an episode with nothing inside Choosing had been waited for before the
#: best there was got taken anyway; nothing outside Choosing is taken by itself now
PATIENT_FOR = 24 * 3600
#: never fetched by itself, whatever the wishes say: a season pack is not an episode
#: and a forty gigabyte remux of one is not what keeping up means
MOST_GB_EACH = 20.0
#: keeping up is about what has just come out. The index holds four months, so a
#: programme added to the list would otherwise fetch a season in an afternoon.
NEW_FOR = 14 * 86400
#: and no more than this many in one pass, whatever is waiting: a quarter of an hour
#: later there is another pass, and a drive fills quietly either way
MOST_AT_ONCE = 3


#: a season in a release name, on its own or as a range: "S32", "S01-S31", "S01-S07"
SEASON_RUN = re.compile(r"(?:^|[^A-Za-z0-9])[sS](\d{1,2})(?:\s*-\s*[sS]?(\d{1,2}))?"
                        r"(?![0-9eE])")


#: how long the list of what is being watched is kept before it is read again
TOP_SHOWS_EVERY = 24 * 3600


#: the popular list's form; a list kept in another is read again
TOP_SHOWS_V = 2
#: one Swedish series in every so many places on the popular list
TOP_SHOWS_HOME_EVERY = 3


#: (programme, season) -> when the tracker had nothing to add for it; asked again a day later
EMPTY_SEASONS = {}
EMPTY_FOR = 86400


#: the other names a series goes by, asked of the catalogue once a day
SERIES_NAMES = {}
#: and the year it began, so a film of the same name with its year is told apart
SERIES_YEAR = {}


#: series sharing a name, per name and catalogue number, asked of the catalogue once a day
SERIES_TWINS = {}


from pd_srv_catalogue import (
    imdb_number, runtime_of, catalogue_title, hit_of_year, listed_tmdb, upgrades, duplicates,
    catalogue_runtime, episode_runtimes, ready_at, seasons_named, season_gaps, top_shows,
    mix_in, show_name, show_rows, popular_everything, add_popular, popular_shows, shelf_series,
    series_shelves, shuffled_last_first, series_episode_keys, library_shows, stock_shows,
    series_names, release_year, twin_verdict, _held_series_number, series_twins)


#: the background workers by what the Automatic runs list calls them
WORKERS = {"torrents": "Download worker", "subcheck": "Subtitle check",
           "cast": "Actor index", "seasons": "Series seasons",
           "episodes": "New episodes", "search": "Tracker search",
           "stills": "Timeline stills", "fixer": "File fixer", "xseed": "Cross-seed",
           "speed": "Speed rules", "boss": "Boss"}


#: workers that run outside this server and report here: id -> {name, step, done, left, at}
OUTSIDE = {}


#: the main server's skip rules as a copy last heard them; None on the main server
HOUSE_LEADS = {"at": 0.0, "rules": None}


LANDING = {"at": 0.0, "rows": []}


def landing():
    """What has just arrived, as last read: answered at once, never waited on. A reading
    older than ten seconds starts the next one beside the answer - asked in the request,
    a download client that was down held Recently added two seconds (2026-11-14)."""
    if time.time() - LANDING["at"] >= 10 and not LANDING.get("busy"):
        LANDING["busy"] = True
        threading.Thread(target=_read_landing_now, name="palladium-landing", daemon=True).start()
    return LANDING["rows"]


def _read_landing_now():
    try:
        LANDING["rows"] = read_landing()
    finally:
        LANDING["at"] = time.time()
        LANDING["busy"] = False


def read_landing():
    """landing_now on the live tracker log, qBittorrent and library."""
    rows = []
    try:
        import pd_tracker, pd_torrents
        with pd_tracker.LOCK:
            log = list(pd_tracker.STATE.get("log") or [])[-50:]
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        done = {}
        for t in json.loads(qb._call("/api/v2/torrents/info?filter=completed", timeout=6)
                            or b"[]"):
            if int(t.get("completion_on") or 0) > 0:
                done[pd_tracker.release_flat(t.get("name"))] = int(t["completion_on"])
        con = local().lib.db()
        try:
            # the releases the library has read a file of lately, by folder and file name
            read_in = set()
            since = time.time() - pd_tracker.LANDING_FOR - 3600
            for (path,) in con.execute("SELECT path FROM file WHERE mtime > ? OR ctime > ?",
                                       (since, since)):
                read_in.add(pd_tracker.release_flat(os.path.basename(os.path.dirname(path))))
                read_in.add(pd_tracker.release_flat(os.path.basename(path)))

            def held(title, year, name=""):
                return (pd_tracker.release_flat(name) in read_in
                        or bool(local()._arrived_since(con, {"title": title, "year": year})))
            rows = pd_tracker.landing_now(log, done, held, time.time())
        finally:
            con.close()
    except Exception:
        rows = []
    return rows


#: what qBittorrent is fetching, asked at most every few seconds: Now playing polls
COMING = {"at": 0.0, "rows": []}


#: the checks as last asked, for four seconds
CHECKS = {"at": 0.0, "rows": []}


#: qBittorrent categories whose torrents are seeding work rather than downloads for the
#: library: cross-seeds of files already here, and freeleech races
NOT_LIBRARY = ("cross-seed", "fl-race")


#: the longest a shuffle's pack fetches wait for the episode it opened to start playing
START_FIRST_S = 60


#: what each cache server is downloading, as its own /torrents/active says it: asked in
#: the background, so a cache that does not answer never holds up the line in the menu
CACHE_ACTIVE = {"at": 0.0, "asked": 0.0, "rows": []}


#: what the other server holds, as it last said, kept across a restart: its films stay on the
#: list, greyed, while it is off
PEER_FILMS = {"rows": [], "name": "", "up": True, "read": False, "dated": {}, "music": []}
PEER_ASK_EVERY = 60


#: who the other server is sending films to, as (address, key), asked at most every 5 s
PEER_LIVE = {"at": 0.0, "marks": {}, "asking": False}


from pd_srv_packs import (
    added_pack_for, release_group, index_of, show_packs, use_show_pack, season_offers,
    fill_season, follow_list, followed_season, aired_up_to, auto_pick, release_flat,
    _ask_leads, lead_rules, tracker_coming, version_words, checking_now, first_picture,
    copies_coming, cache_downloads, _ask_cache_downloads, ask_other_slot, with_cache_downloads,
    handed_not_taken, coming_in, read_coming, tracker_takes, catalogue_lists, last_groups, shows_waiting,
    grab_episode, keep_up_with_shows)


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


#: the settings as read at the start of an answer that only reads them, for the rest of it
_HELD = threading.local()


class settings_held:
    """`with settings_held():` - read_settings answers with one reading for the length of
    the block, on this thread only. For answers that read the file field by field."""

    def __enter__(self):
        _HELD.stored = None
        _HELD.stored = read_settings()
        return _HELD.stored

    def __exit__(self, *gone):
        _HELD.stored = None
        return False


from pd_vtt import (
    SCRIPTS, STAMP, WRITTEN_IN, _cp1252_tail, _stamp, as_vtt, cut_vtt, drop_echoes,
    flatten_rollup, last_cue, mend_vtt, one_language, script_of, shift_vtt, srt_to_vtt,
    stamp_of, stamp_secs, stretch_vtt, vtt_span, whole_stamps,
)


# faults the server itself hit, by signature and when it was last written down
FAULTS_SEEN = {}


#: a character UTF-8 wrote as two to four bytes, read back one byte at a time as
#: Windows-1252: "â™ª" for a music note, "â€™" for an apostrophe
GARBLED = re.compile("[\u00c2-\u00f4][" + re.escape(_cp1252_tail()) + "]{1,3}")


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


#: the file being measured this minute, for Now playing to show
MEASURE_NOW = {"name": "", "since": 0}
#: the file somebody is watching, waiting for the one measurement running to end:
#: part -> (path, size). It was turned away while the backlog had the slot, so a new
#: episode played without its loudness for as long as a 4K film took to read.
MEASURE_NEXT = {}

#: Target loudness. Not EBU's -23: this library holds two copies of one episode 13 dB
#: apart, and the usual level here is nearer streaming's -16 to -18.
VOLUME_TARGET = -18.0
#: and the maximum correction applied
VOLUME_MOST = 8.0


#: Which files of a title have been played, by title key: {path: when}. A film held in
#: three versions was copied to the cache as one - whichever came first - and the
#: version somebody was watching could not be split between the two machines.
PLAYED_FILES = {}
PLAYED_DAYS = 30.0
PLAYED_READ = []


#: When each title fetched before anybody asked for it was first named, so one
#: nobody ever got to can be dropped rather than held for ever. Kept on disk: the
#: server is restarted often enough that a clock living in memory would never run out.
AHEAD_SINCE = {}
AHEAD_DAYS = 30.0
#: days a copy keeps a title after it was watched, unless the copy says otherwise
KEEP_DAYS = 7.0


#: the sweep through everything not measured yet, and when it last looked
MEASURE_SWEEP = {"at": 0.0, "left": None}
#: the files about to be watched, worked out every five minutes: (id, path, size)
MEASURE_SOON = {"at": 0.0, "rows": []}


MEASURE_EVERY = 20.0
#: files here measured the 20-sample way and files still to go, counted from the library
MEASURE_COUNT = {"at": 0.0, "done": 0, "left": 0}


#: the median loudness of films and of episodes whose sound can be passed through,
#: and when it was worked out
TYPICAL = {"at": 0.0, "film": None, "episode": None}


#: when the main server's loudness readings were last asked for
HOUSE_LOUDNESS = {"at": 0.0}


#: a title's loudness as the other server read it, by key: {key: (when asked, answer)}
PEER_READINGS = {}


#: what the other server said it holds, by key: {key: (when asked, held)}
PEER_HELD = {}


#: when the positions last went across because somebody paused or stopped
PLACE_SAID_AT = {"at": 0.0}


#: when the other server's stills were last asked for
PEER_STILLS = {"at": 0.0}


from pd_srv_peer import (
    peer_recent, keep_peer_recent, copy_answers, peer_art, _ask_peer_recent, peer_live,
    _ask_peer_live, peer_marks, peer_away, peer_fetch, peer_reading, held_here, peer_holds,
    place_said, stills_folder, stills_rows, stills_to_take, take_peer_stills)


#: the last reading of Windows' disk events, kept an hour: (when, [{drive, count, kinds}])
DISK_FAULTS = [0.0, []]


#: Start's fronts made ahead: first looked at this long after the server starts
START_AHEAD_AFTER = 600


# what a viewer is told without asking - the passes, their timings - is pd_notify's
from pd_notify import (WATCH_NEWS_EVERY, WATCH_NEWS_KEEP, watch_news_pass,  # noqa: E402,F401
                       WATCH_ARRIVALS_EVERY, WATCH_ARRIVALS, tokens_by_name, came_in_at,
                       games_onto_watchlists, title_for_news, watch_arrivals_pass,
                       keep_watch_arrivals, keep_watch_news)


#: where the app everyone but the owner is given is kept: released at APP_RELEASE_HOUR
APP_RELEASED = "app-released"
APP_RELEASE_HOUR = 8


#: what the fronts' own process made last, read back at every start
FRONTS_FILE = "start_fronts.json"
#: the most the fronts' process may take before it is stopped
FRONTS_PATIENCE = 30 * 60


# Handler's parts kept in their own files
import pd_h_follow
import pd_h_get
import pd_h_guard
import pd_h_house
import pd_h_library
import pd_h_post
import pd_h_settings
import pd_h_shuffle
import pd_h_stream
import pd_h_subs


class Handler(pd_h_subs.SubtitleParts, pd_h_follow.FollowParts, pd_h_shuffle.ShuffleParts,
              pd_h_stream.StreamParts, pd_h_settings.SettingsParts,
              pd_h_guard.GuardParts, pd_h_library.LibraryParts, pd_h_house.HouseParts,
              pd_h_post.PostRoutes, pd_h_get.GetRoutes, http.server.SimpleHTTPRequestHandler):
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
    SEEN: dict = {}
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
        row: "dict | None" = self.SEEN.get(key)
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
                pd_log.raw("%s fault %s %s%s%s%s" % (
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
        # The routes, in the order they are tried. A part that answers returns None; one
        # that does not hands back what the parts after it need. Cut out of one function
        # of 3841 lines, which no checker could read; nothing inside a part was changed.
        path = None
        went = self._post_part_01(path)
        if went is None:
            return
        (path,) = went
        if self._post_part_02(path) is None:
            return
        went = self._post_part_03(path)
        if went is None:
            return
        (path,) = went
        if self._post_part_04(path) is None:
            return
        if self._post_part_05(path) is None:
            return
        if self._post_part_06(path) is None:
            return
        if self._post_part_07(path) is None:
            return
        if self._post_part_08(path) is None:
            return
        if self._post_part_09(path) is None:
            return
        if self._post_part_10(path) is None:
            return
        if self._post_part_11(path) is None:
            return
        if self._post_part_12(path) is None:
            return
        if self._post_part_13(path) is None:
            return
        if self._post_part_14(path) is None:
            return
        if self._post_part_15(path) is None:
            return
        if self._post_part_16(path) is None:
            return
        if self._post_part_17(path) is None:
            return
        if self._post_part_18(path) is None:
            return
        if self._post_part_19(path) is None:
            return
        self._post_part_20(path)

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
        from pd_localapi import resolution_label
        label = resolution_label(src.get("height"), src.get("width"))
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
    # Off screen, as near the picture as it goes, and one row up on the picture where
    # there is no black under it: the owner's own choice, everyone's starting point.
    SUBTITLE_DEFAULTS = {
        "tv":    {"size": 1.0,  "position": 0.0, "colour": "white",
                  "background": "shadow", "font": "sans", "base": "screen",
                  "onPicture": 0.08},
        "web":   {"size": 1.0,  "position": 0.0, "colour": "white",
                  "background": "shadow", "font": "sans", "base": "screen",
                  "onPicture": 0.08},
        # 0.05 was between two of the four heights the menus offer, so a phone opened
        # its settings with none of them marked; a height is a row of text now, and
        # this is the first row up - which is where it was drawn anyway.
        "phone": {"size": 1.25, "position": 0.0, "base": "screen", "colour": "white",
                  "background": "shadow", "font": "sans", "onPicture": 0.08},
    }

    @classmethod
    def device_of(cls, name):
        name = (name or "web").lower()
        return name if name in cls.DEVICES else "web"

    def lead_rows(self, rules):
        """Skip rules as rows with the programme's title, for the settings page."""
        import pd_leads
        rows = pd_leads.listed(rules)
        con = local().lib.db()
        try:
            for row in rows:
                got = con.execute("SELECT title FROM item WHERE id=?", (row["show"],)).fetchone()
                row["title"] = got["title"] if got else row["show"]
        finally:
            con.close()
        rows.sort(key=lambda r: (str(r["title"]).lower(), r["season"], r["episode"]))
        return rows

    def offline_post(self, path):
        """A copy for a device to keep, made on this server: asked for, taken, or let go.
        The viewer's own."""
        import pd_offline
        body = self.read_json() or {}
        who = self.offline_who()
        if path == "/offline/make":
            key, mi = str(body.get("key") or ""), int(body.get("mi") or 0)
            src = local().file_for(key, mi, who=self.viewer())
            if not src:
                self.reply_json({"ok": False, "why": "no file for that title here"})
                return
            title, sub, _ = local().now_playing_fields(key)
            can = body.get("can") if isinstance(body.get("can"), dict) else {}
            self.reply_json(pd_offline.ask(key, mi, str(body.get("quality") or ""), can, src,
                                           who, " - ".join(x for x in (title, sub) if x)))
            return
        ident = str(body.get("id") or "")
        job = pd_offline.load()["jobs"].get(ident)
        if not job or not (self.role == "owner" or job.get("who") == who):
            self.reply_json({"ok": False, "why": "no such copy"})
            return
        self.reply_json({"ok": pd_offline.drop(ident, taken=path == "/offline/got")})

    def offline_who(self):
        """The viewer's offline copies are kept under: a digest, never the key itself."""
        return hashlib.sha256(self.viewer().encode("utf-8")).hexdigest()[:16]

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


    # ---- subtitles from OpenSubtitles ---------------------------------------

    #: What the last look at a file's sound produced, so a second subtitle for the same
    #: film is placed without decoding it again. Small: a film is a few thousand bits.
    HEARD = {}
    HEARD_LOCK = threading.Lock()

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
                    # absent means yes: this is how it behaved before there was a
                    # switch, and an upgrade should not quietly stop keeping it
                    "cacheLately": bool(row.get("cacheLately", True)),
                    "syncGbWeek": float(row.get("syncGbWeek") or 0),
                    "downloadGbWeek": float(row.get("downloadGbWeek") or 0),
                    "token": mine}
        return {"cacheDeck": bool(stored.get("cacheDeck", True)),
                "cacheList": bool(stored.get("cacheList", False)),
                # the last dozen things the house watched, and the episodes after
                # them: on unless somebody says otherwise, as it always was
                "cacheLately": bool(stored.get("cacheLately", True)),
                # what the shuffle would put on next: an evening of casual watching
                # is exactly the evening nobody chooses a film for
                "cacheCasual": bool(stored.get("cacheCasual", False)),
                "syncGbWeek": float(stored.get("syncGbWeek") or 0),
                "downloadGbWeek": float(stored.get("downloadGbWeek") or 0)}

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

    @staticmethod
    def reading_of(key):
        """This server's loudness reading of a title's file, and the codec it is judged by."""
        found = local().file_for(str(key or ""), 0) or {}
        part = found.get("part")
        if part is None:
            return {}
        with LOUDNESS_LOCK:
            known = read_loudness().get(str(part))
        return {"reading": known, "codec": codec_of(part)} if known else {}

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

    #: Films the machine keeping copies fetched for itself while this one was off,
    #: waiting to be copied back. Named by the pack and the file inside it, because
    #: that is what decides where it has to sit for qBittorrent to know it.
    FROM_COPY = []
    FROM_COPY_LOCK = threading.Lock()
    FROM_COPY_BUSY = [False]
    #: a fetch from the copy that wrote nothing is tried this many times in all
    FROM_COPY_TRIES = 3
    #: what the copy list wants that this server has no file for, from the fullest recent
    #: asking: {"keys": [(key, why)], "deck": bool, "when": time} - fetched from the copy
    WANTED_HERE = {"keys": [], "deck": False, "when": 0.0}

    #: the last sync back: when, what it took back and let go, and why it stopped if it did
    SYNC_BACK = {"at": 0.0, "busy": False, "fetch": [], "drop": [], "why": ""}
    #: the most films let go in one round, ten minutes apart
    LET_GO_PER_ROUND = 5
    #: what was on a screen at the last round, as TMDB numbers: never let go meanwhile
    SYNC_PLAYING = set()
    #: drive -> files this server is sending to the copy server now, for the drives' lines
    SERVING = {}
    SERVING_LOCK = threading.Lock()
    #: what the copy rules wanted at the last round, as TMDB numbers: what the copy's own
    #: reports are measured against between rounds
    SYNC_WANTED = set()

    @staticmethod
    def tmdb_of_pack_file(info_hash, index):
        """The TMDB number of one file of one pack, or 0."""
        import pd_torrents
        for pack in pd_torrents.load()["packs"]:
            if pack.get("hash") == info_hash:
                for film in pack.get("films") or []:
                    if int(film.get("index", -1)) == int(index if index is not None else -2):
                        return int(film.get("tmdb") or 0)
        return 0

    def files_here(self, key="", title="", year=0):
        """The names of the files this house holds for one title, flattened.

        A release and the file it unpacked to are named the same thing, so a name in
        this set is a release already here - which is how a version list says so
        rather than fetching it twice.
        """
        here = set()
        try:
            con = local().lib.db()
            try:
                if key:
                    rows = con.execute(
                        "SELECT path FROM file WHERE item_id=? OR episode_id=?",
                        (key, key)).fetchall()
                else:
                    # By the title with its punctuation taken out. A tracker row
                    # spells a film's name without the apostrophe the library keeps,
                    # so an exact match said nothing was here for a film sitting on
                    # the disk in four versions.
                    plain = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
                    rows = [r for r in con.execute(
                        "SELECT i.title, f.path FROM file f "
                        "JOIN item i ON i.id = f.item_id "
                        "WHERE i.year = ? OR ? = 0", (year, year)).fetchall()
                        if re.sub(r"[^a-z0-9]+", "", str(r["title"] or "").lower())
                        == plain]
            finally:
                con.close()
            for r in rows:
                where = str(r["path"] or "")
                # the disk rather than the record: a download cancelled part way
                # leaves its row until the next scan, and saying "on disk" about
                # something that was just thrown away is worse than saying nothing
                if not where or not os.path.exists(where):
                    continue
                # the file's name, and its folder's: a release unpacks to a folder
                # named for it holding a file called anything - "lchd-efla.mkv"
                for name in (os.path.splitext(os.path.basename(where))[0],
                             os.path.basename(os.path.dirname(where))):
                    flat = re.sub(r"[^a-z0-9]+", "", name.lower())
                    if flat:
                        here.add(flat)
        except Exception:
            return set()
        return here

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

    @staticmethod
    def receiver_not_for(at_home, is_device):
        """The answer to a screen the receiver is not raised for, or None. "off" is
        the one reason every version of the app shows nothing for; the two that were
        sent stood on a remote viewer's information line as a fault."""
        if not at_home:
            return {"ok": False, "why": "off", "because": "not in the house"}
        if not is_device:
            return {"ok": False, "why": "off", "because": "not the device set for the receiver"}
        return None

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

    # ---- a collection, played in shuffle -------------------------------------


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

    def reply_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        # room on the drive is the owner's; a guest sees it only when a file will not fit
        if getattr(self, "role", None) != "owner" and b'"free": ' in body:
            import pd_torrents
            body = json.dumps(pd_torrents.room_for_guest(obj)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body = self.squeezed(body, "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- the owner's own password -------------------------------------------
    #
    # Optional, and off until somebody sets one: a server on a home network with one
    # person on it needs no password, and demanding one would be theatre. Set one and
    # it is required from everywhere except the machine itself.

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
        rng = self.headers.get("Range")
        asked_for = byte_range(rng, size)
        if asked_for is None:
            self.send_response(416)
            self.send_header("Content-Range", "bytes */%d" % size)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = asked_for
        if rng and rng.startswith("bytes="):
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
        # what has gone since the last pause, and when the viewers and the ceiling are
        # looked at next: four times a second, not every megabyte. Read every 1 MB,
        # the settings file and the list of viewers cost a copy a third of its speed.
        paced, next_look, ceiling = 0, 0.0, 0.0
        # a copy reads in megabytes: fewer turns of this loop for the same file
        block = 1048576 if copying_out else 262144
        # One file at a time to any one machine. Whoever asked last is the one still
        # listening; anything older is being sent to nobody.
        whom = self.client_address[0]
        if copying_out:
            Handler.SYNC_NOW[whom] = sid
        # once: asked for every block it read the settings file for each of them
        viewer = self.watcher()
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                want = min(block, remaining)
                chunk = None
                # evenly spread rather than the first so-many of every hundred:
                # one block in five for a fifth, not twenty then eighty
                if now and (taken * now) % 100 < now:
                    # its turn: ask the cache, and fall to this disk the moment it is
                    # slow or unwilling. A block late is worse than a block read here.
                    chunk = self.block_from_copy(share, at, want)
                    if chunk:
                        f.seek(at + len(chunk))
                        Handler.CARRIED[viewer] = (
                            lambda c: (c[0], c[1] + len(chunk), time.time()))(
                                Handler.CARRIED.get(viewer) or (0, 0, 0))
                if chunk is None and not copying_out:
                    chunk = warm_block(path, at, want)
                    if chunk:
                        f.seek(at + len(chunk))
                if chunk is None:
                    try:
                        began = time.monotonic()
                        chunk = f.read(want)
                        disk += time.monotonic() - began
                        if chunk:
                            Handler.CARRIED[viewer] = (
                                lambda c: (c[0] + len(chunk), c[1], time.time()))(
                                    Handler.CARRIED.get(viewer) or (0, 0, 0))
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
                # A film being played, once it is more than a look at the file's index: its
                # subtitles are read from these same blocks, so lifting them does not read
                # the film a second time (owner, 2026-11-06)
                if not copying_out and taken >= 16:
                    pd_subtap.feed(path, at, chunk)
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
                if copying_out and time.monotonic() >= next_look:
                    next_look = time.monotonic() + Handler.COPY_LOOK_EVERY
                    ceiling = 0.0
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
                        # a viewer whose buffer is running down: the copy stops until
                        # it is full again, not walks - a while at a time, so the other
                        # machine's read never waits long enough to give up
                        # and while a viewer on the drive it reads is filling their buffer:
                        # the speed handler's rule, from the buffers (2026-11-06)
                        import pd_speed
                        held = 0
                        while held < Handler.COPY_HOLD_MOST and (
                                playback_short() or not pd_speed.copy_may_run(aheads_on_drive(path))):
                            time.sleep(1)
                            held += 1
                if copying_out and ceiling > 0 and taken % 4 == 0:
                    # how long what went since the last pause should have taken at
                    # the ceiling, less what it did take
                    owed = (paced * 8 / (ceiling * 1e6)) - held_back
                    if owed > 0:
                        time.sleep(min(owed, 1.0))
                    held_back, paced = 0.0, 0
                elif copying_out and not ceiling:
                    held_back, paced = 0.0, 0     # free: nothing owed when a ceiling comes
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
                paced += len(chunk)
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
            if self.codes_shut():
                self.send_error(429, "too many tries; wait an hour")
                return
            if not INVITES.by_code(m.group(1)):
                self.wrong_code(path)
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
        # The routes, in the order they are tried. A part that answers returns None; one
        # that does not hands back what the parts after it need. Cut out of one function
        # of 2740 lines, which no checker could read; nothing inside a part was changed.
        path = None
        q = None
        went = self._get_part_01(path)
        if went is None:
            return
        (path,) = went
        if self._get_part_02(path) is None:
            return
        if self._get_part_03(path) is None:
            return
        if self._get_part_04(path) is None:
            return
        if self._get_part_05(path) is None:
            return
        if self._get_part_06(path) is None:
            return
        if self._get_part_07(path) is None:
            return
        if self._get_part_08(path) is None:
            return
        if self._get_part_09(path) is None:
            return
        if self._get_part_10(path) is None:
            return
        went = self._get_part_11(path)
        if went is None:
            return
        (path,) = went
        if self._get_part_12(path) is None:
            return
        went = self._get_part_13(path, q)
        if went is None:
            return
        (q,) = went
        went = self._get_part_14(path, q)
        if went is None:
            return
        (q,) = went
        if self._get_part_15(path, q) is None:
            return
        self._get_part_16(path, q)

    def _get_part_16(self, path, q):
        """GET routes, part 16 of 16: /gpu/subs, /gpu/status, /config."""
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
            head_from = 0
            tapped = False
            try:
                offset = float(q.get("offset", ["0"])[0])
                video = (local().file_for(q.get("key", [""])[0],
                                          int(q.get("mi", ["0"])[0])) or {}).get("file", "")
                partial = False
                if q.get("src", [""])[0] == "local" and video:
                    ready = cached_subtitle(sub_cache_path(video, index))
                    if ready is not None:
                        said = ready
                    elif pd_subtap.wait_for(video, index, 5):
                        # being played: what its own blocks have shown so far, marked partial
                        # so the player asks again as it goes
                        said, partial = pd_subtap.vtt(video, index), True
                        tapped = True
                    elif cached_subtitle(head_cache_path(video, index, offset)) is not None:
                        # the stretch read for an earlier ask: nothing to read, nothing to wait for
                        said, partial = cached_subtitle(head_cache_path(video, index, offset)), True
                    elif sub_reader_waits(video, True):
                        # somebody is short: nothing is read now, and the player asks again
                        said, partial = "WEBVTT\n\n", True
                    else:
                        # Nobody has lifted this track out yet, and doing the whole of
                        # it means reading the film end to end. Take the first twenty
                        # minutes, which stops as soon as ffmpeg is past that mark, and
                        # set the whole reading going behind it. There is text on
                        # screen in a few seconds, and the rest arrives before it is
                        # needed.
                        # from the ten minutes this playing starts in: resumed an hour
                        # in, the first twenty minutes held nothing to show
                        head_from = int(offset // 600) * 600
                        start_pull(video, index, "for a screen playing it")
                        # The first stretch is waited for eight seconds and no more: on
                        # a disk busy with the stream, a copy to the cache and a listen
                        # to the sound, it took minutes and the player sat with nothing.
                        # It carries on behind, and the player's next ask takes it.
                        head = head_soon(video, index, head_from, 8)
                        if head:
                            said, partial = head, True
                        else:
                            # Nothing in the first stretch yet - a film whose download
                            # had only just finished, or one quiet at the start. Said at
                            # once and marked partial, so the player asks again: held
                            # open on the whole read instead, a 5 GB film read off a
                            # busy disk kept its subtitles off screen for many minutes.
                            said, partial = "WEBVTT" + chr(10) + chr(10), True
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
                    # the roll-up first, then the repeats left in what it wrote: a
                    # rolled track says each line two or three times over, and only
                    # once it is flattened are those plainly the same line twice
                    said = flatten_rollup(said)
                    said = drop_echoes(said)
                mended = self.sub_fit(self.fit_name(video, index))
                if mended and said:
                    said = mend_vtt(said, mended)
                shift = q.get("shift", ["0"])[0]
                if said and shift not in ("", "0", "0.0"):
                    said = shift_vtt(said, shift)
                if said and offset > 0:
                    said = cut_vtt(said, offset)
                body = mend_garbled(said).encode("utf-8")
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
                # read from a play: only as far as the player has fetched, so it asks again soon
                self.send_header("X-Palladium-Subs-Until",
                                 str(int(last_cue(body)) if tapped else head_from + SUB_HEAD))
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
                       # the owner away from home: a guest's page, with Now playing
                       # and the logs to read
                       "monitor": bool(self.owner_away),
                       # and which owner's groups this key opens from away
                       "ownerAway": (sorted(g for g, on in self.remote_owner().items()
                                            if on) if self.owner_away else []),
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
            cfg["letThemFetch"] = self.let_them_fetch()
            cfg["mayFetch"] = self.may_fetch()
            # the tab this viewer opens on, so the page can draw it first
            cfg["startTab"] = self.start_tab()
            # and how big a thing they may fetch: nought for the owner, who is not
            # held to it
            cfg["fetchMaxGb"] = 0.0 if self.owner_fetching() else self.fetch_most_gb()
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


#: the certificate in use and when its file was last looked at: {ctx, mtime, at}
TLS = {"ctx": None, "mtime": 0.0, "at": 0.0}


def tls_context():
    """The HTTPS context from tls/fullchain.pem and key.pem, read again when the file
    changes (renewed every two months); None while there is no certificate."""
    import ssl
    now = time.time()
    if now - TLS["at"] < 60 and TLS["at"]:
        return TLS["ctx"]
    TLS["at"] = now
    chain = os.path.join(ROOT, "tls", "fullchain.pem")
    key = os.path.join(ROOT, "tls", "key.pem")
    try:
        mtime = os.path.getmtime(chain)
    except OSError:
        TLS["ctx"] = None
        return None
    if TLS["ctx"] is None or mtime != TLS["mtime"]:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(chain, key)
            TLS.update(ctx=ctx, mtime=mtime)
        except (OSError, ssl.SSLError) as e:
            pd_log.raw("%s https: certificate not read: %s%s" % (time.strftime("%H:%M:%S"), e, chr(10)))
    return TLS["ctx"]


def tls_or_plain(sock):
    """The connection as it should be read: wrapped in TLS when its first byte opens a
    handshake (0x16) and there is a certificate, as it is otherwise. A caller that sends
    nothing for a minute is let go."""
    sock.settimeout(60)
    first = sock.recv(1, socket.MSG_PEEK)
    sock.settimeout(None)
    if first != b"\x16":
        return sock
    ctx = tls_context()
    if ctx is None:
        raise OSError("https asked for, no certificate here")
    sock.settimeout(15)
    wrapped = ctx.wrap_socket(sock, server_side=True)
    wrapped.settimeout(None)
    return wrapped


#: what the heartbeat counts. Connections accepted rather than requests answered:
#: the question it exists to settle is whether this process was still taking them.
BEATS = {"taken": 0}


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        BEATS["taken"] += 1
        return socketserver.ThreadingTCPServer.process_request(
            self, request, client_address)

    def process_request_thread(self, request, client_address):
        """HTTPS and plain HTTP on the one port: a connection that opens with a TLS
        handshake is given the certificate in tls/, any other is answered as before - so
        the links already handed out go on working beside the https ones (2026-11-07)."""
        if isinstance(request, socket.socket):
            try:
                request = tls_or_plain(request)
            except Exception:
                try:
                    request.close()
                except OSError:
                    pass
                return
        return socketserver.ThreadingTCPServer.process_request_thread(
            self, request, client_address)
    #: How many connections may be waiting to be accepted. Python's own answer is
    #: five, which was plenty while a film was one long connection: it is one stretch
    #: at a time from one screen. Reading a film off several machines asks for every
    #: two megabytes on a connection of its own, so a 4K film is several a second from
    #: each screen, and a copy taking files and a settings page asking how things are
    #: going are on top of that. Past five, the rest are refused - and what the player
    #: sees is a machine that will not answer, waits five seconds, and drops it. The
    #: film stopped for that long, now and then, on a server that was perfectly well.
    request_queue_size = 128


#: bumped when every viewer's subtitle look is set back to the defaults
SUB_PLACE_ROUND = 3
#: what a look is made of; a title's timing correction is not part of it and stays
SUB_LOOK_KEYS = ("size", "position", "colour", "background", "font", "base", "onPicture")


#: Text tracks worth lifting out. A picture subtitle has no text in it and is burned
#: into the stream instead.
TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")


from pd_srv_subs import (
    sub_cache_path, cached_subtitle, keep_tapped_subtitle, screen_waits, sub_reader_waits,
    head_subtitle, head_cache_path, head_soon, pulling_now, start_pull, _quietly,
    pull_subtitle, declared_cues, warmup_batches, pull_subtitles_all, _pull_subtitle,
    subtitle_without_one, subtitle_keepalive, mend_garbled, film_length, black_after,
    prune_subtitle_notes, sweep_subtitle_notes, place_subtitles_off_screen,
    rescale_subtitle_sizes, warm_subtitles)


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


#: held for as long as this server runs; see first_on_this_port
ONLY_ONE = {}


def first_on_this_port():
    """Whether this is the only Palladium for this port on the machine.

    Asking the port is not enough: two started in the same second - the Startup
    shortcut and an installer at logon - both find nobody answering yet, and both go
    on. The cache ran two that way, both copying into the same half-written file, and
    an 8.5 GB film crossed the network as 13. A named mutex is taken by one of them
    only, whatever the timing.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.CreateMutexW(None, False, r"Local\PalladiumServer-%d" % PORT)
        if not handle:
            return True
        if kernel.GetLastError() == 183:            # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(handle)
            return False
        ONLY_ONE["handle"] = handle
        return True
    except Exception:
        return True


def trust_our_own_certificates():
    """Verify HTTPS against a CA bundle that ships with the build.

    A machine that is never browsed on has a thin Windows certificate store - the
    cache had 39 roots and three intermediates - and Python, unlike the OS, does not
    fetch a missing intermediate to finish a chain. Every TMDB call there failed with
    CERTIFICATE_VERIFY_FAILED while PowerShell reached the same address, which is why
    it had no posters, no credits and could identify nothing. The bundle makes the
    chain complete wherever this runs.
    """
    import ssl
    where = os.path.join(CODE, "cacert.pem")
    if not os.path.exists(where):
        return
    try:
        held = ssl.create_default_context(cafile=where)
    except Exception:
        return
    ssl._create_default_https_context = lambda *a, **k: held


#: what the old Casual list left in each viewer's settings
OLD_CASUAL = ("casual", "casualMoved", "casualOrder", "casualQueue", "casualPlayed",
              "casualAt", "casualRun")


from pd_srv_settings import (
    carry_shifts, carry_the_keys, settings_path, languages_read, plain_words, read_settings,
    watch_key_of, fill_watchlists, retire_pack_keys, keep_pack_keys_retired, carry_keys,
    put_on_watchlist, write_settings, note_download, learn_invites, flush_invites,
    titles_in_use, forget_old_casual)


#: the jobs that run by themselves, each of which the owner can turn off
AUTO_RUNS = ("credits", "subcheck", "cast", "seasons", "episodes", "search", "stills", "fixer",
             "loudness", "warmup", "xseed")


#: which way the two servers copy for each other
SYNC_WAYS = ("both", "toCopy", "fromCopy")


from pd_srv_loudness import (
    anybody_watching, playing_drives, loudness_file, read_loudness, write_loudness,
    _read_played, played_file, holding_changes, log_rows, played_files, codec_of,
    take_readings, twin_of, twin_reading, measure_loudness, ahead_file, read_ahead,
    write_ahead, soon_to_be_watched, loudness_counts, loudness_rows, typical_loudness,
    take_house_loudness, loudness_reset, measure_the_backlog, keep_measuring, volume_gain,
    hand_over_the_gain, sittings)


#: the start and the end of the next episode's file, read while the one before it ends:
#: path -> {"at": when, "parts": [(offset, bytes)]}. An MP4 keeps its index at the end,
#: and the player opened every episode three times - start, end, start again - each
#: waiting on the disk: 6 to 9 s of a frozen 0:00 between episodes (2026-11-01)
WARM = {}
WARM_LOCK = threading.Lock()
#: bytes kept from each end of a file, files kept, and how long one is kept unasked for
WARM_EDGE = 16 * 1048576
WARM_FILES = 3
WARM_FOR = 1200


#: files read for somebody about to watch them - the next episode of one playing: not
#: stopped for a busy drive, like the one on screen
URGENT_FILES = set()


from pd_srv_workers import (
    busy_now, worker_state, workers_now, worker_reset, disk_slots, set_disk_slots,
    disk_faults_now, library_drives, scp_drives, scp_sending_from, start_disk_watch,
    sweep_segments, quiet_now, yield_pack_reads, split_seed_way, sync_way, byte_range,
    disks_busy, playback_short, aheads_on_drive, background_waits, files_on_screen, warm_file,
    warm_block, transfer_turn, drive_loaded, worker_waits, copy_while_busy)


def other_server():
    """The other server's (address, key): the main server's from a copy, the copy's from it."""
    import pd_follow
    cfg = local().lib.config()
    one = pd_follow.settings(cfg)
    if one.get("on") and one.get("master"):
        return str(one["master"]), str(one.get("key") or "")
    return str((cfg.get("standby") or {}).get("where") or ""), ""


def restart_self():
    """This server ended and started again by a small command that waits for the port to be
    free. Only an installed one: a source copy has nothing to start."""
    if not PACKAGED:
        return False
    exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "palladium-server.exe")
    if not os.path.exists(exe):
        return False
    import subprocess
    subprocess.Popen('cmd /c "ping -n 6 127.0.0.1 >nul & start "" "%s" --no-open"' % exe,
                     creationflags=0x00000008 | 0x00000200 | 0x08000000, close_fds=True)
    tray = TRAY.get("icon")
    threading.Timer(0.5, lambda: tray.bow_out_now() if tray else os._exit(0)).start()
    return True


def push_request():
    """The copy server told a download has been asked for. Not getting through costs little:
    it looks once a minute anyway."""
    try:
        where = str(Handler.in_house().standby_now().get("where") or "").rstrip("/")
        if where:
            req = urllib.request.Request(where + "/follow/takenow", headers={"X-Palladium-App": "house"})
            urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


#: one writer at a time for the paper of subtitle checks asked for
SUBCHECK_ASKED_LOCK = threading.Lock()


#: whether the subtitle check is measuring something this minute
SUBCHECK_BUSY = {"on": False}


from pd_srv_loops import (
    folder_fingerprint, watch_folders, keepalive_loop, keep_countries,
    app_release, keep_app_released, fronts_load, fronts_make, fronts_merge, fronts_child,
    keep_start_ready, keep_lists, keep_the_hats, heartbeat, start_apart, auto_set, auto_on,
    start_cast_index, stills_todo, stills_count, stills_inputs, start_fixer, start_speed,
    start_boss, start_xseed, start_weekly, start_claude, start_stills, settle_requests,
    subcheck_asked, first_in_line, start_credits, start_subcheck)


#: how a release is chosen on a fresh install: the house's own settings of 2026-09-26
CHOOSING_DEFAULTS = {
    "preferRes": "1080p",
    "preferSource": "bluray,webdl,webrip",
    "preferCodec": "any",
    "preferSound": "ddp,ac3,atmos,aac",
    "preferRate264": "5-8",
    "preferRate265": "5-8",
    "rateLock": False,
    "preferQuality": "pergb",
    "preferSeeds": "most",
    "preferBy": "seeds",
    "preferOrder": "res,seeds,quality,rate,source,codec,sound",
}


def choosing_defaults():
    """Start a fresh install with the house's Choosing settings. Only a server with no
    settings yet: one that has run before without choosing - a cache, a friend's - keeps
    choosing nothing, rather than finding a bitrate window it never set turning
    releases away."""
    stored = read_settings()
    if stored is None or stored:
        return                        # unreadable is not empty: never write over it
    if os.path.exists(settings_path()):
        return                        # there, but saying nothing: not a fresh install
    write_settings(dict(CHOOSING_DEFAULTS))


# the notifications' handler gets what it needs of the server, once everything is defined
pd_notify.attach(read_settings=read_settings, write_settings=write_settings, local=local,
                 put_on_watchlist=put_on_watchlist, release_flat=release_flat,
                 name_of_token=lambda token: Handler.name_of_token(token), peer_holds=peer_holds)


def at_normal_priority():
    """This process at normal priority, whatever started it: started from a build at idle,
    the server and every ffmpeg it ran inherited idle and a 4K film lagged (2026-11-14)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        # the handle typed as one: passed as a plain int on 64-bit it named no process
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x20)      # NORMAL_PRIORITY_CLASS
    except Exception:
        pass


def main():
    at_normal_priority()
    trust_our_own_certificates()
    # One at a time. An installer that starts the program while the Startup shortcut
    # is bringing it up leaves two, and Windows lets the second bind a port the first
    # already holds - so it answers nobody while scanning the same library and
    # writing the same files.
    if not first_on_this_port() or already_serving():
        print("Palladium is already running on port %d" % PORT)
        return
    try:
        choosing_defaults()
    except Exception:
        pass
    # what the first run fetched, if it did. Named before anything imports the engine,
    # which looks for ffmpeg once and remembers what it found.
    told = (read_settings() or {}).get("ffmpeg") or ""
    if told and os.path.exists(told):
        os.environ["PALLADIUM_FFMPEG"] = told
    # and what the server before this one left running: behind the start, not in its way
    threading.Thread(target=stop_left_behind_ffmpeg, name="palladium-orphans",
                     daemon=True).start()
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
        try:
            place_subtitles_off_screen()
        except Exception:
            pass
        # and the subtitles inside films, lifted out while nobody is watching, so that
        # choosing one is instant rather than eight minutes of reading a container
        threading.Thread(target=warm_subtitles, daemon=True).start()
        threading.Thread(target=yield_pack_reads, daemon=True).start()
        # a subtitle being made from the sound when the server last stopped goes on
        import pd_ai_subs
        pd_ai_subs.resume()
        # the API key is purged if it goes unused; this notices before that happens
        threading.Thread(target=keepalive_loop, daemon=True).start()
        # new films appear on their own; the server can notice without being asked
        threading.Thread(target=watch_folders, daemon=True).start()
        # and every file's loudness, measured while nothing is playing
        threading.Thread(target=keep_measuring, name="palladium-measuring",
                         daemon=True).start()
        # and what the shuffles draw next, fetched from the packs before it is drawn
        threading.Thread(target=keep_the_hats, name="palladium-hats", daemon=True).start()
        # and the kept lists, read again from their sources every day
        threading.Thread(target=keep_lists, name="palladium-lists", daemon=True).start()
        # and every active viewer's Start front, made ahead
        threading.Thread(target=keep_app_released, name="palladium-app-release",
                         daemon=True).start()
        # and the pack keys written down, moved onto the titles they name
        threading.Thread(target=keep_pack_keys_retired, name="palladium-pack-keys",
                         daemon=True).start()
        threading.Thread(target=keep_start_ready, name="palladium-start",
                         daemon=True).start()
        # and the watchlists' films not out or not on the tracker yet, looked at for one
        threading.Thread(target=keep_watch_news, name="palladium-watchnews",
                         daemon=True).start()
        # and what has just come in, blinking the Watchlist heading of whoever has it listed
        threading.Thread(target=keep_watch_arrivals, name="palladium-watcharrivals",
                         daemon=True).start()
        # and where each series was made, asked of the catalogue a few at a time
        threading.Thread(target=keep_countries, name="palladium-countries",
                         daemon=True).start()
        # segments left by an iPhone that has stopped watching: gigabytes a film, so
        # they are swept rather than left for the temp folder to accumulate
        threading.Thread(target=sweep_segments, daemon=True).start()
        threading.Thread(target=flush_invites, daemon=True).start()
        # a line every half minute in a file of its own: a silence in it is this
        # process having stopped, and ticks either side of an outage say it did not
        threading.Thread(target=heartbeat, daemon=True).start()
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
        # copies for devices to keep, made behind the workers' gate on the card
        import pd_offline
        pd_offline.start(ROOT, lambda: worker_waits(card=True, in_line=True, idle_priority=True))
        import pd_webget
        pd_webget.use_data_dir(ROOT, scan_after_download)
        import pd_music
        pd_music.use_data_dir(ROOT)
        # which files qBittorrent is still writing, read now rather than by the first
        # title somebody opens: 285 torrents are five seconds the first time. After
        # start(): asked before it, the state was read from nowhere and kept empty
        pd_torrents.warm_unfinished()
        # subtitles read from the blocks a film is played from
        import pd_gpu as _pg
        import pd_localapi
        pd_localapi.PEER_RECENT = peer_recent
        pd_localapi.PEER_ART = peer_art
        pd_localapi.PEER_HOLDS = peer_holds
        pd_localapi.PLACE_SAID = place_said
        threading.Thread(target=keep_peer_recent, name="palladium-peer-recent", daemon=True).start()
        pd_subtap.configure(lambda: _pg.FFMPEG or "ffmpeg",
                            lambda v, i: os.path.exists(sub_cache_path(v, i)), keep_tapped_subtitle)
        # who is running low, so downloading and seeding go gently meanwhile
        start_speed()
        pd_torrents.STATE["pushRequest"] = push_request
        pd_torrents.STATE["copyUp"] = copy_answers
        pd_torrents.PEER["ask"] = ask_other_slot

        # where each title's story ends, measured by day while the machine is idle
        try:
            start_credits()
        except Exception:
            pass
        # which subtitle fits each title, measured against its speech while idle
        try:
            start_subcheck()
        except Exception:
            pass
        # who is in the packs' films, for searching by actor
        try:
            start_cast_index()
        except Exception:
            pass
        # every drive's line: what runs there from outside, read every ten seconds
        try:
            import pd_subs
            pd_subs.keep_allowance(ROOT)
        except Exception:
            pass
        try:
            import pd_workers
            pd_workers.setup(ROOT)
            start_disk_watch()
        except Exception:
            pass
        # the timeline's stills, made ahead for what is being watched and what is new
        try:
            start_stills()
        except Exception:
            pass
        # broken files - the empty shell of a download - mended, one at a time
        try:
            start_fixer()
        except Exception:
            pass
        # the library's files seeded on every upload of the same release
        try:
            start_xseed()
        except Exception:
            pass
        # and the boss watching them all
        try:
            start_boss()
        except Exception:
            pass
        # a new fault read by Claude Code, when the owner has asked for that
        try:
            start_claude()
        except Exception:
            pass
        # this week's picks, drawn on Monday
        try:
            start_weekly()
        except Exception:
            pass
        # whether the two servers are on different connections
        try:
            start_apart()
        except Exception:
            pass

        # What the tracker is carrying, kept by both machines so either can answer.
        # It says which of the films on the shelf there is something to fetch for;
        # what is listed is decided elsewhere, by what people are watching.
        #
        # Reading the feeds is done by both; asking by name only by the machine that
        # is nobody's copy. A search signs in as a person, and one session used from
        # two machines is twice the asking and reads as a shared account.
        # what the old Casual list left behind, once, on either machine
        try:
            forget_old_casual()
        except Exception:
            pass
        try:
            import pd_tracker, pd_follow
            following = pd_follow.settings(local().lib.config()).get("on")
            pd_tracker.ALLOWED["ask"] = lambda: auto_on("search")
            pd_tracker.start(ROOT, may_ask=not following,
                config=lambda: local().lib.config())
            # and the programmes this house keeps up with, looked at on the same
            # clock as the feeds that fill the index. Only the main server fetches:
            # a copy doing it as well is the same episode twice.
            if not following:
                import pd_beat

                def keeping_up():
                    # nothing looked at yet: said as such rather than as not running
                    first = True
                    while True:
                        if first:
                            pd_beat.rest("seasons", 15 * 60, "first check")
                        # requests that can now be downloaded close themselves
                        try:
                            settle_requests()
                        except Exception:
                            pass
                        pd_beat.sleep("episodes", 15 * 60,
                                      "first check" if first else "waiting")
                        first = False
                        try:
                            if auto_on("episodes"):
                                pd_beat.beat("episodes", "looking", 1800)
                                keep_up_with_shows()
                        except Exception:
                            pass
                        # and the programmes chosen off the list of what is being
                        # watched, a season or two at a time, as links
                        try:
                            if auto_on("seasons"):
                                pd_beat.beat("seasons", "looking", 1800)
                                stock_shows()
                        except Exception:
                            pass
                        # looked at again on the episodes' clock
                        pd_beat.rest("seasons", 15 * 60)
                threading.Thread(target=keeping_up, daemon=True).start()
        except Exception:
            pass

        # and the list of what has just come out, read on its own clock rather than
        # by whoever opens the shelf first after it has gone stale
        try:
            import pd_streaming
            pd_streaming.keep_fresh(local().lib)
        except Exception:
            pass

        # Who is in what, read through quietly in the background.
        #
        # A cast fetched only when a page is opened makes "everything with her in it"
        # answer with the one film that was open. This walks the library instead, a
        # batch at a time, and stops when there is nothing left to ask about - so it
        # costs one evening once and nothing afterwards.
        def learn_the_casts():
            time.sleep(90)             # let a scan and the first evening settle first
            while True:
                try:
                    done = local().lib.credits_backlog()
                except Exception:
                    done = 0
                # and the episodes that arrived with a number and nothing else: a
                # new one, or one out of a pack, drawing as a blank card
                try:
                    done += local().lib.episodes_backlog()
                except Exception:
                    pass
                if not done:
                    time.sleep(3600)   # nothing waiting: look again in an hour
                else:
                    time.sleep(20)
        threading.Thread(target=learn_the_casts, name="palladium-casts",
                         daemon=True).start()
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
        setattr(WATCHING, "ledger", pd_traffic.note)
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
                TRAY["icon"] = pd_tray.start_inside()
            except Exception:
                pass                       # no icon is no reason not to serve
        # A tab on the first start only, or when asked for: every update restarts the
        # server, and each restart left another tab open on the machine
        if "--open" in sys.argv or (FIRST_START and "--no-open" not in sys.argv):
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
    if FRONTS_CHILD:
        fronts_child(FRONTS_CHILD)
    else:
        main()
