#!/usr/bin/env python3
"""Putting a subtitle in step with the film, without anybody watching a stopwatch.

A subtitle cut for another release runs a second or two out, sometimes twenty. Choosing
another file is a lottery, and nudging by hand works but somebody has to sit there
doing it.

The film says when people are talking. Decode the audio small and cheap, keep only the
band speech lives in, and mark every tenth of a second as sound or quiet: that is a
rough map of the dialogue. The subtitle is the same map drawn by somebody else - a cue
is on screen or it is not. Slide one over the other, and where they agree best is the
correction.

    speech_frames()   the film, as one bit per tenth of a second
    cue_frames()      the subtitle, in the same shape
    find_offset()     how far apart they are, and how sure that is
    sync()            all three, for a file and a subtitle

Measured on a real film: an offset put in by hand comes back exactly, to the frame,
whether it was one second or sixty, and the whole thing takes about eight seconds.

It knows when it does not know. A cartoon with wall-to-wall music and effects has no
quiet to line up against, and a subtitle for a different film matches nowhere in
particular; both come back with a low confidence rather than a confident wrong answer.
"""
import array
import math
import re
import subprocess

#: The server has no console of its own, so anything it starts is handed a fresh one by
#: Windows - a black window over whatever is on screen, and one per ffmpeg. Measuring a
#: subtitle starts a dozen of them.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Frames a second. Finer than anybody can see a subtitle be wrong by, coarse enough
#: that a film is a few thousand numbers rather than millions.
HZ = 10

#: How far out a subtitle may be and still be the same subtitle. Two minutes covers a
#: release with a different opening on the front; beyond that it is another cut, and
#: the answer would be a guess dressed up as one.
MAX_SHIFT = 120

#: How much of the film to listen to. Half an hour is more than enough signal to place
#: a subtitle, and it keeps a button press under ten seconds.
LISTEN = 2100

#: Below this, the answer is "I could not tell" rather than a number.
SURE_ENOUGH = 0.35


def _ffmpeg():
    from pd_gpu import FFMPEG
    return FFMPEG


def _ffprobe():
    exe = _ffmpeg() or ""
    if exe.endswith("ffmpeg.exe"):
        return exe[:-len("ffmpeg.exe")] + "ffprobe.exe"
    if exe.endswith("ffmpeg"):
        return exe[:-len("ffmpeg")] + "ffprobe"
    return "ffprobe"


