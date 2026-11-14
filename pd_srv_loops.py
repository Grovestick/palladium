"""The server's own errands on a timer: folders watched, the app released, lists and fronts kept ready, stills, credits, casts and the subtitle check.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pd_h
import pd_log
import pd_safe

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def folder_fingerprint(folders):
    """A cheap summary of what the library's folders look like right now.

    Directory timestamps and names, two levels down - and the timestamp of every folder
    at the second level as well, which is the one that matters for a series: an episode
    dropped into Season 4 changes nothing about the programme's own folder, nor the list
    of seasons inside it. Only Season 4's own timestamp moves.

    Names and timestamps only, never the files' contents: quick enough to do every
    minute on a spinning disk.
    """
    marks = []
    for root in folders:
        if not os.path.isdir(root):
            continue
        try:
            for name in sorted(os.listdir(root)):
                here = os.path.join(root, name)
                try:
                    marks.append("%s:%d" % (name, int(os.path.getmtime(here))))
                    if os.path.isdir(here):
                        for inner in sorted(os.listdir(here))[:400]:
                            below = os.path.join(here, inner)
                            try:
                                if os.path.isdir(below):
                                    marks.append("%s:%d" % (inner,
                                                            int(os.path.getmtime(below))))
                                else:
                                    marks.append(inner)
                            except OSError:
                                marks.append(inner)
                except OSError:
                    continue
        except OSError:
            continue
    return hash(tuple(marks))


def watch_folders():
    """Notice new files, and scan once they have stopped arriving.

    A download writes for as long as it takes; scanning halfway through reads a
    truncated file and has to be done again. So a change is noted, and the scan waits
    until nothing has changed for two minutes.
    """
    settled_after = 120
    was, changed_at = None, 0
    while True:
        try:
            if S.Handler.game_holds("scans"):
                # the machine is wanted for something else: reading a library is
                # exactly the kind of work that can wait an hour
                time.sleep(60)
                continue
            cfg = S.local().lib.config()
            every = int(cfg.get("scanEvery") or 0)
            watching = bool(cfg.get("scanOnChange"))
            folders = (cfg.get("movies") or []) + (cfg.get("tv") or []) + \
                      (cfg.get("mixed") or [])
            now = time.time()

            if watching and folders:
                mark = S.folder_fingerprint(folders)
                if was is None:
                    was = mark
                elif mark != was:
                    was, changed_at = mark, now
                elif changed_at and now - changed_at >= settled_after:
                    changed_at = 0
                    if not S.local().lib.scan_state.get("running"):
                        S.local().lib.scan()

            if every:
                last = S.local().lib.config().get("scannedAt", 0)
                if now - last >= every * 60 and not S.local().lib.scan_state.get("running"):
                    S.local().lib.scan()
                    cfg = S.local().lib.config()
                    cfg["scannedAt"] = int(now)
                    S.local().lib.save_config(cfg)
        except Exception:
            pass                      # a watcher is never worth taking the server down
        time.sleep(60)


def keepalive_loop():
    """Twice a day is plenty to notice that forty days have gone by."""
    while True:
        try:
            outcome = S.subtitle_keepalive()
            if "nothing to do" not in outcome:
                pd_log.raw("%s subtitles: %s%s"
                        % (time.strftime("%Y-%m-%d %H:%M"), outcome, chr(10)))
        except Exception:
            pass                       # a keep-alive is never worth taking the server down
        time.sleep(12 * 3600)


def keep_countries():
    """Fill in where each series was made, thirty at a time, until every one is known;
    then look again every hour for the ones added since."""
    while True:
        try:
            done = S.local().lib.fill_countries()
        except Exception:
            done = -1
        time.sleep(60 if done > 0 else 3600)


def app_release(now=None):
    """The newest app given to everyone, if it is newer than the released one; the first
    time, the app already here is the released one. Open fault reports do not hold it
    back: one old report held every app for a week, and the owner said ship it at eight
    regardless (2026-11-13). What was done, in words, or ""."""
    newest = S.Handler.app_beside_us()
    if not newest:
        return ""
    here = os.path.dirname(newest)
    keep = os.path.join(S.ROOT, S.APP_RELEASED)

    def code(folder):
        try:
            with open(os.path.join(folder, "version.json"), encoding="utf-8") as f:
                said = json.load(f) or {}
            return int(said.get("versionCode") or 0), str(said.get("versionName") or "")
        except (OSError, ValueError):
            return 0, ""
    new, name = code(here)
    had, had_name = code(keep)
    if not new or new <= had:
        return ""
    os.makedirs(keep, exist_ok=True)
    for one in ("palladium.apk", "version.json"):
        shutil.copy2(os.path.join(here, one), os.path.join(keep, one + ".part"))
    # the number last, so nobody is told of a file that is not there yet
    os.replace(os.path.join(keep, "palladium.apk.part"), os.path.join(keep, "palladium.apk"))
    os.replace(os.path.join(keep, "version.json.part"), os.path.join(keep, "version.json"))
    return "app %s released to everyone%s" % (name, (" (was %s)" % had_name) if had else "")


def keep_app_released():
    """At eight each morning the newest app goes to everyone, whatever reports are open;
    the owner's own screens have it as soon as it is built (owner, 2026-11-05)."""
    import pd_beat
    # the day of the last eight o'clock look, kept on disk: held in memory, every install
    # after eight was that day's look and let the new app out (0.19.403 at 09:51)
    mark = os.path.join(S.ROOT, S.APP_RELEASED, "looked")
    try:
        with open(mark, encoding="utf-8") as f:
            done = f.read().strip()
    except OSError:
        done = ""
    try:
        if not os.path.exists(os.path.join(S.ROOT, S.APP_RELEASED, "version.json")):
            said = S.app_release()
            if said:
                pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), said, chr(10)))
    except Exception:
        pass
    while True:
        today = time.strftime("%Y-%m-%d")
        if done != today and time.localtime().tm_hour >= S.APP_RELEASE_HOUR:
            done = today
            try:
                os.makedirs(os.path.dirname(mark), exist_ok=True)
                with open(mark, "w", encoding="utf-8") as f:
                    f.write(today)
            except OSError:
                pass
            try:
                said = S.app_release()
                if said:
                    pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), said, chr(10)))
            except Exception as e:
                pd_log.raw("%s app release: %s%s" % (time.strftime("%H:%M:%S"), str(e)[:120], chr(10)))
        pd_beat.sleep("apprelease", 600)


