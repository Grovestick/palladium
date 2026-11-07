"""debug.log, written in one place: a line at a time under one lock, so two threads'
lines never run into each other, and a disk that refuses a write is no fault of the
caller's.

  pd_log.raw(text)       text as given, its own newline included
  pd_log.line(text)      "HH:MM:SS text" and a newline
"""
import os
import threading
import time

LOCK = threading.Lock()
STATE = {"root": ""}
#: the day last said in each log, by path: the lines carry the time only, so a deletion
#: at 22:49 could not be put on a day (2026-11-07)
DAYS = {}


def day_line(where, today):
    """The line that says the date, when `today` is not the day last said in that log."""
    if DAYS.get(where) == today:
        return ""
    DAYS[where] = today
    return "---- " + today + chr(10)


def setup(root):
    STATE["root"] = root


def path():
    return os.path.join(STATE["root"], "debug.log") if STATE["root"] else ""


def raw(text, where=None):
    """Text added to the log as it is - to `where` when a module keeps its log elsewhere;
    False when it could not be written."""
    where = where or path()
    if not where:
        return False
    try:
        with LOCK:
            with open(where, "a", encoding="utf-8") as f:
                f.write(day_line(where, time.strftime("%Y-%m-%d")) + str(text))
        return True
    except OSError:
        return False


def line(text):
    """One line with the time in front."""
    return raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))
