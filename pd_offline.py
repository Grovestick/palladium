"""Copies for a device to keep: a film or an episode encoded to one Matroska file the app
takes away and plays without the server.

One encode at a time, on the card, in the background: it asks the workers' gate before it
starts, holds its drive's turn while it runs, and is paused while the gate says wait. The
copy is deleted once the device says it has it, or KEEP_FOR after it was made.
"""
import io
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

import pd_disk
import pd_safe

#: (lines, Mbit) per quality; original keeps the picture as it is
QUALITY = {"original": (0, 0), "1080": (1080, 8), "720": (720, 4)}
#: a copy nobody fetched is deleted after this long
KEEP_FOR = 7 * 86400
#: what a drive keeps free besides the copy
ROOM = 10 * 1024 ** 3
#: seconds between looks at the queue and the encode running
EVERY = 15
#: subtitle codecs a Matroska file carries and Media3 reads; mov_text is rewritten as srt
SUBS = ("subrip", "srt", "ass", "ssa", "webvtt", "hdmv_pgs_subtitle", "dvd_subtitle",
        "mov_text")
HDR = ("smpte2084", "arib-std-b67")

LOCK = threading.Lock()
STATE = {"root": "", "data": None, "proc": None, "job": "", "turn": None, "paused": False,
         "filters": None, "gate": None}


def _path():
    return os.path.join(STATE["root"], "offline.json")


def load():
    """The jobs; {} only where no file ever was - one that cannot be read raises."""
    with LOCK:
        if STATE["data"] is None:
            path = _path()
            if os.path.exists(path):
                with io.open(path, encoding="utf-8") as f:
                    STATE["data"] = json.load(f)
            else:
                STATE["data"] = {"jobs": {}}
            STATE["data"].setdefault("jobs", {})
        return STATE["data"]


def save():
    with LOCK:
        if not STATE["root"] or STATE["data"] is None:
            return
        tmp = _path() + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE["data"], f)
            pd_safe.settle(f)
        os.replace(tmp, _path())


def public(job):
    """What a device is told about one job."""
    out = {k: job.get(k) for k in ("id", "key", "mi", "quality", "state", "pct", "size",
                                   "why", "subs", "title")}
    out["sound"] = job.get("sound") or ""
    return out


def lines_class(height):
    """The quality a picture of `height` lines is: 1080 from 760 up, so a film shot wide
    at 1920 by 800 counts as one; 720 from 500 up; 0 below, or unknown."""
    h = int(height or 0)
    return 2160 if h >= 1600 else 1080 if h >= 760 else 720 if h >= 500 else 0


def offered(height):
    """The qualities a copy of a file of `height` lines may be asked in: none above the
    file's own (owner, 2026-11-12). An unknown height offers all."""
    have = lines_class(height)
    return [q for q, (lines, _) in QUALITY.items()
            if not lines or not height or lines <= max(have, 720 if have else 0)]


def ask(key, mi, quality, can, src, who, title):
    """A copy of `src` (the library's file_for) in `quality` for a device that can play
    `can` ({hevc, hdr, sound: [codec]}): the job, new or the one already asked for."""
    if quality not in QUALITY:
        return {"ok": False, "why": "no such quality"}
    if not src or not os.path.exists(str(src.get("file") or "")):
        return {"ok": False, "why": "the file is not on this machine"}
    if quality not in offered(src.get("height")):
        return {"ok": False, "why": "the file is only %sp" % (src.get("height") or "?")}
    data = load()
    with LOCK:
        for job in data["jobs"].values():
            if (job["key"] == key and job["mi"] == mi and job["quality"] == quality
                    and job["who"] == who and job["state"] in ("queued", "making", "ready")):
                return {"ok": True, "job": public(job)}
        ident = uuid.uuid4().hex[:16]
        data["jobs"][ident] = {
            "id": ident, "key": key, "mi": mi, "quality": quality, "who": who,
            "title": title, "src": src["file"], "audio": src.get("audio"),
            "duration": float(src.get("duration") or 0), "can": can or {},
            "state": "queued", "pct": 0, "size": 0, "why": "", "subs": [], "sound": "",
            "asked": time.time(), "made": 0, "out": "", "home": STATE["root"]}
        job = data["jobs"][ident]
    save()
    return {"ok": True, "job": public(job)}


