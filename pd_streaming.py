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
#: and how well they thought of it. TMDB scores out of ten, and its average is the
#: share of people who liked a film expressed as a mark - so six is three out of five
#: rating it positively. Under that the shelf was offering a week of releases nobody
#: thought much of, ordered by nothing but the date they came out.
KNOWN_LIKED = 6.0
#: the same bar in the source's own terms: sixty per cent of its users
#: rating a film positively is what it calls fresh with the audience
LIKED_PERCENT = 60
#: how far ahead of today a release still belongs on the shelf. The list
#: this is read from carries the next few days, and a film everybody is
#: about to be able to watch is news in the same way one from yesterday is.
AHEAD_DAYS = 7
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


def _page(url, timeout=30):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                      "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as answer:
        return answer.read().decode("utf-8", "replace")


TILE = re.compile(r'<poster-tile\b[^>]*media-url="([^"]+)"[^>]*>(.*?)</poster-tile>',
                  re.S)
#: What the people who watched it thought, as the source itself puts it: the
#: share of its own users who rated a film positively. It sits beside the tile
#: rather than inside it - the tile holds the poster and a trailer button - so it
#: is read by the address the two share.
SCORES = re.compile(
    r'href="(/m/[^"]+)"[^>]*>\s*<score-pairs-deprecated>(.*?)</score-pairs-deprecated>',
    re.S)
AUDIENCE = re.compile(r'slot="audienceScore"[^>]*>\s*(\d+)%')
CRITICS = re.compile(r'slot="criticsScore"[^>]*>\s*(\d+)%')
ALT = re.compile(r'alt="([^"]*)"')
YEAR = re.compile(r"_(\d{4})$")


#: liked against not liked, on a title's own page. The browse tiles hide the
#: percentage for a film with few ratings - "Fewer than 50 Ratings" - and that is
#: exactly the case worth deciding: one such film sat at the top of the shelf with a
#: third of its viewers liking it, because the database it was looked up in had a
#: different opinion and a better mark.
LIKED_JSON = re.compile(
    r'"(audienceScore|criticsScore)":\{[^{}]{0,400}?"likedCount":(\d+),'
    r'"notLikedCount":(\d+)')
#: what was learned about each film's scores, kept between readings. A title page is
#: a request of its own and the list is hundreds long: asked once, remembered, and
#: never asked again unless it is still unknown.
ASKED = {}
#: how many title pages one reading may ask for. Without a cap a reading took longer
#: than the twelve hours between readings and never finished, so the list was never
#: sifted at all - which is the fault this number exists to prevent.
ASK_AT_MOST = 120
BUDGET = {"left": 0}
#: when each answer was learned, so a blank one can be asked again later
WHEN = {}


def _asked_path():
    return os.path.join(STATE.get("root") or ".", "meters.json")


#: how long a "nothing there" is believed. An address that answered with no scores
#: is usually the wrong address - a guessed one - but it can also be a film released
#: this week that nobody has rated yet, so it is asked again after a while.
BLANK_FOR = 7 * 86400
#: and how long a score is believed at all: a new release is judged on its first
#: handful of ratings, and those move for weeks
SCORES_FOR = 7 * 86400


def remember_meters():
    """Keep what was learned, misses included.

    Only the hits were kept at first, so every reading spent its whole budget
    retrying the same misses and never reached the films further down the list.
    """
    try:
        with open(_asked_path(), "w", encoding="utf-8") as f:
            json.dump({k: [v[0], v[1], int(WHEN.get(k) or time.time())]
                       for k, v in ASKED.items()}, f)
    except OSError:
        pass


def recall_meters():
    if ASKED:
        return
    try:
        with open(_asked_path(), encoding="utf-8") as f:
            said = json.load(f) or {}
    except (OSError, ValueError):
        return
    now = time.time()
    for k, v in said.items():
        if not (isinstance(v, list) and len(v) >= 2):
            continue
        when = int(v[2]) if len(v) > 2 else int(now)
        if v[0] is None and v[1] is None and now - when > BLANK_FOR:
            continue                      # worth asking again
        ASKED[k] = (v[0], v[1])
        WHEN[k] = when


