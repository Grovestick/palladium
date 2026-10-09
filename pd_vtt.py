"""Subtitle text as WebVTT: reading time stamps, shifting, stretching, cutting and mending cues, telling
which language a file is written in, and turning SRT into VTT. Text in, text out.

Moved out of pd-server.py, where every name is still imported, so callers are unchanged.
"""

import re


#: A WebVTT timestamp, with or without its hour. ffmpeg leaves the hour off for the
#: first hour of a film, which is legal and was quietly fatal here.
STAMP = r"(?:\d+:)?\d{1,2}:\d\d[.,]\d+"


def stamp_secs(stamp):
    """Seconds from a WebVTT or SubRip timestamp, in either shape."""
    bits = stamp.replace(",", ".").split(":")
    if len(bits) == 3:
        return int(bits[0]) * 3600 + int(bits[1]) * 60 + float(bits[2])
    if len(bits) == 2:
        return int(bits[0]) * 60 + float(bits[1])
    return float(bits[0])


def stamp_of(seconds):
    """And back, always with the hour, so nothing downstream has to wonder."""
    seconds = max(0.0, seconds)
    return "%02d:%02d:%06.3f" % (int(seconds // 3600), int(seconds % 3600 // 60),
                                 seconds % 60)


def _cp1252_tail():
    """The characters the bytes 0x80-0xBF become when UTF-8 is read as Windows-1252."""
    out = []
    for b in range(0x80, 0xC0):
        try:
            out.append(bytes([b]).decode("cp1252"))
        except UnicodeDecodeError:
            out.append(bytes([b]).decode("latin-1"))
    return "".join(out)


def whole_stamps(text):
    """Every timestamp written out in full, hour included.

    Done once, where a subtitle enters the program, so that everything after it can
    read cues without asking which shape they are in.
    """
    def both(m):
        return stamp_of(stamp_secs(m.group(1))) + " --> " + stamp_of(
            stamp_secs(m.group(2)))
    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), both, text or "")


