"""Reaching this server from outside, with a name and a certificate.

Two ways out of the house, and the plain one is the default: the port this server
listens on, forwarded in the router, exactly as before. Nothing here runs unless
somebody asks for the other way.

The other way is Caddy: one program, fetched on request the way ffmpeg is, which
takes 443, gets a certificate for a name you own, and hands what it receives to this
server. It is kept beside the library rather than installed: a file under the
Palladium folder, started when this machine signs in, and forgotten by deleting it.

What it needs from whoever sets it up is a name that already answers to this house -
a certificate is issued for a name, never for an address - and one of two ways of
proving the name is theirs: the door open on 80 and 443, or a token for the account
that holds the name's DNS, which opens nothing at all.
"""
import io
import json
import os
import subprocess
import threading
import time
import urllib.request

import pd_machine

#: where the program and its answer live: beside the library, not in Program Files
STATE = {"root": "", "port": 8765, "fetching": {"busy": False, "said": "", "part": 0.0}}
LOCK = threading.RLock()

#: the one file Caddy is, for this machine
WHERE = "https://caddyserver.com/api/download?os=windows&arch=amd64"

#: how a download is described while it runs, in the same words ffmpeg uses
FETCHING = STATE["fetching"]


def start(root, port):
    """Where this machine keeps its own copy, and what it would stand in front of."""
    STATE["root"] = root
    STATE["port"] = int(port or 8765)
    return load()


def home():
    return os.path.join(STATE["root"], "caddy")


def program():
    return os.path.join(home(), "caddy.exe" if os.name == "nt" else "caddy")


def _path():
    return os.path.join(home(), "proxy.json")


def load():
    """What was set up here. "port" is the default: nothing in front of this server."""
    try:
        with io.open(_path(), encoding="utf-8") as f:
            said = json.load(f)
    except (OSError, ValueError):
        said = {}
    said.setdefault("how", "port")          # "port" or "caddy"
    said.setdefault("name", "")             # the name a certificate would be for
    said.setdefault("prove", "open")        # "open" (80 and 443) or "dns"
    said.setdefault("dnsProvider", "")      # cloudflare, and whatever is added later
    said.setdefault("dnsToken", "")
    said.setdefault("follower", "")         # a second name, for the machine that follows
    said.setdefault("followerAt", "")       # and where that one answers
    return said


def save(said):
    os.makedirs(home(), exist_ok=True)
    tmp = _path() + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(said, f, indent=2)
    os.replace(tmp, _path())
    return said


def here():
    """True when the program has been fetched and is ready to be run."""
    return os.path.exists(program()) and os.path.getsize(program()) > 1000000


def fetch():
    """Fetch Caddy itself, saying how far it has got while it comes."""
    with LOCK:
        if FETCHING["busy"]:
            return {"ok": True, "already": True}
        FETCHING.update(busy=True, said="Fetching Caddy…", part=0.0, done=False)
    threading.Thread(target=_fetch, daemon=True).start()
    return {"ok": True}


def _fetch():
    try:
        os.makedirs(home(), exist_ok=True)
        into = program() + ".part"
        with urllib.request.urlopen(WHERE, timeout=120) as r, open(into, "wb") as f:
            size = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                lump = r.read(262144)
                if not lump:
                    break
                f.write(lump)
                got += len(lump)
                FETCHING["part"] = round(got / size, 3) if size else 0.0
                FETCHING["said"] = ("Fetching Caddy - %d%% of %d MB"
                                    % (round(100 * got / size), round(size / 1e6))
                                    if size else
                                    "Fetching Caddy - %d MB so far" % round(got / 1e6))
        os.replace(into, program())
        FETCHING.update(busy=False, done=True, part=1.0,
                        said="Caddy is here. Give it a name and turn it on.")
    except Exception as e:
        FETCHING.update(busy=False, said="Could not fetch Caddy: %s" % str(e)[:160])


def caddyfile(said=None):
    """The configuration, written from the settings rather than by hand.

    Video is why most of this is here: a proxy that buffers a film delays every seek
    by as much as it holds, and one that gives up after a minute cuts a long viewing
    in half. Ranges pass through untouched, because seeking is a range request.
    """
    said = said or load()
    name = (said.get("name") or "").strip()
    if not name:
        return ""
    lines = []
    if said.get("prove") == "dns" and said.get("dnsProvider") and said.get("dnsToken"):
        lines += ["{", "\ttls {",
                  "\t\tdns %s %s" % (said["dnsProvider"], said["dnsToken"]),
                  "\t}", "}", ""]
    def site(host, to):
        return [
            "%s {" % host,
            "\treverse_proxy %s {" % to,
            # a film is a stream: held back, it stutters; cut short, it stops
            "\t\tflush_interval -1",
            "\t\ttransport http {",
            "\t\t\tread_timeout 24h",
            "\t\t\twrite_timeout 24h",
            "\t\t}",
            "\t}",
            "}",
            "",
        ]
    lines += [x for x in site(name, "127.0.0.1:%d" % STATE["port"]) if x != ""]
    other, at = (said.get("follower") or "").strip(), (said.get("followerAt") or "").strip()
    if other and at:
        lines += [x for x in site(other, at) if x != ""]
    return "\n".join(lines) + "\n"


