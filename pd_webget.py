"""Video from a web page saved into the download folder through yt-dlp, then scanned in.

Only what yt-dlp can read: a copy-protected stream is refused by yt-dlp itself and
said as such. Nothing here goes round copy protection.
"""
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

#: id -> one download: url, title, got, size, done, why, file, at
JOBS = {}
#: id -> its yt-dlp process while it runs
PROCS = {}
LOCK = threading.Lock()
STATE = {"root": "", "scan": None}
#: finished downloads are listed this long
KEEP = 3600
#: a scan already running is asked again this often, this many times
SCAN_EVERY, SCAN_TRIES = 30, 20
#: music videos go to this folder under the download folder (pd_library.MUSIC_FOLDER)
MUSIC_DIR = "Music videos"
#: videos taken from one playlist or mix
MIX_MOST = 50


def use_data_dir(root, scan=None):
    STATE["root"] = root
    STATE["scan"] = scan


def is_address(url):
    return bool(re.match(r"(?i)^https?://[^\s/]+\.[^\s]*$", str(url or "").strip()))


def command(exe, url, folder, temp, ffmpeg="", music=False):
    """yt-dlp's arguments: one video, its own folder named after its title, pieces kept
    in temp until merged, the final path and progress printed one per line. A music
    video keeps its thumbnail as cover.jpg; a music playlist or mix brings MIX_MOST."""
    mix = music and re.search(r"[?&]list=", url)
    cmd = list(exe) + [
        # UTF-8 out: in the console's code page "Alizée" was read back as "Aliz?e"
        "--encoding", "utf-8",
        "--newline", "--no-warnings", "--no-simulate", "--progress",
        "--progress-template",
        "download:@@P %(progress.downloaded_bytes)s %(progress.total_bytes,progress.total_bytes_estimate)s",
        "--print", "before_dl:@@T %(title)s",
        "--print", "after_move:@@F %(filepath)s",
        # AV1 only when nothing else is offered: the Google TV Streamer decodes it in
        # software, 1080p AV1 played at 4 fps; VP9 and H.264 it decodes in hardware
        "-f", "bv*[vcodec!^=av01]+ba/b[vcodec!^=av01]/bv*+ba/b",
        "--merge-output-format", "mkv",
        "-P", folder, "-P", "temp:" + temp,
        "-o", "%(title).120B/%(title).120B.%(ext)s"]
    cmd += (["--yes-playlist", "--playlist-end", str(MIX_MOST), "--ignore-errors"] if mix
            else ["--no-playlist"])
    if music:
        cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg",
                "-o", "thumbnail:%(title).120B/cover.%(ext)s"]
    if ffmpeg:
        cmd += ["--ffmpeg-location", ffmpeg]
    return cmd + ["--", url]


def read_line(job, line):
    """One line of yt-dlp's output into the job; True when it was one of ours."""
    line = line.strip()
    if line.startswith("@@T "):
        job["title"] = line[4:].strip()
    elif line.startswith("@@F "):
        job["file"] = line[4:].strip()
        job["count"] = job.get("count", 0) + 1
    elif line.startswith("@@P "):
        parts = line[4:].split()
        nums = [int(float(p)) if re.match(r"^\d+(\.\d+)?$", p) else 0 for p in parts[:2]]
        nums += [0] * (2 - len(nums))
        job["got"], size = nums
        if size:
            job["size"] = size
    else:
        return False
    return True


def why_failed(err):
    """yt-dlp's last ERROR line in plain words."""
    text = str(err or "").strip().splitlines()
    lines = [l for l in text if "ERROR" in l] or text[-1:]
    last = lines[-1] if lines else ""
    if re.search(r"(?i)\bDRM\b", last):
        return "copy-protected: it cannot be downloaded"
    if "Unsupported URL" in last:
        return "no video found at that address"
    if re.search(r"(?i)not available in your country|geo.?restrict", last):
        return "not available from this country"
    # "ERROR: [site] video-id: what went wrong"
    last = re.sub(r"^.*?ERROR:\s*(\[[^\]]*\]\s*([^\s:]+:\s*)?)?", "", last).strip()
    return last[:200] or "yt-dlp stopped without saying why"


def start(url, folder, exe, ffmpeg="", env=None, music=False):
    """One download started; the job, or {"why"} when it cannot start."""
    url = str(url or "").strip()
    if not is_address(url):
        return {"why": "not a web address"}
    if not folder:
        return {"why": "no download folder set"}
    if not exe:
        return {"why": "yt-dlp is not here yet; it is fetched two minutes after the server starts"}
    ident = uuid.uuid4().hex[:10]
    temp = os.path.join(STATE["root"] or ".", "webget", ident)
    if music:
        folder = os.path.join(folder, MUSIC_DIR)
    job = {"id": ident, "url": url, "title": "", "got": 0, "size": 0, "done": False,
           "why": "", "file": "", "count": 0, "music": bool(music), "at": time.time()}
    with LOCK:
        JOBS[ident] = job
    threading.Thread(target=_run, args=(job, command(exe, url, folder, temp, ffmpeg, music),
                                        temp, env), daemon=True).start()
    return dict(job)


def _run(job, cmd, temp, env):
    os.makedirs(temp, exist_ok=True)
    err = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        job.update(done=True, why="yt-dlp did not start: %s" % e, ended=time.time())
        return
    with LOCK:
        PROCS[job["id"]] = proc
    # stderr read beside stdout so a full pipe cannot stall it
    reader = threading.Thread(target=lambda: err.extend(
        proc.stderr.read().decode("utf-8", "replace").splitlines()), daemon=True)
    reader.start()
    for raw in proc.stdout:
        with LOCK:
            read_line(job, raw.decode("utf-8", "replace"))
    proc.wait()
    reader.join(5)
    with LOCK:
        PROCS.pop(job["id"], None)
        cancelled = job.get("why") == "cancelled"
        # a mix with one video gone still brought the rest
        if not cancelled and not job["count"]:
            job["why"] = why_failed("\n".join(err))
        job["done"] = True
        job["ended"] = time.time()
    shutil.rmtree(temp, ignore_errors=True)
    if job["count"]:
        _scan()


def _scan():
    """The library scanned for the new file; a scan already running is waited out."""
    scan = STATE["scan"]
    if not scan:
        return
    for _ in range(SCAN_TRIES):
        try:
            if scan():
                return
        except Exception:
            return
        time.sleep(SCAN_EVERY)


def cancel(ident):
    with LOCK:
        job = JOBS.get(str(ident or ""))
        proc = PROCS.get(str(ident or ""))
        if not job or job["done"]:
            return False
        job["why"] = "cancelled"
    if proc:
        proc.kill()
    return True


def listed(now=None):
    """Every download, newest first; finished ones drop off after KEEP."""
    now = now or time.time()
    with LOCK:
        for k in [k for k, j in JOBS.items()
                  if j["done"] and now - j.get("ended", j["at"]) > KEEP]:
            JOBS.pop(k, None)
        return sorted((dict(j) for j in JOBS.values()), key=lambda j: -j["at"])
