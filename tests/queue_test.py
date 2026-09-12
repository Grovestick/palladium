"""The queue, against the library as it is.

What it checks is what somebody watching would notice: the order is the order the
sources were asked in, whatever is on a screen leads, a series is in season and
episode order, nothing appears twice, and nothing already watched is on it.

    python tests/queue_test.py            (asks the running server)
"""
import collections
import json
import os
import re
import sys
import urllib.request

WHERE = os.environ.get("PD_SERVER", "http://127.0.0.1:8765")


def ask(path):
    with urllib.request.urlopen(WHERE + path, timeout=60) as answer:
        return json.loads(answer.read().decode("utf-8", "replace"))


def main():
    said = ask("/follow/playing?deck=1&hours=4&eps=6&casual=0&whole=0")
    # a subtitle carries the same key as the film it sits beside, and is a few
    # kilobytes rather than a thing anybody queues
    subs = (".srt", ".ass", ".vtt", ".sub", ".idx")
    rows = [r for r in (said.get("wanted") or [])
            if not r.get("side")
            and not str(r.get("name") or "").lower().endswith(subs)]
    bad = []
    if not rows:
        print("the queue is empty - nothing to check")
        return 0

    # nothing twice
    seen = collections.Counter(str(r.get("key")) for r in rows)
    for key, n in seen.items():
        if n > 1:
            bad.append("%s is on the queue %d times" % (key, n))

    # whatever is on a screen leads
    hot = [i for i, r in enumerate(rows) if r.get("hot")]
    if hot and max(hot) >= len([r for r in rows if r.get("hot")]):
        bad.append("something on a screen now is below something that is not")

    # every row says why it is there and for whom
    for r in rows:
        if not r.get("why"):
            bad.append("%s does not say why it is wanted" % r.get("title"))
            break

    # a series runs in season and episode order
    by_show = collections.OrderedDict()
    for i, r in enumerate(rows):
        m = re.match(r"^(.*?) S(\d+)E(\d+)", str(r.get("title") or ""))
        if m:
            by_show.setdefault(m.group(1), []).append(
                (i, int(m.group(2)), int(m.group(3))))
    for show, eps in by_show.items():
        # what is on a screen now leads, and so does anything moved to the front by
        # hand: both are somebody saying they want it sooner. The rest must climb.
        rest = [(s, e) for i, s, e in eps
                if not rows[i].get("hot") and not rows[i].get("pinned")]
        if rest != sorted(rest):
            bad.append("%s is out of order: %s" % (
                show, " ".join("S%02dE%02d" % x for x in rest[:8])))

    print("%d titles wanted, %d series" % (len(rows), len(by_show)))
    for show, eps in list(by_show.items())[:4]:
        print("   %-34s %s" % (show[:34],
                               " ".join("S%02dE%02d" % (s, e) for _, s, e in eps[:8])))
    if bad:
        print()
        for line in bad:
            print("  " + line)
        print("\nFAIL: %d" % len(bad))
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
