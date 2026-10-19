#!/usr/bin/env python
"""A fingerprint of the shuffle code, so the build runs the shuffle suite only when it
changed.

    python pd-shuffle-sig.py          prints the fingerprint
    python pd-shuffle-sig.py --same   exit 0 when it matches the last pass, 1 otherwise
    python pd-shuffle-sig.py --keep   writes it down as passed

The shuffle lives in pd-server.py among everything else, so the file as a whole changes
in every build; what is fingerprinted is the shuffle's own functions, its suite and
pd_torrents (whose packs the hat draws from).
"""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEPT = os.path.join(HERE, ".shuffle-passed")
WORDS = re.compile(r"shuffle|hat|shelf_pool|draw|round")


def functions(text):
    """Each top-level or method def whose name says shuffle, with its whole body."""
    out = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)def ([A-Za-z_]\w*)\(", lines[i])
        if m and WORDS.search(m.group(2)):
            indent = len(m.group(1))
            body = [lines[i]]
            i += 1
            while i < len(lines) and (not lines[i].strip() or
                                      len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i])
                i += 1
            out.append("\n".join(body))
            continue
        i += 1
    return out


def sig():
    h = hashlib.sha256()
    with open(os.path.join(HERE, "pd-server.py"), encoding="utf-8") as f:
        for one in functions(f.read()):
            h.update(one.encode("utf-8"))
    for name in ("pd-test-shuffle.py", "pd_torrents.py"):
        with open(os.path.join(HERE, name), "rb") as f:
            h.update(f.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


if __name__ == "__main__":
    now = sig()
    if "--keep" in sys.argv:
        with open(KEPT, "w", encoding="utf-8") as f:
            f.write(now)
    elif "--same" in sys.argv:
        try:
            with open(KEPT, encoding="utf-8") as f:
                was = f.read().strip()
        except OSError:
            was = ""
        sys.exit(0 if was == now else 1)
    else:
        print(now)