def fronts_load(name=S.FRONTS_FILE):
    """The fronts the last making left, into the kept ones. The day they were made, or ""."""
    try:
        with open(os.path.join(S.ROOT, name), encoding="utf-8") as f:
            said = json.load(f) or {}
    except (OSError, ValueError):
        return ""
    S.local().start_keep([(str(x.get("who") or ""), str(x.get("kind") or "movie"), x.get("page"))
                        for x in said.get("fronts") or [] if x.get("page") is not None],
                       float(said.get("at") or 0))
    return str(said.get("day") or "")


def fronts_make(only=()):
    """Every active viewer's fronts made in a process of its own, on a copy of the settings
    and the library under its own root: fourteen fronts took a whole core for five minutes
    on the cache server, and in this one every search waited behind them (2026-11-05).
    `only`: these viewers', merged into the day's file. What it wrote is read back; True
    when it wrote it."""
    import sqlite3
    work = os.path.join(S.ROOT, "fronts-work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    for name in os.listdir(S.ROOT):
        if name.endswith(".json") and os.path.isfile(os.path.join(S.ROOT, name)):
            shutil.copy2(os.path.join(S.ROOT, name), os.path.join(work, name))
    src = sqlite3.connect(os.path.join(S.ROOT, "library.db"), timeout=30)
    dst = sqlite3.connect(os.path.join(work, "library.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    out = os.path.join(S.ROOT, "start_fronts_some.json" if only else S.FRONTS_FILE)
    # installed, the server beside sys.executable, as restart_self finds it: started as
    # sys.executable itself, every making failed with "cannot find the file" (0.19.398)
    me = ([os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "palladium-server.exe")]
          if S.PACKAGED else [sys.executable, os.path.abspath(__file__)])
    flags = 0x00004000 | 0x08000000 if os.name == "nt" else 0   # below normal, no window
    try:
        subprocess.run(me + ["--root", work, "--make-fronts", out, "--no-tray", "--no-open"]
                       + (["--fronts-for", ",".join(only)] if only else []),
                       timeout=S.FRONTS_PATIENCE, creationflags=flags,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pd_log.raw("%s Start fronts: their process took over %d min and was stopped%s"
                   % (time.strftime("%H:%M:%S"), S.FRONTS_PATIENCE // 60, chr(10)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not only:
        return S.fronts_load() == time.strftime("%Y-%m-%d")
    # theirs into the kept ones, and into the day's file so a restart keeps them too
    if S.fronts_load("start_fronts_some.json") != time.strftime("%Y-%m-%d"):
        return False
    S.fronts_merge(os.path.join(S.ROOT, "start_fronts_some.json"), os.path.join(S.ROOT, S.FRONTS_FILE))
    return True


def fronts_merge(some, day):
    """The fronts in `some` over the same viewers' in the day's file."""
    try:
        with open(some, encoding="utf-8") as f:
            new = json.load(f) or {}
        with open(day, encoding="utf-8") as f:
            had = json.load(f) or {}
    except (OSError, ValueError):
        return
    mark = lambda x: (x.get("who"), x.get("kind"))
    fresh = {mark(x) for x in new.get("fronts") or []}
    had["fronts"] = [x for x in had.get("fronts") or [] if mark(x) not in fresh] + list(new.get("fronts") or [])
    part = day + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(had, f)
    os.replace(part, day)


def fronts_child(out):
    """The fronts' own process: every active viewer's films and series front, written to
    `out` with the day, and gone."""
    api = S.local()
    con = api.lib.db()
    only = [w for w in S.FRONTS_FOR.split(",") if w]
    try:
        api.start_make_ahead(con, only=only or None)
        fronts = [{"who": k[0], "kind": k[1], "page": v[1]}
                  for k, v in list(api._START_KEPT.items()) if k[2] == "0" and k[3] == ""
                  and (not only or k[0] in only)]
    finally:
        con.close()
    part = out + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump({"day": time.strftime("%Y-%m-%d"), "at": time.time(), "fronts": fronts}, f)
    os.replace(part, out)
    os._exit(0)


def keep_start_ready():
    """The Start front of everyone who watched anything this last month, made once a day
    per server (owner, 2026-11-05) in a process of its own, so opening Palladium loads it
    rather than waits for it - and the server answers while it is made."""
    import pd_beat
    try:
        made = S.fronts_load()
    except Exception:
        made = ""
    # ten minutes after starting: right after an install the shelves are being read for
    # the first time
    pd_beat.sleep("start", S.START_AHEAD_AFTER)
    while True:
        try:
            # whoever watched something since: theirs again, the rest stand
            stale = S.local().start_stale_take()
            if made != time.strftime("%Y-%m-%d"):
                if S.fronts_make():
                    made = time.strftime("%Y-%m-%d")
            elif stale:
                S.fronts_make(only=stale)
        except Exception as e:
            pd_log.raw("%s Start fronts: %s%s" % (time.strftime("%H:%M:%S"), str(e)[:120], chr(10)))
        pd_beat.sleep("start", 600)


def keep_lists():
    """Every day: each kept list read again from where it comes from - IMDb's top 100
    from its chart - and what is new on it looked up in the catalogue."""
    import pd_beat
    while True:
        try:
            api = S.local()
            con = api.lib.db()
            try:
                api.refresh_lists(con)
            finally:
                con.close()
        except Exception:
            pass                       # a list not refreshed is the list of yesterday
        pd_beat.sleep("lists", 6 * 3600)


def keep_the_hats():
    """Every ten minutes: what each shuffle draws next that only a pack holds, fetched.

    It was asked only when the machine keeping copies asked for its list - at night and
    when it starts - so a round's next episodes waited all day and were all fetched at
    once the next time both servers restarted.
    """
    import pd_beat
    house = S.Handler.__new__(S.Handler)
    house.role = "owner"
    while True:
        pd_beat.sleep("hats", 600)
        try:
            # the main server's work: the packs download onto its disk, and a machine
            # keeping copies takes what it has from there
            import pd_follow
            if pd_follow.settings(S.local().lib.config()).get("master"):
                continue
            _decks, _lists, shuffles, _lately = house.cached_for()
            for who in shuffles:
                house.fetch_the_hat(who, [str(k) for k in house.casual_ahead(who)])
        except Exception:
            pass                      # a fetch ahead is never worth a failure


def heartbeat(every=30.0):
    """A line every half minute, in a file that is otherwise never quiet.

    A log nobody writes to says nothing about a silent minute: a server that was
    stalled and a server that was refusing connections from a full queue look exactly
    alike from outside. This ticks regardless, so a gap in it is the process itself
    having stopped, and ticks either side of an outage mean the process was fine and
    the trouble was the queue or the network. The counters say what it was carrying
    when it happened.
    """
    where = os.path.join(S.ROOT, "heartbeat.log")
    beat = 0
    was_at = time.time()
    was_cpu = time.process_time()
    was_taken = S.BEATS["taken"]
    while True:
        time.sleep(every)
        now, cpu, taken = time.time(), time.process_time(), S.BEATS["taken"]
        gap = now - was_at
        line = ("%s beat %d  %d connections  %d threads  %.1f s cpu  "
                "%.1f s since the last%s"
                % (time.strftime("%H:%M:%S", time.localtime(now)), beat,
                   taken - was_taken, threading.active_count(), cpu - was_cpu, gap,
                   "  <- STALLED" if gap > every * 1.8 else "")) + chr(10)
        try:
            # a file of its own, kept to a few days rather than for ever
            if os.path.exists(where) and os.path.getsize(where) > 2 * 1024 * 1024:
                try:
                    os.replace(where, where + ".1")
                except OSError:
                    pass
            with open(where, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass                           # a log that cannot be written is not a fault
        beat += 1
        was_at, was_cpu, was_taken = now, cpu, taken


def start_apart():
    """Watch whether the main server and its cache are on different connections."""
    import pd_apart
    import pd_follow
    try:
        one = pd_follow.settings(S.local().lib.config())
        role = "cache" if (one.get("on") and one.get("master")) else "host"
    except Exception:
        role = "host"

    def doors():
        # the cache's own address first, then its outside one: at home the first
        # answers, away from home only the second can
        where = str(S.Handler.STANDBY.get("where") or "")
        outside = str(S.Handler.STANDBY.get("outside") or "")
        if outside and "://" not in outside:
            outside = "http://" + outside
        key = next((str(r.get("token")) for r in S.INVITES.load()
                    if r.get("follows") and r.get("token")), "")
        return [d for d in (where, outside) if d], key

    def choice():
        return str((S.read_settings() or {}).get("apartPause") or "host")

    def say(text):
        try:
            pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))
        except OSError:
            pass

    pd_apart.start(S.ROOT, role, {"doors": doors, "choice": choice, "say": say})


def auto_set(name):
    """Whether the owner has one automatic job on: what Automatic runs shows."""
    said = (S.read_settings() or {}).get("autoRuns") or {}
    return said.get(name, True) is not False


def auto_on(name):
    """Whether one automatic job may run now: on, and the boss has not stopped it."""
    import pd_boss
    return S.auto_set(name) and not pd_boss.rested(name)


def start_cast_index():
    """Who is in every film the packs carry, a film every few seconds from the
    catalogue, so an actor's name finds their films before anybody has opened one."""
    import pd_beat

    def work():
        pd_beat.sleep("cast", 120, "first check")
        while True:
            try:
                if not S.auto_on("cast"):
                    pd_beat.sleep("cast", 300, "off")
                    continue
                why = S.worker_waits()
                if why:
                    pd_beat.sleep("cast", 60, why)
                    continue
                pd_beat.beat("cast", "reading packs", 1800)
                import pd_torrents
                con = S.local().lib.db()
                try:
                    known = {str(r[0]) for r in con.execute(
                        "SELECT DISTINCT item_id FROM credit WHERE item_id LIKE 'o%'")}
                finally:
                    con.close()
                todo = [o for o in pd_torrents.offered()
                        if str(o.get("ratingKey")) not in known
                        and (o.get("tmdb") or o.get("tmdbId"))]
                if not todo:
                    pd_beat.sleep("cast", 3600, "nothing new")
                    continue
                for o in todo[:200]:
                    if not S.auto_on("cast") or S.worker_waits():
                        break
                    pd_beat.beat("cast", "reading a cast", 600)
                    try:
                        S.local()._cast_of(str(o["ratingKey"]), "movie",
                                         o.get("tmdb") or o.get("tmdbId"))
                    except Exception:
                        pass
                    pd_beat.sleep("cast", 3)
            except Exception:
                pd_beat.sleep("cast", 300, "failed, again soon")

    def tally():
        import pd_torrents
        con = S.local().lib.db()
        try:
            known = {str(r[0]) for r in con.execute(
                "SELECT DISTINCT item_id FROM credit WHERE item_id LIKE 'o%'")}
        finally:
            con.close()
        films = [o for o in pd_torrents.offered() if (o.get("tmdb") or o.get("tmdbId"))]
        done = sum(1 for o in films if str(o.get("ratingKey")) in known)
        return done, len(films) - done

    pd_beat.counter("cast", tally)
    threading.Thread(target=work, name="palladium-cast", daemon=True).start()


def stills_todo():
    """The files whose timeline stills are still to be made, in the order to make them."""
    import pd_localapi
    progress, files, done = S.stills_inputs()
    return pd_localapi.stills_order(progress, files, done, time.time())


def stills_count():
    """(done, left) over every file the stills worker would take, not only the next forty."""
    import pd_localapi
    progress, files, done = S.stills_inputs()
    every = pd_localapi.stills_order(progress, files, set(), time.time(), most=None)
    made = sum(1 for f in every if f["id"] in done)
    return made, len(every) - made


def stills_inputs():
    """What the stills order is worked out from: (progress, files, done ids)."""
    import pd_localapi
    root = S.local().lib.root
    con = S.local().lib.db()
    try:
        progress = [dict(r) for r in con.execute(
            "SELECT key, position, duration, updated, marked FROM progress "
            "WHERE updated > ?", (int(time.time()) - 30 * 86400,))]
        files = [dict(r) for r in con.execute(
            "SELECT f.id, f.fid, f.item_id, f.episode_id, f.ctime, f.duration, f.path, "
            "i.title FROM file f JOIN item i ON i.id=f.item_id")]
        # the episode after each one watched lately: same programme, next number, or
        # the first of the next season
        eps = {}
        for r in con.execute("SELECT id, item_id, season, number FROM episode"):
            eps[str(r["id"])] = (str(r["item_id"]), int(r["season"] or 0), int(r["number"] or 0))
    finally:
        con.close()
    place = {v: k for k, v in eps.items()}
    after = {}
    for p in progress:
        at = eps.get(str(p["key"]))
        if not at:
            continue
        nxt = place.get((at[0], at[1], at[2] + 1)) or place.get((at[0], at[1] + 1, 1))
        if nxt:
            after[nxt] = str(p["key"])
    for f in files:
        f["next_of"] = after.get(str(f.get("episode_id") or ""))
    folder = os.path.join(root, "cache", "frames")
    done = {f["id"] for f in files
            if f.get("fid") and os.path.exists(os.path.join(folder, str(f["fid"]), ".done"))}
    return progress, files, done


def start_fixer():
    """The file fixer, as background work: it waits as the others do."""
    import pd_fixer

    def wait():
        if not S.auto_on("fixer"):
            return "off"
        return S.worker_waits()

    def log(text):
        pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))

    pd_fixer.start(S.ROOT, lambda: S.local().lib, wait, log)


def start_speed():
    """qBittorrent's speed: every rule is in pd_speed."""
    import pd_speed

    def here():
        # what /watching says, which knows a paused player from a playing one; not answering,
        # a stream going out still counts
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/watching" % S.PORT, timeout=4) as answer:
                return pd_speed.viewing(json.loads(answer.read()).get("live") or [])
        except Exception:
            return any(r.get("how") != "syncing" for r in S.WATCHING.snapshot())

    def log(text):
        pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))

    pd_speed.start(S.other_server, here, S.Handler.how_thin, log)


