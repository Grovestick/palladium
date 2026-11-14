"""Loudness and what has been played: each file's reading, measured while nothing plays, what is soon to be watched, and the gain handed to the library.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import threading
import time
from typing import Any

import pd_h

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def anybody_watching():
    """Whether a film is being read for somebody this minute, copying aside.

    Asked of the live list rather than of Handler.WATCHING_NOW, which is written while
    a file is being served and never taken down again: measuring stopped at the first
    viewing after a restart and never started again, which is why a library of three
    thousand files had twenty-five measured.
    """
    try:
        return any(r.get("how") != "syncing" for r in S.WATCHING.snapshot())
    except Exception:
        return True                    # unanswerable: leave the disk alone


def playing_drives(copies=False):
    """The drives films are being read from for somebody this minute - or, with
    copies, the drives the cache is copying from."""
    drives = set()
    try:
        for r in S.WATCHING.snapshot():
            if (r.get("how") == "syncing") != copies:
                continue
            path = (S.local().file_for(str(r.get("key") or ""), 0) or {}).get("file") or ""
            drives.add(os.path.splitdrive(path)[0].upper() or "?")
    except Exception:
        drives.add("?")
    return drives


def loudness_file():
    return os.path.join(S.ROOT, "loudness.json")


def read_loudness():
    """The cached measurements, read from disk once."""
    if S.LOUDNESS:
        return S.LOUDNESS
    try:
        with open(S.loudness_file(), encoding="utf-8") as f:
            S.LOUDNESS.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    # readings of silence kept before they were refused: dropped, so they are measured
    # again
    for part in [k for k, v in S.LOUDNESS.items()
                 if isinstance(v, dict) and float(v.get("lufs") or 0) <= -60]:
        del S.LOUDNESS[part]
    return S.LOUDNESS


def write_loudness():
    try:
        with open(S.loudness_file(), "w", encoding="utf-8") as f:
            json.dump(S.LOUDNESS, f)
    except OSError:
        pass


def _read_played():
    """played_files.json, once. Read only on a play, a restart with nobody playing left the
    list without these versions and the cache deleted its copies. Unreadable is not empty:
    it is put aside, and a read that failed is tried again."""
    if S.PLAYED_READ:
        return
    where = os.path.join(S.ROOT, "played_files.json")
    try:
        with open(where, encoding="utf-8") as f:
            S.PLAYED_FILES.update(json.load(f) or {})
    except FileNotFoundError:
        pass
    except ValueError:
        try:
            os.replace(where, where + ".bad")
        except OSError:
            return
    except OSError:
        return
    S.PLAYED_READ.append(True)


def played_file(key, path):
    """Note that this file of this title was played, for the copy machine's list."""
    if not key or not path:
        return
    S._read_played()
    now = time.time()
    was = S.PLAYED_FILES.setdefault(str(key), {})
    fresh = now - float(was.get(path) or 0) > 600
    was[path] = now
    if fresh and S.PLAYED_READ:
        for k in list(S.PLAYED_FILES):
            S.PLAYED_FILES[k] = {p: t for p, t in S.PLAYED_FILES[k].items()
                               if now - float(t) < S.PLAYED_DAYS * 86400}
            if not S.PLAYED_FILES[k]:
                del S.PLAYED_FILES[k]
        try:
            tmp = os.path.join(S.ROOT, "played_files.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(S.PLAYED_FILES, f)
            os.replace(tmp, os.path.join(S.ROOT, "played_files.json"))
        except OSError:
            pass


def holding_changes(before, rows, deck, most=8):
    """What a house cache list gained and lost since the one before, as one log line, and
    the list to compare the next one with: {name: why}. "" when nothing changed or there
    was no list before. Each name carries its reason, and whether the ask was a deck one."""
    now = {}
    for r in rows:
        name = str(r.get("name") or "")
        if name and not name.lower().endswith((".srt", ".ass", ".sub", ".idx")):
            now.setdefault(name, "%s, %s" % (r.get("why") or "?", r.get("who") or "?"))
    if not before:
        return "", now
    came = [n for n in now if n not in before]
    went = [n for n in before if n not in now]
    if not came and not went:
        return "", now
    words = ["%d on it (was %d)%s" % (len(now), len(before), ", deck ask" if deck else "")]
    words += ["+ %s (%s)" % (n, now[n]) for n in came[:most]]
    words += ["- %s (was %s)" % (n, before[n]) for n in went[:most]]
    if len(came) > most or len(went) > most:
        words.append("and %d more" % (max(0, len(came) - most) + max(0, len(went) - most)))
    return "; ".join(words), now


def log_rows(rows, done=""):
    """The download log, newest first; with done=N only the newest N finished rows - the
    panel's times of arrival, which parsed all 840 rows into 45 KB and ran out (2026-11-13)."""
    rows = sorted(rows, key=lambda d: -int(d.get("when") or 0))
    try:
        most = int(done)
    except (TypeError, ValueError):
        return rows
    return [d for d in rows if d.get("state") == "done"][:max(0, most)]


def played_files(key):
    """The files of this title played within the days kept, newest first."""
    S._read_played()
    now = time.time()
    got = (S.PLAYED_FILES.get(str(key)) or {}).items()
    return [p for p, t in sorted(got, key=lambda x: -float(x[1]))
            if now - float(t) < S.PLAYED_DAYS * 86400 and os.path.exists(p)]


def codec_of(part):
    """The audio codec the library has for one file, lower case, or ''."""
    con = S.local().lib.db()
    try:
        row = con.execute("SELECT acodec, duration FROM file WHERE id=?", (part,)).fetchone()
    except Exception:
        row = None
    finally:
        con.close()
    return str((row["acodec"] if row else "") or "").lower()


def take_readings(part, path, size, length=0.0):
    """Measure what one file lacks and keep it: its loudness and dialnorm, and for a
    Dolby track the loudness with its own compression off. A file whose first reading
    stands is not read through again for the second. True when anything was kept."""
    import pd_gpu
    import pd_receiver
    codec = S.codec_of(part)
    dolby = codec in pd_receiver.DOLBY
    with S.LOUDNESS_LOCK:
        had = dict(S.read_loudness().get(str(part)) or {})
    whole = (had.get("how") == "spread" and had.get("lufs") is not None
             and int(had.get("size") or 0) == int(size or 0))
    one = had if whole else {}
    # the same file somewhere else - a copy one folder deeper, the same name and size -
    # already measured: its reading is this one's. A 2160p film's second copy was read
    # again and again and never finished.
    twin = None if whole else S.twin_reading(part, path, size)
    if twin:
        one = dict(twin)
        whole = True
    if not whole:
        said = pd_gpu.loudness(path, length=length)
        # -70 is digital silence, ffmpeg's floor: a file still downloading, its
        # sound not yet written, or a track it could not read. No film is that
        # quiet; kept, the player turned it up as though it were whispered.
        if said is None or float(said) <= -60:
            # counted: a file that cannot be read - a copy cut short - was read through
            # again on every pass, for ever
            with S.LOUDNESS_LOCK:
                S.read_loudness()
                was = dict(S.LOUDNESS.get(str(part)) or {})
                n = int(was.get("failed") or 0) if int(was.get("failedSize") or 0) == int(size or 0) else 0
                was.update(failed=n + 1, failedSize=int(size or 0), failedWhen=int(time.time()))
                S.LOUDNESS[str(part)] = was
                S.write_loudness()
            return False
        one = {"lufs": said, "size": int(size or 0), "when": int(time.time()),
               "how": "spread", "dialnorm": pd_gpu.dialnorm(path)}
    if dolby and one.get("flat") is None:
        flat = pd_gpu.loudness(path, length=length, flat=True)
        # unreadable that way: the first reading stands in, so it is not tried for ever
        one["flat"] = flat if flat is not None and float(flat) > -60 else one["lufs"]
    elif whole and not twin:
        return False
    with S.LOUDNESS_LOCK:
        S.read_loudness()                   # loaded before one is added and all written back
        S.LOUDNESS[str(part)] = one
        S.write_loudness()
    S.TYPICAL["at"] = min(S.TYPICAL["at"], time.time() - 1500)    # counted in within five minutes
    return True


def twin_of(name, size, others):
    """The id among `others` - (id, path, size, reading) - of the same file measured
    already: the same name and the same size, read the whole way through."""
    for fid, other, other_size, reading in others:
        if (os.path.basename(str(other)).lower() == str(name).lower()
                and int(other_size or 0) == int(size or 0) > 0
                and isinstance(reading, dict) and reading.get("how") == "spread"
                and reading.get("lufs") is not None
                and int(reading.get("size") or 0) == int(size or 0)):
            return fid
    return None


def twin_reading(part, path, size):
    """The reading of this file's twin elsewhere in the library, or None."""
    name = os.path.basename(str(path or ""))
    if not name or not size:
        return None
    con = S.local().lib.db()
    try:
        rows = con.execute("SELECT id, path, size FROM file WHERE size=? AND id<>?",
                           (int(size), int(part))).fetchall()
    finally:
        con.close()
    with S.LOUDNESS_LOCK:
        known = S.read_loudness()
        others = [(r["id"], r["path"], r["size"], known.get(str(r["id"]))) for r in rows]
    fid = S.twin_of(name, size, others)
    return dict(known.get(str(fid)) or {}) if fid is not None else None


def measure_loudness(part, path, size, playing=False, free_drive=False):
    """Measure one file on a background thread and cache the result.

    `playing` is the one exception to waiting for a quiet machine: the file somebody is
    watching this minute. It is already being read off that disk, and it is the only
    file whose measurement anybody is waiting for - left to the quiet rule it could
    never be measured at all, because the rule is "not while anybody is watching" and
    the file in question is the one being watched.
    """
    def work(turn=None):
        S.MEASURE_NOW["name"] = os.path.basename(path or "")
        S.MEASURE_NOW["since"] = int(time.time())
        try:
            length = 0.0
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT duration FROM file WHERE id=?",
                                  (part,)).fetchone()
                length = float((row["duration"] if row else 0) or 0)
            finally:
                con.close()
            S.take_readings(part, path, size, length)
        except Exception:
            pass
        S.MEASURE_NOW["name"] = ""
        import pd_disk
        pd_disk.done(turn)
        with S.LOUDNESS_LOCK:
            S.MEASURING.discard(str(part))
            waiting = S.MEASURE_NEXT.popitem() if S.MEASURE_NEXT else None
        if waiting:
            S.measure_loudness(waiting[0], waiting[1][0], waiting[1][1], playing=True)
    # Not while a viewer is short of disk: measuring is a 180 s read off the same
    # disks. Two concurrent measurements held a copy at 41 Mbit with the drive at
    # 57 ms latency. An unmeasured file plays uncorrected until the next quiet moment.
    if S.Handler.disk_reading_slow():
        return
    # turned off under Automatic runs: nothing is measured, not even the file playing
    if not S.auto_on("loudness"):
        return
    # a download, the copy or a scan has the disks: only the file on a screen now is measured
    if S.worker_waits(for_screen=playing, other_drive=free_drive):
        return
    import pd_receiver
    codec = S.codec_of(part)
    with S.LOUDNESS_LOCK:
        S.read_loudness()
        if not pd_receiver.wants_measuring(S.LOUDNESS.get(str(part)), codec, size or 0):
            return
        if str(part) in S.MEASURING or len(S.MEASURING) >= 1:
            # one at a time: this is ffmpeg, not a lookup - but the one being watched
            # goes next, ahead of the backlog
            if playing and str(part) not in S.MEASURING:
                S.MEASURE_NEXT.clear()
                S.MEASURE_NEXT[str(part)] = (path, size)
            return
        # a turn on the drive: the file being watched takes one at once, the backlog
        # waits in the drive's line
        import pd_disk
        turn, why = pd_disk.ask(path, "watch" if playing else "work",
                                "Loudness: " + os.path.basename(path or ""), S.disk_slots(path),
                                "loudness")
        if why:
            return
        S.MEASURING.add(str(part))
    threading.Thread(target=work, args=(turn,), name="palladium-loudness", daemon=True).start()


