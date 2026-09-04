#!/usr/bin/env python3
"""Who is watching what, right now, and how fast it is going out.

Nothing here is stored: a session exists while a socket is open and vanishes when it
closes. Bytes are sampled into a short rolling window rather than counted as an
average since the start, because the interesting number is what the line is carrying
this minute - a paused film still has a large total and a rate of zero.
"""
import itertools
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
_next_id = itertools.count(1)


class Watching:
    def __init__(self):
        self.lock = threading.Lock()
        self.live = {}
        # the last state of viewings whose sockets have closed, by who and what
        self.gone = {}

    def start(self, who, title, quality, how, address, key="", app="", device=""):
        sid = next(_next_id)
        with self.lock:
            self.live[sid] = {
                "id": sid, "who": who, "title": title, "quality": quality,
                "how": how, "address": address, "started": time.time(),
                # what is playing it: the build the client says it is, and what kind
                # of thing that is. A fault report reads differently from a
                # three-week-old build than from today's.
                "app": app, "kind": device,
                # which title this is, so the player's own reports - where it is, and
                # whether it is running - can be matched to it
                "key": str(key or ""),
                "bytes": 0, "marks": [(time.time(), 0)], "peak": 0.0,
            }
        return sid

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
                self.gone[self._same(s)] = (time.time(), s)

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
            for parts in groups.values():
                s = self.joined(parts)
                first_t, first_b = s["marks"][0]
                span = max(0.5, now - first_t)
                rate = (s["bytes"] - first_b) / span / 1048576.0
                # the average since it opened, which is what a bursty direct play is
                # really doing when the last few seconds happen to be quiet
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
                    "mb": round(s["bytes"] / 1048576.0, 1),
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
