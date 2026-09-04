#!/usr/bin/env python3
"""Leave a server playing for a while and watch what it does to itself.

The smoke test proves a fresh server answers. This proves it is still the same server
an hour later: memory, handles, threads, and how many ffmpeg processes are alive that
nobody is watching any more.

It runs against a copy of the real library - the index and the folder list, not the
settings - on a port of its own, so the server you use is untouched.

    python pd-soak.py                 twenty-five minutes
    python pd-soak.py --minutes 120   two hours
    python pd-soak.py --keep          leave the papers folder behind

Exit code 0 if nothing grew and nothing was left running.
"""
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

#: How much of a stream to pull each time. Enough to make ffmpeg work, small enough
#: that a long soak does not fill a disk with nothing.
CHUNK = 4 * 1024 * 1024


def flag(name, fallback):
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return fallback


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(url, timeout=30):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:
        return 0, str(e)[:120].encode()


def pull(url, bytes_wanted=CHUNK, timeout=60):
    """Read the beginning of a stream and hang up, the way a player that stops does."""
    got = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            while got < bytes_wanted:
                block = r.read(256 * 1024)
                if not block:
                    break
                got += len(block)
    except Exception:
        pass
    return got


def sample(pid):
    """Memory, handles and threads for one process, and how many ffmpegs are alive."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$p = Get-Process -Id %d -ErrorAction SilentlyContinue;"
         "$f = @(Get-Process -Name ffmpeg -ErrorAction SilentlyContinue).Count;"
         "if ($p) { '{0} {1} {2} {3}' -f $p.WorkingSet64, $p.HandleCount,"
         "$p.Threads.Count, $f } else { '0 0 0 ' + $f }" % pid],
        capture_output=True, text=True)
    try:
        rss, handles, threads, ffmpegs = out.stdout.split()
        return int(rss), int(handles), int(threads), int(ffmpegs)
    except Exception:
        return 0, 0, 0, 0


def main():
    minutes = float(flag("--minutes", "25"))
    keep = "--keep" in sys.argv
    root = tempfile.mkdtemp(prefix="palladium-soak-")
    # the library it already has, so there is something real to play
    for name in ("library.db", "library.json", "config.json"):
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(root, name))
    port = free_port()
    log = open(os.path.join(root, "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "pd-server.py"), "--no-open",
         "--port", str(port), "--root", root],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(40):
        if ask(base + "/settings", timeout=2)[0]:
            break
        time.sleep(0.5)

    # what there is to play
    keys = []
    for section in ("1", "2"):
        code, body = ask("%s/local/library/sections/%s/all" % (base, section))
        if code != 200:
            continue
        for m in json.loads(body)["MediaContainer"].get("Metadata") or []:
            if m.get("type") == "movie":
                keys.append(m["ratingKey"])
            elif m.get("type") == "show":
                got = ask("%s/local/library/metadata/%s/children"
                          % (base, m["ratingKey"]))
                if got[0] == 200:
                    for season in json.loads(got[1])["MediaContainer"].get(
                            "Metadata") or []:
                        eps = ask("%s/local/library/metadata/%s/children"
                                  % (base, season["ratingKey"]))
                        if eps[0] == 200:
                            keys += [e["ratingKey"] for e in
                                     json.loads(eps[1])["MediaContainer"].get(
                                         "Metadata") or []][:3]
                        break
    random.shuffle(keys)
    keys = keys[:40]
    print("soaking for %g minutes on port %d, %d titles to play from"
          % (minutes, port, len(keys)))
    if not keys:
        print("nothing in the library to play - the soak would prove nothing")

    OTHER = ["/settings", "/nowplaying", "/watching", "/marks", "/library/status",
             "/gpu/status", "/local/library/sections", "/setup/state",
             "/local/hubs/search?query=the", "/watchlog?limit=20", "/changes"]

    first = None
    worst = (0, 0, 0, 0)
    rounds = 0
    started = time.time()
    ends = started + minutes * 60
    nextsample = 0.0
    while time.time() < ends:
        rounds += 1
        if keys:
            key = random.choice(keys)
            offset = random.choice([0, 60, 300, 900, 1500])
            pull("%s/gpu/stream?src=local&key=%s&offset=%d&height=0&mi=0"
                 % (base, key, offset))
            ask("%s/gpu/subs?src=local&key=%s&index=-1&mi=0&offset=%d"
                % (base, key, offset), timeout=45)
            ask("%s/subs/plan?key=%s&mi=0&index=-1" % (base, key))
        for path in random.sample(OTHER, 5):
            ask(base + path)
        if time.time() >= nextsample:
            nextsample = time.time() + 15
            now = sample(proc.pid)
            if first is None:
                first = now
            worst = tuple(max(a, b) for a, b in zip(worst, now))
            print("  %4ds  rss %6.1f MB   handles %5d   threads %4d   ffmpeg %d"
                  % (time.time() - started, now[0] / 1e6, now[1], now[2], now[3]))
        time.sleep(2)

    last = sample(proc.pid)
    code, body = ask(base + "/gpu/status")
    left = len(json.loads(body).get("streams", [])) if code == 200 else -1
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
    time.sleep(2)
    stragglers = sample(0)[3]
    log.close()
    with open(os.path.join(root, "server.log"), encoding="utf-8",
              errors="replace") as f:
        spew = f.read()
    tracebacks = spew.count("Traceback (most recent call last)")

    print("\n%d rounds in %g minutes" % (rounds, minutes))
    names = ["memory", "handles", "threads"]
    grew = False
    for i, name in enumerate(names):
        a, b, c = first[i], last[i], worst[i]
        if i == 0:
            a, b, c = a / 1e6, b / 1e6, c / 1e6
        rise = (b - a) / a * 100 if a else 0
        print("  %-8s start %8.1f   end %8.1f   worst %8.1f   %+.0f%%"
              % (name, a, b, c, rise))
        if rise > 50:
            grew = True
    print("  sessions still open at the end:", left)
    print("  ffmpeg processes after the server stopped:", stragglers)
    print("  tracebacks:", tracebacks)
    if tracebacks:
        print("\n".join(l for l in spew.splitlines() if l.strip())[-2000:])
    if keep:
        print("papers left in", root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if (grew or tracebacks or stragglers) else 0


if __name__ == "__main__":
    sys.exit(main())
