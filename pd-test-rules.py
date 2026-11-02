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
FAILED = {"failed": 3, "failedSize": 10}
check("a file whose readings failed three times at its size is left; at another size, or twice, it is tried",
      not rcv.wants_measuring(FAILED, "aac", size=10) and rcv.wants_measuring(FAILED, "aac", size=11)
      and rcv.wants_measuring(dict(FAILED, failed=2), "aac", size=10)
      and [o[0] for o in rcv.measure_order([(7, "g", 10, "aac"), (6, "f", 10, "aac")],
                                           {"7": FAILED, "6": dict(FAILED, failed=1)})] == [6])
_known = {"1": dict(FAILED), "2": dict(WHOLE, failed=1, failedSize=10), "3": dict(WHOLE)}
_srvL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("Loudness is one of the runs the owner turns off, and off it measures nothing",
      '"fixer",\n             "loudness", "warmup", "xseed")' in _srvL
      and _srvL.index('if not auto_on("loudness"):') < _srvL.index("    if worker_waits(for_screen=playing, other_drive=free_drive):"))
check("Reset: the failed readings go, a reading stays, and the files are wanted again",
      rcv.forget_failures(_known) == 2 and "1" not in _known and _known["2"] == WHOLE
      and _known["3"] == WHOLE and rcv.wants_measuring(_known.get("1"), "aac", size=10))
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
      "self._person_asking = pd_streaming.ask_tracker_about(rest[:8], self.lib)" in _l22
      and "if pd_streaming.ask_tracker_slowly(rest[8:]):" in _l22
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
# (the episode's version list asking for its season is run in "an episode's packs" below)

# ---- a season whose only packs miss the choices still gets one, the best seeded
_s29 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("the finder takes the best seeded pack when none meets the choices",
      'best = found.get("pick") or max(' in _s29 and 'if int(o.get("seeds") or 0) > 0 and not o.get("unsure")],' in _s29)

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
      "asking = self._catalogue_in_search(spelt, hubs) or asking" in _l24
      and "return len(said) >= 3 and self._collection_in_search(spelt, hubs)" in _l24)

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

# ---- a torrent being rechecked is reading its files, not writing them: they stay in
import json as _jsK, tempfile as _tfK, pd_torrents as _ptK
_dK = _tfK.mkdtemp()
_fK = os.path.join(_dK, "Film.2000.mkv")
with open(_fK, "wb") as _w:
    _w.write(os.urandom(4096))


class _QBK:
    state = "checkingUP"

    def __init__(self, cfg):
        pass

    def _call(self, path, timeout=10):
        return _jsK.dumps([{"hash": "h1", "save_path": _dK, "downloaded": 5, "progress": 0.4,
                            "amount_left": 1, "size": 4096, "state": _QBK.state}]).encode()

    def files(self, h):
        return [{"name": "Film.2000.mkv", "progress": 0.4, "priority": 1}]


_wasK = (_ptK.QB, _ptK.load, _ptK._keep_unfinished)
_ptK.QB, _ptK.load, _ptK._keep_unfinished = _QBK, (lambda: {"config": {}}), (lambda paths: None)
try:
    _ptK.FILE_LISTS.clear()
    _ptK._read_unfinished_now()
    _checking = set(_ptK.UNFINISHED["paths"])
    _QBK.state = "downloading"
    _ptK.FILE_LISTS.clear()
    _ptK._read_unfinished_now()
    _writing = set(_ptK.UNFINISHED["paths"])
finally:
    _ptK.QB, _ptK.load, _ptK._keep_unfinished = _wasK
    _ptK.FILE_LISTS.clear()
check("a file being rechecked stays in the library; the same file being downloaded does not: %s %s"
      % (_checking, _writing), not _checking and len(_writing) == 1)

# ---- the copy carries over the main server's subtitle choices to its own files
import pd_follow as _pfS
_gotS = _pfS.subs_for_copy(
    {"subsPick": {"t1": "A.en.srt", "t9": "Gone.en.srt"}, "subsOk": {"e1": {"en": {"release": "x"}}},
     "subFit": {"G:\\Series\\S\\Show.S01E01.mkv.en.srt": {"parts": [1]}, "D:\\X\\Nope.srt": {}},
     "splitSubs": {"D:\\Dl\\Two.S01E01-02.mkv|2": [0.0, 600.0]}},
    {"t1", "e1"}, {"show.s01e01.mkv.en.srt": "D:/Palladium\\Show.S01E01.mkv.en.srt",
                   "two.s01e01-02.mkv": "D:/Palladium\\Two.S01E01-02.mkv"})
check("copy: subtitle choices for titles held here, paths turned into the files here: %s" % _gotS,
      _gotS["subsPick"] == {"t1": "A.en.srt"} and list(_gotS["subsOk"]) == ["e1"]
      and _gotS["subFit"] == {"D:/Palladium\\Show.S01E01.mkv.en.srt": {"parts": [1]}}
      and _gotS["splitSubs"] == {"D:/Palladium\\Two.S01E01-02.mkv|2": [0.0, 600.0]})

# ---- the copy server's receiver: the main server's settings, its own, or off
import pd_receiver as _rcF
_mainF = {"on": True, "ip": "192.168.0.140", "steps": 8, "mode": "loudness", "device": "192.168.0.157"}
check("copy receiver: main's taken by default, own kept, off turns it off",
      _rcF.for_copy(_mainF, {})["mode"] == "loudness" and _rcF.for_copy(_mainF, {})["on"] is True
      and _rcF.for_copy(_mainF, {"from": "own", "on": False, "steps": 3})["steps"] == 3
      and _rcF.for_copy(_mainF, {"from": "own", "on": False})["on"] is False
      and _rcF.for_copy(_mainF, {"from": "off", "on": True})["on"] is False
      and _rcF.for_copy({}, {"from": "main", "steps": 4})["steps"] == 4)

# ---- the disk queue: watching first, then a download, the copy, a check, the workers;
# ---- as many at once on a drive as it takes, first come first served within a kind
import pd_disk as _dq
check("disk: watching never waits", _dq.decide("G:", "watch", ["work"], [], ["download"], 1) == "")
check("disk: background work waits for a download on its drive, not on another",
      "download" in _dq.decide("G:", "work", [], [], ["download"], 1)
      and _dq.decide("D:", "work", [], [], [], 1) == "")
check("disk: a full drive makes the next wait; two turns let a second in",
      "1 of 1" in _dq.decide("G:", "work", ["work"], [], [], 1)
      and _dq.decide("G:", "work", ["work"], [], [], 2) == "")
_dq.HELD.clear(); _dq.ASKING.clear(); _dq.OUTSIDE.clear()
_h, _ = _dq.ask("G:\\x.mkv", "copy", "x", 1, "X")
_w, _ww = _dq.ask("G:\\w.mkv", "work", "w", 1, "W")
_k, _wk = _dq.ask("G:\\k.mkv", "check", "k", 1, "K")
_dq.done(_h)
_w2, _ww2 = _dq.ask("G:\\w.mkv", "work", "w", 1, "W")
_k2, _wk2 = _dq.ask("G:\\k.mkv", "check", "k", 1, "K")
check("disk: a check waiting goes before background work waiting longer: %s" % [_ww, _wk, _ww2, _wk2],
      _ww and _wk and "a check" in _ww2 and _wk2 == "")
_dq.HELD.clear(); _dq.ASKING.clear(); _dq.OUTSIDE.clear()
_a, _wa = _dq.ask("G:\\a.mkv", "work", "a", 1, "A")
_b, _wb = _dq.ask("G:\\b.mkv", "work", "b", 1, "B")
_c, _wc = _dq.ask("G:\\c.mkv", "work", "c", 1, "C")
_dq.done(_a)
_c2, _wc2 = _dq.ask("G:\\c.mkv", "work", "c", 1, "C")
_b2, _wb2 = _dq.ask("G:\\b.mkv", "work", "b", 1, "B")
check("disk: one at a time on a drive, and the one waiting longest goes next: %s"
      % [_wa, _wb, _wc, _wc2, _wb2], _wa == "" and _wb and _wc and _wc2 and _wb2 == "")
_dq.HELD.clear(); _dq.ASKING.clear()
_found = _dq.outside_from([{"save_path": "G:\\Movies", "state": "checkingUP", "dlspeed": 0},
                           {"save_path": "D:\\Download", "state": "downloading", "dlspeed": 9_000_000},
                           {"save_path": "D:\\Download", "state": "stalledDL", "dlspeed": 0}], {"F:"})
check("disk: qBittorrent's checks and downloads and the copy counted per drive: %s" % _found,
      _found == {"G:": ["check"], "D:": ["download"], "F:": ["copy"]})

# ---- every worker: what failed three times is passed over until Reset
import pd_workers as _pw, tempfile as _tfw
_pw.setup(_tfw.mkdtemp()); _pw.STATE["failed"] = None
_pw.failed("warmup", "a|3"); _pw.failed("warmup", "a|3")
_two = _pw.skip("warmup", "a|3")
_pw.failed("warmup", "a|3")
_three = _pw.skip("warmup", "a|3")
_pw.STATE["failed"] = None                       # read back from the file, as after a restart
_kept = _pw.skip("warmup", "a|3") and _pw.given_up("warmup") == 1
_n = _pw.reset("warmup")
check("workers: passed over after the third failure, kept over a restart, tried again after Reset",
      not _two and _three and _kept and _n == 1 and not _pw.skip("warmup", "a|3"))
_pw.failed("stills", "9"); _pw.worked("stills", "9")
check("workers: one that works is forgotten", _pw.given_up("stills") == 0 and not _pw.skip("stills", "9"))
import pd_health as _phK
_pK = _phK.problems({"machines": [], "disks": [{"drive": "G:", "freeGB": 41, "downloads": False},
                                               {"drive": "D:", "freeGB": 900, "downloads": True}]}, 0)
check("health: a library drive under 50 GB is said: %s" % _pK, any("G: has 41 GB free" in p for p in _pK))

# ---- a film's series in the order it came out; the ones here by their library key
import pd_streaming as _psS
_partsS = [{"id": 3, "title": "C", "release_date": "2020-05-01"}, {"id": 1, "title": "A", "release_date": "1999-01-01"},
           {"id": 2, "title": "B", "release_date": "2005-01-01"}, {"id": 9, "title": "None", "release_date": ""},
           {"id": 4, "title": "D", "release_date": "2099-01-01"}]
_gotS2 = _psS.saga_order(_partsS, {2: "libB"}, "2026-10-30")
check("series: in release order, the one here by its own key, none without a date, one to come marked: %s"
      % [(o["title"], o["key"][:4], o["coming"]) for o in _gotS2],
      [o["title"] for o in _gotS2] == ["A", "B", "C", "D"] and _gotS2[1]["key"] == "libB"
      and _gotS2[0]["key"].startswith("rt") and _gotS2[3]["coming"] and not _gotS2[2]["coming"])

# ---- every setting is placed: mirrored to the copy, a viewer's, or this machine's own
import pd_follow as _pfK, json as _jsK2, re as _reK2
_vkK = set(_reK2.findall(r'"([A-Za-z0-9]+)"', _reK2.search(r"VIEWER_KEYS = \(([\s\S]*?)\)\n", _srvL).group(1))) | {"quality"}
_liveK = os.path.join(os.environ.get("APPDATA", ""), "Palladium", "settings.json")
if os.path.exists(_liveK):
    _sK = _jsK2.load(open(_liveK, encoding="utf-8"))
    _uK = set()
    for _u in (_sK.get("users") or {}).values():
        _uK |= set(_u or {})
    _offK = _pfK.unplaced(set(_sK), _uK, _vkK)
    check("settings: every one placed - mirrored, a viewer's or this machine's own: %s" % (_offK,),
          _offK == ([], []))
check("settings: an unknown setting is caught", _pfK.unplaced({"port", "aNewOne"}, {"subLang", "x"}, _vkK) == (["aNewOne"], ["x"]))
check("settings: skip at start and the week's pick are copied", "skipStart" in _pfK.HOUSE_MIRRORED
      and "weekPick" in _pfK.HOUSE_MIRRORED)

# ---- the words cards show, decided on the server for both the page and the app
import pd_words as _pwW, datetime as _dtW
_tW = _dtW.date(2026, 10, 29)
check("words: the band - the date short, month and year far off, Upcoming without one to come",
      [_pwW.band(c, _tW) for c in ({"originallyAvailableAt": "2026-12-15"}, {"airDate": "2027-01-03"},
                                   {"airDate": "2027-10-01"}, {}, {"airDate": "2026-10-29"})]
      == ["15 Dec", "3 Jan", "Oct 2027", "Upcoming", "Upcoming"])
check("words: a Start slide plays only a film here; not out yet says when",
      [_pwW.act(c) for c in ({"type": "movie", "upcoming": True, "originallyAvailableAt": "2026-12-15"},
                             {"type": "movie", "askable": True}, {"type": "movie", "offered": True},
                             {"type": "movie"}, {"type": "show"})]
      == [("Out 15 Dec 2026", False), ("Request", False), ("\u2913 Get it", False), ("\u25b6 Play", True), ("\u25b6 Watch", False)])
check("words: a version held says On disk, wherever it came from",
      [_pwW.version_tag(r) for r in ({"disk": True}, {"pack": True, "have": True}, {"have": True},
                                     {"pack": True}, {"box": True, "seasonPack": True}, {})]
      == ["On disk", "On disk", "On disk", "In a pack here", "Season pack", "Tracker"])
_bodyW = _pwW.dress({"Hub": [{"Metadata": [{"type": "movie", "upcoming": True, "originallyAvailableAt": "2026-12-15"}]}],
                     "Metadata": [{"type": "movie"}]}, _tW)
check("words: every card of an answer dressed, in its hubs too",
      _bodyW["Hub"][0]["Metadata"][0]["band"] == "15 Dec" and _bodyW["Metadata"][0]["act"] == {"word": "\u25b6 Play", "plays": True})

# ---- the gate: a library number that fell in the live library too is not the build's fault
import importlib.util as _ilG
_specG = _ilG.spec_from_file_location("pd_gate_t", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-gate.py"))
_gG = _ilG.module_from_spec(_specG)
_specG.loader.exec_module(_gG)
check("gate: a fall the live library made too is excused, one it did not is kept",
      _gG.excused(["library.db .file fell from 3795 to 3784", "library.db .item fell from 10 to 5",
                   "tracker.json bytes fell from 9 to 1"],
                  {".file": 3795, ".item": 10}, {".file": 3784, ".item": 10})
      == ["library.db .item fell from 10 to 5", "tracker.json bytes fell from 9 to 1"])

# ---- receivers of other makers: each one's volume in the half-decibel steps counted here
import pd_receiver as _rcB
check("receiver: Denon/Marantz volume read and set, its MVMAX line passed over",
      _rcB.denon_volume("MVMAX 98\rMV505\r") == 101 and _rcB.denon_volume("MV50") == 100
      and _rcB.denon_volume("PWON") is None and _rcB.denon_set(101) == "MV505" and _rcB.denon_set(100) == "MV50")
_pk = _rcB.iscp_packet("MVLQSTN")
check("receiver: an eISCP packet and its answer for Onkyo and Pioneer",
      _pk[:4] == b"ISCP" and _pk[16:] == b"!1MVLQSTN\r" and int.from_bytes(_pk[8:12], "big") == 10
      and _rcB.iscp_volume(b"ISCP....!1MVL2A\x1a") == 84 and _rcB.iscp_volume("!1PWR01") is None)
check("receiver: Sony's volume and its most",
      _rcB.sony_volume({"result": [[{"volume": 25, "maxVolume": 50}]]}) == (50, 100))
check("receiver: an unknown make is a Yamaha; each make has its own ceiling",
      _rcB.settings({"receiver": {"brand": "nope"}})["brand"] == "yamaha"
      and _rcB.settings({"receiver": {"brand": "sony"}})["brand"] == "sony"
      and all(b in _rcB.CEILINGS for b, _ in _rcB.BRANDS))

# ---- health: a disk that reported faults in a day is named, with what kind
import pd_health as _phD
_dfD = _phD.disk_faults([(7, "The device, \\Device\\Harddisk3\\DR3, has a bad block."),
                         (7, "The device, \\Device\\Harddisk3\\DR3, has a bad block."),
                         (153, "The IO operation at logical block address 0x1 for Disk 2 (PDO name) was retried."),
                         (11, "The driver detected a controller error on \\Device\\Harddisk0\\DR0.")],
                        {3: "C:", 2: "G:"})
_pD = _phD.problems({"machines": [], "diskFaults": _dfD}, 0)
check("health: disk faults by drive, worst first, unknown kinds left out: %s / %s" % (_dfD, _pD),
      [(d["drive"], d["count"]) for d in _dfD] == [("C:", 2), ("G:", 1)]
      and any(p.startswith("C: reported 2 disk faults in a day (bad block)") for p in _pD))

# ---- OpenSubtitles' day of downloads: kept over a restart, and its end read off the refusal
import pd_subs as _psA, tempfile as _tfA, calendar as _calA, time as _tmA
_dA = _tfA.mkdtemp()
_psA.ALLOWANCE.update(left=None, at=0.0, back=0.0, file="")
_psA.keep_allowance(_dA)
_backA = _psA.refused_until("You have downloaded your allowed 100 subtitles for 24h.Your quota will be renewed in "
                            "06 hours and 02 minutes (2026-10-30 23:59:59 UTC) ts=1")
_psA._note_allowance(0, _backA)
_psA.ALLOWANCE.update(left=None, at=0.0, back=0.0)            # a restart
_psA.keep_allowance(_dA)
check("subtitles: the day's downloads used up are remembered over a restart, until they come back",
      _backA == _calA.timegm((2026, 10, 30, 23, 59, 59, 0, 0, 0)) and _psA.ALLOWANCE["left"] == 0
      and _psA.allowance_short(30, _backA - 60) and not _psA.allowance_short(30, _backA + 60))
_psA.ALLOWANCE.update(left=50, at=_tmA.time(), back=0.0)
check("subtitles: plenty left is not short; 30 or fewer is", not _psA.allowance_short(30)
      and (_psA.ALLOWANCE.update(left=30) or _psA.allowance_short(30)))
_psA.ALLOWANCE.update(left=20, at=_tmA.time(), back=_tmA.time() + 3600)
check("subtitles: in the last hours before the reset the reserve is spent too, but not past none",
      not _psA.allowance_short(30) and (_psA.ALLOWANCE.update(left=0) or _psA.allowance_short(30)))
_psA.ALLOWANCE.update(left=None, at=0.0, back=0.0, file="")
check("subtitles: the copy server's check fetches nothing",
      "        if self.mirror_of():\n            fetch = False" in _srvL)

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
check("and it says where it comes from as well", 'v.pick && !v.have ? "Recommended" : "", versionTag(v),' in _a21
      and 'v.pack ? "In a pack here"\n    : v.box ? (v.seasonPack ? "Season pack" : "Box set") : "Tracker";' in _a21)

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
      "if not place:\n                asking = self._catalogue_in_search(spelt, hubs) or asking" in _ls7
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
            {"id": 193, "name": "Quiet Street", "first_air_date": "2020-01-01",
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
      and "[r for r in named_rows if not pd_streaming.not_out(r)], self.lib, mine=True)" in _pla_src
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
      'FILMS_SHOW = {"disk": True, "download": True, "request": False,' in _fs117
      and 'FILMS_SHOW = {"disk": True, "download": True, "request": False,' in _fs117s
      and 'said?.optBoolean("request", false) ?: false' in _fs117k
      and "const fallback = { disk: true, download: true, request: false," in _fs117w)

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
check("a series' packs are its versions: the Version picker removes one added, its tracker list links one",
      "ShowPacksDialog" not in _kt121
      and 'if (versions && full.type == "show") {' in _kt121
      and "Api.removePack(full.title, hash)" in _kt121
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

# ---- the app's title page waits on the tracker, saying so
check("the app's title page says Checking and asks the tracker again while it is still asked",
      'Pill("Checking the tracker\u2026",' in _kt120
      and "while (versions.rows.isEmpty() && !full.onTracker && stillChecking) {" in _kt120
      and "stillChecking = false" not in _kt120)

# ---- a finished download leaves the film's page
check("a download that has finished is not shown on the film's page",
      "if (one.done) return@forEach" in _kt120 and 'Text("Done  ·  " + one.name' not in _kt120)

# ---- live TV waits for a channel's first playlist rather than giving up at 8 s
_lv133 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "LivePlayerActivity.kt"), encoding="utf-8").read()
check("the live player connects as the film player does: the known address, and time to start",
      ".setReadTimeoutMs(Route.LIVE_READ_MS.toInt())" in _lv133
      and "KnownHosts.reachable(spec.uri.toString())" in _lv133 and "DefaultMediaSourceFactory(named)" in _lv133)

# ---- a person's series not held carry their bands, as every series card does
_la134 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("a person's series not held go through with_sources: Checking, not Request, while the tracker is asked",
      "for c in self.with_sources([" in _la134 and "for r in pd_streaming.show_credit_rows(self.lib, tv)]):" in _la134)

# ---- a long list's series are asked of the tracker behind, slowly, by name and complete
import pd_tracker as _pt135
_asked135, _was135 = [], (_pt135.state, _pt135.asked_within, _pt135.ask_for)
_pt135.state = lambda: {"asks": True}
_pt135.asked_within = lambda t, y=0, s=0: t == "Seen Lately"
_pt135.ask_for = lambda t, y=0, again_after=0: _asked135.append(t) or []
try:
    _go135 = st.ask_tracker_slowly_about_shows(["Show %d" % n for n in range(5)] + ["Seen Lately", "Show 0"], gap=0.0)
    for _n135 in range(100):
        if not st.ASKING_SHOWS_SLOWLY["busy"]:
            break
        time.sleep(0.05)
    check("a long list's series are each asked by name and as a complete run, behind, once",
          _go135 and len(_asked135) == 10 and "Show 3 complete" in _asked135
          and "Seen Lately" not in _asked135 and st.SHOW_GAP >= 20)
finally:
    _pt135.state, _pt135.asked_within, _pt135.ask_for = _was135
_srv135 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("a series' page asked about before answers at once and asks again behind",
      "if pd_tracker.asked_within(str(title), 0, _known.CHECKED_FOR):" in _srv135
      and "pd_streaming.ask_tracker_slowly_about_shows(ask[10:])" in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read())

# ---- a series named for every word searched shows, however few have rated it
class _Tv136:
    def tmdb(self, path, **kw):
        page = int(kw.get("page") or 1)
        rows = ([{"id": 9101, "name": "Placer Run", "first_air_date": "2010-12-03", "poster_path": "/a.jpg", "vote_count": 195},
                 {"id": 9102, "name": "Placer Run: Far South", "first_air_date": "2013-01-01", "poster_path": "/b.jpg", "vote_count": 2},
                 {"id": 9103, "name": "Something Else Entirely", "first_air_date": "2015-01-01", "poster_path": "/c.jpg", "vote_count": 1}]
                if page == 1 else
                [{"id": 9104, "name": "Placer Run: Legends", "first_air_date": "2016-01-01", "poster_path": "/d.jpg", "vote_count": 1}])
        return {"results": rows, "total_pages": 2}
    def db(self):
        import sqlite3
        c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE item (id TEXT, title TEXT, year INT, tmdb_id INT, type TEXT)")
        return c
st.FOUND_BY_NAME.pop("tv|placer run", None)
_names136 = [r["show"] for r in st.shows_by_name(_Tv136(), "placer run")]
check("a search finds every series named for its words, rated or not, over two pages",
      _names136 == ["Placer Run", "Placer Run: Far South", "Placer Run: Legends"])

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
_ml = _srvm[_srvm.index("def measure_loudness("):]
_ml = _ml[:_ml.index(chr(10) + "def ")]
check("loudness: under a download only the file on a screen is measured, not the about-to-be-watched",
      "if worker_waits(for_screen=playing, other_drive=free_drive):" in _ml
      and _ml.index("worker_waits(") < _ml.index("threading.Thread("))

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
      _srvt.index('raise LookupError("an episode, or a page already known")', _ti) <
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
          {"name": "Formula1 2025 Round9 Old Race F1TV WEB-DL 1080p", "at": _now - 40 * 86400}])
_ev = _spt.events(_rows, now=_now)
check("one card a race whatever its versions, sixteen games at most, a year back by the race",
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
      'mode not in ("auto", "files", "database", "doubles",' in _ps58 and '"titles"):' in _ps58)

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

# ---- double episodes in a pack: the next one after "E04E05", and names on the catalogue's numbers
import pd_torrents as _pt351
def _ep351(s, e, path):
    return {"kind": "episode", "season": s, "episode": e, "title": "Show", "name": "Show", "path": path}
_films351 = [_ep351(6, 3, "P/S06/Show.S06E03.mkv"), _ep351(6, 4, "P/S06/Show.S06E04E05.mkv"),
             _ep351(6, 6, "P/S06/Show.S06E06.mkv"), _ep351(7, 1, "P/S07/Show.S07E01.mkv")]
_was351 = _pt351.load
try:
    _pt351.load = lambda: {"packs": [{"films": _films351}], "downloads": [], "config": {}}
    check("next episode: after a double episode comes the next number the season has, not the next season",
          _pt351.episode_after("Show", 6, 4) is _films351[2])
    check("next episode: after the season's last, the first of the next",
          _pt351.episode_after("Show", 6, 6) is _films351[3])
    check("pack: a season with a double episode file is known as one",
          _pt351._season_has_double(_films351[2]) and not _pt351._season_has_double(_films351[3]))
finally:
    _pt351.load = _was351
import pd_library as _pl351
_cat351 = [{"episode_number": n, "runtime": 44 if n == 4 else 22} for n in range(1, 8)]
check("pack names: the release's E06 after a double E04E05 is the catalogue's E05",
      _pl351.doubles_map(_cat351).get(6) == 5 and _pl351.doubles_map(_cat351).get(4) == 4)

# ---- downloads one at a time: a stuck one does not start the next; the owner moves the top one
import pd_torrents as _pt350
_st350 = dict(_pt350.STATE)
_a350 = {"key": "a", "hash": "h1", "state": "downloading", "qb": "stalledDL", "started": 1}
_b350, _c350 = {"key": "b", "hash": "h2", "state": "queued"}, {"key": "c", "hash": "h3", "state": "queued"}
_rows350 = [_a350, _b350, _c350]
_data350 = {"config": {}, "downloads": _rows350, "packs": []}
_was350 = (_pt350.load, _pt350.save, _pt350.by_key, _pt350.refused, _pt350._begin, _pt350._back_to_waiting)
_begun350, _off350 = [], []
try:
    _pt350.load, _pt350.save = (lambda: _data350), (lambda: None)
    _pt350.by_key = lambda k: ({"hash": "x"}, {"key": k})
    _pt350.refused = lambda p: ""
    _pt350._begin = lambda row, p, f: _begun350.append(row["key"])
    _pt350._back_to_waiting = lambda hs: _off350.extend(hs)
    _pt350._start_next()
    check("queue: one at a time - a stuck download does not start the next", _begun350 == []
          and _b350["state"] == "queued")
    # the owner moves the one coming in below a waiting one
    _off350.clear()
    _pt350.reorder(["c", "a"])
    check("queue: the owner moves the one coming in down, the one above it starts",
          _a350["state"] == "queued" and _off350 == ["h1"] and _begun350[-1] == "c"
          and _c350["state"] == "downloading")
    _a350["state"], _c350["state"] = "downloading", "queued"
    _off350.clear()
    _pt350.reorder(["c", "a"], token="guest")
    check("queue: a guest never moves the one coming in", _a350["state"] == "downloading" and not _off350)
finally:
    (_pt350.load, _pt350.save, _pt350.by_key, _pt350.refused, _pt350._begin, _pt350._back_to_waiting) = _was350
    _pt350.STATE.clear()
    _pt350.STATE.update(_st350)

# ---- the copy names only what it fetched itself for the main server to take back
import pd_torrents as _pt352
check("fetched: a film moved here, found whole or taken back is not named as fetched",
      not any(_pt352.fetched_here({"reason": r}) for r in (
          "moved here: the pack's home", "whole on the disk", "finishing a copy that was cut short",
          "taken back from the copy")))
class _QB352:
    def __init__(self, cfg):
        pass

    def files(self, h):
        return [{"index": 1, "name": "P/a.mkv"}, {"index": 2, "name": "P/b.mkv"}]
_was352 = (_pt352.load, _pt352.QB)
try:
    _pt352.load = lambda: {"config": {}, "downloads": [
        {"hash": "h", "index": 1, "state": "done", "reason": "moved here: the pack's home"},
        {"hash": "h", "index": 2, "state": "done", "reason": ""}]}
    _pt352.QB = _QB352
    check("fetched: the copy names to the main server only the film it fetched, not the one moved to it",
          [d["index"] for d in _pt352.my_own_downloads()] == [2])
finally:
    (_pt352.load, _pt352.QB) = _was352
check("fetched: one asked for, drawn in a shuffle or fetched ahead is",
      all(_pt352.fetched_here({"reason": r}) for r in ("", "drawn in a shuffle", "episodes ahead of where somebody is")))

# ---- pack reads give way to a viewer short of disk
import pd_torrents as _pt353
_rt = _pt353.read_turns
check("reads: a read on the drive a viewer short of disk watches from is paused, one elsewhere is not",
      _rt({"a": "G:", "b": "F:"}, {"G:"}, True, {}, 0.0, 100)[0] == ["a"])
check("reads: a paused read waits for calm before it starts again",
      _rt({}, {"G:"}, False, {"a": "G:"}, 0.0, 100)[1] == []
      and _rt({}, {"G:"}, False, {"a": "G:"}, 100.0, 100 + _pt353.CALM_FOR)[1] == ["a"])
check("reads: a paused read starts at once when nobody watches from its drive",
      _rt({}, set(), False, {"a": "G:"}, 0.0, 100)[1] == ["a"])
check("reads: short again resets the calm", _rt({}, {"G:"}, True, {"a": "G:"}, 50.0, 100)[2] == 0.0)
_g353 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
check("reads: the pack guard never starts a read paused for a viewer",
      'pack["hash"] not in STATE.get("yielding", {})' in _g353[_g353.index("def _guard_packs"):_g353.index("def _guard_packs") + 1500])

# ---- a check waiting for another pack's check is said to be waiting, not checking
import pd_torrents as _pt354
_pt354.STATE["checkSeen"] = {}
check("checks: a moving check says checking, one still for a minute says waiting",
      _pt354.check_state("h", 0.10, 0) == "checkingDL" and _pt354.check_state("h", 0.10, 30) == "checkingDL"
      and _pt354.check_state("h", 0.10, 0 + _pt354.CHECK_STILL) == "checkWaiting"
      and _pt354.check_state("h", 0.11, 70) == "checkingDL")
check("checks: the words for one waiting", _pt354.stage_words("downloading", "checkWaiting", 0.6)[1]
      == "Waiting for another pack's check")
_pt354.STATE["checkSeen"] = {}

# ---- the download log answers fast with many rows and big packs
import pd_torrents as _pt356, time as _tm356
_was356 = _pt356.load
try:
    _films356 = [{"key": "k%d" % n, "kind": "movie"} for n in range(14000)]
    _rows356 = [{"key": "k%d" % (13999 - n), "state": "done"} for n in range(300)]
    _calls356 = []
    # every load() waits for the lock the workers hold while they save: counted, not timed
    _pt356.load = lambda: (_calls356.append(1), {"packs": [{"films": _films356}],
                                                 "downloads": _rows356, "config": {}})[1]
    _n356 = len(_pt356.downloads())
    check("download log: the state is taken once, not once a row - each take waits on the workers' lock",
          _n356 == 300 and len(_calls356) == 1)
finally:
    _pt356.load = _was356

# ---- double-episode numbering: a catalogue episode no file was numbered as gets a row
import sqlite3 as _sq357, pd_library as _pl357
_c357 = _sq357.connect(":memory:"); _c357.row_factory = _sq357.Row
_c357.executescript("CREATE TABLE item (id TEXT, tmdb_id INT); CREATE TABLE episode (id TEXT PRIMARY KEY, item_id TEXT, season INT, number INT, title TEXT, overview TEXT, aired TEXT, still TEXT, UNIQUE(item_id, season, number));"
                    "CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT, episode_id TEXT);"
                    "CREATE TABLE progress (who TEXT, key TEXT, position REAL, PRIMARY KEY (who, key));")
# a viewer watched the file numbered E06 through, and is part way into E07
_c357.execute("INSERT INTO progress VALUES ('v', 'e6', 1300), ('v', 'e7', 200)")
_c357.execute("INSERT INTO item VALUES ('show', 1)")
for _n357, _f357 in ((3, "S.S06E03.mp4"), (4, "S.S06E04E05.mp4"), (6, "S.S06E06.mp4"), (7, "S.S06E07.mp4")):
    _c357.execute("INSERT INTO episode (id, item_id, season, number) VALUES (?, 'show', 6, ?)", ("e%d" % _n357, _n357))
    _c357.execute("INSERT INTO file (path, episode_id) VALUES (?, ?)", (_f357, "e%d" % _n357))
class _Lib357:
    def tmdb(self, path):
        return {"episodes": [{"episode_number": n, "runtime": 44 if n == 4 else 22, "name": "C%d" % n} for n in range(1, 8)]}
_pl357.Library._renumber_doubles(_Lib357(), _c357, "show", 6)
_on357 = {r[0]: r[1] for r in _c357.execute("SELECT f.path, e.number FROM file f JOIN episode e ON e.id = f.episode_id")}
check("double numbering: the file after a double episode goes onto the catalogue's episode, made if missing",
      _on357 == {"S.S06E03.mp4": 3, "S.S06E04E05.mp4": 4, "S.S06E06.mp4": 5, "S.S06E07.mp4": 6})
_pr357 = {r[0]: r[1] for r in _c357.execute(
    "SELECT e.number, p.position FROM progress p JOIN episode e ON e.id = p.key")}
check("double numbering: a viewer's place follows the file to its new episode",
      _pr357 == {5: 1300, 6: 200})

# ---- a verify made by a check marks the file and leaves every viewer's choice alone
_sv358 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_vf358 = _sv358[_sv358.index("    def verify_subtitle"):]
_vf358 = _vf358[:_vf358.index(chr(10) + "    def ", 10)]
check("subtitle verify: only a person's own verify writes their series choice and pick",
      "mine = self.viewer_settings(stored) if not how else {}" in _vf358
      and "if show and not how:" in _vf358 and "if name and index is None and not how:" in _vf358
      and 'how="check" if body.get("check") else ""' in _sv358)

# ---- the next episode's file read ahead at both ends, served from memory
import ast as _ast359, tempfile as _tf359, threading as _th359, time as _tm359
_sv359 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns359 = {"os": os, "threading": _th359, "time": _tm359}
_pa359 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                           "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
for _n359 in _ast359.parse(_sv359).body:
    if (isinstance(_n359, _ast359.FunctionDef) and _n359.name in ("warm_file", "warm_block")) or (
            isinstance(_n359, _ast359.Assign) and getattr(_n359.targets[0], "id", "") in
            ("WARM", "WARM_LOCK", "WARM_EDGE", "WARM_FILES", "WARM_FOR")):
        exec(_ast359.get_source_segment(_sv359, _n359), _ns359)
_ns359["WARM_EDGE"] = 1000
_d359 = _tf359.mkdtemp()
_f359 = os.path.join(_d359, "ep.mp4")
_b359 = bytes(range(256)) * 20
open(_f359, "wb").write(_b359)
_ns359["warm_file"](_f359)
_wb359 = _ns359["warm_block"]
check("warm: the start and the end of the next file are served from memory, byte for byte",
      _wb359(_f359, 0, 500) == _b359[:500] and _wb359(_f359, len(_b359) - 300, 300) == _b359[-300:])
check("warm: the middle is not held, and a read running past the start part gets what it holds",
      _wb359(_f359, 2500, 100) is None and _wb359(_f359, 900, 400) == _b359[900:1000])
for _i359 in range(4):
    _g359 = os.path.join(_d359, "e%d.mp4" % _i359); open(_g359, "wb").write(_b359); _ns359["warm_file"](_g359)
check("warm: only the newest few files are kept", len(_ns359["WARM"]) == 3 and _wb359(_f359, 0, 10) is None)
check("warm: the sender asks the store before the disk, and the app asks ahead and names the next at once",
      "chunk = warm_block(path, at, want)" in _sv359
      and "Api.warm(it)" in _pa359 and "nameBar?.text = listOf(next.title, next.subtitle)" in _pa359)

# ---- a version named by its folder when the file says nothing: two copies of one episode told apart
import pd_library as _pl360
check("edition: a plainly named episode in a Superfan pack is the Superfan version",
      _pl360.edition_of(os.path.join("G:", os.sep, "TV", "Some Show 2005 S04 Superfan 1080p WebRip",
                                     "Some.Show.S04E15.Some.Name.mp4")) == "Superfan")
