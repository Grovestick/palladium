"""The amplifier in the house: a Yamaha receiver, reached over its own HTTP interface -
or a Denon or Marantz (telnet), an Onkyo, Integra or Pioneer (eISCP) or a Sony (its Audio
Control API). The others are written from their makers' published protocols and have not
been tried on a receiver here.

Sound passed through to the receiver never reaches the player's own volume leveling, so
a quiet Dolby track stayed quiet. While a film's sound is passed through, the receiver
is turned up by a set number of steps, and down by as many when the film ends - from
wherever it then is, so a change made by hand meanwhile is kept.

The steps are counted as the receiver's display counts: +5 is 5.0 more on it, ten
presses of its volume button (each press is 0.5).
"""
import json
import os
import socket
import struct
import threading
import time
import urllib.request

#: what is set, written in the server's settings under "receiver"
DEFAULTS = {"on": False, "ip": "192.168.0.140", "steps": 5,
            # "steps": by the steps set; "loudness": by what the film measured
            "mode": "steps",
            # the one screen whose passthrough moves it: the television in front of it
            "device": "192.168.0.157",
            # on the copy server: "main" takes the main server's settings, "own" keeps
            # what is set here, "off" leaves the receiver alone
            "from": "main",
            # whose receiver: yamaha, denon (and Marantz), onkyo (Integra, Pioneer), sony
            "brand": "yamaha"}
#: the makers spoken to, as Settings names them
BRANDS = (("yamaha", "Yamaha"), ("denon", "Denon / Marantz"), ("onkyo", "Onkyo / Pioneer"),
          ("sony", "Sony"))
#: the most it will be raised to, whatever the steps say: a mistake must not deafen
CEILING = 130
#: by loudness, the most it moves either way, in dB
MOST_UP, MOST_DOWN = 15.0, 8.0
#: a raise older than this is dropped unlowered
STALE = 8 * 3600
#: who raised it and by how much, so the same raise is undone once - kept on disk too:
#: a server restarted mid-film forgot the raise, and the receiver stayed up
RAISED = {}
LOCK = threading.Lock()
#: where that is kept; set by the server to a file beside its settings
KEPT = {"path": "", "read": False}


def _load(stored=None):
    if KEPT["read"] or not KEPT["path"]:
        return
    KEPT["read"] = True
    try:
        with open(KEPT["path"], encoding="utf-8") as f:
            RAISED.update(json.load(f) or {})
    except (OSError, ValueError):
        pass
    # the older form named the app's version as well: one screen, newest raise only
    newest = {}
    for k in [k for k in RAISED if k.count("|") >= 2]:
        was = RAISED.pop(k)
        ip = k.split("|", 1)[0]
        if float(was.get("at") or 0) >= float((newest.get(ip) or ("", {}))[1].get("at") or 0):
            newest[ip] = (k.rsplit("|", 1)[1], was)
    for ip, (key, was) in newest.items():
        RAISED["%s|%s" % (ip, key)] = was
    # older than any film: left by a screen that never said it ended. Dropped
    # without touching the volume, which has been set by hand since
    for who, was in list(RAISED.items()):
        if time.time() - float(was.get("at") or 0) > STALE:
            RAISED.pop(who, None)
    _keep()
    # one that was ending when the server stopped is undone now
    for who, was in list(RAISED.items()):
        if was.get("ending"):
            threading.Timer(GRACE, _lower_now, args=(stored or {}, who)).start()


def _keep():
    if not KEPT["path"]:
        return
    try:
        with open(KEPT["path"], "w", encoding="utf-8") as f:
            json.dump(RAISED, f)
    except OSError:
        pass


