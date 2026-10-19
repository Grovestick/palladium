"""What the tracker's box sets hold.

The tracker answers a search with release names, and a film that is only posted inside
a collection is found by no search for its title. So each box set in the tracker's index
has its .torrent read once - a few kilobytes, no film - and the films inside are kept
here; a search then finds the film inside it, and Download adds the box set as a pack
and fetches that one film.
"""
import json
import os
import re
import threading
import time

STATE = {"sets": None, "first": [], "next_at": 0.0, "scraped_at": 0.0, "word": 0,
         "unread": False, "dirty": False, "written": 0.0, "by": None}
LOCK = threading.Lock()

#: box sets read per minute: the torrent host is the one people download from
PER_MINUTE = 10
#: a box set whose torrent could not be fetched is tried again after this long
RETRY_S = 86400
#: words that turn up collections on the tracker, asked in turn, one per half hour
SCRAPE_WORDS = ("collection", "trilogy", "box set", "anthology", "quadrilogy",
                "duology", "saga", "film collection", "movie collection", "complete movies")
SCRAPE_EVERY = 1800
#: a release name that says it holds several films
_NAMED = re.compile(r"(?i)\b(collection|trilogy|quadrilogy|duology|anthology|box ?set|"
                    r"saga|complete (movies|films)|\d+ (movies|films))\b")


def _root():
    import pd_tracker
    return pd_tracker.STATE.get("root") or ""


def _path():
    return os.path.join(_root() or ".", "boxsets.json")


def _kept(ident):
    return os.path.join(_root() or ".", "boxsets",
                        "%s.torrent" % re.sub(r"[^0-9A-Za-z_-]", "", str(ident)))


def read():
    """Every box set read so far, by tracker id."""
    with LOCK:
        if STATE["sets"] is not None:
            return STATE["sets"]
    sets = None
    for _ in range(5):
        try:
            with open(_path(), encoding="utf-8") as f:
                sets = json.load(f) or {}
            break
        except FileNotFoundError:
            sets = {}
            break
        except (OSError, ValueError):
            time.sleep(0.2)
    with LOCK:
        if sets is None:
            STATE["unread"] = True      # unreadable is not empty: never written over
            sets = {}
        if STATE["sets"] is None:
            STATE["sets"] = sets
        return STATE["sets"]


def write(force=False):
    """Kept on disk once a minute while reading, not after every box set."""
    if STATE["unread"] or not _root() or not STATE["dirty"]:
        return
    if not force and time.time() - STATE["written"] < 60:
        return
    with LOCK:
        said = json.dumps(STATE["sets"] or {})
        STATE["dirty"] = False
        STATE["written"] = time.time()
    tmp = _path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(said)
        os.replace(tmp, _path())
    except OSError:
        pass


def is_boxset(row):
    """A release holding several films: the tracker's BoxSets shelf, or a name that says
    so - and not a season of a series."""
    import pd_tracker
    name = str(row.get("name") or "")
    if pd_tracker.episode_tag(name):
        return False
    return (str(row.get("kind") or "").lower() == "boxsets"
            or bool(_NAMED.search(name)))


def films_in(raw):
    """The films a torrent holds, the way a pack's are read: big enough to be a film,
    not a sample, not an episode."""
    import pd_torrents
    _, name, files = pd_torrents.parse_torrent(raw)
    out = []
    for one in files:
        low = one["path"].lower()
        if (not low.endswith(pd_torrents.VIDEO) or one["size"] < pd_torrents.FILM_BYTES
                or "sample" in low or pd_torrents.episode_of(one["path"], name)):
            continue
        title, year = pd_torrents.film_of(one["path"])
        if title:
            out.append({"title": title, "year": int(year or 0), "index": one["index"],
                        "path": one["path"], "size": one["size"]})
    return out


def first(ids):
    """Box sets to read before the rest: the ones a search has just turned up."""
    with LOCK:
        for ident in ids:
            if ident and str(ident) not in STATE["first"]:
                STATE["first"].insert(0, str(ident))
        del STATE["first"][40:]


