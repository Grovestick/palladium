#!/usr/bin/env python3
"""Summarise palladium/debug.log: when the control bar showed/hid and what caused it."""
import json, math, os, sys

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log")
ev = []
for line in open(path, encoding="utf-8"):
    try:
        ev.append(json.loads(line))
    except Exception:
        pass
pages = {}
for e in ev:
    pages.setdefault(e.get("page", "?"), []).append(e)
if len(pages) > 1:
    print(f"!! {len(pages)} separate pages are logging: "
          + ", ".join(f"{k} ({len(v)} entries)" for k, v in pages.items()))
    newest = max(pages.values(), key=lambda v: v[-1].get("t", 0))
    print(f"   analysing only the busiest one")
    print("")
    ev = max(pages.values(), key=len)
ev.sort(key=lambda e: e.get("t", 0))
if not ev:
    print("log is empty")
    sys.exit()

span = (ev[-1]["t"] - ev[0]["t"]) / 1000.0
print(f"{len(ev)} entries over {span:.0f}s\n")

bar = [e for e in ev if e.get("event") in ("show", "hide")]
print("control bar timeline:")
for i, e in enumerate(bar):
    prev = bar[i - 1] if i else None
    gap = f"+{(e['t'] - prev['t'])/1000:5.1f}s" if prev else "   start"
    d = ""
    if e["event"] == "show" and prev and prev.get("ptr") and e.get("ptr") \
       and prev["ptr"][0] is not None and e["ptr"][0] is not None:
        d = f" moved={round(math.dist(prev['ptr'], e['ptr']))}px"
    if e["event"] == "show":
        print(f"  {gap}  SHOW  why={e.get('why'):<9} paused={e.get('paused')}{d}")
    else:
        print(f"  {gap}  hide  hard={e.get('hard')} paused={e.get('paused')}")

rate = [e for e in ev if e.get("event") == "mousemove-rate"]
if rate:
    print(f"\nmouse traffic while idle ({len(rate)} samples):")
    print(f"  events/sec: max {max(r['count'] for r in rate)}, "
          f"total {sum(r['count'] for r in rate)}")
    print(f"  largest single jump: {max(r['maxDelta'] for r in rate)}px")
    for r in rate[:12]:
        print(f"    t={r['t']:>6}  {r['count']:>3} moves, max delta {r['maxDelta']}px")
else:
    print("\nno mouse movement recorded at all")

evs = {}
for e in ev:
    if e.get("event") == "evt":
        evs[e["type"]] = evs.get(e["type"], 0) + 1
print("\nother player events:", evs or "none")
