"""What the tracker is carrying, so a film can be fetched rather than only asked for.

The list of new films comes from elsewhere; this says which of them there is actually
something to download for. It is read from the tracker's own feeds - the thing it
publishes for automation - rather than from its search pages, which want a session and
break when the site is restyled.

Both machines keep their own copy. The feeds are the same on either, so whichever is
up can answer, and neither waits on the other.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

#: the newest hundred, and everything from the last day. The first is cheap and
#: catches a burst; the second is what fills the index in one pass after a restart.
FEEDS = ("https://rss.torrentleech.org/%s",
         "https://rss24h.torrentleech.org/%s")
#: how often to look. The day feed carries some six hundred rows, so nothing is
#: missed at this interval even when the tracker is busy.
EVERY = 20 * 60
#: how long a release stays in the index. A film that was uploaded three months ago
#: is still downloadable; the cap is about the file not growing without end.
KEEP_DAYS = 120
KEEP = 20000

#: the tracker's own search, which the feeds are not. A feed carries the newest
#: hundred and the last day; everything older than that is only findable by asking.
#: Asking needs the session a person is signed in with - the login form has a captcha
#: on it - so the cookie is kept here rather than a password.
SEARCH = "https://www.torrentleech.org/torrents/browse/list/query/%s"
#: how many of those to make in a day, spread across it. Nothing here is urgent and a
#: tracker watches how often it is asked: this is one every three quarters of an hour,
#: slower than a person reading the site, and it stops when there is nothing left to
#: ask about.
ASK_A_DAY = 30
#: what each category id is called, for the few worth carrying. The rest are games,
#: books and music, which this house is not looking for.
KINDS = {8: "Cam", 9: "TS", 11: "DVDRip", 37: "WEBRip", 43: "HDRip", 14: "BlurayRip",
         12: "DVD-R", 13: "Bluray", 47: "4K", 15: "BoxSets", 29: "Documentaries",
         26: "Episodes", 32: "Episodes HD", 27: "BoxSets", 34: "Anime",
         35: "Cartoons", 36: "Foreign", 44: "TV Foreign"}

LOCK = threading.Lock()
STATE = {"at": 0.0, "rows": {}, "root": "", "key": "", "asked": {}, "why": "",
         "searched": 0.0, "asks": True, "config": None}

ITEM = re.compile(r"<item>(.*?)</item>", re.S)
TITLE = re.compile(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", re.S)
LINK = re.compile(r"<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>", re.S)
IDENT = re.compile(r"/download/(\d+)/")
#: what kind of release it is, and how many are carrying it - the two things worth
#: knowing when there is more than one of a film to choose from
CATEGORY = re.compile(r"<category>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</category>", re.S)
SEEDS = re.compile(r"Seeders:\s*(\d+)")
#: the feed key, as it appears in an address the torrent client holds
KEY_IN = re.compile(r"torrentleech\.org/([0-9a-f]{12,})")
#: a year in a release name, which is where the title stops
YEAR = re.compile(r"[.\s(\[](19\d\d|20\d\d)[.\s)\]]")


def use_data_dir(root):
    STATE["root"] = root or ""


def _path():
    return os.path.join(STATE.get("root") or ".", "tracker.json")


def key_for_downloads():
    """The feed key, which is what fetches a torrent whichever way it was found."""
    return key()


def key():
    """The feed key: what this house already uses to subscribe.

    Taken from the settings where somebody has put one, and otherwise from the
    torrent client's own feed list - it is already subscribed on both machines, so
    there is nothing to type in twice.
    """
    with LOCK:
        got = STATE.get("key")
    if got:
        return got
    mine = ""
    try:
        import pd_torrents
        data = pd_torrents.load()
        said = (data.get("config") or {})
        mine = str(said.get("trackerKey") or "")
        if not mine:
            # whatever this machine is already subscribed to in the torrent client:
            # the key is in the feed address, and both machines have it there
            body = pd_torrents.QB(said)._call("/api/v2/rss/items", timeout=10)
            found = KEY_IN.search((body or b"").decode("utf-8", "replace"))
            if found:
                mine = found.group(1)
    except Exception:
        mine = ""
    if not mine:
        # straight at the client on this machine. The call above goes through the
        # login the packs use, and a client that trusts its own machine - which is
        # how both of these are set up - needs none of it.
        for where in ("http://127.0.0.1:8080", "http://localhost:8080"):
            try:
                body = _fetch(where + "/api/v2/rss/items?withData=false")
            except Exception:
                continue
            found = KEY_IN.search(body)
            if found:
                mine = found.group(1)
                break
    with LOCK:
        # only a real one is kept. The client is often still starting when this
        # machine is, and remembering that it had no answer meant never asking again.
        if mine:
            STATE["key"] = mine
    return mine


#: where the indexer that holds its own login lives, and the address it answers on
JACKETT = "http://127.0.0.1:9117"


def jackett():
    """The local indexer's address and key, where one is set up.

    This is the way in that does not expire: it keeps the login itself and signs in
    again whenever the site logs it out, so nothing here has to hold a session at
    all. The cookie below stays as the way in when there is no such indexer.
    """
    try:
        import pd_torrents
        said = pd_torrents.load().get("config") or {}
    except Exception:
        said = {}
    where = str(said.get("jackettUrl") or JACKETT).rstrip("/")
    key = str(said.get("jackettKey") or "")
    if not key:
        # what the indexer wrote down about itself when it was installed
        for path in (os.path.join(os.environ.get("PROGRAMDATA", ""), "Jackett",
                                  "ServerConfig.json"),):
            try:
                with open(path, encoding="utf-8") as f:
                    key = str((json.load(f) or {}).get("APIKey") or "")
            except (OSError, ValueError):
                key = key
            if key:
                break
    return (where, key) if key else ("", "")


def ask_jackett(words, kind="search"):
    """Search through the local indexer. Rows in the same shape the feeds give."""
    where, key = jackett()
    if not (where and words):
        return []
    url = ("%s/api/v2.0/indexers/torrentleech/results/torznab/api"
           "?apikey=%s&t=%s&q=%s" % (where, key, kind, urllib.parse.quote(str(words))))
    try:
        ask = urllib.request.Request(url, headers={"User-Agent": "Palladium"})
        with urllib.request.urlopen(ask, timeout=60) as answer:
            body = answer.read().decode("utf-8", "replace")
    except Exception as why:
        with LOCK:
            STATE["why"] = "the indexer did not answer: %s" % str(why)[:90]
        return []
    if "<error" in body:
        got = re.search(r'description="([^"]*)', body)
        with LOCK:
            STATE["why"] = "the indexer said: %s" % (got.group(1) if got else "no")[:90]
        return []
    # everything already known, before anything is added to it. Writing without
    # having read puts only what this answer carried into the file and throws the
    # rest of the index away - which is exactly what it did the first time.
    read()
    with LOCK:
        STATE["why"] = ""
    mine = key_for_downloads()
    now = time.time()
    learned = []
    for block in re.findall(r"<item>(.*?)</item>", body, re.S):
        name = re.search(r"<title>(.*?)</title>", block, re.S)
        ident = re.search(r"/(?:download|torrent)/(\d+)", block)
        if not (name and ident):
            continue
        name = re.sub(r"&amp;", "&", name.group(1)).strip()
        size = re.search(r"<size>(\d+)</size>", block)
        seeds = re.search(r'name="seeders" value="(\d+)"', block)
        imdb = re.search(r'name="imdb(?:id)?" value="(tt)?(\d+)"', block)
        title, year = bare(name)
        if not title:
            continue
        got = {"id": ident.group(1), "name": name, "title": title, "year": year,
               "kind": "", "seeds": int(seeds.group(1)) if seeds else 0,
               "size": int(size.group(1)) if size else 0,
               "imdb": ("tt" + imdb.group(2)) if imdb else "",
               "url": "https://www.torrentleech.org/rss/download/%s/%s/%s.torrent"
                      % (ident.group(1), mine, re.sub(r"[^A-Za-z0-9.]+", ".", name)),
               "at": now}
        with LOCK:
            had = STATE["rows"].get(got["id"])
            if had:
                had.update({k: v for k, v in got.items() if k not in ("at", "via")})
            else:
                STATE["rows"][got["id"]] = dict(got, via="search")
        learned.append(got)
    if learned:
        write()
    with LOCK:
        STATE["searched"] = now
    return learned


def cookie():
    """The signed-in session to search with, as the owner last gave it."""
    try:
        import pd_torrents
        return str((pd_torrents.load().get("config") or {}).get("trackerCookie") or "")
    except Exception:
        return ""


def keep_cookie(said):
    """Remember a session. Returns what it is worth: a search is tried with it."""
    said = " ".join(str(said or "").split())
    if not said:
        return {"ok": False, "why": "nothing given"}
    try:
        import pd_torrents
        data = pd_torrents.load()
        data.setdefault("config", {})["trackerCookie"] = said
        pd_torrents.save()
    except Exception as why:
        return {"ok": False, "why": str(why)[:120]}
    got = search("the matrix", 1999)
    return {"ok": bool(got), "found": len(got),
            "why": STATE.get("why") or ("" if got else "the tracker did not answer")}


def nobody_else():
    """Whether the machine that does the asking is away.

    Only then does this one touch the tracker: two machines keeping one session warm
    is twice the asking for no gain, and the point is the hours when the other is off.
    """
    try:
        import pd_follow
        said = STATE.get("config")
        one = pd_follow.settings(said() if said else {})
        where = str((one or {}).get("master") or "")
    except Exception:
        where = ""
    if not where:
        return False
    try:
        urllib.request.urlopen(where.rstrip("/") + "/build", timeout=6).read(200)
        return False                      # it is up: leave the session to it
    except Exception:
        return True


def keep_warm():
    """Touch the tracker so the session does not idle out between searches.

    A session lives on the last request made with it, and a site's idea of idle is
    usually under half an hour - shorter than the gap between searches, so the session
    died between one and the next and somebody had to sign in and paste a new one in.
    One page read every refresh is far less than a person browsing, and keeps it.
    """
    if HELD["on"]:
        return None
    said = cookie()
    if not said:
        return False
    try:
        ask = urllib.request.Request(
            "https://www.torrentleech.org/torrents/browse",
            headers={"User-Agent": "Mozilla/5.0", "Cookie": said})
        with urllib.request.urlopen(ask, timeout=30) as answer:
            body = answer.read(4000).decode("utf-8", "replace")
    except Exception:
        return False                      # the tracker being away is not the session
    gone = "Login ::" in body or "login-form" in body
    with LOCK:
        STATE["why"] = ("the session has expired - sign in again and hand it over"
                        if gone else "")
    return not gone


def look_for(words, most=40):
    """Ask the tracker for anything, by whatever somebody typed.

    The daily asking is about the films on the new list; this is the other half - a
    person looking for a series, a box set, something the catalogue has never heard
    of. What comes back is folded into the index like any other row, so the version
    list and the fetch work on it afterwards without a second lookup.
    """
    got = search(str(words or ""), 0)
    if got:
        return got[:most]
    # nothing matched the title as a film: the rows are still learned, and what was
    # asked for is what to hand back
    rows = read()
    want = _flat(words)
    if not want:
        return []
    out = [r for r in list(rows.values()) if want in _flat(r.get("name"))]
    out.sort(key=lambda r: (int(r.get("seeds") or 0), float(r.get("at") or 0)),
             reverse=True)
    return out[:most]


def search(title, year=0):
    """Ask the tracker what it has for one film. Returns the rows learned.

    This is what the feeds cannot do: they carry only what was posted today, and a
    film released last month is findable only by asking for it by name.

    Through the local indexer where there is one - it keeps its own login and signs
    in again by itself, so nothing expires - and otherwise with the session somebody
    signed in with by hand.
    """
    if HELD["on"]:
        return []
    # the year goes into the search as well as the filtering: "F1" alone brings back a
    # hundred race broadcasts and one release of the film; "F1 2025" brings sixteen
    words = ("%s %d" % (str(title or "").strip(), int(year)) if year else str(title or ""))
    through = ask_jackett(words)
    if through:
        return through
    if jackett()[1]:
        return []                         # the indexer answered, and it has nothing
    said = cookie()
    if not said:
        with LOCK:
            STATE["why"] = "no session: sign in and hand the browser's cookie over"
        return []
    want = urllib.parse.quote(words.strip())
    if not want:
        return []
    try:
        ask = urllib.request.Request(
            SEARCH % want,
            headers={"User-Agent": "Mozilla/5.0", "Cookie": said,
                     "Accept": "application/json"})
        with urllib.request.urlopen(ask, timeout=40) as answer:
            body = answer.read().decode("utf-8", "replace")
    except Exception as why:
        with LOCK:
            STATE["why"] = str(why)[:140]
        return []
    if not body.lstrip().startswith("{"):
        # the login page: the session has run out, and saying so is the only way
        # anybody learns to hand over a new one
        with LOCK:
            STATE["why"] = "the session has expired - sign in again and hand it over"
        return []
    with LOCK:
        STATE["why"] = ""
    try:
        found = json.loads(body).get("torrentList") or []
    except ValueError:
        return []
    mine = key()
    now = time.time()
    learned = []
    for row in found:
        ident = str(row.get("fid") or "")
        name = str(row.get("name") or "").strip()
        filename = str(row.get("filename") or "")
        if not (ident and name and filename):
            continue
        its_title, its_year = bare(name)
        if not its_title:
            continue
        got = {"id": ident, "name": name, "title": its_title,
               "year": its_year or int(year or 0),
               "kind": KINDS.get(int(row.get("categoryID") or 0), ""),
               "seeds": int(row.get("seeders") or 0),
               "size": int(row.get("size") or 0),
               "imdb": str(row.get("imdbID") or ""),
               # fetched with the feed key, the same as everything else here: the
               # session is for asking, not for taking
               "url": "https://www.torrentleech.org/rss/download/%s/%s/%s"
                      % (ident, mine, filename),
               "at": now}
        with LOCK:
            had = STATE["rows"].get(ident)
            if had:
                # what was already known, brought up to date: a row from the feeds
                # has no size and its seeders are from whenever it was posted
                had.update({k: v for k, v in got.items() if k not in ("at", "via")})
            else:
                STATE["rows"][ident] = dict(got, via="search")
        learned.append(got)
    if learned:
        write()
    with LOCK:
        STATE["searched"] = now
    return learned


def _fetch(url):
    ask = urllib.request.Request(url, headers={"User-Agent": "Palladium"})
    with urllib.request.urlopen(ask, timeout=30) as answer:
        return answer.read().decode("utf-8", "replace")


def bare(name):
    """A release name cut back to the title, and the year if it carries one."""
    said = re.sub(r"[_]+", " ", str(name or "")).strip()
    got = YEAR.search(said)
    year = int(got.group(1)) if got else 0
    if got:
        said = said[:got.start()]
    said = re.sub(r"[.]+", " ", said)
    return said.strip(" -.[]()"), year


#: set while this machine is not to use the tracker - the two servers on different
#: connections, the account in two places at once
HELD = {"on": False}


def refresh(force=False):
    """Read the feeds and fold what they carry into the index. Returns how many are new."""
    if HELD["on"]:
        return 0
    with LOCK:
        if not force and time.time() - STATE["at"] < EVERY:
            return 0
        STATE["at"] = time.time()
    mine = key()
    if not mine:
        return 0
    read()
    fresh = 0
    now = time.time()
    for shape in FEEDS:
        try:
            body = _fetch(shape % mine)
        except Exception:
            continue
        for block in ITEM.findall(body):
            name = TITLE.search(block)
            link = LINK.search(block)
            if not (name and link):
                continue
            ident = IDENT.search(link.group(1))
            if not ident:
                continue
            got = ident.group(1)
            with LOCK:
                if got in STATE["rows"]:
                    # a row from before this knew what to keep: filled in rather than
                    # left blank until somebody uploads it again
                    had = STATE["rows"][got]
                    if not had.get("kind") or had.get("seeds") is None:
                        kind = CATEGORY.search(block)
                        seeds = SEEDS.search(block)
                        if kind:
                            had["kind"] = kind.group(1).strip()
                        had["seeds"] = int(seeds.group(1)) if seeds else 0
                        fresh += 1
                    continue
                title, year = bare(name.group(1))
                if not title:
                    continue
                kind = CATEGORY.search(block)
                seeds = SEEDS.search(block)
                STATE["rows"][got] = {"id": got, "name": name.group(1).strip(),
                                      "title": title, "year": year,
                                      "kind": kind.group(1).strip() if kind else "",
                                      "seeds": int(seeds.group(1)) if seeds else 0,
                                      "url": link.group(1).strip(), "at": now,
                                      "via": "rss"}
                fresh += 1
    if fresh:
        write()
    return fresh


def read():
    """The index as it stands, loading it the first time."""
    with LOCK:
        if STATE["rows"]:
            return STATE["rows"]
    # A file that is there and cannot be read is asked again, and never written over:
    # taken for an empty index, the next write replaced it and the record of who took
    # what went with it.
    said = None
    for attempt in range(5):
        try:
            with open(_path(), encoding="utf-8") as f:
                said = json.load(f) or {}
            break
        except FileNotFoundError:
            said = {}
            break
        except (OSError, ValueError):
            time.sleep(0.2)
    if said is None:
        STATE["unread"] = True
        said = {}
    rows = said.get("rows") if isinstance(said, dict) else None
    asked = said.get("asked") if isinstance(said, dict) else None
    taken = said.get("taken") if isinstance(said, dict) else None
    log = said.get("log") if isinstance(said, dict) else None
    with LOCK:
        STATE["rows"] = rows if isinstance(rows, dict) else {}
        STATE["asked"] = asked if isinstance(asked, dict) else {}
        STATE["taken"] = taken if isinstance(taken, dict) else {}
        STATE["log"] = log if isinstance(log, list) else []
        return STATE["rows"]


def write():
    """Keep the index, oldest rows dropped."""
    with LOCK:
        rows = dict(STATE["rows"])
    cut = time.time() - KEEP_DAYS * 86400
    kept = {k: v for k, v in rows.items() if float(v.get("at") or 0) >= cut}
    if len(kept) > KEEP:
        newest = sorted(kept.values(), key=lambda r: float(r.get("at") or 0),
                        reverse=True)[:KEEP]
        kept = {r["id"]: r for r in newest}
    with LOCK:
        STATE["rows"] = kept
    where = _path()
    tmp = where + ".tmp"
    if not kept:
        return                            # nothing learned yet: leave what is there
    if STATE.get("unread") or not STATE.get("root"):
        return                            # the file could not be read: not written over
    # one copy a day of what is about to be replaced, seven kept
    try:
        if os.path.exists(where) and os.path.getsize(where) > 2:
            today = where + "." + time.strftime("%Y%m%d") + ".bak"
            if not os.path.exists(today):
                import shutil
                shutil.copy2(where, today)
                folder = os.path.dirname(where)
                old = sorted(n for n in os.listdir(folder)
                             if n.startswith(os.path.basename(where) + ".") and n.endswith(".bak"))
                for name in old[:-7]:
                    os.remove(os.path.join(folder, name))
    except OSError:
        pass
    # written out whole while nothing can change it: a row given its size by a take
    # while this ran ended it with "dictionary changed size during iteration"
    with LOCK:
        text = json.dumps({"at": time.time(), "rows": kept,
                           "asked": dict(STATE.get("asked") or {}),
                           "taken": dict(STATE.get("taken") or {}),
                           # who took what, kept past the download itself
                           "log": list(STATE.get("log") or [])})
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, where)
    except OSError:
        pass


def _bendecode(raw, at=0) -> tuple:
    """Enough bencode to read a torrent's own file list. Returns (value, next index)."""
    ch = raw[at:at + 1]
    if ch == b"i":
        end = raw.index(b"e", at)
        return int(raw[at + 1:end]), end + 1
    if ch == b"l":
        out, at = [], at + 1
        while raw[at:at + 1] != b"e":
            one, at = _bendecode(raw, at)
            out.append(one)
        return out, at + 1
    if ch == b"d":
        out, at = {}, at + 1
        while raw[at:at + 1] != b"e":
            name, at = _bendecode(raw, at)
            one, at = _bendecode(raw, at)
            out[name] = one
        return out, at + 1
    colon = raw.index(b":", at)
    size = int(raw[at:colon])
    return raw[colon + 1:colon + 1 + size], colon + 1 + size


