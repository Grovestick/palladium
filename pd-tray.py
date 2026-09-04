#!/usr/bin/env python3
"""Palladium in the system tray.

Runs the server as a child process and puts a P in the notification area with the
things worth reaching from there: the web player, the phone install page, a library
scan, and a way to stop it. Closing the menu does not stop the server; Quit does.

    python tray.py            start the server and show the icon
    python tray.py --attach   just the icon, for a server already running elsewhere
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from PIL import Image, ImageDraw
import pystray

#: Packaged, this file lives inside the executable and __file__ points nowhere
#: useful: the program and the server beside it are next to sys.executable instead.
PACKAGED = bool(globals().get("__compiled__")) or getattr(sys, "frozen", False)
ROOT = os.path.dirname(os.path.abspath(
    sys.executable if PACKAGED else __file__))
def port_wanted():
    """The port the server on this machine answers on.

    Two Palladiums in one house are two ports - the library on 8765, the machine
    keeping copies on 8764 - and the tray was opening a browser at 8765 whatever the
    server had been set to. The setting is read where the server keeps it.
    """
    said = os.environ.get("PALLADIUM_PORT") or ""
    if not said:
        try:
            data = os.path.join(os.environ.get("APPDATA") or ROOT, "Palladium",
                                "settings.json")
            with open(data, encoding="utf-8") as f:
                said = (json.load(f) or {}).get("port") or ""
        except Exception:
            said = ""
    try:
        port = int(said)
    except (TypeError, ValueError):
        return 8765
    return port if 1 <= port <= 65535 else 8765


PORT = port_wanted()
LOCAL = "http://127.0.0.1:%d" % PORT

GROUND = (11, 13, 16)
ACCENT = (229, 160, 13)


def mark(size=64, bg=None):
    """The Palladium P, drawn rather than shipped as a file."""
    img = Image.new("RGBA", (size, size), (bg or GROUND) + (255,) if bg is not None else GROUND + (255,))
    d = ImageDraw.Draw(img)
    u = size / 108.0                       # the same 108-unit grid the app icon uses
    stem = [36 * u, 22 * u, 47 * u, 86 * u]
    d.rectangle(stem, fill=ACCENT)
    d.ellipse([40 * u, 22 * u, 84 * u, 62 * u], fill=ACCENT)
    d.ellipse([52 * u, 33 * u, 72 * u, 51 * u], fill=GROUND)
    d.rectangle([36 * u, 22 * u, 47 * u, 86 * u], fill=ACCENT)
    return img


def ico(path):
    """A multi-size .ico, so Windows has a crisp one at every scale."""
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 256]
    mark(256).save(path, sizes=[(s, s) for s in sizes])
    return path


def only_one():
    """True if this is the only tray. Windows keeps the name for us.

    Two of these start together after an installer runs - the one it launches and the
    one the Startup shortcut has just brought up - and each looks for a server, finds
    none yet, and starts one. Two icons, two servers, one of them holding no port.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Palladium.tray.one")
        if not handle:
            return True
        if ctypes.windll.kernel32.GetLastError() == 183:      # already exists
            return False
        only_one.held = handle            # kept for as long as this runs
        return True
    except Exception:
        return True


