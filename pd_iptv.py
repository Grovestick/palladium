"""Live TV from the owner's own IPTV playlist: a plugin.

Everything about it is in this file and in static/iptv.js; delete both and it is gone -
pd_plugins.py imports this only if it is there. The playlist is an M3U address or a
file, read every six hours; its channels play through pd_live's HLS copy, at most as
many at once as the subscription allows. The address holds the subscription's login:
it goes to the owner and nobody else.
"""
import json
import os
import re
import threading
import time
import urllib.request

import pd_live

#: the own playlist read again after this long
OWN_EVERY = 6 * 3600
#: the most read of a playlist, and the most channels kept from it
OWN_BYTES = 60_000_000
OWN_MOST = 20000
#: a channel of the own playlist nobody has asked for in this long gives its
#: connection to the next one started: a player asks every few seconds
FREED_AFTER = 12
#: films and episodes in a provider's playlist: not channels
_NOT_LIVE = re.compile(r"(?i)(/movie/|/series/|\.(mp4|mkv|avi|m4v|mov)(\?|$)|"
                       r"//(www\.|m\.)?(youtube\.com|youtu\.be)/)")
#: the channels played last, put first on the row: the most kept
RECENT_MOST = 12


def _own_kept():
    return os.path.join(pd_live.ROOT["dir"], "live_own.json")


def own_file():
    """Where a playlist sent as a file is kept."""
    return os.path.join(pd_live.ROOT["dir"], "live_own.m3u")


def _settings():
    try:
        with open(os.path.join(pd_live.ROOT["dir"], "settings.json"), encoding="utf-8") as f:
            said = json.load(f)
        return said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        return {}


def own_address():
    """The own playlist's address, "file" for one sent as a file, "" for none."""
    return str(_settings().get("liveList") or "")


def most_at_once():
    """How many channels of the own playlist may play at once; 0 is no limit."""
    try:
        return max(0, int(_settings().get("liveMost") or 0))
    except (TypeError, ValueError):
        return 0


def own_for_all():
    """Whether guests see the own channels too; otherwise the owner alone."""
    return _settings().get("liveWho") == "all"


_OWN_HELD = {"when": ("", 0.0), "map": {}}


def read_own():
    """The own playlist as kept, read again only when the file changes: a provider's
    list runs to megabytes, and a playing channel looks itself up every few seconds."""
    try:
        when = (_own_kept(), os.path.getmtime(_own_kept()))
        if when != _OWN_HELD["when"]:
            with open(_own_kept(), encoding="utf-8") as f:
                _OWN_HELD["map"] = json.load(f) or {}
            _OWN_HELD["when"] = when
        return _OWN_HELD["map"]
    except (OSError, ValueError):
        return {}


def live_only(channels):
    """A provider's playlist without its films and episodes - nor a YouTube page, which
    is no stream - at most OWN_MOST."""
    return [c for c in channels if not _NOT_LIVE.search(c.get("url") or "")][:OWN_MOST]


