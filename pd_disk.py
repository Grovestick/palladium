"""One queue for every drive: who reads it, in what order, and how many at once.

Watching comes first and never waits. Then a download coming in, then the copy to the
other machine, then a torrent being checked, then the background workers - loudness,
stills, credits, subtitles, the fixer. Each drive takes as many jobs at once as the owner
set (one unless told otherwise); a job waits while the drive is full or something before
it in that order is on the same drive or asking for it.

What this server does not run itself - qBittorrent's downloads and checks, the copy being
read by the other machine - is counted from outside with `outside`, so a worker waits for
them as well. Nothing here touches a disk: it only answers who may go.
"""
import os
import threading
import time

#: the order: the lower, the sooner
KINDS = {"watch": 0, "download": 1, "copy": 2, "check": 3, "work": 4}
WORDS = {"watch": "somebody watching", "download": "a download", "copy": "the copy",
         "check": "a check", "work": "background work"}
#: how many jobs a drive takes at once unless the owner set it
SLOTS = 1
#: a job asking that has not asked again for this long has given up
ASK_FOR = 120

LOCK = threading.Lock()
#: id -> {drive, kind, label, since}: holding a turn
HELD = {}
#: id -> {drive, kind, label, since, at}: waiting for one
ASKING = {}
#: drive -> [kind, ...]: what runs there that this server does not run, read every round
OUTSIDE = {}
_N = [0]


def drive_of(path):
    """The drive a path is on, "G:", or "" for one with none."""
    return os.path.splitdrive(str(path or ""))[0].upper()


def decide(drive, kind, held, asking, outside, slots):
    """Why a job of `kind` may not take a turn on `drive` now, or "" when it may.

    held lists the kinds holding a turn on that drive, asking the kinds waiting there
    ahead of this one (an earlier kind, or the same kind waiting longer), outside the
    kinds running there that this server does not run, slots how many at once."""
    if kind == "watch":
        return ""
    mine = KINDS[kind]
    going = list(held) + list(outside)
    before = [k for k in going if KINDS.get(k, 9) < mine and k != "watch"] + list(asking)
    if before:
        return "%s on %s first" % (WORDS[sorted(before, key=lambda k: KINDS[k])[0]], drive or "the disk")
    if len([k for k in going if k != "watch"]) >= max(1, int(slots or 1)):
        return "%s has %d of %d going" % (drive or "the disk", len([k for k in going if k != "watch"]),
                                         max(1, int(slots or 1)))
    return ""


def _on(drive, table, but=None):
    return [one["kind"] for i, one in table.items() if one["drive"] == drive and i != but]


def ask(path, kind, label="", slots=None, ident=None):
    """A turn on the drive `path` is on: (ident, "") once given, (ident, why) while it
    must wait. Asked again with the same ident until it is given; given back with done."""
    drive = drive_of(path)
    now = time.time()
    with LOCK:
        if ident is None:
            _N[0] += 1
            ident = "%s-%d" % (kind, _N[0])
        for i in [i for i, one in ASKING.items() if now - one["at"] > ASK_FOR]:
            ASKING.pop(i, None)
        if ident in HELD:
            return ident, ""
        why = decide(drive, kind, _on(drive, HELD), _ahead(drive, kind, ident, now),
                     OUTSIDE.get(drive, []), SLOTS if slots is None else slots)
        if why:
            was = ASKING.get(ident)
            ASKING[ident] = {"drive": drive, "kind": kind, "label": str(label or ""),
                             "since": was["since"] if was else now, "at": now}
            return ident, why
        ASKING.pop(ident, None)
        HELD[ident] = {"drive": drive, "kind": kind, "label": str(label or ""), "since": now}
        return ident, ""


def _ahead(drive, kind, ident, now):
    """The kinds asking for this drive before this one: an earlier kind, or the same kind
    asking since sooner - first come, first served within a kind."""
    mine = ASKING.get(ident)
    since = mine["since"] if mine else now
    out = []
    for i, one in ASKING.items():
        if i == ident or one["drive"] != drive:
            continue
        if KINDS[one["kind"]] < KINDS[kind] or (one["kind"] == kind and one["since"] < since):
            out.append(one["kind"])
    return out


def done(ident):
    """The turn given back."""
    with LOCK:
        HELD.pop(ident, None)
        ASKING.pop(ident, None)


class turn:
    """with turn(path, "work", "Loudness: x.mkv", "loudness") as why: - why "" means go;
    otherwise the job does not run now, and keeps its place in the line for as long as
    it asks again under the same name within ASK_FOR."""

    def __init__(self, path, kind, label="", ident=None, slots=None):
        self.args = (path, kind, label, slots)
        self.name = ident
        self.ident = None

    def __enter__(self):
        path, kind, label, slots = self.args
        got, why = ask(path, kind, label, slots, self.name)
        self.ident = None if why else got
        return why

    def __exit__(self, *a):
        if self.ident:
            done(self.ident)
        return False


def outside_from(torrents, copying_drives):
    """{drive: [kind, ...]} of what runs without this server: qBittorrent's downloads
    taking in data and its checks, and the drives the other machine copies from.
    torrents are qBittorrent's rows {save_path, state, dlspeed}."""
    out = {}
    for t in torrents or []:
        state = str(t.get("state") or "")
        drive = drive_of(t.get("save_path"))
        if state.startswith("checking"):
            out.setdefault(drive, []).append("check")
        elif state in ("downloading", "forcedDL") and int(t.get("dlspeed") or 0) > 50_000:
            out.setdefault(drive, []).append("download")
    for drive in copying_drives or ():
        out.setdefault(str(drive).upper(), []).append("copy")
    return out


def busy(drive):
    """What keeps a drive from taking a second download: a download, the copy or a check
    going there, this server's or from outside. "" when none."""
    with LOCK:
        going = [one["kind"] for one in HELD.values() if one["drive"] == drive]
        going += list(OUTSIDE.get(drive, []))
    going = sorted((k for k in going if k in ("download", "copy", "check")), key=lambda k: KINDS[k])
    return "%s on %s" % (WORDS[going[0]], drive or "the disk") if going else ""


def set_outside(found):
    with LOCK:
        OUTSIDE.clear()
        OUTSIDE.update(found)


def status():
    """Each drive's queue for the health page: [{drive, going: [...], waiting: [...]}]."""
    now = time.time()
    with LOCK:
        drives = sorted({one["drive"] for one in HELD.values()} | {one["drive"] for one in ASKING.values()}
                        | set(OUTSIDE))
        out = []
        for d in drives:
            going = [{"kind": one["kind"], "label": one["label"], "for": int(now - one["since"])}
                     for one in HELD.values() if one["drive"] == d]
            going += [{"kind": k, "label": WORDS[k] + " (qBittorrent)" if k != "copy" else "the copy",
                       "for": None} for k in OUTSIDE.get(d, [])]
            waiting = sorted(({"kind": one["kind"], "label": one["label"], "for": int(now - one["since"])}
                              for one in ASKING.values() if one["drive"] == d),
                             key=lambda w: (KINDS[w["kind"]], -w["for"]))
            out.append({"drive": d, "going": going, "waiting": waiting})
    return out
