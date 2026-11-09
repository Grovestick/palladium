"""A shuffle's round: the whole shelf drawn in advance, and the shuffle's own silent progress
through it (owner, 2026-11-08: "a round that is predrawn, and a silent progress - if Next is
pressed that episode is marked watched for the shuffle, for that round").

One round per viewer and shelf, kept in the viewer's settings:

    order   every title of the shelf, in the order drawn when the round began
    pos     the place in it: the title the round is on, -1 before the first
    done    that title, once it is finished - played to its end or stepped past with Next
    at      {title: {"at": seconds, "when": time}} - where the title it is on was left
    run     which round this is
    next    the order of the round after this one, drawn once when the end is in sight,
            so a look at what comes next and the move onto it agree

Nothing here touches the library: what the shuffle has played is not marked watched, and it
puts nothing on Continue watching. Everything is a plain function over that dict, so the rules
can be tested without a server.
"""
import random
import time

#: a title is followed onto when the player reports it this early in: the next one starting
FOLLOW_S = 120


def finished(position, duration, key=None):
    """Whether a playing got to its end: the library's watched-through line, the credits where
    the end finder has found them."""
    from pd_localapi import LocalAPI
    return bool(LocalAPI.watched_through(position, duration, key))


def fresh(one=None):
    return {"order": [], "pos": -1, "done": "", "at": {}, "run": 1, "next": []}


def current(one):
    """The title the round is on, "" before the first or once it is done."""
    order, pos = one.get("order") or [], int(one.get("pos") if one.get("pos") is not None else -1)
    on = order[pos] if 0 <= pos < len(order) else ""
    return "" if not on or on == str(one.get("done") or "") else on


def place(one, key):
    """Where `key` was left in this round, in seconds; 0 for none worth resuming."""
    value = (one.get("at") or {}).get(str(key))
    at = int(value.get("at") or 0) if isinstance(value, dict) else int(value or 0)
    return at if at > 30 else 0


def ahead(one, usable, start):
    """The index of the first title after `start` that can be played, or None."""
    order = one.get("order") or []
    return next((j for j in range(start + 1, len(order)) if usable(order[j])), None)


def behind(one, usable, start):
    order = one.get("order") or []
    return next((j for j in range(start - 1, -1, -1) if usable(order[j])), None)


def draw_order(pool, last=(), rng=random):
    """A new round's order: the whole shelf shuffled, each title once."""
    order = list(dict.fromkeys(str(k) for k in pool))
    rng.shuffle(order)
    return order


def peek(one, usable, pool, rng=random, copy=False):
    """What Next would play, changing nothing that a move would not keep: at the end of the
    order the next round is drawn once and kept, so the move plays the same title."""
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    to = ahead(one, usable, pos)
    if to is not None:
        return (one.get("order") or [])[to]
    if copy:
        return next((k for k in one.get("order") or [] if usable(k)), "")
    if not one.get("next"):
        one["next"] = draw_order(pool, one.get("order") or [], rng)
    return next((k for k in one["next"] if usable(k)), "")


def forward(one, usable, pool, left_from="", rng=random, copy=False):
    """Next, or the title playing to its end: the one left is done for this round and the round
    moves on. A request about a title the round has already moved past - a second press, a
    screen behind the times - moves nothing. Returns (key, started_over)."""
    order = one.get("order") or []
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    on = order[pos] if 0 <= pos < len(order) else ""
    left_from = str(left_from or "")
    if left_from and left_from != on:
        at = order.index(left_from) if left_from in order else -1
        if not 0 < at - pos <= 3:
            # a title the round has moved past, one from the round before, or none of its
            # own: a second press or a screen behind the times - nothing moves
            return (current(one) or on), False
        pos = at                                          # it ran ahead by itself: from there
    to = ahead(one, usable, pos)
    # the one left keeps no place; the one moved onto keeps its own, started elsewhere
    one["at"] = {k: v for k, v in (one.get("at") or {}).items()
                 if to is not None and k == order[to]}
    if to is not None:
        one["pos"], one["done"] = to, ""
        return order[to], False
    if copy:
        # a machine keeping copies holds part of the shelf and does not end a round the
        # main server keeps: the earliest title here plays, the place stays
        again = next((k for k in order if usable(k)), "")
        return again, False
    new = one.get("next") or draw_order(pool, order, rng)
    one.update({"order": list(new), "pos": -1, "done": "", "next": [],
                "run": int(one.get("run") or 1) + 1})
    to = ahead(one, usable, -1)
    if to is None:
        return "", True
    one["pos"] = to
    return one["order"][to], True