def slug_for(title, year=0):
    """Where that film's page is, worked out from its name.

    Rows that came from the film database rather than from the website carry no
    address, and those were the ones never judged: the shelf fell back to the
    database's own mark, which is the number that disagreed in the first place. The
    address is the title in lower case with the awkward characters taken out, and the
    year after it where the bare one is taken.
    """
    flat = re.sub(r"[^a-z0-9]+", "_", str(title or "").lower()).strip("_")
    if not flat:
        return []
    # the year first: where two films share a name, the bare address is usually the
    # older one - a new release read its scores off a different film and fell off
    out = []
    if year:
        out.append("/m/%s_%d" % (flat, int(year)))
    out.append("/m/" + flat)
    return out


def meters_of(slug):
    """Both meters off a title's own page, as percentages: (audience, critics).

    Either may be None. A film with few ratings has its percentage left off the
    browse tiles, and that is the one worth asking about.
    """
    recall_meters()
    if slug in ASKED:
        fresh = time.time() - (WHEN.get(slug) or 0) < SCORES_FOR
        # read again once it is a week old, while this reading has requests to
        # spare; otherwise the last answer stands
        if fresh or BUDGET["left"] <= 0:
            return ASKED[slug]
    if BUDGET["left"] <= 0:
        return (None, None)               # next reading will ask for this one
    BUDGET["left"] -= 1
    got = {}
    try:
        page = _page("https://www.rottentomatoes.com" + slug, timeout=10)
        for which, liked, other in LIKED_JSON.findall(page):
            liked, other = int(liked), int(other)
            if which in got or liked + other <= 0:
                continue
            got[which] = int(round(100.0 * liked / (liked + other)))
    except Exception:
        got = {}
    pair = (got.get("audienceScore"), got.get("criticsScore"))
    ASKED[slug] = pair
    WHEN[slug] = int(time.time())
    remember_meters()
    return pair


def audience_of(slug):
    """What share of the people who rated it liked it, as a percentage, or None."""
    return meters_of(slug)[0]

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
    liked, judged, since = {}, {}, {}
    for page in pages:
        for slug, when in streaming_dates(page).items():
            since.setdefault(slug, when)
        for slug, block in SCORES.findall(page):
            got = AUDIENCE.search(block)
            if got:
                liked.setdefault(slug, int(got.group(1)))
            got = CRITICS.search(block)
            if got:
                judged.setdefault(slug, int(got.group(1)))
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
                    "liked": liked.get(slug),
                    "judged": judged.get(slug),
                    # the day the source says it came to be watched at home
                    "listed": since.get(slug) or "",
                    "key": key_for(slug)})
    return out


#: one tile of the source's list, and the date printed under it
_TILE_BLOCK = re.compile(r"<media-info-tile>(.*?)</media-info-tile>", re.S)
_TILE_SLUG = re.compile(r'media-url="([^"]+)"')
_TILE_DATE = re.compile(r'list-item-start-date"[^>]*>\s*Streaming\s+'
                        r'([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})')
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}


def streaming_dates(page):
    """Each title's at-home date as the source prints it under the tile: slug to
    "2026-09-25". Tile by tile, so a tile without a date takes no other tile's."""
    out = {}
    for block in _TILE_BLOCK.findall(page or ""):
        slug = _TILE_SLUG.search(block)
        when = _TILE_DATE.search(block)
        if not (slug and when) or when.group(1) not in _MONTHS:
            continue
        out.setdefault(slug.group(1), "%04d-%02d-%02d" % (
            int(when.group(3)), _MONTHS[when.group(1)], int(when.group(2))))
    return out


#: the catalogue's kinds of place to watch: included in a subscription or free, as
#: against a store that rents or sells the one film
_STREAMS = ("flatrate", "free", "ads")
_STORES = ("rent", "buy")


