#!/usr/bin/env python3
"""The transcode engine: ffmpeg, on the graphics card where there is one.

Only for what a screen cannot play as it stands. ffmpeg decodes on the GPU (NVDEC),
scales there, and encodes with NVENC - measured at 16x realtime for 4K to 1080p
against 2.4x on the processor. Everything else direct plays, which costs nothing.

The library hands in the path of the file to play; this runs one ffmpeg per session
and hands back a byte stream.

Design notes worth knowing:
  * the source files are mostly 10-bit HEVC, and h264_nvenc cannot encode 10-bit, so
    the pixel format is converted on the GPU (scale_cuda=format=yuv420p);
  * audio keeps its channel count (5.1 stays 5.1) rather than being downmixed;
  * output is fragmented MP4 down a single pipe rather than DASH or HLS segment files.
    ffmpeg's dash muxer proved erratic here - it would encode at 20x realtime while
    flushing no segments at all - whereas a pipe is one process, one response, and the
    browser plays it natively with no MSE library in the way. Seeking restarts the
    encode at the new offset, which is what a transcode does anyway.
"""
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time

# The server has no console of its own, so anything it starts is handed a fresh one by
# Windows - a black window flashing over whatever is on screen. Not a flag elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SESSION_IDLE_S = 60          # no ping for this long -> kill ffmpeg
SEG_SECONDS = 4


#: The hardware encoders worth trying, best first. Each says how to reach the card,
#: what to scale with, and how the picture reaches an overlay filter.
#:
#: Only Nvidia keeps the frames on the card the whole way. For the others the decode is
#: left to "-hwaccel auto" - it uses D3D11VA, VAAPI or VideoToolbox where it can and
#: falls back to the processor where it cannot - and the frames arrive in system memory,
#: which is where their scalers and encoders want them anyway. The encode is the
#: expensive part and it is the part that runs on the card either way.
ENCODERS = [
    dict(name="nvenc", label="NVENC", codec="h264_nvenc",
         hwaccel=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
         scale="scale_cuda", download="hwdownload,format=p010le,format=yuv420p",
         preset=["-preset", "p5"]),
    dict(name="qsv", label="Quick Sync", codec="h264_qsv",
         hwaccel=["-hwaccel", "auto"], scale=None, download=None,
         preset=["-preset", "medium"]),
    dict(name="amf", label="AMF", codec="h264_amf",
         hwaccel=["-hwaccel", "auto"], scale=None, download=None,
         preset=["-quality", "balanced"]),
    dict(name="videotoolbox", label="VideoToolbox", codec="h264_videotoolbox",
         hwaccel=["-hwaccel", "auto"], scale=None, download=None, preset=[]),
]


def rate_args(enc, mbit, ceiling):
    """How to ask this encoder for a bitrate, or for a quality with a ceiling.

    Asked by name for megabits, every one of them has to be told plainly, or it treats
    the number as advice: NVENC with a quality target set wrote 11.4 Mbit against a
    ceiling of 6.
    """
    if enc is None:
        return (["-crf", "23", "-maxrate", "%dM" % mbit,
                 "-bufsize", "%dM" % max(2, mbit * 2)] if mbit else ["-crf", "20"])
    name = enc["name"]
    if mbit:
        peak = max(1, int(mbit * 1.5))
        if name == "nvenc":
            return ["-rc", "vbr", "-b:v", "%dM" % mbit, "-maxrate", "%dM" % peak,
                    "-bufsize", "%dM" % max(2, mbit * 2)]
        if name == "amf":
            return ["-rc", "vbr_peak", "-b:v", "%dM" % mbit, "-maxrate", "%dM" % peak,
                    "-bufsize", "%dM" % max(2, mbit * 2)]
        return ["-b:v", "%dM" % mbit, "-maxrate", "%dM" % peak,
                "-bufsize", "%dM" % max(2, mbit * 2)]
    if name == "nvenc":
        return ["-cq", "19", "-b:v", "0", "-maxrate", "%dM" % ceiling,
                "-bufsize", "%dM" % (ceiling * 2)]
    if name == "qsv":
        return ["-global_quality", "23", "-maxrate", "%dM" % ceiling,
                "-bufsize", "%dM" % (ceiling * 2)]
    if name == "amf":
        return ["-rc", "cqp", "-qp_i", "22", "-qp_p", "24",
                "-maxrate", "%dM" % ceiling, "-bufsize", "%dM" % (ceiling * 2)]
    return ["-q:v", "55"]                        # VideoToolbox counts the other way