check("edition: the file's own name still wins, and a plain one in a plain folder says nothing",
      _pl360.edition_of(os.path.join("G:", os.sep, "TV", "Show S04 Superfan", "Show.S04E01.Extended.Cut.mkv")) == "Extended"
      and _pl360.edition_of(os.path.join("G:", os.sep, "TV", "Show.S04.1080p.BluRay-GRP", "Show.S04E01.1080p-GRP.mkv")) == "")

# ---- official order: each file on its broadcast number, named by the catalogue episode it is
import sqlite3 as _sq361, pd_library as _pl361
_c361 = _sq361.connect(":memory:"); _c361.row_factory = _sq361.Row
_c361.executescript("CREATE TABLE item (id TEXT, tmdb_id INT); CREATE TABLE episode (id TEXT PRIMARY KEY, item_id TEXT, season INT, number INT, title TEXT, overview TEXT, aired TEXT, still TEXT, UNIQUE(item_id, season, number));"
                    "CREATE TABLE file (id INTEGER PRIMARY KEY, path TEXT, episode_id TEXT);"
                    "CREATE TABLE progress (who TEXT, key TEXT, position REAL, PRIMARY KEY (who, key));")
_c361.execute("INSERT INTO item VALUES ('show', 1)")
# as the catalogue numbering left it: the double E04E05 on 4, E06 on 5 "Mafia", E07 on 6
for _n361, _t361, _f361 in ((4, "Niagara", "S.S06E04E05.mp4"), (5, "Mafia", "S.S06E06.mp4"), (6, "The Lover", "S.S06E07.mp4")):
    _c361.execute("INSERT INTO episode (id, item_id, season, number, title) VALUES (?, 'show', 6, ?, ?)", ("e%d" % _n361, _n361, _t361))
    _c361.execute("INSERT INTO file (path, episode_id) VALUES (?, ?)", (_f361, "e%d" % _n361))
_c361.execute("INSERT INTO progress VALUES ('v', 'e5', 1300)")
class _Lib361:
    def tmdb(self, path):
        return {"episodes": [{"episode_number": n, "runtime": 44 if n == 4 else 22, "name": t}
                             for n, t in ((1, "a"), (2, "b"), (3, "c"), (4, "Niagara"), (5, "Mafia"), (6, "The Lover"), (7, "Koi Pond"))]}
_pl361.Library._renumber_official(_Lib361(), _c361, "show", 6)
_on361 = {r[0]: (r[1], r[2]) for r in _c361.execute("SELECT f.path, e.number, e.title FROM file f JOIN episode e ON e.id = f.episode_id")}
check("official order: files on their broadcast numbers, named by the catalogue episode they are",
      _on361 == {"S.S06E04E05.mp4": (4, "Niagara"), "S.S06E06.mp4": (6, "Mafia"), "S.S06E07.mp4": (7, "The Lover")})
_pr361 = {r[0]: r[1] for r in _c361.execute("SELECT e.number, p.position FROM progress p JOIN episode e ON e.id = p.key")}
check("official order: a viewer's place follows the file", _pr361 == {6: 1300})

# ---- an extended cut is the standard copy of a title that has one
import pd_localapi as _la362
_rows362 = [{"id": 1, "path": os.path.join("G:", os.sep, "TV", "Show.S04.1080p.BluRay-RBG", "Show.S04E15.mp4"),
             "size": 400, "height": 1080, "bitrate": 9, "duration": 1300},
            {"id": 2, "path": os.path.join("G:", os.sep, "TV", "Some Show S04 Superfan 1080p", "Show.S04E15.Some.Name.mp4"),
             "size": 300, "height": 1080, "bitrate": 5, "duration": 1500}]
check("versions: the Superfan cut comes first, ahead of a bigger broadcast copy",
      [r["id"] for r in _la362.best_first(_rows362)] == [2, 1])

# ---- a series' version is the pack it draws from, picked by hand or not
_api363 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main", "java",
                            "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
check("series version: with none picked by hand, the packs in use by themselves are named",
      "(chosen.ifEmpty { auto }) to rows" in _api363 and 'said.optJSONArray("auto")' in _api363)

# ---- an added pack is known on the tracker's list by its group and seasons
import ast as _ast364
_sv364 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns364 = {"re": __import__("re")}
for _n364 in _ast364.parse(_sv364).body:
    if isinstance(_n364, _ast364.FunctionDef) and _n364.name == "release_group":
        exec(_ast364.get_source_segment(_sv364, _n364), _ns364)
_rg364 = _ns364["release_group"]
check("release group: the same group however the name is written, and a season run is none",
      _rg364("24 S01-S09 + Redemption br 10bit ddp hevc-d3g") == "d3g"
      and _rg364("24 S01-S09 + Redemption Complete 1080p BluRay 10Bit DDP5 1 HEVC-d3g") == "d3g"
      and _rg364("24 S01-S09") == "" and _rg364("Show S01-S05 1080p-GRP)") == "grp")

# ---- a series of two letters is searched in the catalogue: "24"
import pd_streaming as _ps365
class _Lib365:
    def tmdb(self, path, **kw):
        return {"results": [{"id": 1973, "name": "24", "first_air_date": "2001-11-06",
                             "poster_path": "/p.jpg", "vote_count": 3000}], "total_pages": 1}
    def db(self):
        import sqlite3
        c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE item (id TEXT, title TEXT, year INT, tmdb_id INT, type TEXT)")
        return c
_ps365.FOUND_BY_NAME.pop("tv|24", None)
check("series search: two letters are searched for a series, and still not for a film",
      [r["show"] for r in _ps365.shows_by_name(_Lib365(), "24")] == ["24"]
      and _ps365.by_name(_Lib365(), "24") == [])
import pd_localapi as _pla365
class _Api365:
    def __init__(self): self.asked = []
    def _catalogue_shows_in_search(self, spelt, hubs): self.asked.append("series")
    def _collection_in_search(self, spelt, hubs): self.asked.append("collection"); return False
_a365, _b365 = _Api365(), _Api365()
_pla365.LocalAPI._catalogue_in_search(_a365, "24", [])
_pla365.LocalAPI._catalogue_in_search(_b365, "lost", [])
check("series search: two letters ask the catalogue for series, not for a collection",
      _a365.asked == ["series"] and _b365.asked == ["series", "collection"])
_ps365.FOUND_BY_NAME.pop("tv|24", None)

# ---- a series held and on offer is one page: the library's
import sqlite3 as _sq366, pd_localapi as _pla366, pd_torrents as _pt366
_c366 = _sq366.connect(":memory:"); _c366.row_factory = _sq366.Row
_c366.execute("CREATE TABLE item (id TEXT, title TEXT, type TEXT)")
_c366.execute("INSERT INTO item VALUES ('aaa', 'Held Show', 'show'), ('bbb', 'A Film', 'movie')")
_h366 = lambda k: _pla366.LocalAPI._held_series(None, _c366, k)
check("one series: an offer of a series the library holds opens the library's page",
      _h366(_pt366.show_key("Held Show")) == "aaa")
check("one series: an offer nothing here holds stays the offer, and a film is not a series",
      _h366(_pt366.show_key("Not Here")) is None and _h366(_pt366.show_key("A Film")) is None
      and _h366("aaa") is None)
class _Api366:
    _held_series = _pla366.LocalAPI._held_series
_k366 = _pt366.show_key("Held Show")
check("one series: an offered season opens the library's season, and any other key stays",
      _pla366.LocalAPI._held_key(_Api366(), _c366, _k366 + "-s2") == "aaa-s2"
      and _pla366.LocalAPI._held_key(_Api366(), _c366, "aaa-s2") == "aaa-s2")

# ---- a series on offer has the library's series page, not a page of its own
class _Api367:
    def _cast_of(self, key, kind, tmdb): return ["someone"]
_p367 = _pla366.LocalAPI._offer_page
_show367 = {"type": "show", "title": "A Show", "offered": True, "offer": {"episodes": 3}}
check("offer page: a series on offer opens as a library series, without the offered marks",
      _p367(_Api367(), "os1", dict(_show367)) == {"type": "show", "title": "A Show"})
check("offer page: its card stays as it was, ribbon and all", _show367["offered"] is True)
check("offer page: a film stays an offer, with its cast",
      _p367(_Api367(), "o1", {"type": "movie", "offered": True}) == {"type": "movie", "offered": True, "Role": ["someone"]})
check("offer page: nothing on offer is nothing", _p367(_Api367(), "o1", None) is None)

# ---- cross-seed worker: same release only, nothing renamed, linked, copied or deleted
import pd_xseed as _xs, tempfile as _tf368, json as _js368, shutil as _sh368
_d368 = _tf368.mkdtemp()
_rel368 = os.path.join(_d368, "Some.Film.2020.1080p-GRP")
os.makedirs(_rel368)
_vid368 = os.path.join(_rel368, "Some.Film.2020.1080p-GRP.mkv")
with open(_vid368, "wb") as _f:
    _f.write(b"x" * 1000)
_before368 = sorted(os.listdir(_rel368))
_cand368 = _xs.candidates([(_vid368, 1000, 5),
                           (os.path.join(_d368, "Show", "S01", "Show.S01E01-GRP.mkv"), 10, 9),
                           (os.path.join(_d368, "Pack.Name", "Film.mkv"), 10, 9),
                           (os.path.join(_d368, "Seed", "race", "Race.mkv"), 10, 9),
                           (os.path.join(_d368, "x.nfo"), 10, 9)],
                          _xs.skipper({"packs": [{"name": "Pack.Name"}]}, ""))
check("cross-seed: a release folder is one name, a season folder counts as its show, and a pack's film, a race and a non-video are not asked about",
      [c["name"] for c in _cand368] == ["Show", "Some.Film.2020.1080p-GRP", "Show.S01E01-GRP"])
check("cross-seed: copies are never seeded from: the machine can clear them",
      _xs.skipper({}, os.path.join("D:", os.sep, "Palladium"))(os.path.join("D:", os.sep, "Palladium", "a.mkv")))
_w368 = {"name": "Some.Film.2020.1080p-GRP", "size": 1000 * 10**6, "local": _rel368}
check("cross-seed: the tracker's spaces are the same name, a near size is the same release",
      _xs.same_release({"name": "Some Film 2020 1080p-GRP", "size": 1010 * 10**6}, _w368))
check("cross-seed: another group or a far size is not",
      not _xs.same_release({"name": "Some Film 2020 1080p-OTHER", "size": 1000 * 10**6}, _w368)
      and not _xs.same_release({"name": "Some Film 2020 1080p-GRP", "size": 9000 * 10**6}, _w368))
# as pd_torrents.parse_torrent gives them: below the torrent's own folder, not naming it
_tf = [{"path": "Some.Film.2020.1080p-GRP.mkv", "size": 1000}, {"path": "info.nfo", "size": 5}]
_n368 = "Some.Film.2020.1080p-GRP"
check("cross-seed: fits where every video is at that exact name and size",
      _xs.place(_rel368, _tf, _n368) == (_d368, "Original"))
_rd368 = _tf368.mkdtemp()
os.makedirs(os.path.join(_rd368, "Other.Folder", "Sample"))
with open(os.path.join(_rd368, "Other.Folder", "Sample", "s.mkv"), "wb") as _f:
    _f.write(b"x" * 7)
with open(os.path.join(_rd368, "Other.Folder", "a.mkv"), "wb") as _f:
    _f.write(b"x" * 9)
_sub368 = [{"path": "Sample/s.mkv", "size": 7}, {"path": "a.mkv", "size": 9}]
check("cross-seed: a folder of another name fits without the torrent's, its own subfolders kept",
      _xs.place(os.path.join(_rd368, "Other.Folder"), _sub368, "Rel") == (os.path.join(_rd368, "Other.Folder"), "NoSubfolder"))
with open(os.path.join(_rd368, "Rel.mkv"), "wb") as _f:
    _f.write(b"x" * 3)
check("cross-seed: one file beside others fits one file's torrent",
      _xs.place(os.path.join(_rd368, "Rel.mkv"), [{"path": "Rel.mkv", "size": 3}], "Rel.mkv") == (_rd368, "Original"))
check("cross-seed: a lone file with a torrent of a folder is not Original, whose path does not exist",
      _xs.place(os.path.join(_rd368, "Rel.mkv"), [{"path": "Rel.mkv", "size": 3}, {"path": "Rel.nfo", "size": 1}], "Rel")
      == (_rd368, "NoSubfolder"))
_sh368.rmtree(_rd368, ignore_errors=True)
_e368 = [{"priority": 1, "piece_range": [0, 3]}, {"priority": 0, "piece_range": [3, 5]}]
check("cross-seed: short only of a piece shared with a file switched off is the edge",
      _xs.edges_only(_e368, [2, 2, 2, 0, 0, 0]))
check("cross-seed: short of a piece of its own is another file, however few",
      not _xs.edges_only(_e368, [2, 0, 2, 2, 0, 0]))
check("cross-seed: near whole and another file is a mismatch, nothing fetched over it",
      _xs.verdict([dict(_e368[0], progress=0.99, size=10**9), dict(_e368[1], progress=0, size=5)], None, [2, 0, 2, 2, 0, 0]) == "mismatch"
      and _xs.verdict([dict(_e368[0], progress=0.99, size=10**9), dict(_e368[1], progress=0, size=5)], None, [2, 2, 2, 0, 0, 0]) == "edge")
check("cross-seed: another size or another file name does not fit, and nothing is renamed to make it",
      _xs.place(_rel368, [dict(_tf[0], size=999)], _n368) is None
      and _xs.place(_rel368, [{"path": "renamed.mkv", "size": 1000}], _n368) is None
      and sorted(os.listdir(_rel368)) == _before368)
_G368 = 2 * 10**9
check("cross-seed: whole is ready, a few MB short is an edge, more is another file, nothing read is once more",
      _xs.verdict([{"progress": 1, "size": _G368}], None) == "ready"
      and _xs.verdict([{"progress": 0.99, "size": _G368}], None) == "edge"
      and _xs.verdict([{"progress": 0.9, "size": _G368}], None) == "mismatch"
      and _xs.verdict([{"progress": 0, "size": _G368}], None) == "again"
      and _xs.verdict([{"progress": 0, "size": _G368}], 1) == "mismatch")
_r368 = {"a": {"state": "added", "root": "G:\\x", "seeds": 9}, "b": {"state": "added", "root": "G:\\y", "seeds": 1},
         "c": {"state": "added", "root": "F:\\y", "seeds": 5}, "d": {"state": "added", "root": "D:\\y", "seeds": 5},
         "e": {"state": "seeding", "root": "E:\\y", "seeds": 0}}
check("cross-seed: one check a drive, fewest seeds first, none on a drive already reading",
      _xs.next_checks(_r368, {"D:"}) == {"G:": "b", "F:": "c"})
check("cross-seed: never asked is due, asked is not for a month, an error is again next day",
      _xs.due({}, {"local": "a"}, 100)
      and not _xs.due({"a": {"at": 100}}, {"local": "a"}, 100 + 86400)
      and _xs.due({"a": {"at": 100}}, {"local": "a"}, 100 + _xs.ASK_AGAIN)
      and _xs.due({"a": {"at": 100, "error": "x"}}, {"local": "a"}, 100 + _xs.ERROR_AGAIN))
# the whole way round on a real folder, with a client and a tracker made up
class _QB368:
    def __init__(self, h):
        self.h, self.rows, self.prio, self.removed, self.started, self.checked = h, {}, [], [], [], []
        self.progress, self.size = 1, 1000
    def info(self, h): return self.rows.get(h)
    def all_info(self): return list(self.rows.values())
    def add_at(self, raw, root, cat, layout="Original"):
        self.rows[self.h] = {"hash": self.h, "state": "stoppedDL", "save_path": root, "category": cat}
    def files(self, h):
        return [{"index": 0, "name": _n368 + "/" + _tf[0]["path"], "size": self.size, "progress": self.progress, "priority": 1},
                {"index": 1, "name": _n368 + "/" + _tf[1]["path"], "size": 5, "progress": 0, "priority": 0 if self.prio else 1}]
    def priority(self, h, idx, v): self.prio.append((list(idx), v))
    def pieces(self, h): return None
    def recheck(self, h): self.checked.append(h); self.rows[h]["state"] = "checkingUP"
    def start(self, h): self.started.append(h)
    def remove(self, h, files=False): self.removed.append(files); self.rows.pop(h, None)
    def _call(self, *a, **k): return b""
_pt368 = _xs.pd_torrents.parse_torrent
_xs.pd_torrents.parse_torrent = lambda raw: ("ABC", _n368, _tf)
_was368 = dict(_xs.STATE)
try:
    _xs.STATE.update(root=_d368, paper={"asked": {}, "torrents": {}}, wait=lambda d: "", log=None)
    _q368 = _QB368("abc")
    # the library's own info.nfo, of another size than the upload's: never fetched over it
    with open(os.path.join(_rel368, "info.nfo"), "wb") as _f:
        _f.write(b"yyy")
    _xs._add(_q368, b"raw", dict(_w368, size=1000), {"name": "Some Film", "seeds": 3}, 100)
    os.remove(os.path.join(_rel368, "info.nfo"))
    _row368 = _xs.paper()["torrents"]["abc"]
    check("cross-seed: added stopped over the files, what is not there at that size turned off, not started",
          _row368["state"] == "added" and _row368["root"] == _d368 and _q368.prio == [([1], 0)]
          and not _q368.started and not _q368.checked)
    with open(os.path.join(_d368, "xseed.json"), encoding="utf-8") as _f:
        check("cross-seed: on paper before it goes in, so a restart cannot lose it", "abc" in _js368.load(_f)["torrents"])
    _xs._add(_q368, b"raw", dict(_w368, size=1000), {"name": "Some Film"}, 100)
    check("cross-seed: one already in the client is left as it is", _xs.paper()["torrents"]["abc"] is _row368)
    _xs.start_checks(_q368, {"abc": _q368.rows["abc"]}, 100)
    check("cross-seed: a drive free takes its check", _q368.checked == ["abc"] and _row368["state"] == "checking")
    _xs.judge(_q368, {"abc": _q368.rows["abc"]}, 200)
    check("cross-seed: not judged while it reads", _row368["state"] == "checking")
    _q368.rows["abc"]["state"] = "stoppedUP"
    _xs.judge(_q368, {"abc": _q368.rows["abc"]}, 200)
    check("cross-seed: passed, it seeds", _row368["state"] == "seeding" and _q368.started == ["abc"])
    _row368.update(state="checking", checkAt=100)
    _q368.progress, _q368.started, _q368.size = 0.5, [], _G368
    _xs.judge(_q368, {"abc": _q368.rows["abc"]}, 200)
    check("cross-seed: failed, it is taken out with its files kept, and never started",
          _row368["state"] == "mismatch" and _q368.removed == [False] and not _q368.started)
    _xs._add(_QB368("abc"), b"raw", dict(_w368, size=1000), {"name": "Some Film"}, 300)
    check("cross-seed: a mismatch is not read a second time", _xs.paper()["torrents"]["abc"]["state"] == "mismatch")
    check("cross-seed: the files are as they were", sorted(os.listdir(_rel368)) == _before368
          and os.path.getsize(_vid368) == 1000)
    with open(os.path.join(_d368, "xseed.json"), "w", encoding="utf-8") as _f:
        _f.write("{not json")
    _xs.STATE["paper"] = None
    try:
        _xs.paper()
        _bad368 = False
    except ValueError:
        _bad368 = True
    check("cross-seed: a state file that cannot be read stops it, and is not started empty",
          _bad368 and open(os.path.join(_d368, "xseed.json"), encoding="utf-8").read() == "{not json")
finally:
    _xs.pd_torrents.parse_torrent = _pt368
    _xs.STATE.clear()
    _xs.STATE.update(_was368)
_src368 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_xseed.py"), encoding="utf-8").read()
check("cross-seed: the source has no link, rename, copy or delete of a file, and removes torrents with files kept",
      not any(w in _src368 for w in ("os.link", "os.rename", "os.remove", "os.unlink", "shutil", "files=True", "symlink"))
      and _src368.count("os.replace(") == 1 and "qb.remove(h, files=False)" in _src368)
_sh368.rmtree(_d368, ignore_errors=True)

# ---- qBittorrent's speed: one handler, and the normal limits never written
import pd_speed as _sp370, glob as _gl370
class _QB370:
    def __init__(self, mode=b"0", alt=10240):
        self.mode, self.prefs, self.order = mode, {"alt_up_limit": alt, "alt_dl_limit": alt, "up_limit": 0, "dl_limit": 0}, []
    def _call(self, path, fields=None, timeout=0):
        if path.endswith("toggleSpeedLimitsMode"):
            self.mode = b"0" if self.mode == b"1" else b"1"
            self.order.append("toggle")
        return self.mode
    def preferences(self): return dict(self.prefs)
    def set_preferences(self, p): self.prefs.update(p); self.order.append("set")
_M370 = 200 * 125000
check("speed: a viewer low beats a viewer watching; watching holds to the page's number; else no hold",
      _sp370.wanted(True, True, 200) == _sp370.GENTLE_BYTES and _sp370.wanted(False, True, 200) == _M370
      and _sp370.wanted(False, False, 200) == 0)
_w370 = {}
check("speed: each reason lasts a minute past its end, then the next one down applies",
      _sp370.decide(True, True, 200, _w370, 1000) == _sp370.GENTLE_BYTES
      and _sp370.decide(False, True, 200, _w370, 1059) == _sp370.GENTLE_BYTES
      and _sp370.decide(False, False, 200, _w370, 1061) == _M370
      and _sp370.decide(False, False, 200, _w370, 1120) == 0)
_q370 = _QB370()
_sp370.apply(_q370, _M370)
check("speed: the number goes in before the mode goes on: it was 10 KB/s",
      _q370.order == ["set", "toggle"] and _q370.mode == b"1" and _q370.prefs["alt_up_limit"] == _M370)
_q370.order = []
_sp370.apply(_q370, _M370)
check("speed: nothing written when it is already so", _q370.order == [])
_sp370.apply(_q370, _sp370.GENTLE_BYTES)
check("speed: a viewer low holds in viewer mode too, where the old rule could not",
      _q370.mode == b"1" and _q370.prefs["alt_dl_limit"] == _sp370.GENTLE_BYTES)
_sp370.apply(_q370, 0)
check("speed: all clear, the mode goes off and the normal limits were never touched",
      _q370.mode == b"0" and _q370.prefs["up_limit"] == 0 and _q370.prefs["dl_limit"] == 0)
_q370 = _QB370()
_d370 = {"config": {"gentleWas": [_sp370.GENTLE_BYTES, 1000]}}
import pd_torrents as _pt370
_sv370 = _pt370.save
_pt370.save = lambda: None
try:
    check("speed: limits an older build kept on disk are put back once, its own number meaning none",
          _sp370.put_back_old(_q370, _d370) and _q370.prefs["dl_limit"] == 0 and _q370.prefs["up_limit"] == 1000
          and "gentleWas" not in _d370["config"] and not _sp370.put_back_old(_q370, _d370))
finally:
    _pt370.save = _sv370
_here370 = os.path.dirname(os.path.abspath(__file__))
_others370 = []
for _p370 in _gl370.glob(os.path.join(_here370, "pd*.py")) + _gl370.glob(os.path.join(_here370, "..", "tlseed", "race*.py")):
    if os.path.basename(_p370) in ("pd_speed.py", "pd-test-rules.py"):
        continue
    _t370 = open(_p370, encoding="utf-8", errors="replace").read()
    if any(w in _t370 for w in ("setUploadLimit", "setDownloadLimit", "toggleSpeedLimitsMode", "alt_up_limit", '"up_limit"')):
        _others370.append(os.path.basename(_p370))
check("speed: no other code sets a speed: %s" % _others370, not _others370)

# ---- the boss: a stalled worker is told once, a dead one started, a stuck disk turn given back
import pd_boss as _bo, pd_disk as _dk371
_said371 = []
_was371 = dict(_bo.STATE)
_bo.STATE.update(log=_said371.append, watched={}, stalled=set(), restarts={}, acted=0, crowded=False, gaveUp=set(),
                 seen={}, rest={}, stress="", calmSince=0.0, busyRounds=0, short=None, root="", viewers=None,
                 otherUp=None, restart=None, headStart=0, otherWas=None, otherSince=0.0, blocked="", weekly=None,
                 started=_bo.STARTED)
try:
    _bo.check_stalled({"a": {"step": "reading", "ok": False, "for": 2000}, "b": {"step": "x", "ok": True}})
    _bo.check_stalled({"a": {"step": "reading", "ok": False, "for": 2030}, "b": {"step": "x", "ok": True}})
    check("boss: a stalled worker is told once, not every round",
          len(_said371) == 1 and "a stalled" in _said371[0])
    _bo.check_stalled({"a": {"step": "reading", "ok": True}, "b": {"step": "x", "ok": True}})
    check("boss: and told when it works again", "a is working again" in _said371[-1] and not _bo.STATE["stalled"])
    _st371 = []
    _bo.watch("w", "thread-w", lambda: _st371.append(1))
    check("boss: a live worker is left alone", _bo.check_threads({"thread-w"}, 1000) == [] and not _st371)
    check("boss: a dead one is started again", _bo.check_threads(set(), 1000) == ["w"] and _st371 == [1])
    for _n in range(5):
        _bo.check_threads(set(), 1000 + _n)
    check("boss: three times an hour, then it is left off and said once",
          len(_st371) == _bo.RESTARTS_PER_HOUR and sum("left off" in x for x in _said371) == 1)
    check("boss: an hour on it may try again", _bo.check_threads(set(), 5000) == ["w"] and "w" not in _bo.STATE["gaveUp"])
    _dk371.HELD["t1"] = {"drive": "G:", "kind": "work", "label": "x", "since": 0}
    _dk371.HELD["t2"] = {"drive": "G:", "kind": "work", "label": "y", "since": _bo.TURN_LIMIT}
    _dk371.HELD["t3"] = {"drive": "G:", "kind": "copy", "label": "z", "since": 0}
    _dk371.HELD["t4"] = {"drive": "G:", "kind": "watch", "label": "v", "since": 0}
    _gone371 = _bo.check_turns(_dk371.HELD, _bo.TURN_LIMIT + 10)
    check("boss: only a background job's turn held too long is given back, never a copy's or a viewer's",
          _gone371 == ["t1"] and all(k in _dk371.HELD for k in ("t2", "t3", "t4")))
    for _k in ("t2", "t3", "t4"):
        _dk371.done(_k)
    def _fails371(name):
        out = []
        for _i in range(3):
            out += _bo.evaluate({name: {"step": "failed, again soon", "ok": True}}, 100 + _i * 10)
            out += _bo.evaluate({name: {"step": "working", "ok": True}}, 105 + _i * 10)
        return out
    check("boss: a secondary worker failing again and again is rested for an hour, and then runs again",
          _fails371("subcheck") == [("subcheck", "rested")] and _bo.rested("subcheck", 200)
          and not _bo.rested("subcheck", 120 + _bo.REST_FOR + 1))
    check("boss: one needed for viewing is told about and never rested",
          _fails371("torrents") == [("torrents", "told")] and not _bo.rested("torrents", 200))
    check("boss: two failures far apart are not again and again",
          not _bo.evaluate({"cast": {"step": "failed", "ok": True}}, 100)
          and not _bo.evaluate({"cast": {"step": "working", "ok": True}}, 110)
          and not _bo.evaluate({"cast": {"step": "failed", "ok": True}}, 110 + _bo.FAILS_FORGOTTEN + 1)
          and not _bo.evaluate({"cast": {"step": "working", "ok": True}}, 110 + _bo.FAILS_FORGOTTEN + 2)
          and not _bo.evaluate({"cast": {"step": "failed", "ok": True}}, 110 + 2 * _bo.FAILS_FORGOTTEN + 4))
    _bo.evaluate({"stills": {"step": "x", "done": 5, "left": 9}}, 100)
    check("boss: work left and none done for hours is told once; moving on clears it",
          _bo.evaluate({"stills": {"step": "x", "done": 5, "left": 9}}, 100 + _bo.NO_PROGRESS + 1) == [("stills", "no progress")]
          and not _bo.evaluate({"stills": {"step": "x", "done": 5, "left": 9}}, 200 + _bo.NO_PROGRESS)
          and not _bo.evaluate({"stills": {"step": "x", "done": 6, "left": 8}}, 300 + _bo.NO_PROGRESS))
    _r372 = _bo.read_stress
    check("boss: a viewer short of disk, memory near full and too many threads are stress at once",
          _r372(True, 0, 0, 1) and _r372(False, 0, _bo.BUSY_MEMORY, 1) and _r372(False, 0, 0, _bo.MOST_THREADS + 1)
          and not _r372(False, 0, 0, 1))
    check("boss: a burst on the processor is not, three rounds of it is",
          not _r372(False, 95, 0, 1) and not _r372(False, 95, 0, 1) and _r372(False, 95, 0, 1)
          and not _r372(False, 10, 0, 1) and not _r372(False, 95, 0, 1))
    _bo.STATE["rest"] = {}
    _bo.judge_stress("memory at 95%", 1000)
    check("boss: under stress secondary workers stop at once, live ones do not",
          _bo.rested("xseed", 1000) and _bo.rested("stills", 1000) and not _bo.rested("torrents", 1000)
          and not _bo.rested("speed", 1000) and _bo.stressed())
    _bo.judge_stress("", 1010)
    _bo.judge_stress("", 1010 + _bo.CALM_FOR - 1)
    check("boss: they stay stopped until it has been calm a while", _bo.rested("xseed", 1200))
    _bo.judge_stress("memory at 95%", 1200)
    _bo.judge_stress("", 1210)
    check("boss: stress coming back starts the calm over", _bo.rested("xseed", 1300) and _bo.STATE["calmSince"] == 1210)
    _bo.judge_stress("", 1210 + _bo.CALM_FOR)
    check("boss: then they go on again", not _bo.rested("xseed", 1400) and not _bo.stressed())
    _nr373 = _bo.needs_restart
    check("boss: a live worker stuck ten minutes calls for a restart; a secondary one only after two hours; a working one never",
          _nr373({"torrents": {"ok": False, "for": _bo.LIVE_STUCK}}) == "torrents"
          and _nr373({"stills": {"ok": False, "for": _bo.LIVE_STUCK}}) == ""
          and _nr373({"stills": {"ok": False, "for": _bo.SECONDARY_STUCK}}) == "stills"
          and _nr373({"torrents": {"ok": True, "for": 99999}}) == "")
    _b373 = _bo.restart_blocked
    _ok373 = _bo.RESTART_EVERY + 1000
    check("boss: viewers on an app that resumes do not stop it",
          _b373([{"app": "android 0.19.344 tv"}], _bo.SETTLED, 0, _ok373) == "")
    check("boss: a viewer on the app that cannot resume does",
          _b373([{"app": "android 0.18.16 tv"}], _bo.SETTLED, 0, _ok373) != "")
    check("boss: one server at a time: the other just changed, online or off, it waits",
          _b373([], _bo.SETTLED - 1, 0, _ok373) != "")
    check("boss: the copy waits longer, so the main server goes first",
          _b373([], _bo.SETTLED, 0, _ok373, _bo.COPY_WAITS) != "" and _b373([], _bo.SETTLED, 0, _ok373) == "")
    check("boss: no more than one restart in six hours", _b373([], _bo.SETTLED, 1000, 1000 + _bo.RESTART_EVERY - 1) != "")
    _t373 = _tf368.mkdtemp()
    _did373 = []
    _bo.STATE.update(root=_t373, restart=lambda: _did373.append(1), otherUp=lambda: False, viewers=lambda: [],
                     otherWas=None, otherSince=0.0, blocked="", headStart=0)
    _stuck373 = {"torrents": {"ok": False, "for": _bo.LIVE_STUCK, "step": "x"}}
    _T373 = _bo.RESTART_EVERY + 5000.0
    check("boss: the other server just seen offline: not yet", _bo.check_restart(_stuck373, _T373) == "" and not _did373)
    check("boss: offline and steady: this one restarts, it is the only one",
          _bo.check_restart(_stuck373, _T373 + _bo.SETTLED) == "torrents" and _did373 == [1])
    check("boss: the restart is on paper before it goes, so a new start does not go again",
          _bo.last_restart() == _T373 + _bo.SETTLED
          and _bo.check_restart(_stuck373, _T373 + _bo.SETTLED + 10) == "" and _did373 == [1])
    with open(os.path.join(_t373, "boss.json"), "w", encoding="utf-8") as _f:
        _f.write("{not json")
    check("boss: a paper that cannot be read counts as just restarted", _bo.last_restart() > _T373 or time.time() - _bo.last_restart() < 5)
    _sh368.rmtree(_t373, ignore_errors=True)
    _bo.STATE.update(restart=None, otherUp=None, viewers=None, root="")
    import datetime as _dt374
    _mon374 = _dt374.datetime(2026, 11, 2, 4, 0).timestamp()      # a Monday, 04:00
    _wk374 = {"on": True, "day": 0, "time": "04:00"}
    check("weekly: the last set time, this week's once it has passed, last week's before",
          _bo.last_slot(_wk374, _mon374 + 60) == _mon374
          and _bo.last_slot(_wk374, _mon374 - 60) == _dt374.datetime(2026, 10, 26, 4, 0).timestamp())
    _began374 = _mon374 - 3 * 86400
    check("weekly: due once the time has passed and this process is older than it",
          _bo.weekly_due(_wk374, _began374, _mon374 + 60) == _mon374)
    check("weekly: a machine hibernated through it is the same process: due when it wakes, days later",
          _bo.weekly_due(_wk374, _began374, _mon374 + 2 * 86400) == _mon374)
    check("weekly: started after the time, by an install or anything, it counts as done",
          _bo.weekly_due(_wk374, _mon374 + 10, _mon374 + 60) == 0)
    check("weekly: off is off", _bo.weekly_due(dict(_wk374, on=False), _began374, _mon374 + 60) == 0
          and _bo.weekly_due(None, _began374, _mon374 + 60) == 0)
    check("weekly: Settings' values are checked",
          _bo.clean_weekly({"on": 1, "day": 9, "time": "25:00"}) == {"on": True, "day": 6, "time": "04:00"}
          and _bo.clean_weekly({"on": True, "day": 2, "time": "03:30"})["time"] == "03:30")
    _t374 = _tf368.mkdtemp()
    _did374 = []
    _seen374 = [[{"app": "android 0.19.344 tv"}]]
    _bo.STATE.update(root=_t374, restart=lambda: _did374.append(1), otherUp=lambda: True, viewers=lambda: _seen374[0],
                     otherWas=True, otherSince=0.0, blocked="", headStart=0, weekly=lambda: _wk374, started=_began374)
    check("weekly: it waits while somebody watches", _bo.check_restart({}, _mon374 + 60) == "" and not _did374)
    check("weekly: after six hours of that, it goes: they carry on",
          _bo.check_restart({}, _mon374 + _bo.WEEKLY_WAITS_FOR_IDLE + 1) == "the weekly restart" and _did374 == [1])
    _bo.STATE.update(started=_mon374 + _bo.WEEKLY_WAITS_FOR_IDLE + 5)
    check("weekly: once a week: restarted, it is not due again", _bo.check_restart({}, _mon374 + 86400) == "" and _did374 == [1])
    _sh368.rmtree(_t374, ignore_errors=True)
    _bo.STATE.update(restart=None, otherUp=None, viewers=None, root="", weekly=None, started=_bo.STARTED)
    check("boss: too many threads told once", _bo.check_crowd(["x"] * 301) and _bo.check_crowd(["x"] * 301)
          and sum("threads alive" in x for x in _said371) == 1 and not _bo.check_crowd(["x"]))
finally:
    _bo.STATE.clear()
    _bo.STATE.update(_was371)
_was372 = dict(_bo.STATE)
try:
    _bo.STATE.pop("wokeAt", None)
    _bo.STATE["lastRound"] = 1000
    check("boss: an ordinary round is not a waking", not _bo.just_woke(1010))
    _dk371.HELD["t372"] = {"kind": "check", "since": 900, "drive": "D:", "label": "x"}
    check("boss: a night's gap rests the boss", _bo.just_woke(40000))
    check("boss: and moves the disk turns past it, so none reads as held all night",
          _dk371.HELD["t372"]["since"] == 900 + 40000 - 1010 and not _bo.check_turns(_dk371.HELD, 40001))
    check("boss: for a minute", _bo.just_woke(40059) and not _bo.just_woke(40061))
finally:
    _dk371.HELD.pop("t372", None)
    _bo.STATE.clear()
    _bo.STATE.update(_was372)
_src371 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_boss.py"), encoding="utf-8").read()
check("boss: it kills nothing itself: a restart goes through the server's own",
      not any(w in _src371 for w in ("os._exit", "taskkill", "sys.exit", "execv", ".kill(")))

