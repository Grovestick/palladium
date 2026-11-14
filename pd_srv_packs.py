"""Packs and the tracker: a series' packs and seasons, what is coming in, what the tracker takes, and keeping up with the shows followed.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import re
import threading
import time
from typing import Any

import pd_h

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def added_pack_for(name, seasons, packs):
    """The hash of the added pack a tracker row is, or "": the same name, or the same seasons
    from the same release group, since a torrent names itself in its own shorthand."""
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    group = S.release_group(name)
    for p in packs:
        if (flat(p.get("name")) == flat(name)
                or (group and S.release_group(p.get("name")) == group
                    and S.release_year(p.get("name")) == S.release_year(name)
                    and sorted(p.get("seasons") or []) == sorted(seasons))):
            return str(p.get("hash") or "")
    return ""


def release_group(name):
    """Who packed a release, from the end of its name: "...HEVC-d3g" is d3g; "" for none."""
    m = re.search(r"-([A-Za-z0-9]{2,})\)?\s*$", str(name or "").strip())
    said = m.group(1).lower() if m else ""
    # a season run's end - "S01-S09" - is not a group
    return "" if re.fullmatch(r"[se]?\d+", said) else said


def index_of(row):
    """A pack file's number from a row, -1 for none. Not `or -1`: file 0 is the first of every
    pack and the only one of a single film, and it was never fetched or sent."""
    said = row.get("index")
    return int(said) if said not in (None, "") else -1


def show_packs(title, look=False, tmdb=0):
    """The packs for one programme: those already carrying it, and - when asked to
    look - what the tracker holds, best first by the house's wishes, then by how many
    seasons it covers, then by how many are carrying it.
    """
    import pd_tracker, pd_torrents
    here: list[dict] = pd_torrents.packs_for_show(title)
    # how many carry each pack here, from the tracker's list, matched by release name
    squash = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    try:
        seeding = {squash(r.get("name")): int(r.get("seeds") or 0)
                   for r in list(pd_tracker.read().values())}
    except Exception:
        seeding = {}
    here = [dict(p, seeds=seeding[squash(p.get("name"))])
            if squash(p.get("name")) in seeding else p for p in here]
    # one the tracker's list no longer carries: the client's own count of its swarm
    swarm = pd_torrents.seeders([p["hash"] for p in here if p.get("seeds") is None])
    here = [dict(p, seeds=swarm[str(p["hash"]).lower()])
            if p.get("seeds") is None and str(p["hash"]).lower() in swarm else p
            for p in here]
    # the same release written two ways: neither the seeders nor the row would pair
    def shape(name):
        low = " " + str(name or "").lower() + " "
        res = next((r for r in ("2160p", "1080p", "720p", "480p") if r in low), "")
        src = next((k for k in ("remux", "bluray", "web-dl", "webdl", "webrip", "hdtv",
                                "dvdrip") if k in low), "")
        return tuple(sorted(S.seasons_named(name))), res, src

    # the words of a release, less the punctuation each spells differently
    def bits(name):
        plain = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower())
        return set(w for w in plain.split() if len(w) > 1)

    def alike(a, b):
        x, y = bits(a), bits(b)
        return (len(x & y) / float(len(x | y))) if x and y else 0.0

    #: how much of two names must agree before they are called one release
    SAME = 0.45

    def closest(name, rows):
        """The one row of the same shape whose name agrees most, or nothing."""
        want = shape(name)
        best, score = None, 0.0
        for one in rows:
            if shape(one[0]) != want:
                continue
            how = alike(name, one[0])
            if how > score:
                best, score = one, how
        return best if score >= SAME else None

    catalogue = []
    if any(p.get("seeds") is None for p in here) or look:
        flat = re.sub(r"[^a-z0-9]+", "", str(title).lower())
        try:
            for r in list(pd_tracker.read().values()):
                one = str(r.get("name") or "")
                if flat and flat not in re.sub(r"[^a-z0-9]+", "", one.lower()):
                    continue
                if shape(one)[0]:
                    catalogue.append((one, int(r.get("seeds") or 0)))
        except Exception:
            catalogue = []
    here = [dict(p, seeds=(closest(p.get("name"), catalogue) or (None, None))[1])
            if p.get("seeds") is None and closest(p.get("name"), catalogue) else p
            for p in here]
    # a torrent of one episode - what keeping up adds - is not a pack to choose
    here = [dict(p, single=True)
            if re.search(r"[sS]\d{1,2}[eE]\d{1,3}", str(p.get("name") or "")) else p
            for p in here]
    choice = pd_torrents.show_choice(title)
    chosen = [h for h in choice["packs"] if any(p["hash"] == h for p in here)]
    # With nothing chosen, what an episode comes from now: per season, the first pack
    # added that carries it. Said on the page rather than left as "by itself".
    auto = []
    if not chosen:
        first = {}
        for p in here:
            if p.get("single"):
                continue
            for n in p["seasons"]:
                first.setdefault(n, p["hash"])
        for p in here:
            mine = sorted(n for n, h in first.items() if h == p["hash"])
            if mine:
                auto.append({"hash": p["hash"], "name": p["name"], "seasons": mine,
                             "seeds": p.get("seeds")})
    # the seasons two or more chosen packs carry, and which of them has each now
    carriers = {}
    for p in here:
        if p["hash"] in chosen:
            for n in p["seasons"]:
                carriers.setdefault(n, []).append(p["hash"])
    overlaps = []
    for n in sorted(carriers):
        if len(carriers[n]) > 1:
            now = choice["season"].get(str(n))
            if now not in carriers[n]:
                now = next(h for h in reversed(chosen) if h in carriers[n])
            overlaps.append({"season": n, "packs": carriers[n], "using": now})
    found = []
    episodes = []
    if look:
        # The name alone finds the newest of everything with that word in it - a one-word
        # name is a hundred other things - so also the complete run, and the other names
        # the series goes by, as a release spells them.
        asks = [str(title), str(title) + " complete"]
        for other in S.series_names(title)[:2]:
            asks += [other, other + " complete"]
        import pd_streaming as _known
        if pd_tracker.asked_within(str(title), 0, _known.CHECKED_FOR):
            # asked before - behind, or by an earlier look: answered from what is known
            # at once, and asked again behind for anything newer. The page waited on
            # every search in turn each time it was opened.
            def refresh(asks=list(dict.fromkeys(asks))):
                for one in asks:
                    pd_tracker.ask_for(one, 0, 600)
            threading.Thread(target=pd_tracker.quietly(refresh), daemon=True).start()
        else:
            for one in dict.fromkeys(asks):
                pd_tracker.ask_for(one, 0, 600)
        flat = re.sub(r"[^a-z0-9]+", "", str(title).lower())
        stored = S.read_settings() or {}
        wishes = S.Handler.ordered_wishes(stored)
        # the series by name, not every series with that word in it: what stands
        # before the season, the run or the year is its name, or one of its others
        names = {flat} | {re.sub(r"[^a-z0-9]+", "", n.lower())
                          for n in S.series_names(title)}
        import pd_streaming
        own, others = S.series_twins(title, tmdb)
        for row in list(pd_tracker.read().values()):
            name = str(row.get("name") or "")
            if flat not in re.sub(r"[^a-z0-9]+", "", name.lower()):
                continue
            # a release group's tag in front is not part of the series' name
            bare_name = pd_streaming.GROUP_TAG.sub("", name)
            head = re.split(r"(?i)[ ._\-(\[]+(?:s\d{1,2}\b|season|complete|"
                            r"(?:19|20)\d\d\b|\d{3,4}p\b)", bare_name)[0]
            # one episode: the name is what stands before its S04E11 - "S04E11" is no
            # season mark on its own, and the name ran on into it
            ep_at = pd_streaming.EPISODE_MARK.search(bare_name)
            if ep_at:
                head = bare_name[:ep_at.start()].rstrip(" ._-([")
            if re.sub(r"[^a-z0-9]+", "", re.sub(r"(?i)^the\s+", "", head).lower())                     not in names and re.sub(r"[^a-z0-9]+", "", head.lower()) not in names:
                continue
            # a year straight after the name that is not the series' own: a film of
            # the same name - House 1985 beside House, which began in 2004
            began = S.SERIES_YEAR.get(str(title or "").strip().lower(), 0)
            said_year = re.search(r"(?:19|20)\d\d", name[len(head):len(head) + 8])
            if not others and began and said_year and abs(int(said_year.group(0)) - began) > 1:
                continue
            one_ep = pd_streaming.episode_of_release(name)
            top = one_ep[0] if one_ep else max(S.seasons_named(name), default=0)
            verdict = S.twin_verdict(S.release_year(name), top, own, others)
            if verdict == "no":
                continue
            if one_ep:
                # one episode, not a pack: listed on its own, a season at a time - a
                # series that comes out week by week has nothing else
                episodes.append({"id": str(row.get("id") or ""), "name": name,
                                 "season": one_ep[0], "episode": one_ep[1],
                                 "size": int(row.get("size") or 0),
                                 "seeds": int(row.get("seeds") or 0)})
                continue
            runs = S.seasons_named(name)
            complete = bool(re.search(r"complete|collection", name, re.I))
            if not runs and not complete:
                continue
            low = " " + name.lower() + " "
            found.append({"id": str(row.get("id") or ""), "name": name,
                          "seasons": sorted(runs), "complete": complete,
                          "size": int(row.get("size") or 0),
                          "seeds": int(row.get("seeds") or 0),
                          "unsure": verdict == "unsure",
                          "fits": all(S.release_is(low, k, w) for k, w in wishes)})
        # the one Choosing puts forward is marked green; the list is by seeders
        best = max([o for o in found if o["fits"] and not o["unsure"]],
                   key=lambda o: (S.rank_key(o, wishes, stored, 0),
                                  len(o["seasons"]), o["seeds"]), default=None)
        for o in found:
            o["pick"] = o is best
        # which added pack a row is, so the page draws one line for one release
        by_name = {squash(p.get("name")): p for p in here if not p.get("single")}
        packs = [p for p in here if not p.get("single")]
        # one added pack answers for one row: the name decides among those of the same
        # shape, and a pack already spoken for is not offered twice
        taken = set()
        for row in found:
            mine = by_name.get(squash(row.get("name")))
            if not mine:
                best, score = None, 0.0
                for p in packs:
                    if p.get("hash") in taken or shape(p.get("name")) != shape(row.get("name")):
                        continue
                    how = alike(row.get("name"), p.get("name"))
                    if how > score:
                        best, score = p, how
                mine = best if score >= SAME else None
            if not mine:
                # or the same seasons from the same release group: a torrent names itself
                # in its own shorthand - "br 10bit ddp hevc-d3g" for "1080p BluRay 10Bit
                # DDP5 1 HEVC-d3g" - and the pack added was offered to be added again
                group = S.release_group(row.get("name"))
                mine = next((p for p in packs if p.get("hash") not in taken and group
                             and S.release_group(p.get("name")) == group
                             and sorted(p.get("seasons") or []) == row.get("seasons")), None)
            if mine:
                taken.add(mine.get("hash"))
            row["held"] = str(mine.get("hash")) if mine else ""
        found.sort(key=lambda o: -o["seeds"])
    # the single episodes, a season and an episode at a time, the most carried first
    episodes.sort(key=lambda e: (e["season"], e["episode"], -e["seeds"]))
    return {"show": title, "chosen": chosen, "here": here, "overlaps": overlaps,
            "auto": auto, "found": found, "episodes": episodes[:300],
            # which pack each season comes from, where one was picked
            "seasonFrom": pd_torrents.show_choice(title)["season"]}


def use_show_pack(title, ident="", info_hash="", drop=False, season=0, unlink=False):
    """Choose a pack for a programme, drop one, or say which chosen pack one season
    comes from. A release on the tracker is added as a link first. Dropped with
    `unlink`, the link itself goes too where nothing else holds on to it."""
    import pd_tracker, pd_torrents
    if drop:
        if unlink:
            return pd_torrents.unlink_pack(title, info_hash)
        pd_torrents.drop_show_pack(title, info_hash)
        return {"ok": True}
    if season and not ident:
        pd_torrents.set_season_pack(title, int(season), info_hash)
        return {"ok": True}
    if ident:
        raw = pd_tracker.torrent_of(str(ident))
        if not raw:
            return {"error": "the tracker would not hand it over"}
        said = pd_torrents.add_pack(raw=raw)
        if not said.get("ok"):
            return {"error": said.get("why") or "that pack could not be added"}
        info_hash = said.get("hash")
    pd_torrents.add_show_pack(title, info_hash) if info_hash else         pd_torrents.set_show_pack(title, "")
    # added from a season's page: that season comes from it
    if season and info_hash:
        pd_torrents.set_season_pack(title, int(season), info_hash)
    return {"ok": True, "hash": info_hash or ""}


def season_offers(title, season, ask=True):
    """What the tracker has that would cover one season, best first.

    A pack of that season, or a run of seasons with it inside - both are added as
    links rather than downloads, so the size of the thing is not what matters. Loose
    episodes come last: they only help where nobody has packed the season at all.
    """
    import pd_tracker, pd_torrents
    if ask:
        pd_tracker.ask_for("%s S%02d" % (title, int(season)))
    flat = re.sub(r"[^a-z0-9]+", "", pd_tracker.plain(title).lower())
    stored = S.read_settings() or {}
    wishes = S.Handler.ordered_wishes(stored)
    # the series by name - its own, or one it also goes by - and not every series that
    # starts with it: a spin-off's pack of its own seasons stood for this one's
    names = {flat} | {re.sub(r"[^a-z0-9]+", "", n.lower()) for n in S.series_names(title)}
    own, others = S.series_twins(title)
    added = pd_torrents.packs_for_show(title)
    packs, loose = [], []
    for row in list(pd_tracker.read().values()):
        name = str(row.get("name") or "")
        low = " " + name.lower() + " "
        if not flat or flat not in re.sub(r"[^a-z0-9]+", "", name.lower()):
            continue
        head = re.split(r"(?i)[ ._\-(\[]+(?:s\d{1,2}\b|season|complete|"
                        r"(?:19|20)\d\d\b|\d{3,4}p\b)", name)[0]
        # one episode: the name is what stands before its S04E11
        import pd_streaming as _eps
        ep_at = _eps.EPISODE_MARK.search(name)
        if ep_at:
            head = name[:ep_at.start()].rstrip(" ._-([")
        if (re.sub(r"[^a-z0-9]+", "", re.sub(r"(?i)^the\s+", "", head).lower()) not in names
                and re.sub(r"[^a-z0-9]+", "", head.lower()) not in names):
            continue
        runs = S.seasons_named(name)
        if int(season) not in runs:
            continue
        verdict = S.twin_verdict(S.release_year(name), max(runs), own, others)
        if verdict == "no":
            continue
        one = {"id": str(row.get("id") or ""), "name": name,
               "size": int(row.get("size") or 0), "seeds": int(row.get("seeds") or 0),
               "seasons": sorted(runs), "unsure": verdict == "unsure",
               "held": S.added_pack_for(name, runs, added),
               "fits": all(S.release_is(low, kind, said) for kind, said in wishes)}
        # an episode names one season and one episode; a pack names the season alone
        if re.search(r"[sS]\d{1,2}[eE]\d{1,3}", name):
            loose.append(one)
        else:
            packs.append(one)
    # the one Choosing puts forward is marked; both lists are by seeders
    best = max([o for o in packs if o["fits"] and not o["unsure"]],
               key=lambda o: (S.rank_key(o, wishes, stored, 0),
                              len(o["seasons"]), o["seeds"]), default=None)
    for o in packs + loose:
        o["pick"] = o is best
    packs.sort(key=lambda o: -o["seeds"])
    loose.sort(key=lambda o: -o["seeds"])
    return {"packs": packs, "loose": loose[:40], "pick": best}


def fill_season(title, season):
    """Add the best pack for one season as a link. Nothing is downloaded."""
    import pd_tracker, pd_torrents
    found = S.season_offers(title, season)
    # the one Choosing puts forward - or, where no pack of the season meets it, the
    # best seeded of what there is: a 480p pack was the only one, and the season stood
    # "out, not here yet" every pass because it was not the resolution asked for
    best = found.get("pick") or max(
        [o for o in found.get("packs") or []
         if int(o.get("seeds") or 0) > 0 and not o.get("unsure")],
        key=lambda o: (len(o.get("seasons") or []), int(o.get("seeds") or 0)), default=None)
    if not best:
        return {"error": "nothing on the tracker packs that season",
                "loose": len(found["loose"])}
    raw = pd_tracker.torrent_of(best["id"])
    if not raw:
        return {"error": "the tracker would not hand it over"}
    said = pd_torrents.add_pack(raw=raw)
    said["name"] = best["name"]
    said["seasons"] = best["seasons"]
    return said


def follow_list(stored=None):
    """The programmes this house follows, each as what it is followed for.

    A name on its own means the whole programme, which is what the first version of
    this stored and what somebody means by adding one. The rest say how far back to
    go: forward only leaves the old seasons alone, and a range is a range.
    """
    stored = S.read_settings() if stored is None else stored
    out = []
    for said in ((stored or {}).get("followShows") or []):
        if isinstance(said, str):
            one = {"show": said.strip(), "mode": "all", "from": 0, "to": 0}
        elif isinstance(said, dict):
            one = {"show": str(said.get("show") or "").strip(),
                   "mode": str(said.get("mode") or "all").lower(),
                   "from": int(said.get("from") or 0),
                   "to": int(said.get("to") or 0),
                   # its newest episode downloaded, whatever the house setting says
                   "grab": bool(said.get("grab"))}
        else:
            continue
        if not one["show"]:
            continue
        if one["mode"] not in ("all", "forward", "range"):
            one["mode"] = "all"
        out.append(one)
    # and the series put in the library from the popular list: one tick covers both
    named = {one["show"].lower() for one in out}
    for one in S.library_shows(stored):
        if one["show"].lower() not in named:
            out.append({"show": one["show"], "mode": "all", "from": 0, "to": 0})
    return out


def followed_season(one, season):
    """Whether one season is inside what a programme is followed for."""
    season = int(season or 0)
    if one["mode"] == "all":
        return True
    if one["mode"] == "forward":
        return True                       # new episodes yes, old seasons no - see gaps
    first = one["from"] or 0
    last = one["to"] or 99
    return first <= season <= last


def aired_up_to(show):
    """The newest episode the catalogue says has aired, as (season, number); None
    when it cannot say."""
    try:
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT tmdb_id FROM item WHERE type='show' AND "
                              "lower(title)=lower(?) AND tmdb_id", (str(show),)).fetchone()
        finally:
            con.close()
        if not row:
            return None
        said = S.local()._catalogue("/tv/%d" % int(row["tmdb_id"]), S.local()._airs,
                                  row["tmdb_id"])
        last = said.get("last_episode_to_air") or {}
        at = (int(last.get("season_number") or 0), int(last.get("episode_number") or 0))
        return at if at[0] else None
    except Exception:
        return None


def auto_pick(results, episode):
    """The subtitle fetching ahead takes from a list in offer order: the first for this
    episode; for a film the first; for an episode with none of its own, nothing."""
    for r in results:
        if r.get("episode"):
            return r
    return results[0] if results and not episode else None


def release_flat(name):
    """A release name as letters and digits, its file ending left off: the tracker
    writes "Some Film 2026 1080p ...-GRP" and qBittorrent "Some.Film...-GRP.mkv",
    which never matched, so who asked for it was lost on the way."""
    bare = re.sub(r"\.(mkv|mp4|avi|m4v|ts)$", "", str(name or "").strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", bare.lower())


def _ask_leads():
    try:
        import pd_follow
        one = pd_follow.settings(S.local().lib.config())
        if one.get("on") and one.get("master"):
            got = pd_follow.ask(one, "/follow/leads", 10)
            if isinstance(got.get("rules"), dict):
                S.HOUSE_LEADS["rules"] = got["rules"]
    except Exception:
        pass


def lead_rules():
    """Skip rules: own settings, or on a copy the main server's. Asked per episode
    card, so the copy refreshes every 10 min on a thread and never waits for it."""
    now = time.time()
    if now - S.HOUSE_LEADS["at"] > 600:
        S.HOUSE_LEADS["at"] = now
        threading.Thread(target=S._ask_leads, name="palladium-leads-ask", daemon=True).start()
    held = S.HOUSE_LEADS["rules"]
    if held is not None:
        return held
    import pd_localapi
    return pd_localapi._settings().get("skipStart") or {}


def tracker_coming(owner=True, who=""):
    """Releases taken from the tracker that are coming in now, shaped as the app's
    download list wants them: the line in the menu showed only what came out of packs."""
    out, seen = [], set()
    for d in S.coming_in() + S.landing():
        if d.get("from") == "pack" or d.get("state") == "queued":
            continue
        # being unpacked is listed by coming_in, and by landing as not in yet: once
        if S.release_flat(d["name"]) in seen:
            continue
        seen.add(S.release_flat(d["name"]))
        if not owner and (d.get("who") or "") != who:
            continue
        title, year = d["name"], 0
        tag = None
        try:
            import pd_tracker
            title, year = pd_tracker.bare(d["name"])
            tag = pd_tracker.episode_tag(d["name"])
        except Exception:
            pass
        # the film's own page and poster where the new releases know it
        key, thumb = "dl-" + S.release_flat(d["name"])[:40], None
        # under the key it will have once it is here, read from its name as the library
        # will read it: one set of keys, and its card and its episode are one (2026-11-06)
        try:
            lib = S.local().lib
            con = lib.db()
            try:
                key = lib.key_of_file(con, "show" if tag else "movie",
                                      "X:/coming/%s.mkv" % re.sub(r"\s+", ".", str(d["name"]))) or key
            finally:
                con.close()
        except Exception:
            pass
        extra = {}
        if tag:
            # an episode, or a season: named as one, under its series where the library
            # has it. Matched by name to the new films, "TYSON S01E01" was put on a
            # film called Tyson and stood on the Films shelf.
            show, season, number = tag
            title = show + (" S%02dE%02d" % (season, number) if number else " Season %d" % season)
            extra = {"type": "episode" if number else "season", "grandparentTitle": show,
                     "parentIndex": season, "index": number or None}
            try:
                flat = re.sub(r"[^a-z0-9]+", "", show.lower())
                con = S.local().lib.db()
                try:
                    for r in con.execute("SELECT id, title, poster FROM item WHERE type='show'"):
                        if re.sub(r"[^a-z0-9]+", "", str(r["title"] or "").lower()) == flat:
                            extra["grandparentRatingKey"] = r["id"]
                            extra["grandparentTitle"] = r["title"]
                            title = str(r["title"]) + title[len(show):]
                            thumb = ("/art/%s/poster" % r["id"]) if r["poster"] else None
                            break
                finally:
                    con.close()
            except Exception:
                pass
        # the page it was started from, written down when it was taken: its poster,
        # and pressing it opens that page. Matched by name to the new films, one that
        # had left that list since stood on Home with no picture at all.
        page = ""
        try:
            import pd_tracker
            with pd_tracker.LOCK:
                page = str(((pd_tracker.STATE.get("taken") or {}).get(d.get("hash") or "")
                            or {}).get("key") or "")
        except Exception:
            page = ""
        if page and not tag:
            key, thumb = page, "/art/%s/poster" % page
        try:
            if tag or page:
                raise LookupError("an episode, or a page already known")
            import pd_streaming
            want = re.sub(r"[^a-z0-9]+", "", str(title).lower())
            one = next((r for r in pd_streaming.read() or []
                        if re.sub(r"[^a-z0-9]+", "", str(r.get("title") or "").lower()) == want
                        and (not year or abs(int(r.get("year") or 0) - int(year)) <= 1)), None)
            if one:
                key = one["key"]
                thumb = ("/art/%s/poster" % key) if one.get("poster") else None
        except Exception:
            pass
        out.append({
            "ratingKey": key, "type": extra.pop("type", "movie"), "thumb": thumb,
            "title": title or d["name"], "year": (year or None) if not tag else None,
            **extra,
            "offered": True, "askedAt": int(d.get("added") or 0),
            "offer": {"state": "unpacking" if d.get("state") == "unpacking" else "downloading",
                      "progress": d.get("progress") or 0.0,
                      "mbit": d.get("mbit") or 0.0, "eta": d.get("eta") or None,
                      "who": d.get("who") or "", "size": d.get("size") or 0,
                      "queueKey": "", "mine": False,
                      # straight from the tracker, not out of a pack: stopped from
                      # the strip of what is coming in, not from the film's page
                      "tracker": True,
                      # which release, said short: a film already here showed only a
                      # percentage and Cancel, with nothing to say which version
                      "release": d["name"], "version": S.version_words(d["name"])}})
    return out


def version_words(name):
    """A release as a person tells it from another: "4K WEB-DL HDR - MiON"."""
    low = " " + str(name or "").lower() + " "
    res = ("4K" if re.search(r"2160p|\buhd\b", low) else "1080p" if "1080p" in low
           else "720p" if "720p" in low else "")
    src = ("Remux" if "remux" in low else "BluRay" if re.search(r"blu-?ray|bdrip", low)
           else "WEB-DL" if re.search(r"web-?dl|\bweb\b", low) else "WEBRip" if "webrip" in low
           else "HDTV" if "hdtv" in low else "")
    hdr = "DV" if re.search(r"\bdv\b|dolby.?vision", low) else (
        "HDR" if "hdr" in low else "")
    # the group as it writes itself: MiON, not mion
    got = re.search(r"-([A-Za-z0-9]+)(?:\.(?:mkv|mp4|avi))?\s*$", str(name or ""))
    group = got.group(1) if got else ""
    said = " ".join(w for w in (res, src, hdr) if w)
    return said + (" - " + group if group else "")


def checking_now():
    """The torrents qBittorrent is checking: name, how far, and the drive it reads."""
    if time.time() - S.CHECKS["at"] < 4:
        return S.CHECKS["rows"]
    import pd_torrents
    try:
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        rows = json.loads(qb._call("/api/v2/torrents/info?filter=checking", timeout=6) or b"[]")
    except Exception:
        rows = []
    out = [{"name": re.sub(r"\.(mkv|mp4|avi|m4v|ts)$", "", str(t.get("name") or ""),
                           flags=re.I),
            "progress": float(t.get("progress") or 0),
            "drive": os.path.splitdrive(str(t.get("save_path") or ""))[0].upper(),
            "size": int(t.get("size") or 0)}
           for t in rows if str(t.get("state") or "").startswith("checking")]
    S.CHECKS.update(at=time.time(), rows=out)
    return out


def first_picture(rows, who):
    """Whether this viewer's player is playing - past its first second, not opening."""
    return any(r.get("who") == who and r.get("how") != "syncing" and r.get("state") == "playing"
               and float(r.get("position") or 0) > 0 for r in rows or [])


