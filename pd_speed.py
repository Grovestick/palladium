"""Every speed rule for qBittorrent, in one place. The strictest that applies wins:

  a viewer here running low           512 KB/s both ways
  somebody watching on either server  the race page's number, both ways (paused is not watching)
  neither, for a minute               no hold

Held through qBittorrent's alternative limits. The normal limits are never written, so
there is nothing to put back and a restart leaves nothing behind. No other code sets a
speed; a test keeps it so.
"""
import json
import threading
import time
import urllib.parse
import urllib.request

import pd_beat
import pd_torrents

#: a viewer here running low: seeding shares the disk the film is read from
GENTLE_BYTES = 512 * 1024
#: seconds a hold stays after its reason has gone
HOLD_AFTER = 60
#: seconds between rounds
EVERY = 5
#: seconds between asking who watches, here and on the other server: /watching is not light
ASK_WATCHING_EVERY = 30
#: seconds between looks at qBittorrent when the limit has not changed
CONFIRM_EVERY = 60
#: seconds between reads of the race page's number
READ_MBIT_EVERY = 60
#: until the race page has answered once
DEFAULT_MBIT = 200
RACE_PORT = 8785
#: seconds the race page's totals are kept for Now playing
TOTALS_EVERY = 5
TOTALS = {"at": 0.0, "got": {}}

STATE = {"mbit": DEFAULT_MBIT, "read": 0.0, "low": 0.0, "seen": 0.0, "said": None,
         "there": False, "askedThere": 0.0, "here": False, "askedHere": 0.0, "applied": None, "appliedAt": 0.0}


def wanted(low, watched, mbit):
    """The limit that applies, bytes a second, or 0 for no hold."""
    if low:
        return GENTLE_BYTES
    return int(mbit) * 125000 if watched else 0


def decide(low, watched, mbit, was, now):
    """wanted, with each reason lasting HOLD_AFTER past its end. was keeps when they last held."""
    if low:
        was["low"] = now
    if watched:
        was["seen"] = now
    return wanted(now - was.get("low", 0.0) < HOLD_AFTER,
                  now - was.get("seen", 0.0) < HOLD_AFTER, mbit)


def apply(qb, want):
    """The client put to it: the number before the mode goes on, so it never runs at an old one."""
    on = (qb._call("/api/v2/transfer/speedLimitsMode", timeout=6) or b"0").strip() == b"1"
    if want:
        prefs = qb.preferences()
        if prefs.get("alt_up_limit") != want or prefs.get("alt_dl_limit") != want:
            qb.set_preferences({"alt_up_limit": want, "alt_dl_limit": want})
    if bool(want) != on:
        qb._call("/api/v2/transfer/toggleSpeedLimitsMode", fields={}, timeout=6)


def put_back_old(qb, data):
    """The limits an older build eased and kept on disk, once. Its own number means none."""
    config = data.get("config") or {}
    kept = config.get("gentleWas")
    if not kept:
        return False
    down, up = (list(kept) + [0, 0])[:2]
    qb.set_preferences({"dl_limit": 0 if down == GENTLE_BYTES else int(down),
                        "up_limit": 0 if up == GENTLE_BYTES else int(up)})
    with pd_torrents.LOCK:
        config.pop("gentleWas", None)
        pd_torrents.save()
    return True


def viewing(rows):
    """Whether any of these rows from /watching is somebody really watching: not a copy going
    to the other server, and not a player left paused, for hours as like as not."""
    return any(r.get("how") != "syncing" and r.get("state") not in ("paused", "stopped") for r in rows)