# ---- the Monitor opened on its own shows nothing of the page until it has drawn
_ix375 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"), encoding="utf-8").read()
_ap375 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("monitor: hidden before the page's own script runs, and shown once drawn or after twelve seconds",
      _ix375.index("monitorwait") < _ix375.index('src="app.js') and 'classList.remove("monitorwait")' in _ap375
      and "waited >= 12000" in _ap375)

# ---- the popular films are a catalogue's list: a refresh may come back shorter, half gone is a fault
_gt376 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-gate.py"), encoding="utf-8").read()
check("gate: the top films list may fall by a refresh, as the top shows may",
      '("top_films.json", ".films")' in _gt376[_gt376.index("MAY_FALL = {"):_gt376.index("EXPIRES = {")]
      and "n < v * 0.5" in _gt376)

# ---- setting a pack's home never deletes here unless Sync back says so
_sv377 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_at377 = _sv377.index("def sync_back_round")
_rd377 = _sv377[_at377:_sv377.index("let_gone = []", _at377)]
check("home: handing a torrent over removes its files here, so it runs only with Sync back on",
      "if sync_on:" + chr(10) + "                cls.hand_over(where" in _rd377
      and _rd377.index("cls.hand_over(where") < _rd377.index("if not sync_on:"))

# ---- films moved in are read, or the main server hands them over for ever
import pd_torrents as _pt378, tempfile as _tf378, shutil as _sh378, threading as _thr378
_d378 = _tf378.mkdtemp()
_f378 = [{"index": 0, "name": "a.mkv", "size": 10, "progress": 0.1, "priority": 0},
         {"index": 1, "name": "b.mkv", "size": 10, "progress": 1, "priority": 1},
         {"index": 2, "name": "c.mkv", "size": 10, "progress": 0, "priority": 0},
         {"index": 3, "name": "d.mkv", "size": 99, "progress": 0, "priority": 0},
         {"index": 4, "name": "e.mkv", "size": 10, "progress": 0, "priority": 0}]
for _n in ("a.mkv", "b.mkv", "c.mkv", "d.mkv", "e.mkv"):
    with open(os.path.join(_d378, _n), "wb") as _h:
        _h.write(b"x" * 10)
class _QB378:
    calls = []
    state = "stalledUP"
    def __init__(self, cfg=None): pass
    def info(self, h): return {"save_path": _d378, "state": _QB378.state}
    def files(self, h): return [dict(f) for f in _f378]
    def stop(self, h): _QB378.calls.append("stop")
    def priority(self, h, idx, v):
        _QB378.calls.append(("prio", sorted(idx), v))
        for f in _f378:
            if f["index"] in idx:
                f["priority"] = v
    def recheck(self, h): _QB378.calls.append("recheck")
    def start(self, h): _QB378.calls.append("start")
def _row378(i, reason="moved here: the pack's home", **k):
    return dict({"hash": "h", "index": i, "state": "done", "reason": reason}, **k)
_data378 = {"config": {}, "packs": [{"hash": "h", "name": "Pack"}],
            "downloads": [_row378(0), _row378(1), _row378(3), _row378(2, "drawn in a shuffle"), _row378(4, off=True)]}
_s378 = (_pt378.QB, _pt378.load, _pt378.save)
_th378 = _thr378.Thread
try:
    _pt378.QB, _pt378.load, _pt378.save = _QB378, (lambda: _data378), (lambda: None)
    check("moved: a moved film on the disk and unread is waiting; one read, short, not moved here or switched off is not",
          _pt378.moved_waiting("h") == [0])
    _data378["downloads"].append(dict(_row378(1), state="downloading"))
    check("moved: not with a download coming into the pack", _pt378.read_moved(100000) == "" and not _QB378.calls)
    _data378["downloads"].pop()
    _QB378.state = "checkingUP"
    check("moved: not while a pack is being read", _pt378.read_moved(100000) == "" and not _QB378.calls)
    _QB378.state = "stalledUP"
    check("moved: read with the pack stopped first, so it fetches nothing",
          _pt378.read_moved(100000) == "Pack" and _QB378.calls == ["stop", ("prio", [0], 1), "recheck"])
    _QB378.calls = []
    check("moved: once a day", _pt378.read_moved(100000 + _pt378.READ_MOVED_EVERY - 1) == "" and not _QB378.calls)
    check("moved: a file read once is not read again, whatever it read as",
          _pt378.moved_waiting("h") == [] and _data378["movedChecked"]["h"] == [0])
    _pt378.moved_fresh("h", 0)
    check("moved: one that really came over the network is read again", _pt378.moved_waiting("h") == [0])
    _data378["movedChecked"]["h"] = [0]
    _QB378.calls = []
    _keep378 = [dict(f) for f in _f378]
    _f378[0].update(progress=0.9, priority=1)
    _QB378.state = "checkingUP"
    check("moved: while it reads nothing is started", _pt378.finish_moved_reads(100100) == [] and not _QB378.calls)
    _QB378.state = "stoppedDL"
    check("moved: read and stopped, what is short is switched off first, so nothing is fetched, and it seeds",
          _pt378.finish_moved_reads(100100) == ["Pack"] and _QB378.calls == [("prio", [0], 0), "start"])
    _QB378.calls = []
    _QB378.state = "stoppedUP"
    _data378["downloads"].append(dict(_row378(1), state="downloading"))
    check("moved: not with a download on it", _pt378.finish_moved_reads(100100) == [] and not _QB378.calls)
    _data378["downloads"].pop()
    check("moved: a day on it is the seeding rules' to stop", _pt378.finish_moved_reads(100000 + _pt378.READ_MOVED_EVERY) == [] and not _QB378.calls)
    _QB378.state = "stalledUP"
    _f378[:] = _keep378
    _QB378.calls = []
    _f378[0].update(progress=0.5, priority=1)
    _sl378 = _pt378.time.sleep
    _pt378.time.sleep = lambda s: None
    try:
        check("moved: a moved film that fails its read is switched off, and the pack seeds on",
              _pt378.seed_when_read("h", every=0, moved=[0]) is True
              and _QB378.calls == [("prio", [0], 0), "start"])
    finally:
        _pt378.time.sleep = _sl378
finally:
    _pt378.QB, _pt378.load, _pt378.save = _s378
    _thr378.Thread = _th378
    _sh378.rmtree(_d378, ignore_errors=True)
_fl378 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
check("moved: the follow round reads once all handed over have arrived",
      "_read_moved_when_in(moves)" in _fl378 and "pd_torrents.read_moved()" in _fl378)

# ---- length is not a film; and a film short only of its edge pieces has arrived
import pd_torrents as _pt379, tempfile as _tf379, shutil as _sh379
_oe379 = _pt379.only_edges_short
_P379 = 134 * 10 ** 6
check("edges: short by up to two pieces is only its edges; more is not; whole is",
      _oe379(0.97, 5 * 10 ** 9, _P379) and not _oe379(0.9, 5 * 10 ** 9, _P379) and _oe379(1, 5 * 10 ** 9, _P379))
_d379 = _tf379.mkdtemp()
with open(os.path.join(_d379, "a.mkv"), "wb") as _h:
    _h.write(b"x" * 10)
_pp379 = _pt379.placed_path
try:
    _pt379.placed_path = lambda h, i, n: os.path.join(_d379, "a.mkv")
    check("placed: right length and on the disk is placed", _pt379.already_placed("h", 0, "a", 10, share=lambda p: 1.0))
    check("placed: right length and mostly empty is a placeholder, and it is sent",
          _pt379.already_placed("h", 0, "a", 10, share=lambda p: 0.29) == "")
    check("placed: another length is not", _pt379.already_placed("h", 0, "a", 11, share=lambda p: 1.0) == "")
finally:
    _pt379.placed_path = _pp379
    _sh379.rmtree(_d379, ignore_errors=True)
_sv379 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_rd379 = _sv379[_sv379.index("def sync_back_round"):_sv379.index("let_gone = []")]
check("edges: a copy short only of its edges is not sent again; deleting here still asks for whole",
      "pd_homes.give(films, here, copy_holds, listed)" in _rd379
      and "copy_whole, playing" in _rd379.replace(chr(10), " ").replace("  ", "")
      and "copy_holds" not in _rd379[_rd379.index("pd_homes.decide("):])

# ---- a placeholder of the right length is written over; a film really there is not
import pd_torrents as _pt380, tempfile as _tf380, shutil as _sh380, io as _io380
_d380 = _tf380.mkdtemp()
_p380 = os.path.join(_d380, "a.mkv")
_pp380 = _pt380.placed_path
try:
    _pt380.placed_path = lambda h, i, n: _p380
    with open(_p380, "wb") as _h:
        _h.write(b"0" * 10)
    _pt380.place_and_recheck("h", 0, "a", _io380.BytesIO(b"1" * 10), 10, recheck=False, share=lambda p: 1.0)
    check("move in: a file really there is left alone", open(_p380, "rb").read() == b"0" * 10)
    _pt380.place_and_recheck("h", 0, "a", _io380.BytesIO(b"1" * 10), 10, recheck=False, share=lambda p: 0.29)
    check("move in: a placeholder of the same length is written over with the film", open(_p380, "rb").read() == b"1" * 10)
    check("move in: nothing left beside it", sorted(os.listdir(_d380)) == ["a.mkv"])
finally:
    _pt380.placed_path = _pp380
    _sh380.rmtree(_d380, ignore_errors=True)

# ---- a stub is never sent as a film, never written over one, and a moved film is never fetched
import pd_torrents as _pt381, tempfile as _tf381, shutil as _sh381, io as _io381
_d381 = _tf381.mkdtemp()
_p381 = os.path.join(_d381, "a.mkv")
_q381 = os.path.join(_d381, "other.mkv")
_s381 = (_pt381.placed_path, _pt381.file_of, _pt381.elsewhere_path, _pt381.load, _pt381.QB, _pt381.asked_for, _pt381.kept_whole)
try:
    with open(_p381, "wb") as _h:
        _h.write(b"0" * 10)
    _pt381.placed_path = lambda h, i, n: _p381
    check("move in: a transfer that comes short leaves what was there",
          _pt381.place_and_recheck("h", 0, "a", _io381.BytesIO(b"1" * 3), 10, recheck=False, share=lambda p: 0.2) == ""
          and open(_p381, "rb").read() == b"0" * 10 and sorted(os.listdir(_d381)) == ["a.mkv"])
    with open(_q381, "wb") as _h:
        _h.write(b"2" * 99)
    _pt381.file_of = lambda h, i: _p381
    _pt381.elsewhere_path = lambda h, i: _q381
    check("send: another torrent holding more of it is sent, not the pack's stub", _pt381.file_to_send("h", 0) == _q381)
    _pt381.elsewhere_path = lambda h, i: ""
    check("send: the pack's own otherwise", _pt381.file_to_send("h", 0) == _p381)
    _pt381.file_of = lambda h, i: ""
    check("send: nothing here is nothing", _pt381.file_to_send("h", 0) == "")
    _rows381 = {"downloads": [{"hash": "h", "index": 1, "state": "done", "reason": "moved here: the pack's home"},
                              {"hash": "h", "index": 2, "state": "done", "reason": "moved here: the pack's home"},
                              {"hash": "h", "index": 3, "state": "done", "reason": ""},
                              {"hash": "h", "index": 4, "state": "downloading", "reason": ""}], "config": {}}
    _pt381.load = lambda: _rows381
    _files381 = [{"index": 1, "progress": 0.3, "priority": 0, "name": "x1", "size": 5}, {"index": 2, "progress": 1, "priority": 1, "name": "x2", "size": 5},
                 {"index": 3, "progress": 1, "priority": 1, "name": "x3", "size": 5}, {"index": 4, "progress": 0.1, "priority": 1, "name": "x4", "size": 5}]
    check("moved: a moved film short of whole is known; a whole one or one asked for is not",
          _pt381.moved_short("h", _files381) == {1})
    _rows381["downloads"].append({"hash": "h", "index": 1, "state": "downloading", "reason": ""})
    check("moved: one somebody has since asked for is fetched like any other", _pt381.moved_short("h", _files381) == set())
    _rows381["downloads"].pop()
    class _Q381:
        def __init__(self, c=None): self.turned = []
        def info(self, h): return {"state": "stalledUP", "save_path": _d381}
        def files(self, h): return _files381
        def priority(self, h, idx, v): self.turned.append((sorted(idx), v))
    _pt381.QB = _Q381
    _pt381.asked_for = lambda h: {1, 2, 3, 4}
    _pt381.kept_whole = lambda h: False
    _qq381 = _Q381()
    _pt381.only_asked(_qq381, "h", files=_files381)
    check("moved: a moved film short of whole is never switched on, so never fetched; what was asked for still is",
          not any(1 in idx and v == 1 for idx, v in _qq381.turned))
finally:
    _pt381.placed_path, _pt381.file_of, _pt381.elsewhere_path, _pt381.load, _pt381.QB, _pt381.asked_for, _pt381.kept_whole = _s381
    _sh381.rmtree(_d381, ignore_errors=True)
_sv381 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("send: the file sent is the one chosen", "where = pd_torrents.file_to_send(" in _sv381)

# ---- split: each file stays where it came down, new downloads go to the machine chosen
import pd_torrents as _pt382, pd_homes as _ph382
_data382 = {"home": {}, "forCopy": [{"key": "a", "move": True, "hash": "h"}, {"key": "b", "move": True, "hash": "other"}, {"key": "c", "hash": "h"}], "packs": [], "downloads": []}
_s382 = (_pt382.load, _pt382.save)
try:
    _pt382.load, _pt382.save = (lambda: _data382), (lambda: None)
    check("split: a home like any other, and unknown ones refused",
          _pt382.set_home("h", "split")["ok"] and _pt382.home_of("h") == "split" and not _pt382.set_home("h", "nowhere")["ok"])
    check("split: what was still listed to move from that pack stays; a request and another pack's move do not go",
          [r["key"] for r in _data382["forCopy"]] == ["b", "c"])
    check("split: new downloads go to the copy server until told otherwise",
          _pt382.split_first() == "copy" and _pt382.set_split_first("main")["ok"] and _pt382.split_first() == "main"
          and not _pt382.set_split_first("nowhere")["ok"] and _pt382.split_first() == "main")
    _data382["splitFirst"] = "copy"
    _h382 = _pt382.home_of
    _rep382 = [{"hash": "h", "index": 1}]
    check("split: nothing the copy holds comes back here, with Sync back on or off",
          _ph382.take_back(_rep382, _h382, lambda h, i: 5, {5}, True) == [] and _ph382.take_back(_rep382, _h382, lambda h, i: 5, {5}, False) == [])
finally:
    _pt382.load, _pt382.save = _s382
_sv382 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_rd382 = _sv382[_sv382.index("def sync_back_round"):_sv382.index("let_gone = []")]
check("split: nothing here is handed to the copy, so nothing moves",
      'in ("copy", "both")' in _rd382[:_rd382.index("asked = {}")] and "split" not in _rd382)
_tr382 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
check("split: nothing is ever let go from a split pack: only a pack at home on the copy",
      'if home_of(info_hash) != "copy":' in _tr382[_tr382.index("def let_go("):_tr382.index("def let_go(") + 1200])
_st382 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8", errors="replace").read()
check("split: on the page as a home, with where downloads go first", '["split", "Split"]' in _st382 and "Downloads first on" in _st382)

# ---- a paused viewer holds no speed back
import pd_speed as _sp383
check("speed: playing counts, paused, stopped and a copy do not",
      _sp383.viewing([{"how": "direct play", "state": "playing"}])
      and _sp383.viewing([{"how": "waiting"}])
      and not _sp383.viewing([{"how": "waiting", "state": "paused"}])
      and not _sp383.viewing([{"how": "direct play", "state": "stopped"}])
      and not _sp383.viewing([{"how": "syncing", "state": "syncing"}]) and not _sp383.viewing([]))

# ---- a copy of a pack's film goes where the pack keeps it, and stays
import pd_follow as _fl384, pd_torrents as _pt384, tempfile as _tf384, shutil as _sh384, io as _io384
_d384 = _tf384.mkdtemp()
_cp384, _pk384 = os.path.join(_d384, "copies"), os.path.join(_d384, "Pack", "Film")
os.makedirs(_cp384)
os.makedirs(_pk384)
_in384 = os.path.join(_pk384, "film.mkv")
_s384 = dict(pack_place=_pt384.pack_place, in_its_place=_pt384.in_its_place, copied_into_pack=_pt384.copied_into_pack)
_f384 = dict(opened=_fl384.opened, looks_like_film=_fl384.looks_like_film, note_ours=_fl384.note_ours, quick_mark=_fl384.quick_mark)
_got384, _ours384, _sent384 = [], [], []
class _Ans384(_io384.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
try:
    _pt384.pack_place = lambda n, s=None: _in384 if str(n).lower() == "film.mkv" and (not s or s == 20) else ""
    _pt384.in_its_place = lambda p: _Real384[0]
    _Real384 = [False]
    _pt384.copied_into_pack = lambda n: _got384.append(n)
    def _op384(req, patience):
        _sent384.append(req.full_url)
        return _Ans384(b"f" * 20)
    _fl384.opened, _fl384.looks_like_film, _fl384.note_ours = _op384, (lambda a, b: True), (lambda f, p: _ours384.append(p))
    _fl384.quick_mark = lambda p: ""
    _one384 = {"master": "http://m", "key": "k"}
    _item384 = {"name": "film.mkv", "size": 20, "part": 7}
    check("copy place: a pack's film goes to the pack, anything else to the copies folder",
          _fl384.copy_place(_cp384, "film.mkv", 20) == _in384 and _fl384.copy_place(_cp384, "other.mkv", 20) == os.path.join(_cp384, "other.mkv")
          and _fl384.copy_place(_cp384, "film.mkv", 11) == os.path.join(_cp384, "film.mkv"))
    _Real384[0] = True
    _fl384.copy_file(_one384, _item384, _cp384)
    check("copy: it lands in the pack, not the copies folder", os.path.getsize(_in384) == 20 and os.listdir(_cp384) == [])
    check("copy: written down for the pack to read, and not on the list a full folder clears from",
          _got384 == ["film.mkv"] and _ours384 == [] and len(_sent384) == 1)
    _sent384.clear(); _got384.clear()
    _fl384.copy_file(_one384, _item384, _cp384)
    check("copy: once it is there nothing is fetched again", not _sent384 and not _got384)
    with open(_in384, "wb") as _h:
        _h.write(b"0" * 20)
    _Real384[0] = False
    check("copy: in a pack, the right length and mostly empty is not here",
          not _fl384.copy_here(_cp384, "film.mkv", _in384, 20))
    _fl384.copy_file(_one384, _item384, _cp384)
    check("copy: a placeholder there is written over", open(_in384, "rb").read() == b"f" * 20 and len(_sent384) == 1)
    _sent384.clear(); _got384.clear()
    with open(_in384, "wb") as _h:
        _h.write(b"0" * 20)
    with open(os.path.join(_cp384, "film.mkv"), "wb") as _h:
        _h.write(b"c" * 20)
    _Real384[0] = False
    _fl384.copy_file(_one384, _item384, _cp384)
    check("copy: one already in the copies folder is put in its place, nothing fetched",
          open(_in384, "rb").read() == b"c" * 20 and not _sent384 and os.listdir(_cp384) == [] and _got384 == ["film.mkv"])
    _sent384.clear()
    _fl384.copy_file(_one384, dict(_item384, name="other.mkv"), _cp384)
    check("copy: a film no pack carries goes on as before, and may be cleared",
          os.path.exists(os.path.join(_cp384, "other.mkv")) and _ours384 == [os.path.join(_cp384, "other.mkv")])
finally:
    for _k, _v in _s384.items():
        setattr(_pt384, _k, _v)
    for _k, _v in _f384.items():
        setattr(_fl384, _k, _v)
    _sh384.rmtree(_d384, ignore_errors=True)

# ---- a guest may warm the next episode, as any viewer's app does
import pd_invites as _in385
check("warm: a guest's app may ask for its next episode to be read ahead",
      _in385.Invites.allowed("/warm") and _in385.Invites.allowed("/warm?key=e1&mi=0"))

# ---- a download somebody asks for starts at once
import pd_follow as _fl386, pd_torrents as _pt386, tempfile as _tf386, shutil as _sh386
_took386 = []
_rows386 = {"forCopy": [{"id": "1", "key": "a"}, {"id": "2", "key": "b", "move": True}, "junk"]}
check("requests: what somebody asked for is taken now; a film to move waits for its round",
      _fl386.take_requests({}, read=lambda: _rows386, take=lambda one, r: _took386.extend(x["id"] for x in r) or _took386) == ["1"])
_took386.clear()
check("requests: nothing asked for, nothing done",
      _fl386.take_requests({}, read=lambda: {"forCopy": []}, take=lambda one, r: _took386.append("x")) == [] and not _took386)
check("requests: looked for once a minute, not often: the main server says when", _fl386.REQUESTS_EVERY >= 60)
_st387 = dict(_pt386.STATE)
_said387 = []
try:
    _pt386.STATE["pushRequest"] = lambda: _said387.append(1)
    _dd387 = {"forCopy": [], "copyAsked": {}}
    _sl387, _sv387 = _pt386.load, _pt386.save
    _pt386.load, _pt386.save = (lambda: _dd387), (lambda: None)
    try:
        _pt386.send_to_copy("k", "t", "w")
        for _n in range(50):
            if _said387:
                break
            time.sleep(0.01)
    finally:
        _pt386.load, _pt386.save = _sl387, _sv387
    check("requests: a download asked for is pushed to the copy server at once", _said387 == [1] and len(_dd387["forCopy"]) == 1)
finally:
    _pt386.STATE.clear()
    _pt386.STATE.update(_st387)
_d386 = _tf386.mkdtemp()
with open(os.path.join(_d386, "a.mkv"), "wb") as _h:
    _h.write(b"x" * 10)
_f386 = [{"index": 0, "name": "a.mkv", "size": 10, "progress": 0.2, "priority": 1}]
check("read first: a film really on the disk is read, so it is not fetched again",
      _pt386.needs_read_first(_f386, _d386, [], "h", 0, share=lambda p: 1.0))
check("read first: a placeholder of the right length is not: its download starts at once",
      not _pt386.needs_read_first(_f386, _d386, [], "h", 0, share=lambda p: 0.3))
_sh386.rmtree(_d386, ignore_errors=True)
_sv386 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("requests: the main server answers the quick look, moves left out",
      'if path == "/follow/asked":' in _sv386 and 'pd_torrents.for_copy() if not r.get("move")' in _sv386)

# ---- less asking: the speed rules write on a change, and ask who watches rarely
import pd_speed as _sp388
class _Q388:
    def __init__(self): self.n = 0
    def _call(self, path, fields=None, timeout=0):
        self.n += 1
        return b"0"
    def preferences(self):
        self.n += 1
        return {"alt_up_limit": 1, "alt_dl_limit": 1}
    def set_preferences(self, p): self.n += 1
_keep388 = dict(_sp388.STATE)
try:
    _sp388.STATE.update(applied=None, appliedAt=0.0, askedHere=0.0, here=False)
    _q388 = _Q388()
    check("speed: the client is told when the limit changes", _sp388.apply_if_due(_q388, 100, 1000) and _q388.n > 0)
    _q388.n = 0
    check("speed: not again while it has not, and looked at once a minute",
          not _sp388.apply_if_due(_q388, 100, 1030) and _q388.n == 0 and _sp388.apply_if_due(_q388, 100, 1000 + _sp388.CONFIRM_EVERY))
    _asked388 = []
    _h388 = lambda: _asked388.append(1) or True
    _sp388.watched_here(_h388, 2000)
    _sp388.watched_here(_h388, 2010)
    check("speed: who watches is asked every half minute, not every round", len(_asked388) == 1 and _sp388.watched_here(_h388, 2000 + _sp388.ASK_WATCHING_EVERY) and len(_asked388) == 2)
finally:
    _sp388.STATE.clear()
    _sp388.STATE.update(_keep388)

# ---- a read that takes minutes does not wait for tomorrow
import pd_torrents as _pt389
_rows389 = [{"hash": "h", "index": 0, "state": "done", "reason": "moved here: x"}]
_data389 = {"config": {}, "packs": [{"hash": "h", "name": "P"}], "downloads": _rows389, "movedRead": {"h": 100000.0}}
_files389 = [{"index": 0, "name": "a", "size": 10, "progress": 0.1, "priority": 0}, {"index": 1, "name": "b", "size": 10, "progress": 1, "priority": 1}]
class _Q389:
    def __init__(self, c=None): pass
    def info(self, h): return {"state": "stalledUP", "save_path": "x"}
    def files(self, h): return [dict(f) for f in _files389]
_s389 = (_pt389.QB, _pt389.load, _pt389.save, _pt389.moved_waiting, _pt389.recheck_if_unread)
_read389 = []
try:
    _pt389.QB, _pt389.load, _pt389.save = _Q389, (lambda: _data389), (lambda: None)
    _pt389.moved_waiting = lambda h: [0]
    _pt389.recheck_if_unread = lambda h, w: _read389.append(h) or True
    check("quick read: not twice within half an hour", _pt389.read_moved(100000 + _pt389.QUICK_READ_EVERY - 1) == "" and not _read389)
    check("quick read: a small one goes after that, not tomorrow", _pt389.read_moved(100000 + _pt389.QUICK_READ_EVERY) == "P" and _read389 == ["h"])
    _read389.clear()
    _data389["movedRead"]["h"] = 100000.0
    # what is on the disk counts, not what is switched on
    _files389[1].update(size=_pt389.QUICK_READ_BYTES + 1, priority=0)
    check("quick read: a big one waits its day, however little of it is switched on", _pt389.read_moved(100000 + _pt389.QUICK_READ_EVERY) == "" and not _read389)
    check("quick read: and then goes", _pt389.read_moved(100000 + _pt389.READ_MOVED_EVERY) == "P" and _read389 == ["h"])
finally:
    _pt389.QB, _pt389.load, _pt389.save, _pt389.moved_waiting, _pt389.recheck_if_unread = _s389

# ---- a file unchanged since the last read is not read again
import pd_torrents as _pt390, tempfile as _tf390, shutil as _sh390
_d390 = _tf390.mkdtemp()
_p390 = os.path.join(_d390, "a.mkv")
with open(_p390, "wb") as _h:
    _h.write(b"x" * 10)
_f390 = [{"index": 0, "name": "a.mkv", "size": 10, "progress": 0.2, "priority": 1}]
_m390 = os.path.getmtime(_p390)
check("read first: never read, a film on the disk is read",
      _pt390.needs_read_first(_f390, _d390, [], "h", 0, share=lambda p: 1.0))
check("read first: read since it last changed, it is not read again: it starts",
      not _pt390.needs_read_first(_f390, _d390, [], "h", 0, share=lambda p: 1.0, read_at=_m390 + 60))
check("read first: changed since that read, it is",
      _pt390.needs_read_first(_f390, _d390, [], "h", 0, share=lambda p: 1.0, read_at=_m390 - 60))
_sh390.rmtree(_d390, ignore_errors=True)

# ---- a pack of a name two series share goes to the one it fits
import ast as _ast91
_src91 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns91 = {"re": __import__("re")}
for _n91 in _ast91.parse(_src91).body:
    if getattr(_n91, "name", "") in ("twin_verdict", "release_year", "added_pack_for", "release_group", "series_twins"):
        exec(compile(_ast91.get_source_segment(_src91, _n91), "x", "exec"), _ns91)
_tv91, _ry91 = _ns91["twin_verdict"], _ns91["release_year"]
_old91, _new91 = {"year": 1985, "seasons": 7}, {"year": 2016, "seasons": 5}
check("twins: seven seasons fit only the one that ran seven",
      _tv91(0, 7, _old91, [_new91]) == "sure" and _tv91(0, 7, _new91, [_old91]) == "no")
check("twins: one season with no year could be either: listed, not put forward",
      _tv91(0, 1, _old91, [_new91]) == "unsure" and _tv91(0, 1, _new91, [_old91]) == "unsure")
check("twins: the year named decides, a year either side counts",
      _tv91(2017, 1, _new91, [_old91]) == "sure" and _tv91(2016, 1, _old91, [_new91]) == "no")
check("twins: a year neither began in says nothing",
      _tv91(2023, 1, _old91, [_new91]) == "unsure")
check("twins: not known which is meant: nothing is ruled out or put forward",
      _tv91(0, 7, None, [_old91, _new91]) == "unsure")
check("twins: a name nobody shares is not judged",
      _tv91(1999, 40, {"year": 2010, "seasons": 3}, []) == "sure" and _tv91(0, 1, None, []) == "sure")
check("twins: a count the catalogue did not give rules nothing out",
      _tv91(0, 9, {"year": 2016, "seasons": 0}, [_old91]) == "sure")
check("release year: the first year, not a resolution",
      _ry91("Show.2016.S01.2160p.WEB") == 2016 and _ry91("Show S01 1080p") == 0)

check("packs: nothing the tracker holds is cut from a series' or a season's list",
      '"found": found,' in _src91 and '"packs": packs,' in _src91)
_ap91 = _ns91["added_pack_for"]
_added91 = [{"hash": "h1", "name": "Twin.Show.S01.1080p.BluRay-grp", "seasons": [1]}]
check("packs: the same release spelt another way is the one added",
      _ap91("Twin Show S01 1080p BluRay-grp", {1}, _added91) == "h1"
      and _ap91("Twin Show br hevc-grp", {1}, _added91) == "h1")
check("packs: the same name with no group in it is the one added",
      _ap91("Twin Show S01 1080p", {1}, [{"hash": "h2", "name": "Twin.Show.S01.1080p", "seasons": [1]}]) == "h2")
check("packs: the group's release of another year is not the one added",
      _ap91("Twin Show 2016 S01 1080p-grp", {1}, _added91) == "")
_ns91.update(time=__import__("time"), SERIES_TWINS={})
_held91 = [0]
_ns91["_held_series_number"] = lambda title: _held91[0]


class _Twins91:
    asked = 0

    def tmdb(self, path, timeout=20, **k):
        _Twins91.asked += 1
        if path.startswith("/search"):
            return {"results": [{"id": 1, "name": "Twin Show", "first_air_date": "1985-01-01", "vote_count": 50},
                                {"id": 2, "name": "Twin Show", "first_air_date": "2016-01-01", "vote_count": 50},
                                {"id": 3, "name": "Twin Show!", "first_air_date": "2001-01-01", "vote_count": 1},
                                {"id": 4, "name": "Twin Show Junior", "first_air_date": "2003-01-01", "vote_count": 50}]}
        return {"number_of_seasons": {"/tv/1": 7, "/tv/2": 5, "/tv/3": 1}[path]}


_ns91["local"] = lambda: type("L", (), {"lib": _Twins91()})
_st91 = _ns91["series_twins"]
_a91 = _st91("Twin Show")
check("twins: not held and no number: every series of the name, none its own",
      _a91[0] is None and [r["id"] for r in _a91[1]] == [1, 2])
_held91[0] = 2
_b91 = _st91("Twin Show")
check("twins: a series held since the last asking is known at once, without asking again",
      _b91[0]["id"] == 2 and [r["id"] for r in _b91[1]] == [1] and _Twins91.asked == 4)
_held91[0] = 0
check("twins: the number given says which",
      _st91("Twin Show", 1)[0]["id"] == 1)
check("twins: a little-known series of the name counts when it is the one meant",
      _st91("Twin Show", 3)[0]["id"] == 3 and len(_st91("Twin Show", 3)[1]) == 2)
check("twins: a number the name does not lead to is not judged",
      _st91("Twin Show", 99) == (None, []))
check("packs: another group's, or other seasons of the group, are not",
      _ap91("Twin Show S01 1080p BluRay-oth", {1}, _added91) == ""
      and _ap91("Twin Show S02 1080p BluRay-grp", {2}, _added91) == "")
import pd_torrents as _pt91
_one91 = {"films": [{"kind": "episode", "title": "Twin Show", "season": 1}]}
check("unlink: a pack of one programme's episodes can go",
      not _pt91.carries_others(_one91, _pt91._plain("Twin Show")))
check("unlink: a pack holding a film or another programme stays",
      _pt91.carries_others({"films": _one91["films"] + [{"kind": "film", "title": "A Film"}]}, _pt91._plain("Twin Show"))
      and _pt91.carries_others({"films": _one91["films"] + [{"kind": "episode", "title": "Other"}]}, _pt91._plain("Twin Show")))

# ---- a catalogue that did not answer hides a search for a minute, an answer stays the hour
import threading as _th91, time as _ti91


class _NoCatalogue91:
    def tmdb(self, *a, **k):
        raise OSError("no answer")

    def db(self):
        raise OSError("no library")


class _EmptyCatalogue91(_NoCatalogue91):
    def tmdb(self, *a, **k):
        return {"results": []}


def _asked91(lib, words):
    st.by_name_soon(lib, words, shows=True)
    for _t in _th91.enumerate():
        if _t.name == "palladium-by-name":
            _t.join(5)
    return _ti91.time() - st.FOUND_BY_NAME["tv|" + words][0]


check("catalogue: no answer is asked again in a minute",
      _asked91(_NoCatalogue91(), "rule91 failed") >= 3600 - st.NO_ANSWER_FOR - 1)
check("catalogue: an empty answer is kept the hour",
      _asked91(_EmptyCatalogue91(), "rule91 empty") < 60)

# ---- an episode number of four digits is that number, not its first three
import pd_library as _pl95
check("file number: S06E0013 is episode 13", _pl95.file_episode("Show.S06E0013.1080p.mp4", 6) == 13)
check("file number: S06E13 and 6x13 still read", _pl95.file_episode("Show.S06E13.mp4", 6) == 13
      and _pl95.file_episode("Show 6x13.mp4", 6) == 13)
check("file number: another season says nothing", _pl95.file_episode("Show.S06E0013.mp4", 5) is None)

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

# ---- web video through yt-dlp: own folder, pieces in temp, copy protection said plainly
import pd_webget as _wg1
_cmd1 = _wg1.command(["yt"], "https://x.se/v", "D:\\Download", "C:\\t", "ff.exe")
_job1 = {"title": "", "file": "", "got": 0, "size": 0}
for _l1 in ("@@T A film", "@@P 50 200", "@@P 100 NA", "[info] x", "@@F D:\\Download\\A film\\A film.mkv"):
    _wg1.read_line(_job1, _l1)
check("a web video: one per folder, pieces kept apart, progress and path read, DRM refused",
      _cmd1[0] == "yt" and _cmd1[-2:] == ["--", "https://x.se/v"] and "--no-playlist" in _cmd1
      and _cmd1[_cmd1.index("-o") + 1].startswith("%(title).120B/")
      and "temp:C:\\t" in _cmd1 and _cmd1[_cmd1.index("--ffmpeg-location") + 1] == "ff.exe"
      and _job1 == {"title": "A film", "file": "D:\\Download\\A film\\A film.mkv",
                    "got": 100, "size": 200, "count": 1}
      and _wg1.why_failed("ERROR: [Amazon] abc: This video is DRM protected").startswith("copy-protected")
      and _wg1.why_failed("x\nERROR: Unsupported URL: https://a.b") == "no video found at that address"
      and _wg1.why_failed("ERROR: [svt] 1: Some other thing") == "Some other thing"
      and _wg1.is_address("https://x.se/v") and not _wg1.is_address("file:///c:/x")
      and not _wg1.is_address("x.se") and _wg1.start("nope", "D:\\", ["yt"]) == {"why": "not a web address"}
      and _wg1.start("https://x.se/v", "D:\\", []).get("why", "").startswith("yt-dlp is not here"))

# ---- music videos: own folder, a mix up to 50 with covers, never a film, last on Start
import pd_library as _lb2, pd_localapi as _la2, sqlite3 as _sq2
_mix2 = _wg1.command(["yt"], "https://www.youtube.com/watch?v=a&list=RDa", "D:\\M", "C:\\t", music=True)
_one2 = _wg1.command(["yt"], "https://www.youtube.com/watch?v=a", "D:\\M", "C:\\t", music=True)
_film2 = _wg1.command(["yt"], "https://www.youtube.com/watch?v=a&list=RDa", "D:\\M", "C:\\t")
_db2 = _sq2.connect(":memory:")
_db2.executescript("CREATE TABLE item (id TEXT, type TEXT); CREATE TABLE file (item_id TEXT, path TEXT);"
                   "CREATE TABLE extra (item_id TEXT, parent TEXT);"
                   "INSERT INTO item VALUES ('f','movie'),('m','movie');"
                   "INSERT INTO file VALUES ('f','D:\\Download\\Film (2004)\\Film.mkv'),"
                   "('m','D:\\Download\\Music videos\\A - B\\A - B.mkv');")
_films2 = [r[0] for r in _db2.execute("SELECT id FROM item WHERE type='movie' AND " + _la2.NOT_EXTRA)]
_music2 = [r[0] for r in _db2.execute("SELECT id FROM item WHERE " + _la2.MUSIC_VIDEO)]
_names2 = [i for i, _ in _la2.LocalAPI.start_row_names(_la2.LocalAPI.__new__(_la2.LocalAPI))]
check("a music video: its folder, a mix's 50 with covers, off the film lists, last on Start",
      "--yes-playlist" in _mix2 and _mix2[_mix2.index("--playlist-end") + 1] == "50"
      and "--no-playlist" in _one2 and "--no-playlist" in _film2
      and "thumbnail:%(title).120B/cover.%(ext)s" in _one2 and "--write-thumbnail" not in _film2
      and _mix2[_mix2.index("--encoding") + 1] == "utf-8"
      and _film2[_film2.index("-f") + 1].startswith("bv*[vcodec!^=av01]+ba/")
      and _lb2.is_music_video("D:\\Download\\Music videos\\A - B\\A - B.mkv")
      and _lb2.is_music_video("/srv/music videos/x/x.mkv")
      and not _lb2.is_music_video("D:\\Download\\Music videos of 1984 (1984)\\x.mkv")
      and _films2 == ["f"] and _music2 == ["m"]
      and _names2[-1] == "music" and _la2.LocalAPI.start_more("music") is None)

# ---- a music video's cover is its poster and its backdrop: the app's Start card draws the backdrop
import tempfile as _tf4, shutil as _sh4
_root4 = _tf4.mkdtemp(prefix="pd-music-")
try:
    _dl4 = os.path.join(_root4, "Download")
    _mv4 = os.path.join(_dl4, "Music videos", "A - B")
    os.makedirs(_mv4)
    open(os.path.join(_mv4, "A - B.mkv"), "wb").write(b"\0" * 2048)
    open(os.path.join(_mv4, "cover.jpg"), "wb").write(b"\xff\xd8" + b"\0" * 6000)
    _lib4 = _lb2.Library(_root4)
    _cfg4 = _lib4.config()
    _cfg4["mixed"] = [_dl4]
    _lib4.save_config(_cfg4)
    _lib4.scan(probe=False, identify=False)
    _c4 = _lib4.db()
    _r4 = _c4.execute("SELECT id, title, year, poster, backdrop, identified FROM item").fetchone()
    # one indexed before backdrops were set: the next scan fills it in
    _c4.execute("UPDATE item SET backdrop=NULL")
    _c4.commit()
    _c4.close()
    _lib4.scan(probe=False, identify=False)
    _c4 = _lib4.db()
    _back4 = _c4.execute("SELECT backdrop FROM item").fetchone()[0]
    _c4.close()
    _want4 = "file:" + os.path.join(_mv4, "cover.jpg")
    check("a music video: its own name, no year, its cover as poster and backdrop, filled in later too",
          _r4 is not None and _r4["title"] == "A - B" and not _r4["year"] and _r4["identified"] == 1
          and _r4["poster"] == _want4 and _r4["backdrop"] == _want4 and _back4 == _want4
          and _lib4.artwork(_want4, "w780") == _want4[5:])
finally:
    _sh4.rmtree(_root4, ignore_errors=True)

# ---- music video playlists: made, named, filled in order, deleted; an unreadable file is left alone
import pd_music as _pm5
_root5 = _tf4.mkdtemp(prefix="pd-playlists-")
try:
    _pm5.use_data_dir(_root5)
    _a5 = _pm5.change("create", name="  Summer  ", keys=["k1", "k2", "k1"])
    _id5 = _a5["id"]
    _pm5.change("set", _id5, keys=["k3", "k1"])
    _pm5.change("rename", _id5, name="Evening")
    _b5 = _pm5.change("create", name="Gone")
    _pm5.change("delete", _b5["id"])
    _now5 = _pm5.load()
    _noname5 = _pm5.change("create", name=" ")
    with open(os.path.join(_root5, "music_playlists.json"), "w", encoding="utf-8") as _f5:
        _f5.write("[{broken")
    _held5 = _pm5.change("create", name="New")
    _still5 = open(os.path.join(_root5, "music_playlists.json"), encoding="utf-8").read()
    check("music playlists: made, renamed, put in order, deleted; an unreadable file is not written over",
          _a5["playlists"][0]["keys"] == ["k1", "k2"] and _a5["playlists"][0]["name"] == "Summer"
          and _now5 == [{"id": _id5, "name": "Evening", "keys": ["k3", "k1"]}]
          and "why" in _noname5 and "why" in _held5 and _pm5.load() is None
          and _still5 == "[{broken")
    with open(os.path.join(_root5, "music_playlists.json"), "w", encoding="utf-8") as _f5:
        _f5.write('[{"id": "ab12", "name": "Good", "keys": []}]')
    check("a music round on Continue watching wears Music and its playlist's name, not Shuffle",
          _la2.LocalAPI.round_name("music:ab12", "Shuffle") == "Music · Good"
          and _la2.LocalAPI.round_name("music", "Shuffle") == "Music"
          and _la2.LocalAPI.round_name("f592640", "Evenings") == "Evenings")
finally:
    _pm5.use_data_dir("")
    _sh4.rmtree(_root5, ignore_errors=True)

# ---- music videos: last played first on the row, never on Continue watching
_db3 = _sq2.connect(":memory:")
_db3.row_factory = _sq2.Row
_db3.executescript("CREATE TABLE item (id TEXT, type TEXT); CREATE TABLE file (item_id TEXT, path TEXT);"
                   "CREATE TABLE progress (key TEXT, who TEXT, updated REAL);"
                   "INSERT INTO item VALUES ('a','movie'),('b','movie'),('c','movie'),('f','movie');"
                   "INSERT INTO file VALUES ('a','D:\\M\\Music videos\\a\\a.mkv'),"
                   "('b','D:\\M\\Music videos\\b\\b.mkv'),('c','D:\\M\\Music videos\\c\\c.mkv'),"
                   "('f','D:\\M\\Film\\f.mkv');"
                   "INSERT INTO progress VALUES ('a','me',100),('c','me',300),('b','guest',900);")
_ord3 = _la2.music_order(_db3, "me")
_src3 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"),
             encoding="utf-8").read()
