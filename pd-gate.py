#!/usr/bin/env python3
"""Run the server about to be installed against a copy of the live papers, cut off.

The smoke test starts from nothing and so never holds anything worth losing. 0.18.439
passed it and emptied the download queue on both machines at start-up. This starts the
source as it stands - its real main(), every worker - on a copy of the installed
server's state files, on a port of its own, and then compares the copy with what it
was given.

Cut off means: no connection but to itself and the film catalogue, which is only read;
no process started, no file written outside the copy. What it tried is listed, not failed on - the point is what it did to its own
papers with the world gone quiet.

    python pd-gate.py                 ninety seconds of running, then the verdict
    python pd-gate.py --seconds 300   longer, to cover the slower workers
    python pd-gate.py --keep          leave the copy to look at

Exit code 0 when nothing shrank, nothing answered 500 and nothing was written to the
fault log; 1 otherwise. Nothing is installed while this fails.
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(os.environ.get("APPDATA") or "", "Palladium")

#: What is asked of the running copy. A 500, or no answer, fails the gate.
GETS = [
    "/", "/config", "/where", "/server", "/settings", "/invites", "/machine",
    "/library/status", "/library/config", "/app/version", "/gpu/status", "/standby",
    "/torrents/active", "/torrents/state", "/tracker/state", "/watchlist", "/casual/peek",
    "/local/library/sections",
    "/local/library/sections/1/all",
    "/local/library/sections/2/all",
    "/local/library/sections/1/recentlyAdded",
    "/local/library/sections/2/recentlyReleased",
    "/local/library/onDeck",
    "/local/library/upcoming",
    "/local/library/popularShows",
    "/local/library/collections",
    "/local/library/streaming",
    "/local/library/seasonal",
    "/watch/services", "/watch/services?region=SE", "/skipstart",
    "/local/hubs/search?query=the",
]


#: Hosts the cut-off server may still read from.
READ_ONLY = {"api.themoviedb.org"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- child

def cut_off(root, port):
    """In the child, before the server is imported: nothing leaves the copy."""
    import builtins
    import io
    root_n = os.path.normcase(os.path.abspath(root))
    code_n = os.path.normcase(HERE)
    seen = set()
    out = open(os.path.join(root, "gate-blocked.log"), "a", buffering=1, encoding="utf-8")

    def note(kind, what):
        if (kind, what) not in seen:
            seen.add((kind, what))
            out.write(kind + "\t" + what + "\n")

    def inside(path):
        try:
            p = os.path.normcase(os.path.abspath(os.fspath(path)))
        except Exception:
            return True
        return (p == root_n or p.startswith(root_n + os.sep) or p == os.devnull
                or "__pycache__" in p or p in ("nul", "\\\\.\\nul"))

    def guard(path, how):
        if not inside(path):
            note("write", how + " " + str(path))
            raise PermissionError("gate: write outside the copy: %s" % (path,))

    # ---- the network: itself, and nobody else
    # the catalogue is read, never written: without it no card for an episode still
    # to come is made, and what those cards wear went unchecked
    reading = set()

    def ours(addr):
        try:
            if str(addr[0]) in reading and int(addr[1]) == 443:
                return True
            return str(addr[0]) in ("127.0.0.1", "localhost", "::1") and int(addr[1]) == port
        except Exception:
            return False

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, addr):
        if ours(addr):
            return real_connect(self, addr)
        note("net", "%s:%s" % (addr[0], addr[1]) if isinstance(addr, tuple) else str(addr))
        raise ConnectionRefusedError("gate: no network")

    def connect_ex(self, addr):
        if ours(addr):
            return real_connect_ex(self, addr)
        note("net", "%s:%s" % (addr[0], addr[1]) if isinstance(addr, tuple) else str(addr))
        return 10061

    def sendto(self, data, *rest):
        addr = rest[-1]
        if ours(addr):
            return real_sendto(self, data, *rest)
        note("net", "udp %s:%s" % (addr[0], addr[1]) if isinstance(addr, tuple) else str(addr))
        raise OSError("gate: no network")

    def getaddrinfo(host, *a, **k):
        h = str(host or "")
        if h in ("", "localhost", "0.0.0.0", "::", "::1") or h.replace(".", "").isdigit():
            return real_getaddrinfo(host, *a, **k)
        if h in READ_ONLY:
            found = real_getaddrinfo(host, *a, **k)
            reading.update(str(f[4][0]) for f in found)
            return found
        note("net", "lookup " + h)
        raise socket.gaierror(11001, "gate: no network")

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo

    # ---- processes: none
    def no_process(self, args, *a, **k):
        said = args if isinstance(args, str) else " ".join(str(x) for x in args)
        note("proc", said[:160])
        raise PermissionError("gate: no processes")

    subprocess.Popen.__init__ = no_process
    if hasattr(os, "startfile"):
        os.startfile = lambda *a, **k: note("proc", "startfile %s" % (a[:1],))
    os.system = lambda c: note("proc", "system " + str(c)[:160]) or 1

    # ---- files: only in the copy
    real_open = builtins.open

    def opened(file, mode="r", *a, **k):
        if not isinstance(file, int) and any(c in str(mode) for c in "wax+"):
            guard(file, "open(%s)" % mode)
        return real_open(file, mode, *a, **k)

    builtins.open = opened
    io.open = opened

    real_os_open = os.open

    def os_opened(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            guard(path, "os.open")
        return real_os_open(path, flags, *a, **k)

    os.open = os_opened

    def one(mod, name, which=(0,)):
        real = getattr(mod, name)

        def wrapped(*a, **k):
            for i in which:
                if i < len(a):
                    guard(a[i], mod.__name__ + "." + name)
            for key in ("dst", "path", "src"):
                if key in k:
                    guard(k[key], mod.__name__ + "." + name)
            return real(*a, **k)

        setattr(mod, name, wrapped)

    for name in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "truncate", "removedirs"):
        one(os, name)
    for name in ("rename", "replace", "link", "symlink"):
        one(os, name, (0, 1))
    one(shutil, "rmtree")
    one(shutil, "move", (0, 1))
    for name in ("copy", "copy2", "copyfile", "copytree"):
        one(shutil, name, (1,))


def child(root, port):
    os.environ["TMP"] = os.environ["TEMP"] = os.path.join(root, "tmp")
    os.makedirs(os.environ["TMP"], exist_ok=True)
    tempfile.tempdir = os.environ["TMP"]
    cut_off(root, port)
    import runpy
    sys.argv = [os.path.join(HERE, "pd-server.py"), "--root", root, "--port", str(port),
                "--no-open", "--no-tray"]
    sys.path.insert(0, HERE)
    runpy.run_path(sys.argv[0], run_name="__main__")


# -------------------------------------------------------------------------- parent

def take_copy(root):
    """The live state files, and the index by sqlite's own backup so it is whole."""
    names = []
    for name in sorted(os.listdir(LIVE)):
        src = os.path.join(LIVE, name)
        if name.endswith(".json") and os.path.isfile(src):
            shutil.copy2(src, os.path.join(root, name))
            names.append(name)
    for db in ("library.db",):
        src = os.path.join(LIVE, db)
        if os.path.exists(src):
            a = sqlite3.connect("file:%s?mode=ro" % src.replace("\\", "/"), uri=True)
            b = sqlite3.connect(os.path.join(root, db))
            a.backup(b)
            a.close(); b.close()
            names.append(db)
    return names


