#!/usr/bin/env python3
"""What the library answers, asked of the source on a copy of the live papers.

    python pd-test-library.py

Cut off as the gate runs the server: nothing here reaches the network, starts a
process or writes outside the copy. Exit code 0 when every check held, 1 otherwise.
Run by the build before anything is installed.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("pdgate", os.path.join(HERE, "pd-gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
root = tempfile.mkdtemp(prefix="pd-library-")
gate.take_copy(root)
# The new-release list as it was read, held still: once it is twelve hours old the
# server reads it again, and with the copy cut off from the world that reading is half
# blind - the films picked at the start had left the list by the time their pages
# were asked for, and the checks failed on the clock rather than on the code.
_list = os.path.join(root, "streaming.json")
if os.path.exists(_list):
    with open(_list, encoding="utf-8") as f:
        _kept = json.load(f)
    _kept["at"] = time.time()
    with open(_list, "w", encoding="utf-8") as f:
        json.dump(_kept, f)
gate.cut_off(root, 0)

sys.argv = ["pd-server.py", "--root", root]
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("pdserver", os.path.join(HERE, "pd-server.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["pdserver"] = m
spec.loader.exec_module(m)
import pd_torrents
import pd_streaming
pd_torrents.STATE["root"] = root
pd_torrents.STATE["worker"] = True
pd_torrents.STATE["lib"] = lambda: m.local().lib
pd_torrents.load()
api = m.local()
FAILS = []


def check(what, ok, detail=""):
    if not ok:
        FAILS.append("%s %s" % (what, detail))
    return ok


def cards(path, q=None):
    status, _, body = api.handle(path, q or {})
    if status != 200:
        return []
    return json.loads(body)["MediaContainer"].get("Metadata") or []


# ---- 1. a new release being fetched from the tracker: on Recently added, first, with
#         how far it has got; and wearing the same numbers wherever else its poster is
pd_streaming.mark_held(api.lib, every=0)          # the list as the row serves it
new = [r for r in pd_streaming.shown() if not r.get("here") and r.get("poster")]
if not new:
    print("1. skipped: no new release on the list that is not held")
else:
    one = new[0]
    key = one["key"]
    offer = {"state": "downloading", "progress": 0.42, "mbit": 46.3, "eta": 900,
             "who": "owner", "size": 7_000_000_000, "queueKey": "", "mine": False,
             "tracker": True, "release": "A.Release.1080p.WEB-DL-GRP.mkv",
             "version": "1080p WEB-DL - GRP"}
    coming = {"ratingKey": key, "type": "movie", "thumb": "/art/%s/poster" % key,
              "title": one["title"], "year": one.get("year"), "offered": True,
              "offer": offer}
    before = cards("/library/sections/1/recentlyAdded")
    check("1 not on the shelf before it is fetched",
          all(c.get("ratingKey") != key for c in before))
    api.coming_from_tracker = lambda owner=True, who="": [dict(coming, offer=dict(offer))]
    shelf = cards("/library/sections/1/recentlyAdded")
    mine = [c for c in shelf if c.get("ratingKey") == key]
    check("1 on Recently added while it comes in", len(mine) == 1, "%d cards" % len(mine))
    if mine:
        # with the films coming in on the live state, if any: only those ahead of it
        check("1 at the front of the shelf",
              all(c.get("offer") for c in shelf[:shelf.index(mine[0])]),
              "at %d" % shelf.index(mine[0]))
        check("1 says how far", (mine[0].get("offer") or {}).get("progress") == 0.42
              and (mine[0].get("offer") or {}).get("state") == "downloading")
        check("1 dated now, so a client sorting by arrival keeps it first",
              abs(int(mine[0].get("addedAt") or 0) - time.time()) < 120)
    check("1 the shelf holds no title twice",
          len({c.get("ratingKey") for c in shelf}) == len(shelf))
    page = cards("/library/metadata/" + key)
    check("1 its own page says how far",
          bool(page) and (page[0].get("offer") or {}).get("progress") == 0.42,
          str((page or [{}])[0].get("offer"))[:60])
    check("1 its page is still a title to ask for, not a pack's film",
          bool(page) and page[0].get("askable") and not page[0].get("offered"))
    row = [c for c in cards("/library/streaming") if c.get("ratingKey") == key]
    check("1 the row of new releases shows it coming in",
          bool(row) and (row[0].get("offer") or {}).get("state") == "downloading")
    released = [c for c in cards("/library/sections/1/all",
                                 {"type": ["1"], "sort": ["originallyAvailableAt:desc"]})
                if c.get("ratingKey") == key]
    check("1 so does the shelf of released films",
          bool(released) and (released[0].get("offer") or {}).get("state") == "downloading")
    others = [c for c in cards("/library/streaming") if c.get("ratingKey") != key]
    check("1 no other new release wears a download",
          all(not c.get("offer") for c in others if c.get("askable")))
    # two coming in: the one asked for last is furthest left, whichever is listed first
    if len(new) > 1:
        two = new[1]
        older = dict(coming, offer=dict(offer), askedAt=1000)
        newer = {"ratingKey": two["key"], "type": "movie", "title": two["title"], "year": two.get("year"),
                 "offered": True, "offer": dict(offer, progress=0.0), "askedAt": 2000}
        for listed in ([older, newer], [newer, older]):
            api.coming_from_tracker = lambda owner=True, who="", listed=listed: [dict(c) for c in listed]
            # the copy may hold real downloads too, asked for since: only these two are judged
            front = [c for c in cards("/library/sections/1/recentlyAdded")
                     if c.get("ratingKey") in (two["key"], key)]
            check("1 of two coming in, the one asked for last is further left",
                  [c.get("ratingKey") for c in front] == [two["key"], key],
                  str([c.get("ratingKey") for c in front]))
            check("1 and dated so a client ordering by arrival keeps them so",
                  len(front) == 2 and front[0].get("addedAt", 0) > front[1].get("addedAt", 0)
                  and abs(front[0]["addedAt"] - time.time()) < 120)
        check("1 a download with no time known comes after one with",
              [c.get("ratingKey") for c in api._newest_asked_first(
                  [{"ratingKey": "a"}, {"ratingKey": "b", "askedAt": 5}])] == ["b", "a"])
        api.coming_from_tracker = lambda owner=True, who="": [dict(coming, offer=dict(offer))]
    # the same film already held here is not put on the shelf a second time
    # a film: the games arrived stand on this shelf too, as episodes
    held = next((c for c in before if not c.get("offered") and c.get("type") == "movie"), None)
    if held:
        api.coming_from_tracker = lambda owner=True, who="": [
            dict(coming, ratingKey="rtffffffffff", title=held["title"], year=held.get("year"))]
        again = cards("/library/sections/1/recentlyAdded")
        check("1 a film already here is not listed again as coming in",
              all(c.get("ratingKey") != "rtffffffffff" for c in again))
    # a download list that cannot be read is an empty one, not a failed shelf
    def broken(owner=True, who=""):
        raise RuntimeError("the client is away")
    api.coming_from_tracker = broken
    check("1 the shelf stands when the download list cannot be read",
          len(cards("/library/sections/1/recentlyAdded")) == len(before))
    del api.coming_from_tracker
    print("1. a tracker download on the shelves and its page", flush=True)

# ---- 2. where a new release can be watched: on its page, each service with its mark
if new:
    listed = 0
    for one in new[:12]:
        page = cards("/library/metadata/" + one["key"])
        if not check("2 the page answers", bool(page), one["key"]):
            continue
        where = page[0].get("providers")
        check("2 the page says where, even when that is nowhere", isinstance(where, list),
              "%s: %r" % (one.get("title"), where))
        for p in where or []:
            listed += 1
            check("2 a service has a name, a kind and a mark kept here",
                  bool(p.get("name")) and p.get("kind") in ("stream", "store")
                  and str(p.get("logo") or "").startswith("/art/provider/"), str(p)[:90])
        check("2 no service twice", len({p.get("name") for p in where or []}) == len(where or []))
    for bad in ("/art/provider/../settings.json", "/art/provider/a.exe", "/art/provider/"):
        check("2 only a picture's name is taken: %s" % bad, api.handle(bad, {})[0] == 404)
    print("2. where to watch, on %d pages: %d services listed" % (min(12, len(new)), listed),
          flush=True)

# ---- 3. the services chosen under Settings are the only ones a page shows
def settle(**changes):
    path = os.path.join(root, "settings.json")
    with open(path, encoding="utf-8") as f:
        kept = json.load(f)
    kept.update(changes)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f)
    settle.n += 7
    os.utime(path, (time.time() + settle.n, time.time() + settle.n))
settle.n = 0

if new:
    settle(watchServices=["A Service Nobody Runs"], watchRegion="")
    shown = sum(len(cards("/library/metadata/" + one["key"])[0].get("providers") or [])
                for one in new[:12])
    check("3 none of the chosen services carries it: none shown", shown == 0, "%d shown" % shown)
    settle(watchServices=[], watchRegion="")
    back = sum(len(cards("/library/metadata/" + one["key"])[0].get("providers") or [])
               for one in new[:12])
    check("3 none chosen is all of them again", back > 0, "%d shown" % back)
    settle(watchServices=[], watchRegion="ZZ")
    # a country nothing is out in: where it is out already, and each badge names the
    # country - never ZZ, which has nothing
    elsewhere = [p for one in new[:12]
                 for p in (cards("/library/metadata/" + one["key"])[0].get("providers") or [])]
    check("3 a country with no services listed shows where it is out, and says which country",
          elsewhere and all(p.get("country") and p.get("country") != "ZZ" for p in elsewhere),
          "%d shown" % len(elsewhere))
    settle(watchServices=[], watchRegion="")
    print("3. the services chosen under Settings", flush=True)

# ---- 4. the studios behind a film, on its page, asked of the catalogue once
films = [c for c in cards("/library/sections/1/recentlyAdded")
         if not c.get("offered") and c.get("type") == "movie"][:6]
named = 0
for film in films:
    page = cards("/library/metadata/" + str(film["ratingKey"]))
    if not check("4 the page answers", bool(page), str(film.get("title"))):
        continue
    studios = page[0].get("studios")
    check("4 the page lists its studios, even when it has none", isinstance(studios, list),
          "%s: %r" % (film.get("title"), studios))
    for st in studios or []:
        named += 1
        check("4 a studio has a name, and a mark kept here or none",
              bool(st.get("name")) and (st.get("logo") == ""
                                        or str(st.get("logo")).startswith("/art/studio/")),
              str(st)[:90])
    check("4 three studios at most, none twice",
          len(studios or []) <= 3
          and len({str(x.get("name")).lower() for x in studios or []}) == len(studios or []))
if films:
    con = api.lib.db()
    try:
        kept = con.execute("SELECT COUNT(*) FROM film_studios").fetchone()[0]
    finally:
        con.close()
    check("4 kept with the library after the first asking", kept >= 1 or named == 0,
          "%d kept, %d named" % (kept, named))
    for bad in ("/art/studio/../settings.json", "/art/studio/a.exe"):
        check("4 only a picture's name is taken: %s" % bad, api.handle(bad, {})[0] == 404)
    print("4. studios on %d film pages: %d named" % (len(films), named), flush=True)

# ---- 5. an episode whose file has gone and that a pack carries is the pack's to give
con = api.lib.db()
try:
    gone = con.execute(
        """SELECT e.item_id, i.title, e.season, e.number FROM episode e JOIN item i ON i.id = e.item_id
           WHERE e.season > 0 AND NOT EXISTS (SELECT 1 FROM file f WHERE f.episode_id = e.id)""").fetchall()
finally:
    con.close()
seasons, stood_in = {}, 0
for r in gone:
    seasons.setdefault((r["item_id"], r["title"], int(r["season"])), set()).add(int(r["number"] or 0))
for (item, title, season), numbers in sorted(seasons.items()):
    packed = {int(e.get("index") or 0)
              for e in pd_torrents.offered_episodes(pd_torrents.show_key(title), season)}
    listing = cards("/library/metadata/%s-s%d/children" % (item, season))
    by_number = {}
    for c in listing:
        by_number.setdefault(int(c.get("index") or 0), []).append(c)
    check("5 one row an episode: %s season %d" % (title, season),
          all(len(v) == 1 for n, v in by_number.items() if n > 0),
          str({n: len(v) for n, v in by_number.items() if len(v) > 1}))
    for n in sorted(numbers & packed):
        one = (by_number.get(n) or [{}])[0]
        stood_in += 1
        check("5 %s S%02dE%02d has no file and a pack carries it: the pack's entry is listed, under the episode's own key"
              % (title, season, n), bool(one.get("offered")) and str(one.get("ratingKey", "")).startswith("e"),
              "listed as %r" % one.get("ratingKey"))
print("5. episodes with no file: %d seasons looked at, %d episodes a pack stands in for"
      % (len(seasons), stood_in), flush=True)

# ---- 6. a skip rule: on every held episode's card it covers, the most specific winning
import pd_leads
con = api.lib.db()
try:
    held = con.execute(
        """SELECT e.item_id, e.season, COUNT(DISTINCT e.number) n FROM episode e
           JOIN file f ON f.episode_id = e.id WHERE e.season > 0
           GROUP BY e.item_id, e.season HAVING n >= 3 ORDER BY n DESC""").fetchall()
finally:
    con.close()
show = held[0]["item_id"] if held else ""
mine = [int(r["season"]) for r in held if r["item_id"] == show]
check("6 a programme with two held seasons to try a rule on", len(mine) >= 2, str(mine))
if len(mine) >= 2:
    ruled, other = mine[0], mine[1]
    eps = lambda season: [c for c in cards("/library/metadata/%s-s%d/children" % (show, season))
                          if not c.get("offered")]
    check("6 no rule, no skip", not any("skipStart" in c for c in eps(ruled)))
    first = int(eps(ruled)[0]["index"])
    stored = m.read_settings() or {}
    stored["skipStart"] = pd_leads.with_rule(
        pd_leads.with_rule(None, show, ruled, 0, 12.5), show, ruled, first, 28)
    m.write_settings(stored, merge=False)
    got = eps(ruled)
    check("6 the season's rule on each of its episodes, one episode's own rule on that one",
          bool(got) and all(c.get("skipStart") == (28 if int(c["index"]) == first else 12.5)
                            for c in got), str([(c["index"], c.get("skipStart")) for c in got]))
    check("6 another season has none", not any("skipStart" in c for c in eps(other)))
    one = cards("/library/metadata/%s" % got[-1]["ratingKey"])
    check("6 and on the episode's own page", bool(one) and one[0].get("skipStart") == 12.5)
    stored["skipStart"] = pd_leads.with_rule(
        pd_leads.with_rule(stored["skipStart"], show, ruled, 0, 0), show, ruled, first, 0)
    m.write_settings(stored, merge=False)
    check("6 rules removed: none left in the settings, none on the cards",
          stored["skipStart"] == {} and not any("skipStart" in c for c in eps(ruled)))
    print("6. skip rule tried on %d episodes of one season" % len(got), flush=True)

# ---- 7. every worker with its state, for the monitor's boxes
import pd_beat
check("7 a worker's state from its heartbeat",
      m.worker_state(None) == "off" and m.worker_state({}) == "off"
      and m.worker_state({"step": "x", "next": None, "ok": True}) == "working"
      and m.worker_state({"step": "waiting", "next": 40, "ok": True}) == "waiting"
      and m.worker_state({"step": "x", "next": None, "ok": False}) == "stalled"
      and m.worker_state({"step": "waiting", "next": 0, "ok": False}) == "stalled")
kept_beats, kept_now = dict(pd_beat.BEATS), dict(m.MEASURE_NOW)
pd_beat.BEATS.clear()
m.MEASURE_NOW.update(name="", since=0)
said = {w["name"]: w for w in m.workers_now()}
check("7 all of them listed, working or not: %s" % sorted(said),
      set(m.WORKERS.values()) <= set(said) and {"Loudness", "Credits analyser", "Library scan",
                                                "Writing subtitles"} <= set(said))
check("7 none started: none lit", all(w["state"] in ("off", "waiting") for w in said.values()),
      str({n: w["state"] for n, w in said.items() if w["state"] not in ("off", "waiting")}))
pd_beat.beat("subcheck", "measuring")
pd_beat.rest("torrents", 30)
m.MEASURE_NOW.update(name="a file.mkv", since=time.time() - 20)
said = {w["name"]: w for w in m.workers_now()}
check("7 in a step: working, with the step; asleep: waiting, with when it wakes",
      said["Subtitle check"]["state"] == "working" and said["Subtitle check"]["step"] == "measuring"
      and said["Download worker"]["state"] == "waiting" and said["Download worker"]["step"] == ""
      and 25 <= int(said["Download worker"]["next"]) <= 30)
check("7 a file being measured lights Loudness, named, with how long",
      said["Loudness"]["state"] == "working" and said["Loudness"]["step"] == "a file.mkv"
      and 19 <= said["Loudness"]["for"] <= 25, str(said["Loudness"]))
pd_beat.BEATS.clear(); pd_beat.BEATS.update(kept_beats)
m.MEASURE_NOW.clear(); m.MEASURE_NOW.update(kept_now)
print("7. %d workers listed" % len(said), flush=True)

# ---- 8. the typical loudness a passed-through raise is counted from, films and episodes
m.TYPICAL["at"] = 0.0
film, episode = m.typical_loudness(False), m.typical_loudness(True)
check("8 a typical loudness for films and one for episodes, each a plausible reading: %s %s" % (film, episode),
      film is not None and episode is not None and -35 < film < -10 and -35 < episode < -10)
m.TYPICAL.update(film=-99.0)
check("8 kept half an hour rather than worked out for every film", m.typical_loudness(False) == -99.0)
m.TYPICAL["at"] = 0.0
import pd_receiver
check("8 a film measuring typical is raised 5 dB", pd_receiver.level_for(m.typical_loudness(False), m.typical_loudness(False)) == 5.0)
print("8. typical loudness: films %s, episodes %s" % (film, episode), flush=True)

# ---- 9. the readings a file lacks are made, and no more: a Dolby track gets the one
#         with its own compression off beside the first, without the first being redone
import pd_gpu
asked = []
real_l, real_d = pd_gpu.loudness, pd_gpu.dialnorm
pd_gpu.loudness = lambda path, length=0, flat=False, **k: (asked.append("flat" if flat else "plain") or (-20.0 if flat else -24.0))
pd_gpu.dialnorm = lambda path, **k: -27
con = api.lib.db()
dolby = con.execute("SELECT id, path, size FROM file WHERE acodec IN ('ac3','eac3') AND path IS NOT NULL LIMIT 1").fetchone()
other = con.execute("SELECT id, path, size FROM file WHERE acodec IN ('aac','mp3','opus') AND path IS NOT NULL LIMIT 1").fetchone()
con.close()
if check("9 a Dolby file and one that is not, to measure", bool(dolby) and bool(other)):
    d, o = str(dolby["id"]), str(other["id"])
    with m.LOUDNESS_LOCK:
        m.read_loudness()
        m.LOUDNESS[d] = {"lufs": -26.5, "size": int(dolby["size"] or 0), "how": "spread", "when": 1, "dialnorm": -24}
        m.LOUDNESS[o] = {"lufs": -19.0, "size": int(other["size"] or 0), "how": "spread", "when": 1}
    took = m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
    one = m.LOUDNESS.get(d) or {}
    check("9 a Dolby file with its first reading: only the second is made, the first left as it was",
          took and asked == ["flat"] and one.get("lufs") == -26.5 and one.get("flat") == -20.0
          and one.get("dialnorm") == -24, "%s %s" % (asked, one))
    del asked[:]
    check("9 with both it is not read again", not m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0) and not asked)
    check("9 a file that is not Dolby and has its reading is not read again",
          not m.take_readings(other["id"], other["path"], other["size"], 3000.0) and not asked)
    with m.LOUDNESS_LOCK:
        m.LOUDNESS.pop(d, None)
    took = m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
    one = m.LOUDNESS.get(d) or {}
    check("9 a Dolby file with none: both, in that order", took and asked == ["plain", "flat"]
          and one.get("lufs") == -24.0 and one.get("flat") == -20.0 and one.get("how") == "spread", str(one))
    pd_gpu.loudness = lambda path, length=0, flat=False, **k: (None if flat else -24.0)
    with m.LOUDNESS_LOCK:
        m.LOUDNESS.pop(d, None)
    m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
    check("9 the second unreadable: the first stands in, so it is not tried for ever",
          (m.LOUDNESS.get(d) or {}).get("flat") == -24.0
          and not pd_receiver.wants_measuring(m.LOUDNESS.get(d), "ac3"))
    pd_gpu.loudness = lambda path, length=0, flat=False, **k: -70.0
    with m.LOUDNESS_LOCK:
        m.LOUDNESS.pop(d, None)
    check("9 silence is no reading, counted as a failed one",
          not m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
          and (m.LOUDNESS.get(d) or {}).get("lufs") is None
          and (m.LOUDNESS.get(d) or {}).get("failed") == 1, str(m.LOUDNESS.get(d)))
    m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
    m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
    check("9 three failed at one size: left alone until the size changes",
          not pd_receiver.wants_measuring(m.LOUDNESS.get(d), "ac3", dolby["size"])
          and pd_receiver.wants_measuring(m.LOUDNESS.get(d), "ac3", dolby["size"] + 1))
    with m.LOUDNESS_LOCK:
        m.LOUDNESS.pop(d, None)
pd_gpu.loudness, pd_gpu.dialnorm = real_l, real_d
print("9. readings made for a file", flush=True)

# ---- 10. what an encode is held to: what is set under Quality, the file's own rate, nothing else
Q = m.Handler.quality_for
none, unset = {"height": 0, "mbit": 0}, {"height": 0, "mbit": 0}
check("10 nothing asked, nothing set: the file's own rate and no other number", Q(0, 0, none, unset, 20) == (0, 20))
check("10 a ceiling set under Quality holds it", Q(0, 0, none, {"height": 1080, "mbit": 8}, 20) == (1080, 8))
check("10 the viewer's own setting is used where they asked for nothing",
      Q(0, 0, {"height": 720, "mbit": 5}, unset, 20) == (720, 5))
check("10 what the player asked for, held to the ceiling", Q(0, 12, none, {"height": 0, "mbit": 8}, 20) == (0, 8)
      and Q(0, 4, none, {"height": 0, "mbit": 8}, 20) == (0, 4))
check("10 never more than the file carries, whatever is asked or set",
      Q(0, 12, none, unset, 3) == (0, 3) and Q(0, 0, none, {"height": 0, "mbit": 8}, 3) == (0, 3))
check("10 a file of unknown rate with nothing set is given no number",
      Q(0, 0, none, unset, 0) == (0, 0))
src = open(os.path.join(HERE, "pd-server.py"), encoding="utf-8").read()
check("10 no megabits for outside viewers that the Quality page does not show",
      "awayMbit" not in src and "at_home() and not mbit" not in src)
rows = {r["name"]: r["value"] for g in m.Handler.settled_numbers() for r in g["rows"]}
check("10 the Advanced page says so, and lists the receiver's two numbers",
      rows.get("Away, with nothing set") == "no ceiling" and rows.get("Receiver, a typical track") == "+5 dB"
      and rows.get("Receiver, corrected within") == "4 dB", str({k: v for k, v in rows.items() if "Away" in k or "Receiver" in k}))
print("10. what an encode is held to", flush=True)

# ---- 11. a file noted as giving no Dolby: for that file, under the command in use
import pd_gpu
N, MAKE = m.Handler.noted_no_dolby, pd_gpu.DOLBY_MAKE
check("11 noted under the command in use: not asked again", N({"f": [10, MAKE]}, "f", 10))
check("11 noted under an older command, or before commands were counted: asked again",
      not N({"f": [10, MAKE - 1]}, "f", 10) and not N({"f": 10}, "f", 10))
check("11 a file replaced since, or one never noted: asked", not N({"f": [10, MAKE]}, "f", 11)
      and not N({"g": [10, MAKE]}, "f", 10) and not N(None, "f", 10))
class _Notes:
    store = {"noDolby": {"old.mkv": 5}}
    def settings_file(self): return self.store
    noted_no_dolby = staticmethod(N)
_file = os.path.join(root, "nodolby.bin")
open(_file, "wb").write(b"x" * 321)
_real, _wrote = m.write_settings, []
m.write_settings = lambda stored, merge=True: _wrote.append(json.loads(json.dumps(stored)))
try:
    m.Handler.note_no_dolby(_Notes(), _file)
finally:
    m.write_settings = _real
_Notes.store = _wrote[-1] if _wrote else {}
check("11 what is written down is read back as noted, the older note beside it is not",
      m.Handler.no_dolby_here(_Notes(), _file) and not N(_Notes.store.get("noDolby"), "old.mkv", 5),
      str(_Notes.store))
print("11. files noted as giving no Dolby", flush=True)

# ---- 12. a season on a shelf wears its own poster, and so does the shelf it stands for
con = api.lib.db()
try:
    two = con.execute("SELECT item_id FROM episode WHERE season > 0 GROUP BY item_id "
                      "HAVING COUNT(DISTINCT season) >= 2 AND item_id IN "
                      "(SELECT id FROM item WHERE poster IS NOT NULL AND poster != '') LIMIT 1").fetchone()
    film = con.execute("SELECT id FROM item WHERE type='movie' AND poster IS NOT NULL AND poster != '' LIMIT 1").fetchone()
    if not two or not film:
        print("12. skipped: no programme of two seasons with a poster, or no film")
    else:
        show = str(two["item_id"])
        a, b = [int(r["season"]) for r in con.execute(
            "SELECT DISTINCT season FROM episode WHERE item_id=? AND season > 0 ORDER BY season LIMIT 2", (show,))]
        ep = {n: str(con.execute("SELECT id FROM episode WHERE item_id=? AND season=? ORDER BY number LIMIT 1",
                                 (show, n)).fetchone()["id"]) for n in (a, b)}
        art = lambda n: "/art/%s/season/%d" % (show, n)
        H = m.Handler
        cards_ = [H.season_card(None, con, "%s-s%d" % (show, n), []) for n in (a, b)]
        check("12 each season card wears its season's poster, not the programme's",
              [c.get("thumb") for c in cards_] == [art(a), art(b)], str([c.get("thumb") for c in cards_]))
        check("12 a shelf of episodes with no poster chosen: the first one's season",
              H.shelf_face(con, {}, [ep[b], ep[a]]) == (ep[b], art(b)), str(H.shelf_face(con, {}, [ep[b], ep[a]])))
        check("12 a shelf of season keys: that season's",
              H.shelf_face(con, {}, ["%s-s%d" % (show, b)]) == ("%s-s%d" % (show, b), art(b)))
        chosen = "%s-s%d" % (show, b)
        check("12 a season chosen for a shelf that holds the programme, or an episode of it, stands",
              H.shelf_face(con, {"cover": chosen}, [show]) == (chosen, art(b))
              and H.shelf_face(con, {"cover": chosen}, [ep[a], ep[b]]) == (chosen, art(b)))
        check("12 a season the shelf holds nothing of does not: the first key's instead",
              H.shelf_face(con, {"cover": chosen}, [ep[a]]) == (ep[a], art(a)), str(H.shelf_face(con, {"cover": chosen}, [ep[a]])))
        check("12 a film is its own poster, and an empty shelf has none",
              H.shelf_face(con, {}, [str(film["id"])]) == (str(film["id"]), "/art/%s/poster" % film["id"])
              and H.shelf_face(con, {}, []) == ("", None), str(H.shelf_face(con, {}, [str(film["id"])])))
finally:
    con.close()
print("12. season posters on a shelf", flush=True)

# ---- 13. background work runs under playback with a full buffer, and waits for a viewer
#          falling behind, the cache copying, a download or a scan
class _Live:
    rows = []
    def snapshot(self):
        return list(self.rows)
class _Engine:
    streams = []
    def status(self):
        return {"streams": list(self.streams)}
_was = (m.WATCHING, m.engine, m.disks_busy)
m.WATCHING, m.engine, m.disks_busy = _Live(), (lambda: _Engine()), (lambda: "")
try:
    _Live.rows = [{"how": "direct play", "seconds": 60, "average": 1.0, "mbit": 9.0}]
    check("13 somebody on direct play with a full buffer: quiet", m.quiet_now() is True)
    _Live.rows = [{"how": "direct play", "seconds": 60, "average": 1.0, "mbit": 0.1}]
    m.Handler.DIPS["None/None"] = __import__("time").time() - 11   # behind for 11 s now
    check("13 somebody falling behind: not quiet", m.quiet_now() is False)
    _Live.rows = [{"how": "syncing"}]
    check("13 a copy being made: not quiet", m.quiet_now() is False)
    _Live.rows = []
    check("13 nobody: quiet", m.quiet_now() is True)
    m.disks_busy = lambda: "a download is coming in"
    check("13 a download coming in: not quiet", m.quiet_now() is False)
    m.disks_busy = lambda: ""
    _Engine.streams = ["gpu1"]
    check("13 an encode running: not quiet", m.quiet_now() is False)
finally:
    m.WATCHING, m.engine, m.disks_busy = _was
# a release taken from the tracker wears the poster of the page it was started from
import pd_tracker as _ptk
_was_tc = (m.coming_in, m.landing, dict(_ptk.STATE.get("taken") or {}))
try:
    m.coming_in = lambda: [{"name": "Some.Gone.Film.2026.1080p.WEB.H264-GRP", "progress": 0.5,
                            "mbit": 9.0, "eta": 60, "size": 5, "who": "Someone", "from": "tracker",
                            "added": 1, "hash": "abc123", "state": "downloading"}]
    m.landing = lambda: []
    _ptk.STATE.setdefault("taken", {})["abc123"] = {"key": "rt0000000001"}
    _tc = m.tracker_coming()
    check("13 a tracker download's poster is its page's, by the key written when it was taken",
          len(_tc) == 1 and _tc[0]["ratingKey"] == "rt0000000001"
          and _tc[0]["thumb"] == "/art/rt0000000001/poster", str(_tc[:1]))
finally:
    m.coming_in, m.landing = _was_tc[0], _was_tc[1]
    _ptk.STATE["taken"] = _was_tc[2]
# a copy runs flat out while nothing is wrong, steps down by tenths when a viewer is short,
# tests without a ceiling, and takes the best it reached; a player nearly out cuts it to a tenth
import types as _tyC
H_ = m.Handler
_keepC = (H_.a_film_is_struggling, H_.disk_reading_slow, H_.how_thin, H_.link_seen, dict(H_.PACE), m.time)
try:
    _st = {"short": False, "thin": "", "t": 1000.0}
    m.time = _tyC.SimpleNamespace(time=lambda: _st["t"], sleep=lambda s: None,
                                  strftime=__import__("time").strftime, monotonic=__import__("time").monotonic)
    H_.a_film_is_struggling = staticmethod(lambda rows, side=None: _st["short"])
    H_.disk_reading_slow = classmethod(lambda cls, now=None: False)
    H_.how_thin = staticmethod(lambda now=None, side=None: _st["thin"])
    H_.link_seen = classmethod(lambda cls, mbit, side: 100.0)
    H_.PACE.clear()
    def at(t, syncing, short=False, thin=""):
        _st.update(t=t, short=short, thin=thin)
        return round(H_.sync_ceiling({}, 10, syncing, "home"), 1)
    seq = [at(1000, 300), at(1001, 300, True), at(1002, 300, True), at(1003, 243),
           at(1005, 400), at(1009, 400)]
    H_.PACE.clear()
    panic = [at(2000, 300), at(2001, 300, thin="panic")]
    check("13 copy: free; -10%% a second when short; a free test; free again at the new best; panic to a tenth",
          seq == [0.0, 270.0, 243.0, 0.0, 0.0, 0.0] and panic == [0.0, 30.0], str((seq, panic)))
finally:
    (H_.a_film_is_struggling, H_.disk_reading_slow, H_.how_thin, H_.link_seen) = _keepC[:4]
    H_.PACE.clear(); H_.PACE.update(_keepC[4]); m.time = _keepC[5]
# a dip counts after 10 s, and never for a player that reports its buffer
H_ = m.Handler
_keepD = (H_.running_dry, dict(H_.AHEAD), dict(H_.DIPS), m.time)
try:
    import types as _tyD
    _tD = {"t": 5000.0}
    m.time = _tyD.SimpleNamespace(time=lambda: _tD["t"], sleep=lambda s: None,
                                  strftime=__import__("time").strftime, monotonic=__import__("time").monotonic)
    H_.running_dry = staticmethod(lambda now=None, side=None: False)
    H_.AHEAD.clear(); H_.DIPS.clear()
    row = {"how": "direct play", "seconds": 60, "average": 1.0, "mbit": 1.0, "who": "g", "address": "1.2.3.4"}
    first = H_.a_film_is_struggling([row])
    _tD["t"] += 3.5
    scene = H_.a_film_is_struggling([row])
    _tD["t"] += 7.0
    later = H_.a_film_is_struggling([row])
    H_.DIPS.clear()
    H_.AHEAD["g/1.2.3.4"] = (25.0, _tD["t"], "home", 25.0, False, False)
    first2 = H_.a_film_is_struggling([row])
    _tD["t"] += 11.0
    H_.AHEAD["g/1.2.3.4"] = (25.0, _tD["t"], "home", 25.0, False, False)
    reported = H_.a_film_is_struggling([row])
    check("13 dip: not at once, not after 3.5 s, yes after 10 s, never for a player reporting its buffer",
          (first, scene, later, first2, reported) == (False, False, True, False, False),
          str((first, scene, later, first2, reported)))
finally:
    H_.running_dry = _keepD[0]
    H_.AHEAD.clear(); H_.AHEAD.update(_keepD[1]); H_.DIPS.clear(); H_.DIPS.update(_keepD[2]); m.time = _keepD[3]
import pd_release, pd_vtt
check("13 the rules moved out are the server's own names still",
      m.video_mbit is pd_release.video_mbit and m.release_is is pd_release.release_is
      and m.shift_vtt is pd_vtt.shift_vtt and m.STAMP is pd_vtt.STAMP)
print("13. quiet check, moved modules", flush=True)

# ---- 14. a new release that has arrived since the list was read is held, not asked for
con = api.lib.db()
try:
    _f = con.execute("SELECT id, title, year, tmdb_id FROM item WHERE type='movie' AND tmdb_id > 0 LIMIT 1").fetchone()
finally:
    con.close()
if not _f:
    print("14. skipped: no film with a catalogue number")
else:
    _kept = list(pd_streaming.STATE["rows"])
    try:
        pd_streaming.STATE["rows"] = [
            {"key": "rtaaaa", "title": _f["title"], "year": _f["year"], "tmdb": int(_f["tmdb_id"]), "kind": "movie"},
            {"key": "rtbbbb", "title": "A Film Nobody Holds Zzqx", "year": 2031, "tmdb": 999999991, "kind": "movie"},
            {"key": "rtcccc", "title": "Another Nobody Holds Zzqx", "year": 2031, "tmdb": 999999992, "kind": "movie",
             "here": "000000000000"},
        ]
        changed = pd_streaming.mark_held(api.lib, every=0)
        got = {r["key"]: r.get("here") for r in pd_streaming.STATE["rows"]}
        check("14 the film the library holds is marked with its key", got["rtaaaa"] == str(_f["id"]), str(got))
        check("14 one nobody holds stays something to ask for", got["rtbbbb"] is None)
        check("14 one marked as held that has left the library is asked for again", got["rtcccc"] is None)
        check("14 two rows changed", changed == 2, str(changed))
        check("14 asked again within the minute, the library is not read again",
              pd_streaming.mark_held(api.lib) == 0)
    finally:
        pd_streaming.STATE["rows"] = _kept
print("14. new releases already held", flush=True)

# ---- 15. what is listed as being copied: there, or going to be fetched
_q = m.Handler.in_the_queue
check("15 a row to be fetched is listed", _q({"key": "e1"}, set()))
check("15 a row the other machine holds is listed, kept-only or not",
      _q({"key": "e1", "kept": True}, {"e1"}) and _q({"key": "e1"}, {"e1"}))
check("15 a row only kept where it already is, and not there, is not listed as next",
      not _q({"key": "e1", "kept": True}, set()) and not _q({"key": "e1", "kept": True}, {"e2"}))
print("15. the copy queue", flush=True)

# ---- 16. the receiver's answer to a screen it is not raised for
_no = m.Handler.receiver_not_for
check("16 a viewer outside the house is told off, the reason every app shows nothing for",
      (_no(False, False) or {}).get("why") == "off" and (_no(False, True) or {}).get("ok") is False)
check("16 so is another screen in the house", (_no(True, False) or {}).get("why") == "off")
check("16 the reason is still said, beside it, for the log",
      (_no(False, False) or {}).get("because") == "not in the house"
      and "device" in str((_no(True, False) or {}).get("because")))
check("16 the screen set for the receiver, in the house, is not turned away", _no(True, True) is None)
print("16. the receiver's refusals", flush=True)

# ---- 17. a settings write: on the disk before the swap, a spare and a dated copy beside it
_sp = m.settings_path()
_was = m.read_settings()
_seen, _fs = [], os.fsync
os.fsync = lambda fd: (_seen.append(1), _fs(fd))[1]
try:
    m.write_settings(dict(_was, gateProbe=17))
finally:
    os.fsync = _fs
check("17 the write was forced to the disk: file, spare and dated copy", len(_seen) >= 2, str(len(_seen)))
check("17 it reads back whole", (m.read_settings() or {}).get("gateProbe") == 17)
check("17 a spare that reads stands beside it", m.pd_safe.readable(_sp + ".bak"))
check("17 and one dated copy for today",
      os.path.exists("%s.%s.bak" % (_sp, time.strftime("%Y%m%d"))))
# the plug pulled: the file all zeros, the spare from the write before still good
with open(_sp, "rb") as _f:
    _n = len(_f.read())
with open(_sp, "wb") as _f:
    _f.write(bytes(_n))
check("17 zeroed, the server will not write over it", m.read_settings() is not None)
_back = m.pd_safe.recover(_sp)
check("17 and it is put back from a copy that reads", bool(_back) and isinstance(m.read_settings(), dict)
      and len(m.read_settings()) >= len(_was), str(_back))
m.write_settings(_was, merge=False)
print("17. settings and a power cut", flush=True)

# ---- 18. Download and Request each hide their own: a new film the tracker carries is a download
import pd_localapi as _pl
_seen = _pl.LocalAPI.new_one_shown
check("18 on the tracker: shown with Download, whatever Request says",
      _seen(True, {"download": True, "request": False}) and not _seen(True, {"download": False, "request": True}))
check("18 not on the tracker: shown with Request, whatever Download says",
      _seen(False, {"download": False, "request": True}) and not _seen(False, {"download": True, "request": False}))
import pd_streaming as _ps
import pd_tracker as _pt
_pt.use_data_dir(root)                 # the copy's tracker index: what decides "on the tracker"
with _pt.LOCK:
    _pt.STATE["rows"] = {}             # read before the copy was named: read it again
_pt.FOUND["stamp"] = None
_rows = [r for r in _ps.shown() if not r.get("here")]
_tracked = [r for r in _rows if _ps.item(r).get("onTracker")]
if not _tracked:
    print("18. skipped on live data: no new film on the tracker in the list")
else:
    _real = api.films_show
    try:
        api.films_show = lambda: {"disk": True, "download": True, "request": False}
        _shelf = cards("/library/sections/1/all", {"type": ["1"], "sort": ["originallyAvailableAt:desc"]})
        _keys = {str(c.get("ratingKey")) for c in _shelf}
        _askable = [c for c in _shelf if c.get("askable")]
        check("18 with Request hidden, new films the tracker carries are still on the film shelf",
              any(str(r.get("key")) in _keys for r in _tracked)
              # and the ones not asked of the tracker yet, Checking, which may be either
              and all(c.get("onTracker") or c.get("checking") for c in _askable), "%d of %d" % (
                  sum(1 for r in _tracked if str(r.get("key")) in _keys), len(_tracked)))
        api.films_show = lambda: {"disk": True, "download": False, "request": True}
        _shelf = cards("/library/sections/1/all", {"type": ["1"], "sort": ["originallyAvailableAt:desc"]})
        check("18 with Download hidden, none of them are, and only ones to ask for remain",
              not [c for c in _shelf if c.get("askable") and c.get("onTracker")])
    finally:
        api.films_show = _real
print("18. Download and Request each hide their own", flush=True)

# ---- 19. the Popular page, which the TV tab opens on, holds every series here
_pop19 = m.popular_everything()
_keys19 = {str(x.get("ratingKey")) for x in _pop19}
_con19 = api.lib.db()
try:
    _held19 = [str(r["id"]) for r in _con19.execute("SELECT id FROM item WHERE type='show'")]
finally:
    _con19.close()
_missing19 = [k for k in _held19 if k not in _keys19]
check("19 every series in the library is on the Popular page, after the trending ones",
      not _missing19 and all(x.get("popularity", 0) >= 0 for x in _pop19),
      "%d of %d missing" % (len(_missing19), len(_held19)))
print("19. every series here on the Popular page", flush=True)

# ---- 40. the Start tab: every vibe has a picture, and a vibe hands back a feature
#          that fits it, a reason, and more - the same on the same day, another on reroll
_s40 = api.start_page(api.lib.db(), lambda k, d="": {}.get(k, d))
_laugh40 = api.start_page(api.lib.db(), lambda k, d="": {"vibe": "laugh"}.get(k, d))
_again40 = api.start_page(api.lib.db(), lambda k, d="": {"vibe": "laugh"}.get(k, d))
_other40 = api.start_page(api.lib.db(), lambda k, d="": {"vibe": "laugh", "seed": "7"}.get(k, d))
_scare40 = api.start_page(api.lib.db(), lambda k, d="": {"vibe": "scare", "kind": "movie"}.get(k, d))
check("40 Start: vibes with pictures; a vibe's feature fits it, the same today, another on reroll",
      len(_s40.get("vibes") or []) == len(api.VIBES) and all(v.get("art") for v in _s40["vibes"][:4])
      and _laugh40.get("feature") and "Comedy" in (_laugh40["feature"].get("genres") or [])
      and _laugh40["why"].startswith("Make me laugh:") and len(_laugh40["Metadata"]) >= 5
      and _again40["feature"]["ratingKey"] == _laugh40["feature"]["ratingKey"]
      and [x["ratingKey"] for x in _other40["Metadata"]] != [x["ratingKey"] for x in _laugh40["Metadata"]]
      and "Horror" in (_scare40["feature"].get("genres") or []),
      "%s %s" % (len(_s40.get("vibes") or []), (_laugh40.get("feature") or {}).get("genres")))
print("40. the Start tab chooses by vibe", flush=True)

# ---- 41. the Start tab leans to what this viewer watches: their genres weigh in, and
#          the reason says so
_con41 = api.lib.db()
_who41 = api.who
try:
    _rows41 = _con41.execute("SELECT id, genres FROM item WHERE type='movie' AND genres LIKE '%Horror%' "
                             "AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = item.id) LIMIT 6").fetchall()
    api.who = "tasteprobe41"
    api._TASTES.clear()
    for _n41, _r41 in enumerate(_rows41):
        _con41.execute("INSERT INTO watchlog (who, key, title, started, updated, casual) "
                       "VALUES (?, ?, ?, ?, ?, 0)",
                       ("tasteprobe41", _r41["id"], "x", time.time() - 3600 - _n41, time.time() - _n41))
    _con41.commit()
    _taste41 = api.taste(_con41)
    _plain41 = {"genres": {}, "decades": {}, "top": []}
    _p41 = {"genres": {"Horror", "Comedy"}, "year": 1990}
    _q41 = {"genres": {"Romance"}, "year": 1990}
    _why41 = api._start_why({"genres": ["Horror", "Comedy"], "rating": 5.0},
                            api.VIBES[0], _taste41)
finally:
    _con41.execute("DELETE FROM watchlog WHERE who='tasteprobe41'")
    _con41.commit()
    _con41.close()
    api.who = _who41
    api._TASTES.clear()
check("41 taste: what somebody watches weighs in, and the reason says so",
      "Horror" in _taste41["top"][:2]
      and api._taste_bonus(_p41, _taste41) > api._taste_bonus(_q41, _taste41)
      and api._taste_bonus(_p41, _plain41) == 0.0
      and "you watch a lot of horror" in _why41,
      "%s %s" % (_taste41.get("top"), _why41))
print("41. the Start tab leans to what this viewer watches", flush=True)

# ---- 42. told "not this": it is never suggested again, and its kind weighs less; told
#          "more like this": its kind weighs more - for that viewer alone
_con42 = api.lib.db()
_who42 = api.who
_vs42 = m.pd_localapi.viewer_settings if hasattr(m, "pd_localapi") else None
import pd_localapi as _pla42
_real_vs42 = _pla42.viewer_settings
try:
    api.who = "tasteprobe42"
    api._TASTES.clear()
    _laugh42 = api.start_page(_con42, lambda k, d="": {"vibe": "laugh"}.get(k, d))
    _gone42 = _laugh42["feature"]["ratingKey"]
    _pla42.viewer_settings = lambda who: ({"startNot": [_gone42]} if who == "tasteprobe42" else _real_vs42(who))
    api._TASTES.clear()
    _after42 = api.start_page(_con42, lambda k, d="": {"vibe": "laugh"}.get(k, d))
    _keys42 = [_after42["feature"]["ratingKey"]] + [x["ratingKey"] for x in _after42["Metadata"]]
    _someone42 = api.who
    api.who = "anotherviewer42"
    api._TASTES.clear()
    _other42 = api.start_page(_con42, lambda k, d="": {"vibe": "laugh"}.get(k, d))
    # left out as such, not only weighed down: a taste that is nothing but the "not"
    _bare42 = api._start_choose(_con42, api._start_pool(_con42, "movie"), api.VIBES[0], "0", 400,
                                {"genres": {}, "decades": {}, "top": [], "never": {_gone42}})
finally:
    _pla42.viewer_settings = _real_vs42
    api.who = _who42
    api._TASTES.clear()
    _con42.close()
check("42 a title told 'not this' is never suggested again - to that viewer alone",
      _gone42 not in _keys42 and _other42["feature"]["ratingKey"] == _gone42
      and _gone42 not in [x["ratingKey"] for x in _bare42] and len(_bare42) > 20, _gone42)
print("42. told not this, the Start tab leaves it out", flush=True)

# ---- 43. Fresh is this year and last; Classic is everything before
_con43 = api.lib.db()
try:
    _y43 = int(time.strftime("%Y"))
    _fresh43 = api.start_page(_con43, lambda k, d="": {"vibe": "edge", "era": "fresh"}.get(k, d))
    _old43 = api.start_page(_con43, lambda k, d="": {"vibe": "edge", "era": "classic"}.get(k, d))
    _fy43 = [int(x.get("year") or 0) for x in [_fresh43.get("feature") or {}] + _fresh43["Metadata"]]
    _oy43 = [int(x.get("year") or 0) for x in [_old43.get("feature") or {}] + _old43["Metadata"]]
finally:
    _con43.close()
check("43 Fresh holds this year and last, Classic everything before",
      _fy43 and all(y >= _y43 - 1 for y in _fy43) and _oy43 and all(0 < y < _y43 - 1 for y in _oy43),
      "%s %s" % (_fy43[:6], _oy43[:6]))
print("43. Fresh and Classic", flush=True)

# ---- 44. the front's rows: films and series mixed - new ones, each mood for both, the
#          classics - every row's titles of its own kind, each row with enough in it
# the copy reaches no network: IMDb's chart is put beside it, made of films it holds
_con44 = api.lib.db()
try:
    _chart44 = [[r["imdb_id"], r["title"], r["year"]] for r in _con44.execute(
        "SELECT i.imdb_id, i.title, i.year FROM item i WHERE i.type='movie' AND i.imdb_id <> '' "
        "AND i.rating >= 7.5 AND i.genres NOT LIKE '%Family%' AND i.genres NOT LIKE '%Animation%' "
        "AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = i.id) ORDER BY i.id LIMIT 12")]
finally:
    _con44.close()
with open(os.path.join(api.lib.root, "imdb_top.json"), "w", encoding="utf-8") as _f44:
    json.dump({"at": time.time(), "rows": _chart44}, _f44)
_con44 = api.lib.db()
try:
    _rows44 = api.start_page(_con44, lambda k, d="": {"rows": "1"}.get(k, d))["rows"]
finally:
    _con44.close()
# a streaming service's row mixes the two, as the service does; the others are one kind
_svc44 = [r for r in _rows44 if r["id"].startswith("svc-")]
# the music videos' row stands last of all, after the classics and the kids' films
_music44 = [r["id"] for r in _rows44][-1:] == ["music"] or \
    all(r["id"] != "music" for r in _rows44)
_rows44 = [r for r in _rows44 if not r["id"].startswith("svc-") and r["id"] != "music"]
_kinds44 = {r["kind"] for r in _rows44}
check("44 the front's rows: films and series, each row its own kind, new and classic too",
      len(_rows44) >= 10 and _kinds44 == {"movie", "show"}
      and all(len(r["Metadata"]) >= 4 for r in _rows44)
      and all(x.get("type") == r["kind"] for r in _rows44 for x in r["Metadata"])
      and _rows44[0]["id"] == "new-movie"
      and [r["id"] for r in _rows44][-2:] == ["classic-movie", "kids-movie"] and _music44
      # a title once a row: a pack's copy of something here is not set beside it
      and all(len({re.sub(r"[^a-z0-9]+", "", str(x.get("title")).lower()) for x in r["Metadata"]})
              == len(r["Metadata"]) for r in _rows44),
      str([(r["id"], len(r["Metadata"])) for r in _rows44]))
print("44. the Start front mixes films and series rows", flush=True)

# ---- 45. a series page's Shuffle: the viewer's collection of that series, all its
#          episodes where there is none - and a whole series draws as a shelf
_con45 = api.lib.db()
try:
    _show45 = _con45.execute("SELECT id FROM item WHERE type='show' AND EXISTS (SELECT 1 FROM "
                             "episode e JOIN file f ON f.episode_id = e.id WHERE e.item_id = item.id) "
                             "ORDER BY id LIMIT 1").fetchone()["id"]
    _other45 = _con45.execute("SELECT id FROM item WHERE type='show' AND id <> ? ORDER BY id LIMIT 1",
                              (_show45,)).fetchone()["id"]
finally:
    _con45.close()
_h45 = m.Handler.__new__(m.Handler)
_h45.collections = lambda: [{"id": "s45", "name": "A series", "mode": "manual", "series": [_show45],
                             "pinned": [], "hidden": []}]
_mine45 = _h45.series_shuffle_id(_show45)
_none45 = _h45.series_shuffle_id(_other45)
_sh45, _pool45 = _h45.shuffle_shelf("show:" + _show45)
check("45 a series' Shuffle is its collection, or every episode of it",
      _mine45 == "s45" and _none45 == "show:" + _other45 and _sh45 is not None and len(_pool45) > 0,
      "%s %s %d" % (_mine45, _none45, len(_pool45)))
print("45. a series page's Shuffle", flush=True)
_rs45 = m.read_settings
_con45 = api.lib.db()
try:
    _film45 = _con45.execute("SELECT id FROM item WHERE type='movie' ORDER BY id LIMIT 3").fetchall()
finally:
    _con45.close()
m.read_settings = lambda: {"users": {"shuffleprobe45": {"collections": [
    {"id": "s45", "name": "A series", "mode": "manual", "series": [_show45], "pinned": [], "hidden": []},
    {"id": "f45", "name": "Some films", "mode": "manual", "pinned": [r["id"] for r in _film45], "hidden": []}],
    "shuffles": {"show:" + _other45: {"order": ["e1"], "pos": 0},
                 "show:" + _show45: {"order": ["e2"], "pos": 0},
                 "show:000000000000": {"order": ["e3"], "pos": 0}}}}}
try:
    _row45 = m.series_shelves("shuffleprobe45")
finally:
    m.read_settings = _rs45
check("45c a series shuffled from its page is on the row as that round, once",
      [x["shelf"] for x in _row45] == ["s45", "show:" + _other45], str(_row45))
_row45 = _row45[:1]
check("45b the Shuffle row holds the series collections and not the film ones",
      [x["shelf"] for x in _row45] == ["s45"] and _row45[0]["art"] == "/art/%s/backdrop" % _show45,
      str(_row45))
print("45b. the Start tab's Shuffle row", flush=True)

# ---- 46. Start's rows in the viewer's order, the turned-off ones gone, a new row on the end
_vs46 = __import__("pd_localapi").viewer_settings
_st46 = __import__("pd_localapi")._settings
try:
    __import__("pd_localapi").viewer_settings = lambda who: {
        "startRows": ["classic-movie", "shuffle", "new-movie"], "startRowsOff": ["new-show"]}
    __import__("pd_localapi")._settings = lambda: {}
    _k46 = api.start_kept()
    __import__("pd_localapi").viewer_settings = lambda who: {}
    _d46 = api.start_kept()
finally:
    __import__("pd_localapi").viewer_settings = _vs46
    __import__("pd_localapi")._settings = _st46
_all46 = [i for i, _ in api.start_row_names()]
check("46 Start's rows follow the viewer: their order, the hidden left out, the unnamed on the end",
      _k46[:3] == ["classic-movie", "shuffle", "new-movie"] and "new-show" not in _k46
      and set(_k46) == set(_all46) - {"new-show"} - api.start_off_by_default()
      and _d46 == [i for i in _all46 if i not in api.start_off_by_default()], str(_k46))
# by default one row in four is series, a series row after each three of films
_rows46 = [i for i in _d46 if i != "shuffle"]
check("46b Start's default rows: one in four is series, never two side by side",
      sum(1 for i in _rows46 if i.endswith("-show")) * 4 <= len(_rows46) + 3
      and not any(a.endswith("-show") and b.endswith("-show") for a, b in zip(_rows46, _rows46[1:])),
      str(_rows46))
print("46. Start's rows in the viewer's order", flush=True)

# ---- 47. a series' seasons name the series: the app's Add pack asks the tracker with it
_con47 = api.lib.db()
try:
    _show47 = _con47.execute("SELECT id, title FROM item WHERE type='show' AND EXISTS (SELECT 1 FROM "
                             "episode e WHERE e.item_id = item.id) ORDER BY id LIMIT 1").fetchone()
finally:
    _con47.close()
_kids47 = cards("/library/metadata/%s/children" % _show47["id"])
check("47 every season of a series carries the series' name",
      bool(_kids47) and all(k.get("parentTitle") == _show47["title"] for k in _kids47 if k.get("index", 0) >= 0),
      str([(k.get("title"), k.get("parentTitle")) for k in _kids47][:4]))
print("47. seasons name their series", flush=True)

# ---- 48. Start shows its best: nothing under the floor, and no bad-on-purpose row
_con48 = api.lib.db()
try:
    _rows48 = api._start_rows(_con48, {}, "0")
finally:
    _con48.close()
# music videos carry no rating: their row is everything in the folder, not a choice
_low48 = [(r["id"], m.get("title"), m.get("rating")) for r in _rows48 for m in r["Metadata"]
          if r["id"] != "music"
          and float(m.get("rating") or 0) < (api.START_FLOOR_NEW if r["id"].startswith("new-")
                                             else api.START_FLOOR)]
_con48 = api.lib.db()
try:
    _low_pool48 = [p for p in api._start_pool(_con48, "movie") if 0 < p["rating"] < 7.0][:3]
    _picked48 = api._start_choose(_con48, _low_pool48, api.ANY_VIBE, "0", 5, {})
finally:
    _con48.close()
check("48 a title under the floor is never chosen, even when nothing else is there",
      len(_low_pool48) == 3 and _picked48 == [], str([c.get("title") for c in _picked48]))
# a pack entry matched to the wrong film has another name, and the key of the film held here
import pd_torrents as _pt48
_con48 = api.lib.db()
_was48 = _pt48.offered
try:
    _pt48.offered = lambda: []
    _key48 = next(p["key"] for p in api._start_pool(_con48, "movie") if p["here"])
    _pt48.offered = lambda: [{"ratingKey": _key48, "title": "A Wrong Match", "rating": 9.9}]
    _dup48 = [p for p in api._start_pool(_con48, "movie") if p["key"] == _key48]
finally:
    _pt48.offered = _was48
    _con48.close()
check("48 a pack entry with the key of a film held here is not a second card, whatever it is called",
      len(_dup48) == 1 and _dup48[0]["here"], str(_dup48))
check("48 Start's rows hold nothing rated under the floor, and no trash row",
      bool(_rows48) and not _low48 and not any(r["id"].startswith("trash") for r in _rows48),
      str(_low48[:5]))
print("48. Start shows its best", flush=True)

# ---- 49. Popular: by TMDB's score where known, then rating, then name - never Z first
_tp49 = api.tmdb_popularity
_con49 = api.lib.db()
try:
    _films49 = [dict(r) for r in _con49.execute(
        "SELECT id, title, sort_title, rating, tmdb_id FROM item WHERE type='movie' AND tmdb_id "
        "ORDER BY sort_title LIMIT 4")]
finally:
    _con49.close()
_cards49 = [{"ratingKey": f["id"], "title": f["title"], "titleSort": f["sort_title"],
             "rating": r} for f, r in zip(_films49, (5.0, 9.0, 6.0, 7.0))]
_cards49 += [{"ratingKey": "zz49", "title": "Zzz", "rating": 8.0, "tmdb": 0},
             {"ratingKey": "aa49", "title": "Aaa", "rating": 8.0, "tmdb": 0}]
# a pack's copy of the second, under a key of its own and with no number: scored by name
_cards49 += [{"ratingKey": "os49", "title": _films49[1]["title"], "rating": 1.0}]
try:
    # the first two scored, the others not yet
    api.tmdb_popularity = lambda kind, numbers: {int(_films49[0]["tmdb_id"]): 50.0,
                                                 int(_films49[1]["tmdb_id"]): 10.0}
    _out49 = [c["ratingKey"] for c in api._by_popularity(_cards49, "movie", True)]
finally:
    api.tmdb_popularity = _tp49
check("49 Popular: scored first by score - a pack's copy by its name - then rating, then name",
      _out49[:3] == [_films49[0]["id"], _films49[1]["id"], "os49"]
      and _out49[3:] == ["aa49", "zz49", _films49[3]["id"], _films49[2]["id"]], str(_out49))
print("49. Popular by TMDB's score", flush=True)

# ---- 50. Shuffle carries on what was watched last; a collection is one round
_m50 = {"shuffles": {"c50": {"order": ["a"], "pos": 0, "at": {"a": {"at": 100, "when": 10}}},
                     "coll:c50": {"order": ["b"], "pos": 0, "at": {"b": {"at": 200, "when": 20}}}}}
m.Handler.fold_round(_m50, "c50")
_n50 = {"shuffles": {"coll:d50": {"order": ["b"], "pos": 0}}}
m.Handler.fold_round(_n50, "d50")
check("50 a collection's two rounds fold into one: the collection's own kept, with the newer place",
      list(_m50["shuffles"]) == ["c50"] and _m50["shuffles"]["c50"]["order"] == ["a"]
      and _m50["shuffles"]["c50"]["at"] == {"b": {"at": 200, "when": 20}}
      and list(_n50["shuffles"]) == ["d50"], str(_m50) + str(_n50))
# an older place under "coll:" does not replace the round's own
_o50 = {"shuffles": {"c51": {"order": ["a"], "pos": 0, "at": {"a": {"at": 100, "when": 30}}},
                     "coll:c51": {"order": [], "pos": -1, "at": {"b": {"at": 200, "when": 20}}}}}
m.Handler.fold_round(_o50, "c51")
# and "coll:<id>" is that round, wherever it is asked for
_h50r = m.Handler.__new__(m.Handler)
_p50 = {"shuffles": {"c52": {"order": ["a"], "pos": 0, "at": {}}}}
check("50b one round per collection: \"coll:<id>\" is the round itself, an older place stays out",
      _o50["shuffles"]["c51"]["at"] == {"a": {"at": 100, "when": 30}}
      and m.Handler.round_id("coll:c52") == "c52"
      and _h50r.shuffle_round(_p50, "coll:c52") is _p50["shuffles"]["c52"]
      and list(_p50["shuffles"]) == ["c52"], str(_o50) + str(_p50))
_con50 = api.lib.db()
try:
    _ep50 = _con50.execute("SELECT e.id, e.item_id FROM episode e JOIN file f ON f.episode_id = e.id "
                           "ORDER BY e.id LIMIT 2").fetchall()
    _who50 = "probe50"
    _con50.execute("DELETE FROM progress WHERE who=?", (_who50,))
    _con50.execute("INSERT INTO progress (who, key, position, duration, updated) VALUES (?,?,?,?,?)",
                   (_who50, str(_ep50[0]["id"]), 600, 1300, 5000))
    _con50.commit()
finally:
    _con50.close()
_h50 = m.Handler.__new__(m.Handler)
_h50.viewer = lambda: _who50
_inside50 = {str(_ep50[0]["id"]), str(_ep50[1]["id"])}
_newer50 = _h50.newest_watched(_inside50, {str(_ep50[1]["id"]): {"at": 50, "when": 4000}})
_older50 = _h50.newest_watched(_inside50, {str(_ep50[1]["id"]): {"at": 50, "when": 6000}})
_con50 = api.lib.db()
try:
    _con50.execute("UPDATE progress SET position=1290 WHERE who=?", (_who50,))
    _con50.commit()
finally:
    _con50.close()
_done50 = _h50.newest_watched(_inside50, {str(_ep50[1]["id"]): {"at": 50, "when": 4000}})
check("50 carrying on takes the episode watched since the round's place, where it was left",
      _newer50 == (str(_ep50[0]["id"]), 600, False) and _older50 is None
      # watched into its last minute: still that one, said to be over, so the round goes on
      and _done50 == (str(_ep50[0]["id"]), 1290, True),
      "%s %s %s" % (_newer50, _older50, _done50))
print("50. Shuffle carries on what was watched last", flush=True)

# ---- 51. children's films: a row of their own and in no other
_con51 = api.lib.db()
try:
    _rows51 = api._start_rows(_con51, {}, "0")
finally:
    _con51.close()
_kid51 = [r for r in _rows51 if r["id"] == "kids-movie"]
_stray51 = [(r["id"], m.get("title")) for r in _rows51 if r["id"] != "kids-movie" and r.get("kind") == "movie"
            for m in r["Metadata"] if api.for_kids(m.get("genres") or [])]
check("51 children's films stand in their own row and in no other",
      bool(_kid51) and all(api.for_kids(m.get("genres") or []) for m in _kid51[0]["Metadata"])
      and not _stray51 and api.for_kids(["Animation", "Comedy"])
      and not api.for_kids(["Animation", "Crime"]) and not api.for_kids(["Comedy"]),
      str(_stray51[:5]))
print("51. a row for the kids", flush=True)

# ---- 52. New films: out this last month, the most popular first
_tp52 = api.tmdb_popularity
_today52 = time.strftime("%Y-%m-%d")
_old52 = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
_con52 = api.lib.db()
try:
    _f52 = [dict(r) for r in _con52.execute(
        "SELECT i.id, i.tmdb_id FROM item i WHERE i.type='movie' AND i.tmdb_id AND EXISTS "
        "(SELECT 1 FROM file f WHERE f.item_id = i.id) ORDER BY i.id LIMIT 3")]
    _pool52 = [{"key": _f52[0]["id"], "tmdb": _f52[0]["tmdb_id"], "released": _today52, "rating": 9.0},
               {"key": _f52[1]["id"], "tmdb": _f52[1]["tmdb_id"], "released": _today52, "rating": 7.0},
               {"key": _f52[2]["id"], "tmdb": _f52[2]["tmdb_id"], "released": _old52, "rating": 9.9}]
    api.tmdb_popularity = lambda kind, numbers: {int(_f52[0]["tmdb_id"]): 10.0,
                                                 int(_f52[1]["tmdb_id"]): 90.0}
    _got52 = [c["ratingKey"] for c in api._start_newest(_con52, _pool52, {}, 10)]
finally:
    api.tmdb_popularity = _tp52
    _con52.close()
check("52 New films: only the last month's, the most popular first, the rating aside",
      _got52 == [_f52[1]["id"], _f52[0]["id"]], str(_got52))
print("52. New films by popularity", flush=True)

# ---- 53. Classics: IMDb's top 100 held or in a pack, in the chart's order
_it53 = api.imdb_top
_con53 = api.lib.db()
try:
    _f53 = [dict(r) for r in _con53.execute(
        "SELECT i.id, i.title, i.year, i.imdb_id FROM item i WHERE i.type='movie' AND i.imdb_id <> '' "
        "AND EXISTS (SELECT 1 FROM file f WHERE f.item_id = i.id) ORDER BY i.id LIMIT 3")]
    # the third by name and year only, as a pack's copy would be found
    api.imdb_top = lambda: [[_f53[1]["imdb_id"], "x", 0], ["tt0000000", _f53[2]["title"], _f53[2]["year"]],
                            [_f53[0]["imdb_id"], "y", 0]]
    _pool53 = [{"key": f["id"], "imdb": f["imdb_id"] if i < 2 else "", "title": f["title"],
                "year": f["year"]} for i, f in enumerate(_f53)]
    _pool53.append({"key": "notontop53", "imdb": "tt9999999", "title": "Nope", "year": 1})
    _got53 = [c["ratingKey"] for c in api._start_classics(_con53, _pool53, {}, 10)]
finally:
    api.imdb_top = _it53
    _con53.close()
check("53 Classics: the top 100's titles here, by number or by name and year, in the chart's order",
      _got53 == [_f53[1]["id"], _f53[2]["id"], _f53[0]["id"]], str(_got53))
print("53. Classics from IMDb's top 100", flush=True)

# ---- 54. the Service menu counts each choice's titles
_sp54 = __import__("pd_streaming").service_page
try:
    __import__("pd_streaming").service_page = lambda lib, kind, provs, region, page, **kw: ([], 3, 1234)
    _con54 = api.lib.db()
    try:
        _c54 = api.service_counts(_con54, "movie", "SE", [8])
        _held54 = _con54.execute("SELECT COUNT(*) FROM item i WHERE i.type='movie' AND EXISTS "
                                 "(SELECT 1 FROM file f WHERE f.item_id = i.id)").fetchone()[0]
    finally:
        _con54.close()
finally:
    __import__("pd_streaming").service_page = _sp54
check("54 the Service menu: a service's count, My library's and In packs'",
      _c54.get(8) == 1234 and _c54.get(-1) == _held54 and -2 in _c54, str(_c54))
print("54. the Service menu's counts", flush=True)

# ---- 55. a row a streaming service: its films and series in turn, rated, no children's
_sc55 = api.service_catalogue
def _fake55(con, one):
    if one("type") == "movie":
        return {"Metadata": [{"ratingKey": "m1", "rating": 8.0, "genres": ["Drama"]},
                             {"ratingKey": "m2", "rating": 5.0, "genres": ["Drama"]},
                             {"ratingKey": "m3", "rating": 8.0, "genres": ["Family"]},
                             {"ratingKey": "m4", "rating": 7.0, "genres": ["Action"]}]}
    return {"Metadata": [{"ratingKey": "s1", "rating": 8.5, "genres": ["Drama"]},
                         {"ratingKey": "s2", "rating": 7.5, "genres": ["Comedy"]}]}
try:
    api.service_catalogue = _fake55
    _con55 = api.lib.db()
    try:
        api._start_service_kept.clear()     # the kept lists are the real ones
        _row55 = [c["ratingKey"] for c in api._start_service(_con55, 8, {})]
    finally:
        _con55.close()
finally:
    api.service_catalogue = _sc55
check("55 a service's row: three films to a series, the low-rated and children's left out",
      _row55 == ["m1", "m4", "s1", "s2"], str(_row55))
print("55. a row a streaming service", flush=True)

# ---- 39. a pack taken off a series loses its link unless a download is still coming from
#          it or another series chose it; a finished download no longer holds it; the
#          torrent is never touched
_saved39 = (pd_torrents.remove_pack, pd_torrents.drop_show_pack, pd_torrents.load)
_gone39 = []
_data39 = {"config": {"showPacks": {"othershow": {"packs": ["hOther"], "season": {}}}},
           "packs": [], "downloads": [{"hash": "hUsed", "state": "downloading"},
                                      {"hash": "hDone", "state": "done"}]}
pd_torrents.remove_pack = lambda h: _gone39.append(h) or {"ok": True}
pd_torrents.drop_show_pack = lambda name, h: None
pd_torrents.load = lambda: _data39
try:
    _free39 = m.use_show_pack("Invented Gold Series", info_hash="hFree", drop=True, unlink=True)
    _used39 = m.use_show_pack("Invented Gold Series", info_hash="hUsed", drop=True, unlink=True)
    _done39 = m.use_show_pack("Invented Gold Series", info_hash="hDone", drop=True, unlink=True)
    _other39 = m.use_show_pack("Invented Gold Series", info_hash="hOther", drop=True, unlink=True)
    _drop39 = m.use_show_pack("Invented Gold Series", info_hash="hPlain", drop=True)
finally:
    pd_torrents.remove_pack, pd_torrents.drop_show_pack, pd_torrents.load = _saved39
check("39 removing a pack drops a free link only",
      _gone39 == ["hFree", "hDone"] and not _free39.get("kept") and _used39.get("kept")
      and not _done39.get("kept")
      and _other39.get("kept") and _drop39 == {"ok": True}, "%s %s %s" % (_gone39, _used39, _other39))
print("39. a pack is removed only when nothing holds on to it", flush=True)

# ---- 39b. background work asks for the other server as the owner on this machine
try:
    _sb39 = m.Handler.in_house().standby_now()
    _sb39ok = isinstance(_sb39, dict) and "where" in _sb39
except Exception as _e39:
    _sb39, _sb39ok = repr(_e39), False
check("39b the standby address read from background work, with no request behind it", _sb39ok, str(_sb39)[:200])
_bare39 = [ln for ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8")
           if "Handler.__new__(Handler).standby_now" in ln]
check("39b nothing asks a bare request for the standby address", not _bare39, str(_bare39))
print("39b. background work finds the other server", flush=True)

# ---- 38. a pack added from a season's page becomes that season's source; a season
#          choice alone still only sets the source
import pd_tracker as _pt38
_calls38 = []
_saved38 = (_pt38.torrent_of, pd_torrents.add_pack, pd_torrents.add_show_pack,
            pd_torrents.set_season_pack)
_pt38.torrent_of = lambda ident: b"raw"
pd_torrents.add_pack = lambda raw=None, path=None, series="": {"ok": True, "hash": "h38"}
pd_torrents.add_show_pack = lambda name, h: _calls38.append(("chosen", name, h))
pd_torrents.set_season_pack = lambda name, season, h: _calls38.append(("season", name, season, h))
try:
    _r38 = m.use_show_pack("Invented Gold Series", "123", season=4)
    _r38b = m.use_show_pack("Invented Gold Series", info_hash="h0", season=5)
finally:
    (_pt38.torrent_of, pd_torrents.add_pack, pd_torrents.add_show_pack,
     pd_torrents.set_season_pack) = _saved38
check("38 a pack added from a season's page is chosen and that season's source",
      _r38.get("ok") and _calls38 == [("chosen", "Invented Gold Series", "h38"),
                                      ("season", "Invented Gold Series", 4, "h38"),
                                      ("season", "Invented Gold Series", 5, "h0")], str(_calls38))
print("38. a season's pack is its source", flush=True)

# ---- 37. a season with nothing linked, put on a collection, shows as that season
_con37 = api.lib.db()
_rt37 = api.route
try:
    _show37 = str(_con37.execute("SELECT id FROM item WHERE type='show' ORDER BY id LIMIT 1").fetchone()["id"])
    api.route = lambda con, path, q: ({"Metadata": [{"ratingKey": "%s-s97" % _show37, "type": "season",
                                                     "index": 97, "title": "Season 97", "upcoming": True}]}
                                      if path == "/library/metadata/%s/children" % _show37
                                      else _rt37(con, path, q))
    _c37 = api._by_season(_con37, ["%s-s97" % _show37])
finally:
    api.route = _rt37
    _con37.close()
check("37 a season with nothing linked shows as its season card",
      len(_c37) == 1 and _c37[0].get("type") == "season" and _c37[0].get("index") == 97
      and _c37[0].get("grandparentRatingKey") == _show37, str(_c37))
print("37. an unlinked season on a collection shows", flush=True)

# ---- 36. a filter collection whose words catch a series holds it whole on its page;
#          a season span does not
_con36 = api.lib.db()
try:
    _row36 = _con36.execute("SELECT i.id, i.title FROM item i JOIN episode e ON e.item_id = i.id "
                            "WHERE i.type='show' GROUP BY i.id HAVING COUNT(*) >= 3 "
                            "ORDER BY i.id LIMIT 1").fetchone()
    _h36 = m.Handler.__new__(m.Handler)
    _w36 = _h36.whole_series(_con36, {"id": "t36", "mode": "filter", "pinned": [], "hidden": [],
                                      "rule": {"words": [str(_row36["title"])], "type": "show"}})
    _s36 = _h36.whole_series(_con36, {"id": "t36", "mode": "filter", "pinned": [], "hidden": [],
                                      "rule": {"words": [str(_row36["title"])], "type": "show",
                                               "season": "s1"}})
finally:
    _con36.close()
check("36 a series a filter catches whole shows every season; a span keeps to its seasons",
      str(_row36["id"]) in _w36 and str(_row36["id"]) not in _s36, "%s %s" % (_w36[:3], _s36[:3]))
print("36. a filter's whole series shows every season", flush=True)

# ---- 35. a collection holding a whole series shows every season the series page lists
_con35 = api.lib.db()
_rt35 = api.route
try:
    _sh35 = _con35.execute("SELECT item_id, season FROM episode GROUP BY item_id, season "
                           "HAVING COUNT(*) >= 2 ORDER BY item_id LIMIT 1").fetchone()
    _show35, _n35 = str(_sh35["item_id"]), int(_sh35["season"])
    _ep35 = [str(r["id"]) for r in _con35.execute(
        "SELECT id FROM episode WHERE item_id=? AND season=? ORDER BY number LIMIT 2",
        (_show35, _n35))]
    _fake35 = [{"ratingKey": "%s-s%d" % (_show35, n), "type": "season", "index": n,
                "title": "Season %d" % n, "upcoming": True} for n in (_n35 + 2, _n35 + 1)]
    api.route = lambda con, path, q: ({"Metadata": _fake35} if path ==
                                      "/library/metadata/%s/children" % _show35 else _rt35(con, path, q))
    _all35 = api._by_season(_con35, _ep35, {_show35})
    _part35 = api._by_season(_con35, _ep35)
finally:
    api.route = _rt35
    _con35.close()
check("35 a whole series on a collection lists its seasons with nothing here too, in order",
      [c.get("index") for c in _all35] == [_n35, _n35 + 1, _n35 + 2]
      and all(c.get("grandparentRatingKey") == _show35 for c in _all35)
      and [c.get("index") for c in _part35] == [_n35],
      str([(c.get("ratingKey"), c.get("index")) for c in _all35]))
print("35. a whole series shows all its seasons", flush=True)

# ---- 33. a season the tracker had nothing for waits, and the next programme gets the search
_ls33, _sg33, _fs33 = m.library_shows, m.season_gaps, m.fill_season
_asked33 = []
m.library_shows = lambda stored=None: [{"show": "Nopack One", "tmdb": 1}, {"show": "Nopack Two", "tmdb": 2},
                                       {"show": "Packed Three", "tmdb": 3}]
m.season_gaps = lambda title, tmdb=0: {"show": title, "missing": [1, 2]}
m.fill_season = lambda title, season: (_asked33.append((title, season))
                                       or ({"error": "nothing"} if title.startswith("Nopack") else {"ok": True}))
m.EMPTY_SEASONS.clear()
try:
    m.stock_shows()
    m.stock_shows()
    m.stock_shows()
finally:
    m.library_shows, m.season_gaps, m.fill_season = _ls33, _sg33, _fs33
    m.EMPTY_SEASONS.clear()
check("33 an empty season waits a day; the next season and programme get the searches",
      _asked33 == [("Nopack One", 1), ("Nopack Two", 1), ("Nopack One", 2), ("Nopack Two", 2),
                   ("Packed Three", 1)], str(_asked33))
print("33. a season with no pack does not hold up the rest", flush=True)

# ---- 32. a pack's key for an episode downloaded since is in the shuffle's hat once
_con32 = api.lib.db()
_md32 = api.metadata_for
try:
    _ep32 = [str(r["id"]) for r in _con32.execute(
        "SELECT e.id FROM episode e JOIN file f ON f.episode_id = e.id "
        "GROUP BY e.id ORDER BY e.id LIMIT 2")]
    _card32 = _md32(_con32, _ep32[0], brief=True)
    api.metadata_for = lambda con, key, brief=False, **kw: (
        dict(_card32) if key == "ozz32arrived" else _md32(con, key, brief=brief, **kw))
    _h32 = m.Handler.__new__(m.Handler)
    _pool32 = _h32.shelf_pool(_con32, {"id": "t32", "name": "t32", "mode": "manual", "rule": {},
                                       "pinned": [_ep32[0], "ozz32arrived", _ep32[1]], "hidden": []})
finally:
    api.metadata_for = _md32
    _con32.close()
check("32 an arrived pack key and its episode are one draw",
      _pool32 == [_ep32[0], _ep32[1]], str(_pool32))
print("32. an arrived episode is in the hat once", flush=True)

# ---- 31. a pack's key for an episode downloaded since folds into its season; one
#          episode of a series held whole is a season card
_con31 = api.lib.db()
_md31 = api.metadata_for
try:
    _sh31 = _con31.execute("SELECT item_id, season FROM episode WHERE item_id IN "
                           "(SELECT item_id FROM episode GROUP BY item_id "
                           "HAVING COUNT(DISTINCT season) >= 2) GROUP BY item_id, season "
                           "HAVING COUNT(*) >= 3 ORDER BY item_id LIMIT 1").fetchone()
    _ep31 = [str(r["id"]) for r in _con31.execute(
        "SELECT id FROM episode WHERE item_id=? AND season=? ORDER BY number LIMIT 3",
        (_sh31["item_id"], _sh31["season"]))]
    _arrived31 = _md31(_con31, _ep31[2], brief=True)
    api.metadata_for = lambda con, key, brief=False, **kw: (
        dict(_arrived31) if key == "ozz31arrived" else _md31(con, key, brief=brief, **kw))
    _c31 = api._by_season(_con31, [_ep31[0], _ep31[1], "ozz31arrived"])
    # two seasons, one episode each: both seasons, which a loop variable named like
    # the argument once broke after the first
    _two31 = _con31.execute("SELECT MIN(id) id FROM episode WHERE item_id=? AND season<>? "
                            "GROUP BY season LIMIT 1", (_sh31["item_id"], _sh31["season"])).fetchone()
    _one31 = api._by_season(_con31, [_ep31[0]] + ([_two31["id"]] if _two31 else []),
                            {str(_sh31["item_id"])})
    _lone31 = api._by_season(_con31, [_ep31[0]])
finally:
    api.metadata_for = _md31
    _con31.close()
check("31 an arrived pack key joins its season, and a lone episode of a whole series is its season",
      len(_c31) == 1 and _c31[0].get("type") == "season" and len(_c31[0].get("holds") or []) == 3
      and _two31 is not None and len(_one31) >= 2 and all(c.get("type") == "season" for c in _one31)
      and len([c for c in _one31 if c.get("holds")]) == 2
      and len(_lone31) == 1 and _lone31[0].get("type") == "episode",
      "%s %s" % ([(c.get("type"), c.get("ratingKey")) for c in _c31], [c.get("type") for c in _one31]))
print("31. a collection reads as seasons", flush=True)

# ---- 30. a hand-made collection given a whole series keeps what the series gains
_con30 = api.lib.db()
try:
    _show30 = _con30.execute("SELECT i.id FROM item i JOIN episode e ON e.item_id = i.id "
                             "WHERE i.type='show' GROUP BY i.id HAVING COUNT(*) >= 3 "
                             "ORDER BY i.id LIMIT 1").fetchone()
    _eps30 = m.series_episode_keys(_con30, _show30["id"]) if _show30 else []
    _shelf30 = {"id": "t30", "name": "t30", "mode": "manual", "rule": {},
                "pinned": [_eps30[0]] if _eps30 else [], "hidden": [], "series": [_show30["id"]] if _show30 else []}
    _all30 = m.Handler.collection_keys(_con30, _shelf30)
    _shelf30["hidden"] = [_eps30[1]] if len(_eps30) > 1 else []
    _less30 = m.Handler.collection_keys(_con30, _shelf30)
finally:
    _con30.close()
check("30 a series on a hand-made collection is every episode it has now, less one taken off",
      bool(_eps30) and set(_all30) == set(_eps30) and len(_all30) == len(_eps30)
      and _eps30[1] not in _less30 and len(_less30) == len(_eps30) - 1)
print("30. a series on a hand-made collection keeps what it gains", flush=True)

# ---- 29. a season's packs are the series' own, not a spin-off's that starts with its name
import pd_tracker as _ptr29
_rd29, _sn29 = _ptr29.read, m.series_names
_ptr29.read = lambda: {"1": {"id": "1", "name": "Placer Run (2010) S02 1080p WEB x265-AA", "seeds": 9},
                       "2": {"id": "2", "name": "Placer Run S01-S05 720p WEB-BB", "seeds": 3},
                       "3": {"id": "3", "name": "Placer Run Far South S02 1080p WEB-CC", "seeds": 30},
                       "4": {"id": "4", "name": "Placer Run White Water S01-S05 1080p WEB-DD", "seeds": 40},
                       "5": {"id": "5", "name": "Placer Run Klondike S02 1080p WEB-EE", "seeds": 5}}
m.series_names = lambda t: ["Placer Run Klondike"]
try:
    _o29 = m.season_offers("Placer Run", 2, ask=False)
finally:
    _ptr29.read, m.series_names = _rd29, _sn29
_n29 = sorted(o["id"] for o in _o29["packs"] + _o29["loose"])
check("29 a season's packs are the series' own and its other names', never a spin-off's",
      _n29 == ["1", "2", "5"], str(_n29))
print("29. no spin-off pack stands for a series' season", flush=True)

# ---- 28. a whole name the catalogue knows, credited nowhere here, finds the person;
_tm28 = api.lib.tmdb
_pf28 = api._person_films
api.lib.tmdb = lambda path, **kw: ({"results": [{"id": 999991, "name": "Ada Madeup Quill",
                                                 "profile_path": "/a.jpg"}]}
                                   if path == "/search/person" else {})
api._person_films = lambda person, name: [{"ratingKey": "tv999992", "type": "show",
                                           "title": "Invented Gold Series", "addable": True}]
api._PEOPLE_ASKED.clear()
try:
    _s28, _, _b28 = api.handle("/hubs/search", {"query": ["Ada Madeup Quill"]})
    _h28 = json.loads(_b28)["MediaContainer"].get("Hub", []) if _s28 == 200 else []
finally:
    api.lib.tmdb = _tm28
    api._person_films = _pf28
    api._PEOPLE_ASKED.clear()
_who28 = [x.get("tag") for h in _h28 if h.get("type") == "person" for x in h.get("Metadata") or []]
_tv28 = [x.get("title") for h in _h28 if h.get("type") == "show" for x in h.get("Metadata") or []]
check("28 a whole name the catalogue knows finds the person and their series",
      "Ada Madeup Quill" in _who28 and "Invented Gold Series" in _tv28, "%s %s" % (_who28, _tv28))
print("28. a person known only to the catalogue is found by name", flush=True)

# ---- 27. a film not matched yet wears the poster its card had before it was downloaded
import pd_streaming as _ps27
_was27 = _ps27.SEARCHED["rows"]
_ps27.SEARCHED["rows"] = [{"key": "rt0000test27", "title": "Made Up Film Twenty Seven", "year": 1999,
                           "poster": "/x.jpg"}]
try:
    _t27 = api._poster_before_match("Made Up Film: Twenty-Seven", 1999)
    _n27 = api._poster_before_match("Made Up Film Twenty Seven", 2010)
finally:
    _ps27.SEARCHED["rows"] = _was27
check("27 a film not matched yet takes the poster its card had; another year does not",
      _t27 == "/art/rt0000test27/poster" and _n27 is None)
print("27. a just-downloaded film keeps its card's poster", flush=True)

# ---- 26. a name typed in full finds the person, credited only on films not here yet
_con26 = api.lib.db()
try:
    _who26 = _con26.execute(
        """SELECT MIN(name) AS name FROM credit WHERE name <> ''
            GROUP BY LOWER(name)
           HAVING SUM(item_id IN (SELECT id FROM item)) = 0
            ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()
