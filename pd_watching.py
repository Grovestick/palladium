#!/usr/bin/env python3
"""Who is watching what, right now, and how fast it is going out.

Nothing here is stored: a session exists while a socket is open and vanishes when it
closes. Bytes are sampled into a short rolling window rather than counted as an
average since the start, because the interesting number is what the line is carrying
this minute - a paused film still has a large total and a rate of zero.
"""
import itertools
import json
import os
import threading
import time

# Long enough to hold a burst and the pause after it: a direct play delivers in
# spurts, and a window shorter than the gaps reads zero for most of a film.
WINDOW = 20.0
PEAK_WINDOW = 3.0        # what "fastest" is measured over
# How long a viewing is held on the list after its last socket closed. A direct play
# is a run of range requests, not one connection: the browser asks for a stretch of
# the file, reads it, and comes back a moment later. Each one is a session opening
# and closing, so a list built from open sockets alone flickered - a row appearing
# and vanishing several times a minute while somebody sat watching one film.
LINGER = 30.0
#: how long what a viewing has taken is remembered, so a seek or a pause
#: does not start the count again. Longer than LINGER.
CARRIED = 900.0
#: how long a viewing's total waits through a pause: an evening, not a quarter of an
#: hour - paused twenty minutes, an episode counted from nought again and finished well
#: short of its file's size
SITTING_KEEP = 4 * 3600.0
_next_id = itertools.count(1)


