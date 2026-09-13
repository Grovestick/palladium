#!/usr/bin/env python3
"""Build Palladium into something a stranger can install.

Two steps, in this order:

    1. Nuitka compiles the server and the tray icon to C and then to native
       executables, with CPython inside them. Nothing to install first, no Python on
       the machine it lands on, and no console window in sight.
    2. Inno Setup wraps those, the web client, the app and the changelog into one
       Palladium-Setup.exe.

ffmpeg is deliberately not inside it. The full build is 222 MB for one executable, and
an installer that size is a download people abandon; the first run fetches it instead,
which is also how it stays current. Anyone who would rather not wait can drop an
ffmpeg.exe beside the program and it will be used as it stands.

Run this from a python.org Python - not the one from the Microsoft Store, which is
sandboxed and cannot produce a distributable build:

    py -3.13 pd-build-installer.py            everything
    py -3.13 pd-build-installer.py compile    just the executables
    py -3.13 pd-build-installer.py wrap       just the installer
"""
import os
import shutil
import subprocess
import time
import atexit
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "build")
DIST = os.path.join(OUT, "Palladium")

#: What the compiled program needs beside it. The APK rides along so a television can
#: be given the app by typing a five-character code, with nothing else to hand.
#: pd_subs_make.py is a script rather than a module: it is run by whichever Python
#: on the machine has the speech model, never by the compiled server itself.
CARRIED = ["static", "changes.json", "config.example.json", "library.example.json",
           "pd_subs_make.py"]

def hand_over(made):
    """Put the installer where a running server can serve it at /server.

    Both places: the tree, so the development server has it, and an installed build,
    so anybody on the house network can be handed the same file without going through
    the beta gate.
    """
    name = os.path.basename(made)
    for where in [os.path.join(HERE, "static"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "Palladium",
                               "static")]:
        if not os.path.isdir(where):
            continue
        try:
            for old in os.listdir(where):
                low = old.lower()
                if (low.startswith("palladium-setup") and low.endswith(".exe")
                        and old != name):
                    os.remove(os.path.join(where, old))
            shutil.copy2(made, os.path.join(where, name))
            print("served from", os.path.join(where, name))
        except OSError as why:
            print("could not put it in %s: %s" % (where, why))


def version_now():
    """The version at the top of the changelog, which is what shipped.

    Kept here rather than typed in twice: a build stamped with last month's number is
    an installer that says it is older than the server inside it.
    """
    import json
    with open(os.path.join(HERE, "changes.json"), encoding="utf-8") as f:
        return str(json.load(f)[0]["version"])


VERSION = version_now()


def bake_version():
    """Write the version into a module, so the program carries its own number.

    A changelog beside the program is a file, and files are copied about by build
    steps and hand-editing alike. What is compiled in cannot drift from what is
    running.
    """
    with open(os.path.join(HERE, "pd_built.py"), "w", encoding="utf-8") as f:
        f.write(chr(34) * 3 + "What this build is. Written by the build."
                + chr(34) * 3 + chr(10))
        f.write("VERSION = %r" % VERSION + chr(10))
        f.write("WHEN = %r" % time.strftime("%Y-%m-%d %H:%M") + chr(10))


def run(cmd, **kw):
    print(">", " ".join(str(c) for c in cmd))
    done = subprocess.run(cmd, **kw)
    if done.returncode:
        raise SystemExit("failed: %s" % cmd[0])


#: Where Inno Setup puts itself: the two Program Files locations, and the per-user
#: one winget uses when it installs without administrator rights.
ISCC_AT = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6",
                 "ISCC.exe"),
]


def iscc():
    """The Inno Setup compiler, wherever it landed, or None."""
    return next((p for p in ISCC_AT if p and os.path.exists(p)), None)


