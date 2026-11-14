"""The catalogue's side of the library: TMDB numbers and runtimes, upgrades and duplicates, popular lists, series shelves and twins.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import re
import time
from typing import Any

import pd_h

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def imdb_number(title, year=0, key=""):
    """A film's IMDb number, which is what finds its releases on the tracker: from the
    library, else the catalogue of new films, else asked of TMDB by its TMDB number.

    Without one the tracker is searched by name - and a short name found other things of that
    year and none of the twenty-five releases under the full title, all tagged with the number.
    """
    tmdb = 0
    try:
        con = S.local().lib.db()
        try:
            row = None
            if key and not str(key).startswith(("rt", "dl-")):
                # by the title's own key: a pack copy's old key is its title's
                import pd_torrents
                row = con.execute("SELECT imdb_id, tmdb_id FROM item WHERE id=?",
                                  (pd_torrents.title_of(key),)).fetchone()
            if not row and title:
                row = con.execute("SELECT imdb_id, tmdb_id FROM item WHERE type='movie' AND "
                                  "title=? AND (year=? OR ?=0)",
                                  (str(title), int(year or 0), int(year or 0))).fetchone()
        finally:
            con.close()
        if row and row["imdb_id"]:
            return str(row["imdb_id"])
        tmdb = int((row["tmdb_id"] if row else 0) or 0)
    except Exception:
        pass
    try:
        import pd_streaming
        for r in pd_streaming.read() or []:
            if (str(r.get("key")) == str(key)) or (
                    (r.get("title") or "") == title and int(r.get("year") or 0) == int(year or 0)):
                if r.get("imdb"):
                    return str(r["imdb"])
                tmdb = tmdb or int(r.get("tmdb") or 0)
                break
    except Exception:
        pass
    if not tmdb:
        tmdb = S.listed_tmdb(title, year)
    if not tmdb:
        return ""
    if tmdb not in S.IMDB_OF:
        try:
            S.IMDB_OF[tmdb] = str((S.local().lib.tmdb("/movie/%d" % tmdb) or {}).get("imdb_id") or "")
        except Exception:
            return ""                     # not kept: asked again next time
    return S.IMDB_OF[tmdb]


def runtime_of(key="", title="", year=0):
    """A film's length in seconds: its file here, else the catalogue's minutes."""
    con = S.local().lib.db()
    try:
        row = None
        if key:
            row = con.execute("SELECT MAX(f.duration) AS d FROM file f "
                              "WHERE f.item_id=? AND f.episode_id IS NULL",
                              (str(key),)).fetchone()
            if row and row["d"]:
                return float(row["d"])
            row = con.execute("SELECT runtime FROM item WHERE id=?", (str(key),)).fetchone()
        if (not row or not row["runtime"]) and title:
            row = con.execute("SELECT runtime FROM item WHERE title=? AND "
                              "(year=? OR ?=0)", (str(title), int(year or 0),
                                                  int(year or 0))).fetchone()
        if row and row["runtime"]:
            return float(row["runtime"]) * 60.0
    except Exception:
        pass
    finally:
        con.close()
    return S.catalogue_runtime(title, year)


def catalogue_title(title, year=0, key=""):
    """(the catalogue's full title, its IMDb number) for a film, or ("", "")."""
    try:
        lib = S.local().lib
        number = 0
        if key:
            con = lib.db()
            try:
                row = con.execute("SELECT tmdb_id FROM item WHERE id=?", (str(key),)).fetchone()
                number = int((row["tmdb_id"] if row else 0) or 0)
            finally:
                con.close()
            import pd_torrents
            if not number and pd_torrents.only_in_pack(key):
                _, film = pd_torrents.by_key(str(key))
                number = int((film or {}).get("tmdb") or 0)
        # a film on a kept list carries its own number: "Seven" of 1995 is the list's
        if not number and title:
            number = S.listed_tmdb(title, year)
        if not number:
            found = lib.tmdb("/search/movie", query=str(title),
                             **({"year": int(year)} if year else {}))
            number = S.hit_of_year(found.get("results") or [], year)
        if not number:
            return "", ""
        said = lib.tmdb("/movie/%d" % number) or {}
        return str(said.get("title") or ""), str(said.get("imdb_id") or "")
    except Exception:
        return "", ""


def hit_of_year(hits, year):
    """The first catalogue hit released within a year of the one asked for; with no year,
    the first. The catalogue's year narrows to anything released that year anywhere, and
    "Seven" of 1995 came back as a film of 1960 with a re-release then."""
    for h in hits or []:
        made = str(h.get("release_date") or "")[:4]
        if not year or (made.isdigit() and abs(int(made) - int(year)) <= 1):
            return int(h.get("id") or 0)
    return 0


def listed_tmdb(title, year=0):
    """A film's catalogue number from a kept list, by its title and year; 0 where none."""
    try:
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT tmdb FROM list_item WHERE LOWER(title)=LOWER(?) AND "
                              "(year=? OR ?=0) AND tmdb>0 LIMIT 1",
                              (str(title), int(year or 0), int(year or 0))).fetchone()
        finally:
            con.close()
        return int(row["tmdb"]) if row else 0
    except Exception:
        return 0


