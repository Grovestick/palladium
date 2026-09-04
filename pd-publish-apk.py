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
APK = os.path.join(ROOT, "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
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


def main():
    if not os.path.exists(APK):
        print("no APK built yet:", APK)
        return 1
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