_deck3 = _src3[_src3.index('if path == "/library/onDeck":'):][:1500]
check("music videos: this viewer's last played first, then the rest; off Continue watching",
      _ord3 == ["c", "a", "b"] and "MUSIC_VIDEO" in _deck3)

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
_many61 = _files61 + [{"id": 100 + n, "item_id": "n%d" % n, "episode_id": None, "ctime": _now61 - 60,
                       "duration": 100} for n in range(50)]
check("stills: forty at a time to work on, every one of them when counting",
      len(_la61.stills_order(_prog61, _many61, set(), _now61)) == 40
      and len(_la61.stills_order(_prog61, _many61, set(), _now61, most=None)) == 54)

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

# ---- a shuffle's draw a pack has to bring stays on its collection, the poster showing it
_tv137 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                      "java", "se", "palladium", "tv")
_ma137 = open(os.path.join(_tv137, "MainActivity.kt"), encoding="utf-8").read()
_pa137 = open(os.path.join(_tv137, "PlayerActivity.kt"), encoding="utf-8").read()
_br137 = _ma137[_ma137.find("Drawn from a pack rather than the library"):][:400]
check("an offered draw keeps the collection open and refetches its grid",
      "drawnComing(ctx, browse, drew.media)" in _br137 and "onOpen(" not in _br137
      and "Api.openDrawn.value = Pair(card.shuffleId, drew.media)" in _pa137
      and "browse.loaded = \"\"" in _ma137[_ma137.find("private fun drawnComing"):][:500])

# ---- an episode's release list offers a season pack as a link, beside downloading the episode
_here140 = os.path.dirname(os.path.abspath(__file__))
_js140 = open(os.path.join(_here140, "static", "app.js"), encoding="utf-8").read()
_srv140 = open(os.path.join(_here140, "pd-server.py"), encoding="utf-8").read()
_tv140 = os.path.join(_here140, "android", "app", "src", "main", "java", "se", "palladium", "tv")
_ed140 = open(os.path.join(_tv140, "EpisodeDownloads.kt"), encoding="utf-8").read()
_ma140 = open(os.path.join(_tv140, "MainActivity.kt"), encoding="utf-8").read()
check("season pack rows add the pack as a link; no word search",
      "if (ep.fetchable && (!CFG.guest || CFG.mayFetch))" not in _js140
      and 'body: JSON.stringify({ show: show, id: v.packId })' in _js140
      and '"packId": str(ident) if episode else "",' in _srv140
      and "Api.usePack(ep.title, id = v.packId)" in _ed140
      and "Search the tracker" not in _js140 and "Search the tracker" not in _ed140
      and "if (c.fetchable) fetchingEp = c" not in _ma140)

# ---- the collection editor's list shows a whole series' unlinked seasons, as its page does
_srv143 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_t143 = _srv143[_srv143.find('if path == "/collections/test":'):][:4000]
check("collection editor lists every season of a series held whole",
      "for show in self.whole_series(con, shelf):" in _t143
      and "rows = local()._every_season(con, rows, show)" in _t143)

# ---- a season's page offers its packs, added as a link to that season
_here145 = os.path.dirname(os.path.abspath(__file__))
_js145 = open(os.path.join(_here145, "static", "app.js"), encoding="utf-8").read()
_tv145 = os.path.join(_here145, "android", "app", "src", "main", "java", "se", "palladium", "tv")
_ma145 = open(os.path.join(_tv145, "MainActivity.kt"), encoding="utf-8").read()
_api145 = open(os.path.join(_tv145, "Api.kt"), encoding="utf-8").read()
check("season page: Add pack lists the season's packs and adds one to that season",
      'add.onclick = () => seasonPacks(list, seasonKey, add);' in _js145
      and 'body: JSON.stringify({ show: show, id: p.id, season: number })' in _js145
      and 'Pill("+ Add pack") { addingPack = true }' in _ma145
      and '.put("season", season.index ?: 0), readMs = 60_000)).optString("error", "")' in _api145)

# ---- the web's collection list (/collections/items) shows unlinked seasons too
_srv146 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_i146 = _srv146[_srv146.find('if path in ("/collections", "/collections/items"):'):][:3000]
check("web collection list adds every season of a series held whole",
      "for show in self.whole_series(con, shelf):" in _i146
      and "rows = local()._every_season(con, rows, show)" in _i146)

# ---- the season's pack menu lists the packs added, each with a Remove that unlinks
_here147 = os.path.dirname(os.path.abspath(__file__))
_js147 = open(os.path.join(_here147, "static", "app.js"), encoding="utf-8").read()
_ed147 = open(os.path.join(_here147, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "EpisodeDownloads.kt"), encoding="utf-8").read()
check("season pack menu: added packs with Remove, on web and app",
      "linkedSeasonPacks(added, show, number, list, seasonKey);" in _js147
      and "body: JSON.stringify({ show: show, hash: p.hash, drop: true, unlink: true })" in _js147
      and "Api.removePack(season.title, p.hash)" in _ed147)

# ---- a service's list on the web has Show all, read as one answer of every page
_here149 = os.path.dirname(os.path.abspath(__file__))
_js149 = open(os.path.join(_here149, "static", "app.js"), encoding="utf-8").read()
_la149 = open(os.path.join(_here149, "pd_localapi.py"), encoding="utf-8").read()
check("service Show all: the web asks every page, the server can answer them in one",
      'servicePages[type] = "all";' in _js149
      and 'whole = str(one("page", "1")) == "all"' in _la149
      and "last = self.SERVICE_ALL if whole else page * self.SERVICE_PAGES" in _la149)

# ---- a stopped live channel's pieces are deleted, its numbering kept
import tempfile as _tf150
import pd_live as _pl150
_root150 = _tf150.mkdtemp(prefix="pd-live-")
_was150 = _pl150.ROOT.get("dir")
_pl150.ROOT["dir"] = _root150
_d150 = os.path.join(_root150, "cache", "live", "lvtest")
os.makedirs(_d150)
for _n150 in (7, 8, 9):
    open(os.path.join(_d150, "seg%06d.ts" % _n150), "wb").write(b"x" * 1000)
open(os.path.join(_d150, "index.m3u8"), "w").write("#EXTM3U")
_pl150.clear("lvtest")
_left150 = sorted(os.listdir(_d150))
_pl150.ROOT["dir"] = _was150
check("a stopped channel keeps one empty piece and carries on numbering after it",
      _left150 == ["seg000009.ts"] and os.path.getsize(os.path.join(_d150, "seg000009.ts")) == 0
      and _pl150.next_number(_left150) == 10)

# ---- the Service menu: My library and In packs marked by default; nothing marked is
#      everything, every service included; marks add up
_js152 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("service sources: house marked by default, In packs listed, none marked is every service",
      'const serviceWanted = { movie: SRC_HOUSE, show: SRC_HOUSE };' in _js152
      and 'serviceList.splice(at + 1, 0, [SRC_PACKS, "In packs"]);' in _js152
      and 'const ids = everything ? (await servicesFor()).map(([v]) => v).filter((v) => +v > 0)' in _js152
      and 'serviceWanted[type] = "";' not in _js152
      and 'serviceWanted[toTab[0]] = "";' not in _js152)

# ---- Show all counts up while it loads, a page at a time, and stops when left
_js153 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("Show all loads page by page with a count that counts up",
      "while (st.at < ids.length) {" in _js153
      and 'if (said === "pause") st.paused = true;' in _js153
      and "list: every ? inServiceOrder(st.out.slice()) : st.out" in _js153
      and 'const loadingWords = (n, total) => "Loading all: "' in _js153)

# ---- every catalogue card carries the catalogue's popularity, so lists read a service
#      at a time can be put back in order
_la153 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"),
              encoding="utf-8").read()
check("catalogue cards carry popularity", _la153.count("cards.append(ranked(") == 4)

# ---- nothing marked loads everything at once, counting up while it comes
_js154 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("nothing marked loads every title at once",
      'if (everything) servicePages[type] = "all";' in _js154
      and 'counter = draw(mine ? sofar() : { list: [], total: 0 }, "loading");' in _js154)

# ---- Pause beside the count while everything loads; Continue carries on from there
_js155 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("loading all has Pause, and Continue carries on where it stopped",
      'if (allPaused) return "pause";' in _js155 and "h.appendChild(pause);" in _js155
      and "const st = every && allRead && allRead.key === titles ? allRead" in _js155
      and "if (allRead) allRead.paused = false;" in _js155)

# ---- another order of everything already read is a sort, not a reload
_js156 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("a new order of everything read sorts what is here",
      "if (every && allDone && allDone.key === titles) {" in _js156
      and "allDone = { key: st.key, list: st.out.slice(), total: st.total };" in _js156)

# ---- a new order while everything loads shows at once, and loading carries on
_js157 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("loading all draws what came in the order chosen, and again as more comes",
      "const sofar = () => ({ list: inServiceOrder(" in _js157
      and 'if (pages === 3 || pages % 25 === 0) counter = draw(sofar(), "loading");' in _js157
      and "const titles = catalogueKey(type, providers);" in _js157)

# ---- every service read one at a time: no capped total, the count is what came
_js158 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("every service: the total is what came, and the count says which service",
      "if (many && !paused) st.total = st.out.length;" in _js158
      and "if (!st.total) st.total = (await ask(providers, 1)).totalSize" not in _js158
      and "where = at;" in _js158)

# ---- this house's titles and the services' are sorted as one list
_js159 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("library and catalogue sorted together",
      "const shown = inServiceOrder(got.list.concat(" in _js159
      and "ours.filter((x) => !fromCat.has(String(x.ratingKey)))));" in _js159)

# ---- a poster names a friend's server, never this house's own copy carrying on
_js160 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("the copy's name is not on every poster while the main server is off",
      """(srv && srv.id !== "__copy" ? '<div class="from">'""" in _js160)

# ---- told from the Start tab: web marks on the posters, the app's hold, one endpoint
_here165 = os.path.dirname(os.path.abspath(__file__))
_js165 = open(os.path.join(_here165, "static", "app.js"), encoding="utf-8").read()
_st165 = open(os.path.join(_here165, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "StartScreen.kt"), encoding="utf-8").read()
_srv165 = open(os.path.join(_here165, "pd-server.py"), encoding="utf-8").read()
check("Start feedback: web marks, app hold, a guest may tell it too",
      "strip.appendChild(startMarks(card(it), it))" in _js165
      and 'fetch("/start/feedback",' in _js165
      and "onHold = { StartChoice.held.value = m }" in _st165
      and '"/start/feedback",' in _srv165.split("GUEST_MAY_POST", 1)[1][:4000] or
      '"/start/feedback",' in _srv165[_srv165.find('"/collections/for", "/collections/mark",'):][:300])

# ---- each viewer chooses the tab they open on: Home, the default, or Start
_here168 = os.path.dirname(os.path.abspath(__file__))
_js168 = open(os.path.join(_here168, "static", "app.js"), encoding="utf-8").read()
_set168 = open(os.path.join(_here168, "static", "settings.js"), encoding="utf-8").read()
_ma168 = open(os.path.join(_here168, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "MainActivity.kt"), encoding="utf-8").read()
_srv168 = open(os.path.join(_here168, "pd-server.py"), encoding="utf-8").read()
check("Opens on: Start by default, Home when chosen - web, app and the server",
      'const tab = CFG && CFG.startTab === "home" ? "home" : "start";' in _js168
      and _js168.count("go(firstTab());") == 1
      and "await post(\"/settings\", { startTab: val });" in _set168
      and 'SettingsChoices("Opens on"' in _ma168
      and 'return "home" if said == "home" else "start"' in _srv168
      and 'cfg["startTab"] = self.start_tab()' in _srv168)

# ---- Start laid out as the services lay theirs: a hero that turns, chips, a row a mood
_here170 = os.path.dirname(os.path.abspath(__file__))
_js170 = open(os.path.join(_here170, "static", "app.js"), encoding="utf-8").read()
_st170 = open(os.path.join(_here170, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "StartScreen.kt"), encoding="utf-8").read()
check("Start: hero carousel, chips and a paged row per mood - web and app",
      "page.appendChild(startCarousel(slides, seq));" in _js170
      and "rows.forEach((r) => page.appendChild(startShelf(r)));" in _js170
      and "StartCarousel(slides, onOpen, play, onFocus =" in _st170
      and "StartShelfRow(r, onOpen, play, onMore)" in _st170
      and 'api("/library/start", { rows: 1,' in _js170)

# ---- the Start tab as it is: a tab on web and app, the server's rows, no Continue
#      watching, Reload and Fresh/Classic in its chips
_here171 = os.path.dirname(os.path.abspath(__file__))
_js171 = open(os.path.join(_here171, "static", "app.js"), encoding="utf-8").read()
_ix171 = open(os.path.join(_here171, "static", "index.html"), encoding="utf-8").read()
_tv171 = os.path.join(_here171, "android", "app", "src", "main", "java", "se", "palladium", "tv")
_ma171 = open(os.path.join(_tv171, "MainActivity.kt"), encoding="utf-8").read()
_st171 = open(os.path.join(_tv171, "StartScreen.kt"), encoding="utf-8").read()
_ap171 = open(os.path.join(_tv171, "Api.kt"), encoding="utf-8").read()
check("Start: a tab on web and app, rows from the server, no buttons, no Continue watching",
      'data-view="start"' in _ix171 and 'else if (view === "start") viewStart();' in _js171
      and 'tabPill("start", "Start")' in _ma171 and "StartScreen(onOpen)" in _ma171
      and 'json("/local/library/start?rows=1&kind=" + kind' in _ap171
      and "function startChips(" not in _js171 and 'item(key = "chips")' not in _st171
      and "Continue watching" not in _st171
      and "/library/onDeck" not in _js171[_js171.find("async function viewStart"):_js171.find("function startCarousel")])

# ---- the app's Start rows move as Home's: a sideways row, not pages with arrows
_st172 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src",
                           "main", "java", "se", "palladium", "tv", "StartScreen.kt"),
              encoding="utf-8").read()
check("app Start rows scroll sideways like Home's",
      "androidx.compose.foundation.lazy.LazyRow(" in _st172
      and "StartCard(r.list[i], cardW, { m -> mark(m); onOpen(m) }," in _st172)

# ---- the app's hero: arrows for a remote, a swipe for a finger
_st173 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src",
                           "main", "java", "se", "palladium", "tv", "StartScreen.kt"),
              encoding="utf-8").read()
check("app hero: swiped on a phone or tablet, arrows only on a television",
      "detectHorizontalDragGestures(" in _st173 and "if (tv) {" in _st173
      and "if (tv || slides.size < 2) Modifier else Modifier.pointerInput(slides)" in _st173)

# ---- the chosen tab from the first frame: remembered on the device, web and app
_here174 = os.path.dirname(os.path.abspath(__file__))
_js174 = open(os.path.join(_here174, "static", "app.js"), encoding="utf-8").read()
_ix174 = open(os.path.join(_here174, "static", "index.html"), encoding="utf-8").read()
_ma174 = open(os.path.join(_here174, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "MainActivity.kt"), encoding="utf-8").read()
check("the page and the app open on the chosen tab from the start, not Home and then it",
      'class="tab active">Home' not in _ix174
      and 'localStorage.getItem("palladium-first-tab")' in _js174
      and "Browse().also { it.tab = Api.lastStartTab(ctx) }" in _ma174)

# ---- the Service menu names this house's two, even before the list of services has come
_js175 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"),
              encoding="utf-8").read()
check("Service menu says My library and In packs, never -1 + -2",
      'names: (serviceList || []).concat([[SRC_MINE, "My library"], [SRC_PACKS, "In packs"]])' in _js175)

# ---- Start's cards carry no "In a pack" mark
_here176 = os.path.dirname(os.path.abspath(__file__))
_js176 = open(os.path.join(_here176, "static", "app.js"), encoding="utf-8").read()
_st176 = open(os.path.join(_here176, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "StartScreen.kt"), encoding="utf-8").read()
check("no In a pack on Start's cards", "In a pack" not in _js176[_js176.find("function startCard"):]
      [:2500] and "In a pack" not in _st176)

# ---- the app's search box stands on Start too
_ma177 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src",
                           "main", "java", "se", "palladium", "tv", "MainActivity.kt"),
              encoding="utf-8").read()
check("app: search on the Start tab",
      'search = if (browse.tab in setOf("start", "home", "films", "tv"))' in _ma177
      and 'browse.tab == "start" && browse.query.trim().isEmpty() ->' in _ma177)

# ---- Start is only where the page and the app open; back and coming back go Home
_here180 = os.path.dirname(os.path.abspath(__file__))
_js180 = open(os.path.join(_here180, "static", "app.js"), encoding="utf-8").read()
_ma180 = open(os.path.join(_here180, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "MainActivity.kt"), encoding="utf-8").read()
check("Start only at the opening: back and the end of a film go to Home, web and app",
      _js180.count("go(firstTab());") == 1 and 'if (where !== "monitor" && SCREEN === FIRST_SCREEN) go(firstTab());' in _js180
      and 'BackHandler(enabled = !inContent && browse.tab != "home")' in _ma180
      and "Browse().also { it.tab = Api.lastStartTab(ctx) }" in _ma180
      and "browse.tab = Api.lastStartTab(ctx)" not in _ma180)

# ---- a shuffle's draw that must download first gives way to the next one here, which
#      plays; the one coming in stays next in line
_srv181 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"),
               encoding="utf-8").read()
_src181 = _srv181[_srv181.find("    def ready_first(order, to, usable, ready):"):]
_src181 = _src181[:_src181.find("\n    def shuffle_settle")]
_ns181 = {}
exec("import textwrap\n" + "def ready_first(order, to, usable, ready):\n" +
     "\n".join(l[4:] for l in _src181.split("\n")[1:]), _ns181)
_o181 = ["o1", "o2", "e3", "e4"]
_ns181["ready_first"](_o181, 0, lambda k: True, lambda k: not k.startswith("o"))
_p181 = ["e1", "o2", "e3"]
_ns181["ready_first"](_p181, 0, lambda k: True, lambda k: not k.startswith("o"))
_q181 = ["o1", "o2"]
_ns181["ready_first"](_q181, 0, lambda k: True, lambda k: not k.startswith("o"))
check("a draw that must download gives way to the next one here, in order",
      _o181 == ["e3", "o1", "o2", "e4"] and _p181 == ["e1", "o2", "e3"] and _q181 == ["o1", "o2"]
      and "self.ready_first(order, to, usable, self.ready_now)" in _srv181)

# ---- Shuffle on a series' page and as a row on Start, web and app
_here182 = os.path.dirname(os.path.abspath(__file__))
_js182 = open(os.path.join(_here182, "static", "app.js"), encoding="utf-8").read()
_tv182 = os.path.join(_here182, "android", "app", "src", "main", "java", "se", "palladium", "tv")
_ma182 = open(os.path.join(_tv182, "MainActivity.kt"), encoding="utf-8").read()
_st182 = open(os.path.join(_tv182, "StartScreen.kt"), encoding="utf-8").read()
check("no Shuffle on a series' page, Start's Shuffle row kept, web and app",
      'shuffleDraw("series:" + s.ratingKey, true)' not in _js182
      and 'startShuffle(ctx, "series:" + full.ratingKey, onOpen)' not in _ma182
      and "StartShuffleRow(sh, onOpen)" in _st182 and 'startShuffle(ctx, "coll:" + id, onOpen)' in _st182
      and 'if (it.type === "shelf") return startShuffleCard(it);' in _js182
      and "const go1 = () => shuffleDraw(it.shelf, true);" in _js182)

# ---- Start's rows: edited on the web as Home's are, kept per viewer, followed by the app
_here183 = os.path.dirname(os.path.abspath(__file__))
_js183 = open(os.path.join(_here183, "static", "app.js"), encoding="utf-8").read()
_sv183 = open(os.path.join(_here183, "pd-server.py"), encoding="utf-8").read()
_st183 = open(os.path.join(_here183, "android", "app", "src", "main", "java", "se", "palladium",
                           "tv", "StartScreen.kt"), encoding="utf-8").read()
check("Start's rows editor on the web, the setting kept per viewer, the app following it",
      "startForm(edit, front)" in _js183 and "body: JSON.stringify(body)," in _js183
      and '"startTab", "startRows", "startRowsOff",' in _sv183
      and 'if "startRows" in body:' in _sv183
      and 'if (r.id == "shuffle")' in _st183)

# ---- the app's first resume is its opening: it stays on the tab it opens on
_ma184 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: the first resume is the opening, not a return to Home",
      "!Route.isOpening(cameBack, MainActivity.openedAt)" in _ma184
      and "openedAt = returned.value + 1" in _ma184)

# ---- More like this widens when nothing carries every genre, web and app
_ma185 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: More like this widens by the genre sets that find most",
      "for (size in Route.genreFewer(words.size)) {" in _ma185)

# ---- every file the page loads itself is open to a guest and to the owner from away
import re as _re186
from pd_invites import Invites as _Inv186
_st186 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_css186 = open(os.path.join(_st186, "style.css"), encoding="utf-8").read()
_htm186 = open(os.path.join(_st186, "index.html"), encoding="utf-8").read()
_files186 = {"/" + u.strip("'\"").lstrip("/").split("?")[0]
             for u in _re186.findall(r"url\(([^)]+)\)", _css186) + _re186.findall(r'(?:src|href)="([^"]+)"', _htm186)
             if not u.startswith(("http", "data:", "#"))}
_shut186 = sorted(f for f in _files186 if not (_Inv186.allowed(f) or _Inv186.public(f)))
check("every file the page loads is open to a guest: " + ", ".join(_shut186), not _shut186)

# ---- Start's row headings and taglines share a baseline in the app
_st187 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start headings and taglines on one baseline",
      _st187.count("modifier = Modifier.alignByBaseline())") == 4
      and "Modifier.padding(bottom = 2.dp))" not in _st187)

# ---- back on a scrolled Films or TV list: its top first, Home the press after
_ma188 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: back with a remote goes to the tab and keeps the list where it is; a phone's goes to the top first",
      "if (!remoteHere &&\n            Route.backToTop(" in _ma188.replace("\r\n", "\n")
      and "// and a Films or TV list back at its top, so the next press goes Home" not in _ma188)

# ---- Start's hero says what a title is about, never why it was chosen
_h189 = os.path.dirname(os.path.abspath(__file__))
_st189 = open(os.path.join(_h189, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "StartScreen.kt"), encoding="utf-8").read()
_js189 = open(os.path.join(_h189, "static", "app.js"), encoding="utf-8").read()
check("Start's hero: the summary, no why-chosen line, web and app",
      "r.why))" not in _st189 and "it.summary || r.why" not in _js189
      and "out.add(Triple(m, r.name.uppercase(), m.summary))" in _st189)

# ---- the app's Start: no tick and cross buttons; holding a card or the picture opens the menu
_st190 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start has no tick and cross button; hold opens the menu",
      'Pill("✓ / ✗")' not in _st190 and "onLongPress = { StartChoice.held.value = m })" in _st190
      and "onLongClick = { StartChoice.held.value = m }" in _st190)

# ---- the app's Start on a TV: the picture holds still under the remote, the page at its top
_st191 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start's picture holds still with the remote on it and keeps the page at its top",
      "if (slides.size < 2 || held) return@LaunchedEffect" in _st191
      and "held = tv && it.hasFocus" in _st191
      and "onFocus = { scope.launch { list.scrollToItem(0) } }" in _st191)

# ---- a series' Shuffle stands first on its page's button row, not beside Mark all watched
_h192 = os.path.dirname(os.path.abspath(__file__))
_ma192 = open(os.path.join(_h192, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "MainActivity.kt"), encoding="utf-8").read()
_js192 = open(os.path.join(_h192, "static", "app.js"), encoding="utf-8").read()
check("no Shuffle on a series' page button row, web and app",
      _ma192.count('Pill("⇄ Shuffle"') == 0
      and "mark.appendChild(shuffle);" not in _js192
      and "mark.appendChild(markControl(s, repaintEpisodes));" in _js192)

# ---- the TV's remote starts on the tab the app opened on, not always Home
_ma193 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_l193 = _ma193[_ma193.find("if (remoteHere && !browse.landed) {"):][:900]
check("app on TV: the remote starts on the tab shown",
      "tabFocus[browse.tab]?.requestFocus()" in _l193 and 'tabFocus["home"]' not in _l193)

# ---- the box set reader walks a copy of the tracker's index, not the live one
import pd_boxsets as _bx194


_rows194 = {"a": {"name": "A 5 Film Collection", "seeds": 3}, "b": {"name": "B", "seeds": 1}}
_isb194, _rd194 = _bx194.is_boxset, _bx194.read


def _adds194(row):
    """A search on another thread, landing while the index is walked."""
    _rows194["late%d" % len(_rows194)] = {"name": "x"}
    return False


try:
    _bx194.read = (lambda: {})
    _bx194.is_boxset = _adds194
    _bx194.waiting(_rows194, 0)
    _ok194 = True
except RuntimeError:
    _ok194 = False
finally:
    _bx194.is_boxset, _bx194.read = _isb194, _rd194
check("box set reader: a search adding to the index mid-walk does not stop it",
      _ok194 and "pairs = list(rows.items())" in open(_bx194.__file__, encoding="utf-8").read())

# ---- the app's Start holds still while what has the remote is on screen
_st195 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start scrolls only to bring an off-screen row into view, not to lift the focused one",
      "LocalBringIntoViewSpec provides steady" in _st195
      and "if (top >= 0f && bottom <= containerSize) return 0f" in _st195
      and "val clear = containerSize * 0.14f" in _st195)

# ---- the report board: one list on the main server and the cache
import tempfile as _tf196
import json
import pd_board as _pb196
_m196, _c196 = _tf196.mkdtemp(), _tf196.mkdtemp()


def _put196(root, board, archive):
    for name, rows in (("feedback.jsonl", board), ("feedback-archive.jsonl", archive)):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")


def _get196(root, name):
    with open(os.path.join(root, name), encoding="utf-8") as f:
        return [json.loads(l)["id"] for l in f if l.strip()]


# main: one open request, one filed fault. cache: a request only it saw, and the main
# server's open request, ticked off there later than the main server last touched it
_put196(_m196, [{"id": "m1", "when": 100, "kind": "request", "text": "a"}],
        [{"id": "m2", "when": 90, "kind": "error", "text": "b", "filed": 95}])
_put196(_c196, [{"id": "c1", "when": 200, "kind": "request", "text": "c"}],
        [{"id": "m1", "when": 100, "kind": "request", "text": "a", "filed": 300,
          "changed": 300}])
_pb196.TRADE["at"] = 0
_back196 = _pb196.trade(_c196, lambda p, b: {"rows": _pb196.take(_m196, b["rows"], True)})
check("report board: both machines end with one list, the copy changed last winning",
      _back196 is not None
      and sorted(_get196(_m196, "feedback.jsonl")) == ["c1"]
      and sorted(_get196(_m196, "feedback-archive.jsonl")) == ["m1", "m2"]
      and sorted(_get196(_c196, "feedback.jsonl")) == ["c1"]
      and sorted(_get196(_c196, "feedback-archive.jsonl")) == ["m1", "m2"])
# an unreadable board is not an empty one: nothing is written over it
_bad196 = _tf196.mkdtemp()
os.makedirs(os.path.join(_bad196, "feedback.jsonl"))
check("report board: a board that cannot be read is left alone",
      _pb196.take(_bad196, [{"id": "x", "when": 1}], True) is None)
_srv196 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"),
               encoding="utf-8").read()
check("report board: every change to a row is stamped",
      _srv196.count('row["changed"] = ') >= 2 and 'back["changed"] = int(time.time())' in _srv196
      and 'if path == "/follow/feedback":' in _srv196)

# ---- back from Start's rows lands on the Start tab, not Home
_ma197 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: Start's rows count as content, so back goes to the Start tab first",
      ".onFocusChanged { inContent = it.hasFocus }) {" in _ma197 and "StartScreen(onOpen) { more ->" in _ma197)

import re
# ---- Start's lists never hand one row's slot to another: a focused card released twice
_st198 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
_lines198 = [l for l in _st198.splitlines() if re.search(r"\bitems?\(", l) and "key =" in l]
check("app: every item in Start's lists has a content type of its own",
      len(_lines198) >= 6 and all("contentType" in l for l in _lines198)
      and 'contentType = "row-" + r.id' in _st198)

# ---- on the picture's arrows, the remote's left and right turn it too
_st199 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
_flat199 = _st199.replace("\r\n", "\n")
check("app: left on the left arrow and right on the right arrow turn Start's picture",
      "Key.DirectionLeft) {\n                                at = (at - 1 + slides.size) % slides.size; true" in _flat199
      and "Key.DirectionRight) {\n                                at = (at + 1) % slides.size; true" in _flat199)

# ---- the app keeps the server's Popular order when it joins the pages
_api200 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                            "java", "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
check("app: Popular joins the pages by popularity, not by name",
      'sort.startsWith("popularity") -> ({ m: Media -> m.popularity })' in _api200)

# ---- the app's catalogue mode asks for the house's titles in the catalogue's order
_ma201 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: nothing marked lists the house's titles in the catalogue's order, mixed in",
      "Api.movies(ctx, svcOrder, 0, HOUSE_ALL," in _ma201
      and "browse.grid = Route.inServiceOrder(ours + got.filter" in _ma201
      and "Api.movies(ctx, order, 0, HOUSE_ALL," not in _ma201)

# ---- the kids' row stays out of Start's picture, web and app
_h202 = os.path.dirname(os.path.abspath(__file__))
_js202 = open(os.path.join(_h202, "static", "app.js"), encoding="utf-8").read()
_st202 = open(os.path.join(_h202, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "StartScreen.kt"), encoding="utf-8").read()
check("Start's picture leaves the kids' row out, web and app",
      'if (r.id === "shuffle" || r.id === "kids-movie" || r.id === "music") return;' in _js202
      and 'rows.filter { it.id != "shuffle" && it.id != "kids-movie" && it.id != "music" }' in _st202)

# ---- the open faults of 21 October: each answered where it arose
_h203 = os.path.dirname(os.path.abspath(__file__))
_js203 = open(os.path.join(_h203, "static", "app.js"), encoding="utf-8").read()
_la203 = open(os.path.join(_h203, "pd_localapi.py"), encoding="utf-8").read()
_lv203 = open(os.path.join(_h203, "pd_live.py"), encoding="utf-8").read()
check("a gone title says so, the subtitle panel survives a closed film, a busy file drops one report",
      "That title is not listed here any more." in _js203
      and "if (!S || !S.meta) { box.remove(); return; }" in _js203
      and "except sqlite3.OperationalError:\n            con.rollback()" in _la203.replace("\r\n", "\n")
      and "self.log_watch_quietly(con, key, pos, dur," in _la203
      and 'if not isinstance(said, dict) or said.get("live_status") != "is_live":' in _lv203)