finally:
    _con26.close()
# their filmography is asked of the catalogue, which this copy cannot reach: the row of
# faces is what is checked here, the films on the live server
_pf26 = api._person_films
api._person_films = lambda person, name: []
try:
    _st26, _, _b26 = (api.handle("/hubs/search", {"query": [_who26["name"]]}) if _who26
                      else (0, "", b"{}"))
finally:
    api._person_films = _pf26
_hubs26 = json.loads(_b26).get("MediaContainer", {}).get("Hub", []) if _st26 == 200 else []
check("26 a full name of somebody credited only on films not here finds them as a person",
      bool(_who26) and any(h.get("type") == "person" and any(
          str(x.get("tag") or "").lower() == str(_who26["name"]).lower()
          for x in h.get("Metadata") or []) for h in _hubs26))
print("26. a person credited only on films not here is found by name", flush=True)

# ---- 25. every film a pack carries is on the films' Popular page, trending or not
_con25 = api.lib.db()
try:
    _keys25 = {str(x.get("ratingKey")) for x in api.popular_films(_con25)}
finally:
    _con25.close()
_offered25 = {str(o.get("ratingKey")) for o in api._offered_quietly()}
check("25 every film on a pack is on the films' Popular page when downloads are shown",
      not api.films_show().get("download") or _offered25 <= _keys25,
      "%d of %d pack films missing" % (len(_offered25 - _keys25), len(_offered25)))