def providers_of(results, region="", most=5, only=(), strict=False):
    """Where a film can be watched, from the catalogue's answer by country: each
    service once, as {"name", "logo", "kind"} with kind "stream" or "store".

    `only` is the services chosen under Settings, by name: none chosen is all of
    them. `strict` keeps to `region` alone - the country chosen there - where the
    default looks everywhere and puts that country's first.

    Those in this house's own country first, then those in the most countries, and
    of two as widespread the one that carries it in a subscription before the shop
    that rents or sells it. A new release is often in one country's shops and no
    other's, so one country alone answered with nothing for most of them - and a
    service streaming it in a single country far away is not where it came out.
    """
    seen = {}
    wanted = {str(n).strip().lower() for n in only or () if str(n).strip()}
    for country, said in (results or {}).items():
        if not isinstance(said, dict) or (strict and country != region):
            continue
        for kinds, kind in ((_STREAMS, "stream"), (_STORES, "store")):
            for name in kinds:
                for p in said.get(name) or []:
                    ident = p.get("provider_id")
                    if ident is None or not p.get("provider_name"):
                        continue
                    if wanted and str(p["provider_name"]).strip().lower() not in wanted:
                        continue
                    # by its name: the catalogue files one service under several
                    # numbers, country by country, and it stood on the page twice
                    one = seen.setdefault(str(p["provider_name"]).strip().lower(), {
                        "name": str(p["provider_name"]),
                        "logo": str(p.get("logo_path") or ""),
                        "kind": kind, "countries": set(), "here": False,
                        "first": int(p.get("display_priority") or 999)})
                    if kind == "stream":
                        one["kind"] = "stream"
                    one["countries"].add(country)
                    if region and country == region:
                        one["here"] = True
    order = sorted(seen.values(), key=lambda o: (not o["here"], -len(o["countries"]),
                                                 o["kind"] != "stream", o["first"],
                                                 o["name"]))
    return [{"name": o["name"], "logo": o["logo"], "kind": o["kind"]}
            for o in order[:max(0, int(most))]]


#: The studios anybody knows by name, by the catalogue's number for each. One of
#: these stands first on a film's page, ahead of the companies set up to make it.
MAJORS = {
    33: "Universal Pictures", 174: "Warner Bros. Pictures", 4: "Paramount Pictures",
    2: "Walt Disney Pictures", 5: "Columbia Pictures", 25: "20th Century Fox",
    127928: "20th Century Studios", 21: "Metro-Goldwyn-Mayer", 1632: "Lionsgate",
    14: "Miramax", 12: "New Line Cinema", 7: "DreamWorks Pictures",
    521: "DreamWorks Animation", 3: "Pixar", 420: "Marvel Studios", 1: "Lucasfilm Ltd.",
    41077: "A24", 34: "Sony Pictures", 559: "TriStar Pictures", 9195: "Touchstone Pictures",
    10146: "Focus Features", 127929: "Searchlight Pictures", 43: "Fox Searchlight Pictures",
    60: "United Artists", 41: "Orion Pictures", 923: "Legendary Pictures",
    178464: "Netflix", 210099: "Amazon MGM Studios", 20580: "Amazon Studios",
    194232: "Apple Studios", 10342: "Studio Ghibli", 3172: "Blumhouse Productions",
    97: "Castle Rock Entertainment", 7405: "Dimension Films", 694: "StudioCanal",
    491: "Summit Entertainment", 6704: "Illumination",
    6125: "Walt Disney Animation Studios", 2251: "Sony Pictures Animation",
    56: "Amblin Entertainment", 90733: "NEON", 275: "Carolco Pictures",
    2785: "Warner Bros. Animation", 24955: "Paramount Animation",
    6: "RKO Radio Pictures", 6181: "SF Studios", 508: "Regency Enterprises",
    79: "Village Roadshow Pictures", 13184: "Annapurna Pictures", 7429: "HBO Films",
}
#: Known by name, but the company that paid for a film or made it for a studio rather
#: than the studio whose film it is: after the studios, ahead of everybody else.
BESIDE = {79, 508, 923, 56, 3172, 97, 275, 13184, 7429, 694, 491}
#: A label that was one of those studios under another name: the studio is what is
#: shown. A film released by Gramercy is a Universal film to anybody asking whose it is.
LABELS = {
    37: 33,          # Gramercy Pictures -> Universal Pictures
    10330: 33,       # Universal International Pictures -> Universal Pictures
    915: 2,          # Hollywood Pictures -> Walt Disney Pictures
    3287: 34,        # Screen Gems -> Sony Pictures
    11341: 34,       # Stage 6 Films -> Sony Pictures
    58: 34,          # Sony Pictures Classics -> Sony Pictures
    711: 25,         # Fox 2000 Pictures -> 20th Century Fox
    838: 4,          # Paramount Vantage -> Paramount Pictures
    11509: 174,      # Warner Independent Pictures -> Warner Bros. Pictures
}


