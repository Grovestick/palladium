"""Playback: what quality a screen gets, transcoding on the card here or on the other machine, the stream itself and the buffer reports that pace it.

Moved whole from Handler in pd-server.py; the server's own names are reached through S,
the server module, which pd-server.py hands over before importing it."""
import json
import math
import os
import re
import subprocess
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


class StreamParts(_Base):
    @staticmethod
    def wanted_audio(q, src=None):
        """Which soundtrack to encode: the one asked for, or the file's English one.

        ffmpeg takes the first soundtrack when nobody says otherwise, and on a
        release with a dub in front of it that is not the one anybody wanted.
        """
        raw = (q.get("atrack") or [""])[0]
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = -1
        if n >= 0:
            return n
        got = (src or {}).get("audio")
        return int(got) if got is not None else None

    #: No cap at all until somebody sets one. Two of them, because a house has a
    #: gigabit of network in it and an upload of rather less, and the film that plays
    #: perfectly in the next room is the one that stutters at a friend's.
    QUALITY_DEFAULTS = {"home": {"height": 0, "mbit": 0},
                        "away": {"height": 0, "mbit": 0}}

    @staticmethod
    def shift_name(key, sub):
        """One correction belongs to one subtitle of one title.

        Two files for the same episode are out by two different amounts - that is
        usually why there are two - so the timing is filed against the subtitle
        itself: its stream number for a track inside the film, its file name for one
        beside it. A title with nothing named is the old form, kept so a correction
        made before this still applies.
        """
        return str(key) + ("|" + str(sub) if sub else "")

    def passthrough_db(self, stored, key):
        """How far the receiver goes for this film (pd_receiver.level_for): +5 dB at the
        typical loudness of its kind, more or less by its own measurement near that,
        +5 alone far from it. None until the film is measured; it is measured now."""
        found = S.local().file_for(str(key or ""), 0) or {}
        part = found.get("part")
        import pd_receiver
        if part is None:
            # held only on the other server: its reading, which it made when it was played
            # there; without it this said "measuring" for the whole film (2026-11-07)
            said = S.peer_reading(key)
            heard = pd_receiver.reading_for(said.get("reading"), said.get("codec") or "")
            if heard is None:
                return None
            return pd_receiver.level_for(heard, S.typical_loudness(S.is_episode(str(key or ""))))
        with S.LOUDNESS_LOCK:
            known = S.read_loudness().get(str(part))
        # one window at 5:00 was up to 9 dB out, which turned a loud film up 13 dB;
        # and a Dolby track is judged by its reading with compression off
        heard = pd_receiver.reading_for(known, S.codec_of(part))
        if heard is None:
            return None
        return pd_receiver.level_for(heard, S.typical_loudness(S.is_episode(str(key or ""))))

    #: films whose passthrough began before they were measured: raised once measured,
    #: unless their screen has said they ended meanwhile
    PASS_WAITING = set()

    def measure_then_raise(self, who, key):
        """Measure the film now, as twenty samples, then raise the receiver for it."""
        import pd_gpu
        import pd_receiver
        found = S.local().file_for(str(key or ""), 0) or {}
        part, path = found.get("part"), found.get("file")
        if part is not None and path:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            try:
                S.take_readings(part, path, size, float(found.get("duration") or 0))
            except Exception:
                pass
        name = "%s|%s" % (who, key)
        if name not in S.Handler.PASS_WAITING:
            return                      # ended before the measurement was done
        S.Handler.PASS_WAITING.discard(name)
        stored = S.read_settings() or {}
        said = pd_receiver.raise_for(stored, who, key, self.passthrough_db(stored, key))
        pd_log.raw("%s receiver start for %s once measured: %s%s"
                % (time.strftime("%H:%M:%S"), who, json.dumps(said), chr(10)))

    @staticmethod
    def fit_name(video, index):
        """What a fit is filed under: the subtitle itself.

        A correction for drift belongs to the file, not to the viewer or the screen -
        the same file is out by the same amount for everybody, and it is the server
        that puts it right before anybody sees it.
        """
        if index is not None and int(index) < 0:
            import pd_localapi as _la
            try:
                return _la.sidecars(video)[-int(index) - 1]["file"]
            except Exception:
                return "%s|s%d" % (video, int(index))
        return "%s|t%s" % (video, index)

    @staticmethod
    def fit_key(name):
        """A fit's name as two machines agree on it: the subtitle's own file name, or
        the film's with its track. The full path is each machine's own - the cache
        keeps its copies in another folder - so a correction made on the main server
        never reached the same subtitle on the cache, which played it 1.9 s early."""
        name = str(name or "")
        if "|" in name:
            video, _, rest = name.partition("|")
            return os.path.basename(video.replace("\\", "/")).lower() + "|" + rest
        return os.path.basename(name.replace("\\", "/")).lower()

    #: the main server's fits by fit_key, as a cache last heard them, and when
    HOUSE_FITS = {"at": 0.0, "fits": {}}

    def house_fits(self):
        """The main server's fits, asked for at most every ten minutes; kept for when
        it is off."""
        now = time.time()
        if now - S.Handler.HOUSE_FITS["at"] > 600:
            S.Handler.HOUSE_FITS["at"] = now
            try:
                import pd_follow
                one = pd_follow.settings(S.local().lib.config())
                if one.get("on") and one.get("master"):
                    got = pd_follow.ask(one, "/follow/fits", 10)
                    if isinstance(got.get("fits"), dict):
                        S.Handler.HOUSE_FITS["fits"] = got["fits"]
            except Exception:
                pass
        return S.Handler.HOUSE_FITS["fits"]

    def my_quality(self):
        """What this viewer has asked for, for themselves. Zero means no preference."""
        mine = self.viewer_settings(self.settings_file()) or {}
        one = mine.get("quality") or {}
        return {"height": max(0, int(one.get("height") or 0)),
                "mbit": max(0, int(one.get("mbit") or 0))}

    def quality_caps(self):
        """The ceiling on each side of the front door, as whole numbers."""
        stored = (self.settings_file() or {}).get("quality") or {}
        out = {}
        for where in ("home", "away"):
            one = stored.get(where) or {}
            out[where] = {"height": max(0, int(one.get("height") or 0)),
                          "mbit": max(0, int(one.get("mbit") or 0))}
        return out

    def too_big_to_send(self, file_id):
        """Why this file may not be sent as it is, or nothing.

        Only when a ceiling applies to this viewer: a file smaller than the limit is
        sent untouched, as it always was, because re-encoding it would cost work and
        gain nothing.
        """
        cap = self.quality_caps()["home" if self.at_home() else "away"]
        own = self.my_quality()
        height = min([x for x in (cap["height"], own["height"]) if x] or [0])
        mbit = min([x for x in (cap["mbit"], own["mbit"]) if x] or [0])
        if not height and not mbit:
            return ""
        facts = S.local().file_facts(file_id) or {}
        if height and (facts.get("height") or 0) > height:
            return ("this copy is %dp and %dp is the most allowed here"
                    % (facts.get("height") or 0, height))
        # the library keeps a bitrate in kilobits
        rate = (facts.get("bitrate") or 0) / 1000.0
        if mbit and rate > mbit:
            return ("this copy runs at %.1f Mbit/s and %d is the most allowed here"
                    % (rate, mbit))
        return ""

    #: What a sound track of this kind costs, in megabits, at six channels. Taken off
    #: the top before the picture is measured: the library keeps the whole container's
    #: rate, and a film with DTS carries a megabit and a half that is not picture - and
    #: is re-encoded to something far smaller anyway.
    SOUND_MBIT = {"truehd": 4.0, "mlp": 4.0, "pcm": 4.6, "flac": 1.0,
                  "dtshd": 3.0, "dts-hd": 3.0, "dts": 1.5,
                  "eac3": 0.64, "ac3": 0.64, "opus": 0.25, "vorbis": 0.25,
                  "aac": 0.25, "mp3": 0.2, "mp2": 0.2}
    SOUND_OTHERWISE = 0.4

    #: How much of an old codec's bitrate a modern one needs for the same picture.
    #: MPEG-2 spends three times what h264 does, and the DVD rips spend about half
    #: again; asking for the old number would re-encode a 21 megabit broadcast at 21.
    CODEC_COST = {"mpeg2video": 0.4, "mpeg1video": 0.4, "msmpeg4v3": 0.6,
                  "mpeg4": 0.6, "msmpeg4v2": 0.6, "wmv3": 0.8, "vc1": 0.8,
                  "h264": 1.0, "avc": 1.0, "hevc": 1.0, "h265": 1.0,
                  "vp9": 1.0, "av1": 1.0}

    #: x265 only: 1.5x the source rate
    HEVC_HEADROOM = 1.5

    @classmethod
    def sound_mbit(cls, src):
        """Audio bitrate of this file, in Mbit."""
        try:
            name = str((src or {})["acodec"] or "").lower()
        except (KeyError, IndexError, TypeError):
            return 0.0
        if not name:
            return 0.0
        cost = cls.SOUND_OTHERWISE
        for word, mbit in cls.SOUND_MBIT.items():
            if word in name:
                cost = mbit
                break
        try:
            channels = int((src or {})["channels"] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            channels = 0
        # table is for 6 channels; 8 cost ~30% more, 2 cost ~60% less
        if channels >= 8:
            cost *= 1.3
        elif 0 < channels <= 2:
            cost *= 0.4
        return cost

    @classmethod
    def source_mbit(cls, src, hevc=False):
        """Encode ceiling derived from the source file, in Mbit, or 0.

        Container rate minus audio, scaled by CODEC_COST for the source codec (MPEG-2
        spends ~3x h264 for the same picture), then HEVC_HEADROOM for x265. Minimum 1.
        """
        try:
            rate = float((src or {})["bitrate"] or 0) / 1000.0
        except (KeyError, IndexError, TypeError, ValueError):
            return 0
        if rate <= 0:
            return 0
        picture = max(0.1, rate - cls.sound_mbit(src))
        try:
            vcodec = str((src or {})["vcodec"] or "").lower()
        except (KeyError, IndexError, TypeError):
            vcodec = ""
        cost = cls.CODEC_COST.get(vcodec, 1.0)
        want = picture * cost * (cls.HEVC_HEADROOM if hevc else 1.0)
        return max(1, int(math.ceil(want)))

    def capped(self, height, mbit, src=None, hevc=False):
        """What a viewer gets, given what they asked for and where they are.

        Three things have a say, in this order: what the player asked for, what this
        viewer has set for themselves, and the ceiling the server keeps for their side
        of the front door. A player that asks for nothing in particular gets the
        viewer's own setting; whatever comes out of that is then held to the ceiling.
        """
        cap = self.quality_caps()["home" if self.at_home() else "away"]
        return self.quality_for(height, mbit, self.my_quality(), cap,
                                self.source_mbit(src, hevc))

    @staticmethod
    def quality_for(height, mbit, own, cap, source_mbit):
        """(height, megabits) for an encode: what was asked, else the viewer's own
        setting, held to the ceiling set for their side of the door, and never more
        megabits than the file carries. Nothing else: with no ceiling set there is
        none, outside the house as inside. An unlisted 8 Mbit stood here for anybody
        outside while the settings page said "no ceiling".
        """
        if not height:
            height = own["height"]
        if not mbit:
            mbit = own["mbit"]
        if cap["height"] and (not height or height > cap["height"]):
            height = cap["height"]
        if cap["mbit"] and (not mbit or mbit > cap["mbit"]):
            mbit = cap["mbit"]
        # capped at what the source carries: the rules above measure the line, not
        # the file. A 528x384 DVD rip at 1 Mbit was re-encoded at 8 Mbit, three times
        # its own size, with no detail to gain.
        if source_mbit and (not mbit or mbit > source_mbit):
            mbit = source_mbit
        return int(height or 0), int(mbit or 0)

    def wanted_rate(self, q):
        """How many megabits the player asked for, or none."""
        try:
            return max(0, int(float(q.get("mbit", ["0"])[0])))
        except (TypeError, ValueError):
            return 0

    def quality_words(self, q, src, height, mbit, hevc=False):
        """How an encode's size and rate were arrived at, in the order quality_for takes
        them: the player's ask, the viewer's own setting, the ceiling for their side of
        the door, what the file carries; then what came out."""
        side = "home" if self.at_home() else "away"
        cap = self.quality_caps()[side]
        own = self.my_quality()
        size = lambda h: "%dp" % h if h else "any size"
        rate = lambda m: "%d Mbit" % m if m else "no rate"
        try:
            asked = int(q.get("height", ["0"])[0] or 0)
        except ValueError:
            asked = 0
        film = float((src or {}).get("bitrate") or 0) / 1000.0
        return ("asked %s %s, own %s %s, %s ceiling %s %s, file %.1f Mbit (at most %s) -> %s %s"
                % (size(asked), rate(self.wanted_rate(q)), size(own["height"]),
                   rate(own["mbit"]), side, size(cap["height"]), rate(cap["mbit"]), film,
                   rate(self.source_mbit(src, hevc)), size(height), rate(mbit)))

    def encode_said(self, st, q, src, height, mbit, hevc=False):
        """One line in the log per encode: how its size and rate were chosen and what
        ffmpeg was told (owner, 2026-11-12). Once per session: a receiver re-reads an
        HLS playlist every few seconds."""
        if getattr(st, "said", False):
            return
        st.said = True
        try:
            pd_log.raw("%s encode: %s to %s [%s] from %ss: %s; %s%s" % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                str(st.info.get("title") or q.get("key", [""])[0])[:50],
                self.client_address[0], self.app_name() or "?",
                q.get("offset", ["0"])[0], self.quality_words(q, src, height, mbit, hevc),
                st.info.get("recipe") or "recipe unknown", chr(10)))
        except Exception:
            pass

    def gpu_hls(self, q):
        """Start an HLS session for a client that cannot take the pipe, and redirect.

        Safari is that client, and on an iPhone there is no Media Source to work round
        it with. The playlist and its segments are served from the session folder by
        gpu_hls_file below.
        """
        if self.game_holds("transcode"):
            # asked to leave the card alone, and encoding is what it would be doing.
            # A film that can be played as it is still plays; one that needs encoding
            # says why rather than starting quietly and stuttering.
            self.send_error(503, "Game mode is on: this server is not encoding just now")
            return
        S.engine().sweep_hls()             # tidy what earlier viewers left behind
        try:
            key = q.get("key", [""])[0]
            mi = int(q.get("mi", ["0"])[0])
            src = (S.local().file_for(key, mi, who=self.viewer())
                   if q.get("src", [""])[0] == "local" else None)
            height, mbit = self.capped(int(q.get("height", ["0"])[0]),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            st = S.engine().start_hls(
                key,
                audio_index=self.wanted_audio(q, src),
                offset=int(float(q.get("offset", ["0"])[0])),
                height=height, mbit=mbit,
                media_index=mi,
                # the same rule as the pipe: a receiver cannot decode a bitmap
                # track either, and this is the path it plays through
                burn_index=(self.burn_for(q, src)
                            if self.burn_allowed_here(q) else None),
                src=src,
                sub_look=self.shifted_look(
                    self.subtitle_settings(key, q.get("device", ["web"])[0]),
                    q.get("subshift", ["0"])[0]))
        except Exception as e:
            self.send_error(500, str(e)[:200])
            return
        self.encode_said(st, q, src, height, mbit,
                         q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
        self.note_cast("playlist asked for", "key=%s offset=%s"
                       % (q.get("key", [""])[0], q.get("offset", ["0"])[0]))
        # The playlist itself, not a redirect to it, and with whole addresses for the
        # segments. A player that resolves a relative name against the address it
        # first asked for was looking for /s00000.ts and finding nothing.
        sid = st.info["id"]
        # the whole film, written out before it is encoded, so the receiver knows how
        # long it is and can draw a timeline
        text = S.engine().hls_playlist(st)
        if text is None:
            # duration unknown: hand over what ffmpeg has written so far instead
            path = S.engine().hls_file(sid, "index.m3u8")
            if not path:
                self.send_error(500, "the playlist did not appear")
                return
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                self.send_error(500, "the playlist could not be read")
                return
        lines = text.splitlines()
        token = self.bearer()
        host = self.headers.get("Host") or ("%s:%d" % (S.LAN_IP, S.PORT))
        base = "http://%s/gpu/hls/%s/" % (host, sid)
        body = chr(10).join(
            (base + line + ("?t=" + token if token else "")) if line.endswith(".ts")
            else line
            for line in lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Palladium-Engine", st.info["engine"])
        self.send_header("X-Palladium-Session", sid)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def gpu_hls_file(self, sid, name):
        """A playlist or a segment out of one session's folder."""
        seg = re.match(r"^s(\d{5})\.ts$", name or "")
        if seg:
            # the playlist names every segment of the film, so one of them can be
            # asked for before it exists - either because the encoder has not reached
            # it yet, or because somebody seeked past it. Both are waited for.
            path = S.engine().hls_segment(sid, int(seg.group(1)))
        else:
            path = S.engine().hls_file(sid, name)
        if not path:
            self.note_cast("MISSING " + name, sid)
            self.send_error(404, "no such segment")
            return
        # the playlist is the moment a receiver commits; segments after it are only
        # noise, so only the first of them is worth a line
        if name.endswith(".m3u8") or name.endswith("00000.ts"):
            self.note_cast("took " + name, sid)
        kind = ("application/vnd.apple.mpegurl" if name.endswith(".m3u8")
                else "video/mp2t")
        try:
            with open(path, "rb") as f:
                body = f.read()
            # A television fetching the segments is a machine of its own: it holds no
            # cookie, and the names in a playlist are relative, so the invitation has
            # to be written into each line or every segment is refused.
            token = self.bearer()
            if name.endswith(".m3u8") and token:
                body = chr(10).join(
                    (line + "?t=" + token) if line.endswith(".ts") else line
                    for line in body.decode("utf-8", "replace").splitlines()
                ).encode()
        except OSError:
            # a segment being written this instant: ask again rather than fail
            self.send_error(404, "not yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    # How long an encoder may take to produce its first block. It is a wait, not a
    # deadline for the film: NVENC on a 4K burn needs a few seconds before anything
    # exists. What it stops is the other case - a muxer that will never write - which
    # left the request hanging with no answer at all rather than failing.
    FIRST_BLOCK_WAIT = 15.0

    def no_dolby_here(self, path):
        """Has this file already been shown to produce nothing as Dolby?

        Written down against its size, so a file replaced by a better rip is asked
        again rather than judged by what the old one did.
        """
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        return self.noted_no_dolby(self.settings_file().get("noDolby"), path, size)

    @staticmethod
    def noted_no_dolby(known, path, size):
        """Noted for this file as it is now, under the Dolby command now in use."""
        import pd_gpu
        return (known or {}).get(path) == [size, pd_gpu.DOLBY_MAKE]

    def note_no_dolby(self, path):
        """Remember it, so the next viewing does not spend the wait discovering it."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        stored = self.settings_file()
        known = stored.get("noDolby", {}) or {}
        import pd_gpu
        known[path] = [size, pd_gpu.DOLBY_MAKE]
        # a few hundred is far more than any library will ever produce; the cap is
        # there so a fault that fires on everything cannot grow the file for ever
        if len(known) > 400:
            known.pop(next(iter(known)), None)
        stored["noDolby"] = known
        S.write_settings(stored, merge=False)

    @classmethod
    def first_block(cls, st):
        """The first bytes of a stream, or nothing if it never manages any."""
        gate = threading.Timer(cls.FIRST_BLOCK_WAIT, st.stop)
        gate.daemon = True
        gate.start()
        try:
            return st.proc.stdout.read(65536)
        except Exception:
            return b""
        finally:
            gate.cancel()

    #: what each file's picture is, asked once: a copy is only for a picture the device
    #: plays as it is
    VIDEO_FORMATS = {}

    def video_format(self, path):
        """The first video track's codec and pixel format."""
        if path in S.Handler.VIDEO_FORMATS:
            return S.Handler.VIDEO_FORMATS[path]
        out = {}
        try:
            import pd_gpu
            from pd_library import ffprobe_beside
            said = subprocess.run(
                [ffprobe_beside(pd_gpu.FFMPEG), "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=20, creationflags=pd_gpu.NO_WINDOW)
            bits = ((said.stdout or "").strip().splitlines() or [""])[0].split(",")
            out = {"codec": bits[0].strip().lower(),
                   "pix": bits[1].strip().lower() if len(bits) > 1 else ""}
        except Exception:
            out = {}
        # only an answer is kept: a probe that timed out on a busy disk was kept as
        # "no codec", and that file was encoded whole until the server restarted
        if out.get("codec"):
            S.Handler.VIDEO_FORMATS[path] = out
        return out

    def art_from_the_house(self, sub, q):
        """A poster or backdrop from the main server this machine follows, kept on disk."""
        import pd_follow
        one = pd_follow.settings(self.library_settings_plain() or {})
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        width = re.sub(r"[^0-9]", "", (q.get("w") or [""])[0])[:4]
        kept = os.path.join(S.ROOT, "cache", "house",
                            re.sub(r"[^0-9a-z]+", "_", sub.lower()).strip("_")
                            + ("_w" + width if width else "") + ".jpg")
        try:
            if os.path.getsize(kept) > 1000:
                with open(kept, "rb") as f:
                    return f.read()
        except OSError:
            pass
        url = (one["master"].rstrip("/") + "/local" + sub + "?t="
               + urllib.parse.quote(one["key"]) + ("&w=" + width if width else ""))
        try:
            req = urllib.request.Request(url, headers={"X-Palladium-App": "follower"})
            with urllib.request.urlopen(req, timeout=8) as answer:
                data = answer.read()
        except Exception:
            return None
        if len(data) < 1000:
            return None
        try:
            os.makedirs(os.path.dirname(kept), exist_ok=True)
            with open(kept, "wb") as f:
                f.write(data)
        except OSError:
            pass
        return data

    def picture_goes_through(self, q, src, height, mbit, burn, asked=None):
        """Whether this encode copies the picture: asked for, and possible. DTS sound the
        device cannot take is converted beside it and no longer makes it a full encode
        (owner, 2026-11-12): tried again, a disc's 4K picture played clean on the television;
        the blocks seen on 2026-09-25 came with one release group's x265 episodes, which
        the house now plays in another version."""
        # the app asks where a stream starts without the copy flag, and adds it only
        # when told the picture would go through - so that question counts as asked
        if not (asked if asked is not None else q.get("copyv", ["0"])[0] == "1"):
            return False
        return self.video_copies(src, q, height, mbit, burn)

    def video_copies(self, src, q, height, mbit, burn):
        """Whether an encode can pass the picture through: nothing burned into it, no
        size or rate asked for, and a picture the device decodes as it is."""
        if not src or not src.get("file") or burn is not None or mbit:
            return False
        if height and (src.get("height") or 0) > height:
            return False
        fmt = self.video_format(src["file"])
        pix = fmt.get("pix", "")
        if fmt.get("codec") == "h264":
            return pix in ("yuv420p", "yuvj420p")
        if fmt.get("codec") == "hevc":
            return (q.get("hevc", ["0"])[0] in ("1", "true", "yes")
                    and pix in ("yuv420p", "yuvj420p", "yuv420p10le"))
        return False

    @staticmethod
    def copy_start(path, offset):
        """Where a copied picture starts for a seek to this second. The keyframe list
        and ffmpeg's seek do not always agree, so ffmpeg is asked, with the stream's
        own flags, for the first frame it would copy."""
        import pd_gpu
        from fractions import Fraction
        tag = ("%.3f" % float(offset)).rstrip("0").rstrip(".")
        try:
            said = subprocess.run(
                [pd_gpu.FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-noaccurate_seek", "-ss", tag, "-copyts", "-i", path,
                 "-map", "0:v:0", "-c", "copy", "-frames:v", "1", "-f", "framecrc", "-"],
                capture_output=True, text=True, timeout=20, creationflags=pd_gpu.NO_WINDOW)
        except Exception:
            return None
        base = None
        for line in (said.stdout or "").splitlines():
            if line.startswith("#tb 0:"):
                try:
                    base = Fraction(line.split(":", 1)[1].strip())
                except (ValueError, ZeroDivisionError):
                    return None
            elif line and not line.startswith("#") and base is not None:
                try:
                    return round(float(base * int(line.split(",")[2].strip())), 3)
                except (IndexError, ValueError):
                    return None
        return None

    def encode_begins(self, q):
        """Where an encode asked for with these settings will start, in film time, and
        whether its picture is copied. An app asks before it opens the stream."""
        offset = float(q.get("offset", ["0"])[0] or 0)
        try:
            key = q.get("key", [""])[0]
            src = (S.local().file_for(key, int(q.get("mi", ["0"])[0] or 0))
                   or self.house_source(q)) \
                if q.get("src", [""])[0] == "local" else None
            height, mbit = self.capped(int(q.get("height", ["0"])[0] or 0),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            burn = self.burn_for(q, src) if self.burn_allowed_here(q) else None
            # the same decision the stream makes: asked separately, the two disagreed
            # and the subtitles were cut for a start the stream never had
            if not self.picture_goes_through(q, src, height, mbit, burn, asked=True):
                return {"at": offset, "copy": False}
            if offset <= 0:
                return {"at": 0, "copy": True}
            start = self.copy_start(src["file"], int(offset))
            # a start after the second asked for, or far before it, is not a seek
            # anybody should be handed
            if start is None or start > int(offset) + 0.01 or int(offset) - start > 30:
                return {"at": offset, "copy": False}
            return {"at": start, "copy": True}
        except Exception as e:
            return {"at": offset, "copy": False, "why": str(e)[:120]}

    #: whether a connected computer answered lately: address -> (when, answered)
    CONNECTED_UP = {}

    def encode_elsewhere(self, q):
        """Sent to the connected computer to encode, when this one is set to and it answers.

        The computer this one copies from, or the one copying from it. When it does not
        answer, this one encodes. A request already handed over is made where it lands,
        so two machines both set to hand over do not pass it back and forth. True when
        sent.
        """
        if q.get("handed", ["0"])[0] == "1":
            return False
        cfg = self.library_settings_plain() or {}
        if cfg.get("encodeOn") != "connected":
            return False
        import pd_follow
        import urllib.request as _ureq
        import urllib.error as _uerr
        one = pd_follow.settings(cfg)
        home = self.at_home()
        if one.get("on") and one.get("master"):
            doors = pd_follow.house_doors()
            check = one["master"]
            door = (doors.get("lan") or one["master"]) if home else (doors.get("outside") or "")
        else:
            other = self.standby_now() or {}
            check = other.get("where") or ""
            door = check if home else (other.get("outside") or "")
        if not (check and door):
            return False
        was = S.Handler.CONNECTED_UP.get(check)
        if was and time.time() - was[0] < 30:
            up = was[1]
        else:
            try:
                with _ureq.urlopen(check.rstrip("/") + "/where", timeout=2) as answer:
                    up = answer.status == 200
            except _uerr.HTTPError:
                up = True                 # it answered, if not to this question
            except Exception:
                up = False
            S.Handler.CONNECTED_UP[check] = (time.time(), up)
        if not up:
            return False
        self.send_response(307)
        self.send_header("Location", door.rstrip("/") + self.path +
                         ("&" if "?" in self.path else "?") + "handed=1")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    #: what the main server said about files this copy holds none of: (when, answer)
    HOUSE_SOURCES = {}

    def house_source(self, q):
        """A title this copy holds no file for, as the main server's file over the network.

        Played from this machine, it is encoded by this machine: the main server hands over
        the file, not the work. None when the file is here, when this machine follows
        nothing, or when the main server does not answer.
        """
        key = q.get("key", [""])[0]
        try:
            mi = int(q.get("mi", ["0"])[0] or 0)
            if S.local().file_for(key, mi):
                return None
        except Exception:
            return None
        import pd_follow
        one = pd_follow.settings(self.library_settings_plain() or {})
        if not (one.get("on") and one.get("master") and one.get("key")):
            return None
        mark = (key, mi)
        was = S.Handler.HOUSE_SOURCES.get(mark)
        # an answer is good for ten minutes; no answer is asked again in thirty seconds
        if was and time.time() - was[0] < (600 if was[1] else 30):
            said = was[1]
        else:
            try:
                said = pd_follow.ask(one, "/follow/source?key=%s&mi=%d"
                                     % (urllib.parse.quote(key), mi), 10)
            except Exception:
                said = None
            S.Handler.HOUSE_SOURCES[mark] = (time.time(), said)
        if not said or not said.get("part"):
            return None
        src = dict(said)
        src["file"] = "%s/local/parts/%d?t=%s" % (one["master"].rstrip("/"), int(said["part"]),
                                                  urllib.parse.quote(one["key"]))
        src["remote"] = True
        return src

    def answering_now(self, where):
        """Whether that machine is up, asked at most once every thirty seconds.

        The same question the handover asks, so a redirect is never written to a
        door nobody is behind: sent on to a main server that was switched off, the
        player had nothing to fail over to and simply stopped.
        """
        import urllib.request as _ureq
        import urllib.error as _uerr
        where = (where or "").rstrip("/")
        if not where:
            return False
        was = S.Handler.CONNECTED_UP.get(where)
        if was and time.time() - was[0] < 30:
            return was[1]
        try:
            with _ureq.urlopen(where + "/where", timeout=2) as answer:
                up = answer.status == 200
        except _uerr.HTTPError:
            up = True                     # it answered, if not to this question
        except Exception:
            up = False
        S.Handler.CONNECTED_UP[where] = (time.time(), up)
        return up

    def send_to_the_house(self, q):
        """A title this copy holds no file for, sent on to the main server.

        For subtitles, and for an encode when the main server's file cannot be read from
        here. An app that moved here while the main server was away keeps asking here.
        Redirected with the same query and key, by the door the viewer can reach;
        True when sent.
        """
        try:
            if S.local().file_for(q.get("key", [""])[0], int(q.get("mi", ["0"])[0] or 0)):
                return False
        except Exception:
            return False
        import pd_follow
        one = pd_follow.settings(self.library_settings_plain() or {})
        if not (one.get("on") and one.get("master")):
            return False
        doors = pd_follow.house_doors()
        house = ((doors.get("lan") or one["master"]) if self.at_home()
                 else doors.get("outside") or "")
        if not house:
            return False
        # and only if somebody is behind that door. A redirect to a machine that is
        # off is worse than no answer: the player follows it, finds nothing, and
        # stops - which is every shuffle drawing a title this copy does not hold
        # while the main server is away.
        if not self.answering_now(house):
            return False
        self.send_response(307)
        self.send_header("Location", house.rstrip("/") + self.path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def gpu_stream(self, q):
        """Pipe one ffmpeg's fragmented MP4 straight to the browser."""
        # A live encode has no byte N: asked to carry on from one - a player retrying
        # after the connection broke, a server restart - it used to start encoding
        # again at the offset in the address, and the player took that as the
        # continuation: the picture went back to wherever this encode began. Refused,
        # the player reopens at the second it has reached.
        got = re.match(r"bytes=(\d+)-", self.headers.get("Range") or "")
        if got and int(got.group(1)) > 0:
            try:
                pd_log.raw("%s stream %s asked again at byte %s: refused, the player "
                        "reopens at its own second%s"
                        % (time.strftime("%H:%M:%S"), q.get("key", [""])[0],
                           got.group(1), chr(10)))
            except OSError:
                pass
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.send_header("X-Palladium-Reason", "an encode cannot resume at a byte")
            self.end_headers()
            return
        # a television asking for the pipe is a mistake somewhere: it is the one thing
        # a receiver cannot reliably play, and knowing it asked explains the failure
        if self.game_holds("transcode"):
            self.send_error(503, "Game mode is on: this server is not encoding just now")
            return
        self.note_cast("asked for the PIPE, which it cannot play",
                       "key=%s" % q.get("key", [""])[0])
        try:
            key = q.get("key", [""])[0]
            mi = int(q.get("mi", ["0"])[0])
            # the library hands us the file itself; there is nothing else to ask
            src = ((S.local().file_for(key, mi, who=self.viewer()) or self.house_source(q))
                   if q.get("src", [""])[0] == "local" else None)
            # which version is being watched, for the copy machine's list
            if src and src.get("file") and not self.app_name().lower().startswith(
                    ("cache", "follower")):
                S.played_file(key, src["file"])
            height, mbit = self.capped(int(q.get("height", ["0"])[0]),
                                       self.wanted_rate(q), src,
                                       q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
            burn = self.burn_for(q, src) if self.burn_allowed_here(q) else None
            # the picture as it is, for an app that asked where such a stream starts
            copy = self.picture_goes_through(q, src, height, mbit, burn)
            begin = lambda mode: S.engine().start(
                                key,
                                audio_index=self.wanted_audio(q, src),
                                # a client that knows it cannot play 5.1 - headphones
                                # on a television box - asks for two channels
                                channels=max(0, int(q.get("ch", ["0"])[0] or 0)),
                                offset=int(float(q.get("offset", ["0"])[0])),
                                height=height, mbit=mbit,
                                media_index=mi,
                                burn_index=burn,
                                copy_video=copy,
                                src=src,
                                audio_mode=mode,
                                # loudness correction, applied by the encoder
                                gain_db=S.volume_gain(src.get("part") or src.get("id"),
                                                    src.get("file"),
                                                    src.get("size") or 0),
                                # the client saying it can decode HEVC, which means
                                # nothing has to be made smaller to reach it
                                hevc=q.get("hevc", ["0"])[0] in ("1", "true", "yes"),
                                # and whether the sound can go through as it lies:
                                # a set with a DTS decoder wants the track, not our
                                # AC-3 made out of it
                                dts=q.get("dts", ["0"])[0] in ("1", "true", "yes"),
                                # a burned track is part of the picture, so the size
                                # and position have to be applied while drawing it
                                # a burned track is part of the picture, so the look
                                # has to be applied while drawing it - this film's own
                                # a client that has zoomed the picture asks for the
                                # burned subtitles to be raised by what zoom will crop
                                sub_look=self.shifted_look(
                                    self.subtitle_settings(q.get("key", [""])[0],
                                                           q.get("device", ["web"])[0]),
                                    q.get("subshift", ["0"])[0]))
            wanted = q.get("audio", ["aac"])[0]
            # A file whose Dolby track has already come out empty is not asked twice.
            # The discovery costs the whole first-block wait and then a cold start -
            # fifteen seconds before a frame - and it would cost that on every play.
            if wanted == "passthrough" and self.no_dolby_here((src or {}).get("file", "")):
                wanted = "aac"
            # Sound as it is, or picture and sound both made. Never one copied
            # beside the other.
            #
            # Every torn picture seen here has been on that one path - the picture
            # handed over as it stands while the sound is remade beside it, the two
            # put back together into fragmented MP4 on the way out. A film sent as
            # the file itself has never torn, and one encoded whole has never torn.
            # The same episode plays perfectly and then tears, from the same file,
            # at the same second: it is not the bit depth, not the resolution, not
            # the container, and not where it is started from - all of those were
            # tried and each was the same on a file that was fine.
            #
            # So the path is not used. It is reached only when the sound has to be
            # remade and the picture need not be - a small part of any library, and
            # untouched for everything that goes out as the file itself, which is
            # what can be read off two machines at once and handed over when one goes.
            if wanted != "passthrough":
                copy = False
            # "Decoder: processor" on the settings page. The card is quicker and is
            # what this uses by default; the processor is there for a card that is
            # full, or busy with something else, or making a mess of a particular
            # file. It costs nothing to offer and it is always present.
            if q.get("engine", [""])[0] == "cpu":
                import pd_gpu
                with pd_gpu.on_the_cpu():
                    st = begin(wanted)
            else:
                st = begin(wanted)
        except Exception as e:
            # An encoder that will not start is the end of the evening on a machine
            # that has no encoder - the machine that keeps copies is a spare box with
            # no card in it, and a viewer handed to it got a five hundred and a
            # stopped picture. The file is there and most of what asks for an encode
            # could have played it as it stands, so send it: a player that truly
            # cannot decode it will say so, which is a better answer than nothing.
            if src and src.get("file") and os.path.exists(src["file"]):
                self.log_message("no encoder here (%s); sending the file as it is",
                                 str(e)[:120])
                self.send_file_ranged(src["file"])
                return
            # the main server's file, and this machine could not encode it: the main server can
            if src and src.get("remote") and self.send_to_the_house(q):
                return
            # Nothing here, and nowhere to send it. Said plainly rather than as a
            # crash: this copy holds a slice of the library, and the machine that
            # holds the rest is off. A five hundred here reads to a player as a
            # server that broke, and it retries; this is a plain no.
            if not (src and src.get("file")):
                self.send_error(404, "not on this machine, and the main server is away")
                return
            self.send_error(500, str(e)[:200])
            return
        # One device, one encode. A player that restarts - a change of soundtrack, a
        # seek, an error it recovers from - opens a new stream while the old one is
        # still being written; the old ffmpeg only stops when its connection dies,
        # which can be half a minute. Four of those at once had the card busy enough
        # that the next request timed out before its first byte, which is what a
        # casual episode refusing to start turned out to be.
        S.engine().stop_others(self.client_address[0],
                             q.get("device", ["web"])[0], st.info["id"])
        self.encode_said(st, q, src, height, mbit,
                         q.get("hevc", ["0"])[0] in ("1", "true", "yes"))
        # The first block, before a word of the answer is written. An encoder that
        # will not start - a filter graph that refuses to configure, a stream asked
        # for that the file does not hold - used to be answered with 200 and nothing
        # behind it, which the player can only read as a broken container. Read the
        # reason off ffmpeg's own output instead and say it.
        first = self.first_block(st)
        # Dolby is asked for by name and cannot always be given. On one file the muxer
        # took the AC-3 track and never wrote a byte - with delay_moov it waits for a
        # header it never manages, without it the header cannot be written at all -
        # and the episode simply did not start. AAC is the plain answer that every
        # device takes, so it is tried before giving up on the film.
        if not first and wanted == "passthrough":
            stalled = st.why()
            st.stop()
            try:
                pd_log.raw("%s no Dolby out of %s, trying plain sound: %s%s" % (
                    time.strftime("%H:%M:%S"), key, stalled, chr(10)))
            except Exception:
                pass
            self.note_no_dolby((src or {}).get("file", ""))
            try:
                st = begin("aac")
            except Exception as e:
                self.send_error(500, str(e)[:200])
                return
            first = self.first_block(st)
        if not first:
            said = st.why() or "the encoder stopped without saying why"
            st.stop()
            # The card may simply have had no room: a subtitle being written holds
            # eight gigabytes of it, and NVENC then refuses to start with nothing much
            # to say. The processor is slower and always available, so it is tried once
            # before anybody is told the film will not play.
            import pd_gpu
            if pd_gpu.HW_OK:
                try:
                    with pd_gpu.on_the_cpu():
                        st = begin("passthrough")
                        first = self.first_block(st)
                except Exception:
                    first = None
                if first:
                    said = ""
        if not first:
            self.file_report("error", "nothing came out of the encoder" + chr(10)
                             + "key=%s offset=%s burn=%s" % (
                                 key, q.get("offset", ["0"])[0],
                                 (q.get("burn") or ["none"])[0]) + chr(10) + said,
                             "server")
            try:
                pd_log.raw("%s encoder said nothing for %s: %s%s" % (
                    time.strftime("%H:%M:%S"), key, said, chr(10)))
            except Exception:
                pass
            self.send_error(502, "the encoder produced nothing: " + said[:120])
            return
        # Chunked, and therefore HTTP/1.1 - written by hand because the rest of the
        # server speaks 1.0 and the status line comes from a class attribute.
        #
        # An encode has no length to declare: it is made as it is sent. Sent as a
        # plain 1.0 body it ends when the socket closes, which makes a server going
        # away byte-for-byte identical to the film finishing - the player saw no
        # error, ran no recovery, and offered the next episode to somebody twenty
        # minutes into this one. Chunked says how long each piece is and ends with a
        # piece of length nought, so a stream that stops without that last piece is a
        # fault the player can see, and the recovery it already has for a dropped
        # direct play runs for an encode too.
        # In the dialect the client asked in. Chunked belongs to HTTP/1.1: a client
        # that asked in 1.0 has never been promised it and would read the lengths as
        # picture. Everything with a player in it - the app, the browsers, the
        # television - asks in 1.1; this is for whatever does not.
        chunked = str(getattr(self, "request_version", "")).upper() >= "HTTP/1.1"
        crlf = chr(13) + chr(10)
        head = crlf.join([
            "HTTP/1.1 200 OK" if chunked else "HTTP/1.0 200 OK",
            "Content-Type: video/mp4",
            "Cache-Control: no-store",
            "X-Palladium-Engine: " + str(st.info["engine"]),
        ] + (["Transfer-Encoding: chunked"] if chunked else []) + [
            "Connection: close",
            "", ""])
        self.wfile.write(head.encode("latin-1", "replace"))
        # said in the headers, and meant here: this socket carries one answer
        self.close_connection = True
        end_mark = crlf.encode()
        sid = S.WATCHING.start(
            self.watcher(),
            S.local().title_for(key) if q.get("src", [""])[0] == "local" else key,
            self.quality(src),
            ("direct video, sound encoded" if st.info.get("copyVideo")
             else "transcode (%s)" % st.info.get("engine", "?")),
            self.client_address[0], key, self.app_name(), self.device_kind())

        def piece(block):
            """One chunk: its length in hexadecimal, the bytes, and a blank line.

            Or just the bytes, for a client that asked in 1.0 - which is exactly what
            this endpoint did for everybody until now.
            """
            if not chunked:
                self.wfile.write(block)
                return
            self.wfile.write(("%x" % len(block)).encode("ascii") + end_mark)
            self.wfile.write(block)
            self.wfile.write(end_mark)

        whole = False
        try:
            piece(first)
            S.WATCHING.sent(sid, len(first))
            while True:
                chunk = st.proc.stdout.read(65536)
                if not chunk:
                    break
                piece(chunk)
                S.WATCHING.sent(sid, len(chunk))
            # the film itself ended: said in the one way that means it
            if chunked:
                self.wfile.write(b"0" + end_mark + end_mark)
            whole = True
        except Exception:
            pass                      # the browser closed the connection: normal on seek
        finally:
            if not whole:
                self.transcode_ended(st, key, q)
            S.WATCHING.stop(sid)
            st.stop()
            if not whole:
                # no terminator was written, which is what tells the player this was
                # an interruption and not an ending
                self.close_connection = True

    def transcode_ended(self, st, key, q):
        """One dated line for a transcode that stopped before its film did: who, how far
        in, whether the encoder was still running, how fast it had been going, and what
        else had the card - so a viewer thrown onto the other machine leaves a reason."""
        try:
            import pd_credits
            import pd_gpu
            ran = time.time() - st.started
            try:
                where = float((q.get("offset") or ["0"])[0] or 0)
            except ValueError:
                where = 0.0
            card = S.Handler.card_now() or {}
            others = []
            if pd_credits.working():
                others.append("credits analyser")
            if S.SUBCHECK_BUSY.get("on"):
                others.append("subtitle check")
            line = ("%s transcode ended early: %s to %s [%s] %.0fs in, from %.0fs, "
                    "encoder %s, speed %s%s, card %s%%%s%s" % (
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        str(st.info.get("title") or key)[:50], self.client_address[0],
                        self.app_name() or "?", ran, where,
                        "still running" if st.alive() else
                        "stopped (%s)" % (st.why() or "no message"),
                        "/".join("%.2fx" % v for v in st.speeds()) or "unknown",
                        pd_gpu.waited_on_player(st.made(), ran),
                        card.get("busy", "?"),
                        ", also on the card: " + " and ".join(others) if others else "",
                        chr(10)))
            pd_log.raw(line)
        except Exception:
            pass

    @staticmethod
    def burn_for(q, src):
        """The subtitle to burn in, but only if this copy holds it.

        A client asks by stream number, and a number means something different in
        another file. Two copies of one film, a player left running while the list
        was reordered, or a rescan: the request arrives for stream 3 of a file whose
        only subtitle is stream 2, ffmpeg finds nothing to overlay and dies, and the
        television reports a container it cannot parse. Playing without the subtitle
        is the better answer.
        """
        if not q.get("burn"):
            return None
        try:
            want = int(q["burn"][0])
        except (TypeError, ValueError):
            return None
        held = (src or {}).get("subs")
        if held is not None and want not in held:
            return None
        return want

    def burn_allowed_here(self, q):
        """A burn asked for from outside the main server, when the owner has not allowed it.

        Refused rather than obeyed: the film plays without the subtitle, which is the
        same answer the picker gives, and a page opened before the setting changed
        cannot spend an encode on it.
        """
        if self.may_burn():
            return True
        # the app draws these itself and never asks for a burn; anything that does
        # ask from away is the browser
        return False

    @classmethod
    def link_seen(cls, total, side="home"):
        """High-water mark per side, halved after 10 quiet minutes.

        A mark that never falls measures the best night this machine ever had. One
        mark per side: LAN and WAN differ by an order of magnitude, and a single
        number took the larger of the two.
        """
        now = time.time()
        mark = cls.LINK.setdefault(side, {"seen": 0.0, "when": 0.0})
        if total > mark["seen"]:
            cls.LINK[side] = {"seen": float(total), "when": now}
        elif now - (mark["when"] or 0) > 600:
            cls.LINK[side] = {"seen": max(0.0, mark["seen"] * 0.5), "when": now}
        return cls.LINK[side]["seen"]

    #: seconds of buffer each viewer reports, and when. The one measure that reads
    #: the same for direct play and for a transcode.
    AHEAD = {}
    #: A fed player holds ~50 s. Under 40 the ceiling stops climbing, under 30 it
    #: halves, under 20 it drops to the floor.
    FED_AHEAD = 50.0
    EASE_AHEAD = 40.0
    THIN = 30.0
    PANIC_AHEAD = 20.0
    #: A transcode holds whatever the encoder is ahead by, so a steady 8 s is
    #: healthy. Direction separates the two; below this, falling or not, it is starved.
    STALLED_AHEAD = 5.0

    @staticmethod
    def how_thin(now=None, side=None):
        """Worst recent report: "", "ease", "back" or "panic".

        Filtered to one side when given: a thin buffer on the WAN says nothing about
        a copy between two machines on the LAN.
        """
        now = now or time.time()
        worst = ""
        for said in list(S.Handler.AHEAD.values()):
            ahead, when = said[0], said[1]
            where = said[2] if len(said) > 2 else None
            if side and where and where != side:
                continue
            if now - when >= 12 or ahead < 0:
                continue
            before = said[3] if len(said) > 3 else -1.0
            # the player reports a stall: no measurement overrides that
            if len(said) > 4 and said[4]:
                return "panic"
            # a transcode's depth is the encoder's lead: only its stalls count
            if len(said) > 5 and said[5]:
                continue
            # steady or rising and above the floor: fed at whatever height this way
            # of watching holds, and taking megabits off a copy would not raise it
            if (ahead > S.Handler.STALLED_AHEAD
                    and not (before >= 0 and ahead < before - 1.0)):
                continue
            if ahead < S.Handler.PANIC_AHEAD:
                return "panic"
            if ahead < S.Handler.THIN:
                worst = "back"
            elif ahead < S.Handler.EASE_AHEAD and worst != "back":
                worst = "ease"
        return worst

    @staticmethod
    def running_dry(now=None, side=None):
        """True when a report is at "back" or "panic"."""
        return S.Handler.how_thin(now, side) in ("back", "panic")

    @classmethod
    def disk_reading_slow(cls, now=None):
        """True when reads were lately spending their time waiting on disk.

        Share of each block's time spent reading rather than sending, measured while
        serving. An earlier signal than a viewer's buffer, and it costs nothing.
        """
        now = now or time.time()
        return (now - (cls.DISK.get("when") or 0) < 12
                and (cls.DISK.get("busy") or 0) > cls.DISK_BUSY)

    @staticmethod
    def a_film_is_struggling(rows, side=None):
        """Whether anybody watching is getting less than they were getting.

        Measured against each viewer's own average rather than against the bitrate of
        what they are watching: a direct play reads in bursts and idles between them,
        so "below what the film needs" is true of a healthy one half the time. A rate
        that has fallen away from its own average has not.

        A viewer with nothing open is holding a full buffer, which is the opposite of
        starved, and the first seconds of a viewing have no average worth the name.
        """
        # the players' own reports first: true before the picture stops, and it
        # reads the same for direct play and for a transcode
        if S.Handler.running_dry(side=side):
            return True
        now = time.time()
        dipping = set()
        for r in rows:
            if r.get("how") == "syncing" or r.get("holding"):
                continue
            # A player paused or stopped is not short of anything: it has stopped asking,
            # so its rate falls under its own average. Counted as starving, one film left
            # paused on a television held every rip, copy and comparison on its drive for
            # hours (2026-11-14).
            if str(r.get("state") or "") in ("paused", "stopped"):
                continue
            if int(r.get("seconds") or 0) < 20:
                continue                  # too new to have an average
            # a player that reports its buffer is judged by that alone: its rate
            # falls at every scene change, and how_thin above has already read it
            if S.Handler.buffer_said(r.get("address"), now):
                continue
            average = float(r.get("average") or 0) * 8
            if average <= 0.5:
                continue
            if float(r.get("mbit") or 0) < average * S.Handler.STARVED:
                who = "%s/%s" % (r.get("who"), r.get("address"))
                dipping.add(who)
                # and for DIP_FOR seconds running, not one
                since = S.Handler.DIPS.setdefault(who, now)
                if now - since >= S.Handler.DIP_FOR:
                    S.Handler.DIPS = {k: v for k, v in S.Handler.DIPS.items() if k in dipping}
                    return True
        S.Handler.DIPS = {k: v for k, v in S.Handler.DIPS.items() if k in dipping}
        return False

    #: when each viewer's rate first fell under their average, while it stays there
    DIPS = {}
    #: how often a copy going out looks at the viewers and its ceiling, in seconds
    COPY_LOOK_EVERY = 0.25
    #: seconds a copy stops at a time for a viewer short of disk, well inside the reader's patience
    COPY_HOLD_MOST = 45

    #: how long a dip must last to count: a scene change lasts several seconds
    DIP_FOR = 10.0

    @staticmethod
    def buffer_said(address, now=None):
        """Whether the screen at this address reported its buffer lately."""
        now = now or time.time()
        return any(str(key).endswith("/" + str(address)) and now - said[1] < 12
                   for key, said in list(S.Handler.AHEAD.items()))

    def stream_buffer(self):
        """Buffer report from a player, and the split of what carried the film.

        Answered on GET and POST. It existed on GET only and read the seconds from a
        request body a GET does not have, so every POST returned 404 into the caller's
        catch and AHEAD stayed empty. Seconds come from the body, else the query.
        """
        said = self.read_json() or {}
        args = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                if "?" in self.path else {})
        # any player can report it, direct or transcoded; the sync ceiling backs off
        # while anybody is low
        try:
            ahead = float(said.get("ahead", (args.get("ahead") or [None])[0]))
        except (TypeError, ValueError):
            ahead = -1.0
        if ahead >= 0:
            if len(S.Handler.AHEAD) > 200:
                S.Handler.AHEAD.clear()
            now = time.time()
            # the previous report from the same screen: a buffer that is not moving is
            # being held, at whatever height that way of watching holds
            # keyed per screen: under the viewer's name alone, one person watching on
            # two devices overwrote their own last report
            who = self.watcher() + "/" + str(self.client_address[0])
            had = S.Handler.AHEAD.get(who)
            before = had[0] if (had and now - had[1] < 30) else -1.0
            # and whether it stalled: depth is a guess, a stall is not
            stalled = bool(said.get("stalled")
                           or (args.get("stalled") or [""])[0] in ("1", "true"))
            # being encoded for this screen: its buffer holds what the encoder is
            # ahead by, a few seconds that read as running dry when they are not
            encoded = any(str(r.get("address")) == str(self.client_address[0])
                          and "transcode" in str(r.get("how") or "")
                          for r in S.WATCHING.snapshot())
            S.Handler.AHEAD[who] = (
                ahead, now, S.Handler.side_of(self.client_address[0]), before, stalled,
                encoded)
        if len(S.Handler.CARRIED) > 200:
            S.Handler.CARRIED.clear()
        # and the per-machine split, for the player's info line: the client cannot
        # tell which server a block came from
        mine, theirs, _ = S.Handler.CARRIED.get(self.watcher()) or (0, 0, 0)
        out = {"ok": True, "ahead": ahead}
        if theirs > 0:
            whole = float(mine + theirs)
            out["split"] = [int(round(mine * 100 / whole)),
                            int(round(theirs * 100 / whole))]
            out["with"] = (self.standby_now() or {}).get("name") or ""
        # what this viewing has taken from this machine altogether - the number the
        # panel and the lists show, so the player can say the same one
        key = str(said.get("key") or (args.get("key") or [""])[0])
        if key:
            got = S.WATCHING.sitting.get((self.watcher(), key, self.device_kind()))
            if got:
                out["mb"] = round(got[1] / 1048576.0, 1)
        self.reply_json(out)
