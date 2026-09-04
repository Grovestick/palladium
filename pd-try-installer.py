"""Install Palladium the way a stranger would, prove it runs, and take it off again.

Deliberately harmless to the machine it is run on:
  * installs into a temporary folder, not the usual one
  * /TASKS="" so it adds no Startup shortcut and no firewall rule
  * the installed server is started on a free port with a temporary papers folder,
    so it cannot touch the settings, library or port of the one already running
  * uninstalls silently afterwards, which now keeps %APPDATA%\\Palladium untouched
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
#: whichever version was built last, rather than one written down here and forgotten
SETUP = max((os.path.join(BUILD, f) for f in (os.listdir(BUILD)
                                              if os.path.isdir(BUILD) else [])
             if f.startswith("Palladium-Setup-") and f.endswith(".exe")),
            key=os.path.getmtime, default="")


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw)


def main():
    if not os.path.exists(SETUP):
        raise SystemExit("no installer at " + SETUP)
    where = tempfile.mkdtemp(prefix="palladium-install-")
    shutil.rmtree(where, ignore_errors=True)          # Inno makes it itself
    log = os.path.join(tempfile.gettempdir(), "palladium-install.log")

    print("--- installing to", where)
    done = run([SETUP, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                '/TASKS=""', "/DIR=" + where, "/LOG=" + log])
    print("setup exit code:", done.returncode)
    if done.returncode != 0:
        raise SystemExit("the installer refused")

    for _ in range(30):                               # it returns before it is done
        if os.path.exists(os.path.join(where, "palladium-server.exe")):
            break
        time.sleep(1)

    print("\n--- what landed")
    if not os.path.isdir(where):
        raise SystemExit("nothing was installed")
    big = [(f, os.path.getsize(os.path.join(where, f)))
           for f in os.listdir(where)
           if os.path.isfile(os.path.join(where, f))]
    big.sort(key=lambda p: -p[1])
    for name, size in big[:8]:
        print("   %8.1f MB  %s" % (size / 1e6, name))
    print("   %d files, %d folders"
          % (sum(len(f) for _, _, f in os.walk(where)),
             sum(len(d) for _, d, _ in os.walk(where))))
    for wanted in ("palladium.exe", "palladium-server.exe", "static", "changes.json",
                   "unins000.exe"):
        print("   %-24s %s" % (wanted, "yes" if os.path.exists(
            os.path.join(where, wanted)) else "MISSING"))

    print("\n--- asking the installed server for everything")
    smoke = run([sys.executable, os.path.join(HERE, "pd-smoke.py"),
                 "--exe", os.path.join(where, "palladium-server.exe")], cwd=HERE)

    print("\n--- uninstalling")
    unins = os.path.join(where, "unins000.exe")
    if os.path.exists(unins):
        run([unins, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        for _ in range(30):
            if not os.path.exists(os.path.join(where, "palladium-server.exe")):
                break
            time.sleep(1)
    left = os.listdir(where) if os.path.isdir(where) else []
    print("left behind in the install folder:", left or "nothing")
    shutil.rmtree(where, ignore_errors=True)

    papers = os.path.join(os.environ.get("APPDATA", ""), "Palladium")
    print("your own %APPDATA%\\Palladium:",
          "still there" if os.path.exists(papers) else "not present (nothing to keep)")
    return smoke.returncode


if __name__ == "__main__":
    sys.exit(main())