def start_boss():
    """The boss over every worker, and the ones it may start again."""
    import pd_boss

    def log(text):
        pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))

    pd_boss.watch("fixer", "palladium-fixer", S.start_fixer)
    pd_boss.watch("xseed", "palladium-xseed", S.start_xseed)
    pd_boss.watch("speed", "palladium-speed", S.start_speed)
    def viewers():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/watching" % S.PORT, timeout=8) as answer:
                live = json.loads(answer.read()).get("live") or []
        except Exception:
            return []
        return [r for r in live if r.get("how") != "syncing"]

    def other_up():
        # asked as the two servers ask each other, with the key: /config asked bare was
        # refused by the main server every minute, so a copy read it as down (2026-11-07)
        url, _ = S.other_server()
        return bool(url) and S.peer_fetch("/follow/live", 4) is not None

    import pd_follow
    one = pd_follow.settings(S.local().lib.config())
    import pd_ai_subs
    pd_boss.start(log, S.playback_short, S.ROOT, viewers, other_up, S.restart_self,
                  pd_boss.COPY_WAITS if one.get("on") and one.get("master") else 0,
                  lambda: (S.read_settings() or {}).get("weeklyRestart"), ai=pd_ai_subs)


def start_xseed():
    """The library's files seeded on every upload of the same release. Asking the tracker
    reads no disk; a check waits as the others do, on the drive it would read."""
    import pd_xseed

    def wait(drive):
        if not S.auto_on("xseed"):
            return "off"
        return S.worker_waits(other_drive=drive not in S.playing_drives(), in_line=True) if drive else ""

    def log(text):
        pd_log.raw("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))

    pd_xseed.start(S.ROOT, lambda: S.local().lib, wait, log)


def start_weekly():
    """This week's picks, drawn on the main server once a week; a copy takes them from it
    with the shared settings."""
    import datetime
    import pd_follow
    import pd_weekly

    def work():
        while True:
            try:
                one = pd_follow.settings(S.local().lib.config())
                stored = S.read_settings() or {}
                if (not (one.get("on") and one.get("master"))
                        and pd_weekly.due(stored.get("weeklyPicks"), datetime.date.today())):
                    con = S.local().lib.db()
                    try:
                        picks = pd_weekly.draw(con, stored.get("weeklyPicks"))
                        # and the Start tab's pick with it: the first drawn, its own
                        # summary as the line. One set by hand stands until Monday.
                        row = con.execute(
                            "SELECT i.id, i.overview FROM item i JOIN file f ON f.item_id = i.id "
                            "WHERE i.type = 'movie' AND i.tmdb_id = ? LIMIT 1",
                            (picks["tmdb"][0],)).fetchone() if picks["tmdb"] else None
                    finally:
                        con.close()
                    if picks["tmdb"]:
                        S.write_settings(dict({"weeklyPicks": picks}, **(
                            {"weekPick": {"key": str(row[0]),
                                          "note": pd_weekly.line(row[1] or "")}} if row else {})))
            except Exception:
                pass
            time.sleep(3600)

    threading.Thread(target=work, name="palladium-weekly", daemon=True).start()


def start_claude():
    """Faults read by Claude Code, when the owner has turned it on; its answer goes on the
    board for the owner only."""
    import pd_claude

    def on():
        return str(S.local().lib.config().get("claudeFaults") or "off") == "on"

    def told():
        return str(S.local().lib.config().get("claudeSource") or "")

    def board(text, name):
        row = {"id": "%x" % (int(time.time() * 1000) % 0xFFFFFFFFFF),
               "when": int(time.time()), "who": "Claude", "kind": "claude",
               "text": (text or "").strip()[:2000] + chr(10) + chr(10) + "claude" + os.sep + name,
               "from": "", "app": "", "source": "person", "owner": True}
        with open(os.path.join(S.ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + chr(10))

    pd_claude.start(S.ROOT, S.CODE, on, told, board)


def start_stills():
    """Every timeline still of what is being watched and what came in lately, made ahead
    while nothing plays: one ffmpeg at a time, at the lowest priority."""
    import pd_beat
    import pd_localapi

    def work():
        import pd_disk
        import shutil
        pd_beat.sleep("stills", 300, "first check")
        # the folders named by a row number, from before the stills were kept by the file's
        # own id: a number given again to another file showed that file the wrong stills
        try:
            folder = S.stills_folder()
            old = pd_localapi.stale_frame_folders(os.listdir(folder)) if os.path.isdir(folder) else []
            for n in old:
                shutil.rmtree(os.path.join(folder, n), ignore_errors=True)
            if old:
                pd_log.raw("%s stills: %d folders kept by row number let go%s"
                           % (time.strftime("%H:%M:%S"), len(old), chr(10)))
        except OSError:
            pass
        while True:
            try:
                if not S.auto_on("stills"):
                    pd_beat.sleep("stills", 300, "off")
                    continue
                if not S.quiet_now():
                    pd_beat.sleep("stills", 120, "waiting for playback to end")
                    continue
                pd_beat.beat("stills", "choosing", 600)
                todo = S.stills_todo()
                if not todo:
                    pd_beat.sleep("stills", 1800, "nothing new")
                    continue
                f = todo[0]
                turn, why = pd_disk.ask(f["path"], "work", "Stills: " + str(f.get("title") or ""),
                                        S.disk_slots(f["path"]), "stills")
                if why:
                    pd_beat.sleep("stills", 60, why)
                    continue
                try:
                    root = S.local().lib.root
                    step = pd_localapi.LocalAPI.FRAME_STEP
                    whole = True
                    for n, sec in enumerate(range(0, int(float(f["duration"])), step)):
                        if n % 10 == 0 and (not S.quiet_now() or not S.auto_on("stills")):
                            whole = False
                            break
                        kept = pd_localapi.frame_file(root, f["fid"], sec, pd_localapi.STILL_WIDTH)
                        if os.path.exists(kept):
                            continue
                        pd_beat.beat("stills", str(f.get("title") or "a film"), 120)
                        if not os.path.exists(f["path"]):
                            whole = False
                            break
                        pd_localapi.make_frame(f["path"], kept, sec, pd_localapi.STILL_WIDTH,
                                               idle=True)
                    if whole:
                        mark = os.path.join(root, "cache", "frames", str(f["fid"]), ".done")
                        os.makedirs(os.path.dirname(mark), exist_ok=True)
                        with open(mark, "w", encoding="utf-8") as m:
                            m.write(str(int(time.time())))
                    elif not os.path.exists(f["path"]):
                        pd_beat.sleep("stills", 600, "a file is missing")
                        continue
                finally:
                    pd_disk.done(turn)
                pd_beat.sleep("stills", 2)
            except Exception:
                pd_beat.sleep("stills", 300, "failed, again soon")

    def tally():
        return S.stills_count()

    pd_beat.counter("stills", tally)
    threading.Thread(target=work, name="palladium-stills", daemon=True).start()


def settle_requests():
    """Close a film request once the film can be had: in the library, in a pack here, or
    on the tracker - a download link is the answer to asking. Returns how many closed."""
    import pd_localapi
    import pd_torrents
    import pd_tracker
    book = S.Handler.read_requests()
    open_ = {k: r for k, r in book.items()
             if isinstance(r, dict) and not r.get("done") and r.get("title")}
    if not open_:
        return 0
    bare = pd_localapi.bare
    near = lambda a, b: not a or not b or abs(int(a) - int(b)) <= 1
    con = S.local().lib.db()
    try:
        held = [(bare(r["title"]), r["year"]) for r in con.execute(
            "SELECT title, year FROM item WHERE type='movie'")]
    finally:
        con.close()
    try:
        packs = [(bare(o.get("title")), o.get("year")) for o in pd_torrents.offered()]
    except Exception:
        packs = []
    try:
        rows = [(bare(r.get("title") or r.get("name")), r.get("year"))
                for r in list(pd_tracker.read().values())]
    except Exception:
        rows = []
    closed = 0
    for key, r in open_.items():
        want, year = bare(r["title"]), r.get("year")
        why = ("in the library" if any(t == want and near(y, year) for t, y in held) else
               "in a pack" if any(t == want and near(y, year) for t, y in packs) else
               "on the tracker" if any(t == want and near(y, year) for t, y in rows) else "")
        if why:
            book[key].update(done=True, doneWhen=int(time.time()), doneWhy=why)
            closed += 1
    if closed:
        S.Handler.write_requests(book)
    return closed


def subcheck_asked(add=None, drop=None):
    """The (key, language) pairs asked to be gone through, as a list of pairs; with
    `add` or `drop`, the list after those are put on or taken off. Kept in
    subcheck_asked.json beside the settings, written whole and swapped in."""
    path = os.path.join(S.ROOT, "subcheck_asked.json")
    with S.SUBCHECK_ASKED_LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                rows = [[str(p[0]), str(p[1])] for p in json.load(f) if len(p) >= 2]
        except (OSError, ValueError, TypeError):
            rows = []
        if add is None and drop is None:
            return rows
        gone = {(str(k), str(l)) for k, l in (drop or [])}
        rows = [p for p in rows if (p[0], p[1]) not in gone]
        have = {(p[0], p[1]) for p in rows}
        for k, l in add or []:
            if (str(k), str(l)) not in have:
                have.add((str(k), str(l)))
                rows.append([str(k), str(l)])
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f)
                pd_safe.settle(f)
            os.replace(tmp, path)
        except OSError:
            pass
        return rows


def first_in_line(days=30):
    """What the analysers take first: what has just arrived, newest first; then what is on
    anybody's watchlist - a series as its episodes in order; then what is being watched.
    Keys of films and episodes, each once."""
    now = int(time.time())
    con = S.local().lib.db()
    try:
        arrived = [str(r["key"]) for r in con.execute(
            """SELECT COALESCE(episode_id, item_id) AS key FROM file
               WHERE duration > 600 AND COALESCE(ctime, mtime) > ?
               GROUP BY key ORDER BY MAX(COALESCE(ctime, mtime)) DESC""",
            (now - days * 86400,))]
        stored = S.read_settings() or {}
        marked = []
        for mine in list((stored.get("users") or {}).values()) + [stored]:
            if isinstance(mine, dict):
                marked += [str(k) for k in (mine.get("watchlist") or [])]
        listed = []
        for key in dict.fromkeys(marked):
            row = con.execute("SELECT type FROM item WHERE id=?", (key,)).fetchone()
            if not row:
                continue
            if row["type"] == "show":
                listed += [str(r["id"]) for r in con.execute(
                    "SELECT id FROM episode WHERE item_id=? ORDER BY season, number",
                    (key,))]
            else:
                listed.append(key)
        watching = [str(r["key"]) for r in con.execute(
            """SELECT key FROM progress WHERE COALESCE(casual, 0) = 0
               AND duration > 600 AND updated > ?
               GROUP BY key ORDER BY MAX(updated) DESC""", (now - days * 86400,))]
    finally:
        con.close()
    return list(dict.fromkeys(arrived + listed + watching))


def start_credits():
    """Measure where titles end: the ones somebody is watching first, then long films."""
    import pd_credits
    import pd_gpu

    def file_for(key):
        found = S.local().file_for(str(key), 0) or {}
        path = found.get("file")
        if not path:
            return None
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT duration FROM file WHERE path=?", (path,)).fetchone()
        finally:
            con.close()
        return path, float((row["duration"] if row else 0) or 0)

    def order():
        con = S.local().lib.db()
        try:
            # everything anybody has a place in, however long ago, the latest first
            watching = [str(r["key"]) for r in con.execute(
                """SELECT key FROM progress WHERE COALESCE(casual, 0) = 0
                   AND duration > 600
                   GROUP BY key ORDER BY MAX(updated) DESC""")]
            films = [str(r["item_id"]) for r in con.execute(
                """SELECT item_id FROM file WHERE episode_id IS NULL AND duration > 3600
                   GROUP BY item_id ORDER BY MAX(COALESCE(ctime, mtime)) DESC""")]
            episodes = [str(r["episode_id"]) for r in con.execute(
                """SELECT episode_id FROM file WHERE episode_id IS NOT NULL
                   AND duration > 1200 GROUP BY episode_id""")]
        finally:
            con.close()
        return list(dict.fromkeys(S.first_in_line() + watching + films + episodes))

    def free():
        if not S.auto_on("credits"):
            return False                  # turned off under Settings
        # a stream encoded for somebody away keeps the card busy, which the gate answers
        return not S.worker_waits(card=True)

    def copy():
        try:
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            return bool(one.get("on") and one.get("master"))
        except Exception:
            return False

    # the table first: the watched line reads it inside queries from the first request
    con = S.local().lib.db()
    try:
        pd_credits.ensure(con)
        con.commit()
    finally:
        con.close()
    def tally():
        con = S.local().lib.db()
        try:
            pd_credits.ensure(con)
            ends = {str(r[0]) for r in con.execute("SELECT key FROM ending")}
        finally:
            con.close()
        keys = order()
        done = sum(1 for k in keys if k in ends)
        return done, len(keys) - done

    import pd_beat
    pd_beat.counter("credits", tally)
    pd_credits.start(lambda: S.local().lib, pd_gpu.FFMPEG, file_for, order, free, copy,
                     S.ROOT, stop=S.drive_loaded)
    # a reading that fails goes on Reports, Errors, not only the debug log
    pd_credits.STATE["fault"] = lambda said: S.Handler.note_fault(None, said)


def start_subcheck():
    """Measure subtitles against the film's speech while the machine is idle, and mark the
    one that fits: the titles being watched, then films that arrived in the last month,
    in every language somebody reads."""
    AGAIN = 14 * 86400
    RECENT = 30 * 86400

    def ensure(con):
        con.execute("""CREATE TABLE IF NOT EXISTS subcheck (
                           key TEXT, lang TEXT, at INTEGER, verdict TEXT,
                           PRIMARY KEY (key, lang))""")

    def languages():
        return S.languages_read(S.read_settings() or {})

    def order():
        # the episode a shuffle or a series plays next, then what has just arrived,
        # what is on a watchlist and what is being watched
        return list(dict.fromkeys(list(S.Handler.SUBCHECK_SOON) + S.first_in_line()))

    def asked():
        """The (key, language) pairs somebody asked to have gone through, kept in a
        paper of their own so a restart does not forget them."""
        return S.subcheck_asked()

    def free():
        if not S.auto_on("subcheck"):
            return False                  # turned off under Settings
        return not S.worker_waits(card=True)

    def copy():
        try:
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            return bool(one.get("on") and one.get("master"))
        except Exception:
            return False

    # The rest of the library, after the ones above: every title with a subtitle
    # file beside it, measured and never fetched for. Found by reading the folders,
    # so kept an hour rather than read again every two minutes.
    rest = {"at": 0, "keys": []}

    def library():
        if time.time() - rest["at"] < 3600:
            return rest["keys"]
        import pd_localapi
        con = S.local().lib.db()
        try:
            rows = con.execute(
                "SELECT COALESCE(episode_id, item_id) AS key, path FROM file "
                "WHERE duration > 600").fetchall()
        finally:
            con.close()
        folders = {}
        keys = []
        for r in rows:
            folder = os.path.dirname(r["path"])
            if folder not in folders:
                try:
                    folders[folder] = [n.lower() for n in os.listdir(folder)
                                       if n.lower().endswith((".srt", ".vtt"))]
                except OSError:
                    folders[folder] = []
            stem = os.path.splitext(os.path.basename(r["path"]))[0].lower()[:40]
            if any(n.startswith(stem) for n in folders[folder]):
                keys.append(str(r["key"]))
        rest.update(at=time.time(), keys=keys)
        return keys

    def pending(stop_at_one):
        """What is left to check, first to last: (key, lang, fetch)."""
        con = S.local().lib.db()
        try:
            ensure(con)
            done = {(r["key"], r["lang"]): (int(r["at"] or 0), r["verdict"] or "")
                    for r in con.execute("SELECT key, lang, at, verdict FROM subcheck")}
        finally:
            con.close()
        import pd_subs
        return pd_subs.due(done, order(), library(), languages(),
                           [tuple(p[:2]) for p in asked()], time.time(), AGAIN, stop_at_one)

    def next_one():
        got = pending(True)
        return got[0] if got else None
    def approaching():
        """The next episode of each one on a screen now that still wants a subtitle - fetched
        while the one before plays, whatever background work is waiting for."""
        con = S.local().lib.db()
        try:
            ensure(con)
            nexts = []
            for one in (S.local().playing_now() or {}).values():
                if str(one.get("how") or "") == "syncing" or not one.get("key"):
                    continue
                nxt = S.local().next_episode_key(con, str(one["key"]))
                if nxt and str(nxt) not in nexts:
                    nexts.append(str(nxt))
            done = {(r["key"], r["lang"]): (int(r["at"] or 0), r["verdict"] or "")
                    for r in con.execute("SELECT key, lang, at, verdict FROM subcheck")}
        finally:
            con.close()
        import pd_subs
        return pd_subs.approaching_due(nexts, done, languages(), time.time(), AGAIN)

    def tally():
        con = S.local().lib.db()
        try:
            ensure(con)
            done = con.execute("SELECT COUNT(*) FROM subcheck WHERE verdict IN "
                               "('fits', 'verified', 'inside')").fetchone()[0]
        finally:
            con.close()
        return int(done), len(pending(False))

    import pd_beat

    def work():
        house = S.Handler.__new__(S.Handler)
        house.role = "owner"
        while True:
            # A minute between one and the next, and a quarter of that while there are
            # ones somebody asked for: each check is two minutes of listening by
            # itself, and two more of doing nothing made a series a day's work.
            pd_beat.sleep("subcheck", 15 if asked() else 60)
            try:
                if copy() or not S.auto_on("subcheck"):
                    continue
                # the next episode of what is on a screen goes ahead of the gate; the
                # rest waits for it, as background work does
                soon = approaching()
                if not soon and not free():
                    continue
                pd_beat.beat("subcheck", "choosing", 600)
                one = soon[0] if soon else next_one()
                # the next episode of what is playing is read as the one on screen is:
                # never stopped for a busy drive
                S.URGENT_FILES.clear()
                if soon:
                    nxt = (S.local().file_for(one[0], 0) or {}).get("file") or ""
                    if nxt:
                        S.URGENT_FILES.add(os.path.normcase(nxt))
                if not one:
                    continue
                key, lang, fetch = one
                pd_beat.beat("subcheck", "measuring" if fetch else "measuring the library",
                             3600)
                S.SUBCHECK_BUSY["on"] = True
                try:
                    said = house.check_subtitles(key, lang, fetch=fetch)
                except Exception as e:
                    said = {"verdict": "failed: %s" % str(e)[:80]}
                finally:
                    S.SUBCHECK_BUSY["on"] = False
                if said.get("verdict") == "stopped":
                    continue                # not checked: tried again, nothing written down
                if key in S.Handler.SUBCHECK_SOON:
                    S.Handler.SUBCHECK_SOON.remove(key)
                con = S.local().lib.db()
                try:
                    ensure(con)
                    con.execute("INSERT OR REPLACE INTO subcheck (key, lang, at, verdict) "
                                "VALUES (?,?,?,?)", (key, lang, int(time.time()),
                                                     said.get("verdict") or ""))
                    con.commit()
                finally:
                    con.close()
                # gone through, whatever it was found to be: off the asked-for list,
                # unless the provider refused for the day, which is tried tomorrow
                if str(said.get("verdict") or "") not in ("login", "allowance"):
                    S.subcheck_asked(drop=[(key, lang)])
                try:
                    pd_log.raw("%s subtitles %s %s: %s %s%s"
                            % (time.strftime("%H:%M:%S"), key, lang,
                               said.get("verdict"),
                               json.dumps({k: v for k, v in said.items()
                                           if k != "verdict"}), chr(10)))
                except OSError:
                    pass
            except Exception:
                pass

    pd_beat.counter("subcheck", tally)
    threading.Thread(target=work, name="palladium-subcheck", daemon=True).start()
