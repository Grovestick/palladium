#!/usr/bin/env python3
"""Small rules that decide something, each tried on its own. Run by the build.

    python pd-test-rules.py

Exit code 0 when every one held, 1 otherwise.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(what, ok):
    if not ok:
        FAILS.append(what)


import pd_streaming as st

# the new-releases row: what the source has scored is not held back for being little
# known in the catalogue; what nobody has scored still needs the catalogue's votes
check("scored by the audience, two votes here", st.known_enough(2, 74, None))
check("scored by the critics, no votes here", st.known_enough(0, None, 91))
check("scored low is still known: the score decides later", st.known_enough(0, 12, 8))
check("unscored and unknown is left out", not st.known_enough(2, None, None))
check("unscored and unknown, no number at all", not st.known_enough(None, None, None))
check("unscored but known here", st.known_enough(st.KNOWN_VOTES, None, None))
check("a vote count that is not a number", not st.known_enough("many", None, None))

# which of the catalogue's answers is the film the source's list means
def F(ident, title, date, original=None):
    return {"id": ident, "title": title, "original_title": original or title, "release_date": date}

got = st.pick_match("In the Marsh", None, "2026-10-06",
                    [F(1, "In the Marsh of Mirrors", "1995-02-03"), F(2, "In the Marsh", "2025-01-24")])
check("a famous old film whose name begins the same is not it", bool(got) and got["id"] == 2)
check("and with the famous one and others to choose from, none is",
      st.pick_match("In the Marsh", None, "2026-10-06", [F(1, "In the Marsh of Mirrors", "1995-02-03"),
                                                          F(20, "A Stone in the Marsh", "1983-01-01")]) is None)
check("nor one whose name ends with it, old or new",
      st.pick_match("Nights", None, "2026-10-03", [F(3, "Paper Nights", "1997-10-07"),
                                                    F(30, "30 Dates and 30 Nights", "2026-02-01")]) is None)
got = st.pick_match("Skirmish Hut", None, "2026-09-29", [F(31, "Westspur", "2023-08-21")])
check("the catalogue's only answer, under another name it goes by, is it", bool(got) and got["id"] == 31)
check("but not one of several under other names",
      st.pick_match("Skirmish Hut", None, "2026-09-29", [F(31, "Westspur", "2023-08-21"), F(32, "Hut", "2020-01-01")]) is None)
check("nor one with it in the middle",
      st.pick_match("Big Dog", None, "2026-09-27", [F(4, "Rufus the Big Blue Dog", "2021-11-10"),
                                                     F(40, "Big Argon Dog", "1999-01-01")]) is None)
got = st.pick_match("The Smell of Rain", None, "2026-09-25", [F(5, "The Smell of Rain", "2004-07-17", "Ame no nioi")])
check("an old film by its exact name, new to streaming, is it", bool(got) and got["id"] == 5)
got = st.pick_match("Drop 2", None, "2026-10-06", [F(6, "Drop 2: Lowpoint", "2026-09-01")])
check("a new film whose name only begins the same is it", bool(got) and got["id"] == 6)
got = st.pick_match("The Upheaval", 2026, "2026-09-29",
                    [F(7, "The Upheaval", "1912-01-01"), F(8, "The Upheaval", "2026-09-10")])
check("the year the source gives decides between two of one name", bool(got) and got["id"] == 8)
got = st.pick_match("The Smell of Rain", None, "2026-09-25",
                    [F(9, "The Smell of Rain", "2004-07-17"), F(10, "The Smell of Rain", "2026-03-01")])
check("with no year given, the first of that name as the catalogue lists them",
      bool(got) and got["id"] == 9)
check("a subtitle is what follows a colon or a dash, not any longer name",
      st._with_subtitle("Drop 2", "Drop 2: Lowpoint") and st._with_subtitle("Sand", "Sand - Part Two")
      and not st._with_subtitle("Drop", "Dropped") and not st._with_subtitle("Sand", "Sand")
      and not st._with_subtitle("Nights", "Nights of Calabria"))
got = st.pick_match("Stand Up Old Man: A Spoons Out Mystery", None, "",
                    [F(15, "Stand Up Old Man", "%d-11-26" % (__import__("time").localtime().tm_year - 1))])
check("no day it reached streaming: a recent film whose name this begins with is still it",
      bool(got) and got["id"] == 15)
got = st.pick_match("Odile", None, "2026-10-01", [F(12, "Odile", "2001-04-25", "Le Fabuleux Destin d'Odile Martin")])
check("a name in another language, by the name the catalogue files it under", bool(got) and got["id"] == 12)
check("no name, no film", st.pick_match("", None, "2026-10-01", [F(13, "", "2026-01-01")]) is None
      and st.pick_match("X", None, "", []) is None)
check("punctuation and an ampersand do not make another film",
      (st.pick_match("Tom & Jerry: The Movie", None, "2026-10-01", [F(14, "Tom and Jerry - The Movie", "2025-12-01")]) or {}).get("id") == 14)

# a lookup that failed: the film stays as it was known, its first date put back
class Away:
    def tmdb(self, *a, **k):
        raise OSError("the catalogue is away")

WAS = {"key": "rt1", "title": "A Film", "tmdb": 77, "votes": 500, "poster": "/p.jpg", "year": 2024,
       "released": "2026-10-02", "premiered": "2024-11-27", "home": "2026-10-02", "liked": 94}
got = st._looked_up(Away(), {"title": "A Film", "key": "rt1"}, WAS)
check("the catalogue away: a film already known stays as it was known: %s" % got,
      bool(got) and got["tmdb"] == 77 and got["poster"] == "/p.jpg" and got["votes"] == 500)
check("with the date it first came out, not the day it reached streaming",
      bool(got) and got["released"] == "2024-11-27" and "home" not in got and "liked" not in got)
check("the catalogue away and nothing known: nothing",
      st._looked_up(Away(), {"title": "A Film", "key": "rt1"}) is None
      and st._looked_up(Away(), {"title": "A Film", "key": "rt1"}, {"key": "rt1"}) is None)
check("a row never dated by the source keeps its one date",
      st.as_known({"tmdb": 5, "released": "2026-08-01"})["released"] == "2026-08-01")

# the date the source prints under a tile is the date a film came out at home
PAGE = """
<media-info-tile> <poster-tile slot="poster" media-url="/m/an_old_film" size="x"> <rt-img alt="An Old Film"></rt-img> </poster-tile>
 <a href="/m/an_old_film"> <rt-text size="0.75" data-qa="discovery-media-list-item-start-date"> Streaming Sep 25, 2026 </rt-text> </a> </media-info-tile>
<media-info-tile> <poster-tile slot="poster" media-url="/m/undated"> </poster-tile> <a href="/m/undated"> </a> </media-info-tile>
<media-info-tile> <poster-tile slot="poster" media-url="/m/next_one"> </poster-tile>
 <a href="/m/next_one"> <rt-text data-qa="discovery-media-list-item-start-date">Streaming Oct 9, 2026</rt-text> </a> </media-info-tile>
<media-info-tile> <poster-tile slot="poster" media-url="/m/in_cinemas"> </poster-tile>
 <a href="/m/in_cinemas"> <rt-text data-qa="discovery-media-list-item-start-date">Opened Oct 2, 2026</rt-text> </a> </media-info-tile>
