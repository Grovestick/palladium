#!/usr/bin/env python3
"""How many downloads, and from where - read back from the gate's own counters.

The Worker counts one line per download: the day, which file, the country Cloudflare
saw, and the browser and system as broad families. No address, no identifier, nothing
written to the machine that asked - a counter of "Sweden, Chrome, Windows, the
thirtieth" is not about anybody in particular, which is what keeps this side of the
GDPR without a consent banner or a retention policy.

    py pd-downloads.py              everything, newest day first
    py pd-downloads.py --days 7     the last week
    py pd-downloads.py --raw        the counters as they are stored
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join("beta", "wrangler.toml")


def wrangler(*args):
    """One wrangler call. The counters are named with bars, which a shell reads as a
    pipeline - so the command is written out as one line with the quoting cmd itself
    understands, rather than handed over as a list for Python to join."""
    line = " ".join(['"%s"' % a if any(c in a for c in "|&<>^ ") else a
                     for a in ["npx", "--yes", "wrangler@latest"] + list(args)])
    said = subprocess.run(line, cwd=HERE,
                          shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if said.returncode:
        print((said.stdout or "") + (said.stderr or ""))
        raise SystemExit("wrangler %s failed" % args[0])
    return said.stdout or ""


def counters():
    """Every download counter in the gate's store, as (parts, count)."""
    # "dl" rather than "dl|": a bar quoted for the shell reaches wrangler with the
    # quotes still on it and matches nothing, and every counter starts with dl anyway
    out = wrangler("kv", "key", "list", "--binding", "KEYS", "--config", CONFIG,
                   "--remote", "--prefix", "dl")
    # wrangler prints a JSON array, sometimes with a line of its own above it
    start = out.find("[")
    rows = json.loads(out[start:]) if start >= 0 else []
    for row in rows:
        name = row.get("name") or ""
        if not name.startswith("dl|"):
            continue
        # the name carries bars and is passed through a shell: quoted, or the shell
        # reads it as a pipeline and wrangler is handed the first word of it
        got = wrangler("kv", "key", "get", name, "--binding", "KEYS",
                       "--config", CONFIG, "--remote").strip()
        try:
            count = int(got.splitlines()[-1])
        except (ValueError, IndexError):
            continue
        yield name.split("|")[1:], count


def main(argv):
    days = 0
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    rows = list(counters())
    if not rows:
        print("nothing counted yet")
        return
    if "--raw" in argv:
        for parts, count in sorted(rows):
            print("%5d  %s" % (count, "  ".join(parts)))
        return

    seen = sorted({p[0] for p, _ in rows}, reverse=True)
    if days:
        seen = seen[:days]
    total = collections.Counter()
    for parts, count in rows:
        if parts[0] not in seen:
            continue
        day, what, country, browser, system = (parts + ["", "", "", "", ""])[:5]
        total["day " + day] += count
        total["file " + what] += count
        total["country " + country] += count
        total["browser " + browser] += count
        total["system " + system] += count

    def show(kind, title):
        rows = [(k[len(kind) + 1:], n) for k, n in total.items()
                if k.startswith(kind + " ")]
        if not rows:
            return
        print("\n%s" % title)
        for name, n in sorted(rows, key=lambda kv: -kv[1]):
            print("  %-16s %4d" % (name, n))

    print("downloads: %d" % sum(n for k, n in total.items() if k.startswith("file ")))
    show("file", "By file")
    show("day", "By day")
    show("country", "By country")
    show("browser", "By browser")
    show("system", "By system")


if __name__ == "__main__":
    main(sys.argv[1:])