def read_own_now(address=None):
    """Read the own playlist and keep its channels. How many, or None when it could
    not be read - the channels read last time stand then."""
    address = own_address() if address is None else address
    if not address:
        return None
    text = ""
    try:
        if address == "file":
            with open(own_file(), encoding="utf-8", errors="replace") as f:
                text = f.read(OWN_BYTES)
        else:
            req = urllib.request.Request(address, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read(OWN_BYTES).decode("utf-8", "replace")
    except Exception:
        return None
    found = live_only(pd_live.parse(text))
    if not found:
        return None
    for c in found:
        c["own"] = True
    try:
        with open(_own_kept(), "w", encoding="utf-8") as f:
            json.dump({"read": time.time(), "from": pd_live.key_of(address), "all": found}, f)
    except OSError:
        return None
    return len(found)


_OWN_READING = {"on": False}


def own_channels():
    """The own playlist's channels as last read, with a read behind it when that is old
    or of another address. Never waits on the read."""
    address = own_address()
    if not address:
        return []
    kept = read_own()
    mine = kept.get("from") == pd_live.key_of(address)
    old = time.time() - float(kept.get("read") or 0) > OWN_EVERY
    if (old or not mine) and not _OWN_READING["on"]:
        _OWN_READING["on"] = True

        def again():
            try:
                read_own_now(address)
            finally:
                _OWN_READING["on"] = False
        threading.Thread(target=again, name="palladium-live-own", daemon=True).start()
    return (kept.get("all") or []) if mine else []


#: the kinds of screen with a filter of their own; the rest take the shared one
SCREENS = ("tv", "phone")


def screen_of(app):
    """The kind of screen a client is, from what it calls itself: "android 0.19 tv"."""
    said = str(app or "").lower()
    if said.endswith(" tv") or said in ("tv", "phone"):
        return said.split()[-1]
    return "phone" if "phone" in said else ""


def shown_groups(screen=""):
    """The groups kept, the same on every screen - the ones set in Settings; none is all
    of them. A screen of its own let a phone keep a list without the USA that Settings
    had added. `screen` is taken and not used, for the callers that still pass one."""
    said = _settings().get("liveGroups")
    return [str(g) for g in said] if isinstance(said, list) else []


def _kept_all():
    """The playlist's channels in the groups kept, playing or not."""
    every = own_channels()
    keep = set(shown_groups())
    return [c for c in every if c.get("group") in keep] if keep else every


def kept_channels(screen=""):
    """The playlist's channels in the groups kept, less those found not to play."""
    return [c for c in _kept_all() if not dead(c["key"])]


# ---- which channels play: tried behind the lists

#: a channel that played is tried again after this long; one that did not, after this
CHECK_EVERY = 6 * 3600
RETRY_DEAD = 12 * 3600
#: and the pause between two tries, so a provider is not asked for many at once
CHECK_PAUSE = 2.0
#: seconds of picture a try decodes
CHECK_SECONDS = 2
_HEALTH = {"map": None, "on": False}


def _health_file():
    return os.path.join(pd_live.ROOT["dir"], "live_own_health.json")


def health():
    """key -> {"ok": bool, "at": when}: what each channel did when it was last tried."""
    if _HEALTH["map"] is None:
        try:
            with open(_health_file(), encoding="utf-8") as f:
                said = json.load(f)
            _HEALTH["map"] = said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            _HEALTH["map"] = {}
    return _HEALTH["map"]


def _save_health():
    try:
        with open(_health_file(), "w", encoding="utf-8") as f:
            json.dump(health(), f)
    except OSError:
        pass


def dead(key):
    """Whether a channel was tried and did not play."""
    h = health().get(str(key))
    return bool(h and h.get("ok") is False)


def due(entry, now):
    """Whether a channel is to be tried: never tried, or tried long enough ago."""
    if not entry:
        return True
    wait = CHECK_EVERY if entry.get("ok") else RETRY_DEAD
    return now - float(entry.get("at") or 0) >= wait


def room_to_try():
    """Whether a try may take a connection: always, unless the playlist's connections are
    all in use watching."""
    most = most_at_once()
    if not most:
        return True
    with pd_live.LOCK:
        busy = sum(1 for r in pd_live.RUNNING.values()
                   if r.get("own") and r["proc"].poll() is None)
    return busy < most


def try_round(ffmpeg, now=None, tries=None):
    """Try every channel of the kept groups that is due, one at a time: whether its
    picture plays, and its sharpest stream and frame rate remembered for its start.
    How many were tried."""
    tries = tries or (lambda src: pd_live.plays(src, ffmpeg, seconds=CHECK_SECONDS))
    done = 0
    for c in _kept_all():
        if not due(health().get(c["key"]), time.time() if now is None else now):
            continue
        if not room_to_try():
            break
        src = pd_live.source_of(c["url"])
        if ffmpeg:
            pd_live.probe_kept(src, ffmpeg)
        health()[c["key"]] = {"ok": bool(tries(src)), "at": time.time() if now is None else now}
        done += 1
        if done % 10 == 0:
            _save_health()
        if now is None:
            time.sleep(CHECK_PAUSE)
    if done:
        _save_health()
    return done


def start():
    """The tries, behind everything, while the server runs - and yt-dlp kept current
    for the YouTube channels."""
    if _HEALTH["on"]:
        return
    _HEALTH["on"] = True
    pd_live.keep_ytdlp()

    def loop():
        while True:
            try:
                from pd_gpu import FFMPEG
                if FFMPEG and own_address():
                    try_round(FFMPEG)
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, name="palladium-iptv-check", daemon=True).start()