def info_hash(raw):
    """What the torrent client will call this torrent: sha1 of its info dict.

    Taken from the bytes as they arrived rather than by re-encoding what was read -
    a dict written back out in another order is another hash, and the point of this
    is to name the same download the client names.
    """
    import hashlib
    try:
        at = raw.index(b"4:info") + len(b"4:info")
        end = _bendecode(raw, at)[1]
        return hashlib.sha1(raw[at:end]).hexdigest()
    except Exception:
        return ""


def size_of(raw):
    """How many bytes a release is, from the torrent itself: the feed does not say.

    One file or many - a season pack is the sum of what is in it.
    """
    try:
        said = _bendecode(raw)[0]
        info = said.get(b"info") or {}
        if b"length" in info:
            return int(info[b"length"])
        return sum(int(f.get(b"length") or 0) for f in (info.get(b"files") or []))
    except Exception:
        return 0


def measure(ident):
    """The size of one release, fetched once and kept with it."""
    row = read().get(str(ident))
    if not row:
        return 0
    if row.get("size"):
        return int(row["size"])
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(row["url"], headers={"User-Agent": "Palladium"}),
            timeout=30).read()
    except Exception:
        return 0
    size = size_of(raw)
    if size:
        with LOCK:
            had = STATE["rows"].get(str(ident))
            if had is not None:
                had["size"] = size
        write()
    return size