def measure(root):
    """What each paper holds, as numbers that must not fall."""
    m = {}
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not (name.endswith(".json") and os.path.isfile(p)):
            continue
        row = {"bytes": os.path.getsize(p)}
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            row["top"] = len(d) if isinstance(d, (dict, list)) else 1
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, (dict, list)):
                        row["." + str(k)] = len(v)
        except Exception as e:
            row["unreadable"] = "%s: %s" % (type(e).__name__, e)
        m[name] = row
    db = os.path.join(root, "library.db")
    if os.path.exists(db):
        row = {"bytes": os.path.getsize(db)}
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                row["." + t] = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            con.close()
        except Exception as e:
            row["unreadable"] = "%s: %s" % (type(e).__name__, e)
        m["library.db"] = row
    return m


#: Numbers allowed to fall: what the server prunes by design, or re-derives.
MAY_FALL = {
    ("settings.json", "bytes"), ("tracker.json", "bytes"), ("torrents.json", "bytes"),
    ("library.db", "bytes"), ("library.db", ".sqlite_stat1"),
    ("streaming.json", "bytes"), ("top_shows.json", "bytes"),
    ("traffic.json", "bytes"), ("meters.json", "bytes"), ("ahead.json", "bytes"),
    ("sittings.json", "bytes"), ("notified.json", "bytes"),
}


#: Papers whose entries go out of date by themselves: what a sitting took is kept a
#: few hours and then dropped. They must still be there and readable; how many
#: entries they hold is theirs to say.
EXPIRES = {"sittings.json", "subcheck_asked.json"}