def _encodes(exe, codec):
    """Can this build, on this machine, actually encode with that codec?

    Listing it in -encoders proves nothing: a build carries every encoder it was
    compiled with, and the card, the driver or the licence decides the rest. One second
    of a test pattern settles it.
    """
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc=size=320x240:rate=15", "-t", "1",
                            "-c:v", codec, "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def find_ffmpeg():
    """An ffmpeg build, and the best encoder this machine will actually run.

    Nvidia first because it is what this server was built on, then Intel, then AMD,
    then Apple; a build that cannot reach any of them still transcodes, on the
    processor, at a fifth of the speed.
    """
    candidates = []
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    # what the first-run wizard fetched, if it did: named rather than searched for,
    # because the papers folder is the server's to know and not this file's
    told = os.environ.get("PALLADIUM_FFMPEG") or ""
    if told and os.path.exists(told):
        candidates.append(told)
    # Beside the program first: that is where the installer drops one, and where
    # somebody who would rather choose their own build puts it.
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if os.path.exists(beside):
        candidates.append(beside)
    # then on PATH, which is how every package manager on every system installs it -
    # apt, brew, chocolatey, or a folder somebody added by hand. Looking only in
    # WinGet's folder found nothing on any machine that had not used WinGet.
    onpath = shutil.which("ffmpeg")
    if onpath:
        candidates.append(onpath)
    if os.name == "nt":
        winget = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
        for root, dirs, files in os.walk(winget):
            if "ffmpeg.exe" in files and root.endswith("bin"):
                candidates.append(os.path.join(root, "ffmpeg.exe"))
            if root.count(os.sep) - winget.count(os.sep) > 3:
                dirs[:] = []
        tmp = os.path.join(os.environ.get("TEMP", ""), "ffmpeg7")
        for root, _, files in os.walk(tmp):
            if "ffmpeg.exe" in files:
                candidates.append(os.path.join(root, "ffmpeg.exe"))
    for enc in ENCODERS:
        for exe in candidates:
            if _encodes(exe, enc["codec"]):
                return exe, enc
    return (candidates[0], None) if candidates else (None, None)


FFMPEG, ENCODER = find_ffmpeg()


def rescan():
    """Look for ffmpeg again, after somebody has just put one there.

    The search runs once at import, which is right - it walks folders. But a server
    that fetched ffmpeg a moment ago must not have to be restarted to see it.
    """
    global FFMPEG, ENCODER, HW_OK, ENGINE_NAME
    FFMPEG, ENCODER = find_ffmpeg()
    HW_OK = ENCODER is not None
    ENGINE_NAME = ENCODER["label"] if HW_OK else "CPU x264"
    return FFMPEG

#: True when anything at all encodes on a card. The rest of the file asks this rather
#: than asking which card it is.
HW_OK = ENCODER is not None

#: What to call it in a status line, a header and the player's information panel.
ENGINE_NAME = ENCODER["label"] if HW_OK else "CPU x264"


import contextlib


@contextlib.contextmanager
def on_the_cpu():
    """Encode without the card for the length of this block.

    The card can be full - a subtitle being written from a film's sound holds eight
    gigabytes of it - and NVENC then refuses to start, saying very little. The
    processor is slower and always there, which is better than telling somebody the
    film will not play.
    """
    global HW_OK
    was = HW_OK
    HW_OK = False
    try:
        yield
    finally:
        HW_OK = was

#: Whether the card can lay one picture over another. Asked once, of the ffmpeg in
#: hand, because it is a build option rather than a property of the card: an ffmpeg
#: without it still burns subtitles, only through system memory.
_CUDA_OVERLAY = None


def cuda_overlay():
    """True when overlay_cuda is available, so a burn never leaves the card."""
    global _CUDA_OVERLAY
    if _CUDA_OVERLAY is None:
        _CUDA_OVERLAY = False
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=NO_WINDOW).stdout
            _CUDA_OVERLAY = ("overlay_cuda" in out and "hwupload_cuda" in out)
        except Exception:
            _CUDA_OVERLAY = False
    return _CUDA_OVERLAY


class Stream:
    """One running ffmpeg, piping fragmented MP4 to one HTTP response."""

    def __init__(self, proc, info, log):
        self.proc = proc
        self.info = info
        self.log = log
        self.started = time.time()

    def alive(self):
        return self.proc.poll() is None

    def why(self):
        """What ffmpeg said before it stopped, in one line.

        A filter graph that will not configure, a file that moved, a stream that is
        not there: ffmpeg writes the reason and exits, and without this the client is
        handed an empty body and left to call it a broken container.
        """
        try:
            self.log.flush()
        except Exception:
            pass
        try:
            with open(self.log.name, "rb") as f:
                f.seek(max(0, os.path.getsize(self.log.name) - 2000))
                said = f.read().decode("utf-8", "replace")
        except Exception:
            return ""
        lines = [ln.strip() for ln in said.splitlines() if ln.strip()]
        # the first complaint is the cause; the ones after it are its consequences
        return lines[0][:200] if lines else ""

    def stop(self):
        try:
            if self.alive():
                self.proc.terminate()
                self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.log.close()
        except Exception:
            pass