def copies_coming(rows):
    """Copies going to the other server, from the watching rows, as lines of the downloads
    list: a viewer's row said nothing of how far a copy had got or how long it had left."""
    out = []
    for r in rows:
        if r.get("how") != "syncing":
            continue
        whole, sent = float(r.get("ofMb") or 0), float(r.get("mb") or 0)
        rate = float(r.get("mbit") or 0)
        left = (whole - sent) * 8.388608 / rate if whole > sent and rate > 0 else 0
        out.append({"name": r.get("title") or "a copy", "state": "copying", "from": "copy",
                    "progress": min(1.0, sent / whole) if whole else 0.0,
                    "size": int(whole * 1048576), "mbit": rate, "eta": int(left),
                    "who": r.get("who") or "", "key": r.get("key") or ""})
    return out


def cache_downloads():
    """The downloads coming in on the cache servers - films handed to them because their pack
    lives there - each marked with the machine's name. The last answer, up to a minute old."""
    import pd_torrents
    if pd_torrents._following():
        return []
    if time.time() - S.CACHE_ACTIVE["asked"] > 4:
        S.CACHE_ACTIVE["asked"] = time.time()
        threading.Thread(target=S._ask_cache_downloads, name="palladium-cache-active",
                         daemon=True).start()
    return list(S.CACHE_ACTIVE["rows"]) if time.time() - S.CACHE_ACTIVE["at"] < 60 else []


