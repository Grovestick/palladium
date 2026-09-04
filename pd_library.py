#!/usr/bin/env python3
"""Palladium's own media library: scan folders, identify titles, keep it all locally.

The library Palladium serves. Point it at the folders you already have and it
builds its own index, owning none of the files:

    folders  ->  scanner (filenames)  ->  ffprobe (codecs)  ->  TMDB (artwork, plot)
                                                            ->  library.db + cache/

Notes that shaped it:
  * IMDb has no public API and blocks scraping (403), so identification goes through
    TMDB, which carries the IMDb id for every title - stored so links still work.
  * Everything is cached on disk. A rescan only touches files whose size or mtime
    changed, and artwork is downloaded once.
  * No API key is required to have a working library: without one you get everything
    the filenames and the files themselves can tell us, and artwork fills in later.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".wmv"}
TMDB = "https://api.themoviedb.org/3"

# junk that release names carry around; stripped before we try to identify anything
JUNK = re.compile(
    r"\b(1080p|2160p|720p|480p|4k|uhd|hdr10?|hdr|dv|bluray|blu-ray|bdrip|brrip|dvdrip|"
    r"webrip|web-dl|webdl|web|hdtv|remux|proper|repack|extended|unrated|directors?\.?cut|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|xvid|divx|aac|ac3|eac3|dts(-hd)?|ma|truehd|atmos|"
    r"5\.1|7\.1|2\.0|ddp?5?1?|multi|dual|subs?|dubbed|imax|amzn|nf|dsnp|hmax|atvp)\b",
    re.I)
# lookarounds, not consuming characters: "2049.2017" has to yield both years, and
# a consuming pattern eats the separator and finds only the first
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
EPISODE_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})"),          # S01E02
    re.compile(r"(?:^|[^\d])(\d{1,2})x(\d{1,3})(?:[^\d]|$)"),   # 1x02
    re.compile(r"[Ss]eason[\s._-]*(\d{1,2}).*?[Ee]p?(?:isode)?[\s._-]*(\d{1,3})", re.I),
]


def clean_title(name):
    """A release filename down to something a metadata service can match.

    Careful with two habits that look clever and are not: stripping any short
    dot-suffix as an "extension" turns Regular.Show into Regular, and stripping a
    trailing -WORD as a release group turns The.X-Files into The.X. So only real
    video extensions go, and the group only goes when the name actually looks like
    a release (it carries a year or scene tags).
    """
    name = re.sub(r"\.(mkv|mp4|m4v|avi|mov|webm|ts|m2ts|wmv)$", "", name, flags=re.I)
    looks_release = bool(YEAR.search(name)) or bool(JUNK.search(name))
    name = re.sub(r"[\[\(].*?[\]\)]", " ", name)               # [group] (tags)
    name = name.replace("_", " ").replace(".", " ")
    name = JUNK.sub(" ", name)
    if looks_release:
        name = re.sub(r"-\s*[A-Za-z0-9]+$", "", name.strip())
    return re.sub(r"\s{2,}", " ", name).strip(" -")


def alone_in_folder(path):
    """Is this the only video file in its directory?

    Cheap enough to ask only when the folder and the file disagree, which is rare.
    """
    try:
        folder = os.path.dirname(path)
        seen = 0
        for name in os.listdir(folder):
            if os.path.splitext(name)[1].lower() in VIDEO_EXT:
                seen += 1
                if seen > 1:
                    return False
        return seen == 1
    except OSError:
        return False


def parse_movie(path):
    """Title and release year from a filename.

    The year is the last *plausible* one, not the first: a film whose own title
    ends in a number is written with that number and then its release year.
    Anything beyond next year is part of the title, not a release date - taking
    the first number catalogued such a film in a year that has not happened, and
    it never matched anything again.
    """
    def read(name):
        limit = time.localtime().tm_year + 1
        years = [(m.start(1), int(m.group(1))) for m in YEAR.finditer(name)]
        plausible = [(pos, y) for pos, y in years if 1900 <= y <= limit]
        if plausible:
            pos, year = plausible[-1]
            return clean_title(name[:pos]), year
        return clean_title(name), None

    # "1080-beetlejuice.mkv": a resolution glued to the front by whoever named it.
    # Only digits are stripped this way - a title may legitimately begin with a word
    # and a hyphen, as Spider-Man does.
    stem = re.sub(r"^\d{3,4}-(?=[A-Za-z])", "", os.path.basename(path))
    title, year = read(stem)
    folder = os.path.basename(os.path.dirname(path))
    ftitle, fyear = read(folder)

    # A release folder holds one film and names it properly; the file inside often
    # carries the group at the front ("rusted-windoms.way..."), which is not part of
    # any title and matches nothing. Trust the folder when it names a year, since that
    # is what makes it a release folder rather than a shelf full of films.
    if fyear and len(ftitle) > 1 and (not title or fyear == year or not year):
        title, year = ftitle, fyear
    elif fyear and len(ftitle) > 1 and year and fyear != year and alone_in_folder(path):
        # the two disagree and there is only one film here, so the folder wins: a
        # mistyped year inside a correctly named release is the common way round
        title, year = ftitle, fyear

    if not title:                                   # nothing before the year: use the folder
        title = ftitle or clean_title(folder)
    return title, year


def episode_from_folders(path):
    """(show, season, episode) when a folder rather than the filename carries SxxExx.

    Read from the innermost folder outwards, because the episode's own folder is the
    one that names it; the series is then whatever the folder above is called.
    """
    parts = os.path.normpath(path).split(os.sep)[:-1]
    for depth, folder in enumerate(reversed(parts)):
        for pat in EPISODE_PATTERNS:
            m = pat.search(folder)
            if not m:
                continue
            season, ep = int(m.group(1)), int(m.group(2))
            show = clean_title(folder[:m.start()])
            show = YEAR.sub(" ", show).strip(" -")
            if len(show) < 2:                  # the folder is only "S01E02"
                above = list(reversed(parts))[depth + 1:]
                show = next((clean_title(f) for f in above
                             if not re.match(r"(?i)^(season|s)\s*\d+", f)
                             and len(clean_title(f)) > 2), "")
                show = YEAR.sub(" ", show).strip(" -")
            return (show, season, ep) if len(show) > 1 else None
    return None


def show_from_folder(path):
    """The show's name as the folders give it, with season markers and junk removed."""
    parts = os.path.normpath(path).split(os.sep)[:-1]
    for folder in reversed(parts):
        if re.match(r"(?i)^(season|s)\s*\d+$", folder):
            continue                                # "Season 2" names no show
        m = re.search(r"(?i)(?:^|[ ._-])(?:s|season)[ ._-]?\d{1,2}", folder)
        head = clean_title(folder[:m.start()] if m else folder)
        head = YEAR.sub(" ", head).strip(" -")
        if len(head) > 2:
            return head
    return None


