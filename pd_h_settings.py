"""Settings: the server's and each viewer's choices - what is fetched and how, the look of the screens, the start tab, notices and the rest of Settings.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

import pd_h
import pd_notify

if TYPE_CHECKING:
    _Base = Any                          # read through Handler: self.viewer, self.reply_json, ...
else:
    _Base = object

#: pd-server.py, put in pd_h by it before the parts are imported
S: Any = pd_h.SERVER


class SettingsParts(_Base):
    def set_weekly_restart(self, body):
        """The house's weekly restart: both servers follow it, one at a time."""
        if self.role != "owner":
            self.send_error(403, "not allowed")
            return
        import pd_boss
        S.write_settings({"weeklyRestart": pd_boss.clean_weekly(body.get("weeklyRestart"))})
        self.reply_json({"weeklyRestart": (S.read_settings() or {}).get("weeklyRestart")})

    @staticmethod
    def restart_on_crash(stored):
        """Whether this server's watchdog starts it again after a crash: on unless turned off."""
        return (stored or {}).get("restartOnCrash") is not False

    def set_restart_on_crash(self, body):
        """This server's own: started again after a crash, or left down (owner, 2026-11-14)."""
        if self.role != "owner":
            self.send_error(403, "not allowed")
            return
        S.write_settings({"restartOnCrash": bool(body.get("restartOnCrash"))})
        self.reply_json({"restartOnCrash": self.restart_on_crash(S.read_settings())})

    def settings_file(self):
        """Everyone's settings. Empty only when there genuinely are none."""
        stored = S.read_settings()
        return stored if stored is not None else {}

    def viewer(self):
        """Whose settings these are: the owner, or the guest's invitation.

        The owner may have a key of their own - the invitation they use on the
        television and away from the main server. Then that is who they are on every
        screen, this one included, and one person has one history instead of two.
        Owning the machine is still a matter of where the request comes from; the
        key says who is watching, not what they may change.
        """
        if self.role == "owner":
            return str((S.read_settings() or {}).get("ownerIs") or "") or "me"
        return self.bearer() or "guest"

    def viewer_settings(self, stored):
        """The part of settings.json that belongs to whoever is asking.

        The owner keeps the top level - that is where these settings have always been
        - and everyone else gets a corner of their own, started from the defaults.

        The owner may take a key of their own, so that they are the same person on the
        television as at the desk. From that moment they are filed under it like
        anybody else, and everything they had at the top level - their shelves, their
        watchlist, how their subtitles look - was still sitting there and no longer
        being read. It is carried over the first time they are asked for, once.
        """
        who = self.viewer()
        if who == "me":
            return stored
        mine = stored.setdefault("users", {}).setdefault(who, {})
        if who == str(stored.get("ownerIs") or ""):
            for name in S.Handler.VIEWER_KEYS:
                if name in stored and name not in mine:
                    mine[name] = stored.pop(name)
            S.Handler.own_the_old_history(who)
        return mine

    def start_tab(self):
        """The tab this viewer opens on: "start" unless they chose "home"."""
        said = self.viewer_settings(self.settings_file()).get("startTab")
        return "home" if said == "home" else "start"

    def marks_state(self, shelf, key):
        """Whether all, some or none of what this key stands for is on that shelf."""
        mine = self.viewer_settings(self.settings_file())
        held = set(str(k) for k in (mine.get(shelf) or []))
        want = self.spread(key)
        if not want:
            return "none"
        hit = sum(1 for k in want if k in held)
        return "all" if hit == len(want) else ("some" if hit else "none")

    #: What game mode may hold back, and what it holds back unless told otherwise.
    #: Encoding is off by default: stopping it turns somebody's film off, which is a
    #: heavier thing than making them wait for a subtitle.
    GAME_DOES = {"subs": True, "scans": True, "copies": True, "transcode": False}

    @classmethod
    def game_mode(cls):
        """What the machine has been asked to leave alone, as {on, subs, scans, ...}.

        An older setting was a plain yes or no; it is read as everything but encoding.
        """
        said = (S.read_settings() or {}).get("gameMode")
        if isinstance(said, dict):
            out = dict(cls.GAME_DOES, **{k: bool(v) for k, v in said.items()
                                         if k in cls.GAME_DOES})
            out["on"] = bool(said.get("on"))
            return out
        return dict(cls.GAME_DOES, on=bool(said))

    def imposed_skin(self):
        """The look this machine puts on by itself, if any, and why.

        Neither is a preference. "arcade" beats "night": a machine that has given its
        card to a game is doing something else entirely, and that is worth saying at
        any hour.
        """
        stored = S.read_settings() or {}
        if self.game_mode().get("on"):
            return "arcade", "the card is busy"
        one = stored.get("follow") or {}
        if one.get("on") and one.get("master"):
            # a machine that follows another is a machine that keeps copies
            frm = int(one.get("nightFrom") or 22)
            to = int(one.get("nightTo") or 8)
            hour = time.localtime().tm_hour
            dark = (frm <= hour or hour < to) if frm > to else (frm <= hour < to)
            if dark:
                return "night", "the night server, after dark"
        return "", ""

    def mood_now(self):
        """The whole look a screen should wear, in one answer.

        The colours travel rather than being written into three clients in two
        languages: a screen asks what to paint with and paints. `chosen` is what this
        viewer picked and `look` is what is actually in force - they differ when the
        machine has put something on by itself, and a look nobody chose should say so.
        """
        import pd_skins
        stored = S.read_settings() or {}
        mine = self.viewer_settings(stored)
        chosen = pd_skins.known(mine.get("skin") or pd_skins.DEFAULT)
        put_on, why = self.imposed_skin()
        wearing = pd_skins.one(put_on or chosen)
        # The colour this viewer draws with stands where a skin does not name one -
        # the same one the rest of the settings answer with, not the machine's
        # default, or a viewer who picked their own would lose it to a look that
        # asked for nothing.
        if not wearing.get("accent"):
            wearing["accent"] = self.accent_now()
            wearing["onAccent"] = pd_skins.on(wearing["accent"])
        return dict(wearing, look=wearing["id"], chosen=chosen, imposed=bool(put_on),
                    why=why, name=self.server_name())

    @classmethod
    def game_holds(cls, what):
        """Whether game mode is on and holding this kind of work back."""
        game = cls.game_mode()
        return bool(game["on"] and game.get(what))

    #: what belongs to a viewer rather than to the machine. The owner's are kept at
    #: the top level of the settings, mixed in with the server's own, so they move by
    #: name - the port and the library folders are not viewing.
    #: Everything filed under one viewer. Anything missing from this list is left
    #: behind when somebody is named the owner, still in the file and read by
    #: nothing - which is how twelve collections and a front page arrangement came
    #: to vanish on the day their owner took a key of their own.
    VIEWER_KEYS = ("watchlist", "watchlistAt", "favorites", "shuffles",
                   "subtitles", "perTitle", "myAccent", "autoNext", "autoFetch",
                   "autoSync", "watchParty", "subLanguage", "mine", "deckAside",
                   # what this viewer reads subtitles in, and what they read when the
                   # film has nothing in the first. Written under these names since
                   # there were settings at all; "subLanguage" above never was one,
                   # so the language stayed behind when somebody took a key.
                   "subLang", "subLang2",
                   # what the Start tab is told to suggest more of, and never again
                   "startMore", "startNot",
                   # and which tab they open on, and the rows Start carries
                   "startTab", "startRows", "startRowsOff",
                   "collections", "homeRows", "skin",
                   # whether a film may be read off two machines at once, and whether
                   # it may move to another machine when the one serving it stops.
                   # Both on unless somebody says otherwise; both belong to the person
                   # watching, not to the machine.
                   "splitPlay", "failover", "dropWatched",
                   # what somebody calls themselves, which is theirs. The name on the
                   # invitation is the owner's record of who they gave a key to and
                   # stays as they wrote it.
                   "myName",
                   # whether a poster stands behind the shelves. Theirs, on every
                   # screen they watch on: the browser, the phone and the television.
                   "myBackdrop",
                   # which app they are offered: the newest at once, or the daily one
                   "appChannel",
                   # which of the three kinds the film shelf stands: held, fetchable,
                   # askable
                   "filmsShow",
                   # whether the TV tab stands the popular programmes not held
                   "tvPopular",
                   # and series nobody can get
                   "tvRequest",
                   # and films only had as a cam or a telesync
                   "camReleases",
                   # and whether they are told of their watchlist's news
                   "watchMessages",
                   # and whether their app is sent each blink as a notification
                   "notify",
                   # which of the two scores a new film has to satisfy
                   "meters")

    #: What the film shelf stands, unless a viewer says otherwise: everything there
    #: is to watch, whether it is held, fetchable or only askable.
    FILMS_SHOW = {"disk": True, "download": True, "request": False, "upcoming": False}

    #: Which scores a new film has to have satisfied to be listed. Both by default -
    #: the people who watched it and the people paid to review it - and a film has to
    #: pass each one that is on and has a number. Neither on lists everything.
    METERS = {"audience": True, "critics": True}

    def meters(self):
        """Which of the two scores this viewer wants a new film judged by."""
        said = self.viewer_settings(self.settings_file()).get("meters")
        if not isinstance(said, dict):
            return dict(self.METERS)
        return {k: bool(said.get(k, v)) for k, v in self.METERS.items()}

    #: what a guest may fetch: nothing, what the packs carry, or that and whatever
    #: the tracker is holding. Packs alone is how it worked before there was a choice.
    FETCH_KINDS = ("off", "packs", "all")
    #: the biggest single download anybody but the owner may start, in GB. The house's
    #: disk fills with one press of a forty gigabyte release as surely as with forty.
    FETCH_MOST_GB = 10
    #: which release to put forward when a film is carried more than once. 1080p is
    #: what a television across a room shows and a quarter of what a remux costs.
    RESOLUTIONS = ("2160p", "1080p", "720p", "any")
    #: and which of those to take when several match: the one most people are
    #: carrying, which arrives quickest, or the smallest, which costs least.
    PREFER_BY = ("seeds", "size")
    #: h265 holds the same picture in about a third of the room, and every machine
    #: here decodes it; h264 is what a very old client can still play directly.
    CODECS = ("h265", "h264", "any")
    #: where the release came from, which decides what the picture can be at best:
    #: a remux is the disc untouched, a rip is somebody's encode of it, and a WEB-DL
    #: is what the streaming service sent.
    SOURCES = ("remux", "bluray", "webdl", "webrip", "hdtv", "dvdrip", "any")
    #: and what the sound is. Atmos and the lossless tracks are several gigabytes of
    #: the file on their own, which is worth knowing before preferring them.
    SOUNDS = ("atmos", "truehd", "dtshd", "dts", "ddp", "ac3", "aac", "any")

    @staticmethod
    def wish_of(stored, field, allowed, fallback="any"):
        """One stored wish out of a settings dict already in hand."""
        said = str((stored or {}).get(field) or fallback).lower()
        words = [w.strip() for w in said.split(",") if w.strip() in allowed]
        return ",".join(words) if words else fallback

    #: which wish counts first when a release cannot meet them all; the owner sets it
    WISH_ORDER = ("res", "source", "codec", "sound", "rate", "quality", "seeds", "group")
    #: who made a release: the two groups one template is released under, and the rest
    GROUPS = ("oft", "nikt0", "other", "any")

    @staticmethod
    def wish_order(stored):
        """The wishes in the order they count, as set; any left out follow in the usual order."""
        said = [w.strip() for w in str((stored or {}).get("preferOrder") or "").split(",")]
        said = [w for w in said if w in S.Handler.WISH_ORDER]
        return tuple(dict.fromkeys(said + list(S.Handler.WISH_ORDER)))

    @staticmethod
    def ordered_wishes(stored):
        """(kind, wish) for each of the four, most important first."""
        each = {"res": S.Handler.wish_of(stored, "preferRes", S.Handler.RESOLUTIONS, "1080p"),
                "source": S.Handler.wish_of(stored, "preferSource", S.Handler.SOURCES),
                "codec": S.Handler.wish_of(stored, "preferCodec", S.Handler.CODECS),
                "sound": S.Handler.wish_of(stored, "preferSound", S.Handler.SOUNDS),
                "group": S.Handler.wish_of(stored, "preferGroup", S.Handler.GROUPS),
                # one place in the order; which of the two targets applies is the
                # release's own encoding
                "rate": "set",
                "quality": str((stored or {}).get("preferQuality") or "any"),
                "seeds": str((stored or {}).get("preferSeeds") or "any")}
        return tuple((kind, each[kind]) for kind in S.Handler.wish_order(stored))

    @staticmethod
    def tidy_wish(said, allowed):
        """A wish as it is stored: the chosen words, or "any" for no preference.

        Several are allowed - "bluray,webdl" - and any one of them meets the wish.
        None when something was named that is not on the list, which is a mistake
        worth answering rather than storing.
        """
        if isinstance(said, (list, tuple)):
            words = [str(w).strip().lower() for w in said]
        else:
            words = [w.strip().lower() for w in str(said or "").split(",")]
        words = [w for w in words if w]
        if not words or "any" in words:
            return "any"
        if any(w not in allowed for w in words):
            return None
        # in the order they were chosen: the first is preferred over the second
        return ",".join(dict.fromkeys(words))

    def wish(self, field, allowed, fallback="any"):
        """One stored wish, with anything unknown in it dropped."""
        return self.wish_of(S.read_settings(), field, allowed, fallback)

    def prefer_res(self):
        """The resolution to put forward, as the owner set it."""
        return self.wish("preferRes", self.RESOLUTIONS, "1080p")

    def prefer_by(self):
        """What settles it when several releases are that resolution."""
        said = str((S.read_settings() or {}).get("preferBy") or "seeds").lower()
        return said if said in self.PREFER_BY else "seeds"

    def disk_files(self, key="", title="", year=0):
        """[(path, size)] of the files this house holds for one title, on disk now."""
        out = []
        try:
            con = S.local().lib.db()
            try:
                if key:
                    rows = con.execute("SELECT path, size FROM file WHERE item_id=? "
                                       "AND episode_id IS NULL", (key,)).fetchall()
                else:
                    plain = re.sub(r"[^a-z0-9]+", "", str(title or "").lower())
                    rows = [r for r in con.execute(
                        "SELECT i.title, f.path, f.size FROM file f "
                        "JOIN item i ON i.id = f.item_id "
                        "WHERE f.episode_id IS NULL AND (i.year = ? OR ? = 0)",
                        (year, year)).fetchall()
                        if re.sub(r"[^a-z0-9]+", "", str(r["title"] or "").lower())
                        == plain]
            finally:
                con.close()
            for r in rows:
                if r["path"] and os.path.exists(r["path"]):
                    out.append((str(r["path"]), int(r["size"] or 0)))
        except Exception:
            pass
        return out

    def prefer_codec(self):
        """Which encoding to put forward, as the owner set it."""
        return self.wish("preferCodec", self.CODECS)

    def prefer_source(self):
        """Which kind of release to put forward: a disc, a rip, a stream."""
        return self.wish("preferSource", self.SOURCES)

    def prefer_sound(self):
        """Which soundtrack to put forward, where the name says what it is."""
        return self.wish("preferSound", self.SOUNDS)

    #: seconds a release list asked for as of now waits for the tracker
    FRESH_MOST = 12

    def let_them_fetch(self):
        """What people other than the owner may bring into the house."""
        said = str((S.read_settings() or {}).get("letThemFetch") or "packs").lower()
        return said if said in self.FETCH_KINDS else "packs"

    def owner_fetching(self):
        """Whether the one asking is the owner.

        Owning the machine is a matter of where the request comes from, which is
        right for changing settings and wrong for this: the owner on a train is the
        same person as the owner on the sofa, and their own key says so.
        """
        if self.role == "owner":
            return True
        mine = str((S.read_settings() or {}).get("ownerIs") or "")
        return bool(mine and self.bearer() == mine)

    def may_fetch(self):
        """Whether whoever is asking may bring something in."""
        if self.owner_fetching():
            return True
        return self.let_them_fetch() == "all"

    def fetch_most_gb(self):
        """The biggest one download anybody else may start. Nought is no limit."""
        said = (S.read_settings() or {}).get("fetchMaxGb", self.FETCH_MOST_GB)
        try:
            return max(0.0, float(said))
        except (TypeError, ValueError):
            return float(self.FETCH_MOST_GB)

    def films_show(self):
        """Which of the three kinds this viewer wants the film shelf to stand."""
        said = self.viewer_settings(self.settings_file()).get("filmsShow")
        import pd_localapi
        return pd_localapi.films_shown(said, self.FILMS_SHOW)

    @classmethod
    def settled_numbers(cls):
        """The server's built-in constants, grouped, for Settings > Advanced.

        Read from the values themselves so the page cannot disagree with the server.
        Read-only: these are the defaults, not settings.
        """
        import pd_follow
        import pd_torrents

        def row(name, value, what):
            return {"name": name, "value": str(value), "what": what}

        sound = ", ".join("%s %.2g" % (k, v) for k, v in
                          sorted(cls.SOUND_MBIT.items(), key=lambda kv: -kv[1]))
        seen, costs = set(), []
        for name, cost in sorted(cls.CODEC_COST.items(), key=lambda kv: kv[1]):
            if cost not in seen or cost == 1.0:
                costs.append("%s %.2g" % (name, cost))
                seen.add(cost)
        return [
            {"title": "Making a film smaller",
             "why": "What an encoder is asked for when a film cannot be sent as it is. "
                    "Each of these is a ceiling, never a target to fill.",
             "rows": [
                 row("Away, with nothing set", "no ceiling",
                     "A viewer outside this network is held to the ceiling set under "
                     "Quality and to nothing else. It was 8 Mbit, unlisted there."),
                 row("x265 headroom", "%.2g times" % cls.HEVC_HEADROOM,
                     "The only encode allowed above the source rate, since a re-encode "
                     "costs bitrate. Every other codec is capped at what came in."),
                 row("Sound taken off the top", sound + " Mbit",
                     "The library keeps the whole container's rate, so the sound comes "
                     "off before the picture is measured - it is re-encoded to a "
                     "fraction of this anyway. Figures are for 6 channels; 8 cost about "
                     "a third more, 2 about half; an unnamed codec counts as %.2g."
                     % cls.SOUND_OTHERWISE),
                 row("What an old codec costs", ", ".join(costs),
                     "How much of the old bitrate a modern encoder needs for the same "
                     "picture. MPEG-2 spends about 3x h264, so a 21 Mbit broadcast is "
                     "re-encoded at 8."),
                 row("Least given to a picture", "1 Mbit",
                     "Floor on the video ceiling, however small the source."),
                 row("Sound aimed at", "%.4g LUFS" % S.VOLUME_TARGET,
                     "Where a file's loudness is put when evening out is on. LUFS "
                     "measures loudness over the whole file rather than at its peak: "
                     "broadcast works to -23, streaming to about -18, a cinema mix to "
                     "about -27. Two files of one episode here measure 15 dB apart."),
                 row("Most ever added or taken", "%.3g dB" % S.VOLUME_MOST,
                     "Cap on the correction: a file far below the target is brought "
                     "most of the way up, not all of it."),
                 row("Measured over", "20 samples of 15 seconds",
                     "Spread from 5 % to 95 % of the running time and read as one. A "
                     "Dolby track is read a second time with its own compression off, "
                     "as a receiver plays it."),
                 row("Receiver, a typical track", "+%.3g dB" % __import__("pd_receiver").CENTRE,
                     "What a passed-through track of typical loudness is raised by: "
                     "45.0 to 50.0 on the receiver's display."),
                 row("Receiver, corrected within", "%.3g dB" % __import__("pd_receiver").BAND,
                     "A track measuring this close to typical gets that much more or "
                     "less than the typical raise; one further out gets the typical "
                     "raise alone."),
             ]},
            {"title": "Sharing the line",
             "why": "How a background copy gives way to playback. Derived from "
                    "measured throughput, not from a configured figure.",
             "rows": [
                 row("Left clear for the films", "%.0f per cent" % ((1 - cls.SPARE) * 100),
                     "Of measured throughput, kept clear above what playback is "
                     "drawing. A player reads in bursts, and a filling buffer wants "
                     "more than its average."),
                 row("Least a copy may have", "%.3g Mbit" % cls.LEAST_MBIT,
                     "A copy is slowed, never stopped outright."),
                 row("Backing off", "times %.3g" % cls.BACK_OFF,
                     "What the ceiling is multiplied by while somebody watching is "
                     "struggling."),
                 row("Creeping back up", "plus %.3g Mbit" % cls.STEP_UP,
                     "Added each second while nobody is."),
                 row("Struggling", "under %.0f per cent of its own average"
                     % (cls.STARVED * 100),
                     "Measured against each viewer's own average, not the film's "
                     "bitrate: direct play reads in bursts and idles between them, so a "
                     "healthy session sits under the film's bitrate half the time."),
                 row("Seconds a player holds",
                     "%.3g fed, %.3g hold, %.3g give back, %.3g floor"
                     % (cls.FED_AHEAD, cls.EASE_AHEAD, cls.THIN, cls.PANIC_AHEAD),
                     "A fed player holds about 50 s. Under 40 the ceiling stops "
                     "climbing, under 30 it halves, under 20 it drops to the floor - but "
                     "only while the buffer is falling, or under %.3g s. A transcode "
                     "arrives as it is made, so its player holds whatever the encoder is "
                     "ahead by and a steady 8 s is healthy. A player reporting a stall "
                     "goes to the floor whatever its depth." % cls.STALLED_AHEAD),
                 row("How far it may climb", "twice what that side has carried",
                     "Measured throughput is what this ceiling has allowed so far, so "
                     "a ceiling capped by its own measurements can never learn a higher "
                     "one. It doubles every few seconds until the link, a disk or a "
                     "viewer stops it."),
                 row("Two lines, not one", "the network here, and the one out",
                     "A stream going out and a copy between two machines here take no "
                     "megabits from each other, so each side is measured separately and "
                     "a copy gives way only to traffic on its own path. One combined "
                     "figure held a local copy to what was left of the upload."),
             ]},
            {"title": "Keeping copies",
             "why": "Limits on what a second machine holds. Hours ahead and episodes "
                    "ahead are per-machine settings, under the cache machine in "
                    "Settings; these are the bounds around them.",
             "rows": [
                 row("Part-way, each person", "%d days" % cls.PARTWAY_DAYS,
                     "Everything one person is part-way through, however many that "
                     "is - the episode they are on and the one before it, for each "
                     "programme. Only the age of a stored position bounds it now, "
                     "and the disk cap decides what falls off. Six programmes each "
                     "meant a series watched last week was not among them, so its "
                     "next episode was on no list and the copy did not have it."),
                 row("Kept after it leaves the screen", "the last %d each"
                     % cls.SCREEN_EACH,
                     "A title drops off the keep list the moment its player stops "
                     "reporting, and a copy deletes what the list no longer names - "
                     "which took a file out from under somebody with a minute of it "
                     "still to run. The one just finished is held while the next is "
                     "watched, and is there to go back to; the disk cap decides when "
                     "it goes."),
                 row("Least kept ahead", "%d episodes" % cls.LEAST_AHEAD,
                     "Floor on the episode count, whatever the hours setting works out "
                     "to."),
                 row("The shuffle's next", "%d" % cls.SHUFFLE_DEEP,
                     "How far down the shuffle queue is copied, counting the title "
                     "playing: drawing a title marks it played, and it was swept while "
                     "it was on screen."),
                 row("Asked how it is getting on", "every %d s" % pd_follow.ASK_EVERY,
                     "How often the machine keeping copies asks the main server what it "
                     "should be holding."),
                 row("Read in lumps of", "%d MB" % (pd_follow.LUMP // (1024 * 1024)),
                     "What a copy asks for at a time."),
                 row("Given up on after", "%d tries" % pd_follow.GIVE_UP_AFTER,
                     "A file that will not come across."),
                 row("A file being read is kept", "%d s" % pd_follow.KEPT_FOR,
                     "After the last request, so a sweep does not delete a file "
                     "between two requests for it."),
             ]},
            {"title": "Films from a pack",
             "why": "What counts as a film inside a torrent, and how it is fetched.",
             "rows": [
                 row("Smallest thing called a film",
                     "%d MB" % (pd_torrents.FILM_BYTES // (1000 * 1000)),
                     "Anything smaller in a pack is a sample, a trailer or an extra."),
                 row("Asked for in blocks of", "%d kB" % (pd_torrents.BLOCK // 1024),
                     "The unit a torrent client reads in."),
             ]},
            {"title": "Reading and waiting",
             "why": "How long this server waits for things, and when it decides a disk "
                    "is too busy to be given more to do.",
             "rows": [
                 row("First block of a film", "%.3g s" % cls.FIRST_BLOCK_WAIT,
                     "How long a stream is given to send anything at all before it is "
                     "taken to have failed."),
                 row("A disk is busy at", "%.0f per cent of a block's time"
                     % (cls.DISK_BUSY * 100),
                     "How much of each block's time went on reading rather than "
                     "sending. Above this the disk is the bottleneck: more reads are "
                     "handed to the cache machine, and a background copy backs off "
                     "before any viewer's buffer falls."),
                 row("And easy again at", "%.3g s a read" % cls.DISK_EASY,
                     "Below this it is taken up again."),
                 row("Artwork worth keeping", "5 kB",
                     "A poster smaller than this is not an image; it is fetched again "
                     "rather than cached."),
             ]},
        ]

    def library_settings_plain(self):
        """The library's settings, from the library if it opens and from its file if not.

        Library.config() is a read of library.json, but getting to it opens the
        library first - and a library that cannot open then takes the settings down
        with it.
        """
        try:
            return S.local().lib.config()
        except Exception:
            try:
                with open(os.path.join(S.ROOT, "library.json"), encoding="utf-8") as f:
                    got = json.load(f)
                return got if isinstance(got, dict) else {}
            except Exception:
                return {}

    def set_watch_messages(self, said):
        """This viewer's choice of whether they are told of their watchlist's news - a film
        that can be downloaded, one that is here: "on" or "off"; the choice as it stands."""
        said = str(said or "")
        if said in ("on", "off"):
            stored = self.settings_file()
            self.viewer_settings(stored)["watchMessages"] = said
            S.write_settings(stored, merge=False)
        return "on" if pd_notify.messages_on(self.viewer_settings(self.settings_file())) else "off"

    def set_copy_default(self, said):
        """The owner's: which file of a title plays for everybody who has not chosen one,
        {key, file} - a file path of that title, or "" for the best again."""
        if self.role != "owner" or not isinstance(said, dict):
            self.send_error(403, "not allowed")
            return
        key, path = str(said.get("key") or ""), str(said.get("file") or "")
        con = S.local().lib.db()
        try:
            # one of this title's own files, and nothing else
            found = path and con.execute(
                "SELECT 1 FROM file WHERE path=? AND (episode_id=? OR "
                "(item_id=? AND episode_id IS NULL))", (path, key, key)).fetchone()
        finally:
            con.close()
        stored = self.settings_file()
        picks = dict(stored.get("copyDefault") or {})
        if path and found and os.path.exists(path):
            picks[key] = os.path.basename(path)     # by name: the same on both machines
        elif not path:
            picks.pop(key, None)
        stored["copyDefault"] = picks
        S.write_settings(stored, merge=False)
        self.reply_json({"copyDefault": {key: picks.get(key, "")}})

    def set_start_tab(self, said):
        """This viewer's tab the page and the app open on - "home" or "start"; the choice
        as it stands."""
        said = str(said or "")
        if said in ("home", "start"):
            stored = self.settings_file()
            self.viewer_settings(stored)["startTab"] = said
            S.write_settings(stored, merge=False)
        return self.start_tab()

    def set_notify(self, said):
        """This viewer's choice of whether their app is sent each Watchlist blink as a
        notification: "on" or "off"; the choice as it stands."""
        said = str(said or "")
        if said in ("on", "off"):
            stored = self.settings_file()
            self.viewer_settings(stored)["notify"] = said
            S.write_settings(stored, merge=False)
        return "on" if pd_notify.notify_on(self.viewer_settings(self.settings_file())) else "off"

    def set_cam_releases(self, said):
        """This viewer's choice of whether their shelves stand films only had as a cam or a
        telesync, kept when it is one; the choice as it now stands."""
        said = str(said or "")
        if said in ("hide", "show"):
            stored = self.settings_file()
            self.viewer_settings(stored)["camReleases"] = said
            S.write_settings(stored, merge=False)
        return S.local().cam_releases_of(self.viewer_settings(self.settings_file()))

    def accent_default(self, stored=None):
        """The colour the server draws with, for anybody who has not chosen."""
        stored = self.settings_file() if stored is None else stored
        want = str(stored.get("accent", "") or "").lower()
        return want if want in [c for c, _ in S.ACCENTS] else S.ACCENT

    def screen_now(self):
        """Which kind of screen is asking: the television app, a phone, or a browser."""
        said = (self.headers.get("X-Palladium-App") or "").lower()
        if said.startswith("android"):
            return "tv" if said.endswith(" tv") else "phone"
        return "web"

    def backdrop_word(self, said):
        """One of on, poster, off. True and False are what older clients stored.

        Nobody having said: a poster stands on a title's own page and nowhere else.
        Behind the shelves as well is a taste, to be asked for rather than given.
        """
        if said is None:
            return "poster"
        if said is True:
            return "on"
        if said is False:
            return "off"
        word = str(said).strip().lower()
        return word if word in ("on", "poster", "off") else "poster"

    def backdrop_now(self, device=None):
        """Where a poster stands on one kind of screen: on, poster, or off.

        on is behind the shelves as well, poster is a title's own page and nowhere
        else. Per viewer and per screen: a television across the room and a phone at
        arm's length are not the same picture, and one person's answer is not another's.
        """
        which = self.device_of(device or self.screen_now())
        mine = self.viewer_settings(self.settings_file()).get("myBackdrop")
        if isinstance(mine, dict):
            return self.backdrop_word(mine.get(which))
        # what it used to be: one answer for every screen
        return self.backdrop_word(mine)

    def backdrop_all(self):
        """Every screen's answer, for a settings page that draws one row each."""
        return {d: self.backdrop_now(d) for d in self.DEVICES}

    def accent_now(self):
        """The colour this viewer draws with, on every screen they use.

        The server's answer is the default and nothing more: somebody who wants their
        own library green should have it green on the television and on the phone
        alike, without deciding it for the rest of the main server.
        """
        stored = self.settings_file()
        mine = str(self.viewer_settings(stored).get("myAccent", "") or "").lower()
        if mine in [c for c, _ in S.ACCENTS]:
            return mine
        return self.accent_default(stored)

    #: Who a browser may be sent a burned-in bitmap subtitle for. Burning is a full
    #: re-encode of the video, so it is worth deciding rather than assuming. The app
    #: is unaffected either way: it decodes PGS and VobSub itself.
    BURN_FOR = ("all", "home", "none")

    #: what a settings change may name that a copy takes from its main server: the
    #: choosing rules, the ceilings, playback, subtitles, shelves and how a viewer
    #: watches, and the live channels. A copy's own - its jobs, its outside name, a
    #: file's subtitle timing, the pair's pause - stay its own to change.
    MIRROR_LOCKED = frozenset((
        "accent", "autoFetch", "autoScan", "burnFor", "closeCall", "evenVolume",
        "fetchMaxGb", "letThemFetch", "libraryShows", "newDays", "nextDelay",
        "preferBy", "preferCodec", "preferGroup", "preferOrder", "preferRes",
        "preferSound", "preferSource", "preferQuality", "preferSeeds",
        "preferRate264", "preferRate265", "quality", "rateLock", "remoteAdmin",
        "remoteOwner", "seasonal", "seriesRate", "volumeTarget", "watchRegion", "weeklyPicks",
        "weekPick", "weeklyRestart", "skipStart",
        "watchServices", "ownerName", "copyDefault",
        "autoNext", "autoSync", "backdrop", "dropWatched", "failover", "filmsShow",
        "audience", "critics", "meters", "disk", "download", "request", "homeRows",
        "startRows", "startRowsOff",
        "language", "language2", "mine", "myName", "splitPlay", "tvPopular", "tvRequest",
        "camReleases", "watchMessages", "notify",
        "watchParty", "numbering",
        "liveList", "liveMost", "liveGroups", "liveFolders", "liveYouTube", "liveWho"))

    def mirror_of(self):
        """The main server this machine mirrors, by name, or "" when it mirrors none."""
        import pd_follow
        one = pd_follow.settings(S.local().lib.config())
        if not (one.get("on") and one.get("master")):
            return ""
        said = ((S.read_settings() or {}).get("copyOf") or {}).get("master")
        return str(said or one.get("master") or "the main server")

    def mirror_locked(self, body):
        """Why a settings change may not be made here, or "" when it may: on a copy, a
        setting the main server's mirror owns - unless the main server is the one
        asking."""
        if not isinstance(body, dict) or self.headers.get("X-Palladium-House"):
            return ""
        named = sorted(set(body) & self.MIRROR_LOCKED)
        if not named:
            return ""
        master = self.mirror_of()
        if not master:
            return ""
        return "Set on %s: this server mirrors its settings" % master

    def home_default(self, stored=None):
        """The front page anybody meets before they arrange their own."""
        stored = self.settings_file() if stored is None else stored
        return [str(x) for x in (stored.get("homeRowsDefault") or [])]

    def home_rows(self):
        """This viewer's front page: their own arrangement, or the server's."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored).get("homeRows") or []
        return [str(x) for x in mine] or self.home_default(stored)

    def burn_policy(self, stored=None):
        """Who may be sent a burn: "all", "home" (the default) or "none".

        Named apart from burn_for(), which answers a different question - whether one
        particular track can be burned out of this file.
        """
        stored = self.settings_file() if stored is None else stored
        want = str(stored.get("burnFor", "") or "").lower()
        if want in self.BURN_FOR:
            return want
        # the older two-state setting, before "none" existed
        return "all" if stored.get("burnAway") else "home"

    def may_burn(self):
        """Whether this caller may be sent one."""
        mode = self.burn_policy()
        return bool(mode == "all" or (mode == "home" and self.at_home()))

    def auto_scan(self, stored=None):
        """Whether the server puts subtitles in step by itself, unless told not to."""
        return bool((stored if stored is not None
                     else self.settings_file()).get("autoScan", True))

    def scans_for(self, stored=None):
        """The answer for the viewer this request belongs to."""
        stored = self.settings_file() if stored is None else stored
        mine = self.viewer_settings(stored)
        return bool(mine.get("autoSync", self.auto_scan(stored)))

    def set_auto_scan(self, on):
        """Set the server's answer: the default for anybody who has not chosen.

        Whoever has turned it off for themselves in the player keeps that. Their
        answer was given about their own screen and this one is not an instruction to
        forget it.
        """
        stored = self.settings_file()
        stored["autoScan"] = bool(on)
        S.write_settings(stored, merge=False)
        return bool(on)

    def set_accent(self, code, everyone=False):
        """Choose a colour: this viewer's, or the server's answer for everybody.

        An empty code gives a viewer back the server's colour rather than pinning
        them to whatever it happens to be today.
        """
        want = str(code or "").lower().strip()
        stored = self.settings_file()
        if everyone:
            # the owner's to set, and only theirs: it is what a new viewer meets
            if self.role != "owner" or want not in [c for c, _ in S.ACCENTS]:
                return self.accent_now()
            stored["accent"] = want
        else:
            mine = self.viewer_settings(stored)
            if want in [c for c, _ in S.ACCENTS]:
                mine["myAccent"] = want
            else:
                mine.pop("myAccent", None)        # back to the server's
        S.write_settings(stored, merge=False)
        return self.accent_now()
