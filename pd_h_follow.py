"""The other machine: what a cache server reads off this one, sync back, copies, and the shared reading of files.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

import pd_h
import pd_log

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class FollowParts(_Base):
    def follow_reads(self, path):
        """What a copy reads off this server about the library: skip rules, subtitle
        corrections, loudness, where credits begin. True when the path was one of them.

        Called for GET and POST alike. They were answered under POST only while the
        copy asks by GET (pd_follow.ask), so every one of them was a 404 nobody saw;
        and a POST whose body is never read is closed with a reset on Windows.
        """
        if path == "/follow/leads":
            # skip rules for a copy: keys are the same on both machines
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return True
            self.reply_json({"rules": (S.read_settings() or {}).get("skipStart") or {}})
            return True
        if path == "/follow/fits":
            # every subtitle correction worked out here, by the subtitle's own name, for
            # a cache that plays the same files from another folder
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return True
            held = (S.read_settings() or {}).get("subFit") or {}
            self.reply_json({"fits": {self.fit_key(k): v for k, v in held.items()}})
            return True
        if path in ("/follow/loudness", "/follow/stills", "/follow/stillpack", "/follow/reading",
                    "/follow/held", "/follow/placesnow"):
            # what one server has measured or made, for the other: the copy asks with its
            # key, the main server asks its copy as the house. Each holds files the other
            # has too and need not read them through again.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if (self.role != "owner" and not (invite and invite.get("follows"))) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return True
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {})
            if path == "/follow/loudness":
                self.reply_json({"loudness": S.loudness_rows()})
            elif path == "/follow/stills":
                self.reply_json({"stills": S.stills_rows()})
            elif path == "/follow/stillpack":
                self.send_stillpack(str((args.get("id") or [""])[0]))
            elif path == "/follow/placesnow":
                # the other server's viewer paused or stopped: the exchange now
                import pd_follow
                threading.Thread(target=pd_follow.places_now, args=(S.local(),), daemon=True).start()
                self.reply_json({"ok": True})
            elif path == "/follow/held":
                asked = [k for k in str((args.get("keys") or [""])[0]).split(",") if k][:200]
                import pd_torrents
                self.reply_json({"held": S.held_here(asked, S.local().file_for, pd_torrents.title_of)})
            else:
                self.reply_json(self.reading_of(str((args.get("key") or [""])[0])))
            return True
        if path == "/follow/endings":
            # where each title's story ends, measured here, for the copy: it has no
            # card to measure with and marks things watched by the same line
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return True
            import pd_credits
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self.reply_json({"endings": pd_credits.rows_since(since)})
            return True
        return False

    def send_stillpack(self, ident):
        """One file's finished timeline stills as a zip, for the other server."""
        import io as _io
        import zipfile
        place = os.path.join(S.stills_folder(), ident)
        if not re.fullmatch(r"[\w-]+", ident) or not os.path.exists(os.path.join(place, ".done")):
            self.send_error(404)
            return
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for n in sorted(os.listdir(place)):
                if n.endswith(".jpg"):
                    z.write(os.path.join(place, n), n)
        blob = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def follower_stopped(self):
        """Whether this caller is a machine the owner has stopped.

        Matched on the address the request arrives from rather than the one the
        machine claims, because a claim is the one thing a stopped machine would
        change.
        """
        try:
            blocked = S.local().lib.config().get("blockedCaches") or []
        except Exception:
            return False
        if not blocked:
            return False
        here = self.client_address[0]
        for one in blocked:
            host = urllib.parse.urlsplit(str(one)).hostname or str(one).strip()
            if host and host == here:
                return True
        return False

    def follows_here(self):
        """The invitation of a machine allowed to follow this one, or None."""
        invite = S.INVITES.check(self.bearer(), self.app_name())
        if not (invite and invite.get("follows")):
            return None
        return None if self.follower_stopped() else invite

    def sharing_with_copy(self, path):
        """Whether to read part of this file off the other machine, and how.

        Off unless somebody sets a share. Worth having when the two machines are
        comparable and worth nothing when they are not - a copy a tenth the speed
        adds a tenth - so it is a number somebody chooses, not a guess this makes.
        """
        try:
            share = int(S.local().lib.config().get("shareWithCopy") or 0)
        except (TypeError, ValueError):
            return None
        if share <= 0:
            return None
        one = self.standby_now()
        where = (one.get("where") or "").rstrip("/")
        if not where or not one.get("alive"):
            return None
        token = ""
        for row in S.INVITES.load():
            if row.get("follows") and row.get("token"):
                token = row["token"]
                break
        if not token:
            return None
        mark = self.quick_mark(path)
        if not mark:
            return None
        return (min(share, 90), where, token, os.path.basename(path), mark)

    #: The cache each following machine is being sent this moment, by address. One
    #: file at a time is what the cache asks for, but a fetch it gives up on leaves
    #: this side still sending - so two, three, four files went out at once, each
    #: taking its share of the line and the air, for a machine that had stopped
    #: listening to all but the last.
    SYNC_NOW = {}
    #: What each machine has carried for a viewer, so the screen can say so:
    #: {who: (bytes from here, bytes from the cache, when)}
    CARRIED = {}
    #: Above this share of the time spent getting blocks off the disk rather than
    #: handing them on, the disk is what is holding the film up. Below the lower
    #: figure it is comfortably ahead and the cache is worth nothing.
    DISK_BUSY = 0.35
    DISK_EASY = 0.15

    #: share of read time spent waiting on disk, and when it was measured
    DISK = {"busy": 0.0, "when": 0.0}

    def share_by_reads(self, now, most, disk, wrote):
        """How much of the reading the cache should do, from how this machine is faring.

        Measured here rather than asked of the viewer. What a screen has left to play
        was the old answer and it is not one: a browser keeps about two seconds in
        front of itself however well it is fed, so it read as running out for the
        whole of every film - and nothing ever sent the figure anyway.

        What does say something is time. Getting a block off this disk against handing
        it on: a disk keeping well ahead of the line is worth nothing to share, and one
        the line is waiting on is worth every block the cache will take.
        """
        both = disk + wrote
        if both <= 0:
            return now
        busy = disk / both
        # recorded for the sync ceiling: a copy reads the same disks, so this is an
        # earlier signal than a viewer's buffer running down
        S.Handler.DISK = {"busy": busy, "when": time.time()}
        if busy > S.Handler.DISK_BUSY:
            return min(most, now + 10)
        if busy < S.Handler.DISK_EASY:
            return max(0, now - 5)
        return now

    def block_from_copy(self, share, at, want):
        """One block off the other machine, or nothing if it is not quick about it.

        Nothing is retried and nothing is waited for: this is a way of spreading the
        reading, not a way of getting the film. A block that does not come is read
        off this disk instead, which is where it would have come from anyway.
        """
        _, where, token, name, mark = share
        url = ("%s/copy/read?name=%s&mark=%s&t=%s"
               % (where, urllib.parse.quote(name), urllib.parse.quote(mark),
                  urllib.parse.quote(token)))
        try:
            req = urllib.request.Request(
                url, headers={"Range": "bytes=%d-%d" % (at, at + want - 1),
                              "X-Palladium-App": "house"})
            with urllib.request.urlopen(req, timeout=4) as answer:
                got = answer.read(want)
            return got if got else None
        except Exception:
            return None

    def copy_holds(self, name, mark):
        """The file of that name in the cache folder, if it is the same file.

        The mark is the size and both ends: two rips never agree on it, and a copy
        that arrived half-written does not agree with itself. Without this a request
        for a name could be answered with a different film of the same name.
        """
        import pd_follow
        try:
            one = pd_follow.settings(S.local().lib.config())
        except Exception:
            return ""
        folder = (one.get("folder") or "").strip()
        safe = pd_follow.a_safe_name(name)
        if not folder or not safe or not pd_follow.a_sane_folder(folder):
            return ""
        try:
            here = pd_follow.copy_place(folder, safe)
        except Exception:
            return ""                 # where the packs keep it cannot be said now
        if not (pd_follow.inside(folder, here) or here != os.path.join(folder, safe)) or not os.path.exists(here):
            return ""
        if mark and self.quick_mark(here) != mark:
            return ""
        return here

    def house_doors(self):
        """Both addresses of the machine this one follows, if it follows one.

        Read from the library's settings, where the following is set up. It was read
        from this viewer's own settings file, which has never held it - so a machine
        that follows another answered that it followed nobody, and a page opened on
        it had no way back to the main server.
        """
        try:
            one = S.local().lib.config().get("follow") or {}
        except Exception:
            one = (S.read_settings() or {}).get("follow") or {}
        if not (one.get("on") and one.get("master")):
            return {}
        try:
            import pd_follow
            said = pd_follow.house_doors()
        except Exception:
            said = {}
        # what it was told, or failing that the address it was set up with
        if not (said.get("lan") or said.get("outside")):
            return {"lan": str(one.get("master") or ""), "outside": "", "name": "",
                    "id": ""}
        return said

    def sync_back_soon(self, hours, deck, episodes, casual, whole, keep_days):
        """Every ten minutes: films of packs at home on the copy server that it lacks and
        this server holds are handed to it; with Sync back on, too, the ones the copy rules
        want here are taken back and the rest let go."""
        if S.Handler.SYNC_BACK["busy"] or time.time() - S.Handler.SYNC_BACK["at"] < 600:
            return
        import pd_torrents
        S.Handler.SYNC_BACK.update(at=time.time(), busy=True, why="")
        try:
            self._sync_back_start(hours, deck, episodes, casual, whole, keep_days)
        except Exception:
            # no round started, so none will clear it: sync back stopped until a restart
            S.Handler.SYNC_BACK["busy"] = False
            raise

    def _sync_back_start(self, hours, deck, episodes, casual, whole, keep_days):
        """What is wanted worked out, and the round started."""
        import pd_torrents
        # what is wanted, as TMDB numbers: the same lists the copy is kept by, the days
        # kept after watching, and whatever was asked for from those packs
        con = S.local().lib.db()
        try:
            with self.PLANNING:
                plan, _ = self.wanted_keys(con, hours, deck, episodes, None, casual, whole)
            keys = {str(p[0]) for p in plan}
            playing_keys = {str(p[0]) for p in plan if len(p) > 3 and p[3]}
            for row in con.execute("SELECT DISTINCT key FROM watchlog WHERE updated > ?",
                                   (time.time() - keep_days * 86400,)):
                keys.add(str(row["key"]))
            keys |= pd_torrents.asked_on_copy()

            def tmdb_of(found):
                out = set()
                for k in found:
                    row = con.execute("SELECT tmdb_id FROM item WHERE id=?", (k,)).fetchone()
                    if row and row["tmdb_id"]:
                        out.add(int(row["tmdb_id"]))
                        continue
                    _, film = pd_torrents.by_key(k)
                    if film and film.get("tmdb"):
                        out.add(int(film["tmdb"]))
                return out
            wanted, playing = tmdb_of(keys), tmdb_of(playing_keys)
            S.Handler.SYNC_WANTED = set(wanted)
            S.Handler.SYNC_PLAYING = set(playing)
        finally:
            con.close()
        one = self.standby_now()
        where = (one.get("where") or "").rstrip("/")
        threading.Thread(target=S.Handler.sync_back_round,
                         args=(where, wanted, playing, bool(S.local().lib.config().get("syncBack"))),
                         daemon=True).start()

    @classmethod
    def sync_back_round(cls, where, wanted, playing, sync_on=True):
        """The slow half of sync back, off the request: asking the copy, handing it what it
        lacks, and with Sync back on fetching and letting go."""
        import pd_homes
        import pd_torrents
        try:
            if not where:
                cls.SYNC_BACK["why"] = "no copy server to sync with"
                return
            data = pd_torrents.load()
            films = [(p, f) for p in data["packs"] if pd_torrents.home_of(p["hash"]) in ("copy", "both")
                     for f in p.get("films") or []
                     if str(f.get("path") or "").lower().endswith((".mkv", ".mp4", ".avi", ".m4v"))]
            asked = {}

            def copy_said(pack, film):
                mark = (pack["hash"], int(film["index"]))
                if mark not in asked:
                    try:
                        req = urllib.request.Request(
                            "%s/follow/packwhole?hash=%s&index=%d" % (where, pack["hash"], mark[1]),
                            headers={"X-Palladium-App": "house"})
                        with urllib.request.urlopen(req, timeout=20) as answer:
                            asked[mark] = json.loads(answer.read() or b"{}")
                    except Exception:
                        asked[mark] = {}         # not answering is not whole
                return asked[mark]

            def copy_whole(pack, film):
                return bool(copy_said(pack, film).get("whole"))

            def copy_holds(pack, film):
                # all it needs to not be sent again; deleting here still asks for whole
                said = copy_said(pack, film)
                return bool(said.get("whole") or said.get("held"))

            # each pack's whole files read once a round: asked a film at a time, the
            # client listed all five thousand files of a pack for every one of them
            whole_here = {}

            def here(pack, film):
                if pack["hash"] not in whole_here:
                    whole_here[pack["hash"]] = set(pd_torrents.whole_files(pack["hash"]))
                return (int(film["index"]) in whole_here[pack["hash"]]
                        or bool(pd_torrents.elsewhere_path(pack["hash"], film["index"])))
            # films these packs lack that another torrent here holds: they go to the pack's
            # home like the pack's own, and their torrent with them (hand_over)
            elsewhere = []
            try:
                qbx = pd_torrents.QB(data["config"])
                infos = {t["hash"]: t for t in qbx.all_info()}
                listed_files = {}

                def files_of(h):
                    if h not in listed_files:
                        listed_files[h] = qbx.files(h)
                    return listed_files[h]
                packs_here = {p["hash"] for p, _ in films if pd_torrents.home_of(p["hash"]) == "copy"}
                elsewhere = pd_torrents.elsewhere_plan(packs_here, infos, files_of)
            except Exception as e:
                cls.SYNC_BACK["why"] = "torrents not read: %s" % str(e)[:120]
            pd_torrents.STATE["elsewhere"] = {(e["pack"], int(e["index"])): e["path"] for e in elsewhere}
            # the viewing rule, Sync back on or off: the copy rules read the other way. What the
            # list the copy fetches by wants and this server has no file for is copied here from
            # the copy, any pack of theirs, in the list's order - what the copy is showing on a
            # screen now first - by the same copy as the other way. Not a rule of its own
            # (owner, 2026-11-07), and worked out again every round, so a restart loses nothing
            if S.sync_way(S.local().lib.config()) in ("both", "fromCopy"):
                wanted_here = ([(str(k), "on a screen there") for _, k in list(S.peer_live())]
                               + list(cls.WANTED_HERE.get("keys") or []))
                picked = pd_homes.wanted_fetch(wanted_here, pd_torrents.copies_of, here, copy_whole)
                items = cls.fetch_items(where, picked)
                if items:
                    import pd_follow
                    threading.Thread(target=pd_follow.fetch_back, args=(items, cls.taken_back),
                                     daemon=True).start()
            # and what waited on a busy drive last round, tried again
            if cls.FROM_COPY:
                cls.take_from_the_copy()
            # the pack's home holds every file: what it lacks and this server has is moved
            # there, one file at a time by the copy's mover
            listed = [r.get("key") for r in pd_torrents.for_copy() if r.get("move")]
            # it ends by removing the torrent and its files from here: only with Sync back on,
            # which is the owner saying this machine may let go (2026-11-01)
            if sync_on:
                cls.hand_over(where, elsewhere, copy_whole)
            for pack, film in pd_homes.give(films, here, copy_holds, listed):
                if pd_torrents.give_to_copy(pack, film, "the pack's home is the copy server"):
                    pd_log.raw("%s home: handed to the copy server: %s%s" % (
                        time.strftime("%Y-%m-%d %H:%M:%S"), film.get("path"), chr(10)),
                        os.path.join(S.ROOT, "sync_back.log"))
            if not sync_on:
                return
            fetch, drop = pd_homes.decide(
                films, wanted, here,
                copy_whole, playing,
                {p["hash"] for p in data["packs"] if pd_torrents.home_of(p["hash"]) == "both"})
            cls.queue_from_copy(where, fetch, "sync back")
            def same_on_copy(pack, film):
                # asked again now, with the fingerprint: whole there, the same length,
                # the same bytes at both ends and in the middle
                def check(path, mark):
                    try:
                        req = urllib.request.Request(
                            "%s/follow/packwhole?hash=%s&index=%d&mark=1"
                            % (where, pack["hash"], int(film["index"])),
                            headers={"X-Palladium-App": "house"})
                        with urllib.request.urlopen(req, timeout=120) as answer:
                            said = json.loads(answer.read() or b"{}")
                    except Exception:
                        return False
                    return (said.get("whole") is True and said.get("mark") == mark
                            and int(said.get("size") or -1) == os.path.getsize(path))
                return check
            let_gone = []
            allowed = [str(f) for f in (S.local().lib.config().get("syncBackFolders") or []) if str(f).strip()]
            for pack, film in drop[:cls.LET_GO_PER_ROUND]:
                said = pd_torrents.let_go(pack["hash"], int(film["index"]), same_on_copy(pack, film),
                                          allowed)
                line = "%s sync back %s: %s | %s | %s%s" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), "let go" if said.get("ok") else "kept",
                    film.get("title"), said.get("path") or "",
                    ("%d bytes, %s" % (said.get("size") or 0, said.get("mark") or "")) if said.get("ok")
                    else said.get("why"), chr(10))
                pd_log.raw(line, os.path.join(S.ROOT, "sync_back.log"))
                if said.get("ok"):
                    let_gone.append(film)
            drop = [(None, f) for f in let_gone]
            cls.SYNC_BACK.update(fetch=[f.get("title") for _, f in fetch],
                                 drop=[f.get("title") for _, f in drop])
        except Exception as e:
            cls.SYNC_BACK["why"] = str(e)[:200]
        finally:
            cls.SYNC_BACK["busy"] = False

    @classmethod
    def queue_from_copy(cls, where, fetch, why):
        """Films of the copy's to fetch here, listed once each and the fetching started."""
        with cls.FROM_COPY_LOCK:
            known = {(str(x.get("hash")), S.index_of(x)) for x in cls.FROM_COPY}
            for pack, film in fetch:
                if (pack["hash"], int(film["index"])) not in known:
                    cls.FROM_COPY.append({"where": where, "hash": pack["hash"],
                                          "index": int(film["index"]), "name": film.get("path"),
                                          "size": film.get("size"), "title": film.get("title"),
                                          "syncBack": True, "why": why})
                    pd_log.raw("%s %s: fetching %s from the copy%s" % (
                        time.strftime("%Y-%m-%d %H:%M:%S"), why, film.get("title"), chr(10)),
                        os.path.join(S.ROOT, "sync_back.log"))
        if fetch:
            cls.take_from_the_copy()

    #: (pack, index) -> when it was copied here from the copy server: not offered again for a
    #: day, so a pack's long read is not started over every round while it runs
    TAKEN_BACK = {}

    @classmethod
    def fetch_items(cls, where, picked):
        """The copy items for pack files the copy server holds whole: its address for the file,
        the file's place in this server's pack, and the length and fingerprint the copy server
        reads off its own disk."""
        import pd_torrents
        items, now = [], time.time()
        for pack, film, why in picked:
            mark, at = str(pack.get("hash") or ""), int(film.get("index", -1))
            if not mark or at < 0 or now - cls.TAKEN_BACK.get((mark, at), 0) < 86400:
                continue
            into = pd_torrents.placed_path(mark, at, str(film.get("path") or ""))
            if not into:
                continue
            try:
                said = json.loads(S.peer_fetch("/follow/packwhole?hash=%s&index=%d&mark=1"
                                             % (urllib.parse.quote(mark), at), 30) or b"{}")
            except ValueError:
                said = {}
            if not said.get("whole") or not said.get("mark"):
                continue
            items.append({"name": os.path.basename(into), "into": into, "size": int(said.get("size") or 0),
                          "mark": str(said["mark"]), "markKind": "pack", "as": "house",
                          "url": "%s/follow/file?hash=%s&index=%d" % (where, urllib.parse.quote(mark), at),
                          "title": film.get("title") or film.get("name"), "why": why,
                          "hash": mark, "index": at})
        return items

    @classmethod
    def taken_back(cls, item, path):
        """A pack file copied here whole from the copy server: written down and read into its pack."""
        import pd_torrents
        cls.TAKEN_BACK[(item["hash"], int(item["index"]))] = time.time()
        pd_torrents.mark_done(item["hash"], item["index"], "copied from the copy server")
        # read into its pack only where this machine seeds it: a split pack the copy
        # server seeds is not read here (owner, 2026-11-08)
        if pd_torrents.seeds_pack_here(item["hash"]):
            pd_torrents.read_in(item["hash"], item["index"])
        pd_log.raw("%s took back from the copy: %s%s" % (time.strftime("%H:%M:%S"), item.get("title"), chr(10)))

    @staticmethod
    def watched_on_copy():
        """TMDB numbers of the films the copy server is sending a viewer now."""
        import pd_torrents
        out = set()
        con = S.local().lib.db()
        try:
            for _, key in list(S.peer_live()):
                row = con.execute("SELECT tmdb_id FROM item WHERE id=?", (key,)).fetchone()
                if row and row["tmdb_id"]:
                    out.add(int(row["tmdb_id"]))
                    continue
                _, film = pd_torrents.by_key(key)
                if film and film.get("tmdb"):
                    out.add(int(film["tmdb"]))
        finally:
            con.close()
        return out

    #: torrent -> when it was sent to the copy server, waiting for it to seed there
    HANDED = {}
    #: the most torrents whose files go from here in one round, once seeded there
    HAND_OVER_PER_ROUND = 5

    @classmethod
    def hand_over(cls, where, elsewhere, copy_whole):
        """A film a pack at home on the copy lacks here, held by a torrent of its own: once
        the copy holds the film whole in its pack, that torrent is sent there and put where
        the film is, read, and seeded; once the copy says it seeds it whole and the file
        there is this one (length and fingerprint), the torrent and its files go from here.
        One copy of each file, on the pack's home. A torrent whose file name does not end
        the pack's own path for it cannot share the file there and stays here."""
        import base64
        import pd_torrents
        qb = pd_torrents.QB(pd_torrents.load()["config"])
        gone = 0

        def ask(path, body=None, patience=60):
            req = urllib.request.Request(
                where + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={"X-Palladium-House": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=patience) as answer:
                return json.loads(answer.read() or b"{}")

        def say(line):
            pd_log.raw("%s hand over: %s%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), line, chr(10)),
                       os.path.join(S.ROOT, "sync_back.log"))
        for e in elsewhere:
            h = e["torrent"]
            if not e["fits"] or not copy_whole({"hash": e["pack"]}, {"index": e["index"]}):
                continue
            try:
                if h not in cls.HANDED:
                    t = qb.info(h) or {}
                    extras = []
                    for f in qb.files(h):
                        name = str(f.get("name") or "")
                        if name.replace("\\", "/") == e["file"] or int(f.get("size") or 0) > 5_000_000:
                            continue
                        mine = pd_torrents.inside(str(t.get("save_path") or ""), name)
                        if mine and os.path.isfile(mine):
                            with open(mine, "rb") as fh:
                                extras.append({"name": name, "data": base64.b64encode(fh.read()).decode()})
                    said = ask("/follow/taketorrent", {
                        "torrent": base64.b64encode(qb.export(h)).decode(), "hash": h,
                        "pack": e["pack"], "index": int(e["index"]), "file": e["file"],
                        "category": t.get("category") or "", "extras": extras})
                    if said.get("ok"):
                        cls.HANDED[h] = time.time()
                        say("sent to the copy server: %s" % e["name"])
                    else:
                        say("not taken there: %s: %s" % (e["name"], said.get("why")))
                    continue
                if gone >= cls.HAND_OVER_PER_ROUND:
                    continue
                there = ask("/follow/torrentwhole?hash=%s" % h)
                if not there.get("whole"):
                    continue
                mark = ask("/follow/packwhole?hash=%s&index=%d&mark=1" % (e["pack"], int(e["index"])), patience=120)
                path = e["path"]
                if not (mark.get("whole") and int(mark.get("size") or -1) == os.path.getsize(path)
                        and mark.get("mark") == pd_torrents.fingerprint(path)):
                    say("kept here, not the same file there: %s" % e["name"])
                    continue
                # never a file another torrent here still seeds
                others = [o for o in qb.all_info() if o.get("hash") != h and os.path.normcase(path).startswith(
                    os.path.normcase(os.path.normpath(str(o.get("save_path") or ""))) + os.sep)
                    and any(os.path.normcase(pd_torrents.inside(str(o.get("save_path") or ""), g.get("name") or ""))
                            == os.path.normcase(path) for g in qb.files(o["hash"]))]
                if others:
                    say("kept here, %s seeds it too: %s" % (others[0].get("name"), e["name"]))
                    continue
                qb.remove(h, files=True)
                cls.HANDED.pop(h, None)
                gone += 1
                say("seeded there, gone from here: %s | %s | %d bytes, %s"
                    % (e["name"], path, int(mark.get("size") or 0), mark.get("mark")))
            except Exception as ex:
                say("%s: %s" % (e["name"], str(ex)[:160]))

    @classmethod
    def home_moved(cls, info_hash, home):
        """A pack's home changed: the files move now, not at the next round. Home on the
        copy (or both) - what it lacks is listed for its mover; home here (or both) - what
        the copy holds and this server lacks is fetched back over the house network."""
        def work():
            import pd_torrents
            try:
                one = S.Handler.in_house().standby_now()
                where = (one.get("where") or "").rstrip("/")
                if not where:
                    return
                if home in ("copy", "both") and not cls.SYNC_BACK["busy"]:
                    cls.SYNC_BACK.update(at=time.time(), busy=True)
                    cls.sync_back_round(where, cls.SYNC_WANTED, cls.SYNC_PLAYING,
                                        bool(S.local().lib.config().get("syncBack")))
                if home in ("main", "both"):
                    req = urllib.request.Request(
                        "%s/follow/packwholes?hash=%s" % (where, urllib.parse.quote(info_hash)),
                        headers={"X-Palladium-App": "house"})
                    with urllib.request.urlopen(req, timeout=60) as answer:
                        there = set(json.loads(answer.read() or b"{}").get("whole") or [])
                    pack = next((p for p in pd_torrents.load()["packs"]
                                 if p.get("hash") == info_hash), None)
                    fetch = [f for f in (pack or {}).get("films") or []
                             if int(f.get("index", -1)) in there
                             and not pd_torrents.whole_file(info_hash, f["index"])]
                    with cls.FROM_COPY_LOCK:
                        known = {(str(x.get("hash")), S.index_of(x))
                                 for x in cls.FROM_COPY}
                        for film in fetch:
                            if (info_hash, int(film["index"])) not in known:
                                cls.FROM_COPY.append({
                                    "where": where, "hash": info_hash, "index": int(film["index"]),
                                    "name": film.get("path"), "size": film.get("size"),
                                    "title": film.get("title")})
                    if fetch:
                        cls.take_from_the_copy()
            except Exception as e:
                S.Handler.note_fault(None, "moving a pack to its home: %s" % e)
        threading.Thread(target=work, daemon=True).start()

    @classmethod
    def take_from_the_copy(cls):
        """Fetch back what the other machine downloaded while this one was away.

        Over the house network rather than off the internet a second time, into the
        place this pack expects, and then qBittorrent is asked to look again - so the
        film is seeded from here too instead of being fetched all over again. One at a
        time and never more than one thread: this is several gigabytes of somebody
        else's evening and nothing is waiting on it.
        """
        with cls.FROM_COPY_LOCK:
            if cls.FROM_COPY_BUSY[0]:
                return
            cls.FROM_COPY_BUSY[0] = True

        def work():
            import pd_torrents
            try:
                while True:
                    with cls.FROM_COPY_LOCK:
                        if not cls.FROM_COPY:
                            return
                        one = cls.FROM_COPY.pop(0)
                    where = str(one.get("where") or "").rstrip("/")
                    mark, at = str(one.get("hash") or ""), S.index_of(one)
                    if not where or not mark or at < 0:
                        continue
                    # already here and the right length: nothing to fetch. Looked for
                    # where the copy would be put, which for a pack this machine's
                    # client does not hold is the save folder - looked for only in the
                    # client, the same three files were fetched again every round
                    mine = pd_torrents.placed_path(mark, at, str(one.get("name") or ""))
                    size = int(one.get("size") or 0)
                    if mine and os.path.exists(mine) and size and                             os.path.getsize(mine) == size:
                        continue
                    url = ("%s/follow/file?hash=%s&index=%d"
                           % (where, urllib.parse.quote(mark), at))
                    try:
                        req = urllib.request.Request(
                            url, headers={"X-Palladium-App": "house"})
                        with urllib.request.urlopen(req, timeout=300) as answer:
                            got = pd_torrents.place_and_recheck(
                                mark, at, str(one.get("name") or ""), answer, size)
                    except urllib.error.HTTPError as e:
                        if e.code == 503:
                            # the copy's drive is another job's: kept, and asked again
                            # next round - not a fault, and not dropped (2026-11-07)
                            with cls.FROM_COPY_LOCK:
                                cls.FROM_COPY.append(one)
                            pd_log.raw("%s taking back %s waits: the copy's drive is busy%s"
                                       % (time.strftime("%H:%M:%S"), one.get("title"), chr(10)))
                            return
                        S.Handler.note_fault(
                            None, "taking back %s: %s" % (one.get("title"), e))
                        continue
                    except Exception as e:
                        S.Handler.note_fault(
                            None, "taking back %s: %s" % (one.get("title"), e))
                        continue
                    if not got:
                        # nothing written: said, and tried again next round, three times in
                        # all. Dropped without a word, a film cut part way was never fetched
                        # and its viewer read from one server (2026-11-07)
                        why = pd_torrents.PLACE_WHY.get("why") or "nothing written"
                        tries = int(one.get("tries") or 0) + 1
                        if tries < cls.FROM_COPY_TRIES:
                            with cls.FROM_COPY_LOCK:
                                cls.FROM_COPY.append(dict(one, tries=tries))
                            pd_log.raw("%s taking back %s failed (%s), try %d of %d: again next round%s"
                                       % (time.strftime("%H:%M:%S"), one.get("title"), why, tries,
                                          cls.FROM_COPY_TRIES, chr(10)))
                            return
                        S.Handler.note_fault(None, "taking back %s: %s, %d tries" % (one.get("title"), why, tries))
                        continue
                    if got:
                        pd_torrents.mark_done(mark, at, "taken back from the copy")
                        # done, not a fault: a line in the log
                        pd_log.raw("%s took back from the copy: %s%s"
                                % (time.strftime("%H:%M:%S"), one.get("title"), chr(10)))
            finally:
                with cls.FROM_COPY_LOCK:
                    cls.FROM_COPY_BUSY[0] = False

        threading.Thread(target=work, daemon=True).start()

    def follower_cost(self, who):
        """How many files, and how many gigabytes, keeping this person costs.

        Counted the way the cacheing counts it: what they are half-way through, and
        what is on their list. It is what makes one person's two ticks visibly dearer
        than another's.
        """
        import pd_localapi
        con = S.local().lib.db()
        seen, files, size = set(), 0, 0
        keys, listed, shuffled = [], [], []
        try:
            keys = [str(r["key"]) for r in con.execute(
                """SELECT key FROM progress WHERE who = ? AND position > 30
                   AND position < """ + pd_localapi.FINISHED_SQL + """
                     AND COALESCE(casual, 0) = 0
                   ORDER BY updated DESC LIMIT 40""",
                (who,))]
            listed = [str(k) for k in self.watchlist_of(who)]
            # and the shuffle, which is a shelf of its own and costs what it costs
            shuffled = [str(k) for k in self.casual_ahead(who)]
            # the file playback would pick, read on this one connection: asked through
            # file_for, each title opened the library twice more, and the People page
            # took a second for fourteen keys (2026-11-05)
            api = S.local()
            for key in keys + listed + shuffled:
                if key in seen:
                    continue
                seen.add(key)
                body = key[1:] if S.is_episode(key) else key
                if len(body) != 12 or any(c not in "0123456789abcdef" for c in body):
                    continue
                if key.startswith("e"):
                    rows = con.execute("SELECT * FROM file WHERE episode_id=?", (key,)).fetchall()
                else:
                    rows = con.execute("SELECT * FROM file WHERE item_id=? AND episode_id IS NULL",
                                       (key,)).fetchall()
                if not rows:
                    continue
                row = pd_localapi.best_first(api._whole(rows))[0]
                files += 1
                size += row["size"] or 0
        finally:
            con.close()
        return {"deck": len(keys), "list": len(listed), "casual": len(shuffled),
                "files": files, "gb": round(size / 1e9, 1)}

    def cached_for(self):
        """Whose Continue watching, whose watchlist and whose shuffle to keep.

        Nobody's by default but the owner's: a house with six guests would otherwise
        ask the other machine to hold the whole library.
        """
        mine = self.owner_caching()
        # The owner, by whichever name they go under - their own key if they have
        # taken one, and the placeholder a fresh install starts with if not. It read
        # "only if they are still called me", so the moment the owner took a key of
        # their own their deck, their list and their shuffle stopped being kept at
        # all: the one person certain to be watching, left out of their own cache.
        me = mine.get("token") or "me"
        keyed = set()
        decks = [me] if mine["cacheDeck"] else []
        lists = [me] if mine["cacheList"] else []
        shuffles = [me] if mine["cacheCasual"] else []
        lately = [me] if mine["cacheLately"] else []
        if me != "me":
            keyed.add(me)                 # counted once, not twice
        for row in S.INVITES.load():
            if row.get("token") in keyed:
                continue
            if row.get("cacheDeck"):
                decks.append(row["token"])
            if row.get("cacheList"):
                lists.append(row["token"])
            if row.get("cacheCasual"):
                shuffles.append(row["token"])
            # absent means yes, so nobody loses it to an upgrade
            if row.get("cacheLately", True):
                lately.append(row["token"])
        return decks, lists, shuffles, lately

    #: the following server, as it last announced itself. Kept here as well as in
    #: the settings file so that a machine announcing every minute is not a write
    #: every minute.
    STANDBY = {"where": "", "outside": "", "name": "", "when": 0}

    #: Every machine that has announced itself as following this one, by address:
    #: its name, its address from outside, and when it last spoke. More than one is
    #: allowed - a house may keep a copy in the cupboard and another at a friend's -
    #: and the freshest is the one clients are sent to.
    FOLLOWERS = {}
    #: written at most once a minute: an announcement arrives after every file
    FOLLOWERS_WRITTEN = [0.0]

    #: What the following server says it holds, by this library's keys. Kept in
    #: memory: it is a fact about another machine, true until it says otherwise, and
    #: worth nothing after a restart of either.
    COPIES = {"keys": [], "when": 0}

    #: The question the machine keeping copies last asked, so the queue on the page
    #: can be answered with the same one. The page used to ask for the night's list
    #: whatever the hour, so what it showed and what was actually being fetched were
    #: two different lists - and the one on screen was the longer of them.
    #: Why each file is in the queue, so the cache log can say who wanted it
    WHY_BY_KEY = {}
    # the last house cache list, {name: why}, to say what the next one changed
    LAST_HOLDING = {}

    #: What the other machine could not fetch, as it last said
    FOLLOWER_TROUBLE = []

    LAST_ASK = {"hours": 4.0, "deck": False, "episodes": 6, "casual": 0.0,
                "whole": False, "when": 0}

    #: Somebody going to bed before the hour this machine sleeps at. The other machine
    #: asks every minute; this is what it is told when it does.
    EARLY_UNTIL = 0.0

    @staticmethod
    def build_beside_us():
        """The installer this machine can hand to another, or nothing.

        Not every server has one. The installer does not pack a copy of itself - that
        would double every download - so this is the machine a build was made on, or
        one that kept the file it was installed from.
        """
        try:
            found = [f for f in os.listdir(S.STATIC)
                     if f.lower().startswith("palladium-setup")
                     and f.lower().endswith(".exe")]
        except OSError:
            return ""
        if not found:
            return ""
        found.sort()
        return os.path.join(S.STATIC, found[-1])

    #: the hash of that file, worked out once: it is 34 MB and it does not change
    BUILD_MARK = {"path": "", "sha256": "", "size": 0, "version": ""}

    @classmethod
    def build_on_offer(cls):
        """What this machine offers, with its hash. Empty when it has no installer."""
        path = cls.build_beside_us()
        if not path:
            return {}
        if cls.BUILD_MARK.get("path") != path:
            import hashlib
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as f:
                    for lump in iter(lambda: f.read(262144), b""):
                        digest.update(lump)
            except OSError:
                return {}
            name = os.path.basename(path)
            cls.BUILD_MARK = {"path": path, "sha256": digest.hexdigest(),
                              "size": os.path.getsize(path),
                              "version": name[len("Palladium-Setup-"):-len(".exe")]}
        return {k: v for k, v in cls.BUILD_MARK.items() if k != "path"}

    @staticmethod
    def copies_file():
        return os.path.join(S.ROOT, "copies.json")

    @classmethod
    def remember_copies(cls, keys):
        """What the other machine says it holds, kept across a restart.

        It was held in memory only, so restarting this server blanked the mark on
        every poster until the other one next spoke - a quarter of an hour of a
        library that looked like it had no copies at all.
        """
        cls.COPIES = {"keys": keys, "when": int(time.time())}
        try:
            with open(cls.copies_file(), "w", encoding="utf-8") as f:
                f.write(json.dumps(cls.COPIES))
        except OSError:
            pass

    @classmethod
    def followers_file(cls):
        return os.path.join(os.path.dirname(cls.copies_file()), "followers.json")

    @classmethod
    def remember_followers(cls, force=False):
        """Keep the record of machines that follow this one across a restart.

        Held in memory only, the list emptied on every restart - a machine that had
        been keeping copies for a month showed as though it had never been here.
        """
        if not force and time.time() - cls.FOLLOWERS_WRITTEN[0] < 60:
            return
        cls.FOLLOWERS_WRITTEN[0] = time.time()
        try:
            with open(cls.followers_file(), "w", encoding="utf-8") as f:
                f.write(json.dumps(cls.FOLLOWERS))
        except OSError:
            pass

    @classmethod
    def recall_followers(cls):
        try:
            with open(cls.followers_file(), encoding="utf-8") as f:
                said = json.loads(f.read())
            if isinstance(said, dict):
                cls.FOLLOWERS = {str(k): v for k, v in said.items()
                                 if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            pass

    def note_cache(self, **stats):
        """Write something down about the machine that is asking, if it is known."""
        here = self.client_address[0]
        where = ""
        for key, one in S.Handler.FOLLOWERS.items():
            host = urllib.parse.urlsplit(str(key)).hostname or str(key)
            if host == here:
                where = key
                break
        if not where:
            where = S.Handler.STANDBY.get("where") or ""
        if not where:
            return
        row = dict(S.Handler.FOLLOWERS.get(where) or {"where": where})
        row.update(stats)
        # and the key it came in with, so its own key can be shown against it and
        # taken away on its own
        was = self.bearer()
        if was:
            row["token"] = was
        row["when"] = int(time.time())
        S.Handler.FOLLOWERS[where] = row
        S.Handler.remember_followers()

    @classmethod
    def recall_copies(cls):
        """Read it back at startup. Old news is better than no news: the other
        machine confirms or corrects it within the minute."""
        try:
            with open(cls.copies_file(), encoding="utf-8") as f:
                said = json.loads(f.read())
            if isinstance(said.get("keys"), list):
                cls.COPIES = {"keys": [str(k) for k in said["keys"]],
                              "when": int(said.get("when") or 0)}
        except (OSError, ValueError, AttributeError):
            pass

    def remember_cache(self, where, name, outside="", build="", room=None):
        """Write down where the following server is, if it has moved."""
        moved = (where != S.Handler.STANDBY.get("where")
                 or name != S.Handler.STANDBY.get("name")
                 or outside != S.Handler.STANDBY.get("outside"))
        # and what it is running, which is worth seeing beside its name: two machines
        # on different builds speak slightly different languages to the same app
        # how full that machine is: it is the only one that can measure its own
        # disk, and "is there room for tonight" is the question this answers
        room = dict(room or {})
        S.Handler.STANDBY.update({"where": where, "outside": outside, "name": name,
                                "build": build, "room": room,
                                "when": int(time.time())})
        # merged, not replaced: what this machine holds and what was sent to it are
        # learned from other requests and would be wiped by every announcement
        was = dict(S.Handler.FOLLOWERS.get(where) or {})
        was.update({"where": where, "outside": outside, "name": name,
                    "build": build, "room": room, "when": int(time.time())})
        held = self.bearer()
        if held:
            was["token"] = held
            # and the key takes the machine's name, once the machine has said what it
            # is called. A key made before anybody knew which computer would use it is
            # called "a following server", and that is what the drawing and the list
            # of keys then called the machine itself.
            if name:
                try:
                    rows = S.INVITES.load()
                    for row in rows:
                        if (row.get("token") == held and row.get("follows")
                                and str(row.get("name") or "") in
                                ("", "a following server")):
                            row["name"] = name
                            S.INVITES.save(rows)
                            break
                except Exception:
                    pass
        S.Handler.FOLLOWERS[where] = was
        # same machine and key under an older address - it moved port, or to another
        # address (a cable in, off the Wi-Fi): one row, the live one, not dead ones beside it
        if held:
            for old in [k for k, v in S.Handler.FOLLOWERS.items()
                        if k != where and v.get("token") == held]:
                S.Handler.FOLLOWERS.pop(old, None)
        S.Handler.remember_followers()
        if not moved:
            return
        cfg = S.local().lib.config()
        cfg["cache"] = {"where": where, "outside": outside, "name": name,
                          "when": int(time.time())}
        S.local().lib.save_config(cfg)

    #: What the cache last said it was doing, and when we asked: (screens, when)
    COPY_BUSY = None

    @staticmethod
    def host_of(where):
        return urllib.parse.urlsplit(where).netloc or where

    def copy_is_busy(self):
        """Which screens the machine that keeps copies is serving, by address.

        Asked of it, because this machine cannot see it. A player reading part of a
        film off the cache talks to the cache directly and says nothing here, so the
        drawing called a machine carrying half a film "standing by".

        Kept for five seconds: the drawing is made again every two, and a round trip
        to the machine next to it takes milliseconds. At half a minute the picture was
        a different moment from the list of viewers printed under it - a film had been
        coming off both machines for twenty seconds before the line appeared.
        """
        now = time.time()
        had = S.Handler.COPY_BUSY
        if had and now - had[1] < 5:
            return had[0]
        S.Handler.COPY_BUSY = ([], now)
        one = self.standby_now()
        where = (one.get("where") or "").rstrip("/")
        if not where or not one.get("alive"):
            return []
        token = ""
        for row in S.INVITES.load():
            if row.get("follows") and row.get("token"):
                token = row["token"]
                break
        if not token:
            return []
        try:
            with urllib.request.urlopen(
                    "%s/watching?t=%s" % (where, urllib.parse.quote(token)),
                    timeout=3) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace"))
            rows = said.get("live") or []
            # who is reading off it and how fast, not merely how many: the drawing
            # runs a line from a machine to the screen it is feeding, and says what
            # is going down it. A count cannot say either.
            busy = [{"address": str((r or {}).get("address") or ""),
                     "mbit": round(float((r or {}).get("mbit") or 0), 1),
                     "title": str((r or {}).get("title") or "")}
                    for r in rows if (r or {}).get("how") != "syncing"]
        except Exception:
            busy = []
        S.Handler.COPY_BUSY = (busy, now)
        return busy

    #: what the followed server is serving, and when it was asked
    HOUSE_BUSY = ([], 0.0)

    def house_is_busy(self):
        """Screens the followed server is serving, by address.

        Mirror of copy_is_busy for a follower: the diagram drew a line only from
        this machine, so a film read off both showed the main server idle while it
        carried half. Asked with the follow key: a browser's key here is local and
        unknown to the main server.
        """
        now = time.time()
        had = S.Handler.HOUSE_BUSY
        if had and now - had[1] < 5:
            return had[0]
        S.Handler.HOUSE_BUSY = ([], now)
        busy = []
        try:
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            where = str(one.get("master") or "").rstrip("/")
            key = str(one.get("key") or "")
            if one.get("on") and where and key:
                with urllib.request.urlopen(
                        "%s/watching?t=%s" % (where, urllib.parse.quote(key)),
                        timeout=3) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace"))
                busy = [{"address": str((r or {}).get("address") or ""),
                         "mbit": round(float((r or {}).get("mbit") or 0), 1),
                         "title": str((r or {}).get("title") or "")}
                        for r in (said.get("live") or [])
                        if (r or {}).get("how") != "syncing"]
        except Exception:
            busy = []
        S.Handler.HOUSE_BUSY = (busy, now)
        return busy

    @staticmethod
    def in_the_queue(row, held):
        """Whether a row of what is worth copying is listed as being copied: there
        already, or going to be fetched. One that is only kept where it already is -
        watched lately - and is not there will never come, and stood as next."""
        return row.get("key") in held or not row.get("kept")

    def standby_now(self) -> dict:
        """The following server for a client to fall back on, or nothing.

        An address that has not announced itself for a day is not offered: the
        machine has been taken away, and sending viewers there wastes their time
        every time this server is slow.
        """
        one = dict(S.Handler.STANDBY)
        if not one.get("where"):
            one = dict((S.local().lib.config().get("standby") or {}))
        # What this machine is and where else it can be reached, and the machine it
        # follows if it follows one. Facts about the machine answering, not about any
        # cache of its own - and they were left out of the answer given by a
        # machine that has no cache, which is every copy. A page opened on the cache
        # was told nothing about anywhere else and had no way back to the main server.
        about_me = {
            "mine": {"lan": (("http://%s:%d" % (S.LAN_IP, S.PORT))
                             if S.LAN_IP and self.may_have_the_lan() else ""),
                     "outside": S.outside_url(),
                     "name": self.server_name()},
            "follows": self.house_doors(),
        }
        where, when = one.get("where") or "", int(one.get("when") or 0)
        if not where or (when and time.time() - when > 86400):
            return dict(about_me, where="", outside="", name="", seen=0)
        # a viewer on this network uses the first, one outside the second. Whether
        # it is awake is this machine's business to know: it is on the same network,
        # and a client that has to find out for itself only finds out too late.
        # Whichever of the two the asker can actually reach. Being allowed the
        # address on this network is one question and being able to use it is
        # another: somebody away from the main server was handed 192.168.x for the other
        # machine, asked it, and waited for an answer that could never come - so from
        # away the second machine had no version, no build, and no way to be reached
        # at all.
        # the address on this network, for the drawing of the house, to whoever may
        # have it - even from away, where it is not the one to connect to
        lan = where if self.may_have_the_lan() else ""
        if not self.at_home() or not self.may_have_the_lan():
            # the way in from outside is the one that works from where they are
            where = one.get("outside") or where
        return {"where": where, "outside": one.get("outside") or "", "lan": lan,
                "name": one.get("name") or "", "seen": when,
                "alive": time.time() - when < 180,
                "build": one.get("build") or "",
                "room": one.get("room") or {},
                # and this machine's own two addresses, handed over while a screen
                # can still reach it. A client away from home cannot ask a server it
                # cannot reach where else it lives, so it has to have been told.
                "mine": about_me["mine"],
                "follows": about_me["follows"],
                # This machine would rather the main server watched from the other one.
                # Game mode is the case: the card has been handed to something else,
                # and a film asked of it now is a film nobody enjoys. The cache holds
                # what the main server is in the middle of, so everyone goes there until
                # the game is over.
                "prefer": bool(self.game_mode().get("on")),
                # and a link to it that carries the key, so the other machine opens
                # rather than asking who this is. A cookie belongs to the machine that
                # set it, so arriving at the cache with one for the main server is arriving
                # with nothing - which is why the owner, who has every right to be
                # there, was shown the way in for a stranger.
                "link": self.copy_link(one),
                # the key itself, so a page that has picked an address of its own -
                # the one on the home network rather than the one from outside - can
                # put the two together
                "key": self.bearer() or ""}

    def copy_link(self, one):
        """The way in to the other machine, carrying this viewer's own key."""
        token = self.bearer()
        if not token:
            return ""
        where = (one.get("outside") or one.get("where") or "").rstrip("/")
        return (where + "/s/" + token) if where else ""

    def languages_kept(self, mine=None):
        """The subtitle languages worth copying: those of the people being kept for.

        A copy is for the people whose viewing it holds, and each of them reads in
        one language. Nobody else's is fetched - a house of six would otherwise send
        six subtitles for every film.
        """
        stored = S.read_settings() or {}

        def theirs(who):
            if who == "me":
                said = stored
            else:
                said = (stored.get("users") or {}).get(who) or {}
            code = (said.get("subLang") or "").lower()[:5]
            return "" if code == "off" else code

        def second(who):
            said = stored if who == "me" else (stored.get("users") or {}).get(who) or {}
            code = (said.get("subLang2") or "").lower()[:5]
            return "" if code == "off" else code

        if mine:
            return {theirs(mine) or "en"} | ({second(mine)} - {""})
        decks, lists, shuffles, lately = self.cached_for()
        who_all = set(decks + lists + shuffles + lately)
        # the second language too: a film with no English subtitle is exactly the one
        # whose Swedish file the other machine needs to have taken
        langs = {theirs(who) for who in who_all} | {second(who) for who in who_all}
        langs.discard("")
        return langs or {"en"}

    def sides_worth_copying(self, path, key, langs, part_id=None, every=False):
        """Subtitle files beside a video that the other machine should have too.

        The ones in the languages being kept for, and whichever was actually chosen
        for this title - a subtitle somebody picked is the one they will look for.

        For a film, [every] one of them: downloaded and already there alike. A film is
        watched once and the subtitle wanted on the night is not always the one the
        languages say, and a subtitle is a few kilobytes against a few gigabytes. What
        changes on this machine follows: each carries the size and both its ends, and
        the other machine fetches again whenever that no longer matches what it holds.
        """
        import pd_localapi as _la
        out = []
        try:
            beside = _la.sidecars(path)
        except Exception:
            return out
        picked = ""
        try:
            picked = os.path.basename(S.local().picked_subtitle(str(key)) or "")
        except Exception:
            picked = ""
        for n, side in enumerate(beside):
            name = os.path.basename(side.get("file") or "")
            if not name:
                continue
            if not every and side.get("lang") not in langs and name != picked:
                continue
            try:
                size = os.path.getsize(side["file"])
            except OSError:
                continue
            out.append({"key": key, "side": n, "part": part_id,
                        "name": name, "size": size,
                        "mark": self.quick_mark(side["file"]),
                        "title": name})
        return out

    @classmethod
    def sync_ceiling(cls, cfg, streaming, syncing, side="home"):
        """What a copy may take this second, in megabits.

        Nought from the settings means no ceiling and is left alone. A number means
        that number. Otherwise it is worked out: what the line has been seen to carry,
        less what the films are drawing, less a margin for the bursts they ask in.
        """
        # 0 means no ceiling chosen, not a ceiling of zero: returned as a number it
        # read downstream as unlimited, and a copy took 300 Mbit off the disks a film
        # was being read from.
        said = cfg.get("syncMbitWhileWatching")
        if said not in (None, "", 0, "0"):
            try:
                return float(said)
            except (TypeError, ValueError):
                pass
        if not cfg.get("syncAdaptive", True):
            return 20.0
        line = cls.link_seen(float(streaming) + float(syncing), side)
        # Flat out while nothing is wrong, in steps only when something is: a viewer
        # getting less than they were, or reads waiting on the disk. A ceiling worked
        # out from what the line had been seen to carry held a copy to a quarter of
        # what a new card could do with nobody short of anything.
        #
        # Down a tenth a second while something is wrong. Then, every PROBE_EVERY
        # seconds, no ceiling at all for PROBE_FOR to see how the streams take it: if
        # nobody runs short, the new ceiling is the best the copy reached, and at least
        # half again the old one; if somebody does, back down from where it was.
        now = time.time()
        st = cls.PACE.setdefault(side, {"mbit": 0.0, "when": 0.0, "probe": 0.0, "best": 0.0})
        if now - (st.get("when") or 0) > 30:
            st.update(mbit=0.0, probe=0.0, best=0.0)   # nobody has watched for a while
        cap, probe = float(st.get("mbit") or 0), float(st.get("probe") or 0)
        trouble = cls.a_film_is_struggling(cls.WATCHING_NOW) or cls.disk_reading_slow()
        st["when"] = now
        if cls.how_thin(side=side) == "panic":
            # a player stalled or nearly out: down to a tenth at once, not a tenth less
            base = cap if cap and not probe else max(float(syncing), cls.LEAST_MBIT)
            st.update(mbit=max(cls.LEAST_MBIT, base * cls.PANIC_TO), probe=0.0, best=0.0)
            return st["mbit"]
        if trouble:
            # a tenth down from what it has now: the ceiling, or what it was taking
            # while it had none
            base = cap if cap and not probe else max(float(syncing), cls.LEAST_MBIT)
            st.update(mbit=max(cls.LEAST_MBIT, base * (1 - cls.STEP)), probe=0.0, best=0.0)
            return st["mbit"]
        if not cap:
            return 0.0                    # nothing wrong, and no ceiling to climb from
        if probe and now < probe:
            st["best"] = max(float(st.get("best") or 0), float(syncing))
            return 0.0                    # testing: no ceiling for a few seconds
        if probe:
            # through the test with nobody short: the new ceiling
            cap = max(cap * (1 + cls.RISE), float(st.get("best") or 0))
            st.update(probe=0.0, best=0.0, tested=now)
            # free once within reach of the best this line has carried: a share of
            # that, not a fixed number, which an old link never reached
            if cap >= cls.link_seen(float(streaming) + float(syncing), side) * cls.FREE_AT:
                st.update(mbit=0.0)
                return 0.0
            st["mbit"] = cap
            return cap
        if cls.how_thin(side=side) == "ease":
            return cap                    # under forty seconds ahead: no testing now
        if now - float(st.get("tested") or 0) >= cls.PROBE_EVERY:
            st.update(probe=now + cls.PROBE_FOR, best=float(syncing), tested=now)
            return 0.0
        return cap

    def house_answers(self, path, body):
        """Put a question about somebody's shuffle to the machine that owns it.

        Only from a machine that follows another, and only while that machine can be
        reached: a copy answering for itself is a second evening, and two evenings is
        the thing this is here to prevent. Under the same key the person is using
        here, so the main server knows whose round it is - not this machine's own key,
        which would make every viewer look like the cache.

        Nothing is retried and nothing waits long. If the main server does not answer, this
        machine answers from what it has, which is what it is for.
        """
        import pd_follow
        try:
            one = pd_follow.settings(S.local().lib.config())
        except Exception:
            return None
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None                   # this machine is the main server
        token = self.bearer()
        if not token:
            return None
        url = (one["master"].rstrip("/") + path + "?t="
               + urllib.parse.quote(token))
        # the main server just failed to answer: this machine answers for a minute
        # rather than making every press wait six seconds for it again
        if time.time() - S.Handler.MAIN_AWAY["at"] < 60:
            return None
        try:
            asked = urllib.request.Request(
                url, data=json.dumps(body or {}).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "X-Palladium-App": "follower"})
            with urllib.request.urlopen(asked, timeout=6) as answer:
                return json.loads(answer.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError:
            return None                   # it answered, just not with a round
        except Exception:
            S.Handler.MAIN_AWAY["at"] = time.time()
            return None                   # the main server is off: answer for ourselves

    #: when the main server last failed to answer a forwarded question
    MAIN_AWAY = {"at": 0.0}

    def house_gets(self, path, query=""):
        """The main server's answer to a GET, from a machine that follows it, under the
        viewer's own key; None when this machine is the main server or it is away."""
        import pd_follow
        try:
            one = pd_follow.settings(S.local().lib.config())
        except Exception:
            return None
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        token = self.bearer()
        if not token or time.time() - S.Handler.MAIN_AWAY["at"] < 60:
            return None
        kept = "&".join(part for part in (query or "").split("&")
                        if part and not part.startswith("t="))
        url = (one["master"].rstrip("/") + path + "?t=" + urllib.parse.quote(token)
               + ("&" + kept if kept else ""))
        try:
            asked = urllib.request.Request(url, headers={"X-Palladium-App": "follower",
                                                         "Accept": "application/json"})
            with urllib.request.urlopen(asked, timeout=6) as answer:
                return json.loads(answer.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError:
            return None
        except Exception:
            S.Handler.MAIN_AWAY["at"] = time.time()
            return None

    #: One plan at a time. A plan empties Handler.CASUAL_NOW, DRAWN and FETCHED_AHEAD as
    #: it starts and reads them as it goes; a second ask arriving meanwhile emptied them
    #: under the first, which then kept episodes ahead of a shuffle's draw for one
    #: answer - copied, then deleted at the next (2026-11-13, two asks in one second).
    PLANNING = threading.RLock()

    def worth_copying(self, *args, **kwargs):
        """The files a machine keeping copies should have - one plan at a time."""
        with self.PLANNING:
            return self._worth_copying(*args, **kwargs)

    def _worth_copying(self, hours=4.0, deck=False, episodes=6, mine=None,
                       casual=0.0, whole=False, only="", kinds=None, keep_days=S.KEEP_DAYS):
        """The files a machine keeping copies should have, in the order to fetch them.

        Three steps, and each is somewhere else: what the main server wants and why, the
        episodes that follow it, and the rows a copy can act on. The order is the
        order the sources were asked in, with one exception - whatever is on a screen
        this minute leads, because it is wanted in twenty minutes and nothing else on
        the list is. A queue with several kinds of priority in it is a queue nobody
        can predict, and predicting it is the whole point of having one.
        """
        con = S.local().lib.db()
        S.local().who = mine or self.viewer()
        langs = self.languages_kept(mine)
        try:
            # watched by the person a title is kept for, not by whoever asked: the
            # owner having seen a film is no reason to drop it from a guest's list
            asking = S.local().who
            tokens = {}
            for row in S.INVITES.load():
                if row.get("token"):
                    tokens.setdefault(self.name_of(row["token"]), row["token"])

            def watched(key, who=None):
                S.local().who = tokens.get(who, asking) if who else asking
                try:
                    return S.local()._watched(con, key)
                except Exception:
                    return False
                finally:
                    S.local().who = asking

            now_named = time.time()
            missing = []
            wrote_ahead = set()
            S.Handler.FETCHED_AHEAD = set()
            plan, listed = self.wanted_keys(con, hours, deck, episodes, mine,
                                            casual, whole, watched, kinds)
            if not mine:
                plan += self.episodes_after(con, plan, listed, hours, episodes,
                                            whole, watched)
            # and what was watched within the days kept, shuffled or chosen, whatever
            # else still names it: a finished film leaves every other list, and the
            # copy deleted it the same evening
            keep_only = set()
            if keep_days > 0:
                named = {str(p[0]) for p in plan}
                decks_, lists_, shuffles_, lately_ = self.cached_for()
                whom = list(dict.fromkeys(decks_ + lists_ + shuffles_ + lately_))
                if whom:
                    for row in con.execute(
                            "SELECT key, who, MAX(updated) FROM watchlog WHERE updated > ? "
                            "AND who IN (%s) GROUP BY key, who ORDER BY MAX(updated) DESC "
                            "LIMIT 200" % ",".join("?" * len(whom)),
                            [now_named - keep_days * 86400] + whom):
                        key = str(row["key"])
                        if key in named:
                            continue
                        named.add(key)
                        keep_only.add(key)     # held if it is there, never fetched for this
                        plan.append((key, "watched lately", self.name_of(row["who"]), False))
            plan = self.in_series_order(con, plan)
            want, sided = [], set()
            for key, why, who, live in plan:
                # A watchlist is kept whole, watched or not: it is what somebody asked
                # for. So is whatever is on a screen this minute - a film passes the
                # watched mark with minutes still to run, and dropping it there had the
                # copy delete the file under somebody who was still watching it. The
                # one machine that could have carried on was the one being turned off.
                # Watched, and kept on the copy for the days set there after it was
                # last watched: held, not fetched - a copy that has it keeps it.
                kept = str(key) in keep_only
                if (why not in ("on their watchlist", "a favourite", "on a screen now",
                                "the one before it")
                        and watched(key, who)):
                    last = con.execute("SELECT MAX(updated) FROM watchlog WHERE key=?",
                                       (str(key),)).fetchone()[0]
                    if not (keep_days > 0 and last
                            and now_named - float(last) < keep_days * 86400):
                        continue
                    kept = True
                # Fetched before anybody asked - the next episodes of something
                # watched, the shuffle's next draws - and still not watched a month
                # later. Nobody is going to: it has been on the disk for thirty days
                # waiting. What somebody chose by name is not held to this.
                if (str(key) in S.Handler.FETCHED_AHEAD
                        or why == "the shuffle's next"):
                    first = S.read_ahead().get(str(key))
                    if first is not None and now_named - float(first) > S.AHEAD_DAYS * 86400:
                        # played since it was first named: a new wait starts. A shuffle's
                        # second round draws what the first played, all first named a
                        # month back, and every draw was dropped (2026-11-14)
                        last = con.execute("SELECT MAX(updated) FROM watchlog WHERE key=?",
                                           (str(key),)).fetchone()[0]
                        if last and float(last) > float(first):
                            first = None
                    if first is None:
                        S.AHEAD_SINCE[str(key)] = now_named
                        wrote_ahead.add(str(key))
                    elif now_named - float(first) > S.AHEAD_DAYS * 86400:
                        continue
                got = self.rows_for(con, key, why, who, live, langs, sided)
                # and every other version of it played lately: two versions of one
                # film watched are two files wanted, so either can be split
                listed_names = {g.get("name") for g in got}
                for other in S.played_files(key):
                    if os.path.basename(other) in listed_names:
                        continue
                    more = self.rows_for(con, key, why, who, live, langs, sided,
                                         path=other)
                    listed_names.update(g.get("name") for g in more)
                    got += more
                if kept:
                    for row in got:
                        row["kept"] = True
                if got and S.Handler.ALSO.get(str(key)):
                    # the file row only; its subtitles ride along
                    got[0]["also"] = [{"why": y, "who": w}
                                      for y, w in S.Handler.ALSO[str(key)]]
                want += got
                # wanted and not here: the same rules read the other way, for the copy
                # server to send here (sync is the same both ways, 2026-11-07)
                if not got and not kept:
                    missing.append((str(key), why))
            # and the book written back when it gained anything, with what has long
            # since fallen off the list dropped from it: a season watched last winter
            # is not worth remembering the first sight of for ever
            if wrote_ahead:
                for old_key in [k for k, when in list(S.AHEAD_SINCE.items())
                                if now_named - float(when)
                                > S.AHEAD_DAYS * 86400 * 2]:
                    S.AHEAD_SINCE.pop(old_key, None)
                S.write_ahead()
            # kept from the fuller asking: a cache asks a light list every minute while
            # copying, and that one leaves the watchlists out
            had = S.Handler.WANTED_HERE
            if (not mine and not only and kinds is None
                    and (deck or not had.get("deck") or now_named - float(had.get("when") or 0) > 900)):
                S.Handler.WANTED_HERE = {"keys": missing, "deck": bool(deck), "when": now_named}
        finally:
            con.close()

        # A cache kept for one person holds what that person is watching. The machine
        # in somebody's own room filled up with the rest of the main server's evening.
        if only:
            wanted_by = only.strip().lower()
            want = [w for w in want
                    if str(w.get("who") or "").strip().lower() == wanted_by
                    or self.name_of(w.get("who")).strip().lower() == wanted_by]

        # what each person may have copied to the other machine in a week
        want = self.within_weekly_sync(want)

        # One thing lifts anything above the list: a screen that is on this minute,
        # because what it needs is needed in twenty minutes. Everything else keeps the
        # place the sources gave it. Moving a title to the front by hand used to be a
        # second kind of priority, and two kinds of priority make a queue nobody can
        # predict - which is the whole of what a queue is for.
        want.sort(key=lambda w: 0 if w.get("hot") else 1)

        # Why each of these is wanted, where the serving side can find it: by the time
        # a file is being sent the reason is three functions away, and a log of what
        # moved says nothing about who wanted it.
        # The first reason for a file, not the last. One episode is often wanted by
        # two people at once - somebody's shelf and somebody else's shuffle - and
        # writing each in turn left whoever came last owning it: a guest's shuffle
        # picks were filed in the copy log under the owner, who had only ever
        # matched them with a shelf rule. The list is already sorted with what is
        # wanted soonest first, so the first entry is the reason worth keeping.
        fresh = {}
        for w in want:
            mark = str(w.get("key") or "")
            if mark:
                fresh.setdefault(mark, (w.get("why") or "", w.get("who") or ""))
        # what this pass says wins, and a file still going out under the last pass
        # keeps the reason it started with
        merged = dict(S.Handler.WHY_BY_KEY)
        merged.update(fresh)
        S.Handler.WHY_BY_KEY = fresh if len(merged) > 4000 else merged
        return want

    def weekly_limits(self, name):
        """Gigabytes a week per person, by the name the cache list files them under."""
        caps = {}
        stored = S.read_settings() or {}
        for row in S.INVITES.load():
            try:
                gb = float(row.get(name) or 0)
            except (TypeError, ValueError):
                gb = 0.0
            if gb > 0 and row.get("token"):
                caps[self.name_of(row["token"]).strip().lower()] = gb
        try:
            mine = float(stored.get(name) or 0)
        except (TypeError, ValueError):
            mine = 0.0
        if mine > 0 and not stored.get("ownerIs"):
            caps[self.name_of("me").strip().lower()] = mine
        return caps

    def within_weekly_sync(self, want):
        """The cache list, less what would take a person past their week's gigabytes.

        What is on a screen now is taken regardless, and so is what the other machine
        already holds: neither costs anything more.
        """
        caps = self.weekly_limits("syncGbWeek")
        if not caps:
            return want
        import pd_traffic
        since = time.time() - 7 * 86400
        spent = {}
        for row in pd_traffic.copies(100000):
            if int(row.get("when") or 0) < since:
                continue
            who = str(row.get("for") or "").strip().lower()
            spent[who] = spent.get(who, 0.0) + float(row.get("gb") or 0)
        held = set(S.Handler.COPIES.get("keys") or [])
        left = {who: cap - spent.get(who, 0.0) for who, cap in caps.items()}
        out, dropped = [], set()
        for w in want:
            who = str(w.get("who") or "").strip().lower()
            key = str(w.get("key") or "")
            if who not in left or w.get("hot") or key in held:
                out.append(w)
                continue
            if w.get("side") is not None:
                if key not in dropped:
                    out.append(w)
                continue
            gb = float(w.get("size") or 0) / 1e9
            if gb > left[who]:
                dropped.add(key)
                continue
            left[who] -= gb
            out.append(w)
        return out

    def rows_for(self, con, key, why, who, live, langs, sided, mi=0, path=None):
        """One wanted title as the rows a copy can act on: the file, then its subtitles.

        Nothing decides anything here. It is the film, what is inside it, what it is
        called and what sits beside it - everything the other machine would otherwise
        have to open the file or reach the internet to find out.
        """
        # a pack copy's key as the title's own, the key it is copied under
        import pd_torrents
        key = pd_torrents.title_of(key)
        if not path:
            found = S.local().file_for(key, mi) or {}
            path = found.get("file")
        if not path or not os.path.exists(path):
            return []
        # not while it is still downloading: a copy taken now is a broken file over
        # there, with the right name and size and half its picture
        try:
            import pd_torrents
            if pd_torrents.unfinished(path):
                return []
        except Exception:
            pass
        row = con.execute(
            """SELECT id, size, duration, container, vcodec, acodec, width,
                      height, channels, bitrate FROM file WHERE path=?""",
            (path,)).fetchone()
        if not row:
            return []
        title, subtitle, _ = S.local().now_playing_fields(key)
        out = [{
            "key": key, "part": row["id"], "art": self.art_of(key), "hot": live,
            "why": why, "who": who,
            # keep it where it is, but do not go and get it
            "casual": str(key) in S.Handler.CASUAL_NOW,
            "name": os.path.basename(path),
            "size": row["size"] or os.path.getsize(path),
            # what the other machine should find when it has it
            "mark": self.quick_mark(path),
            # and what is inside it, so a copy does not read as an unknown container -
            # which a player answers by asking for a transcode of a file it could
            # have played as it stands
            "facts": {k: row[k] for k in
                      ("duration", "container", "vcodec", "acodec",
                       "width", "height", "channels", "bitrate")},
            # Series and numbering first: a queue is read down a column of series,
            # not of episode names.
            "title": (" - ".join(
                x for x in ((subtitle, title) if str(key).startswith("e")
                            else (title, subtitle)) if x).replace("  ", " ")),
        }]
        # and the subtitles beside it, in the languages the people this is kept for
        # read. A film on the other machine with no subtitle is a film half the main server
        # cannot watch, and a subtitle is a few kilobytes.
        # a film takes all of its subtitles; an episode takes the languages being
        # kept for, because a series is hundreds of files and a film is one
        for side in self.sides_worth_copying(path, key, langs, row["id"],
                                             every=not str(key).startswith("e")):
            if side["name"] in sided:
                continue
            sided.add(side["name"])
            out.append(dict(side, hot=live))
        # a music video, said as one: the other machine keeps it in a folder of that
        # name, which is how its library tells it from a film
        import pd_library
        if pd_library.is_music_video(path):
            for one in out:
                one["music"] = True
        return out