# a reading that moves on keeps what it dropped where a key can still find it
import pd_streaming as _ps203
_root203 = _ps203.STATE["root"]
_tmp203 = __import__("tempfile").mkdtemp()
_ps203.STATE["root"] = _tmp203
_ps203.SEARCHED["rows"] = []
_ps203._keep_searched([{"key": "rtgone203", "title": "Gone"}])
_found203 = _ps203.one_of("rtgone203")
_ps203.STATE["root"] = _root203
_ps203.SEARCHED["rows"] = None
check("a key the new-releases reading dropped still opens its page",
      bool(_found203) and _found203.get("title") == "Gone"
      and "_keep_searched(dropped)" in open(_ps203.__file__, encoding="utf-8").read())

# ---- the Sports row: the chosen sports, a year back by when each event was run
import pd_sports as _sp205
_now205 = time.mktime((2026, 10, 21, 12, 0, 0, 0, 0, -1))
_rows205 = [
    {"id": "1", "name": "NFL 2026 01 05 Bears Vs Packers 1080p WEB h264", "at": _now205},
    {"id": "2", "name": "NFL 2025 11 09 Bears Vs Vikings 1080p WEB h264", "at": _now205},
    {"id": "3", "name": "NFL 2024 09 07 Bears Vs Lions 1080p WEB h264", "at": _now205},
    {"id": "4", "name": "NBA 2026 10 20 Boston Celtics vs Detroit Pistons 1080p WEB h264", "at": _now205},
] + [{"id": "n%d" % i, "name": "NFL 2026 10 %02d Team%d Vs Rams 1080p WEB h264" % (i, i),
      "at": _now205} for i in range(1, 20)]
_ev205 = _sp205.sport_events(_rows205, "nfl", now=_now205)
_titles205 = [e["title"] for e in _ev205]
check("Sports: a year back by the event's own day, the chosen sports only, a sport's page uncapped",
      "Bears vs Packers" in _titles205 and "Bears vs Vikings" in _titles205
      and "Bears vs Lions" not in _titles205 and len(_ev205) == 21
      and _sp205.read_name(_rows205[3]["name"]) is None and _sp205.DAYS == 365)

# ---- a SubRip file's ASS overrides are taken out, not shown as words
import pd_vtt as _vt206
_v206 = _vt206.srt_to_vtt("1\n00:00:01,000 --> 00:00:02,000\n{\\pos(190,200)}Today is ethics day.\n\n"
                          "2\n00:00:03,000 --> 00:00:04,000\n{\\an8}At the top\n")
check("subtitles: ASS overrides gone from the words, a top line kept at the top",
      "{" not in _v206 and "Today is ethics day." in _v206
      and "00:00:03.000 --> 00:00:04.000 line:5%" in _v206
      and "00:00:01.000 --> 00:00:02.000\n" in _v206)

# ---- the Service menu shows each choice's count, web and app; one card a series
_h207 = os.path.dirname(os.path.abspath(__file__))
_js207 = open(os.path.join(_h207, "static", "app.js"), encoding="utf-8").read()
_ma207 = open(os.path.join(_h207, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "MainActivity.kt"), encoding="utf-8").read()
_ap207 = open(os.path.join(_h207, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "Api.kt"), encoding="utf-8").read()
check("Service menu counts on web and app; a series once across both servers",
      "pairs.map(([v, n]) => [v, serviceNamed(type, v, n)])" in _js207
      and "counted = Api.serviceCounts[" in _ma207
      and "Route.oneCardASeries(sortMerged(answer, sort)" in _ap207
      and "return sortLike(once);" in _js207)

# ---- the app is built with R8 and its ahead-of-time profiles; names kept for crash reports
_h208 = os.path.dirname(os.path.abspath(__file__))
_g208 = open(os.path.join(_h208, "android", "app", "build.gradle.kts"), encoding="utf-8").read()
_pr208 = open(os.path.join(_h208, "android", "app", "proguard-rules.pro"), encoding="utf-8").read()
check("app: R8 on, profiles installed, names kept, the Cast provider kept",
      "isMinifyEnabled = true" in _g208 and "androidx.profileinstaller:profileinstaller" in _g208
      and "-dontobfuscate" in _pr208 and "se.palladium.tv.CastOptionsProvider" in _pr208
      and os.path.exists(os.path.join(_h208, "android", "app", "src", "main", "baseline-prof.txt")))

# ---- the app's Service menu: wide enough for one line a service, the count apart
_ma209 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: the Service menu opens 300 wide, names on one line, the count at the end",
      "menuWidth = 300.dp)" in _ma209 and 'Text("%,d".format(count)' in _ma209
      and ".width(menuWidth).heightIn(max = 360.dp)" in _ma209)

# ---- Start's picture: a two-line title takes a line off the summary, so the buttons fit
_st210 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: a two-line title on Start's picture leaves the summary two lines",
      "maxLines = if (titleLines > 1) 2 else 3," in _st210
      and "onTextLayout = { titleLines = it.lineCount }" in _st210)

# ---- Start's cards wear the picture the server names, not one guessed from the key
_st211 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start's pictures by the card's own picture, not by its key",
      '"/art/" + m.ratingKey + "/backdrop"' not in _st211 and _st211.count("m.widePicture()") == 2)

# ---- Start: a row the remote enters goes to one place - replaced, see startRowMove below

# ---- Start's big picture with its shade painted in on the server, the TV drawing one picture
import io as _io213
import pd_shade as _sh213
from PIL import Image as _Img213
_b213 = _io213.BytesIO()
_Img213.new("RGB", (400, 200), (200, 200, 200)).save(_b213, "JPEG")
_o213 = _Img213.open(_io213.BytesIO(_sh213.shaded(_b213.getvalue(), 2.0)))
_px213 = lambda x, y: sum(_o213.getpixel((x, y))) / 3
check("Start's picture: dark on the left and at the foot, clear at the upper right, cut to its shape",
      _o213.size[0] / _o213.size[1] > 1.9 and _px213(5, 20) < 60 and _px213(_o213.size[0] - 5, 20) > 180
      and _px213(_o213.size[0] - 5, _o213.size[1] - 3) < 60)
_st213 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start's picture asks for its shade painted in; cards carry their title beneath, no shade",
      'shade=hero&a=' in _st213 and "ComposeShader" not in _st213
      and "Brush.verticalGradient(0f to Color(0x0005070A)" not in _st213
      and "scrollAnimationSpec" in _st213)

# ---- Start: rows do not slide at every press; pressing Start again goes back to the top
_h214 = os.path.dirname(os.path.abspath(__file__))
_st214 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "StartScreen.kt"), encoding="utf-8").read()
_ma214 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv",
                           "MainActivity.kt"), encoding="utf-8").read()
check("app: Start's rows scroll only when needed; Start pressed again resets page and rows",
      _st214.count("LocalBringIntoViewSpec provides StartChoice.inARow") == 2
      and 'if (chosen == "start" && browse.tab == "start")' in _ma214
      and "LaunchedEffect(top) { if (top > 0) runCatching { rowState.scrollToItem(0) } }" in _st214)

# ---- Start: the slides are springs - a tween restarted every frame by the scroll eased out for seconds
check("app: Start's page and rows slide on springs, not tweens",
      _st214.count("Slide.along") == 1 and _st214.count("Slide.down") == 1
      and "core.tween(" not in _st214)
# ---- every tab slides the same: the app's root, the grid and the rows along
check("app: Home slides on Start's springs to the TV default's own places; the grid on Start's spring",
      "Slide.keeping(androidx.compose.foundation.gestures.LocalBringIntoViewSpec.current," in _ma214
      and "Route.calmAlong(offset, size, containerSize, room)" in _ma214
      and "base.calculateScrollDistance(offset, size, containerSize)" in open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Ui.kt"), encoding="utf-8").read()
      and "runCatching { grid.animateScrollBy(by, Slide.down) }" in _ma214
      and "row.toFloat() else -row.toFloat(), Slide.down) }" in _ma214
      and "override val scrollAnimationSpec = Slide.down" in _ma214)

# ---- a pack with a download coming in is started again when it was left stopped
import pd_torrents as _pt215


class _QB215:
    calls = []

    def __init__(self, cfg): pass

    def info(self, h): return {"state": _QB215.state}

    def files(self, h): return [{"index": 0, "priority": 1}, {"index": 1, "priority": 0}]

    def priority(self, h, idx, v): pass

    def start(self, h): _QB215.calls.append(("start", h))

    def _call(self, path, fields=None): _QB215.calls.append((path, fields.get("hashes")))


_keep215 = (_pt215.QB, _pt215.STATE.get("data"), _pt215.STATE.get("root"), _pt215.STATE.get("held"))
_pt215.QB = _QB215
_pt215.STATE["root"] = ""
_pt215.STATE["held"] = False
try:
    def _guard215(state, rows):
        _QB215.state, _QB215.calls = state, []
        _pt215.STATE["data"] = {"config": {}, "packs": [{"hash": "h1"}], "downloads": rows}
        _pt215._guard_packs()
        return _QB215.calls
    _on215 = [{"hash": "h1", "index": 0, "state": "downloading"}]
    check("a stopped pack with a download coming in is started",
          _guard215("stoppedDL", _on215) == [("start", "h1")])
    check("a running pack with a download coming in is left running",
          _guard215("downloading", _on215) == [])
    check("a pack whose downloads are all done is left stopped",
          _guard215("stoppedUP", [{"hash": "h1", "index": 0, "state": "done"}]) == [])
    _pt215.STATE["held"] = True
    check("a stopped pack stays stopped while the tracker is held",
          _guard215("stoppedDL", _on215) == [])
finally:
    _pt215.QB, _pt215.STATE["data"], _pt215.STATE["root"], _pt215.STATE["held"] = _keep215

# ---- the library is asked once which keys of a round are held, not once per key
_srv216 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("shuffle: held keys read once per round, not per key",
      "not in self.held_keys(" not in _srv216 and _srv216.count("held = self.held_keys(con, ") >= 2)

# ---- Home: up and down go to the card in line, as on Start, not to a remembered one
check("app: Home's up and down are the nearest card in line",
      "moveTo(browse.shelfAsk[shelf - 1])" not in _ma214
      and "moveTo(browse.shelfAsk[shelf + 1])" not in _ma214
      and "if (shelf > 0) false" in _ma214
      # Show more padded as a poster is: lower, it was what down found
      and "vertical = if (LocalConfiguration.current.screenHeightDp < 560) 4.dp else 8.dp)" in _ma214)

# ---- a download somebody asked for is said when it is in the library; the shuffle's are not
import pd_torrents as _pt218
_keep218 = (_pt218.STATE.get("data"), _pt218.STATE.get("root"), _pt218.arrived)
_pt218.STATE["root"] = ""
try:
    _pt218.STATE["data"] = {"config": {}, "packs": [], "downloads": [
        {"id": "a", "key": "k1", "token": "t1", "state": "done", "done": 200, "title": "One"},
        {"id": "b", "key": "k2", "token": "t1", "state": "done", "done": 200, "title": "Two", "auto": True},
        {"id": "c", "key": "k3", "token": "t2", "state": "done", "done": 200, "title": "Three"},
        {"id": "d", "key": "k4", "token": "t1", "state": "done", "done": 50, "title": "Four"},
        {"id": "e", "key": "k5", "token": "t1", "state": "downloading", "title": "Five"},
        {"id": "f", "key": "k6", "token": "t1", "state": "done", "done": 200, "title": "Six"},
        {"id": "g", "key": "k7", "token": "t1", "state": "done", "done": 200, "title": "Show",
         "kind": "episode", "season": "2", "episode": "9"}]}
    _pt218.arrived = lambda key: None if key == "k6" else "L" + key
    _r218 = _pt218.ready_for({"t1"}, 100)
    check("downloads: ready is one's own, asked by hand, since then and in the library",
          [r["id"] for r in _r218] == ["a", "g"] and _r218[1]["title"] == "Show S02E09"
          and _r218[0]["key"] == "Lk1")
finally:
    _pt218.STATE["data"], _pt218.STATE["root"], _pt218.arrived = _keep218

# ---- a torrent done before its file: the file is read again, not kept unfinished
import pd_torrents as _pt219


class _QB219:
    files_read = []

    def __init__(self, cfg): pass

    def _call(self, path, timeout=10):
        return json.dumps([{"hash": "h9", "progress": 1.0, "amount_left": 0, "size": 100,
                            "state": "stalledUP", "save_path": "D:\\Download",
                            "downloaded": 100}]).encode()

    def files(self, h):
        _QB219.files_read.append(h)
        return [{"name": "film.mkv", "progress": _QB219.p, "priority": 1}]


_keep219 = (_pt219.QB, _pt219.STATE.get("data"), _pt219.STATE.get("root"),
            dict(_pt219.FILE_LISTS), set(_pt219.UNFINISHED["paths"]))
_pt219.QB = _QB219
_pt219.STATE["root"] = ""
_pt219.STATE["data"] = {"config": {}, "packs": [], "downloads": []}
_pt219.FILE_LISTS.clear()
try:
    _QB219.p = 0.9999
    _pt219._read_unfinished()
    _short219 = any("film.mkv" in p for p in _pt219.UNFINISHED["paths"])
    _QB219.p = 1.0
    _pt219._read_unfinished()
    check("downloads: a torrent finished before its file is read again and its film is whole",
          _short219 and not any("film.mkv" in p for p in _pt219.UNFINISHED["paths"])
          and len(_QB219.files_read) == 2)
finally:
    (_pt219.QB, _pt219.STATE["data"], _pt219.STATE["root"]) = _keep219[:3]
    _pt219.FILE_LISTS.clear(); _pt219.FILE_LISTS.update(_keep219[3])
    _pt219.UNFINISHED["paths"] = _keep219[4]

# ---- a download taken in is matched at once, and a tracker one is no longer "coming"
_lib220 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_library.py"), encoding="utf-8").read()
_trk220 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_tracker.py"), encoding="utf-8").read()
check("downloads: taken in, a film is matched for its poster at once and is no longer coming",
      "self.identify_pending(limit=took + 5)" in _lib220
      and 'pd_torrents.UNFINISHED["paths"].discard(os.path.normcase(os.path.normpath(p)))' in _trk220)

# ---- an episode or season out already with no source is Missing, not Upcoming
import pd_localapi as _la222
_mn222 = _la222.LocalAPI.missing_now
check("series: aired with nothing to get it from is missing; to come, today, on the tracker or undated is not",
      _mn222("2026-10-01", 0, "2026-10-22") and not _mn222("2026-10-01", 2, "2026-10-22")
      and not _mn222("2026-10-22", 0, "2026-10-22") and not _mn222("2026-11-01", 0, "2026-10-22")
      and not _mn222("", 0, "2026-10-22"))
_src222 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("series: every upcoming episode and season card says whether it is missing",
      _src222.count("self.missing_now(") == 3)

# ---- search: a lone number is tried spelt out too, and a film not out yet is upcoming
import pd_streaming as _ps223
check("search: \"dune 3\" is asked as dune three and dune iii as well",
      _ps223.number_spellings("dune 3") == ["dune 3", "dune three", "dune iii"]
      and _ps223.number_spellings("alien") == ["alien"])
_c223 = _ps223.item({"key": "rtx", "title": "Later", "released": "2099-12-15"})
_d223 = _ps223.item({"key": "rty", "title": "Earlier", "released": "2001-01-01"})
check("search: a film not out yet is upcoming and nothing to ask for; one out is to ask for",
      _c223["upcoming"] is True and _c223["askable"] is False
      and _d223["upcoming"] is False and _d223["askable"] is True)


class _Lib223:
    asked = []

    def tmdb(self, path, timeout=6, query="", include_adult="false"):
        _Lib223.asked.append(query)
        if query == "dune three":
            return {"results": [
                {"id": 1, "title": "Dune: Part Three", "release_date": "2099-12-15",
                 "poster_path": "/p.jpg", "vote_count": 0, "popularity": 60.0},
                {"id": 2, "title": "Dune Thing", "release_date": "2099-12-15",
                 "poster_path": "/q.jpg", "vote_count": 0, "popularity": 1.0},
                {"id": 3, "title": "Old Dune", "release_date": "1990-01-01",
                 "poster_path": "/r.jpg", "vote_count": 5, "popularity": 50.0}]}
        return {"results": []}


_held223 = _ps223._held
_ps223._held = lambda lib: ({}, {})
_ps223.FOUND_BY_NAME.pop("dune 3", None)
try:
    _got223 = _ps223.by_name(_Lib223(), "dune 3")
finally:
    _ps223._held = _held223
    _ps223.FOUND_BY_NAME.pop("dune 3", None)
check("search: dune 3 finds the film not out yet that people are waiting for, and not the obscure ones",
      [r["title"] for r in _got223] == ["Dune: Part Three"] and "dune three" in _Lib223.asked)

# ---- a film's Show more: the words take the remote first and are read through
check("app: Show more's words take the remote first and up and down read through them",
      "LaunchedEffect(Unit) { runCatching { readAt.requestFocus() } }" in _ma214
      and "Route.readStep(down, readScroll.value.toFloat()," in _ma214)

# ---- a miniseries in parts is one series, its parts the episodes, its year kept
import pd_library as _pl224
_m224 = _pl224.parse_miniseries(
    "F:\\Download\\Dune.The.Miniseries.Part2.2000.1080p.BluRay.x264-PFa\\pfa-dune.p2.1080p.mkv")
check("library: a miniseries' part is episode n of its series, with the year to match it by",
      _m224 == ("Dune", 1, 2, 2000)
      and _pl224.parse_miniseries("X:\\Films\\Some.Saga.Miniseries.Part.3.mkv")[:3] == ("Some Saga", 1, 3)
      and _pl224.parse_miniseries("X:\\Films\\Harry.Potter.Deathly.Hallows.Part.2.2011.mkv") is None
      and _pl224.parse_miniseries("X:\\Films\\Kill.Bill.Vol.1.2003.mkv") is None)

# ---- app: a search on any tab shows everything it found, the tab's kind first
check("app: a search shows everything it found on every tab",
      "browse.grid = Route.tabFirst(found.map { it.type }, tab).map { found[it] }" in _ma214
      and '"films" -> found.filter { it.type == "movie" }' not in _ma214)

# ---- a screen can be told to carry on a shuffle, not only to play one title
_srv225 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("screens: /screen/play takes a shuffle to carry on, and the screen draws it",
      'NOTICE["shelf"] = shelf' in _srv225 and '"shelf": (NOTICE.get("shelf") or "") if fresh else ""' in _srv225
      and "startShuffle(ctx, word.shelf, onOpen)" in _ma214)

# ---- app search waits as the web page does, and asks again when a server was silent
_api226 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
check("app: a search waits fifteen seconds for each server and asks again when one said nothing",
      "const val SEARCH_PATIENCE = 15_000L" in _api226
      and "if (answered < Servers.merged(ctx).size) searchFilling = true" in _api226)

# ---- a film page's More like this fills the room under the words, the row at the foot
check("app: More like this is as large as the room under the film's words allows",
      "tvPage && roomForShelf -> Route.likeWide(" in _ma214
      and "Route.likeTen(maxWidth.value, POSTER_SIDES)" in _ma214
      and "if (onTv()) StartChoice.still" in _ma214
      and "shelfFrom = it.positionInParent().y" in _ma214
      and "Route.footGap(pageTall, shelfFrom / dpx, shelfTall / dpx, 14f)" in _ma214
      and "Route.footGap(pageTall, shelfFrom / dpx, shelfTall / dpx, 14f)" in _ma214)

# ---- app search: films and series under their own headings on every tab, TV first on TV
check("app: a search splits films and TV shows on every tab, the TV tab's programmes first",
      'listOf("TV shows" to tv, "Films" to films)' in _ma214
      and "if (Route.tvFirst(browse.tab))" in _ma214)

# ---- a title the server tells a screen to play starts with the viewer's subtitles
check("screens: a title played by command keeps the subtitles last chosen for it, with its details asked again",
      "play(ctx, use, word.at, use.startSub())" in _ma214 and "if (full?.partKey != null) break" in _ma214)

# ---- Start: each row's Show more opens its tab, ordered and narrowed as the row is
import pd_localapi as _la228
_sm228 = _la228.LocalAPI.start_more
check("start: a row's Show more opens its own tab and kind",
      _sm228("laugh-movie") == {"tab": "films", "sort": "popular", "genre": "Comedy"}
      and _sm228("mind-show") == {"tab": "tv", "sort": "popular", "genre": "Sci-Fi & Fantasy"}
      and _sm228("svc-8") == {"tab": "films", "sort": "popular", "service": "8"}
      and _sm228("new-show") == {"tab": "tv", "sort": "originallyAvailableAt"}
      and _sm228("kids-movie")["genre"] == "Family" and _sm228("shuffle") is None
      and _sm228("classic-movie") == {"list": "imdb-top", "name": "IMDb Top 250"}
      and all(_sm228(v[0] + "-movie") for v in _la228.LocalAPI.VIBES if v[0] != "trash"))
_st228 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("app: Start's rows end in a Show more that opens the tab",
      "StartMoreCard(cardW) { onMore(more) }" in _st228 and "StartScreen(onOpen) { more ->" in _ma214
      and "if (more != null && !onTv()) Modifier.clickable { onMore(more) }" in _st228)

# ---- kept lists are read again every day by the server's own round
_srv230 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("lists: the server reads its kept lists again every day",
      "threading.Thread(target=keep_lists, name=\"palladium-lists\", daemon=True).start()" in _srv230
      and "api.refresh_lists(con)" in _srv230)

# ---- Start's fronts are made ahead by the server's own round, dropped on feedback
_srv232 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("start: fronts made ahead every ten minutes, a viewer's dropped when they tell Start something",
      'threading.Thread(target=keep_start_ready, name="palladium-start",' in _srv232
      and "local().start_forget(self.viewer())" in _srv232)

# ---- Start's Shuffle row: the shuffle used last first
import importlib.util as _iu233
_srv233 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns233 = {"Handler": type("H", (), {"round_stamp": staticmethod(
    lambda one: int(one.get("casualStamp") or 0) if isinstance(one, dict) else 0)})}
exec(_srv233[_srv233.index("def shuffled_last_first"):_srv233.index("def series_episode_keys")], _ns233)
_cards233 = [{"shelf": "a"}, {"shelf": "b"}, {"shelf": "c"}, {"shelf": "show:d"}]
_rounds233 = {"a": {"casualStamp": 10}, "coll:b": {"casualStamp": 30}, "show:d": {"casualStamp": 20}}
check("start: the Shuffle row leads with the shuffle used last, an unused one after",
      [c["shelf"] for c in _ns233["shuffled_last_first"](_cards233, _rounds233)] == ["b", "show:d", "a", "c"]
      and "local().start_forget(self.viewer())" in _srv233.split('if path == "/collections/shuffle":')[1][:2000])

# ---- Collections: the series' shuffles in a row of their own; a Start shuffle with none makes one
_srv234 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_js234 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("collections: one row, a collection with a shuffle wearing its ribbon - web and app",
      _srv234.count("seriesShelf") >= 2
      and "(c.seriesShelf ? '<div class=\"ribbon\"><span>Shuffle</span></div>' : \"\")" in _js234
      and '"shuffle": ("coll:" + str(c.get("id") or ""))' in _srv234
      and "Shuffles" not in _ma214.split("val films = shown.filter")[1][:1500])
check("collections: a series shuffled with no collection gets one, its round carried over",
      'if cid.startswith("show:") and not body.get("peek"):' in _srv234
      and "cid = self.own_series_shelf(cid[5:]) or cid" in _srv234)

# ---- an episode numbered with four digits is that episode, in a pack and in the library
import pd_torrents as _pt236
import pd_library as _pl236
_d236 = {"packs": [{"name": "Some.Show.S06.1080p.BluRay.x265-RBG", "films": [
    {"path": "Some Show S06/Some Show S06E0013 1080p BluRay x265-RBG.mkv", "kind": "movie",
     "title": "Some Show S06E0013", "year": None},
    {"path": "Some.Film.2001.1080p.mkv", "kind": "movie", "title": "Some Film", "year": 2001}]}]}
_n236 = _pt236._episodes_read_again(_d236)
_f236 = _d236["packs"][0]["films"]
check("packs and library: S06E0013 is season 6 episode 13, a film stays a film",
      _n236 == 1 and _f236[0]["kind"] == "episode" and _f236[0]["season"] == 6
      and _f236[0]["episode"] == 13 and _f236[1]["kind"] == "movie"
      and _pl236.parse_episode("X:\\Show\\Some.Show.S06E0013.1080p.mkv")[1:] == (6, 13)
      and _pl236.parse_episode("X:\\Show\\Some.Show.S06E13.1080p.mkv")[1:] == (6, 13))

# ---- a collection plays all in its order, or shuffled - two buttons, web and app
_js237 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
_pa237 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("collections: Play all in the order shown and Play shuffle, and the player plays the list on",
      'act("\\u25b6 Play all",' in _js237 and 'Pill("\\u25b6 Play all", primary = true,' in _ma214
      and "if (queueAhead().isNotEmpty()) step(forward = true)" in _pa237
      and "queueAhead().isNotEmpty() || queueBehind().isNotEmpty()" in _pa237
      and 'putStringArrayListExtra("queue", ArrayList(keys.drop(1)))' in _ma214)

# ---- a collection has a second rule for its shuffle, set on the web's collection form
_srv239 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_js239 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("collections: the shuffle's own span is saved and drawn from",
      "return shelf, self.shuffle_part(con, shelf, self.shelf_pool(con, shelf))" in _srv239
      and '"shuffleSeasonWithout"):' in _srv239
      and "body.shuffleSeason = shuffleIn.value.trim();" in _js239)

# ---- a collection's shuffle-excluded cards turn red while Play shuffle is pointed at
_js241 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
_srv241 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("collections: what the shuffle leaves out is red while Play shuffle is pointed at - web and app",
      'if (it.notShuffled) el.classList.add("unshuf");' in _js241
      and 'playIt.addEventListener("mouseenter", lit(true));' in _js241
      and "self.shuffle_marks(con, shelf, rows, raw)" in _srv241
      and 'q["unshuffled"] = [",".join(unshuffled)]' in _srv241
      and "browse.shuffleLit = it.isFocused || it.hasFocus" in _ma214)

# ---- a collection edit ends with its tab: the next collection's form starts clean
_js242 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
_go242 = _js242.split("function go(view, keep) {")[1][:400]
_form242 = _js242.split("function collectionForm(shelf) {")[1][:400]
check("collections: another tab ends an edit, and a new form drops the last one's rule test",
      "endCollectionEdit();" in _go242 and "collTestOn = null;" in _form242
      and "if (form && form.dataset.shelf === (collectionOn || \"\")) return;" in _js242)

# ---- a manual collection's form: a search of its own, the name copied in while new
_js243 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
_form243 = _js243.split("function collectionForm(shelf) {")[1].split("\nfunction ")[0]
check("collections: a manual form searches on its own, the new name copied in until typed over",
      "if (shelf || findOwn) return;" in _form243 and "find.value = name.value;" in _form243
      and 'find.addEventListener("input", () => { findOwn = true; findSoon(); });' in _form243
      and "stage(key, !inNow().has(key));" in _form243
      and 'findRow.classList.toggle("hidden", on);' in _form243)

# ---- the cache never installs an older build from the main server
_srv244 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_door244 = _srv244.split('if path == "/update/install":')[1][:5000]
import pd_update as _pu244
check("update: an installer from the main server older than this one is refused",
      "if onto and here_now and not pd_update.newer(" in _door244
      and _door244.index("build_from_master") < _door244.index("if onto and here_now and not pd_update.newer(")
      and not _pu244.newer("0.19.201", "0.19.226") and _pu244.newer("0.19.227", "0.19.226")
      and not _pu244.newer("0.19.226", "0.19.226"))

# ---- a kept list opens in its own order, web and app; Show more closes on Back (web)
_js245 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
check("lists: open in the list's order with List order a sort, web and app; Show more's sheet closes on Back",
      'key: listed ? "list"' in _js245 and '[["list", "List order"]]' in _js245
      and 'if (on.key === "list") return dir * ((a.listRank || 0) - (b.listRank || 0));' in _js245
      and "Route.extraOrders(browse.moreRow, browse.moreList)" in _ma214
      and "browse.moreList = true" in _ma214
      and 'history.pushState({ sheet: 1 }, "");' in _js245
      and "if (sheetPop) { const then = sheetPop; sheetPop = null; then(); return; }" in _js245)

# ---- a download dialog says what a press did at its top, where it is seen
_ed246 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "EpisodeDownloads.kt"), encoding="utf-8").read().replace("\r\n", "\n")
check("downloads: the episode and season dialogs say what a press did at the top",
      _ed246.count("verticalArrangement = Arrangement.spacedBy(8.dp)) {\n                // what a press did, first") == 2
      and "if (said.isNotEmpty()) Text(said, color = Skin.Dim" not in _ed246)

# ---- an actor's search offers their whole career, not the thirty best known
import pd_streaming as _ps247
_held247, _keep247 = _ps247._held, _ps247._keep_searched
_ps247._held = lambda lib: ({("movie", 999): "x"}, {})
_ps247._keep_searched = lambda rows: None
try:
    _cred247 = [{"id": n, "title": "Film %d" % n, "release_date": "1990-01-01",
                 "poster_path": "/p.jpg", "vote_count": 6, "popularity": 100 - n}
                for n in range(60)]
    _cred247 += [{"id": 999, "title": "Held", "release_date": "1990-01-01", "poster_path": "/p.jpg",
                  "vote_count": 50, "popularity": 500},
                 {"id": 998, "title": "Few votes", "release_date": "1990-01-01",
                  "poster_path": "/p.jpg", "vote_count": 3, "popularity": 400}]
    _got247 = _ps247.credit_rows(None, _cred247, skip=lambda t: t in ("Film 0", "Film 1"))
    _names247 = [r["title"] for r in _got247]
    check("search: an actor's films are all offered - a pack's and held ones take no place",
          len(_got247) == 58 and "Film 0" not in _names247 and "Held" not in _names247
          and "Few votes" not in _names247 and _names247[0] == "Film 2"
          and "Film 59" in _names247)
finally:
    _ps247._held, _ps247._keep_searched = _held247, _keep247

# ---- an episode's releases: the added pack first, then single episodes, then season packs
_ed248 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "EpisodeDownloads.kt"), encoding="utf-8").read()
check("downloads: an episode's releases stand under headings, the pack you added first",
      "Route.releaseGroups(rows.map { it.pack to it.box }).forEach { (heading, at) ->" in _ed248
      and '"Tracker",' not in _ed248)

# ---- the watchlist and collection buttons open popups: Add or Remove as it stands, Go to
_ma249 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_sp249 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "ShelfPicker.kt"), encoding="utf-8").read()
check("marks: watchlist and collection popups with Add/Remove and Go to (app)",
      "onList = { listing = true }," in _ma249 and "WatchlistPicker(listed, favorite" in _ma249
      and 'onGoTo = { where -> goToShelf(ctx, browse, stack, where) })' in _ma249
      and 'if (onGoTo != null) Pill("Go to", small = true) { onClose(); onGoTo(s.id) }' in _sp249)

# ---- web: the same popups - Add or Remove as it stands, Favorite, Go to
_js250 = open(os.path.join(_h214, "static", "app.js"), encoding="utf-8").read()
check("marks: watchlist and collection popups with Add/Remove and Go to (web)",
      "pickWatchlist(item, left, () => {" in _js250
      and 'act.textContent = s.state === "all" ? "Remove" : "Add";' in _js250
      and "goToCollection(s.id);" in _js250 and 'go("watchlist");' in _js250.split("function pickWatchlist")[1][:3000])

# ---- web: Fix match as plain words, and a title page's buttons one height
_css251 = open(os.path.join(_h214, "static", "style.css"), encoding="utf-8").read()
_js251 = open(os.path.join(_h214, "static", "app.js"), encoding="utf-8").read()
check("web: Fix match plain words, the title page's buttons one height",
      'b.className = "btn fixmatch";' in _js251 and ".btn.fixmatch{background:none;border:0;" in _css251
      and "#seasonbar .btn:not(.tiny)" in _css251 and "height:40px;box-sizing:border-box" in _css251)

# ---- Start: the page still while the row the remote goes to is on screen
_st252 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("start: still while the row is on screen, one row on below the lower one",
      "Route.startRowMove(offset, size, containerSize, fromTop)?.let { move ->" in _st252
      and "Route.rowPivot(offset, size, containerSize)?.let { return it }" not in _st252)

# ---- Start's picture: the summary keeps the room of its most lines
_st253 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("start: the picture's summary keeps three lines' room, so its title does not move",
      "minLines = if (titleLines > 1) 2 else 3," in _st253)

# ---- phone: Start's and Home's first poster under its row's name
_st254 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("phone: Start's cards start under the row's name",
      _st254.count("contentPadding = PaddingValues(horizontal = if (onTv()) 8.dp else 0.dp,") == 2)

# ---- a title sent to the screens plays on the ones listening, not on one opened later
_srv255 = open(os.path.join(_h214, "pd-server.py"), encoding="utf-8").read()
_ma255 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("screens: a play command is for a minute, and an app's first ask does not act on it",
      'NOTICE["playUntil"] = time.time() + PLAY_FOR' in _srv255
      and '"play": (NOTICE.get("play") or "") if fresh else "",' in _srv255
      and "if (first) {" in _ma255.split("val first = !browse.noticeAsked")[1][:900])

# ---- Start: off the picture only while a quarter of the screen is picture, and for that slide
_st256 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("start: down from the first row is not taken for down off the picture",
      "hero.offset + hero.size > containerSize * 0.25f" in _st256
      and "val fromTop = now < offPictureTill" in _st256)

# ---- web: an episode's releases under headings; an episode added alone is not a season pack
_js257 = open(os.path.join(_h214, "static", "app.js"), encoding="utf-8").read()
check("web: releases under headings, the added pack first; single episodes said as such",
      '[["From the pack you added", rows.filter((v) => v.pack)],' in _js257
      and '"Added - single episodes, not a pack of the season"' in _js257
      and '"No pack of this season on the tracker."' not in _js257)

# ---- a subtitle's place: wrapped rows counted, the rules in Route.subtitleTop (RouteTest)
_pa258 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("player: a wrapped subtitle's rows counted, its place from Route.subtitleTop",
      ".setLine(subtitleTop(rowsOf(it))," in _pa258 and "it.count { c -> c == chr10 } + 1" not in _pa258
      and "Route.subtitleTop(barShare()" in _pa258)

# ---- a season card says its number on every shelf, not only where the caller asks
_ui259 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Ui.kt"), encoding="utf-8").read()
check("app: a season poster says Season N on Home and Favorites too",
      "val line = Route.posterLine(m.type, m.index, instead, m.grandparentTitle)" in _ui259)

# ---- a pack taken off a series: a cancelled download from it does not keep it linked
import pd_torrents as _pt260
_d260 = [{"hash": "aa", "state": "cancelled"}, {"hash": "bb", "state": "done"},
         {"hash": "cc", "state": "downloading"}, {"hash": "dd", "state": "failed"}]
check("packs: only a download that brought something keeps a pack linked",
      _pt260._came_from(_d260) == {"bb", "cc"}
      and _pt260.packs_free("X", {"showPacks": {"x": {"packs": ["aa", "bb"]}}}, _d260) == ["aa"])

# ---- qBittorrent through the link merge tool while it runs with two links, straight out otherwise
import pd_torrents as _pt261
check("downloads: qBittorrent through link merge when turned on and it has a link up - the LAN gone too",
      _pt261.merge_wanted({"useMerge": True}, ["LAN", "Phone 1"])
      and _pt261.merge_wanted({"useMerge": True}, ["Phone 1"])
      and not _pt261.merge_wanted({}, ["LAN", "Phone 1"])
      and not _pt261.merge_wanted({"useMerge": True}, []) and not _pt261.merge_wanted({"useMerge": False}, ["LAN", "P"])
      and _pt261.merge_prefs(True)["proxy_type"] == "HTTP" and _pt261.merge_prefs(True)["proxy_port"] == 8899
      and _pt261.merge_prefs(False)["proxy_type"] == "None"
      and '_step("keep_merge", keep_merge)' in open(_pt261.__file__, encoding="utf-8").read())

# ---- link merge on Now playing: each link's speed over the last seconds, its total, the sum
import pd_torrents as _pt262
_said262 = {"links": [{"name": "LAN", "bytes": 1000, "active": 3, "use": True, "down": False},
                      {"name": "Phone 1", "bytes": 500, "active": 2, "use": True, "down": True}],
            "history": [[1, 10.0, 2.0], [2, 20.0, 4.0], [3, 30.0, 6.0], [4, 40.0, 8.0]]}