def pending():
    """Whether box sets a search asked for are still to be read."""
    with LOCK:
        return bool(STATE["first"])


def waiting(rows, now=None):
    """Box sets in the tracker's index still to be read: those a search asked for, then
    the most carried."""
    now = now or time.time()
    sets = read()

    def due(ident):
        had = sets.get(ident)
        return had is None or (had.get("failed") and now - float(had.get("at") or 0) > RETRY_S)

    with LOCK:
        wanted = list(STATE["first"])
    out = [i for i in wanted if i in rows and due(i)]
    rest = sorted(((i, r) for i, r in rows.items() if i not in out and is_boxset(r) and due(i)),
                  key=lambda kv: -int(kv[1].get("seeds") or 0))
    return out + [i for i, _ in rest]


def read_one(ident, fetch=None, rows=None):
    """Read one box set's torrent and keep the films in it."""
    import pd_tracker
    fetch = fetch or pd_tracker.torrent_of
    row = (rows if rows is not None else pd_tracker.read()).get(str(ident)) or {}
    raw = fetch(str(ident))
    entry = {"name": row.get("name") or "", "seeds": int(row.get("seeds") or 0),
             "at": time.time(), "films": []}
    if raw:
        try:
            entry["films"] = films_in(raw)
        except Exception:
            entry["films"] = []
        if entry["films"] and _root():
            # kept, so Download adds it without asking the tracker again
            try:
                os.makedirs(os.path.dirname(_kept(ident)), exist_ok=True)
                with open(_kept(ident), "wb") as f:
                    f.write(raw)
            except OSError:
                pass
    else:
        entry["failed"] = True
    read()
    with LOCK:
        STATE["sets"][str(ident)] = entry
        if str(ident) in STATE["first"]:
            STATE["first"].remove(str(ident))
        STATE["dirty"] = True
        STATE["by"] = None
    return entry


def _flat(text):
    import pd_tracker
    return pd_tracker._flat(text)


def find(title, year=0):
    """Box sets holding this film: [(set id, set, film)], the most carried first."""
    want = _flat(title)
    if not want:
        return []
    sets = read()
    with LOCK:
        by = STATE["by"]
        if by is None:
            by = {}
            for ident, one in sets.items():
                for film in one.get("films") or []:
                    by.setdefault(_flat(film.get("title")), []).append((ident, one, film))
            STATE["by"] = by
    year = int(year or 0)
    out = [(i, one, film) for i, one, film in by.get(want, [])
           if not year or not film.get("year") or abs(int(film["year"]) - year) <= 1]
    out.sort(key=lambda t: -int(t[1].get("seeds") or 0))
    return out


def ask_about(lib, title, year, tmdb):
    """A film with no release of its own: the collection it belongs to and its director,
    asked of the tracker, and any box set that turns up read before the rest."""
    import pd_tracker
    names = []
    try:
        said = lib.tmdb("/movie/%d" % int(tmdb), timeout=8, append_to_response="credits")
    except Exception:
        said = {}
    whole = str((said.get("belongs_to_collection") or {}).get("name") or "")
    if whole:
        names.append(re.sub(r"(?i)\s*collection$", "", whole).strip() + " collection")
    makers = [c.get("name") for c in ((said.get("credits") or {}).get("crew") or [])
              if c.get("job") == "Director" and c.get("name")]
    if makers:
        names.append(makers[0] + " collection")
    found = []
    for words in names[:2]:
        try:
            found += pd_tracker.look_for(words, 40)
        except Exception:
            pass
    sets = [r.get("id") for r in found if is_boxset(r)]
    first(sets)
    return len(sets)


#: each season pack's files, by tracker id, read once
FILES = {}


