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

#: how far back the row reaches
DAYS = 30
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
               "golf": "Golf"}


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
    if e["sport"] == "f1" and e.get("round"):
        return (-int(e.get("year") or 0), -int(e["round"]), -float(e.get("at") or 0))
    if e.get("when"):
        y, m, d = (int(x) for x in str(e["when"]).split("-"))
        return (-y, -(m * 100 + d), -float(e.get("at") or 0))
    return (-int(e.get("year") or 0), 0, -float(e.get("at") or 0))


def sport_cards(rows, now=None):
    """The Sports row: one card a sport that has anything, the sport with the newest
    posting first. Pressed, the sport's own events."""
    found = {}
    for e in events(rows, now=now, most=10 ** 6):
        found.setdefault(e["sport"], []).append(e)
    out = []
    for sport, these in found.items():
        word = {"f1": "race", "nfl": "game"}.get(sport, "event")
        out.append({"ratingKey": "spx" + sport, "type": "movie", "sportRow": True,
                    "sport": sport, "title": SPORT_NAMES.get(sport, sport),
                    "summary": "%d %s%s \u00b7 newest: %s" % (len(these), word,
                                                             "" if len(these) == 1 else "s",
                                                             these[0]["title"]),
                    "thumb": "/art/sports/%s" % sport, "duration": 0, "viewCount": 0,
                    "addedAt": int(max(float(e.get("at") or 0) for e in these))})
    return sorted(out, key=lambda c: -c["addedAt"])


def sport_events(rows, sport, now=None, most=40):
    """One sport's events, newest event first, each with its place in that order."""
    these = [e for e in events(rows, now=now, most=10 ** 6) if e["sport"] == sport]
    these = sorted(these, key=newest_first)[:most]
    return these


def events(rows, now=None, days=DAYS, most=MOST):
    """The newest events the index holds releases of, newest first, each with its
    releases: [{key, sport, title, year, at, releases}]."""
    now = time.time() if now is None else now
    found = {}
    for row in rows or []:
        said = read_name(row.get("name"))
        if not said:
            continue
        at = float(row.get("at") or 0)
        if days and at and now - at > days * 86400:
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
        if taken.get(e["sport"], 0) >= PER_SPORT.get(e["sport"], PER_SPORT_ELSE):
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
            "genres": [sport], "thumb": "/art/%s/poster" % e["key"], "duration": 0, "viewCount": 0,
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
    for name in (stem, folder):
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


def sport_poster(sport, width=400):
    """The Sports row's card for one sport."""
    import io
    from PIL import Image, ImageDraw
    W, H = 400, 600
    looks = {"f1": ("#15151E", "#E10600", "F1", "FORMULA 1"),
             "nfl": ("#013369", "#D50A0A", "NFL", "FOOTBALL"),
             "golf": ("#1B5E20", "#FFFFFF", "GOLF", ""),
             "cycling": ("#F5C400", "#111111", "CYCLING", ""),
             "dakar": ("#C8913A", "#111111", "DAKAR", "RALLY")}
    bg, accent, big, small = looks.get(sport, ("#2A2A35", "#FFFFFF", str(sport).upper(), ""))
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    if sport == "f1":
        d.polygon([(0, 0), (W, 0), (W, 140), (0, 260)], fill=accent)
        d.polygon([(0, H), (W, H), (W, H - 120), (0, H - 40)], fill=accent)
    elif sport == "nfl":
        d.rectangle([0, H // 2 - 6, W, H // 2 + 6], fill="#FFFFFF")
        d.rectangle([0, 0, W, 18], fill=accent)
        d.rectangle([0, H - 18, W, H], fill=accent)
    fg = "#FFFFFF" if sport in ("f1", "nfl") else accent
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
