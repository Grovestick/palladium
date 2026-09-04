#!/usr/bin/env python3
"""Sending a fault to palladium.video, when the person running the server allows it.

Off unless somebody turns it on. Three settings and no others:

    off      nothing leaves this machine, ever
    errors   what went wrong and which build it was
    more     the same, and what kind of machine it happened on

What is never sent, at any setting: the name of a film, a series or an episode; the
name or address of anybody watching; an invitation key; a file path; the address of
this machine or any other. Those are scrubbed here, on the way out, rather than being
trusted not to appear - a stack trace is written by a program in a hurry and it puts
whatever it has to hand into the message.

What "more" adds is a description of a computer, not of a person: how many cores, how
much memory, what the graphics card calls itself, which Windows. None of it identifies
anybody and all of it is the difference between "playback stopped" and "playback
stopped on the machines with that card".
"""
import json
import os
import platform
import re
import subprocess
import threading
import time
import urllib.request

#: where they go
WHERE = "https://get.palladium.video/faults"

#: how the setting is written down, and what each means
OFF, ERRORS, MORE = "off", "errors", "more"

#: at most one every few seconds, so a server in a crash loop cannot make a nuisance
#: of itself
LAST = {"at": 0.0}
EVERY = 5.0

#: paths, addresses, keys and anything in brackets - a title is put in brackets by the
#: player when it reports a fault, and a title is the one thing that must not travel
SCRUB = (
    # a link first: it carries an address and a key inside it, and taking those out
    # first would leave a mangled link behind rather than no link at all
    (re.compile(r'https?://\S+'), '<a link>'),
    # to the end of the line, not to the first space: a title has spaces in it, and
    # a pattern that stops at the first one leaves most of the title behind
    (re.compile(r'[A-Za-z]:\\[^\r\n"\']*'), '<a file>'),
    (re.compile(r'/(?:home|Users|mnt|media|srv|data)/[^\r\n"\']*'), '<a file>'),
    # and anything at all that ends in something a player can open, spaces included
    (re.compile(r'[^\s"\']*(?:\s[^\s"\']+)*?'
                r'\.(?:mkv|mp4|m4v|avi|mov|ts|m2ts|webm|mpe?g|wmv|flv'
                r'|srt|ass|vtt|sub|idx)\b', re.I), '<a file>'),
    (re.compile(r'(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?'), '<an address>'),
    (re.compile(r'\bt=[A-Za-z0-9_-]{8,}'), 't=<a key>'),
    # the player writes the film it was playing in brackets, and a title is the one
    # thing that must never leave the house
    (re.compile(r'\[[^\]]{1,120}\]'), '[a title]'),
)


def clean(text, people=()):
    """Everything that could name a person, a place or a film, taken out.

    `people` is the names this house knows - the owner and everyone invited. A name
    cannot be found by its shape, and a fault that says who was watching when it
    happened is a fault about a person. The server knows them, so it says so, and
    they go the same way as the addresses and the titles.
    """
    out = str(text or "")
    for pattern, instead in SCRUB:
        out = pattern.sub(instead, out)
    for name in sorted({str(n).strip() for n in people if str(n).strip()},
                       key=len, reverse=True):
        if len(name) < 2:
            continue
        out = re.sub(r'\b' + re.escape(name) + r'\b', "<somebody>", out,
                     flags=re.IGNORECASE)
    return out[:4000]


def machine():
    """What kind of computer this is. Nothing about who uses it."""
    said = {"system": platform.system(), "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version()}
    try:
        said["cores"] = str(os.cpu_count() or "")
    except Exception:
        pass
    try:
        import ctypes

        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong),
                        ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong),
                        ("totalpage", ctypes.c_ulonglong),
                        ("availpage", ctypes.c_ulonglong),
                        ("totalvirt", ctypes.c_ulonglong),
                        ("availvirt", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]
        it = Memory()
        it.length = ctypes.sizeof(Memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(it)):
            said["memory"] = "%d GB" % round(it.total / (1024 ** 3))
    except Exception:
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name = (out.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        if name:
            said["card"] = name[0].strip()[:60]
    except Exception:
        pass
    return said


def send(row, how, build="", app="", called="", forced=False, people=()):
    """Post one fault, in the background, if the setting allows it.

    `forced` is somebody pressing send on a single report with the setting off: one
    report, chosen by hand, which is a different thing from a machine reporting on
    its own and is allowed whatever the setting says.
    """
    how = str(how or OFF)
    if not forced and how not in (ERRORS, MORE):
        return False
    if time.time() - LAST["at"] < EVERY and not forced:
        return False
    LAST["at"] = time.time()
    said = {
        "kind": str(row.get("kind") or "error")[:20],
        "text": clean(row.get("text") or "", people),
        "build": str(build or "")[:20],
        "app": str(app or "")[:40],
        "from": str(called or "")[:24],
    }
    if how == MORE or (forced and how != OFF):
        said["machine"] = machine()

    def go():
        try:
            req = urllib.request.Request(
                WHERE, data=json.dumps(said).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
        except Exception:
            pass                          # a fault about a fault helps nobody

    threading.Thread(target=go, daemon=True).start()
    return True