print("25. pack films on the Popular page: %d" % len(_offered25 & _keys25), flush=True)

# ---- 24. a series held here carries its rating on its card, as a film does
_con24 = api.lib.db()
try:
    _r24 = _con24.execute("SELECT id, rating FROM item WHERE type='show' AND rating > 0 "
                          "ORDER BY id LIMIT 1").fetchone()
    _c24 = api.metadata_for(_con24, str(_r24["id"]), brief=True) if _r24 else None
finally:
    _con24.close()
check("24 a held series' card carries its rating, so Rating sorts it",
      bool(_c24) and float(_c24.get("rating") or 0) == float(_r24["rating"]))
print("24. a held series carries its rating", flush=True)

# ---- 23. the front page's Popular films row: the popular films, forty at most
_con23 = api.lib.db()
try:
    _row23 = api.popular_films(_con23, as_row=True)
finally:
    _con23.close()
_rk23 = [int(x.get("popularity") or 0) for x in _row23]
check("23 the Popular films row holds the popular films, most watched first, forty at most",
      0 < len(_row23) <= 40 and all(n > 0 for n in _rk23) and _rk23 == sorted(_rk23, reverse=True))
print("23. Popular films row: %d films" % len(_row23), flush=True)

# ---- 22. the Service menu's My library: alone the library, with a service its titles there
_st22, _, _b22 = api.handle("/library/services", {})
_menu22 = json.loads(_b22)["MediaContainer"]["Directory"] if _st22 == 200 else []
_con22 = api.lib.db()
try:
    _films22 = {str(r["id"]): int(r["tmdb_id"] or 0) for r in _con22.execute(
        "SELECT id, tmdb_id FROM item WHERE type='movie'")}
