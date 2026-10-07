"""A shelf for the season: Christmas films at Christmas, and the other holidays.

Which films belong to a holiday is the catalogue's to say - it keeps a keyword for
each - and the shelf is whatever of those this house holds or a pack can give, the
best known first. It stands on the front page when it is switched on under Settings:
for one holiday by name, or by the calendar, where each holiday has its days.

The catalogue's list is kept on disk for a week; the films of Christmas do not change
between one evening and the next.
"""
import datetime
import json
import os
import threading
import time

#: each holiday: what it is called, the catalogue's keywords for it, and its days.
#: Days are (month, day) to (month, day), both counted in; Easter moves and is
#: worked out from the year.
HOLIDAYS = [
    {"id": "halloween", "title": "Halloween", "keywords": [3335],
     "from": (10, 17), "to": (11, 1)},
    {"id": "christmas", "title": "Christmas", "keywords": [207317],
     "from": (12, 1), "to": (12, 26)},
    {"id": "newyear", "title": "New Year's Eve", "keywords": [613],
     "from": (12, 27), "to": (1, 1)},
    {"id": "valentine", "title": "Valentine's Day", "keywords": [160404],
     "from": (2, 7), "to": (2, 14)},
    {"id": "easter", "title": "Easter", "keywords": [9921],
     "easter": (-9, 1)},
]
BY_ID = {h["id"]: h for h in HOLIDAYS}
#: what the setting may be: off, by the calendar, or one holiday by name
CHOICES = ("off", "auto") + tuple(h["id"] for h in HOLIDAYS)
#: how many of the catalogue's pages are read for one holiday, twenty films a page,
#: and how few votes a film may have and still be one of them
PAGES = 15
VOTES = 30
EVERY = 7 * 86400.0

STATE = {"root": "", "lists": None}
LOCK = threading.Lock()


def use_data_dir(root):
    STATE["root"] = root


def _path():
    return os.path.join(STATE["root"] or ".", "holidays.json")


def easter_sunday(year):
    """The date of Easter Sunday, by the anonymous Gregorian reckoning."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return datetime.date(year, month, day)


def days_of(holiday, year):
    """The first and last day of a holiday's shelf in the year it begins in."""
    if "easter" in holiday:
        sunday = easter_sunday(year)
        before, after = holiday["easter"]
        return (sunday + datetime.timedelta(days=before),
                sunday + datetime.timedelta(days=after))
    (m1, d1), (m2, d2) = holiday["from"], holiday["to"]
    first = datetime.date(year, m1, d1)
    # a holiday that runs over the turn of the year ends in the next one
    last = datetime.date(year + (1 if (m2, d2) < (m1, d1) else 0), m2, d2)
    return first, last


def on_the_calendar(day):
    """The holiday whose days this one falls in, or "". The first listed wins."""
    for holiday in HOLIDAYS:
        for year in (day.year, day.year - 1):
            first, last = days_of(holiday, year)
            if first <= day <= last:
                return holiday["id"]
    return ""


def chosen(setting, day=None):
    """Which holiday's shelf stands today, from what Settings says: "" for none."""
    said = str(setting or "off").lower()
    if said == "auto":
        return on_the_calendar(day or datetime.date.today())
    return said if said in BY_ID else ""


def in_words(holiday):
    """A holiday's days as somebody would say them: "17 October to 1 November"."""
    if "easter" in holiday:
        before, after = holiday["easter"]
        return "from %d days before Easter Sunday to the day after it" % -before
    names = ("January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December")
    (m1, d1), (m2, d2) = holiday["from"], holiday["to"]
    return "%d %s to %d %s" % (d1, names[m1 - 1], d2, names[m2 - 1])


def _read():
    if STATE["lists"] is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                STATE["lists"] = json.load(f)
        except (OSError, ValueError):
            STATE["lists"] = {}
    return STATE["lists"]


def films(lib, which, wait=True):
    """The catalogue's numbers for a holiday's films, the best known first.

    Read once a week. A list in hand is answered with while a newer one is read behind
    it; with none in hand the first asking waits for it, unless told not to.
    """
    holiday = BY_ID.get(which)
    if not holiday:
        return []
    with LOCK:
        had = dict(_read().get(which) or {})
    fresh = time.time() - float(had.get("at") or 0) < EVERY
    if had.get("ids") and fresh:
        return list(had["ids"])
    if had.get("ids") or not wait:
        threading.Thread(target=lambda: _fetch(lib, holiday), daemon=True).start()
        return list(had.get("ids") or [])
    return _fetch(lib, holiday) or []


def _fetch(lib, holiday):
    ids, seen = [], set()
    try:
        for page in range(1, PAGES + 1):
            said = lib.tmdb("/discover/movie",
                            with_keywords="|".join(str(k) for k in holiday["keywords"]),
                            sort_by="vote_count.desc", page=page,
                            **{"vote_count.gte": VOTES})
            for one in said.get("results") or []:
                number = int(one.get("id") or 0)
                if number and number not in seen:
                    seen.add(number)
                    ids.append(number)
            if page >= int(said.get("total_pages") or 1):
                break
    except Exception:
        if not ids:
            return None                       # nothing read: what was there stands
    with LOCK:
        lists = _read()
        lists[holiday["id"]] = {"at": time.time(), "ids": ids}
        if STATE["root"]:
            try:
                tmp = _path() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(lists, f)
                os.replace(tmp, _path())
            except OSError:
                pass
    return ids


def in_order(cards, ids):
    """Cards put in the catalogue's order, one per film, those it does not list left out.

    Each card says which film it is with "tmdb". A film held here and offered by a
    pack as well stands once, as the one given first.
    """
    place = {int(n): i for i, n in enumerate(ids)}
    out, seen = [], set()
    for card in cards:
        try:
            number = int(card.get("tmdb") or 0)
        except (TypeError, ValueError):
            number = 0
        if number in place and number not in seen:
            seen.add(number)
            out.append(card)
    out.sort(key=lambda c: place[int(c.get("tmdb") or 0)])
    return out