def check():
    """Say what is missing before anything takes twenty minutes to fail."""
    missing = []
    if "WindowsApps" in sys.executable:
        missing.append(
            "This is the Microsoft Store Python, which is sandboxed and cannot build a\n"
            "  distributable. Install Python from python.org and run this with that one.")
    try:
        import nuitka                                        # noqa: F401
    except ImportError:
        missing.append("Nuitka is not installed:  py -3.13 -m pip install nuitka")
    try:
        import PIL, pystray                                  # noqa: F401
    except ImportError:
        missing.append("The tray needs Pillow and pystray:  pip install pillow pystray")
    if not iscc():
        missing.append("Inno Setup 6 is not installed:  winget install JRSoftware.InnoSetup")
    for name in ("pd-server.py", "pd-tray.py", "static"):
        if not os.path.exists(os.path.join(HERE, name)):
            missing.append("missing from this folder: " + name)
    if missing:
        print("Cannot build yet:\n")
        for line in missing:
            print("  * " + line)
        raise SystemExit(1)


def compile_one(script, name, console):
    """One executable, with CPython compiled into it."""
    cmd = [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
           # every core: this is hundreds of C files and it was compiling them one
           # after another
           "--jobs=%d" % (os.cpu_count() or 4),
           "--output-dir=" + OUT, "--output-filename=" + name,
           "--company-name=Grovestick Studios", "--product-name=Palladium",
           "--file-version=" + VERSION, "--product-version=" + VERSION,
           "--windows-icon-from-ico=" + os.path.join(HERE, "static", "palladium.ico"),
           # the modules are imported by name at the point they are needed
           "--include-module=pd_localapi", "--include-module=pd_library",
           "--include-module=pd_gpu", "--include-module=pd_invites",
           "--include-module=pd_subs", "--include-module=pd_sync",
           "--include-module=pd_upnp", "--include-module=pd_watching", "--include-module=pd_follow",
           "--include-module=pd_traffic", "--include-module=pd_built",
           "--include-module=pd_faults", "--include-module=pd_skins",
           "--include-module=pd_ai_subs", "--include-module=pd_tray",
           "--include-module=pd_torrents", "--include-module=pd_machine"]
    if not console:
        cmd.append("--windows-console-mode=disable")
    cmd.append(os.path.join(HERE, script))
    run(cmd, cwd=HERE)


#: Held for as long as a build runs. Two builds share this folder, and the second
#: one deletes files the first is halfway through writing - which fails both and
#: leaves no installer at all.
ALONE = os.path.join(HERE, ".building")


def only_one_build():
    """Refuse to start beside another build, and say which one."""
    os.makedirs(OUT, exist_ok=True)
    try:
        with open(ALONE, "x", encoding="utf-8") as f:
            f.write("%d at %s" % (os.getpid(), time.strftime("%H:%M:%S")))
        return
    except FileExistsError:
        pass
    try:
        with open(ALONE, encoding="utf-8") as f:
            said = f.read().strip()
        old = int(said.split()[0])
    except (OSError, ValueError):
        old, said = 0, "?"
    alive = False
    if old:
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, old)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                alive = True
        except Exception:
            alive = False
    if alive:
        raise SystemExit("another build is running (%s). Wait for it, or stop it."
                         % said)
    # the one that held it is gone: its half-written files are ours to replace
    os.remove(ALONE)
    only_one_build()


def let_go():
    """Release the build lock, however the build ended."""
    try:
        os.remove(ALONE)
    except OSError:
        pass


def compile_all():
    only_one_build()
    atexit.register(let_go)
    check()
    # what is left of an earlier build, but not its cache: emptying this folder was
    # costing a full recompile every time, which is ten minutes of the same work
    for name in ("Palladium",):
        if os.path.exists(os.path.join(OUT, name)):
            shutil.rmtree(os.path.join(OUT, name))
    compile_one("pd-server.py", "palladium-server.exe", console=False)
    # No separate tray program: the server shows the icon. A windowless launcher that
    # starts a hidden server at sign-in is what Defender's machine learning quarantined.
    # One program now: the server, which shows the tray icon itself. A tray folder
    # left by an earlier build is not merged in - its palladium.exe is what Defender
    # quarantines, and reading it aborts the installer.
    server = os.path.join(OUT, "pd-server.dist")
    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    shutil.copytree(server, DIST)
    for name in CARRIED:
        source = os.path.join(HERE, name)
        if not os.path.exists(source):
            continue
        target = os.path.join(DIST, name)
        if os.path.isdir(source):
            # not the installer itself: /server keeps one beside the web client, and
            # packing that into the next installer doubles its size every build
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                "Palladium-Setup*.exe"))
        else:
            shutil.copy2(source, target)
    print("compiled into", DIST)
    prove()


