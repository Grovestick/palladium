"""The report board kept as one list on the main server and the cache.

Each machine wrote what was reported to it into its own feedback.jsonl, so a request
made while the cache was answering stayed on the cache and the main server's page never
showed it. The cache now trades its board with the main server's: every row by its id,
the copy changed last winning, and both machines write the result back. Nothing on the
board is ever deleted - clearing moves a row to the archive - so a merge by id loses
nothing.
"""
import json
import os
import threading
import time

BOARD = "feedback.jsonl"
ARCHIVE = "feedback-archive.jsonl"

#: one rewrite at a time on this machine
LOCK = threading.Lock()


def _lines(path):
    """The rows of one file, or None when it is there and cannot be read."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            out.append(row)
    return out


def read(root):
    """Every row on the board and in the archive; an archived row carries "filed".
    None when either file cannot be read: unreadable is not empty."""
    board = _lines(os.path.join(root, BOARD))
    archive = _lines(os.path.join(root, ARCHIVE))
    if board is None or archive is None:
        return None
    for row in board:
        row.pop("filed", None)
    for row in archive:
        if not row.get("filed"):
            row["filed"] = int(row.get("when") or 1)
    return board + archive


def stamp(row):
    """When a row last changed: written, amended, filed or put back."""
    return max(int(row.get("changed") or 0), int(row.get("filed") or 0),
               int(row.get("when") or 0))


def merge(first, second):
    """Both lists as one, by id. Where both hold a row the copy changed last wins, and
    on a tie the first - the main server's."""
    out = {}
    for row in first or []:
        out.setdefault(str(row["id"]), row)
    for row in second or []:
        ident = str(row.get("id") or "")
        if not ident:
            continue
        had = out.get(ident)
        if had is None or stamp(row) > stamp(had):
            out[ident] = row
    return list(out.values())


def _write_one(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + chr(10))
    os.replace(tmp, path)


def write(root, rows):
    """The rows back out: the board in the order they were written, the archive in
    the order they were filed."""
    board = sorted((r for r in rows if not r.get("filed")),
                   key=lambda r: (int(r.get("when") or 0), str(r["id"])))
    archive = sorted((r for r in rows if r.get("filed")),
                     key=lambda r: (int(r.get("filed") or 0), int(r.get("when") or 0),
                                    str(r["id"])))
    _write_one(os.path.join(root, BOARD), board)
    _write_one(os.path.join(root, ARCHIVE), archive)


def take(root, theirs, first_is_mine=True):
    """Merge another machine's rows into this one's files and say what both now hold.
    The main server's copy wins ties: first_is_mine there, not on the cache."""
    with LOCK:
        mine = read(root)
        if mine is None:
            return None
        both = merge(mine, theirs) if first_is_mine else merge(theirs, mine)
        if _key(both) != _key(mine):
            write(root, both)
        return both


def _key(rows):
    return sorted((str(r["id"]), stamp(r), bool(r.get("filed"))) for r in rows)


#: when the cache last traded its board
TRADE = {"at": 0.0}
TRADE_EVERY = 60


def trade(root, tell):
    """On the cache: send this board to the main server and keep what it answers.
    `tell` posts a body to the main server and returns its answer."""
    if time.time() - TRADE["at"] < TRADE_EVERY:
        return None
    TRADE["at"] = time.time()
    with LOCK:
        mine = read(root)
    if mine is None:
        return None
    said = tell("/follow/feedback", {"rows": mine}) or {}
    theirs = said.get("rows")
    if not isinstance(theirs, list):
        return None
    # theirs first: the main server's word on a tie, and anything reported here since
    # the send is kept by the merge
    return take(root, theirs, first_is_mine=False)
