"""The other server: what it has added and holds, its live rows and marks, its readings and stills, asked for and taken.

Moved whole from pd-server.py, which imports them back by name; the server's own names,
these functions among them, are reached through S, the server module."""
import json
import os
import re
import threading
import time
from typing import Any

import pd_h
import pd_log

#: pd-server.py, put in pd_h by it before its parts are imported
S: Any = pd_h.SERVER


def peer_recent():
    """(films the other server holds, its name, whether it answered when last asked). Off means
    an ask failed: how long ago it answered said off for a list nobody had opened in a while."""
    if not S.PEER_FILMS["read"]:
        S.PEER_FILMS["read"] = True
        try:
            with open(os.path.join(S.ROOT, "peer_recent.json"), encoding="utf-8") as f:
                kept = json.load(f)
            S.PEER_FILMS.update(rows=kept["rows"], name=kept["name"], dated=kept.get("dated") or {},
                              music=kept.get("music") or [])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return (S.PEER_FILMS["rows"], S.PEER_FILMS["name"], S.PEER_FILMS["up"], S.PEER_FILMS["dated"],
            S.PEER_FILMS["music"])


def keep_peer_recent():
    """The other server asked every minute, whether or not anybody is looking at the list."""
    while True:
        S.peer_recent()
        S._ask_peer_recent()
        time.sleep(S.PEER_ASK_EVERY)


def copy_answers():
    """Whether a cache answers now, asked for each download a pack would send to it."""
    import urllib.request as _ureq
    for where, row in list(S.Handler.FOLLOWERS.items()):
        if row.get("revoked"):
            continue
        try:
            req = _ureq.Request(where.rstrip("/") + "/follow/slot", headers={"X-Palladium-House": "1"})
            with _ureq.urlopen(req, timeout=3):
                return True
        except Exception:
            continue
    return False


def peer_art(key, which):
    """The poster or backdrop of a film on the other server's list, fetched from it once and
    kept beside this server's own pictures; None for a key not on that list - this is no
    way to fetch anything else off the other machine - or when it cannot be had."""
    import urllib.parse as _uparse
    import urllib.request as _ureq
    import pd_torrents
    key = str(key or "")
    if which not in ("poster", "backdrop") or not re.fullmatch(r"[0-9a-f]{12}", key):
        return None
    if not (any(str(c.get("ratingKey") or "") == key for c in S.peer_recent()[0])
            or (S.local().file_for(key, 0) is None and key in S.peer_holds([key])[0])):
        return None
    kept = os.path.join(S.ROOT, "cache", "peer_%s_%s.jpg" % (key, which))
    try:
        with open(kept, "rb") as f:
            return f.read()
    except OSError:
        pass
    if S.peer_away():
        return None                       # not asked while it does not answer
    try:
        one = pd_torrents._following()
        if one:
            url = (one["master"].rstrip("/") + "/local/art/%s/%s?t=%s"
                   % (key, which, _uparse.quote(one["key"])))
            req = _ureq.Request(url, headers={"X-Palladium-App": "follower"})
        else:
            where = next((w for w, row in list(S.Handler.FOLLOWERS.items()) if not row.get("revoked")), "")
            if not where:
                return None
            # as a follower asks for pictures: the house header is for /follow, and the
            # other machine refused a picture asked with it (403)
            req = _ureq.Request(where.rstrip("/") + "/local/art/%s/%s" % (key, which),
                                headers={"X-Palladium-App": "follower"})
        with _ureq.urlopen(req, timeout=8) as answer:
            blob = answer.read()
    except Exception:
        return None
    # a picture, not an error page
    if not (blob[:2] == bytes([0xFF, 0xD8]) or blob[:4] == bytes([0x89, 80, 78, 71])):
        return None
    try:
        os.makedirs(os.path.dirname(kept), exist_ok=True)
        with open(kept + ".tmp", "wb") as f:
            f.write(blob)
        os.replace(kept + ".tmp", kept)
    except OSError:
        pass
    return blob


def _ask_peer_recent():
    import urllib.request as _ureq
    import pd_follow
    import pd_torrents
    try:
        said = None
        one = pd_torrents._following()
        if one:
            said = pd_follow.ask(one, "/follow/recent", 8)
        else:
            for where, row in list(S.Handler.FOLLOWERS.items()):
                if row.get("revoked") or time.time() - float(row.get("when") or 0) > 900:
                    continue
                req = _ureq.Request(where.rstrip("/") + "/follow/recent",
                                    headers={"X-Palladium-House": "1"})
                with _ureq.urlopen(req, timeout=8) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
                break
        if not (isinstance(said, dict) and isinstance(said.get("films"), list)):
            S.PEER_FILMS["up"] = False
            return
        S.PEER_FILMS.update(rows=said["films"], name=str(said.get("name") or ""), up=True,
                          dated=dict(said.get("dated") or {}), music=list(said.get("music") or []))
        path = os.path.join(S.ROOT, "peer_recent.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"rows": S.PEER_FILMS["rows"], "name": S.PEER_FILMS["name"],
                       "dated": S.PEER_FILMS["dated"], "music": S.PEER_FILMS["music"]}, f)
        os.replace(path + ".tmp", path)
    except Exception:
        S.PEER_FILMS["up"] = False          # off: its films stay as it last said, greyed


