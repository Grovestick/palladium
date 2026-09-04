#!/usr/bin/env python3
"""Keeping the server itself current.

The app has had this since the beginning; the server has not, and during a beta the
server is the half that changes every day. Three steps, each of which can fail on its
own and says so:

    1. Ask palladium.video what the current build is - a small file, no key needed.
    2. Fetch that build through the beta gate, with the key this server was given.
       A revoked key stops updates as well as downloads, which is the point of it.
    3. Check the hash, run the installer silently, and start the new program.

Nothing happens without being asked: the check is cheap and automatic, the install is
a press. A server running from source is left alone entirely - there is nothing to
replace, and the copy on disk is somebody's working tree.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

#: where the current build announces itself, and where the file comes from. The
#: build needs no key: a key is for handing the installer to somebody new, and an
#: update that stops the day a key expires is an update nobody can rely on.
WHERE = "https://palladium.video/server.json"
GATE = "https://get.palladium.video/build"

#: how long an answer is kept before asking again: a beta ships often, but not hourly
CACHE_FOR = 6 * 3600
_said = {"when": 0, "what": None}


def packaged():
    """Whether this is the built program rather than a working tree.

    Nuitka sets __compiled__ on the module; a source checkout has no installer to
    replace and no business running one.
    """
    return bool(globals().get("__compiled__")) or getattr(sys, "frozen", False)


def latest(force=False):
    """What the site says the current build is, or None if it cannot be reached."""
    now = time.time()
    if not force and _said["what"] and now - _said["when"] < CACHE_FOR:
        return _said["what"]
    try:
        req = urllib.request.Request(WHERE, headers={"User-Agent": "palladium"})
        with urllib.request.urlopen(req, timeout=12) as r:
            said = json.loads(r.read().decode("utf-8"))
        if not isinstance(said, dict) or not said.get("version"):
            return None
        _said["what"] = said
        _said["when"] = now
        return said
    except Exception:
        return None


def newer(there, here):
    """Whether one version string is later than another, counted part by part."""
    def parts(v):
        out = []
        for bit in str(v or "0").split("."):
            digits = "".join(c for c in bit if c.isdigit())
            out.append(int(digits or 0))
        return out
    a, b = parts(there), parts(here)
    while len(a) < len(b):
        a.append(0)
    while len(b) < len(a):
        b.append(0)
    return a > b


def fetch(key, want_sha, onto=None):
    """The installer, checked against the hash the site published.

    Returns the path it was written to. A file that does not match is deleted rather
    than kept: a half-downloaded installer that runs is worse than no installer. The
    key is accepted and ignored - it is not needed here, and callers still pass one.
    """
    url = GATE
    onto = onto or os.path.join(tempfile.gettempdir(),
                                "Palladium-Setup-%d.exe" % int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": "palladium"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(onto, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    if want_sha and digest.hexdigest().lower() != str(want_sha).lower():
        try:
            os.remove(onto)
        except OSError:
            pass
        raise ValueError("the download does not match the published hash")
    return onto


def install(path):
    """Stand the program down, replace it, and start it again. One press does all of it.

    The server cannot be running while its own files are replaced - it holds
    palladium-server.exe and the OpenSSL libraries beside it, and an installer that
    finds them locked stops and asks, which nobody is there to answer. So the work is
    handed to a detached shell that outlives us: wait a moment for this answer to
    reach the browser, close both programs, install, start the new one.

    Not `&&` between the steps: taskkill returns a failure when there was nothing to
    kill, which would stop the chain before the installer ran.
    """
    program = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Palladium",
                           "palladium.exe")
    log = os.path.join(tempfile.gettempdir(), "palladium-update.log")
    script = os.path.join(tempfile.gettempdir(), "palladium-update.cmd")
    with open(script, "w", encoding="ascii", errors="replace") as f:
        f.write("@echo off\r\n")
        # let the reply out of the socket before the socket goes
        f.write("ping -n 3 127.0.0.1 >nul\r\n")
        f.write("taskkill /F /IM palladium.exe >nul 2>&1\r\n")
        f.write("taskkill /F /IM palladium-server.exe >nul 2>&1\r\n")
        f.write("ping -n 3 127.0.0.1 >nul\r\n")
        f.write('"%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS '
                '/FORCECLOSEAPPLICATIONS /LOG="%s"\r\n' % (path, log))
        f.write('start "" "%s"\r\n' % program)
        f.write('exit\r\n')
    # A file rather than a line, and one shell starting another rather than one shell
    # doing the work: the chain is killed halfway through by design - it kills this
    # very program - and a cmd spawned straight from here went with it, so the
    # installer was never reached. The inner shell is started by one that exits at
    # once, and what it does is written down where it can be read afterwards.
    # /b rather than /min: "start" makes a console of its own whatever the
    # flags on the shell that called it, and a minimised window is still a
    # window - one was left sitting in the temporary folder after every
    # update anybody took.
    subprocess.Popen(["cmd", "/c", "start", "", "/b", script],
                     cwd=tempfile.gettempdir(), close_fds=True,
                     creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0) |
                                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                                    getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    return True


def beside(here_version, folders):
    """A newer installer already on this machine, if one is sitting there.

    Building one leaves it beside the web client, where /server hands it out. A server
    that has one newer than itself should offer it: waiting for the site to publish is
    the right answer for a stranger's copy and a silly one for the machine the build
    was made on.
    """
    best = ("", "")
    for where in folders:
        try:
            names = os.listdir(where)
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if not (low.startswith("palladium-setup") and low.endswith(".exe")):
                continue
            version = name[len("Palladium-Setup-"):-len(".exe")]
            if newer(version, here_version) and newer(version, best[0]):
                best = (version, os.path.join(where, name))
    return best


def state(here, key, force=False, folders=()):
    """What to say on the settings screen: what is out, and whether it can be had."""
    near, path = beside(here, folders) if packaged() else ("", "")
    if near:
        # one sitting here beats anything the site has to send: it is already fetched
        return {"have": here, "latest": near, "when": "", "notes": "",
                "newer": True, "from": "this machine",
                "file": os.path.basename(path)}
    said = latest(force=force)
    if not said:
        return {"have": here, "latest": "", "why": "palladium.video did not answer"}
    out = {"have": here, "latest": said.get("version", ""),
           "when": said.get("built", ""), "notes": said.get("notes", ""),
           "sha256": said.get("sha256", "")}
    out["newer"] = newer(out["latest"], here)
    if not packaged():
        out["why"] = "This is running from source; update it with git"
    return out
