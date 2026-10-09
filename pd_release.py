"""What a release is, read off its name and size: source, codec, group, bitrate, and how it ranks against
what somebody asked for. Pure rules - nothing here touches the library, the disk or the network.

Moved out of pd-server.py, where every name is still imported, so callers are unchanged.
"""

import os
import re


#: What the words in a release name mean, for the wishes a house sets about what to
#: fetch. A name is all there is to go on before anything is downloaded: the file
#: itself cannot be probed until it is here.
RELEASE_WORDS = {
    "source": {
        "remux": ("remux",),
        # a rip of the disc counts as the disc: it is the same picture, encoded
        "bluray": ("bluray", "blu-ray", "bdrip", "brrip", "bdremux"),
        # bare WEB is what a streaming service sent, the same as WEB-DL
        "webdl": ("web-dl", "webdl", "web dl", " web "),
        "webrip": ("webrip", "web-rip"),
        "hdtv": ("hdtv", "pdtv"),
        "dvdrip": ("dvdrip", "dvd-rip", "dvdr", "xvid"),
    },
    "codec": {
        "h265": ("x265", "h265", "h.265", "h 265", "hevc"),
        "h264": ("x264", "h264", "h.264", "h 264", "avc"),
    },
    "sound": {
        "atmos": ("atmos",),
        "truehd": ("truehd", "true-hd", "true hd"),
        "dtshd": ("dts-hd", "dtshd", "dts hd", "dts-x", "dtsx"),
        "ddp": ("ddp", "dd+", "eac3", "e-ac3", "ddp5", "ddp 5"),
        "ac3": ("ac3", "dd5", "dd 5", "dd2", "dd 2"),
        "dts": ("dts",),
        "aac": ("aac",),
    },
}


def release_is(low, kind, want):
    """Whether one release name meets one wish. A wish of "any" is always met.

    A wish can name several things - "bluray,webdl" - and any one of them meets it.
    Nothing chosen at all is the same as any.
    """
    wants = [w.strip() for w in str(want or "").lower().split(",") if w.strip()]
    if not wants or "any" in wants:
        return True
    if kind == "group":
        return group_of(low) in wants
    if kind != "res" and kind not in RELEASE_WORDS:
        return True                   # bitrate, quality, seeders: not read off the name
    for one in wants:
        if kind == "res":
            if one in low:
                return True
            continue
        words = (RELEASE_WORDS.get(kind) or {}).get(one) or ()
        if any(w in low for w in words):
            return True
    if kind == "res":
        return False
    # it names something of this kind that was not chosen: outside the pills. A name
    # that says nothing about it cannot be shown to be outside them, and passes.
    named = any(w in low for words in RELEASE_WORDS[kind].values() for w in words)
    return not named


#: what the sound takes of a file, Mbit/s, by the words in the release name - checked
#: in this order, so "TrueHD Atmos" is the lossless track and "DDP Atmos" the lossy one
AUDIO_MBIT = (("truehd", 5.0), ("true-hd", 5.0), ("dts-x", 4.0), ("dtsx", 4.0),
              ("dts-hd", 3.5), ("dtshd", 3.5), ("dts hd", 3.5), ("flac", 2.0),
              ("lpcm", 4.5), ("dts", 1.5), ("ddp", 0.7), ("dd+", 0.7), ("eac3", 0.7),
              ("e-ac3", 0.7), ("ac3", 0.45), ("dd5", 0.45), ("dd 5", 0.45),
              ("dd2", 0.2), ("aac", 0.2), ("opus", 0.15))


#: h265 holds the same picture in fewer bits: its bitrate counted as this much h264
H265_WORTH = 1.6


def rate_band(want):
    """(least, most) Mbit/s for a wish, or None when it sets no bound.

    A single number is the ceiling it always was, so a house that set one before keeps
    what it asked for.
    """
    want = str(want or "any").strip().lower()
    if not want or want == "any":
        return None
    try:
        if "-" in want:
            least, most = want.split("-", 1)
            low = 0.0 if least.strip() in ("", "any") else float(least)
            high = 1e9 if most.strip() in ("", "any") else float(most)
            if low <= 0 and high >= 1e9:
                return None
            return low, high
        return 0.0, float(want)
    except ValueError:
        return None