def _recent():
    return os.path.join(pd_live.ROOT["dir"], "live_own_played.json")


def recent():
    try:
        with open(_recent(), encoding="utf-8") as f:
            said = json.load(f)
        return [str(k) for k in said] if isinstance(said, list) else []
    except (OSError, ValueError):
        return []


def note_played(key):
    """A channel of the playlist played: first on the row from now. A YouTube stream
    puts its channel first: the stream ends, the channel stays."""
    whose = channel_of(key)
    if whose:
        key = whose["key"]
    was = recent()
    if not find(key) or (was and was[0] == key):
        return
    try:
        with open(_recent(), "w", encoding="utf-8") as f:
            json.dump(([key] + [k for k in was if k != key])[:RECENT_MOST], f)
    except OSError:
        pass


#: seconds watched, per channel and day: kept a week and a day, written once a minute
WEEK = 7
_WATCHED = {"map": None, "written": 0.0}


def _watched_file():
    return os.path.join(pd_live.ROOT["dir"], "live_own_watched.json")


def _watched_map():
    if _WATCHED["map"] is None:
        try:
            with open(_watched_file(), encoding="utf-8") as f:
                said = json.load(f)
            _WATCHED["map"] = said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            _WATCHED["map"] = {}
    return _WATCHED["map"]


def note_watched(key, seconds, now=None):
    """A piece of a playlist channel handed to a player: its seconds counted to today.
    A YouTube stream's count to its channel as well: the stream ends, the channel is
    what comes back on the row."""
    if not find(key):
        return
    whose = channel_of(key)
    if whose:
        note_watched(whose["key"], seconds, now)
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    days = _watched_map().setdefault(key, {})
    days[day] = float(days.get(day) or 0) + float(seconds)
    if now - _WATCHED["written"] >= 60:
        _WATCHED["written"] = now
        oldest = time.strftime("%Y-%m-%d", time.localtime(now - (WEEK + 1) * 86400))
        for k in list(_watched_map()):
            kept = {d: s for d, s in _watched_map()[k].items() if d >= oldest}
            if kept:
                _watched_map()[k] = kept
            else:
                del _watched_map()[k]
        try:
            with open(_watched_file(), "w", encoding="utf-8") as f:
                json.dump(_watched_map(), f)
        except OSError:
            pass


def week_seconds(now=None):
    """Seconds each channel was watched in the last seven days."""
    now = time.time() if now is None else now
    since = time.strftime("%Y-%m-%d", time.localtime(now - (WEEK - 1) * 86400))
    return {k: sum(s for d, s in days.items() if d >= since)
            for k, days in _watched_map().items()}


#: YouTube channels, read this often for what is live on them
YOUTUBE_EVERY = 180
#: the group every YouTube card belongs to: one circle under Show more
YOUTUBE = "YouTube"
_YT = {"list": [], "channels": [], "at": 0.0, "on": False}


def youtube_sources():
    """The YouTube channels set under IP-TV, as addresses."""
    said = _settings().get("liveYouTube") or []
    return [str(u).strip() for u in said if str(u).strip()] if isinstance(said, list) else []


def youtube_title(title):
    """A stream's title without the LIVE its channel puts in front of every one."""
    said = re.sub(r"(?i)^\s*live\s*[|I:-]?\s*", "", str(title or "")).strip()
    # "Race I Portimão I GT World Challenge": the I its channel uses for a bar
    # and "OB #3", the onboard camera of car 3, in words
    said = re.sub(r"^OB\s+#", "Onboard #", said)
    return re.sub(r"\s+[I|]\s+", " · ", said) or "Live"