def prove():
    """Ask the built server for everything, before it is wrapped and handed out.

    A compiled build fails in ways source never does - a module imported by name and
    never followed, a file that was not copied in - and the first person to find out
    should not be somebody who downloaded it.
    """
    exe = os.path.join(DIST, "palladium-server.exe")
    if not os.path.exists(exe):
        raise SystemExit("nothing was built: " + exe)
    print(chr(10) + "--- proving the build ---")
    done = subprocess.run([sys.executable, os.path.join(HERE, "pd-smoke.py"),
                           "--exe", exe], cwd=HERE)
    if done.returncode:
        raise SystemExit("the built server failed its own smoke test; not wrapping it")
    print("--- the build answers everything ---" + chr(10))


#: the smallest a real installer has been. One short of this has lost something.
LEAST_SETUP_MB = 28


def wrap():
    """Inno Setup turns the folder into one Palladium-Setup.exe."""
    where = iscc()
    if not where:
        raise SystemExit("Inno Setup 6 is not installed")
    # The script takes the folder by wildcard, so a file that is not there is simply
    # not in the installer and the compile still says it succeeded. The virus scanner
    # takes the freshly built server away every few builds - it has been doing it for
    # weeks - and the installer that came out was five megabytes short and held no
    # server at all. Nothing about it said so.
    server = os.path.join(OUT, "Palladium", "palladium-server.exe")
    if not os.path.exists(server) or os.path.getsize(server) < 2_000_000:
        raise SystemExit(
            "the built server is missing from %s - the virus scanner takes it, and an "
            "installer without it compiles quite happily. Restore it (Windows Security "
            "> Protection history > Allow) or exclude the build folder, and run this "
            "again." % os.path.dirname(server))
    run([where, "/DMyVersion=" + VERSION, os.path.join(HERE, "palladium.iss")], cwd=HERE)
    made = os.path.join(OUT, "Palladium-Setup-%s.exe" % VERSION)
    # and the same check from the other end: what came out is the size an installer is
    if os.path.getsize(made) < LEAST_SETUP_MB * 1_000_000:
        raise SystemExit("the installer is only %.1f MB - something was left out of it"
                         % (os.path.getsize(made) / 1e6))
    print("installer in", made)
    announce(made)
    hand_over(made)


def announce(made):
    """Write what the site tells a running server: the build, and what it hashes to.

    The server checks this file, fetches the installer through the beta gate and
    refuses anything whose hash disagrees - so this is the one place the number and
    the file are tied together.
    """
    import hashlib
    import json as _json
    with open(os.path.join(HERE, "changes.json"), encoding="utf-8") as f:
        top = _json.load(f)[0]
    digest = hashlib.sha256()
    with open(made, "rb") as f:
        for chunk in iter(lambda: f.read(262144), b""):
            digest.update(chunk)
    said = {"version": VERSION,
            "built": time.strftime("%Y-%m-%d"),
            "size": os.path.getsize(made),
            "sha256": digest.hexdigest(),
            "notes": top.get("title", "")}
    where = os.path.join(HERE, "site", "server.json")
    with open(where, "w", encoding="utf-8") as f:
        _json.dump(said, f, indent=2)
    print("announced", VERSION, "in", where)


if __name__ == "__main__":
    what = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if what == "prove":
        prove()
        raise SystemExit(0)
    if what in ("all", "compile"):
        # the number goes in before the compiler does
        bake_version()
        compile_all()
    if what in ("all", "wrap"):
        wrap()
