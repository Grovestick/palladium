"""Reading a season or episode span written the way people say it.

A collection rule matches titles, which is the right grain for films and the
wrong one for a programme: what somebody wants on a shelf is the middle of a
series, not all of it. So a rule can also name a span, in the shorthand everyone
already writes in a message about what to watch.

    s3              one whole season
    s3-s6           three whole seasons, ends included
    s3e34           one episode
    s3e34-s6e2      from that episode to that one, across the seasons between
    s3e34-          from there to the end of the programme
    -s6e2           everything up to and including that one
    s3 | s7-s9      a bar starts another span, as in the title rules

Written with a capital S, with an x instead of the e, or with a space between
the parts, all read the same. The point is that nobody should have to learn it.
"""
import re

# s3, s03e4, 3x04, and the same with any capitalisation or a space in the middle
_ONE = re.compile(r"""^\s*
    (?: s\s*(?P<season>\d{1,3}) )
    (?: \s*(?:e|x|\s)\s*(?P<episode>\d{1,4}) )?
    \s*$""", re.I | re.X)
# 3x04 written without the s
_ALT = re.compile(r"^\s*(?P<season>\d{1,3})\s*x\s*(?P<episode>\d{1,4})\s*$", re.I)


def _point(said):
    """One end of a span: (season, episode or None), or None if it is not one."""
    if not said or not said.strip():
        return None
    m = _ONE.match(said) or _ALT.match(said)
    if not m:
        return None
    ep = m.group("episode")
    return (int(m.group("season")), int(ep) if ep is not None else None)


def parse(said):
    """A written span to a list of (start, end) pairs, each an open-ended range.

    A bound is (season, episode) with episode None meaning the whole season, and
    a bound of None meaning open at that end. Anything unreadable is skipped
    rather than guessed at, so a typo narrows nothing instead of everything.
    """
    spans = []
    for chunk in str(said or "").split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # an en dash is what a phone puts there
        chunk = chunk.replace("–", "-").replace("—", "-")
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            lo, hi = _point(lo_s), _point(hi_s)
            if lo is None and hi is None:
                continue
            if lo_s.strip() and lo is None:
                continue                      # "sx-s6" is a typo, not open-ended
            if hi_s.strip() and hi is None:
                continue
            spans.append((lo, hi))
        else:
            one = _point(chunk)
            if one:
                spans.append((one, one))
    return spans


def _at_or_after(point, season, number):
    s, e = point
    if season != s:
        return season > s
    return True if e is None else number >= e


def _at_or_before(point, season, number):
    s, e = point
    if season != s:
        return season < s
    return True if e is None else number <= e


def holds(spans, season, number):
    """Is this episode inside any of the spans?"""
    try:
        season, number = int(season), int(number)
    except (TypeError, ValueError):
        return False
    for lo, hi in spans:
        if lo is not None and not _at_or_after(lo, season, number):
            continue
        if hi is not None and not _at_or_before(hi, season, number):
            continue
        return True
    return False


def seasons_touched(spans, season_numbers):
    """Which of a programme's seasons a span reaches at all."""
    out = []
    for s in sorted(set(int(x) for x in season_numbers)):
        lo_ok = any(lo is None or s >= lo[0] for lo, _hi in spans)
        hi_ok = any(hi is None or s <= hi[0] for _lo, hi in spans)
        if any((lo is None or s >= lo[0]) and (hi is None or s <= hi[0])
               for lo, hi in spans):
            out.append(s)
    return out


def describe(spans):
    """The span said back in words, for the line under the box."""
    if not spans:
        return ""
    bits = []
    for lo, hi in spans:
        def one(p):
            if p is None:
                return ""
            return "s%d" % p[0] if p[1] is None else "s%de%d" % p
        if lo == hi and lo is not None:
            bits.append("all of %s" % one(lo) if lo[1] is None else one(lo))
        elif lo is None:
            bits.append("up to and including %s" % one(hi))
        elif hi is None:
            bits.append("%s to the end" % one(lo))
        else:
            bits.append("%s to %s" % (one(lo), one(hi)))
    return ", ".join(bits)
