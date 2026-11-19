"""Sport on the tracker: the races and games posted after they are run, as a row.

Read from the tracker's own index, so nothing is asked of anybody to fill it. A
release is recognised by how its name is written; one that cannot be read is left
out rather than guessed at. The row holds the newest events; pressing one lists its
versions, and the house's own choice of resolution is the one put forward.

A downloaded one is filed as an episode of its sport - "Formula 1", season the year,
episode the round - rather than as a film, which is what its name would make of it.
"""
import hashlib
import re
import time

#: how far back the row reaches: a year of each sport, by when the event was run
DAYS = 365
#: how many events it holds
MOST = 30
#: and how many of them one sport may take
PER_SPORT = {"nfl": 16}
PER_SPORT_ELSE = 8

#: the NFL's teams by the last word of their names, for an episode number that is
#: the same every time a game's file is read: a team plays once a day
NFL_TEAMS = sorted("""49ers Bears Bengals Bills Broncos Browns Buccaneers Cardinals Chargers
Chiefs Colts Commanders Cowboys Dolphins Eagles Falcons Giants Jaguars Jets Lions Packers
Panthers Patriots Raiders Rams Ravens Saints Seahawks Steelers Texans Titans Vikings""".split())

_F1 = re.compile(r"(?i)^Formula[ ._]?1[ ._](\d{4})[ ._](?:Round[ ._]?(\d{1,2})[ ._])?(.+?)[ ._]"
                 r"(Grand[ ._]Prix|Race|Sprint[ ._]Qualifying|Sprint|Qualifying|"
                 r"(?:Free[ ._])?Practice[ ._]?\d|FP\d|Pre[ ._-]?Race[ ._]Show|"
                 r"Post[ ._-]?Race[ ._]Show|Ted.?s[ ._]Notebook)(?=[ ._]|$)")
_NFL = re.compile(r"(?i)^NFL[ ._](\d{4})[ ._](\d{2})[ ._](\d{2})[ ._](.+?)[ ._]Vs[ ._](.+?)"
                  r"[ ._](?:\d{3,4}p|WEB|HDTV)")
#: motocross, year first: "2026 AMA Supercross Rd 9 Indianapolis 1080p", "2026 MXGP Rd 19
#: Australia Day 2 1080p", "2026 Motocross of Nations Ernee Day 1 1080p" (owner, 2026-11-18)
_MX = re.compile(r"(?i)^(\d{4})[ ._](AMA[ ._]Supercross|AMA[ ._]Motocross|Australian[ ._]Supercross|"
                 r"MXGP|Motocross[ ._]of[ ._]Nations)[ ._](?:Rd[ ._]?(\d{1,2})[ ._])?(.+?)"
                 r"[ ._](?:\d{3,4}p\d*|WEB|HDTV)(?:[ ._]|$)")
#: the rest: named for the event, then a year or a stage
_OTHER = [
    ("cycling", re.compile(r"(?i)^(Tour[ ._]de[ ._]France|Giro[ ._]d.?Italia|La[ ._]Vuelta|"
                           r"Vuelta[ ._]a[ ._]Espana|Paris[ ._-]Roubaix|Tour[ ._]of[ ._]Flanders|"
                           r"Milan[ ._-]San[ ._]Remo|Il[ ._]Lombardia)\b")),
    ("dakar", re.compile(r"(?i)^(?:Rally[ ._])?(Dakar(?:[ ._]Rally)?)\b")),
    ("golf", re.compile(r"(?i)^(PGA[ ._]Tour|PGA[ ._]Championship|LIV[ ._]Golf|The[ ._]Masters|"
                        r"Masters[ ._]Tournament|Ryder[ ._]Cup|The[ ._]Open[ ._]Championship|"
                        r"DP[ ._]World[ ._]Tour|US[ ._]Open[ ._]Golf)\b")),
]
SPORT_NAMES = {"f1": "Formula 1", "nfl": "NFL", "cycling": "Cycling", "dakar": "Dakar",
               "golf": "Golf", "motocross": "Motocross"}


def search_words(now=None):
    """What the tracker is asked about, a phrase at a time in turn, so a year of these
    sports is in the index and not only what the feeds carried this month."""
    year = int(time.strftime("%Y", time.localtime(now or time.time())))
    out = []
    for y in (year, year - 1):
        # the races by name: asked for the year alone, the forty answers were mostly
        # practice and qualifying, and ten races of two seasons were found
        out += ["Formula 1 %d Race" % y, "Formula1 %d Race" % y, "NFL %d" % y,
                "Tour de France %d" % y, "PGA Tour %d" % y, "Dakar %d" % y,
                "%d AMA Supercross" % y, "%d AMA Motocross" % y, "%d MXGP" % y]
    return tuple(out)


def run_on(e, now=None):
    """When an event was run, as a time: its day where its name gives one, its year's
    end where only a year is known, and when it was posted otherwise."""
    if e.get("when"):
        try:
            return time.mktime(time.strptime(str(e["when"]), "%Y-%m-%d"))
        except ValueError:
            pass
    if e.get("year"):
        # a year alone: counted to its last day, so a race of last season stands a
        # year from when the season ended rather than from New Year
        return min(time.time() if now is None else now,
                   time.mktime((int(e["year"]), 12, 31, 0, 0, 0, 0, 0, -1)))
    return float(e.get("at") or 0)


