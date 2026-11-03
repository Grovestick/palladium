"""Films offered from torrent packs, fetched one at a time through qBittorrent.

A pack's films are listed in the library before they are here - greyed, with their
page - and a viewer who asks for one gets that film and nothing else from the pack:
every other file in it is left at "do not download".

State is one JSON file beside the library: the qBittorrent connection, the packs with
what each film was matched to, and every download with who asked for it and when.
"""
import pd_log
import hashlib
import io
import json
import os
import re
import shutil
import threading

import pd_beat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import pd_safe

LOCK = threading.RLock()
STATE = {"root": "", "data": None, "lib": None, "scan": None, "worker": False,
         "owned": None, "owned_at": 0.0, "scan_wanted": False, "why": "",
         # the films on offer, worked out again at most every few seconds: every
         # collection asks, and a page of shelves asks once per shelf
         "offered": None, "offered_at": 0.0, "free": None,
         # the main server counts as present from start-up until it fails to answer:
         # nought here read every restart as the main server being away
         "house_at": time.time()}
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


def _read_state():
    """What is written down, or {} only where nothing ever was.

    A file that is there and cannot be read is never taken for an empty one: asked
    again, then its newest backup, and failing both this raises - an empty state kept
    here is saved over the real one a moment later, which is how the whole download
    history and every pack's settings were lost."""
    path = _path()
    if not os.path.exists(path) and not _backups():
        return {}
    last = None
    for attempt in range(5):
        try:
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            last = e
            time.sleep(0.2)
    for kept in _backups():
        try:
            with io.open(kept, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    raise RuntimeError("torrents.json could not be read: %s" % last)


def _backups():
    """The daily copies of the state, newest first."""
    try:
        folder = os.path.dirname(_path())
        return sorted((os.path.join(folder, n) for n in os.listdir(folder)
                       if n.startswith("torrents.json.") and n.endswith(".bak")),
                      reverse=True)
    except OSError:
        return []


def load():
    with LOCK:
        if STATE["data"] is None:
            if not STATE["root"]:
                # asked before start() has said where the state lives: an answer for
                # whoever asked, and never kept - kept, it was the state from then on,
                # and the first save wrote it over the real file
                return {"config": {}, "packs": [], "downloads": []}
            data = _read_state()
            data.setdefault("config", {})
            data.setdefault("packs", [])
            data.setdefault("downloads", [])
            _episodes_read_again(data)
            STATE["data"] = data
        return STATE["data"]


def _episodes_read_again(data):
    """A pack's file read as a film that names an episode after all - "S06E0013", which
    the reading of the day passed over - made the episode it is. How many."""
    fixed = 0
    for pack in data.get("packs") or []:
        for film in pack.get("films") or []:
            if film.get("kind") == "episode":
                continue
            told = episode_of(film.get("path") or "", pack.get("name") or "")
            if not told:
                continue
            show, made, season, number = told
            film.update({"title": show, "year": made or film.get("year"), "kind": "episode",
                         "season": season, "episode": number, "tmdb": None})
            fixed += 1
    return fixed


def save():
    with LOCK:
        STATE["offered_at"] = 0.0
        STATE["shows_at"] = 0.0
        STATE["by_show_at"] = 0.0
        STATE["unfinished"] = None
        if not STATE["root"] or STATE["data"] is None:
            return
        path = _path()
        # one copy a day of what is about to be replaced, seven kept
        try:
            if os.path.exists(path) and os.path.getsize(path) > 2:
                today = path + "." + time.strftime("%Y%m%d") + ".bak"
                if not os.path.exists(today):
                    shutil.copy2(path, today)
                    for old in _backups()[7:]:
                        os.remove(old)
        except OSError:
            pass
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE["data"], f)
            pd_safe.settle(f)
        os.replace(tmp, path)


# ---------------------------------------------------------------- reading a .torrent

def _dec(b, i) -> tuple:
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
#: four digits too: "S06E0013" is episode 13, and read as nothing it was a film
EPISODE_AT = re.compile(r"(?:^|[^A-Za-z0-9])[sS](\d{1,2})[eE](\d{1,4})(?![0-9])")


#: folders a pack keeps some of its episodes in that are not a programme's name
LOOSE_FOLDERS = {"extras", "extra", "specials", "special", "featurettes", "bonus",
                 "bonusfeatures", "misc", "other"}


def episode_of(path, pack=""):
    """(show, year, season, number) for a file naming an episode, else None.

    The programme's name is read before SxxExx, else from the folders above it, else
    from the pack's own name - where a pack keeps loose episodes in "0 Extras", or
    names its files "S03E07 Title" with the programme said only on the pack.

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
    # no show name before SxxExx: the folders above it, nearest first, passing over
    # one that is only a place for loose files
    for folder in reversed(parts[:-1]):
        if show:
            break
        if re.sub(r"[^a-z]+", "", folder.lower()) in LOOSE_FOLDERS:
            continue
        show, year = name_and_year(EPISODE_AT.split(folder)[0])
    if not show and pack:
        # the pack's name, up to its seasons: "Showname- Holiday Specials
        # S01-S36 COMPLETE" is Showname; what follows a dash is the collection's
        cut = re.split(r"(?i)[ ._]+(?:s\d{1,2}(?:[ ._-]*s?\d{1,2})?|season|complete)\b",
                       pack)[0]
        cut = re.split(r"\s*-\s+", cut)[0]
        show, year = name_and_year(cut)
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

def _folder_rank(folder):
    """A folder sorts by the number in front of it ("5 - Animations" before
    "13 - Talk Shows"), then by name."""
    first = folder.split("/")[0]
    got = re.match(r"\s*(\d+)\s*-", first)
    return (int(got.group(1)) if got else 9999, folder.lower())


def _plain_folder(part):
    """ "5 - Animations" as "Animations"; a name with no number in front as it is."""
    return re.sub(r"^\s*\d+\s*-\s*", "", part).strip()


def series_layout(files, series):
    """Every video in a pack as one series: each folder a season, in the pack's own
    folder order, its files the episodes in name order. For a site rip, whose shows are
    on no catalogue: one card, and the folders as they were laid out.

    `files` are the torrent's (index, path, size); returns the pack entries."""
    def clean(text):
        return re.sub(r"\s+", " ", str(text).replace("\ufffd", "-")).strip(" -.")

    videos = [f for f in files if f["path"].lower().endswith(VIDEO)]
    folders = sorted({f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "" for f in videos},
                     key=_folder_rank)
    out = []
    for season, folder in enumerate(folders, 1):
        these = sorted((f for f in videos
                        if (f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "") == folder),
                       key=lambda f: [int(t) if t.isdigit() else t.lower()
                                      for t in re.split(r"(\d+)", f["path"])])
        parts = [_plain_folder(p) for p in folder.split("/") if p][1:] or \
                [_plain_folder(p) for p in folder.split("/") if p]
        label = clean(" - ".join(parts))
        # letters and digits only, "&" read as "and": "Hammer & Bolter" and
        # "Hammer And Bolter." are the same words
        flat = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower().replace("&", "and"))
        whole = flat(label)
        for number, f in enumerate(these, 1):
            stem = os.path.splitext(f["path"].rsplit("/", 1)[-1])[0]
            # "Show - Season 1 - 3 - Title": what the folder already says, and the
            # season and number the order gives, left off
            bits = [clean(b) for b in re.split(r"\s+[-\u2013\u2014\ufffd]\s+|\.\s+", stem)]
            bits = [b for b in bits if b]
            keep = [b for b in bits if flat(b) and flat(b) not in whole
                    and not re.fullmatch(r"(?i)season\s*\d+|\d+|episode\s*\d+\.?", b)]
            # all of it said by the folder ("Astartes - 1 - Astartes"): its last word
            if not keep:
                keep = [b for b in bits if not re.fullmatch(r"\d+", b)][-1:] or [stem]
            name = clean(" - ".join(keep))
            out.append({"index": f["index"], "path": f["path"], "size": f["size"],
                        "title": series, "year": 0, "kind": "episode",
                        "season": season, "episode": number,
                        "episodeName": (label + ": " + name) if label else name,
                        # the folder it came from: the season's own name
                        "seasonName": label,
                        # on no catalogue: not looked up, and not matched to whatever
                        # programme a search turns up
                        "tmdb": 0, "name": series, "rechecked": True,
                        "series": True, "key": None})
    return out


def season_name(entry):
    """A series pack's season by its folder's name; packs added before the name was kept
    carry it at the front of each episode's name. "" for any other pack."""
    if not entry.get("series"):
        return ""
    said = str(entry.get("seasonName") or "")
    if not said and ": " in str(entry.get("episodeName") or ""):
        said = str(entry["episodeName"]).split(": ", 1)[0]
    return said


def series_seasons(packs, title):
    """{season: name} for a series made of a pack, by its title; empty for the rest."""
    want = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
    out = {}
    for pack in packs or []:
        for one in pack.get("films") or []:
            if one.get("series") and re.sub(r"[^a-z0-9]+", "", str(one.get("title") or "").lower()) == want:
                name = season_name(one)
                if name:
                    out.setdefault(int(one.get("season") or 0), name)
    return out


#: every series pack's season names by flattened title, read again every 30 s: the TV
#: tab asks once per card, and walking every pack's every file for each was slow
_SERIES_NAMES = {"at": 0.0, "map": {}}


def seasons_named(title):
    """series_seasons against the packs this server holds."""
    if time.time() - _SERIES_NAMES["at"] > 30:
        found = {}
        try:
            for pack in load()["packs"]:
                for one in pack.get("films") or []:
                    if one.get("series"):
                        flat = re.sub(r"[^a-z0-9]+", "", str(one.get("title") or "").lower())
                        name = season_name(one)
                        if name:
                            found.setdefault(flat, {}).setdefault(int(one.get("season") or 0), name)
        except Exception:
            found = {}
        _SERIES_NAMES.update(at=time.time(), map=found)
    return dict(_SERIES_NAMES["map"].get(re.sub(r"[^a-z0-9]+", "", str(title or "").lower()), {}))


def series_art(title, season=0):
    """Where a drawn poster for a series pack is fetched: the series, or one season."""
    flat = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
    said = "/art/series/%s/%s" % (flat, ("s%d" % season) if season else "poster")
    # a new address once it wears an official poster: screens keep pictures by address
    own = folded_poster(flat, season) if season else series_logo(flat)
    return said + ("?own=1" if own else "")


def series_by_flat(flat):
    """(title, {season: name}) of the series pack whose flattened title this is."""
    for pack in load()["packs"]:
        for one in pack.get("films") or []:
            if one.get("series") and re.sub(r"[^a-z0-9]+", "", str(one.get("title") or "").lower()) == flat:
                return str(one.get("title")), series_seasons(load()["packs"], one.get("title"))
    return None


def find_told(packs, path):
    """(series, season, episode, name) for a file from a pack added as one series, by
    the end of its path; None for any other file."""
    flat = str(path or "").replace("\\", "/").lower()
    for pack in packs or []:
        for one in pack.get("films") or []:
            if not one.get("series"):
                continue
            inside = str(one.get("path") or "").replace("\\", "/").lower()
            if inside and flat.endswith("/" + inside):
                return (str(one.get("title") or ""), int(one.get("season") or 0),
                        int(one.get("episode") or 0), str(one.get("episodeName") or ""))
    return None


def told(path):
    """find_told against the packs this server holds: the library's hook - and a file of
    a programme held on its own that a series pack carries, filed under that series."""
    try:
        said = find_told(load()["packs"], path)
    except Exception:
        said = None
    if said:
        return said
    got = folded()["paths"].get(os.path.normcase(str(path or "")))
    return tuple(got) if got else None


#: files folded into a series pack's series, and that series' seasons' own posters
FOLDED = {"data": None, "at": 0.0}
#: how often the library is looked through for programmes a series pack carries
FOLD_EVERY = 600


def _folded_path():
    return os.path.join(STATE["root"] or ".", "folded.json")


def folded():
    """{"paths": {path: [series, season, episode, name]}, "seasons": {flat series:
    {season: {"title", "poster"}}}}."""
    if FOLDED["data"] is None:
        try:
            with open(_folded_path(), encoding="utf-8") as f:
                FOLDED["data"] = json.load(f) or {}
        except FileNotFoundError:
            FOLDED["data"] = {}
        except (OSError, ValueError):
            return {"paths": {}, "seasons": {}, "unread": True}   # never written over
        FOLDED["data"].setdefault("paths", {})
        FOLDED["data"].setdefault("seasons", {})
    return FOLDED["data"]


def folded_shows():
    """{tmdb number: {"title", "series", "season"}} of programmes filed under a series pack."""
    return dict(folded().get("shows") or {})


def folded_poster(flat_series, season):
    """The official poster a season of a series pack wears: the programme filed into it,
    else its own show on the series' network. None for a drawn one."""
    data = folded()
    own = ((data["seasons"].get(flat_series) or {}).get(str(int(season))) or {}).get("poster")
    return own or (((data.get("art") or {}).get(flat_series) or {}).get("seasons")
                   or {}).get(str(int(season)))


def series_logo(flat_series):
    """The logo of the network a series pack is, for its main poster; None if unknown."""
    return ((folded().get("art") or {}).get(flat_series) or {}).get("logo")


#: how often a series pack's official pictures are looked for again
ART_EVERY = 7 * 86400


def match_season_posters(seasons, shows):
    """{season: poster} for a series pack's seasons, from shows of its network: the
    longest show name a season's name starts with. "Loremasters - Age of Sigmar" is
    Loremasters; "Blacktalon - Season 2" takes Blacktalon's season 2 where it has one."""
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower().replace("&", "and"))
    out = {}
    for number, name in (seasons or {}).items():
        mine = flat(name)
        best = max((s for s in shows or [] if s.get("poster") and flat(s.get("name"))
                    and mine.startswith(flat(s.get("name")))),
                   key=lambda s: len(flat(s.get("name"))), default=None)
        if not best:
            continue
        told = re.search(r"(?i)season\s*(\d+)", str(name))
        own = (best.get("seasons") or {}).get(int(told.group(1))) if told else None
        out[int(number)] = own or best["poster"]
    return out


def official_art(lib, title, network):
    """The network's logo and its shows' posters for a series pack's seasons."""
    said = lib.tmdb("/network/%d" % int(network), timeout=10) or {}
    shows = []
    for page in range(1, 6):
        got = lib.tmdb("/discover/tv", timeout=10, with_networks=int(network), page=page,
                       sort_by="popularity.desc") or {}
        shows += [{"id": s["id"], "name": s.get("name"), "poster": s.get("poster_path")}
                  for s in got.get("results") or []]
        if page >= int(got.get("total_pages") or 1):
            break
    seasons = seasons_named(title)
    # a season that names its season number: that season's own poster, where it has one
    for s in shows:
        if any(s["name"] and re.search(r"(?i)season\s*\d+", n) and n.lower().startswith(
                s["name"].lower()) for n in seasons.values()):
            try:
                det = lib.tmdb("/tv/%d" % s["id"], timeout=10) or {}
                s["seasons"] = {int(x.get("season_number") or 0): x.get("poster_path")
                                for x in det.get("seasons") or [] if x.get("poster_path")}
            except Exception:
                pass
    posters = match_season_posters(seasons, shows)
    # and the rest by name, taken only from this network
    for number, name in sorted(seasons.items())[:80]:
        if number in posters:
            continue
        try:
            found = (lib.tmdb("/search/tv", timeout=10, query=re.sub(r"[^\w ]+", " ", name)) or {}
                     ).get("results") or []
        except Exception:
            continue
        same = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower().replace("&", "and"))
        for one in found[:3]:
            if not one.get("poster_path"):
                continue
            # the same name exactly, or another name on this network
            if same(one.get("name")) == same(name):
                posters[number] = one["poster_path"]
                break
            try:
                det = lib.tmdb("/tv/%d" % one["id"], timeout=10) or {}
            except Exception:
                continue
            if any(int(n.get("id") or 0) == int(network) for n in det.get("networks") or []):
                posters[number] = one["poster_path"]
                break
    return {"network": int(network), "logo": said.get("logo_path"),
            "seasons": {str(k): v for k, v in posters.items()}, "at": time.time()}


def series_network(lib, title):
    """The network a series pack is, from a programme filed into it: the one named like
    the series. 0 when none is."""
    flat = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
    for number, one in folded_shows().items():
        if one.get("series") != title:
            continue
        try:
            det = lib.tmdb("/tv/%d" % int(number), timeout=10) or {}
        except Exception:
            continue
        for n in det.get("networks") or []:
            if re.sub(r"[^a-z0-9]+", "", str(n.get("name") or "").lower()) == flat:
                return int(n["id"])
    return 0


ARTED = {"at": 0.0}


def _official_art():
    """Each series pack that has a network: its logo and its seasons' official posters,
    looked for weekly."""
    if time.time() - ARTED["at"] < FOLD_EVERY:
        return
    ARTED["at"] = time.time()
    lib = STATE["lib"]() if STATE["lib"] else None
    data = folded()
    if not lib or data.get("unread") or not STATE["root"]:
        return
    art = data.setdefault("art", {})
    changed = False
    for title in sorted({one.get("series") for one in folded_shows().values() if one.get("series")}):
        flat = re.sub(r"[^a-z0-9]+", "", title.lower())
        if time.time() - float((art.get(flat) or {}).get("at") or 0) < ART_EVERY:
            continue
        try:
            network = series_network(lib, title)
            if network:
                art[flat] = official_art(lib, title, network)
                changed = True
        except Exception:
            continue
    if changed:
        tmp = _folded_path() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, _folded_path())
        except OSError:
            pass