_r262 = _pt262.merge_read(_said262, True)
_js262 = open(os.path.join(_h214, "static", "settings.js"), encoding="utf-8").read()
check("link merge: speeds over the last three seconds, totals, the sum; a light and Stats in settings",
      [l["mbit"] for l in _r262["links"]] == [30.0, 6.0] and _r262["mbit"] == 36.0
      and _r262["bytes"] == 1500 and _r262["links"][1]["up"] is False and _r262["through"] is True
      and 'stats.onclick = () => { tab = "now"; render(); };' in _js262
      and "if (monitor) mergeBlock(main);" in _js262)

# ---- with link merge on, the server's own calls out go through it; the house's machines straight
import pd_torrents as _pt263
check("link merge: the server's own internet calls through it, the house's machines straight",
      _pt263._in_the_house("127.0.0.1:8080") and _pt263._in_the_house("192.168.0.25:8764")
      and _pt263._in_the_house("localhost") and not _pt263._in_the_house("api.themoviedb.org")
      and not _pt263._in_the_house("8.8.8.8"))

# ---- a catalogue lookup by name keeps to the year asked for: Seven of 1995 is not a film of 1960
_src264 = open(os.path.join(_h214, "pd-server.py"), encoding="utf-8").read()
_ns264 = {}
exec(_src264[_src264.index("def hit_of_year"):_src264.index("def listed_tmdb")], _ns264)
_hits264 = [{"id": 966, "release_date": "1960-10-12"}, {"id": 465948, "release_date": "1995-01-11"},
            {"id": 807, "release_date": "1995-09-22"}]
check("catalogue: a name lookup takes the first hit of the year asked for",
      _ns264["hit_of_year"](_hits264, 1995) == 465948 and _ns264["hit_of_year"](_hits264, 0) == 966
      and _ns264["hit_of_year"](_hits264[:1], 1995) == 0 and _ns264["hit_of_year"]([], 1995) == 0
      and "number = listed_tmdb(title, year)" in _src264 and "tmdb = listed_tmdb(title, year)" in _src264)

# ---- Download becomes Cancel once a release is on its way, queued or coming in (app)
_ma265 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_tr265 = open(os.path.join(_h214, "pd_tracker.py"), encoding="utf-8").read()
# what the server says about a tracker download in hand: its page's key, how far, and the words
import pd_tracker as _tk265, pd_torrents as _tq265
class _QB265:
    def __init__(self, cfg): pass
    def info(self, h): return {"progress": 0.4, "state": "downloading", "dlspeed": 2500000,
                               "eta": 120, "size": 9}
_was265 = (_tk265.STATE.get("taken"), _tk265.STATE.get("root"), _tq265.QB, _tk265.read)
_tk265.STATE["taken"] = {"h1": {"id": "7", "name": "F 2023", "key": "o42", "at": time.time()}}
_tk265.STATE["root"], _tq265.QB, _tk265.read = "", _QB265, (lambda: {})
_tg265 = _tk265.taking()
_tk265.STATE["taken"], _tk265.STATE["root"], _tq265.QB, _tk265.read = _was265
check("downloads: a tracker download in hand names its page, how far, and what it is doing",
      len(_tg265) == 1 and _tg265[0]["key"] == "o42" and _tg265[0]["progress"] == 0.4
      and _tg265[0]["stage"] == "downloading" and _tg265[0]["words"].startswith("Downloading 40%")
      and "val going = coming" in _ma265)

# ---- link merge: the server's own calls through it only with the LAN gone; the guard's check direct
import pd_torrents as _pt266
check("link merge: own calls only when the LAN is lost; address check and tracker always direct",
      not _pt266.merge_own_wanted(True, ["LAN", "Phone 1"]) and _pt266.merge_own_wanted(True, ["Phone 1"])
      and not _pt266.merge_own_wanted(False, ["Phone 1"]) and not _pt266.merge_own_wanted(True, [])
      and _pt266._in_the_house("api.ipify.org") and _pt266._in_the_house("tracker.torrentleech.org:443"))

# ---- web: Download becomes Cancel once pressed, queued or coming in, pack or tracker
_js267 = open(os.path.join(_h214, "static", "app.js"), encoding="utf-8").read()
# (the web page's Download becoming its Cancel is pressed through in pd-test-web.js, 46)

# ---- the Play build: no downloads, requests or self-update; the house build unchanged
_gr268 = open(os.path.join(_h214, "android", "app", "build.gradle.kts"), encoding="utf-8").read()
_pm268 = open(os.path.join(_h214, "android", "app", "src", "play", "AndroidManifest.xml"), encoding="utf-8").read()
_up268 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Updates.kt"), encoding="utf-8").read()
_api268 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Api.kt"), encoding="utf-8").read()
check("play build: own id, no install permission, no self-update, only what the server holds",
      'create("play") {' in _gr268 and 'applicationIdSuffix = ".play"' in _gr268
      and 'buildConfigField("boolean", "PLAY", "false")' in _gr268
      and 'REQUEST_INSTALL_PACKAGES"' in _pm268 and 'tools:node="remove"' in _pm268
      and "if (BuildConfig.PLAY) null" in _up268
      and "if (!Route.forStore(BuildConfig.PLAY," in _api268)

# ---- the Play build's settings carry the catalogue credits in place of the update check
_ma269 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
_ro269 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Route.kt"), encoding="utf-8").read()
check("play build: TMDB and JustWatch credits in its settings",
      "Text(Route.PLAY_CREDITS," in _ma269 and "not endorsed or certified by TMDB" in _ro269
      and "JustWatch" in _ro269)

# ---- Start's fronts made ahead give way: not right after a start, and a pause between two
_srv270 = open(os.path.join(_h214, "pd-server.py"), encoding="utf-8").read()
import pd_localapi as _pl270
class _Fake270:
    START_ACTIVE_DAYS = 31
    who = "me"
    def start_active(self, con): return ["a", "b"]
    def start_page(self, con, one, fresh=False): pass
_p270 = []
_n270 = _pl270.LocalAPI.start_make_ahead(_Fake270(), None, pause=lambda: _p270.append(1))
check("start: fronts made ahead ten minutes after a start, with a pause between each",
      "START_AHEAD_AFTER = 600" in _srv270 and "pause=lambda: time.sleep(START_AHEAD_GAP)" in _srv270
      and _n270 == 4 and len(_p270) == 3)

# ---- a watchlisted film that gets a source is said once; one that already had one never
import pd_streaming as _ps271
_src271 = {"a": "fetch", "b": "fetch", "c": "", "d": "fetch", "e": "here", "m": "here", "n": "fetch"}
_now271, _new271 = _ps271.watch_changes(
    ["a", "b", "c", "d", "e"], {"a": "", "b": "fetch", "c": "", "e": "fetch", "m": "", "n": ""},
    lambda k: _src271.get(k, ""))
_srv271 = open(os.path.join(_h214, "pd-server.py"), encoding="utf-8").read()
check("watchlist: a step up - fetchable, or here - is news once; a here moved key too; new or had ones not",
      _new271 == ["a", "e", "m"] and _now271 == {"a": "fetch", "b": "fetch", "c": "", "d": "fetch", "e": "here"}
      and 'one["arrived"] = True' in open(os.path.join(_h214, "pd_localapi.py"), encoding="utf-8").read()
      and 'if path == "/watchlist/news":' in _srv271
      and "threading.Thread(target=keep_watch_news" in _srv271)

# ---- the watchlist in four rows: films, TV shows, favorite films, favorite TV shows
_js272 = open(os.path.join(_h214, "static", "app.js"), encoding="utf-8").read()
_ma272 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("watchlist: films, TV, favorite films and favorite TV each under a heading - web and app",
      '[["Films", films], ["TV shows", telly]].forEach' in _js272
      and '["Favorite TV shows", kept.filter' in _js272
      and "val groups = Route.watchlistGroups(" in _ma272)

# ---- the app's grid: up and down go on into the list under the next heading, same column
_ma273 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("app: up and down cross the watchlist's headings in the same column",
      _ma273.count("val step = Route.gridStep(at, across, list.size,") == 2
      and _ma273.count("above = groups.getOrNull(at - 1)?.second ?: emptyList(),") == 2)

# ---- the app's grid counts its columns from its fullest row, not a heading
_nav274 = open(os.path.join(_h214, "android", "app", "src", "main", "java", "se", "palladium", "tv", "Nav.kt"), encoding="utf-8").read()
check("app: grid columns from the width of a cell",
      "return Route.columnsOf(info.viewportSize.width," in _nav274)

# ---- a viewer can hide films not out yet
import pd_localapi as _pl275
_srv275 = open(os.path.join(_h214, "pd-server.py"), encoding="utf-8").read()
_js275 = open(os.path.join(_h214, "static", "settings.js"), encoding="utf-8").read()
check("films: Upcoming shown or hidden per viewer - popular list and search",
      _pl275.LocalAPI.FILMS_SHOW.get("upcoming") is False
      and 'for name in ("disk", "download", "request", "upcoming"):' in _srv275
      and '["upcoming", "Upcoming",' in _js275)

# ---- a second slow list while one is being asked joins it, newest first
import pd_streaming as _ps276, pd_tracker as _pt276
_was276 = (_pt276.state, _pt276.asked_within, _pt276.ask_for, _ps276.fetchable)
_asked276 = []
try:
    _pt276.state = lambda: {"asks": True}
    _pt276.asked_within = lambda t, y, s: (t, y) in [(a, b) for a, b in _asked276]
    _pt276.ask_for = lambda t, y, again_after=0: _asked276.append((t, y))
    _ps276.fetchable = lambda r: False
    _ps276.ASKING_SLOWLY.update(busy=True, waiting=[])
    _ps276.ask_tracker_slowly([{"title": "A", "year": 1}, {"title": "B", "year": 2}], gap=0)
    _ps276.ask_tracker_slowly([{"title": "C", "year": 3}, {"title": "A", "year": 1}], gap=0)
    _q276 = [r["title"] for r in _ps276.ASKING_SLOWLY["waiting"]]
    _ps276.ASKING_SLOWLY.update(busy=False)
    _ps276.ask_tracker_slowly([{"title": "D", "year": 4}], gap=0)
    for _ in range(50):
        if not _ps276.ASKING_SLOWLY["busy"]:
            break
        time.sleep(0.05)
    check("tracker: slow lists queue rather than drop - newest first, each film once",
          _q276 == ["C", "A", "B"] and [t for t, y in _asked276] == ["D", "C", "A", "B"])
finally:
    _pt276.state, _pt276.asked_within, _pt276.ask_for, _ps276.fetchable = _was276
    _ps276.ASKING_SLOWLY.update(busy=False, waiting=[])

# ---- Settings: Library's own sub-tabs; every second row of tabs drawn alike
_js277 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"),
              encoding="utf-8", errors="replace").read()
_css277 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "style.css"),
               encoding="utf-8", errors="replace").read()
check("settings: Extras, Episode numbering, Streaming services, Skip at start under Library",
      '["shelves", "Shelves"], ["extras", "Extras"],' in _js277
      and '["numbering", "Episode numbering"], ["streaming", "Streaming services"],' in _js277
      and '["skip", "Skip at start"]]' in _js277
      and '\n                      ["extras", "Extras"],' not in _js277)
check("settings: tab rows one class, no heading above one - they did not jump",
      '"addrow subtabs"' not in _js277 and ".addrow.subtabs" not in _css277
      and "'<span class=\"ct\">Reports</span>'" not in _js277)

# ---- each music playlist card wears a picture no card before it wears
import pd_localapi as _la278, pd_music as _pm278
_was278 = (_la278.music_order, _la278.viewer_settings, _pm278.load)
try:
    _la278.music_order = lambda con, who, seed: ["a", "b", "c", "d"]
    _la278.viewer_settings = lambda who: {}
    _pm278.load = lambda: [{"id": "1", "name": "Good", "keys": ["a", "b", "c", "d"]},
                           {"id": "2", "name": "Pop", "keys": ["a", "c"]},
                           {"id": "3", "name": "Rock", "keys": ["a"]}]
    _api278 = object.__new__(_la278.LocalAPI)
    _api278.who = "me"
    _c278 = _api278._start_music(None, "0")
    _faces278 = [c["art"] for c in _c278]
    check("music: playlists that start alike get pictures of their own; one with no other repeats",
          _faces278 == ["/art/a/backdrop", "/art/c/backdrop", "/art/a/backdrop", "/art/b/backdrop"]
          and all("_keys" not in c for c in _c278))
finally:
    _la278.music_order, _la278.viewer_settings, _pm278.load = _was278

# ---- a torrent kept whole is never trimmed back to the files asked for; adds keep their folder
import pd_torrents as _pt279
_was279 = _pt279.load
try:
    _pt279.load = lambda: {"config": {}, "packs": [], "downloads": [], "whole": ["abc"]}
    class _Q279:
        def __init__(self): self.calls = []
        def files(self, h): return [{"index": 0, "priority": 1}, {"index": 1, "priority": 1}]
        def priority(self, h, idx, v): self.calls.append((h, tuple(idx), v))
    _q279 = _Q279()
    _kept279 = _pt279.only_asked(_q279, "abc")
    _trim279 = _pt279.only_asked(_q279, "other")
    check("torrents: one kept whole keeps every file on; any other is trimmed to what was asked",
          _kept279 is False and ("other", (0, 1), 0) in _q279.calls
          and not any(c[0] == "abc" for c in _q279.calls))
finally:
    _pt279.load = _was279
check("torrents: Palladium adds every torrent in a folder of its own",
      '"contentLayout": "Subfolder"}' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "pd_torrents.py"), encoding="utf-8").read())

# ---- back on Start: the row and card that were pressed, not the foot of the page
_ss280 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "StartScreen.kt"), encoding="utf-8").read()
check("start: a pressed card is remembered and gone back to; with none the page opens at its top",
      "val mark: (Media) -> Unit = { m -> StartChoice.cameFrom = r.id to m.ratingKey }" in _ss280
      and "LaunchedEffect(Unit) { StartChoice.restore.value = StartChoice.cameFrom }" in _ss280
      and "else if (StartChoice.cameFrom == null) runCatching { list.scrollToItem(0) }" in _ss280
      and "runCatching { reqs[key]?.requestFocus() }" in _ss280
      and "if (front != null) plugged.forEach { r ->" in _ss280)

# ---- the cache deletes a copy nobody names only after two verified lists in a row leave it out
import tempfile as _tf281, shutil as _sh281, json as _js281
_d281 = _tf281.mkdtemp(prefix="pdkeep")
try:
    for _n281 in ("gone.mkv", "back.mkv", "named.mkv"):
        with open(os.path.join(_d281, _n281), "wb") as _f281:
            _f281.write(b"x")
    with open(pd_follow.ledger_file(_d281), "w", encoding="utf-8") as _f281:
        _f281.write(_js281.dumps({"files": ["gone.mkv", "back.mkv", "named.mkv"]}))
    _p281 = lambda n: os.path.join(_d281, n)
    pd_follow.ABSENT.clear()
    pd_follow.clear_unwanted(_d281, {_p281("named.mkv")}, set(), verified=False)
    _after_unverified = sorted(n for n in os.listdir(_d281) if n.endswith(".mkv"))
    pd_follow.clear_unwanted(_d281, {_p281("named.mkv")}, set())
    _after_one = sorted(n for n in os.listdir(_d281) if n.endswith(".mkv"))
    pd_follow.clear_unwanted(_d281, {_p281("named.mkv"), _p281("back.mkv")}, set())
    pd_follow.clear_unwanted(_d281, {_p281("named.mkv")}, set())
    _after_three = sorted(n for n in os.listdir(_d281) if n.endswith(".mkv"))
    check("cache: an unverified list deletes nothing; a file goes after two verified lists in a row; "
          "back on a list starts again: %s %s %s" % (_after_unverified, _after_one, _after_three),
          _after_unverified == ["back.mkv", "gone.mkv", "named.mkv"]
          and _after_one == ["back.mkv", "gone.mkv", "named.mkv"]
          and _after_three == ["back.mkv", "named.mkv"]
          and "KEEP_UNWANTED_HOURS" not in open(pd_follow.__file__, encoding="utf-8").read())
finally:
    pd_follow.ABSENT.clear()
    _sh281.rmtree(_d281, ignore_errors=True)

# ---- headphones: only ones that arrive after the player starts reopen it, once, by the player on screen
_pa282 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("player: headphones already there when it starts do not reopen it; the callback goes with the player",
      "if (added.orEmpty().none { it.isSink && it.id !in there }) return" in _pa282
      and "if (newest !== this@PlayerActivity || isFinishing || isDestroyed) return" in _pa282
      and "if (soundIsPlain()) return" in _pa282
      and ".unregisterAudioDeviceCallback(cb)" in _pa282
      and "am.registerAudioDeviceCallback(earsCame, null)" in _pa282)

# ---- a subtitle named for the release alone counts as cut for that file
import pd_localapi as _la283
_v283 = "Show.S09E01.720p.HDTV.x264-EVOLVE.mkv"
check("subtitles: the release's own name beside the file is its cut; another release's is not",
      _la283.cut_for(_v283, "S09E01.HDTV.x264-EVOLVE")
      and _la283.cut_for(_v283, "English S09E01.HDTV.x264-EVOLVE")
      and not _la283.cut_for(_v283, "Show - 09x01 - Title.WEB-DL-BS")
      and not _la283.cut_for(_v283, "Show S09E01 Title.DVDRip.HI.cc.en.20FOX")
      and not _la283.cut_for(_v283, "S09E01.WEB.x264-EVOLVE")
      and not _la283.cut_for("Film.2010.1080p.BluRay.x264-GRP.mkv", "English 1080p"))

# ---- a new player stops the one it replaces at once; a replaced one reopens and reports nothing
_pa284 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "PlayerActivity.kt"), encoding="utf-8").read()
check("player: the replaced player is stopped as the new one opens, and neither recovers nor reports",
      "if (before != null && before !== this) before.handedOver()" in _pa284
      and "runCatching { current()?.stop() }" in _pa284
      and _pa284.count("if (isFinishing || newest !== this") >= 1
      and "if (n != null && n !== this) return" in _pa284
      and _pa284.count("newest !== this@PlayerActivity) return") >= 2)

# ---- a pack switched between whole and the rules: whole turns every file on, back trims at once
_was285 = (_pt279.load, _pt279.save, _pt279.QB, _pt279.only_asked)
try:
    _d285 = {"config": {}, "packs": [], "downloads": [], "whole": []}
    _pt279.load = lambda: _d285
    _pt279.save = lambda: None
    _calls285 = []
    class _Q285:
        def __init__(self, cfg): pass
        def files(self, h): return [{"index": 0}, {"index": 1}, {"index": 2}]
        def priority(self, h, idx, v): _calls285.append(("prio", tuple(idx), v))
        def start(self, h): _calls285.append(("start",))
    _pt279.QB = _Q285
    _pt279.only_asked = lambda qb, h, extra=(), files=None: _calls285.append(("trim", h)) or True
    _on285 = _pt279.keep_whole("p1", True)
    _whole285 = _pt279.kept_whole("p1")
    _off285 = _pt279.keep_whole("p1", False)
    check("packs: whole turns every file on and starts it; back to the rules trims at once",
          _on285.get("whole") is True and _whole285 and ("prio", (0, 1, 2), 1) in _calls285
          and ("start",) in _calls285 and _off285.get("whole") is False
          and not _pt279.kept_whole("p1") and ("trim", "p1") in _calls285)
finally:
    _pt279.load, _pt279.save, _pt279.QB, _pt279.only_asked = _was285
_st285 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8").read()
check("packs: Settings shows Ahead only / Whole pack, and the pack's home, as buttons with notes under them",
      '[[false, kind === "tv" ? "Ahead only" : "Asked only"], [true, "Whole pack"]]' in _st285
      and 'post("/torrents/whole", { hash: p.hash, on: w })' in _st285
      and "r.append(head, meter, facts, mode, modeNote, home, first, homeNote, inside);" in _st285)

# ---- a film's progress is qBittorrent's own number for its file
_src286 = open(_pt279.__file__, encoding="utf-8").read()
check("progress: a download shows qBittorrent's percentage for its file, no estimate of its own",
      "            progress = float(f.get(\"progress\") or 0)\n" in _src286 and "arrived_share" not in _src286)

# ---- a torrent that never took in a byte (a cross-seed waiting for its check) hides no film
_was286 = (_pt279.load, _pt279.QB, _pt279._keep_unfinished, dict(_pt279.FILE_LISTS),
           dict(_pt279.UNFINISHED))
try:
    _pt279.load = lambda: {"config": {}}
    _pt279._keep_unfinished = lambda paths: None
    _pt279.FILE_LISTS.clear()
    class _Q286:
        def __init__(self, cfg): pass
        def _call(self, path, timeout=10):
            return json.dumps([
                {"hash": "seed", "save_path": "G:/M", "downloaded": 0, "progress": 0.4},
                {"hash": "dl", "save_path": "G:/M", "downloaded": 5, "progress": 0.4}]).encode()
        def files(self, h):
            return [{"index": 0, "name": h + ".mkv", "progress": 0.4, "priority": 1}]
    _pt279.QB = _Q286
    _pt279._read_unfinished()
    _got286 = {os.path.basename(x) for x in _pt279.UNFINISHED["paths"]}
    check("unfinished: a torrent with nothing received holds back no file; one receiving does",
          _got286 == {"dl.mkv"})
finally:
    (_pt279.load, _pt279.QB, _pt279._keep_unfinished) = _was286[:3]
    _pt279.FILE_LISTS.clear(); _pt279.FILE_LISTS.update(_was286[3])
    _pt279.UNFINISHED.clear(); _pt279.UNFINISHED.update(_was286[4])

# ---- Now playing draws each pass whole: downloads asked before the rows are cleared
_st287 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8").read()
_dl287 = _st287[_st287.index("async function drawLive"):]
_dl287 = _dl287[:_dl287.index("into._liveTimer = setTimeout")]
check("Now playing: everything is read before the list is cleared, downloads are listed once, and Link merge sits over Workers",
      'await get("/torrents/log")' not in _dl287 and _dl287.count("(data.downloading || []).forEach") == 1 and "book.downloads" not in _dl287
      and _dl287.count("await ") == _dl287[:_dl287.index('into.innerHTML = "";')].count("await ")
      and "try { s = tick.last = await get(\"/merge\"); }" in _st287
      and "mergeBlock(right);" + chr(10) + "      right.appendChild(work);" in _st287)

# ---- the cache copies only what is on a screen while a film comes in or the library scans
import ast as _ast288, time as _time288, types as _types288
_txt288 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns288 = {"time": _time288}
for _n288 in _ast288.parse(_txt288).body:
    if isinstance(_n288, _ast288.FunctionDef) and _n288.name in ("disks_busy", "copy_while_busy"):
        exec(_ast288.get_source_segment(_txt288, _n288), _ns288)
_fake288 = _types288.SimpleNamespace(rows=[])
_was288 = sys.modules.get("pd_torrents")
sys.modules["pd_torrents"] = _types288.SimpleNamespace(load=lambda: {"downloads": _fake288.rows})
_scan288 = {"running": False}
_ns288["local"] = lambda: _types288.SimpleNamespace(lib=_types288.SimpleNamespace(scan_state=_scan288))
try:
    _quiet288 = _ns288["disks_busy"]()
    _fake288.rows = [{"state": "downloading", "started": _time288.time() - 600}]
    _dl288 = _ns288["disks_busy"]()
    _fake288.rows = [{"state": "downloading", "started": _time288.time() - 7 * 3600}]
    _old288 = _ns288["disks_busy"]()
    _scan288["running"] = True
    _sc288 = _ns288["disks_busy"]()
finally:
    sys.modules["pd_torrents"] = _was288
_w288 = [{"why": "on a screen now"}, {"why": "kept ahead"}, {"why": "part-way through"}]
check("cache: a film coming in or a scan holds back every copy but what is on a screen now",
      _quiet288 == "" and _dl288 and _old288 == "" and _sc288
      and _ns288["copy_while_busy"](_w288) == [{"why": "on a screen now"}]
      and "wanted = copy_while_busy(wanted)" in _txt288)

# ---- a film whole on the disk is said ready within seconds; a check is not a download
_pt289 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
_wk289 = _pt289[_pt289.index("def _work():"):]
_wk289 = _wk289[:_wk289.index("pd_beat.sleep(")]
_ui289 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                           "java", "se", "palladium", "tv", "Ui.kt"), encoding="utf-8").read()
_sv289 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("ready: downloads followed first in the round and after the slow steps; screens ask every 8 s; checks are not listed",
      _wk289.index('_step("_follow_downloads"') < _wk289.index('_step("_mirror_house"')
      and _wk289.count('_step("_follow_downloads"') >= 2
      and _wk289.rindex('_step("_follow_downloads"') > _wk289.index('_step("_guard_packs"')
      and "const val EVERY_MS = 8_000L" in _ui289
      and 'or state.startswith("checking"):' in _sv289)

# ---- loudness measuring leaves alone the drive the cache is copying from
_sv290 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
import ast as _ast290, types as _ty290
_ns290 = {"os": os}
for _n290 in _ast290.parse(_sv290).body:
    if isinstance(_n290, _ast290.FunctionDef) and _n290.name == "playing_drives":
        exec(_ast290.get_source_segment(_sv290, _n290), _ns290)
_ns290["WATCHING"] = _ty290.SimpleNamespace(snapshot=lambda: [{"how": "direct play", "key": "a"},
                                                              {"how": "syncing", "key": "b"}])
_ns290["local"] = lambda: _ty290.SimpleNamespace(
    file_for=lambda k, n: {"file": {"a": "D:/x.mkv", "b": "G:/y.mkv"}[k]})
check("measuring: the drive being watched from and the drive a copy reads from are both left alone",
      _ns290["playing_drives"]() == {"D:"} and _ns290["playing_drives"](copies=True) == {"G:"}
      and "busy |= playing_drives(copies=True)" in _sv290)

# ---- background workers: on under playback with a full buffer, waiting for a copy, a download or a scan
_sv291 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_q291 = _sv291[_sv291.index("def quiet_now():"):]
_q291 = _q291[:_q291.index("def ", 10)]
_bw291 = _sv291[_sv291.index("def background_waits():"):]
_bw291 = _bw291[:_bw291.index("def ", 10)]
NL = chr(10)
_ns291 = {}
import ast as _ast291, types as _ty291
for _n291 in _ast291.parse(_sv291).body:
    if isinstance(_n291, _ast291.FunctionDef) and _n291.name == "worker_waits":
        exec(_ast291.get_source_segment(_sv291, _n291), _ns291)
_w291 = _ns291["worker_waits"]
def _gate291(short=False, waits="", game=False, busy=0):
    _ns291.update(playback_short=lambda: short, background_waits=lambda: waits,
                  Handler=_ty291.SimpleNamespace(game_mode=lambda: {"on": game},
                                                 card_now=lambda: {"busy": busy}))
_gate291()
check("workers' gate: a quiet machine lets work run", _w291() == "" and _w291(card=True) == "")
_gate291(waits="a download is coming in")
check("workers' gate: a download stops background work, not work for a screen",
      _w291() == "a download is coming in" and _w291(for_screen=True) == "")
_gate291(short=True)
check("workers' gate: a viewer short of disk stops it, unless the work is on another drive",
      _w291() != "" and _w291(other_drive=True) == "")
_gate291(game=True)
check("workers' gate: game mode stops card work only", _w291() == "" and _w291(card=True) == "game mode")
_ns291b = {"re": __import__("re")}
for _n291 in _ast291.parse(_sv291).body:
    if isinstance(_n291, _ast291.FunctionDef) and _n291.name == "scp_drives":
        exec(_ast291.get_source_segment(_sv291, _n291), _ns291b)
import pd_disk as _pdd291
_scp291 = _ns291b["scp_drives"]([r'scp -q -l 600000 -i C:\Users\x\.ssh\key G:\Movies\Pack\a.mkv host:D:/Downloads/a.mkv'])
_out291 = _pdd291.outside_from([], _scp291)
check("disk queue: a copy over scp holds the workers of the drive it reads, no other drive",
      _scp291 == {"G:"} and _pdd291.decide("G:", "work", [], [], _out291.get("G:", []), 1) != ""
      and _pdd291.decide("D:", "work", [], [], _out291.get("D:", []), 1) == ""
      and "copying = scp_sending_from()" in _sv291)
check("workers' gate: every worker asks it and nothing else",
      "if worker_waits():" + NL + "        return False" in _q291
      and 'r.get("how") == "syncing"' in _bw291 and "return disks_busy()" in _bw291
      and _sv291.count("return not worker_waits(card=True)") == 2
      and 'return worker_waits()' in _sv291
      and "if worker_waits(for_screen=playing, other_drive=free_drive):" in _sv291
      and "if worker_waits(other_drive=True):" in _sv291
      and 'pd_beat.sleep("cast", 60, why)' in _sv291
      and _sv291.count("background_waits()") == 2 and _sv291.count("playback_short()") == 6
      and "while held < 120 and playback_short():" in _sv291)

# ---- checks are shown as checks: on Now playing in their own section, never as downloads
_sv292 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_js292 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8").read()
_fo292 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
import ast as _ast292, types as _ty292, json as _json292, re as _re292
_ns292 = {"time": __import__("time"), "json": _json292, "os": os, "re": _re292,
          "CHECKS": {"at": 0.0, "rows": []}}
for _n292 in _ast292.parse(_sv292).body:
    if isinstance(_n292, _ast292.FunctionDef) and _n292.name == "checking_now":
        exec(_ast292.get_source_segment(_sv292, _n292), _ns292)
class _Q292:
    def __init__(self, cfg): pass
    def _call(self, path, timeout=6):
        return _json292.dumps([{"name": "A.Film.2019.1080p-GRP.mkv", "progress": 0.25,
                                "save_path": "G:/Seed", "size": 5, "state": "checkingUP"},
                               {"name": "B", "progress": 1, "save_path": "D:/x", "state": "stalledUP"}]).encode()
_was292 = sys.modules.get("pd_torrents")
sys.modules["pd_torrents"] = _ty292.SimpleNamespace(QB=_Q292, load=lambda: {"config": {}})
try:
    _c292 = _ns292["checking_now"]()
finally:
    sys.modules["pd_torrents"] = _was292
check("checks: listed with name, how far and drive; only torrents being checked; their own section on Now playing",
      _c292 == [{"name": "A.Film.2019.1080p-GRP", "progress": 0.25, "drive": "G:", "size": 5}]
      and 'said["checking"] = reading' in _sv292
      and 'head.textContent = "Checking";' in _js292
      and 'log_line(("copies wait: " + held) if held else "copies go on")' in _fo292)

# ---- the cache gives up on a file only for the file's faults, never a cut connection
import pd_follow as _pf293, http.client as _hc293, urllib.error as _ue293
_keep293 = (list(_pf293.TROUBLE), dict(_pf293.GIVEN_UP))
try:
    _pf293.TROUBLE[:] = []; _pf293.GIVEN_UP.clear()
    _it293 = {"name": "ep.mkv", "size": 5}
    for _ in range(4):
        _e = ConnectionResetError("reset")
        _pf293.note_trouble("ep.mkv", _e, 5, None if _pf293.connection_lost(_e) else _it293)
    _cut293 = "ep.mkv" not in _pf293.GIVEN_UP
    for _ in range(3):
        _e = ValueError("not the film that was offered")
        _pf293.note_trouble("bad.mkv", _e, 5, None if _pf293.connection_lost(_e) else {"name": "bad.mkv", "size": 5})
    _bad293 = "bad.mkv" in _pf293.GIVEN_UP
finally:
    _pf293.TROUBLE[:] = _keep293[0]; _pf293.GIVEN_UP.clear(); _pf293.GIVEN_UP.update(_keep293[1])
_src293 = open(_pf293.__file__, encoding="utf-8").read()
check("cache: a cut connection never gives up on a file, a wrong file does; each round says what it did",
      _cut293 and _bad293
      and _pf293.connection_lost(_hc293.RemoteDisconnected("x"))
      and _pf293.connection_lost(_ue293.URLError(ConnectionRefusedError()))
      and not _pf293.connection_lost(ValueError("x"))
      and 'log_line("round %d s: %d on the list, %d copied%s" % (' in _src293)

# ---- a release taken from the tracker says it is ready on the screens of whoever took it
import pd_tracker as _ptr294, sqlite3 as _sq294, types as _ty294
_db294 = _sq294.connect(":memory:", check_same_thread=False)
_db294.executescript("CREATE TABLE item(id TEXT, title TEXT); CREATE TABLE episode(id TEXT, season INT, number INT);"
                     "CREATE TABLE file(path TEXT, item_id TEXT, episode_id TEXT);"
                     "INSERT INTO item VALUES('k1','A Film'); INSERT INTO file VALUES('D:/a.mkv','k1',NULL);")
class _Lib294:
    def db(self):
        class _C:
            def execute(s2, *a): return _db294.execute(*a)
            def close(s2): pass
        return _C()
_keep294 = dict(_ptr294.STATE.get("taken") or {})
try:
    _ptr294.STATE["taken"] = {
        "h1": {"name": "A.Film.2026.1080p-GRP", "in": True, "inAt": 200, "paths": ["D:/a.mkv"], "token": "tok"},
        "h2": {"name": "A.Film.2026.1080p-GRP", "in": True, "inAt": 200, "paths": ["D:/a.mkv"], "token": "other"},
        "h3": {"name": "A.Film.2026.1080p-GRP", "in": True, "inAt": 50, "paths": ["D:/a.mkv"], "token": "tok"},
        "h4": {"name": "A.Film.2026.1080p-GRP", "in": False, "paths": [], "token": "tok"}}
    _r294 = _ptr294.ready_for({"tok"}, 100, _Lib294())
finally:
    _ptr294.STATE["taken"] = _keep294
check("ready: a tracker download in the library since the last look is said once to whoever took it",
      _r294 == [{"id": "t:h1", "key": "k1", "title": "A Film", "done": 200}])

# ---- the cache fetches again a file of the right size and other contents, once a round
import pd_follow as _pf295, tempfile as _tf295, io as _io295
_d295 = _tf295.mkdtemp()
_old295 = b"old" * 100
_new295 = b"new" * 100
open(os.path.join(_d295, "x.mkv"), "wb").write(_old295)
open(os.path.join(_d295, "y.mkv"), "wb").write(_new295)
_mark295 = _pf295.quick_mark(os.path.join(_d295, "y.mkv"))
os.remove(os.path.join(_d295, "y.mkv"))
_was295 = (_pf295.urllib.request.urlopen, _pf295.looks_like_film, _pf295.note_ours)
class _Ans295(_io295.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
_asked295 = []
try:
    _pf295.urllib.request.urlopen = lambda req, timeout=60: (_asked295.append(1), _Ans295(_new295))[1]
    _pf295.looks_like_film = lambda part, into: True
    _pf295.note_ours = lambda folder, into: None
    _pf295.copy_file({"master": "http://m", "key": "k"},
                     {"name": "x.mkv", "size": len(_new295), "mark": _mark295, "part": 1}, _d295)
    _got295 = open(os.path.join(_d295, "x.mkv"), "rb").read()
    _pf295.copy_file({"master": "http://m", "key": "k"},
                     {"name": "x.mkv", "size": len(_new295), "mark": _mark295, "part": 1}, _d295)
finally:
    _pf295.urllib.request.urlopen, _pf295.looks_like_film, _pf295.note_ours = _was295
_src295 = open(_pf295.__file__, encoding="utf-8").read()
check("cache: a same-size file with other contents is fetched again, a matching one is not; each file once a round",
      _got295 == _new295 and len(_asked295) == 1
      and 'and str(w.get("name") or "") not in handled' in _src295)

# ---- a release packed in RAR parts is unpacked beside them, its per cent shown, half a film never taken in
import pd_tracker as _ptu, tempfile as _tfu
_du = _tfu.mkdtemp(prefix="pdunpack")
open(os.path.join(_du, "x.rar"), "wb").close()
open(os.path.join(_du, "x.mkv"), "wb").close()
open(os.path.join(_du, _ptu.UNPACK_MARK), "wb").close()
_half = _ptu.unpacked(os.path.join(_du, "x.rar"))
os.remove(os.path.join(_du, _ptu.UNPACK_MARK))
_whole = _ptu.unpacked(os.path.join(_du, "x.rar"))
_srvu = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_jsu = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8").read()
_rtu = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                         "java", "se", "palladium", "tv", "Route.kt"), encoding="utf-8").read()
check("unpack: first part found, per cent read, half a film held back, and Unpacking said on every screen",
      _ptu.first_volume(["a/x.r00", "a/x.rar", "a/x.r01"]) == "a/x.rar"
      and _ptu.first_volume(["a/x.part02.rar", "a/x.part01.rar"]) == "a/x.part01.rar"
      and _ptu.first_volume(["a/x.r01", "a/x.r00"]) == "a/x.r00"
      and _ptu.unpack_progress("... 12%\b\b\b\b 45%") == 45 and _ptu.unpack_progress("none") is None
      and _half is None and _whole == [os.path.join(_du, "x.mkv")]
      and '"state": "unpacking"' in _srvu
      and '(d.state === "unpacking" ? "unpacking " : "downloading ")' in _jsu
      and '"unpacking" -> "Unpacking "' in _rtu
      and 'head.classList.add("golink");' in _jsu and 'tab = "remote";' in _jsu)