def _words(text):
    return re.sub(r"[ ._]+", " ", str(text or "")).strip(" -")


def _key(*parts):
    return "sp" + hashlib.sha1("|".join(str(p).lower() for p in parts).encode()).hexdigest()[:12]


def read_name(name):
    """What a release is, when it is sport: a dict with the sport, the event it belongs
    to and its title; None for anything else, and for an F1 session that is not the
    race - the row is the races."""
    name = str(name or "")
    m = _F1.match(name)
    if m:
        year, rnd, place, session = int(m.group(1)), int(m.group(2) or 0), _words(m.group(3)), m.group(4)
        flat = re.sub(r"[ ._]", " ", session).lower()
        if not (flat == "race" or flat.startswith("grand prix")):
            return None
        # "Bahrain Grand Prix Practice Two", "Spanish Grand Prix Weekend Warm-Up": the
        # words after the Grand Prix say which session it is, and it is not the race
        if re.match(r"(?i)[ ._-]+(Practice|Qualifying|Sprint|Weekend|Warm|Pre|Post|Ted|"
                    r"Highlights|Press|Notebook|Paddock|Drivers|Team|Show|Build)",
                    name[m.end():]):
            return None
        place = re.sub(r"(?i)\s+grand\s+prix$", "", place)
        return {"sport": "f1", "year": year, "round": rnd, "place": place,
                "event": _key("f1", year, re.sub(r"[^a-z0-9]", "", place.lower())),
                "title": "%s Grand Prix" % place, "when": ""}
    m = _MX.match(name)
    if m:
        year, series, rnd = int(m.group(1)), _words(m.group(2)), int(m.group(3) or 0)
        # the live show of a round is the same round: one event, its releases together
        place = re.sub(r"(?i)\s*(?:Race\s+)?Day\s+Live$", "", _words(m.group(4))).strip()
        return {"sport": "motocross", "year": year, "round": rnd, "place": place,
                "league": league_of(series),
                "event": _key("mx", year, series.lower(), rnd, re.sub(r"[^a-z0-9]", "", place.lower())),
                "title": "%s%s %s" % (series, " Rd %d" % rnd if rnd else "", place), "when": ""}
    m = _NFL.match(name)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        away, home = _words(m.group(4)), _words(m.group(5))
        return {"sport": "nfl", "year": year, "away": away, "home": home,
                "when": "%04d-%02d-%02d" % (year, month, day),
                "event": _key("nfl", year, month, day, home.lower()),
                "title": "%s vs %s" % (away, home)}
    for sport, pat in _OTHER:
        m = pat.match(name)
        if not m:
            continue
        # the event's words up to its resolution or source: "Tour de France 2026 Stage 12"
        head = re.split(r"(?i)[ ._](?:\d{3,4}p|WEB|HDTV|WEB-DL|WEBRip|BluRay)(?:[ ._]|$)", name)[0]
        title = _words(head)
        year = re.search(r"\b(19|20)\d\d\b", title)
        return {"sport": sport, "year": int(year.group(0)) if year else 0,
                "league": league_of(m.group(1)),
                "event": _key(sport, re.sub(r"[^a-z0-9]", "", title.lower())),
                "title": title, "when": ""}
    return None


def quality(name):
    low = str(name or "").lower()
    return "4K" if re.search(r"2160p|\buhd\b", low) else "1080p" if "1080p" in low \
        else "720p" if "720p" in low else ""


def newest_first(e):
    """The order a sport's events stand in: the newest event first - a race by its year
    and round, a game by its day - not the newest posting, so a race posted again a
    week later stays where it was run."""
    if e["sport"] in ("f1", "motocross") and e.get("round"):
        return (-int(e.get("year") or 0), -int(e["round"]), -float(e.get("at") or 0))
    if e.get("when"):
        y, m, d = (int(x) for x in str(e["when"]).split("-"))
        return (-y, -(m * 100 + d), -float(e.get("at") or 0))
    return (-int(e.get("year") or 0), 0, -float(e.get("at") or 0))


