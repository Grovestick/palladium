#!/usr/bin/env python3
"""Fold one viewer's history into another's, once.

Somebody who watches at the desk as the owner and on the television through an
invitation is one person with two histories: two Continue watching shelves, two
watchlists, two shuffles, and a figures page that says two people. This moves
everything filed under one name to the other and leaves nothing behind.

    py pd-merge-viewer.py me ZCUmqVHa...        move the owner's viewing to that key
    py pd-merge-viewer.py --dry me ZCUmqVHa...  say what would move, change nothing

The later note always wins where both had one: two servers, or two screens, writing
in the same book. Nothing is deleted from the library - only who each row belongs to.
"""
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.join(os.environ.get("APPDATA", ""), "Palladium")

#: What belongs to a viewer rather than to the machine. The owner's are kept at the
#: top level of settings.json, mixed in with the server's own, so they are moved by
#: name rather than wholesale - the port and the library folders are not viewing.
VIEWER_KEYS = (
    "watchlist", "casual", "casualNext", "casualPlayed", "casualRun", "casualTotal",
    "casualAt", "casualQueue", "casualOrder", "subtitles", "perTitle", "myAccent",
    "autoNext", "autoFetch", "autoSync", "watchParty", "subLanguage", "mine",
)


def merge_settings(stored, frm, to, dry=False):
    """Move the viewing settings of one viewer onto another. Returns what moved."""
    users = stored.setdefault("users", {})
    mine = stored if frm == "me" else (users.get(frm) or {})
    theirs = users.setdefault(to, {}) if to != "me" else stored
    moved = []
    for key in VIEWER_KEYS:
        if key not in mine:
            continue
        was, now = mine.get(key), theirs.get(key)
        if isinstance(was, list):
            # two shelves of the same person's, which is one shelf
            joined = list(dict.fromkeys(list(now or []) + list(was)))
            if not dry:
                theirs[key] = joined
            moved.append("%s (%d + %d = %d)" % (key, len(now or []), len(was),
                                                len(joined)))
        elif isinstance(was, dict) and isinstance(now, dict):
            # a setting per screen, a count per way of playing: neither side is
            # wrong, and the one being moved is the one being used more
            joined = dict(now)
            for k, v in was.items():
                if isinstance(v, list) and isinstance(joined.get(k), list):
                    joined[k] = list(dict.fromkeys(joined[k] + v))
                elif isinstance(v, (int, float)) and isinstance(joined.get(k),
                                                                (int, float)):
                    joined[k] = max(v, joined[k])
                else:
                    joined[k] = v
            if not dry:
                theirs[key] = joined
            moved.append("%s (merged, %d keys)" % (key, len(joined)))
        elif now is None or was is not None:
            if not dry:
                theirs[key] = was
            moved.append(key if now is None else key + " (took yours)")
        else:
            moved.append(key + " (kept theirs)")
        if not dry and frm == "me":
            stored.pop(key, None)
    if not dry and frm != "me":
        users.pop(frm, None)
    return moved


def merge_rows(con, frm, to, dry=False):
    """watchlog and progress, row by row. The later note wins."""
    log = con.execute("SELECT COUNT(*) c FROM watchlog WHERE who=?", (frm,)).fetchone()[0]
    if not dry:
        con.execute("UPDATE watchlog SET who=? WHERE who=?", (to, frm))
    # progress has one row per viewer and key, so a clash is a real question
    kept, taken = 0, 0
    for row in con.execute(
            "SELECT key, position, duration, updated FROM progress WHERE who=?",
            (frm,)).fetchall():
        had = con.execute("SELECT updated FROM progress WHERE who=? AND key=?",
                          (to, row[0])).fetchone()
        if had and int(had[0] or 0) >= int(row[3] or 0):
            kept += 1
            continue
        taken += 1
        if not dry:
            con.execute(
                """INSERT INTO progress (key, position, duration, updated, who)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(who, key) DO UPDATE SET position=excluded.position,
                   duration=excluded.duration, updated=excluded.updated""",
                (row[0], row[1], row[2], row[3], to))
    if not dry:
        con.execute("DELETE FROM progress WHERE who=?", (frm,))
        con.commit()
    return log, taken, kept


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    frm, to = args
    spath = os.path.join(ROOT, "settings.json")
    dbpath = os.path.join(ROOT, "library.db")
    with open(spath, encoding="utf-8") as f:
        stored = json.load(f)
    if not dry:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(spath, spath + "." + stamp + ".bak")
        shutil.copyfile(dbpath, dbpath + "." + stamp + ".bak")
        print("copies kept beside the originals, stamped", stamp)

    con = sqlite3.connect(dbpath)
    try:
        log, taken, kept = merge_rows(con, frm, to, dry)
    finally:
        con.close()
    print("watchlog rows moved:", log)
    print("positions moved:", taken, "- already newer on the other side:", kept)
    moved = merge_settings(stored, frm, to, dry)
    print("settings moved:", ", ".join(moved) or "nothing")
    if not dry:
        # and from now on the owner is that viewer, so nothing accumulates under the
        # name that has just been emptied
        if frm == "me":
            stored["ownerIs"] = to
        tmp = spath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)
        os.replace(tmp, spath)
        print("written. The owner now watches as", to)
    else:
        print("(dry run - nothing was written)")


if __name__ == "__main__":
    main(sys.argv[1:])
