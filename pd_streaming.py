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
BROWSE = "https://www.rottentomatoes.com/browse/movies_at_home/"
#: Newest first, and what people are actually watching at home beside it. Newest on
#: its own is mostly films nobody has heard of - a week of small releases - so a
#: title anybody would recognise arrived buried or not at all.
SOURCE = BROWSE + "sort:newest"
POPULAR = BROWSE + "sort:popular"
EVERY = 12 * 3600.0
#: how many of its pages to read. Each answers with everything up to it, so the
#: twelfth is the first 336 titles in one request. Four did not reach two years back.
PAGES = 12
KEEP = 400
#: how many people must have rated a film for it to count as one anybody would
#: recognise. Everything Rotten Tomatoes lists arrives; most of it nobody has seen.
KNOWN_VOTES = 60
#: how far back a film may have been released and still count as new. The popular
#: list carries whatever is watched at home, 1998 releases included.
NEW_MONTHS = 24

STATE = {"root": "", "rows": [], "at": 0.0, "busy": False,
         #: how far back the list in hand was read for, in months. A narrower window
         #: is answered by sifting what is already here; a wider one has to be read
         #: again, because what is not in the list cannot be filtered into it.
         "months": NEW_MONTHS}
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
            # a cache left by a build that read programmes as well: the row is
            # films, and a season drawn as one is a poster with no title on it
            STATE["rows"] = [r for r in (said.get("rows") or [])
                             if r.get("kind") in (None, "movie")]
            STATE["at"] = float(said.get("at") or 0)
            STATE["months"] = int(said.get("months") or NEW_MONTHS)
        except (OSError, ValueError):
            STATE["rows"], STATE["at"] = [], 0.0
        return STATE["rows"]


def _write():
    if not STATE["root"]:
        return
    tmp = _path() + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": STATE["at"], "rows": STATE["rows"],
                       "months": STATE.get("months") or NEW_MONTHS}, f)
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
    pages = []
    # what people are watching first, the newest behind it: a row that opens on a
    # week of releases nobody has heard of reads as having nothing in it
    # the popular list is one page - asking for a second answers 404 - so depth
    # comes from the newest list and from the catalogue below
    for where, deep in ((POPULAR, 1), (SOURCE, PAGES)):
        for n in range(deep, 0, -1):
            try:
                pages.append(_page(where + ("?page=%d" % n if n > 1 else "")))
                break
            except Exception:
                continue
    out, seen = [], set()
    for slug, body in [m for page in pages for m in TILE.findall(page)]:
        if slug in seen:
            continue
        seen.add(slug)
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


#: a date the catalogue gives, as it gives it. Compared as text against the oldest
#: date still new: time.mktime raises OverflowError on a film released before 1970,
#: and the popular list carries those.
DATE = re.compile(r"\d{4}-\d\d-\d\d$")


def _by_name(held, mark, flat, year):
    """The library's key for a title by name, allowing a year either side.

    Where neither side knows the year the name alone decides.
    """
    by_name, by_any = held
    year = int(year or 0)
    if not year:
        return by_any.get((mark, flat))
    # year 0 last: a library entry whose year nobody knows matches on the name alone
    for n in (year, year - 1, year + 1, 0):
        key = by_name.get((mark, flat, n))
        if key:
            return key
    return None


def _held(lib):
    """What this library already has: by catalogue number, and by name and year.

    The number is the one that works: the website spells a title with the franchise
    in front of it and the library without, so the words say two films where the
    catalogue gives one number.
    """
    from pd_library import flatten_title
    by_tmdb, by_name, by_any = {}, {}, {}
    try:
        con = lib.db()
    except Exception:
        return by_tmdb, (by_name, by_any)
    try:
        for r in con.execute("SELECT id, title, year, tmdb_id, type FROM item"):
            # a programme and a film can carry the same catalogue number, so they are
            # kept apart by kind
            mark = ("show" if r["type"] == "show" else "movie")
            if r["tmdb_id"]:
                by_tmdb[(mark, int(r["tmdb_id"]))] = str(r["id"])
            flat = flatten_title(r["title"])
            by_name[(mark, flat, int(r["year"] or 0))] = str(r["id"])
            by_any.setdefault((mark, flat), str(r["id"]))
        return by_tmdb, (by_name, by_any)
    finally:
        con.close()


#: TMDB's numbers for categories, read once so a title can carry the words.
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
                "votes": int(found.get("vote_count") or 0),
                "known": float(found.get("popularity") or 0),
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