def _ask_cache_downloads():
    import urllib.request as _ureq
    rows, names = [], set()
    for where, row in list(S.Handler.FOLLOWERS.items()):
        name = row.get("name") or where
        if row.get("revoked") or name in names or time.time() - float(row.get("when") or 0) > 900:
            continue
        try:
            req = _ureq.Request(where.rstrip("/") + "/follow/active",
                                headers={"X-Palladium-House": "1"})
            with _ureq.urlopen(req, timeout=3) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
        except Exception:
            continue
        names.add(name)
        for m in (said.get("MediaContainer") or {}).get("Metadata") or []:
            if isinstance(m, dict) and isinstance(m.get("offer"), dict):
                m["offer"] = dict(m["offer"], on=name, mine=False)
                rows.append(m)
    S.CACHE_ACTIVE.update(at=time.time(), rows=rows)


def ask_other_slot():
    """The other server's /follow/slot: the main server for a cache, the cache for the main
    server. The first one downloading, or the last that answered; None when none did."""
    import urllib.request as _ureq
    import pd_torrents
    import pd_follow
    one = pd_torrents._following()
    if one:
        return pd_follow.ask(one, "/follow/slot", 5)
    said = None
    for where, row in list(S.Handler.FOLLOWERS.items()):
        if row.get("revoked") or time.time() - float(row.get("when") or 0) > 900:
            continue
        try:
            req = _ureq.Request(where.rstrip("/") + "/follow/slot", headers={"X-Palladium-House": "1"})
            with _ureq.urlopen(req, timeout=3) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
        except Exception:
            continue
        if int(said.get("count") or 0):
            return said
    return said