def studios_of(companies, most=3, logo_of=None):
    """The studios behind a film, from the catalogue's list of who made it: each once,
    as {"name", "logo"} - the logo "" where there is none.

    A label stands as the studio it was a name for, the studios anybody knows come
    first, and the companies set up for the one film fill what room is left - those
    with a mark of their own before those without. `logo_of` answers a studio's mark
    by its number, for one that is shown in place of its label and so is not in the
    film's own list.
    """
    out, seen = [], set()
    for c in companies or []:
        c = c or {}
        name = str(c.get("name") or "").strip()
        logo = str(c.get("logo_path") or "")
        try:
            ident = int(c.get("id") or 0)
        except (TypeError, ValueError):
            ident = 0
        parent = LABELS.get(ident)
        if parent:
            ident, name, logo = parent, MAJORS.get(parent, name), ""
        elif ident in MAJORS:
            name = name or MAJORS[ident]
        if not name:
            continue
        mark = ("#%d" % ident) if ident in MAJORS else name.lower()
        if mark in seen or name.lower() in seen:
            continue
        seen.update((mark, name.lower()))
        out.append({"name": name, "logo": logo, "major": ident in MAJORS, "id": ident,
                    "beside": ident in BESIDE})
    # a studio standing in for its label takes its own mark - or the film's own entry
    # for it, when the film lists both
    listed = {}
    for c in companies or []:
        try:
            listed[int((c or {}).get("id") or 0)] = str(c.get("logo_path") or "")
        except (TypeError, ValueError):
            pass
    for one in out:
        if one["major"] and not one["logo"]:
            one["logo"] = listed.get(one["id"]) or ""
            if not one["logo"] and logo_of:
                try:
                    one["logo"] = str(logo_of(one["id"]) or "")
                except Exception:
                    one["logo"] = ""
    # stable: the film's own order within each
    out.sort(key=lambda s: (not s["major"], s["beside"], not s["logo"]))
    return [{"name": s["name"], "logo": s["logo"]} for s in out[:max(0, int(most))]]


#: the catalogue's countries and each country's services, kept a day
_WATCH = {}


def watch_regions(lib):
    """Every country the catalogue knows services for: [{"code", "name"}], by name."""
    had = _WATCH.get("regions")
    if not had or time.time() - had[0] > 86400:
        try:
            said = lib.tmdb("/watch/providers/regions").get("results") or []
        except Exception:
            said = []
        if not said:
            return had[1] if had else []
        had = (time.time(), sorted(
            ({"code": str(r.get("iso_3166_1") or ""),
              "name": str(r.get("english_name") or r.get("iso_3166_1") or "")}
             for r in said if r.get("iso_3166_1")), key=lambda r: r["name"]))
        _WATCH["regions"] = had
    return had[1]


def services_in_order(listed, region=""):
    """A country's services as the catalogue lists them, each name once, in the order
    it shows them there: [{"name", "logo"}]."""
    def place(p):
        return int((p.get("display_priorities") or {}).get(region,
                                                           p.get("display_priority") or 999))
    out, seen = [], set()
    for p in sorted(listed or [], key=lambda p: (place(p), str(p.get("provider_name") or ""))):
        name = str(p.get("provider_name") or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "logo": str(p.get("logo_path") or "")})
    return out


def watch_services(lib, region=""):
    """The services of one country - or of all, with none named - for choosing among."""
    region = str(region or "").upper()
    had = _WATCH.get("services:" + region)
    if not had or time.time() - had[0] > 86400:
        try:
            said = (lib.tmdb("/watch/providers/movie", watch_region=region) if region
                    else lib.tmdb("/watch/providers/movie")).get("results") or []
        except Exception:
            said = []
        if not said:
            return had[1] if had else []
        had = (time.time(), services_in_order(said, region))
        _WATCH["services:" + region] = had
    return had[1]


