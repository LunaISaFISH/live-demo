"""
The monitor, run as it would actually run.

`monitor.scan` answers "what would have fired over this window", which is
the right shape for evaluating a detector and the wrong shape for
understanding the system. A real monitor does not see a window. It wakes
up on a day, sees only what has happened so far, decides whether to say
anything, and goes back to sleep.

This replays a record one day at a time and does exactly that. Nothing
here is a simulation in the sense of made-up data - every day is a real
day from PMData, walked in order, with the record truncated to that day.
What is simulated is *time passing*, which is the only thing a five-month
export cannot supply on its own.

    for each day:
        detect      code, deterministic, sees only days <= today
        de-duplicate a condition that held yesterday is not news today
        review       hand the signal to the coach as an injected turn
        decide       the coach says whether the person should hear it now

The two halves fail differently and that is the point. Layer one is
tuned to miss nothing, so it fires on the top decile **by construction** -
about one day in ten looks unusual whether or not anything is wrong.
Layer two is the only half that can look at *why*: a heart rate in the
97th percentile the morning after three hours' lost sleep is a body doing
what bodies do, and a short, steep rise at rest with nothing around it
may be the one thing worth saying today.

    ../.venv/bin/python watch.py p05          # replay, review with the agent
    ../.venv/bin/python watch.py p05 --dry    # layer one only, no model
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import monitor
from profile import Profile

# =====================================================================
# The state of a simulated monitoring run
# =====================================================================
# A browser-driven run has no fixed window - the person clicks "Advance
# to next review" again and again, each click scanning more days. The run
# is therefore state: which days it has scanned, which signals the coach
# has reviewed (each once), which Alerts it raised, and for each continuing
# condition its reviews and the day the coach asked to look at it again.
# It survives across requests in a small per-participant file and Reset
# clears it.

STATE_DIR = pathlib.Path(__file__).parent / ".watch"


def _fresh_run() -> dict:
    return {"start_day": None, "through_day": None, "scanned_days": 0,
            "alerts": [],        # what the person was told in this run
            "reviewed": {},      # signal_key -> decision, one per signal per run
            "conditions": {}}    # kind -> {started, reviews, next_review}


def signal_key(sig) -> str:
    """One Candidate Signal's identity: when it became available, and its kind."""
    at = sig.at.isoformat(sep=" ") if sig.at else str(sig.day)
    return f"{at}|{sig.kind}"


def run_state(pid: str) -> dict:
    f = STATE_DIR / f"{pid}.json"
    if f.exists():
        # Merge over the defaults so a state file written before a field
        # existed still loads with every key.
        return {**_fresh_run(), **json.loads(f.read_text())}
    return _fresh_run()