def measure_many(ids, at_once=3, gap=0.5, budget=5.0):
    """Sizes for several releases, a few at a time rather than all at once.

    A version list can carry twenty-five releases nobody has sized yet, and asking
    the tracker for all of them in the same instant is what a robot looks like. Three
    at a time with half a second between, which is about five a second at its
    busiest, and the list itself waits no longer than the budget: whatever is left
    unsized is read the next time somebody opens it. A size is kept once it is known,
    so this happens once per release and never again.
    """
    left = [str(i) for i in ids if i]
    if not left:
        return 0
    until = time.time() + float(budget)
    done = [0]
    pick = threading.Lock()

    def work():
        while time.time() < until:
            with pick:
                if not left:
                    return
                ident = left.pop(0)
            if measure(ident):
                with pick:
                    done[0] += 1
            time.sleep(gap)

    hands = [threading.Thread(target=work, daemon=True)
             for _ in range(max(1, int(at_once)))]
    for t in hands:
        t.start()
    for t in hands:
        t.join(max(0.1, until - time.time()))
    return done[0]


def _flat(said):
    return "".join(c for c in str(said or "").lower() if c.isalnum())


#: the index by name and by IMDb number, rebuilt when the list changes or once a minute
FOUND = {"stamp": None, "at": 0.0, "title": {}, "imdb": {}}