def dated_by_the_source(one):
    """A title with the source's own at-home date takes it as the date it came out.

    The catalogue's date is the cinema's: a film from 2004 that came to streaming last
    month was dropped as twenty years old, and one the catalogue had no at-home date
    for was held back as not out. The source lists what can be watched at home and
    says since when; that is the date this shelf is about. The first date is kept as
    "premiered".
    """
    listed = str(one.get("listed") or "")
    if not DATE.match(listed):
        return one
    # asked and answered: a date still ahead is not "not yet asked about"
    out = dict(one, home=listed, released=listed, homeChecked=time.time())
    if str(one.get("released") or "")[:10] not in ("", listed):
        out["premiered"] = str(one.get("released"))[:10]
    return out


def known_enough(votes, liked, judged):
    """Whether a title on the source's list is one to look at further.

    Scored by the source itself, it is: twenty of the twenty-eight films on its page
    were held back here for having under sixty votes in the catalogue, eight of them
    with a score on the very page they were read from. Only a title nobody has scored
    there still needs to be known here.
    """
    if liked is not None or judged is not None:
        return True
    try:
        return int(votes or 0) >= KNOWN_VOTES
    except (TypeError, ValueError):
        return False


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


def _plain(title):
    """A title as it is compared: its letters and digits, and "and" for "&"."""
    return re.sub(r"[^a-z0-9]+", "", str(title or "").lower().replace("&", " and "))


def _with_subtitle(short, long):
    """Whether one title is the other with a subtitle after it: "Drop 2" and
    "Drop 2: Lowpoint". Not merely beginning or ending the same - "Nights" is no
    short form of "30 Dates and 30 Nights"."""
    a, b = str(short or "").strip().lower(), str(long or "").strip().lower()
    if not a or len(b) <= len(a) or not b.startswith(a):
        return False
    return b[len(a):].lstrip()[:1] in (":", "-", "–", "—")


def pick_match(title, year, listed, results):
    """Which of the catalogue's answers to a title is the film meant, or None.

    The name has to be the film's name. Asked for "In the Mouth" the catalogue answers
    with a famous film of 1995 whose name begins that way, and asked for "Nights" with
    one of 1997 that ends in it: each went on the shelf of new releases under the
    other's poster. So: a film called exactly this, the year agreeing where the
    source gives one - the first of them as the catalogue lists them - and failing
    that a film that is this with a subtitle, or the other way about, but then only
    a recent one, new to cinemas within two years of the day it came to be watched
    at home. A longer-named film from decades back is another film.
    """
    want = _plain(title)
    if not want:
        return None
    try:
        year = int(year or 0)
    except (TypeError, ValueError):
        year = 0
    at_home = int(str(listed)[:4]) if str(listed or "")[:4].isdigit() else 0

    def made(found):
        y = str(found.get("release_date") or "")[:4]
        return int(y) if y.isdigit() else 0

    exact, near = [], []
    for found in results or []:
        names = {_plain(found.get("title")), _plain(found.get("original_title"))} - {""}
        y = made(found)
        # the year has to agree where both know it
        if year and y and abs(y - year) > 1:
            continue
        if want in names:
            exact.append(found)
        elif any(_with_subtitle(title, found.get(k)) or _with_subtitle(found.get(k), title)
                 for k in ("title", "original_title")):
            near.append(found)
    if exact:
        # the catalogue's own first of that name: it lists the one people mean first,
        # and the newest of a name is as often a short nobody has seen
        return exact[0]
    # no day it reached streaming: this year stands for it
    recent = at_home or time.localtime().tm_year
    for found in near:
        y = made(found)
        if year or (y and recent - 2 <= y <= recent + 1):
            return found
    # One answer and no other: the catalogue found it by a name it also goes by.
    # A film listed under one title and filed under another answers alone.
    only = [f for f in results or [] if not (year and made(f) and abs(made(f) - year) > 1)]
    if len(results or []) == 1 and len(only) == 1:
        return only[0]
    return None


#: what the catalogue said of a film, as a row keeps it
_FROM_CATALOGUE = ("tmdb", "votes", "known", "genres", "poster", "backdrop", "overview",
                   "rating", "year", "imdb")