def back(one, usable):
    """The title before the one the round is on, from its start. None at the first."""
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    to = behind(one, usable, pos)
    if to is None:
        return None
    one["pos"], one["done"], one["at"] = to, "", {}
    return one["order"][to]


def carry_on(one, usable, pool, seen=None, rng=random, copy=False):
    """Carrying on: the title the round is on, where it was left - or the next one, when that
    title is finished. `seen(key)` gives (position, duration) of the last playing of a title,
    from the watch log, which hears the end of a playing that the round may not have.
    Returns (key, resume_at, started_over)."""
    on = current(one)
    if on and seen:
        got = seen(on)
        if got and finished(got[0], got[1], on):
            one["done"] = on
            on = ""
    if on and usable(on):
        at = place(one, on)
        if seen and not at:
            got = seen(on)
            if got and int(got[0] or 0) > 30:
                at = int(got[0])
        return on, at, False
    key, over = forward(one, usable, pool, rng=rng, copy=copy)
    at = place(one, key) if key else 0
    if key and seen and not at:
        # the next one, where its own progress has it - unless that was watched through
        got = seen(key)
        if got and int(got[0] or 0) > 30 and not finished(got[0], got[1], key):
            at = int(got[0])
    return key, at, over


def follow(one, key, position, duration, now=None):
    """A report from the player: where the title being played has got to. The title the round
    is on keeps its place, or is done when it reached its end. A title a little further on that
    is just starting - the player rolling on by itself - moves the round there. Anything else
    moves nothing: the goodbye of a title stepped back from must not carry the round forward.
    True when anything changed."""
    order = one.get("order") or []
    key = str(key)
    if key not in order:
        return False
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    at = order.index(key)
    if at > pos:
        if at - pos > 3 or float(position or 0) > FOLLOW_S:
            return False
        one["pos"], one["done"], one["at"] = at, "", {}
        pos = at
    if at != pos:
        return False
    before = (one.get("done"), dict(one.get("at") or {}))
    if finished(position, duration, key):
        one["done"], one["at"] = key, {}
    elif float(position or 0) < 30:
        one["at"] = {}
    else:
        one["at"] = {key: {"at": int(position), "when": int(now if now is not None else time.time())}}
    return (one.get("done"), one.get("at")) != before or at != pos


def settle(one, pool, rng=random):
    """A round made whole for the shelf as it is now: each title once, the place kept on the
    title it is on, titles new to the shelf put in at random places after the next one (that one
    is promised), and a round with no order drawn. Titles taken off the shelf stay in the order
    and are stepped over."""
    order = [str(k) for k in one.get("order") or []]
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    if 0 <= pos < len(order):
        on = order[pos]
        kept, seen = [], set()
        for i, k in enumerate(order):
            if i == pos:
                pos = len(kept)
                kept.append(k)
                seen.add(k)
            elif k != on and k not in seen:
                seen.add(k)
                kept.append(k)
        order = kept
    else:
        order = list(dict.fromkeys(order))
        pos = -1 if not order else min(max(pos, -1), len(order) - 1)
    if not order:
        order, pos = draw_order(pool, (), rng), -1
    held = set(order)
    for k in (str(k) for k in pool):
        if k not in held:
            order.insert(rng.randint(min(pos + 2, len(order)), len(order)), k)
            held.add(k)
    one["order"], one["pos"] = order, pos
    one.setdefault("at", {})
    one.setdefault("done", "")
    one.setdefault("run", 1)
    one.setdefault("next", [])
    if one.get("next"):
        # the next round's order kept whole for the shelf as well
        nxt = [k for k in dict.fromkeys(str(k) for k in one["next"])]
        nxt += [k for k in (str(k) for k in pool) if k not in set(nxt)]
        one["next"] = nxt
    return one


def derived(one, inside=None, deep=10):
    """What older readers look at: what the round has played, up to the title it is on, and the
    titles still to come that are on the shelf."""
    order = one.get("order") or []
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    one["played"] = list(order[:pos + 1])
    rest = order[pos + 1:]
    if inside is not None:
        rest = [k for k in rest if k in inside]
    one["queue"] = list(rest[:deep])
    return one


def left(one, pool):
    """How many titles of the shelf the round has still to come to."""
    pos = int(one.get("pos") if one.get("pos") is not None else -1)
    rest = set((one.get("order") or [])[pos + 1:])
    return len([k for k in pool if str(k) in rest])