def _by():
    """The list looked up by name and number, built once rather than scanned per ask.

    Every film that can only be asked for was looked for here as the film shelf was
    drawn: ninety-odd scans of ten thousand rows, a second and a half for one page.
    """
    rows = read()
    stamp = (id(rows), len(rows))
    if FOUND["stamp"] != stamp or time.time() - FOUND["at"] > 60:
        by_title, by_imdb = {}, {}
        for row in list(rows.values()):
            by_title.setdefault(_flat(row.get("title")), []).append(row)
            number = re.sub(r"[^0-9]", "", str(row.get("imdb") or ""))
            if number:
                by_imdb.setdefault(number, []).append(row)
        FOUND.update(stamp=stamp, at=time.time(), title=by_title, imdb=by_imdb)
    return FOUND


def find(title, year=0, imdb=""):
    """What the tracker has for that film, newest first. Empty when it has nothing.

    The year decides where two films share a name, and a release that carries no year
    is allowed where the name is an exact match.

    A number beats both. Releases are named by whoever made them, and the catalogue
    calls a film whatever its own source calls it; where the two agree on an IMDb
    number they are the same film whatever either of them is called, and where they
    do not the title is all there is to go on.
    """
    number = re.sub(r"[^0-9]", "", str(imdb or ""))
    found = _by()
    if number:
        said = list(found["imdb"].get(number) or [])
        if said:
            said.sort(key=lambda r: (int(r.get("seeds") or 0), float(r.get("at") or 0)),
                      reverse=True)
            return said
    want = _flat(title)
    if not want:
        return []
    year = int(year or 0)
    out = []
    for row in found["title"].get(want) or []:
        theirs = int(row.get("year") or 0)
        if year and theirs and abs(theirs - year) > 1:
            continue
        out.append(row)
    # the most carried first, and the newest among equals: what somebody picking a
    # version wants at the top is the one that will actually arrive
    out.sort(key=lambda r: (int(r.get("seeds") or 0), float(r.get("at") or 0)),
             reverse=True)
    return out


