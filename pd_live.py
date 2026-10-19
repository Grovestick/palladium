"""Live channels: the free Swedish ones iptv-org lists, as a row.

The list is iptv-org's Sweden playlist, read twice a day. The row is the ten best of it
that answer: on around the clock, the sharpest picture first, in the list's own order
otherwise. A channel is played through this server - ffmpeg copies the picture and
makes the sound AAC, in short HLS pieces, so the browser can play what the channel's
own server would not let it reach - and stopped a minute after nobody asks for it.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

SOURCE = "https://iptv-org.github.io/iptv/countries/se.m3u"
#: the lists read: Sweden's, and the world's news and sport
SOURCES = (SOURCE, "https://iptv-org.github.io/iptv/categories/news.m3u",
           "https://iptv-org.github.io/iptv/categories/sports.m3u")
#: the row, in this order, by name: Swedish first, then news, then sport, then the spares
#: that stand in for one that stops working. Each one was played for six seconds before
#: it was put here; the Pluto TV channels, ABC, CBS, DW and Reuters would not play at all.
LINEUP = ("Expressen TV", "Di TV", "Kunskapskanalen HD", "ATG Live",
          "Euronews English HD", "Al Jazeera English", "France 24 English",
          "Red Bull TV UK", "PGA Tour", "FIFA+ United States",
          # spares
          "Golf Channel", "Tennis Channel 2", "Sky News Extra 3", "NBC News NOW",
          "TRT World HD", "World Poker Tour", "Horse & Country TV")
#: the row's form; a row kept in another is picked again
LINEUP_V = 2
#: how many the row holds
MOST = 10
#: the list read again after this long, the ten checked again after this long
READ_EVERY = 12 * 3600
CHECK_EVERY = 3600
#: a channel nobody has asked for in this long is stopped
IDLE = 60

ROOT = {"dir": ""}
LOCK = threading.Lock()
#: id -> {"proc", "dir", "seen"}
RUNNING = {}


def use_data_dir(root):
    ROOT["dir"] = root or ""


def _kept():
    return os.path.join(ROOT["dir"], "live_channels.json")


def key_of(url):
    return "lv" + hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:12]


def parse(text):
    """The channels of an M3U playlist: name, logo, group, stream, and its marks."""
    out, said = [], None
    for line in str(text or "").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([a-z-]+)="([^"]*)"', line))
            name = line.rsplit(",", 1)[-1].strip()
            said = {"name": name, "logo": attrs.get("tvg-logo", ""),
                    "group": attrs.get("group-title", ""), "tvg": attrs.get("tvg-id", "")}
        elif line and not line.startswith("#") and said:
            said["url"] = line
            said["key"] = key_of(line)
            out.append(said)
            said = None
    return out


def lines(name):
    """The picture height a channel's name says, 0 when it does not say."""
    got = re.search(r"\((\d{3,4})p\)", str(name or ""))
    return int(got.group(1)) if got else 0


def clean(name):
    """The name without its marks: "ATG Live (720p)" as "ATG Live", and the circled
    letters some lists put after a name - geo-blocked, on YouTube - left off."""
    return re.sub(r"\s*(\(\d{3,4}p\)|\[[^\]]*\]|[\u24b6-\u24e9])", "", str(name or "")).strip()


def ranked(channels):
    """The ones worth trying, best first: on around the clock and not blocked abroad,
    the sharper picture first, the list's order otherwise."""
    usable = [c for c in channels
              if "not 24/7" not in c["name"].lower() and "geo-blocked" not in c["name"].lower()]
    return sorted(usable, key=lambda c: -lines(c["name"]))


def lineup(every):
    """The channels named in LINEUP, in its order, the first of each name found."""
    by = {}
    for c in every:
        by.setdefault(clean(c["name"]).lower(), c)
    return [by[n.lower()] for n in LINEUP if n.lower() in by]


def plays(url, ffmpeg, seconds=5):
    """Whether a channel really plays: ffmpeg decodes this many seconds of its picture.
    An address that answers can still hand out nothing playable."""
    if not ffmpeg:
        return answers(url)
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-user_agent", "Mozilla/5.0", "-i", url,
                            "-t", str(seconds), "-f", "null", "-"],
                           capture_output=True, text=True, timeout=40,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    frames = [int(x) for x in re.findall(r"frame=\s*(\d+)", p.stderr or "")]
    return bool(frames) and max(frames) >= 10 * seconds