"""
got = st.streaming_dates(PAGE)
check("dates read tile by tile: %s" % got,
      got == {"/m/an_old_film": "2026-09-25", "/m/next_one": "2026-10-09"})
check("no page, no dates", st.streaming_dates("") == {} and st.streaming_dates(None) == {})
old_film = st.dated_by_the_source({"title": "An Old Film", "released": "2004-07-17",
                                   "listed": "2026-09-25", "home": ""})
check("a film from 2004 streaming since September is dated September",
      old_film["released"] == "2026-09-25" and old_film["home"] == "2026-09-25"
      and old_film["premiered"] == "2004-07-17")
check("and is out now", st.out_now(old_film))
undated = {"title": "X", "released": "2026-09-10", "home": "", "homeChecked": 1}
check("without the source's date nothing changes", st.dated_by_the_source(dict(undated)) == undated)
check("a date that is no date changes nothing",
      st.dated_by_the_source(dict(undated, listed="soon"))["released"] == "2026-09-10")
ahead = st.dated_by_the_source({"title": "Y", "released": "2026-08-01", "listed": "2099-01-01"})
check("a date still ahead is not out yet", not st.out_now(ahead))

# where a film can be watched: each service once, this country's first, then the most
# widespread, a subscription before a shop where two are as widespread
def P(ident, name, first=10):
    return {"provider_id": ident, "provider_name": name, "logo_path": "/%d.png" % ident,
            "display_priority": first}

WHERE = {"US": {"link": "x", "rent": [P(10, "Shop A"), P(2, "Shop B")], "buy": [P(10, "Shop A")]},
         "GB": {"flatrate": [P(8, "Stream N")], "rent": [P(10, "Shop A")]},
         "SE": {"rent": [P(3, "Shop C")], "ads": [P(9, "Stream F")]},
         "DE": {"flatrate": [P(8, "Stream N")]}}
got = [(p["name"], p["kind"]) for p in st.providers_of(WHERE, "SE")]
check("this country's first, then the most widespread, each once: %s" % got,
      got == [("Stream F", "stream"), ("Shop C", "store"), ("Stream N", "stream"),
              ("Shop A", "store"), ("Shop B", "store")])
got = [p["name"] for p in st.providers_of(WHERE, "")]
check("no country of its own: the most widespread first: %s" % got,
      got == ["Stream N", "Shop A", "Stream F", "Shop B", "Shop C"])
far = dict(WHERE, PL={"flatrate": [P(77, "Stream Far")]}, FR={"rent": [P(10, "Shop A")]})
got = [p["name"] for p in st.providers_of(far, "")]
check("a service streaming it in one country does not lead: %s" % got,
      got[0] == "Shop A" and got.index("Stream Far") > got.index("Stream N"))
check("no more than asked for", len(st.providers_of(WHERE, "SE", most=2)) == 2)
check("nothing known, nothing shown",
      st.providers_of({}, "SE") == [] and st.providers_of(None) == [])
check("a service with no name or number is left out",
      st.providers_of({"US": {"rent": [{"provider_id": 1}, {"provider_name": "X"}]}}) == [])
check("each carries its mark", all(p["logo"].endswith(".png") for p in st.providers_of(WHERE)))
check("one service under two numbers stands once",
      [p["name"] for p in st.providers_of({"US": {"rent": [P(10, "Shop A")]},
                                           "GB": {"rent": [P(11, "Shop A ")]}})] == ["Shop A"])

# the services chosen under Settings, and the country chosen there
got = [p["name"] for p in st.providers_of(WHERE, "SE", strict=True)]
check("a country chosen: only what that country has: %s" % got, got == ["Stream F", "Shop C"])
got = [p["name"] for p in st.providers_of(WHERE, "", only=["stream n", " Shop B "])]
check("services chosen: only those, however they are spelt: %s" % got, got == ["Stream N", "Shop B"])
check("a country and services chosen, and it is on none of them there",
      st.providers_of(WHERE, "SE", only=["Stream N"], strict=True) == [])
check("no services chosen is all of them",
      len(st.providers_of(WHERE, "", only=[])) == 5 and len(st.providers_of(WHERE, "", only=[" "])) == 5)
check("a country with nothing listed", st.providers_of(WHERE, "NO", strict=True) == [])
# the studios behind a film: each once, those with a mark first
# a label stands as the studio it was a name for, and the known studios come first
MALL = [{"id": 37, "name": "Gramercy Pictures", "logo_path": "/g.png"},
        {"id": 900001, "name": "Small One", "logo_path": "/s1.png"},
        {"id": 900002, "name": "Small Two", "logo_path": "/s2.png"}]
got = st.studios_of(MALL, logo_of=lambda n: {33: "/universal.png"}.get(n))
check("a label is shown as its studio, with the studio's own mark: %s" % got,
      got == [{"name": "Universal Pictures", "logo": "/universal.png"},
              {"name": "Small One", "logo": "/s1.png"}, {"name": "Small Two", "logo": "/s2.png"}])
got = [s["name"] for s in st.studios_of(
    [{"id": 900001, "name": "Small One", "logo_path": "/s1.png"},
     {"id": 900003, "name": "No Mark Films"},
     {"id": 174, "name": "Warner Bros. Pictures", "logo_path": "/wb.png"},
     {"id": 12, "name": "New Line Cinema", "logo_path": "/nl.png"}])]
check("the known studios first, in the film's order, then what room is left: %s" % got,
      got == ["Warner Bros. Pictures", "New Line Cinema", "Small One"])
got = st.studios_of([{"id": 37, "name": "Gramercy Pictures", "logo_path": "/g.png"},
                     {"id": 33, "name": "Universal Pictures", "logo_path": "/u.png"}])
check("a label and its studio both listed stand once, with the film's own mark: %s" % got,
      got == [{"name": "Universal Pictures", "logo": "/u.png"}])
got = [s["name"] for s in st.studios_of(
    [{"id": 79, "name": "Village Roadshow Pictures", "logo_path": "/v.png"},
     {"id": 900004, "name": "A Partnership"},
     {"id": 900005, "name": "Small Pictures", "logo_path": "/sp.png"},
     {"id": 174, "name": "Warner Bros. Pictures", "logo_path": "/wb.png"}])]
check("the studio before the company that paid for it: %s" % got,
      got == ["Warner Bros. Pictures", "Village Roadshow Pictures", "Small Pictures"])
check("those beside a studio are all known ones", st.BESIDE <= set(st.MAJORS))
def away(n):
    raise RuntimeError("the catalogue is away")
got = st.studios_of(MALL[:1], logo_of=away)
check("a studio whose mark cannot be had is still named", got == [{"name": "Universal Pictures", "logo": ""}])
check("and with nobody to ask", st.studios_of(MALL[:1]) == [{"name": "Universal Pictures", "logo": ""}])
check("every label names a known studio", all(p in st.MAJORS for p in st.LABELS.values())
      and not (set(st.LABELS) & set(st.MAJORS)))
check("a number that is no number is a company like any other",
      st.studios_of([{"id": "x", "name": "Odd", "logo_path": "/o.png"}]) == [{"name": "Odd", "logo": "/o.png"}])

COMPANIES = [{"name": "A Partnership", "logo_path": None}, {"name": "Big Studio", "logo_path": "/b.png"},
             {"name": "big studio", "logo_path": "/b2.png"}, {"name": "", "logo_path": "/x.png"},
             {"name": "Second Studio", "logo_path": "/s.png"}, {"name": "Third", "logo_path": "/t.png"},
             {"name": "Fourth", "logo_path": "/f.png"}]
got = [(c["name"], c["logo"]) for c in st.studios_of(COMPANIES)]
check("studios with a mark first, each once, three at most: %s" % got,
      got == [("Big Studio", "/b.png"), ("Second Studio", "/s.png"), ("Third", "/t.png")])
check("a studio with no mark is still named",
      st.studios_of([{"name": "Small Films"}]) == [{"name": "Small Films", "logo": ""}])
check("no studios", st.studios_of(None) == [] and st.studios_of([]) == [])
# a country's services in the order the catalogue shows them there, each name once
LISTED = [{"provider_name": "Second", "logo_path": "/2.png", "display_priorities": {"SE": 2, "US": 1}},
          {"provider_name": "First", "logo_path": "/1.png", "display_priorities": {"SE": 1, "US": 9}},
          {"provider_name": "first", "logo_path": "/1b.png", "display_priorities": {"SE": 5}},
          {"provider_name": "", "logo_path": "/0.png"}]
check("services in the country's own order, each once",
      [p["name"] for p in st.services_in_order(LISTED, "SE")] == ["First", "Second"]
      and [p["name"] for p in st.services_in_order(LISTED, "US")] == ["Second", "First"])

# which subtitle is gone through next
import pd_subs as subs
NOW, AGAIN = 2_000_000, 14 * 86400
DONE = {("a", "en"): (NOW - 100, "fits"), ("b", "en"): (NOW - 3600, "none fit"),
        ("c", "en"): (NOW - 3600, "allowance"), ("d", "en"): (NOW - 2 * 86400, "allowance"),
        ("e", "en"): (NOW - 15 * 86400, "none fit")}
got = subs.due(DONE, ["a", "b", "f"], ["g", "b"], ["en"], [], NOW, AGAIN)
check("nobody asking: good ones and recent tries are left, the rest in order: %s" % got,
      got == [("f", "en", True), ("g", "en", False)])
got = subs.due(DONE, ["f"], [], ["en"], [("a", "en"), ("b", "en"), ("c", "en"), ("d", "en"), ("z", "en")],
               NOW, AGAIN)
check("asked for: first, fetched for, a recent try no bar - a good one and today's refusal still are: %s" % got,
      got == [("b", "en", True), ("d", "en", True), ("z", "en", True), ("f", "en", True)])
check("one asked for that is also next in line is done once",
      subs.due({}, ["x"], ["x"], ["en"], [("x", "en")], NOW, AGAIN) == [("x", "en", True)])
check("only the first when only one is wanted",
      subs.due(DONE, ["f", "h"], [], ["en"], [("b", "en")], NOW, AGAIN, True) == [("b", "en", True)])
check("a second language is its own check",
      subs.due({("f", "en"): (NOW, "fits")}, ["f"], [], ["en", "sv"], [], NOW, AGAIN) == [("f", "sv", True)])
check("an old try comes round again by itself",
      subs.due(DONE, ["e"], [], ["en"], [], NOW, AGAIN) == [("e", "en", True)])

# where an episode starts: the most particular rule wins
import pd_leads as leads
import random
RULES = {"show": {"all": 2, "s17": 12.5, "s15e02": 28}}
check("the episode's own rule", leads.lead_for(RULES, "show", 15, 2) == 28)
check("else its season's", leads.lead_for(RULES, "show", 17, 4) == 12.5)
check("else the programme's", leads.lead_for(RULES, "show", 15, 3) == 2)
check("another programme has none", leads.lead_for(RULES, "other", 17, 4) == 0
      and leads.lead_for({}, "show", 1, 1) == 0 and leads.lead_for(None, "show", 1, 1) == 0)
check("a rule that is no number is no rule", leads.lead_for({"show": {"s1": "soon"}}, "show", 1, 1) == 0)
check("no rule skips more than three minutes", leads.lead_for({"show": {"s1": 9999}}, "show", 1, 1) == 180)
got = leads.with_rule(RULES, "show", 18, 0, 12)
check("a rule set, what was given left alone", got["show"]["s18"] == 12 and "s18" not in RULES["show"])
got = leads.with_rule(leads.with_rule(RULES, "show", 17, 0, 0), "show", 15, 2, 0)
check("a rule taken away by nought seconds", got == {"show": {"all": 2}})
check("the last rule gone takes the programme with it", leads.with_rule({"x": {"s1": 5}}, "x", 1, 0, 0) == {})
rows = leads.listed({"b": {"s2": 5, "s2e01": 7, "all": 1, "s1": 3}, "a": {"s10": 4}})
check("listed a programme together, the particular after the general: %s" % rows,
      [(r["show"], r["season"], r["episode"]) for r in rows]
      == [("a", 10, 0), ("b", 0, 0), ("b", 1, 0), ("b", 2, 0), ("b", 2, 1)])
# what a season opens with in common
rnd = random.Random(7)
def sound(n): return [rnd.getrandbits(32) for _ in range(n)]
IDENT = sound(100)                       # 12.4 s of the same sound in front of each
QUIET = [0x5A5A5A5A] * 30                # and the same silence after it
season = {n: IDENT + QUIET + sound(400) for n in range(1, 9)}
got = leads.suggest(season)
check("a season that opens alike: about its length, to the last sound and not through the quiet: %s" % got,
      bool(got) and 11.5 <= got["seconds"] <= 12.5 and got["agree"] == 8 and got["of"] == 8)
plain = {n: sound(500) for n in range(1, 11)}
check("a season where each opens its own way: nothing", leads.suggest(plain) is None)
twopart = dict(plain); twopart[9] = plain[8][:350] + sound(150)
check("two halves of one story opening alike are not a logo", leads.suggest(twopart) is None)
silent = {n: [0x5A5A5A5A] * 80 + sound(400) for n in range(1, 9)}
check("every episode beginning in silence is not a logo either", leads.suggest(silent) is None)
most = dict(season); most[1] = sound(530); most[2] = sound(530)
got = leads.suggest(most)
check("six of eight is a season's opening", bool(got) and got["agree"] == 6 and got["of"] == 8)
few = dict(plain)
for n in (1, 2, 3): few[n] = IDENT + sound(400)
check("three of ten is not", leads.suggest(few) is None)
check("too few episodes to judge by", leads.suggest({1: IDENT + sound(300), 2: IDENT + sound(300)}) is None)
shifted = {n: (IDENT[n % 4:] + QUIET + sound(400)) for n in range(1, 9)}
got = leads.suggest(shifted)
check("files that begin a few frames into the same opening still agree: %s" % got,
      bool(got) and got["agree"] == 8)
check("nothing heard, nothing said", leads.suggest({}) is None and leads.suggest({1: [], 2: [], 3: []}) is None)

# the receiver: a lowering it did not take is tried again, once, to the same level
import pd_receiver as rcv
class _Recv:
    vol, fails, sets, timers = 90, 0, None, None
def _rv(ip):
    if _Recv.fails > 0:
        _Recv.fails -= 1
        raise OSError("timed out")
    return "on", _Recv.vol, 161
def _rs(ip, volume):
    _Recv.sets.append(int(volume)); _Recv.vol = int(volume); return True
class _Timer:
    def __init__(self, secs, fn, args=()): _Recv.timers.append((secs, fn, args))
    def start(self): pass
    daemon = False
_real = (rcv._volume, rcv._set, rcv.threading.Timer, dict(rcv.KEPT))
rcv._volume, rcv._set, rcv.threading.Timer = _rv, _rs, _Timer
rcv.KEPT.update(path="", read=True)
ON = {"receiver": {"on": True, "ip": "10.0.0.1", "steps": 5, "mode": "steps", "device": "10.0.0.2"}}
def fresh(vol=90):
    rcv.RAISED.clear(); _Recv.vol, _Recv.fails, _Recv.sets, _Recv.timers = vol, 0, [], []
fresh()
got = rcv.raise_for(ON, "tv", "e1")
check("raised by the steps set: %s" % got, got.get("to") == 100 and rcv.RAISED["tv|e1"]["by"] == 10)
got = rcv.lower_for(ON, "tv", "e1")
check("ended: back where it was, nothing kept", got.get("to") == 90 and _Recv.vol == 90 and not rcv.RAISED)
fresh(); rcv.raise_for(ON, "tv", "e1"); _Recv.fails = 1
got = rcv.lower_for(ON, "tv", "e1")
check("the receiver not answering: the raise stays on record and another try is set: %s" % got,
      got.get("ok") is False and got.get("again") == rcv.RETRIES - 1 and "tv|e1" in rcv.RAISED
      and len(_Recv.timers) == 1 and _Recv.timers[0][0] == rcv.RETRY_SECS and _Recv.vol == 100)
secs, fn, args = _Recv.timers.pop()
got = fn(*args)
check("the next try lowers it and clears the record: %s" % got,
      got.get("ok") is True and _Recv.vol == 90 and not rcv.RAISED and not _Recv.timers)
fresh(); rcv.raise_for(ON, "tv", "e1"); rcv.RAISED["tv|e1"]["ending"] = 1.0
rcv.RAISED["tv|e1"]["down"] = 90; _Recv.vol = 90; _Recv.sets = []
got = rcv._lower_now(ON, "tv|e1")
check("a set that landed before it timed out is not subtracted twice",
      got.get("ok") is True and _Recv.vol == 90 and _Recv.sets == [] and not rcv.RAISED)
fresh(); rcv.raise_for(ON, "tv", "e1"); _Recv.fails = 99
got = rcv.lower_for(ON, "tv", "e1")
n = 1
while _Recv.timers:
    secs, fn, args = _Recv.timers.pop(); fn(*args); n += 1
check("tried %d times and no more; the raise is still on record" % rcv.RETRIES,
      n == rcv.RETRIES and "tv|e1" in rcv.RAISED)
_Recv.fails = 0
got = rcv.raise_for(ON, "tv", "e2")
check("and the next film on that screen counts it into its base: %s" % got,
      got.get("to") == 100 and _Recv.vol == 100 and list(rcv.RAISED) == ["tv|e2"])
fresh(); rcv.raise_for(ON, "tv", "e1"); _Recv.fails = 1
rcv.lower_for(ON, "tv", "e1")
rcv.raise_for(ON, "tv", "e1")
secs, fn, args = _Recv.timers.pop()
check("back on the same film before the retry: it stays up",
      fn(*args) is None and _Recv.vol == 100 and "tv|e1" in rcv.RAISED)
# how far it moves for a measured track: +5 at typical, by measurement near it, +5 far out
check("a track of typical loudness: +5", rcv.level_for(-22.2, -22.2) == 5.0)
check("quieter than typical by 3: 3 more; louder by 3: 3 less",
      rcv.level_for(-25.2, -22.2) == 8.0 and rcv.level_for(-19.2, -22.2) == 2.0)
check("at the edge of the band it is still corrected",
      rcv.level_for(-26.2, -22.2) == 9.0 and rcv.level_for(-18.2, -22.2) == 1.0)
check("a fringe one, either side: the plain +5",
      rcv.level_for(-29.2, -22.2) == 5.0 and rcv.level_for(-12.0, -22.2) == 5.0
      and rcv.level_for(-26.3, -22.2) == 5.0)
check("typical not known: the plain +5; not measured: not moved",
      rcv.level_for(-25, None) == 5.0 and rcv.level_for(None, -22.2) is None)
import random as _rnd
_r = _rnd.Random(3)
_lib = [_r.gauss(-22.2, 4.0) for _ in range(2000)]
_usual = rcv.typical(_lib)
_raises = [rcv.level_for(l, _usual) for l in _lib]
check("over a library the raise averages 5, and none is outside 1 to 9: mean %.2f"
      % (sum(_raises) / len(_raises)),
      abs(sum(_raises) / len(_raises) - 5.0) < 0.25 and min(_raises) >= 1.0 and max(_raises) <= 9.0)
check("typical is the middle reading, and unknown for too few",
      rcv.typical([-30, -20, -25] * 10) == -25 and rcv.typical([-20, -24] * 15) == -22.0
      and rcv.typical([-20] * 29) is None and rcv.typical([None, -20] * 20) is None)
# which reading a passed-through track is judged by, and what is left to measure
WHOLE = {"lufs": -24.0, "how": "spread", "size": 10}
check("Dolby: the reading with compression off, and none until it is made",
      rcv.reading_for(dict(WHOLE, flat=-20.5), "eac3") == -20.5 and rcv.reading_for(WHOLE, "AC3") is None)
check("any other codec: the one reading", rcv.reading_for(WHOLE, "dts") == -24.0
      and rcv.reading_for(dict(WHOLE, flat=-1.0), "truehd") == -24.0)
check("a single window or no reading is no reading",
      rcv.reading_for({"lufs": -24.0, "how": "window", "flat": -20}, "ac3") is None
      and rcv.reading_for(None, "ac3") is None)
check("left to measure: none, one window, another size, Dolby without the second reading",
      rcv.wants_measuring(None, "aac") and rcv.wants_measuring({"lufs": -24, "how": "window"}, "aac")
      and rcv.wants_measuring(WHOLE, "aac", size=11) and rcv.wants_measuring(WHOLE, "ac3"))
check("not left: whole and not Dolby, or Dolby with both",
      not rcv.wants_measuring(WHOLE, "aac") and not rcv.wants_measuring(WHOLE, "aac", size=10)
      and not rcv.wants_measuring(dict(WHOLE, flat=-22), "eac3"))
order = rcv.measure_order(
    [(5, "e", 1, "ac3"), (4, "d", 1, "aac"), (3, "c", 1, "ac3"), (2, "b", 1, "eac3"), (1, "a", 1, "ac3")],
    {"5": dict(WHOLE, flat=-20), "4": WHOLE, "3": WHOLE, "2": {"lufs": -20, "how": "window"}})
check("taken in this order: unmeasured, single window, Dolby lacking the second; the rest not at all: %s" % order,
      [o[0] for o in reversed(order)] == [1, 2, 3])
import pd_gpu
plain, flat = pd_gpu.loudness_cmd("f.mkv", length=3000), pd_gpu.loudness_cmd("f.mkv", length=3000, flat=True)
check("compression off is asked of the decoder once for each of the twenty samples, and only when wanted",
      "-drc_scale" not in plain and flat.count("-drc_scale") == 20 and plain.count("-i") == 20
      and all(flat[i + 1] == "0" and flat[i + 2] == "-ss" for i, a in enumerate(flat) if a == "-drc_scale")
      and [a for a in flat if a not in ("-drc_scale",)].count("0") + 0 >= 0)
short = pd_gpu.loudness_cmd("f.mkv", length=0, flat=True)
check("and for a file of unknown length, on its one window", short.count("-drc_scale") == 1
      and short.index("-drc_scale") < short.index("-i"))
# a track made into Dolby is written without the file's chapters
import shutil as _sh, subprocess as _sp, tempfile as _tf
_dir = _tf.mkdtemp(prefix="pd-rules-gpu-")
_eng = pd_gpu.Engine(_dir)
_src = {"file": "f.mkv", "width": 1280, "height": 720, "videoCodec": "h264", "audioCodec": "dts",
        "audioChannels": 6, "duration": 1200, "bitrate": 6000}
_made = _eng.command(_src, 0, 0, None, "passthrough")
_kept = _eng.command(dict(_src, audioCodec="ac3"), 0, 0, None, "passthrough", copy_video=True)
check("Dolby made or copied: delay_moov, and the chapters left out before the writer's flags",
      all("-map_chapters" in c and c[c.index("-map_chapters") + 1] == "-1"
          and c.index("-map_chapters") < c.index("-movflags")
          and c[c.index("-movflags") + 1].endswith("+delay_moov") for c in (_made, _kept)))
check("plain sound is written as it was", "-map_chapters" not in _eng.command(_src, 0, 0, None, "aac"))
if pd_gpu.FFMPEG:
    # the file that showed it, made up: a frame rate of 13978/583 and one chapter of 12 s
    open(os.path.join(_dir, "meta.txt"), "w").write(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000000000\nSTART=0\nEND=12000000000\ntitle=one\n")
    _odd = os.path.join(_dir, "odd.mkv")
    _sp.run([pd_gpu.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "testsrc2=size=320x240:rate=13978/583", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-i", os.path.join(_dir, "meta.txt"), "-t", "12", "-map", "0:v", "-map", "1:a", "-map_metadata", "2",
             "-map_chapters", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "flac", "-ac", "6", _odd],
            capture_output=True, timeout=120)
    _cmd = _eng.command(dict(_src, file=_odd, width=320, height=240, audioCodec="flac", duration=12, bitrate=900),
                        0, 0, None, "passthrough")
    _ran = _sp.run(_cmd, capture_output=True, timeout=120)
    check("an odd frame-rate fraction with a chapter: the writer takes it (%d bytes, exit %s)"
          % (len(_ran.stdout), _ran.returncode),
          _ran.returncode == 0 and b"is invalid" not in _ran.stderr and len(_ran.stdout) > 100000)
    _cut = _cmd.index("-map_chapters") if "-map_chapters" in _cmd else len(_cmd)
    _ran = _sp.run(_cmd[:_cut] + _cmd[_cut + 2:], capture_output=True, timeout=120)
    check("and that file is the faulty kind: with its chapters let in the writer refuses them",
          b"is invalid" in _ran.stderr)
else:
    print("     NOT RUN: no ffmpeg here, the chapter fault was not tried on a file")
_sh.rmtree(_dir, ignore_errors=True)
fresh()
got = rcv.raise_for(dict(ON, receiver=dict(ON["receiver"], mode="loudness")), "tv", "e1", rcv.level_for(-23, -23))
check("5 dB is ten of the receiver's units: 45.0 to 50.0 on its display: %s" % got,
      got.get("from") == 90 and got.get("to") == 100)
rcv.RAISED.clear()
rcv._volume, rcv._set, rcv.threading.Timer = _real[:3]
rcv.KEPT.update(_real[3]); rcv.RAISED.clear()

# a copy's requests to the main server: ask() is a GET and tell() a POST, and the path
# must be handled under that method - a GET for a POST-only path is a 404 nobody sees
import re as _re
_here = os.path.dirname(os.path.abspath(__file__))
_src = open(os.path.join(_here, "pd-server.py"), encoding="utf-8").read()
_fol = open(os.path.join(_here, "pd_follow.py"), encoding="utf-8").read()
def _span(name, parts=""):
    """Where a method stands in the source - and, for a route function, its parts after it."""
    at = _src.index("    def %s(self" % name)
    end = _src.index("\n    def ", at + 10)
    while parts and _src.startswith("\n    def %s" % parts, end):
        end = _src.index("\n    def ", end + 10)
    return at, end
_post, _get, _both = _span("_do_POST", "_post_part_"), _span("_do_GET", "_get_part_"), _span("follow_reads")
_handled = {"POST": set(), "GET": set()}
for _m in _re.finditer(r'path == "(/follow[^"]*)"', _src):
    if _both[0] < _m.start() < _both[1]:
        # answered for whichever handler calls the shared method
        for _how, _sp in (("POST", _post), ("GET", _get)):
            if "self.follow_reads(path)" in _src[_sp[0]:_sp[1]]:
                _handled[_how].add(_m.group(1))
    elif _post[0] < _m.start() < _post[1]:
        _handled["POST"].add(_m.group(1))
    elif _get[0] < _m.start() < _get[1]:
        _handled["GET"].add(_m.group(1))
_asked = {"GET": set(), "POST": set()}
for _text in (_src, _fol):
    for _m in _re.finditer(r'\b(ask|tell)\(\s*one,\s*"(/follow[^"?]*)', _text):
        _asked["GET" if _m.group(1) == "ask" else "POST"].add(_m.group(2))
check("the main server's follow paths were found under both methods: %s handled, %s asked"
      % ({k: len(v) for k, v in _handled.items()}, {k: len(v) for k, v in _asked.items()}),
      len(_handled["POST"]) > 10 and len(_handled["GET"]) > 5
      and len(_asked["GET"]) > 3 and len(_asked["POST"]) > 5)
for _how in ("GET", "POST"):
    _lost = sorted(_asked[_how] - _handled[_how])
    check("every follow path asked by %s is handled under %s: %s" % (_how, _how, _lost), not _lost)

# a module imported at the top is not imported again inside a function that uses it
# earlier: the second import makes the name local to the whole function, and the earlier
# use fails the first time that branch runs (the new releases row answered 500)
import ast as _ast
_dir = os.path.dirname(os.path.abspath(__file__))
_shadowed = []
for _f in sorted(n for n in os.listdir(_dir) if n.endswith(".py") and (n.startswith("pd_") or n == "pd-server.py")):
    _tree = _ast.parse(open(os.path.join(_dir, _f), encoding="utf-8").read())
    _top = set()
    for _n in _tree.body:
        if isinstance(_n, _ast.Import):
            _top |= {(a.asname or a.name).split(".")[0] for a in _n.names}
    for _fn in _ast.walk(_tree):
        if not isinstance(_fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        _local, _stack = {}, list(_fn.body)
        while _stack:
            _n = _stack.pop()
            if isinstance(_n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda, _ast.ClassDef)):
                continue
            if isinstance(_n, _ast.Import):
                for a in _n.names:
                    _nm = (a.asname or a.name).split(".")[0]
                    if _nm in _top:
                        _local.setdefault(_nm, _n.lineno)
            _stack.extend(_ast.iter_child_nodes(_n))
        for _n in _ast.walk(_fn):
            if isinstance(_n, _ast.Name) and _n.id in _local and _n.lineno < _local[_n.id]:
                _shadowed.append("%s %s(): %s used at line %d, imported again at %d"
                                 % (_f, _fn.name, _n.id, _n.lineno, _local[_n.id]))
check("no top-level module is imported again inside a function after being used there: %s"
      % sorted(set(_shadowed))[:4], not _shadowed)

import datetime
import pd_holidays as hol

D = datetime.date
# the season's shelf by the calendar: each holiday's first and last day, and the days
# either side of them
for day, want in ((D(2026, 10, 16), ""), (D(2026, 10, 17), "halloween"),
                  (D(2026, 11, 1), "halloween"), (D(2026, 11, 2), ""),
                  (D(2026, 11, 30), ""), (D(2026, 12, 1), "christmas"),
                  (D(2026, 12, 26), "christmas"), (D(2026, 12, 27), "newyear"),
                  (D(2026, 12, 31), "newyear"), (D(2027, 1, 1), "newyear"),
                  (D(2027, 1, 2), ""), (D(2027, 2, 6), ""), (D(2027, 2, 7), "valentine"),
                  (D(2027, 2, 14), "valentine"), (D(2027, 2, 15), ""),
                  (D(2026, 7, 1), "")):
    check("the calendar on %s is %r" % (day, want), hol.on_the_calendar(day) == want)
# Easter moves: Sunday 5 April 2026, 28 March 2027, 20 April 2025, 31 March 2024
for year, month, day in ((2026, 4, 5), (2027, 3, 28), (2025, 4, 20), (2024, 3, 31),
                         (2038, 4, 25), (2008, 3, 23)):
    check("Easter Sunday %d" % year, hol.easter_sunday(year) == D(year, month, day))
check("nine days before Easter is Easter's", hol.on_the_calendar(D(2026, 3, 27)) == "easter")
check("ten days before is not", hol.on_the_calendar(D(2026, 3, 26)) == "")
check("Easter Monday is", hol.on_the_calendar(D(2026, 4, 6)) == "easter")
check("the Tuesday after is not", hol.on_the_calendar(D(2026, 4, 7)) == "")
# what Settings says
check("off is none", hol.chosen("off", D(2026, 12, 24)) == "")
check("nothing set is none", hol.chosen(None, D(2026, 12, 24)) == "")
check("the calendar at Christmas", hol.chosen("auto", D(2026, 12, 24)) == "christmas")
check("the calendar in July", hol.chosen("auto", D(2026, 7, 1)) == "")
check("a holiday by name, any day", hol.chosen("christmas", D(2026, 7, 1)) == "christmas")
check("a word that is no holiday", hol.chosen("midsummer", D(2026, 6, 20)) == "")
check("every choice is a known one",
      all(hol.chosen(c, D(2026, 12, 24)) in ("",) + tuple(hol.BY_ID) for c in hol.CHOICES))
check("each holiday says its days", all(hol.in_words(h) for h in hol.HOLIDAYS))
# the shelf's order: the catalogue's, one card a film, the unlisted left out
cards = [{"ratingKey": "a", "tmdb": 3}, {"ratingKey": "b", "tmdb": 1},
         {"ratingKey": "o1", "tmdb": 1}, {"ratingKey": "c", "tmdb": 99},
         {"ratingKey": "d", "tmdb": None}, {"ratingKey": "e", "tmdb": "2"}]
got = [c["ratingKey"] for c in hol.in_order(cards, [1, 2, 3])]
check("in the catalogue's order, held before offered, unlisted out: %s" % got,
      got == ["b", "e", "a"])
check("no list, no shelf", hol.in_order(cards, []) == [])

# a machine's own sleep is not a job's: after a night asleep a waiting job is not stalled
import pd_beat
import time
pd_beat.BEATS.clear()
pd_beat.BEATS["t"] = {"step": "nothing new", "at": time.time() - 9 * 3600, "rest": 3600, "limit": 0}
check("nine hours into an hour's rest is stalled", not pd_beat.status()["t"]["ok"])
pd_beat.slept(8.5 * 3600)
check("less the eight and a half the machine slept, it is not", pd_beat.status()["t"]["ok"])
pd_beat.BEATS.clear()

# the main server's loudness readings, taken by a copy for the files it holds
import pd_follow
rows = [{"name": "A Film (2020).mkv", "size": 100, "lufs": -21.5, "dialnorm": -27, "when": 5},
        {"name": "Other.mkv", "size": 7, "lufs": -19.0, "when": 6},
        {"name": "Silent.mkv", "size": 9, "lufs": -70.0, "when": 7}]
files = [(1, "D:/films/a film (2020).mkv", 100), (2, "D:/films/Other.mkv", 8),
         (3, "D:/films/Silent.mkv", 9), (4, "D:/films/Own.mkv", 50), (5, "D:/x/Other.mkv", 7)]
known = {"5": {"lufs": -18.0, "how": "spread"}, "4": {"lufs": -20.0, "how": "spread"}}
got = pd_follow.loudness_for_copy(rows, files, known)
check("the same name and length is the same file: %s" % sorted(got), sorted(got) == ["1"])
check("with the reading and where it came from",
      got.get("1", {}).get("lufs") == -21.5 and got["1"]["from"] == "main" and got["1"]["how"] == "spread")
check("another length is another file, silence is no reading, one made here is kept",
      "2" not in got and "3" not in got and "5" not in got)
known = {"5": {"lufs": -18.0, "how": "window"}}
check("a reading from one window here gives way to the main server's whole-file one",
      "5" in pd_follow.loudness_for_copy(rows, files, known))
rows2 = [dict(r, flat=(float(r["lufs"]) + 3.0) if r.get("lufs") is not None else None) for r in rows]
got = pd_follow.loudness_for_copy(rows2, files, {"1": {"lufs": -30.0, "how": "spread", "size": 10}})
check("its own whole-file reading is kept and the main server's compression-off one added to it: %s" % got.get("1"),
      got.get("1", {}).get("lufs") == -30.0 and got["1"].get("flat") == -18.5 and "from" not in got["1"])
check("one it has both of is left alone",
      "1" not in pd_follow.loudness_for_copy(rows2, files, {"1": {"lufs": -30.0, "how": "spread", "flat": -29.0}}))
check("a new one comes with both", pd_follow.loudness_for_copy(rows2, files, {}).get("1", {}).get("flat") == -18.5)

# one table of what a copy knows of its house, not two under one name
check("the follow module's house table holds the owner and the doors together: %s" % sorted(pd_follow.HOUSE),
      pd_follow.HOUSE.get("owner") == "" and all(k in pd_follow.HOUSE for k in ("at", "lan", "outside", "name", "id")))
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
check("and is made once", len(_re.findall(r"(?m)^HOUSE\b[^\n=]*=", _src)) == 1)
# the route functions stay cut into parts a checker can read
_srv = _ast.parse(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read())
_long = [(n.name, n.end_lineno - n.lineno + 1) for n in _ast.walk(_srv)
         if isinstance(n, _ast.FunctionDef) and n.end_lineno - n.lineno + 1 > 700]
check("no function in the server is over 700 lines: %s" % _long, not _long)
_twice = []
for _cls in [n for n in _srv.body if isinstance(n, _ast.ClassDef)] + [_srv]:
    _seen = set()
    for n in _cls.body:
        if isinstance(n, _ast.FunctionDef):
            if n.name in _seen:
                _twice.append(n.name)
            _seen.add(n.name)
check("nothing in the server is defined twice under one name: %s" % _twice, not _twice)

# ---- state files and a power cut: on the disk before the swap, and put back from a copy that reads
import json as _js
import shutil as _sh3
import tempfile as _tf3
import pd_safe
_d = _tf3.mkdtemp(prefix="pd-rules-safe-")
_p = os.path.join(_d, "settings.json")
def _put(path, data):
    with open(path, "wb") as f:
        f.write(data)
def _get(path):
    with open(path, "rb") as f:
        return f.read()
_good = _js.dumps({"port": 8764, "n": 1}).encode()
_zero = bytes(len(_good))
_put(_p, _good)
_calls, _fsync, _replace = [], os.fsync, os.replace
os.fsync = lambda fd: (_calls.append("fsync"), _fsync(fd))[1]
os.replace = lambda a, b: (_calls.append("replace"), _replace(a, b))[1]
try:
    _made = pd_safe.spare(_p)
finally:
    os.fsync, os.replace = _fsync, _replace
check("a spare is forced to the disk before it carries the name: %s" % _calls,
      _made and _calls == ["fsync", "replace"] and _get(_p + ".bak") == _good)
_put(_p, _zero)
check("a file the right length and all zeros does not read", not pd_safe.readable(_p)
      and not pd_safe.readable(os.path.join(_d, "nothing.json")) and pd_safe.readable(_p + ".bak"))
check("and the spare is not refreshed from it", pd_safe.spare(_p) is False and _get(_p + ".bak") == _good)
check("nor is a dated copy made of it", pd_safe.daily(_p, today="20261001") is False
      and not [n for n in os.listdir(_d) if n.endswith("20261001.bak")])
_used = pd_safe.recover(_p)
check("an unreadable file is put back from its spare", _used == _p + ".bak" and _js.loads(_get(_p))["port"] == 8764)
check("and the damaged one is kept beside it",
      [_get(os.path.join(_d, n)) for n in os.listdir(_d) if n.startswith("settings.json.unreadable-")] == [_zero])
check("a file that reads is left alone, and so is one that is not there",
      pd_safe.recover(_p) is None and pd_safe.recover(os.path.join(_d, "nothing.json")) is None)
# the file and its spare both zeroed in the same second - what a pulled plug left
for n in os.listdir(_d):
    if n.startswith("settings.json.unreadable-"):
        os.remove(os.path.join(_d, n))
_put(_p, _zero); _put(_p + ".bak", _zero)
_put(_p + ".20261001.bak", _js.dumps({"port": 8764, "n": 1}).encode())
_put(_p + ".20261005.bak", _js.dumps({"port": 8764, "n": 5}).encode())
_put(_p + ".20261008.bak", _zero)
_used = pd_safe.recover(_p)
check("with the spare gone too, the newest dated copy that reads is used: %s" % os.path.basename(str(_used)),
      str(_used).endswith("20261005.bak") and _js.loads(_get(_p))["n"] == 5)
# nothing that reads anywhere: the damaged file stays exactly where it is
_e = os.path.join(_d, "invites.json")
_put(_e, _zero); _put(_e + ".bak", _zero)
check("with no copy that reads, nothing is touched and nothing empty is written",
      pd_safe.recover(_e) is None and _get(_e) == _zero and sorted(os.listdir(_d)).count("invites.json") == 1
      and not [n for n in os.listdir(_d) if n.startswith("invites.json.unreadable-")])
# one dated copy a day, seven kept
_k = os.path.join(_d, "library.json")
_put(_k, _good)
_first = [pd_safe.daily(_k, keep=7, today="202610%02d" % day) for day in range(1, 10)]
_dated = sorted(n for n in os.listdir(_d) if n.startswith("library.json.2026"))
check("a dated copy a day, the newest seven kept: %s" % [n[13:21] for n in _dated],
      all(_first) and len(_dated) == 7 and _dated[0].endswith("20261003.bak") and _dated[-1].endswith("20261009.bak"))
check("and not a second one the same day", pd_safe.daily(_k, keep=7, today="20261009") is False)
_sh3.rmtree(_d, ignore_errors=True)
# every writer of a file that cannot be rebuilt settles it before the swap
_here2 = os.path.dirname(os.path.abspath(__file__))
_need = {"pd-server.py": 5, "pd_follow.py": 1, "pd_invites.py": 1, "pd_library.py": 1, "pd_torrents.py": 1,
         "pd_traffic.py": 1}
_have = {n: open(os.path.join(_here2, n), encoding="utf-8").read().count("pd_safe.settle(f)") for n in _need}
check("each state writer forces its file to the disk before swapping it in: %s" % _have,
      all(_have[n] >= _need[n] for n in _need))
_srvtext = open(os.path.join(_here2, "pd-server.py"), encoding="utf-8").read()
check("the settings' spare is never a blind copy of whatever is there",
      'shutil.copyfile(path, path + ".bak")' not in _srvtext and "pd_safe.spare(path)" in _srvtext
      and "pd_safe.daily(path)" in _srvtext)
check("an unreadable file is put back before the port is read from the settings",
      0 < _srvtext.index("pd_safe.recover(os.path.join(ROOT, _name))") < _srvtext.index("PORT = _port_wanted()"))

# ---- check and repair: the index and the state files
import sqlite3 as _sq
import pd_repair
_r = _tf3.mkdtemp(prefix="pd-rules-repair-")
def _index(name):
    path = os.path.join(_r, name)
    con = _sq.connect(path)
    con.execute("create table item (id text primary key, title text)")
    con.execute("create index by_title on item(title)")
    con.executemany("insert into item values (?,?)", [("k%05d" % i, "title-%05d" % i) for i in range(2000)])
    con.commit()
    return path, con
_db, _con = _index("sound.db"); _con.close()
_said = pd_repair.index_repair(_db)
check("a sound index is left alone and no copy is taken: %s" % _said,
      _said["ok"] and _said["did"] == "sound" and not pd_repair.index_copies(_db))
# an index that no longer holds what it says it holds: its entries are titles, its definition now says ids
_db, _con = _index("disagrees.db")
_con.execute("PRAGMA writable_schema=ON")
_con.execute("UPDATE sqlite_master SET sql='CREATE INDEX by_title ON item(id)' WHERE name='by_title'")
_con.commit(); _con.close()
check("an index that disagrees with its table is found damaged", not pd_repair.index_health(_db)["ok"])
_said = pd_repair.index_repair(_db)
_rows = _sq.connect(_db).execute("select count(*) from item").fetchone()[0]
check("it is copied first, its indexes rebuilt, and it is sound with every row: %s" % _said,
      _said["ok"] and _said["did"] == "indexes rebuilt" and len(pd_repair.index_copies(_db)) == 1
      and _said["copy"] == os.path.basename(pd_repair.index_copies(_db)[0])
      and pd_repair.index_health(_db)["ok"] and _rows == 2000)
# pages gone: not something a rebuild of indexes mends - said, copied, and left as it is
_db, _con = _index("pages.db"); _con.close()
_raw = bytearray(_get(_db)); _raw[100:6000] = bytes(5900); _put(_db, bytes(_raw))
_said = pd_repair.index_repair(_db)
check("an index with pages gone is said to be damaged, copied, and not touched: %s" % _said["did"],
      not _said["ok"] and _said["did"] == "damaged" and _said["said"] and _get(_db) == bytes(_raw)
      and len(pd_repair.index_copies(_db)) == 1)
check("no index at all is said, and nothing is made in its place",
      pd_repair.index_repair(os.path.join(_r, "none.db"))["did"] == "missing"
      and not os.path.exists(os.path.join(_r, "none.db")))
# copies of the index: the newest three kept
_db, _con = _index("copies.db"); _con.close()
for _n in range(5):
    pd_repair.index_copy(_db, now=1791600000 + _n * 60)
check("three copies of the index are kept, the newest: %d" % len(pd_repair.index_copies(_db)),
      len(pd_repair.index_copies(_db)) == 3)
# the state files
_put(os.path.join(_r, "settings.json"), bytes(40))
_put(os.path.join(_r, "settings.json.bak"), _js.dumps({"port": 8764}).encode())
_put(os.path.join(_r, "invites.json"), _js.dumps([{"name": "x"}]).encode())
_put(os.path.join(_r, "tracker.json"), bytes(40))
_files = {f["file"]: f for f in pd_repair.state_repair(_r)}
check("a state file that cannot be read is put back from its copy: %s" % _files["settings.json"],
      _files["settings.json"]["state"] == "put back" and _files["settings.json"]["from"] == "settings.json.bak"
      and _js.loads(_get(os.path.join(_r, "settings.json")))["port"] == 8764)
check("one that reads is left, one with no copy is said to be unreadable, one not there is not made",
      _files["invites.json"]["state"] == "reads" and _files["tracker.json"]["state"] == "unreadable"
      and _get(os.path.join(_r, "tracker.json")) == bytes(40)
      and _files["library.json"]["state"] == "absent" and not os.path.exists(os.path.join(_r, "library.json")))
_all = pd_repair.everything(_r, os.path.join(_r, "sound.db"))
check("the verdict is not ok while anything is still unreadable, and names it",
      _all["ok"] is False and any(r["what"] == "tracker.json" and r["state"] == "unreadable" for r in _all["rows"])
      and _all["rows"][0]["what"] == "Library index" and _all["rows"][0]["state"] == "sound")
os.remove(os.path.join(_r, "tracker.json"))
check("and ok once everything reads", pd_repair.everything(_r, os.path.join(_r, "sound.db"))["ok"] is True)
_sh3.rmtree(_r, ignore_errors=True)
check("the repair is the owner's alone",
      _re.search(r'if path == "/library/repair":[\s\S]{0,420}if self\.role != "owner":\s+self\.send_error\(403', _srvtext) is not None)

# ---- downloads at once: qBittorrent's queue, seeding never limited
import pd_torrents as _tor
check("one at a time unless set otherwise", _tor.at_once({}) == 1 and _tor.at_once({"atOnce": "x"}) == 1)
check("set, and held to 0..5", _tor.at_once({"atOnce": 2}) == 2 and _tor.at_once({"atOnce": 0}) == 0
      and _tor.at_once({"atOnce": 50}) == 5 and _tor.at_once({"atOnce": -3}) == 0)
_q = _tor.queue_prefs(1)
check("one at a time: the queue on, one download, seeding unlimited: %s" % _q,
      _q["queueing_enabled"] is True and _q["max_active_downloads"] == 1
      and _q["max_active_uploads"] == -1 and _q["max_active_torrents"] == -1
      and _q["dont_count_slow_torrents"] is False)
check("no limit is the client's -1, and nothing else is touched",
      _tor.queue_prefs(0) == {"max_active_downloads": -1})
class _FakeQB:
    have = {"queueing_enabled": False, "max_active_downloads": 3, "max_active_uploads": 3,
            "max_active_torrents": 5, "dont_count_slow_torrents": True}
    told = []
    def __init__(self, cfg): pass
    def preferences(self): return dict(self.have)
    def set_preferences(self, p): self.told.append(dict(p)); self.have.update(p)
_realqb, _realload = _tor.QB, _tor.load
_tor.QB = _FakeQB
_tor.load = lambda: {"config": {"atOnce": 1}, "packs": [], "downloads": []}
try:
    _first = _tor.keep_at_once(force=True)
    _second = _tor.keep_at_once(force=True)
    _tor.STATE["atOnceAt"] = time.time()
    _quiet = _tor.keep_at_once()
finally:
    _tor.QB, _tor.load = _realqb, _realload
check("what differs is put to the client, seeding limits lifted with it: %s" % _first,
      _first.get("max_active_downloads") == 1 and _first.get("max_active_torrents") == -1
      and _first.get("queueing_enabled") is True and len(_FakeQB.told) == 1)
check("and once it agrees nothing more is sent", _second == {} and len(_FakeQB.told) == 1)
check("between checks the client is not asked at all", _quiet == {})

# ---- subtitles beside a film: verified first
import pd_localapi as _pla
_rank = _pla.side_rank
_files = {"named for the release": _rank(False, True, False, False, 900),
          "verified, made from the soundtrack": _rank(False, False, True, False, 0),
          "chosen last time": _rank(False, False, False, True, 50),
          "most downloaded": _rank(False, False, False, False, 5000),
          "verified but runs past the end": _rank(True, True, True, True, 9999)}
_order = [k for k, _ in sorted(_files.items(), key=lambda kv: kv[1])]
check("a verified subtitle opens before one only named for the release: %s" % _order,
      _order == ["verified, made from the soundtrack", "chosen last time", "named for the release",
                 "most downloaded", "verified but runs past the end"])
check("between two verified, the one chosen last time", _rank(False, False, True, True, 0)
      < _rank(False, True, True, False, 999))

# ---- subtitles made from the sound: English and Swedish from any language
import pd_subs_make as _mk
check("English from any language is Whisper's own", _mk.route("en", "fr") == ("whisper", None)
      and _mk.route("en", "ko") == ("whisper", None) and _mk.route("en", "en") == ("as spoken", None))
check("Swedish from Swedish speech is written down as spoken", _mk.route("sv", "sv") == ("as spoken", None))
check("Swedish from English, French, Korean, Japanese: translated from that language itself",
      _mk.route("sv", "en") == ("nllb", "eng_Latn") and _mk.route("sv", "fr") == ("nllb", "fra_Latn")
      and _mk.route("sv", "ko") == ("nllb", "kor_Hang") and _mk.route("sv", "ja") == ("nllb", "jpn_Jpan"))
check("a language NLLB does not carry, and a third subtitle language, are refused",
      _mk.route("sv", "la") is None and _mk.route("sv", "") is None and _mk.route("de", "fr") is None)
check("every NLLB code is a language and a script: %s" % [v for v in _mk.NLLB.values()
                                                          if not _re.fullmatch(r"[a-z]{3}_[A-Z][a-z]{3}", v)],
      all(_re.fullmatch(r"[a-z]{3}_[A-Z][a-z]{3}", v) for v in _mk.NLLB.values()) and len(_mk.NLLB) >= 90)
_mksrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_subs_make.py"), encoding="utf-8").read()
check("the translation is asked for from the language spoken, not from English",
      "into_swedish(fresh_words, source=how[1])" in _mksrc and 'src_lang=source' in _mksrc)

# ---- a verified subtitle is recognised whichever way its name was written down
_same = _pla.same_release
check("verified as \"English X\" and listed as \"X\": the same subtitle",
      _same("English Show.410.fqm.VO", "Show.410.fqm.VO") and _same("Show.410.fqm.VO", "english show.410.fqm.vo"))
check("and with .srt on one of them", _same("Show S01E02.srt", "Swedish Show S01E02"))
check("another release is not the same", not _same("English Show.410.fqm", "Show.410.LOL")
      and not _same("", "x") and not _same("x", None))
check("a name that only starts with a language word is not cut short",
      not _same("Englishman in New York", "man in New York"))

# ---- the skins: five to choose from, each readable
import pd_skins as _sk
_ids = [s["id"] for s in _sk.all_of()]
check("House, the standard, first and five more to choose from, the old ones gone: %s" % _ids,
      _ids == ["house", "crimson", "harbour", "graphite", "violet", "archive"])
check("House stays the standard: nothing said, or an old one, wears it",
      _sk.known("house") == "house" and all(_sk.known(x) == _sk.DEFAULT == "house"
                                            for x in ("paper", "green", "", None)))
check("the machine's own two still exist, and are not offered",
      _sk.one("night")["imposed"] and _sk.one("arcade")["imposed"] and not set(_ids) & set(_sk.IMPOSED))
for _s in _sk.all_of():
    _fg = _sk.contrast(_s["fg"], _s["bg"]); _dim = _sk.contrast(_s["dim"], _s["bg"])
    check("%s: text reads on its ground (%.1f, dim %.1f)" % (_s["id"], _fg, _dim), _fg >= 7 and _dim >= 4.5)
    if _s.get("accent"):
        _a = _sk.contrast(_s["accent"], _s["bg"]); _on = _sk.contrast(_s["onAccent"], _s["accent"])
        check("%s: the accent stands out (%.1f) and its text reads on it (%.1f)" % (_s["id"], _a, _on),
              _a >= 3 and _on >= 3.5)
    check("%s: corner and titles said" % _s["id"], 0.2 <= _s["corner"] <= 2 and _s["titles"] in ("sans", "serif"))
check("white text on the red accent, dark text on the gold", _sk.on("#e50914") == "#ffffff"
      and _sk.on("#d9a441") == "#111111")

# ---- a finished tracker fetch stays on its card until the library has read it
import pd_tracker as _srv
if True:
    _now = 1_800_000_000
    _log = [{"name": "Some Film 2026 1080p WEB-DL H 264-GRP", "who": "guest", "size": 9, "at": _now - 900},
            {"name": "Other Film 2025 1080p WEB-DL H 264-GRP", "who": "owner", "at": _now - 900},
            {"name": "A Show S02E03 1080p WEB H264-GRP", "at": _now - 900},
            {"name": "Old Film 2024 1080p BluRay x264-GRP", "at": _now - 9000}]
    _done = {_srv.release_flat("Some.Film.2026.1080p.WEB-DL.H.264-GRP.mkv"): _now - 60,
             _srv.release_flat("Other.Film.2025.1080p.WEB-DL.H.264-GRP.mkv"): _now - 60,
             _srv.release_flat("A.Show.S02E03.1080p.WEB.H264-GRP.mkv"): _now - 60,
             _srv.release_flat("Old.Film.2024.1080p.BluRay.x264-GRP.mkv"): _now - 5000}
    _held = lambda title, year: title == "Other Film"
    _got = _srv.landing_now(_log, _done, _held, _now)
    check("finished, not read in yet: kept at 100%% with who asked: %s" % [g["name"] for g in _got],
          [g["name"] for g in _got] == ["Some Film 2026 1080p WEB-DL H 264-GRP"]
          and _got[0]["progress"] == 1.0 and _got[0]["who"] == "guest" and _got[0]["from"] == "tracker")
    check("one still downloading is not landing",
          not _srv.landing_now(_log[:1], {}, lambda t, y: False, _now))
    check("held, an episode, or finished long ago: gone from the list",
          not _srv.landing_now(_log[1:], _done, _held, _now))
_lasrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the release row's dates match held films on every listing, not only on the row's own page",
      _lasrc.count("self._mark_held()") >= 2 and "changed = self._mark_held()" in _lasrc)

# ---- the soundtrack: the film's own language, and what this viewer chose before
_pa = _pla.pick_audio
_film = [{"index": 1, "lang": "kor", "title": "Korean"}, {"index": 2, "lang": "eng", "title": "English"}]
check("a Korean film plays Korean, not the English dub", _pa(_film, "ko") == 0)
check("nothing known of its language: English, as before", _pa(_film, "") == 1)
check("chosen before: that one, over the film's own", _pa(_film, "ko", {"lang": "eng", "title": "English"}) == 1)
_dubbed = [{"index": 1, "lang": "ger", "title": ""}, {"index": 2, "lang": "eng", "title": ""}]
check("an English film with a dub in front plays English", _pa(_dubbed, "en") == 1)
_comm = [{"index": 1, "lang": "eng", "title": "Commentary"}, {"index": 2, "lang": "fre", "title": ""},
         {"index": 3, "lang": "eng", "title": ""}]
check("the film's own language, never its commentary", _pa(_comm, "en") == 2 and _pa(_comm, "fr") == 1)
_two = [{"index": 1, "lang": "eng", "title": "5.1"}, {"index": 2, "lang": "eng", "title": "Stereo"}]
check("two in the chosen language: the one with the same name", _pa(_two, "en", {"lang": "eng", "title": "Stereo"}) == 1)
check("a chosen language the file has not got: the film's own", _pa(_film, "ko", {"lang": "spa", "title": ""}) == 0)
check("file and TMDB codes for one language", _pla.same_language("kor", "ko") and _pla.same_language("ger", "de")
      and _pla.same_language("tha", "th") and not _pla.same_language("eng", "ko") and not _pla.same_language("", "en"))

# ---- Next does not step over an episode a pack has
_gap, _span = _pla.next_gap, _pla.episode_span_end
check("a double episode holds the next number", _span("D:/x/Show.S03E12E13.1080p.mp4", 12) == 13
      and _span("Show.S03E12-E13.mkv", 12) == 13 and _span("Show.S03E12-13.mkv", 12) == 13
      and _span("Show.S03E12.mkv", 12) == 12 and _span("Show.S03E12E13.mkv", 5) == 5
      and _span("Show.S03E12.1080p.x265-E13.mkv", 12) == 12)
_s3 = [(3, n, "S3E%d" % n) for n in range(14, 24)]
check("after E12E13 with S4E01 held: S3E14 from the pack, not S4E01",
      _gap(3, 13, (4, 1), _s3) == "S3E14")
check("the next one held: nothing to fill in", _gap(3, 13, (3, 14), _s3) is None)
check("a gap of two: the first of them", _gap(3, 5, (3, 9), [(3, 7, "E7"), (3, 6, "E6")]) == "E6")
check("nothing held after: the next season's first from a pack",
      _gap(3, 23, None, [(4, 1, "S4E1"), (4, 2, "S4E2")]) == "S4E1")
check("nothing on offer: none", _gap(3, 13, (4, 1), []) is None)

# ---- a double episode file holds both episodes on the season's page
_held = _pla.held_numbers([(11, "Show.S03E11.mp4"), (12, "Show.S03E12E13.1080p.mp4"), (14, "Show.S03E14.mp4")])
check("E12E13 holds 13: not listed as out, not here yet (%s)" % sorted(_held), _held == {11, 12, 13, 14})
_lasrc2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the season page and the next-episode card count it",
      "have = {int(e.get(\"index\") or 0) for e in out} | filed" in _lasrc2
      and "episode_span_end(last[\"path\"]" in _lasrc2)

# ---- the subtitle source kept from the last episode
_src = _pla.release_source
check("a release's source from its name", _src("Show.S03E12.1080p.BluRay.x265-RBG") == "bluray"
      and _src("Show S03E12 Blu-ray") == "bluray" and _src("Show.S03E12.WEB-DL.x264") == "web"
      and _src("Show.S03E12.HDTV.XviD") == "hdtv" and _src("Show.S03E12.DVDRip") == "dvd"
      and _src("Show.S03E12.x264") == "")
_srvsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_tier = _srvsrc[_srvsrc.index("def rank_subtitles"):_srvsrc.index("def rank_subtitles") + 6000]
check("ranked after the verified group, before word overlap",
      _tier.index("release_family(name), 0)") < _tier.index("release_source(name) == source")
      < _tier.index("len(file_words & words),           # then"))
check("the list asks for it, and the automatic fetch takes the list's pick", _srvsrc.count("self.source_wanted(key,") == 1)

# ---- the list on offer keeps the rank inside each tier, not the download count
_off = _pla.offer_order([
    {"name": "BTN", "episode": True, "downloads": 11065},
    {"name": "LOL", "episode": True, "downloads": 25774},
    {"name": "other episode", "episode": False, "downloads": 90000},
    {"name": "same name", "episode": True, "sameName": True, "downloads": 1}])
check("ranked order kept: verified group above more downloads (%s)" % [r["name"] for r in _off],
      [r["name"] for r in _off] == ["same name", "BTN", "LOL", "other episode"])
check("the list uses it", "results = pd_localapi.offer_order(results)" in _srvsrc
      and "-int(r.get(\"downloads\") or 0))\n            results.sort(key=order)" not in _srvsrc)
_auto = _srvsrc[_srvsrc.index("def auto_subtitle("):_srvsrc.index("def place_later(")]
check("a subtitle beside of another group does not stop the verified group being fetched",
      "self.family_beside(src[\"file\"], want[\"language\"], families)" in _auto
      and "return keep_beside()        # the group is not on offer" in _auto
      and _auto.index("families = self.fitting_families") < _auto.index("found = self.find_subtitles"))
check("a sync with no correction is not sure", '"sure": bool(parts) and sure >= pd_sync.SURE_ENOUGH' in _srvsrc)

# ---- a person's page: the rest of their films and series from the catalogue, the tracker asked
import pd_streaming as _pst22
class _FakeLib22:
    def db(self):
        raise RuntimeError("no library here")
_old22 = (_pst22.STATE["root"], _pst22.SEARCHED["rows"])
_pst22.STATE["root"] = ""
_pst22.SEARCHED["rows"] = []
_films22 = _pst22.credit_rows(_FakeLib22(), [
    {"id": 1, "title": "Big", "release_date": "1988-06-03", "poster_path": "/b.jpg", "vote_count": 900, "popularity": 50},
    {"id": 2, "title": "Unmade", "release_date": "2999-01-01", "poster_path": "/u.jpg", "vote_count": 900},
    {"id": 3, "title": "Unseen", "release_date": "2001-01-01", "poster_path": "/s.jpg", "vote_count": 2},
    {"id": 4, "title": "Splash", "release_date": "1984-03-09", "poster_path": "/s2.jpg", "vote_count": 500, "popularity": 90}])
check("out already, heard of, best known first: %s" % [r["title"] for r in _films22],
      [r["title"] for r in _films22] == ["Splash", "Big"])
_shows22 = _pst22.show_credit_rows(_FakeLib22(), [
    {"id": 10, "name": "A Drama", "first_air_date": "2010-01-01", "poster_path": "/d.jpg", "episode_count": 20, "character": "Jim"},
    {"id": 11, "name": "Late Show", "first_air_date": "2015-01-01", "poster_path": "/l.jpg", "episode_count": 9, "character": "Self"},
    {"id": 12, "name": "Cameo", "first_air_date": "2012-01-01", "poster_path": "/c.jpg", "episode_count": 1, "character": "Bob"}])
check("series they are a regular in, not themselves on a chat show: %s" % [r["show"] for r in _shows22],
      [r["show"] for r in _shows22] == ["A Drama"])
_pst22.STATE["root"], _pst22.SEARCHED["rows"] = _old22
_l22 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the page asks the tracker about them and looks again soon while it does",
      "self._person_asking = pd_streaming.ask_tracker_about(rest[:12], self.lib)" in _l22
      and "(20 if kept[2] else 600)" in _l22)

# ---- an episode with no release of its own: the season packs that hold it, listed
import pd_boxsets as _pbx29
_rows29 = [{"id": "1", "name": "Road to Timbuktu S01 480p AMZN WEB-DL-FLUX", "seeds": 11},
           {"id": "2", "name": "Road to Timbuktu S01E02 720p", "seeds": 5},
           {"id": "3", "name": "Keeping Up With the Neighbours S01-S20 1080p", "seeds": 30},
           {"id": "4", "name": "Long Road North S01 1080p", "seeds": 50}]
check("its own season's pack, not a single episode, not another programme",
      [r["id"] for r in _pbx29.season_packs(_rows29, "Road to Timbuktu", 1)] == ["1"])
check("and a run of seasons that takes it in",
      [r["id"] for r in _pbx29.season_packs(_rows29, "Keeping Up With the Neighbours", 7)] == ["3"])
_s29b = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the episode's version list offers it, and asks for the season when none is known",
      "boxed = pd_boxsets.episode_in_packs(" in _s29b
      and 'pd_tracker.ask_for("%s S%02d" % (want, season), 0)' in _s29b)

# ---- a season whose only packs miss the choices still gets one, the best seeded
_s29 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the finder takes the best seeded pack when none meets the choices",
      'best = found.get("pick") or max(' in _s29 and 'if int(o.get("seeds") or 0) > 0],' in _s29)

# ---- a studio's logo as the white mark the page draws: its letters, not its box
import pd_sports as _psp28
import io as _io
from PIL import Image as _I28, ImageDraw as _D28
_b28 = _I28.new("RGBA", (120, 40), (0, 0, 0, 255)); _D28.Draw(_b28).rectangle([40, 10, 80, 30], fill=(255, 255, 255, 255))
_o28 = _io.BytesIO(); _b28.save(_o28, "PNG")
_m28 = _I28.open(_io.BytesIO(_psp28.studio_mark(_o28.getvalue()))).getchannel("A")
check("white letters in a black box: the letters stay, the box goes",
      _m28.getpixel((60, 20)) > 200 and _m28.getpixel((5, 5)) < 30)
_l28 = _I28.new("RGBA", (120, 40), (0, 0, 0, 0)); _D28.Draw(_l28).rectangle([40, 10, 80, 30], fill=(20, 20, 20, 255))
_o28 = _io.BytesIO(); _l28.save(_o28, "PNG")
_n28 = _I28.open(_io.BytesIO(_psp28.studio_mark(_o28.getvalue()))).getchannel("A")
check("dark letters on nothing: kept, white", _n28.getpixel((60, 20)) > 200 and _n28.getpixel((5, 5)) < 30)
_la28 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the country chosen sifts the packs' programmes too",
      "extra=extra, lands=lands)" in _la28 and "if lands & {str(c).upper() for c in o.get(\"countries\") or []}]" in _la28)

# ---- an empty file already found empty is walked past by a scan, and so leaves the library
_lb27 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_library.py"), encoding="utf-8").read()
check("the scan passes an empty file by",
      'empty = {r["path"] for r in con.execute("SELECT path FROM file WHERE hollow=1")}' in _lb27
      and "if path in empty and hollow_file(path):" in _lb27)

# ---- a cancelled download's empty file is no film
_uf = __import__("pd_torrents").unfinished_file
check("begun or asked for: unfinished; not asked and empty: unfinished; not asked and real: a film",
      _uf(0.5, 0, "x") and _uf(0.0, 1, "x") and _uf(0.0, 0, "x", hollow=lambda p: True)
      and not _uf(0.0, 0, "x", hollow=lambda p: False) and not _uf(1.0, 0, "x", hollow=lambda p: True))

# ---- a film not asked of the tracker yet is Checking, and stands unless both kinds are hidden
_ns = _pla.LocalAPI.new_one_shown
check("checking: shown with Request hidden, hidden only when Download is too",
      _ns(False, {"download": True, "request": False}, True)
      and not _ns(False, {"download": True, "request": False}, False)
      and not _ns(False, {"download": False, "request": False}, True))
_ls28 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("every shelf passes it on, and the Films tab asks about them behind it",
      # the shelves, a person's films, and a streaming service's catalogue
      _ls28.count('.get("onTracker"), show, ') == 4
      and "pd_streaming.ask_tracker_about([r for r in waiting if r], self.lib)" in _ls28)

# ---- a person's titles under a search for their name: each in its own row, once
_hubs27 = [{"type": "show", "title": "TV shows", "Metadata": [{"ratingKey": "iasip", "title": "It's Raining Again", "type": "show"}]}]
_pla.LocalAPI._person_in_search([{"ratingKey": "f1", "title": "Awful Managers", "type": "movie"},
                                 {"ratingKey": "iasip", "title": "It's Raining Again", "type": "show"},
                                 {"ratingKey": "tv9", "title": "Epic Quest", "type": "show"}], _hubs27)
check("films with the films, series with the series, a held series not twice: %s" % [(h["type"], [x["title"] for x in h["Metadata"]]) for h in _hubs27],
      [(h["type"], [x["title"] for x in h["Metadata"]]) for h in _hubs27]
      == [("movie", ["Awful Managers"]), ("show", ["It's Raining Again", "Epic Quest"])])

# ---- a collection the words name: its films, the first one included
import pd_streaming as _pst24
class _FakeLib24:
    def tmdb(self, path, timeout=20, **params):
        if path == "/search/collection":
            return {"results": [{"id": 84, "name": "Indiana Jones Collection"}]}
        assert path == "/collection/84"
        return {"parts": [
            {"id": 89, "title": "Indiana Jones and the Last Crusade", "release_date": "1989-05-24", "poster_path": "/c.jpg", "vote_count": 9000},
            {"id": 85, "title": "Raiders of the Lost Ark", "release_date": "1981-06-12", "poster_path": "/r.jpg", "vote_count": 9000}]}
    def db(self):
        raise RuntimeError("no library here")
_old24 = (_pst24.STATE["root"], _pst24.SEARCHED["rows"])
_pst24.STATE["root"] = ""
_pst24.SEARCHED["rows"] = []
_pst24.FOUND_BY_NAME.clear()
_c24 = _pst24.collection_rows(_FakeLib24(), "Indiana Jones")
check("its films oldest first, the first one too: %s" % [r["title"] for r in _c24],
      [r["title"] for r in _c24] == ["Raiders of the Lost Ark", "Indiana Jones and the Last Crusade"])
_pst24.STATE["root"], _pst24.SEARCHED["rows"] = _old24
_l24 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("search adds them beside what matched, the tracker asked",
      "asking = self._collection_in_search(spelt, hubs) or asking" in _l24)

# ---- a season out on one day is one card: the earliest not watched
_d23 = lambda show, day, n, seen=0: {"grandparentRatingKey": show, "originallyAvailableAt": day,
                                     "parentIndex": 1, "index": n, "viewCount": seen}
_got23 = _pla.one_per_day([_d23("tyson", "2026-10-13", 4), _d23("tyson", "2026-10-13", 3),
                           _d23("tyson", "2026-10-13", 2), _d23("tyson", "2026-10-13", 1, 1),
                           _d23("office", "2026-10-12", 9), _d23("office", "2026-10-05", 8)])
check("one card for the day, the first not watched; other days apart: %s" % [(e["grandparentRatingKey"], e["index"]) for e in _got23],
      [(e["grandparentRatingKey"], e["index"]) for e in _got23] == [("tyson", 2), ("office", 9), ("office", 8)])
check("all watched: the last of the day",
      [e["index"] for e in _pla.one_per_day([_d23("x", "d", 1, 1), _d23("x", "d", 2, 1)])] == [2])

# ---- a season out on one day is in episode order
_eps22 = [{"originallyAvailableAt": "2026-10-13", "parentIndex": 1, "index": n} for n in (2, 4, 1, 3)]
_eps22.append({"originallyAvailableAt": "2026-10-12", "parentIndex": 9, "index": 9})
check("newest first: episode 4 to 1 of the same day, then the day before",
      [e["index"] for e in sorted(_eps22, key=_pla.release_order, reverse=True)] == [4, 3, 2, 1, 9])

# ---- the recommended version comes after what is on disk, whatever its seeders
_s21 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("on disk, then recommended, then by seeders",
      'out.sort(key=lambda o: (not o.get("disk"), not o.get("pick"), -(o["seeds"] or 0)))' in _s21)
_a21 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("and it says where it comes from as well", 'v.pick ? "Recommended" : "",' in _a21
      and 'v.pack ? "In a pack here" : v.box ? (v.seasonPack ? "Season pack" : "Box set") : "Tracker",' in _a21)

# ---- one budget for the tracker, shared by everything that asks it
_ptk2 = __import__("pd_tracker")
_was_spent, _was_per = {k: list(v) for k, v in _ptk2._SPENT.items()}, dict(_ptk2.PER_MINUTE)
_ptk2._SPENT.update(search=[], fetch=[])
_ptk2.PER_MINUTE.update(search=2, fetch=2)
_bg = _ptk2.quietly(lambda: _ptk2._spend("search"))
check("background work gives way once the minute is spent", _bg() and _bg() and not _bg()
      and not _ptk2.room("search"))
check("a person is still served, and counted", _ptk2._spend("search") and len(_ptk2._SPENT["search"]) == 3)
_ptk2._SPENT.clear(); _ptk2._SPENT.update(_was_spent)
_ptk2.PER_MINUTE.clear(); _ptk2.PER_MINUTE.update(_was_per)
_trs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_tracker.py"), encoding="utf-8").read()
check("every search and every torrent file goes through it, and one skipped is not marked asked",
      'if not _spend("search"):\n        return None' in _trs
      and 'if not row or not _spend("fetch"):' in _trs
      and "        return []                         # gave way: not marked asked" in _trs)
_bgsrc = "".join(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), n), encoding="utf-8").read()
                 for n in ("pd-server.py", "pd_localapi.py", "pd_streaming.py", "pd_boxsets.py"))
check("the background askers are marked as such",
      _bgsrc.count("pd_tracker.quietly(") >= 6 and "target=pd_tracker.ask_for" not in _bgsrc)

# ---- an episode of a series made of a pack wears its season's poster
_ptf9 = __import__("pd_torrents")
_ep9 = _ptf9.episode_item({"key": "o1", "season": 6, "episode": 1, "series": True,
                           "episodeName": "Anvil and Bolt: Death's Hand", "path": "x.mkv"}, "Hobby TV")
check("an offered one: %s" % _ep9["thumb"], str(_ep9["thumb"]).startswith("/art/series/hobbytv/s6"))
_ls9 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("and one that is here", "else season_art)," in _ls9 and "show_poster_missing" not in _ls9)

# ---- series from the catalogue join a search beside what matched, on every server
_ls7 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("not only when nothing matched: the cache and this server answered differently",
      "if spelt and len(spelt.strip()) >= 3 and not place:\n                self._catalogue_shows_in_search(spelt, hubs)" in _ls7
      and "if bare(str(r[\"show\"] or \"\")) not in have and int(r[\"tmdb\"]) not in have_tmdb]" in _ls7)

# ---- the TV tab stands the popular programmes not held, unless a viewer keeps them under Popular
_pla6 = __import__("pd_localapi")
check("always by default, popular when chosen, nothing else",
      _pla6.LocalAPI.tv_popular_of({}) == "always" and _pla6.LocalAPI.tv_popular_of({"tvPopular": "popular"}) == "popular"
      and _pla6.LocalAPI.tv_popular_of({"tvPopular": "x"}) == "always")
_ls6 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the TV tab joins them in with the packs' programmes, filtered and sorted as one",
      "and self.tv_popular() == \"always\" else [])" in _ls6 and "extra=extra, lands=lands)" in _ls6
      and "offers = offers + [o for o in extra if plain(o.get(\"title\")) not in here]" in _ls6)

# ---- a series pack's official pictures: its network's shows for its seasons
_ptf3 = __import__("pd_torrents")
_seas = {1: "Aces of Iron", 3: "Greyclaw - Season 1", 4: "Greyclaw - Season 2",
         28: "Lorekeepers - Age of Stone", 53: "Anvil & Bolt Original Score", 19: "Brainboyz"}
_net = [{"name": "Aces of Iron", "poster": "/aod.jpg"},
        {"name": "Greyclaw", "poster": "/bt.jpg", "seasons": {2: "/bt2.jpg"}},
        {"name": "Lorekeepers", "poster": "/lm.jpg"}, {"name": "Anvil and Bolt", "poster": "/hb.jpg"},
        {"name": "Angels", "poster": "/short.jpg"}]
check("each season the longest show name it starts with, a numbered season its own: %s"
      % _ptf3.match_season_posters(_seas, _net),
      _ptf3.match_season_posters(_seas, _net) == {1: "/aod.jpg", 3: "/bt.jpg", 4: "/bt2.jpg",
                                                  28: "/lm.jpg", 53: "/hb.jpg"})
_wasf3 = _ptf3.FOLDED["data"]
_ptf3.FOLDED["data"] = {"paths": {}, "seasons": {}, "shows": {},
                        "art": {"hobbytv": {"logo": "/logo.png", "seasons": {"3": "/bt.jpg"}}}}
check("the series wears its network's logo, a season its show's poster, each at a new address",
      _ptf3.series_art("Hobby TV") == "/art/series/hobbytv/poster?own=1"
      and _ptf3.series_art("Hobby TV", 3) == "/art/series/hobbytv/s3?own=1"
      and _ptf3.series_art("Hobby TV", 19) == "/art/series/hobbytv/s19"
      and _ptf3.folded_poster("hobbytv", 3) == "/bt.jpg")
_ptf3.FOLDED["data"] = _wasf3
from PIL import Image as _ImgL
import io as _io
_lb = _io.BytesIO(); _ImgL.new("RGBA", (200, 100), (255, 255, 255, 255)).save(_lb, "PNG")
import pd_sports as _psp
check("a logo drawn onto a poster", _ImgL.open(_io.BytesIO(_psp.logo_poster(_lb.getvalue(), 300))).size == (300, 450))
_lbd = _io.BytesIO(); _ImgL.new("RGBA", (200, 100), (0, 0, 0, 255)).save(_lbd, "PNG")
_drawn = _ImgL.open(_io.BytesIO(_psp.logo_poster(_lbd.getvalue(), 400)))
check("a black logo is drawn light", sum(_drawn.getpixel((200, 300))) > 600)
# ---- a seeder count nobody has asked the tracker for is not shown as none
_srv17 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("a pack's count from the client when it has one, else the tracker's list, else nothing",
      '"seeds": swarm.get(str(c["hash"]).lower()) or listed_seeds.get(' in _srv17
      and "for t in rows if int(t.get(\"num_complete\") or -1) > 0}" in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read())
check("and the web says nothing rather than 0", "(v.seeds || 0) + \" seeding\"" not in open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read())

# ---- a programme filed under a series pack: found as that series, never offered to Add
_ptf2 = __import__("pd_torrents")
_was_fold = _ptf2.FOLDED["data"]
_ptf2.FOLDED["data"] = {"paths": {}, "seasons": {"hobbytv": {"1": {"title": "Aces of Iron", "poster": "/p.jpg"}}},
                        "shows": {"132123": {"title": "Aces of Iron", "series": "Hobby TV", "season": 1}}}
check("its season's address changes when it wears the programme's poster",
      _ptf2.series_art("Hobby TV", 1) == "/art/series/hobbytv/s1?own=1"
      and _ptf2.series_art("Hobby TV", 2) == "/art/series/hobbytv/s2"
      and _ptf2.folded_shows() == {"132123": {"title": "Aces of Iron", "series": "Hobby TV", "season": 1}})
_ptf2.FOLDED["data"] = _was_fold
_lasrc5 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
_stsrc5 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_streaming.py"), encoding="utf-8").read()
check("search finds the series, and neither the catalogue nor Popular offers the programme to Add",
      "self._folded_in_search(con, spelt, hubs)" in _lasrc5
      and _lasrc5.index("self._folded_in_search(con, spelt, hubs)") < _lasrc5.index("self._popular_in_search(con, spelt, hubs)")
      and "held_tmdb |= {int(k) for k in pd_torrents.folded_shows()}" in _lasrc5
      and "or str(int(one.get(\"id\") or 0)) in filed):" in _stsrc5)

# ---- search finds the popular programmes not held yet, whatever else it finds
_lasrc4 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
_si = _lasrc4.index("    def _popular_in_search(")
check("popular programmes are matched by name and offered with Add, held ones left out",
      'if x.get("tmdb") and bare_in(spelt, x.get("show"))' in _lasrc4[_si:_si + 3000]
      and "and int(x[\"tmdb\"]) not in held_tmdb" in _lasrc4[_si:_si + 3000]
      and "self._popular_in_search(con, spelt, hubs)" in _lasrc4)

# ---- programmes held on their own that a series pack carries: filed under it
import pd_torrents as _ptf
_fpack = [{"films": [
    {"series": True, "title": "Hobby TV", "season": 1, "episode": 1, "seasonName": "Aces of Iron",
     "episodeName": "Aces of Iron: Blood and Duty"},
    {"series": True, "title": "Hobby TV", "season": 1, "episode": 12, "seasonName": "Aces of Iron",
     "episodeName": "Aces of Iron: In the Company of Death"},
    {"series": True, "title": "Hobby TV", "season": 6, "episode": 1, "seasonName": "Anvil and Bolt",
     "episodeName": "Anvil and Bolt: Death's Hand"},
    {"series": True, "title": "Hobby TV", "season": 53, "episode": 1, "seasonName": "Anvil & Bolt Original Score",
     "episodeName": "Anvil & Bolt Original Score: Death's Hand"}]}]
_fshows = [{"id": "a", "title": "Aces of Iron", "episodes": [{"title": "Blood and Duty", "path": "G:/a/1.mkv"}]},
           {"id": "o", "title": "Aces of Iron: Origins", "episodes": [{"title": "In the Company of Death", "path": "G:/o/3.mkv"}]},
           {"id": "h", "title": "Anvil and Bolt", "episodes": [{"title": "Death's Hand", "path": "G:/h/1.mkv"},
                                                                  {"title": "Not In The Pack", "path": "G:/h/9.mkv"}]},
           {"id": "x", "title": "Some Other Show", "episodes": [{"title": "Blood and Duty", "path": "G:/x/1.mkv"}]}]
_fm = {s: (t, m, n) for s, t, m, n in _ptf.fold_matches(_fshows, _fpack)}
check("each held episode filed as the pack's own, by title and the season named after it: %s" % _fm,
      _fm.get("a") == ("Hobby TV", {"G:/a/1.mkv": (1, 1, "Aces of Iron: Blood and Duty")}, 1)
      and _fm.get("o", (0, {}))[1] == {"G:/o/3.mkv": (1, 12, "Aces of Iron: In the Company of Death")})
check("not when one episode is missing from the pack, nor for a show the pack does not name",
      "h" not in _fm and "x" not in _fm)
_ptsrc_f = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
check("the library's hook files them there, the worker looks every ten minutes, the season wears its poster",
      'got = folded()["paths"].get(os.path.normcase(str(path or "")))' in _ptsrc_f
      and '_step("_fold_held", _fold_held)' in _ptsrc_f
      and "own = pd_torrents.folded_poster(m.group(1), season) if season else None"
      in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read())

# ---- a search with nothing here: series by that name, to Add as the popular list does
import pd_streaming as _pst3
import tempfile as _tfs3
class _FakeTv:
    def tmdb(self, path, timeout=20, **params):
        assert path == "/search/tv" and params["query"] == "neighbours"
        return {"results": [
            {"id": 191, "name": "The Neighbours", "first_air_date": "2022-04-14",
             "poster_path": "/k.jpg", "vote_count": 120, "overview": "x"},
            {"id": 192, "name": "Neighbours Unaired", "first_air_date": "", "poster_path": "/u.jpg",
             "vote_count": 99},
            {"id": 193, "name": "Neighbours Quiet", "first_air_date": "2020-01-01",
             "poster_path": "/q.jpg", "vote_count": 1}]}
    def db(self):
        raise RuntimeError("no library in this test")
_old3 = (_pst3.STATE["root"], _pst3.SEARCHED["rows"])
_pst3.STATE["root"] = _tfs3.mkdtemp(prefix="pd-rules-tv-")
_pst3.SEARCHED["rows"] = None
_pst3.FOUND_BY_NAME.clear()
_tv = _pst3.shows_by_name(_FakeTv(), "Neighbours")
check("aired, with a poster, heard of: %s" % [r["show"] for r in _tv],
      [(r["show"], r["key"], r["year"]) for r in _tv] == [("The Neighbours", "tv191", 2022)])
check("found again by its number after a restart", (_pst3.SEARCHED.update(rows=None) or True)
      and (_pst3.searched_show(191) or {}).get("show") == "The Neighbours"
      and _pst3.searched_show(999) is None)
_pst3.STATE["root"], _pst3.SEARCHED["rows"] = _old3
_lasrc3 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("their cards are Add cards, and their page and poster are found",
      '"genres": [], "addable": True, "tmdb": r["tmdb"]}' in _lasrc3
      and _lasrc3.count("pd_streaming.searched_show(") == 2)

# ---- box sets on the tracker: the films inside each, read once, found by search
import pd_boxsets as _pbx
def _bt(name, files):
    b = lambda s: b"%d:%s" % (len(s.encode()), s.encode())
    parts = b"".join(b"d6:lengthi%de4:pathl" % size + b"".join(b(p) for p in path.split("/")) + b"ee"
                     for path, size in files)
    return b"d4:infod5:filesl" + parts + b"e4:name" + b(name) + b"12:piece lengthi262144e6:pieces0:ee"
_box_raw = _bt("Sci-Fi Collection", [("Sci-Fi Collection/Timecrimes (2007)/Timecrimes.2007.1080p.mkv", 4000000000),
                                     ("Sci-Fi Collection/Primer 2004/Primer.2004.720p.mkv", 900000000),
                                     ("Sci-Fi Collection/Primer 2004/sample.mkv", 900000000),
                                     ("Sci-Fi Collection/extras/poster.jpg", 100000)])
_bf = _pbx.films_in(_box_raw)
check("the films in a box set's torrent: %s" % [(f["title"], f["year"]) for f in _bf],
      [(f["title"], f["year"], f["index"]) for f in _bf] == [("Timecrimes", 2007, 0), ("Primer", 2004, 1)])
check("a box set by its shelf or its name, and not a season",
      _pbx.is_boxset({"kind": "BoxSets", "name": "Mixed Bag 1080p"})
      and _pbx.is_boxset({"kind": "Movies", "name": "Sci-Fi Collection 1980-2010 1080p"})
      and _pbx.is_boxset({"kind": "", "name": "Forward to the Past Trilogy 1080p"})
      and not _pbx.is_boxset({"kind": "BoxSets", "name": "Some Show S01 1080p"})
      and not _pbx.is_boxset({"kind": "Movies", "name": "Primer 2004 1080p"}))
_bx_saved = {k: (list(v) if isinstance(v, list) else v) for k, v in _pbx.STATE.items()}
_pbx.STATE.update(sets={}, first=[], next_at=0.0, scraped_at=time.time(), by=None, unread=True, dirty=False)
_bx_rows = {"1": {"id": "1", "name": "Sci-Fi Collection 1080p", "kind": "BoxSets", "seeds": 9},
            "2": {"id": "2", "name": "Primer 2004 1080p", "kind": "Movies", "seeds": 50},
            "3": {"id": "3", "name": "Other Trilogy", "kind": "", "seeds": 2}}
_pbx.first(["3"])
check("asked for first, then the most carried, never a single film: %s" % _pbx.waiting(_bx_rows),
      _pbx.waiting(_bx_rows) == ["3", "1"])
_bx_fetched = []
_bx_fetch = lambda i: (_bx_fetched.append(i) or (_box_raw if i == "1" else None))
_now = time.time()
check("one box set a turn, six seconds apart",
      _pbx.step(_now, _bx_rows, _bx_fetch) and not _pbx.step(_now + 1, _bx_rows, _bx_fetch)
      and _pbx.step(_now + 7, _bx_rows, _bx_fetch) and _bx_fetched == ["3", "1"])
check("one that could not be fetched is tried again tomorrow, not every turn",
      _pbx.waiting(_bx_rows, _now + 60) == [] and _pbx.waiting(_bx_rows, _now + 90000) == ["3"])
_hit = _pbx.find("Timecrimes", 2007)
check("a search for the film finds it inside", len(_hit) == 1 and _hit[0][0] == "1"
      and _hit[0][2]["index"] == 0 and _pbx.find("Timecrimes", 1995) == [] and _pbx.find("Nope") == [])
import pd_streaming as _pst2
check("and the card says Download", _pst2.boxed({"title": "Primer", "year": 2004}))
_pbx.STATE.clear(); _pbx.STATE.update(_bx_saved)
_srvb = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("its version in the list, and Download adds the box set as a pack",
      '"id": "box:%s:%d" % (ident, int(film["index"]))' in _srvb
      and 'made = pd_boxsets.take(ident[4:])' in _srvb and 'ident = "pack:" + made["key"]' in _srvb
      and _srvb.index('if ident.startswith("box:"):') < _srvb.index('if ident.startswith("pack:"):'))

# ---- a tracker download is in the library once it is whole, not at the next scan
import pd_tracker as _ptk
class _FakeQB:
    def info(self, mark):
        return {"progress": 1.0 if mark == "whole" else 0.4, "save_path": r"D:\Download"}
    def files(self, mark):
        return [{"name": "Show.S29E03/show.s29e03.mkv"}, {"name": "Show.S29E03/show.s29e03.nfo"},
                {"name": "Show.S29E03/Sample/show.sample.mkv"}]
class _FakeTaker:
    def __init__(self):
        self.got = []
    def take_in(self, paths):
        self.got += paths
        return len(paths)
_was_taken, _was_root, _was_rows = _ptk.STATE.get("taken"), _ptk.STATE.get("root"), _ptk.STATE.get("rows")
_ptk.STATE["root"] = ""
_ptk.STATE["rows"] = _was_rows or {"x": {"id": "x", "at": time.time()}}
_ptk.STATE["taken"] = {"whole": {"id": "1", "name": "a"}, "half": {"id": "2", "name": "b"}}
_tk = _FakeTaker()
check("the whole one's video taken in, not its sample, nfo or the half-done one: %s" % _tk.got,
      _ptk.take_in_finished(_tk, _FakeQB()) == 1
      and _tk.got == [os.path.join("D:\\Download", "Show.S29E03", "show.s29e03.mkv")]
      and _ptk.STATE["taken"]["whole"].get("in") and not _ptk.STATE["taken"]["half"].get("in"))
check("and only once", _ptk.take_in_finished(_tk, _FakeQB()) == 0 and len(_tk.got) == 1)
_ptk.STATE["taken"], _ptk.STATE["root"], _ptk.STATE["rows"] = _was_taken, _was_root, _was_rows
_ptsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
check("the download worker asks every round", '_step("_tracker_landed", _tracker_landed)' in _ptsrc
      and '_step("_boxsets", _boxsets)' in _ptsrc)

# ---- a pick remembered without the language word is the file listed with it
check("a fetched pick matches its file's listed name",
      _pla.same_release("The.Bureau.US.S03E21.Womens.Outing.720p.BluRay.DDP5.1.x264-BTN",
                        "English The.Bureau.US.S03E21.Womens.Outing.720p.BluRay.DDP5.1.x264-BTN"))
_lsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the list and the opening choice both compare that way",
      '"picked": same_release(chosen, side["name"]),' in _lsrc
      and "same_release(chosen, pair[1][\"name\"])," in _lsrc and "== chosen" not in _lsrc)

# ---- a search that finds nothing here: the films the catalogue knows by the words
import pd_streaming as _pst
import tempfile as _tfs
class _FakeLib:
    def tmdb(self, path, timeout=20, **params):
        assert path == "/search/movie" and params["query"] == "save the last"
        return {"results": [
            {"id": 9, "title": "Save the Last Dance", "release_date": "2001-01-12",
             "poster_path": "/p.jpg", "vote_count": 900, "genre_ids": [18]},
            {"id": 10, "title": "Save the Last Dance 3", "release_date": "2999-01-01",
             "poster_path": "/q.jpg", "vote_count": 900},
            {"id": 11, "title": "Save the Last Word", "release_date": "2010-01-01",
             "poster_path": None, "vote_count": 900},
            {"id": 12, "title": "Save the Lost", "release_date": "2012-01-01",
             "poster_path": "/r.jpg", "vote_count": 3}]}
    def db(self):
        raise RuntimeError("no library in this test")
_old_root = _pst.STATE["root"]
_pst.STATE["root"] = _tfs.mkdtemp(prefix="pd-rules-searched-")
_pst.SEARCHED["rows"] = None
_pst.FOUND_BY_NAME.clear()
_fn = _pst.by_name(_FakeLib(), "Save  the Last")
check("out already, with a poster, heard of: %s" % [r["title"] for r in _fn],
      [r["title"] for r in _fn] == ["Save the Last Dance"] and _fn[0]["year"] == 2001)
check("its key opens it after a restart", (_pst.SEARCHED.update(rows=None) or True)
      and (_pst.one_of(_fn[0]["key"]) or {}).get("tmdb") == 9
      and _pst.item(_pst.one_of(_fn[0]["key"]))["askable"])
check("asked once an hour, not per keystroke", _pst.by_name(object(), "save the last") == _fn)
check("two letters are not a search", _pst.by_name(_FakeLib(), "sa") == [])
_pst.STATE["root"] = _old_root
_pst.SEARCHED["rows"] = None
_pla_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("search asks the catalogue behind it, and says the tracker is being asked",
      "found = pd_streaming.by_name_soon(self.lib, spelt)" in _pla_src
      and "asking = (pd_streaming.ask_tracker_about(named_rows, self.lib, mine=True)" in _pla_src
      and "target=work if mine else pd_tracker.quietly(work)" in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "pd_streaming.py"), encoding="utf-8").read()
      and "or pd_boxsets.pending())" in _pla_src
      and '"askingTracker": asking,' in _pla_src)
_ajs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("the web asks once more, the app too",
      "if (SCREEN && SCREEN.kind === \"search\" && SCREEN.q === q) viewSearch(q, again + 1);" in _ajs
      and "if (asking && again < SEARCH_AGAIN_TIMES) {" in _ajs)

# ---- a long list's Checking films are all asked of the tracker, slowly, one run at a time
import pd_tracker as _pt114
_asked114, _was114 = [], (_pt114.state, _pt114.asked_within, _pt114.ask_for, st.fetchable)
_pt114.state = lambda: {"asks": True}
_pt114.asked_within = lambda t, y=0, s=0: t == "Seen today"
_pt114.ask_for = lambda t, y=0, again_after=0: _asked114.append(t) or []
st.fetchable = lambda r: r.get("title") == "On a pack"
try:
    _rows114 = [{"title": "Film %d" % n, "year": 2026} for n in range(30)] + \
               [{"title": "Seen today"}, {"title": "On a pack"}]
    _go114 = st.ask_tracker_slowly(_rows114, gap=0.0)
    _again114 = st.ask_tracker_slowly(_rows114, gap=0.0) if st.ASKING_SLOWLY["busy"] else True
    for _n114 in range(100):
        if not st.ASKING_SLOWLY["busy"]:
            break
        time.sleep(0.05)
    check("the films' Popular page asks the tracker about every Checking film, not twelve",
          _go114 and _again114 and len(_asked114) == 30
          and "Seen today" not in _asked114 and "On a pack" not in _asked114
          and st.SLOW_GAP >= 6.0)
finally:
    _pt114.state, _pt114.asked_within, _pt114.ask_for, st.fetchable = _was114
_la114 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the films' Popular page hands the rest to the slow asking",
      "pd_streaming.ask_tracker_slowly(rows[12:])" in _la114)

# ---- films only to ask for are hidden until a viewer turns Request on, on server and clients
_here117 = os.path.dirname(os.path.abspath(__file__))
_fs117 = open(os.path.join(_here117, "pd_localapi.py"), encoding="utf-8").read()
_fs117s = open(os.path.join(_here117, "pd-server.py"), encoding="utf-8").read()
_fs117k = open(os.path.join(_here117, "android", "app", "src", "main", "java", "se", "palladium",
                            "tv", "Api.kt"), encoding="utf-8").read()
_fs117w = open(os.path.join(_here117, "static", "settings.js"), encoding="utf-8").read()
check("Request is off by default for films, everywhere the default is written",
      'FILMS_SHOW = {"disk": True, "download": True, "request": False}' in _fs117
      and 'FILMS_SHOW = {"disk": True, "download": True, "request": False}' in _fs117s
      and 'said?.optBoolean("request", false) ?: false' in _fs117k
      and "const fallback = { disk: true, download: true, request: false };" in _fs117w)

# ---- a held title in a service's list wears the score the list is sorted by
_la118 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("a held title in a service list carries the catalogue's rating, so Rating stays in order",
      'if r.get("vote_average"):\n                            got["rating"] = float(r["vote_average"])' in _la118)

# ---- the app's series page: no tracker answer is not "no packs", and a pack error is said
_kt120 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_api120 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                            "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
check("the app waits a minute for the tracker, and a silence offers Look again, not Add",
      ".put(\"look\", true), readMs = 60_000))" in _api120 and "}.getOrNull()" in _api120
      and "lookFailed = found == null" in _kt120 and 'Pill("Look again", filled = true)' in _kt120)
check("the app says when the pack failed rather than that it is on its way",
      'error.isNotEmpty() -> "In the library, but the pack: $error"' in _kt120)

# ---- a held series' packs can be added, chosen and dropped from its page in the app
_kt121 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_api121 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                            "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
_dp121 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "DownloadPicker.kt"), encoding="utf-8").read()
check("a series' packs are its versions: the Version picker drops one in use, its tracker list links one",
      "ShowPacksDialog" not in _kt121
      and 'if (versions && full.type == "show") {' in _kt121
      and "Api.usePack(full.title, hash = hash, drop = true)" in _kt121
      and "onTake != null -> onTake(one)" in _dp121
      and 'post("/shows/usepack", JSONObject().put("show", title).put("id", id)' in _api121)

# ---- Speak: the microphone mark, and Speech Recognition by name before any saved default
_ui122 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "Ui.kt"), encoding="utf-8").read()
check("the app's voice search is the mic icon and asks Speech Recognition by name first",
      'Pill("Search by voice", filled = true, small = small, icon = MicIcon)' in _ui122
      and 'const val SPEECH_PACKAGE = "com.google.android.tts"' in _ui122
      and "if (said.resolveActivity(ctx.packageManager) == null) said.setPackage(null)" in _ui122)

# ---- Google can hand Palladium a search or a play
_mf123 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "AndroidManifest.xml"), encoding="utf-8").read()
check("the app takes Google's search and play-from-search, with a searchable",
      '<action android:name="android.intent.action.SEARCH" />' in _mf123
      and '<action android:name="com.google.android.gms.actions.SEARCH_ACTION" />' in _mf123
      and '<action android:name="android.media.action.MEDIA_PLAY_FROM_SEARCH" />' in _mf123
      and 'android:resource="@xml/searchable"' in _mf123
      and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src",
                                      "main", "res", "xml", "searchable.xml")))

# ---- a series' own bitrate window: 2-8 lowers the floor for episodes, films keep theirs
import pd_release as _pr124
_s124 = _pr124.for_series({"seriesRate": "2-8", "preferRate265": "4-8", "preferRate264": "5-8"})
check("Series bitrate 2-8 holds episodes to 2-8 Mbit/s in both encodings, and is offered",
      _s124["preferRate265"] == "2-8" and _s124["preferRate264"] == "2-8"
      and _pr124.rate_band("2-8") == (2.0, 8.0)
      and '["2-8", "2-8"]' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static",
                                                 "settings.js"), encoding="utf-8").read())

# ---- a film opening on the television: the remote on the time bar, not on pause
_pa126 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("the time bar takes the remote again once the film is ready to play",
      "if (state == Player.STATE_READY && onTelevision() &&\n                    view?.isControllerFullyVisible == true) {\n                    view?.post { barTakesTheRemote() }" in _pa126)

# ---- a phone: two taps left or right of the picture move it as the arrows do
check("double tap on the left third goes back, the right third forward, as the arrow buttons",
      "e.x < third -> p.seekBack()" in _pa126 and "e.x > v.width - third -> p.seekForward()" in _pa126
      and ".setSeekForwardIncrementMs(15_000)" in _pa126 and ".setSeekBackIncrementMs(10_000)" in _pa126)

# ---- a cast that could not be asked for is asked again, on the server and in the app
_la127 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("no answer from the catalogue keeps no empty cast, and Show more asks again",
      'if "cast" not in said:' in _la127 and "if (had == 0 && !full.isFolder) {" in _kt120)

# ---- Show more: the cast under the plot; what it had goes to the log
check("Show more puts the cast under the plot; a title's page keeps what it loaded",
      _kt120.index("Text(full.summary, color = Skin.Fg.copy(alpha = 0.82f), fontSize = 14.sp,") <
      _kt120.index("Box(Modifier.padding(bottom = 14.dp)) { studios() }")
      and _kt120.count("mutableStateOf(m, androidx.compose.runtime.referentialEqualityPolicy())") == 2)

# ---- one rule for the subtitle a title opens with, on the server
_ss = _pla.start_subtitle
_t = lambda i, lang, **k: dict({"index": i, "codec": "srt" if i < 0 else "subrip", "language": lang}, **k)
check("a file beside in the first language before the film's own",
      _ss([_t(2, "eng"), _t(-1, "eng")], "en", "") == -1)
check("the film's plain track before forced and SDH",
      _ss([_t(1, "eng", forced=True), _t(2, "eng", sdh=True), _t(3, "eng")], "en", "") == 3)
check("a Swedish pick gives way to English once English is here",
      _ss([_t(-1, "swe", picked=True), _t(-2, "eng")], "en", "") == -2)
check("the pick stands when it is in the first language",
      _ss([_t(-1, "eng"), _t(-2, "eng", picked=True)], "en", "") == -2)
check("the second language when the first is not here",
      _ss([_t(1, "ger"), _t(2, "swe")], "en", "sv") == 2)
check("second set to off: nothing in the first language is none",
      _ss([_t(1, "ger")], "en", "off") is None)
check("first set to off: none, unless picked",
      _ss([_t(-1, "eng")], "off", "") is None and _ss([_t(-1, "eng", picked=True)], "off", "") == -1)
check("pictures are never opened with", _ss([{"index": 4, "codec": "hdmv_pgs_subtitle", "language": "eng"}], "en", "") is None)
check("unless picked: a DVD's picture track chosen once opens the next play",
      _ss([{"index": 2, "codec": "dvd_subtitle", "language": "eng", "picked": True}], "en", "") == 2
      and _ss([{"index": 2, "codec": "dvd_subtitle", "language": "eng", "picked": True},
               _t(-1, "eng")], "en", "") == 2)
check("an index 0 track is an answer, not none", _ss([_t(0, "eng")], "en", "") == 0)
_la_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("every title's details carry the server's choice", _la_src.count("sub_langs=self.sub_languages())") == 2
      and '"subsDecided": sub_langs is not None' in _la_src)
_appjs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("the web follows it", "if (md && md.subsDecided) return list.filter((t) => t.start)[0] || null;" in _appjs)
_mkt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java", "se",
                         "palladium", "tv", "Media.kt"), encoding="utf-8").read()
check("and so does the app", "Route.serverSub(subtitleStreams)?.let { return it.index }" in _mkt)
check("the server fetches the next episode's subtitle two minutes in",
      'if watched_key.startswith("e") and at >= self.AHEAD_AT_S:\n                    self.subtitle_ahead(watched_key)' in _srvsrc
      and "self.auto_subtitle(nxt, str(key))" in _srvsrc)

# ---- an episode title with an apostrophe matches a release name without one
_wfn = next(n for c in _ast.parse(_srvsrc).body if isinstance(c, _ast.ClassDef)
            for n in c.body if isinstance(n, _ast.FunctionDef) and n.name == "_words")
_wfn.decorator_list = []
_wns = {"re": _re}
exec(compile(_ast.Module(body=[_wfn], type_ignores=[]), "pd-server.py", "exec"), _wns)
_ww = _wns["_words"]
check("Women's and Womens are one word: %s" % sorted(_ww("Women's Outing") & _ww("The.Bureau.US.S03E21.Womens.Outing.720p.BluRay-BTN")),
      _ww("Women's Outing") & _ww("The.Bureau.US.S03E21.Womens.Outing.720p.BluRay-BTN") == {"womens", "outing"}
      and _ww("Women\u2019s Day") == {"womens", "day"})

# ---- two releases of one episode are two files beside it
import pd_subs as _psb
_v = r"D:\x\The.Bureau.US.S03E20.1080p.BluRay.x265-RBG.mp4"
_a = _psb.sidecar_path(_v, "en", "The.Bureau.US.S03E20.Stock.Take.720p.BluRay.DDP5.1.x264-BTN")
_b = _psb.sidecar_path(_v, "en", "The Bureau (US) - S03E20 - Stock Take")
check("releases sharing their first 34 characters get their own files: %s" % [os.path.basename(_a), os.path.basename(_b)],
      _a != _b and _a.endswith(".en.srt") and _b.endswith(".en.srt")
      and _a == _psb.sidecar_path(_v, "en", "The.Bureau.US.S03E20.Stock.Take.720p.BluRay.DDP5.1.x264-BTN")
      and len(_a) <= 250)

# ---- a group verified in a series counts only for videos of its source
_fit = _pla.family_fits_video
check("Blu-ray group on a Blu-ray file; not on the next season's WEB-DL",
      _fit(("bluray", "btn"), r"D:\x\Show.S03E20.1080p.BluRay.x265-RBG.mp4")
      and not _fit(("bluray", "btn"), r"D:\x\Show.S04E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX.mkv")
      and _fit(("", "btn"), "Show.S04E01.WEB-DL.mkv") and _fit(("bluray", "btn"), "Show.S04E01.mkv"))
_fam = _srvsrc[_srvsrc.index("    def fitting_families"):_srvsrc.index("    def rank_subtitles")]
check("the series' groups are counted through it", "pd_localapi.family_fits_video(family, video)" in _fam)
_ver = _srvsrc[_srvsrc.index("    def verify_subtitle"):_srvsrc.index("    def remove_sidecar")]
check("a verified episode fetches ahead for the next two, not from the idle check",
      "if name and show and how != \"speech\":\n            self.fetch_ahead_of(key)" in _ver
      and "AHEAD_ON_VERIFY = 2" in _ver and "self.auto_subtitle(at)" in _ver)

# ---- a screen on the cache comes home: guests too
_home = _srvsrc[_srvsrc.index("    def _get_part_01"):_srvsrc.index("    def _get_part_01") + 1500]
check("an app's /server check is answered for any key, before the guest guard",
      'path == "/server" and (self.headers.get("X-Palladium-App") or "").startswith("android ")' in _home
      and 'self.who("/where") is not None' in _home)
_apikt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
_chk = _apikt[_apikt.index("fun comeHomeAt("):_apikt.index("fun comeHomeIfUp(")]
check("the app asks /where to come home", '"/where", token, 3000, 3000' in _chk and '"/server"' not in _chk)

# ---- the file being watched is measured next, not turned away by the backlog
_m = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_mi = _m.index("def measure_loudness(")
_mb = _m[_mi:_mi + 4000]
check("a busy slot keeps the playing file for next", "MEASURE_NEXT[str(part)] = (path, size)" in _mb
      and _mb.index("if playing and str(part) not in MEASURING") < _mb.index("MEASURE_NEXT[str(part)]"))
check("and the measurement that ends starts it", "waiting = MEASURE_NEXT.popitem()" in _mb
      and "measure_loudness(waiting[0], waiting[1][0], waiting[1][1], playing=True)" in _mb)

# ---- loudness: what is about to be watched is measured first
import pd_receiver as _pr
_nexts = {"e1": "e2", "e2": "e3", "e3": "e4", "e4": "e5", "ex1": "ex2", "ex2": "ex3", "ex3": "ex4"}
_fin = lambda p, d: bool(d) and p >= d * 0.9
_soon = _pr.soon_keys([("e2", 100, 1300), ("ex1", 1290, 1300), ("m1", 500, 6000), ("m2", 5990, 6000)],
                      _nexts.get, _fin)
check("left part-way, then the next ones; a finished episode's next three; a film left part-way: %s" % _soon,
      _soon == ["e2", "e3", "e4", "ex2", "ex3", "ex4", "m1"])
_known = {"1": {"lufs": -23, "how": "spread"}}
_rows = [(1, "D:/a.mkv", 10), (2, "D:/b.mkv", 10), (3, "E:/c.mkv", 10)]
check("the first about-to-be-watched file not yet measured", _pr.first_soon(_rows, _known, {}) == (2, "D:/b.mkv", 10))
check("skipping the drive somebody is watching from", _pr.first_soon(_rows, _known, {}, {"D:"}) == (3, "E:/c.mkv", 10))
check("all measured: the backlog's turn", _pr.first_soon(_rows[:1], _known, {}) is None)
_srvm = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_bi = _srvm.index("def measure_the_backlog")
check("the backlog asks for them before its own list",
      _srvm.index("pd_receiver.first_soon(soon_to_be_watched()", _bi) < _srvm.index("while left:", _bi))

# ---- a site rip as one series: a folder a season, its files in order
import pd_torrents as _pt
_files = [{"index": 0, "path": "13 - Talk Shows/Low Strike/Low Strike - Season 1 - Aces of Iron.mkv", "size": 9},
          {"index": 1, "path": "5 - Animations/Anvil and Bolt/Anvil and Bolt - Season 1 - 10 - Rogue.mkv", "size": 9},
          {"index": 2, "path": "5 - Animations/Anvil and Bolt/Anvil and Bolt - Season 1 - 2 - Bound for Greatness.mkv", "size": 9},
          {"index": 3, "path": "5 - Animations/Aces of Iron/Aces of Iron - Season 1 - 1 - Blood and Duty.mkv", "size": 9},
          {"index": 4, "path": "6 - Shows/Lorekeepers/Age of Stone/Lorekeepers - Age of Stone - Archaon.mkv", "size": 9},
          {"index": 5, "path": "5 - Animations/Aces of Iron/note.txt", "size": 1}]
_lay = _pt.series_layout(_files, "Hobby TV")
_by = {e["index"]: e for e in _lay}
check("every video, and nothing else: %s" % sorted(_by), sorted(_by) == [0, 1, 2, 3, 4])
check("folders in the pack's own order, numbered first: %s" % [(_by[i]["season"], _by[i]["episode"]) for i in sorted(_by)],
      _by[3]["season"] == 1 and _by[2]["season"] == 2 and _by[1]["season"] == 2
      and _by[4]["season"] == 3 and _by[0]["season"] == 4)
check("episodes in name order, 2 before 10", _by[2]["episode"] == 1 and _by[1]["episode"] == 2)
check("the episode's name without what the folder says: %s" % [_by[i]["episodeName"] for i in sorted(_by)],
      _by[3]["episodeName"] == "Aces of Iron: Blood and Duty"
      and _by[4]["episodeName"] == "Lorekeepers - Age of Stone: Archaon"
      and _by[0]["episodeName"] == "Low Strike: Aces of Iron")
check("one series, never looked up", all(e["title"] == "Hobby TV" and e["kind"] == "episode"
                                         and e["tmdb"] == 0 for e in _lay))
_more = _pt.series_layout([
    {"index": 0, "path": "5 - Animations/Astartes/Astartes - 1 - Astartes.mkv", "size": 9},
    {"index": 1, "path": "16 - Scores/Anvil & Bolt Original Score/Anvil & Bolt Original Score - Season 1 - "
                         "Anvil And Bolt Original Score – Artefacts.mkv", "size": 9}], "Hobby TV")
check("a name the folder says in full keeps its last word; & reads as and, an en dash splits: %s"
      % [e["episodeName"] for e in _more],
      [e["episodeName"] for e in _more] == ["Astartes: Astartes", "Anvil & Bolt Original Score: Artefacts"])
_packs = [{"films": _lay}, {"films": [{"path": "Other/Show.S01E01.mkv", "title": "Show", "season": 1, "episode": 1}]}]
check("a downloaded file is filed by the pack: series, season, number and name",
      _pt.find_told(_packs, "D:\\Download\\Hobby.TV.SiteRip\\5 - Animations\\Aces of Iron\\"
                            "Aces of Iron - Season 1 - 1 - Blood and Duty.mkv")
      == ("Hobby TV", 1, 1, "Aces of Iron: Blood and Duty"))
check("a file from an ordinary pack, or a stranger, is left to its name",
      _pt.find_told(_packs, "D:/Download/Other/Show.S01E01.mkv") is None
      and _pt.find_told(_packs, "D:/x/Blood and Duty.mkv") is None)
_libsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_library.py"), encoding="utf-8").read()
check("the library asks the pack before reading the name",
      _libsrc.index("told = hook(path) if hook else None") < _libsrc.index("parse_episode(path)", _libsrc.index("def _index_file")))

# ---- a series coming from the tracker is named as one, and is not a film
import pd_tracker as _ptk
_et = _ptk.episode_tag
check("an episode, a season, and a film: %s" % [_et("TYSON S01E01 1080p WEB H264-THEGAME"),
                                                 _et("The.Show.2024.S02.1080p.WEB-DL"), _et("Colony 2026 1080p WEB")],
      _et("TYSON S01E01 1080p WEB H264-THEGAME") == ("TYSON", 1, 1)
      and _et("The.Show.2024.S02.1080p.WEB-DL") == ("The Show", 2, 0)
      and _et("Its Raining in Rotterdam S18E10 1080p") == ("Its Raining in Rotterdam", 18, 10)
      and _et("Colony 2026 1080p AMZN WEB-DL") is None and _et("Se7en 1995 1080p") is None)
_srvt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ti = _srvt.index("def tracker_coming")
check("not matched to a film of the same name, and typed as what it is",
      _srvt.index('raise LookupError("an episode is not one of the new films")', _ti) <
      _srvt.index("pd_streaming.read()", _ti)
      and '"type": extra.pop("type", "movie")' in _srvt[_ti:_ti + 5000])
_las = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the films shelf takes films only", 'if card.get("type") not in (None, "movie"):' in _las)

# ---- a season nothing here holds says what the tracker has of it
_sw = _pla.season_words
check("episodes on the tracker before the air date's words",
      _sw(1, "Out, not here yet") == "1 episode on the tracker"
      and _sw(3, "Airs 18 Oct") == "3 episodes on the tracker"
      and _sw(0, "Out, not here yet") == "Out, not here yet")
_lsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("and the season card asks the tracker, and can be fetched from",
      '"airs": season_words(onit, self._air_words(one.get("air_date"))),' in _lsrc
      and '"fetchable": onit > 0,' in _lsrc)

# ---- Swedish series: by their Swedish names, and in plain letters at the tracker
import pd_tracker as _ptr2
check("å, ä and ö read as a, a and o", _ptr2.plain("Över Bergen") == "Over Bergen"
      and _ptr2._flat("Gränsbevakarna Sverige") == "gransbevakarnasverige"
      and _ptr2._flat("Bonde söker fru") == _ptr2._flat("Bonde Soker Fru"))
_srvsw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns = {}
exec(_srvsw[_srvsw.index("def show_name(r):"):_srvsw.index("def show_rows():")], _ns)
check("a Swedish series by its Swedish name, the rest by the catalogue's",
      _ns["show_name"]({"name": "The Land of the Cyclops", "original_name": "Cyklopernas land",
                        "original_language": "sv"}) == "Cyklopernas land"
      and _ns["show_name"]({"name": "Squid Game", "original_name": "x", "original_language": "ko"}) == "Squid Game")
_ns2 = {}
exec(_srvsw[_srvsw.index("def mix_in("):_srvsw.index("def show_name(r):")], _ns2)
_mix = _ns2["mix_in"](["w1", "w2", "w3", "w4", "w5"], ["s1", "s2", "s3"], 3)
check("one Swedish series in every three places, each list in its order: %s" % _mix,
      _mix == ["w1", "w2", "s1", "w3", "w4", "s2", "w5", "s3"])
check("either list alone stands as it was", _ns2["mix_in"](["a", "b"], [], 3) == ["a", "b"]
      and _ns2["mix_in"]([], ["x", "y"], 3) == ["x", "y"])
check("the popular list asks for Swedish series airing now",
      '"with_origin_country": "SE"' in _srvsw and '"air_date.gte": since' in _srvsw)
_libsw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_library.py"), encoding="utf-8").read()
check("and the library names one so when it is matched",
      'detail.get("original_language") == "sv"' in _libsw)

# ---- the Sports row: races and games read from the tracker's names
import pd_sports as _spt
_rn = _spt.read_name
_race = _rn("Formula1 2026 Round17 Singapore Race F1TV WEB-DL 1080p H264 English-MWR")
check("an F1 race, its round and its place", _race and _race["sport"] == "f1" and _race["round"] == 17
      and _race["title"] == "Singapore Grand Prix")
check("the same race named another way is the same event",
      _rn("Formula1 2026 Singapore Grand Prix 1080p WEB h264-BILLIE")["event"] == _race["event"])
check("a session named after the Grand Prix is not the race",
      _rn("Formula1 2026 Bahrain Grand Prix Practice Two 1080p WEB h264-BILLIE") is None
      and _rn("Formula1 2026 Spanish Grand Prix Weekend Warm-Up 1080p WEB h264-BILLIE") is None
      and _rn("Formula1 2026 Bahrain Grand Prix 1080p WEB h264-BILLIE")["title"] == "Bahrain Grand Prix")
check("qualifying, practice and the shows are not on the row",
      _rn("Formula1 2026 Round17 Singapore Qualifying F1TV WEB-DL 1080p") is None
      and _rn("Formula1 2026 Round17 Singapore Post-Race Show F1TV WEB-DL 1080p") is None
      and _rn("Formula1 2026 Round17 Singapore FP1 F1TV WEB-DL 1080p") is None)
_game = _rn("NFL 2026 10 12 Buffalo Bills Vs Los Angeles Rams 1080p WEB H264-NiGHTN")
check("an NFL game, its teams and its day", _game and _game["title"] == "Buffalo Bills vs Los Angeles Rams"
      and _game["when"] == "2026-10-12")
check("a film is not sport", _rn("Colony 2026 1080p AMZN WEB-DL") is None
      and _rn("Its Raining in Rotterdam S18E10 1080p") is None)
_now = 1_800_000_000
_rows = ([{"name": "NFL 2026 10 %02d Team%d Bills Vs Team%d Rams 1080p WEB H264-X" % (1 + i % 9, i, i),
           "at": _now - 3600 * i, "seeds": 5} for i in range(20)] +
         [{"name": "Formula1 2026 Round%d Place%d Race F1TV WEB-DL 1080p" % (i, i), "at": _now - 7200 * i}
          for i in range(1, 4)] +
         [{"name": "Formula1 2026 Round1 Place1 Race F1TV WEB-DL 2160p HLG", "at": _now, "seeds": 9},
          {"name": "Formula1 2026 Round9 Old Race F1TV WEB-DL 1080p", "at": _now - 40 * 86400}])
_ev = _spt.events(_rows, now=_now)
check("one card a race whatever its versions, sixteen games at most, a month back",
      sum(1 for e in _ev if e["sport"] == "nfl") == 16 and sum(1 for e in _ev if e["sport"] == "f1") == 3
      and len(next(e for e in _ev if e["title"] == "Place1 Grand Prix")["releases"]) == 2)
_card = _spt.card(_ev[0])
check("a card to fetch from: its page lists the versions", _card["askable"] and _card["onTracker"]
      and _card["ratingKey"].startswith("sp"))
check("a downloaded race is Formula 1, season the year, episode the round",
      _spt.episode_of("Formula1 2026 Round17 Singapore Race F1TV WEB-DL 1080p", []) ==
      ("Formula 1", 2026, 17, "Singapore Grand Prix"))
_g1 = _spt.episode_of("NFL 2026 10 12 Buffalo Bills Vs Los Angeles Rams 1080p WEB H264-NiGHTN", [])
_g2 = _spt.episode_of("NFL 2026 10 12 Chicago Bears Vs Green Bay Packers 720p WEB H264-NiGHT", [])
check("a game is NFL, numbered by its day and home team, the same every time: %s %s" % (_g1, _g2),
      _g1[0] == "NFL" and _g1[1] == 2026 and _g1[2] != _g2[2] and _g1[2] // 100 == 1012
      and _spt.episode_of("NFL 2026 10 12 Buffalo Bills Vs Los Angeles Rams 720p WEB H264-Y", []) == _g1)
_srvsp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the versions list reads an event's own releases, and a download is filed under its sport",
      "pd_sports.releases_of(str(body[\"key\"])" in _srvsp and "_pt.told(path) or _sp.told(path)" in _srvsp)

# ---- a sport card has a poster, drawn here
import io as _io
from PIL import Image as _Img
_pics = [_spt.poster(e, 300) for e in (_spt.read_name("Formula1 2026 Round17 Singapore Race F1TV WEB-DL 1080p") | {"round": 17},
                                     _spt.read_name("NFL 2026 10 12 Buffalo Bills Vs Los Angeles Rams 1080p WEB H264-X"),
                                     {"sport": "golf", "title": "PGA Tour 2026 Round 4", "year": 2026})]
check("a poster for a race, a game and the rest, 2:3 and JPEG",
      all(p[:2] == b"\xff\xd8" and _Img.open(_io.BytesIO(p)).size == (300, 450) for p in _pics))
check("and the card points at it", _spt.card(dict(_ev[0]))["thumb"] == "/art/%s/poster" % _ev[0]["key"])

# ---- the TV tab by country: kept with the library, counted and narrowed
import pd_library as _plib
check("where a series and a film were made", _plib.made_in({"origin_country": ["SE", "DK"]}) == "DK,SE"
      and _plib.made_in({"production_countries": [{"iso_3166_1": "us"}]}) == "US" and _plib.made_in({}) == "")
check("the countries marked", _pla.made_in_any("se, dk,") == {"SE", "DK"} and _pla.made_in_any("") == set())
_lc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the shelf is narrowed by them and the menu counts them",
      'lands = made_in_any(one("country", ""))' in _lc and 'if path == "/library/countries":' in _lc)

# ---- a series made of a pack: seasons by their folders' names, posters drawn
_wh = [{"films": _pt.series_layout([
    {"index": 0, "path": "5 - Animations/Aces of Iron/Aces of Iron - Season 1 - 1 - Blood and Duty.mkv", "size": 9},
    {"index": 1, "path": "6 - Shows/Lorekeepers/Age of Stone/Lorekeepers - Age of Stone - Archaon.mkv", "size": 9}],
    "Hobby TV")}]
check("each season by its folder's name: %s" % _pt.series_seasons(_wh, "Hobby TV"),
      _pt.series_seasons(_wh, "Hobby TV") == {1: "Aces of Iron", 2: "Lorekeepers - Age of Stone"})
_old = {"series": True, "episodeName": "Aces of Iron: Blood and Duty"}
check("a pack added before the name was kept: from the episode's name",
      _pt.season_name(_old) == "Aces of Iron" and _pt.season_name({"episodeName": "x: y"}) == "")
check("nothing for an ordinary series", _pt.series_seasons([{"films": [{"title": "Show", "season": 1}]}], "Show") == {})
check("where its posters are", _pt.series_art("Hobby TV") == "/art/series/hobbytv/poster"
      and _pt.series_art("Hobby TV", 3) == "/art/series/hobbytv/s3")
check("a poster for the series and for a season, 2:3 JPEG",
      all(p[:2] == _spt.poster(_ev[0], 300)[:2] and _Img.open(_io.BytesIO(p)).size == (300, 450)
          for p in (_spt.series_poster("Hobby TV", "", 0, 300),
                    _spt.series_poster("Hobby TV", "Lorekeepers - Age of Stone", 2, 300))))

# ---- the Sports row: one card a sport, its events newest first by when they were run
_rows2 = [{"name": "Formula1 2026 Round17 Singapore Race F1TV WEB-DL 1080p", "at": _now - 86400},
          {"name": "Formula1 2026 Round16 Baku Race F1TV WEB-DL 1080p", "at": _now - 600},
          {"name": "NFL 2026 10 05 Buffalo Bills Vs Los Angeles Rams 1080p WEB H264-X", "at": _now - 50},
          {"name": "NFL 2026 10 12 Chicago Bears Vs Green Bay Packers 1080p WEB H264-X", "at": _now - 9000}]
_cards = _spt.sport_cards(_rows2, now=_now)
check("one card a sport, the newest posting first: %s" % [c["title"] for c in _cards],
      [c["sport"] for c in _cards] == ["nfl", "f1"] and all(c["sportRow"] for c in _cards))
check("a sport's events by when they were run, not when they were posted",
      [e["title"] for e in _spt.sport_events(_rows2, "f1", now=_now)] == ["Singapore Grand Prix", "Baku Grand Prix"]
      and [e["when"] for e in _spt.sport_events(_rows2, "nfl", now=_now)] == ["2026-10-12", "2026-10-05"])
check("each sport's own card is drawn", _spt.sport_poster("f1", 300)[:2] == _spt.sport_poster("nfl", 300)[:2])

# ---- Live TV: iptv-org's Swedish channels, the ones on around the clock, sharpest first
import pd_live as _lv
_m3u = """#EXTM3U
#EXTINF:-1 tvg-id="a.se" tvg-logo="http://x/a.png" group-title="News",Alpha TV (720p)
http://s/a.m3u8
#EXTINF:-1 tvg-id="b.se" group-title="Series",Beta (1080p) [Not 24/7]
http://s/b.m3u8
#EXTINF:-1 tvg-id="c.se" group-title="Sports",Gamma (1080p)
http://s/c.m3u8
#EXTINF:-1 tvg-id="d.se",Delta [Geo-blocked]
http://s/d.m3u8
"""
_chs = _lv.parse(_m3u)
check("a playlist read: name, logo, group, stream", len(_chs) == 4 and _chs[0]["logo"] == "http://x/a.png"
      and _chs[0]["group"] == "News" and _chs[0]["url"] == "http://s/a.m3u8" and _chs[0]["key"].startswith("lv"))
check("not 24/7 and geo-blocked left out, the sharper first: %s" % [c["name"] for c in _lv.ranked(_chs)],
      [_lv.clean(c["name"]) for c in _lv.ranked(_chs)] == ["Gamma", "Alpha TV"])
check("each piece keeps the address's token",
      _lv.playlist("#EXTM3U\n#EXTINF:4,\nseg000001.ts", "t=abc") == "#EXTM3U\n#EXTINF:4,\nseg000001.ts?t=abc\n"
      and _lv.playlist("x.ts", "") == "x.ts")
_lvc = _lv.card(_chs[0], 3)
check("a channel's card: live, its poster drawn here", _lvc["live"] and _lvc["thumb"] == "/art/live/" + _chs[0]["key"]
      and _lvc["title"] == "Alpha TV")
check("its poster, a square JPEG for the round card, with or without a logo",
      _Img.open(_io.BytesIO(_lv.poster(_chs[0], None, 300))).size == (300, 300))
_srvlv = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the server copies a channel into pieces and serves them",
      'pd_live.start(key, ffmpeg_now())' in _srvlv and 'pd_live.playlist(open(listed' in _srvlv)

# ---- a drawn poster asked for without a width is drawn at its full width
_lw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("w missing is 400 wide, not 64", 'int(one("w", "0") or 400), 400)' not in _lw
      and _lw.count('int(one("w", "0") or 0) or 400, 400)') >= 4)

# ---- Live TV: the named lineup, in its order, spares after
_every = [{"name": "Himlen TV7 (720p)", "url": "u1"}, {"name": "Di TV (1080p)", "url": "u2"},
          {"name": "Expressen TV (1080p)", "url": "u3"}, {"name": "Golf Channel", "url": "u4"},
          {"name": "Kunskapskanalen HD (1080p) [Geo-blocked]", "url": "u5"}]
check("the lineup in its own order, and nothing outside it: %s" % [c["name"] for c in _lv.lineup(_every)],
      [c["url"] for c in _lv.lineup(_every)] == ["u3", "u2", "u5", "u4"])
check("ten named before the spares, the religious and single-show channels left out",
      len(_lv.LINEUP) > _lv.MOST and not any("Himlen" in n or "MTV" in n for n in _lv.LINEUP))

# ---- the IPTV plugin: channels only, by group and name, and the connections it allows
import tempfile as _tf30
import pd_iptv as _ip30
import pd_plugins as _pp30
_own30 = _tf30.mkdtemp()
_lv.use_data_dir(_own30)
check("a provider's films and episodes are not channels",
      [c["url"] for c in _ip30.live_only([{"url": "http://p/live/u/p/1.ts"}, {"url": "http://p/movie/u/p/2.mkv"},
                                          {"url": "http://p/series/u/p/3.mp4"}, {"url": "http://p/u/p/4"}])]
      == ["http://p/live/u/p/1.ts", "http://p/u/p/4"])
_m3u30 = ('#EXTM3U\n#EXTINF:-1 tvg-logo="" group-title="Sport",Sport One\nhttp://p/1\n'
          '#EXTINF:-1 group-title="News",News One\nhttp://p/2\n'
          '#EXTINF:-1 group-title="Sport",Sport Two\nhttp://p/3\n')
_set30 = {"liveMost": 1}


def _write30(kept):
    with open(os.path.join(_own30, "settings.json"), "w", encoding="utf-8") as f:
        __import__("json").dump(kept, f)


check("a guest cannot set it, and a body not about it is not the plugin's",
      _pp30.settings_post({"liveList": "http://x/p.m3u"}, False, dict(_set30), _write30)[0] == 403
      and _pp30.settings_post({"tvPopular": "always"}, True, dict(_set30), _write30) is None)
_said30 = _pp30.settings_post({"liveListText": _m3u30}, True, dict(_set30), _write30)
check("a playlist sent as a file is read, three channels", _said30 == (200, dict(_said30[1], read=3))
      and _said30[1]["liveCount"] == 3 and _said30[1]["liveList"] == "file")
check("the address and its settings to the owner alone", _pp30.settings_get(False) == {}
      and _pp30.settings_get(True)["liveMost"] == 1)
check("by group, and by words in the name",
      [c["title"] for c in _ip30.own_row("Sport")] == ["Sport One", "Sport Two"]
      and [c["title"] for c in _ip30.own_row("", "two")] == ["Sport Two"]
      and _ip30.own_groups() == [{"title": "Sport", "count": 2}, {"title": "News", "count": 1}])
check("the row and its channels to the owner; a guest only once it is shared",
      [r["id"] for r in _pp30.rows(True)] == ["mylive"] and _pp30.rows(False) == []
      and _pp30.library("/library/live/own", {}, False) == []
      and len(_pp30.library("/library/live/own", {}, True)) == 3
      and _pp30.library("/library/sports", {}, True) is None)
_k30 = [c["key"] for c in _ip30.read_own()["all"]]
_ip30._WATCHED["map"] = {}
_ip30.note_watched(_k30[1], 400, now=1_800_000_000)
_ip30.note_watched(_k30[0], 120, now=1_800_000_000)
check("the row puts first what was watched most in the week, the playlist's order kept for stepping",
      [c["title"] for c in _ip30.own_row()] == ["News One", "Sport One", "Sport Two"]
      and [c["title"] for c in _ip30.own_row(plain=True)] == ["Sport One", "News One", "Sport Two"])
_wk39 = _ip30.week_seconds(now=1_800_000_000)
check("a week's seconds per channel, and a piece older than the week not counted",
      _wk39.get(_k30[1]) == 400 and _wk39.get(_k30[0]) == 120
      and _ip30.week_seconds(now=1_800_000_000 + 8 * 86400).get(_k30[1], 0) == 0)
_ip30._WATCHED["map"] = {}
_ip30.note_played(_k30[2])
check("the channel played last is first on the row, not on a group's page",
      [c["title"] for c in _ip30.own_row()][0] == "Sport Two"
      and [c["title"] for c in _ip30.own_row("Sport")][0] == "Sport One")
_pp30.settings_post({"liveGroups": ["Sport"], "liveScreen": "tv"}, True,
                    dict(_set30, liveList="file", liveGroupsBy={"phone": ["News"]}), _write30)
check("one list of groups on every screen, whichever set it; a screen's own list from before is gone",
      [c["title"] for c in _ip30.own_row(plain=True, screen="tv")] == ["Sport One", "Sport Two"]
      and [c["title"] for c in _ip30.own_row(plain=True, screen="phone")] == ["Sport One", "Sport Two"]
      and [c["title"] for c in _ip30.own_row(plain=True)] == ["Sport One", "Sport Two"]
      and "liveGroupsBy" not in _ip30._settings())
_pp30.settings_post({"liveGroups": []}, True, dict(_set30, liveList="file"), _write30)
# a channel that does not play is left off the lists until it plays again
_ip30._HEALTH["map"] = {}
_tried57 = []
_ip30.try_round("", now=1_000_000.0, tries=lambda src: (_tried57.append(src), "p/2" not in src)[1])
check("each channel of the kept groups tried once; the one that did not play left out",
      len(_tried57) == 3 and [c["title"] for c in _ip30.own_row(plain=True)] == ["Sport One", "Sport Two"]
      and _ip30.dead(_k30[1]))
_tried57.clear()
_ip30.try_round("", now=1_000_000.0 + _ip30.CHECK_EVERY + 1, tries=lambda src: (_tried57.append(src), True)[1])
check("tried again: the playing ones after six hours, the silent one only after twelve",
      len(_tried57) == 2)
_ip30.try_round("", now=1_000_000.0 + _ip30.RETRY_DEAD + 1, tries=lambda src: True)
check("and back on the lists once it plays", len(_ip30.own_row(plain=True)) == 3 and not _ip30.dead(_k30[1]))
_ip30._HEALTH["map"] = {}
_pp30.settings_post({"liveFolders": ["News", "Nowhere"]}, True, dict(_set30, liveList="file"), _write30)
_row40 = _ip30.own_row()
check("a group named first stands as the row's first circle, one with no channels does not",
      _row40[0].get("liveGroup") == "News" and _row40[0]["summary"] == "1 channels"
      and not any(c.get("liveGroup") == "Nowhere" for c in _row40)
      and not any(c.get("liveGroup") for c in _ip30.own_row(plain=True)))
check("its picture is drawn by the plugin", (_pp30.art("/art/livegroup/" + _ip30.group_id("News"), 100) or b"")[:2] == b"\xff\xd8")
_more43 = _pp30.library("/library/live/own", {"more": ["1"]}, True)
check("Show more: a circle per group shown, the first-on-the-row ones first, and any group's picture drawn",
      [c.get("liveGroup") for c in _more43] == ["News", "Sport"]
      and (_pp30.art("/art/livegroup/" + _ip30.group_id("Sport"), 100) or b"")[:2] == b"\xff\xd8"
      and len(_pp30.library("/library/live/own", {"more": ["1"], "words": ["two"]}, True)) == 1)
_pp30.settings_post({"liveGroups": ["News"], "liveFolders": []}, True, dict(_set30, liveList="file"), _write30)
check("only the groups kept, on the row, its page and its groups; the settings list them all",
      [c["title"] for c in _ip30.own_row()] == ["News One"]
      and _ip30.own_groups() == [{"title": "News", "count": 1}]
      and len(_pp30.settings_get(True)["liveAllGroups"]) == 2)
check("a YouTube page is no channel", _ip30.live_only([{"url": "https://www.youtube.com/@x/live"}]) == [])
check("pd_live finds the plugin's channels by key", (_lv.one(_k30[0]) or {}).get("own") is True)


class _Proc30:
    def __init__(self): self.dead = False
    def poll(self): return 0 if self.dead else None
    def kill(self): self.dead = True


_lv.RUNNING.clear()
_lv.RUNNING[_k30[0]] = {"proc": _Proc30(), "dir": "", "seen": 1000.0, "own": True}
check("one connection: a second channel waits while the first is watched, the same one is shared",
      not _ip30.admit(_k30[1], now=1005.0) and _ip30.admit(_k30[0], now=1005.0))
check("and gets it once nobody has asked for the first in a while",
      _ip30.admit(_k30[1], now=1000.0 + _ip30.FREED_AFTER + 1) and _k30[0] not in _lv.RUNNING)
_lv.RUNNING.clear()
_lv.use_data_dir("")
_ssrc30 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("a channel that did not start is stopped, one that did is noted",
      "pd_live.stop(key)\n                        self.send_error(504" in _ssrc30.replace("\r\n", "\n")
      and "pd_live.played(key)" in _ssrc30)
check("the server asks the plugins, and pd_live, before a channel starts",
      "got = pd_plugins.settings_post(body, self.role == \"owner\", self.settings_file()," in _ssrc30
      and "if not pd_live.room_for(key):" in _ssrc30 and "pd_iptv" not in _ssrc30)

# ---- a live channel plays its sharpest picture, and its name without a list's marks
_said31 = """  Stream #0:0: Video: h264 (Main), yuv420p, 448x252, 25 fps
  Stream #0:3: Video: h264 (High), yuv420p(tv, bt709), 1280x720 [SAR 1:1 DAR 16:9], 50 fps
  Stream #0:4: Video: h264 (High), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 50 fps
  Stream #0:5(swe): Audio: aac (LC), 48000 Hz, stereo