def audio_mbit(low):
    """What the sound of a release takes, from its name; a plain track when it says none."""
    for word, mbit in AUDIO_MBIT:
        if word in low:
            return mbit
    return 0.6


def release_codec(low):
    """h265, h264 or "" from a release name."""
    for kind in ("h265", "h264"):
        if any(w in low for w in RELEASE_WORDS["codec"][kind]):
            return kind
    return ""


def video_mbit(name, size, seconds):
    """The picture's bitrate: the file's average less what the sound takes. None when
    the size or the length is not known."""
    if not size or not seconds or seconds < 60:
        return None
    low = " " + str(name or "").lower() + " "
    return max(0.1, size * 8.0 / seconds / 1e6 - audio_mbit(low))


def quality_of(name, size, seconds):
    """One figure to compare releases by: the picture's bitrate as h264 would need it."""
    mbit = video_mbit(name, size, seconds)
    if mbit is None:
        return None
    low = " " + str(name or "").lower() + " "
    return round(mbit * (H265_WORTH if release_codec(low) == "h265" else 1.0), 1)


def per_gb(name, size, seconds):
    """The quality figure for each gigabyte of the file: what the disk buys."""
    got = quality_of(name, size, seconds)
    if got is None or not size:
        return None
    return round(got / (size / 1073741824.0), 2)


def meets_all(one, wishes, stored, seconds):
    """Whether a release is inside every pill that is set: resolution, bitrate window,
    release, encoding and sound. Only such a release is put forward."""
    name = one.get("name")
    low = " " + str(name or "").lower() + " "
    for kind, want in wishes:
        if kind in ("quality", "seeds"):
            continue
        if kind == "rate":
            if not rate_fits(name, int(one.get("size") or 0), seconds, stored):
                return False
        elif not release_is(low, kind, want):
            return False
    return True


#: what each kind of wish is called in a reason
MISS_NAMES = {"res": "resolution", "source": "release", "codec": "encoding",
              "sound": "sound"}


def misses(one, wishes, stored, seconds):
    """Which of the rules under Choosing a release falls outside, each with why, and
    how far outside it is altogether: one point a rule, and for the bitrate the share
    it lies beyond the window as well, so 30 Mbit/s against 5-8 is further than 9."""
    name = one.get("name")
    low = " " + str(name or "").lower() + " "
    out, far = [], 0.0
    for kind, want in wishes:
        if kind in ("quality", "seeds"):
            continue
        if kind == "rate":
            size = int(one.get("size") or 0)
            if rate_fits(name, size, seconds, stored):
                continue
            mbit = video_mbit(name, size, seconds) or 0.0
            field = "preferRate265" if release_codec(low) == "h265" else "preferRate264"
            band = rate_band(str((stored or {}).get(field) or "any").lower()) or (0, 0)
            least, most = band
            edge = most if mbit > most else least
            beyond = abs(mbit - edge) / max(edge, 0.1)
            out.append("%.1f Mbit/s, window %g-%g" % (mbit, least, most))
            far += 1 + beyond
        elif not release_is(low, kind, want):
            said = str(want) if kind == "res" else str(want).upper()
            out.append("%s not %s" % (MISS_NAMES.get(kind, kind), said.replace(",", "/")))
            far += 1
    return out, round(far, 2)