def upgrades(fresh=False):
    """Every film here whose file is not the release the settings under Choosing would
    pick from what the tracker's index and the packs carry now. Read from what is known,
    never searched for: the whole library at once would be a search per film."""
    import pd_tracker
    import pd_torrents
    stored = S.read_settings() or {}
    # kept ten minutes, and only while Choosing says what it said: a bitrate window
    # changed there left the list judging by the old one
    said = json.dumps({k: v for k, v in stored.items()
                       if str(k).startswith(("prefer", "rateLock", "closeCall"))}, sort_keys=True)
    if not fresh and time.time() - S.UPGRADES["at"] < 600 and S.UPGRADES.get("said") == said:
        return S.UPGRADES["list"]
    S.UPGRADES["said"] = said
    wishes = S.Handler.ordered_wishes(stored)
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    # the packs' films by name once, rather than every pack scanned per title
    packs = {}
    for pack in pd_torrents.load()["packs"]:
        for film in pack.get("films") or []:
            if film.get("kind") == "episode":
                continue
            for name in {flat(film.get("title")), flat(film.get("name"))}:
                if name:
                    packs.setdefault(name, []).append((pack, film))
    held = pd_torrents.seeders([p.get("hash") for p in pd_torrents.load()["packs"]])
    con = S.local().lib.db()
    try:
        films = con.execute(
            "SELECT i.id, i.title, i.year, i.imdb_id, i.runtime, f.path, f.size, f.duration, "
            "f.height, f.width FROM item i JOIN file f ON f.item_id = i.id "
            "WHERE i.type = 'movie' AND f.episode_id IS NULL").fetchall()
    finally:
        con.close()
    by_film = {}
    for r in films:
        if r["path"] and os.path.exists(r["path"]):
            by_film.setdefault(r["id"], []).append(r)
    out = []
    for key, every in by_film.items():
        from pd_library import cut_groups, cut_label
        split = {}
        for f, (group, _) in zip(every, cut_groups([(f["path"], f["duration"])
                                                    for f in every])):
            split.setdefault(group, []).append(f)
        many_cuts = len(split) > 1
        for files in split.values():
            cut_name = next((cut_label(f["path"]) for f in files if cut_label(f["path"])), "")
            first = files[0]
            title, year = first["title"] or "", int(first["year"] or 0)
            # the file's own length, else the catalogue's minutes
            seconds = (max(float(f["duration"] or 0) for f in files)
                       or float(first["runtime"] or 0) * 60)
            mine = set()
            for f in files:
                for n in (os.path.splitext(os.path.basename(f["path"]))[0],
                          os.path.basename(os.path.dirname(f["path"]))):
                    if flat(n):
                        mine.add(flat(n))
            rows = []
            for r in pd_tracker.find(title, year, first["imdb_id"] or "")[:40]:
                rows.append({"id": str(r.get("id") or ""), "name": r.get("name") or "",
                             "size": int(r.get("size") or 0), "seeds": int(r.get("seeds") or 0),
                             "pack": False})
            for pack, film in packs.get(flat(title), []):
                if year and film.get("year") and abs(int(film["year"]) - year) > 1:
                    continue
                rows.append({"id": "pack:" + str(film.get("key")),
                             "name": os.path.basename(str(film.get("path") or "")),
                             "size": int(film.get("size") or 0),
                             "seeds": int(held.get(str(pack.get("hash")).lower()) or 0),
                             "pack": True, "packShort": pd_torrents.pack_short(pack.get("name"))})
            # one cut at a time: a theatrical file is matched against theatrical
            # releases, an extended one against extended - read as one film, the
            # 720p theatrical copy hid behind the extended pack film beside it
            if many_cuts:
                rows = [o for o in rows if cut_label(o["name"]) == cut_name]
            fits = [o for o in rows if S.meets_all(o, wishes, stored, seconds)]
            if not fits:
                continue
            # the release groups whose encodes are on offer for it, for the Group filter
            groups = sorted({S.release_group(o["name"]) for o in rows} & S.TRUSTED_GROUPS)
            best = max(fits, key=lambda o: (S.rank_key(o, wishes, stored, seconds), o["seeds"]))
            best = S.close_call(best, fits, stored, seconds)
            # Every file, not only the biggest of each cut: Upgrade is where a library is
            # cleared out, and a 720p copy beside a 4K one of the same cut was nowhere
            # in it. Where the pick itself is here, the others of that cut are spares.
            picked = flat(os.path.splitext(best["name"])[0])
            picked_here = picked in mine
            for have in sorted(files, key=lambda f: -int(f["size"] or 0)):
                have_name = os.path.basename(have["path"])
                if picked_here and picked in {flat(os.path.splitext(have_name)[0]),
                                              flat(os.path.basename(os.path.dirname(have["path"])))}:
                    continue                      # the one that would be picked: it stays
                # which rules the file here falls outside, and how far, read off its folder
                # and its own name together as a release is read - and its resolution
                # off the picture: "creep-noescape1080" names none, and a 1920x800 film
                # is 1080p, not 800 lines
                lines = S.picture_lines(have["height"], have["width"])
                seen = ("2160p" if lines >= 1700 else "1080p" if lines >= 900
                        else "720p" if lines >= 650 else "")
                read_as = (os.path.basename(os.path.dirname(have["path"])) + " " +
                           have_name + (" " + seen if seen else ""))
                why, far = S.misses({"name": read_as, "size": int(have["size"] or 0)},
                                  wishes, stored, seconds)
                # above what is asked for, below it, or both: the Upgrade tab filters by it
                side = S.which_side({"name": read_as, "size": int(have["size"] or 0)},
                                  lines, stored, seconds)
                if picked_here:
                    why = ["spare copy - the pick, %s, is here" % best["name"]] + why
                out.append({
                    "key": key, "title": title, "year": year or None,
                    # which cut this row is, where the film is held in more than one
                    "cut": (cut_name or "%d min" % round(seconds / 60)) if many_cuts else "",
                    # another copy of a cut whose pick is here: a candidate to clear out
                    "spare": picked_here,
                    "why": why, "far": far, "side": side,
                    "gaps": S.reasons_of({"name": read_as, "size": int(have["size"] or 0)},
                                       lines, wishes, stored, seconds),
                    "have": {"name": have_name, "size": int(have["size"] or 0),
                             "path": have["path"],
                             # 4K is read off the picture, not the name: the Upgrade tab can
                             # leave those files out
                             "uhd": S.picture_lines(have["height"], have["width"]) >= 1700 or bool(
                                 re.search(r"2160p|\buhd\b|\b4k\b", have_name, re.I)),
                             "mbit": (round(S.video_mbit(have_name, int(have["size"] or 0), seconds) or 0, 1)
                                      if S.video_mbit(have_name, int(have["size"] or 0), seconds) else None),
                             "fits": S.meets_all({"name": os.path.basename(os.path.dirname(have["path"]))
                                                + " " + have_name, "size": int(have["size"] or 0)},
                                               wishes, stored, seconds)},
                    "groups": groups,
                    "pick": dict(best, mbit=(round(S.video_mbit(best["name"], best["size"], seconds) or 0, 1)
                                             if S.video_mbit(best["name"], best["size"], seconds)
                                             else None)),
                })
    for o in out:
        o["reasons"] = list(o.get("gaps") or {})
    out.sort(key=lambda o: flat(o["title"]))
    S.UPGRADES.update(at=time.time(), list=out)
    return out