def as_known(was):
    """What a row already says of its film, to stand in for a lookup that failed:
    the catalogue's answer as it was kept, with the date it first came out put back
    where the day it reached streaming has since been written over it. None when
    nothing of the film is known."""
    if not was or not was.get("tmdb"):
        return None
    out = {k: was[k] for k in _FROM_CATALOGUE if k in was}
    out["released"] = str(was.get("premiered") or was.get("released") or "")
    return out


def _looked_up(lib, one, was=None):
    """The same film in TMDB, for a poster and the words on its page.

    `was` is the row this title had at the last reading. The catalogue not answering
    is not the catalogue not knowing the film: eight films at the foot of the list
    went from the shelf for twelve hours because eight requests in a row failed, and
    came back at the next reading. A title already known stays as it was known.
    """
    try:
        said = lib.tmdb("/search/movie", query=one["title"],
                        **({"year": one["year"]} if one.get("year") else {}))
    except Exception:
        STATE["missed"] = int(STATE.get("missed") or 0) + 1
        return as_known(was)
    found = pick_match(one.get("title"), one.get("year"), one.get("listed"),
                       (said.get("results") or [])[:20])
    if found:
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
                               "primary_release_date.lte": time.strftime(
                                   "%Y-%m-%d",
                                   time.localtime(time.time()
                                                  + AHEAD_DAYS * 86400)),
                               "vote_count.gte": KNOWN_VOTES,
                               "vote_average.gte": KNOWN_LIKED})
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
                # filled in afterwards, a few at a time: the list call does not carry
                # it and asking per film while building the list would be a hundred
                # requests before anything could be shown
                "imdb": "",
            })
    return out


def window():
    """How far back the list in hand reaches, in months."""
    read()                                   # loads the file if it is not in hand
    return int(STATE.get("months") or NEW_MONTHS)


