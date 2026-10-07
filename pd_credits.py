"""Where each title's story ends, so "watched" can mean the credits have started.

A fixed line - 95%, or three minutes left - falls before the story ends in one film in
six and minutes into the credits in one in four, because credits run anything from one
minute to twelve. Measured per file instead:

  1. a chapter named for the credits, read in a fraction of a second;
  2. otherwise the later of two readings of the last quarter of the file - where the
     picture turns to credits (CLIP on the keyframes) and where the last words are
     spoken (a speech detector on the sound). The later of the two is seldom before
     the story ends: 6 of 60 films against 22 of 60 for the fixed line;
  3. otherwise nothing, and the fixed line stands.

The picture and the sound are read by pd_credits_make.py in the system's own Python,
which has the GPU libraries the compiled server does not. It runs by day, one file at
a time, and only while nobody is watching and the card is idle.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

#: key -> (end in seconds, file length it was measured on, how), reread every minute
CACHE = {"at": 0.0, "ends": {}}
STATE = {"lib": None, "free": None, "order": None, "file_for": None, "ffmpeg": "",
         "running": False, "now": "", "done": 0, "why": "", "copy": None}

#: the hours it may run, local time: by day, when the house is not watching
DAY_FROM, DAY_TO = 8, 20
#: a title measured and found to have nothing is asked again after this long
AGAIN_AFTER = 30 * 86400
#: key -> when a reading of it last failed; tried again after RETRY
FAILED = {}
RETRY = 3600
#: "none" rows written before this were failed readings, not titles without an ending
FAILED_AS_NONE_BEFORE = 1790365000
#: and endings measured before this heard the sound alone: the picture reading failed
#: on every film until the model stopped asking the hub each run. Measured again.
SOUND_ONLY_BEFORE = 1790427256

#: a chapter named for the credits
CREDIT_NAMES = re.compile(r"credit|end ?title|outro|abspann|eftertext|generique", re.I)


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS ending (
                       key TEXT PRIMARY KEY, at REAL, dur REAL, how TEXT,
                       measured INTEGER)""")


def _ends():
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return {}
    if time.time() - CACHE["at"] < 60:
        return CACHE["ends"]
    con = lib.db()
    try:
        ensure(con)
        CACHE["ends"] = {str(r[0]): (float(r[1] or 0), float(r[2] or 0), r[3] or "")
                         for r in con.execute("SELECT key, at, dur, how FROM ending "
                                              "WHERE at > 0")}
    except sqlite3.Error:
        pass
    finally:
        con.close()
    CACHE["at"] = time.time()
    return CACHE["ends"]


def end_for(key, duration):
    """The second this title's story ends, or None to use the fixed line.

    Only for the file it was measured on: another copy of the same film may be a
    different cut, so a length more than five seconds out is not trusted.
    """
    if not key or not duration:
        return None
    got = _ends().get(str(key))
    if not got:
        return None
    at, dur, _ = got
    if abs(dur - float(duration)) > 5:
        return None
    # a credits chapter half way in is a mislabelled chapter, not an ending
    if not (0.75 * dur <= at < dur):
        return None
    return at


def sql_end():
    """The same answer inside a query over progress, for FINISHED_SQL."""
    return ("(SELECT n.at FROM ending n WHERE n.key = progress.key "
            "AND ABS(n.dur - progress.duration) <= 5 "
            "AND n.at >= progress.duration * 0.75 AND n.at < progress.duration)")


def store(key, at, dur, how):
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return
    con = lib.db()
    try:
        ensure(con)
        con.execute("INSERT OR REPLACE INTO ending (key, at, dur, how, measured) "
                    "VALUES (?,?,?,?,?)", (str(key), float(at or 0), float(dur or 0),
                                           how, int(time.time())))
        con.commit()
    finally:
        con.close()
    CACHE["at"] = 0.0