def film_length(video):
    """How long the film runs, asked of ffprobe. Nought when it cannot be told."""
    try:
        out = subprocess.run(
            [_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", video],
            capture_output=True, text=True, timeout=60,
            creationflags=NO_WINDOW).stdout.strip()
        return float(out.split()[0]) if out else 0.0
    except Exception:
        return 0.0


def has_centre(video):
    """Whether the soundtrack has a centre channel to take the dialogue from."""
    try:
        out = subprocess.run(
            [_ffprobe(), "-v", "quiet", "-select_streams", "a:0", "-show_entries",
             "stream=channels,channel_layout", "-of", "default=nw=1:nk=1", video],
            capture_output=True, text=True, timeout=60,
            creationflags=NO_WINDOW).stdout.lower()
    except Exception:
        return False
    if "5.1" in out or "7.1" in out or "(fc" in out or "+fc" in out:
        return True
    first = out.split()
    return bool(first) and first[0].isdigit() and int(first[0]) >= 6


def _energies(video, af, hz, seconds, pan, start=0.0):
    """Loudness per frame from one pass of ffmpeg, filtered however it is asked."""
    cmd = [_ffmpeg(), "-v", "quiet"]
    if start:
        # before -i, so ffmpeg seeks to the window rather than decoding its way there
        cmd += ["-ss", "%.2f" % float(start)]
    if seconds:
        cmd += ["-t", str(int(seconds))]
    cmd += ["-i", video, "-vn", "-af", ((pan + ",") if pan else "") + (af or "anull"),
            "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=900,
                             creationflags=NO_WINDOW).stdout
    except Exception:
        return []
    pcm = array.array("h")
    pcm.frombytes(raw[:len(raw) // 2 * 2])
    step = int(8000 / hz)
    out = []
    for i in range(0, len(pcm) - step, step):
        total = 0
        # every fourth sample: loudness does not need all of them, and this is the
        # difference between four seconds and sixteen on a long film
        for j in range(i, i + step, 4):
            v = pcm[j]
            total += v * v
        out.append(total)
    return out


def signals(video, hz=HZ, seconds=LISTEN, start=0.0):
    """Two readings of the same sound, because films differ in what gives them away.

    **loud** - where the film is louder than its own middle, in the band speech lives
    in. Plain, and enough for anything with quiet between the lines.

    **speech** - how much of the sound is *in* that band rather than spread across the
    whole of it. Music and effects are broad; a voice is not. A cartoon with a score
    running under every scene is loud from beginning to end and tells the first reading
    nothing at all - this one places its subtitles to the second.

    Whichever lines up more sharply wins, so neither has to be right about every film.
    """
    pan = "pan=mono|c0=FC" if has_centre(video) else ""
    band = _energies(video, "highpass=f=200,lowpass=f=3000", hz, seconds, pan, start)
    # asking a stereo track for a centre channel gives back silence rather than an
    # error, so the answer is checked before it is believed
    if pan and (len(band) < 60 or sorted(band)[int(len(band) * 0.98)] < 1000):
        pan = ""
        band = _energies(video, "highpass=f=200,lowpass=f=3000", hz, seconds, pan,
                         start)
    if len(band) < 60:
        return {}
    full = _energies(video, "", hz, seconds, pan, start)
    out = {}
    mid = sorted(band)[len(band) // 2]
    out["loud"] = [1 if v > mid else 0 for v in band]
    n = min(len(full), len(band))
    if n >= 60:
        ratio = [band[i] / float(full[i] + 1) for i in range(n)]
        rmid = sorted(ratio)[n // 2]
        quiet = sorted(band[:n])[n // 3]
        out["speech"] = [1 if ratio[i] > rmid and band[i] > quiet else 0
                         for i in range(n)]
    return out


#: How much sound to take from each end of a long film. Twelve minutes places a
#: subtitle on its own, and two of them cost what one long listen costs.
WINDOW = 720


def ends(video, duration=0.0, hz=HZ):
    """The film's sound at both ends, as (where the window starts, its readings).

    One window for anything short enough to listen to whole, two for a film - so a
    drift is measured across all of it rather than guessed at from the first half hour
    and extrapolated over the remaining ninety minutes.
    """
    duration = duration or film_length(video)
    if not duration or duration <= WINDOW * 2 + 300:
        heard = signals(video, hz=hz,
                        seconds=None if 0 < duration <= LISTEN else LISTEN)
        return [(0.0, heard)] if heard else []
    late_at = max(0.0, duration - WINDOW)
    out = []
    early = signals(video, hz=hz, seconds=WINDOW)
    if early:
        out.append((0.0, early))
    late = signals(video, hz=hz, seconds=WINDOW, start=late_at)
    if late:
        out.append((late_at, late))
    return out


#: How wide each listening window is, and how many of them across a film. Four
#: minutes is enough to place a subtitle; five of them find a step wherever it is.
SWEEP = 240
SWEEPS = 5

#: Two answers this close belong to the same piece of the film.
SAME_S = 0.5


def sweep(video, duration=0.0, hz=HZ, count=SWEEPS, span=SWEEP):
    """Listen at several points across the film, evenly spread.

    Returns [(where it starts, the readings there)]. A short thing gets fewer windows;
    anything under twice a window is listened to whole.
    """
    duration = duration or film_length(video)
    if not duration or duration <= span * 2:
        heard = signals(video, hz=hz, seconds=None)
        return [(0.0, heard)] if heard else []
    count = max(2, min(count, int(duration // span)))
    step = (duration - span) / float(count - 1)
    out = []
    for i in range(count):
        start = round(i * step, 1)
        heard = signals(video, hz=hz, seconds=span, start=start)
        if heard:
            out.append((start, heard))
    return out


def profile(got, vtt, hz=HZ, max_shift=MAX_SHIFT):
    """What each window says the subtitle is out by: [(from, to, offset, sure)].

    Each window is searched around what the one before it found, not around nought. A
    subtitle made for another framerate is a few seconds out early on and five minutes
    out at the end, which is far outside any sensible search - but never more than a few
    seconds away from where the last window left it.
    """
    out = []
    expect = 0.0
    for start, heard in got:
        marks = heard.get("speech") or heard.get("loud") or []
        if not marks:
            continue
        cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz,
                          shift=-start + expect)
        offset, sure = find_offset(marks, cues, hz=hz, max_shift=max_shift)
        found = round(expect + offset, 2)
        if sure >= SURE_ENOUGH:
            expect = found
        out.append((start, start + len(marks) / float(hz), found, sure))
    return out


def straight(good, allow=0.35):
    """A rate and an offset when the readings sit on a line, or nothing.

    Least squares through the middle of each window. It is only believed when every
    reading is within `allow` of the line - otherwise the film has a step in it, and a
    line through a step is wrong at both ends.
    """
    if len(good) < 3:
        return None
    xs = [(w[0] + w[1]) / 2.0 for w in good]
    ys = [w[2] for w in good]
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    bottom = sum((x - mx) ** 2 for x in xs)
    if bottom <= 0:
        return None
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs))) / bottom
    base = my - slope * mx
    if max(abs(ys[i] - (slope * xs[i] + base)) for i in range(len(xs))) > allow:
        return None
    rate = snap_rate(1.0 + slope)
    if abs(rate - 1.0) < 1e-6:
        return None                     # a flat line is a plain offset, not a rate
    return round(rate, 6), round(base, 3)


#: A lone window may say the subtitle is a second or two out and be believed. Saying it
#: is two minutes out is a claim worth a second opinion.
LONE_S = 10.0


def seconded(got, vtt, offset, hz=HZ):
    """Does more than one window agree the subtitle is out by this much?

    The candidate is applied to the subtitle and each window asked again, this time
    looking only a few seconds either way. A real offset makes several windows agree at
    nought; a peak that happened to win one window makes none of the others agree.
    """
    moved = stretched(vtt, 1.0, offset)
    agree = 0
    for start, heard in got:
        marks = heard.get("speech") or heard.get("loud") or []
        if not marks:
            continue
        cues = cue_frames(moved, len(marks) + 10 * hz, hz=hz, shift=-start)
        found, sure = find_offset(marks, cues, hz=hz, max_shift=6)
        if sure >= SURE_ENOUGH * 0.7 and abs(found) <= SAME_S:
            agree += 1
    return agree >= 2


#: How near a reading has to be to a line before it counts as agreeing with it.
NEAR_S = 0.6

#: A line has to be believed by this many windows, and to be no madder than this: a
#: subtitle running one part in fifty out is not a subtitle for this film.
SUPPORT = 3
MAX_RATE = 0.02


def peaks(marks, cues, hz=HZ, max_shift=MAX_SHIFT, top=4):
    """The best few offsets this window can suggest, not merely its favourite.

    A window that cannot make up its mind is still worth asking: the answer may be its
    second choice, and the way to tell is whether other windows sit on a line with it.
    """
    if not marks or not cues:
        return []
    sums = {"speech": _running(marks), "cues": _running(cues)}
    span = int(max_shift * hz)
    step = max(1, hz // 2)
    scored = []
    for by in range(-span, span + 1, step):
        got = _score(marks, cues, by, sums)
        if got > -1.0:
            scored.append((by, got))
    if not scored:
        return []
    out = []
    for i, (by, got) in enumerate(scored):
        before = scored[i - 1][1] if i else -2.0
        after = scored[i + 1][1] if i + 1 < len(scored) else -2.0
        if got >= before and got >= after:                # a local peak
            out.append((by, got))
    out.sort(key=lambda p: -p[1])
    fine = []
    for by, _ in out[:top]:
        best = max(((_score(marks, cues, at, sums), at)
                    for at in range(by - step, by + step + 1)), default=(0.0, by))
        fine.append((best[1] / float(hz), best[0]))
    return fine


def offered(got, vtt, hz=HZ, max_shift=MAX_SHIFT, top=4):
    """What each window is prepared to suggest: [(middle second, [(offset, score)])]."""
    out = []
    for start, heard in got:
        marks = heard.get("speech") or heard.get("loud") or []
        if not marks:
            continue
        cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz, shift=-start)
        said = peaks(marks, cues, hz=hz, max_shift=max_shift, top=top)
        if said:
            out.append((start + len(marks) / (2.0 * hz), said))
    return out


def through(offers):
    """The line most of the windows agree with, or nothing.

    Every pair of suggestions from different windows makes a line; the line kept is the
    one the most windows have a suggestion near. Three windows is the least that can
    tell a line from a coincidence.
    """
    best = None
    for i, (t1, first) in enumerate(offers):
        for o1, s1 in first:
            for t2, second in offers[i + 1:]:
                if abs(t2 - t1) < 60:
                    continue
                for o2, s2 in second:
                    slope = (o2 - o1) / (t2 - t1)
                    if abs(slope) > MAX_RATE:
                        continue
                    base = o1 - slope * t1
                    support, weight = 0, 0.0
                    for t, said in offers:
                        want = slope * t + base
                        near = [(sc, abs(o - want)) for o, sc in said
                                if abs(o - want) <= NEAR_S]
                        if near:
                            support += 1
                            weight += max(sc for sc, _ in near)
                    if support < SUPPORT:
                        continue
                    mark = (support, round(weight, 4))
                    if best is None or mark > best[0]:
                        best = (mark, snap_rate(1.0 + slope), round(base, 2))
    # Two independent windows, minutes apart, whose own best answer agrees to within
    # half a second: that is a constant, and no line drawn through weaker peaks is
    # allowed to argue with it. It is the difference between a subtitle that is simply
    # right - most of them - and one that drifts, where the best answers are all over
    # the place and only the second-best ones line up.
    tops = sorted(((said[0][1], said[0][0]) for _, said in offers if said),
                  reverse=True)
    if len(tops) >= 2 and abs(tops[0][1] - tops[1][1]) <= SAME_S:
        at = round((tops[0][1] + tops[1][1]) / 2.0, 2)
        support, weight = 0, 0.0
        for _, said in offers:
            near = [sc for o, sc in said if abs(o - at) <= NEAR_S]
            if near:
                support += 1
                weight += max(near)
        return 1.0, at, support, weight

    # The same question asked of one number: which single offset do the most windows
    # have a peak near? A line is only worth having if it explains more than that.
    level = None
    for _, first in offers:
        for o0, _ in first:
            support, weight = 0, 0.0
            for _, said in offers:
                near = [sc for o, sc in said if abs(o - o0) <= NEAR_S]
                if near:
                    support += 1
                    weight += max(near)
            mark = (support, round(weight, 4))
            if level is None or mark > level[0]:
                level = (mark, o0)

    if level and level[0][0] >= SUPPORT:
        # A line must be believed by more windows than the constant, or by as many with
        # markedly more of their agreement. Anything less and the extra freedom is
        # fitting noise, which is what -19 seconds drifting to +22 was.
        if (best is None or level[0][0] > best[0][0]
                or (level[0][0] == best[0][0] and best[0][1] < level[0][1] * 1.3)):
            (support, weight), at = level
            return 1.0, round(at, 2), support, weight
    if not best:
        return None
    (support, weight), rate, base = best
    return rate, base, support, weight


def plan(seen, duration=0.0, corroborate=None):
    """Turn those readings into pieces: [(from, rate, shift)], and how sure it is.

    One piece when the film agrees with itself. Several when it does not: the windows
    are grouped where they agree to within half a second, and each group becomes a piece
    running to the middle of the gap before the next one. A group of one window that
    disagrees with both its neighbours is noise and is dropped.
    """
    good = [w for w in seen if w[3] >= SURE_ENOUGH]
    if len(good) < 2:
        best = max(seen, key=lambda w: w[3]) if seen else None
        if not best or best[3] < SURE_ENOUGH:
            return [], 0.0
        # One window is one voice. A second or two it may have on its own; two minutes
        # is a claim, and a claim wants seconding - a subtitle that really is out by
        # that much makes the rest of the film agree once it is moved.
        if abs(best[2]) > LONE_S and not (corroborate and corroborate(best[2])):
            # Refused. Before giving up, try the quiet answer: a window that was not
            # sure enough on its own but says the subtitle is nearly right, which is
            # what most subtitles are. It has to be seconded like any other.
            near = [w for w in seen if abs(w[2]) <= LONE_S and w[3] >= SURE_ENOUGH * 0.5]
            near.sort(key=lambda w: -w[3])
            for w in near:
                if corroborate and corroborate(w[2]):
                    return [(0.0, 1.0, w[2])], w[3]
            return [], best[3]
        return [(0.0, 1.0, best[2])], best[3]
    groups = [[good[0]]]
    for w in good[1:]:
        if abs(w[2] - groups[-1][-1][2]) <= SAME_S:
            groups[-1].append(w)
        else:
            groups.append([w])
    # a lone window between two that agree with each other is a bad reading
    if len(groups) > 2:
        kept = [groups[0]]
        for i in range(1, len(groups) - 1):
            before, here, after = kept[-1], groups[i], groups[i + 1]
            if len(here) == 1 and abs(before[-1][2] - after[0][2]) <= SAME_S:
                continue
            kept.append(here)
        kept.append(groups[-1])
        groups = kept
    if len(groups) == 1:
        offsets = [w[2] for w in groups[0]]
        return ([(0.0, 1.0, round(sum(offsets) / len(offsets), 2))],
                min(w[3] for w in groups[0]))
    # A straight line first: a subtitle made for another framerate is out by a little
    # more in every window, evenly, and one rate puts the whole film right. Steps are
    # what is left when the readings do not sit on a line.
    line = straight(good)
    if line:
        return [(0.0, line[0], line[1])], min(w[3] for w in good)
    # A step is a claim that the film changes part-way through, and one window is not
    # enough to make it: a single reading between two that agree is noise, and a plan
    # built on it moves the second half of a film for no reason. Groups of one are
    # dropped unless they are more certain than anything else here.
    strong = [g for g in groups
              if len(g) > 1 or max(w[3] for w in g) >= SURE_ENOUGH * 1.5]
    if len(strong) < 2:
        best = max(groups, key=lambda g: (len(g), max(w[3] for w in g)))
        offsets = [w[2] for w in best]
        return ([(0.0, 1.0, round(sum(offsets) / len(offsets), 2))],
                min(w[3] for w in best))
    groups = strong
    pieces = []
    for i, group in enumerate(groups):
        offsets = [w[2] for w in group]
        shift = round(sum(offsets) / len(offsets), 2)
        if i == 0:
            begins = 0.0
        else:
            # halfway between the end of the last window before and the start of the
            # first after: the change happened somewhere in there
            begins = round((groups[i - 1][-1][1] + group[0][0]) / 2.0, 1)
        pieces.append((begins, 1.0, shift))
    return pieces, min(w[3] for g in groups for w in g)


def from_ends(got, vtt, hz=HZ, max_shift=MAX_SHIFT):
    """How to put this subtitle right, from windows already listened to.

    Each window is placed on its own. Two that agree mean the subtitle is simply out by
    that much; two that disagree mean it drifts, and the answer is the line through
    both - measured across the whole film, since that is where the windows are.
    """
    if not got:
        return 1.0, 0.0, 0.0
    if len(got) == 1:
        return fit(got[0][1], vtt, hz=hz, max_shift=max_shift)
    placed = []
    for start, heard in got:
        marks = heard.get("speech") or heard.get("loud") or []
        if not marks:
            continue
        cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz, shift=-start)
        offset, sure = find_offset(marks, cues, hz=hz, max_shift=max_shift)
        placed.append((start + len(marks) / (2.0 * hz), offset, sure))
    if len(placed) < 2 or min(p[2] for p in placed) < SURE_ENOUGH:
        # only one end could be read: answer from that one, and say how sure it was
        best = max(placed, key=lambda p: p[2]) if placed else (0.0, 0.0, 0.0)
        return 1.0, best[1], best[2]
    (t1, o1, s1), (t2, o2, s2) = placed[0], placed[-1]
    if abs(o1 - o2) <= DRIFT_S:
        return 1.0, round((o1 + o2) / 2.0, 2), min(s1, s2)
    rate = snap_rate(1.0 + (o2 - o1) / (t2 - t1))
    return round(rate, 6), round(o1 - (rate - 1.0) * t1, 3), min(s1, s2)


def flat_enough(rate, seconds):
    """A rate that moves the last line by less than a third of a second is not a rate.

    Two windows measured a tenth of a second apart over twenty minutes will produce
    one, and calling that a drift is precision nobody asked for and nobody can see.
    """
    return abs((rate - 1.0) * (seconds or 0)) < 0.3


def pin_step(video, vtt, lo, hi, before, after, hz=HZ, max_shift=MAX_SHIFT,
             span=90, tries=3):
    """Find where a step actually happens, between two windows that disagree.

    The sweep says only that the change is somewhere in the gap - five minutes wide,
    which is five minutes of subtitles at the wrong offset. Listening at the middle of
    the gap says which side of it that minute belongs to, and doing that three times
    narrows it to well under a minute.
    """
    for _ in range(int(tries)):
        if hi - lo <= span:
            break
        middle = round((lo + hi) / 2.0, 1)
        heard = signals(video, hz=hz, seconds=span, start=max(0.0, middle - span / 2.0))
        marks = (heard.get("speech") or heard.get("loud") or []) if heard else []
        if not marks:
            break
        cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz,
                          shift=-(middle - span / 2.0))
        offset, sure = find_offset(marks, cues, hz=hz, max_shift=max_shift)
        if sure < SURE_ENOUGH * 0.6:
            break                        # a minute of nothing to hear: leave it
        if abs(offset - before) <= abs(offset - after):
            lo = middle                  # still the old timing here
        else:
            hi = middle                  # already the new one
    return round((lo + hi) / 2.0, 1)


def rated(got, vtt, hz=HZ, max_shift=MAX_SHIFT):
    """Try the framerates a subtitle is actually made at, when nothing else fits.

    A subtitle written for a 25 fps broadcast and played against a 23.976 fps release is
    four per cent slow: ten seconds adrift inside a four-minute window, so no window can
    place it and every reading is noise. There is no measuring a way out of that - but
    there are only a handful of ratios anybody ships, so each is tried and the one that
    makes both ends of the film agree is the answer.

    Returns (rate, offset, confidence) or None.
    """
    if len(got) < 2:
        return None
    best = None
    for rate in [1.0] + FRAME_RATES:
        bent = stretched(vtt, rate, 0.0) if abs(rate - 1.0) > 1e-9 else vtt
        readings = []
        for start, heard in (got[0], got[-1]):
            marks = heard.get("speech") or heard.get("loud") or []
            if not marks:
                continue
            cues = cue_frames(bent, len(marks) + int(max_shift * hz), hz=hz, shift=-start)
            readings.append(find_offset(marks, cues, hz=hz, max_shift=max_shift))
        if len(readings) < 2:
            continue
        sure = min(r[1] for r in readings)
        if sure < SURE_ENOUGH or abs(readings[0][0] - readings[1][0]) > SAME_S:
            continue
        if best is None or sure > best[2]:
            best = (rate, round((readings[0][0] + readings[1][0]) / 2.0, 2), sure)
    return best


def measure(video, vtt, duration=0.0, hz=HZ, max_shift=MAX_SHIFT):
    """The whole job for one film and one subtitle."""
    return from_ends(ends(video, duration, hz=hz), vtt, hz=hz, max_shift=max_shift)


def speech_frames(video, hz=HZ, seconds=LISTEN):
    """The plainer of the two readings, kept for anything that asks for just one."""
    return signals(video, hz=hz, seconds=seconds).get("loud", [])


#: What a subtitle for the deaf writes where there is no speech: "[♪ jovial music]",
#: "(door slams)", a line of musical notes. Those mark the moments the film is *not*
#: talking, which is the exact opposite of what is being looked for.
SOUND_ONLY = re.compile(r"\[[^\]]*\]|\([^)]*\)|♪[^♪]*♪|♪|<[^>]*>")


def speaks(said):
    """Whether a cue is somebody talking, rather than a noise being described."""
    left = SOUND_ONLY.sub("", said or "").strip()
    return bool(left.strip(" -–—.…"))


def cue_frames(vtt, count, hz=HZ, shift=0.0):
    """The subtitle in the same shape: a frame is 1 while a line of speech is up.

    Sound cues are left out. On one episode with a subtitle for the deaf they were
    forty-seven cues out of five hundred and sixty, and they were enough to make the
    film unplaceable: every window came back under the line, two of them absurd - +79.7
    and -32.4 seconds. Without them the same windows read +0.2 to +0.8.
    """
    out = [0] * max(0, count)
    # "01:02:03.400" and "02:03.400" are both WebVTT: ffmpeg leaves the hour out when
    # there is none, and a subtitle lifted out of a film is written that way
    stamp = r"(?:(\d+):)?(\d{1,2}):(\d\d[.,]\d+)"
    for m in re.finditer(stamp + r"\s*-->\s*" + stamp, vtt or ""):
        def secs(h, mm, ss):
            return int(h or 0) * 3600 + int(mm) * 60 + float(ss.replace(",", "."))
        a = secs(*m.group(1, 2, 3)) + shift
        b = secs(*m.group(4, 5, 6)) + shift
        # what this cue actually says, up to the blank line that ends it
        rest = (vtt or "")[m.end():]
        said = rest.split("\n\n")[0].split("\r\n\r\n")[0]
        if not speaks(said):
            continue
        for f in range(max(0, int(a * hz)), min(len(out), int(b * hz) + 1)):
            out[f] = 1
    return out


def _running(values):
    """Running totals, so any window's sum is one subtraction."""
    out = [0] * (len(values) + 1)
    for i, v in enumerate(values):
        out[i + 1] = out[i] + v
    return out


def _score(speech, cues, by, sums, floor=0.6):
    """Pearson's correlation between the two, where they overlap.

    Anything simpler rewards sliding most of the subtitle off the end of the film: a
    few cues left over a loud stretch look like a perfect match, and the answer comes
    back as "move it by ten minutes".
    """
    n = len(speech)
    lo, hi = max(0, by), min(n, len(cues) + by)
    width = hi - lo
    if width < n * floor:
        return -1.0
    hit = 0
    for i in range(lo, hi):
        if cues[i - by] and speech[i]:
            hit += 1
    a = sums["speech"][hi] - sums["speech"][lo]
    b = sums["cues"][hi - by] - sums["cues"][lo - by]
    if a < 20 or b < 20 or a >= width or b >= width:
        return -1.0
    top = hit - (a * b) / float(width)
    bottom = math.sqrt((a - a * a / float(width)) * (b - b * b / float(width)))
    return top / bottom if bottom else -1.0


def find_offset(speech, cues, hz=HZ, max_shift=MAX_SHIFT):
    """How far the subtitle is out, in seconds, and how sure that is, from 0 to 1.

    Coarse first, half a second a step, then finely around whatever won. Confidence is
    how far the winner stands above the ordinary run of answers: a subtitle that
    belongs to another film scores much the same wherever it is put, and says so.
    """
    if not speech or not cues:
        return 0.0, 0.0
    sums = {"speech": _running(speech), "cues": _running(cues)}
    span = int(max_shift * hz)
    coarse = max(1, hz // 2)
    tried = [(_score(speech, cues, by, sums), by)
             for by in range(-span, span + 1, coarse)]
    tried = [t for t in tried if t[0] > -1.0]
    if len(tried) < 5:
        return 0.0, 0.0
    best = max(tried)
    top = max((_score(speech, cues, by, sums), by)
              for by in range(best[1] - coarse, best[1] + coarse + 1))
    if top[0] <= 0.05:
        return 0.0, 0.0
    # How sure: a true match is a sharp spike. The best score five seconds away from it
    # is a tenth of the height for a subtitle that belongs to this film, and all but
    # level with it for one that does not - which is exactly the difference between
    # "move it by 1.4 seconds" and "I have no idea what this file is".
    away = max([s for s, by in tried if abs(by - top[1]) > 5 * hz] or [0.0])
    sure = 0.0 if top[0] < 0.12 else max(0.0, 1.0 - away / top[0])
    return round(top[1] / float(hz), 2), round(min(1.0, sure), 3)


def sync(video, vtt, seconds=LISTEN, hz=HZ, max_shift=MAX_SHIFT):
    """How many seconds this subtitle should be moved by, and how sure that is.

    Positive means the subtitle is early and should be held back. Below SURE_ENOUGH the
    caller should say it could not tell rather than move anything.
    """
    heard = signals(video, hz=hz, seconds=seconds)
    if not heard:
        return 0.0, 0.0
    return place(heard, vtt, hz=hz, max_shift=max_shift)


def place(heard, vtt, hz=HZ, max_shift=MAX_SHIFT):
    """The best answer either reading of the sound can give for this subtitle."""
    best = (0.0, 0.0)
    for marks in heard.values():
        if not marks:
            continue
        cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz)
        if sum(cues) < 20:
            continue
        offset, sure = find_offset(marks, cues, hz=hz, max_shift=max_shift)
        if sure > best[1]:
            best = (offset, sure)
    return best


#: Two subtitles are out by the same amount all the way through, or they are not. Half a
#: second of difference between the beginning and the end is drift, and no single number
#: puts a drifting subtitle right.
DRIFT_S = 0.6


#: The ratios between the framerates films are actually shipped at. A subtitle made
#: for a 25 fps broadcast and played against a 23.976 fps release runs 4.27% fast; the
#: rest are the same family. Measuring gets close to one of these; using the exact
#: figure is right for the whole film rather than close for most of it.
FRAME_RATES = [24000 / 24001.0, 24 / 25.0, 23.976 / 24.0, 23.976 / 25.0,
               24 / 23.976, 25 / 24.0, 25 / 23.976, 30 / 29.97, 29.97 / 30.0,
               24001 / 24000.0]


def snap_rate(rate, within=0.0008):
    """A measured rate, rounded to a real framerate ratio when it is plainly one."""
    for known in FRAME_RATES:
        if abs(rate - known) <= within:
            return known
    return rate


def fit(heard, vtt, hz=HZ, max_shift=MAX_SHIFT):
    """How to put this subtitle right: an offset, and a rate if it drifts.

    A subtitle made for another framerate, or for an edit with different breaks in it,
    is a second out at the start and four seconds out at the end - and a single
    correction, measured over the whole film, is wrong at both ends. So it is measured
    twice, over the first part and the last, and if the two disagree the answer is the
    straight line between them: a time in the file becomes `a * t + b`.

    Returns (a, b, confidence). A subtitle that is merely late comes back with a of 1.
    """
    which = "speech" if heard.get("speech") else "loud"
    marks = heard.get(which) or []
    if len(marks) < hz * 300:                 # under five minutes: one answer will do
        offset, sure = place(heard, vtt, hz=hz, max_shift=max_shift)
        return 1.0, offset, sure
    cut = len(marks) // 2
    cues = cue_frames(vtt, len(marks) + int(max_shift * hz), hz=hz)
    early = find_offset(marks[:cut], cues[:cut + int(max_shift * hz)], hz=hz,
                        max_shift=max_shift)
    late_cues = cue_frames(vtt, len(marks) - cut + int(max_shift * hz), hz=hz,
                           shift=-cut / float(hz))
    late = find_offset(marks[cut:], late_cues, hz=hz, max_shift=max_shift)
    whole = place(heard, vtt, hz=hz, max_shift=max_shift)
    if min(early[1], late[1]) < SURE_ENOUGH:
        return 1.0, whole[0], whole[1]        # one half could not be read: keep it simple
    if abs(early[0] - late[0]) <= DRIFT_S:
        return 1.0, whole[0], max(whole[1], min(early[1], late[1]))
    # the middle of each half, in seconds, is where each answer belongs
    t1 = (cut / 2.0) / hz
    t2 = (cut + (len(marks) - cut) / 2.0) / hz
    a = snap_rate(1.0 + (late[0] - early[0]) / (t2 - t1))
    b = early[0] - (a - 1.0) * t1
    # Two windows give the slope; they do not give the last half-second of the
    # intercept, because each answer is only as fine as the step it was found on. So
    # the line is laid over the subtitle and what is left over is measured once more
    # and folded in - which is the difference between "nearly right" and right.
    left = find_offset(marks, cue_frames(stretched(vtt, a, b),
                                         len(marks) + int(max_shift * hz), hz=hz),
                       hz=hz, max_shift=max_shift)
    if left[1] >= SURE_ENOUGH * 0.6:
        b = b + left[0]
    return round(a, 6), round(b, 3), min(early[1], late[1])


def stretched(vtt, rate, shift):
    """The same subtitle with every timestamp put through rate * t + shift."""
    def moved(stamp):
        p = stamp.replace(",", ".").split(":")
        at = ((int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])) if len(p) == 3
              else int(p[0]) * 60 + float(p[1]))
        at = max(0.0, at * rate + shift)
        return "%02d:%02d:%06.3f" % (int(at // 3600), int(at % 3600 // 60), at % 60)

    stamp = r"(?:\d+:)?\d+:\d\d[.,]\d+"
    return re.sub(r"(%s)(\s*-->\s*)(%s)" % (stamp, stamp),
                  lambda m: moved(m.group(1)) + " --> " + moved(m.group(3)), vtt or "")
