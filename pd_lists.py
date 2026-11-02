"""Lists to show: a name and titles in a set order, kept in the library's database.

A list is filled from wherever it comes from - IMDb's top 250 from its chart - and read
back as cards: what this house holds, what a pack carries, and what can only be asked
for, in the list's own order. The catalogue's details of a title not held (its poster,
its number there) are looked up once, behind the page, and kept with the list.
"""
import threading
import time

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS list (id TEXT PRIMARY KEY, title TEXT, source TEXT, "
    "updated INTEGER)",
    "CREATE TABLE IF NOT EXISTS list_item (list_id TEXT, rank INTEGER, imdb TEXT, "
    "title TEXT, year INTEGER, tmdb INTEGER, poster TEXT, backdrop TEXT, overview TEXT, "
    "rating REAL, released TEXT, PRIMARY KEY (list_id, rank))",
)

#: the lists there are: id -> (title, where it comes from)
KNOWN = {"imdb-top": ("IMDb Top 250", "imdb")}

_RESOLVING = set()
_LOCK = threading.Lock()


def ensure(con):
    for sql in SCHEMA:
        con.execute(sql)


def put(con, list_id, title, source, rows):
    """The list as it stands now: `rows` are (imdb, title, year), first first. What was
    looked up for a title already on it is kept."""
    ensure(con)
    kept = {r[0]: r[1:] for r in con.execute(
        "SELECT imdb, tmdb, poster, backdrop, overview, rating, released FROM list_item "
        "WHERE list_id=?", (list_id,)).fetchall()}
    con.execute("DELETE FROM list_item WHERE list_id=?", (list_id,))
    for rank, (imdb, name, year) in enumerate(rows):
        was = kept.get(imdb) or (None, None, None, None, None, None)
        con.execute("INSERT INTO list_item VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (list_id, rank, imdb, name, int(year or 0)) + tuple(was))
    con.execute("INSERT OR REPLACE INTO list VALUES (?,?,?,?)",
                (list_id, title, source, int(time.time())))
    con.commit()


def items(con, list_id):
    ensure(con)
    return con.execute("SELECT * FROM list_item WHERE list_id=? ORDER BY rank",
                       (list_id,)).fetchall()


def updated(con, list_id):
    ensure(con)
    row = con.execute("SELECT updated FROM list WHERE id=?", (list_id,)).fetchone()
    return int(row[0] or 0) if row else 0


def resolve_behind(lib, list_id):
    """The catalogue's details of the titles on a list not looked up yet, asked behind
    the page: one at a time, and once a list at a time."""
    with _LOCK:
        if list_id in _RESOLVING:
            return
        _RESOLVING.add(list_id)

    def work():
        try:
            con = lib.db()
            try:
                todo = con.execute("SELECT rank, imdb FROM list_item WHERE list_id=? AND "
                                   "tmdb IS NULL AND imdb LIKE 'tt%'", (list_id,)).fetchall()
                for rank, imdb in todo:
                    try:
                        got = (lib.tmdb("/find/" + imdb, external_source="imdb_id") or {})
                    except Exception:
                        break                 # no catalogue now: asked again next time
                    one = (got.get("movie_results") or [{}])[0]
                    con.execute("UPDATE list_item SET tmdb=?, poster=?, backdrop=?, overview=?, "
                                "rating=?, released=? WHERE list_id=? AND rank=?",
                                (int(one.get("id") or 0), one.get("poster_path"),
                                 one.get("backdrop_path"), one.get("overview") or "",
                                 one.get("vote_average"), one.get("release_date") or "",
                                 list_id, rank))
                    con.commit()
                    time.sleep(0.25)
            finally:
                con.close()
        finally:
            with _LOCK:
                _RESOLVING.discard(list_id)
    threading.Thread(target=work, daemon=True, name="list-" + list_id).start()
