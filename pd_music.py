"""Music video playlists: a name and the videos in their order, kept in music_playlists.json.

Each one is a card on Start's Music videos row and plays as the shuffle shelf
"music:<id>", in its order, round and round. A file that cannot be read is not an
empty list: nothing is written over it until it reads again.
"""
import json
import os
import shutil
import threading
import time
import uuid

STATE = {"root": "", "broken": False}
LOCK = threading.Lock()


def use_data_dir(root):
    STATE["root"] = root


def _path():
    return os.path.join(STATE["root"] or ".", "music_playlists.json")


def load():
    """Every playlist, [{"id", "name", "keys"}]; None when the file is there but unreadable."""
    try:
        with open(_path(), encoding="utf-8") as f:
            said = json.load(f)
    except FileNotFoundError:
        STATE["broken"] = False
        return []
    except (OSError, ValueError):
        STATE["broken"] = True
        return None
    STATE["broken"] = False
    out = []
    for p in said if isinstance(said, list) else []:
        if isinstance(p, dict) and p.get("id"):
            out.append({"id": str(p["id"]), "name": str(p.get("name") or ""),
                        "keys": [str(k) for k in p.get("keys") or []]})
    return out


def _save(lists):
    path = _path()
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    with open(path + ".new", "w", encoding="utf-8") as f:
        json.dump(lists, f, ensure_ascii=False, indent=1)
    os.replace(path + ".new", path)


def find(pid):
    return next((p for p in load() or [] if p["id"] == str(pid or "")), None)


def change(do, pid="", name="", keys=None):
    """create, rename, delete or set (the videos in their order); the lists after, or {"why"}."""
    with LOCK:
        lists = load()
        if lists is None:
            return {"why": "music_playlists.json cannot be read; nothing is written over it"}
        name = str(name or "").strip()[:60]
        one = next((p for p in lists if p["id"] == str(pid or "")), None)
        if do == "create":
            if not name:
                return {"why": "a playlist needs a name"}
            one = {"id": uuid.uuid4().hex[:8], "name": name,
                   "keys": list(dict.fromkeys(str(k) for k in keys or []))}
            lists.append(one)
        elif one is None:
            return {"why": "there is no such playlist"}
        elif do == "rename":
            if not name:
                return {"why": "a playlist needs a name"}
            one["name"] = name
        elif do == "delete":
            lists.remove(one)
        elif do == "set":
            one["keys"] = list(dict.fromkeys(str(k) for k in keys or []))
        else:
            return {"why": "no such change"}
        _save(lists)
        return {"playlists": lists, "id": one["id"], "at": time.time()}