def fold_matches(shows, packs):
    """Programmes held on their own whose every episode is one of a series pack's: the
    pack's season named after them, matched episode by episode on the episode's title.

    `shows` are {"id", "title", "episodes": [{"title", "path"}]}; returns
    [(show id, series, {path: (season, episode, name)}, season)]."""
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower().replace("&", "and"))
    series = {}
    for pack in packs or []:
        for one in pack.get("films") or []:
            if not one.get("series"):
                continue
            mine = series.setdefault(str(one.get("title") or ""), {"seasons": {}, "names": {}})
            season = int(one.get("season") or 0)
            mine["seasons"].setdefault(season, flat(season_name(one)))
            name = str(one.get("episodeName") or "").split(": ", 1)[-1]
            mine["names"].setdefault((season, flat(name)),
                                     (int(one.get("episode") or 0), str(one.get("episodeName") or "")))
    out = []
    for show in shows or []:
        mine_flat = flat(show.get("title"))
        eps = [e for e in show.get("episodes") or [] if e.get("path")]
        if not mine_flat or not eps:
            continue
        for title, got in series.items():
            if flat(title) == mine_flat:
                continue                     # the series itself
            near = sorted(s for s, n in got["seasons"].items()
                          if n and (n in mine_flat or mine_flat in n))
            mapping = {}
            for e in eps:
                hit = next(((s, got["names"][(s, flat(e.get("title")))])
                            for s in near if (s, flat(e.get("title"))) in got["names"]), None)
                if not hit or not flat(e.get("title")):
                    mapping = {}
                    break
                mapping[e["path"]] = (hit[0], hit[1][0], hit[1][1])
            if mapping:
                out.append((show["id"], title, mapping, min(s for s, _, _ in mapping.values())))
                break
    return out


def fold_held(lib, now=None):
    """File programmes held on their own under the series pack that carries them: their
    files become that series' episodes, their own poster the season's, and the separate
    card goes. Their viewing goes with each episode. Returns how many were folded."""
    now = now or time.time()
    if now - FOLDED["at"] < FOLD_EVERY:
        return 0
    FOLDED["at"] = now
    data = folded()
    if data.get("unread") or not STATE["root"]:
        return 0
    import pd_tracker
    if not pd_tracker.state().get("asks"):
        return 0                             # the main machine files; a copy follows its rows
    con = lib.db()
    try:
        shows = []
        for r in con.execute("SELECT id, title, poster, tmdb_id FROM item WHERE type='show'"):
            eps = [{"title": e["title"], "path": e["path"], "id": e["id"]} for e in con.execute(
                "SELECT e.id, e.title, f.path FROM episode e JOIN file f ON f.episode_id=e.id "
                "WHERE e.item_id=?", (r["id"],))]
            if eps:
                shows.append({"id": r["id"], "title": r["title"], "poster": r["poster"],
                              "tmdb": r["tmdb_id"], "episodes": eps})
        found = fold_matches(shows, load()["packs"])
        if not found:
            return 0
        by_id = {s["id"]: s for s in shows}
        hook = getattr(lib, "told_by_pack", None)
        for show_id, title, mapping, season in found:
            show = by_id[show_id]
            for path, (s, e, name) in mapping.items():
                data["paths"][os.path.normcase(path)] = [title, s, e, name]
            flat = re.sub(r"[^a-z0-9]+", "", title.lower())
            # which programme it was, so a search for it finds the series it is in now
            # and never offers it to Add again
            if show.get("tmdb"):
                data.setdefault("shows", {})[str(int(show["tmdb"]))] = {
                    "title": show["title"], "series": title, "season": season}
            if show.get("poster"):
                data["seasons"].setdefault(flat, {}).setdefault(
                    str(season), {"title": show["title"], "poster": show["poster"]})
            if not hook:
                continue
            for ep in show["episodes"]:
                try:
                    lib._index_file(con, "show", ep["path"], os.stat(ep["path"]),
                                    reset_probe=False, known=True)
                except OSError:
                    continue
                new = con.execute("SELECT episode_id FROM file WHERE path=?",
                                  (ep["path"],)).fetchone()
                if new and new["episode_id"] and new["episode_id"] != ep["id"]:
                    # what was watched of it, and where somebody stopped, goes with it
                    for table in ("progress", "watchlog", "ending", "subcheck"):
                        try:
                            con.execute("UPDATE OR IGNORE %s SET key=? WHERE key=?" % table,
                                        (new["episode_id"], ep["id"]))
                        except Exception:
                            pass
            left = con.execute("SELECT COUNT(*) AS n FROM file WHERE item_id=?",
                               (show_id,)).fetchone()["n"]
            if not left:
                for table, col in (("episode", "item_id"), ("credit", "item_id"),
                                   ("season_art", "item_id"), ("item", "id")):
                    try:
                        con.execute("DELETE FROM %s WHERE %s=?" % (table, col), (show_id,))
                    except Exception:
                        pass
        con.commit()
    finally:
        con.close()
    tmp = _folded_path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _folded_path())
    except OSError:
        pass
    return len(found)


def _fold_held():
    """Programmes a series pack carries, filed under it."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if lib:
        fold_held(lib)


def add_pack(raw=None, path=None, series=""):
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
        if series:
            films = series_layout(files, series)
            for entry in films:
                entry["key"] = key_for(info_hash, entry["index"])
        for one in ([] if series else files):
            low = one["path"].lower()
            # a file that names a season and an episode is judged as an episode
            floor = EPISODE_BYTES if episode_of(one["path"], name) else FILM_BYTES
            if not low.endswith(VIDEO) or one["size"] < floor or "sample" in low:
                continue
            title, year = film_of(one["path"])
            entry = {"index": one["index"], "path": one["path"], "size": one["size"],
                     "title": title, "year": year,
                     "key": key_for(info_hash, one["index"]), "tmdb": None}
            # episode entries are matched against TMDB TV, not movies
            told = episode_of(one["path"], name)
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


#: how long a magnet's metadata is waited for from the swarm
MAGNET_WAIT = 180


def torrent_of_magnet(magnet, wait=MAGNET_WAIT):
    """A magnet link's torrent file: qBittorrent fetches the metadata from the swarm,
    the .torrent is exported and the torrent taken out again before any film is fetched."""
    found = re.search(r"xt=urn:btih:([0-9a-zA-Z]+)", magnet or "")
    if not found:
        return None, "That is not a magnet link"
    info_hash = found.group(1).lower()
    if len(info_hash) == 32:
        import base64
        info_hash = base64.b32decode(info_hash.upper()).hex()
    data = load()
    if any(p.get("hash") == info_hash for p in data["packs"]):
        return None, "already"
    qb = QB(data["config"])
    held = qb.info(info_hash)
    save_to = os.path.join(save_folder() or STATE["root"], "_magnet")
    if not held:
        qb.add_magnet(magnet, save_to)
    got = None
    try:
        until = time.time() + wait
        started = False
        while time.time() < until:
            one = qb.info(info_hash) or {}
            if one.get("has_metadata") or (int(one.get("total_size") or 0) > 0
                                           and one.get("state") != "metaDL"):
                qb.stop(info_hash)
                got = qb.export(info_hash)
                break
            # a stopped magnet may not ask the swarm: started, with nothing to fetch yet
            if not started and time.time() > until - wait + 15:
                qb.start(info_hash)
                started = True
            time.sleep(2)
    finally:
        if not held:
            try:
                qb.remove(info_hash, files=True)
            except Exception:
                pass
    if not got:
        return None, "No peer sent the metadata within %d s" % wait
    return got, ""


def add_magnet(magnet):
    raw, why = torrent_of_magnet(magnet)
    if why == "already":
        return {"ok": True, "already": True}
    if not raw:
        return {"ok": False, "why": why}
    return add_pack(raw=raw)


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
                floor = (EPISODE_BYTES if episode_of(one["path"], pack.get("name") or "")
                         else FILM_BYTES)
                if not low.endswith(VIDEO) or one["size"] < floor or "sample" in low:
                    continue
                title, year = film_of(one["path"])
                entry = {"index": one["index"], "path": one["path"],
                         "size": one["size"], "title": title, "year": year,
                         "key": key_for(info_hash, one["index"]), "tmdb": None}
                told = episode_of(one["path"], pack.get("name") or "")
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
            if pack.get("episodesRead") == 2:
                continue
            pack["episodesRead"] = 2          # 2: loose folders and the pack's name
            changed = True
            for film in pack.get("films") or []:
                told = episode_of(film.get("path") or "", pack.get("name") or "")
                if not told:
                    continue
                show, made, season, number = told
                # only what reads differently now is asked about again
                if (film.get("kind") == "episode" and film.get("title") == show
                        and film.get("season") == season and film.get("episode") == number):
                    continue
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


#: downloads at the same time unless set otherwise, and the most that can be set
AT_ONCE = 1
AT_ONCE_MOST = 5
#: how often the client is checked for the setting: a restarted or reinstalled
#: qBittorrent forgets nothing, but one set by hand since is put back
AT_ONCE_EVERY = 600


def slots(data):
    """Downloads that may run now: the setting, one unless set otherwise. 0 is no limit."""
    return at_once(data["config"])


def at_once(cfg):
    """Downloads that may run at once, from the settings; 0 is no limit."""
    try:
        n = int((cfg or {}).get("atOnce", AT_ONCE))
    except (TypeError, ValueError):
        n = AT_ONCE
    return max(0, min(AT_ONCE_MOST, n))


def queue_prefs(n):
    """What qBittorrent is told for `n` downloads at once. Its own queue holds the rest,
    whoever added them - a film from a pack, a release from the tracker, one added by
    hand. Seeding is never limited: a torrent stopped for the queue is a torrent the
    tracker stops counting. No limit is the client's own -1."""
    if n <= 0:
        return {"max_active_downloads": -1}
    return {"queueing_enabled": True, "max_active_downloads": int(n),
            "max_active_uploads": -1, "max_active_torrents": -1,
            "dont_count_slow_torrents": False}


def keep_at_once(force=False):
    """The setting put to the client when it differs from what the client has. Returns
    what was changed, {} when nothing was."""
    if not force and time.time() - float(STATE.get("atOnceAt") or 0) < AT_ONCE_EVERY:
        return {}
    STATE["atOnceAt"] = time.time()
    data = load()
    cfg = data["config"]
    want = queue_prefs(slots(data))
    qb = QB(cfg)
    have = qb.preferences()
    changed = {k: v for k, v in want.items() if have.get(k) != v}
    if changed:
        qb.set_preferences(changed)
    return changed


#: the link merge tool on this PC: a proxy that spreads connections over every internet
#: link it has - the LAN and phones on USB - and its own page of what each link is doing
MERGE_AT = "http://127.0.0.1:8899"
#: how often it is looked for: a minute, so a stopped tool never leaves qBittorrent
#: pointed at nothing for longer than that
MERGE_EVERY = 60


def merge_links():
    """The links the merge tool has up and in use; [] when it is not running."""
    try:
        with urllib.request.urlopen(MERGE_AT + "/stats", timeout=2) as answer:
            said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return []
    return [str(l.get("name") or "") for l in said.get("links") or []
            if l.get("use") and not l.get("down")]


def merge_stats():
    """What the link merge tool is doing now: each link's name, speed over the last
    three seconds and total, and the sum - for Now playing. running is False when the
    tool does not answer."""
    try:
        with urllib.request.urlopen(MERGE_AT + "/stats", timeout=2) as answer:
            said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        return {"running": False, "links": [], "mbit": 0.0, "bytes": 0,
                "through": bool(STATE.get("merging"))}
    return merge_read(said, bool(STATE.get("merging")))


def merge_read(said, through=False):
    """The tool's own answer read into link names, speeds and totals: its history holds
    a second a row, the time first and then each link's Mbit/s in link order."""
    links = said.get("links") or []
    recent = (said.get("history") or [])[-3:]
    out = []
    for i, link in enumerate(links):
        speeds = [float(h[i + 1]) for h in recent if isinstance(h, list) and len(h) > i + 1]
        out.append({"name": str(link.get("name") or ""),
                    "mbit": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
                    "bytes": int(link.get("bytes") or 0),
                    "active": int(link.get("active") or 0),
                    "up": bool(link.get("use")) and not link.get("down")})
    return {"running": True, "links": out, "through": through,
            "mbit": round(sum(l["mbit"] for l in out), 1),
            "bytes": sum(l["bytes"] for l in out)}


def merge_prefs(on):
    """qBittorrent's proxy for the merge tool, or none. Peers and trackers both: the
    client has one switch for its BitTorrent traffic."""
    if not on:
        return {"proxy_type": "None", "proxy_bittorrent": False,
                "proxy_peer_connections": False}
    return {"proxy_type": "HTTP", "proxy_ip": "127.0.0.1",
            "proxy_port": int(MERGE_AT.rsplit(":", 1)[1]),
            "proxy_auth_enabled": False, "proxy_bittorrent": True,
            "proxy_peer_connections": True, "proxy_misc": False, "proxy_rss": False}


#: states in which a torrent is taking something in
FETCHING = ("downloading", "forcedDL", "metaDL")
#: how long after the last download the merge tool stays in use, so a torrent going
#: between downloading and stalled does not switch qBittorrent's proxy every minute
MERGE_LINGER = 300


def merge_wanted(cfg, links, fetching=True):
    """Through the merge tool when it has a link up - even one: the LAN gone, the tool
    still has the phones. On: always. Auto: only while something downloads, since
    through it qBittorrent takes no incoming connections and a seeder lives on those.
    Off until somebody turns it on."""
    mode = cfg.get("useMerge")
    if not links:
        return False
    return mode is True or (mode == "auto" and bool(fetching))


def fetching_now(states, now, last):
    """(whether downloads go through the merge tool now, when one was last seen): on
    while anything downloads and for MERGE_LINGER after."""
    if any(s in FETCHING for s in states):
        return True, now
    return now - float(last or 0) < MERGE_LINGER, last


def keep_merge(force=False):
    """Every minute: qBittorrent through the merge tool while it runs, straight out when
    it does not. Returns what was changed, {} when nothing was."""
    if not force and time.time() - float(STATE.get("mergeAt") or 0) < MERGE_EVERY:
        return {}
    STATE["mergeAt"] = time.time()
    cfg = load()["config"]
    links = merge_links()
    STATE["mergeLinks"] = links
    qb = QB(cfg)
    try:
        states = qb.states().values()
    except Exception:
        states = []
    fetching, STATE["fetchedAt"] = fetching_now(states, time.time(), STATE.get("fetchedAt"))
    want = merge_prefs(merge_wanted(cfg, links, fetching))
    have = qb.preferences()
    changed = {k: v for k, v in want.items() if have.get(k) != v}
    if changed:
        qb.set_preferences(changed)
    STATE["merging"] = want["proxy_type"] == "HTTP"
    # the server's own calls only while the LAN is down: through the tool otherwise,
    # the tracker's address check went out over a phone and saw this machine on
    # another line than the cache, and stopped every torrent
    merge_own_calls(merge_own_wanted(STATE["merging"], links))
    return changed


def merge_own_wanted(merging, links):
    """The server's own calls through the merge tool: only when it is in use and the
    LAN is not among its links - lost, so the phones are all there is."""
    return bool(merging) and bool(links) and "LAN" not in links


#: always asked directly: the address check the tracker guard makes, which must see the
#: line this machine really is on, and the tracker itself
DIRECT_HOSTS = ("api.ipify.org", "torrentleech.org", "tleechreload.org")


def _in_the_house(host):
    """A machine on this network, or this one, or a host asked directly whatever the merge
    tool does."""
    import ipaddress
    host = str(host or "").split(":")[0].strip("[]").lower()
    if host in ("", "localhost") or host.endswith(".local") or host.endswith(".lan"):
        return True
    if any(host == h or host.endswith("." + h) for h in DIRECT_HOSTS):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


class _MergeProxy(urllib.request.ProxyHandler):
    """The server's own calls out to the internet through the merge tool; the house's
    own machines - qBittorrent, the cache server, the receiver - straight."""

    def proxy_open(self, req, proxy, type):
        if _in_the_house(req.host):
            return None
        return urllib.request.ProxyHandler.proxy_open(self, req, proxy, type)


def merge_own_calls(on):
    """Palladium's own calls - the tracker, the catalogue, subtitles - through the merge
    tool while downloads go through it, so the LAN going down does not stop them."""
    if bool(STATE.get("ownMerged")) == bool(on):
        return
    if on:
        at = MERGE_AT
        urllib.request.install_opener(urllib.request.build_opener(
            _MergeProxy({"http": at, "https": at})))
    else:
        urllib.request.install_opener(urllib.request.build_opener())
    STATE["ownMerged"] = bool(on)


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
        # useMerge: qBittorrent through the link merge tool while it runs
        if "useMerge" in body:
            cfg["useMerge"] = "auto" if body["useMerge"] == "auto" else bool(body["useMerge"])
            STATE["mergeAt"] = 0.0             # put to the client on the next pass
        # atOnce: how many downloads run at the same time; 0 is no limit
        if "atOnce" in body:
            try:
                cfg["atOnce"] = max(0, min(AT_ONCE_MOST, int(body["atOnce"])))
            except (TypeError, ValueError):
                pass
            STATE["atOnceAt"] = 0.0            # put to the client on the next pass
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


def room_for_guest(obj):
    """A reply as a guest gets it: a "free" (GB on the download drive) is the owner's
    to see, kept only where a "size" (bytes) beside or under it does not fit in it."""
    def sizes(o):
        if isinstance(o, dict):
            got = [o["size"]] if isinstance(o.get("size"), (int, float)) else []
            return got + [s for v in o.values() for s in sizes(v)]
        if isinstance(o, list):
            return [s for v in o for s in sizes(v)]
        return []

    def walk(o):
        if isinstance(o, list):
            return [walk(v) for v in o]
        if not isinstance(o, dict):
            return o
        out = {k: walk(v) for k, v in o.items()}
        free = o.get("free")
        if isinstance(free, (int, float)) and not isinstance(free, bool):
            if not any(s / 1e9 > free for s in sizes(o)):
                out["free"] = None
        return out
    return walk(obj)


