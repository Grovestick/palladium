#!/usr/bin/env python3
"""Sharing Palladium with someone outside the main server.

A guest gets one link carrying a token. That token is the whole key, so the rules are
deliberately narrow:

  * it opens the library, the artwork, the streams and the app download - nothing else;
  * it never sees config.json, nor the diagnostics, nor
    the library settings, nor anything that could change what the server indexes;
  * it can be named, so you know who you gave it to, and revoked on its own;
  * it can expire.

Everything is stored in invites.json beside the database. Tokens are compared with
compare_digest so a wrong guess leaks nothing through timing.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

# What anybody at all may reach, invitation or not. The app is a client with no
# credentials in it: until somebody is given a token it shows an empty screen, so
# refusing the download only stops people who are already welcome - typically on a
# television, where a link cannot be typed and a remote is the only keyboard.
PUBLIC_PREFIXES = (
    "/health",          # answering or not, and nothing more than that
    "/app/version",     # what the newest version is
    "/palladium.apk",   # the file itself
    "/i/",              # the same file by a short code, and the code exchange
    "/invite.png",      # the picture a chat app shows in its preview card
    "/favicon.ico", "/palladium.ico",
    "/icon-",           # the home-screen icon, in its several sizes
    "/manifest.webmanifest",   # written per caller: it repeats their own invitation
)

# What a guest token may reach. Everything else is refused from off-network.
GUEST_PREFIXES = (
    "/local/",          # browse the library and its artwork
    "/gpu/stream",      # play
    "/gpu/begins",      # where that play will start, asked first
    "/torrents/get",    # one film from a torrent pack, within their week's limit
    "/torrents/active", # how far their own download has got: the handler already
                        # answers a guest with their own rows and nobody else's
    # Stopping one they asked for. cancel() already decides who may: the owner, or
    # whoever asked for it. It was on the list of what a guest may write and not on
    # this one, so every press was refused at the door - the same way round as the
    # player reports above, and it means somebody can start a download and not stop it.
    "/torrents/cancel",
    "/copy/pick",       # which of two machines to read a film from, the screen's own choice
    "/gpu/hls",         # the same, in segments, for Safari
    "/gpu/subs",
    "/app",             # the install page and the version check
    "/palladium.apk",
    "/s/",              # the landing page the invite link opens
    "/invite.png",      # the picture in a chat app's preview card
    "/feedback",        # report a fault, or ask for something
    # A player saying what it is doing - which machines it found, what it chose, why
    # it stopped. It was listed among what a guest may write and not among what makes
    # somebody a guest at all, so every one of these was refused: the only people
    # whose players could report anything were the ones sitting at the machine, and
    # the trouble is nearly always somewhere else.
    "/trace",
    "/applog",          # and a crash, for the same reason
    "/changes",         # what has been added lately: everyone's business
    "/mood",            # how the screen should be dressed, which is not a secret
    "/where",           # both ways in to this machine, to whoever already has one
    "/skins",           # the looks a screen may wear, and picking one
    "/copying",         # what the second machine is fetching, for whoever is watching it
    "/subs/",           # subtitle files beside a film, searching for more, and
                        # asking for one to be written down from the soundtrack
    "/log",             # the browser's diagnostic trail, as /applog is the app's
    "/mystream",        # the rate their own film is arriving at, for their own
                        # statistics line: it tells them about nobody else
    "/standby",         # where the machine keeping copies is, and their own way in
                        # to it: a guest whose evening stops needs it before it does
    "/copies",          # which titles that machine holds, for the dot on a poster
    "/config",          # sanitised for a guest: nothing about how this is set up
    "/settings",        # how subtitles are drawn; readable, not writable
    "/watchlist",       # what they mean to watch: their own list, in their own corner
    "/favorites",       # and which of those they keep: the same shelf, the same corner
    # How many seconds of film a screen has left, which is the screen's own business
    # and the one thing only it knows. It was listed among what a guest may write and
    # not among what makes somebody a guest at all - the same mistake as /trace above -
    # so every report from a guest was refused at the door. Copying gave way only to
    # whoever was sitting at the machine, and a guest on the far side of the line is
    # exactly who it should be giving way to.
    "/stream/buffer",
    "/marks",           # how much of a programme is on either of those shelves
    "/casual",          # and which of those are for putting on without choosing
    "/ondeck",          # and what they have put aside from Continue watching
    "/collections",     # their own shelves: made by them, seen by them, like the list
    "/build",           # which build drew this page. It says nothing about the main server:
                        # "lan" is already false for anybody reading it from outside
    "/setup.js", "/tizen.js",   # page code the browser loads before it knows who it is
    "/party",           # the watch party: a guest is somebody to watch with
    "/chat",            # what is said in the lobby and in the party
    "/notice",          # and a line from the server addressed to them
    "/app.js", "/chat.js", "/controls.js", "/settings.js", "/style.css", "/index.html",
    "/dash.all.min.js", "/favicon.ico", "/palladium.ico",
)


class Invites:
    """The list of guests, held in memory because it is consulted constantly.

    Reading it from disk on every request - and writing it back to count the hit -
    meant that two requests arriving together could leave one of them looking at a
    half-written file and concluding that a perfectly good token was unknown. In the
    middle of a film that reads as "access denied" for no reason anybody can see.
    """

    #: how often the hit counters are allowed to reach the disk. They are worth
    #: keeping, but not worth a write per request.
    FLUSH = 30

    def __init__(self, root):
        self.path = os.path.join(root, "invites.json")
        self.lock = threading.Lock()
        self.rows = None            # None until first read
        self.stamp = 0              # mtime the memory copy was read from
        self.dirty = 0              # when the counters last changed
        self.flushed = 0

    def _read(self):
        """The file, or an empty list. Caller holds the lock."""
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = json.load(f)
            return rows if isinstance(rows, list) else []
        except FileNotFoundError:
            return []
        except Exception:
            # unreadable for a moment: keep what is already known rather than
            # forgetting every guest at once
            return self.rows if self.rows is not None else []

    def _fresh(self):
        """The rows, re-read only if something else has changed the file."""
        try:
            stamp = os.path.getmtime(self.path)
        except OSError:
            stamp = 0
        if self.rows is None or stamp != self.stamp:
            self.rows = self._read()
            self.stamp = stamp
        return self.rows

    def load(self):
        with self.lock:
            return json.loads(json.dumps(self._fresh()))     # a copy to hand out

    def _write(self, rows):
        """Whole or not at all: written beside the file and moved over it."""
        tmp = "%s.%d.tmp" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        # Windows refuses the swap while anybody holds the file open; the lock is
        # gone in a moment, so it is tried again rather than thrown away
        for again in range(6):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                if again == 5:
                    raise
                time.sleep(0.05)
        self.rows = rows
        try:
            self.stamp = os.path.getmtime(self.path)
        except OSError:
            self.stamp = 0
        self.flushed = time.time()
        self.dirty = 0

    def save(self, rows):
        with self.lock:
            self._write(rows)

    def create(self, name, days=0, email=""):
        row = {
            "token": secrets.token_urlsafe(24),
            "name": name or "guest",
            "email": email or "",
            "created": int(time.time()),
            "expires": int(time.time()) + days * 86400 if days else 0,
            "lastSeen": 0,
            "hits": 0,
        }
        with self.lock:
            rows = self._fresh()
            rows.append(row)
            self._write(rows)
        return row

    def set_email(self, token, email):
        """Fill in an address later, so the link can be posted again without asking."""
        with self.lock:
            rows = self._fresh()
            for row in rows:
                if row["token"] == token:
                    row["email"] = email
            self._write(rows)
            return json.loads(json.dumps(rows))

    def revoke(self, token):
        with self.lock:
            rows = [r for r in self._fresh() if r["token"] != token]
            self._write(rows)
            return json.loads(json.dumps(rows))

    def check(self, token, app=""):
        """The matching invite, or None. Expiry is enforced here, not by the caller.

        `app` is what the client called itself, which is written down beside the
        counters: the owner has no other way of knowing who is still on a build from
        three weeks ago.
        """
        if not token:
            return None
        now = int(time.time())
        with self.lock:
            rows = self._fresh()
            for row in rows:
                if hmac.compare_digest(str(row.get("token", "")), token):
                    if row.get("expires") and now > row["expires"]:
                        return None
                    row["lastSeen"] = now
                    row["hits"] = row.get("hits", 0) + 1
                    if app and row.get("app") != app:
                        row["app"] = app
                        self.dirty = self.dirty or now
                    self.dirty = self.dirty or now
                    # the counters are worth keeping but not worth a write per
                    # request: a film is dozens of requests a second
                    if now - self.flushed >= self.FLUSH:
                        try:
                            self._write(rows)
                        except Exception:
                            pass        # the memory copy is still right
                    return dict(row)
        return None

    def flush(self):
        """Put the counters on disk. Called when the server is closing down."""
        with self.lock:
            if self.dirty and self.rows is not None:
                try:
                    self._write(self.rows)
                except Exception:
                    pass

    #: no O or I, no 0 or 1: a code is read out loud and typed with a remote, and
    #: those are the four characters people get wrong
    ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    @classmethod
    def code_for(cls, token):
        """A short, stable, typable stand-in for one token.

        Derived from the token rather than stored, so it survives a restart, can be
        read out over the telephone, and needs no expiry of its own - revoking the
        invitation revokes the code with it.
        """
        if not token:
            return ""
        digest = hashlib.sha256(("palladium-install:" + token).encode()).digest()
        n = int.from_bytes(digest[:5], "big")
        out = ""
        for _ in range(5):
            out += cls.ALPHABET[n % len(cls.ALPHABET)]
            n //= len(cls.ALPHABET)
        return out

    def by_code(self, code):
        """The invitation a code stands for, or None."""
        want = (code or "").strip().upper()
        if len(want) != 5:
            return None
        for row in self.load():
            if hmac.compare_digest(self.code_for(row.get("token", "")), want):
                return row
        return None

    #: Whole paths a guest may reach, matched exactly. "/update" says what build a
    #: machine is running, which is worth showing on any screen; "/update/install"
    #: replaces the program and is the owner's, so this cannot be a prefix.
    GUEST_EXACT = ("/update",)

    @staticmethod
    def allowed(path):
        bare = path.split("?", 1)[0]
        return (path == "/" or bare in Invites.GUEST_EXACT
                or path.startswith(GUEST_PREFIXES))

    #: whole paths rather than beginnings: "/app" is the install page, and matching it
    #: as a prefix would also have opened /app.js and /applog to anybody
    PUBLIC_EXACT = ("/app",)

    @staticmethod
    def public(path):
        """True for the few things that need no invitation at all."""
        return path in Invites.PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)