finally:
    _con22.close()
_sv22 = api.services_of
# every film with an even catalogue number streams on service 8, the rest nowhere
api.services_of = lambda kind, numbers: {n: ([8] if n % 2 == 0 else []) for n in numbers if n}
try:
    def _ask22(prov, sort="titleSort:asc"):
        st, _, body = api.handle("/library/service", {"type": ["movie"], "provider": [prov],
                                                      "sort": [sort]})
        return [str(x.get("ratingKey")) for x in
                json.loads(body)["MediaContainer"]["Metadata"]] if st == 200 else None
    _alone22 = _ask22("-1")
    _on8 = _ask22("-1,8")
finally:
    api.services_of = _sv22
check("22 the Service menu starts with My library",
      bool(_menu22) and _menu22[0].get("id") == -1 and _menu22[0].get("name") == "My library")
check("22 My library alone is every film here, with a service only the ones it carries",
      _alone22 is not None and set(_alone22) == set(_films22)
      and _on8 is not None
      and set(_on8) == {k for k, n in _films22.items() if n and n % 2 == 0},
      "%s alone, %s on 8" % (len(_alone22 or []), len(_on8 or [])))
print("22. My library in the Service menu: %d films, %d on the made-up service"
      % (len(_alone22 or []), len(_on8 or [])), flush=True)