"""
check("the 1080p stream of five, none chosen when there is one picture",
      _lv.sharpest(_said31) == 4 and _lv.sharpest("  Stream #0:0: Video: h264, 1280x720, 25 fps") is None)
check("circled marks off a name", _lv.clean("SVT 1 \u24bc") == "SVT 1" and _lv.clean("ATG Live (720p)") == "ATG Live")

# ---- a key from outside the house opens a live channel at the app's address too
from pd_invites import Invites as _Inv32
check("the page's live player and the IPTV plugin load for a key from outside",
      _Inv32.allowed("/hls.min.js") and _Inv32.allowed("/iptv.js"))
check("the app's /live/ and the web's /local/live/ are both open to a key",
      _Inv32.allowed("/live/lv0123456789ab/index.m3u8") and _Inv32.allowed("/local/live/lv0123456789ab/seg000001.ts"))

# ---- a fresh channel is answered with a cushion, not at its first piece
check("eight seconds of picture, or two seconds after the first piece; never with none",
      _lv.cushioned(8.4, 0.5) and not _lv.cushioned(4.0, 1.0) and _lv.cushioned(2.0, 2.5)
      and not _lv.cushioned(0, 30.0)
      and _lv.seconds_in("#EXTINF:6.4,\nseg1.ts\n#EXTINF:3.2,\nseg2.ts") == 9.6)
check("the playlist answer waits on it",
      "if pd_live.cushioned(pd_live.seconds_in(text)," in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read())
_calls53 = []
_was53 = _lv._probe
_lv._probe = lambda src, ff: (_calls53.append(src), (7, 50.0))[1]
_lv.PROBED.clear()
check("a channel's probe is asked once and remembered",
      _lv.probe_kept("u", "ff", now=1000.0) == (7, 50.0) and _lv.probe_kept("u", "ff", now=1100.0) == (7, 50.0)
      and _calls53 == ["u"] and _lv.probe_kept("u", "ff", now=1000.0 + _lv.PROBE_KEEP + 1) == (7, 50.0)
      and len(_calls53) == 2)
_lv._probe = _was53
_lv.PROBED.clear()

# ---- a channel starts half a minute back, so a player has a cushion; its card is square
check("an HLS source is begun ten pieces back, any other as it comes",
      _lv.head_start("https://x/l4/se/svt1/master-fmp4.m3u8?format=hls") == ["-live_start_index", "-10"]
      and _lv.head_start("https://x/stream.mpd") == [] and _lv.LIST_PIECES >= 12)
_im40 = __import__("PIL.Image", fromlist=["Image"]).open(__import__("io").BytesIO(
    _lv.poster({"name": "SVT 1 (720p)"}, None, 200)))
check("a channel's card is square, for the round card the screens cut it to", _im40.size == (200, 200))
_css40 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "style.css"), encoding="utf-8").read()
check("and the page cuts it round", ".card.live .poster{aspect-ratio:1/1;border-radius:50%}" in _css40)

# ---- a live channel's frame rate, for the television to match
_said38 = """  Stream #0:0[0x1](und): Video: h264 (Main), yuv420p, 448x252, 25 fps, 25 tbr
  Stream #0:7[0x7](und): Video: h264 (High), yuv420p(tv, bt709), 1920x1080 [SAR 1:1 DAR 16:9], 6002 kb/s, 50 fps, 50 tbr
  Stream #0:8(swe): Audio: aac (LC), 48000 Hz, stereo