def ahead_file():
    return os.path.join(S.ROOT, "ahead.json")


def read_ahead():
    """When each fetched-ahead title was first named, read from disk once."""
    if S.AHEAD_SINCE:
        return S.AHEAD_SINCE
    try:
        with open(S.ahead_file(), encoding="utf-8") as f:
            S.AHEAD_SINCE.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    return S.AHEAD_SINCE


def write_ahead():
    try:
        with open(S.ahead_file(), "w", encoding="utf-8") as f:
            json.dump(S.AHEAD_SINCE, f)
    except OSError:
        pass


def soon_to_be_watched():
    """The files somebody is about to watch, nearest first: what everyone left part-way
    in the last month, the next three episodes of each series, and each shuffle's next
    draws. Measured before the backlog."""
    now = time.time()
    if now - S.MEASURE_SOON["at"] < 300:
        return S.MEASURE_SOON["rows"]
    S.MEASURE_SOON["at"] = now
    import pd_receiver
    keys = []
    con = S.local().lib.db()
    try:
        rows = con.execute(
            "SELECT key, position, duration FROM progress WHERE updated > ? "
            "ORDER BY updated DESC LIMIT 60", (int(now - 30 * 86400),)).fetchall()
        keys = pd_receiver.soon_keys(
            [(r["key"], r["position"], r["duration"]) for r in rows],
            lambda k: S.local().next_episode_key(con, k),
            lambda p, d: bool(d) and float(p or 0) >= float(d) * 0.9)
    except Exception:
        keys = []
    finally:
        con.close()
    # and what each kept shuffle deals next
    try:
        house = S.Handler.__new__(S.Handler)
        house.role = "owner"
        _decks, _lists, shuffles, _lately = house.cached_for()
        for who in shuffles:
            for k in house.casual_ahead(who)[:5]:
                if str(k) not in keys:
                    keys.append(str(k))
    except Exception:
        pass
    out = []
    con = S.local().lib.db()
    try:
        for k in keys:
            if k.startswith("e"):
                got = con.execute("SELECT id, path, size FROM file WHERE episode_id=?",
                                  (k,)).fetchall()
            else:
                got = con.execute("SELECT id, path, size FROM file WHERE item_id=? "
                                  "AND episode_id IS NULL", (k,)).fetchall()
            out += [(r["id"], r["path"], r["size"]) for r in got if r["path"]]
    except Exception:
        pass
    finally:
        con.close()
    S.MEASURE_SOON["rows"] = out
    return out