def rows_since(since):
    """What has been measured since then, for the machine keeping copies."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return []
    con = lib.db()
    try:
        ensure(con)
        return [{"key": r[0], "at": r[1], "dur": r[2], "how": r[3], "measured": r[4]}
                for r in con.execute("SELECT key, at, dur, how, measured FROM ending "
                                     "WHERE measured > ? ORDER BY measured LIMIT 2000",
                                     (int(since or 0),))]
    finally:
        con.close()


def take_rows(rows):
    """Endings measured on the main server, kept here: the copy has no card to measure with."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib or not rows:
        return 0
    con = lib.db()
    try:
        ensure(con)
        for r in rows:
            con.execute("INSERT OR REPLACE INTO ending (key, at, dur, how, measured) "
                        "VALUES (?,?,?,?,?)", (str(r.get("key")), float(r.get("at") or 0),
                                               float(r.get("dur") or 0), r.get("how") or "",
                                               int(r.get("measured") or 0)))
        con.commit()
    finally:
        con.close()
    CACHE["at"] = 0.0
    return len(rows)


# ---------------------------------------------------------------- measuring

def _ffprobe():
    ff = STATE["ffmpeg"] or "ffmpeg"
    probe = os.path.join(os.path.dirname(ff), "ffprobe" + (".exe" if ff.endswith(".exe") else ""))
    return probe if os.path.exists(probe) else (shutil.which("ffprobe") or "ffprobe")


