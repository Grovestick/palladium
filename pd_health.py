"""The house at a glance: both servers, the downloads, the disks, the backup and the faults,
and from them the few things that need somebody. What every fault this week was visible
in before anybody noticed it, on one page.
"""

import time

#: a torrent in one of these is stopped on something rather than waiting on a swarm
HALTED = ("error", "missingFiles")
#: reading its files for longer than this is a check that has hung
CHECK_HANGS_S = 15 * 60
#: below this much room a download drive is close to refusing the next film
LOW_GB = 20
#: Windows' disk events that say a disk could not give back or keep what it holds
DISK_FAULTS = {7: "bad block", 51: "paging error", 153: "retried read", 157: "surprise removal"}
#: and a drive that keeps the library, below this
KEEP_LOW_GB = 50
#: a backup older than this did not run last evening
BACKUP_OLD_S = 30 * 3600


def one_each(machines):
    """One row a machine: one filed under two addresses is up when either answered."""
    out = []
    for m in machines:
        had = next((x for x in out if x.get("name") == m.get("name")), None)
        if had is None:
            out.append(m)
        elif m.get("up") and not had.get("up"):
            out[out.index(had)] = m
    return out


def disk_faults(events, letters):
    """[{drive, count, kinds}] from Windows' disk events of the last day: `events` as
    [(event id, message)], `letters` {disk number: "C:"}. A disk with none is left out."""
    import re
    per = {}
    for ident, message in events:
        if int(ident) not in DISK_FAULTS:
            continue
        m = re.search(r"Harddisk(\d+)|Disk (\d+)", str(message))
        if not m:
            continue
        disk = int(m.group(1) or m.group(2))
        one = per.setdefault(disk, {"drive": letters.get(disk) or "disk %d" % disk, "count": 0, "kinds": []})
        one["count"] += 1
        if DISK_FAULTS[int(ident)] not in one["kinds"]:
            one["kinds"].append(DISK_FAULTS[int(ident)])
    return sorted(per.values(), key=lambda d: -d["count"])


def problems(facts, now=None):
    """What needs somebody, as short lines; empty when all is well.

    facts: {"machines": [{name, build, up}], "torrents": [{name, state, since}],
            "disks": [{drive, freeGB, downloads}], "backup": {at, ok} | None,
            "faults": int, "workers": [{name, state}]}"""
    now = time.time() if now is None else now
    out = []
    machines = facts.get("machines") or []
    for m in machines:
        if not m.get("up"):
            out.append("%s is not answering" % m.get("name"))
    builds = {m.get("build") for m in machines if m.get("up") and m.get("build")}
    if len(builds) > 1:
        out.append("the servers run different versions: " +
                   ", ".join("%s %s" % (m.get("name"), m.get("build")) for m in machines if m.get("up")))
    for t in facts.get("torrents") or []:
        state = str(t.get("state") or "")
        if state in HALTED:
            out.append("%s: qBittorrent stopped on it (%s)" % (t.get("name"), state))
        elif t.get("stuck"):
            out.append("%s: stuck at 100%%" % t.get("name"))
        elif (state.startswith("checking") and t.get("since") is not None
              and now - float(t["since"]) > CHECK_HANGS_S):
            out.append("%s: its check has not moved for %d min"
                       % (t.get("name"), (now - float(t["since"])) // 60))
    for d in facts.get("disks") or []:
        if d.get("downloads") and float(d.get("freeGB") or 0) < LOW_GB:
            out.append("%s has %d GB free for downloads" % (d.get("drive"), d.get("freeGB") or 0))
        elif not d.get("downloads") and float(d.get("freeGB") or 0) < KEEP_LOW_GB:
            # the drives that keep: a pack's picked film lands there too
            out.append("%s has %d GB free" % (d.get("drive"), d.get("freeGB") or 0))
    # a disk failing: Windows logged it giving back bad blocks or retrying reads in a day
    for d in facts.get("diskFaults") or []:
        if d.get("count"):
            out.append("%s reported %d disk fault%s in a day (%s) - back it up and plan to replace it"
                       % (d.get("drive") or "a disk", d["count"], "" if d["count"] == 1 else "s",
                          ", ".join(sorted(set(d.get("kinds") or [])))))
    backup = facts.get("backup")
    if not backup:
        out.append("no backup has run")
    elif not backup.get("ok"):
        out.append("the last backup failed")
    elif now - float(backup.get("at") or 0) > BACKUP_OLD_S:
        out.append("the backup has not run since %s" % time.strftime("%d %b", time.localtime(backup["at"])))
    tracker = facts.get("tracker") or {}
    if tracker.get("cookie") and tracker.get("expired"):
        out.append("the tracker session has expired and the indexer could not sign in again: "
                   "searches and the account figures stop")
    if int(facts.get("faults") or 0):
        out.append("%d fault report%s open" % (facts["faults"], "" if facts["faults"] == 1 else "s"))
    for w in facts.get("workers") or []:
        if w.get("state") == "stalled":
            out.append("%s has stalled" % w.get("name"))
    return out
