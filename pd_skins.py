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
    # House, the standard, and five more, each after a kind of streaming service and named for itself.
    # `corner` scales every rounded corner (1 is the app's own), `titles` is the face
    # shelf titles are set in.
    "house": {
        "name": "House",
        "why": "the way it has always looked, the standard",
        "bg": "#0b0d10", "panel": "#161a20", "panel2": "#1e242c",
        "line": "#2a323c", "fg": "#e8ecf1", "dim": "#93a0b0",
        # the accent is the house's own, kept in settings; "" means leave it alone
        "accent": "",
        "corner": 1.0, "titles": "sans",
    },
    "crimson": {
        "name": "Crimson",
        "why": "black and red, sharp corners",
        "bg": "#0b0b0c", "panel": "#161617", "panel2": "#202022",
        "line": "#2e2e31", "fg": "#f5f5f1", "dim": "#a3a3a3",
        "accent": "#e50914",
        "corner": 0.4, "titles": "sans",
        "glow": [[50, -12, 1200, "rgba(229,9,20,.10)"]],
    },
    "harbour": {
        "name": "Harbour",
        "why": "deep navy and bright blue, soft corners",
        "bg": "#0f1923", "panel": "#1a2633", "panel2": "#223142",
        "line": "#2f4153", "fg": "#f2f6fa", "dim": "#8fa3b8",
        "accent": "#1aa9e8",
        "corner": 1.6, "titles": "sans",
        "glow": [[85, -10, 1100, "rgba(26,169,232,.12)"],
                 [10, 110, 900, "rgba(80,120,255,.08)"]],
    },
    "graphite": {
        "name": "Graphite",
        "why": "black and white, calm, your own accent colour",
        "bg": "#000000", "panel": "#141414", "panel2": "#1d1d1f",
        "line": "#2c2c2e", "fg": "#f5f5f7", "dim": "#9a9a9f",
        # the house's own accent, kept in settings: this skin brings none of its own
        "accent": "",
        "corner": 1.2, "titles": "sans",
    },
    "violet": {
        "name": "Violet",
        "why": "dark purple and violet",
        "bg": "#0a0716", "panel": "#161127", "panel2": "#1f1836",
        "line": "#2e2550", "fg": "#f1eefc", "dim": "#a49cc4",
        "accent": "#7b5cff",
        "corner": 1.0, "titles": "sans",
        "glow": [[20, -10, 1100, "rgba(123,92,255,.16)"],
                 [90, 105, 900, "rgba(0,130,255,.10)"]],
    },
    "archive": {
        "name": "Archive",
        "why": "warm dark, gold, serif titles - the film club",
        "bg": "#14110e", "panel": "#1d1915", "panel2": "#26211c",
        "line": "#3a332b", "fg": "#efe8dc", "dim": "#a99d8b",
        "accent": "#d9a441",
        "corner": 0.25, "titles": "serif",
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
}


#: what a screen wears when nobody has said otherwise
DEFAULT = "house"

#: the two a machine puts on by itself, and cannot be chosen instead of
IMPOSED = ("night", "arcade")


#: the ones a person chooses from, in the order they are offered; the skins before
#: these (paper, phosphor) are gone and a screen that wore one wears the default
CHOOSABLE = ("house", "crimson", "harbour", "graphite", "violet", "archive")


def known(name):
    """The skin by that name, or the default. Never raises: a screen must draw."""
    return SKINS.get(str(name or "").strip().lower()) and str(name).strip().lower() \
        or DEFAULT


def _channel(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcode):
    """WCAG relative luminance of "#rrggbb"."""
    h = str(hexcode or "").lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(one, two):
    a, b = sorted((luminance(one), luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def on(accent):
    """The text colour that reads on this accent: near-black or white, whichever has
    more contrast. Dark text on the red accent could hardly be read."""
    try:
        return "#111111" if contrast(accent, "#111111") >= contrast(accent, "#ffffff") else "#ffffff"
    except (ValueError, TypeError):
        return "#111111"


def _filled(key):
    said = dict(SKINS[key], id=key, imposed=key in IMPOSED)
    said.setdefault("corner", 1.0)
    said.setdefault("titles", "sans")
    said["onAccent"] = on(said.get("accent"))
    return said


def all_of():
    """Every skin a person may choose, in the order they should be offered."""
    return [_filled(k) for k in CHOOSABLE]


def one(name):
    """One skin, by name, with its id on it."""
    return _filled(known(name))