def _ask(ip, path, timeout=4):
    url = "http://%s/YamahaExtendedControl/v1/%s" % (ip, path)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def settings(stored):
    said = dict(DEFAULTS)
    said.update({k: v for k, v in ((stored or {}).get("receiver") or {}).items()
                 if k in DEFAULTS})
    try:
        said["steps"] = max(1, min(20, int(said["steps"])))
    except (TypeError, ValueError):
        said["steps"] = DEFAULTS["steps"]
    said["on"] = bool(said["on"])
    said["mode"] = "loudness" if said.get("mode") == "loudness" else "steps"
    said["ip"] = str(said["ip"] or "").strip()
    said["device"] = str(said["device"] or "").strip()
    said["from"] = said.get("from") if said.get("from") in ("main", "own", "off") else "main"
    said["brand"] = said.get("brand") if said.get("brand") in dict(BRANDS) else "yamaha"
    if said["from"] == "off":
        said["on"] = False
    return said


def for_copy(main, here):
    """The copy server's receiver settings: the main server's while it takes those, its
    own otherwise - turned off with "off". `main` and `here` as stored."""
    mine = settings({"receiver": here or {}})
    if mine["from"] == "main" and isinstance(main, dict) and main:
        mine = settings({"receiver": dict(main, **{"from": "main"})})
    return mine


def status(stored):
    """Whether the receiver answers, and what it is doing - for the settings tab."""
    one = settings(stored)
    out = {"settings": one, "connected": False, "raised": dict(RAISED)}
    if not one["ip"]:
        return out
    if one.get("brand", "yamaha") != "yamaha":
        try:
            power, vol, most = _volume(one)
        except Exception as e:
            out["why"] = str(e)[:120]
            return out
        out.update(connected=True, model=dict(BRANDS).get(one["brand"]), power=power,
                   volume=vol, shown=vol / 2.0, maxVolume=most)
        return out
    try:
        info = _ask(one["ip"], "system/getDeviceInfo")
        now = _ask(one["ip"], "main/getStatus")
    except Exception as e:
        out["why"] = str(e)[:120]
        return out
    shown = (now.get("actual_volume") or {}).get("value")
    out.update(connected=info.get("response_code") == 0, model=info.get("model_name"),
               power=now.get("power"), input=now.get("input"), volume=now.get("volume"),
               shown=shown, maxVolume=now.get("max_volume"), mute=now.get("mute"))
    return out


#: per maker, the most it is ever raised to, in its half-decibel steps: about +1 dB on
#: each one's own scale, so a mistake does not deafen
CEILINGS = {"yamaha": CEILING, "denon": 162, "onkyo": 120, "sony": 60}


def denon_volume(said):
    """Denon/Marantz's answer to MV? in half-decibel steps: "MV50" 100, "MV505" 101; None
    for anything else (its MVMAX line among them)."""
    for line in str(said or "").replace(chr(13), chr(10)).split(chr(10)):
        line = line.strip()
        if line.startswith("MV") and line[2:].isdigit():
            digits = line[2:]
            return int(digits[:2]) * 2 + (1 if len(digits) > 2 and digits[2] == "5" else 0)
    return None


