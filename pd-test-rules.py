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
rcv._volume, rcv._set, rcv.threading.Timer = _real[:3]
rcv.KEPT.update(_real[3]); rcv.RAISED.clear()

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

print("RULES FAILED: %d" % len(FAILS) if FAILS else "RULES PASSED")
for f in FAILS:
    print("  -", f)
sys.exit(1 if FAILS else 0)
