"""Notifications: everything that tells a viewer something without being asked.

One handler for them all. The line the owner sends to every screen in the house (the
notice); a watchlisted film that has gone a step - a cam for a viewer who shows those, a
real release, here - said once and blinking the Watchlist until it is opened; what has
just come in, and a race or a game somebody downloaded put on their watchlist. The
endpoints read and clear through here as well, so what a viewer is told is decided in
this file and nowhere else.

The server lends what this needs - its settings, its library, its watchlist and its
names for people - through attach(), once, as it loads.
"""
import os
import threading
import time
from typing import Any

import pd_log


def _not_attached(*args, **kwargs) -> Any:
    raise RuntimeError("pd_notify.attach() has not been called")


# the server's own, lent by attach()
read_settings = _not_attached
write_settings = _not_attached
local = _not_attached
put_on_watchlist = _not_attached
release_flat = _not_attached
name_of_token = _not_attached
peer_holds = _not_attached


def attach(**parts):
    """The server's settings, library, watchlist and names, for the passes below."""
    globals().update(parts)


# ---- the screen notice ------------------------------------------------------------------
#: A line for the screens in the house: what it says, which one it is, and when it
#: stops being said. Not written down - a notice nobody was in the room for is not
#: worth keeping until tomorrow.
#:
#: This is the channel a watch party will speak over: one line to every screen is the
#: hard half, and what that needs on top of it is a sender's name, a message per
#: viewer rather than one for the main server, and a guest allowed to send.
#: Numbered from the clock, not from nought: a screen ignores an id it has already
#: heard, so after a restart every notice and play command went unheard until the
#: count passed the last one before it.
NOTICE = {"id": int(time.time()), "text": "", "until": 0, "to": "", "play": "", "at": 0,
          "shelf": ""}
#: Raised the moment a notice is written, so the screens waiting on one are answered
#: at once rather than on their next visit. Cleared straight after: the flag is the
#: knock on the door, and the id is what says whether it was already heard.
NOTICE_RUNG = threading.Event()

def say(body):
    """POST /notice: a line for the screens, or nothing to take the last one down. What
    was set, for the answer."""
    words = str(body.get("text") or "").strip()[:200]
    hold = max(10, min(int(body.get("seconds") or 600), 86400))
    NOTICE["id"] = NOTICE.get("id", 0) + 1
    NOTICE["text"] = words
    # who it is for: nothing means the main server, an address means one screen,
    # "all" means everybody including whoever is watching from away
    NOTICE["to"] = str(body.get("to") or "").strip()[:60]
    NOTICE["until"] = time.time() + hold if words else 0
    # a line of words is not an instruction: whatever was to be played has
    # been played, and must not be started again by the next screen to ask
    NOTICE["play"] = ""
    NOTICE["at"] = 0
    NOTICE["shelf"] = ""
    # everyone waiting hears it now
    NOTICE_RUNG.set()
    NOTICE_RUNG.clear()
    return {"id": NOTICE["id"], "text": words, "seconds": hold}


# ---- the watchlist: what a viewer is told, and what blinks -------------------------------
def blinking(mine):
    """GET /watchlist/blink: what on this viewer's watchlist has come in or gone a step
    since they last opened it; the Watchlist heading blinks while there is any."""
    listed = {str(k) for k in mine.get("watchlist") or []}
    return [k for k in mine.get("watchBlink") or [] if str(k) in listed]


def seen(mine):
    """POST /watchlist/seen: the Watchlist opened, what was blinking has been seen.
    Whether anything changed, to be written."""
    if mine.get("watchBlink"):
        mine["watchBlink"] = []
        return True
    return False


def messages_on(mine):
    """Whether this viewer is told of their watchlist's news: "on" as it comes, or "off"."""
    return (mine or {}).get("watchMessages") != "off"


def notify_on(mine):
    """Whether this viewer's app is sent a notification with each blink: off unless they
    turned it on (owner, 2026-11-12)."""
    return (mine or {}).get("notify") == "on"


def light(mine, blinks, key, note):
    """One step on this viewer's watchlist: `key` lights their Watchlist, and with
    notifications on the same step waits for their app as `note` {key, title, how, kind}.
    Whether anything was added."""
    added = False
    if key not in blinks:
        blinks.append(key)
        added = True
    if notify_on(mine) and note:
        queue = list(mine.get("notifyQueue") or [])
        queue.append(dict(note, at=int(time.time())))
        mine["notifyQueue"] = queue[-50:]
        added = True
    return added


def notes_once(mine):
    """GET /notify: the steps this viewer's app has not posted yet, each handed over once."""
    queue = list(mine.get("notifyQueue") or [])
    if queue:
        mine["notifyQueue"] = []
    return queue if notify_on(mine) else []


