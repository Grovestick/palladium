"""The amplifier in the house: a Yamaha receiver, reached over its own HTTP interface.

Sound passed through to the receiver never reaches the player's own volume leveling, so
a quiet Dolby track stayed quiet. While a film's sound is passed through, the receiver
is turned up by a set number of steps, and down by as many when the film ends - from
wherever it then is, so a change made by hand meanwhile is kept.

The steps are counted as the receiver's display counts: +5 is 5.0 more on it, ten
presses of its volume button (each press is 0.5).
"""
import json
import threading
import time
import urllib.request

#: what is set, written in the server's settings under "receiver"
DEFAULTS = {"on": False, "ip": "192.168.0.140", "steps": 5,
            # "steps": by the steps set; "loudness": by what the film measured
            "mode": "steps",
            # the one screen whose passthrough moves it: the television in front of it
            "device": "192.168.0.157"}
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
    return said


def status(stored):
    """Whether the receiver answers, and what it is doing - for the settings tab."""
    one = settings(stored)
    out = {"settings": one, "connected": False, "raised": dict(RAISED)}
    if not one["ip"]:
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


def _volume(ip):
    now = _ask(ip, "main/getStatus")
    return now.get("power"), int(now.get("volume") or 0), int(now.get("max_volume") or 161)


def _set(ip, volume):
    return _ask(ip, "main/setVolume?volume=%d" % int(volume)).get("response_code") == 0


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
            power, vol, most = _volume(one["ip"])
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
        to = max(0, min(base + units, most, CEILING))
        if to != vol and not _set(one["ip"], to):
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
            power, vol, _most = _volume(one["ip"])
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
            if vol <= to or _set(one["ip"], to):
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