def peer_live():
    """(viewer address, title key) of every film the other server is sending now. Never waits:
    asked behind the caller, and an old answer stands in meanwhile."""
    if time.time() - S.PEER_LIVE["at"] >= 5 and not S.PEER_LIVE["asking"]:
        S.PEER_LIVE.update(at=time.time(), asking=True)
        threading.Thread(target=S._ask_peer_live, name="palladium-peer-live", daemon=True).start()
    return S.PEER_LIVE["marks"]


def _ask_peer_live():
    import urllib.request as _ureq
    import pd_follow
    import pd_torrents
    try:
        said = None
        one = pd_torrents._following()
        if one:
            said = pd_follow.ask(one, "/follow/live", 3)
        else:
            for where, row in list(S.Handler.FOLLOWERS.items()):
                if row.get("revoked"):
                    continue
                req = _ureq.Request(where.rstrip("/") + "/follow/live", headers={"X-Palladium-House": "1"})
                with _ureq.urlopen(req, timeout=3) as answer:
                    said = json.loads(answer.read().decode("utf-8", "replace") or "{}")
                break
        S.PEER_LIVE["marks"] = S.peer_marks(said)
    except Exception:
        S.PEER_LIVE["marks"] = {}
    finally:
        S.PEER_LIVE["asking"] = False


def peer_marks(said):
    """{(address, key): Mbit the other server sends} of each film being watched in a /watching
    answer; copies left out."""
    rows = (said or {}).get("live") if isinstance(said, dict) else None
    out = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("how") != "syncing" and r.get("key"):
            try:
                mbit = float(r.get("mbit") or 0)
            except (TypeError, ValueError):
                mbit = 0.0
            out[(str(r.get("address") or ""), str(r.get("key") or ""))] = mbit
    return out


def peer_away():
    """Whether the other server is not answering, by the same record the handover reads:
    asked at most every thirty seconds, two seconds at most. While it is away nothing waits
    on it - a page asking for posters waited eight seconds a poster with it off (2026-11-08)."""
    import pd_torrents
    one = pd_torrents._following()
    where = (one or {}).get("master") or next(
        (w for w, row in list(S.Handler.FOLLOWERS.items()) if not row.get("revoked")), "")
    return not where or not S.Handler.answering_now(S.Handler, where)


def peer_fetch(path, timeout=30):
    """One answer from the other server as bytes, either way round: a copy asks the main
    server with its key, the main server asks its copy as the house. None when nobody
    answers, or the other server is marked as not answering."""
    import urllib.parse as _uparse
    import urllib.request as _ureq
    import pd_torrents
    if S.peer_away():
        return None
    one = pd_torrents._following()
    if one:
        url = (one["master"].rstrip("/") + path + ("&" if "?" in path else "?")
               + "t=" + _uparse.quote(one["key"]))
        req = _ureq.Request(url, headers={"X-Palladium-App": "follower"})
    else:
        where = next((w for w, row in list(S.Handler.FOLLOWERS.items()) if not row.get("revoked")), "")
        if not where:
            return None
        req = _ureq.Request(where.rstrip("/") + path, headers={"X-Palladium-House": "1"})
    try:
        with _ureq.urlopen(req, timeout=timeout) as answer:
            return answer.read()
    except Exception:
        return None


def peer_reading(key):
    """The other server's reading of a film this one does not hold: {reading, codec} or {}.
    Asked once in ten minutes a title: the receiver asks every fifteen seconds."""
    key = str(key or "")
    had = S.PEER_READINGS.get(key)
    if had and time.time() - had[0] < 600:
        return had[1]
    import urllib.parse as _uparse
    try:
        said = json.loads(S.peer_fetch("/follow/reading?key=" + _uparse.quote(key), 10) or b"{}")
    except ValueError:
        said = {}
    said = said if isinstance(said, dict) and said.get("reading") else {}
    S.PEER_READINGS[key] = (time.time(), said)
    return said


def held_here(asked, file_for, title_of):
    """Of the keys asked about, those this server holds a file for. A film offered from a
    pack is asked about by its pack key, which no file here is filed under: read as its
    title's own key, or a film the cache downloaded whole kept its Download ribbon on the
    main server (2026-11-12). Answered with the key that was asked."""
    out = []
    for k in asked:
        try:
            own = title_of(k) or k
        except Exception:
            own = k
        if file_for(own, 0) or (own != k and file_for(k, 0)):
            out.append(k)
    return out