def with_cache_downloads(got, token=None):
    """This server's downloads and then the caches' - everyone's, or one person's - a title
    once, and the tokens left out for anyone but the owner."""
    seen = {m.get("ratingKey") for m in got}
    for m in S.cache_downloads():
        offer = m.get("offer") or {}
        if m.get("ratingKey") in seen or (token is not None and offer.get("token") != token):
            continue
        seen.add(m.get("ratingKey"))
        got.append(m)
    # and what was handed to a cache server and not taken by it yet: on no list, the film
    # read as not asked for and Download was pressed again and again (2026-11-08)
    for m in S.handed_not_taken(token):
        if m.get("ratingKey") not in seen:
            seen.add(m.get("ratingKey"))
            got.append(m)
    if token is not None:
        for m in got:
            (m.get("offer") or {}).pop("token", None)
    return got


def handed_not_taken(token=None):
    """Films handed to the cache server, as the download list shows a title: waiting, on that
    machine, until it starts them and lists them itself. Everyone's, or one person's."""
    import pd_torrents
    if pd_torrents._following():
        return []
    name = next((row.get("name") for row in S.Handler.FOLLOWERS.values()
                 if not row.get("revoked") and row.get("name")), "") or "the cache server"
    out = []
    for r in pd_torrents.for_copy():
        if r.get("move") or (token is not None and str(r.get("token") or "") != token):
            continue
        one = pd_torrents.metadata(str(r.get("key") or ""))
        if not one:
            continue
        one = dict(one, offer=dict(one.get("offer") or {}, state="queued", stage="queued",
                                   progress=0.0, words="Starting on " + name, on=name,
                                   mine=False, who=str(r.get("who") or ""),
                                   token=str(r.get("token") or ""), queueKey=str(r.get("key") or "")))
        out.append(one)
    return out


