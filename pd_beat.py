"""What each background job is doing, so Settings can show which one has stopped.

A job says when it starts a step and when it goes to sleep. It is stalled when a step
runs past its limit, or when it sleeps well past the time it said it would wake.
"""
import threading
import time

BEATS = {}
_LOCK = threading.Lock()
#: name -> function giving (done, left), and the last answer with when it was asked
COUNTERS = {}
_COUNTED = {}
COUNT_EVERY = 60


def counter(name, fn):
    """How a job counts what it has done and what is left: fn() -> (done, left)."""
    COUNTERS[name] = fn


def _count(name):
    fn = COUNTERS.get(name)
    if not fn:
        return None
    was = _COUNTED.get(name)
    if was and time.time() - was[0] < COUNT_EVERY:
        return was[1]
    try:
        got = fn()
    except Exception:
        got = was[1] if was else None
    _COUNTED[name] = (time.time(), got)
    return got


def beat(name, step="working", limit=1800):
    """A job starting a step; stalled if still on it after limit seconds."""
    with _LOCK:
        BEATS[name] = {"step": step, "at": time.time(), "rest": 0, "limit": limit}


def rest(name, seconds, step="waiting"):
    """A job that will be back in so many seconds; stalled if not heard from well after."""
    with _LOCK:
        BEATS[name] = {"step": step, "at": time.time(), "rest": seconds, "limit": 0}


def sleep(name, seconds, step="waiting"):
    """rest, and sleep for it."""
    rest(name, seconds, step)
    time.sleep(seconds)


def slept(gap):
    """The machine was asleep for gap seconds: no job worked or waited through them."""
    with _LOCK:
        for b in BEATS.values():
            b["at"] += gap


#: how often the clock is looked at for a jump
WATCH = 30


def _watch():
    # time.sleep does not count the hours a machine is asleep and the clock does: after a
    # night's sleep every waiting job read as nine hours late. A jump in the clock moves
    # each job's start forward by the jump.
    last = time.time()
    while True:
        time.sleep(WATCH)
        now = time.time()
        gap = now - last - WATCH
        if gap > 120:
            slept(gap)
        last = now


threading.Thread(target=_watch, name="palladium-beat", daemon=True).start()


def status():
    """name -> {step, for (seconds on it), next (seconds to waking), ok}."""
    now = time.time()
    out = {}
    with _LOCK:
        beats = dict(BEATS)
    for name, b in beats.items():
        age = now - b["at"]
        if b["rest"]:
            late = age > b["rest"] + max(300, b["rest"] * 0.5)
            wake = max(0, int(b["rest"] - age))
        else:
            late = age > b["limit"]
            wake = None
        out[name] = {"step": b["step"], "for": int(age), "next": wake, "ok": not late}
    for name in COUNTERS:
        got = _count(name)
        if got:
            out.setdefault(name, {})["done"], out[name]["left"] = got
    return out