def duplicates():
    """Every film held here more than once: each copy with its size, its bitrate, what
    it is, which rules under Choosing it falls outside, and whether the torrent client
    is seeding it - with the copy Choosing would keep marked."""
    import pd_torrents
    stored = S.read_settings() or {}
    wishes = S.Handler.ordered_wishes(stored)
    # what the client holds, as folders and files on the disk: a copy inside one of
    # them is being seeded, and deleting it is never the answer
    seeding = []
    try:
        cfg = pd_torrents.load()["config"]
        rows = json.loads(pd_torrents.QB(cfg)._call("/api/v2/torrents/info", timeout=20)
                          or b"[]")
        for t in rows:
            where = os.path.normcase(os.path.join(str(t.get("save_path") or ""),
                                                  str(t.get("name") or "")))
            seeding.append((where, str(t.get("state") or "")))
    except Exception:
        pass
    con = S.local().lib.db()
    try:
        files = con.execute(
            "SELECT i.id, i.title, i.year, f.path, f.size, f.duration, f.vcodec, f.acodec, "
            "f.width, f.height FROM item i JOIN file f ON f.item_id = i.id "
            "WHERE i.type = 'movie' AND f.episode_id IS NULL").fetchall()
    finally:
        con.close()
    by_film = {}
    for r in files:
        if r["path"] and os.path.exists(r["path"]):
            by_film.setdefault(r["id"], []).append(r)
    out = []
    for key, copies in by_film.items():
        if len(copies) < 2:
            continue
        seconds = max(float(c["duration"] or 0) for c in copies)
        rows = []
        for c in copies:
            name = os.path.basename(c["path"])
            read_as = os.path.basename(os.path.dirname(c["path"])) + " " + name
            size = int(c["size"] or 0)
            why, far = S.misses({"name": read_as, "size": size}, wishes, stored, seconds)
            here = os.path.normcase(c["path"])
            seed = next((state for where, state in seeding
                         if here == where or here.startswith(where + os.sep)), "")
            mbit = S.video_mbit(read_as, size, seconds)
            rows.append({"path": c["path"], "name": name, "size": size,
                         "mbit": round(mbit, 1) if mbit else None,
                         "codec": c["vcodec"] or "", "sound": c["acodec"] or "",
                         "height": int(c["height"] or 0), "width": int(c["width"] or 0),
                         "why": why, "far": far, "seeding": seed,
                         "rank": S.rank_key({"name": read_as, "size": size}, wishes,
                                          stored, seconds)})
        # One 4K copy and one 1080p copy is a pair kept on purpose: each plays as it
        # lies on the screens that suit it, and neither has to be transcoded. A copy is
        # spare only against another of the same kind, and one is kept of each.
        uhd = lambda r: S.picture_lines(r["height"], r.get("width")) >= 1700 or bool(
            re.search(r"2160p|\buhd\b|\b4k\b", r["name"], re.I))
        for kind in (True, False):
            same = [r for r in rows if uhd(r) == kind]
            if not same:
                continue
            keep = min(same, key=lambda r: (r["far"], [-x for x in r["rank"]]))
            for r in same:
                r["keep"] = r is keep
                r["uhd"] = kind
        # and the one copy Choosing would keep of all of them, for a house that counts a
        # 4K copy beside a 1080p one as a duplicate like any other
        one = min(rows, key=lambda r: (r["far"], [-x for x in r["rank"]]))
        for r in rows:
            r["keepOne"] = r is one
            r.pop("rank", None)
        spare = sum(r["size"] for r in rows if not r["keep"])
        spare_one = sum(r["size"] for r in rows if not r["keepOne"])
        rows.sort(key=lambda r: (not r["keep"], not r.get("uhd"), r["far"]))
        first = copies[0]
        out.append({"key": key, "title": first["title"] or "",
                    "year": int(first["year"] or 0) or None, "copies": rows,
                    "spare": spare, "spareOne": spare_one,
                    # a 4K copy and a 1080p copy and nothing else
                    "pair": spare == 0})
    out.sort(key=lambda o: (o["pair"], -o["spare"]))
    return out