def news_once(mine):
    """GET /watchlist/news: what this viewer has not been told yet, each said once, on
    whichever screen asks first - taken off as it is handed over. Nothing for a viewer who
    has turned messages off; what was waiting goes all the same."""
    news = list(mine.get("watchNews") or [])
    if news:
        mine["watchNews"] = []
    return news if messages_on(mine) else []


# ---- one set of messages, kept by the main server ----------------------------------------
#
# The copy works nothing out for itself: a file copied over from the main server is not
# news - it blinked and said "is here" a second time when the copy finished. What the copy
# downloaded itself is handed to the main server; a screen on the copy reads and clears
# its messages there.

def on_copy():
    """The main server this machine keeps copies of, or None on the main server."""
    import pd_torrents
    return pd_torrents._following()


#: until when the main server is taken for not answering: asked again only after it, so a
#: copy answering for a main server that sleeps does not wait four seconds on every
#: watchlist question (2026-11-13)
MAIN_AWAY = {"until": 0.0}
MAIN_AWAY_FOR = 60


def ask_main(who, op, what=None, patience=4):
    """A viewer's messages on the main server, from the copy: "news", "blink", "seen", or
    "tell" (what the copy found of its own). None when the main server does not answer -
    at once while the copy's rounds have not seen it up, and for a minute after an ask
    that went unanswered."""
    one = on_copy()
    if not one:
        return None
    # what the copy's own rounds last saw of the main server first: off there, not asked
    # at all; it is asked again once a round has seen it answer (owner, 2026-11-13)
    import pd_torrents
    if not pd_torrents.house_is_up() or time.time() < MAIN_AWAY["until"]:
        return None
    import pd_follow
    try:
        said = pd_follow.tell(one, "/follow/watch", {"who": who, "op": op, "what": what or {}},
                              patience)
    except Exception:
        MAIN_AWAY["until"] = time.time() + MAIN_AWAY_FOR
        return None
    MAIN_AWAY["until"] = 0.0
    return said


def for_copy(stored, who, op, what):
    """The main server's side of ask_main: (the answer, whether the settings changed)."""
    mine = stored if who == "me" else (stored.get("users") or {}).get(who)
    if not isinstance(mine, dict):
        return {"ok": False}, False
    if op == "news":
        had = bool(mine.get("watchNews"))
        return {"news": news_once(mine)}, had
    if op == "blink":
        return {"keys": blinking(mine)}, False
    if op == "notes":
        had = bool(mine.get("notifyQueue"))
        return {"notes": notes_once(mine)}, had
    if op == "seen":
        return {"ok": True}, seen(mine)
    if op == "tell":
        listed = {str(k) for k in mine.get("watchlist") or []}
        blink = [str(k) for k in mine.get("watchBlink") or []]
        news = list(mine.get("watchNews") or [])
        changed = False
        for k in (what or {}).get("blink") or []:
            if str(k) in listed and str(k) not in blink:
                blink.append(str(k))
                changed = True
        for n in (what or {}).get("notes") or []:
            if isinstance(n, dict) and n.get("key") and str(n["key"]) in listed:
                changed = light(mine, blink, str(n["key"]), n) or changed
        for n in (what or {}).get("news") or []:
            if isinstance(n, dict) and n.get("key"):
                news.append(n)
                changed = True
        if changed:
            mine["watchBlink"] = blink[-50:]
            mine["watchNews"] = news
        return {"ok": True}, changed
    return {"ok": False}, False


#: how often the watchlists' films not here yet are looked at for a source
WATCH_NEWS_EVERY = 600
#: a word kept for whoever has not opened anything since, this long
WATCH_NEWS_KEEP = 14 * 86400


