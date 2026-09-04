#!/usr/bin/env python3
"""The looks a screen can wear, kept in one place because there are three screens.

The web client, the Android app and the settings page all draw the same library, and
until now each carried its own six colours. Adding a look meant editing three files in
two languages and hoping they agreed. They live here instead: the clients ask what the
skins are and paint themselves, so a new one is an entry in this file.

Two of them are not chosen by anybody. A machine that only keeps copies, after dark,
wears "night"; a machine whose graphics card has been handed to a game wears "arcade".
Those are facts about the machine rather than preferences, so they overrule whatever
was picked, and the client is told which is which - a look nobody chose should say so.
"""

#: Every colour a screen needs, by name. The clients know these seven and nothing else,
#: which is what keeps a new skin from needing new code on either side.
#:
#: `glow` is optional: two or three washes of colour laid over the ground, as
#: (x%, y%, radius px, rgba) - the only thing here that is not a flat colour, and what
#: keeps a dark skin from reading as a black rectangle.
SKINS = {
    "house": {
        "name": "House",
        "why": "the way it has always looked",
        "bg": "#0b0d10", "panel": "#161a20", "panel2": "#1e242c",
        "line": "#2a323c", "fg": "#e8ecf1", "dim": "#93a0b0",
        # the accent is the house's own, kept in settings; "" means leave it alone
        "accent": "",
    },
    "night": {
        "name": "Night server",
        "why": "the machine that keeps copies, after dark",
        "bg": "#07060d", "panel": "#100d1c", "panel2": "#171327",
        "line": "#241d3a", "fg": "#d6cfe6", "dim": "#7a6f96",
        "accent": "#8a6ee8",
        "glow": [[78, -8, 1200, "rgba(138,110,232,.14)"],
                 [8, 108, 900, "rgba(60,160,180,.07)"]],
    },
    "arcade": {
        "name": "Arcade",
        "why": "the card is busy",
        "bg": "#0a0620", "panel": "#171043", "panel2": "#211757",
        "line": "#3a2a8f", "fg": "#f4f2ff", "dim": "#a99cf0",
        "accent": "#00e0ff",
        "loud": True,
        "glow": [[12, -10, 1000, "rgba(255,214,0,.16)"],
                 [88, 0, 900, "rgba(0,224,255,.16)"],
                 [60, 110, 800, "rgba(255,60,180,.14)"]],
    },
    "paper": {
        "name": "Paper",
        "why": "for a room with the lights on",
        "bg": "#f2efe8", "panel": "#ffffff", "panel2": "#e9e5dc",
        "line": "#d6d0c4", "fg": "#1b1a17", "dim": "#6b665c",
        "accent": "#9a5b28",
    },
    "green": {
        "name": "Phosphor",
        "why": "one colour, and the dark",
        "bg": "#000d06", "panel": "#04180e", "panel2": "#082418",
        "line": "#0f3a24", "fg": "#9dffc6", "dim": "#3f8c62",
        "accent": "#37e07f",
        "glow": [[50, -10, 1100, "rgba(55,224,127,.10)"]],
    },
}

#: what a screen wears when nobody has said otherwise
DEFAULT = "house"

#: the two a machine puts on by itself, and cannot be chosen instead of
IMPOSED = ("night", "arcade")


def known(name):
    """The skin by that name, or the default. Never raises: a screen must draw."""
    return SKINS.get(str(name or "").strip().lower()) and str(name).strip().lower() \
        or DEFAULT


def all_of():
    """Every skin a person may choose, in the order they should be offered."""
    return [dict(SKINS[k], id=k, imposed=k in IMPOSED)
            for k in ("house", "night", "arcade", "paper", "green")]


def one(name):
    """One skin, by name, with its id on it."""
    key = known(name)
    return dict(SKINS[key], id=key, imposed=key in IMPOSED)