def loudness_counts():
    """How many files here are measured and how many are not, kept for half a minute.

    Counted from the files in the library rather than the measurements kept, which
    hold files long gone, and rather than the sweep's own list, which is read once and
    misses everything that arrived after it.
    """
    if time.time() - S.MEASURE_COUNT["at"] > 30:
        try:
            with S.LOUDNESS_LOCK:
                known = dict(S.read_loudness())
            con = S.local().lib.db()
            try:
                ids = [str(r[0]) for r in con.execute(
                    "SELECT id FROM file WHERE path IS NOT NULL").fetchall()]
            finally:
                con.close()
            import pd_receiver
            done = sum(1 for i in ids if (known.get(i) or {}).get("how") == "spread"
                       or pd_receiver.gave_up(known.get(i)))
            S.MEASURE_COUNT.update(at=time.time(), done=done, left=len(ids) - done)
        except Exception:
            pass
    return S.MEASURE_COUNT["done"], S.MEASURE_COUNT["left"]


def loudness_rows():
    """This machine's whole-file loudness readings, named by file: [{name, size, lufs, ...}]."""
    with S.LOUDNESS_LOCK:
        known = {k: v for k, v in S.read_loudness().items()
                 if isinstance(v, dict) and v.get("how") == "spread"}
    if not known:
        return []
    con = S.local().lib.db()
    try:
        files = con.execute("SELECT id, path, size FROM file WHERE path IS NOT NULL").fetchall()
    finally:
        con.close()
    out = []
    for r in files:
        one = known.get(str(r["id"]))
        if one and r["size"]:
            out.append({"name": os.path.basename(r["path"]), "size": int(r["size"]),
                        "lufs": one.get("lufs"), "dialnorm": one.get("dialnorm"),
                        "flat": one.get("flat"), "when": int(one.get("when") or 0)})
    return out


