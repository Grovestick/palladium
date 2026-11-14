"""Who is asking: the owner at home or away, guests and their keys, passwords and sessions, wrong codes and knocks, and what each role may do.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
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


class GuardParts(_Base):
    def may_have_the_lan(self):
        """Whether this asker is told the address on this network.

        The owner always. A guest only if their invitation says so: the address of a
        machine inside somebody's house is not a secret worth guarding hard, but it
        is theirs, and handing it to everyone who ever received a link is a decision
        rather than an accident. A guest without it still has the way in from
        outside, which is the one that works from where they are.
        """
        if self.role == "owner":
            return True
        invite = S.INVITES.check(self.bearer(), self.app_name())
        if not invite:
            return False
        if invite.get("follows"):
            return True                    # the main server's own second machine
        return bool(invite.get("shareLan", True))

    @classmethod
    def in_house(cls):
        """A request as the server's own background work makes it: the owner, on this
        machine. A bare one had no address, and standby_now asked for it - moving a pack
        to its home failed on every try (2026-11-02)."""
        house = cls.__new__(cls)
        house.role = "owner"
        house.client_address = ("127.0.0.1", 0)
        return house

    #: wrong setup codes lately, by address. Five characters is enough only because
    #: they cannot be tried in bulk.
    GUESSES = {}

    def too_many_guesses(self):
        now = time.time()
        tries = [t for t in S.Handler.GUESSES.get(self.client_address[0], [])
                 if now - t < 3600]
        S.Handler.GUESSES[self.client_address[0]] = tries
        return len(tries) >= 20

    def wrong_guess(self):
        S.Handler.GUESSES.setdefault(self.client_address[0], []).append(time.time())

    #: wrong codes from every address in the last hour, and when codes were shut for
    #: everybody: 20 an hour per address does nothing against many addresses at once
    CODE_MISSES = []
    CODES_SHUT = [0.0]
    #: the most wrong codes an hour across all addresses before codes stop working
    CODES_MOST = 100
    #: when each kind of warning was last filed, so a run of guesses is one row an hour
    WARNED = {}

    def codes_shut(self):
        """Whether this address, or everybody at once, has guessed too often."""
        if time.time() - S.Handler.CODES_SHUT[0] < 3600:
            return True
        return self.too_many_guesses()

    def wrong_code(self, path):
        """One wrong five-character code: counted, logged, and warned about."""
        now = time.time()
        self.wrong_guess()
        S.Handler.CODE_MISSES[:] = [t for t in S.Handler.CODE_MISSES if now - t < 3600]
        S.Handler.CODE_MISSES.append(now)
        self.refused(path, "wrong code")
        ip = self.client_address[0]
        mine = len([t for t in S.Handler.GUESSES.get(ip, []) if now - t < 3600])
        everyone = len(S.Handler.CODE_MISSES)
        if everyone >= S.Handler.CODES_MOST:
            S.Handler.CODES_SHUT[0] = now
        # a typo on a television is one or two; five from one address is somebody trying
        if mine >= 5:
            self.warn_once("guess:" + ip,
                           "Someone is guessing invite codes: %d wrong in the last hour "
                           "from %s. That address is stopped after 20." % (mine, ip))
        if everyone >= 25:
            self.warn_once("guess:all",
                           "Invite codes are being guessed from several addresses: %d "
                           "wrong in the last hour. At %d every code stops working for "
                           "an hour." % (everyone, S.Handler.CODES_MOST))
        if everyone >= S.Handler.CODES_MOST:
            self.warn_once("guess:shut",
                           "Invite codes are shut for an hour: %d wrong guesses in the "
                           "last hour. Links with the full key still work."
                           % everyone)

    def warn_once(self, kind, text):
        """A security warning on the reports page, once an hour per kind, owner only."""
        now = time.time()
        if now - S.Handler.WARNED.get(kind, 0) < 3600:
            return
        S.Handler.WARNED[kind] = now
        try:
            with open(os.path.join(S.ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "id": "%x" % (int(now * 1000) % 0xFFFFFFFFFF), "when": int(now),
                    "who": "server", "kind": "error", "text": text,
                    "from": self.client_address[0], "app": "security",
                    "source": "auto", "owner": True}) + chr(10))
        except Exception:
            pass

    def knocks(self, hours=24):
        """Refused requests from outside the house, by address, from the log's tail."""
        out = {}
        since = time.time() - hours * 3600
        try:
            path = os.path.join(S.ROOT, "debug.log")
            with open(path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 8 * 1024 * 1024))
                tail = f.read().decode("utf-8", "replace").splitlines()
        except Exception:
            return []
        # the log carries a clock, not a date: walked backwards, a clock that goes up
        # means the line before it was yesterday
        day = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        last = 24 * 3600
        for ln in reversed(tail):
            m = re.match(r"^(\d\d):(\d\d):(\d\d) refused (\S+) to ([0-9a-fA-F.:]+) \((.*?)\)",
                         ln)
            if not m:
                continue
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if secs > last:
                day -= 86400
            last = secs
            when = day + secs
            if when < since:
                break
            ip = m.group(5)
            # the house itself, reached through its own router, is not a stranger
            if (ip.startswith(("192.168.", "10.", "127.")) or ip == "::1"
                    or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip) or ip == S.WAN["ip"]):
                continue
            why = m.group(6)
            # a known guest's own key that went stale is not a stranger knocking
            if not (why.startswith("no token") or why.startswith("unknown token")
                    or "wrong code" in why):
                continue
            row = out.setdefault(ip, {"address": ip, "count": 0, "first": when,
                                      "last": when, "paths": {}, "codes": 0})
            row["count"] += 1
            row["first"] = min(row["first"], when)
            row["last"] = max(row["last"], when)
            row["paths"][m.group(4)[:60]] = row["paths"].get(m.group(4)[:60], 0) + 1
            if "wrong code" in why:
                row["codes"] += 1
        rows = sorted(out.values(), key=lambda r: (-r["codes"], -r["count"]))
        for r in rows:
            r["paths"] = sorted(r["paths"].items(), key=lambda p: -p[1])[:6]
        return rows

    def squeezed(self, body, ctype):
        """The body gzipped when the asker takes it and it is worth it, with the header
        said; call between send_response and Content-Length. The film list is 5.8 MB of
        JSON, which is seconds on mobile data and a tenth of that compressed."""
        if (len(body) < 1400 or "gzip" not in (self.headers.get("Accept-Encoding") or "")
                or not (ctype.startswith("application/json") or ctype.startswith("text/"))):
            return body
        import gzip
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        return gzip.compress(body, compresslevel=5)

    @staticmethod
    def side_of(host):
        """"home" for an address on this network, "away" for one out in the world.

        The two do not share a pipe: a film going out to somebody's phone is on the
        line to the world, and a copy to a machine in the next room is on the network
        here. Counting them together held a copy between two machines three metres
        apart to what was left of an upload.
        """
        host = str(host or "")
        if host in ("127.0.0.1", "::1"):
            return "home"
        parts = host.split(".")
        if len(parts) == 4 and parts[0].isdigit():
            a, b = int(parts[0]), int(parts[1])
            # 169 alone was the whole of 169.0.0.0/8, which is mostly ordinary
            # public address space - only 169.254 is link-local. Any visitor whose
            # address began 169 was therefore taken for someone in the house and
            # given the owner's rights, from anywhere.
            if (a == 10 or (a == 192 and b == 168)
                    or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)):
                return "home"
        return "away"

    def at_home(self):
        """True for a caller on this machine or this home network."""
        return self.side_of(self.client_address[0]) == "home"

    #: what the owner's key may read from away with Remote admin off: Now playing,
    #: the drawing of the house and the logs. Nothing here changes anything.
    MONITOR_PATHS = {"/watching", "/wiring", "/traffic", "/machine", "/copied",
                     "/watchlog", "/follow", "/follow/queue", "/gpu/status",
                     "/setup/state", "/server", "/torrents/log", "/security/knocks",
                     "/standby"}
    #: set on a request carrying the owner's key from away
    owner_away = False

    #: what an owner's key may do from away, group by group, when the run of the place
    #: does not travel: each is turned on under Running this server from away
    REMOTE_GROUPS = {
        "invites": ("/invites",),
        "downloads": ("/torrents", "/tracker", "/downloads", "/shows/add",
                      "/shows/addpack", "/requests"),
        "library": ("/library", "/extras", "/receiver", "/notice", "/party",
                    "/feedback"),
        "settings": ("/settings", "/machine", "/proxy", "/security"),
        "copies": ("/follow",),
        "updates": ("/update", "/app/fetch"),
    }

    def remote_owner(self):
        """Which groups an owner's key opens from away: every one when the run of the
        place travels, else those turned on one by one."""
        stored = self.settings_file()
        if stored.get("remoteAdmin") is not False:
            return {g: True for g in S.Handler.REMOTE_GROUPS}
        said = stored.get("remoteOwner") or {}
        return {g: bool(said.get(g)) for g in S.Handler.REMOTE_GROUPS}

    def remote_owner_allows(self, path):
        """Whether this path belongs to a group the owner may use from away."""
        p = str(path).split("?", 1)[0]
        groups = self.remote_owner()
        return any(groups.get(g) and any(p == x or p.startswith(x + "/") for x in pre)
                   for g, pre in S.Handler.REMOTE_GROUPS.items())

    def remote_admin_ok(self):
        """Whether the run of the place travels beyond this network.

        Off, an owner's key is a guest's key from away: films yes, settings no. The
        machine itself and this network are never affected - shutting somebody out of
        their own server from the sofa is not a security setting, it is a fault. On
        until somebody says otherwise, which is how it has always behaved.
        """
        return self.settings_file().get("remoteAdmin") is not False

    @staticmethod
    def hash_password(word, salt=None):
        """A password, as something that cannot be turned back into the password."""
        import binascii
        import hashlib
        salt = salt or binascii.hexlify(os.urandom(16)).decode()
        made = hashlib.pbkdf2_hmac("sha256", word.encode("utf-8"),
                                   salt.encode("utf-8"), 200000)
        return salt, binascii.hexlify(made).decode()

    @staticmethod
    def password_set():
        return bool((S.read_settings() or {}).get("ownerHash"))

    @staticmethod
    def untouched():
        """A server nobody has set up yet: no password, no invitations, no folders.

        There is nothing here to protect at that point - no library, no settings, no
        keys - and whoever reaches it is the person who has just installed it.
        """
        try:
            stored = S.read_settings() or {}
            if stored.get("setupDone") or stored.get("ownerHash"):
                return False
            if S.INVITES.load():
                return False
            lib = S.local().lib.config() or {}
            return not (lib.get("movies") or lib.get("tv") or lib.get("mixed"))
        except Exception:
            return False

    def password_ok(self, word):
        import hmac
        stored = S.read_settings() or {}
        salt, want = stored.get("ownerSalt") or "", stored.get("ownerHash") or ""
        if not want:
            return False
        _, got = self.hash_password(word or "", salt)
        return hmac.compare_digest(got, want)

    def set_password(self, word):
        """Set it, or clear it with an empty one. Every session ends either way."""
        stored = S.read_settings() or {}
        if not (word or "").strip():
            # emptied rather than removed: settings are written by merging keys, so a
            # key that is missing from what is written keeps whatever was there before
            # - which meant a password could be set and never taken off again
            stored["ownerHash"], stored["ownerSalt"] = "", ""
        else:
            salt, made = self.hash_password(word.strip())
            stored["ownerSalt"], stored["ownerHash"] = salt, made
        stored["ownerSessions"] = {}
        S.write_settings(stored)
        return self.password_set()

    def open_session(self):
        """A token that says this caller has given the password."""
        import binascii
        token = binascii.hexlify(os.urandom(24)).decode()
        stored = S.read_settings() or {}
        live = {t: when for t, when in (stored.get("ownerSessions") or {}).items()
                if when > time.time()}
        live[token] = time.time() + 180 * 24 * 3600
        stored["ownerSessions"] = live
        S.write_settings(stored)
        return token

    def session_ok(self):
        token = self.bearer()
        if not token:
            return False
        when = (S.read_settings() or {}).get("ownerSessions", {}).get(token)
        return bool(when and when > time.time())

    def through_a_proxy(self):
        """Did this request come by way of something else?

        A proxy makes every caller look local. Two headers say so, and either one is
        enough to stop the address being taken as proof of anything.
        """
        return bool(self.headers.get("X-Forwarded-For")
                    or self.headers.get("Forwarded")
                    or self.headers.get("X-Real-IP"))

    @staticmethod
    def cookie_for(token):
        return "pal=%s; Path=/; Max-Age=%d; SameSite=Lax" % (token, S.COOKIE_LIFE)

    def device_kind(self):
        """What kind of thing is watching: an app on a television, a browser, a phone.

        The client says its own name - "Streamer", "SM-S931B" - which says nothing
        about what it is. This is worked out from what it says it is and how it asks.
        """
        said = (self.headers.get("X-Palladium-App") or "").lower()
        if said.startswith("android"):
            return "Google TV app" if said.endswith(" tv") else "Android app"
        agent = (self.headers.get("User-Agent") or "")
        low = agent.lower()
        if "crkey" in low:
            return "Chromecast"
        if "web0s" in low or "webos" in low:
            return "LG TV browser"
        if "tizen" in low or "smart-tv" in low or "smarttv" in low:
            return "Samsung TV browser"
        if "iphone" in low:
            return "iPhone"
        if "ipad" in low:
            return "iPad"
        if "android" in low:
            return "Android browser"
        if "macintosh" in low or "mac os" in low:
            return "Mac browser"
        if "windows" in low:
            return "Windows browser"
        if "mozilla" in low:
            return "browser"
        return ""

    def app_name(self):
        """What the client calls itself: "android 0.11.4 tv", or the browser.

        The app says so in a header. A browser says nothing, and does not need to -
        it is whatever the page it loaded is, which the server knows already.
        """
        said = (self.headers.get("X-Palladium-App") or "").strip()[:60]
        if said:
            return said
        agent = (self.headers.get("User-Agent") or "").lower()
        if "mozilla" in agent:
            return "web"
        return ""

    def bearer(self):
        """An invite token from the URL, a header, or the cookie the landing page set.

        Three places because three kinds of caller: the Android app sends a header,
        a shared link carries the token in the query, and once a browser has been to
        /s/<token> every later request for a poster or a video segment carries only
        the cookie.
        """
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        if q.get("t"):
            return q["t"][0]
        head = self.headers.get("X-Palladium-Token")
        if head:
            return head
        for bit in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = bit.strip().partition("=")
            if k == "pal":
                return v
        return ""

    #: What a *receiver* calls itself. CrKey is the Cast receiver on every Chromecast
    #: and Google TV; the rest are televisions casting natively.
    RECEIVERS = ("crkey", "cast_sender", "appletv", "airplay", "smarttv", "tizen",
                 "web0s", "aftv")

    #: And what our own app calls itself. A Google TV Streamer's build string contains
    #: "Chromecast", so the app running on one looked exactly like a receiver - every
    #: ordinary play from that device was logged as a cast asking for the pipe, which
    #: sent me looking for a fault that was not there.
    OURSELVES = ("dalvik", "okhttp", "palladium")

    def receiver(self):
        """The name a television gives itself, or empty for anything else."""
        ua = (self.headers.get("User-Agent") or "").lower()
        if any(mark in ua for mark in self.OURSELVES):
            return ""
        for mark in self.RECEIVERS:
            if mark in ua:
                return (self.headers.get("User-Agent") or "")[:70]
        return ""

    def note_cast(self, what, extra=""):
        """Write down a television asking for something, with what it asked for.

        Only televisions: a browser fetching the same playlist is ordinary traffic and
        would drown this. What is wanted here is the answer to "did the cast even
        reach us", which was previously unanswerable.
        """
        who = self.receiver()
        if not who:
            return
        pd_log.raw("%s cast %s from %s%s [%s]%s"
                % (time.strftime("%H:%M:%S"), what, self.client_address[0],
                   (" " + extra) if extra else "", who, chr(10)))

    def refused(self, path, why):
        """Note a refusal: what was asked for, by whom, and what they carried."""
        token = self.bearer()
        known = S.INVITES.check(token) if token else None
        who = self.receiver()
        pd_log.raw("%s refused %s to %s (%s)%s%s"
                % (time.strftime("%H:%M:%S"), path, self.client_address[0],
                   ("no token" if not token else
                    ("unknown token " + token[:8]) if not known else
                    known["name"] + ": " + why),
                   # which matters most for a television: it cannot show an error,
                   # it just sits there
                   (" [%s]" % who) if who else "",
                   chr(10)))

    #: What a key is for. "user" watches; "owner" may change the library as though
    #: sitting at the machine; "cache" is another server keeping copies of this one.
    #: Kept on the key itself, so one list of keys answers "who has a way in here,
    #: and what may they do".
    ROLES = ("user", "owner", "admin", "cache")

    @staticmethod
    def role_of(row):
        """The role on a key, or the one its old flags imply."""
        role = str((row or {}).get("role") or "")
        if role in S.Handler.ROLES:
            return role
        if (row or {}).get("follows"):
            return "cache"
        return "user"

    def who(self, path):
        """'owner', 'guest' or None - the answer every request starts with."""
        # decided afresh each request: one connection carries many
        self.owner_away = False
        if self.headers.get("X-Palladium-House"):
            # the server this machine follows, setting how it copies: only while
            # somebody here allows it, from that server's address, and its copying only
            return ("owner" if self.managed_from_the_house()
                    and str(path).startswith("/follow") else None)
        # The machine itself needs no key: somebody sitting at it can read the files
        # anyway. Every other caller carries one - being on the home network is not a
        # credential, and a television in the kitchen is as much a stranger as a phone
        # on the train until it holds an invitation.
        here = (self.client_address[0] in ("127.0.0.1", "::1")
                and not self.through_a_proxy())
        # In a container there is no sitting at the machine: every request arrives off
        # the network, including the first one. So a server in one could not be set up
        # at all - no password to give, no invitation to hold, and the only address
        # that counted was inside the container. Until somebody has set it up, being
        # on the same network is enough; finishing the wizard is what closes it.
        if not here and self.untouched() and self.in_the_house():
            here = True
        if self.password_set():
            # A session is the password given once and remembered, so it opens the
            # place from wherever it was given - which is the thing the setting is
            # about. Sitting at the machine is not affected by it.
            if here or (self.session_ok() and (self.at_home()
                                               or self.remote_admin_ok())):
                return "owner"
        elif here:
            return "owner"
        if self.managed_from_the_house():
            # This machine keeps copies for another and has been told that machine
            # may change its settings. Only that machine, only by its address, and
            # only because somebody sitting here turned it on.
            return "owner"
        invite = S.INVITES.check(self.bearer(), self.app_name())
        if invite and S.Handler.role_of(invite) == "owner":
            # a key that carries the run of the place, for somebody who has it and is
            # not sitting at the machine
            self.guest_name = invite["name"]
            if self.at_home() or self.remote_admin_ok():
                return "owner"
            # From away with the setting off, the owner may still watch the house:
            # Now playing and the logs, read and never changed
            self.owner_away = True
            if self.command in ("GET", "HEAD") and (
                    str(path) in S.Handler.MONITOR_PATHS
                    or str(path).startswith("/follow/log")):
                return "owner"
            # and whatever groups have been let out one by one
            if self.remote_owner_allows(path):
                return "owner"
            # Anything else: the same key, read as a guest's. It falls through to the
            # guest handling below rather than being refused, so what it is mostly
            # used for - watching - goes on working.
            self.guest_name = invite["name"]
        # A machine that keeps copies is not a guest and never was: it was owner here
        # only because it sat on the same network, and requiring a key of every screen
        # took that away - so its own rounds were refused at the door, and caching
        # stopped. The handlers behind /follow check the key themselves, that it
        # follows this server and that it has not been stopped; the door defers to
        # them rather than deciding it twice.
        # and the build this machine is running: a machine that follows this one
        # replaces itself with what this one has, which means fetching the installer.
        # Being on the same network stopped being a credential, and its key was good
        # for /follow alone - so following the build broke at the door.
        low = str(path).lower()
        build_file = low.startswith("/palladium-setup") and low.endswith(".exe")
        # and who is watching, which is the one thing the pair cannot work out
        # separately: a film read off both machines is two streams, and each machine
        # can see only its own. Neither draws the pair honestly without asking the
        # other, and this is a list of what is going out of a house to itself.
        # /server/build as well as /server: the first is what a follower asks for
        # the version and hash, and it was refused at the door while the handler
        # behind it was waiting to check the same key itself - so a machine that
        # follows this one could never take its build, fell back to the site, and
        # refused to go backwards to what the site was still announcing.
        if (invite and S.Handler.role_of(invite) == "cache"
                and (str(path).startswith("/follow")
                     or path == "/server" or path == "/server/build"
                     or path == "/watching" or build_file
                     # and the viewing figures, which count both machines here
                     or path == "/stats"
                     # and the posters of what it lists without holding
                     or str(path).startswith("/local/art/"))):
            self.guest_name = invite["name"]
            return "owner"
        if invite and S.Handler.role_of(invite) == "admin":
            # A panel on the wall rather than a person: it reads whatever the owner
            # reads - who is watching, who holds a key, what is arriving - and writes
            # nothing. Asking is a GET; everything that changes anything is refused,
            # because only the owner's own key answers "owner" to those.
            self.guest_name = invite["name"]
            if self.command in ("GET", "HEAD"):
                return "owner"
            return "guest" if S.Invites.allowed(path) else None
        if invite and S.Invites.allowed(path):
            self.guest_name = invite["name"]
            return "guest"
        # the app and its install page: no invitation needed to hold a client that
        # cannot see anything until it is given one
        if S.Invites.public(path):
            return "guest"
        # A screen saying why it died. No key: a crash report is posted by a process
        # on its way out, from a handler that runs before anything is loaded and after
        # everything is lost. Only from this network, and only a write - a stranger on
        # the internet cannot fill the log from outside.
        if path == "/applog" and self.command == "POST" and self.in_the_house():
            return "guest"
        return None