def reasons_of(one, height, wishes, stored, seconds):
    """Each rule under Choosing a file falls outside, as a word the Upgrade tab can
    filter by - the resolution and the bitrate with which way they miss - and how far:
    the share of the window or of the height wanted it is out by, 1 for a yes/no rule."""
    name = str(one.get("name") or "")
    low = " " + name.lower() + " "
    out = {}
    for kind, want in wishes:
        if kind in ("quality", "seeds"):
            continue
        if kind == "rate":
            size = int(one.get("size") or 0)
            if rate_fits(name, size, seconds, stored):
                continue
            mbit = video_mbit(name, size, seconds) or 0.0
            field = "preferRate265" if release_codec(low) == "h265" else "preferRate264"
            band = rate_band(str((stored or {}).get(field) or "any").lower()) or (0, 0)
            edge = band[1] if mbit > band[1] else band[0]
            out["rate-higher" if mbit > band[1] else "rate-lower"] = round(
                abs(mbit - edge) / max(edge, 0.1), 3)
        elif not release_is(low, kind, want):
            if kind == "res":
                side = which_side({"name": name}, height, stored, seconds)
                wanted = {"2160p": 2160, "4k": 2160, "1080p": 1080, "720p": 720,
                          "480p": 480}.get(str(want).split(",")[0].strip().lower(), 0)
                tall = int(height or 0)
                out["res-" + side if side in ("higher", "lower") else "res"] = (
                    round(abs(tall - wanted) / wanted, 3) if wanted and tall else 1.0)
            else:
                out[kind] = 1.0
    return out


def which_side(one, height, stored, seconds):
    """Whether a file sits above what Choosing asks for, below it, or both: the bitrate
    against its window, the picture against the resolution wanted. "" when neither."""
    name = str(one.get("name") or "")
    low = " " + name.lower() + " "
    sides = set()
    want = str((stored or {}).get("preferRes") or "").lower()
    wanted = {"2160p": 2160, "4k": 2160, "1080p": 1080, "720p": 720, "480p": 480}.get(
        want.split(",")[0].strip(), 0)
    tall = int(height or 0) or (2160 if re.search(r"2160p|uhd|4k", low) else
                               1080 if "1080p" in low else 720 if "720p" in low else 0)
    if wanted and tall:
        # a picture is the height it was cut to, within a crop: 1036 is a 1080p film
        if tall > wanted * 1.3:
            sides.add("higher")
        elif tall < wanted * 0.8:
            sides.add("lower")
    size = int(one.get("size") or 0)
    if not rate_fits(name, size, seconds, stored):
        mbit = video_mbit(name, size, seconds) or 0.0
        field = "preferRate265" if release_codec(low) == "h265" else "preferRate264"
        band = rate_band(str((stored or {}).get(field) or "any").lower())
        if band and mbit:
            if mbit > band[1]:
                sides.add("higher")
            elif mbit < band[0]:
                sides.add("lower")
    return "both" if len(sides) > 1 else (sides.pop() if sides else "")


def rank_key(one, wishes, stored, seconds):
    """How one release sorts, row by row in the order the house set: a pill row by its
    first matching pill, quality by its figure in whole Mbit/s (or tenths per GB),
    seeders by the exact count. Each row sorts within the one above it."""
    name = one.get("name")
    size = int(one.get("size") or 0)
    low = " " + str(name or "").lower() + " "
    out = []
    for kind, want in wishes:
        if kind == "rate":
            out.append(rate_fits(name, size, seconds, stored))
        elif kind == "quality":
            # best inside the bitrate window that is set, not the biggest on offer:
            # one outside it is lowest on this row
            if want in ("pergb", "best") and not rate_fits(name, size, seconds, stored):
                out.append(-1)
            elif want == "pergb":
                out.append(round(per_gb(name, size, seconds) or 0, 1))
            elif want == "best":
                out.append(int(quality_of(name, size, seconds) or 0))
            else:
                out.append(0)
        elif kind == "seeds":
            out.append(int(one.get("seeds") or 0) if want == "most" else 0)
        else:
            out.append(preference(low, kind, want))
    return tuple(out)