def typical_loudness(episode):
    """The typical measured loudness of films, or of episodes, with a soundtrack a
    receiver takes as it is; worked out every half hour. None while too few are measured."""
    if time.time() - S.TYPICAL["at"] > 1800:
        import pd_receiver
        with S.LOUDNESS_LOCK:
            # as a receiver plays it where that is known: the reading with compression off
            known = {k: (v.get("flat") if v.get("flat") is not None else v.get("lufs"))
                     for k, v in S.read_loudness().items()
                     if isinstance(v, dict) and v.get("how") == "spread"}
        films, episodes = [], []
        con = S.local().lib.db()
        try:
            for r in con.execute("SELECT id, episode_id FROM file WHERE path IS NOT NULL "
                                 "AND acodec IN ('ac3','eac3','dts','truehd')"):
                lufs = known.get(str(r["id"]))
                if lufs is not None and float(lufs) > -60:
                    (episodes if r["episode_id"] else films).append(float(lufs))
        finally:
            con.close()
        S.TYPICAL.update(at=time.time(), film=pd_receiver.typical(films),
                       episode=pd_receiver.typical(episodes))
    return S.TYPICAL["episode" if episode else "film"]


def take_house_loudness():
    """The other server's readings for the files held here too, every half hour, either way
    round: a film measured on one is not read through again on the other. Returns how many
    taken."""
    if time.time() - S.HOUSE_LOUDNESS["at"] < 1800:
        return 0
    S.HOUSE_LOUDNESS["at"] = time.time()
    try:
        rows = json.loads(S.peer_fetch("/follow/loudness", 30) or b"{}").get("loudness") or []
    except ValueError:
        rows = []
    if not rows:
        return 0
    con = S.local().lib.db()
    try:
        files = [(r["id"], r["path"], r["size"]) for r in con.execute(
            "SELECT id, path, size FROM file WHERE path IS NOT NULL")]
    finally:
        con.close()
    import pd_follow
    with S.LOUDNESS_LOCK:
        got = pd_follow.loudness_for_copy(rows, files, S.read_loudness())
        if got:
            S.LOUDNESS.update(got)
            S.write_loudness()
    return len(got)


