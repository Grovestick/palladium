"""A pack moved from the main server to the copy server while people watch and ask for films,
run through Palladium's own rules on two made-up machines. Checked every step: nothing is
fetched from the swarm but films somebody asked for, and only on the main server; the copy
server never reads or downloads during the move; every film asked for arrives and is moved;
a viewer always finds the film on one of the two; the copy server reads once at the end,
everything reads whole, and only then does the home change.

    python pd-test-move.py            several made-up evenings, MOVE PASSED or what failed
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pd_torrents  # noqa: E402

HASH = "p" * 40
FAILS = []


def check(what, ok):
    if not ok:
        FAILS.append(what)


class Machine:
    """One server's qBittorrent and disk: files {index: {priority, progress, size, name}},
    the pack's state, what it fetched from the swarm, and Palladium's download rows."""

    def __init__(self, name, root, films):
        self.name, self.root, self.state = name, root, "stoppedDL"
        self.files = {i: {"index": i, "name": "Pack/F%03d/F%03d.mkv" % (i, i), "size": 1000,
                          "priority": 0, "progress": 0.0} for i in range(films)}
        self.disk = {}
        self.swarm = {}
        self.reads = 0
        self.downloads = []

    def path(self, i):
        return os.path.join(self.root, self.files[i]["name"].replace("/", os.sep))

    # the qBittorrent calls Palladium makes
    def info(self, h):
        return {"state": self.state, "save_path": self.root}

    def files_list(self, h):
        return [dict(f) for f in self.files.values()]

    def priority(self, h, ix, v):
        for i in ix:
            self.files[int(i)]["priority"] = int(v)

    def start(self, h):
        self.state = "downloading"

    def stop(self, h):
        self.state = "stoppedDL"

    def recheck(self, h):
        self.reads += 1
        for i, f in self.files.items():
            if f["priority"] > 0 and self.disk.get(self.path(i)) == f["size"] and f.get("whole_bytes"):
                f["progress"] = 1.0
        self.state = "stoppedDL"


class QBView:
    """What pd_torrents.QB looks like, over one Machine."""

    def __init__(self, m):
        self.m = m

    def info(self, h):
        return self.m.info(h)

    def files(self, h):
        return self.m.files_list(h)

    def priority(self, h, ix, v):
        self.m.priority(h, ix, v)

    def start(self, h):
        self.m.start(h)

    def stop(self, h):
        self.m.stop(h)


def as_machine(m):
    """pd_torrents and os.path pointed at one machine for the length of a call."""
    pd_torrents.load = lambda: {"downloads": m.downloads, "packs": [], "config": {}}
    pd_torrents.asked_for = lambda h: {int(d["index"]) for d in m.downloads
                                       if d.get("state") in ("downloading", "done") and not d.get("off")}
    pd_torrents.kept_whole = lambda h: False
    os.path.exists = lambda p: p in m.disk
    os.path.getsize = lambda p: m.disk[p]


def tick_swarm(m, step):
    """A running pack fetches a slice of every file switched on and short; qBittorrent sets
    a file's full length aside on the disk as it starts."""
    if m.state != "downloading":
        return
    for i, f in m.files.items():
        if f["priority"] > 0 and f["progress"] < 1:
            m.disk[m.path(i)] = f["size"]
            got = min(step, 1.0 - f["progress"])
            f["progress"] = round(f["progress"] + got, 6)
            f["whole_bytes"] = f["progress"] >= 1
            m.swarm[i] = m.swarm.get(i, 0) + got * f["size"]
            if f["progress"] >= 1:
                for d in m.downloads:
                    if int(d["index"]) == i and d["state"] == "downloading":
                        d["state"] = "done"