class Engine:
    """Runs ffmpeg over a file the library hands in, and returns a byte stream."""

    def __init__(self, workdir):
        self.root = workdir
        os.makedirs(self.root, exist_ok=True)
        self.streams = {}
        self.lock = threading.Lock()

    # what "leave it where it was authored" means; the setting shifts relative to this
    SUB_HOME = 0.08
    # A picture subtitle is authored around 4.5% of the frame height, and 100% means
    # 5% of it everywhere else (SUB_BASE in pd-server.py). Burning it in is therefore
    # a small enlargement rather than the shrink it used to be, so that one number
    # means one size whether the text is drawn by a client or painted into the frame.
    SUB_BURN_MATCH = 1.11

    def burn_chain(self, burn_index, look, height=0, src=None):
        """The filter that lays a bitmap subtitle over the picture, sized and placed.

        With the settings at their defaults and nothing to resize this is the plain
        overlay it always was - no scaling, no offset, and no cost.

        When the picture is being made smaller the resize happens on the card, before
        the frames come down: a quarter of the pixels to copy and a quarter to overlay.
        The subtitle plane is scaled by the same ratio, so the text keeps the size and
        the place it has against the picture.
        """
        tall = (src or {}).get("height") or 0
        ratio = (float(height) / tall) if (height and tall and height < tall) else 1.0
        size = float((look or {}).get("size", 1.0) or 1.0) * self.SUB_BURN_MATCH * ratio
        shift = float((look or {}).get("position", self.SUB_HOME)) - self.SUB_HOME
        # Everything on the card. The picture never comes down, the subtitle goes up,
        # and the blend is the card's - which is where the processor was going: it was
        # copying every frame out of the card, mixing a bitmap into it and pushing it
        # back, for as long as the film ran. Measured on a 4K scope film with its
        # sound: 0.29x realtime through system memory, 0.98x on the card.
        #
        # The plane is scaled before it is uploaded, by the same factor and with the
        # same placement as the other path, so the text lands where it always did.
        if HW_OK and cuda_overlay() and (ENCODER or {}).get("scale"):
            base = "%s=%sformat=yuv420p" % (
                ENCODER["scale"], ("-2:%d:" % int(height)) if ratio < 1.0 else "")
            return ("[0:v:0]%s[v];"
                    "[0:%d]scale=iw*%.2f:ih*%.2f,format=yuva420p,hwupload_cuda[s];"
                    "[v][s]overlay_cuda=x=(W-w)/2:y=H-h-(H*%.3f)[out]"
                    % (base, int(burn_index), size, size, shift))
        # Frames on the card have to come down for the overlay filter; frames that
        # were never on it are already where the filter wants them.
        pull = (ENCODER or {}).get("download")
        if ratio < 1.0 and (ENCODER or {}).get("scale"):
            # Sized on the card, so what comes down is already 8-bit and small. The
            # format after hwdownload is not decoration: without it the filter asks
            # the overlay what it wants, the overlay wants alpha, and a CUDA frame
            # cannot be downloaded as yuva420p - the graph refuses to configure and
            # ffmpeg dies before the first frame.
            head = "[0:v:0]%s=-2:%d:format=yuv420p,hwdownload,format=yuv420p[v]" % (
                ENCODER["scale"], int(height))
        elif ratio < 1.0:
            head = "[0:v:0]%s,scale=-2:%d[v]" % (pull if pull else "null", int(height))
        else:
            head = "[0:v:0]%s[v]" % (pull if pull else "null")
        if abs(size - 1.0) < 0.02 and abs(shift) < 0.005:
            return "%s;[v][0:%d]overlay[out]" % (head, int(burn_index))
        # Scaled against its own size, which is where it was before I measured a
        # subtitle plane against the picture instead and made the text enormous on
        # exactly the film the change was meant to fix. The disc's plane and the
        # video are usually the same size, and where they are not the honest answer
        # is a measurement of the drawn text, not another guess at the canvas.
        # scale the plane, then sit its bottom edge where the shift asks for
        return ("%s;[0:%d]scale=iw*%.2f:ih*%.2f[s];"
                "[v][s]overlay=x=(W-w)/2:y=H-h-(H*%.3f)[out]"
                % (head, int(burn_index), size, size, shift))

    def command(self, src, offset, height, burn_index=None, audio_mode="aac",
                sub_look=None, audio_index=None, mbit=0, channels=0, hevc=False,
                dts=False, copy_video=False):
        # 10-bit HEVC has to come down to 8-bit: no h264 encoder on any of these
        # cards takes it. On Nvidia that happens on the card, in the same filter that
        # resizes; elsewhere the frames are in system memory and this is an ordinary
        # scale.
        # No 4K h264. These boxes decode HEVC and VP9 at 2160 lines and h264 at 1080,
        # so a 4K h264 stream is a picture the device cannot keep up with - which is
        # what a 4K film looked like the moment a subtitle forced an encode. Capped on
        # width, because a scope film is 3840 by 1600 and 1600 lines is still 4K wide.
        # It is also twice as fast to make: 9.2x realtime against 4.4x, measured.
        # HEVC where the client says it can decode it: the same box that stumbles
        # over 4K h264 takes 4K HEVC without noticing, which is how these files play
        # untouched in the first place. Then nothing has to be made smaller.
        hevc = bool(hevc) and (ENCODER or {}).get("name") == "nvenc"
        if not hevc and (ENCODER or {}).get("codec", "").startswith("h264"):
            wide, tall = src.get("width") or 0, src.get("height") or 0
            if wide > 1920 and tall:
                fits = int(round(tall * 1920.0 / wide)) // 2 * 2
                height = min(height or fits, fits)
        smaller = bool(height and src.get("height") and src["height"] > height)
        if (ENCODER or {}).get("scale"):
            scale = "%s=%sformat=yuv420p" % (
                ENCODER["scale"], ("-2:%d:" % height) if smaller else "")
        else:
            scale = ("scale=-2:%d,format=yuv420p" % height) if smaller else "format=yuv420p"
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
        # Burning a bitmap subtitle (PGS/VobSub) needs the picture in system memory for
        # the overlay filter, but the decode can still happen on the GPU: pull the frames
        # down as p010 (hwdownload rejects nv12 for 10-bit sources, which is most of this
        # library) and convert. That measured 11.6x realtime against 6.8x for decoding on
        # the CPU. Text tracks never come through here - they are extracted separately.
        if HW_OK and not copy_video:
            cmd += list(ENCODER["hwaccel"])
        if copy_video and offset:
            # both tracks from the keyframe the seek lands on: an accurate seek starts
            # the sound at the second asked for and the copied picture earlier, and
            # fragmented MP4 loses the difference, so the sound ran ahead
            cmd += ["-noaccurate_seek"]
        if offset:
            # to the fraction: the segment grid is not a whole number of seconds, and
            # rounding here would put a seek a second or two from where it was asked
            cmd += ["-ss", ("%.3f" % float(offset)).rstrip("0").rstrip(".")]
        if str(src["file"]).startswith(("http://", "https://")):
            # the house's file: a dropped connection is picked up where it broke
            cmd += ["-reconnect", "1"]
        cmd += ["-i", src["file"]]
        # the soundtrack asked for, by its number in the file; the first one when
        # nobody has said otherwise
        sound = "0:%d" % int(audio_index) if audio_index is not None else "0:a:0"
        if burn_index is None:
            cmd += ["-map", "0:v:0", "-map", sound]
        else:
            cmd += ["-filter_complex",
                    self.burn_chain(burn_index, sub_look, height, src),
                    "-map", "[out]", "-map", sound]
        # Constant quality rather than a flat ceiling - sharper where it matters -
        # but never more than the film itself was made with.
        #
        # Re-encoding something already compressed at a high quality target spends far
        # more than the original did: a 720p episode of six megabits went out at
        # twenty and more, which is worse than the film it was made from and floods
        # any line thinner than the house's own. The film's own bitrate is the ceiling
        # unless somebody has asked for a number, with a little room above it for a
        # busy scene and a floor so a very small file still has something to work with.
        made = float(src.get("bitrate") or 0) / 1000.0        # kbit in the library
        if made > 0:
            ceiling = max(2, min(40, int(made * 1.2 + 0.5)))
        else:
            ceiling = 40 if (src.get("height") or 1080) > 1080 else 24
        # A number of megabits asked for by name is a different mode: quality drives
        # the picture until somebody says how much line there is, and then the line
        # does. -cq has to go, or NVENC treats the bitrate as advice and overruns it.
        rate = rate_args(ENCODER, mbit, ceiling)
        codec = "hevc_nvenc" if hevc else ENCODER["codec"] if HW_OK else "libx264"
        # without hvc1 an HEVC track in MP4 is written as hev1, which several players
        # - the television's among them - will not open
        tag = ["-tag:v", "hvc1"] if hevc else []
        if copy_video:
            # the picture as it is: the device plays it, only the sound needed making
            cmd += ["-c:v", "copy"] + (
                ["-tag:v", "hvc1"]
                if str(src.get("videoCodec") or "").lower() in ("hevc", "h265") else [])
        elif HW_OK and burn_index is not None:
            # the overlay filter has already sized the picture
            cmd += ["-c:v", codec] + list(ENCODER["preset"]) + rate + tag
        elif HW_OK:
            cmd += ["-vf", scale, "-c:v", codec] + list(ENCODER["preset"]) + rate + tag
        else:                                       # same output, just slower
            vf = "scale=-2:%d" % height if height and (src.get("height") or 0) > height else "null"
            cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast"] + rate
        # Audio depends on where it is going. Chrome has no Dolby decoder, so the web
        # client asks for AAC; a television or an Android device would rather have the
        # original, and copying it is both better and cheaper than re-encoding.
        # what the file has, unless the client has said what it can play. Headphones
        # are the case: nothing passes through a Bluetooth pair, the box has to decode
        # and downmix, and some of them answer that by playing nothing at all.
        ch = channels or (6 if (src.get("audioChannels") or 2) >= 6 else 2)
        acodec = (src.get("audioCodec") or "").lower()
        # DTS as it lies, for a set that decodes it - which is most televisions and
        # every receiver, and almost no phone. Copying it is better than the AC-3 we
        # make from it in every way: nothing is decoded, nothing is re-encoded, and
        # the track that arrives is the one on the disc.
        passes = ("ac3", "eac3") + (("dts",) if dts else ())
        copying = audio_mode == "passthrough" and acodec in passes
        if copying:
            cmd += ["-c:a", "copy"]                       # bit for bit, Dolby intact
            dolby = True
        elif audio_mode == "passthrough" and ch == 6 and not channels:
            cmd += ["-c:a", "ac3", "-ac", "6", "-b:a", "640k"]   # DTS and TrueHD land here
            dolby = True
        else:
            cmd += ["-c:a", "aac", "-ac", str(ch), "-b:a", "448k" if ch == 6 else "192k"]
            dolby = False
        # delay_moov whenever the track being written is Dolby, copied or encoded here.
        # An AC-3 track cannot be described until its first packets exist, and ffmpeg
        # will not write the header without it - "Cannot write moov atom before AC3
        # packets", followed by no film at all. That is what a DTS source became on a
        # television, where the sound is re-encoded to AC-3 rather than copied.
        #
        # It stays off for AAC, where it is not merely unnecessary: it once hung the
        # muxer outright on a film with four commentary tracks.
        flags = "frag_keyframe+empty_moov+default_base_moof"
        if dolby:
            flags += "+delay_moov"
        cmd += ["-movflags", flags,
                "-frag_duration", "2000000", "-f", "mp4", "pipe:1"]
        return cmd

    def hls_command(self, src, folder, offset, height, burn_index=None,
                    sub_look=None, audio_index=None, start_number=0, ts_offset=0.0,
                    mbit=0):
        """The same encode, written as MPEG-TS segments with a playlist beside them.

        Safari is the reason this exists. MPEG-TS rather than fragmented MP4 segments
        because every iOS that has ever existed can read it, and AAC rather than Dolby
        because a phone has no Dolby decoder and would otherwise play silence.
        """
        cmd = self.command(src, offset, height, burn_index, "aac", sub_look,
                           audio_index, mbit)
        # Sized for the screen it is going to. The pipe's quality costs nothing to
        # store; these segments are written to disk, and at nineteen megabits a second
        # a two-hour film is seventeen gigabytes of temporary files.
        # The video flags are written out rather than amended. With -cq set, NVENC is
        # in constant-quality mode and treats a bitrate as advice: asked for five
        # megabits with a ceiling of eight it wrote eleven and a half. Variable
        # bitrate has to be asked for by name.
        cut = cmd.index("-c:v")
        end = cut + 2
        while end < len(cmd) and cmd[end] in ("-preset", "-cq", "-b:v", "-maxrate",
                                              "-bufsize", "-crf", "-rc"):
            end += 2
        if HW_OK:
            # no quality target here: with one set, the encoder drives from that and
            # the bitrate becomes advice again - it wrote 11.4 Mbit against a
            # ceiling of 6. Plain variable bitrate does what it is told.
            # -forced-idr is not optional here. Without it NVENC answers a forced
            # keyframe with an ordinary I-frame, which the muxer will not cut on, so
            # the segments came out at whatever the GOP was - ten and a half seconds -
            # and the four-second grid the playlist is written on was fiction.
            # four megabits unless somebody has said how much line there is
            want = mbit or 4
            video = (["-c:v", ENCODER["codec"]] + list(ENCODER["preset"])
                     + rate_args(ENCODER, want, want)
                     # only Nvidia needs telling; the others cut where they are asked
                     + (["-forced-idr", "1"] if ENCODER["name"] == "nvenc" else []))
        else:
            want = mbit or 6
            video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                     "-maxrate", "%dM" % want, "-bufsize", "%dM" % max(2, want * 2)]
        cmd = cmd[:cut] + video + cmd[end:]
        # eight times realtime: far enough ahead that seeking works - forty minutes
        # ready after five minutes of watching - without writing out the whole film
        # for somebody who stops after one scene
        cmd = cmd[:1] + ["-readrate", "8"] + cmd[1:]
        # everything up to the muxer is identical; only the output changes
        cut = cmd.index("-movflags")
        out = cmd[:cut] + [
            # A keyframe exactly on every boundary, so each segment really is
            # SEG_SECONDS long. That is what lets the playlist be written before the
            # film has been encoded - and a playlist written in full is what gives a
            # television a timeline instead of a growing stub.
            "-force_key_frames", "expr:gte(t,n_forced*%d)" % SEG_SECONDS,
            "-f", "hls",
            "-hls_time", str(SEG_SECONDS),
            "-hls_playlist_type", "event",
            "-hls_list_size", "0",
            "-hls_flags", "independent_segments+temp_file",
            "-start_number", str(start_number),
            "-hls_segment_filename", os.path.join(folder, "s%05d.ts"),
            os.path.join(folder, "index.m3u8"),
        ]
        if start_number:
            # picked up in the middle: the timestamps have to carry on from where the
            # earlier segments left off, or the receiver sees the clock go back. An
            # output option, so it belongs with the muxer flags rather than the input.
            at = out.index("-f")
            out = out[:at] + ["-output_ts_offset",
                              "%.6f" % (ts_offset or start_number * SEG_SECONDS)] + out[at:]
        return out

    def start_hls(self, rating_key, offset=0, height=0, media_index=0,
                  burn_index=None, src=None, sub_look=None, audio_index=None,
                  mbit=0):
        """The HLS session for this recipe: the one already running, or a new one.

        A receiver re-reads the playlist every few seconds - that is how HLS works -
        and every one of those reads used to start another ffmpeg over another folder.
        The receiver got a playlist of segments it had never heard of, at addresses
        that had just changed again, so it played the first four seconds and stopped.
        The same recipe now answers with the same session.
        """
        # the library hands in the file; there is nowhere else to ask
        if not src:
            raise FileNotFoundError("no file was given to play")
        if (not str(src["file"]).startswith(("http://", "https://"))
                and not os.path.exists(src["file"])):
            raise FileNotFoundError(src["file"])
        recipe = json.dumps([src["file"], int(offset), int(height), int(media_index),
                             burn_index, audio_index, sub_look, int(mbit)],
                            sort_keys=True, default=str)
        with self.lock:
            for old_id, old in list(self.streams.items()):
                if getattr(old, "recipe", None) != recipe:
                    continue
                if not getattr(old, "folder", None) or not os.path.isdir(old.folder):
                    continue
                old.touched = time.time()
                return old
        sid = "hls%d" % int(time.time() * 1000)
        folder = os.path.join(self.root, sid)
        os.makedirs(folder, exist_ok=True)
        log = open(os.path.join(self.root, sid + ".log"), "wb")
        proc = subprocess.Popen(
            self.hls_command(src, folder, offset, height, burn_index, sub_look,
                             audio_index, mbit=mbit),
            creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=log, bufsize=0)
        info = {
            "id": sid, "folder": folder, "hls": True,
            "engine": ENGINE_NAME, "hw": HW_OK, "burn": burn_index,
            "source": "%s %sx%s" % (src.get("videoCodec"), src.get("width"),
                                    src.get("height")),
            "offset": offset, "duration": src["duration"], "title": src.get("title"),
        }
        # how many segments the whole of what is left comes to. The playlist is written
        # from this before any of it exists.
        left = max(0.0, float(src["duration"] or 0) - float(offset))
        info["segments"] = int(math.ceil(left / SEG_SECONDS)) if left else 0
        st = Stream(proc, info, log)
        st.folder = folder
        st.touched = time.time()
        st.recipe = recipe
        st.src = src
        st.height = height
        st.burn_index = burn_index
        st.sub_look = sub_look
        st.audio_index = audio_index
        st.mbit = mbit
        st.start_seg = 0            # the segment number this process began at
        st.restarted = 0.0
        with self.lock:
            self.streams[sid] = st
        # Enough segments have to exist before the playlist is worth handing over.
        # Safari gives up on an empty one; a Chromecast is stricter still - an
        # open-ended playlist holding a single segment is not enough for it to start,
        # and it walks away without asking for anything. Three is about thirty seconds
        # of film, which at eight times realtime costs about four seconds to make.
        playlist = os.path.join(folder, "index.m3u8")
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                with open(playlist, encoding="utf-8") as f:
                    ready = sum(1 for line in f if line.strip().endswith(".ts"))
            except OSError:
                ready = 0
            if ready >= 3:
                break
            if not st.alive():
                # the encode finished first: a short film, or one nearly over
                if ready:
                    break
                raise RuntimeError("the encoder stopped before writing anything")
            time.sleep(0.25)
        return st

    def hls_playlist(self, st):
        """The whole film as a playlist, written before it has been encoded.

        Every segment is exactly SEG_SECONDS long because the encoder is told to put a
        keyframe on every boundary, so the list can be written out in full, ended with
        ENDLIST, and handed over at once. A receiver reading it knows how long the film
        is and draws a timeline; a growing playlist gave it nothing to draw.
        """
        if not st.info.get("segments"):
            return None                     # duration unknown: fall back to ffmpeg's
        left = float(st.info["duration"]) - float(st.info["offset"])
        unit = self.segment_length(st)
        total = int(math.ceil(left / unit))
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:VOD",
                 "#EXT-X-TARGETDURATION:%d" % int(math.ceil(unit)),
                 "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-INDEPENDENT-SEGMENTS"]
        for i in range(total):
            lines.append("#EXTINF:%.6f," % min(unit, left - i * unit))
            lines.append("s%05d.ts" % i)
        lines.append("#EXT-X-ENDLIST")
        return chr(10).join(lines) + chr(10)

    def segment_length(self, st):
        """How long a segment really is, from the ones already written.

        A four-second segment of a 23.976fps film is 96 frames, which is 4.004 seconds.
        Writing 4.000 in every line would put the end of a two-hour film five seconds
        away from the truth, and a timeline that lies is worse than none. So the real
        figure is read back from what the encoder wrote.
        """
        try:
            with open(os.path.join(st.folder, "index.m3u8"), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#EXTINF:"):
                        seen = float(line.split(":", 1)[1].strip().rstrip(","))
                        # a last, short segment is not the measure of the rest
                        if abs(seen - SEG_SECONDS) < 1:
                            return seen
        except (OSError, ValueError):
            pass
        return float(SEG_SECONDS)

    def write_head(self, st):
        """The highest segment number on disk, or -1."""
        try:
            names = [n for n in os.listdir(st.folder) if re.match(r"^s\d{5}\.ts$", n)]
        except OSError:
            return -1
        return max((int(n[1:6]) for n in names), default=-1)

    def restart_hls(self, st, seg):
        """Move the encoder to a segment somebody has asked for.

        Somebody seeking to the last ten minutes asks for a segment the encoder will
        not reach for an hour. Rather than making them wait for it, ffmpeg is started
        again from that point, numbering its output from there, and the segments
        already written stay where they are.
        """
        st.restarted = time.time()
        try:
            st.proc.kill()
        except Exception:
            pass
        st.start_seg = seg
        # the same grid the playlist was written on, not a rounded one, or a seek near
        # the end lands a couple of seconds away from where it was asked for
        unit = self.segment_length(st)
        st.proc = subprocess.Popen(
            self.hls_command(st.src, st.folder,
                             float(st.info["offset"]) + seg * unit,
                             st.height, st.burn_index, st.sub_look, st.audio_index,
                             start_number=seg, ts_offset=seg * unit,
                             mbit=getattr(st, "mbit", 0)),
            creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=st.log, bufsize=0)

    def hls_segment(self, sid, seg, wait=25.0):
        """The file for one segment, waiting for it or going and fetching it."""
        with self.lock:
            st = self.streams.get(sid)
        if not st or not getattr(st, "folder", None):
            return None
        st.touched = time.time()
        path = os.path.join(st.folder, "s%05d.ts" % seg)
        began = time.time()
        deadline = began + wait
        while True:
            if os.path.exists(path):
                st.touched = time.time()
                return path
            head = self.write_head(st)
            # Moving the encoder is expensive, and a player reading ahead of a running
            # encode is not a seek. So: only when it is going somewhere the encoder
            # will never arrive - behind where this process started, a minute or more
            # in front of it, after it has stopped - or when waiting has plainly got
            # nowhere.
            move = (seg < getattr(st, "start_seg", 0)
                    or seg > head + 15
                    or (not st.alive() and seg > head)
                    or time.time() - began > 8)
            if move and time.time() - st.restarted > 3:
                self.restart_hls(st, seg)
                began = time.time()
            if time.time() > deadline:
                return None
            time.sleep(0.25)

    def stop_others(self, address, device, keep):
        """Stop what this same viewer had running before.

        One device plays one thing at a time. A pipe only ends when its connection
        does, so a player that restarts leaves the old encode running against the same
        card - and a handful of those makes the next one late enough to time out.
        """
        who = "%s/%s" % (address, device)
        with self.lock:
            for sid, st in list(self.streams.items()):
                if sid == keep or getattr(st, "folder", None):
                    continue                   # itself, or an HLS session with its own life
                if getattr(st, "who", None) != who:
                    continue
                st.stop()
                self.streams.pop(sid, None)
            st = self.streams.get(keep)
            if st is not None:
                st.who = who

    def hls_file(self, sid, name):
        """One file out of a session's folder, and a note that it is still wanted."""
        with self.lock:
            st = self.streams.get(sid)
        if not st or not getattr(st, "folder", None):
            return None
        st.touched = time.time()
        # only ever a plain name from inside the folder: no paths, no climbing out
        if not re.match(r"^[A-Za-z0-9_.-]+$", name or ""):
            return None
        path = os.path.join(st.folder, name)
        return path if os.path.exists(path) else None

    def sweep_old(self):
        """At startup: nothing in the work folder belongs to a session still running."""
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            try:
                if time.time() - os.path.getmtime(path) < 3600:
                    continue
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except OSError:
                pass

    def sweep_hls(self):
        """Stop sessions nobody is asking for, and delete what they left behind."""
        now = time.time()
        with self.lock:
            for sid, st in list(self.streams.items()):
                folder = getattr(st, "folder", None)
                if not folder:
                    continue
                if now - getattr(st, "touched", 0) < SESSION_IDLE_S:
                    continue
                st.stop()
                self.streams.pop(sid, None)
                shutil.rmtree(folder, ignore_errors=True)

    def subtitle_command(self, src, stream_index, offset):
        """Pull one text subtitle track out as WebVTT.

        Seeking first matters: without -ss ffmpeg reads the whole container looking for
        interleaved subtitle packets, which took over 90s on a 4 GB film. And no -copyts,
        so cue times start at zero exactly like the stream this accompanies."""
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if offset:
            # to the millisecond: a copied picture starts on a keyframe, not a second
            cmd += ["-ss", ("%.3f" % float(offset)).rstrip("0").rstrip(".")]
        cmd += ["-i", src["file"], "-map", "0:%d" % int(stream_index),
                "-vn", "-an", "-f", "webvtt", "pipe:1"]
        return cmd

    def subtitles(self, src, stream_index, offset=0):
        """One track lifted out of the file, as WebVTT."""
        return subprocess.Popen(self.subtitle_command(src, stream_index, offset),
                                creationflags=NO_WINDOW,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def start(self, rating_key, offset=0, height=0, media_index=0, burn_index=None,
              src=None, audio_mode="aac", sub_look=None, audio_index=None, mbit=0,
              channels=0, hevc=False, dts=False, copy_video=False):
        # the library hands in the file to play; there is nowhere else to ask
        if not src:
            raise FileNotFoundError("no file was given to play")
        if (not str(src["file"]).startswith(("http://", "https://"))
                and not os.path.exists(src["file"])):
            raise FileNotFoundError(src["file"])
        sid = "gpu%d" % int(time.time() * 1000)
        log = open(os.path.join(self.root, sid + ".log"), "wb")
        proc = subprocess.Popen(self.command(src, offset, height, burn_index, audio_mode,
                                             sub_look, audio_index, mbit, channels,
                                             hevc, dts, copy_video),
                                creationflags=NO_WINDOW,
                                stdout=subprocess.PIPE, stderr=log, bufsize=0)
        info = {
            "id": sid,
            "engine": ENGINE_NAME,
            "hw": HW_OK,
            "decoder": ("copy" if copy_video else
                        ("hardware + CPU overlay" if burn_index is not None
                         else "hardware") if HW_OK else "software"),
            "copyVideo": bool(copy_video),
            "burn": burn_index,
            "source": "%s %sx%s" % (src.get("videoCodec"), src.get("width"), src.get("height")),
            "audioChannels": src.get("audioChannels"),
            "offset": offset,
            "duration": src["duration"],
            "title": src.get("title"),
        }
        st = Stream(proc, info, log)
        with self.lock:
            # Concurrent streams are fine - the GPU has headroom and each one dies with
            # its HTTP connection. Evicting other streams here meant a second viewer (or
            # a test) silently killed someone else's film.
            for old_id, old in list(self.streams.items()):
                if not old.alive():
                    self.streams.pop(old_id, None)
            self.streams[sid] = st
        return st

    def stop_all(self):
        with self.lock:
            for sid, st in list(self.streams.items()):
                st.stop()
                self.streams.pop(sid, None)

    def status(self):
        with self.lock:                       # forget streams that have exited
            for sid, st in list(self.streams.items()):
                if not st.alive():
                    self.streams.pop(sid, None)
        return {
            "ffmpeg": FFMPEG,
            "nvenc": HW_OK,             # the name the clients already ask by
            "encoder": (ENCODER or {}).get("name", "x264"),
            "engine": ENGINE_NAME,
            "streams": [dict(st.info, alive=st.alive(),
                             running=round(time.time() - st.started, 1))
                        for st in self.streams.values()],
        }
