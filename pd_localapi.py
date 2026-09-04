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
    return str(show_id) in (_settings().get("subsFor", {}) or {})


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


def subtitle_confirmed(episode_id):
    """The same question, asked the way an episode row asks it."""
    return subtitle_verified("e%s" % episode_id)


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
                _NAMES["map"] = json.loads(f.read()).get("subsRelease", {}) or {}
        except Exception:
            _NAMES["map"] = {}
        _NAMES["when"] = when
    return _NAMES["map"]


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
        out.append({"file": full, "lang": lang, "name": label})
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
    # the copy that got played. Nothing in a film folder is bigger than the film.
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


def media_block(rows, key_prefix="/parts/", aside="", picked="", proved="",
                copy=""):
    """The Media/Part/Stream nesting the clients expect, built from our file rows.

    `copy` is the file a viewer settled on. The list is best picture first, so the
    first entry is what a client plays unless one is marked - which is the whole rule:
    the best copy by default, the chosen one once somebody has chosen.
    """
    out = []
    for r in best_first(rows):
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
        marked = (verified_records("e%s" % r["episode_id"]) if r["episode_id"]
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
        beside = list(enumerate(sidecars(r["path"])))
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
        beside.sort(key=lambda pair: (overruns(pair[1]),
                                      not is_verified(pair[1]),
                                      (pair[1]["name"] or "").strip().lower() != chosen))
        for n, side in beside:
            streams.append({"streamType": 3, "id": 200000 + n, "index": -(n + 1),
                            "codec": "srt", "external": True,
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
        height = r["height"] or 0
        out.append({
            "id": r["id"],
            # the copy this viewer settled on, if it is this one
            "picked": bool(copy) and r["path"] == copy,
            "container": container_of(r),
            "videoCodec": r["vcodec"], "audioCodec": r["acodec"],
            "width": r["width"], "height": r["height"],
            "videoResolution": ("4k" if height >= 1700 else
                                "1080" if height >= 900 else
                                "720" if height >= 650 else str(height or "")),
            "bitrate": r["bitrate"], "audioChannels": r["channels"],
            "duration": int((r["duration"] or 0) * 1000),
            "Part": [{"id": r["id"], "key": key_prefix + str(r["id"]),
                      "file": r["path"], "size": r["size"],
                      "container": container_of(r), "Stream": streams}],
        })
    return out


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
    @property
    def aside(self):
        return self._mine("aside", {})

    @aside.setter
    def aside(self, value):
        self._asking.aside = value

    # Where a casual playing has got to, per title. Set by the server before it hands
    # anything over, and written back through `casual_note` - the library's own record
    # of what has been watched is left alone, because putting something on is not
    # watching it.
    @property
    def casual_at(self):
        return self._mine("casual_at", {})

    @casual_at.setter
    def casual_at(self, value):
        self._asking.casual_at = value

    @property
    def casual_note(self):
        return self._mine("casual_note", None)

    @casual_note.setter
    def casual_note(self, value):
        self._asking.casual_note = value

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
    def _files(self, con, item_id=None, episode_id=None):
        if episode_id is not None:
            return con.execute("SELECT * FROM file WHERE episode_id=?", (episode_id,)).fetchall()
        return con.execute("SELECT * FROM file WHERE item_id=? AND episode_id IS NULL",
                           (item_id,)).fetchall()

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
    #: and how much of it has to have been played to say so. Skipping to the end of an
    #: episode puts the resume point past the mark without a minute of it having been
    #: watched, and pressing next or previous from near the end did the same.
    SAT_THROUGH = 0.6

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
            "SELECT position, duration, COALESCE(marked, 0) marked FROM progress "
            "WHERE key=? AND who=?", (str(key), self.who)).fetchone()
        if row and row["marked"]:
            return True                    # said by hand, which settles it
        if not (row and row["duration"] and
                row["position"] / row["duration"] > self.WATCHED):
            return False
        try:
            seen = con.execute(
                """SELECT COALESCE(SUM(MAX(0, MIN(updated - started, duration))), 0) sat,
                          COUNT(*) n
                   FROM watchlog WHERE who=? AND key=?""",
                (self.who, str(key))).fetchone()
        except Exception:
            return True
        if not seen or not seen["n"]:
            return True                    # nothing logged: the old answer stands
        return (seen["sat"] or 0) >= row["duration"] * self.SAT_THROUGH

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
        m = re.match(r"^(\d+)-s(\d+)$", key)
        if m:
            rows = con.execute("SELECT id FROM episode WHERE item_id=? AND season=?",
                               (int(m.group(1)), int(m.group(2)))).fetchall()
            return ["e%d" % r["id"] for r in rows]
        if key.isdigit():
            rows = con.execute("SELECT id FROM episode WHERE item_id=?",
                               (int(key),)).fetchall()
            return ["e%d" % r["id"] for r in rows] or [key]      # a film is itself
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
                con.execute("DELETE FROM progress WHERE key=? AND who=?", (one, self.who))
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
                                  (int(key[1:]),)).fetchone()
            else:
                row = con.execute("SELECT duration FROM file WHERE item_id=? "
                                  "AND episode_id IS NULL", (int(key),)).fetchone()
        except (TypeError, ValueError):
            return 1.0
        return float(row["duration"]) if row and row["duration"] else 1.0

    def _progress(self, con, key):
        row = con.execute("SELECT position, duration FROM progress WHERE key=? AND who=?",
                          (key, self.who)).fetchone()
        return row

    def _movie(self, con, row, brief=False):
        files = self._files(con, item_id=row["id"])
        dur = int((files[0]["duration"] or 0) * 1000) if files else 0
        prog = self._progress(con, str(row["id"]))
        out = {
            "ratingKey": str(row["id"]), "type": "movie", "title": row["title"],
            "titleSort": row["sort_title"], "year": row["year"],
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
        # a film that has been watched offers no resume point: it would land on the
        # closing seconds, and the button belongs to whoever has not finished it
        if prog and not out["viewCount"]:
            out["viewOffset"] = int((prog["position"] or 0) * 1000)
        if not brief:
            out["Media"] = media_block(
                files, picked=self.picked_subtitle(str(row["id"])),
                proved=str(row["id"]), copy=self.picked_copy(str(row["id"])))
        return out

    def _show(self, con, row):
        eps = con.execute("SELECT COUNT(*) c FROM episode WHERE item_id=?", (row["id"],)).fetchone()["c"]
        seasons = con.execute("SELECT COUNT(DISTINCT season) c FROM episode WHERE item_id=?",
                              (row["id"],)).fetchone()["c"]
        # when the series last put out an episode: what "recently released" means for
        # television, and what a merged list from several servers sorts on
        aired = con.execute("SELECT MAX(aired) a FROM episode WHERE item_id=? AND aired <> ''",
                            (row["id"],)).fetchone()["a"]
        # how much of the series this viewer has finished, for the tick on the poster
        seen = sum(1 for r in con.execute("SELECT id FROM episode WHERE item_id=?",
                                          (row["id"],)).fetchall()
                   if self._watched(con, "e%d" % r["id"]))
        return {
            # nothing rather than null: a client that reads it as text writes the word
            "originallyAvailableAt": aired or (
                "%04d-01-01" % row["year"] if row["year"] else ""),
            "ratingKey": str(row["id"]), "type": "show", "title": row["title"],
            "titleSort": row["sort_title"], "year": row["year"],
            "summary": row["overview"] or "", "leafCount": eps, "childCount": seasons,
            "thumb": f"/art/{row['id']}/poster" if row["poster"] else None,
            "guid": f"imdb://{row['imdb_id']}" if row["imdb_id"] else None,
            "addedAt": row["added"],
            "viewedLeafCount": seen,
            "viewCount": 1 if eps and seen >= eps else 0,
            "maxHeight": self._tall(con).get(row["id"], 0),
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

    def _episode(self, con, row, show=None, brief=False):
        show = show or con.execute("SELECT * FROM item WHERE id=?", (row["item_id"],)).fetchone()
        files = self._files(con, episode_id=row["id"])
        dur = int((files[0]["duration"] or 0) * 1000) if files else 0
        prog = self._progress(con, "e%d" % row["id"])
        out = {
            "ratingKey": "e%d" % row["id"], "type": "episode",
            "title": row["title"] or ("Episode %d" % row["number"]),
            "summary": row["overview"] or "", "index": row["number"],
            "parentIndex": row["season"], "duration": dur,
            "grandparentTitle": show["title"] if show else "",
            "grandparentRatingKey": str(row["item_id"]),
            "parentRatingKey": "%d-s%d" % (row["item_id"], row["season"]),
            "grandparentThumb": f"/art/{row['item_id']}/poster" if show and show["poster"] else None,
            "thumb": f"/art/{row['item_id']}/poster" if show and show["poster"] else None,
            "originallyAvailableAt": row["aired"],
            "viewCount": 1 if self._watched(con, "e%d" % row["id"]) else 0,
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
        if not brief:
            # the series' name and this episode's title are no evidence of a release
            out["Media"] = media_block(
                files,
                aside=((show["title"] if show else "") + " " + (row["title"] or "")),
                picked=self.picked_subtitle("e%d" % row["id"]),
                copy=self.picked_copy("e%d" % row["id"]))
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

        m = re.match(r"^/library/matches/(\d+)$", path)
        if m:
            # what else this title could be, for somebody to choose from when the
            # first answer TMDB gave was the wrong programme
            try:
                return {"size": 0,
                        "matches": self.lib.candidates(m.group(1), one("q", ""))}
            except Exception as e:
                return {"size": 0, "matches": [], "error": str(e)[:200]}

        if path == "/library/genres":
            # what is on the shelves, and how much of it: a genre nobody has is not
            # worth offering
            kind = one("type", "movie")
            rows = con.execute("SELECT genres FROM item WHERE type=? AND genres <> ''",
                               (kind,)).fetchall()
            tally = {}
            for r in rows:
                for g in (r["genres"] or "").split(","):
                    g = g.strip()
                    if g:
                        tally[g] = tally.get(g, 0) + 1
            listed = [{"title": g, "count": n} for g, n in
                      sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))]
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
                       ORDER BY last_aired IS NULL, last_aired """
                    # the SQL contains a printf of its own, so this is joined on
                    # rather than formatted in
                    + ("ASC" if sort.endswith(":asc") else "DESC")
                    + ", sort_title ASC").fetchall()
            else:
                rows = con.execute(f"SELECT * FROM item WHERE type=? ORDER BY {order}",
                                   (kind,)).fetchall()
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
            if want:
                rows = [r for r in rows
                        if want in [g.strip().lower()
                                    for g in (r["genres"] or "").split(",")]]
            items = [self._movie(con, r, brief=True) if kind == "movie" else self._show(con, r)
                     for r in rows]
            return self._page(items, q)

        m = re.match(r"^/library/sections/(\d+)/recentlyReleased$", path)
        if m:
            # the newest episodes by air date, to match the shelf beside it - a shelf
            # of series would answer a different question from "recently released"
            rows = con.execute("""SELECT e.* FROM episode e JOIN file f ON f.episode_id=e.id
                                  WHERE e.aired IS NOT NULL AND e.aired <> ''
                                  GROUP BY e.id ORDER BY e.aired DESC LIMIT 60""").fetchall()
            return self._page([self._episode(con, r, brief=True) for r in rows], q)

        m = re.match(r"^/library/sections/(\d+)/recentlyAdded$", path)
        if m:
            kind = "movie" if m.group(1) == "1" else "show"
            if kind == "movie":
                rows = con.execute("SELECT * FROM item WHERE type='movie' ORDER BY added DESC "
                                   "LIMIT 100").fetchall()
                items = [self._movie(con, r, brief=True) for r in rows]
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
                        return {"key": "e%d" % row["id"]}
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

        if path == "/library/casual/pool":
            # Everything the marked titles amount to: a film is one thing, a series is
            # all of its episodes. Keys only - this is the hat, not the shelf.
            keys = [k for k in (q.get("keys", [""])[0] or "").split(",") if k]
            out = []
            # the same thing grouped by where it came from, so a rotation can take one
            # episode of each programme in turn rather than one thing at random
            groups = []
            for key in keys:
                before = len(out)
                if key.startswith("e"):
                    out.append(key)
                    groups.append([key])
                    continue
                # a season, marked from a television: its own episodes, in order. The
                # pool understood films, programmes and single episodes, so a season
                # marked for the shuffle quietly contributed nothing at all.
                season = re.match(r"^(\d+)-s(\d+)$", key)
                if season:
                    got = ["e%d" % r["id"] for r in con.execute(
                        "SELECT id FROM episode WHERE item_id=? AND season=? "
                        "ORDER BY number",
                        (int(season.group(1)), int(season.group(2)))).fetchall()]
                    if got:
                        out += got
                        groups.append(got)
                    continue
                if not key.isdigit():
                    continue
                row = con.execute("SELECT type FROM item WHERE id=?",
                                  (int(key),)).fetchone()
                if not row:
                    continue
                if row["type"] == "movie":
                    out.append(key)
                else:
                    # a programme brings its episodes, so one with two hundred of them
                    # is not as likely to come up as a single film
                    out += ["e%d" % r["id"] for r in con.execute(
                        "SELECT id FROM episode WHERE item_id=? ORDER BY season, number",
                        (int(key),)).fetchall()]
                if len(out) > before:
                    groups.append(out[before:])
            return {"size": len(out), "keys": out, "groups": groups}

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
                "SELECT key, position, duration, updated, COALESCE(marked, 0) marked "
                "FROM progress WHERE who=? ORDER BY updated DESC LIMIT 600",
                (self.who,)).fetchall()
            items, seen, households = [], set(), set()
            for r in rows:
                key = r["key"]
                # put aside by hand, and nothing has been watched since: the whole
                # programme stays off the shelf, rather than handing over to its next
                # episode and looking as though nothing happened
                if float(self.aside.get(self.deck_family(con, key), 0)) >= \
                        float(r["updated"] or 0):
                    continue
                # a mark made by hand is finished however little of it was played:
                # it says so, and the shelf hands over to the next episode
                finished = bool(r["marked"]) or (
                    r["duration"] and r["position"] / r["duration"] > 0.95)
                # and a row at the very beginning is not something to carry on with:
                # nobody resumes a film at nought. It is what a tick leaves behind,
                # or a player that reported once and stopped.
                if not finished and (r["position"] or 0) <= 30:
                    continue
                if finished:
                    # An episode hands its place to the next one - but only to one that
                    # has not been watched either, or marking a season watched would
                    # fill the shelf with episodes already seen. A film has nowhere to
                    # go and simply leaves.
                    if not str(key).startswith("e"):
                        continue
                    for _ in range(400):            # a season is not longer than this
                        key = self.next_episode_key(con, key)
                        if not key or not self._watched(con, key):
                            break
                    if not key:
                        continue
                if key in seen:
                    continue
                seen.add(key)
                # One row per programme. Each finished episode hands over to the first
                # unwatched one after itself, so a series watched out of order - a few
                # here, a few there - produced a row for every gap in it: forty-one
                # cards of the same show, all of it "continue watching".
                family = self.deck_family(con, key)
                if family in households:
                    continue
                households.add(family)
                items.append(self.metadata_for(con, key, brief=True))
            return self._page([i for i in items if i], q)

        if path == "/prev":
            # and what came before it, for the button beside the other one
            before = self.prev_episode_key(con, one("key", ""))
            item = self.metadata_for(con, before) if before else None
            return {"size": 1, "Metadata": [item]} if item else {"size": 0, "Metadata": []}

        if path == "/next":
            # what follows this episode: the client asks rather than working out for
            # itself where a season ends and the next begins
            after = self.next_episode_key(con, one("key", ""))
            item = self.metadata_for(con, after) if after else None
            return {"size": 1, "Metadata": [item]} if item else {"size": 0, "Metadata": []}

        m = re.match(r"^/library/metadata/([^/]+)$", path)
        if m:
            # opening one title is the moment to find out what soundtracks it has,
            # for anything indexed before the library recorded them
            key = m.group(1)
            try:
                if key.startswith("e"):
                    files = con.execute("SELECT * FROM file WHERE episode_id=?",
                                        (int(key[1:]),)).fetchall()
                elif key.isdigit():
                    files = con.execute("SELECT * FROM file WHERE item_id=? AND "
                                        "episode_id IS NULL", (int(key),)).fetchall()
                else:
                    files = []
                self.learn_audio(con, files)
            except Exception:
                pass                       # a soundtrack list is not worth a failure
            item = self.metadata_for(con, m.group(1))
            return {"size": 1, "Metadata": [item]} if item else None

        m = re.match(r"^/library/metadata/([^/]+)/children$", path)
        if m:
            key = m.group(1)
            season = re.match(r"^(\d+)-s(\d+)$", key)
            if season:                                   # a season: its episodes
                rows = con.execute("""SELECT * FROM episode WHERE item_id=? AND season=?
                                      ORDER BY number""",
                                   (int(season.group(1)), int(season.group(2)))).fetchall()
                return {"size": len(rows),
                        "Metadata": [self._episode(con, r, brief=True) for r in rows]}
            if key.isdigit():                            # a show: its seasons
                show = con.execute("SELECT * FROM item WHERE id=?", (int(key),)).fetchone()
                rows = con.execute("""SELECT season, COUNT(*) c FROM episode WHERE item_id=?
                                      GROUP BY season ORDER BY season""", (int(key),)).fetchall()
                return {"size": len(rows), "Metadata": [{
                    "ratingKey": "%s-s%d" % (key, r["season"]), "type": "season",
                    "viewedLeafCount": sum(
                        1 for e in con.execute(
                            "SELECT id FROM episode WHERE item_id=? AND season=?",
                            (int(key), r["season"])).fetchall()
                        if self._watched(con, "e%d" % e["id"])),
                    "title": "Season %d" % r["season"], "index": r["season"],
                    "leafCount": r["c"], "parentRatingKey": key,
                    "thumb": f"/art/{key}/poster" if show and show["poster"] else None,
                } for r in rows]}
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
            movies = shows = []
            # "4k" is not in any title, but it is what somebody means when they type
            # it: everything held in a file that tall, films and programmes alike
            if spelt.strip().lower() in ("4k", "2160p", "uhd", "4k hdr"):
                tall = self._tall(con)
                big = [k for k, h in tall.items() if h >= self.UHD]
                if big:
                    marks = ",".join("?" * len(big))
                    movies = con.execute(
                        "SELECT * FROM item WHERE type='movie' AND id IN (%s) "
                        "ORDER BY sort_title LIMIT 60" % marks, big).fetchall()
                    shows = con.execute(
                        "SELECT * FROM item WHERE type='show' AND id IN (%s) "
                        "ORDER BY sort_title LIMIT 60" % marks, big).fetchall()
                spelt = ""                      # the words are spent
            # an episode is not searched for by picture: the programme it belongs to
            # is what the answer names, and every episode of it would be the list
            if spelt:
                movies = con.execute(
                    "SELECT * FROM item WHERE type='movie' AND title LIKE ? "
                    "ORDER BY sort_title LIMIT 40", (term,)).fetchall()
                shows = con.execute(
                    "SELECT * FROM item WHERE type='show' AND title LIKE ? "
                    "ORDER BY sort_title LIMIT 40", (term,)).fetchall()
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
                    "SELECT * FROM item WHERE year=? ORDER BY type, sort_title "
                    "LIMIT 80", (int(year_name),)).fetchall()
                    if r["id"] not in named]
            else:
                genre_name = self.genre_named(con, spelt)
                if genre_name:
                    genred = [r for r in con.execute(
                        "SELECT * FROM item WHERE genres <> '' "
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
            # and under those, what the word means rather than what it spells
            listed = lambda rows: [self._movie(con, r, brief=True) if r["type"] == "movie"
                                   else self._show(con, r) for r in rows]
            if genred:
                hubs.append({"type": "genre", "title": genre_name,
                             "Metadata": listed(genred)})
            if dated:
                hubs.append({"type": "year", "title": "From " + year_name,
                             "Metadata": listed(dated)})
            return {"size": len(hubs), "Hub": hubs}

        m = re.match(r"^/art/(\d+)/(poster|backdrop)$", path)
        if m:
            row = con.execute("SELECT poster, backdrop FROM item WHERE id=?",
                              (int(m.group(1)),)).fetchone()
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
            return {"size": 0}

        if path == "/:/timeline":                        # the client reports progress here
            key = one("ratingKey")
            pos = float(one("time", "0") or 0) / 1000.0
            dur = float(one("duration", "0") or 0) / 1000.0
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
                # Watched state is not touched: no progress row, so nothing appears in
                # Continue watching, nothing is marked seen, and no series is moved on.
                # The place is kept in the shuffle's own notes instead, which is the
                # only thing that can resume it.
                if self.casual_note:
                    self.casual_note(key, pos, dur)
                self.note_playing(key, pos, dur, one("state", "playing"),
                                  one("device", "") or "", one("client", ""),
                                  one("client", ""))
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
                if not (stopping and not known):
                    self.note_uncasual(con, key, one("device", "") or "",
                                       one("client", ""), one("state", "playing"))
                    con.execute("""INSERT INTO progress (key, position, duration, updated, who)
                                   VALUES (?,?,?,?,?)
                                   ON CONFLICT(who, key) DO UPDATE SET
                                   position=excluded.position, duration=excluded.duration,
                                   updated=excluded.updated""",
                                (key, pos, dur, int(time.time()), self.who))
                    con.commit()
                # the same post tells the panel what is on screen: state and device are
                # optional, so an older client simply reads as "playing"
                self.note_playing(key, pos, dur, one("state", "playing"),
                                  one("device", "") or "", one("client", ""),
                                  one("client", ""))
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
                                 WHERE who=? AND key=? AND casual=1
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
                             WHERE who=? AND key=? AND device=?
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

    def note_playing(self, key, position, duration, state, device, client="",
                     kind=""):
        """Remember what one client is doing. Keyed by device, so two do not fight."""
        who = device or "player"
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
        NOW[who] = {"state": said, "moved": moved,
                    "key": str(key), "title": title, "subtitle": subtitle,
                    "poster": poster,
                    "position": int(position), "duration": int(duration),
                    "device": who,
                    # a browser or the app: it changes what a fault means and what
                    # advice is worth giving
                    "client": client or "",
                    # and which build of it, and what sort of thing it is running on
                    "app": self.app_now or "", "kind": kind or "",
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
                    "app": row.get("app") or "", "kind": row.get("kind") or ""}
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
            mark = said.get("device") or said.get("title")
            if mark in seen:
                continue
            seen.add(mark)
            out.append(said)
        return out

    def _by_season(self, con, keys):
        """The same shelf, gathered into seasons.

        Marking a programme now marks each of its episodes, which is right for taking
        one off again and hopeless to look at: two hundred cards where there was one.
        Episodes of a season are shown as that season, in the order the first of them
        was marked, with how many of them are on the shelf. Films and lone episodes
        stand as they are.
        """
        out, seen = [], set()
        # which season each marked episode belongs to, in one question rather than one
        # per episode
        ids = [int(k[1:]) for k in keys if k.startswith("e") and k[1:].isdigit()]
        home = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for r in con.execute(
                    "SELECT id, item_id, season FROM episode WHERE id IN (%s)" % marks,
                    ids):
                home[r["id"]] = (r["item_id"], r["season"])
        # how many episodes each season holds, so a part-marked season says so
        counted = {}
        for item_id, season in set(home.values()):
            counted[(item_id, season)] = con.execute(
                "SELECT COUNT(*) c FROM episode WHERE item_id=? AND season=?",
                (item_id, season)).fetchone()["c"]
        held = {}
        for key in keys:
            if key.startswith("e") and key[1:].isdigit():
                where = home.get(int(key[1:]))
                if where:
                    held.setdefault(where, []).append(key)
        for key in keys:
            if key.startswith("e") and key[1:].isdigit():
                where = home.get(int(key[1:]))
                if where and where in held:
                    if where in seen:
                        continue
                    seen.add(where)
                    mine = held[where]
                    if len(mine) == 1:
                        one_card = self.metadata_for(con, mine[0], brief=True)
                        if one_card:
                            out.append(one_card)
                        continue
                    # the card wants the same shape the shelves give it: the
                    # programme, the season, how many episodes it holds and when it
                    # last gained one
                    row = con.execute(
                        """SELECT e.item_id, e.season, COUNT(*) episodes,
                                  MAX(COALESCE(f.ctime, f.mtime)) added
                           FROM episode e JOIN file f ON f.episode_id=e.id
                           WHERE e.item_id=? AND e.season=?""",
                        where).fetchone()
                    card = self._season_card(con, row) if row else None
                    if card:
                        card["ratingKey"] = "%d-s%d" % where
                        whole = counted.get(where, len(mine))
                        card["leafCount"] = whole
                        card["title"] = card.get("title") or "Season %d" % where[1]
                        # what is actually on the shelf, when it is not the whole thing
                        if len(mine) < whole:
                            card["shelfCount"] = len(mine)
                        out.append(card)
                    continue
            if key.isdigit():
                # marked before a whole programme was kept as its episodes: the shelf
                # still holds the show itself, and it reads as seasons too
                seasons = con.execute(
                    """SELECT e.item_id, e.season, COUNT(*) episodes,
                              MAX(COALESCE(f.ctime, f.mtime)) added
                       FROM episode e JOIN file f ON f.episode_id=e.id
                       WHERE e.item_id=? GROUP BY e.season ORDER BY e.season""",
                    (int(key),)).fetchall()
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
            if self._watched(con, "e%d" % e["id"]))
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
            "thumb": "/art/%s/poster" % key if show["poster"] else None,
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
            if r["atracks"] is not None:
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
            row = con.execute("SELECT height, vcodec FROM file WHERE id=?",
                              (int(file_id),)).fetchone()
        except Exception:
            return ""
        finally:
            con.close()
        if not row:
            return ""
        h = row["height"] or 0
        label = "4K" if h >= 1700 else ("%dp" % h if h else "")
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

    def _page(self, items, q):
        # A window into a long list. An older app that asks in the older way gets the
        # whole list rather than a page of it, which is heavier and still correct.
        def asked(name, fallback):
            return int(q[name][0] or fallback) if q.get(name) else fallback
        start = asked("start", 0)
        size = asked("count", len(items))
        return {"size": len(items[start:start + size]), "totalSize": len(items),
                "Metadata": items[start:start + size]}

    def metadata_for(self, con, key, brief=False):
        if key.startswith("e"):
            row = con.execute("SELECT * FROM episode WHERE id=?", (int(key[1:]),)).fetchone()
            return self._episode(con, row, brief=brief) if row else None
        season = re.match(r"^(\d+)-s(\d+)$", key)
        if season:
            show = con.execute("SELECT * FROM item WHERE id=?", (int(season.group(1)),)).fetchone()
            return self._show(con, show) if show else None
        if key.isdigit():
            row = con.execute("SELECT * FROM item WHERE id=?", (int(key),)).fetchone()
            if not row:
                return None
            return self._movie(con, row, brief=brief) if row["type"] == "movie" \
                else self._show(con, row)
        return None

    # ---- playback -----------------------------------------------------------
    def file_for(self, key, media_index=0):
        """The file behind a rating key, for direct play or for the ffmpeg engine.

        Nothing for a key that is not one: keys arrive from clients, and "l12" or an
        empty string is a question with no answer rather than something to fall over.
        """
        key = str(key or "")
        if not (key[1:] if key.startswith("e") else key).isdigit():
            return None
        con = self.lib.db()
        try:
            if key.startswith("e"):
                rows = con.execute("SELECT * FROM file WHERE episode_id=?", (int(key[1:]),)).fetchall()
            else:
                rows = con.execute("SELECT * FROM file WHERE item_id=? AND episode_id IS NULL",
                                   (int(key),)).fetchall()
            if not rows:
                return None
            # the same order the client was given, or version 1 of two means one file
            # for the picture and the other for its subtitles
            rows = best_first(rows)
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
                    "title": os.path.basename(r["path"])}
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
                    (int(str(key)[1:]),)).fetchone()
                if not row:
                    return None, None, None
                return (row["ep"] or row["show"],
                        "%s  S%02dE%02d" % (row["show"], row["season"] or 0,
                                            row["number"] or 0),
                        # 320 wide: the panel can only halve or quarter an image, so a
                        # 500-wide poster would land at 250 in a 320-wide slot
                        "/local/art/%d/poster?w=320" % row["show_id"] if row["poster"]
                        else None)
            row = con.execute("SELECT id, title, year, poster FROM item WHERE id=?",
                              (int(key),)).fetchone()
            if not row:
                return None, None, None
            return (row["title"],
                    str(row["year"]) if row["year"] else None,
                    "/local/art/%d/poster?w=320" % row["id"] if row["poster"] else None)
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
                               (int(str(key)[1:]),)).fetchone()
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
        return "e%d" % row["id"] if row else None

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
                              (int(key[1:]),)).fetchone()
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
                               (int(str(key)[1:]),)).fetchone()
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
        return ("e%d" % nxt["id"]) if nxt else None

    def title_for(self, key):
        """A title a person would recognise, for the list of what is being watched."""
        con = self.lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT e.title AS ep, e.season, e.number, i.title AS show "
                    "FROM episode e JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (int(str(key)[1:]),)).fetchone()
                if not row:
                    return "something"
                return "%s S%dE%d - %s" % (row["show"], row["season"] or 0,
                                           row["number"] or 0, row["ep"] or "")
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (int(key),)).fetchone()
            if not row:
                return "something"
            return row["title"] + (" (%d)" % row["year"] if row["year"] else "")
        except Exception:
            return "something"
        finally:
            con.close()

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
                (int(key[1:]),)).fetchone() if key[1:].isdigit() else None
            if not row:
                return None
            return {"type": "episode", "show": row["title"],
                    "season": row["season"] or 0, "episode": row["number"] or 0}
        if key.isdigit():
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (int(key),)).fetchone()
            if not row:
                return None
            return {"type": "movie", "title": row["title"], "year": row["year"] or 0}
        return None

    def key_of(self, con, said):
        """The other way about: what this library calls the thing described."""
        from pd_library import flatten_title
        if not isinstance(said, dict):
            return ""
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
                    return "e%d" % row["id"]
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

    def take_progress(self, rows):
        """Write down where other machines say these viewings had got to.

        Only what this library actually holds, and only where the other machine's
        note is the newer one: two servers in one house are two people writing in the
        same book, and the last one to watch is the one who was right.
        """
        con = self.lib.db()
        taken = 0
        try:
            for row in rows or []:
                key = self.key_of(con, row.get("is") or {})
                if not key:
                    continue
                who = str(row.get("who") or "me")
                when = int(row.get("updated") or 0)
                had = con.execute(
                    "SELECT updated FROM progress WHERE who=? AND key=?",
                    (who, key)).fetchone()
                if had and int(had["updated"] or 0) >= when:
                    continue
                con.execute(
                    """INSERT INTO progress (key, position, duration, updated, who)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(who, key) DO UPDATE SET
                       position=excluded.position, duration=excluded.duration,
                       updated=excluded.updated""",
                    (key, int(row.get("position") or 0),
                     int(row.get("duration") or 0), when, who))
                taken += 1
            con.commit()
        finally:
            con.close()
        return taken

    def progress_of(self, who_list, since=0, limit=60):
        """Where these viewers had got to, described so another library can place it."""
        con = self.lib.db()
        out = []
        try:
            for who in who_list or []:
                for row in con.execute(
                        """SELECT key, position, duration, updated FROM progress
                           WHERE who = ? AND updated > ? AND position > 30
                             AND position < duration * 0.95
                           ORDER BY updated DESC LIMIT ?""",
                        (who, int(since), int(limit))):
                    said = self.what_it_is(con, row["key"])
                    if not said:
                        continue
                    out.append({"who": who, "is": said,
                                "position": int(row["position"] or 0),
                                "duration": int(row["duration"] or 0),
                                "updated": int(row["updated"] or 0)})
        finally:
            con.close()
        return out

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
        return ("e%d" % row["episode_id"]) if row["episode_id"] else str(row["item_id"])

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
        return self.title_for("e%d" % row["episode_id"] if row["episode_id"]
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