def peer_holds(keys):
    """(the keys of these the other server holds a file for, its name, whether it answered).
    One ask for those not asked in the last two minutes; a server that does not answer
    holds nothing, and its name is the last one known."""
    import urllib.parse as _uparse
    now = time.time()
    keys = [str(k) for k in keys if k]
    ask = [k for k in keys if now - S.PEER_HELD.get(k, (0, False))[0] > 120]
    up = True
    if ask:
        said = None
        try:
            said = json.loads(S.peer_fetch("/follow/held?keys=" + _uparse.quote(",".join(ask[:200])), 4) or b"null")
        except ValueError:
            said = None
        up = isinstance(said, dict)
        held = set((said or {}).get("held") or [])
        for k in ask[:200]:
            S.PEER_HELD[k] = (now, k in held)
    name = S.peer_recent()[1] or ("the other server")
    return {k for k in keys if S.PEER_HELD.get(k, (0, False))[1]}, name, up


def place_said():
    """A viewer here paused or stopped: the positions go across now, ten seconds apart at
    most. A copy sends and fetches itself; the main server asks the copy to fetch."""
    if time.time() - S.PLACE_SAID_AT["at"] < 10:
        return
    S.PLACE_SAID_AT["at"] = time.time()

    def go():
        import pd_follow
        if not pd_follow.places_now(S.local()):
            S.peer_fetch("/follow/placesnow", 5)
    threading.Thread(target=go, daemon=True).start()


def stills_folder():
    return os.path.join(S.local().lib.root, "cache", "frames")


def stills_rows():
    """Every file whose timeline stills are finished here, by name and length:
    [{id, name, size}]."""
    folder = S.stills_folder()
    con = S.local().lib.db()
    try:
        files = con.execute("SELECT fid, path, size FROM file WHERE path IS NOT NULL "
                            "AND fid IS NOT NULL AND fid <> ''").fetchall()
    finally:
        con.close()
    return [{"id": str(r["fid"]), "name": os.path.basename(r["path"]), "size": int(r["size"] or 0)}
            for r in files if r["size"] and os.path.exists(os.path.join(folder, str(r["fid"]), ".done"))]


def stills_to_take(theirs, files, done_here):
    """Which of the other server's finished stills belong to files here that have none:
    [(their id, my id)], matched by name and length like the loudness readings."""
    mine = {}
    for ident, path, size in files:
        if size:
            mine[(os.path.basename(str(path or "")).lower(), int(size))] = str(ident)
    out = []
    for r in theirs or []:
        try:
            ident = mine.get((str(r["name"]).lower(), int(r["size"])))
        except (KeyError, TypeError, ValueError):
            continue
        if ident and ident not in done_here:
            out.append((str(r["id"]), ident))
    return out


def take_peer_stills(most=20):
    """The other server's finished timeline stills for files held here without any, a few at
    a time and only while nobody needs the disks: made once in the house, not on each
    machine. Returns how many films' stills were taken."""
    if time.time() - S.PEER_STILLS["at"] < 1800:
        return 0
    S.PEER_STILLS["at"] = time.time()
    if S.worker_waits(idle_priority=True):
        return 0
    import io as _io
    import zipfile
    try:
        theirs = json.loads(S.peer_fetch("/follow/stills", 30) or b"{}").get("stills") or []
    except ValueError:
        return 0
    folder = S.stills_folder()
    con = S.local().lib.db()
    try:
        files = [(r["fid"], r["path"], r["size"]) for r in con.execute(
            "SELECT fid, path, size FROM file WHERE path IS NOT NULL AND fid IS NOT NULL AND fid <> ''")]
    finally:
        con.close()
    done_here = {str(i) for i, _, _ in files if os.path.exists(os.path.join(folder, str(i), ".done"))}
    taken = 0
    for theirs_id, mine_id in S.stills_to_take(theirs, files, done_here)[:most]:
        if S.worker_waits(idle_priority=True):
            break
        blob = S.peer_fetch("/follow/stillpack?id=" + theirs_id, 120)
        if not blob:
            continue
        place = os.path.join(folder, mine_id)
        try:
            with zipfile.ZipFile(_io.BytesIO(blob)) as z:
                names = [n for n in z.namelist() if re.fullmatch(r"[\w.-]+\.jpg", n)]
                if not names:
                    continue
                os.makedirs(place, exist_ok=True)
                for n in names:
                    with open(os.path.join(place, n), "wb") as f:
                        f.write(z.read(n))
            # finished last, so a half-written folder is made again here rather than kept
            with open(os.path.join(place, ".done"), "w", encoding="utf-8") as f:
                f.write("from the other server")
            taken += 1
        except (OSError, zipfile.BadZipFile):
            continue
    if taken:
        pd_log.raw("%s stills: taken from the other server for %d films%s"
                   % (time.strftime("%H:%M:%S"), taken, chr(10)))
    return taken