def loudness_reset():
    """The files whose readings failed too often, tried again; how many."""
    import pd_receiver
    with S.LOUDNESS_LOCK:
        S.read_loudness()
        n = pd_receiver.forget_failures(S.LOUDNESS)
        S.write_loudness()
    S.MEASURE_SWEEP["left"] = None
    return n


def measure_the_backlog():
    """Measure one unmeasured file, whenever nothing is playing and the disk is easy.

    Called from the housekeeping round. One file at a time and only while the machine
    is quiet, so it costs nothing anybody can feel; a library of a few thousand files
    works through itself over a few nights, and a file that arrives tomorrow is picked
    up the same way without anybody asking.
    """
    now = time.time()
    if now - S.MEASURE_SWEEP["at"] < S.MEASURE_EVERY:
        return
    S.MEASURE_SWEEP["at"] = now
    if S.Handler.disk_reading_slow():
        return
    # Somebody watching keeps their own drive, not every drive: the old single-window
    # measurements were up to 9 dB out and are redone meanwhile from the others
    busy = S.playing_drives() if S.playback_short() else set()
    # and the drive a copy is reading from: measuring beside it took a copy to 40 Mbit
    busy |= S.playing_drives(copies=True)
    if "?" in busy:
        return
    with S.LOUDNESS_LOCK:
        S.read_loudness()
        if S.MEASURING:
            return                     # one is running: it is ffmpeg, not a lookup
        known = dict(S.LOUDNESS)
    left = S.MEASURE_SWEEP["left"]
    if not left:
        # Read once and worked through, rather than a query per file: the point is to
        # be cheap enough to run every twenty seconds for a week.
        con = S.local().lib.db()
        try:
            rows = con.execute(
                "SELECT id, path, size, acodec FROM file WHERE path IS NOT NULL "
                "ORDER BY id DESC").fetchall()
        finally:
            con.close()
        # unmeasured first, then the ones measured from a single window, then the
        # Dolby tracks that lack the reading with compression off
        import pd_receiver
        S.MEASURE_SWEEP["codec"] = {str(r["id"]): str(r["acodec"] or "").lower() for r in rows}
        left = pd_receiver.measure_order(
            [(r["id"], r["path"], r["size"], r["acodec"]) for r in rows], known)
        S.MEASURE_SWEEP["left"] = left
    import pd_receiver
    codecs = S.MEASURE_SWEEP.get("codec") or {}
    # what is about to be watched goes before the backlog
    soon = pd_receiver.first_soon(S.soon_to_be_watched(), known, codecs, busy)
    if soon:
        S.measure_loudness(soon[0], soon[1], soon[2] or 0, free_drive=bool(busy))
        return
    # the backlog is background work: it waits for the copy, a download and a scan
    if S.worker_waits(other_drive=True):
        return
    skipped = []
    while left:
        part, path, size = left.pop()
        if not pd_receiver.wants_measuring(known.get(str(part)), codecs.get(str(part))):
            continue
        if os.path.splitdrive(path or "")[0].upper() in busy:
            skipped.append((part, path, size))
            continue
        S.measure_loudness(part, path, size or 0, free_drive=bool(busy))
        break
    # a file on the drive being watched from waits for the next turn
    left.extend(reversed(skipped))