def preference(low, kind, want):
    """How high a release sits in one row: its first matching pill, the first chosen
    counting most. A name that says nothing of this kind comes after every pill it
    could have matched; one outside the pills, lowest."""
    wants = [w.strip() for w in str(want or "").lower().split(",") if w.strip()]
    if not wants or "any" in wants:
        return 0
    if kind == "group":
        # the first chosen counts most; a group outside the chosen, lowest
        mine = group_of(low)
        return len(wants) - wants.index(mine) if mine in wants else -1
    for at, one in enumerate(wants):
        if release_is(low, kind, one):
            words = (RELEASE_WORDS.get(kind) or {}).get(one) or ()
            said = one in low if kind == "res" else any(w in low for w in words)
            if said:
                return len(wants) - at
    return 0 if release_is(low, kind, want) else -1


def rate_fits(name, size, seconds, stored):
    """Whether a release's picture stays inside the bitrate set for its encoding. Met
    when nothing is set or nothing is known."""
    mbit = video_mbit(name, size, seconds)
    if mbit is None:
        return True
    low = " " + str(name or "").lower() + " "
    field = "preferRate265" if release_codec(low) == "h265" else "preferRate264"
    want = str((stored or {}).get(field) or "any").lower()
    band = rate_band(want)
    if band is None:
        return True
    # a fifth either way: a release named for 6 Mbit/s lands anywhere near it
    least, most = band
    return least * 0.8 <= mbit <= most * 1.2


def for_series(stored):
    """Choosing as it applies to an episode: the series bitrate window in place of the
    films' one, when one is set."""
    stored = dict(stored or {})
    band = str(stored.get("seriesRate") or "same").lower()
    if band != "same":
        stored["preferRate264"] = band
        stored["preferRate265"] = band
    return stored


def height_of(name):
    """2160, 1080, 720 or 0 from a release name."""
    low = str(name or "").lower()
    return (2160 if re.search(r"2160p|\buhd\b|\b4k\b", low) else 1080 if "1080p" in low
            else 720 if "720p" in low else 0)


def picture_lines(height, width):
    """A picture's size in lines with letterboxing accounted for; see pd_localapi."""
    from pd_localapi import picture_lines as lines
    return lines(height, width)


def close_call(best, fits, stored, seconds):
    """The close-call rule under Choosing: among releases inside every rule and of the
    same resolution as the pick, any within the share set of its quality counts as
    level with it, and the most seeded of those is taken, the smaller on a tie."""
    try:
        share = float(str((stored or {}).get("closeCall") or "0").rstrip("%")) / 100.0
    except ValueError:
        share = 0.0
    if not best or share <= 0 or best.get("have"):
        return best
    top = quality_of(best.get("name"), int(best.get("size") or 0), seconds)
    if not top:
        return best
    tall = height_of(best.get("name"))
    near = [o for o in fits if height_of(o.get("name")) == tall
            and (quality_of(o.get("name"), int(o.get("size") or 0), seconds) or 0)
            >= top * (1 - share)]
    if not near:
        return best
    held = [o for o in near if o.get("have")]
    if held:
        return held[0]                    # one already here is level with the pick
    return max(near, key=lambda o: (int(o.get("seeds") or 0), -int(o.get("size") or 0)))


#: the groups the Upgrade tab can filter by and Choosing can prefer: one encoding
#: template under two names, 1080p Blu-ray x264 at 5.76 Mbit/s, the whole of both packs
TRUSTED_GROUPS = {"oft", "nikt0"}


def group_of(name):
    """"oft", "nikt0", or "other" for every other group and a name that names none."""
    got = release_group(str(name or "").strip())
    return got if got in TRUSTED_GROUPS else "other"


def release_group(name):
    """The group that made a release: the word after its last dash, "BTN" in
    "The.Office.S02E01.720p.BluRay.x264-BTN"."""
    stem = re.sub(r"\.(mkv|mp4|avi|m4v|ts)$", "", os.path.basename(str(name or "")), flags=re.I)
    stem = re.sub(r"\[[^\]]*\]$", "", stem).strip()
    found = re.search(r"-([A-Za-z0-9]{2,})$", stem)
    return found.group(1).lower() if found else ""