# ---- 21. a search with an "and" the title does not carry still finds it
_con21 = api.lib.db()
try:
    _two21 = next((str(r["title"]) for r in _con21.execute(
        "SELECT title FROM item WHERE type='movie' ORDER BY id")
        if re.fullmatch(r"[A-Za-z]{4,} [A-Za-z]{4,}", str(r["title"] or ""))
        and "and" not in str(r["title"]).lower().split()), "")
finally:
    _con21.close()
_w21 = _two21.split()
_st21, _, _b21 = api.handle("/hubs/search", {"query": ["%s and %s" % (_w21[0], _w21[1])]})     if _w21 else (0, "", b"{}")
_hit21 = [mm.get("title") for h in json.loads(_b21).get("MediaContainer", {}).get("Hub", [])
          for mm in h.get("Metadata", [])] if _w21 else []
check("21 a search with an \"and\" the title lacks still finds the title",
      bool(_w21) and _two21 in _hit21, "%r -> %d hits" % (_two21, len(_hit21)))
print("21. \"and\" dropped from a search that found nothing", flush=True)

# ---- 20. the Films tab's Popular page: the popular ones first, then every film here
import pd_streaming as _ps20
_ps20.ask_tracker_about = lambda *a, **k: None
_ps20.ask_tracker_slowly = lambda *a, **k: None
# TMDB's score stood in for by the catalogue number, so the order can be checked
_tp20 = api.tmdb_popularity
api.tmdb_popularity = lambda kind, numbers: {n: float(n) for n in numbers if n}
_con20 = api.lib.db()
try:
    _pop20 = api.popular_films(_con20)
    _held20 = [str(r["id"]) for r in _con20.execute("SELECT id FROM item WHERE type='movie'")]