class Watching:
    def __init__(self):
        self.lock = threading.Lock()
        self.live = {}
        # the last state of viewings whose sockets have closed, by who and what
        self.gone = {}
        # bytes per viewing across all its requests, sampled for the rate: a direct
        # play reads in chunks, and one chunk over its own short life measured
        # several times the film's bitrate
        self.flows = {}
        # what a viewing had taken when its last stream closed, so carrying on after
        # a seek or a pause adds to the sitting instead of starting from nothing
        self.spent = {}
        # what a sitting has taken altogether, by person, title and screen - not by
        # address, which a television changes between the house's two doors - added
        # to by every byte of every stream, and forgotten after a quarter of an hour
        # with nothing sent
        self.sitting = {}

    def keep_in(self, folder):
        """The sittings written down every half minute and read back at a start: an
        install restarts the server, and a film carried on through it counted its
        megabytes from nought - the panel said 22 MB where the television said 500."""
        where = os.path.join(folder, "sittings.json")
        try:
            with open(where, encoding="utf-8") as f:
                rows = json.load(f)
            now = time.time()
            with self.lock:
                for k, t, n in rows:
                    if now - float(t) < SITTING_KEEP:
                        self.sitting[tuple(k)] = (float(t), int(n))
        except Exception:
            pass

        def write():
            while True:
                time.sleep(30)
                with self.lock:
                    rows = [[list(k), v[0], v[1]] for k, v in self.sitting.items()]
                try:
                    with open(where + ".new", "w", encoding="utf-8") as f:
                        json.dump(rows, f)
                    os.replace(where + ".new", where)
                except Exception:
                    pass
        threading.Thread(target=write, daemon=True).start()

    def start(self, who, title, quality, how, address, key="", app="", device="",
              # why this file is being sent and whose watching asked for it. A log of
              # what moved says nothing about why it moved, and the reason is three
              # functions away by the time the bytes are going out.
              why="", asked_for="", path="", offset=0, size=0):
        sid = next(_next_id)
        with self.lock:
            row = {
                "id": sid, "who": who, "title": title, "quality": quality,
                "how": how, "address": address, "started": time.time(),
                # what is playing it: the build the client says it is, and what kind
                # of thing that is. A fault report reads differently from a
                # three-week-old build than from today's.
                "app": app, "kind": device,
                # which title this is, so the player's own reports - where it is, and
                # whether it is running - can be matched to it
                "key": str(key or ""),
                # the file itself, so a machine keeping copies can be told which of
                # them are being read this minute
                "path": str(path or ""),
                "why": str(why or ""), "for": str(asked_for or ""),
                # a copy: where in the file this stream began, and the file's size
                "offset": int(offset or 0), "size": int(size or 0),
                "bytes": 0, "marks": [(time.time(), 0)], "peak": 0.0,
            }
            # carrying on with the same title on the same screen: the sitting keeps
            # what it had taken before the stream was closed and opened again
            now = time.time()
            mark = self._same(row)
            for old in list(self.spent):
                if now - self.spent[old][0] > CARRIED:
                    del self.spent[old]
            for old in list(self.sitting):
                if now - self.sitting[old][0] > SITTING_KEEP:
                    del self.sitting[old]
            had = self.spent.get(mark)
            if had:
                row["bytes"] = int(had[1] or 0)
                row["started"] = min(row["started"], float(had[2] or row["started"]))
                row["peak"] = max(row["peak"], float(had[3] or 0.0))
                row["marks"] = [(now, row["bytes"])]
            self.live[sid] = row
        return sid

    def paths(self):
        """Every file being watched now, however briefly nothing is open on it.

        A player reads a stretch, closes the connection and comes back a moment
        later, so for most of a film there is no handle on the file at all - which is
        why a sweep that trusted the lock to refuse it deleted a film somebody was
        watching, between two of its requests.
        """
        with self.lock:
            return {os.path.normcase(s.get("path") or "")
                    for s in self.live.values() if s.get("path")}

    @staticmethod
    def joined(parts):
        """Several sockets for one viewing, read as one.

        Bytes add up, the clock runs from the first of them, and the fastest moment
        of any of them is the fastest moment of the viewing.
        """
        if len(parts) == 1:
            return parts[0]
        first = min(parts, key=lambda p: p["started"])
        out = dict(first)
        out["bytes"] = sum(p["bytes"] for p in parts)
        out["peak"] = max(p["peak"] for p in parts)
        # the window is measured from the oldest mark any of them holds, and the bytes
        # counted against it are all of them
        oldest = min(p["marks"][0][0] for p in parts)
        out["marks"] = [(oldest, 0)]
        return out

    def sent(self, sid, n):
        with self.lock:
            s = self.live.get(sid)
            if not s:
                return
            s["bytes"] += n
            now = time.time()
            seat = self._seat(s)
            was = self.sitting.get(seat)
            self.sitting[seat] = (now, (was[1] if was and now - was[0] < SITTING_KEEP else 0) + n)
            mark = self._same(s)
            f = self.flows.get(mark)
            if f is None or now - f["marks"][-1][0] > LINGER + WINDOW:
                f = self.flows[mark] = {"first": now, "bytes": 0, "peak": 0.0,
                                        "marks": [(now, 0)]}
            f["bytes"] += n
            if now - f["marks"][-1][0] >= 0.25:
                f["marks"].append((now, f["bytes"]))
                # one mark at or before the cut stays, so the rate spans the window
                cut = now - WINDOW
                while len(f["marks"]) > 2 and f["marks"][1][0] <= cut:
                    f["marks"].pop(0)
                recent = [m for m in f["marks"] if now - m[0] <= PEAK_WINDOW]
                if len(recent) > 1 and now - recent[0][0] >= 1.0:
                    f["peak"] = max(f["peak"], (f["bytes"] - recent[0][1])
                                    / (now - recent[0][0]) / 1048576.0)
            # four times a second: a burst that lasts two seconds is then several
            # points rather than one, and the rate it implies is a real number
            if now - s["marks"][-1][0] >= 0.25:
                s["marks"].append((now, s["bytes"]))
                cut = now - WINDOW
                while len(s["marks"]) > 2 and s["marks"][0][0] < cut:
                    s["marks"].pop(0)
                # the peak over three seconds: long enough that a pair of reads off a
                # fast disk does not become the headline figure
                recent = [m for m in s["marks"] if now - m[0] <= PEAK_WINDOW]
                if len(recent) > 1:
                    span = now - recent[0][0]
                    if span >= 1.0:
                        rate = (s["bytes"] - recent[0][1]) / span / 1048576.0
                        s["peak"] = max(s["peak"], rate)

    @staticmethod
    def _seat(s):
        """One sitting: a person, a title, a screen, whichever door it came in by."""
        return (s.get("who") or "", str(s.get("key") or ""), s.get("kind") or "")

    @staticmethod
    def _same(s):
        """What makes two sessions one viewing: a person, a title, a screen."""
        return (s.get("who") or "", str(s.get("key") or ""),
                s.get("kind") or "", s.get("address") or "")

    #: Told what was sent and how, when a stream closes: (bytes, how). Set by the
    #: server, which is the half that knows where to write it down.
    ledger = None

    def stop(self, sid):
        with self.lock:
            s = self.live.pop(sid, None)
            if s:
                if self.ledger and s.get("bytes"):
                    try:
                        # the row as well as the count: a copy is worth writing down
                        # by name, and by where it went
                        self.ledger(int(s["bytes"]), str(s.get("how") or ""), s)
                    except Exception:
                        pass          # a figure nobody kept is not worth a fault
                # remembered rather than forgotten, so the gap between one range
                # request and the next does not empty the list
                s["closed"] = True
                mark = self._same(s)
                self.gone[mark] = (time.time(), s)
                self.spent[mark] = (time.time(), s.get("bytes", 0),
                                    s.get("started", time.time()), s.get("peak", 0.0))

    def snapshot(self):
        now = time.time()
        out = []
        with self.lock:
            # every open socket for one viewing counts as that viewing: a direct play
            # opens several at once and in turn, and each used to be its own row
            groups = {}
            for s in self.live.values():
                groups.setdefault(self._same(s), []).append(s)
            for mark, (when, s) in list(self.gone.items()):
                if now - when > LINGER:
                    del self.gone[mark]
                elif mark not in groups:
                    groups[mark] = [s]        # held, until it has been quiet a while
            for mark in list(self.flows):
                if mark not in groups:
                    del self.flows[mark]
            for mark, parts in groups.items():
                s = self.joined(parts)
                f = self.flows.get(mark)
                if f:
                    # the viewing over the window, gaps between its requests included
                    first_t, first_b = f["marks"][0]
                    rate = (f["bytes"] - first_b) / max(1.0, now - first_t) / 1048576.0
                    lived = max(1.0, now - f["first"])
                    average = f["bytes"] / lived / 1048576.0
                    s = dict(s, started=min(s["started"], f["first"]),
                             bytes=max(s["bytes"], f["bytes"]),
                             peak=max(s["peak"], f["peak"]))
                else:
                    first_t, first_b = s["marks"][0]
                    span = max(0.5, now - first_t)
                    rate = (s["bytes"] - first_b) / span / 1048576.0
                    # the average since it opened, which is what a bursty direct play
                    # is really doing when the last few seconds happen to be quiet
                    lived = max(1.0, now - s["started"])
                    average = s["bytes"] / lived / 1048576.0
                out.append({
                    "who": s["who"], "title": s["title"], "quality": s["quality"],
                    "how": s["how"], "address": s["address"],
                    "app": s.get("app", ""), "kind": s.get("kind", ""),
                    # which title this is: what the player's own reports are matched on
                    "key": s.get("key", ""),
                    "seconds": int(now - s["started"]),
                    # when the connection opened, so the list can be kept in the order
                    # people arrived rather than shuffled by name
                    "started": int(s["started"]),
                    # the sitting's total, kept across reconnects, seeks and doors
                    "mb": round(((s.get("offset", 0) + s["bytes"]) if s.get("size")
                                 else max(s["bytes"], (self.sitting.get(self._seat(s))
                                                       or (0, 0))[1])) / 1048576.0, 1),
                    # a copy: how big the whole file is, so "of" can be said
                    "ofMb": round(s.get("size", 0) / 1048576.0, 1) if s.get("size") else None,
                    # megabytes a second, as measured
                    "mbps": round(rate if rate > 0.01 else average, 2),
                    # and the same number as a line speed, which is how a network is
                    # spoken about everywhere outside a file manager
                    "mbit": round((rate if rate > 0.01 else average) * 8, 1),
                    "average": round(average, 2),
                    "peak": round(max(s["peak"], average), 2),
                    # nothing is open for it this second, but somebody is watching it
                    "holding": all(one.get("closed") for one in parts),
                })
        # oldest connection first: a queue, where a new arrival joins the end
        return sorted(out, key=lambda x: x["started"])


WATCHING = Watching()
