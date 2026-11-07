"""Subtitles read from the bytes a direct play already reads off the disk.

Lifting a track out of a Matroska file means reading the whole film: on a 73 GB remux, a
second pass over the drive the film is playing from, and the film stuttered for it. The
server hands a player every block of the file anyway, so those blocks are passed on to an
ffmpeg that keeps only the text tracks: nothing more is read from the disk. A player holds
about a minute ahead, so a line is here before it is due. A jump starts a new reader at the
next cluster, after the file's header; what was read before is kept.

Never holds a viewer up: a block is handed over without waiting and everything else happens
on one thread of its own. A queue that is full drops blocks and picks up at the next cluster.
"""
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time

#: the marker that starts a Matroska cluster: a reader can begin at one
CLUSTER = bytes([0x1F, 0x43, 0xB6, 0x75])
TEXT_CODECS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")
#: the header is read from the file itself, up to here, to find the first cluster
HEAD_MOST = 64 * 1024 * 1024
#: blocks waiting for the reader: 128 of a player's 256 KB is 32 MB
QUEUE_MOST = 128
#: seconds without a block before a reader is finished
IDLE_FOR = 90
#: seconds a reader stays with the read it follows before a block elsewhere counts as a jump:
#: a player opens a second read of a file, and each block restarted the reader
FOLLOW_FOR = 3.0
#: seconds before a file whose first look failed is looked at again
LOOK_AGAIN = 60
CUE = re.compile(r"^(\S+) --> (\S+)([^\n]*)\n((?:[^\n]+\n?)*)", re.M)

LOCK = threading.Lock()
TAPS = {}
#: blocks on their way to the readers: a viewer's thread only ever puts one here
INBOX = queue.Queue(QUEUE_MOST)
#: how a path's whole track is kept, and ffmpeg, from the server
CONFIG = {"ffmpeg": None, "cached": None, "keep": None, "log": None}


def configure(ffmpeg, cached, keep, log=None):
    """ffmpeg() its path; cached(video, index) whether a whole track is kept; keep(video,
    index, text) keeps one."""
    CONFIG.update(ffmpeg=ffmpeg, cached=cached, keep=keep, log=log)


def seconds_of(stamp):
    """A WebVTT time as seconds."""
    total = 0.0
    for part in str(stamp).split(":"):
        total = total * 60 + float(part)
    return total


def stamp_of(seconds):
    """Seconds as a WebVTT time."""
    ms = int(round(max(0.0, seconds) * 1000))
    return "%02d:%02d:%02d.%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def parse(text, shift=0.0):
    """{start in ms: (start, end, settings, words)} of a WebVTT text, each time less `shift`:
    a reader keeps the file's own clock, a player counts from where the film starts."""
    out = {}
    for start, end, settings, words in CUE.findall(str(text or "")):
        try:
            a, b = seconds_of(start) - shift, seconds_of(end) - shift
        except ValueError:
            continue
        out[int(round(a * 1000))] = (a, b, settings, words.rstrip("\n"))
    return out


def as_vtt(cues):
    """Cues as a WebVTT text, in order."""
    lines = ["WEBVTT", ""]
    for key in sorted(cues):
        a, b, settings, words = cues[key]
        lines += ["%s --> %s%s" % (stamp_of(a), stamp_of(b), settings), words, ""]
    return "\n".join(lines) + "\n"


def first_cluster(block):
    """Where the first cluster in these bytes begins, -1 for none."""
    return block.find(CLUSTER)