def watch_news_pass():
    """Every viewer's watchlist: a catalogue film that has gone a step - a cam, for a
    viewer who shows those; a real release; here - is written down for that viewer to be
    told, and blinks their Watchlist while it is still on it: a viewer who shows cams sees
    two blinks, the cam and then the real one. Out already and not on the tracker, it is
    asked about, a few a minute. How many were written. The main server's work alone: the
    copy reads the same tracker, and told it all twice."""
    import pd_streaming
    if on_copy():
        return 0
    stored = read_settings() or {}
    if not stored:
        return 0
    told, changed, ask = 0, False, []
    people = [("me", stored)] + [(k, v) for k, v in (stored.get("users") or {}).items()
                                 if isinstance(v, dict)]
    today = time.strftime("%Y-%m-%d")
    for who, mine in people:
        keys = [str(k) for k in (mine.get("watchlist") or []) if str(k).startswith("rt")]
        # where each was last time; an older record of yes or no read as such
        had = {k: ("fetch" if v is True else "" if v is False else str(v or ""))
               for k, v in dict(mine.get("watchSources") or {}).items()}
        if not keys and not had:
            continue
        cams = local().cam_releases_of(mine) == "show"
        now, newly = pd_streaming.watch_changes(
            keys, had, lambda k, cams=cams: pd_streaming.seen_source(pd_streaming.source_of(k), cams))
        for key in keys:
            one = pd_streaming.one_of(key) or {}
            # asked about until a real release is found: a cam is not the end of it
            if (pd_streaming.SOURCE_STEP.get(now.get(key) or "", 0) < pd_streaming.SOURCE_STEP["fetch"]
                    and str(one.get("released") or "")[:10] <= today and one):
                ask.append(one)
        news = [n for n in (mine.get("watchNews") or [])
                if time.time() - float(n.get("at") or 0) < WATCH_NEWS_KEEP]
        fetched = {str(k) for k in mine.get("watchFetched") or []}
        blink = [str(k) for k in mine.get("watchBlink") or []]
        for key in newly:
            one = pd_streaming.one_of(key) or {}
            how = now.get(key) or pd_streaming.source_of(key)
            # downloaded by this viewer: its arrival says it is now on the watchlist, not this
            if key in fetched:
                continue
            said = {"key": key, "title": str(one.get("title") or ""), "how": how,
                    "at": int(time.time())}
            # which kind of cinema release, for the notification to say (owner, 2026-11-12)
            if how == "cam":
                said["kind"] = pd_streaming.cam_kind(one)
            news.append(said)
            told += 1
            # still on the list: the Watchlist blinks until they open it, and the app is
            # told the same step where they asked for notifications
            if key in keys:
                light(mine, blink, key, {k: said[k] for k in said if k != "at"})
        if blink != [str(k) for k in mine.get("watchBlink") or []]:
            mine["watchBlink"] = blink[-50:]
            changed = True
        # films that came in once stood first on Continue watching; no longer (owner, 2026-11-11)
        if mine.pop("deckArrivals", None) is not None:
            changed = True
        if mine.get("notifyQueue") and blink != [str(k) for k in mine.get("watchBlink") or []]:
            changed = True
        if now != had or news != (mine.get("watchNews") or []):
            mine["watchSources"] = now
            mine["watchNews"] = news
            changed = True
    if changed:
        write_settings(stored, merge=False)
    if ask:
        pd_streaming.ask_tracker_slowly(ask)
    return told


#: seconds between looks for watchlist titles that have just come in
WATCH_ARRIVALS_EVERY = 60
#: when the last look was: what came in since is what blinks. In memory - after a restart
#: the first look reaches back ten minutes
WATCH_ARRIVALS = {"at": 0.0}


def tokens_by_name(stored):
    """{name as written, lower case: token} for every invitation and the owner ("me"): how
    the tracker log's who is read as a person."""
    by_name = {}
    for token, mine in (stored.get("users") or {}).items():
        if isinstance(mine, dict):
            for n in (mine.get("myName"), name_of_token(token)):
                if n:
                    by_name.setdefault(str(n).strip().lower(), token)
    for n in (stored.get("myName"), stored.get("ownerName"), "you", "me"):
        if n:
            by_name[str(n).strip().lower()] = "me"
    return by_name


def came_in_at(con, key):
    """When the newest file of a film or an episode came in, 0 for one not here."""
    row = con.execute("SELECT MAX(COALESCE(f.mtime, f.ctime)) FROM file f WHERE f.episode_id=? "
                      "OR (f.item_id=? AND f.episode_id IS NULL)", (str(key), str(key))).fetchone()
    return float((row or [0])[0] or 0)


def games_onto_watchlists(stored, api, con, now=None):
    """A race or a game somebody downloaded, on their watchlist once it has come in - an
    episode, which nothing else puts there (owner, 2026-11-06). [(token, key)] added."""
    import pd_tracker
    with pd_tracker.LOCK:
        log = list(pd_tracker.STATE.get("log") or [])
    who_of = {release_flat(t.get("name")): str(t.get("who") or "") for t in log if not t.get("auto")}
    if not who_of:
        return []
    by_name = tokens_by_name(stored)
    users = stored.setdefault("users", {})
    added = []
    for card in api._games_arrived(con, now=now):
        key = str(card.get("ratingKey") or "")
        paths = [r[0] for r in con.execute("SELECT path FROM file WHERE episode_id=?", (key,))]
        who = next((who_of.get(release_flat(os.path.basename(p))) or
                    who_of.get(release_flat(os.path.basename(os.path.dirname(p)))) for p in paths
                    if who_of.get(release_flat(os.path.basename(p))) or
                    who_of.get(release_flat(os.path.basename(os.path.dirname(p))))), "")
        token = by_name.get(who.strip().lower()) if who else None
        mine = stored if token == "me" else users.get(token) if token else None
        if not isinstance(mine, dict):
            continue
        listed = [str(k) for k in mine.get("gamesListed") or []]
        if key in listed:
            continue
        mine["gamesListed"] = (listed + [key])[-100:]
        had = [str(k) for k in (mine.get("watchlist") or [])]
        if key not in had:
            put_on_watchlist(mine, [key])
        fetched = [str(k) for k in mine.get("watchFetched") or []]
        if key not in fetched:
            mine["watchFetched"] = (fetched + [key])[-100:]
        added.append((token, key))
    return added