def _same_start(folder_name, file_name):
    """Do these name the same show, the folder more briefly?"""
    a = re.sub(r"[^a-z0-9]", "", (folder_name or "").lower())
    b = re.sub(r"[^a-z0-9]", "", (file_name or "").lower())
    return bool(a) and len(a) >= 3 and b.startswith(a)


#: Everything a release adds after the episode's own name. The title sits between the
#: SxxExx marker and the first of these.
RELEASE_WORDS = re.compile(
    r"(?i)[ ._-](?:web[ ._-]?dl|webrip|web|bluray|blu[ ._-]?ray|bdrip|brrip|hdtv|dvdrip|"
    r"dvd|remux|proper|repack|internal|extended|uncut|limited|amzn|nf|dsnp|hmax|atvp|"
    r"itunes|hulu|1080p|720p|2160p|480p|4k|uhd|x264|x265|h264|h265|hevc|avc|xvid|"
    r"10bit|8bit|aac|ac3|eac3|dts|ddp?5|dd5|flac|opus|mp3|2[ ._-]?0|5[ ._-]?1|"
    r"multi|dual|sub(?:s|bed)?|swedish|nordic|complete)(?:[ ._-]|$)")

EPISODE_MARK = re.compile(
    r"(?i)s\d{1,2}[ ._-]?e\d{1,3}(?:[ ._-]?(?:e|-)\d{1,3})*|(?<![a-z0-9])\d{1,2}x\d{1,3}")


def file_episode(path, season=None):
    """The episode number the filename itself gives, or None if it gives none.

    Only when it agrees about the season: "S02E05" beside a season 3 episode says
    nothing about season 3.
    """
    m = re.search(r"s(\d{1,2})[\s._-]?e(\d{1,3})|(\d{1,2})x(\d{1,3})",
                  os.path.basename(path or ""), re.I)
    if not m:
        return None
    said_season = int(m.group(1) or m.group(3))
    said_number = int(m.group(2) or m.group(4))
    if season is not None and said_season != int(season):
        return None
    return said_number


def title_in_name(path):
    """The episode's own title as the filename gives it, or nothing.

    "A Series S04E03 The Episode Web-DL 2.0 1080p x265" is "The Episode": what
    follows the marker, up to the first word about the release rather than the story.
    """
    base = os.path.splitext(os.path.basename(path))[0]
    m = EPISODE_MARK.search(base)
    if not m:
        return ""
    rest = base[m.end():]
    stop = RELEASE_WORDS.search(rest)
    if stop:
        rest = rest[:stop.start()]
    rest = re.sub(r"[._]+", " ", rest).strip(" -[](){}")
    # a release group left on the end, and years, help nothing
    rest = YEAR.sub(" ", rest)
    return re.sub(r"\s{2,}", " ", rest).strip()


def same_title(said, want):
    """Two spellings of one episode title, as loosely as is still safe."""
    def bare(s):
        s = re.sub(r"(?i)\bpart\s*(\d+)", r"\1", s or "")
        s = re.sub(r"(?i)\b(i{1,3}|iv|v|vi{1,3})\b",
                   lambda m: str({"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
                                  "vi": 6, "vii": 7, "viii": 8}[m.group(1).lower()]), s)
        return re.sub(r"[^a-z0-9]", "", s.lower())

    a, b = bare(said), bare(want)
    if not a or not b or min(len(a), len(b)) < 4:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.88


def parse_episode(path):
    """(show, season, episode) or None - the show name comes from the folder when it can."""
    base = os.path.basename(path)
    for pat in EPISODE_PATTERNS:
        m = pat.search(base)
        if not m:
            continue
        season, ep = int(m.group(1)), int(m.group(2))
        show = clean_title(base[:m.start()])
        if len(show) < 2:                           # "S01E02.mkv" - look at the folders
            parent = os.path.basename(os.path.dirname(path))
            if re.match(r"(?i)^(season|s)\s*\d+$", parent):
                parent = os.path.basename(os.path.dirname(os.path.dirname(path)))
            show = clean_title(parent)
        show = YEAR.sub(" ", show).strip(" -")
        # the filename often carries the episode's own title before the marker, which
        # would make every episode a series of its own; the folder says it once
        folder_show = show_from_folder(path)
        if folder_show and _same_start(folder_show, show):
            show = folder_show
        return show, season, ep
    return None


# "Angels.of.Death.01.Blood.mkv" in a folder called "...S01..." - the number is in the
# file and the season is in the folder, which is a common shape for a ripped season
LOOSE_NUMBER = re.compile(r"(?i)(?:^|[ ._-])(?:e|ep|episode|part)?[ ._-]?(\d{1,3})"
                          r"(?=[ ._-]|$|\.[a-z0-9]{2,4}$)")
FOLDER_SEASON = re.compile(r"(?i)(?:^|[ ._-])(?:s|season)[ ._-]?(\d{1,2})(?:[ ._-]|$)")


def in_a_season_folder(path):
    """True when a parent folder is a season and nothing else.

    "Season 01" or "S01" above a file is the folder saying television as plainly as
    SxxExx in the name would: a film does not live in a season. It is what lets a
    mixed folder read "Season 01/01 - A Rose for Lotta.avi" as an episode while
    "Alien 3" beside it stays a film.
    """
    parts = os.path.normpath(path).split(os.sep)[:-1]
    return any(re.match(r"(?i)^(season|s)[ ._-]?\d{1,2}$", folder.strip())
               for folder in parts)


def flatten_title(title):
    """A title with nothing in it but letters and digits, for comparing two spellings.

    Apostrophes, dots, ampersands and dashes are where a file name and a catalogue
    disagree; the words are where they agree.
    """
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower().replace("&", "and"))