def keep_fresh(lib):
    """Read the list again on its own, rather than when somebody happens to look.

    It was refreshed by whoever opened the shelf after it had gone stale, which means
    the first person to look in the morning waited for it - and a house where nobody
    opens the app for a day had a day-old list waiting for them when they did.
    """
    def round_and_round():
        while True:
            try:
                read()
                if time.time() - (STATE.get("at") or 0) > EVERY:
                    refresh(lib)
                else:
                    learn_home(lib)       # and a film not out yet is looked at again
            except Exception:
                pass                      # the catalogue being away is not a failure
            time.sleep(900)

    threading.Thread(target=round_and_round, daemon=True).start()


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
        recall_meters()
        BUDGET["left"] = ASK_AT_MOST
        found = scrape()
        _genres(lib)
        by_tmdb, held = _held(lib)
        # the oldest release date still counted as new, compared as text
        months = int(months or NEW_MONTHS)
        oldest = time.strftime("%Y-%m-%d",
                               time.localtime(time.time() - months * 30.5 * 86400))
        rows = []
        had = {str(r.get("key")): r for r in STATE["rows"] if r.get("key")}
        STATE["missed"] = 0
        for one in found:
            if len(rows) >= KEEP:
                break
            flat = flatten_title(one["title"])
            more = _looked_up(lib, one, had.get(str(one.get("key"))))
            if not more:
                continue        # nothing in the catalogue knows it: nor would anybody
            one = dated_by_the_source(dict(one, **more))
            # Films people have heard of. Rotten Tomatoes lists everything that
            # arrives, which is a week of releases nobody has seen - the number of
            # people who have rated a film is the plainest measure of whether it is
            # one worth being offered - unless the source has scored it itself.
            if not known_enough(one.get("votes"), one.get("liked"), one.get("judged")):
                continue
            # and enough of them liked it. The source's own audience score
            # where it has one - the share of its users who rated the film
            # positively - and the database's mark out of ten where it has
            # not, which a film released this week often has not.
            said, judged = one.get("liked"), one.get("judged")
            if (said is None or judged is None) and not one.get("slug"):
                # no address of its own: try the one its name would have
                for guess in slug_for(one.get("title"), one.get("year")):
                    pair = meters_of(guess)
                    if any(x is not None for x in pair):
                        one["slug"] = guess
                        break
            if (said is None or judged is None) and one.get("slug"):
                # asked of its own page: the tile leaves a number out precisely where
                # it is worth having
                pair = meters_of(str(one["slug"]))
                said = said if said is not None else pair[0]
                judged = judged if judged is not None else pair[1]
            one["liked"], one["judged"] = said, judged
            # Kept if either meter vouches for it. Which of the two a viewer wants is
            # theirs to say and is applied where the shelf is built; throwing a film
            # away here would decide it for everybody.
            marks: list = [m for m in (said, judged) if m is not None]
            if marks:
                if max(marks) < LIKED_PERCENT:
                    continue
            else:
                try:
                    mark = float(one.get("rating") or 0)
                except (TypeError, ValueError):
                    mark = 0.0
                if mark < KNOWN_LIKED:
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
        if STATE.get("missed"):
            # said in the log: a shelf shorter than the source's is otherwise a puzzle
            try:
                with io.open(os.path.join(STATE["root"] or ".", "debug.log"), "a",
                             encoding="utf-8") as f:
                    f.write("%s new releases: the catalogue did not answer for %d of %d "
                            "titles; those already known were kept as they were%s"
                            % (time.strftime("%H:%M:%S"), STATE["missed"], len(found), chr(10)))
            except OSError:
                pass
        known = {int(one.get("tmdb") or 0) for one in rows}
        for one in _from_the_catalogue(lib, months, KEEP - len(rows)):
            if len(rows) >= KEEP:
                break
            if int(one.get("tmdb") or 0) in known:
                continue
            known.add(int(one.get("tmdb") or 0))
            # Judged the same way as the rest.
            #
            # These come from the database rather than the website, so they carry no
            # address and were never asked what people thought of them - they went
            # onto the shelf on the database's own mark alone, which is the number
            # that disagreed with the audience in the first place.
            said, judged = one.get("liked"), one.get("judged")
            if said is None and judged is None:
                for guess in slug_for(one.get("title"), one.get("year")):
                    pair = meters_of(guess)
                    if any(x is not None for x in pair):
                        one = dict(one, slug=guess, liked=pair[0], judged=pair[1])
                        said, judged = pair
                        break
            marks = [m for m in (said, judged) if m is not None]
            if marks and max(marks) < LIKED_PERCENT:
                continue
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
            # the IMDb numbers already learned go with the films into the new list: it
            # is rebuilt with them empty, twenty were filled in a pass, and the rest
            # were forgotten at every refresh - so "F1" was searched for by name and
            # found the races, never the releases tagged with its number
            known = {int(r.get("tmdb") or 0): r.get("imdb") for r in STATE["rows"]
                     if r.get("imdb")}
            for one in rows:
                if not one.get("imdb") and known.get(int(one.get("tmdb") or 0)):
                    one["imdb"] = known[int(one.get("tmdb") or 0)]
            STATE["rows"] = rows
            STATE["at"] = time.time()
            # and how far back this reading went. Left unwritten, the file kept saying
            # two years however deep the reading had been - so every later ask for a
            # wider window read the whole thing again, from the beginning, while
            # somebody waited on it.
            STATE["months"] = int(months)
            _write()
        # and the numbers these films are known by elsewhere, a few each pass: it is
        # what lets a release named differently still be found as the same film
        try:
            learn_imdb(lib)
        except Exception:
            pass                          # a number nobody has yet is not a failure
        try:
            learn_home(lib)
        except Exception:
            pass
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


def fetchable(one):
    """What the tracker has for this title, if anything.

    Asked of the index kept beside this one. A film nobody has uploaded is still
    listed - the shelf is about what is worth watching, not about what happens to be
    on a tracker this week - it simply has nothing to fetch yet.

    By its number where both sides know one: a release is named by whoever made it,
    and "Facing the Baron" against "The Baron: Facing the Ring" is the same film to
    everybody except a comparison of titles.
    """
    try:
        import pd_tracker
        return pd_tracker.find(one.get("title") or "", one.get("year") or 0,
                               one.get("imdb") or "")
    except Exception:
        return []


