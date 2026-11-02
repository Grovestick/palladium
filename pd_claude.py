"""Faults read by Claude Code: a new fault starts it on this machine, read-only.

Off unless the owner turns it on. A fault is a report the machinery filed (a crash the
app posted, an error a page caught) or a stack the server wrote to fault.log. When one
arrives, and Claude Code is installed here, it is started with the faults, this
machine's logs and - where it can find them - the server's source, and may only read.
What it writes back goes on the noticeboard for the owner, and in claude\\ beside the
other state. One run at a time, and none within GAP of the last: a crash loop is one
question, not a hundred.
"""
import glob
import json
import os
import shutil
import subprocess
import threading
import time

#: seconds between two runs, at least
GAP = 15 * 60
#: how long one run may take
LIMIT = 15 * 60
#: how often the logs are looked at
EVERY = 60
#: the tools it may use: reading only
TOOLS = "Read,Grep,Glob"

STATE = {"root": "", "code": "", "on": None, "told": None, "file": None, "running": False}


def find():
    """Where Claude Code is on this machine, or "": on the path, its own installer's
    folder, or inside the editor extension (newest first)."""
    got = shutil.which("claude")
    if got:
        return got
    home = os.path.expanduser("~")
    for p in [os.path.join(home, ".local", "bin", "claude.exe")] + sorted(
            glob.glob(os.path.join(home, ".vscode", "extensions", "anthropic.claude-code-*",
                                   "resources", "native-binary", "claude.exe")), reverse=True):
        if os.path.isfile(p):
            return p
    return ""


def source(code, told=""):
    """The folder with the server's source, or "": the program's own folder when it
    runs from source, else the one the owner named."""
    for d in (code, told):
        if d and os.path.isfile(os.path.join(d, "pd-server.py")):
            return d
    return ""


def _paper():
    path = os.path.join(STATE["root"], "claude.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _keep(paper):
    path = os.path.join(STATE["root"], "claude.json")
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(paper, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def new_faults(rows, since):
    """The machinery's own reports newer than since: [(when, kind, app, text)]."""
    return [(int(r.get("when") or 0), str(r.get("kind") or ""), str(r.get("app") or ""),
             str(r.get("text") or "")) for r in rows
            if r.get("source") == "auto" and int(r.get("when") or 0) > since]


def new_stacks(text):
    """What fault.log gained, less the line each start writes: "" when only starts."""
    return "\n".join(ln for ln in text.splitlines()
                     if ln.strip() and not ln.startswith("---- started "))


def due(now, last, running):
    """Whether a run may start now."""
    return not running and now - last >= GAP


def question(faults, stacks, root, src):
    """What Claude is asked."""
    out = ["A Palladium media server on this computer has logged a fault. Find the cause. "
           "Read only: change no file, run nothing. Logs are in " + root +
           " (debug.log, fault.log, app-crash.log, feedback.jsonl)."]
    out.append("The server's source is in " + src + "." if src else
               "The server's source is not on this computer: work from the logs.")
    for when, kind, app, text in faults[-10:]:
        out.append("\n%s %s%s:\n%s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)),
                                       kind, " from " + app if app else "", text[:1500]))
    if stacks:
        out.append("\nfault.log gained:\n" + stacks[-4000:])
    out.append("\nAnswer in under 300 words: what failed, where in the code, how sure "
               "you are, and the change that would fix it.")
    return "\n".join(out)


def ask(exe, prompt, cwd, src):
    # these three tools exist and nothing else - no connectors, helpers or publishing,
    # any of which could write or send where reading cannot
    args = [exe, "-p", prompt, "--tools", TOOLS, "--allowedTools", TOOLS,
            "--strict-mcp-config"]
    if src and src != cwd:
        args += ["--add-dir", src]
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=LIMIT,
                          encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (done.stdout or "").strip() or ("(no answer, exit %d) %s"
                                            % (done.returncode, (done.stderr or "")[-400:]))


def look():
    """One look at the logs; a run in a thread of its own when there is something new."""
    paper = _paper()
    now = time.time()
    root = STATE["root"]
    fault_log = os.path.join(root, "fault.log")
    size = os.path.getsize(fault_log) if os.path.exists(fault_log) else 0
    if "since" not in paper:
        # turned on now: what came before is not this run's business
        paper.update(since=int(now), faultAt=size, last=0)
        _keep(paper)
        return
    rows = []
    try:
        with open(os.path.join(root, "feedback.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    faults = new_faults(rows, int(paper.get("since") or 0))
    stacks = ""
    at = int(paper.get("faultAt") or 0)
    if size > at:
        with open(fault_log, encoding="utf-8", errors="replace") as f:
            f.seek(at)
            stacks = new_stacks(f.read())
    elif size < at:
        paper["faultAt"] = size
    if not faults and not stacks:
        if size != at:
            paper["faultAt"] = size
            _keep(paper)
        return
    if not due(now, float(paper.get("last") or 0), STATE["running"]):
        return
    exe = find()
    if not exe:
        return
    paper.update(since=max([w for w, *_ in faults] or [int(paper.get("since") or 0)]),
                 faultAt=size, last=now)
    _keep(paper)
    src = source(STATE["code"], STATE["told"]() if STATE.get("told") else "")
    prompt = question(faults, stacks, root, src)
    STATE["running"] = True

    def run():
        try:
            answer = ask(exe, prompt, root, src)
        except Exception as e:
            answer = "(Claude Code did not finish: %s)" % str(e)[:200]
        finally:
            STATE["running"] = False
        folder = os.path.join(root, "claude")
        os.makedirs(folder, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + ".md"
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(prompt + "\n\n---\n\n" + answer + "\n")
        if STATE["file"]:
            STATE["file"](answer, name)

    threading.Thread(target=run, name="palladium-claude", daemon=True).start()


def start(root, code, on, told, file):
    """root: the state folder; code: the program's folder; on() the setting; told() the
    source folder the owner named; file(text, name) puts the answer on the board."""
    STATE.update(root=root, code=code, on=on, told=told, file=file)

    def work():
        while True:
            try:
                if on():
                    look()
                else:
                    paper = _paper()
                    if "since" in paper:
                        # off: the next time it is on starts from then
                        _keep({k: v for k, v in paper.items() if k == "last"})
            except Exception:
                pass
            time.sleep(EVERY)

    threading.Thread(target=work, name="palladium-claude-watch", daemon=True).start()