def jobs(ids):
    data = load()
    return [public(data["jobs"][i]) for i in ids if i in data["jobs"]]


def file_of(ident):
    """The finished copy's path, or ""."""
    job = load()["jobs"].get(ident) or {}
    out = job.get("out") or ""
    return out if job.get("state") == "ready" and os.path.exists(out) else ""


def drop(ident, taken=False):
    """The copy deleted: the device has it (taken) or no longer wants it."""
    data = load()
    job = data["jobs"].get(ident)
    if not job or not ours(job):
        return False
    if STATE["job"] == ident:
        _stop()
    _remove(job.get("out") or "")
    _remove((job.get("out") or "") + ".part")
    with LOCK:
        if taken:
            job.update(state="taken", out="", taken=time.time())
        else:
            data["jobs"].pop(ident, None)
    save()
    return True


def _remove(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _has_filter(ffmpeg, name):
    if STATE["filters"] is None:
        try:
            said = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                                  capture_output=True, text=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            STATE["filters"] = said.stdout
        except (OSError, subprocess.SubprocessError):
            STATE["filters"] = ""
    return (" %s " % name) in STATE["filters"]


def probe(ffmpeg, path):
    """ffprobe's streams for a file."""
    exe = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe" if os.name == "nt" else "ffprobe")
    if not os.path.exists(exe):
        exe = shutil.which("ffprobe") or exe
    said = subprocess.run([exe, "-v", "error", "-show_streams", "-of", "json", path],
                          capture_output=True, text=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return json.loads(said.stdout or "{}").get("streams") or []


def command(ffmpeg, encoder, streams, job, out, progress, tonemap=True):
    """(arguments, subtitle indexes kept, sound words) for one copy. Pure: everything it
    reads is handed in."""
    height, mbit = QUALITY[job["quality"]]
    can = job.get("can") or {}
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    sounds = [s for s in streams if s.get("codec_type") == "audio"]
    if not video or not sounds:
        raise ValueError("no picture or no sound in the file")
    v = video[0]
    a = next((s for s in sounds if s.get("index") == job.get("audio")), sounds[0])
    subs = [s for s in streams if s.get("codec_type") == "subtitle"
            and s.get("codec_name") in SUBS]
    vcodec = str(v.get("codec_name") or "")
    hdr = str(v.get("color_transfer") or "") in HDR
    nvenc = (encoder or {}).get("name") == "nvenc"
    tall = int(v.get("height") or 0)
    # every copy HEVC (owner, 2026-11-12): original copies an HEVC picture as it is and
    # makes any other one HEVC at its own size
    copy_video = job["quality"] == "original" and vcodec == "hevc"
    if job["quality"] == "original":
        mbit = 20
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-progress", progress, "-nostats"]
    if nvenc and not copy_video:
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    cmd += ["-i", job["src"], "-map", "0:%d" % v["index"], "-map", "0:%d" % a["index"]]
    for s in subs:
        cmd += ["-map", "0:%d" % s["index"]]
    if copy_video:
        cmd += ["-c:v", "copy"]
    else:
        lines = min(height or tall, tall) if tall else height
        size = ("-2:%d:" % lines) if tall and lines < tall else ""
        keep_hdr = hdr and can.get("hdr")
        if nvenc:
            if keep_hdr:
                vf = "scale_cuda=%sformat=p010le" % size
            elif hdr and tonemap:
                # the card resizes, the processor maps the light down to an ordinary screen
                vf = ("scale_cuda=%sformat=p010le,hwdownload,format=p010le,"
                      "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                      "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
                      "format=yuv420p" % size)
            else:
                vf = "scale_cuda=%sformat=yuv420p" % size
            cmd += ["-vf", vf, "-c:v", "hevc_nvenc"] + list(encoder.get("preset") or [])
            cmd += ["-rc", "vbr", "-cq", "23", "-b:v", "0",
                    "-maxrate", "%dM" % mbit, "-bufsize", "%dM" % (mbit * 2)]
            if keep_hdr:
                cmd += ["-profile:v", "main10", "-color_primaries", "bt2020",
                        "-color_trc", str(v.get("color_transfer")), "-colorspace", "bt2020nc"]
        else:
            vf = ("scale=%s" % size.rstrip(":")) if size else "null"
            cmd += ["-vf", vf + ",format=yuv420p", "-c:v", "libx265", "-preset", "fast",
                    "-crf", "24", "-x265-params",
                    "vbv-maxrate=%d:vbv-bufsize=%d" % (mbit * 1000, mbit * 2000)]
    acodec = str(a.get("codec_name") or "")
    if acodec in (can.get("sound") or []):
        cmd += ["-c:a", "copy"]
        sound = "%s %sch, as it is" % (acodec, a.get("channels") or "?")
    else:
        cmd += ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]
        sound = "aac 2ch 192k from %s" % acodec
    cmd += ["-c:s", "copy"]
    for n, s in enumerate(subs):
        if s.get("codec_name") == "mov_text":
            cmd += ["-c:s:%d" % n, "srt"]
    cmd += ["-f", "matroska", out]
    return cmd, [s["index"] for s in subs], sound


def _drive_room(path):
    try:
        return shutil.disk_usage(os.path.splitdrive(path)[0] + os.sep).free
    except OSError:
        return 0


def _need(job):
    height, mbit = QUALITY[job["quality"]]
    if not mbit:
        try:
            return os.path.getsize(job["src"])
        except OSError:
            return 0
    return int(job["duration"] * (mbit + 0.3) * 1e6 / 8)


def _folder(job):
    """Where the copy is written: beside the library on the source's drive, else the
    server's own; "" when neither has room."""
    need = _need(job) + ROOM
    for base in (os.path.splitdrive(job["src"])[0] + os.sep, STATE["root"]):
        if base and _drive_room(base) > need:
            return os.path.join(base, "Palladium offline")
    return ""


def _stop():
    proc = STATE["proc"]
    if proc and proc.poll() is None:
        try:
            if STATE["paused"]:
                _pause(False)
            proc.kill()
            proc.wait(10)
        except (OSError, subprocess.SubprocessError):
            pass
    STATE.update(proc=None, job="", paused=False)
    if STATE["turn"]:
        pd_disk.done(STATE["turn"])
        STATE["turn"] = None


def _pause(on):
    try:
        import psutil
        p = psutil.Process(STATE["proc"].pid)
        p.suspend() if on else p.resume()
        STATE["paused"] = on
    except Exception:
        pass


def _progress(job):
    """Per cent done, from ffmpeg's progress file."""
    try:
        with io.open(job["out"] + ".progress", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-2000:]
        us = [int(x.split("=", 1)[1]) for x in tail.splitlines()
              if x.startswith("out_time_us=") and x.split("=", 1)[1].strip().isdigit()]
        if us and job["duration"]:
            return min(99, int(us[-1] / 1e6 / job["duration"] * 100))
    except OSError:
        pass
    return job.get("pct") or 0


def _finish(job):
    proc = STATE["proc"]
    rc = proc.returncode
    err = ""
    try:
        with io.open(job["out"] + ".log", encoding="utf-8", errors="replace") as f:
            err = " ".join(f.read().split())[-300:]
    except OSError:
        pass
    part = job["out"] + ".part"
    if rc == 0 and os.path.exists(part):
        os.replace(part, job["out"])
        job.update(state="ready", pct=100, size=os.path.getsize(job["out"]), made=time.time(),
                   why="")
    else:
        _remove(part)
        job.update(state="failed", why="ffmpeg %s: %s" % (rc, err or "no message"))
    for extra in (".progress", ".log"):
        _remove(job["out"] + extra)
    STATE.update(proc=None, job="", paused=False)
    if STATE["turn"]:
        pd_disk.done(STATE["turn"])
        STATE["turn"] = None


def _begin(job):
    """The encode started, or why it was not."""
    where = _folder(job)
    if not where:
        return "no drive has room for it"
    os.makedirs(where, exist_ok=True)
    import pd_gpu
    ffmpeg, encoder = pd_gpu.FFMPEG, (pd_gpu.ENCODER if pd_gpu.HW_OK else None)
    if not ffmpeg:
        return "no ffmpeg on this machine"
    try:
        streams = probe(ffmpeg, job["src"])
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return "could not read the file: %s" % e
    base = os.path.join(where, "%s-%s" % (job["id"], job["quality"]))
    job["out"] = base + ".mkv"
    try:
        cmd, kept, sound = command(ffmpeg, encoder, streams, job,
                                   job["out"] + ".part", job["out"] + ".progress",
                                   tonemap=_has_filter(ffmpeg, "zscale"))
    except (KeyError, ValueError) as e:
        return str(e)
    log = open(job["out"] + ".log", "wb")
    STATE["proc"] = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                       | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)))
    log.close()
    STATE["job"] = job["id"]
    job.update(state="making", subs=kept, sound=sound, why="", started=time.time())
    return ""