def _from_the_catalogue(lib, months, want):
    """Films released in the last while, from TMDB, best known first.

    Rotten Tomatoes says what has arrived to watch at home, which is the right
    question - but its popular list is a single page and its newest list reaches back
    a few weeks, so between them they cannot fill two years. The catalogue can: asked
    for what came out since a date, with enough people having rated it to count as a
    film anybody has heard of.
    """
    out = []
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - months * 30.5 * 86400))
    for page in range(1, 9):
        if len(out) >= want:
            break
        try:
            said = lib.tmdb("/discover/movie",
                            sort_by="popularity.desc",
                            include_adult="false", include_video="false",
                            page=page,
                            **{"primary_release_date.gte": since,
                               "primary_release_date.lte": time.strftime("%Y-%m-%d"),
                               "vote_count.gte": KNOWN_VOTES})
        except Exception:
            break
        found = said.get("results") or []
        if not found:
            break
        for one in found:
            when = str(one.get("release_date") or "")[:10]
            if len(when) != 10:
                continue
            out.append({
                "title": one.get("title") or "",
                # no slug: these never came from the website, and a made-up one is a
                # link to a page that is not there
                "key": key_for("tmdb:%s" % one.get("id")),
                "tmdb": one.get("id"),
                "votes": int(one.get("vote_count") or 0),
                "genres": [GENRES.get(g) for g in (one.get("genre_ids") or [])
                           if GENRES.get(g)],
                "poster": one.get("poster_path"),
                "backdrop": one.get("backdrop_path"),
                "overview": one.get("overview") or "",
                "rating": one.get("vote_average"),
                "released": when,
                "year": int(when[:4]),
            })
    return out


def window():
    """How far back the list in hand reaches, in months."""
    read()                                   # loads the file if it is not in hand
    return int(STATE.get("months") or NEW_MONTHS)


def refresh(lib, force=False, months=None):
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
        by_tmdb, held = _held(lib)
        # the oldest release date still counted as new, compared as text
        months = int(months or NEW_MONTHS)
        oldest = time.strftime("%Y-%m-%d",
                               time.localtime(time.time() - months * 30.5 * 86400))
        rows = []
        for one in found:
            if len(rows) >= KEEP:
                break
            flat = flatten_title(one["title"])
            more = _looked_up(lib, one)
            if not more:
                continue        # nothing in the catalogue knows it: nor would anybody
            one = dict(one, **more)
            # Films people have heard of. Rotten Tomatoes lists everything that
            # arrives, which is a week of releases nobody has seen - the number of
            # people who have rated a film is the plainest measure of whether it is
            # one worth being offered.
            if int(one.get("votes") or 0) < KNOWN_VOTES:
                continue
            # and whether this house already holds it. One that is here is not a thing
            # to ask for: it is a film with a poster and a Play button, listed here
            # because it is new rather than because it is missing.
            # a programme can carry a film's catalogue number, so the library is
            # asked for a film
            mine = by_tmdb.get(("movie", int(one.get("tmdb") or 0)))
            if not mine:
                # by_name is keyed by the year too, so the years worth trying are
                # looked up rather than the whole library walked for each title
                mine = _by_name(held, "movie", flat, one.get("year"))
            if mine:
                one = dict(one, here=mine)
            # and released recently enough to be new. A date the catalogue does not
            # know is taken on trust: it is rarer than an old film on the popular list.
            when = str(one.get("released") or "")[:10]
            if DATE.match(when) and when < oldest:
                continue
            rows.append(one)
        # newest first: the row is about what has just arrived, and the lists it is
        # read from are in two different orders
        # and the catalogue behind them, for the depth the website cannot reach
        known = {int(one.get("tmdb") or 0) for one in rows}
        for one in _from_the_catalogue(lib, months, KEEP - len(rows)):
            if len(rows) >= KEEP:
                break
            if int(one.get("tmdb") or 0) in known:
                continue
            known.add(int(one.get("tmdb") or 0))
            mark = "movie"
            mine = by_tmdb.get((mark, int(one.get("tmdb") or 0)))
            if not mine:
                mine = _by_name(held, mark, flatten_title(one["title"]),
                                one.get("year"))
            if mine:
                one = dict(one, here=mine)
            rows.append(one)
        rows.sort(key=lambda one: str(one.get("released") or "")[:10], reverse=True)
        with LOCK:
            STATE["rows"] = rows
            STATE["at"] = time.time()
            # and how far back this reading went. Left unwritten, the file kept saying
            # two years however deep the reading had been - so every later ask for a
            # wider window read the whole thing again, from the beginning, while
            # somebody waited on it.
            STATE["months"] = int(months)
            _write()
        return len(rows)
    except Exception:
        return len(STATE["rows"])
    finally:
        STATE["busy"] = False


def held_dates():
    """When each film this house holds actually came out, by its library key.

    The catalogue gives a date to the day; the library keeps only a year, so a list
    asked for "most recently released" could do no better than put every film of the
    same year in alphabetical order. These are the dates already read for the new
    arrivals row - a few dozen of them, and they are exactly the recent end of the
    shelf, which is the part that wants ordering.
    """
    out = {}
    for one in read():
        here, when = one.get("here"), one.get("released")
        if here and when:
            out[str(here)] = str(when)
    return out


def _where(one):
    """The page to read about it: the website's if it came from there, else TMDB's."""
    slug = str(one.get("slug") or "")
    if slug:
        return "https://www.rottentomatoes.com" + slug
    return ("https://www.themoviedb.org/movie/%s" % one["tmdb"]
            if one.get("tmdb") else "")


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
        "where": _where(one),
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