def picture_address(path, now=None):
    """A card's picture address with the hour in it: the apps keep a picture by its address
    for a day, so a card's next photograph, or a poster found after the drawn one was shown,
    came to a phone only the day after (owner, 2026-11-19). The server reads no more than
    the path."""
    return "%s?h=%d" % (path, int((time.time() if now is None else now) // PHOTO_EVERY))


def sport_cards(rows, now=None):
    """The Sports row: one card a sport that has anything, the sport with the newest
    posting first. Pressed, the sport's own events."""
    found = {}
    for e in events(rows, now=now, most=10 ** 6, capped=False):
        found.setdefault(e["sport"], []).append(e)
    out = []
    for sport, these in found.items():
        word = {"f1": "race", "nfl": "game"}.get(sport, "event")
        out.append({"ratingKey": "spx" + sport, "type": "movie", "sportRow": True,
                    "sport": sport, "title": SPORT_NAMES.get(sport, sport),
                    "summary": "%d %s%s \u00b7 newest: %s" % (len(these), word,
                                                             "" if len(these) == 1 else "s",
                                                             these[0]["title"]),
                    "thumb": picture_address("/art/sports/%s" % sport, now), "duration": 0, "viewCount": 0,
                    "addedAt": int(max(float(e.get("at") or 0) for e in these))})
    return sorted(out, key=lambda c: -c["addedAt"])


def sport_events(rows, sport, now=None, most=400):
    """One sport's events, newest event first, each with its place in that order - the
    year of them, not the few a mixed row would carry."""
    these = [e for e in events(rows, now=now, most=10 ** 6, capped=False)
             if e["sport"] == sport]
    these = sorted(these, key=newest_first)[:most]
    return these


def events(rows, now=None, days=DAYS, most=MOST, capped=True):
    """The newest events the index holds releases of, newest first, each with its
    releases: [{key, sport, title, year, at, releases}]. Within `days` of when the event
    was run - its day or its year - not of when the index happened to see it: a search
    stamps everything it finds with the day it was asked."""
    now = time.time() if now is None else now
    found = {}
    for row in rows or []:
        said = read_name(row.get("name"))
        if not said:
            continue
        at = float(row.get("at") or 0)
        if days and now - run_on(dict(said, at=at), now) > days * 86400:
            continue
        one = found.setdefault(said["event"], dict(said, key=said["event"], at=0.0,
                                                   releases=[]))
        one["releases"].append(row)
        one["at"] = max(one["at"], at)
        # a round number from one release names the event for all of them
        if said.get("round") and not one.get("round"):
            one["round"] = said["round"]
    # newest first, and no sport filling the row: a Sunday of football is sixteen games
    taken, out = {}, []
    for e in sorted(found.values(), key=newest_first):
        if capped and taken.get(e["sport"], 0) >= PER_SPORT.get(e["sport"], PER_SPORT_ELSE):
            continue
        taken[e["sport"]] = taken.get(e["sport"], 0) + 1
        out.append(e)
    out = out[:most]
    for e in out:
        e["releases"].sort(key=lambda r: (int(r.get("seeds") or 0), float(r.get("at") or 0)),
                           reverse=True)
    return out


def card(e):
    """One event as a shelf card: a title not here, on the tracker, to fetch."""
    sport = str(SPORT_NAMES.get(e["sport"]) or e["sport"])
    if e["sport"] == "f1":
        under = "Formula 1 %d" % e["year"] + (" · Round %d" % e["round"] if e.get("round") else "")
    elif e["sport"] == "nfl":
        under = "NFL · %s" % e["when"]
    else:
        under = sport
    under = str(under)
    sizes = sorted({quality(r.get("name")) for r in e["releases"]} - {""})
    return {"ratingKey": e["key"], "type": "movie", "sport": e["sport"],
            "title": e["title"], "year": e.get("year") or None,
            "summary": under + ((" · " + ", ".join(sizes)) if sizes else ""),
            "originallyAvailableAt": e.get("when") or "",
            "genres": [sport], "thumb": picture_address("/art/%s/poster" % e["key"]), "duration": 0, "viewCount": 0,
            "addedAt": int(e.get("at") or 0),
            # on the tracker, nowhere here: its page lists the versions to fetch
            "askable": True, "onTracker": True}


def _all_events(rows):
    """Every event in the index, uncapped and of any age."""
    found = {}
    for row in rows or []:
        said = read_name(row.get("name"))
        if not said:
            continue
        one = found.setdefault(said["event"], dict(said, key=said["event"], releases=[]))
        one["releases"].append(row)
        if said.get("round") and not one.get("round"):
            one["round"] = said["round"]
    return list(found.values())


def releases_of(key, rows):
    """The index's releases of one event, most carried first."""
    for e in _all_events(rows):
        if e["key"] == key:
            return e
    return None


def episode_of(name, rows):
    """(series, season, episode, title) a downloaded release is filed under: the sport,
    the year, and a number that is the same every time - the round, or for a game the
    day and the home team. None for a release that is not sport, and for an F1 race
    whose round nothing in the index names."""
    said = read_name(name)
    if not said:
        return None
    if said["sport"] == "f1":
        rnd = said.get("round") or 0
        if not rnd:
            e = releases_of(said["event"], rows)
            rnd = (e or {}).get("round") or 0
        if not rnd:
            return None
        return ("Formula 1", said["year"], rnd, said["title"])
    if said["sport"] == "nfl":
        month_day = int(said["when"][5:7]) * 100 + int(said["when"][8:10])
        nick = said["home"].split()[-1].lower() if said["home"] else ""
        team = next((i for i, t in enumerate(NFL_TEAMS) if t.lower() == nick), 99)
        return ("NFL", said["year"], month_day * 100 + team,
                "%s · %s" % (said["title"], said["when"]))
    return None


#: the series a downloaded race or game is filed under
FILED_UNDER = ("Formula 1", "NFL")

#: days a race or game read in stands on Recently added, wearing New
ARRIVED_DAYS = 3


def told(path):
    """The library's hook: (series, season, episode, title) for a downloaded race or
    game, by its file's name or its folder's; None for anything else."""
    import os
    import pd_tracker
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    folder = os.path.basename(os.path.dirname(str(path or "")))
    if not read_name(stem) and not read_name(folder):
        return None
    rows = list(pd_tracker.read().values())
    # the folder first when the file is named in lower case: the game was filed as
    # "cincinnati bengals vs baltimore ravens" off its file (2026-11-05)
    for name in ((folder, stem) if stem == stem.lower() else (stem, folder)):
        got = episode_of(name, rows)
        if got:
            return got
    return None


# ---- posters, drawn here: none of these has one anywhere to fetch

#: each NFL team's colour, by the last word of its name
NFL_COLOURS = {
    "49ers": "#AA0000", "Bears": "#0B162A", "Bengals": "#FB4F14", "Bills": "#00338D",
    "Broncos": "#FB4F14", "Browns": "#311D00", "Buccaneers": "#D50A0A", "Cardinals": "#97233F",
    "Chargers": "#0080C6", "Chiefs": "#E31837", "Colts": "#002C5F", "Commanders": "#5A1414",
    "Cowboys": "#041E42", "Dolphins": "#008E97", "Eagles": "#004C54", "Falcons": "#A71930",
    "Giants": "#0B2265", "Jaguars": "#006778", "Jets": "#125740", "Lions": "#0076B6",
    "Packers": "#203731", "Panthers": "#0085CA", "Patriots": "#002244", "Raiders": "#000000",
    "Rams": "#003594", "Ravens": "#241773", "Saints": "#9F8958", "Seahawks": "#002244",
    "Steelers": "#101820", "Texans": "#03202F", "Titans": "#0C2340", "Vikings": "#4F2683"}
#: the rest, a colour for the sport and one for the words on it
SPORT_COLOURS = {"golf": ("#1B5E20", "#FFFFFF"), "cycling": ("#F5C400", "#111111"),
                 "dakar": ("#C8913A", "#111111")}


def league_of(name):
    """A competition's own key: "Tour.de.France" and "tour de france" alike, "La Vuelta" and
    "Vuelta a Espana" one."""
    flat = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()
    return {"la vuelta": "vuelta", "vuelta a espana": "vuelta", "dakar rally": "dakar",
            "masters tournament": "the masters", "us open golf": "us open"}.get(flat, flat)


#: each league's own poster: ground, accent, its mark, its name - one look for every event of
#: it, the event's round, stage or place under it (owner, 2026-11-18)
LEAGUES = {
    "ama supercross": ("#0B1F3A", "#FFC72C", "SX", "AMA SUPERCROSS"),
    "ama motocross": ("#141414", "#E31B23", "MX", "PRO MOTOCROSS"),
    "mxgp": ("#0A0A0A", "#FF6A13", "MXGP", "WORLD CHAMPIONSHIP"),
    "motocross of nations": ("#1E3A8A", "#FFFFFF", "MXoN", "MOTOCROSS OF NATIONS"),
    "australian supercross": ("#00843D", "#FFCD00", "AUSX", "AUSTRALIAN SUPERCROSS"),
    "tour de france": ("#F5C400", "#111111", "TDF", "TOUR DE FRANCE"),
    "giro d italia": ("#E5007E", "#FFFFFF", "GIRO", "GIRO D'ITALIA"),
    "vuelta": ("#D7192A", "#FFFFFF", "VUELTA", "LA VUELTA"),
    "paris roubaix": ("#3A3A3A", "#FFFFFF", "ROUBAIX", "PARIS-ROUBAIX"),
    "tour of flanders": ("#FFD500", "#111111", "RONDE", "TOUR OF FLANDERS"),
    "milan san remo": ("#1F4E9E", "#FFFFFF", "SANREMO", "MILAN-SAN REMO"),
    "il lombardia": ("#6D2077", "#FFFFFF", "LOMBARDIA", "IL LOMBARDIA"),
    "pga tour": ("#0B2A4A", "#FFFFFF", "PGA", "PGA TOUR"),
    "pga championship": ("#1B3D6E", "#C8A15A", "PGA", "PGA CHAMPIONSHIP"),
    "liv golf": ("#000000", "#FFFFFF", "LIV", "LIV GOLF"),
    "the masters": ("#006747", "#FCE300", "MASTERS", "THE MASTERS"),
    "ryder cup": ("#002D72", "#C9A227", "RYDER", "RYDER CUP"),
    "the open championship": ("#001A57", "#FFD100", "OPEN", "THE OPEN"),
    "dp world tour": ("#00263E", "#FFFFFF", "DPWT", "DP WORLD TOUR"),
    "us open": ("#0A2240", "#FFFFFF", "US OPEN", "US OPEN"),
    "dakar": ("#C8913A", "#111111", "DAKAR", "RALLY"),
}


def league_lines(e):
    """What tells one event of a league from the next: (big, small) - the round and the
    place, or what the title says beyond the league and the year."""
    if e.get("round"):
        return "ROUND %d" % e["round"], str(e.get("place") or "").upper()
    rest = str(e.get("title") or "")
    for word in re.split(r"[ .\-]+", str(e.get("league") or "")):
        if word:
            rest = re.sub(r"(?i)\b%s\b" % re.escape(word), " ", rest)
    rest = re.sub(r"\b(19|20)\d\d\b", " ", rest)
    rest = " ".join(rest.replace("'", " ").split()).strip(" -").upper()
    return rest, ""


def _font(size, wide=False):
    from PIL import ImageFont
    for name in (("bahnschrift.ttf", "arialbd.ttf") if wide else ("arialbd.ttf", "segoeuib.ttf")):
        for where in ("C:/Windows/Fonts/" + name, name):
            try:
                return ImageFont.truetype(where, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _fit(draw, text, width, start, wide=False, least=14):
    """The largest of the font that puts `text` inside `width`."""
    size = start
    while size > least:
        font = _font(size, wide)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 2
    return _font(least, wide)


def _centred(draw, y, text, font, fill, width):
    w = draw.textlength(text, font=font)
    draw.text(((width - w) / 2, y), text, font=font, fill=fill)


def poster(e, width=400):
    """A poster for one event, 2:3, as JPEG bytes."""
    import io
    from PIL import Image, ImageDraw
    W, H = 400, 600
    im = Image.new("RGB", (W, H), "#15151E")
    d = ImageDraw.Draw(im)
    if e["sport"] == "f1":
        d.polygon([(0, 0), (W, 0), (W, 140), (0, 260)], fill="#E10600")
        d.text((28, 26), "F1", font=_font(110, wide=True), fill="#FFFFFF")
        place = str(e.get("place") or e["title"].replace(" Grand Prix", "")).upper()
        _centred(d, 330, place, _fit(d, place, W - 40, 64, wide=True), "#FFFFFF", W)
        _centred(d, 405, "GRAND PRIX", _font(34, wide=True), "#E10600", W)
        under = ("ROUND %d \u00b7 %d" % (e["round"], e["year"])) if e.get("round") else str(e.get("year") or "")
        _centred(d, 530, under, _font(26), "#B9B9C3", W)
    elif e["sport"] == "nfl":
        away, home = str(e.get("away") or ""), str(e.get("home") or "")
        for top, team in ((0, away), (H // 2, home)):
            nick = team.split()[-1] if team else ""
            d.rectangle([0, top, W, top + H // 2], fill=NFL_COLOURS.get(nick, "#333344"))
            city = " ".join(team.split()[:-1]).upper()
            _centred(d, top + 70, nick.upper(), _fit(d, nick.upper(), W - 40, 78, wide=True),
                     "#FFFFFF", W)
            _centred(d, top + 170, city, _fit(d, city, W - 60, 26), "#FFFFFF", W)
        # a line between the halves: two teams in the same blue are still two
        d.rectangle([0, H // 2 - 3, W, H // 2 + 3], fill="#FFFFFF")
        d.ellipse([W / 2 - 34, H / 2 - 34, W / 2 + 34, H / 2 + 34], fill="#FFFFFF")
        _centred(d, H / 2 - 17, "VS", _font(28, wide=True), "#111111", W)
        when = str(e.get("when") or "")
        _centred(d, H - 48, "NFL \u00b7 " + when, _font(24), "#FFFFFF", W)
    elif e.get("league") in LEAGUES:
        bg, accent, mark, name = LEAGUES[e["league"]]
        d.rectangle([0, 0, W, H], fill=bg)
        # the league's band and mark: the same on every event of it
        d.polygon([(0, 0), (W, 0), (W, 120), (0, 210)], fill=accent)
        ink = bg if accent.upper() not in ("#FFFFFF",) or bg.upper() in ("#FFFFFF",) else "#111111"
        _centred(d, 34, mark, _fit(d, mark, W - 70, 96, wide=True), ink if accent != "#FFFFFF" else bg, W)
        _centred(d, 250, name, _fit(d, name, W - 50, 34, wide=True), accent, W)
        big, small = league_lines(e)
        if big:
            _centred(d, 330, big, _fit(d, big, W - 50, 56, wide=True),
                     "#FFFFFF" if bg.upper() not in ("#F5C400", "#FFD500", "#C8913A") else "#111111", W)
        if small:
            _centred(d, 410, small, _fit(d, small, W - 50, 40, wide=True),
                     "#FFFFFF" if bg.upper() not in ("#F5C400", "#FFD500", "#C8913A") else "#111111", W)
        if e.get("year"):
            _centred(d, 530, str(e["year"]), _font(28), accent if accent != "#111111" else "#111111", W)
    else:
        bg, fg = SPORT_COLOURS.get(e["sport"], ("#2A2A35", "#FFFFFF"))
        d.rectangle([0, 0, W, H], fill=bg)
        name = SPORT_NAMES.get(e["sport"], e["sport"]).upper()
        _centred(d, 60, name, _fit(d, name, W - 40, 60, wide=True), fg, W)
        words, line, lines = str(e["title"]).split(), "", []
        font = _font(40, wide=True)
        for w in words:
            trial = (line + " " + w).strip()
            if d.textlength(trial, font=font) > W - 50 and line:
                lines.append(line)
                line = w
            else:
                line = trial
        if line:
            lines.append(line)
        for n, one in enumerate(lines[:6]):
            _centred(d, 230 + n * 52, one, font, fg, W)
    if width and width < W:
        im = im.resize((width, int(width * 1.5)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


def studio_mark(raw):
    """A studio's logo as a white mark on nothing, the way the film's page draws it.

    The pages draw every logo as a white silhouette, which is right for letters on
    nothing and wrong for the rest: white letters in a black box became a white box,
    and a drawing with a white face a white blob. So the ink is found first - what
    stands out from the commonest shade where the logo has both a dark and a light
    part, the dark lines of a dark drawing, the light ones of a light drawing - and
    only that is kept, white. Where that leaves next to nothing, the silhouette."""
    import io
    from PIL import Image, ImageChops, ImageOps, ImageStat
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    alpha = im.getchannel("A")
    light = im.convert("L")
    solid = alpha.point([0] * 129 + [255] * 127)
    count = ImageStat.Stat(solid).sum[0] / 255.0
    if not count:
        return raw
    counts = light.histogram(mask=solid)
    dark = sum(counts[:96]) / count
    pale = sum(counts[160:]) / count
    boxed = dark >= 0.15 and pale >= 0.15
    if boxed:
        # a box and its letters, or lines and a fill: the ink is what differs from the
        # commonest shade, in steps of eight - half black and half white has a
        # median of grey, which is neither
        steps = [sum(counts[i:i + 8]) for i in range(0, 256, 8)]
        box = steps.index(max(steps)) * 8 + 4
        ink = ImageChops.difference(light, Image.new("L", im.size, box))
    elif ImageStat.Stat(light, mask=solid).mean[0] < 128:
        ink = ImageOps.invert(light)          # dark lines: they are the mark
    else:
        ink = light                           # light lines on nothing
    ink = ImageChops.multiply(ink, alpha)
    top = int(ink.getextrema()[1] or 1) or 1                   # type: ignore[index]
    ink = ink.point([min(255, v * 255 // top) for v in range(256)])
    kept = ImageStat.Stat(ink.point([0] * 129 + [255] * 127)).sum[0] / 255.0
    # a box's letters are a small part of it; a pale logo's lines should be most of it
    if kept < count * (0.02 if boxed else 0.25):
        ink = alpha                           # next to nothing left: the silhouette
    out = Image.new("RGBA", im.size, (255, 255, 255, 0))
    out.putalpha(ink)
    made = io.BytesIO()
    out.save(made, "PNG")
    return made.getvalue()


def logo_poster(logo_bytes, width=400):
    """A series' poster made of its network's logo on a dark ground."""
    import io
    from PIL import Image
    W, H = 400, 600
    im = Image.new("RGB", (W, H), "#101014")
    logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    logo.thumbnail((W - 60, 300))
    # a dark logo is drawn light: black on the dark ground was not there at all
    from PIL import ImageStat
    alpha = logo.split()[3]
    seen = ImageStat.Stat(logo.convert("L"), mask=alpha.point([0] * 129 + [255] * 127))
    if seen.count[0] and seen.mean[0] < 90:
        light = Image.new("RGBA", logo.size, (242, 242, 242, 255))
        light.putalpha(alpha)
        logo = light
    im.paste(logo, ((W - logo.width) // 2, (H - logo.height) // 2), logo)
    if width and width < W:
        im = im.resize((width, int(width * 1.5)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def series_poster(title, season_name="", season=0, width=400):
    """A poster for a series made of a pack - or one of its seasons - which no catalogue
    has a picture of: black and gold, the series' name, and the season's under it."""
    import io
    from PIL import Image, ImageDraw
    W, H = 400, 600
    gold, dim = "#C9A227", "#8A7A4A"
    im = Image.new("RGB", (W, H), "#0B0B0D")
    d = ImageDraw.Draw(im)
    d.rectangle([14, 14, W - 15, H - 15], outline=gold, width=3)
    d.rectangle([22, 22, W - 23, H - 23], outline=dim, width=1)
    words = str(title or "").upper().split()
    if not season_name:
        # the series: its name large, a word to a line
        lines = words or [""]
        size = 76
        y = (H - len(lines) * (size + 14)) // 2
        for one in lines:
            _centred(d, y, one, _fit(d, one, W - 70, size, wide=True), gold, W)
            y += size + 14
    else:
        top = " ".join(words)
        _centred(d, 50, top, _fit(d, top, W - 80, 30, wide=True), gold, W)
        d.line([(60, 100), (W - 60, 100)], fill=dim, width=2)
        # the season's name, wrapped, as large as fits
        font = _font(44, wide=True)
        lines, line = [], ""
        for w in str(season_name).split():
            trial = (line + " " + w).strip()
            if d.textlength(trial, font=font) > W - 70 and line:
                lines.append(line)
                line = w
            else:
                line = trial
        if line:
            lines.append(line)
        lines = lines[:6]
        y = (H - len(lines) * 56) // 2
        for one in lines:
            _centred(d, y, one, font, "#F2E6C0", W)
            y += 56
        d.line([(60, H - 100), (W - 60, H - 100)], fill=dim, width=2)
        _centred(d, H - 80, "SEASON %d" % season, _font(26, wide=True), gold, W)
    if width and width < W:
        im = im.resize((width, int(width * 1.5)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


#: TheSportsDB: the leagues' own posters - every F1 race and NFL game has one; motocross,
#: cycling and the Dakar none (checked 2026-11-18)
POSTER_SITE = "https://www.thesportsdb.com/api/v1/json/123/"
#: its number for Formula 1
POSTER_SITE_F1 = 4370
#: the sports it has posters of
POSTER_SPORTS = ("f1", "nfl")
#: and the leagues whose own poster is a sport's card on the Sports row: motocross by MXGP's,
#: cycling by the World Tour's; the Dakar and golf have none there (checked 2026-11-18)
POSTER_LEAGUES = {"nfl": 4391}

#: sports whose card is a photograph with the sport's name over it, from Wikimedia Commons, an
#: hour each in turn: the top teams' F1 cars, cycling's great riders on their bikes, the Dakar's cars, trucks and
#: motorbikes, motocross in the air (owner, 2026-11-18). The name only - no rider's - and MX,
#: not MXGP. (word, its colour, line under it, [(photo, where across it the subject is)])
PHOTO_CARDS = {
    "f1": ("FORMULA 1", "#E10600", "", [
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/2/24/2024_British_Grand_Prix%2C_Verstappen_%283%29.jpg/960px-2024_British_Grand_Prix%2C_Verstappen_%283%29.jpg", 0.55),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0e/Lando_Norris%2CChinese_GP_2024_Race.jpg/960px-Lando_Norris%2CChinese_GP_2024_Race.jpg", 0.5),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0f/2024-08-24_Motorsport%2C_Formel_1%2C_Gro%C3%9Fer_Preis_der_Niederlande_2024_STP_3412_by_Stepro.jpg/960px-2024-08-24_Motorsport%2C_Formel_1%2C_Gro%C3%9Fer_Preis_der_Niederlande_2024_STP_3412_by_Stepro.jpg", 0.58),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/3/39/Lewis_Hamilton_2024_Chinese_GP.jpg/960px-Lewis_Hamilton_2024_Chinese_GP.jpg", 0.5),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/6/6c/Piastri_2025_Espagne_Grand_Prix.jpg/960px-Piastri_2025_Espagne_Grand_Prix.jpg", 0.6),
    ]),
    "cycling": ("CYCLING", "#F5C400", "", [
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/1/1b/Miguel_Indur%C3%A1in_Tour_de_France_1993.jpg/960px-Miguel_Indur%C3%A1in_Tour_de_France_1993.jpg", 0.45),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/0/03/Lance_Armstrong_MidiLibre_2002.jpg/960px-Lance_Armstrong_MidiLibre_2002.jpg", 0.55),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/e/e5/Marco_Pantani%2C_1997.jpg/960px-Marco_Pantani%2C_1997.jpg", 0.56),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/2/2f/Jonas_Vingegaard_in_Marie_Blanque_pass_during_Tour_de_France_2023.jpg/960px-Jonas_Vingegaard_in_Marie_Blanque_pass_during_Tour_de_France_2023.jpg", 0.48),
        ("https://upload.wikimedia.org/wikipedia/commons/7/73/Tadej_Pogacar_%C3%A9tape_16_TDF_2024.jpg", 0.5),
    ]),
    "dakar": ("DAKAR", "#F0A030", "RALLY", [
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/4/48/Peugeot_3008_DKR_Dakar_2019.jpg/960px-Peugeot_3008_DKR_Dakar_2019.jpg", 0.62),
        ("https://upload.wikimedia.org/wikipedia/commons/2/2e/Dutch_team_truck_Dakar_2010.jpg", 0.6),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a7/Dakar_2010_-_Francisco_L%C3%B3pez.jpg/960px-Dakar_2010_-_Francisco_L%C3%B3pez.jpg", 0.5),
    ]),
    "motocross": ("MX", "#FF6A13", "", [
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/1/11/Kompetisi_Motocross_MXGP_di_Pangkal_Pinang_Indonesia.jpg/960px-Kompetisi_Motocross_MXGP_di_Pangkal_Pinang_Indonesia.jpg", 0.3),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/d/d7/Motocross_jump_2008.jpg/960px-Motocross_jump_2008.jpg", 0.42),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f1/Motocross_in_Yyteri_2010_-_8.jpg/960px-Motocross_in_Yyteri_2010_-_8.jpg", 0.4),
    ]),
}

#: how long one photograph stands before the next
PHOTO_EVERY = 3600


def photo_card_now(sport, now=None):
    """The photograph a sport's card shows now: (word, colour, line, photo, across), or None."""
    card = PHOTO_CARDS.get(sport)
    if not card:
        return None
    word, colour, line, photos = card
    photo, across = photos[int((time.time() if now is None else now) // PHOTO_EVERY) % len(photos)]
    return word, colour, line, photo, across


def photo_poster(photo, word, colour, line, across=0.5, width=400):
    """A sport's card from a photograph (a path): cropped to 2:3 round `across`, dark at the
    foot, the sport's name over it and the line under that. JPEG bytes."""
    import io
    from PIL import Image, ImageDraw
    W, H = 400, 600
    pic = Image.open(photo).convert("RGB")
    scale = max(W / pic.width, H / pic.height)
    pic = pic.resize((max(W, int(pic.width * scale + 0.5)), max(H, int(pic.height * scale + 0.5))))
    left = min(max(0, int(pic.width * across - W / 2)), pic.width - W)
    top = (pic.height - H) // 3
    im = pic.crop((left, top, left + W, top + H))
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(H):
        if y > 330:
            sd.line([(0, y), (W, y)], fill=int(235 * min(1.0, (y - 330) / 200.0)))
    im = Image.composite(Image.new("RGB", (W, H), "#000000"), im, shade)
    d = ImageDraw.Draw(im)
    big = _fit(d, word, W - 48, 84, wide=True)
    small = _fit(d, line or " ", W - 48, 30, wide=True)
    # the sport's name alone sits lower, where the line would have been
    said = [(word, big, H - 168, colour), (line, small, H - 66, "#FFFFFF")] if line \
        else [(word, big, H - 130, colour)]
    for text, font, y, fill in said:
        w = d.textlength(text, font=font)
        d.text(((W - w) / 2 + 2, y + 2), text, font=font, fill="#000000")
        d.text(((W - w) / 2, y), text, font=font, fill=fill)
    if width and width < W:
        im = im.resize((width, int(width * 1.5)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


def league_poster(answer):
    """The league's poster in the poster site's answer to lookupleague, or ""."""
    for x in (answer or {}).get("leagues") or []:
        url = str(x.get("strPoster") or "")
        if url.startswith("https://"):
            return url
    return ""


def poster_ask(e):
    """What the poster site is asked for a race or game: (address, its words), or None for one
    it has no poster of - a race by its round, a game by its two teams (home first) and season."""
    if e.get("sport") == "f1" and e.get("round") and e.get("year"):
        return ("eventsround.php", {"id": POSTER_SITE_F1, "r": int(e["round"]), "s": int(e["year"])})
    if e.get("sport") == "nfl" and e.get("home") and e.get("away") and e.get("when"):
        year, month = int(e["when"][:4]), int(e["when"][5:7])
        # a game in January or February is the season before's
        return ("searchevents.php", {"e": ("%s vs %s" % (e["home"], e["away"])).replace(" ", "_"),
                                     "s": year - 1 if month < 3 else year})
    return None


def poster_pick(e, events):
    """The race's or game's poster among what the site answered: the race, not a practice,
    qualifying or sprint of its weekend; the game on its day, a day either way (the site
    counts days in UTC). None where it has none."""
    for x in events or []:
        url = str(x.get("strPoster") or "")
        if not url.startswith("https://"):
            continue
        if e.get("sport") == "f1":
            if re.search(r"(?i)practice|qualifying|sprint|shootout|testing", str(x.get("strEvent") or "")):
                continue
            return url
        if e.get("sport") == "nfl":
            try:
                apart = abs(time.mktime(time.strptime(str(x.get("dateEvent")), "%Y-%m-%d"))
                            - time.mktime(time.strptime(str(e["when"]), "%Y-%m-%d")))
            except (ValueError, KeyError):
                continue
            if apart <= 86400 * 1.5:
                return url
    return None


def sport_poster(sport, width=400):
    """The Sports row's card for one sport."""
    import io
    from PIL import Image, ImageDraw
    W, H = 400, 600
    looks = {"f1": ("#15151E", "#E10600", "F1", "FORMULA 1"),
             "nfl": ("#013369", "#D50A0A", "NFL", "FOOTBALL"),
             "golf": ("#1B5E20", "#FFFFFF", "GOLF", ""),
             "cycling": ("#F5C400", "#111111", "CYCLING", ""),
             "dakar": ("#C8913A", "#111111", "DAKAR", "RALLY"),
             "motocross": ("#111111", "#FF6A13", "MX", "MOTOCROSS")}
    bg, accent, big, small = looks.get(sport, ("#2A2A35", "#FFFFFF", str(sport).upper(), ""))
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    if sport == "f1":
        d.polygon([(0, 0), (W, 0), (W, 140), (0, 260)], fill=accent)
        d.polygon([(0, H), (W, H), (W, H - 120), (0, H - 40)], fill=accent)
    elif sport == "motocross":
        # a track's ruts: orange bands across the top and the foot, the name clear between
        for y in (70, 150, H - 120, H - 40):
            d.polygon([(0, y), (W, y - 70), (W, y - 40), (0, y + 30)], fill=accent)
    elif sport == "nfl":
        d.rectangle([0, H // 2 - 6, W, H // 2 + 6], fill="#FFFFFF")
        d.rectangle([0, 0, W, 18], fill=accent)
        d.rectangle([0, H - 18, W, H], fill=accent)
    fg = "#FFFFFF" if sport in ("f1", "nfl", "motocross") else accent
    font = _fit(d, big, W - 60, 170, wide=True)
    _centred(d, (H - 170) // 2 if sport != "nfl" else H // 2 - 200, big, font, fg, W)
    if small:
        _centred(d, H - 170 if sport != "nfl" else H // 2 + 60, small,
                 _fit(d, small, W - 80, 44, wide=True), fg, W)
    if width and width < W:
        im = im.resize((width, int(width * 1.5)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()
