"""The library's answers: seasons and their cards, shelves, collections, requests, watchlists and the order things are watched in.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import pd_h
import pd_safe

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class LibraryParts(_Base):
    def casual_item(self, key):
        """One key as a thing with a poster, for a client to name and show."""
        S.local().who = self.viewer()
        con = S.local().lib.db()
        try:
            return S.local().metadata_for(con, key)
        finally:
            con.close()

    def ask_the_pack(self, keys):
        """Start fetching anything here that only a pack has, quietly.

        A shuffle can now draw a title the library does not hold. Nothing waits for
        this: the draw answers at once, saying the title is still coming, and the
        download runs behind it.
        """
        keys = [str(k) for k in keys]
        import pd_torrents
        wanted = [k for k in keys if pd_torrents.only_in_pack(k)]
        if not wanted:
            return
        who = self.watcher()

        def work():
            import pd_torrents
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            # the one being opened first: the rest wait until it plays, or a minute. Asked
            # at once, two episodes were read out of a pack on the drive the episode opened
            # from, and it took 18 s to start (owner, 2026-11-06)
            if keys and keys[0] not in wanted:
                until = time.time() + S.START_FIRST_S
                while time.time() < until and not S.first_picture(S.WATCHING.snapshot(), who):
                    time.sleep(1)
            for key in wanted:
                try:
                    pd_torrents.request(key, token, self.watcher(), cap, auto=True,
                                        reason="drawn in a shuffle")
                except Exception:
                    pass               # a shuffle is not held up by a download
        threading.Thread(target=work, daemon=True).start()

    #: pack titles in a shuffle's hat already asked for, and when
    HAT_ASKED = {}

    def fetch_the_hat(self, who, keys):
        """Fetch what a round's hat holds that only a pack has, once an hour per title.

        A draw asks the pack too, but a hat that was resumed, peeked at or put back was
        never asked for, and a copy can only take what is on this disk.
        """
        now = time.time()
        if len(S.Handler.HAT_ASKED) > 500:
            S.Handler.HAT_ASKED.clear()
        # a round is kept as it was last moved: an episode in it whose file has gone
        # is still under its library key there, and is asked for as the pack's
        keys = [str(k) for k in keys]
        try:
            con = S.local().lib.db()
            try:
                held = self.held_keys(con, keys)
                stand = self.packed_for(con, [k for k in keys if k not in held])
            finally:
                con.close()
            keys = [stand.get(k, k) for k in keys]
        except Exception:
            pass
        import pd_torrents
        wanted = [k for k in keys if pd_torrents.only_in_pack(k)
                  and now - S.Handler.HAT_ASKED.get(k, 0) > 3600]
        if not wanted:
            return
        for key in wanted:
            S.Handler.HAT_ASKED[key] = now
        name = self.name_of(who)
        cap = self.weekly_limits("downloadGbWeek").get(name.strip().lower(), 0.0)

        def work():
            import pd_torrents
            for key in wanted:
                try:
                    if not pd_torrents.arrived(key):
                        pd_torrents.request(key, who, name, cap, auto=True,
                                            reason="in a shuffle's hat, drawn soon")
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def fetch_ahead(self, keys):
        """Fetch subtitles for what is coming, quietly and in the background.

        The same trick that makes an episode start with its subtitles already beside
        it, applied to a queue rather than to one series. Failures are silent: this is
        a courtesy, and nothing waits for it.
        """
        def work():
            for key in keys:
                try:
                    self.auto_subtitle(key, "")
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def seasons_of(self, item):
        """The season keys of one programme, in order."""
        con = S.local().lib.db()
        try:
            rows = con.execute(
                "SELECT DISTINCT season FROM episode WHERE item_id=? ORDER BY season",
                (str(item),)).fetchall()
            return ["%s-s%d" % (item, r["season"]) for r in rows]
        except Exception:
            return []
        finally:
            con.close()

    def read_as_seasons(self, con, raw):
        """A shelf's keys as the cards they are read as: seasons, not episodes.

        Returns (cards, keys) - the season cards, and the keys that are not part of
        any of them and stand for themselves.
        """
        import pd_torrents
        # Episodes stand as the seasons they belong to. A shelf holding four
        # episodes of a fifty-six episode programme showed four episode cards
        # and said nothing about the programme; one season card saying "4 of
        # 56 episodes" is what somebody reading the shelf wants to know. The
        # episodes are still what the shelf holds - this is how it is read,
        # not what it is.
        keys, seasons, whole = [], {}, set()
        for key in raw:
            if key.startswith("e"):
                seat = con.execute(
                    "SELECT item_id, season, number FROM episode WHERE id=?",
                    (key,)).fetchone() if S.is_episode(key) else None
                if seat:
                    where = "%s-s%d" % (seat["item_id"], seat["season"] or 0)
                    seasons.setdefault(where, [])
                    seasons[where].append((seat["number"] or 0, key))
                    continue
            elif re.match(r"^[0-9a-f]{12}-s\d+$", key):
                whole.add(key)
                continue
            elif pd_torrents.only_in_pack(key):
                # an episode only a pack holds stands in its season too, under the
                # programme the library has: a series marked whole is its pack
                # episodes as well, and they read as seasons, not as cards of their own
                seat = self.packed_seat(con, key)
                if seat:
                    where, number = seat
                    seasons.setdefault(where, [])
                    seasons[where].append((number, key))
                    continue
            keys.append(key)
        # A programme is its seasons too. One card for a thirty-five season
        # series said nothing about which part of it is on the shelf, and
        # there was nothing to open to take a single episode off.
        mine = [k for k in keys if re.fullmatch(r"[0-9a-f]{12}", k)]
        if mine:
            shows = set(str(r["id"]) for r in con.execute(
                "SELECT id FROM item WHERE type='show' AND id IN (%s)"
                % ",".join("?" * len(mine)), mine))
            drawn = set()
            for show in shows:
                mine = set()
                for r in con.execute(
                        "SELECT DISTINCT season FROM episode WHERE item_id=?",
                        (show,)):
                    whole.add("%s-s%d" % (show, r["season"] or 0))
                    mine.add(int(r["season"] or 0))
                    drawn.add(show)
                # and the seasons only a pack has. A programme somebody holds a
                # dozen episodes of drew nine tiles of one and two episodes each
                # and said nothing about the rest of the series, which is most of
                # it - the same merge the programme's own page already makes.
                row = con.execute("SELECT title FROM item WHERE id=?",
                                  (show,)).fetchone()
                for extra in S.local()._offered_seasons_of(
                        row["title"] if row else "", mine):
                    whole.add("%s-s%d" % (show, int(extra.get("index") or 0)))
                    drawn.add(show)
            # a programme with no episodes here has no seasons to draw, so it
            # keeps its own card rather than disappearing off the shelf
            keys = [k for k in keys if k not in drawn]
        # what each of those seasons holds - one query per programme, not one
        # per episode: a shelf of long-running series is thousands of them
        here = {}
        for show in sorted(set(k.partition("-s")[0] for k in whole)):
            for r in con.execute(
                    "SELECT id, season, number FROM episode WHERE item_id=? "
                    "ORDER BY season, number", (show,)):
                where = "%s-s%d" % (show, r["season"] or 0)
                if where in whole:
                    seasons.setdefault(where, [])
                    seasons[where].append((r["number"] or 0, str(r["id"])))
                    here.setdefault(where, set()).add(int(r["number"] or 0))
        # A season a pack can give and this machine has none of has no rows at all,
        # so it had no tile; a season it has two episodes of is not two episodes
        # long. How big each one really is comes from the pack, the way the
        # programme's own page already counts them.
        sizes = {}
        for where in whole:
            seasons.setdefault(where, [])
            show, _, number = where.partition("-s")
            row = con.execute("SELECT title FROM item WHERE id=?", (show,)).fetchone()
            try:
                number = int(number)
            except ValueError:
                continue
            extra = S.local()._offered_episodes_of(
                row["title"] if row else "", number, here.get(where) or set())
            if extra:
                sizes[where] = len(here.get(where) or ()) + len(extra)
        rows = []
        for where, inside in seasons.items():
            one = self.season_card(con, where, inside)
            if one:
                # what is here, against the whole of it - the rest is downloadable
                one["leafCount"] = max(int(one.get("leafCount") or 0),
                                       int(sizes.get(where) or 0))
                # nothing of it here yet: the Download ribbon, like anything else a
                # pack can give, so a series mostly on offer reads as one at a glance
                if not any(str(k).startswith("e") for _, k in inside):
                    one["offered"] = True
                rows.append(one)
        return rows, keys

    #: pack episode key -> (season key under the library's programme, episode number)
    PACKED_SEAT = {}

    def packed_seat(self, con, key):
        """Where an episode only a pack holds sits: the library programme of the same
        name and its season, or None when the library has no such programme."""
        if key in S.Handler.PACKED_SEAT:
            return S.Handler.PACKED_SEAT[key]
        seat = None
        try:
            import pd_torrents
            _, film = pd_torrents.by_key(key)
            if film and film.get("kind") == "episode":
                name = str(film.get("name") or film.get("title") or "")
                want = pd_torrents.show_key(name)
                for r in con.execute("SELECT id, title FROM item WHERE type='show'"):
                    if pd_torrents.show_key(r["title"]) == want:
                        seat = ("%s-s%d" % (r["id"], int(film.get("season") or 0)),
                                int(film.get("episode") or 0))
                        break
        except Exception:
            seat = None
        if len(S.Handler.PACKED_SEAT) > 5000:
            S.Handler.PACKED_SEAT.clear()
        S.Handler.PACKED_SEAT[key] = seat
        return seat

    def season_card(self, con, where, inside):
        """One season key as the card that is read for it, or None.

        Named as the season it is. The key for a season answers with the
        programme, so nineteen cards all read as the same programme and
        counted their episodes against the whole of it - "7 of 179" for
        a season of twelve, nineteen times over.
        """
        one = S.local().metadata_for(con, where, brief=True)
        if not one:
            return None
        show, _, number = str(where).partition("-s")
        try:
            number = int(number)
        except ValueError:
            number = 0
        seat = con.execute(
            "SELECT COUNT(*) c FROM episode WHERE item_id=? AND season=?",
            (show, number)).fetchone()
        one = dict(one)
        one["type"] = "season"
        one["ratingKey"] = str(where)
        # the programme it hangs off, so pressing the card opens the
        # show at this season rather than at the first one
        one["parentRatingKey"] = show
        one["parentTitle"] = one.get("title") or ""
        one["grandparentTitle"] = one.get("title") or ""
        one["title"] = "Season %d" % number if number else "Specials"
        one["index"] = number
        # its own poster: the key answers with the programme's, and a shelf of
        # seventeen seasons was the same picture seventeen times
        one["thumb"] = S.local()._season_thumb(show, number, one.get("thumb"))
        # the season's year, not the programme's: nine cards all saying 2001
        try:
            aired = S.local()._season_aired(con, show, one.get("grandparentTitle") or "", number)
            if aired:
                one["originallyAvailableAt"] = aired
                one["year"] = int(aired[:4])
        except Exception:
            pass
        # how much of it is here, against how much of it there is: the
        # difference between a season on a shelf and part of one
        seen = set()
        mine = [k for _, k in sorted(inside)
                if not (k in seen or seen.add(k))]
        one["leafCount"] = int((seat and seat["c"]) or len(mine))
        one["shelfCount"] = len(mine)
        # and what it stands for, in order. A card rolled up out of
        # episodes is a way of reading the shelf; playing it has to play
        # the episodes, and playing the season's own key opened the
        # programme's page instead - Play took somebody to the show.
        one["holds"] = mine
        return one

    SEASON_KEY = re.compile(r"^([0-9a-f]{12})-s(\d+)$")

    @staticmethod
    def holds_season(con, keys, where):
        """Whether a shelf holding these keys holds this season: by its own key, by
        its programme's, or by one of its episodes."""
        m = S.Handler.SEASON_KEY.match(str(where))
        if not m:
            return False
        keys = set(str(k) for k in keys)
        if str(where) in keys or m.group(1) in keys:
            return True
        return any(str(r["id"]) in keys for r in con.execute(
            "SELECT id FROM episode WHERE item_id=? AND season=?",
            (m.group(1), int(m.group(2)))))

    @staticmethod
    def face_art(face, one):
        """The poster for the key that stands for a shelf: the season's own for a
        season or an episode, the title's for anything else."""
        m = S.Handler.SEASON_KEY.match(str(face))
        if m:
            return S.local()._season_thumb(m.group(1), int(m.group(2)),
                                         one.get("thumb") or one.get("grandparentThumb"))
        return one.get("parentThumb") or one.get("thumb") or one.get("grandparentThumb")

    @staticmethod
    def shelf_face(con, shelf, keys):
        """(key, poster) standing for a shelf: the one chosen for it, or the first
        title it holds, so a new shelf has a face at once. A season chosen on the
        shelf's page counts where the shelf holds that season as episodes or as the
        whole programme; it was dropped for the first key's poster."""
        keys = [str(k) for k in keys]
        face = str(shelf.get("cover") or "")
        if face not in keys and not S.Handler.holds_season(con, keys, face):
            import pd_torrents
            face = next((k for k in keys if not pd_torrents.only_in_pack(k)), keys[0] if keys else "")
        if not face:
            return "", None
        one = S.local().metadata_for(con, face, brief=True) or {}
        return face, S.Handler.face_art(face, one)

    def season_of(self, ekey):
        """The season key one episode belongs to, or an empty string."""
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT item_id, season FROM episode WHERE id=?",
                              (str(ekey),)).fetchone()
            return "%s-s%d" % (row["item_id"], row["season"]) if row else ""
        except Exception:
            return ""
        finally:
            con.close()

    def covers(self, mark, key):
        """Whether a mark stands above a key: a programme over one of its seasons or
        episodes, or a season over one of its episodes."""
        mark, key = str(mark), str(key)
        if mark == key:
            return True
        if key.startswith("e"):
            season = self.season_of(key)
            if mark == season:
                return True
            return bool(season) and mark == season.split("-s")[0]
        one = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
        return bool(one) and mark == one.group(1)

    def without(self, mark, key):
        """What is left of a mark when one thing under it is taken off.

        A programme becomes its seasons, and a season its episodes - one step at a
        time, because the shelf is drawn by season and a mark per episode of a
        long-running programme is a settings file nobody can read.
        """
        mark, key = str(mark), str(key)
        if mark == key:
            return []
        one = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
        if S.is_title(mark) and one and one.group(1) == mark:
            return [s for s in self.seasons_of(mark) if s != key]
        if key.startswith("e"):
            season = self.season_of(key)
            rest = [e for e in self.spread(season) if e != key] if season else []
            if mark == season:
                return rest
            if S.is_title(mark) and season.startswith(mark + "-s"):
                return [s for s in self.seasons_of(mark) if s != season] + rest
        return []

    def spread(self, key):
        """The keys a mark on this thing should actually set.

        A programme and a season are not shelves of their own: marking one means
        marking what is under it, so that an episode can then be taken off on its own
        without arguing with a mark somewhere above it. A film, or a single episode,
        stands for itself.
        """
        key = str(key or "")
        if not key or key.startswith("e"):
            return [key] if key else []
        con = S.local().lib.db()
        try:
            # and what only a pack holds of it: a programme with one season on disk and
            # eight in a pack went onto a collection as six episodes
            def packed(title, number=None):
                try:
                    import pd_torrents
                    return [str(k) for k in pd_torrents.offered_keys(title, number)]
                except Exception:
                    return []
            season = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
            if season:
                rows = con.execute(
                    "SELECT id FROM episode WHERE item_id=? AND season=? ORDER BY number",
                    (season.group(1), int(season.group(2)))).fetchall()
                show = con.execute("SELECT title FROM item WHERE id=?",
                                   (season.group(1),)).fetchone()
                here = [str(r["id"]) for r in rows]
                more = packed(show["title"], int(season.group(2))) if show else []
                return (here + [k for k in more if k not in here]) or [key]
            if not S.is_title(key):
                return [key]
            row = con.execute("SELECT type, title FROM item WHERE id=?", (str(key),)).fetchone()
            if not row or row["type"] == "movie":
                return [key]
            rows = con.execute(
                "SELECT id FROM episode WHERE item_id=? ORDER BY season, number",
                (str(key),)).fetchall()
            here = [str(r["id"]) for r in rows]
            more = packed(row["title"])
            return (here + [k for k in more if k not in here]) or [key]
        except Exception:
            return [key]
        finally:
            con.close()

    def rollup(self, keys):
        """Which seasons and programmes a list of marked episodes amounts to.

        A card for a programme or a season carries no key of its own on these shelves
        any more - marking one marks its episodes - so a grid asking "is this marked?"
        about a series would always hear no. This answers for the things above the
        episodes: fully marked, and partly marked, kept apart so a half-marked season
        can look different from a whole one.
        """
        ids = [k for k in map(str, keys)
               if S.is_episode(k)]
        if not ids:
            return [], []
        con = S.local().lib.db()
        try:
            marks = ",".join("?" * len(ids))
            held = {}
            for r in con.execute(
                    "SELECT item_id, season, COUNT(*) c FROM episode WHERE id IN (%s) "
                    "GROUP BY item_id, season" % marks, ids):
                held[(r["item_id"], r["season"])] = r["c"]
            shows = set(item for item, _ in held)
            whole = {}
            for r in con.execute(
                    "SELECT item_id, season, COUNT(*) c FROM episode WHERE item_id IN (%s) "
                    "GROUP BY item_id, season" % ",".join("?" * len(shows)),
                    sorted(shows)):
                whole[(r["item_id"], r["season"])] = r["c"]
        except Exception:
            return [], []
        finally:
            con.close()
        full, part = [], []
        for where, count in held.items():
            key = "%s-s%d" % where
            (full if count >= whole.get(where, count) else part).append(key)
        for item in shows:
            mine = sum(c for w, c in held.items() if w[0] == item)
            all_of = sum(c for w, c in whole.items() if w[0] == item)
            (full if mine >= all_of else part).append(str(item))
        return full, part

    def shelves_holding(self, key):
        """Each of this viewer's shelves, with whether it holds all, some or none of a title."""
        want = self.spread(key)
        con = S.local().lib.db()
        try:
            above = {}                             # episode -> (its season, its programme)
            eps = [k for k in want if k.startswith("e")]
            for at in range(0, len(eps), 500):
                part = eps[at:at + 500]
                for r in con.execute("SELECT id, item_id, season FROM episode WHERE id IN (%s)"
                                     % ",".join("?" * len(part)), part):
                    above[str(r["id"])] = ("%s-s%d" % (r["item_id"], r["season"]),
                                           str(r["item_id"]))
            out = []
            for shelf in self.collections():
                held = set(str(k) for k in self.collection_keys(con, shelf))
                # counted, never taken from the programme's own key: a shelf holding the
                # programme said "all" while the seasons only a pack has were not on it,
                # and pressing the tick to add them took the programme off instead
                hit = sum(1 for k in want if k in held or (
                    k in above and (above[k][0] in held or above[k][1] in held)))
                out.append({"id": str(shelf.get("id")), "name": str(shelf.get("name") or ""),
                            "mode": str(shelf.get("mode") or "filter"),
                            "state": ("all" if want and hit >= len(want)
                                      else "some" if hit else "none")})
            return out
        finally:
            con.close()

    def shelf_mark(self, shelf, key, on):
        """Put a title on a shelf or take it off. A programme or a season is its episodes,
        and on a filter shelf what the rule already catches is struck out, not unpinned."""
        con = S.local().lib.db()
        try:
            byrule = (set() if str(shelf.get("mode") or "filter") == "manual"
                      else set(str(k) for k in self.collection_keys(
                          con, dict(shelf, pinned=[], hidden=[]))))
            held = [str(k) for k in self.collection_keys(con, shelf)]
        finally:
            con.close()
        keys = self.spread(key)
        gone = set([key] + keys)
        marked = [k for k in held if k not in gone]
        if on:
            marked = keys + marked
        else:
            # one season off a programme held whole: the mark above splits into its parts
            out = []
            for k in marked:
                if self.covers(k, key):
                    out += [x for x in self.without(k, key) if x not in gone]
                else:
                    out.append(k)
            marked = out
        wanted = set(marked)
        shelf["pinned"] = [k for k in marked if k not in byrule]
        shelf["hidden"] = [k for k in byrule if k not in wanted]
        if str(shelf.get("mode") or "filter") == "manual":
            # a whole series kept as itself, so what it gains joins; taken off, it goes,
            # and an episode of one taken off on its own stays off
            series = [str(s) for s in shelf.get("series") or []]
            whole = self.is_series(key)
            if whole and on and key not in series:
                series.append(key)
            elif whole and not on:
                series = [s for s in series if s != key]
            shelf["series"] = series
            if series:
                kept = set(shelf["pinned"])
                shelf["hidden"] = sorted(
                    {str(h) for h in shelf.get("hidden") or [] if str(h) not in kept}
                    | ({k for k in keys if k not in kept} if not on and not whole else set()))
            else:
                shelf["hidden"] = []

    def whole_series(self, con, shelf):
        """The series a shelf holds whole, for the page only: put on a hand-made one,
        or caught entire by a filter's words - not a season span's part of one."""
        if str(shelf.get("mode") or "filter") == "manual":
            return [str(s) for s in shelf.get("series") or []]
        ids = [str(k) for k in self.collection_keys(con, shelf)
               if re.fullmatch(r"[0-9a-f]{12}", str(k))]
        if not ids:
            return []
        return [str(r["id"]) for r in con.execute(
            "SELECT id FROM item WHERE type='show' AND id IN (%s)" % ",".join("?" * len(ids)),
            ids)]

    @staticmethod
    def is_series(key):
        """Whether a key is a whole series in the library."""
        if not re.fullmatch(r"[0-9a-f]{12}", str(key or "")):
            return False
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT type FROM item WHERE id=?", (str(key),)).fetchone()
            return bool(row and row["type"] == "show")
        finally:
            con.close()

    @staticmethod
    def requests_file():
        return os.path.join(S.ROOT, "requests.json")

    @staticmethod
    def read_requests():
        """Everything anybody has asked for, by the key they asked for it under."""
        try:
            with open(S.Handler.requests_file(), encoding="utf-8") as f:
                said = json.load(f)
            return said if isinstance(said, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def write_requests(book):
        tmp = S.Handler.requests_file() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(book, f, indent=2)
                pd_safe.settle(f)
            os.replace(tmp, S.Handler.requests_file())
        except OSError:
            pass

    def asked_for(self, key):
        """What is known about a request for this title, or nothing."""
        return self.read_requests().get(str(key)) or None

    def collections(self):
        """This viewer's named shelves, in the order they were made."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        out = []
        for one in (mine.get("collections") or []):
            if isinstance(one, dict) and one.get("id"):
                out.append(one)
        return out

    def save_collections(self, shelves):
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        mine["collections"] = shelves[:60]
        S.write_settings(stored, merge=False)

    #: each offer list's titles made plain, kept while the list is the same list
    OFFERS_PLAIN = {}

    @staticmethod
    def offers_plain(offers, plain):
        """The offers beside their plain titles, worked out once per list.

        Every shelf matched its words against the four thousand films the packs offer,
        making each title plain again: a few hundredths of a second a shelf, most of the
        collections page.
        """
        mark = id(offers)
        got = S.Handler.OFFERS_PLAIN.get(mark)
        if got is None or got[0] is not offers:
            got = (offers, [(one, plain(one.get("title"))) for one in offers])
            # the list itself is kept, so its number cannot be handed to another
            S.Handler.OFFERS_PLAIN = {k: v for k, v in S.Handler.OFFERS_PLAIN.items()
                                    if k != mark and len(S.Handler.OFFERS_PLAIN) < 8}
            S.Handler.OFFERS_PLAIN[mark] = got
        return got[1]

    @staticmethod
    def collection_keys(con, shelf):
        """The keys a shelf holds: what its rule matches, plus and minus by hand.

        A rule is words in the title, and any of the usual narrowings - films or
        programmes, a genre, a span of years. "alien" catches four films and misses
        the two that do not say so, which is what the by-hand list is for.
        """
        rule = shelf.get("rule") or {}
        # Two kinds of shelf. A filter one keeps looking: whatever the library gains
        # that answers the rule joins it without being asked. A manual one holds
        # what it was given and nothing else - the rule is not consulted at all, so
        # a film added tomorrow cannot wander into it.
        if str(shelf.get("mode") or "filter") == "manual":
            hidden = set(str(k) for k in (shelf.get("hidden") or []))
            held = [str(k) for k in (shelf.get("pinned") or []) if str(k) not in hidden]
            # and a whole series put on it: every episode it has now, seasons linked
            # since included - it held what the series had the day it was added
            have = set(held)
            for show in shelf.get("series") or []:
                for k in S.series_episode_keys(con, str(show)):
                    if k not in have and k not in hidden:
                        have.add(k)
                        held.append(k)
            return held
        # Each entry is one rule; a title joins the shelf if it answers any of them.
        # Within a rule every word must appear, in any order and anywhere in the
        # title - so "night train" is narrower than "night", and "comet | harbour" is
        # two rules rather than one long one.
        # Punctuation is dropped from both sides before they are compared. A rule
        # typed as "Its Raining Again" found nothing at all, because the apostrophe in
        # the title means "its" is not inside "it's" - and nobody types the
        # apostrophe when they are naming a shelf.
        plain = S.plain_words

        words = [plain(w) for w in (rule.get("words") or []) if str(w).strip()]
        rules = [[part for part in one.split() if part] for one in words if one]
        # and the other half of the rule: what the words above catch and should not.
        # Read the same way - every word in a rule must appear - and a title answering
        # any of them stays out, whatever the include rules said.
        bans = [plain(w) for w in (rule.get("without") or []) if str(w).strip()]
        blocked = [[part for part in one.split() if part] for one in bans if one]
        # A programme joins whole unless a span says which part of it. The span is
        # read per episode, and the seasons it touches are what the shelf shows.
        import pd_seasons
        spans = pd_seasons.parse(rule.get("season") or "")
        # and the part of that span to leave out, so "all of it except season 2" is
        # one rule rather than nine
        nope = pd_seasons.parse(rule.get("seasonWithout") or "")
        kind = (rule.get("type") or "").strip()
        genres = {g.strip().lower() for g in str(rule.get("genre") or "").split(",")
                  if g.strip()}
        early = int(rule.get("from") or 0)
        late = int(rule.get("to") or 0)
        found = []
        here = set()
        if words or kind or genres or early or late:
            sql = "SELECT id, title, sort_title, year, genres, type FROM item WHERE 1=1"
            args = []
            if kind in ("movie", "show"):
                sql += " AND type=?"
                args.append(kind)
            if early:
                sql += " AND year >= ?"
                args.append(early)
            if late:
                sql += " AND year <= ?"
                args.append(late)
            for row in con.execute(sql + " ORDER BY sort_title", args):
                name = plain(row["title"])
                if rules and not any(all(part in name for part in one)
                                     for one in rules):
                    continue
                if any(all(part in name for part in one) for one in blocked):
                    continue
                # several genres, comma-joined: the title carries all of them
                if not genres <= {g.strip().lower()
                                  for g in (row["genres"] or "").split(",")}:
                    continue
                # A span only means anything to a programme; a film has no
                # seasons and is left exactly as it was.
                if (spans or nope) and row["type"] == "show":
                    def inside(sn, n):
                        if spans and not pd_seasons.holds(spans, sn, n):
                            return False
                        return not (nope and pd_seasons.holds(nope, sn, n))
                    bysn = {}
                    for r in con.execute(
                            "SELECT id, season, number FROM episode WHERE item_id=? "
                            "ORDER BY season, number", (row["id"],)):
                        bysn.setdefault(int(r["season"] or 0), []).append(
                            (int(r["number"] or 0), str(r["id"])))
                    kept = False
                    for sn in sorted(bysn):
                        eps = bysn[sn]
                        mine = [key for n, key in eps if inside(sn, n)]
                        if not mine:
                            continue
                        kept = True
                        if len(mine) == len(eps):
                            # the shape the rest of the program already uses for a
                            # season - spread(), covers() and the marks all read it
                            found.append("%s-s%d" % (row["id"], sn))
                        else:
                            # part of a season is the episodes themselves: a span
                            # ending at s6e2 that put the whole of season six on the
                            # shelf is not the span that was written
                            found += mine
                    # and the seasons only a pack has: a span over a programme
                    # this machine holds a dozen episodes of was read against those
                    # twelve alone, so excluding the last few seasons left seven
                    # tiles rather than the rest of the series.
                    for s_o in S.local()._offered_seasons_of(row["title"], set(bysn)):
                        sn = int(s_o.get("index") or 0)
                        nums = [int(e.get("index") or 0) for e in
                                S.local()._offered_episodes_of(row["title"], sn, set())]
                        if any(inside(sn, n) for n in nums):
                            found.append("%s-s%d" % (row["id"], sn))
                            kept = True
                    if not kept:
                        continue
                    here.add((name, int(row["year"] or 0)))
                    continue
                found.append(str(row["id"]))
                # what the library already holds, so a pack offering the same
                # programme does not put it on the shelf a second time
                here.add((name, int(row["year"] or 0)))
            # films on offer from a torrent pack, by the same rule: they join the shelf
            # greyed until they are here
            if kind in ("", "movie", "show"):
                try:
                    import pd_torrents
                    offers = []
                    if kind in ("", "movie"):
                        offers += S.Handler.offers_plain(pd_torrents.offered(), plain)
                    # and the programmes a pack can give. A shelf matched the library
                    # and the films on offer, so a series that only a pack has could
                    # not join one - it is on the shelf everywhere else it is listed.
                    if kind in ("", "show"):
                        offers += S.Handler.offers_plain(pd_torrents.offered_shows(), plain)
                except Exception:
                    offers = []
                for one, name in offers:
                    year = int(one.get("year") or 0)
                    if (early and (not year or year < early)) or (late and (not year or year > late)):
                        continue
                    if rules and not any(all(part in name for part in r) for r in rules):
                        continue
                    if any(all(part in name for part in r) for r in blocked):
                        continue
                    if not genres <= {g.strip().lower() for g in one.get("genres") or []}:
                        continue
                    # A programme stood on a shelf twice: once as the programme in
                    # the library and once as the same programme a pack was offering.
                    # An offer is only worth showing for something not already here.
                    if (name, year) in here or any(n == name for n, _y in here):
                        continue
                    found.append(str(one["ratingKey"]))
        # by hand: what the rule missed, and what it should not have caught
        hidden = set(str(k) for k in (shelf.get("hidden") or []))
        for key in (shelf.get("pinned") or []):
            if str(key) not in found:
                found.append(str(key))
        return [k for k in found if k not in hidden]

    def onto_watchlist(self, key):
        """A title somebody asked for, kept in their own corner.

        Asking is the end of what a guest can do about a film - the answer is the
        owner's - so the one place it can be looked for afterwards is the watchlist
        it was going to be on anyway. True when it was put there, False when it was
        already on it.
        """
        if not key:
            return False
        try:
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            had = [str(k) for k in (mine.get("watchlist") or [])]
            if key in had:
                return False
            S.put_on_watchlist(mine, [key])
            S.write_settings(stored)
            return True
        except Exception:
            return False

    def watch_fetched(self, key="", release=""):
        """Something this viewer fetched, on their watchlist: a film, or a programme for a
        season (owner, 2026-11-06). Kept as fetched as well, so its arrival says it is now on
        the watchlist rather than that it was."""
        try:
            listed = S.watch_key_of(key, release)
            if not listed:
                return False
            got = self.onto_watchlist(listed)
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            had = [str(k) for k in mine.get("watchFetched") or []]
            if listed not in had:
                mine["watchFetched"] = (had + [listed])[-100:]
                S.write_settings(stored, merge=False)
            return got
        except Exception:
            return False

    def fetched_here(self):
        """The library keys of what this viewer downloaded onto their watchlist."""
        import pd_streaming
        mine = self.viewer_settings(self.settings_file())
        out = set()
        for k in mine.get("watchFetched") or []:
            k = str(k)
            out.add(str((pd_streaming.one_of(k) or {}).get("here") or "") if k.startswith("rt") else k)
        return out - {""}

    @staticmethod
    def name_of_token(token):
        """The name an invitation gives its key, or ""."""
        try:
            return str(S.Handler.__new__(S.Handler).name_of(token) or "")
        except Exception:
            return ""

    def watchlist(self):
        """The keys this viewer has marked, newest first."""
        mine = self.viewer_settings(self.settings_file())
        return [str(k) for k in (mine.get("watchlist") or [])]

    def watchlist_of(self, who):
        """One viewer's watchlist, by the name their settings are filed under.

        In the order it will be watched rather than the order it was marked. A list
        built over a year holds episodes in the order somebody pressed the button, so
        five of one series arrived from three different seasons at once.
        """
        stored = S.read_settings() or {}
        if who == "me":
            marked = list(stored.get("watchlist") or [])
        else:
            marked = list(((stored.get("users") or {}).get(who) or {})
                          .get("watchlist") or [])
        return self.in_watching_order(marked)

    def in_watching_order(self, keys):
        """Episodes of one series together and in order; everything else left alone.

        A series keeps the place its first marked episode had, so somebody's list
        still reads in the order they built it - it is only the inside of each series
        that is put right.
        """
        keys = [str(k) for k in keys]
        wanted = [k for k in keys if k.startswith("e")]
        if len(wanted) < 2:
            return keys
        facts = {}
        try:
            con = S.local().lib.db()
            rows = con.execute(
                "SELECT id, item_id, season, number FROM episode WHERE id IN (%s)"
                % ",".join("?" * len(wanted)),
                [k for k in wanted if S.is_episode(k)]).fetchall()
            for r in rows:
                facts[str(r["id"])] = (r["item_id"], r["season"] or 0,
                                          r["number"] or 0)
        except Exception:
            return keys                    # nothing known: leave the list as it is
        seen, out = set(), []
        for k in keys:
            if k in seen:
                continue
            if k not in facts:
                seen.add(k)
                out.append(k)
                continue
            show = facts[k][0]
            same = sorted((x for x in keys if facts.get(x, (None,))[0] == show),
                          key=lambda x: facts[x][1:])
            for x in same:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
        return out

    #: A file's mark, kept against its size and time so it is worked out once. Reading
    #: two ends of a film is a fraction of a second; reading the whole of a hundred of
    #: them, every minute, would be a disk full of work for nothing.
    MARKS = {}

    @classmethod
    def quick_mark(cls, path):
        """A cheap, exact-enough fingerprint: the size and both ends of the file.

        The same scheme subtitle sites use to recognise a film. Two different rips
        never agree on it, and a file that arrives changed - or half - does not agree
        with itself. Hashing twenty gigabytes to answer the same question would cost
        a minute a film.
        """
        try:
            stat = os.stat(path)
        except OSError:
            return ""
        seen = cls.MARKS.get(path)
        if seen and seen[0] == (stat.st_size, int(stat.st_mtime)):
            return seen[1]
        import hashlib
        digest = hashlib.sha256()
        digest.update(str(stat.st_size).encode())
        try:
            with open(path, "rb") as f:
                digest.update(f.read(65536))
                if stat.st_size > 131072:
                    f.seek(-65536, os.SEEK_END)
                    digest.update(f.read(65536))
        except OSError:
            return ""
        mark = digest.hexdigest()
        cls.MARKS[path] = ((stat.st_size, int(stat.st_mtime)), mark)
        if len(cls.MARKS) > 400:
            cls.MARKS.clear()
        return mark

    def casual_places(self, who):
        """What this person left part-way in each shelf's shuffle, newest first, five per round."""
        stored = S.read_settings() or {}
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        out = []
        for one in (mine.get("shuffles") or {}).values():
            if not isinstance(one, dict):
                continue
            places = []
            for key, value in (one.get("at") or {}).items():
                when = int(value.get("when") or 0) if isinstance(value, dict) else 0
                places.append((when, str(key)))
            # each round its own five: one cap across rounds left the others none
            out += [k for _, k in sorted(places, reverse=True)[:5] if k not in out]
        return out

    def casual_ahead(self, who):
        """What this person's shuffle rounds draw next, most recently moved round first.

        Only queues already drawn: peeking does not take from the hat, and an empty
        queue means nothing is copied.
        """
        stored = S.read_settings() or {}
        mine = (stored if who == "me"
                else ((stored.get("users") or {}).get(who) or {}))
        rounds = [r for r in (mine.get("shuffles") or {}).values() if isinstance(r, dict)]
        ahead = []
        for one in sorted(rounds, key=lambda r: -self.round_stamp(r)):
            # under the library's own keys: a round moved on the copy, which lacked the
            # file, came back holding the pack's key for an episode this machine has, and
            # a pack key is never copied - the copy fetched it from the pack itself while
            # somebody waited for it to start (owner, 2026-11-06)
            played = set(self.as_held(k) for k in (one.get("played") or []))
            # The one the round is on comes first. Drawing a title writes it down as
            # played there and then, so a copy stopped wanting it at the very moment
            # somebody started watching it - and swept it while they were. It is the
            # one thing on the list that is certainly about to be read.
            now_on = self.shuffle_current(one)
            now_on = self.as_held(now_on) if now_on else now_on
            if now_on and now_on not in ahead:
                ahead.append(now_on)
            # every round's whole hat: one cap across all rounds left older rounds
            # with nothing on the copy
            ahead += [k for k in (self.as_held(k) for k in (one.get("queue") or []))
                      if k not in played and k not in ahead][:self.SHUFFLE_DEEP]
        return ahead

    #: however few hours are asked for, a series is copied this far ahead: an
    #: evening is at least three episodes of anything.
    LEAST_AHEAD = 3

    #: What is on a screen this minute without anybody having chosen it. Kept where it
    #: is, never fetched, and never the start of a run of episodes.
    CASUAL_NOW = set()

    #: What the shuffle has drawn for somebody. Copied as it stands, with nothing
    #: taken after it: the next thing a shuffle plays is the next thing it draws.
    DRAWN = set()

    #: Keys last seen playing unchosen, and when. CASUAL_NOW holds only what a player
    #: is reporting this second, and the screen list below outlives that by design -
    #: so the episode a shuffle played a minute ago read as an ordinary one, and the
    #: run of episodes after it was stocked in season order for a shelf that is never
    #: played in season order. A key is dropped again the moment somebody chooses it.
    CASUAL_SEEN = {}
    CASUAL_KEEP = 200

    #: The most this machine has been seen to shift at once, and when that was seen.
    #: Nobody can ask a network how fast it is; what it has actually carried is the
    #: only honest answer, and it is only learnt while something is pushing it.
    LINK = {"home": {"seen": 0.0, "when": 0.0},
            "away": {"seen": 0.0, "when": 0.0}}

    #: How much of the line to leave alone above what the films are drawing. A film
    #: asks in bursts and a buffer that is filling wants more than its average.
    SPARE = 0.85

    #: And the least a copy may have, so it is never stopped outright.
    LEAST_MBIT = 4.0

    #: What a copy is allowed this second. Steered rather than calculated: the line is
    #: wireless and the number it carried an hour ago is not the number it carries
    #: when somebody walks between the aerials.
    PACE = {"home": {"mbit": 0.0, "when": 0.0},
            "away": {"mbit": 0.0, "when": 0.0}}

    #: The live rows the steering last saw. Handed in rather than fetched again: the
    #: caller has just taken the snapshot and taking a second one costs the lock.
    WATCHING_NOW = []

    #: Given up in one step when a film is struggling, and taken back in small ones.
    #: The other way round ends with the cache winning, which is the wrong way round.
    BACK_OFF = 0.55
    #: a tenth down each second while somebody is short
    STEP = 0.10
    #: and at least half again up after a test nobody ran short in
    RISE = 0.50
    #: what a copy keeps of its speed when a player is nearly out
    PANIC_TO = 0.10
    #: how often a copy held down tries no ceiling, and for how long
    PROBE_EVERY = 20
    PROBE_FOR = 5
    #: free of any ceiling once the steps climb to this share of the best the line
    #: has carried
    FREE_AT = 0.90
    STEP_UP = 8.0

    #: A viewer getting this much less than its own running average, with a socket
    #: still open, is being starved rather than sitting on a full buffer.
    STARVED = 0.75

    #: Why a thing is copied, gathered under the name the settings use for it. A copy
    #: says which of these it will take this minute - each can be off, kept only inside
    #: its night hours, or kept always - and what it does not ask for is left out here
    #: rather than fetched and swept later.
    #: How many part-way films one person brings to a copy, and how old a place may
    #: be before it stops counting as being in the middle of something. Forty apiece
    #: with no age at all put a hundred and ten films in the queue - a guest who
    #: sampled fifty things last winter was still "part-way through" every one.
    PARTWAY_EACH = 6
    PARTWAY_DAYS = 30

    #: The title each viewer has on a screen and the one before it, newest first. A
    #: copy is asked to hold them until newer ones push them off, rather than for so
    #: many hours: an episode stopped being wanted the moment its player went quiet,
    #: and the copy deleted it with a minute of it still to run. The one before is
    #: kept so somebody going back an episode to remind themselves what happened does
    #: not wait for it to be fetched again. What finally takes them off the disk is
    #: the cap, which deletes the oldest first.
    SCREEN_LATELY = {}
    SCREEN_EACH = 2

    #: keys put on the list because they follow something, rather than because anybody
    #: asked for them; rebuilt every time the list is made
    FETCHED_AHEAD = set()

    #: every further (why, who) a listed file is kept for, beyond the first; rebuilt
    #: with the list. The file is copied once and shown under each person.
    ALSO = {}

    @classmethod
    def also_for(cls, key, why, who):
        pairs = cls.ALSO.setdefault(str(key), [])
        if (why, who) not in pairs:
            pairs.append((why, who))

    KINDS = {
        "partway": ("part-way through", "the one before it"),
        "watchlist": ("on their watchlist", "a favourite", "on a shelf"),
        "lately": ("watched lately",),
        "shuffle": ("the shuffle's next", "left part-way in the shuffle"),
        "screen": ("on a screen now",),
    }

    @classmethod
    def kind_of(cls, why):
        """Which kind a reason belongs to, or none when it belongs to none."""
        for kind, reasons in cls.KINDS.items():
            if why in reasons:
                return kind
        return ""

    def partway_keys(self, con, who):
        """What one viewer is part-way through, by programme rather than by episode.

        The question asked here used to be whether a progress row had been stopped in
        the middle, which is part-way through an *episode* - so somebody working
        through a series and finishing each one was never part-way through anything.
        A programme watched that way had nothing kept ahead of it at all, and the
        episode Continue watching was offering was the one episode the copy did not
        hold: press play with the main server off and there was nothing to read.

        Continue watching has always answered this properly - a finished episode hands
        over to the first unwatched one after it - so the same two steps are taken
        here. Stopped in the middle still counts, and is preferred: it is where
        somebody actually is.
        """
        out, seen, shows = [], set(), set()          # (key, why) for each
        # What this viewer has cleared off Continue watching. Saying "I am done with
        # this" took it off the shelf and left the copy holding it: ninety-eight
        # titles were being kept part-way through while the shelf showed two. Aside
        # is the same answer to both questions.
        # Both places it can be written: the owner's own settings sit at the top of
        # the file and every other viewer's under their key, and the same programme
        # can be in either. The later of the two stands.
        def take(key, why="part-way through"):
            key = str(key or "")
            if key and key not in seen:
                seen.add(key)
                out.append((key, why))

        since = int(time.time()) - self.PARTWAY_DAYS * 86400
        for row in con.execute(
                """SELECT key, position, duration, updated,
                          COALESCE(marked, 0) marked FROM progress WHERE who = ?
                   AND COALESCE(casual, 0) = 0 AND updated > ? AND position > 30
                   ORDER BY updated DESC LIMIT 400""", (who, since)):
            key = str(row["key"])
            # One programme once, as the shelf itself shows: a series watched out of
            # order would otherwise name a key for every gap in it.
            family = S.local().deck_family(con, key)
            if family in shows:
                continue
            shows.add(family)
            # A mark made by hand is finished however little of it was played - the
            # shelf says so, and the two must not disagree about what is still going
            # on. Ninety-five titles were being kept part-way through while the shelf
            # showed two, and most of them had been ticked off by hand.
            done = bool(row["marked"]) or S.local().watched_through(
                row["position"], row["duration"], key)
            if not done:
                take(key)                  # stopped in the middle of this one
                # and the one before it, the same as for an episode just finished:
                # being part-way through one is the commoner way to be part-way
                # through a programme, and it was the branch that stopped here.
                if key.startswith("e"):
                    take(S.local().prev_episode_key(con, key), "the one before it")
                continue
            # Finished. What follows it is what somebody reaches for next - and only
            # an episode; a film that is over leads nowhere.
            if not key.startswith("e"):
                continue
            after = key
            for _ in range(400):           # a season is not longer than this
                after = S.local().next_episode_key(con, after)
                if not after or not S.local()._watched(con, after):
                    break
            take(after)
            # and the one just finished. It is what somebody goes back to - the end of
            # it missed, or the whole of it again - and holding only what comes next
            # meant the copy had the episode ahead and not the one behind, which is
            # the one they had actually been watching.
            take(key, "the one before it")
        return out

    def wanted_keys(self, con, hours, deck, episodes, mine, casual, whole, watched=None,
                    kinds=None):
        """What is worth copying, in the order to fetch it, as (key, why, who, live).

        One list of sources, asked in order. Nothing here reads a file or decides a
        rank: it says what the main server wants and why, and the order it says it in is
        the order it is wanted. Whether a thing is on a screen this minute travels
        with it, because that is the one fact that moves something to the front.

        `kinds` is what the copy will take this minute, by the names in KINDS. None
        means all of them, which is what every copy asked for before it could say.
        """
        import pd_torrents
        import pd_localapi
        out = []
        said = set()
        taking = None if kinds is None else set(kinds)
        S.Handler.ALSO = {}

        def want(key, why, who, live=False):
            key = str(key or "")
            if not key:
                return
            # a kind this copy is not taking now: off, or outside its hours
            if taking is not None:
                kind = self.kind_of(why)
                if kind and kind not in taking:
                    return
            if key in said:
                S.Handler.also_for(key, why, who)
                # reached twice - a watchlist and a screen, say. It is the same file
                # either way and it is wanted at the sooner of the two moments.
                for had in out:
                    if had[0] != key:
                        continue
                    if live:
                        had[3] = True
                    # and a watchlist or a favourite keeps its reason: those are copied
                    # watched or not, and the first reason reached was dropped as watched
                    # A reason that survives being watched wins over one that does
                    # not. "Watched lately" is reached first and is dropped again by
                    # the watched test below, so the episode just finished - named to
                    # be kept precisely because it has been watched - was thrown out
                    # by the reason it had already been given.
                    if (why in ("on their watchlist", "a favourite",
                                "the one before it")
                            and had[1] not in ("on their watchlist", "a favourite",
                                               "the one before it")):
                        S.Handler.also_for(key, had[1], had[2])
                        had[1], had[2] = why, who
                return
            said.add(key)
            out.append([key, why, who, live])

        if mine:
            # One person's own cache on somebody else's machine: what they are in the
            # middle of, then what they mean to watch. Nothing about anybody else in
            # the main server is theirs to copy.
            for row in con.execute(
                    """SELECT key FROM progress WHERE who = ?
                       AND position > 30
                       AND position < """ + pd_localapi.FINISHED_SQL + """
                       AND COALESCE(casual, 0) = 0 AND updated > ?
                       ORDER BY updated DESC LIMIT ?""",
                    (mine, int(time.time()) - self.PARTWAY_DAYS * 86400,
                     self.PARTWAY_EACH)):
                want(row["key"], "part-way through", self.name_of(mine))
            for key in self.watchlist_of(mine):
                want(key, "on their watchlist", self.name_of(mine))
            return out, set()

        # What is on a screen this minute. Somebody is sitting in front of it and the
        # next episode is wanted in twenty minutes, which nothing else on this list is.
        S.Handler.CASUAL_NOW = set()
        S.Handler.DRAWN = set()
        for one in (S.local().playing_now() or {}).values():
            if one.get("casual"):
                # On a screen, and nobody picked it. Named and fetched like anything on a
                # screen - the house carries on from the copy if the main server goes - but
                # the mark below stops the six episodes after it: what comes next in a
                # shuffle is whatever the shuffle draws.
                S.Handler.CASUAL_NOW.add(str(one.get("key") or ""))
                S.Handler.CASUAL_SEEN[str(one.get("key") or "")] = time.time()
                if len(S.Handler.CASUAL_SEEN) > S.Handler.CASUAL_KEEP:
                    for old_key in sorted(S.Handler.CASUAL_SEEN,
                                          key=lambda k: S.Handler.CASUAL_SEEN[k]
                                          )[:len(S.Handler.CASUAL_SEEN)
                                            - S.Handler.CASUAL_KEEP]:
                        S.Handler.CASUAL_SEEN.pop(old_key, None)
            elif one.get("key"):
                # chosen: it is an ordinary viewing again, and what follows it is
                # worth having
                S.Handler.CASUAL_SEEN.pop(str(one.get("key")), None)
            mark = str(one.get("key") or "")
            # Filed under the viewer, not under what they are called. playing_now()
            # lists a viewing twice - once under its key and once under its title -
            # and a name is not one thing either, so the same person ended up with
            # several lists of their own: seven episodes were named "on a screen now"
            # for one guest, where the most that can be on a screen is the one playing
            # and the one before it.
            whose = str(one.get("who") or one.get("name") or "")
            seen_by = self.name_of(whose)
            if mark:
                had = [k for k in (S.Handler.SCREEN_LATELY.get(whose) or [])
                       if k != mark]
                S.Handler.SCREEN_LATELY[whose] = ([mark] + had)[:S.Handler.SCREEN_EACH]
                if len(S.Handler.SCREEN_LATELY) > 40:
                    S.Handler.SCREEN_LATELY.clear()
            want(one.get("key"), "on a screen now", seen_by, True)
        # And the one before it, per viewer. A title leaves this list the second its
        # player stops reporting - the end of an episode, a pause long enough to be
        # closed, somebody stepping out - and the copy sweeps whatever the list no
        # longer names, which took an episode away with a minute of it still to run.
        # Holding the last two means the one just finished is still there while the
        # next is watched, and is there to go back to. The disk cap decides when it
        # finally goes.
        for whose, keys in list(S.Handler.SCREEN_LATELY.items()):
            seen_by = self.name_of(whose)
            for mark in keys[:S.Handler.SCREEN_EACH]:
                want(mark, "on a screen now", seen_by)
                # and it is still a shuffled one a minute after the player went
                # quiet, so nothing sequential is taken behind it
                if mark in S.Handler.CASUAL_SEEN:
                    S.Handler.CASUAL_NOW.add(mark)
            # And the episode before whatever is on now, worked out rather than
            # remembered. The list above is this server's memory of what it has seen
            # playing, so it is empty every time the server starts - and the one just
            # finished, the one somebody goes back to, was gone with it. The episode
            # before is the same episode however often this is restarted.
            first = keys[0] if keys else ""
            # Not for a shuffle. The episode before a drawn one is not the one that
            # was just watched and not one anybody is going back to - it is simply
            # the episode with the number below it, and naming it put an ordinary
            # key on the list, which the run of episodes below then followed: ten
            # files in season order per viewer, per draw, for a shelf being played
            # in no order at all. What a shuffle leaves part-way is kept by
            # casual_places; what it will play next is the hat.
            if str(first).startswith("e") and first not in S.Handler.CASUAL_SEEN:
                before = S.local().prev_episode_key(con, first)
                if before:
                    want(before, "on a screen now", seen_by)
        # The shuffle is left out here too. A casual playing writes a log line like
        # any other - it has to, or Now playing and the watch log would lie - but
        # "the house watched this lately" is a reason to copy the rest of the series,
        # and nobody watched it. One evening of shuffled episodes put five of a
        # season on the other machine at eight in the evening.
        # Named, because somebody watched it. "The main server" is not a viewer: it has no
        # invitation, it is on nobody's cache, and a queue that says it cannot be
        # filtered down to one person's shelf.
        # And only for the people who want it kept. It was the whole house before, so
        # one person's evening put the episodes after it on the other machine for
        # everybody - there was a switch for a deck, a list and a shuffle, and none
        # for this.
        decks, lists, shuffles, lately = self.cached_for()
        if lately:
            for row in con.execute(
                    "SELECT key, who FROM watchlog WHERE COALESCE(casual, 0) = 0 "
                    "AND who IN (%s) ORDER BY updated DESC LIMIT 12"
                    % ",".join("?" * len(lately)), lately):
                want(row["key"], "watched lately", self.name_of(row["who"]))

        listed = set()
        if deck:
            # What the people this is kept for are part-way through, and what they
            # mean to watch. Before this machine sleeps that is what somebody reaches
            # for next, and the other machine is the one that will be awake.
            shelved = {}             # each shelf read once, however many people have it
            for who in decks:
                for key, why in self.partway_keys(con, who):
                    want(key, why, self.name_of(who))
            for who in lists:
                # the whole list: cut at forty, the fortieth fell off each time a title
                # was marked above it and was deleted, and came back the next day - 11.7 GB
                # a time (2026-11-12). The copy's own size cap is what limits it.
                marked = self.watchlist_of(who)
                # Whole list off: a programme's night's worth, not every marked episode
                if not whole:
                    marked = self.first_unwatched(con, marked, episodes, watched,
                                                  self.name_of(who))
                for key in marked:
                    listed.add(str(key))
                    want(key, "on their watchlist", self.name_of(who))
                # A programme on a shelf somebody made is a programme meant to be
                # watched. Only the programmes: a shelf is mostly films, and naming
                # every one of those had the copy fetching hundreds of titles nobody
                # had asked for. Only the next unwatched episodes of each, as many as
                # the episodes setting allows - the bound the watchlist already uses.
                stored = self.settings_file()
                theirs = (self.viewer_settings(stored) if who == self.viewer()
                          else ((stored.get("users") or {}).get(who) or {}))
                # the programmes their whole-library shuffle is drawing from, read
                # once for the person rather than once for each shelf
                hat_shows = (self.casual_shows(con, theirs)
                             if who in shuffles else set())
                for shelf in (theirs.get("collections") or []):
                    if not (isinstance(shelf, dict) and shelf.get("id")):
                        continue
                    at = str(shelf.get("id"))
                    shows = shelved.get(at)
                    if shows is None:
                        # A shelf is a rule over the library, so reading one is a pass
                        # over every title: thirteen of them, per person, took the
                        # round from under a second to eight and a half.
                        keys = [str(k) for k in self.collection_keys(con, shelf)
                                if not pd_torrents.only_in_pack(k)]
                        shows = []
                        if keys:
                            rows = con.execute(
                                "SELECT id FROM item WHERE type='show' AND id IN (%s)"
                                % ",".join("?" * len(keys)), keys).fetchall()
                            shows = [str(r["id"]) for r in rows]
                        shelved[at] = shows
                    if not shows:
                        continue
                    # A shelf somebody plays shuffled is not one they are working
                    # through, and stocking it in order fills the copy with episodes
                    # the hat will almost never draw next: a shelf of a long series
                    # took ten slots with the first unwatched episodes in order while
                    # its own queue - the episodes actually coming up - got four.
                    # The round's queue is kept below, by casual_ahead, so this is
                    # not the shelf going uncopied; it is the same shelf read the way
                    # it is actually played.
                    round_here = (theirs.get("shuffles") or {}).get(at) or {}
                    if who in shuffles and (round_here.get("queue") or []):
                        continue
                    # the programmes the hat has already drawn from, when what they
                    # shuffle is the whole library rather than that shelf: the hat's
                    # own ten are kept below by casual_ahead, and that is all of
                    # those worth holding. The rest of the shelf still wants stocking
                    # - one draw used to skip every other programme on it.
                    stock = [s for s in shows if str(s) not in hat_shows]
                    if not stock:
                        continue
                    for key in self.first_unwatched(con, stock, episodes, watched,
                                                    self.name_of(who)):
                        listed.add(str(key))
                        want(key, "on a shelf", self.name_of(who))
            # favourites, everybody's and all of them: marked to be on both machines
            everyone = S.read_settings() or {}
            for who, one in (list((everyone.get("users") or {}).items())
                             + [("me", everyone)]):
                for key in ((one or {}).get("favorites") or []):
                    want(key, "a favourite", self.name_of(who))
            for who in shuffles:
                if casual <= 0:
                    continue          # "do not copy", said on the machine keeping them
                # An evening of casual watching is the one nobody picks a film for.
                # What was left part-way comes first, because carrying on with what
                # was actually on is the thing the other machine could not do at all:
                # a casual playing writes no row Continue watching reads, so nothing
                # ever put a half-watched shuffled episode over there.
                # Ten in all, which is the depth of the queue - not ten and then some.
                # Everything left part-way, and the whole hat behind it. Ten counted
                # across both lists meant three part-way places left seven slots for a
                # queue ten deep, so the copy never held the whole of it - and a hat
                # held in part is one a copy cannot draw from once those few are
                # watched. The ten is the hat's own depth; it is not a budget the
                # part-way places spend first.
                theirs, said_here = [], set()
                for key in self.casual_places(who):
                    key = str(key)
                    if key in said_here:
                        continue
                    said_here.add(key)
                    theirs.append((key, "left part-way in the shuffle"))
                # each round's hat is capped in casual_ahead; a cap here across rounds
                # kept only the newest round
                hat = [str(k) for k in self.casual_ahead(who)]
                for key in hat:
                    if key in said_here:
                        continue
                    said_here.add(key)
                    theirs.append((key, "the shuffle's next"))
                # what only a pack has must be on this disk before a copy can take it
                self.fetch_the_hat(who, hat)
                # How many, and how much of an evening, are the same two numbers
                # that govern everything else kept ahead: Episodes ahead, and Hours
                # ahead at most. A shelf of half-hour comedies and one of hour-long
                # drama are not the same number of files, and the settings already
                # say so.
                # Ten is the ceiling whatever the settings say, because ten is all
                # the shuffle knows: the queue is drawn ten deep and the eleventh
                # has not been decided. Asking for twenty would mean inventing ten.
                # The whole hat, however long it comes to. The hours setting cut it
                # to about seven of the ten - and the estimate runs high for a file
                # nobody has measured, so it cut it further - which left the copy
                # holding part of a queue that moves every time somebody presses next.
                # A hat the copy holds only part of is a hat it cannot draw from at
                # all once those few have been watched, measured this morning: of 153
                # episodes the shuffle could still draw, the copy had none.
                for key, why_it in theirs:
                    S.Handler.DRAWN.add(key)
                    want(key, why_it, self.name_of(who))
        return out, listed

    @staticmethod
    def casual_shows(con, mine):
        """The programmes a shuffle of the whole library is drawing from.

        A round over a shelf is filed under that shelf's number; a round over the
        whole library has no shelf and is filed under "casual". So a lookup by shelf
        found nothing for it, and the programme the hat was drawing from was stocked
        in season order behind its back - ten episodes in a row for a shelf that is
        never played in a row.

        What it has played counts with what it has drawn: a hat halfway through a
        programme is still the reason not to stock that programme in order.
        """
        one = ((mine or {}).get("shuffles") or {}).get("casual") or {}
        keys = [str(k) for k in ((one.get("queue") or []) + (one.get("played") or []))
                if S.is_episode(str(k))][:400]
        if not keys:
            return set()
        try:
            rows = con.execute(
                "SELECT DISTINCT item_id FROM episode WHERE id IN (%s)"
                % ",".join("?" * len(keys)), keys).fetchall()
        except Exception:
            return set()
        return set(str(r["item_id"]) for r in rows)

    @staticmethod
    def first_unwatched(con, keys, episodes, watched, who):
        """Each series' episodes cut to the first `episodes` unwatched, in order; other keys kept."""
        eps = [str(k) for k in keys if S.is_episode(k)]
        shows = {}
        if eps:
            try:
                rows = con.execute("SELECT id, item_id FROM episode WHERE id IN (%s)"
                                   % ",".join("?" * len(eps)), eps).fetchall()
                shows = {str(r["id"]): r["item_id"] for r in rows}
            except Exception:
                return list(keys)
        taken, out = {}, []
        for k in keys:
            show = shows.get(str(k))
            if show is None:
                out.append(k)
                continue
            if watched and watched(str(k), who):
                continue
            if taken.get(show, 0) >= episodes:
                continue
            taken[show] = taken.get(show, 0) + 1
            out.append(k)
        return out

    def episodes_after(self, con, plan, listed, hours, episodes, whole, watched):
        """The episodes following each series already wanted, in order.

        Walked in the order the series were asked for, so the one somebody is
        watching now is walked first and its next episode is second on the list
        rather than fortieth. Walking in the order a set happens to hold things in is
        what makes a queue of episodes read as random when each series is in order.
        """
        after = []
        said = set(k for k, _, _, _ in plan)
        # one window per programme and viewer: each place a series was reached from (screen,
        # watch log, part-way) walked its own, so Episodes ahead 10 queued 21 of one series
        ahead = {}
        for key, why, who, live in list(plan):
            # Nothing follows a shuffled draw. What comes after one of those is
            # whatever the shuffle draws next, which is already on this list - and
            # taking six episodes after each of ten draws made seventy files out of
            # ten, in season order, for a shelf whose whole point is that it is not.
            if (not key.startswith("e") or key in S.Handler.CASUAL_NOW
                    or key in S.Handler.DRAWN):
                continue
            # Every unwatched episode of a programme somebody put on a list is a
            # different question from enough for tonight: they mean to watch all of
            # it. Otherwise, forward until either limit is reached.
            enough = 9999 if (whole and key in listed) else episodes
            try:
                row = con.execute("SELECT item_id FROM episode WHERE id=?", (str(key),)).fetchone()
                show = row["item_id"] if row else key
            except Exception:
                show = key
            slot = ahead.setdefault((show, who), [0.0, 0])     # seconds covered, episodes ahead
            at, tried = key, 0
            while (slot[1] < enough and tried < 200
                   and (slot[0] < hours * 3600 or slot[1] < self.LEAST_AHEAD
                        or enough > episodes)):
                nxt = S.local().next_episode_key(con, at)
                if not nxt:
                    break
                tried += 1
                at = nxt
                if watched(nxt, who):
                    continue          # walked past, not copied and not counted
                if nxt not in said:
                    said.add(nxt)
                    # its own reason: inheriting the one it was walked from filed ten
                    # unstarted episodes as "part-way through" or "the one before it"
                    after.append([nxt, "kept ahead", who, live])
                    # asked for by nobody: it follows something somebody watched. Held
                    # to the age rule below, where a title somebody chose is not.
                    S.Handler.FETCHED_AHEAD.add(str(nxt))
                else:
                    S.Handler.also_for(nxt, "kept ahead", who)
                # already listed from another source still fills this viewer's window
                slot[1] += 1
                slot[0] += self.how_long(con, nxt)
        return after

    @staticmethod
    def in_series_order(con, plan):
        """Each programme's episodes in season and episode order, where it stands.

        Episodes reach the list from three places - a watchlist, somebody part-way
        through, and the walk ahead of both - and each added where it landed, so a
        series read as S01E01, S01E04, S01E02. A programme keeps the place of its
        earliest entry, so the list still reads in the order the main server wants things;
        it is only the inside of each series that is put right.
        """
        want = [k for k, _, _, _ in plan
                if S.is_episode(k)]
        if len(want) < 2:
            return plan
        facts = {}
        try:
            rows = con.execute(
                "SELECT id, item_id, season, number FROM episode WHERE id IN (%s)"
                % ",".join("?" * len(want)), [str(k) for k in want]).fetchall()
            for r in rows:
                facts[str(r["id"])] = (r["item_id"], r["season"] or 0,
                                          r["number"] or 0)
        except Exception:
            return plan                    # nothing known: leave it as it is
        at = {}
        for i, one in enumerate(plan):
            at[one[0]] = i
        done, out = set(), []
        for one in plan:
            key = one[0]
            if key in done:
                continue
            if key not in facts:
                done.add(key)
                out.append(one)
                continue
            show = facts[key][0]
            group = [x for x in plan if facts.get(x[0], (None,))[0] == show]
            # whatever is on a screen now stays at the front of its own series: it is
            # what somebody is watching, not the earliest episode they own
            group.sort(key=lambda x: (0 if x[3] else 1, facts[x[0]][1:]))
            for x in group:
                if x[0] not in done:
                    done.add(x[0])
                    out.append(x)
        return out

    @staticmethod
    def how_long(con, key):
        """Episode runtime in seconds, for filling the hours window.

        Falls back to the programme's average where this episode has no measured file.
        Counted as zero, an unmeasured episode never filled the window, so "four hours
        ahead" took ten episodes of a programme with 23 of its 55 unmeasured.
        """
        if not S.is_episode(key):
            return 0.0
        try:
            row = con.execute("SELECT duration FROM file WHERE episode_id=? "
                              "AND duration > 0 LIMIT 1", (str(key),)).fetchone()
            if row and row["duration"]:
                return float(row["duration"])
            row = con.execute(
                """SELECT AVG(f.duration) d FROM file f
                   JOIN episode e ON e.id = f.episode_id
                   WHERE f.duration > 0 AND e.item_id =
                         (SELECT item_id FROM episode WHERE id = ?)""",
                (str(key),)).fetchone()
            return float((row and row["d"]) or 0)
        except Exception:
            return 0.0
