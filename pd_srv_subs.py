"""Subtitles read out of the files: a film's own tracks pulled, the first minutes ahead of the rest, cached, mended, and the notes about them kept tidy.

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


def sub_cache_path(video, index):
    """Where the text of one track inside one film is kept.

    Named for the film, its size and when it was last written, so a re-encode or a
    replacement of the file is a different question with a different answer.
    """
    import hashlib
    try:
        stamp = "%d|%d" % (os.path.getsize(video), int(os.path.getmtime(video)))
    except OSError:
        stamp = "0"
    mark = hashlib.sha1(("%s|%s|%s" % (video, stamp, index)).encode("utf-8",
                                                                   "replace"))
    return os.path.join(S.ROOT, "subcache", mark.hexdigest() + ".vtt")


def cached_subtitle(where):
    try:
        if os.path.getsize(where) > 40:
            with open(where, encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return None


def keep_tapped_subtitle(video, index, text):
    """A track read whole from a play of the film, kept where a lifted track is kept."""
    where = S.sub_cache_path(video, index)
    os.makedirs(os.path.dirname(where), exist_ok=True)
    with open(where, "w", encoding="utf-8") as f:
        f.write(text)


def screen_waits(path):
    """THE gate for disk work a screen is waiting on now - the subtitles of the film playing:
    why it should pause, or "". A copy, a download or a scan never holds it - one copy kept
    a playing film's subtitles off the screen (2026-11-13). The server under stress, a viewer
    short of disk on that drive, or somebody on it filling their buffer do: the film's own
    viewer counts, a read of the film on the screen is what stalled it."""
    import pd_disk
    import pd_speed
    why = S.worker_waits(other_drive=pd_disk.drive_of(path) not in S.playing_drives(), in_line=True)
    if not why and not pd_speed.copy_may_run(S.aheads_on_drive(path)):
        why = "somebody on this drive is filling their buffer"
    return why


def sub_reader_waits(video, for_screen=False):
    """Why a read of a film for its subtitles should stop. For a film playing now, the
    screen's gate; for any other - the warm-up - the workers' gate and the buffers of
    whoever plays off its drive."""
    if for_screen:
        return S.screen_waits(video)
    import pd_disk
    import pd_speed
    why = S.worker_waits(other_drive=pd_disk.drive_of(video) not in S.playing_drives())
    if not why and not pd_speed.copy_may_run(S.aheads_on_drive(video)):
        why = "somebody on this drive is filling their buffer"
    return why


def head_subtitle(video, index, seconds=S.SUB_HEAD, patience=180, start=0):
    """A stretch of a track, quickly, for somebody who has pressed play: from `start`,
    in the film's own clock.

    Kept beside the whole one under its own name so a later request can tell them
    apart: this one stops at a timestamp and the film goes on past it. From where the
    film was resumed rather than from its beginning: the first twenty minutes cut to a
    place an hour in were nothing at all, and the player said so every five seconds.
    """
    start = int(max(0, start))
    where = S.sub_cache_path(video, index) + (".head" if not start else ".head%d" % start)
    said = S.cached_subtitle(where)
    if said is not None:
        return said
    from pd_gpu import FFMPEG
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start:
        # a seek, with the film's own times kept
        cmd += ["-ss", str(start), "-copyts"]
    cmd += ["-i", video, "-map", "0:%d" % int(index), "-vn", "-an",
            # an output limit, so ffmpeg stops reading once it is past the mark
            "-to", str(start + int(seconds)), "-f", "webvtt", "pipe:1"]
    import pd_workers
    _, out, stopped = pd_workers.run_yielding(cmd, lambda: S.sub_reader_waits(video, True), patience)
    if stopped:
        return None
    said = S.whole_stamps(out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or ""))
    if said.count("-->") < 1:
        return None
    try:
        os.makedirs(os.path.dirname(where), exist_ok=True)
        with open(where, "w", encoding="utf-8") as f:
            f.write(said)
    except OSError:
        pass
    return said


def head_cache_path(video, index, offset):
    """Where the stretch read for a play starting at `offset` is kept: head_subtitle's file
    for the ten minutes it falls in."""
    start = int(max(0, offset) // 600) * 600
    return S.sub_cache_path(video, index) + (".head" if not start else ".head%d" % start)


def head_soon(video, index, start, wait):
    """head_subtitle, waited for `wait` seconds; after that it finishes in the background
    and is cached for the next ask, and this answers None."""
    where = S.sub_cache_path(video, index) + (".head" if not start else ".head%d" % int(start))
    said = S.cached_subtitle(where)
    if said is not None:
        return said
    with S.PULLING_LOCK:
        job = S.HEADS.get(where)
        if job is None or not job.is_alive():
            box = {}
            job = threading.Thread(target=lambda: box.__setitem__(
                "said", S.head_subtitle(video, index, start=start)), daemon=True)
            setattr(job, "box", box)
            S.HEADS[where] = job
            job.start()
    job.join(wait)
    return None if job.is_alive() else getattr(job, "box", {}).get("said")


def pulling_now(video, index):
    """Whether somebody is already reading the whole of this track."""
    where = S.sub_cache_path(video, index)
    with S.PULLING_LOCK:
        lock = S.PULLING.get(where)
    return bool(lock and lock.locked())


def start_pull(video, index, why="", for_screen=True):
    """Read the whole track in the background, for whoever asks next: a screen playing it."""
    if S.pulling_now(video, index) or S.cached_subtitle(S.sub_cache_path(video, index)):
        return
    threading.Thread(target=lambda: S._quietly(S.pull_subtitle, video, index, 900, why, for_screen),
                     daemon=True).start()


def _quietly(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


def pull_subtitle(video, index, patience=900, why="", for_screen=False):
    """The whole of one embedded track as WebVTT, from the cache or from ffmpeg.

    The whole of it, not the part after some offset: one copy then answers for every
    seek, and cutting it to a live encode is arithmetic rather than another five
    minutes of reading an eight-gigabyte file. Fifteen minutes of patience because
    that read competes with the film being streamed off the same disk; it only ever
    happens once, and nothing else is waiting on the process that does it.

    Whoever asks first does the reading; anybody who asks while that is happening
    waits for it and takes the same answer.
    """
    where = S.sub_cache_path(video, index)
    said = S.cached_subtitle(where)
    if said is not None:
        return said
    # a minute more for every gigabyte past fifteen: a 31 GB file on a busy disk took
    # longer than fifteen minutes to read, was killed, and never had its subtitles
    try:
        patience = max(patience, 60 * os.path.getsize(video) / 1e9)
    except OSError:
        pass
    with S.PULLING_LOCK:
        mine = S.PULLING.get(where)
        if mine is None:
            mine = S.PULLING[where] = threading.Lock()
    with mine:
        said = S.cached_subtitle(where)      # somebody may have just finished it
        if said is not None:
            return said
        with S.PULL_ONE:
            said = S.cached_subtitle(where)
            if said is None:
                # the whole file is read for it: said, with what for, so a disk busy
                # during a download can be traced to whoever asked
                try:
                    pd_log.raw("%s reading subtitle track %s of %s - %s%s" % (
                        time.strftime("%H:%M:%S"), index, os.path.basename(video),
                        why or "asked", chr(10)))
                except OSError:
                    pass
                said = S._pull_subtitle(video, index, where, patience, for_screen)
    with S.PULLING_LOCK:
        S.PULLING.pop(where, None)
    return said


def declared_cues(video, index):
    """How many lines a subtitle track says it holds (the container's statistics tag),
    or 0 when it does not say."""
    from pd_gpu import FFMPEG
    probe = os.path.join(os.path.dirname(FFMPEG or ""), "ffprobe" + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(probe):
        probe = "ffprobe"
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", str(int(index)),
                              "-show_entries", "stream_tags=NUMBER_OF_FRAMES,NUMBER_OF_FRAMES-eng",
                              "-of", "default=nw=1:nk=1", video],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return max([int(x) for x in out.split() if x.strip().isdigit()] or [0])
    except Exception:
        return 0


def warmup_batches(work):
    """[(video, track)] as [(video, [tracks])], each video once, in the order first named."""
    out, at = [], {}
    for video, index in work:
        if video not in at:
            at[video] = len(out)
            out.append((video, []))
        out[at[video]][1].append(index)
    return out


def pull_subtitles_all(video, indexes, patience=900, why="", stop=None):
    """Every one of these embedded tracks out of one read of the file: one ffmpeg, one
    output per track. One track a read made a film with seven subtitle tracks seven
    whole passes over its disk - an hour of the drive for the warm-up alone, and a copy
    to the other machine crawled at 2 MB/s beside it. Each track is kept only when it
    came out whole, as pull_subtitle keeps it. stop() gives a reason to give the read up
    part-way - a background read, whose drive somebody else now needs. Returns {track: text}."""
    from pd_gpu import FFMPEG
    import pd_workers
    todo = [i for i in indexes if S.cached_subtitle(S.sub_cache_path(video, i)) is None]
    said = {i: S.cached_subtitle(S.sub_cache_path(video, i)) for i in indexes if i not in todo}
    if not todo:
        return said
    try:
        patience = max(patience, 60 * os.path.getsize(video) / 1e9)
    except OSError:
        pass
    import tempfile
    folder = tempfile.mkdtemp(prefix="pd-subs-")
    outs = {i: os.path.join(folder, "%d.vtt" % int(i)) for i in todo}
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video]
    for i in todo:
        cmd += ["-map", "0:%d" % int(i), "-f", "webvtt", outs[i]]
    with S.PULL_ONE:
        try:
            pd_log.raw("%s reading subtitle tracks %s of %s in one read - %s%s" % (
                time.strftime("%H:%M:%S"), ",".join(str(i) for i in todo),
                os.path.basename(video), why or "asked", chr(10)))
        except OSError:
            pass
        code, _, _ = pd_workers.run_yielding(cmd, stop, patience, stdout=subprocess.DEVNULL)
    for i in todo:
        try:
            with open(outs[i], encoding="utf-8", errors="replace") as f:
                text = S.whole_stamps(f.read())
        except OSError:
            text = ""
        declared = S.declared_cues(video, i)
        whole = code == 0 and (not declared or text.count("-->") >= 0.9 * declared)
        if text.count("-->") >= 1 and whole:
            where = S.sub_cache_path(video, i)
            try:
                os.makedirs(os.path.dirname(where), exist_ok=True)
                with open(where, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass
        said[i] = text
    import shutil
    shutil.rmtree(folder, ignore_errors=True)
    return said


def _pull_subtitle(video, index, where, patience, for_screen=False):
    from pd_gpu import FFMPEG
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", video, "-map", "0:%d" % int(index), "-vn", "-an",
           "-f", "webvtt", "pipe:1"]
    import pd_workers
    code, out, _ = pd_workers.run_yielding(cmd, lambda: S.sub_reader_waits(video, for_screen), patience)
    said = S.whole_stamps(out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or ""))
    # Kept only when whole: ffmpeg stopped part way - the disk too busy, the process
    # ended - handed back the first 209 lines of a 1612-line track, and that was kept
    # as the track for good. The container says how many lines it holds.
    declared = S.declared_cues(video, index)
    whole = code == 0 and (not declared or said.count("-->") >= 0.9 * declared)
    if said.count("-->") >= 1 and whole:
        try:
            os.makedirs(os.path.dirname(where), exist_ok=True)
            with open(where, "w", encoding="utf-8") as f:
                f.write(said)
        except OSError:
            pass                      # a cache that cannot be written is not a fault
    return said


def subtitle_without_one():
    """A film in the library with no subtitles at all: the best use of a keep-alive."""
    import pd_subs
    con = S.local().lib.db()
    try:
        rows = con.execute(
            """SELECT f.path, i.id, i.title, i.year FROM file f JOIN item i ON i.id=f.item_id
               WHERE i.type='movie' AND f.episode_id IS NULL AND i.title IS NOT NULL
               ORDER BY i.added DESC LIMIT 200""").fetchall()
    finally:
        con.close()
    for r in rows:
        try:
            embedded = json.loads(
                S.local().lib.db().execute("SELECT streams FROM file WHERE path=?",
                                         (r["path"],)).fetchone()["streams"] or "[]")
        except Exception:
            embedded = []
        text_tracks = [x for x in embedded
                       if (x.get("codec") or "") in ("subrip", "ass", "mov_text")]
        import pd_localapi
        if text_tracks or pd_localapi.sidecars(r["path"]):
            continue
        return r
    return None


def subtitle_keepalive(language="en"):
    """Download one subtitle if it has been too long since the last one."""
    cfg = S.local().lib.config()
    if not (cfg.get("opensubtitles_key") or "").strip():
        return "no API key"
    stored = S.read_settings()
    last = stored.get("subsLastDownload", 0)
    days = (time.time() - last) / 86400.0 if last else 999
    if days < S.KEEPALIVE_DAYS:
        return "last download %.0f days ago - nothing to do" % days

    pick = S.subtitle_without_one()
    if not pick:
        return "nothing in the library needs subtitles"
    import pd_subs
    client = pd_subs.OpenSubtitles(cfg.get("opensubtitles_key"),
                                   cfg.get("opensubtitles_user"),
                                   cfg.get("opensubtitles_pass"))
    results, err = client.search(pick["title"], pick["year"], language, pick["path"])
    if not results:
        return "keep-alive found nothing for %s (%s)" % (pick["title"], err)
    # a few entries have empty files behind them; one bad one must not waste the
    # keep-alive, and this runs so rarely that three attempts cost nothing
    text = None
    for candidate in results[:3]:
        text, err = client.download(candidate["id"])
        if text:
            break
    if not text:
        return "keep-alive could not download: %s" % (err or "no reason given")
    out = pd_subs.sidecar_path(pick["path"], language,
                               (results[0].get("name") or ""))
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        return "keep-alive could not write: %s" % e
    S.note_download()
    return "keep-alive fetched subtitles for %s" % pick["title"]


def mend_garbled(text):
    """Undo UTF-8 that was read as Windows-1252 somewhere before it reached us - a
    release that saved its subtitles that way showed a music note as a-hat and TM.
    Only a run that turns back into real UTF-8 is changed; anything else stays."""
    if not text or not S.GARBLED.search(text):
        return text

    def back(m):
        chunk = m.group(0)
        try:
            raw = chunk.encode("cp1252")
        except UnicodeEncodeError:
            try:
                raw = chunk.encode("latin-1")
            except UnicodeEncodeError:
                return chunk
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return chunk
    return S.GARBLED.sub(back, text)


def film_length(path):
    """How long a video is, asked of ffprobe. Nought when it cannot be told."""
    try:
        from pd_gpu import FFMPEG
        # only the program's own name, not every "ffmpeg" in the path it lives under -
        # the build folder is called ffmpeg-9.0-full_build and renaming that finds
        # nothing at all
        probe = (FFMPEG[:-len("ffmpeg.exe")] + "ffprobe.exe"
                 if FFMPEG.endswith("ffmpeg.exe")
                 else FFMPEG[:-len("ffmpeg")] + "ffprobe"
                 if FFMPEG.endswith("ffmpeg") else "ffprobe")
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return float(out.split()[0]) if out else 0.0
    except Exception:
        return 0.0


def black_after(path, after, window=120):
    """Where the picture goes black next - the join between two parts of an episode.

    A double episode is one file with two programmes in it, and the second one starts
    after a few black frames. Nothing else in the file says where: there are no
    chapters, and the subtitle for the second half is timed from its own zero.
    Returns the second the picture comes back, or nought if it never goes black.
    """
    start = max(0.0, after - 4.0)
    try:
        from pd_gpu import FFMPEG
        out = subprocess.run(
            [FFMPEG or "ffmpeg", "-hide_banner", "-ss", "%.2f" % start, "-t", "%d" % window,
             "-i", path, "-vf", "blackdetect=d=0.3:pix_th=0.10", "-an",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stderr
    except Exception:
        return 0.0
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", out):
        began, ended = float(m.group(1)), float(m.group(2))
        # the black must come after the last line of the previous part, not be the
        # fade the part itself ended on
        if start + began >= after - 1.0:
            return round(start + ended, 2)
    return 0.0


def prune_subtitle_notes():
    """Drop the timing and the chosen file for titles nobody is part way through.

    Kept while a film is resumable, and kept for good once a subtitle has been
    verified for it. Everything else is a note about something finished with, and
    the file should not grow a line per title watched once.
    """
    live = S.titles_in_use()
    if live is None:
        return 0
    proved = set((S.read_settings() or {}).get("subsOk") or {})
    def wanted(name):
        # "le11804|Something.srt" and "l608" both name a title with an l in front
        title = re.sub(r"^[lp]", "", str(name).split("|", 1)[0])
        # A verified subtitle is settled: somebody said so, or watched far enough
        # for it to have been read. Its timing is worth keeping for good, whether
        # or not anybody is part way through the film today.
        return title in live or title in proved
    stored = S.read_settings() or {}
    gone = 0
    held = stored.get("subShift") or {}
    for name in [n for n in held if not wanted(n)]:
        held.pop(name, None)
        gone += 1
    stored["subShift"] = held
    for mine in [stored] + list((stored.get("users") or {}).values()):
        picks = mine.get("subsPick") or {}
        for name in [n for n in picks if not wanted(n)]:
            picks.pop(name, None)
            gone += 1
        if picks or "subsPick" in mine:
            mine["subsPick"] = picks
    if gone:
        S.write_settings(stored)
    return gone


def sweep_subtitle_notes():
    """Once a day: forget the timing and the choice for films nobody is watching.

    Kept while a film is resumable or on a list, and kept for good once a subtitle
    has been verified for it. Everything else is a note about something watched and
    finished with, and the settings file should not grow a line per title.
    """
    while True:
        time.sleep(86400)
        try:
            S.prune_subtitle_notes()
        except Exception:
            pass


def place_subtitles_off_screen():
    """Everyone back to the defaults once, as the owner asked: every viewer's own look
    on every screen, and every film's and series' own, cleared, so each starts again
    from Off screen nearest the picture with Just up on it, and sets their own from
    there. A title's timing correction is kept - it is about the file, not the look."""
    stored = S.read_settings()
    if not isinstance(stored, dict) or not stored:
        return
    if int(stored.get("subPlaceRound") or 0) >= S.SUB_PLACE_ROUND:
        return
    for one in [stored] + [u for u in (stored.get("users") or {}).values()
                           if isinstance(u, dict)]:
        one.pop("subtitles", None)
        per = one.get("perTitle") or {}
        for name in list(per):
            look = per[name]
            if isinstance(look, dict):
                for k in S.SUB_LOOK_KEYS:
                    look.pop(k, None)
            if not look:
                per.pop(name, None)
        if "perTitle" in one:
            one["perTitle"] = per
    stored["subPlaceRound"] = S.SUB_PLACE_ROUND
    S.write_settings(stored)


def rescale_subtitle_sizes():
    """Move every size already chosen onto the one meaning of 100%.

    Runs once: the file records the base it was written for, and a file already on
    this base is left alone.
    """
    stored = S.read_settings()
    if not isinstance(stored, dict) or not stored:
        return
    if abs(float(stored.get("subSizeBase") or 0) - S.SUB_BASE) < 1e-9:
        return

    def move(look, device):
        was = S.SUB_BASE_WAS.get(device)
        if not was or not isinstance(look, dict) or "size" not in look:
            return
        try:
            look["size"] = round(float(look["size"]) * was / S.SUB_BASE, 3)
        except (TypeError, ValueError):
            pass

    def walk(mine):
        for device, look in (mine.get("subtitles") or {}).items():
            move(look, device)
        for name, look in (mine.get("perTitle") or {}).items():
            move(look, str(name).split("|", 1)[0])

    walk(stored)
    for one in (stored.get("users") or {}).values():
        if isinstance(one, dict):
            walk(one)
    stored["subSizeBase"] = S.SUB_BASE
    S.write_settings(stored)


def warm_subtitles():
    """Pull every embedded text track into the cache, slowly, when the machine is idle.

    One at a time, newest films first - what somebody just added is what they are
    about to watch. Failures are passed over: a file that cannot be read now will be
    tried again next time the server starts.
    """
    import json as _json
    import pd_disk
    import pd_workers
    time.sleep(90)                      # let the server settle before touching a disk
    while True:
        try:
            if not S.quiet_now():
                time.sleep(60)
                continue
            con = S.local().lib.db()
            try:
                rows = con.execute(
                    "SELECT path, streams FROM file ORDER BY COALESCE(ctime, mtime) DESC"
                ).fetchall()
            finally:
                con.close()
            work = []
            for row in rows:
                try:
                    tracks = _json.loads(row["streams"] or "[]")
                except Exception:
                    continue
                for track in tracks:
                    if (track.get("codec") or "").lower() not in S.TEXT_CODECS:
                        continue
                    where = S.sub_cache_path(row["path"], track.get("index"))
                    # a track that came out empty three times is passed over: Tyson's 35
                    # tracks were read whole, every round, on both machines
                    if (not os.path.exists(where) and os.path.exists(row["path"])
                            and not pd_workers.skip("warmup", "%s|%s" % (row["path"], track.get("index")))):
                        work.append((row["path"], track.get("index")))
            # a film's tracks together: all of them out of one read of the file
            for video, indexes in S.warmup_batches(work):
                if not S.quiet_now() or not S.auto_on("warmup"):
                    break               # somebody started watching; the rest can wait
                with pd_disk.turn(video, "work", "Subtitles: " + os.path.basename(video),
                                  "warmup", S.disk_slots(video)) as why:
                    if why:
                        break           # the drive is someone else's now
                    try:
                        S.pull_subtitles_all(video, indexes, why="subtitle warm-up",
                                           stop=lambda v=video: S.drive_loaded(v) or S.sub_reader_waits(v))
                    except Exception:
                        pass
                for index in indexes:
                    item = "%s|%s" % (video, index)
                    if S.cached_subtitle(S.sub_cache_path(video, index)) is None:
                        pd_workers.failed("warmup", item, "no whole track came out")
                    else:
                        pd_workers.worked("warmup", item)
                time.sleep(2)           # a gap between films, so the disk is not ours
            time.sleep(600 if not work else 30)
        except Exception:
            time.sleep(300)