def episodes_of(title, season):
    """The index's single-episode releases of one season, by episode number.

    Whatever the catalogue says about air dates: an episode that turns up before its
    date is on the tracker all the same, and that is when somebody wants it.
    """
    want = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
    if not want:
        return {}
    tag = re.compile(r"(?i)(?:^|[ ._\-\[])S0*%d[ ._]?E0*(\d{1,3})(?=[ ._\-\]]|$)" % int(season))
    out = {}
    for row in list(read().values()):
        name = str(row.get("name") or "")
        m = tag.search(name)
        if not m:
            continue
        # the programme's name, before the episode tag, and nothing else in front of it
        head = re.sub(r"[^a-z0-9]+", "", name[:m.start()].lower())
        head = re.sub(r"(19|20)\d\d$", "", head)
        if head != want:
            continue
        out.setdefault(int(m.group(1)), []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (int(r.get("seeds") or 0), float(r.get("at") or 0)),
                  reverse=True)
    return out


def torrent_of(ident):
    """The torrent file for one release in the index, or nothing."""
    row = read().get(str(ident))
    if not row:
        return None
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(row["url"], headers={"User-Agent": "Palladium"}),
            timeout=60).read()
    except Exception:
        return None
    return raw if (raw.startswith(b"d8:announce")
                   or b"announce" in raw[:200]) else None