def files_of(ident):
    """The files of one release on the tracker, from its torrent - kept, so Download adds
    it without asking again."""
    ident = str(ident)
    if ident in FILES:
        return FILES[ident]
    import pd_torrents
    import pd_tracker
    raw = None
    try:
        with open(_kept(ident), "rb") as f:
            raw = f.read()
    except OSError:
        raw = pd_tracker.torrent_of(ident)
        if raw and _root():
            try:
                os.makedirs(os.path.dirname(_kept(ident)), exist_ok=True)
                with open(_kept(ident), "wb") as f:
                    f.write(raw)
            except OSError:
                pass
    try:
        files = pd_torrents.parse_torrent(raw)[2] if raw else []
    except Exception:
        files = []
    if len(FILES) > 200:
        FILES.clear()
    FILES[ident] = files
    return files


def season_packs(rows, title, season, most=4):
    """Releases on the tracker holding a whole season of this programme - its own, or a
    run of seasons that takes it in - the most carried first."""
    import pd_tracker
    want = _flat(title)
    out = []
    for row in rows:
        name = str(row.get("name") or "")
        tag = pd_tracker.episode_tag(name)
        if not tag or _flat(tag[0]) != want or tag[2]:
            continue
        run = re.search(r"(?i)\bS(\d{1,2})\s*-\s*S?(\d{1,2})\b", name)
        if tag[1] == int(season) or (run and int(run.group(1)) <= int(season) <= int(run.group(2))):
            out.append(row)
    out.sort(key=lambda r: -int(r.get("seeds") or 0))
    return out[:most]


def episode_in_packs(title, season, number, skip=()):
    """One episode inside the tracker's season packs: [(id, pack, file)] as find() gives
    a film in a box set - so the version list offers it and Download takes that file.
    `skip` are release names already added here as packs."""
    import pd_torrents
    import pd_tracker
    import os as _os
    gone = {pd_tracker.release_flat(n) for n in skip}
    out = []
    for row in season_packs(list(pd_tracker.read().values()), title, season):
        if pd_tracker.release_flat(row.get("name")) in gone:
            continue
        for f in files_of(row.get("id")):
            told = pd_torrents.episode_of(f["path"], row.get("name") or "")
            if (told and int(told[2] or 0) == int(season) and int(told[3] or 0) == int(number)
                    and str(f["path"]).lower().endswith(pd_torrents.VIDEO)):
                out.append((str(row.get("id")), {"name": row.get("name") or "",
                                                 "seeds": int(row.get("seeds") or 0)},
                            {"index": f["index"], "path": f["path"], "size": f["size"],
                             "title": title}))
                break
    return out


def take(spec):
    """Add the box set "<id>:<file index>" names as a pack; the pack key of that film."""
    import pd_torrents
    import pd_tracker
    ident, _, index = str(spec).rpartition(":")
    raw = None
    try:
        with open(_kept(ident), "rb") as f:
            raw = f.read()
    except OSError:
        raw = pd_tracker.torrent_of(ident)
    if not raw:
        return {"ok": False, "why": "the tracker would not hand the box set over"}
    added = pd_torrents.add_pack(raw=raw)
    if not added.get("ok"):
        return added
    return {"ok": True, "key": pd_torrents.key_for(added["hash"], int(index))}


def step(now=None, rows=None, fetch=None, look=None):
    """One turn of the reader: a box set every six seconds, and a collection search every
    half hour. Only on the machine that asks the tracker. True when one was read."""
    import pd_tracker
    if rows is None and not pd_tracker.state().get("asks"):
        return False
    now = now or time.time()
    if now >= STATE["scraped_at"] + SCRAPE_EVERY:
        STATE["scraped_at"] = now
        word = SCRAPE_WORDS[STATE["word"] % len(SCRAPE_WORDS)]
        STATE["word"] += 1
        threading.Thread(target=pd_tracker.quietly(
            look or (lambda: pd_tracker.look_for(word, 100))), daemon=True).start()
    if now < STATE["next_at"]:
        return False
    if fetch is None and not pd_tracker.room("fetch"):
        return False                      # the minute's torrent files are spent
    rows = rows if rows is not None else pd_tracker.read()
    todo = waiting(rows, now)
    if not todo:
        write(force=True)
        return False
    STATE["next_at"] = now + 60.0 / PER_MINUTE
    pd_tracker.quietly(read_one)(todo[0], fetch=fetch, rows=rows)
    write()
    return True
