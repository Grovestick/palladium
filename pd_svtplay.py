"""SVT Play: the films and documentaries on it, found by name and year, and the stream
of one when it is played.

SVT's own title list - the one its search is built from - is read twice a day and kept
on disk. A film's stream is asked of SVT's video service when it is played: an HLS
address, which plays from Sweden only. Copy-protected titles are left out; they cannot
be played here.
"""
import json
import os
import re
import threading
import time
import urllib.request

GRAPH = "https://api.svt.se/contento/graphql?ua=svtplaywebb-play-render-production-client"
VIDEO = "https://api.svt.se/video/%s"
#: how often the title list is read again
EVERY = 12 * 3600
#: a film or a documentary, not a clip or an episode cut short
FILM_SECONDS = 40 * 60
QUERY = ("{ allTitlesForSearchIndexer { __typename name urls { svtplay } "
         "restrictions { drmCopyProtection } "
         "... on Single { videoSvtId productionYear duration originalProgramTitle "
         "portraitPosterImage { id changed } image { id changed } } "
         "... on TvSeries { svtId portraitPosterImage { id changed } image { id changed } "
         "seasons { name episodes { name videoSvtId positionInSeason "
         "duration productionYear restrictions { drmCopyProtection } } } } } }")
#: an SVT picture by its id and when it changed, 400 wide
PICTURE = "https://www.svtstatic.se/image/medium/400/%s/%s"
#: the stream formats, the one most players take first
FORMATS = ("hls-cmaf-full", "hls-ts-full", "hls-ts-avc", "hls")

STATE = {"root": None, "films": None, "at": 0.0, "reading": False, "index": {},
         "series": [], "shows": {}, "episodes": {}}
#: video id -> (when, stream): asked once in ten minutes
STREAMS = {}
LOCK = threading.Lock()


def use_data_dir(root):
    STATE["root"] = root


def _path():
    return os.path.join(STATE["root"] or ".", "svt_play.json")


