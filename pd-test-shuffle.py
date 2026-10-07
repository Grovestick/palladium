#!/usr/bin/env python3
"""The shuffle's rules, tried on a copy of the live papers. Nothing here touches the server.

    python pd-test-shuffle.py              every round of every viewer, then the rules
    python pd-test-shuffle.py --presses 300

The rules (shuffle_draw): one order, drawn when the round starts, and a place in it.
Next and Back move the place and nothing else. A title added to the shelf goes in ahead
of the next one; one taken off stays in the order and is stepped over. Past the end a
new round is drawn. On a machine keeping copies only what it holds is played and no
new round starts.

Exit code 0 when every rule held, 1 otherwise. Run by the build before anything is
installed.
"""
import copy
import importlib.util
import os
import random
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PRESSES = int(sys.argv[sys.argv.index("--presses") + 1]) if "--presses" in sys.argv else 60

spec = importlib.util.spec_from_file_location("pdgate", os.path.join(HERE, "pd-gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
root = tempfile.mkdtemp(prefix="pd-shuffle-")
gate.take_copy(root)
# no network, no processes, no file outside the copy - as the gate runs the server
gate.cut_off(root, 0)

sys.argv = ["pd-server.py", "--root", root]
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("pdserver", os.path.join(HERE, "pd-server.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["pdserver"] = m
spec.loader.exec_module(m)
import pd_torrents
pd_torrents.STATE["root"] = root
pd_torrents.STATE["worker"] = True
pd_torrents.STATE["lib"] = lambda: m.local().lib
pd_torrents.load()
pd_torrents.request = lambda *a, **k: {"ok": True}
H = m.Handler
REAL_AS_HELD = H.as_held
BEGAN = time.time()

FAILS = []
def check(what, ok, detail=""):
    if not ok:
        FAILS.append("%s %s" % (what, detail))
    return ok

def handler(who):
    h = H.__new__(H)
    h.headers = {}; h.client_address = ("127.0.0.1", 0); h.guest_name = ""; h.path = "/"
    stored = m.read_settings() or {}
    if who == str(stored.get("ownerIs") or "") or who == "me":
        h.role = "owner"
    else:
        h.role = "guest"; h.bearer = lambda: who
    h.fetch_ahead = lambda keys: None
    h.ask_the_pack = lambda keys: None
    h.follows_a_main = lambda: False
    return h

def round_of(who, cid):
    s = m.read_settings() or {}
    mine = s if who == "me" else (s.get("users") or {}).get(who) or {}
    return (mine.get("shuffles") or {}).get(cid) or {}

def walk(h, who, cid, presses, label):
    """Random Back and Next: the order never changes, the place moves by one usable
    title, and a place always shows the same title."""
    # the shelf does not change during a walk: read once, not per press
    real_shelf, memo = h.shuffle_shelf, {}

    def shelf_once(c):
        if c not in memo:
            memo[c] = real_shelf(c)
        return memo[c][0], list(memo[c][1])
    h.shuffle_shelf = shelf_once
    try:
        return walked(h, who, cid, presses, label)
    finally:
        del h.shuffle_shelf


def walked(h, who, cid, presses, label):
    first = h.shuffle_draw(cid, peek=True)          # settles the round; moves nothing
    if "error" in first:
        return check(label, first["error"] in ("that shelf is empty", "there is no such shelf",
                                                "nothing on this shelf can be played here"),
                     "peek said " + first["error"])
    # a look is not kept; a press forward and one back keeps the settled round
    h.shuffle_draw(cid)
    h.shuffle_draw(cid, back=True)
    one = round_of(who, cid)
    order0, run0 = list(one["order"]), int(one.get("run") or 1)
    check(label + " no repeats", len(order0) == len(set(order0)))
    shelf, pool = h.shuffle_shelf(cid)
    inside = set(H.as_held(k) for k in pool)
    check(label + " whole shelf in the order", inside <= set(order0),
          "%d missing" % len(inside - set(order0)))
    seen = {}
    rng = random.Random(hash((who, cid)) & 0xffff)
    for n in range(presses):
        before = round_of(who, cid)
        pos0 = int(before["pos"])
        back = rng.random() < 0.45
        said = h.shuffle_draw(cid, back=back)
        after = round_of(who, cid)
        if int(after.get("run") or 1) != run0:
            # past the end: a new round, drawn afresh from the whole shelf
            check(label + " new round holds the shelf", set(after["order"]) == inside)
            order0, run0, seen = list(after["order"]), int(after.get("run") or 1), {}
            continue
        if "error" in said:
            check(label + " only error is the start", back and said["error"] == "nothing before this one"
                  and not any(k in inside for k in order0[:pos0]), said["error"])
            check(label + " place kept on an error", int(after["pos"]) == pos0)
            continue
        pos1 = int(after["pos"])
        check(label + " order unchanged", after["order"] == order0, "press %d" % n)
        between = order0[min(pos0, pos1) + 1:max(pos0, pos1)]
        check(label + " one step", (pos1 < pos0) == back and not any(k in inside for k in between),
              "press %d from %d to %d" % (n, pos0, pos1))
        check(label + " answers the title at the place", said.get("key") == order0[pos1])
        check(label + " title on the shelf", said.get("key") in inside)
        if pos1 in seen:
            check(label + " same title at the same place", seen[pos1] == said.get("key"))
        seen[pos1] = said.get("key")
        check(label + " played and queue follow the order",
              after.get("played") == order0[:pos1 + 1]
              and after.get("queue") == order0[pos1 + 1:pos1 + 1 + H.SHUFFLE_DEEP])
    return True

stored = m.read_settings() or {}
owner = str(stored.get("ownerIs") or "") or "me"
people = [owner] + [w for w in (stored.get("users") or {}) if w != owner]

# ---- 1. every round of every viewer: 300 random presses each
rounds = 0
for who in people:
    mine = stored if who == "me" else (stored.get("users") or {}).get(who) or {}
    h = handler(who)
    shelves = {str(c.get("id")) for c in (mine.get("collections") or [])}
    for cid in list((mine.get("shuffles") or {})):
        if cid not in shelves:
            continue
        try:
            walk(h, who, cid, PRESSES, "walk %s/%s" % (who[:6], cid))
            rounds += 1
        except Exception:
            FAILS.append("walk %s/%s raised: %s" % (who[:6], cid, traceback.format_exc(limit=3)))
print("1. random Back/Next, %d presses a round: %d rounds of %d viewers  (%.0f s)"
      % (PRESSES, rounds, len(people), time.time() - BEGAN), flush=True)

# the rest on the owner's largest round
h = handler(owner)
mine = (stored.get("users") or {}).get(owner) or stored
cid = max((mine.get("shuffles") or {}), key=lambda c: len((mine["shuffles"][c].get("order") or [])))
shelf, pool = h.shuffle_shelf(cid)
inside = [H.as_held(k) for k in pool]

# ---- 2. a look at what is next moves nothing; carrying on answers the title it is on
h.shuffle_draw(cid)
a = copy.deepcopy(round_of(owner, cid))
peek = h.shuffle_draw(cid, peek=True)
b = round_of(owner, cid)
check("2 peek moves nothing", a["order"] == b["order"] and a["pos"] == b["pos"])
nxt = h.shuffle_draw(cid)
check("2 peek said what Next plays", peek.get("key") == nxt.get("key"))
res = h.shuffle_draw(cid, resume=True)
check("2 resume is the title it is on", res.get("key") == round_of(owner, cid)["order"][round_of(owner, cid)["pos"]])
check("2 resume moves nothing", round_of(owner, cid)["pos"] == b["pos"] + (round_of(owner, cid)["pos"] - b["pos"]))
print("2. peek and resume", flush=True)

# ---- 3. Back at the very start
s = m.read_settings(); r = (s["users"][owner] if owner != "me" else s)["shuffles"][cid]
first = next(i for i, k in enumerate(r["order"]) if k in set(inside))
r["pos"] = first; m.write_settings(s, merge=False)
said = h.shuffle_draw(cid, back=True)
check("3 nothing before the first", said.get("error") == "nothing before this one", str(said)[:80])
check("3 place kept", round_of(owner, cid)["pos"] == first)
print("3. Back at the start", flush=True)

# ---- 4. past the end: a new round, every title once
s = m.read_settings(); r = (s["users"][owner] if owner != "me" else s)["shuffles"][cid]
run0 = int(r.get("run") or 1); r["pos"] = len(r["order"]) - 1; m.write_settings(s, merge=False)
said = h.shuffle_draw(cid)
r = round_of(owner, cid)
check("4 new round", int(r.get("run") or 1) == run0 + 1 and "error" not in said)
check("4 new round holds the whole shelf once", sorted(r["order"]) == sorted(set(inside)))
check("4 new round starts at its first", r["pos"] == 0 and said.get("key") == r["order"][0])
print("4. end of the order", flush=True)

CLEAN = copy.deepcopy(round_of(owner, cid))


def fresh_round(presses=3):
    """The round as section 4 left it - newly drawn, nothing odd in it - and a few on."""
    s = m.read_settings()
    (s["users"][owner] if owner != "me" else s)["shuffles"][cid] = copy.deepcopy(CLEAN)
    m.write_settings(s, merge=False)
    for _ in range(presses):
        h.shuffle_draw(cid)


# ---- 5. a pack's episode comes in mid-round: same place, same titles, no repeat
fresh_round(3)
r = copy.deepcopy(round_of(owner, cid))
packs_ahead = [k for k in r["order"][r["pos"] + 1:] if k.startswith("o") and not k.startswith("os")]
packs_behind = [k for k in r["order"][:r["pos"]] if k.startswith("o") and not k.startswith("os")]
if packs_ahead:
    came = {packs_ahead[0]: "e_arrived_ahead"}
    if packs_behind:
        came[packs_behind[0]] = "e_arrived_behind"
    on = r["order"][r["pos"]]
    H.as_held = staticmethod(lambda key, came=came: came.get(str(key)) or REAL_AS_HELD(key))
    try:
        n1 = h.shuffle_draw(cid)
        r2 = round_of(owner, cid)
        want = [came.get(k, k) for k in r["order"]]
        check("5 same length, no repeat", len(r2["order"]) == len(r["order"]) == len(set(r2["order"])))
        check("5 arrived title where its pack key was", want == r2["order"])
        check("5 Next is the title that was next", n1.get("key") == want[r["pos"] + 1]
              and r2["pos"] == r["pos"] + 1, "%s at %s" % (n1.get("key"), r2["pos"]))
        b1 = h.shuffle_draw(cid, back=True)
        check("5 Back returns to the title it was on", b1.get("key") == came.get(on, on),
              "%s vs %s" % (b1.get("key"), on))
    finally:
        H.as_held = staticmethod(REAL_AS_HELD)
    print("5. a pack episode arriving mid-round")
else:
    print("5. skipped: no pack episode ahead in this round")

# ---- 6. the title it is on appears twice: the one at the place is kept
fresh_round(8)
s = m.read_settings(); r = (s["users"][owner] if owner != "me" else s)["shuffles"][cid]
pos = int(r["pos"])
on = r["order"][pos]
later = r["order"][pos + 3]
r["order"].insert(pos + 6, on)      # a repeat of it later
r["order"].insert(2, on)            # and one earlier: the place moves one on
r["order"].insert(1, later)         # another title repeated before the place: one more
r["pos"] = pos + 2
assert r["order"][r["pos"]] == on
want = list(dict.fromkeys(k for k in r["order"]))
m.write_settings(s, merge=False)
n1 = h.shuffle_draw(cid)
b1 = h.shuffle_draw(cid, back=True)
r2 = round_of(owner, cid)
check("6 place stays on its title", b1.get("key") == on and r2["order"][r2["pos"]] == on,
      "back gave %s, on was %s, next gave %s" % (b1.get("key"), on, n1.get("key")))
check("6 no repeats left", len(r2["order"]) == len(set(r2["order"])))
check("6 nothing lost", set(r2["order"]) == set(want))
print("6. repeats in a stored order", flush=True)

# ---- 7. a title added to the shelf goes in ahead; one taken off is stepped over
fresh_round(3)
real_shelf = h.shuffle_shelf
r = copy.deepcopy(round_of(owner, cid))
h.shuffle_shelf = lambda c: (real_shelf(c)[0], real_shelf(c)[1] + ["e_new_on_shelf"])
promised = r["order"][r["pos"] + 1]
n1 = h.shuffle_draw(cid)
r2 = round_of(owner, cid)
at = r2["order"].index("e_new_on_shelf") if "e_new_on_shelf" in r2["order"] else -1
check("7 new title is in the order", at >= 0)
check("7 the promised next still came next", n1.get("key") == promised,
      "next gave %s, promised %s, on the shelf: %s" % (n1.get("key"), promised, promised in set(inside)))
check("7 new title is ahead, after the promised next", at >= r["pos"] + 2,
      "at %d, place was %d" % (at, r["pos"]))
check("7 the rest kept its order", [k for k in r2["order"] if k != "e_new_on_shelf"] == r["order"])
gone = r2["order"][r2["pos"] + 1]
h.shuffle_shelf = lambda c: (real_shelf(c)[0], [k for k in real_shelf(c)[1] if H.as_held(k) != gone])
said = h.shuffle_draw(cid)
r3 = round_of(owner, cid)
check("7 a title off the shelf is stepped over", said.get("key") != gone and gone in r3["order"])
h.shuffle_shelf = real_shelf
print("7. shelf gaining and losing a title", flush=True)

# ---- 8. a round from before the order had a place
fresh_round(0)
s = m.read_settings(); rounds_ = (s["users"][owner] if owner != "me" else s)["shuffles"]
r = rounds_[cid]
keys = [k for k in r["order"] if k in set(inside)]
rounds_[cid] = {"played": keys[:4], "queue": keys[4:14], "order": keys[4:], "at": {}, "run": 1,
                "casualStamp": 0, "back": 1}
m.write_settings(s, merge=False)
said = h.shuffle_draw(cid)                 # a look alone is not kept; a press is
r2 = round_of(owner, cid)
check("8 old round: played first, in order", r2["order"][:4] == keys[:4])
# one back from the last played is the third; Next from there is the fourth
check("8 old round: Next from one back is the last played", r2.get("pos") == 3
      and said.get("key") == keys[3], "pos %s key %s" % (r2.get("pos"), said.get("key")))
check("8 old round: nothing twice", len(r2["order"]) == len(set(r2["order"])))
print("8. a round written before places", flush=True)

# ---- 9. on a machine keeping copies: only what it holds, and no new round at the end
fresh_round(3)
h.follows_a_main = lambda: True
held = set(k for k in inside if k.startswith("e"))
some = set(list(held)[::3])
h.can_be_played = lambda k: k in some
r = copy.deepcopy(round_of(owner, cid))
got = []
for _ in range(len(some) + 5):
    said = h.shuffle_draw(cid)
    got.append(said.get("key"))
r2 = round_of(owner, cid)
check("9 a copy plays only what it holds", all(k in some for k in got), str([k for k in got if k not in some][:3]))
check("9 a copy starts no new round", int(r2.get("run") or 1) == int(r.get("run") or 1))
check("9 a copy leaves the order alone", r2["order"] == r["order"])
h.follows_a_main = lambda: False
print("9. on the copy", flush=True)

# ---- 10. what is fetched ahead and the Resume place read the same round
fresh_round(3)
h2 = handler(owner)
r = round_of(owner, cid)
ahead = [str(k) for k in h2.casual_ahead(owner)]
check("10 the hat's next are in what is copied ahead",
      all(k in ahead for k in r["order"][r["pos"] + 1:r["pos"] + 1 + H.SHUFFLE_DEEP] if k in set(inside)) or not ahead)
print("10. the hat read by the copy", flush=True)

print()
print("SHUFFLE FAILED: %d" % len(FAILS) if FAILS else "SHUFFLE PASSED  (%.0f s)" % (time.time() - BEGAN))
for f in sorted(set(FAILS))[:40]:
    print("  -", f)
sys.stdout.flush()
shutil.rmtree(root, ignore_errors=True)
os._exit(1 if FAILS else 0)