#: seconds a reading of qBittorrent stands before the next is started
COMING_FRESH = 4


def coming_in():
    """Every download in progress, as qBittorrent was last read: answered at once, never
    waited on. A reading older than COMING_FRESH starts the next one beside the answer.
    Asked in the request, a qBittorrent that was down held Recently added two seconds
    on every look, and a server without one has nothing to wait for (2026-11-14)."""
    if time.time() - S.COMING["at"] >= COMING_FRESH and not S.COMING.get("busy"):
        S.COMING["busy"] = True
        threading.Thread(target=_read_coming_now, name="palladium-coming", daemon=True).start()
    return S.COMING["rows"]


def _read_coming_now():
    try:
        S.COMING["rows"] = S.read_coming()
    except Exception:
        pass
    finally:
        S.COMING["at"] = time.time()
        S.COMING["busy"] = False


def read_coming():
    """Every download in progress, from qBittorrent itself - packs, the tracker and
    anything added by hand - with who asked and why, where the logs know."""
    import pd_torrents, pd_tracker
    flat = S.release_flat
    try:
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        rows = json.loads(qb._call("/api/v2/torrents/info?filter=downloading", timeout=6)
                          or b"[]")
    except Exception:
        rows = []
    with pd_tracker.LOCK:
        taken = {flat(t.get("name")): t for t in (pd_tracker.STATE.get("log") or [])}
    # one torrent carries several films asked for: its line is the one coming in, not the
    # last one queued behind it - that one read 0%, 0 Mbit/s and stood twice (2026-11-06)
    packs = {}
    for d in pd_torrents.load()["downloads"]:
        h = str(d.get("hash") or "").lower()
        if d.get("state") == "downloading" or (d.get("state") == "queued" and h not in packs):
            packs[h] = d
    out = []
    for t in rows:
        # qBittorrent counts a stopped or paused one as downloading; it is not, and a
        # check reads files already here
        state = str(t.get("state") or "")
        if state in ("stoppedDL", "pausedDL", "error", "missingFiles") or state.startswith("checking"):
            continue
        # seeding work, not the library's: a cross-seed fetching its last pieces, or a
        # race, stood first on Recently added as a film on its way with no poster
        if str(t.get("category") or "") in S.NOT_LIBRARY:
            continue
        name = str(t.get("name") or "")
        mine = taken.get(flat(name)) or {}
        pack = packs.get(str(t.get("hash") or "").lower()) or {}
        # a pack's torrent is named for the pack: the film asked out of it is the news
        what = pack.get("title") or name
        if pack.get("episode"):
            what += " S%02dE%02d" % (int(pack.get("season") or 0), int(pack["episode"]))
        # a film out of a pack is the film: its own size, progress and time left, not the
        # whole torrent's - one film read 212 GB, 89% and fifteen hours
        whole_eta = int(t.get("eta") or 0) if int(t.get("eta") or 0) < 8640000 else 0
        out.append({
            "name": what,
            "progress": float(pack.get("progress") or 0) if pack else float(t.get("progress") or 0),
            "mbit": (float(pack.get("mbit") or 0) if pack
                     else round(float(t.get("dlspeed") or 0) * 8 / 1e6, 1)),
            "eta": int(pack.get("eta") or 0) if pack else whole_eta,
            "size": int(pack.get("size") or 0) if pack else int(t.get("size") or 0),
            "who": pack.get("who") or mine.get("who") or "",
            "reason": pack.get("reason") or mine.get("reason") or "",
            "auto": bool(pack.get("auto") or mine.get("auto")),
            "from": "pack" if pack else ("tracker" if mine else ""),
            # when it was put into the client, for ordering the newest asked first
            "added": int(t.get("added_on") or 0),
            "hash": str(t.get("hash") or "").lower(),
            "state": str(t.get("state") or "")})
    # and a release being unpacked: whole, and not a film yet
    for u in list(pd_tracker.UNPACKING.values()):
        mine = taken.get(flat(u.get("name"))) or {}
        out.append({"name": str(u.get("name") or ""), "progress": float(u.get("progress") or 0),
                    "mbit": 0.0, "eta": 0, "size": int(mine.get("size") or 0),
                    "who": mine.get("who") or "", "reason": mine.get("reason") or "",
                    "auto": bool(mine.get("auto")), "from": "tracker",
                    "state": "unpacking", "why": str(u.get("why") or "")})
    # and the ones waiting their turn, in the order they will start
    for d in pd_torrents.load()["downloads"]:
        if d.get("state") != "queued":
            continue
        what = d.get("title") or ""
        if d.get("episode"):
            what += " S%02dE%02d" % (int(d.get("season") or 0), int(d["episode"]))
        out.append({"name": what, "progress": 0.0, "mbit": 0.0, "eta": 0,
                    "size": int(d.get("size") or 0), "who": d.get("who") or "",
                    "reason": d.get("reason") or "", "auto": bool(d.get("auto")),
                    "from": "pack", "state": "queued",
                    "place": pd_torrents._place(d.get("key"))})
    # one gone from the list since the last look has finished: landing is read again
    # now, or for up to ten seconds the film was on neither list and the screens
    # showed Download again
    names = {d["name"] for d in out}
    if S.COMING.get("names") is not None and S.COMING["names"] - names:
        S.LANDING["at"] = 0.0
    S.COMING.update(at=time.time(), rows=out, names=names)
    return out