def denon_set(steps):
    """The command that sets a Denon/Marantz to these half-decibel steps: "MV50", "MV505"."""
    steps = max(0, int(steps))
    return "MV%02d%s" % (steps // 2, "5" if steps % 2 else "")


def iscp_packet(command):
    """An eISCP message for Onkyo, Integra and Pioneer: "MVLQSTN" asks the volume."""
    data = ("!1" + command + chr(13)).encode("ascii")
    return b"ISCP" + struct.pack(">IIB3x", 16, len(data), 1) + data


def iscp_volume(said):
    """An eISCP answer's volume, in half-decibel steps: "!1MVL2A" is 42 steps of the
    receiver's own, 84 here. None when it is not a volume."""
    text = said.decode("ascii", "replace") if isinstance(said, bytes) else str(said or "")
    at = text.find("!1MVL")
    if at < 0:
        return None
    try:
        return int(text[at + 5:at + 7], 16) * 2
    except ValueError:
        return None


def sony_volume(said):
    """Sony's getVolumeInformation answer: (volume, most), in half-decibel steps."""
    one = ((said or {}).get("result") or [[{}]])[0][0]
    return int(one.get("volume") or 0) * 2, int(one.get("maxVolume") or 50) * 2


def _telnet(ip, command, port=23, timeout=4, wait=0.6):
    """A line to a receiver's text port, and what it said back in the moment after."""
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.sendall(command if isinstance(command, bytes) else (command + chr(13)).encode("ascii"))
        s.settimeout(wait)
        got = b""
        try:
            while len(got) < 4096:
                part = s.recv(1024)
                if not part:
                    break
                got += part
        except socket.timeout:
            pass
    return got


def _sony(ip, service, method, params, timeout=4):
    body = json.dumps({"method": method, "id": 1, "params": params, "version": "1.1"}).encode()
    req = urllib.request.Request("http://%s:10000/sony/%s" % (ip, service), data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _volume(one):
    """(power "on"/"standby", volume, the most) of this receiver, in half-decibel steps."""
    ip, brand = one["ip"], one.get("brand") or "yamaha"
    if brand == "denon":
        power = "on" if b"PWON" in _telnet(ip, "PW?") else "standby"
        return power, int(denon_volume(_telnet(ip, "MV?").decode("ascii", "replace")) or 0), 196
    if brand == "onkyo":
        power = "on" if b"PWR01" in _telnet(ip, iscp_packet("PWRQSTN"), 60128) else "standby"
        return power, int(iscp_volume(_telnet(ip, iscp_packet("MVLQSTN"), 60128)) or 0), 200
    if brand == "sony":
        state = ((_sony(ip, "system", "getPowerStatus", []).get("result") or [{}])[0] or {}).get("status")
        vol, most = sony_volume(_sony(ip, "audio", "getVolumeInformation", [{}]))
        return ("on" if state == "active" else "standby"), vol, most
    now = _ask(ip, "main/getStatus")
    return now.get("power"), int(now.get("volume") or 0), int(now.get("max_volume") or 161)


def _set(one, volume):
    ip, brand = one["ip"], one.get("brand") or "yamaha"
    if brand == "denon":
        _telnet(ip, denon_set(volume), wait=0.2)
        return True
    if brand == "onkyo":
        _telnet(ip, iscp_packet("MVL%02X" % max(0, int(volume) // 2)), 60128, wait=0.2)
        return True
    if brand == "sony":
        got = _sony(ip, "audio", "setAudioVolume", [{"volume": str(int(volume) // 2), "output": ""}])
        return "error" not in got
    return _ask(ip, "main/setVolume?volume=%d" % int(volume)).get("response_code") == 0


#: what a passed-through track of typical loudness is raised by, in dB: 45.0 -> 50.0 on
#: the display, judged right by ear on a film and on an episode
CENTRE = 5.0
#: how far from typical a track may measure and still be corrected by its measurement;
#: further out it gets CENTRE alone
BAND = 4.0


#: the codecs whose decoder compresses by itself unless told not to
DOLBY = ("ac3", "eac3")


def reading_for(known, codec):
    """The loudness a passed-through track is judged by, or None while it is not made:
    for Dolby the reading with the track's own compression off, as the receiver
    plays it; for any other codec the one reading there is."""
    if not known or known.get("how") != "spread" or known.get("lufs") is None:
        return None
    if str(codec or "").lower() in DOLBY:
        return known.get("flat")
    return known.get("lufs")


#: readings of one file that failed at one size before it is left alone; it is tried
#: again once its size changes
GIVE_UP_AFTER = 3


def gave_up(known, size=None):
    """Whether a file's readings failed GIVE_UP_AFTER times at the size it has now."""
    known = known or {}
    return (int(known.get("failed") or 0) >= GIVE_UP_AFTER
            and (size is None or int(known.get("failedSize") or 0) == int(size or 0)))


def forget_failures(known):
    """The failed readings dropped from `known`, so those files are measured again; how
    many. A file with a reading keeps it."""
    n = 0
    for k in list(known):
        one = known[k]
        if isinstance(one, dict) and one.get("failed"):
            n += 1
            rest = {f: v for f, v in one.items() if f not in ("failed", "failedSize", "failedWhen")}
            if rest:
                known[k] = rest
            else:
                del known[k]
    return n


def wants_measuring(known, codec, size=None):
    """Whether a file still has a reading to make: none yet, one from a single window,
    one of the file at another size, or - a Dolby track - none with compression off.
    Not one whose readings failed too often at this size."""
    if gave_up(known, size):
        return False
    if not known or known.get("how") != "spread" or known.get("lufs") is None:
        return True
    if size is not None and int(known.get("size") or 0) != int(size or 0):
        return True
    return str(codec or "").lower() in DOLBY and known.get("flat") is None


def measure_order(rows, known):
    """The files left to measure, the last one first to be taken: (id, path, size).

    rows are (id, path, size, codec), newest id first. Taken first: the files with no
    reading; then those read from a single window; last the Dolby tracks that only
    lack the reading with compression off - they already play at a level.
    """
    lack, window, none = [], [], []
    for ident, path, size, codec in rows:
        one = known.get(str(ident))
        if gave_up(one, size):
            continue
        if one is None:
            none.append((ident, path, size))
        elif one.get("how") != "spread":
            window.append((ident, path, size))
        elif wants_measuring(one, codec):
            lack.append((ident, path, size))
    return lack + window + none


def first_soon(soon, known, codecs, busy=()):
    """Of the files about to be watched, in the order they will be, the first still
    wanting a reading and not on a drive somebody is watching from: (id, path, size),
    or None. Taken before the backlog: a reading matters to the next episode, not to a
    film nobody has opened in a year."""
    for ident, path, size in soon:
        if not wants_measuring(known.get(str(ident)), codecs.get(str(ident), "")):
            continue
        if os.path.splitdrive(path or "")[0].upper() in busy:
            continue
        return (ident, path, size)
    return None


def soon_keys(progress, next_of, finished, many=3):
    """The titles about to be watched, nearest first, from (key, position, duration)
    rows newest first: a film or episode left part-way, and the episodes after one -
    `many` of them - in the order the series runs. `next_of(key)` is the episode after,
    or None; `finished(position, duration)` says whether it was watched to the end."""
    out = []

    def add(key):
        if key and key not in out:
            out.append(key)

    for key, position, duration in progress:
        key = str(key)
        done = finished(position, duration)
        if not done:
            add(key)
        if not key.startswith("e"):
            continue
        step, ahead = key, many - (0 if done else 1)
        for _ in range(max(0, ahead)):
            step = next_of(step)
            if not step:
                break
            add(str(step))
    return out


def typical(readings, least=30):
    """The median of the measured loudness of a kind of title, or None for too few."""
    known = sorted(float(v) for v in readings if v is not None)
    if len(known) < least:
        return None
    mid = len(known) // 2
    return known[mid] if len(known) % 2 else (known[mid - 1] + known[mid]) / 2.0


def level_for(lufs, usual, centre=CENTRE, band=BAND):
    """dB the receiver moves for a passed-through track.

    A track of typical loudness (usual: the median of its kind) gets centre. One within
    band dB of typical gets centre plus how much quieter than typical it is, so the two
    come out alike. One further out - a very quiet or very loud mix, or a measurement
    that is off - gets centre alone, as does any track where typical is not known.
    The average over a library comes to centre.
    """
    if lufs is None:
        return None
    if usual is None:
        return round(float(centre), 1)
    off = float(usual) - float(lufs)
    return round(float(centre) + (off if abs(off) <= float(band) else 0.0), 1)


def raise_for(stored, who, key, db=None):
    """Sound is going through to the receiver for this screen: up by the steps set, or
    by db when set to follow loudness and the film has been measured.

    Kept by screen and title: the next episode starts before the last one has closed,
    and the last one's ending must lower only its own raise."""
    who = "%s|%s" % (who, key)
    one = settings(stored)
    if not one["on"] or not one["ip"]:
        return {"ok": False, "why": "off"}
    with LOCK:
        _load(stored)
        if who in RAISED:
            # back to the same film before its lowering came due: it stays up
            if RAISED[who].pop("ending", None):
                _keep()
                return {"ok": True, "back": True}
            return {"ok": True, "already": True}
        # Another film on the same screen, ending or never said to have ended: the
        # raise is taken over and set for this one, never stacked on top of it
        screen = who.rsplit("|", 1)[0]
        before = [k for k in RAISED if k.rsplit("|", 1)[0] == screen]
        try:
            power, vol, most = _volume(one)
        except Exception as e:
            return {"ok": False, "why": "the receiver did not answer: " + str(e)[:80]}
        if power != "on":
            return {"ok": False, "why": "the receiver is off"}
        # the display counts in halves of the receiver's own units, 0.5 dB each
        if one["mode"] == "loudness" and db is not None:
            units = int(round(max(-MOST_DOWN, min(MOST_UP, float(db))) * 2))
        else:
            units = one["steps"] * 2
        # where it stood before any raise, keeping what was changed by hand since
        base = vol - sum(int(RAISED[k].get("by") or 0) for k in before)
        for k in before:
            RAISED.pop(k, None)
        to = max(0, min(base + units, most, CEILINGS.get(one.get("brand") or "yamaha", CEILING)))
        if to != vol and not _set(one, to):
            _keep()
            return {"ok": False, "why": "the receiver refused"}
        if to != base:
            RAISED[who] = {"by": to - base, "key": str(key), "at": time.time(), "to": to}
        _keep()
        return {"ok": True, "from": vol, "to": to, "db": db,
                "carried": bool(before)}


#: how long an ended film's raise waits before it is undone. None: each episode starts
#: at the volume set by hand and is raised again when its own passthrough is seen
GRACE = 0


def lower_for(stored, who, key):
    """That screen's film is over: down by as many steps as it was raised, a few seconds
    from now unless the next one on the same screen takes the raise over."""
    who = "%s|%s" % (who, key)
    with LOCK:
        _load(stored)
        if who not in RAISED:
            return {"ok": True, "nothing": True}
        RAISED[who]["ending"] = time.time()
        _keep()
    if GRACE <= 0:
        return _lower_now(stored, who) or {"ok": True}
    threading.Timer(GRACE, _lower_now, args=(stored, who)).start()
    return {"ok": True, "lowering in": GRACE}


#: a lowering the receiver did not take is tried again: seconds between tries, tries
RETRY_SECS, RETRIES = 5, 6


def _lower_now(stored, who, tries=RETRIES):
    one = settings(stored)
    with LOCK:
        if not (RAISED.get(who) or {}).get("ending"):
            return                        # taken over by the next episode
        _load()
        was = RAISED.get(who)
        if not was or not one["ip"]:
            RAISED.pop(who, None)
            _keep()
            return {"ok": True, "nothing": True}
        try:
            power, vol, _most = _volume(one)
            if power != "on":
                RAISED.pop(who, None)
                _keep()
                return {"ok": True, "off": True}
            # target fixed at the first try: a set that landed but timed out is not
            # subtracted a second time
            if "down" not in was:
                was["down"] = max(0, min(vol - int(was["by"]), CEILING))
                _keep()
            to = int(was["down"])
            if vol <= to or _set(one, to):
                RAISED.pop(who, None)
                _keep()
                return {"ok": True, "from": vol, "to": min(vol, to)}
            said = {"ok": False, "why": "the receiver refused"}
        except Exception as e:
            said = {"ok": False, "why": "the receiver did not answer: " + str(e)[:80]}
    # not lowered: the raise stays on record (the next film on that screen counts it
    # into its base) and the lowering is tried again
    if tries > 1:
        again = threading.Timer(RETRY_SECS, _lower_now, args=(stored, who, tries - 1))
        again.daemon = True
        again.start()
        said["again"] = tries - 1
    return said