def title_for_news(con, key):
    """A title as the line on screen names it: a film's title, an episode's programme and
    its place or its own name for a game; "" when not known."""
    import pd_streaming
    if str(key).startswith("rt"):
        return str((pd_streaming.one_of(key) or {}).get("title") or "")
    row = con.execute("SELECT title FROM item WHERE id=?", (str(key),)).fetchone()
    if row:
        return str(row[0] or "")
    row = con.execute("SELECT e.title, e.season, e.number, i.title FROM episode e JOIN item i "
                      "ON i.id = e.item_id WHERE e.id=?", (str(key),)).fetchone()
    if not row:
        return ""
    if row[3] in __import__("pd_sports").FILED_UNDER:
        return str(row[0] or row[3])
    return "%s S%02dE%02d" % (row[3], int(row[1] or 0), int(row[2] or 0))


def watch_arrivals_pass(now=None):
    """Every viewer's watchlist: a film or a game whose file has come in since the last
    look blinks the Watchlist heading until the viewer opens it (owner, 2026-11-06); a
    game somebody downloaded is put there first. A title added that was here already does
    not blink: its file came in long before. How many lit. On the copy: only what it
    downloaded itself, handed to the main server - a copy of the main server's file is not
    news."""
    import pd_streaming
    copy = on_copy()
    now = time.time() if now is None else now
    since = WATCH_ARRIVALS["at"] or now - 600
    WATCH_ARRIVALS["at"] = now
    stored = read_settings() or {}
    if not stored:
        return 0
    api = local()
    con = api.lib.db()
    lit = 0
    try:
        # the games somebody downloaded are the main server's to put on watchlists
        games = [] if copy else games_onto_watchlists(stored, api, con, now=now)
        changed = bool(games)
        people = [("me", stored)] + [(k, v) for k, v in (stored.get("users") or {}).items()
                                     if isinstance(v, dict)]
        for who, mine in people:
            blink = [str(k) for k in mine.get("watchBlink") or []]
            was = list(blink)
            came = []
            for key in [str(k) for k in mine.get("watchlist") or []]:
                if key in blink:
                    continue
                held = str((pd_streaming.one_of(key) or {}).get("here") or "") if key.startswith("rt") else key
                if (who, key) in games or (held and came_in_at(con, held) > since - 5):
                    came.append((key, held))
            if copy and came:
                # a copy of the main server's file is not news; its own downloads are
                theirs, _, up = peer_holds([h for _, h in came if h])
                came = [(k, h) for k, h in came if up and h not in theirs]
            told = []
            notes = []
            for key, held in came:
                note = {"key": key, "title": title_for_news(con, held or key), "how": "here"}
                if copy:
                    blink.append(key)
                    notes.append(note)
                else:
                    light(mine, blink, key, note)
                lit += 1
                # downloaded by them: said once, as now on the watchlist (owner, 2026-11-06)
                if key in [str(k) for k in mine.get("watchFetched") or []]:
                    told.append({"key": key, "title": title_for_news(con, held or key),
                                 "how": "fetched", "at": int(now)})
            if copy:
                if came:
                    ask_main(who, "tell", {"blink": [k for k, _ in came], "news": told,
                                           "notes": notes})
                continue
            if told:
                mine["watchNews"] = list(mine.get("watchNews") or []) + told
            if blink != was:
                mine["watchBlink"] = blink[-50:]
                changed = True
        if changed:
            write_settings(stored, merge=False)
    finally:
        con.close()
    return lit


def keep_watch_arrivals():
    """Every minute: what has just come in, on the watchlists that hold it."""
    import pd_beat
    pd_beat.sleep("watcharrivals", 60)
    while True:
        try:
            watch_arrivals_pass()
        except Exception as e:
            pd_log.raw("%s watch arrivals: %s%s" % (time.strftime("%H:%M:%S"), str(e)[:200], chr(10)))
        pd_beat.sleep("watcharrivals", WATCH_ARRIVALS_EVERY)


def keep_watch_news():
    """Every ten minutes: the watchlists looked at for films that have got a source."""
    import pd_beat
    pd_beat.sleep("watchnews", 300)
    while True:
        try:
            watch_news_pass()
        except Exception:
            pass
        pd_beat.sleep("watchnews", WATCH_NEWS_EVERY)