def read_youtube_now():
    """The YouTube channels set, each a card of its own - live or not - and every stream
    live on them now: (channels, streams). Asked of yt-dlp, a few seconds a channel."""
    import subprocess
    exe = pd_live.ytdlp()
    out, channels = [], []
    if not exe:
        return channels, out
    for src in youtube_sources():
        base = re.sub(r"/(streams|live|videos|featured)$", "", src.rstrip("/"))
        page = base + "/streams"
        try:
            raw = subprocess.run([exe, "--no-warnings", "--flat-playlist",
                                  "--playlist-items", "1-30", "-J", page],
                                 capture_output=True, timeout=90, stdin=subprocess.DEVNULL,
                                 env=pd_live.ytdlp_env(),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            said = json.loads(raw or b"{}")
        except Exception:
            continue
        if not isinstance(said, dict):
            continue
        who = str(said.get("channel") or said.get("uploader") or "YouTube")
        who = re.sub(r"(?i)\s*-\s*(live|streams)$", "", who).strip() or "YouTube"
        live = [e for e in said.get("entries") or []
                if e.get("live_status") == "is_live" and e.get("id")]
        # the channel itself: its main live stream when it has one, grey when not
        avatar = next((str(t.get("url")) for t in said.get("thumbnails") or []
                       if "avatar" in str(t.get("id") or "") and t.get("url")), "")
        main = main_stream(live)
        channels.append({"key": pd_live.key_of(base + "/live"), "name": who,
                         "url": ("https://www.youtube.com/watch?v=" + str(main["id"]))
                         if main else base + "/live",
                         "group": YOUTUBE, "youtube": True,
                         "channel": base, "off": not live,
                         "logo": ("https://i.ytimg.com/vi/%s/hqdefault.jpg" % main["id"])
                         if main else avatar})
        for e in live:
            watch = "https://www.youtube.com/watch?v=" + str(e["id"])
            out.append({"key": pd_live.key_of(watch), "name": youtube_title(e.get("title")),
                        "url": watch, "group": YOUTUBE, "youtube": True, "channel": base,
                        "logo": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % e["id"]})
    return channels, out


def main_stream(live):
    """The stream a channel's own card plays when several are live: the one in English
    - a channel running a race in five languages put a German one on its live page -
    and not an onboard camera; else the first."""
    def score(e):
        t = str(e.get("title") or "").lower()
        return (0 if "english" in t else 1, 1 if re.search(r"(?<![a-z])ob(?![a-z])|onboard", t) else 0)
    return min(live, key=score) if live else None


def _youtube_file():
    return os.path.join(os.path.dirname(own_file()), "live_youtube.json")


def youtube_live():
    """The YouTube streams live now, as last read; read again behind when old, never
    waited for. After a restart the last read stands until the first new one is in: a
    player asking for its channel straight away was told there was no such channel."""
    if not _YT["at"] and not _YT["list"] and not _YT["channels"]:
        try:
            with open(_youtube_file(), encoding="utf-8") as f:
                said = json.load(f)
            _YT["channels"] = list(said.get("channels") or [])
            _YT["list"] = list(said.get("list") or [])
        except (OSError, ValueError, AttributeError):
            pass
    if (time.time() - _YT["at"] > YOUTUBE_EVERY and not _YT["on"]
            and youtube_sources()):
        _YT["on"] = True

        def again():
            try:
                channels, streams = read_youtube_now()
                _YT["at"] = time.time()
                # not one channel read - YouTube or the network away, yt-dlp missing:
                # what was known stands, on screen and on disk, until a read answers
                if not channels:
                    return
                _YT["channels"], _YT["list"] = channels, streams
                try:
                    with open(_youtube_file(), "w", encoding="utf-8") as f:
                        json.dump({"channels": _YT["channels"], "list": _YT["list"]}, f)
                except OSError:
                    pass
            finally:
                _YT["on"] = False
        threading.Thread(target=again, name="palladium-youtube", daemon=True).start()
    return list(_YT["list"]) if youtube_sources() else []


def youtube_channels():
    """The YouTube channels set, as cards, live or grey; read with youtube_live()."""
    youtube_live()
    return list(_YT["channels"]) if youtube_sources() else []


def youtube_cards():
    """Everything in the YouTube circle: the channels, then each stream live on them."""
    return youtube_channels() + youtube_live()


def channel_of(key):
    """The YouTube channel card a stream's key belongs to; None for anything else."""
    stream = next((c for c in _YT["list"] if c["key"] == key), None)
    if not stream:
        return None
    return next((c for c in _YT["channels"] if c.get("channel") == stream.get("channel")),
                None)


def tube_card(c, at):
    """A YouTube card: a channel that is not live says so."""
    card = dict(pd_live.card(c, at), genres=[YOUTUBE])
    if c.get("off"):
        card["summary"] = "Not live now"
    return card


def own_row(group="", words="", plain=False, screen=""):
    """The own channels as cards, of one group and with these words in the name when
    asked, in the playlist's order. The row itself puts first what was watched most in
    the last week, the ones played last after them; `plain` keeps the playlist's order,
    for stepping through channels."""
    every = kept_channels(screen)
    want = str(words or "").lower().split()
    out = [c for c in every
           if (not group or c.get("group") == group)
           and all(w in c["name"].lower() for w in want)]
    # the list a player steps through: the YouTube cards too, under their group
    if plain and not want and group in ("", YOUTUBE):
        tube = youtube_cards()
        if group == YOUTUBE:
            return [tube_card(c, len(tube) - n) for n, c in enumerate(tube)]
        both = tube + out
        return [tube_card(c, len(both) - n) if c.get("youtube")
                else dict(pd_live.card(c, len(both) - n),
                          genres=[c["group"]] if c.get("group") else [])
                for n, c in enumerate(both)]
    # The YouTube circle: its channels, live or grey, then each stream live on them
    if group == YOUTUBE:
        tube = [c for c in youtube_cards() if all(w in c["name"].lower() for w in want)]
        return [tube_card(c, len(tube) - n) for n, c in enumerate(tube)]
    folded = []
    if not group and not want and not plain:
        first = {k: n for n, k in enumerate(recent())}
        most = week_seconds()
        # a YouTube channel watched this week stands on the row like any other channel
        out += [c for c in youtube_channels() if most.get(c["key"]) or c["key"] in first]
        # the one played last first, then the one before it; the rest by how long they
        # were watched this week. By the week's total alone, an evening of one channel
        # kept it ahead of whatever was put on after it.
        out.sort(key=lambda c: (first.get(c["key"], len(first)), -most.get(c["key"], 0)))
        # the row: its group circles first, then the channels. YouTube's circle stays
        # under Show more; a channel from it that was watched stands here by itself
        folded = folder_cards(every)
    elif want:
        # searched for by name: the YouTube cards that match as well
        out += [c for c in youtube_cards() if all(w in c["name"].lower() for w in want)]
    return folded + [tube_card(c, len(out) - n) if c.get("youtube")
                     else dict(pd_live.card(c, len(out) - n),
                               genres=[c["group"]] if c.get("group") else [])
                     for n, c in enumerate(out)]


def folders():
    """The groups that stand first on the row as circles of their own, in order."""
    said = _settings().get("liveFolders")
    return [str(g) for g in said] if isinstance(said, list) else []


def group_id(group):
    return "lvg" + __import__("hashlib").sha1(str(group).encode("utf-8")).hexdigest()[:12]


def folder_cards(every):
    """A circle for each group named in folders() that has channels: pressed, that
    group's channels."""
    return group_cards(folders(), every)


def more_cards(every):
    """What Show more opens on: a circle for every group this screen shows, the ones
    first on the row first, the rest in the playlist's order."""
    names = list(folders())
    for c in every:
        g = c.get("group") or ""
        if g and g not in names:
            names.append(g)
    # and the YouTube circle, while there are YouTube channels set
    tube = youtube_cards()
    if tube and YOUTUBE not in names:
        names.append(YOUTUBE)
    return group_cards(names, list(every) + tube)


def group_cards(names, every):
    """A circle for each of these groups that has channels among `every`."""
    counts = {}
    for c in every:
        counts[c.get("group") or ""] = counts.get(c.get("group") or "", 0) + 1
    out = []
    for n, g in enumerate(names):
        if counts.get(g):
            out.append({"ratingKey": group_id(g), "type": "movie", "liveGroup": g,
                        "title": g, "summary": "%d channels" % counts[g],
                        "thumb": "/art/livegroup/" + group_id(g), "duration": 0,
                        "viewCount": 0, "popularity": 10 ** 6 - n})
    return out


def art(path, width):
    """A group circle's picture: its name on a dark ground. None for another path."""
    m = re.match(r"^/art/livegroup/(lvg[0-9a-f]{12})$", path)
    if not m:
        return None
    group = next((g for g in {c.get("group") or "" for c in own_channels()} | set(folders())
                  | ({YOUTUBE} if youtube_sources() else set())
                  if g and group_id(g) == m.group(1)), None)
    if group is None:
        return None
    import io
    from PIL import Image, ImageDraw
    import pd_sports
    W = 400
    im = Image.new("RGB", (W, W), "#23324a")
    d = ImageDraw.Draw(im)
    name = pd_live.clean(group).upper()
    pd_sports._centred(d, W // 2 - 34, name, pd_sports._fit(d, name, 260, 64, wide=True),
                       "#FFFFFF", W)
    if width and width < W:
        im = im.resize((width, width))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


def own_groups(every=False, screen=""):
    """The own playlist's groups, in its order, with how many channels each: the ones
    kept, or with `every` all of them, each saying whether it is kept."""
    counts = {}
    for c in (own_channels() if every else kept_channels(screen)):
        if c.get("group"):
            counts[c["group"]] = counts.get(c["group"], 0) + 1
    tube = youtube_cards()
    if tube:
        counts[YOUTUBE] = len(tube)
    if not every:
        return [{"title": g, "count": n} for g, n in counts.items()]
    keep = set(shown_groups(screen))
    return [{"title": g, "count": n, "kept": g in keep} for g, n in counts.items()]


def admit(key, now=None):
    """Whether this channel may start: one already running is shared, and the own
    playlist's connections are counted - a channel nobody has asked for in a few
    seconds is stopped to make room. The free channels have no limit."""
    c = pd_live.one(key)
    most = most_at_once()
    if not c or not c.get("own") or not most:
        return True
    now = time.time() if now is None else now
    with pd_live.LOCK:
        on = pd_live.RUNNING.get(key)
        if on and on["proc"].poll() is None:
            return True
        busy = [(k, r) for k, r in pd_live.RUNNING.items() if r.get("own") and r["proc"].poll() is None]
        if len(busy) < most:
            return True
        for k, r in sorted(busy, key=lambda kr: kr[1]["seen"]):
            if len(busy) < most:
                break
            if now - r["seen"] > FREED_AFTER:
                try:
                    r["proc"].kill()
                except Exception:
                    pass
                pd_live.RUNNING.pop(k, None)
                busy = [b for b in busy if b[0] != k]
        return len(busy) < most


def find(key):
    """One of the playlist's channels by its key, for pd_live - or one of the YouTube
    channels or streams."""
    return (next((c for c in (read_own().get("all") or []) if c["key"] == key), None)
            or next((c for c in _YT["list"] + _YT["channels"] if c["key"] == key), None))


pd_live.FINDERS.append(find)
pd_live.ADMITS.append(admit)
pd_live.PLAYED.append(note_played)
pd_live.WATCHED.append(note_watched)


# ---- what the server asks of a plugin

#: the row this adds to the front page, and its page: by group, and by name
ROW = {"id": "mylive", "title": "My channels", "path": "/library/live/own",
       "groups": "/library/live/own/groups", "facts": "/library/live/own/facts"}


def stream_facts(key):
    """What a player's info shows of a channel beyond its picture and sound: for a
    YouTube stream its source, the show and how long it has been on, as lines."""
    c = pd_live.one(key)
    if not c or not c.get("youtube"):
        return {"lines": []}
    said = pd_live.YOUTUBE_FACTS.get(c.get("url") or "") or {}
    whose = channel_of(key) or (c if not c.get("channel") or c.get("url", "").endswith("/live")
                                else None)
    channel = said.get("channel") or (whose or {}).get("name") or ""
    lines = ["Source: YouTube" + (" \u00b7 " + channel if channel else "")]
    show = youtube_title(said.get("title")) if said.get("title") else c.get("name") or ""
    if show and show != channel:
        lines.append("Show: " + show)
    on = []
    if said.get("since"):
        on.append("live since " + time.strftime("%H:%M", time.localtime(said["since"])))
    if said.get("watching"):
        on.append("{:,} watching".format(said["watching"]).replace(",", " "))
    if on:
        lines.append(" \u00b7 ".join(on).capitalize())
    return {"lines": lines}


def rows(owner):
    """The rows a viewer is shown: the owner's, and everybody's when shared, while
    there is a playlist."""
    return [ROW] if (own_address() or youtube_sources()) and (owner or own_for_all()) else []


def library(path, q, owner):
    """The row's channels, or its groups; None for a path that is not this plugin's."""
    if path == ROW["facts"]:
        return [stream_facts((q.get("key") or [""])[0])]
    if path not in (ROW["path"], ROW["groups"]):
        return None
    if not (owner or own_for_all()):
        return []
    screen = screen_of((q.get("screen") or [""])[0])
    if path == ROW["groups"]:
        return own_groups((q.get("all") or [""])[0] == "1", screen)
    group, words = (q.get("group") or [""])[0], (q.get("words") or [""])[0]
    # Show more: the groups this screen shows, each opening to its channels
    if (q.get("more") or [""])[0] == "1" and not group and not words:
        return more_cards(kept_channels(screen))
    return own_row(group, words, plain=(q.get("order") or [""])[0] == "list", screen=screen)


def settings_get(owner):
    """The playlist's settings, for the owner alone: the address is their login."""
    if not owner:
        return {}
    address = own_address()
    kept = read_own()
    every = {}
    for c in own_channels():
        if c.get("group"):
            every[c["group"]] = every.get(c["group"], 0) + 1
    return {"liveList": address, "liveMost": most_at_once(),
            "liveWho": "all" if own_for_all() else "me",
            "liveGroups": shown_groups(),
            "liveFolders": folders(),
            "liveAllGroups": [{"title": g, "count": n} for g, n in every.items()],
            "liveDead": sum(1 for c in _kept_all() if dead(c["key"])),
            "liveTried": sum(1 for c in _kept_all() if str(c["key"]) in health()),
            "liveCount": len(kept.get("all") or [])
            if address and kept.get("from") == pd_live.key_of(address) else 0,
            "liveYouTube": youtube_sources(),
            "liveYouTubeNow": [{"title": c["name"], "group": c["group"]} for c in youtube_live()],
            "liveYouTubeReady": bool(pd_live.ytdlp())}


def settings_post(body, owner, stored, write):
    """A change to the playlist's settings: (status, reply), or None when the body is
    not about them. `write` keeps `stored`."""
    if not any(n in body for n in ("liveList", "liveListText", "liveMost", "liveWho",
                                   "liveGroups", "liveFolders", "liveYouTube")):
        return None
    if not owner:
        return 403, {"error": "not allowed"}
    if "liveListText" in body:
        text = str(body.get("liveListText") or "")
        if "#EXTINF" not in text:
            return 400, {"error": "not an M3U playlist"}
        with open(own_file(), "w", encoding="utf-8") as f:
            f.write(text)
        stored["liveList"] = "file"
    elif "liveList" in body:
        said = str(body.get("liveList") or "").strip()
        if said and not re.match(r"(?i)^https?://\S+$", said):
            return 400, {"error": "an http:// or https:// address"}
        stored["liveList"] = said
    if "liveMost" in body:
        try:
            stored["liveMost"] = max(0, min(50, int(body.get("liveMost") or 0)))
        except (TypeError, ValueError):
            stored["liveMost"] = 0
    if "liveWho" in body:
        stored["liveWho"] = "all" if body.get("liveWho") == "all" else "me"
    if "liveYouTube" in body:
        said = body.get("liveYouTube")
        stored["liveYouTube"] = [str(u).strip() for u in said
                                 if re.match(r"(?i)^https?://(www\.)?youtube\.com/\S+$",
                                             str(u).strip())][:12] if isinstance(said, list) else []
        _YT["at"] = 0.0                    # read again on the next look at the row
    if "liveFolders" in body:
        said = body.get("liveFolders")
        stored["liveFolders"] = [str(g) for g in said][:12] if isinstance(said, list) else []
    if "liveGroups" in body:
        said = body.get("liveGroups")
        kept = [str(g) for g in said][:500] if isinstance(said, list) else []
        # a television's or a phone's own, set from its player; the settings page's are
        # everybody else's
        # one list for every screen, whichever screen set it; a screen's own, left by an
        # earlier build, goes
        stored["liveGroups"] = kept
        stored.pop("liveGroupsBy", None)
    write(stored)
    read = None
    if ("liveListText" in body or "liveList" in body) and stored.get("liveList"):
        read = read_own_now()
    return 200, dict(settings_get(True), read=read)