def parse_episode_loose(path):
    """(show, season, episode) for a file in a series folder with no SxxExx.

    Only ever used where the folder has already said "this is television", so it can
    afford to guess: the season comes from whichever parent folder names one, the
    number from the first plausible integer in the filename, and the show from the
    folder above.
    """
    base = os.path.splitext(os.path.basename(path))[0]
    parts = os.path.normpath(path).split(os.sep)[:-1]

    season, show_from = None, None
    for folder in reversed(parts):
        m = FOLDER_SEASON.search(folder)
        if m and season is None:
            season = int(m.group(1))
            # "Show.Name.S01.WEBRip..." names the show as well as the season
            head = clean_title(folder[:m.start()])
            if len(head) > 1:
                show_from = head
            continue
        if show_from is None and folder and not re.match(r"(?i)^(season|s)\s*\d+$", folder):
            show_from = clean_title(folder)
    if season is None:
        season = 1

    stripped = base
    if show_from:
        # do not read the show's own name as an episode number ("24", "Alien 3")
        lead = re.escape(show_from.replace(" ", "")).replace(r"\ ", "[ ._-]*")
        stripped = re.sub("(?i)^" + lead.replace(" ", "[ ._-]*"), "", base.replace(" ", ""))
    m = LOOSE_NUMBER.search(stripped) or LOOSE_NUMBER.search(base)
    if not m:
        # DVD-rip scene naming: "prince-trek103" is season 1, episode 3. Three digits
        # glued to the end of a name, only ever read this way inside a series folder.
        m3 = re.search(r"(\d)(\d{2})$", base)
        if m3:
            return ((show_from or clean_title(base)).strip(),
                    int(m3.group(1)), int(m3.group(2)))
        return None
    number = int(m.group(1))
    if not (0 < number < 400):
        return None
    show = show_from or clean_title(base)
    # folder names full of brackets and dashes leave gaps behind once stripped
    show = re.sub(r"[\s-]{2,}", " ", YEAR.sub(" ", show)).strip(" -")
    if len(show) < 2:
        return None
    return show, season, number


def ffprobe_beside(ffmpeg):
    """The ffprobe that belongs to this ffmpeg.

    Not a string replacement on the name: WinGet's shim is called ffmpeg.EXE, and
    replacing "ffmpeg.exe" in it changed nothing - so ffmpeg itself was run with
    ffprobe's arguments, answered nothing, and every file scanned since was recorded
    as having no codecs at all. A file with no codecs cannot be shown to play
    directly, so all of them were transcoded.
    """
    if not ffmpeg:
        return shutil.which("ffprobe") or "ffprobe"
    folder, name = os.path.split(ffmpeg)
    stem, ext = os.path.splitext(name)
    if stem.lower().endswith("ffmpeg"):
        near = os.path.join(folder, stem[:-len("ffmpeg")] + "ffprobe" + ext)
        if os.path.exists(near):
            return near
    return shutil.which("ffprobe") or "ffprobe"


