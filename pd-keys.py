#!/usr/bin/env python3
"""Beta keys: who may download the installer, how often, and until when.

A key is not an account. It says one thing - this person was let into the beta - and
it carries the two limits that make a closed beta closed: how many downloads it is
good for, and when it stops working. Revoking one is a line in this file, not a
conversation with anybody's e-mail provider.

    py pd-keys.py new "Johan" --uses 3 --days 60     mint one
    py pd-keys.py list                               who has one, and what is left
    py pd-keys.py revoke 7QK3-2M8P                   stop it working
    py pd-keys.py export keys.json                   the file the Worker's KV wants

The store lives beside the server's own settings and never leaves this machine; only
`export` produces something to upload, and that carries no names.
"""
import json
import os
import re
import secrets
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "betakeys.json")

#: No vowels and no look-alikes: a key gets read down a telephone and typed by hand,
#: and 0/O and 1/I are where that goes wrong.
ALPHABET = "23456789CDFGHJKLMNPQRSTVWXZ"


def load():
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        # a broken store is worth saying so about rather than quietly starting again
        print("betakeys.json could not be read; nothing was changed")
        raise SystemExit(1)


def save(rows):
    tmp = "%s.%d.tmp" % (STORE, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    os.replace(tmp, STORE)


def mint():
    """Two groups of four, which is short enough to type and long enough to be safe."""
    pick = lambda n: "".join(secrets.choice(ALPHABET) for _ in range(n))
    return pick(4) + "-" + pick(4)


def tidy(key):
    return re.sub(r"[^0-9A-Z]", "", (key or "").upper())


def new(name, uses, days):
    rows = load()
    key = mint()
    while any(r["key"] == key for r in rows):
        key = mint()
    rows.append({
        "key": key,
        "who": name,
        "uses": int(uses),
        "used": 0,
        "made": int(time.time()),
        "until": int(time.time()) + int(days) * 86400 if int(days) else 0,
        "revoked": False,
    })
    save(rows)
    print(key, " for", name)
    return key


def show():
    rows = load()
    if not rows:
        print("no keys yet")
        return
    now = int(time.time())
    for r in rows:
        left = "revoked" if r.get("revoked") else (
            "expired" if r.get("until") and r["until"] < now
            else "%d of %d left" % (max(0, r["uses"] - r["used"]), r["uses"]))
        when = ("until " + time.strftime("%Y-%m-%d", time.localtime(r["until"]))
                if r.get("until") else "no end")
        print("%-11s %-22s %-16s %s" % (r["key"], r["who"][:22], left, when))


def revoke(key):
    key = tidy(key)
    rows = load()
    for r in rows:
        if tidy(r["key"]) == key:
            r["revoked"] = True
            save(rows)
            print(r["key"], "revoked")
            return
    print("no such key")


def export(path):
    """What the gate needs and nothing else: no names leave this machine."""
    out = {}
    for r in load():
        if r.get("revoked"):
            continue
        out[tidy(r["key"])] = {"uses": r["uses"], "used": r["used"],
                               "until": r.get("until") or 0}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(len(out), "keys written to", path)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    what = argv[0]
    if what == "new":
        name = argv[1] if len(argv) > 1 else "beta"
        uses = 3
        days = 90
        if "--uses" in argv:
            uses = argv[argv.index("--uses") + 1]
        if "--days" in argv:
            days = argv[argv.index("--days") + 1]
        new(name, uses, days)
    elif what == "list":
        show()
    elif what == "revoke" and len(argv) > 1:
        revoke(argv[1])
    elif what == "export":
        export(argv[1] if len(argv) > 1 else os.path.join(HERE, "keys.json"))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
