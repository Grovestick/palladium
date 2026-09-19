"""What has just arrived on streaming, from Rotten Tomatoes.

A row of films nobody here holds yet. They cannot be played and they are not on any
pack - the only thing to do with one is ask for it, which is what the button on its
page does.

The list is read from Rotten Tomatoes' "movies at home", newest first, and each title
is matched against TMDB so it has a poster and a page like anything else. Titles this
library already holds are dropped: a row of what is new is no use if half of it is
already on the shelf.

Kept on disk with the hour it was read, because the page is fetched rarely and the
server is restarted often.
"""

import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.request
import html as htmlmod

#: where it is read from, and how often. Twice a day is far more often than a
#: streaming service adds anything.
SOURCE = "https://www.rottentomatoes.com/browse/movies_at_home/sort:newest"
EVERY = 12 * 3600.0
#: how many of its pages to read. Each one answers with everything up to it, so the
#: fourth is the first hundred and twelve titles.
PAGES = 4
KEEP = 120

STATE = {"root": "", "rows": [], "at": 0.0, "busy": False}
LOCK = threading.Lock()


def use_data_dir(root):
    STATE["root"] = root


def _path():
    return os.path.join(STATE["root"] or ".", "streaming.json")


def key_for(slug):
    """A key no library title and no pack can have: "rt" and ten hex."""
    return "rt" + hashlib.sha1(str(slug or "").encode("utf-8")).hexdigest()[:10]


def read():
    """What was read last time, from disk once."""
    with LOCK:
        if STATE["rows"] or STATE["at"]:
            return STATE["rows"]
        try:
            with io.open(_path(), encoding="utf-8") as f:
                said = json.load(f) or {}
            STATE["rows"] = said.get("rows") or []
            STATE["at"] = float(said.get("at") or 0)
        except (OSError, ValueError):
            STATE["rows"], STATE["at"] = [], 0.0
        return STATE["rows"]


def _write():
    if not STATE["root"]:
        return
    tmp = _path() + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": STATE["at"], "rows": STATE["rows"]}, f)
        os.replace(tmp, _path())
    except OSError:
        pass


def _page(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                      "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as answer:
        return answer.read().decode("utf-8", "replace")


TILE = re.compile(r'<poster-tile\b[^>]*media-url="([^"]+)"[^>]*>(.*?)</poster-tile>',
                  re.S)
ALT = re.compile(r'alt="([^"]*)"')
YEAR = re.compile(r"_(\d{4})$")


def scrape():
    """Title, year and where it sits on Rotten Tomatoes - nothing else read.

    The last page is asked for and holds every one before it, so one reading is
    enough; earlier pages are only read if that one fails.
    """
    page = ""
    for n in range(PAGES, 0, -1):
        try:
            page = _page(SOURCE + ("?page=%d" % n if n > 1 else ""))
            break
        except Exception:
            continue
    out = []
    for slug, body in TILE.findall(page):
        name = ALT.search(body)
        if not name:
            continue
        title = htmlmod.unescape(name.group(1)).strip()
        if not title:
            continue
        year = YEAR.search(slug)
        out.append({"title": title,
                    "year": int(year.group(1)) if year else None,
                    "slug": slug,
                    "key": key_for(slug)})
    return out


def _held(lib):
    """What this library already has, as flattened title and year."""
    from pd_library import flatten_title
    try:
        con = lib.db()
    except Exception:
        return set()
    try:
        return {(flatten_title(r["title"]), int(r["year"] or 0))
                for r in con.execute(
                    "SELECT title, year FROM item WHERE type='movie'")}
    finally:
        con.close()


#: TMDB's numbers for categories, read once so a title can carry the words
GENRES = {}


def _genres(lib):
    """What TMDB calls each category, by its number."""
    if GENRES:
        return GENRES
    try:
        for one in (lib.tmdb("/genre/movie/list") or {}).get("genres") or []:
            GENRES[one.get("id")] = one.get("name")
    except Exception:
        pass
    return GENRES


def _looked_up(lib, one):
    """The same film in TMDB, for a poster and the words on its page."""
    try:
        said = lib.tmdb("/search/movie", query=one["title"],
                        **({"year": one["year"]} if one.get("year") else {}))
    except Exception:
        return None
    for found in (said.get("results") or [])[:4]:
        made = str(found.get("release_date") or "")[:4]
        made = int(made) if made.isdigit() else 0
        # The year has to agree where both know it. Searching for a film new this year
        # by name alone answered with a film of the same name from nineteen eighty-nine,
        # and its poster would have gone on the row as though it were the new one.
        if one.get("year") and made and abs(made - int(one["year"])) > 1:
            continue
        return {"tmdb": found.get("id"),
                "genres": [GENRES.get(g) for g in (found.get("genre_ids") or [])
                           if GENRES.get(g)],
                "poster": found.get("poster_path"),
                "backdrop": found.get("backdrop_path"),
                "overview": found.get("overview") or "",
                "rating": found.get("vote_average"),
                "released": found.get("release_date") or "",
                "year": (int(str(found.get("release_date"))[:4])
                         if str(found.get("release_date") or "")[:4].isdigit()
                         else one.get("year"))}
    return None


def refresh(lib, force=False):
    """Read the page again, unless it was read recently. Returns how many are held."""
    read()
    if not force and time.time() - STATE["at"] < EVERY and STATE["rows"]:
        return len(STATE["rows"])
    with LOCK:
        if STATE["busy"]:
            return len(STATE["rows"])
        STATE["busy"] = True
    try:
        from pd_library import flatten_title
        found = scrape()
        _genres(lib)
        here = _held(lib)
        rows = []
        for one in found:
            if len(rows) >= KEEP:
                break
            flat = flatten_title(one["title"])
            # already on the shelf, by name and year or by name alone where the year
            # is not known: a row of what is new should hold nothing already here
            if any(flat == name and (not one["year"] or not year
                                     or abs(year - one["year"]) <= 1)
                   for name, year in here):
                continue
            more = _looked_up(lib, one)
            if more:
                one = dict(one, **more)
            rows.append(one)
        with LOCK:
            STATE["rows"] = rows
            STATE["at"] = time.time()
            _write()
        return len(rows)
    except Exception:
        return len(STATE["rows"])
    finally:
        STATE["busy"] = False


def item(one):
    """One of them, shaped the way the library shapes a film."""
    key = one["key"]
    return {
        "ratingKey": key, "type": "movie",
        "title": one.get("title") or "",
        "year": one.get("year"),
        "summary": one.get("overview") or "",
        "rating": one.get("rating"),
        "genres": list(one.get("genres") or []),
        "originallyAvailableAt": one.get("released") or "",
        "duration": 0, "viewCount": 0, "addedAt": 0,
        "thumb": ("/art/%s/poster" % key) if one.get("poster") else None,
        "art": ("/art/%s/backdrop" % key) if one.get("backdrop") else None,
        #: not here, not on a pack: the only thing to do with it is ask
        "askable": True,
        "where": "https://www.rottentomatoes.com" + str(one.get("slug") or ""),
    }


def rows():
    """The row, newest first."""
    return [item(one) for one in read()]


def one_of(key):
    """The title behind one of these keys, as it was read."""
    return next((one for one in read() if one.get("key") == str(key)), None)


def art_of(key, kind):
    """The TMDB path of its poster or backdrop."""
    one = one_of(key) or {}
    return one.get("poster" if kind == "poster" else "backdrop")
