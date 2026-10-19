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
        check("1 at the front of the shelf", shelf.index(mine[0]) <= 1,
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
    held = next((c for c in before if not c.get("offered")), None)
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
films = [c for c in cards("/library/sections/1/recentlyAdded") if not c.get("offered")][:6]
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
        check("5 %s S%02dE%02d has no file and a pack carries it: the pack's entry is listed"
              % (title, season, n), bool(one.get("offered")) and str(one.get("ratingKey", "")).startswith("o"),
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
    check("9 silence is no reading", not m.take_readings(dolby["id"], dolby["path"], dolby["size"], 3000.0)
          and d not in m.LOUDNESS)
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

# ---- 13. background work waits for anybody watching, direct play included
class _Live:
    rows = []
    def snapshot(self):
        return list(self.rows)
class _Engine:
    streams = []
    def status(self):
        return {"streams": list(self.streams)}
_was = (m.WATCHING, m.engine)
m.WATCHING, m.engine = _Live(), (lambda: _Engine())
try:
    _Live.rows = [{"how": "direct play"}]
    check("13 somebody on direct play: not quiet", m.quiet_now() is False)
    _Live.rows = [{"how": "syncing"}]
    check("13 only a copy being made: quiet", m.quiet_now() is True)
    _Live.rows = []
    check("13 nobody: quiet", m.quiet_now() is True)
    _Engine.streams = ["gpu1"]
    check("13 an encode running: not quiet", m.quiet_now() is False)
finally:
    m.WATCHING, m.engine = _was
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
print("LIBRARY FAILED: %d" % len(FAILS) if FAILS else "LIBRARY PASSED")
for f in sorted(set(FAILS)):
    print("  -", f)
sys.stdout.flush()
shutil.rmtree(root, ignore_errors=True)
os._exit(1 if FAILS else 0)