def take(ident="", title="", year=0, most_gb=0.0, who="", auto=False, reason=""):
    """Hand one release to the torrent client. Returns what happened.

    By its number where the caller has one, and otherwise the newest release for that
    film. Added stopped, like everything else the packs fetch, so nothing starts
    moving until it is meant to.

    A size beyond what this asker may start is refused: the torrent says how big it
    is, and it is already in hand by then, so nothing is fetched twice to find out.
    """
    rows = read()
    row = rows.get(str(ident)) if ident else None
    if row is None:
        got = find(title, year)
        row = got[0] if got else None
    if row is None:
        return {"error": "nothing on the tracker for that one"}
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(row["url"], headers={"User-Agent": "Palladium"}),
            timeout=60).read()
    except Exception as why:
        return {"error": "the tracker would not hand it over: %s" % str(why)[:120]}
    if not raw.startswith(b"d8:announce") and b"announce" not in raw[:200]:
        return {"error": "that was not a torrent file"}
    size = size_of(raw)
    if size:
        with LOCK:
            had = STATE["rows"].get(str(row.get("id")))
            if had is not None and not had.get("size"):
                had["size"] = size
    if most_gb and size and size / 1073741824.0 > float(most_gb):
        return {"error": "That one is %.1f GB, and %.0f GB is as much as anybody but "
                         "the owner may fetch at once."
                         % (size / 1073741824.0, float(most_gb))}
    mark = info_hash(raw)
    try:
        import pd_torrents
        where = pd_torrents.save_folder()
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        # One the client already holds is not fetched again. Handing a torrent it
        # already has to qBittorrent answers with an HTTP error, and that is what the
        # screen showed - a number, about a film that is simply already here.
        if mark and qb.info(mark) is not None:
            return {"error": "that one is on disk already"}
        qb.add(raw, where, hold=False)
    except Exception as why:
        return {"error": str(why)[:160]}
    # written down so it can be stopped again: a download nobody can cancel is a
    # disk filling up with something somebody pressed by mistake
    with LOCK:
        if mark:
            STATE.setdefault("taken", {})[mark] = {
                "id": str(row.get("id") or ""), "name": row.get("name"),
                "size": size, "at": time.time()}
        # and who asked for it, kept after the download is gone: a release taken
        # straight from the tracker left no name behind
        log = STATE.setdefault("log", [])
        log.append({"at": int(time.time()), "who": who or "", "name": row.get("name") or "",
                    "size": int(size or 0), "auto": bool(auto),
                    # why it started, and how the index came to have the release
                    "reason": str(reason or "")[:200], "via": row.get("via") or ""})
        del log[:-2000]
    write()
    return {"taken": True, "name": row.get("name"), "where": where, "hash": mark}