def tracker_takes():
    """Releases taken straight from the tracker, by hand or by themselves, as rows of
    the download log: the log held only what came out of the packs, so an episode
    fetched when it landed was nowhere in it. Progress from qBittorrent, by name."""
    import pd_tracker, pd_torrents
    with pd_tracker.LOCK:
        taken = list(pd_tracker.STATE.get("log") or [])
    if not taken:
        return []
    flat = S.release_flat
    there = {}
    try:
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        for t in json.loads(qb._call("/api/v2/torrents/info", timeout=20) or b"[]"):
            there[flat(t.get("name"))] = t
    except Exception:
        there = {}
    out = []
    for t in taken:
        name = str(t.get("name") or "")
        got = there.get(flat(name))
        progress = float((got or {}).get("progress") or 0)
        out.append({
            "when": int(t.get("at") or 0), "who": t.get("who") or "",
            "title": name, "name": name, "from": "tracker",
            "size": int((got or {}).get("size") or t.get("size") or 0),
            "state": ("done" if progress >= 1 else "downloading") if got else "asked for",
            "progress": progress,
            "done": int((got or {}).get("completion_on") or 0) if progress >= 1 else 0,
            "auto": bool(t.get("auto")), "reason": t.get("reason") or "",
            "via": t.get("via") or ""})
    return out


