#!/usr/bin/env python3
"""How much went out of this machine, month by month, and what it went out as.

Two kinds of traffic leave a Palladium and they are not the same thing. One is
somebody watching - a film sent to a television, a phone, a friend's browser. The
other is the machine that keeps copies filling itself up, which happens at night, to
nobody, and can be several hundred gigabytes in a week.

Added together they answer no question worth asking. Kept apart, the first is what
the house watched and the second is what the copy cost, and a line in an ISP's bill
can be laid against either. So each is counted in its own row and neither is ever
folded into the other.

Written to a small file beside the settings, once a minute at most: a stream that
sends four gigabytes is one number, not four thousand.
"""
import json
import os
import threading
import time

LOCK = threading.RLock()
STATE = {"path": "", "book": None, "dirty": False, "wrote": 0.0}

#: how the rows are named. "syncing" is what a file going to the following server is
#: filed as; everything else somebody was watching.
KINDS = ("playing", "syncing")

#: at most one write a minute, whatever arrives
EVERY = 60


def use(path):
    """Where to keep the book. Called once, at startup."""
    with LOCK:
        STATE["path"] = path
        STATE["book"] = None


def _book():
    if STATE["book"] is None:
        try:
            with open(STATE["path"], encoding="utf-8") as f:
                said = json.load(f)
            STATE["book"] = said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            STATE["book"] = {}
    return STATE["book"]


def _write(force=False):
    if not STATE["path"] or not STATE["dirty"]:
        return
    if not force and time.time() - STATE["wrote"] < EVERY:
        return
    tmp = "%s.%d.tmp" % (STATE["path"], os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE["book"], f, indent=1)
        os.replace(tmp, STATE["path"])
        STATE["dirty"] = False
        STATE["wrote"] = time.time()
    except OSError:
        pass                          # the count in memory is still right


#: where the list of copies is kept: one line per file that went to another machine
BOOK = {"path": ""}


def use_log(path):
    """Where to write down each copy. Called once, at startup."""
    BOOK["path"] = path


def copies(many=200):
    """The files that have gone to a machine keeping copies, newest first."""
    if not BOOK["path"]:
        return []
    out = []
    try:
        with open(BOOK["path"], encoding="utf-8") as f:
            rows = f.readlines()[-many:]
    except OSError:
        return []
    for line in reversed(rows):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass                      # a half-written line spoils only itself
    return out


def copied_stats():
    """This week, this month and altogether - as the watching figures are given.

    The same three questions people ask of the viewing figures get asked of the
    copying: how much went over, in how many files, and what the largest of them was.
    Read from the same line-per-copy book, which is the only record of it.
    """
    now = time.time()
    spans = {"week": now - 7 * 86400, "month": now - 30 * 86400, "total": 0}
    rows = copies(100000)
    out = {}
    for name, since in spans.items():
        mine = [r for r in rows if float(r.get("when") or 0) >= since]
        gb = sum(float(r.get("gb") or 0) for r in mine)
        titles = {str(r.get("title") or r.get("key") or "") for r in mine}
        big = max(mine, key=lambda r: float(r.get("gb") or 0), default=None)
        where = {}
        for r in mine:
            to = str(r.get("to") or r.get("address") or "")
            if to:
                where[to] = round(where.get(to, 0.0) + float(r.get("gb") or 0), 1)
        out[name] = {
            "gb": round(gb, 1),
            "files": len(mine),
            "titles": len([t for t in titles if t]),
            "top": ({"title": big.get("title") or big.get("key") or "a file",
                     "gb": round(float(big.get("gb") or 0), 2)} if big else None),
            "where": [{"who": k, "gb": v} for k, v in
                      sorted(where.items(), key=lambda kv: -kv[1])[:3]],
        }
    return out


def note(sent, how="", row=None, when=None):
    """One stream's worth of bytes, filed under the month it finished in."""
    if not sent or sent < 0:
        return
    kind = "syncing" if str(how).startswith("syncing") else "playing"
    month = time.strftime("%Y-%m", time.localtime(when or time.time()))
    with LOCK:
        book = _book()
        held = book.setdefault(month, {})
        held[kind] = int(held.get(kind, 0)) + int(sent)
        STATE["dirty"] = True
        _write()
    # and a line of its own for a copy: what it was, where it went, how big
    if kind == "syncing" and BOOK["path"] and row:
        try:
            with open(BOOK["path"], "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "when": int(when or time.time()),
                    "title": row.get("title") or "",
                    "key": row.get("key") or "",
                    "gb": round(int(sent) / 1e9, 3),
                    "to": row.get("who") or "",
                    "address": row.get("address") or "",
                }) + chr(10))
        except OSError:
            pass                      # a note nobody kept is not worth a fault


def flush():
    """Put the book on disk. Called when the server is closing down."""
    with LOCK:
        _write(force=True)


def read(months=13):
    """The last so many months, newest first, and the years they add up to.

    Gigabytes rather than bytes: nobody reads a bill in bytes, and the rows are for
    reading.
    """
    with LOCK:
        book = dict(_book())
    out, years = [], {}
    for month in sorted(book.keys(), reverse=True):
        row = book[month] or {}
        one = {"month": month}
        for kind in KINDS:
            one[kind] = round(int(row.get(kind, 0)) / 1e9, 1)
        out.append(one)
        year = years.setdefault(month.split("-")[0],
                                {"year": month.split("-")[0],
                                 "playing": 0.0, "syncing": 0.0})
        for kind in KINDS:
            year[kind] = round(year[kind] + one[kind], 1)
    return {"months": out[:months],
            "years": sorted(years.values(), key=lambda y: y["year"], reverse=True)}