#: how long before a film is worth asking about again. One that the tracker had
#: nothing for may have something next week; one that already has releases may have
#: better ones. Long enough that the round trip through the list is slow.
AGAIN_AFTER = 7 * 86400
#: and a new release the tracker has nothing for yet, daily: it is the one most likely
#: to turn up, and eleven of them are eleven of the day's thirty searches
AGAIN_EMPTY = 86400


def _wanted():
    """Films worth asking about, the most worth asking first.

    Two tiers. Everything with nothing to fetch yet comes first, newest release at the
    front: that is what the list is short of. Then everything else, longest since it
    was asked about - because the feeds carry only the last day, and a machine that was
    off for a weekend has a hole in its list that nothing else will ever fill.
    """
    try:
        import pd_streaming
        rows = pd_streaming.read() or []
    except Exception:
        return []
    read()                                # what has been asked already, from the file
    now = time.time()
    with LOCK:
        asked = dict(STATE.get("asked") or {})
    empty, known = [], []
    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        year = int(row.get("year") or 0)
        mark = "%s|%d" % (title.lower(), year)
        when = float(asked.get(mark) or 0)
        have = bool(find(title, year))
        if now - when < (AGAIN_AFTER if have else AGAIN_EMPTY):
            continue
        one = (str(row.get("released") or ""), title, year, mark)
        if have:
            known.append((when, one))
        else:
            empty.append(one)
    empty.sort(reverse=True)              # newest release first
    known.sort()                          # longest since it was asked about first
    return empty + [one for _, one in known]


def ask_about_one():
    """One search, for the newest film nothing is known about. Returns what it learned."""
    if HELD["on"]:
        return 0
    want = _wanted()
    if not want:
        return 0
    _, title, year, mark = want[0]
    got = search(title, year)
    with LOCK:
        STATE.setdefault("asked", {})[mark] = time.time()
    write()
    return len(got)


def ask_for(title, year=0, again_after=90.0):
    """Search for one film now, unless it was asked about a moment ago.

    For films nobody is on the list for - anything already in the library. What it
    learns is kept in the index like everything else, and asked again the next time
    somebody opens that list, so a release that came out since is there. The short
    guard is only against the same open asking twice.
    """
    if HELD["on"]:
        return []
    read()
    mark = "%s|%d" % (str(title or "").strip().lower(), int(year or 0))
    now = time.time()
    with LOCK:
        when = float((STATE.get("asked") or {}).get(mark) or 0)
        # and how many have gone out in the last minute, whatever film they were
        # about: somebody walking down a shelf opens a list per film, and each open
        # is a search. Ten a minute is a person browsing; a hundred is a robot, and
        # the tracker is entitled to think so.
        lately = [t for t in (STATE.get("lately") or []) if now - t < 60.0]
        room = len(lately) < 10
        if room:
            lately.append(now)
        STATE["lately"] = lately
    if time.time() - when < again_after or not room:
        return []
    got = search(title, year)
    with LOCK:
        STATE.setdefault("asked", {})[mark] = time.time()
    write()
    return got


def ask_about(many=1, gap=5.0):
    """A run of searches, one after another. Returns how many films were asked about."""
    done = 0
    for n in range(max(1, int(many))):
        if n:
            time.sleep(gap)               # the tracker watches how often it is asked
        if not _wanted():
            break
        ask_about_one()
        done += 1
    return done


def room_left():
    """Gigabytes free where downloads land, or None when it cannot be read."""
    try:
        import pd_torrents
        return pd_torrents.free_gb(pd_torrents.save_folder())
    except Exception:
        return None


def state():
    """What the index is and how the asking is going, for a page that shows it.

    `swept` against `films` is how far round the list the asking has got, and
    `matched` is what it is for: how many of them there is anything to fetch for.
    """
    read()
    with LOCK:
        rows = len(STATE["rows"])
        asked = len(STATE.get("asked") or {})
        why = STATE.get("why") or ""
        searched = float(STATE.get("searched") or 0)
    left = len(_wanted())
    films = matched = 0
    try:
        import pd_streaming
        for row in pd_streaming.read() or []:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            films += 1
            if find(title, int(row.get("year") or 0)):
                matched += 1
    except Exception:
        pass
    return {"releases": rows, "asked": asked, "why": why,
            "asks": bool(STATE.get("asks", True)), "free": room_left(),
            "searched": int(searched), "left": left,
            "films": films, "matched": matched, "swept": max(0, films - left),
            "session": bool(cookie()) and not why,
            "aDay": int(round(86400.0 / how_often()))}