def evening(seed, scenario="plain", films=30, held=12, steps=400):
    """One made-up evening of a move. scenario: plain; busy (requests three times as
    often); copy-offline (the copy server away for a stretch); copy-restart (it restarts
    in the middle of a film, leaving half of it); main-restart (the main server restarts,
    its rows read back); damaged (one film arrives with wrong bytes); late-request (a film
    asked for while the copy reads). Returns what happened, for the record."""
    rnd = random.Random(seed * 100 + hash(scenario) % 97)
    main = Machine("Main", os.path.join("G:", os.sep, "Movies"), films)
    copy = Machine("Copy", os.path.join("D:", os.sep, "Media"), films)
    saved = (pd_torrents.load, pd_torrents.asked_for, pd_torrents.kept_whole,
             os.path.exists, os.path.getsize)
    for i in rnd.sample(range(films), held):
        f = main.files[i]
        f.update(priority=1, progress=1.0, whole_bytes=True)
        main.disk[main.path(i)] = f["size"]
        main.downloads.append({"hash": HASH, "index": i, "state": "done"})
    main.state = "uploading"
    copy.state = "downloading"           # the harsher case: the copy's pack left running
    asked, moved, home = set(), set(), "main"
    away = range(40, 90) if scenario == "copy-offline" else range(0)
    half_at = 25 if scenario == "copy-restart" else -1
    damaged = None
    ask_rate = 0.15 if scenario == "busy" else 0.05
    tag = "%s seed %d" % (scenario, seed)

    def ask_for_one():
        want = [i for i in main.files if main.files[i]["progress"] < 1 and i not in asked]
        if want:
            i = rnd.choice(want)
            asked.add(i)
            main.downloads.append({"hash": HASH, "index": i, "state": "downloading"})
            as_machine(main)
            pd_torrents.only_asked(QBView(main), HASH, extra=[i])
            # the read-before-download rule, as fetch asks it: never for a film coming in,
            # whose full length qBittorrent has set aside
            if pd_torrents.needs_read_first(main.files_list(HASH), main.root,
                                            main.downloads, HASH, i):
                main.reads += 1
            main.start(HASH)

    try:
        for step in range(steps):
            if rnd.random() < ask_rate:
                ask_for_one()
            if scenario == "main-restart" and step == 60:
                # Palladium restarts: its rows come back from the state file, qBittorrent
                # carries on as it was
                main.downloads = [dict(d) for d in main.downloads]
            copy_up = step not in away
            for m in ((main, copy) if copy_up else (main,)):
                as_machine(m)
                pd_torrents.only_asked(QBView(m), HASH)
            tick_swarm(main, 0.25)
            if copy_up:
                tick_swarm(copy, 0.25)
                here = [i for i in main.files if main.files[i]["progress"] >= 1 and i not in moved]
                if here:
                    i = here[0]
                    if step == half_at:
                        # restarted in the middle: half a file in its place, not written down
                        copy.disk[copy.path(i)] = main.files[i]["size"] // 2
                        half_at = -1
                    else:
                        copy.disk[copy.path(i)] = main.files[i]["size"]
                        copy.files[i]["whole_bytes"] = True
                        if scenario == "damaged" and damaged is None:
                            damaged = i
                            copy.files[i]["whole_bytes"] = False
                        copy.downloads.append({"hash": HASH, "index": i, "state": "done"})
                        moved.add(i)
            whole = [i for i in main.files if main.files[i]["progress"] >= 1 or i in moved]
            if whole:
                i = rnd.choice(whole)
                check("%s: a film being watched is on a disk" % tag,
                      main.path(i) in main.disk or copy.path(i) in copy.disk)
            check("%s: the copy fetches nothing from the swarm during the move" % tag, not copy.swarm)
            check("%s: the copy never reads during the move" % tag, copy.reads == 0)
            check("%s: the main server fetches only films asked for" % tag, set(main.swarm) <= asked)
            check("%s: the main server never reads its pack for a film asked for" % tag, main.reads == 0)
            if (not [i for i in asked if main.files[i]["progress"] < 1]
                    and moved == {i for i in main.files if main.files[i]["progress"] >= 1}
                    and step > steps // 2):
                break
        check("%s: every film asked for came in" % tag,
              all(main.files[i]["progress"] >= 1 for i in asked))
        check("%s: every film the main server holds was moved" % tag,
              moved == {i for i in main.files if main.files[i]["progress"] >= 1})
        # the read at the end, a request arriving in the middle of it on the main server
        reads_needed = 0
        while True:
            copy.priority(HASH, sorted(moved), 1)
            copy.state = "checkingDL"
            if scenario == "late-request" and reads_needed == 0:
                ask_for_one()
                for _ in range(8):
                    as_machine(main)
                    pd_torrents.only_asked(QBView(main), HASH)
                    tick_swarm(main, 0.25)
                check("%s: a film asked for during the read comes in on the main server" % tag,
                      all(main.files[i]["progress"] >= 1 for i in asked))
            copy.recheck(HASH)
            reads_needed += 1
            short = [i for i in moved if copy.files[i]["progress"] < 1]
            if not short:
                home = "copy"
                break
            check("%s: a film read short keeps the home where it is" % tag, home == "main")
            # sent again, then read again
            for i in short:
                copy.files[i]["whole_bytes"] = True
            if reads_needed > 2:
                break
        check("%s: the home changes only once everything reads whole" % tag,
              home == "copy" and all(copy.files[i]["progress"] >= 1 for i in moved))
        check("%s: reads: one, or two after a damaged film" % tag,
              reads_needed == (2 if scenario == "damaged" else 1))
        copy.start(HASH)
        tick_swarm(copy, 0.25)
        check("%s: seeding afterwards fetches nothing" % tag, not copy.swarm)
    finally:
        (pd_torrents.load, pd_torrents.asked_for, pd_torrents.kept_whole,
         os.path.exists, os.path.getsize) = saved
    return {"asked": len(asked), "moved": len(moved), "reads": reads_needed,
            "swarm_main": round(sum(main.swarm.values())), "swarm_copy": round(sum(copy.swarm.values())),
            "asked_bytes": len(asked) * 1000, "home": home}


SCENARIOS = ("plain", "busy", "copy-offline", "copy-restart", "main-restart", "damaged", "late-request")


if __name__ == "__main__":
    import json
    table = {}
    for scenario in SCENARIOS:
        for seed in range(12):
            r = evening(seed, scenario)
            t = table.setdefault(scenario, {"evenings": 0, "asked": 0, "moved": 0, "reads": [],
                                            "swarm_main": 0, "asked_bytes": 0, "swarm_copy": 0})
            t["evenings"] += 1
            for k in ("asked", "moved", "swarm_main", "asked_bytes", "swarm_copy"):
                t[k] += r[k]
            t["reads"].append(r["reads"])
    if "--json" in sys.argv:
        print(json.dumps(table))
    if FAILS:
        print("MOVE FAILED: %d" % len(set(FAILS)))
        for f in sorted(set(FAILS))[:20]:
            print("  -", f)
        sys.exit(1)
    print("MOVE PASSED: %d scenarios x 12 evenings" % len(SCENARIOS))