def watched_there(url, key, now):
    """Whether anybody is watching from the other server, asked every ASK_WATCHING_EVERY seconds.
    One that does not answer is asleep or restarting, which is nobody watching from it."""
    if now - STATE["askedThere"] < ASK_WATCHING_EVERY:
        return STATE["there"]
    STATE["askedThere"] = now
    STATE["there"] = False
    if url:
        ask = url.rstrip("/") + "/watching" + ("?t=" + urllib.parse.quote(key) if key else "")
        try:
            with urllib.request.urlopen(ask, timeout=4) as answer:
                STATE["there"] = viewing(json.loads(answer.read()).get("live") or [])
        except Exception:
            pass
    return STATE["there"]


def watched_here(here, now):
    """here(), asked every ASK_WATCHING_EVERY seconds."""
    if now - STATE["askedHere"] >= ASK_WATCHING_EVERY:
        STATE["askedHere"] = now
        STATE["here"] = bool(here())
    return STATE["here"]


def apply_if_due(qb, want, now):
    """The client told only when the limit changes, and looked at once a minute to confirm."""
    if want != STATE["applied"] or now - STATE["appliedAt"] >= CONFIRM_EVERY:
        apply(qb, want)
        STATE["applied"], STATE["appliedAt"] = want, now
        return True
    return False


def ask_race(url, path):
    """The race page's answer to path: on this machine if it runs here, else on the other
    server. None when neither answers."""
    for host in ("127.0.0.1", urllib.parse.urlparse(url or "").hostname):
        if not host:
            continue
        try:
            with urllib.request.urlopen("http://%s:%d%s" % (host, RACE_PORT, path), timeout=4) as answer:
                return json.loads(answer.read())
        except Exception:
            continue
    return None


def read_mbit(url, now):
    """The race page's number: on this machine if it runs here, else on the other server."""
    if now - STATE["read"] < READ_MBIT_EVERY:
        return STATE["mbit"]
    STATE["read"] = now
    try:
        STATE["mbit"] = int(ask_race(url, "/api/settings")["values"]["KIND_MBIT"])
    except (TypeError, KeyError, ValueError):
        pass
    return STATE["mbit"]


def totals_of(panel):
    """Both machines' torrent speeds, bytes a second, and the tracker ratio, from the race
    page's panel answer; {} without one. read: the tracker's own ratio, not an estimate."""
    if not isinstance(panel, dict):
        return {}
    ratio = panel.get("ratio")
    return {"up": int(panel.get("up") or 0), "down": int(panel.get("down") or 0),
            "ratio": None if ratio is None else float(ratio), "read": bool(panel.get("ratioRead"))}


def totals(url, now):
    """totals_of the race page, asked at most every TOTALS_EVERY."""
    if now - TOTALS["at"] >= TOTALS_EVERY:
        TOTALS["at"] = now
        TOTALS["got"] = totals_of(ask_race(url, "/api/panel"))
    return TOTALS["got"]


def one_round(other, here, thin, log, now):
    url, key = other()
    low = thin() in ("panic", "back")
    # asked of the other server only when nobody here settles it
    watched = watched_here(here, now) or watched_there(url, key, now)
    want = decide(low, watched, read_mbit(url, now), STATE, now)
    apply_if_due(pd_torrents.QB(pd_torrents.load()["config"]), want, now)
    if want != STATE["said"]:
        STATE["said"] = want
        log("qBittorrent %s" % ("held to %d Mbit" % (want * 8 // 1000000) if want else "at full speed"))


def start(other, here, thin, log):
    """other() the other server's (address, key); here() whether anybody watches from this one;
    thin() how low a viewer here is; log(text) a line in the debug log."""
    def work():
        try:
            data = pd_torrents.load()
            put_back_old(pd_torrents.QB(data["config"]), data)
        except Exception:
            pass
        while True:
            try:
                one_round(other, here, thin, log, time.time())
            except Exception as why:
                # once per error: it is asked every few seconds
                said = str(why)[:160]
                if said != STATE.get("fault"):
                    STATE["fault"] = said
                    log("qBittorrent speed: %s" % said)
            pd_beat.sleep("speed", EVERY)

    threading.Thread(target=work, name="palladium-speed", daemon=True).start()
