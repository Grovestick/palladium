#!/usr/bin/env python3
"""Fetching subtitles for a film that has none.

OpenSubtitles is the only source worth asking. Their v1 API wants two things:

    an API key   identifies the application; free, from opensubtitles.com/consumers
    a login      identifies the person, and carries the daily download allowance

The key alone is enough to *search*, which is why searching works as soon as a key is
pasted in and downloading asks for the login separately.

Matching is by the file's own hash where possible rather than by its name. The hash is
OpenSubtitles' own: the file size plus the first and last 64 kB, read as 64-bit words.
It identifies a particular release exactly, which is what makes the timing right - a
subtitle matched by title alone is frequently out by several seconds, having been made
for a different cut.
"""
import json
import time
import os
import re
import struct
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.opensubtitles.com/api/v1"
AGENT = "Palladium v1.0"


def file_hash(path):
    """OpenSubtitles' hash: size + the first and last 64 kB as 64-bit words."""
    chunk = 65536
    try:
        size = os.path.getsize(path)
        if size < chunk * 2:
            return None, size
        value = size
        with open(path, "rb") as f:
            for _ in range(2):
                for _ in range(chunk // 8):
                    value = (value + struct.unpack("<q", f.read(8))[0]) & 0xFFFFFFFFFFFFFFFF
                f.seek(max(0, size - chunk), os.SEEK_SET)
        return "%016x" % value, size
    except (OSError, struct.error):
        return None, 0


class OpenSubtitles:
    def __init__(self, key, user="", password=""):
        self.key = (key or "").strip()
        self.user = (user or "").strip()
        self.password = password or ""
        self.token = ""

    def _call(self, path, data=None, token=False):
        headers = {"Api-Key": self.key, "User-Agent": AGENT,
                   "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            API + path, headers=headers,
            data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def login(self):
        """Only needed to download; searching works with the key alone."""
        if self.token:
            return True
        if not (self.user and self.password):
            return False
        held = TOKENS.get(self.user)
        if held and time.time() - held[1] < 12 * 3600:
            self.token = held[0]
            return True
        # logins are rate limited; a new client per download was refused mid-run
        for wait in (0, 2, 5):
            time.sleep(wait)
            try:
                out = self._call("/login", {"username": self.user,
                                            "password": self.password})
                self.token = out.get("token", "")
            except Exception:
                continue
            if self.token:
                TOKENS[self.user] = (self.token, time.time())
                return True
        return False

    def search(self, title, year=None, languages="en", path=None, season=None,
               episode=None, imdb=""):
        """Candidates for one title, best match first.

        The hash is sent when the file can be read: an exact release match beats a
        title match, and OpenSubtitles ranks it accordingly.
        """
        # By number where the library has one. A name is parsed at the other end,
        # and a title ending in four digits is read as a year: the search asks for a
        # film from a year that has not happened and finds nothing at all, where the
        # same film by its id answers with sixty-five. Any title that is a year, or
        # ends in one, falls into the same trap.
        digits = "".join(c for c in str(imdb or "") if c.isdigit())
        if digits:
            q = {"languages": languages}
            q["parent_imdb_id" if season is not None else "imdb_id"] = digits
        else:
            q = {"languages": languages, "query": title}
        # A year narrows a film usefully, but for an episode it is the year the series
        # began, and OpenSubtitles compares it against the episode's own - so asking
        # for season 3 of a show that started in 2010 returns nothing at all.
        if year and season is None and not digits:
            q["year"] = str(year)
        if season is not None:
            q["season_number"] = str(season)
        if episode is not None:
            q["episode_number"] = str(episode)
        if path:
            h, size = file_hash(path)
            if h:
                q["moviehash"] = h
        try:
            out = self._call("/subtitles?" + urllib.parse.urlencode(q))
        except urllib.error.HTTPError as e:
            return [], "OpenSubtitles said %d - %s" % (e.code, e.reason)
        except Exception as e:
            return [], str(e)[:100]

        found = []
        for row in (out.get("data") or [])[:20]:
            a = row.get("attributes", {})
            files = a.get("files") or []
            if not files:
                continue
            found.append({
                "id": files[0].get("file_id"),
                "name": a.get("release") or files[0].get("file_name") or "subtitle",
                "language": a.get("language"),
                "downloads": a.get("download_count", 0),
                "hearing": bool(a.get("hearing_impaired")),
                "fromHash": bool(a.get("moviehash_match")),
                "uploader": (a.get("uploader") or {}).get("name", ""),
            })
        # an exact release match first, then whatever the most people have taken
        found.sort(key=lambda f: (not f["fromHash"], -f["downloads"]))
        return found, ""

    def download(self, file_id):
        """The subtitle itself, as text. Requires the login and its daily allowance."""
        if not self.login():
            if self.user and self.password:
                return None, "OpenSubtitles did not accept the login just now."
            return None, "Downloading needs the OpenSubtitles username and password."
        try:
            try:
                out = self._call("/download", {"file_id": int(file_id)}, token=True)
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                # a kept token that has expired: log in again once
                TOKENS.pop(self.user, None)
                self.token = ""
                if not self.login():
                    raise
                out = self._call("/download", {"file_id": int(file_id)}, token=True)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = json.loads(e.read().decode("utf-8", "replace")).get("message", "")
            except Exception:
                pass
            return None, "OpenSubtitles refused: %s" % (body or e.reason)
        except Exception as e:
            return None, str(e)[:100]
        link = out.get("link")
        if out.get("remaining") is not None:
            ALLOWANCE.update(left=int(out.get("remaining") or 0), at=time.time())
        if not link:
            return None, "No download link came back (allowance used up?)."
        try:
            req = urllib.request.Request(link, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except Exception as e:
            return None, str(e)[:100]
        if not raw.strip():
            # it happens: an entry whose file is empty on their side. Saying so beats
            # handing back an empty string and a blank error.
            return None, "that subtitle came back empty"
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding), ""
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace"), ""


#: login token per user, with when it was issued; shared by every client
TOKENS = {}

#: what OpenSubtitles said was left of today's downloads, and when it said it
ALLOWANCE = {"left": None, "at": 0.0}


def release_tag(release):
    """A short, filename-safe piece of a release name, to tell variants apart."""
    # some releases are named after the file they came in; the extension is noise
    text = re.sub(r"\.(srt|vtt|ass|ssa)$", "", (release or "").strip(),
                  flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text[:34].strip("-")


def sidecar_path(video, language, release=""):
    """Where a downloaded subtitle belongs: beside the film, named for its language.

    A subtitle that says which release it came from can sit beside the last one rather
    than replacing it, which is the difference between trying three of them and being
    left with whichever was tried last. The language stays the final piece of the name
    because that is what every player reads it by.

    Windows stops at 260 characters and these file names are long already, so the tag
    is dropped rather than truncated into nonsense when there is no room for it.
    """
    stem = os.path.splitext(video)[0]
    lang = (language or "en").lower()[:5]
    tag = release_tag(release)
    if tag:
        named = "%s.%s.%s.srt" % (stem, tag, lang)
        if len(named) <= 250:
            return named
    return "%s.%s.srt" % (stem, lang)


# ---------------------------------------------------------------- what to check next

#: a subtitle found good is not looked at again
GOOD = ("fits", "verified", "inside")
#: the provider said no for today: asked again tomorrow, whoever asks
TOMORROW = ("login", "allowance")


def due(done, first, rest, languages, asked, now, again, stop_at_one=False):
    """What is left to check, first to last: (key, language, fetch).

    `done` is what each (key, language) was last found to be and when. `first` are
    the titles worth fetching a subtitle for, in order; `rest` the library's others,
    whose own subtitle is only measured. One found good is never looked at again; one
    that was not is left `again` seconds before another try.

    `asked` are the (key, language) pairs somebody asked to have gone through. They
    come before everything, a subtitle is fetched for them, and the wait since the
    last try does not hold them back - that try is what they asked to have redone.
    Only the provider's own refusal for the day still waits for tomorrow.
    """
    out, seen = [], set()

    def take(key, lang, fetch, forced):
        if (key, lang) in seen:
            return False
        at, verdict = done.get((key, lang), (0, ""))
        if verdict in GOOD:
            return False
        wait = 86400 if verdict in TOMORROW else (0 if forced else again)
        if at and now - at < wait:
            return False
        seen.add((key, lang))
        out.append((key, lang, fetch))
        return True

    for key, lang in asked or []:
        if take(str(key), str(lang), True, True) and stop_at_one:
            return out
    ahead = set(first)
    for fetch, keys in ((True, first), (False, [k for k in rest if k not in ahead])):
        for key in keys:
            for lang in languages:
                if take(key, lang, fetch, False) and stop_at_one:
                    return out
    return out