def tick(now=None):
    """One look: old copies swept, the running encode followed or paused, the next one
    started when the gate and the drive allow."""
    now = now or time.time()
    data = load()
    changed = False
    for ident, job in list(data["jobs"].items()):
        if not ours(job):
            continue
        old = (job["state"] == "ready" and now - (job.get("made") or now) > KEEP_FOR) or (
            job["state"] in ("failed", "taken") and now - (job.get("asked") or now) > KEEP_FOR)
        if old:
            _remove(job.get("out") or "")
            data["jobs"].pop(ident, None)
            changed = True
    gate = STATE["gate"] or (lambda: "")
    running = data["jobs"].get(STATE["job"]) if STATE["job"] else None
    if running:
        if STATE["proc"].poll() is not None:
            _finish(running)
            changed = True
        else:
            why = gate()
            if why and not STATE["paused"]:
                _pause(True)
            elif not why and STATE["paused"]:
                _pause(False)
            running.update(pct=_progress(running), why=("waiting: " + why) if why else "")
            changed = True
    if not STATE["job"]:
        waiting = sorted((j for j in data["jobs"].values() if j["state"] == "queued" and ours(j)),
                         key=lambda j: j["asked"])
        if waiting:
            job = waiting[0]
            why = gate()
            if not why:
                ident, why = pd_disk.ask(job["src"], "work", "Offline copy: " + job["title"],
                                         ident="offline-" + job["id"])
                if not why:
                    STATE["turn"] = ident
                    why = _begin(job)
                    if why:
                        pd_disk.done(ident)
                        STATE["turn"] = None
                        job.update(state="failed", why=why)
            if why and job["state"] == "queued":
                job["why"] = "waiting: " + why
            changed = True
    if changed:
        save()


def _loop():
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001 - the loop outlives one bad look
            import pd_log
            pd_log.line("offline copies: %r" % e)
        time.sleep(EVERY)


def ours(job):
    """Whether this server made the job: its files are only that server's to touch. One
    from before the job said so was the live server's. A state copied elsewhere - the
    gate runs the server on one - acts on nothing it did not make (2026-11-12)."""
    home = job.get("home") or os.path.join(os.environ.get("APPDATA") or "", "Palladium")
    return os.path.normcase(os.path.abspath(home)) == os.path.normcase(
        os.path.abspath(STATE["root"] or "."))


def start(root, gate):
    """Jobs kept under `root`; `gate` is the workers' gate, "" for go."""
    STATE.update(root=root, gate=gate)
    data = load()
    # an encode cut off by a restart starts again from the beginning
    for job in data["jobs"].values():
        if job["state"] == "making" and ours(job):
            _remove((job.get("out") or "") + ".part")
            job.update(state="queued", pct=0)
    save()
    threading.Thread(target=_loop, name="offline", daemon=True).start()