def catalogue_lists(show, season, number):
    """Whether the catalogue has that episode in its season, aired or not yet."""
    try:
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT tmdb_id FROM item WHERE type='show' AND "
                              "lower(title)=lower(?) AND tmdb_id", (str(show),)).fetchone()
        finally:
            con.close()
        if not row:
            return False
        said = S.local()._catalogue("/tv/%d" % int(row["tmdb_id"]), S.local()._airs,
                                  row["tmdb_id"])
        for one in said.get("seasons") or []:
            if int(one.get("season_number") or -1) == int(season):
                return 0 < int(number) <= int(one.get("episode_count") or 0)
        return False
    except Exception:
        return False


def last_groups():
    """The group of the newest episode file here, by programme: the next episode is
    taken from the same group when it has one that meets the wishes, so a series
    stays one release from episode to episode."""
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    out = {}
    try:
        con = S.local().lib.db()
        try:
            rows = con.execute(
                """SELECT i.title, e.season, e.number, f.path FROM file f
                   JOIN episode e ON e.id = f.episode_id JOIN item i ON i.id = e.item_id
                   ORDER BY e.season, e.number""").fetchall()
        finally:
            con.close()
    except Exception:
        return out
    for r in rows:
        group = S.release_group(r["path"])
        if group:
            out[flat(r["title"])] = group
    return out


def shows_waiting(stored=None):
    """Every new episode of a programme in the library, and what is on offer for it.

    One entry per episode: the releases the feeds have seen, the best of them, and
    whether that best meets the house's wishes. The watcher acts on this; the page
    shows it, so an episode can be taken by hand the moment the first release appears
    rather than waiting for the good one.
    """
    import pd_tracker
    stored = S.read_settings() if stored is None else stored
    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
    follows = [dict(one, followed=True) for one in S.follow_list(stored)]
    # and every other programme in the library: its new episodes come in as links too,
    # followed or not. Following is what lets the newest one be downloaded.
    chosen = {flat(one["show"]) for one in follows}
    try:
        con = S.local().lib.db()
        try:
            names = [str(r["title"] or "") for r in
                     con.execute("SELECT title FROM item WHERE type='show'")]
        finally:
            con.close()
    except Exception:
        names = []
    for name in names:
        if name.strip() and flat(name) not in chosen:
            chosen.add(flat(name))
            follows.append({"show": name, "mode": "all", "from": 0, "to": 0,
                            "followed": False})
    if not follows:
        return []
    wanted = {flat(one["show"]): one for one in follows}
    try:
        import pd_torrents
        owned = (pd_torrents._owned() or {}).get("episodes") or set()
    except Exception:
        return []
    if not owned:
        # The library could not be read, or holds no episodes at all. What is already
        # on the disk is the only thing that stops this fetching everything.
        return []
    # The newest episode here of each programme, from a file or a pack. Keeping up
    # is what comes after it: a release the tracker's list first saw this week is
    # not a new episode when it is season one of something that ended years ago -
    # and every pack search pulled old releases in, each of them looking new.
    newest = {}
    for show, season, number in owned:
        key = flat(show)
        if (season, number) > newest.get(key, (0, 0)):
            newest[key] = (season, number)
    try:
        for name, holds in (pd_torrents._episodes_by_show() or {}).items():
            key = flat(name)
            for film, _pack in holds:
                at = (int(film.get("season") or 0), int(film.get("episode") or 0))
                if at > newest.get(key, (0, 0)):
                    newest[key] = at
    except Exception:
        pass
    rows = pd_tracker.read()
    now = time.time()
    # Episodes taken straight from the tracker by hand - the season page, a version
    # list - are fetched already, though not on the disk until they finish: without
    # this the job took a second release of one somebody was already downloading.
    fetched = set()
    with pd_tracker.LOCK:
        taken_log = list(pd_tracker.STATE.get("log") or [])
    for t in taken_log:
        if now - float(t.get("at") or 0) > S.NEW_FOR:
            continue
        try:
            said = pd_torrents.episode_of(str(t.get("name") or "") + ".mkv")
        except Exception:
            said = None
        if said and said[2] and said[3]:
            fetched.add((flat(said[0]), int(said[2]), int(said[3])))
    seen = {}
    # a copy: the tracker's reader adds rows while this walks them
    for row in list(rows.values()):
        told = None
        try:
            told = pd_torrents.episode_of(str(row.get("name") or "") + ".mkv")
        except Exception:
            told = None
        if not told:
            continue
        show, _year, season, number = told
        one = wanted.get(flat(show))
        if not one or not season or not S.followed_season(one, season):
            continue
        which = str(one["show"])
        if (which.strip().lower(), int(season), int(number)) in owned:
            continue                      # already on the disk
        if (flat(which), int(season), int(number)) in fetched:
            continue                      # already coming in, taken by hand
        last = newest.get(flat(which))
        if not last or (int(season), int(number)) <= last:
            continue                      # not after anything here: not keeping up
        # and not past what has aired: a group that numbers a streaming run as season
        # thirteen is offering episodes this house has under other numbers
        # An early release, posted before its air date, passes when the catalogue lists
        # that episode at all.
        aired = S.aired_up_to(which)
        if aired and (int(season), int(number)) > aired and                 not S.catalogue_lists(which, int(season), int(number)):
            continue
        seen.setdefault((which, int(season), int(number)), []).append(row)
    with pd_tracker.LOCK:
        got = dict(pd_tracker.STATE.get("grabbed") or {})
    stored = S.for_series(stored)
    wishes = S.Handler.ordered_wishes(stored)
    lengths = S.episode_runtimes()
    groups = S.last_groups()

    def granted(one, seconds=0.0):
        low = " " + str(one.get("name") or "").lower() + " "
        return tuple(S.rate_fits(one.get("name"), int(one.get("size") or 0), seconds, stored)
                     if kind == "rate" else S.release_is(low, kind, said)
                     for kind, said in wishes if kind not in ("quality", "seeds"))

    out = []
    for (show, season, number), these in sorted(seen.items()):
        long = lengths.get(flat(show), 0.0)
        mark = "%s|s%02de%02d" % (flat(show), season, number)
        first = min(float(r.get("at") or now) for r in these)
        if now - first > S.NEW_FOR:
            continue                      # not new: this is keeping up, not filling in
        small = [r for r in these if (r.get("size") or 0) <= S.MOST_GB_EACH * 1073741824]
        fits = [r for r in small if all(granted(r, long))]
        # the group the last episode came from first, when one of its releases meets
        # the wishes; then as Choosing ranks them
        same = groups.get(flat(show), "")
        best = max(fits or small or these,
                   key=lambda r: (bool(fits) and bool(same)
                                  and S.release_group(r.get("name")) == same,
                                  S.rank_key(r, wishes, stored, long),
                                  int(r.get("seeds") or 0)))
        if fits:
            best = S.close_call(best, fits, stored, long)
        out.append({
            "show": show, "season": season, "episode": number,
            "followed": bool((wanted.get(flat(show)) or {}).get("followed")),
            "grab": bool((wanted.get(flat(show)) or {}).get("grab")),
            "mark": mark, "releases": len(these),
            "first": first, "waited": round((now - first) / 3600.0, 1),
            "good": bool(fits), "taken": bool(got.get(mark)),
            "id": str(best.get("id") or ""), "name": best.get("name"),
            "size": int(best.get("size") or 0), "seeds": int(best.get("seeds") or 0),
            # what the watcher would do about it, now
            # and never by itself a release outside Choosing, however long: one of
            # those can still be taken by hand from the list. One that first
            # appeared in the night waits the night out as well: nobody is watching,
            # and the good releases have till morning to arrive.
            "waiting": not got.get(mark) and (now < S.ready_at(first) or not fits),
        })
    return out


