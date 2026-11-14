"""The boss: watches every background worker, judges what it sees, and acts.

Workers are of two kinds. LIVE ones serve somebody watching now and are never rested;
the rest are secondary and give way.

  stalled              logged once, counted in its Workers box; ok again is logged too
  failing again and again  secondary: rested for an hour. Live: logged, keeps running
  no progress for hours    logged once
  thread dead          started again, at most RESTARTS_PER_HOUR times an hour each
  disk turn held long  given back, so one stuck job does not block its drive for the others
  too many threads     logged once, with who has the most
  server under stress  secondary workers stop until it has been calm for CALM_FOR seconds:
                       a viewer short of disk, the processor or memory near full, too many threads

  stuck worker         the server restarted, when viewers get their service back
  weekly restart       set in Settings; missed asleep or hibernated, it goes on waking
  AI subtitle helper   a job silent for AI_STUCK stopped and put back; a failed one tried again
                       AI_TRIES times from where it stopped, then cleared; an answer cleared
                       after AI_CLEAR_AFTER; the helper started again if its thread has gone

A thread that is stuck but alive cannot be killed from inside the server, and a second one
beside it would do the work twice, so a restart is what clears it. The apps resume in place
or carry on from the other server. Never while the other server's state has just changed,
so one at a time when both are online; with the other offline it goes when it is needed.
"""
import datetime
import json
import os
import re
import threading
import time

import pd_beat
import pd_disk

#: seconds between rounds
EVERY = 10
#: processor and memory at or above these, in per cent, are stress
BUSY_CPU = 90
BUSY_MEMORY = 92
#: rounds in a row the processor has to be that busy: a burst is not stress
BUSY_ROUNDS = 3
#: seconds without stress before secondary workers go on again
CALM_FOR = 120
#: starts of one worker in an hour before the boss stops and says so
RESTARTS_PER_HOUR = 3
#: a background job's disk turn held this long is given back. Watching never waits for one
TURN_LIMIT = 3 * 3600
#: threads alive before it is reported: 427 hung the pages on 2026-11-01
MOST_THREADS = 300
#: needed by somebody watching now: what they asked for coming in, and the speed rules
LIVE = ("torrents", "speed")
#: separate failures before a secondary worker is rested, and for how long
FAILS_BEFORE_REST = 3
REST_FOR = 3600
#: a failure older than this no longer counts towards a rest
FAILS_FORGOTTEN = 2 * 3600
#: seconds with work left and none done before it is called no progress
NO_PROGRESS = 6 * 3600
#: seconds stalled before a restart is called for
LIVE_STUCK = 600
SECONDARY_STUCK = 2 * 3600
#: the AI subtitle helper: seconds a job may go without a word from its worker, how often a
#: failed job is tried again, and how long an answer or a given-up job stays on the list
AI_STUCK = 30 * 60
AI_TRIES = 1
AI_CLEAR_AFTER = 10 * 60
#: what a person did, not a fault
AI_BY_PERSON = ("stopped", "taken out of the queue")
#: seconds between restarts: a fault a restart does not cure must not become a loop
RESTART_EVERY = 6 * 3600
#: seconds between asking whether the other server is up
ASK_OTHER_EVERY = 60
#: seconds the other server must have been steady, online or offline: it may be restarting
SETTLED = 300
#: the copy server's extra wait, so the main server goes first when both are stuck
COPY_WAITS = 120
#: seconds a weekly restart waits for nobody to be watching before it goes anyway
WEEKLY_WAITS_FOR_IDLE = 6 * 3600
#: when this process started: an install or any restart after the set time counts as done.
#: Coming back from hibernating is the same process, so it does not
STARTED = time.time()
#: apps older than this cannot resume after a restart
RESUMES_FROM = (0, 18, 17)
#: kinds of turn the boss may give back
GIVEN_BACK = ("check", "work")

STATE = {"log": None, "watched": {}, "stalled": set(), "restarts": {}, "acted": 0, "crowded": False,
         "gaveUp": set(), "seen": {}, "rest": {}, "stress": "", "calmSince": 0.0, "busyRounds": 0,
         "short": None, "root": "", "viewers": None, "otherUp": None, "restart": None, "headStart": 0,
         "otherWas": None, "otherSince": 0.0, "blocked": "", "weekly": None, "started": STARTED,
         "ai": None}


def watch(name, thread, start):
    """A worker the boss may start again: its beat name, thread name and start()."""
    STATE["watched"][name] = {"thread": thread, "start": start}


def _say(text):
    if STATE["log"]:
        STATE["log"]("boss: " + text)


def kind_of(name):
    return "live" if name in LIVE else "secondary"


