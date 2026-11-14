"""The background workers and the drives: who may read what and when, the gates a screen's reads go through, and what a busy drive holds back.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import re
import subprocess
import threading
import time
from typing import Any

import pd_h
import pd_log

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def busy_now():
    """What this machine is working at, besides streams, downloads and credits,
    which have rows of their own: each worker in the middle of a step, a library
    scan, and a subtitle being written from the sound."""
    import pd_beat
    out = []
    for name, b in pd_beat.status().items():
        # asleep, or shown elsewhere
        if name not in S.WORKERS or b.get("next") is not None or "step" not in b:
            continue
        out.append({"name": S.WORKERS[name], "step": str(b.get("step") or ""),
                    "for": int(b.get("for") or 0), "done": b.get("done"),
                    "left": b.get("left"), "ok": bool(b.get("ok", True))})
    try:
        scan = S.local().lib.scan_state
        if scan.get("running"):
            out.append({"name": "Library scan", "step": str(scan.get("phase") or ""),
                        "done": scan.get("done"), "left":
                            max(0, int(scan.get("total") or 0) - int(scan.get("done") or 0))
                            if scan.get("total") else None, "ok": True})
    except Exception:
        pass
    try:
        import pd_ai_subs
        job = pd_ai_subs.look().get("on")
        if job:
            out.append({"name": "Writing subtitles", "ok": True,
                        "step": "%s, %s%s" % (job.get("title") or job.get("file") or "a film",
                                              job.get("language") or "",
                                              (" - %d%%" % round(100 * float(job["at"])))
                                              if float(job.get("at") or 0) > 0 else ""),
                        "left": len(pd_ai_subs.look().get("queued") or []) or None})
    except Exception:
        pass
    return out


def worker_state(beat):
    """One worker's state from its heartbeat row: off (never started), stalled,
    working (in a step) or waiting (asleep until its next round)."""
    if not beat or "step" not in beat:
        return "off"
    if not beat.get("ok", True):
        return "stalled"
    return "working" if beat.get("next") is None else "waiting"


def workers_now():
    """Every background worker with its state, for the monitor's boxes:
    [{name, state, step, for, next, done, left}]. All of them every time, so a box
    stays where it is and only lights up."""
    import pd_beat
    said = pd_beat.status()
    out = []
    for key, name in S.WORKERS.items():
        b = said.get(key) or {}
        state = S.worker_state(b)
        out.append({"name": name, "state": state,
                    "step": str(b.get("step") or "") if state in ("working", "stalled") else "",
                    "for": int(b.get("for") or 0) if state in ("working", "stalled") else 0,
                    "next": b.get("next"), "done": b.get("done"), "left": b.get("left")})
    one = {"name": "Loudness", "state": "waiting", "step": "", "for": 0, "next": None}
    if S.MEASURE_NOW.get("name"):
        done, left = S.loudness_counts()
        since = float(S.MEASURE_NOW.get("since") or 0)
        one.update(state="working", step=str(S.MEASURE_NOW["name"]), done=done, left=left)
        one["for"] = max(0, int(time.time() - since)) if since else 0
    out.append(one)
    one = {"name": "Credits analyser", "state": "waiting", "step": "", "for": 0, "next": None}
    try:
        import pd_credits
        doing = pd_credits.working()
        if doing:
            since = float(doing.get("since") or 0)
            one.update(state="working", step=str(doing.get("name") or ""),
                       done=doing.get("done"), left=doing.get("left"))
            one["for"] = max(0, int(time.time() - since)) if since else 0
    except Exception:
        pass
    out.append(one)
    one = {"name": "Library scan", "state": "waiting", "step": "", "for": 0, "next": None}
    try:
        scan = S.local().lib.scan_state
        if scan.get("running"):
            total, done = int(scan.get("total") or 0), int(scan.get("done") or 0)
            one.update(state="working", step=str(scan.get("phase") or ""), done=done,
                       left=max(0, total - done) if total else None)
    except Exception:
        pass
    out.append(one)
    # and the ones outside it, while they keep reporting
    now = time.time()
    for one in list(S.OUTSIDE.values()):
        if now - float(one.get("at") or 0) > 600:
            continue
        out.append({"name": one["name"], "state": "working" if one.get("step") else "waiting",
                    "step": one.get("step") or "", "for": 0, "next": None,
                    "done": one.get("done"), "left": one.get("left"), "link": one.get("link") or ""})
    one = {"name": "Writing subtitles", "state": "waiting", "step": "", "for": 0, "next": None}
    try:
        import pd_ai_subs
        look = pd_ai_subs.look()
        job = look.get("on")
        if job:
            one.update(state="working", left=len(look.get("queued") or []) or None,
                       step="%s, %s" % (job.get("title") or job.get("file") or "a film",
                                        job.get("language") or ""))
    except Exception:
        pass
    out.append(one)
    return out


def worker_reset(name):
    """One worker's failures forgotten, so it tries them again; how many."""
    import pd_workers
    n = pd_workers.reset(name)
    if name == "loudness":
        n += S.loudness_reset()
    return n