def flat(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _poster(t):
    """SVT's portrait poster of a title as "id-changed", or ""."""
    for p in (t.get("portraitPosterImage") or {}, t.get("image") or {}):
        # the portrait poster, else SVT's wide picture of it
        if p.get("id") and p.get("changed"):
            return "%s-%s" % (p["id"], p["changed"])
    return ""


def films_from(titles):
    """The films and documentaries of SVT's title list that can be played here."""
    out = []
    for t in titles or []:
        if not isinstance(t, dict) or t.get("__typename") != "Single" or not t.get("videoSvtId"):
            continue
        if (t.get("restrictions") or {}).get("drmCopyProtection"):
            continue
        if int(t.get("duration") or 0) < FILM_SECONDS:
            continue
        year = str(t.get("productionYear") or "")[:4]
        out.append({"id": str(t["videoSvtId"]), "name": t.get("name") or "",
                    "original": t.get("originalProgramTitle") or "",
                    "year": int(year) if year.isdigit() else 0,
                    "url": (t.get("urls") or {}).get("svtplay") or "",
                    "seconds": int(t.get("duration") or 0), "poster": _poster(t)})
    return out


def _number(text):
    """The last number in "Säsong 10" or "Säsong 10 — Avsnitt 3"; 0 for none."""
    found = re.findall(r"\d+", str(text or ""))
    return int(found[-1]) if found else 0


def series_from(titles):
    """The series of SVT's title list that can be played here, each with its seasons
    and the episodes in them that are not copy-protected."""
    out = []
    for t in titles or []:
        if not isinstance(t, dict) or t.get("__typename") != "TvSeries" or not t.get("svtId"):
            continue
        if (t.get("restrictions") or {}).get("drmCopyProtection"):
            continue
        seasons, years = [], []
        for n, se in enumerate(t.get("seasons") or []):
            eps = []
            for e in (se or {}).get("episodes") or []:
                if not isinstance(e, dict) or not e.get("videoSvtId"):
                    continue
                if (e.get("restrictions") or {}).get("drmCopyProtection"):
                    continue
                if e.get("productionYear"):
                    years.append(int(e["productionYear"]))
                eps.append({"id": str(e["videoSvtId"]), "name": e.get("name") or "",
                            "number": _number(e.get("positionInSeason")),
                            "seconds": int(e.get("duration") or 0)})
            if eps:
                # in the order they run: SVT lists some series newest season first
                eps.sort(key=lambda e: (e["number"] == 0, e["number"]))
                seasons.append({"name": se.get("name") or "Season %d" % (n + 1),
                                "number": _number(se.get("name")), "episodes": eps})
        # season 1 first, anything unnumbered - extras, a special - after the rest
        seasons.sort(key=lambda se: (se["number"] == 0, se["number"]))
        if seasons:
            out.append({"id": str(t["svtId"]), "name": t.get("name") or "",
                        "year": min(years) if years else 0,
                        "url": (t.get("urls") or {}).get("svtplay") or "",
                        "poster": _poster(t), "seasons": seasons})
    return out


def episodes_of(series_list):
    """Every episode by its video id: (series, season, episode)."""
    out = {}
    for s in series_list or []:
        for se in s.get("seasons") or []:
            for e in se.get("episodes") or []:
                out[e["id"]] = (s, se, e)
    return out


def card(video_id):
    """A film or an episode on SVT Play shaped the way the library shapes one, keyed
    "sv" + its video id - what Continue watching shows of something played from SVT.
    None for an id SVT's list does not have."""
    video_id = str(video_id or "")
    fresh()
    f = next((x for x in STATE["films"] or [] if x["id"] == video_id), None)
    if f:
        return {"ratingKey": "sv" + video_id, "type": "movie", "title": f["name"],
                "year": f.get("year") or None, "duration": int(f.get("seconds") or 0) * 1000,
                "summary": "", "thumb": ("/art/svt/" + f["poster"]) if f.get("poster") else None,
                "svt": video_id, "askable": True}
    found = STATE["episodes"].get(video_id)
    if not found:
        return None
    s, se, e = found
    thumb = ("/art/svt/" + s["poster"]) if s.get("poster") else None
    return {"ratingKey": "sv" + video_id, "type": "episode", "title": e["name"],
            "grandparentTitle": s["name"], "parentIndex": se.get("number") or None,
            "index": e.get("number") or None, "duration": int(e.get("seconds") or 0) * 1000,
            "summary": "", "thumb": thumb, "grandparentThumb": thumb,
            "svt": video_id, "askable": True}


def picture_url(code):
    """Where SVT keeps a poster named "id-changed", or ""."""
    m = re.match(r"^(\d+)-(\d+)$", str(code or ""))
    return PICTURE % (m.group(1), m.group(2)) if m else ""


def index_of(films):
    """Each film under its name and its original name, flattened."""
    idx = {}
    for f in films:
        for n in (f.get("name"), f.get("original")):
            if flat(n):
                idx.setdefault(flat(n), []).append(f)
    return idx


def _load():
    if STATE["films"] is not None:
        return
    try:
        with open(_path(), encoding="utf-8") as fh:
            kept = json.load(fh)
        films, at = kept.get("films") or [], float(kept.get("at") or 0)
        series = kept.get("series")
        # a list kept before series, or posters, were read: read again at once
        if series is None or (films and "poster" not in films[0]):
            series, at = series or [], 0.0
    except (OSError, ValueError):
        films, at, series = [], 0.0, []
    with LOCK:
        STATE["films"], STATE["at"], STATE["index"] = films, at, index_of(films)
        STATE["series"], STATE["shows"] = series, index_of(series)
        STATE["episodes"] = episodes_of(series)


def read_now():
    """Read SVT's title list and keep its films; how many, or None when nothing came -
    then what was kept stands."""
    req = urllib.request.Request(GRAPH, data=json.dumps({"query": QUERY}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        said = json.loads(r.read())
    titles = ((said.get("data") or {}).get("allTitlesForSearchIndexer")) or []
    films, series = films_from(titles), series_from(titles)
    if not films:
        return None
    with LOCK:
        STATE["films"], STATE["at"], STATE["index"] = films, time.time(), index_of(films)
        STATE["series"], STATE["shows"] = series, index_of(series)
        STATE["episodes"] = episodes_of(series)
    try:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"at": STATE["at"], "films": films, "series": series}, fh)
        os.replace(tmp, _path())
    except OSError:
        pass
    return len(films)


def fresh():
    """The list as kept, with a read behind it when it is old. Never waits."""
    _load()
    if time.time() - STATE["at"] > EVERY and not STATE["reading"]:
        STATE["reading"] = True

        def run():
            try:
                read_now()
            except Exception:
                pass
            finally:
                STATE["reading"] = False
        threading.Thread(target=run, daemon=True).start()


def film_for(title, year=0, original=""):
    """The SVT Play video id of a film, or "": by its name or original name, a year
    either side - SVT dates a film by its production, the catalogue by its release."""
    fresh()
    year = int(year or 0)
    for n in (title, original):
        for f in STATE["index"].get(flat(n), []):
            if not year or not f["year"] or abs(f["year"] - year) <= 1:
                return f["id"]
    return ""


def series_for(name, year=0):
    """The SVT Play id of a series, or "": by its name, and when both are known its first
    year within three of the catalogue's - two series share a name more often than films."""
    fresh()
    year = int(year or 0)
    for s in STATE["shows"].get(flat(name), []):
        if not year or not s["year"] or abs(s["year"] - year) <= 3:
            return s["id"]
    return ""


def series(series_id):
    """One series as SVT Play has it: its seasons and their episodes, or {}. In running
    order whatever order it was kept in - a list read before they were sorted included."""
    fresh()
    got = next((s for s in STATE["series"] if s["id"] == str(series_id)), {})
    if not got:
        return {}
    seasons = [dict(se, episodes=sorted(se.get("episodes") or [],
                                        key=lambda e: (e["number"] == 0, e["number"])))
               for se in got.get("seasons") or []]
    seasons.sort(key=lambda se: (se["number"] == 0, se["number"]))
    return dict(got, seasons=seasons)


def stream_from(said):
    """What playing one takes, from SVT's answer about it: the HLS address, its
    subtitles, how long. {} for one that is copy-protected or has no HLS."""
    if not isinstance(said, dict) or (said.get("rights") or {}).get("drmCopyProtection"):
        return {}
    refs = {v.get("format"): v.get("url") for v in said.get("videoReferences") or []
            if isinstance(v, dict) and v.get("url")}
    hls = next((refs[f] for f in FORMATS if refs.get(f)), "")
    if not hls:
        return {}
    subs = [{"url": s["url"], "language": "sv"} for s in said.get("subtitleReferences") or []
            if isinstance(s, dict) and s.get("format") == "webvtt" and s.get("url")]
    return {"hls": hls, "subs": subs, "seconds": int(said.get("contentDuration") or 0),
            "title": said.get("episodeTitle") or said.get("programTitle") or ""}


def stream(video_id):
    """One film's stream, asked of SVT when it is played."""
    video_id = str(video_id or "")
    if not re.match(r"^[A-Za-z0-9]{4,20}$", video_id):
        return {}
    hit = STREAMS.get(video_id)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    req = urllib.request.Request(VIDEO % video_id, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        got = stream_from(json.loads(r.read()))
    if len(STREAMS) > 200:
        STREAMS.clear()
    STREAMS[video_id] = (time.time(), got)
    return got