class Tap:
    """One file's reader and everything read from it so far."""

    def __init__(self, path, tracks, shift, header):
        self.path, self.tracks, self.shift, self.header = path, tracks, shift, header
        self.cues = {i: {} for i in tracks}
        self.proc = None
        self.blocks = queue.Queue(QUEUE_MOST)
        self.folder = tempfile.mkdtemp(prefix="pdtap")
        self.next_at = None           # the byte the reader expects next; None: find a cluster
        self.fed_at = time.time()
        self.from_start = False       # read without a gap from the first cluster
        self.whole = False
        self.run = 0

    def _file(self, index):
        return os.path.join(self.folder, "t%d-%d.vtt" % (index, self.run))

    def _start(self, from_start):
        self._stop()
        self.run += 1
        self.blocks = queue.Queue(QUEUE_MOST)
        self.from_start = from_start
        line = [CONFIG["ffmpeg"]() if CONFIG["ffmpeg"] else "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-analyzeduration", "0", "-probesize", "5000000", "-f", "matroska", "-i", "pipe:0", "-copyts"]
        for i in self.tracks:
            line += ["-map", "0:%d" % i, "-c:s", "webvtt", "-f", "webvtt", "-flush_packets", "1", self._file(i)]
        self.proc = subprocess.Popen(line, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc, blocks = self.proc, self.blocks

        def write():
            try:
                stdin = proc.stdin
                if stdin is None:
                    return
                stdin.write(self.header)
                while True:
                    block = blocks.get()
                    if block is None:
                        break
                    stdin.write(block)
                stdin.close()
            except (OSError, ValueError):
                pass
        threading.Thread(target=write, name="palladium-subtap", daemon=True).start()

    def _stop(self):
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            self.blocks.put_nowait(None)
        except queue.Full:
            proc.kill()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
        self.read()

    def feed(self, at, block):
        """A block the player was given, from byte `at`."""
        if (self.proc is not None and self.next_at not in (None, at)
                and time.time() - self.fed_at < FOLLOW_FOR):
            return
        self.fed_at = time.time()
        if self.proc is None or self.next_at != at:
            # a jump, or the first block: from the next cluster in what comes
            k = first_cluster(block)
            if k < 0:
                self.next_at = None
                return
            self._start(at + k <= len(self.header))
            block, at = block[k:], at + k
        try:
            self.blocks.put_nowait(block)
        except queue.Full:
            # behind: this block is lost to it, and it begins again at the next cluster
            self.next_at = None
            self.from_start = False
            return
        self.next_at = at + len(block)

    def read(self):
        """What the readers have written so far, added to what is held."""
        for i in self.tracks:
            for run in range(1, self.run + 1):
                try:
                    with open(os.path.join(self.folder, "t%d-%d.vtt" % (i, run)), encoding="utf-8",
                              errors="replace") as f:
                        self.cues[i].update(parse(f.read(), self.shift))
                except OSError:
                    pass

    def finish(self, size):
        """The reader ended; a track read from its first cluster to the end of the file is
        whole, and kept as any other lifted track."""
        ended = self.from_start and self.next_at is not None and self.next_at >= size
        self._stop()
        if ended and CONFIG["keep"]:
            self.whole = True
            for i in self.tracks:
                if self.cues[i]:
                    CONFIG["keep"](self.path, i, as_vtt(self.cues[i]))


def _facts(path):
    """(text track numbers, the film's start in seconds) of a file."""
    ffmpeg = CONFIG["ffmpeg"]() if CONFIG["ffmpeg"] else "ffmpeg"
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(ffprobe):
        ffprobe = "ffprobe"
    said = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=start_time:stream=index,codec_type,codec_name",
         "-of", "json", path], capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or b"{}")
    tracks = [int(s["index"]) for s in said.get("streams") or []
              if s.get("codec_type") == "subtitle" and s.get("codec_name") in TEXT_CODECS]
    return tracks, float((said.get("format") or {}).get("start_time") or 0)


def _open(path):
    """The tap for a file, made when it has a text track not lifted yet; None otherwise."""
    if not str(path).lower().endswith(".mkv"):
        return None
    tracks, shift = _facts(path)
    if CONFIG["cached"]:
        tracks = [i for i in tracks if not CONFIG["cached"](path, i)]
    if not tracks:
        return None
    with open(path, "rb") as f:
        head = f.read(HEAD_MOST)
    k = first_cluster(head)
    if k < 0:
        return None
    return Tap(path, tracks, shift, head[:k])


def feed(path, at, block):
    """A block of a direct play, handed over. Asked of every block on a viewer's thread: it
    never waits."""
    if not CONFIG.get("draining"):
        CONFIG["draining"] = True
        threading.Thread(target=_drain, name="palladium-subtap-feed", daemon=True).start()
    try:
        INBOX.put_nowait((path, at, block))
    except queue.Full:
        pass


def _drain():
    while True:
        path, at, block = INBOX.get()
        try:
            _take(path, at, block)
        except Exception:
            pass


def _take(path, at, block):
    """One block, on the readers' own thread. The first look at a file is off it too."""
    tap = TAPS.get(path)
    if tap is None:
        with LOCK:
            if path in TAPS:
                return
            TAPS[path] = False        # being looked at; False: nothing to read
        threading.Thread(target=_look, args=(path,), name="palladium-subtap-open", daemon=True).start()
        return
    if tap is not False:
        with LOCK:
            tap.feed(at, block)


def _look(path):
    try:
        tap = _open(path)
    except Exception:
        # a look that failed, a drive too busy to answer, is not nothing to read
        tap = None
        again = threading.Timer(LOOK_AGAIN, lambda: TAPS.pop(path, None))
        again.daemon = True
        again.start()
    with LOCK:
        TAPS[path] = tap if tap is not None else False
    _sweeper()


def _sweeper():
    """One thread ends the readers nobody has fed for a while."""
    if CONFIG.get("sweeping"):
        return
    CONFIG["sweeping"] = True

    def sweep():
        while True:
            time.sleep(15)
            with LOCK:
                items = list(TAPS.items())
            for path, tap in items:
                if tap is False:
                    continue
                if tap.proc is not None and time.time() - tap.fed_at > IDLE_FOR:
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    with LOCK:
                        tap.finish(size)
    threading.Thread(target=sweep, name="palladium-subtap-sweep", daemon=True).start()


def reading(path, index):
    """Whether this track is coming from a play of the file: a screen asks here, and reading
    the film a second time is left alone."""
    tap = TAPS.get(path)
    return bool(tap) and int(index) in tap.tracks and (tap.proc is not None or tap.next_at is None
                                                       and time.time() - tap.fed_at < IDLE_FOR)


def wait_for(path, index, seconds):
    """reading, given a moment: a player opens the film and asks for its subtitles at once."""
    until = time.time() + seconds
    while time.time() < until:
        if reading(path, index):
            return True
        if TAPS.get(path) is False:
            return False
        time.sleep(0.2)
    return reading(path, index)


def vtt(path, index):
    """What is read of this track so far, as WebVTT."""
    tap = TAPS.get(path)
    if not tap or int(index) not in tap.tracks:
        return "WEBVTT\n\n"
    tap.read()
    return as_vtt(tap.cues[int(index)])