"""
check("the chosen stream's rate, the first picture's when none is named, nought when unsaid",
      _lv.rate_of(_said38, 7) == 50.0 and _lv.rate_of(_said38) == 25.0
      and _lv.rate_of("  Stream #0:0: Video: h264, 1280x720") == 0.0)
_s38 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the playlist answer carries it", 'self.send_header("X-Palladium-Fps", "%g" % pd_live.rate(key))' in _s38)

# ---- a new release keeps its key when the list is read again from the other source
_kk51 = __import__("pd_streaming").keep_keys(
    [{"title": "Hope", "tmdb": 7, "key": "rtNEW"}, {"title": "Other", "tmdb": 8, "key": "rtB"}],
    [{"title": "Hope", "tmdb": 7, "key": "rtOLD"}])
check("the same catalogue number, the key it had; a film new to the list, its own",
      [r["key"] for r in _kk51] == ["rtOLD", "rtB"])

# ---- a file's twin, measured already, gives its reading instead of a measurement
_tw51 = {}
exec(compile(__import__("ast").get_source_segment(_src51 := open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read(),
     next(n for n in __import__("ast").parse(_src51).body if getattr(n, "name", "") == "twin_of")), "x", "exec"),
     {"os": os}, _tw51)
_good51 = {"lufs": -14.8, "size": 100, "how": "spread"}
check("same name and size, read the whole way through: its twin; anything less: none",
      _tw51["twin_of"]("A.mkv", 100, [(1, r"G:\x\A.mkv", 100, _good51)]) == 1
      and _tw51["twin_of"]("A.mkv", 100, [(1, r"G:\x\A.mkv", 99, _good51)]) is None
      and _tw51["twin_of"]("A.mkv", 100, [(1, r"G:\x\B.mkv", 100, _good51)]) is None
      and _tw51["twin_of"]("A.mkv", 100, [(1, r"G:\x\A.mkv", 100, {"lufs": -20, "size": 100, "how": "window"})]) is None)

# ---- ffmpeg left behind by an earlier server is ours to stop, and only ours
import importlib.util as _iu51
_sp51 = _iu51.spec_from_file_location("pd_server_51", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"))
_src51 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns51 = {}
exec(compile(__import__("ast").get_source_segment(_src51, next(n for n in __import__("ast").parse(_src51).body
     if getattr(n, "name", "") == "ffmpeg_is_ours")), "x", "exec"), _ns51)
_own51 = _ns51["ffmpeg_is_ours"]
_ff51 = r"C:\Users\x\AppData\Local\Microsoft\WinGet\Links\ffmpeg.EXE"
check("our ffmpeg, quoted or not, or writing into our folder; never somebody else's",
      _own51('"%s" -hide_banner -i a.mkv -af ebur128 -f null -' % _ff51, _ff51, r"C:\data")
      and _own51("%s -i x" % _ff51, _ff51, r"C:\data")
      and _own51(r"C:\other\ffmpeg.exe -i x -f hls C:\data\cache\live\lv1\index.m3u8", _ff51, r"C:\data")
      and not _own51(r"C:\other\ffmpeg.exe -i x out.mp4", _ff51, r"C:\data")
      and not _own51("", _ff51, r"C:\data"))
check("the server stops them as it starts", "threading.Thread(target=stop_left_behind_ffmpeg" in _src51)

# ---- a season whose release counts double episodes as two
import pd_library as _pl58
check("a double episode's two numbers from its filename, and nothing for a single one",
      _pl58.file_double("The.Bureau.US.S04E03E04.1080p.mp4", 4) == (3, 4)
      and _pl58.file_double("Show.S04E03-E04.mkv", 4) == (3, 4)
      and _pl58.file_double("Show.S04E03-04.mkv", 4) == (3, 4)
      and _pl58.file_double("Show.S04E09.mkv", 4) is None
      and _pl58.file_double("Show.S04E03E07.mkv", 4) is None)
_eps58 = [{"episode_number": n, "runtime": r} for n, r in
          ((1, 42), (2, 42), (3, 42), (4, 42), (5, 22), (6, 22), (7, 22), (8, 22), (9, 23),
           (10, 22), (11, 22), (12, 22), (13, 21), (14, 42))]
_map58 = _pl58.doubles_map(_eps58)
check("the catalogue's doubles take two of the release's numbers: E03E04 is E02, E05E06 E03, E09 E05, E18E19 E14",
      _map58[3] == 2 and _map58[4] == 2 and _map58[5] == 3 and _map58[9] == 5
      and _map58[18] == 14 and _map58[19] == 14 and _map58[1] == 1)
_ps58 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("and the season's numbering may be set to it",
      'mode not in ("auto", "files", "database", "doubles")' in _ps58)

# ---- half-fetched files stay out of the library when qBittorrent cannot be asked
import pd_torrents as _pt84, tempfile as _tf84
_root84, _was84 = _tf84.mkdtemp(), _pt84.STATE["root"]
_pt84.STATE["root"] = _root84
_pt84._keep_unfinished({"d:/download/a/a.mkv"})
_pt84.UNFINISHED["paths"] = set()
_qb84 = _pt84.QB
_pt84.QB = lambda cfg: (_ for _ in ()).throw(OSError("qBittorrent is off"))
_pt84._read_unfinished()
_pt84.QB = _qb84
check("qBittorrent off: the files it last called unfinished are still held back",
      "d:/download/a/a.mkv" in _pt84.UNFINISHED["paths"])
_pt84.STATE["root"] = _was84
_pt84.UNFINISHED["paths"] = set()

# ---- a YouTube card carries its name, in two lines at most
import pd_live as _pl81, pd_sports as _ps81
from PIL import Image as _Im81, ImageDraw as _Dr81
_d81 = _Dr81.Draw(_Im81.new("RGB", (400, 400)))
_w81 = _pl81.wrapped(_d81, "Race 1 · Portimão · McLaren Trophy Europe 2026 with a long tail of words", _ps81, 270, 2)
check("a long YouTube name is wrapped to two lines that fit, the last cut short",
      len(_w81) == 2 and all(_d81.textlength(l, font=_ps81._font(26, False)) <= 300 for l in _w81)
      and _w81[-1].endswith("\u2026")
      and len(_pl81.poster({"name": "Sky News", "youtube": True}, None, 200)) > 1000)

# ---- SVT Play: its films, matched by name and year, and the stream of one
import pd_svtplay as _sv103
_t103 = [{"__typename": "Single", "videoSvtId": "aaa1", "name": "Dox: Övervakad", "originalProgramTitle": "Total Trust",
          "productionYear": "2023", "duration": 5831, "restrictions": {"drmCopyProtection": False}, "urls": {"svtplay": "/video/x"}},
         {"__typename": "Single", "videoSvtId": "bbb2", "name": "Locked", "productionYear": "2022", "duration": 6000,
          "restrictions": {"drmCopyProtection": True}},
         {"__typename": "Single", "videoSvtId": "ccc3", "name": "Clip", "productionYear": "2022", "duration": 300,
          "restrictions": {"drmCopyProtection": False}},
         {"__typename": "TvSeries", "name": "A Series", "restrictions": {"drmCopyProtection": False}}]
_f103 = _sv103.films_from(_t103)
_sv103.STATE.update({"films": _f103, "at": time.time(), "index": _sv103.index_of(_f103)})
_s103 = _sv103.stream_from({"rights": {"drmCopyProtection": False}, "contentDuration": 5831, "programTitle": "Dox",
                            "videoReferences": [{"format": "dash", "url": "d.mpd"}, {"format": "hls-ts-full", "url": "t.m3u8"},
                                                {"format": "hls-cmaf-full", "url": "c.m3u8"}],
                            "subtitleReferences": [{"format": "webvtt", "url": "s.vtt"}]})
check("SVT Play: unprotected films only; matched by original name a year either side; the CMAF stream and its subtitles",
      [f["id"] for f in _f103] == ["aaa1"] and _sv103.film_for("Total Trust", 2024) == "aaa1"
      and _sv103.film_for("Total Trust", 2020) == "" and _sv103.film_for("Locked", 2022) == ""
      and _s103["hls"] == "c.m3u8" and _s103["subs"] == [{"url": "s.vtt", "language": "sv"}]
      and _sv103.stream_from({"rights": {"drmCopyProtection": True}, "videoReferences": [{"format": "hls", "url": "x"}]}) == {})

# ---- SVT Play series: unprotected episodes, by season, matched by name and first year
_ts104 = [{"__typename": "TvSeries", "svtId": "s1", "name": "Akuten", "restrictions": {"drmCopyProtection": False},
           "seasons": [{"name": "Säsong 10", "episodes": [
               {"name": "1. En vakande ängel", "videoSvtId": "e1", "positionInSeason": "Säsong 10 — Avsnitt 1",
                "duration": 2600, "productionYear": 2019, "restrictions": {"drmCopyProtection": False}},
               {"name": "2. Låst", "videoSvtId": "e2", "positionInSeason": "Säsong 10 — Avsnitt 2",
                "duration": 2600, "restrictions": {"drmCopyProtection": True}}]},
               {"name": "Extramaterial", "episodes": []}]},
          {"__typename": "TvSeries", "svtId": "s2", "name": "Vigil", "restrictions": {"drmCopyProtection": True},
           "seasons": [{"name": "Säsong 1", "episodes": [{"name": "1", "videoSvtId": "v1",
                                                         "restrictions": {"drmCopyProtection": False}}]}]}]
_s104 = _sv103.series_from(_ts104)
_sv103.STATE.update({"series": _s104, "shows": _sv103.index_of(_s104)})
check("SVT Play series: protected series and episodes left out, empty seasons too; found by name near its first year",
      [s["id"] for s in _s104] == ["s1"] and len(_s104[0]["seasons"]) == 1
      and _s104[0]["seasons"][0]["number"] == 10 and [e["id"] for e in _s104[0]["seasons"][0]["episodes"]] == ["e1"]
      and _s104[0]["seasons"][0]["episodes"][0]["number"] == 1
      and _sv103.series_for("Akuten", 2017) == "s1" and _sv103.series_for("Akuten", 2010) == ""
      and _sv103.series_for("Vigil", 2021) == "" and _sv103.series("s1")["name"] == "Akuten")
_o104 = _sv103.series_from([{"__typename": "TvSeries", "svtId": "k", "name": "Klassen",
    "restrictions": {"drmCopyProtection": False}, "seasons": [
        {"name": "Säsong 2", "episodes": [{"name": "b", "videoSvtId": "b2", "positionInSeason": "Säsong 2 — Avsnitt 2"},
                                          {"name": "a", "videoSvtId": "b1", "positionInSeason": "Säsong 2 — Avsnitt 1"}]},
        {"name": "Julspecial", "episodes": [{"name": "x", "videoSvtId": "x1", "positionInSeason": ""}]},
        {"name": "Säsong 1", "episodes": [{"name": "c", "videoSvtId": "c1", "positionInSeason": "Säsong 1 — Avsnitt 1"}]}]}])
check("SVT Play series run in order: season 1 first, extras last, episodes by number",
      [se["name"] for se in _o104[0]["seasons"]] == ["Säsong 1", "Säsong 2", "Julspecial"]
      and [e["id"] for e in _o104[0]["seasons"][1]["episodes"]] == ["b1", "b2"])
_sv103.STATE["series"] = [{"id": "kk", "name": "Klassen", "seasons": [
    {"name": "Säsong 2", "number": 2, "episodes": [{"id": "e2", "number": 2}, {"id": "e1", "number": 1}]},
    {"name": "Säsong 1", "number": 1, "episodes": [{"id": "f1", "number": 1}]}]}]
check("an SVT series kept in SVT's order is handed out in running order",
      [se["number"] for se in _sv103.series("kk")["seasons"]] == [1, 2]
      and [e["id"] for e in _sv103.series("kk")["seasons"][1]["episodes"]] == ["e1", "e2"])

# ---- a tracker search that failed is not counted as asked; one that found nothing is
import pd_tracker as _pt108
_was108 = (_pt108.search, _pt108.HELD["on"], dict(_pt108.STATE.get("asked") or {}), _pt108.STATE.get("why"))
_pt108.HELD["on"] = False
def _fail108(t, y=0):
    _pt108.STATE["why"] = "the indexer did not answer: timed out"
    return []
def _none108(t, y=0):
    _pt108.STATE["why"] = ""
    return []
_pt108.STATE["lately"] = []
_pt108.search = _fail108; _pt108.ask_for("Nobody Answers Show", 0, again_after=1)
_a1 = _pt108.asked_within("Nobody Answers Show", 0, 60)
_pt108.search = _none108; _pt108.ask_for("Nothing Found Show", 0, again_after=1)
_a2 = _pt108.asked_within("Nothing Found Show", 0, 60)
check("a search the tracker never answered is asked again; an answer of nothing counts", (not _a1) and _a2)
_pt108.search, _pt108.HELD["on"] = _was108[0], _was108[1]
_pt108.STATE["asked"] = _was108[2]; _pt108.STATE["why"] = _was108[3]

# ---- a tracker search covers films, television and its Anime section, and documentaries
import pd_tracker as _pt112
_u112 = _pt112.jackett_url("http://x", "k", "search", "Shutter Hop")
check("a tracker search asks films, TV (Anime with it) and documentaries by category",
      "&cat=2000,5000,100029" in _u112 and "q=Shutter%20Hop" in _u112)

# ---- a series not held says where it streams, as a film does
_src111 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("a series not held carries its streaming services on its page",
      'got["providers"] = self._providers(number, "show")' in _src111)

# ---- single-episode releases: S04E11, the anime "S3 - 11", a group's tag in front
import pd_streaming as _ps110
check("a release of one episode is known by S04E11 or S3 - 11, a group's tag in front or not; a pack is none",
      _ps110.episode_of_release("SHUTTER HOP S04E11 1080p WEB H264-SUGOI") == (4, 11)
      and _ps110.episode_of_release("[SubsPlease] Shutter Hop S3 - 11 (1080p) [5CE4F7D2]") == (3, 11)
      and _ps110.episode_of_release("Show.S02.1080p.BluRay.x265-RBG") is None
      and _ps110.episode_of_release("Some.Film.2024.1080p") is None
      and "shutterhop" in _ps110.series_names([{"name": "[SubsPlease] Shutter Hop S3 - 11 (1080p)"}]))

# ---- a series not held: downloadable, streamed, being asked about, or only to request
import pd_streaming as _ps108
import pd_localapi as _pla108
_n108 = _ps108.series_names([{"name": "Quick Ponies S04 1080p ATVP WEB-DL DDP5 1-NTb"},
                            {"name": "The Bureau US S02 1080p BluRay x265-RBG"},
                            {"name": "Shogun 2024 S01E03 1080p WEB h264-ETHEL"},
                            {"name": "Some.Film.2024.1080p.BluRay.x264-GRP"},
                            {"name": "Dark Season 2 Complete 720p"}])
_k108 = _pla108.LocalAPI.keep_series_card
check("series on the tracker found by name, with or without a year or US; a film is no series; nobody's series hidden unless asked for",
      _ps108.show_fetchable("Quick Ponies", 2022, _n108) and _ps108.show_fetchable("The Bureau", 2005, _n108)
      and _ps108.show_fetchable("Shōgun", 2024, _n108) is False and _ps108.show_fetchable("Shogun", 2024, _n108)
      and _ps108.show_fetchable("Dark", 2017, _n108) and not _ps108.show_fetchable("Some Film", 2024, _n108)
      and _k108("s1", False, False, False) and _k108("", True, False, False) and _k108("", False, True, False)
      and not _k108("", False, False, False) and _k108("", False, False, True))

# ---- something played from SVT Play has a card of its own, keyed sv + its video id
_c107f = _sv103.films_from([{"__typename": "Single", "videoSvtId": "f107", "name": "Pig", "productionYear": 2021,
                             "duration": 5600, "restrictions": {"drmCopyProtection": False},
                             "portraitPosterImage": {"id": "123", "changed": 456}}])
_sv103.STATE.update({"films": _c107f, "series": _s104, "episodes": _sv103.episodes_of(_s104)})
_cf = _sv103.card("f107"); _ce = _sv103.card("e1")
check("an SVT film and an SVT episode as cards, with SVT's poster; an unknown id is none",
      _cf["ratingKey"] == "svf107" and _cf["type"] == "movie" and _cf["thumb"] == "/art/svt/123-456"
      and _cf["svt"] == "f107" and _ce["type"] == "episode" and _ce["grandparentTitle"] == "Akuten"
      and _ce["parentIndex"] == 10 and _ce["index"] == 1 and _sv103.card("nope") is None
      and _sv103.picture_url("123-456") == "https://www.svtstatic.se/image/medium/400/123/456"
      and _sv103.picture_url("../x") == "")

# ---- a streaming service's catalogue is asked for any of the services, every genre, the years
import pd_streaming as _ps99
class _Lib99:
    def __init__(self): self.asked = []
    def tmdb(self, path, timeout=20, **params):
        self.asked.append((path, params))
        return {"results": [{"id": 1, "title": "A", "release_date": "2014-04-06", "poster_path": "/p.jpg"}],
                "total_pages": 7, "total_results": 130}
_ps99._DISCOVER.clear()
_l99 = _Lib99()
_r99 = _ps99.service_page(_l99, "movie", [8, 1899], "se", 2, genres=[18, 53], years=(1990, 1999))
_p99 = _l99.asked[0][1]
_ps99.service_page(_l99, "movie", [8, 1899], "se", 2, genres=[18, 53], years=(1990, 1999))
_ps99.service_page(_l99, "tv", [8], "SE", 1, order="originallyAvailableAt:desc")
_t99 = _l99.asked[-1]
_ps99.service_page(_l99, "movie", [8], "SE", 1, order="rating:desc")
_g99 = _l99.asked[-1][1]
_ps99.service_page(_l99, "tv", [8], "SE", 1, order="titleSort:asc")
_n99 = _l99.asked[-1][1]
check("a service's catalogue: any of the services, every genre, the decade, cached, in the order asked",
      _l99.asked[0][0] == "/discover/movie" and _p99["with_watch_providers"] == "8|1899"
      and _p99["watch_region"] == "SE" and _p99["page"] == 2 and _p99["with_genres"] == "18,53"
      and _p99["primary_release_date.gte"] == "1990-01-01" and _p99["primary_release_date.lte"] == "1999-12-31"
      and _p99["sort_by"] == "popularity.desc" and len(_l99.asked) == 4
      and _t99[0] == "/discover/tv" and _t99[1]["sort_by"] == "first_air_date.desc"
      and _t99[1]["first_air_date.lte"] <= time.strftime("%Y-%m-%d")
      and _g99["sort_by"] == "vote_average.desc" and _g99["vote_count.gte"] == 100
      and _n99["sort_by"] == "name.asc"
      and _r99[1:] == (7, 130) and _ps99.film_row(_r99[0][0])["year"] == 2014)

# ---- the Service filter lists the country's subscriptions, its best known first
_ps99._WATCH.clear()
class _Lib99m:
    def tmdb(self, path, timeout=20, **params):
        rows = [("Netflix", 8, 0), ("JustWatch TV", 2285, 4), ("Apple TV Store", 2, 6), ("Viaplay", 76, 7),
                ("Netflix Kids", 175, 10), ("Blockbuster", 423, 14), ("SVT Play", 493, 17)]
        if path.endswith("/tv"):
            rows += [("HBO Max", 1899, 42)]
        return {"results": [{"provider_name": n, "provider_id": i, "logo_path": "/l.png",
                             "display_priorities": {"SE": p}} for n, i, p in rows]}
_m99 = _ps99.menu_services(_Lib99m(), "se", 14)
check("the Service filter: Sweden's big services only, films' and TV's together, HBO Max second",
      [s["name"] for s in _m99] == ["Netflix", "HBO Max", "Viaplay", "SVT Play"] and _m99[1]["id"] == 1899
      and _ps99.menu_services(_Lib99m(), "XX", 14)[0]["name"] == "Netflix"
      and "Blockbuster" not in [s["name"] for s in _ps99.menu_services(_Lib99m(), "XX", 14)])

# ---- a search shows a film once, though its pack and the catalogue date it a year apart
import pd_localapi as _pla98
_cards98 = [{"title": "Coherence", "year": 2013, "tmdb": 220289}, {"title": "Primer", "year": 2004}]
_rows98 = [{"title": "Coherence", "year": 2014, "tmdb": 0}, {"title": "Primer", "year": 2004, "tmdb": 14337},
           {"title": "Something", "year": 2020, "tmdb": 220289}, {"title": "Primer", "year": 2019, "tmdb": 9}]
check("the catalogue's own copy of a film already shown is left out: same number, or same title a year either side",
      [r["year"] for r in _pla98.not_shown_yet(_rows98, _cards98)] == [2019])

# ---- a season numbered by choice is renumbered when its files change, once per change
import pd_library as _pl91
_calls91 = []
_fake91 = type("F", (), {})()
_fake91.root = None
import tempfile as _tf91, sqlite3 as _sq91
_d91 = _tf91.mkdtemp()
with open(os.path.join(_d91, "settings.json"), "w", encoding="utf-8") as _f91:
    _js.dump({"numbering": {"abc-s4": "doubles", "def-s1": "auto"}}, _f91)
_db91 = os.path.join(_d91, "t.db")
_c91 = _sq91.connect(_db91)
_c91.execute("CREATE TABLE episode (id INTEGER PRIMARY KEY, item_id TEXT, season INT, number INT)")
_c91.execute("CREATE TABLE file (id INTEGER PRIMARY KEY, episode_id INT, path TEXT)")
_c91.execute("INSERT INTO episode VALUES (1, 'abc', 4, 10)")
_c91.execute("INSERT INTO file VALUES (1, 1, 'x.S04E10.mkv')")
_c91.commit(); _c91.close()
_fake91.root = _d91
_fake91.db = lambda: _sq91.connect(_db91)
_fake91.renumber = lambda k, s, m: _calls91.append((k, s, m))
_keep91 = _pl91.Library.keep_numbering
_n1 = _keep91(_fake91); _n2 = _keep91(_fake91)
_c91 = _sq91.connect(_db91); _c91.execute("INSERT INTO file VALUES (2, 1, 'x.S04E14.mkv')"); _c91.commit(); _c91.close()
_n3 = _keep91(_fake91)
check("a doubles season is put on its numbers at start and again when a file arrives, an auto one never",
      (_n1, _n2, _n3) == (1, 0, 1) and _calls91 == [("abc", 4, "doubles"), ("abc", 4, "doubles")])

# ---- the live channels are the main server's on the copy too, and not changed there
import pd_follow as _pf92
_srv92 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_lock92 = _srv92[_srv92.index("MIRROR_LOCKED = frozenset(("):]
_lock92 = _lock92[:_lock92.index("))")]
check("the copy takes the live playlist, YouTube channels and groups from the main server, and cannot change them",
      all(k in _pf92.HOUSE_MIRRORED and ('"%s"' % k) in _lock92
          for k in ("liveList", "liveMost", "liveGroups", "liveFolders", "liveYouTube", "liveWho")))

# ---- room on the drive: a guest sees it only when the file does not fit
import pd_torrents as _pt88
_r88 = _pt88.room_for_guest([{"offer": {"size": 4 * 10**9, "free": 900.0, "versions": []}},
                              {"offer": {"size": 5 * 10**9, "free": 3.0}},
                              {"free": 2.0, "found": [{"size": 10**9}, {"size": 6 * 10**9}]},
                              {"free": 2.0, "found": [{"size": 10**9}]},
                              {"free": [{"provider_name": "x"}]}])
check("a guest is not told the room left unless a file in the reply does not fit in it",
      _r88[0]["offer"]["free"] is None and _r88[1]["offer"]["free"] == 3.0
      and _r88[2]["free"] == 2.0 and _r88[3]["free"] is None
      and _r88[4]["free"] == [{"provider_name": "x"}])

# ---- a YouTube read that reached nothing keeps what was known
import pd_iptv as _pi88
_was88 = (_pi88.read_youtube_now, _pi88.youtube_sources, dict(_pi88._YT))
_pi88.youtube_sources = lambda: ["https://www.youtube.com/@x"]
_pi88._YT.update({"channels": [{"key": "lvkept0000001"}], "list": [{"key": "lvkept0000002"}], "at": 0.0, "on": False})
_pi88.read_youtube_now = lambda: ([], [])
_pi88.youtube_live()
for _ in range(50):
    if not _pi88._YT["on"]:
        break
    time.sleep(0.05)
check("a YouTube read that reached no channel leaves the known channels and streams in place",
      _pi88._YT["channels"] == [{"key": "lvkept0000001"}] and _pi88._YT["list"] == [{"key": "lvkept0000002"}])
_pi88.read_youtube_now, _pi88.youtube_sources = _was88[0], _was88[1]
_pi88._YT.clear(); _pi88._YT.update(_was88[2])

# ---- streaming badges for the chosen country, or where the title is out already
import pd_streaming as _ps87
_res87 = {"US": {"rent": [{"provider_name": "Apple TV Store", "logo_path": "/a.png", "provider_id": 2}]},
          "GB": {"flatrate": [{"provider_name": "Netflix", "logo_path": "/n.png", "provider_id": 8}]}}
_c87, _w87 = _ps87.providers_country(_res87, "SE", "SE")
_c88, _w88 = _ps87.providers_country(dict(_res87, SE={"flatrate": [{"provider_name": "Netflix", "logo_path": "/n.png", "provider_id": 8}]}), "SE", "SE")
check("nothing in the chosen country: the US's services, said to be the US's; Sweden's first when it has some",
      _c87 == "US" and [p["name"] for p in _w87] == ["Apple TV Store"]
      and _c88 == "SE" and _ps87.providers_country({}, "SE", "SE") == ("SE", []))

# ---- a black logo on a see-through ground is drawn white; a picture is left alone
import pd_live as _pl86
from PIL import Image as _Im86, ImageDraw as _Dr86
_logo86 = _Im86.new("RGBA", (100, 50), (0, 0, 0, 0))
_Dr86.Draw(_logo86).rectangle((10, 10, 60, 40), fill=(10, 10, 10, 255))
_pic86 = _Im86.new("RGBA", (100, 50), (20, 20, 20, 255))
_lite86 = _Im86.new("RGBA", (100, 50), (0, 0, 0, 0))
_Dr86.Draw(_lite86).rectangle((10, 10, 60, 40), fill=(240, 240, 240, 255))
check("a dark logo is drawn white; a light one and a full picture are not",
      _pl86.dark_logo(_logo86) and not _pl86.dark_logo(_pic86) and not _pl86.dark_logo(_lite86))

# ---- a YouTube channel's card plays its English stream, not an onboard
import pd_iptv as _pi85
_l85 = [{"id": "de", "title": "LIVE | Rennen 2 | GT4 (Deutsch)"},
        {"id": "ob", "title": "LIVE OB #3 | Race | GT World (English)"},
        {"id": "en", "title": "LIVE | Race | GT World Challenge (English)"}]
check("a channel's card plays the English stream that is not an onboard",
      _pi85.main_stream(_l85)["id"] == "en" and _pi85.main_stream(_l85[:1])["id"] == "de"
      and _pi85.main_stream([]) is None)

# ---- yt-dlp kept current, and a failed YouTube start reads its address again
import pd_live as _pl79
_pl79.YOUTUBE_OF["https://www.youtube.com/watch?v=zz"] = ("https://old", time.time())
_one79 = _pl79.one
_pl79.one = lambda key: {"key": key, "url": "https://www.youtube.com/watch?v=zz"}
_pl79.forget_source("lvzz")
_pl79.one = _one79
_srv79 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_iptv.py"), encoding="utf-8").read()
check("a YouTube stream that did not start is read afresh, and yt-dlp is updated daily",
      "https://www.youtube.com/watch?v=zz" not in _pl79.YOUTUBE_OF
      and "pd_live.keep_ytdlp()" in _srv79 and _pl79.YTDLP_EVERY == 86400)

# ---- YouTube streams on the Live TV row: first, titled plainly, played from yt-dlp
import pd_iptv as _pi77, pd_live as _pl77
check("a stream's title loses its LIVE and its I bars, and OB is the onboard",
      _pi77.youtube_title("LIVE | Race | Portimao | GT World (English)") == "Race · Portimao · GT World (English)"
      and _pi77.youtube_title("LIVE OB #3 | Race") == "Onboard #3 · Race"
      and _pl77.is_youtube("https://www.youtube.com/watch?v=abc") and not _pl77.is_youtube("http://x/live.m3u8"))
_kept77, _yt77, _ch77, _wk77, _fc77 = (_pi77.kept_channels, _pi77.youtube_live,
                                       _pi77.youtube_channels, _pi77.week_seconds,
                                       _pi77.folder_cards)
_pi77.kept_channels = lambda screen="": [{"key": "lv000000000001", "name": "SVT 1", "group": "Sweden"}]
_pi77.youtube_live = lambda: [{"key": "lv0000000000yt", "name": "Race", "group": "YouTube", "youtube": True}]
_pi77.youtube_channels = lambda: [{"key": "lv00000000chan", "name": "GT World", "group": "YouTube",
                                   "youtube": True, "off": False}]
_pi77.folder_cards = lambda every: []
_pi77.week_seconds = lambda now=None: {}
_row77 = [c["ratingKey"] for c in _pi77.own_row()]
_in77 = [c["ratingKey"] for c in _pi77.own_row(group="YouTube")]
_more77 = [c.get("liveGroup") for c in _pi77.more_cards([{"key": "lv1", "name": "SVT 1", "group": "Sweden"}])]
_pi77.week_seconds = lambda now=None: {"lv00000000chan": 900}
_used77 = _pi77.own_row()
_pi77.kept_channels, _pi77.youtube_live, _pi77.youtube_channels, _pi77.week_seconds, _pi77.folder_cards = (
    _kept77, _yt77, _ch77, _wk77, _fc77)
check("YouTube lives in its circle under Show more: channels, then live streams; off the row until used",
      _row77 == ["lv000000000001"] and _in77 == ["lv00000000chan", "lv0000000000yt"]
      and "YouTube" in _more77)
_pi77.recent = (lambda real: (lambda: ["lv00000000chan"]))(_pi77.recent)
_pi77.kept_channels = lambda screen="": [{"key": "lv000000000001", "name": "SVT 1", "group": "Sweden"}]
_pi77.youtube_live, _pi77.youtube_channels = (lambda: []), (lambda: [{"key": "lv00000000chan", "name": "GT World",
                                                                      "group": "YouTube", "youtube": True}])
_pi77.week_seconds = lambda now=None: {"lv000000000001": 36000, "lv00000000chan": 600}
_pi77.folder_cards = lambda every: []
_last78 = [c["ratingKey"] for c in _pi77.own_row()]
_pi77.kept_channels, _pi77.youtube_live, _pi77.youtube_channels, _pi77.week_seconds, _pi77.folder_cards = (
    _kept77, _yt77, _ch77, _wk77, _fc77)
check("the channel played last stands first, ahead of one watched longer this week",
      _last78 == ["lv00000000chan", "lv000000000001"])
check("a YouTube channel watched this week stands on the row by itself, not its circle",
      [c.get("liveGroup") or c["ratingKey"] for c in _used77] == ["lv00000000chan", "lv000000000001"])

# ---- a subtitle is this episode by its title, whichever numbering its name uses
import pd_localapi as _la76
check("a subtitle numbered the catalogue's way or the file's way can be this episode",
      _la76.number_is_ours((4, 6), 4, 6, 4) and _la76.number_is_ours((4, 10), 4, 6, 4)
      and not _la76.number_is_ours((4, 7), 4, 6, 4) and not _la76.number_is_ours((5, 6), 4, 6, 4))
check("one named after another episode of the season never is, hash or not",
      _la76.names_another({"office", "s04e10", "chair", "model"}, {"branch", "wars"},
                          [{"chair", "model"}, {"local", "ad"}])
      and not _la76.names_another({"office", "s04e06", "branch", "wars"}, {"branch", "wars"},
                                  [{"chair", "model"}])
      and not _la76.names_another({"office", "s04e10", "bluray"}, {"branch", "wars"},
                                  [{"chair", "model"}]))
_src76 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("and the search asks for our number, the file's number added once, not twice",
      'return (src, row["title"], row["year"], row["season"], row["number"])' in _src76)

# ---- a season's numbering is the main server's on the cache too
import pd_follow as _pf75
check("the cache renumbers the seasons whose numbering the main server changed",
      _pf75.numbering_changes({}, {"3ed885df2f7c-s4": "doubles"}) == [("3ed885df2f7c", 4, "doubles")]
      and _pf75.numbering_changes({"a1-s2": "files"}, {}) == [("a1", 2, "auto")]
      and _pf75.numbering_changes({"a1-s2": "files"}, {"a1-s2": "files"}) == []
      and "numbering" in _pf75.HOUSE_MIRRORED)

# ---- half of a double episode is never the version played by default
import pd_localapi as _la72
_rows72 = [{"id": 1, "path": "S.S04E07E08.BluRay.x265.mp4", "size": 700e6, "height": 1080, "bitrate": 0, "duration": 2520.0},
           {"id": 2, "path": "S.S04E08.Money.Part.2.Extended.AMZN.mkv", "size": 2.2e9, "height": 1080, "bitrate": 0, "duration": 1728.0}]
_b72 = _la72.best_first(_rows72)
check("a part-only version goes after the whole episode, however much bigger, and says which part it is",
      [r["id"] for r in _b72] == [1, 2] and _la72.part_label(_rows72[1]["path"]) == "Part 2 only"
      and not _la72.part_only(2520.0 * 0.85, 2520.0))

# ---- one viewing read over two addresses is one row
import pd_watching as _pw70
_w70 = _pw70.Watching()
_w70.start("me", "A Film", "1080p", "direct play", "93.184.216.34", key="k1", app="a", device="Google TV app")
_w70.start("me", "A Film", "1080p", "direct play", "192.168.0.157", key="k1", app="a", device="Google TV app")
_w70.start("guest", "A Film", "1080p", "direct play", "100.73.1.2", key="k1", app="a", device="Google TV app")
_rows70 = _w70.snapshot()
check("one person's film over the house network and the outside address is one row, at the house address",
      len(_rows70) == 2
      and [r["address"] for r in _rows70 if r["who"] == "me"] == ["192.168.0.157"])

# ---- a copy that picks up where it stopped is not counted twice
_w71 = _pw70.Watching()
_mb71 = 1048576
_a71 = _w71.start("Cache", "A Film", "", "syncing", "192.168.0.25", key="k2", size=2000 * _mb71)
_w71.sent(_a71, 1000 * _mb71)
_w71.stop(_a71)
_b71 = _w71.start("Cache", "A Film", "", "syncing", "192.168.0.25", key="k2", offset=1000 * _mb71, size=2000 * _mb71)
_w71.sent(_b71, 500 * _mb71)
_r71 = [r for r in _w71.snapshot() if r["key"] == "k2"]
check("a copy resumed at its offset reads 1500 of 2000 MB, not the first part twice",
      len(_r71) == 1 and _r71[0]["mb"] == 1500.0 and _r71[0]["ofMb"] == 2000.0)

# ---- a place between the two servers goes to the episode by its key first
import sqlite3 as _sq65
import pd_localapi as _la65
_c65 = _sq65.connect(":memory:")
_c65.row_factory = _sq65.Row
_c65.executescript("""CREATE TABLE item (id TEXT, type TEXT, title TEXT, year INTEGER);
CREATE TABLE episode (id TEXT, item_id TEXT, season INTEGER, number INTEGER, title TEXT);
INSERT INTO item VALUES ('aaaaaaaaaaaa', 'show', 'A Show', 2005);
INSERT INTO episode VALUES ('e000000000002', 'aaaaaaaaaaaa', 4, 2, 'Infinity');
INSERT INTO episode VALUES ('e000000000003', 'aaaaaaaaaaaa', 4, 3, 'Launch Party');
INSERT INTO episode VALUES ('e000000000004', 'aaaaaaaaaaaa', 4, 4, 'Money');
INSERT INTO episode VALUES ('e000000000005', 'aaaaaaaaaaaa', 4, 5, 'Local Ad');""")
# the cache files Infinity under this machine's key for it, but numbers it 4
_said65 = {"type": "episode", "show": "A Show", "season": 4, "episode": 4,
           "key": "e000000000002", "name": "Infinity"}
# and Local Ad under the key this machine gives Launch Party, numbered 5 like here
_ad65 = {"type": "episode", "show": "A Show", "season": 4, "episode": 5,
         "key": "e000000000003", "name": "Local Ad"}
check("a place from the other server lands on the episode its title names, by key or by number",
      _la65.LocalAPI.key_of(None, _c65, _said65) == "e000000000002"
      and _la65.LocalAPI.key_of(None, _c65, _ad65) == "e000000000005"
      and _la65.LocalAPI.key_of(None, _c65, dict(_said65, key="e00000000000f")) == "e000000000004"
      and _la65.LocalAPI.key_of(None, _c65, {k: v for k, v in _said65.items() if k != "name"}) == "e000000000002"
      and _la65.LocalAPI.what_it_is(None, _c65, "e000000000005")["name"] == "Local Ad")
_srv65 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("a still's second is asked as s, not as the sign-in key t",
      'self._frame(m.group(1), one("s", "0"), one("w", "0"))' in _srv65)

# ---- the subtitle list and fetching ahead are one order with one pick
import importlib.util as _iu62
_sp62 = _iu62.spec_from_file_location("pd_server_62", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"))
_src62 = open(_sp62.origin, encoding="utf-8").read()
_ns62 = {}
exec(compile(_src62[_src62.index("def auto_pick("):_src62.index("def release_flat(")], "auto_pick", "exec"), _ns62)
_ap62 = _ns62["auto_pick"]
check("auto pick: the first for this episode, a film's first, nothing for an episode with none",
      _ap62([{"id": 1, "episode": False}, {"id": 2, "episode": True}, {"id": 3, "episode": True}], True)["id"] == 2
      and _ap62([{"id": 1, "episode": False}], False)["id"] == 1
      and _ap62([{"id": 1, "episode": False}], True) is None and _ap62([], False) is None)
check("and fetching ahead takes the one the list marks, not an order of its own",
      'pick = next((r for r in results if r.get("autoPick")), None)' in _src62
      and _src62.count("self.rank_subtitles(") == 1)

# ---- the stills worker: what is being watched first, then what is new
import pd_localapi as _la61
_now61 = 2_000_000_000
_prog61 = [{"key": "film1", "position": 600, "duration": 6000, "updated": _now61 - 100, "marked": 0},
           {"key": "ep1", "position": 2500, "duration": 2520, "updated": _now61 - 50, "marked": 0},
           {"key": "film2", "position": 5990, "duration": 6000, "updated": _now61 - 10, "marked": 1},
           {"key": "film3", "position": 300, "duration": 6000, "updated": _now61 - 90 * 86400, "marked": 0}]
_files61 = [{"id": 1, "item_id": "film1", "episode_id": None, "ctime": 0, "duration": 6000},
            {"id": 2, "item_id": "show", "episode_id": "ep2", "ctime": 0, "duration": 2520, "next_of": "ep1"},
            {"id": 3, "item_id": "film2", "episode_id": None, "ctime": 0, "duration": 6000},
            {"id": 4, "item_id": "film3", "episode_id": None, "ctime": 0, "duration": 6000},
            {"id": 5, "item_id": "new", "episode_id": None, "ctime": _now61 - 86400, "duration": 5000},
            {"id": 6, "item_id": "newer", "episode_id": None, "ctime": _now61 - 3600, "duration": 5000},
            {"id": 7, "item_id": "nolen", "episode_id": None, "ctime": _now61 - 60, "duration": 0}]
_got61 = [f["id"] for f in _la61.stills_order(_prog61, _files61, {6}, _now61)]
check("stills: the next episode and the film being watched first, then the new ones; "
      "nothing finished, stale, done or without a length",
      _got61 == [2, 1, 5])

# ---- a programme removed: its packs go, but not one shared or one a file came from
import pd_torrents as _pt60
_cfg60 = {"showPacks": {"aseries": {"packs": ["h1", "h2", "h3"], "season": {}},
                        "another": {"packs": ["h2"], "season": {}}},
          "showPack": {"aseries": "h4"}}
check("a removed programme's packs go, but not one another chose or one a file came from",
      _pt60.packs_free("A Series", _cfg60, [{"hash": "h3", "state": "done"}]) == ["h1", "h4"]
      and _pt60.packs_free("Nothing", _cfg60, []) == [])

# ---- search: a year in the words, and the closest title first
import pd_localapi as _la54
check("a year at the end is the year, not part of the title; a year alone stays the words",
      _la54.title_and_year("the orchard 2004") == ("the orchard", 2004)
      and _la54.title_and_year("The Orchard (2004)") == ("The Orchard", 2004)
      and _la54.title_and_year("1917") == ("1917", 0)
      and _la54.title_and_year("star courier") == ("star courier", 0))
check("the title itself, with or without the, before one beginning with it, a word, anywhere",
      [_la54.match_rank(t, "the orchard") for t in
       ("The Orchard", "Orchard", "Orchard of the Damned", "The Lost Orchard", "Orchardists", "Cars")]
      == [0, 0, 1, 2, 1, 4])
_hubs54 = [{"type": "movie", "Metadata": [
    {"title": "Orchard of the Damned", "year": 1995}, {"title": "The Orchard", "year": 1993},
    {"title": "The Orchard", "year": 2004}, {"title": "The Lost Orchard", "year": 2004}]},
    {"type": "person", "Metadata": [{"title": "x"}]}]
_bf54 = _la54.closest_first([dict(h, Metadata=[dict(x) for x in h["Metadata"]]) for h in _hubs54], "the orchard")
check("closest first, the server's order kept among equals",
      [(x["title"], x["year"]) for x in _bf54[0]["Metadata"]]
      == [("The Orchard", 1993), ("The Orchard", 2004), ("Orchard of the Damned", 1995), ("The Lost Orchard", 2004)])
_bfy54 = _la54.closest_first([dict(h, Metadata=[dict(x) for x in h["Metadata"]]) for h in _hubs54], "the orchard", 2004)
check("with the year: the exact title of that year only",
      [(x["title"], x["year"]) for x in _bfy54[0]["Metadata"]] == [("The Orchard", 2004)]
      and _bfy54[1]["type"] == "person")

# ---- a copy refuses changes to what it mirrors
_s50 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_locked50 = set(__import__("re").findall(r'"([A-Za-z0-9]+)"', _s50[_s50.index("MIRROR_LOCKED = frozenset(("):_s50.index("    def mirror_of(self):")]))
import pd_follow as _pf50
_house_body50 = {k for k in _pf50.HOUSE_MIRRORED if k not in ("subtitles", "subSizeBase", "subPlaceRound", "subLang", "subLang2", "homeRowsDefault")}
check("every mirrored house setting is locked on a copy: %s" % sorted(_house_body50 - _locked50),
      _house_body50 <= _locked50 and not {"apartPause", "autoRuns", "outsideName", "subShift"} & _locked50)
check("the settings endpoint refuses them first, and says which server they are set on",
      "locked = self.mirror_locked(body)" in _s50 and '"mirrorOf": self.mirror_of(),' in _s50)

# ---- the copy knows each viewer's second subtitle language and their shelf choices
_vk48 = _src45 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_keep48 = _vk48[_vk48.index('if path == "/follow/viewers":'):]
_keep48 = _keep48[:_keep48.index("out = {}")]
check("every viewer setting goes to the copy, by the server's own list, but the ones naming titles by number",
      "keep = tuple(k for k in Handler.VIEWER_KEYS + (\"quality\",)" in _keep48
      and "if k not in pd_follow.VIEWER_LOCAL)" in _keep48)
import pd_follow as _pf49
_fsrc49 = open(_pf49.__file__, encoding="utf-8").read()
check("the copy takes a viewer's setting whenever it differs, and drops a house setting unset there",
      "if here.get(name) != value:" in _fsrc49 and "del mine[name]            # unset on the main server, unset here" in _fsrc49
      and "deckAside" in _pf49.VIEWER_LOCAL and "subLang2" in _pf49.SHARED)
_aj48 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("a quality change keeps the subtitle showing", "o.subId = S.subId || 0;\n    o.height = what.height" in _aj48)

# ---- the copy chooses releases by the main server's rules
import pd_follow as _pf45
_src45 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_rel45 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_release.py"), encoding="utf-8").read()
_used45 = set(__import__("re").findall(r'"(prefer[A-Z][A-Za-z0-9]*|rateLock|closeCall|seriesRate)"', _src45 + _rel45))
check("every setting that chooses a release is mirrored to the copy: %s" % sorted(_used45 - set(_pf45.CHOOSING)),
      _used45 <= set(_pf45.CHOOSING) and set(_pf45.CHOOSING) <= set(_pf45.HOUSE_MIRRORED)
      and "house = {k: stored[k] for k in pd_follow.HOUSE_MIRRORED if k in stored}" in _src45)
check("and what every viewer meets: the quality ceilings, burn-in, even volume, next-episode pause, subtitle fetch, region, accent",
      set(_pf45.SHARED) <= set(_pf45.HOUSE_MIRRORED)
      and {"quality", "burnFor", "evenVolume", "nextDelay", "autoFetch", "watchRegion", "accent"} <= set(_pf45.SHARED)
      and not {"port", "ffmpeg", "receiver", "gameMode", "followShows", "machineId"} & set(_pf45.HOUSE_MIRRORED))

# ---- the app opens a live channel at the door that answers, not the home address
_ma36 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                          "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("the live player is given addressOf(), as a film is",
      '.putExtra("base", Api.addressOf(picked.srv))' in _ma36 and "picked.srv?.base ?: Api.base" not in _ma36)

# ---- a search with few films asks the catalogue too, not only one with none
_la46 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("the catalogue is asked on every search, behind it, and adds only new ones",
      'if not (spelt and len(spelt.strip()) >= 3 and not place):' in _la46
      and "found = pd_streaming.by_name_soon(self.lib, spelt)" in _la46
      and '"fillingIn": bool(getattr(self._filling, "on", False))}' in _la46
      and __import__("pd_streaming").BY_NAME_MOST >= 20
      and "not in shown]" in _la46 and __import__("pd_localapi").FEW_FILMS >= 2)

# ---- a direct play refused for the ceiling becomes an encode in the app
_pa46 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                          "java", "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("a 403 on a direct play restarts as an encode, not a network fault",
      ".any { it.responseCode == 403 }" in _pa46 and "audio = Route.ENCODE_AUDIO)" in _pa46)

# ---- the Downloading button is the cancel button
check("no separate Cancel download; the button arms, then cancels",
      'Pill("Cancel download")' not in _ma36 and "val press = Route.offerPress(live.offerState, armed)" in _ma36
      and '"Press again to cancel"' in _ma36)

# ---- a live shelf opened whole has no film controls above it
check("Show more on the live row, and a group opened, hide sort, genre, decade and country",
      "picks = if (browse.liveView && browse.moreRow != null) null" in _ma36
      and _ma36.count("browse.liveView = true") == 2 and "liveView = false" in _ma36)

# ---- the plugins load with the server, not with the first page that asks for them
_ss35 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read().replace("\r\n", "\n")
check("pd_plugins imported beside pd_live at start",
      "pd_live.use_data_dir(ROOT)\n        # the plugins at start" in _ss35)

# ---- a DASH channel is played from its HLS twin, and a restart carries on its numbering
check("SVT's DASH address has its HLS twin first, and an HLS address none",
      _lv.twins("https://x.net/l6/se/svt2/manifest.mpd?format=dash&defaultSubLang=1")[0]
      == "https://x.net/l6/se/svt2/master-fmp4.m3u8?format=hls&defaultSubLang=1"
      and _lv.twins("https://x.net/a/playlist.m3u8") == [])
check("a restart numbers on from the last piece, a first start from nought",
      _lv.next_number(["index.m3u8", "seg000007.ts", "seg000012.ts"]) == 13
      and _lv.next_number([]) == 0)
_lvs34 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_live.py"), encoding="utf-8").read()
check("the command is given the twin and the number", '"-i", src,' in _lvs34
      and '"-start_number", str(first),' in _lvs34)

# ---- a live channel is answered at the address the web page asks
import re as _re3
_lvsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_lvpat = _re3.search(r'm = re\.match\(r"(\^\(\?:/local\)\?/live/[^"]+)", path\)', _lvsrc)
check("both /live/ and /local/live/ reach the channel",
      bool(_lvpat) and all(_re3.match(_lvpat.group(1), p) for p in
                           ("/live/lv0123456789ab/index.m3u8", "/local/live/lv0123456789ab/seg000001.ts")))

# ---- a film found by the names the catalogue knows it by, and without its year
import pd_streaming as _pst, pd_tracker as _ptk2
check("the catalogue's other names kept", _pst.also_named("Battle Cabin", {"title": "Northspur", "original_title": "Northspur"}) == ["Northspur"]
      and _pst.also_named("Colony", {"title": "Colony", "original_title": "Colony"}) == [])
_asked = []
_real_search = _ptk2._search
_ptk2._search = lambda title, year=0: (_asked.append((title, year)) or ([{"name": "x"}] if year == 0 else []))
_got = _ptk2.search("Northspur", 2023)
_ptk2._search = _real_search
check("nothing with the year: asked once more without it", _asked == [("Northspur", 2023), ("Northspur", 0)] and _got)
_real_find = _ptk2.find
_ptk2.find = lambda title, year=0, imdb="": [{"name": "Northspur 2022"}] if title == "Northspur" else []
check("the shelf's card counts the releases under its other name",
      _pst.fetchable({"title": "Battle Cabin", "year": 2023, "aka": ["Northspur"]}) == [{"name": "Northspur 2022"}])
_ptk2.find = _real_find

print("RULES FAILED: %d" % len(FAILS) if FAILS else "RULES PASSED")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
