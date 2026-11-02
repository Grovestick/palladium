"""What every background worker shares: the files it failed on, kept so one that fails
again and again is passed over, and a reset that forgets them.

A worker says what it worked on with `failed(worker, item)` when the item did not work
and `worked(worker, item)` when it did; `skip(worker, item)` is True once an item failed
GIVE_UP_AFTER times. Kept in workers.json beside the settings, so a restart does not
start the failing over again; Reset under Automatic runs forgets one worker's list.
"""
import json
import os
import threading
import time

#: failures of one item before the worker passes it over
GIVE_UP_AFTER = 3
LOCK = threading.Lock()
STATE = {"root": "", "failed": None}


def run_yielding(cmd, stop, timeout, every=2.0, **kw):
    """A background job's child process, stopped the moment its gate closes: (returncode,
    stdout, why stopped). stop() is asked every few seconds and gives a reason to stop or
    "". A started read used to run to its end whatever came onto the drive after it - two
    viewers and a copy beside a whole-file subtitle read put the drive at 1.3 s a read.
    The whole tree goes: the maker script's own ffmpeg with it."""
    import subprocess
    kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kw.setdefault("stdin", subprocess.DEVNULL)
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.DEVNULL)
    proc = subprocess.Popen(cmd, **kw)
    began, out, why = time.time(), None, ""
    while True:
        try:
            out, _ = proc.communicate(timeout=every)
            break
        except subprocess.TimeoutExpired:
            pass
        why = (stop() if stop else "") or ("took longer than %d s" % timeout
                                           if time.time() - began > timeout else "")
        if why:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=kw["creationflags"])
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            break
    return proc.returncode, out, why


def setup(root):
    STATE["root"] = root


def _file():
    return os.path.join(STATE["root"], "workers.json") if STATE["root"] else ""


def _load():
    if STATE["failed"] is None:
        STATE["failed"] = {}
        try:
            with open(_file(), encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                STATE["failed"] = {str(k): dict(v) for k, v in got.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass
    return STATE["failed"]


def _write():
    where = _file()
    if not where:
        return
    try:
        tmp = where + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE["failed"], f)
        os.replace(tmp, where)
    except OSError:
        pass


def failed(worker, item, why=""):
    """One more failure of `item` for `worker`; how many it has now."""
    with LOCK:
        one = _load().setdefault(str(worker), {}).setdefault(str(item), {"n": 0})
        one["n"] = int(one.get("n") or 0) + 1
        one["at"] = int(time.time())
        if why:
            one["why"] = str(why)[:200]
        _write()
        return one["n"]


def worked(worker, item):
    """The item worked: its failures forgotten."""
    with LOCK:
        mine = _load().get(str(worker)) or {}
        if str(item) in mine:
            mine.pop(str(item), None)
            _write()


def skip(worker, item):
    """Whether the worker passes this item over: it failed GIVE_UP_AFTER times."""
    with LOCK:
        one = (_load().get(str(worker)) or {}).get(str(item)) or {}
    return int(one.get("n") or 0) >= GIVE_UP_AFTER


def reset(worker):
    """The worker's failures forgotten, so it tries them again; how many there were."""
    with LOCK:
        mine = _load().pop(str(worker), None) or {}
        _write()
    return len(mine)


def given_up(worker):
    """How many items the worker passes over now."""
    with LOCK:
        mine = _load().get(str(worker)) or {}
    return sum(1 for one in mine.values() if int(one.get("n") or 0) >= GIVE_UP_AFTER)