def free_cached():
    was = STATE["free"]
    if was and time.time() - was[0] < 30:
        return was[1]
    gb = free_gb(save_folder())
    STATE["free"] = (time.time(), gb)
    return gb


def films_for(title, year=0):
    """Every copy of one film that a pack here carries: [{key, name, size, hash}]."""
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    want = flat(title)
    if not want:
        return []
    out = []
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") == "episode":
                continue
            if want not in (flat(film.get("title")), flat(film.get("name"))):
                continue
            if year and film.get("year") and abs(int(film["year"]) - int(year)) > 1:
                continue
            out.append({"key": film.get("key"), "hash": pack.get("hash"),
                        "name": os.path.basename(str(film.get("path") or "")),
                        "size": int(film.get("size") or 0)})
    return out


def episodes_for(show, season, number):
    """Every copy of one episode a pack here carries: [{key, name, size, hash, chosen}],
    chosen meaning the pack is one picked for the programme under Series."""
    want = _plain(show)
    if not want:
        return []
    picked = set(show_choice(_pack_spelling(show))["packs"])
    out = []
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") != "episode":
                continue
            if want not in (_plain(film.get("title")), _plain(film.get("name"))):
                continue
            if (int(film.get("season") or -1), int(film.get("episode") or -1)) != (
                    int(season), int(number)):
                continue
            out.append({"key": film.get("key"), "hash": pack.get("hash"),
                        "name": os.path.basename(str(film.get("path") or "")),
                        "size": int(film.get("size") or 0),
                        "chosen": pack.get("hash") in picked})
    return out


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


