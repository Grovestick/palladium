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
        try:
            out = self._call("/login", {"username": self.user, "password": self.password})
            self.token = out.get("token", "")
            return bool(self.token)
        except Exception:
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
            return None, "Downloading needs the OpenSubtitles username and password."
        try:
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
