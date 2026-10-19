"""Check and repair: the library's index and the state files beside it.

What the button in Settings runs. It looks first and changes as little as it can:

    the index       sqlite's own integrity check; a copy is taken before anything is
                    tried; a damaged index has its indexes rebuilt and is checked again
    the state files each one read; one that cannot be read is put back from its newest
                    copy that can (pd_safe.recover), the damaged file kept beside it

Nothing is ever deleted and nothing empty is ever written. What cannot be repaired is
said, with the name of the newest sound copy if there is one.
"""
import os
import re
import sqlite3
import time

import pd_safe

#: copies of the index kept by the repair, newest first
KEEP_COPIES = 3

#: the state files a server cannot rebuild for itself
STATE_FILES = ("settings.json", "invites.json", "library.json", "torrents.json", "tracker.json")


def index_health(dbpath):
    """What sqlite says of the index: {"ok": bool, "said": [lines]}."""
    if not os.path.exists(dbpath):
        return {"ok": False, "said": ["there is no index file"]}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % dbpath.replace("\\", "/"), uri=True, timeout=30)
        try:
            said = [str(r[0]) for r in con.execute("PRAGMA integrity_check(20)")]
        finally:
            con.close()
    except sqlite3.Error as e:
        return {"ok": False, "said": [str(e)[:200]]}
    return {"ok": said == ["ok"], "said": said}


def index_copies(dbpath):
    """The copies a repair has kept, newest first."""
    folder, name = os.path.split(dbpath)
    try:
        found = [os.path.join(folder, n) for n in os.listdir(folder or ".")
                 if re.fullmatch(re.escape(name) + r"\.\d{8}-\d{6}\.bak", n)]
    except OSError:
        return []
    return sorted(found, reverse=True)


def index_copy(dbpath, now=None, keep=KEEP_COPIES):
    """A whole copy of the index beside it, by sqlite's own backup so it is consistent
    while the server runs; the newest `keep` kept. The path, or None."""
    into = "%s.%s.bak" % (dbpath, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    try:
        a = sqlite3.connect("file:%s?mode=ro" % dbpath.replace("\\", "/"), uri=True, timeout=30)
        b = sqlite3.connect(into)
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
    except sqlite3.Error:
        # an index sqlite will not read is copied as bytes: it is still the evidence
        try:
            if os.path.exists(into):
                os.remove(into)
            with open(dbpath, "rb") as src, open(into, "wb") as dst:
                while True:
                    lump = src.read(1 << 20)
                    if not lump:
                        break
                    dst.write(lump)
                pd_safe.settle(dst)
        except OSError:
            return None
    for old in index_copies(dbpath)[max(1, int(keep)):]:
        try:
            os.remove(old)
        except OSError:
            pass
    return into


def index_repair(dbpath):
    """Check the index; if it is damaged, copy it, rebuild its indexes and check again.

    {"ok": sound now, "did": what was done, "copy": the copy taken or "", "said": lines}
    """
    before = index_health(dbpath)
    if before["ok"]:
        return {"ok": True, "did": "sound", "copy": "", "said": []}
    if not os.path.exists(dbpath):
        return {"ok": False, "did": "missing", "copy": "", "said": before["said"]}
    copy = index_copy(dbpath) or ""
    try:
        con = sqlite3.connect(dbpath, timeout=60)
        try:
            con.execute("REINDEX")
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as e:
        return {"ok": False, "did": "damaged", "copy": os.path.basename(copy),
                "said": before["said"][:5] + ["rebuilding its indexes failed: " + str(e)[:160]]}
    after = index_health(dbpath)
    return {"ok": after["ok"], "did": "indexes rebuilt" if after["ok"] else "damaged",
            "copy": os.path.basename(copy), "said": [] if after["ok"] else after["said"][:5]}


def state_repair(root, names=STATE_FILES):
    """Each state file: read, and put back from its newest readable copy if it cannot be.

    [{"file", "state": "reads" | "put back" | "unreadable" | "absent", "from"}]
    """
    out = []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.exists(path):
            out.append({"file": name, "state": "absent", "from": ""})
        elif pd_safe.readable(path):
            out.append({"file": name, "state": "reads", "from": ""})
        else:
            used = pd_safe.recover(path)
            out.append({"file": name, "state": "put back" if used else "unreadable",
                        "from": os.path.basename(used) if used else ""})
    return out


def everything(root, dbpath):
    """The whole check, as rows for the settings page and one verdict."""
    index = index_repair(dbpath)
    files = state_repair(root)
    rows = [{"what": "Library index",
             "state": index["did"],
             "note": ("Copied to %s first. " % index["copy"] if index["copy"] else "")
                     + "; ".join(index["said"][:3])}]
    for f in files:
        if f["state"] == "absent":
            continue
        rows.append({"what": f["file"], "state": f["state"],
                     "note": ("from %s; the unreadable file is kept beside it" % f["from"])
                             if f["from"] else ""})
    ok = index["ok"] and all(f["state"] != "unreadable" for f in files)
    return {"ok": ok, "rows": rows}
