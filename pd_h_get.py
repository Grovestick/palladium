"""The GET routes: every path a request can ask for, in the order they are tried. Handler._do_GET walks them.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

import pd_h
import pd_log
import pd_notify

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class GetRoutes(_Base):
    def _get_part_01(self, path):
        """GET routes, part 1 of 16:
        /, /index.html, /receiver, /security/knocks, /invites, /nowplaying, /subs/side,
        /local/subs/side, /subs/plan, /subs/sync, and 2 more
        """
        path = self.path.split("?")[0]
        # apps up to 0.19.5 ask /server (the installer, refused to guests) to come home from the cache
        if (path == "/server" and (self.headers.get("X-Palladium-App") or "").startswith("android ")
                and self.who("/where") is not None):
            self.reply_json({"ok": True, "name": self.server_name()})
            return
        # the invite link carries its token in the path rather than the query, so the
        # landing page has to answer before the guard could possibly recognise anyone
        m = re.match(r"^/s/([A-Za-z0-9_-]{8,})$", path)
        if m:
            self.landing(m.group(1))
            return
        # A page opened with a token in the address becomes a session, the way the
        # invitation page does it. This is how the home-screen app on an iPhone gets
        # in: it starts at /?t=<token> in a cookie jar of its own, and without this
        # everything the page went on to ask for would be refused.
        if path in ("/", "/index.html") and "t=" in (self.path.split("?", 1) + [""])[1]:
            token = self.bearer()
            if token and S.INVITES.check(token):
                self.send_response(302)
                self.send_header("Location", path)
                self.send_header("Set-Cookie", self.cookie_for(token))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
        # the same code, exchanged for the invitation it stands for. A television
        # cannot reasonably be made to type a token, so it types five characters and
        # asks what they mean.
        m = re.match(r"^/i/([A-Za-z0-9]{5})/setup$", path)
        if m:
            if self.codes_shut():
                self.send_error(429, "too many tries; wait an hour")
                return
            invite = S.INVITES.by_code(m.group(1))
            if not invite:
                self.wrong_code(path)
                self.send_error(404, "no such code")
                return
            self.reply_json({"token": invite["token"], "name": invite["name"],
                             "serverId": self.server_id()})
            return
        # the app, fetched by a code short enough to type with a remote. It is the one
        # thing this address does: no library, no streams, only the file.
        m = re.match(r"^/i/([A-Za-z0-9]{5})/open$", path)
        if m:
            # the same code that fetches the app also opens the library, for somebody
            # typing on a television or reading a code off a screen. Counted like the
            # setup path: this one had no limit, and a hit here is a session.
            if self.codes_shut():
                self.send_error(429, "too many tries; wait an hour")
                return
            invite = S.INVITES.by_code(m.group(1))
            if not invite:
                self.wrong_code(path)
                self.locked_page()
                return
            self.landing(invite["token"])
            return
        m = re.match(r"^/i/([A-Za-z0-9]{5})(?:\.apk)?$", path)
        if m:
            if self.codes_shut():
                self.send_error(429, "too many tries; wait an hour")
                return
            invite = S.INVITES.by_code(m.group(1))
            if not invite:
                self.wrong_code(path)
                self.send_error(404, "no such code")
                return
            self.role = "guest"
            self.guest_name = invite["name"]
            self.send_app(who=invite["name"])
            return
        self.role = self.who(path)
        if self.role == "guest" and self.wants_a_page(path):
            # count the year from this visit, not from the day the link was opened:
            # somebody who watches every week should never be asked for it again
            for bit in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = bit.strip().partition("=")
                if k == "pal" and v:
                    self.renew = v
        if self.role is None and self.wants_a_page(path):
            # an invitation that has expired, or a cookie that is gone: say what to do
            # rather than refusing in the abstract
            self.refused(path, "no invitation - shown the door")
            self.locked_page()
            return
        if self.role is None:
            self.refused(path, "not a path a guest may open")
            self.send_error(403, "not allowed")
            return
        if path == "/receiver":
            # the receiver in the house: whether it answers, and what it is doing
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_receiver
            said = pd_receiver.status(S.read_settings() or {})
            # what the set device last said it was, and when
            at = said["settings"].get("device")
            seen = [r for r in self.SEEN.values() if r.get("where") == at]
            if seen:
                last = max(seen, key=lambda r: r.get("when") or 0)
                said["deviceSeen"] = {"name": last.get("name") or "", "app": last.get("said"),
                                      "ago": int(time.time() - (last.get("when") or 0))}
            self.reply_json(said)
            return
        if path == "/security/knocks":
            # strangers refused in the last day, by address, and whether codes are shut
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            now = time.time()
            self.reply_json({
                "knocks": self.knocks(24),
                "wrongCodes": len([t for t in S.Handler.CODE_MISSES if now - t < 3600]),
                "codesMost": S.Handler.CODES_MOST,
                "codesShutFor": max(0, int(3600 - (now - S.Handler.CODES_SHUT[0])))})
            return
        if path == "/invites":
            self._get_part_invites()
            return
        m = re.match(r"^(?:/local)?/live/(lv[0-9a-f]{12})/(index\.m3u8|seg\d{6}\.ts|stop)$", path)
        if m:
            # a live channel, copied into HLS pieces here: started on its playlist,
            # and asked for with /local in front by the web page, which builds every
            # address of the library that way
            # kept going while anybody asks, stopped a minute after
            import pd_live
            key, part = m.group(1), m.group(2)
            if part == "stop":
                # a player changing channel, or closed: the connection is free at once
                pd_live.stop(key)
                self.reply_json({"stopped": key})
                return
            if part == "index.m3u8":
                # a plugin's channels may be limited to so many at once
                if not pd_live.room_for(key):
                    self.send_error(429, "every connection of the playlist is in use")
                    return
                where = pd_live.start(key, S.ffmpeg_now())
                if not where:
                    self.send_error(404, "no such channel")
                    return
                listed = os.path.join(where, "index.m3u8")
                # the first piece within 15 s, and then a cushion before the answer
                began, first = time.time(), 0.0
                while True:
                    text = (open(listed, encoding="utf-8").read()
                            if os.path.exists(listed) else "")
                    pieces = text.count(".ts")
                    if pieces and not first:
                        first = time.time()
                    if pd_live.cushioned(pd_live.seconds_in(text),
                                         time.time() - first if first else 0):
                        break
                    if not first and time.time() - began > 15:
                        pd_live.forget_source(key)
                        pd_live.stop(key)
                        self.send_error(504, "the channel did not start")
                        return
                    time.sleep(0.25)
                pd_live.played(key)
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                body = pd_live.playlist(open(listed, encoding="utf-8").read(), query).encode("utf-8")
                ctype = "application/vnd.apple.mpegurl"
            else:
                pd_live.touch(key)
                pd_live.watched(key, 4)
                piece = os.path.join(pd_live._dir(key), part)
                if not os.path.exists(piece):
                    self.send_error(404, "gone")
                    return
                with open(piece, "rb") as f:
                    body = f.read()
                ctype = "video/mp2t"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # the channel's frames a second, for a television to match its refresh rate
            if part == "index.m3u8" and pd_live.rate(key):
                self.send_header("X-Palladium-Fps", "%g" % pd_live.rate(key))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/nowplaying":
            # the AV panel polls this while its Media app is open: plain JSON, no auth
            # beyond being on this network, and an answer well inside its 2.5s poll
            self.reply_json(S.local().now_playing())
            return
        # clients resolve a Part key against the library's own base, so it arrives
        # with /local in front of it; both spellings mean the same file
        if path in ("/subs/side", "/local/subs/side"):
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            self.serve_sidecar(S.local().part((args.get("file") or ["0"])[0]),
                               int((args.get("n") or ["0"])[0]),
                               (args.get("shift") or ["0"])[0])
            return

        if path == "/subs/plan":
            # what is already known about this subtitle's timing, without measuring
            # anything: a player asks on the way in so it can say what is in force
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            one = lambda name, fallback="": (args.get(name) or [fallback])[0]
            found = S.local().file_for(one("key"), int(one("mi", "0") or 0)) or {}
            parts = []
            if found.get("file"):
                parts = self.sub_fit(self.fit_name(found["file"],
                                                   int(one("index", "0") or 0))) or []
            self.reply_json({
                "parts": parts,
                "kind": ("parts" if len(parts) > 1
                         else "drift" if parts and abs(float(parts[0][1]) - 1.0) > 1e-9
                         else "static" if parts else "none")})
            return

        if path == "/subs/sync":
            # Work out how far a subtitle is out and, if it is sure, write it down for
            # everybody. The film's own sound is the reference: nothing is fetched, and
            # no subtitle is trusted to be what it claims.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            one = lambda name, fallback="": (args.get(name) or [fallback])[0]
            self.reply_json(self.sync_subtitle(
                one("key"), int(one("mi", "0") or 0), int(one("index", "0") or 0),
                one("skey"), one("sub"), one("save", "1") not in ("0", "false", ""),
                "placing a subtitle, asked by %s %s" % (
                    self.client_address[0], self.headers.get("X-Palladium-App") or "")))
            return

        # the reads a copy asks for by GET
        if self.follow_reads(path):
            return
        if path == "/follow/now":
            # Build the list again and take what is missing, now: what is worth
            # keeping changed the moment somebody started watching something.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            self.reply_json(pd_follow.sync_now(lambda: S.local().lib.config(),
                                               S.local().lib, S.local()))
            return
        if path == "/follow/log":
            # every file that has gone to a machine keeping copies, newest first,
            # with where it went
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                many = max(1, min(int((args.get("limit") or ["200"])[0]), 2000))
            except ValueError:
                many = 200
            import pd_traffic
            self.reply_json({"copies": pd_traffic.copies(many)})
            return
        return (path, )

    def _get_part_02(self, path):
        """GET routes, part 2 of 16: /follow/queue, /follow/test, /follow."""
        if path == "/follow/queue":
            # What the machine keeping copies will take, in the order it will take
            # it, and what it already holds.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            held = set(S.Handler.COPIES.get("keys") or [])
            # Only what the other machine says it holds. Having sent a file once is
            # not the same as it being there - it can arrive wrong, be swept away to
            # stay under a cap, or never finish - and treating "sent" as "here" hid
            # forty-six rows the other machine was still asking for, which is how a
            # transfer comes to have no line anywhere on this page.
            # The same question the other machine last asked, rather than one of this
            # page's own. Asking for the night's list at four in the afternoon showed
            # a queue nobody was working through.
            ask = S.Handler.LAST_ASK
            fresh = ask.get("when") and time.time() - ask["when"] < 900
            rows = self.worth_copying(
                float(ask["hours"]) if fresh else float(one.get("hours") or 4),
                bool(ask["deck"]) if fresh else False,
                int(ask["episodes"]) if fresh else int(one.get("episodes") or 6),
                None,
                float(ask["casual"]) if fresh else float(one.get("casualHours") or 0),
                bool(ask["whole"]) if fresh else bool(one.get("wholeList")))
            rows = [r for r in rows if self.in_the_queue(r, held)]
            queue = [{"key": r.get("key"),
                      "title": r.get("title") or r.get("name"),
                      # why it is in the list, and whose viewing put it there
                      "why": r.get("why") or "",
                      "who": r.get("who") or "",
                      # and everybody else it is kept for
                      "also": r.get("also") or [],
                      "gb": round((r.get("size") or 0) / 1e9, 2),
                      "here": r.get("key") in held,
                      # nothing is moved to the front by hand any more: a screen
                      # that is on is the only thing that lifts a row
                      "pinned": False,
                      # somebody is watching this, or the episode before it
                      "hot": bool(r.get("hot")),
                      "side": r.get("side") is not None}
                     for r in rows]
            # What is still to come stands above what has already arrived. The order
            # inside each half is the order the other machine will work in; a list
            # that reads top to bottom should not put a dozen finished rows between
            # the thing being fetched and the thing after it.
            queue.sort(key=lambda r: 1 if r["here"] else 0)
            # And the file actually being fetched this minute goes at the top, whether
            # or not the list as it stands now would still ask for it. The other
            # machine works from the list it was given a minute ago and finishes what
            # it started; a queue that leaves that file out is describing a different
            # evening from the one happening. It is the first thing anybody looks for.
            # What would not come stands above what has not been tried: a file that
            # failed is the thing holding the queue up, and it was being reported in a
            # list of its own underneath while the queue above pretended it was next.
            import pd_follow
            stuck = {}
            for bad in (list(S.Handler.FOLLOWER_TROUBLE)
                        + list(pd_follow.troubles() or [])):
                mark = str(bad.get("name") or "")
                if mark:
                    stuck[mark] = bad
            if stuck:
                def failed(r):
                    title = str(r.get("title") or "")
                    for name, bad in stuck.items():
                        if name == r.get("name") or (title and
                                                     name.startswith(title)):
                            return bad
                    return None
                first, rest = [], []
                for r in queue:
                    bad = failed(r)
                    if bad:
                        r["stuck"] = str(bad.get("said") or "would not come")
                        r["tries"] = int(bad.get("tries") or 1)
                        first.append(r)
                    else:
                        rest.append(r)
                queue = first + rest
            busy = next((r for r in S.WATCHING.snapshot()
                         if r.get("how") == "syncing"), None)
            if busy:
                mark = str(busy.get("key") or "")
                already = next((r for r in queue if str(r["key"]) == mark), None)
                if already:
                    queue.remove(already)
                    already["now"] = True
                    queue.insert(0, already)
                else:
                    queue.insert(0, {
                        "key": mark,
                        "title": str(busy.get("title") or ""),
                        "gb": round(float(busy.get("size") or 0) / 1e9, 2),
                        "here": False, "pinned": False, "hot": False,
                        "side": False, "now": True})
            self.reply_json({"queue": queue})
            return
        if path == "/follow/test":
            # Both halves of the question, from whichever machine is asked: can this
            # one reach the server it follows, and can it reach the machine that
            # follows it. Nothing is changed - it is a knock on a door.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            said = {"master": pd_follow.try_master(one) if one.get("master") else None,
                    "follower": pd_follow.try_standby(
                        (self.standby_now() or {}).get("where") or ""),
                    "kept": pd_follow.kept_here(one)}
            self.reply_json(said)
            return
        if path == "/follow":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            # the key this server hands to a machine that follows it, if one has
            # been made: the page shows it rather than making somebody press a
            # button to find out whether there is one
            # a key is a machine's because somebody marked it Cache under Users;
            # the flag underneath it is set at the same time, but the role is what
            # was chosen and what this list is of
            machines = [r for r in S.INVITES.load()
                        if S.Handler.role_of(r) == "cache"]
            mine = next(iter(machines), None)
            self.reply_json({"follow": pd_follow.settings(S.local().lib.config()),
                             "state": pd_follow.look(),
                             # how much of the reading is handed to the cache
                             "share": int(S.local().lib.config()
                                          .get("shareWithCopy") or 0),
                             "syncBack": dict(S.Handler.SYNC_BACK),
                             # every key made for a machine, with what each one
                             # allows. There was one key for the main server and one
                             # ceiling for whoever held it.
                             "keys": [
                                 {"token": r.get("token") or "",
                                  "name": r.get("name") or "",
                                  "created": int(r.get("created") or 0),
                                  "lastSeen": int(r.get("lastSeen") or 0),
                                  "cap": float(r.get("cap") or 0),
                                  "mayCopy": bool(r.get("mayCopy", True)),
                                  "role": S.Handler.role_of(r)}
                                 for r in machines],
                             "mine": {"key": (mine or {}).get("token") or "",
                                      "where": "http://%s:%d" % (S.LAN_IP, S.PORT),
                                      # the ceiling this library puts on a machine
                                      # that follows it; 0 means it sets its own
                                      "cap": float(S.local().lib.config()
                                                   .get("cacheCap") or 0)},
                             # and the machine following this one, as it announced
                             # itself: the address viewers are sent to when this
                             # server is off
                             # what this machine is called, and what it would be
                             # called if nobody had said: the page shows both, so a
                             # name is chosen rather than guessed at
                             "name": self.server_name(),
                             "hostname": socket.gethostname(),
                             # what the main server's key allows, the main server's name, and
                             # whether this request is the main server managing this machine
                             "houseCap": float(pd_follow.ALLOWED.get("gb") or 0),
                             "houseName": pd_follow.house_doors().get("name") or "",
                             "fromHouse": bool(self.headers.get("X-Palladium-House")
                                               and self.managed_from_the_house()),
                             # which catalogue keys this house holds: a button
                             # offering to send one it has not got can only fail
                             "have": {k: bool((S.local().lib.config().get(k) or "").strip())
                                      for k in ("opensubtitles_key", "tmdb_key")},
                             "standby": self.standby_now(),
                             # every machine that follows this one, freshest first,
                             # with whether it has spoken lately
                             "followers": sorted(
                                 [dict(f, alive=time.time() - f["when"] < 180,
                                       ago=int(time.time() - f["when"]))
                                  for f in S.Handler.FOLLOWERS.values()],
                                 key=lambda f: -f["when"]),
                             # the port this one answers on, and what the settings
                             # file asks for if that is a different number
                             # how much of the cache is used, and of what
                             "kept": pd_follow.kept_here(
                                 pd_follow.settings(S.local().lib.config())),
                             # and what would not come, if anything: one error at a
                             # time was written over by the next success
                             "trouble": pd_follow.troubles(),
                             # machines the owner has stopped, which keep their key
                             # and are refused anyway until they are let back in
                             "blocked": list(S.local().lib.config()
                                             .get("blockedCaches") or []),
                             "port": S.PORT,
                             "portWanted": int((S.read_settings() or {}).get("port")
                                               or S.PORT)})
            return
        return ()

    def _get_part_03(self, path):
        """GET routes, part 3 of 16:
        /performance, /machine/addons, /stream/buffer, /copy/read, /follow/side,
        /follow/facts, /follow/progress, /follow/here, /copies
        """
        if path == "/performance":
            # What this machine is doing that a person would feel: films being
            # encoded, a subtitle being written, the library being read, copies being
            # taken. And whether game mode is on, which stops the ones that can wait.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.reply_json(self.how_busy())
            return
        if path == "/machine/addons":
            # the big pieces of machinery: what they are for, whether they are on this
            # computer, and whether they are turned on
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_ai_subs
            os.environ["PALLADIUM_DATA"] = S.ROOT
            stored = S.read_settings() or {}
            self.reply_json({"addons": pd_ai_subs.addons(stored.get("addons") or {}),
                             "can": pd_ai_subs.ready() and S.tools_on(),
                             "toolsOff": bool(stored.get("toolsOff")),
                             # the scripts that drive the models, which are ours and
                             # come from our own site rather than from a model's
                             "tools": pd_ai_subs.tools()})
            return
        if path == "/stream/buffer":
            self.stream_buffer()
            return
        if path == "/copy/read":
            # One stretch of a file this machine holds, for the server it copies from
            # to hand on as part of its own answer. Named by the file and its mark
            # rather than by a number: a part id belongs to the library that issued
            # it, and these are two libraries.
            if not self.follows_here():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            name = (args.get("name") or [""])[0]
            mark = (args.get("mark") or [""])[0]
            here = self.copy_holds(name, mark)
            if not here:
                self.send_error(404, "not that file")
                return
            self.send_file_ranged(here)
            return
        if path == "/follow/side":
            # A subtitle file beside a video, as it lies on disk. Named by the video
            # it belongs to and its place in that list, never by a path: the machine
            # asking is another server, and a path is a thing to be careful with.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                which = int((args.get("n") or ["0"])[0])
            except ValueError:
                which = 0
            video = S.local().part((args.get("part") or ["0"])[0])
            if not video:
                self.send_error(404)
                return
            import pd_localapi as _la
            beside = _la.sidecars(video)
            if not (0 <= which < len(beside)):
                self.send_error(404)
                return
            self.send_file_ranged(beside[which]["file"])
            return
        if path == "/follow/facts":
            # What one title is, for the machine keeping copies. It makes a row of its
            # own for a place on a film it has not got, so the shelf can say where
            # somebody had got to; this gives that row its name and its picture.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            key = str((args.get("key") or [""])[0])
            con = S.local().lib.db()
            try:
                row = con.execute(
                    "SELECT title, year, overview, poster, backdrop, tmdb_id "
                    "FROM item WHERE id=?", (key,)).fetchone()
            finally:
                con.close()
            if not row:
                self.reply_json({"found": False})
                return
            self.reply_json({"found": True, "title": row["title"],
                             "year": row["year"], "overview": row["overview"],
                             "art": {"poster": row["poster"],
                                     "backdrop": row["backdrop"],
                                     "tmdb": row["tmdb_id"]}})
            return
        if path == "/follow/progress":
            # Where the people this machine keeps copies for had got to. Sent as what
            # the thing is rather than as a number: the other library files it under
            # its own. Without this, a house whose server sleeps at ten finds every
            # film starting from the beginning at five past.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            decks, lists, shuffles, lately = self.cached_for()
            whom = list(dict.fromkeys(decks + lists + shuffles + lately))
            # "now" is where the follower resumes from, which is not the clock when
            # there was more in the window than fitted in one answer
            places, mark = S.local().progress_of(whom, since, with_mark=True)
            more = mark < int(time.time())
            self.reply_json({"progress": places, "now": mark, "more": more})
            return
        if path == "/follow/here":
            # The following server saying where it can be reached. Written down so
            # that a viewer whose server does not answer has somewhere else to ask.
            # Only the main server's own cache: an ordinary invitation is somebody
            # else's machine, and its address is theirs, not ours to hand out.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                port = max(1, min(int((args.get("port") or ["8765"])[0]), 65535))
            except ValueError:
                port = 8765
            # the address it came from, not one it asks us to believe
            where = "http://%s:%d" % (self.client_address[0], port)
            # and the address from outside the main server, which is this network's own
            # with that machine's port forwarded to it - unless the owner set one
            # by hand, for a cache that lives somewhere else entirely
            said = (args.get("outside") or [""])[0][:120].strip().rstrip("/")
            # an address typed without http:// in front of it is still an address
            if said and not said.startswith(("http://", "https://")):
                said = "http://" + said
            outside = said if re.match(r"^https?://[\w.\-]+(:\d+)?$", said) else S.outside_url(port)
            def number(name):
                try:
                    return round(float((args.get(name) or ["0"])[0]), 1)
                except (ValueError, TypeError):
                    return 0.0
            self.remember_cache(where, (args.get("name") or [""])[0][:40],
                                  outside.rstrip("/"),
                                  (args.get("build") or [""])[0][:20],
                                  {"gb": number("gb"), "free": number("free"),
                                   "cap": number("cap"),
                                   "files": int(number("files"))})
            # whether that machine lets this one set how it copies
            if where in S.Handler.FOLLOWERS:
                S.Handler.FOLLOWERS[where]["managed"] = \
                    (args.get("managed") or ["0"])[0] == "1"
                S.Handler.remember_followers()
            self.reply_json({"ok": True, "where": where, "outside": outside})
            return
        if path == "/copies":
            # which titles the other machine is holding, so a shelf can mark them
            if not (self.role == "owner" or S.INVITES.check(self.bearer(),
                                                          self.app_name())):
                self.send_error(403, "not allowed")
                return
            said = dict(S.Handler.COPIES)
            # an answer nobody has refreshed for a day is not worth drawing
            if said.get("when") and time.time() - said["when"] > 86400:
                said = {"keys": [], "when": 0}
            self.reply_json(said)
            return
        return ()

    def _get_part_04(self, path):
        """GET routes, part 4 of 16: /follow/file, /server/build, /standby, /follow/invites."""
        if path == "/follow/takenow":
            # the main server saying a download has been asked for: taken at once
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            threading.Thread(target=pd_follow.take_requests, args=(one,), daemon=True).start()
            self.reply_json({"ok": True})
            return
        if path == "/follow/asked":
            # downloads asked for whose pack starts on the copy server: asked for often,
            # so a press starts at once and not when a copy round has ended
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json({"forCopy": [r for r in pd_torrents.for_copy() if not r.get("move")]})
            return
        if path == "/follow/packwhole":
            # whether this machine holds one pack file whole, for the server it follows
            # to decide whether it may let its own copy go
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            mark, index = (args.get("hash") or [""])[0], int((args.get("index") or ["-1"])[0] or -1)
            whole = pd_torrents.whole_file(mark, index)
            answer = {"whole": whole, "held": whole or pd_torrents.held_file(mark, index)}
            # asked to, the length and the fingerprint of the file as it is on the disk now
            if whole and (args.get("mark") or ["0"])[0] == "1":
                where = pd_torrents.file_of(mark, index)
                try:
                    answer.update(size=os.path.getsize(where), mark=pd_torrents.fingerprint(where))
                except OSError:
                    answer["whole"] = False
            self.reply_json(answer)
            return
        if path == "/follow/file":
            # One file this machine fetched, for the server it follows to take a copy
            # of. Only that server may ask, and only for something this machine went
            # and got itself.
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            where = pd_torrents.file_to_send((args.get("hash") or [""])[0],
                                             (args.get("index") or ["-1"])[0])
            if not where or not os.path.exists(where):
                self.send_error(404, "not here")
                return
            # a copy on its drive while it goes, so that drive's workers wait; and a
            # viewer short of disk comes first - only live viewing goes before it
            import pd_disk
            turn, busy = S.transfer_turn(where, False, "move-%s-%s" % (
                (args.get("hash") or [""])[0], (args.get("index") or [""])[0]),
                "Move: " + os.path.basename(where))
            if busy:
                self.send_error(503, "the drive is busy: " + busy)
                return
            # carried on from a byte: the other server's copy resumes where it stopped
            whole_size = os.path.getsize(where)
            span = S.byte_range(self.headers.get("Range"), whole_size)
            if span is None:
                pd_disk.done(turn)
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % whole_size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # only now: after a 200 the 503 above went out as the file's first bytes
            self.send_response(206 if self.headers.get("Range") else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(span[1] - span[0] + 1))
            if self.headers.get("Range"):
                self.send_header("Content-Range", "bytes %d-%d/%d" % (span[0], span[1], whole_size))
            self.end_headers()
            drive = os.path.splitdrive(where)[0].upper()
            with S.Handler.SERVING_LOCK:
                S.Handler.SERVING[drive] = S.Handler.SERVING.get(drive, 0) + 1
            # on Now playing like any copy to the other machine, saying it is a move
            sid = S.WATCHING.start((self.follows_here() or {}).get("name") or "copy server",
                                 os.path.splitext(os.path.basename(where))[0], "", "syncing",
                                 self.client_address[0], "", self.app_name(), self.device_kind(),
                                 why="moving to its home", path=where,
                                 size=os.path.getsize(where))
            try:
                with open(where, "rb") as f:
                    f.seek(span[0])
                    left = span[1] - span[0] + 1
                    while left > 0:
                        held = 0
                        while held < 120 and S.playback_short():
                            time.sleep(1)
                            held += 1
                        lump = f.read(min(262144, left))
                        if not lump:
                            break
                        left -= len(lump)
                        try:
                            self.wfile.write(lump)
                        except Exception:
                            break
                        S.WATCHING.sent(sid, len(lump))
            finally:
                S.WATCHING.stop(sid)
                pd_disk.done(turn)
                with S.Handler.SERVING_LOCK:
                    S.Handler.SERVING[drive] = S.Handler.SERVING.get(drive, 1) - 1
                    if S.Handler.SERVING[drive] <= 0:
                        S.Handler.SERVING.pop(drive, None)
            return
        if path == "/follow/torrentwhole":
            # whether a torrent sent here is read and seeding whole
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            t = pd_torrents.QB(pd_torrents.load()["config"]).info((args.get("hash") or [""])[0]) or {}
            state = str(t.get("state") or "")
            self.reply_json({"whole": bool(t) and float(t.get("progress") or 0) >= 1
                             and not state.startswith(("checking", "stopped", "paused", "error", "missing")),
                             "state": state})
            return
        if path == "/follow/packpart":
            # the part file of a pack this machine holds: the pieces of the files nobody
            # asked for, which the next home needs for the films' edge pieces to read whole
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            import pd_disk
            info_hash = re.sub(r"[^0-9a-f]", "", (args.get("hash") or [""])[0].lower())
            _, (where, size) = pd_torrents.files_here(info_hash)
            if not size:
                self.send_error(404, "no part file here")
                return
            turn, busy = S.transfer_turn(where, False, "part-" + info_hash, "Move: part file")
            if busy:
                self.send_error(503, "the drive is busy: " + busy)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(where, "rb") as f:
                    while True:
                        lump = f.read(1048576)
                        if not lump:
                            break
                        self.wfile.write(lump)
            finally:
                pd_disk.done(turn)
            return
        if path == "/follow/packwholes":
            # every file of one pack this machine holds whole, for the server it follows
            # to move back the ones it lacks when the pack's home comes back to it
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_torrents
            self.reply_json({"whole": pd_torrents.whole_files((args.get("hash") or [""])[0])})
            return
        if path == "/server/build":
            # The installer this machine is running, for the one that follows it. Only
            # a cache may ask: it is 34 MB off this machine's disk, and the hash
            # goes with it so what arrives is checked rather than trusted.
            if not (self.role == "owner" or self.follows_here()):
                self.send_error(403, "not allowed")
                return
            said = S.Handler.build_on_offer()
            if not said:
                self.reply_json({"version": ""})
                return
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            if (args.get("file") or [""])[0] not in ("1", "true", "yes"):
                self.reply_json(said)
                return
            where = S.Handler.BUILD_MARK.get("path") or ""
            if not where or not os.path.exists(where):
                self.send_error(404, "no installer here")
                return
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.microsoft.portable-executable")
            self.send_header("Content-Length", str(os.path.getsize(where)))
            self.send_header("X-Palladium-Version", said.get("version", ""))
            self.end_headers()
            with open(where, "rb") as f:
                while True:
                    lump = f.read(262144)
                    if not lump:
                        break
                    try:
                        self.wfile.write(lump)
                    except Exception:
                        break
            return
        if path == "/standby":
            # The other machine that holds copies of what this house watches, for a
            # client to fall back on when this server is off. Answered to anybody who
            # may read the library at all - it is this house's second machine.
            if not (self.role == "owner" or S.INVITES.check(self.bearer(),
                                                          self.app_name())):
                self.send_error(403, "not allowed")
                return
            self.reply_json(self.standby_now())
            return
        if path == "/follow/invites":
            # The main server's invitations, for the cache to honour: a guest whose
            # server is off reaches the cache with the link they already have.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            self.reply_json({"invites": [
                {"token": r.get("token"), "name": r.get("name"),
                 "expires": r.get("expires"), "language": r.get("language"),
                 # and what the key is for. Sent as a bare guest, the owner's own key
                 # opened nothing of theirs on their own second machine, and the panel
                 # on the wall was refused the one thing it asks for.
                 "role": r.get("role") or "",
                 # and what is kept for them, so the other machine can say on its
                 # own front page why its shelves are short
                 "shareLan": bool(r.get("shareLan", True)),
                 "cacheDeck": bool(r.get("cacheDeck")),
                 "cacheList": bool(r.get("cacheList")),
                 "cacheCasual": bool(r.get("cacheCasual"))}
                for r in S.INVITES.load() if not r.get("follows")],
                "owner": self.owner_caching(),
                # and who the person at that machine is, so the cache files their
                # viewing under the same name: the places travel under it, and a
                # machine that calls them somebody else shows an empty shelf
                "ownerIs": str((S.read_settings() or {}).get("ownerIs") or ""),
                "ownerName": str((S.read_settings() or {}).get("ownerName") or ""),
                "name": socket.gethostname(),
                # the catalogue key, so what the other machine copies arrives as
                # films with posters rather than as a list of file names. It is the
                # house's own key, going to the main server's own second machine.
                "tmdb": (S.local().lib.config().get("tmdb_key") or ""),
                # How each person has their subtitles drawn, and in what language.
                # An evening that moves to the cache mid-film should not change size,
                # colour or language halfway through - and the menus over there had
                # this machine's defaults behind them rather than that person's.
                "look": self.everyones_subtitles(),
                "language": (S.local().lib.config().get("language") or "en-US")})
            return
        return ()

    def _get_part_05(self, path):
        """GET routes, part 5 of 16: /follow/playing, /subs/making, /subs/find."""
        if path == "/follow/playing":
            # What another server should keep a copy of: whatever is being watched
            # here, and the episodes after it. Only a key marked as a cache may
            # ask - it is the main server's viewing, not a guest's business.
            # Two kinds of cache. A key marked as one is another server of this
            # house: it may know what the main server is watching. An ordinary invitation
            # is a person keeping their own copies on their own machine - they get
            # what is theirs and nothing else, which is no more than they can already
            # see in Continue watching.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (self.role == "owner" or invite):
                self.send_error(403, "not allowed")
                return
            mine = None if (self.role == "owner" or invite.get("follows"))                 else invite["token"]
            try:
                hours = max(0.0, min(float(
                    (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                     .get("hours") or ["4"])[0]), 24.0))
            except (ValueError, IndexError):
                hours = 4.0
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            deck = (args.get("deck") or ["0"])[0] in ("1", "true", "yes")
            whole = (args.get("whole") or ["0"])[0] in ("1", "true", "yes")
            try:
                episodes = max(0, min(int((args.get("eps") or ["6"])[0]), 40))
            except ValueError:
                episodes = 6
            try:
                # hours of casual watching to keep ahead, for whoever asked for it
                casual = max(0.0, min(float((args.get("casual") or ["0"])[0]), 24.0))
            except ValueError:
                casual = 0.0
            # Which kinds of thing this copy will take this minute. Each kind is off,
            # inside its hours, or always, and the machine keeping the copies works out
            # which of them apply now - it is the one that knows what time it is there.
            # A copy that says nothing gets all of them, as every copy did before.
            these = (args.get("these") or [""])[0].strip()
            kinds = ([k for k in these.split(",") if k in S.Handler.KINDS]
                     if these else None)
            cfg = S.local().lib.config()
            # What the shuffle would play next is copied when the machine keeping the
            # copies asks for it, and not otherwise. It used to be asked and then
            # overruled here, so the setting on that machine's own page did nothing
            # anybody could see. It is that machine's disk and its own hours.
            if mine is None:
                # The fuller question wins. A cache asks twice a round - once for
                # what is on a screen, once for the watchlists as well - and asks the
                # lighter one every minute while a copy is running. Remembering
                # whichever came last meant the page reproduced the short list and
                # left out everything actually being fetched from a watchlist.
                held = S.Handler.LAST_ASK
                fresher = (deck or not held.get("deck")
                           or time.time() - (held.get("when") or 0) > 900)
                if fresher:
                    S.Handler.LAST_ASK = {"hours": hours, "deck": deck,
                                        "episodes": episodes, "casual": casual,
                                        "whole": whole, "when": int(time.time())}
            # whose cache this is: a name means only that viewer's own viewing is
            # worth copying, and nothing means the whole house's
            only = (args.get("for") or [""])[0][:60].strip()
            # Two lists, from one pass. "holding" is everything worth keeping whatever
            # the hour; "wanted" is the part this copy will take this minute. The copy
            # sweeps against the first and fetches from the second - asked with the
            # hour applied, the only list it had was the short one, so a kind set to
            # night was fetched at nine and deleted at eight the next morning.
            try:
                keep_days = max(0.0, min(float((args.get("keep") or [S.KEEP_DAYS])[0]), 365.0))
            except ValueError:
                keep_days = S.KEEP_DAYS
            holding = self.worth_copying(hours, deck, episodes, mine,
                                         casual, whole, only, None, keep_days)
            # what the house list gained or lost since the last time it was asked: a
            # file on one list and off the next is copied and then deleted
            if mine is None and not only:
                said, S.Handler.LAST_HOLDING = S.holding_changes(S.Handler.LAST_HOLDING, holding, deck)
                if said:
                    pd_log.raw("%s cache list: %s%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), said, chr(10)))
            # and the other way: what this server should hold of packs living on the copy
            if mine is None:
                try:
                    self.sync_back_soon(hours, deck, episodes, casual, whole, keep_days)
                except Exception as e:
                    S.Handler.SYNC_BACK["why"] = str(e)[:200]
            keep = None if kinds is None else set(kinds)
            # A reason belonging to no kind is taken whatever the hour, which is what
            # the filter inside wanted_keys did: the episodes after a programme and the
            # subtitles riding with a film are named by neither of the settings.
            wanted = ([w for w in holding
                       if not S.Handler.kind_of(w.get("why") or "")
                       or S.Handler.kind_of(w.get("why") or "") in keep]
                      if keep is not None else holding)
            wanted = [w for w in wanted if not w.get("kept")]
            # Sync set to from the copy server only: it fetches nothing from here, and keeps
            # what it holds - holding is still the whole list
            if S.sync_way(S.local().lib.config()) == "fromCopy":
                wanted = []
            # a film coming in or the library scanning has the disks: only what is on
            # a screen now is copied meanwhile, the rest waits for it
            busy = S.disks_busy()
            if busy:
                wanted = S.copy_while_busy(wanted)
            import pd_torrents
            self.reply_json({"wanted": wanted, "holding": holding, "busy": busy,
                             # which machines seed the split packs, and which those are
                             "splitSeed": {"way": S.split_seed_way(S.local().lib.config()),
                                           "packs": sorted(h for h, v in (pd_torrents.load().get("home") or {}).items()
                                                           if v == "split")},
                             # films asked for whose pack lives on the copy server
                             "forCopy": pd_torrents.for_copy(),
                             # the copy server's settings, kept here
                             "managed": ((S.read_settings() or {}).get("managedCopies") or {})
                             .get(self.bearer() or "") or {},
                             # who this house has, so the other machine can offer
                             # the names rather than asking somebody to type one
                             "house": self.everyone_here()[:40],
                             # what this machine is running, and what it thinks
                             # the asking one is. A cache set to replace itself
                             # when the main server moves on reads both from here - and
                             # neither was ever sent, so it compared nothing against
                             # nothing and stayed on the build it was installed with.
                             "serverVersion": self.build_version(),
                             "mine": (S.Handler.STANDBY.get("build") or ""),
                             # whose viewing this answer is about, so the other
                             # machine can say so on its own page
                             "whose": ("the main server" if mine is None
                                       else (invite.get("name") or "you")),
                             # and whether anybody is watching this minute: the disk
                             # and the line belong to them, not to a copy of a film
                             # nobody has started
                             "watching": len(S.local().playing_now() or {}) > 0,
                             # somebody here is going to bed early: take everything
                             # anybody is half way through, whatever the clock says
                             "earlyUntil": (S.Handler.EARLY_UNTIL
                                            if S.Handler.EARLY_UNTIL > time.time()
                                            else 0)})
            return
        if path == "/warm":
            # the next episode's file, its two ends read ahead while this one ends, so
            # opening it is served from memory rather than waiting on the disk
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            found = S.local().file_for((args.get("key") or [""])[0],
                                     int((args.get("mi") or ["0"])[0] or 0)) or {}
            video = found.get("file") or ""
            if video and os.path.exists(video):
                threading.Thread(target=S.warm_file, args=(video,), daemon=True).start()
            self.reply_json({"ok": bool(video)})
            return
        if path == "/subs/making":
            # what is being written down at the moment, and what is waiting
            import pd_ai_subs
            self.reply_json(pd_ai_subs.look())
            return
        if path == "/subs/find":
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            self.reply_json(self.find_subtitles(
                (args.get("key") or [""])[0],
                (args.get("lang") or [self.viewer_language().replace("off", "en")])[0]))
            return
        return ()

    def _get_part_invites(self):
        """GET /invites: everybody holding a key, with what keeping each costs - the
        settings read once for the whole answer: read for each person's link, list and
        shuffle, they were most of its time (2026-11-05)."""
        with S.settings_held():
            people = [self.with_link(r) for r in S.INVITES.load()]
            mine = self.owner_caching()
            # the settings once for the whole list, not once a person
            stored = S.read_settings() or {}
            for row in people:
                row["cost"] = self.follower_cost(row["token"])
                # What sort of key this is. They were all drawn as people, so a key
                # made for a machine sat in the list of viewers being asked whose
                # half-watched films it should keep.
                row["role"] = S.Handler.role_of(row)
                # and what they call themselves, if they have said. The name on the
                # invitation is the owner's record of who they gave a key to; this is
                # the name that shows on a screen.
                row["shown"] = str((stored.get("users") or {})
                                   .get(row["token"], {}).get("myName") or "")
                row["kind"] = "machine" if row["role"] == "cache" else "guest"
                # the owner's own key: one person, one row
                row["you"] = bool(mine.get("token")
                                  and row["token"] == mine["token"])
            self.reply_json({"people": people,
                             # what the owner is called, so no screen shows "me" -
                             # which is a placeholder a server wears until somebody
                             # says who they are, not a person's name
                             "ownerName": stored.get("ownerName") or "",
                             "me": dict(mine,
                                        cost=self.follower_cost(mine.get("token")
                                                             or "me")),
                             "lan": S.LAN_IP, "wan": S.wan_ip(), "port": S.PORT,
                             "outsideName": stored.get("outsideName") or ""})

    def _get_part_settings(self):
        """GET /settings: this viewer's settings and the house's, answered with the
        settings file read once for the whole answer - read for each of its fields, it
        was parsed a hundred times and took a third of a second (2026-11-05)."""
        with S.settings_held():
            # the query is parsed further down for the media paths; this branch sits
            # above that, so it reads its own
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            key = (args.get("key") or [""])[0]
            device = self.device_of((args.get("device") or ["web"])[0])
            self.reply_json({
                # what this viewer is called, so a page can show it and let them
                # change it - guests included
                "myName": (self.viewer_settings(self.settings_file()).get("myName")
                           or self.guest_name
                           or (S.read_settings() or {}).get("ownerName") or ""),
                # how a film should be fetched for this viewer
                "splitPlay": bool(self.viewer_settings(self.settings_file())
                                  .get("splitPlay", True)),
                "failover": bool(self.viewer_settings(self.settings_file())
                                 .get("failover", True)),
                # whether finishing a title takes it off this viewer's watchlist
                "dropWatched": bool(self.viewer_settings(self.settings_file())
                                    .get("dropWatched", True)),
                # which app this viewer is offered
                "appChannel": self.app_channel(),
                "subtitles": self.subtitle_settings(key, device),
                "override": bool(key) and self.has_override(key, device),
                "device": device,
                # every screen at once, for a settings page that shows all three
                "devices": {d: self.subtitle_settings(None, d) for d in self.DEVICES},
                "language": self.viewer_language(),
                # and what to read instead when the film has nothing in the first
                "language2": self.viewer_language_2(),
                # the accent, and the colours on offer, so a settings page anywhere
                # can draw the row without knowing the list itself
                "accent": self.accent_now(),
                # a poster behind the shelves, per screen: the row for each, and
                # the answer for whichever screen is asking
                "backdrop": self.backdrop_all(),
                "backdropHere": self.backdrop_now(),
                # what the server draws with, so a settings page can show which
                # swatch is merely the default and which one this viewer chose
                "accentDefault": self.accent_default(),
                "accentMine": str(self.viewer_settings(self.settings_file())
                                  .get("myAccent", "") or ""),
                "accents": [{"code": c, "name": n} for c, n in S.ACCENTS],
                # a stored null is not an answer: the key has been written empty,
                # and "not false" is what everybody who never chose should get -
                # otherwise the next episode quietly stops following this one
                "autoNext": self.settings_file().get("autoNext") is not False,
                # what the film shelf stands for this viewer: what is held, what a
                # pack can fetch, and what can only be asked for
                "filmsShow": self.films_show(),
                # and whether their TV tab stands the popular programmes not held
                "tvPopular": S.local().tv_popular_of(self.viewer_settings(self.settings_file())),
                # and whether they stand series nobody can download or stream
                "startTab": self.start_tab(),
                "camReleases": S.local().cam_releases_of(self.viewer_settings(self.settings_file())),
                "watchMessages": ("on" if pd_notify.messages_on(self.viewer_settings(self.settings_file()))
                                  else "off"),
                "notify": ("on" if pd_notify.notify_on(self.viewer_settings(self.settings_file()))
                           else "off"),
                "tvRequest": ((self.viewer_settings(self.settings_file()).get("tvRequest")
                               if self.viewer_settings(self.settings_file()).get("tvRequest")
                               in ("hide", "show") else "hide")),
                "letThemFetch": self.let_them_fetch(),
                "fetchMaxGb": self.fetch_most_gb(),
                "preferRate265": str((S.read_settings() or {}).get("preferRate265") or "any"),
                "autoRuns": {n: S.auto_set(n) for n in S.AUTO_RUNS},
                "weeklyRestart": __import__("pd_boss").clean_weekly((S.read_settings() or {}).get("weeklyRestart")),
                "restartOnCrash": self.restart_on_crash(S.read_settings()),
                # what each job is doing now, and whether it has stalled
                "autoBeats": __import__("pd_beat").status(),
                "diskSlots": S.disk_slots(), "drives": S.library_drives(),
                "diskQueue": __import__("pd_disk").status(),
                "givenUp": {n: __import__("pd_workers").given_up(n) for n in S.AUTO_RUNS},
                "preferRate264": str((S.read_settings() or {}).get("preferRate264") or "any"),
                "rateLock": bool((S.read_settings() or {}).get("rateLock")),
                "preferQuality": str((S.read_settings() or {}).get("preferQuality") or "any"),
                "preferSeeds": str((S.read_settings() or {}).get("preferSeeds") or "any"),
                "seriesRate": str((S.read_settings() or {}).get("seriesRate") or "same"),
                "closeCall": str((S.read_settings() or {}).get("closeCall") or "off"),
                "preferRes": self.prefer_res(),
                "preferBy": self.prefer_by(),
                "preferCodec": self.prefer_codec(),
                "preferSource": self.prefer_source(),
                "preferSound": self.prefer_sound(),
                "preferGroup": self.wish("preferGroup", self.GROUPS),
                                 "preferOrder": ",".join(self.wish_order(S.read_settings())),
                "followShows": list((S.read_settings() or {}).get("followShows") or []),
                "grabLatest": bool((S.read_settings() or {}).get("grabLatest")),
                "libraryShows": S.library_shows(),
                "meters": self.meters(),
                # whether one release is brought to the level of the next, and where
                # that level is
                "evenVolume": bool(self.settings_file().get("evenVolume")),
                "volumeTarget": self.settings_file().get("volumeTarget", S.VOLUME_TARGET),
                # whether this viewer takes part in watch parties at all
                "watchParty": self.wants_a_party(),
                # whether subtitles are put in step by themselves: the server's
                # answer unless this viewer has one of their own
                # whether the next episode's subtitle is fetched before it starts;
                # anything but an explicit no means yes, as it always behaved
                "autoFetch": self.settings_file().get("autoFetch") is not False,
                # how many days an arrived episode is marked New
                "newDays": __import__("pd_localapi").new_days(),
                # the season's shelf: what is chosen, what there is to choose, and
                # which holiday the calendar says it is today
                "seasonal": str((S.read_settings() or {}).get("seasonal") or "off"),
                # the streaming services a new release's page shows
                "watchRegion": str((S.read_settings() or {}).get("watchRegion") or ""),
                "watchServices": list((S.read_settings() or {}).get("watchServices") or []),
                "holidays": [{"id": h["id"], "title": h["title"],
                              "days": __import__("pd_holidays").in_words(h)}
                             for h in __import__("pd_holidays").HOLIDAYS],
                "holidayToday": __import__("pd_holidays").on_the_calendar(
                    __import__("datetime").date.today()),
                # what else may be downloaded by itself, and the languages read here
                "fetchRules": self.fetch_rules(),
                "languagesRead": self.languages_read(),
                # whether an owner's key still runs the place from outside the house
                "remoteAdmin": self.settings_file().get("remoteAdmin") is not False,
                "remoteOwner": self.remote_owner(),
                "autoSync": self.scans_for(),
                "autoScan": self.auto_scan(),
                # the front page: what this viewer sees, what they chose for
                # themselves, and what the server sets for anybody who has not
                "homeRows": self.home_rows(),
                "homeRowsMine": (self.viewer_settings(self.settings_file())
                                 .get("homeRows") or []),
                "homeRowsDefault": self.home_default(),
                "ownerName": self.owner_name(),
                # whether a picture subtitle can be painted in for this caller, and
                # the owner's setting behind it
                "burnAllowed": self.may_burn(),
                "burnFor": self.burn_policy(),
                # and which clock they read: 24 hours unless they say otherwise
                "clock": str(self.viewer_settings(self.settings_file())
                             .get("clock", "24")),
                "nextDelay": int(self.settings_file().get("nextDelay", 5)),
                # the ceiling on each side of the front door, and which side this
                # viewer is on, so a player can say what it is actually going to get
                "quality": self.quality_caps(),
                "here": "home" if self.at_home() else "away",
                # how far this subtitle has been moved, when one was asked about: a
                # correction belongs to the file, not to the screen it was made on
                "subShift": self.sub_shift(key, (args.get("sub") or [""])[0]),
                # and whether the film asked for it or somebody did
                "shiftBy": self.shift_source(key, (args.get("sub") or [""])[0]),
                # and this viewer's own preference, which everybody may set
                "mine": self.my_quality(),
                # the main server this one mirrors, for a settings page to say so
                "mirrorOf": self.mirror_of(),
                # the plugins' settings: each decides what a guest may read
                **__import__("pd_plugins").settings_get(self.role == "owner"),
            })

    def _get_part_06(self, path):
        """GET routes, part 6 of 16: /settings, /marks, /mystream."""
        if path == "/settings":
            self._get_part_settings()
            return
        if path == "/marks":
            # all, some or none of what this key stands for, per shelf: what the
            # buttons on a programme and a season have to draw themselves from now
            # that marking one means marking its episodes
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            key = (args.get("key") or [""])[0]
            self.reply_json({"key": key,
                             "watchlist": self.marks_state("watchlist", key),
                             "favorites": self.marks_state("favorites", key)})
            return
        if path == "/mystream":
            # what this caller is being sent, for its own statistics line
            mine = [w for w in S.WATCHING.snapshot() if w["address"] == self.client_address[0]]
            self.reply_json(mine[0] if mine else {})
            return
        return ()

    def _get_part_07(self, path):
        """GET routes, part 7 of 16: /watching, /collections/season."""
        if path in ("/health", "/follow/health"):
            # The house at a glance: what needs somebody, from both machines. The
            # cache answers /follow/health for the main server, as its copying is asked.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            facts = self.health_here()
            if path == "/health":
                self.health_of_followers(facts)
            import pd_health
            self.reply_json({"at": int(time.time()), "machines": facts["machines"],
                             "problems": pd_health.problems(facts), "facts": facts})
            return
        if path == "/watching":
            # a stream knows the address it goes to; the player knows what the device
            # calls itself. Match them on the title and show the friendlier one.
            live = S.WATCHING.snapshot()
            names = S.local().devices()
            doing = S.local().playing_now()
            matched = set()          # clients whose report belongs to an open stream
            for row in live:
                # a file going to the machine that keeps copies is not a viewing:
                # nobody is at the other end of it, so it borrows neither a name from
                # the main server nor a state from a player
                if row.get("how") == "syncing":
                    row["state"] = "syncing"
                    continue
                named = names.get(row["title"])
                if named and row["who"] == row["address"]:
                    row["who"] = named
                # what the player itself says: running or held, and how far in. The
                # stream knows the bytes; only the client knows the film.
                said = doing.get(row.get("key") or "") or doing.get(row["title"]) or {}
                if said:
                    matched.add((said.get("who") or "",
                                 said.get("device") or said.get("title")))
                for k in ("state", "position", "duration", "episode",
                          "client", "device", "app", "kind", "info"):
                    if said.get(k) not in (None, ""):
                        row[k] = said[k]
                # and whether it was put on rather than chosen. Now playing read the
                # same for a film somebody sat down to and an episode a shuffle dealt
                # them, which are not the same evening - and it is the difference
                # that decides what is kept ahead for them.
                row["casual"] = bool(said.get("casual"))
                # how many machines are sending it: blocks from the copy in the last
                # half minute make it two, for the panel's mark beside the stream
                mine, theirs, last = S.Handler.CARRIED.get(row.get("who")) or (0, 0, 0)
                row["sources"] = 2 if (theirs > 0 and time.time() - last < 30) else 1
                # and a player reading the film off both machines itself: the other one
                # is sending this viewer the same title (dual stream in the app)
                # and the speed is both together: what this one sends was all the row said
                there = (str(row.get("address") or ""), str(row.get("key") or ""))
                if row.get("how") != "syncing" and there in S.peer_live():
                    row["sources"] = 2
                    row["mbit"] = round(float(row.get("mbit") or 0) + S.peer_live()[there], 1)
                # How much picture that screen is holding, and whether it has run out.
                # The players have reported this for a long time and the page has
                # always been ready to draw it - it was simply never put on the row,
                # so "12s buffered" was left out of every line. Kept per screen, so
                # one person watching in two rooms does not read as one.
                who_is = str(row.get("who") or "")
                held = S.Handler.AHEAD.get(who_is + "/" + str(row.get("address") or ""))
                if not held and who_is:
                    # the report and the stream can arrive by different roads - one
                    # over the network here, the other from outside - so the address
                    # need not match. The newest report from that person will do.
                    theirs = [v for k, v in S.Handler.AHEAD.items()
                              if k.startswith(who_is + "/")]
                    held = max(theirs, key=lambda v: v[1]) if theirs else None
                if held and time.time() - held[1] < 60:
                    row["ahead"] = round(float(held[0]), 1)
                    row["stalled"] = bool(held[4]) if len(held) > 4 else False
            # Somebody paused has no connection open - a direct play closes it, and a
            # transcode is stopped to save the GPU - but they are still watching, and
            # their player says so. They belong in the list.
            for said in S.local().playing_reports():
                if (said.get("who") or "",
                        said.get("device") or said.get("title")) in matched:
                    continue
                live.append({
                    # the person, and the machine they are on only if the report
                    # cannot say who they are: a row drawn from a player's report
                    # took the device's own model name, so the same person watching
                    # one film appeared as themselves on one machine and as a
                    # telephone's part number on the other. A report says who by
                    # their key, which is not a thing to print at somebody.
                    "who": (self.name_of(said.get("who")) if said.get("who")
                            else said.get("device") or "someone"),
                    # an episode by its programme as well: the player reports the
                    # episode's own name, and "The Secret" alone said nothing on the panel
                    "title": ((S.local().title_for(str(said.get("key")))
                               if str(said.get("key") or "").startswith("e") else "")
                              or said["title"]),
                    "episode": said.get("episode") or "",
                    "quality": "", "how": "waiting", "address": "",
                    "started": said.get("began") or 0,
                    "seconds": 0, "mb": 0, "mbps": 0.0, "mbit": 0.0,
                    "average": 0.0, "peak": 0.0,
                    "state": said.get("state") or "paused",
                    "position": said.get("position"), "duration": said.get("duration"),
                    "client": said.get("client") or "", "key": said.get("key") or "",
                    # what is playing it, for a row with no stream of its own
                    "app": said.get("app") or "", "kind": said.get("kind") or "",
                })
            # One shape for every row: the film and its year in brackets, the way a
            # file is named. A player reports the name and the year apart, so the same
            # person watching the same film read as two different films.
            #
            # The year is dropped whether or not the title had to be rewritten. Cleared
            # only on the rewrite, a row whose title already carried its year kept the
            # label - and rows are gathered per machine under key plus that label, so
            # one film read off both machines was two rows on the page: the machine the
            # player reports to had the year, the other had nothing.
            for row in live:
                said = str(row.get("title") or "")
                year = str(row.get("episode") or "").strip()
                if year.isdigit() and len(year) == 4:
                    if not said.endswith("(%s)" % year):
                        row["title"] = "%s (%s)" % (said, year)
                    row["episode"] = ""
            # How many seconds of film each screen says it is holding. The server has
            # had this from every player since the reports started arriving, and used
            # it only to decide how hard to hold a copy back - nothing ever showed it,
            # so a viewer running thin was invisible to anybody but the throttle.
            now = time.time()
            for row in live:
                mark = str(row.get("who") or "") + "/" + str(row.get("address") or "")
                said = S.Handler.AHEAD.get(mark)
                if not said or now - (said[1] or 0) > 60:
                    continue
                row["ahead"] = round(float(said[0]), 1)
                row["aheadAt"] = int(said[1])
                if len(said) > 4 and said[4]:
                    row["stalled"] = True
            live.sort(key=lambda r: r.get("started") or 0)
            # and what the machine is listening to for its loudness, which is work
            # somebody watching a page should be able to see happening
            said = {"live": live}
            if S.MEASURE_NOW.get("name"):
                done, left = S.loudness_counts()
                said["measuring"] = {
                    "name": S.MEASURE_NOW["name"],
                    "since": S.MEASURE_NOW.get("since") or 0,
                    "done": done, "left": left}
            # and where a film's credits begin, read off the picture and the sound
            try:
                import pd_credits
                doing = pd_credits.working()
                if doing:
                    said["analysing"] = doing
            except Exception:
                pass
            # and what is coming in: every download is something going on in the house
            try:
                coming = S.coming_in() + S.copies_coming(live)
                # and a remux being ripped to 1080p: work on this machine somebody asked for
                try:
                    import pd_rip
                    coming += [{"name": "%s (%s) - %s - %s" % (r["title"], r["year"], r.get("profile") or "rip",
                                                         pd_rip.stage(r)), "state": "ripping",
                                "progress": r["progress"], "eta": r["eta"] or 0, "mbit": 0.0,
                                "who": "", "from": ""} for r in pd_rip.running()]
                except Exception:
                    pass
                if coming:
                    said["downloading"] = coming
            except Exception:
                pass
            # and the torrents qBittorrent is checking: files already here being read
            try:
                reading = S.checking_now()
                if reading:
                    said["checking"] = reading
            except Exception:
                pass
            # and every other piece of work this machine is on this minute
            try:
                busy = S.busy_now()
                if busy:
                    said["working"] = busy
            except Exception:
                pass
            # every worker with its state, for the boxes beside the drawing
            try:
                said["workers"] = S.workers_now()
            except Exception:
                pass
            self.reply_json(said)
            return
        if path == "/collections/season":
            # what the box under the Season field shows: the span read back, so a
            # typo is visible before it is saved rather than after it has quietly
            # taken nothing
            import pd_seasons
            asked = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            spec = str(asked.get("spec", [""])[0])
            nope = str(asked.get("without", [""])[0])
            spans = pd_seasons.parse(spec)
            out = pd_seasons.parse(nope)
            self.reply_json({"said": pd_seasons.describe(spans),
                             "saidWithout": pd_seasons.describe(out),
                             "spans": len(spans), "without": len(out)})
            return
        return ()

    def _get_part_08(self, path):
        """GET routes, part 8 of 16:
        /collections, /collections/items, /machine, /library/numbering
        """
        if path in ("/collections", "/collections/items"):
            shelves = self.collections()
            con = S.local().lib.db()
            try:
                if path == "/collections":
                    out = []
                    for c in shelves:
                        keys = self.collection_keys(con, c)
                        # the poster the shelf shows: the one chosen for it, or the
                        # first title it holds, so a new shelf has a face at once
                        face, art = self.shelf_face(con, c, keys)
                        out.append(dict(c, count=len(keys), cover=face, art=art,
                                        resumeAt=self.shuffle_resume(c.get("id")),
                                        seriesShelf=bool(S.shelf_series(con, c))))
                    self.reply_json({"collections": out})
                    return
                args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                        if "?" in self.path else {})
                want = (args.get("id") or [""])[0]
                shelf = next((c for c in shelves if c.get("id") == want), None)
                if not shelf:
                    self.reply_json({"error": "no such collection", "Metadata": []}, 404)
                    return
                # what the shelf holds, read as seasons rather than as episodes
                raw = [str(k) for k in self.collection_keys(con, shelf)]
                seasoned, keys = self.read_as_seasons(con, raw)
                rows = []
                for key in keys:
                    one = S.local().metadata_for(con, key, brief=True)
                    if one:
                        rows.append(one)
                rows += seasoned
                # and the seasons of a series held whole that nothing links yet, as the
                # collection's own page shows them
                for show in self.whole_series(con, shelf):
                    rows = S.local()._every_season(con, rows, show)

                # In the order they were made. A collection is usually a series of
                # films, and the year is how anybody reads one - alphabetical put
                # the fourth film before the second, and anything added by hand at the end.
                # A season's own title is "Season 10", which sorts before "Season 2"
                # and says nothing about which programme it belongs to - so a season
                # is placed by its programme and then by its number.
                def order(m):
                    name = (m.get("titleSort")
                            or (m.get("parentTitle") if m.get("type") == "season"
                                else "")
                            or m.get("title") or "")
                    return (int(m.get("year") or 0), name, int(m.get("index") or 0))
                rows.sort(key=order)
                self.shuffle_marks(con, shelf, rows, raw)
                # What is out: struck out by hand, or caught by the include words
                # and taken out again by the exclude ones. Both are shown while
                # editing, so either can be put back without searching for it.
                pinned = set(str(k) for k in (shelf.get("pinned") or []))
                hidden = set(str(k) for k in (shelf.get("hidden") or []))
                for one in rows:
                    one["hand"] = str(one.get("ratingKey")) in pinned
                # the keys the shelf actually holds, and the cards they were read
                # as: a programme drawn as its seasons is still on the shelf, and
                # comparing only the cards had it listed as struck out.
                held = set(raw) | set(str(m.get("ratingKey")) for m in rows)
                # Both excludes lifted: the title words that knocked something out,
                # and the season span that did. What either took is shown struck
                # out, so it can be put back without searching for it.
                wide = dict(shelf, hidden=[],
                            rule=dict(shelf.get("rule") or {}, without=[],
                                      seasonWithout=""))
                def strike(k):
                    """What is really out, for one key the rule or the hand took.

                    A programme drawn as its seasons: only the seasons that are not
                    on the shelf are out. Its own key put a whole series in the
                    struck row over one episode taken off one season.
                    """
                    k = str(k)
                    if k in held:
                        return []
                    if re.fullmatch(r"[0-9a-f]{12}", k):
                        mine = set(int(r["season"] or 0) for r in con.execute(
                            "SELECT DISTINCT season FROM episode WHERE item_id=?", (k,)))
                        # the pack's seasons too, or a season struck out by a span
                        # that this machine holds no file of is out of both rows
                        row = con.execute("SELECT title FROM item WHERE id=?",
                                          (k,)).fetchone()
                        mine |= set(int(o.get("index") or 0) for o in
                                    S.local()._offered_seasons_of(
                                        row["title"] if row else "", mine))
                        parts = ["%s-s%d" % (k, n) for n in sorted(mine)]
                        if parts:
                            return [p for p in parts if p not in held]
                    return [k]
                gone = []
                for k in (list(self.collection_keys(con, wide))
                          + list(shelf.get("hidden") or [])):
                    gone += [x for x in strike(k) if x not in gone]
                struck = []
                for key in gone:
                    key = str(key)
                    # a struck season is read as a season too, or eighteen cards in
                    # this row all carry the programme's name and nothing else
                    one = (self.season_card(con, key, [])
                           if re.match(r"^[0-9a-f]{12}-s\d+$", key)
                           else S.local().metadata_for(con, key, brief=True))
                    if one:
                        one["hand"] = key in hidden or key in pinned
                        struck.append(one)
                struck.sort(key=order)
                # which title is its face, resolved the same way the list is, so
                # an editing screen can mark it without asking twice
                face = str(shelf.get("cover") or "")
                if face not in [str(m.get("ratingKey")) for m in rows]:
                    face = str(rows[0].get("ratingKey")) if rows else ""
                self.reply_json({"name": shelf.get("name", ""), "id": shelf["id"],
                                 "rule": shelf.get("rule") or {}, "cover": face,
                                 "Metadata": rows, "Excluded": struck})
                return
            finally:
                con.close()
        if path == "/machine":
            # what this computer is set to do about Palladium: start it at sign-in,
            # and let the rest of the main server reach it
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            now = time.time()
            self.reply_json(dict(
                pd_machine.state(S.PORT, S.LAN_IP),
                server=self.build_version(),
                # newest first, and only what has spoken in the last day: a phone that
                # was here on Tuesday says nothing about what is running now
                clients=[dict(row, ago=int(now - row["when"]))
                         for row in sorted(self.SEEN.values(), key=lambda r: -r["when"])
                         if now - row["when"] < 86400]))
            return
        if path == "/library/numbering":
            # Seasons where the files count the episodes differently from the season,
            # and what each is set to do about it. The owner's: it moves rows.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            chosen = self.settings_file().get("numbering") or {}
            rows = S.local().lib.seasons_shifted()
            for r in rows:
                r["mode"] = str(chosen.get("%s-s%s" % (r["key"], r["season"]), "auto"))
            self.reply_json({"seasons": rows})
            return
        return ()

    def _get_part_09(self, path):
        """GET routes, part 9 of 16: /party, /chat, /notice."""
        if path == "/party":
            # Whether there is one, who is in it, and whether the asker is.
            now = int(time.time())
            here = [dict(v, key=k,
                         name=self.named_for_reader(v.get("name")),
                         where=(v.get("where") if self.role == "owner" else ""))
                    for k, v in S.PARTY["who"].items()
                    if now - v.get("seen", 0) < 300]
            if not self.wants_a_party():
                self.reply_json({"on": False, "off": True, "in": False,
                                 "people": []})
                return
            # Who is about: the screens that have asked this server for anything in
            # the last few minutes, with what each has playing. This is what makes the
            # lobby a room rather than a message list - somebody arriving can see that
            # three people are here and two of them are already watching.
            now = int(time.time())
            playing = {}
            try:
                for row in S.WATCHING.snapshot():
                    playing[row.get("address") or ""] = row.get("title") or ""
            except Exception:
                playing = {}
            # One line per screen, not one per build it has run today: the client
            # register keeps a row for each address and version, and a phone that
            # updated twice was three people in the lobby.
            about, seen_here = [], set()
            for row in sorted(self.SEEN.values(), key=lambda r: -r["when"]):
                where = row.get("where", "")
                if now - row["when"] > 300 or where in seen_here:
                    continue
                seen_here.add(where)
                about.append({"name": row.get("name") or where,
                              "where": where,
                              "guest": bool(row.get("name")),
                              "version": row.get("version", ""),
                              "watching": playing.get(where, ""),
                              "ago": max(0, int(now - row["when"]))})
            asked = S.PARTY["asked"].get(self.party_key())
            owner = self.role == "owner"
            # a guest sees who is here, not where they are. Screens the server has no
            # name for are numbered, so two of them read as two people rather than as
            # the same line twice.
            counted = 0
            for row in about:
                if not owner:
                    named = self.named_for_reader(row["name"])
                    if named == self.A_SCREEN:
                        counted += 1
                        named = "Screen %d" % counted
                    row["name"] = named
                    row["where"] = ""
            self.reply_json({"on": bool(S.PARTY["on"]), "since": S.PARTY["since"],
                             "in": self.party_key() in S.PARTY["who"],
                             "title": S.PARTY.get("title", ""),
                             "key": S.PARTY.get("key", ""),
                             # who might be asked: the screens that have spoken to
                             # this server today, and everybody with an invitation
                             # the same screens, one line each, for asking
                             # Asking a screen by address is the owner's to do:
                             # it is their network. A guest may ask the other guests.
                             "screens": ([{"key": "here:" + r["where"],
                                           "name": r["where"], "where": r["where"]}
                                          for r in about if not r.get("guest")]
                                         if owner else []),
                             # only people who are actually here: an invitation
                             # nobody is holding open is not somebody to ask
                             "guests": [{"key": "guest:" + r["name"], "name": r["name"]}
                                        for r in about if r.get("guest")],
                             "invited": asked,
                             "here": about if self.in_the_house() else [],
                             "people": here})
            return
        if path == "/chat":
            # What has been said since the caller last looked. Held open until there
            # is something, the same way a notice is: a conversation that arrives half
            # a minute late is not one.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
                wait = max(0, min(int((args.get("wait") or ["0"])[0]), 55))
            except ValueError:
                since, wait = 0, 0
            if not self.wants_a_party():
                # said plainly rather than silently: the box knows to put itself away
                self.reply_json({"party": False, "off": True, "messages": [], "id": 0})
                return
            room = (args.get("room") or ["party"])[0]
            if room == "lobby":
                # The lobby is always open: anybody who may watch may sit in it,
                # whether or not there is a party on. It is where a party is arranged
                # in the first place.
                # a line addressed to one screen is for that screen, in the lobby as
                # much as in the party: a test sent to a television is not everybody's
                def lobby_lines():
                    return [m for m in S.CHAT if m.get("room") == "lobby"
                            and self.addressed_to_me(m.get("to", "all"))]
                fresh = [m for m in lobby_lines() if m["id"] > since]
                if not since and not fresh:
                    fresh = lobby_lines()[-20:]
                until = time.time() + wait
                while wait and not fresh and time.time() < until:
                    S.CHAT_RUNG.wait(min(5.0, max(0.5, until - time.time())))
                    fresh = [m for m in lobby_lines() if m["id"] > since]
                self.reply_json({"party": bool(S.PARTY["on"]), "room": "lobby",
                                 "messages": self.said_safely(fresh),
                                 "id": S.CHAT[-1]["id"] if S.CHAT else 0})
                return
            if not S.PARTY["on"]:
                # no party, no room: said plainly, so a client can put its box away
                self.reply_json({"party": False, "messages": [], "id": 0})
                return
            self.join_party()
            until = time.time() + wait
            while wait and not [m for m in S.CHAT if m["id"] > since
                                and self.addressed_to_me(m.get("to", "all"))] \
                    and time.time() < until:
                S.CHAT_RUNG.wait(min(5.0, max(0.5, until - time.time())))
            mine = [m for m in S.CHAT if m.get("room", "party") == "party"
                    and self.addressed_to_me(m.get("to", "all"))]
            fresh = [m for m in mine if m["id"] > since]
            # a reader arriving in the middle is given the last of it rather than
            # nothing at all
            if not since and not fresh:
                fresh = mine[-20:]
            self.reply_json({"party": True, "messages": self.said_safely(fresh),
                             "id": S.CHAT[-1]["id"] if S.CHAT else 0})
            return
        if path == "/notice":
            # A line the owner wants the screens in the house to see. Held in memory
            # and only for a while: this is a word in passing, not a message anybody
            # should find again tomorrow.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                since = int((args.get("since") or ["0"])[0])
                wait = max(0, min(int((args.get("wait") or ["0"])[0]), 55))
            except ValueError:
                since, wait = 0, 0

            def whats_said():
                words = S.NOTICE.get("text") or ""
                if words and time.time() > S.NOTICE.get("until", 0):
                    words = ""
                return words

            # Held open until there is something to say. A screen asks with the id it
            # already has and the answer comes the moment somebody writes - which is
            # what "push" means over an ordinary connection, and needs nothing on the
            # television but a longer read timeout.
            until = time.time() + wait
            while (wait and S.NOTICE.get("id", 0) <= since
                   and self.addressed_to_me(S.NOTICE.get("to"))
                   and time.time() < until):
                S.NOTICE_RUNG.wait(min(5.0, max(0.5, until - time.time())))
                if S.NOTICE.get("id", 0) > since:
                    break
            mine = self.addressed_to_me(S.NOTICE.get("to"))
            said = whats_said() if mine else ""
            # an instruction to play is now or never
            fresh = mine and time.time() < float(S.NOTICE.get("playUntil") or 0)
            self.reply_json({"id": S.NOTICE.get("id", 0), "text": said,
                             # what to open, for a screen this was addressed to
                             "play": (S.NOTICE.get("play") or "") if fresh else "",
                             "shelf": (S.NOTICE.get("shelf") or "") if fresh else "",
                             "at": int(S.NOTICE.get("at") or 0) if fresh else 0})
            return
        return ()

    def _get_part_10(self, path):
        """GET routes, part 10 of 16:
        /wiring, /skins, /where, /copying, /mood, /build, /server, /update
        """
        if path == "/wiring":
            # Everything the drawing on the settings page shows, in one answer. It
            # asked three times over, every few seconds; on a machine at the end of a
            # slow link that is three round trips for one picture, and the page felt
            # like treacle for want of a single endpoint.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            import pd_follow
            import pd_torrents
            now = time.time()
            rows = S.WATCHING.snapshot()
            # which side the copying is on, so the line and the ceiling shown are the
            # ones that decide it: the network here and the line to the world are two
            # different pipes and only one of them is holding a copy back
            copy_side = next((S.Handler.side_of(r.get("address")) for r in rows
                              if r.get("how") == "syncing"), "home")
            work = S.busy_now()
            try:
                import pd_credits
                doing = pd_credits.working()
                if doing:
                    work.append({"name": "Credits analyser",
                                 "step": str(doing.get("name") or "")})
            except Exception:
                pass
            if S.MEASURE_NOW.get("name"):
                work.append({"name": "Loudness", "step": str(S.MEASURE_NOW["name"])})
            self.reply_json({
                "name": self.server_name(),
                "machine": dict(pd_machine.state(S.PORT, S.LAN_IP),
                                server=self.build_version(),
                                # its own address on this network, for the drawing
                                lan=("http://%s:%d" % (S.LAN_IP, S.PORT)) if S.LAN_IP else "",
                                # this machine's own way in from outside, so the
                                # drawing can name both doors rather than the
                                # cache's alone
                                outside=S.outside_url()),
                "standby": dict(self.standby_now(), busy=self.copy_is_busy()),
                # and the machine this one follows, for a drawing made on the
                # cache: the other half of the pair is the main server, not a cache
                # of its own that it does not have - with what that machine is
                # feeding, so the line from it to a screen can be drawn here too
                "follows": dict(self.house_doors(), busy=self.house_is_busy()),
                "state": dict(pd_follow.STATE),
                "clients": [dict(row, ago=int(now - row["when"]))
                            for row in sorted(self.SEEN.values(),
                                              key=lambda r: -r["when"])
                            if now - row["when"] < 86400],
                # films coming in from a torrent pack, the one downloading first: the
                # machine is busy even when nothing is going out
                # - from qBittorrent itself, so the tracker's and those added by
                # hand are there as well as the packs'
                "downloads": [dict(title=d["name"], state=d.get("state") if d.get("state") == "queued" else "downloading",
                                   progress=d["progress"], mbit=d["mbit"],
                                   eta=d["eta"] or None, who=d["who"])
                              for d in S.coming_in()],
                # and whatever else this machine is working at, credits included
                "working": work,
                "live": [r for r in rows if r.get("how") != "syncing"],
                "syncing": sum(1 for r in rows if r.get("how") == "syncing"),
                # and what the cacheing is taking off this machine. Only the number of
                # them was sent, so what a machine was actually shifting left the
                # copying out - which on a night of syncing is most of it.
                "syncingMbit": round(sum(float(r.get("mbit") or 0) for r in rows
                                         if r.get("how") == "syncing"), 1),
                # what the line has been seen to carry, and what a copy is allowed
                # against it this second. Both are worked out rather than set, so
                # there is nowhere else to read them.
                # Per side, because they are two lines: the network here and the one
                # to the world. Shown for the side the copying is actually on, which is
                # the number that decides anything.
                "linkMbit": round((S.Handler.LINK.get(copy_side) or {}).get("seen", 0), 1),
                "linkSide": copy_side,
                "syncGivingWay": bool(S.Handler.a_film_is_struggling(
                    [r for r in rows if S.Handler.side_of(r.get("address")) == copy_side],
                    copy_side)),
                "syncCeiling": round(S.Handler.sync_ceiling(
                    S.local().lib.config(),
                    sum(float(r.get("mbit") or 0) for r in rows
                        if r.get("how") != "syncing"
                        and S.Handler.side_of(r.get("address")) == copy_side),
                    sum(float(r.get("mbit") or 0) for r in rows
                        if r.get("how") == "syncing"
                        and S.Handler.side_of(r.get("address")) == copy_side),
                    copy_side), 1),
            })
            return
        if path == "/skins":
            # every look a screen may wear, and which one this viewer picked. The
            # colours live on the server so that adding one is an entry in a file
            # rather than an edit to three clients in two languages.
            import pd_skins
            stored = S.read_settings() or {}
            mine = self.viewer_settings(stored)
            put_on, why = self.imposed_skin()
            self.reply_json({"skins": pd_skins.all_of(),
                             "chosen": pd_skins.known(mine.get("skin")),
                             "imposed": put_on, "why": why})
            return
        if path == "/where":
            # Both ways in to this machine, and the name it answers to.
            #
            # A screen that only knows the address it happened to be opened with has
            # one way in: a page opened at home cannot reach the main server from a train,
            # and one opened from away goes out to the router and back in to reach a
            # machine three feet from it. Neither is a secret - anybody who can ask
            # this question already has one of the two - and knowing both is what
            # lets a client keep working when one of them stops.
            self.reply_json({
                "lan": (("http://%s:%d" % (S.LAN_IP, S.PORT))
                        if S.LAN_IP and self.may_have_the_lan() else ""),
                "outside": S.outside_url(),
                "name": self.server_name(),
                # the same machine under two addresses answers with one id, which is
                # how a list of servers keeps from showing it twice.
                #
                # server_id, not machine_id: it is the one the handshake hands out and
                # the one every client files a server under. Answering here with the
                # other left the two unable to agree that a machine was itself.
                "id": self.server_id(),
                # and what it can encode with, if anything. A machine with no card
                # and no ffmpeg was still being labelled "transcode (NVENC)" by
                # whatever was playing from it, which is a sentence about somebody
                # else's computer.
                "engine": self.engine_name(),
                # And the machine this one follows, both of its ways in. Whichever of
                # the two machines somebody adds, they get all four addresses: this
                # one answers for itself and repeats what the main server told it while the
                # house could still be asked.
                "follows": self.house_doors(),
            })
            return
        if path == "/copying":
            # What the machine that keeps copies is fetching this minute, if
            # anything. Anybody may ask: it is one line about this house's own second
            # machine, and a viewer watching the film being fetched is the person it
            # matters to.
            row = next((r for r in S.WATCHING.snapshot()
                        if r.get("how") == "syncing"), None)
            if not row:
                self.reply_json({"key": "", "title": "", "at": 0.0})
                return
            self.reply_json({"key": str(row.get("key") or ""),
                             "title": str(row.get("title") or ""),
                             "at": float(row.get("position") or 0.0)})
            return
        if path == "/mood":
            # What this machine should be dressed as. Two things change it and
            # neither is a preference: a server that only holds copies, after dark,
            # is not the one the main server usually watches - and a machine whose card has
            # been handed to a game is not serving films at all. Anyone may ask; it
            # decides nothing, it only says what is already true.
            self.reply_json(dict(self.mood_now(),
                                 backdrop=self.backdrop_now()))
            return
        if path == "/build":
            # what this server is, for a page that wants to say which build drew it -
            # and whether the asker is in the same house, which is who may say
            # anything about it
            self.reply_json({"version": self.build_version(),
                             "lan": self.in_the_house()})
            return
        if path == "/server":
            # The installer, from this machine rather than from the site. Somebody on
            # this network who wants their own server should not have to go through
            # the beta gate to get the same file that is sitting here.
            self.send_installer()
            return
        if path == "/update":
            # What build the site is carrying, against the one running. Anybody
            # holding a key may ask: it is a version number, and a screen that cannot
            # ask shows nothing at all where the machines should be - which is what a
            # viewer away from the main server saw, an empty box rather than two servers.
            #
            # Replacing the program is a different matter and stays where it was: the
            # owner, from the same house as the machine. "lan" below says which of the
            # two the asker is, so a screen that may not press knows not to offer.
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            import pd_update
            stored = self.settings_file()
            said = pd_update.state(
                self.build_version(), stored.get("betaKey", ""),
                force=(args.get("force") or [""])[0] in ("1", "true", "yes"),
                folders=self.installer_folders())
            said["lan"] = self.in_the_house()
            self.reply_json(said)
            return
        return ()

    def _get_part_11(self, path):
        """GET routes, part 11 of 16:
        /copied, /traffic, /stats, /watchlog, /changes, /feedback, /manifest.webmanifest,
        /health
        """
        if path == "/copied":
            # The cacheing, asked the way the watching is asked: this week, this
            # month, altogether. Two different questions about the same machine, and
            # they read best side by side.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_traffic
            self.reply_json(pd_traffic.copied_stats())
            return
        if path == "/traffic":
            # How much has left this machine, by month and by year, kept apart by
            # what it left as: a film somebody watched, or a copy going to the other
            # server. Added together they answer nothing.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_traffic
            self.reply_json(pd_traffic.read())
            return
        if path == "/stats":
            # What the house has watched: this week, this month, and since the library was
            # built. The owner's, like the log it is counted from - and the machine that
            # follows asks for it, so its page says the same totals.
            if self.role != "owner" and not self.follows_here():
                self.send_error(403, "not allowed")
                return
            # on the copy: the main server's figures, which count both machines; its own
            # log holds only what was watched here
            import pd_torrents
            if pd_torrents._following():
                try:
                    said = json.loads(S.peer_fetch("/stats", 20) or b"null")
                except ValueError:
                    said = None
                if isinstance(said, dict) and said.get("total"):
                    self.reply_json(said)
                    return
                here = self.watch_stats()
                here["onlyHere"] = True       # the main server not answering: this machine's own
                self.reply_json(here)
                return
            self.reply_json(self.watch_stats())
            return
        if path == "/watchlog":
            # who watched what, and when. The owner's: it is everybody's viewing
            # history, which is not a guest's business.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            # the newest few hundred by default - a page nobody scrolls to the end
            # of - and the whole book when it is asked for
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            try:
                want = int((args.get("limit") or ["400"])[0])
            except ValueError:
                want = 400
            want = max(1, min(want, 20000))
            # or everything since a number of days back, both machines' plays
            try:
                days = max(0, int((args.get("days") or ["0"])[0]))
            except ValueError:
                days = 0
            since = int(time.time()) - days * 86400 if days else 0
            con = S.local().lib.db()
            try:
                held = con.execute("SELECT COUNT(*) c FROM watchlog WHERE started >= ?",
                                   (since,)).fetchone()["c"]
                rows = con.execute("""SELECT * FROM watchlog WHERE started >= ?
                                      ORDER BY started DESC LIMIT ?""",
                                   (since, 20000 if days else want)).fetchall()
            finally:
                con.close()
            names = {r["token"]: r["name"] for r in S.INVITES.load()}
            here_name = self.server_name()
            out = []
            for r in rows:
                out.append({
                    "who": (self.owner_name() if r["who"] == "me"
                            else names.get(r["who"], "a guest")),
                    "title": r["title"], "device": r["device"], "client": r["client"],
                    "started": r["started"], "updated": r["updated"],
                    "seconds": max(0, int(r["updated"] or 0) - int(r["started"] or 0)),
                    "position": r["position"], "duration": r["duration"],
                    "casual": bool(r["casual"]), "key": r["key"],
                    # which build was watching, for rows recorded since that was kept
                    "app": (r["app"] if "app" in r.keys() else "") or "",
                    # and which machine it played on: a row from the other one says so
                    "machine": ((r["machine"] if "machine" in r.keys() else "")
                                or here_name),
                })
            # One play, one line. A film read off both machines was logged by each of
            # them, and with both logs kept here it showed twice: rows of one viewer
            # and one title whose times overlap are one play, on both ("A + B"). A
            # play that carries on on the other machine within ten minutes is one
            # play that moved there - a failover - and says so ("A → B").
            merged, open_ = [], {}
            for row, r in sorted(zip(out, rows), key=lambda x: int(x[1]["started"] or 0)):
                mark = (r["who"], r["key"])
                a, b = int(r["started"] or 0), int(r["updated"] or 0)
                prev = open_.get(mark)
                if prev is not None:
                    overlap = a <= prev["updated"] and b >= prev["started"]
                    # moved: carried on elsewhere after the machine that was playing it
                    # last stopped, within ten minutes
                    moved = (not overlap and row["machine"] != prev["_last"]
                             and 0 <= a - prev["updated"] <= 600)
                    if overlap or moved:
                        prev["_parts"].append((row["machine"], a, b))
                        prev["_moved"] = prev["_moved"] or moved
                        if b >= prev["updated"]:
                            prev["_last"] = row["machine"]
                        prev["updated"] = max(prev["updated"], b)
                        prev["position"] = max(prev["position"] or 0, row["position"] or 0)
                        prev["seconds"] = max(0, prev["updated"] - prev["started"])
                        continue
                row.update(started=a, updated=b, _parts=[(row["machine"], a, b)],
                           _moved=False, _last=row["machine"])
                open_[mark] = row
                merged.append(row)
            for row in merged:
                # which minutes on which machine: each machine's rows joined where
                # they touch, in time order, and marked where two ran at once
                parts = sorted(row.pop("_parts"), key=lambda x: x[1])
                spans = []
                for m, a, b in parts:
                    last = next((sp for sp in reversed(spans) if sp["machine"] == m), None)
                    if last and a <= last["to"] + 60:
                        last["to"] = max(last["to"], b)
                    else:
                        spans.append({"machine": m, "from": a, "to": b})
                for sp in spans:
                    sp["both"] = any(o is not sp and o["machine"] != sp["machine"]
                                     and o["from"] <= sp["to"] and o["to"] >= sp["from"]
                                     for o in spans)
                # a moment on one machine inside the other's span - the parts a split
                # read took from it - is that span's "both", not a line of its own
                spans = [sp for sp in spans if not any(
                    o is not sp and o["machine"] != sp["machine"]
                    and o["from"] <= sp["from"] and sp["to"] <= o["to"]
                    and (o["to"] - o["from"]) > (sp["to"] - sp["from"]) for o in spans)]
                row["parts"] = spans
                row["failover"] = row.pop("_moved")
                row.pop("_last")
                path = []
                for sp in spans:
                    if sp["machine"] not in path:
                        path.append(sp["machine"])
                row["machine"] = (" → " if row["failover"] else " + ").join(path)
            merged.reverse()
            # how many there are altogether, so a page showing the newest few can
            # say what it is not showing
            self.reply_json({"watched": merged, "held": held})
            return
        if path == "/changes":
            # What has been added, newest first. Kept beside the code in changes.json
            # so it is written when the thing is written, rather than remembered
            # afterwards by whoever is asked.
            try:
                with open(os.path.join(S.CODE, "changes.json"), encoding="utf-8") as f:
                    entries = json.loads(f.read())
            except Exception:
                entries = []
            self.reply_json({"changes": entries,
                             "app": self.app_version()})
            return
        if path == "/feedback":
            rows = self.reports()
            # what has come in since the owner last opened the page. Their own writing
            # does not count: nobody needs telling about what they just wrote.
            since = self.settings_file().get("reportsSeen", 0)
            unseen = sum(1 for r in rows
                         if r.get("when", 0) > since and r.get("who") != "you"
                         and not r.get("done"))
            self.reply_json({"reports": rows, "owner": self.role == "owner",
                             # what has been cleared away, so a mistake can be undone
                             # without going to look in a file
                             "filed": self.filed(),
                             # and how a backlog being handed over is getting on
                             "sending": dict(S.SENDING),
                             "unseen": unseen})
            return
        if path == "/manifest.webmanifest":
            # Written per person, not served from disk. A home-screen web app on iOS
            # has its own cookie jar, so the invitation cookie does not follow it in -
            # the address it starts at has to carry the invitation itself, or the icon
            # opens on a locked server.
            token = self.bearer()
            body = json.dumps({
                "name": "Palladium", "short_name": "Palladium",
                "description": "A private film library.",
                "start_url": ("/?t=" + token) if token else "/",
                "scope": "/",
                "display": "standalone",
                "orientation": "any",
                "background_color": "#0b0d10",
                "theme_color": "#0b0d10",
                "icons": [
                    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {"src": "/icon-1024.png", "sizes": "1024x1024",
                     "type": "image/png", "purpose": "any maskable"},
                ],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            # For whatever is watching the process: a container's healthcheck, a
            # reverse proxy, a monitor. It says the server is answering and nothing
            # else - not the machine's name, not who is watching, not the library.
            self.reply_json({"ok": True})
            return
        return (path, )

    def _get_part_12(self, path):
        """GET routes, part 12 of 16: /app, /app/version, /palladium.apk, /invite.png."""
        if path in ("/app", "/app/version"):
            # version.json is written by publish_apk.py straight from the APK, so the
            # page, the update check and the file itself can never disagree
            info = {"versionName": "?", "versionCode": 0, "sizeMb": 0, "built": ""}
            # the one this asker is given: the owner the newest, everyone else the copy
            # released at eight; beside the program, or with this server's own things
            for told in [self.app_for_asker()[1]] + [os.path.join(w, "version.json")
                                                     for w in (S.STATIC, S.ROOT)]:
                try:
                    with open(told, encoding="utf-8") as f:
                        info.update(json.loads(f.read()))
                    break
                except Exception:
                    continue
            # and whether there is actually a file to go with the number
            # what is on the disk, and whether this server hands it out
            info["have"] = bool(self.app_beside_us())
            info["off"] = bool((S.read_settings() or {}).get("appOff"))
            info["here"] = info["have"] and not info["off"]
            info["getting"] = bool(S.Handler.APP_FETCH["busy"])
            info["from"] = S.Handler.APP_FETCH.get("where") or S.Handler.APP_FROM
            info["got"] = S.Handler.APP_FETCH.get("got") or 0
            info["size"] = S.Handler.APP_FETCH.get("size") or 0
            info["part"] = S.Handler.APP_FETCH.get("part") or 0.0
            if path == "/app/version":
                # The server's own release as well as the app's. They were one number
                # while both were built together and have since drifted - the browser
                # is the server's own client and was labelling itself with the phone's
                # number, which is a version it does not run.
                # What this server is actually running, which is the number a
                # machine that follows it compares itself against. It was read out
                # of the changelog beside the program - a file the installer
                # rewrites, and one that is missing entirely on a machine running
                # from source - so the answer was often empty, and a cache
                # comparing itself against nothing never updated at all.
                info["serverVersion"] = self.build_version()
                self.reply_json(info)
                return
            # the invitation this page was opened with travels on to the file, or the
            # button is refused the moment it is pressed from outside the main server
            token = self.bearer()
            info["carry"] = ("?t=" + token) if token else ""
            # A television has no browser: the way anything is installed on a Google TV
            # is the Downloader app, which wants an address typed with a remote. Hence
            # the short code - and the .apk on the end, so Downloader knows what it has.
            info["tv"] = ""
            if token:
                code = S.Invites.code_for(token)
                info["tv"] = (
                    "<p style='color:#93a0b0;font-size:13px;margin:22px auto 0;"
                    "max-width:420px;line-height:1.7;border-top:1px solid #1e242c;"
                    "padding-top:20px'>On a <b style='color:#e8ecf1'>Google TV</b> "
                    "there is no browser. Install <b style='color:#e8ecf1'>Downloader"
                    "</b> from the Play Store, allow it to install unknown apps, and "
                    "type this in the address box - no http, nothing on the end:"
                    "<br><span style='display:inline-block;margin-top:10px;"
                    "font:600 19px ui-monospace,monospace;color:#4a90f0;letter-spacing:"
                    "1px'>%s/i/%s</span></p>"
                    % (self.headers.get("Host") or S.LAN_IP, code))
            html = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>Palladium</title>"
                    "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                    "text-align:center;padding:11vh 20px;margin:0'>"
                    "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                    "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                    "margin:10px 0 4px'>PALLADIUM</h1>"
                    "<p style='color:#93a0b0;margin:0 0 4px'>Android app for phones, "
                    "tablets and Google TV</p>"
                    "<p style='color:#4a90f0;font-size:15px;margin:0 0 26px'>"
                    "version %(versionName)s <span style='color:#6b7683'>&middot; "
                    "%(sizeMb).1f MB &middot; built %(built)s</span></p>"
                    "<a href='/palladium.apk%(carry)s' style='display:inline-block;"
                    "background:#4a90f0;"
                    "color:#111;font-weight:600;padding:15px 34px;border-radius:9px;"
                    "text-decoration:none;font-size:17px'>Download and install</a>"
                    "<p style='color:#6b7683;font-size:13px;margin-top:28px;line-height:1.6;"
                    "max-width:420px;margin-left:auto;margin-right:auto'>"
                    "Installing over an existing copy updates it and keeps your settings. "
                    "Android asks permission to install from your browser the first time."
                    "</p>%(tv)s</body>") % info
            body = html.replace("#4a90f0", self.accent_now()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/palladium.apk":
            self.send_app(who=self.guest_name)
            return
        if path == "/invite.png":
            # a chat app fetching a preview has no token; this is a logo and nothing
            # else, so it is the one thing served to anybody who asks
            pic = os.path.join(S.STATIC, "invite.png")
            if not os.path.exists(pic):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(os.path.getsize(pic)))
            self.end_headers()
            with open(pic, "rb") as f:
                self.wfile.write(f.read())
            return
        if path == "/palladium.apk":
            apk = os.path.join(S.STATIC, "palladium.apk")
            if not os.path.exists(apk):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Length", str(os.path.getsize(apk)))
            self.send_header("Content-Disposition", "attachment; filename=palladium.apk")
            self.end_headers()
            with open(apk, "rb") as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        return ()

    def _get_part_13(self, path, q):
        """GET routes, part 13 of 16: /local/."""
        if path.startswith("/local/"):
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            # everyone keeps their own place in a film; a guest is known by the token
            # they arrived with, which is the only name the server can be sure of -
            # and an owner with a key of their own is known by that, on the
            # television at home as much as in a browser away from it
            S.local().who = self.viewer()
            # and the shelves being shuffled, so Continue watching can carry a row
            # for each of them: what is being carried on with, or what is next
            mine_now = self.viewer_settings(self.settings_file())
            S.local().shuffles = mine_now.get("shuffles") or {}
            S.local().shelf_names = {str(c.get("id")): str(c.get("name") or "")
                                   for c in (mine_now.get("collections") or [])
                                   if isinstance(c, dict) and c.get("id")}
            # and a series shuffled from its page, named after the series
            S.local().shelf_names.update(self.series_shuffle_names(S.local().shuffles))
            # where a shuffled playing got to, kept on the shelf's round
            S.local().shelf_note = self.shuffle_note
            S.local().shelf_forget = self.title_finished
            # and the next episode fetched from a pack while this one is still on
            S.local().half_way = self.keep_the_next_one_ready
            # and what the client calls itself, so the watch log can say which build
            # was watching - a fault from a three-week-old one is a different
            # conversation from a fault on today's
            S.local().app_now = self.app_name()
            sub = path[len("/local"):]
            # a watch log row records what kind of thing was watching, not only the
            # name it calls itself by - and this is the request it is written from
            if sub.startswith("/:/timeline"):
                kind = self.device_kind()
                if kind:
                    q["client"] = [kind]
            if sub == "/library/onDeck":
                # A copy shows the main server's Continue watching: one shelf, the
                # same rows and the same shuffle card on both machines. Its own
                # answer only while the main server cannot be reached.
                said = self.house_gets(self.path.split("?", 1)[0],
                                       self.path.split("?", 1)[1] if "?" in self.path
                                       else "")
                if said is not None:
                    self.reply_json(said)
                    return
            if sub == "/library/popularShows":
                # the programmes being watched now that this house can put on - or
                # the whole list, for the page the row opens onto
                rows = (S.popular_everything() if (q.get("all") or [""])[0] in ("1", "true")
                        else S.popular_shows())
                self.reply_json({"MediaContainer": {"size": len(rows), "Metadata": rows}})
                return
            if sub == "/library/watchlist":
                # what this viewer marked. It is theirs, so it comes from their
                # settings and not from anything the caller can ask for.
                q["keys"] = [",".join(self.watchlist())]
                # A mark follows its title. One starred while a pack was the only
                # thing carrying it is stored under the pack's key; the day the film
                # arrives it has a library key, and the star on its poster looked for
                # that one and found nothing - so it stood on the watchlist unlit.
                # The list is asked plainly first, and any key that resolved to
                # another is rewritten to what it resolved to.
                try:
                    S.local().marks_moved = {}
                    plain = dict(q)
                    plain["byseason"] = ["0"]
                    S.local().handle("/library/watchlist", plain)
                    moved = dict(getattr(S.local(), "marks_moved", {}) or {})
                except Exception:
                    moved = {}
                if moved:
                    stored = self.settings_file()
                    mine = self.viewer_settings(stored)
                    was = [str(k) for k in (mine.get("watchlist") or [])]
                    now, seen = [], set()
                    for k in was:
                        k = moved.get(k, k)
                        if k not in seen:
                            seen.add(k)
                            now.append(k)
                    if now != was:
                        mine["watchlist"] = now
                        # when each was put on goes with it to its new key
                        at = mine.get("watchlistAt")
                        if isinstance(at, dict):
                            for old, new in moved.items():
                                if old in at:
                                    at[new] = max(int(at.pop(old) or 0), int(at.get(new) or 0))
                        S.write_settings(stored, merge=False)
                        q["keys"] = [",".join(now)]
                # and gathered into seasons for the same reason the shuffle is:
                # starring a season writes a mark on each of its episodes
                q["byseason"] = ["1"]
                # and when each was put on, for the order it opens in
                at = self.viewer_settings(self.settings_file()).get("watchlistAt")
                q["listed"] = [json.dumps(at if isinstance(at, dict) else {})]
            if sub == "/library/favorites":
                # the same shelf drawing as the watchlist, over this viewer's favourites
                sub = "/library/watchlist"
                mine = self.viewer_settings(self.settings_file())
                q["keys"] = [",".join(str(k) for k in (mine.get("favorites") or []))]
                q["byseason"] = ["1"]
            if sub == "/library/collections":
                # The shelves themselves, drawn as things with posters so a client can
                # show a row of them the way it shows a row of films. The key says
                # which shelf rather than which title: nothing in the library has one
                # like it, so nothing can open it by mistake.
                rows = []
                con = S.local().lib.db()
                try:
                    for c in self.collections():
                        keys = self.collection_keys(con, c)
                        face, art = self.shelf_face(con, c, keys)
                        rows.append({"ratingKey": "coll:" + str(c.get("id") or ""),
                                     "type": "collection",
                                     "title": str(c.get("name") or ""),
                                     "thumb": art, "childCount": len(keys),
                                     # where the round on this shelf was left, so the
                                     # button can say Resume rather than Play
                                     "resumeAt": self.shuffle_resume(c.get("id")),
                                     # the sort and filters it opens with
                                     "view": c.get("view") or {},
                                     # a series' own, with a shuffle: its ribbon
                                     "seriesShelf": bool(S.shelf_series(con, c)),
                                     "shuffle": ("coll:" + str(c.get("id") or ""))
                                                if S.shelf_series(con, c) else "",
                                     "leafCount": len(keys)})
                finally:
                    con.close()
                self.reply_json({"MediaContainer": {"size": len(rows),
                                                    "Metadata": rows}})
                return
            if sub == "/library/collection":
                # what one shelf holds, in the order it reads: oldest first, because a
                # collection is usually a series of films
                want = (q.get("id") or [""])[0]
                want = want[5:] if want.startswith("coll:") else want
                shelf = next((c for c in self.collections()
                              if str(c.get("id")) == want), None)
                con = S.local().lib.db()
                try:
                    # the same list the shuffle draws from: a programme on a
                    # collection is its episodes, the ones here and the ones a pack
                    # can give, rather than one row standing for all of them
                    keys = self.shelf_pool(con, shelf) if shelf else []
                    whole = self.whole_series(con, shelf) if shelf else []
                    # films oldest first, because a collection of them is usually a
                    # series; a programme's episodes in season and episode order after
                    # them, each programme whole. Asking every key for its year meant
                    # six hundred lookups through the packs to sort a list that is
                    # about to be folded into seasons anyway.
                    places, _ = S.local().episode_places(con, keys)
                    shows = {}

                    def in_order(key):
                        where = places.get(key)
                        if not where:
                            return (0, int((S.local().metadata_for(con, key, brief=True)
                                            or {}).get("year") or 0), 0, 0)
                        shows.setdefault(where[0], len(shows))
                        return (1, shows[where[0]], where[1], where[2])

                    keys.sort(key=in_order)
                    # what the shuffle span leaves out, marked on the cards
                    unshuffled = ([] if not shelf else
                                  sorted(set(keys) - set(self.shuffle_part(con, shelf, keys))))
                finally:
                    con.close()
                q["unshuffled"] = [",".join(unshuffled)]
                sub = "/library/watchlist"
                q["keys"] = [",".join(keys)]
                # By season, now that what a collection holds is episodes. Grouping
                # was turned off when the keys were programmes, because it broke one
                # programme into "Season 28" and "Season 36" standing on their own;
                # over episodes it does the opposite, and folds six hundred of them
                # back into the seasons they belong to.
                q["byseason"] = ["1"]
                # a series the shelf holds whole: every season a card, linked or not
                q["whole"] = [",".join(whole)]
            m = re.match(r"^/parts/(\d+)$", sub)
            if m:
                real = S.local().part(m.group(1))
                if not real:
                    self.send_error(404)
                    return
                # The file itself, which is the one way round a ceiling: a page
                # opened before the limit was set knows nothing about it and asks
                # for the original. A ceiling that only the client honours is not a
                # ceiling, so this is refused here and the client falls back to the
                # transcoder, which is held to the limit like everything else.
                too_big = self.too_big_to_send(m.group(1))
                if too_big:
                    self.refused(path, too_big)
                    self.send_error(403, too_big)
                    return
                # a direct play is the cheapest thing this server does, and still the
                # heaviest thing on the line, so it belongs in the list too.
                # The machine that keeps copies reads files the same way, and read as
                # a viewing it says somebody is watching a film in another room.
                # It calls itself a follower; this asked whether it called itself a
                # cache, so every file it pulled was written down as somebody watching
                # - at the speed of a copy, and never giving way to a real viewer.
                said = self.app_name().lower()
                its_key = S.INVITES.check(self.bearer(), self.app_name())
                taking = (said.startswith(("cache", "follower"))
                          or (its_key is not None
                              and S.Handler.role_of(its_key) == "cache"))
                # and it is named as the machine it is, not as an address in the
                # house that appears to be watching something
                who = self.watcher()
                if taking:
                    who = (S.Handler.STANDBY.get("name")
                           or S.Handler.host_of(S.Handler.STANDBY.get("where") or "")
                           or "another server")
                # why the cache wanted this one, worked out when the queue was
                # made and kept against the key until the file goes out
                fileKey = S.local().key_for_file(m.group(1))
                why, asked = S.Handler.WHY_BY_KEY.get(str(fileKey or ""), ("", ""))
                # a copy carries on from where its part file ends: where it starts,
                # so the list can say how far into the film it has got
                begin = 0
                if taking:
                    got = re.match(r"bytes=(\d+)-", str(self.headers.get("Range") or ""))
                    begin = int(got.group(1)) if got else 0
                if not taking:
                    S.played_file(fileKey, real)
                # a copy to the other machine takes a turn on the drive it reads, like
                # every other job: one at a time a drive, what is on a screen first
                turn = None
                if taking:
                    turn, busy = S.transfer_turn(real, why == "on a screen now",
                                               "sync-%s" % fileKey, "Copy: " + os.path.basename(real))
                    if busy:
                        self.send_response(503)
                        self.send_header("Retry-After", "30")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                sid = S.WATCHING.start(who, S.local().title_for_file(m.group(1)),
                                     S.local().facts_for_file(m.group(1)),
                                     "syncing" if taking else "direct play",
                                     self.client_address[0],
                                     fileKey,
                                     self.app_name(), self.device_kind(),
                                     why=why, asked_for=self.name_of(asked),
                                     path=m.group(1), offset=begin,
                                     size=os.path.getsize(real) if taking else 0)
                try:
                    self.send_file_ranged(real, sid)
                finally:
                    S.WATCHING.stop(sid)
                    if turn:
                        import pd_disk
                        pd_disk.done(turn)
                return
            status, ctype, body = S.local().handle(sub, q)
            # A poster this machine has no row for: a copy lists the main server's whole
            # catalogue but holds rows only for what it keeps, so every other card stood
            # blank here. Asked of the main server once, and kept.
            if status == 404 and re.match(r"^/art/[0-9a-z]+/(poster|backdrop)$", sub):
                got = self.art_from_the_house(sub, q)
                if got:
                    status, ctype, body = 200, "image/jpeg", got
            # the title a progress report is about, and nothing for every other route
            # through here: read for all of them, it was a name that did not exist yet
            watched_key = ""
            # a subtitle that carried an episode to the end is one that fits - and
            # "the end" is where the credits start, not the last second of them
            if sub == "/:/timeline":
                watched_key = (q.get("ratingKey") or [""])[0]
                using = (q.get("sub") or [""])[0]
                try:
                    at = float((q.get("time") or ["0"])[0]) / 1000.0
                except (TypeError, ValueError):
                    at = 0.0
                if watched_key.startswith("e") and at >= self.AHEAD_AT_S:
                    self.subtitle_ahead(watched_key)
                if watched_key:
                    # when this subtitle came on. A different one from a moment ago
                    # starts the clock again, which is what stops a track chosen in
                    # the last minute from being credited with the whole film.
                    was = S.SUB_SINCE.get(watched_key) or self.subtitle_since(watched_key)
                    if not was or was[0] != using:
                        # A subtitle swapped for another during a watching we were
                        # following is a choice, and its position counts. The first
                        # sighting of one - a resume, or the first request after a
                        # restart - says nothing about when it came on, and taking the
                        # position it happened to arrive at as the moment it was chosen
                        # is what refused a whole episode watched with subtitles on.
                        sure = bool(was)
                        S.SUB_SINCE[watched_key] = (using, at if sure else None)
                        self.remember_since(watched_key, using, at, sure)
                        if len(S.SUB_SINCE) > 40:
                            for old in list(S.SUB_SINCE)[:20]:
                                S.SUB_SINCE.pop(old, None)
                    else:
                        S.SUB_SINCE[watched_key] = was
                if watched_key and S.local().seen_through(watched_key):
                    known = S.SUB_SINCE.get(watched_key) or self.subtitle_since(watched_key)
                    told = self.promote_subtitle_choice(
                        watched_key, using,
                        since=known[1] if known and known[0] == using else None)
                    # and the answer carries the news
                    if told and ctype.startswith("application/json"):
                        try:
                            doc = json.loads(body.decode("utf-8"))
                            doc.setdefault("MediaContainer", {})["subsVerified"] = told
                            body = json.dumps(doc).encode("utf-8")
                        except Exception:
                            pass
            # What the sound of this file needs now, so a player can be corrected
            # without being restarted. The gain used to be settled when the film was
            # opened, so a file measured while it was playing stayed uncorrected to
            # the end and a player that had never been told could not be told.
            if watched_key and ctype.startswith("application/json"):
                try:
                    src = S.local().file_for(watched_key, 0) or {}
                    if src.get("path"):
                        doc = json.loads(body.decode("utf-8"))
                        doc.setdefault("MediaContainer", {})["gainDb"] = S.volume_gain(
                            src.get("id"), src.get("path"), src.get("size") or 0,
                            playing=True)
                        # and how loud it was found to be, for the line along the top:
                        # a file measured while it was playing had the number on the
                        # server and nothing on the screen
                        known = S.read_loudness().get(str(src.get("id")))
                        if known and known.get("lufs") is not None:
                            doc["MediaContainer"]["lufs"] = float(known["lufs"])
                        body = json.dumps(doc).encode("utf-8")
                except Exception:
                    pass
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            body = self.squeezed(body, ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if ctype.startswith("application")
                             else "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        return (q, )

    def _get_part_14(self, path, q):
        """GET routes, part 14 of 16:
        /library/scan, /advanced, /skipstart, /watch/services, /library/status, /tizen.zip,
        /auth/state, /setup/state, /library/config, /gpu/hls, and 2 more
        """
        if path == "/library/scan":
            # An address somebody can open to start a scan - and anything that opens
            # an address can open it again. Without this it started another scan on
            # every visit, and several walks of the same folders writing to one index
            # is where "database is locked" comes from.
            lib = S.local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"started": False, "why": "a scan is already running",
                                 "scan": lib.scan_state})
                return
            import threading as _t
            _t.Thread(target=lambda: lib.scan(), daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/advanced":
            self.reply_json({"groups": self.settled_numbers()})
            return

        if path == "/skipstart":
            # the skip rules, the programmes a rule can be set for, the listen job
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_leads
            shows = {}
            con = S.local().lib.db()
            try:
                for r in con.execute(
                        "SELECT i.id, i.title, e.season FROM item i JOIN episode e ON e.item_id=i.id "
                        "JOIN file f ON f.episode_id=e.id WHERE i.type='show' AND e.season>0 "
                        "GROUP BY i.id, e.season ORDER BY i.sort_title, e.season"):
                    shows.setdefault(str(r["id"]), {"key": str(r["id"]), "title": r["title"],
                                                    "seasons": []})["seasons"].append(int(r["season"]))
            finally:
                con.close()
            self.reply_json({"rules": self.lead_rows((S.read_settings() or {}).get("skipStart")),
                             "shows": list(shows.values()), "most": pd_leads.MOST_SECS,
                             "listening": pd_leads.state()})
            return
        if path == "/watch/services":
            # the countries the catalogue knows services for, and one country's
            # services, for choosing among under Settings
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_streaming
            asked = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (asked.get("region") or [""])[0].strip().upper()
            if code and not re.match(r"^[A-Z]{2}$", code):
                code = ""
            lib = S.local().lib
            self.reply_json({
                "region": code,
                "regions": pd_streaming.watch_regions(lib),
                "services": [dict(s, logo=("/art/provider/" + s["logo"].strip("/"))
                                  if re.match(r"^/[A-Za-z0-9_-]+\.(png|jpg|jpeg)$", s["logo"])
                                  else "")
                             for s in pd_streaming.watch_services(lib, code)]})
            return
        if path == "/library/status":
            said = S.local().lib.stats()
            try:
                import pd_torrents
                said["offered"] = len(pd_torrents.offered())
            except Exception:
                said["offered"] = 0
            # every drive the library and the downloads live on: how much is free
            drives = {}
            try:
                import pd_torrents
                cfg = S.local().lib.config()
                places = list(cfg.get("movies") or []) + list(cfg.get("tv") or []) +                     list(cfg.get("mixed") or []) + [pd_torrents.save_folder()]
                for place in places:
                    drive = os.path.splitdrive(os.path.abspath(str(place)))[0].upper()
                    if not drive or drive in drives:
                        continue
                    try:
                        use = shutil.disk_usage(drive + os.sep)
                    except OSError:
                        continue
                    drives[drive] = {"drive": drive, "free": round(use.free / 1e9),
                                     "total": round(use.total / 1e9)}
            except Exception:
                pass
            said["disks"] = sorted(drives.values(), key=lambda d: d["drive"])
            self.reply_json(said)
            return
        if path == "/tizen.zip":
            # The source of a Samsung TV app, with this server's address written into
            # it. Not a signed package: Samsung will not install one that has not been
            # signed with a certificate belonging to a person, so the last two steps
            # are Tizen Studio's and cannot be done here.
            q = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                 if "?" in self.path else {})
            token = (q.get("t") or [""])[0]
            where = "http://%s:%d" % (S.LAN_IP, S.PORT)
            if token:
                where += "/s/" + token
            import io as _io
            import zipfile
            out = _io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for name in ("config.xml", "index.html"):
                    with open(os.path.join(S.STATIC, "tizen", name),
                              encoding="utf-8") as f:
                        z.writestr(name, f.read().replace("__SERVER__", where))
                with open(os.path.join(S.STATIC, "tizen", "icon.png"), "rb") as f:
                    z.writestr("icon.png", f.read())
            body = out.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             "attachment; filename=palladium-tizen.zip")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/auth/state":
            self.reply_json({
                "password": self.password_set(),
                "owner": self.role == "owner",
                "local": self.client_address[0] in ("127.0.0.1", "::1"),
                "proxied": self.through_a_proxy(),
            })
            return
        if path == "/setup/state":
            # What a new install is still waiting for. One answer rather than four
            # requests, because the page asks for it every second while it works.
            cfg = S.local().lib.config()
            folders = ((cfg.get("movies") or []) + (cfg.get("tv") or [])
                       + (cfg.get("mixed") or []))
            stats = S.local().lib.stats()
            stored = S.read_settings() or {}
            self.reply_json({
                "folders": folders,
                "tmdb": bool((cfg.get("tmdb_key") or "").strip()),
                "ffmpeg": S.ffmpeg_now(),
                # the cache on this disk, in use or let go of: a row that vanished
                # when it was fetched left nowhere to say either
                "ffmpegHave": S.ffmpeg_in_home(),
                "ffmpegFrom": S.FFMPEG_FROM.get(os.name) or "",
                "engine": S.engine_name(),
                "fetching": dict(S.FETCHING),
                "films": stats.get("movies", 0),
                "episodes": stats.get("episodes", 0),
                "scanning": bool(S.local().lib.scan_state.get("running")),
                "done": bool(stored.get("setupDone")),
                "password": self.password_set(),
                "packaged": S.PACKAGED,
            })
            return
        if path == "/library/config":
            # with the name this machine answers to, so a settings page opened
            # against it can say whose settings are on the screen
            import pd_claude
            cfg = S.local().lib.config()
            # and for the Claude row: whether Claude Code is here, and what source it reads
            self.reply_json(dict(cfg, serverName=self.server_name(),
                                 claudeHere=bool(pd_claude.find()),
                                 claudeReads=pd_claude.source(
                                     S.CODE, str(cfg.get("claudeSource") or ""))))
            return
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        # Safari's route: a playlist and segments rather than one long response
        m = re.match(r"^/gpu/hls/(hls\d+)/([A-Za-z0-9_.-]+)$", path)
        if m:
            self.gpu_hls_file(m.group(1), m.group(2))
            return
        if path == "/gpu/hls":
            self.gpu_hls(q)
            return
        if path == "/rip/remuxes":
            # every film with a remux, for the ripper's own page: the files that can be ripped
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_rip
            con = S.local().lib.db()
            try:
                rows = con.execute(
                    "SELECT i.id, i.title, i.year, f.path, f.size, f.bitrate, f.height, f.vcodec, f.mtime "
                    "FROM file f JOIN item i ON i.id = f.item_id WHERE f.episode_id IS NULL "
                    "AND i.type = 'movie' ORDER BY i.sort_title").fetchall()
            finally:
                con.close()
            films = {}
            for key, title, year, fpath, size, rate, height, codec, mtime in rows:
                if not pd_rip.is_remux(fpath, float(rate or 0) / 1000):
                    continue
                one = films.setdefault(key, {"key": key, "title": title, "year": year, "files": [], "added": 0})
                one["files"].append({"path": fpath, "gb": round(float(size or 0) / 1e9, 1),
                                     "mbit": round(float(rate or 0) / 1000, 1), "height": height,
                                     "codec": codec or ""})
                one["added"] = max(one["added"], int(mtime or 0))
            self.reply_json({"films": list(films.values())})
            return
        if path == "/offline/jobs":
            # how far the copies a device asked for have got
            import pd_offline
            ids = [x for x in (q.get("ids") or [""])[0].split(",") if x]
            who = self.offline_who()
            mine = [j for j in pd_offline.jobs(ids)
                    if self.role == "owner"
                    or (pd_offline.load()["jobs"].get(j["id"]) or {}).get("who") == who]
            self.reply_json({"jobs": mine})
            return
        if path == "/offline/file":
            # a finished copy, in ranges, so a download cut off goes on where it stopped
            import pd_offline
            ident = (q.get("id") or [""])[0]
            job = pd_offline.load()["jobs"].get(ident) or {}
            where = pd_offline.file_of(ident)
            if not where or not (self.role == "owner" or job.get("who") == self.offline_who()):
                self.send_error(404)
                return
            self.send_file_ranged(where)
            return
        if path == "/rip":
            # the rips of one title, or all of them: state and how far, for the film page
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_rip
            key = (q.get("key") or [""])[0]
            said = pd_rip.state(key or None)
            # the profiles starred on the ripper's page: a button each on the film page
            self.reply_json({"jobs": said.get("jobs") or [], "profile": said.get("profile") or "",
                             "offered": said.get("offered") or ([said["profile"]] if said.get("profile") else []),
                             "away": bool(said.get("away"))})
            return
        if path == "/watchlist/blink":
            # what on this viewer's watchlist has come in since they last opened it: the
            # Watchlist heading blinks while there is any
            # on the copy, as the main server keeps them; its own when that one does not answer
            said = pd_notify.ask_main(self.viewer(), "blink")
            self.reply_json(said if isinstance(said, dict) and "keys" in said
                            else {"keys": pd_notify.blinking(self.viewer_settings(self.settings_file()))})
            return
        if path == "/notify":
            # the Watchlist blinks this viewer's app has not posted as notifications yet,
            # each handed over once
            said = pd_notify.ask_main(self.viewer(), "notes")
            if isinstance(said, dict) and "notes" in said:
                self.reply_json({"notes": said["notes"]})
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            had = bool(mine.get("notifyQueue"))
            notes = pd_notify.notes_once(mine)
            if had:
                S.write_settings(stored, merge=False)
            self.reply_json({"notes": notes})
            return
        if path == "/watchlist/news":
            # a film on this viewer's watchlist that has got a source since they were
            # last told: each said once, on whichever screen asks first
            said = pd_notify.ask_main(self.viewer(), "news")
            if isinstance(said, dict) and "news" in said:
                self.reply_json({"news": said["news"]})
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            had = bool(mine.get("watchNews"))
            news = pd_notify.news_once(mine)
            if had:
                S.write_settings(stored, merge=False)
            self.reply_json({"news": news})
            return
        if path == "/torrents/ready":
            # what this viewer asked for that has come in since `since`, for the line
            # each screen says when it is ready; without `since` only the clock, so a
            # screen starts from now and not from everything ever downloaded
            import pd_torrents
            now = int(time.time())
            try:
                since = float((q.get("since") or [""])[0])
            except ValueError:
                self.reply_json({"now": now, "ready": []})
                return
            mine = {self.bearer() or "me"} | ({"me"} if self.role == "owner" else set())
            ready = pd_torrents.ready_for(mine, since)
            # and what was taken straight from the tracker, which is not a pack's film
            try:
                import pd_tracker
                ready += pd_tracker.ready_for(mine, since, S.local().lib)
            except Exception:
                pass
            # one this viewer downloaded and so has on their watchlist is said by the
            # watchlist's own line, "now on your watchlist" - not twice (owner, 2026-11-06)
            ready = [r for r in ready if str(r.get("key") or "") not in self.fetched_here()]
            self.reply_json({"now": now, "ready": ready})
            return
        if path == "/torrents/totals":
            # both machines' torrent speeds and the tracker ratio, for Now playing: off the
            # race page, which a browser away from home cannot reach itself
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_speed
            self.reply_json(pd_speed.totals(S.other_server()[0], time.time()))
            return
        if path == "/merge":
            # the link merge tool's links, speeds and totals, for Now playing: asked of
            # the tool on this computer, which a browser elsewhere cannot reach itself
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json(pd_torrents.merge_stats())
            return
        if path == "/torrents/active":
            # what is coming in now, for the line in the menu: everyone's for the owner,
            # a guest's own otherwise
            import pd_torrents
            mine = None if self.role == "owner" else (self.bearer() or "me")
            # and what a cache server is downloading for the house: a pack that lives there
            # is fetched there, and was on no list here (2026-11-06)
            got = S.with_cache_downloads(pd_torrents.active(mine), mine)
            # and a rip being made or waiting, the owner's: on the same line as a download,
            # on the web, in the app and on the panel (owner, 2026-11-06)
            if self.role == "owner":
                try:
                    import pd_rip
                    got = got + pd_rip.line_rows()
                except Exception:
                    pass
            # the panel asks for copies to the other server as well: the owner's only
            if "sync=1" in self.path and self.role == "owner":
                got = got + [{"title": "Copy: " + str(c["name"])[:40], "offer": {
                    "state": "downloading", "progress": c["progress"], "mbit": c["mbit"],
                    "eta": c["eta"], "who": c["who"]}} for c in S.copies_coming(S.WATCHING.snapshot())]
            # and what is coming straight from the tracker, which is not a pack's
            try:
                got = got + S.tracker_coming(self.role == "owner",
                                           self.name_of(self.bearer() or "me"))
            except Exception:
                pass
            self.reply_json({"MediaContainer": {"size": len(got), "Metadata": got}})
            return
        if path == "/proxy":
            # how this server is reached from outside: the port forwarded in the
            # router, which is the default, or Caddy with a name and a certificate
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_proxy
            self.reply_json(pd_proxy.state())
            return
        return (q, )

    def _get_part_15(self, path, q):
        """GET routes, part 15 of 16:
        /torrents/contents, /torrents, /torrents/log, /follow/torrents, /follow/offers,
        /follow/get, /follow/source, /gpu/begins, /gpu/stream, /gpu/subs
        """
        if path == "/torrents/contents":
            # what one pack holds, film by film, and where each stands here
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            want = (q.get("hash") or [""])[0].lower()
            pack = next((p for p in pd_torrents.load()["packs"]
                         if str(p.get("hash")).lower() == want), None)
            if not pack:
                self.reply_json({"films": [], "why": "no such pack"})
                return
            owned = pd_torrents._owned()
            out = []
            for f in pack.get("films") or []:
                was = pd_torrents.latest_download(f.get("key")) or {}
                state = ("here" if pd_torrents.held_here(f, owned)
                         else was.get("state") if was.get("state") in ("queued", "downloading", "done")
                         else "offered" if f.get("tmdb") else "not matched")
                name = str(f.get("name") or f.get("title") or "")
                if f.get("kind") == "episode":
                    name += " S%02dE%02d" % (int(f.get("season") or 0), int(f.get("episode") or 0))
                out.append({"key": f.get("key"), "title": name, "year": f.get("year"),
                            "size": int(f.get("size") or 0), "state": state,
                            "file": os.path.basename(str(f.get("path") or ""))})
            out.sort(key=lambda r: (str(r["title"]).lower(), r.get("year") or 0))
            self.reply_json({"name": pack.get("name"), "films": out})
            return
        if path in ("/torrents", "/torrents/log"):
            # films offered from torrent packs, and every download somebody asked for
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            said = (pd_torrents.status() if path == "/torrents"
                    else {"downloads": S.log_rows(pd_torrents.downloads() + S.tracker_takes(),
                                                q.get("done", [""])[0])})
            if path == "/torrents":
                # whether the two servers are on different connections, and what is
                # paused because of it
                try:
                    import pd_apart
                    said["apart"] = pd_apart.status()
                    said["apartPause"] = str((S.read_settings() or {}).get("apartPause")
                                             or "host")
                except Exception:
                    pass
            self.reply_json(said)
            return
        if path == "/follow/torrents":
            # the packs offered here, for a cache to offer the same films while this
            # machine is off; the torrent itself only for a pack it does not hold yet
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            if q.get("ping"):
                self.reply_json({"ok": True})
            else:
                self.reply_json(pd_torrents.mirror(
                    set(h for h in q.get("have", [""])[0].split(",") if h)))
            return
        if path == "/follow/offers":
            # the films on offer here matching a search typed on a cache, which offers
            # none of its own while this machine is up
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            from pd_localapi import bare_in, near
            spelt = q.get("q", [""])[0]
            self.reply_json({"films": [o for o in pd_torrents.offered()
                                       if bare_in(spelt, o.get("title"))
                                       or near(spelt, o.get("title")) >= 0.8][:60]})
            return
        if path == "/follow/active":
            # what this cache is downloading, for the main server's download line: its
            # own /torrents/active is refused to a caller marked as the main server, which
            # may reach only /follow, so the line never showed what came in here (2026-11-06)
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if self.role != "owner" and not (invite and invite.get("follows")):
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            got = pd_torrents.active(None)
            self.reply_json({"MediaContainer": {"size": len(got), "Metadata": got}})
            return
        if path == "/follow/live":
            # who this server is sending films to, for the other's "two sources" mark
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if self.role != "owner" and not (invite and invite.get("follows")):
                self.send_error(403, "not allowed")
                return
            self.reply_json({"live": [{"address": r.get("address"), "key": r.get("key"), "how": r.get("how"),
                                       "mbit": r.get("mbit")}
                                      for r in S.WATCHING.snapshot()]})
            return
        if path == "/follow/recent":
            # the films held here, for the one Recently added list both servers show
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if self.role != "owner" and not (invite and invite.get("follows")):
                self.send_error(403, "not allowed")
                return
            import socket as _s
            con = S.local().lib.db()
            try:
                films = S.local().held_recent(con)
                dated, music = S.local().held_dates(con)
            finally:
                con.close()
            self.reply_json({"films": films, "dated": dated, "music": music,
                             "name": str(S.local().lib.config().get("serverName") or "") or _s.gethostname()})
            return
        if path == "/follow/slot":
            # what this server is downloading, for the other: one at a time in the house
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if self.role != "owner" and not (invite and invite.get("follows")):
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json(pd_torrents.slot_report())
            return
        if path == "/follow/took":
            # the copy server saying which requests for its packs it has taken
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json(pd_torrents.copy_took(q.get("ids", [""])[0].split(",")))
            return
        if path == "/follow/get":
            # a download pressed on a cache: fetched here, under the name it was asked by,
            # with the limits the cache worked out for them
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            one = lambda name, fallback="": q.get(name, [fallback])[0]
            try:
                cap, most = float(one("cap", "0") or 0), float(one("most", "0") or 0)
            except ValueError:
                cap = most = 0.0
            self.reply_json(pd_torrents.request(one("key"), one("token") or "me",
                                                one("who"), cap, most))
            return
        if path == "/follow/source":
            # what a file is and the part it is read by, for a cache asked to play a
            # title it holds no copy of: it encodes with its own encoder, off this file
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            found = S.local().file_for(q.get("key", [""])[0], int(q.get("mi", ["0"])[0] or 0))
            if not found or not found.get("part"):
                self.send_error(404, "no such file here")
                return
            self.reply_json({k: v for k, v in found.items() if k != "file"})
            return
        if path in ("/gpu/begins", "/gpu/stream", "/gpu/subs") and q.get("src", [""])[0] == "local":
            # made on the connected computer when this one is set to, while it answers
            if path != "/gpu/subs" and self.encode_elsewhere(q):
                return
            # a title this copy holds no file for: its own encoder reads the main server's
            # file. Subtitles, which mean reading the whole file, are cut by the main server.
            if (path == "/gpu/subs" or not self.house_source(q)) and self.send_to_the_house(q):
                return
        if path == "/gpu/begins":
            # where an app's encode will start, and whether its picture goes through
            self.reply_json(self.encode_begins(q))
            return
        if path == "/gpu/stream":
            self.gpu_stream(q)
            return
        return ()
