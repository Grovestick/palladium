"""Re-rips, as the ripper on this machine makes them: its own program (ripper/ripper.py, port
8797), so a rip of several hours outlives this server's restarts. This module only asks it
and passes on what it says; the profiles, the settings and the queue are the ripper's.
"""
import json
import os
import time
import urllib.parse
import urllib.request

#: where the ripper answers
RIPPER = "http://127.0.0.1:8797"
#: Mbit/s that marks a remux not named one, as the ripper's own default: a 2160p WEB-DL
#: runs to 25, a UHD remux 50 and up
REMUX_MBIT = 40


#: until when a reading is not asked of the ripper again, after it did not answer. A closed
#: port on Windows takes two seconds to refuse, and a server with no ripper spent them on
#: every Now playing - the panel gives up at two and drew it as off (2026-11-07)
AWAY = {"until": 0.0}
AWAY_FOR = 60


def _ask(path, body=None, patience=5):
    """The ripper's answer, or None when it does not answer. A reading is not asked for
    again for a minute after one went unanswered; a press - start, cancel - always is."""
    data = json.dumps(body).encode() if body is not None else None
    if data is None and time.time() < AWAY["until"]:
        return None
    req = urllib.request.Request(RIPPER + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=patience) as answer:
            said = json.loads(answer.read() or b"{}")
        AWAY["until"] = 0.0
        return said
    except Exception:
        AWAY["until"] = time.time() + AWAY_FOR
        return None


def is_remux(path, mbit=0.0):
    """A file worth ripping: named a remux, or carrying a disc's bitrate."""
    name = os.path.basename(str(path or "")).lower()
    return "remux" in name or float(mbit or 0) >= REMUX_MBIT


def ask(key, src, title, year, mbit=0.0, profile=""):
    """A rip of `src` queued in `profile`, or the ripper's chosen one: {"ok", "job"} or
    {"ok": False, "why"}."""
    body = {"key": key, "src": src, "title": title, "year": year, "mbit": mbit}
    if profile:
        body["profile"] = profile
    said = _ask("/api/rip", body, 15)
    if said is None:
        return {"ok": False, "why": "the ripper is not running on this machine"}
    return said


def state(key=None):
    """{"jobs", "running", "profile"} as the ripper says them; empty lists when it is away."""
    said = _ask("/api/jobs" + ("?key=" + urllib.parse.quote(str(key)) if key else ""))
    return said if isinstance(said, dict) else {"jobs": [], "running": [], "profile": "", "away": True}


def jobs(key=None):
    return state(key).get("jobs") or []


def running():
    """The rip being made now: [{title, year, progress, eta, pass, profile}]."""
    return state().get("running") or []


def stage(r):
    """A running rip's stage as screens say it: "pass 1 of 2 · 0.8x"."""
    said = "pass %d of %d" % (int(r.get("pass") or 1), int(r.get("passes") or 2))
    if r.get("paused"):
        return said + " · paused: " + str(r["paused"])
    if float(r.get("speed") or 0) > 0:
        said += " · %gx" % float(r["speed"])
    return said


#: the last answer for the download line, which every screen asks for every few seconds
ROWS = {"at": 0.0, "rows": []}


def left_words(seconds):
    """Seconds left as a screen says them: "4 h 20 min"."""
    hours, minutes = int(seconds) // 3600, (int(seconds) % 3600) // 60
    return ("%d h %d min" % (hours, minutes)) if hours else "%d min" % minutes


def rows_of(said):
    """The rips, as rows of the download line: the one being made and those waiting, each with
    its film's key, so pressing it opens the film."""
    running_now = {r.get("id"): r for r in said.get("running") or []}
    out = []
    for j in said.get("jobs") or []:
        if j.get("state") not in ("queued", "pass1", "pass2"):
            continue
        r = running_now.get(j.get("id"))
        if r:
            words = "%d%% · %s" % (int(float(r.get("progress") or 0) * 100), stage(r))
            if r.get("eta"):
                words += " · %s left" % left_words(r["eta"])
        else:
            words = str(j.get("why") or "queued")
        out.append({"ratingKey": str(j.get("key") or ""), "type": "movie",
                    "title": "Rip: " + str(j.get("title") or ""), "year": j.get("year"),
                    "offer": {"state": "ripping", "stage": "ripping", "words": words,
                              "progress": float((r or j).get("progress") or 0),
                              "eta": (r or {}).get("eta"), "mbit": 0.0, "who": "",
                              "pass": (r or {}).get("pass"), "passes": (r or {}).get("passes"),
                              "speed": (r or {}).get("speed")}})
    return out


def line_rows(now=None):
    """rows_of what the ripper says, kept two seconds; never long enough to hold a screen up."""
    now = time.time() if now is None else now
    if now - ROWS["at"] >= 2:
        said = _ask("/api/jobs", patience=1.5)
        ROWS.update(at=now, rows=rows_of(said) if isinstance(said, dict) else [])
    return list(ROWS["rows"])


def cancel(ident):
    said = _ask("/api/cancel", {"id": ident}, 10)
    return bool(said and said.get("ok"))
