"""The shuffle: rounds, draws, what is ready next, and the marks it keeps per viewer.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import pd_h
import pd_log

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class ShuffleParts(_Base):
    @staticmethod
    def round_moved(mine):
        """Say when this shuffle last moved, so two machines can tell whose is newer.

        The round is one thing wherever it is played. Merging two of them makes a
        queue nobody drew, so the newer one is taken whole and the older dropped.
        """
        mine["casualStamp"] = int(time.time())

    @staticmethod
    def round_stamp(one):
        """When a round last moved; nought for none."""
        return int(one.get("casualStamp") or 0) if isinstance(one, dict) else 0

    #: How far ahead the hat is drawn. Ten is what makes "what is next" answerable,
    #: and answerable is what lets a subtitle be fetched for it and a copy kept of it.
    SHUFFLE_DEEP = 10

    @staticmethod
    def round_id(cid):
        """The one name a collection's round is kept under: "coll:<id>" is its id. A
        shuffle started from Start reported its places under the first and was drawn
        under the second - two rounds, and the wrong episode resumed."""
        cid = str(cid or "")
        return cid[5:] if cid.startswith("coll:") else cid

    def shuffle_round(self, mine, cid, make=True):
        """This person's round for one shelf: the hat, the history and the places."""
        cid = S.Handler.round_id(cid)
        S.Handler.fold_round(mine, cid)
        rounds = mine.get("shuffles")
        if not isinstance(rounds, dict):
            rounds = {}
            if make:
                mine["shuffles"] = rounds
        one = rounds.get(str(cid))
        if not isinstance(one, dict):
            one = {"order": [], "pos": -1, "queue": [], "played": [], "at": {}, "run": 1,
                   "casualStamp": 0}
            if make:
                rounds[str(cid)] = one
        return one

    def shuffle_resume(self, cid):
        """Where the title this person's round on one shelf is on was left, in seconds.

        Only that title. Other titles left part-way on the shelf start from their
        beginning when they are drawn. Nought when it has not been started or has been
        finished - so a button can say Resume when there is something to resume.
        """
        stored = S.read_settings() or {}
        who = self.viewer()
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        one = ((mine.get("shuffles") or {}).get(S.Handler.round_id(cid)) or {})
        key = self.shuffle_current(one)
        if not key:
            return 0
        value = (one.get("at") or {}).get(key)
        at = int(value.get("at") or 0) if isinstance(value, dict) else int(value or 0)
        return at if at > 30 else 0

    @staticmethod
    def shuffle_current(one):
        """The title a round is on, unless it has been finished."""
        import pd_shuffle
        if "pos" not in one:          # a round written before the order had a place
            played = [str(k) for k in (one.get("played") or [])]
            on = played[-1] if played else ""
            return "" if not on or on == str(one.get("done") or "") else on
        return pd_shuffle.current(one)

    @staticmethod
    def series_shuffle_names(rounds):
        """The rounds of whole series - "show:<id>" - each named as its series."""
        ids = [str(k)[5:] for k in (rounds or {}) if str(k).startswith("show:")]
        if not ids:
            return {}
        con = S.local().lib.db()
        try:
            return {"show:" + str(r["id"]): str(r["title"] or "") for r in con.execute(
                "SELECT id, title FROM item WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
        finally:
            con.close()

    def own_series_shelf(self, show):
        """The viewer's collection of one series, made now if they have none: a hand-made
        shelf holding the series whole, named after it. A round kept for the series as
        "show:<id>" moves onto it. Its id, or "" for a series this library has not."""
        show = str(show or "")
        con = S.local().lib.db()
        try:
            for c in self.collections():
                if S.shelf_series(con, c) == show:
                    return str(c.get("id"))
            row = con.execute("SELECT title FROM item WHERE id=? AND type='show'",
                              (show,)).fetchone()
        finally:
            con.close()
        if not row:
            return ""
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        shelves = [c for c in (mine.get("collections") or []) if isinstance(c, dict)]
        made = "%x" % (int(time.time() * 1000) % 0xFFFFFFF)
        shelves.append({"id": made, "name": str(row["title"] or "")[:60], "mode": "manual",
                        "series": [show], "pinned": [], "hidden": [], "rule": {},
                        "cover": ""})
        mine["collections"] = shelves[:60]
        rounds = mine.get("shuffles")
        if isinstance(rounds, dict):
            for old in ("show:" + show, "coll:show:" + show):
                if isinstance(rounds.get(old), dict) and not rounds.get(made):
                    rounds[made] = rounds.pop(old)
        S.write_settings(stored, merge=False)
        return made

    def series_shuffle_id(self, show):
        """The shuffle a series page's button plays: the viewer's own collection of that
        series, or - with none - "show:<id>", every episode of it."""
        show = str(show or "")
        con = S.local().lib.db()
        try:
            for c in self.collections():
                if S.shelf_series(con, c) == show:
                    return str(c.get("id"))
        finally:
            con.close()
        return "show:" + show

    def shuffle_shelf(self, cid):
        """The keys one shelf holds, in library order, or nothing if there is no shelf.

        The name a shelf goes by on one page is "coll:f592640" and on another it is
        "f592640" - the first is how the library API numbers everything it hands out,
        so that a shelf and a film cannot collide. Both are the same shelf, and being
        strict about which was sent is how a television came to say there was nothing
        to play on a shelf full of films.
        """
        cid = str(cid or "")
        if cid.startswith("coll:"):
            cid = cid[5:]
        shelf = next((c for c in self.collections()
                      if str(c.get("id")) == str(cid)), None)
        # a series with no collection of its own: every episode of it, as a shelf that
        # holds the series whole
        if shelf is None and (cid == "music" or cid.startswith("music:")):
            # every music video, or one playlist of them in its order
            import pd_localapi, pd_music
            one = pd_music.find(cid[6:]) if cid.startswith("music:") else None
            if cid != "music" and one is None:
                return None, []
            con = S.local().lib.db()
            try:
                every = pd_localapi.music_order(con, self.viewer())
            finally:
                con.close()
            here = set(every)
            keys = [k for k in one["keys"] if k in here] if one else every
            return {"id": cid, "name": one["name"] if one else "Music videos", "mode": "manual",
                    "series": [], "pinned": [], "hidden": [], "rule": {}}, keys
        if shelf is None and re.fullmatch(r"show:[0-9a-f]{12}", cid):
            shelf = {"id": cid, "name": "", "mode": "manual", "series": [cid[5:]],
                     "pinned": [], "hidden": [], "rule": {}}
        if not shelf:
            return None, []
        con = S.local().lib.db()
        try:
            # What a pack can give belongs in the hat too. It cannot be played the
            # second it is drawn - it has to be fetched first - so the draw still
            # prefers anything already here, and asks for the rest in the background.
            #
            # And a programme is drawn an episode at a time. A shelf matches titles,
            # so a rule that catches a series put the series on the shelf and the hat
            # had one thing in it that could not be played at all. Every episode of it
            # goes in instead - the ones here, and the ones a pack has.
            # and a collection with a rule of its own for the shuffle draws only
            # what that rule leaves: the whole series on the shelf, a few seasons in
            # the hat
            return shelf, self.shuffle_part(con, shelf, self.shelf_pool(con, shelf))
        finally:
            con.close()

    @staticmethod
    def shuffle_part(con, shelf, keys):
        """Of a collection's titles, the ones its shuffle draws: an episode only where
        its season and number answer the collection's shuffle span ("s3-s6", and the
        part of it to leave out); a film always. Every key when it has no such span."""
        import pd_seasons
        import pd_torrents
        spans = pd_seasons.parse(str(shelf.get("shuffleSeason") or ""))
        nope = pd_seasons.parse(str(shelf.get("shuffleSeasonWithout") or ""))
        if not spans and not nope:
            return keys
        def inside(sn, n):
            if spans and not pd_seasons.holds(spans, sn, n):
                return False
            return not (nope and pd_seasons.holds(nope, sn, n))
        out = []
        for key in keys:
            key = str(key)
            place = None
            if S.is_episode(key):
                row = con.execute("SELECT season, number FROM episode WHERE id=?",
                                  (key,)).fetchone()
                if row:
                    place = (int(row["season"] or 0), int(row["number"] or 0))
            # an episode only a pack has: its place from the pack - by its title id now,
            # which the library's episode table may not hold yet
            if place is None and pd_torrents.only_in_pack(key):
                film = pd_torrents.by_key(key)[1] or {}
                if film.get("kind") == "episode":
                    place = (int(film.get("season") or 0), int(film.get("episode") or 0))
            if place is None or inside(*place):
                out.append(key)
        return out

    @staticmethod
    def shuffle_marks(con, shelf, rows, keys):
        """A collection's cards marked for what its shuffle span leaves out; a season
        held whole that no key links yet is read through its own episodes."""
        import pd_localapi
        if not (shelf.get("shuffleSeason") or shelf.get("shuffleSeasonWithout")):
            return rows
        keys = [str(k) for k in keys]
        extra = {}
        for one in rows:
            m = S.Handler.SEASON_KEY.match(str(one.get("ratingKey") or ""))
            if m and not one.get("holds"):
                extra[id(one)] = [str(r["id"]) for r in con.execute(
                    "SELECT id FROM episode WHERE item_id=? AND season=?",
                    (m.group(1), int(m.group(2))))]
                keys += extra[id(one)]
        off = set(keys) - set(S.Handler.shuffle_part(con, shelf, keys))
        return pd_localapi.mark_unshuffled(rows, off, extra)

    def shelf_pool(self, con, shelf):
        """Everything one collection holds, an episode at a time for a programme."""
        pool = []
        for key in self.collection_keys(con, shelf):
            key = str(key)
            if S.is_episode(key):
                pool.append(key)
                continue
            import pd_torrents
            if pd_torrents.as_pack_show(key).startswith("os"):
                pool += self.offered_episode_keys(key)
                continue
            got = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
            if got:
                # a season stands for its episodes; its own key plays nothing - and
                # where the library has no rows for that season at all, spread hands
                # the season key straight back and the hat filled with keys nothing
                # could play. What a pack holds of that season goes in beside it: a
                # shelf with a season rule reaches here rather than the branch below,
                # so this is where a whole series is either in the hat or is not.
                these = [k for k in self.spread(key) if k != key]
                said = con.execute("SELECT title FROM item WHERE id=?",
                                   (got.group(1),)).fetchone()
                if said and said["title"]:
                    these += self.offered_season_keys(said["title"],
                                                      int(got.group(2)))
                pool += these
                continue
            rows = con.execute(
                """SELECT e.id FROM episode e JOIN file f ON f.episode_id = e.id
                   WHERE e.item_id = ? GROUP BY e.id
                   ORDER BY e.season, e.number""", (key,)).fetchall()
            pool += [str(r["id"]) for r in rows] if rows else [key]
            # and what a pack can give of the same programme. The offered path above
            # only fires for a programme that exists nowhere but in a pack; a series
            # the library already holds eight episodes of matched the library item and
            # the other three hundred were never in the hat - so a shelf that lists a
            # whole series shuffled the fraction of it that happened to be on disk.
            # The shelf is a rule over what is listed, and this is what is listed.
            said = con.execute("SELECT title FROM item WHERE id=?", (key,)).fetchone()
            if said and said["title"]:
                pool += self.offered_episode_keys(
                    __import__("pd_torrents").show_key(said["title"]))
        seen, out = set(), []
        for key in pool:
            if key not in seen:
                seen.add(key)
                out.append(key)
        # a pack's key for an episode downloaded since is that episode: in the hat once
        out = S.local()._arrived_as(con, out)
        # A library row whose file has gone is not in the hat as itself: drawn, it had
        # nothing to play and was never fetched, since only a pack's own key is asked
        # of the pack. Where a pack carries that episode the pack's key stands in for
        # it, so drawing it fetches it; where none does it is left out.
        held = self.held_keys(con, out)
        stand = self.packed_for(con, [k for k in out if k not in held])
        return list(dict.fromkeys(stand.get(k, k) for k in out if k in held or k in stand))

    def packed_for(self, con, keys):
        """Library episode keys without a file -> the key of the pack's entry for the
        same episode, for those a pack carries. Nothing on a copy: it fetches nothing
        itself, and most of its rows have no file."""
        out = {}
        if not keys or self.follows_a_main():
            return out
        try:
            import pd_torrents
        except Exception:
            return out
        seasons = {}
        for key in keys:
            if not S.is_episode(key):
                continue
            row = con.execute(
                "SELECT i.title AS title, e.season AS season, e.number AS number "
                "FROM episode e JOIN item i ON i.id = e.item_id WHERE e.id=?", (key,)).fetchone()
            if not row or not row["title"]:
                continue
            at = (row["title"], int(row["season"] or 0))
            if at not in seasons:
                try:
                    seasons[at] = {int(o.get("index") or 0): str(o.get("ratingKey") or "")
                                   for o in pd_torrents.offered_episodes(
                                       pd_torrents.show_key(at[0]), at[1])}
                except Exception:
                    seasons[at] = {}
            got = seasons[at].get(int(row["number"] or 0))
            if got:
                out[str(key)] = got
        return out

    @staticmethod
    def held_keys(con, keys):
        """Of these keys, the ones that can be in a hat: a pack's own (o...), and a
        library episode or title with a file. Any other shape of key is kept."""
        keys = [str(k) for k in keys]
        eps = [k for k in keys if S.is_episode(k)]
        titles = [k for k in keys if re.match(r"^[0-9a-f]{12}$", k)]
        filed = set()
        for i in range(0, len(eps), 400):
            part = eps[i:i + 400]
            filed |= {str(r[0]) for r in con.execute(
                "SELECT DISTINCT episode_id FROM file WHERE episode_id IN (%s)"
                % ",".join("?" * len(part)), part)}
        for i in range(0, len(titles), 400):
            part = titles[i:i + 400]
            filed |= {str(r[0]) for r in con.execute(
                "SELECT DISTINCT item_id FROM file WHERE episode_id IS NULL AND item_id IN (%s)"
                % ",".join("?" * len(part)), part)}
        library = set(eps) | set(titles)
        return {k for k in keys if k not in library or k in filed}

    @staticmethod
    def offered_season_keys(title, season):
        """Every episode one pack can give of one season of a programme."""
        try:
            import pd_torrents
            return [str(one.get("ratingKey") or "")
                    for one in pd_torrents.offered_episodes(
                        pd_torrents.show_key(title), int(season))
                    if one.get("ratingKey")]
        except Exception:
            return []

    @staticmethod
    def offered_episode_keys(show_key):
        """Every episode one pack can give of one offered programme."""
        try:
            import pd_torrents
            out = []
            for season in pd_torrents.offered_seasons(show_key):
                for one in pd_torrents.offered_episodes(show_key,
                                                        int(season.get("index") or 0)):
                    key = str(one.get("ratingKey") or "")
                    if key:
                        out.append(key)
            return out
        except Exception:
            return []

    def shuffle_draw(self, cid, peek=False, resume=False, back=False, left_from="", at=""):
        """Next, Back, a look at what is next, or carrying on, on one shelf's shuffle.

        A shuffle is one order, drawn when it starts, and a place in it. Next and Back
        move the place and nothing else: nothing goes back in the hat, nothing is drawn
        again, and stepping forward after stepping back shows the same titles in the
        same order. Titles added to the shelf later are put in at random places ahead
        of the next one. Past the end of the order a new round is drawn.
        """
        import random
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        shelf, pool = self.shuffle_shelf(cid)
        if shelf is None:
            return {"error": "there is no such shelf"}
        if not pool:
            return {"error": "that shelf is empty"}
        # One key a title. A pack's episode that has come in is the library's episode,
        # and the shelf went on listing it as the pack's: the round held it under one
        # key and the shelf under the other, so it was put into the order again at a
        # random place on every press, and the titles between were stepped over as
        # not on the shelf - Back and Next landed anywhere.
        pool = list(dict.fromkeys(self.as_held(k) for k in pool))
        self.fold_round(mine, cid)
        one = self.shuffle_round(mine, cid)
        self.shuffle_settle(one, pool)
        # the music videos and their playlists: in order, from the one pressed, round and round
        music = str(cid) == "music" or str(cid).startswith("music:")
        if music and at and str(at) in pool:
            i = pool.index(str(at))
            one.update({"order": pool[i:] + pool[:i], "pos": -1, "at": {}, "done": ""})
            resume = False
        order, pos = one["order"], one["pos"]
        inside = set(str(k) for k in pool)
        # On the main server everything on the shelf is drawn, a pack's episodes too:
        # they are fetched while the draw answers (ask_the_pack). On a copy only what it
        # holds a file for, since nothing can be fetched from a main server that is off.
        copy = self.follows_a_main()
        usable = lambda k: k in inside and (not copy or self.can_be_played(k))
        import pd_shuffle
        # the rules of a round are the shuffle handler's: drawn in advance, its own silent
        # progress, Next marking the title done for the round (owner, 2026-11-08)
        if music:
            # the music videos loop their own order, from where the row was last started,
            # rather than draw a new one
            one["next"] = [k for k in order if k in inside] + [str(k) for k in pool if str(k) not in set(order)]
        if peek:
            # a look moves nothing; at the end of the order the next round is drawn once and
            # kept, so the move plays what the look showed
            had_next = list(one.get("next") or [])
            key = pd_shuffle.peek(one, usable, pool, copy=copy)
            if not key:
                return {"error": "nothing on this shelf can be played here"}
            if list(one.get("next") or []) != had_next:
                S.write_settings(stored)
            self.shuffle_derive(one, inside)
            return self.shuffle_said(one, key, pool, self.shuffle_left(one, pool))
        if back:
            key = pd_shuffle.back(one, usable)
            if key is None and music:
                # the music videos wrap: before the first is the last
                to = pd_shuffle.behind(one, usable, len(order))
                if to is not None:
                    one["pos"], one["done"], one["at"] = to, "", {}
                    key = order[to]
            if key is None:
                return {"error": "nothing before this one"}
            return self.shuffle_moved(stored, one, pool)
        if resume:
            key, at, over = pd_shuffle.carry_on(one, usable, pool, seen=self.seen_last, copy=copy)
            if over:
                self.round_log(cid, one, "end of the order, pool %d" % len(pool))
            if not key:
                return {"error": "nothing on this shelf can be played here"}
            S.Handler.round_moved(one)
            self.shuffle_derive(one, inside)
            S.write_settings(stored)
            self.ask_the_pack([key])
            return self.shuffle_said(one, key, pool, self.shuffle_left(one, pool), at)
        key, over = pd_shuffle.forward(one, usable, pool, left_from, copy=copy)
        if over:
            self.round_log(cid, one, "end of the order, pool %d" % len(pool))
        if not key:
            return {"error": "nothing on this shelf can be played here" if not copy
                    else "nothing on this shelf is here yet"}
        # What has to download first is not what plays now: the next in the drawn order
        # that is here plays, and the one coming in stays next in line while it arrives
        if not copy and 0 <= int(one.get("pos", -1)) < len(one["order"]):
            self.ready_first(one["order"], int(one["pos"]), usable, self.ready_now)
        said = self.shuffle_moved(stored, one, pool)
        self.fetch_ahead(one["queue"][:3])
        # what a pack has to give, asked for before it is wanted: the one now on and the
        # ten after it
        self.ask_the_pack([said.get("key") or key] + list(one["queue"]))
        return said

    @staticmethod
    def ready_now(key):
        """Whether a title plays the moment it is drawn: a pack's episode only once it
        has come in. Anything else in a hat has a file."""
        key = str(key)
        import pd_torrents
        if not pd_torrents.only_in_pack(key):
            return True
        try:
            import pd_torrents
            return bool(pd_torrents.arrived(key))
        except Exception:
            return True

    @staticmethod
    def ready_first(order, to, usable, ready):
        """The order put so the title at `to` is one that plays now: where it is not, the
        first after it that is moves up to `to`, the rest keeping their order behind it."""
        if ready(order[to]):
            return
        here = next((j for j in range(to + 1, len(order))
                     if usable(order[j]) and ready(order[j])), None)
        if here is not None:
            order.insert(to, order.pop(here))

    def shuffle_settle(self, one, pool):
        """A round's order made whole: pack titles under their library keys, an order
        for a round that has none, and titles new to the shelf put in ahead."""
        import random
        for field in ("played", "queue", "order"):
            if one.get(field):
                one[field] = [self.as_held(k) for k in one[field]]
        if one.get("done"):
            one["done"] = self.as_held(one["done"])
        if isinstance(one.get("at"), dict):
            one["at"] = {self.as_held(k): v for k, v in one["at"].items()}
        # and the other way: an episode in the order whose file has gone, under the key
        # of the pack that carries it, at the same place - so a round standing on it is
        # standing on something that is fetched
        con = S.local().lib.db()
        try:
            mine = [str(k) for k in (one.get("order") or [])]
            # read once: asked per key it was a pass over the library for each of a
            # 661-episode order, and a draw took minutes
            held = self.held_keys(con, mine)
            stand = self.packed_for(con, [k for k in mine if k not in held])
        finally:
            con.close()
        if stand:
            for field in ("played", "queue", "order"):
                if one.get(field):
                    one[field] = [stand.get(str(k), k) for k in one[field]]
            if one.get("done"):
                one["done"] = stand.get(str(one["done"]), one["done"])
            if isinstance(one.get("at"), dict):
                one["at"] = {stand.get(str(k), k): v for k, v in one["at"].items()}
        import pd_shuffle
        if "pos" not in one:
            # A round from before the order had a place: what it played, in the order
            # it played it, then the rest of its order, and the place on the title it
            # is on.
            order = [str(k) for k in (one.get("order") or [])]
            played = [str(k) for k in (one.get("played") or [])]
            played = [k for i, k in enumerate(played) if k not in played[:i]]
            order = played + [k for k in order if k not in set(played)]
            pos = len(played) - 1 - int(one.get("back") or 0)
            one["order"], one["pos"] = order, max(-1, min(pos, len(played) - 1))
        pd_shuffle.settle(one, [str(k) for k in pool])
        one.pop("back", None)
        self.shuffle_derive(one, set(str(k) for k in pool))

    def shuffle_derive(self, one, inside=None):
        """What older readers of a round look at: what it has played up to the title it is
        on, and the titles after it still on the shelf."""
        import pd_shuffle
        pd_shuffle.derived(one, inside, self.SHUFFLE_DEEP)

    def shuffle_left(self, one, pool):
        """How many titles of the shelf the round has still to come to."""
        import pd_shuffle
        return pd_shuffle.left(one, pool)

    def shuffle_moved(self, stored, one, pool):
        """The round kept where the handler moved it, and answered with the title it is on."""
        import pd_shuffle
        order = one.get("order") or []
        pos = int(one.get("pos") if one.get("pos") is not None else -1)
        key = order[pos] if 0 <= pos < len(order) else ""
        self.shuffle_derive(one, set(str(k) for k in pool))
        S.Handler.round_moved(one)
        S.write_settings(stored)
        return self.shuffle_said(one, key, pool, self.shuffle_left(one, pool),
                                 pd_shuffle.place(one, key))

    def round_log(self, cid, one, why):
        """One line in debug.log when a round starts over, and why."""
        try:
            pd_log.raw("%s shuffle %s for %s: round %d starts over - %s, %d played%s"
                    % (time.strftime("%H:%M:%S"), cid, self.watcher(),
                       int(one.get("run") or 1), why,
                       len(one.get("played") or []), chr(10)))
        except Exception:
            pass

    @staticmethod
    def as_held(key):
        """A key as the title it names: a pack copy's old key or file id becomes its title's
        id, the one the library files it under once it is here (owner, 2026-11-06)."""
        key = str(key)
        if key.startswith("os"):
            return key
        try:
            import pd_torrents
            return pd_torrents.title_of(key)
        except Exception:
            return key

    def follows_a_main(self):
        """Whether this machine is a copy following a main server."""
        import pd_follow
        try:
            one = pd_follow.settings(S.local().lib.config())
        except Exception:
            return False
        return bool(one.get("on") and one.get("master"))

    def can_be_played(self, key):
        """Whether this machine holds a file for one title.

        True for everything on the main server. A copy holds part of a shelf, and
        drawing a title it has not got yet stops playback: the film cannot be fetched
        from a main server that is off, which is the only time a copy is answering a
        draw at all.
        """
        try:
            return bool(S.local().file_for(str(key), 0))
        except Exception:
            return True               # unanswerable is not the same as missing

    @staticmethod
    def fold_round(mine, cid):
        """A collection's round kept under "coll:<id>" as well as its id: the
        collection's own is the one kept, and the other only stands in where the
        collection has none - its places newer than the own round's carried over, since
        they are where the viewer was last."""
        rounds = mine.get("shuffles")
        if not isinstance(rounds, dict) or str(cid).startswith(("coll:", "show:")):
            return
        other = rounds.pop("coll:" + str(cid), None)
        own = rounds.get(str(cid))
        if isinstance(other, dict) and not isinstance(own, dict):
            rounds[str(cid)] = other
        elif isinstance(other, dict) and isinstance(other.get("at"), dict):
            when = lambda v: int(v.get("when") or 0) if isinstance(v, dict) else 0
            kept = max([when(v) for v in (own.get("at") or {}).values()] or [0])
            newer = {k: v for k, v in other["at"].items() if when(v) > kept}
            if newer:
                own["at"] = dict(sorted(newer.items(), key=lambda kv: -when(kv[1]))[:1])

    #: how near its end an episode stopped counts as over for the shuffle: Continue
    #: watching's three minutes is a sitcom's last scene, and a place stopped in it was
    #: lost - the round went back to an older place instead
    SHUFFLE_OVER = 60

    def seen_last(self, key):
        """(position, duration) of this viewer's last shuffle playing of one title, from the watch
        log - both servers' rows - or their progress row, whichever is newer. None when neither.
        Ordinary watching is not a shuffle's place (owner, 2026-11-09)."""
        con = S.local().lib.db()
        try:
            rows = [r for r in (
                con.execute("SELECT position, duration, updated FROM watchlog WHERE who=? AND key=? "
                            "AND COALESCE(casual, 0) = 1 ORDER BY updated DESC LIMIT 1",
                            (self.viewer(), str(key))).fetchone(),
                con.execute("SELECT position, duration, updated FROM progress WHERE who=? AND key=? "
                            "AND COALESCE(casual, 0) = 1", (self.viewer(), str(key))).fetchone()) if r]
        finally:
            con.close()
        if not rows:
            return None
        r = max(rows, key=lambda r: int(r["updated"] or 0))
        return float(r["position"] or 0), float(r["duration"] or 0)

    def newest_watched(self, inside, places):
        """The title of this shelf this viewer watched last, where, and whether that was
        its last minute - when that was later than the round's own place. None
        otherwise."""
        import pd_localapi
        kept = max([int(v.get("when") or 0) for v in (places or {}).values()
                    if isinstance(v, dict)] or [0])
        con = S.local().lib.db()
        try:
            rows = con.execute("SELECT key, position, duration, updated FROM progress "
                               "WHERE who=? AND updated > ? ORDER BY updated DESC LIMIT 400",
                               (self.viewer(), kept)).fetchall()
        finally:
            con.close()
        for r in rows:
            key = str(r["key"])
            if key not in inside:
                continue
            position = int(r["position"] or 0)
            if position <= 30:
                return None              # barely begun: the round's own place stands
            whole = int(r["duration"] or 0)
            return key, position, bool(whole) and position >= whole - self.SHUFFLE_OVER
        return None

    def shuffle_said(self, one, key, pool, left, at=0, drawn=False):
        """One answer about a shelf: what to play, where to start, and how far in."""
        S.local().who = self.viewer()
        con = S.local().lib.db()
        try:
            item = S.local().metadata_for(con, key)
        finally:
            con.close()
        return {"item": item, "key": str(key), "resumeAt": int(at or 0),
                "run": int(one.get("run") or 1), "pool": len(pool),
                "left": int(left),
                "queue": list(one.get("queue") or [])[:self.SHUFFLE_DEEP]}

    #: when each title's place was last passed to the main server, by viewer and key
    NOTED_UP = {}

    def note_the_house(self, cid, key, position, duration):
        """Pass a shuffle place to the main server while it answers for the round.

        Played here, drawn there: the draw on the main server otherwise worked from a
        round that never heard the episode finish, stamped it newer, and the finish
        kept here was overwritten - the episode came back at its old place. The start
        and the finish always go; in between, one every half minute.
        """
        import pd_localapi
        token = self.bearer()
        if not token or not self.follows_a_main():
            return
        edge = position < 30 or pd_localapi.LocalAPI.watched_through(position, duration, key)
        mark = token + "/" + str(key)
        if not edge and time.time() - S.Handler.NOTED_UP.get(mark, 0) < 30:
            return
        S.Handler.NOTED_UP[mark] = time.time()
        if len(S.Handler.NOTED_UP) > 500:
            S.Handler.NOTED_UP.clear()
        body = {"id": str(cid), "key": str(key), "position": position, "duration": duration}
        threading.Thread(target=self.house_answers,
                         args=("/collections/shuffle/note", body), daemon=True).start()

    def shuffle_note(self, cid, key, position, duration, forward=True):
        """Remember where a shuffled playing got to, on the shelf it came from.

        Near the end is finished and the note is dropped, so coming back does not
        offer to resume something four seconds from its credits.
        """
        import pd_localapi
        if forward:
            try:
                self.note_the_house(cid, key, position, duration)
            except Exception:
                pass
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        one = self.shuffle_round(mine, cid)
        places = dict(one.get("at") or {})
        # Only a key the library holds. A player reported a file id once and it went
        # into the round as the title being played, which left the shelf with no row
        # on Continue watching at all: nothing could be looked up for it.
        con = S.local().lib.db()
        try:
            if not S.local().metadata_for(con, str(key), brief=True):
                return
        finally:
            con.close()
        # the player's report: the place of the title the round is on, the end of it, or
        # the next one starting by itself - the round follows (shuffle handler)
        import pd_shuffle
        if pd_shuffle.follow(one, str(key), float(position or 0), float(duration or 0)):
            self.shuffle_derive(one)
            S.Handler.round_moved(one)
            S.write_settings(stored)

    #: keys that have already asked for what follows them, so a report every ten
    #: seconds asks once. Cleared when it grows: this is a note, not a record.
    ASKED_NEXT = {}

    def keep_the_next_one_ready(self, key):
        """Somebody is half way through an episode: fetch the one after it from a pack.

        Only where the setting is on, only for an episode, and only where a pack has
        the next one and this machine does not. Asked once per episode: the player
        reports its place every few seconds, and every one of those reports is past the
        middle once the first one is.
        """
        key = str(key or "")
        import pd_torrents
        if not key.startswith("e") or not pd_torrents.next_wanted():
            return
        now = time.time()
        if now - (S.Handler.ASKED_NEXT.get(key) or 0) < 3600:
            return
        if len(S.Handler.ASKED_NEXT) > 200:
            S.Handler.ASKED_NEXT.clear()
        S.Handler.ASKED_NEXT[key] = now
        try:
            con = S.local().lib.db()
            try:
                row = con.execute(
                    """SELECT i.title AS show, e.season AS season, e.number AS number
                       FROM episode e JOIN item i ON i.id = e.item_id
                       WHERE e.id = ?""", (key,)).fetchone()
            finally:
                con.close()
            if not row or row["season"] is None or row["number"] is None:
                return
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            # As many ahead as the machine keeping copies keeps: it copies what is on
            # this disk, so one ahead left it one ahead whatever it was told. With no
            # copy asking lately, one, as before.
            asked = S.Handler.LAST_ASK
            if time.time() - float(asked.get("when") or 0) < 3600:
                most, hours = int(asked.get("episodes") or 1), float(asked.get("hours") or 0)
            else:
                most, hours = 1, 0.0
            for said in pd_torrents.fetch_ahead(row["show"], row["season"], row["number"],
                                                token, self.watcher(), cap, most, hours):
                if said.get("ok") and not said.get("already"):
                    pd_log.raw("%s fetching %s from a pack - %s is half way through %s%s"
                            % (time.strftime("%H:%M:%S"), said.get("episode"),
                               self.watcher(),
                               "S%02dE%02d" % (int(row["season"]), int(row["number"])),
                               chr(10)))
        except Exception:
            pass                    # a film being watched is not held up by this

    def title_finished(self, key):
        """A title finished or marked watched: it leaves the
        viewer's watchlist unless it is one of their favorites. A shuffle's round is not
        touched: what is watched outside it does not move it (owner, 2026-11-09)."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        # a favorite stays on the watchlist watched or not - that is what makes it one -
        # and an episode of a favorite programme with it
        kept = {str(k) for k in (mine.get("favorites") or [])}
        show = ""
        if str(key).startswith("e"):
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT item_id FROM episode WHERE id=?",
                                  (str(key),)).fetchone()
                show = str(row["item_id"]) if row else ""
            finally:
                con.close()
        if str(key) in kept or (show and show in kept):
            return
        listed = [str(k) for k in (mine.get("watchlist") or [])]
        if str(key) in listed:
            mine["watchlist"] = [k for k in listed if k != str(key)]
            S.write_settings(stored)

    def shuffle_lists(self, cid, most=40):
        """What is coming, what was left part-way, and what the hat has used."""
        stored = S.read_settings() or {}
        who = self.viewer()
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        one = self.shuffle_round(mine, cid, make=False)
        _, pool = self.shuffle_shelf(cid)
        places = one.get("at") or {}

        def seconds(v):
            return int(v.get("at") or 0) if isinstance(v, dict) else int(v or 0)

        def when(v):
            return int(v.get("when") or 0) if isinstance(v, dict) else 0

        carry = []
        for key in sorted(places, key=lambda k: -when(places[k])):
            item = self.casual_item(key)
            if item:
                carry.append({"item": item, "key": str(key),
                              "at": seconds(places[key]), "when": when(places[key])})
        played = [str(k) for k in reversed(one.get("played") or [])][:most]
        return {"queue": [i for i in (self.casual_item(k)
                                      for k in (one.get("queue") or [])[:10]) if i],
                "carryOn": carry,
                "seen": [i for i in (self.casual_item(k) for k in played) if i],
                "played": len(one.get("played") or []),
                "run": int(one.get("run") or 1), "pool": len(pool)}
