"""The type check: pyright over pd-server.py and pd_*.py (pyrightconfig.json), zero findings.

    python pd-test-types.py

It reads every function for names that do not exist, names used before they are set, a
function defined twice, a call with the wrong arguments, an attribute a module or class
does not have. Four rules are off - the ones that only say an untyped dictionary might
hold None - and are named in pyrightconfig.json.

pyright lives in .typecheck, beside the web tests' own tools:

    npm install --prefix .typecheck pyright@1.1.414
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, ".typecheck", "node_modules", "pyright", "index.js")


def main():
    if not os.path.exists(TOOL):
        print("TYPES NOT RUN: pyright is not installed here")
        print("  npm install --prefix .typecheck pyright@1.1.414")
        return 1
    try:
        ran = subprocess.run(["node", TOOL, "-p", os.path.join(HERE, "pyrightconfig.json"),
                              "--outputjson"], cwd=HERE, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600)
        said = json.loads(ran.stdout)
    except Exception as e:
        print("TYPES NOT RUN: %s: %s" % (type(e).__name__, str(e)[:120]))
        return 1
    found = [d for d in said.get("generalDiagnostics") or [] if d.get("severity") in ("error", "warning")]
    files = (said.get("summary") or {}).get("filesAnalyzed", 0)
    for d in sorted(found, key=lambda d: (d["file"], d["range"]["start"]["line"])):
        print("  %s:%d  %s  %s" % (os.path.basename(d["file"]), d["range"]["start"]["line"] + 1,
                                    (d.get("rule") or "")[6:],
                                    " ".join(str(d.get("message") or "").split())[:160]))
    # a file the checker did not open is a file it said nothing about
    if files < 30:
        print("TYPES FAILED: only %d files were read" % files)
        return 1
    if found:
        print("TYPES FAILED: %d in %d files" % (len(found), files))
        return 1
    print("TYPES PASSED: %d files, pyright %s" % (files, said.get("version", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
