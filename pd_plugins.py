"""The optional parts, each a module of its own: delete one and it is gone, nothing else
needs changing. A plugin may offer any of:

  rows(owner)                         front-page rows: [{"id", "title", "path", "groups"}]
  library(path, q, owner)             the items of a path it owns, None for any other
  art(path, width)                    JPEG bytes of a picture it draws, None for any other
  start()                             work of its own behind the server
  settings_get(owner)                 entries for GET /settings
  settings_post(body, owner, stored, write)   (status, reply), None when not its business
"""
PLUGINS = []

try:
    import pd_iptv          # Live TV from the owner's own IPTV playlist
    PLUGINS.append(pd_iptv)
except ImportError:
    pass


def rows(owner):
    out = []
    for plug in PLUGINS:
        if hasattr(plug, "rows"):
            out += plug.rows(owner)
    return out


def library(path, q, owner):
    for plug in PLUGINS:
        if hasattr(plug, "library"):
            got = plug.library(path, q, owner)
            if got is not None:
                return got
    return None


def art(path, width):
    """A picture a plugin draws for its own cards; None when no plugin has it."""
    for plug in PLUGINS:
        if hasattr(plug, "art"):
            got = plug.art(path, width)
            if got is not None:
                return got
    return None


def start():
    """Anything a plugin runs behind the server, started with it."""
    for plug in PLUGINS:
        if hasattr(plug, "start"):
            try:
                plug.start()
            except Exception:
                pass


def settings_get(owner):
    out = {}
    for plug in PLUGINS:
        if hasattr(plug, "settings_get"):
            out.update(plug.settings_get(owner))
    return out


def settings_post(body, owner, stored, write):
    for plug in PLUGINS:
        if hasattr(plug, "settings_post"):
            got = plug.settings_post(body, owner, stored, write)
            if got is not None:
                return got
    return None