finally:
    _con20.close()
_keys20 = [str(x.get("ratingKey")) for x in _pop20]
_ranks20 = [int(x.get("popularity") or 0) for x in _pop20]
_r20 = api.rank_popular([{"ratingKey": k} for k in "abcd"], 1, "movie",
                         {"b": 5, "c": 9, "d": 0})
api.tmdb_popularity = _tp20
check("20b the titles not trending follow most popular on TMDB first, ranks whole numbers",
      [x["ratingKey"] for x in _r20] == ["a", "c", "b", "d"]
      and [x["popularity"] for x in _r20] == [4, 3, 2, 1])
check("20 every film in the library is on the films' Popular page, the popular ones first",
      not [k for k in _held20 if k not in set(_keys20)]
      and len(_keys20) == len(set(_keys20))
      and _ranks20 == sorted(_ranks20, reverse=True),
      "%d cards, %d films held, %d ranked" % (len(_pop20), len(_held20),
                                              sum(1 for n in _ranks20 if n)))
print("20. every film here on the films' Popular page: %d cards, %d ranked"
      % (len(_pop20), sum(1 for n in _ranks20 if n)), flush=True)

print()
# ---- a service's list for Start is kept: the second Start does not build it again
_calls217 = []
_real217 = api.service_catalogue
api._start_service_kept.clear()
try:
    api.service_catalogue = lambda con, one: (_calls217.append(one("provider")) or _real217(con, one))
    _r1 = api._start_service(api.lib.db(), 8, {})
    _n1 = len(_calls217)
    _r2 = api._start_service(api.lib.db(), 8, {})
    time.sleep(0.5)                  # and nothing built behind it either
    check("217 a service's Start row is built once and then kept",
          _n1 == 2 and len(_calls217) == 2 and [c.get("ratingKey") for c in _r1] == [c.get("ratingKey") for c in _r2],
          "%d then %d" % (_n1, len(_calls217)))
    # past its time: the kept one answers at once, a fresh one is built behind it
    for _k217 in list(api._start_service_kept):
        _t, _rows, _b = api._start_service_kept[_k217]
        api._start_service_kept[_k217] = (_t - api.START_SERVICE_FOR - 1, _rows, False)
    _r3 = api._start_service(api.lib.db(), 8, {})
    for _ in range(200):
        if len(_calls217) >= 4:
            break
        time.sleep(0.05)
    check("217b past its time the kept row answers and a fresh one is built behind it",
          [c.get("ratingKey") for c in _r3] == [c.get("ratingKey") for c in _r1] and len(_calls217) == 4,
          str(len(_calls217)))
finally:
    api.service_catalogue = _real217

# ---- Popular films: a film not out yet is no card to ask for
_real221 = api.top_films
try:
    _rows221 = [dict(r) for r in _real221()]
    import pd_streaming as _ps221
    _by221, _name221 = _ps221._held(api.lib)
    from pd_library import flatten_title as _flat221
    # nor on a pack or a download here: that card is the pack's, not the catalogue's
    _offer221 = {api._bare(o.get("title")) for o in api._offered_quietly()}
    _free221 = next((r for r in _rows221 if not _by221.get(("movie", int(r.get("tmdb") or 0)))
                     and api._bare(r.get("title")) not in _offer221
                     and not _ps221._by_name(_name221, "movie", _flat221(r["title"]),
                                             int(r.get("year") or 0))), None)
    if _free221 is None:
        print("221. skipped: every popular film is here", flush=True)
    else:
        _free221["released"] = "2099-12-15"
        api.top_films = lambda: _rows221
        _show221 = api.films_show
        # no release on the tracker: a date ahead with a release is out
        _keep221 = (_ps221.fetchable, _ps221.boxed)
        _ps221.fetchable, _ps221.boxed = (lambda one: []), (lambda one: False)
        api.films_show = lambda: _pl.films_shown({"download": True, "request": True}, api.FILMS_SHOW)
        try:
            _cards221 = api.popular_films(api.lib.db(), as_row=False)
        finally:
            api.films_show = _show221
        _shown221 = {str(c.get("title")) for c in _cards221 if c.get("askable")}
        _up221 = [c for c in _cards221 if str(c.get("title")) == str(_free221["title"])]
        # requests hidden: upcoming hidden with them
        api.films_show = lambda: _pl.films_shown({"download": True, "request": False}, api.FILMS_SHOW)
        try:
            _hid221 = {str(c.get("title")) for c in api.popular_films(api.lib.db(), as_row=False)}
        finally:
            api.films_show = _show221
        # upcoming hidden by this viewer, requests shown: hidden
        api.films_show = lambda: _pl.films_shown({"download": True, "request": True, "upcoming": False}, api.FILMS_SHOW)
        try:
            _nope221 = {str(c.get("title")) for c in api.popular_films(api.lib.db(), as_row=False)}
        finally:
            api.films_show = _show221
        # the same film a year on, out now: a card again
        _free221["released"] = "2001-01-01"
        api.films_show = lambda: _pl.films_shown({"download": True, "request": True}, api.FILMS_SHOW)
        try:
            _again221 = {str(c.get("title")) for c in api.popular_films(api.lib.db(), as_row=False)}
        finally:
            api.films_show = _show221
        # dated ahead with a release: a card to fetch, for a viewer hiding upcoming too
        _free221["released"] = "2099-12-15"
        _ps221.fetchable = lambda one: [{"id": 1}]
        api.films_show = lambda: _pl.films_shown({"download": True, "request": True, "upcoming": False}, api.FILMS_SHOW)
        try:
            _rel221 = [c for c in api.popular_films(api.lib.db(), as_row=False)
                       if str(c.get("title")) == str(_free221["title"])]
        finally:
            api.films_show = _show221
            _ps221.fetchable, _ps221.boxed = _keep221
        check("221 Popular films: dated ahead with a release is out, whatever the viewer hides",
              len(_rel221) == 1 and _rel221[0].get("upcoming") is False
              and _rel221[0].get("onTracker") is True, str(_free221["title"]))
        check("221 Popular films: a film not out yet is upcoming, not a card to ask for, and is once it is out",
              str(_free221["title"]) not in _shown221 and str(_free221["title"]) in _again221
              and len(_up221) == 1 and _up221[0].get("upcoming") is True
              and str(_free221["title"]) not in _hid221 and str(_free221["title"]) not in _nope221,
              str(_free221["title"]))
finally:
    api.top_films = _real221

# ---- 229. a kept list: IMDb's top 100 in the database, read back in its order
import pd_lists as _pl229
_con229 = api.lib.db()
try:
    _page229 = api.list_page(_con229, "imdb-top", {})
    _rows229 = _pl229.items(_con229, "imdb-top")
    _cards229 = _page229.get("Metadata") or []
    _ranks229 = [c.get("listRank") for c in _cards229]
    check("229 the IMDb top 100 is kept in the database and read back in its order",
          len(_rows229) > 0 and 0 < len(_cards229) <= len(_rows229)
          and _ranks229 == sorted(_ranks229, reverse=True)
          and _page229.get("title") == "IMDb Top 250"
          and api.start_more("classic-movie") == {"list": "imdb-top", "name": "IMDb Top 250"},
          "%d rows, %d cards" % (len(_rows229), len(_cards229)))
    check("229b an unknown list is empty", (api.list_page(_con229, "nope", {}).get("Metadata") or []) == [])
    # read again once a day old: the chart's order changed, the list follows it
    _con229.execute("UPDATE list SET updated = updated - 2 * 86400 WHERE id = 'imdb-top'")
    _con229.commit()
    _real229 = api.imdb_top
    _moved229 = list(reversed(_real229()))
    api.imdb_top = lambda: _moved229
    try:
        api.refresh_lists(_con229)
    finally:
        api.imdb_top = _real229
    _now229 = [r["imdb"] for r in _pl229.items(_con229, "imdb-top")]
    check("229c a kept list a day old is read again from its source",
          _now229 == [row[0] for row in _moved229] and
          time.time() - _pl229.updated(_con229, "imdb-top") < 60)
finally:
    _con229.close()

# ---- 231. Start made ahead: a viewer's front is answered as made, made again on the round
_con231 = api.lib.db()
try:
    api._START_KEPT.clear()
    _ask231 = lambda k, d="": {"rows": "1", "kind": "movie", "seed": "0", "era": ""}.get(k, d)
    _who231 = api.who
    _made231 = api.start_make_ahead(_con231)
    _active231 = api.start_active(_con231)
    _key231 = (str(_who231), "movie", "0", "")
    _t0 = time.time()
    _a231 = api.start_page(_con231, _ask231)
    _fast231 = time.time() - _t0
    _b231 = api.start_page(_con231, _ask231)
    check("231 Start's front is made ahead for the active viewers and answered as made",
          _made231 == 2 * len(_active231) and (not _active231 or str(_who231) not in _active231
                                               or _a231 is api._START_KEPT[_key231][1])
          and _a231 is _b231 and api.who == _who231,
          "%d made for %d, %.2f s" % (_made231, len(_active231), _fast231))
    api.start_forget(_who231)
    check("231b what a viewer tells Start drops their kept front",
          not any(k[0] == str(_who231) for k in api._START_KEPT))
finally:
    _con231.close()

# ---- 235. a series' shuffle with no collection: one is made, named after it, its round moved
_h235 = m.Handler.__new__(m.Handler)
_h235.headers = {}; _h235.client_address = ("127.0.0.1", 0); _h235.guest_name = ""
_h235.path = "/"; _h235.role = "owner"
_con235 = api.lib.db()
try:
    _show235 = _con235.execute("SELECT i.id, i.title FROM item i WHERE i.type='show' AND EXISTS "
                               "(SELECT 1 FROM episode e JOIN file f ON f.episode_id=e.id "
                               "WHERE e.item_id=i.id) ORDER BY i.title LIMIT 1").fetchone()
finally:
    _con235.close()
_st235 = m.read_settings()
_mine235 = _h235.viewer_settings(_st235)
_con235 = api.lib.db()
try:
    _had235 = [c for c in (_mine235.get("collections") or [])
               if m.shelf_series(_con235, c) == str(_show235["id"])]
finally:
    _con235.close()
_mine235.setdefault("shuffles", {})["show:" + str(_show235["id"])] = {"order": ["x"], "pos": 0,
                                                                     "casualStamp": 5}
m.write_settings(_st235, merge=False)
_id235 = _h235.own_series_shelf(str(_show235["id"]))
_mine235 = _h235.viewer_settings(m.read_settings())
_made235 = next((c for c in _mine235.get("collections") or [] if str(c.get("id")) == _id235), {})
check("235 a series' shuffle with no collection makes one, named after it, the round moved onto it",
      bool(_id235) and (_had235 or (_made235.get("name") == _show235["title"]
                                    and _made235.get("series") == [str(_show235["id"])]
                                    and (_mine235.get("shuffles") or {}).get(_id235, {}).get("order") == ["x"]))
      and _h235.own_series_shelf(str(_show235["id"])) == _id235,
      str(_made235)[:200])

# ---- 238. a collection's shuffle can have its own span: the whole series held, some drawn
_con238 = api.lib.db()
try:
    _eps238 = _con238.execute("SELECT e.id, e.season, e.number FROM episode e JOIN file f "
                              "ON f.episode_id = e.id WHERE e.season BETWEEN 1 AND 3 "
                              "LIMIT 40").fetchall()
    _keys238 = [str(r["id"]) for r in _eps238] + ["filmkey000001"]
    _shelf238 = {"shuffleSeason": "s1-s2", "shuffleSeasonWithout": "s2"}
    _got238 = m.Handler.shuffle_part(_con238, _shelf238, _keys238)
    _want238 = [str(r["id"]) for r in _eps238 if int(r["season"]) == 1] + ["filmkey000001"]
    check("238 a collection's shuffle span draws only its seasons; films and no span keep all",
          _got238 == _want238 and m.Handler.shuffle_part(_con238, {}, _keys238) == _keys238,
          "%d of %d" % (len(_got238), len(_keys238)))
finally:
    _con238.close()

# ---- 240. what a collection's shuffle span leaves out is marked on its cards
_con240 = api.lib.db()
try:
    _by240 = {}
    for _r in _con240.execute("SELECT e.id, e.season FROM episode e JOIN file f "
                              "ON f.episode_id = e.id WHERE e.season BETWEEN 1 AND 2 "
                              "LIMIT 60").fetchall():
        _by240.setdefault(int(_r["season"]), []).append(str(_r["id"]))
    _s1, _s2 = _by240.get(1, [])[:3], _by240.get(2, [])[:3]
    _rows240 = [{"ratingKey": "a-s1", "holds": list(_s1)}, {"ratingKey": "a-s2", "holds": list(_s2)},
                {"ratingKey": "a-mix", "holds": _s1[:1] + _s2[:1]}, {"ratingKey": "filmkey000001"}]
    _keys240 = _s1 + _s2 + ["filmkey000001"]
    m.Handler.shuffle_marks(_con240, {"shuffleSeason": "s1"}, _rows240, _keys240)
    _plain240 = [dict(r) for r in _rows240[:2]]
    for _r in _plain240:
        _r.pop("notShuffled", None); _r.pop("shuffleOut", None)
    m.Handler.shuffle_marks(_con240, {}, _plain240, _keys240)
    check("240 a collection's cards say what its shuffle leaves out: whole, in part, or none",
          bool(_s1) and bool(_s2)
          and not _rows240[0].get("notShuffled") and _rows240[1].get("notShuffled") is True
          and _rows240[2].get("shuffleOut") == 1 and not _rows240[3].get("notShuffled")
          and not any(r.get("notShuffled") or r.get("shuffleOut") for r in _plain240),
          str(_rows240))
finally:
    _con240.close()

# ---- 241. a series carries its wide picture, as a film does
_con241 = api.lib.db()
try:
    _r241 = _con241.execute("SELECT * FROM item WHERE type='show' AND backdrop IS NOT NULL "
                            "AND backdrop != '' LIMIT 1").fetchone()
    _m241 = api._show(_con241, _r241) if _r241 else {}
    check("241 a series with a backdrop sends it as its wide picture",
          bool(_r241) and _m241.get("art") == "/art/%s/backdrop" % _r241["id"], str(_m241.get("art")))
finally:
    _con241.close()

# ---- 242. Start fronts made by their own process stand as made here, whatever their age
_con242 = api.lib.db()
_was242 = api.who
try:
    _p242 = {"marker": 242}
    api.who = "t242"
    api.start_keep([("t242", "movie", _p242)], 1.0)
    _ask242 = {"rows": "1", "kind": "movie", "seed": "0", "era": ""}
    _got242 = api.start_page(_con242, lambda k, d="", a=_ask242: a.get(k, d))
    api.start_forget("t242")
    _made242 = api.start_page(_con242, lambda k, d="", a=_ask242: a.get(k, d))
    _sv242 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
    check("242 a front made by the fronts' process is served as it is, a day old or not; forgotten, one is made",
          _got242 is _p242 and _made242 is not _p242 and isinstance(_made242, dict))
    check("242 the fronts' process runs on a copy under its own root, once a day",
          '["--root", work, "--make-fronts", out' in _sv242 and 'made != time.strftime("%Y-%m-%d")' in _sv242
          and '"palladium-server.exe")]' in _sv242 and "if PACKAGED else [sys.executable, os.path.abspath(__file__)])" in _sv242)
finally:
    api.start_forget("t242")
    api.who = _was242
    _con242.close()

# ---- 243. the app reaches everyone at eight, if nothing holds it back; the owner at once
_d243 = os.path.join(root, "t243")
_root243, _static243 = os.path.join(_d243, "root"), os.path.join(_d243, "static")
os.makedirs(_root243); os.makedirs(_static243)
_was243 = (m.ROOT, m.STATIC, m.app_problems)


def _app243(code, name):
    with open(os.path.join(_static243, "palladium.apk"), "w") as f:
        f.write("apk " + name)
    with open(os.path.join(_static243, "version.json"), "w") as f:
        json.dump({"versionCode": code, "versionName": name}, f)


try:
    m.ROOT, m.STATIC = _root243, _static243
    _held243 = ["1 open fault report"]
    m.app_problems = lambda since: list(_held243)
    _app243(10, "0.1.10")
    _first243 = m.app_release()
    _app243(11, "0.1.11")
    _back243 = m.app_release()
    # the role decided by the app routes themselves, never one left on the handler
    _guest243 = m.Handler.__new__(m.Handler); _guest243.role = "owner"
    _guest243.who = lambda path: None
    _owner243 = m.Handler.__new__(m.Handler); _owner243.role = "guest"
    _owner243.who = lambda path: "owner"
    _away243 = m.Handler.__new__(m.Handler); _away243.who = lambda path: None
    _away243.owner_away = True
    for _h243 in (_guest243, _owner243, _away243):
        _h243.path = "/app/version"
    _g243 = open(_guest243.app_for_asker()[0]).read()
    _o243 = open(_owner243.app_for_asker()[0]).read()
    _a243 = open(_away243.app_for_asker()[0]).read()
    _keen243 = m.Handler.__new__(m.Handler); _keen243.role = "guest"
    _keen243.app_channel = lambda: "latest"
    _calm243 = m.Handler.__new__(m.Handler); _calm243.role = "owner"
    _calm243.app_channel = lambda: "daily"
    _k243 = open(_keen243.app_for_asker()[0]).read()
    _c243 = open(_calm243.app_for_asker()[0]).read()
    _held243.clear()
    _out243 = m.app_release()
    _again243 = m.app_release()
    _g2_243 = open(_guest243.app_for_asker()[0]).read()
    check("243 the first app here is the released one; a newer one waits while a fault is open",
          _first243.startswith("app 0.1.10 released") and _back243.startswith("app 0.1.11 held back"), _first243 + " | " + _back243)
    check("243 held back, a guest is given the released app and the owner the newest, at home or away",
          _g243 == "apk 0.1.10" and _o243 == "apk 0.1.11" and _a243 == "apk 0.1.11",
          _g243 + " | " + _o243 + " | " + _a243)
    _sv243 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
    check("243 the day of the eight o'clock look is kept on disk, so a restart after eight lets nothing out",
          'mark = os.path.join(ROOT, APP_RELEASED, "looked")' in _sv243 and "f.write(today)" in _sv243)
    check("243 Settings decide: a guest on Latest gets the newest, the owner on Daily the released one",
          _k243 == "apk 0.1.11" and _c243 == "apk 0.1.10", _k243 + " | " + _c243)
    check("243 with nothing open it goes to everyone, once",
          _out243.startswith("app 0.1.11 released") and _again243 == "" and _g2_243 == "apk 0.1.11", _out243)
