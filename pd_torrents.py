"""Films offered from torrent packs, fetched one at a time through qBittorrent.

A pack's films are listed in the library before they are here - greyed, with their
page - and a viewer who asks for one gets that film and nothing else from the pack:
every other file in it is left at "do not download".

State is one JSON file beside the library: the qBittorrent connection, the packs with
what each film was matched to, and every download with who asked for it and when.
"""
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LOCK = threading.RLock()
STATE = {"root": "", "data": None, "lib": None, "scan": None, "worker": False,
         "owned": None, "owned_at": 0.0, "scan_wanted": False, "why": "",
         # the films on offer, worked out again at most every few seconds: every
         # collection asks, and a page of shelves asks once per shelf
         "offered": None, "offered_at": 0.0, "free": None}
VIDEO = (".mkv", ".mp4", ".m4v", ".avi")
#: smaller than this is a sample or an extra, not the film
FILM_BYTES = 300 * 1000 * 1000
#: The same floor for an episode would throw most of a series away. Three hundred
#: megabytes is a sensible smallest film and a large half-hour episode: a 1080p x265
#: episode runs to about two hundred, and one programme came in as 55 files of 140
#: and another as 2 of 180. Low enough to keep a short episode, high enough that the
#: extras and the artwork in a pack are still left alone.
EPISODE_BYTES = 40 * 1000 * 1000
#: libtorrent's piece picker counts at most this many 16 KiB blocks to a piece: just under
#: 256 MiB on libtorrent 1.2, just under 512 MiB on 2.0. A pack cut into larger pieces is
#: refused by that qBittorrent whatever is sent.
BLOCK = 16 * 1024
PIECE_BLOCKS = {1: (1 << 14) - 1, 2: (1 << 15) - 1}
QUALITY = re.compile(r"^(2160p|1080p|720p|576p|480p|bluray|brrip|bdrip|web|web-dl|"
                     r"webrip|hdtv|dvdrip|remux|uhd|x264|x265|h264|h265|hevc)$", re.I)


# ---------------------------------------------------------------- state on disk

def start(root, lib_of, scan):
    """Where the state lives, how to reach the library, and how to ask for a scan."""
    STATE["root"] = root
    STATE["lib"] = lib_of
    STATE["scan"] = scan
    load()
    os.makedirs(drop_folder(), exist_ok=True)
    ensure_worker()


#: where somebody puts a torrent file for it to become a pack. Beside the library
#: rather than beside the program: the program folder is replaced by every update.
DROP = "pack"


def drop_folder():
    return os.path.join(STATE["root"] or ".", DROP)


def read_the_folder():
    """Take in any torrent file somebody has put in the folder.

    One direction only. A file appearing is somebody asking for the pack; a file
    disappearing is not somebody asking for it to go, because that is also what a
    half-finished copy, a synced folder catching up, or a drive that has not woken
    looks like - and a pack leaving takes its offers off every shelf. Removing one is
    done in Settings, which deletes the file as well so it cannot walk back in.
    """
    folder = drop_folder()
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".torrent"))
    except OSError:
        return 0
    known = {str(p.get("dropped") or "") for p in load()["packs"]}
    took = 0
    for name in names:
        if name in known:
            continue
        try:
            said = add_pack(path=os.path.join(folder, name))
        except (OSError, ValueError):
            continue
        if not said.get("ok"):
            continue
        # written down under the file it came in as, so the folder is read once per
        # file however often it is looked at
        with LOCK:
            for p in load()["packs"]:
                if p.get("hash") == said.get("hash"):
                    p["dropped"] = name
            save()
        took += 1
    return took


def _path():
    return os.path.join(STATE["root"], "torrents.json")


def load():
    with LOCK:
        if STATE["data"] is None:
            try:
                with io.open(_path(), encoding="utf-8") as f:
                    STATE["data"] = json.load(f)
            except (OSError, ValueError):
                STATE["data"] = {}
            data = STATE["data"]
            data.setdefault("config", {})
            data.setdefault("packs", [])
            data.setdefault("downloads", [])
        return STATE["data"]


def save():
    with LOCK:
        STATE["offered_at"] = 0.0
        STATE["unfinished"] = None
        if not STATE["root"]:
            return
        tmp = _path() + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE["data"], f)
        os.replace(tmp, _path())


# ---------------------------------------------------------------- reading a .torrent

