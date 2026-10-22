"""How often does the coach alert over a whole record? A live replay.

Alert fatigue is measured, not assumed: every day of a participant's record
is replayed in order and everything due that day - each one-off event, each
condition when it starts and again on the day the coach asked to look at it -
is reviewed by the coach in one continuing conversation, exactly as the
Monitor tab does, and its Alert decision is kept. All state (conversation, workspace, run, audit) lives in
the output folder, so the application's own state is untouched.

    ../.venv/bin/python experiment_alerts.py p16      # one participant per process
    ../.venv/bin/python experiment_alerts.py --report # after the runs

Output: ../evidence/monitoring/replay-r1/<pid>.jsonl (one review per line)
and state/ (the full audit records of every review).
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import sys
import time

import anyio

OUT = pathlib.Path(os.environ.get("ALERT_REPLAY_OUT") or
                   pathlib.Path(__file__).resolve().parent.parent / "evidence" / "monitoring" / "replay-r1")


def isolate(pid: str) -> None:
    import agent
    import audit
    import watch
    import workspace
    state = OUT / "state" / pid
    agent.SESSIONS_DIR, audit.LOG_DIR = state / "sessions", state / "audit"
    watch.STATE_DIR, workspace.ROOT = state / "watch", state / "workspace"


async def replay(pid: str) -> None:
    isolate(pid)
    import agent
    import watch
    from profile import Profile

    p = Profile(pid)
    days = (p.today - p.first_day).days + 1
    session = agent.Session(pid, p).load()
    dest = OUT / f"{pid}.jsonl"
    dest.unlink(missing_ok=True)
    try:
        for day in (p.first_day + dt.timedelta(days=i) for i in range(days)):
            for why, sig in watch.due(p, day, watch.run_state(pid)):
                started = time.monotonic()
                try:
                    r = await agent.review_signal(session, sig)
                    row = {"review": r["review"], "notification": r["notification"],
                           "reply": r["reply"], "usage": r.get("usage")}
                except Exception as exc:      # recorded, and the replay goes on
                    row = {"error": f"{type(exc).__name__}: {exc}"}
                row = {"day": str(day), "at": sig.at.isoformat(sep=" ") if sig.at else None,
                       "kind": sig.kind, "due": why, "severity": round(sig.severity, 2), "summary": sig.summary,
                       "seconds": round(time.monotonic() - started, 1), **row}
                with dest.open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                n = row.get("notification") or {}
                print(f"{pid} {row['at']} {sig.kind:<12} {why:<7} {n.get('verdict', 'ERROR'):<11} "
                      f"{'ALERT' if n.get('notify') else '-':<5} {row['seconds']}s", flush=True)
    finally:
        await session.close()


def report() -> None:
    for f in sorted(OUT.glob("p??.jsonl")):
        rows = [json.loads(line) for line in f.read_text().splitlines()]
        ok = [r for r in rows if "notification" in r]
        alerts = [r for r in ok if r["notification"]["notify"]]
        first, last = rows[0]["day"], rows[-1]["day"]
        months = max(1, (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days / 30.4)
        print(f"\n{f.stem}: {len(rows)} signals reviewed, {len(rows) - len(ok)} failed, "
              f"{len(alerts)} Alerts ({len(alerts) / months:.1f} per month over {months:.1f} months)")
        by = collections.Counter((r["kind"], r["notification"]["notify"]) for r in ok)
        for kind in sorted({k for k, _ in by}):
            print(f"  {kind:<13} reviewed {by[(kind, True)] + by[(kind, False)]:>3}  alerted {by[(kind, True)]:>3}")
        for r in alerts:
            print(f"  ALERT {r['at']} {r['kind']}: {r['notification']['reason'][:150]}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        anyio.run(replay, sys.argv[1])