def keep_measuring():
    """Work through the unmeasured files, one at a time, for as long as the server runs.

    Its own loop rather than a turn in the folder watcher: that one walks the media
    folders to notice new files, and asking it to tick three times as often to measure
    faster would have tripled the walking as well.
    """
    while True:
        try:
            S.take_house_loudness()
        except Exception:
            pass                      # the main server is off: measured here as before
        try:
            S.take_peer_stills()
        except Exception:
            pass                      # the other server is off: made here as before
        try:
            S.measure_the_backlog()
        except Exception:
            pass                      # measuring is never worth taking the server down
        time.sleep(S.MEASURE_EVERY)


def volume_gain(part, path=None, size=0, cfg=None, playing=False):
    """Correction in dB for this file, or 0.

    Every measured file is corrected, to the cap. A dead band left anything within
    4 dB of the target alone, which put two files measuring 0.2 dB apart 3.9 dB apart
    out of the speakers - the opposite of what evening out is for.
    """
    stored = cfg if cfg is not None else (S.read_settings() or {})
    if not stored.get("evenVolume"):
        return 0.0
    with S.LOUDNESS_LOCK:
        known = S.read_loudness().get(str(part))
    if not known:
        if path:
            S.measure_loudness(part, path, size, playing)
        return 0.0
    if known.get("how") != "spread" and path:
        S.measure_loudness(part, path, size, playing)    # the old single window, again
    try:
        target = float(stored.get("volumeTarget") or S.VOLUME_TARGET)
    except (TypeError, ValueError):
        target = S.VOLUME_TARGET
    off = target - float(known.get("lufs") or target)
    return round(max(-S.VOLUME_MOST, min(S.VOLUME_MOST, off)), 1)


def hand_over_the_gain():
    """Give pd_localapi the gain and loudness lookups. Called once at startup."""
    try:
        import pd_localapi
        pd_localapi.GAIN_OF[0] = lambda part, path, size: S.volume_gain(part, path, size)
        pd_localapi.LOUDNESS_OF[0] = lambda part: (
            (S.read_loudness().get(str(part)) or {}).get("lufs"))
    except Exception:
        pass


def sittings(rows):
    """The watch log's rows as viewings: one person's rows of one title that overlap in time
    are one sitting - written by both servers when it moved between them, or opened again -
    and each sitting counts from its first report to its last, but for no more than the
    length of what was watched: a player left running reports for hours, and one twenty-
    minute episode claimed thirty-five of them. Each as {who, key, title, started, took,
    casual}."""
    def length(d):
        d = d or 0
        return int(d / 1000) if d > 10000 else int(d)
    groups = {}
    for r in rows:
        mark = (str(r.get("who") or ""), str(r.get("key") or "") or str(r.get("title") or ""))
        groups.setdefault(mark, []).append(r)
    out = []
    for (who, _), rs in groups.items():
        rs.sort(key=lambda r: int(r.get("started") or 0))
        cur = None
        for r in rs:
            start = int(r.get("started") or 0)
            end = max(int(r.get("updated") or 0), start)
            if cur and start <= cur["end"]:
                cur["end"] = max(cur["end"], end)
                cur["length"] = max(cur["length"], length(r.get("duration")))
                cur["casual"] = cur["casual"] and bool(r.get("casual"))
                continue
            if cur:
                out.append(cur)
            cur = {"who": who, "key": r.get("key"), "title": r.get("title"), "started": start,
                   "end": end, "length": length(r.get("duration")), "casual": bool(r.get("casual"))}
        if cur:
            out.append(cur)
    for v in out:
        took = v["end"] - v["started"]
        v["took"] = min(took, v["length"]) if v["length"] > 0 else took
    return out