def disk_slots(path=None):
    """How many jobs a drive takes at once, as the owner set it - one unless told; with no
    path, {drive: n} for every drive set."""
    said = (S.read_settings() or {}).get("diskSlots") or {}
    said = {str(k).upper(): v for k, v in said.items() if isinstance(v, int) and 1 <= v <= 4}
    if path is None:
        return said
    return said.get(os.path.splitdrive(str(path or ""))[0].upper(), 1)


def set_disk_slots(asked):
    """The owner's turns per drive kept; {drive: n} as now set."""
    stored = S.read_settings() or {}
    mine = dict(stored.get("diskSlots") or {})
    for drive, n in (asked or {}).items():
        drive = str(drive).upper()[:2]
        if len(drive) == 2 and drive[1] == ":" and isinstance(n, int) and 1 <= n <= 4:
            mine[drive] = n
    stored["diskSlots"] = mine
    S.write_settings(stored)
    return S.disk_slots()


def disk_faults_now():
    """The drives whose disks reported faults in the last day, from Windows' System log -
    readable without being an administrator, which SMART is not. Kept an hour."""
    if time.time() - S.DISK_FAULTS[0] < 3600:
        return S.DISK_FAULTS[1]
    import pd_health
    ps = ("$l=@{}; Get-Partition | ? DriveLetter | % { $l[[string]$_.DiskNumber] += $_.DriveLetter + ':' };"
          "$e=Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='disk'; StartTime=(Get-Date).AddDays(-1)} -EA 0 |"
          " ? { $_.Id -in 7,51,153,157 } | % { @{id=$_.Id; m=$_.Message} };"
          "@{letters=$l; events=@($e)} | ConvertTo-Json -Depth 4 -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        said = json.loads(out or "{}")
        letters = {int(k): v[:2] for k, v in (said.get("letters") or {}).items()}
        events = [(e.get("id"), e.get("m")) for e in (said.get("events") or []) if isinstance(e, dict)]
        S.DISK_FAULTS[:] = [time.time(), pd_health.disk_faults(events, letters)]
    except Exception:
        S.DISK_FAULTS[0] = time.time()
    return S.DISK_FAULTS[1]


def library_drives():
    """The drives the library and the downloads are on, with free space and size, GB:
    [{drive, freeGB, sizeGB, downloads}]."""
    import shutil
    try:
        import pd_torrents
        saves = pd_torrents.save_folder() or ""
    except Exception:
        saves = ""
    cfg = S.local().lib.config()
    drives = {os.path.splitdrive(p)[0].upper() for p in
              (cfg.get("movies") or []) + (cfg.get("tv") or []) + (cfg.get("mixed") or []) + [saves] if p}
    out = []
    for d in sorted(x for x in drives if x):
        try:
            use = shutil.disk_usage(d + chr(92))
            out.append({"drive": d, "freeGB": int(use.free / 1e9), "sizeGB": int(use.total / 1e9),
                        "downloads": saves[:2].upper() == d})
        except OSError:
            pass
    return out


def scp_drives(lines):
    """The drives scp reads from, out of its command lines: each argument that is a local
    path with a drive letter - the source; the far end is user@host:path. The key after
    -i is a path too, and not what is copied."""
    out = set()
    for line in lines:
        args = [a.strip('"') for a in re.findall(r'"[^"]*"|\S+', str(line or ""))]
        for i, arg in enumerate(args):
            if i and args[i - 1] in ("-i", "-F", "-S", "-o", "-c", "-l", "-P", "-J"):
                continue
            if re.match(r"^[A-Za-z]:[\\/]", arg):
                out.add(arg[:2].upper())
    return out


def scp_sending_from():
    """The drives a copy over scp is reading this minute - a pack going to the cache."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if "scp.exe" not in subprocess.run(["tasklist", "/FI", "IMAGENAME eq scp.exe", "/NH"],
                                           capture_output=True, text=True, timeout=10,
                                           creationflags=flags).stdout.lower():
            return set()
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='scp.exe'\" | % CommandLine"],
                             capture_output=True, text=True, timeout=20, creationflags=flags).stdout
        return S.scp_drives(out.splitlines())
    except (OSError, subprocess.SubprocessError):
        return set()


def start_disk_watch():
    """What runs on each drive without this server - qBittorrent taking in or checking,
    the other machine copying - read every ten seconds for the drives' lines."""
    import pd_disk

    def work():
        while True:
            try:
                import pd_torrents
                qb = pd_torrents.QB(pd_torrents.load()["config"])
                rows = json.loads(qb._call("/api/v2/torrents/info", timeout=10) or b"[]")
            except Exception:
                rows = []
            # copies and moves this server sends hold turns of their own; what runs
            # without it - an scp - is counted from outside
            copying = S.scp_sending_from()
            pd_disk.set_outside(pd_disk.outside_from(rows, copying))
            time.sleep(10)
    threading.Thread(target=work, name="palladium-disks", daemon=True).start()


def sweep_segments():
    """Tidy up after HLS sessions nobody is watching any more, once a minute."""
    while True:
        time.sleep(60)
        try:
            S.engine().sweep_hls()
        except Exception:
            pass


def quiet_now():
    """True when no viewer is short of disk, no encode is running, and neither the
    cache, a download nor a scan has the disks.

    Background work has no deadline: the point is that it happens before somebody
    asks, not that it happens quickly. Playback with its buffer full does not stop it.
    """
    if S.worker_waits():
        return False
    try:
        return not (S.engine().status().get("streams") or [])
    except Exception:
        return True


def yield_pack_reads():
    """Every few seconds: a pack read on a drive somebody watches from paused while they
    are short of disk, and started again once they are not."""
    import pd_torrents
    while True:
        time.sleep(5)
        try:
            stop, start = pd_torrents.yield_reads(S.playing_drives(), S.playback_short())
            for h in stop:
                pd_log.raw("%s pack read paused for a viewer short of disk: %s%s"
                           % (time.strftime("%H:%M:%S"), h[:8], chr(10)))
            for h in start:
                pd_log.raw("%s pack read started again: %s%s" % (time.strftime("%H:%M:%S"), h[:8], chr(10)))
        except Exception:
            pass


def split_seed_way(cfg):
    """Which machines seed split packs: copy, main or both - both when unset."""
    import pd_torrents
    way = str((cfg or {}).get("splitSeed") or "both")
    return way if way in pd_torrents.SPLIT_SEED_WAYS else "both"


def sync_way(cfg):
    """Both ways, to the copy server only, or from it only: the Sync setting, both when unset."""
    way = str((cfg or {}).get("syncWay") or "both")
    return way if way in S.SYNC_WAYS else "both"


def byte_range(said, size):
    """The (start, end) a Range header asks of a file of `size` bytes: the whole file when
    there is no Range, None when it cannot be served (416). A last-N ask (bytes=-N) is the
    end of the file; a start past the end, an end before the start, several ranges or a
    number that is no number cannot be served - a player's offset that wrapped negative
    answered 500 and wrote a fault report each time (3,300 of them, 2026-11-06)."""
    if not said or not str(said).startswith("bytes="):
        return 0, size - 1
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", str(said).strip())
    if not m or not (m.group(1) or m.group(2)) or size <= 0:
        return None
    if not m.group(1):
        return max(0, size - int(m.group(2))), size - 1
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return (start, end) if start <= end else None


def disks_busy():
    """Why the disks are wanted for something else just now: a film coming in, or the
    library reading what has arrived. "" when neither."""
    try:
        import pd_torrents
        # started in the last six hours: one stuck for days is not worth holding the
        # cache back for
        since = time.time() - 6 * 3600
        if any(d.get("state") == "downloading" and float(d.get("started") or 0) > since
               for d in pd_torrents.load()["downloads"]):
            return "a download is coming in"
    except Exception:
        pass
    try:
        if S.local().lib.scan_state.get("running"):
            return "the library is scanning"
    except Exception:
        pass
    return ""


def playback_short():
    """Whether somebody watching is short of disk: a player running low on what it has
    ahead, a viewer getting less than their own average, or reads waiting on the disk.
    Playback with its buffer full leaves the background free to work."""
    try:
        return bool(S.Handler.a_film_is_struggling(S.WATCHING.snapshot())
                    or S.Handler.disk_reading_slow()
                    # and the signal that holds qBittorrent: a buffer under 30 s and
                    # falling, or a stall - one handler for downloads and the copy
                    or S.Handler.how_thin() in ("back", "panic"))
    except Exception:
        return True


def aheads_on_drive(path):
    """The seconds ahead each viewer has who plays a film off the drive `path` is on, None
    for one whose player has not said lately. A direct play only: a transcode's depth is the
    encoder's lead, 0 for the whole film in a browser, and held every copy off its drive."""
    drive = os.path.splitdrive(str(path or ""))[0].upper()
    out = []
    try:
        for r in S.WATCHING.snapshot():
            if not str(r.get("how") or "").startswith("direct"):
                continue
            film = (S.local().file_for(str(r.get("key") or ""), 0) or {}).get("file") or ""
            if not drive or os.path.splitdrive(film)[0].upper() != drive:
                continue
            said = S.Handler.AHEAD.get(str(r.get("who") or "") + "/" + str(r.get("address") or ""))
            out.append(float(said[0]) if said and time.time() - (said[1] or 0) < 60 else None)
    except Exception:
        pass
    return out


def background_waits():
    """Why a background worker reading whole files should wait: the cache copying, a
    film coming in, the library scanning. "" when none of those. Work for what somebody
    is watching does not ask."""
    try:
        if any(r.get("how") == "syncing" for r in S.WATCHING.snapshot()):
            return "the cache is copying"
    except Exception:
        pass
    return S.disks_busy()


def files_on_screen():
    """The files being read for somebody watching this minute."""
    out = set()
    try:
        for r in S.WATCHING.snapshot():
            if r.get("how") != "syncing":
                path = (S.local().file_for(str(r.get("key") or ""), 0) or {}).get("file") or ""
                if path:
                    out.add(os.path.normcase(path))
    except Exception:
        pass
    return out


def warm_file(path):
    """The first and last WARM_EDGE bytes of a file read into WARM, the oldest dropped."""
    try:
        size = os.path.getsize(path)
        parts = []
        with open(path, "rb") as f:
            parts.append((0, f.read(min(S.WARM_EDGE, size))))
            if size > 2 * S.WARM_EDGE:
                f.seek(size - S.WARM_EDGE)
                parts.append((size - S.WARM_EDGE, f.read(S.WARM_EDGE)))
    except OSError:
        return False
    with S.WARM_LOCK:
        S.WARM[os.path.normcase(path)] = {"at": time.time(), "parts": parts}
        now = time.time()
        for k in [k for k, v in S.WARM.items() if now - v["at"] > S.WARM_FOR]:
            S.WARM.pop(k, None)
        while len(S.WARM) > S.WARM_FILES:
            S.WARM.pop(min(S.WARM, key=lambda k: S.WARM[k]["at"]), None)
    return True


def warm_block(path, at, want):
    """want bytes from offset at, out of WARM when it holds them whole; else None."""
    with S.WARM_LOCK:
        held = S.WARM.get(os.path.normcase(path))
        if not held:
            return None
        for start, data in held["parts"]:
            if start <= at and at + want <= start + len(data):
                return data[at - start:at - start + want]
            if start <= at < start + len(data):
                return data[at - start:]          # what it holds; the rest off the disk
    return None


def transfer_turn(path, hot, ident, label):
    """A turn on the drive a copy or a move to the other machine reads: (ident, "") once
    given, (ident, why) while somebody else has it. One at a time a drive - two transfers
    go together only when they share no drive at either end; one for a screen asks first."""
    import pd_disk
    got, why = pd_disk.ask(path, "download" if hot else "copy", label, ident=ident)
    return got, why


def drive_loaded(path):
    """Why a background job already reading path should stop: its drive taken by a copy,
    a download or a check, or the workers' gate closed. "" while it may go on, and always
    for a file somebody is watching - only live viewing is served regardless."""
    import pd_disk
    if os.path.normcase(str(path or "")) in S.files_on_screen() | S.URGENT_FILES:
        return ""
    drive = pd_disk.drive_of(path)
    with pd_disk.LOCK:
        outside = list(pd_disk.OUTSIDE.get(drive, []))
    if outside:
        return "%s on %s" % (pd_disk.WORDS.get(outside[0], outside[0]), drive)
    return S.worker_waits(other_drive=drive not in S.playing_drives())


def worker_waits(for_screen=False, card=False, other_drive=False, in_line=False, idle_priority=False):
    """THE gate every background worker asks: why it should wait now, or "". Work for a
    title on a screen now never waits; everything else waits while a viewer is short of
    disk, a download is coming in, the cache copies or the library scans.
    card: work on the graphics card also waits for game mode and a busy card.
    other_drive: reading a drive nobody is playing from, so a viewer short of disk is
    no reason to wait.
    in_line: takes its turn from the disk's line, which knows the copies and downloads on
    its own drive: one on another drive is no reason to wait.
    idle_priority: uses only the processor nothing else wants, and is often what fills it: a
    busy processor is no reason to wait - the ripper paused itself otherwise. Nor is memory,
    which an encoder stopped where it is goes on holding, so it would wait for ever."""
    if for_screen:
        return ""
    import pd_boss
    stress = pd_boss.stressed()
    if stress and not (idle_priority and stress.startswith(("processor", "memory"))):
        return "server under stress: " + stress
    if not other_drive and S.playback_short():
        return "a viewer is short of disk"
    why = "" if in_line else S.background_waits()
    if why:
        return why
    if card:
        if S.Handler.game_mode().get("on"):
            return "game mode"
        one = S.Handler.card_now()
        if one and int(one.get("busy") or 0) >= 25:
            return "the card is busy"
    return ""


def copy_while_busy(wanted):
    """What the cache copies while the disks are busy: only what is on a screen now."""
    return [w for w in wanted if (w.get("why") or "") == "on a screen now"]
