"""The house's own pages and errands: the app and the installer handed out, fault reports and health, the watch party, and the landing page.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
import shutil
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
import pd_safe

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class HouseParts(_Base):
    def tell_the_site(self, row, forced=False):
        """Hand one fault to palladium.video, and write down what came of it.

        (sent, why). The row is marked sent only once the site has taken it, and
        carries the reason when it would not go - a machine with no way out says so
        on the report rather than showing 115 faults it thinks it has sent.
        """
        ident = str(row.get("id") or "")

        def wrote(ok, why):
            if not ident:
                return
            if ok:
                self.amend_report(ident, {"sentAway": int(time.time()), "sendWhy": ""})
            else:
                self.amend_report(ident, {"sendWhy": str(why or "")[:160],
                                          "sendTried": int(time.time())})

        try:
            import pd_faults
            # read where the page writes it: with the machine's own settings, not
            # the viewer's - what a computer sends about itself is the computer's
            how = str(S.local().lib.config().get("sendFaults") or pd_faults.OFF)
            ok, why = pd_faults.send(row, how, build=self.build_version(),
                                     app=str(row.get("app") or ""),
                                     called=self.server_name(), forced=forced,
                                     people=self.everyone_here(), then=wrote)
        except Exception as e:
            ok, why = False, str(e)[:160]
        if forced:
            wrote(ok, why)
        return ok, why

    #: what the graphics card last said about itself, and when
    CARD = {"when": 0.0, "said": {}}

    @classmethod
    def card_now(cls):
        """What the graphics card is doing, if it will say. Asked twice a minute."""
        now = time.time()
        if now - cls.CARD["when"] < 4:
            return cls.CARD["said"]
        said = {}
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,"
                 "temperature.gpu,name", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            bits = (out.stdout or b"").decode("utf-8", "replace").strip().split(",")
            if len(bits) >= 5:
                said = {"busy": int(bits[0]), "used": int(bits[1]),
                        "total": int(bits[2]), "hot": int(bits[3]),
                        "name": bits[4].strip()}
        except Exception:
            said = {}
        cls.CARD.update(when=now, said=said)
        return said

    def how_busy(self):
        """Everything running on this machine that a person would feel."""
        import pd_ai_subs
        streams = []
        try:
            for one in (S.engine().status() or {}).get("streams", []):
                streams.append({"key": one.get("key"), "height": one.get("height"),
                                "running": one.get("running"),
                                "burn": bool(one.get("burn_index") is not None)})
        except Exception:
            pass
        try:
            import pd_follow
            copying = pd_follow.look()
        except Exception:
            copying = {}
        writing = pd_ai_subs.look()
        lib = S.local().lib
        return {
            "game": self.game_mode(),
            "does": self.GAME_DOES,
            "card": self.card_now(),
            "streams": streams,
            "writing": {"on": writing.get("on"), "queued": len(writing.get("queued") or [])},
            "scan": dict(lib.scan_state),
            "copying": {"on": copying.get("on"), "file": copying.get("copying"),
                        "at": copying.get("at")},
            "watching": len(S.local().now_playing().get("Metadata", []) or []),
        }

    def wants_a_party(self):
        """Whether this viewer takes part in watch parties at all.

        Their own answer, not the owner's: a chat box on somebody's screen while they
        are watching something is a thing they should be able to say no to, and saying
        no should mean nothing arrives rather than merely nothing being shown. The
        owner can still send a notice - that is the server speaking to its own
        screens, not a room full of people.
        """
        return self.viewer_settings(self.settings_file()).get("watchParty") is not False

    #: What a screen is called to somebody who may not know where it is.
    A_SCREEN = "a screen in the house"

    def named_for_reader(self, name):
        """A name safe to show to whoever is asking.

        An address identifies a machine and, in a house, a person. The owner is
        looking at their own network and may see them; a guest is looking at other
        people, and gets the name on an invitation or nothing at all.
        """
        said = str(name or "")
        if self.role == "owner":
            return said
        looks_like_address = (said.replace(".", "").isdigit() and said.count(".") == 3
                              or ":" in said)
        return self.A_SCREEN if looks_like_address or not said else said

    def party_key(self):
        """Who this caller is, as far as a party is concerned.

        A guest is their invitation, wherever they are watching from; anybody in the
        house is the screen they are on, because a house has no names for its
        televisions.
        """
        if self.role == "guest" and self.guest_name:
            return "guest:" + self.guest_name
        return "here:" + self.client_address[0]

    def said_safely(self, rows):
        """Messages as this reader may see them: names, never addresses."""
        if self.role == "owner":
            return rows
        out = []
        for m in rows:
            said = dict(m)
            said["who"] = said["from"] = self.named_for_reader(
                m.get("from") or m.get("who"))
            said.pop("to", None)
            out.append(said)
        return out

    def join_party(self):
        """Mark this caller present. Reading the room is being in it."""
        if not S.PARTY["on"]:
            return
        key = self.party_key()
        row = S.PARTY["who"].setdefault(key, {
            "name": self.watcher(), "where": self.client_address[0],
            "kind": self.device_kind() or ""})
        row["seen"] = int(time.time())

    def addressed_to_me(self, whom):
        """Whether a message marked for somebody is for the screen now asking.

        Empty is the main server: everybody on this network. An address is one screen -
        which is how a test reaches the television and nothing else. "all" is the
        house and everybody watching from outside it, which is what a room is.
        """
        want = str(whom or "").strip().lower()
        if not want:
            return self.in_the_house()
        if want == "all":
            return True
        if want == "party":
            return S.PARTY["on"] and self.party_key() in S.PARTY["who"]
        return self.client_address[0].lower() == want

    def installer_folders(self):
        """Where an installer made on this machine would be sitting."""
        return (S.STATIC, os.path.join(S.CODE, "build"))

    def installer_here(self):
        """The newest Palladium-Setup beside this program, if there is one.

        Two places, because there are two ways to be running: a build keeps it in
        static/ next to the web client, and a tree that has just built one has it in
        build/. Newest wins - a stale installer is worse than none.
        """
        looked = []
        for where in (S.STATIC, os.path.join(S.CODE, "build")):
            try:
                for name in os.listdir(where):
                    low = name.lower()
                    if low.startswith("palladium-setup") and low.endswith(".exe"):
                        full = os.path.join(where, name)
                        looked.append((os.path.getmtime(full), full))
            except OSError:
                continue
        return max(looked)[1] if looked else ""

    def send_installer(self):
        """Hand over the installer, named for the version it is."""
        where = self.installer_here()
        if not where:
            self.send_error(404, "No installer on this machine")
            return
        try:
            size = os.path.getsize(where)
            f = open(where, "rb")
        except OSError:
            self.send_error(404, "No installer on this machine")
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.microsoft.portable-executable")
            self.send_header("Content-Length", str(size))
            # named for the build, so a Downloads folder says which one it holds
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(where))
            self.end_headers()
            shutil.copyfileobj(f, self.wfile)

    #: torrent hash -> when it was first seen checking, or first seen stuck at 100%
    CHECK_SEEN = {}
    STUCK_SEEN = {}

    def health_here(self):
        """This machine's part of the health page: itself, its torrents in trouble, its
        disks, its workers, its open fault reports and - on the main server - the backup."""
        import shutil
        import pd_torrents
        import pd_tracker
        now = time.time()
        facts = {"machines": [{"name": self.server_name(), "build": self.build_version(),
                               "up": True}],
                 "torrents": [], "disks": [], "workers": [], "faults": 0, "backup": None}
        saves = ""
        try:
            qb = pd_torrents.QB(pd_torrents.load()["config"])
            saves = str(json.loads(qb._call("/api/v2/app/preferences", timeout=10) or b"{}")
                        .get("save_path") or "")
            for t in json.loads(qb._call("/api/v2/torrents/info", timeout=10) or b"[]"):
                mark, state = str(t.get("hash") or ""), str(t.get("state") or "")
                checking = S.Handler.CHECK_SEEN.setdefault(mark, now) if state.startswith("checking") \
                    else S.Handler.CHECK_SEEN.pop(mark, None)
                stuck = (state in ("stalledDL", "forcedDL") and int(t.get("amount_left") or 0) <= 0
                         and float(t.get("progress") or 0) >= 0.999)
                since = S.Handler.STUCK_SEEN.setdefault(mark, now) if stuck else S.Handler.STUCK_SEEN.pop(mark, None)
                stuck = stuck and now - float(since or now) >= pd_tracker.STUCK_FOR
                if state in ("error", "missingFiles") or stuck or state.startswith("checking"):
                    facts["torrents"].append({"name": t.get("name") or "", "state": state,
                                              "stuck": stuck, "since": checking})
        except Exception as e:
            facts["torrents"].append({"name": "qBittorrent", "state": "error",
                                      "why": str(e)[:100]})
        facts["disks"] = S.library_drives()
        facts["diskFaults"] = S.disk_faults_now()
        facts["diskQueue"] = __import__("pd_disk").status()
        facts["workers"] = [{"name": w["name"], "state": w["state"]} for w in S.workers_now()]
        facts["tracker"] = {"cookie": bool(pd_tracker.cookie()),
                            "expired": bool(pd_tracker.STATE.get("sessionGone"))}
        try:
            facts["faults"] = sum(1 for r in self.reports()
                                  if r.get("source") == "auto" and not r.get("done"))
        except Exception:
            pass
        try:
            with open(os.path.join(S.ROOT, "backup.json"), encoding="utf-8") as f:
                facts["backup"] = json.load(f)
        except (OSError, ValueError):
            pass
        return facts

    def health_of_followers(self, facts):
        """The machines that follow this one, asked for their part and added in: a machine
        that does not answer is down; one that will not say is up with its own part left out."""
        import urllib.request as _ureq
        for where, row in list(S.Handler.FOLLOWERS.items()):
            if row.get("revoked"):
                continue
            name = row.get("name") or where
            # one machine filed under its home address and its way in from outside: asked
            # once it has answered, and counted once
            if any(m["name"] == name and m["up"] for m in facts["machines"]):
                continue
            one = {"name": name, "build": "", "up": False}
            facts["machines"].append(one)
            try:
                req = _ureq.Request(where.rstrip("/") + "/follow/health",
                                    headers={"X-Palladium-House": "1"})
                with _ureq.urlopen(req, timeout=8) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
                theirs = said.get("facts") or {}
                me = (theirs.get("machines") or [{}])[0]
                one.update(build=me.get("build") or "", up=True)
                for t in theirs.get("torrents") or []:
                    facts["torrents"].append(dict(t, name="%s: %s" % (name, t.get("name"))))
                for d in theirs.get("disks") or []:
                    facts["disks"].append(dict(d, drive="%s %s" % (name, d.get("drive"))))
                for w in theirs.get("workers") or []:
                    facts["workers"].append(dict(w, name="%s %s" % (name, w.get("name"))))
            except Exception:
                try:
                    with _ureq.urlopen(where.rstrip("/") + "/config", timeout=5) as answer:
                        json.loads(answer.read().decode("utf-8", "replace") or "{}")
                    one["up"] = True
                except Exception:
                    pass
        import pd_health
        facts["machines"] = pd_health.one_each(facts["machines"])

    @staticmethod
    def build_version():
        """What build this is, taken from the changelog it ships with.

        Not server_version: that name belongs to BaseHTTPRequestHandler, which puts it
        in the Server header - shadowing it with a method broke every reply the server
        made.
        """
        # What this program was built as, baked in when it was built. A file beside
        # the program is not the program: building an installer rewrites the
        # changelog there, and the server then claimed a version it was not running
        # until somebody restarted it - which is how a machine one build behind
        # reported itself up to date.
        if S.PACKAGED:
            # the number the build stamped beside the program: a server whose code did
            # not change is not compiled again, so its baked-in pd_built is older
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                       "built.txt"), encoding="utf-8") as f:
                    said = f.read().strip()
                if re.match(r"^[0-9]+[.][0-9]+[.][0-9]+$", said):
                    return said
            except OSError:
                pass
            try:
                import pd_built
                if pd_built.VERSION:
                    return str(pd_built.VERSION)
            except Exception:
                pass
        try:
            with open(os.path.join(S.CODE, "changes.json"), encoding="utf-8") as f:
                return str(json.loads(f.read())[0].get("version") or "0.0.0")
        except Exception:
            return "0.0.0"

    def owner_name(self):
        """What to call the owner in a list that names everybody else.

        Guests are named by their invitation, so "you" beside them read as a fourth
        kind of person rather than as the one reading the page.
        """
        return str(self.settings_file().get("ownerName") or "").strip() or "you"

    def watch_stats(self):
        """Hours, viewings and titles over three windows, and who did the watching.

        Counted from the watch log, which is written from what the players report, so
        it covers the browser, the phone and the television alike. Time is the length
        of each viewing - from its first report to its last - rather than the length
        of the film, because a film left running for ten minutes is ten minutes.
        """
        now = int(time.time())
        windows = (("week", now - 7 * 86400), ("month", now - 30 * 86400),
                   ("total", 0))
        names = {r["token"]: r["name"] for r in S.INVITES.load()}
        # not "mine": that name is taken further down for the rows in the window
        ownName = self.owner_name()
        con = S.local().lib.db()
        try:
            # every viewing, the ones the other server wrote down as well: left out, a
            # guest who watched from the cache counted 44 hours of 72 (2026-11-08)
            rows = [dict(r) for r in con.execute(
                "SELECT who, key, title, started, updated, position, duration, casual "
                "FROM watchlog").fetchall()]
        finally:
            con.close()
        viewings = S.sittings(rows)
        out = {}
        for name, since in windows:
            mine = [r for r in viewings if (r["started"] or 0) >= since]
            people, titles = {}, {}
            seconds = 0
            for r in mine:
                took = r["took"]
                seconds += took
                # str, because this is a key: one invitation held a list where its
                # name should be, and the whole figures page answered 500 rather than
                # counting the hours of everybody else
                who = str(ownName if r["who"] == "me"
                          else names.get(r["who"], "a guest") or "a guest")
                people[who] = people.get(who, 0) + took
                if r["title"]:
                    titles[r["title"]] = titles.get(r["title"], 0) + took
            ranked = sorted(people.items(), key=lambda kv: -kv[1])
            out[name] = {
                "hours": round(seconds / 3600.0, 1),
                "viewings": len(mine),
                "titles": len({r["key"] for r in mine if r["key"]}),
                "casual": len([r for r in mine if r["casual"]]),
                "people": [{"who": w, "hours": round(sec / 3600.0, 1)}
                           for w, sec in ranked],
                # the three longest sittings, which is what "what have we been
                # watching" actually means
                "top": [{"title": t, "hours": round(sec / 3600.0, 1)}
                        for t, sec in sorted(titles.items(),
                                             key=lambda kv: -kv[1])[:5]],
            }
        return out

    def note_fault(self, trail):
        """File a fault the server itself hit, once.

        A broken page asks again every few seconds, and a hundred identical rows on
        the noticeboard hide everything else - so the same fault is filed once an
        hour and counted in the log the rest of the time.
        """
        lines = [ln.strip() for ln in (trail or "").strip().splitlines() if ln.strip()]
        if not lines:
            return
        # the last line names the fault; the line above it is where in the program
        signature = " | ".join(lines[-2:])[:200]
        now = time.time()
        if now - S.FAULTS_SEEN.get(signature, 0) < 3600:
            return
        S.FAULTS_SEEN[signature] = now
        head = lines[-1]
        # a worker in the background has no request: called with None for self
        where = ("%s %s" % (self.command, self.path.split("?")[0]) if self is not None
                 else "background work")
        try:
            S.Handler.file_report(self, "error", head + chr(10) + where + chr(10)
                                + chr(10).join(lines[-6:]), "server")
        except Exception:
            pass                       # a fault while filing a fault is not worth one

    def file_report(self, kind, text, app=""):
        """Put one report on the noticeboard, as though somebody had written it.

        Used for the faults the machinery notices - a crash posted by the app, a fault
        the page caught - which is why the person is whoever the request came from
        rather than whoever is reading.
        """
        row = {
            "id": "%x" % (int(time.time() * 1000) % 0xFFFFFFFFFF),
            "when": int(time.time()),
            "who": ("server" if self is None
                    else self.guest_name if self.role == "guest" else "you"),
            "kind": kind,
            "text": (text or "").strip()[:2000],
            "from": self.client_address[0] if self is not None else "",
            "app": app,
            "source": "auto" if kind in ("error", "crash") else "person",
        }
        if not row["text"]:
            return
        with open(os.path.join(S.ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + chr(10))

    def reports(self):
        """What people have written in, newest first.

        Everyone using the server can read this, so that two people do not report the
        same fault twice - everyone except for the rows the owner has hidden, which are
        not sent at all rather than merely marked.
        """
        path = os.path.join(S.ROOT, "feedback.jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        out = list(reversed(out))[:200]
        # what is still wrong comes first: a sorted report is worth reading - somebody
        # hitting the same fault should see it is known - but it is not news
        out.sort(key=lambda r: 1 if r.get("done") else 0)
        if self.role == "owner":
            return out
        # a guest sees the words, not the addresses they came from - and not the
        # security warnings, which name addresses in their text
        return [{k: v for k, v in r.items() if k != "from"}
                for r in out if not r.get("hidden") and not r.get("owner")]

    def app_version(self):
        """What the app beside this server is, or empty if there is no app.

        Two places, because there are two ways it got here: the installer puts it
        beside the program, and a server that fetched one for itself keeps it with
        its own things.
        """
        for where in (S.STATIC, S.ROOT):
            try:
                with open(os.path.join(where, "version.json"), encoding="utf-8") as f:
                    return str(json.loads(f.read()).get("versionName") or "")
            except Exception:
                continue
        return ""

    #: Where a server with no app beside it can fetch one, and what it is told is
    #: current. Read, never written to: nothing about this machine goes with the
    #: request.
    APP_FROM = "https://get.palladium.video/app"
    APP_SAYS = "https://palladium.video/version.json"

    #: One fetch at a time, and not again for an hour if it failed
    APP_FETCH = {"busy": False, "tried": 0.0, "where": "", "got": 0, "size": 0,
                 "part": 0.0}

    @staticmethod
    def app_beside_us():
        """The app file this server hands out: the one it was installed with, or the
        one it fetched for itself."""
        near = os.path.join(S.STATIC, "palladium.apk")
        if os.path.exists(near):
            return near
        mine = os.path.join(S.ROOT, "palladium.apk")
        return mine if os.path.exists(mine) else ""

    def app_channel(self):
        """"latest" or "daily": the app this viewer is offered, as they chose in Settings -
        the owner the newest unless they said otherwise, everyone else the daily one."""
        # decided here: the app's routes are answered before the request's role is, and a
        # role left on the handler is the last request's on this connection - every phone
        # was told the daily app, the owner's included (0.19.404)
        try:
            role = self.who(str(self.path).split("?")[0])
        except Exception:
            role = None
        try:
            said = str(self.viewer_settings(self.settings_file()).get("appChannel") or "")
        except Exception:
            said = ""
        if said in ("latest", "daily"):
            return said
        # the owner away from home is the owner too
        return "latest" if role == "owner" or getattr(self, "owner_away", False) else "daily"

    def save_viewer_choice(self, body):
        """One of the viewer's own switches from Settings, saved and answered: the app they
        are offered ("latest" or "daily"), or splitPlay, failover, dropWatched. True when
        the body was one of them."""
        if "appChannel" in body:
            name, said = "appChannel", ("latest" if body.get("appChannel") == "latest" else "daily")
        else:
            name = next((n for n in ("splitPlay", "failover", "dropWatched") if n in body), "")
            if not name:
                return False
            said = bool(body[name])
        stored = self.settings_file()
        self.viewer_settings(stored)[name] = said
        S.write_settings(stored, merge=False)
        self.reply_json({name: said})
        return True

    def app_for_asker(self):
        """(apk, version.json) this asker is given: the newest at once, or the copy
        released at eight (owner, 2026-11-05) - the newest where none has been released
        yet. Which is the viewer's own choice in Settings."""
        newest = self.app_beside_us()
        kept = os.path.join(S.ROOT, S.APP_RELEASED, "palladium.apk")
        if self.app_channel() == "daily" and newest and os.path.exists(kept):
            return kept, os.path.join(S.ROOT, S.APP_RELEASED, "version.json")
        if not newest:
            return "", ""
        return newest, os.path.join(os.path.dirname(newest), "version.json")

    @classmethod
    def fetch_app(cls, force=False):
        """Take a copy of the app, once, in the background.

        A container is built from source and source does not include an APK, so a
        server can be running perfectly well with no app to hand anybody. It is a
        download, not a decision: the same file the site offers, written where this
        server keeps its own things.
        """
        if cls.APP_FETCH["busy"]:
            return
        if not force and time.time() - cls.APP_FETCH["tried"] < 3600:
            return
        try:
            if S.local().lib.config().get("fetchFromSite") is False:
                return                    # this house asks the site for nothing
        except Exception:
            pass
        if cls.app_beside_us():
            return

        def work():
            cls.APP_FETCH.update(busy=True, tried=time.time())
            try:
                req = urllib.request.Request(cls.APP_FROM,
                                             headers={"User-Agent": "palladium"})
                cls.APP_FETCH.update(where=cls.APP_FROM, got=0, size=0, part=0.0)
                with urllib.request.urlopen(req, timeout=180) as r:
                    size = int(r.headers.get("Content-Length") or 0)
                    cls.APP_FETCH["size"] = size
                    parts, got = [], 0
                    while True:
                        lump = r.read(262144)
                        if not lump:
                            break
                        parts.append(lump)
                        got += len(lump)
                        cls.APP_FETCH["got"] = got
                        cls.APP_FETCH["part"] = round(got / size, 3) if size else 0.0
                    body = b"".join(parts)
                if len(body) > 1_000_000 and body[:2] == b"PK":
                    part = os.path.join(S.ROOT, "palladium.apk.part")
                    with open(part, "wb") as f:
                        f.write(body)
                    os.replace(part, os.path.join(S.ROOT, "palladium.apk"))
                    # and what it is, so the page and the file never disagree
                    try:
                        said = urllib.request.Request(
                            cls.APP_SAYS, headers={"User-Agent": "palladium"})
                        with urllib.request.urlopen(said, timeout=30) as r:
                            facts = r.read(4000)
                        json.loads(facts)          # refuse anything that is not it
                        with open(os.path.join(S.ROOT, "version.json"), "wb") as f:
                            f.write(facts)
                    except Exception:
                        pass
            except Exception:
                pass                  # no app to hand out is a state, not a fault
            finally:
                cls.APP_FETCH["busy"] = False

        threading.Thread(target=work, daemon=True).start()

    def send_app(self, head_only=False, who=""):
        """Hand over the app itself.

        Named for its version, because Downloader keeps what it fetches under a name
        taken from the address: ask twice for palladium.apk and the second answer may
        never be looked at, the cache already on the television being installed instead.
        That is what being stuck on an old version looks like.
        """
        # let go of in Settings means not handed out, whatever is on the disk
        apk = "" if (S.read_settings() or {}).get("appOff") else self.app_for_asker()[0]
        if not apk:
            # A server built from source has no app beside it. It can fetch one, but
            # not because a stranger asked for a file: whoever owns this machine says
            # so, in Settings, and until then there is honestly nothing to hand over.
            self.send_error(404, "no app on this server yet - its owner can fetch one "
                                 "from Settings")
            return
        size = os.path.getsize(apk)
        try:
            with open(self.app_for_asker()[1], encoding="utf-8") as f:
                version = str(json.load(f).get("versionName") or "")
        except (OSError, ValueError):
            version = self.app_version()
        name = ("palladium-%s.apk" % version) if version else "palladium.apk"
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "attachment; filename=" + name)
        self.send_header("X-Palladium-Version", version)
        # never a stored copy: the whole point of asking again is to get the new one
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if head_only:
            return
        sent = 0
        with open(apk, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except Exception:
                    break                       # gave up part way
                sent += len(chunk)
        # a successful download used to leave no trace, so "has he got it?" could not
        # be answered by anybody
        pd_log.raw("%s app %s -> %s%s (%.1f of %.1f MB)%s"
                % (time.strftime("%H:%M:%S"), version or "?",
                   self.client_address[0], (" " + who) if who else "",
                   sent / 1e6, size / 1e6, chr(10)))

    def server_id(self):
        """This machine's name for itself: generated once, then kept.

        Two addresses reaching the same server give the same answer, which is the only
        way a client holding both can tell they are one server rather than two.
        """
        path = os.path.join(S.ROOT, "config.json")
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.loads(f.read())
        except Exception:
            cfg = {}
        ident = cfg.get("serverId") or ""
        if not ident:
            import uuid
            ident = uuid.uuid4().hex[:16]
            cfg["serverId"] = ident
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
                pd_safe.settle(f)
            os.replace(tmp, path)
        return ident

    def filed(self):
        """What has been cleared off the board, newest first.

        Everyone sees it. The point of writing down what caused a fault and what fixed
        it is that the next person to hit the same thing reads it instead of reporting
        it again - and that person is usually not the owner. What a guest does not get
        is the addresses the rows came from, and anything the owner has hidden.
        """
        path = os.path.join(S.ROOT, "feedback-archive.jsonl")
        if not os.path.exists(path):
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                row["archived"] = True
                out.append(row)
        out = list(reversed(out))[:200]
        if self.role == "owner":
            return out
        return [{k: v for k, v in r.items() if k != "from"}
                for r in out if not r.get("hidden")]

    def unfile(self, ident):
        """Put one archived row back on the board - a tick pressed by mistake."""
        path = os.path.join(S.ROOT, "feedback-archive.jsonl")
        if not os.path.exists(path) or not ident:
            return None
        kept, back = [], None
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("id") == ident and back is None:
                    back = row
                else:
                    kept.append(row)
        if back is None:
            return None
        back.pop("filed", None)
        back["done"] = False              # back on the board means back to answer
        back["changed"] = int(time.time())
        with open(path, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row) + chr(10))
        with open(os.path.join(S.ROOT, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(back) + chr(10))
        return back

    def file_away(self, wanted):
        """Move the rows `wanted` picks out of the board and into the archive.

        Nothing written on the noticeboard is ever destroyed: a sorted report carries
        what caused the fault and what was done about it, which outlives any interest
        in the fault itself. feedback-archive.jsonl is the same format, so a row can be
        read back or pasted in again by hand.
        """
        path = os.path.join(S.ROOT, "feedback.jsonl")
        if not os.path.exists(path):
            return 0
        kept, gone = [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                (gone if wanted(row) else kept).append(row)
        if not gone:
            return 0
        stamp = int(time.time())
        with open(os.path.join(S.ROOT, "feedback-archive.jsonl"), "a",
                  encoding="utf-8") as f:
            for row in gone:
                row["filed"] = stamp
                row["changed"] = stamp
                f.write(json.dumps(row) + chr(10))
        with open(path, "w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row) + chr(10))
        return len(gone)

    def amend_report(self, ident, changes):
        """Change one row and write its file back out.

        The board and the archive are both a line per report, rewritten whole, so
        hiding a row, ticking it off and writing down what fixed it are all the same
        operation - and the archive has to be included, because the row somebody wants
        to explain is often one that has already been filed away.
        """
        if not ident or not changes:
            return
        for name in ("feedback.jsonl", "feedback-archive.jsonl"):
            path = os.path.join(S.ROOT, name)
            if not os.path.exists(path):
                continue
            rows, hit = [], False
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("id") == ident:
                        row.update(changes)
                        row["changed"] = int(time.time())
                        hit = True
                    rows.append(row)
            if not hit:
                continue
            with open(path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row) + chr(10))
            return

    def hide_report(self, ident, hidden):
        """Take one row out of everyone else's sight, or put it back."""
        self.amend_report(ident, {"hidden": bool(hidden)})

    def watched_at(self, token):
        """When this invitation last watched something, from the watch log.

        The hit counter has been unreliable - it was lost when the process was killed,
        and overwritten when another invitation was created - so an invite can read
        "never used" while the log has an hour of a film against it.
        """
        try:
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT MAX(updated) w FROM watchlog WHERE who=?",
                                  (token,)).fetchone()
            finally:
                con.close()
            return int(row["w"] or 0) if row else 0
        except Exception:
            return 0

    def with_link(self, row):
        """An invite as the settings page wants it: the row plus the link to send."""
        out = dict(row)
        out["link"] = ((S.outside_url() or "http://%s:%d" % (S.LAN_IP, S.PORT))
                       + "/s/" + row["token"])
        out["lanLink"] = "http://%s:%d/s/%s" % (S.LAN_IP, S.PORT, row["token"])
        # And the same key on the machine that keeps copies, for when this one is
        # off. A guest cannot be expected to know there is a second address, and a
        # browser cannot find one for itself: the page it would ask comes from here.
        other = (self.standby_now() or {}).get("outside") or ""
        if other:
            out["copyLink"] = "%s/s/%s" % (other.rstrip("/"), row["token"])
        # the five characters that stand for this invitation: typed into a television,
        # read out over the telephone, and revoked with the invitation itself
        # guests only: the owner's, the cache's and the panel's keys have no code
        out["code"] = (S.Invites.code_for(row["token"])
                       if str(row.get("role") or "user") in ("user", "guest") else "")
        # what the watch log knows, which outlives a counter that has been lost twice
        watched = self.watched_at(row["token"])
        if watched:
            out["watchedAt"] = watched
            if watched > int(out.get("lastSeen") or 0):
                out["lastSeen"] = watched
        return out

    def wants_a_page(self, path):
        """True when a browser is asking for something to look at."""
        if path not in ("/", "/index.html"):
            return False
        return "text/html" in (self.headers.get("Accept") or "")

    def locked_page(self):
        """What a stranger sees: what this is, and how to get in."""
        body = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Palladium</title>"
                "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                "text-align:center;padding:12vh 20px;margin:0'>"
                "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                "margin:10px 0 6px'>PALLADIUM</h1>"
                "<p style='color:#93a0b0;margin:0 0 26px'>This library is private. "
                "Open the invitation link you were sent, and this browser will "
                "remember you.</p>"
                "<form method='get' action='/s/' id='f' "
                "style='display:flex;gap:8px;max-width:460px;margin:0 auto'>"
                "<input id='t' placeholder='paste the link, or the 5-letter code' "
                "style='flex:1;background:#161b22;border:1px solid #232a33;"
                "color:#e8ecf1;border-radius:9px;padding:12px 14px;font:inherit'>"
                "<button style='background:#4a90f0;color:#111;font-weight:600;"
                "border:0;border-radius:9px;padding:12px 20px;font:inherit;"
                "cursor:pointer'>Go</button></form>"
                "<p style='color:#6b7683;font-size:13px;margin-top:22px'>"
                "Lost the link? Ask whoever invited you to send it again.</p>"
                + (
                    # the same door, for the person who owns the place
                    "<form id='p' style='display:flex;gap:8px;max-width:460px;"
                    "margin:26px auto 0'>"
                    "<input id='pw' type='password' placeholder='owner password' "
                    "style='flex:1;background:#161b22;border:1px solid #232a33;"
                    "color:#e8ecf1;border-radius:9px;padding:12px 14px;font:inherit'>"
                    "<button style='background:#232a33;color:#e8ecf1;border:0;"
                    "border-radius:9px;padding:12px 20px;font:inherit;cursor:pointer'>"
                    "Sign in</button></form>"
                    "<p id='pe' style='color:#f0704f;font-size:13px;min-height:18px'>"
                    "</p>"
                    if self.password_set() else "")
                + "<script>var pf=document.getElementById('p');if(pf)pf.onsubmit="
                "function(e){e.preventDefault();"
                "fetch('/login',{method:'POST',headers:{'Content-Type':"
                "'application/json'},body:JSON.stringify({password:"
                "document.getElementById('pw').value})}).then(function(r){"
                "return r.json();}).then(function(d){"
                "if(d.ok){location.href='/';}else{"
                "document.getElementById('pe').textContent=d.error||'no';}});};"
                "document.getElementById('f').onsubmit=function(e){"
                "e.preventDefault();var v=document.getElementById('t').value.trim();"
                "var m=v.match(/\\/s\\/([A-Za-z0-9_-]{8,})/);"
                "if(m){location.href='/s/'+m[1];return;}"
                "if(/^[A-Za-z0-9]{5}$/.test(v)){location.href='/i/'+v+'/open';return;}"
                "if(/^[A-Za-z0-9_-]{8,}$/.test(v)){location.href='/s/'+v;return;}"
                "alert('That does not look like an invitation link or a code.');};"
                "</script></body>").replace("#4a90f0", self.accent_now()).encode()
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def landing(self, token):
        """What a guest sees when they open the link they were sent.

        The token moves out of the address and into a cookie here, so the rest of the
        session is ordinary browsing: posters, subtitle tracks and video segments all
        carry it without every URL having to.
        """
        invite = S.INVITES.check(token)
        if not invite:
            body = ("<title>Palladium</title><body style='background:#0b0d10;"
                    "color:#e8ecf1;font:16px system-ui;text-align:center;padding:14vh'>"
                    "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                    "<h1 style='font-weight:300;letter-spacing:3px;font-size:20px'>"
                    "PALLADIUM</h1><p style='color:#93a0b0'>This invitation is no longer "
                    "valid. Ask for a new link.</p></body>").encode()
            self.send_response(410)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        app = {"versionName": "?"}
        try:
            with open(os.path.join(S.STATIC, "version.json"), encoding="utf-8") as f:
                app.update(json.loads(f.read()))
        except Exception:
            pass
        # what a chat app puts in its preview card: without these it invents
        # something from the address, which reads as junk
        card = ("<meta property='og:title' content='Palladium'>"
                "<meta property='og:site_name' content='Palladium'>"
                "<meta property='og:type' content='website'>"
                "<meta property='og:description' content='%s, this link is your key "
                "to a private film library. Watch in a browser, or install the app "
                "for a phone or a Google TV.'>"
                "<meta property='og:image' content='%s/invite.png'>"
                "<meta name='description' content='A private film library.'>"
                % (invite["name"], S.outside_url() or "http://%s:%d" % (S.LAN_IP, S.PORT)))
        html = ("<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Palladium</title>" + card +
                "<body style='background:#0b0d10;color:#e8ecf1;font:16px system-ui;"
                "text-align:center;padding:9vh 20px;margin:0'>"
                "<div style='font:600 52px Georgia,serif;color:#4a90f0'>P</div>"
                "<h1 style='font-size:22px;letter-spacing:3px;font-weight:300;"
                "margin:10px 0 2px'>PALLADIUM</h1>"
                "<p style='color:#93a0b0;margin:0 0 30px'>Welcome, %(name)s.</p>"
                "<a href='/' style='display:inline-block;background:#4a90f0;color:#111;"
                "font-weight:600;padding:15px 34px;border-radius:9px;text-decoration:none;"
                "font-size:17px'>Watch in the browser</a>"
                "<div style='margin:22px 0 0'><a href='/app?t=%(token)s' "
                "style='color:#4a90f0;font-size:15px'>Install the Android app "
                "(%(versionName)s)</a></div>"
                "<p style='color:#6b7683;font-size:13px;margin-top:34px;line-height:1.7;"
                "max-width:430px;margin-left:auto;margin-right:auto'>"
                "Phones, tablets and Google TV can install the app. If it asks for a "
                "server, paste this whole link into it.</p>"
                # A television has no browser, so none of the above can be pressed
                # there. Downloader is how anything gets onto a Google TV, and it wants
                # an address typed with a remote - hence the short code.
                # An iPhone cannot install an app from a link - Apple does not
                # allow it - but it can keep this page on the home screen, where it
                # opens full screen with its own icon and no browser around it.
                "<div style='color:#6b7683;font-size:13px;margin:26px auto 0;"
                "max-width:430px;line-height:1.8;border-top:1px solid #1e242c;"
                "padding-top:22px;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On an iPhone or iPad</b><br>"
                "Open this page in Safari, press <b style='color:#c3ccd6'>Share</b>, "
                "then <b style='color:#c3ccd6'>Add to Home Screen</b>. It gets its own "
                "icon and opens full screen, already signed in as you."
                "</div>"
                "<div style='color:#6b7683;font-size:13px;margin:18px auto 0;"
                "max-width:430px;line-height:1.8;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On a Google TV</b><br>"
                "1. Install <b style='color:#c3ccd6'>Downloader</b> from the Play "
                "Store.<br>"
                "2. Settings &rsaquo; Apps &rsaquo; Security &amp; restrictions "
                "&rsaquo; Unknown sources: turn <b style='color:#c3ccd6'>Downloader"
                "</b> on.<br>"
                "3. Open Downloader and type this in the address box, exactly "
                "as it reads - no http, nothing on the end:<br>"
                "<span style='display:inline-block;margin:8px 0 4px;font:600 18px "
                "ui-monospace,monospace;color:#4a90f0;letter-spacing:1px'>"
                "%(host)s/i/%(code)s</span><br>"
                "4. When the app asks for a server, give it "
                "<span style='color:#c3ccd6'>%(host)s</span> and the code "
                "<span style='color:#c3ccd6'>%(code)s</span>."
                "</div>"
                # A Samsung set has a browser of its own, and the client is a web
                # page: that is the whole of it for a guest. An installed app needs
                # a certificate made on their own machine, which is a job rather than
                # a step, so it is offered second and plainly.
                "<div style='color:#6b7683;font-size:13px;margin:18px auto 0;"
                "max-width:430px;line-height:1.8;text-align:left'>"
                "<b style='color:#e8ecf1;font-size:14px'>On a Samsung TV</b><br>"
                "Open the television's own web browser and go to<br>"
                "<span style='display:inline-block;margin:8px 0 4px;font:600 18px "
                "ui-monospace,monospace;color:#4a90f0;letter-spacing:1px'>"
                "%(host)s/i/%(code)s/open</span><br>"
                "then press <b style='color:#c3ccd6'>Watch in the browser</b>. That is "
                "the whole library, in the set's own browser, signed in as you - add it "
                "to the favourites and it is one press from then on."
                "<br><br>"
                "For an icon on the home row instead, the app has to be built and "
                "signed on a computer of your own - Samsung installs nothing signed "
                "by anybody else. <a href='/tizen.zip?t=%(token)s' download "
                "style='color:#4a90f0'>The source is here</a>, with this address "
                "already written into it; Tizen Studio and a free Samsung account do "
                "the rest. It is an afternoon, not a step."
                "</div>"
                "</body>") % {"name": invite["name"], "token": token,
                              "versionName": app["versionName"],
                              "host": self.headers.get("Host") or (
                                  "%s:%d" % (S.outside_host() or S.LAN_IP, S.PORT)),
                              "code": S.Invites.code_for(token)}
        body = html.replace("#4a90f0", self.accent_now()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # the invite is the session: a year, renewed on every visit, and only back
        # to this server
        self.send_header("Set-Cookie", self.cookie_for(token))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
