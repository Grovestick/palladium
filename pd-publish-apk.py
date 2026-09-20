#!/usr/bin/env python3
"""Copy the built APK into static/ and record what version it is.

The version is read out of the APK itself rather than typed twice: the download page
and the in-app update check both read version.json, so they cannot disagree with the
file people actually install.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
#: The release build, and the debug one only if that is all there is.
#:
#: A debug build is debuggable, which turns off what the runtime would otherwise do
#: with the code: the same app took 5938ms to reach its first screen as a debug build
#: and 1138ms as a release one, on the same television, with one frame in the debug
#: build taking 4.3 seconds on its own. Both are signed with the same key, so one
#: installs over the other.
def _apk():
    made = os.path.join(ROOT, "android", "app", "build", "outputs", "apk")
    release = os.path.join(made, "release", "app-release.apk")
    debug = os.path.join(made, "debug", "app-debug.apk")
    if os.path.exists(release) and (not os.path.exists(debug)
                                    or os.path.getmtime(release) >= os.path.getmtime(debug)):
        return release
    return debug


APK = _apk()
DEST = os.path.join(ROOT, "static", "palladium.apk")
#: where a build installed on this machine keeps the files it serves. A phone on the
#: house network asks that server for updates, not this tree, so an APK published only
#: here is one nobody can install.
BUILT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Palladium", "static")


def aapt():
    sdk = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\build-tools")
    for ver in sorted(os.listdir(sdk), reverse=True):
        exe = os.path.join(sdk, ver, "aapt2.exe")
        if os.path.exists(exe):
            return exe
    return None


def version_from_apk(path):
    """Ask aapt2, and fall back to the gradle file if the tool is missing."""
    tool = aapt()
    if tool:
        try:
            out = subprocess.run([tool, "dump", "badging", path], capture_output=True,
                                 timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                 ).stdout.decode("utf-8", "replace")
            code = re.search(r"versionCode='(\d+)'", out)
            name = re.search(r"versionName='([^']+)'", out)
            if code and name:
                return int(code.group(1)), name.group(1)
        except Exception:
            pass
    gradle = open(os.path.join(ROOT, "android", "app", "build.gradle.kts"),
                  encoding="utf-8").read()
    code = re.search(r"versionCode\s*=\s*(\d+)", gradle)
    name = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    return int(code.group(1)) if code else 0, name.group(1) if name else "?"


#: Who is watching, straight from the running server. Loopback is always the owner,
#: so this needs no token.
def _watching():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/watching", timeout=5) as r:
            return json.load(r).get("live", []) or []
    except Exception:
        return []                      # server down: nobody is watching through it


#: Publishing is not a background action.
#:
#: The APK lands in the installed server's static folder, the in-app updater reads it
#: within the minute, and every app that sees a higher code relaunches itself. On
#: 2026-09-20 that threw a guest 28 minutes into a transcode back to the start of the
#: episode - the publish did it, not the install that followed. So the house is read
#: first, and --anyway is the way to say it does not matter this time.
def _house_is_clear(force):
    live = _watching()
    if not live or force:
        for r in live:
            print("  publishing over %s - %s, %s" %
                  (r.get("who"), r.get("title", ""), r.get("how", "")))
        return True
    print("not published: somebody is watching")
    for r in live:
        print("  %-8s %-34s %-18s %d/%ds" %
              (r.get("who"), (r.get("title") or "")[:34], r.get("how"),
               r.get("position", 0), r.get("duration", 0)))
    print("their app updates itself from this file and relaunches. "
          "Wait, or run with --anyway.")
    return False


def main():
    if not os.path.exists(APK):
        print("no APK built yet:", APK)
        return 1
    if not _house_is_clear("--anyway" in sys.argv):
        return 2
    code, name = version_from_apk(APK)
    # One number for both halves: the app and the server that ships with it are the
    # same release, and two version lines to read was two things to get wrong.
    try:
        with open(os.path.join(ROOT, "changes.json"), encoding="utf-8") as f:
            want = json.load(f)[0].get("version")
        if want and want != name:
            print("the app says %s and the changelog says %s - set versionName in "
                  "android/app/build.gradle.kts" % (name, want))
    except Exception:
        pass
    shutil.copy2(APK, DEST)
    info = {
        "versionCode": code,
        "versionName": name,
        # megabytes, not mebibytes: the number is shown beside a download and
        # compared against Content-Length, both of which count in millions
        "sizeMb": round(os.path.getsize(DEST) / 1_000_000, 1),
        "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(APK))),
    }
    with open(os.path.join(ROOT, "static", "version.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print("published %s (code %d), %.1f MB, built %s"
          % (info["versionName"], code, info["sizeMb"], info["built"]))
    if os.path.isdir(BUILT):
        try:
            shutil.copy2(DEST, os.path.join(BUILT, "palladium.apk"))
            with open(os.path.join(BUILT, "version.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
            print("handed to the installed server:", BUILT)
        except OSError as why:
            print("the installed server did not take it:", why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
