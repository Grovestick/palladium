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
    nowhere = sum(len(cards("/library/metadata/" + one["key"])[0].get("providers") or [])
                  for one in new[:12])
    check("3 a country with no services listed shows none", nowhere == 0, "%d shown" % nowhere)
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

print()
print("LIBRARY FAILED: %d" % len(FAILS) if FAILS else "LIBRARY PASSED")
for f in sorted(set(FAILS)):
    print("  -", f)
sys.stdout.flush()
shutil.rmtree(root, ignore_errors=True)
os._exit(1 if FAILS else 0)