class Library:
    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "cache"), exist_ok=True)
        self.dbpath = os.path.join(root, "library.db")
        self.cfgpath = os.path.join(root, "library.json")
        self.lock = threading.Lock()
        self.scan_state = {"running": False, "done": 0, "total": 0, "phase": "idle"}
        self._init_db()

    # ---- config -------------------------------------------------------------
    def config(self):
        if not os.path.exists(self.cfgpath):
            return {"movies": [], "tv": [], "tmdb_key": "", "language": "en-US"}
        with open(self.cfgpath, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def exclusive(cfg, changed=None):
        """A folder may be in one list only.

        Being in two meant every file under it was read twice, once as a film and once
        as an episode, and whichever list came first won - which is not a decision
        anybody made. The list most recently edited keeps the folder.
        """
        lists = ["movies", "tv", "mixed"]
        order = ([changed] + [k for k in lists if k != changed]) if changed else lists
        claimed = set()
        for key in order:
            kept = []
            for folder in cfg.get(key, []) or []:
                mark = folder.rstrip("\\/").lower()
                if mark in claimed:
                    continue
                claimed.add(mark)
                kept.append(folder)
            cfg[key] = kept
        return cfg

    def save_config(self, cfg):
        with open(self.cfgpath, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return cfg

    # ---- storage ------------------------------------------------------------
    def db(self):
        """A connection that waits its turn rather than giving up.

        One server writing while another reads is now ordinary here: a scan, a
        viewing being written down, and a machine asking every minute what is worth
        copying. In the old journal a writer shuts every reader out, and a reader
        that will not wait says "database is locked" - which it did, thirty times in
        one evening. The write-ahead log lets them work at once, and the timeout
        covers the moment when they genuinely have to queue.
        """
        con = sqlite3.connect(self.dbpath, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout=30000")
            if not Library._walked:
                con.execute("PRAGMA journal_mode=WAL")
                Library._walked = True
            con.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass                      # an older file, or one on a share: as it was
        return con

    #: whether the file has been put into the write-ahead log this run. It is a
    #: property of the file, not of a connection, so it is asked for once.
    _walked = False

    def _init_db(self):
        con = self.db()
        con.executescript("""
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY,
            type TEXT,                -- movie | show
            title TEXT, sort_title TEXT, year INTEGER,
            tmdb_id INTEGER, imdb_id TEXT,
            overview TEXT, rating REAL, genres TEXT, runtime INTEGER,
            poster TEXT, backdrop TEXT,
            added INTEGER, identified INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS file (
            id INTEGER PRIMARY KEY,
            item_id INTEGER, episode_id INTEGER,
            path TEXT UNIQUE, size INTEGER, mtime INTEGER,
            duration REAL, container TEXT, vcodec TEXT, acodec TEXT, ctime INTEGER,
            width INTEGER, height INTEGER, channels INTEGER, bitrate INTEGER,
            probed INTEGER DEFAULT 0, streams TEXT
        );
        CREATE TABLE IF NOT EXISTS episode (
            id INTEGER PRIMARY KEY,
            item_id INTEGER, season INTEGER, number INTEGER,
            title TEXT, overview TEXT, aired TEXT, still TEXT,
            UNIQUE(item_id, season, number)
        );
        CREATE TABLE IF NOT EXISTS progress (
            key TEXT PRIMARY KEY, position REAL, duration REAL, updated INTEGER
        );
        CREATE TABLE IF NOT EXISTS watchlog (
            id INTEGER PRIMARY KEY,
            who TEXT, key TEXT, title TEXT, device TEXT, client TEXT,
            started INTEGER, updated INTEGER,
            position REAL, duration REAL, casual INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS watchlog_when ON watchlog(updated DESC);
        """)
        # which build was watching: added later, so the column is checked for rather
        # than assumed
        if "app" not in [r[1] for r in con.execute(
                "PRAGMA table_info(watchlog)").fetchall()]:
            con.execute("ALTER TABLE watchlog ADD COLUMN app TEXT")
        con.executescript("""
        CREATE INDEX IF NOT EXISTS item_type ON item(type, sort_title);
        CREATE INDEX IF NOT EXISTS file_item ON file(item_id);
        CREATE INDEX IF NOT EXISTS ep_item ON episode(item_id, season, number);
        """)
        # older databases predate the ctime column
        cols = [r[1] for r in con.execute("PRAGMA table_info(file)").fetchall()]
        if "ctime" not in cols:
            con.execute("ALTER TABLE file ADD COLUMN ctime INTEGER")
        # what soundtracks a file holds. Empty for everything probed before this
        # existed; filled in the first time somebody opens the title.
        if "atracks" not in cols:
            con.execute("ALTER TABLE file ADD COLUMN atracks TEXT")
        # Everyone keeps their own place in a film. "who" is "me" for whoever runs the
        # server and the invitation token for everybody else; progress recorded before
        # this becomes the owner's, which is whose it was.
        # The table was keyed by title alone, so two people could not both have a
        # place in the same film. A key cannot be altered in SQLite, so it is rebuilt;
        # whatever was recorded before becomes the owner's, which is whose it was.
        info = con.execute("PRAGMA table_info(progress)").fetchall()
        has_who = any(r[1] == "who" for r in info)
        # The old table was keyed by the film alone; the new one by the viewer and
        # the film together. PRAGMA numbers the columns of a composite key 1, 2 - so
        # "key is part of the primary key" is true of both, and this rebuilt the
        # table on every single start. That cost nothing until the table grew a
        # column: the rebuild did not carry it, and every tick anybody had made was
        # wiped each time the server came up.
        keyed_alone = any(r[1] == "key" and r[5] == 1 for r in info) and not has_who
        if keyed_alone:
            con.execute("""CREATE TABLE progress_new (
                who TEXT NOT NULL DEFAULT 'me', key TEXT NOT NULL,
                position REAL, duration REAL, updated INTEGER,
                PRIMARY KEY (who, key)
            )""")
            # everything the old table held, including anything added since - a
            # rebuild that drops a column is how a whole evening of ticks vanished
            keep = [r[1] for r in info
                    if r[1] not in ("who", "key", "position", "duration", "updated")]
            for name in keep:
                con.execute("ALTER TABLE progress_new ADD COLUMN %s" % name)
            cols = ", ".join(["who", "key", "position", "duration", "updated"] + keep)
            con.execute("""INSERT INTO progress_new (%s)
                           SELECT %s, key, position, duration, updated%s FROM progress"""
                        % (cols, "COALESCE(who, 'me')" if has_who else "'me'",
                           ("," + ",".join(keep)) if keep else ""))
            con.execute("DROP TABLE progress")
            con.execute("ALTER TABLE progress_new RENAME TO progress")
        # A mark made by hand is not a viewing, and the two disagreed: an episode
        # somebody ticked stayed unwatched because the log said they had not sat
        # through it. The tick says so itself now.
        if not any(r[1] == "marked" for r in
                   con.execute("PRAGMA table_info(progress)").fetchall()):
            con.execute("ALTER TABLE progress ADD COLUMN marked INTEGER DEFAULT 0")
            # what the old scheme wrote for a tick: the full duration, and no viewing
            # anywhere in the log to have produced it. Those are hand marks, and they
            # were being argued with.
            con.execute("""UPDATE progress SET marked = 1
                           WHERE duration > 0 AND position >= duration * 0.99
                             AND NOT EXISTS (SELECT 1 FROM watchlog w
                                             WHERE w.who = progress.who
                                               AND w.key = progress.key)""")
        # And the ticks that were wiped: a row at the very beginning that nobody has
        # ever played is not somebody a minute into a film - it is a tick whose mark
        # was thrown away by the rebuild above. Runs once, alongside the repair.
        if not con.execute("SELECT COUNT(*) c FROM progress WHERE marked=1")                .fetchone()[0]:
            con.execute("""UPDATE progress SET marked = 1
                           WHERE COALESCE(marked, 0) = 0 AND duration > 0
                             AND position <= 30
                             AND NOT EXISTS (SELECT 1 FROM watchlog w
                                             WHERE w.who = progress.who
                                               AND w.key = progress.key)""")
        con.commit()
        con.close()

    # ---- scanning -----------------------------------------------------------
    def walk(self, folders):
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for dirpath, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(("$", "."))
                           and d.lower() not in ("sample", "samples", "extras",
                                                 "featurettes", "proof")]
                for name in files:
                    if os.path.splitext(name)[1].lower() in VIDEO_EXT:
                        full = os.path.join(dirpath, name)
                        try:
                            st = os.stat(full)
                        except OSError:
                            continue
                        if st.st_size < 50 * 1024 * 1024:     # samples, trailers, extras
                            continue
                        yield full, st

    @staticmethod
    def kind_for(path, folders):
        """Which list this file belongs to: the innermost configured folder wins.

        So a series that lives inside a film folder is corrected by adding that one
        folder to Series, without moving anything on disk.
        """
        here = os.path.normcase(os.path.abspath(path))
        best, best_len = None, -1
        for folder, kind in folders:
            root = os.path.normcase(os.path.abspath(folder))
            if here.startswith(root + os.sep) and len(root) > best_len:
                best, best_len = kind, len(root)
        return best

    def scan(self, probe=True, identify=True):
        """Walk the configured folders and bring the index up to date."""
        cfg = self.config()
        self.scan_state = {"running": True, "done": 0, "total": 0, "phase": "walking"}
        # innermost first, and each file considered once however many lists cover it
        folders = ([(f, "movie") for f in cfg.get("movies", [])] +
                   [(f, "show") for f in cfg.get("tv", [])] +
                   [(f, "mixed") for f in cfg.get("mixed", [])])
        found, seen_paths = [], set()
        for path, st in self.walk([f for f, _ in folders]):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            found.append((self.kind_for(path, folders) or "mixed", path, st))
        self.scan_state.update(total=len(found), phase="indexing")
        con = self.db()
        seen = set()
        for kind, path, st in found:
            seen.add(path)
            row = con.execute("SELECT id, size, mtime FROM file WHERE path=?", (path,)).fetchone()
            if row and row["size"] == st.st_size and row["mtime"] == int(st.st_mtime):
                self.scan_state["done"] += 1
                continue                                    # unchanged
            self._index_file(con, kind, path, st)
            self.scan_state["done"] += 1
        # Rows the walk did not produce: either the file is gone, or it is no longer
        # one of ours - a sample, an extra, or a folder taken off the list. Both are
        # reasons to drop it. The exception is a drive that is not plugged in, where
        # every path under it would vanish at once and take the library with it.
        roots = [f for f, _ in folders if os.path.isdir(f)]
        for row in con.execute("SELECT id, path FROM file").fetchall():
            if row["path"] in seen:
                continue
            under_a_live_root = any(
                os.path.normcase(row["path"]).startswith(os.path.normcase(r) + os.sep)
                for r in roots)
            if not os.path.exists(row["path"]) or under_a_live_root:
                con.execute("DELETE FROM file WHERE id=?", (row["id"],))
        con.execute("DELETE FROM item WHERE id NOT IN (SELECT DISTINCT item_id FROM file)")
        con.commit()
        con.close()
        if probe:
            self.probe_pending()
        if identify and self.config().get("tmdb_key"):
            self.identify_pending()
            self.merge_duplicates()      # two spellings, one title
        self.scan_state.update(running=False, phase="idle")
        return self.stats()

    def _index_file(self, con, kind, path, st, reset_probe=True):
        """What a file is comes from the list its folder is in.

        A series folder yields episodes, taking the season from the folder when the
        filename does not carry it. A film folder yields films - unless the filename
        itself carries SxxExx, which is the file saying what it is rather than anything
        being inferred, and which is how whole seasons come to sit in a download folder.
        A mixed folder has nothing but the filename to go on either way.

        What the folder buys is protection from the looser reading: "Alien 3" in a film
        folder stays a film, where in a series folder it would be episode three.
        """
        parsed = parse_episode(path)                  # unambiguous in any folder
        if parsed is None:
            parsed = episode_from_folders(path)       # equally unambiguous
        if parsed is None and (kind == "show" or in_a_season_folder(path)):
            parsed = parse_episode_loose(path)
        if parsed:
            show, season, number = parsed
            item_id = self._upsert_item(con, "show", show, None)
            con.execute(
                "INSERT OR IGNORE INTO episode (item_id, season, number) VALUES (?,?,?)",
                (item_id, season, number))
            episode_id = con.execute(
                "SELECT id FROM episode WHERE item_id=? AND season=? AND number=?",
                (item_id, season, number)).fetchone()["id"]
        elif kind == "show":
            return                       # nothing in the name or the folders to go on
        else:
            title, year = parse_movie(path)
            if not title:
                return
            item_id = self._upsert_item(con, "movie", title, year)
            episode_id = None
        # re-parsing a filename must not discard what ffprobe already learned: that
        # costs minutes to rebuild and nothing about the file itself has changed
        ctime = int(getattr(st, "st_ctime", 0) or st.st_mtime)
        probe_clause = "probed=0" if reset_probe else "probed=file.probed"
        con.execute(f"""INSERT INTO file (item_id, episode_id, path, size, mtime, ctime, probed)
                       VALUES (?,?,?,?,?,?,0)
                       ON CONFLICT(path) DO UPDATE SET item_id=excluded.item_id,
                         episode_id=excluded.episode_id, size=excluded.size,
                         mtime=excluded.mtime, ctime=excluded.ctime, {probe_clause}""",
                    (item_id, episode_id, path, st.st_size, int(st.st_mtime), ctime))
        # "recently added" should mean when it arrived here, not when the scan ran
        con.execute("""UPDATE item SET added = MAX(COALESCE(added, 0), ?) WHERE id = ?""",
                    (ctime, item_id))

    def _upsert_item(self, con, kind, title, year):
        sort = re.sub(r"^(the|a|an)\s+", "", (title or "").lower()).strip()
        row = con.execute("SELECT id FROM item WHERE type=? AND sort_title=? AND "
                          "(year IS ? OR year=?)", (kind, sort, year, year)).fetchone()
        if row:
            return row["id"]
        # The stored title is whatever TMDB called it, and a filename never spells it
        # the same way: "Its Always Sunny in Philadelphia" against "It's Always Sunny
        # in Philadelphia" is one programme. Without this, re-reading the filenames
        # made a second, unidentified item and left the first with no files - which is
        # a library that loses every poster it had.
        plain = flatten_title(sort)
        if plain:
            for other in con.execute(
                    "SELECT id, sort_title FROM item WHERE type=? AND (year IS ? OR year=?)",
                    (kind, year, year)):
                if flatten_title(other["sort_title"] or "") == plain:
                    return other["id"]
        # No arrival time of its own: it belongs to the files. Stamping the scan time
        # here is what made "recently added" mean "in the order I was indexed" - the
        # MAX() that folds in the file's own date could never beat the current time.
        cur = con.execute("INSERT INTO item (type, title, sort_title, year, added) "
                          "VALUES (?,?,?,?,0)", (kind, title, sort, year))
        return cur.lastrowid

    # ---- media facts --------------------------------------------------------
    def probe_pending(self, limit=100000):
        """ffprobe whatever has not been probed: codecs decide how it can be played."""
        from pd_gpu import FFMPEG
        ffprobe = ffprobe_beside(FFMPEG)
        con = self.db()
        rows = con.execute("SELECT id, path FROM file WHERE probed=0 LIMIT ?", (limit,)).fetchall()
        self.scan_state.update(phase="probing", total=len(rows), done=0)
        for row in rows:
            info = self.probe(ffprobe, row["path"])
            if info:
                con.execute("""UPDATE file SET duration=?, container=?, vcodec=?, acodec=?,
                               width=?, height=?, channels=?, bitrate=?, streams=?,
                               atracks=?, probed=1
                               WHERE id=?""",
                            (info["duration"], info["container"], info["vcodec"], info["acodec"],
                             info["width"], info["height"], info["channels"], info["bitrate"],
                             json.dumps(info["streams"]),
                             json.dumps(info.get("atracks") or []), row["id"]))
            else:
                # A probe that failed is not a file that has been probed. This marked
                # it done either way, so a film that was still being written when the
                # scanner reached it was left with no codecs for ever - and a file
                # with no codecs cannot be shown to play directly, so it was
                # transcoded on every screen that asked for it. A file touched in the
                # last day is likely still arriving and gets another look next scan;
                # anything older is genuinely unreadable and is not asked again.
                try:
                    fresh = time.time() - os.path.getmtime(row["path"]) < 86400
                except OSError:
                    fresh = False
                if not fresh:
                    con.execute("UPDATE file SET probed=1 WHERE id=?", (row["id"],))
            self.scan_state["done"] += 1
            if self.scan_state["done"] % 25 == 0:
                con.commit()
        con.commit()
        con.close()
        self.scan_state.update(phase="idle")

    @staticmethod
    def probe(ffprobe, path):
        try:
            out = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json",
                                  "-show_format", "-show_streams", path],
                                 capture_output=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            data = json.loads(out.stdout.decode("utf-8", "replace"))
        except Exception:
            return None
        fmt = data.get("format", {})
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
        # forced and default come from the container's own disposition, which is
        # the only reliable way to know: a forced track is often named nothing at all
        subs = [{"index": s.get("index"), "codec": s.get("codec_name"),
                 "lang": (s.get("tags") or {}).get("language"),
                 "title": (s.get("tags") or {}).get("title"),
                 "forced": bool((s.get("disposition") or {}).get("forced")),
                 "default": bool((s.get("disposition") or {}).get("default"))}
                for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]
        # every soundtrack the file holds: a second language, a commentary, a stereo
        # downmix beside the 5.1. Numbered as ffmpeg numbers them, which is what the
        # transcoder will be told to take.
        auds = [{"index": s.get("index"), "codec": s.get("codec_name"),
                 "channels": s.get("channels"),
                 "lang": (s.get("tags") or {}).get("language"),
                 "title": (s.get("tags") or {}).get("title"),
                 "default": bool((s.get("disposition") or {}).get("default"))}
                for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        return {
            "duration": float(fmt.get("duration") or 0),
            "container": (fmt.get("format_name") or "").split(",")[0],
            "vcodec": v.get("codec_name"), "acodec": a.get("codec_name"),
            "width": v.get("width"), "height": v.get("height"),
            "channels": a.get("channels"),
            "bitrate": int(float(fmt.get("bit_rate") or 0) / 1000),
            "streams": subs,
            "atracks": auds,
        }

    # ---- identification -----------------------------------------------------
    def tmdb(self, path, **params):
        cfg = self.config()
        params["api_key"] = cfg.get("tmdb_key", "")
        params.setdefault("language", cfg.get("language", "en-US"))
        url = f"{TMDB}{path}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())

    def identify_pending(self, limit=10000):
        cfg = self.config()
        if not cfg.get("tmdb_key"):
            return {"identified": 0, "reason": "no tmdb key configured"}
        con = self.db()
        rows = con.execute("SELECT * FROM item WHERE identified=0 LIMIT ?", (limit,)).fetchall()
        self.scan_state.update(phase="identifying", total=len(rows), done=0)
        done = 0
        for row in rows:
            try:
                self._identify(con, row)
                done += 1
            except Exception:
                pass                       # a title that will not match must not stop the rest
            con.execute("UPDATE item SET identified=1 WHERE id=?", (row["id"],))
            con.commit()
            self.scan_state["done"] += 1
        con.close()
        self.scan_state.update(phase="idle")
        return {"identified": done}

    @staticmethod
    def _plain(text):
        """A title with nothing in it but letters and numbers, for comparing two."""
        return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

    def _pick_match(self, results, title, year):
        """Which of TMDB's answers is the film in hand.

        TMDB answers in the order most people looked things up, which is not the order
        of what a file is: a short title comes back under the sequel that repeats it,
        and the first answer is the wrong film. The name in the file is exact and so
        is the year beside it, so both are asked before popularity gets a say: the
        same title, then the same year, then whatever else agrees, and only then the
        order they arrived in.
        """
        want = self._plain(title)
        def score(n_and_row):
            n, r = n_and_row
            name = r.get("title") or r.get("name") or ""
            date = r.get("release_date") or r.get("first_air_date") or ""
            theirs = int(date[:4]) if date[:4].isdigit() else 0
            plain = self._plain(name)
            return (plain == want,                       # this title, not one like it
                    bool(year) and theirs == int(year),  # and the year on the file
                    plain.startswith(want) or want.startswith(plain),
                    -abs(theirs - int(year)) if (year and theirs) else -999,
                    -n)                                  # TMDB's own order, last
        return max(enumerate(results), key=score)[1]

    def _identify(self, con, row):
        kind = "movie" if row["type"] == "movie" else "tv"
        q = {"query": row["title"]}
        if row["year"] and kind == "movie":
            q["year"] = row["year"]
        res = self.tmdb(f"/search/{kind}", **q).get("results") or []
        if not res and "year" in q:                  # the year may belong to the title
            q.pop("year")
            res = self.tmdb(f"/search/{kind}", **q).get("results") or []
        if not res:
            # The name in the file is whoever packed it, and a title spelt wrong is
            # a title TMDB answers nothing at all for - so the film sat in the
            # library with no poster and no plot and was never asked about again.
            # The beginning of a name is usually right, so it is asked for in
            # shortening steps - and the year decides which of the answers it is,
            # which is what a shorter question makes ambiguous.
            # only with a year to judge by: a shorter question has more answers, and
            # without the year the first of them is a guess. Three extras named
            # "Animatic ..." were all answered with the same unrelated film.
            words = str(row["title"] or "").split() if row["year"] else []
            for cut in range(len(words) - 1, 1, -1):
                shorter = " ".join(words[:cut])
                res = self.tmdb(f"/search/{kind}",
                                query=shorter).get("results") or []
                if not res:
                    continue
                same = [r for r in res
                        if str((r.get("release_date") or
                                r.get("first_air_date") or ""))[:4] == str(row["year"])]
                if same:
                    res = same
                    break
                res = []                         # the wrong film is worse than none
        if not res:
            return
        self.apply_match(con, row, kind,
                         self._pick_match(res, row["title"], row["year"])["id"])

    def apply_match(self, con, row, kind, tmdb_id):
        """Write one TMDB entry over an item: what it is, what it looks like, its parts.

        Separate from choosing it, because choosing badly is the common failure and
        the cure is to choose again rather than to scan again.
        """
        detail = self.tmdb(f"/{kind}/{tmdb_id}", append_to_response="external_ids")
        ext = detail.get("external_ids") or {}
        title = detail.get("title") or detail.get("name") or row["title"]
        date = detail.get("release_date") or detail.get("first_air_date") or ""
        con.execute("""UPDATE item SET title=?, sort_title=?, year=?, tmdb_id=?, imdb_id=?,
                       overview=?, rating=?, genres=?, runtime=?, poster=?, backdrop=?,
                       identified=1
                       WHERE id=?""",
                    (title,
                     re.sub(r"^(the|a|an)\s+", "", title.lower()).strip(),
                     int(date[:4]) if date[:4].isdigit() else row["year"],
                     detail.get("id"), ext.get("imdb_id"),
                     detail.get("overview"), detail.get("vote_average"),
                     ",".join(g["name"] for g in detail.get("genres", [])),
                     detail.get("runtime") or (detail.get("episode_run_time") or [None])[0],
                     detail.get("poster_path"), detail.get("backdrop_path"),
                     row["id"]))
        if kind == "tv":
            self._identify_episodes(con, row["id"], detail)
        con.commit()
        return title

    def candidates(self, item_id, term=""):
        """What this could be instead: TMDB's answers, for somebody to choose from."""
        con = self.db()
        try:
            row = con.execute("SELECT * FROM item WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return []
            kind = "movie" if row["type"] == "movie" else "tv"
            res = self.tmdb("/search/%s" % kind,
                            query=(term or row["title"])).get("results") or []
            out = []
            for r in res[:12]:
                date = r.get("release_date") or r.get("first_air_date") or ""
                out.append({
                    "id": r.get("id"),
                    "title": r.get("title") or r.get("name"),
                    "year": date[:4],
                    "overview": (r.get("overview") or "")[:300],
                    "poster": r.get("poster_path"),
                    "current": r.get("id") == row["tmdb_id"],
                })
            return out
        finally:
            con.close()

    def rematch(self, item_id, tmdb_id):
        """Say what this actually is, and rewrite it from that entry."""
        con = self.db()
        try:
            row = con.execute("SELECT * FROM item WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return None
            kind = "movie" if row["type"] == "movie" else "tv"
            # nothing to clean up: artwork is cached under TMDB's own name for the
            # picture, so a new match simply points at a different file
            return self.apply_match(con, row, kind, int(tmdb_id))
        finally:
            con.close()

    def _identify_episodes(self, con, item_id, detail):
        seasons = {e["season"] for e in
                   con.execute("SELECT DISTINCT season FROM episode WHERE item_id=?",
                               (item_id,)).fetchall()}
        for season in sorted(seasons):
            try:
                data = self.tmdb(f"/tv/{detail['id']}/season/{season}")
            except Exception:
                continue
            def write_titles():
                for ep in data.get("episodes", []):
                    con.execute("""UPDATE episode SET title=?, overview=?, aired=?,
                                   still=? WHERE item_id=? AND season=? AND number=?""",
                                (ep.get("name"), ep.get("overview"), ep.get("air_date"),
                                 ep.get("still_path"), item_id, season,
                                 ep.get("episode_number")))
            write_titles()
            # Now that the titles are known, check the files are on the right numbers:
            # a release that counts a double episode as two is one ahead of the record
            # for everything after it. If anything moved, the titles are written again,
            # since they are filed by number.
            try:
                # unless somebody has said how this season is to be numbered: a choice
                # the scanner overrules on the next scan is not a choice
                if self.numbering_choice(item_id, season) == "auto" and \
                        self._align_season(con, item_id, season):
                    write_titles()
            except Exception:
                pass

    def numbering_choice(self, item_id, season):
        """What was chosen for this season: auto, files, or database."""
        try:
            with open(os.path.join(self.root, "settings.json"), encoding="utf-8") as f:
                said = json.loads(f.read()).get("numbering") or {}
        except Exception:
            return "auto"
        return str(said.get("%s-s%s" % (item_id, season), "auto"))

    def seasons_shifted(self):
        """Every season where the files number the episodes differently.

        One row per season: what it is, how many files say a number at all, and how
        far they are from the numbers the episodes have. A season nobody has to think
        about does not appear.
        """
        con = self.db()
        try:
            rows = con.execute(
                """SELECT i.id item, i.title show, e.season, e.number, e.title episode,
                          f.path
                   FROM file f JOIN episode e ON e.id=f.episode_id
                   JOIN item i ON i.id=e.item_id
                   ORDER BY i.title, e.season, e.number""").fetchall()
        finally:
            con.close()
        seasons = {}
        for r in rows:
            said = file_episode(r["path"], r["season"])
            if said is None:
                continue
            key = (r["item"], r["show"], r["season"])
            seen = seasons.setdefault(key, {})
            seen[said - r["number"]] = seen.get(said - r["number"], 0) + 1
        out = []
        for (item, show, season), counts in seasons.items():
            by, files = max(counts.items(), key=lambda kv: kv[1])
            if not by:
                continue                   # the file and the episode agree
            out.append({"key": str(item), "show": show, "season": season,
                        "shift": by, "files": files,
                        "total": sum(counts.values())})
        out.sort(key=lambda r: (r["show"] or "", r["season"]))
        return out

    def renumber(self, item_id, season, mode):
        """Put a season on the numbers somebody has chosen.

        "files" takes the number out of each filename. "auto" hands it back to the
        scanner, which decides by title - and to decide by title it needs the title
        the database gives each number, so those are fetched again first. Without
        that step nothing can be undone: after following the files every row's title
        has moved with its number, and the two then agree at every shift.
        """
        con = self.db()
        try:
            rows = con.execute(
                """SELECT f.path, e.id eid, e.number FROM file f
                   JOIN episode e ON e.id=f.episode_id
                   WHERE e.item_id=? AND e.season=? ORDER BY e.number""",
                (int(item_id), int(season))).fetchall()
            if mode == "auto":
                said = con.execute("SELECT tmdb_id FROM item WHERE id=?",
                                   (int(item_id),)).fetchone()
                data = {}
                if said and said["tmdb_id"]:
                    try:
                        data = self.tmdb("/tv/%s/season/%s"
                                         % (said["tmdb_id"], int(season)))
                    except Exception:
                        data = {}

                def write_titles():
                    for ep in data.get("episodes", []):
                        con.execute(
                            """UPDATE episode SET title=?, overview=?, aired=?, still=?
                               WHERE item_id=? AND season=? AND number=?""",
                            (ep.get("name"), ep.get("overview"), ep.get("air_date"),
                             ep.get("still_path"), int(item_id), int(season),
                             ep.get("episode_number")))

                write_titles()
                moved = self._align_season(con, int(item_id), int(season))
                if moved:
                    write_titles()
                con.commit()
                return moved
            want = {}
            for r in rows:
                said = file_episode(r["path"], season)
                if said is not None:
                    want[r["eid"]] = said
            moved = 0
            # in the direction that empties each number before it is asked for
            going_up = any(want.get(r["eid"], r["number"]) > r["number"] for r in rows)
            for r in sorted(rows, key=lambda x: -x["number"] if going_up else x["number"]):
                target = want.get(r["eid"])
                if not target or target < 1 or target == r["number"]:
                    continue
                taken = con.execute(
                    "SELECT id FROM episode WHERE item_id=? AND season=? AND number=? "
                    "AND id<>?", (int(item_id), int(season), target, r["eid"])).fetchone()
                if taken:
                    continue
                con.execute("UPDATE episode SET number=? WHERE id=?", (target, r["eid"]))
                moved += 1
            con.commit()
            return moved
        finally:
            con.close()

    def _align_season(self, con, item_id, season):
        """Put a season's files on the numbers the episodes actually have.

        A release that counts a double episode as two - "S04E01-02 the double episode" - is one
        ahead of the record for the rest of the season: its E03 is the programme's E02.
        Every later episode then carries the title, the plot and the subtitle search of
        the one after it, which is the sort of fault nobody suspects the scanner of.

        The filenames carry the episode's own title, so they can be asked. A shift is
        applied only when it plainly reads better across the season than no shift at
        all - four or more files agreeing, and better than what they have now.
        """
        rows = con.execute("""SELECT f.id fid, f.path, e.id eid, e.number, e.title
                              FROM file f JOIN episode e ON e.id=f.episode_id
                              WHERE e.item_id=? AND e.season=? ORDER BY e.number""",
                           (item_id, season)).fetchall()
        if len(rows) < 5:
            return 0
        named = {r["number"]: (r["title"] or "") for r in con.execute(
            "SELECT number, title FROM episode WHERE item_id=? AND season=?",
            (item_id, season))}
        if sum(1 for t in named.values() if t) < 5:
            return 0                      # nothing identified: nothing to align to

        def score(shift):
            hits = 0
            for r in rows:
                said = title_in_name(r["path"])
                if not said:
                    continue
                want = named.get(r["number"] - shift)
                if want and same_title(said, want):
                    hits += 1
            return hits

        now = score(0)
        best, by = now, 0
        for shift in (1, 2, -1):
            hits = score(shift)
            if hits > best:
                best, by = hits, shift
        if not by or best < 4 or best <= now:
            return 0
        # Renumber the episodes themselves rather than moving files between them: the
        # row is what everything else points at - where somebody had got to, what is
        # marked, what has been watched - and that history belongs to the file, not to
        # the number it was filed under.
        moved = 0
        order = sorted(rows, key=lambda r: r["number"] if by > 0 else -r["number"])
        for r in order:
            want = r["number"] - by
            if want < 1:
                continue
            taken = con.execute("SELECT id FROM episode WHERE item_id=? AND season=? "
                                "AND number=? AND id<>?",
                                (item_id, season, want, r["eid"])).fetchone()
            if taken:
                continue                  # something is already there: leave both alone
            con.execute("UPDATE episode SET number=? WHERE id=?", (want, r["eid"]))
            moved += 1
        return moved

    def reparse(self):
        """Re-read every filename without re-probing or re-downloading anything.

        Needed when the parser itself improves: the files have not changed, so a normal
        scan skips them, and the wrong titles would stay wrong forever.
        """
        cfg = self.config()
        # the same folder rules the scanner uses: re-reading a filename must not change
        # what kind of thing it is, and "movie" for everything would do exactly that
        folders = ([(f, "movie") for f in cfg.get("movies", [])] +
                   [(f, "show") for f in cfg.get("tv", [])] +
                   [(f, "mixed") for f in cfg.get("mixed", [])])
        con = self.db()
        rows = con.execute("SELECT id, path FROM file").fetchall()
        for row in rows:
            st = type("st", (), {"st_size": 0, "st_mtime": 0})
            cur = con.execute("SELECT size, mtime FROM file WHERE id=?", (row["id"],)).fetchone()
            st.st_size, st.st_mtime = cur["size"], cur["mtime"]
            kind = self.kind_for(row["path"], folders) or "mixed"
            self._index_file(con, kind, row["path"], st, reset_probe=False)
        con.execute("DELETE FROM episode WHERE item_id NOT IN (SELECT id FROM item)")
        con.execute("DELETE FROM item WHERE id NOT IN (SELECT DISTINCT item_id FROM file)")
        # anything whose title changed deserves another go at identification
        con.execute("UPDATE item SET identified=0 WHERE tmdb_id IS NULL")
        # episodes orphaned by a re-split show would otherwise be counted twice
        con.execute("""DELETE FROM episode WHERE id NOT IN
                       (SELECT episode_id FROM file WHERE episode_id IS NOT NULL)""")
        con.commit()
        con.close()
        return len(rows)

    def merge_duplicates(self):
        """Fold together items that turned out to be the same title.

        Two spellings of a filename ("Hyori's Bed & Breakfast" and "Hyoris Bed and
        Breakfast") make two items during scanning; identification then resolves both
        to one TMDB id. Whichever came first keeps its id, everything else is moved
        onto it - files, episodes and progress alike.
        """
        con = self.db()
        merged = 0
        rows = con.execute("""SELECT type, tmdb_id, GROUP_CONCAT(id) ids, COUNT(*) c
                              FROM item WHERE tmdb_id IS NOT NULL
                              GROUP BY type, tmdb_id HAVING c > 1""").fetchall()
        for row in rows:
            ids = sorted(int(x) for x in row["ids"].split(","))
            keep, rest = ids[0], ids[1:]
            for other in rest:
                # episodes first: the same season/number may exist on both sides
                for ep in con.execute("SELECT * FROM episode WHERE item_id=?", (other,)).fetchall():
                    existing = con.execute("""SELECT id FROM episode WHERE item_id=? AND
                                              season=? AND number=?""",
                                           (keep, ep["season"], ep["number"])).fetchone()
                    if existing:
                        con.execute("UPDATE file SET episode_id=?, item_id=? WHERE episode_id=?",
                                    (existing["id"], keep, ep["id"]))
                        con.execute("DELETE FROM episode WHERE id=?", (ep["id"],))
                    else:
                        con.execute("UPDATE episode SET item_id=? WHERE id=?", (keep, ep["id"]))
                con.execute("UPDATE file SET item_id=? WHERE item_id=?", (keep, other))
                con.execute("DELETE FROM item WHERE id=?", (other,))
                merged += 1
        con.commit()
        con.close()
        return merged

    # ---- artwork ------------------------------------------------------------
    def artwork(self, tmdb_path, size="w500"):
        """Fetch a TMDB image once and keep it; returns a local file path."""
        if not tmdb_path:
            return None
        name = size + "_" + tmdb_path.strip("/").replace("/", "_")
        local = os.path.join(self.root, "cache", name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        url = f"https://image.tmdb.org/t/p/{size}{tmdb_path}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            with open(local, "wb") as f:
                f.write(data)
            return local
        except Exception:
            return None

    # ---- reading ------------------------------------------------------------
    def stats(self):
        con = self.db()
        out = {}
        for key, sql in (("movies", "SELECT COUNT(*) c FROM item WHERE type='movie'"),
                         ("shows", "SELECT COUNT(*) c FROM item WHERE type='show'"),
                         ("episodes", "SELECT COUNT(*) c FROM episode"),
                         ("files", "SELECT COUNT(*) c FROM file"),
                         ("identified", "SELECT COUNT(*) c FROM item WHERE tmdb_id IS NOT NULL"),
                         ("probed", "SELECT COUNT(*) c FROM file WHERE probed=1")):
            out[key] = con.execute(sql).fetchone()["c"]
        con.close()
        out["scan"] = self.scan_state
        return out