def catalogue_runtime(title, year=0):
    """A film's length from TMDB, for one that is not in the library yet: a new
    release on the tracker has a size but nothing here to divide it by."""
    title = str(title or "").strip()
    if not title:
        return 0.0
    mark = "%s|%d" % (title.lower(), int(year or 0))
    if mark in S.RUNTIMES:
        return S.RUNTIMES[mark]
    seconds = 0.0
    try:
        lib = S.local().lib
        number = 0
        try:
            import pd_streaming
            number = next((int(r.get("tmdb") or 0) for r in pd_streaming.read() or []
                           if str(r.get("title") or "").lower() == title.lower()
                           and (not year or int(r.get("year") or 0) == int(year))
                           and r.get("tmdb")), 0)
        except Exception:
            number = 0
        if not number:
            found = lib.tmdb("/search/movie", query=title,
                             **({"year": int(year)} if year else {}))
            hits = found.get("results") or []
            number = int(hits[0].get("id") or 0) if hits else 0
        if number:
            seconds = float((lib.tmdb("/movie/%d" % number) or {}).get("runtime") or 0) * 60
    except Exception:
        seconds = 0.0
    if len(S.RUNTIMES) > 500:
        S.RUNTIMES.clear()
    S.RUNTIMES[mark] = seconds
    return seconds


