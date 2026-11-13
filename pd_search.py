"""The search: what a search finds, and every rule that silently leaves something out of it.

One handler for the search box on every screen. The hidden rules come first, in one place, so
a rule is changed here and nowhere else; under them, how the words are read and ranked; then
the search itself and the rows it gathers (films, series, packs, the catalogue, people), which
LocalAPI takes on as SearchParts. Moved here whole from pd_localapi, nothing in them changed.
"""
import json
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    _Base = Any                          # read through LocalAPI: self.lib, self.films_show, ...
else:
    _Base = object


# ---- the hidden rules: everything a search leaves out without saying so --------------------
#
#  1. tracker: only the server the screen is on asks the tracker ("here"); the other answers
#     from its index ("elsewhere"). Unmarked: the machine's rule - the main server asks.
#  2. person: an actor's face pressed - their face and the titles they are in, nothing else.
#  3. upcoming: a catalogue film not out yet, only for a viewer who shows upcoming films.
#  4. series nobody can get: not here, on the tracker or SVT and no longer being checked -
#     only for a viewer who shows Request series.
#  Rules 3 and 4 give way to an exact match: the words are the title, so it stands.

def begin(path, q):
    """A request starts: the tracker mark of a search (rule 1), cleared for anything else."""
    import pd_tracker
    pd_tracker.asked_as(str((q.get("tracker") or [""])[0]) if path == "/hubs/search" else "")


def end():
    """A request is answered: the mark goes, so the next request on this thread starts clean."""
    import pd_tracker
    pd_tracker.asked_as("")


def narrow(api, path, q, body):
    """The rules applied to a search's whole answer: an actor's face pressed (rule 2)."""
    person = str((q.get("person") or [""])[0])
    if path == "/hubs/search" and person.isdigit() and isinstance(body, dict):
        return api._only_person(body, int(person), str((q.get("query") or [""])[0]))
    return body


def exact(title, words, year=0):
    """Whether the words are this title: "the orchard", or "the orchard 2004" for the one from
    that year. An exact match stands whatever rules 3 and 4 would leave out."""
    from pd_localapi import bare
    name, said_year = title_and_year(words)
    return bool(bare(name)) and bare(name) == bare(title) and (
        not said_year or said_year == int(year or 0))


def upcoming_shown(card, shows, words=""):
    """Rule 3: a film not out yet stands only for a viewer who shows upcoming films - or
    when it is what was typed."""
    return (not card.get("upcoming") or bool(shows.get("upcoming"))
            or exact(card.get("title"), words, card.get("year") or 0))


def series_shown(svt, fetchable, checking, requests):
    """Rule 4: a series card not held here stands when streamed, downloadable or still being
    asked about; one nobody can get only for a viewer who wants them."""
    return bool(svt or fetchable or checking or requests)


# ---- reading and ranking the words ----------------------------------------------------------

#: A place in a series written the way people write it: s4e9, S04E09, s4 e9, 4x09.
#: It has to sit on its own - bounded by the ends of the words or by punctuation - so a
#: title that happens to contain the letters is not mistaken for a number.
EPISODE_CODE = re.compile(
    r"(?:^|[\s\-_.\[(])"
    r"(?:s\s*(\d{1,2})\s*[\s._x-]?\s*e\s*(\d{1,3})|(\d{1,2})\s*x\s*(\d{1,3}))"
    r"(?=$|[\s\-_.\])])", re.I)