def eta_words(seconds):
    """Time left, said short: "40 s", "6 min", "1 h 5 min"."""
    s = int(seconds)
    if s < 60:
        return "%d s" % max(1, s)
    if s < 3600:
        return "%d min" % (s // 60)
    return "%d h %d min" % (s // 3600, s % 3600 // 60)


#: seconds a check may sit still before it is said to be waiting for another
CHECK_STILL = 60


def check_state(info_hash, progress, now):
    """"checkingDL", or "checkWaiting" for a pack whose check has not moved for
    CHECK_STILL seconds: qBittorrent reads one pack at a time and says "checking" of the
    ones waiting too."""
    seen = STATE.setdefault("checkSeen", {})
    was = seen.get(info_hash)
    if was is None or was[0] != progress:
        seen[info_hash] = (progress, now)
        return "checkingDL"
    return "checkWaiting" if now - was[1] >= CHECK_STILL else "checkingDL"


def stage_words(state, qb_state="", progress=0.0, mbit=0.0, eta=None, place=0, why=""):
    """What a download is doing, as (stage, the line every screen shows). One wording for
    the web and the app: each screen made up its own and said "Downloading 0%" for a
    torrent still looking for seeders, checking its files, or stopped on an error."""
    pct = "%d%%" % int(max(0.0, min(1.0, float(progress or 0))) * 100)
    qs = str(qb_state or "")
    if why and state in ("downloading", "unpacking"):
        return "stuck", why
    if state == "unpacking":
        return "unpacking", "Unpacking " + pct
    if state == "done":
        return "arriving", "Downloaded · adding it to the library"
    if state == "queued":
        return "queued", "Queued" + (" · %d ahead" % place if place else "")
    if state != "downloading":
        return "", ""
    if qs == "checkWaiting":
        return "queued", "Waiting for another pack's check"
    if qs.startswith("checking"):
        return "checking", "Checking the files " + pct
    if qs in ("metaDL", "forcedMetaDL"):
        return "finding", "Fetching the torrent"
    if qs.startswith("queued"):
        return "queued", "Waiting its turn in qBittorrent"
    if float(mbit or 0) <= 0:
        return (("finding", "Looking for seeders") if float(progress or 0) <= 0
                else ("waiting", "Waiting for seeders · " + pct))
    return "downloading", ("Downloading %s · %.0f Mbit/s" % (pct, float(mbit)) +
                           (" · %s left" % eta_words(eta) if eta is not None and int(eta) >= 0 else ""))


def coming_of(key, keys=()):
    """This release's download, or another release's of the same film while it is queued or
    coming in: the page opens on one release and the picker can fetch another."""
    own = latest_download(key) or {}
    if own.get("state") in ("queued", "downloading"):
        return own
    for other in keys:
        got = latest_download(other) or {}
        if other != key and (got.get("state") in ("queued", "downloading") or
                             # in and not in the library yet: Arriving, not Download
                             (got.get("state") == "done"
                              and time.time() - float(got.get("when") or 0) < 86400)):
            return got
    return own


def release_keys(key):
    """Every pack release of the film this key is one release of."""
    _, film = by_key(key)
    if not film or film.get("kind") == "episode":
        return [key]
    same = _same(film)
    return [f["key"] for p in load()["packs"] for f in (p.get("films") or []) if _same(f) == same]


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
    labels = [edition(film) or "Standard" for film, _ in group]
    for (film, _), label in zip(group, labels):
        got = latest_download(film["key"]) or {}
        # two plain releases both read "Standard": the release group tells them apart
        if labels.count(label) > 1:
            base = os.path.splitext(os.path.basename(film.get("path") or ""))[0]
            if "-" in base:
                label += " " + base.rsplit("-", 1)[1]
        out.append({"key": film["key"], "label": label,
                    "file": os.path.basename(film.get("path") or ""),
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
    got = coming_of(film["key"], [v["key"] for v in versions])
    stage, words = stage_words(got.get("state") or "", got.get("qb") or "",
                               got.get("progress") or 0, got.get("mbit") or 0, got.get("eta"),
                               _place(got.get("key") or film["key"])
                               if got.get("state") == "queued" else 0, got.get("why") or "")
    return {
        "ratingKey": film["key"], "type": "movie", "title": name,
        # the catalogue's number for it, which is what its cast is asked by
        "tmdb": film.get("tmdb") or 0,
        "titleSort": re.sub(r"^(the|a|an) ", "", name.lower()),
        "year": year or None,
        "genres": list(film.get("genres") or []),
        "summary": film.get("overview") or "",
        "rating": film.get("rating"),
        "duration": int(film.get("runtime") or 0) * 60000,
        "thumb": "/art/%s/poster" % film["key"] if film.get("poster") else None,
        "art": "/art/%s/backdrop" % film["key"] if film.get("backdrop") else None,
        # asked for, it is added from that moment: Recently added shows it coming in -
        # and while it waits, in the order of the queue rather than newest asked first,
        # which drew the queue backwards: the one downloading, then the next in line
        "addedAt": (int(time.time()) - _place(film["key"])
                    if got.get("state") in ("queued", "downloading")
                    else int(got.get("when") or 0) if got.get("state") == "done"
                    else int(pack.get("added") or 0)),
        "viewCount": 0,
        "maxHeight": 1080 if "1080p" in film.get("path", "").lower() else 0,
        "offered": True,
        "offer": {"size": int(film.get("size") or 0),
                  "state": got.get("state") or "",
                  "progress": float(got.get("progress") or 0),
                  # while it comes in: megabits a second towards this film, and seconds left
                  "mbit": float(got.get("mbit") or 0),
                  "eta": got.get("eta"),
                  # what it is doing, in the words every screen shows
                  "stage": stage, "words": words,
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
    """Stable key for an offered programme: "os" + sha1(name)[:10].

    By the name a pack spells it with, whichever spelling asks: the library's
    "Dr. Clock" and a pack's "Dr Clock" are one programme, and as two keys the
    library's row never got the pack's episodes and the shelf stood both."""
    name = _pack_spelling(name)
    return "os" + hashlib.sha1(str(name or "").strip().lower().encode("utf-8")
                               ).hexdigest()[:10]


def _pack_spelling(name):
    """The name the packs give a programme spelled the same in letters and digits."""
    if time.time() - STATE.get("spellings_at", 0) > 30:
        names = {}
        try:
            for pack in load()["packs"]:
                for f in pack.get("films") or []:
                    if f.get("kind") == "episode":
                        said = str(f.get("name") or f.get("title") or "").strip()
                        if said:
                            names.setdefault(_plain(said), said)
        except Exception:
            pass
        STATE["spellings"], STATE["spellings_at"] = names, time.time()
    return (STATE.get("spellings") or {}).get(_plain(name), name)


def _plain(name):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def show_choice(name):
    """The packs chosen to carry one programme, and which of them has each season
    where two of them overlap. An older single choice reads as a list of one."""
    said = (load()["config"].get("showPacks") or {}).get(_plain(name))
    if isinstance(said, dict):
        return {"packs": [str(h) for h in said.get("packs") or [] if h],
                "season": {str(k): str(v) for k, v in (said.get("season") or {}).items()
                           if v}}
    old = (load()["config"].get("showPack") or {}).get(_plain(name))
    return {"packs": [str(old)] if old else [], "season": {}}


def _put_choice(name, choice):
    data = load()
    with LOCK:
        kept = data["config"].setdefault("showPacks", {})
        if choice["packs"] or choice["season"]:
            kept[_plain(name)] = choice
        else:
            kept.pop(_plain(name), None)
        (data["config"].get("showPack") or {}).pop(_plain(name), None)
    save()


def show_pack(name):
    """The pack chosen last for a programme, or nothing."""
    packs = show_choice(name)["packs"]
    return packs[-1] if packs else ""


def add_show_pack(name, info_hash):
    """One more pack chosen for a programme; picked again, it moves to the end,
    which is what wins where two chosen packs carry the same season."""
    choice = show_choice(name)
    choice["packs"] = [h for h in choice["packs"] if h != info_hash] + [str(info_hash)]
    _put_choice(name, choice)


def drop_show_pack(name, info_hash):
    choice = show_choice(name)
    choice["packs"] = [h for h in choice["packs"] if h != info_hash]
    choice["season"] = {k: v for k, v in choice["season"].items() if v != info_hash}
    _put_choice(name, choice)


def carries_others(pack, plain):
    """Whether a pack holds anything but this programme's episodes: one pack for many titles
    is not one programme's to take away."""
    return any(f.get("kind") != "episode" or _plain(f.get("name") or f.get("title")) != plain
               for f in pack.get("films") or [])


def unlink_pack(name, info_hash):
    """One pack taken off a programme: its link goes, unless an episode was downloaded
    from it or another programme chose it. The torrent and its files are never touched."""
    drop_show_pack(name, info_hash)
    data = load()
    plain = _plain(name)
    others = set()
    for k, v in (data["config"].get("showPacks") or {}).items():
        if k != plain:
            others |= set(str(h) for h in ((v.get("packs") or []) if isinstance(v, dict) else [v]) if h)
    used = _came_from(data["downloads"])
    if info_hash in used:
        return {"ok": True, "kept": "an episode was downloaded from it - it stays linked"}
    if info_hash in others:
        return {"ok": True, "kept": "another series uses it - it stays linked"}
    if any(p.get("hash") == info_hash and carries_others(p, plain) for p in data["packs"]):
        return {"ok": True, "kept": "it carries other titles too - it stays linked"}
    remove_pack(info_hash)
    return {"ok": True}


def packs_free(name, config, downloads):
    """The packs chosen for one programme that can go with it: not one another programme
    chose, and not one a download came from."""
    plain = _plain(name)

    def chosen(v):
        if isinstance(v, dict):
            return [str(h) for h in v.get("packs") or [] if h]
        return [str(v)] if v else []
    packs = config.get("showPacks") or {}
    mine = chosen(packs.get(plain)) + chosen((config.get("showPack") or {}).get(plain))
    others = {h for k, v in packs.items() if k != plain for h in chosen(v)}
    used = _came_from(downloads)
    return [h for h in dict.fromkeys(mine) if h not in others and h not in used]


def _came_from(downloads):
    """The packs an episode or film really came from: a download cancelled or failed
    brought nothing, and counting it kept a wrong pack linked to a series for good."""
    return {str(d.get("hash")) for d in downloads or []
            if d.get("hash") and d.get("state") not in ("cancelled", "failed")}


def remove_show(name):
    """A programme's packs dropped with it: links only, the torrents and files stay."""
    data = load()
    going = packs_free(name, data["config"], data["downloads"])
    for h in going:
        remove_pack(h)
    _put_choice(name, {"packs": [], "season": {}})
    return {"ok": True, "packs": len(going)}


def set_season_pack(name, season, info_hash):
    """Which chosen pack one season comes from, where more than one carries it."""
    choice = show_choice(name)
    if info_hash:
        choice["season"][str(int(season))] = str(info_hash)
    else:
        choice["season"].pop(str(int(season)), None)
    _put_choice(name, choice)


def set_show_pack(name, info_hash):
    """Older callers: an empty hash forgets every choice, a hash adds one."""
    if info_hash:
        add_show_pack(name, info_hash)
    else:
        _put_choice(name, {"packs": [], "season": {}})


def seeders(hashes):
    """Seeders for packs added here, from the client: {hash: count}, empty on failure."""
    hashes = [h for h in hashes if h]
    if not hashes:
        return {}
    try:
        return QB(load()["config"]).swarm(hashes)
    except Exception:
        return {}


def shows_with_packs():
    """Every series a pack added here carries episodes of, by its name in the pack."""
    out = set()
    for pack in load()["packs"]:
        if re.search(r"[sS]\d{1,2}[eE]\d{1,3}", str(pack.get("name") or "")):
            continue                        # one episode, not a pack
        for f in pack.get("films") or []:
            if f.get("kind") == "episode":
                out.add(str(f.get("name") or f.get("title") or ""))
    return sorted(n for n in out if n)


def packs_for_show(name):
    """The packs carrying episodes of one programme, with the seasons each has."""
    want = _plain(name)
    out = []
    for pack in load()["packs"]:
        seasons = sorted({int(f.get("season") or 0) for f in pack.get("films") or []
                          if f.get("kind") == "episode"
                          and _plain(f.get("name") or f.get("title")) == want
                          and f.get("season")})
        if seasons:
            out.append({"hash": pack.get("hash"), "name": pack.get("name"),
                        "seasons": seasons})
    return out


def _lines_of(film):
    """The picture height a pack file's name says, 0 where it says none."""
    got = re.search(r"(2160|1080|720|576|480)[pi]", str(film.get("path") or ""), re.I)
    return int(got.group(1)) if got else 0


def _episodes_by_show():
    """Every episode a pack holds that the library does not, by programme.

    Where a pack has been chosen for a programme, the seasons it carries come from
    it alone: two packs of one series otherwise offered every episode twice, and
    which one a download came from was whichever happened to be listed first. The
    seasons it does not carry still come from whatever pack has them - a series is
    often three packs, and choosing one must not hide the other two.
    """
    # kept ten seconds, as the offered films and programmes are: a series' season list
    # asked for it three times a season, 50 ms a time - 2.8 s for eighteen seasons
    if STATE.get("by_show") is not None and time.time() - STATE.get("by_show_at", 0) < 10:
        return STATE["by_show"]
    out = _episodes_by_show_now()
    STATE["by_show"] = out
    STATE["by_show_at"] = time.time()
    return out


def _episodes_by_show_now():
    owned = _owned()
    out = {}
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") != "episode" or held_here(film, owned):
                continue
            name = str(film.get("name") or film.get("title") or "").strip()
            if name:
                out.setdefault(name, []).append((film, pack))
    for name, holds in list(out.items()):
        choice = show_choice(name)
        present = {p.get("hash") for _, p in holds}
        chosen = [h for h in choice["packs"] if h in present]
        # for each season, the pack it comes from: the one picked for that season,
        # otherwise the chosen pack picked last that carries it, otherwise the pack
        # carrying most of the season - the sharper picture where two carry as much
        carries = {}
        for f, p in holds:
            carries.setdefault(int(f.get("season") or 0), {}).setdefault(
                p.get("hash"), []).append(f)
        source = {}
        for season, packs in carries.items():
            picked = choice["season"].get(str(season))
            if picked in packs:
                source[season] = picked
                continue
            mine = next((h for h in reversed(chosen) if h in packs), None)
            if mine:
                source[season] = mine
                continue
            source[season] = max(packs, key=lambda h: (
                len({int(f.get("episode") or 0) for f in packs[h]}),
                max(_lines_of(f) for f in packs[h])))
        # One of each episode. Two packs of one series without a choice made listed
        # every episode twice; an episode the season's pack does not carry still
        # comes from whichever pack has it.
        best = {}
        for f, p in holds:
            at = (int(f.get("season") or 0), int(f.get("episode") or 0))
            rank = (p.get("hash") == source.get(at[0]), _lines_of(f))
            if at not in best or rank > best[at][0]:
                best[at] = (rank, (f, p))
        out[name] = [fp for _, fp in sorted(best.values(),
                                            key=lambda b: (int(b[1][0].get("season") or 0),
                                                           int(b[1][0].get("episode") or 0)))]
    return out


def searchable(spelt):
    """The films on offer a search may find. A cache offers none while the main server is
    up, so it asks the main server for the ones matching the search."""
    if not house_is_up():
        return offered()
    import urllib.parse
    import pd_follow
    asked = STATE.setdefault("asked_offers", {})
    was = asked.get(spelt)
    if was and time.time() - was[0] < 30:
        return was[1]
    try:
        got = pd_follow.ask(_following(), "/follow/offers?q=" + urllib.parse.quote(spelt),
                            10).get("films") or []
    except Exception:
        got = []
    if len(asked) > 200:
        asked.clear()
    asked[spelt] = (time.time(), got)
    return got


def offered_shows():
    """Offered programmes, shaped as the library shapes a show row."""
    if house_is_up():
        return []                     # the main server offers them while it is up
    # kept ten seconds, as the films are: a page of collections asked for it once per
    # collection, and each ask walked every episode of every pack
    if STATE.get("shows") is not None and time.time() - STATE.get("shows_at", 0) < 10:
        return STATE["shows"]
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
            "thumb": ("/art/%s/poster" % film["key"] if film.get("poster")
                      else series_art(name) if film.get("series") else None),
            "art": "/art/%s/backdrop" % film["key"] if film.get("backdrop") else None,
            "addedAt": max([int(g.get("when") or 0) for g in got] or [0]) or
                       int(holds[0][1].get("added") or 0),
            "maxHeight": 1080 if "1080p" in (film.get("path") or "").lower() else 0,
            "offered": True,
            "offer": {"size": sum(int(f.get("size") or 0) for f, _ in holds),
                      "episodes": len(holds), "seasons": len(seasons),
                      "free": free_cached()},
        })
    STATE["shows"] = sorted(out, key=lambda x: x["titleSort"])
    STATE["shows_at"] = time.time()
    return STATE["shows"]


def offered_seasons(key):
    """Seasons of one offered programme, shaped as library season rows."""
    for name, holds in _episodes_by_show().items():
        if show_key(name) != key:
            continue
        film = holds[0][0]
        by_season = {}
        for f, _ in holds:
            by_season.setdefault(int(f.get("season") or 0), []).append(f)
        def first_aired(these):
            days = sorted(str(f.get("aired") or "")[:10] for f in these if f.get("aired"))
            return days[0] if days else ""
        return [{
            "ratingKey": "%s-s%d" % (key, season), "type": "season",
            # a series pack's season by its folder's name
            "title": season_name(these[0]) or "Season %d" % season, "index": season,
            "leafCount": len(these), "viewedLeafCount": 0,
            "parentRatingKey": key, "parentTitle": name, "grandparentTitle": name,
            "thumb": ("/art/%s/poster" % film["key"] if film.get("poster")
                      else series_art(name, season) if film.get("series") else None),
            "offered": True,
            # the season's own year, not the programme's
            "originallyAvailableAt": first_aired(these),
            "year": int(first_aired(these)[:4] or 0) or None,
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
        # a series made of a pack: its season's poster, official or drawn
        "thumb": ("/art/%s/poster" % film["key"] if film.get("poster")
                  else series_art(show, season) if film.get("series") else None),
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


#: files qBittorrent has not finished, by normalised path, and when that was asked
UNFINISHED = {"at": 0.0, "paths": set(), "read": False}
#: held while one reading of the file lists runs: a reading of 900 torrents outlasted
#: the 30 s between them, a new one started every half minute, and they piled up until
#: the server spent a core and a half on them and answered the screens in seconds
_READING = threading.Lock()
#: held while the first read is under way, so a caller that must wait waits for it
_FIRST_READ = threading.Lock()


def _first_read():
    """The first look at every torrent's files: 285 of them take 5 s, once."""
    with _FIRST_READ:
        if not UNFINISHED["read"]:
            _read_unfinished(wait=True)
            UNFINISHED["at"] = time.time()
            UNFINISHED["read"] = True


def warm_unfinished():
    """Started with the server, so the first title's page does not wait on it. Only
    once start() has said where the state lives: asked before, it read an empty one."""
    def work():
        for _ in range(120):
            if STATE["root"]:
                break
            time.sleep(0.5)
        if STATE["root"]:
            _first_read()
    threading.Thread(target=work, daemon=True).start()


def unfinished(path, wait=True):
    """True while qBittorrent is still writing this file: a torrent not complete holds it
    and its own progress is under 1. A file can sit in the library under its final name
    for the whole of a download, and the copy took it half written. Asked of qBittorrent
    at most every 30 s; if it cannot be asked, nothing is held back.

    Before the first answer is in, a caller that must not get it wrong - the copy -
    waits for it; one that only draws a page (wait=False) is answered at once."""
    now = time.time()
    if not STATE["root"]:
        return False                   # before start(): nothing known, nothing held
    if not UNFINISHED["read"]:
        if wait:
            _first_read()
        elif not _FIRST_READ.locked():
            warm_unfinished()
    elif now - UNFINISHED["at"] > 30:
        UNFINISHED["at"] = now
        # asked behind the request, not in it: a title's page waited two seconds on
        # qBittorrent every half minute. The last answer stands until the new one is in.
        threading.Thread(target=_read_unfinished, daemon=True).start()
    return os.path.normcase(os.path.normpath(str(path or ""))) in UNFINISHED["paths"]


#: each torrent's file list, by hash, with what it was read at: re-read only when the
#: torrent has moved on, so 283 torrents are not asked for every file every half minute
FILE_LISTS = {}


def unfinished_file(progress, priority, path, hollow=None):
    """Whether a torrent's file is no film yet: begun, asked for - or neither, but the
    empty space the client made for it is what stands there, which is what a download
    cancelled before anything came leaves: 26 GB with a name and nothing in it, and the
    library took it for a film. A real file put there from elsewhere still counts."""
    if progress >= 1:
        return False
    if progress > 0 or priority > 0:
        return True
    if hollow is None:
        from pd_library import hollow_file
        hollow = lambda where: os.path.exists(where) and hollow_file(where)
    return bool(hollow(path))


def _read_unfinished(wait=False):
    """One reading at a time; a second asked for while one runs is not started - or,
    with wait, starts once the first is done."""
    if not _READING.acquire(blocking=wait):
        return
    try:
        _read_unfinished_now()
    finally:
        _READING.release()


def _read_unfinished_now():
    """Every file a torrent is still writing, judged file by file across all torrents.

    By file, not by torrent: a pack whose wanted films are all in reads as finished,
    and a film switched off part way inside it - Tenet at 96.3% - is still half a file.
    And a file one torrent has whole is whole, whatever another one listing the same
    path says: a pack with no folder of its own puts its films where single downloads
    of the same release already sit. Nothing of it from qBittorrent and not asked for
    is not counted either: whatever is at that path came from somewhere else."""
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    try:
        qb = QB(load()["config"])
        rows = json.loads(qb._call("/api/v2/torrents/info", timeout=10) or b"[]")
        done, partial = set(), set()
        live = set()
        for t in rows:
            h = str(t.get("hash") or "")
            live.add(h)
            # never took in a byte: added over files already here to seed them, and
            # waiting for or in its check. Those files are whole; it is not writing them.
            # Nor while it is being checked: a recheck reads the files, and their progress
            # climbing from nought took every film of a pack out of the library
            fetched = (int(t.get("downloaded") or 0) > 0
                       and not str(t.get("state") or "").startswith("checking"))
            mark = (round(float(t.get("progress") or 0), 4), int(t.get("amount_left") or 0),
                    int(t.get("size") or 0), str(t.get("state") or "")[:4])
            was = FILE_LISTS.get(h)
            # finished, with a file still short of whole in the list kept: read again.
            # qBittorrent says the torrent is done a moment before its file is, and the
            # torrent then never changes - the film read as unfinished for good, and a
            # scan took it out of the library while it was being watched
            short = was is not None and mark[0] >= 1 and any(
                float(f.get("progress") or 0) < 1 and int(f.get("priority") or 0) > 0
                for f in was[1])
            if was is None or was[0] != mark or short:
                FILE_LISTS[h] = (mark, qb.files(h))
            root = str(t.get("save_path") or "")
            for f in FILE_LISTS[h][1]:
                p = norm(os.path.join(root, str(f.get("name") or "")))
                progress = float(f.get("progress") or 0)
                if progress >= 1:
                    done.add(p)
                elif unfinished_file(progress if fetched else 0,
                                     int(f.get("priority") or 0) if fetched else 0, p):
                    partial.add(p)
        for h in list(FILE_LISTS):
            if h not in live:
                FILE_LISTS.pop(h, None)
        UNFINISHED["paths"] = partial - done
        _keep_unfinished(UNFINISHED["paths"])
    except Exception:
        # unanswered: keep what was known - and after a restart, what was known last
        # time. Holding nothing back let every half-fetched film of a pack into the
        # library whenever qBittorrent was off.
        if not UNFINISHED["paths"]:
            UNFINISHED["paths"] = _kept_unfinished()


def _unfinished_file():
    return os.path.join(STATE["root"] or ".", "unfinished.json")


def _keep_unfinished(paths):
    """The files qBittorrent last said were unfinished, written down."""
    if not STATE["root"]:
        return
    try:
        with open(_unfinished_file() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(sorted(paths), f)
        os.replace(_unfinished_file() + ".tmp", _unfinished_file())
    except OSError:
        pass


def _kept_unfinished():
    """The files last known unfinished, for when qBittorrent cannot be asked."""
    try:
        with open(_unfinished_file(), encoding="utf-8") as f:
            said = json.load(f)
        return {str(p) for p in said} if isinstance(said, list) else set()
    except (OSError, ValueError):
        return set()


def offered_keys(title, season=None):
    """The keys of a programme's episodes that only a pack holds - one season, or all of
    them - in season and episode order: what marking the programme on a collection
    takes beyond the episodes on disk."""
    want = show_key(title)
    for name, holds in _episodes_by_show().items():
        if show_key(name) != want:
            continue
        these = [f for f, _ in holds
                 if season is None or int(f.get("season") or 0) == int(season)]
        these.sort(key=lambda f: (int(f.get("season") or 0), int(f.get("episode") or 0)))
        return [f["key"] for f in these]
    return []


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
    one = item(film, pack, free_cached(), _versions(group))
    # a copy of a film the library holds already: its page can play that one while
    # this one comes in, where it offered nothing to press but Cancel
    one["offer"]["heldKey"] = held_key(film)
    return one


def held_key(film):
    """The library's key for the film this pack file is a copy of, or ""."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib:
        return ""
    con = lib.db()
    try:
        row = None
        if film.get("tmdb"):
            row = con.execute("SELECT id FROM item WHERE type = 'movie' AND tmdb_id = ? LIMIT 1",
                              (int(film["tmdb"]),)).fetchone()
        if row is None and film.get("year"):
            row = con.execute("SELECT id FROM item WHERE type = 'movie' AND lower(title) = ? AND year = ? "
                              "LIMIT 1", (str(film.get("name") or film.get("title") or "").strip().lower(),
                                          int(film["year"]))).fetchone()
        return str(row[0]) if row else ""
    except Exception:
        return ""
    finally:
        con.close()


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
            cookie = (answer.headers.get("Set-Cookie") or "").split(";", 1)[0].strip()
        # 5.x names the cookie QBT_SID_<port> and answers with an empty body; older
        # builds sent SID= and "Ok.". The cookie is what says the login took.
        if "=" not in cookie:
            raise RuntimeError("qBittorrent refused the login")
        self.cookie = cookie

    def version(self):
        return self._call("/api/v2/app/version", timeout=6).decode("utf-8", "replace").strip()

    def info(self, info_hash):
        rows = json.loads(self._call("/api/v2/torrents/info?hashes=" + info_hash) or b"[]")
        return rows[0] if rows else None

    def states(self):
        """Every torrent's state, by hash, in one ask rather than one ask each."""
        rows = json.loads(self._call("/api/v2/torrents/info", timeout=20) or b"[]")
        return {str(t.get("hash") or "").lower(): str(t.get("state") or "")
                for t in rows}

    def swarm(self, hashes):
        """Seeders in each torrent's swarm, as its trackers last said: {hash: count}."""
        rows = json.loads(self._call("/api/v2/torrents/info?hashes=" + "|".join(hashes),
                                     timeout=10) or b"[]")
        # 0 from a torrent that has not asked its tracker yet is not known to be none
        return {str(t.get("hash") or "").lower(): int(t.get("num_complete") or 0)
                for t in rows if int(t.get("num_complete") or -1) > 0}

    def files(self, info_hash):
        return json.loads(self._call("/api/v2/torrents/files?hash=" + info_hash) or b"[]")

    def pieces(self, info_hash):
        """Each piece's state: 0 not held, 1 coming, 2 held."""
        return json.loads(self._call("/api/v2/torrents/pieceStates?hash=" + info_hash) or b"[]")

    def preferences(self):
        return json.loads(self._call("/api/v2/app/preferences", timeout=10) or b"{}")

    def set_preferences(self, prefs):
        self._call("/api/v2/app/setPreferences", {"json": json.dumps(prefs)}, timeout=10)

    def remove(self, info_hash, files=False):
        """Take a torrent out of the client, with what it has written if asked.

        For something fetched whole rather than one film out of a pack: stopping it
        and leaving it there would keep the part-file on the disk for ever.
        """
        self._call("/api/v2/torrents/delete",
                   fields={"hashes": info_hash,
                           "deleteFiles": "true" if files else "false"})
        return True

    def add(self, raw, save_to, hold=True):
        said = self._call("/api/v2/torrents/add", fields={
            "savepath": save_to, "category": "palladium",
            # A pack is added stopped because only one film in it is wanted and the
            # rest have to be turned off first. A film of its own has nothing to turn
            # off, so holding it only left it sitting there doing nothing.
            "stopped": "true" if hold else "false",
            "paused": "true" if hold else "false",
            # every torrent in a folder of its own: without one, two packs' Season 01
            # folders were the same folder
            "contentLayout": "Subfolder"}, upload=raw, timeout=60)
        if b"Fail" in said:
            raise RuntimeError("qBittorrent would not take the torrent")

    def add_magnet(self, magnet, save_to):
        said = self._call("/api/v2/torrents/add", fields={
            "urls": magnet, "savepath": save_to, "category": "palladium",
            "stopped": "true", "paused": "true"}, timeout=30)
        if b"Fail" in said:
            raise RuntimeError("qBittorrent would not take the magnet")

    def add_at(self, raw, location, category, layout="Original"):
        """A torrent added stopped with its files laid out as they are named, under
        location: where its file already is, to be read and then seeded."""
        said = self._call("/api/v2/torrents/add", fields={
            "savepath": location, "category": category or "palladium",
            "stopped": "true", "paused": "true", "contentLayout": layout},
            upload=raw, timeout=60)
        if b"Fail" in said:
            raise RuntimeError("qBittorrent would not take the torrent")

    def export(self, info_hash):
        return self._call("/api/v2/torrents/export?hash=" + info_hash, timeout=30)

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

    def set_location(self, info_hash, location):
        """Move a torrent's files under another folder: a rename on the same drive, a copy
        and a removal across drives - one file either way."""
        self._call("/api/v2/torrents/setLocation", fields={"hashes": info_hash,
                                                           "location": location})

    def all_info(self):
        return json.loads(self._call("/api/v2/torrents/info") or b"[]")

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
    qb = {"ok": False, "version": "", "why": "",
          # the link merge tool: the links it has up, and whether qBittorrent uses it
          "merge": {"links": list(STATE.get("mergeLinks") or []),
                    "on": bool(STATE.get("merging"))}}
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
    # what qBittorrent has stopped on, asked once for all of them
    try:
        states = QB(cfg).states()
    except Exception:
        states = {}
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
                      # qBittorrent has stopped on this one - and whether reading
                      # its files again has already been tried
                      "halted": states.get(str(pack["hash"]).lower(), "")
                                if states.get(str(pack["hash"]).lower(), "") in HALTED
                                else "",
                      "haltedWhy": halted_why(states.get(str(pack["hash"]).lower(), ""),
                                              pack["hash"]),
                      "refused": refused(pack),
                      # fetched and seeded whole, or held to what is asked and ahead
                      "whole": kept_whole(pack["hash"]),
                      "home": home_of(pack["hash"]), "splitFirst": split_first(),
                      "pieceMiB": round((pack.get("pieceBytes") or 0) / 1048576)})
    return {"config": {"url": cfg.get("url") or "http://127.0.0.1:8080",
                       "user": cfg.get("user") or "",
                       "hasPassword": bool(cfg.get("password")),
                       "nextEpisode": bool(cfg.get("nextEpisode")),
                       "atOnce": at_once(cfg),
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


def episode_after(show, season, number):
    """The pack entry for the episode after this one: the next number its season has, and
    failing that the first of the season after. A double episode is listed by its first
    number - "E04E05" as 4 - and looking for 5 jumped a whole season (2026-11-01)."""
    want = str(show or "").strip().lower()
    if not want:
        return None
    best = None
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") != "episode":
                continue
            s, e = int(film.get("season") or 0), int(film.get("episode") or 0)
            if not ((s == int(season) and e > int(number)) or s == int(season) + 1):
                continue
            if not any(str(n or "").strip().lower() == want
                       for n in (film.get("name"), film.get("title"))):
                continue
            if best is None or (s, e) < best[0]:
                best = ((s, e), film)
    return best[1] if best else None


def next_after(show, season, number):
    """The episode after this one, if a pack has it and the library does not.

    The next number in the season, and failing that the first of the season after it -
    which is what "the next episode" means to somebody watching the last one of a season.
    """
    # The one after this, and the first of the season after it only where the season
    # has ended - not where the next number is simply already here. Falling through on
    # "already held" jumped a whole season the moment the next episode had arrived.
    film = episode_after(show, season, number)
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
    said = request(film["key"], token, who or "the next episode", cap_gb, auto=True,
                   reason="next episode after S%02dE%02d, while it plays"
                          % (int(season or 0), int(number or 0)))
    said["key"] = film["key"]
    said["episode"] = "S%02dE%02d" % (int(film.get("season") or 0),
                                      int(film.get("episode") or 0))
    return said


def fetch_ahead(show, season, number, token="me", who="", cap_gb=0.0, most=1, hours=0.0):
    """The episodes after this one, as many as the copy keeps ahead, from their packs.

    One ahead left the machine keeping copies holding the episode on screen and the
    next, whatever it was told to keep: it copies what is on this disk and nothing
    else. Walked forward from here - past episodes already on the disk or on their
    way, which count towards the number - asking for each one that is only in a pack,
    until `most` episodes or `hours` of them are ahead. Asked for in order, so they
    arrive in the order they will be watched.
    """
    if not next_wanted():
        return []
    out, ahead, spent = [], 0, 0.0
    at = (int(season), int(number))
    owned = _owned()
    while ahead < max(1, int(most)):
        film = episode_after(show, at[0], at[1])
        if not film:
            break
        at = (int(film.get("season") or 0), int(film.get("episode") or 0))
        ahead += 1
        spent += float(film.get("duration") or 0) / 3600.0
        if hours and spent > hours:
            break
        if held_here(film, owned):
            continue
        was = latest_download(film["key"]) or {}
        if was.get("state") in ("queued", "downloading") or (
                was.get("state") == "done" and not was.get("off")):
            continue
        said = request(film["key"], token, who or "the next episode", cap_gb, auto=True,
                       reason="episodes ahead of where %s is" % (who or "somebody"))
        said["key"] = film["key"]
        said["episode"] = "S%02dE%02d" % at
        out.append(said)
        if not said.get("ok", True):
            break                       # a limit reached: the rest would say the same
    return out


def drop_own_copied(names):
    """This copy's own downloads of files the main server now lists for it, cancelled.

    Asked for while the main server was away; copying brings them now, and fetching
    them as well is the same file twice.
    """
    names = {str(n).lower() for n in names if n}
    if not names or not _following():
        return 0
    gone = 0
    for row in [d for d in load()["downloads"] if d.get("state") in ("queued", "downloading")]:
        _, film = by_key(row.get("key"))
        base = os.path.basename(str((film or {}).get("path") or "")).lower()
        if base and base in names and cancel(row["key"], "me", owner=True).get("ok"):
            gone += 1
    return gone


def take_over_copy(part, size, name=""):
    """Carry on a half-copied film by downloading it: the half here, checked, and the rest.

    Matched to a pack by its exact size, the name only telling two of one size apart.
    Moved to where the pack would put that file, then asked for like any film: qBittorrent
    checks what is there against the torrent and fetches only the pieces that are not.
    "gone" means no pack carries it - a file of the main server's own - and nothing is
    to be done.
    """
    if not size:
        return {"ok": False, "gone": True}
    here = os.path.getsize(part) if os.path.exists(part) else 0
    base = os.path.basename(name).lower()
    found = []
    for pack in load()["packs"]:
        for film in pack.get("films") or []:
            if int(film.get("size") or 0) == int(size):
                found.append((os.path.basename(str(film.get("path") or "")).lower() == base,
                              pack, film))
    if not found:
        return {"ok": False, "gone": True}
    found.sort(key=lambda x: not x[0])
    _, pack, film = found[0]
    folder = save_folder()
    if not folder:
        return {"ok": False, "why": "no folder to download into"}
    into = os.path.join(folder, *str(film.get("path") or "").split("/"))
    if not os.path.exists(into) and here:
        os.makedirs(os.path.dirname(into), exist_ok=True)
        import shutil
        shutil.move(part, into)
        with LOCK:
            STATE.setdefault("recheck", set()).add(pack["hash"])
    said = request(film["key"], "me", "the copy that was cut short", auto=True,
                   reason="finishing a copy that was cut short")
    return said if said.get("ok", True) else said


def request(key, token, who, cap_gb=0.0, most_gb=0.0, auto=False, reason=""):
    """Fetch one film of a pack for somebody: that file on, everything else off.

    most_gb is the biggest one film they may start, nought for no limit.
    """
    if house_is_up() and not auto:
        # a cache downloads nothing while the main server is up: it is asked there
        import urllib.parse
        import pd_follow
        try:
            return pd_follow.ask(_following(), "/follow/get?" + urllib.parse.urlencode(
                {"key": key, "token": token, "who": who, "cap": cap_gb, "most": most_gb}), 30)
        except Exception as e:
            return {"ok": False, "why": "The main server did not take it: %s" % e}
    pack, film = by_key(key)
    if not film:
        return {"ok": False, "why": "That film is not on offer"}
    # a pack that lives on the copy server is downloaded there, not here; the copy
    # server's own requests (it follows this one) are never sent back
    if not _following() and home_of(pack["hash"]) == "copy":
        # held whole here: the file is moved to the copy, not downloaded there again
        if whole_file(pack["hash"], film["index"]):
            give_to_copy(pack, film, reason or "asked for")
        return send_to_copy(key, token, who, cap_gb, most_gb, auto, reason)
    # split, the copy server first: it comes down there and stays, unless it is whole here
    if (not _following() and home_of(pack["hash"]) == "split" and split_first() == "copy"
            and not whole_file(pack["hash"], film["index"])):
        return send_to_copy(key, token, who, cap_gb, most_gb, auto, reason)
    # at home on both: the copy server fetches it too, and it comes in here as well
    if not _following() and home_of(pack["hash"]) == "both":
        send_to_copy(key, token, who, cap_gb, most_gb, auto, reason)
    # a pack qBittorrent cannot load is not tried, or every press is one more failure
    if refused(pack):
        return {"ok": False, "why": "qBittorrent cannot load this pack: " + refused(pack)}
    data = load()
    # whole on the disk already: a finished download, with nothing to fetch
    try:
        qb = QB(data["config"])
        t = qb.info(pack["hash"])
        if t is not None:
            f = next((f for n, f in enumerate(qb.files(pack["hash"]))
                      if int(f.get("index", n)) == int(film["index"])), None)
            if _whole_here(f, t.get("save_path"), film["size"]):
                with LOCK:
                    was = latest_download(key)
                    if not (was and was.get("state") == "done" and not was.get("off")):
                        data["downloads"].append(_done_row(
                            pack, film, os.path.join(str(t.get("save_path") or ""),
                                                     str(f.get("name") or "")),
                            "whole on the disk"))
                        save()
                STATE["owned"] = None
                return {"ok": True, "already": True, "state": "done", "progress": 1.0}
    except Exception:
        pass                       # qBittorrent not answering: asked for as usual
    # room first: a film that cannot fit is not started
    folder = save_folder()
    free = free_gb(folder)
    if free is not None and film["size"] / 1e9 > free:
        return {"ok": False,
                "why": "Not enough room: %.1f GB free on %s, and this film is %.1f GB"
                       % (free, os.path.splitdrive(folder)[0] or folder, film["size"] / 1e9)}
    if most_gb and film["size"] / 1073741824.0 > float(most_gb):
        return {"ok": False,
                "why": "That one is %.1f GB, and %.0f GB is as much as anybody but the "
                       "owner may fetch at once."
                       % (film["size"] / 1073741824.0, float(most_gb))}
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
               "progress": 0.0, "why": "",
               # fetched ahead of anybody asking - the next episodes, a shuffle's next
               # draws: kept quiet, where a download somebody pressed for is announced
               "auto": bool(auto),
               # why a download nobody pressed for started, for the download log
               "reason": str(reason or "")[:200]}
        if film.get("kind") == "episode":
            # which episode, on the row itself: the title is the programme's name and
            # is the same for all of them
            row.update({"kind": "episode", "season": film.get("season"),
                        "episode": film.get("episode"),
                        "episodeName": film.get("episodeName") or ""})
        # one film at a time: behind another, it waits its turn with its file off
        ahead = sum(1 for d in data["downloads"] if d.get("state") in ("queued", "downloading"))
        # the tracker paused on this machine: asked for, and waiting for it to come back
        if STATE.get("held"):
            ahead = ahead or 1
        if not ahead:
            row.update(state="downloading", started=int(time.time()))
        data["downloads"].append(row)
        save()
    ensure_worker()
    if ahead:
        return {"ok": True, "state": "queued", "why": "", "place": ahead}
    _begin(row, pack, film)
    return {"ok": row["state"] != "failed", "state": row["state"], "why": row["why"]}


#: What a failure to reach qBittorrent looks like, in the words the socket uses.
#: Being unable to reach it is not the same as being unable to do the thing.
ASLEEP = ("winerror 10061", "connection refused", "actively refused",
          "connection aborted", "timed out", "urlopen error", "no connection could be made")


def _waiting_for_qbt(why):
    """True when this went wrong only because qBittorrent was not running."""
    said = str(why or "").lower()
    return any(word in said for word in ASLEEP)


def _begin(row, pack, film):
    """A download whose turn it is: the pack in qBittorrent, that file on.

    qBittorrent being off is not a failed download, it is one that has not started.
    The row stays queued and the worker tries it again - one at a time, oldest first -
    until the client is back. Anything else is a real failure and is said as one.
    """
    try:
        fetch(pack, film)
    except Exception as e:
        why = str(e)[:200]
        if _waiting_for_qbt(why):
            row["state"] = "queued"
            row["why"] = "Waiting for qBittorrent"
            row.pop("started", None)
        else:
            row["state"] = "failed"
            row["why"] = why
    with LOCK:
        save()


def _start_next():
    """As many at a time as the setting says, one unless set otherwise: the request that
    has waited longest starts when one is free."""
    if STATE.get("held"):
        return                             # the tracker is paused on this machine
    with LOCK:
        data = load()
        running = [d for d in data["downloads"] if d.get("state") == "downloading"]
        room = slots(data)
        if room != STATE.get("slotsSaid"):
            STATE["slotsSaid"] = room
            STATE["atOnceAt"] = 0.0        # the client told on the next pass
        row = None
        if not room or len(running) < room:
            row = next((d for d in data["downloads"] if d.get("state") == "queued"), None)
        if row is None and not running:
            # and one that failed only because qBittorrent was not running, which it
            # may be again. Oldest first, like everything else in this queue.
            row = next((d for d in data["downloads"]
                        if d.get("state") == "failed"
                        and _waiting_for_qbt(d.get("why"))), None)
        was = row.get("state") if row else ""
    if row is None:
        return
    with LOCK:
        # started or cancelled by another thread while the lock was free: not started twice,
        # and not brought back
        if row.get("state") != was:
            return
        pack, film = by_key(row.get("key"))
        why = "No longer on offer" if not film else ""
        if not why and refused(pack):
            why = "qBittorrent cannot load this pack: " + refused(pack)
        if why:
            row.update(state="failed", why=why)
        else:
            row.update(state="downloading", started=int(time.time()))
            row["why"] = ""
        save()
    if not why:
        _begin(row, pack, film)


def _back_to_waiting(hashes):
    """The files of downloads put back to waiting switched off in qBittorrent."""
    if not hashes:
        return
    try:
        qb = QB(load()["config"])
        for h in set(hashes):
            only_asked(qb, h)
    except Exception as e:
        STATE["why"] = str(e)[:160]


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
    # a half copy put where the pack keeps it: a torrent already in the client has to
    # be told to look again, or it goes on believing the file is not there
    with LOCK:
        again = pack["hash"] in (STATE.get("recheck") or set())
        (STATE.get("recheck") or set()).discard(pack["hash"])
    # and files on that are already on the disk whole but not yet read - copied in
    # beside the client: started without a look, it downloads them again from the swarm
    # (17.7 GB of films already on the disk, on the copy server, 2026-10-31)
    if held is not None and not again:
        try:
            again = needs_read_first(qb.files(pack["hash"]), held.get("save_path") or "",
                                     load()["downloads"], pack["hash"], film["index"],
                                     read_at=read_started(pack["hash"]))
        except Exception:
            again = True                   # not known: look before fetching
    if again and held is not None:
        note_read(pack["hash"])
        qb.recheck(pack["hash"])
    qb.start(pack["hash"])


def present_unread(files, save_path, exists, size_of):
    """The files short of whole in the client that are on the disk at their full length,
    on or off: there, and not read yet."""
    out = []
    for n, f in enumerate(files or []):
        if float(f.get("progress") or 0) >= 1:
            continue
        path = os.path.join(save_path, str(f.get("name") or "").replace("/", os.sep))
        try:
            if exists(path) and int(size_of(path)) == int(f.get("size") or -1):
                out.append(int(f.get("index", n)))
        except OSError:
            continue
    return out


def note_read(info_hash):
    """When a read of this pack began. It covers all of the pack on the disk, on or off."""
    data = load()
    with LOCK:
        data.setdefault("packReadAt", {})[str(info_hash)] = time.time()
        save()


def read_started(info_hash):
    return float((load().get("packReadAt") or {}).get(str(info_hash)) or 0)


def needs_read_first(files, save_path, downloads, pack_hash, index, share=None, read_at=0.0):
    """Whether a pack is read before a film of it starts: only when that film is on the disk
    whole and has never been read, so it is not fetched again. Every other unread file
    waits: reading a whole pack for them held one download for hours (2026-11-01). A
    placeholder of the right length is not on the disk: asked for, it starts at once. And a
    file unchanged since the last read began has been read, whatever that found: two films
    asked for together read a 700 GB pack twice, 100 minutes each (2026-11-02)."""
    if int(index) not in set(unchecked(files, save_path, os.path.exists, os.path.getsize)):
        return False
    for n, f in enumerate(files or []):
        if int(f.get("index", n)) == int(index):
            path = os.path.join(save_path, str(f.get("name") or "").replace("/", os.sep))
            if read_at and os.path.getmtime(path) < read_at:
                return False
            return (share or on_disk_share)(path) >= ON_DISK_FULL
    return False


def unchecked(files, save_path, exists, size_of):
    """The files on, short of whole in the client, that are on the disk at their full
    length: there, and never read. A recheck finds them; a download would fetch them again."""
    out = []
    for n, f in enumerate(files or []):
        if int(f.get("priority") or 0) <= 0 or float(f.get("progress") or 0) >= 1:
            continue
        path = os.path.join(save_path, str(f.get("name") or "").replace("/", os.sep))
        try:
            if exists(path) and int(size_of(path)) == int(f.get("size") or -1):
                out.append(int(f.get("index", n)))
        except OSError:
            continue
    return out


def adopt(info_hash, indexes):
    """Films of a pack switched on in qBittorrent by hand, made downloads of the owner's:
    shown, timed and finished like any other. Only films - a picture or a note switched
    on is left to qBittorrent. True when any was taken in."""
    data = load()
    pack = next((p for p in data["packs"] if p.get("hash") == info_hash), None)
    if not pack:
        return False
    took = False
    # a finished film switched back on is kept on again: no second download of it
    for d in data["downloads"]:
        if d.get("hash") == info_hash and d.get("state") == "done" and d.get("off")                 and int(d.get("index", -1)) in indexes:
            d.pop("off", None)
            indexes = set(indexes) - {int(d["index"])}
            took = True
    for film in pack.get("films") or []:
        if int(film.get("index", -1)) not in indexes:
            continue
        if not str(film.get("path") or "").lower().endswith((".mkv", ".mp4", ".avi", ".m4v")):
            continue
        row = {"id": uuid.uuid4().hex[:10], "key": film["key"], "hash": info_hash,
               "index": film["index"], "title": film.get("name") or film.get("title"),
               "year": film.get("year"), "size": film["size"], "who": "qBittorrent",
               "token": "me", "when": int(time.time()), "state": "downloading",
               "started": int(time.time()), "progress": 0.0, "why": "", "auto": False,
               "reason": "switched on in qBittorrent"}
        if film.get("kind") == "episode":
            row.update({"kind": "episode", "season": film.get("season"),
                        "episode": film.get("episode"),
                        "episodeName": film.get("episodeName") or ""})
        with LOCK:
            data["downloads"].append(row)
        took = True
    if took:
        save()
    return took


def asked_for(info_hash):
    """The files of one pack that are on: coming in now, or here. One waiting its turn is off."""
    return {int(d["index"]) for d in load()["downloads"]
            if d.get("hash") == info_hash and d.get("state") in ("downloading", "done")
            and not d.get("off")}


def home_of(info_hash):
    """Which machine a pack lives on: "main" - this one - "copy" for the copy server, "both",
    every file kept on each, or "split": each file stays on the machine it came down on and
    nothing moves. Its films are downloaded, kept and seeded there."""
    home = (load().get("home") or {}).get(str(info_hash))
    return home if home in ("copy", "both", "split") else "main"


def split_first():
    """Which machine a split pack's new downloads go to: "copy" or "main"."""
    return "main" if load().get("splitFirst") == "main" else "copy"


def set_split_first(where):
    """The machine a split pack's new downloads go to."""
    if where not in ("main", "copy"):
        return {"ok": False, "why": "no such server"}
    data = load()
    with LOCK:
        data["splitFirst"] = where
        save()
    return {"ok": True, "splitFirst": where}


def set_home(info_hash, home):
    """A pack's home: "copy" sends what is asked of it to the copy server, "both" fetches it
    on each, "split" leaves each file where it came down, "main" keeps it here."""
    info_hash = str(info_hash or "")
    if not info_hash or home not in ("main", "copy", "both", "split"):
        return {"ok": False, "why": "no torrent, or no such home"}
    data = load()
    with LOCK:
        homes = dict(data.get("home") or {})
        if home in ("copy", "both", "split"):
            homes[info_hash] = home
        else:
            homes.pop(info_hash, None)
        data["home"] = homes
        if home == "split":
            # nothing still listed to move: what has not gone stays
            data["forCopy"] = [r for r in data.get("forCopy") or []
                               if not (r.get("move") and r.get("hash") == info_hash)]
        save()
    return {"ok": True, "home": home}


def for_copy():
    """Requests waiting for the copy server to take: [{id, key, token, who, cap, most, when}]."""
    return list(load().get("forCopy") or [])


def copy_took(ids):
    """The requests the copy server has taken, off the list."""
    ids = {str(i) for i in ids or []}
    data = load()
    with LOCK:
        data["forCopy"] = [r for r in data.get("forCopy") or [] if str(r.get("id")) not in ids]
        save()
    return {"ok": True}


def send_to_copy(key, token, who, cap_gb=0.0, most_gb=0.0, auto=False, reason=""):
    """A request for a film whose pack lives on the copy server, kept for it to take on its
    next round. Asked twice, it is listed once."""
    data = load()
    with LOCK:
        # asked for, for sync back: a film somebody pressed for is wanted here too
        asked = data.setdefault("copyAsked", {})
        asked[str(key)] = int(time.time())
        for k in [k for k, at in asked.items() if time.time() - float(at) > ASKED_DAYS * 86400]:
            asked.pop(k, None)
        rows = data.setdefault("forCopy", [])
        if not any(r.get("key") == key for r in rows):
            rows.append({"id": uuid.uuid4().hex[:10], "key": key, "token": token, "who": who,
                         "cap": cap_gb, "most": most_gb, "auto": bool(auto),
                         "reason": str(reason or "")[:200], "when": int(time.time())})
            save()
    # said now, so it starts at once and not at the copy server's next look
    push = STATE.get("pushRequest")
    if push:
        threading.Thread(target=push, name="palladium-push-request", daemon=True).start()
    return {"ok": True, "copy": True, "state": "queued",
            "why": "Downloading on the copy server, where this pack lives"}


def files_here(info_hash):
    """[(index, name, size)] of every file of one torrent on this machine's disk at its full
    length - films and the small files beside them alike - and the part file's
    (path, size), where qBittorrent keeps the pieces of the files nobody asked for."""
    data = load()
    qb = QB(data["config"])
    t = qb.info(info_hash)
    if t is None:
        return [], ("", 0)
    root = str(t.get("save_path") or "")
    out = []
    for n, f in enumerate(qb.files(info_hash)):
        path = os.path.join(root, str(f.get("name") or "").replace("/", os.sep))
        try:
            if os.path.isfile(path) and os.path.getsize(path) == int(f.get("size") or -1):
                out.append((int(f.get("index", n)), str(f.get("name") or ""), int(f.get("size") or 0)))
        except OSError:
            continue
    part = os.path.join(root, "." + str(info_hash) + ".parts")
    return out, (part, os.path.getsize(part) if os.path.isfile(part) else 0)


def give_to_copy(pack, film, reason):
    """A film of a pack at home on the copy server, held here, listed for the copy to move
    in from this server (move: the file itself over the house network, not the swarm). Not
    a request by anybody: it is not marked as wanted back here (copyAsked)."""
    key = film.get("key")
    move = {"move": True, "hash": pack.get("hash"), "index": int(film.get("index", -1)),
            "name": film.get("path"), "size": int(film.get("size") or 0), "title": film.get("title")}
    data = load()
    with LOCK:
        rows = data.setdefault("forCopy", [])
        if not key:
            return False
        was = next((r for r in rows if r.get("key") == key), None)
        if was is not None:
            if was.get("move"):
                return False
            was.update(move)               # listed as a download: the file is here, so moved
        else:
            rows.append(dict({"id": uuid.uuid4().hex[:10], "key": key, "token": "me", "who": "",
                              "cap": 0.0, "most": 0.0, "auto": True, "reason": str(reason)[:200],
                              "when": int(time.time())}, **move))
        save()
    return True


#: how long a film asked for from a pack at home on the copy is wanted back here
ASKED_DAYS = 30


def asked_on_copy():
    """The films asked for from packs at home on the copy server in the last ASKED_DAYS."""
    return set((load().get("copyAsked") or {}).keys())


def whole_file(info_hash, index):
    """Whether this machine holds one file of one pack whole: every piece checked, and the
    file there at its length."""
    data = load()
    try:
        qb = QB(data["config"])
        t = qb.info(info_hash)
        if t is None:
            return False
        f = next((f for n, f in enumerate(qb.files(info_hash)) if int(f.get("index", n)) == int(index)), None)
        return bool(f) and _whole_here(f, t.get("save_path"), int(f.get("size") or 0))
    except Exception:
        return False


def only_edges_short(progress, size, piece):
    """Whether all that is missing of a file is its first and last piece. Those hold bytes of
    the films next to it in the pack, so with them not here the pieces can never be read,
    and the file never reads as whole, however right it is."""
    return bool(piece) and float(progress) >= 1 or (
        bool(piece) and (1 - float(progress)) * int(size) <= 2 * int(piece))


def held_file(info_hash, index):
    """Whether this machine has one pack file, whole or short only of its edge pieces: the
    answer to whether to send it again. Not whether another machine may delete its own."""
    data = load()
    try:
        qb = QB(data["config"])
        t = qb.info(info_hash)
        if t is None:
            return False
        f = next((f for n, f in enumerate(qb.files(info_hash)) if int(f.get("index", n)) == int(index)), None)
        if not f:
            return False
        pack = next((p for p in data["packs"] if p.get("hash") == info_hash), {})
        if float(f.get("progress") or 0) <= 0 or not only_edges_short(f.get("progress") or 0, f.get("size") or 0,
                                                                      int(pack.get("pieceBytes") or 0)):
            return False
        path = os.path.join(str(t.get("save_path") or ""), str(f.get("name") or ""))
        return os.path.getsize(path) == int(f.get("size") or 0) and on_disk_share(path) >= ON_DISK_FULL
    except Exception:
        return False


def whole_files(info_hash):
    """The indexes of every file of one pack this machine holds whole."""
    data = load()
    try:
        qb = QB(data["config"])
        if qb.info(info_hash) is None:
            return []
        return [int(f.get("index", n)) for n, f in enumerate(qb.files(info_hash))
                if float(f.get("progress") or 0) >= 1 and int(f.get("priority") or 0) > 0]
    except Exception:
        return []


#: seconds a machine's list of what its packs carry is kept
PLACES_FOR = 300


def _pack_places():
    """{file name: {path, size, hash, index}} of the video files this machine's packs carry."""
    now = time.time()
    if STATE.get("packPlaces") is not None and now - STATE.get("packPlacesAt", 0) < PLACES_FOR:
        return STATE["packPlaces"]
    places = {}
    data = load()
    qb = QB(data["config"])
    for pack in data["packs"]:
        t = qb.info(pack["hash"])
        if t is None:
            continue
        for n, f in enumerate(qb.files(pack["hash"])):
            name = str(f.get("name") or "")
            if name.lower().endswith(VIDEO_FILE):
                places.setdefault(os.path.basename(name).lower(), {
                    "path": os.path.join(str(t.get("save_path") or ""), name.replace("/", os.sep)),
                    "size": int(f.get("size") or 0), "hash": pack["hash"], "index": int(f.get("index", n))})
    STATE["packPlaces"], STATE["packPlacesAt"] = places, now
    return places


def pack_place(name, size=None):
    """Where a pack on this copy server keeps the file of this name and size, or "": the one
    place a copy of it belongs, seeded and played as one file and never cleared for room."""
    if not _following():
        return ""
    try:
        row = _pack_places().get(os.path.basename(str(name or "")).lower())
    except Exception:
        return ""
    return row["path"] if row and (not size or row["size"] == int(size)) else ""


def in_its_place(path):
    """Whether a pack's file is really on the disk, not a placeholder of the same length."""
    try:
        return os.path.getsize(path) > 0 and on_disk_share(path) >= ON_DISK_FULL
    except OSError:
        return False


def copied_into_pack(name):
    """A copy that landed in its pack: written down, and read by the pack in its turn."""
    row = _pack_places().get(os.path.basename(str(name or "")).lower())
    if row:
        mark_done(row["hash"], row["index"], "moved here: copied for dual stream")
        moved_fresh(row["hash"], row["index"])


def mark_done(info_hash, index, reason):
    """A pack file put in place here written down as a finished download, so the rule that
    switches off what nobody asked for leaves it on."""
    data = load()
    pack = next((p for p in data["packs"] if p.get("hash") == info_hash), None)
    film = next((f for f in (pack or {}).get("films") or [] if int(f.get("index", -1)) == int(index)), None)
    if not film:
        return False
    with LOCK:
        if any(d.get("hash") == info_hash and int(d.get("index", -1)) == int(index)
               and d.get("state") == "done" and not d.get("off") for d in data["downloads"]):
            return False
        data["downloads"].append(_done_row(pack, film, file_of(info_hash, index), reason))
        save()
    STATE["owned"] = None
    return True


#: what may be let go: a film, never anything else a pack carries
LET_GO_ENDINGS = (".mkv", ".mp4", ".avi", ".m4v")


def fingerprint(path):
    """The file's size and a megabyte from its start, middle and end, hashed - read fresh,
    never remembered, so two machines comparing it compare what is on the disk now."""
    import hashlib
    size = os.path.getsize(path)
    digest = hashlib.sha256(str(size).encode())
    lump = 1 << 20
    with open(path, "rb") as f:
        for at in (0, max(0, size // 2 - lump // 2), max(0, size - lump)):
            f.seek(at)
            digest.update(f.read(lump))
    return digest.hexdigest()


def _deep_enough(folder):
    """A folder at least two levels under its drive's root: never a drive, never one folder in."""
    return len([p for p in os.path.splitdrive(folder)[1].split(os.sep) if p]) >= 2


def safe_to_remove(path, save_path, qb_name, film, allowed=()):
    """Why this file may not be removed, or "" when it may: it must be the pack's own file,
    inside the pack's own folder, inside one of the folders allowed for removing (Settings),
    a film, at the length the pack says - and no link."""
    if not path or not save_path or not qb_name or not film:
        return "nothing to go on"
    real = os.path.normcase(os.path.realpath(path))
    inside = [f for f in allowed or [] if str(f).strip() and _deep_enough(os.path.realpath(str(f).strip()))
              and real.startswith(os.path.normcase(os.path.realpath(str(f).strip())) + os.sep)]
    if not inside:
        return "not inside a folder allowed for removing: %s" % path
    root = os.path.realpath(save_path)
    # a drive's root or a folder one level under it is never a pack's own folder
    if not _deep_enough(root):
        return "the pack's folder is too near the top of the drive: %s" % root
    expected = os.path.realpath(os.path.join(save_path, qb_name.replace("/", os.sep)))
    if os.path.normcase(os.path.realpath(path)) != os.path.normcase(expected):
        return "not the file qBittorrent keeps for it: %s" % path
    if not os.path.normcase(expected).startswith(os.path.normcase(root) + os.sep):
        return "outside the pack's folder: %s" % expected
    if os.path.islink(path) or not os.path.isfile(path):
        return "not a plain file: %s" % path
    if not path.lower().endswith(LET_GO_ENDINGS):
        return "not a film: %s" % path
    if os.path.getsize(path) != int(film.get("size") or -1):
        return "not the length the pack gives it: %s" % path
    return ""


def let_go(info_hash, index, same_on_copy, allowed=(), remove=os.remove):
    """One film of a pack at home on the copy server, no longer kept here: switched off in
    this machine's copy of the pack, its finished download marked off, and the file removed.

    Every guard is asked at the moment of removing, and any one of them stops it: the pack
    lives on the copy server; this machine has every piece of the file checked; the file is
    the pack's own, inside the pack's own folder (safe_to_remove); and same_on_copy(path,
    fingerprint) - the copy server asked now, not earlier - holds the same file whole.
    Returns {"ok", "why", "path"}."""
    if home_of(info_hash) != "copy":
        return {"ok": False, "why": "its pack does not live on the copy server"}
    data = load()
    pack = next((p for p in data["packs"] if p.get("hash") == info_hash), None)
    film = next((f for f in (pack or {}).get("films") or [] if int(f.get("index", -1)) == int(index)), None)
    try:
        qb = QB(data["config"])
        t = qb.info(info_hash)
        f = next((f for n, f in enumerate(qb.files(info_hash)) if int(f.get("index", n)) == int(index)), None)
    except Exception as e:
        return {"ok": False, "why": "qBittorrent did not answer: %s" % e}
    if t is None or f is None or film is None:
        return {"ok": False, "why": "not in this machine's copy of the pack"}
    save_path, qb_name = str(t.get("save_path") or ""), str(f.get("name") or "")
    path = os.path.join(save_path, qb_name.replace("/", os.sep))
    if not _whole_here(f, save_path, film["size"]):
        return {"ok": False, "why": "not whole here", "path": path}
    why = safe_to_remove(path, save_path, qb_name, film, allowed)
    if why:
        return {"ok": False, "why": why, "path": path}
    # one file can be seeded by two torrents - a film taken into its pack keeps its own
    # torrent: removed while that one still seeds it, it is a seeding file gone
    try:
        for other in qb.all_info():
            if other.get("hash") == info_hash:
                continue
            base = os.path.normcase(os.path.normpath(str(other.get("save_path") or ""))) + os.sep
            if not os.path.normcase(path).startswith(base):
                continue
            for g in qb.files(other["hash"]):
                if os.path.normcase(os.path.join(str(other.get("save_path") or ""),
                                                 str(g.get("name") or "").replace("/", os.sep))) \
                        == os.path.normcase(path):
                    return {"ok": False, "why": "another torrent seeds this file: %s"
                            % other.get("name"), "path": path}
    except Exception as e:
        return {"ok": False, "why": "could not ask which torrents use it: %s" % e, "path": path}
    mark = fingerprint(path)
    if not same_on_copy(path, mark):
        return {"ok": False, "why": "the copy server did not confirm the same file whole", "path": path}
    try:
        qb.priority(info_hash, [int(index)], 0)
    except Exception as e:
        return {"ok": False, "why": "could not switch it off: %s" % e, "path": path}
    with LOCK:
        for d in data["downloads"]:
            if d.get("hash") == info_hash and int(d.get("index", -1)) == int(index) and d.get("state") == "done":
                d["off"] = True
        save()
    STATE["owned"] = None
    try:
        remove(path)
    except OSError as e:
        # still here and still whole: back on, or no round would ever try it again
        with LOCK:
            for d in data["downloads"]:
                if d.get("hash") == info_hash and int(d.get("index", -1)) == int(index):
                    d.pop("off", None)
            save()
        try:
            qb.priority(info_hash, [int(index)], 1)
        except Exception:
            pass
        return {"ok": False, "why": "could not remove it: %s" % e, "path": path}
    return {"ok": True, "why": "", "path": path, "mark": mark, "size": film["size"]}


def kept_whole(info_hash):
    """Whether this torrent is fetched and seeded whole: a tracker counts a partial
    download as never seeding."""
    return str(info_hash) in set(load().get("whole") or [])


def keep_whole(info_hash, on=True):
    """Fetch and seed a torrent whole, every file on, or go back to the files asked for."""
    info_hash = str(info_hash or "")
    if not info_hash:
        return {"ok": False, "why": "no torrent"}
    data = load()
    with LOCK:
        kept = [h for h in data.get("whole") or [] if h != info_hash]
        data["whole"] = kept + ([info_hash] if on else [])
        save()
    qb = QB(data["config"])
    try:
        files = qb.files(info_hash)
    except Exception:
        files = []
    if not files:
        # not in qBittorrent yet: the choice is kept and applies once it is added
        return {"ok": True, "whole": on, "later": True}
    if on:
        qb.priority(info_hash, [int(f.get("index", n)) for n, f in enumerate(files)], 1)
        qb.start(info_hash)
    else:
        # back under the rules now, not at the next pass
        only_asked(qb, info_hash, files=files)
    return {"ok": True, "whole": on}


def moved_short(info_hash, files=None):
    """Films this machine was given by the other one and does not hold whole. Nobody asked
    for those to be fetched: switched on, they came from the swarm, a ratio's cost. One
    somebody has since asked for is a download like any other."""
    rows = load()["downloads"]
    asked = {int(d["index"]) for d in rows if d.get("hash") == info_hash
             and d.get("state") in ("downloading", "queued")}
    moved = {int(d["index"]) for d in rows if d.get("hash") == info_hash and d.get("state") == "done"
             and str(d.get("reason") or "").startswith("moved here")} - asked
    if not moved:
        return set()
    try:
        files = files if files is not None else QB(load()["config"]).files(info_hash)
    except Exception:
        return moved
    whole = {int(f.get("index", n)) for n, f in enumerate(files) if float(f.get("progress") or 0) >= 1}
    return moved - whole


def only_asked(qb, info_hash, extra=(), files=None):
    """Every file in the pack off except the ones asked for. True when anything changed."""
    if kept_whole(info_hash):
        return False
    want = asked_for(info_hash) - moved_short(info_hash) | {int(i) for i in extra}
    files = files if files is not None else qb.files(info_hash)
    # a film on the disk whole and not yet read stays off, except while the pack is
    # being read: switched on in a running pack, its edge pieces - which hold the
    # neighbouring files' bytes - are fetched from the swarm (2026-11-01, 21 GB)
    try:
        held = qb.info(info_hash) or {}
        if not str(held.get("state") or "").startswith("checking"):
            # a film being downloaded is never one of these: qBittorrent sets its full
            # length aside on the disk as it starts, and it read as whole and unread
            coming = {int(d.get("index", -1)) for d in load()["downloads"]
                      if d.get("hash") == info_hash and d.get("state") in ("downloading", "queued")}
            want -= set(present_unread(files, str(held.get("save_path") or ""),
                                       os.path.exists, os.path.getsize)) - coming - {int(i) for i in extra}
    except Exception:
        pass
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


#: done rows for files that came from the other machine or were already on the disk: never
#: named as fetched. Moved films named so were all taken back by the main server, which
#: read its whole pack again (2026-11-01)
NOT_FETCHED = ("moved here", "whole on the disk", "finishing a copy", "taken back")


def fetched_here(row):
    """Whether a done row is a film this machine fetched itself from the swarm."""
    return not str(row.get("reason") or "").startswith(NOT_FETCHED)


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
        if d.get("state") != "done" or not fetched_here(d):
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
        name = names[h].get(int(d.get("index", -1))) or ""
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


def on_disk_share(path):
    """How much of a file's length is really on the disk, 0 to 1. A client sets the length
    aside before it has the data, so length alone says nothing: 1.0 where it cannot be asked."""
    try:
        import ctypes
        from ctypes import wintypes
        get = ctypes.WinDLL("kernel32", use_last_error=True).GetCompressedFileSizeW
        get.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        get.restype = wintypes.DWORD
        high = wintypes.DWORD(0)
        low = get("\\\\?\\" + os.path.abspath(path), ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error():
            return 1.0
        return ((high.value << 32) + low) / max(os.path.getsize(path), 1)
    except (OSError, AttributeError, ImportError, ValueError):
        return 1.0


#: this much of a file on the disk, or it is a placeholder with some pieces in it
ON_DISK_FULL = 0.95


def already_placed(info_hash, index, name, size, share=None):
    """The path of a file already in its place here at its full length; "" when it is not.
    Left switched off: recheck_if_unread switches it on with the pack stopped. Asked before anything is sent: opening the
    transfer first had the other machine read and send every film it had sent already."""
    onto = placed_path(info_hash, index, name)
    try:
        if not (onto and size and os.path.getsize(onto) == int(size)):
            return ""
        if (share or on_disk_share)(onto) < ON_DISK_FULL:
            return ""                     # a placeholder: "moved in" in two seconds, nothing sent
    except OSError:
        return ""
    return onto


def recheck_if_unread(info_hash, indexes):
    """A pack looked at again only when one of these files is still short of whole in the
    client, and not while it is being looked at already - a whole pack is hundreds of
    gigabytes read. Returns whether a recheck was asked for."""
    qb = QB(load()["config"])
    t = qb.info(info_hash)
    if t is None or str(t.get("state") or "").startswith("checking"):
        return False
    wanted = {int(i) for i in indexes}
    short = [int(f.get("index", n)) for n, f in enumerate(qb.files(info_hash))
             if int(f.get("index", n)) in wanted and (float(f.get("progress") or 0) < 1
                                                      or int(f.get("priority") or 0) <= 0)]
    if not short:
        return False
    # stopped before the files are switched on: a running pack fetches a file switched on
    # and not yet read from the swarm - 17.7 GB of films already on the disk, 2026-10-31
    qb.stop(info_hash)
    qb.priority(info_hash, short, 1)
    note_read(info_hash)
    qb.recheck(info_hash)
    return True


def seed_when_read(info_hash, patience=4 * 3600, every=60, moved=()):
    """After a recheck: once it is done and every file on is whole, the pack is started -
    seeding only, nothing left to download. A file on and still short leaves it stopped:
    started, it would fetch that file from the swarm. moved: files read because they were
    moved in: one that failed is switched off, not left to stop the whole pack."""
    qb = QB(load()["config"])
    began = time.time()
    while time.time() - began < patience:
        time.sleep(every)
        t = qb.info(info_hash)
        if t is None:
            return False
        if str(t.get("state") or "").startswith("checking"):
            continue
        files = qb.files(info_hash)
        failed = [int(f.get("index", n)) for n, f in enumerate(files)
                  if int(f.get("index", n)) in set(moved) and float(f.get("progress") or 0) < 1]
        if failed:
            qb.priority(info_hash, failed, 0)
            files = qb.files(info_hash)
        if all(float(f.get("progress") or 0) >= 1 for f in files if int(f.get("priority") or 0) > 0):
            qb.start(info_hash)
            return True
        return False
    return False


#: seconds between reads of one pack for films moved into it: a read takes hours and the
#: pack does not seed meanwhile
READ_MOVED_EVERY = 86400
#: this little to read is minutes, so it goes as soon as a film arrives, this far apart: a
#: read after every film copied would keep stopping the pack. A read covers all of the pack
#: that is on the disk, switched on or off: 63 GB on took as long as the 700 GB there
QUICK_READ_BYTES = 50 * 1000 * 1000 * 1000
QUICK_READ_EVERY = 1800


def moved_waiting(info_hash):
    """Files moved into this pack that are on the disk at their length and that qBittorrent
    has not read: until it has, the main server sees them as missing and hands them over
    again every round (2535 times on 2026-11-01)."""
    data = load()
    mine = {int(d["index"]) for d in data["downloads"]
            if d.get("hash") == info_hash and d.get("state") == "done" and not d.get("off")
            and str(d.get("reason") or "").startswith("moved here")}
    # read once is enough: short after that is its edge pieces, or a file that failed
    mine -= {int(i) for i in (data.get("movedChecked") or {}).get(info_hash) or []}
    if not mine:
        return []
    qb = QB(data["config"])
    t = qb.info(info_hash)
    if t is None:
        return []
    out = []
    for n, f in enumerate(qb.files(info_hash)):
        i = int(f.get("index", n))
        if i not in mine or (float(f.get("progress") or 0) >= 1 and int(f.get("priority") or 0) > 0):
            continue
        try:
            if os.path.getsize(os.path.join(str(t.get("save_path") or ""), str(f.get("name") or ""))) == int(f.get("size") or 0):
                out.append(i)
        except (OSError, TypeError, ValueError):
            pass
    return out


def read_moved(now=None):
    """One pack read for the films moved into it, once they have all arrived: at most one
    at a time, once a day each, never with a download coming into it. The name of the pack
    read, or ""."""
    now = time.time() if now is None else now
    data = load()
    qb = QB(data["config"])
    packs = data["packs"]
    if any(str((qb.info(p["hash"]) or {}).get("state") or "").startswith("checking") for p in packs):
        return ""
    for pack in packs:
        h = pack["hash"]
        since = now - float((data.get("movedRead") or {}).get(h) or 0)
        if since < QUICK_READ_EVERY:
            continue
        if any(d.get("hash") == h and d.get("state") == "downloading" for d in data["downloads"]):
            continue
        waiting = moved_waiting(h)
        if not waiting:
            continue
        if since < READ_MOVED_EVERY:
            to_read = sum(float(f.get("progress") or 0) * int(f.get("size") or 0)
                          + (int(f.get("size") or 0) if int(f.get("index", n)) in waiting else 0)
                          for n, f in enumerate(qb.files(h)))
            if to_read > QUICK_READ_BYTES:
                continue
        if recheck_if_unread(h, waiting):
            with LOCK:
                data.setdefault("movedRead", {})[h] = now
                checked = data.setdefault("movedChecked", {})
                checked[h] = sorted(set(checked.get(h) or []) | set(waiting))
                save()
            return str(pack.get("name") or "")
    return ""


def moved_fresh(info_hash, index):
    """A file that really came over the network: it is read again."""
    data = load()
    rows = (data.get("movedChecked") or {}).get(info_hash) or []
    if int(index) in rows:
        with LOCK:
            rows.remove(int(index))
            save()


def finish_moved_reads(now=None):
    """Packs whose read for moved films has ended, seeding again: asked each round, so a
    restart during a read cannot leave one stopped. Moved films still short are switched
    off, so nothing is fetched: their edge pieces hold other films' bytes. The names started."""
    now = time.time() if now is None else now
    data = load()
    qb = QB(data["config"])
    started = []
    for pack in data["packs"]:
        h = pack["hash"]
        if now - float((data.get("movedRead") or {}).get(h) or 0) >= READ_MOVED_EVERY:
            continue
        t = qb.info(h) or {}
        if not str(t.get("state") or "").startswith(("stopped", "paused")) or h in STATE.get("yielding", {}):
            continue
        if any(d.get("hash") == h and d.get("state") == "downloading" for d in data["downloads"]):
            continue
        moved = {int(d["index"]) for d in data["downloads"] if d.get("hash") == h
                 and str(d.get("reason") or "").startswith("moved here")}
        files = qb.files(h)
        short = [int(f.get("index", n)) for n, f in enumerate(files)
                 if int(f.get("index", n)) in moved and int(f.get("priority") or 0) > 0
                 and float(f.get("progress") or 0) < 1]
        if short:
            qb.priority(h, short, 0)
            files = qb.files(h)
        if all(float(f.get("progress") or 0) >= 1 for f in files if int(f.get("priority") or 0) > 0):
            qb.start(h)
            started.append(str(pack.get("name") or ""))
    return started


VIDEO_FILE = (".mkv", ".mp4", ".avi", ".m4v")


def elsewhere_plan(packs, infos, files_of):
    """Films packs lack here that another torrent here holds whole - the same file, by name
    and length: [{torrent, name, file, path, pack, index, fits}]. fits: the torrent's file
    name ends the pack's own path for it, so the torrent can be put where the pack keeps
    the film and both seed one file. Races are not counted."""
    want = {}
    for h in packs:
        t = infos.get(h)
        if not t:
            continue
        for n, f in enumerate(files_of(h)):
            if float(f.get("progress") or 0) < 1 and str(f.get("name") or "").lower().endswith(VIDEO_FILE):
                key = (os.path.basename(str(f["name"])).lower(), int(f.get("size") or 0))
                want.setdefault(key, (h, int(f.get("index", n)), str(f["name"])))
    # only torrents named like a wanted film - its folder or its file - are opened: asked
    # of every torrent, a round listed the files of two thousand of them
    names = set()
    for (base, _), (_, _, pname) in want.items():
        names.update({base, os.path.splitext(base)[0]})
        names.update(x.lower() for x in pname.replace("\\", "/").split("/")[:-1])
    out = []
    for h, t in infos.items():
        if h in packs or float(t.get("progress") or 0) < 1 or t.get("category") == "fl-race":
            continue
        if str(t.get("name") or "").lower() not in names:
            continue
        for f in files_of(h):
            key = (os.path.basename(str(f.get("name") or "")).lower(), int(f.get("size") or 0))
            if key not in want or not key[0].endswith(VIDEO_FILE):
                continue
            ph, index, pname = want.pop(key)
            tail = str(f["name"]).replace("\\", "/")
            out.append({"torrent": h, "name": str(t.get("name") or ""), "file": tail,
                        "path": os.path.join(str(t.get("save_path") or ""), tail.replace("/", os.sep)),
                        "pack": ph, "index": index,
                        "fits": ("/" + pname.replace("\\", "/").lower()).endswith("/" + tail.lower())})
    return out


def location_for(pack_path, tail):
    """The folder a torrent whose file is named tail goes under so that file is the one at
    pack_path, or "" when tail does not end pack_path."""
    full = os.path.normpath(str(pack_path))
    tail = os.path.normpath(str(tail).replace("/", os.sep))
    if not tail or tail.startswith(("..", os.sep)) or os.path.isabs(tail):
        return ""
    if not os.path.normcase(full).endswith(os.sep + os.path.normcase(tail)):
        return ""
    return full[:-len(tail) - 1]


def inside(folder, name):
    """folder joined with a torrent's own file name, or "" when the name leaves the folder."""
    rel = os.path.normpath(str(name).replace("/", os.sep))
    if not rel or rel.startswith("..") or os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        return ""
    return os.path.join(folder, rel)


def file_to_send(info_hash, index):
    """The path of a pack film to send: the pack's own, unless another torrent here holds more
    of it. The pack's can be a stub with the film's name: 779 bytes went out as a whole film."""
    where = file_of(info_hash, index)
    other = elsewhere_path(info_hash, int(index or -1))

    def length(path):
        try:
            return os.path.getsize(path) if path else -1
        except OSError:
            return -1
    best = other if length(other) > length(where) else where
    return best if length(best) >= 0 else ""


def elsewhere_path(info_hash, index):
    """Where a pack film held by another torrent here sits, from the last pack round."""
    return (STATE.get("elsewhere") or {}).get((str(info_hash), int(index)), "")


def placed_path(info_hash, index, name):
    """Where a file copied from the other machine goes here: where this machine's own
    pack expects it, else under the save folder by the name the other machine sent."""
    onto = file_of(info_hash, index)
    if onto:
        return onto
    root = save_folder()
    if not root or not name:
        return ""
    return os.path.join(root, str(name).replace("/", os.sep))


def place_and_recheck(info_hash, index, name, reader, size=0, recheck=True, share=None):
    """Write a file copied from the other machine where its pack expects it, then look again.

    Returns the path written, or "" when there was nowhere sensible to put it. The
    file is written beside itself and moved into place, so a half-copied one is never
    mistaken for the film. recheck=False leaves the looking again to the caller: a pack
    of hundreds of gigabytes is read whole by each recheck, so a run of files is checked once.
    """
    # Where this machine's own qBittorrent expects that file, not where the other
    # machine kept it. The two hold the same pack under different layouts - one with
    # the pack's own folder, one without - so the name the other machine sends put a
    # film a folder above where the pack looks for it: it played, because the folder
    # is one the library reads, and the recheck that was meant to hand it to
    # qBittorrent found nothing. The sent name is the fallback for a pack this
    # machine does not have at all.
    onto = placed_path(info_hash, index, name)
    if not onto:
        return ""
    try:
        os.makedirs(os.path.dirname(onto), exist_ok=True)
    except OSError:
        return ""
    if (os.path.exists(onto) and size and os.path.getsize(onto) == int(size)
            and (share or on_disk_share)(onto) >= ON_DISK_FULL):
        return onto                        # already here, the right length, and really there
    part = onto + ".part"
    try:
        with open(part, "wb") as f:
            while True:
                lump = reader.read(LUMP)
                if not lump:
                    break
                f.write(lump)
        # short of its length it is not the film: a 779-byte stub replaced a partial copy here
        if size and os.path.getsize(part) != int(size):
            raise ValueError("came short")
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
    if not recheck:
        return onto                        # switched on by the caller, with the pack stopped
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
    # a copy's pack episodes mostly come from the main server, not its own downloads
    if (not got or got.get("state") != "done") and not _following():
        return None
    _, film = by_key(key)
    name = os.path.basename((film or {}).get("path") or "")
    lib = STATE["lib"]() if STATE["lib"] else None
    if not lib or not name:
        return None
    # An episode is filed against its own row rather than a title of its own, so the
    # file that has arrived is looked for either way - without this an episode that had
    # come in and been scanned read as still on its way.
    # and an episode is answered as the episode: the programme it belongs to is not
    # the thing that arrived, and a page following the download never found it playable
    episode = (film or {}).get("kind") == "episode"
    where = ("SELECT f.item_id, f.episode_id FROM file f JOIN item i ON i.id = f.item_id "
             "WHERE f.path LIKE ?" + ("" if episode else " AND f.episode_id IS NULL"))
    con = lib.db()
    try:
        row = con.execute(where, ("%" + name,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return str(row["episode_id"] if episode and row["episode_id"] else row["item_id"])


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
        row = coming_of(key, release_keys(key))
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
    """Films coming in now, as the library lists a title: everyone's, or one person's.

    In queue order - the downloading one, then the waiting ones in the order they will
    start - and each saying whether whoever asked may move it."""
    out = []
    rows = [d for d in load()["downloads"] if d.get("state") in ("queued", "downloading")
            and (token is None or d.get("token") == token)]
    rows.sort(key=lambda d: 0 if d.get("state") == "downloading" else 1)
    for d in rows:
        one = metadata(d["key"])
        if one and not any(o["ratingKey"] == one["ratingKey"] for o in out):
            offer = one.setdefault("offer", {})
            offer["queueKey"] = d["key"]
            # the owner moves the one coming in as well
            offer["mine"] = d.get("state") == "queued" or token is None
            out.append(one)
    return out


def ready_for(tokens, since):
    """What these tokens asked for by hand that has come in since `since` and is in the
    library now: the line each screen says when somebody's download is ready. What the
    shuffle fetched ahead is left out - nobody asked for it, and it came by the dozen."""
    out = []
    for d in load()["downloads"]:
        if (d.get("state") != "done" or d.get("auto") or d.get("token") not in tokens
                or float(d.get("done") or 0) <= since):
            continue
        here = arrived(d.get("key") or "")
        if not here:
            continue                      # whole, but not in the library yet
        name = str(d.get("title") or "")
        if d.get("kind") == "episode" and d.get("season") and d.get("episode"):
            name += " S%02dE%02d" % (int(d["season"]), int(d["episode"]))
        out.append({"id": d.get("id"), "key": here, "title": name,
                    "done": int(float(d.get("done") or 0))})
    return out


def reorder(keys, token=None):
    """Put waiting downloads in the order given. Each one keeps to the places the ones
    being moved already hold, so somebody can reorder their own and nobody else's; the
    owner (token None) can reorder all of them, and moves the one coming in below a
    waiting one by doing so: it goes back to waiting and the one above it starts."""
    keys = [str(k) for k in dict.fromkeys(str(k) for k in keys or [])]
    back = []
    with LOCK:
        data = load()
        rows = data["downloads"]
        if token is None:
            state_of = {d.get("key"): d.get("state") for d in rows}
            for d in rows:
                if d.get("state") == "downloading" and d.get("key") in keys and any(
                        state_of.get(k) == "queued" for k in keys[:keys.index(d["key"])]):
                    d.update(state="queued", why="")
                    d.pop("started", None)
                    back.append(d["hash"])
        slots = [n for n, d in enumerate(rows) if d.get("state") == "queued"
                 and (token is None or d.get("token") == token)]
        by_key = {rows[n]["key"]: rows[n] for n in slots}
        wanted = [by_key[k] for k in dict.fromkeys(keys) if k in by_key]
        rest = [rows[n] for n in slots if rows[n] not in wanted]
        for n, row in zip(slots, wanted + rest):
            rows[n] = row
        save()
    if back:
        _back_to_waiting(back)
        try:
            _start_next()
        except Exception as e:
            STATE["why"] = str(e)[:160]
    return {"ok": True, "order": [r["key"] for r in wanted + rest]}


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
                # here already, and switched to "do not download" in qBittorrent: that
                # is the owner's to decide, and it stays off until switched on there
                d["off"] = True
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


#: seconds nobody watching has been short of disk before a paused pack read starts again
CALM_FOR = 45


def read_turns(checking, watched, short, held, calm_since, now):
    """(stop, start, calm_since) for pack reads: those on a drive somebody is watching from
    are stopped while a viewer is short of disk; the ones stopped start again once nobody
    has been short for CALM_FOR seconds, or their drive is no longer watched. checking:
    {hash: drive} being read; held: {hash: drive} stopped for a viewer. A read and a film
    on one drive left the film three seconds from stopping (2026-11-01)."""
    if short:
        return [h for h, d in checking.items() if d in watched and h not in held], [], 0.0
    calm_since = calm_since or now
    start = [h for h, d in held.items() if d not in watched or now - calm_since >= CALM_FOR]
    return [], start, calm_since


def yield_reads(watched, short, now=None):
    """Pack reads paused and started again by read_turns, on this machine's client."""
    now = time.time() if now is None else now
    data = load()
    qb = QB(data["config"])
    held = STATE.setdefault("yielding", {})
    checking = {}
    for pack in data["packs"]:
        try:
            t = qb.info(pack["hash"]) or {}
        except Exception:
            continue
        if str(t.get("state") or "").startswith("checking"):
            checking[pack["hash"]] = os.path.splitdrive(str(t.get("save_path") or ""))[0].upper()
    stop, start, STATE["calmSince"] = read_turns(checking, {str(d).upper() for d in watched},
                                                 short, held, float(STATE.get("calmSince") or 0), now)
    for h in stop:
        qb.stop(h)
        held[h] = checking[h]
    for h in start:
        held.pop(h, None)
        qb.start(h)
    return stop, start


def _guard_packs():
    """Keep every pack in qBittorrent to what Palladium was asked for. One that nobody asked
    for is stopped with all of its files off; one that was is held to those films, and
    one with a download coming in runs."""
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
            stopped = str(held.get("state") or "").startswith(("stopped", "paused"))
            if not asked_for(pack["hash"]) and not kept_whole(pack["hash"]) and not stopped:
                _stop(qb, pack["hash"])
            # stopped in the moment between one download and the next, it stayed
            # stopped with the next one on: 0% and an ETA counting up
            # nor one whose read is paused for somebody watching
            elif stopped and not STATE.get("held") and pack["hash"] not in STATE.get("yielding", {}) and any(
                    d.get("hash") == pack["hash"] and d.get("state") == "downloading"
                    for d in data["downloads"]):
                qb.start(pack["hash"])
        except Exception as e:
            STATE["why"] = str(e)[:160]


def downloads():
    """Every download, newest first, without the key it was asked with.

    Season and episode are filled in from the pack entry where the row has none: rows
    written before packs read episodes carry the programme's name alone, which is the
    same for every episode of it.
    """
    data = load()
    # every pack film by its key, once: looked up one row at a time, the page that polls
    # this every two seconds searched 14 000 films for each of 300 rows and the requests
    # piled up behind each other until the server stopped answering (2026-11-01)
    films = {f.get("key"): f for p in data["packs"] for f in p.get("films") or []}
    out = []
    for d in reversed(data["downloads"]):
        one = {k: v for k, v in d.items() if k != "token"}
        if not one.get("episode"):
            film = films.get(d.get("key") or "") or {}
            if film.get("kind") == "episode":
                one.update({"kind": "episode", "season": film.get("season"),
                            "episode": film.get("episode"),
                            "episodeName": film.get("episodeName") or ""})
        out.append(one)
    return out


# ---------------------------------------------------------------- the worker

def _tally():
    """Downloads finished, and downloads still to come."""
    rows = load()["downloads"]
    return (sum(1 for d in rows if d.get("state") == "done"),
            sum(1 for d in rows if d.get("state") in ("queued", "downloading")))


pd_beat.counter("torrents", _tally)


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
    waiting = [(p, f) for p, doubled in ((p, _doubled(p)) for p in load()["packs"])
               for f in (p.get("films") or [])
               if f.get("tmdb") is None or _wrong_year(f)
               or (f.get("kind") == "episode" and f.get("named") != 2 and f.get("tmdb")
                   and (str(f.get("title") or "").lower(), int(f.get("season") or 0)) in doubled)]
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


def _season_has_double(film):
    """Whether the pack this episode is in has a double-episode file - "S06E04E05" - in its
    season."""
    for pack in load()["packs"]:
        if any(f is film for f in pack.get("films") or []):
            return (str(film.get("title") or "").lower(), int(film.get("season") or 0)) in _doubled(pack)
    return False


def _doubled(pack):
    """(programme, season) of a pack's seasons that have a double-episode file."""
    from pd_library import file_double
    return {(str(f.get("title") or "").lower(), int(f.get("season") or 0))
            for f in pack.get("films") or []
            if f.get("kind") == "episode" and file_double(f.get("path") or "", int(f.get("season") or 0))}


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
    found = STATE.setdefault("tv_found", {}).get((show.lower(), year))
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
        STATE["tv_found"][(show.lower(), year)] = found
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
    number = int(film.get("episode") or 0)
    if _season_has_double(film):
        # the release counts a double episode as two, the catalogue as one: from the
        # double on, the release's number is one or more ahead of the catalogue's
        from pd_library import doubles_map
        number = doubles_map(list(told.values())).get(number, number)
    one = told.get(number) or {}
    with LOCK:
        film["named"] = 2
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


#: What qBittorrent says when it has stopped on something rather than waiting on a
#: swarm, and what that means in words somebody can act on. A recheck is the answer to
#: all of them: it reads the files on disk again and starts what is whole.
HALTED = {
    "missingFiles": "qBittorrent cannot find this pack's files",
    "error": "qBittorrent stopped on an error",
    "unknown": "qBittorrent is not sure about this pack",
}


def halted_why(state, info_hash=""):
    """What to say about a stopped pack: before it was read again, and after."""
    said = HALTED.get(str(state or ""), "")
    if not said:
        return ""
    if info_hash and info_hash in LOOKED:
        return said + " - the files were read again and it is still stopped, so it "                       "needs looking at"
    return said + " - reading its files again"


#: packs already asked, by itself, to read their files again - and what came of it.
#: Once each: a recheck that did not mend it will not mend it on the tenth try, and
#: something a person has to deal with should be said once and then left to them.
LOOKED = {}


def hold_all():
    """Stop every torrent that is running here. Returns the hashes stopped, so exactly
    those can be started again: one the owner stopped by hand stays stopped."""
    try:
        qb = QB(load()["config"])
        states = qb.states()
    except Exception as e:
        STATE["why"] = str(e)[:160]
        return []
    stopped = []
    for info_hash, state in states.items():
        if state.startswith(("stopped", "paused")) or state in ("error", "missingFiles"):
            continue
        try:
            _stop(qb, info_hash)
            stopped.append(info_hash)
        except Exception:
            pass
    return stopped


def release(hashes):
    """Start again what hold_all stopped."""
    try:
        qb = QB(load()["config"])
    except Exception:
        return
    for info_hash in hashes or []:
        try:
            qb.start(info_hash)
        except Exception:
            pass


def mend_halted(info_hash="", force=False):
    """Read the files on disk again for packs qBittorrent has stopped on.

    A drive that goes away for a minute leaves every torrent touching it in
    "missingFiles" - qBittorrent stops them and waits, and nothing arrives even
    though the disk is back. Reading them again finds the pieces and starts what is
    whole. Done by itself when the state is seen, and by hand from the Torrents page.

    `info_hash` names one pack; without it, every halted one. `force` ignores how
    recently it was tried, which is what a press of the button means.
    """
    if STATE.get("held"):
        return {"ok": True, "rechecked": 0, "waiting": 0, "hashes": []}
    data = load()
    qb = QB(data["config"])
    want = []
    if info_hash:
        want = [info_hash]
    else:
        for pack in data["packs"]:
            try:
                said = qb.info(pack["hash"]) or {}
            except Exception:
                continue
            if str(said.get("state") or "") in HALTED:
                want.append(pack["hash"])
    mended, held_off = [], 0
    for one in want:
        # tried once already and still halted: it needs somebody, not another go
        if not force and one in LOOKED:
            held_off += 1
            continue
        LOOKED[one] = time.time()
        try:
            qb.recheck(one)
            qb.start(one)
            mended.append(one)
        except Exception as e:
            STATE["why"] = str(e)[:160]
    if mended:
        STATE["why"] = ("read the files again for %d pack%s qBittorrent had "
                        "stopped" % (len(mended), "" if len(mended) == 1 else "s"))
    return {"ok": True, "rechecked": len(mended), "waiting": held_off,
            "hashes": mended}


def _whole_here(qb_file, save_path, size):
    """A pack's file verified on the disk: qBittorrent holds every piece of it, and the
    file is there at its full length. What "downloaded" means, whatever is written down."""
    if not qb_file or float(qb_file.get("progress") or 0) < 1:
        return False
    try:
        return os.path.getsize(os.path.join(str(save_path or ""),
                                            str(qb_file.get("name") or ""))) == int(size)
    except (OSError, TypeError, ValueError):
        return False


def _done_row(pack, film, path, reason):
    """A finished download for a file found whole on the disk."""
    try:
        when = int(os.path.getctime(path))
    except OSError:
        when = int(time.time())
    row = {"id": uuid.uuid4().hex[:10], "key": film["key"], "hash": pack["hash"],
           "index": film["index"], "title": film.get("name") or film.get("title"),
           "year": film.get("year"), "size": film["size"], "who": "", "token": "me",
           "when": when, "state": "done", "done": when, "progress": 1.0, "why": "",
           "auto": True, "reason": reason}
    if film.get("kind") == "episode":
        row.update({"kind": "episode", "season": film.get("season"),
                    "episode": film.get("episode"),
                    "episodeName": film.get("episodeName") or ""})
    return row


#: when the packs were last read against the disk
ADOPTED = {"at": 0.0}


def adopt_whole(every=600):
    """Every pack file that is whole on the disk is a finished download, written down
    or not: read off qBittorrent and the disk every ten minutes. What is downloaded is
    what is there - a history that was lost left files on the disk listed as only on
    offer, and they were queued to be fetched again."""
    now = time.time()
    if now - ADOPTED["at"] < every:
        return 0
    ADOPTED["at"] = now
    data = load()
    qb = QB(data["config"])
    held = {str(t.get("hash") or ""): t
            for t in json.loads(qb._call("/api/v2/torrents/info", timeout=10) or b"[]")}
    took = 0
    for pack in list(data["packs"]):
        t = held.get(pack.get("hash"))
        if not t:
            continue
        known = {int(d["index"]) for d in data["downloads"]
                 if d.get("hash") == pack["hash"] and not d.get("off")
                 and d.get("state") in ("queued", "downloading", "done")}
        films = [f for f in (pack.get("films") or []) if int(f["index"]) not in known]
        if not films:
            continue
        try:
            files = {int(f.get("index", n)): f
                     for n, f in enumerate(qb.files(pack["hash"]))}
        except Exception:
            continue
        root = str(t.get("save_path") or "")
        for film in films:
            f = files.get(int(film["index"]))
            if not _whole_here(f, root, film["size"]):
                continue
            with LOCK:
                data["downloads"].append(_done_row(
                    pack, film, os.path.join(root, str(f.get("name") or "")),
                    "whole on the disk"))
            took += 1
    if took:
        with LOCK:
            save()
        STATE["owned"] = None
    return took


def _follow_downloads():
    """Read how far each download has got, how fast its torrent is coming in, and so how
    long is left. True while anything is still on its way."""
    data = load()
    active = [d for d in data["downloads"] if d.get("state") == "downloading"]
    if not active:
        return False
    qb = QB(data["config"])
    changed = False
    finished = []
    for info_hash in {d["hash"] for d in active}:
        try:
            files = qb.files(info_hash)
            said = qb.info(info_hash) or {}
            # A torrent qBittorrent has halted is not a slow download. "missingFiles"
            # is what it says when the data it had is no longer on the disk - which is
            # what a drive dropping off leaves behind, on every torrent that was
            # touching it. It stays that way until the files are read again, and
            # nothing arrives meanwhile: the film sat at 0% saying "downloading".
            state = str(said.get("state") or "")
            if state in HALTED:
                for d in [x for x in active if x["hash"] == info_hash]:
                    d["stuck"] = state
                    d["why"] = halted_why(state, info_hash)
                changed = True
                continue
            # a pack waiting for another pack's check says "checking" too: one that has
            # not moved for a minute is waiting its turn, and said so
            if state.startswith("checking"):
                state = check_state(info_hash, float(said.get("progress") or 0), time.time())
            for d in [x for x in active if x["hash"] == info_hash]:
                if d.pop("stuck", None):
                    d["why"] = ""
                    changed = True
                if d.get("qb") != state:
                    d["qb"] = state             # checking, finding, waiting: said as such
                    changed = True
            speed = float(said.get("dlspeed") or 0)
            was = SPEEDS.get(info_hash)
            settled = speed if was is None else was * 0.8 + speed * 0.2
            SPEEDS[info_hash] = settled
            # Turned on in qBittorrent by hand since the last look: taken in as a
            # download of the owner's rather than switched straight off again, which is
            # what happened to a film started there - it went off within seconds.
            # A handful at most: a pack that arrives with every file on is trimmed.
            applied = STATE.setdefault("applied", {})
            now_on = {int(f.get("index", n)) for n, f in enumerate(files)
                      if (f.get("priority") or 0) > 0}
            before = applied.get(info_hash)
            if before is not None:
                fresh = now_on - before - asked_for(info_hash)
                if 0 < len(fresh) <= 5 and adopt(info_hash, fresh):
                    changed = True
                    active = [d for d in data["downloads"]
                              if d.get("state") == "downloading"]
            if only_asked(qb, info_hash, files=files):
                files = qb.files(info_hash)
            applied[info_hash] = {int(f.get("index", n)) for n, f in enumerate(files)
                                  if (f.get("priority") or 0) > 0}
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
            remaining = left(d)
            # qBittorrent's own number for the file: an estimate from the torrent's bytes
            # received read 99% while qBittorrent said 80%, and then looked stuck
            progress = float(f.get("progress") or 0)
            d.pop("got", None)
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
            if progress >= 1.0 and d["state"] != "done" and not _whole_here(
                    f, said.get("save_path"), d.get("size") or f.get("size") or 0):
                # every piece in qBittorrent and no whole file on the disk: not done
                if d.get("why") != "qBittorrent has it whole; the file on the disk is not":
                    d["why"] = "qBittorrent has it whole; the file on the disk is not"
                    changed = True
                continue
            if progress >= 1.0 and d["state"] != "done":
                d["state"] = "done"
                d["done"] = int(time.time())
                d["mbit"] = 0.0
                d["eta"] = None
                STATE["scan_wanted"] = True
                finished.append(d)
                changed = True
    if changed:
        with LOCK:
            save()
    # playable at once: the file is whole, and indexing it alone takes a second where
    # the full scan asked for above takes minutes
    if finished:
        # whole now: off the list of files still coming at once, or a scan inside the
        # next half minute would take the film it had just been given back out again
        for d in finished:
            try:
                UNFINISHED["paths"].discard(os.path.normcase(os.path.normpath(
                    file_of(d.get("hash"), d.get("index")) or "")))
            except Exception:
                pass
        lib = STATE["lib"]() if STATE["lib"] else None
        if lib:
            try:
                lib.take_in([file_of(d.get("hash"), d.get("index")) for d in finished
                             if d.get("hash") and d.get("index") is not None])
            except Exception as e:
                STATE["why"] = "taking in: %s" % str(e)[:140]
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


def _boxsets():
    """One box set from the tracker read for the films inside it."""
    import pd_boxsets
    pd_boxsets.step()


def _tracker_landed():
    """A film or episode fetched from the tracker, into the library once it is whole."""
    lib = STATE["lib"]() if STATE["lib"] else None
    if lib:
        import pd_tracker
        pd_tracker.take_in_finished(lib)


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


#: the last error each step of the worker gave, so the same one is logged once
STEP_FAULTS = {}


def _step(name, call):
    """One step of the worker's round, on its own: a step that fails says so in the log,
    once per error, and the steps after it still run. Run as one chain, a single
    failing step skipped everything behind it every round - downloads sat at 0 % with
    the file whole on the disk, and nothing anywhere said why."""
    pd_beat.beat("torrents", name, 900)
    try:
        got = call()
        STEP_FAULTS.pop(name, None)
        return got
    except Exception as e:
        why = "%s: %s" % (type(e).__name__, str(e)[:160])
        STATE["why"] = why
        if STEP_FAULTS.get(name) != why:
            STEP_FAULTS[name] = why
            try:
                pd_log.raw("%s download worker: %s failed - %s%s"
                        % (time.strftime("%H:%M:%S"), name, why, chr(10)), os.path.join(STATE["root"], "debug.log"))
            except OSError:
                pass
        return None


def _scan_if_wanted():
    if STATE["scan_wanted"] and STATE["scan"]:
        if STATE["scan"]():
            STATE["scan_wanted"] = False
            STATE["owned"] = None


def _work():
    while True:
        # first and again after the slow steps: a round takes half a minute, and a film
        # whole on the disk waited that long to be called ready
        _step("_follow_downloads", _follow_downloads)
        _step("_mirror_house", _mirror_house)
        _step("_notice_removed", _notice_removed)
        _step("read_the_folder", read_the_folder)
        _step("refill_packs", refill_packs)
        _step("read_episodes", read_episodes)
        matching = bool(_step("_match_some", _match_some))
        fetching = bool(_step("_follow_downloads", _follow_downloads))
        _step("adopt_whole", adopt_whole)
        # a pack qBittorrent has stopped on is read again, once - a drive that
        # went away and came back leaves every torrent that touched it halted
        _step("mend_halted", mend_halted)
        _step("keep_at_once", keep_at_once)
        _step("keep_merge", keep_merge)
        _step("_start_next", _start_next)
        _step("_guard_packs", _guard_packs)
        _step("_follow_downloads", _follow_downloads)
        _step("_name_arrivals", _name_arrivals)
        _step("_chase_arrivals", _chase_arrivals)
        _step("_tracker_landed", _tracker_landed)
        _step("_boxsets", _boxsets)
        _step("_fold_held", _fold_held)
        _step("_official_art", _official_art)
        _step("_unindex_unfinished", _unindex_unfinished)
        _step("scan", _scan_if_wanted)
        pd_beat.beat("torrents", "idle", 900)
        # every few seconds while a film is coming in, so its page can show it moving
        # and every twenty seconds while there are packs, so a pack qBittorrent picks up by
        # itself is turned down before it has taken much
        pd_beat.sleep("torrents",
                      5 if fetching else 15 if matching else 20 if load()["packs"] else 60)