def write_config(said=None):
    os.makedirs(home(), exist_ok=True)
    text = caddyfile(said)
    with io.open(os.path.join(home(), "Caddyfile"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return text


def running():
    """The process id of the Caddy this machine started, or 0."""
    if not pd_machine.windows():
        return 0
    said = pd_machine.powershell(
        "(Get-CimInstance Win32_Process -Filter \"Name='caddy.exe'\" | "
        "Select-Object -First 1 -ExpandProperty ProcessId)") or ""
    try:
        return int(str(said).strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def run():
    """Start Caddy with the configuration written from the settings."""
    said = load()
    if not here():
        return {"ok": False, "why": "Caddy has not been fetched yet."}
    if not (said.get("name") or "").strip():
        return {"ok": False, "why": "Give it a name first: a certificate is issued for "
                                    "a name, never for an address."}
    write_config(said)
    if running():
        return stopstart()
    try:
        subprocess.Popen([program(), "run", "--config", os.path.join(home(), "Caddyfile"),
                          "--adapter", "caddyfile"],
                         cwd=home(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return {"ok": False, "why": str(e)[:160]}
    time.sleep(1.5)
    return {"ok": bool(running()), "why": "" if running() else
            "Caddy would not stay up. Port 443 may be taken, or the name may not "
            "answer to this house yet."}


def stop():
    if pd_machine.windows():
        pd_machine.powershell("Get-Process caddy -ErrorAction SilentlyContinue | "
                              "Stop-Process -Force")
    return {"ok": not running()}


def stopstart():
    stop()
    time.sleep(1)
    return run()


def state():
    """What the settings page draws: which way out, and how that way is getting on."""
    said = load()
    pid = running() if here() else 0
    return {
        "how": said.get("how"), "name": said.get("name"), "prove": said.get("prove"),
        "dnsProvider": said.get("dnsProvider"), "dnsSet": bool(said.get("dnsToken")),
        "follower": said.get("follower"), "followerAt": said.get("followerAt"),
        "here": here(), "running": bool(pid), "pid": pid,
        "fetching": dict(FETCHING),
        "port": STATE["port"],
        "startsAtLogin": _at_login(),
    }


def _shortcut():
    return os.path.join(pd_machine.STARTUP, "Palladium proxy.lnk") \
        if hasattr(pd_machine, "STARTUP") else ""


def _at_login():
    where = _shortcut()
    return bool(where) and os.path.exists(where)


def set_at_login(on):
    """Caddy at sign-in, the same way the server itself does it: a shortcut, no service."""
    where = _shortcut()
    if not where:
        return {"ok": False, "why": "Only on Windows."}
    if not on:
        try:
            os.remove(where)
        except OSError:
            pass
        return {"ok": not _at_login()}
    if not here():
        return {"ok": False, "why": "Caddy has not been fetched yet."}
    pd_machine.powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; $s.Arguments = 'run --config \"%s\" --adapter caddyfile'; "
        "$s.WorkingDirectory = '%s'; $s.WindowStyle = 7; $s.Save()"
        % (where, program(), os.path.join(home(), "Caddyfile"), home()))
    return {"ok": _at_login()}


def set_config(body):
    """What was typed on the settings page. A change while it runs is applied at once."""
    said = load()
    for name in ("how", "name", "prove", "dnsProvider", "dnsToken", "follower", "followerAt"):
        if name not in body:
            continue
        word = str(body[name] or "").strip()
        # the token is never sent back to the page, so what comes back as dots is the
        # page saying "as it was", not somebody typing dots
        if name == "dnsToken" and word and set(word) <= set("•*· "):
            continue
        said[name] = word
    if said.get("how") not in ("port", "caddy"):
        said["how"] = "port"
    if said.get("prove") not in ("open", "dns"):
        said["prove"] = "open"
    save(said)
    if said["how"] == "caddy" and here() and (said.get("name") or ""):
        write_config(said)
        if running():
            stopstart()
    if said["how"] == "port" and running():
        stop()
    return state()