def how_often():
    """Seconds between searches, from the number a day this house allows."""
    try:
        import pd_torrents
        said = int((pd_torrents.load().get("config") or {}).get("trackerAskADay")
                   or ASK_A_DAY)
    except Exception:
        said = ASK_A_DAY
    return 86400.0 / max(1, min(500, said))


def taking():
    """What this house is fetching from the tracker now, as the client sees it."""
    index = read() or {}
    with LOCK:
        mine = dict(STATE.get("taken") or {})
    if not mine:
        return []
    try:
        import pd_torrents
        qb = pd_torrents.QB(pd_torrents.load()["config"])
    except Exception:
        return []
    out, forget = [], []
    now = time.time()
    for mark, said in mine.items():
        try:
            held = qb.info(mark)
        except Exception:
            held = None
        if held is None:
            continue                      # gone from the client: nothing to show
        done = float(held.get("progress") or 0) >= 1.0
        # One that finished is the library's now, not a download. It is shown for a
        # few minutes so whoever started it sees it land, and then it drops off the
        # list - otherwise everything ever fetched sits there for ever.
        if done and now - float(said.get("at") or 0) > 600:
            forget.append(mark)
            continue
        # which film it is, so a film's page shows its own download and no other
        row = index.get(str(said.get("id"))) or {}
        out.append({"hash": mark, "id": said.get("id"), "name": said.get("name"),
                    "title": row.get("title") or "", "year": int(row.get("year") or 0),
                    "size": int(held.get("size") or said.get("size") or 0),
                    "state": str(held.get("state") or ""),
                    "progress": float(held.get("progress") or 0),
                    "done": done,
                    "eta": int(held.get("eta") or -1),
                    "mbit": round(float(held.get("dlspeed") or 0) * 8 / 1e6, 1)})
    if forget:
        with LOCK:
            for mark in forget:
                (STATE.get("taken") or {}).pop(mark, None)
        write()
    return out


def drop(mark):
    """Stop one and take what it has written with it."""
    mark = str(mark or "")
    if not mark:
        return {"ok": False, "why": "nothing said"}
    try:
        import pd_torrents
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        held = qb.info(mark)
        # A finished one is not a download any more: cancelling it would delete the
        # film off the disk, which is not what the word says on the button.
        if held is not None and float(held.get("progress") or 0) >= 1.0:
            with LOCK:
                (STATE.get("taken") or {}).pop(mark, None)
            write()
            return {"ok": False, "why": "that one has finished: it is on the disk now"}
        qb.remove(mark, files=True)
    except Exception as why:
        return {"ok": False, "why": str(why)[:140]}
    with LOCK:
        (STATE.get("taken") or {}).pop(mark, None)
    write()
    return {"ok": True}


#: whether the owner lets the daily asking run; a callable, or None for always
ALLOWED = {"ask": None}


def start(root, may_ask=True, config=None):
    """Keep the index fresh in the background, on whichever machine this is.

    Both machines read the feeds, so either can answer when the other is off. Only one
    of them asks: a search signs in as a person, and the same session used from two
    machines at once is both twice the traffic and the shape of a shared account. The
    machine that keeps copies reads and does not ask.
    """
    use_data_dir(root)
    with LOCK:
        STATE["asks"] = bool(may_ask)
        STATE["config"] = config
    read()

    import pd_beat

    def round_and_round():
        asked_at = 0.0
        while True:
            pd_beat.beat("search", "reading feeds", 1800)
            try:
                refresh()
            except Exception:
                pass
            # and one question of its own, now and then: the feeds only carry what was
            # posted today, so everything older is found by asking for it by name
            try:
                if cookie() and (may_ask or nobody_else()):
                    # Kept alive on every round, asked with now and then: the first is
                    # what makes the second still work an hour later.
                    #
                    # The copy keeps it warm as well, but only while the machine that
                    # does the asking is off - which is the one time it stops being
                    # kept alive at all. A session signed in by hand and then lost to
                    # a night's sleep is a person fetching a browser cookie every
                    # morning; one page read every twenty minutes from whichever
                    # machine is awake costs the tracker nothing.
                    keep_warm()
                    if may_ask and time.time() - asked_at > how_often() \
                            and (ALLOWED["ask"] is None or ALLOWED["ask"]()):
                        asked_at = time.time()
                        ask_about_one()
            except Exception:
                pass
            pd_beat.sleep("search", EVERY)

    threading.Thread(target=round_and_round, daemon=True).start()
