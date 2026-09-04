#!/usr/bin/env python3
"""Ask the server to place the subtitles of every film, and write down what happened.

Sync is the part of this program with the most to go wrong and the least to see: it
either moves the text or says it could not, and one film at a time tells you nothing
about how often it works. This runs it across the library and reports the shape of the
answers - how many placed, how many refused, how long each took, and which films it
could not do anything with.

    python pd-subcheck.py                  twelve films, against a copy of the library
    python pd-subcheck.py --films 40       more of them
    python pd-subcheck.py --live           against the server already running

Nothing is saved: every measurement is made with save=0, so the library's own
corrections are left exactly as they were.

The report names films, so it is written to the temp folder and not into the
repository.
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


def flag(name, fallback):
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return fallback


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(url, timeout=600):
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": "http %d" % e.code}
    except Exception as e:
        return {"error": str(e)[:120]}


def main():
    films = int(flag("--films", "12"))
    live = "--live" in sys.argv
    root, proc, log = None, None, None
    if live:
        base = "http://127.0.0.1:8765"
    else:
        root = tempfile.mkdtemp(prefix="palladium-subs-")
        for name in ("library.db", "library.json", "config.json", "settings.json"):
            src = os.path.join(HERE, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(root, name))
        # the subtitles already lifted out of their films: without these the check
        # spends its time reading containers rather than measuring anything
        cache = os.path.join(HERE, "subcache")
        if os.path.isdir(cache):
            shutil.copytree(cache, os.path.join(root, "subcache"))
        port = free_port()
        log = open(os.path.join(root, "server.log"), "wb")
        proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "pd-server.py"), "--no-open",
             "--port", str(port), "--root", root],
            cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
        base = "http://127.0.0.1:%d" % port
        for _ in range(40):
            if "error" not in ask(base + "/settings", timeout=2):
                break
            time.sleep(0.5)

    shelf = ask(base + "/local/library/sections/1/all")
    rows = (shelf.get("MediaContainer") or {}).get("Metadata") or []
    random.shuffle(rows)
    print("%d films in the library; asking about %d of them\n"
          % (len(rows), min(films, len(rows))))

    lines, counts = [], {"placed": 0, "refused": 0, "unreadable": 0, "none": 0}
    slowest = 0.0
    for film in rows:
        if len(lines) >= films:
            break
        key = film["ratingKey"]
        full = ask(base + "/local/library/metadata/" + key)
        meta = ((full.get("MediaContainer") or {}).get("Metadata") or [{}])[0]
        subs = [st for md in meta.get("Media", [])
                for part in md.get("Part", [])
                for st in part.get("Stream", [])
                if st.get("streamType") == 3]
        if not subs:
            counts["none"] += 1
            continue
        # the one a viewer would be given: English, not forced, not SDH - a forced
        # track holds six lines and is not a subtitle anybody is placing
        def mine(s):
            return (s.get("language") or "").lower().startswith("eng")
        want = (next((s for s in subs if mine(s) and not s.get("forced")
                      and not s.get("sdh")), None)
                or next((s for s in subs if mine(s) and not s.get("forced")), None)
                or next((s for s in subs if not s.get("forced")), None)
                or subs[0])
        index = want.get("index")
        began = time.time()
        said = ask("%s/subs/sync?key=%s&mi=0&index=%s&save=0" % (base, key, index))
        took = time.time() - began
        slowest = max(slowest, took)
        minutes = round((meta.get("duration") or 0) / 60000)
        if said.get("error"):
            counts["unreadable"] += 1
            verdict = "unreadable: " + said["error"][:60]
        elif said.get("sure"):
            counts["placed"] += 1
            verdict = "%s %+.2fs (rate %.6f), sure %.2f" % (
                said.get("kind"), said.get("offset", 0), said.get("rate", 1),
                said.get("confidence", 0))
        else:
            counts["refused"] += 1
            verdict = "refused: would have said %+.2fs, sure %.2f" % (
                said.get("offset", 0), said.get("confidence", 0))
        windows = said.get("windows") or []
        agreed = sum(1 for w in windows if abs(w[2] - (said.get("offset") or 0)) < 0.6)
        line = ("%-42s %4d min  %2d tracks  %5.1fs  %-58s %d/%d windows agreed"
                % (meta.get("title", "")[:42], minutes, len(subs), took, verdict,
                   agreed, len(windows)))
        print(" ", line)
        lines.append(line)

    print("\n%d placed, %d refused, %d unreadable, %d films with no subtitles at all"
          % (counts["placed"], counts["refused"], counts["unreadable"],
             counts["none"]))
    print("slowest measurement: %.1fs" % slowest)

    out = os.path.join(tempfile.gettempdir(), "palladium-subcheck.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("Subtitle placement across the library - %s\n"
                % time.strftime("%Y-%m-%d %H:%M"))
        f.write("Nothing was saved: every measurement made with save=0.\n\n")
        f.write("\n".join(lines))
        f.write("\n\n%d placed, %d refused, %d unreadable, %d without subtitles."
                % (counts["placed"], counts["refused"], counts["unreadable"],
                   counts["none"]))
        f.write("\nSlowest measurement: %.1fs\n" % slowest)
    print("written to", out)

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