def chapter_end(path, dur):
    """The start of a chapter named for the credits, in the last quarter, or None."""
    try:
        out = subprocess.run([_ffprobe(), "-v", "quiet", "-print_format", "json",
                              "-show_chapters", path], capture_output=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        chapters = json.loads(out or b"{}").get("chapters") or []
    except Exception:
        return None
    found = [float(c.get("start_time") or 0) for c in chapters
             if CREDIT_NAMES.search(str((c.get("tags") or {}).get("title") or ""))]
    found = [s for s in found if 0.75 * dur <= s < dur]
    return min(found) if found else None


def _script():
    here = [os.path.dirname(os.path.abspath(sys.executable)),
            os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    for where in here:
        found = os.path.join(where, "pd_credits_make.py")
        if os.path.exists(found):
            return found
    return ""


def _make(args, timeout=1200):
    """Run the maker script in the system's Python: (what it said, why not)."""
    import pd_ai_subs
    python = pd_ai_subs.tool()
    script = _script()
    if not python or not script:
        return None, "no Python or no maker script"
    data = pd_ai_subs.where_data()
    try:
        mine = os.path.join(data, "pd_credits_make.py")
        if not os.path.exists(mine) or os.path.getmtime(mine) < os.path.getmtime(script):
            shutil.copyfile(script, mine)
        script = mine
    except OSError:
        pass
    air = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    air["PALLADIUM_FFMPEG"] = STATE["ffmpeg"] or "ffmpeg"
    try:
        out = subprocess.run([python, "-P", script] + list(args), cwd=data,
                             capture_output=True, timeout=timeout, env=air, text=True,
                             encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception as e:
        return None, str(e)[:120]
    last = [ln for ln in (out or "").splitlines() if ln.startswith("OK ")]
    if not last:
        return None, ((out or "").strip().splitlines() or ["nothing said"])[-1][:120]
    return json.loads(last[-1][3:]), ""


def picture_and_speech(path, dur):
    """(credits start by the picture, last words by the sound), from the maker script."""
    said, why = _make([path, str(dur)])
    if not said:
        return None, None, why
    return said.get("picture"), said.get("speech"), said.get("why") or ""


def speech_windows(path, dur):
    """Where people speak, in windows across the file: ([[start, length, spans]], why)."""
    said, why = _make(["--speech", path, str(dur)], timeout=900)
    return ((said or {}).get("windows") or []), why


def measure(key):
    """Find and keep one title's ending. True when something was written."""
    found = STATE["file_for"](key) if STATE["file_for"] else None
    if not found:
        return False
    path, dur = found
    if not path or not os.path.exists(path) or not dur:
        return False
    STATE["name"] = os.path.basename(path)
    STATE["since"] = time.time()
    at = chapter_end(path, dur)
    if at:
        store(key, at, dur, "chapter")
        return True
    picture, speech, why = picture_and_speech(path, dur)
    if why:
        STATE["why"] = why
        _say("ending %s: %s" % (os.path.basename(path)[:50], why))
        # and onto Reports, Errors: filed once an hour for the same fault, the film
        # left out of the first lines so a hundred films failing alike are one report
        if STATE.get("fault"):
            try:
                STATE["fault"]("last film: " + os.path.basename(path)[:80] + chr(10)
                               + "the end-credits reader failed" + chr(10) + why[:200])
            except Exception:
                pass
    heard = [x for x in (picture, speech) if x]
    # the picture failed to run: speech alone put the end at the last sung word, the
    # file's last seconds, on 177 titles while the model cache was broken
    if not picture and "picture:" in (why or ""):
        heard = []
    if not heard and why:
        # the reading failed, which is not the same as finding nothing: asked again
        # in an hour rather than written down as "none" for a month
        FAILED[str(key)] = time.time()
        return False
    # The scroll, where the picture finds one: the credits have started once names roll
    # on black, whatever is sung over them. Taking the later of the two let a closing
    # song carry the end three minutes past the start of the scroll. The sound decides
    # only a film whose picture never turns to credits.
    at, how = (picture, "picture") if picture else (speech or 0, "speech")
    if at and not (0.75 * dur <= at < dur):
        at = 0
    store(key, at, dur, how if at else "none")
    return True


def _say(text):
    """A line in the server's debug log, where a failed reading can be seen."""
    root = STATE.get("root") or ""
    if not root:
        return
    try:
        with open(os.path.join(root, "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))
    except OSError:
        pass


def working():
    """What is being measured this minute, for Now playing, or None."""
    if not STATE.get("now") or not STATE.get("name"):
        return None
    lib = STATE["lib"]() if STATE["lib"] else None
    done = 0
    if lib:
        con = lib.db()
        try:
            done = con.execute("SELECT COUNT(*) FROM ending WHERE at > 0").fetchone()[0]
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return {"name": STATE["name"], "since": int(STATE.get("since") or 0),
            "done": done, "left": int(STATE.get("left") or 0)}


def is_day(now=None):
    hour = time.localtime(now or time.time()).tm_hour
    return DAY_FROM <= hour < DAY_TO


def _work():
    import pd_beat
    while True:
        pd_beat.sleep("credits", 60)
        try:
            if STATE["copy"] and STATE["copy"]():
                continue                   # a copy takes the main server's endings
            if not is_day() or not (STATE["free"] and STATE["free"]()):
                STATE["running"] = False
                continue
            STATE["running"] = True
            ends = _all_rows()
            for key in (STATE["order"]() if STATE["order"] else []):
                had = ends.get(str(key))
                if had and (had[0] > 0 or time.time() - had[1] < AGAIN_AFTER):
                    continue
                if time.time() - FAILED.get(str(key), 0) < RETRY:
                    continue
                if not is_day() or not STATE["free"]():
                    break
                pd_beat.beat("credits", "measuring", 3600)
                STATE["now"] = str(key)
                STATE["left"] = sum(1 for k in (STATE["order"]() or [])
                                    if str(k) not in ends)
                try:
                    if measure(key):
                        STATE["done"] += 1
                finally:
                    STATE["now"] = ""
                    STATE["name"] = ""
                break                      # one a minute: the next is decided afresh
        except Exception as e:
            STATE["why"] = str(e)[:160]


def _all_rows():
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return {}
    con = lib.db()
    try:
        ensure(con)
        return {str(r[0]): (float(r[1] or 0), int(r[2] or 0))
                for r in con.execute("SELECT key, at, measured FROM ending")}
    finally:
        con.close()


def start(lib, ffmpeg, file_for, order, free, copy, root=""):
    """lib: the library getter; file_for(key) -> (path, seconds); order() -> keys, most
    wanted first; free() -> whether the machine is idle; copy() -> whether this machine
    follows another; root: where debug.log is."""
    STATE.update(lib=lib, ffmpeg=ffmpeg, file_for=file_for, order=order, free=free,
                 copy=copy, root=root)
    got = lib() if lib else None
    if got and not copy():
        con = got.db()
        try:
            ensure(con)
            con.execute("DELETE FROM ending WHERE how = 'none' AND measured < ?",
                        (FAILED_AS_NONE_BEFORE,))
            # every one measured as the later of picture and sound is measured again:
            # a song over the scroll made those late
            con.execute("DELETE FROM ending WHERE how = 'picture+speech'")
            con.commit()
        except sqlite3.Error:
            pass
        finally:
            con.close()
    if not STATE.get("thread"):
        STATE["thread"] = True
        threading.Thread(target=_work, name="palladium-credits", daemon=True).start()
