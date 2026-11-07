"""The words a card or a list shows, decided once here and sent with it, so the web page
and the app say the same and are not each taught the rule.

  band(card, today)    the ribbon of a title not out yet: "15 Dec", "Oct 2027" or "Upcoming"
  act(card)            a Start slide's first button: (word, plays)
  out_line(date)       a film not out yet, where Play would be: "Out 15 Dec 2026"
  version_tag(row)     where a version in the download list comes from
"""
import datetime

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(text):
    try:
        return datetime.date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def band(card, today):
    """A card not out yet wears its date: within most of a year "15 Dec", beyond it the
    month and year; Upcoming without a date to come."""
    when = _day(card.get("airDate") or card.get("originallyAvailableAt"))
    if when is None or when <= today:
        return "Upcoming"
    days = (when - today).days
    return "%s %d" % (MONTHS[when.month - 1], when.year) if days > 330 else "%d %s" % (when.day, MONTHS[when.month - 1])


def out_line(date):
    """Where Play would be on a film not out yet: "Out 15 Dec 2026", or "Not out yet"."""
    when = _day(date)
    return "Out %d %s %d" % (when.day, MONTHS[when.month - 1], when.year) if when else "Not out yet"


def act(card):
    """A Start slide's first button: its word, and whether it plays the film - only one
    that is here does; one not out yet says when."""
    if card.get("upcoming"):
        return out_line(card.get("originallyAvailableAt")), False
    # in a pack here, or carried by the tracker: fetched, not asked for (2026-11-06)
    if card.get("offered") or card.get("onTracker"):
        return "⤓ Get it", False
    if card.get("askable") or card.get("addable"):
        return "Request", False
    return ("▶ Play", True) if card.get("type") == "movie" else ("▶ Watch", False)


def version_tag(row):
    """Where a version in the download list comes from; one this house holds says On disk."""
    if row.get("disk") or row.get("have"):
        return "On disk"
    if row.get("pack"):
        return "In a pack here"
    if row.get("box"):
        return "Season pack" if row.get("seasonPack") else "Box set"
    return "Tracker"


def dress(body, today=None):
    """Every card in an answer given its words: a title not out yet its band, every one
    a Start slide could show its act. The answer itself, changed in place."""
    today = today or datetime.date.today()
    lists = []
    if isinstance(body, dict):
        lists.append(body.get("Metadata"))
        for hub in body.get("Hub") or []:
            if isinstance(hub, dict):
                lists.append(hub.get("Metadata"))
    for cards in lists:
        for card in cards or []:
            if not isinstance(card, dict):
                continue
            if card.get("upcoming"):
                card["band"] = band(card, today)
            if card.get("type") in ("movie", "show"):
                word, plays = act(card)
                card["act"] = {"word": word, "plays": plays}
    return body