# ---- file fixer: the shell of a download is found, and mended from a whole copy or qBittorrent
import pd_fixer as _fx, pd_library as _plx, tempfile as _tfx, json as _jfx
_dx = _tfx.mkdtemp(prefix="pdfix")
_shell = os.path.join(_dx, "a", "Film.mkv"); _whole = os.path.join(_dx, "b", "Film.mkv")
os.makedirs(os.path.dirname(_shell)); os.makedirs(os.path.dirname(_whole))
with open(_shell, "wb") as _f:
    _f.write(os.urandom(65536)); _f.write(b"\0" * (65536 * 40))
with open(_whole, "wb") as _f:
    _f.write(os.urandom(65536 * 41))
_real = os.path.join(_dx, "real.mkv")
with open(_real, "wb") as _f:
    _f.write(os.urandom(65536 * 41))
_hollow_ok = _plx.hollow_file(_shell) and not _plx.hollow_file(_whole) and not _plx.hollow_file(_real)
_size = os.path.getsize(_shell)
_rows = [(_shell, _size), (_whole, _size)]
_did = _fx.fix(_shell, _size, _rows, _plx.hollow_file, None, [], None)
_linked = _did.startswith("replaced by a link") and open(_shell, "rb").read() == open(_whole, "rb").read()
class _Qx:
    def __init__(self): self.calls = []; self.state = "stoppedUP"
    def _call(self, path, **k):
        return _jfx.dumps([{"hash": "h1", "save_path": os.path.join(_dx, "c"), "state": self.state}]).encode()
    def files(self, h): return [{"index": 3, "name": "Other.mkv"}]
    def recheck(self, h): self.calls.append(("recheck", h))
    def info(self, h): return {"state": self.state}
    def priority(self, h, idx, v): self.calls.append(("prio", h, tuple(idx), v))
    def start(self, h): self.calls.append(("start", h))
os.makedirs(os.path.join(_dx, "c"))
_lone = os.path.join(_dx, "c", "Other.mkv")
with open(_lone, "wb") as _f:
    _f.write(os.urandom(65536)); _f.write(b"\0" * (65536 * 40))
_q = _Qx()
_keepx = _fx.STATE["paper"]
_fx.STATE["paper"] = {"checked": {}, "found": {}}
try:
    _d2 = _fx.fix(_lone, os.path.getsize(_lone), [(_lone, 1)], _plx.hollow_file, _q, [], None)
    _fx.paper()["found"][_lone] = {"why": "x", "did": _d2, "at": 0}
    _fx.STATE["root"] = _dx
    _fx.follow_up(_q)
finally:
    _fx.STATE["paper"] = _keepx
check("fixer: a shell is told from a film; mended by a link to a whole copy; or rechecked and its parts fetched",
      _hollow_ok and _linked
      and _d2 == "rechecking in qBittorrent; the missing parts follow: h1/3"
      and ("recheck", "h1") in _q.calls and ("prio", "h1", (3,), 1) in _q.calls and ("start", "h1") in _q.calls
      and _fx.next_to_check([("x", 1), ("y", 1)], {"x": 10 ** 9}, 10 ** 9 + 10) == ("y", 1)
      and _fx.save_path_for(os.path.join("G:" + os.sep, "M", "Rel", "f.mkv"), "Rel/f.mkv") == os.path.join("G:" + os.sep, "M"))

# ---- a film that has come in shows as here: no Download ribbon, and its open page turns to Play
_uiA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                         "java", "se", "palladium", "tv", "Ui.kt"), encoding="utf-8").read()
_maA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                         "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("arrived: the shelf's stale card loses Download, and the open page reads itself again",
      "val arrived = (m.offered || m.askable) && live == null && Api.cameIn(m.ratingKey)" in _uiA
      and 'else if (arrived) CornerBand("NEW", bandW, Color(0xFFD9A21B))' in _uiA
      and 'else if (m.offered && said.isEmpty() && !arrived) CornerBand("DOWNLOAD", bandW)' in _uiA
      and "if (got != null && !got.askable && !got.offered) {" in _maA
      and _uiA.index('else if (arrived) CornerBand("NEW"') < _uiA.index('else if (m.askable && m.onTracker) CornerBand("DOWNLOAD", bandW)'))

# ---- web: a screen opened while another loads stays; the one left stops drawing and asking
_apN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js"), encoding="utf-8").read()
_hmN = _apN[_apN.index("async function viewHome()"):]
_hmN = _hmN[:_hmN.index("\n}\n")]
check("web: start-up leaves a chosen screen; Home stops when left; its rows are given up",
      'if (where !== "monitor" && SCREEN === FIRST_SCREEN) go(firstTab());' in _apN
      and _apN.count("  pageMoved();\n  SCREEN = { kind:") == 7
      and _hmN.count("if (seq !== sectionSeq) return;") >= 2
      and "if (left && left.aborted) { e.pageMoved = true; throw e; }" in _apN
      and "if (e && e.pageMoved) throw e;" in _apN
      and "count: start + size }, srv, true);" in _apN)

# ---- link merge for qBittorrent: Off, On, or Auto - only while something downloads, and a while after
import pd_torrents as _ptm
_mw = _ptm.merge_wanted
_ft = _ptm.fetching_now
check("link merge: Auto only while downloading (and five minutes after); On always; Off never",
      _mw({"useMerge": "auto"}, ["LAN", "Phone 1"], True) and not _mw({"useMerge": "auto"}, ["LAN", "Phone 1"], False)
      and _mw({"useMerge": True}, ["LAN"], False) and not _mw({"useMerge": False}, ["LAN"], True)
      and not _mw({"useMerge": "auto"}, [], True)
      and _ft(["stalledUP", "downloading"], 1000, 0) == (True, 1000)
      and _ft(["stalledUP"], 1000 + 200, 1000) == (True, 1000)
      and _ft(["stalledUP"], 1000 + 400, 1000) == (False, 1000)
      and '[[false, "Off"], ["auto", "Auto"], [true, "On"]]' in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "static", "settings.js"), encoding="utf-8").read())

# ---- a tracker download's page: no yellow Download before its look, and the state at once after pressing
_maT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                         "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("title page: Download lit only once looked; pressed, it says Starting at once; a known download shows at once",
      "primary = looked," in _maT and 'if (begun) starting = true' in _maT
      and "if (going == null && (listed != null || starting)) {" in _maT
      and _maT.index("if (going == null && (listed != null || starting)) {") < _maT.index("Cancel  " + chr(92) + "u00b7"))

# ---- a film in from a download is read at once, scan or not; arriving is said, not Download again
_lbP = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_library.py"), encoding="utf-8").read()
_tiP = _lbP[_lbP.index("    def take_in(self, paths):"):]
_tiP = _tiP[:_tiP.index("        return took")]
_svP = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_maP = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "android", "app", "src", "main",
                         "java", "se", "palladium", "tv", "MainActivity.kt"), encoding="utf-8").read()
check("arrival: probed at once whatever the scan; arriving shown; the download list has no gap at the end",
      _tiP.index("self.probe_pending(paths=added)") < _tiP.index('if took and not self.scan_state.get("running"):')
      and 'Pill("Downloaded - arriving", modifier = Modifier.focusRequester(actFocus)) { }' in _maP
      and "landed = coming == null && mine.any { it.done }" in _maP
      and 'LANDING["at"] = 0.0' in _svP)

# ---- a worker outside the server reports into the Workers boxes; the file fixer says its total
_svO = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_fxO = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_fixer.py"), encoding="utf-8").read()
check("workers: an outside worker reports name, step, done and left; the fixer counts what is left",
      'if path == "/workers/report":' in _svO and "for one in list(OUTSIDE.values()):" in _svO
      and "if now - float(one.get(\"at\") or 0) > 600:" in _svO
      and "return done, max(0, int(total) - done)" in _fxO)

# ---- the reading of qBittorrent's file lists runs once at a time; the ones asked meanwhile are not started
import pd_torrents as _ptR, threading as _thR, time as _tmR
_calls = []
_wasR = _ptR._read_unfinished_now
try:
    _ptR._read_unfinished_now = lambda: (_calls.append(1), _tmR.sleep(0.5))
    _ts = [_thR.Thread(target=_ptR._read_unfinished) for _ in range(5)]
    [t.start() for t in _ts]; [t.join() for t in _ts]
    _one = len(_calls)
    _ptR._read_unfinished(wait=True)
finally:
    _ptR._read_unfinished_now = _wasR
check("unfinished: five asked at once read once; one that must wait reads after", _one == 1 and len(_calls) == 2)

# ---- the cache puts a long copy down for a file on a screen that it does not hold
import pd_follow as _pfQ, tempfile as _tfQ
_dQ = _tfQ.mkdtemp(prefix="pdq")
open(os.path.join(_dQ, "here.mkv"), "wb").write(b"x" * 10)
_wQ = [{"why": "kept ahead", "name": "far.mkv", "size": 5},
       {"why": "on a screen now", "name": "here.mkv", "size": 10},
       {"why": "on a screen now", "name": "copying.mkv", "size": 7},
       {"why": "on a screen now", "name": "wanted.mkv", "size": 9},
       {"why": "on a screen now", "name": "wanted.mkv", "size": 9, "side": 0}]
_srcQ = open(_pfQ.__file__, encoding="utf-8").read()
_svQ = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("cache: the first on a screen and not held is put first; held or being copied is not; "
      "subtitles chosen ahead only in the viewer's language",
      _pfQ.first_on_screen(_wQ, _dQ, "copying.mkv") == "wanted.mkv"
      and _pfQ.first_on_screen(_wQ[:3], _dQ, "copying.mkv") == ""
      and "and not is_hot(item)" in _srcQ and "raise Preempted(PREEMPT[" in _srcQ
      and "if fits and mine_too and not self.picked_for(key):" in _svQ)

# ---- a transcode's thin buffer is the encoder's lead, not a viewer running dry; its stalls still count
_svT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_htT = _svT[_svT.index("    def how_thin(now=None, side=None):"):]
_htT = _htT[:_htT.index("    def running_dry")]
check("buffer: a transcoded screen's depth is skipped after its stall is counted",
      _htT.index("if len(said) > 4 and said[4]:") < _htT.index("if len(said) > 5 and said[5]:")
      and '"transcode" in str(r.get("how") or "")' in _svT)

# ---- a copy's speed is its last few seconds, not the 20 s a direct play needs
import pd_watching as _pwC
_mC = [(t, t * 1048576 * (1 if t < 20 else 4)) for t in range(0, 20)] + [(t, 20 * 1048576 + (t - 20) * 4 * 1048576) for t in range(20, 25)]
_lastC = _mC[-1][1]
check("copy speed: a freed line shows within the copy window; nothing in it reads 0",
      abs(_pwC.Watching.copy_rate(_mC, _lastC, 24.0, 1.0) - 4.0) < 0.01
      and _pwC.Watching.copy_rate(_mC, _lastC, 40.0, 1.0) == 0.0)

# ---- a copy going out looks at viewers and its ceiling on a clock, not every megabyte
_svL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_lpL = _svL[_svL.index("    def send_file_ranged(self, path, sid=None):"):]
_lpL = _lpL[:_lpL.index("        if trouble:")]
check("copy loop: viewers and settings read at most every COPY_LOOK_EVERY, copies in 1 MB blocks, pace by bytes sent",
      _lpL.count("WATCHING.snapshot()") == 1 and _lpL.count("lib.config()") == 1
      and _lpL.index("if copying_out and time.monotonic() >= next_look:") < _lpL.index("WATCHING.snapshot()")
      and "block = 1048576 if copying_out else 262144" in _lpL
      and "owed = (paced * 8 / (ceiling * 1e6)) - held_back" in _lpL)

# ---- a fault starts Claude Code: only the machinery's faults, never a start line, read-only, spaced
import pd_claude as _pcC
_rowsC = [{"source": "auto", "when": 100, "kind": "crash", "text": "a"},
          {"source": "person", "when": 200, "kind": "problem", "text": "b"},
          {"source": "auto", "when": 50, "kind": "error", "text": "c"}]
check("claude: new auto faults only; start lines are not a fault; one run per GAP; tools read only",
      [k for _, k, _, _ in _pcC.new_faults(_rowsC, 60)] == ["crash"]
      and _pcC.new_stacks("---- started 2026-10-26 10:00:00, pid 1\n") == ""
      and "Thread 0x" in _pcC.new_stacks("---- started x\nThread 0x01 (most recent call first):\n")
      and _pcC.due(1000.0, 0.0, False) and not _pcC.due(1000.0, 999.0, False)
      and not _pcC.due(10 ** 6, 0.0, True)
      and _pcC.TOOLS == "Read,Grep,Glob")

# ---- this week's picks: the same draw all week, unseen and not lately picked, a new week a new draw
import datetime as _dtW
import pd_weekly as _pwW
_filmsW = [(str(n), 1000 + n, 6.0 + (n % 40) / 10.0) for n in range(60)]
_seenRowsW = _pwW.seen([("1", 95, 100, 0, 95), ("2", 10, 100, 0, 10), ("3", 0, 0, 1, 0)])
# rated 7.0 and up: 10..39 and 50..59; the first thirty of those seen, so ten are left
_seenW = {str(n) for n in range(10, 40)}
_aW = _pwW.choose(_filmsW, _seenW, [], "2026-W44")
_bW = _pwW.choose(_filmsW, _seenW, _aW, "2026-W45")
_tinyW = _pwW.choose(_filmsW[:12], set(), [1000 + n for n in range(12)], "2026-W44")
check("weekly: one draw per week, ten, all rated GOOD, none seen, none from last week; a small library still fills",
      _aW == _pwW.choose(_filmsW, _seenW, [], "2026-W44") and len(_aW) == _pwW.SIZE
      and _seenRowsW == {"1", "3"}
      and sorted(_aW) == [1000 + n for n in range(50, 60)]
      and all(r >= _pwW.GOOD for k, t, r in _filmsW if t in _bW)
      and not set(_aW) & set(_bW) and len(set(_bW)) == len(_bW)
      and 0 < len(_tinyW) <= _pwW.SIZE
      and _pwW.due({"week": "2026-W43"}, _dtW.date(2026, 10, 26))
      and not _pwW.due({"week": "2026-W44"}, _dtW.date(2026, 11, 1))
      and _pwW.line("Short.") == "Short."
      and _pwW.line("First sentence is here. " + "x" * 300) == "First sentence is here."
      and len(_pwW.line("word " * 100)) <= 201
      and 'write_settings(dict({"weeklyPicks": picks}, **(' in
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"),
               encoding="utf-8").read())

# ---- a cross-seed or a race is not a film on its way
_svN = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ciN = _svN[_svN.index("def coming_in():"):]
_ciN = _ciN[:_ciN.index("\ndef ")]
check("coming in: cross-seed and race torrents are left out before anything is listed",
      'NOT_LIBRARY = ("cross-seed", "fl-race")' in _svN
      and _ciN.index('in NOT_LIBRARY:') < _ciN.index("out.append({"))

# ---- Request and Upcoming are two settings; an unsaved Upcoming follows Request
import pd_localapi as _laF
_dF = {"disk": True, "download": True, "request": False, "upcoming": False}
check("films shown: nothing saved shows neither",
      _laF.films_shown(None, _dF) == _dF)
check("films shown: Request saved on, Upcoming never saved, follows it",
      _laF.films_shown({"disk": True, "download": True, "request": True}, _dF)["upcoming"] is True)
check("films shown: Upcoming on with Request off stays on",
      _laF.films_shown({"request": False, "upcoming": True}, _dF) == dict(_dF, upcoming=True))
check("films shown: Upcoming off with Request on stays off",
      _laF.films_shown({"request": True, "upcoming": False}, _dF)["upcoming"] is False)
_srcF = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("films shown: a film not out yet is shown by Upcoming alone",
      'got = card if show.get("upcoming") else None' in _srcF)

# ---- one film, two pack releases: the page on one follows the other one picked
import pd_torrents as _ptV, tempfile as _tfV
_wasV = (_ptV.STATE["root"], _ptV.STATE["data"], _ptV.QB)
_ptV.STATE["root"] = _tfV.mkdtemp()
_ptV.STATE["data"] = {"config": {}, "downloads": [
    {"key": "oB", "state": "downloading", "progress": 0.4, "mbit": 200.0, "token": "me", "hash": "h"}],
    "packs": [{"name": "p", "pieceBytes": 0, "films": [
        {"key": "oA", "tmdb": 7, "title": "F", "year": 2023, "path": "F.2023.BluRay-X.mkv", "size": 5},
        {"key": "oB", "tmdb": 7, "title": "F", "year": 2023, "path": "F.2023.WEB-Y.mkv", "size": 6},
        {"key": "oC", "tmdb": 8, "title": "G", "year": 2023, "path": "G.2023.mkv", "size": 7}]}]}
_ptV.QB = lambda cfg: (_ for _ in ()).throw(OSError("qBittorrent is off"))
_offV = _ptV.metadata("oA")["offer"]
check("pack releases: the page on one release says the other is downloading",
      _offV["state"] == "downloading" and _offV["progress"] == 0.4)
check("pack releases: another film's download is not this film's",
      _ptV.metadata("oC")["offer"]["state"] == "")
check("pack releases: Cancel on the page stops the release coming in",
      _ptV.cancel("oA", "me", True)["ok"] is True
      and _ptV.latest_download("oB")["state"] == "cancelled")
_ptV.latest_download("oB").update(state="done", when=time.time())
check("pack releases: the other release in and not in the library yet: Arriving, not Download",
      _ptV.metadata("oA")["offer"]["state"] == "done")
_ptV.latest_download("oB").update(when=time.time() - 3 * 86400)
check("pack releases: one in days ago is not arriving any more",
      _ptV.metadata("oA")["offer"]["state"] == "")
_ptV.STATE["root"], _ptV.STATE["data"], _ptV.QB = _wasV

# ---- the installer size check passes a real installer and stops one without the server
import re as _reI, glob as _glI
_srcI = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-build-installer.py"), encoding="utf-8").read()
_leastI = float(_reI.search(r"^LEAST_SETUP_MB = ([0-9.]+)", _srcI, _reI.M).group(1))
_madeI = sorted(_glI.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "Palladium-Setup-*.exe")),
                key=os.path.getmtime)[-5:]
check("installer size check: the last real installers pass it, one 5 MB short does not",
      all(os.path.getsize(p) >= _leastI * 1e6 for p in _madeI)
      and min([os.path.getsize(p) for p in _madeI] or [22.5e6]) - 5e6 < _leastI * 1e6)

# ---- a download says what it is doing, in one wording for every screen
import pd_torrents as _ptW, pd_tracker as _trW
_sw = _ptW.stage_words
check("download words: looking for seeders, not Downloading 0%",
      _sw("downloading", "stalledDL", 0, 0) == ("finding", "Looking for seeders")
      and _sw("downloading", "metaDL", 0, 0)[0] == "finding")
check("download words: checking the files, waiting for seeders, the rate and time left",
      _sw("downloading", "checkingDL", 0.3, 0) == ("checking", "Checking the files 30%")
      and _sw("downloading", "stalledDL", 0.42, 0) == ("waiting", "Waiting for seeders · 42%")
      and _sw("downloading", "downloading", 0.42, 180.4, 360)
          == ("downloading", "Downloading 42% · 180 Mbit/s · 6 min left"))
check("download words: stopped on something says why; queued says its place; done says arriving",
      _sw("downloading", "missingFiles", 0.1, 0, None, 0, "it is stuck")[0] == "stuck"
      and _sw("queued", "", 0, 0, None, 2) == ("queued", "Queued · 2 ahead")
      and _sw("done")[0] == "arriving" and _sw("unpacking", "", 0.3)[1] == "Unpacking 30%")
_gW = {"hash": "g", "state": "stalledDL", "amount_left": -564, "progress": 1.0}
check("qBittorrent's 100% that never seeds is caught after five minutes; a film just finishing is not",
      not _trW.glitched(_gW, 1000.0) and not _trW.glitched(_gW, 1100.0) and _trW.glitched(_gW, 1300.0)
      and not _trW.glitched({"hash": "f", "state": "downloading", "amount_left": 0, "progress": 1.0}, 0)
      and not _trW.glitched({"hash": "f", "state": "downloading", "amount_left": 0, "progress": 1.0}, 900)
      and not _trW.glitched({"hash": "s", "state": "stalledUP", "amount_left": 0, "progress": 1.0}, 0)
      and not _trW.glitched({"hash": "h", "state": "stalledDL", "amount_left": 5000, "progress": 0.5}, 0))
_lbW = {"hash": "lb", "state": "stalledDL", "amount_left": 16384, "progress": 0.99999, "num_seeds": 58}
check("a pack's last block stalled with seeds connected is caught after five minutes; with no seed it waits",
      not _trW.glitched(_lbW, 2000.0) and _trW.glitched(_lbW, 2300.0)
      and not _trW.glitched(dict(_lbW, hash="ns", num_seeds=0), 2000.0)
      and not _trW.glitched(dict(_lbW, hash="ns", num_seeds=0), 2400.0))
_nowW = time.time()
_heldW = [{"hash": "a", "progress": 1.0, "added_on": _nowW - 3600},       # lost note: taken in
          {"hash": "b", "progress": 1.0, "added_on": _nowW - 3600},       # noted already
          {"hash": "c", "progress": 1.0, "added_on": _nowW - 3600},       # a pack
          {"hash": "d", "progress": 0.5, "added_on": _nowW - 3600},       # not whole
          {"hash": "e", "progress": 1.0, "added_on": _nowW - 30 * 86400}] # long ago
check("a finished tracker download with its note lost is taken in; packs, noted, half and old are not",
      [t["hash"] for t in _trW.orphans(_heldW, {"b": {}}, {"c"}, _nowW)] == ["a"])