def _dec(b, i):
    c = b[i:i + 1]
    if c == b"i":
        j = b.index(b"e", i)
        return int(b[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while b[i:i + 1] != b"e":
            v, i = _dec(b, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while b[i:i + 1] != b"e":
            k, i = _dec(b, i)
            v, i = _dec(b, i)
            out[k] = v
        return out, i + 1
    j = b.index(b":", i)
    n = int(b[i:j])
    return b[j + 1:j + 1 + n], j + 1 + n


def parse_torrent(data):
    """(info hash, name, files) - files in the order qBittorrent numbers them."""
    if data[:1] != b"d":
        raise ValueError("not a torrent file")
    i, top, span = 1, {}, None
    while data[i:i + 1] != b"e":
        k, i = _dec(data, i)
        begin = i
        v, i = _dec(data, i)
        top[k] = v
        if k == b"info":
            span = (begin, i)
    if not span:
        raise ValueError("a torrent file with no info in it")
    info = top[b"info"]
    name = (info.get(b"name.utf-8") or info.get(b"name") or b"").decode("utf-8", "replace")
    files = []
    if b"files" in info:
        for n, f in enumerate(info[b"files"]):
            parts = f.get(b"path.utf-8") or f.get(b"path") or []
            files.append({"index": n,
                          "path": "/".join(p.decode("utf-8", "replace") for p in parts),
                          "size": int(f.get(b"length") or 0)})
    else:
        files.append({"index": 0, "path": name, "size": int(info.get(b"length") or 0)})
    return hashlib.sha1(data[span[0]:span[1]]).hexdigest(), name, files


def film_of(path):
    """(title, year) from a film's folder or file name: the last year before the
    quality words, so a title that is itself a year keeps it."""
    parts = path.split("/")
    for candidate in ([parts[-2]] if len(parts) > 1 else []) + [os.path.splitext(parts[-1])[0]]:
        tokens = [t for t in re.split(r"[._ ()\[\]]+", candidate) if t]
        stop = next((n for n, t in enumerate(tokens) if QUALITY.match(t)), len(tokens))
        years = [n for n in range(1, stop) if re.fullmatch(r"(19|20)\d{2}", tokens[n])]
        if years:
            at = years[-1]
            return " ".join(tokens[:at]), int(tokens[at])
    base = os.path.splitext(parts[-1])[0]
    return " ".join(t for t in re.split(r"[._ ]+", base) if t), 0


#: SxxExx anywhere in a name, bounded by a non-digit, so a resolution or a year is
#: not read as an episode number.
EPISODE_AT = re.compile(r"(?:^|[^A-Za-z0-9])[sS](\d{1,2})[eE](\d{1,3})(?![0-9])")


def episode_of(path):
    """(show, year, season, number) for a file naming an episode, else None.

    Pack entries were parsed by film_of(), which drops SxxExx with the quality words:
    all 34 files of a TV pack came out as one title and matched no film in TMDB.
    """
    parts = str(path or "").replace("\\", "/").split("/")
    base = os.path.splitext(parts[-1])[0]
    found = EPISODE_AT.search(base)
    before = base[:found.start()] if found else ""
    if not found:
        return None

    def name_and_year(text):
        words = [w for w in re.split(r"[._ \-()\[\]]+", text) if w]
        # drop a trailing season marker: "Show.Name.2019.S01" -> "Show Name", 2019
        while words:
            if (len(words) > 1 and re.fullmatch(r"\d{1,2}", words[-1])
                    and words[-2].lower() == "season"):
                words = words[:-2]           # "Season 3", written out
            elif (re.fullmatch(r"[sS]\d{1,2}", words[-1])
                    or words[-1].lower() in ("season", "complete")):
                words = words[:-1]
            else:
                break
        year = 0
        if words and re.fullmatch(r"(19|20)\d{2}", words[-1]):
            year = int(words[-1])
            words = words[:-1]
        return " ".join(words).strip(" -"), year

    show, year = name_and_year(before)
    if not show and len(parts) > 1:
        # no show name before SxxExx: take it from the parent folder
        folder = EPISODE_AT.split(parts[-2])[0]
        show, year = name_and_year(folder)
    if not show:
        return None
    return show, year, int(found.group(1)), int(found.group(2))


def piece_bytes(raw):
    """The piece size a .torrent is cut into, in bytes."""
    try:
        top, _ = _dec(raw, 0)
        return int((top.get(b"info") or {}).get(b"piece length") or 0)
    except (ValueError, IndexError, AttributeError):
        return 0


def client_limit():
    """(largest piece in bytes, libtorrent version) of the qBittorrent connected, or
    (0, "") when it cannot be asked - then qBittorrent itself decides."""
    was = STATE.get("limit")
    if was and time.time() - was[0] < 60:
        return was[1], was[2]
    try:
        info = json.loads(QB(load()["config"])._call("/api/v2/app/buildInfo", timeout=6) or b"{}")
        lt = str(info.get("libtorrent") or "")
        major = int(lt.split(".")[0]) if lt[:1].isdigit() else 0
        blocks = PIECE_BLOCKS.get(major) or (PIECE_BLOCKS[2] if major > 2 else 0)
        limit = blocks * BLOCK
    except Exception:
        lt, limit = "", 0
    STATE["limit"] = (time.time(), limit, lt)
    return limit, lt


def refused(pack):
    """Why the qBittorrent connected cannot take this pack, or nothing. Worked out against
    the client as it is now: a qBittorrent on a newer libtorrent takes larger pieces, and a
    pack refused under the old one is not refused for ever."""
    if "pieceBytes" not in pack:
        try:
            with open(pack["file"], "rb") as f:
                pack["pieceBytes"] = piece_bytes(f.read())
        except OSError:
            pack["pieceBytes"] = 0
    pack.pop("pieceMiB", None)
    limit, lt = client_limit()
    size = int(pack.get("pieceBytes") or 0)
    if limit and size > limit:
        return ("its pieces are %d MiB, and this qBittorrent (libtorrent %s) takes pieces of at "
                "most %.2f MiB. The qBittorrent build on libtorrent 2.0 takes up to %.2f MiB."
                % (size // 1048576, lt, limit / 1048576.0, PIECE_BLOCKS[2] * BLOCK / 1048576.0))
    said = pack.get("refused") or ""
    # the size rule is worked out above, never kept; a refusal qBittorrent itself gave
    # stands only under the libtorrent it gave it with
    if said.startswith("its pieces are") or (said and pack.get("refusedBy", "") != lt):
        pack.pop("refused", None)
        pack.pop("refusedBy", None)
        return ""
    return said


def key_for(info_hash, index):
    """A key no library title can have: "o" and twelve hex."""
    return "o" + hashlib.sha1(("%s:%d" % (info_hash, index)).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- packs

def add_pack(raw=None, path=None):
    if path:
        with open(path, "rb") as f:
            raw = f.read()
    if not raw:
        return {"ok": False, "why": "No torrent file was given"}
    try:
        info_hash, name, files = parse_torrent(raw)
    except (ValueError, IndexError) as e:
        return {"ok": False, "why": "That is not a torrent file: %s" % e}
    data = load()
    with LOCK:
        if any(p.get("hash") == info_hash for p in data["packs"]):
            return {"ok": True, "already": True, "hash": info_hash}
        folder = os.path.join(STATE["root"], "torrents")
        os.makedirs(folder, exist_ok=True)
        kept = os.path.join(folder, info_hash + ".torrent")
        with open(kept, "wb") as f:
            f.write(raw)
        films = []
        for one in files:
            low = one["path"].lower()
            # a file that names a season and an episode is judged as an episode
            floor = EPISODE_BYTES if episode_of(one["path"]) else FILM_BYTES
            if not low.endswith(VIDEO) or one["size"] < floor or "sample" in low:
                continue
            title, year = film_of(one["path"])
            entry = {"index": one["index"], "path": one["path"], "size": one["size"],
                     "title": title, "year": year,
                     "key": key_for(info_hash, one["index"]), "tmdb": None}
            # episode entries are matched against TMDB TV, not movies
            told = episode_of(one["path"])
            if told:
                show, made, season, number = told
                entry.update({"title": show, "year": made or year, "kind": "episode",
                              "season": season, "episode": number})
            films.append(entry)
        pack = {"hash": info_hash, "name": name, "file": kept,
                "added": int(time.time()), "films": films}
        refused(pack)
        data["packs"].append(pack)
        save()
    ensure_worker()
    return {"ok": True, "hash": info_hash, "name": name, "films": len(films),
            "refused": refused(pack)}


#: bumped when the rule for what counts as a file worth offering changes, so packs
#: already added are read again once against the new one
REFILL = 1


def refill_packs():
    """Read the packs already added against today's rule for what counts.

    Judging every file by a film's smallest size threw most of a series away - one
    programme came in as 55 files of 140, another as 2 of 180 - and a pack cannot be
    added twice, so lowering the floor would have done nothing for the packs already
    here. Their torrent files are kept beside the library, so they are simply read
    again and whatever was missed is added.
    """
    data = load()
    if int(data.get("refilled") or 0) >= REFILL:
        return 0
    added = 0
    with LOCK:
        for pack in data["packs"]:
            kept = pack.get("file") or ""
            if not kept or not os.path.exists(kept):
                continue
            try:
                with open(kept, "rb") as f:
                    info_hash, name, files = parse_torrent(f.read())
            except (OSError, ValueError, IndexError):
                continue
            have = {int(f.get("index", -1)) for f in (pack.get("films") or [])}
            for one in files:
                if int(one["index"]) in have:
                    continue
                low = one["path"].lower()
                floor = EPISODE_BYTES if episode_of(one["path"]) else FILM_BYTES
                if not low.endswith(VIDEO) or one["size"] < floor or "sample" in low:
                    continue
                title, year = film_of(one["path"])
                entry = {"index": one["index"], "path": one["path"],
                         "size": one["size"], "title": title, "year": year,
                         "key": key_for(info_hash, one["index"]), "tmdb": None}
                told = episode_of(one["path"])
                if told:
                    show, made, season, number = told
                    entry.update({"title": show, "year": made or year,
                                  "kind": "episode", "season": season,
                                  "episode": number})
                pack.setdefault("films", []).append(entry)
                added += 1
        data["refilled"] = REFILL
        save()
    return added


def read_episodes():
    """Mark the episodes in packs added before any of this was read. Once per pack.

    Their entries were matched against the catalogue's films and found nothing, and the
    answer was written down as "not found" - so nothing would ask again. The mark is
    cleared with it, and the next pass asks the right catalogue.
    """
    data = load()
    changed = False
    with LOCK:
        for pack in data["packs"]:
            if pack.get("episodesRead"):
                continue
            pack["episodesRead"] = True
            changed = True
            for film in pack.get("films") or []:
                told = episode_of(film.get("path") or "")
                if not told:
                    continue
                show, made, season, number = told
                film.update({"title": show, "year": made or film.get("year") or 0,
                             "kind": "episode", "season": season, "episode": number})
                # cleared so _match_some asks TMDB TV for it
                film["tmdb"] = None
                film.pop("rechecked", None)
        if changed:
            save()
    return changed


def remove_pack(info_hash):
    data = load()
    with LOCK:
        going = [p for p in data["packs"] if p.get("hash") == info_hash]
        data["packs"] = [p for p in data["packs"] if p.get("hash") != info_hash]
        save()
    # The file it arrived as goes with it. Left in the folder it would be read again
    # on the next look and the pack would come straight back, which reads as the
    # remove button doing nothing.
    for p in going:
        name = str(p.get("dropped") or "")
        if name:
            try:
                os.remove(os.path.join(drop_folder(), name))
            except OSError:
                pass
    return {"ok": True}


def set_config(body):
    data = load()
    with LOCK:
        cfg = data["config"]
        for name in ("url", "user", "saveTo"):
            if name in body:
                cfg[name] = str(body[name] or "").strip()
        # nextEpisode: fetch the following episode at the half-way point of one
        if "nextEpisode" in body:
            cfg["nextEpisode"] = bool(body["nextEpisode"])
        if body.get("password") is not None and body.get("password") != "":
            cfg["password"] = str(body["password"])
        if body.get("clearPassword"):
            cfg.pop("password", None)
        save()
    return status()


#: what a cache copies of the main server's match for each film
MATCH_FIELDS = ("tmdb", "name", "poster", "backdrop", "overview", "rating", "released",
                "genres", "runtime", "rechecked")


def _following():
    """This machine's follow settings when it follows a house, else None."""
    was = STATE.get("following")
    if was and time.time() - was[0] < 30:
        return was[1]
    one = None
    lib = STATE["lib"]() if STATE["lib"] else None
    if lib:
        try:
            import pd_follow
            got = pd_follow.settings(lib.config())
            if got.get("on") and got.get("master") and got.get("key"):
                one = got
        except Exception:
            one = None
    STATE["following"] = (time.time(), one)
    return one


def house_is_up():
    """A cache offers no films while its house answers: the main server offers them, and a
    film asked for on both would come down twice."""
    return bool(_following()) and time.time() - STATE.get("house_at", 0) < 150


def mirror(have=()):
    """The packs offered here, as a cache takes them: each with its films as matched
    here, and the torrent itself for a pack the cache does not hold yet."""
    import base64
    out = []
    for pack in load()["packs"]:
        one = {"hash": pack["hash"], "name": pack.get("name"), "films": pack.get("films") or []}
        if pack["hash"] not in have:
            try:
                with open(pack["file"], "rb") as f:
                    one["data"] = base64.b64encode(f.read()).decode("ascii")
            except OSError:
                continue
        out.append(one)
    return {"packs": out}


def _mirror_house():
    """A cache takes the main server's packs, to offer the same films while the main server is off.
    The main server is asked after once a minute, and for its packs every ten."""
    one = _following()
    if not one:
        return
    import base64
    import pd_follow
    now = time.time()
    if now - STATE.get("house_asked", 0) < 60:
        return
    STATE["house_asked"] = now
    data = load()
    try:
        if now - STATE.get("mirrored_at", 0) < 600:
            pd_follow.ask(one, "/follow/torrents?ping=1", 8)
            STATE["house_at"] = time.time()
            return
        said = pd_follow.ask(one, "/follow/torrents?have=" +
                             ",".join(p["hash"] for p in data["packs"]), 120)
    except Exception:
        return                          # the main server is off, or older: this machine offers
    STATE["house_at"] = STATE["mirrored_at"] = time.time()
    theirs = {p.get("hash"): p for p in said.get("packs") or [] if p.get("hash")}
    for info_hash, p in theirs.items():
        mine = next((x for x in data["packs"] if x.get("hash") == info_hash), None)
        if mine is None and p.get("data"):
            try:
                added = add_pack(raw=base64.b64decode(p["data"]))
            except (ValueError, TypeError):
                continue
            mine = next((x for x in data["packs"] if x.get("hash") == info_hash), None)
            if not added.get("ok") or mine is None:
                continue
            mine["mirrored"] = True
        if mine is None:
            continue
        matched = {f.get("key"): f for f in p.get("films") or []}
        with LOCK:
            for film in mine.get("films") or []:
                there = matched.get(film.get("key"))
                if there and there.get("tmdb") is not None:
                    for name in MATCH_FIELDS:
                        if name in there:
                            film[name] = there[name]
    with LOCK:
        # a pack the main server no longer offers goes; one added here by hand stays
        data["packs"] = [x for x in data["packs"] if not x.get("mirrored") or x.get("hash") in theirs]
        save()


def save_folder():
    """Where downloads go: the folder set, or the library's first film folder. A cache
    with neither keeps them beside its copies, not among them: its cap clears that folder."""
    cfg = load()["config"]
    lib = STATE["lib"]() if STATE["lib"] else None
    conf = lib.config() if lib else {}
    folders = list(conf.get("movies") or [])
    if cfg.get("saveTo") or folders:
        return cfg.get("saveTo") or folders[0]
    one = _following()
    if one and one.get("folder"):
        return os.path.join(os.path.dirname(os.path.normpath(one["folder"])), "Palladium Downloads")
    mixed = list(conf.get("mixed") or [])
    return mixed[0] if mixed else ""


def _in_the_library(folder):
    """The folder downloads go to, among the library's folders, so what arrives is scanned in."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib or not folder:
        return
    cfg = lib.config()
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    for f in (cfg.get("movies") or []) + (cfg.get("mixed") or []) + (cfg.get("tv") or []):
        if norm(folder) == norm(f) or norm(folder).startswith(norm(f).rstrip("\\/") + os.sep):
            return
    os.makedirs(folder, exist_ok=True)
    cfg.setdefault("movies", []).append(folder)
    lib.save_config(cfg)


def free_gb(path):
    """Gigabytes free on the drive a folder is on, or None when it cannot be read."""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not probe:
        return None
    try:
        return round(shutil.disk_usage(probe).free / 1e9, 1)
    except (OSError, ValueError):
        return None


def free_cached():
    was = STATE["free"]
    if was and time.time() - was[0] < 30:
        return was[1]
    gb = free_gb(save_folder())
    STATE["free"] = (time.time(), gb)
    return gb


def by_key(key):
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("key") == key:
                return pack, film
    return None, None


# ---------------------------------------------------------------- the library's view

def _owned():
    """What the library already holds, by TMDB number and by name and year."""
    if STATE["owned"] is not None and time.time() - STATE["owned_at"] < 60:
        return STATE["owned"]
    tmdb, named, episodes = set(), set(), set()
    lib = STATE["lib"]() if STATE["lib"] else None
    if lib:
        con = lib.db()
        try:
            for row in con.execute("SELECT tmdb_id, title, year FROM item WHERE type='movie'"):
                if row["tmdb_id"]:
                    tmdb.add(int(row["tmdb_id"]))
                named.add(((row["title"] or "").strip().lower(), int(row["year"] or 0)))
            # episodes with a file on disk. item rows exist for a whole programme
            # whether or not its files are here, so the title says nothing
            for row in con.execute(
                    """SELECT i.title AS show, e.season AS season, e.number AS number
                       FROM episode e JOIN item i ON i.id = e.item_id
                       JOIN file f ON f.episode_id = e.id
                       WHERE e.season IS NOT NULL AND e.number IS NOT NULL"""):
                episodes.add(((row["show"] or "").strip().lower(),
                              int(row["season"] or 0), int(row["number"] or 0)))
        finally:
            con.close()
    STATE["owned"] = {"tmdb": tmdb, "named": named, "episodes": episodes}
    STATE["owned_at"] = time.time()
    return STATE["owned"]


def latest_download(key):
    rows = [d for d in load()["downloads"] if d.get("key") == key]
    return rows[-1] if rows else None


def unfinished(path):
    """True for a file a download has not finished. The library leaves it out until it
    has: indexed halfway, ffmpeg reads zeros where the missing pieces are."""
    names = STATE.get("unfinished")
    if names is None or time.time() - STATE.get("unfinished_at", 0) > 5:
        names = set()
        latest = {}
        for d in load()["downloads"]:
            latest[d.get("key")] = d
        for d in latest.values():
            # a cancelled download leaves its part-file behind, which plays no better
            if d.get("state") in ("queued", "downloading") or (
                    d.get("state") == "cancelled" and float(d.get("progress") or 0) < 1):
                _, film = by_key(d.get("key"))
                if film and film.get("path"):
                    names.add(os.path.normcase(os.path.basename(film["path"])))
        STATE["unfinished"], STATE["unfinished_at"] = names, time.time()
    return os.path.normcase(os.path.basename(path)) in names


def held_here(film, owned=None):
    """True when the library already holds this entry.

    Episodes by (show, season, number); films by TMDB id, else name and year.
    """
    owned = owned or _owned()
    if film.get("kind") == "episode":
        return ((str(film.get("name") or film.get("title") or "").strip().lower(),
                 int(film.get("season") or 0), int(film.get("episode") or 0))
                in owned["episodes"])
    if film.get("tmdb") and int(film["tmdb"]) in owned["tmdb"]:
        return True
    return ((str(film.get("name") or film.get("title") or "").strip().lower(),
             int(film.get("year") or 0)) in owned["named"])


def _same(film):
    """What makes two files in a pack the same film: its TMDB match, else name and year."""
    if film.get("tmdb"):
        return ("tmdb", int(film["tmdb"]))
    return ("named", (film.get("name") or film.get("title") or "").strip().lower(),
            int(film.get("year") or 0))


def edition(film):
    """What sets one release apart from another of the same film: the words after its year
    that are not quality words - Criterion, Extended, disc 2. Empty for the plain one."""
    base = os.path.splitext(os.path.basename(film.get("path") or ""))[0]
    tokens = [t for t in re.split(r"[._ ()\[\]]+", base) if t]
    year = str(film.get("year") or "")
    at = max([n for n, t in enumerate(tokens) if year and t == year] or [-1])
    title = {w.lower() for w in re.split(r"\W+", film.get("title") or "") if w}
    return " ".join(t for t in tokens[at + 1:]
                    if not QUALITY.match(t) and not QUALITY.match(t.split("-")[0])
                    and (at >= 0 or t.lower() not in title))


def _versions(group):
    """The releases of one film to choose between, when there is more than one."""
    if len(group) < 2:
        return []
    out = []
    for film, _ in group:
        got = latest_download(film["key"]) or {}
        out.append({"key": film["key"], "label": edition(film) or "Standard",
                    "size": int(film.get("size") or 0), "state": got.get("state") or "",
                    "progress": float(got.get("progress") or 0)})
    return out


def _ahead(a, b):
    """Of two releases of one film, the one to offer: the one asked for, else the larger."""
    asked_a, asked_b = latest_download(a["key"]) is not None, latest_download(b["key"]) is not None
    if asked_a != asked_b:
        return asked_a
    return int(a.get("size") or 0) >= int(b.get("size") or 0)


def _place(key):
    """How many downloads are ahead of one waiting its turn."""
    ahead = 0
    for d in load()["downloads"]:
        if d.get("key") == key and d.get("state") == "queued":
            return ahead
        if d.get("state") in ("queued", "downloading"):
            ahead += 1
    return 0


def item(film, pack, free=None, versions=()):
    """One film on offer, as the library lists a title."""
    name = film.get("name") or film.get("title") or ""
    year = int(film.get("year") or 0) or int(str(film.get("released") or "0")[:4] or 0)
    got = latest_download(film["key"]) or {}
    return {
        "ratingKey": film["key"], "type": "movie", "title": name,
        "titleSort": re.sub(r"^(the|a|an) ", "", name.lower()),
        "year": year or None,
        "genres": list(film.get("genres") or []),
        "summary": film.get("overview") or "",
        "rating": film.get("rating"),
        "duration": int(film.get("runtime") or 0) * 60000,
        "thumb": "/art/%s/poster" % film["key"] if film.get("poster") else None,
        "art": "/art/%s/backdrop" % film["key"] if film.get("backdrop") else None,
        # asked for, it is added from that moment: Recently added shows it coming in
        "addedAt": int(got.get("when") or 0) if got.get("state") in ("queued", "downloading", "done")
                   else int(pack.get("added") or 0),
        "viewCount": 0,
        "maxHeight": 1080 if "1080p" in film.get("path", "").lower() else 0,
        "offered": True,
        "offer": {"size": int(film.get("size") or 0),
                  "state": got.get("state") or "",
                  "progress": float(got.get("progress") or 0),
                  # while it comes in: megabits a second towards this film, and seconds left
                  "mbit": float(got.get("mbit") or 0),
                  "eta": got.get("eta"),
                  "who": got.get("who") or "",
                  # waiting its turn: how many are ahead of it
                  "place": _place(film["key"]) if got.get("state") == "queued" else 0,
                  "file": film.get("path") or "",
                  # room on the drive it would go to, in gigabytes
                  "free": free,
                  # why it cannot be fetched at all, when qBittorrent cannot load the pack
                  "refused": ("qBittorrent cannot load this pack: " + refused(pack))
                             if refused(pack) else "",
                  # the pack's other releases of this film, chosen between on its page
                  "versions": list(versions)},
    }


def offered():
    """Every film on offer the library does not hold yet."""
    if house_is_up():
        return []                     # the main server offers them while it is up
    if STATE["offered"] is not None and time.time() - STATE["offered_at"] < 10:
        return STATE["offered"]
    owned = _owned()
    free = free_cached()
    groups, order = {}, []
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            # episodes are offered through the TV section, not here: grouped as
            # films they showed as one row standing for a whole pack
            if film.get("kind") == "episode":
                continue
            if held_here(film, owned):
                continue
            # a pack can carry a film twice: a restored cut, a second disc, its trailers.
            # One poster, showing the release asked for or else the largest.
            same = _same(film)
            if same not in groups:
                groups[same] = []
                order.append(same)
            groups[same].append((film, pack))
    out = []
    for same in order:
        film, pack = groups[same][0]
        for f, p in groups[same][1:]:
            if not _ahead(film, f):
                film, pack = f, p
        out.append(item(film, pack, free, _versions(groups[same])))
    STATE["offered"] = out
    STATE["offered_at"] = time.time()
    return out


def show_key(name):
    """Stable key for an offered programme: "os" + sha1(name)[:10]."""
    return "os" + hashlib.sha1(str(name or "").strip().lower().encode("utf-8")
                               ).hexdigest()[:10]


def _episodes_by_show():
    """Every episode a pack holds that the library does not, by programme."""
    owned = _owned()
    out = {}
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") != "episode" or held_here(film, owned):
                continue
            name = str(film.get("name") or film.get("title") or "").strip()
            if name:
                out.setdefault(name, []).append((film, pack))
    return out


def offered_shows():
    """Offered programmes, shaped as the library shapes a show row."""
    if house_is_up():
        return []                     # the main server offers them while it is up
    out = []
    for name, holds in _episodes_by_show().items():
        film = holds[0][0]
        seasons = sorted({int(f.get("season") or 0) for f, _ in holds})
        got = [latest_download(f["key"]) or {} for f, _ in holds]
        out.append({
            "ratingKey": show_key(name), "type": "show", "title": name,
            "titleSort": re.sub(r"^(the|a|an) ", "", name.lower()),
            "year": film.get("year") or None,
            "originallyAvailableAt": str(film.get("released") or ""),
            "summary": film.get("overview") or "",
            "genres": list(film.get("genres") or []),
            "rating": film.get("rating"),
            "leafCount": len(holds), "childCount": len(seasons),
            "viewedLeafCount": 0, "viewCount": 0,
            "thumb": "/art/%s/poster" % film["key"] if film.get("poster") else None,
            "art": "/art/%s/backdrop" % film["key"] if film.get("backdrop") else None,
            "addedAt": max([int(g.get("when") or 0) for g in got] or [0]) or
                       int(holds[0][1].get("added") or 0),
            "maxHeight": 1080 if "1080p" in (film.get("path") or "").lower() else 0,
            "offered": True,
            "offer": {"size": sum(int(f.get("size") or 0) for f, _ in holds),
                      "episodes": len(holds), "seasons": len(seasons),
                      "free": free_cached()},
        })
    return sorted(out, key=lambda x: x["titleSort"])


def offered_seasons(key):
    """Seasons of one offered programme, shaped as library season rows."""
    for name, holds in _episodes_by_show().items():
        if show_key(name) != key:
            continue
        film = holds[0][0]
        by_season = {}
        for f, _ in holds:
            by_season.setdefault(int(f.get("season") or 0), []).append(f)
        return [{
            "ratingKey": "%s-s%d" % (key, season), "type": "season",
            "title": "Season %d" % season, "index": season,
            "leafCount": len(these), "viewedLeafCount": 0,
            "parentRatingKey": key,
            "thumb": "/art/%s/poster" % film["key"] if film.get("poster") else None,
            "offered": True,
        } for season, these in sorted(by_season.items())]
    return []


def episode_item(film, show, pack=None):
    """One offered episode, shaped as a library episode row."""
    got = latest_download(film["key"]) or {}
    key = show_key(show)
    season = int(film.get("season") or 0)
    return {
        "ratingKey": film["key"], "type": "episode",
        "title": film.get("episodeName") or ("Episode %d" % int(film.get("episode") or 0)),
        "index": int(film.get("episode") or 0),
        "parentIndex": season,
        "parentRatingKey": "%s-s%d" % (key, season),
        "parentTitle": "Season %d" % season,
        "grandparentRatingKey": key, "grandparentTitle": show,
        "summary": film.get("overview") or "",
        "originallyAvailableAt": str(film.get("aired") or ""),
        "duration": 0, "viewCount": 0,
        "addedAt": int(got.get("when") or 0) or int((pack or {}).get("added") or 0),
        "thumb": "/art/%s/poster" % film["key"] if film.get("poster") else None,
        "art": "/art/%s/backdrop" % film["key"] if film.get("backdrop") else None,
        "maxHeight": 1080 if "1080p" in (film.get("path") or "").lower() else 0,
        "offered": True,
        "offer": {"size": int(film.get("size") or 0),
                  "state": got.get("state") or "",
                  "progress": float(got.get("progress") or 0),
                  "mbit": float(got.get("mbit") or 0),
                  "eta": got.get("eta"),
                  "who": got.get("who") or "",
                  "place": _place(film["key"]) if got.get("state") == "queued" else 0,
                  "file": film.get("path") or "",
                  "free": free_cached(),
                  "refused": ("qBittorrent cannot load this pack: " + refused(pack))
                             if pack and refused(pack) else "",
                  "versions": []},
    }


def offered_episodes(key, season):
    """Offered episodes of one season, in episode order."""
    for name, holds in _episodes_by_show().items():
        if show_key(name) != key:
            continue
        these = [(f, p) for f, p in holds if int(f.get("season") or 0) == int(season)]
        return [episode_item(f, name, p)
                for f, p in sorted(these, key=lambda x: int(x[0].get("episode") or 0))]
    return []


def metadata(key):
    # an offered programme, not one of its files
    if str(key).startswith("os"):
        return next((s for s in offered_shows() if s["ratingKey"] == key), None)
    pack, film = by_key(key)
    if not film:
        return None
    # episode entries return episode metadata: as movie metadata the page showed
    # the programme's name with no episode number
    if film.get("kind") == "episode":
        return episode_item(film, str(film.get("name") or film.get("title") or ""), pack)
    same = _same(film)
    group = [(f, p) for p in load()["packs"] for f in (p.get("films") or []) if _same(f) == same]
    return item(film, pack, free_cached(), _versions(group))


def art_of(key, kind):
    """The TMDB path of a film on offer's poster or backdrop."""
    _, film = by_key(key)
    return (film or {}).get("poster" if kind == "poster" else "backdrop")


# ---------------------------------------------------------------- qBittorrent

class QB:
    """qBittorrent's Web API. A request from an address it trusts needs no login."""

    def __init__(self, cfg):
        self.base = (cfg.get("url") or "http://127.0.0.1:8080").rstrip("/")
        self.user = cfg.get("user") or ""
        self.password = cfg.get("password") or ""
        self.cookie = ""

    def _call(self, path, fields=None, upload=None, timeout=30, again=True):
        headers = {"Referer": self.base, "Origin": self.base}
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if upload is not None:
            boundary = "palladium" + uuid.uuid4().hex
            parts = []
            for k, v in (fields or {}).items():
                parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                              % (boundary, k, v)).encode("utf-8"))
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"torrents\"; "
                          "filename=\"pack.torrent\"\r\nContent-Type: application/x-bittorrent"
                          "\r\n\r\n" % boundary).encode("utf-8") + upload + b"\r\n")
            parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
            data = b"".join(parts)
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        elif fields is not None:
            data = urllib.parse.urlencode(fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as answer:
                return answer.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and again and self.user:
                self.login()
                return self._call(path, fields, upload, timeout, again=False)
            raise

    def login(self):
        req = urllib.request.Request(
            self.base + "/api/v2/auth/login",
            data=urllib.parse.urlencode({"username": self.user,
                                         "password": self.password}).encode("utf-8"),
            headers={"Referer": self.base, "Origin": self.base,
                     "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as answer:
            said = answer.read().decode("utf-8", "replace")
            cookie = answer.headers.get("Set-Cookie") or ""
        if "Ok" not in said or "SID=" not in cookie:
            raise RuntimeError("qBittorrent refused the login")
        self.cookie = cookie.split(";", 1)[0]

    def version(self):
        return self._call("/api/v2/app/version", timeout=6).decode("utf-8", "replace").strip()

    def info(self, info_hash):
        rows = json.loads(self._call("/api/v2/torrents/info?hashes=" + info_hash) or b"[]")
        return rows[0] if rows else None

    def files(self, info_hash):
        return json.loads(self._call("/api/v2/torrents/files?hash=" + info_hash) or b"[]")

    def add(self, raw, save_to):
        said = self._call("/api/v2/torrents/add", fields={
            "savepath": save_to, "category": "palladium",
            # added stopped, whichever of the two names this build reads
            "stopped": "true", "paused": "true",
            # the film's own folder straight under the save path, not inside the pack's
            "contentLayout": "NoSubfolder"}, upload=raw, timeout=60)
        if b"Fail" in said:
            raise RuntimeError("qBittorrent would not take the torrent")

    def priority(self, info_hash, indexes, value):
        self._call("/api/v2/torrents/filePrio", fields={
            "hash": info_hash, "id": "|".join(str(i) for i in indexes),
            "priority": str(value)})

    def start(self, info_hash):
        try:
            self._call("/api/v2/torrents/start", fields={"hashes": info_hash})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            self._call("/api/v2/torrents/resume", fields={"hashes": info_hash})

    def recheck(self, info_hash):
        """Ask qBittorrent to look at the files on disk again.

        A film copied in from the other machine is sitting exactly where this pack
        expects it, but qBittorrent has no idea: it still thinks that file is one it
        has never fetched. A recheck reads what is there, finds the pieces complete
        and marks it done - so it is seeded rather than fetched a second time.
        """
        self._call("/api/v2/torrents/recheck", fields={"hashes": info_hash})

    def stop(self, info_hash):
        # start and stop under 5, pause and resume before it
        try:
            self._call("/api/v2/torrents/stop", fields={"hashes": info_hash})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            self._call("/api/v2/torrents/pause", fields={"hashes": info_hash})


def status():
    data = load()
    cfg = data["config"]
    qb = {"ok": False, "version": "", "why": ""}
    try:
        qb["version"] = QB(cfg).version()
        qb["ok"] = True
    except Exception as e:
        qb["why"] = str(e)[:160]
    lib = STATE["lib"]() if STATE["lib"] else None
    folders = list((lib.config().get("movies") or []) + (lib.config().get("mixed") or [])) \
        if lib else []
    packs = []
    on_offer = {o["ratingKey"] for o in offered()}
    for pack in data["packs"]:
        films = pack.get("films") or []
        keys = {f["key"] for f in films}
        latest = {}
        for d in data["downloads"]:
            if d.get("key") in keys:
                latest[d["key"]] = d
        done = [d for d in latest.values() if d.get("state") == "done"]
        busy = [d for d in latest.values() if d.get("state") in ("queued", "downloading")]
        # Episodes are not on the film shelves, so the count of what is on offer
        # cannot come from that list: each one is held or it is not.
        owned = _owned()
        eps = [f for f in films if f.get("kind") == "episode"]
        eps_held = sum(1 for f in eps if held_here(f, owned))
        eps_done = sum(1 for f in eps
                       if (latest.get(f["key"]) or {}).get("state") == "done")
        packs.append({"hash": pack["hash"], "name": pack.get("name"),
                      "added": pack.get("added"), "films": len(films),
                      "matched": sum(1 for f in films if f.get("tmdb")),
                      "unmatched": sum(1 for f in films if f.get("tmdb") == 0),
                      "waiting": sum(1 for f in films if f.get("tmdb") is None),
                      "gb": round(sum(f.get("size") or 0 for f in films) / 1e9, 1),
                      # what has come of it: fetched, on its way, still on offer, and
                      # what the library already had
                      "downloaded": len(done),
                      "downloadedGb": round(sum(d.get("size") or 0 for d in done) / 1e9, 1),
                      "downloading": len(busy),
                      "offered": len(keys & on_offer) + max(
                          0, len(eps) - eps_held - eps_done),
                      "episodes": len(eps),
                      "held": len(films) - (len(keys & on_offer) + max(
                          0, len(eps) - eps_held - eps_done)) - len(done),
                      "refused": refused(pack),
                      "pieceMiB": round((pack.get("pieceBytes") or 0) / 1048576)})
    return {"config": {"url": cfg.get("url") or "http://127.0.0.1:8080",
                       "user": cfg.get("user") or "",
                       "hasPassword": bool(cfg.get("password")),
                       "nextEpisode": bool(cfg.get("nextEpisode")),
                       "saveTo": cfg.get("saveTo") or (folders[0] if folders else "")},
            "folders": folders, "qbittorrent": qb, "packs": packs,
            # where a torrent file can be dropped to become a pack by itself, so the
            # page can open the picker there rather than at the top of a drive
            "packFolder": drop_folder(),
            "offered": len(offered()),
            "free": free_gb(cfg.get("saveTo") or (folders[0] if folders else ""))}


# ---------------------------------------------------------------- asking for one film

def next_wanted():
    """Whether the next episode should be fetched while one is being watched."""
    try:
        return bool(load()["config"].get("nextEpisode"))
    except Exception:
        return False


def episode_in_a_pack(show, season, number):
    """The pack entry for one episode of one programme, or nothing.

    Matched on the name the catalogue gave the programme where there is one, and on the
    name read off the file where there is not - the library's own title is the catalogue's.
    """
    want = str(show or "").strip().lower()
    if not want:
        return None
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") != "episode":
                continue
            if int(film.get("season") or 0) != int(season):
                continue
            if int(film.get("episode") or 0) != int(number):
                continue
            for name in (film.get("name"), film.get("title")):
                if str(name or "").strip().lower() == want:
                    return film
    return None


def next_after(show, season, number):
    """The episode after this one, if a pack has it and the library does not.

    The next number in the season, and failing that the first of the season after it -
    which is what "the next episode" means to somebody watching the last one of a season.
    """
    # The one after this, and the first of the season after it only where the season
    # has ended - not where the next number is simply already here. Falling through on
    # "already held" jumped a whole season the moment the next episode had arrived.
    film = (episode_in_a_pack(show, int(season), int(number) + 1)
            or episode_in_a_pack(show, int(season) + 1, 1))
    if not film or held_here(film, _owned()):
        return None
    was = latest_download(film["key"]) or {}
    if was.get("state") in ("queued", "downloading"):
        return None                        # already on its way, and once is enough
    if was.get("state") == "done" and not was.get("off"):
        return None                        # fetched already; the scan will pick it up
    return film


def fetch_next(show, season, number, token="me", who="", cap_gb=0.0):
    """Ask for the episode after this one. Nothing at all when there is none to ask for."""
    if not next_wanted():
        return None
    film = next_after(show, season, number)
    if not film:
        return None
    said = request(film["key"], token, who or "the next episode", cap_gb)
    said["key"] = film["key"]
    said["episode"] = "S%02dE%02d" % (int(film.get("season") or 0),
                                      int(film.get("episode") or 0))
    return said


def request(key, token, who, cap_gb=0.0):
    """Fetch one film of a pack for somebody: that file on, everything else off."""
    pack, film = by_key(key)
    if not film:
        return {"ok": False, "why": "That film is not on offer"}
    # a pack qBittorrent cannot load is not tried, or every press is one more failure
    if refused(pack):
        return {"ok": False, "why": "qBittorrent cannot load this pack: " + refused(pack)}
    data = load()
    # room first: a film that cannot fit is not started
    folder = save_folder()
    free = free_gb(folder)
    if free is not None and film["size"] / 1e9 > free:
        return {"ok": False,
                "why": "Not enough room: %.1f GB free on %s, and this film is %.1f GB"
                       % (free, os.path.splitdrive(folder)[0] or folder, film["size"] / 1e9)}
    with LOCK:
        was = latest_download(key)
        # done and not scanned in yet is on its way; done long ago and asked for again
        # means the file has gone from the library, and it is fetched again
        if was and (was.get("state") in ("queued", "downloading")
                    or (was.get("state") == "done" and not was.get("off")
                        and time.time() - float(was.get("done") or 0) < 900)):
            return {"ok": True, "already": True, "state": was["state"],
                    "progress": was.get("progress") or 0}
        if cap_gb and cap_gb > 0:
            since = time.time() - 7 * 86400
            used = sum(d.get("size") or 0 for d in data["downloads"]
                       if d.get("token") == token and d.get("when", 0) >= since
                       and d.get("state") not in ("failed", "cancelled")) / 1e9
            if used + film["size"] / 1e9 > cap_gb:
                return {"ok": False,
                        "why": "That would pass this week's download limit: %.1f of %.1f GB "
                               "used, and this film is %.1f GB" % (used, cap_gb, film["size"] / 1e9)}
        row = {"id": uuid.uuid4().hex[:10], "key": key, "hash": pack["hash"],
               "index": film["index"], "title": film.get("name") or film.get("title"),
               "year": film.get("year"), "size": film["size"], "who": who,
               "token": token, "when": int(time.time()), "state": "queued",
               "progress": 0.0, "why": ""}
        if film.get("kind") == "episode":
            # which episode, on the row itself: the title is the programme's name and
            # is the same for all of them
            row.update({"kind": "episode", "season": film.get("season"),
                        "episode": film.get("episode"),
                        "episodeName": film.get("episodeName") or ""})
        # one film at a time: behind another, it waits its turn with its file off
        ahead = sum(1 for d in data["downloads"] if d.get("state") in ("queued", "downloading"))
        if not ahead:
            row.update(state="downloading", started=int(time.time()))
        data["downloads"].append(row)
        save()
    ensure_worker()
    if ahead:
        return {"ok": True, "state": "queued", "why": "", "place": ahead}
    _begin(row, pack, film)
    return {"ok": row["state"] != "failed", "state": row["state"], "why": row["why"]}


def _begin(row, pack, film):
    """A download whose turn it is: the pack in qBittorrent, that file on."""
    try:
        fetch(pack, film)
    except Exception as e:
        row["state"] = "failed"
        row["why"] = str(e)[:200]
    with LOCK:
        save()


def _start_next():
    """One film at a time: when nothing is coming in, the request that has waited longest
    starts."""
    with LOCK:
        data = load()
        if any(d.get("state") == "downloading" for d in data["downloads"]):
            return
        row = next((d for d in data["downloads"] if d.get("state") == "queued"), None)
        if row is None:
            return
        pack, film = by_key(row.get("key"))
        why = "No longer on offer" if not film else ""
        if not why and refused(pack):
            why = "qBittorrent cannot load this pack: " + refused(pack)
        if why:
            row.update(state="failed", why=why)
        else:
            row.update(state="downloading", started=int(time.time()))
        save()
    if not why:
        _begin(row, pack, film)


def fetch(pack, film):
    cfg = load()["config"]
    save_to = save_folder()
    if not save_to:
        raise RuntimeError("No folder to save into: set one under Downloads")
    _in_the_library(save_to)
    qb = QB(cfg)
    held = qb.info(pack["hash"])
    if held is None:
        with open(pack["file"], "rb") as f:
            try:
                qb.add(f.read(), save_to)
            except urllib.error.HTTPError as e:
                if e.code != 415:
                    raise
                # qBittorrent's word for a .torrent it could not load
                with LOCK:
                    pack["refused"] = "qBittorrent said the torrent file is not valid"
                    pack["refusedBy"] = client_limit()[1]
                    save()
                raise RuntimeError("qBittorrent cannot load this pack: the torrent file "
                                   "was refused as not valid")
        files = []
        for _ in range(30):
            try:
                files = qb.files(pack["hash"])
            except urllib.error.HTTPError:
                files = []
            if files:
                break
            time.sleep(1)
        if not files:
            raise RuntimeError("qBittorrent took the torrent but lists no files in it")
    # Only what somebody asked for, however the torrent got into qBittorrent. A pack its
    # own watched folder picked up - or added by hand - has every file set to download,
    # and turning the one film on left the other thousand on with it.
    only_asked(qb, pack["hash"], extra=[film["index"]])
    qb.start(pack["hash"])


def asked_for(info_hash):
    """The files of one pack that are on: coming in now, or here. One waiting its turn is off."""
    return {int(d["index"]) for d in load()["downloads"]
            if d.get("hash") == info_hash and d.get("state") in ("downloading", "done")
            and not d.get("off")}


def only_asked(qb, info_hash, extra=(), files=None):
    """Every file in the pack off except the ones asked for. True when anything changed."""
    want = asked_for(info_hash) | {int(i) for i in extra}
    files = files if files is not None else qb.files(info_hash)
    selected = {int(f.get("index", n)) for n, f in enumerate(files) if (f.get("priority") or 0) > 0}
    off = sorted(selected - want)
    on = sorted(i for i in want if i not in selected)
    if off:
        qb.priority(info_hash, off, 0)
    if on:
        qb.priority(info_hash, on, 1)
        # A torrent qBittorrent reads as finished - every file it was holding is in -
        # takes no notice of another one being asked for. The request answers as
        # though it worked and the file stays off, which then reads here as somebody
        # having turned it off by hand, and the download is cancelled a minute later.
        # Stopped, it listens. So anything that did not take is asked for again with
        # the torrent stopped, and it is started once more.
        deaf = [i for i in on if not _wanted_now(qb, info_hash, i)]
        if deaf:
            qb.stop(info_hash)
            time.sleep(1.0)
            qb.priority(info_hash, deaf, 1)
            qb.start(info_hash)
    return bool(off or on)


def _wanted_now(qb, info_hash, index):
    """Whether qBittorrent took the asking: read back, not assumed."""
    try:
        for n, f in enumerate(qb.files(info_hash)):
            if int(f.get("index", n)) == int(index):
                return (f.get("priority") or 0) > 0
    except Exception:
        return True                       # not answering is not a refusal
    return False


#: how much is read at a time when a film is copied in from the other machine
LUMP = 4 * 1024 * 1024


def my_own_downloads():
    """What this machine fetched itself, for the server it follows to take a copy of.

    Only what finished, and only the pack entry it came from - the name inside the
    torrent is what says where it has to sit on the other machine for that pack to
    recognise it.
    """
    data = load()
    out = []
    try:
        qb = QB(data["config"])
    except Exception:
        return out
    names = {}
    for d in data["downloads"]:
        if d.get("state") != "done":
            continue
        h = str(d.get("hash") or "")
        if not h:
            continue
        if h not in names:
            try:
                names[h] = {int(f.get("index", n)): str(f.get("name") or "")
                            for n, f in enumerate(qb.files(h))}
            except Exception:
                names[h] = {}
        name = names[h].get(int(d.get("index") or -1)) or ""
        if not name:
            continue
        out.append({"hash": h, "index": int(d["index"]), "name": name,
                    "size": int(d.get("size") or 0), "key": str(d.get("key") or ""),
                    "title": str(d.get("title") or "")})
    return out


def file_of(info_hash, index):
    """Where one file of one pack sits on this machine, or ''."""
    data = load()
    try:
        qb = QB(data["config"])
        for n, f in enumerate(qb.files(info_hash)):
            if int(f.get("index", n)) == int(index):
                held = qb.info(info_hash) or {}
                root = str(held.get("save_path") or save_folder() or "")
                return os.path.join(root, str(f.get("name") or "").replace("/", os.sep))
    except Exception:
        pass
    return ""


def place_and_recheck(info_hash, index, name, reader, size=0):
    """Write a file copied from the other machine where its pack expects it, then look again.

    Returns the path written, or "" when there was nowhere sensible to put it. The
    file is written beside itself and moved into place, so a half-copied one is never
    mistaken for the film.
    """
    # Where this machine's own qBittorrent expects that file, not where the other
    # machine kept it. The two hold the same pack under different layouts - one with
    # the pack's own folder, one without - so the name the other machine sends put a
    # film a folder above where the pack looks for it: it played, because the folder
    # is one the library reads, and the recheck that was meant to hand it to
    # qBittorrent found nothing. The sent name is the fallback for a pack this
    # machine does not have at all.
    root = save_folder()
    onto = file_of(info_hash, index)
    if not onto:
        if not root or not name:
            return ""
        onto = os.path.join(root, str(name).replace("/", os.sep))
    try:
        os.makedirs(os.path.dirname(onto), exist_ok=True)
    except OSError:
        return ""
    if os.path.exists(onto) and size and os.path.getsize(onto) == int(size):
        return onto                        # already here, and the right length
    part = onto + ".part"
    try:
        with open(part, "wb") as f:
            while True:
                lump = reader.read(LUMP)
                if not lump:
                    break
                f.write(lump)
        os.replace(part, onto)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        return ""
    # Asked for, then looked at again: a file nobody asked this machine to fetch sits
    # at priority nought, and a recheck over one of those leaves the pack saying it
    # has none of it however whole the file on the disk is.
    try:
        qb = QB(load()["config"])
        try:
            qb.priority(info_hash, [int(index)], 1)
        except Exception:
            pass                           # an older qBittorrent: recheck anyway
        qb.recheck(info_hash)
    except Exception:
        pass                               # the file is there either way
    return onto


def arrived(key):
    """The library's own key for a film that has come in from its offer, or None while it has
    not. Looked up by its file each time: a match or a merge can file it under another key."""
    got = latest_download(key)
    if not got or got.get("state") != "done":
        return None
    _, film = by_key(key)
    name = os.path.basename((film or {}).get("path") or "")
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib or not name:
        return None
    # An episode is filed against its own row rather than a title of its own, so the
    # file that has arrived is looked for either way - without this an episode that had
    # come in and been scanned read as still on its way.
    where = ("SELECT f.item_id FROM file f JOIN item i ON i.id = f.item_id "
             "WHERE f.path LIKE ?"
             + ("" if (film or {}).get("kind") == "episode" else " AND f.episode_id IS NULL"))
    con = lib.db()
    try:
        row = con.execute(where, ("%" + name,)).fetchone()
    finally:
        con.close()
    return str(row["item_id"]) if row else None


def _name_arrivals():
    """A film that has come in takes the match it was offered under: its poster, summary
    and genres, rather than waiting for the library to guess from the file name."""
    data = load()
    pending = [d for d in data["downloads"] if d.get("state") == "done" and not d.get("named")]
    if not pending:
        return
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return
    changed = False
    for d in pending:
        _, film = by_key(d.get("key"))
        # An episode takes its name from the programme it belongs to, which the library
        # does for itself; there is no title of its own here to write.
        if (film or {}).get("kind") == "episode":
            d["named"] = True
            changed = True
            continue
        tmdb = int((film or {}).get("tmdb") or 0)
        name = os.path.basename((film or {}).get("path") or "")
        if not tmdb or not name:
            d["named"] = True
            changed = True
            continue
        con = lib.db()
        try:
            row = con.execute("SELECT f.item_id, i.tmdb_id FROM file f JOIN item i ON i.id = f.item_id "
                              "WHERE f.path LIKE ? AND f.episode_id IS NULL", ("%" + name,)).fetchone()
        finally:
            con.close()
        if not row:
            continue                      # not scanned in yet: asked again next round
        if not row["tmdb_id"]:
            try:
                lib.rematch(str(row["item_id"]), tmdb)
            except Exception as e:
                STATE["why"] = str(e)[:160]
                continue
        d["named"] = True
        STATE["owned"] = None
        changed = True
    if changed:
        with LOCK:
            save()


def _stop(qb, info_hash):
    try:
        qb._call("/api/v2/torrents/stop", fields={"hashes": info_hash})
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        qb._call("/api/v2/torrents/pause", fields={"hashes": info_hash})    # before 5.0


def cancel(key, token, owner=False):
    """Stop a download: its file off in qBittorrent, the film offered again. The owner's to
    do, or whoever asked for it."""
    with LOCK:
        row = latest_download(key)
        if not row or row.get("state") not in ("queued", "downloading"):
            return {"ok": False, "why": "That film is not downloading"}
        if not owner and row.get("token") != token:
            return {"ok": False, "why": "Only whoever asked for it can cancel it"}
        row.update(state="cancelled", why="Cancelled", mbit=0.0, eta=None)
        save()
    try:
        qb = QB(load()["config"])
        only_asked(qb, row["hash"])
        if not asked_for(row["hash"]):
            _stop(qb, row["hash"])
    except Exception as e:
        STATE["why"] = str(e)[:160]
    try:
        _start_next()                 # the next in line, now rather than on the next round
    except Exception as e:
        STATE["why"] = str(e)[:160]
    return {"ok": True, "state": "cancelled"}


def active(token=None):
    """Films coming in now, as the library lists a title: everyone's, or one person's."""
    out = []
    for d in load()["downloads"]:
        if d.get("state") in ("queued", "downloading") and (token is None or d.get("token") == token):
            one = metadata(d["key"])
            if one and not any(o["ratingKey"] == one["ratingKey"] for o in out):
                out.append(one)
    return out


def _notice_removed():
    """A download whose file was turned off in qBittorrent, or whose torrent was taken out
    of it, is over: the film is offered again and waits for the next request."""
    data = load()
    now = time.time()
    # a download waiting its turn has its file off on purpose, and is left alone
    rows = [d for d in data["downloads"]
            if (d.get("state") == "downloading" or (d.get("state") == "done" and not d.get("off")))
            and now - float(d.get("started") or d.get("when") or 0) > 60]
    if not rows:
        return
    qb = QB(data["config"])
    missing = STATE.setdefault("missing", {})
    changed = False
    for info_hash in {d["hash"] for d in rows}:
        try:
            held = qb.info(info_hash)
            files = qb.files(info_hash) if held is not None else []
        except Exception as e:
            STATE["why"] = str(e)[:160]
            continue                    # not answering is not the same as removed
        if held is None:
            # a qBittorrent just started lists its torrents a little later
            if now - missing.setdefault(info_hash, now) < 90:
                continue
        else:
            missing.pop(info_hash, None)
        on = {int(f.get("index", n)) for n, f in enumerate(files) if (f.get("priority") or 0) > 0}
        for d in rows:
            if d["hash"] != info_hash or (held is not None and int(d["index"]) in on):
                continue
            if d["state"] == "done":
                d["off"] = True           # here already; only no longer kept on in qBittorrent
                changed = True
                continue
            # A pack whose every asked-for file is in reads as finished to
            # qBittorrent, and it drops a part-downloaded file it no longer counts.
            # That is not somebody cancelling: the download was running and was
            # switched off underneath it - four of them died this way at a third,
            # a tenth and a tenth of the way in. Ask for it again, which stops the
            # torrent so it listens, and give up only when it will not take.
            if held is not None and int(d.get("revived") or 0) < 3:
                d["revived"] = int(d.get("revived") or 0) + 1
                changed = True
                try:
                    only_asked(qb, info_hash, extra=[int(d["index"])])
                    if _wanted_now(qb, info_hash, int(d["index"])):
                        qb.start(info_hash)
                        continue
                except Exception as e:
                    STATE["why"] = str(e)[:160]
                continue
            d.update(state="cancelled", mbit=0.0, eta=None,
                     why=("Taken out of qBittorrent" if held is None
                          else "qBittorrent would not keep it on"))
            changed = True
    if changed:
        with LOCK:
            save()


def _guard_packs():
    """Keep every pack in qBittorrent to what Palladium was asked for. One that nobody asked
    for is stopped with all of its files off; one that was is held to those films."""
    data = load()
    if not data["packs"]:
        return
    qb = QB(data["config"])
    for pack in data["packs"]:
        try:
            held = qb.info(pack["hash"])
            if held is None:
                continue
            only_asked(qb, pack["hash"])
            if not asked_for(pack["hash"]) and not str(held.get("state") or "").startswith(("stopped", "paused")):
                _stop(qb, pack["hash"])
        except Exception as e:
            STATE["why"] = str(e)[:160]


def downloads():
    """Every download, newest first, without the key it was asked with.

    Season and episode are filled in from the pack entry where the row has none: rows
    written before packs read episodes carry the programme's name alone, which is the
    same for every episode of it.
    """
    out = []
    for d in reversed(load()["downloads"]):
        one = {k: v for k, v in d.items() if k != "token"}
        if not one.get("episode"):
            film = by_key(d.get("key") or "")[1] or {}
            if film.get("kind") == "episode":
                one.update({"kind": "episode", "season": film.get("season"),
                            "episode": film.get("episode"),
                            "episodeName": film.get("episodeName") or ""})
        out.append(one)
    return out


# ---------------------------------------------------------------- the worker

def ensure_worker():
    with LOCK:
        if STATE["worker"] or not STATE["root"]:
            return
        STATE["worker"] = True
    threading.Thread(target=_work, daemon=True).start()


def _match_some(most=40):
    """Match up to so many films to TMDB. False when there is nothing left to match."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib or not (lib.config().get("tmdb_key") or "").strip():
        return False
    waiting = [(p, f) for p in load()["packs"] for f in (p.get("films") or [])
               if f.get("tmdb") is None or _wrong_year(f)]
    if not waiting:
        return False
    genres = STATE.get("genres")
    if genres is None:
        try:
            genres = {g["id"]: g["name"]
                      for g in lib.tmdb("/genre/movie/list").get("genres") or []}
            STATE["genres"] = genres
        except Exception:
            genres = {}
    for _, film in waiting[:most]:
        year = int(film.get("year") or 0)
        if film.get("kind") == "episode":
            _match_episode(lib, film)
            continue
        try:
            # year= also matches re-releases, so a remake could take the original's match
            results = []
            if year:
                results = lib.tmdb("/search/movie", query=film["title"],
                                   primary_release_year=year).get("results") or []
                if not results:
                    results = lib.tmdb("/search/movie", query=film["title"],
                                       year=year).get("results") or []
            if not results:
                results = lib.tmdb("/search/movie", query=film["title"]).get("results") or []
        except Exception:
            return True                      # asked again on the next pass
        best = next((r for r in results
                     if year and str(r.get("release_date") or "")[:4] == str(year)),
                    results[0] if results else None)
        with LOCK:
            film["rechecked"] = True
            if not best:
                if film.get("tmdb") is None:
                    film["tmdb"] = 0
                continue
            film.update({"tmdb": int(best.get("id") or 0),
                         "name": best.get("title") or film["title"],
                         "poster": best.get("poster_path") or "",
                         "backdrop": best.get("backdrop_path") or "",
                         "overview": best.get("overview") or "",
                         "rating": best.get("vote_average"),
                         "released": best.get("release_date") or "",
                         "genres": [genres.get(g) for g in best.get("genre_ids") or []
                                    if genres.get(g)]})
        time.sleep(0.1)
    with LOCK:
        save()
    return True


def _match_episode(lib, film):
    """Match one episode to its programme, and take its own name from the season.

    Asked of the catalogue's programmes rather than its films - there is no film called
    by a programme's name, which is why every episode of a pack came back "not found".
    The programme is looked up once; the season is read once and answers every episode
    in it.
    """
    show = str(film.get("title") or "")
    year = int(film.get("year") or 0)
    genres = STATE.get("tv_genres")
    if genres is None:
        try:
            genres = {g["id"]: g["name"]
                      for g in lib.tmdb("/genre/tv/list").get("genres") or []}
        except Exception:
            genres = {}
        STATE["tv_genres"] = genres
    found = STATE.setdefault("shows", {}).get((show.lower(), year))
    if found is None:
        try:
            results = []
            if year:
                results = lib.tmdb("/search/tv", query=show,
                                   first_air_date_year=year).get("results") or []
            if not results:
                results = lib.tmdb("/search/tv", query=show).get("results") or []
        except Exception:
            return False                     # asked again on the next pass
        found = results[0] if results else {}
        STATE["shows"][(show.lower(), year)] = found
        time.sleep(0.1)
    with LOCK:
        film["rechecked"] = True
        if not found:
            if film.get("tmdb") is None:
                film["tmdb"] = 0
            return True
        film.update({"tmdb": int(found.get("id") or 0),
                     "name": found.get("name") or show,
                     "poster": found.get("poster_path") or "",
                     "backdrop": found.get("backdrop_path") or "",
                     "overview": found.get("overview") or "",
                     "rating": found.get("vote_average"),
                     "released": found.get("first_air_date") or "",
                     "genres": [genres.get(g) for g in found.get("genre_ids") or []
                                if genres.get(g)]})
    # and the episode's own name, from the season it is in
    season = int(film.get("season") or 0)
    where = (int(found.get("id") or 0), season)
    told = STATE.setdefault("seasons", {}).get(where)
    if told is None:
        try:
            told = {int(e.get("episode_number") or 0): e
                    for e in (lib.tmdb("/tv/%d/season/%d" % where).get("episodes") or [])}
        except Exception:
            told = {}
        STATE["seasons"][where] = told
        time.sleep(0.1)
    one = told.get(int(film.get("episode") or 0)) or {}
    if one:
        with LOCK:
            film["episodeName"] = one.get("name") or ""
            if one.get("overview"):
                film["overview"] = one["overview"]
            if one.get("air_date"):
                film["aired"] = one["air_date"]
    return True


def _wrong_year(film):
    """Matched, before first-release matching, to a film of another year. Asked once more."""
    if film.get("rechecked") or not film.get("tmdb") or not film.get("year"):
        return False
    made = str(film.get("released") or "")[:4]
    return made.isdigit() and abs(int(made) - int(film["year"])) > 1


#: a torrent's speed as it settles, per torrent. A swarm serving 256 MB pieces reads
#: 200 Mbit one moment and 3 the next, and a time left worked out from whichever moment
#: was asked said three minutes and then three hours. Read every five seconds and
#: weighted a fifth, this settles over about half a minute.
SPEEDS = {}


def _follow_downloads():
    """Read how far each download has got, how fast its torrent is coming in, and so how
    long is left. True while anything is still on its way."""
    data = load()
    active = [d for d in data["downloads"] if d.get("state") == "downloading"]
    if not active:
        return False
    qb = QB(data["config"])
    changed = False
    for info_hash in {d["hash"] for d in active}:
        try:
            files = qb.files(info_hash)
            speed = float((qb.info(info_hash) or {}).get("dlspeed") or 0)
            was = SPEEDS.get(info_hash)
            settled = speed if was is None else was * 0.8 + speed * 0.2
            SPEEDS[info_hash] = settled
            if only_asked(qb, info_hash, files=files):
                files = qb.files(info_hash)
        except Exception as e:
            STATE["why"] = str(e)[:160]
            continue
        by_index = {int(f.get("index", n)): f for n, f in enumerate(files)}
        mine = [d for d in active if d["hash"] == info_hash]

        def left(d):
            f = by_index.get(int(d["index"])) or {}
            return max(0.0, 1.0 - float(f.get("progress") or 0)) * float(d.get("size") or 0)
        # one torrent's speed, shared among its films by how much each still has to come
        waiting = sum(left(d) for d in mine) or 1.0
        for d in mine:
            f = by_index.get(int(d["index"]))
            if not f:
                continue
            progress = float(f.get("progress") or 0)
            remaining = left(d)
            share = speed * remaining / waiting
            mbit = round(share * 8 / 1e6, 1)
            # the rate as it reads now for the rate, the settled one for the time
            # left: a number that bounces is honest about a swarm, a time left that
            # bounces is no use to anybody
            steady = settled * remaining / waiting
            eta = int(remaining / steady) if steady > 0 and remaining > 0 else None
            if (abs(progress - float(d.get("progress") or 0)) > 0.0001
                    or mbit != d.get("mbit") or eta != d.get("eta")):
                d["progress"] = round(progress, 4)
                d["mbit"] = mbit
                d["eta"] = eta
                changed = True
            if progress >= 1.0 and d["state"] != "done":
                d["state"] = "done"
                d["done"] = int(time.time())
                d["mbit"] = 0.0
                d["eta"] = None
                STATE["scan_wanted"] = True
                changed = True
    if changed:
        with LOCK:
            save()
    return True


def _unindex_unfinished():
    """A file the library indexed before its download finished: playable, and hiding the
    film's progress. A scan takes it out, since the walk now passes unfinished files by.
    Asked once a download."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return
    asked = STATE.setdefault("unindexed", set())
    names = []
    for d in load()["downloads"]:
        if d.get("state") in ("queued", "downloading") and d.get("key") not in asked:
            _, film = by_key(d.get("key"))
            if film and film.get("path"):
                names.append((d["key"], os.path.basename(film["path"])))
    if not names:
        return
    con = lib.db()
    try:
        held = [key for key, name in names
                if con.execute("SELECT 1 FROM file WHERE path LIKE ? LIMIT 1",
                               ("%" + name,)).fetchone()]
    finally:
        con.close()
    if held:
        asked.update(held)
        STATE["scan_wanted"] = True


#: when a download that finished but never reached the library last asked for a scan
LOOKED = {"at": 0.0}


def _chase_arrivals():
    """A film that came in while nothing was reading the folders.

    The scan is asked for once, when the download finishes. If the server is stopped
    in the minutes that scan takes - an update, a restart - the asking is lost with
    it: the film sits on the disk, out of the library, and its page says "arriving"
    for as long as anybody cares to look at it. This asks again, at most twice an
    hour, because a scan of a folder holding a thousand-film pack is not cheap.
    """
    if time.time() - LOOKED["at"] < 1800:
        return
    waiting = [d for d in load()["downloads"]
               if d.get("state") == "done"
               and time.time() - float(d.get("done") or 0) > 120]
    if not waiting:
        return
    LOOKED["at"] = time.time()
    if any(not arrived(d.get("key")) for d in waiting[-20:]):
        STATE["scan_wanted"] = True


def _work():
    while True:
        matching = fetching = False
        try:
            _mirror_house()
            _notice_removed()
            read_the_folder()
            refill_packs()
            read_episodes()
            matching = _match_some()
            fetching = _follow_downloads()
            _start_next()
            _guard_packs()
            _name_arrivals()
            _chase_arrivals()
            _unindex_unfinished()
            if STATE["scan_wanted"] and STATE["scan"]:
                if STATE["scan"]():
                    STATE["scan_wanted"] = False
                    STATE["owned"] = None
        except Exception as e:
            STATE["why"] = str(e)[:160]
        # every few seconds while a film is coming in, so its page can show it moving
        # and every twenty seconds while there are packs, so a pack qBittorrent picks up by
        # itself is turned down before it has taken much
        time.sleep(5 if fetching else 15 if matching else 20 if load()["packs"] else 60)