def grab_episode(mark, stored=None):
    """Fetch the best release of one waiting episode now, whatever the wishes say."""
    import pd_tracker
    one = next((w for w in S.shows_waiting(stored) if w["mark"] == str(mark)), None)
    if not one:
        return {"error": "nothing waiting under that name"}
    if one.get("taken"):
        return {"error": "that one has been fetched already"}
    said = pd_tracker.take(one["id"], who="Download newest", auto=True,
                           reason="%s S%02dE%02d: taken by hand from the new episodes"
                                  % (one["show"], one["season"], one["episode"]))
    if not said.get("taken"):
        return said
    with pd_tracker.LOCK:
        pd_tracker.STATE.setdefault("grabbed", {})[one["mark"]] = {
            "name": one["name"], "at": time.time(), "show": one["show"],
            "season": one["season"], "episode": one["episode"], "byHand": True}
    pd_tracker.write()
    return {"taken": True, "name": one["name"]}


def keep_up_with_shows():
    """Put the new episodes of the programmes this house follows within reach.

    Nothing is downloaded here. The best release of an episode is added as a pack -
    a link - so the episode appears with its poster and is fetched only when somebody
    plays it. The choosing is the same: the best that meets the wishes, once the
    episode has been out long enough for the good releases to have landed, and the
    best there is after a day rather than none at all.
    """
    import pd_tracker, pd_torrents
    stored = S.read_settings() or {}
    # the newest episode of each programme, which is the one a house may want on the
    # disk rather than a press away
    ready = [w for w in S.shows_waiting(stored)
             if not w["taken"] and not w["waiting"] and w["id"]]
    newest = {}
    for one in ready:
        was = newest.get(one["show"])
        if not was or (one["season"], one["episode"]) > (was["season"], was["episode"]):
            newest[one["show"]] = one
    fetch_latest = bool(stored.get("grabLatest"))
    added = 0
    for one in ready:
        if added >= S.MOST_AT_ONCE:
            break
        # Downloaded only where the house has asked for that, and only the newest
        # episode of a programme. Everything else is a link: the episode appears and
        # the file comes when somebody plays it.
        if ((fetch_latest or one.get("grab")) and one.get("followed")
                and newest.get(one["show"]) is one):
            # a pack chosen for the programme that carries it: from there, not the
            # tracker - it is the copy the house picked for that season
            packed = [c for c in pd_torrents.episodes_for(one["show"], one["season"],
                                                          one["episode"])
                      if c.get("chosen")]
            if packed:
                said = pd_torrents.request(
                    packed[0]["key"], "me", "New episodes", auto=True,
                    reason="%s S%02dE%02d: newest episode of a followed series, from "
                           "its chosen pack" % (one["show"], one["season"], one["episode"]))
                if said.get("ok"):
                    with pd_tracker.LOCK:
                        pd_tracker.STATE.setdefault("grabbed", {})[one["mark"]] = {
                            "name": packed[0]["name"], "at": time.time(),
                            "show": one["show"], "season": one["season"],
                            "episode": one["episode"]}
                    pd_tracker.write()
                    added += 1
                    continue
            said = pd_tracker.take(
                one["id"], who="New episodes", auto=True,
                reason="%s S%02dE%02d: newest episode of a followed series, %s"
                       % (one["show"], one["season"], one["episode"],
                          "set to Download newest" if one.get("grab")
                          else "every followed series downloads its newest"))
            if not said.get("taken"):
                continue
            with pd_tracker.LOCK:
                pd_tracker.STATE.setdefault("grabbed", {})[one["mark"]] = {
                    "name": one["name"], "at": time.time(), "show": one["show"],
                    "season": one["season"], "episode": one["episode"]}
            pd_tracker.write()
            added += 1
            continue
        raw = pd_tracker.torrent_of(one["id"])
        if not raw:
            continue
        said = pd_torrents.add_pack(raw=raw)
        if not said.get("ok", True) and said.get("why"):
            continue
        with pd_tracker.LOCK:
            pd_tracker.STATE.setdefault("grabbed", {})[one["mark"]] = {
                "name": one["name"], "at": time.time(), "show": one["show"],
                "season": one["season"], "episode": one["episode"], "asLink": True}
        pd_tracker.write()
        added += 1
    return added
