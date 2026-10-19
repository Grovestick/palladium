#!/usr/bin/env python
"""A fingerprint of what each test reads, so the build runs a test only when that
changed since its last pass - or when something went wrong that nobody explained.

    python pd-test-sig.py --same NAME   exit 0 when NAME's inputs match its last pass
    python pd-test-sig.py --keep NAME   writes NAME's fingerprint down as passed
    python pd-test-sig.py               prints every test's fingerprint and whether it is due

A test is due again whatever its fingerprint when fault.log or app-crash.log in the
data folder changed since its pass, or when the build asks for all (PD_ALL=1).
The version lines (pd_built.py, versionCode/versionName) change every build and are
left out.
"""
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEPT = os.path.join(HERE, ".tests-passed")
DATA = os.path.join(os.environ.get("APPDATA", ""), "Palladium")
ALARMS = ("fault.log", "app-crash.log")


def _python():
    return sorted(["pd-server.py"] + [p for p in glob.glob("pd_*.py") if p != "pd_built.py"])


def _web():
    return sorted(glob.glob("static/*.js") + glob.glob("static/*.css") + glob.glob("static/*.html"))


def _app():
    out = []
    for root, _, files in os.walk(os.path.join("android", "app", "src")):
        out += [os.path.join(root, f) for f in files if f.endswith((".kt", ".java", ".xml"))]
    return sorted(out) + [os.path.join("android", "app", "build.gradle.kts")]


TESTS = {
    "web": lambda: _web() + ["pd-test-web.js"],
    "types": lambda: _python() + ["pd-test-types.py"],
    "rules": lambda: _python() + ["pd-test-rules.py"],
    "library": lambda: _python() + ["pd-test-library.py"],
    "gate": lambda: _python() + ["pd-gate.py"],
    "app": _app,
}


def sig(name):
    h = hashlib.sha256()
    for path in TESTS[name]():
        try:
            with open(os.path.join(HERE, path), "rb") as f:
                body = f.read().replace(b"\r\n", b"\n")
        except OSError:
            body = b"missing"
        if path.endswith("build.gradle.kts"):
            body = re.sub(rb"(?m)^\s*version(Code|Name)\s*=.*$", b"", body)
        h.update(path.replace(os.sep, "/").encode() + b"\0" + body + b"\0")
    return h.hexdigest()


def alarms():
    """What each alarm file says, without the "---- started" line every server start
    writes to fault.log: on its time, every install made every test due again."""
    out = {}
    for name in ALARMS:
        try:
            with open(os.path.join(DATA, name), "rb") as f:
                said = [ln for ln in f.read().splitlines() if not ln.startswith(b"---- started")]
            out[name] = hashlib.sha256(b"\n".join(said)).hexdigest()
        except OSError:
            out[name] = ""
    return out


def _kept():
    try:
        with open(KEPT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def same(name):
    if os.environ.get("PD_ALL") == "1":
        return False
    was = _kept().get(name) or {}
    return was.get("sig") == sig(name) and was.get("alarms") == alarms()


def keep(name):
    kept = _kept()
    kept[name] = {"sig": sig(name), "alarms": alarms()}
    with open(KEPT, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=1)


if __name__ == "__main__":
    os.chdir(HERE)
    if len(sys.argv) == 3 and sys.argv[1] in ("--same", "--keep") and sys.argv[2] in TESTS:
        if sys.argv[1] == "--keep":
            keep(sys.argv[2])
        else:
            sys.exit(0 if same(sys.argv[2]) else 1)
    else:
        for n in TESTS:
            print(n, "unchanged" if same(n) else "due")
