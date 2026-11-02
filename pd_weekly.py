"""This week's picks: films from the library chosen on Monday for the whole week.

SIZE films the house holds, well rated and not seen to the end by anybody, none picked
in the last NOT_AGAIN weeks. The better rated a film, the likelier it is drawn, and the
first drawn is the week's pick. The draw is seeded by the week, so the same library
gives the same picks wherever it is asked; the main server keeps them in settings.json
as weeklyPicks, which a copy takes from it with the rest of the shared settings.
"""
import datetime
import random

#: films on the shelf
SIZE = 10
#: a film picked is not picked again for this many weeks
NOT_AGAIN = 26
#: the catalogue's rating a film needs, out of ten
GOOD = 7.0
#: watched this far through is seen
SEEN_AT = 0.9


def week_of(day):
    """"2026-W44": the ISO week a day falls in."""
    y, w, _ = day.isocalendar()
    return "%d-W%02d" % (y, w)


def seen(rows):
    """The keys anybody has watched to the end, from progress rows (key, position,
    duration, marked, furthest)."""
    out = set()
    for key, position, duration, marked, furthest in rows:
        got = max(float(position or 0), float(furthest or 0))
        if marked or (duration and got >= SEEN_AT * float(duration)):
            out.add(str(key))
    return out


def choose(films, seen_keys, before, week):
    """[tmdb] for the week, the pick first. films: [(key, tmdb, rating)] held here.

    Not seen and not picked lately first; a library too small for that lets the seen
    ones in, then the recent ones, rather than showing a short shelf."""
    rng = random.Random(week)
    before = set(int(t) for t in before)
    films = sorted({int(t): (str(k), int(t), float(r or 0)) for k, t, r in films
                    if t and float(r or 0) >= GOOD}.values())
    tiers = [[f for f in films if f[0] not in seen_keys and f[1] not in before],
             [f for f in films if f[1] not in before],
             films]
    out = []
    for pool in tiers:
        # weighted draw without replacement: each film's key u^(1/weight), largest first
        keyed = sorted(((rng.random() ** (1.0 / max(0.1, r - GOOD + 1)), t)
                        for _, t, r in pool if t not in out), reverse=True)
        out += [t for _, t in keyed[:SIZE - len(out)]]
        if len(out) >= SIZE:
            break
    return out


def line(overview, most=200):
    """The Start tab's line under the pick: the summary's first sentences, up to most
    characters, cut at a sentence end where one comes before that."""
    text = " ".join(str(overview or "").split())
    if len(text) <= most:
        return text
    cut = text[:most]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 0 else cut[:cut.rfind(" ")] + "…"


def due(stored, today):
    """Whether this week's picks are still to be drawn."""
    return str((stored or {}).get("week") or "") != week_of(today)


def draw(con, stored, today=None):
    """The weeklyPicks setting for this week: {"week", "tmdb", "before"}."""
    today = today or datetime.date.today()
    films = con.execute(
        "SELECT DISTINCT i.id, i.tmdb_id, i.rating FROM item i JOIN file f ON f.item_id = i.id "
        "WHERE i.type = 'movie' AND i.tmdb_id IS NOT NULL").fetchall()
    keys = seen(con.execute(
        "SELECT key, position, duration, marked, furthest FROM progress").fetchall())
    stored = stored or {}
    before = ([int(t) for t in stored.get("tmdb") or []]
              + [int(t) for t in stored.get("before") or []])[:SIZE * NOT_AGAIN]
    week = week_of(today)
    return {"week": week, "tmdb": choose(films, keys, before, week), "before": before}