class Tray:
    def __init__(self, attach=False):
        self.proc = None
        self.icon = None
        self.status = "starting"
        if not attach:
            self.start_server()

    # ---- the server process -------------------------------------------------
    def start_server(self):
        if self.running():
            self.status = "already running"
            return
        # Built, there is no Python and no pd-server.py: sys.executable is this tray,
        # and starting it again would put up a second icon rather than a server. The
        # built server sits beside it under its own name.
        built = os.path.join(ROOT, "palladium-server.exe")
        line = ([built, "--no-open"] if os.path.exists(built)
                else [sys.executable, os.path.join(ROOT, "pd-server.py"), "--no-open"])
        self.proc = subprocess.Popen(
            line,
            cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def stop_server(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except Exception:
                self.proc.kill()
        self.proc = None

    @staticmethod
    def running():
        try:
            urllib.request.urlopen(LOCAL + "/config", timeout=2).read()
            return True
        except Exception:
            return False

    # ---- what the icon says -------------------------------------------------
    def library(self):
        try:
            with urllib.request.urlopen(LOCAL + "/library/status", timeout=4) as r:
                s = json.loads(r.read())
            scan = s.get("scan") or {}
            if scan.get("running"):
                return "%s %s/%s" % (scan.get("phase"), scan.get("done"), scan.get("total"))
            return "%d films, %d shows, %d episodes" % (s["movies"], s["shows"], s["episodes"])
        except Exception:
            return "server not responding"

    def watch(self):
        """Keep the tooltip honest about what the server is doing."""
        turns = 0
        while True:
            self.status = self.library() if self.running() else "stopped"
            if self.icon:
                self.icon.title = "Palladium - " + self.status
            # And whether there is a build to take. Asked by the icon rather than
            # waiting to be asked: a line reading "Check for updates" says nothing
            # about whether there is one, so nobody presses it and a build sits here
            # unnoticed. Every ten minutes, and once at startup.
            if self.running() and turns % 60 == 0:
                self.look_for_update()
            turns += 1
            time.sleep(10)

    def look_for_update(self):
        """What is out, without doing anything about it."""
        try:
            with urllib.request.urlopen(LOCAL + "/update", timeout=20) as r:
                said = json.loads(r.read())
        except Exception:
            return
        if said.get("newer"):
            self.found = said.get("latest", "")
            self.update_says = "Install %s" % self.found
        elif said.get("why") and not said.get("latest"):
            self.update_says = "Check for updates"
        else:
            self.found = ""
            self.update_says = "Up to date (%s) - check again" % said.get("have", "")

    # ---- menu ---------------------------------------------------------------
    def menu(self):
        return pystray.Menu(
            pystray.MenuItem("Open Palladium", lambda: webbrowser.open(LOCAL + "/"), default=True),
            # the tab, not merely the page: the address says which screen to open
            pystray.MenuItem("Library settings",
                             lambda: webbrowser.open(LOCAL + "/#settings/library")),
            pystray.MenuItem("Install on a phone", lambda: webbrowser.open(LOCAL + "/app")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Scan library now", self.scan),
            pystray.MenuItem(lambda item: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            # the same question the settings page asks, for somebody who never opens it
            pystray.MenuItem(lambda item: self.update_says, self.update),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart server", self.restart),
            pystray.MenuItem("Quit", self.quit),
        )

    #: what the update line reads before anybody has asked
    update_says = "Check for updates"
    #: the build the last look found, if it found one
    found = ""

    def say(self, words):
        """The line in the menu, and a notice for the press that closed it.

        Windows shuts the menu the moment an item is pressed, and pystray cannot hold
        it open - so asking about updates hid its own answer until the menu was opened
        again. The answer is shown beside the icon as well.
        """
        self.update_says = words
        try:
            if self.icon:
                self.icon.notify(words, "Palladium")
        except Exception:
            pass                       # notices are a courtesy, not a duty

    def update(self):
        """Ask the server what build is out, and take it if there is a newer one.

        Two presses rather than one: the first says what is there, the second fetches
        it. Nobody should replace a running server by brushing past a menu.
        """
        try:
            with urllib.request.urlopen(LOCAL + "/update?force=1", timeout=20) as r:
                said = json.loads(r.read())
        except Exception:
            self.say("Could not ask the site")
            return
        if said.get("why") and not said.get("newer"):
            self.say(said["why"])
            return
        if not said.get("newer"):
            self.found = ""
            # said out loud that pressing it again asks again: a line that only
            # states a fact reads as a label rather than a control
            self.say("Up to date (%s) - check again" % said.get("have", ""))
            return
        # The icon looks by itself, so the line may already name the build. Naming it
        # is the warning; the press that follows a named build is the one that takes
        # it, and a press on "Check for updates" only names it.
        if not self.update_says.startswith("Install ") and not self.found:
            self.found = said.get("latest", "")
            self.say("Install %s - press again to take it" % self.found)
            return
        # second press: the server fetches it, checks the hash and stands aside
        try:
            req = urllib.request.Request(LOCAL + "/update/install", data=b"{}",
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=180).read()
            self.say("Installing %s - it will come back on its own"
                     % said.get("latest", ""))
        except Exception:
            self.say("The update could not be fetched")

    def scan(self):
        try:
            req = urllib.request.Request(LOCAL + "/library/scan", data=b"{}",
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=5).read()
            self.status = "scanning"
        except Exception:
            self.status = "scan failed"

    def restart(self):
        self.stop_server()
        time.sleep(1)
        self.start_server()

    def quit(self):
        self.stop_server()
        if self.icon:
            self.icon.stop()

    def run(self):
        threading.Thread(target=self.watch, daemon=True).start()
        self.icon = pystray.Icon("palladium", mark(64), "Palladium", self.menu())
        self.icon.run()


if __name__ == "__main__":
    if not only_one():
        raise SystemExit(0)               # another tray is already there
    if "--make-ico" in sys.argv:                 # for the Startup shortcut
        print(ico(os.path.join(ROOT, "palladium.ico")))
    else:
        Tray(attach="--attach" in sys.argv).run()