def learn_imdb(lib, most=200):
    """Fill in the number IMDb knows these films by, a few at a time.

    The list comes from a catalogue that numbers films its own way; the tracker
    names releases and carries the other number. One of them is a name and one is a
    number, and only the number can be compared without being clever.
    """
    read()
    with LOCK:
        want = [r for r in STATE["rows"] if r.get("tmdb") and not r.get("imdb")]
    if not want:
        return 0
    done = 0
    for row in want[:most]:
        try:
            said = lib.tmdb("/movie/%s/external_ids" % row["tmdb"]) or {}
        except Exception:
            break                         # the catalogue is away: try again next pass
        got = str(said.get("imdb_id") or "")
        if got:
            row["imdb"] = got
            done += 1
    if done:
        with LOCK:
            _write()
    return done


#: release kinds in the catalogue's dates that mean a film can be watched at home:
#: digital and physical
AT_HOME_TYPES = (4, 5)
#: how often a film not out yet is asked about again
HOME_AGAIN = 20 * 3600
#: words in a release name that mean it was filmed in a cinema, not released
CAMMED = ("cam", "hdcam", "camrip", "ts", "hdts", "telesync", "tc", "telecine",
          "screener", "scr", "dvdscr", "hdtc")


def learn_home(lib, most=200):
    """When each film came out to watch at home - the earliest digital or disc date the
    catalogue has for it anywhere. Asked again now and then for one not out yet: the
    date is often filled in only a few weeks ahead."""
    read()
    now = time.time()
    with LOCK:
        want = [r for r in STATE["rows"] if r.get("tmdb") and not r.get("home")
                and now - float(r.get("homeChecked") or 0) > HOME_AGAIN]
    done = 0
    for row in want[:most]:
        try:
            said = lib.tmdb("/movie/%s/release_dates" % row["tmdb"]) or {}
        except Exception:
            break                         # the catalogue is away: try again next pass
        dates = [str(d.get("release_date") or "")[:10]
                 for country in (said.get("results") or [])
                 for d in (country.get("release_dates") or [])
                 if int(d.get("type") or 0) in AT_HOME_TYPES]
        dates = sorted(d for d in dates if len(d) == 10)
        row["home"] = dates[0] if dates else ""
        row["homeChecked"] = now
        done += 1
    if done:
        with LOCK:
            _write()
    return done


def out_now(one):
    """Whether a film can be watched at home yet: held here already, or past its first
    digital or disc date, or the tracker carries a real release of it - not one filmed
    in a cinema. A film not yet asked about is given the benefit of the doubt."""
    if one.get("here"):
        return True
    today = time.strftime("%Y-%m-%d")
    home = str(one.get("home") or "")
    if home and home <= today:
        return True
    for r in fetchable(one):
        words = set(re.split(r"[^a-z0-9]+", str(r.get("name") or "").lower()))
        if not (words & set(CAMMED)):
            return True
    return not one.get("homeChecked")


#: when the rows were last matched against the library, between readings
HELD_AT = [0.0]


def mark_held(lib, every=60.0):
    """Between readings, which rows this house holds now. The list is read twice a day;
    a film that arrived since stood on the shelf as something to ask for until the
    next reading. The same match a reading makes, at most once in `every` seconds.
    Returns how many rows changed."""
    now = time.time()
    if now - HELD_AT[0] < every:
        return 0
    HELD_AT[0] = now
    from pd_library import flatten_title
    read()
    by_tmdb, held = _held(lib)
    changed = 0
    with LOCK:
        for one in STATE["rows"]:
            mine = (by_tmdb.get(("movie", int(one.get("tmdb") or 0)))
                    or _by_name(held, "movie", flatten_title(one.get("title") or ""),
                                one.get("year")))
            if (mine or None) != (one.get("here") or None):
                if mine:
                    one["here"] = mine
                else:
                    one.pop("here", None)
                changed += 1
    return changed


def shown():
    """The shelf: what is new and can actually be watched at home."""
    return [one for one in read() if out_now(one)]


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
        # and whether there is something to fetch for it. The shelf is the popular
        # list either way; this only decides whether the button asks somebody or
        # fetches it.
        "onTracker": bool(fetchable(one)),
        # Rotten Tomatoes: the critics' share and the audience's, percent
        "critics": one.get("judged"), "audience": one.get("liked"),
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