def rested(name, now=None):
    """Whether the boss has this worker stopped: rested for failing, or the server under
    stress and it is secondary. auto_on asks, so it stops as if turned off."""
    if STATE["stress"] and kind_of(name) != "live":
        return True
    return (time.time() if now is None else now) < STATE["rest"].get(name, 0)


def stressed():
    """Why the server is under stress, or ""."""
    return STATE["stress"]


def read_stress(short, cpu, memory, threads):
    """What is wrong now, or "". cpu is counted over rounds, so one burst says nothing."""
    STATE["busyRounds"] = STATE["busyRounds"] + 1 if cpu >= BUSY_CPU else 0
    if short:
        return "a viewer is short of disk"
    if memory >= BUSY_MEMORY:
        return "memory at %d%%" % memory
    if threads > MOST_THREADS:
        return "%d threads" % threads
    if STATE["busyRounds"] >= BUSY_ROUNDS:
        return "processor at %d%%" % cpu
    return ""


def judge_stress(why, now):
    """Under stress at once; out of it only after CALM_FOR seconds with none."""
    if why:
        if not STATE["stress"]:
            _say("server under stress, secondary workers stopped: %s" % why)
            STATE["acted"] += 1
        STATE["stress"], STATE["calmSince"] = why, 0.0
    elif STATE["stress"]:
        STATE["calmSince"] = STATE["calmSince"] or now
        if now - STATE["calmSince"] >= CALM_FOR:
            _say("calm again, secondary workers go on")
            STATE["stress"] = ""
    return STATE["stress"]


def needs_restart(status):
    """The worker stuck long enough to call for a restart, or "": sooner for a live one."""
    for name, b in status.items():
        if name == "boss" or b.get("ok", True):
            continue
        if int(b.get("for") or 0) >= (LIVE_STUCK if kind_of(name) == "live" else SECONDARY_STUCK):
            return name
    return ""


def clean_weekly(said):
    """A weekly restart as Settings sends it: {on, day 0 Monday to 6, time "HH:MM"}."""
    said = said if isinstance(said, dict) else {}
    try:
        day = max(0, min(6, int(said.get("day", 0))))
    except (TypeError, ValueError):
        day = 0
    got = re.match(r"^([01][0-9]|2[0-3]):([0-5][0-9])$", str(said.get("time") or ""))
    return {"on": bool(said.get("on")), "day": day, "time": got.group(0) if got else "04:00"}