def episode_runtimes():
    """The usual length of an episode of each programme here, by flattened name."""
    con = S.local().lib.db()
    by = {}
    try:
        for r in con.execute("SELECT i.title AS t, f.duration AS d FROM file f "
                             "JOIN episode e ON f.episode_id = e.id "
                             "JOIN item i ON e.item_id = i.id WHERE f.duration > 60"):
            by.setdefault(re.sub(r"[^a-z0-9]+", "", str(r["t"] or "").lower()),
                          []).append(float(r["d"]))
    except Exception:
        by = {}
    finally:
        con.close()
    return {k: sorted(v)[len(v) // 2] for k, v in by.items() if v}


def ready_at(first):
    """When an episode first posted at `first` may be taken: an hour later, or at the
    end of the night it was posted in, whichever is later."""
    at = first + S.SETTLE_FOR
    here = time.localtime(first)
    hour = here.tm_hour
    dark = (hour >= S.NIGHT_FROM or hour < S.NIGHT_TO) if S.NIGHT_FROM > S.NIGHT_TO         else (S.NIGHT_FROM <= hour < S.NIGHT_TO)
    if dark:
        day = time.mktime((here.tm_year, here.tm_mon, here.tm_mday, S.NIGHT_TO, 0, 0,
                           0, 0, -1))
        if hour >= S.NIGHT_TO:
            day += 86400                  # posted before midnight: the next morning
        at = max(at, day)
    return at


def seasons_named(name):
    """Which seasons a release name covers, as a set. Empty when it names none."""
    out = set()
    for first, last in S.SEASON_RUN.findall(str(name or "")):
        a = int(first)
        b = int(last) if last else a
        if 0 < a <= b <= 60:
            out.update(range(a, b + 1))
    return out


def season_gaps(title, tmdb=0):
    """Seasons of one programme this house cannot reach at all.

    What exists comes from TMDB, through the key the library already holds. What can
    be reached is a file on the disk or an episode a pack offers - a pack is a link,
    not a download, so a season it carries is one press from being watchable.

    `tmdb` is the catalogue's number for a programme the library does not hold yet:
    one chosen off the list of what is being watched has no row here to read it from.
    """
    con = S.local().lib.db()
    try:
        row = con.execute("SELECT id, tmdb_id FROM item WHERE type='show' AND title=?",
                          (str(title),)).fetchone()
        number = int((row["tmdb_id"] if row else 0) or tmdb or 0)
        if not number:
            return {"show": title, "every": [], "here": [], "missing": []}
        files = {int(r["season"]) for r in con.execute(
            "SELECT DISTINCT e.season AS season FROM episode e "
            "JOIN file f ON f.episode_id = e.id WHERE e.item_id=?", (row["id"],))
            if r["season"] is not None} if row else set()
    finally:
        con.close()
    try:
        said = S.local().lib.tmdb("/tv/%d" % number) or {}
    except Exception:
        said = {}
    every = sorted({int(x.get("season_number") or 0) for x in (said.get("seasons") or [])
                    if int(x.get("season_number") or 0) > 0})
    if not every:
        return {"show": title, "every": [], "here": sorted(files), "missing": []}
    # and what the packs carry, which is what makes a season reachable without
    # anything having been downloaded
    offered = set()
    try:
        import pd_torrents
        flat = re.sub(r"[^a-z0-9]+", "", str(title).lower())
        for pack in pd_torrents.load()["packs"]:
            for film in pack.get("films") or []:
                if film.get("kind") != "episode":
                    continue
                if re.sub(r"[^a-z0-9]+", "",
                          str(film.get("name") or "").lower()) != flat:
                    continue
                if film.get("season"):
                    offered.add(int(film["season"]))
    except Exception:
        offered = set()
    here = sorted(files | offered)
    return {"show": title, "every": every, "here": here,
            "missing": [s for s in every if s not in files and s not in offered]}


def top_shows(fresh=False):
    """The programmes most watched now, from the catalogue: trending this week and
    popular, a few pages of each, kept a day.

    Each carries its catalogue number, which is what puts one in the library before
    the library holds a single file of it.
    """
    where = os.path.join(S.ROOT, "top_shows.json")
    try:
        with open(where, encoding="utf-8") as f:
            kept = json.load(f)
        shows = kept.get("shows") or []
        # kept before the Swedish list was mixed in: read again
        dated = shows and kept.get("v") != S.TOP_SHOWS_V
        if not fresh and not dated and \
                time.time() - float(kept.get("at") or 0) < S.TOP_SHOWS_EVERY:
            return shows
    except (OSError, ValueError):
        kept = {}
    out, seen = [], set()
    lib = S.local().lib
    # Swedish series airing in the last three months, most watched first: the world's
    # lists carry almost none, and they are what this house watches as well
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
    sources = [("/trending/tv/week", {}), ("/tv/popular", {}),
               ("/discover/tv", {"with_origin_country": "SE", "sort_by": "popularity.desc",
                                 "air_date.gte": since})]
    try:
        named = {int(g["id"]): str(g["name"]) for g in
                 (lib.tmdb("/genre/tv/list") or {}).get("genres") or []}
    except Exception:
        named = {}
    home = []
    for path, extra in sources:
        for page in ((1, 2) if extra else (1, 2, 3)):
            try:
                said = lib.tmdb(path, page=page, **extra) or {}
            except Exception:
                continue
            for r in said.get("results") or []:
                number = int(r.get("id") or 0)
                if not number or number in seen:
                    continue
                seen.add(number)
                (home if extra else out).append({"tmdb": number,
                            "show": S.show_name(r),
                            "year": int(str(r.get("first_air_date") or "0")[:4] or 0),
                            "overview": str(r.get("overview") or ""),
                            "poster": r.get("poster_path") or "",
                            "votes": int(r.get("vote_count") or 0),
                            "rating": float(r.get("vote_average") or 0),
                            "genres": [named[g] for g in r.get("genre_ids") or []
                                       if g in named],
                            # where it was made: "US", "KR", "JP"
                            "countries": [str(c) for c in r.get("origin_country") or []]})
    if not out:
        return kept.get("shows") or []
    out = S.mix_in(out, home, S.TOP_SHOWS_HOME_EVERY)
    try:
        with open(where, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "v": S.TOP_SHOWS_V, "shows": out}, f)
    except OSError:
        pass
    return out


def mix_in(world, home, every):
    """The world's list with this country's woven through it, one in every `every`
    places, each list in its own order - forty Swedish series after a hundred and ten
    of everyone's were never reached. What is left of either runs on at the end."""
    out, w, h = [], list(world), list(home)
    while w or h:
        if h and (len(out) % every == every - 1 or not w):
            out.append(h.pop(0))
        else:
            out.append(w.pop(0))
    return out


def show_name(r):
    """What a series from the catalogue is called here: a Swedish one by its Swedish
    name, which is what its releases carry - "Cyklopernas land", not "The Land of the
    Cyclops", which no release is named."""
    if str(r.get("original_language") or "") == "sv" and r.get("original_name"):
        return str(r["original_name"])
    return str(r.get("name") or r.get("original_name") or "")


def show_rows():
    """A library entry for every programme ticked under Top shows.

    Ticked means in the library. One no file or pack has reached yet still needs a
    page to open and a card on the shelf, so it is given a row of its own - its
    title, poster and summary from the catalogue - and the episodes a pack brings
    fold into that row when they arrive. Returns the library key for each.
    """
    from pd_library import title_key
    keys = {}
    con = S.local().lib.db()
    try:
        for one in S.library_shows():
            number = int(one.get("tmdb") or 0)
            row = con.execute("SELECT id FROM item WHERE type='show' AND tmdb_id=?",
                              (number,)).fetchone() if number else None
            if not row:
                row = con.execute("SELECT id FROM item WHERE type='show' AND title=?",
                                  (one["show"],)).fetchone()
            if row:
                keys[number or one["show"]] = row["id"]
                continue
            if not number:
                continue
            key = title_key("show", one["show"], 0)
            con.execute("""INSERT OR IGNORE INTO item (id, type, title, sort_title, year,
                                                       added, identified)
                           VALUES (?, 'show', ?, ?, 0, ?, 0)""",
                        (key, one["show"], one["show"], int(time.time())))
            con.commit()
            keys[number] = key
            try:
                S.local().lib.rematch(key, number)   # the poster, the summary, the year
            except Exception:
                pass
    finally:
        con.close()
    return keys


def popular_everything():
    """The whole list of what is being watched now, most watched first.

    Held or carried by a pack, a programme is its own card. Otherwise it is a card
    with an Add band: pressed, it goes on the Top shows list and into the library,
    and opens there.
    """
    shows = S.top_shows()
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    out = []
    con = S.local().lib.db()
    try:
        held, named = {}, {}
        for r in con.execute("SELECT id, tmdb_id, title FROM item WHERE type='show'"):
            if r["tmdb_id"]:
                held[int(r["tmdb_id"])] = r["id"]
            named[flat(r["title"])] = r["id"]
        try:
            import pd_torrents
            offered = {flat(o.get("title")): o for o in (pd_torrents.offered_shows() or [])}
        except Exception:
            offered = {}
        for at, one in enumerate(shows):
            number = int(one.get("tmdb") or 0)
            key = held.get(number) or named.get(flat(one.get("show")))
            got = S.local().metadata_for(con, str(key), brief=True) if key else None
            if not got and flat(one.get("show")) in offered:
                got = dict(offered[flat(one.get("show"))])
            if not got:
                got = {"ratingKey": "tv%d" % number, "type": "show",
                       "title": one.get("show") or "", "year": one.get("year") or None,
                       "summary": one.get("overview") or "",
                       "thumb": ("/art/tv%d/poster" % number) if one.get("poster") else None,
                       # for the page's genre and decade filters
                       "genres": list(one.get("genres") or []),
                       "rating": one.get("rating"),
                       "addable": True, "tmdb": number}
            got["popularity"] = len(shows) - at
            # where it was made, held or not, for the page's country filter
            got["countries"] = list(one.get("countries") or [])
            out.append(got)
        # And every other series this house has, after the trending ones, most popular
        # on TMDB first: the TV tab opens here, and a series not trending this week was
        # nowhere on it.
        trending = len(out)
        numbers = {}
        listed = {str(x.get("ratingKey")) for x in out}
        for r in con.execute("SELECT id, countries, tmdb_id FROM item WHERE type='show' "
                             "ORDER BY title COLLATE NOCASE").fetchall():
            if str(r["id"]) in listed:
                continue
            got = S.local().metadata_for(con, str(r["id"]), brief=True)
            if not got:
                continue
            got["popularity"] = 0
            got["countries"] = [c for c in str(r["countries"] or "").split(",") if c]
            listed.add(str(r["id"]))
            numbers[str(r["id"])] = int(r["tmdb_id"] or 0)
            out.append(got)
        # and the ones only a pack carries
        for o in offered.values():
            if str(o.get("ratingKey")) not in listed and not any(
                    flat(x.get("title")) == flat(o.get("title")) for x in out):
                got = dict(o)
                got["popularity"] = 0
                listed.add(str(o.get("ratingKey")))
                out.append(got)
        out = S.local().rank_popular(out, trending, "show", numbers)
    finally:
        con.close()
    # each one not held: downloadable, streamed, being asked about, or only to request
    return S.local().with_sources(out)


def add_popular(show, number):
    """Tick one programme under Top shows and hand back its library key."""
    stored = S.read_settings() or {}
    listed = S.library_shows(stored)
    if not any(int(x["tmdb"] or 0) == int(number) for x in listed):
        listed.append({"show": str(show), "tmdb": int(number)})
        stored["libraryShows"] = listed
        S.write_settings(stored)
    keys = S.show_rows()
    return keys.get(int(number)) or keys.get(str(show)) or ""


def popular_shows(most=40):
    """The programmes ticked under Top shows, most watched first.

    In the order of the list they were chosen from; a ticked one that has since
    dropped off that list goes at the end rather than disappearing. With nothing
    ticked, the popular programmes this house can already put on stand in instead,
    so the row is never simply empty.
    """
    shows = S.top_shows()
    rank = {int(x.get("tmdb") or 0): len(shows) - at for at, x in enumerate(shows)}
    chosen = S.library_shows()
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    out = []
    if chosen:
        keys = S.show_rows()
        con = S.local().lib.db()
        try:
            for one in sorted(chosen, key=lambda x: -rank.get(int(x.get("tmdb") or 0), 0)):
                key = keys.get(int(one.get("tmdb") or 0) or one["show"])
                got = S.local().metadata_for(con, str(key), brief=True) if key else None
                if got:
                    got["popularity"] = rank.get(int(one.get("tmdb") or 0), 0)
                    out.append(got)
        finally:
            con.close()
        return out[:most]
    con = S.local().lib.db()
    try:
        held, named = {}, {}
        for r in con.execute("SELECT id, tmdb_id, title FROM item WHERE type='show'"):
            if r["tmdb_id"]:
                held[int(r["tmdb_id"])] = r["id"]
            named[flat(r["title"])] = r["id"]
        try:
            import pd_torrents
            offered = {flat(o.get("title")): o for o in (pd_torrents.offered_shows() or [])}
        except Exception:
            offered = {}
        for at, one in enumerate(shows):
            key = held.get(int(one.get("tmdb") or 0)) or named.get(flat(one.get("show")))
            got = S.local().metadata_for(con, str(key), brief=True) if key else None
            if not got and flat(one.get("show")) in offered:
                got = dict(offered[flat(one.get("show"))])
            if got:
                got["popularity"] = len(shows) - at
                out.append(got)
            if len(out) >= most:
                break
    finally:
        con.close()
    return out


def shelf_series(con, shelf):
    """The series a collection holds most of, or "" when it holds more films than
    episodes - which collection is a series' own, for its page's Shuffle."""
    keys = [str(k) for k in S.Handler.collection_keys(con, shelf)]
    shows, films = {}, 0
    episodes = [k for k in keys if k.startswith("e")]
    for i in range(0, len(episodes), 400):
        part = episodes[i:i + 400]
        for r in con.execute("SELECT item_id FROM episode WHERE id IN (%s)"
                             % ",".join("?" * len(part)), part):
            shows[str(r["item_id"])] = shows.get(str(r["item_id"]), 0) + 1
    for k in keys:
        season = re.match(r"^([0-9a-f]{12})-s\d+$", k)
        if season:
            shows[season.group(1)] = shows.get(season.group(1), 0) + 1
        elif re.fullmatch(r"[0-9a-f]{12}", k):
            row = con.execute("SELECT type FROM item WHERE id=?", (k,)).fetchone()
            if row and row["type"] == "show":
                shows[k] = shows.get(k, 0) + 1
            elif row:
                films += 1
    if not shows or sum(shows.values()) <= films:
        return ""
    return max(shows, key=lambda s: shows[s])


def series_shelves(who):
    """One viewer's collections that hold series rather than films - the Start tab's
    Shuffle row: a card each, the series it holds most of as its picture."""
    stored = S.read_settings() or {}
    mine = stored if who == "me" else ((stored.get("users") or {}).get(who) or {})
    out = []
    con = S.local().lib.db()
    try:
        for c in mine.get("collections") or []:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            show = S.shelf_series(con, c)
            if not show:
                continue
            out.append({"ratingKey": "coll:" + str(c["id"]), "type": "shelf",
                        "title": str(c.get("name") or ""), "shelf": str(c["id"]),
                        "art": "/art/%s/backdrop" % show, "thumb": "/art/%s/poster" % show,
                        "series": show})
        # and a series shuffled from its own page with no collection of it: that round,
        # so the card goes on where the series page left it
        held = {o["series"] for o in out}
        for cid, one in (mine.get("shuffles") or {}).items():
            show = str(cid)[5:]
            if not re.fullmatch(r"show:[0-9a-f]{12}", str(cid)) or show in held:
                continue
            if not isinstance(one, dict) or not one.get("order"):
                continue
            row = con.execute("SELECT title FROM item WHERE id=? AND type='show'", (show,)).fetchone()
            if not row:
                continue
            held.add(show)
            out.append({"ratingKey": "coll:" + str(cid), "type": "shelf",
                        "title": str(row["title"] or ""), "shelf": str(cid),
                        "art": "/art/%s/backdrop" % show, "thumb": "/art/%s/poster" % show,
                        "series": show})
    finally:
        con.close()
    return S.shuffled_last_first(out, mine.get("shuffles") or {})


def shuffled_last_first(cards, rounds):
    """The Shuffle row in the order its shuffles were last used, the latest first; one
    never used keeps its place after them."""
    def used(card):
        sid = str(card.get("shelf") or "")
        return max(S.Handler.round_stamp(rounds.get(sid)),
                   S.Handler.round_stamp(rounds.get("coll:" + sid)))
    return sorted(cards, key=lambda c: -used(c))


def series_episode_keys(con, show):
    """Every episode of a series here: the library's own, then what only a pack carries."""
    rows = con.execute("SELECT id FROM episode WHERE item_id=? ORDER BY season, number",
                       (str(show),)).fetchall()
    here = [str(r["id"]) for r in rows]
    title = con.execute("SELECT title FROM item WHERE id=?", (str(show),)).fetchone()
    more = []
    if title:
        try:
            import pd_torrents
            more = [str(k) for k in pd_torrents.offered_keys(title["title"], None)]
        except Exception:
            more = []
    return here + [k for k in more if k not in here]


def library_shows(stored=None):
    """The programmes chosen to stand in the library, each with its catalogue number."""
    stored = S.read_settings() if stored is None else stored
    out = []
    for one in ((stored or {}).get("libraryShows") or []):
        if isinstance(one, dict) and str(one.get("show") or "").strip():
            out.append({"show": str(one["show"]).strip(),
                        "tmdb": int(one.get("tmdb") or 0)})
    return out


def stock_shows(most=2):
    """Put the chosen programmes in the library, a season at a time, as links.

    A season nothing here can reach is looked for on the tracker and the best pack of
    it added as a link - never a download: the episodes stand on the TV shelf with a
    button each, and a file comes only when somebody plays one. A pack carrying many
    seasons fills them all at once, so most programmes take a pass or two. Kept to a
    couple of seasons a pass: each one is a search on the tracker.
    """
    added = 0
    for one in S.library_shows():
        if added >= most:
            break
        gaps = S.season_gaps(one["show"], one["tmdb"])
        # one season a programme a pass: the pack added may carry the rest, and the
        # next pass reads the gaps again rather than searching for seasons it now has
        # a season that found nothing waits a day: two programmes with an unfillable
        # first season took both searches of every pass and the rest never came up
        now = time.time()
        due = [s for s in gaps["missing"]
               if now - S.EMPTY_SEASONS.get((one["show"].lower(), s), 0) > S.EMPTY_FOR]
        if due:
            said = S.fill_season(one["show"], due[0])
            if not isinstance(said, dict) or said.get("error"):
                S.EMPTY_SEASONS[(one["show"].lower(), due[0])] = now
            added += 1
    return added


def series_names(title):
    """English names the catalogue knows a series by besides its own: "House M.D."."""
    key = str(title or "").strip().lower()
    got = S.SERIES_NAMES.get(key)
    if got and time.time() - got[0] < 86400:
        return got[1]
    names = []
    hit = None
    try:
        lib = S.local().lib
        hits = (lib.tmdb("/search/tv", query=str(title)) or {}).get("results") or []
        hit = next((h for h in hits if str(h.get("name") or "").lower() == key),
                   hits[0] if hits else None)
        if hit:
            alts = (lib.tmdb("/tv/%d/alternative_titles" % int(hit["id"])) or {}).get(
                "results") or []
            flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
            for a in alts:
                name = str(a.get("title") or "").strip()
                if (a.get("iso_3166_1") in ("US", "GB", "") and name
                        and flat(name) != flat(title) and flat(title) in flat(name)
                        and name not in names):
                    names.append(name)
    except Exception:
        names = []
    S.SERIES_NAMES[key] = (time.time(), names)
    S.SERIES_YEAR[key] = int(str((hit or {}).get("first_air_date") or "0")[:4] or 0)
    return names


def release_year(name):
    """The first year in a release name, or 0. 2160p and 1080p are no years."""
    m = re.search(r"(?<!\d)(?:19|20)\d\d(?![\dp])", str(name or ""))
    return int(m.group(0)) if m else 0


def twin_verdict(year, top, own, others):
    """Whether a release fits a series whose name others share: "sure", "unsure" (listed,
    not put forward) or "no" (not listed). Each series is {year, seasons}; `own` is None
    when it is not known which is meant. A name nobody shares is not judged."""
    if not others:
        return "sure"
    every = ([own] if own else []) + list(others)
    # a year none of them began in says nothing: a re-release's
    if not any(abs(int(year) - s["year"]) <= 1 for s in every):
        year = 0
    fits = lambda s: ((not year or abs(int(year) - s["year"]) <= 1)
                      and (not s["seasons"] or int(top) <= s["seasons"]))
    if own is None:
        return "unsure"
    if not fits(own):
        return "no"
    return "unsure" if any(fits(o) for o in others) else "sure"


def _held_series_number(title):
    """The catalogue number of the one series held under this title, else 0."""
    try:
        con = S.local().lib.db()
        try:
            rows = con.execute("SELECT tmdb_id FROM item WHERE type='show' AND lower(title)=?",
                               (str(title or "").strip().lower(),)).fetchall()
        finally:
            con.close()
    except Exception:
        return 0
    return int(rows[0][0] or 0) if len(rows) == 1 else 0


def series_twins(title, tmdb=0):
    """(this series, the others of exactly this name), each {id, year, seasons, votes};
    (None, []) when the catalogue did not answer or the name is not shared. Only the
    catalogue's answer is kept, a failure for a minute: which series is held is read each
    time, or one added since stayed unknown for the day."""
    key = str(title or "").strip().lower()
    got = S.SERIES_TWINS.get(key)
    if got and time.time() - got[0] < 86400:
        rows = got[1]
    else:
        flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
        rows = []
        try:
            lib = S.local().lib
            hits = (lib.tmdb("/search/tv", timeout=6, query=str(title)) or {}).get("results") or []
            same = [h for h in hits if flat(h.get("name")) == flat(title)][:6]
            if len(same) > 1:
                for h in same:
                    said = lib.tmdb("/tv/%d" % int(h["id"]), timeout=6) or {}
                    rows.append({"id": int(h["id"]),
                                 "year": int(str(h.get("first_air_date") or "0")[:4] or 0),
                                 "seasons": int(said.get("number_of_seasons") or 0),
                                 "votes": int(h.get("vote_count") or 0)})
        except Exception:
            S.SERIES_TWINS[key] = (time.time() - 86400 + 60, [])
            return None, []
        if len(S.SERIES_TWINS) > 200:
            S.SERIES_TWINS.clear()
        S.SERIES_TWINS[key] = (time.time(), rows)
    number = int(tmdb or 0) or S._held_series_number(title)
    known = [r for r in rows if r["votes"] >= 5 or r["id"] == number]
    own = next((r for r in known if r["id"] == number), None)
    # a number the name does not lead to: nothing to compare it with
    if len(known) < 2 or (number and not own):
        return None, []
    return own, [r for r in known if r is not own]
