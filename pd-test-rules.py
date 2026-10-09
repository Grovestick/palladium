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

print("RULES FAILED: %d" % len(FAILS) if FAILS else "RULES PASSED")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
