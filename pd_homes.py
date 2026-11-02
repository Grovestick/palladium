"""Sync back: the films of packs whose home is the copy server, held on this server only
while the copy rules want them.

The copy server keeps every film of such a pack and seeds it day and night. This server
takes a film back from it - over the house network, into its own copy of the pack, so a
film it holds can be read off both machines at once - when somebody is watching it, part
way through it, has it on a watchlist or the shuffle, watched it within the days kept, or
asked for it. It lets the film go again when none of that is true, and only once the copy
server says it holds the film whole. What is decided is here; pd-server fetches, and
pd_torrents.let_go switches a film off and removes the file.

One handler for both ways a film comes back: what the copy server reports it downloaded
while this one was away (take_back) and what the rules round finds (decide) go through the
same decision by home and into the same fetch queue.
"""


def take_back(items, home_of, tmdb_of, wanted, sync_back_on):
    """Which of the films the copy server holds and this one lacks come back here: each by
    its pack's home. Home here or on both - the home holds every file, so all of them. Home
    on the copy - only with Sync back on, and only those the copy rules want (wanted, TMDB
    numbers). items are the copy's reports, {hash, index, ...}; home_of(hash) and
    tmdb_of(hash, index) look them up here. Returns the items to fetch."""
    out = []
    for it in items:
        home = home_of(it.get("hash"))
        if home in ("main", "both"):
            out.append(it)
        elif home == "copy" and sync_back_on and tmdb_of(it.get("hash"), it.get("index")) in wanted:
            out.append(it)
    return out


def give(films, here, copy_whole, listed=()):
    """The films this server holds whole that the copy server, their pack's home, does not,
    and that are not listed for it already (listed: keys): each moved to it, one file at a
    time, by its mover. films are the (pack, film) pairs of packs at home on the copy or on
    both."""
    listed = set(listed)
    return [(pack, film) for pack, film in films
            if film.get("key") not in listed and here(pack, film) and not copy_whole(pack, film)]


def decide(films, wanted, here, copy_whole, playing=(), always=()):
    """What to take back and what to let go, as (fetch, drop) lists of (pack, film).

    films are the (pack, film) pairs of packs living on the copy server; wanted and
    playing are sets of TMDB numbers; here(pack, film) is whether this server holds the
    film whole, copy_whole(pack, film) whether the copy server does. A film is taken
    back only from a copy that has it, and let go only when the copy has it - and never
    while it is on a screen, whatever the lists say. A pack whose hash is in always is
    at home on both machines: every file of it is wanted here and none is let go.
    """
    fetch, drop = [], []
    for pack, film in films:
        tmdb = int(film.get("tmdb") or 0)
        want = pack.get("hash") in always or (bool(tmdb) and tmdb in wanted)
        on = here(pack, film)
        if want and not on:
            if copy_whole(pack, film):
                fetch.append((pack, film))
        elif on and not want and tmdb not in playing and pack.get("hash") not in always:
            if copy_whole(pack, film):
                drop.append((pack, film))
    return fetch, drop