def _save_run(pid: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{pid}.json").write_text(json.dumps(state))


def extend_run(pid: str, start: dt.date, through: dt.date) -> dict:
    """Record that the run scanned the days from `start` to `through`.

    The state carries the run's identity - its first day and the cursor
    it has scanned through - so the operation is idempotent and cannot
    inherit an unrelated run:

      * an interval already inside the run (an exact HTTP retry) changes
        nothing;
      * an advance starting on or inside the run adds only the days past
        the cursor;
      * a start outside the run (an earlier date, or a jump past the
        cursor) begins a new run.
    """
    state = run_state(pid)
    s0, t0 = state["start_day"], state["through_day"]
    if s0 is None or not s0 <= str(start) <= t0:
        state = {**_fresh_run(), "start_day": str(start),
                 "through_day": str(through),
                 "scanned_days": max(0, (through - start).days)}
    elif str(through) > t0:
        state["scanned_days"] += (through - dt.date.fromisoformat(t0)).days
        state["through_day"] = str(through)
    _save_run(pid, state)
    return state


def record_review(pid: str, decision: dict, sig) -> dict:
    """Keep one reviewed signal's decision in the run, so it is never
    reviewed twice and later reviews know which Alerts were raised."""
    state = run_state(pid)
    key = signal_key(sig)
    state["reviewed"][key] = {"notify": decision["notify"], "verdict": decision["verdict"]}
    if decision["notify"]:
        state["alerts"].append({"key": key, "kind": sig.kind, "summary": sig.summary,
                                "reason": decision["reason"], "measure": decision.get("measure")})
    if decision.get("condition"):
        # A continuing condition keeps its reviews while it lasts, and comes
        # back when the coach asked to look again; a new onset starts afresh.
        started = decision["condition"]["started"]
        c = state["conditions"].get(sig.kind)
        if not c or c["started"] != started:
            c = state["conditions"][sig.kind] = {"started": started, "reviews": []}
        c["reviews"].append({"day": str(sig.day), "summary": sig.summary,
                             "verdict": decision["verdict"], "alert": decision["notify"],
                             "reason": decision["reason"],
                             "recheck_in_days": decision.get("recheck_in_days")})
        days = decision.get("recheck_in_days")
        c["next_review"] = str(sig.day + dt.timedelta(days=days)) if days else None
    _save_run(pid, state)
    return {**decision, "run": summary(state)}


def summary(state: dict) -> dict:
    """The run as the page and the audit show it."""
    return {"start_day": state["start_day"], "through_day": state["through_day"],
            "scanned_days": state["scanned_days"], "alerts": len(state["alerts"]),
            "reviewed": len(state["reviewed"])}


def reset_run(pid: str) -> None:
    (STATE_DIR / f"{pid}.json").unlink(missing_ok=True)


def replay(p: Profile, days: int = 28, upto: dt.date | None = None):
    """Walk a record forward one day at a time.

    Yields `(day, signals)` for the days something fired, having already
    dropped repeats of a condition that was already firing. The record is
    never consulted past `day`, which is what makes this a replay rather
    than a review of a window - and it is checked, because reading one
    day ahead would make every verdict here worthless.
    """
    if p.today is None:
        return
    if upto is not None:
        p = p.snapshot(upto)
    start = max(p.first_day, p.today - dt.timedelta(days=days - 1))
    firing: set[str] = set()
    day = start
    while day <= p.today:
        found = monitor.signals_on(p, day)
        kinds = {s.kind for s in found}
        fresh = [s for s in found if s.kind not in firing]
        firing = kinds
        if fresh:
            yield day, fresh
        day += dt.timedelta(days=1)


def due(p: Profile, day: dt.date, state: dict) -> list[tuple[str, "monitor.Signal"]]:
    """What the coach should review on `day`, with why.

    Every event (an intraday signal) is reviewed when it happens. A
    continuing condition is reviewed when it starts - or when the run first
    sees it, if it began before the run - and again on the day the coach
    asked to look at it, for as long as it lasts. No rule here judges
    whether it got better or worse: that is for the coach, from the history.
    """
    view = p.snapshot(day)
    out = []
    for sig in monitor.signals_on(view, day):
        if signal_key(sig) in state["reviewed"]:
            continue
        if monitor.is_event(sig):
            out.append(("event", sig))
            continue
        c = state["conditions"].get(sig.kind)
        started = monitor.condition_facts(view, sig)["started"]
        if not c or c["started"] != started:
            out.append(("new", sig))
        elif c.get("next_review") and c["next_review"] <= str(day):
            out.append(("recheck", sig))
    # An intraday event happens before a daily aggregate is available.
    return sorted(out, key=lambda r: (r[1].at or dt.datetime.max, -r[1].severity))


def next_due(p: Profile, after: dt.date, state: dict):
    """The first day from `after` on with something for the coach to review.

    Each detector sees a physical snapshot ending on the day being scanned,
    so time advances without giving the detector or the coach future data.
    """
    day = after
    # Linear replay is enough for a five-month teaching dataset;
    # index detector transitions if this ever fronts multi-year records.
    while day <= p.today:
        found = due(p, day, state)
        if found:
            return day, found
        day += dt.timedelta(days=1)
    return None, []


async def watch(p: Profile, days: int = 28, model: str | None = None):
    """Replay and review: the whole pipeline, in order."""
    import agent as coach

    session = coach.Session(p.pid, p).load()
    scanned = min(days, (p.today - p.first_day).days + 1)
    out = []
    try:
        for day, signals in replay(p, days):
            for sig in signals:
                r = await (coach.review_signal(session, sig, model) if model
                           else coach.review_signal(session, sig))
                out.append((sig, {**r["review"], **(r["notification"] or {})}))
    finally:
        await session.close()
    return {"pid": p.pid, "scanned": scanned, "raised": len(out),
            "spoken": sum(bool(v.get("notify")) for _, v in out), "reviews": out}


def main() -> None:
    import sys

    pid = next((a for a in sys.argv[1:] if a.startswith("p")), "p05")
    p = Profile(pid)
    print(f"replaying {pid}: {p.first_day} to {p.today}, last 28 days\n")

    if "--dry" in sys.argv:
        n = 0
        for day, signals in replay(p):
            for s in signals:
                n += 1
                print(f"  {s}")
        print(f"\n  {n} signals raised by layer one, unreviewed")
        return

    import anyio

    async def go():
        res = await watch(p)
        for sig, v in res["reviews"]:
            print(f"  [{sig.day}] {sig.kind}")
            print(f"     {sig.summary}")
            mark = "ALERT" if v.get("notify") else "no alert"
            print(f"     -> {v['verdict']}, {mark}")
            print(f"     {v.get('note', v.get('reason', ''))[:180]}\n")
        print(f"  {res['scanned']} days replayed, {res['raised']} signals "
              f"reviewed, {res['spoken']} reached the user")

    anyio.run(go)


if __name__ == "__main__":
    main()