def compare(before, after):
    bad, rows = [], []
    for name, was in before.items():
        now = after.get(name)
        if now is None:
            bad.append("%s is gone" % name)
            continue
        if "unreadable" in now:
            bad.append("%s can no longer be read: %s" % (name, now["unreadable"]))
            continue
        if name in EXPIRES:
            rows.append((name, ["%s %s -> %s" % (k, v, now.get(k)) for k, v in was.items()
                                if isinstance(v, int) and now.get(k) != v]))
            continue
        changed = []
        for k, v in was.items():
            if k == "unreadable" or not isinstance(v, int):
                continue
            n = now.get(k)
            if n is None:
                if k != "bytes":
                    bad.append("%s lost %s (%d)" % (name, k, v))
                continue
            if n != v:
                changed.append("%s %d -> %d" % (k, v, n))
            # a tenth is a prune; more is a file that started again from nothing
            fell = n < v if (name, k) not in MAY_FALL else n < v * 0.5
            if k == "bytes" and (name, k) not in MAY_FALL:
                fell = n < v * 0.9
            if fell:
                bad.append("%s %s fell from %d to %d" % (name, k, v, n))
        rows.append((name, changed))
    return bad, rows


def ask(url, token="", timeout=40):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    if token:
        req.add_header("X-Palladium-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, ("%s: %s" % (type(e).__name__, e)).encode()


def tell(url, token, body, timeout=40):
    """POST json; (status, parsed answer or {})."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Palladium-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception as e:
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def cards(base, path, token):
    code, body = ask(base + path, token)
    if code != 200:
        return code, []
    try:
        d = json.loads(body)
        mc = d.get("MediaContainer", d)
        return code, mc.get("Metadata") or mc.get("Hub") or []
    except Exception:
        return code, []


def walk(base, token):
    """Every listed address, then down into one series, one season and one film."""
    bad, n = [], 0
    for path in GETS:
        code, body = ask(base + path, token)
        n += 1
        if code == 0 or code >= 500:
            bad.append("%s answered %s %s" % (path, code, body[:160]))
    # a series, its seasons, a season's episodes, an episode; a film
    _, shows = cards(base, "/local/library/sections/2/all", token)
    _, films = cards(base, "/local/library/sections/1/all", token)
    for one in shows[:3] + films[:3]:
        key = one.get("ratingKey")
        for path in ("/local/library/metadata/%s" % key,
                     "/local/library/metadata/%s/children" % key,
                     "/local/library/metadata/%s/nextUp" % key):
            if one.get("type") != "show" and not path.endswith(str(key)):
                continue
            code, body = ask(base + path, token)
            n += 1
            if code == 0 or code >= 500:
                bad.append("%s answered %s %s" % (path, code, body[:160]))
    for show in shows[:2]:
        _, seasons = cards(base, "/local/library/metadata/%s/children" % show.get("ratingKey"), token)
        for season in seasons[:2]:
            code, eps = cards(base, "/local/library/metadata/%s/children" % season.get("ratingKey"), token)
            n += 1
            if code == 0 or code >= 500:
                bad.append("season %s answered %s" % (season.get("ratingKey"), code))
            for ep in eps[:1]:
                code, body = ask(base + "/local/library/metadata/%s" % ep.get("ratingKey"), token)
                n += 1
                if code == 0 or code >= 500:
                    bad.append("episode %s answered %s" % (ep.get("ratingKey"), code))
    # what this build promises of the cards themselves: every episode on a shelf - held,
    # or the next one still to come - wears its season's poster
    eps = []
    for shelf in ("/local/library/sections/2/recentlyReleased", "/local/library/upcoming",
                  "/local/library/onDeck"):
        _, listed = cards(base, shelf, token)
        these = [c for c in listed if c.get("type") == "episode" and c.get("grandparentRatingKey")]
        eps += these
        for c in these:
            want = "/art/%s/season/%d" % (c.get("grandparentRatingKey"),
                                          int(c.get("parentIndex") or 0))
            if c.get("grandparentThumb") and c.get("parentThumb") != want:
                bad.append("%s: episode %s wears %s, not its season's poster"
                           % (shelf, c.get("ratingKey"), c.get("parentThumb")))
                break
    # the services to choose among under Settings: countries, and one country's own
    code, body = ask(base + "/watch/services?region=SE", token)
    try:
        said = json.loads(body) if code == 200 else {}
    except ValueError:
        said = {}
    print("gate: %d countries, %d services in the one asked for"
          % (len(said.get("regions") or []), len(said.get("services") or [])))
    if code == 200 and not (len(said.get("regions") or []) > 20
                            and all(s.get("name") for s in said.get("services") or [])
                            and len(said.get("services") or []) > 5):
        bad.append("the list of streaming services to choose among came back short")
    # skip rules page: every programme with held episodes, each with its seasons
    code, body = ask(base + "/skipstart", token)
    try:
        said = json.loads(body) if code == 200 else {}
    except ValueError:
        said = {}
    print("gate: %d programmes a skip rule can be set for, %d rules"
          % (len(said.get("shows") or []), len(said.get("rules") or [])))
    if code == 200 and not (said.get("shows") and all(s.get("key") and s.get("seasons")
                                                      for s in said["shows"])
                            and isinstance(said.get("rules"), list)
                            and isinstance(said.get("listening"), dict)):
        bad.append("the skip rules page came back short")
    # a rule set over HTTP reaches that season's episode cards, and is put back after
    for one in (said.get("shows") or [])[:1]:
        show, season = one["key"], one["seasons"][0]
        had = [r["seconds"] for r in said.get("rules") or []
               if r["show"] == show and r["season"] == season and not r["episode"]]
        rule = {"show": show, "season": season, "episode": 0}
        code, got = tell(base + "/skipstart", token, dict(rule, seconds=7.5))
        if code != 200 or not [r for r in got.get("rules") or []
                               if r["show"] == show and r["season"] == season
                               and r["seconds"] == 7.5 and r.get("title")]:
            bad.append("a skip rule was not taken: %s %s" % (code, str(got)[:160]))
        _, eps = cards(base, "/local/library/metadata/%s-s%d/children" % (show, season), token)
        held = [c for c in eps if not c.get("offered")]
        if not held or any(c.get("skipStart") != 7.5 for c in held):
            bad.append("a skip rule set did not reach its season's %d episode cards" % len(held))
        code, got = tell(base + "/skipstart", token, dict(rule, seconds=had[0] if had else 0))
        left = [r["seconds"] for r in got.get("rules") or []
                if r["show"] == show and r["season"] == season and not r["episode"]]
        if code != 200 or left != had:
            bad.append("a skip rule was not put back: %s %s, was %s" % (code, left, had))
        if tell(base + "/skipstart", token, {"show": "nosuchshow", "season": 1, "seconds": 5})[0] != 404:
            bad.append("a skip rule for a programme that is not held was taken")
        if tell(base + "/skipstart", token, dict(rule, seconds=9999))[0] != 400:
            bad.append("a skip rule of 9999 seconds was taken")
    # Now playing carries every worker with a state the monitor's boxes know
    code, body = ask(base + "/watching", token)
    try:
        said = json.loads(body) if code == 200 else {}
    except ValueError:
        said = {}
    workers = said.get("workers") or []
    print("gate: %d workers reported, %d working"
          % (len(workers), sum(1 for w in workers if w.get("state") == "working")))
    if len(workers) < 8 or any(w.get("state") not in ("off", "waiting", "working", "stalled")
                               or not w.get("name") for w in workers):
        bad.append("the workers came back short or with a state the page does not know")
    # what a copy reads by GET is routed under GET: refused without its key, never a 404
    for path in ("/follow/leads", "/follow/fits", "/follow/loudness", "/follow/endings?since=0"):
        code, _ = ask(base + path, token)
        n += 1
        if code not in (200, 403):
            bad.append("%s asked by GET answered %s" % (path, code))
    # the season's shelf: named on every card, one card a film, in its order
    _, season = cards(base, "/local/library/seasonal", token)
    print("gate: %d films on the season's shelf" % len(season))
    if season:
        keys = [c.get("ratingKey") for c in season]
        if len(set(keys)) != len(keys):
            bad.append("the season's shelf holds a film twice")
        if any(c.get("shelf") != "Christmas" for c in season):
            bad.append("a card on the season's shelf does not say which shelf it is")
        ranks = [int(c.get("seasonRank") or 0) for c in season]
        if ranks != sorted(ranks, reverse=True):
            bad.append("the season's shelf is not in its order")
    for c in shows:
        if c.get("latestKey") and c.get("thumb") and not c.get("latestThumb"):
            bad.append("series %s has a newest episode and no poster for its season"
                       % c.get("ratingKey"))
            break
    print("gate: %d episode cards on the shelves, %d of them still to come"
          % (len(eps), len([c for c in eps if c.get("upcoming")])))
    return bad, n, len(shows), len(films), len(eps)


def main():
    if "--child" in sys.argv:
        i = sys.argv.index("--child")
        return child(sys.argv[i + 1], int(sys.argv[i + 2]))
    seconds = 90
    if "--seconds" in sys.argv:
        seconds = int(sys.argv[sys.argv.index("--seconds") + 1])
    if not os.path.isdir(LIVE):
        print("no installed server's papers at", LIVE)
        return 1
    root = tempfile.mkdtemp(prefix="pd-gate-")
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    failed = []
    print("gate: copy of %s in %s, port %d" % (LIVE, root, port))
    take_copy(root)
    # the copy is given a holiday, so the season's shelf has something to answer with
    try:
        with open(os.path.join(root, "settings.json"), encoding="utf-8") as f:
            kept = json.load(f)
        kept["seasonal"] = "christmas"
        with open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(kept, f)
    except Exception:
        pass
    before = measure(root)
    token = ""
    try:
        with open(os.path.join(root, "settings.json"), encoding="utf-8") as f:
            token = str(json.load(f).get("ownerIs") or "")
    except Exception:
        pass
    log = open(os.path.join(root, "gate-out.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--child", root, str(port)],
                            cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
    try:
        began = time.time()
        up = False
        while time.time() - began < 90 and proc.poll() is None:
            if ask(base + "/config", timeout=3)[0] == 200:
                up = True
                break
            time.sleep(0.5)
        if not up:
            failed.append("the server did not answer within 90 s (exit %s)" % proc.poll())
        else:
            print("gate: answering after %.1f s" % (time.time() - began))
            bad, n, shows, films, eps = walk(base, token)
            failed += bad
            print("gate: %d addresses asked; %d series, %d films, %d released episodes read"
                  % (n, shows, films, eps))
            if not shows or not films:
                failed.append("the library came back empty: %d series, %d films" % (shows, films))
            left = seconds - (time.time() - began)
            if left > 0:
                print("gate: running %d s more for the workers" % left)
                time.sleep(left)
            # asked again at the end: still the same server
            bad, n, shows2, films2, _ = walk(base, token)
            failed += bad
            if (shows2, films2) != (shows, films):
                failed.append("the library changed while it ran: %d/%d series, %d/%d films"
                              % (shows, shows2, films, films2))
            if proc.poll() is not None:
                failed.append("the server stopped by itself (exit %s)" % proc.poll())
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(15)
            except Exception:
                proc.kill()
        log.close()
    # give the last writes a moment to land, then read what is there
    time.sleep(1)
    after = measure(root)
    bad, rows = compare(before, after)
    failed += bad
    print("gate: state files, before -> after")
    for name, changed in rows:
        print("   %-22s %s" % (name, "; ".join(changed) if changed else "unchanged"))

    def tail(name, n=40):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()][-n:]

    # the line every start writes is not a fault
    faults = [l for l in tail("fault.log", 400)
              if l.strip() and not l.startswith("---- started")]
    # the sandbox's own refusals are not the server's faults
    traces = [l for l in tail("gate-out.log", 4000) + tail("debug.log", 4000)
              if "Traceback" in l or "Error" in l]
    ours = [l for l in traces if "gate:" not in l and "ConnectionRefused" not in l
            and "gaierror" not in l]
    if faults:
        failed.append("fault.log was written to:\n      " + "\n      ".join(faults[-12:]))
    if any("Traceback" in l for l in tail("gate-out.log", 4000)):
        failed.append("a traceback on the console:\n      "
                      + "\n      ".join(tail("gate-out.log", 30)))
    blocked = tail("gate-blocked.log", 400)
    kinds = {}
    for l in blocked:
        kinds.setdefault(l.split("\t")[0], []).append(l.split("\t", 1)[-1])
    for kind, what in sorted(kinds.items()):
        print("gate: held back %d %s: %s" % (len(what), kind, "; ".join(what[:6])
                                             + (" ..." if len(what) > 6 else "")))
    writes = kinds.get("write", [])
    if writes:
        failed.append("tried to write outside its own papers: " + "; ".join(writes[:8]))
    if ours:
        print("gate: errors in its log (%d), last:" % len(ours))
        for l in ours[-8:]:
            print("   " + l[:220])
    if "--keep" in sys.argv or failed:
        print("gate: copy kept at", root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    if failed:
        print("GATE FAILED")
        for f in failed:
            print(" - " + f)
        return 1
    print("GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