def shift_vtt(text, seconds):
    """Move every cue in a WebVTT document by so many seconds.

    Anything that would land before the start is clamped there rather than dropped: a
    line at the very beginning is worth keeping even when the offset is negative.
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return text
    if abs(seconds) < 0.01:
        return text

    def moved(stamp):
        return stamp_of(stamp_secs(stamp) + seconds)

    def line(match):
        return moved(match.group(1)) + " --> " + moved(match.group(2))

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), line, text)


def mend_vtt(text, parts, base=0.0):
    """Move each cue by whatever its part of the film asks for.

    A plan is [[from, rate, shift], ...] in order. A subtitle that is simply late has
    one part; one made for a master without the recap this release carries has two, and
    the second is later than the first by however long that recap is.

    A plan is always written in the film's own clock: the rate is measured from the
    first frame and a part begins at a minute of the film. Cues pulled out of a live
    encode count from the encode instead, so `base` says what o'clock their zero is -
    without it a drift is applied as though the viewer had started at the beginning,
    and a film resumed twenty minutes in comes out that much under-corrected.
    """
    if not parts:
        return text
    pieces = []
    for row in parts:
        try:
            pieces.append((float(row[0]), float(row[1]), float(row[2])))
        except (TypeError, ValueError, IndexError):
            continue
    if not pieces:
        return text
    pieces.sort()

    def at(seconds):
        rate, shift = pieces[0][1], pieces[0][2]
        for begins, r, sh in pieces:
            if seconds >= begins:
                rate, shift = r, sh
            else:
                break
        return max(0.0, seconds * rate + shift)

    try:
        base = float(base or 0.0)
    except (TypeError, ValueError):
        base = 0.0

    def moved(stamp):
        # into the film's clock, corrected there, and back to the cues' own
        seconds = stamp_secs(stamp) + base
        return _stamp(max(0.0, at(seconds) - base))

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP),
                  lambda m: moved(m.group(1)) + " --> " + moved(m.group(2)), text)


def _stamp(at):
    return "%02d:%02d:%06.3f" % (int(at // 3600), int(at % 3600 // 60), at % 60)


def stretch_vtt(text, rate, shift):
    """Every timestamp becomes rate * t + shift.

    For a subtitle that drifts - one made for another framerate, or for an edit with
    different breaks in it. That is a second out at the start and four at the end, and
    no single offset is right at both.
    """
    try:
        rate, shift = float(rate), float(shift)
    except (TypeError, ValueError):
        return text
    if abs(rate - 1.0) < 1e-6 and abs(shift) < 0.01:
        return text

    def moved(stamp):
        h, m, rest = stamp.split(":")
        at = (int(h) * 3600 + int(m) * 60 + float(rest)) * rate + shift
        at = max(0.0, at)
        return "%02d:%02d:%06.3f" % (int(at // 3600), int(at % 3600 // 60), at % 60)

    return re.sub(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP),
                  lambda m: moved(m.group(1)) + " --> " + moved(m.group(2)), text)


def cut_vtt(text, offset):
    """Everything from `offset` seconds onward, with the clock reset to zero.

    A subtitle file beside a film is cut to the film. A live encode starting at
    twenty-nine minutes has its own clock starting at zero, so handing it the whole
    file leaves the text half an hour ahead of the picture - which on screen is one
    line sitting there while the scene moves on.

    Shifting is not enough on its own: everything before the cut has to go, rather
    than pile up at the beginning.
    """
    try:
        offset = float(offset)
    except (TypeError, ValueError):
        return text
    if offset <= 0.01:
        return text

    secs, stamp = stamp_secs, stamp_of

    out = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), block)
        if not m:
            continue                      # the header, or a stray blank
        start, end = secs(m.group(1)), secs(m.group(2))
        if end <= offset:
            continue                      # already gone by the time this starts
        block = block.replace(m.group(0),
                              stamp(start - offset) + " --> " + stamp(end - offset))
        out.append(block.strip())
        out.append("")
    return chr(10).join(out)


#: Which writing system a line of subtitle is in. Only enough of them to tell two
#: languages apart in one file; anything unrecognised counts as nothing at all.
SCRIPTS = (
    ("han", "一-鿿㐀-䶿豈-﫿"),
    ("kana", "぀-ヿ"),
    ("hangul", "가-힯ᄀ-ᇿ"),
    ("cyrillic", "Ѐ-ӿ"),
    ("greek", "Ͱ-Ͽ"),
    ("arabic", "؀-ۿ"),
    ("hebrew", "֐-׿"),
    ("thai", "฀-๿"),
    ("latin", "A-Za-zÀ-ɏ"),
)


#: What a language code is written in, when it is not the Latin alphabet.
WRITTEN_IN = {"zh": "han", "cmn": "han", "yue": "han", "chi": "han", "zho": "han",
              "ja": "kana", "jpn": "kana", "ko": "hangul", "kor": "hangul",
              "ru": "cyrillic", "rus": "cyrillic", "uk": "cyrillic",
              "bg": "cyrillic", "sr": "cyrillic", "mk": "cyrillic",
              "el": "greek", "gre": "greek", "ell": "greek",
              "ar": "arabic", "ara": "arabic", "fa": "arabic", "ur": "arabic",
              "he": "hebrew", "heb": "hebrew", "iw": "hebrew",
              "th": "thai", "tha": "thai"}


def script_of(said):
    """Which of the writing systems above this text is mostly in."""
    best, most = "", 0
    for name, chars in SCRIPTS:
        n = len(re.findall("[" + chars + "]", said))
        if n > most:
            best, most = name, n
    return best


def last_cue(body):
    """The second the last cue in this WebVTT ends, so a player knows what it holds."""
    last = 0.0
    for m in re.finditer(rb"--> (\d+):(\d\d):(\d\d)[.,](\d\d\d)", body):
        h, mi, sec, ms = (int(x) for x in m.groups())
        last = max(last, h * 3600 + mi * 60 + sec + ms / 1000.0)
    return last


def one_language(text, want="en"):
    """Keep one language when a subtitle file holds two.

    Subtitles are handed out with two languages in one file more often than anybody
    would guess: the English cues, and then the whole thing again in Chinese on the
    same timestamps. Both are live at the same instant, so both are drawn - one line
    of English with a line of Chinese underneath it.

    The language the file claims to be wins. If it claims nothing recognisable, the
    one with the most cues wins. A handful of foreign lines - a sign, a song, a name -
    is not a second language and is left alone.
    """
    blocks = re.split(r"\n\s*\n", text)
    kinds, counted = [], {}
    for block in blocks:
        said = re.sub(r"^\s*\d+\s*$", "", block, flags=re.M)
        said = re.sub(r"\d+:\d\d:\d\d[.,]\d+.*", "", said)
        kind = script_of(said) if said.strip() else ""
        kinds.append(kind)
        if kind:
            counted[kind] = counted.get(kind, 0) + 1
    if len(counted) < 2:
        return text
    order = sorted(counted.items(), key=lambda kv: -kv[1])
    keep = WRITTEN_IN.get((want or "").lower()[:3], "latin")
    if keep not in counted:
        keep = order[0][0]
    other = sum(n for k, n in counted.items() if k != keep)
    # a few stray lines in another alphabet are part of this subtitle, not a second one
    if other < 8 or other * 5 < counted.get(keep, 0):
        return text
    out = [b for b, kind in zip(blocks, kinds) if kind in ("", keep)]
    gap = chr(10) + chr(10)
    body = gap.join(b.strip() for b in out if b.strip()).strip()
    if not re.search(r"\d+:\d\d:\d\d", body):
        return text                       # filtered everything away: leave it be
    return ("WEBVTT" + gap + re.sub(r"^WEBVTT\s*", "", body)).strip() + chr(10)


def drop_echoes(text):
    """Fold a live-captioned track's repeated cues into the cue they repeat.

    The repeat is written a fifth of a second after the line, and blinks on a player
    that draws the newest cue only. One episode carried 672 in 1996 cues.

    A tenth of the cues must be repeats before the track is touched. Below that the
    overlaps are two speakers at once, and shortening the first cue cuts a line to
    0.2s; flatten_rollup wants two cues in five, which an echoed track can sit under.
    """
    def bare(lines):
        return [re.sub(r"[^a-z0-9]+", "", l.lower()) for l in lines]

    stamps = re.compile(r"(%s)\s*-->\s*(%s)(.*)" % (STAMP, STAMP))
    blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        rows = block.split(chr(10))
        at = next((i for i, l in enumerate(rows) if stamps.search(l)), None)
        if at is None:
            continue
        m = stamps.search(rows[at])
        # the line above the stamp is the cue's name and what follows it is where on
        # the screen it is drawn: both are carried, not dropped in the rewrite
        ident = rows[at - 1].strip() if at else ""
        lines = [l for l in rows[at + 1:] if l.strip()]
        if lines:
            blocks.append([stamp_secs(m.group(1)), stamp_secs(m.group(2)), lines,
                           ident, m.group(3).rstrip()])
    if len(blocks) < 8:
        return text

    #: how long after a line closes a repeat of it is an echo of it rather than the
    #: line being said again
    SOON = 0.4

    def echoed(one, before):
        return bool(before) and (bare(before[2]) == bare(one[2])
                                 and one[0] - before[1] <= SOON)

    #: how much of a track has to be echoes before it is a track that echoes. The
    #: episode above was a third of its cues; a written subtitle that repeats a line
    #: twice is not, and its simultaneous cues are two people talking.
    echoes = sum(1 for i, one in enumerate(blocks)
                 if echoed(one, blocks[i - 1] if i else None))
    if echoes < len(blocks) * 0.1:
        return text

    out, changed = [], 0
    for one in blocks:
        last = out[-1] if out else None
        # However long the repeat stands. The line is often written three times over
        # - once as it is said, then again while the line under it is added - and only
        # the first two of those are short.
        if echoed(one, last):
            last[1] = max(last[1], one[1])     # the same line held, not said twice
            changed += 1
            continue
        if last and one[0] < last[1]:
            last[1] = one[0]                   # and nothing sits on the one before it
            changed += 1
        out.append(list(one))
    if not changed:
        return text

    said = ["WEBVTT", ""]
    for began, ended, lines, ident, where in out:
        if ended - began < 0.2:
            ended = began + 0.2
        if ident:
            said.append(ident)
        said.append(stamp_of(began) + " --> " + stamp_of(ended) + where)
        said.extend(lines)
        said.append("")
    return chr(10).join(said)


def flatten_rollup(text):
    """Captions written to roll up the screen, made into what is new in each one.

    Live captioning does not write a line and take it away: it writes a line, and the
    next cue carries that line again with another under it. Turned into WebVTT that
    reads as the next line of dialogue appearing long before it is spoken - one cue
    held from twelve seconds to sixty-eight while the room is silent, because the
    caption that began at twelve was still on the screen at sixty-eight.

    Each cue keeps what is new in it and ends where the next one starts. The overlap
    is a run of lines rather than one, and the case changes as a line rolls up, so
    both are read loosely. Left alone unless the track is plainly of this kind: half
    the cues repeating the lines above is not something a written subtitle does.
    """
    def bare(line):
        return re.sub(r"[^a-z0-9]+", "", line.lower())

    blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        m = re.search(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), block)
        if not m:
            continue
        lines = [l for l in block.split(chr(10))[1:] if l.strip()]
        if lines:
            blocks.append([stamp_secs(m.group(1)), stamp_secs(m.group(2)), lines])
    if len(blocks) < 8:
        return text

    def carried(before, now):
        """How many of this cue's first lines the one before it already had."""
        most = min(len(before), len(now))
        for k in range(most, 0, -1):
            if [bare(x) for x in before[-k:]] == [bare(x) for x in now[:k]]:
                return k
        return 0

    rolling = sum(1 for a, b in zip(blocks, blocks[1:]) if carried(a[2], b[2]))
    if rolling < len(blocks) * 0.4:
        return text                       # an ordinary file: nothing to flatten

    out = ["WEBVTT", ""]
    for n, (began, ended, lines) in enumerate(blocks):
        if n:
            k = carried(blocks[n - 1][2], lines)
            fresh = lines[k:] or lines[-1:]
        else:
            fresh = lines
        stop = min(ended, blocks[n + 1][0]) if n + 1 < len(blocks) else ended
        # A caption stands while it is being spoken, not while the room is silent:
        # the cue that opened this kind of track can otherwise hold three lines for a
        # minute because that is how long it was on the screen.
        stop = min(stop, began + 8.0)
        if stop - began < 0.2:
            stop = began + 0.2
        out.append(stamp_of(began) + " --> " + stamp_of(stop))
        out.extend(fresh)
        out.append("")
    return chr(10).join(out)


def vtt_span(text):
    """When the first cue starts and the last one ends, in seconds."""
    stamps = re.findall(r"(%s)\s*-->\s*(%s)" % (STAMP, STAMP), text)
    if not stamps:
        return None

    def secs(stamp):
        h, m, rest = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    return secs(stamps[0][0]), max(secs(b) for _, b in stamps)


def as_vtt(path):
    """One subtitle file beside a video, read and turned into WebVTT."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return srt_to_vtt(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return ""


def srt_to_vtt(text):
    """SubRip to WebVTT: a header, and commas in timestamps become full stops."""
    body = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", text.replace("\r", ""))
    return "WEBVTT" + chr(10) + chr(10) + body