finally:
    m.ROOT, m.STATIC, m.app_problems = _was243
    shutil.rmtree(_d243, ignore_errors=True)

# ---- 244. one reading of the settings for the length of an answer, and only on its thread
_a244 = m.read_settings()
_b244 = m.read_settings()
with m.settings_held() as _h244:
    _c244 = m.read_settings()
    _d244 = m.read_settings()
_e244 = m.read_settings()
check("244 held, the settings are read once for the answer; outside it each reading is its own",
      _c244 is _d244 is _h244 and _a244 is not _b244 and _e244 is not _c244 and _a244 == _e244)

# ---- 245. what keeping a person costs, counted on one connection, as playback picks the file
_who245 = "me"
_own245 = m.Handler.in_house().follower_cost(_who245)
_files245, _size245, _seen245 = 0, 0, set()
_con245 = api.lib.db()
try:
    import pd_localapi as _pl245
    _keys245 = [str(r["key"]) for r in _con245.execute(
        "SELECT key FROM progress WHERE who = ? AND position > 30 AND position < " + _pl245.FINISHED_SQL +
        " AND COALESCE(casual, 0) = 0 ORDER BY updated DESC LIMIT 40", (_who245,))]
    _h245 = m.Handler.in_house()
    for _k245 in _keys245 + [str(k) for k in _h245.watchlist_of(_who245)[:40]] + [str(k) for k in _h245.casual_ahead(_who245)]:
        if _k245 in _seen245:
            continue
        _seen245.add(_k245)
        _f245 = (api.file_for(_k245, 0) or {}).get("file")
        _r245 = _con245.execute("SELECT size FROM file WHERE path=?", (_f245,)).fetchone() if _f245 else None
        if _r245:
            _files245 += 1
            _size245 += _r245["size"] or 0
finally:
    _con245.close()
check("245 a person's cost is the same files and gigabytes as file_for counted them",
      _own245["files"] == _files245 and _own245["gb"] == round(_size245 / 1e9, 1), "%s vs %s %s" % (_own245, _files245, _size245))

# ---- 246. a viewer who watched something has their Start fronts made again
api.start_stale_take()
api.start_watched("t246")
_took246 = api.start_stale_take()
_none246 = api.start_stale_take()
_some246, _day246 = os.path.join(root, "t246_some.json"), os.path.join(root, "t246_day.json")
with open(_day246, "w", encoding="utf-8") as f:
    json.dump({"day": "d", "fronts": [{"who": "a", "kind": "movie", "page": 1}, {"who": "b", "kind": "movie", "page": 2}]}, f)
with open(_some246, "w", encoding="utf-8") as f:
    json.dump({"day": "d", "fronts": [{"who": "a", "kind": "movie", "page": 9}]}, f)
m.fronts_merge(_some246, _day246)
with open(_day246, encoding="utf-8") as f:
    _merged246 = {(x["who"], x["kind"]): x["page"] for x in json.load(f)["fronts"]}
_lp246 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_localapi.py"), encoding="utf-8").read()
check("246 watched: the viewer waits for new fronts once, theirs replace their old ones, the rest stand",
      _took246 == ["t246"] and _none246 == [] and _merged246 == {("a", "movie"): 9, ("b", "movie"): 2}
      and _lp246.count("self.start_watched(self.who)") == 2, str(_merged246))

# ---- 247. a race or game read in leads the Sports row, wearing New, newest first; not on films
import pd_sports as _sp247
_con247 = api.lib.db()
try:
    _rows247 = _con247.execute(
        "SELECT e.id, MAX(COALESCE(f.mtime, f.ctime)) AS came FROM episode e JOIN item i ON i.id=e.item_id "
        "JOIN file f ON f.episode_id=e.id WHERE i.type='show' AND i.title IN ('NFL','Formula 1') "
        "GROUP BY e.id ORDER BY came DESC LIMIT 1").fetchall()
    if _rows247:
        _at247 = float(_rows247[0]["came"])
        _in247 = api._games_arrived(_con247, now=_at247 + 60)
        _out247 = api._games_arrived(_con247, now=_at247 + _sp247.ARRIVED_DAYS * 86400 + 60)
        check("247 a game read in a minute ago leads the games arrived, an episode; gone after the days",
              bool(_in247) and _in247[0]["ratingKey"] == str(_rows247[0]["id"]) and _in247[0]["type"] == "episode"
              and all(c["ratingKey"] != str(_rows247[0]["id"]) for c in _out247))
finally:
    _con247.close()
_page247 = api.route(api.lib.db(), "/library/sections/1/recentlyAdded", {}) or {}
_sports247 = api.route(api.lib.db(), "/library/sports", {}) or {}
_ids247 = {str(r["id"]) for r in _rows247} if _rows247 else set()
_now247 = api._games_arrived(api.lib.db())
check("247 Recently added films carries no game arrived",
      bool(_page247) and not any(str(m.get("ratingKey")) in {str(c["ratingKey"]) for c in _now247}
                                 for m in _page247.get("Metadata") or []))
check("247 the Sports row leads with the games arrived, then a card a sport",
      [str(m.get("ratingKey")) for m in (_sports247.get("Metadata") or [])][:len(_now247)]
      == [str(c["ratingKey"]) for c in _now247])
check("247 a game from the tracker wears its own drawn poster once it is here",
      api._game_poster("NFL", "D:/x/NFL.2026.10.25.Cincinnati.Bengals.Vs.Baltimore.Ravens.720p.WEB.H264-G/a.mkv")
      == "/art/%s/poster" % _sp247.read_name("NFL.2026.10.25.Cincinnati.Bengals.Vs.Baltimore.Ravens.720p.WEB.H264-G")["event"]
      and api._game_poster("Some Show", "D:/x/NFL.2026.10.25.A.Vs.B.720p.WEB.H264-G/a.mkv") is None)
if _rows247 and _in247:
    check("247 and the card on the shelf carries it", str(_in247[0].get("thumb") or "").startswith("/art/sp"),
          str(_in247[0].get("thumb")))
    check("247 and when it came in, which the app orders the shelf by",
          int(_in247[0].get("addedAt") or 0) == int(_at247), str(_in247[0].get("addedAt")))
check("247 a game file named in lower case is filed by its folder's name",
      _sp247.told("D:/x/NFL.2026.10.25.Cincinnati.Bengals.Vs.Baltimore.Ravens.720p.WEB.H264-G/"
                  "nfl.2026.10.25.cincinnati.bengals.vs.baltimore.ravens.720p.web.h264-g.mkv")[3]
      .startswith("Cincinnati Bengals"))

# ---- 248. the shuffle's next go to the copy under the keys of the files this machine holds
_h248 = m.Handler.__new__(m.Handler)
_who248 = [w for w, u in (m.read_settings().get("users") or {}).items()
           if isinstance(u, dict) and u.get("shuffles")]
_ahead248 = [k for w in _who248 for k in _h248.casual_ahead(w)]
_packed248 = [k for k in _ahead248 if str(k).startswith("o") and m.Handler.as_held(k) != k]
check("248 the shuffle's next are listed under the library's keys where the file is here",
      bool(_ahead248) and not _packed248, str(_packed248[:5]))

# ---- 249. one id per file: the title's key and the code of the name, the same in any folder
import pd_library as _lb249
_con249 = api.lib.db()
try:
    _rows249 = _con249.execute("SELECT item_id, episode_id, path, fid FROM file").fetchall()
finally:
    _con249.close()
_off249 = [r["path"] for r in _rows249 if r["fid"] != _lb249.file_id(r["episode_id"] or r["item_id"], r["path"])]
check("249 every file in the library carries its file id, made from its title and its name",
      bool(_rows249) and not _off249, str(_off249[:3]))
check("249 a file's id is the same on disk, on the cache and inside its pack",
      _lb249.file_id("e5d4ce2d5d554", r"D:\Download\MacGyver.S01-S07\Season 1\MacGyver.S01E04.The.Gauntlet.1080p.Web.HEVC.x265-JoeBee.mkv")
      == _lb249.file_id("e5d4ce2d5d554", r"D:\Palladium\MacGyver.S01E04.The.Gauntlet.1080p.Web.HEVC.x265-JoeBee.mkv")
      == _lb249.file_id("e5d4ce2d5d554", "MacGyver.S01-S07/Season 1/macgyver.s01e04.the.gauntlet.1080p.web.hevc.x265-joebee.mkv")
      == "e5d4ce2d5d554-c4043735")

# ---- 250. a film inside a pack carries the title id the library files it under once here
import pd_torrents as _pt250
_con250 = api.lib.db()
try:
    _held250 = {_lb249.file_code(r["path"]): (r["episode_id"] or r["item_id"])
                for r in _con250.execute("SELECT path, item_id, episode_id FROM file")}
    _both250 = [f for p in _pt250.load()["packs"] for f in p.get("films") or []
                if _lb249.file_code(f.get("path") or "") in _held250][:300]
    _off250 = []
    for _f in _both250:
        _t, _fid = _pt250.ids_of(_f, api.lib, _con250)
        if _t != _held250[_lb249.file_code(_f["path"])]:
            _off250.append(os.path.basename(_f["path"]))
finally:
    _con250.close()
check("250 pack films read from their names carry the library's own title id (%d checked)" % len(_both250),
      len(_both250) > 50 and not _off250, str(_off250[:3]))

# ---- 251. a pack title answers to its title id: its card, its page, its downloads
if not _pt250.STATE.get("lib"):
    _pt250.STATE["lib"] = lambda: api.lib
_pt250.HELD["at"] = 0.0
_held251 = _pt250.held_keys()
_films251 = [f for p in _pt250.load()["packs"] for f in p.get("films") or [] if f.get("titleId")]
_only251 = next((f for f in _films251 if f.get("kind") != "episode" and f["titleId"] not in _held251), None)
_have251 = next((f for f in _films251 if f["titleId"] in _held251), None)
_con251 = api.lib.db()
try:
    if _only251:
        _card251 = api.metadata_for(_con251, _only251["titleId"], brief=True) or {}
        _old251 = api.metadata_for(_con251, _only251["key"], brief=True) or {}
        check("251 a title only a pack has: its card under the title id, from its title id or its old pack key",
              _pt250.only_in_pack(_only251["titleId"]) and _pt250.only_in_pack(_only251["key"])
              and _card251.get("ratingKey") == _only251["titleId"] == _old251.get("ratingKey"),
              "%s %s" % (_card251.get("ratingKey"), _old251.get("ratingKey")))
    if _have251:
        _lib251 = api.metadata_for(_con251, _have251["titleId"], brief=True) or {}
        check("251 a pack title that is here is the library's own, under the same key",
              not _pt250.only_in_pack(_have251["titleId"]) and not _pt250.only_in_pack(_have251["key"])
              and _lib251.get("ratingKey") == _have251["titleId"] and not _lib251.get("offered"),
              str(_lib251.get("ratingKey")))
finally:
    _con251.close()
check("251 an old pack key is its title's key wherever a key is kept",
      bool(_films251) and m.Handler.as_held(_films251[0]["key"]) == _films251[0]["titleId"])
_dl251 = next((d for d in _pt250.load()["downloads"]
               if str(d.get("key") or "").startswith("o") and _pt250.title_of(d["key"]) != d["key"]), None)
if _dl251:
    check("251 a download made under a pack key answers to the title's id",
          (_pt250.latest_download(_pt250.title_of(_dl251["key"])) or {}).get("key") is not None)

# ---- 252. the pack keys written down move onto their titles: shuffles, collections, watchlists
import re as _re252
_known252 = _pt250.pack_keys_moved()["titles"]
def _count252(o):
    if isinstance(o, dict):
        return sum(_count252(v) for v in o.values())
    if isinstance(o, list):
        return sum(_count252(v) for v in o)
    return 1 if isinstance(o, str) and o in _known252 else 0
_before252 = m.read_settings() or {}
_n_before252 = _count252(_before252)
_first252 = None
for _u in (_before252.get("users") or {}).values():
    for _r in ((_u or {}).get("shuffles") or {}).values():
        _o = [k for k in (_r or {}).get("order") or [] if k in _known252]
        if _o:
            _first252 = (_r, (_r.get("order") or []).index(_o[0]), _o[0])
            break
    if _first252:
        break
_moved252 = m.retire_pack_keys()
_after252 = m.read_settings() or {}
check("252 every pack key written down is moved onto its title (%d were)" % _n_before252,
      _moved252 == _n_before252 and _count252(_after252) == 0
      and sorted(_after252) == sorted(_before252)
      and len(_after252.get("users") or {}) == len(_before252.get("users") or {}),
      "%d moved, %d left" % (_moved252, _count252(_after252)))
check("252 and a second sweep finds nothing to move", m.retire_pack_keys() == 0)
_root252 = _pt250.STATE.get("root")
_pt250.STATE["root"] = None
_early252 = _pt250.all_named()
_pt250.STATE["root"] = _root252
check("252 the sweep does not take packs not read yet for packs all named", not _early252 and _pt250.all_named())

# ---- 253. a release is chosen by its file id, and says its own download state
_groups253 = {}
for _p in _pt250.load()["packs"]:
    for _f in _p.get("films") or []:
        if _f.get("kind") != "episode" and _f.get("fid"):
            _groups253.setdefault(_pt250._same(_f), []).append((_f, _p))
_two253 = next((g for g in _groups253.values() if len(g) >= 2), None)
if _two253:
    _vers253 = _pt250._versions(_two253)
    _fids253 = [f["fid"] for f, _ in _two253]
    check("253 each release of a film is listed under its file id, and that id finds that copy",
          [v["key"] for v in _vers253] == _fids253
          and all(_pt250.by_key(v["key"])[1] is f for v, (f, _) in zip(_vers253, _two253)),
          str([v["key"] for v in _vers253][:3]))

# ---- 254. a programme only packs carry: its own key on its card, its page and its seasons open
_pt250.PROGRAMMES["at"] = 0.0
_pt250.HELD["at"] = 0.0
_pt250.STATE["shows"] = None
_shows254 = list(_pt250.offered_shows())
check("254 offered programmes are listed, none under a pack's own key (%d)" % len(_shows254),
      bool(_shows254) and not any(str(c.get("ratingKey")).startswith("os") for c in _shows254))
_lone254 = next((c for c in _shows254 if str(c.get("ratingKey")) not in _pt250.library_shows()), None)
check("254 there is a programme only packs carry to try", _lone254 is not None)
if _lone254:
    _k254 = str(_lone254["ratingKey"])
    _page254 = cards("/library/metadata/" + _k254)
    _kids254 = cards("/library/metadata/%s/children" % _k254)
    check("254 a programme only packs carry opens: its page and its seasons, under its own key",
          bool(_page254) and _page254[0].get("type") == "show" and bool(_kids254)
          and str(_kids254[0].get("ratingKey")).startswith(_k254 + "-s"),
          "%s %s" % (str(_page254[:1])[:150], str(_kids254[:1])[:150]))
    if _kids254:
        _eps254 = cards("/library/metadata/%s/children" % _kids254[0]["ratingKey"])
        check("254 and a season's episodes, under the episodes' own keys",
              bool(_eps254) and all(str(e.get("ratingKey")).startswith("e") and e.get("offered") for e in _eps254)
              and _eps254[0].get("grandparentRatingKey") == _k254, str(_eps254[:1])[:200])
    check("254 the old name-made key still opens the same page",
          (cards("/library/metadata/" + _pt250.show_key(_lone254["title"])) or [{}])[0].get("ratingKey") == _k254)
_any254 = next(iter(_pt250._episodes_by_show()), None)
check("254 what a collection writes down for a programme is its episodes' own keys",
      bool(_any254) and bool(_pt250.offered_keys(_any254))
      and not any(str(k).startswith("o") for k in _pt250.offered_keys(_any254)))

# ---- 255. a download coming in from the tracker: its card under the key it will have
_con255 = api.lib.db()
try:
    _ep255 = _con255.execute("SELECT f.path, f.episode_id FROM file f WHERE f.episode_id IS NOT NULL "
                             "AND lower(f.path) LIKE '%s0_e0%' LIMIT 1").fetchone()
    _film255 = _con255.execute("SELECT f.path, f.item_id FROM file f WHERE f.episode_id IS NULL "
                               "AND f.path LIKE '%1080p%' LIMIT 1").fetchone()
finally:
    _con255.close()
_rows255 = []
_real255 = m.coming_in, m.landing
try:
    for _r255 in (_ep255, _film255):
        if _r255:
            _stem255 = os.path.splitext(os.path.basename(_r255[0]))[0]
            m.coming_in = lambda s=_stem255: [{"name": s, "progress": 0.5, "from": "tracker", "state": "downloading"}]
            m.landing = lambda: []
            _lib255 = api.lib.db()
            try:
                _want255 = api.lib.key_of_file(_lib255, "show" if _r255 is _ep255 else "movie", _r255[0])
            finally:
                _lib255.close()
            _rows255.append((m.tracker_coming()[0]["ratingKey"], _want255))
finally:
    m.coming_in, m.landing = _real255
check("255 a download coming in stands under the title id its file will have",
      len(_rows255) == 2 and all(a == b for a, b in _rows255), str(_rows255))

# ---- 256. what people fetched themselves goes on their watchlists, once; the house's own fetches do not
_s256 = m.read_settings() or {}
_s256.pop("watchFilled", None)
m.write_settings(_s256, merge=False)
_mine256 = [d for d in _pt250.load()["downloads"] if not d.get("auto") and d.get("token")]
_auto256 = [d for d in _pt250.load()["downloads"] if d.get("auto") and d.get("token")]
def _wlb256(st, tok):
    mine = st if tok == "me" else (st.get("users") or {}).get(tok) or {}
    return set(str(k) for k in mine.get("watchlist") or [])