def episode_code(words):
    """Split "the orchard s4e9" into ((4, 9), "the orchard").

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

def title_and_year(words):
    """("the orchard", 2004) from "the orchard 2004" or "the orchard (2004)"; the words
    and 0 when they end in no year, or are nothing but one."""
    from pd_localapi import bare
    m = re.match(r"^(.*\S)\s+\(?((?:19|20)\d{2})\)?$", str(words or "").strip())
    if m and bare(m.group(1)):
        return m.group(1), int(m.group(2))
    return words, 0

_ARTICLE = re.compile(r"^(the|a|an)\s+")
#: seconds a search gives the catalogue for its series the first time the words are asked
CATALOGUE_WAIT = 2.0

def match_rank(title, typed):
    """How close a title is to what was typed: 0 the same (with or without "the"), 1
    beginning with it, 2 a word of it, 3 anywhere in it, 4 not in it at all."""
    from pd_localapi import bare
    t = _ARTICLE.sub("", bare(str(title or "")))
    w = _ARTICLE.sub("", bare(str(typed or "")))
    if not w:
        return 9
    # a title spelt with marks between its letters: B*E*A*R is "b e a r" bare, and is
    # "bear" exactly - one came 17th of 18 under its own word (2026-11-13)
    if t == w or t.replace(" ", "") == w.replace(" ", ""):
        return 0
    if t.startswith(w):
        return 1
    if (" " + t).find(" " + w) >= 0:
        return 2
    if w in t or w.replace(" ", "") in t.replace(" ", ""):
        return 3
    return 4

def not_shown_yet(rows, cards):
    """The catalogue's rows that are not a film already among the cards: the same
    catalogue number, or the same title a year either side - a pack dates a film by its
    festival year and the catalogue by its release, and one film came up as 2013
    and as 2014, twice."""
    from pd_localapi import bare
    numbers = {int(c.get("tmdb") or 0) for c in cards} - {0}
    years = {}
    for c in cards:
        years.setdefault(bare(str(c.get("title") or "")), []).append(int(c.get("year") or 0))

    def shown(r):
        if int(r.get("tmdb") or 0) in numbers:
            return True
        y = int(r.get("year") or 0)
        return any(not y or not w or abs(y - w) <= 1
                   for w in years.get(bare(str(r.get("title") or "")), []))
    return [r for r in rows if not shown(r)]

def closest_first(hubs, typed, want_year=0):
    """Each row of titles closest first, keeping the order within each closeness; with
    a year asked for, only that year's - the exact title if it is there. A row left
    empty goes."""
    out = []
    for h in hubs:
        if h.get("type") not in ("movie", "show", "episode"):
            out.append(h)
            continue
        rows = h.get("Metadata") or []
        for it in rows:
            named = it.get("grandparentTitle") if h["type"] == "episode" else it.get("title")
            it["match"] = match_rank(named or it.get("title"), typed)
        rows = sorted(rows, key=lambda it: it["match"])
        if want_year:
            same = [it for it in rows if int(it.get("year") or 0) == want_year]
            rows = [it for it in same if it["match"] == 0] or same
        if rows:
            out.append(dict(h, Metadata=rows))
    return out


# ---- the search itself, and the rows it gathers ----------------------------------------------
class SearchParts(_Base):
    """The search's part of LocalAPI: the answer to /hubs/search and the rows it is built from."""

    @staticmethod
    def keep_series_card(svt, fetchable, checking, requests):
        """Whether a series card not held here stands: rule 4 above."""
        return series_shown(svt, fetchable, checking, requests)

    def search(self, con, q):
        """The answer to /hubs/search: every kind of thing the words find, closest first."""
        from pd_localapi import NOT_EXTRA, bare, bare_in
        one = lambda k, d=None: (q.get(k) or [d])[0]
        words = (one("query") or "").strip()
        # "s4e9" on its own means that place in every series; with a name in front
        # of it, that place in the series that answers to the name.
        place, rest = episode_code(words)
        # A code and nothing else would leave "%%" behind, which matches the whole
        # library, so the words are only searched for when there are some.
        spelt = rest if place else words
        # "the orchard 2004": the title, and the year it has to be from
        want_year = 0
        if not place:
            spelt, want_year = title_and_year(spelt)
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
        # a programme filed under a series pack is found as that series
        self._folded_in_search(con, spelt, hubs)
        # and the popular programmes not held yet, as the Popular page offers them
        self._popular_in_search(con, spelt, hubs)
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
        # few films by these words here, in a pack or among the new ones: the films
        # the catalogue knows by them too, the tracker asked about the first two.
        # Only when there were none, "cold snap" found a new film called Cold Snap and
        # never The Cold Snap of 1990, which "the cold snap" did.
        self._filling.on = False
        asking = self._named_in_search(spelt, hubs, place)
        if not place:
            asking = self._catalogue_in_search(spelt, hubs) or asking
        # Names are searched for as well as titles: a word that is nobody's film
        # is often somebody's name, and the row of faces is one press from
        # everything held with them in it. Only people with something on the
        # shelves here - the same rule the person page reads by, so pressing a
        # face never lands on an empty page.
        if spelt and len(spelt.strip()) >= 2:
            self._people_in_search(con, spelt, term, hubs)
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
        # a film here as an episode: the episode, under the film's name
        self._twins_in_search(con, hubs)
        # the closest titles first, and with a year in the words, that year only
        hubs = closest_first(hubs, spelt, want_year)
        # the catalogue still being asked: the page asks again in a moment
        return {"size": len(hubs), "Hub": hubs, "askingTracker": asking,
                "fillingIn": bool(getattr(self._filling, "on", False))}

    def _folded_in_search(self, con, spelt, hubs):
        """The series a programme was filed under, for a search that names the programme:
        Aces of Iron is a season of Hobby TV now, and the search finds that."""
        from pd_localapi import bare_in
        if not spelt or len(spelt.strip()) < 3:
            return
        import pd_torrents
        wanted = {one["series"] for one in pd_torrents.folded_shows().values()
                  if bare_in(spelt, one.get("title")) or bare_in(spelt, one.get("series"))}
        if not wanted:
            return
        tv = next((h for h in hubs if h["type"] == "show"), None)
        have = {str(x.get("ratingKey")) for x in (tv or {}).get("Metadata", [])}
        found = []
        for series in sorted(wanted):
            row = con.execute("SELECT * FROM item WHERE type='show' AND title=?",
                              (series,)).fetchone()
            if row and str(row["id"]) not in have:
                found.append(self._show(con, row))
        if not found:
            return
        if tv:
            tv["Metadata"][:0] = found
        else:
            at = next((n + 1 for n, h in enumerate(hubs) if h["type"] == "movie"), 0)
            hubs.insert(at, {"type": "show", "title": "TV shows", "Metadata": found})

    def _popular_in_search(self, con, spelt, hubs):
        """The popular programmes not held yet whose name holds the words, added to the
        search's TV shows as the Popular page's Add cards."""
        from pd_localapi import bare, bare_in
        if spelt and len(spelt.strip()) >= 2:
            try:
                with open(os.path.join(self.lib.root, "top_shows.json"),
                          encoding="utf-8") as f:
                    popular = json.load(f).get("shows") or []
            except (OSError, ValueError):
                popular = []
            tv = next((h for h in hubs if h["type"] == "show"), None)
            shown = {bare(str(x.get("title") or "")) for x in (tv or {}).get("Metadata", [])}
            held_tmdb = {int(r["tmdb_id"]) for r in con.execute(
                "SELECT tmdb_id FROM item WHERE type='show' AND tmdb_id IS NOT NULL")}
            # and those filed under a series pack, which are held too
            import pd_torrents
            held_tmdb |= {int(k) for k in pd_torrents.folded_shows()}
            more = [{"ratingKey": "tv%d" % int(x["tmdb"]), "type": "show",
                     "title": x.get("show") or "", "year": x.get("year") or None,
                     "summary": x.get("overview") or "",
                     "thumb": ("/art/tv%d/poster" % int(x["tmdb"])) if x.get("poster") else None,
                     "genres": list(x.get("genres") or []), "rating": x.get("rating"),
                     "addable": True, "tmdb": int(x["tmdb"])}
                    for x in popular
                    if x.get("tmdb") and bare_in(spelt, x.get("show"))
                    and int(x["tmdb"]) not in held_tmdb
                    and bare(str(x.get("show") or "")) not in shown][:20]
            # where each can come from, as on the Popular page: a search found these
            # with no band to say so
            more = self.with_sources(more, spelt)
            if more:
                if tv:
                    tv["Metadata"].extend(more)
                else:
                    at = next((n + 1 for n, h in enumerate(hubs)
                               if h["type"] == "movie"), 0)
                    hubs.insert(at, {"type": "show", "title": "TV shows", "Metadata": more})

    @staticmethod
    def _person_in_search(more, hubs):
        """A person's titles under a search for their name: films with the films and
        series with the series, once each - a held series of theirs stood in both rows."""
        from pd_localapi import bare
        for kind, title, at in (("movie", "Films", 0), ("show", "TV shows", 1)):
            mine = [x for x in more if (x.get("type") == "movie") == (kind == "movie")]
            hub = next((h for h in hubs if h["type"] == kind), None)
            have = {str(x.get("ratingKey")) for x in (hub or {}).get("Metadata", [])}
            names = {bare(str(x.get("title") or "")) for x in (hub or {}).get("Metadata", [])}
            mine = [x for x in mine if str(x.get("ratingKey")) not in have
                    and bare(str(x.get("title") or "")) not in names]
            if not mine:
                continue
            if hub:
                hub["Metadata"].extend(mine)
            else:
                hubs.insert(min(at, len(hubs)), {"type": kind, "title": title, "Metadata": mine})

    #: whether this request's search left the catalogue still to answer
    _filling = threading.local()

    def _named_in_search(self, spelt, hubs, place):
        """The catalogue's films by these words, beside what was found here, every search:
        held films matching the words hid every film not held - "star" found a dozen
        here and never the one being looked for. Whether the tracker is being asked."""
        films_now = next((h for h in hubs if h["type"] == "movie"), None)
        if not (spelt and len(spelt.strip()) >= 3 and not place):
            return False
        try:
            import pd_streaming
            # what is here is answered at once; the catalogue's films come with the
            # next asking, the page filling in behind it
            found = pd_streaming.by_name_soon(self.lib, spelt)
            if found is None:
                self._filling.on = True
                return False
            named_rows = not_shown_yet(found, (films_now or {}).get("Metadata") or [])
            if not named_rows:
                return False
            import pd_boxsets
            # every film shown: one not asked about stood as Request, and opening it -
            # which asks - said Download
            asking = (pd_streaming.ask_tracker_about(
                [r for r in named_rows if not pd_streaming.not_out(r)], self.lib, mine=True)
                      or pd_boxsets.pending())
            named = [pd_streaming.item(r) for r in named_rows]
            # a film not out yet only for a viewer who shows those
            shows = self.films_show()
            named = [c for c in named if upcoming_shown(c, shows, spelt)]
            if films_now:
                films_now["Metadata"].extend(named)
            else:
                hubs.insert(0, {"type": "movie", "title": "Films", "Metadata": named})
            return bool(asking)
        except Exception:
            return False

    def _collection_in_search(self, spelt, hubs):
        """The films of the catalogue collection the words name that are not here or on a
        pack, under the films a search found; the tracker asked about them. True while
        it is still being asked."""
        from pd_localapi import bare
        try:
            import pd_streaming
            rows = pd_streaming.collection_rows(self.lib, spelt)
        except Exception:
            return False
        films = next((h for h in hubs if h["type"] == "movie"), None)
        have = {bare(str(x.get("title") or "")) for x in (films or {}).get("Metadata", [])}
        rows = [r for r in rows if bare(str(r.get("title") or "")) not in have]
        if not rows:
            return False
        asking = pd_streaming.ask_tracker_about(rows, self.lib, mine=True)
        cards = [pd_streaming.item(r) for r in rows]
        if films:
            films["Metadata"].extend(cards)
        else:
            hubs.insert(0, {"type": "movie", "title": "Films", "Metadata": cards})
        return asking

    def _catalogue_in_search(self, spelt, hubs):
        """Series by these words from the catalogue, beside whatever matched: the cache,
        which held no pack of a programme, answered with three where this server answered
        with one. Two letters for a series: "24" (2026-11-01). Then the films of a
        collection the words name. Whether the collection is still being asked for."""
        said = (spelt or "").strip()
        if len(said) >= 2:
            self._catalogue_shows_in_search(spelt, hubs)
        return len(said) >= 3 and self._collection_in_search(spelt, hubs)

    def _catalogue_shows_in_search(self, spelt, hubs):
        """Series the catalogue knows by these words that this library does not hold, as
        the popular list's Add cards, under the TV shows a search found."""
        from pd_localapi import bare
        try:
            import pd_streaming
            named_shows = pd_streaming.by_name_soon(self.lib, spelt, shows=True,
                                                    wait=CATALOGUE_WAIT)
        except Exception:
            return
        if named_shows is None:
            self._filling.on = True     # with the next asking
            return
        tv = next((h for h in hubs if h["type"] == "show"), None)
        have = {bare(str(x.get("title") or "")) for x in (tv or {}).get("Metadata", [])}
        have_tmdb = {int(x.get("tmdb") or 0) for x in (tv or {}).get("Metadata", [])}
        cards = [{"ratingKey": r["key"], "type": "show", "title": r["show"],
                  "year": r["year"] or None, "summary": r["overview"],
                  "thumb": "/art/%s/poster" % r["key"], "rating": r["rating"],
                  "genres": [], "addable": True, "tmdb": r["tmdb"],
                  "svt": self._svt_series(r["show"], r["year"])}
                 for r in named_shows
                 if bare(str(r["show"] or "")) not in have and int(r["tmdb"]) not in have_tmdb]
        cards = self.with_sources(cards, spelt)
        if not cards:
            return
        if tv:
            tv["Metadata"].extend(cards)
        else:
            at = next((n + 1 for n, h in enumerate(hubs) if h["type"] == "movie"), 0)
            hubs.insert(at, {"type": "show", "title": "TV shows", "Metadata": cards})

    def _near_misses(self, con, spelt, listed):
        """Films and series whose title is the search with a letter or two wrong."""
        from pd_localapi import NOT_EXTRA, bare, near
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

    #: a whole name asked of the catalogue's people, kept ten minutes: (when, rows)
    _PEOPLE_ASKED = {}

    def _person_by_name(self, spelt):
        """Somebody the catalogue knows by exactly this whole name, as a row of the faces:
        a person on television only - a reality series' cast - is in no film's cast
        here, and searching their name found nobody. [] for one word, or no such name."""
        words = spelt.strip()
        if len(words.split()) < 2:
            return []
        kept = self._PEOPLE_ASKED.get(words.lower())
        if kept and time.time() - kept[0] < 600:
            return kept[1]
        try:
            found = (self.lib.tmdb("/search/person", query=words) or {}).get("results") or []
        except Exception:
            return []
        hit = next((r for r in found if r.get("id")
                    and str(r.get("name") or "").strip().lower() == words.lower()), None)
        rows = ([{"person": int(hit["id"]), "name": str(hit["name"]), "ord": 0,
                  "profile": str(hit.get("profile_path") or ""), "held": 0}] if hit else [])
        if len(self._PEOPLE_ASKED) > 200:
            self._PEOPLE_ASKED.clear()
        self._PEOPLE_ASKED[words.lower()] = (time.time(), rows)
        return rows

    def _people_in_search(self, con, spelt, term, hubs):
        """The people a search's words name, as a row of faces, and for a whole name
        typed everything they are in under Films and TV shows."""
        people = con.execute(
            """SELECT MAX(c.person) AS person, MIN(c.name) AS name,
                      MIN(c.ord) AS ord, MAX(c.profile) AS profile,
                      COUNT(DISTINCT i.id) AS held
                 FROM credit c JOIN item i ON i.id = c.item_id
                WHERE c.name LIKE ?
                  AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = i.id)
                GROUP BY LOWER(c.name)
                ORDER BY held DESC, ord ASC LIMIT 20""", (term,)).fetchall()
        # The whole name typed, of somebody credited only on films not here yet -
        # a pack's or the tracker's: found as well. Pressing their name found
        # thirty of their titles; typing it found none of them.
        if not any(str(r["name"] or "").strip().lower() == spelt.strip().lower()
                   for r in people):
            named = con.execute(
                """SELECT MAX(person) AS person, MIN(name) AS name, MIN(ord) AS ord,
                          MAX(profile) AS profile, 0 AS held
                     FROM credit WHERE LOWER(name) = LOWER(?) AND name <> ''
                    GROUP BY LOWER(name)""", (spelt.strip(),)).fetchall()
            people = list(named) + list(people)
        if not any(str(r["name"] or "").strip().lower() == spelt.strip().lower()
                   for r in people):
            people = self._person_by_name(spelt) + list(people)
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
                self._person_in_search(
                    self._person_films(int(top["person"] or 0), top["name"]), hubs)

    def _only_person(self, body, person, name):
        """A search made by pressing an actor's face: the ordinary search for their name,
        narrowed without a word to that one person - their face and the titles they are
        in, not titles that only carry the words, nor others who share the name."""
        from pd_localapi import bare
        theirs = self._person_films(person, name)
        keys = {str(m.get("ratingKey")) for m in theirs}
        names = {bare(str(m.get("title") or "")) for m in theirs}
        hubs = []
        for h in body.get("Hub") or []:
            if h.get("type") == "person":
                rows = [m for m in h.get("Metadata") or [] if int(m.get("id") or 0) == person]
            elif h.get("type") in ("movie", "show"):
                rows = [m for m in h.get("Metadata") or []
                        if str(m.get("ratingKey")) in keys or bare(str(m.get("title") or "")) in names]
            else:
                rows = []
            if rows:
                hubs.append(dict(h, Metadata=rows))
        # and whatever of theirs the words alone did not find: a namesake took the search
        self._person_in_search(theirs, hubs)
        return dict(body, Hub=hubs, size=len(hubs))

    def _twins_in_search(self, con, hubs):
        """Films a search found that are not here as films but are as an episode: the
        episode's card in their place, under the film's name, and once."""
        films = next((h for h in hubs if h["type"] == "movie"), None)
        if not films:
            return
        out, seen = [], set()
        for card in films["Metadata"]:
            key = str(card.get("ratingKey") or "")
            twin = ""
            if card.get("type") == "movie" and not con.execute(
                    "SELECT 1 FROM file WHERE item_id=? LIMIT 1", (key,)).fetchone():
                twin = self._episode_twin(con, card.get("title"), int(card.get("year") or 0))
            if twin:
                got = self.metadata_for(con, twin, brief=True)
                if got:
                    card = dict(got, aka={"title": card.get("title"), "year": card.get("year")})
                    key = twin
            if key in seen:
                continue
            seen.add(key)
            out.append(card)
        films["Metadata"] = out
