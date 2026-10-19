"""State files that survive the power going.

A file written and swapped into place is whole or absent only once its bytes are on the
disk. Without that, a power cut in the same second leaves a file of the right length
and all zeros - and a spare copied in that second goes the same way. That took a
settings file and its spare together, and the server came up on its default port.

    settle(f)          the open file's bytes forced to the disk, before it is swapped in
    spare(path)        path.bak refreshed, but only from a file that can be read
    daily(path)        one dated copy a day, seven kept
    recover(path)      an unreadable file put back from its newest readable copy
"""
import json
import os
import re
import shutil
import time

#: dated copies kept of each file
KEEP_DAYS = 7


def settle(f):
    """Force an open file's bytes to the disk. Called before the file is swapped in."""
    f.flush()
    os.fsync(f.fileno())


def readable(path):
    """Whether a file holds JSON - an object or a list - rather than zeros or a part
    of one. A missing file is not readable."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if not raw.strip(b"\x00 \r\n\t"):
            return False
        return isinstance(json.loads(raw.decode("utf-8")), (dict, list))
    except (OSError, ValueError):
        return False


def _copy(src, dst):
    """A copy that is on the disk before it carries the name."""
    tmp = "%s.%d.tmp" % (dst, os.getpid())
    with open(src, "rb") as a, open(tmp, "wb") as b:
        shutil.copyfileobj(a, b)
        settle(b)
    os.replace(tmp, dst)


def spare(path):
    """Refresh path.bak from the file as it stands - unless the file cannot be read,
    which is exactly when the spare is the only good copy left."""
    try:
        if readable(path):
            _copy(path, path + ".bak")
            return True
    except OSError:
        pass
    return False


def _dated(path):
    """The dated copies of one file, newest first."""
    folder, name = os.path.split(path)
    found = []
    try:
        for one in os.listdir(folder or "."):
            if re.fullmatch(re.escape(name) + r"\.\d{8}\.bak", one):
                found.append(os.path.join(folder, one))
    except OSError:
        pass
    return sorted(found, reverse=True)


def daily(path, keep=KEEP_DAYS, today=None):
    """One dated copy a day of a file that can be read, the newest `keep` kept."""
    try:
        mine = "%s.%s.bak" % (path, today or time.strftime("%Y%m%d"))
        if os.path.exists(mine) or not readable(path):
            return False
        _copy(path, mine)
        for old in _dated(path)[max(1, int(keep)):]:
            os.remove(old)
        return True
    except OSError:
        return False


def spares(path):
    """Every copy a file could be put back from, best first: the spare of the last
    write, then the dated ones, then any other copy beside it by its date."""
    folder, name = os.path.split(path)
    first = [path + ".bak"] + _dated(path)
    others = []
    try:
        for one in os.listdir(folder or "."):
            full = os.path.join(folder, one)
            if one.startswith(name + ".bak") and full not in first:
                others.append(full)
    except OSError:
        pass
    others.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    return [p for p in first + others if os.path.exists(p)]


def recover(path, now=None):
    """Put an unreadable file back from its newest readable copy.

    Nothing is done to a file that reads, or to one that is not there. The damaged
    file is kept beside it under another name. Returns the copy used, or None - and
    with no readable copy the damaged file is left exactly where it is: unreadable is
    not empty, and nothing here ever writes an empty file.
    """
    if not os.path.exists(path) or readable(path):
        return None
    for one in spares(path):
        if not readable(one):
            continue
        try:
            aside = "%s.unreadable-%s" % (path, time.strftime("%Y%m%d-%H%M%S",
                                                               time.localtime(now)))
            os.replace(path, aside)
            _copy(one, path)
            return one
        except OSError:
            return None
    return None
