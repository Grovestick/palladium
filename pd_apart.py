"""Whether the main server and its cache are on different connections, and what to pause.

TorrentLeech allows one account, used from one address. The main server taken away
from home - to a LAN - with the cache left at home puts the account on two addresses at
once, which is what the tracker treats as a shared account. The main server says where
it is to the cache every few minutes, by the cache's outside address when the home one
does not answer, and each compares the other's public address with its own. The owner
chooses which of the two stops using the tracker while they are apart: off, host,
cache or both.

Stopping means every running torrent stopped - and remembered, so exactly those start
again - and no feed read, no search and no new download on that machine.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

CHOICES = ("off", "host", "cache", "both")
#: a word from the other machine is trusted this long
HEARD_FOR = 15 * 60
#: how often the main server says where it is
EVERY = 3 * 60

STATE = {"mine": "", "mine_at": 0.0, "theirs": "", "heard": 0.0, "home": "",
         "choice": "host", "role": "", "held": False, "stopped": [], "why": "",
         "root": "", "hooks": {}}


def _book():
    return os.path.join(STATE["root"], "apart.json")


def _save():
    try:
        with open(_book(), "w", encoding="utf-8") as f:
            json.dump({"home": STATE["home"], "stopped": STATE["stopped"],
                       "held": STATE["held"], "choice": STATE["choice"]}, f)
    except OSError:
        pass


def _load():
    try:
        with open(_book(), encoding="utf-8") as f:
            said = json.load(f) or {}
        STATE.update(home=str(said.get("home") or ""),
                     stopped=list(said.get("stopped") or []),
                     held=bool(said.get("held")),
                     choice=str(said.get("choice") or "host"))
    except (OSError, ValueError):
        pass


def my_wan():
    """This machine's public address, asked afresh every five minutes: a laptop carried
    to another house is on another address the moment it is plugged in."""
    if STATE["mine"] and time.time() - STATE["mine_at"] < 300:
        return STATE["mine"]
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
            ip = r.read().decode().strip()
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
            STATE.update(mine=ip, mine_at=time.time())
    except Exception:
        pass
    return STATE["mine"]


def apart():
    """Whether the two are on different connections, as far as this machine knows."""
    mine, theirs = STATE["mine"], STATE["theirs"]
    fresh = theirs and time.time() - STATE["heard"] < HEARD_FOR
    if STATE["role"] == "host":
        if fresh:
            return bool(mine and theirs != mine)
        # the cache did not answer: away if this is not where they were last together
        return bool(mine and STATE["home"] and mine != STATE["home"])
    return bool(fresh and mine and theirs != mine)


def should_hold():
    choice = STATE["choice"] if STATE["choice"] in CHOICES else "host"
    return apart() and (choice == "both" or choice == STATE["role"])


def status():
    return {"role": STATE["role"], "choice": STATE["choice"], "apart": apart(),
            "held": STATE["held"], "mine": STATE["mine"], "theirs": STATE["theirs"],
            "heard": int(STATE["heard"] or 0), "home": STATE["home"],
            "stopped": len(STATE["stopped"]), "why": STATE["why"]}


def heard_from_host(wan, choice):
    """The main server saying where it is and what the owner chose: the cache's side."""
    STATE["theirs"] = str(wan or "")
    STATE["heard"] = time.time()
    if choice in CHOICES:
        STATE["choice"] = choice
    return {"wan": my_wan()}


# ---------------------------------------------------------------- holding

def _hold(on):
    """Stop, or start again, this machine's use of the tracker."""
    import pd_torrents
    import pd_tracker
    if on:
        stopped = pd_torrents.hold_all()
        STATE["stopped"] = sorted(set(STATE["stopped"]) | set(stopped))
    else:
        pd_torrents.release(STATE["stopped"])
        STATE["stopped"] = []
    pd_torrents.STATE["held"] = on
    pd_tracker.HELD["on"] = on
    STATE["held"] = on
    _save()
    say = STATE["hooks"].get("say")
    if say:
        say("tracker %s: %s" % ("paused" if on else "on again",
                                "the two servers are on different connections"
                                if on else "the two servers are together again"))


def _tick():
    my_wan()
    if STATE["role"] == "host":
        _tell_the_cache()
    want = should_hold()
    if want != STATE["held"]:
        _hold(want)
    elif want:
        # and anything started behind its back since - a pack added, a client restarted
        import pd_torrents
        more = pd_torrents.hold_all()
        if more:
            STATE["stopped"] = sorted(set(STATE["stopped"]) | set(more))
            _save()


def _tell_the_cache():
    hooks = STATE["hooks"]
    choice = hooks["choice"]() if hooks.get("choice") else "host"
    STATE["choice"] = choice
    doors, key = hooks["doors"]() if hooks.get("doors") else ([], "")
    if not key:
        return
    body = json.dumps({"key": key, "wan": my_wan(), "choice": choice}).encode("utf-8")
    for door in doors:
        if not door:
            continue
        try:
            url = (door.rstrip("/") + "/follow/apart?t="
                   + urllib.parse.quote(key))
            ask = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json",
                                                  "X-Palladium-App": "house"})
            with urllib.request.urlopen(ask, timeout=8) as r:
                said = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except Exception as e:
            STATE["why"] = "%s did not answer: %s" % (door, str(e)[:80])
            continue
        STATE["theirs"] = str(said.get("wan") or "")
        STATE["heard"] = time.time()
        STATE["why"] = ""
        if STATE["theirs"] and STATE["theirs"] == STATE["mine"]:
            if STATE["home"] != STATE["mine"]:
                STATE["home"] = STATE["mine"]      # where the two last were together
                _save()
        return


def _work():
    time.sleep(30)
    while True:
        try:
            _tick()
        except Exception as e:
            STATE["why"] = str(e)[:160]
        time.sleep(EVERY if STATE["role"] == "host" else 60)


def start(root, role, hooks):
    """role: "host" or "cache". hooks: choice() -> str, doors() -> ([address], key) for
    the host; say(text) for the log."""
    STATE.update(root=root, role=role, hooks=dict(hooks or {}))
    _load()
    if STATE["held"]:
        # held when the server stopped: the tracker stays paused until the next check
        import pd_torrents
        import pd_tracker
        pd_torrents.STATE["held"] = True
        pd_tracker.HELD["on"] = True
    if not STATE.get("thread"):
        STATE["thread"] = True
        threading.Thread(target=_work, name="palladium-apart", daemon=True).start()