def last_slot(weekly, now):
    """The latest set time at or before now, as a time stamp."""
    at = datetime.datetime.fromtimestamp(now)
    hour, minute = (int(n) for n in weekly["time"].split(":"))
    slot = (at - datetime.timedelta(days=(at.weekday() - weekly["day"]) % 7)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    if slot > at:
        slot -= datetime.timedelta(days=7)
    return slot.timestamp()


def weekly_due(weekly, started, now):
    """The set time this process has not been restarted since, as a time stamp, or 0."""
    if not (weekly and weekly.get("on")):
        return 0
    slot = last_slot(clean_weekly(weekly), now)
    return slot if started < slot else 0


def old_app(app):
    """Whether a viewer's app is too old to resume after a restart."""
    got = re.search(r"(\d+)\.(\d+)\.(\d+)", str(app or ""))
    return bool(got) and tuple(int(n) for n in got.groups()) < RESUMES_FROM


def restart_blocked(viewers, steady_for, last, now, head_start=0):
    """Why a restart may not go now, or "". Offline or online makes no difference to the
    other server's part: what stops it is that server having just changed."""
    if now - last < RESTART_EVERY:
        return "restarted under %d hours ago" % (RESTART_EVERY // 3600)
    if steady_for < SETTLED + head_start:
        return "the other server has just changed: one at a time"
    if any(old_app(r.get("app")) for r in viewers):
        return "a viewer on an old app cannot resume"
    return ""


def _paper():
    return os.path.join(STATE["root"], "boss.json")


def last_restart():
    """When the boss last restarted this server. Unreadable counts as just now: a loop of
    restarts is the worse mistake."""
    try:
        with open(_paper(), encoding="utf-8") as f:
            return float(json.load(f).get("restarted") or 0)
    except FileNotFoundError:
        return 0.0
    except (OSError, ValueError):
        return time.time()


def other_steady_for(up, now):
    """Seconds the other server has been as it is now, online or offline."""
    if up != STATE["otherWas"]:
        STATE["otherWas"], STATE["otherSince"] = up, now
    return now - STATE["otherSince"]


def check_restart(status, now):
    """A restart when a worker calls for one and nothing stands in its way. What it did."""
    if STATE["otherUp"] and now - STATE.get("otherAsked", 0.0) >= ASK_OTHER_EVERY:
        STATE["otherAsked"] = now
        STATE["otherNow"] = bool(STATE["otherUp"]())
    steady = other_steady_for(STATE.get("otherNow"), now) if STATE["otherUp"] else 0
    stuck = needs_restart(status)
    slot = weekly_due(STATE["weekly"]() if STATE["weekly"] else None, STATE["started"], now)
    if not (stuck or slot) or not STATE["restart"]:
        STATE["blocked"] = ""
        return ""
    viewers = STATE["viewers"]() if STATE["viewers"] else []
    why = restart_blocked(viewers, steady, last_restart(), now, STATE["headStart"])
    if not why and not stuck and viewers and now - slot < WEEKLY_WAITS_FOR_IDLE:
        why = "somebody is watching"
    stuck = stuck or "the weekly restart"
    if why:
        if why != STATE["blocked"]:
            STATE["blocked"] = why
            _say("%s calls for a restart, not yet: %s" % (stuck, why))
        return ""
    keep = _paper() + ".new"
    with open(keep, "w", encoding="utf-8") as f:
        json.dump({"restarted": now, "for": stuck}, f)
    os.replace(keep, _paper())
    _say("restarting this server: %s" % (stuck if slot and stuck == "the weekly restart" else stuck + " is stuck"))
    STATE["restart"]()
    return stuck


def _machine():
    """(processor, memory) in per cent, or (0, 0) where it cannot be read."""
    try:
        import psutil
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    except Exception:
        return 0, 0


def evaluate(status, now):
    """Each worker's behaviour judged: failing again and again, and work left with none
    done. What was done about it, as [(name, what)]."""
    did = []
    for name, b in status.items():
        if name == "boss":
            continue
        row = STATE["seen"].setdefault(name, {"failing": False, "fails": 0, "failAt": 0.0, "done": None,
                                              "doneAt": now, "told": False})
        failing = "failed" in str(b.get("step") or "")
        if now - row["failAt"] > FAILS_FORGOTTEN:
            row["fails"] = 0
        if failing and not row["failing"]:
            row["fails"] += 1
            row["failAt"] = now
        row["failing"] = failing
        if row["fails"] >= FAILS_BEFORE_REST:
            row["fails"] = 0
            if kind_of(name) == "live":
                _say("%s, needed for viewing, keeps failing: left running" % name)
                did.append((name, "told"))
            else:
                STATE["rest"][name] = now + REST_FOR
                STATE["acted"] += 1
                _say("%s keeps failing: rested for an hour" % name)
                did.append((name, "rested"))
        done, left = b.get("done"), b.get("left")
        if done != row["done"] or not left:
            row.update(done=done, doneAt=now, told=False)
        elif now - row["doneAt"] > NO_PROGRESS and not row["told"] and not rested(name, now):
            row["told"] = True
            _say("%s has %s left and has done none in %d hours" % (name, left, NO_PROGRESS // 3600))
            did.append((name, "no progress"))
    return did


def check_stalled(status):
    """Names newly stalled and newly recovered, from pd_beat.status()."""
    now_stalled = {n for n, b in status.items() if b.get("step") is not None and not b.get("ok", True)}
    new, back = now_stalled - STATE["stalled"], STATE["stalled"] - now_stalled
    STATE["stalled"] = now_stalled
    for name in sorted(new):
        _say("%s worker %s stalled on %s for %d s" % (kind_of(name), name, status[name].get("step"),
                                                       status[name].get("for") or 0))
    for name in sorted(back):
        _say("%s is working again" % name)
    return new, back


def check_threads(alive, now):
    """Watched workers whose thread has gone, started again. alive: names of live threads."""
    started = []
    for name, one in STATE["watched"].items():
        if one["thread"] in alive:
            continue
        times = [t for t in STATE["restarts"].get(name, []) if now - t < 3600]
        if len(times) >= RESTARTS_PER_HOUR:
            if name not in STATE["gaveUp"]:
                STATE["gaveUp"].add(name)
                _say("%s died %d times in an hour: left off" % (name, len(times)))
            STATE["restarts"][name] = times
            continue
        STATE["gaveUp"].discard(name)
        STATE["restarts"][name] = times + [now]
        try:
            one["start"]()
            started.append(name)
            STATE["acted"] += 1
            _say("%s had stopped: started again" % name)
        except Exception as why:
            _say("%s would not start: %s" % (name, str(why)[:120]))
    return started


def check_turns(held, now):
    """Background jobs' disk turns held past TURN_LIMIT given back. held: pd_disk.HELD."""
    gone = []
    for ident, one in list(held.items()):
        if one.get("kind") in GIVEN_BACK and now - float(one.get("since", now)) > TURN_LIMIT:
            pd_disk.done(ident)
            gone.append(ident)
            STATE["acted"] += 1
            _say("gave back a disk turn on %s held %d min: %s" % (
                one.get("drive"), (now - float(one["since"])) // 60, one.get("label")))
    return gone


def check_crowd(names):
    """Too many threads reported once, until it has eased. names: every live thread's name."""
    crowded = len(names) > MOST_THREADS
    if crowded and not STATE["crowded"]:
        counts = {}
        for n in names:
            key = n.rstrip("0123456789-_ ")
            counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        _say("%d threads alive, most: %s" % (len(names), top))
    STATE["crowded"] = crowded
    return crowded


#: a gap between rounds this long is the machine having slept, and the boss rests this long
#: after it: every clock it reads from has to be moved past the night first
SLEPT_AFTER = 120
AFTER_WAKING = 60


def just_woke(now):
    """Whether the machine has just woken. Disk turns are moved past the night here, and the
    beats by pd_beat within thirty seconds: read before that, a worker at rest reads as
    stuck for nine hours and the server was restarted at seven in the morning for it."""
    last = STATE.get("lastRound")
    STATE["lastRound"] = now
    if last and now - last > SLEPT_AFTER:
        STATE["wokeAt"] = now
        with pd_disk.LOCK:
            for one in pd_disk.HELD.values():
                if "since" in one:
                    one["since"] += now - last
    return now - STATE.get("wokeAt", 0.0) < AFTER_WAKING


def check_ai(helper, now):
    """The AI subtitle helper looked after: (stopped, tried again, cleared, revived).
    helper: pd_ai_subs, or None on a server without it."""
    if helper is None:
        return [], [], [], False
    stopped, again, cleared = [], [], []
    seen = helper.look()
    on = seen.get("on")
    if on and now - float(on.get("moved") or on.get("when") or now) > AI_STUCK:
        helper.stop(why="silent for %d min: stopped by the boss" % (AI_STUCK // 60))
        stopped.append(on.get("file"))
        STATE["acted"] += 1
        _say("ai subtitles: %s said nothing for %d min: stopped" % (
            on.get("file"), AI_STUCK // 60))
    with helper.LOCK:
        done = list(helper.STATE["done"])
    for job in done:
        if job.get("ok") or job.get("what") in AI_BY_PERSON:
            continue
        if not job.get("answer") and int(job.get("tries") or 0) < AI_TRIES:
            helper.retry(job)
            again.append(job.get("file"))
            STATE["acted"] += 1
            _say("ai subtitles: %s failed (%s): tried again from where it stopped" % (
                job.get("file"), str(job.get("what"))[:80]))
        elif now - float(job.get("finished") or now) > AI_CLEAR_AFTER:
            helper.clear(job)
            cleared.append(job.get("file"))
            _say("ai subtitles: %s cleared from the list: %s" % (job.get("file"), str(job.get("what"))[:80]))
    revived = helper.revive()
    if revived:
        STATE["acted"] += 1
        _say("ai subtitles: the helper had stopped with work waiting: started again")
    return stopped, again, cleared, revived


def one_round(now):
    if just_woke(now):
        return
    status = pd_beat.status()
    check_stalled(status)
    evaluate(status, now)
    names = [t.name for t in threading.enumerate()]
    check_threads(set(names), now)
    check_turns(pd_disk.HELD, now)
    check_crowd(names)
    cpu, memory = _machine()
    short = STATE["short"]() if STATE["short"] else False
    judge_stress(read_stress(short, cpu, memory, len(names)), now)
    check_restart(status, now)
    check_ai(STATE["ai"], now)


def tally():
    """(things the boss has done, workers stalled now) for its Workers box."""
    return STATE["acted"], len(STATE["stalled"])


def start(log, short=None, root="", viewers=None, other_up=None, restart=None, head_start=0, weekly=None,
          ai=None):
    """log(text) a line in the debug log; short() whether a viewer is short of disk; root
    where boss.json lives; viewers() who is watching here, with their apps; other_up()
    whether the other server answers; restart() this server ended and started again;
    head_start seconds more this one waits on the other; weekly() the weekly restart set;
    ai the AI subtitle helper (pd_ai_subs)."""
    STATE.update(log=log, short=short, root=root, viewers=viewers, otherUp=other_up, restart=restart,
                 headStart=head_start, weekly=weekly, ai=ai)

    def work():
        while True:
            try:
                one_round(time.time())
            except Exception as why:
                _say("round failed: %s" % str(why)[:120])
            pd_beat.sleep("boss", EVERY, "watching")

    pd_beat.counter("boss", tally)
    threading.Thread(target=work, name="palladium-boss", daemon=True).start()