# ---- a subtitle cut for a whole double episode: each file gets its own half
import pd_vtt as _vD, random as _rD
_rD.seed(7)
def _srtD(times):
    nl = chr(10)
    stamp = lambda t: "%02d:%02d:%02d,%03d" % (int(t // 3600), int(t % 3600 // 60), int(t % 60), int(t % 1 * 1000))
    return (nl + nl).join("%d%s%s --> %s%sline" % (i + 1, nl, stamp(a), stamp(a + 1.8), nl)
                          for i, a in enumerate(times))
_p1D = sorted(_rD.uniform(5, 1250) for _ in range(300))      # the first file's lines, credits after 1250
_p2D = sorted(_rD.uniform(3, 1255) for _ in range(300))      # the second file's lines
_wholeD = _srtD(_p1D + [1252 + a for a in _p2D])            # the whole: no credits between
_endD = _vD.srt_end(_wholeD)
check("double episode: the whole subtitle is told to be the first half here, the second there",
      _vD.double_part(_endD, 1299, 2495, 1314, "Show.S05E14", 14) == ("first", 1299.0)
      and _vD.double_part(_endD, 1314, 1299, 1299, "Show.S05E14", 15) == ("second", 1299.0)
      and _vD.double_part(_endD, 1314, 1299, 1299, "Show", 15) is None
      and _vD.double_part(1300, 1299, 1299, 1299, "Show.S05E14", 14) is None)
_h1D = _vD.split_double(_wholeD, "first", 1299, 1299, _srtD(_p1D))
_h2D = _vD.split_double(_wholeD, "second", 1299, 1314, _srtD(_p2D))
_t1D, _t2D = [a for a, _ in _vD.cue_times(_h1D)], [a for a, _ in _vD.cue_times(_h2D)]
check("double episode: each half placed on its own file's clock by the subtitle beside it",
      len(_t1D) == 300 and abs(_t1D[0] - _p1D[0]) < 0.6 and abs(_t1D[-1] - _p1D[-1]) < 0.6
      and len(_t2D) == 300 and abs(_t2D[0] - _p2D[0]) < 0.6 and abs(_t2D[-1] - _p2D[-1]) < 0.6)
check("double episode: with nothing beside it the first half is cut at its length, the second left alone",
      abs(_vD.srt_end(_vD.split_double(_wholeD, "first", 1299, 1299, "")) - 1299) < 60
      and _vD.split_double(_wholeD, "second", 1299, 1314, "") == "")
_fsD = _sv291[_sv291.index("    def fetch_subtitle("):]
check("double episode: a fetched subtitle goes through it before it is written",
      _fsD.index("text = self.half_of_double(key, text, release)") < _fsD.index("with open(out, \"w\""))

# ---- a download taken in and dropped again while it was read over goes back in
import pd_tracker as _trX
_tkX = {"a": {"in": True, "inAt": 1000, "paths": ["D:/a.mkv"]},          # dropped: back in
        "b": {"in": True, "inAt": 1000, "paths": ["D:/b.mkv"]},          # still in the library
        "c": {"in": True, "inAt": 1000, "paths": ["D:/c.mkv"]},          # being read over
        "d": {"in": True, "inAt": 1000 - 5 * 86400, "paths": ["D:/d.mkv"]},  # long ago
        "e": {"in": False, "paths": []}}
check("a finished download dropped from the library while it was read over is taken in again",
      _trX.dropped(_tkX, {"a", "b", "d", "e"}, lambda p: p == "D:/b.mkv", 2000.0) == [["D:/a.mkv"]])

# ---- the debug log is moved aside past its size, and left alone short of it
import ast as _astL, tempfile as _tfL
_svL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_nsL = {"os": os, "LOG_KEPT_MB": 20}
for _nL in _astL.parse(_svL).body:
    if isinstance(_nL, _astL.FunctionDef) and _nL.name == "rotate_log":
        exec(_astL.get_source_segment(_svL, _nL), _nsL)
_dL = _tfL.mkdtemp()
_fL = os.path.join(_dL, "debug.log")
open(_fL, "w").write("x" * 3000)
_keptL = not _nsL["rotate_log"](_fL, 0.01) and os.path.exists(_fL)
open(_fL, "w").write("x" * 20000)
check("debug log: moved to .1 past its size, kept short of it",
      _keptL and _nsL["rotate_log"](_fL, 0.01) and not os.path.exists(_fL)
      and os.path.getsize(_fL + ".1") == 20000
      and 'rotate_log(os.path.join(ROOT, "debug.log"))' in _svL)

# ---- the health page: what needs somebody, from the facts of both machines
import pd_health as _hH
_okH = {"machines": [{"name": "A", "build": "1", "up": True}, {"name": "B", "build": "1", "up": True}],
        "torrents": [], "disks": [{"drive": "D:", "freeGB": 100, "downloads": True}],
        "backup": {"at": 1000, "ok": True}, "faults": 0, "workers": [{"name": "W", "state": "waiting"}]}
check("health: all well says nothing", _hH.problems(_okH, 2000) == [])
_badH = dict(_okH, machines=[{"name": "A", "build": "1", "up": True}, {"name": "B", "build": "2", "up": True},
                             {"name": "C", "up": False}],
             torrents=[{"name": "t1", "state": "missingFiles"}, {"name": "t2", "state": "stalledDL", "stuck": True},
                       {"name": "t3", "state": "checkingResumeData", "since": 0},
                       {"name": "t4", "state": "checkingDL", "since": 1900}],
             disks=[{"drive": "D:", "freeGB": 5, "downloads": True}, {"drive": "G:", "freeGB": 5, "downloads": False}],
             backup={"at": 1000, "ok": False}, faults=2, workers=[{"name": "W", "state": "stalled"}])
_pH = _hH.problems(_badH, 2000)
check("health: every kind of trouble is said, once each: %s" % _pH,
      len(_pH) == 10 and any("C is not answering" in p for p in _pH) and any("different versions" in p for p in _pH)
      and any("t1" in p for p in _pH) and any("t2: stuck" in p for p in _pH) and any("t3" in p for p in _pH)
      and not any("t4" in p for p in _pH) and any("D: has 5 GB free for downloads" in p for p in _pH)
      and any(p == "G: has 5 GB free" for p in _pH)
      and any("backup failed" in p for p in _pH) and any("2 fault reports" in p for p in _pH)
      and any("W has stalled" in p for p in _pH))
check("health: a machine under two addresses is one row, up when either answered",
      _hH.one_each([{"name": "A", "up": True}, {"name": "B", "up": False, "build": ""},
                    {"name": "B", "up": True, "build": "2"}, {"name": "C", "up": False},
                    {"name": "C", "up": False}])
      == [{"name": "A", "up": True}, {"name": "B", "up": True, "build": "2"}, {"name": "C", "up": False}])
check("health: a backup that did not run last evening, or never ran",
      any("has not run" in p for p in _hH.problems(dict(_okH, backup={"at": 0, "ok": True}), 200000))
      and any("no backup" in p for p in _hH.problems(dict(_okH, backup=None), 2000)))

# ---- an episode's list asks the tracker about its season, as the season's list does
import pd_boxsets as _bxE, pd_tracker as _tkE
_askedE = []
_wasE = (_tkE.ask_for, _bxE.episode_in_packs)
_tkE.ask_for = lambda title, year=0, again_after=90.0: _askedE.append(title) or []
_bxE.episode_in_packs = lambda title, season, number, skip=(): [("1", {}, {})]
_gotE = _bxE.episode_packs("Show", 3, 7, skip=["x"])
_tkE.ask_for, _bxE.episode_in_packs = _wasE
check("an episode's packs: the season is asked of the tracker even with a pack already known",
      _askedE == ["Show S03"] and _gotE == [("1", {}, {})]
      and "pd_boxsets.episode_packs(" in _sv291)

# ---- a whole-file subtitle read says in the log what it is for
import types as _tyS, tempfile as _tfS
_svS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_nsS = {"os": os, "time": time, "threading": __import__("threading"), "chr": chr}
for _nS in _ast291.parse(_svS).body:
    if isinstance(_nS, _ast291.FunctionDef) and _nS.name == "pull_subtitle":
        exec(_ast291.get_source_segment(_svS, _nS), _nsS)
_rootS = _tfS.mkdtemp()
import pd_log as _plS
_plS.setup(_rootS)                         # the log is written by the one helper, into this folder
_nsS.update(ROOT=_rootS, pd_log=_plS, sub_cache_path=lambda v, i: v + ".vtt", cached_subtitle=lambda w: None,
            PULLING_LOCK=__import__("threading").Lock(), PULLING={}, PULL_ONE=__import__("threading").Lock(),
            _pull_subtitle=lambda v, i, w, p: "WEBVTT")
_nsS["pull_subtitle"](os.path.join(_rootS, "Show.S01E01.mkv"), 2, why="subtitle warm-up")
_logS = open(os.path.join(_rootS, "debug.log"), encoding="utf-8").read()
check("a whole-file subtitle read is logged with the file, the track and what it is for: %s" % _logS.strip(),
      "reading subtitle track 2 of Show.S01E01.mkv - subtitle warm-up" in _logS
      and 'pull_subtitles_all(video, indexes, why="subtitle warm-up",' in _svS
      and 'start_pull(video, index, "for a screen playing it")' in _svS)

# ---- a pack's home: one living on the copy server has its requests sent there
import pd_torrents as _ptH, pd_follow as _pfH
_dH = {"packs": [{"hash": "hh", "films": [{"key": "fk", "index": 3, "size": 5, "path": "a/b.mkv"}]}],
       "downloads": [], "forCopy": [], "home": {}, "config": {}}
_wasH = (_ptH.load, _ptH.save, _ptH._following)
try:
    _ptH.load, _ptH.save, _ptH._following = (lambda: _dH), (lambda: None), (lambda: {})
    _hA = _ptH.home_of("hh")
    _ptH.set_home("hh", "copy")
    _sH = _ptH.request("fk", "tok", "Ann")
    _ptH.request("fk", "tok", "Ann")
    check("pack home: this server unless set; set to the copy, a request is kept for it once",
          _hA == "main" and _ptH.home_of("hh") == "copy" and _sH.get("copy")
          and [r["key"] for r in _ptH.for_copy()] == ["fk"])
    _gotH = []
    _tH = _pfH.take_for_copy({}, _ptH.for_copy(),
                             request=lambda *a, **k: _gotH.append((a[0], k.get("auto"))) or {"ok": True},
                             told=lambda ids: _ptH.copy_took(ids))
    check("pack home: the copy server downloads it as its own, and it leaves the list",
          _gotH == [("fk", True)] and len(_tH) == 1 and _ptH.for_copy() == [])
    _ptH._following = lambda: {"master": "x"}
    _ptH.set_home("hh", "main")
    check("pack home: back to this server, nothing more is sent", _ptH.home_of("hh") == "main"
          and "home" in _dH and _dH["home"] == {})
    _ptH.set_home("hh", "both")
    check("pack home: both is kept as both; anything else is refused",
          _ptH.home_of("hh") == "both" and not _ptH.set_home("hh", "maybe").get("ok"))
    _ptH.set_home("hh", "main")
finally:
    _ptH.load, _ptH.save, _ptH._following = _wasH

# ---- sync back: packs at home on the copy held here only while the rules want them
import pd_homes as _phS
_fS = [({"hash": "h"}, {"index": 1, "tmdb": 10}), ({"hash": "h"}, {"index": 2, "tmdb": 20}),
       ({"hash": "h"}, {"index": 3, "tmdb": 30}), ({"hash": "h"}, {"index": 4, "tmdb": 40}),
       ({"hash": "h"}, {"index": 5, "tmdb": 50})]
_hereS = {2, 3, 4, 5}                                   # 1 is wanted and not here
_copyS = {1, 2, 3, 4}                                   # 5 is not whole on the copy
_fetchS, _dropS = _phS.decide(_fS, {10, 20}, lambda p, f: f["index"] in _hereS,
                              lambda p, f: f["index"] in _copyS, playing={40})
check("sync back: a wanted film the copy holds is taken back; one nobody wants is let go - "
      "never one on a screen, never one the copy does not hold whole",
      [f["index"] for _, f in _fetchS] == [1] and [f["index"] for _, f in _dropS] == [3])
_fB, _dB = _phS.decide([({"hash": "b"}, {"index": 7, "tmdb": 70}), ({"hash": "b"}, {"index": 8, "tmdb": 80})],
                       set(), lambda p, f: f["index"] == 8, lambda p, f: True, always={"b"})
check("sync back: a pack at home on both has every file fetched here and none let go",
      [f["index"] for _, f in _fB] == [7] and _dB == [])
import tempfile as _tfG, pd_torrents as _ptG
_dG = _tfG.mkdtemp()
_packG = os.path.join(_dG, "Packs", "Big.Pack")
os.makedirs(os.path.join(_packG, "Film.A"))
_fileG = os.path.join(_packG, "Film.A", "Film.A.mkv")
open(_fileG, "wb").write(b"x" * 3000)
_filmG = {"size": 3000}
_alG = [os.path.join(_dG, "Packs")]
_okG = _ptG.safe_to_remove(_fileG, _packG, "Film.A/Film.A.mkv", _filmG, _alG)
_nfoG = os.path.join(_packG, "Film.A", "Film.A.nfo"); open(_nfoG, "wb").write(b"n" * 3000)
check("sync back: a file is removed only as the pack's own film inside its own folder at its own length",
      _okG == ""
      and _ptG.safe_to_remove(_fileG, _packG, "Film.B/Film.B.mkv", _filmG, _alG) != ""        # another file's name
      and _ptG.safe_to_remove(_fileG, os.path.join(_dG, "Packs"), "Big.Pack/Film.A/Film.A.mkv", {"size": 3000}, _alG) == ""
      and _ptG.safe_to_remove(_fileG, _packG, "Film.A/Film.A.mkv", {"size": 2999}, _alG) != ""   # wrong length
      and _ptG.safe_to_remove(_nfoG, _packG, "Film.A/Film.A.nfo", _filmG, _alG) != ""           # not a film
      and _ptG.safe_to_remove(_fileG, os.path.splitdrive(_dG)[0] + os.sep, _fileG[3:], _filmG, _alG) != ""  # drive root
      and _ptG.safe_to_remove(_fileG, _packG, "../Film.A/Film.A.mkv", _filmG, _alG) != ""        # climbing out
      and _ptG.fingerprint(_fileG) == _ptG.fingerprint(_fileG))
# a file beside the pack's folder, named by qBittorrent as if inside it
_outG = os.path.join(_dG, "Packs", "Loose.mkv"); open(_outG, "wb").write(b"x" * 3000)
check("sync back: never a file outside the pack's folder, never outside the allowed folders, none when none are allowed",
      _ptG.safe_to_remove(_outG, _packG, "../Loose.mkv", _filmG, _alG) != ""
      and _ptG.safe_to_remove(_fileG, _packG, "Film.A/Film.A.mkv", _filmG, [os.path.join(_dG, "Elsewhere")]) != ""
      and _ptG.safe_to_remove(_fileG, _packG, "Film.A/Film.A.mkv", _filmG, []) != ""
      and _ptG.safe_to_remove(_fileG, _packG, "Film.A/Film.A.mkv", _filmG, [os.path.splitdrive(_dG)[0] + os.sep]) != "")
_dataG = {"packs": [{"hash": "g1", "films": [{"index": 0, "size": 3000, "path": "Film.A/Film.A.mkv"}]}],
          "downloads": [{"hash": "g1", "index": 0, "state": "done"}], "home": {"g1": "copy"}, "config": {}}


class _QBG:
    def __init__(self, cfg): pass
    def info(self, h): return {"save_path": _packG}
    def files(self, h): return [{"index": 0, "name": "Film.A/Film.A.mkv", "progress": 1.0, "size": 3000}]
    def priority(self, h, i, v): _gonePrioG.append(v)
    def all_info(self): return [{"hash": "g1", "save_path": _packG}] + _alsoG


_alsoG = []
_gonePrioG, _removedG = [], []
_wasG = (_ptG.load, _ptG.save, _ptG.QB)
try:
    _ptG.load, _ptG.save, _ptG.QB = (lambda: _dataG), (lambda: None), _QBG
    _noG = _ptG.let_go("g1", 0, lambda p, m: False, _alG, remove=_removedG.append)
    _dataG["home"] = {"g1": "both"}
    _bothG = _ptG.let_go("g1", 0, lambda p, m: True, _alG, remove=_removedG.append)
    _dataG["home"] = {"g1": "copy"}
    _yesG = _ptG.let_go("g1", 0, lambda p, m: m == _ptG.fingerprint(_fileG), _alG, remove=_removedG.append)
finally:
    _ptG.load, _ptG.save, _ptG.QB = _wasG
check("sync back: nothing is removed unless the copy confirms the same file, nor from a pack at home on both",
      not _noG["ok"] and not _bothG["ok"] and _yesG["ok"] and _removedG == [_fileG] and _gonePrioG == [0]
      and _dataG["downloads"][0].get("off"))
# and never a file another torrent here seeds as well
_dataG["downloads"][0].pop("off", None)
_QBG.files = lambda self, h: [{"index": 0, "name": "Film.A/Film.A.mkv", "progress": 1.0, "size": 3000}]
_alsoG.append({"hash": "own", "name": "Film.A", "save_path": _packG})
try:
    _ptG.load, _ptG.save, _ptG.QB = (lambda: _dataG), (lambda: None), _QBG
    _twoG = _ptG.let_go("g1", 0, lambda p, m: True, _alG, remove=_removedG.append)
finally:
    _ptG.load, _ptG.save, _ptG.QB = _wasG
check("sync back: a file another torrent here seeds as well is never removed",
      not _twoG["ok"] and "another torrent seeds" in _twoG["why"] and _removedG == [_fileG])
_homesT = {"m": "main", "b": "both", "c": "copy"}
_itemsT = [{"hash": "m", "index": 1}, {"hash": "b", "index": 2}, {"hash": "c", "index": 3},
           {"hash": "c", "index": 4}]
_tmdbT = lambda h, i: {3: 30, 4: 40}.get(i, 0)
_onT = _phS.take_back(_itemsT, _homesT.get, _tmdbT, {30}, True)
_offT = _phS.take_back(_itemsT, _homesT.get, _tmdbT, {30}, False)
check("take back: films follow their pack's home - here and both all, the copy's only as the rules want, with Sync back on",
      [i["index"] for i in _onT] == [1, 2, 3] and [i["index"] for i in _offT] == [1, 2])
check("sync back: a wanted film the copy does not have is not fetched",
      _phS.decide([({"hash": "h"}, {"index": 9, "tmdb": 90})], {90}, lambda p, f: False,
                  lambda p, f: False) == ([], []))

# ---- the subtitle warm-up reads a film once for all its tracks
check("warm-up: a film's tracks are grouped, each film once, in order",
      _srvL.count("def warmup_batches(") == 1)
_nsW = {}
exec(_srvL[_srvL.index("def warmup_batches("):_srvL.index("def pull_subtitles_all(")], _nsW)
check("warm-up: [(film, track)] become one batch a film",
      _nsW["warmup_batches"]([("a", 3), ("b", 1), ("a", 5), ("a", 7)]) == [("a", [3, 5, 7]), ("b", [1])])
_pW = _srvL[_srvL.index("def pull_subtitles_all("):_srvL.index("def _pull_subtitle(")]
check("warm-up: one ffmpeg for all of a film's tracks, one output each",
      'cmd += ["-map", "0:%d" % int(i), "-f", "webvtt", outs[i]]' in _pW and _pW.count("pd_workers.run_yielding(cmd, stop, patience") == 1
      and "pull_subtitles_all(video, indexes" in _srvL)

# ---- stuck at 100%: stopped and started first, read again only if that did not help
import pd_tracker as _tkR
check("stuck at 100%: the house's packs are looked at too, cross-seeds and races are not",
      [t["hash"] for t in _tkR.ours([{"hash": "a", "category": "palladium"}, {"hash": "p", "category": ""},
                                      {"hash": "x", "category": "cross-seed"}, {"hash": "r", "category": "fl-race"},
                                      {"hash": "o", "category": ""}], {"p"})] == ["a", "p"])
check("stuck at 100%: a restart first, a recheck only twenty minutes on, each once",
      _tkR.stuck_remedy(300, False, False) == "restart"
      and _tkR.stuck_remedy(600, True, False) == ""
      and _tkR.stuck_remedy(1200, True, False) == "recheck"
      and _tkR.stuck_remedy(5000, True, True) == ""
      and _tkR.stuck_remedy(1200, True, False, whole=False) == "")

# ---- a pack copy of a film the library holds names it, so its page can play that one
import pd_torrents as _ptH, sqlite3 as _sqH, tempfile as _tfH, types as _tyH
_dbH = os.path.join(_tfH.mkdtemp(), "lib.db")
_cH = _sqH.connect(_dbH)
_cH.execute("CREATE TABLE item (id TEXT, type TEXT, title TEXT, year INT, tmdb_id INT)")
_cH.executemany("INSERT INTO item VALUES (?,?,?,?,?)",
                [("m1", "movie", "Held Film", 2014, 118340), ("m2", "movie", "Named Only", 1999, None)])
_cH.commit(); _cH.close()
_wasH = _ptH.STATE.get("lib")
_ptH.STATE["lib"] = lambda: _tyH.SimpleNamespace(db=lambda: _sqH.connect(_dbH))
check("a pack copy names the library's own copy, by its number or its name and year",
      _ptH.held_key({"tmdb": 118340, "name": "x"}) == "m1"
      and _ptH.held_key({"name": "Named Only", "year": 1999}) == "m2"
      and _ptH.held_key({"tmdb": 5, "name": "Other", "year": 2001}) == "")
_ptH.STATE["lib"] = _wasH

# ---- the tracker account's figures, read off its page's header
import pd_tracker as _tkA
_aA = _tkA.account_of('<div><span>21.24 TB</span> (928)</div> <div>15.79 TB (28)</div><b>5.45 TB</b> '
                      '<i>1.345</i> <a>1</a> <span>31 new</span> TL Points: 100 799,08')
check("tracker account: uploaded, downloaded, buffer, ratio and hit-and-runs off the header: %s" % _aA,
      _aA == {"up": 21.24e12, "down": 15.79e12, "buffer": 5.45e12, "ratio": 1.345, "hnr": 1}
      and _tkA.account_of("<form class='login-form'>Login ::</form>") is None
      and _tkA.account_of("880.5 GB (12) 1.20 TB (40) -400.1 GB 0.712 0")["buffer"] == -400.1e9)
check("health: an expired tracker session is said, and only where there is one",
      any("tracker session has expired" in p for p in _hH.problems(dict(_okH, tracker={"cookie": True, "expired": True}), 2000))
      and _hH.problems(dict(_okH, tracker={"cookie": False, "expired": True}), 2000) == [])

# ---- a background read already running stops when its drive is wanted
import pd_workers as _pw300, time as _t300
_asks300 = []
def _stop300():
    _asks300.append(1)
    return "a copy on G:" if len(_asks300) >= 2 else ""
_b300 = _t300.time()
_rc300, _o300, _why300 = pd_workers_run = _pw300.run_yielding(
    [sys.executable, "-c", "import time; time.sleep(30)"], _stop300, 60, every=0.3)
check("background read: a running job is stopped when its gate closes, not left to finish",
      _why300 == "a copy on G:" and _t300.time() - _b300 < 10)
_rc300, _o300, _why300 = _pw300.run_yielding([sys.executable, "-c", "print('ok')"], lambda: "", 60,
                                              every=0.3, text=True)
check("background read: a job its gate leaves alone runs to its end", _why300 == "" and "ok" in _o300)
_sv300 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ns300 = {"os": os, "URGENT_FILES": set()}
import ast as _ast300, types as _ty300
for _n300 in _ast300.parse(_sv300).body:
    if isinstance(_n300, _ast300.FunctionDef) and _n300.name == "drive_loaded":
        exec(_ast300.get_source_segment(_sv300, _n300), _ns300)
import pd_disk as _pd300
def _dl300(path, screen=(), outside=None, waits=""):
    _ns300.update(files_on_screen=lambda: {os.path.normcase(x) for x in screen},
                  worker_waits=lambda other_drive=False: waits, playing_drives=lambda: set())
    with _pd300.LOCK:
        _was = dict(_pd300.OUTSIDE)
        _pd300.OUTSIDE.clear()
        _pd300.OUTSIDE.update(outside or {})
    try:
        return _ns300["drive_loaded"](path)
    finally:
        with _pd300.LOCK:
            _pd300.OUTSIDE.clear()
            _pd300.OUTSIDE.update(_was)
check("background read: stopped for a copy on its own drive, not for one on another drive",
      _dl300("G:/a.mkv", outside={"G:": ["copy"]}) != "" and _dl300("D:/a.mkv", outside={"G:": ["copy"]}) == "")
check("background read: a file somebody is watching is never stopped",
      _dl300("G:/a.mkv", screen=["G:/a.mkv"], outside={"G:": ["copy"]}, waits="x") == "")
check("background read: the workers' gate closing stops it", _dl300("D:/a.mkv", waits="the cache is copying") != "")
_cr300 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_credits.py"), encoding="utf-8").read()
check("background read: the subtitle warm-up and the credits and speech reads go through it",
      "stop=lambda v=video: drive_loaded(v))" in _sv300 and "stop=drive_loaded)" in _sv300
      and "pd_workers.run_yielding(" in _cr300 and "subprocess.run([python" not in _cr300
      and _cr300.count(", path=path)") == 2)

# ---- a pack at home on the copy server: what it lacks and this server holds is moved to it
import pd_homes as _ph310, pd_follow as _pf310, time as _t310
_f310 = [({"hash": "p"}, {"index": i, "key": "k%d" % i}) for i in range(5)]
_here310 = lambda p, f: f["index"] != 1           # 1 is not held here
_copy310 = lambda p, f: f["index"] == 0           # 0 the copy holds already
check("home on the copy: every film it lacks and this server holds is listed for it, once",
      [f["index"] for _, f in _ph310.give(_f310, _here310, _copy310, ())] == [2, 3, 4]
      and [f["index"] for _, f in _ph310.give(_f310, _here310, _copy310, ["k2"])] == [3, 4])
_got310, _told310, _chk310, _live310 = [], [], [], [0, 0]
def _fetch310(r):
    _live310[0] += 1
    _live310[1] = max(_live310[1], _live310[0])
    _t310.sleep(0.05)
    _got310.append(r["index"])
    _live310[0] -= 1
    return "x"
_pf310.MOVES.update(rows=[], busy=False)
_rc310 = lambda h, ix: _chk310.append((h, sorted(ix)))
_pf310.move_in({"master": "http://m", "key": "k"},
               [{"id": "a", "hash": "p", "index": 2}, {"id": "b", "hash": "p", "index": 3},
                {"id": "c", "hash": "q", "index": 1}],
               fetch=_fetch310, told=_told310.extend, recheck=_rc310)
_pf310.move_in({"master": "http://m", "key": "k"}, [{"id": "a", "hash": "p", "index": 2}],
               fetch=_fetch310, told=_told310.extend, recheck=_rc310)
for _ in range(100):
    if not _pf310.MOVES["busy"]:
        break
    _t310.sleep(0.05)
import pd_torrents as _pt310
check("move in: one file at a time, each once, the main server told each, and no read when a run ends",
      _got310 == [2, 3, 1] and _live310[1] == 1 and _told310 == ["a", "b", "c"]
      and _chk310 == [])
_fl311 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
_get311 = _fl311[_fl311.index("    def get(r):"):_fl311.index("    fetch = fetch or get")]
check("move in: a file already in its place is not sent again - looked for before the transfer opens",
      "pd_torrents.already_placed(" in _get311
      and _get311.find("pd_torrents.already_placed(") < _get311.find("opened(req, MOVE_SILENCE)"))
_ft311 = [{"index": 0, "priority": 1, "progress": 1.0}, {"index": 1, "priority": 1, "progress": 0.0}]
class _QB311:
    def __init__(self, state, files):
        self.state, self.fs, self.asked, self.steps = state, files, 0, []
    def info(self, h):
        return {"state": self.state}
    def files(self, h):
        return self.fs
    def stop(self, h):
        self.steps.append("stop")
    def priority(self, h, ix, v):
        self.steps.append(("on", sorted(ix), v))
    def recheck(self, h):
        self.asked += 1
        self.steps.append("recheck")
import pd_torrents as _pt311
_was311 = (_pt311.QB, _pt311.load)
try:
    _pt311.load = lambda: {"config": {}}
    _q = [_QB311("stoppedDL", _ft311), _QB311("stoppedDL", _ft311[:1]), _QB311("checkingDL", _ft311)]
    _it = iter(_q)
    _pt311.QB = lambda cfg: next(_it)
    _r311 = [_pt311.recheck_if_unread("h", [0, 1]), _pt311.recheck_if_unread("h", [0]),
             _pt311.recheck_if_unread("h", [0, 1])]
finally:
    _pt311.QB, _pt311.load = _was311
check("move in: a pack is read again only when a file in is short of whole, never while it is being read",
      _r311 == [True, False, False] and [q.asked for q in _q] == [1, 0, 0])
check("move in: the pack is stopped before its new files are switched on, then read - never fetched from the swarm",
      _q[0].steps == ["stop", ("on", [1], 1), "recheck"])
_tr312 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
_ap312 = _tr312[_tr312.index("def already_placed("):_tr312.index("def recheck_if_unread(")]
_pr312 = _tr312[_tr312.index("def place_and_recheck("):_tr312.index("def arrived(")]
check("move in: placing a file never switches it on - only the stopped pack's recheck does",
      ".priority(" not in _ap312 and _pr312.index("if not recheck:") < _pr312.index(".priority("))
_sv310 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_sb310 = _sv310[_sv310.index("    def sync_back_soon("):_sv310.index("    def take_from_the_copy(")]
_tr310 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
_gc310 = _tr310[_tr310.index("def give_to_copy("):_tr310.index("ASKED_DAYS = ")]
_fl310 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
check("home on the copy: listed every round whether Sync back is on or not, as a move, not marked wanted here",
      'if not local().lib.config().get("syncBack"):' not in _sb310
      and _sb310.index("pd_homes.give(") < _sb310.index("if not sync_on:") < _sb310.index("pd_homes.decide(")
      and "copyAsked" not in _gc310.split('"""')[2] and '"move": True' in _gc310
      and 'moves = [r for r in rows if r.get("move")' in _fl310 and "move_in(one, moves)" in _fl310
      and 'take_for_copy(one, [r for r in rows if not r.get("move")])' in _fl310
      and "recheck=False)" in _fl310)
check("home changed: the move starts at once, both ways; a file being sent counts as a copy on its drive",
      'Handler.home_moved(str(body.get("hash") or ""), said["home"])' in _sv310
      and "/follow/packwholes?hash=" in _sv310 and 'transfer_turn(where, False, "move-' in _sv310
      and "while held < 120 and playback_short():" in _sv310)

# ---- a film held here goes to the copy as a move, never a download
_tr320 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
_rq320 = _tr320[_tr320.index("def request("):_tr320.index("def request(") + 2500]
_gv320 = _tr320[_tr320.index("def give_to_copy("):_tr320.index("ASKED_DAYS = ")]
check("home on the copy: a film asked for that is whole here is moved, and a download already listed becomes a move",
      _rq320.index("if whole_file(pack[\"hash\"], film[\"index\"]):") < _rq320.index("return send_to_copy(")
      and "was.update(move)" in _gv320
      and 'r.get("key") for r in pd_torrents.for_copy() if r.get("move")]' in _sv310)

# ---- a copy server the main server manages: its settings kept there, locked here
import pd_follow as _pf330, types as _ty330
_one330 = {"on": True, "allowRemote": True, "master": "http://m", "casualHours": 0, "hours": 4.0,
           "folder": "D:/x", "kinds": {"shuffle": "night"}}
class _Lib330:
    def __init__(self):
        self.cfg = {"follow": dict(_one330)}
        self.saved = 0
    def config(self):
        return dict(self.cfg, follow=dict(self.cfg["follow"]))
    def save_config(self, cfg):
        self.cfg = cfg
        self.saved += 1
_lib330 = _Lib330()
_o330 = dict(_one330)
_ch330 = _pf330.take_managed(_o330, {"casualHours": "10", "kinds": {"shuffle": "always"},
                                     "folder": "C:/elsewhere", "master": "http://evil"}, _lib330)
check("managed copy server: the main server's settings are taken, only its copying ones",
      _ch330 and _o330["casualHours"] == 10.0 and _o330["kinds"]["shuffle"] == "always"
      and _lib330.cfg["follow"]["casualHours"] == 10.0 and _lib330.cfg["follow"]["folder"] == "D:/x"
      and _lib330.cfg["follow"]["master"] == "http://m")
check("managed copy server: the same settings again change nothing and write nothing",
      not _pf330.take_managed(_o330, {"casualHours": 10}, _lib330) and _lib330.saved == 1)
check("managed copy server: not managed, the main server's settings are not taken",
      not _pf330.take_managed(dict(_one330, allowRemote=False), {"casualHours": 10}, _lib330))
_sv330 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("managed copy server: changed on the copy server itself, its copying settings are refused",
      "locked = [k for k in pd_follow.MANAGED if k in body]" in _sv330
      and _sv330.index("locked = [k for k in pd_follow.MANAGED") < _sv330.index("pd_follow.apply_settings(one, body)"))
check("managed copy server: the main server keeps what it sets, seeds from the copy once, hands it over each round",
      'stored.setdefault("managedCopies", {})[known["token"]] = kept' in _sv330
      and "elif not kept:" in _sv330 and '"managed": ((read_settings() or {}).get("managedCopies") or {})' in _sv330
      and 'take_managed(one, said.get("managed"), lib)' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read())

check("pack rounds: a pack's whole files are read once a round, not once a film",
      "whole_here[pack[\"hash\"]] = set(pd_torrents.whole_files(pack[\"hash\"]))" in _sv310
      and "pd_torrents.whole_file(p[\"hash\"], f[\"index\"])" not in _sv310)

# ---- a pack started with files on the disk but never read: read first, not downloaded
import pd_torrents as _pt340
_fs340 = [{"index": 0, "name": "P/a.mkv", "priority": 1, "progress": 0.0, "size": 100},
          {"index": 1, "name": "P/b.mkv", "priority": 1, "progress": 0.0, "size": 100},
          {"index": 2, "name": "P/c.mkv", "priority": 0, "progress": 0.0, "size": 100},
          {"index": 3, "name": "P/d.mkv", "priority": 1, "progress": 1.0, "size": 100}]
_disk340 = {os.path.join("S", "P", "a.mkv"): 100, os.path.join("S", "P", "b.mkv"): 40,
            os.path.join("S", "P", "c.mkv"): 100, os.path.join("S", "P", "d.mkv"): 100}
check("pack start: a file on, whole on the disk and never read is read before any download",
      _pt340.unchecked(_fs340, "S", lambda p: p in _disk340, lambda p: _disk340[p]) == [0])
_tr340 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_torrents.py"), encoding="utf-8").read()
_fe340 = _tr340[_tr340.index("def fetch(pack, film):"):_tr340.index("def needs_read_first(")]
check("pack start: the check comes before the start",
      _fe340.index("again = needs_read_first(") < _fe340.index('qb.recheck(pack["hash"])') < _fe340.index('qb.start(pack["hash"])'))
# a film coming in has its full length set aside by qBittorrent: not a reason to read the pack
_ex340, _sz340 = os.path.exists, os.path.getsize
try:
    os.path.exists, os.path.getsize = (lambda p: p in _disk340), (lambda p: _disk340[p])
    check("pack start: the film asked for, whole on the disk and never read, is read for first",
          _pt340.needs_read_first(_fs340, "S", [], "h", 0))
    check("pack start: another unread film is no reason to read the pack",
          not _pt340.needs_read_first(_fs340, "S", [], "h", 1))
finally:
    os.path.exists, os.path.getsize = _ex340, _sz340

check("moving to its home: on Now playing as a copy, never as a viewer",
      'why="moving to its home", path=where' in _sv310 and '"syncing",' in _sv310[_sv310.index('why="moving to its home"') - 400:_sv310.index('why="moving to its home"')]
      and "WATCHING.sent(sid, len(lump))" in _sv310)

# ---- a pack film held by a torrent of its own goes to the pack's home with that torrent
import pd_torrents as _pt350
_infos350 = {"P": {"hash": "P", "name": "Pack", "save_path": "G:/M", "progress": 0.1},
             "a": {"hash": "a", "name": "Film.A", "save_path": "G:/Classic", "progress": 1.0, "category": "cross-seed"},
             "b": {"hash": "b", "name": "Film.B.mkv", "save_path": "D:/Dl", "progress": 1.0},
             "r": {"hash": "r", "name": "Film.C", "save_path": "G:/Seed", "progress": 1.0, "category": "fl-race"},
             "x": {"hash": "x", "name": "Other", "save_path": "G:/X", "progress": 1.0}}
_files350 = {"P": [{"index": 0, "name": "Pack/Film.A/Film.A.mkv", "size": 10, "progress": 0},
                   {"index": 1, "name": "Pack/Film.B/Film.B.mkv", "size": 20, "progress": 0},
                   {"index": 2, "name": "Pack/Film.C/Film.C.mkv", "size": 30, "progress": 0},
                   {"index": 3, "name": "Pack/Film.D/Film.D.mkv", "size": 40, "progress": 1}],
             "a": [{"name": "Film.A/Film.A.mkv", "size": 10}, {"name": "Film.A/Film.A.nfo", "size": 1}],
             "b": [{"name": "Film.B.mkv", "size": 20}],
             "r": [{"name": "Film.C/Film.C.mkv", "size": 30}],
             "x": [{"name": "Other/Film.A.mkv", "size": 10}]}
_opened350 = []
def _fo350(h):
    _opened350.append(h)
    return _files350[h]
_plan350 = {e["torrent"]: e for e in _pt350.elsewhere_plan({"P"}, _infos350, _fo350)}
check("hand over: films a pack lacks that a torrent here holds whole are found, races are not, only like-named torrents are opened",
      sorted(_plan350) == ["a", "b"] and _plan350["a"]["index"] == 0 and _plan350["a"]["fits"]
      and _plan350["b"]["fits"] and "x" not in _opened350 and "r" not in _opened350)
check("hand over: a torrent is put where the pack keeps the film, never outside it",
      _pt350.location_for(os.path.join("D:", os.sep, "PD", "Pack", "Film.A", "Film.A.mkv"), "Film.A/Film.A.mkv")
      == os.path.join("D:", os.sep, "PD", "Pack")
      and _pt350.location_for(os.path.join("D:", os.sep, "PD", "Pack", "Film.A", "Film.A.mkv"), "Other/Film.A.mkv") == ""
      and _pt350.location_for(os.path.join("D:", os.sep, "PD", "Pack", "x.mkv"), os.path.join("..", "x.mkv")) == ""
      and _pt350.inside(os.path.join("D:", os.sep, "L"), "a/b.nfo") == os.path.join("D:", os.sep, "L", "a", "b.nfo")
      and _pt350.inside(os.path.join("D:", os.sep, "L"), "../b.nfo") == ""
      and _pt350.inside(os.path.join("D:", os.sep, "L"), os.path.join("C:", os.sep, "b.nfo")) == "")
_sv350 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ho350 = _sv350[_sv350.index("    def hand_over(cls"):_sv350.index("    def home_moved(cls")]
check("hand over: its files go from here only after the copy seeds it whole and the file there is this one, never one another torrent seeds",
      _ho350.index("if not e[\"fits\"] or not copy_whole(") < _ho350.index("/follow/taketorrent")
      < _ho350.index("/follow/torrentwhole") < _ho350.index("pd_torrents.fingerprint(path)")
      < _ho350.index("if others:") < _ho350.index("qb.remove(h, files=True)")
      and "HAND_OVER_PER_ROUND" in _ho350)
_tk350 = _sv350[_sv350.index('if path == "/follow/taketorrent":'):_sv350.index('if path == "/torrents/home":')]
check("hand over: the copy takes a torrent only for a film its pack holds whole, where that film is, and seeds it once it reads whole",
      _tk350.index("pd_torrents.whole_file(ph, index)") < _tk350.index("qb.add_at(")
      and "pd_torrents.location_for(" in _tk350 and "pd_torrents.inside(location" in _tk350
      and "seed_when_read" in _tk350 and 'if self.role != "owner":' in _tk350)

check("move in: a transfer silent for five minutes is given up, not waited on for half an hour",
      _pf310.MOVE_SILENCE <= 300 and "opened(req, MOVE_SILENCE)" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read()
      and "timeout=1800" not in _sv310)

# ---- the next episode of what is on a screen gets its subtitle while the one before plays
import pd_subs as _ps360
_good360 = sorted(_ps360.GOOD)[0]
_due360 = _ps360.approaching_due(["e3", "e9"], {("e9", "en"): (-5000, _good360), ("e3", "sv"): (990, "nothing")},
                                 ["en", "sv"], 1000 + 60, 3600)
check("next episode: its subtitle is fetched while the one before plays - not once found good, not again within the wait",
      _due360 == [("e3", "en", True), ("e9", "sv", True)])
_sv360 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_lp360 = _sv360[_sv360.index("    def approaching():"):]
_lp360 = _lp360[:_lp360.index("    def tally():") if "    def tally():" in _lp360 else 6000]
_wk360 = _sv360[_sv360.index('pd_beat.sleep("subcheck", 15 if asked() else 60)'):][:900]
check("next episode: goes ahead of the background gate, the backlog still waits for it",
      "soon = approaching()" in _wk360 and "if not soon and not free():" in _wk360
      and "one = soon[0] if soon else next_one()" in _wk360)

_sv370 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_cp370 = _sv370[_sv370.index("    def send_file_ranged"):][:12000]
check("copies: stop, not walk, while a viewer's buffer runs down - and never longer than the reader waits",
      "while held < Handler.COPY_HOLD_MOST and playback_short():" in _cp370
      and _cp370.index("ceiling = Handler.sync_ceiling(") < _cp370.index("Handler.COPY_HOLD_MOST and playback_short()")
      and "COPY_HOLD_MOST = 45" in _sv370)

_sv380 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_wk380 = _sv380[_sv380.index('pd_beat.sleep("subcheck", 15 if asked() else 60)'):][:3000]
check("subtitle check: a read stopped for a busy drive writes nothing down - not 'no speech' for two weeks",
      'return {"verdict": "stopped"}' in _sv380 and 'if said.get("verdict") == "stopped":' in _wk380
      and _wk380.index('if said.get("verdict") == "stopped":') < _wk380.index("INSERT OR REPLACE INTO subcheck"))
_ns380 = {"os": os, "URGENT_FILES": {os.path.normcase("F:/s/e3.mkv")}}
for _n380 in _ast291.parse(_sv380).body:
    if isinstance(_n380, _ast291.FunctionDef) and _n380.name == "drive_loaded":
        exec(_ast291.get_source_segment(_sv380, _n380), _ns380)
_ns380.update(files_on_screen=lambda: set(), worker_waits=lambda other_drive=False: "the cache is copying",
              playing_drives=lambda: set())
check("subtitle check: the next episode of one playing is read through a busy gate, other files are stopped",
      _ns380["drive_loaded"]("F:/s/e3.mkv") == "" and _ns380["drive_loaded"]("F:/s/e9.mkv") != "")

# ---- numbering that does not match: the previous episode's upload by name, then the text
import pd_subs as _ps390
_res390 = [{"id": 1, "name": "Other.Show.S01", "downloads": 900}, {"id": 2, "name": "The Show", "downloads": 10},
           {"id": 3, "name": "The.Show.S01.DVDRip", "downloads": 500}]
check("subtitles by name: the upload named like the previous episode's comes first, then by downloads",
      [r["id"] for r in _ps390.by_name_order(_res390, "The Show")] == [2, 1, 3]
      and [r["id"] for r in _ps390.by_name_order(_res390, "")] == [1, 3, 2])
check("subtitles by name: a text is this episode when it says so or carries a word of its title",
      _ps390.names_episode("1\n00:00:01 --> 00:00:02\nEpisode 3: Thorpe Park", "", 3)
      and _ps390.names_episode("they went to the harbour", "Harbour Club", 5)
      and not _ps390.names_episode("Merry Christmas everyone", "Harbour Club", 5))
_sv390 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ab390 = _sv390[_sv390.index("    def auto_by_name("):_sv390.index("    def auto_subtitle(")]
check("subtitles by name: each one fetched is read before it is kept, and another episode's is deleted",
      _ab390.index("self.fetch_subtitle(") < _ab390.index("pd_subs.names_episode(") < _ab390.index("os.remove(where)")
      and "return self.auto_by_name(key, after, want[\"language\"], results, src)" in _sv390)

import pd_follow as _pf400, tempfile as _tf400
_d400 = _tf400.mkdtemp()
_w400 = [{"name": "Film.mkv", "why": "on their watchlist", "size": 9},
         {"name": "Show.S01E03.mkv", "why": "kept ahead", "hot": True, "size": 5},
         {"name": "Show.S01E04.mkv", "why": "kept ahead", "hot": False, "size": 5}]
check("copies: the episodes kept ahead of a screen put down any other copy in flight, not only the one on screen",
      _pf400.first_on_screen(_w400, _d400, "Film.mkv") == "Show.S01E03.mkv"
      and _pf400.is_hot(_w400[1]) and not _pf400.is_hot(_w400[0]) and not _pf400.is_hot(_w400[2])
      and "and not is_hot(item)" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_follow.py"), encoding="utf-8").read())

# ---- a season in another order whose files name their episodes: each row onto its own
import pd_library as _pl410
_named410 = {5: "Paper Boat Day", 7: "Lantern Over Water", 9: "Winter Comes Home"}
_rows410 = [("a", 7, "Paper Boat Day"), ("b", 9, "Lantern Over Water"),
            ("c", 5, "Winter Comes Home"), ("d", 10, "")]
check("numbering by title: a reordered season puts each file on the episode it names",
      _pl410.titles_plan(_rows410, _named410) == [("a", 5), ("b", 7), ("c", 9)])
check("numbering by title: not a clean reordering - two files on one number, or onto a number another file keeps - nothing moves",
      _pl410.titles_plan([("a", 7, "Paper Boat Day"), ("b", 9, "Paper Boat Day")], _named410) == [])
check("numbering by title: a file naming an episode whose number another file keeps moves nothing",
      _pl410.titles_plan([("a", 7, "Paper Boat Day"), ("x", 5, "")], _named410) == [])

# ---- copies and moves: one at a time a disk, at both ends
import pd_disk as _pd420
with _pd420.LOCK:
    _h420, _a420, _o420 = dict(_pd420.HELD), dict(_pd420.ASKING), dict(_pd420.OUTSIDE)
    _pd420.HELD.clear(); _pd420.ASKING.clear(); _pd420.OUTSIDE.clear()
try:
    _t1 = _pd420.ask("G:/a.mkv", "copy", "a", ident="t1")
    _t2 = _pd420.ask("G:/b.mkv", "copy", "b", ident="t2")
    _t3 = _pd420.ask("F:/c.mkv", "copy", "c", ident="t3")
    _pd420.done("t1")
    _t2b = _pd420.ask("G:/b.mkv", "copy", "b", ident="t2")
    _pd420.done("t2"); _pd420.done("t3")
    _t4 = _pd420.ask("G:/d.mkv", "copy", "d", ident="t4")
    _t5 = _pd420.ask("G:/e.mkv", "download", "e", ident="t5")
    _pd420.done("t4")
    _t6 = _pd420.ask("G:/f.mkv", "copy", "f", ident="t6")
    _t5b = _pd420.ask("G:/e.mkv", "download", "e", ident="t5")
finally:
    with _pd420.LOCK:
        _pd420.HELD.clear(); _pd420.ASKING.clear(); _pd420.OUTSIDE.clear()
        _pd420.HELD.update(_h420); _pd420.ASKING.update(_a420); _pd420.OUTSIDE.update(_o420)
check("transfers: one at a time a disk - a second on the same disk waits, one on another disk goes",
      _t1[1] == "" and _t2[1] != "" and _t3[1] == "" and _t2b[1] == "")
check("transfers: a copy for a screen takes the next turn ahead of one waiting for less",
      _t5[1] != "" and _t6[1] != "" and _t5b[1] == "")
import pd_follow as _pf420
check("transfers: what arrives is written one transfer a disk at a time, and a busy sender is asked again, not given up on",
      _pf420.drive_lock("D:/x/a.mkv") is _pf420.drive_lock("D:/y/b.mkv")
      and _pf420.drive_lock("D:/a") is not _pf420.drive_lock("E:/a")
      and "except Busy:" in open(_pf420.__file__, encoding="utf-8").read()
      and 'turn, busy = transfer_turn(real, why == "on a screen now",' in _sv310)

# ---- a film on the disk and not read yet is never switched on in a running pack
import pd_torrents as _pt430
class _QB430:
    def __init__(self, state, files):
        self.state, self.fs, self.prio = state, files, []
    def info(self, h): return {"state": self.state, "save_path": "S"}
    def files(self, h): return self.fs
    def priority(self, h, ix, v): self.prio.append((sorted(ix), v))
    def stop(self, h): pass
    def start(self, h): pass
_fs430 = [{"index": 0, "name": "P/moved.mkv", "priority": 1, "progress": 0.97, "size": 100},
          {"index": 1, "name": "P/asked.mkv", "priority": 0, "progress": 0.0, "size": 100},
          {"index": 2, "name": "P/coming.mkv", "priority": 0, "progress": 0.03, "size": 100}]
_disk430 = {os.path.join("S", "P", "moved.mkv"): 100, os.path.join("S", "P", "coming.mkv"): 100}
_was430 = (_pt430.asked_for, _pt430.kept_whole, os.path.exists, os.path.getsize)
try:
    _pt430.asked_for = lambda h: {0, 2}
    _pt430.kept_whole = lambda h: False
    _was430b = _pt430.load
    _pt430.load = lambda: {"downloads": [{"hash": "h", "index": 2, "state": "downloading"}], "packs": []}
    os.path.exists = lambda p: p in _disk430
    os.path.getsize = lambda p: _disk430[p]
    _q430 = _QB430("stoppedDL", _fs430)
    _pt430.only_asked(_q430, "h", extra=[1], files=_fs430)
    _c430 = _QB430("checkingDL", [dict(_fs430[0], priority=0), _fs430[1]])
    _pt430.only_asked(_c430, "h", extra=[1], files=_c430.fs)
finally:
    _pt430.asked_for, _pt430.kept_whole, os.path.exists, os.path.getsize = _was430
    _pt430.load = _was430b
check("pack files: a request switches on its own film, never one on the disk and not yet read",
      ([0], 0) in _q430.prio and any(1 in ix and v == 1 for ix, v in _q430.prio))
check("pack files: a film being downloaded stays on though qBittorrent set its full length aside",
      any(2 in ix and v == 1 for ix, v in _q430.prio) and not any(2 in ix and v == 0 for ix, v in _q430.prio))
check("pack files: while the pack is being read, the films on the disk stay on",
      any(0 in ix and v == 1 for ix, v in _c430.prio))

_sv440 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
_ci440 = _sv440[_sv440.index("def coming_in():"):][:4000]
check("downloads: a film out of a pack shows its own size, progress and time left, not the torrent's",
      '"size": int(pack.get("size") or 0) if pack else' in _ci440
      and '"progress": float(pack.get("progress") or 0) if pack else' in _ci440)
check("followers: the same machine at a new address keeps one row, the live one",
      "if k != where and v.get(\"token\") == held]:" in _sv440
      and "urllib.parse.urlsplit(str(k)).hostname == host]" not in _sv440)

print("RULES FAILED: %d" % len(FAILS) if FAILS else "RULES PASSED")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
