"""Skip rules: seconds of channel ident an episode starts past.

Rules are per programme, season or episode ({show: {"all" | "s17" | "s15e02": seconds}});
the most specific wins. Players apply them on a fresh start from 0 only; no file changes.

suggest() fingerprints the first 75 s of each episode (chromaprint) and proposes a
season rule when most episodes share the opening. A suggestion only: pairs of episodes
that open alike (two-parters) must not become a rule.
"""
import os
import struct
import subprocess
import threading
import time

#: seconds one fingerprint value stands for, and how much of each episode is read
STEP = 0.1238
READ_SECS = 75
#: two values this many bits apart or fewer are the same sound
SAME_BITS = 10
#: a value this many bits from the one before it is sound that is doing something;
#: silence gives the same value over and over
LIVE_BITS = 6
#: an opening shared for less than this, or with less changing sound in it, is nothing
LEAST_SECS = 3.0
LEAST_LIVE = 1.5
#: the longest a rule may skip
MOST_SECS = 180.0

STATE = {"busy": False, "show": "", "done": 0, "of": 0, "found": [], "why": ""}
LOCK = threading.Lock()


# ------------------------------------------------------------------------- the rules

def _mark(season=0, episode=0):
    """How a rule is filed: "all", "s17" or "s15e02"."""
    season, episode = int(season or 0), int(episode or 0)
    if season <= 0:
        return "all"
    return "s%d" % season if episode <= 0 else "s%de%02d" % (season, episode)


def lead_for(rules, show, season, episode):
    """How many seconds in an episode starts: the rule for that episode, else its
    season's, else the programme's; nought where there is none."""
    mine = (rules or {}).get(str(show)) or {}
    if not isinstance(mine, dict):
        return 0.0
    for mark in (_mark(season, episode), _mark(season), "all"):
        if mark in mine:
            try:
                return max(0.0, min(MOST_SECS, float(mine[mark])))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def with_rule(rules, show, season, episode, seconds):
    """The rules with one set, or with it taken away when the seconds are nought.
    A new dict: what was given is left as it was."""
    out = {str(k): dict(v) for k, v in (rules or {}).items() if isinstance(v, dict)}
    try:
        seconds = float(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    mine = out.setdefault(str(show), {})
    mark = _mark(season, episode)
    if seconds <= 0:
        mine.pop(mark, None)
    else:
        mine[mark] = round(min(MOST_SECS, seconds), 1)
    if not mine:
        out.pop(str(show), None)
    return out


def listed(rules):
    """Every rule as a row, a programme's together: the programme's own first, then
    its seasons in order, each season's episodes after it."""
    rows = []
    for show, mine in (rules or {}).items():
        if not isinstance(mine, dict):
            continue
        for mark, seconds in mine.items():
            season = episode = 0
            if mark != "all":
                try:
                    body = str(mark)[1:]
                    season = int(body.split("e")[0])
                    episode = int(body.split("e")[1]) if "e" in body else 0
                except (ValueError, IndexError):
                    continue
            try:
                rows.append({"show": str(show), "season": season, "episode": episode,
                             "seconds": float(seconds)})
            except (TypeError, ValueError):
                continue
    rows.sort(key=lambda r: (r["show"], r["season"], r["episode"]))
    return rows


# ------------------------------------------------------------------ the listening

def _bits(a, b):
    return (a ^ b).bit_count()


def shared(a, b, slide=8):
    """How two episodes open alike: (seconds to the last sound they share, seconds of
    that which is sound doing something). One is slid up to a second against the
    other, since a file may begin a few frames into the same opening.

    Counted to the last sound, not to where they part: after a logo both go quiet,
    and quiet is alike in every episode ever made.
    """
    best = (0.0, 0.0)
    for d in range(-slide, slide + 1):
        x, y = a[max(0, d):], b[max(0, -d):]
        miss = live = last_live = 0
        for i in range(min(len(x), len(y))):
            if _bits(x[i], y[i]) <= SAME_BITS:
                miss = 0
                if i and _bits(x[i], x[i - 1]) >= LIVE_BITS:
                    live += 1
                    last_live = i + 1
            else:
                miss += 1
                if miss > 6:                  # three quarters of a second apart: over
                    break
        found = (round(last_live * STEP, 2), round(live * STEP, 2))
        if (found[1], found[0]) > (best[1], best[0]):
            best = found
    return best


def suggest(prints):
    """What a season's episodes open with in common, from each one's fingerprint:
    {"seconds", "agree", "of"} or None.

    An episode has the opening when it shares it with at least half the others - one
    partner is a two-part story or a repeat, not a channel's logo. The season has it
    when six in ten of its episodes do, and there are at least three to judge by.
    """
    heard = {n: p for n, p in (prints or {}).items() if p}
    if len(heard) < 3:
        return None
    leads = {}
    for n, mine in heard.items():
        with_others = [shared(mine, other) for m, other in heard.items() if m != n]
        good = sorted(s for s, live in with_others if s >= LEAST_SECS and live >= LEAST_LIVE)
        if len(good) >= max(2, (len(heard) - 1 + 1) // 2):
            leads[n] = good[len(good) // 2]
    if len(leads) < 0.6 * len(heard):
        return None
    all_ = sorted(leads.values())
    middle = all_[len(all_) // 2]
    # half a second short of it: starting a moment early shows black, starting late
    # cuts the first word
    return {"seconds": max(0.0, int((middle - 0.25) * 2) / 2.0), "agree": len(leads),
            "of": len(heard)}


def fingerprint(path, ffmpeg="ffmpeg"):
    """The first minute and a quarter of a file's sound as fingerprint values, or []."""
    try:
        raw = subprocess.run(
            [ffmpeg, "-v", "error", "-t", str(READ_SECS), "-i", path, "-vn", "-ac", "1",
             "-ar", "11025", "-f", "chromaprint", "-fp_format", "raw", "-"],
            capture_output=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    n = len(raw) // 4
    return list(struct.unpack("<%dI" % n, raw[:n * 4])) if n else []


def listen(show, seasons, ffmpeg="ffmpeg"):
    """Listen to a programme season by season, behind the page that asked.

    `seasons` is {season: {episode: path}}. What is found is left in STATE for the
    page to read; one programme at a time.
    """
    with LOCK:
        if STATE["busy"]:
            return False
        STATE.update(busy=True, show=str(show), done=0, found=[], why="",
                     of=sum(len(e) for e in seasons.values()))

    def work():
        try:
            for season in sorted(seasons):
                prints = {}
                for episode, path in sorted(seasons[season].items()):
                    prints[episode] = fingerprint(path, ffmpeg) if os.path.exists(path) else []
                    STATE["done"] += 1
                if not any(prints.values()) and prints:
                    STATE["why"] = "the sound could not be read - ffmpeg without chromaprint?"
                said = suggest(prints)
                if said:
                    STATE["found"].append(dict(said, season=int(season)))
        except Exception as e:
            STATE["why"] = "%s: %s" % (type(e).__name__, e)
        finally:
            STATE["busy"] = False
            STATE["at"] = time.time()

    threading.Thread(target=work, name="palladium-leads", daemon=True).start()
    return True


def state():
    return {"busy": bool(STATE["busy"]), "show": STATE["show"], "done": int(STATE["done"]),
            "of": int(STATE["of"]), "found": list(STATE["found"]), "why": STATE["why"]}
