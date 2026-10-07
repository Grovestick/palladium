#!/usr/bin/env python3
"""The library, answered as JSON.

A nested shape the clients already parse - a container, its items, and the media and
parts inside each - answered here from library.db. Point a client at /local and it
browses; playback is the part that differs, because there is no separate transcoder
behind it. Files either direct play or go through our own ffmpeg engine.

Key space (the client only ever passes these back to us):
    movie    "123"          item.id
    show     "123"          item.id
    season   "123-s2"       item id and season number
    episode  "e456"         episode.id
"""
import json
import os
import re
from pd_library import edition_of, is_episode, is_title

def bare(text):
    """A title as it is searched: lower case, apostrophes gone, other punctuation a
    space - "Don't" is "dont", "Ant-Hill" is "ant hill"."""
    t = str(text or "").lower()
    t = re.sub(r"['\u2019\u2018`\u00b4]", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def bare_in(words, title):
    """Whether words someone typed are in a title, spelt either way: with the spaces,
    or run together - "anthill" finds "Ant-Hill"."""
    want, have = bare(words), bare(title)
    return bool(want) and (want in have or want.replace(" ", "") in have.replace(" ", ""))


def near(spelt, title):
    """How close a title is to the search, every word typed against its best word in the
    title; a word of three letters or fewer has to be right. 0 to 1."""
    import difflib
    words = [w for w in re.split(r"[^a-z0-9]+", bare(spelt)) if w]
    have = [w for w in re.split(r"[^a-z0-9]+", bare(title or "")) if w]
    if not words or not have:
        return 0.0
    got = []
    for w in words:
        if len(w) <= 3 and w not in have:
            return 0.0
        got.append(max(difflib.SequenceMatcher(None, w, h).ratio() for h in have))
    return min(got)


def picture_lines(height, width):
    """How big a picture is in lines, letterboxing accounted for: a 3840x1600 film is
    2160 lines wide-screen, not 1600."""
    return max(int(height or 0), int(width or 0) * 9 // 16)


def resolution_label(height, width):
    """4K, 1080p, 720p or the lines, by the picture's size, not its height alone."""
    n = picture_lines(height, width)
    return ("4K" if n >= 1700 else "1080p" if n >= 900 else "720p" if n >= 650
            else ("%dp" % n if n else ""))


#: not a title of its own: every list of titles leaves the extras out
NOT_EXTRA = "id NOT IN (SELECT item_id FROM extra WHERE parent <> '')"
import threading
import time

# what each client last said it was doing, keyed by device name; see now_playing()
NOW = {}

VIDEO_MIME = {"mkv": "video/x-matroska", "mp4": "video/mp4", "m4v": "video/mp4",
              "avi": "video/x-msvideo", "webm": "video/webm", "mov": "video/quicktime"}
#: A place in a series written the way people write it: s4e9, S04E09, s4 e9, 4x09.
#: It has to sit on its own - bounded by the ends of the words or by punctuation - so a
#: title that happens to contain the letters is not mistaken for a number.
EPISODE_CODE = re.compile(
    r"(?:^|[\s\-_.\[(])"
    r"(?:s\s*(\d{1,2})\s*[\s._x-]?\s*e\s*(\d{1,3})|(\d{1,2})\s*x\s*(\d{1,3}))"
    r"(?=$|[\s\-_.\])])", re.I)


def episode_code(words):
    """Split "regular show s4e9" into ((4, 9), "regular show").

    Returns (None, words) when there is no code in there, so the caller can search for
    words as it always has.
    """
    found = EPISODE_CODE.search(words or "")
    if not found:
        return None, words
    season = int(found.group(1) or found.group(3))
    number = int(found.group(2) or found.group(4))
    rest = words[:found.start()] + " " + words[found.end():]
    return (season, number), " ".join(rest.split()).strip(" -_.[]()")


SECTIONS = [{"key": "1", "type": "movie", "title": "Films"},
            {"key": "2", "type": "show", "title": "TV shows"}]


def container_of(row):
    ext = os.path.splitext(row["path"])[1].lstrip(".").lower()
    return row["container"] or ext


_NAMES = {"when": 0, "map": {}}
_SETTINGS = {"when": 0, "map": {}}


#: Where the papers are.
#:
#: Run from a folder of source this is that folder, and for a long time saying so was
#: enough. Installed, the program sits somewhere read-only and everything it writes
#: goes to this account's application data - so a module that read settings.json from
#: beside its own file read a file that was not there. Everything settled through
#: settings was then dead in a build and alive in the source tree: a subtitle verified
#: by hand, a hidden track, the release a series had settled on. The server says where
#: the papers are, once, at startup.
DATA = os.path.dirname(os.path.abspath(__file__))


def pick_audio(auds):
    """Which soundtrack to play when the viewer has not said.

    English first: a release with a dub in front of it played in a language nobody
    in the house speaks, and the track that was wanted was three presses away. A
    commentary is never it. Failing English, the track the file itself calls
    default, and failing that the first.
    """
    def commentary(a):
        return "comment" in str(a.get("title") or "").lower()

    for n, a in enumerate(auds):
        if str(a.get("lang") or "").lower().startswith("en") and not commentary(a):
            return n
    for n, a in enumerate(auds):
        if a.get("default") and not commentary(a):
            return n
    return 0


def use_data_dir(path):
    """Told by the server, which is the only thing that knows."""
    global DATA
    if path:
        DATA = path
        _SETTINGS["when"] = _NAMES["when"] = None


def _settings():
    """settings.json, re-read only when it changes; asked for on every episode."""
    path = os.path.join(DATA, "settings.json")
    try:
        when = os.path.getmtime(path)
    except OSError:
        return {}
    if when != _SETTINGS["when"]:
        try:
            with open(path, encoding="utf-8") as f:
                _SETTINGS["map"] = json.loads(f.read())
        except Exception:
            _SETTINGS["map"] = {}
        _SETTINGS["when"] = when
    return _SETTINGS["map"]


def subtitles_wanted(show_id):
    """Has this series settled on a subtitle? Cleared when one is watched without."""
    stored = _settings()
    rows = [stored] + [u for u in (stored.get("users") or {}).values() if isinstance(u, dict)]
    return any(str(show_id) in (row.get("subsFor", {}) or {}) for row in rows)


def hidden_tracks(file_id):
    """Streams the owner has put out of sight, by file - the video is untouched."""
    rows = (_settings().get("subsHidden", {}) or {}).get(str(file_id)) or []
    return set(int(n) for n in rows)


def verified_records(key):
    """What is verified for this title, by language: a release name or a stream.

    Written as one record per title once, so a file in that shape is read as a map of
    one rather than thrown away.
    """
    done = (_settings().get("subsOk", {}) or {}).get(str(key)) or {}
    if "release" in done:                       # the older single-record shape
        return {(done.get("language") or "en"): {"release": done.get("release", "")}}
    out = {}
    for language, row in done.items():
        out[language] = row if isinstance(row, dict) else {"release": row}
    return out


def verified_map(key):
    """The same, as language to release name - for the callers that want a name."""
    return {lang: row.get("release", "")
            for lang, row in verified_records(key).items() if row.get("release")}


def subtitle_verified(key, language=None):
    """The release verified for one language, or any of them when none is named."""
    marked = verified_map(key)
    if language:
        return marked.get(language) or marked.get((language or "")[:2], "")
    return next(iter(marked.values()), "")


def episode_named(episode_id):
    """An episode's key from its row. The row holds the whole key now, e and all; an e
    put in front of it again looked up a key that does not exist, and every verified
    subtitle read as unverified."""
    k = str(episode_id or "")
    return k if k.startswith("e") else "e" + k


def subtitle_confirmed(episode_id):
    """The same question, asked the way an episode row asks it."""
    return subtitle_verified(episode_named(episode_id))


_ORDINARY = {"the", "web", "dvd", "bluray", "brrip", "webrip", "hdtv", "srt",
             "eng", "english", "subs", "subtitles", "season", "episode"}


def _release_words(text):
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(w) > 2 and not w.isdigit()} - _ORDINARY


def viewer_settings(who):
    """The part of settings.json belonging to one viewer - the owner keeps the top."""
    stored = _settings()
    if who == "me":
        return stored
    return (stored.get("users") or {}).get(who, {})


def same_release(one, two, aside=""):
    """The same subtitle, named the same way: this is one episode, so names match.

    `aside` is unused now that the comparison is within a single episode; it stays in
    the signature because the caller has it to hand and a looser rule may want it.
    """
    if not one or not two:
        return False
    return one.strip().lower().rstrip(".srt") == two.strip().lower().rstrip(".srt")


def _release_names():
    """What each downloaded subtitle was called where it came from.

    Read from settings.json, and only when that file has actually changed: this is
    asked once per video row, and a library page is hundreds of rows.
    """
    path = os.path.join(DATA, "settings.json")
    try:
        when = os.path.getmtime(path)
    except OSError:
        return {}
    if when != _NAMES["when"]:
        try:
            with open(path, encoding="utf-8") as f:
                said = json.loads(f.read())
            _NAMES["map"] = said.get("subsRelease", {}) or {}
            _NAMES["downloads"] = said.get("subsDownloads", {}) or {}
            _NAMES["newDays"] = said.get("newDays")
        except Exception:
            _NAMES["map"] = {}
            _NAMES["downloads"] = {}
        _NAMES["when"] = when
    return _NAMES["map"]


def new_days():
    """How many days an arrived episode is marked New, the air day the first: as set
    under Settings, six where nothing is."""
    _release_names()
    try:
        days = int(_NAMES.get("newDays") or 0)
    except (TypeError, ValueError):
        days = 0
    return days if 1 <= days <= 30 else 6


def _download_counts():
    """How many had downloaded each fetched subtitle, where it came from."""
    _release_names()
    return _NAMES.get("downloads") or {}


def cut_for(video, name):
    """Whether a subtitle is named for this very release: the same name, give or take a
    language tag on the end."""
    bare = lambda t: re.sub(r"[^a-z0-9]", "", os.path.splitext(t or "")[0].lower())
    ours = bare(os.path.basename(video or ""))
    theirs = re.sub(r"^(english|swedish|danish|norwegian|finnish|german|french|spanish)",
                    "", bare(name))
    if not ours or not theirs:
        return False
    if ours == theirs:
        return True
    longer, shorter = (theirs, ours) if len(theirs) > len(ours) else (ours, theirs)
    return longer.startswith(shorter) and len(longer) - len(shorter) <= 6


_ENDS = {}

def sidecar_end(path):
    """When the last line of a subtitle file is spoken, in seconds.

    Only the tail is read - the last cue is in the last few hundred bytes - and the
    answer is kept against the file's size and time, so a shelf of episodes costs one
    small read each and nothing at all the second time.
    """
    try:
        stat = os.stat(path)
        mark = (stat.st_size, int(stat.st_mtime))
    except OSError:
        return 0
    if _ENDS.get(path, (None,))[0] == mark:
        return _ENDS[path][1]
    last = 0
    try:
        with open(path, "rb") as f:
            f.seek(max(0, stat.st_size - 4096))
            tail = f.read().decode("utf-8", "replace")
        for m in re.finditer(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})", tail):
            h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
            last = max(last, h * 3600 + mi * 60 + sec)
    except OSError:
        return 0
    _ENDS[path] = (mark, last)
    return last


def far_too_long(path, runtime):
    """A subtitle running well past the end of its video was cut for something else - a
    double episode, another edit - and is not used. Poor sync is allowed for: a 25 fps
    subtitle on 23.976 runs 4.3% long, and an offset adds up to a minute."""
    end = sidecar_end(path)
    return bool(runtime and end and end > float(runtime) * 1.08 + 60)


#: What a language code is called, for a subtitle whose file name says nothing else.
LANGUAGE_NAMES = {
    "en": "English", "eng": "English", "sv": "Swedish", "swe": "Swedish",
    "no": "Norwegian", "nor": "Norwegian", "da": "Danish", "dan": "Danish",
    "fi": "Finnish", "fin": "Finnish", "de": "German", "ger": "German",
    "deu": "German", "fr": "French", "fre": "French", "fra": "French",
    "es": "Spanish", "spa": "Spanish", "it": "Italian", "ita": "Italian",
    "nl": "Dutch", "dut": "Dutch", "pl": "Polish", "pol": "Polish",
    "pt": "Portuguese", "por": "Portuguese", "ru": "Russian", "rus": "Russian",
    "ja": "Japanese", "jpn": "Japanese", "ko": "Korean", "kor": "Korean",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese", "ar": "Arabic",
    "ara": "Arabic", "tr": "Turkish", "tur": "Turkish", "cs": "Czech",
    "cze": "Czech", "hu": "Hungarian", "hun": "Hungarian", "el": "Greek",
    "gre": "Greek", "he": "Hebrew", "heb": "Hebrew", "ro": "Romanian",
    "rum": "Romanian", "is": "Icelandic", "ice": "Icelandic", "et": "Estonian",
    "est": "Estonian", "lv": "Latvian", "lav": "Latvian", "lt": "Lithuanian",
    "lit": "Lithuanian",
}


def sidecars(video):
    """Subtitle files sitting beside a video: downloaded, or dropped there by hand.

    Named "Film.en.srt" by convention, so the piece between the last two dots is the
    language when it looks like one.
    """
    out = []
    folder = os.path.dirname(video)
    stem = os.path.splitext(os.path.basename(video))[0].lower()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        low = name.lower()
        if not low.endswith((".srt", ".vtt", ".ass")):
            continue
        if not low.startswith(stem[:40]):
            continue
        middle = os.path.splitext(name)[0].split(".")[-1]
        lang = middle.lower() if 2 <= len(middle) <= 5 and middle.isalpha() else "und"
        full = os.path.join(folder, name)
        # the release it was downloaded as, when we know it; otherwise the file name
        # with the film's own name trimmed off the front, which is all that varies
        label = _release_names().get(full) or name[len(stem):].lstrip(". ") or name
        # The language first, because that is what a list of subtitles is read by,
        # and then whatever else the name has to say. "Film.sv.srt" trimmed down to
        # "sv.srt" - the language twice and the extension - and said nothing at all.
        if not _release_names().get(full):
            # what the file name says beyond the video's own name and its language
            rest = os.path.splitext(name)[0][len(stem):].strip(". ")
            if rest.lower().endswith("." + lang):
                rest = rest[:-(len(lang) + 1)].strip(". ")
            elif rest.lower() == lang:
                rest = ""
            label = rest
        said = LANGUAGE_NAMES.get(lang)
        if said:
            label = (said + " " + label).strip() if label and label != said else said
        elif not label:
            label = name
        out.append({"file": full, "lang": lang, "name": label,
                    "downloads": int(_download_counts().get(full) or 0),
                    # cut for this very release: the one whose timing fits
                    "match": cut_for(video, _release_names().get(full) or name)})
    return out


def best_first(rows):
    """The copies of a film, best picture first - the order a version number means.

    Copies of the same file count once. A download folder, the film folder it was
    moved to and a stray nested copy are three paths to the same twenty-four
    gigabytes, and offering them as three versions asks somebody to choose between
    things that cannot be told apart. Same size and same length is the test: two
    genuinely different rips of one film never agree on both.
    """
    # Size first. Height put a trailer beside the film it came with at the top of the
    # list - a 1080p two-minute extra outranks a 720p feature on picture, and it is
    # the cache that got played. Nothing in a film folder is bigger than the film.
    ordered = sorted(rows, key=lambda r: ((r["size"] or 0), (r["height"] or 0),
                                          (r["bitrate"] or 0)),
                     reverse=True)
    out, seen = [], set()
    for r in ordered:
        mark = (r["size"] or 0, round(float(r["duration"] or 0), 1))
        if mark in seen and mark[0]:
            continue
        seen.add(mark)
        out.append(r)
    return out


#: What a track is for, worked out from its name when the container did not say.
#: "Forced" is the common case; the rest are the words releases actually use.
def _forced(row):
    if row.get("forced"):
        return True
    said = (row.get("title") or "").lower()
    return "forced" in said or "signs" in said


def _sdh(row):
    said = (row.get("title") or "").lower()
    return "sdh" in said or "cc" == said.strip() or "hearing" in said


#: what a version's own cover was found to be, by file path, size and time
VERSION_ART = {}
IMAGES = (".jpg", ".jpeg", ".png", ".webp")