def answers(url, timeout=6):
    """Whether a stream is there: an HLS playlist, or video coming."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            head = r.read(512)
            kind = str(r.headers.get("Content-Type") or "").lower()
        return b"#EXTM3U" in head or "mpegurl" in kind or "video" in kind or "octet" in kind
    except Exception:
        return False


def read_kept():
    try:
        with open(_kept(), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def channels(fresh=False, ffmpeg=""):
    """The row's channels, kept on disk: read and checked again when old."""
    kept = read_kept()
    now = time.time()
    # a row picked before the lineup was named is picked again
    if kept.get("v") != LINEUP_V:
        kept = {"v": LINEUP_V}
    if not fresh and kept.get("top") and now - float(kept.get("checked") or 0) < CHECK_EVERY:
        return kept["top"]
    every = kept.get("all") or []
    if fresh or not every or now - float(kept.get("read") or 0) > READ_EVERY:
        read = []
        for source in SOURCES:
            try:
                req = urllib.request.Request(source, headers={"User-Agent": "Palladium"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    read += parse(r.read().decode("utf-8", "replace"))
            except Exception:
                pass
        if read:
            every = read
            kept["read"] = now            # otherwise the list as last read stands
    top = []
    for c in lineup(every):
        if len(top) >= MOST:
            break
        if plays(c["url"], ffmpeg):
            top.append(c)
    if top:
        kept.update(all=every, top=top, checked=now, v=LINEUP_V)
        try:
            with open(_kept(), "w", encoding="utf-8") as f:
                json.dump(kept, f)
        except OSError:
            pass
    return top or kept.get("top") or []


#: channels from a plugin, beside the free list's: each a function key -> channel or None
FINDERS = []
#: and its say on whether a channel may start now: each a function key -> bool
ADMITS = []


def one(key):
    """A channel by its key: the free list's, or a plugin's."""
    found = next((c for c in (read_kept().get("all") or []) if c["key"] == key), None)
    for find in FINDERS:
        found = found or find(key)
    return found


def room_for(key):
    """Whether a channel may start now: every plugin agrees."""
    return all(admit(key) for admit in ADMITS)


def card(c, at=0):
    """A channel as a shelf card."""
    return {"ratingKey": c["key"], "type": "movie", "live": True, "title": clean(c["name"]),
            "summary": "Live" + (" · " + c["group"] if c.get("group") else ""),
            "thumb": "/art/live/%s" % c["key"], "duration": 0, "viewCount": 0,
            "popularity": at}


# ---- playing one

def sharpest(said):
    """The stream index of the tallest picture in what ffmpeg says of a channel, None
    when it offers one or none: an HLS list hands ffmpeg its smallest first, 448x252
    of a channel that also sends 1080p."""
    found = [(int(h), int(i)) for i, h in
             re.findall(r"Stream #0:(\d+)[^:\n]*: Video: [^\n]*?\b\d{2,5}x(\d{2,5})\b", str(said or ""))]
    if len(found) < 2:
        return None
    return max(found)[1]


def rate_of(said, index=None):
    """The frames a second of one stream in what ffmpeg says - the first picture's when
    no stream is named - 0 when it does not say."""
    for i, line in re.findall(r"Stream #0:(\d+)([^\n]*)", str(said or "")):
        if ": Video:" not in line or (index is not None and int(i) != index):
            continue
        got = re.search(r"([\d.]+) fps", line)
        return float(got.group(1)) if got else 0.0
    return 0.0


def _probe(url, ffmpeg):
    """The sharpest picture's stream index, and its frames a second."""
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-user_agent", "Mozilla/5.0", "-i", url],
                           capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        best = sharpest(p.stderr)
        return best, rate_of(p.stderr, best)
    except Exception:
        return None, 0.0


def rate(key):
    """The frames a second of a running channel, 0 when not known: what a television
    switches its refresh rate to, so 50 frames are not shown on a 60 Hz screen."""
    with LOCK:
        return float((RUNNING.get(key) or {}).get("fps") or 0)


def _dir(key):
    return os.path.join(ROOT["dir"], "cache", "live", key)


def twins(url):
    """The HLS addresses a DASH stream is usually also served at, likeliest first: ffmpeg
    follows a live DASH stream for about fifteen seconds and then asks for pieces the
    server no longer has, so the channel started over every few seconds."""
    url = str(url or "")
    if ".mpd" not in url.split("?", 1)[0].lower():
        return []
    path, _, query = url.partition("?")
    query = query.replace("format=dash", "format=hls")
    out = []
    for name in ("master-fmp4.m3u8", "master.m3u8", "playlist.m3u8", "index.m3u8"):
        out.append(re.sub(r"[^/]*\.mpd$", name, path, flags=re.I) + ("?" + query if query else ""))
    out.append(re.sub(r"\.mpd$", ".m3u8", path, flags=re.I) + ("?" + query if query else ""))
    return list(dict.fromkeys(out))


#: a DASH address -> the HLS one found for it, or itself when there was none
SOURCE_OF = {}
#: a YouTube address -> (its stream's master playlist, when it was asked)
YOUTUBE_OF = {}
#: asked again after this long: YouTube's playlist addresses run for about six hours
YOUTUBE_KEEP = 1800
#: a YouTube address -> what yt-dlp said of the stream: channel, title, since, watching
YOUTUBE_FACTS = {}


def is_youtube(url):
    """Whether a channel's address is a YouTube page rather than a stream."""
    return bool(re.match(r"(?i)^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", str(url or "")))


def ytdlp():
    """yt-dlp: the one kept beside the server's data, else one on the path; "" when
    there is none."""
    import shutil
    here = os.path.join(ROOT["dir"] or "", "tools", "yt-dlp.exe")
    if ROOT["dir"] and os.path.exists(here):
        return here
    return shutil.which("yt-dlp") or ""


def ytdlp_env():
    """yt-dlp's surroundings: this process's, with a PATH of Windows' own folders only.
    It looks along the PATH for a JavaScript program, and a folder link on it - Scoop
    keeps one for every app - stops it outright in the server's context ("untrusted
    mount point")."""
    env = dict(os.environ)
    root = env.get("SystemRoot") or r"C:\Windows"
    env["PATH"] = os.pathsep.join([os.path.join(root, "System32"), root])
    return env


def youtube_stream(url):
    """The master playlist of a live YouTube stream - picture and sound, every size -
    asked of yt-dlp; "" when it is not live or cannot be read."""
    exe = ytdlp()
    if not exe:
        return ""
    try:
        # no input of its own: a program with no window hands its children none
        ran = subprocess.run([exe, "--no-warnings", "-J", url], capture_output=True,
                             stdin=subprocess.DEVNULL, timeout=60, env=ytdlp_env(),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        said = json.loads(ran.stdout or b"{}")
    except Exception as e:
        _say_youtube("yt-dlp did not answer for %s: %s" % (url, e))
        return ""
    if not isinstance(said, dict) or said.get("live_status") != "is_live":
        _say_youtube("yt-dlp gave no live stream for %s: exit %s, %d bytes, %s" % (
            url, ran.returncode, len(ran.stdout or b""),
            (ran.stderr or b"")[-300:].decode("utf-8", "replace").strip()))
        return ""
    YOUTUBE_FACTS[url] = {
        "channel": str(said.get("channel") or said.get("uploader") or ""),
        "title": str(said.get("title") or ""),
        "since": int(said.get("release_timestamp") or said.get("timestamp") or 0),
        "watching": int(said.get("concurrent_view_count") or 0),
        "at": time.time()}
    return next((f["manifest_url"] for f in said.get("formats") or []
                 if f.get("manifest_url")), "")


def _say_youtube(text):
    """A line in the server's log about reading a YouTube stream."""
    try:
        with open(os.path.join(ROOT["dir"] or ".", "debug.log"), "a", encoding="utf-8") as f:
            f.write("%s %s%s" % (time.strftime("%H:%M:%S"), text, chr(10)))
    except OSError:
        pass


def source_of(url):
    """What ffmpeg is given for a channel: its HLS twin when it is DASH and has one, and
    for a YouTube stream the playlist behind the page, asked again every half hour."""
    if is_youtube(url):
        got = YOUTUBE_OF.get(url)
        if not got or not got[0] or time.time() - got[1] > YOUTUBE_KEEP:
            got = (youtube_stream(url), time.time())
            YOUTUBE_OF[url] = got
        return got[0] or url
    if url not in SOURCE_OF:
        SOURCE_OF[url] = next((t for t in twins(url) if answers(t)), url)
    return SOURCE_OF[url]


def next_number(names):
    """The number a channel's pieces carry on from after a restart: one past the last
    piece it made. From nought again, a player took the new pieces for the old ones
    and played the first few seconds over and over."""
    nums = [int(n[3:9]) for n in names if re.match(r"^seg\d{6}\.ts$", n)]
    return max(nums) + 1 if nums else 0


#: a channel's first answer waits for this many pieces - twenty seconds - or this long
#: after its first piece, whichever is first. Answered at the first piece, a player had
#: four seconds in hand and stalled before the burst behind it reached its playlist.
CUSHION_SECONDS = 8.0
CUSHION_WAIT = 2.0
#: the first pieces are cut this short, so the first is there in a second or two; the
#: rest are the usual four
FIRST_PIECES = 1


def cushioned(seconds, waited):
    """Whether a channel's playlist holds enough to hand to a player: twelve seconds of
    picture, or whatever there is three seconds after the first piece. Counted in
    pieces, a channel that only comes in as it is broadcast waited eight seconds for
    five of them."""
    return seconds >= CUSHION_SECONDS or (seconds > 0 and waited >= CUSHION_WAIT)


def seconds_in(playlist):
    """The seconds of picture an HLS playlist holds."""
    return round(sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", str(playlist or ""))), 3)


#: a channel's address -> (sharpest stream, frames a second, when): asked once in a
#: while rather than at every start, which cost one to two seconds each time
PROBED = {}
PROBE_KEEP = 6 * 3600


def probe_kept(src, ffmpeg, now=None):
    """_probe, remembered for a few hours per address."""
    now = time.time() if now is None else now
    hit = PROBED.get(src)
    if hit and now - hit[2] < PROBE_KEEP:
        return hit[0], hit[1]
    best, fps = _probe(src, ffmpeg)
    if best is not None or fps:
        PROBED[src] = (best, fps, now)
    return best, fps


#: pieces the channel's own playlist holds: a minute, so a player half a minute behind
#: the edge is well inside it
LIST_PIECES = 15


def head_start(src):
    """ffmpeg's options to begin an HLS source about half a minute back. From the edge,
    a player had four to eight seconds in hand and then only what came in real time,
    and the first hiccup at the source - about half a minute in - was a stall. From
    further back the first pieces come at once: 38 s of SVT in the first 12."""
    if ".m3u8" not in str(src or "").split("?", 1)[0].lower():
        return []
    return ["-live_start_index", "-10"]


def start(key, ffmpeg):
    """Start copying a channel into HLS pieces, unless it is running. Its folder."""
    c = one(key)
    if not c or not ffmpeg:
        return ""
    with LOCK:
        on = RUNNING.get(key)
        if on and on["proc"].poll() is None:
            on["seen"] = time.time()
            return on["dir"]
    # the address and the sharpest picture asked outside the lock: a few seconds each,
    # and every other channel's pieces wait on the lock
    src = source_of(c["url"])
    best, fps = probe_kept(src, ffmpeg)
    with LOCK:
        on = RUNNING.get(key)
        if on and on["proc"].poll() is None:
            on["seen"] = time.time()
            return on["dir"]
        where = _dir(key)
        os.makedirs(where, exist_ok=True)
        first = next_number(os.listdir(where))
        for f in os.listdir(where):
            try:
                os.remove(os.path.join(where, f))
            except OSError:
                pass
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-user_agent", "Mozilla/5.0",
               *head_start(src),
               "-i", src, "-map", "0:%d" % best if best is not None else "0:v:0?",
               "-map", "0:a:0?", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "160k", "-f", "hls", "-hls_time", "4",
               "-start_number", str(first),
               "-hls_init_time", str(FIRST_PIECES),
               "-hls_list_size", str(LIST_PIECES), "-hls_flags", "delete_segments+omit_endlist",
               "-hls_segment_filename", os.path.join(where, "seg%06d.ts"),
               os.path.join(where, "index.m3u8")]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        RUNNING[key] = {"proc": proc, "dir": where, "seen": time.time(),
                        "own": bool(c.get("own")), "fps": fps}
    _reaper()
    return where


def forget_source(key):
    """A channel that did not start: the address read for it is not tried again - a
    YouTube stream's is read afresh on the next start."""
    c = one(key)
    if c and is_youtube(c.get("url")):
        YOUTUBE_OF.pop(c["url"], None)


#: yt-dlp's own update, asked for this often; and where it comes from when missing
YTDLP_EVERY = 24 * 3600
YTDLP_FROM = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
_YTDLP = {"on": False}


def keep_ytdlp():
    """yt-dlp kept current, once a day while the server runs: YouTube changes and a
    month-old yt-dlp stops reading it. Fetched from its own releases when it is not
    there at all. Each update said in the server's log."""
    if _YTDLP["on"] or not ROOT["dir"]:
        return
    _YTDLP["on"] = True

    def loop():
        time.sleep(120)                 # after the server has settled
        while True:
            try:
                here = os.path.join(ROOT["dir"], "tools", "yt-dlp.exe")
                if not os.path.exists(here):
                    os.makedirs(os.path.dirname(here), exist_ok=True)
                    req = urllib.request.Request(YTDLP_FROM, headers={"User-Agent": "Palladium"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        blob = r.read(200_000_000)
                    # a program, not an error page
                    if blob[:2] == b"MZ" and len(blob) > 1_000_000:
                        with open(here + ".new", "wb") as f:
                            f.write(blob)
                        os.replace(here + ".new", here)
                        _say_youtube("yt-dlp fetched: %d MB" % (len(blob) // 1048576))
                else:
                    ran = subprocess.run([here, "-U"], capture_output=True, timeout=300,
                                         stdin=subprocess.DEVNULL, env=ytdlp_env(),
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    said = (ran.stdout or b"").decode("utf-8", "replace").strip().splitlines()
                    _say_youtube("yt-dlp update: %s" % (said[-1] if said else
                                                        "exit %s" % ran.returncode))
            except Exception as e:
                _say_youtube("yt-dlp update failed: %s" % e)
            time.sleep(YTDLP_EVERY)
    threading.Thread(target=loop, name="palladium-ytdlp", daemon=True).start()


def stop(key):
    """Stop a channel now: one that did not start holds no connection while it retries."""
    with LOCK:
        on = RUNNING.pop(key, None)
    if on:
        try:
            on["proc"].kill()
        except Exception:
            pass


#: told of each channel that played: each a function key -> None
PLAYED = []


def played(key):
    for note in PLAYED:
        try:
            note(key)
        except Exception:
            pass


#: told of each piece of a channel handed to a player: each a function (key, seconds)
WATCHED = []


def watched(key, seconds):
    for note in WATCHED:
        try:
            note(key, seconds)
        except Exception:
            pass


def touch(key):
    with LOCK:
        if key in RUNNING:
            RUNNING[key]["seen"] = time.time()


_REAPING = {"on": False}


def _reaper():
    """Stop each channel a minute after its last request."""
    if _REAPING["on"]:
        return
    _REAPING["on"] = True

    def loop():
        while True:
            time.sleep(15)
            with LOCK:
                for key, on in list(RUNNING.items()):
                    if time.time() - on["seen"] > IDLE or on["proc"].poll() is not None:
                        try:
                            on["proc"].kill()
                        except Exception:
                            pass
                        RUNNING.pop(key, None)
    threading.Thread(target=loop, name="palladium-live", daemon=True).start()


def playlist(text, query):
    """The HLS playlist with this request's own query on each piece, so a player that
    signed in with a token in the address keeps it for the pieces."""
    if not query:
        return text
    return "\n".join((l + "?" + query) if l.strip().endswith(".ts") else l
                     for l in str(text).splitlines()) + "\n"


# ---- its card's picture

def poster(c, logo_bytes=None, width=400):
    """A channel's round card: square, its logo in the middle on a dark ground, inside
    the circle the screens cut it to - the name stands under the card, as a film's
    does. Without a logo, the name itself."""
    import io
    from PIL import Image, ImageDraw
    import pd_sports
    W = H = 400
    im = Image.new("RGB", (W, H), "#161a22")
    d = ImageDraw.Draw(im)
    if c.get("youtube"):
        # A YouTube card: its name, large, in the middle of the circle - a thumbnail
        # told nobody which channel or which stream it was - on the thumbnail filling
        # the card behind it, darkened so the name reads
        if logo_bytes:
            try:
                from PIL import ImageEnhance, ImageFilter, ImageOps
                back = ImageOps.fit(Image.open(io.BytesIO(logo_bytes)).convert("RGB"), (W, H))
                back = back.filter(ImageFilter.GaussianBlur(3))
                im = ImageEnhance.Brightness(back).enhance(0.42)
                d = ImageDraw.Draw(im)
            except Exception:
                pass
        lines = wrapped(d, clean(c["name"]), pd_sports, 300, 4, size=44)
        step = 54
        top = H // 2 - step * len(lines) // 2
        for n, line in enumerate(lines):
            font = pd_sports._fit(d, line, 300, 46, least=32)
            pd_sports._centred(d, top + n * step, line, font, "#FFFFFF", W)
        logo_bytes = b"drawn"
    elif logo_bytes:
        try:
            logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
            # the square inside the circle, less a margin
            logo.thumbnail((240, 200))
            # a black logo on nothing - SVT's - vanished on the dark card: drawn white
            if dark_logo(logo):
                white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
                white.putalpha(logo.getchannel("A"))
                logo = white
            plate = Image.new("RGBA", logo.size, (255, 255, 255, 0))
            plate.alpha_composite(logo)
            im.paste(plate, ((W - logo.width) // 2, (H - logo.height) // 2), plate)
        except Exception:
            logo_bytes = None
    if not logo_bytes:
        name = clean(c["name"]).upper()
        font = pd_sports._fit(d, name, 250, 56, wide=True)
        pd_sports._centred(d, H // 2 - 30, name, font, "#FFFFFF", W)
    if c.get("off"):
        # not live: grey and dimmed, so it reads as asleep at a glance
        from PIL import ImageEnhance, ImageOps
        im = ImageEnhance.Brightness(ImageOps.grayscale(im).convert("RGB")).enhance(0.55)
    if width and width < W:
        im = im.resize((width, width))
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


def dark_logo(logo):
    """Whether a logo is dark marks on a see-through ground: its drawn pixels, on
    average, nearly black."""
    try:
        alpha = logo.getchannel("A")
        grey = logo.convert("L")
        seen = [g for g, a in zip(grey.getdata(), alpha.getdata()) if a > 128]
        if not seen or len(seen) > 0.9 * logo.width * logo.height:
            return False                 # nothing drawn, or a picture with no ground
        return sum(seen) / len(seen) < 70
    except Exception:
        return False


def wrapped(d, text, sports, width, most, size=26):
    """`text` in at most `most` lines that fit `width` at a readable size, the last one
    cut short with an ellipsis when it runs over."""
    words, lines, line = str(text or "").split(), [], ""
    font = sports._font(size, False)
    for w in words:
        trial = (line + " " + w).strip()
        if line and d.textlength(trial, font=font) > width:
            lines.append(line)
            line = w
        else:
            line = trial
    if line:
        lines.append(line)
    if len(lines) > most:
        lines = lines[:most]
        lines[-1] = lines[-1].rstrip(" .,-") + "\u2026"
    return lines or [""]


def logo_of(c):
    """The channel's logo, fetched; None when it cannot be."""
    if not c.get("logo"):
        return None
    try:
        req = urllib.request.Request(c["logo"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(3_000_000)
    except Exception:
        return None


_REFRESHING = {"on": False}


def row():
    """The row's channels as cards, never waiting on a check: what was found last time,
    and a new look behind it when that is old or there is nothing yet."""
    kept = read_kept()
    if kept.get("v") != LINEUP_V:
        kept = {}
    old = time.time() - float(kept.get("checked") or 0) > CHECK_EVERY
    if (old or not kept.get("top")) and not _REFRESHING["on"]:
        _REFRESHING["on"] = True

        def again():
            try:
                try:
                    from pd_gpu import FFMPEG as ffmpeg
                except Exception:
                    ffmpeg = ""
                channels(fresh=False, ffmpeg=ffmpeg)
            finally:
                _REFRESHING["on"] = False
        threading.Thread(target=again, name="palladium-live-check", daemon=True).start()
    top = kept.get("top") or []
    return [card(c, len(top) - n) for n, c in enumerate(top)]