_asked256 = {(str(d["token"]), m.watch_key_of(d.get("titleId") or d.get("key"))) for d in _mine256}
# the live server has run this pass already: what it put there is taken off again on the copy,
# so the pass has to put it back
_off256 = m.read_settings() or {}
for _t256, _k256 in _asked256:
    _u256 = _off256 if _t256 == "me" else (_off256.get("users") or {}).get(_t256)
    if _k256 and isinstance(_u256, dict):
        _u256["watchlist"] = [k for k in _u256.get("watchlist") or [] if str(k) != _k256]
m.write_settings(_off256, merge=False)
_before256 = m.read_settings() or {}
_house256 = {(str(d["token"]), m.watch_key_of(d.get("titleId") or d.get("key"))) for d in _auto256} - _asked256
_house256 = {(t, k) for t, k in _house256 if k and k not in _wlb256(_before256, t)}
# one film somebody fetched and has watched since: marked watched on the copy
def _who256(tok):
    return (str(_before256.get("ownerIs") or "") or "me") if tok == "me" else tok
_seenone256 = next(((str(d["token"]), m.watch_key_of(d.get("titleId") or d.get("key"))) for d in _mine256
                    if m.watch_key_of(d.get("titleId") or d.get("key"))
                    and m.watch_key_of(d.get("titleId") or d.get("key")) not in _wlb256(_before256, str(d["token"]))),
                   None)
if _seenone256:
    _c256 = api.lib.db()
    try:
        _c256.execute("INSERT OR REPLACE INTO progress (who, key, position, duration, updated, marked) "
                      "VALUES (?, ?, 0, 0, ?, 1)", (_who256(_seenone256[0]), _seenone256[1], int(time.time())))
        _c256.commit()
    finally:
        _c256.close()
def _watched256(tok, key):
    c = api.lib.db()
    was = api.who
    try:
        api.who = _who256(tok)
        return api._watched(c, key)
    finally:
        api.who = was
        c.close()
import pd_tracker as _tr256
_log256 = list(_tr256.STATE.get("log") or [])
_tr256.STATE["log"] = []          # the pack fetches alone, so what the house fetched is told apart
try:
    _added256 = m.fill_watchlists()
finally:
    _tr256.STATE["log"] = _log256
_after256 = m.read_settings() or {}
def _wl256(tok):
    mine = _after256 if tok == "me" else (_after256.get("users") or {}).get(tok) or {}
    return set(str(k) for k in mine.get("watchlist") or [])
_miss256 = [d.get("key") for d in _mine256 if m.watch_key_of(d.get("titleId") or d.get("key"))
            and not _watched256(str(d["token"]), m.watch_key_of(d.get("titleId") or d.get("key")))
            and m.watch_key_of(d.get("titleId") or d.get("key")) not in _wl256(str(d["token"]))]
check("256 a film somebody fetched and has watched since is not put back on their watchlist",
      _seenone256 is not None and _seenone256[1] not in _wl256(_seenone256[0]), str(_seenone256))
check("256 every title somebody fetched themselves is on their own watchlist (%d added)" % _added256,
      _added256 > 0 and not _miss256 and _after256.get("watchFilled"), str(_miss256[:3]))
_tracker256 = {k for t, k in _house256}
check("256 a title only the house fetched by itself is not put on anybody's watchlist (%d looked at)" % len(_house256),
      bool(_house256) and not [(t, k) for t, k in _house256 if k in _wl256(t)
                               and not any(k == kk for tt, kk in _asked256)])
check("256 and the pass runs once", m.fill_watchlists() == 0)
_ep256 = next((d for d in _mine256 if str(_pt250.title_of(d.get("key"))).startswith("e")), None)
if _ep256:
    check("256 a single episode fetched puts nothing on the watchlist - not it, not its programme",
          m.watch_key_of(_ep256["key"]) == "", m.watch_key_of(_ep256["key"]))
check("256 a single episode from the tracker puts nothing on it either",
      m.watch_key_of("", "Some.Show.S02E05.1080p.WEB.h264-GRP") == "")
_con256 = api.lib.db()
try:
    _show256 = _con256.execute("SELECT i.id, i.title FROM item i JOIN episode e ON e.item_id=i.id "
                               "WHERE i.type='show' AND e.season=1 LIMIT 1").fetchone()
finally:
    _con256.close()
if _show256:
    _rel256 = "%s.S01.1080p.BluRay.x264-GRP" % re.sub(r"[^A-Za-z0-9]+", ".", _show256[1]).strip(".")
    check("256 a season fetched from the tracker puts its programme on the watchlist, once",
          m.watch_key_of("", _rel256) == str(_show256[0]), "%s -> %s" % (_rel256, m.watch_key_of("", _rel256)))

# ---- 257. one torrent, several films asked for: its line is the one coming in, once
import pd_torrents as _pt257


class _QB257:
    def __init__(self, *a):
        pass

    def _call(self, *a, **k):
        return json.dumps([{"hash": "AB257", "name": "A Pack 257", "state": "downloading",
                            "dlspeed": 5e6, "size": 9e9, "added_on": 1}]).encode()


_d257 = {"config": {}, "packs": [], "downloads": [
    {"key": "k1", "hash": "ab257", "title": "Coming In", "state": "downloading", "progress": 0.4,
     "mbit": 40.0, "size": 3, "who": "Ann"},
    {"key": "k2", "hash": "ab257", "title": "Behind It", "state": "queued", "size": 4, "who": "Ann"}]}
_was257 = (_pt257.QB, _pt257.load, _pt257._place)
try:
    _pt257.QB, _pt257.load, _pt257._place = _QB257, (lambda: _d257), (lambda k: 1)
    m.COMING["at"] = 0.0
    _rows257 = [(r["name"], r.get("state"), r.get("mbit")) for r in m.coming_in()]
finally:
    _pt257.QB, _pt257.load, _pt257._place = _was257
    m.COMING["at"] = 0.0
check("257 a torrent with two films asked for: the one coming in on its line, the other queued once",
      _rows257 == [("Coming In", "downloading", 40.0), ("Behind It", "queued", 0.0)], str(_rows257))

# ---- 258. what a cache server downloads for somebody is on the main server's line too
_was258 = (dict(m.CACHE_ACTIVE), _pt257._following)
try:
    _pt257._following = lambda: {}
    m.CACHE_ACTIVE.update(at=time.time(), asked=time.time(), rows=[
        {"ratingKey": "t1", "offer": {"token": "tokA", "on": "C"}},
        {"ratingKey": "t2", "offer": {"token": "tokB", "on": "C"}},
        {"ratingKey": "t0", "offer": {"token": "tokA", "on": "C"}}])
    _own258 = m.with_cache_downloads([{"ratingKey": "t0", "offer": {"token": "tokA"}}])
    _ann258 = m.with_cache_downloads([], "tokA")
finally:
    m.CACHE_ACTIVE.update(_was258[0])
    _pt257._following = _was258[1]
check("258 the owner's line: this server's, then the cache's, a title once",
      [r["ratingKey"] for r in _own258] == ["t0", "t1", "t2"], str(_own258))
check("258 a guest's line: their own from the cache, and no token in it",
      [r["ratingKey"] for r in _ann258] == ["t1", "t0"]
      and not any("token" in r["offer"] for r in _ann258), str(_ann258))

# ---- 259. a film that is an episode as well: its page and its search card play the episode
_con259 = api.lib.db()
try:
    _ep259 = next((r for r in _con259.execute(
        "SELECT e.id, e.title, e.aired FROM episode e WHERE e.aired LIKE '____-%' AND EXISTS "
        "(SELECT 1 FROM file f WHERE f.episode_id = e.id)").fetchall()
        if len(str(r["title"] or "").split()) >= 4 and len(str(r["title"])) >= 14
        and not _con259.execute("SELECT 1 FROM item WHERE type='movie' AND lower(title)=lower(?)",
                                (r["title"],)).fetchone()), None)
    if _ep259:
        _y259 = int(str(_ep259["aired"])[:4])
        _one259 = {"title": str(_ep259["title"]).upper(), "year": _y259 - 1}
        check("259 a film by an episode's name, aired within a year: the episode",
              api._arrived_since(_con259, _one259) == str(_ep259["id"]))
        check("259 the same name years apart is another film",
              api._arrived_since(_con259, dict(_one259, year=_y259 - 3)) == "")
        _hubs259 = [{"type": "movie", "Metadata": [
            {"ratingKey": "rt259none", "type": "movie", "title": _ep259["title"], "year": _y259}]}]
        api._twins_in_search(_con259, _hubs259)
        _card259 = _hubs259[0]["Metadata"][0]
        check("259 its search card is the episode, with the film's name beside it",
              _card259.get("ratingKey") == str(_ep259["id"]) and _card259.get("aka", {}).get("year") == _y259,
              str(_card259)[:200])
    _short259 = _con259.execute(
        "SELECT e.title, e.aired FROM episode e WHERE length(e.title) BETWEEN 4 AND 9 AND e.aired LIKE '____-%' "
        "AND EXISTS (SELECT 1 FROM file f WHERE f.episode_id = e.id) LIMIT 1").fetchone()
    if _short259:
        check("259 a name of one or two short words is never taken for an episode",
              api._episode_twin(_con259, _short259["title"], int(str(_short259["aired"])[:4])) == "")
finally:
    _con259.close()
check("259 there is an episode to try it on", _ep259 is not None)

# ---- 260. a game somebody downloaded goes on their watchlist and blinks it; opening stops it
import pd_tracker as _tr260
_con260 = api.lib.db()
try:
    _g260 = _con260.execute(
        "SELECT e.id, f.path, MAX(COALESCE(f.mtime, f.ctime)) AS came FROM episode e JOIN item i ON i.id=e.item_id "
        "JOIN file f ON f.episode_id=e.id WHERE i.type='show' AND i.title IN ('NFL','Formula 1') "
        "GROUP BY e.id ORDER BY came DESC LIMIT 1").fetchone()
    _old260 = _con260.execute(
        "SELECT i.id FROM item i JOIN file f ON f.item_id=i.id AND f.episode_id IS NULL WHERE i.type='movie' "
        "GROUP BY i.id HAVING MAX(COALESCE(f.mtime, f.ctime)) < ? LIMIT 1", (time.time() - 30 * 86400,)).fetchone()
finally:
    _con260.close()
_s260 = m.read_settings() or {}
_tok260 = next((t for t, u in (_s260.get("users") or {}).items() if isinstance(u, dict)), None)
check("260 there is a game, an old film and a guest to try it on", bool(_g260 and _old260 and _tok260))
if _g260 and _old260 and _tok260:
    _name260 = m.Handler.name_of_token(_tok260) or "guest260"
    _u260 = _s260["users"][_tok260]
    _u260["myName"] = _name260
    _u260["watchlist"] = [str(_old260[0])] + [k for k in (_u260.get("watchlist") or [])
                                               if str(k) not in (str(_g260["id"]), str(_old260[0]))]
    for _k260 in ("gamesListed", "watchBlink"):
        _u260.pop(_k260, None)
    m.write_settings(_s260, merge=False)
    _log260 = list(_tr260.STATE.get("log") or [])
    _tr260.STATE["log"] = [{"name": os.path.basename(_g260["path"]), "who": _name260}]
    try:
        _at260 = float(_g260["came"]) + 60
        m.WATCH_ARRIVALS["at"] = _at260 - 120
        m.watch_arrivals_pass(now=_at260)
        _after260 = (m.read_settings() or {})["users"][_tok260]
        m.watch_arrivals_pass(now=_at260 + 60)
        _again260 = (m.read_settings() or {})["users"][_tok260]
        # watched and taken off: not put back by the next look
        _w260 = m.read_settings() or {}
        _w260["users"][_tok260]["watchlist"] = [k for k in _w260["users"][_tok260].get("watchlist") or []
                                               if str(k) != str(_g260["id"])]
        m.write_settings(_w260, merge=False)
        m.watch_arrivals_pass(now=_at260 + 120)
        _gone260 = (m.read_settings() or {})["users"][_tok260]
    finally:
        _tr260.STATE["log"] = _log260
    check("260 a game somebody downloaded is on their watchlist once it has come in, and blinks it",
          str(_g260["id"]) in _after260.get("watchlist", []) and str(_g260["id"]) in _after260.get("watchBlink", []),
          str({k: _after260.get(k) for k in ("watchBlink", "gamesListed")})[:200])
    check("260 a film on the list whose file came in long ago does not blink",
          str(_old260[0]) not in _after260.get("watchBlink", []))
    check("260 the game is put there once: the next look adds it no second time",
          _again260.get("watchlist", []).count(str(_g260["id"])) == 1
          and _again260.get("watchBlink", []).count(str(_g260["id"])) == 1)
    check("260 a game somebody downloaded says it is now on their watchlist, once",
          [n.get("how") for n in _after260.get("watchNews") or [] if n.get("key") == str(_g260["id"])] == ["fetched"]
          and [n.get("how") for n in _again260.get("watchNews") or [] if n.get("key") == str(_g260["id"])] == ["fetched"],
          str(_after260.get("watchNews"))[:200])
    check("260 a game watched and taken off the watchlist is not put back",
          str(_g260["id"]) not in _gone260.get("watchlist", []))
    _srv260 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
    check("260 opening the Watchlist clears what blinks, and a guest may say so",
          'GUEST_MAY_POST = ("/applog", "/log", "/trace", "/feedback", "/watchlist/seen",' in _srv260
          and 'mine["watchBlink"] = []' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd_notify.py"), encoding="utf-8").read()
          and "if pd_notify.seen(self.viewer_settings(stored)):" in _srv260)

# ---- an episode's own page: its series' cast, for Show more
_con261 = api.lib.db()
try:
    _r261 = _con261.execute(
        "SELECT e.id, e.item_id FROM episode e WHERE EXISTS (SELECT 1 FROM credit c "
        "WHERE c.item_id = e.item_id) LIMIT 1").fetchone()
    if _r261:
        _ep261 = api.metadata_for(_con261, str(_r261["id"]), brief=False) or {}
        _br261 = api.metadata_for(_con261, str(_r261["id"]), brief=True) or {}
        check("261 an episode's page carries its series' cast, a card does not",
              len(_ep261.get("Role") or []) > 0 and "Role" not in _br261,
              "%d roles" % len(_ep261.get("Role") or []))
    else:
        check("261 a series with a cast to test with", False)
finally:
    _con261.close()

# ---- a watchlisted film: blinks at each quality step the viewer sees; never on Continue watching
import pd_streaming as _ps262
_s262 = m.read_settings() or {}
_toks262 = [t for t, u in (_s262.get("users") or {}).items() if isinstance(u, dict)][:2]
check("262 two guests to try it on", len(_toks262) == 2)
if len(_toks262) == 2:
    _cam262, _good262 = _toks262
    for _t262, _say262 in ((_cam262, "show"), (_good262, "hide")):
        _u262 = _s262["users"][_t262]
        _u262["camReleases"] = _say262
        _u262["watchlist"] = ["rtwatch262"] + [k for k in _u262.get("watchlist") or [] if str(k).startswith("rt") is False]
        _u262["watchSources"] = {"rtwatch262": ""}
        _u262["deckArrivals"] = [{"key": "x", "at": int(time.time())}]
        for _k262 in ("watchBlink", "watchNews", "watchFetched"):
            _u262.pop(_k262, None)
    m.write_settings(_s262, merge=False)
    _was262 = (_ps262.source_of, _ps262.one_of, _ps262.ask_tracker_slowly)
    _now262 = {"how": "cam"}
    _ps262.source_of = lambda k: _now262["how"] if k == "rtwatch262" else _was262[0](k)
    _ps262.one_of = lambda k: {"title": "A Watched Film", "released": "2001-01-01"} if k == "rtwatch262" else _was262[1](k)
    _ps262.ask_tracker_slowly = lambda rows: None
    _blinks262 = {_cam262: 0, _good262: 0}
    try:
        for _step262 in ("cam", "cam", "fetch"):
            _now262["how"] = _step262
            m.watch_news_pass()
            _st262 = m.read_settings() or {}
            for _t262 in _toks262:
                _me262 = _st262["users"][_t262]
                if "rtwatch262" in (_me262.get("watchBlink") or []):
                    _blinks262[_t262] += 1
                    _me262["watchBlink"] = []       # opened the Watchlist
            m.write_settings(_st262, merge=False)
        _end262 = m.read_settings() or {}
    finally:
        _ps262.source_of, _ps262.one_of, _ps262.ask_tracker_slowly = _was262
    check("262 shows cams: two blinks, the cam and the real release",
          _blinks262[_cam262] == 2, "%d blinks" % _blinks262[_cam262])
    check("262 hides cams: one blink, when the real release comes", _blinks262[_good262] == 1, "%d blinks" % _blinks262[_good262])
    check("262 nothing goes to Continue watching any more",
          all("deckArrivals" not in _end262["users"][t] for t in _toks262))

# ---- subtitles: none fits a watched title, one is written from its sound; once
import pd_ai_subs as _ai263
_was263 = (_ai263.ready, _ai263.ask, _ai263.beside)
_asked263 = []
_ai263.ready = lambda: True
_ai263.ask = lambda video, language, **k: _asked263.append((video, language, k.get("key")))
_ai263.beside = lambda video, language: os.path.join(root, "no-such-file.srt")
try:
    _h263 = m.Handler.__new__(m.Handler)
    _one263 = _h263.write_from_sound("X:/a film.mkv", "en", "k1")
    _ai263.beside = lambda video, language: os.path.abspath(__file__)      # one is there
    _two263 = _h263.write_from_sound("X:/a film.mkv", "en", "k1")
finally:
    _ai263.ready, _ai263.ask, _ai263.beside = _was263
_src263 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pd-server.py"), encoding="utf-8").read()
check("263 no subtitle fits a watched title: one is written from its sound, never twice",
      _one263 and not _two263 and _asked263 == [("X:/a film.mkv", "en", "k1")]
      and "if fetch and not login_failed:" in _src263
      and "self.write_from_sound(video, language, key)" in _src263)
check("263 an answer asked by the release's own number is kept only when it names this episode",
      "if not r.get(\"shifted\") or about_this_episode(r)]" in _src263)

print("LIBRARY FAILED: %d" % len(FAILS) if FAILS else "LIBRARY PASSED")
for f in sorted(set(FAILS)):
    # a title with a letter the console's code page lacks ("⧸") stopped the printout
    print("  -", f.encode("ascii", "backslashreplace").decode("ascii"))
sys.stdout.flush()
shutil.rmtree(root, ignore_errors=True)
os._exit(1 if FAILS else 0)
