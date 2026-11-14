"""The POST routes: every path a request can post to, in the order they are tried. Handler._do_POST walks them.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

import pd_h
import pd_log
import pd_notify
import pd_repair

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class PostRoutes(_Base):
    def _post_part_01(self, path):
        """POST routes, part 1 of 20:
        /login, /logout, /settings, /invites/role, /invites, /party
        """
        path = self.path.split("?")[0]
        self.role = self.who(path)
        # What a guest may write is what belongs to them: their own subtitle settings,
        # the track they chose, a subtitle fetched for a film they are watching, and a
        # report. Everything else here changes the library, the invitations, or files
        # on disk, and belongs to whoever owns the server.
        GUEST_MAY_POST = ("/applog", "/log", "/trace", "/feedback", "/watchlist/seen",
                          "/feedback/cancel", "/settings",
                          "/subs/pick", "/copy/pick",
                          "/subs/find", "/subs/get", "/subs/auto", "/subs/sync",
                          # making one out of the soundtrack is the same kind of act
                          # as fetching one: it is a subtitle for what they are watching
                          "/subs/make", "/subs/stop",
                          "/subs/plan",
                          # vouching for a subtitle is a fact about the file, and the
                          # person who just watched it is the one who knows. Removing
                          # or hiding one is not on this list: those are the owner's.
                          "/subs/verify", "/subs/reset",
                          # their own shelves, and trying a rule before saving it
                          "/watchlist", "/favorites", "/collections", "/collections/test",
                          "/collections/season",
                          # a title on or off a shelf, and a shelf played in shuffle: the
                          # round belongs to the person, and the owner is a guest from outside
                          "/collections/for", "/collections/mark",
                          # what the Start tab should suggest them more, or never again
                          "/start/feedback",
                          # the look they wear, theirs on every screen - the owner from
                          # away pressed Theme and nothing happened (2026-11-05)
                          "/skins",
                          # a download they asked for, stopped by them, or moved in the queue
                          "/torrents/cancel", "/torrents/order",
                          "/collections/shuffle", "/collections/shuffle/queue",
                          "/collections/shuffle/reset", "/collections/shuffle/undraw",
                          "/collections/shuffle/note",
                          # a guest is a person in the room, and a room where only the
                          # owner may speak is not a conversation
                          "/chat", "/party",

                          # what a screen has left to play, which is the screen's own
                          # business and the one thing only it knows
                          "/stream/buffer",
                          # one film from a torrent pack, within their week's limit
                          "/torrents/get",
                          # asking for a film the house has not got: written down with who
                          # asked, never fetched. The app showed guests Request and every
                          # press was refused (2026-11-06)
                          "/requests",
                          # what the tracker is carrying for one film, and fetching
                          # one of them. Reachable rather than allowed: each of these
                          # decides for itself whether this person may, by the key
                          # they hold and by what the house has been opened up to.
                          # Behind this door they were refused before the question was
                          # even asked - which is how the owner, away from home, was
                          # shown a picker that never filled.
                          "/tracker/list", "/tracker/get",
                          "/tracker/waiting", "/tracker/grab",
                          "/tracker/gaps", "/tracker/season", "/tracker/fillseason",
                          "/shows/top", "/shows/stock", "/shows/packs", "/shows/usepack",
                          "/shows/withpacks",
                          "/shows/add", "/shows/addpack",
                          # and stopping one they started, which whoever may fetch may do
                          "/tracker/active", "/tracker/cancel",
                          # a copy of a title for their own device to keep, and letting it go
                          "/offline/make", "/offline/got", "/offline/drop",
                          # its sound going through to the receiver, from the house
                          "/receiver/passthrough")
        # Giving the password is how somebody who is nobody becomes the owner, so
        # it cannot be behind the check for being the owner. Its own rate limit is
        # what guards it.
        if path in ("/login", "/logout"):
            pass
        elif self.role != "owner" and not (self.role == "guest" and
                                           path in GUEST_MAY_POST):
            self.refused(path, "not something a guest may write")
            self.send_error(403, "not allowed")
            return
        # and one thing a guest must not do even on a path they may use: change what
        # every viewer gets
        if self.role == "guest" and path == "/settings":
            peek = self.read_json() or {}
            if ("autoNext" in peek or "quality" in peek or "nextDelay" in peek
                    or "fetchMaxGb" in peek or "letThemFetch" in peek
                    or "outsideName" in peek
                    or "preferRes" in peek or "preferBy" in peek
                    or "preferGroup" in peek or "preferOrder" in peek):
                self.send_error(403, "that setting belongs to the server")
                return
            self.settings_body = peek
        if path == "/invites/role":
            # What one key is for, changed on the key itself. The flags underneath it
            # are kept in step so everything that asks "does this key follow us" goes
            # on working; the role is the thing somebody sets.
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            role = str(body.get("role") or "")
            if role not in S.Handler.ROLES:
                self.reply_json({"error": "no such role"}, 400)
                return
            if role == "cache":
                # it has to be a machine: one that has announced itself to this server
                # with that key and is not stopped. A person's key cannot be a cache.
                known = any(str(v.get("token") or "") == token
                            for v in S.Handler.FOLLOWERS.values())
                if not known:
                    self.reply_json({"error": "That key has not been used by a server "
                                              "yet. Point the other machine at this one "
                                              "with that invitation first."}, 400)
                    return
            rows = S.INVITES.load()
            for row in rows:
                if row.get("token") != token:
                    continue
                row["role"] = role
                row["follows"] = role == "cache"
                if not row["follows"]:
                    row.pop("follows", None)
                S.INVITES.save(rows)
                self.reply_json({"ok": True, "role": role})
                return
            self.reply_json({"error": "no such key"}, 404)
            return
        if path == "/invites":
            body = self.read_json()
            row = S.INVITES.create((body.get("name") or "").strip()[:40],
                                 int(body.get("days") or 0),
                                 (body.get("email") or "").strip()[:120])
            # Two kinds of key from one door. A guest is a person; a machine key lets
            # another server read this library and keep copies of it.
            kind = str(body.get("kind") or "guest")
            if kind == "machine":
                rows = S.INVITES.load()
                for r in rows:
                    if r["token"] != row["token"]:
                        continue
                    r["follows"] = True
                    if body.get("cap") is not None:
                        try:
                            r["cap"] = max(0.0, float(body.get("cap") or 0))
                        except (TypeError, ValueError):
                            r["cap"] = 0.0
                    if body.get("mayCopy") is not None:
                        r["mayCopy"] = bool(body.get("mayCopy"))
                    S.INVITES.save(rows)
                    row = r
                    break
            self.reply_json(dict(self.with_link(row), kind=kind))
            return
        if path == "/party":
            # Starting one is the owner's; joining is anybody's, and is done by
            # reading rather than by pressing.
            body = self.read_json() or {}
            if "on" in body:
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                S.PARTY["on"] = bool(body["on"])
                S.PARTY["since"] = int(time.time()) if S.PARTY["on"] else 0
                if S.PARTY["on"]:
                    # what everybody is here to watch, if it was said
                    S.PARTY["key"] = str(body.get("key") or "")[:40]
                    S.PARTY["title"] = str(body.get("title") or "")[:120]
                if not S.PARTY["on"]:
                    S.PARTY["who"].clear()
                    S.PARTY["asked"].clear()
                    S.PARTY["title"] = S.PARTY["key"] = ""
                    # the party's own room goes; the lobby is not an evening and stays
                    S.CHAT[:] = [m for m in S.CHAT if m.get("room") == "lobby"][-S.CHAT_KEEP:]
                else:
                    self.join_party()
                S.CHAT_RUNG.set()
                S.CHAT_RUNG.clear()
            elif body.get("invite"):
                # asking somebody to watch: they see it wherever they are, and it
                # waits until they answer or the evening ends
                whom = body.get("invite")
                whom = whom if isinstance(whom, list) else [whom]
                for one in whom:
                    key = str(one or "").strip()[:80]
                    if key:
                        S.PARTY["asked"][key] = {
                            "from": self.watcher(),
                            "title": S.PARTY.get("title", "") or str(body.get("title") or ""),
                            "key": S.PARTY.get("key", "") or str(body.get("key") or ""),
                            "when": int(time.time())}
                S.CHAT_RUNG.set()
                S.CHAT_RUNG.clear()
            elif body.get("accept"):
                S.PARTY["asked"].pop(self.party_key(), None)
                self.join_party()
            elif body.get("decline"):
                S.PARTY["asked"].pop(self.party_key(), None)
            elif body.get("leave"):
                S.PARTY["who"].pop(self.party_key(), None)
            self.reply_json({"on": bool(S.PARTY["on"]), "since": S.PARTY["since"],
                             "people": len(S.PARTY["who"])})
            return
        return (path, )

    def _post_part_02(self, path):
        """POST routes, part 2 of 20: /chat, /notice, /screen/play, /machine."""
        if path == "/chat":
            # Anybody who may watch may speak: a guest is a person in the room, and a
            # room where only the owner talks is not a conversation.
            body = self.read_json() or {}
            if not self.wants_a_party():
                self.reply_json({"ok": False,
                                 "why": "Watch parties are off for you"}, 409)
                return
            room = str(body.get("room") or "party")
            if room != "lobby":
                if not S.PARTY["on"]:
                    self.reply_json({"ok": False,
                                     "why": "There is no watch party on"}, 409)
                    return
                self.join_party()
            words = str(body.get("text") or "").strip()[:300]
            if not words:
                self.reply_json({"ok": False, "why": "nothing to say"}, 400)
                return
            # Whose voice it is. A guest is known by their invitation; the owner
            # may say who they are, which is also how a screen with no name of its
            # own - a television in the front room - signs what it sends.
            from_who = self.watcher()
            if self.role == "owner":
                asked = str(body.get("from") or "").strip()[:40]
                if asked:
                    from_who = asked
            S.CHAT.append({"id": (S.CHAT[-1]["id"] + 1) if S.CHAT else 1,
                         "who": from_who, "from": from_who, "text": words,
                         # which room it was said in
                         "room": "lobby" if room == "lobby" else "party",
                         # the party by default; an address when somebody is
                         # talking to one screen, which is what a test is
                         "to": str(body.get("to") or
                                   ("all" if room == "lobby" else "party")).strip()[:60],
                         "when": int(time.time())})
            del S.CHAT[:-S.CHAT_KEEP]
            S.CHAT_RUNG.set()
            S.CHAT_RUNG.clear()
            self.reply_json({"ok": True, "id": S.CHAT[-1]["id"]})
            return
        if path == "/notice":
            # Sent to every screen in the house, and gone when it expires. The owner's:
            # nobody else's words belong on somebody else's television.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.reply_json(pd_notify.say(self.read_json() or {}))
            return
        if path == "/screen/play":
            # Open a title on a screen in the house, from here. There was no way to
            # do it at all: a television could be told a line of words and nothing
            # else, so anything that had to be played on it had to be found on it,
            # with the remote. Carried on the same held connection the notice uses,
            # so it lands the moment it is sent.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = str(body.get("key") or "").strip()[:40]
            # or a shuffle to carry on, as its card on the screen would: the round
            # resumes where it was, and Next draws from it
            shelf = str(body.get("shelf") or "").strip()[:60]
            if not key and not shelf:
                self.reply_json({"error": "which one?"}, 400)
                return
            # a title a shuffle is on goes as the shuffle: sent by name it played as an
            # ordinary viewing and went onto Continue watching (2026-11-13)
            if key and not shelf:
                import pd_localapi
                shelf = pd_localapi.shelf_on(
                    (self.viewer_settings(self.settings_file()) or {}).get("shuffles"), key)
                if shelf:
                    key = ""
            S.NOTICE["id"] = S.NOTICE.get("id", 0) + 1
            S.NOTICE["play"] = key
            S.NOTICE["shelf"] = shelf
            # for the screens listening now: a minute, and a screen opened after that is
            # not sent the film as well
            S.NOTICE["playUntil"] = time.time() + S.PLAY_FOR
            try:
                S.NOTICE["at"] = max(0, int(body.get("at") or 0))
            except (TypeError, ValueError):
                S.NOTICE["at"] = 0
            # a word with it, or none: "playing this on your television" is worth
            # saying when somebody else set it going
            S.NOTICE["text"] = str(body.get("text") or "").strip()[:200]
            S.NOTICE["until"] = time.time() + 30 if S.NOTICE["text"] else 0
            S.NOTICE["to"] = str(body.get("to") or "").strip()[:60]
            S.NOTICE_RUNG.set()
            S.NOTICE_RUNG.clear()
            self.reply_json({"id": S.NOTICE["id"], "play": key, "shelf": shelf,
                             "at": S.NOTICE["at"], "to": S.NOTICE["to"]})
            return
        if path == "/machine":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_machine
            body = self.read_json() or {}
            if "startup" in body:
                pd_machine.set_start_at_login(bool(body["startup"]))
            if "firewall" in body:
                # these put up Windows' own prompt, and wait for it
                if body.get("firewall"):
                    ok, why = pd_machine.open_firewall(S.PORT)
                else:
                    ok, why = pd_machine.close_firewall(S.PORT)
                if not ok:
                    self.reply_json(dict(pd_machine.state(S.PORT, S.LAN_IP), why=why), 200)
                    return
            if body.get("private"):
                # a network Windows calls public is shut to the main server whatever rule
                # is written, and this is the one thing that changes that
                ok, why = pd_machine.make_private()
                if not ok:
                    self.reply_json(dict(pd_machine.state(S.PORT, S.LAN_IP), why=why), 200)
                    return
            self.reply_json(pd_machine.state(S.PORT, S.LAN_IP))
            return
        return ()

    def _post_part_03(self, path):
        """POST routes, part 3 of 20:
        /update/install, /requests, /requests/done, /collections/shuffle,
        /collections/shuffle/queue
        """
        if path == "/update/install":
            # Fetch it, check it against the hash the site published, and hand it to
            # the installer. The answer goes out before the program is replaced,
            # because the program answering is the one about to be shut.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            if not self.in_the_house():
                self.reply_json({"ok": False,
                                 "why": "The server is replaced from a screen in the "
                                        "same house as it"}, 403)
                return
            import pd_update
            stored = self.settings_file()
            if not pd_update.packaged():
                self.reply_json({"ok": False,
                                 "why": "This copy runs from source; update it with git"},
                                400)
                return
            # one already on this machine, from a build made here: nothing to fetch,
            # nothing to check against a published hash - the file is the file
            near, path = pd_update.beside(self.build_version(),
                                          self.installer_folders())
            if near:
                self.reply_json({"ok": True, "version": near,
                                 "installer": os.path.basename(path)})
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                pd_update.install(path)
                return
            # The machine this one follows, if it has the installer and this one is
            # allowed to take it from there: the same file over the network the two
            # share, rather than 34 MB across the internet - and it guarantees the two
            # end up on the same build rather than both chasing what is published.
            body = self.read_json() or {}
            onto = ""
            said = {}
            # The library's settings, read so that a library which will not open does
            # not also stop the one thing that could mend it. The cache's first key
            # conversion left a half-made table behind, every start after that fell
            # over opening the library, and this door asked the library for its
            # settings before it did anything - so the build with the fix could not
            # be taken, by this door, by the tray, or by the cache asking it.
            cfg = self.library_settings_plain()
            if not body.get("fromSite"):
                try:
                    import pd_follow
                    onto, said = pd_follow.build_from_master(pd_follow.settings(cfg))
                except Exception:
                    onto, said = "", {}
                # never backwards from that side either: an old installer left beside
                # the main server put the cache back twenty-six versions
                here_now = self.build_version()
                if onto and here_now and not pd_update.newer(
                        str(said.get("version") or ""), here_now):
                    try:
                        os.remove(onto)
                    except OSError:
                        pass
                    onto, said = "", {}
            if not onto:
                if cfg.get("fetchFromSite") is False:
                    self.reply_json({"ok": False,
                                     "why": "This server is set not to fetch anything "
                                            "from palladium.video, and the machine it "
                                            "follows has no installer to give it"}, 409)
                    return
                said = pd_update.latest(force=True) or {}
                # Never backwards, and only here. A site left announcing an old build
                # had this server replace itself with one from weeks ago - a working
                # install of the wrong thing, so nothing afterwards notices. An
                # installer made on this machine is a different matter and is checked
                # where it is found; putting this at the top of the door refused every
                # install there is, including the ones that were newer.
                here_now = self.build_version()
                offered = str(said.get("version") or "")
                if (offered and here_now and offered != here_now
                        and not pd_update.newer(offered, here_now)):
                    self.reply_json({"ok": False,
                                     "why": "The site offers %s and this server is "
                                            "%s. It will not go backwards."
                                            % (offered, here_now)}, 400)
                    return
                if not said.get("version"):
                    self.reply_json({"ok": False,
                                     "why": "palladium.video did not answer"}, 503)
                    return
            try:
                if not onto:
                    onto = pd_update.fetch(stored.get("betaKey", ""),
                                           said.get("sha256"))
            except Exception as e:
                # imported here: it never was in this function, and a failed fetch
                # crashed on the name instead of answering
                import traceback
                S.Handler.note_fault(None, traceback.format_exc())
                self.reply_json({"ok": False, "why": "Could not fetch it: %s" % e}, 502)
                return
            self.reply_json({"ok": True, "version": said.get("version", ""),
                             "installer": os.path.basename(onto)})
            try:
                self.wfile.flush()
            except Exception:
                pass
            pd_update.install(onto)
            return
        if path == "/requests":
            # Somebody asking for a film this house has not got. It is written down
            # rather than acted on: what comes into the house is the owner's to decide,
            # and a request that fetched by itself would be a download button by
            # another name.
            body = self.read_json() or {}
            key = str(body.get("key") or "")[:40]
            if not key:
                self.reply_json({"error": "which title?"}, 400)
                return
            book = self.read_requests()
            mine = book.get(key) or {}
            who = self.guest_name if self.role == "guest" else "you"
            # Who is asking, by the key they hold rather than the name they show.
            # Counting names made everyone who is not a guest the same person, and
            # two guests called the same thing one person as well.
            mark = hashlib.sha1(
                str(self.viewer()).encode("utf-8")).hexdigest()[:12]
            if mine and not mine.get("done"):
                # Asked for again. One entry for the title, and the people who want it
                # counted on it: deciding what comes into the house is better served
                # by "four of us asked" than by whoever happened to ask first. The
                # same person asking twice is still one person.
                names = [str(n) for n in (mine.get("whoAll")
                                          or ([mine.get("who")] if mine.get("who") else []))
                         if n]
                # rows written before this counted one asker and did not say who
                marks = [str(k) for k in (mine.get("whoKeys") or []) if k]
                if not marks and names:
                    marks = ["?%d" % i for i in range(len(names))]
                if mark not in marks:
                    marks.append(mark)
                    if who not in names:
                        names.append(who)
                mine["whoAll"] = names
                mine["whoKeys"] = marks
                mine["asks"] = len(marks)
                book[key] = mine
                self.write_requests(book)
                self.reply_json({"already": True, "request": mine,
                                 "mine": True,
                                 "watchlisted": self.onto_watchlist(key)})
                return
            title = str(body.get("title") or mine.get("title") or "")[:200]
            mine = {"key": key, "title": title,
                    "year": body.get("year") or mine.get("year"),
                    "who": who, "whoAll": [who], "whoKeys": [mark], "asks": 1,
                    "when": int(time.time()),
                    "where": str(body.get("where") or "")[:300],
                    "done": False}
            book[key] = mine
            self.write_requests(book)
            # and on the noticeboard, where requests are already answered
            try:
                self.file_report("request",
                                 ("%s asked for %s" % (who, title or key))
                                 + (chr(10) + str(mine.get("where") or "")).rstrip(),
                                 self.app_name())
            except Exception:
                pass
            self.reply_json({"asked": True, "request": mine, "mine": True,
                             "watchlisted": self.onto_watchlist(key)})
            return
        if path == "/requests/done":
            # marked as dealt with, so the poster stops saying it is being asked for
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            book = self.read_requests()
            if key in book:
                book[key]["done"] = bool(body.get("done", True))
                book[key]["doneWhen"] = int(time.time())
                self.write_requests(book)
            self.reply_json({"requests": self.read_requests()})
            return
        if path == "/collections/shuffle":
            # Putting a shelf on: a draw, a look, a step back, or carrying on with
            # what was left. The main server owns the round, so a machine keeping copies
            # asks there first and hands the answer back.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            cid = str(body.get("id") or "")
            # a collection is one round whether asked for as "coll:<id>" - the Start
            # tab's card - or by its id: two names had made two rounds, two places
            if cid.startswith("coll:"):
                cid = cid[5:]
            # a series page's Shuffle: the viewer's collection of that series where
            # there is one, all its episodes where there is not
            if cid.startswith("series:"):
                cid = self.series_shuffle_id(cid[7:])
            # a series' shuffle with no collection of its own: one is made for it, named
            # after the series, so it is in Collections with the rest - its round, and the
            # place in it, carried over
            if cid.startswith("show:") and not body.get("peek"):
                cid = self.own_series_shelf(cid[5:]) or cid
            said = self.shuffle_draw(cid, bool(body.get("peek")), bool(body.get("resume")),
                                     bool(body.get("back")), str(body.get("from") or ""),
                                     str(body.get("at") or ""))
            if isinstance(said, dict):
                said["shelf"] = cid
            # the Start front made again: its Shuffle row leads with the one used last
            if not body.get("peek"):
                S.local().start_forget(self.viewer())
            self.reply_json(said)
            return
        if path == "/collections/shuffle/queue":
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            self.reply_json(self.shuffle_lists(str(body.get("id") or "")))
            return
        return (path, )

    def _post_part_04(self, path):
        """POST routes, part 4 of 20:
        /collections/shuffle/note, /collections/shuffle/undraw, /collections/shuffle/reset,
        /collections/test
        """
        if path == "/collections/shuffle/note":
            # where a shuffled playing got to, sent by the copy it is playing from: the
            # round is drawn here, so its places have to be here as well
            body = self.read_json() or {}
            try:
                self.shuffle_note(str(body.get("id") or ""), str(body.get("key") or ""),
                                  float(body.get("position") or 0),
                                  float(body.get("duration") or 0), forward=False)
            except (TypeError, ValueError):
                pass
            self.reply_json({"ok": True})
            return
        if path == "/collections/shuffle/undraw":
            # A drawn title stays drawn: putting it back in the hat made Next skip it
            # after stepping back. Older apps still ask; the answer is no.
            self.read_json()
            self.reply_json({"putBack": False})
            return
        if path == "/collections/shuffle/reset":
            # The hat back to full, the history empty, and nothing kept half-watched.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            one = self.shuffle_round(mine, str(body.get("id") or ""))
            self.round_log(str(body.get("id") or ""), one, "reset by the viewer")
            one.update({"order": [], "pos": -1, "queue": [], "played": [], "at": {},
                        "done": "", "run": int(one.get("run") or 1) + 1})
            one.pop("back", None)
            S.Handler.round_moved(one)
            S.write_settings(stored)
            self.reply_json({"ok": True, "run": one["run"]})
            return
        if path == "/collections/test":
            # What a rule would take, without saving it. The form sends what is typed
            # in its boxes: what the include words catch is green, what the exclude
            # words or the hand then knock out is red.
            body = self.read_json() or {}
            shelf = {"mode": str(body.get("mode") or "filter"),
                     "rule": body.get("rule") or {},
                     "pinned": body.get("pinned") or [],
                     "hidden": body.get("hidden") or []}
            # the same reckoning a save makes: a pin the words already catch, or a
            # strike on something they never caught, is no override at all
            if shelf["mode"] != "manual":
                con = S.local().lib.db()
                try:
                    byrule = set(self.collection_keys(
                        con, dict(shelf, pinned=[], hidden=[])))
                finally:
                    con.close()
                if byrule:
                    shelf["pinned"] = [k for k in shelf["pinned"]
                                       if str(k) not in byrule]
                    shelf["hidden"] = [k for k in shelf["hidden"]
                                       if str(k) in byrule]
            # the season range lifted too: seasons past "-s30" are left out by the rule,
            # and the editor drew them nowhere rather than in red
            wide = dict(shelf, hidden=[],
                        rule=dict(shelf["rule"], without=[], seasonWithout="", season=""))
            con = S.local().lib.db()
            try:
                took = self.collection_keys(con, shelf)
                held = set(took)
                # which of them are there by hand rather than by the rule, so the
                # test can say so and the mark can be taken back
                pinned = set(str(k) for k in (shelf["pinned"] or []))
                struck = set(str(k) for k in (shelf["hidden"] or []))
                rows, out = [], []
                # Read the same way the saved shelf is, or the test says one thing
                # and the shelf it makes says another: a programme as its seasons,
                # and part of a season as that season with a count on it.
                def cards(keys):
                    seasoned, rest = self.read_as_seasons(con, [str(k) for k in keys])
                    return seasoned + [one for one in
                                       (S.local().metadata_for(con, k, brief=True)
                                        for k in rest) if one]
                for one in cards(took):
                    one["hand"] = str(one.get("ratingKey")) in pinned
                    rows.append(one)
                # and the seasons of a series held whole that nothing links yet, as the
                # collection's page shows them: what is matched and what is not
                for show in self.whole_series(con, shelf):
                    rows = S.local()._every_season(con, rows, show)
                for one in rows:
                    one.setdefault("hand", False)
                # and what the rule would take with both excludes lifted, less
                # whatever is already above: those are the ones that are out
                shown = set(str(m.get("ratingKey")) for m in rows)
                for one in cards(self.collection_keys(con, wide)):
                    key = str(one.get("ratingKey"))
                    if key in shown:
                        continue
                    one["hand"] = key in pinned or key in struck
                    out.append(one)
            finally:
                con.close()
            # by programme and then by number, or "Season 10" sorts before "Season 2"
            order = lambda m: (int(m.get("year") or 0),
                               (m.get("titleSort")
                                or (m.get("parentTitle") if m.get("type") == "season"
                                    else "")
                                or m.get("title") or ""),
                               int(m.get("index") or 0))
            rows.sort(key=order)
            out.sort(key=order)
            self.reply_json({"Metadata": rows, "Excluded": out})
            return
        return ()

    def _post_part_05(self, path):
        """POST routes, part 5 of 20:
        /collections, /watchlist, /favorites, /collections/for, /collections/mark,
        /stream/buffer, /feedback/seen
        """
        if path == "/collections":
            # make, change or remove one. The shelves are the viewer's own, like the
            # watchlist they sit beside.
            body = self.read_json() or {}
            shelves = self.collections()
            want = str(body.get("id") or "")
            # Two shelves with one name cannot be told apart on the row of buttons,
            # and "Watchlist" is the button that stands for having no collection
            # open at all.
            asked = str(body.get("name") or "").strip()
            if asked:
                taken = [c for c in shelves
                         if c.get("id") != want
                         and (c.get("name") or "").strip().lower() == asked.lower()]
                if taken or asked.lower() == "watchlist":
                    self.reply_json({
                        "error": ("There is already a collection called %s." % asked)
                                 if taken else
                                 "Watchlist is the name of the shelf itself.",
                        "collections": shelves}, 409)
                    return
            if body.get("remove") and want:
                self.save_collections([c for c in shelves if c.get("id") != want])
                self.reply_json({"collections": self.collections()})
                return
            if want:
                for c in shelves:
                    if c.get("id") != want:
                        continue
                    for field in ("name", "rule", "pinned", "hidden", "cover",
                                  "mode", "view", "shuffleSeason",
                                  "shuffleSeasonWithout"):
                        if field in body:
                            c[field] = body[field]
                    # What the rule says on its own, so that a mark records only
                    # what the rule does not already say: putting back something the
                    # words catch is lifting the strike, not pinning it, and taking
                    # out something they never caught is lifting the pin. Otherwise
                    # a shelf fills with overrides that agree with the filter, and
                    # every poster claims to have been edited by hand.
                    byrule = set()
                    if (str(c.get("mode") or "filter") != "manual"
                            and (body.get("add") or body.get("drop")
                                 or "rule" in body)):
                        con = S.local().lib.db()
                        try:
                            byrule = set(self.collection_keys(
                                con, dict(c, pinned=[], hidden=[])))
                        finally:
                            con.close()
                    # One title in or out. Said as add and drop rather than as two
                    # lists, because a title struck out has to leave the pinned list
                    # as well as join the struck-out one - a rule may catch it, and
                    # then it is only out if it is out of both.
                    for key in ([body["add"]] if body.get("add") else []):
                        key = str(key)
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) != key]
                        if key not in byrule and key not in [
                                str(k) for k in (c.get("pinned") or [])]:
                            c["pinned"] = (c.get("pinned") or []) + [key]
                    # Taking a manual edit back: the title stops being pinned or
                    # struck out and the rule decides it again, whichever way that
                    # falls. Said as its own verb because neither add nor drop means
                    # "no opinion".
                    for key in ([body["clear"]] if body.get("clear") else []):
                        key = str(key)
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) != key]
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) != key]
                    for key in ([body["drop"]] if body.get("drop") else []):
                        key = str(key)
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) != key]
                        if key in byrule and key not in [
                                str(k) for k in (c.get("hidden") or [])]:
                            c["hidden"] = (c.get("hidden") or []) + [key]
                    # and the same reckoning over the whole shelf: an override the
                    # rule now agrees with is no override at all, and editing the
                    # words is the usual way one stops meaning anything
                    if byrule:
                        c["pinned"] = [k for k in (c.get("pinned") or [])
                                       if str(k) not in byrule]
                        c["hidden"] = [k for k in (c.get("hidden") or [])
                                       if str(k) in byrule]
                    break
            else:
                shelves.append({
                    "cover": str(body.get("cover") or ""),
                    "mode": ("manual" if str(body.get("mode") or "") == "manual"
                             else "filter"),
                    "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFF),
                    "name": str(body.get("name") or "Collection")[:60],
                    "rule": body.get("rule") or {},
                    "pinned": body.get("pinned") or [],
                    "hidden": body.get("hidden") or [],
                })
            self.save_collections(shelves)
            self.reply_json({"collections": self.collections()})
            return
        if path in ("/offline/make", "/offline/got", "/offline/drop"):
            self.offline_post(path)
            return
        if path == "/rip":
            # a remux of this title made into a 1080p x264 copy beside it, on this server
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_rip
            import pd_torrents
            if pd_torrents._following():
                self.reply_json({"ok": False, "why": "rips are made on the main server"})
                return
            body = self.read_json() or {}
            key, path_in = str(body.get("key") or ""), str(body.get("path") or "")
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT i.title, i.year, f.path, f.bitrate FROM file f JOIN item i "
                                  "ON i.id = f.item_id WHERE f.item_id=? AND f.path=? "
                                  "AND f.episode_id IS NULL", (key, path_in)).fetchone()
            finally:
                con.close()
            if not row:
                self.reply_json({"ok": False, "why": "that file is not one of this film's"})
                return
            # the file's bitrate is kept in kbit/s
            if not pd_rip.is_remux(row[2], float(row[3] or 0) / 1000):
                self.reply_json({"ok": False, "why": "only a remux is ripped"})
                return
            # made by the ripper, a program of its own on this machine: it outlives a restart
            self.reply_json(pd_rip.ask(key, row[2], row[0], row[1], float(row[3] or 0) / 1000,
                                       str(body.get("profile") or "")))
            return
        if path == "/disk/hold":
            # a program beside this server reading a drive - the ripper - asking whether to wait
            if not (self.role == "owner" and self.client_address[0] in ("127.0.0.1", "::1")):
                self.send_error(403, "not allowed")
                return
            import pd_speed
            body = self.read_json() or {}
            # a background worker like any other: the gate they all ask, its disk turn taken
            why = S.worker_waits(in_line=True, idle_priority=True)
            if not why and not pd_speed.copy_may_run(S.aheads_on_drive(str(body.get("path") or ""))):
                why = "somebody on this drive is filling their buffer"
            self.reply_json({"hold": why})
            return
        if path in ("/disk/turn", "/disk/done"):
            # a program beside this server - the ripper - taking a turn on a drive from the
            # same queue every job of this server's takes one from: a download or the copy
            # goes first, watching never waits, and the health page shows it
            if not (self.role == "owner" and self.client_address[0] in ("127.0.0.1", "::1")):
                self.send_error(403, "not allowed")
                return
            import pd_disk
            body = self.read_json() or {}
            if path == "/disk/done":
                pd_disk.done(str(body.get("ident") or ""))
                self.reply_json({"ok": True})
                return
            kind = str(body.get("kind") or "work")
            if kind not in pd_disk.KINDS or kind == "watch":
                kind = "work"
            ident, why = pd_disk.ask(str(body.get("path") or ""), kind, str(body.get("label") or "")[:120],
                                     ident=str(body.get("ident") or "") or None)
            self.reply_json({"ident": ident, "why": why})
            return
        if path == "/rip/cancel":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_rip
            body = self.read_json() or {}
            self.reply_json({"ok": pd_rip.cancel(str(body.get("id") or ""))})
            return
        if path == "/watchlist/seen":
            # the Watchlist opened: what was blinking there has been seen
            pd_notify.ask_main(self.viewer(), "seen")
            stored = self.settings_file()
            if pd_notify.seen(self.viewer_settings(stored)):
                S.write_settings(stored, merge=False)
            self.reply_json({"ok": True})
            return
        if path == "/watchlist":
            # "that one, later": a film, an episode or a whole series, kept in this
            # viewer's corner rather than in the library, which belongs to everyone
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            if not key:
                held = self.watchlist()
                full, part = self.rollup(held)
                self.reply_json({"watchlist": held, "covers": full, "part": part})
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            # the same as the shuffle: a programme or a season is its episodes
            keys = self.spread(key)
            marked = [str(k) for k in (mine.get("watchlist") or [])
                      if str(k) != key and str(k) not in keys]
            mine["watchlist"] = marked[:400]
            if body.get("on", True):
                # newest first: the order it was thought of
                marked = S.put_on_watchlist(mine, keys)
            S.write_settings(stored)
            full, part = self.rollup(marked)
            self.reply_json({"watchlist": marked, "on": key in marked,
                             "covers": full, "part": part})
            return
        if path == "/favorites":
            # kept on both machines: a favourite is copied whatever else is wanted.
            # A programme or a season is its episodes, as on the watchlist.
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            held = [str(k) for k in (mine.get("favorites") or [])]
            key = str(body.get("key") or "")
            if key:
                keys = self.spread(key)
                held = [k for k in held if k != key and k not in keys]
                if body.get("on", True):
                    held = keys + held
                mine["favorites"] = held
                S.write_settings(stored)
            full, part = self.rollup(held)
            self.reply_json({"favorites": held, "on": key in held,
                             "covers": full, "part": part})
            return
        if path == "/collections/for":
            # which shelves hold this title: all of it, some of it, or none
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            self.reply_json({"collections": self.shelves_holding(str(body.get("key") or ""))})
            return
        if path == "/start/feedback":
            # the Start tab, told by this viewer: more like this one, or not this again
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            what = str(body.get("what") or "")
            if not key or what not in ("more", "not", "clear"):
                self.reply_json({"error": "a key, and more, not or clear"}, 400)
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            more = [k for k in (mine.get("startMore") or []) if k != key]
            never = [k for k in (mine.get("startNot") or []) if k != key]
            if what == "more":
                more = [key] + more
            elif what == "not":
                never = [key] + never
            mine["startMore"] = more[:300]
            mine["startNot"] = never[:1000]
            S.write_settings(stored, merge=False)
            # their taste is read again on the next ask, with this in it - and their
            # front made again rather than answered as it was
            S.local()._TASTES.pop(str(self.viewer()), None)
            S.local().start_forget(self.viewer())
            self.reply_json({"ok": True, "more": len(mine["startMore"]),
                             "not": len(mine["startNot"])})
            return
        if path == "/collections/mark":
            # one title on or off one shelf; a programme or a season is its episodes
            body = self.read_json() or {}
            said = self.house_answers(path, body)
            if said is not None:
                self.reply_json(said)
                return
            key = str(body.get("key") or "")
            want = str(body.get("id") or "")
            if want.startswith("coll:"):
                want = want[5:]
            self.collections()                     # old marks become a shelf first
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            shelf = next((c for c in (mine.get("collections") or [])
                          if isinstance(c, dict) and str(c.get("id")) == want), None)
            if not key or shelf is None:
                self.reply_json({"error": "there is no such shelf"}, 404)
                return
            self.shelf_mark(shelf, key, bool(body.get("on", True)))
            S.write_settings(stored)
            self.reply_json({"collections": self.shelves_holding(key)})
            return
        if path == "/stream/buffer":
            self.stream_buffer()
            return
        if path == "/feedback/seen":
            # the owner has the page open: everything up to now has been looked at
            stored = self.settings_file()
            stored["reportsSeen"] = int(time.time())
            S.write_settings(stored, merge=False)
            self.reply_json({"ok": True, "seen": stored["reportsSeen"]})
            return
        return ()

    def _post_part_06(self, path):
        """POST routes, part 6 of 20: /feedback."""
        if path == "/feedback":
            # anyone using this server may report a fault or ask for something; it is
            # the one thing a guest is allowed to write here
            # (and if this machine has been told it may, a fault - not a request -
            # goes on to palladium.video, with everything identifying taken out)
            body = self.read_json()
            row = {
                # a stable name for the row, so it can be hidden later; the file is
                # append-only text and positions shift when it is cleared
                "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFFFFF),
                "when": int(time.time()),
                "who": self.guest_name if self.role == "guest" else "you",
                "kind": (body.get("kind") or "problem")[:20],
                "text": (body.get("text") or "").strip()[:2000],
                "from": self.client_address[0],
                "app": (body.get("app") or "")[:40],
            }
            # a stack trace is a fault whoever hands it in: the kind decides where it
            # is shelved, not the client, which could say anything
            row["source"] = "auto" if row["kind"] in ("error", "crash") else "person"
            if row["text"]:
                with open(os.path.join(S.ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + chr(10))
                # and on to palladium.video, if this machine has been told it may. A
                # fault only - a request is somebody talking to the person who runs
                # this server, and it is theirs.
                if row["source"] == "auto":
                    self.tell_the_site(row)
            self.reply_json({"ok": True})
            return
        return ()

    def _post_part_07(self, path):
        """POST routes, part 7 of 20: /settings."""
        if path == "/settings":
            # a guest's body has already been read, to check what it was asking for
            body = getattr(self, "settings_body", None) or self.read_json() or {}
            self.settings_body = None
            # a copy that mirrors the main server takes these from it every round: a
            # change made here would be undone within two minutes, so it is refused
            locked = self.mirror_locked(body)
            if locked:
                self.reply_json({"error": locked, "mirrored": True}, 409)
                return
            key = body.get("key") or ""
            device = self.device_of(body.get("device"))
            # What somebody is called, set by them. A name was something only the
            # person who wrote the invitation could give, so everybody wore whatever
            # they were christened at the moment they were invited - and the owner
            # wore "me", which is what a server calls whoever installed it before
            # anybody has said who that is.
            if self.save_viewer_choice(body):
                return
            if "myName" in body:
                called = str(body.get("myName") or "").strip()[:40]
                if not called:
                    self.reply_json({"error": "a name, please"}, 400)
                    return
                # Their own, not the one on the invitation. What the owner wrote
                # when they made the key stays as it was written: it is how the owner
                # knows who they gave it to, and somebody renaming themselves must not
                # quietly rewrite that.
                stored = self.settings_file()
                self.viewer_settings(stored)["myName"] = called
                S.write_settings(stored, merge=False)
                # the owner has no key to keep a name on, so theirs is the one the
                # server keeps for whoever installed it
                if self.viewer() == "me":
                    S.write_settings({"ownerName": called})
                self.reply_json({"name": called})
                return
            if key and body.get("reset"):
                self.reply_json({"subtitles": self.clear_override(key, device),
                                 "override": False, "device": device})
                return
            if body.get("useServerDefault"):
                # the owner's settings for this screen, adopted as this viewer's own
                device = self.device_of(body.get("device"))
                stored = self.settings_file()
                theirs = (stored.get("subtitles", {}) or {}).get(device) \
                         or dict(self.SUBTITLE_DEFAULTS[device])
                mine = self.viewer_settings(stored)
                kept = mine.get("subtitles", {}) or {}
                kept[device] = dict(theirs)
                mine["subtitles"] = kept
                S.write_settings(stored, merge=False)
                self.reply_json({"subtitles": self.subtitle_settings(None, device),
                                 "device": device,
                                 "devices": {d: self.subtitle_settings(None, d)
                                             for d in self.DEVICES}})
                return
            if "accent" in body:
                # "everyone" is the owner setting what a viewer meets before they
                # have chosen; without it a viewer is choosing for themselves
                self.reply_json({
                    "accent": self.set_accent(body["accent"],
                                              bool(body.get("everyone"))),
                    "accentDefault": self.accent_default(),
                })
                return
            if "backdrop" in body:
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                was = mine.get("myBackdrop")
                now = dict(was) if isinstance(was, dict) else {
                    d: self.backdrop_word(was) for d in self.DEVICES}
                which = self.device_of(body.get("device") or self.screen_now())
                now[which] = self.backdrop_word(body["backdrop"])
                mine["myBackdrop"] = now
                S.write_settings(stored, merge=False)
                self.reply_json({"backdrop": self.backdrop_all(),
                                 "backdropHere": self.backdrop_now()})
                return
            if "language2" in body:
                self.reply_json(
                    {"language2": self.set_viewer_language_2(body["language2"])})
                return
            if "language" in body:
                self.reply_json({"language": self.set_viewer_language(body["language"])})
                return
            if "subShift" in body:
                one = body["subShift"] or {}
                said = self.set_sub_shift(one.get("key") or "", one.get("seconds") or 0,
                                          one.get("sub") or "")
                # Moving a subtitle does not vouch for it. A file can be out by a
                # constant amount and still be the wrong translation, or drift, or be
                # cut for an entirely different edit - and nudging it once proves
                # none of that. The verified mark is still earned the two ways it
                # always was: said by hand, or watched far enough to have been read.
                self.reply_json({"subShift": said})
                return
            if "mine" in body:
                # a viewer's own ceiling, kept with the rest of their settings so it
                # follows them from the television to the telephone
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                one = body["mine"] or {}
                mine["quality"] = {"height": max(0, int(one.get("height") or 0)),
                                   "mbit": max(0, int(one.get("mbit") or 0))}
                S.write_settings(stored)
                self.reply_json({"mine": self.my_quality()})
                return
            if "quality" in body:
                stored = self.settings_file()
                caps = stored.get("quality") or {}
                for where in ("home", "away"):
                    one = (body["quality"] or {}).get(where)
                    if one is None:
                        continue
                    caps[where] = {"height": max(0, int(one.get("height") or 0)),
                                   "mbit": max(0, int(one.get("mbit") or 0))}
                stored["quality"] = caps
                S.write_settings(stored)
                self.reply_json({"quality": self.quality_caps()})
                return
            if "nextDelay" in body:
                stored = self.settings_file()
                # nought to five: nought is "no pause at all", five is long enough to
                # reach for the remote and stop it
                stored["nextDelay"] = max(0, min(5, int(body["nextDelay"] or 0)))
                S.write_settings(stored)
                self.reply_json({"nextDelay": stored["nextDelay"]})
                return
            if "clock" in body:
                # 24 hours or 12, for everything that prints a time
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                mine["clock"] = "12" if str(body["clock"]) == "12" else "24"
                S.write_settings(stored)
                self.reply_json({"clock": mine["clock"]})
                return
            if "burnFor" in body:
                # the owner's: it is their machine and their line being spent
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = str(body["burnFor"] or "").lower()
                if want not in self.BURN_FOR:
                    self.send_error(400, "not one of the three")
                    return
                stored = self.settings_file()
                stored["burnFor"] = want
                stored.pop("burnAway", None)          # replaced by the three-state one
                S.write_settings(stored, merge=False)
                self.reply_json({"burnFor": want})
                return
            if "betaKey" in body:
                # The key the installer was fetched with. Updates come through the
                # same gate, so a key that is revoked stops both - which is what
                # closing a beta means.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = re.sub(r"[^0-9A-Za-z-]", "", str(body["betaKey"] or "")).upper()
                stored = self.settings_file()
                if want:
                    stored["betaKey"] = want[:16]
                else:
                    stored.pop("betaKey", None)
                S.write_settings(stored, merge=False)
                self.reply_json({"betaKey": stored.get("betaKey", "")})
                return
            if "ownerName" in body:
                # what the owner is called in the log and the figures
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                stored["ownerName"] = str(body["ownerName"] or "").strip()[:40]
                S.write_settings(stored, merge=False)
                self.reply_json({"ownerName": stored["ownerName"]})
                return
            if "startRows" in body:
                # The Start tab's rows, as homeRows below: the kept ones in order, and
                # the ones turned off, so a row added to the program later shows up
                want = [str(x)[:40] for x in (body["startRows"] or [])][:40]
                off = [str(x)[:40] for x in (body.get("startRowsOff") or [])][:40]
                stored = self.settings_file()
                if body.get("everyone"):
                    if self.role != "owner":
                        self.send_error(403, "not allowed")
                        return
                    stored["startRowsDefault"] = want
                    stored["startRowsDefaultOff"] = off
                else:
                    mine = self.viewer_settings(stored)
                    mine["startRows"] = want
                    mine["startRowsOff"] = off
                S.write_settings(stored, merge=False)
                self.reply_json({"startRows": want, "startRowsOff": off})
                return
            if "homeRows" in body:
                # Which shelves the front page carries, and in what order. Named
                # rather than numbered, so a shelf added to the program later is
                # simply absent from an old list and takes its default place.
                want = [str(x)[:40] for x in (body["homeRows"] or [])][:20]
                stored = self.settings_file()
                if body.get("everyone"):
                    # the arrangement anybody meets before they have made one
                    if self.role != "owner":
                        self.send_error(403, "not allowed")
                        return
                    stored["homeRowsDefault"] = want
                else:
                    mine = self.viewer_settings(stored)
                    mine["homeRows"] = want
                S.write_settings(stored, merge=False)
                self.reply_json({"homeRows": self.home_rows(),
                                 "homeRowsDefault": self.home_default()})
                return
            if "autoScan" in body:
                # the default for anybody who has not chosen for themselves
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                self.reply_json({"autoScan": self.set_auto_scan(body["autoScan"])})
                return
            if "autoSync" in body:
                # whether a subtitle should be put in step with the film by itself,
                # which is this viewer's choice and not the server's
                stored = self.settings_file()
                mine = self.viewer_settings(stored)
                mine["autoSync"] = bool(body["autoSync"])
                S.write_settings(stored)
                self.reply_json({"autoSync": mine["autoSync"]})
                return
            if any(k in body for k in ("subsOwnTrack", "subsCheckFetch", "subsLangs")):
                # what is downloaded by itself: the owner's allowance and disk
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                if "subsOwnTrack" in body and body["subsOwnTrack"] in self.OWN_TRACK:
                    stored["subsOwnTrack"] = body["subsOwnTrack"]
                if "subsCheckFetch" in body:
                    stored["subsCheckFetch"] = bool(body["subsCheckFetch"])
                if "subsLangs" in body and isinstance(body["subsLangs"], list):
                    stored["subsLangs"] = [str(x)[:2].lower() for x in body["subsLangs"]
                                           if str(x).strip()][:12]
                S.write_settings(stored, merge=False)
                self.reply_json({"fetchRules": self.fetch_rules(stored)})
                return
            if "watchRegion" in body or "watchServices" in body:
                # which country's streaming services a new release's page shows, and
                # which of them
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                if "watchRegion" in body:
                    code = str(body.get("watchRegion") or "").strip().upper()
                    if code and not re.match(r"^[A-Z]{2}$", code):
                        self.reply_json({"error": "a country's two letters, or nothing"}, 400)
                        return
                    stored["watchRegion"] = code
                if "watchServices" in body:
                    said = body.get("watchServices")
                    if not isinstance(said, list):
                        self.reply_json({"error": "a list of names"}, 400)
                        return
                    stored["watchServices"] = list(dict.fromkeys(
                        str(n).strip()[:80] for n in said[:300] if str(n).strip()))
                S.write_settings(stored, merge=False)
                self.reply_json({"watchRegion": stored.get("watchRegion") or "",
                                 "watchServices": stored.get("watchServices") or []})
                return
            if "seasonal" in body:
                # the season's shelf on the front page: off, by the calendar, or one
                # holiday by name
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                import pd_holidays
                want = str(body.get("seasonal") or "off").lower()
                if want not in pd_holidays.CHOICES:
                    self.reply_json({"error": "one of: " + ", ".join(pd_holidays.CHOICES)}, 400)
                    return
                stored = self.settings_file()
                stored["seasonal"] = want
                S.write_settings(stored, merge=False)
                self.reply_json({"seasonal": want})
                return
            if "newDays" in body:
                # how long an arrived episode is marked New, counted from its air date
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                try:
                    days = max(1, min(30, int(body["newDays"])))
                except (TypeError, ValueError):
                    days = 6
                stored = self.settings_file()
                stored["newDays"] = days
                S.write_settings(stored, merge=False)
                self.reply_json({"newDays": days})
                return
            if "autoFetch" in body:
                # whether an episode's subtitle is fetched before it starts. The
                # owner's, because it is the owner's OpenSubtitles allowance being
                # spent and their disk the files land on.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = self.settings_file()
                stored["autoFetch"] = bool(body["autoFetch"])
                S.write_settings(stored, merge=False)
                self.reply_json({"autoFetch": stored["autoFetch"]})
                return
            if "watchParty" in body:
                # theirs alone: an owner turning it off for everybody is a different
                # thing, and that is what ending the party is for
                stored = self.settings_file()
                self.viewer_settings(stored)["watchParty"] = bool(body["watchParty"])
                S.write_settings(stored, merge=False)
                self.reply_json({"watchParty": bool(body["watchParty"])})
                return
            if "meters" in body:
                said = body["meters"] if isinstance(body["meters"], dict) else {}
                stored = self.settings_file()
                mine = dict(self.meters())
                for name in ("audience", "critics"):
                    if name in said:
                        mine[name] = bool(said[name])
                self.viewer_settings(stored)["meters"] = mine
                S.write_settings(stored, merge=False)
                self.reply_json({"meters": mine})
                return
            # a plugin's own settings, each answered by the plugin
            import pd_plugins
            got = pd_plugins.settings_post(body, self.role == "owner", self.settings_file(),
                                           lambda kept: S.write_settings(kept, merge=False))
            if got:
                self.reply_json(got[1], got[0])
                return
            if "tvPopular" in body:
                # theirs: whether their TV tab stands the popular programmes not held
                said = str(body.get("tvPopular") or "")
                if said in ("always", "popular"):
                    stored = self.settings_file()
                    self.viewer_settings(stored)["tvPopular"] = said
                    S.write_settings(stored, merge=False)
                self.reply_json({"tvPopular": S.local().tv_popular_of(self.viewer_settings(
                    self.settings_file()))})
                return
            if "weeklyRestart" in body:
                return self.set_weekly_restart(body)
            if "restartOnCrash" in body:
                return self.set_restart_on_crash(body)
            if "weekPick" in body:
                # the house's: the title the Start tab puts first this week, and a line on it
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                said = body.get("weekPick") or {}
                S.write_settings({"weekPick": {"key": str(said.get("key") or ""),
                                             "note": str(said.get("note") or "")[:200]}
                                if said.get("key") else {}})
                self.reply_json({"weekPick": (S.read_settings() or {}).get("weekPick") or {}})
                return
            # theirs, one choice each: the tab they open on, cam releases, watchlist messages,
            # notifications in the app
            for name, choose in (("startTab", self.set_start_tab),
                                 ("camReleases", self.set_cam_releases),
                                 ("watchMessages", self.set_watch_messages),
                                 ("notify", self.set_notify)):
                if name in body:
                    self.reply_json({name: choose(body.get(name))})
                    return
            if "tvRequest" in body:
                # theirs: whether their lists stand series nobody can get
                said = str(body.get("tvRequest") or "")
                if said in ("hide", "show"):
                    stored = self.settings_file()
                    self.viewer_settings(stored)["tvRequest"] = said
                    S.write_settings(stored, merge=False)
                got = self.viewer_settings(self.settings_file()).get("tvRequest")
                self.reply_json({"tvRequest": got if got in ("hide", "show") else "hide"})
                return
            if "filmsShow" in body:
                # theirs, like the rest of what a person sees: one viewer turning
                # asking on is not the house turning it on
                said = body["filmsShow"] if isinstance(body["filmsShow"], dict) else {}
                stored = self.settings_file()
                mine = dict(self.films_show())
                for name in ("disk", "download", "request", "upcoming"):
                    if name in said:
                        mine[name] = bool(said[name])
                self.viewer_settings(stored)["filmsShow"] = mine
                S.write_settings(stored, merge=False)
                self.reply_json({"filmsShow": mine})
                return
            if "letThemFetch" in body:
                # Who may bring something in, and from where. The owner's to set: it
                # is the house's disk and the house's line.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = str(body.get("letThemFetch") or "").lower()
                if want not in ("off", "packs", "all"):
                    self.reply_json({"error": "off, packs or all"}, 400)
                    return
                stored = S.read_settings()
                stored["letThemFetch"] = want
                S.write_settings(stored)
                self.reply_json({"letThemFetch": want})
                return
            if "outsideName" in body:
                # the router's DDNS name, handed out instead of the bare address
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                want = str(body.get("outsideName") or "").strip().lower()
                want = re.sub(r"^https?://", "", want).split("/")[0].split(":")[0]
                if want and not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", want):
                    self.reply_json({"error": "a host name, like name.tplinkdns.com"}, 400)
                    return
                stored = S.read_settings()
                stored["outsideName"] = want
                S.write_settings(stored)
                self.reply_json({"outsideName": want})
                return
            if "fetchMaxGb" in body:
                # The biggest one download anybody but the owner may start. The
                # owner's to set, like who may fetch at all.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                try:
                    want = max(0.0, float(body.get("fetchMaxGb") or 0))
                except (TypeError, ValueError):
                    self.reply_json({"error": "a size in GB, or nought for no limit"},
                                    400)
                    return
                stored = S.read_settings()
                stored["fetchMaxGb"] = want
                S.write_settings(stored)
                self.reply_json({"fetchMaxGb": want})
                return
            if any(k in body for k in ("preferRes", "preferBy", "preferCodec",
                                       "preferSource", "preferSound", "preferOrder",
                                       "preferGroup",
                                       "preferRate265", "preferRate264", "rateLock",
                                       "preferQuality", "preferSeeds",
                                       "seriesRate", "closeCall",
                                       "followShows",
                                       "grabLatest", "libraryShows")):
                # What to put forward when a film is carried more than once, and the
                # programmes to keep up with. The house's, like everything else about
                # what comes into it.
                #
                # Every one of these has to be named here: the door was opened by the
                # first two alone, so setting any of the others was accepted, answered
                # with the whole settings file, and quietly dropped.
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                stored = S.read_settings()
                if "preferRes" in body:
                    want = self.tidy_wish(body.get("preferRes"), self.RESOLUTIONS)
                    if want is None:
                        self.reply_json({"error": "2160p, 1080p, 720p or any"}, 400)
                        return
                    stored["preferRes"] = want
                if "preferBy" in body:
                    want = str(body.get("preferBy") or "").lower()
                    if want not in self.PREFER_BY:
                        self.reply_json({"error": "seeds or size"}, 400)
                        return
                    stored["preferBy"] = want
                for field, allowed in (("preferQuality", ("best", "pergb", "any")),
                                       ("preferSeeds", ("most", "any"))):
                    if field in body:
                        want = str(body.get(field) or "any").lower()
                        if want not in allowed:
                            self.reply_json({"error": " or ".join(allowed)}, 400)
                            return
                        stored[field] = want
                if "rateLock" in body:
                    # h265 holds the same picture in about a third fewer bits, so one
                    # band settles both: h264 is set and h265 follows it
                    stored["rateLock"] = bool(body["rateLock"])
                for field in ("preferRate265", "preferRate264"):
                    if field in body:
                        want = str(body.get(field) or "any").lower()
                        if want != "any" and S.rate_band(want) is None:
                            self.reply_json({"error": "a band like 3-6, or any"}, 400)
                            return
                        band = S.rate_band(want) if want != "any" else None
                        if band and band[0] > band[1]:
                            self.reply_json(
                                {"error": "From must be lower than To"}, 400)
                            return
                        stored[field] = want
                if "seriesRate" in body:
                    # the bitrate window for episodes; "same" is the films' one
                    want = str(body.get("seriesRate") or "same").lower()
                    if want not in ("same", "any") and S.rate_band(want) is None:
                        self.reply_json({"error": "a band like 3-6, same or any"}, 400)
                        return
                    stored["seriesRate"] = want
                if "closeCall" in body:
                    want = str(body.get("closeCall") or "off").lower()
                    if want not in ("off", "10", "25", "50"):
                        self.reply_json({"error": "off, 10, 25 or 50"}, 400)
                        return
                    stored["closeCall"] = want
                if "preferCodec" in body:
                    want = self.tidy_wish(body.get("preferCodec"), self.CODECS)
                    if want is None:
                        self.reply_json({"error": "h265, h264 or any"}, 400)
                        return
                    stored["preferCodec"] = want
                if "preferSource" in body:
                    want = self.tidy_wish(body.get("preferSource"), self.SOURCES)
                    if want is None:
                        self.reply_json({"error": "remux, bluray, webdl, webrip, "
                                                  "hdtv, dvdrip or any"}, 400)
                        return
                    stored["preferSource"] = want
                if "libraryShows" in body:
                    said = body.get("libraryShows")
                    if not isinstance(said, list):
                        self.reply_json({"error": "a list of programmes"}, 400)
                        return
                    stored["libraryShows"] = [
                        {"show": str(x.get("show") or "").strip(),
                         "tmdb": int(x.get("tmdb") or 0)}
                        for x in said[:200]
                        if isinstance(x, dict) and str(x.get("show") or "").strip()]
                    S.write_settings(stored)
                    threading.Thread(target=S.show_rows, daemon=True).start()
                if "grabLatest" in body:
                    stored["grabLatest"] = bool(body.get("grabLatest"))
                if "followShows" in body:
                    said = body.get("followShows")
                    if not isinstance(said, list):
                        self.reply_json({"error": "a list of programmes"}, 400)
                        return
                    # A name on its own means the whole programme; an entry with a
                    # mode on it says how much of it. Written as they came rather
                    # than as text: str() on a dictionary stores its printed form,
                    # which reads back as a programme called "{'show': ...}".
                    kept = []
                    for one in said[:60]:
                        if isinstance(one, str) and one.strip():
                            kept.append(one.strip())
                        elif isinstance(one, dict) and str(one.get("show") or "").strip():
                            mode = str(one.get("mode") or "all").lower()
                            try:
                                first = max(0, int(one.get("from") or 0))
                                last = max(0, int(one.get("to") or 0))
                            except (TypeError, ValueError):
                                first, last = 0, 0
                            kept.append({
                                "show": str(one["show"]).strip(),
                                "mode": mode if mode in ("all", "forward", "range")
                                        else "all",
                                "from": first, "to": last,
                                "grab": bool(one.get("grab"))})
                    stored["followShows"] = kept
                if "preferSound" in body:
                    want = self.tidy_wish(body.get("preferSound"), self.SOUNDS)
                    if want is None:
                        self.reply_json({"error": "atmos, truehd, dtshd, ddp, ac3 "
                                                  "or any"}, 400)
                        return
                    stored["preferSound"] = want
                if "preferGroup" in body:
                    want = self.tidy_wish(body.get("preferGroup"), self.GROUPS)
                    if want is None:
                        self.reply_json({"error": "oft, nikt0, other or any"}, 400)
                        return
                    stored["preferGroup"] = want
                if "preferOrder" in body:
                    stored["preferOrder"] = ",".join(self.wish_order(
                        {"preferOrder": body.get("preferOrder")}))
                S.write_settings(stored)
                self.reply_json({"preferRes": self.prefer_res(),
                                 "preferRate265": str((S.read_settings() or {}).get(
                                     "preferRate265") or "any"),
                                 "preferRate264": str((S.read_settings() or {}).get(
                                     "preferRate264") or "any"),
                                 "preferQuality": str((S.read_settings() or {}).get(
                                     "preferQuality") or "any"),
                                 "preferSeeds": str((S.read_settings() or {}).get(
                                     "preferSeeds") or "any"),
                                 "preferBy": self.prefer_by(),
                                 "preferCodec": self.prefer_codec(),
                                 "preferSource": self.prefer_source(),
                                 "preferSound": self.prefer_sound(),
                                 "preferGroup": self.wish("preferGroup", self.GROUPS),
                                 "preferOrder": ",".join(self.wish_order(S.read_settings())),
                                 "followShows": list(
                                     (S.read_settings() or {}).get("followShows") or []),
                                 "grabLatest": bool(
                                     (S.read_settings() or {}).get("grabLatest"))})
                return
            if "autoRuns" in body:
                # which of the jobs that run by themselves may run: the owner's
                if self.role != "owner":
                    self.send_error(403, "not allowed")
                    return
                said = body.get("autoRuns")
                if not isinstance(said, dict):
                    said = {}
                stored = S.read_settings() or {}
                runs = dict(stored.get("autoRuns") or {})
                for name in S.AUTO_RUNS:
                    if name in said:
                        runs[name] = bool(said[name])
                stored["autoRuns"] = runs
                S.write_settings(stored)
                self.reply_json({"autoRuns": {n: S.auto_set(n) for n in S.AUTO_RUNS}})
                return
            if body.get("autoReset") in S.AUTO_RUNS and self.role == "owner":
                self.reply_json({"reset": S.worker_reset(body["autoReset"])})
                return
            if "diskSlots" in body and self.role == "owner":
                self.reply_json({"diskSlots": S.set_disk_slots(body.get("diskSlots"))})
                return
            if "apartPause" in body:
                # which server stops using the tracker while the two are apart
                import pd_apart
                want = str(body.get("apartPause") or "off").lower()
                if want not in pd_apart.CHOICES:
                    self.reply_json({"error": "off, host, cache or both"}, 400)
                    return
                stored = self.settings_file()
                stored["apartPause"] = want
                S.write_settings(stored)
                self.reply_json({"apartPause": want})
                return
            if "autoNext" in body:
                stored = self.settings_file()
                stored["autoNext"] = bool(body["autoNext"])
                S.write_settings(stored, merge=False)
                self.reply_json({"autoNext": stored["autoNext"]})
                return
            if "remoteOwner" in body:
                # the groups one by one, by the same rule as the whole: from the house
                if self.role != "owner" or not self.at_home():
                    self.send_error(403, "not allowed from outside the house")
                    return
                stored = self.settings_file()
                said = dict(stored.get("remoteOwner") or {})
                for g, on in (body.get("remoteOwner") or {}).items():
                    if g in S.Handler.REMOTE_GROUPS:
                        said[g] = bool(on)
                stored["remoteOwner"] = said
                S.write_settings(stored, merge=False)
                self.reply_json({"remoteOwner": self.remote_owner()})
                return
            if "copyDefault" in body:
                self.set_copy_default(body["copyDefault"])
                return
            if "remoteAdmin" in body:
                # Only from the machine or this network, and only by the owner: turning
                # it off from away would be the last thing that key could do, and
                # turning it on from away is what somebody holding a stolen key wants.
                if self.role != "owner" or not self.at_home():
                    self.send_error(403, "not allowed from outside the house")
                    return
                stored = self.settings_file()
                stored["remoteAdmin"] = bool(body["remoteAdmin"])
                S.write_settings(stored, merge=False)
                self.reply_json({"remoteAdmin": stored["remoteAdmin"]})
                return
            # volume normalisation on/off, and the target in LUFS
            if "evenVolume" in body or "volumeTarget" in body:
                stored = self.settings_file()
                if "evenVolume" in body:
                    stored["evenVolume"] = bool(body["evenVolume"])
                if "volumeTarget" in body:
                    try:
                        stored["volumeTarget"] = max(-31.0, min(-9.0,
                                                     float(body["volumeTarget"])))
                    except (TypeError, ValueError):
                        pass
                S.write_settings(stored, merge=False)
                self.reply_json({"evenVolume": bool(stored.get("evenVolume")),
                                 "volumeTarget": stored.get("volumeTarget",
                                                            S.VOLUME_TARGET)})
                return
            saved = self.save_subtitle_settings(body.get("subtitles", {}), key, device)
            self.reply_json({
                "subtitles": saved, "device": device,
                "override": bool(key) and self.has_override(key, device),
                "devices": {d: self.subtitle_settings(None, d) for d in self.DEVICES},
            })
            return
        return ()

    def _post_part_08(self, path):
        """POST routes, part 8 of 20:
        /subs/reset, /subs/verify, /subs/remove, /subs/hide, /copy/pick, /subs/pick,
        /subs/get, /machine/port, /follow/watched, /follow/fetched, and 2 more
        """
        if path == "/subs/reset":
            # Put a subtitle back where its own file has it: the plan the server was
            # applying, and whatever was nudged on top of it. What the file says is
            # always recoverable; a correction is not, which is why this is a button
            # somebody presses rather than something that happens by itself.
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            index = int(body.get("index") or 0)
            found = S.local().file_for(key, int(body.get("mi") or 0)) or {}
            if found.get("file"):
                self.set_sub_fit(self.fit_name(found["file"], index), [])
            self.set_sub_shift(body.get("skey") or ("l" + key), 0.0,
                               body.get("sub") or "")
            self.reply_json({"ok": True, "offset": 0.0, "parts": []})
            return
        if path == "/subs/verify":
            # said by hand rather than earned by watching: the same record either way
            body = self.read_json() or {}
            self.reply_json(self.verify_subtitle(
                body.get("key") or "", body.get("name") or "",
                body.get("language") or "en",
                None if body.get("index") is None else int(body["index"]),
                # a check marks the file and chooses nothing for anybody
                how="check" if body.get("check") else ""))
            return
        if path == "/subs/remove":
            body = self.read_json() or {}
            if body.get("index") is not None and int(body["index"]) >= 0:
                # a track inside the video is never cut out of it: the film is left
                # alone and the track is simply not offered any more
                self.reply_json(self.hide_track(body.get("key") or "",
                                                int(body["index"]),
                                                int(body.get("mi") or 0)))
                return
            self.reply_json(self.remove_sidecar(body.get("key") or "",
                                                body.get("name") or "",
                                                int(body.get("mi") or 0)))
            return
        if path == "/subs/hide":
            # out of sight without touching the film: for when there is no room to
            # rewrite it, or no wish to
            body = self.read_json() or {}
            self.reply_json(self.hide_track(body.get("key") or "",
                                            int(body.get("index") or 0),
                                            int(body.get("mi") or 0),
                                            bool(body.get("show"))))
            return
        if path == "/copy/pick":
            # which file of a title a viewer settled on. Nothing is stored for the
            # best copy: that is the default everywhere, and a stored default would
            # outlive the day a better rip is added.
            body = self.read_json() or {}
            self.remember_copy(body.get("key") or "", body.get("file") or "")
            self.reply_json({"ok": True})
            return
        if path == "/sound/pick":
            # which soundtrack a viewer chose: played first for that film or series
            # from now, on every screen
            body = self.read_json() or {}
            try:
                said = S.local().sound_choice(str(body.get("key") or ""),
                                            int(body.get("index")), int(body.get("mi") or 0))
            except (TypeError, ValueError):
                said = None
            if not said:
                self.reply_json({"ok": False})
                return
            stored = self.settings_file()
            mine = self.viewer_settings(stored)
            picks = mine.get("soundPick", {}) or {}
            if said["standard"]:
                picks.pop(said["item"], None)      # the one that plays anyway
            else:
                picks[said["item"]] = said["liked"]
            mine["soundPick"] = picks
            S.write_settings(stored, merge=False)
            self.reply_json({"ok": True})
            return
        if path == "/subs/pick":
            # which subtitle a viewer settled on for one title: offered first from now
            body = self.read_json() or {}
            self.remember_pick(body.get("key") or "", body.get("name") or "")
            self.reply_json({"ok": True})
            return
        if path == "/subs/get":
            body = self.read_json() or {}
            self.reply_json(self.fetch_subtitle(body.get("key") or "",
                                                body.get("id"),
                                                body.get("language") or "en",
                                                bool(body.get("remember", True)),
                                                body.get("release") or "",
                                                downloads=body.get("downloads") or 0))
            return
        if path == "/library/repair":
            # The index checked, and its indexes rebuilt if it is damaged; each state
            # file read, and one that cannot be put back from its newest copy that can.
            # A copy is taken first and nothing is deleted. The owner's alone.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.reply_json(pd_repair.everything(S.ROOT, S.local().lib.dbpath))
            return
        if path == "/machine/port":
            # Which port this server answers on. Two Palladiums in one house are one
            # router forwarding two ports: the library on 8765, the cache on 8764.
            # Taken at the next start - a server cannot move while it is answering.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            try:
                want = int(body.get("port") or 0)
            except (TypeError, ValueError):
                want = 0
            if not (1 <= want <= 65535):
                self.reply_json({"error": "A port is a number from 1 to 65535."})
                return
            stored = S.read_settings()
            if stored is None:
                self.reply_json({"error": "The settings could not be read."})
                return
            stored["port"] = want
            S.write_settings(stored)
            self.reply_json({"port": want, "now": S.PORT,
                             "restart": want != S.PORT})
            return
        if path == "/follow/watched":
            # An evening watched on the machine keeping copies belongs in the same
            # book as the rest: it hands back where each viewing got to, and the
            # later note wins.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            rows = (self.read_json() or {}).get("progress") or []
            # a place for a title only the copy holds is kept too, against a row made
            # for it: dropped, Continue watching here never showed what was watched
            # there (2026-11-07)
            self.reply_json({"taken": S.local().take_progress(rows[:200], may_stub=True)})
            return
        if path == "/follow/fetched":
            # A machine that keeps copies saying what it fetched for itself while this
            # one was off. The films are not asked for here - only named. What to do
            # about them is decided on a thread of its own, because the answer is
            # several gigabytes long and nothing should wait for it.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            items = body.get("items")
            # the address it answers on, as this machine already knows it, matched to
            # where the request came from - not whatever the request claims to be
            here = self.client_address[0]
            where = ""
            for known in list(S.Handler.FOLLOWERS):
                # the host out of "http://address:port" without urllib: this method
                # imports it further down, which makes the name local to the whole of
                # it and unusable here
                host = str(known).split("//")[-1].split(":")[0].split("/")[0]
                if host == here:
                    where = str(known).rstrip("/")
                    break
            if not isinstance(items, list) or not where:
                self.reply_json({"taking": 0})
                return
            # each film by its pack's home: here or both, all of it; on the copy, what the
            # copy rules want, with Sync back on - the same decision the rules round makes
            import pd_homes
            import pd_torrents
            reported = [dict(one, where=where) for one in items[:200] if isinstance(one, dict)]
            chosen = pd_homes.take_back(
                reported, pd_torrents.home_of, S.Handler.tmdb_of_pack_file,
                S.Handler.SYNC_WANTED, bool(S.local().lib.config().get("syncBack")))
            fresh = []
            with S.Handler.FROM_COPY_LOCK:
                known = {(str(x.get("hash")), S.index_of(x))
                         for x in S.Handler.FROM_COPY}
                for one in chosen:
                    mark = (str(one.get("hash") or ""), S.index_of(one))
                    if not mark[0] or mark[1] < 0 or mark in known:
                        continue
                    known.add(mark)
                    fresh.append(one)
                S.Handler.FROM_COPY += fresh
            if fresh:
                S.Handler.take_from_the_copy()
            self.reply_json({"taking": len(fresh)})
            return
        if path == "/follow/apart":
            # the main server saying where it is, from wherever it is: this machine
            # compares that with its own address and pauses the tracker if told to
            import pd_follow
            body = self.read_json() or {}
            try:
                one = pd_follow.settings(S.local().lib.config())
            except Exception:
                one = {}
            if not (one.get("on") and one.get("key")
                    and str(body.get("key") or "") == str(one["key"])):
                self.send_error(403, "not allowed")
                return
            import pd_apart
            self.reply_json(pd_apart.heard_from_host(str(body.get("wan") or ""),
                                                     str(body.get("choice") or "")))
            return
        if path == "/follow/round":
            # shuffle rounds the cache moved while this machine was off: per shelf,
            # the newer round is taken whole
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            who = str(body.get("who") or "")
            theirs = body.get("shuffles")
            if not who or not isinstance(theirs, dict):
                self.reply_json({"took": [], "why": "nothing to take"})
                return
            stored = self.settings_file()
            here = (stored.setdefault("users", {}).setdefault(who, {})
                    if who != "me" else stored)
            rounds = here.get("shuffles")
            if not isinstance(rounds, dict):
                rounds = {}
            took = []
            for cid, one in theirs.items():
                mine = rounds.get(str(cid))
                if (isinstance(one, dict)
                        and self.round_stamp(one) > self.round_stamp(mine)):
                    # the newer round, whole: one order and one place in it
                    rounds[str(cid)] = one
                    took.append(str(cid))
            if took:
                here["shuffles"] = rounds
                S.write_settings(stored)
            self.reply_json({"took": took})
            return
        return ()

    def _post_part_09(self, path):
        """POST routes, part 9 of 20:
        /follow/watchlog, /follow/viewers, /follow/whatis, /follow/holding
        """
        if path == "/follow/feedback":
            # The cache's report board, merged with this one by id - the copy changed
            # last winning - and the one list handed back for it to keep as well.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_board
            body = self.read_json() or {}
            rows = [r for r in (body.get("rows") or []) if isinstance(r, dict)]
            both = pd_board.take(S.ROOT, rows, first_is_mine=True)
            if both is None:
                self.reply_json({"error": "the board here could not be read"}, 503)
                return
            self.reply_json({"rows": both})
            return
        if path == "/follow/watchlog":
            # The machine keeping copies trades watch logs: its plays kept here under
            # its name, and this machine's handed back to it under this one's.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            import pd_follow
            body = self.read_json() or {}
            name = self.server_name()
            con = S.local().lib.db()
            try:
                pd_follow.log_rows_take(con, body.get("rows") or [])
                back = pd_follow.log_rows_played_here(
                    con, int(body.get("since") or 0), name)
            finally:
                con.close()
            self.reply_json({"machine": name, "rows": back})
            return
        if self.follow_reads(path):
            return
        if path == "/follow/watch":
            # the copy's screens: a viewer's watchlist messages, kept here (pd_notify)
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed"); return
            body = self.read_json() or {}
            stored = S.read_settings() or {}
            said, changed = pd_notify.for_copy(stored, str(body.get("who") or ""),
                                               str(body.get("op") or ""), body.get("what") or {})
            if changed:
                S.write_settings(stored, merge=False)
            self.reply_json(said)
            return
        if path == "/follow/tracker":
            # this machine's tracker index, for the copy: it reads the feeds only, and a
            # search there for a series nobody uploaded this week found nothing to offer
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            import pd_tracker
            self.reply_json({"rows": pd_tracker.rows_since(float(body.get("since") or 0))})
            return
        if path == "/follow/viewers":
            # What each viewer keeps, for the machine that copies this library.
            #
            # A watchlist, the shelves somebody arranged, the collections they made:
            # these live beside the library, and a copy held none of them. So the one
            # moment a viewer needs the other machine - this one being off - is the
            # moment their own list is empty and the shelves are somebody's defaults.
            # Nothing about anybody's password or key goes over; only what they would
            # see on a screen.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            stored = S.read_settings() or {}
            # the shuffle rounds travel too, so a phone on the cache carries on the round
            # every setting that is a viewer's own, the server's list of them - the
            # copy mirrors it; a second subtitle language left off this list once
            # showed a Swedish series without subtitles there. Not the ones naming
            # titles by this library's numbers, which mean other titles on the copy.
            import pd_follow
            keep = tuple(k for k in S.Handler.VIEWER_KEYS + ("quality",)
                         if k not in pd_follow.VIEWER_LOCAL)
            out = {}
            con = S.local().lib.db()
            try:
                for who, one in (stored.get("users") or {}).items():
                    mine = {k: v for k, v in (one or {}).items() if k in keep}
                    if mine:
                        mine.update(S.local().round_in_words(con, one or {}))
                        out[who] = mine
            finally:
                con.close()
            # and the owner's own, under the key they watch by, so the person who
            # owns the library is not the one viewer it forgets
            owner = str(stored.get("ownerIs") or "")
            if owner and owner not in out:
                mine = {k: v for k, v in stored.items() if k in keep}
                if mine:
                    out[owner] = mine
            # and the house's own choices, which decide what every viewer's rows hold:
            # without them the copy's Popular shows row was whatever it happened to hold
            # and how a release is chosen, so Recommended is the same on both
            import pd_follow
            house = {k: stored[k] for k in pd_follow.HOUSE_MIRRORED if k in stored}
            # and the subtitle choices, which the copy carries over to its own files
            names = pd_follow.SUBS_BY_TITLE + pd_follow.SUBS_BY_PATH
            subs = {k: stored[k] for k in names if isinstance(stored.get(k), dict)}
            subs_by = {who: {k: one[k] for k in names if isinstance((one or {}).get(k), dict)}
                       for who, one in (stored.get("users") or {}).items()}
            house["receiver"] = stored.get("receiver") or {}
            self.reply_json({"viewers": out, "house": house, "subs": subs,
                             "subsBy": {w: v for w, v in subs_by.items() if v},
                             "ownerIs": owner, "ownerName": stored.get("ownerName")})
            return
        if path == "/follow/whatis":
            # What this library calls these files. The machine keeping copies knows
            # them by name and nothing else - it was copying before it started
            # writing down what each one is - and a file it cannot name is a film on
            # its disk that nobody is told about.
            invite = S.INVITES.check(self.bearer(), self.app_name())
            # a machine the owner has stopped keeps its key and is refused
            if not (invite and invite.get("follows")) or self.follower_stopped():
                self.send_error(403, "not allowed")
                return
            names = [str(n) for n in ((self.read_json() or {}).get("names") or [])][:400]
            found, facts, items, titles = {}, {}, {}, {}
            if names:
                con = S.local().lib.db()
                try:
                    wanted = {n.lower(): n for n in names}
                    for row in con.execute(
                            """SELECT f.path, f.item_id, f.episode_id, f.duration,
                                      f.container, f.vcodec, f.acodec, f.width,
                                      f.height, f.channels, f.bitrate,
                                      e.aired AS aired,
                                      i.title AS title, i.year AS year,
                                      i.sort_title AS sort_title,
                                      i.genres AS genres
                               FROM file f
                               LEFT JOIN episode e ON e.id = f.episode_id
                               LEFT JOIN item i ON i.id = f.item_id"""):
                        low = os.path.basename(row["path"] or "").lower()
                        if low not in wanted:
                            continue
                        name = wanted[low]
                        found[name] = (str(row["episode_id"]) if row["episode_id"]
                                       else str(row["item_id"]))
                        items[name] = str(row["item_id"])      # the title, for a copy to file under
                        # and this server's title for it. A follower names titles
                        # from file names, so any title with punctuation differed and
                        # lookups by name between the two returned nothing.
                        # and what it is about. A copy names its titles off file
                        # names and never learns a category, so every collection made
                        # by category was empty there - which is the one place they
                        # are read, the evening this machine is off.
                        titles[name] = {"title": row["title"], "year": row["year"],
                                        "sort": row["sort_title"],
                                        "genres": row["genres"]}
                        # and what was measured when it arrived here, so the cache
                        # does not have to open the file to know what is in it
                        facts[name] = {k: row[k] for k in
                                       ("duration", "container", "vcodec", "acodec",
                                        "width", "height", "channels", "bitrate")}
                        # and when the episode went out. A copy looks its titles up
                        # for itself, but nothing tells it the air date of an episode
                        # it holds - so the shelf of recently released episodes was
                        # empty on the machine holding the newest of them.
                        if row["aired"]:
                            facts[name]["aired"] = row["aired"]
                finally:
                    con.close()
            self.reply_json({"keys": found, "facts": facts, "items": items,
                             "titles": titles})
            return
        if path == "/follow/holding":
            # What the following server actually has, by this library's own numbers.
            # Not what it was asked to keep: a copy that failed half way is not a
            # copy, and a poster that says otherwise is a promise nobody can keep.
            if not self.follows_here():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            keys = [str(k) for k in (body.get("keys") or [])][:2000]
            # and what would not come, so the queue can say so. A copy that sets a
            # file aside after it fails carries on down the list, and the page had no
            # way of knowing - it showed rows waiting while something below them was
            # being fetched, which reads as the queue being ignored.
            bad = body.get("trouble")
            if isinstance(bad, list):
                S.Handler.FOLLOWER_TROUBLE = [
                    {"name": str(r.get("name") or "")[:120],
                     "why": str(r.get("why") or "")[:160],
                     "key": str(r.get("key") or ""),
                     "when": int(r.get("when") or time.time())}
                    for r in bad[:40] if isinstance(r, dict)]
            # The whole of what it holds replaces; a part of it is added to. Both
            # were added to, because there was no way to tell them apart - so a film
            # swept out of the cache's cache stayed on this list for good, the dot
            # stayed on its poster, and with this machine off somebody chose it and
            # there was nothing at the other end.
            if body.get("whole"):
                S.Handler.remember_copies(sorted(set(keys)))
            else:
                S.Handler.remember_copies(sorted(set(S.Handler.COPIES.get("keys") or [])
                                               | set(keys)))
            self.note_cache(kept=len(keys),
                               bad=len(S.Handler.FOLLOWER_TROUBLE))
            # and what this library allows that machine to use, in gigabytes. Lending
            # somebody a copy of a library is not lending them the whole disk, and the
            # setting that says so belongs here rather than on their machine. Zero
            # means their own setting stands.
            # A ceiling belongs to the key, the way an invitation for a person
            # carries what that person may do. One number for every machine meant
            # lending a second machine a copy re-lent the first one's allowance, and
            # taking it away from one took it from all of them. The library's old
            # single figure stands for keys made before this.
            mine = S.INVITES.check(self.bearer(), self.app_name()) or {}
            cap = mine.get("cap")
            # when the invitations last changed. The cache takes them a quarter of an
            # hour at a time, which is a long while to wait after handing somebody a
            # key - so this says whether there is anything new to take, and the cache
            # comes back for them at once rather than on its own slow clock.
            try:
                changed = int(os.path.getmtime(S.INVITES.path))
            except OSError:
                changed = 0
            if cap is None:
                cap = S.local().lib.config().get("cacheCap") or 0
            self.reply_json({"kept": len(keys), "cap": float(cap or 0),
                             # and what it is allowed to do here
                             "mayCopy": bool(mine.get("mayCopy", True)),
                             "invitesAt": changed})
            return
        return ()

    def _post_part_10(self, path):
        """POST routes, part 10 of 20:
        /follow/key, /app/fetch, /follow/clear, /follow/claim, /follow/extras,
        /proxy/fetch, /proxy/config, /proxy/run, /proxy/stop, /proxy/login
        """
        if path == "/follow/key":
            # A key for another Palladium, not for a person: it may read the library,
            # take copies of the files, and ask what the main server is watching. Revoked
            # like any other invitation.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            asked = self.read_json() or {}
            rows = S.INVITES.load()
            mine = next((r for r in rows if r.get("follows")), None)
            if asked.get("remove"):
                # Taking it away rather than replacing it. A new key stops the old
                # one but leaves a key on the table; this leaves none, and nothing
                # may read this library or take copies of it until one is made.
                #
                # One machine's key when one is named, every follow key when none is:
                # revoking used to take the key away from every machine that had one,
                # which is not what "stop this one" means.
                only = str(asked.get("only") or "").strip()
                if only:
                    S.INVITES.save([r for r in rows if not (
                        r.get("follows") and r.get("token") == only)])
                else:
                    S.INVITES.save([r for r in rows if not r.get("follows")])
                gone = S.Handler.STANDBY.get("where") or ""
                S.Handler.STANDBY.clear()
                cfg = S.local().lib.config()
                cfg["cache"] = {}
                S.local().lib.save_config(cfg)
                if gone and gone in S.Handler.FOLLOWERS:
                    # kept in the record, marked for what it is: a machine that used
                    # to copy from here is worth remembering after the key is gone
                    S.Handler.FOLLOWERS[gone]["revoked"] = int(time.time())
                    S.Handler.remember_followers(force=True)
                self.reply_json({"key": "", "where": ""})
                return
            # A key per machine. There used to be one for all of them - a new key
            # took the old one away from every cache at once, and stopping one
            # machine stopped the lot - so a key is made for each, named after the
            # machine it was made for, and revoked on its own.
            # What this key allows, set when it is made or changed later: a
            # ceiling in gigabytes, nought for as much as that machine allows itself,
            # and whether it may take copies at all or only read.
            forWhom = str(asked.get("for") or "").strip()
            if asked.get("set"):
                token = str(asked.get("set") or "")
                for row in rows:
                    if row.get("token") != token or not row.get("follows"):
                        continue
                    if "cap" in asked:
                        try:
                            row["cap"] = max(0.0, float(asked.get("cap") or 0))
                        except (TypeError, ValueError):
                            row["cap"] = 0.0
                    if "mayCopy" in asked:
                        row["mayCopy"] = bool(asked.get("mayCopy"))
                    S.INVITES.save(rows)
                    self.reply_json({"ok": True})
                    return
                self.send_error(404, "no such key")
                return
            if forWhom:
                mine = next((r for r in rows if r.get("follows")
                             and str(r.get("name") or "") == forWhom), None)
            if not mine or asked.get("again"):
                mine = S.INVITES.create(forWhom or "a following server", 0, "")
                rows = S.INVITES.load()
                for row in rows:
                    if row["token"] == mine["token"]:
                        row["follows"] = True
                    elif not forWhom:
                        # no machine named: the old behaviour, one key for the main server,
                        # and a new one stops whatever was there
                        row.pop("follows", None)
                    elif str(row.get("name") or "") == forWhom:
                        # the one this machine was using before: replaced, not kept -
                        # and every other machine keeps the key it has
                        row.pop("follows", None)
                S.INVITES.save(rows)
                mine = next(r for r in S.INVITES.load()
                            if r["token"] == mine["token"])
            self.reply_json({"key": mine["token"], "name": mine.get("name") or "",
                             "where": "http://%s:%d" % (S.LAN_IP, S.PORT)})
            return
        if path == "/app/fetch":
            # Take a copy of the app so this server can hand it to a phone or a
            # television. Owner only, and only when pressed: it is the one request
            # this program makes to palladium.video that nobody asked for otherwise.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            stored = S.read_settings() or {}
            if body.get("off") or body.get("on"):
                # handed out or not. The file stays either way: this is whether the
                # server offers it, not whether it has it.
                stored["appOff"] = bool(body.get("off"))
                S.write_settings(stored)
                self.reply_json({"off": bool(body.get("off")),
                                 "have": bool(self.app_beside_us())})
                return
            if self.app_beside_us() and not body.get("overwrite"):
                self.reply_json({"ask": True, "have": True,
                                 "version": self.app_version(),
                                 "where": S.Handler.APP_FROM})
                return
            self.fetch_app(force=True)
            self.reply_json({"getting": True})
            return
        if path == "/follow/clear":
            # What clearing the cache would delete, and doing it. Read before it is
            # done and before automatic clearing is switched on: "what will this do"
            # has to be answerable before it is answered by it happening.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            folder = (one.get("folder") or "").strip()
            cap = int(pd_follow.cap_now(one) * (1000 ** 3))
            how = str(one.get("deleteBy") or "oldest")
            keeping = set(pd_follow.KEEPING.get("set") or ())
            body = self.read_json() or {}
            said = pd_follow.could_go(folder, keeping, cap, how)
            said["cap"] = round(cap / 1e9, 2)
            # nothing has been copied yet this run, so nothing is known to be worth
            # keeping - and a clear that does not know that would take tonight's
            said["ready"] = bool(pd_follow.KEEPING.get("when"))
            if body.get("now") and said["sane"] and said["ready"]:
                pd_follow.make_room(folder, cap, keeping, how)
                said["cleared"] = said["gb"]
                after = pd_follow.could_go(folder, keeping, cap, how)
                said.update({"files": after["files"], "gb": after["gb"],
                             "rows": after["rows"], "used": after["used"]})
            self.reply_json(said)
            return
        if path == "/follow/claim":
            # Once, by hand: everything already in the cache folder is this
            # machine's own. The only way back for copies made before there was a
            # record of them - and it takes films and subtitles, nothing else.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            folder = (one.get("folder") or "").strip()
            if not (self.read_json() or {}).get("confirm"):
                self.reply_json({"ok": False, "why": "not confirmed"})
                return
            said = pd_follow.claim_folder(folder)
            said["ok"] = bool(said.get("taken"))
            said["folder"] = folder
            self.reply_json(said)
            return
        if path == "/follow/extras":
            # What is in the cache folder that this machine did not fetch. It is
            # never deleted by the program - only what it wrote is - so this is the
            # only way any of it goes: somebody reading the list and saying so.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            one = pd_follow.settings(S.local().lib.config())
            folder = (one.get("folder") or "").strip()
            # read only. A file this machine did not fetch is not its to delete,
            # whoever asks: the folder is a path typed by hand, and the whole point
            # of the list is that these are the files nothing here can account for.
            said = {}
            found = pd_follow.extras(folder)
            said.update({"folder": folder,
                         "sane": pd_follow.a_sane_folder(folder),
                         "extras": found[:200],
                         "count": len(found),
                         "gb": round(sum(f["bytes"] for f in found) / 1e9, 2)})
            self.reply_json(said)
            return
        if path in ("/proxy/fetch", "/proxy/config", "/proxy/run", "/proxy/stop",
                    "/proxy/login"):
            # the way in from outside is the owner's arrangement with their own router
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_proxy
            body = self.read_json() or {}
            if path == "/proxy/fetch":
                self.reply_json(pd_proxy.fetch())
            elif path == "/proxy/config":
                self.reply_json(pd_proxy.set_config(body))
            elif path == "/proxy/login":
                self.reply_json(pd_proxy.set_at_login(bool(body.get("on"))))
            elif path == "/proxy/run":
                said = pd_proxy.run()
                said.update(pd_proxy.state())
                self.reply_json(said)
            else:
                said = pd_proxy.stop()
                said.update(pd_proxy.state())
                self.reply_json(said)
            return
        return ()

    def _post_part_11(self, path):
        """POST routes, part 11 of 20:
        /torrents/config, /torrents/add, /torrents/remove, /torrents/get, /tracker/state,
        /tracker/duplicates, /receiver, /app/quit, /receiver/passthrough, /library/reveal, and 1 more
        """
        if path in ("/torrents/config", "/torrents/add", "/torrents/remove"):
            # the owner's: how qBittorrent is reached, and which packs are offered
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import base64
            import pd_torrents
            body = self.read_json() or {}
            if path == "/torrents/config":
                self.reply_json(pd_torrents.set_config(body))
            elif path == "/torrents/add":
                raw = None
                if body.get("data"):
                    try:
                        raw = base64.b64decode(str(body["data"]).split(",")[-1])
                    except (ValueError, TypeError):
                        raw = None
                given = str(body.get("path") or "").strip().strip('"')
                magnet = str(body.get("magnet") or "")
                if given.lower().startswith("magnet:"):
                    magnet, given = given, ""
                if magnet and not raw:
                    self.reply_json(pd_torrents.add_magnet(magnet))
                    return
                if given and not os.path.isfile(given):
                    self.reply_json({"ok": False, "why": "No such file: " + given})
                    return
                # "series": every video in it as one series, a folder a season
                self.reply_json(pd_torrents.add_pack(raw=raw, path=given or None,
                                                     series=str(body.get("series") or "")))
            else:
                self.reply_json(pd_torrents.remove_pack(str(body.get("hash") or "")))
            return
        if path == "/torrents/get":
            # one film from a pack, for whoever asks, within their week's download limit
            import pd_torrents
            if self.role != "owner" and self.let_them_fetch() == "off":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            token = self.bearer() or "me"
            cap = self.weekly_limits("downloadGbWeek").get(
                self.name_of(token).strip().lower(), 0.0)
            # and the biggest one download they may start, which the owner is not
            # held to
            most = 0.0 if self.owner_fetching() else self.fetch_most_gb()
            got = pd_torrents.request(str(body.get("key") or ""), token, self.watcher(), cap, most)
            if got.get("ok"):
                self.watch_fetched(str(body.get("key") or ""))
            self.reply_json(got)
            return
        if path == "/tracker/state":
            # what the index holds and how the asking is going, for the settings page
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            self.reply_json(pd_tracker.state())
            return
        if path == "/tracker/duplicates":
            # films held more than once, each copy and which one Choosing would keep
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            self.read_json()
            self.reply_json({"films": S.duplicates()})
            return
        if path == "/receiver":
            # what is set for the receiver: on or off, where it is, how many steps
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_receiver
            body = self.read_json() or {}
            stored = S.read_settings() or {}
            one = pd_receiver.settings(stored)
            for k in ("on", "ip", "steps", "device", "mode", "from", "brand"):
                if k in body:
                    one[k] = body[k]
            stored["receiver"] = pd_receiver.settings({"receiver": one})
            S.write_settings(stored)
            self.reply_json(pd_receiver.status(S.read_settings() or {}))
            return
        if path == "/app/threads":
            # what every thread is doing this moment, for a page that has gone slow
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(403, "not allowed")
                return
            import traceback
            names = {t.ident: t.name for t in threading.enumerate()}
            out = []
            for ident, frame in sys._current_frames().items():
                out.append("--- %s" % names.get(ident, ident))
                out.append("".join(traceback.format_stack(frame)[-6:]))
            self.reply_json({"threads": len(out) // 2, "stacks": chr(10).join(out)})
            return
        if path == "/app/restart":
            # the boss's restart, asked by hand from this machine only
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(403, "not allowed")
                return
            self.reply_json({"ok": S.restart_self()})
            return
        if path == "/app/quit":
            # An update about to replace this program, from this machine only: the
            # icon comes off the tray and the server ends, so no dead P is left behind
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(403, "not allowed")
                return
            self.reply_json({"ok": True})
            tray = S.TRAY.get("icon")
            threading.Timer(0.3, lambda: tray.bow_out_now() if tray
                            else os._exit(0)).start()
            return
        if path == "/receiver/passthrough":
            # A screen in the house passing a film's sound through to the receiver: up
            # by the steps set while it plays, down again when it ends. From the house
            # only - a guest's passthrough at home is their own amplifier, not this one.
            import pd_receiver
            pd_receiver.KEPT["path"] = os.path.join(S.ROOT, "receiver-raised.json")
            body = self.read_json() or {}
            # and only the device set for it, by its address: the television in front
            # of the receiver
            no = self.receiver_not_for(
                self.at_home(),
                self.client_address[0] == pd_receiver.settings(S.read_settings() or {})["device"])
            if no:
                self.reply_json(no)
                return
            # the screen by its address only: the app's version in it made each update
            # a new screen, and the old one's raise was never lowered
            who = self.client_address[0]
            stored = S.read_settings() or {}
            db = (self.passthrough_db(stored, body.get("key"))
                  if str(body.get("state")) == "start" else None)
            if (str(body.get("state")) == "start" and db is None
                    and pd_receiver.settings(stored)["mode"] == "loudness"):
                # not measured the new way yet: measured now, raised when it is known
                waiting = "%s|%s" % (who, body.get("key"))
                if waiting not in S.Handler.PASS_WAITING:
                    S.Handler.PASS_WAITING.add(waiting)
                    threading.Thread(target=self.measure_then_raise,
                                     args=(who, body.get("key")), daemon=True).start()
                said = {"ok": True, "measuring": True, "by": 0, "mode": "loudness"}
            elif str(body.get("state")) == "start":
                said = pd_receiver.raise_for(stored, who, body.get("key"), db)
                # what is in force for this film now, for the player's information line
                held = pd_receiver.RAISED.get("%s|%s" % (who, body.get("key"))) or {}
                said["by"] = int(held.get("by") or 0)
                said["mode"] = pd_receiver.settings(stored)["mode"]
            else:
                S.Handler.PASS_WAITING.discard("%s|%s" % (who, body.get("key")))
                said = pd_receiver.lower_for(stored, who, body.get("key"))
            try:
                pd_log.raw("%s receiver %s for %s: %s%s" % (
                    time.strftime("%H:%M:%S"), body.get("state"), who,
                    json.dumps(said), chr(10)))
            except OSError:
                pass
            self.reply_json(said)
            return
        if path == "/library/reveal":
            # the film's folder opened in Explorer on this machine, its file selected
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            where = ""
            # a path asked for is opened only if it is a file of the library
            if body.get("file"):
                con = S.local().lib.db()
                try:
                    if con.execute("SELECT 1 FROM file WHERE path=?",
                                   (str(body["file"]),)).fetchone():
                        where = str(body["file"])
                finally:
                    con.close()
            if not where:
                found = S.local().file_for(str(body.get("key") or ""), 0) or {}
                where = found.get("file") or ""
            if not where or not os.path.exists(where):
                self.reply_json({"ok": False, "why": "no file for that key"})
                return
            subprocess.Popen('explorer /select,"%s"' % os.path.normpath(where))
            self.reply_json({"ok": True, "file": where})
            return
        if path == "/tracker/upgrades":
            # films here that the settings under Choosing would swap for another release
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            got = S.upgrades(bool(body.get("fresh")))
            self.reply_json({"films": got, "at": int(S.UPGRADES["at"]),
                             "reasonNames": S.REASON_NAMES})
            return
        return ()

    def _post_part_12(self, path):
        """POST routes, part 12 of 20: /tracker/list."""
        if path == "/tracker/list":
            # every version of one film the tracker is carrying, so somebody can pick
            # rather than be handed whichever came last
            import pd_tracker
            body = self.read_json() or {}
            # by number where the list knows one for this film, and by name otherwise
            want = str(body.get("title") or "")
            year = int(body.get("year") or 0)
            number = str(body.get("imdb") or "") or S.imdb_number(
                want, year, str(body.get("key") or ""))
            # an episode not here yet, from a season page or the upcoming shelf: its
            # single-episode releases, early ones before the air date included
            episode = re.match(r"^upnext-([0-9a-f]{12})-s(\d+)e(\d+)$",
                               str(body.get("key") or ""))
            # and an episode that is here: other versions of it, as a film has
            held_ep = None
            if not episode and re.match(r"^e[0-9a-f]{12}$", str(body.get("key") or "")):
                con = S.local().lib.db()
                try:
                    held_ep = con.execute(
                        "SELECT i.title, e.season, e.number FROM episode e JOIN item i "
                        "ON i.id = e.item_id WHERE e.id=?", (str(body["key"]),)).fetchone()
                finally:
                    con.close()
            # an episode a pack offers, opened by its own key: an episode too. Read as a
            # film it searched the tracker for the programme's name and put forward a
            # whole-series pack for one episode
            offered_ep = None
            import pd_torrents
            if not episode and not held_ep and pd_torrents.only_in_pack(body.get("key")):
                try:
                    _, offered_ep = pd_torrents.by_key(str(body["key"]))
                except Exception:
                    offered_ep = None
                if offered_ep and offered_ep.get("kind") != "episode":
                    offered_ep = None
            if held_ep:
                episode = True
                want = str(held_ep["title"] or want)
                season, number_ep = int(held_ep["season"]), int(held_ep["number"])
            elif offered_ep:
                episode = True
                want = str(offered_ep.get("name") or offered_ep.get("title") or want)
                season = int(offered_ep.get("season") or 0)
                number_ep = int(offered_ep.get("episode") or 0)
            elif episode:
                con = S.local().lib.db()
                try:
                    row = con.execute("SELECT title FROM item WHERE id=?",
                                      (episode.group(1),)).fetchone()
                finally:
                    con.close()
                want = str(row["title"] if row else want)
                season, number_ep = int(episode.group(2)), int(episode.group(3))
            # a race or a game from the Sports row: its own releases, as the row read them
            sport = None
            if not episode and str(body.get("key") or "").startswith("sp"):
                import pd_sports
                sport = pd_sports.releases_of(str(body["key"]),
                                              list(pd_tracker.read().values()))
            if sport:
                got = list(sport["releases"])[:25]
                want, year = sport["title"], 0
            elif episode:
                got = pd_tracker.episodes_of(want, season).get(number_ep, [])[:25]
                if not got:
                    pd_tracker.ask_for("%s S%02dE%02d" % (want, season, number_ep), 0)
                    got = pd_tracker.episodes_of(want, season).get(number_ep, [])[:25]
                elif body.get("fresh"):
                    pd_tracker.ask_waiting("%s S%02d" % (want, season), 0, S.Handler.FRESH_MOST)
                    got = pd_tracker.episodes_of(want, season).get(number_ep, [])[:25]
                else:
                    threading.Thread(target=pd_tracker.quietly(pd_tracker.ask_for),
                                     args=("%s S%02d" % (want, season), 0),
                                     daemon=True).start()
            else:
                got = pd_tracker.find(want, year, number)[:25]
            # nothing known about this one: ask now rather than hand back an empty
            # list. The feeds only carry what was posted today, so a film that has
            # been in the library for years has nothing in the index until somebody
            # asks about it - which is exactly what opening this list is.
            if episode or sport:
                pass
            elif not got and want:
                pd_tracker.ask_for(want, year)
                got = pd_tracker.find(want, year, number)[:25]
                if not got:
                    # still nothing: the name it goes by here may be short for the one
                    # releases carry - "Readers II" for "Readers II: The Next Chapter" -
                    # so once more with the catalogue's full title, and its IMDb number
                    full, imdb = S.catalogue_title(want, year, str(body.get("key") or ""))
                    flat = lambda t: re.sub(r"[^a-z0-9]+", "", str(t or "").lower())
                    if full and flat(full) != flat(want):
                        pd_tracker.ask_for(full, year)
                        got = pd_tracker.find(full, year, imdb or number)[:25]
            elif want and body.get("fresh"):
                # a page's second ask or a plain press on Download: the search behind the
                # list waited for, and the list as of now
                pd_tracker.ask_waiting(want, year, S.Handler.FRESH_MOST)
                got = pd_tracker.find(want, year, number)[:25]
            elif want:
                # already known: the list opens on what is held and is asked about
                # behind it, so the next open carries whatever has been posted since
                threading.Thread(target=pd_tracker.quietly(pd_tracker.ask_for), args=(want, year),
                                 daemon=True).start()
            here = self.files_here(str(body.get("key") or ""), want, year)
            # how big each one is, which the feed does not carry - it is in the
            # torrent itself, so the ones being offered are read now and kept. Three
            # at a time with a breath between rather than twenty-five at once, and
            # what the budget does not cover is read the next time this list opens.
            pd_tracker.measure_many([r.get("id") for r in got if not r.get("size")])
            held = pd_tracker.read()
            # and the room left where it would land: a size means nothing on its own
            # to somebody deciding whether to fetch a twenty gigabyte release
            free = pd_tracker.room_left()
            most = 0.0 if self.owner_fetching() else self.fetch_most_gb()
            may = self.may_fetch()

            def refused(size):
                """Why this one cannot be fetched, or nothing at all.

                Said against each version rather than after pressing: a list where
                half the rows will be turned away is worth reading before choosing,
                not after.
                """
                gb = (size or 0) / 1073741824.0
                if not may:
                    return "not yours to fetch"
                if most and gb > most:
                    return "over your %.0f GB limit" % most
                if free is not None and gb > free:
                    return "no room: %.0f GB free" % free
                return ""

            out = []
            length = S.runtime_of(str(body.get("key") or ""), want, year)
            for r in got:
                size = int((held.get(str(r.get("id"))) or r).get("size") or 0)
                flat = re.sub(r"[^a-z0-9]+", "", str(r.get("name") or "").lower())
                out.append({"id": r.get("id"), "name": r.get("name"),
                            "kind": r.get("kind", ""),
                            "seeds": int(r.get("seeds") or 0), "size": size,
                            # downloading it counts nothing against the account
                            "freeleech": pd_tracker.freeleech(r),
                            # the picture's bitrate as h264 would need it, Mbit/s
                            "quality": S.quality_of(r.get("name"), size, length),
                            # the picture's own bitrate, Mbit/s, for the list
                            "mbit": (round(S.video_mbit(r.get("name"), size, length) or 0, 1)
                                     if S.video_mbit(r.get("name"), size, length) else None),
                            # and how much of that each gigabyte buys
                            "perGb": S.per_gb(r.get("name"), size, length),
                            # already on the disk: the same release, by name
                            "have": bool(flat and flat in here),
                            "why": refused(size)})
            # and the copies a pack here carries: a version like the others, fetched
            # from the pack rather than the tracker
            try:
                import pd_torrents
                carried = (pd_torrents.episodes_for(want, season, number_ep) if episode
                           else pd_torrents.films_for(want, year))
                swarm = pd_torrents.seeders([c["hash"] for c in carried])
                # a pack the client has not asked its tracker about yet: the tracker's
                # own count of that release, from its list
                pack_names = {str(p.get("hash") or "").lower(): p.get("name")
                              for p in pd_torrents.load()["packs"]}
                listed_seeds = {}
                if any(str(c["hash"]).lower() not in swarm for c in carried):
                    listed_seeds = {pd_tracker.release_flat(r.get("name")): int(r.get("seeds") or 0)
                                    for r in held.values() if r.get("name")}
            except Exception:
                carried, swarm, pack_names, listed_seeds = [], {}, {}, {}
            for c in carried:
                flat = re.sub(r"[^a-z0-9]+", "", os.path.splitext(c["name"])[0].lower())
                # its own row even when the tracker carries the same release: which
                # one it comes from is part of the choice
                out.append({"id": "pack:" + str(c["key"]), "name": c["name"],
                            "kind": "in a pack here", "pack": True,
                            # which pack, shortly: Top 5K
                            "packShort": pd_torrents.pack_short(pack_names.get(str(c["hash"]).lower())),
                            "chosen": bool(c.get("chosen")),
                            # unknown is said as nothing, not as none
                            "seeds": swarm.get(str(c["hash"]).lower()) or listed_seeds.get(
                                pd_tracker.release_flat(pack_names.get(str(c["hash"]).lower()))),
                            "size": c["size"],
                            "quality": S.quality_of(c["name"], c["size"], length),
                            "perGb": S.per_gb(c["name"], c["size"], length),
                            "mbit": (round(S.video_mbit(c["name"], c["size"], length) or 0, 1)
                                     if S.video_mbit(c["name"], c["size"], length) else None),
                            "have": bool(flat and flat in here),
                            "why": refused(c["size"])})
            # and a film inside a box set on the tracker: fetched by adding the box set
            # as a pack and taking this one file
            # - or an episode inside a season pack on the tracker, the same way: an
            # episode with no release of its own had nothing to choose at all
            try:
                import pd_boxsets
                if sport:
                    boxed = []
                elif episode:
                    import pd_torrents
                    boxed = pd_boxsets.episode_packs(
                        want, season, number_ep,
                        skip=[p.get("name") for p in pd_torrents.load()["packs"]])
                else:
                    boxed = pd_boxsets.find(want, year)[:10]
            except Exception:
                boxed = []
            for ident, one, film in boxed:
                shown = os.path.basename(str(film.get("path") or ""))
                flat = re.sub(r"[^a-z0-9]+", "", os.path.splitext(shown)[0].lower())
                out.append({"id": "box:%s:%d" % (ident, int(film["index"])), "name": shown,
                            "kind": ("in a season pack: " if episode else "in a box set: ")
                                    + str(one.get("name") or ""),
                            "box": True, "seasonPack": bool(episode),
                            # the pack itself, to add to the series as a link
                            "packId": str(ident) if episode else "",
                            "seeds": int(one.get("seeds") or 0),
                            "size": int(film.get("size") or 0),
                            "quality": S.quality_of(shown, film.get("size"), length),
                            "perGb": S.per_gb(shown, film.get("size"), length),
                            "mbit": (round(S.video_mbit(shown, film.get("size"), length) or 0, 1)
                                     if S.video_mbit(shown, film.get("size"), length) else None),
                            "have": bool(flat and flat in here),
                            "why": refused(film.get("size"))})
            # and a file on disk that nothing listed matches: the version this house
            # has, whatever the tracker carries now
            listed = {re.sub(r"[^a-z0-9]+", "", os.path.splitext(str(o.get("name") or ""))[0].lower())
                      for o in out}
            for where, size in ([] if episode else
                                self.disk_files(str(body.get("key") or ""), want, year)):
                names = {re.sub(r"[^a-z0-9]+", "", n.lower()) for n in
                         (os.path.splitext(os.path.basename(where))[0],
                          os.path.basename(os.path.dirname(where)))}
                if names & listed:
                    continue
                shown = os.path.basename(os.path.dirname(where))                     if len(os.path.basename(where)) < 24 else os.path.basename(where)
                out.append({"id": "", "name": shown, "kind": "on disk", "disk": True,
                            "have": True, "seeds": 0, "size": size, "why": "",
                            "quality": S.quality_of(shown, size, length),
                            "perGb": S.per_gb(shown, size, length),
                            "mbit": (round(S.video_mbit(shown, size, length) or 0, 1)
                                     if S.video_mbit(shown, size, length) else None)})
            # where each comes from, in the words both the page and the app show
            import pd_words
            for o in out:
                o["tag"] = pd_words.version_tag(o)
            # The one to take unless somebody wants otherwise: the resolution this
            # house prefers, settled among those by whichever of most-carried or
            # smallest the owner chose, and never one this viewer would be turned
            # away from. A 4K remux is four times the disk for a difference a
            # television across a room does not show, which is why the default is
            # 1080p rather than the best thing on offer.
            # what is already on the disk counts among the candidates: if the file
            # here is the one that would be recommended, saying so is the answer to
            # "should I fetch a better one" - and it is not refused for being over a
            # limit, because it is already here.
            able = [o for o in out if not o["why"] or o["have"]]
            by_size = self.prefer_by() == "size"
            if by_size:
                # not something too small to be the film: a sample or a sub pack is a
                # few megabytes and would win every time
                able = [o for o in able
                        if o["size"] > 300 * 1024 * 1024 or o["have"]]
            # Every wish is met where it can be, in the order they are set out here:
            # resolution first, then where the release came from, then the encoding,
            # then the sound. A wish can name several things, any of which meets it,
            # and a wish nothing meets is dropped
            # rather than emptying the list - a house that prefers 4K remuxes still
            # gets a recommendation for a film nobody has posted one of.
            stored = S.read_settings()
            if episode:
                stored = S.for_series(stored)
            wishes = S.Handler.ordered_wishes(stored)
            def settled(one):
                return -one["size"] if by_size else (one["seeds"] or 0)

            # only a release inside every pill is put forward; none, and nothing is
            fits = [o for o in able if S.meets_all(o, wishes, stored, length)]
            best = max(fits, key=lambda o: (S.rank_key(o, wishes, stored, length),
                                            settled(o)), default=None)
            best = S.close_call(best, fits, stored, length)
            if episode:
                chosen = [o for o in able if o.get("chosen")]
                if chosen:
                    best = max(chosen, key=lambda o: (o in fits, o["seeds"] or 0))
            for o in out:
                o["pick"] = bool(best and o is best)
            # what is on disk and nowhere else first, then most seeded; the recommended
            # one is preselected, not moved
            # and the recommended one next: a pack's copy whose seeders nobody knows sank
            # to the foot of the list it was recommended in
            out.sort(key=lambda o: (not o.get("disk"), not o.get("pick"), -(o["seeds"] or 0)))
            # nothing to fetch for what is already here, whatever else it is
            for o in out:
                if o["have"]:
                    o["why"] = ""
            self.reply_json({"free": free, "versions": out})
            return
        return ()

    def _post_part_13(self, path):
        """POST routes, part 13 of 20:
        /shows/top, /skipstart, /skipstart/listen, /subs/checkall, /shows/packs,
        /shows/withpacks, /shows/usepack, /shows/addpack, /shows/add, /shows/stock,
        /shows/remove
        """
        if path == "/shows/top":
            # what is being watched now, and which of it this house has chosen to
            # stand in its library - the owner's choice, like everything that comes in
            if not self.owner_fetching():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            shows = S.top_shows(bool(body.get("fresh")))
            chosen = {int(x["tmdb"]) for x in S.library_shows() if x["tmdb"]}
            con = S.local().lib.db()
            try:
                held = {int(r["tmdb_id"] or 0) for r in con.execute(
                    "SELECT tmdb_id FROM item WHERE type='show'")}
            finally:
                con.close()
            self.reply_json({"shows": [dict(x, chosen=x["tmdb"] in chosen,
                                            here=x["tmdb"] in held) for x in shows]})
            return
        if path == "/skipstart":
            # set or remove one skip rule: {show, season, episode, seconds}; 0 s removes
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_leads
            body = self.read_json() or {}
            show = str(body.get("show") or "")
            try:
                season = max(0, int(body.get("season") or 0))
                episode = max(0, int(body.get("episode") or 0)) if season else 0
                seconds = float(body.get("seconds") or 0)
            except (TypeError, ValueError):
                self.reply_json({"error": "season, episode and seconds are numbers"}, 400)
                return
            if not (0 <= seconds <= pd_leads.MOST_SECS):
                self.reply_json({"error": "0 to %d seconds" % pd_leads.MOST_SECS}, 400)
                return
            con = S.local().lib.db()
            try:
                known = con.execute("SELECT 1 FROM item WHERE id=? AND type='show'",
                                    (show,)).fetchone()
            finally:
                con.close()
            if not known:
                self.reply_json({"error": "no such programme"}, 404)
                return
            stored = self.settings_file()
            stored["skipStart"] = pd_leads.with_rule(stored.get("skipStart"), show, season,
                                                     episode, seconds)
            S.write_settings(stored, merge=False)
            self.reply_json({"rules": self.lead_rows(stored["skipStart"])})
            return
        if path == "/skipstart/listen":
            # fingerprint a programme's held episodes and suggest a rule per season
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_gpu
            import pd_leads
            body = self.read_json() or {}
            show = str(body.get("show") or "")
            try:
                only = int(body.get("season") or 0)
            except (TypeError, ValueError):
                only = 0
            seasons = {}
            con = S.local().lib.db()
            try:
                for r in con.execute(
                        "SELECT e.season, e.number, f.path FROM episode e "
                        "JOIN file f ON f.episode_id=e.id WHERE e.item_id=? AND e.season>0 "
                        "ORDER BY e.season, e.number, f.size DESC", (show,)):
                    if only and int(r["season"]) != only:
                        continue
                    seasons.setdefault(int(r["season"]), {}).setdefault(int(r["number"]), r["path"])
            finally:
                con.close()
            if not seasons:
                self.reply_json({"error": "no episodes held"}, 404)
                return
            if not pd_leads.listen(show, seasons, pd_gpu.FFMPEG or "ffmpeg"):
                self.reply_json({"error": "already listening"}, 409)
                return
            self.reply_json({"listening": pd_leads.state()})
            return
        if path == "/subs/checkall":
            # every episode held of a programme or a season - or one film - put on the
            # list of subtitles to go through, in one language
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            lang = str(body.get("lang") or "en").lower()[:5]
            season = re.match(r"^([0-9a-f]{12})-s(\d+)$", key)
            con = S.local().lib.db()
            # set before the read: a query that failed left it unset and the lines
            # after the try crashed on it
            keys = []
            try:
                if season:
                    keys = [str(r["id"]) for r in con.execute(
                        "SELECT DISTINCT e.id, e.number FROM episode e JOIN file f ON f.episode_id=e.id "
                        "WHERE e.item_id=? AND e.season=? ORDER BY e.number",
                        (season.group(1), int(season.group(2))))]
                else:
                    keys = [str(r["id"]) for r in con.execute(
                        "SELECT DISTINCT e.id, e.season, e.number FROM episode e "
                        "JOIN file f ON f.episode_id=e.id WHERE e.item_id=? "
                        "ORDER BY e.season, e.number", (key,))]
                    if not keys and con.execute("SELECT 1 FROM file WHERE item_id=? AND episode_id IS NULL",
                                                (key,)).fetchone():
                        keys = [key]
                good = {str(r["key"]) for r in con.execute(
                    "SELECT key FROM subcheck WHERE lang=? AND verdict IN ('fits','verified','inside')",
                    (lang,))} if keys else set()
            except Exception:
                good = set()
            finally:
                con.close()
            todo = [k for k in keys if k not in good]
            rows = S.subcheck_asked(add=[(k, lang) for k in todo])
            self.reply_json({"held": len(keys), "good": len(keys) - len(todo),
                             "asked": len(todo), "waiting": len(rows)})
            return
        if path == "/shows/packs":
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            number = str(body.get("tmdb") or 0)
            self.reply_json(S.show_packs(str(body.get("show") or ""), bool(body.get("look")),
                                       int(number) if number.isdigit() else 0))
            return
        if path == "/shows/withpacks":
            # every series a pack added here carries, for the filter on the Series tab
            if not self.owner_fetching():
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            self.reply_json({"shows": pd_torrents.shows_with_packs()})
            return
        if path == "/shows/usepack":
            if not self.owner_fetching():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            self.reply_json(S.use_show_pack(str(body.get("show") or ""),
                                          str(body.get("id") or ""),
                                          str(body.get("hash") or ""),
                                          bool(body.get("drop")),
                                          int(body.get("season") or 0),
                                          bool(body.get("unlink"))))
            return
        if path == "/shows/addpack":
            # a popular programme's page: into the library, with the version chosen
            # there added as a pack - a link, nothing downloaded
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            show = str(body.get("show") or "")
            key = S.add_popular(show, int(body.get("tmdb") or 0))
            if not key:
                self.reply_json({"error": "could not add that one"})
                return
            said = {"key": key}
            # a series added from its pack: on the watchlist of whoever added it
            self.onto_watchlist(key)
            if body.get("id"):
                got = S.use_show_pack(show, str(body.get("id")))
                if got.get("error"):
                    said["error"] = got["error"]
            self.reply_json(said)
            return
        if path == "/shows/add":
            # a card off the popular list pressed: ticked, given its library entry,
            # and the key handed back so the page can open it
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            key = S.add_popular(str(body.get("show") or ""), int(body.get("tmdb") or 0))
            if key:
                # a series added from its pack: on the watchlist of whoever added it
                self.onto_watchlist(key)
            self.reply_json({"key": key} if key else {"error": "could not add that one"})
            return
        if path == "/shows/stock":
            if not self.owner_fetching():
                self.send_error(403, "not allowed")
                return
            self.reply_json({"added": S.stock_shows()})
            return
        if path == "/shows/remove":
            # a programme out of the follow list and the library list, and its packs
            # nothing was downloaded from: links only, files stay
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            name = str((self.read_json() or {}).get("show") or "").strip()
            if not name:
                self.reply_json({"error": "which programme"}, 400)
                return

            def other(t):
                said = t.get("show") if isinstance(t, dict) else t
                return str(said or "").strip().lower() != name.lower()
            stored = S.read_settings() or {}
            stored["libraryShows"] = [t for t in stored.get("libraryShows") or [] if other(t)]
            stored["followShows"] = [t for t in stored.get("followShows") or [] if other(t)]
            S.write_settings(stored)
            out = pd_torrents.remove_show(name)
            threading.Thread(target=S.show_rows, daemon=True).start()
            self.reply_json(out)
            return
        return ()

    def _post_part_14(self, path):
        """POST routes, part 14 of 20:
        /tracker/gaps, /tracker/season, /tracker/fillseason, /tracker/waiting,
        /tracker/grab, /tracker/look, /tracker/pack, /tracker/search, /tracker/active,
        /tracker/cancel, and 2 more
        """
        if path == "/tracker/gaps":
            # the seasons of each followed programme this house cannot reach, and
            # what the tracker has that would cover them
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            if body.get("shows"):
                want = [{"show": str(t), "mode": "all", "from": 0, "to": 0}
                        for t in body["shows"]]
            else:
                want = S.follow_list()
            out = []
            for one in want[:20]:
                # a programme followed forward only has no gaps to fill: the old
                # seasons are deliberately left alone
                if one["mode"] == "forward":
                    continue
                said = S.season_gaps(one["show"])
                said["missing"] = [n for n in said["missing"]
                                   if S.followed_season(one, n)]
                said["mode"] = one["mode"]
                if said["missing"]:
                    out.append(said)
            self.reply_json({"gaps": out})
            return
        if path == "/tracker/season":
            # what would cover one season, without adding anything
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            self.reply_json(S.season_offers(str(body.get("show") or ""),
                                          int(body.get("season") or 0)))
            return
        if path == "/tracker/fillseason":
            # and adding it: a pack is a link, so this downloads nothing
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            self.reply_json(S.fill_season(str(body.get("show") or ""),
                                        int(body.get("season") or 0)))
            return
        if path == "/tracker/waiting":
            # the new episodes of followed programmes: what the feeds have seen, and
            # what the watcher will do about each of them
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            self.reply_json({"waiting": S.shows_waiting()})
            return
        if path == "/tracker/grab":
            # one of those, now, rather than when the good release lands
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            self.reply_json(S.grab_episode(str(body.get("mark") or "")))
            return
        if path == "/tracker/look":
            # Search the tracker for whatever somebody typed, and say what it holds:
            # the name, how big, how many are carrying it, and whether it is a pack of
            # a whole series or one film. Nothing is fetched by looking.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            body = self.read_json() or {}
            words = str(body.get("words") or "").strip()
            if not words:
                self.reply_json({"found": [], "why": "nothing to look for"})
                return
            if not pd_tracker.cookie():
                self.reply_json({"found": [], "why": "no session: sign in on the "
                                                     "tracker and hand the cookie over"})
                return
            got = pd_tracker.look_for(words)
            # the tracker drops one-letter words ("V 1984" came back as every 1984
            # release): names starting with every word typed come first, then names
            # holding all of them, each in the tracker's own order
            want = re.findall(r"[a-z0-9]+", words.lower())
            def fits(r):
                said = re.findall(r"[a-z0-9]+", str(r.get("name") or "").lower())
                if said[:len(want)] == want:
                    return 0
                return 1 if all(w in said for w in want) else 2
            got = sorted(got, key=fits)
            self.reply_json({"free": pd_tracker.room_left(),
                             "why": pd_tracker.state().get("why") or "",
                             "found": [
                                 {"id": r.get("id"), "name": r.get("name"),
                                  "kind": r.get("kind", ""),
                                  "seeds": int(r.get("seeds") or 0),
                                  "size": int(r.get("size") or 0),
                                  "imdb": r.get("imdb", "")} for r in got]})
            return
        if path == "/music/playlists":
            # the music video playlists: read by anyone, changed by the owner; with them
            # every music video, for the editor
            import pd_music, pd_localapi
            body = self.read_json() or {}
            do = str(body.get("do") or "")
            if do and self.role != "owner":
                self.send_error(403, "not allowed")
                return
            said = pd_music.change(do, body.get("id"), body.get("name"), body.get("keys"))                 if do else {"playlists": pd_music.load()}
            con = S.local().lib.db()
            try:
                said["videos"] = [
                    {"key": str(r["id"]), "title": str(r["title"] or ""),
                     "thumb": "/art/%s/poster" % r["id"] if r["poster"] else None}
                    for r in con.execute(
                        "SELECT id, title, poster FROM item WHERE type='movie' AND "
                        + pd_localapi.MUSIC_VIDEO + " ORDER BY title")]
            finally:
                con.close()
            S.local().start_forget(self.viewer())
            self.reply_json(said)
            return
        if path in ("/webget/start", "/webget/list", "/webget/cancel"):
            # a video from a web page through yt-dlp into the download folder; copy-protected
            # streams are refused by yt-dlp and said as such
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_webget, pd_live, pd_torrents, pd_gpu
            body = self.read_json() or {}
            if path == "/webget/start":
                exe = pd_live.ytdlp()
                self.reply_json(pd_webget.start(body.get("url"), pd_torrents.save_folder(),
                                                [exe] if exe else [], pd_gpu.FFMPEG or "",
                                                pd_live.ytdlp_env(),
                                                music=bool(body.get("music"))))
            elif path == "/webget/cancel":
                self.reply_json({"ok": pd_webget.cancel(body.get("id"))})
            else:
                self.reply_json({"jobs": pd_webget.listed()})
            return
        if path == "/tracker/pack":
            # Take one release as a pack: everything in it becomes something that can
            # be fetched a film or an episode at a time, rather than the whole thing
            # landing on the disk at once.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import base64
            import pd_tracker, pd_torrents
            body = self.read_json() or {}
            raw = pd_tracker.torrent_of(str(body.get("id") or ""))
            if not raw:
                self.reply_json({"ok": False, "why": "the tracker would not hand it over"})
                return
            self.reply_json(pd_torrents.add_pack(raw=raw))
            return
        if path == "/tracker/search":
            # Ask the tracker now, rather than waiting for the hour to come round.
            # For a list that has a hole in it - the feeds carry one day, so a machine
            # that was off over a weekend never learns what was posted meanwhile.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            body = self.read_json() or {}
            many = max(1, min(25, int(body.get("many") or 5)))
            if not pd_tracker.state().get("asks"):
                self.reply_json({"ok": False,
                                 "why": "this machine reads the list; the one it "
                                        "follows does the asking"})
                return
            if not pd_tracker.cookie():
                self.reply_json({"ok": False,
                                 "why": "no session: sign in on the tracker and hand "
                                        "the browser's cookie over"})
                return
            threading.Thread(target=pd_tracker.ask_about, args=(many,),
                             daemon=True).start()
            self.reply_json({"ok": True, "asking": many})
            return
        if path == "/tracker/active":
            # what is coming in from the tracker now, so a page can show how far it
            # has got and offer to stop it
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            self.reply_json({"taking": pd_tracker.taking()})
            return
        if path == "/tracker/cancel":
            # Stop one and take the part-file with it. Whoever may fetch may stop:
            # the alternative is a disk filling with something pressed by mistake
            # and only the owner able to undo it.
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            body = self.read_json() or {}
            self.reply_json(pd_tracker.drop(str(body.get("hash") or ""),
                                            self.name_of(self.bearer() or "me")))
            return
        if path == "/tracker/cookie":
            # The signed-in session to search the tracker with. Its login form has a
            # captcha, so no machine can sign in by itself: a person signs in and
            # hands the session over. Kept on the machine, never shown back.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            body = self.read_json() or {}
            self.reply_json(pd_tracker.keep_cookie(str(body.get("cookie") or "")))
            return
        if path == "/downloads/log":
            # who downloaded what: films from the packs, with who asked, and releases
            # taken straight from the tracker, newest first
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            import pd_tracker
            rows = []
            for d in pd_torrents.load()["downloads"]:
                name = str(d.get("title") or "")
                if d.get("kind") == "episode" and d.get("season") is not None:
                    name += " S%02dE%02d" % (int(d.get("season") or 0),
                                             int(d.get("episode") or 0))
                rows.append({"at": int(d.get("when") or 0), "who": d.get("who") or "",
                             "name": name, "size": int(d.get("size") or 0),
                             "from": "pack", "auto": bool(d.get("auto")),
                             "reason": d.get("reason") or "",
                             "state": d.get("state") or ""})
            with pd_tracker.LOCK:
                taken = list(pd_tracker.STATE.get("log") or [])
            for t in taken:
                rows.append(dict(t, **{"from": "tracker", "state": ""}))
            rows.sort(key=lambda r: -r["at"])
            self.reply_json({"downloads": rows[:400]})
            return
        return ()

    def _post_part_15(self, path):
        """POST routes, part 15 of 20:
        /tracker/get, /torrents/recheck, /torrents/order, /torrents/cancel,
        /follow/managed, /follow/tonight, /follow/stop, /follow/now
        """
        if path == "/tracker/get":
            # A film the tracker is carrying, handed to the torrent client. What comes
            # into the house is the owner's to decide, as with everything else that
            # fetches by itself.
            # the owner always - by address at home, by their own key from away -
            # and everybody else when the house has been opened up
            if not self.may_fetch():
                self.send_error(403, "not allowed")
                return
            import pd_tracker
            body = self.read_json() or {}
            ident = str(body.get("id") or "")
            want = str(body.get("title") or "")
            year = int(body.get("year") or 0)
            if ident.startswith("box:"):
                # a film inside a box set on the tracker: the box set becomes a pack
                # here, and this film is fetched from it like any pack's
                import pd_boxsets
                made = pd_boxsets.take(ident[4:])
                if not made.get("ok"):
                    self.reply_json({"error": made.get("why") or "could not add the box set"})
                    return
                ident = "pack:" + made["key"]
            if ident.startswith("pack:"):
                # a copy in a pack here: the same as fetching it from the film's page
                import pd_torrents
                token = self.bearer() or "me"
                cap = self.weekly_limits("downloadGbWeek").get(
                    self.name_of(token).strip().lower(), 0.0)
                most = 0.0 if self.owner_fetching() else self.fetch_most_gb()
                got = pd_torrents.request(ident[5:], token, self.watcher(), cap, most)
                if got.get("ok"):
                    self.watch_fetched(ident[5:])
                    _, film = pd_torrents.by_key(ident[5:])
                    got = dict(got, taken=True,
                               name=os.path.basename(str((film or {}).get("path") or want)))
                else:
                    got = dict(got, error=got.get("why") or "could not fetch that one")
                self.reply_json(got)
                return
            # Not the one already on the disk. The list says which that is and its
            # button is green rather than a press, but the answer belongs here too:
            # a second copy of the same release is the same file under another name.
            said = (pd_tracker.read().get(ident) or {}) if ident else {}
            flat = re.sub(r"[^a-z0-9]+", "", str(said.get("name") or "").lower())
            if flat and flat in self.files_here(str(body.get("key") or ""), want, year):
                self.reply_json({"error": "that one is already here"})
                return
            took = pd_tracker.take(
                ident, want, year,
                0.0 if self.owner_fetching() else self.fetch_most_gb(),
                who=self.watcher(), key=str(body.get("key") or ""),
                token=self.bearer() or "me")
            if took.get("taken"):
                self.watch_fetched(str(body.get("key") or ""), str(took.get("name") or ""))
            self.reply_json(took)
            return
        if path == "/workers/report":
            # a worker running outside the server - a script on this machine - put
            # in the Workers boxes with what it is doing and how far it has got
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            wid = str(body.get("id") or "")[:40]
            if wid:
                # link: the worker's own page, opened by clicking its box
                link = str(body.get("link") or "")[:200]

                def count(v):
                    try:
                        return max(0, int(v))
                    except (TypeError, ValueError):
                        return None
                S.OUTSIDE[wid] = {"name": str(body.get("name") or wid)[:40],
                                "step": str(body.get("step") or "")[:120],
                                "done": count(body.get("done")), "left": count(body.get("left")),
                                "link": link if re.match(r"https?://", link) else "",
                                "at": time.time()}
            self.reply_json({"ok": bool(wid)})
            return
        if path == "/follow/taketorrent":
            # A torrent of the main server's whose film this machine's pack holds whole:
            # put where that film is, with its small files beside it, read, and seeded
            # once it reads whole. Only the main server, managing this machine.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import base64
            import pd_torrents
            body = self.read_json() or {}
            ph, index = str(body.get("pack") or ""), S.index_of(body)
            if not pd_torrents.whole_file(ph, index):
                self.reply_json({"ok": False, "why": "the film is not whole in the pack here"})
                return
            location = pd_torrents.location_for(pd_torrents.file_of(ph, index), str(body.get("file") or ""))
            if not location:
                self.reply_json({"ok": False, "why": "its file name does not match the pack's"})
                return
            qb = pd_torrents.QB(pd_torrents.load()["config"])
            h = str(body.get("hash") or "")
            if qb.info(h) is None:
                for x in body.get("extras") or []:
                    onto = pd_torrents.inside(location, x.get("name") or "")
                    if onto and not os.path.exists(onto):
                        os.makedirs(os.path.dirname(onto), exist_ok=True)
                        with open(onto, "wb") as fh:
                            fh.write(base64.b64decode(x.get("data") or ""))
                qb.add_at(base64.b64decode(body.get("torrent") or ""), location,
                          str(body.get("category") or ""))
                for _ in range(30):
                    if qb.info(h) is not None:
                        break
                    time.sleep(1)
                qb.recheck(h)
                threading.Thread(target=pd_torrents.seed_when_read, args=(h,), daemon=True).start()
            self.reply_json({"ok": True, "location": location})
            return
        if path == "/torrents/home":
            # which machine a pack lives on: this one, or the copy server
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            body = self.read_json() or {}
            if "splitFirst" in body:
                self.reply_json(pd_torrents.set_split_first(str(body.get("splitFirst") or "")))
                return
            said = pd_torrents.set_home(str(body.get("hash") or ""),
                                        str(body.get("home") or "main"))
            if said.get("ok"):
                S.Handler.home_moved(str(body.get("hash") or ""), said["home"])
            self.reply_json(said)
            return
        if path == "/torrents/drop":
            # films the owner takes out of a pack, once it is seeded enough: off and deleted
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            body = self.read_json() or {}
            # whenSeeded: what is not seeded enough yet waits, and goes once it is
            drop = pd_torrents.drop_when_seeded if body.get("whenSeeded") else pd_torrents.drop_films
            self.reply_json({"films": drop(
                str(body.get("hash") or ""), [int(i) for i in body.get("indexes") or []])})
            return
        if path == "/torrents/whole":
            # a torrent fetched and seeded whole, or back to the files asked for
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            body = self.read_json() or {}
            self.reply_json(pd_torrents.keep_whole(str(body.get("hash") or ""),
                                                   body.get("on", True) is not False))
            return
        if path == "/torrents/recheck":
            # Read the files on disk again for a pack qBittorrent has stopped on, or
            # for every one of them. The server does this once by itself; this is the
            # press that says try it again anyway.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_torrents
            body = self.read_json() or {}
            self.reply_json(pd_torrents.mend_halted(str(body.get("hash") or ""),
                                                    force=True))
            return
        if path == "/torrents/order":
            # the waiting downloads in the order given: the owner's all, a guest's own
            import pd_torrents
            body = self.read_json() or {}
            self.reply_json(pd_torrents.reorder(
                body.get("keys") or [],
                None if self.role == "owner" else (self.bearer() or "me")))
            return
        if path == "/torrents/cancel":
            # a download stopped: by the owner, or by whoever asked for it
            import pd_torrents
            body = self.read_json() or {}
            self.reply_json(pd_torrents.cancel(str(body.get("key") or ""), self.bearer() or "me",
                                               self.role == "owner"))
            return
        if path == "/follow/managed":
            # A cache's copying settings, read and set from here: only a machine
            # that follows this one, at the address it announced, and only paths about
            # its copying. The cache refuses unless it allows the main server to.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import urllib.request as _ureq
            import urllib.error as _uerr
            body = self.read_json() or {}
            there = str(body.get("where") or "").rstrip("/")
            known = S.Handler.FOLLOWERS.get(there) or {}
            asked = str(body.get("path") or "")
            method = "POST" if body.get("method") == "POST" else "GET"
            allowed = {("GET", "/follow"), ("POST", "/follow"), ("POST", "/follow/clear"),
                       ("POST", "/follow/extras"), ("POST", "/follow/claim"),
                       ("GET", "/follow/test"), ("GET", "/follow/now")}
            if not known or (method, asked.split("?", 1)[0]) not in allowed:
                self.reply_json({"error": "Not a machine following this server, "
                                          "or not one of its copying settings."})
                return
            called = known.get("name") or there
            req = _ureq.Request(
                there + asked, method=method,
                data=(json.dumps(body.get("body") or {}).encode("utf-8")
                      if method == "POST" else None),
                headers={"X-Palladium-House": "1", "Content-Type": "application/json"})
            try:
                with _ureq.urlopen(req, timeout=60) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
                # this server keeps the copy server's settings: what was set from here,
                # and the first time, what the copy server had
                if asked.split("?", 1)[0] == "/follow" and known.get("token"):
                    import pd_follow
                    stored = S.read_settings() or {}
                    kept = dict((stored.get("managedCopies") or {}).get(known["token"]) or {})
                    theirs = said.get("follow") if isinstance(said.get("follow"), dict) else {}
                    if method == "POST":
                        sent = {k: v for k, v in (body.get("body") or {}).items()
                                if k in pd_follow.MANAGED}
                        kept = pd_follow.apply_settings(dict(kept or theirs), sent)
                    elif not kept:
                        kept = {k: theirs[k] for k in pd_follow.MANAGED if k in theirs}
                    kept = {k: kept[k] for k in pd_follow.MANAGED if k in kept}
                    stored.setdefault("managedCopies", {})[known["token"]] = kept
                    S.write_settings(stored)
                self.reply_json(said)
            except _uerr.HTTPError as e:
                self.reply_json({"error": "%s refused (%d)%s" % (
                    called, e.code, ": Managed from the main server is off there"
                    if e.code in (401, 403) else "")})
            except Exception as e:
                self.reply_json({"error": "%s did not answer: %s" % (called, str(e)[:100])})
            return
        if path == "/follow/tonight":
            # Take tonight's copies now. This machine cannot tell the other one
            # anything - it is the other one that asks - so the answer is left here
            # and handed over the next time it does, which is within the minute.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            S.Handler.EARLY_UNTIL = time.time() + 10 * 3600
            self.reply_json({"ok": True,
                             "said": "The other machine takes tonight's copies from "
                                     "its next round, within a minute."})
            return
        if path == "/follow/stop":
            # Stop one machine that follows this one, or let it back in. The key is
            # untouched: this is about a machine, not about the key it holds, and
            # rotating the key would stop every other cache with it.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            where = str(body.get("where") or "").strip().rstrip("/")
            cfg = S.local().lib.config()
            blocked = [str(x) for x in (cfg.get("blockedCaches") or [])]
            if where:
                if body.get("allow"):
                    blocked = [x for x in blocked if x.rstrip("/") != where]
                elif where not in blocked:
                    blocked.append(where)
            cfg["blockedCaches"] = blocked
            S.local().lib.save_config(cfg)
            self.reply_json({"blocked": blocked})
            return
        if path == "/follow/now":
            # Build the list again and take what is missing, now: what is worth
            # keeping changed the moment somebody finished an episode.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path else {})
            early = (args.get("early") or [""])[0] in ("1", "true", "yes")
            self.reply_json(pd_follow.sync_now(lambda: S.local().lib.config(),
                                               S.local().lib, S.local(), early=early))
            return
        return ()

    def _post_part_16(self, path):
        """POST routes, part 16 of 20: /follow, /performance, /machine/addons."""
        if path == "/follow":
            # this server following another one
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_follow
            body = self.read_json() or {}
            cfg = S.local().lib.config()
            one = pd_follow.settings(cfg)
            # managed from the main server: what it sets is set there and locked here
            locked = []
            if pd_follow.managed(one) and not (self.headers.get("X-Palladium-House")
                                               and self.managed_from_the_house()):
                locked = [k for k in pd_follow.MANAGED if k in body]
                body = {k: v for k, v in body.items() if k not in pd_follow.MANAGED}
            pd_follow.apply_settings(one, body)
            cfg["follow"] = one
            # A folder of copies nothing scans is a folder of copies nobody can
            # watch: the cache joins the library as a mixed folder, films and
            # episodes together, which is how they arrive.
            if one.get("folder"):
                mixed = [f for f in (cfg.get("mixed") or [])]
                here = os.path.normcase(os.path.abspath(one["folder"]))
                known = any(os.path.normcase(os.path.abspath(f)) == here
                            for f in (list(mixed) + list(cfg.get("movies") or [])
                                      + list(cfg.get("tv") or [])))
                if not known:
                    mixed.append(one["folder"])
                    cfg["mixed"] = mixed
            S.local().lib.save_config(cfg)
            # what is being read this minute, so the sweep leaves it alone
            pd_follow.BUSY = S.WATCHING.paths
            pd_follow.start(lambda: S.local().lib.config(), S.local().lib,
                            lambda: S.Handler.game_holds("copies"),
                            (S.PORT, socket.gethostname(), S.STATIC,
                             S.Handler.build_version(), S.settings_path()),
                            S.learn_invites, S.local(), S.carry_the_keys)
            self.reply_json({"follow": one, "state": pd_follow.look(),
                             "locked": locked})
            return
        if path == "/performance":
            # Game mode: the machine is wanted for something else. Nothing that can
            # wait is started, and what is running that can be stopped is stopped.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            if "game" in body or "does" in body:
                stored = S.read_settings() or {}
                game = self.game_mode()
                for name in self.GAME_DOES:
                    if name in (body.get("does") or {}):
                        game[name] = bool(body["does"][name])
                if "game" in body:
                    game["on"] = bool(body["game"])
                stored["gameMode"] = game
                S.write_settings(stored)
                if game["on"] and game.get("subs"):
                    try:
                        import pd_ai_subs
                        pd_ai_subs.stop()      # the card is wanted elsewhere
                    except Exception:
                        pass
            self.reply_json(self.how_busy())
            return
        if path == "/machine/addons":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            import pd_ai_subs
            os.environ["PALLADIUM_DATA"] = S.ROOT
            body = self.read_json() or {}
            which = str(body.get("id") or "")
            if "on" in body:
                stored = S.read_settings() or {}
                switched = dict(stored.get("addons") or {})
                switched[which] = bool(body["on"])
                stored["addons"] = switched
                S.write_settings(stored)
            if body.get("tools") in ("off", "on"):
                # let go of, or taken up again. The scripts stay on the disk: this
                # is whether this server runs them, not whether it has them.
                stored = S.read_settings() or {}
                stored["toolsOff"] = body["tools"] == "off"
                S.write_settings(stored)
            if body.get("tools") == "fetch":
                # over a copy that is already here only when asked twice
                have = (pd_ai_subs.tools() or {}).get("version")
                if have and not body.get("overwrite"):
                    self.reply_json({"ask": True, "have": have,
                                     "where": "palladium.video"})
                    return
                said = pd_ai_subs.get_tools()
                if said.get("error"):
                    self.reply_json(said, 409)
                    return
            elif body.get("fetch"):
                said = pd_ai_subs.get_addon(which)
                if said.get("error"):
                    self.reply_json(said, 409)
                    return
            if body.get("remove"):
                pd_ai_subs.drop_addon(which)
            stored = S.read_settings() or {}
            self.reply_json({"addons": pd_ai_subs.addons(stored.get("addons") or {}),
                             "can": pd_ai_subs.ready() and S.tools_on(),
                             "toolsOff": bool(stored.get("toolsOff")),
                             "tools": pd_ai_subs.tools()})
            return
        return ()

    def _post_part_17(self, path):
        """POST routes, part 17 of 20:
        /subs/make, /subs/stop, /subs/keepalive, /subs/auto, /network/check, /network/open,
        /feedback/delete, /feedback/restore, /feedback/cancel, /follow/keys
        """
        if path == "/subs/make":
            # Hear the film and write down what is said. The file lands beside the
            # video with "ai-gen" in its name, so nobody mistakes it for one a person
            # wrote and published.
            import pd_ai_subs
            body = self.read_json() or {}
            key = str(body.get("key") or "")
            want = str(body.get("language") or "en").lower()[:2]
            src = S.local().file_for(key, int(body.get("mi") or 0))
            if not src:
                self.reply_json({"error": "no file for that title"}, 404)
                return
            if not (pd_ai_subs.ready() and S.tools_on()):
                # This machine cannot hear a film - the speech model is three
                # gigabytes and the machine that keeps copies has no card to run it
                # on. The one it copies from has both, and the subtitle written there
                # arrives here on the next pass like any other file beside a video.
                handed = self.hand_subs_over(key, want)
                if handed is not None:
                    self.reply_json(handed)
                    return
                self.reply_json({"error": "This server has no speech model installed."},
                                409)
                return
            if self.game_holds("subs"):
                self.reply_json({"error": "Game mode is on: the graphics card is "
                                          "wanted for something else."}, 409)
                return
            switched = (S.read_settings() or {}).get("addons") or {}
            if not switched.get("speech", True):
                self.reply_json({"error": "Speech to text is turned off in Settings, "
                                          "This computer."}, 409)
                return
            if want == "sv" and not switched.get("translate", True):
                self.reply_json({"error": "Translation is turned off in Settings, "
                                          "This computer."}, 409)
                return
            os.environ["PALLADIUM_DATA"] = S.ROOT      # where the model is kept
            # every copy of this film: one listening, and the answer written beside
            # each of them, because which copy plays is the library's decision
            copies = []
            con = S.local().lib.db()
            try:
                where = ("episode_id=?" if str(key).startswith("e")
                         else "item_id=? AND episode_id IS NULL")
                # the key as it is stored: an episode's with its e, a title's as hex.
                # This took the number out of it, which a hex key has none of - the
                # ValueError was swallowed below and no copy was ever found to listen to.
                number = str(key)
                for row in con.execute("SELECT path FROM file WHERE " + where, (number,)):
                    if os.path.exists(row["path"]):
                        copies.append(row["path"])
            except (TypeError, ValueError):
                pass
            finally:
                con.close()
            self.reply_json(pd_ai_subs.ask(src["file"], want,
                                           S.local().now_playing_fields(key)[0] or "",
                                           copies, key, self.names_in(key)))
            return
        if path == "/subs/stop":
            import pd_ai_subs
            body = self.read_json() or {}
            self.reply_json(pd_ai_subs.stop(str(body.get("out") or "")))
            return
        if path == "/subs/keepalive":
            self.reply_json({"outcome": S.subtitle_keepalive()})
            return
        if path == "/subs/auto":
            body = self.read_json() or {}
            self.reply_json(self.auto_subtitle(body.get("key") or "",
                                               body.get("after") or ""))
            return
        if path == "/network/check":
            ok, nodes, note = S.reachable_from_outside(S.PORT)
            self.reply_json({"ok": ok, "address": "%s:%d" % (S.wan_ip(), S.PORT),
                             "nodes": nodes, "message": note})
            return
        if path == "/network/open":
            # asking the router to forward the port is an outward-facing thing, so it
            # happens on a press rather than at startup
            import pd_upnp as upnp
            ok, message = upnp.forward(S.LAN_IP, S.PORT)
            if not ok:
                message += (" Forward TCP %d to %s in the router at %s, and the links "
                            "will work." % (S.PORT, S.LAN_IP, S.gateway()))
            self.reply_json({"ok": ok, "message": message})
            return
        if path == "/feedback/delete":
            ident = (self.read_json() or {}).get("id") or ""
            self.file_away(lambda row: row.get("id") == ident)
            self.reply_json({"reports": self.reports()})
            return
        if path == "/feedback/restore":
            # a tick pressed by mistake, or a row cleared too soon
            self.unfile((self.read_json() or {}).get("id") or "")
            self.reply_json({"reports": self.reports(), "filed": self.filed()})
            return
        if path == "/feedback/cancel":
            # Anybody may take back what they wrote, and nobody else's. A guest is
            # known by the name on their invitation, which is what was written down
            # with the report; the owner's rows say "you".
            body = self.read_json() or {}
            ident = str(body.get("id") or "")
            mine = "you" if self.role == "owner" else (self.guest_name or "")
            row = next((r for r in self.reports() if r.get("id") == ident), None)
            if not row:
                self.reply_json({"error": "no such report"}, 404)
                return
            if (row.get("who") or "") != mine:
                self.refused(path, "that report belongs to somebody else")
                self.reply_json({"error": "that one is not yours"}, 403)
                return
            self.amend_report(ident, {"done": True, "doneWhen": int(time.time()),
                                      "withdrawn": True,
                                      "solution": "Withdrawn by " + mine})
            self.reply_json({"reports": self.reports()})
            return
        if path == "/follow/keys":
            # Hand this house's catalogue keys to the machine that keeps copies.
            #
            # It fetches subtitles for the films it takes and looks their posters up
            # for itself when the main server cannot be reached; without keys of its own it
            # holds films half the main server cannot read and a shelf of grey rectangles.
            # Sent rather than copied by hand because the address and the invitation
            # are already here. The owner's, and only to this house's own cache.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            one = self.standby_now()
            if not one.get("where"):
                self.reply_json({"ok": False, "why": "nothing follows this server"})
                return
            cfg = S.local().lib.config()
            #: which of them to send: named, so a page can offer one button or two
            want = [k for k in ("opensubtitles_key", "tmdb_key")
                    if not body.get("only") or body.get("only") == k]
            # and the login that goes with the subtitle key. A key searches; only a
            # login downloads, and it carries the daily allowance. Sending one without
            # the other gave the other machine a list of subtitles it could see and
            # could not fetch, which is the same as no subtitles at all.
            if "opensubtitles_key" in want:
                want += ["opensubtitles_user", "opensubtitles_pass"]
            send = {k: (cfg.get(k) or "").strip() for k in want}
            send = {k: v for k, v in send.items() if v}
            if not send:
                self.reply_json({"ok": False, "why": "no key here to send"})
                return
            token = ""
            for row in S.INVITES.load():
                if row.get("follows") and row.get("token"):
                    token = row["token"]
                    break
            if not token:
                self.reply_json({"ok": False, "why": "that machine has no key here"})
                return
            try:
                req = urllib.request.Request(
                    one["where"].rstrip("/") + "/library/config?t=" +
                    urllib.parse.quote(token),
                    data=json.dumps(send).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as answer:
                    answer.read()
                # written down: which of this house's catalogue keys that machine
                # has been given, so nobody has to remember whether they sent them
                self.note_cache(sent=dict(
                    (S.Handler.FOLLOWERS.get(S.Handler.STANDBY.get("where") or "") or {})
                    .get("sent") or {},
                    **{k: int(time.time()) for k in send}))
                self.reply_json({"ok": True, "sent": sorted(send)})
            except Exception as e:
                self.reply_json({"ok": False, "why": str(e)[:120]})
            return
        return ()

    def _post_part_18(self, path):
        """POST routes, part 18 of 20:
        /follow/subkey, /skins, /feedback/send, /feedback/sendall, /feedback/fix,
        /library/numbering, /extras/list, /extras/set
        """
        if path == "/follow/subkey":
            # Hand the subtitle key to the machine that keeps copies.
            #
            # It fetches subtitles for the films it takes, and without a key of its
            # own it takes films half the main server cannot read. Sent rather than copied
            # by hand because the address and the invitation are already here; only
            # the owner may, and only to this house's own cache.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            one = self.standby_now()
            key = (S.local().lib.config().get("opensubtitles_key") or "").strip()
            if not one.get("where"):
                self.reply_json({"ok": False, "why": "nothing follows this server"})
                return
            if not key:
                self.reply_json({"ok": False, "why": "no key to send"})
                return
            token = ""
            for row in S.INVITES.load():
                if row.get("follows") and row.get("token"):
                    token = row["token"]
                    break
            if not token:
                self.reply_json({"ok": False, "why": "that machine has no key here"})
                return
            try:
                body = json.dumps({
                    "opensubtitles_key": key,
                    # the login too: a key alone can search and cannot download
                    "opensubtitles_user": (S.local().lib.config()
                                           .get("opensubtitles_user") or "").strip(),
                    "opensubtitles_pass": (S.local().lib.config()
                                           .get("opensubtitles_pass") or "").strip(),
                }).encode()
                req = urllib.request.Request(
                    one["where"].rstrip("/") + "/library/config?t=" +
                    urllib.parse.quote(token),
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as answer:
                    answer.read()
                self.reply_json({"ok": True})
            except Exception as e:
                self.reply_json({"ok": False, "why": str(e)[:120]})
            return
        if path == "/skins":
            # a look is this viewer's own business, guest or owner
            import pd_skins
            body = self.read_json() or {}
            stored = S.read_settings() or {}
            mine = self.viewer_settings(stored)
            mine["skin"] = pd_skins.known(body.get("skin"))
            S.write_settings(stored, merge=False)
            self.reply_json({"chosen": mine["skin"]})
            return
        if path == "/feedback/send":
            # One report, handed over by hand. This is how a fault reaches
            # palladium.video on a machine that sends nothing of its own accord: the
            # owner reads it, decides, and presses send - which is a different act
            # from a machine reporting on itself, and is allowed either way.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            ident = str((self.read_json() or {}).get("id") or "")
            row = next((r for r in self.reports() if r.get("id") == ident), None)
            if not row:
                self.reply_json({"ok": False, "why": "no such report"}, 404)
                return
            sent, why = self.tell_the_site(row, forced=True)
            self.reply_json({"ok": bool(sent),
                             "why": "" if sent else (why or "could not be sent")})
            return
        if path == "/feedback/sendall":
            # The backlog. A machine told to send faults that had no way out keeps
            # them, and they are worth nothing sitting here. Oldest first, one a
            # second, and each row marked with what came of it.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            waiting = [r for r in self.reports()
                       if (r.get("source") == "auto"
                           or r.get("kind") in ("error", "crash"))
                       and not r.get("sentAway") and not r.get("hidden")]
            waiting.sort(key=lambda r: r.get("when") or 0)
            if S.SENDING["busy"]:
                self.reply_json({"ok": True, "already": True, "left": S.SENDING["left"]})
                return
            if not waiting:
                self.reply_json({"ok": True, "queued": 0})
                return
            S.SENDING.update(busy=True, left=len(waiting), sent=0, failed=0, why="")

            def hand_over(rows, me):
                try:
                    for one in rows:
                        ok, why = me.tell_the_site(one, forced=True)
                        S.SENDING["left"] = max(0, S.SENDING["left"] - 1)
                        if ok:
                            S.SENDING["sent"] += 1
                        else:
                            S.SENDING["failed"] += 1
                            S.SENDING["why"] = str(why or "")[:160]
                        time.sleep(1.0)
                finally:
                    S.SENDING["busy"] = False

            threading.Thread(target=hand_over, args=(waiting, self),
                             daemon=True).start()
            self.reply_json({"ok": True, "queued": len(waiting)})
            return
        if path == "/feedback/fix":
            # ticking one off, and the two sentences worth keeping: what caused it and
            # what was done. The owner writes these - it is a statement about the
            # machinery, not about how annoying the fault was.
            body = self.read_json()
            changes = {}
            if "done" in body:
                changes["done"] = bool(body["done"])
                changes["doneWhen"] = int(time.time()) if body["done"] else 0
            for field in ("reason", "solution"):
                if field in body:
                    changes[field] = (body.get(field) or "").strip()[:500]
            # and the wording itself, which the owner may tidy: a request typed on a
            # television remote arrives misspelt, and one typed on a phone that lost
            # its accents arrives unreadable. What was meant is worth keeping legibly.
            if "text" in body:
                changes["text"] = (body.get("text") or "").strip()[:2000]
            self.amend_report(body.get("id") or "", changes)
            self.reply_json({"reports": self.reports()})
            return
        if path == "/library/numbering":
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            # twelve hex digits, letters and all. Keeping only the digits named a
            # different title or none at all, so choosing how a season is numbered
            # quietly did nothing - the same fault this page's rematch already carries
            # a note about.
            key = re.sub(r"[^0-9a-f]", "", str(body.get("key") or "").lower())
            season = re.sub(r"[^0-9]", "", str(body.get("season") or ""))
            mode = str(body.get("mode") or "auto")
            if not key or not season or mode not in ("auto", "files", "database", "doubles", "official",
                                                     "titles"):
                self.reply_json({"error": "which season, and numbered how?"}, 400)
                return
            # a copy numbers each season as its main server does, taken every round
            if self.mirror_of() and not self.headers.get("X-Palladium-House"):
                self.reply_json({"error": "Set on %s: this server mirrors how its "
                                          "seasons are numbered" % self.mirror_of()}, 409)
                return
            stored = self.settings_file()
            chosen = stored.get("numbering") or {}
            if mode == "auto":
                chosen.pop("%s-s%s" % (key, season), None)
            else:
                chosen["%s-s%s" % (key, season)] = mode
            stored["numbering"] = chosen
            S.write_settings(stored, merge=False)
            S.local().lib.last_moves = {}
            moved = S.local().lib.renumber(str(key), int(season), mode)
            # what is kept outside the library under an episode's key - the subtitle
            # picked, verified and its timing - follows the file to its new episode
            keys = dict(getattr(S.local().lib, "last_moves", {}) or {})
            if keys:
                S.carry_the_keys({"episodes": keys})
                S.carry_shifts(keys)
            self.reply_json({"moved": moved, "mode": mode})
            return
        if path in ("/extras/list", "/extras/set"):
            # the files that belong to a title rather than being one, and moving one
            # by hand: to a title, to no title ("?"), or out of the extras ("")
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            con = S.local().lib.db()
            try:
                if path == "/extras/set":
                    key = re.sub(r"[^0-9a-f]", "", str(body.get("key") or "").lower())
                    parent = str(body.get("parent") or "")
                    if parent not in ("", "?"):
                        parent = re.sub(r"[^0-9a-f]", "", parent.lower())
                    if not key or key == parent:
                        self.reply_json({"error": "which file, and whose extra?"}, 400)
                        return
                    con.execute("INSERT OR REPLACE INTO extra (item_id, parent, how) "
                                "VALUES (?, ?, 'hand')", (key, parent))
                    con.commit()
                rows = con.execute(
                    "SELECT x.item_id, x.parent, x.how, i.title, p.title AS parent_title, "
                    "p.type AS parent_type, (SELECT path FROM file f WHERE f.item_id = i.id "
                    "LIMIT 1) AS path FROM extra x JOIN item i ON i.id = x.item_id "
                    "LEFT JOIN item p ON p.id = x.parent WHERE x.parent <> '' "
                    "ORDER BY x.parent = '?' DESC, p.sort_title, i.sort_title").fetchall()
            finally:
                con.close()
            self.reply_json({"extras": [
                {"key": r["item_id"], "title": r["title"] or "", "path": r["path"] or "",
                 "parent": r["parent"], "parentTitle": r["parent_title"] or "",
                 "parentType": r["parent_type"] or "", "how": r["how"] or ""}
                for r in rows]})
            return
        return ()

    def _post_part_19(self, path):
        """POST routes, part 19 of 20:
        /library/rematch, /feedback/hide, /feedback/clear, /invites/update, /invites/owner,
        /invites/cache, /invites/revoke, /library/config, /login
        """
        if path == "/library/rematch":
            # what this actually is. Identification takes TMDB's first answer, and
            # where two unrelated things share a title that answer is the popular one
            # rather than the right one - a rescan would only choose it again.
            body = self.read_json() or {}
            # One file of a title that is another film of the same name and year: set
            # apart under its own key, as that entry. Both films share a name, so the
            # whole title cannot be rematched without moving the other film's copies
            # too (owner, 2026-11-14). tmdbId 0 puts it back with its name.
            if body.get("file"):
                lib = S.local().lib
                tmdb = int(re.sub(r"[^0-9]", "", str(body.get("tmdbId") or "")) or 0)
                try:
                    lib.set_apart(body["file"], tmdb)
                    moved = lib.refile(body["file"])
                except Exception as e:
                    self.reply_json({"error": str(e)[:200]}, 500)
                    return
                self.reply_json({"apart": tmdb, "moved": moved})
                return
            # the library's keys are twelve hex digits; keeping only the digits named a
            # different title, or none
            key = re.sub(r"[^0-9a-f]", "", str(body.get("key") or "").lower())
            tmdb = re.sub(r"[^0-9]", "", str(body.get("tmdbId") or ""))
            if not key or not tmdb:
                self.reply_json({"error": "which title, and which entry?"}, 400)
                return
            try:
                title = S.local().lib.rematch(str(key), int(tmdb))
            except Exception as e:
                self.reply_json({"error": str(e)[:200]}, 500)
                return
            self.reply_json({"title": title})
            return
        if path == "/feedback/hide":
            body = self.read_json()
            self.hide_report(body.get("id") or "", bool(body.get("hidden", True)))
            self.reply_json({"reports": self.reports()})
            return
        if path == "/feedback/clear":
            # what is still open stays: the board is for what is still wrong, and
            # clearing it should not be a way to lose that by accident
            everything = bool((self.read_json() or {}).get("everything"))
            moved = self.file_away(lambda row: everything or bool(row.get("done")))
            self.reply_json({"reports": self.reports(), "filed": moved})
            return
        if path == "/invites/update":
            body = self.read_json()
            S.INVITES.set_email(body.get("token") or "",
                              (body.get("email") or "").strip()[:120])
            self.reply_json({"people": [self.with_link(r) for r in S.INVITES.load()]})
            return
        if path == "/invites/owner":
            # Who the person at this machine is. Owning it is still a matter of where
            # a request comes from - this says whose viewing it is, so one person is
            # not a viewer in a browser away from home and the machine itself on
            # their own network.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            rows = S.INVITES.load()
            row = next((r for r in rows if r.get("token") == token), None)
            if token and not row:
                self.reply_json({"error": "No such person."})
                return
            if row and row.get("follows"):
                self.reply_json({"error": "That key belongs to a server, not a "
                                          "person."})
                return
            stored = S.read_settings()
            if stored is None:
                self.reply_json({"error": "The settings could not be read."})
                return
            was = str(stored.get("ownerIs") or "") or "me"
            if token:
                stored["ownerIs"] = token
                stored["ownerName"] = row.get("name") or "you"
            else:
                stored.pop("ownerIs", None)
            S.write_settings(stored, merge=False)
            moved = self.hand_over_history(was, token or "me")
            self.reply_json({"ownerIs": token, "name": stored.get("ownerName", ""),
                             "moved": moved,
                             "people": [self.with_link(r) for r in S.INVITES.load()]})
            return
        if path == "/invites/cache":
            # What a following server should keep for this person: what they are in
            # the middle of, and what they mean to watch. Off by default for
            # everybody but whoever owns the machine.
            body = self.read_json() or {}
            token = str(body.get("token") or "")
            if token == "me":
                stored = S.read_settings() or {}
                for name in ("cacheDeck", "cacheList", "cacheCasual",
                             "cacheLately", "shareLan"):
                    if name in body:
                        stored[name] = bool(body[name])
                # gigabytes a week copied for them, and downloaded by them; nought is
                # no limit
                for name in ("syncGbWeek", "downloadGbWeek"):
                    if name in body:
                        try:
                            stored[name] = max(0.0, float(body[name] or 0))
                        except (TypeError, ValueError):
                            pass
                S.write_settings(stored)
            else:
                rows = S.INVITES.load()
                for row in rows:
                    if row.get("token") == token:
                        # and whether this person is handed the address
                        # this machine answers to on its own network
                        for name in ("cacheDeck", "cacheList", "cacheCasual",
                                     "cacheLately", "shareLan"):
                            if name in body:
                                row[name] = bool(body[name])
                        for name in ("syncGbWeek", "downloadGbWeek"):
                            if name in body:
                                try:
                                    row[name] = max(0.0, float(body[name] or 0))
                                except (TypeError, ValueError):
                                    pass
                S.INVITES.save(rows)
            self.reply_json({"people": [self.with_link(r) for r in S.INVITES.load()],
                             "me": self.owner_caching()})
            return
        if path == "/invites/revoke":
            S.INVITES.revoke(self.read_json().get("token") or "")
            self.reply_json({"people": [self.with_link(r) for r in S.INVITES.load()]})
            return
        if path == "/library/config":
            # The machine's own settings: its folders, its keys, its name. Guests are
            # already turned away at the door by the list of what an invitation may
            # ask for, but this one writes where the films are kept and what this
            # server searches with, and that is worth refusing here as well.
            if self.role != "owner":
                self.send_error(403, "not allowed")
                return
            cfg = S.local().lib.config()
            body = self.read_json()
            changed = None
            for key in ("movies", "tv", "mixed", "tmdb_key", "language",
                        "opensubtitles_key", "opensubtitles_user",
                        "opensubtitles_pass", "scanEvery", "scanOnChange",
                        # nothing, faults, or faults with a description of this
                        # computer: off unless somebody says otherwise
                        "sendFaults",
                        # whether a new fault starts Claude Code here, read-only, and
                        # the source folder it reads when the program is installed
                        "claudeFaults", "claudeSource",
                        # whether this machine may fetch anything from palladium.video
                        "fetchFromSite",
                        # the most a machine following this one may use, in gigabytes
                        "cacheCap",
                        # whether the shuffle's next picks are copied at all
                        "copyCasual",
                        # per cent of a film's blocks read off the machine that keeps
                        # copies while this one hands the film to a screen
                        "shareWithCopy",
                        # films of packs at home on the copy server held here only while
                        # the copy rules want them: fetched back, and let go after
                        "syncBack",
                        # the only folders sync back may remove a film from
                        "syncBackFolders",
                        # which way the two servers copy for each other: both, toCopy
                        # or fromCopy - by the same list and the same copy
                        "syncWay",
                        # which machines seed the packs whose home is split
                        "splitSeed",
                        # the megabits a copy may take while somebody is watching.
                        # Twenty unless changed; nought lets it run flat out.
                        "syncMbitWhileWatching",
                        # what to call this machine, wherever it is named. Empty
                        # means the computer's own name, which is what it was
                        # before anybody thought to ask.
                        "serverName",
                        # where encodes are made: "here", or "connected" - the
                        # computer this one copies from, or the one copying from it
                        "encodeOn"):
                if key in body:
                    if key == "syncWay" and body[key] not in S.SYNC_WAYS:
                        continue
                    if key == "splitSeed" and body[key] not in ("copy", "main", "both"):
                        continue
                    cfg[key] = body[key]
                    if key in ("movies", "tv", "mixed"):
                        changed = key
            cfg.setdefault("mixed", [])
            # a folder in two lists was read twice and typed by whichever came first
            S.local().lib.exclusive(cfg, changed)
            S.local().lib.save_config(cfg)
            # tell the client which of the folders actually exist
            cfg["exists"] = {p: os.path.isdir(p)
                             for p in cfg["movies"] + cfg["tv"] + cfg["mixed"]}
            self.reply_json(cfg)
            return
        if path == "/login":
            # Slow on purpose, and slower still after a wrong one: the password is the
            # only thing between a stranger on the internet and the whole library.
            said = self.read_json() or {}
            here = self.client_address[0]
            waited = S.GUESSES.get(here) or [0, 0.0]
            if waited[0] >= 8 and time.time() - waited[1] < 300:
                self.reply_json({"ok": False,
                                 "error": "Too many tries. Wait five minutes."}, 429)
                return
            if not self.password_set():
                self.reply_json({"ok": False, "error": "No password is set."}, 400)
                return
            if not self.password_ok(said.get("password") or ""):
                S.GUESSES[here] = [waited[0] + 1, time.time()]
                time.sleep(1.0)
                self.reply_json({"ok": False, "error": "That is not the password."},
                                403)
                return
            S.GUESSES.pop(here, None)
            token = self.open_session()
            body = json.dumps({"ok": True, "token": token}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", self.cookie_for(token))
            self.end_headers()
            self.wfile.write(body)
            return
        return ()

    def _post_part_20(self, path):
        """POST routes, part 20 of 20:
        /logout, /password, /setup/ffmpeg, /setup/done, /library/scan, /library/reparse,
        /library/browse, /trace, /applog, /log
        """
        if path == "/logout":
            token = self.bearer()
            stored = S.read_settings() or {}
            live = stored.get("ownerSessions") or {}
            if token in live:
                live.pop(token, None)
                stored["ownerSessions"] = live
                S.write_settings(stored)
            self.reply_json({"ok": True})
            return

        if path == "/password":
            # Setting it, or clearing it, is the owner's own business: this route is
            # inside do_POST, which only an owner reaches at all.
            said = self.read_json() or {}
            if self.password_set() and not self.password_ok(said.get("old") or ""):
                # once one exists, changing it means knowing it - otherwise anybody
                # who is already through the door can lock everyone else out
                self.reply_json({"ok": False, "error": "The old password is wrong."},
                                403)
                return
            on = self.set_password(said.get("password") or "")
            self.reply_json({"ok": True, "password": on})
            return

        if path == "/setup/ffmpeg":
            # Fetching, letting go of, or taking up again the cache on this disk.
            # Letting go is not deleting: the files stay where they are and it is
            # taken up again without asking anybody for anything.
            body = self.read_json() or {}
            if body.get("off"):
                S.use_ffmpeg("")
                self.reply_json({"using": "", "have": S.ffmpeg_in_home()})
                return
            if body.get("on"):
                found = S.ffmpeg_in_home()
                self.reply_json({"using": S.use_ffmpeg(found) if found else "",
                                 "have": found})
                return
            # a fetch over a copy that is already here is a question, not a default
            have = S.ffmpeg_in_home()
            if have and not body.get("overwrite"):
                self.reply_json({"ask": True, "have": have,
                                 "where": S.FFMPEG_FROM.get(os.name) or ""})
                return
            # 80 MB from the internet, so only ever because somebody pressed for it
            if not S.FETCHING["busy"]:
                threading.Thread(target=S.fetch_ffmpeg, daemon=True).start()
            self.reply_json({"started": True, "fetching": dict(S.FETCHING)})
            return
        if path == "/setup/done":
            stored = S.read_settings() or {}
            stored["setupDone"] = True
            S.write_settings(stored)
            # A server that does not come back after a restart reads as broken, and
            # the first restart of a new machine is usually the same evening. So it
            # is arranged when the setting up is finished rather than asked for in a
            # wizard - the switch is in Settings, This computer, for whoever wants it
            # off. Only the first time: somebody who turned it off meant it.
            try:
                import pd_machine
                if not stored.get("askedStartup") and not pd_machine.starts_at_login():
                    pd_machine.set_start_at_login(True)
                stored["askedStartup"] = True
                S.write_settings(stored)
            except Exception:
                pass
            self.reply_json({"ok": True})
            return
        if path == "/library/scan":
            body = self.read_json()
            lib = S.local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"error": "a scan is already running", **lib.stats()}, 409)
                return
            threading.Thread(
                target=lambda: lib.scan(probe=body.get("probe", True),
                                        identify=body.get("identify", True)),
                daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/library/reparse":
            # Read every filename again with today's rules. The files have not
            # changed, so a scan skips them, and a title the old parser got wrong
            # stays wrong until this is run.
            lib = S.local().lib
            if lib.scan_state.get("running"):
                self.reply_json({"error": "a scan is already running"}, 409)
                return
            threading.Thread(target=lib.reparse, daemon=True).start()
            self.reply_json({"started": True})
            return
        if path == "/library/browse":
            # a small directory picker: list the folders inside a path, and the files
            # in it that end a given way - a torrent is chosen the same way a folder
            # is, because a browser's own file box hands back a sandboxed name rather
            # than a path this machine could open.
            body = self.read_json() or {}
            here = (body.get("path") or "").strip()
            ending = str(body.get("files") or "")[:12].lower()
            try:
                if not here:
                    if os.name == "nt":
                        import string
                        roots = ["%s:%s" % (d, os.sep) for d in string.ascii_uppercase
                                 if os.path.isdir("%s:%s" % (d, os.sep))]
                    else:
                        # where films live on a machine without drive letters, and
                        # the mount points a container is given them through
                        roots = [p for p in ("/media", "/mnt", "/srv", "/data",
                                             os.path.expanduser("~"), "/")
                                 if os.path.isdir(p)]
                    self.reply_json({"path": "", "parent": None, "dirs": roots})
                    return
                inside = os.listdir(here)
                entries = sorted([os.path.join(here, d) for d in inside
                                  if os.path.isdir(os.path.join(here, d))
                                  and not d.startswith(("$", "."))])
                files = sorted([os.path.join(here, d) for d in inside
                                if ending and d.lower().endswith(ending)
                                and os.path.isfile(os.path.join(here, d))]) if ending else []
                parent = os.path.dirname(here.rstrip(os.sep + "/")) or ""
                self.reply_json({"path": here, "parent": parent, "dirs": entries,
                                 "files": files})
            except Exception as e:
                self.reply_json({"error": str(e)[:120], "path": here, "dirs": []}, 400)
            return
        if path == "/trace":
            # a line from a client about what it is actually doing - what the player
            # holds, what it was given, what it makes of it. Straight into the log:
            # this is for reading here, not for the noticeboard.
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace").strip()
            pd_log.raw("%s trace %s: %s\n" % (time.strftime("%H:%M:%S"),
                                            self.client_address[0], body[:1500]))
            self.reply_json({"noted": True})
            return
        if path == "/applog":
            # the Android app posts its crashes here: no store, no cable, no other way
            # to learn why it died on the sofa
            n = int(self.headers.get("Content-Length") or 0)
            # a stack trace is a few kilobytes; anything longer is not one, and the
            # door lets this through without a key
            body = self.rfile.read(min(n, 65536)).decode("utf-8", "replace")
            with open(os.path.join(S.ROOT, "app-crash.log"), "a", encoding="utf-8") as f:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write("---- " + stamp + " from " + self.client_address[0]
                        + " ----\n" + body + "\n")
            # and into the reports, where somebody will actually see it: the whole
            # trace stays in the log, the first lines are what identifies it
            head = [ln for ln in body.splitlines() if ln.strip()][:8]
            # the device says what it is in the first lines; the server knows who sent
            # it, which the device cannot say for itself
            self.file_report("crash", "\n".join(head), "android")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path != "/log":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(n, 32768)).decode("utf-8", "replace")
        if self.role == "guest":
            # whose browser this was: a trail is worth little if it cannot be
            # told from everybody else's, and a guest cannot flood the file
            who = self.guest_name or "guest"
            body = "\n".join(
                (who + ": " + ln) for ln in body.splitlines()[:120])
        body = self.not_again(body)
        if body:
            pd_log.raw(body.rstrip("\n") + "\n")
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()
