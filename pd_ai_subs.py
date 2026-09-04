#!/usr/bin/env python3
"""Making a subtitle out of the soundtrack: one job at a time, in its own process.

The models want several gigabytes of graphics memory and the machine is also serving
films, so this runs one at a time and lets the process go afterwards. What it makes
is named "<film>.ai-gen.<language>.srt", which is how anybody reading the list knows
it was heard by a machine rather than written by a person.
"""
import hashlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

#: what a job looks like to whoever asks: the film, the language, how far along.
STATE = {"on": None, "queued": [], "done": [], "why": ""}
#: A lock that may be taken twice by the same thread. It was a plain one, and asking
#: for a film that was already being written deadlocked the lot: the answer to
#: "already" is the state, and reading the state takes the lock again.
LOCK = threading.RLock()
WAITING = queue.Queue()
STARTED = False

#: how many finished jobs to remember, so a client that looks away and back is told
KEEP = 12

#: the process doing the work, so it can be stopped: a film put on by mistake holds
#: the graphics card for half an hour and everything else waits behind it
RUNNING = {"proc": None}


#: The answer to "which Python can do this", kept: finding out costs a process start
#: and an import of the whole speech library, and clients ask for the state of a job
#: every few seconds. Without this the status request took a minute and everything
#: else queued up behind it.
FOUND = {"python": None, "when": 0.0}
FOUND_FOR = 600


def tool():
    """The Python that can do the work, or None.

    Chosen by looking, not by asking: starting a process and importing the speech
    library took the better part of a minute on a busy machine, and this is called
    every time a client asks how a job is getting on. If the interpreter is there but
    the library is not, the job itself says so - once, in its own message, rather than
    holding up every request in the meantime.
    """
    if FOUND["python"]:
        return FOUND["python"]
    tried = [os.environ.get("PALLADIUM_PYTHON")]
    local = os.environ.get("LOCALAPPDATA", "")
    for version in ("Python313", "Python312", "Python311"):
        tried.append(os.path.join(local, "Programs", "Python", version, "python.exe"))
    for path in tried:
        if path and os.path.exists(path):
            FOUND.update(python=path, when=time.time())
            return path
    # nothing installed where a person installs Python: fall back to the path, which
    # costs one cheap lookup rather than an import of the whole library
    found = shutil.which("python3") or shutil.which("python")
    FOUND.update(python=found, when=time.time())
    return found


def ready():
    """Whether this server can make subtitles at all."""
    return bool(tool())


#: The big pieces of machinery, and what each is for. Neither is installed with
#: Palladium: together they are eight gigabytes, and a server that never writes a
#: subtitle should not be carrying them about.
ADDONS = [
    {"id": "speech", "name": "Speech to text",
     "what": "Writes down what is said in a film, in whatever language it is spoken. "
             "This is what makes a subtitle out of the sound.",
     "folder": "faster-whisper-large-v3", "mark": "model.bin", "size": "3.1 GB"},
    {"id": "translate", "name": "Translation",
     "what": "Turns English into Swedish, a line at a time. Only needed for Swedish "
             "subtitles of a film spoken in English.",
     "folder": "nllb-200-distilled-1.3B", "mark": "tokenizer.json", "size": "5.2 GB"},
]


#: Where the scripts that run the models are published. Unlike the models and the
#: libraries - which are other people's work, fetched from whoever publishes them -
#: these are Palladium's own, and they come from Palladium. Separately from the
#: server, so a fault in one can be mended without anybody installing a new build.
TOOLS_AT = "https://palladium.video/tools/"
TOOLS_SAYS = TOOLS_AT + "tools.json"

#: What is being fetched at this moment, and what went wrong last time
TOOLS_STATE = {"getting": False, "why": ""}


