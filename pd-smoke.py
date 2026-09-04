#!/usr/bin/env python3
"""Start a fresh Palladium and ask it for everything, to see what breaks.

A new install is the state nobody develops in and everybody starts in: no settings,
no library, no TMDB key, no films. This starts the server with a folder of its own on
a port of its own, walks every page and endpoint, and reports anything that answers
with a 500 or writes a traceback.

    python pd-smoke.py                     the source
    python pd-smoke.py --exe build/Palladium/palladium-server.exe   the built one
    python pd-smoke.py --keep              leave the temporary folder to look at

The --exe form is the one that matters before a release: a compiled build has its own
ways of failing - a module nobody imported by name, a file that was never copied in -
and none of them show up when the source is run.

Exit code 0 when nothing failed, 1 otherwise, so it can sit in a build.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

#: Everything a browser or an app asks for on its own, in the order it asks. A fresh
#: server has nothing in it, so the right answer is usually "nothing" - what is being
#: tested is that it says so rather than falling over.
GETS = [
    "/",
    # the first thing any page asks for, and the one that stopped a new install dead
    "/config",
    "/index.html",
    "/app.js",
    "/style.css",
    "/manifest.webmanifest",
    "/changes",
    "/settings",
    "/settings?device=tv",
    "/settings?device=phone",
    "/nowplaying",
    "/watching",
    "/watchlog?limit=5",
    "/marks",
    "/invites",
    "/casual",
    "/casual/peek",
    "/watchlist",
    "/ondeck/aside",
    "/feedback",
    "/library/status",
    "/library/config",
    "/app/version",
    "/gpu/status",
    "/mystream",
    "/local/library/sections",
    "/local/library/sections/1/all",
    "/local/library/sections/2/all",
    "/local/hubs/search?query=test",
    "/local/hubs/search?query=s4e9",
    "/local/library/metadata/e1",
    "/subs/plan?key=l1&mi=0&index=-1",
    "/setup/state",
    "/auth/state",
]

#: The ones that change something. A fresh server has nothing to change, so these are
#: about the refusal being an answer rather than a crash.
POSTS = [
    ("/feedback/seen", {}),
    ("/settings", {"clock": "24"}),
    ("/settings", {"autoSync": True}),
    ("/settings", {"subtitles": {"size": 1.0, "position": 0.08, "colour": "white",
                                 "background": "shadow", "font": "sans"},
                   "device": "web"}),
    ("/subs/auto", {"key": "e1"}),
    ("/subs/reset", {"key": "l1", "mi": 0, "index": -1}),
    ("/casual/reset", {}),
    ("/library/scan", {}),
    # asks machines in other countries to knock on this door: slower than anything
    # else here, and answered by somebody else's server
    ("/network/check", {}, 40),
    ("/setup/done", {}),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(url, body=None, timeout=25):
    """(status, first line of the body) - never raises for an HTTP answer."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            said = r.read(400).decode("utf-8", "replace").replace("\n", " ")
            return r.status, said[:120]
    except urllib.error.HTTPError as e:
        said = e.read(400).decode("utf-8", "replace").replace("\n", " ")
        return e.code, said[:120]
    except Exception as e:
        return 0, str(e)[:120]


def main():
    keep = "--keep" in sys.argv
    root = tempfile.mkdtemp(prefix="palladium-smoke-")
    port = free_port()
    log = open(os.path.join(root, "server.log"), "wb")
    exe = None
    for i, arg in enumerate(sys.argv):
        if arg == "--exe" and i + 1 < len(sys.argv):
            exe = os.path.abspath(sys.argv[i + 1])
    start = ([exe] if exe else [sys.executable, os.path.join(HERE, "pd-server.py")])
    proc = subprocess.Popen(
        start + ["--no-open", "--port", str(port), "--root", root],
        cwd=os.path.dirname(exe) if exe else HERE,
        stdout=log, stderr=subprocess.STDOUT)
    print("under test:", exe or "the source")
    base = "http://127.0.0.1:%d" % port
    print("fresh server on %d, papers in %s" % (port, root))

    up = False
    for _ in range(40):
        if ask(base + "/settings", timeout=2)[0]:
            up = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    bad = []
    if not up:
        print("it never came up")
        bad.append(("start", 0, "no answer"))
    else:
        for path in GETS:
            code, said = ask(base + path)
            mark = "ok " if 200 <= code < 500 else "BAD"
            if code == 0 or code >= 500:
                bad.append((path, code, said))
            print("  %s %-46s %s  %s" % (mark, path, code, said[:60]))
        for case in POSTS:
            # a few of these are slower than the rest and say so themselves
            path, body = case[0], case[1]
            code, said = ask(base + path, body,
                             timeout=(case[2] if len(case) > 2 else 25))
            mark = "ok " if 200 <= code < 500 else "BAD"
            if code == 0 or code >= 500:
                bad.append((path, code, said))
            print("  %s POST %-41s %s  %s" % (mark, path, code, said[:60]))

    time.sleep(1)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log.close()
    with open(os.path.join(root, "server.log"), encoding="utf-8",
              errors="replace") as f:
        spew = f.read()
    tracebacks = spew.count("Traceback (most recent call last)")
    if tracebacks:
        print("\n%d traceback(s) in the server's own output:" % tracebacks)
        keep_lines = [l for l in spew.splitlines() if l.strip()]
        print("\n".join(keep_lines[-40:]))
    if not keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print("\nleft behind:", root)

    print("\n%d requests, %d failed, %d tracebacks"
          % (len(GETS) + len(POSTS), len(bad), tracebacks))
    for path, code, said in bad:
        print("   %-46s %s  %s" % (path, code, said))
    return 1 if (bad or tracebacks) else 0


if __name__ == "__main__":
    sys.exit(main())
