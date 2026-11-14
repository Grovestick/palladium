"""Subtitles: finding, fetching, ranking, syncing and serving them, and each viewer's subtitle settings.

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


class SubtitleParts(_Base):
    def sub_fit(self, name):
        """The plan worked out for that subtitle: [[from, rate, shift], ...] or none."""
        held = (self.settings_file() or {}).get("subFit") or {}
        got = held.get(name)
        # not worked out here: the main server's, for the same subtitle by its name
        if got is None and self.follows_a_main():
            got = self.house_fits().get(self.fit_key(name))
        if isinstance(got, dict):
            parts = got.get("parts")
            return parts if isinstance(parts, list) and parts else None
        # the older form, one rate and one offset for the whole film
        if isinstance(got, list) and len(got) == 2:
            try:
                return [[0.0, float(got[0]), float(got[1])]]
            except (TypeError, ValueError):
                return None
        return None

    def set_sub_fit(self, name, parts):
        """Write it down for everybody, as a correction by hand is written down."""
        stored = self.settings_file()
        held = stored.get("subFit") or {}
        wanted = [[round(float(a), 1), round(float(r), 6), round(float(sh), 2)]
                  for a, r, sh in (parts or [])]
        # one piece that moves nothing is not a plan
        if not wanted or (len(wanted) == 1 and abs(wanted[0][1] - 1.0) < 1e-6
                          and abs(wanted[0][2]) < 0.05):
            held.pop(name, None)
        else:
            held[name] = {"parts": wanted, "when": int(time.time())}
        stored["subFit"] = held
        S.write_settings(stored)
        return held.get(name)

    def sub_shift(self, key, sub=""):
        """How far this subtitle has been moved, in seconds.

        Kept for the server rather than for one viewer: a file cut for another release
        is out by the same amount for everybody who plays it, and whoever notices
        first should be the last to have to correct it.
        """
        if not key:
            return 0.0
        held = (self.settings_file() or {}).get("subShift") or {}
        for name in (self.shift_name(key, sub), str(key)):
            if name in held:
                try:
                    return float(held.get(name) or 0.0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def shift_source(self, key, sub=""):
        """Who put that correction there: the film, or a person."""
        held = (self.settings_file() or {}).get("subShiftBy") or {}
        for name in (self.shift_name(key, sub), str(key)):
            if name in held:
                return str(held[name])
        return ""

    def set_sub_shift(self, key, seconds, sub="", how="hand"):
        """Anybody may correct it, because it is a fact about the file.

        It says nothing about whether the subtitle is any good: a file can be out by
        a constant amount and still be the wrong translation, or drift apart later,
        or belong to another edit entirely. So this writes down the offset and
        nothing else - the verified mark is earned by hand or by watching.
        """
        stored = self.settings_file()
        held = stored.get("subShift") or {}
        seconds = round(max(-1800.0, min(1800.0, float(seconds))), 2)
        name = self.shift_name(key, sub)
        # and who said so: a number the film asked for reads differently from one
        # somebody dialled in, even though they are the same number
        source = stored.get("subShiftBy") or {}
        if abs(seconds) < 0.05:
            held.pop(name, None)              # back where the file has it: forget it
            held.pop(str(key), None)          # and the old form, if it was there
            source.pop(name, None)
            source.pop(str(key), None)
        else:
            held[name] = seconds
            source[name] = how
        stored["subShift"] = held
        stored["subShiftBy"] = source
        S.write_settings(stored)
        return seconds

    def viewer_language(self):
        """Which language this viewer wants subtitles in."""
        mine = self.viewer_settings(self.settings_file())
        return (mine.get("subLang") or "en").lower()[:5]

    def set_viewer_language(self, code):
        code = re.sub(r"[^a-z-]", "", (code or "en").lower())[:5] or "en"
        stored = self.settings_file()
        self.viewer_settings(stored)["subLang"] = code
        S.write_settings(stored, merge=False)
        return code

    def viewer_language_2(self):
        """What to read instead when the first language is not in the film.

        Empty is no second choice, which is how it behaved before there was one: the
        plainest track in the film, whatever language it is in.
        """
        mine = self.viewer_settings(self.settings_file())
        return (mine.get("subLang2") or "").lower()[:5]

    def set_viewer_language_2(self, code):
        code = re.sub(r"[^a-z-]", "", (code or "").lower())[:5]
        stored = self.settings_file()
        self.viewer_settings(stored)["subLang2"] = code
        S.write_settings(stored, merge=False)
        return code

    def scope_key(self, key):
        """Which title an exception is really about.

        Clients name a title in their own way - "le11155" from the app, a metadata
        path from the browser - and an episode always stands for its series here.
        """
        text = str(key or "")
        found = re.search(r"(?:^|[/l])e([0-9a-f]{12})", text)
        if found:
            show = self.show_of("e" + found.group(1))
            if show:
                return "show|" + show
        season = re.search(r"([0-9a-f]{12})-s\d+", text)
        if season:
            return "show|" + season.group(1)
        # a programme's own page names the programme, which is the scope its episodes read
        whole = re.search(r"(?:^|[/l])([0-9a-f]{12})$", text)
        if whole:
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT type FROM item WHERE id=?",
                                  (whole.group(1),)).fetchone()
            finally:
                con.close()
            if row and row["type"] == "show":
                return "show|" + whole.group(1)
        return text

    def everyones_subtitles(self):
        """How each viewer here has their subtitles drawn, by the key they are known by.

        The owner under "me", everybody else under their token. Sent to the machine
        that keeps copies so an evening carried over mid-film looks the same.
        """
        stored = S.read_settings() or {}
        out = {}

        def bit(one):
            said = {}
            for name in ("subtitles", "perTitle", "subLanguage", "language"):
                if one.get(name):
                    said[name] = one[name]
            return said

        mine = bit(stored)
        if mine:
            out["me"] = mine
        for token, one in (stored.get("users") or {}).items():
            said = bit(one or {})
            if said:
                out[str(token)] = said
        return out

    def subtitle_settings(self, key=None, device="web"):
        """The look for one screen, with that title's exceptions on top of it."""
        device = self.device_of(device)
        mine = self.viewer_settings(self.settings_file())
        out = dict(self.SUBTITLE_DEFAULTS[device])
        kept = mine.get("subtitles", {}) or {}
        # an older file held one flat set for everything; it becomes the starting
        # point for all three rather than being thrown away
        out.update(kept.get(device, kept if "size" in kept else {}))
        if key:
            out.update((mine.get("perTitle", {}) or {})
                       .get(device + "|" + self.scope_key(key), {}))
        # "the same as Position" is not a height: an app with no on-screen height of
        # its own sent it, and it replaced the real one
        try:
            if float(out.get("onPicture", 0)) < 0:
                out["onPicture"] = self.SUBTITLE_DEFAULTS[device].get("onPicture", 0.08)
        except (TypeError, ValueError):
            out["onPicture"] = self.SUBTITLE_DEFAULTS[device].get("onPicture", 0.08)
        return out

    def has_override(self, key, device="web"):
        per = self.viewer_settings(self.settings_file()).get("perTitle", {}) or {}
        return (self.device_of(device) + "|" + self.scope_key(key)) in per

    def save_subtitle_settings(self, body, key=None, device="web"):
        """Clamped on the way in: a client cannot ask for text three screens tall.

        With a key, only what differs from that screen's default is written - so
        raising the default size later still raises it for a film that only disagreed
        about colour.
        """
        device = self.device_of(device)
        cur = self.subtitle_settings(key, device)
        try:
            if "size" in body:
                cur["size"] = round(min(1.8, max(0.7, float(body["size"]))), 2)
            if "position" in body:
                asked = float(body["position"])
                # 0.9 and over is "very bottom", which both players draw at the floor
                cur["position"] = (0.99 if asked >= 0.9
                                   else round(min(0.4, max(0.0, asked)), 3))
            if "onPicture" in body:
                # Off screen's height when the black under the picture is too small
                # for the text and it goes on the picture instead; below nought is
                # "the same as Position"
                asked = float(body["onPicture"])
                # below nought means "not set here": what is held stays
                if asked >= 0:
                    cur["onPicture"] = round(min(0.4, max(0.0, asked)), 3)
            if "font" in body:
                # the faces on offer; anything else is somebody guessing
                name = str(body["font"]).lower()
                if name in ("sans", "serif", "condensed", "rounded", "mono"):
                    cur["font"] = name
            if "shift" in body:
                # seconds, either way: a subtitle cut for another release is usually
                # out by a constant amount rather than drifting
                cur["shift"] = round(min(120.0, max(-120.0, float(body["shift"]))), 2)
            if "colour" in body:
                name = str(body["colour"]).lower()
                if name in ("white", "yellow", "cyan", "green", "grey"):
                    cur["colour"] = name
            if "base" in body:
                # what a height is measured from. Only these two, and anything else is
                # a client with an idea of its own.
                name = str(body["base"]).lower()
                if name in ("screen", "picture"):
                    cur["base"] = name
            if "background" in body:
                # the four every player settles on: nothing, a shadow, a dark box,
                # or a solid one for a bright picture
                name = str(body["background"]).lower()
                if name in ("none", "shadow", "dark", "black"):
                    cur["background"] = name
        except (TypeError, ValueError):
            pass
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        kept = mine.get("subtitles", {}) or {}
        if "size" in kept:                        # migrate the old flat shape
            kept = {d: dict(kept) for d in self.DEVICES}
        if key:
            base = dict(self.SUBTITLE_DEFAULTS[device])
            base.update(kept.get(device, {}))
            differs = {k: v for k, v in cur.items() if base.get(k) != v}
            # a height means nothing without what it is measured from: kept as a pair
            if "position" in differs or "base" in differs:
                differs["position"], differs["base"] = cur["position"], cur["base"]
            per = mine.get("perTitle", {}) or {}
            slot = device + "|" + self.scope_key(key)
            if differs:
                per[slot] = differs
            else:
                per.pop(slot, None)               # back in step with the default
            mine["perTitle"] = per
            mine["subtitles"] = kept
        else:
            kept[device] = cur
            mine["subtitles"] = kept
        S.write_settings(stored, merge=False)
        return cur

    @staticmethod
    def shifted_look(look, shift):
        """The same look, raised - for a client that is cropping the bottom away."""
        try:
            extra = max(0.0, min(0.4, float(shift)))
        except (TypeError, ValueError):
            return look
        if extra <= 0:
            return look
        out = dict(look)
        out["position"] = min(0.45, float(out.get("position", 0.08)) + extra)
        return out

    def clear_override(self, key, device="web"):
        """Forget a title's exceptions on this screen; it follows the default again."""
        device = self.device_of(device)
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        per = mine.get("perTitle", {}) or {}
        per.pop(device + "|" + self.scope_key(key), None)
        mine["perTitle"] = per
        S.write_settings(stored, merge=False)
        return self.subtitle_settings(None, device)

    def opensubtitles(self):
        """A client, or None with a reason - the key is the application's, not a user's."""
        cfg = S.local().lib.config()
        if not (cfg.get("opensubtitles_key") or "").strip():
            return None, ("No OpenSubtitles API key yet. One is needed for the "
                          "application as a whole - Settings, Library.")
        import pd_subs
        return pd_subs.OpenSubtitles(cfg.get("opensubtitles_key"),
                                     cfg.get("opensubtitles_user"),
                                     cfg.get("opensubtitles_pass")), ""

    def _title_for_search(self, key):
        """What to ask OpenSubtitles for, and which file it is about."""
        src = S.local().file_for(key, 0)
        con = S.local().lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT e.season, e.number, i.title, i.year FROM episode e "
                    "JOIN item i ON i.id = e.item_id WHERE e.id=?",
                    (str(key),)).fetchone()
                if not row:
                    return None, None, None, None, None
                # Our number, the catalogue's. find_subtitles asks for the number the
                # file's name carries as well, where the two differ: subtitle sites
                # number some series the catalogue's way and some the release's, and
                # the episode's title in a subtitle's name decides which is which.
                # Adding the shift here and again there asked for the file's number
                # alone - S04E10, another episode by the catalogue.
                return (src, row["title"], row["year"], row["season"], row["number"])
            row = con.execute("SELECT title, year FROM item WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return None, None, None, None, None
            return src, row["title"], row["year"], None, None
        except (TypeError, ValueError):
            return None, None, None, None, None
        finally:
            con.close()

    def season_titles(self, key):
        """The words of each other episode's title in this episode's season."""
        if not str(key).startswith("e"):
            return []
        con = S.local().lib.db()
        try:
            here = con.execute("SELECT item_id, season FROM episode WHERE id=?",
                               (str(key),)).fetchone()
            if not here:
                return []
            return [self._words(r["title"] or "") for r in con.execute(
                "SELECT title FROM episode WHERE item_id=? AND season=? AND id<>?",
                (here["item_id"], here["season"], str(key)))]
        except (TypeError, ValueError):
            return []
        finally:
            con.close()

    def imdb_for(self, key):
        """The IMDb number of the title behind this key - the film, or the series."""
        con = S.local().lib.db()
        try:
            if str(key).startswith("e"):
                row = con.execute(
                    "SELECT i.imdb_id FROM episode e JOIN item i ON i.id = e.item_id "
                    "WHERE e.id=?", (str(key),)).fetchone()
            else:
                row = con.execute("SELECT imdb_id FROM item WHERE id=?",
                                  (str(key),)).fetchone()
            return (row["imdb_id"] or "") if row else ""
        except (TypeError, ValueError):
            return ""
        finally:
            con.close()

    def whole_episode(self, video, sides, lang):
        """Two subtitle files for one double episode, joined into one.

        Two programmes in one file are subtitled as two files, each timed from its own
        zero. Whichever is chosen, half the episode has no subtitles and the other half
        has them at the wrong time - which looks like the subtitle simply stopping.

        The join is found by looking for the black frames between the two parts, and
        remembered, because looking costs a few seconds of ffmpeg. Anything that does
        not look plainly like a split episode is left alone: this returns nothing and
        the file is served as it is.
        """
        wanted = [s for s in sides
                  if (s.get("lang") or "und") == lang or lang == "und"]
        if len(wanted) < 2 or len(wanted) > 4:
            return None
        texts, spans = [], []
        for s in wanted:
            text = S.one_language(S.as_vtt(s["file"]), lang)
            span = S.vtt_span(text)
            if not span:
                return None
            texts.append(text)
            spans.append(span)
        length = S.film_length(video)
        if length < 60:
            return None
        # every part must start near its own beginning and cover a fraction of the
        # film, and together they must come to roughly the whole of it
        if any(a > 120 for a, _ in spans):
            return None
        if any(b > length * 0.8 for _, b in spans):
            return None
        covered = sum(b - a for a, b in spans)
        if not (length * 0.55 < covered < length * 1.05):
            return None
        held = (self.settings_file() or {}).get("splitSubs") or {}
        mark = video + "|" + str(int(len(texts)))
        offsets = held.get(mark)
        if not isinstance(offsets, list) or len(offsets) != len(texts):
            offsets, at = [0.0], spans[0][1]
            for i in range(1, len(texts)):
                join = S.black_after(video, at)
                if not join:
                    return None               # nothing found: do not guess
                offsets.append(join)
                at = join + spans[i][1]
            stored = self.settings_file()
            held = stored.get("splitSubs") or {}
            held[mark] = offsets
            stored["splitSubs"] = held
            S.write_settings(stored)
        out = []
        for text, offset in zip(texts, offsets):
            body = S.shift_vtt(text, float(offset))
            out.append(re.sub(r"^WEBVTT\s*", "", body).strip())
        gap = chr(10) + chr(10)
        return "WEBVTT" + gap + gap.join(b for b in out if b) + chr(10)

    def serve_sidecar(self, video, n, shift=0.0, offset=0.0):
        """The nth subtitle file beside a video, as WebVTT - the one form every
        player accepts, browser and television alike.

        A shift moves every cue by that many seconds, for a subtitle cut to a different
        release: the browser can do this as it draws, but a player being handed a file
        needs the file itself to be right.
        """
        try:
            import pd_localapi as _la
            side = _la.sidecars(video)[n]
            with open(side["file"], "rb") as f:
                raw = f.read()
        except Exception:
            self.send_error(404, "no such subtitle file")
            return
        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        # a file with two languages in it draws both at once, one line under the
        # other: keep the one it says it is
        said = S.one_language(S.srt_to_vtt(text or ""), side.get("lang") or "en")
        # two files for one double episode are served as one, in step with the picture,
        # rather than half an episode of subtitles and then nothing
        try:
            whole = self.whole_episode(video, _la.sidecars(video),
                                       side.get("lang") or "und")
        except Exception:
            whole = None
        if whole:
            said = whole
        # a subtitle that drifts is served already straightened: a client holds one
        # number, and one number cannot describe a drift. This comes after the joining,
        # which builds its text from the files afresh - applying it first threw it away.
        fitted = self.sub_fit(side["file"])
        if fitted:
            said = S.mend_vtt(said, fitted)
        # the shift is about this release; the offset is about where the stream
        # being watched begins
        body = S.mend_garbled(S.cut_vtt(S.shift_vtt(said, shift), offset)).encode("utf-8")
        if not body.strip():
            self.send_error(404, "that subtitle file is empty")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Still being written: the machine is listening to the film and the file grows
        # as it goes. Both clients know this header - it is what they are told while a
        # track is being lifted out of a container - and they ask again as the picture
        # nears the end of what they hold, so a subtitle can be watched from the start
        # while the rest of it is still being made.
        if self.being_written(side.get("file", "")):
            self.send_header("X-Palladium-Subs", "partial")
            self.send_header("X-Palladium-Subs-Until", str(int(S.last_cue(body))))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def being_written(path):
        """Whether a subtitle file is the one a job is writing at this moment."""
        if not path:
            return False
        try:
            import pd_ai_subs
            on = pd_ai_subs.look().get("on") or {}
            return os.path.normcase(on.get("out", "")) == os.path.normcase(path)
        except Exception:
            return False

    def subtitle_text(self, video, index, key="", mi=0, why=""):
        """One subtitle as WebVTT, wherever it lives - beside the film or inside it."""
        if index < 0:
            import pd_localapi as _la
            side = _la.sidecars(video)[-index - 1]
            with open(side["file"], "rb") as f:
                raw = f.read()
            text = ""
            for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            said = S.one_language(S.srt_to_vtt(text), side.get("lang") or "en")
            try:
                whole = self.whole_episode(video, _la.sidecars(video),
                                           side.get("lang") or "und")
            except Exception:
                whole = None
            return whole or said
        # Lifting a track out of a film means reading the whole film: a minute and a
        # half for a UHD disc rip. Worth remembering, since placing a second subtitle
        # of the same film would pay it again.
        # the same copy the player is given, kept on disk and pulled once
        return S.pull_subtitle(video, index, why=why)

    def hand_subs_over(self, key, want):
        """Ask the server this one follows to write the subtitle instead.

        Returns its answer, or None if there is nobody to ask. The film is named by
        what it is rather than by a number, because the other machine files it under
        its own; and the file it writes lands beside its own copy, which is where
        this machine fetches everything else from.
        """
        one = (S.read_settings() or {}).get("follow") or {}
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        con = S.local().lib.db()
        try:
            what = S.local().what_it_is(con, key)
        finally:
            con.close()
        if not what:
            return None
        try:
            where = one["master"].rstrip("/")
            # by key first: both machines derive the same key from title and year,
            # while names differ because a follower names files from their filenames
            theirs = ""
            try:
                with urllib.request.urlopen(
                        where + "/local/library/metadata/" + urllib.parse.quote(str(key))
                        + "?t=" + urllib.parse.quote(one["key"]), timeout=20) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace"))
                if ((said.get("MediaContainer") or {}).get("Metadata") or []):
                    theirs = str(key)
            except Exception:
                theirs = ""
            if theirs:
                found = {}
            else:
                ask = urllib.parse.urlencode(
                    {k: v for k, v in what.items() if v not in (None, "")})
                with urllib.request.urlopen(
                        where + "/local/library/find?" + ask + "&t=" +
                        urllib.parse.quote(one["key"]), timeout=20) as answer:
                    found = json.loads(answer.read().decode("utf-8", "replace"))
                theirs = ((found.get("MediaContainer") or {}).get("key") or "")
            if not theirs:
                return None
            req = urllib.request.Request(
                where + "/subs/make?t=" + urllib.parse.quote(one["key"]),
                data=json.dumps({"key": theirs, "language": want}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as answer:
                said = json.loads(answer.read().decode("utf-8", "replace"))
        except Exception as e:
            return {"error": "Asked the main server server and it did not answer: " +
                             str(e)[:90]}
        said["handed"] = True
        said["where"] = one["master"]
        return said

    def sync_subtitle(self, key, mi=0, index=0, skey="", sub="", save=True, why=""):
        """Put this subtitle in step with the film, by listening to the film.

        Returns what it worked out and whether it was sure enough to write down. A
        subtitle that belongs to another release comes back with an offset; one that
        belongs to another film comes back saying so, and nothing is written.
        """
        import pd_sync
        found = S.local().file_for(key, mi) or {}
        video = found.get("file")
        if not video or not os.path.exists(video):
            return {"error": "that film is not here"}
        # A subtitle still being written from this film's sound covers the first
        # stretch and no more. Sliding that against the whole film finds agreement
        # where there is none - one such measurement put a film sixty seconds out.
        # Finished, it can be measured like any other.
        try:
            import pd_localapi as _la
            if index < 0:
                beside = _la.sidecars(video)
                mine = beside[-index - 1] if -index - 1 < len(beside) else None
                if mine and "ai-gen" in mine["file"]:
                    length = float((S.local().file_for(key, mi) or {}).get("duration") or 0)
                    reaches = _la.sidecar_end(mine["file"])
                    if self.being_written(mine["file"]) or (
                            length and reaches and reaches < length * 0.8):
                        return {"error": "This subtitle is still being written from "
                                         "the film's sound. It can be put in step "
                                         "once it is finished.",
                                "offset": 0.0, "rate": 1.0, "parts": [],
                                "kind": "none", "sure": False, "saved": False}
        except Exception:
            pass
        try:
            vtt = self.subtitle_text(video, index, key, mi, why)
        except IndexError:
            # the files beside the video have changed under this request: one was
            # written while the film was playing, and every place after it moved
            return {"error": "that subtitle has moved - open the list again"}
        except Exception as e:
            return {"error": "could not read that subtitle: %s" % e}
        if not vtt or vtt.count("-->") < 20:
            return {"error": "that subtitle has too little in it to place"}
        mark = video + "|sweep|" + str(os.path.getmtime(video))
        with self.HEARD_LOCK:
            heard = self.HEARD.get(mark)
        if heard is None:
            # several windows across the whole film: one answer says how late the
            # subtitle is, several say whether it stays that way
            heard = pd_sync.sweep(video)
            with self.HEARD_LOCK:
                if len(self.HEARD) > 8:
                    self.HEARD.clear()      # a handful of films, not a memory leak
                self.HEARD[mark] = heard
        if not heard:
            return {"error": "could not listen to that film"}
        length = pd_sync.film_length(video)
        seen = pd_sync.profile(heard, vtt)
        parts, sure = pd_sync.plan(
            seen, length, corroborate=lambda by: pd_sync.seconded(heard, vtt, by))
        if not parts:
            # No window was sure on its own. They may still agree with each other: take
            # the best few peaks from each and look for a line that most of them sit on.
            # A subtitle drifting through a film gives exactly that, and no single
            # window can see it.
            offers = pd_sync.offered(heard, vtt)
            line = pd_sync.through(offers)
            if line:
                rate, base, support, weight = line
                sure = round(min(0.9, (support / float(max(1, len(offers))))
                                 * (weight / support) * 3), 3)
                # A line can be drawn through any scatter of peaks, and a subtitle cut
                # for another edit gives nothing but scatter: windows saying 23, 28,
                # -95, 26, 41, 53 seconds, none of them matching more than a tenth of
                # what was heard. That was being fitted and written down as a drift of
                # half a per cent, which is a correction nobody asked for on a file
                # that simply does not belong to this cut. The windows themselves have
                # to have heard something.
                heard_well = [w[3] for w in seen if w[3] > 0]
                if not heard_well or (sum(heard_well) / len(heard_well)) < 0.2:
                    line, sure = None, 0.0
                else:
                    parts = [(0.0, rate, base)]
        if not parts:
            # Rather than give up, listen in
            # more places for less time at each: eleven windows of three minutes find a
            # signal where five of four minutes could not, and give any answer more
            # chances to be seconded.
            close = video + "|sweep11|" + str(os.path.getmtime(video))
            with self.HEARD_LOCK:
                denser = self.HEARD.get(close)
            if denser is None:
                denser = pd_sync.sweep(video, length, count=11, span=180)
                with self.HEARD_LOCK:
                    if len(self.HEARD) > 8:
                        self.HEARD.clear()
                    self.HEARD[close] = denser
            if denser:
                closer = pd_sync.profile(denser, vtt)
                found, how = pd_sync.plan(
                    closer, length,
                    corroborate=lambda by: pd_sync.seconded(denser, vtt, by))
                if not found:
                    offers = pd_sync.offered(denser, vtt)
                    line = pd_sync.through(offers)
                    if line:
                        rate, base, support, weight = line
                        how = round(min(0.9, (support / float(max(1, len(offers))))
                                        * (weight / support) * 3), 3)
                        # same guard as above: only fit a line when the windows
                        # behind it matched something
                        strong = [w[3] for w in closer if w[3] > 0]
                        if strong and (sum(strong) / len(strong)) >= 0.2:
                            found = [(0.0, rate, base)]
                        else:
                            found, how = [], 0.0
                if found:
                    heard, seen, parts, sure = denser, closer, found, how
        if not parts:
            # Nothing could be placed. That is what a subtitle made at another
            # framerate looks like - four per cent adrift is ten seconds inside a
            # four-minute window, so every window reads as noise - and there are only
            # a handful of ratios anybody ships, so they are tried by name.
            found = pd_sync.rated(heard, vtt)
            if found and abs(found[0] - 1.0) > 1e-9:
                parts, sure = [(0.0, found[0], found[1])], found[2]
        if len(parts) > 1:
            # the sweep only says the step is somewhere in the gap between two windows;
            # listening inside that gap says where, to under a minute
            tight = [parts[0]]
            for i in range(1, len(parts)):
                before, after = parts[i - 1][2], parts[i][2]
                # the last window that ended before the step, and the first that
                # began after it: the change is somewhere between those two
                lo = max([w[1] for w in seen if w[1] <= parts[i][0]]
                         or [parts[i][0] - 300])
                hi = min([w[0] for w in seen if w[0] >= parts[i][0]]
                         or [parts[i][0] + 300])
                at = pd_sync.pin_step(video, vtt, lo, hi, before, after)
                tight.append((at, parts[i][1], parts[i][2]))
            parts = tight
        # a rate that moves the end of the film by a fraction of a second is a flat
        # line with rounding on it: say so plainly rather than calling it a drift
        parts = [(a, 1.0 if pd_sync.flat_enough(r, length) else r, sh)
                 for a, r, sh in parts]
        offset = parts[0][2] if parts else 0.0
        out = {"offset": offset, "rate": parts[0][1] if parts else 1.0,
               "parts": [[a, r, sh] for a, r, sh in parts],
               "kind": ("parts" if len(parts) > 1
                        else "drift" if parts and abs(parts[0][1] - 1.0) > 1e-9
                        else "static"),
               "drift": len(parts) > 1,
               "windows": [[round(a), round(b), off, s] for a, b, off, s in seen],
               "confidence": sure, "sure": bool(parts) and sure >= pd_sync.SURE_ENOUGH,
               "saved": False}
        if not out["sure"]:
            # Say which way it failed, and where it can be shown rather than guessed.
            # Another subtitle beside the same film, cut for this release, settles it:
            # a file that is merely late says the same number all the way through.
            proof = None
            try:
                proof = self.another_cut(video, index)
            except Exception:
                proof = None
            if proof and proof.get("another_cut"):
                out["why"] = (
                    "this is a subtitle for another cut of the episode. Against \"%s\", "
                    "which matches this file, %d lines in common are out by anything "
                    "from %+.0f to %+.0f seconds - so no single correction can fit it. "
                    "Use that one instead."
                    % (proof["against"], proof["lines"],
                       proof["middle"] - proof["spread"] / 2.0,
                       proof["middle"] + proof["spread"] / 2.0))
                out["another_cut"] = proof
            elif proof:
                out["why"] = (
                    "the film could not be listened to well enough to be sure, but "
                    "against \"%s\" this file is steady at %+.1f seconds - that is "
                    "probably the correction it wants."
                    % (proof["against"], proof["middle"]))
                out["against"] = proof
            else:
                # the one named after this very release has nothing better to be
                # compared against - it is the thing others are compared against
                own = None
                try:
                    import pd_localapi as _la
                    beside = _la.sidecars(video)
                    fits = self.best_beside(video)
                    own = (index < 0 and fits and beside and
                           beside[-index - 1].get("file") == fits.get("file"))
                except Exception:
                    own = None
                if own:
                    out["why"] = ("this is the subtitle cut for the very release this "
                                  "file is - the film itself is too talkative to line "
                                  "anything up against, but this is the one to use")
                    out["release_match"] = True
                else:
                    out["why"] = ("this subtitle does not line up with the film "
                                  "anywhere in particular - it may belong to another "
                                  "release")
            return out
        if save and out["sure"]:
            shelf = skey or ("l" + str(key))
            name = self.fit_name(video, index)
            # The correction lives in the file the server hands out whenever it is more
            # than one number, and in the viewer's own shift when it is exactly one -
            # so a plain "two seconds late" is still a number anybody can see and undo.
            # Either way, whatever was nudged by hand was measured against the old
            # timing and no longer applies.
            # A rate is a plan whether or not it comes in parts: stored as a plain
            # number, the rate would be thrown away and the drift come back.
            if len(parts) > 1 or abs(parts[0][1] - 1.0) > 1e-9:
                self.set_sub_fit(name, parts)
                self.set_sub_shift(shelf, 0.0, sub)
                out["offset"] = 0.0
            else:
                self.set_sub_fit(name, [])
                self.set_sub_shift(shelf, offset, sub, how="sync")
            out["saved"] = True
        return out

    def find_subtitles(self, key, language, after=None):
        """Subtitles on offer for one title, in the order they are tried, the one
        fetching ahead takes marked autoPick: the list and the automatic fetch are this
        one function. `after` is the episode watched before, else the one before it."""
        client, why = self.opensubtitles()
        if not client:
            return {"error": why, "results": []}
        src, title, year, season, episode = self._title_for_search(key)
        if not title:
            return {"error": "Nothing here to search for.", "results": []}
        results, err = client.search(title, year, language,
                                     (src or {}).get("file"), season, episode,
                                     imdb=self.imdb_for(key))
        # A season the scanner has renumbered has two numbers for the same episode:
        # ours, and the one the release carries. Subtitle sites are numbered both ways
        # by different uploaders, so both are asked and the answers are put together -
        # the release's own first among candidates that are equally this episode.
        import pd_localapi
        shift = (pd_localapi.LocalAPI.numbering_shift(
            (src or {}).get("file"), season, episode) if episode is not None else 0) or 0
        if shift:
            other, _ = client.search(title, year, language, (src or {}).get("file"),
                                     season, (episode or 0) + shift, imdb=self.imdb_for(key))
            seen = {r.get("id") for r in (results or [])}
            for r in other or []:
                if r.get("id") in seen:
                    continue
                r["shifted"] = True
                results = (results or []) + [r]
                seen.add(r.get("id"))
        if not results and self.imdb_for(key):
            # The id is the better question when it is answered, and no question at all
            # when the two databases disagree about which film this is - a remake takes
            # the name and the old id keeps the subtitles. Ask by name before giving up.
            byname, err2 = client.search(title, year, language,
                                         (src or {}).get("file"), season, episode)
            if byname:
                results, err = byname, ""
            elif not err:
                err = err2
        # Numbering is not agreed between databases: one series' E31 was our E32 for
        # a whole season, so each episode carried the next one's title
        # sitting one place out. So when nothing the numbered search returned mentions
        # this episode by name, the neighbours are asked as well - the right subtitle
        # is usually one number away, and it can be recognised by its title.
        here = self.episode_facts(key)
        own = (here.get("title") or "").strip()
        mine_words = self._words(own)

        def about_this_episode(r):
            words = self._words(r.get("name") or "")
            enough = (len(mine_words) if len(mine_words) <= 2
                      else max(2, (len(mine_words) + 1) // 2))
            return bool(mine_words) and len(mine_words & words) >= enough

        # Asked by the release's own number, an answer is this episode only when it says
        # so: a pack in DVD order called its S02E09 what was broadcast as S02E07, and the
        # S02E09 that came back was another episode's subtitle (2026-11-11).
        if own:
            results = [r for r in (results or [])
                       if not r.get("shifted") or about_this_episode(r)]

        if own and season is not None and episode is not None and                 not any(about_this_episode(r) for r in (results or [])):
            seen = {r.get("id") for r in (results or [])}
            for nudge in (-1, 1, -2, 2):
                if episode + nudge < 1:
                    continue
                near, _ = client.search(title, year, language,
                                        (src or {}).get("file"), season,
                                        episode + nudge, imdb=self.imdb_for(key))
                keep = [r for r in (near or [])
                        if r.get("id") not in seen and about_this_episode(r)]
                if keep:
                    results = (results or []) + keep
                    seen |= {r.get("id") for r in keep}
                    break
        if own and season is not None:
            named, _ = client.search("%s %s" % (title, own), year, language,
                                     (src or {}).get("file"), None, None)
            if named:
                # A search by name has no season to hold it down, so a title and
                # an episode name together also find unrelated programmes.
                # Only what mentions this programme is worth adding.
                show_words = self._words(title)
                seen = {r.get("id") for r in (results or [])}
                results = (results or []) + [
                    r for r in named
                    if r.get("id") not in seen
                    and len(show_words & self._words(r.get("name") or "")) >=
                        max(1, (len(show_words) + 1) // 2)]
        # ordered by how likely each is to be in time, so the first one offered is the
        # first one worth trying
        if results:
            # the release fetching ahead looks for: the series' remembered one, else
            # what the episode before was tried with
            want = self.auto_want(key, self.prev_of(key) if after is None else after,
                                  language)
            release = (want or {}).get("release") or ""
            results = self.rank_subtitles(results, release,
                                          here.get("title", ""), here.get("season"),
                                          here.get("release", here.get("number")),
                                          (src or {}).get("file", ""),
                                          self.fitting_families(key, language),
                                          self.source_wanted(key, release))
            # and each one says whether it is the release this episode was proved on -
            # the same episode, so the names match outright rather than by family
            proved = (self.confirmed_release(key, language) or "").strip().lower()
            # whether this candidate is even for this episode. Judged by the episode's
            # own title, because that is the thing two databases agree on when their
            # numbering does not.
            mine = self._words(own)
            for r in results:
                name = (r.get("name") or "").strip().lower()
                r["confirmed"] = bool(proved) and (
                    name == proved or name.rstrip(".srt") == proved.rstrip(".srt"))
                # This episode, or another one. A short title has to appear whole:
                # "Diary" matched Diary of a Wimpy Kid on one word out of one, which
                # is how a film ends up offered as an episode of a cartoon.
                words = self._words(name)
                enough = len(mine) if len(mine) <= 2 else max(2, (len(mine) + 1) // 2)
                # A number in the name settles it before any words do. Both halves
                # of a two-parter carry the same title, so the second half passed the
                # word test for the first and - once the release decided the order -
                # led the list. A name that says which episode it is, and says a
                # different one, is not this episode whatever it is called.
                said = re.search(r"s(\d{1,2})[\s._-]?e(\d{1,3})|(\d{1,2})x(\d{1,3})",
                                 name)
                if said and season is not None and episode is not None:
                    got = ((int(said.group(1)), int(said.group(2))) if said.group(1)
                           else (int(said.group(3)), int(said.group(4))))
                    # either number of this episode: the catalogue's or the file's
                    if not pd_localapi.number_is_ours(got, season, episode, shift):
                        r["episode"] = False
                        continue
                r["episode"] = (bool(mine)
                                and len(mine & words) >= enough
                                and len(self._words(title) & words) >=
                                    max(1, (len(self._words(title)) + 1) // 2))
            # what this file is called, and which of these are named after it
            common = self.title_words((src or {}).get("file") or "")
            mine = (src or {}).get("file") or ""
            for r in results:
                # named after this very file: whoever made it had this release open
                r["sameName"] = self.same_name(mine, r.get("name") or "")
                # A hash match whose name is for a different release. The moviehash
                # is filesize plus the first and last 64 kB, attached by the uploader,
                # so it is wrong often enough to matter: a DVDRip and a one-word
                # upload both claimed a 1080p BluRay. Still listed, marked, and ranked
                # below candidates whose name agrees with the release.
                # A hash match is doubted when its name is for another release -
                # but a name carrying this episode's own title is not another
                # release, whatever else it shares. Subtitle sites number and name
                # episodes their own way, and a name that says which episode it is
                # can share not one word with the file it belongs to.
                names_it = bool(mine_words) and len(
                    mine_words & self._words(r.get("name") or "")) >= max(
                    1, (len(mine_words) + 1) // 2)
                r["hashOdd"] = (bool(r.get("fromHash")) and not r["sameName"]
                                and not names_it
                                and self.release_agrees(
                                    mine, r.get("name") or "", common) < 2)
            # Being the right episode outranks everything. A subtitle cut to this
            # very release is worthless if it is a different episode, which is what
            # the numbered search offers first when the databases disagree.
            #
            # A hash match is the right episode by definition: it was uploaded against
            # a file byte for byte the same as this one. It used to be demoted here
            # for not spelling the episode's title out in its name, which release
            # names rarely do.
            for r in results:
                if r.get("fromHash") and not r.get("hashOdd"):
                    r["episode"] = True
            # and nothing named after another episode of this season is this one,
            # hash or no hash: a subtitle for S04E10 The Chair Model was taken for
            # Branch Wars, whose file carries the number 10
            others = self.season_titles(key)
            for r in results:
                if pd_localapi.names_another(self._words(r.get("name") or ""),
                                             self._words(own), others):
                    r["episode"] = False
                    if r.get("fromHash"):
                        r["hashOdd"] = True
            # already beside the film: fetched before under this release name
            import pd_localapi
            names = pd_localapi._release_names()
            bare = lambda t: re.sub(r"[^a-z0-9]", "",
                                    re.sub(r"(?i)\.srt$", "", str(t or "")).lower())
            held = {bare(names.get(side["file"]) or "") for side in
                    pd_localapi.sidecars(mine)} - {""}
            for r in results:
                r["onDisk"] = bare(r.get("name")) in held
            # and inside that, the release: the ranked order stands within each tier
            results = pd_localapi.offer_order(results)
            chosen = S.auto_pick(results, str(key).startswith("e"))
            if chosen is not None:
                chosen["autoPick"] = True
            fitting = sum(1 for r in results if r.get("episode"))
            if own and not fitting:
                # every result is for some other episode: worth saying before somebody
                # downloads one and watches an episode out of step
                err = (err or "") + ("These all look like a different episode - "
                                     "nothing here mentions \"%s\". The numbering at "
                                     "OpenSubtitles may not match this library."
                                     % own)
        return {"results": results, "error": err, "title": title}

    def episode_facts(self, key):
        """An episode's own title and numbering, for judging a subtitle by.

        Two numbers: ours, and the one the releases use. A candidate's name carries
        the second, so that is what its "s04e29" has to be read against.
        """
        if not str(key).startswith("e"):
            return {}
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT title, season, number FROM episode WHERE id=?",
                              (str(key),)).fetchone()
            if not row:
                return {}
            said = dict(row)
            import pd_localapi
            src = S.local().file_for(key, 0) or {}
            said["release"] = said["number"] + (pd_localapi.LocalAPI.numbering_shift(
                src.get("file"), said["season"], said["number"]) or 0)
            return said
        except (TypeError, ValueError):
            return {}
        finally:
            con.close()

    # words in every release name, which say nothing about which release it is
    COMMON_WORDS = {"the", "web", "dvd", "bluray", "brrip", "webrip", "hdtv", "srt",
                    "eng", "english", "subs", "subtitles", "season", "episode"}

    def same_family(self, one, two, show_title="", episode_title=""):
        """Are these two names the same release, one episode apart?

        What they share, once the show, the episode and the words every release uses
        are set aside, is the group and the encode - and that is what carries timing
        from one episode to the next.
        """
        if not one or not two:
            return False
        aside = (self._words(show_title) | self._words(episode_title)
                 | self.COMMON_WORDS)
        left = (self._words(one) & self._words(two)) - aside
        return len(left) >= 2

    def confirmed_release(self, key, language="en"):
        """The release verified for this title in one language, if any.

        Per title and per language: subtitles are cut to a particular file, the episode
        after this one is frequently somebody else's rip, and an English subtitle says
        nothing about the Swedish one.
        """
        marked = self.verified_for(
            (self.settings_file().get("subsOk", {}) or {}).get(str(key)))
        row = marked.get(language) or marked.get((language or "")[:2]) or {}
        return row.get("release", "") if isinstance(row, dict) else (row or "")

    def names_in(self, key):
        """Who is in a title and who they play, guest stars too: the spellings a subtitle
        made from the sound should use."""
        names = []
        con = S.local().lib.db()
        try:
            ep = (con.execute("SELECT item_id, season, number FROM episode WHERE id=?",
                              (str(key),)).fetchone() if str(key).startswith("e") else None)
            item = str(ep["item_id"]) if ep else str(key)
            for r in con.execute("SELECT name, role FROM credit WHERE item_id=? "
                                 "ORDER BY ord LIMIT 30", (item,)):
                names += [r["role"] or "", r["name"] or ""]
            row = con.execute("SELECT tmdb_id FROM item WHERE id=?", (item,)).fetchone()
        except Exception:
            ep, row = None, None
        finally:
            con.close()
        if ep and row and row["tmdb_id"]:
            try:
                said = S.local().lib.tmdb("/tv/%s/season/%s/episode/%s" % (
                    row["tmdb_id"], ep["season"], ep["number"])) or {}
                for g in (said.get("guest_stars") or [])[:30]:
                    names += [g.get("character") or "", g.get("name") or ""]
            except Exception:
                pass
        out = []
        for n in names:
            for part in re.split(r"\s*/\s*|\s*\(", str(n)):
                part = part.strip(" )'\"")
                if part and part not in out and "voice" not in part.lower():
                    out.append(part)
        return out[:80]

    def series_choice(self, show, stored=None):
        """This viewer's subtitle choice for a series. Kept per viewer; the house-wide one
        from before counts only when it is in this viewer's main language."""
        stored = self.settings_file() if stored is None else stored
        mine = self.viewer_settings(stored)
        chosen = (mine.get("subsFor", {}) or {}).get(show)
        if chosen:
            return chosen
        house = (stored.get("subsFor", {}) or {}).get(show)
        if house and str(house.get("language") or "en")[:2] == self.viewer_language()[:2]:
            return house
        return None

    def prev_of(self, key):
        """The episode before this one, or ""."""
        if not str(key).startswith("e"):
            return ""
        con = S.local().lib.db()
        try:
            return S.local().prev_episode_key(con, key) or ""
        except Exception:
            return ""
        finally:
            con.close()

    def auto_want(self, key, after, language=None, stored=None):
        """What fetching ahead looks for on a title: the series' remembered choice,
        else what the episode before was tried with - within the same programme only;
        in a shuffle the last episode says nothing about the next film. None when
        neither, or when `language` is given and the choice is in another."""
        stored = self.settings_file() if stored is None else stored
        show = self.show_of(key) if str(key).startswith("e") else None
        want = self.series_choice(show or str(key), stored)
        if not want and after and show and self.show_of(after) == show:
            want = (self.viewer_settings(stored).get("subsTried", {}) or {}).get(str(after))
        if want and language and str(want.get("language") or "")[:2] != str(language)[:2]:
            return None
        return want

    def series_release(self, key):
        """The release this series has settled on, if it has settled on one."""
        show = self.show_of(key) if str(key).startswith("e") else None
        if not show:
            return ""
        chosen = self.series_choice(show) or {}
        return chosen.get("release", "")

    def fetch_subtitle(self, key, file_id, language, remember, release="", pick=True,
                       downloads=0):
        client, why = self.opensubtitles()
        if not client:
            return {"ok": False, "error": why}
        src, title, year, season, episode = self._title_for_search(key)
        if not src:
            return {"ok": False, "error": "No file behind that."}
        text, err = client.download(file_id)
        if not text:
            return {"ok": False, "error": err}
        text = self.half_of_double(key, text, release)
        import pd_subs
        # The same file may already be there under another name: fetched automatically
        # for the next episode, and then asked for by hand while watching it. Two
        # identical files beside one video are two entries in the menu that do the same
        # thing, and nobody can tell which is which.
        same = self.same_subtitle_beside(src["file"], text)
        if same:
            self.note_downloads(same, downloads)
            if remember:
                self.note_subtitle_attempt(key, language, release)
            if release and pick:
                self.remember_pick(key, release)
            return {"ok": True, "file": os.path.basename(same), "already": True}
        # named for the release, so trying another variant keeps this one
        out = pd_subs.sidecar_path(src["file"], language, release)
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            return {"ok": False, "error": "Could not write beside the film: %s" % e}
        self.note_downloads(out, downloads)
        # recorded as an attempt only: it becomes the series' choice if this episode
        # is watched to the end with it, which is the only evidence that it fits
        if remember:
            self.note_subtitle_attempt(key, language, release)
        # taking a subtitle is choosing it: it leads the list from now on
        if release and pick:
            self.remember_pick(key, release)
        if release:
            # the file is named for the film, so the release name would otherwise be
            # lost - and it is the only thing that tells one variant from another
            stored = S.read_settings()
            names = stored.setdefault("subsRelease", {})
            names[out] = release
            if len(names) > 400:                 # a cap, so it cannot grow forever
                for old in list(names)[:100]:
                    names.pop(old, None)
            S.write_settings(stored)
        S.note_download()
        return {"ok": True, "file": os.path.basename(out)}

    def half_of_double(self, key, text, release=""):
        """A subtitle cut for the double episode this file is one half of: only its half,
        on its own clock. Anything else is handed back as it came."""
        import pd_vtt
        if not str(key).startswith("e"):
            return text
        con = S.local().lib.db()
        try:
            def length(k):
                row = con.execute("SELECT MAX(duration) AS d FROM file WHERE episode_id=?",
                                  (str(k),)).fetchone() if k else None
                return float((row["d"] if row else 0) or 0)
            runtime = length(key)
            before = length(S.local().prev_episode_key(con, key))
            after = length(S.local().next_episode_key(con, key))
            row = con.execute("SELECT number FROM episode WHERE id=?", (str(key),)).fetchone()
            number = int(row["number"] or 0) if row else 0
        except Exception:
            return text
        finally:
            con.close()
        end = pd_vtt.srt_end(text)
        if not runtime or end <= runtime * 1.08 + 60:
            return text
        part = pd_vtt.double_part(end, runtime, before, after, release, number)
        if not part:
            return text
        # a subtitle beside it that fits this file, any language, places the half
        import pd_localapi
        ref, most = "", 0
        for side in pd_localapi.sidecars(str((S.local().file_for(key, 0) or {}).get("file") or "")):
            if pd_localapi.far_too_long(side["file"], runtime):
                continue
            try:
                with open(side["file"], encoding="utf-8", errors="replace") as f:
                    said = f.read()
            except OSError:
                continue
            if said.count("-->") > most:
                ref, most = said, said.count("-->")
        half = pd_vtt.split_double(text, part[0], part[1], runtime, ref)
        if not half:
            return text
        try:
            pd_log.raw("%s subtitles %s: cut for the double episode, its %s half kept%s"
                    % (time.strftime("%H:%M:%S"), key, part[0], chr(10)))
        except OSError:
            pass
        return half

    @staticmethod
    def note_downloads(path, downloads):
        """How many had downloaded a subtitle where it came from: the order the list of
        files beside a film is read in."""
        try:
            count = int(downloads or 0)
        except (TypeError, ValueError):
            count = 0
        if not path or count <= 0:
            return
        stored = S.read_settings()
        counts = stored.setdefault("subsDownloads", {})
        counts[path] = count
        if len(counts) > 2000:
            for old in list(counts)[:500]:
                counts.pop(old, None)
        S.write_settings(stored)

    @staticmethod
    def same_subtitle_beside(video, text):
        """A file beside this video holding exactly this, or nothing.

        Compared on what is in them rather than on their names: the same subtitle
        arrives named for the release when it is asked for by hand and named for the
        film when it is fetched by itself.
        """
        import pd_localapi
        want = (text or "").replace(chr(13) + chr(10), chr(10)).strip()
        if not want:
            return ""
        for side in pd_localapi.sidecars(video):
            where = side.get("file") or ""
            try:
                with open(where, encoding="utf-8", errors="replace") as f:
                    if f.read().replace(chr(13) + chr(10), chr(10)).strip() == want:
                        return where
            except OSError:
                continue
        return ""

    def show_of(self, episode_key):
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT item_id FROM episode WHERE id=?",
                              (str(episode_key),)).fetchone()
            return str(row["item_id"]) if row else None
        except (TypeError, ValueError):
            return None
        finally:
            con.close()

    @staticmethod
    def verified_for(record):
        """A title's verified subtitles as a map by language, whatever shape it is in."""
        record = record or {}
        if "release" in record:                 # the older single-record shape
            return {(record.get("language") or "en"): {"release": record["release"]}}
        return {k: v for k, v in record.items()}

    def verify_subtitle(self, key, name, language="en", index=None, how=""):
        """Mark one subtitle as the right one for this title, by hand - or unmark it.

        The same record an episode watched to the end would have written: a statement
        about this file's timing, which a person watching knows in seconds. An empty
        name takes the statement back, because the wrong file vouched for once would
        otherwise lead the list for ever.
        """
        if not key:
            return {"ok": False, "error": "Nothing named."}
        if name and index is None:
            # only a subtitle that is actually beside the film can be vouched for
            import pd_localapi
            src = S.local().file_for(key, 0) or {}
            beside = [x["name"] for x in pd_localapi.sidecars(src.get("file") or "")]
            if name not in beside:
                return {"ok": False,
                        "error": "That subtitle is not beside the film any more."}
        stored = self.settings_file()
        done = stored.get("subsOk", {}) or {}
        done[str(key)] = self.verified_for(done.get(str(key)))
        if index is not None:
            # a track inside the film: its stream number is what identifies it, since
            # the name in a container is whatever the person who made it typed
            done[str(key)][language] = {"index": index}
        elif name:
            done[str(key)][language] = dict({"release": name}, **({"how": how} if how else {}))
        else:
            done[str(key)].pop(language, None)
            if not done[str(key)]:
                done.pop(str(key), None)
        stored["subsOk"] = done
        # a series keeps the last confirmed release as its guess for what to try next;
        # not the idle check's, which runs every language and would switch the series
        # Only a person's own verify is their choice too: one made by a check - the
        # idle one's "speech", or "check" when subtitles are put in step for somebody
        # else - marks the file and leaves every viewer's choices alone. Checks run for
        # another viewer's series wrote over the owner's choice for his own (2026-11-01)
        show = self.show_of(key) if str(key).startswith("e") else None
        mine = self.viewer_settings(stored) if not how else {}
        if show and not how:
            subs = mine.get("subsFor", {}) or {}
            subs[show] = {"language": language, "release": name, "confirmed": True}
            mine["subsFor"] = subs
        # the series' guess follows the last thing actually verified
        if show and not name and not how:
            (mine.get("subsFor", {}) or {}).pop(show, None)
        S.write_settings(stored, merge=False)
        if name and index is None and not how:
            self.remember_pick(key, name)      # verified is also chosen
        if name and show and how != "speech":
            self.fetch_ahead_of(key)
        return {"ok": True, "verified": name or ("track %d" % index
                                                 if index is not None else "")}

    #: episodes after a verified one that get the series' subtitle fetched
    AHEAD_ON_VERIFY = 2

    #: seconds into an episode at which the next one's subtitle is fetched
    AHEAD_AT_S = 120

    #: episode -> when its next one was last fetched for
    AHEAD_DONE = {}

    def subtitle_ahead(self, key):
        """The next episode's subtitle, fetched by the server for every client, once per
        episode watched; the web and the app each asked for it five minutes from the end."""
        now = time.time()
        if now - S.Handler.AHEAD_DONE.get(str(key), 0) < 6 * 3600:
            return
        S.Handler.AHEAD_DONE[str(key)] = now
        if len(S.Handler.AHEAD_DONE) > 400:
            for old in list(S.Handler.AHEAD_DONE)[:200]:
                S.Handler.AHEAD_DONE.pop(old, None)

        def work():
            con = S.local().lib.db()
            try:
                nxt = S.local().next_episode_key(con, str(key)) or ""
            finally:
                con.close()
            if nxt:
                try:
                    self.auto_subtitle(nxt, str(key))
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def fetch_ahead_of(self, key):
        """The next episodes take the group just verified, so one opened directly
        rather than as the next starts in step."""
        def work():
            at = str(key)
            for _ in range(self.AHEAD_ON_VERIFY):
                con = S.local().lib.db()
                try:
                    at = S.local().next_episode_key(con, at) or ""
                finally:
                    con.close()
                if not at:
                    return
                try:
                    self.auto_subtitle(at)
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def remove_sidecar(self, key, name, mi=0):
        """Delete one subtitle file from beside its video, and forget it."""
        import pd_localapi
        src = S.local().file_for(key, mi) or {}
        video = src.get("file")
        if not video:
            return {"ok": False, "error": "No file behind that."}
        wanted = (name or "").strip().lower()
        for side in pd_localapi.sidecars(video):
            if (side["name"] or "").strip().lower() != wanted:
                continue
            try:
                os.remove(side["file"])
            except OSError as e:
                return {"ok": False, "error": str(e)[:120]}
            stored = self.settings_file()
            (stored.get("subsRelease", {}) or {}).pop(side["file"], None)
            # and any note that pointed at it, so nothing offers it again
            for shelf in ("subsOk", "subsPick"):
                rows = stored.get(shelf, {}) or {}
                for at, value in list(rows.items()):
                    said = value.get("release") if isinstance(value, dict) else value
                    if pd_localapi.same_release(said, wanted):
                        rows.pop(at, None)
                stored[shelf] = rows
            S.write_settings(stored, merge=False)
            return {"ok": True, "removed": os.path.basename(side["file"])}
        return {"ok": False, "error": "No such subtitle beside that film."}

    def hide_track(self, key, index, mi=0, show=False):
        """Stop offering one of a video's own subtitle tracks. Reversible."""
        con = S.local().lib.db()
        try:
            src = S.local().file_for(key, mi) or {}
            row = con.execute("SELECT id FROM file WHERE path=?",
                              (src.get("file") or "",)).fetchone()
        finally:
            con.close()
        if not row:
            return {"ok": False, "error": "No file behind that."}
        stored = self.settings_file()
        shelf = stored.get("subsHidden", {}) or {}
        rows = set(int(n) for n in (shelf.get(str(row["id"])) or []))
        rows.discard(index) if show else rows.add(index)
        if rows:
            shelf[str(row["id"])] = sorted(rows)
        else:
            shelf.pop(str(row["id"]), None)
        stored["subsHidden"] = shelf
        S.write_settings(stored, merge=False)
        return {"ok": True, "hidden": sorted(rows)}

    def remember_pick(self, key, name):
        """Note which subtitle was chosen for a title, for whoever chose it."""
        if not key:
            return
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        picks = mine.get("subsPick", {}) or {}
        if name:
            picks[str(key)] = name
        else:
            picks.pop(str(key), None)          # subtitles off is also a choice
        mine["subsPick"] = picks
        S.write_settings(stored, merge=False)

    def note_subtitle_attempt(self, episode_key, language, release):
        """What is currently being tried on this episode."""
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        tried = mine.get("subsTried", {}) or {}
        tried[str(episode_key)] = {"language": language, "release": release or ""}
        mine["subsTried"] = tried
        S.write_settings(stored, merge=False)

    def subtitle_since(self, key):
        """When the subtitle on screen came on, as last written down.

        The memory of it is lost with the server; the file is not. An episode watched
        across a restart - which is every episode watched while this is being worked on -
        otherwise reported the position of the first request after it and was refused.
        """
        one = (self.settings_file().get("subSince", {}) or {}).get(str(key))
        if not one or len(one) < 2:
            return None
        # the fourth field says whether the position means anything; older rows have
        # none, and those were all written by a first sighting
        sure = bool(one[3]) if len(one) > 3 else False
        return (one[0], float(one[1]) if sure else None)

    def remember_since(self, key, using, at, sure=True):
        stored = self.settings_file()
        kept = stored.get("subSince", {}) or {}
        kept[str(key)] = [using, round(float(at), 1), int(time.time()), bool(sure)]
        # a few evenings' worth; the oldest go first
        if len(kept) > 40:
            for old in sorted(kept, key=lambda k: kept[k][2])[:20]:
                kept.pop(old, None)
        stored["subSince"] = kept
        S.write_settings(stored, merge=False)

    def promote_subtitle_choice(self, episode_key, using="", since=None):
        """The episode was finished: whatever it was watched with is what fits.

        Called as progress is reported, so trying three subtitles and watching the
        third teaches the series about the third and forgets the other two.

        Watching an episode through with subtitles turned off is the same statement in
        reverse - this series does not want them - and it stops the fetching. Unless the
        choice has been confirmed: a subtitle that has carried an episode to the end is
        known to fit, and one quiet evening without it is no reason to lose it.
        """
        stored = self.settings_file()
        mine = self.viewer_settings(stored)
        if using == "off":
            show = self.show_of(episode_key)
            if show:
                subs = mine.get("subsFor", {}) or {}
                chosen = subs.get(show) or {}
                if chosen.get("confirmed"):
                    pass                     # proved itself once; it stays
                elif subs.pop(show, None) is not None:
                    mine["subsFor"] = subs
            (mine.get("subsTried", {}) or {}).pop(str(episode_key), None)
            mine["subsTried"] = mine.get("subsTried", {}) or {}
            S.write_settings(stored, merge=False)
            return
        tried = (mine.get("subsTried", {}) or {})
        want = tried.get(str(episode_key))
        # Some clients name the track they are drawing; the players only say that
        # something is on. Comparing "on" with the name of the file that was fetched
        # said they were different things and refused every automatic verification -
        # which is exactly what it looked like from the sofa.
        named = using not in ("", "on", "off", "none")
        if want and named and want.get("release") and using != want.get("release"):
            # something else was on screen at the end, so the fetched file is not what
            # this watching is evidence about
            want = None
        if not want and named:
            # nothing fetched for this one, or something else was watched with: the
            # track named in the reports is the evidence. Anything can earn the mark -
            # a file that was already beside the film, or one inside it.
            want = self.watched_with(episode_key, using)
        if not want:
            return
        # and it has to have been on for the whole of it. A track chosen with ten
        # minutes to go is not what the last two hours were watched with.
        con = S.local().lib.db()
        try:
            row = con.execute(
                "SELECT position, duration FROM progress WHERE key=? AND who=?",
                (str(episode_key), self.viewer())).fetchone()
        finally:
            con.close()
        length = (row["duration"] if row else 0) or 0
        if since is not None and length and since > 0.3 * length:
            return
        tried_all = tried
        fetched = tried.pop(str(episode_key), None)
        tried = fetched or want
        # a film has no series to teach, but the subtitle it was watched through with
        # is verified just the same
        show = self.show_of(episode_key) if str(episode_key).startswith("e") else None
        # Confirmed by the only evidence there is - this episode, watched through with
        # this subtitle. It is recorded against the episode because that is what it is
        # true of: the next episode may well be somebody else's rip.
        done = stored.get("subsOk", {}) or {}
        done[str(episode_key)] = self.verified_for(done.get(str(episode_key)))
        # a file is known by its name, a track inside the film by its number
        lang = tried.get("language") or "en"
        mark = ({"index": tried["index"]} if tried.get("index") is not None
                else {"release": tried.get("release", "")})
        # Progress is reported every few seconds, and every report after the credits
        # comes through here. The mark is written each time, harmlessly - but the news
        # is the moment it goes from unverified to verified, and nothing after that.
        # Comparing the whole record was not enough: the first report knows the file
        # that was fetched and later ones know the track on screen, so the two forms
        # differed and announced themselves in turn.
        news = lang not in done[str(episode_key)]
        done[str(episode_key)][lang] = mark
        stored["subsOk"] = done
        # The series keeps it as a starting guess for what to fetch next time - only
        # from a file that was fetched, since a stream number means nothing on the next
        # episode's release.
        subs = mine.get("subsFor", {}) or {}
        if show and fetched:
            subs[show] = dict(fetched, confirmed=True)
        mine["subsFor"] = subs
        mine["subsTried"] = tried_all
        S.write_settings(stored, merge=False)
        # what to tell the client, the once: a panel that is open turns its tick green
        # as this happens rather than the next time somebody opens it
        if not news:
            return None
        return dict(mark, key=str(episode_key), language=lang)

    def watched_with(self, key, using):
        """Which track the reports name, as a record to verify - or nothing.

        Clients name a file beside the film by its name and a track inside it as
        "t" and the stream number, which is what the timing corrections are filed
        under too.
        """
        if not using or using in ("off", "none"):
            return None
        src = S.local().file_for(key, 0) or {}
        video = src.get("file") or ""
        if not video:
            return None
        if str(using).startswith("t") and str(using)[1:].isdigit():
            index = str(using)
            con = S.local().lib.db()
            try:
                row = con.execute(
                    "SELECT streams FROM file WHERE path=?", (video,)).fetchone()
                tracks = json.loads((row["streams"] if row else None) or "[]")
            except Exception:
                tracks = []
            finally:
                con.close()
            for t in tracks:
                if t.get("index") == index:
                    return {"language": (t.get("lang") or "en")[:2], "index": index}
            return None
        import pd_localapi
        for side in pd_localapi.sidecars(video):
            if side.get("name") == using:
                return {"language": (side.get("lang") or "en")[:2],
                        "release": side.get("name")}
        return None

    TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")

    def title_words(self, video):
        """The words that name the programme and the episode, rather than the release."""
        con = S.local().lib.db()
        try:
            row = con.execute(
                """SELECT i.title show, e.title episode FROM file f
                   LEFT JOIN item i ON i.id = f.item_id
                   LEFT JOIN episode e ON e.id = f.episode_id
                   LEFT JOIN item si ON si.id = e.item_id
                   WHERE f.path = ?""", (video,)).fetchone()
        except Exception:
            row = None
        finally:
            con.close()
        said = " ".join(x for x in [(row["show"] if row else "") or "",
                                    (row["episode"] if row else "") or ""] if x)
        # the series' own name reaches the file through the episode when the item row
        # is the programme rather than the film
        return self._words(said)

    def picked_for(self, key):
        """What this viewer settled on for this title, if anything."""
        mine = self.viewer_settings(self.settings_file())
        return (mine.get("subsPick", {}) or {}).get(str(key), "")

    @staticmethod
    def bare_name(name):
        """A name with nothing in it but letters and numbers, for comparing two.

        Releases are written with dots, spaces, brackets and dashes at the whim of
        whoever packed them, and the same release is written every one of those ways.
        """
        import re as _re
        text = _re.sub(r"\.(srt|vtt|ass|ssa|sub|mkv|mp4|avi|m4v|ts)$", "",
                       (name or "").strip(), flags=_re.I)
        return _re.sub(r"[^a-z0-9]+", "", text.lower())

    def same_name(self, video, name):
        """Whether this subtitle is named after this very file.

        The strongest evidence there is short of a hash: whoever made it had this
        release in front of them. A trailing language or a "-en" is still the same
        name - what is left over has to be short enough to be a tag rather than a
        different release.
        """
        ours = self.bare_name(os.path.basename(video or ""))
        theirs = self.bare_name(name)
        if not ours or not theirs:
            return False
        if ours == theirs:
            return True
        longer, shorter = (theirs, ours) if len(theirs) > len(ours) else (ours, theirs)
        return longer.startswith(shorter) and len(longer) - len(shorter) <= 6

    def release_agrees(self, video, name, ignore=None):
        """How much of the release these two names share, ignoring bare numbers.

        A year is in every name for a film and says nothing about which rip this is,
        so it is not evidence of anything - and it was the whole of the agreement
        between a 1080p BluRay and a DVDRip that claimed to be the same file.
        """
        theirs = {w for w in self._words(name) if not w.isdigit()} - (ignore or set())
        ours = {w for w in self._words(
            os.path.splitext(os.path.basename(video or ""))[0])
            if not w.isdigit()} - (ignore or set())
        return len(ours & theirs)

    def matches_release(self, video, name, ignore=None):
        """How much a subtitle's name has in common with the video's own name.

        A subtitle is cut to one release; the one named after the same release as the
        file is the one whose timing fits. Words every episode shares - the programme,
        the numbering - say nothing, so what is left is the release itself: the source,
        the group, the encoder.
        """
        theirs = self._words(name) - (ignore or set())
        ours = self._words(os.path.splitext(os.path.basename(video))[0]) - (ignore or set())
        return len(ours & theirs)

    def best_beside(self, video, language=""):
        """The file beside this video most likely to be in time with it.

        Scored on the release alone. The programme's name and the episode's number are
        in every one of these names and say nothing about which is in time - counting
        them made a subtitle for another edit look like a perfect match, because it
        agreed about the name of the programme.
        """
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        if not beside:
            return None
        # the programme's name and the episode's, which every one of these carries and
        # none of which says anything about timing
        common = self.title_words(video)
        want = (language or "").lower()[:2]
        def score(side):
            same = (side.get("lang") or "").lower()[:2] == want if want else False
            return (self.matches_release(video, side.get("name") or "", common), same)
        best = max(beside, key=score)
        return best if score(best)[0] >= 2 else None

    def another_cut(self, video, index):
        """Whether this subtitle is for a different edit of the film - and the proof.

        Listening to the film cannot tell a late subtitle from one made for another
        cut. Another subtitle beside the same file can: if the two say the same lines
        at times that differ by a steady amount, one is simply late; if the difference
        wanders by half a minute across the film, they are different edits and no
        single correction can help.
        """
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        # A file beside the video is addressed by its place in this list, and the list
        # is sorted by name: a subtitle arriving while the film plays - one being
        # written from the sound - shifts every place after it. A number that no
        # longer points at anything is a question about a subtitle that has moved,
        # not a fault.
        if index >= 0 or not beside or -index - 1 >= len(beside):
            return None
        mine = beside[-index - 1]
        fits = self.best_beside(video)
        if not fits or fits.get("file") == mine.get("file"):
            return None

        def lines(path):
            out = {}
            try:
                with open(path, "rb") as f:
                    raw = f.read().decode("utf-8", "replace")
            except OSError:
                return out
            for block in re.split(r"\n\s*\n", raw):
                m = re.search(r"(\d\d):(\d\d):(\d\d)[,.](\d+)\s*-->", block)
                if not m:
                    continue
                at = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 +
                      int(m.group(3)) + int(m.group(4)[:3]) / 1000.0)
                said = re.sub(r"[^a-z ]", " ", block[m.end():].lower())
                said = " ".join(said.split())
                if len(said) > 12:
                    out.setdefault(said, []).append(at)
            return out

        ours, theirs = lines(mine.get("file")), lines(fits.get("file"))
        both = [(ours[said][0] - theirs[said][0]) for said in ours
                if said in theirs and len(ours[said]) == 1 and len(theirs[said]) == 1]
        if len(both) < 20:
            return None                     # too little in common to say anything
        both.sort()
        middle = both[len(both) // 2]
        spread = both[-1] - both[0]
        near = sum(1 for d in both if abs(d - middle) < 1.0)
        return {"lines": len(both), "middle": round(middle, 2),
                "spread": round(spread, 1), "steady": near,
                "against": fits.get("name") or "",
                # a file that is merely late says the same number everywhere; one for
                # another edit wanders, because the breaks fall in other places
                "another_cut": spread > 5.0 and near < len(both) * 0.7}

    def inside_like(self, key, after, index):
        """The subtitle track in this episode's file that matches track `index` of the
        one before: same codec and language, the same number where there is a choice."""
        def tracks(episode):
            con = S.local().lib.db()
            try:
                row = con.execute("SELECT streams FROM file WHERE episode_id=?",
                                  (str(episode),)).fetchone()
                return json.loads((row["streams"] if row else None) or "[]")
            except Exception:
                return []
            finally:
                con.close()
        was = next((t for t in tracks(after) if t.get("index") == index), None)
        if not was:
            return None
        kind = lambda t: ((t.get("codec") or "").lower(), (t.get("lang") or "").lower()[:2])
        here = [t for t in tracks(key) if kind(t) == kind(was)]
        if not here:
            return None
        return next((t["index"] for t in here if t.get("index") == index), here[0]["index"])

    #: what counts as the film having subtitles of its own, for downloading
    OWN_TRACK = ("skip", "text", "always")

    def fetch_rules(self, stored=None):
        """What may be downloaded: over a track of the film's own ("skip" - never,
        "text" - only where its own is pictures, "always"), whether the subtitle check
        downloads when nothing fits, and which languages - empty for every one read."""
        stored = self.settings_file() if stored is None else stored
        own = str(stored.get("subsOwnTrack") or "skip")
        langs = [str(x)[:2] for x in (stored.get("subsLangs") or []) if x]
        return {"own": own if own in self.OWN_TRACK else "skip",
                "check": stored.get("subsCheckFetch") is not False,
                "langs": langs}

    def languages_read(self, stored=None):
        """Every subtitle language somebody here reads, first and second choices."""
        return S.languages_read(self.settings_file() if stored is None else stored)

    def may_fetch_language(self, language, stored=None):
        """Whether subtitles in this language may be downloaded by themselves."""
        langs = self.fetch_rules(stored)["langs"]
        return not langs or str(language or "")[:2].lower() in langs

    def has_own(self, path, language, stored=None):
        """Whether the film's own tracks stop a download in this language."""
        own = self.fetch_rules(stored)["own"]
        if own == "always":
            return False
        return self.text_track_in(path, language, pictures=(own == "skip"))

    def already_subtitled(self, key, path, language):
        """Has this episode got subtitles already, from any source?

        A file beside the video counts however it is named, and so does a text track
        inside the video in the language being asked for. Either way there is nothing
        to fetch, and a download saved is a download left in the day's allowance.
        """
        import pd_localapi
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT duration FROM file WHERE path=?", (path,)).fetchone()
            runtime = float((row["duration"] if row else 0) or 0)
        except Exception:
            runtime = 0.0
        finally:
            con.close()
        # one cut for a longer video does not count: a proper one is fetched instead
        if any(not pd_localapi.far_too_long(side["file"], runtime)
               for side in pd_localapi.sidecars(path)):
            return True
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT streams FROM file WHERE episode_id=?",
                              (str(key),)).fetchone()
            tracks = json.loads((row["streams"] if row else None) or "[]")
        except Exception:
            tracks = []
        finally:
            con.close()
        # a text track of its own in the language is there to read; whether a
        # picture track stops a download is has_own's to say, by the setting
        names = {"en": ("en", "eng"), "sv": ("sv", "swe")}.get(
            (language or "en").lower()[:2], ((language or "en").lower()[:2],))
        return any((t.get("codec") or "").lower() in self.TEXT_CODECS
                   and (t.get("lang") or "").lower() in names for t in tracks)

    @staticmethod
    def _words(text):
        """Words worth comparing: no punctuation, no numbers, nothing tiny."""
        # apostrophes joined, not split: "Women's" against a release's "Womens"
        plain = re.sub("['\u2019`]", "", (text or "").lower())
        return {w for w in re.split(r"[^a-z0-9]+", plain)
                if len(w) > 2 and not w.isdigit()}

    @staticmethod
    def release_family(name):
        """(source, group) a subtitle's release name ends in, e.g. ("bluray", "btn");
        None when the name carries no group."""
        low = re.sub(r"\.(srt|vtt)$", "", (name or "").strip().lower())
        source = next((s for s, pat in (("bluray", r"blu-?ray|bdrip|brrip"),
                                        ("web", r"web-?dl|webrip|amzn|\bweb\b"),
                                        ("hdtv", r"hdtv"), ("dvd", r"dvd"))
                       if re.search(pat, low)), "")
        m = re.search(r"[-.]([a-z0-9]{2,12})$", low)
        group = m.group(1) if m else ""
        if not group or group in ("x264", "x265", "h264", "hevc", "dl", "web", "en",
                                  "eng", "proper", "repack", "hi") or group.isdigit():
            return None
        return (source, group)

    def family_beside(self, video, language, families):
        """Whether a subtitle in this language beside the video is of one of these groups."""
        import pd_localapi
        names = pd_localapi._release_names()
        for side in pd_localapi.sidecars(video):
            if (side.get("lang") or "")[:2] != language[:2]:
                continue
            for n in (names.get(side["file"]), side.get("name"), os.path.basename(side["file"])):
                if n and self.release_family(n) in families:
                    return True
        return False

    def source_wanted(self, key, release=""):
        """The source a subtitle for this episode should be cut from: the one the
        previous episode's subtitle names, else the series' remembered release, else
        the video file's own name."""
        import pd_localapi
        before = ""
        if str(key).startswith("e"):
            con = S.local().lib.db()
            try:
                before = S.local().prev_episode_key(con, key) or ""
            except Exception:
                before = ""
            finally:
                con.close()
        names = []
        if before:
            mine = self.viewer_settings(self.settings_file())
            picked = str(self.picked_for(before) or "")
            if not re.match(r"^t\d+$", picked):
                names.append(picked)
            tried = (mine.get("subsTried", {}) or {}).get(before)
            if isinstance(tried, dict):
                names.append(str(tried.get("release") or ""))
        names.append(release or "")
        names.append(os.path.basename(str((S.local().file_for(key, 0) or {}).get("file") or "")))
        return next((s for s in map(pd_localapi.release_source, names) if s), "")

    def fitting_families(self, key, language):
        """Release families verified on other episodes of the same series, counted.
        A series' subtitles mostly come cut by one group for one source, and the
        group that fitted episode 5 fits episode 6."""
        show = self.show_of(key) if str(key).startswith("e") else None
        if not show:
            return {}
        con = S.local().lib.db()
        try:
            ids = [str(r["id"]) for r in con.execute(
                "SELECT id FROM episode WHERE item_id=?", (show,))]
        finally:
            con.close()
        import pd_localapi
        done = self.settings_file().get("subsOk") or {}
        video = (S.local().file_for(key, 0) or {}).get("file") or ""
        counts = {}
        for ep in ids:
            if ep == str(key):
                continue
            mark = self.verified_for(done.get(ep))
            said = mark.get(language) or mark.get(language[:2]) or {}
            family = self.release_family(said.get("release") if isinstance(said, dict)
                                         else "")
            if family and pd_localapi.family_fits_video(family, video):
                counts[family] = counts.get(family, 0) + 1
        return counts

    def rank_subtitles(self, results, release, title, season, number, filename="",
                       families=None, source=""):
        """Best first - and "best" means most likely to be in time with the picture.

        The words rarely differ between variants; the timing does. A subtitle is cut to
        one particular release, so the one named after the same release as the video is
        the one whose timing fits, and that outweighs everything except a hash of the
        file itself. Then the episode's own identity, then the release this series has
        been watching with, and downloads only to break a tie.
        """
        import pd_localapi
        tags = []
        if season is not None and number is not None:
            tags = ["s%02de%02d" % (season, number), "%dx%02d" % (season, number),
                    "%dx%d" % (season, number)]
        episode_words = self._words(title)
        release_words = self._words(release)
        # the video's own name, minus the words every episode of the show shares
        file_words = self._words(os.path.splitext(os.path.basename(filename))[0])
        file_words -= episode_words

        def score(r):
            name = r.get("name") or ""
            low = name.lower()
            words = self._words(name)
            points = 0
            # the hash is of the video itself: nothing else comes close as evidence
            if r.get("fromHash"):
                points += 6
            # cut for the same release as the file in hand, which is what timing is
            points += min(8, 2 * len(file_words & words))
            if tags and any(t in low for t in tags):
                points += 3
            points += 2 * len(episode_words & words)
            points += len(release_words & words)
            # Tiers, not a total. Sharing the release is the one thing that predicts
            # timing, and as points it could be outweighed by a subtitle that merely
            # agreed about the episode's title and carried its number - which every
            # candidate for the right episode does. So it is asked first, and only
            # what is left of the scoring settles the order inside each tier.
            return (1 if self.same_name(filename, name) else 0,   # this very release
                    1 if r.get("fromHash") else 0,     # the same bytes: nothing beats it
                    # the group whose subtitles fitted this series' other episodes
                    (families or {}).get(self.release_family(name), 0),
                    # cut from the same source as the last episode's subtitle: a
                    # series watched with Blu-ray subtitles keeps to Blu-ray ones
                    1 if source and pd_localapi.release_source(name) == source else 0,
                    len(file_words & words),           # then how much of the release
                    points,
                    r.get("downloads", 0))

        return sorted(results, key=score, reverse=True)

    #: candidates downloaded to read their text, at most, when the numbering does not match
    BY_NAME_TRIES = 3

    def auto_by_name(self, key, after, language, results, src):
        """A subtitle for an episode the provider numbers otherwise: the upload named like
        the one the episode before was fetched with first, then one whose text names
        this episode - each read before it is kept, a wrong one deleted again."""
        import pd_subs
        stored = self.settings_file()
        before = ""
        prev = S.local().file_for(after, 0) if after else None
        if prev and prev.get("file"):
            stem = os.path.splitext(os.path.basename(prev["file"]))[0].lower()
            for path, name in (stored.get("subsRelease") or {}).items():
                base = os.path.basename(str(path)).lower()
                if base.startswith(stem) and base.endswith("." + language[:2].lower() + ".srt"):
                    before = str(name or "")
                    break
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT title, number FROM episode WHERE id=?", (str(key),)).fetchone()
        finally:
            con.close()
        title, number = (str(row["title"] or ""), int(row["number"] or 0)) if row else ("", 0)
        for r in pd_subs.by_name_order(results, before)[:self.BY_NAME_TRIES]:
            got = self.fetch_subtitle(key, r["id"], language, True, r.get("name", ""),
                                      downloads=r.get("downloads") or 0)
            if not got.get("ok"):
                continue
            where = os.path.join(os.path.dirname(src["file"]), got.get("file") or "") if src else ""
            try:
                with open(where, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                text = ""
            if pd_subs.names_episode(text, title, number) or (before and r.get("name") == before
                                                             and pd_subs.names_episode(text, "", number)):
                self.place_later(key, got.get("file", ""))
                return dict(got, byName=True)
            try:
                os.remove(where)        # another episode's: not kept
            except OSError:
                pass
        return {"ok": False, "error": "Nothing found for this episode - the numbering at "
                                      "OpenSubtitles does not match this library, and no "
                                      "upload names it."}

    def auto_subtitle(self, key, after=""):
        """Fetch what this series has been having, if it has been having anything.

        Silent about most failures on purpose: this runs when an episode starts, and
        an episode that plays without subtitles is better than one that refuses to.
        """
        if self.settings_file().get("autoFetch") is False:
            return {"ok": False, "error": "Fetching subtitles ahead is turned off."}
        episode = str(key).startswith("e")
        con = S.local().lib.db()
        try:
            row = (con.execute("SELECT item_id FROM episode WHERE id=?",
                               (str(key),)).fetchone() if episode else None)
            # the same question for whatever is playing now, so a remembered choice is
            # only carried across when both belong to the same programme
            before = (con.execute("SELECT item_id FROM episode WHERE id=?",
                                  (str(after),)).fetchone()
                      if str(after).startswith("e") else None)
        except (TypeError, ValueError):
            row, before = None, None
        finally:
            con.close()
        if episode and not row:
            return {"ok": False, "error": "No such episode."}
        family = str(row["item_id"]) if row else str(key)
        stored = self.settings_file()
        # The episode being watched is on a track inside its own file: the next one
        # takes the same kind of track from its file, and nothing is fetched. A track
        # with no language on it read as no subtitle, so a file was downloaded and
        # chosen over the one the series was being watched with.
        if after and before and str(before["item_id"]) == family:
            was = str(self.picked_for(after) or "")
            if re.match(r"^t\d+$", was):
                same = self.inside_like(key, after, int(was[1:]))
                if same is not None:
                    if not self.picked_for(key):
                        self.remember_pick(key, "t%d" % same)
                    return {"ok": True, "already": True, "inside": True}
        # fetching happens before the episode in hand has finished, so the series has
        # not been taught yet - but the episode being watched knows what it is using
        after = after or self.prev_of(key)
        want = self.auto_want(key, after, stored=stored)
        if not want:
            # nothing remembered for this one - which is most of what a shuffle plays.
            # The language this viewer reads is enough to go on.
            language = self.viewer_language()
            if language == "off":
                return {"ok": False, "error": "Subtitles are set to off."}
            if not language:
                return {"ok": False, "error": "This series has no remembered choice."}
            want = {"language": language}

        if not self.may_fetch_language(want["language"], stored):
            return {"ok": False, "error": "Not a language set to be downloaded."}
        src = S.local().file_for(key, 0)
        if src and self.has_own(src["file"], want["language"], stored):
            return {"ok": True, "already": True, "own": True}
        import pd_localapi
        import pd_subs

        def keep_beside():
            # Nothing to fetch - but there may be several beside the film, and one of
            # them may be cut for this very release. That one is in time with the
            # picture and the others are somebody else's edit, so it leads the list
            # unless a person has already chosen for themselves.
            fits = self.best_beside(src["file"], want["language"])
            # only in the viewer's own language: fetching ahead brings every language
            # kept, and the Swedish one landing second became an English viewer's pick
            mine_too = want["language"][:2].lower() == self.viewer_language()[:2]
            if fits and mine_too and not self.picked_for(key):
                self.remember_pick(key, fits.get("name") or "")
                return {"ok": True, "already": True, "chose": fits.get("name") or ""}
            return {"ok": True, "already": True}

        # the group verified on this series' other episodes, wanted when nothing beside
        # is of it: a pack's own subtitle of another cut was kept over it
        families = {}
        if src and self.already_subtitled(key, src["file"], want["language"]):
            families = self.fitting_families(key, want["language"])
            if (not families or self.picked_for(key)
                    or pd_localapi.subtitle_verified(key, want["language"])
                    or self.family_beside(src["file"], want["language"], families)):
                return keep_beside()

        # the list's own order and the one it marks: what is shown is what is taken
        found = self.find_subtitles(key, want["language"], after=after)
        results = found.get("results") or []
        if not results:
            return {"ok": False, "error": found.get("error") or "Nothing found."}
        pick = next((r for r in results if r.get("autoPick")), None)
        if pick is None:
            # The numbering at OpenSubtitles does not match this library's, so nothing
            # is marked for this episode. The upload the episode before was watched
            # with, by its name, then one whose own text names this episode.
            return self.auto_by_name(key, after, want["language"], results, src)
        if families and self.release_family(pick.get("name")) not in families:
            return keep_beside()        # the group is not on offer for this one
        # remembered like any other fetch: this is the file that episode will be
        # watched with, and watching it through is what verifies it. Fetched ahead
        # rather than by hand is no reason for it never to earn the mark.
        got = self.fetch_subtitle(key, pick["id"], want["language"], True,
                                  pick.get("name", ""),
                                  downloads=pick.get("downloads") or 0)
        if got.get("ok"):
            self.place_later(key, got.get("file", ""))
            if str(key) not in S.Handler.SUBCHECK_SOON:
                S.Handler.SUBCHECK_SOON.append(str(key))
                del S.Handler.SUBCHECK_SOON[:-40]
        return got

    def place_later(self, key, filename):
        """Measure a just-fetched subtitle against the film, in the background.

        Only when this viewer has asked for subtitles to be placed for them. It happens
        here, five minutes before the episode is reached, rather than as it starts: the
        first line is then already in step, and no screen shows anything happening.
        Failures are silent, as everything about fetching ahead is.
        """
        if not filename:
            return
        if not self.scans_for():
            return
        found = S.local().file_for(key, 0) or {}
        video = found.get("file")
        if not video:
            return
        import pd_localapi
        beside = pd_localapi.sidecars(video)
        at = next((i for i, one in enumerate(beside)
                   if os.path.basename(one["file"]) == filename), None)
        if at is None:
            return
        # named the way a client names it, so what is written here is what the player
        # reads when the episode starts, rather than a second copy under another name
        sub = beside[at].get("name") or ""

        def work():
            try:
                self.sync_subtitle(key, 0, -(at + 1), "l" + str(key), sub, True,
                                   "placing a fetched subtitle before its episode is reached")
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    #: speech heard in a file, by path and modification time: [(start, {"speech": marks})]
    SPEECH = {}

    #: titles to check before the rest: a subtitle was just fetched ahead for them
    SUBCHECK_SOON = []

    #: downloads left in the day's allowance below which the check leaves the rest to people
    SUBCHECK_RESERVE = 30

    #: candidates fetched per title and language
    SUBCHECK_TRIES = 3

    def speech_for(self, video, length):
        """Where people speak in this file, measured once and kept beside the others."""
        import hashlib
        import pd_credits
        import pd_sync
        mark = video + "|vad|" + str(os.path.getmtime(video))
        if mark in self.SPEECH:
            return self.SPEECH[mark]
        where = os.path.join(S.ROOT, "speech",
                             hashlib.sha1(mark.encode("utf-8")).hexdigest() + ".json")
        windows = None
        try:
            with open(where, encoding="utf-8") as f:
                windows = json.load(f)
        except (OSError, ValueError):
            pass
        if windows is None:
            windows, why = pd_credits.speech_windows(video, length)
            if not windows and str(why or "").startswith("stopped"):
                return None                 # its drive was wanted: no answer, asked again
            if not windows:
                return []
            try:
                os.makedirs(os.path.dirname(where), exist_ok=True)
                with open(where, "w", encoding="utf-8") as f:
                    json.dump(windows, f)
            except OSError:
                pass
        hz = pd_sync.HZ
        got = []
        for start, span, spans in windows:
            marks = [0] * int(span * hz)
            for a, b in spans:
                for i in range(max(0, int(a * hz)), min(len(marks), int(b * hz) + 1)):
                    marks[i] = 1
            got.append((float(start), {"speech": marks}))
        if len(self.SPEECH) > 16:
            self.SPEECH.clear()
        self.SPEECH[mark] = got
        return got

    def check_subtitles(self, key, language, fetch=True):
        """Measure this title's subtitles in one language against its speech; mark the one
        that fits.

        The ones beside the film are measured first, which costs nothing. Only when none
        of them fits are up to three candidates fetched, best ranked first; the one that
        fits is kept and verified, the others are deleted again. A subtitle steady at an
        offset gets that offset written down for its file, as a sync would.
        """
        import pd_localapi
        import pd_subs
        import pd_sync
        if pd_localapi.subtitle_verified(key, language):
            return {"verdict": "verified"}
        found = S.local().file_for(key, 0) or {}
        video = found.get("file")
        length = float(found.get("duration") or 0)
        if not video or not os.path.exists(video) or length < 300:
            return {"verdict": "no file"}
        got = self.speech_for(video, length)
        if got is None:
            return {"verdict": "stopped"}
        if len([w for w in got if sum(w[1]["speech"]) > 50]) < 2:
            return {"verdict": "no speech"}

        def mine():
            return [(n, side) for n, side in enumerate(pd_localapi.sidecars(video))
                    if (side.get("lang") or "")[:2] == language[:2]
                    and side["file"].lower().endswith((".srt", ".vtt"))
                    and not pd_localapi.far_too_long(side["file"], length)]

        def measure(side):
            try:
                with open(side["file"], encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                return None
            if text.count("-->") < 20:
                return None
            return pd_sync.judge(got, text)

        seen = {}
        for n, side in mine():
            seen[side["file"]] = (side, measure(side))
        if not seen and self.text_track_in(video, language):
            return {"verdict": "inside"}       # the film's own track: nothing to fetch
        fetched = []
        login_failed = False
        best = self._fits_best(seen)
        if not best and self.has_own(video, language):
            # the film carries this language itself: nothing is downloaded for it
            return {"verdict": "inside", "measured": len(seen)}
        # and nothing at all where downloading is off for the check or the language
        rules = self.fetch_rules()
        if not rules["check"] or not self.may_fetch_language(language):
            fetch = False
        # the copy server shares the main server's OpenSubtitles login and takes its
        # choices: its check measures what it has and spends nothing of the day's downloads
        if self.mirror_of():
            fetch = False
        if not best and not fetch:
            # the rest of the library: what is beside the film is measured, and
            # nothing is fetched for a title nobody is watching
            return {"verdict": "none fit" if seen else "nothing here",
                    "measured": len(seen), "fetched": 0}
        if not best:
            if pd_subs.allowance_short(self.SUBCHECK_RESERVE):
                return {"verdict": "allowance", "measured": len(seen)}
            asked = self.find_subtitles(key, language)
            ranked = asked.get("results") or []
            ranked = [r for r in ranked if r.get("episode") is not False]
            if any("episode" in r for r in ranked):
                ranked = [r for r in ranked if r.get("episode")]
            have = {(side.get("name") or "").strip().lower() for side, _ in seen.values()}
            for r in ranked:
                if len(fetched) >= self.SUBCHECK_TRIES:
                    break
                if (r.get("name") or "").strip().lower() in have:
                    continue
                out = self.fetch_subtitle(key, r["id"], language, False,
                                          r.get("name", ""), pick=False)
                if not out.get("ok"):
                    if "login" in (out.get("error") or ""):
                        # a refused login says nothing about the subtitles: asked again
                        # later instead of written down as none fitting
                        login_failed = True
                        break
                    if "allowance" in (out.get("error") or "") or                             "refused" in (out.get("error") or ""):
                        break
                    continue
                if out.get("already"):
                    continue
                fetched.append(out.get("file"))
                for n, side in mine():
                    if os.path.basename(side["file"]) == out.get("file"):
                        seen[side["file"]] = (side, measure(side))
            best = self._fits_best(seen)
        # what was fetched and did not win goes again - unless nothing fitted and
        # nothing was here before, when the best ranked one is still better than none
        keep = best[0]["file"] if best else ""
        if not best and fetched and not any(os.path.basename(f) not in fetched
                                            for f in seen):
            keep = next((f for f in seen if os.path.basename(f) == fetched[0]), "")
        for f in list(seen):
            if os.path.basename(f) in fetched and f != keep:
                self.remove_sidecar(key, seen[f][0].get("name") or "")
        if not best:
            # nothing fits a title somebody is watching or is about to: one is written
            # from its sound instead (owner, 2026-11-11)
            if fetch and not login_failed:
                self.write_from_sound(video, language, key)
            return {"verdict": "login" if login_failed else "none fit",
                    "measured": len(seen), "fetched": len(fetched)}
        side, said = best
        if abs(said["offset"]) > pd_sync.AGREE_S:
            self.set_sub_fit(side["file"], [(0.0, 1.0, said["offset"])])
        self.verify_subtitle(key, side.get("name") or "", language, how="speech")
        return {"verdict": "fits", "name": side.get("name"), "offset": said["offset"],
                "agree": said["agree"], "heard": said["heard"],
                "measured": len(seen), "fetched": len(fetched)}

    def write_from_sound(self, video, language, key):
        """A subtitle written from the film's sound, asked for once: where the tools are
        here, and none has been written for it already."""
        import pd_ai_subs
        try:
            if not pd_ai_subs.ready() or os.path.exists(pd_ai_subs.beside(video, language)):
                return False
            pd_ai_subs.ask(video, language, key=key)
            return True
        except Exception:
            return False

    def text_track_in(self, video, language, pictures=False):
        """Whether the video carries a text subtitle track in this language - or, with
        pictures, any subtitle track in it: a Blu-ray's own is pictures."""
        con = S.local().lib.db()
        try:
            row = con.execute("SELECT streams FROM file WHERE path=?", (video,)).fetchone()
            tracks = json.loads((row["streams"] if row else None) or "[]")
        except Exception:
            tracks = []
        finally:
            con.close()
        names = {"en": ("en", "eng"), "sv": ("sv", "swe")}.get(language[:2],
                                                                (language[:2],))
        return any((pictures or (t.get("codec") or "").lower() in self.TEXT_CODECS)
                   and (t.get("lang") or "").lower() in names for t in tracks)

    @staticmethod
    def _fits_best(seen):
        """The steadiest subtitle that fits: most windows agreeing, then nearest nought."""
        good = [(side, said) for side, said in seen.values() if said and said["good"]]
        if not good:
            return None
        return max(good, key=lambda p: (p[1]["agree"], p[1]["sure"],
                                        -abs(p[1]["offset"])))