def version_art(path):
    """A cover that came with this copy, or "": a poster, cover or folder picture in
    the film's own folder, or - loose in a shared one - a cover whose name shares a
    word with the file's, the way a fan edit's "..._coverart2_spicediver.jpg" sits
    beside "...[Spicediver].mkv"."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    mark = (path, st.st_size, int(st.st_mtime))
    if mark in VERSION_ART:
        return VERSION_ART[mark]
    found = ""
    folder = os.path.dirname(path)
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    videos = [n for n in names if n.lower().endswith((".mkv", ".mp4", ".avi", ".m4v", ".ts"))]
    pics = [n for n in names if n.lower().endswith(IMAGES)]
    rank = lambda n: next((i for i, w in enumerate(("poster", "cover", "folder", "front"))
                           if w in n.lower()), 9)
    if len(videos) == 1:
        own = sorted((n for n in pics if rank(n) < 9), key=rank)
        found = os.path.join(folder, own[0]) if own else ""
    else:
        words = {w for w in re.split(r"[^a-z0-9]+", os.path.basename(path).lower())
                 if len(w) >= 5 and not w.isdigit()
                 and w not in ("bluray", "1080p", "2160p", "remux", "x264", "x265",
                               "edition", "special", "extended", "theatrical")}
        near = [n for n in pics if rank(n) < 9 and words &
                set(re.split(r"[^a-z0-9]+", n.lower()))]
        near.sort(key=rank)
        found = os.path.join(folder, near[0]) if near else ""
    VERSION_ART[mark] = found
    return found


def media_block(rows, key_prefix="/parts/", aside="", picked="", proved="",
                copy=""):
    """The Media/Part/Stream nesting the clients expect, built from our file rows.

    `copy` is the file a viewer settled on. The list is best picture first, so the
    first entry is what a client plays unless one is marked - which is the whole rule:
    the best copy by default, the chosen one once somebody has chosen.
    """
    out = []
    ordered = best_first(rows)
    # which cut each copy is, said only where the film is held in more than one
    from pd_library import cut_groups
    cuts = cut_groups([(r["path"], r["duration"]) for r in ordered])
    cut_by = {r["id"]: c for r, c in zip(ordered, cuts)}
    many_cuts = len({c[0] for c in cuts}) > 1
    for r in ordered:
        streams = []
        try:
            subs = json.loads(r["streams"] or "[]")
        except Exception:
            subs = []
        streams.append({"streamType": 1, "codec": r["vcodec"], "width": r["width"],
                        "height": r["height"], "frameRate": None})
        # Every soundtrack, not one summary line: a second language, a commentary,
        # a stereo mix beside the 5.1. Numbered as ffmpeg numbers them, because that
        # is what the transcoder will be told to take.
        try:
            auds = json.loads(r["atracks"] or "[]")
        except Exception:
            auds = []
        if auds:
            want = pick_audio(auds)
            for n, a in enumerate(auds):
                streams.append({
                    "streamType": 2, "id": 300000 + (a.get("index") or n),
                    "index": a.get("index"), "codec": a.get("codec"),
                    "channels": a.get("channels"),
                    "languageTag": a.get("lang"), "language": a.get("lang"),
                    "title": a.get("title"),
                    # the one that plays when nobody says otherwise
                    "selected": n == want,
                })
        else:
            streams.append({"streamType": 2, "codec": r["acodec"],
                            "channels": r["channels"], "selected": True})
        out_of_sight = hidden_tracks(r["id"])
        # what is verified for this title, by language: read once, before the tracks
        # that ask about it. An episode knows its own; a film is told by the caller.
        marked = (verified_records(episode_named(r["episode_id"])) if r["episode_id"]
                  else verified_records(proved))
        for s in subs:
            if (s.get("index") or 0) in out_of_sight:
                continue                 # hidden: still in the file, not on offer
            streams.append({"streamType": 3, "id": 100000 + (s.get("index") or 0),
                            "index": s.get("index"), "codec": s.get("codec"),
                            "languageTag": s.get("lang") or "",
                            "language": s.get("lang") or "",
                            "title": s.get("title") or "",
                            # what the track is for, so a client can offer the right
                            # one rather than the first one in the file
                            "forced": _forced(s), "sdh": _sdh(s),
                            # a built-in track is remembered by its stream number
                            "confirmed": any(row.get("index") == s.get("index")
                                             for row in marked.values()),
                            # and chosen by it: "t2" is the second track in the film.
                            # Only a file beside the video used to be rememberable,
                            # so choosing the English track inside one never stuck.
                            "picked": (picked or "").strip().lower()
                                      == "t%s" % (s.get("index") or 0)})
        # files next to the video count as tracks: they need no extraction, and they
        # are text, so they are drawn by the player rather than burned in
        # Verified first, then whichever was chosen last time, then the rest as they
        # lie beside the film: the strongest statement about a subtitle is that
        # somebody watched it against the picture and said it fits.
        # numbered before the overlong are left out: the number is how one is fetched
        beside = [(n, side) for n, side in enumerate(sidecars(r["path"]))
                  if not far_too_long(side["file"], r["duration"])]
        chosen = (picked or "").strip().lower()
        def is_verified(side):
            said = (side["name"] or "").strip().lower()
            return any((row.get("release") or "").strip().lower() == said
                       for row in marked.values())
        # A subtitle whose last line is spoken after the film has ended belongs to
        # something else - a different episode, or a different cut. Two files sat
        # beside one episode of a series, both named for it; the one that ran two and
        # a half minutes past the credits is the one that was offered.
        runtime = float(r["duration"] or 0)
        def overruns(side):
            end = sidecar_end(side["file"])
            return bool(runtime and end and end > runtime + 5)
        # Cut for this release first, then verified, then the one chosen last time,
        # then by how many had downloaded it - and anything that runs past the end
        # of the film at the bottom whatever else it has going for it.
        beside.sort(key=lambda pair: (overruns(pair[1]),
                                      not pair[1].get("match"),
                                      not is_verified(pair[1]),
                                      (pair[1]["name"] or "").strip().lower() != chosen,
                                      -int(pair[1].get("downloads") or 0)))
        for n, side in beside:
            streams.append({"streamType": 3, "id": 200000 + n, "index": -(n + 1),
                            "codec": "srt", "external": True,
                            "downloads": int(side.get("downloads") or 0),
                            "match": bool(side.get("match")),
                            "languageTag": side["lang"], "language": side["lang"],
                            "title": side["name"],
                            # from the release this series was proved on
                            "confirmed": is_verified(side),
                            # its last line lands after this film has finished, so it
                            # was cut for something else
                            "overruns": overruns(side),
                            # or it stops well before the end, which is what a
                            # subtitle still being written looks like
                            "short": bool(runtime and sidecar_end(side["file"])
                                          and sidecar_end(side["file"])
                                          < runtime * 0.8),
                            # and the one this viewer chose the last time
                            "picked": (side["name"] or "").strip().lower() == chosen,
                            "key": "/subs/side?file=%d&n=%d" % (r["id"], n)})
        height = picture_lines(r["height"], r["width"])
        out.append({
            "id": r["id"],
            # the cache this viewer settled on, if it is this one
            "picked": bool(copy) and r["path"] == copy,
            "container": container_of(r),
            "videoCodec": r["vcodec"], "audioCodec": r["acodec"],
            "width": r["width"], "height": r["height"],
            "videoResolution": ("4k" if height >= 1700 else
                                "1080" if height >= 900 else
                                "720" if height >= 650 else str(height or "")),
            "bitrate": r["bitrate"], "audioChannels": r["channels"],
            "duration": int((r["duration"] or 0) * 1000),
            # which cut this file is, where its name says
            "edition": edition_of(r["path"]),
            # the cut, read from the name and the length, and a cover of its own
            "cut": cut_by[r["id"]][1] if many_cuts else "",
            "thumb": ("/art/version/%d" % r["id"]) if version_art(r["path"]) else None,
            "Part": [{"id": r["id"], "key": key_prefix + str(r["id"]),
                      "file": r["path"], "size": r["size"],
                      # how many decibels this file is under or over what everything
                      # else sits at, for a player to make up on its way out, and how
                      # loud it was measured to be
                      "gainDb": gain_of(r["id"], r["path"], r["size"]),
                      "lufs": loudness_of(r["id"]),
                      "container": container_of(r), "Stream": streams}],
        })
    return out


#: Set by the server: how far one file's sound is from everything else's. The library
#: knows nothing about loudness - it is measured beside the files, and the server holds
#: what has been measured.
GAIN_OF = [None]


def gain_of(part, path, size):
    """Decibels for this file, or nought where there is nothing to do about it."""
    if not GAIN_OF[0]:
        return 0.0
    try:
        return GAIN_OF[0](part, path, size)
    except Exception:
        return 0.0


#: Set by the server as well: how loud a file was measured to be, in LUFS.
LOUDNESS_OF = [None]


def loudness_of(part):
    """What this file measured, or nothing where it has not been measured yet."""
    if not LOUDNESS_OF[0]:
        return None
    try:
        return LOUDNESS_OF[0](part)
    except Exception:
        return None


#: The same rule in SQL, for the queries that ask about places rather than files:
#: ninety-five per cent, or three minutes from the end, whichever comes first, and
#: never before eighty-five per cent.
# the measured ending when it comes before the fixed line; the fixed line otherwise -
# a measurement can only bring "watched" forward, never push it later
# Where the story ends when that has been measured; the fixed line only for a file
# nobody has measured
#: a minute before the measured credits still counts: a viewer who stops at the last
#: shot, eight seconds before the titles, has seen the film
END_BUFFER = 60
FINISHED_SQL = ("COALESCE((SELECT n.at - 60 FROM ending n WHERE n.key = progress.key "
                "AND ABS(n.dur - progress.duration) <= 5 "
                "AND n.at >= progress.duration * 0.75 AND n.at < progress.duration), "
                "MAX(duration * 0.85, CASE WHEN duration > 3600 "
                "THEN MIN(duration * 0.90, duration - 600) "
                "ELSE MIN(duration * 0.95, duration - 180) END))")


class LocalAPI:
    """The library, as one shared object serving every request at once.

    Everything about *who is asking* is held per thread, not on the object. There is
    one of these for the whole server, and requests arrive in parallel: with the
    viewer written on the object, a guest's report and the owner's page a millisecond
    apart would each read whatever the other had just written. That is how a guest's
    film came to sit in the owner's Continue watching, with a progress row under "me"
    for a film only the guest had played.
    """

    #: the per-request corner: who is asking, and the few things that belong to them
    _asking = threading.local()

    def _mine(self, name, unset):
        return getattr(self._asking, name, unset)

    # Whose progress this request is about, set by the server before it hands anything
    # over: "me" for the owner, the invitation token for a guest.
    @property
    def who(self):
        return self._mine("who", "me")

    @who.setter
    def who(self, value):
        self._asking.who = value

    # What this viewer has put aside from Continue watching, and when: a programme
    # stays off the shelf until it is played again. Set by the server, like `who`,
    # because it lives in that person's settings rather than in the library.
    

    # The shelves this viewer is shuffling, and what those shelves are called. Set by
    # the server the same way, and read the same way - a plain attribute on the api
    # object is written to one thing and read from another, which is why Continue
    # watching had no row for a shelf that plainly had a place in it.
    @property
    def shuffles(self):
        return self._mine("shuffles", {})

    @shuffles.setter
    def shuffles(self, value):
        self._asking.shuffles = value

    @property
    def shelf_names(self):
        return self._mine("shelf_names", {})

    @shelf_names.setter
    def shelf_names(self, value):
        self._asking.shelf_names = value

    #: Set by the server before it hands anything over: where a shuffled playing on
    #: one shelf has got to. The library's own record of what has been watched is left
    #: alone, because putting something on is not watching it.
    @property
    def shelf_note(self):
        return self._mine("shelf_note", None)

    @shelf_note.setter
    def shelf_note(self, value):
        self._asking.shelf_note = value

    #: Set by the server: drop one title's place from every shelf keeping it. What
    #: a shelf keeps is where a playing got to, and only a playing off that shelf says
    #: which shelf it was - so finishing one any other way left the place standing.
    @property
    def shelf_forget(self):
        return self._mine("shelf_forget", None)

    @shelf_forget.setter
    def shelf_forget(self, value):
        self._asking.shelf_forget = value

    #: Set by the server: somebody has passed the middle of an episode. What is done
    #: about it is the server's business - it is the only side that knows about packs -
    #: and this is the only place that knows where a playing has got to.
    @property
    def half_way(self):
        return self._mine("half_way", None)

    @half_way.setter
    def half_way(self, value):
        self._asking.half_way = value

    #: what the client asking calls itself, set per request by the server
    @property
    def app_now(self):
        return self._mine("app_now", "")

    @app_now.setter
    def app_now(self, value):
        self._asking.app_now = value

    def __init__(self, library):
        self.lib = library

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _whole(rows):
        """The files qBittorrent has finished. One still downloading, or switched off part
        way, has its full size on disk and is no version of anything: it plays to where the
        pieces stop. Kept only when there is nothing else, so a title never loses its file."""
        try:
            import pd_torrents
            # a page is drawn with what is known; the copy waits for the first answer
            done = [r for r in rows if not pd_torrents.unfinished(r["path"], wait=False)]
            return done or rows
        except Exception:
            return rows

    def _files(self, con, item_id=None, episode_id=None):
        if episode_id is not None:
            return self._whole(con.execute("SELECT * FROM file WHERE episode_id=?",
                                           (episode_id,)).fetchall())
        return self._whole(con.execute("SELECT * FROM file WHERE item_id=? AND episode_id IS NULL",
                                       (item_id,)).fetchall())

    # 4K in a filename is a claim; the frame is the fact. But height alone is not
    # the frame: a scope film off a 4K disc is 3840 by 1600, which is every bit a 4K
    # copy and reads as less than 1080p by height. So the measure is what the frame
    # would be if it were 16:9 - width times nine sixteenths, or the height itself,
    # whichever is larger - and the threshold stays where the clients have it.
    UHD = 1700

    @staticmethod
    def frame_lines(row):
        """One number for how big a picture is, letterboxing accounted for."""
        try:
            height, width = row["height"] or 0, row["width"] or 0
        except (IndexError, KeyError):
            return 0
        return max(height, width * 9 // 16)

    def genre_named(self, con, word):
        """The category this word names, spelt as the library spells it.

        Written how somebody types it - "sci fi" for Science Fiction, "docs" for
        Documentary - as well as in full. Anything shorter than three letters is a
        word on the way to being typed, not a category.
        """
        want = re.sub(r"[^a-z0-9]", "", (word or "").lower())
        if len(want) < 3:
            return ""
        seen = set()
        for r in con.execute("SELECT genres FROM item WHERE genres <> ''"):
            for g in (r["genres"] or "").split(","):
                g = g.strip()
                if g:
                    seen.add(g)
        for g in sorted(seen):
            flat = re.sub(r"[^a-z0-9]", "", g.lower())
            if flat == want or (len(want) >= 4 and flat.startswith(want)):
                return g
        return ""

    def _tall(self, con):
        """How many lines the tallest file of each title has, by item id.

        Two queries for the whole library rather than one per row: a shelf of four
        hundred posters each asking after its own files was the slowest thing on the
        page. Held for a few seconds, which is the life of one listing.
        """
        if getattr(self, "_tall_at", 0) > time.time() - 20 and getattr(self, "_tall_map", None):
            return self._tall_map
        out = {}
        for r in con.execute("""SELECT item_id, MAX(height) height, MAX(width) width
                                FROM file
                                WHERE episode_id IS NULL AND item_id IS NOT NULL
                                GROUP BY item_id"""):
            out[r["item_id"]] = self.frame_lines(r)
        # a series is as good as its best episode: a season upgraded to 4K makes the
        # programme worth finding under 4K, and nothing else says so on a poster
        for r in con.execute("""SELECT e.item_id, MAX(f.height) height,
                                       MAX(f.width) width
                                FROM file f JOIN episode e ON e.id = f.episode_id
                                GROUP BY e.item_id"""):
            out[r["item_id"]] = max(out.get(r["item_id"], 0), self.frame_lines(r))
        self._tall_map, self._tall_at = out, time.time()
        return out

    WATCHED = 0.95            # finished, once this much of it has gone by
    #: or with this long left, whichever comes first. Ninety-five per cent of a
    #: twenty-two minute episode is a minute of credits still to go, so an episode
    #: somebody watched to the end sat in Continue watching for ever; three minutes
    #: from the end of a film is still the film, which is why it is the earlier of the
    #: two that counts.
    WATCHED_TAIL = 180.0
    #: over an hour: 90% or ten minutes left, whichever comes first
    LONG = 3600.0
    WATCHED_LONG = 0.90
    WATCHED_TAIL_LONG = 600.0
    #: and never before this, for something too short for either to mean anything
    WATCHED_FLOOR = 0.85
    #: and how much of it has to have been played to say so. Skipping to the end of an
    #: episode puts the resume point past the mark without a minute of it having been
    #: watched, and pressing next or previous from near the end did the same.
    SAT_THROUGH = 0.6

    @staticmethod
    def finished_at(duration, key=None):
        """The second a file counts as having been watched through: where its story
        ends when that has been measured, and the fixed line only when it has not."""
        whole = float(duration or 0)
        if whole <= 0:
            return 0.0
        if whole > LocalAPI.LONG:
            line = min(whole * LocalAPI.WATCHED_LONG, whole - LocalAPI.WATCHED_TAIL_LONG)
        else:
            line = min(whole * LocalAPI.WATCHED, whole - LocalAPI.WATCHED_TAIL)
        line = max(whole * LocalAPI.WATCHED_FLOOR, line)
        if key:
            try:
                import pd_credits
                end = pd_credits.end_for(key, whole)
            except Exception:
                end = None
            if end:
                return max(0.0, end - END_BUFFER)
        return line

    @staticmethod
    def watched_through(position, duration, key=None):
        """Whether a place is far enough in to call the thing finished."""
        return (bool(duration)
                and float(position or 0) >= LocalAPI.finished_at(duration, key))

    #: what the catalogue says about a programme's next episode, kept a few hours
    _airs = {}
    #: a film not held yet, as the catalogue describes it: runtime, tagline, director
    _film_facts = {}
    #: and about one season of it, episode by episode
    _season_airs = {}

    @staticmethod
    def _air_words(day):
        """When an episode airs, as a card says it: a date to come, or that it is out."""
        import datetime
        try:
            when = datetime.date.fromisoformat(str(day or "")[:10])
        except ValueError:
            return "Date not known"
        if when > datetime.date.today():
            return "Airs %d %s" % (when.day, when.strftime("%b"))
        # the catalogue's date is the American one: released in the evening there,
        # it reaches the tracker in the night here
        if when == datetime.date.today():
            return "Airs today"
        return "Out, not here yet"

    def _catalogue(self, path, kept, ident):
        """One answer from the catalogue, kept six hours."""
        got = kept.get(ident)
        if not got or time.time() - got[0] > 6 * 3600:
            try:
                said = self.lib.tmdb(path) or {}
            except Exception:
                said = {}
            # a failed ask is not an answer: kept, it hid every upcoming card for six
            # hours after the machine had been offline
            if not said:
                return got[1] if got else {}
            got = (time.time(), said)
            kept[ident] = got
        return got[1]

    #: a programme's season posters, asked of the catalogue again after this long
    SEASON_ART_AGE = 7 * 86400

    def _season_poster(self, con, key, number):
        """The catalogue's poster for one season, or None: the programme's is the fallback.

        Kept in the library so a restart does not ask TMDB once per programme again.
        """
        con.execute("CREATE TABLE IF NOT EXISTS season_art (item_id TEXT, season INTEGER, "
                    "poster TEXT, at INTEGER, PRIMARY KEY (item_id, season))")
        row = con.execute("SELECT poster, at FROM season_art WHERE item_id=? AND season=?",
                          (str(key), int(number))).fetchone()
        if row and time.time() - int(row["at"] or 0) < self.SEASON_ART_AGE:
            return row["poster"]
        show = con.execute("SELECT tmdb_id, poster FROM item WHERE id=?", (str(key),)).fetchone()
        if not show or not show["tmdb_id"]:
            return row["poster"] if row else None
        said = self._catalogue("/tv/%d" % int(show["tmdb_id"]), self._airs, show["tmdb_id"])
        seasons = said.get("seasons") or []
        if not seasons:
            return row["poster"] if row else None      # no answer: keep what was known
        now = int(time.time())
        for one in seasons:
            # a season whose poster is the programme's is no poster of its own
            art = one.get("poster_path")
            if art and art == said.get("poster_path"):
                art = None
            con.execute("INSERT OR REPLACE INTO season_art (item_id, season, poster, at) "
                        "VALUES (?,?,?,?)", (str(key), int(one.get("season_number") or 0),
                                             art, now))
        # a season the catalogue does not list: nothing of its own, asked again later
        con.execute("INSERT OR IGNORE INTO season_art (item_id, season, poster, at) "
                    "VALUES (?,?,NULL,?)", (str(key), int(number), now))
        con.commit()
        got = con.execute("SELECT poster FROM season_art WHERE item_id=? AND season=?",
                          (str(key), int(number))).fetchone()
        return got["poster"] if got else None

    @staticmethod
    def _season_thumb(key, number, show_poster):
        """Where a season's picture is fetched: its own, or the programme's in its place."""
        return "/art/%s/season/%d" % (key, int(number)) if show_poster else None

    def _upcoming(self, con, days=60):
        """The next episode of every programme this viewer is up to date with.

        Up to date means the newest episode this house holds of it is watched. The
        catalogue then says what comes after: one already out that is not here yet,
        or the date the next one airs. Drawn greyed on Continue watching and at the
        front of Recently added, so a series somebody is waiting on stays in sight
        rather than dropping off the shelf the moment its last episode ends.
        """
        now = time.time()
        rows = con.execute(
            """SELECT e.item_id AS item_id, MAX(p.updated) AS at
                 FROM progress p JOIN episode e ON e.id = p.key
                WHERE p.who = ? AND p.updated > ? AND COALESCE(p.casual, 0) = 0
             GROUP BY e.item_id""", (self.who, int(now - days * 86400))).fetchall()
        out = []
        for r in rows:
            item = con.execute("SELECT * FROM item WHERE id=? AND type='show'",
                               (r["item_id"],)).fetchone()
            if not item or not item["tmdb_id"]:
                continue
            last = con.execute(
                """SELECT e.id, e.season, e.number FROM episode e
                     JOIN file f ON f.episode_id = e.id
                    WHERE e.item_id = ? AND e.season > 0
                 ORDER BY e.season DESC, e.number DESC LIMIT 1""", (item["id"],)).fetchone()
            if not last or not self._watched(con, last["id"]):
                continue                      # still something here to watch
            said = self._catalogue("/tv/%d" % int(item["tmdb_id"]), self._airs,
                                   item["tmdb_id"])
            held = (int(last["season"]), int(last["number"]))
            aired = said.get("last_episode_to_air") or {}
            coming = said.get("next_episode_to_air") or {}
            spot = lambda e: (int(e.get("season_number") or 0), int(e.get("episode_number") or 0))
            if aired and spot(aired) > held:
                one, word, far = aired, "Out, not here yet", False
            elif coming and spot(coming) > held:
                one = coming
                day = str(coming.get("air_date") or "")
                try:
                    airs = time.mktime(time.strptime(day, "%Y-%m-%d"))
                    word = "Airs " + time.strftime("%d %b", time.localtime(airs)).lstrip("0")
                except ValueError:
                    airs, word = None, "Coming"
                # the card shows from six days before the air date; undated or further off, not yet
                far = airs is None or airs - now > 6 * 86400
            else:
                continue
            show = self._show(con, item)
            season, number = spot(one)
            if word.startswith("Out") and str(one.get("air_date") or "") == \
                    time.strftime("%Y-%m-%d"):
                word = "Airs today"
            # on the tracker already, before its date or after it: pressable
            try:
                import pd_tracker
                many = len(pd_tracker.episodes_of(item["title"], season).get(number) or [])
            except Exception:
                many = 0
            if many:
                word = ("On the tracker - press to download" if word.startswith("Out")
                        else "Out early - on the tracker")
            elif far:
                continue
            out.append({
                "ratingKey": "upnext-%s-s%02de%02d" % (item["id"], season, number),
                "type": "episode", "upcoming": True, "airs": word,
                # its air date is today: the card wears TODAY rather than Upcoming
                "airsToday": str(one.get("air_date") or "") == time.strftime("%Y-%m-%d"),
                "fetchable": many > 0,
                "airDate": str(one.get("air_date") or ""),
                "originallyAvailableAt": str(one.get("air_date") or ""),
                "title": one.get("name") or "Episode %d" % number,
                "summary": one.get("overview") or "",
                "grandparentTitle": show.get("title"),
                "grandparentRatingKey": item["id"], "grandparentKey": item["id"],
                "parentIndex": season, "index": number,
                "parentRatingKey": "%s-s%d" % (item["id"], season),
                # its season's poster, as the episodes beside it on the shelf wear
                "thumb": self._season_thumb(item["id"], season, show.get("thumb")),
                "parentThumb": self._season_thumb(item["id"], season, show.get("thumb")),
                "art": show.get("art"),
                "grandparentThumb": show.get("thumb"),
                # out and on the tracker: first on Continue watching, where the press
                # that downloads it is
                "lastViewedAt": int(now) if many else int(r["at"] or 0),
                "addedAt": int(now),
            })
        return out

    #: whether this machine follows a main server, asked at most once a minute
    _copy_said = [0.0, False]

    def _is_copy(self):
        if time.time() - LocalAPI._copy_said[0] > 60:
            try:
                import pd_follow
                one = pd_follow.settings(self.lib.config())
                LocalAPI._copy_said[1] = bool(one.get("on") and one.get("master"))
            except Exception:
                LocalAPI._copy_said[1] = False
            LocalAPI._copy_said[0] = time.time()
        return LocalAPI._copy_said[1]

    def _watched_all(self, con):
        """The same answer as _watched, for every title this viewer has a place in.

        Two reads for the lot. Continue watching asked it once per episode while
        walking each series forward - eleven thousand questions for one row.
        """
        rows = con.execute(
            "SELECT key, MAX(COALESCE(position, 0), COALESCE(furthest, 0)) position, "
            "duration, COALESCE(marked, 0) marked FROM progress "
            "WHERE who=? AND COALESCE(casual, 0) = 0", (self.who,)).fetchall()
        spans = {}
        copy = self._is_copy()
        if not copy:
            for r in con.execute("SELECT key, started, updated FROM watchlog WHERE who=? "
                                 "AND COALESCE(casual, 0) = 0 ORDER BY started",
                                 (self.who,)):
                spans.setdefault(str(r["key"]), []).append(
                    (int(r["started"] or 0), int(r["updated"] or 0)))
        out = {}
        for row in rows:
            key = str(row["key"])
            if row["marked"]:
                out[key] = True
                continue
            if not (row["duration"] and
                    self.watched_through(row["position"], row["duration"], key)):
                out[key] = False
                continue
            mine = spans.get(key)
            if copy or not mine:
                out[key] = True
                continue
            sat, end = 0, 0
            for a, b in mine:
                if b <= end:
                    continue
                sat += b - max(a, end)
                end = b
            out[key] = min(sat, row["duration"]) >= row["duration"] * self.SAT_THROUGH
        return out

    def _watched(self, con, key):
        """Has this viewer finished it?

        Two questions, not one: how far in the resume point is, and how long they
        actually sat there. The first alone marked an episode watched for anybody who
        skipped to the end of it - which is how a series moved itself on past an
        episode nobody saw.

        The second is asked only where there is a record to ask: viewings written
        before the log existed have none, and those are taken at their resume point as
        they always were.
        """
        row = con.execute(
            "SELECT position, duration, COALESCE(marked, 0) marked, "
            "COALESCE(furthest, 0) furthest FROM progress "
            "WHERE key=? AND who=? AND COALESCE(casual, 0) = 0",
            (str(key), self.who)).fetchone()
        if row and row["marked"]:
            return True                    # said by hand, which settles it
        # the furthest they got, not only where the player was left: the last place
        # saved can be a couple of minutes before the end they actually reached
        if not (row and row["duration"] and
                self.watched_through(max(float(row["position"] or 0),
                                         float(row["furthest"] or 0)),
                                     row["duration"], key)):
            return False
        # A copy's watch log holds only what played on it: an episode watched on the
        # main server had four minutes here, and so counted as unwatched and dropped
        # its upcoming card. Its places come from the main server, which already asked.
        if self._is_copy():
            return True
        try:
            spans = con.execute(
                "SELECT started, updated FROM watchlog WHERE who=? AND key=? "
                "AND COALESCE(casual, 0) = 0 ORDER BY started",
                (self.who, str(key))).fetchall()
        except Exception:
            return True
        if not spans:
            return True                    # nothing logged: the old answer stands
        # Both machines keep both logs, and a film read off the two at once was logged
        # by each: overlapping rows are one sitting, counted once.
        sat, end = 0, 0
        for r in spans:
            a, b = int(r["started"] or 0), int(r["updated"] or 0)
            if b <= end:
                continue
            sat += b - max(a, end)
            end = b
        return min(sat, row["duration"]) >= row["duration"] * self.SAT_THROUGH

    def picked_copy(self, key):
        """Which file of this title the viewer chose, by path; nothing means the best."""
        return (viewer_settings(self.who).get("copyPick") or {}).get(str(key), "")

    def picked_subtitle(self, key):
        """Which subtitle this viewer chose for this title, last time they watched."""
        return (viewer_settings(self.who).get("subsPick") or {}).get(str(key), "")

    def is_watched(self, key):
        """Finished, for the viewer this request belongs to."""
        con = self.lib.db()
        try:
            return self._watched(con, key)
        finally:
            con.close()

    # Credits, and how long people sit through them: a few minutes on an episode, ten
    # on a film. Long enough to be nowhere near the last second, short enough that
    # stopping halfway is still stopping halfway.
    SUB_TAIL_SHORT = 3 * 60
    SUB_TAIL_FILM = 10 * 60
    SUB_LONG = 45 * 60           # anything longer than this is treated as a film
    SUB_FLOOR = 0.7              # and most of it has to have gone by regardless

    def seen_through(self, key):
        """Watched far enough to judge the subtitle it was watched with.

        Deliberately more forgiving than the watched mark: a subtitle has done its work
        by the time the credits roll, and that is where people stop.
        """
        con = self.lib.db()
        try:
            row = con.execute(
                "SELECT position, duration FROM progress WHERE key=? AND who=?",
                (str(key), self.who)).fetchone()
        finally:
            con.close()
        if not row or not row["duration"]:
            return False
        position, duration = row["position"] or 0, row["duration"]
        tail = self.SUB_TAIL_FILM if duration > self.SUB_LONG else self.SUB_TAIL_SHORT
        return (position / duration >= self.SUB_FLOOR
                and duration - position <= tail)

    def _episode_keys(self, con, key):
        """Every episode under a key: an episode's own, a season's, or a series'."""
        key = str(key)
        if key.startswith("e"):
            return [key]
        m = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
        if m:
            rows = con.execute("SELECT id FROM episode WHERE item_id=? AND season=?",
                               (m.group(1), int(m.group(2)))).fetchall()
            return [str(r["id"]) for r in rows]
        if is_title(key):
            rows = con.execute("SELECT id FROM episode WHERE item_id=?",
                               (str(key),)).fetchall()
            return [str(r["id"]) for r in rows] or [key]      # a film is itself
        return [key]

    def _set_watched(self, con, key, watched):
        """Mark, or unmark, everything under this key - for this viewer only.

        A mark made by hand says what a viewing cannot: that this one is done. It is
        written as a mark rather than as a resume point at the end of the film, so
        the row does not read as "you are two seconds from the credits" - marking an
        episode watched takes it off Continue watching and leaves no place to resume.
        """
        for one in self._episode_keys(con, key):
            if not watched:
                # Not watched: the place goes back to nought, the row stays.
                #
                # It used to be deleted, which threw away that this was ever played
                # at all. The row is the record; only the place in it is being taken
                # back. Nought is under the half minute the shelf ignores, so it
                # leaves Continue watching without leaving the log.
                con.execute("""UPDATE progress SET position=0, marked=0, furthest=0,
                                                  updated=?
                               WHERE key=? AND who=?""",
                            (int(time.time()), one, self.who))
                # and written down, because the other machine still has its copy.
                # Deleting a place here and saying nothing meant the copy posted it
                # back within five minutes and the film returned to the shelf, which
                # to anybody watching is the press not having worked. A later
                # viewing beats this, because its own note is newer.
                con.execute("""INSERT INTO forgot (who, key, at) VALUES (?,?,?)
                               ON CONFLICT(who, key) DO UPDATE SET at=excluded.at""",
                            (self.who, one, int(time.time())))
                continue
            dur = self._duration_of(con, one)
            con.execute("""INSERT INTO progress (key, position, duration, updated, who,
                                                 marked)
                           VALUES (?,?,?,?,?,1)
                           ON CONFLICT(who, key) DO UPDATE SET
                           position=excluded.position, duration=excluded.duration,
                           updated=excluded.updated, marked=1""",
                        (one, 0.0, dur, int(time.time()), self.who))
        con.commit()

    @staticmethod
    def _duration_of(con, key):
        """Seconds, from the file - a mark has to land above the watched line."""
        key = str(key)
        try:
            if key.startswith("e"):
                row = con.execute("SELECT duration FROM file WHERE episode_id=?",
                                  (key,)).fetchone()
            else:
                row = con.execute("SELECT duration FROM file WHERE item_id=? "
                                  "AND episode_id IS NULL", (str(key),)).fetchone()
        except (TypeError, ValueError):
            return 1.0
        return float(row["duration"]) if row and row["duration"] else 1.0

    def _progress(self, con, key):
        # a shuffle's places are its own; an ordinary card resumes only from ordinary play
        row = con.execute("SELECT position, duration FROM progress WHERE key=? AND who=? "
                          "AND COALESCE(casual, 0) = 0", (key, self.who)).fetchone()
        return row

    #: when films actually came out, by library key, read from the new arrivals list
    #: and kept for a few minutes: it is a few dozen entries and a file read each way
    _dates = {}
    _dates_at = 0.0

    #: how many people have asked for each title, re-read once a minute
    _asks = {}
    _asks_at = 0.0

    def _did_i_ask(self, key):
        """Whether this viewer is one of the people who asked for it.

        By the same mark the request was written with - a hash of who they are, not
        of the name they show, so two guests called the same thing are two people.
        """
        import hashlib
        said = self._asked_by_how_many().get(str(key or ""))
        if not said:
            return False
        mark = hashlib.sha1(str(self.who).encode("utf-8")).hexdigest()[:12]
        return mark in (said[1] or ())

    def _asked_by_how_many(self):
        """The count of people waiting on each title nobody here holds."""
        now = time.time()
        if now - LocalAPI._asks_at > 60:
            try:
                import json as _json
                import pd_streaming
                where = os.path.join(pd_streaming.STATE.get("root") or ".",
                                     "requests.json")
                with open(where, encoding="utf-8") as f:
                    book = _json.load(f)
                LocalAPI._asks = {
                    str(k): (int(v.get("asks") or 1),
                             tuple(str(x) for x in (v.get("whoKeys") or [])))
                    for k, v in (book or {}).items()
                    if isinstance(v, dict) and not v.get("done")}
            except Exception:
                LocalAPI._asks = {}
            LocalAPI._asks_at = now
        return LocalAPI._asks

    def _release_dates(self):
        """The day each film came out, where the catalogue said so.

        The library keeps a year and nothing finer. Sorting "recently released" on a
        year alone leaves every film of this year tied, so the shelf and the page each
        broke the tie their own way and disagreed about what was newest.
        """
        now = time.time()
        if now - LocalAPI._dates_at > 300:
            try:
                import pd_streaming
                LocalAPI._dates = pd_streaming.held_dates()
            except Exception:
                LocalAPI._dates = {}
            LocalAPI._dates_at = now
        return LocalAPI._dates

    def _movie(self, con, row, brief=False):
        files = self._files(con, item_id=row["id"])
        dur = int((files[0]["duration"] or 0) * 1000) if files else 0
        prog = self._progress(con, str(row["id"]))
        out = {
            "ratingKey": str(row["id"]), "type": "movie", "title": row["title"],
            "titleSort": row["sort_title"], "year": row["year"],
            # Sent, not only sorted on. The server knew the day this came out and kept
            # it to itself, so a client ordering the same list had nothing but the
            # year to go on - which is how one shelf and its own page put the same
            # film in two different places.
            "originallyAvailableAt": self._release_dates().get(str(row["id"]), ""),
            "genres": [g.strip() for g in (row["genres"] or "").split(",") if g.strip()],
            "summary": row["overview"] or "", "rating": row["rating"],
            "duration": dur or ((row["runtime"] or 0) * 60000),
            "thumb": f"/art/{row['id']}/poster" if row["poster"] else None,
            "art": f"/art/{row['id']}/backdrop" if row["backdrop"] else None,
            "guid": f"imdb://{row['imdb_id']}" if row["imdb_id"] else None,
            "addedAt": row["added"],
            "viewCount": 1 if self._watched(con, str(row["id"])) else 0,
            # the tallest copy held, so a poster can say 4K without asking further
            "maxHeight": max([self.frame_lines(f) for f in files] or [0]),
            # the subtitle this film was watched through with, or marked as right
            "subsConfirmed": subtitle_verified(str(row["id"])),
        }
        # held in more than one cut - theatrical, extended, a fan edition - for the
        # mark on the poster; copies of one cut in other qualities do not count
        if len(files) > 1:
            from pd_library import cut_groups
            many = len({g for g, _ in cut_groups([(f["path"], f["duration"])
                                                  for f in files])})
            if many > 1:
                out["cuts"] = many
        # a film that has been watched offers no resume point: it would land on the
        # closing seconds, and the button belongs to whoever has not finished it
        if prog and not out["viewCount"]:
            out["viewOffset"] = int((prog["position"] or 0) * 1000)
        if not brief:
            out["Media"] = media_block(
                files, picked=self.picked_subtitle(str(row["id"])),
                proved=str(row["id"]), copy=self.picked_copy(str(row["id"])))
            # Who is in it. Fetched the first time this page is opened and kept, so
            # the shelves stay as quick as they were - they ask brief and get none of
            # this - and pressing a name can be answered from the library.
            # the studios behind it, kept with the library after the first asking
            try:
                out["studios"] = self._studios_for(con, str(row["id"]), row["tmdb_id"])
            except Exception:
                out["studios"] = []
            try:
                out["Role"] = [{"tag": c["name"], "role": c["role"],
                                "id": c["person"],
                                # their face, through this server like every other
                                # picture: a guest away from home reaches us, not TMDB
                                "thumb": ("/art/person/%d" % c["person"])
                                         if c.get("profile") else None}
                               for c in self.lib.credits_for(str(row["id"]))]
            except Exception:
                out["Role"] = []
        return out

    def _show(self, con, row):
        eps = con.execute("SELECT COUNT(*) c FROM episode WHERE item_id=?", (row["id"],)).fetchone()["c"]
        seasons = con.execute("SELECT COUNT(DISTINCT season) c FROM episode WHERE item_id=?",
                              (row["id"],)).fetchone()["c"]
        # when the series last put out an episode: what "recently released" means for
        # television, and what a merged list from several servers sorts on
        aired = con.execute("SELECT MAX(aired) a FROM episode WHERE item_id=? AND aired <> ''",
                            (row["id"],)).fetchone()["a"]
        # and which episode that was, for a shelf of recently released series: its
        # number on the poster, and pressing it opens its season on it
        newest = con.execute("SELECT id, season, number FROM episode WHERE item_id=? "
                             "AND aired = ? ORDER BY season DESC, number DESC LIMIT 1",
                             (row["id"], aired)).fetchone() if aired else None
        # how much of the series this viewer has finished, for the tick on the poster
        seen = sum(1 for r in con.execute("SELECT id FROM episode WHERE item_id=?",
                                          (row["id"],)).fetchall()
                   if self._watched(con, str(r["id"])))
        return {
            # nothing rather than null: a client that reads it as text writes the word
            "originallyAvailableAt": aired or (
                "%04d-01-01" % row["year"] if row["year"] else ""),
            "ratingKey": str(row["id"]), "type": "show", "title": row["title"],
            "titleSort": row["sort_title"], "year": row["year"],
            "summary": row["overview"] or "", "leafCount": eps, "childCount": seasons,
            "genres": [g.strip() for g in (row["genres"] or "").split(",") if g.strip()],
            "thumb": f"/art/{row['id']}/poster" if row["poster"] else None,
            "guid": f"imdb://{row['imdb_id']}" if row["imdb_id"] else None,
            "addedAt": row["added"],
            "viewedLeafCount": seen,
            "viewCount": 1 if eps and seen >= eps else 0,
            "maxHeight": self._tall(con).get(row["id"], 0),
            # an episode here that aired this past week: the series wears New, as its
            # season does and the episode itself
            "fresh": self._fresh_unseen(con, row["id"]),
            "latestKey": str(newest["id"]) if newest else "",
            "latestSeason": int(newest["season"] or 0) if newest else 0,
            "latestNumber": int(newest["number"] or 0) if newest else 0,
            # that episode's season, for the poster on a shelf of what was released
            "latestThumb": (self._season_thumb(row["id"], newest["season"] or 0, row["poster"])
                            if newest else None),
        }

    @staticmethod
    def numbering_shift(path, season, number):
        """How far the file's own episode number is from this episode's.

        A release that counts a double-length premiere as two is one ahead of the
        database for the rest of the season; the scanner puts the episodes on the
        numbers their titles say, and this is what it moved them by. Nought when the
        two agree, and nothing at all when the name does not say.
        """
        m = re.search(r"s(\d{1,2})[\s._-]?e(\d{1,3})|(\d{1,2})x(\d{1,3})",
                      os.path.basename(path or ""), re.I)
        if not m:
            return None
        said_season = int(m.group(1) or m.group(3))
        said_number = int(m.group(2) or m.group(4))
        if said_season != int(season or 0):
            return None
        return said_number - int(number or 0)

    def _fresh_unseen(self, con, item_id, season=None):
        """Whether a series, or one season of it, holds an episode here that is New and
        that this viewer has not watched: a watched episode wears no ribbon, and so
        lends none to its season or series."""
        sql = ("SELECT e.id, e.aired FROM episode e JOIN file f ON f.episode_id = e.id "
               "WHERE e.item_id=?")
        args = [str(item_id)]
        if season is not None:
            sql += " AND e.season=?"
            args.append(season)
        return any(self._fresh(e["aired"]) and not self._watched(con, str(e["id"]))
                   for e in con.execute(sql, args).fetchall())

    @classmethod
    def _fresh(cls, aired):
        """Aired within the days set for New, the air day the first: the New ribbon."""
        # dates, not mktime: Windows' mktime refuses anything before 1970
        import datetime
        try:
            day = datetime.date.fromisoformat(str(aired or "")[:10])
        except ValueError:
            return False
        return 0 <= (datetime.date.today() - day).days < new_days()

    def _episode(self, con, row, show=None, brief=False):
        show = show or con.execute("SELECT * FROM item WHERE id=?", (row["item_id"],)).fetchone()
        files = self._files(con, episode_id=row["id"])
        dur = int((files[0]["duration"] or 0) * 1000) if files else 0
        prog = self._progress(con, str(row["id"]))
        out = {
            "ratingKey": str(row["id"]), "type": "episode",
            "title": row["title"] or ("Episode %d" % row["number"]),
            "summary": row["overview"] or "", "index": row["number"],
            "parentIndex": row["season"], "duration": dur,
            "grandparentTitle": show["title"] if show else "",
            "grandparentRatingKey": str(row["item_id"]),
            "genres": [g.strip() for g in ((show["genres"] if show else "") or "").split(",") if g.strip()],
            "parentRatingKey": "%s-s%d" % (row["item_id"], row["season"]),
            "grandparentThumb": f"/art/{row['item_id']}/poster" if show and show["poster"] else None,
            "thumb": f"/art/{row['item_id']}/poster" if show and show["poster"] else None,
            "parentThumb": self._season_thumb(row["item_id"], row["season"] or 0,
                                              show and show["poster"]),
            "originallyAvailableAt": row["aired"],
            "viewCount": 1 if self._watched(con, str(row["id"])) else 0,
            "maxHeight": max([self.frame_lines(f) for f in files] or [0]),
            # what the file calls this episode, against what the season does: a
            # release one ahead is filed by title rather than by number, and the
            # difference is worth saying rather than leaving somebody to notice
            "numberShift": (self.numbering_shift(files[0]["path"], row["season"],
                                                 row["number"]) if files else None),
            # the series is being watched with subtitles: the client turns them on
            # without waiting to be told twice
            "subsWanted": subtitles_wanted(row["item_id"]),
            # the release this episode itself was watched through with
            "subsConfirmed": subtitle_confirmed(row["id"]),
        }
        if prog and not out["viewCount"]:
            out["viewOffset"] = int((prog["position"] or 0) * 1000)
        # seconds of channel ident a fresh start begins past
        import pd_leads
        hook = getattr(self, "lead_rules", None)
        lead = pd_leads.lead_for(hook() if hook else _settings().get("skipStart"),
                                 row["item_id"], row["season"], row["number"])
        if lead:
            out["skipStart"] = lead
        # new, and not yet watched by whoever is asking
        if files and self._fresh(row["aired"]) and not self._watched(con, str(row["id"])):
            out["fresh"] = True
        if not brief:
            # the series' name and this episode's title are no evidence of a release
            out["Media"] = media_block(
                files,
                aside=((show["title"] if show else "") + " " + (row["title"] or "")),
                picked=self.picked_subtitle(str(row["id"])),
                copy=self.picked_copy(str(row["id"])))
        return out

    # ---- endpoints ----------------------------------------------------------
    def handle(self, path, q):
        """Returns (status, content_type, body_bytes). Path is everything after /local."""
        con = self.lib.db()
        try:
            body = self.route(con, path, q)
        finally:
            con.close()
        if body is None:
            return 404, "application/json", b'{"error":"not found"}'
        if isinstance(body, tuple):                      # (content_type, bytes)
            return 200, body[0], body[1]
        return 200, "application/json", json.dumps({"MediaContainer": body}).encode()

    def route(self, con, path, q):
        one = lambda k, d=None: (q.get(k) or [d])[0]

        if path == "/library/sections":
            return {"size": len(SECTIONS), "Directory": SECTIONS}

        m = re.match(r"^/library/matches/([0-9a-f]{12})$", path)
        if m:
            # what else this title could be, for somebody to choose from when the
            # first answer TMDB gave was the wrong programme
            try:
                return {"size": 0,
                        "matches": self.lib.candidates(m.group(1), one("q", ""))}
            except Exception as e:
                return {"size": 0, "matches": [], "error": str(e)[:200]}

        if path == "/library/genres":
            # What is on the shelves and how much of it, counted among whatever is
            # already marked rather than across the whole library. Mark Animation and
            # every other number falls to how many of those are also that - which is
            # what the filter does, shown rather than explained.
            #
            # The films on offer from the packs are counted too, because the shelf
            # lists them: the number against a genre is what picking it will show, and
            # it counted only what was on the disk - three hundred against a word that
            # then filled the screen with two thousand.
            kind = one("type", "movie")
            wants = {g.strip().lower() for g in one("genre", "").split(",") if g.strip()}
            firsts = self.decades_asked(one("decade", ""))
            tally = {}

            def count_in(names, year):
                mine = {str(g).strip() for g in names if str(g).strip()}
                if wants and not wants <= {g.lower() for g in mine}:
                    return
                if firsts and not (year and any(f <= year <= f + 9 for f in firsts)):
                    return
                for one_name in mine:
                    tally[one_name] = tally.get(one_name, 0) + 1

            for r in con.execute("SELECT genres, year FROM item "
                                 "WHERE type=? AND genres <> ''", (kind,)).fetchall():
                count_in((r["genres"] or "").split(","), int(r["year"] or 0))
            if kind == "movie":
                for o in self._offered_quietly():
                    count_in(o.get("genres") or [], int(o.get("year") or 0))
            listed = [{"title": g, "count": n} for g, n in
                      sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))]
            return {"size": len(listed), "Directory": listed}

        if path == "/library/decades":
            # which decades the shelves actually hold, newest first. A decade nobody
            # has anything from is not worth offering, and a title with no year is
            # from no decade rather than from the first one.
            # Narrowed by the genres marked, but not by the decades: a title has one
            # year, so counting the eighties among titles already cut to the eighties
            # would put a nought against every other decade. The packs are counted for
            # the same reason the genres count them - it is what picking one shows.
            kind = one("type", "movie")
            wants = {g.strip().lower() for g in one("genre", "").split(",") if g.strip()}
            tally = {}

            def count_year(names, year):
                if not year:
                    return
                if wants and not wants <= {str(g).strip().lower() for g in names
                                           if str(g).strip()}:
                    return
                era = (year // 10) * 10
                tally[era] = tally.get(era, 0) + 1

            for r in con.execute("SELECT genres, year FROM item "
                                 "WHERE type=? AND year > 0", (kind,)).fetchall():
                count_year((r["genres"] or "").split(","), int(r["year"] or 0))
            if kind == "movie":
                for o in self._offered_quietly():
                    count_year(o.get("genres") or [], int(o.get("year") or 0))
            listed = [{"title": "%ds" % era, "decade": era, "count": n}
                      for era, n in sorted(tally.items(), reverse=True)]
            return {"size": len(listed), "Directory": listed}

        m = re.match(r"^/library/sections/(\d+)/all$", path)
        if m:
            kind = "movie" if m.group(1) == "1" else "show"
            sort = one("sort", "titleSort:asc")
            order = {"titleSort:asc": "sort_title ASC", "titleSort:desc": "sort_title DESC",
                     # A title with no year at all goes to the bottom, whichever
                     # way the list runs. It is not the newest thing in the library
                     # and it is not the oldest either - it is unidentified, and
                     # nobody wants a pile of those at the top of a list they have
                     # just turned round. NULLIF because an unknown year is written
                     # down as a nought as often as as nothing.
                     "year:asc":
                         "NULLIF(year, 0) IS NULL, NULLIF(year, 0) ASC, sort_title ASC",
                     "year:desc":
                         "NULLIF(year, 0) IS NULL, NULLIF(year, 0) DESC, sort_title ASC",
                     "addedAt:desc": "added DESC, sort_title ASC",
                     "addedAt:asc": "added ASC, sort_title ASC",
                     "originallyAvailableAt:desc":
                         "NULLIF(year, 0) IS NULL, NULLIF(year, 0) DESC, sort_title ASC",
                     "originallyAvailableAt:asc":
                         "NULLIF(year, 0) IS NULL, NULLIF(year, 0) ASC, sort_title ASC",
                     }.get(sort, "sort_title ASC")
            tall = self._tall(con) if sort.startswith("quality:") else {}
            if kind == "show" and sort.startswith("originallyAvailableAt:"):
                # a series is as recent as its newest episode, not as its first year
                rows = con.execute(
                    """SELECT i.*, COALESCE(
                           (SELECT MAX(e.aired) FROM episode e
                            WHERE e.item_id = i.id AND e.aired IS NOT NULL AND e.aired <> ''),
                           NULLIF(printf('%04d-01-01', COALESCE(i.year, 0)),
                                  '0000-01-01')) AS last_aired
                       FROM item i WHERE i.type='show'
                         AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = i.id)
                       ORDER BY last_aired IS NULL, last_aired """
                    # the SQL contains a printf of its own, so this is joined on
                    # rather than formatted in
                    + ("ASC" if sort.endswith(":asc") else "DESC")
                    + ", sort_title ASC").fetchall()
            else:
                # Only what this machine holds a file for. A copy carries the whole
                # catalogue as names and posters so it can say what a key is, and
                # listed them all: with the main server off it offered five thousand
                # films and could play about one in forty. On the main server every
                # item has a file, so this changes nothing there.
                rows = con.execute(
                    "SELECT * FROM item WHERE type=? "
                    "AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = item.id) "
                    "AND " + NOT_EXTRA + " "
                    f"ORDER BY {order}", (kind,)).fetchall()
            # Films carry a year and nothing finer, so "recently released" could only
            # sort by year and then by title - which put a film released last week
            # under R, thousands of rows down a list of this year's titles. The dates
            # read for the new arrivals row are used where there is one.
            if kind == "movie" and sort.startswith("originallyAvailableAt:"):
                try:
                    import pd_streaming
                    dated = pd_streaming.held_dates()
                except Exception:
                    dated = {}
                if dated:
                    def when(r):
                        got = dated.get(str(r["id"]))
                        if got:
                            return got
                        y = int(r["year"] or 0)
                        # a year on its own sits below any dated film of that year,
                        # and above every film of the year before
                        return "%04d-00-00" % y if y else ""
                    back = sort.endswith(":desc")
                    has = [r for r in rows if when(r)]
                    none = [r for r in rows if not when(r)]
                    # sorted twice, and stably: the alphabet decides between two films
                    # released on the same day
                    has.sort(key=lambda r: (r["sort_title"] or "").lower())
                    has.sort(key=when, reverse=back)
                    none.sort(key=lambda r: (r["sort_title"] or "").lower())
                    rows = has + none
            if tall:
                # picture first, title second, and nothing measured at the bottom
                # either way round - an unprobed file is not a small one, it is
                # unknown, and a shelf of those above the 4K is nobody's idea of
                # "best first"
                rows = sorted(rows, key=lambda r: (
                    tall.get(r["id"], 0) == 0,
                    -tall.get(r["id"], 0) if sort.endswith(":desc")
                    else tall.get(r["id"], 0),
                    (r["sort_title"] or "").lower()))
            # one genre at a time: the lists are short enough to sift here, and it
            # keeps the ordering above from having to know about it
            want = one("genre", "").strip().lower()
            # several genres, comma-joined: a title carrying all of them (sci-fi and comedy)
            wants = {g.strip() for g in want.split(",") if g.strip()}
            if wants:
                rows = [r for r in rows
                        if wants <= {g.strip().lower()
                                     for g in (r["genres"] or "").split(",")}]
            # decades, read the way people say them: 80 or 1980 both mean the
            # eighties. Several may be asked for at once and a title need only be from
            # one of them - a film has one year, so asking for all of them at once
            # would answer nothing. A title with no year is in no decade rather than in
            # the first one - a nought is what an unidentified film is written down
            # as, not a claim about when it was made.
            era = one("decade", "").strip()
            firsts = self.decades_asked(era)
            if firsts:
                rows = [r for r in rows
                        if (r["year"] or 0) and any(f <= r["year"] <= f + 9
                                                    for f in firsts)]
            # what this viewer wants the shelf to stand, held apart from the offers
            # below so that turning the library itself off still leaves them
            show = self.films_show() if kind == "movie" else {"disk": True,
                                                              "download": True,
                                                              "request": False}
            # The recently added shelf is about what has arrived here, and a film on
            # a pack or one to ask for has not. Either the caller says so - it opened
            # this shelf off that row - or the shelf is standing in that order, which
            # is the same question asked by hand. Everywhere else the two kinds show
            # as the switches in Settings say.
            if (one("disk", "") in ("1", "true", "yes")
                    or sort.startswith("addedAt")):
                show = dict(show, download=False, request=False)
            if not show.get("disk"):
                rows = []
            # How far back the part nobody holds reaches. The same window the new
            # arrivals reading uses: a film nobody here has is on this shelf because
            # it is new, and the reading itself may have gone deeper than that.
            cut = ""
            if kind == "movie" and show.get("request"):
                import pd_streaming
                cut = time.strftime(
                    "%Y-%m-%d",
                    time.localtime(time.time()
                                   - pd_streaming.NEW_MONTHS * 30.5 * 86400))
            items = [self._movie(con, r, brief=True) if kind == "movie" else self._show(con, r)
                     for r in rows]
            if not sort.startswith("quality:"):
                items = self._with_offered(items, sort, want, era, kind, show, cut)
            return self._page(items, q)

        m = re.match(r"^/library/sections/(\d+)/recentlyReleased$", path)
        if m:
            # the newest episodes by air date, to match the shelf beside it - a shelf
            # of series would answer a different question from "recently released"
            rows = con.execute("""SELECT e.* FROM episode e JOIN file f ON f.episode_id=e.id
                                  WHERE e.aired IS NOT NULL AND e.aired <> ''
                                  GROUP BY e.id ORDER BY e.aired DESC LIMIT 60""").fetchall()
            items = [self._episode(con, r, brief=True) for r in rows]
            # and what is on its way: the next episode of a series this viewer is up to
            # date with, greyed and dated - all of it in air-date order, newest first,
            # so a date ahead stands before today's and "out, not here yet" among the
            # episodes of its own day
            try:
                items = self._upcoming(con) + items
            except Exception:
                pass
            items.sort(key=lambda m: str(m.get("originallyAvailableAt") or ""), reverse=True)
            return self._page(items, q)

        if path == "/library/upcoming":
            # the same greyed cards on their own, for the app's released-series row
            try:
                return self._page(self._upcoming(con), q)
            except Exception:
                return self._page([], q)

        if path == "/library/withPerson":
            # Everything held with one person in it. By TMDB's number where the client
            # has one, by name otherwise - two actors share a name often enough that
            # the number is worth preferring.
            try:
                person = int(one("person", "0") or 0)
            except ValueError:
                person = 0
            found = self.lib.with_person(person, one("name", ""))
            con2 = self.lib.db()
            try:
                items = []
                for r in found:
                    got = self.metadata_for(con2, str(r["id"]), brief=True)
                    if got:
                        got["role"] = r.get("role") or ""
                        items.append(got)
            finally:
                con2.close()
            # and what is not on disk: a pack that could fetch them, or a title to
            # ask for. Shown as the viewer has asked for those to be shown anywhere.
            items = items + self._people_offers(person, one("name", ""), items,
                                                self.films_show())
            # in the order they came out, the newest first and the oldest last. The
            # day where it is known, else the year; one with neither goes at the end.
            def released(m):
                day = str(m.get("originallyAvailableAt") or "")[:10]
                year = int(m.get("year") or (day[:4] if day[:4].isdigit() else 0) or 0)
                return (year, day or "%04d-00" % year)
            items.sort(key=released, reverse=True)
            return self._page(items, q)

        m = re.match(r"^/library/sections/(\d+)/recentlyAdded$", path)
        if m:
            kind = "movie" if m.group(1) == "1" else "show"
            if kind == "movie":
                # only what has a file: a place kept for a film not copied here is a
                # row with no file, no poster, and the time it was written down
                rows = con.execute("SELECT * FROM item WHERE type='movie' AND " + NOT_EXTRA +
                                   " AND EXISTS (SELECT 1 FROM file f WHERE f.item_id=item.id)"
                                   " ORDER BY added DESC "
                                   "LIMIT 100").fetchall()
                items = [self._movie(con, r, brief=True) for r in rows]
                # every film coming in or queued leads the shelf, one card each, in
                # queue order
                try:
                    import pd_torrents
                    have = {(str(m.get("title") or "").lower(), m.get("year"))
                            for m in items}
                    coming = [m for m in pd_torrents.active()
                              if m.get("type") == "movie"
                              and (m.get("offer") or {}).get("state")
                              in ("downloading", "queued")
                              and (str(m.get("title") or "").lower(),
                                   m.get("year")) not in have]
                    # and what is coming straight from the tracker: a new release
                    # being fetched was on no shelf until it had arrived
                    for card in self._coming_offers().values():
                        mark = (str(card.get("title") or "").lower(), card.get("year"))
                        if mark in have or any(c.get("ratingKey") == card.get("ratingKey")
                                               for c in coming):
                            continue
                        coming.append(dict(card, addedAt=int(time.time())))
                except Exception:
                    coming = []
                items = coming + items
            else:
                # A season at a time. Adding a series used to put one card on the shelf
                # per episode - twenty-two of the same programme, pushing everything
                # else off the end - when what arrived was a season.
                rows = con.execute("""
                    SELECT e.item_id, e.season, COUNT(*) AS episodes,
                           MAX(COALESCE(f.ctime, f.mtime)) AS added
                    FROM episode e JOIN file f ON f.episode_id=e.id
                    GROUP BY e.item_id, e.season
                    ORDER BY added DESC LIMIT 60""").fetchall()
                items = [self._season_card(con, r) for r in rows]
            return self._page(items, q)

        if path == "/library/find":
            # The same title on another machine, by what it is rather than by the
            # number this library happens to file it under. A viewer whose server
            # goes off is handed to the machine keeping copies, and that machine
            # numbered its own library: the film is the same, the key is not.
            from pd_library import flatten_title
            kind = (one("type") or "").lower()
            if kind == "episode":
                show = flatten_title(one("show") or "")
                try:
                    season, number = int(one("season") or 0), int(one("episode") or 0)
                except ValueError:
                    season = number = 0
                for row in con.execute(
                        "SELECT e.id, i.title FROM episode e JOIN item i "
                        "ON i.id=e.item_id WHERE e.season=? AND e.number=?",
                        (season, number)):
                    if flatten_title(row["title"]) == show:
                        return {"key": str(row["id"])}
                return {"key": ""}
            plain = flatten_title(one("title") or "")
            year = one("year") or ""
            for row in con.execute("SELECT id, title, year FROM item "
                                   "WHERE type='movie'"):
                if flatten_title(row["title"]) != plain:
                    continue
                if year and str(row["year"] or "") != str(year):
                    continue
                return {"key": str(row["id"])}
            return {"key": ""}

        if path == "/library/seasonal":
            # The season's shelf: the films the catalogue files under the holiday that
            # is switched on, of what this house holds or a pack can give, the best
            # known first. Nothing when it is switched off or it is no holiday.
            import datetime
            import pd_holidays
            which = pd_holidays.chosen(_settings().get("seasonal"), datetime.date.today())
            if not which:
                return dict(self._page([], q), holiday="", title="")
            ids = pd_holidays.films(self.lib, which)
            cards = []
            if ids:
                rows = con.execute(
                    "SELECT DISTINCT i.id, i.tmdb_id FROM item i JOIN file f ON f.item_id = i.id "
                    "WHERE i.type = 'movie' AND i.tmdb_id IN (%s)" % ",".join("?" * len(ids)),
                    ids).fetchall()
                for r in rows:
                    got = self.metadata_for(con, str(r["id"]), brief=True)
                    if got:
                        cards.append(dict(got, tmdb=int(r["tmdb_id"] or 0)))
                if self.films_show().get("download"):
                    cards += [dict(c) for c in self._offered_quietly()]
            out = pd_holidays.in_order(cards, ids)
            title = pd_holidays.BY_ID[which]["title"]
            for n, card in enumerate(out):
                # the shelf's name travels on its cards, and their place in it: a
                # client merging two machines' answers keeps neither otherwise
                card["shelf"] = title
                card["seasonRank"] = len(out) - n
            return dict(self._page(out, q), holiday=which, title=title)

        if path == "/library/streaming":
            # What has just turned up to watch at home, which this house has none of.
            # Read twice a day; asking the page again while somebody waits would put a
            # website between them and their own front page, so a list already in hand
            # is answered with and the reading is done behind it.
            import pd_streaming
            # how far back to look: two years unless asked otherwise, and "all" is
            # as far as anything goes. A narrower window sifts the list in hand; a
            # wider one has to be read again, because a film left out of the reading
            # cannot be filtered back into it.
            try:
                years = float(one("years", "2") or 2)
            except ValueError:
                years = 2.0
            months = 1200 if years <= 0 else max(1, int(round(years * 12)))
            if months > pd_streaming.window():
                pd_streaming.refresh(self.lib, force=True, months=months)
            if one("fresh", "") in ("1", "true", "yes"):
                pd_streaming.refresh(self.lib, force=True, months=months)   # read it again now
            elif not pd_streaming.read():
                pd_streaming.refresh(self.lib)
            elif time.time() - pd_streaming.STATE["at"] > pd_streaming.EVERY:
                threading.Thread(target=lambda: pd_streaming.refresh(self.lib),
                                 daemon=True).start()
            # A title this house already holds is answered with the library's own
            # entry for it: an ordinary poster with a Play button, no band across the
            # corner, and pressing it opens the film rather than a page for asking.
            out = []
            # One card per film. Two catalogue entries can name the same film - a
            # release listed twice, a fortnight apart - and where this house holds it
            # both were answered with the library's own item, so the same key stood on
            # the shelf twice. A repeated key crashes a keyed list outright. The rows
            # are newest first, so the first of a pair is the one kept.
            seen = set()
            cut = ("" if months >= 1200 else
                   time.strftime("%Y-%m-%d",
                                 time.localtime(time.time() - months * 30.5 * 86400)))
            # only what can be watched at home: a film in cinemas with a pre-order
            # link has no release to download yet
            coming_in = self._coming_offers()
            for row in pd_streaming.shown():
                # judged as this viewer asks to have new films judged
                if not self.well_thought_of(row):
                    continue
                here = row.get("here")
                got = self.metadata_for(con, str(here), brief=True) if here else None
                if got and row.get("released"):
                    # the date this row is ordered by. The library's own entry often
                    # has none - it knows when a film arrived here, not when it came
                    # out - and without it the film sank to the end of the list.
                    got["originallyAvailableAt"] = row["released"]
                # outside the window asked for: it was read for a wider one
                if cut and str(row.get("released") or "") and str(row["released"]) < cut:
                    continue
                shown = got or self._wearing(pd_streaming.item(row), coming_in)
                if str(shown.get("ratingKey") or "") in seen:
                    continue
                seen.add(str(shown.get("ratingKey") or ""))
                # how many people have asked for it, so the button can say so
                said = self._asked_by_how_many().get(str(row.get("key") or ""))
                if said:
                    shown["asks"] = said[0]
                    # and whether this viewer is one of them, so the button can say
                    # Requested rather than offering it again
                    shown["asked"] = self._did_i_ask(row.get("key"))
                out.append(shown)
            return self._page(out, q)

        if path == "/library/watchlist":
            # the keys come from the caller's own settings; this only turns them into
            # things with posters, and quietly drops any that have left the library
            keys = [k for k in (q.get("keys", [""])[0] or "").split(",") if k]
            con = self.lib.db()
            try:
                if one("byseason", "") in ("1", "true", "yes"):
                    items = self._by_season(con, keys)
                else:
                    items = [self.metadata_for(con, k, brief=True) for k in keys]
                # A key can stop being the key. A film starred while a pack was the
                # only thing carrying it is stored under the pack's key, and the day
                # it arrives in the library it has a library key - so it stood on the
                # watchlist with its star unlit, because the star looks for the key
                # the poster now has. Where one resolves to another, the mark is
                # moved to what it resolved to.
                moved = {}
                if not one("byseason", "") in ("1", "true", "yes"):
                    for key, got in zip(keys, items):
                        if got and str(got.get("ratingKey") or "") not in ("", key):
                            moved[key] = str(got["ratingKey"])
                self.marks_moved = moved
            finally:
                con.close()
            return self._page([i for i in items if i], q)

        if path == "/library/onDeck":
            # `updated` comes back as well: putting a programme aside is a moment in
            # time, and it stays aside only until something newer happens to it
            # Deep enough to survive a tick storm: marking a season watched writes
            # a row per episode, and at sixty rows those alone pushed everything
            # anybody was half-way through off the shelf. Most of what comes back is
            # thrown away below - finished, or handed on to the next episode - so the
            # window is about how far back to look, not how much to show.
            rows = con.execute(
                "SELECT key, position, duration, updated, COALESCE(marked, 0) marked, "
                "       MAX(COALESCE(furthest, 0), COALESCE(position, 0)) reached "
                "FROM progress WHERE who=? AND COALESCE(casual, 0) = 0 "
                "ORDER BY updated DESC LIMIT 600",
                (self.who,)).fetchall()
            items, taken = [], set()
            # what each viewer has finished, read once, and each series' episodes in
            # order, read once a series: the walk forward is lookups, not queries
            seen = None
            in_order = {}

            def later_than(key):
                here = con.execute("SELECT item_id, season, number FROM episode "
                                   "WHERE id=?", (str(key),)).fetchone()
                if not here:
                    return []
                show = here["item_id"]
                if show not in in_order:
                    rows_of = [
                        (int(e["season"] or 0), int(e["number"] or 0), str(e["id"]))
                        for e in con.execute(
                            """SELECT DISTINCT e.id, e.season, e.number FROM episode e
                                 JOIN file f ON f.episode_id = e.id
                                WHERE e.item_id = ? ORDER BY e.season, e.number""",
                            (show,))]
                    # places and keys apart, so the episodes after one are found by
                    # bisection: walked whole, a long series cost 3 ms a finished
                    # episode and two hundred of them made the shelf take 0.6 s
                    in_order[show] = ([(sn, n) for sn, n, _ in rows_of],
                                      [k for _, _, k in rows_of])
                at = (int(here["season"] or 0), int(here["number"] or 0))
                places, keys = in_order[show]
                import bisect
                return keys[bisect.bisect_right(places, at):]

            for r in rows:
                key = r["key"]
                # Everything part-way through belongs here. There is no third
                # state between watching something and having watched it: a title
                # leaves this shelf when it is marked watched, and not before.
                # a mark made by hand is finished however little of it was played:
                # it says so, and the shelf hands over to the next episode
                # the furthest anybody got, not where the player was left: an episode
                # watched to the credits and then taken back to the beginning is
                # watched, and reading only the position called it untouched
                finished = bool(r["marked"]) or self.watched_through(
                    r["reached"], r["duration"], r["key"])
                # Nought is a place like any other: somebody who rewound to the
                # beginning is watching it from the beginning, and the episode they
                # are on is the one this shelf is for. What nought used to mean here
                # was "crossed off", and that is written down separately - so the
                # cross is what takes a row off the shelf, not the number nought.
                if not finished:
                    gone = con.execute(
                        "SELECT at FROM forgot WHERE who=? AND key=?",
                        (self.who, key)).fetchone()
                    if gone and int(gone["at"] or 0) >= int(r["updated"] or 0):
                        continue
                    # An episode is kept wherever it is: it is the one its programme
                    # is on, and somebody who rewound to the beginning is watching it
                    # from the beginning. A film has no programme to be the place of -
                    # opened for a second and shut again it is not something anybody is
                    # part-way through - so it needs a place worth coming back to.
                    #
                    # Neither is kept for a place of nothing at all. A row with no
                    # position, no furthest and no length is not somewhere anybody
                    # left off; it is a place that arrived empty from the other
                    # machine, and it put an episode nobody had opened on the shelf.
                    if not (r["reached"] or 0) and not (r["duration"] or 0):
                        continue
                    if not str(key).startswith("e") and (r["reached"] or 0) <= 30:
                        continue
                if finished:
                    # An episode hands its place to the next one - but only to one that
                    # has not been watched either, or marking a season watched would
                    # fill the shelf with episodes already seen. A film has nowhere to
                    # go and simply leaves.
                    if not str(key).startswith("e"):
                        continue
                    if seen is None:
                        seen = self._watched_all(con)
                    ahead = iter(later_than(key))
                    for _ in range(400):            # a season is not longer than this
                        key = next(ahead, None)
                        if not key:
                            break
                        if seen.get(key, False):
                            continue
                        # and not one taken back to nought since. A cross on a
                        # handed-over episode means the programme leaves the shelf,
                        # so the walk stops rather than offering the one after it:
                        # clearing an episode with no progress of its own used to
                        # draw the next episode in its place, press after press.
                        put = con.execute(
                            "SELECT position, updated FROM progress "
                            "WHERE who=? AND key=? AND COALESCE(casual, 0) = 0",
                            (self.who, key)).fetchone()
                        if (put and float(put["position"] or 0) <= 30
                                and int(put["updated"] or 0) >= int(r["updated"] or 0)):
                            key = ""
                            break
                        # and one taken back before there was a row to take back:
                        # saying "not watched" used to throw the row away, so there
                        # is nothing to read - the note that it was forgotten is
                        # what remains, and it counts the same way
                        gone = con.execute(
                            "SELECT at FROM forgot WHERE who=? AND key=?",
                            (self.who, key)).fetchone()
                        if gone and int(gone["at"] or 0) >= int(r["updated"] or 0):
                            key = ""
                            break
                        break
                    if not key:
                        continue
                if key in taken:
                    continue
                taken.add(key)
                # A row for every place, films and episodes alike. This used to keep
                # one row per programme, so a series watched out of order showed only
                # the first of its places and the rest were invisible - somebody
                # part-way through four episodes was told about one. What is
                # part-way through is what this shelf is for, all of it.
                row = self.metadata_for(con, key, brief=True)
                # an episode whose programme has gone from this library: a card with no
                # name to it, and nothing to open it onto
                if row and row.get("type") == "episode" and not row.get("grandparentTitle"):
                    row = None
                if row:
                    # when it was last watched, which is what the shelf is ordered by.
                    # Only the shuffled rows said, so everything else sorted as nought
                    # and the thing watched five minutes ago landed anywhere.
                    row["lastViewedAt"] = int(r["updated"] or 0)
                    # the next episode, arrived since the last one was watched: dated
                    # by its arrival, so the card that was greyed Upcoming comes back
                    # first on the shelf
                    if finished:
                        came = con.execute(
                            "SELECT MAX(COALESCE(ctime, mtime)) at FROM file "
                            "WHERE episode_id=?", (str(key),)).fetchone()
                        if came and int(came["at"] or 0) > row["lastViewedAt"]:
                            row["lastViewedAt"] = int(came["at"])
                    # and whether this machine can actually play it. The copy holds a
                    # slice of the library, so a place written on the main server is
                    # worth showing here even where the file is not - shown faded and
                    # not offered, rather than missing, which reads as lost.
                    if not self.file_for(str(key), 0):
                        row["unplayable"] = True
                items.append(row)

            # and the programmes somebody is up to date with: the next episode, greyed,
            # with when it airs - so a series being waited on does not simply vanish
            try:
                items.extend(self._upcoming(con))
            except Exception:
                pass
            # A shelf being shuffled gets one row, and one row is the whole point: a
            # hat of two hundred episodes putting each one it touched onto the shelf
            # is what made Continue watching unreadable in the first place. What the
            # row shows is what somebody would press play on - whatever was left
            # part-way, and otherwise whatever the hat has drawn next.
            for cid, round_now in (self._mine("shuffles", {}) or {}).items():
                if not isinstance(round_now, dict):
                    continue
                places = round_now.get("at") or {}

                def when(value):
                    return int(value.get("when") or 0) if isinstance(value, dict) else 0

                def seconds(value):
                    return (int(value.get("at") or 0) if isinstance(value, dict)
                            else int(value or 0))

                # The title the round is on - the last drawn, until it is finished -
                # and otherwise whatever it would draw next. Tried in that order and
                # the first one the library can resolve wins: a key it cannot was
                # taken as the answer and the shelf then had no row at all.
                played = [str(k) for k in (round_now.get("played") or [])]
                tries = []
                if played and played[-1] != str(round_now.get("done") or ""):
                    tries.append(played[-1])
                # what the hat draws next, never one already played this round
                gone = set(played)
                tries.extend([str(k) for k in (round_now.get("queue") or [])
                              if str(k) not in gone][:5])
                one, key, at = None, "", 0
                for maybe in tries:
                    one = self.metadata_for(con, str(maybe), brief=True)
                    if one:
                        key = str(maybe)
                        # where it was left, which only the title the round is on has
                        at = seconds(places[key]) if key in places else 0
                        break
                if not one:
                    continue
                name = (self._mine("shelf_names", {}) or {}).get(str(cid)) or "Shuffle"
                # The shelf's own place stands beside an ordinary one for the same
                # film rather than replacing it. Putting something on and later
                # choosing it are two viewings of one title at two different seconds,
                # and collapsing them into one row is what made it impossible to see
                # which was which. Both stand; the badge says which is the shelf's.
                # what the card says across its poster, and where pressing it starts
                one["shuffle"] = name
                one["shuffleId"] = str(cid)
                if at > 30:
                    one["viewOffset"] = int(at) * 1000
                else:
                    one.pop("viewOffset", None)      # an ordinary place is not the shuffle's
                # when the shelf was last played, so the row sorts among the others by
                # how recent it is. Without it the row had no date at all: a client
                # ordering the shelf by when things were last watched put it at the
                # very end, where a page of a dozen cards cuts it off - it appeared
                # for as long as the first answer was on screen and then went.
                # A drawn title nobody has started has no place of its own: the latest
                # place in the round dates it. Dating it "now" put the shelf ahead of
                # whatever was actually watched last.
                # When the round was last played, from the watch log: a finished
                # episode takes its place with it, and the card then had no date and
                # fell to the end of the shelf.
                recent = played[-40:]
                last_play = 0
                if recent:
                    try:
                        got = con.execute(
                            "SELECT MAX(updated) FROM watchlog WHERE who=? AND "
                            "COALESCE(casual, 0) = 1 AND key IN (%s)"
                            % ",".join("?" * len(recent)), [self.who] + recent).fetchone()
                        last_play = int((got[0] if got else 0) or 0)
                    except Exception:
                        last_play = 0
                one["lastViewedAt"] = max(last_play, when(places.get(key)),
                                          max([when(v) for v in places.values()] or [0])
                                          ) or int(round_now.get("casualStamp") or 0) or 1
                items.append(one)
            # an episode, shuffled or not, wears its own season's poster
            for one in items:
                if one and one.get("type") == "episode" and one.get("grandparentRatingKey"):
                    art = self._season_thumb(one["grandparentRatingKey"],
                                             one.get("parentIndex") or 0,
                                             one.get("grandparentThumb"))
                    if art:
                        one["thumb"] = one["parentThumb"] = art
            return self._page([i for i in items if i], q)

        if path == "/prev":
            # and what came before it, for the button beside the other one
            before = self.prev_episode_key(con, one("key", ""))
            item = self.metadata_for(con, before) if before else None
            return {"size": 1, "Metadata": [item]} if item else {"size": 0, "Metadata": []}

        if path == "/next":
            # what follows this episode: the client asks rather than working out for
            # itself where a season ends and the next begins
            here_key = one("key", "")
            after = self.next_episode_key(con, here_key)
            # The one that actually follows, even where there is no file for it.
            # next_episode_key joins to the files, so an episode only a pack has is
            # not in its answer at all - and Next stepped over it in silence, handing
            # back the episode after that. Somebody working through a series was
            # quietly skipped past whatever had not been fetched.
            gap = self.offered_next_episode(con, here_key, after)
            if gap:
                return {"size": 1, "Metadata": [gap]}
            item = self.metadata_for(con, after) if after else None
            return {"size": 1, "Metadata": [item]} if item else {"size": 0, "Metadata": []}

        m = re.match(r"^/library/metadata/([^/]+)$", path)
        if m:
            # opening one title is the moment to find out what soundtracks it has,
            # for anything indexed before the library recorded them
            key = m.group(1)
            if key.startswith("o"):
                # a film on offer from a torrent pack - and once it has come in, the film
                # itself, so a page left open on the offer turns into the film's own
                import pd_torrents
                here = pd_torrents.arrived(key)
                if not here:
                    offer = pd_torrents.metadata(key)
                    # and who is in it, as a film here would say: the page draws its
                    # cast and Show more from this
                    if offer and offer.get("type") == "movie":
                        try:
                            offer["Role"] = self._cast_of(
                                key, "movie", offer.get("tmdb") or offer.get("tmdbId"))
                        except Exception:
                            offer["Role"] = []
                    return {"size": 1, "Metadata": [offer]} if offer else None
                key = here
            try:
                if key.startswith("e"):
                    files = con.execute("SELECT * FROM file WHERE episode_id=?",
                                        (key,)).fetchall()
                elif is_title(key):
                    files = con.execute("SELECT * FROM file WHERE item_id=? AND "
                                        "episode_id IS NULL", (str(key),)).fetchall()
                else:
                    files = []
                self.learn_audio(con, files)
            except Exception:
                pass                       # a soundtrack list is not worth a failure
            item = self.metadata_for(con, key)
            return {"size": 1, "Metadata": [item]} if item else None

        # the episode a press of Play on a programme starts
        m = re.match(r"^/library/metadata/([0-9a-f]{12})/nextUp$", path)
        if m:
            got = self.next_up(con, m.group(1))
            return {"size": 1 if got else 0, "Metadata": [got] if got else []}

        m = re.match(r"^/library/metadata/([^/]+)/children$", path)
        if m:
            key = m.group(1)
            # a programme on offer, and one of its seasons
            if key.startswith("os"):
                import pd_torrents
                offer = re.match(r"^(os[0-9a-f]{10})-s(\d+)$", key)
                rows = (pd_torrents.offered_episodes(offer.group(1), int(offer.group(2)))
                        if offer else pd_torrents.offered_seasons(key))
                return {"size": len(rows), "Metadata": rows}
            extras = re.match(r"^([0-9a-f]{12})-extras$", key)
            if extras:                                   # a title's extras, as episodes
                out = self._extras_listing(con, extras.group(1))
                return {"size": len(out), "Metadata": out}
            if is_title(key):
                # a film's children are its extras; a series goes on to its seasons
                kind = con.execute("SELECT type FROM item WHERE id=?", (key,)).fetchone()
                if kind and kind["type"] == "movie":
                    out = self._extras_listing(con, key)
                    return {"size": len(out), "Metadata": out}
            season = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
            if season:                                   # a season: its episodes
                rows = con.execute("""SELECT * FROM episode WHERE item_id=? AND season=?
                                      ORDER BY number""",
                                   (season.group(1), int(season.group(2)))).fetchall()
                # and the ones a pack can give that this machine has not got. A
                # programme is listed whole whether or not its files are here, so
                # without this the missing half of a season is simply not there.
                #
                # Not got means no file, not no row. An episode whose file has gone
                # keeps its row, and counted as here it was listed with nothing to
                # play and nothing to fetch while a pack carrying it stood by: a
                # season replaced by a better pack was fourteen dead rows. The pack's
                # entry stands in its place.
                show = con.execute("SELECT title FROM item WHERE id=?",
                                   (season.group(1),)).fetchone()
                filed = {int(r["number"] or 0) for r in con.execute(
                    "SELECT DISTINCT e.number FROM episode e JOIN file f ON f.episode_id = e.id "
                    "WHERE e.item_id=? AND e.season=?",
                    (season.group(1), int(season.group(2))))}
                given = self._offered_episodes_of(show["title"] if show else "",
                                                  int(season.group(2)), filed)
                coming = {int(e.get("index") or 0) for e in given}
                out = [self._episode(con, r, brief=True) for r in rows
                       if int(r["number"] or 0) in filed or int(r["number"] or 0) not in coming]
                out += given
                # and what the catalogue knows of this season that nothing here has:
                # greyed, with when it airs, so a season being waited on reads as
                # unfinished rather than finished
                item = con.execute("SELECT id, tmdb_id, poster FROM item WHERE id=?",
                                   (season.group(1),)).fetchone()
                if item and item["tmdb_id"]:
                    number = int(season.group(2))
                    said = self._catalogue("/tv/%d/season/%d" % (int(item["tmdb_id"]), number),
                                           self._season_airs, (item["tmdb_id"], number))
                    have = {int(e.get("index") or 0) for e in out}
                    # what the tracker holds of the ones missing here, early releases
                    # before their air date included; and asked about behind this page,
                    # so the next open has what has been posted since
                    title = show["title"] if show else ""
                    try:
                        import pd_tracker, threading
                        onit = pd_tracker.episodes_of(title, number)
                        if any(int(ep.get("episode_number") or 0) not in have
                               for ep in said.get("episodes") or []):
                            threading.Thread(target=pd_tracker.ask_for,
                                             args=("%s S%02d" % (title, number), 0),
                                             daemon=True).start()
                    except Exception:
                        onit = {}
                    import datetime
                    today = datetime.date.today().isoformat()
                    for ep in said.get("episodes") or []:
                        n = int(ep.get("episode_number") or 0)
                        if not n or n in have:
                            continue
                        many = len(onit.get(n) or [])
                        early = str(ep.get("air_date") or "9999") > today
                        aired = str(ep.get("air_date") or "9999") < today
                        out.append({
                            "ratingKey": "upnext-%s-s%02de%02d" % (item["id"], number, n),
                            "type": "episode", "upcoming": True,
                            # out already, and nowhere to get it from: not upcoming,
                            # missing - the band says so rather than Upcoming
                            "missing": aired and not many,
                            # on the tracker: pressed, it lists the releases to download
                            "fetchable": many > 0,
                            "airs": (("Out early - on the tracker" if early
                                      else "On the tracker - press to download")
                                     if many else self._air_words(ep.get("air_date"))),
                            "airDate": str(ep.get("air_date") or ""),
                            "airsToday": str(ep.get("air_date") or "") == today,
                            "title": ep.get("name") or "Episode %d" % n,
                            "summary": ep.get("overview") or "",
                            "index": n, "parentIndex": number,
                            "parentRatingKey": "%s-s%d" % (item["id"], number),
                            "grandparentRatingKey": item["id"], "grandparentKey": item["id"],
                            "thumb": ("/art/%s/poster" % item["id"]) if item["poster"] else None})
                out.sort(key=lambda e: int(e.get("index") or 0))
                # on a season's page each episode wears that season's poster
                held = con.execute("SELECT poster FROM item WHERE id=?",
                                   (season.group(1),)).fetchone()
                art = self._season_thumb(season.group(1), int(season.group(2)),
                                         held and held["poster"])
                if art:
                    for e in out:
                        e["thumb"] = e["parentThumb"] = art
                return {"size": len(out), "Metadata": out}
            if is_title(key):                            # a show: its seasons
                show = con.execute("SELECT * FROM item WHERE id=?", (str(key),)).fetchone()
                rows = con.execute("""SELECT season, COUNT(*) c FROM episode WHERE item_id=?
                                      GROUP BY season ORDER BY season""", (str(key),)).fetchall()
                out = {"size": len(rows), "Metadata": [{
                    "ratingKey": "%s-s%d" % (key, r["season"]), "type": "season",
                    "viewedLeafCount": sum(
                        1 for e in con.execute(
                            "SELECT id FROM episode WHERE item_id=? AND season=?",
                            (str(key), r["season"])).fetchall()
                        if self._watched(con, str(e["id"]))),
                    "title": "Season %d" % r["season"], "index": r["season"],
                    # the episodes here and those a pack can give: the season's page
                    # lists both, and its count said only what was on disk
                    "leafCount": r["c"] + len(self._offered_episodes_of(
                        show["title"] if show else "", r["season"],
                        self._numbers_here(con, key, r["season"]))),
                    # the year the season first aired, not the programme's first year
                    "originallyAvailableAt": self._season_aired(
                        con, key, show["title"] if show else "", r["season"]),
                    "year": int(self._season_aired(
                        con, key, show["title"] if show else "", r["season"])[:4] or 0) or None,
                    "parentRatingKey": key,
                    # New on the season as on the Recently added shelf: the app shows
                    # the seasons first, and the episode's own mark was a level down
                    "fresh": self._fresh_unseen(con, key, r["season"]),
                    "thumb": self._season_thumb(key, r["season"], show and show["poster"]),
                } for r in rows]}
                # and a season this machine has none of, which a pack can give whole
                mine = {int(r["season"]) for r in rows}
                for extra in self._offered_seasons_of(
                        show["title"] if show else "", mine):
                    out["Metadata"].append(dict(
                        extra, parentRatingKey=key,
                        ratingKey="%s-s%d" % (key, int(extra.get("index") or 0)),
                        thumb=(self._season_thumb(key, int(extra.get("index") or 0),
                                                  show and show["poster"])
                               or extra.get("thumb"))))
                if show and show["tmdb_id"]:
                    said = self._catalogue("/tv/%d" % int(show["tmdb_id"]), self._airs,
                                           show["tmdb_id"])
                    have = {int(x.get("index") or 0) for x in out["Metadata"]}
                    for one in said.get("seasons") or []:
                        n = int(one.get("season_number") or 0)
                        if n <= 0 or n in have:
                            continue
                        out["Metadata"].append({
                            "ratingKey": "%s-s%d" % (key, n), "type": "season",
                            "title": "Season %d" % n, "index": n, "parentRatingKey": key,
                            "leafCount": int(one.get("episode_count") or 0),
                            "viewedLeafCount": 0, "upcoming": True,
                            "airs": self._air_words(one.get("air_date")),
                            "thumb": self._season_thumb(key, n, show["poster"])})
                out["Metadata"].sort(key=lambda s: int(s.get("index") or 0))
                # and the extras, last, as a card of their own. Numbered -1 so nothing
                # counting seasons upward runs on into them after the last episode.
                many = len(self._extras_of(con, key))
                if many:
                    out["Metadata"].append({
                        "ratingKey": "%s-extras" % key, "type": "season",
                        "title": "Extras", "index": -1, "extras": True,
                        "leafCount": many, "viewedLeafCount": 0, "parentRatingKey": key,
                        "thumb": "/art/%s/extras" % key})
                out["size"] = len(out["Metadata"])
                return out
            return None

        if path == "/hubs/search":
            words = (one("query") or "").strip()
            # "s4e9" on its own means that place in every series; with a name in front
            # of it, that place in the series that answers to the name.
            place, rest = episode_code(words)
            # A code and nothing else would leave "%%" behind, which matches the whole
            # library, so the words are only searched for when there are some.
            spelt = rest if place else words
            term = "%" + spelt + "%"
            # titles compared as they are spelt in the search box: "dont" finds "Don't"
            con.create_function("bare", 1, bare, deterministic=True)
            typed = bare(spelt)
            bterm = "%" + typed + "%"
            nterm = "%" + typed.replace(" ", "") + "%"
            movies = shows = []
            # "4k" is not in any title, but it is what somebody means when they type
            # it: everything held in a file that tall, films and programmes alike
            if spelt.strip().lower() in ("4k", "2160p", "uhd", "4k hdr"):
                tall = self._tall(con)
                big = [k for k, h in tall.items() if h >= self.UHD]
                if big:
                    marks = ",".join("?" * len(big))
                    movies = con.execute(
                        "SELECT * FROM item WHERE type='movie' AND " + NOT_EXTRA +
                        " AND id IN (%s) "
                        "ORDER BY sort_title LIMIT 60" % marks, big).fetchall()
                    shows = con.execute(
                        "SELECT * FROM item WHERE type='show' AND id IN (%s) "
                        "ORDER BY sort_title LIMIT 60" % marks, big).fetchall()
                spelt = ""                      # the words are spent
            # an episode is not searched for by picture: the programme it belongs to
            # is what the answer names, and every episode of it would be the list
            if spelt:
                # the title itself first, then titles starting with the words, then
                # a word of the title, then anywhere: "v" matched every title with a
                # v in it and the one called V was past the forty kept
                low = typed
                near = ("ORDER BY bare(title) = ? DESC, bare(title) LIKE ? DESC, "
                        "(' ' || bare(title)) LIKE ? DESC, sort_title LIMIT 40")
                ranks = (low, low + "%", "% " + low + "%")
                matches = ("(bare(title) LIKE ? OR "
                           "REPLACE(bare(title), ' ', '') LIKE ?)")
                movies = con.execute(
                    "SELECT * FROM item WHERE type='movie' AND " + NOT_EXTRA +
                    " AND " + matches + " " + near,
                    (bterm, nterm) + ranks).fetchall()
                shows = con.execute(
                    "SELECT * FROM item WHERE type='show' AND " + matches + " " + near,
                    (bterm, nterm) + ranks).fetchall()
                # and by who is in it: "jet li" is a person, not a title
                if len(spelt.strip()) >= 3:
                    named_here = {r["id"] for r in list(movies) + list(shows)}
                    acted = con.execute(
                        "SELECT i.* FROM item i WHERE i." + NOT_EXTRA + " AND i.id IN "
                        "(SELECT DISTINCT item_id FROM credit WHERE name LIKE ?) "
                        "ORDER BY i.sort_title LIMIT 60", (term,)).fetchall()
                    movies = list(movies) + [r for r in acted if r["type"] == "movie"
                                             and r["id"] not in named_here]
                    shows = list(shows) + [r for r in acted if r["type"] == "show"
                                           and r["id"] not in named_here]
            # Four digits are a year as well as a possible title, and a word may be
            # a category. Both are answered, on shelves of their own: "1917" finds the
            # film under Films and everything from 1917 under its own heading, rather
            # than one instead of the other or the two mixed together.
            named = {r["id"] for r in list(movies) + list(shows)}
            dated = genred = []
            year_name = genre_name = ""
            if re.match(r"^(19|20)\d{2}$", spelt.strip()):
                year_name = spelt.strip()
                # a programme is of the year it began; an episode broadcast that year
                # belongs to a series somebody would look for by name instead
                dated = [r for r in con.execute(
                    "SELECT * FROM item WHERE year=? AND " + NOT_EXTRA +
                    " ORDER BY type, sort_title "
                    "LIMIT 80", (int(year_name),)).fetchall()
                    if r["id"] not in named]
            else:
                genre_name = self.genre_named(con, spelt)
                if genre_name:
                    genred = [r for r in con.execute(
                        "SELECT * FROM item WHERE genres <> '' AND " + NOT_EXTRA + " "
                        "ORDER BY type, sort_title").fetchall()
                        if r["id"] not in named and genre_name.lower() in
                        [g.strip().lower() for g in (r["genres"] or "").split(",")]][:80]
            if place and spelt:
                eps = con.execute(
                    """SELECT e.* FROM episode e JOIN item i ON i.id = e.item_id
                       WHERE e.season=? AND e.number=? AND i.title LIKE ?
                       ORDER BY i.sort_title LIMIT 40""",
                    (place[0], place[1], term)).fetchall()
            elif place:
                eps = con.execute(
                    """SELECT e.* FROM episode e JOIN item i ON i.id = e.item_id
                       WHERE e.season=? AND e.number=?
                       ORDER BY i.sort_title LIMIT 40""", place).fetchall()
            elif not spelt:
                eps = []
            else:
                eps = con.execute(
                    """SELECT e.* FROM episode e WHERE e.title LIKE ?
                       ORDER BY e.item_id, e.season, e.number LIMIT 40""",
                    (term,)).fetchall()
            hubs = []
            if eps:
                hubs.append({"type": "episode",
                             "title": ("Season %d, episode %d" % place) if place
                                      else "Episodes",
                             "Metadata": [self._episode(con, r, brief=True) for r in eps]})
            if movies:
                hubs.append({"type": "movie", "title": "Films",
                             "Metadata": [self._movie(con, r, brief=True) for r in movies]})
            if shows:
                hubs.append({"type": "show", "title": "TV shows",
                             "Metadata": [self._show(con, r) for r in shows]})
            # what was asked for first: an episode code puts episodes at the top, words
            # put the titles that carry them there
            if not place:
                hubs.sort(key=lambda h: ("movie", "show", "episode").index(h["type"]))
            # films on offer from a torrent pack, among what is already here
            if spelt and len(spelt.strip()) >= 2:
                try:
                    import pd_torrents
                    low = spelt.strip().lower()
                    # by title, and by anybody in the cast the index has for them
                    acting = {str(r["item_id"]) for r in con.execute(
                        "SELECT DISTINCT item_id FROM credit WHERE name LIKE ? "
                        "AND item_id LIKE 'o%'", (term,)).fetchall()}                         if len(low) >= 3 else set()
                    found = [o for o in pd_torrents.searchable(spelt)
                             if bare_in(spelt, o.get("title"))
                             or str(o.get("ratingKey")) in acting][:60]
                except Exception:
                    found = []
                if found:
                    # among the films, greyed, rather than on a shelf of their own: the list
                    # is sorted and filtered as one
                    films = next((h for h in hubs if h["type"] == "movie"), None)
                    if films:
                        films["Metadata"].extend(found)
                    else:
                        hubs.insert(len(hubs) if place else 0,
                                    {"type": "movie", "title": "Films", "Metadata": found})
            # series that are pack links only, among the programmes, best match
            # first; the library's own programme of the same name wins
            if spelt:
                try:
                    import pd_torrents
                    low = bare(spelt)
                    def fit(t):
                        t = bare(t)
                        return (0 if t == low else 1 if t.startswith(low)
                                else 2 if (" " + low) in (" " + t) else 3)
                    linked = sorted([s for s in pd_torrents.offered_shows()
                                     if bare_in(spelt, s.get("title"))],
                                    key=lambda s: fit(s.get("title") or ""))[:20]
                except Exception:
                    linked = []
                tv = next((h for h in hubs if h["type"] == "show"), None)
                if linked and tv:
                    held = {str(x.get("title") or "").strip().lower()
                            for x in tv["Metadata"]}
                    linked = [s for s in linked
                              if str(s.get("title") or "").strip().lower() not in held]
                if linked:
                    if tv:
                        # in order of fit across both, so a one-letter title stays ahead of a longer one
                        tv["Metadata"] = sorted(
                            tv["Metadata"] + linked,
                            key=lambda s: fit(str(s.get("title") or "")))
                    else:
                        at = next((n + 1 for n, h in enumerate(hubs)
                                   if h["type"] == "movie"), 0)
                        hubs.insert(at, {"type": "show", "title": "TV shows",
                                         "Metadata": linked})
            # and what is new on streaming, which this house has none of. A search
            # that cannot find one of those answers "nothing" about a title that is
            # one press from being asked for.
            if spelt and len(spelt.strip()) >= 2:
                try:
                    import pd_streaming
                    low = spelt.strip().lower()
                    # not the ones this house holds: those came back above as their
                    # own entry, with a Play button, and a second card for the same
                    # film with a Request band across it is the same film twice
                    fresh = [pd_streaming.item(r) for r in pd_streaming.read()
                             if not r.get("here")
                             and bare_in(spelt, r.get("title"))][:20]
                except Exception:
                    fresh = []
                films = next((h for h in hubs if h["type"] == "movie"), None)
                if fresh and films:
                    # One card per film, and the nearest thing to hand wins: a film
                    # already here is played, one on a pack is fetched, and asking is
                    # the last resort. Held titles were left out above; this is the
                    # other half of it - a film a pack can fetch should not also be
                    # offered as something to ask somebody for.
                    said = lambda x: str(x.get("title") or "").strip().lower()
                    already = {said(x) for x in films["Metadata"]}
                    fresh = [x for x in fresh if said(x) not in already]
                if fresh:
                    if films:
                        films["Metadata"].extend(fresh)
                    else:
                        # where the films hub would have gone: the sort above has
                        # already run, so appending puts it under the episodes
                        hubs.insert(len(hubs) if place else 0,
                                    {"type": "movie", "title": "Films",
                                     "Metadata": fresh})
            # Names are searched for as well as titles: a word that is nobody's film
            # is often somebody's name, and the row of faces is one press from
            # everything held with them in it. Only people with something on the
            # shelves here - the same rule the person page reads by, so pressing a
            # face never lands on an empty page.
            if spelt and len(spelt.strip()) >= 2:
                people = con.execute(
                    """SELECT MAX(c.person) AS person, MIN(c.name) AS name,
                              MIN(c.ord) AS ord, MAX(c.profile) AS profile,
                              COUNT(DISTINCT i.id) AS held
                         FROM credit c JOIN item i ON i.id = c.item_id
                        WHERE c.name LIKE ?
                          AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = i.id)
                        GROUP BY LOWER(c.name)
                        ORDER BY held DESC, ord ASC LIMIT 20""", (term,)).fetchall()
                if people:
                    hubs.append({"type": "person", "title": "Actors", "Metadata": [
                        {"type": "person", "id": r["person"], "tag": r["name"],
                         "held": r["held"],
                         "thumb": ("/art/person/%d" % r["person"]) if r["profile"] else None}
                        for r in people]})
                    # The whole name typed: everything they are in, as pressing the name
                    # finds it - the films here, and the packs and new releases the
                    # catalogue says they are in - rather than only the ones whose cast
                    # has been read so far.
                    top = people[0]
                    if str(top["name"] or "").strip().lower() == spelt.strip().lower():
                        more = self._person_films(int(top["person"] or 0), top["name"])
                        films = next((h for h in hubs if h["type"] == "movie"), None)
                        have = {str(x.get("ratingKey")) for x in (films or {}).get("Metadata", [])}
                        extra = [x for x in more if str(x.get("ratingKey")) not in have]
                        if extra:
                            if films:
                                films["Metadata"].extend(extra)
                            else:
                                hubs.insert(0, {"type": "movie", "title": "Films",
                                                "Metadata": extra})
            # and under those, what the word means rather than what it spells
            listed = lambda rows: [self._movie(con, r, brief=True) if r["type"] == "movie"
                                   else self._show(con, r) for r in rows]
            if genred:
                hubs.append({"type": "genre", "title": genre_name,
                             "Metadata": listed(genred)})
            if dated:
                hubs.append({"type": "year", "title": "From " + year_name,
                             "Metadata": listed(dated)})
            # Nothing spelt that way: the titles a letter or two off, every word of the
            # search near a word of the title. a name one letter short found nothing while
            # the film sat in two packs.
            if not hubs and spelt and len(spelt.strip()) >= 4 and not place:
                hubs = self._near_misses(con, spelt, listed)
            return {"size": len(hubs), "Hub": hubs}

        m = re.match(r"^/art/person/(\d+)$", path)
        if m:
            # a face beside a name. Kept like every other picture: fetched once from
            # TMDB and served from here afterwards.
            local = self.lib.artwork(self.lib.face_of(int(m.group(1))), "w185")
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())

        m = re.match(r"^/art/studio/([A-Za-z0-9_-]+\.(png|jpg|jpeg|svg))$", path)
        if m:
            # a studio's mark, wider than it is tall, kept like every other picture
            local = self.lib.artwork("/" + m.group(1), "w185")
            if not local:
                return None
            with open(local, "rb") as f:
                return ({"png": "image/png", "svg": "image/svg+xml"}.get(m.group(2), "image/jpeg"),
                        f.read())

        m = re.match(r"^/art/provider/([A-Za-z0-9_-]+\.(png|jpg|jpeg))$", path)
        if m:
            # a streaming service's mark, kept like every other picture
            local = self.lib.artwork("/" + m.group(1), "w92")
            if not local:
                return None
            with open(local, "rb") as f:
                return ("image/png" if m.group(2) == "png" else "image/jpeg", f.read())

        m = re.match(r"^/art/(rt[0-9a-f]{10})/(poster|backdrop)$", path)
        if m:
            import pd_streaming
            local = self.lib.artwork(pd_streaming.art_of(m.group(1), m.group(2)),
                                     "w500" if m.group(2) == "poster" else "w780")
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())
        m = re.match(r"^/art/tv(\d+)/poster$", path)
        if m:
            # a programme off the list of what is being watched, not held yet: its
            # poster is the catalogue's, kept like every other picture
            try:
                with open(os.path.join(self.lib.root, "top_shows.json"),
                          encoding="utf-8") as f:
                    shows = json.load(f).get("shows") or []
            except (OSError, ValueError):
                shows = []
            show = next((x for x in shows if str(x.get("tmdb")) == m.group(1)), None)
            local = self.lib.artwork(show.get("poster"), "w500") if show else None
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())
        m = re.match(r"^/art/(o[0-9a-f]{12})/(poster|backdrop)$", path)
        if m:
            # a film on offer from a torrent pack: its picture from TMDB, kept like any
            import pd_torrents
            local = self.lib.artwork(pd_torrents.art_of(m.group(1), m.group(2)),
                                     "w500" if m.group(2) == "poster" else "w780")
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())
        m = re.match(r"^/art/version/(\d+)$", path)
        if m:
            # the cover that came with one copy of a film, for when it is the one chosen
            row = con.execute("SELECT path FROM file WHERE id=?",
                              (int(m.group(1)),)).fetchone()
            local = version_art(row["path"]) if row else ""
            if not local:
                return None
            # made smaller in the server's own cache: beside the original would leave
            # a picture in the download folder for the next look to find
            want = max(64, min(int(one("w", "0") or 500), 1000))
            import hashlib
            kept = os.path.join(self.lib.root, "cache", "versionart", "%s.w%d.jpg" % (
                hashlib.sha1(local.encode("utf-8")).hexdigest()[:16], want))
            try:
                if not (os.path.exists(kept) and
                        os.path.getmtime(kept) >= os.path.getmtime(local)):
                    from PIL import Image
                    os.makedirs(os.path.dirname(kept), exist_ok=True)
                    with Image.open(local) as im:
                        im = im.convert("RGB")
                        # a whole case wrap - back, spine, front - is wider than it is
                        # tall: the front is its right-hand half
                        w, h = im.size
                        if w > h * 1.25:
                            im = im.crop((int(w * 0.525), 0, w, h))
                        im.thumbnail((want, want * 2))
                        im.save(kept, "JPEG", quality=85)
                local = kept
            except Exception:
                pass
            with open(local, "rb") as f:
                return ("image/png" if local.lower().endswith(".png") else "image/jpeg",
                        f.read())
        m = re.match(r"^/art/([0-9a-f]{12})/extras$", path)
        if m:
            made = self._extras_poster(con, m.group(1), int(one("w", "0") or 0))
            if not made:
                return None
            with open(made, "rb") as f:
                return ("image/jpeg", f.read())

        m = re.match(r"^/art/([0-9a-f]{12})/season/(\d+)$", path)
        if m:
            try:
                art = self._season_poster(con, m.group(1), int(m.group(2)))
            except Exception:
                art = None
            if not art:
                row = con.execute("SELECT poster FROM item WHERE id=?",
                                  (m.group(1),)).fetchone()
                art = row["poster"] if row else None
            local = self.lib.artwork(art, "w500")
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())
        m = re.match(r"^/art/([0-9a-f]{12})/(poster|backdrop)$", path)
        if m:
            row = con.execute("SELECT poster, backdrop FROM item WHERE id=?",
                              (m.group(1),)).fetchone()
            tmdb_path = row[m.group(2)] if row else None
            local = self.lib.artwork(tmdb_path, "w500" if m.group(2) == "poster" else "w780")
            if not local:
                return None
            want = int(one("w", "0") or 0)
            if want:
                sized = self._resized(local, want)
                if sized:
                    local = sized
            with open(local, "rb") as f:
                return ("image/jpeg", f.read())

        if path in ("/:/scrobble", "/:/unscrobble"):
            # the names the clients already use for it, kept so nothing needs a new verb
            key = one("key") or one("ratingKey")
            if key:
                self._set_watched(con, key, path.endswith("/:/scrobble"))
                if path.endswith("/:/scrobble") and self.shelf_forget:
                    self.shelf_forget(key)
            return {"size": 0}

        if path == "/:/timeline":                        # the client reports progress here
            key = one("ratingKey")
            pos = float(one("time", "0") or 0) / 1000.0
            dur = float(one("duration", "0") or 0) / 1000.0
            # Near the end is finished, whoever was playing it and from wherever. The
            # shelf's own rule only ran for a playing that named the shelf, so an
            # episode watched from its page stayed on the shelf as half-watched.
            if key and dur and self.watched_through(pos, dur, key) and self.shelf_forget:
                self.shelf_forget(key)
            # half way through, which is where somebody is watching rather than sampling.
            # Not in a shuffle: what comes after a drawn episode is the next draw, which
            # the round fetches for itself - this fetched ten in season order for it.
            if (key and dur and pos / dur >= 0.5 and self.half_way
                    and one("casual", "") not in ("1", "true", "yes")):
                self.half_way(key)
            if key and one("state", "playing") != "stopped":
                self.log_watch(con, key, pos, dur, one("device", "") or "",
                               one("client", ""),
                               one("casual", "") in ("1", "true", "yes"),
                               one("state", "playing"))
            # What the client says is what is recorded. A playing that is casual
            # says so in every report, including the last one; guessing on the
            # server's side would hide the truth from the watch log and from Now
            # playing, which are the two places it has to be right.
            if key and one("casual", "") in ("1", "true", "yes"):
                # Watched state is not touched: nothing is marked seen and no series is
                # moved on. The place is kept in two places answering two questions -
                # the shuffle's own notes, which resume Casual play, and a progress row
                # marked casual, which resumes the film if it is opened by name. The
                # mark keeps it off Continue watching and out of the cache queue.
                shelf = one("shelf", "")
                if shelf and self.shelf_note:
                    # a shelf keeps its own place: it is what Carry on reads, and what
                    # the one row on Continue watching is built out of
                    self.shelf_note(shelf, key, pos, dur)
                if self.watched_through(pos, dur, key) or pos < 30:
                    # the rule the shuffle's own notes keep: near the end is finished,
                    # and the first half minute is not a place worth coming back to.
                    # Only a casual row - a film somebody chose is not touched here.
                    con.execute("DELETE FROM progress WHERE who=? AND key=? "
                                "AND COALESCE(casual, 0) = 1", (self.who, key))
                else:
                    con.execute(
                        """INSERT INTO progress (key, position, duration, updated,
                                                 who, casual)
                           VALUES (?,?,?,?,?,1)
                           ON CONFLICT(who, key) DO UPDATE SET
                           position=excluded.position, duration=excluded.duration,
                           updated=excluded.updated, casual=1
                           WHERE COALESCE(progress.marked, 0) = 0""",
                        (key, pos, dur, int(time.time()), self.who))
                con.commit()
                self.note_playing(key, pos, dur, one("state", "playing"),
                                  one("device", "") or "", one("client", ""),
                                  one("client", ""), casual=True, shelf=shelf,
                                  info=one("info", ""))
                return {"size": 0}
            if key:
                # A "stopped" on its own does not put something in Continue watching.
                # A player that is opened and closed again reports the place it would
                # have resumed from and nothing else - no "playing" ever arrives - and
                # that was enough to leave a film sitting on the shelf as though it
                # had been watched. Once there is a row, stopping updates it as usual.
                stopping = one("state", "playing") == "stopped"
                known = con.execute(
                    "SELECT 1 FROM progress WHERE who=? AND key=?",
                    (self.who, key)).fetchone()
                # A report that does not move the picture records nothing. A player
                # left paused sends one every few seconds and each rewrote the row,
                # so a programme put aside was back on Continue watching before the
                # press had finished - the shelf keeps something aside only until
                # something newer happens to it, and read the heartbeat as that.
                # Stopping still writes: it is the last place, and for a player that
                # is opened and closed again it is the only one that ever arrives.
                still = not stopping and self.same_place(
                    key, one("device", "") or "", pos)
                if not (stopping and not known) and not still:
                    self.note_uncasual(con, key, one("device", "") or "",
                                       one("client", ""), one("state", "playing"))
                    # casual back to nought: whatever the shuffle left there,
                    # somebody has chosen this one and it belongs on the shelf
                    # A mark made earlier gives way to a viewing that is really under
                    # way: a minute after the mark, past the first half minute, short of
                    # the watched line. Left as it was, a film marked watched stayed off
                    # Continue watching however far into it somebody got the next time.
                    # The minute keeps a player that is closing as the mark is made from
                    # taking it straight back.
                    con.execute("""INSERT INTO progress (key, position, duration,
                                                         updated, who, casual, furthest)
                                   VALUES (?,?,?,?,?,0,?)
                                   ON CONFLICT(who, key) DO UPDATE SET
                                   furthest=MAX(COALESCE(progress.furthest, 0),
                                                excluded.position),
                                   marked=CASE
                                       WHEN COALESCE(progress.marked, 0) = 1
                                        AND excluded.updated - progress.updated > 60
                                        AND excluded.position > 30
                                        AND (excluded.duration <= 0
                                             OR excluded.position <
                                                MAX(excluded.duration * 0.85,
                                                    CASE WHEN excluded.duration > 3600
                                                    THEN MIN(excluded.duration * 0.90,
                                                             excluded.duration - 600)
                                                    ELSE MIN(excluded.duration * 0.95,
                                                             excluded.duration - 180)
                                                    END))
                                       THEN 0 ELSE progress.marked END,
                                   position=excluded.position, duration=excluded.duration,
                                   updated=excluded.updated, casual=0""",
                                (key, pos, dur, int(time.time()), self.who, pos))
                    con.commit()
                # the same post tells the panel what is on screen: state and device are
                # optional, so an older client simply reads as "playing"
                self.note_playing(key, pos, dur, one("state", "playing"),
                                  one("device", "") or "", one("client", ""),
                                  one("client", ""), info=one("info", ""))
            return {"size": 0}

        return None

    @staticmethod
    def _resized(path, width):
        """A narrower copy of one poster, made once and kept beside the original.

        The panel can only halve, quarter or eighth an image, never enlarge it, so a
        500-wide poster lands at 250 in a 320-wide slot. Handing it the width it asks
        for fills the slot properly, and the work happens once per poster.
        """
        width = max(64, min(width, 1000))
        out = "%s.w%d.jpg" % (os.path.splitext(path)[0], width)
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(path):
            return out
        try:
            from PIL import Image
            with Image.open(path) as im:
                if im.width <= width:
                    return path                  # never enlarge: it would only blur
                height = round(im.height * width / im.width)
                im.convert("RGB").resize((width, height), Image.LANCZOS).save(
                    out, "JPEG", quality=88, optimize=True)
            return out
        except Exception:
            return None                          # Pillow missing or a broken file

    def note_uncasual(self, con, key, device, client, state):
        """Write down a report that arrives without the casual flag over a casual one.

        Continue watching filled up with things that were only put on, and the watch
        log said casual throughout: the report that wrote the progress row left no
        trace of itself. This names it - which screen, which client, which build.
        """
        try:
            row = con.execute("""SELECT app, client, casual FROM watchlog
                                 WHERE who=? AND key=? AND casual=1 AND origin IS NULL
                                 AND updated > ? ORDER BY updated DESC LIMIT 1""",
                              (self.who, str(key), int(time.time()) - 900)).fetchone()
            if not row:
                return
            with open(os.path.join(DATA, "debug.log"), "a", encoding="utf-8") as f:
                f.write("%s casual leak key=%s state=%s device=%s client=%s app=%s "
                        "(casual viewing open, logged by %s)%s" % (
                            time.strftime("%H:%M:%S"), key, state, device, client,
                            self.app_now or "", row["app"] or "", chr(10)))
        except Exception:
            pass                       # a note is never worth failing a report over

    def log_watch(self, con, key, position, duration, device, client, casual,
                  state="playing"):
        """Record this viewing, or extend the row it belongs to.

        Reports arrive every few seconds; a gap of more than fifteen minutes for the
        same viewer, title and device counts as a new viewing. A paused player keeps
        reporting: those reports move the place but not the clock, so a film left
        paused adds nothing to watch time, and a pause longer than the gap ends the
        viewing where it stopped.
        """
        if not key:
            return
        now = int(time.time())
        paused = state == "paused"
        row = con.execute("""SELECT id, updated, casual FROM watchlog
                             WHERE who=? AND key=? AND device=? AND origin IS NULL
                             ORDER BY updated DESC LIMIT 1""",
                          (self.who, str(key), device or "")).fetchone()
        # A report that disagrees about how this is being watched belongs to a
        # different viewing: extending the row would keep the old answer and the log
        # would say casual while a progress row was being written underneath it.
        if row and int(row["casual"] or 0) != (1 if casual else 0):
            row = None
        if row and now - int(row["updated"] or 0) < 900:
            if paused:
                con.execute("UPDATE watchlog SET position=?, duration=? WHERE id=?",
                            (position, duration, row["id"]))
            else:
                con.execute("""UPDATE watchlog SET updated=?, position=?, duration=?,
                               app=? WHERE id=?""",
                            (now, position, duration, self.app_now or "", row["id"]))
        elif paused:
            return                     # a viewing does not begin while paused
        else:
            title, subtitle, _ = self.now_playing_fields(key)
            whole = " - ".join(x for x in (title, subtitle) if x)
            con.execute("""INSERT INTO watchlog
                           (who, key, title, device, client, started, updated,
                            position, duration, casual, app)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (self.who, str(key), whole, device or "", client or "",
                         now, now, position, duration, 1 if casual else 0,
                         self.app_now or ""))
        con.commit()

    def same_place(self, key, device, position):
        """Whether this report says exactly what the last one from that player said.

        A paused player sends a report every few seconds, all of them the same second
        of the same episode. Nothing has been watched between two of them.
        """
        before = NOW.get((self.who or "") + chr(0) + (device or "player"))
        if not (before and str(before.get("key") or "") == str(key)):
            return False
        try:
            return abs(float(before.get("position") or -1) - float(position)) < 1.0
        except (TypeError, ValueError):
            return False

    def note_playing(self, key, position, duration, state, device, client="",
                     kind="", casual=False, shelf="", info=""):
        """Remember what one client is doing.

        Keyed by the viewer and the device together. Under the device alone, two
        televisions that call themselves the same thing - and every Google TV
        Streamer does - wrote over each other every five seconds, so of two people
        watching only whichever reported last had a position on Now playing, and the
        two rows took turns showing one.
        """
        who = (self.who or "") + "\u0000" + (device or "player")
        if state == "stopped":
            NOW.pop(who, None)
            return
        title, subtitle, poster = self.now_playing_fields(key)
        if not title:
            return
        # when this viewing began, kept across reports so the list can say "since"
        before = NOW.get(who)
        began = (before or {}).get("began") if (before or {}).get("key") == str(key) \
                else None
        said = state if state in ("playing", "paused", "buffering") else "playing"
        # When the picture last moved. A client that says it is playing and reports
        # the same second for a minute is not playing: a page that came back after
        # the server was replaced said "playing" for as long as it stayed open, and
        # the panel showed a film nobody was watching.
        moved = time.time()
        if (before and before.get("key") == str(key)
                and int(before.get("position") or -1) == int(position)):
            moved = before.get("moved") or moved
            if said == "playing" and time.time() - moved > 45:
                said = "paused"
        NOW[who] = {"state": said, "moved": moved, "info": info or "",
                    "key": str(key), "title": title, "subtitle": subtitle,
                    "poster": poster,
                    "position": int(position), "duration": int(duration),
                    # the name it calls itself, not the key this is filed under:
                    # that key carries the viewer's id, which is the token their
                    # player authenticates with, and Now playing showed it
                    "device": device or "player",
                    # a browser or the app: it changes what a fault means and what
                    # advice is worth giving
                    "client": client or "",
                    # and which build of it, and what sort of thing it is running on
                    "app": self.app_now or "", "kind": kind or "",
                    # Whose it is, and whether they chose it. Putting something on is
                    # not a reason to copy the rest of the series, and the copy queue
                    # is the only thing that reads either of these.
                    "who": self.who, "casual": bool(casual),
                    # which shelf it was drawn from, so the place can be kept there
                    "shelf": str(shelf or ""),
                    "began": began or time.time(),
                    "updated": time.time()}

    @staticmethod
    def playing_now():
        """Every client heard from lately: what it is on, and whether it is running.

        Keyed by the film's title, which is how a stream can be matched to it - the
        stream knows the file it is sending, the client knows what it calls it.
        """
        now = time.time()
        out = {}
        for row in NOW.values():
            # a minute: a report is due every five to eight seconds, so this is several
            # missed in a row rather than one slow moment
            if now - row["updated"] > 60:
                continue
            said = {"state": row["state"], "position": row["position"],
                    "duration": row["duration"], "episode": row.get("subtitle") or "",
                    "client": row.get("client") or "", "device": row.get("device") or "",
                    "title": row.get("title") or "", "began": int(row.get("began") or 0),
                    "key": str(row.get("key") or ""),
                    # which build is playing it, and what sort of thing it is
                    "app": row.get("app") or "", "kind": row.get("kind") or "",
                    # the line the player would show, as it had it at its last report
                    "info": row.get("info") or "",
                    "who": row.get("who") or "",
                    "casual": bool(row.get("casual"))}
            # by key where the client gave one, and by title as well, since an older
            # client says only what it is watching
            if row.get("key"):
                out[str(row["key"])] = said
            out[row["title"]] = said
        return out

    @staticmethod
    def playing_reports():
        """The same, one entry per client rather than one per way of finding it."""
        seen, out = set(), []
        for said in LocalAPI.playing_now().values():
            # the viewer as well as the device: two screens of the same name are two
            # clients, and one of them was dropped here as a repeat of the other
            mark = (said.get("who") or "",
                    said.get("device") or said.get("title"))
            if mark in seen:
                continue
            seen.add(mark)
            out.append(said)
        return out

    @staticmethod
    def _offered_homes(keys):
        """Which programme, season and number each offered episode key belongs to.

        One pass over the packs rather than a lookup per key: a collection of a
        programme nobody holds is six hundred keys, and each of those lookups reads
        every pack.
        """
        want = {str(k) for k in keys}
        if not want:
            return {}
        try:
            import pd_torrents
            out = {}
            for name, holds in pd_torrents._episodes_by_show().items():
                for film, _ in holds:
                    k = str(film.get("key") or "")
                    if k in want:
                        out[k] = (name, int(film.get("season") or 0),
                                  int(film.get("episode") or 0), film)
            return out
        except Exception:
            return {}

    def episode_places(self, con, keys):
        """Where each episode key sits: its programme here, season and number.

        Held and offered alike. An offered episode answers to the programme in the
        library when there is one - the key is made from the name - so the two halves
        of a half-held programme are one programme rather than two.
        """
        out = {}
        ids = [k for k in keys if is_episode(k)]
        if ids:
            marks = ",".join("?" * len(ids))
            for r in con.execute(
                    "SELECT id, item_id, season, number FROM episode "
                    "WHERE id IN (%s)" % marks, ids):
                out[r["id"]] = (str(r["item_id"]), int(r["season"] or 0),
                                int(r["number"] or 0))
        offers = self._offered_homes([k for k in keys if str(k).startswith("o")])
        if offers:
            try:
                import pd_torrents
                mine = {}
                for r in con.execute("SELECT id, title FROM item WHERE type='show'"):
                    mine[pd_torrents.show_key(r["title"] or "")] = str(r["id"])
                for key, (name, season, number, _film) in offers.items():
                    show = pd_torrents.show_key(name)
                    out[key] = (mine.get(show, show), season, number)
            except Exception:
                pass
        return out, offers

    def _by_season(self, con, keys):
        """The same shelf, gathered into seasons.

        Marking a programme now marks each of its episodes, which is right for taking
        one off again and hopeless to look at: two hundred cards where there was one.
        A collection made by filter is worse - six hundred and eighty episodes across
        thirty-six seasons, flat. Episodes of a season are shown as that season, with
        how many of them are on the shelf. Films and lone episodes stand as they are.

        Episodes a pack can give are folded in beside the ones here, into the same
        season: a programme half held and half offered read as two programmes.
        """
        out, seen = [], set()
        places, offers = self.episode_places(con, keys)
        home = {k: (where[0], where[1]) for k, where in places.items()}
        # how many episodes each season holds, so a part-marked season says so
        counted = {}
        for item_id, season in set(home.values()):
            counted[(item_id, season)] = con.execute(
                "SELECT COUNT(*) c FROM episode WHERE item_id=? AND season=?",
                (item_id, season)).fetchone()["c"]
        held = {}
        for key in keys:
            where = home.get(key)
            if where:
                held.setdefault(where, []).append(key)

        def in_order(key):
            return (places.get(key) or ("", 0, 0))[2]

        for key in keys:
            where = home.get(key)
            if where:
                if where in seen:
                    continue
                seen.add(where)
                mine = sorted(held[where], key=in_order)
                if len(mine) == 1:
                    one_card = self.metadata_for(con, mine[0], brief=True)
                    if one_card:
                        out.append(one_card)
                    continue
                # the card wants the same shape the shelves give it: the programme,
                # the season, how many episodes it holds and when it last gained one
                row = con.execute(
                    """SELECT e.item_id, e.season, COUNT(*) episodes,
                              MAX(COALESCE(f.ctime, f.mtime)) added
                       FROM episode e JOIN file f ON f.episode_id=e.id
                       WHERE e.item_id=? AND e.season=?""",
                    where).fetchone()
                card = self._season_card(con, row) if row and row["item_id"] else None
                if not card:
                    # nothing of this season is here: it is a season a pack is
                    # offering. The programme's own poster if it is known here, the
                    # pack's otherwise.
                    film = (offers.get(mine[0]) or (None, 0, 0, {}))[3] or {}
                    show = con.execute("SELECT * FROM item WHERE id=?",
                                       (where[0],)).fetchone()
                    card = {
                        "ratingKey": "%s-s%d" % where, "type": "season",
                        "title": "Season %d" % where[1], "index": where[1],
                        "parentRatingKey": where[0],
                        "grandparentRatingKey": where[0],
                        "grandparentTitle": (show["title"] if show
                                             else (offers.get(mine[0]) or [""])[0]),
                        "parentTitle": (show["title"] if show
                                        else (offers.get(mine[0]) or [""])[0]),
                        "year": (show["year"] if show else film.get("year")) or None,
                        "viewedLeafCount": 0, "viewCount": 0,
                        "thumb": (("/art/%s/poster" % where[0])
                                  if show and show["poster"]
                                  else (("/art/%s/poster" % film.get("key"))
                                        if film.get("poster") else None)),
                        "addedAt": 0,
                    }
                card["ratingKey"] = "%s-s%d" % where
                # offered until one of its episodes is here, which is what decides
                # whether the client draws it as something to download
                if all(str(k).startswith("o") for k in mine):
                    card["offered"] = True
                whole = counted.get(where, 0) + sum(
                    1 for k in mine if str(k).startswith("o"))
                card["leafCount"] = whole or len(mine)
                card["title"] = card.get("title") or "Season %d" % where[1]
                # what is actually on the shelf, when it is not the whole thing
                if len(mine) < (whole or len(mine)):
                    card["shelfCount"] = len(mine)
                # and which episodes it stands for, in order. A season card is a way
                # of reading the shelf; playing one has to play these. Asking the
                # library for the season answers with the programme, so Play opened
                # the show and started nothing.
                card["holds"] = mine
                out.append(card)
                continue
            if is_title(key):
                # marked before a whole programme was kept as its episodes: the shelf
                # still holds the show itself, and it reads as seasons too
                seasons = con.execute(
                    """SELECT e.item_id, e.season, COUNT(*) episodes,
                              MAX(COALESCE(f.ctime, f.mtime)) added
                       FROM episode e JOIN file f ON f.episode_id=e.id
                       WHERE e.item_id=? GROUP BY e.season ORDER BY e.season""",
                    (str(key),)).fetchall()
                if seasons:
                    for row in seasons:
                        card = self._season_card(con, row)
                        if card:
                            out.append(card)
                    continue
            card = self.metadata_for(con, key, brief=True)
            if card:
                out.append(card)
        return out

    def _season_card(self, con, row):
        """One season, as a thing with a poster: what "a series was added" means.

        Dated by the newest file in it, so a series gaining an episode a week comes
        back to the front of the shelf each time instead of arriving all at once.
        """
        show = con.execute("SELECT * FROM item WHERE id=?", (row["item_id"],)).fetchone()
        if not show:
            return None
        key = str(row["item_id"])
        watched = sum(1 for e in con.execute(
            "SELECT id FROM episode WHERE item_id=? AND season=?",
            (row["item_id"], row["season"])).fetchall()
            if self._watched(con, str(e["id"])))
        return {
            "ratingKey": "%s-s%d" % (key, row["season"]), "type": "season",
            "title": "Season %d" % (row["season"] or 0),
            "parentTitle": show["title"],
            # the card shows the programme's name above the season, which is the way
            # round anybody reads it
            "grandparentTitle": show["title"],
            "index": row["season"], "leafCount": row["episodes"],
            "viewedLeafCount": watched,
            "parentRatingKey": key, "grandparentRatingKey": key,
            "addedAt": int(row["added"] or 0),
            "year": show["year"],
            "genres": [g.strip() for g in (show["genres"] or "").split(",") if g.strip()],
            "thumb": self._season_thumb(key, row["season"] or 0, show["poster"]),
            # an episode in it that aired this past week and is here
            "fresh": self._fresh_unseen(con, row["item_id"], row["season"]),
        }

    def learn_audio(self, con, rows):
        """Probe for soundtracks any of these files has not been asked about yet.

        Everything indexed before the library knew about audio tracks has none
        recorded. Re-probing the whole library to find out would take an hour for a
        question nobody has asked; one file, when it is opened, costs nothing anybody
        notices.
        """
        import pd_library
        from pd_gpu import FFMPEG
        ffprobe = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
        for r in rows:
            # never asked, which an empty list also means: it is what a probe
            # that knew nothing about soundtracks wrote for every file it touched
            if r["atracks"] not in (None, "", "[]"):
                continue
            info = pd_library.Library.probe(ffprobe, r["path"])
            tracks = (info or {}).get("atracks") or []
            con.execute("UPDATE file SET atracks=? WHERE id=?",
                        (json.dumps(tracks), r["id"]))
            con.commit()

    def facts_for_file(self, file_id):
        """"1080p HEVC" for a file being read straight from disk."""
        con = self.lib.db()
        try:
            row = con.execute("SELECT height, width, vcodec FROM file WHERE id=?",
                              (int(file_id),)).fetchone()
        except Exception:
            return ""
        finally:
            con.close()
        if not row:
            return ""
        label = resolution_label(row["height"], row["width"])
        return " ".join(x for x in [label, (row["vcodec"] or "").upper()] if x)

    @staticmethod
    def devices():
        """Which device is watching what, as the players last reported it."""
        now = time.time()
        return {v["title"]: v["device"] for v in NOW.values()
                if now - v["updated"] < 60 and v.get("device")}

    @staticmethod
    def now_playing():
        """The one worth showing: whatever is playing, else the freshest thing paused.

        Anything not heard from for twenty seconds is treated as gone - a client that
        crashes or loses the network must not leave a film frozen on the panel.
        """
        now = time.time()
        live = [v for v in NOW.values() if now - v["updated"] < 20]
        if not live:
            return {"state": "stopped"}
        live.sort(key=lambda v: (v["state"] != "playing", -v["updated"]))
        best = dict(live[0])
        best.pop("updated", None)
        best.pop("moved", None)          # bookkeeping, not something to show
        return {k: v for k, v in best.items() if v is not None}

    @staticmethod
    def decades_asked(said):
        """The decades a request names, as the year each one begins.

        Comma-separated, and each read the way people say it: 80 or 1980 both mean the
        eighties. Anything that is not a number is passed over rather than refused - a
        filter nobody can spell is not worth an error page.
        """
        out = []
        for part in str(said or "").split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            first = int(part)
            if first < 100:
                first += 1900 if first >= 30 else 2000
            out.append(first - first % 10)
        return out

    @staticmethod
    def _offered_seasons_of(title, already):
        """Seasons of this programme a pack can give that are not here at all."""
        if not title:
            return []
        try:
            import pd_torrents
            key = pd_torrents.show_key(title)
            return [s for s in pd_torrents.offered_seasons(key)
                    if int(s.get("index") or 0) not in already]
        except Exception:
            return []

    @staticmethod
    def _numbers_here(con, show, season):
        """The episode numbers of one season this machine holds."""
        return {int(r["number"] or 0) for r in con.execute(
            "SELECT number FROM episode WHERE item_id=? AND season=?", (str(show), int(season)))}

    def _season_aired(self, con, show, title, season):
        """The day a season first aired: its earliest episode here or in a pack, or ""."""
        row = con.execute("SELECT MIN(aired) a FROM episode WHERE item_id=? AND season=? "
                          "AND aired IS NOT NULL AND aired <> ''",
                          (str(show), int(season))).fetchone()
        days = [str(row["a"])[:10]] if row and row["a"] else []
        days += [str(e.get("originallyAvailableAt") or "")[:10]
                 for e in self._offered_episodes_of(title, season, set())
                 if e.get("originallyAvailableAt")]
        days = sorted(d for d in days if d)
        return days[0] if days else ""

    @staticmethod
    def _offered_episodes_of(title, season, already):
        """Episodes of one season a pack can give that this machine has not got."""
        if not title:
            return []
        try:
            import pd_torrents
            key = pd_torrents.show_key(title)
            return [e for e in pd_torrents.offered_episodes(key, season)
                    if int(e.get("index") or 0) not in already]
        except Exception:
            return []

    def _near_misses(self, con, spelt, listed):
        """Films and series whose title is the search with a letter or two wrong."""
        if not bare(spelt).strip():
            return []

        def close(title):
            return near(spelt, title)

        rows = con.execute("SELECT * FROM item WHERE type IN ('movie','show') AND " +
                           NOT_EXTRA).fetchall()
        hits = sorted(((close(r["title"]), r) for r in rows), key=lambda x: -x[0])
        hits = [r for score, r in hits if score >= 0.8][:20]
        films = [r for r in hits if r["type"] == "movie"]
        shows = [r for r in hits if r["type"] == "show"]
        out = []
        offers = []
        try:
            import pd_torrents
            offers = sorted((o for o in pd_torrents.searchable(spelt)
                             if close(o.get("title")) >= 0.8),
                            key=lambda o: -close(o.get("title")))[:20]
        except Exception:
            pass
        if films or offers:
            out.append({"type": "movie", "title": "Films", "Metadata": listed(films) + offers})
        if shows:
            out.append({"type": "show", "title": "Series", "Metadata": listed(shows)})
        return out

    @staticmethod
    def _offered_shows_quietly():
        """Programmes a pack can give. Never worth a failure on a shelf."""
        try:
            import pd_torrents
            return pd_torrents.offered_shows() or []
        except Exception:
            return []

    #: What the film shelf stands, unless this viewer says otherwise: everything
    #: there is to watch, held, fetchable or only askable. The same defaults the
    #: server answers with.
    FILMS_SHOW = {"disk": True, "download": True, "request": True}
    #: which of the two scores a new film must have satisfied, per viewer
    METERS = {"audience": True, "critics": True}

    def meters(self):
        """Which scores this viewer wants a new film judged by."""
        said = viewer_settings(self.who).get("meters")
        if not isinstance(said, dict):
            return dict(self.METERS)
        return {k: bool(said.get(k, v)) for k, v in self.METERS.items()}

    def well_thought_of(self, row):
        """Whether a title on the streaming list passes the scores this viewer keeps.

        Either meter vouching for it is enough. Both having to agree meant the
        critics alone decided: a film ninety-one per cent of the audience liked was
        kept off the shelf on a thirty-six from the reviewers, and the newest two
        releases were missing for that reason alone.

        A meter with no number does not block - a film released this week often has
        neither yet - and a film with no numbers at all is shown.
        """
        want = self.meters()
        if not (want.get("audience") or want.get("critics")):
            return True
        import pd_streaming
        bar = getattr(pd_streaming, "LIKED_PERCENT", 60)
        marks = [int(row[field])
                 for name, field in (("audience", "liked"), ("critics", "judged"))
                 if want.get(name) and row.get(field) is not None]
        return (not marks) or max(marks) >= bar

    def films_show(self):
        """Which of the three kinds this viewer wants the film shelf to stand."""
        said = viewer_settings(self.who).get("filmsShow")
        if not isinstance(said, dict):
            return dict(self.FILMS_SHOW)
        return {k: bool(said.get(k, v)) for k, v in self.FILMS_SHOW.items()}

    @staticmethod
    def _offered_quietly():
        """The films on offer from the packs, or none if they cannot be read."""
        try:
            import pd_torrents
            return pd_torrents.offered() or []
        except Exception:
            return []

    @staticmethod
    def _bare(title):
        """A title with nothing in it but its letters and numbers, for comparing."""
        return "".join(c for c in str(title or "").lower() if c.isalnum())

    #: one actor's films as a search found them, kept a few minutes: the search asks
    #: again with every letter typed, and the catalogue once is enough
    _PERSON_FILMS = {}

    def _person_films(self, person, name):
        """Everything one person is in that this house holds or can get, newest first -
        the same list pressing their name opens."""
        mark = "%s|%s" % (person, str(name or "").lower())
        kept = self._PERSON_FILMS.get(mark)
        if kept and time.time() - kept[0] < 600:
            return kept[1]
        found = self.lib.with_person(person, name)
        items = []
        con2 = self.lib.db()
        try:
            for r in found:
                got = self.metadata_for(con2, str(r["id"]), brief=True)
                if got:
                    items.append(got)
        finally:
            con2.close()
        try:
            items = items + self._people_offers(person, name, items, self.films_show())
        except Exception:
            pass
        items.sort(key=lambda m: (int(m.get("year") or 0),
                                  str(m.get("originallyAvailableAt") or "")[:10]),
                   reverse=True)
        if len(self._PERSON_FILMS) > 50:
            self._PERSON_FILMS.clear()
        self._PERSON_FILMS[mark] = (time.time(), items)
        return items

    def _people_offers(self, person, name, already, show):
        """What a pack could fetch or somebody could be asked for, with them in it.

        The credit table only covers what is on disk, so a name pressed on a film
        found the shelf and nothing else. TMDB is asked what else that person is in,
        and the answer is matched against the packs and the catalogue: by TMDB's own
        number where there is one, and by title and year for a pack, which carries
        no number.
        """
        if not (show.get("download") or show.get("request")):
            return []
        said = None
        try:
            if person:
                said = self.lib.tmdb("/person/%d/movie_credits" % int(person))
            if not said and str(name or "").strip():
                hit = ((self.lib.tmdb("/search/person", query=name) or {})
                       .get("results") or [])
                if hit and hit[0].get("id"):
                    said = self.lib.tmdb("/person/%d/movie_credits"
                                         % int(hit[0]["id"]))
        except Exception:
            return []
        theirs = (said or {}).get("cast") or []
        if not theirs:
            return []
        numbers = {int(c["id"]) for c in theirs if c.get("id")}
        named = {}
        for c in theirs:
            year = str(c.get("release_date") or "")[:4]
            named[self._bare(c.get("title"))] = int(year) if year.isdigit() else 0
        seen = {self._bare(x.get("title")) for x in already}
        out = []
        def take(one):
            bare = self._bare(one.get("title"))
            if not bare or bare in seen:
                return
            seen.add(bare)
            out.append(one)
        if show.get("download"):
            for o in self._offered_quietly():
                want = named.get(self._bare(o.get("title")))
                if want is None:
                    continue
                here = int(o.get("year") or 0)
                # the same title in another year is another film
                if want and here and abs(want - here) > 1:
                    continue
                take(o)
        if show.get("request"):
            try:
                import pd_streaming
                for r in pd_streaming.shown():
                    if r.get("here"):
                        continue
                    if int(r.get("tmdb") or 0) in numbers:
                        take(pd_streaming.item(r))
            except Exception:
                pass
        return out

    def _with_offered(self, items, sort, genre, era, kind="movie", show=None, cut=""):
        """What a pack can give, among the library's own and in its order.

        Films for the film shelf and programmes for the other: a film on offer has
        always stood on the shelf whether it was here or not, and a programme from a
        pack of episodes is the same offer in another shape.

        `show` is what this viewer wants the shelf to stand - what a pack can fetch,
        and what can only be asked for. Play, then download, then ask: a title held
        here or carried by a pack is never also offered as one to ask somebody for.
        """
        show = show or {"disk": True, "download": True}
        named_low = lambda x: str(x.get("title") or "").strip().lower()
        offers = [] if not show.get("download") else (
            self._offered_shows_quietly() if kind == "show"
            else self._offered_quietly())
        if kind == "show" and offers:
            # a programme this machine already has stands once: its own row, with the
            # pack's episodes inside it rather than a second row beside it
            # in letters and digits: "Dr Clock" from a pack is the library's "Dr. Clock"
            plain = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
            here = {plain(x.get("title")) for x in items}
            offers = [o for o in offers if plain(o.get("title")) not in here]
        if kind == "movie" and show.get("request"):
            # new on streaming and nowhere in this house: joined here so that the
            # genre and decade below sift them with everything else
            try:
                import pd_streaming
                # By the name and the year, not the name alone. A pack carries
                # a film from 2002 and the new film of the same name was
                # dropped as one the house already had - two films twenty-four years
                # apart counted as one. Where a year is missing the name still has to
                # stand on its own.
                def same_film(x):
                    return (named_low(x), int(x.get("year") or 0))
                taken = {same_film(x) for x in items + offers}
                unyeared = {named_low(x) for x in items + offers
                            if not int(x.get("year") or 0)}
                coming_in = self._coming_offers()
                offers = offers + [self._wearing(pd_streaming.item(r), coming_in)
                                   for r in pd_streaming.shown()
                                   if not r.get("here")
                                   and self.well_thought_of(r)
                                   and named_low(r) not in unyeared
                                   # outside the window asked for: it was read for a
                                   # wider one and is not this shelf's business
                                   and not (cut and str(r.get("released") or "")
                                            and str(r["released"])[:10] < cut)
                                   and (str(r.get("title") or "").strip().lower(),
                                        int(r.get("year") or 0)) not in taken]
            except Exception:
                pass
        if not offers:
            return items
        wants = {g.strip().lower() for g in str(genre or "").split(",") if g.strip()}
        if wants:
            offers = [o for o in offers
                      if wants <= {g.strip().lower() for g in o.get("genres") or []}]
        firsts = self.decades_asked(era)
        if firsts:
            offers = [o for o in offers
                      if o.get("year") and any(f <= o["year"] <= f + 9 for f in firsts)]
        field, _, way = sort.partition(":")
        back = way == "desc"
        named = lambda x: (x.get("titleSort") or x.get("title") or "").lower()
        both = items + offers
        if field == "originallyAvailableAt":
            # A list holding what the packs offer is ordered here rather than in the
            # query, so this is the sort that decides "recently released" - the one
            # place it had to be put right. The library keeps a year and nothing
            # finer, so every film of this year tied and the name broke the tie: a
            # film released last week sat under R, thousands of rows down. The dates
            # already read for the new arrivals row settle it where there is one.
            try:
                import pd_streaming
                dated = pd_streaming.held_dates()
            except Exception:
                dated = {}

            def when(x):
                got = dated.get(str(x.get("ratingKey") or ""))
                if got:
                    return got
                # its own date, which a film on offer and one to ask for both carry.
                # Held titles are looked up above because the library keeps a year
                # and nothing finer for them.
                said = str(x.get("originallyAvailableAt") or "")[:10]
                if len(said) == 10 and said[4] == "-" and said[7] == "-":
                    return said
                y = int(x.get("year") or 0)
                # a year on its own sits below every dated film of that year and
                # above everything from the year before
                return "%04d-00-00" % y if y else ""

            has = [x for x in both if when(x)]
            none = [x for x in both if not when(x)]
            # sorted twice and stably: the alphabet decides between two films that
            # came out on the same day, and holds the unknown-year tail in order
            has.sort(key=named)
            has.sort(key=when, reverse=back)
            none.sort(key=named)
            return has + none
        if field == "year":
            return sorted(both, key=lambda x: (not x.get("year"),
                                               -(x.get("year") or 0) if back
                                               else (x.get("year") or 0), named(x)))
        if field == "addedAt":
            return sorted(both, key=lambda x: x.get("addedAt") or 0, reverse=back)
        return sorted(both, key=named, reverse=back)

    #: a film's studios, asked of the catalogue again after this long
    STUDIOS_AGE = 30 * 86400

    #: bumped when the rule for which studios stand on a page changes: what was kept
    #: under the old rule is asked for again
    STUDIOS_RULE = 3

    def _studio_cards(self, companies):
        """Studios as a page wants them: a name, and where its mark is fetched."""
        import pd_streaming

        def logo_of(number):
            # a studio shown in place of its label: its own mark, from the catalogue
            return (self._catalogue("/company/%d" % int(number), self._film_facts,
                                    ("company", int(number))) or {}).get("logo_path")
        return [{"name": s["name"],
                 "logo": ("/art/studio/" + s["logo"].strip("/"))
                         if re.match(r"^/[A-Za-z0-9_-]+\.(png|jpg|jpeg|svg)$", s["logo"]) else ""}
                for s in pd_streaming.studios_of(companies, logo_of=logo_of)]

    def _studios_for(self, con, key, number):
        """The studios behind a film here. Kept in the library, so its page asks the
        catalogue once and not again for a month."""
        try:
            number = int(number or 0)
        except (TypeError, ValueError):
            number = 0
        if not number:
            return []
        con.execute("CREATE TABLE IF NOT EXISTS film_studios "
                    "(item_id TEXT PRIMARY KEY, said TEXT, at INTEGER)")
        row = con.execute("SELECT said, at FROM film_studios WHERE item_id=?",
                          (str(key),)).fetchone()
        kept = None
        try:
            had = json.loads(row["said"]) if row else None
            if isinstance(had, dict) and had.get("rule") == self.STUDIOS_RULE:
                kept = list(had.get("studios") or [])
        except ValueError:
            kept = None
        if kept is not None and time.time() - int(row["at"] or 0) < self.STUDIOS_AGE:
            return kept
        said = self._catalogue("/movie/%d" % number, self._film_facts, number) or {}
        if not said:
            # the catalogue is away: what was kept stands, and nothing is written down
            return kept or []
        got = self._studio_cards(said.get("production_companies"))
        con.execute("INSERT OR REPLACE INTO film_studios (item_id, said, at) VALUES (?,?,?)",
                    (str(key), json.dumps({"rule": self.STUDIOS_RULE, "studios": got}),
                     int(time.time())))
        con.commit()
        return got

    def _coming_offers(self):
        """Releases coming in straight from the tracker, by the key of the title each
        is: its card as the download list gives it, with how far it has got."""
        hook = getattr(self, "coming_from_tracker", None)
        if not hook:
            return {}
        try:
            return {str(c.get("ratingKey")): c for c in hook(True, "") or []}
        except Exception:
            return {}

    @staticmethod
    def _wearing(card, coming):
        """A new release's card wearing its download while one is coming in, so its
        poster says how far wherever it stands. Still a title to ask for - only the
        numbers are added."""
        said = coming.get(str(card.get("ratingKey") or "")) if coming else None
        if said and said.get("offer"):
            card["offer"] = dict(said["offer"])
        return card

    def _page(self, items, q):
        # A window into a long list. An older app that asks in the older way gets the
        # whole list rather than a page of it, which is heavier and still correct.
        def asked(name, fallback):
            return int(q[name][0] or fallback) if q.get(name) else fallback
        start = asked("start", 0)
        size = asked("count", len(items))
        return {"size": len(items[start:start + size]), "totalSize": len(items),
                "Metadata": items[start:start + size]}

    def _arrived_since(self, con, one):
        """The library's key for a streaming title that has come in since the reading."""
        from pd_library import flatten_title
        want = flatten_title(one.get("title") or "")
        if not want:
            return ""
        year = int(one.get("year") or 0)
        try:
            # names and years only, of what this house holds a file for. The whole
            # column rather than a narrowing clause: a title is spelt differently in a
            # catalogue and in a file name, which is what flattening them is for.
            rows = con.execute(
                "SELECT id, title, year FROM item WHERE type='movie' "
                "AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = item.id)").fetchall()
        except Exception:
            return ""
        for r in rows:
            if flatten_title(r["title"]) != want:
                continue
            # the year has to agree where both know it: two films share a name
            if year and r["year"] and abs(int(r["year"]) - year) > 1:
                continue
            return str(r["id"])
        return ""

    def _cast_of(self, key, kind, tmdb):
        """Who is in a film this house does not hold yet, as its own page shows it.

        A new release or a film a pack carries had a title, a year and a summary and
        nobody in it. Asked of the catalogue once and kept under the film's own key, in
        the same table as everything else's cast - so the faces are served the way any
        other is, and a name pressed finds what else that person is in.
        """
        try:
            number = int(tmdb or 0)
        except (TypeError, ValueError):
            number = 0
        con = self.lib.db()
        try:
            rows = con.execute("SELECT person, name, role, profile FROM credit "
                               "WHERE item_id=? ORDER BY ord", (key,)).fetchall()
            if not rows and number and (self.lib.config().get("tmdb_key") or "").strip():
                try:
                    said = self.lib.tmdb("/%s/%d/credits" % (kind, number)) or {}
                except Exception:
                    said = {}
                cast = (said.get("cast") or [])[:self.lib.CAST_KEPT]
                con.execute("DELETE FROM credit WHERE item_id=?", (key,))
                con.executemany(
                    "INSERT INTO credit (item_id, ord, person, name, role, profile) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(key, n, int(p.get("id") or 0), str(p.get("name") or ""),
                      str(p.get("character") or ""), str(p.get("profile_path") or ""))
                     for n, p in enumerate(cast) if (p.get("name") or "").strip()]
                    or [(key, 0, 0, "", "", "")])
                con.commit()
                rows = con.execute("SELECT person, name, role, profile FROM credit "
                                   "WHERE item_id=? ORDER BY ord", (key,)).fetchall()
        finally:
            con.close()
        return [{"tag": r["name"], "role": r["role"], "id": r["person"],
                 "thumb": ("/art/person/%d" % r["person"]) if r["profile"] else None}
                for r in rows if (r["name"] or "").strip()]

    def _extras_of(self, con, key):
        """The extras filed under a title, by name."""
        try:
            return con.execute(
                "SELECT i.* FROM extra x JOIN item i ON i.id = x.item_id "
                "WHERE x.parent = ? ORDER BY i.sort_title", (str(key),)).fetchall()
        except Exception:
            return []

    def _extras_listing(self, con, key):
        """A title's extras, shaped as the episodes of an Extras season."""
        owner = con.execute("SELECT * FROM item WHERE id=?", (key,)).fetchone()
        out = []
        for n, row in enumerate(self._extras_of(con, key), 1):
            one = self._movie(con, row, brief=True)
            one.update({"type": "episode", "index": n, "parentIndex": -1, "extra": True,
                        "parentRatingKey": "%s-extras" % key, "parentTitle": "Extras",
                        "grandparentRatingKey": key, "grandparentKey": key,
                        "grandparentTitle": owner["title"] if owner else "",
                        "thumb": one.get("thumb") or "/art/%s/extras" % key})
            out.append(one)
        return out

    def _extras_poster(self, con, key, want=0):
        """The title's poster with EXTRAS across its foot, made once and kept."""
        row = con.execute("SELECT poster FROM item WHERE id=?", (key,)).fetchone()
        base = self.lib.artwork(row["poster"], "w500") if row and row["poster"] else None
        folder = os.path.join(self.lib.root, "cache", "extras")
        os.makedirs(folder, exist_ok=True)
        made = os.path.join(folder, "%s-%s.jpg" % (key, os.path.basename(str(base or "none"))
                                                  .split(".")[0]))
        if not os.path.exists(made):
            try:
                from PIL import Image, ImageDraw, ImageFont
                pic = (Image.open(base).convert("RGB") if base
                       else Image.new("RGB", (500, 750), (40, 44, 52)))
                w, h = pic.size
                shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                ImageDraw.Draw(shade).rectangle([0, int(h * 0.72), w, h], fill=(0, 0, 0, 190))
                pic = Image.alpha_composite(pic.convert("RGBA"), shade).convert("RGB")
                size = int(w * 0.16)
                try:
                    font = ImageFont.truetype("arialbd.ttf", size)
                except OSError:
                    font = ImageFont.load_default()
                draw = ImageDraw.Draw(pic)
                box = draw.textbbox((0, 0), "EXTRAS", font=font)
                draw.text(((w - (box[2] - box[0])) / 2, h * 0.86 - (box[3] - box[1]) / 2),
                          "EXTRAS", font=font, fill=(255, 255, 255))
                pic.save(made, "JPEG", quality=88)
            except Exception:
                return base
        if want:
            return self._resized(made, want) or made
        return made

    def next_up(self, con, show):
        """The episode Play on a programme starts: one left part-way (the latest), else
        the first unwatched after the last one watched, else the first episode.
        Specials are left out: season 0 is not where a programme begins."""
        eps = [str(r["id"]) for r in con.execute(
            """SELECT DISTINCT e.id, e.season, e.number FROM episode e
                 JOIN file f ON f.episode_id = e.id
                WHERE e.item_id = ? AND COALESCE(e.season, 0) > 0
                ORDER BY e.season, e.number""", (str(show),))]
        if not eps:
            return None
        mine = set(eps)
        rows = {str(r["key"]): r for r in con.execute(
            "SELECT key, position, updated FROM progress "
            "WHERE who=? AND COALESCE(casual, 0) = 0", (self.who,))
            if str(r["key"]) in mine}
        seen = self._watched_all(con)
        how, pick, at = "start", eps[0], 0.0
        part = [k for k in eps if k in rows and not seen.get(k)
                and float(rows[k]["position"] or 0) > 30]
        if part:
            pick = max(part, key=lambda k: int(rows[k]["updated"] or 0))
            how, at = "resume", float(rows[pick]["position"] or 0)
        else:
            watched = [k for k in eps if seen.get(k)]
            if watched:
                last = max(watched, key=lambda k: int(rows[k]["updated"] or 0)
                           if k in rows else 0)
                after = [k for k in eps[eps.index(last) + 1:] if not seen.get(k)]
                if after:
                    pick, how = after[0], "next"
        item = self.metadata_for(con, pick, brief=True)
        if not item:
            return None
        item["nextUp"] = how
        if how == "resume":
            item["viewOffset"] = int(at * 1000)
        else:
            item.pop("viewOffset", None)
        return item

    def metadata_for(self, con, key, brief=False):
        if re.match(r"^tv\d+$", str(key)):
            # a programme off the popular list, not held yet: its page is where a
            # version of it is chosen and added as a pack
            try:
                with open(os.path.join(self.lib.root, "top_shows.json"),
                          encoding="utf-8") as f:
                    shows = json.load(f).get("shows") or []
            except (OSError, ValueError):
                shows = []
            number = int(str(key)[2:])
            one = next((x for x in shows if int(x.get("tmdb") or 0) == number), None)
            if not one:
                return None
            got = {"ratingKey": str(key), "type": "show", "title": one.get("show") or "",
                   "year": one.get("year") or None, "summary": one.get("overview") or "",
                   "rating": one.get("rating"), "tmdb": number, "addable": True,
                   "thumb": ("/art/%s/poster" % key) if one.get("poster") else None}
            if not brief:
                try:
                    said = self.lib.tmdb("/tv/%d" % number) or {}
                except Exception:
                    said = {}
                got["genres"] = [g.get("name") for g in said.get("genres") or []
                                 if g.get("name")]
                got["childCount"] = int(said.get("number_of_seasons") or 0)
                got["leafCount"] = int(said.get("number_of_episodes") or 0)
                got["Role"] = self._cast_of(str(key), "tv", number)
            return got
        if str(key).startswith("rt"):
            # New on streaming. Something to ask for - unless it has since arrived,
            # in which case it is a film with a poster and a Play button, and that is
            # what this key now means.
            #
            # The reading marks what this house holds, but it is read twice a day: a
            # film that came in this morning is still marked as missing until then,
            # and a watchlist made of these keys went on offering to ask for a film
            # already on the shelf. So the library is asked here as well.
            import pd_streaming
            one = pd_streaming.one_of(str(key))
            if not one:
                return None
            here = one.get("here")
            if not here:
                here = self._arrived_since(con, one)
            if here:
                got = self.metadata_for(con, str(here), brief=brief)
                if got:
                    return got
            got = self._wearing(pd_streaming.item(one), self._coming_offers())
            if not brief:
                got["Role"] = self._cast_of(str(key), "movie", one.get("tmdb"))
                # how long it is and what it says of itself, from the catalogue: a
                # page for deciding whether to download had neither
                try:
                    number = int(one.get("tmdb") or 0)
                    facts = (self._catalogue("/movie/%d" % number, self._film_facts,
                                             number) if number else {}) or {}
                except Exception:
                    facts = {}
                if facts.get("runtime"):
                    got["duration"] = int(facts["runtime"]) * 60000
                if facts.get("tagline"):
                    got["tagline"] = facts["tagline"]
                if len(str(facts.get("overview") or "")) > len(got.get("summary") or ""):
                    got["summary"] = facts["overview"]
                try:
                    crew = (self._catalogue("/movie/%d/credits" % number, self._film_facts,
                                            ("credits", number)) if number else {}) or {}
                except Exception:
                    crew = {}
                directors = [c.get("name") for c in (crew.get("crew") or [])
                             if c.get("job") == "Director" and c.get("name")]
                if directors:
                    got["directors"] = directors[:3]
                got["studios"] = self._studio_cards(facts.get("production_companies"))
                # where it has come out to be watched: the services' marks on its page
                try:
                    said = (self._catalogue("/movie/%d/watch/providers" % number,
                                            self._film_facts, ("providers", number))
                            if number else {}) or {}
                    # the country and the services chosen under Settings; with no
                    # country chosen, everywhere, this library's own country first
                    chosen = str(_settings().get("watchRegion") or "").upper()
                    region = chosen or str(
                        self.lib.config().get("language") or "").partition("-")[2].upper()
                    where = pd_streaming.providers_of(
                        said.get("results"), region, only=_settings().get("watchServices") or (),
                        strict=bool(chosen))
                except Exception:
                    where = []
                got["providers"] = [
                    dict(p, logo="/art/provider/" + p["logo"].strip("/"))
                    for p in where if re.match(r"^/[A-Za-z0-9_-]+\.(png|jpg|jpeg)$", p["logo"])]
            return got
        if str(key).startswith("o"):
            # a film on offer from a torrent pack, wherever a title is asked for - and once
            # it has come in, the film itself: a page left open on the offer turns into it
            import pd_torrents
            here = pd_torrents.arrived(str(key))
            if here:
                return self.metadata_for(con, here, brief)
            got = pd_torrents.metadata(str(key))
            if got and not brief and got.get("type") == "movie":
                got["Role"] = self._cast_of(str(key), "movie",
                                            got.get("tmdb") or got.get("tmdbId"))
            return got
        if key.startswith("e"):
            row = con.execute("SELECT * FROM episode WHERE id=?", (key,)).fetchone()
            return self._episode(con, row, brief=brief) if row else None
        season = re.match(r"^([0-9a-f]{12})-(?:s\d+|extras)$", key)
        if season:
            show = con.execute("SELECT * FROM item WHERE id=?", (season.group(1),)).fetchone()
            return self._show(con, show) if show else None
        if is_title(key):
            row = con.execute("SELECT * FROM item WHERE id=?", (str(key),)).fetchone()
            if not row:
                return None
            got = self._movie(con, row, brief=brief) if row["type"] == "movie" \
                else self._show(con, row)
            if got and not brief:
                if row["type"] == "movie":
                    got["Extras"] = [self._movie(con, x, brief=True)
                                     for x in self._extras_of(con, key)]
                # and what this one is an extra of, for the page to say so
                try:
                    of = con.execute("SELECT x.parent, i.title FROM extra x LEFT JOIN item i "
                                     "ON i.id = x.parent WHERE x.item_id = ?",
                                     (str(key),)).fetchone()
                except Exception:
                    of = None
                if of and of["parent"]:
                    got["extraOf"] = {"key": of["parent"], "title": of["title"] or ""}
            return got
        return None

    # ---- playback -----------------------------------------------------------
    def file_for(self, key, media_index=0):
        """The file behind a rating key, for direct play or for the ffmpeg engine.

        Nothing for a key that is not one: keys arrive from clients, and "l12" or an
        empty string is a question with no answer rather than something to fall over.
        """
        key = str(key or "")
        # hex, of the length keys are made in - and an e in front of an episode's.
        # Anything else came from somewhere that does not know what a key is.
        body = key[1:] if is_episode(key) else key
        if len(body) != 12 or any(c not in "0123456789abcdef" for c in body):
            return None
        con = self.lib.db()
        try:
            if key.startswith("e"):
                rows = con.execute("SELECT * FROM file WHERE episode_id=?", (key,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM file WHERE item_id=? AND episode_id IS NULL",
                                   (str(key),)).fetchall()
            if not rows:
                return None
            # the same files the client was listed, in the same order: version 1 of two
            # is one file for the picture and the other for its subtitles otherwise
            rows = best_first(self._whole(rows))
            r = rows[media_index if media_index < len(rows) else 0]
            try:
                inside = [int(x.get("index")) for x in json.loads(r["streams"] or "[]")
                          if x.get("index") is not None]
            except Exception:
                inside = []
            try:
                auds = json.loads(r["atracks"] or "[]")
            except Exception:
                auds = []
            return {"file": r["path"], "duration": r["duration"] or 0,
                    "videoCodec": r["vcodec"], "width": r["width"], "height": r["height"],
                    "audioChannels": r["channels"] or 2, "audioCodec": r["acodec"],
                    # which subtitle streams this copy actually holds, so a request to
                    # burn one that is not in it can be refused rather than obeyed
                    "subs": inside,
                    # and which soundtrack to encode when the client has not said:
                    # ffmpeg would take the first, which on a dubbed release is wrong
                    "audio": (auds[pick_audio(auds)].get("index")
                              if auds else None),
                    "title": os.path.basename(r["path"]),
                    # the row, which is the part the file is read by over the network
                    "part": r["id"]}
        finally:
            con.close()

    def now_playing_fields(self, key):
        """(title, subtitle, poster) for the panel: a film, or an episode of a show.

        An episode is named by its own title with the series and the numbering
        underneath, and borrows the series poster - an episode still is a wide picture
        and the panel's slot is a tall one.
        """
        con = self.lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT e.title AS ep, e.season, e.number, i.id AS show_id, "
                    "i.title AS show, i.poster AS poster "
                    "FROM episode e JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (str(key),)).fetchone()
                if not row:
                    return None, None, None
                return (row["ep"] or row["show"],
                        "%s  S%02dE%02d" % (row["show"], row["season"] or 0,
                                            row["number"] or 0),
                        # 320 wide: the panel can only halve or quarter an image, so a
                        # 500-wide poster would land at 250 in a 320-wide slot
                        "/local/art/%s/poster?w=320" % row["show_id"] if row["poster"]
                        else None)
            row = con.execute("SELECT id, title, year, poster FROM item WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return None, None, None
            return (row["title"],
                    str(row["year"]) if row["year"] else None,
                    "/local/art/%s/poster?w=320" % row["id"] if row["poster"] else None)
        except Exception:
            return None, None, None
        finally:
            con.close()

    def prev_episode_key(self, con, key):
        """The episode before this one - the mirror of next_episode_key.

        The first episode of a season leads back into the last of the one before, and
        the very first leads nowhere, which is right.
        """
        try:
            here = con.execute("SELECT item_id, season, number FROM episode WHERE id=?",
                               (str(key),)).fetchone()
        except (TypeError, ValueError):
            return None
        if not here:
            return None
        row = con.execute(
            """SELECT e.id FROM episode e JOIN file f ON f.episode_id = e.id
               WHERE e.item_id = ?
                 AND (e.season < ? OR (e.season = ? AND e.number < ?))
               ORDER BY e.season DESC, e.number DESC LIMIT 1""",
            (here["item_id"], here["season"], here["season"], here["number"])).fetchone()
        return str(row["id"]) if row else None

    def deck_family(self, con, key):
        """What a Continue watching row belongs to: its programme, or the film itself.

        Putting something aside is about the programme - saying "not this series just
        now" - and an episode is only ever a way of naming one.
        """
        key = str(key)
        if not key.startswith("e"):
            return key
        try:
            row = con.execute("SELECT item_id FROM episode WHERE id=?",
                              (key,)).fetchone()
        except (TypeError, ValueError):
            return key
        return str(row["item_id"]) if row else key

    def next_episode_key(self, con, key):
        """The episode after this one, if the library holds it.

        Ordered by season then number, so the last episode of a season leads into the
        first of the next; the end of the last season leads nowhere, which is right.
        """
        try:
            here = con.execute("SELECT item_id, season, number FROM episode WHERE id=?",
                               (str(key),)).fetchone()
        except (TypeError, ValueError):
            return None
        if not here:
            return None
        nxt = con.execute(
            """SELECT e.id FROM episode e JOIN file f ON f.episode_id = e.id
               WHERE e.item_id = ?
                 AND (e.season > ? OR (e.season = ? AND e.number > ?))
               ORDER BY e.season, e.number LIMIT 1""",
            (here["item_id"], here["season"], here["season"], here["number"])).fetchone()
        return (str(nxt["id"])) if nxt else None

    def offered_next_episode(self, con, key, after):
        """The episode straight after this one when a pack has it and this machine does not.

        Returns its offer, or None where the next one is here (or nothing is). Only the
        very next number counts: this is about not stepping over one, not about
        offering the rest of the series.
        """
        try:
            here = con.execute(
                "SELECT item_id, season, number FROM episode WHERE id=?",
                (str(key),)).fetchone()
        except (TypeError, ValueError):
            return None
        if not here:
            return None
        season, number = int(here["season"] or 0), int(here["number"] or 0)
        # what the next one would be numbered, and what the file-backed answer was
        if after:
            nxt = con.execute("SELECT season, number FROM episode WHERE id=?",
                              (str(after),)).fetchone()
            if nxt and int(nxt["season"] or 0) == season                     and int(nxt["number"] or 0) == number + 1:
                return None               # the next one is here: nothing to fill in
        show = con.execute("SELECT title FROM item WHERE id=?",
                           (here["item_id"],)).fetchone()
        for one in self._offered_episodes_of(show["title"] if show else "",
                                             season, {number}):
            if int(one.get("index") or 0) == number + 1:
                return one
        return None

    def title_for(self, key):
        """A title a person would recognise, for the list of what is being watched."""
        con = self.lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT e.title AS ep, e.season, e.number, i.title AS show "
                    "FROM episode e JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (str(key),)).fetchone()
                if not row:
                    return "something"
                # a season or a number kept as text passes "or 0" and then
                # fails %d, which took the whole answer down rather than one title
                def num(v):
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return 0
                return "%s S%dE%d - %s" % (row["show"], num(row["season"]),
                                           num(row["number"]), row["ep"] or "")
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return "something"
            try:
                year = int(row["year"] or 0)
            except (TypeError, ValueError):
                year = 0
            return row["title"] + ((" (%d)" % year) if year else "")
        except Exception:
            return "something"
        finally:
            con.close()

    def round_in_words(self, con, one):
        """One viewer's watchlist, described so another library can place it."""
        return {"watchlistIs": [w for w in (self.what_it_is(con, k)
                                            for k in (one.get("watchlist") or [])) if w]}

    def what_it_is(self, con, key):
        """What a key is, in words another library would recognise.

        Every library numbers its own titles, so a key is meaningless on the machine
        keeping copies. A programme, a season and a number is not.
        """
        key = str(key or "")
        if key.startswith("e"):
            row = con.execute(
                "SELECT e.season, e.number, i.title FROM episode e "
                "JOIN item i ON i.id = e.item_id WHERE e.id=?",
                (key,)).fetchone() if is_episode(key) else None
            if not row:
                return None
            return {"type": "episode", "show": row["title"],
                    "season": row["season"] or 0, "episode": row["number"] or 0}
        if is_title(key):
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (str(key),)).fetchone()
            # no row is not the same as nothing to say: a pack's key and a streaming
            # key look like a title's and are not one, so they fell out here
            if row:
                return {"type": "movie", "title": row["title"],
                        "year": row["year"] or 0}
        # A film on offer from a pack, or one on the streaming list that can only be
        # asked for: its key is not this library's numbering at all - it is worked out
        # from the same source on every machine - so it travels as itself. Returning
        # nothing dropped them here, and a watchlist of eleven arrived as four.
        if key:
            return {"type": "key", "key": key}
        return None

    def key_of(self, con, said):
        """The other way about: what this library calls the thing described."""
        from pd_library import flatten_title
        if not isinstance(said, dict):
            return ""
        if said.get("type") == "key":
            return str(said.get("key") or "")
        if said.get("type") == "episode":
            show = flatten_title(said.get("show") or "")
            try:
                season = int(said.get("season") or 0)
                number = int(said.get("episode") or 0)
            except (TypeError, ValueError):
                return ""
            for row in con.execute(
                    "SELECT e.id, i.title FROM episode e JOIN item i "
                    "ON i.id=e.item_id WHERE e.season=? AND e.number=?",
                    (season, number)):
                if flatten_title(row["title"]) == show:
                    return str(row["id"])
            return ""
        plain = flatten_title(said.get("title") or "")
        year = str(said.get("year") or "")
        for row in con.execute("SELECT id, title, year FROM item WHERE type='movie'"):
            if flatten_title(row["title"]) != plain:
                continue
            if year and year != "0" and str(row["year"] or "") != year:
                continue
            return str(row["id"])
        return ""

    def _stub_for(self, con, said):
        """A row for a title this library has not got, so a place has somewhere to live.

        The machine keeping copies holds a slice of the library, and a place written
        on the main server for a film it has not copied had nowhere to go: the row was
        dropped on arrival and never sent again, so the two shelves disagreed for
        good. Both machines work a key out from the title itself, so the row made here
        is the one the film will arrive into if it is ever copied.

        Nothing is played from it - there is no file - and the shelf says so.
        """
        from pd_library import title_key, episode_key
        if not isinstance(said, dict):
            return ""
        now = int(time.time())

        def stub_item(kind, name, year):
            key = title_key(kind, name, year)
            con.execute(
                """INSERT OR IGNORE INTO item (id, type, title, sort_title, year,
                                               added, identified)
                   VALUES (?,?,?,?,?,?,0)""",
                # added 0: a place written down is not an arrival
                (key, kind, name, name, int(year or 0), 0))
            return key

        if said.get("type") == "movie":
            name = str(said.get("title") or "").strip()
            if not name:
                return ""
            key = stub_item("movie", name, said.get("year") or 0)
        elif said.get("type") == "episode":
            show = str(said.get("show") or "").strip()
            if not show:
                return ""
            item = stub_item("show", show, 0)
            key = episode_key(item, int(said.get("season") or 0),
                              int(said.get("episode") or 0))
            con.execute(
                """INSERT OR IGNORE INTO episode (id, item_id, season, number, title)
                   VALUES (?,?,?,?,'')""",
                (key, item, int(said.get("season") or 0),
                 int(said.get("episode") or 0)))
        else:
            return ""
        con.commit()
        return key

    def merge_places(self, frm, to):
        """Move one viewer's places onto another, the newer note winning.

        For a machine that filed the house's evenings under a name of its own before
        it knew the key the rest of the house uses. What was marked and what was
        forgotten travel with the place: both are answers about the same viewing.
        """
        if not frm or not to or frm == to:
            return 0
        con = self.lib.db()
        moved = 0
        try:
            for r in con.execute(
                    "SELECT key, position, duration, updated, COALESCE(casual, 0) casual,"
                    " COALESCE(marked, 0) marked FROM progress WHERE who=?",
                    (frm,)).fetchall():
                had = con.execute("SELECT updated FROM progress WHERE who=? AND key=?",
                                  (to, r["key"])).fetchone()
                if had and int(had["updated"] or 0) >= int(r["updated"] or 0):
                    continue
                con.execute(
                    """INSERT INTO progress (key, position, duration, updated, who,
                                             casual, marked)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(who, key) DO UPDATE SET
                       position=excluded.position, duration=excluded.duration,
                       updated=excluded.updated, casual=excluded.casual,
                       marked=excluded.marked""",
                    (r["key"], r["position"], r["duration"], r["updated"], to,
                     r["casual"], r["marked"]))
                moved += 1
            for r in con.execute("SELECT key, at FROM forgot WHERE who=?",
                                 (frm,)).fetchall():
                con.execute(
                    """INSERT INTO forgot (who, key, at) VALUES (?,?,?)
                       ON CONFLICT(who, key) DO UPDATE SET at=MAX(at, excluded.at)""",
                    (to, r["key"], r["at"]))
            con.execute("DELETE FROM progress WHERE who=?", (frm,))
            con.execute("DELETE FROM forgot WHERE who=?", (frm,))
            con.commit()
        finally:
            con.close()
        return moved

    def take_progress(self, rows, may_stub=False):
        """Write down where other machines say these viewings had got to.

        Only where the other machine's note is the newer one: two servers in one house
        are two people writing in the same book, and the last one to watch is the one
        who was right.

        may_stub is for the machine keeping copies: a place for a title it has not got
        is kept against a row made for it rather than thrown away.
        """
        con = self.lib.db()
        taken = 0
        made = []
        try:
            for row in rows or []:
                key = self.key_of(con, row.get("is") or {})
                if not key and may_stub:
                    key = self._stub_for(con, row.get("is") or {})
                    if key:
                        made.append(key)
                if not key:
                    continue
                who = str(row.get("who") or "me")
                when = int(row.get("updated") or 0)
                had = con.execute(
                    "SELECT updated, position, duration, COALESCE(marked, 0) marked "
                    "FROM progress WHERE who=? AND key=?", (who, key)).fetchone()
                if had and int(had["updated"] or 0) >= when:
                    continue
                # Forgotten here, and nothing watched since: the other machine is
                # offering a place this viewer threw away. Only a newer viewing
                # brings it back.
                gone = con.execute("SELECT at FROM forgot WHERE who=? AND key=?",
                                   (who, key)).fetchone()
                if gone and int(gone["at"] or 0) >= when:
                    continue
                # The later note usually wins, but "barely started" must not wipe out
                # a place somebody actually reached. A film opened for four seconds
                # and shut again is newer than the forty minutes watched yesterday,
                # and would replace it. This could not arise while places under half a
                # minute were never sent; now that everything travels, it can.
                #
                # Finished and marked are never held back by this - they are the whole
                # reason the sending end stopped filtering.
                coming = int(row.get("position") or 0)
                if (had and not row.get("marked") and not row.get("forgot")
                        and coming <= 30
                        and int(had["position"] or 0) > coming + 60):
                    continue
                if row.get("forgot"):
                    # pressed there, so it counts here: the place goes back to nought
                    # and the note travels with it, or the shelf here would hand the
                    # episode on to the next one instead of letting the programme go.
                    con.execute("""INSERT INTO forgot (who, key, at) VALUES (?,?,?)
                                   ON CONFLICT(who, key) DO UPDATE SET at=excluded.at""",
                                (who, key, when))
                # A forget is the absence of a place, not a place of nought.
                #
                # A cross pressed on an episode the other machine never played leaves
                # only a note there - no progress row to carry it - so it travels as
                # position nought with no length on it. Writing that down as progress
                # invented a place out of a deletion, and Continue watching then
                # showed an episode nobody had opened. The note is recorded above;
                # here the place goes, which is what the cross meant.
                empty = (not int(row.get("position") or 0)
                         and not int(row.get("furthest") or 0)
                         and not int(row.get("duration") or 0)
                         and not row.get("marked"))
                if empty and row.get("forgot"):
                    con.execute("DELETE FROM progress WHERE who=? AND key=?",
                                (who, key))
                    taken += 1
                    continue
                if empty and not had:
                    continue
                # and whether it was said by hand, which is the one kind of finished a
                # position cannot show. Never unsaid by a machine that does not know
                # it: a build before this one sends no "marked" at all, and taking
                # that as "not marked" put a film marked here back on Continue
                # watching. Marked travels one way - by hand, on the page.
                marked = 1 if (row.get("marked") or (had and had["marked"])) else 0
                con.execute(
                    """INSERT INTO progress (key, position, duration, updated, who,
                                             casual, marked, furthest)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(who, key) DO UPDATE SET
                       position=excluded.position, duration=excluded.duration,
                       updated=excluded.updated, casual=excluded.casual,
                       marked=excluded.marked,
                       furthest=MAX(COALESCE(excluded.furthest, 0),
                                    CASE WHEN excluded.position <= 30 THEN 0
                                         ELSE COALESCE(progress.furthest, 0) END)""",
                    (key, int(row.get("position") or 0),
                     int(row.get("duration") or 0)
                     or (int(had["duration"] or 0) if had else 0), when, who,
                     1 if row.get("casual") else 0, marked,
                     int(row.get("furthest") or row.get("position") or 0)))
                taken += 1
            con.commit()
        finally:
            con.close()
        # what was made a row for, so whoever asked can go and find out what it is:
        # a card with a name and no picture is what a lost title looks like
        self.stubs_made = made
        return taken

    def progress_of(self, who_list, since=0, limit=500, with_mark=False):
        """Where these viewers had got to, described so another library can place it.

        Oldest first, and with the point the caller may resume from: a window holding
        more places than the limit used to hand back the newest of them while the
        caller moved its mark to now, so the rest were never sent at all. Truncated
        here means the mark stops at the last place actually sent.
        """
        con = self.lib.db()
        out = []
        mark = int(time.time())
        try:
            for who in who_list or []:
                # A place past the end is sent too. Holding those back meant the one
                # fact that settles a disagreement was the one fact never sent: the
                # moment a film was finished here it dropped out of this list, so the
                # other machine kept the last part-way figure it had seen and showed
                # it as half watched for ever. Finished is not the absence of a place,
                # it is a place - and the machine receiving it works out "watched"
                # from the position itself, the same way this one does.
                # Marked by hand comes with it: that is written as position nought,
                # which the "past the first half minute" test threw away.
                # Nothing is held back at all now. Every test here was a place one
                # machine knew and the other was not told about, and each one showed
                # up as the two disagreeing about Continue watching: finished, marked,
                # and the first half minute - which reads as "not worth resuming" on
                # the machine that has it and as "never started" on the one that does
                # not. What the house has watched is one set of facts, not two.
                rows = con.execute(
                    """SELECT key, position, duration, updated,
                              COALESCE(casual, 0) casual,
                              COALESCE(marked, 0) marked,
                              COALESCE(furthest, 0) furthest FROM progress
                       WHERE who = ? AND updated > ?
                       ORDER BY updated ASC LIMIT ?""",
                    (who, int(since), int(limit))).fetchall()
                for row in rows:
                    said = self.what_it_is(con, row["key"])
                    if not said:
                        continue
                    # and whether this nought was pressed rather than played. A
                    # forgotten place travels as position nought, which the other end
                    # refuses where it holds a real place - so the cross never
                    # reached the second machine and the film stayed on its shelf.
                    gone = con.execute("SELECT at FROM forgot WHERE who=? AND key=?",
                                       (who, row["key"])).fetchone()
                    out.append({"who": who, "is": said,
                                "position": int(row["position"] or 0),
                                "duration": int(row["duration"] or 0),
                                "casual": int(row["casual"] or 0),
                                "marked": int(row["marked"] or 0),
                                "furthest": int(row["furthest"] or 0),
                                "forgot": 1 if (gone and int(gone["at"] or 0)
                                                >= int(row["updated"] or 0)) else 0,
                                "updated": int(row["updated"] or 0)})
                # and the forgets that have no place of their own. A cross pressed
                # on an episode this machine never played writes only the note -
                # there is no row in progress to carry it - so the other machine
                # kept the place it had and the card came back from there.
                for row in con.execute(
                        """SELECT f.key key, f.at at FROM forgot f
                           LEFT JOIN progress p ON p.who = f.who AND p.key = f.key
                           WHERE f.who = ? AND f.at > ? AND p.key IS NULL
                           ORDER BY f.at ASC LIMIT ?""",
                        (who, int(since), int(limit))).fetchall():
                    said = self.what_it_is(con, row["key"])
                    if not said:
                        continue
                    out.append({"who": who, "is": said, "position": 0,
                                "duration": 0, "casual": 0, "marked": 0,
                                "forgot": 1, "updated": int(row["at"] or 0)})
                # a full page is a window with more in it than was sent: the caller
                # resumes at the last one sent rather than at now
                if len(rows) >= int(limit) and rows:
                    mark = min(mark, int(rows[-1]["updated"] or 0))
        finally:
            con.close()
        return (out, mark) if with_mark else out

    def key_for_file(self, file_id):
        """The rating key of whatever a file belongs to - an episode, or a film."""
        con = self.lib.db()
        try:
            row = con.execute("SELECT item_id, episode_id FROM file WHERE id=?",
                              (int(file_id),)).fetchone()
        except (TypeError, ValueError):
            return ""
        finally:
            con.close()
        if not row:
            return ""
        return str(row["episode_id"]) if row["episode_id"] else str(row["item_id"])

    def title_for_file(self, file_id):
        """The same, for a file being read directly rather than encoded."""
        con = self.lib.db()
        try:
            row = con.execute("SELECT item_id, episode_id FROM file WHERE id=?",
                              (int(file_id),)).fetchone()
        except Exception:
            return "something"
        finally:
            con.close()
        if not row:
            return "something"
        return self.title_for(str(row["episode_id"]) if row["episode_id"]
                              else str(row["item_id"]))

    def file_facts(self, file_id):
        """Size and shape of one file, for deciding whether it may be sent as it is."""
        con = self.lib.db()
        try:
            row = con.execute("SELECT height, bitrate, duration FROM file WHERE id=?",
                              (int(file_id),)).fetchone()
            return dict(row) if row else {}
        except Exception:
            return {}
        finally:
            con.close()

    def part(self, file_id):
        con = self.lib.db()
        try:
            row = con.execute("SELECT path FROM file WHERE id=?", (int(file_id),)).fetchone()
            return row["path"] if row else None
        finally:
            con.close()
