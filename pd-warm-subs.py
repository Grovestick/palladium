#!/usr/bin/env python3
"""Lift every embedded subtitle out of every film now, rather than when it is wanted.

The server does this by itself when nothing is playing. This is the same work, asked
for: useful after adding a lot of films, or when somebody is about to sit down and
does not want to wait eight minutes for a track to come out of a container.

    python pd-warm-subs.py                 everything not already lifted
    python pd-warm-subs.py --key 2633      one title first, then the rest
    python pd-warm-subs.py --only 2633     one title and stop

Nothing is downloaded and nothing is changed: the text comes out of the films that are
already here, and is kept beside the settings.
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")


def flag(name, fallback=""):
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return fallback


def main():
    # the server's own two functions, so the cache it fills is the one the server reads
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pdserver", os.path.join(HERE, "pd-server.py"))
    pd = importlib.util.module_from_spec(spec)
    sys.modules["pdserver"] = pd
    spec.loader.exec_module(pd)

    first = flag("--key") or flag("--only")
    only = bool(flag("--only"))
    con = sqlite3.connect(os.path.join(HERE, "library.db"))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT path, streams, item_id, episode_id FROM file "
        "ORDER BY COALESCE(ctime, mtime) DESC").fetchall()
    con.close()

    work = []
    for row in rows:
        try:
            tracks = json.loads(row["streams"] or "[]")
        except Exception:
            continue
        mine = first and (str(row["item_id"]) == first
                          or ("e" + str(row["episode_id"] or "")) == first)
        if only and not mine:
            continue
        for track in tracks:
            if (track.get("codec") or "").lower() not in TEXT_CODECS:
                continue
            where = pd.sub_cache_path(row["path"], track.get("index"))
            if os.path.exists(where) or not os.path.exists(row["path"]):
                continue
            work.append((0 if mine else 1, row["path"], track.get("index")))
    work.sort(key=lambda w: w[0])

    print("%d tracks to lift out" % len(work))
    done, failed = 0, 0
    for _, video, index in work:
        began = time.time()
        try:
            said = pd.pull_subtitle(video, index)
            cues = said.count("-->")
        except Exception as e:
            print("  %-52s failed: %s" % (os.path.basename(video)[:52], str(e)[:40]))
            failed += 1
            continue
        done += 1
        print("  %-52s track %-3s %5d cues  %5.1fs"
              % (os.path.basename(video)[:52], index, cues, time.time() - began))
    print("\n%d lifted, %d failed" % (done, failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