def where_data():
    return os.environ.get("PALLADIUM_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "Palladium")


def where_tools():
    return os.path.join(where_data(), "tools")


def tools_here():
    """What was fetched, if anything, and when."""
    try:
        with open(os.path.join(where_tools(), "tools.json"), encoding="utf-8") as f:
            return json.loads(f.read())
    except Exception:
        return {}


def tools_there(timeout=12):
    """What the site publishes now. An empty answer is not an error worth showing:
    a machine with no way out still runs the copy it already has."""
    try:
        req = urllib.request.Request(TOOLS_SAYS, headers={"User-Agent": "palladium"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def get_tools():
    """Fetch the scripts as one zip, check it, and unpack it into the data folder.

    One file rather than a list of them: one request, one hash to check, and room to
    add a second script later without the server needing to know its name.

    Checked rather than trusted, and unpacked by hand rather than by extractall: a zip
    can name a member that climbs out of the folder, and this is a script the server
    is about to run.
    """
    if TOOLS_STATE["getting"]:
        return {"getting": True}
    said = tools_there()
    if not (said or {}).get("version"):
        return {"error": "palladium.video did not say what there is to fetch"}

    def work():
        TOOLS_STATE.update(getting=True, why="")
        folder = where_tools()
        try:
            import zipfile
            req = urllib.request.Request(TOOLS_AT + "tools.zip",
                                         headers={"User-Agent": "palladium"})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            want = str(said.get("sha256") or "")
            if want and hashlib.sha256(body).hexdigest() != want:
                raise ValueError("the file did not arrive as published")
            os.makedirs(folder, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(body)) as zip_file:
                for name in zip_file.namelist():
                    plain = os.path.basename(name)
                    if not plain.endswith(".py") or plain != name:
                        continue          # a name is a name, not a path
                    with zip_file.open(name) as inside:
                        keep = inside.read()
                    part = os.path.join(folder, plain + ".part")
                    with open(part, "wb") as f:
                        f.write(keep)
                    os.replace(part, os.path.join(folder, plain))
            with open(os.path.join(folder, "tools.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps(dict(said, when=time.strftime("%Y-%m-%d %H:%M"))))
        except Exception as why:
            TOOLS_STATE["why"] = str(why)[:200]
        finally:
            TOOLS_STATE["getting"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"getting": True}


def tools():
    """What the page shows: what is here, what is published, and what went wrong."""
    mine = tools_here()
    said = tools_there()
    return {"version": mine.get("version", ""), "when": mine.get("when", ""),
            "latest": (said or {}).get("version", ""),
            "here": bool(script_path()),
            "getting": TOOLS_STATE["getting"], "why": TOOLS_STATE["why"],
            "from": TOOLS_AT}


def script_path():
    """The maker script: the one fetched from palladium.video first, then the one the
    installer put beside the program, then whatever the working folder holds.

    The fetched one wins so that a mended script takes effect the moment it lands,
    rather than at the next install.
    """
    for where in (where_tools(),
                  os.path.dirname(os.path.abspath(sys.executable)),
                  os.path.dirname(os.path.abspath(__file__)),
                  os.getcwd()):
        found = os.path.join(where, "pd_subs_make.py")
        if os.path.exists(found):
            return found
    return ""


def where_models():
    data = os.environ.get("PALLADIUM_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "Palladium")
    return os.path.join(data, "models")


def on_disk(one):
    """How much of an add-in is on this machine: the size it takes, or 0."""
    folder = os.path.join(where_models(), one["folder"])
    if not os.path.exists(os.path.join(folder, one["mark"])):
        return 0
    total = 0
    for here, dirs, names in os.walk(folder):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
    return total


def addons(switched):
    """What each add-in is, whether it is here, and whether it is turned on."""
    out = []
    for one in ADDONS:
        got = on_disk(one)
        out.append(dict(one, here=bool(got),
                        took=round(got / 1e9, 1),
                        on=bool(switched.get(one["id"], True)),
                        getting=GETTING.get(one["id"], "")))
    return out


#: what is being fetched at this moment, so the page can say so
GETTING = {}


def get_addon(which):
    """Fetch one add-in, in its own process, without holding anything up."""
    one = next((a for a in ADDONS if a["id"] == which), None)
    if not one or GETTING.get(which):
        return {"getting": bool(one)}
    python = tool()
    if not python:
        return {"error": "no Python on this machine can fetch it"}
    script = script_path()
    if not script:
        return {"error": "the subtitle tools are not on this machine yet - "
                         "fetch them in Settings"}
    data = os.environ.get("PALLADIUM_DATA") or ""
    air = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    if data:
        air["PALLADIUM_DATA"] = data

    def work():
        GETTING[which] = "fetching"
        try:
            subprocess.run([python, "-P", script, "--fetch", which], env=air,
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            GETTING.pop(which, None)

    threading.Thread(target=work, daemon=True).start()
    return {"getting": True}


def drop_addon(which):
    """Take one off this machine again. It can always be fetched afresh."""
    one = next((a for a in ADDONS if a["id"] == which), None)
    if not one:
        return {"error": "no such add-in"}
    shutil.rmtree(os.path.join(where_models(), one["folder"]), ignore_errors=True)
    return {"gone": True}


def beside(video, language):
    """Where the made subtitle goes, named so its origin is plain."""
    return "%s.ai-gen.%s.srt" % (os.path.splitext(video)[0], language.lower()[:2])


def ask(video, language, title="", copies=(), key=""):
    """Put a film in the queue. Returns what to tell the caller.

    `copies` are the other files this library holds of the same title. A house that
    keeps a film twice has one of them playing and the other beside it, and a subtitle
    written beside the wrong one is a subtitle nobody can choose.
    """
    out = beside(video, language)
    with LOCK:
        already = (STATE["on"] and STATE["on"]["out"] == out) or any(
            j["out"] == out for j in STATE["queued"])
    if already:
        return {"already": True, "state": look()}
    with LOCK:
        job = {"video": video, "language": language.lower()[:2], "out": out,
               "copies": [beside(p, language) for p in copies
                          if os.path.normcase(p) != os.path.normcase(video)],
               # named from the start, so a client can tell whether a finished job -
               # or a failed one - has anything to do with the film it is showing
               "file": os.path.basename(out),
               # which title it belongs to, so a client can match it without
               # comparing file names it may not have
               "key": str(key),
               "title": title, "at": 0.0, "what": "waiting", "when": int(time.time())}
        STATE["queued"].append(job)
    WAITING.put(job)
    start()
    return {"started": True, "state": look()}


def stop(out=""):
    """Stop the job in hand, or take one out of the queue.

    Without a name it stops whatever is running, which is what a person pressing Stop
    means. The worker is killed with its children: it has an ffmpeg of its own reading
    the soundtrack, and that would otherwise carry on alone.
    """
    with LOCK:
        on = STATE["on"]
        waiting = [j for j in STATE["queued"] if not out or j["out"] == out]
        if out and (not on or on["out"] != out):
            for job in waiting:
                job["ok"], job["what"] = False, "taken out of the queue"
                STATE["queued"] = [j for j in STATE["queued"] if j is not job]
                STATE["done"] = (STATE["done"] + [job])[-KEEP:]
            return {"stopped": bool(waiting)}
        proc = RUNNING.get("proc")
    if not on or not proc:
        return {"stopped": False}
    on["what"] = "stopped"
    try:
        import psutil
        me = psutil.Process(proc.pid)
        for kid in me.children(recursive=True):
            kid.kill()
        me.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return {"stopped": True}


def look():
    """What to show: the job in hand, what is waiting, and what has finished."""
    can = ready()                        # outside the lock: nothing waits on this
    with LOCK:
        on = dict(STATE["on"]) if STATE["on"] else None
        return {"on": on,
                "queued": [dict(j) for j in STATE["queued"]],
                "done": [dict(j) for j in STATE["done"]],
                "can": can}


def start():
    global STARTED
    with LOCK:
        if STARTED:
            return
        STARTED = True
    threading.Thread(target=_work, daemon=True).start()


def _work():
    while True:
        job = WAITING.get()
        with LOCK:
            STATE["queued"] = [j for j in STATE["queued"] if j is not job]
            STATE["on"] = job
        try:
            _run(job)
        except Exception as e:
            job["what"] = "failed: %s" % str(e)[:120]
            job["ok"] = False
        with LOCK:
            STATE["on"] = None
            job["finished"] = int(time.time())
            STATE["done"] = (STATE["done"] + [job])[-KEEP:]


def _run(job):
    python = tool()
    if not python:
        job["ok"] = False
        job["what"] = "no Python on this machine has the speech model"
        return
    # Compiled, __file__ points inside the executable's own module tree, where no
    # script was ever written. Beside the program is where the installer puts it.
    script = script_path()
    if not script:
        job["ok"] = False
        job["what"] = ("the subtitle tools are not on this machine yet - "
                       "fetch them in Settings")
        return
    # Run it from the data folder, never from the program folder. Python puts the
    # script's own directory first on its path, so a worker started in the program
    # folder imports the compiled server's select.pyd and holds it open - and the
    # next install then stops to ask somebody to close an application.
    data = os.environ.get("PALLADIUM_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "Palladium")
    try:
        os.makedirs(data, exist_ok=True)
        mine = os.path.join(data, "pd_subs_make.py")
        if (not os.path.exists(mine)
                or os.path.getmtime(mine) < os.path.getmtime(script)):
            shutil.copyfile(script, mine)
        script = mine
    except OSError:
        pass                              # the program folder will have to do
    job["what"] = "starting"
    # the same ffmpeg the server streams with, and the same folder it keeps its own
    # files in: the worker is a stranger to both otherwise
    air = dict(os.environ)
    # The compiled server carries PYTHONHOME and PYTHONPATH of its own, pointing at a
    # runtime that is not this one. Handed to a real Python they hide its own
    # site-packages, and the job died on "you need to have sentencepiece installed"
    # with sentencepiece plainly installed.
    for name in [k for k in air if k.startswith("PYTHON")]:
        air.pop(name, None)
    try:
        import pd_gpu
        air["PALLADIUM_FFMPEG"] = pd_gpu.FFMPEG
    except Exception:
        pass
    if os.environ.get("PALLADIUM_DATA"):
        air["PALLADIUM_DATA"] = os.environ["PALLADIUM_DATA"]
    # -P: the script's folder stays off the path, so nothing of ours is imported by
    # accident. cwd is the data folder for the same reason.
    proc = subprocess.Popen([python, "-P", script,
                             job["video"], job["language"], job["out"]],
                            cwd=os.path.dirname(script),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=air,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    RUNNING["proc"] = proc
    last = ""
    try:
        last = _read(proc, job)
    finally:
        # whatever went wrong up there, the child does not outlive this: one left
        # running holds the machine and everything asking after it waits
        RUNNING["proc"] = None
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
    if job.get("what") == "stopped":
        job["ok"] = False
        return
    if "ok" not in job:
        job["ok"] = proc.returncode == 0
        if not job["ok"]:
            job["what"] = last[:160] or "the model stopped without saying why"
    for other in job.get("copies") or []:
        try:
            shutil.copyfile(job["out"], other)
        except OSError:
            pass


def _read(proc, job):
    """Follow the worker's own account of itself. Returns its last word."""
    last = ""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        last = line
        # as soon as there is a file, say so: it grows while the rest is written and
        # a player can be watching it already
        if not job.get("file") and os.path.exists(job["out"]):
            job["file"] = os.path.basename(job["out"])
        # and beside every other copy of the film, as it grows: which copy is played
        # is the library's decision, not this one's
        now = time.time()
        if job.get("copies") and now - job.get("spread", 0) > 15:
            job["spread"] = now
            for other in job["copies"]:
                try:
                    shutil.copyfile(job["out"], other)
                except Exception:
                    pass                   # a copy that will not be made is not the job
        if line.startswith("P "):
            bits = line.split(" ", 2)
            try:
                said = float(bits[1])
                if said >= 0:
                    job["at"] = said       # below zero: the step keeps the old number
            except (IndexError, ValueError):
                pass
            job["what"] = bits[2] if len(bits) > 2 else job["what"]
        elif line.startswith("OK "):
            job["at"], job["ok"], job["what"] = 1.0, True, "done"
            job["file"] = os.path.basename(job["out"])
        elif line.startswith("NO "):
            job["ok"], job["what"] = False, line[3:]
    return last
