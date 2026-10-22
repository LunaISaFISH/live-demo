"""
The body signal light. Layer one: code, and only code.

This is the background half of the system. Nobody asked it anything; it
walks a record day by day and emits candidate signals. What it does
*not* do is decide whether any of them is worth saying - that is layer
two (the agent's signal review, driven by `watch.py`), where the coach
looks at the surrounding history and decides whether the person should
hear about it now.

Two rules constrain everything here, and both are inherited rather than
invented for this file.

**Only facts about the past.** The project tried forecasting twice and
dropped it both times on evidence, so no signal here is
allowed to be a prediction. "Your resting heart rate is in the top
decile of your own month" is a description. "You are at risk of injury"
is not, and would be the same withdrawn claim under a new name. Every
detector below reports a position, a streak, or an absence.

**Personal, not population.** A signal fires against this person's own
recent distribution, which is why the same code says nothing about a
runner with a resting heart rate of 45 and speaks up for one at 70.

Alert fatigue is handled where the judgement is: a condition that keeps
firing is passed on once, when it starts, and the coach is told which
Alerts it has already raised in the run.

Run:  ../.venv/bin/python monitor.py
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

import guidelines as gl
import realtime
from profile import Profile, hr_position, wellness_words

# Every remaining detector branch value also lives in the threshold
# registry with its unit, why and evidence class; module attributes are
# kept (and read at call time) so sensitivity sweeps can move them.
QUIET_DAYS = int(gl.value("quiet_days"))
LOW_READINESS_RUN = int(gl.value("low_readiness_streak"))
INACTIVE_WEEKS = int(gl.value("inactive_weeks"))

# Intraday detector branch values, from the threshold registry so each
# carries a unit, a why and an evidence class. Kept as module attributes
# (read at call time) so sensitivity sweeps can move them.
INTRADAY_BUCKET_MINUTES = int(gl.value("intraday_bucket_minutes"))
INTRADAY_HR_DELTA = gl.value("intraday_hr_delta")
INTRADAY_LOW_STEPS = gl.value("intraday_low_steps")
INTRADAY_RUN_BUCKETS = int(gl.value("intraday_run_buckets"))


@dataclass
class Signal:
    """One observation, with everything needed to judge it later."""
    day: dt.date
    kind: str
    summary: str          # plain enough to show a user unedited
    severity: float       # 0-1, for ranking within the budget only
    evidence: dict = field(default_factory=dict)
    at: dt.datetime | None = None
    scope: str = "daily"    # daily aggregates become available at end-of-day

    def __str__(self) -> str:
        return f"{self.day}  {self.kind:<16} {self.summary}"


# =====================================================================
# Detectors - each answers "is today unusual for this person", no more
# =====================================================================

def _intraday_state(p: Profile, day: dt.date,
                    until: dt.datetime | None = None) -> dict | None:
    baselines = [v for d, v in p.resting_hr.items()
                 if day - dt.timedelta(days=28) <= d <= day]
    if not baselines:
        return None
    resting = p.resting_hr.get(day, statistics.median(baselines))
    threshold = resting + INTRADAY_HR_DELTA
    sessions = [(s.start, s.start + dt.timedelta(minutes=s.minutes))
                for s in p.sessions if s.date == day and s.start]
    points = []
    for point in realtime.day_window(p.pid, day, INTRADAY_BUCKET_MINUTES)["points"]:
        at = dt.datetime.fromisoformat(point["at"])
        if until is not None and at >= until:
            continue
        in_session = any(start <= at < end for start, end in sessions)
        qualifies = (point["heart_bpm"] is not None
                     and point["heart_bpm"] >= threshold
                     and point["steps"] is not None
                     and point["steps"] <= INTRADAY_LOW_STEPS
                     and not in_session)
        points.append({**point, "qualifies": qualifies, "in_logged_session": in_session})
    return {"resting_bpm": round(resting, 1), "threshold_bpm": round(threshold, 1),
            "points": points}


def _intraday_hr(p: Profile, day: dt.date) -> Signal | None:
    state = _intraday_state(p, day)
    if not state:
        return None
    run = []
    for point in state["points"]:
        run = [*run, point] if point["qualifies"] else []
        if len(run) < INTRADAY_RUN_BUCKETS:
            continue
        window = run[-INTRADAY_RUN_BUCKETS:]
        mean_hr = statistics.mean(p["heart_bpm"] for p in window)
        steps = sum(p["steps"] or 0 for p in window)
        start = dt.datetime.fromisoformat(window[0]["at"])
        duration = INTRADAY_BUCKET_MINUTES * INTRADAY_RUN_BUCKETS
        detected_at = (dt.datetime.fromisoformat(window[-1]["at"])
                       + dt.timedelta(minutes=INTRADAY_BUCKET_MINUTES))
        return Signal(
            day, "intraday_hr",
            f"heart rate averaged {mean_hr:.0f} bpm for {duration} minutes from "
            f"{start:%H:%M}, with {steps} recorded steps and no overlapping "
            f"logged exercise; daily resting reference {state['resting_bpm']:.0f} bpm",
            severity=min(1.0, (mean_hr - state["resting_bpm"]) / 60),
            evidence={"mean_hr_bpm": round(mean_hr, 1), "steps": steps,
                      "duration_minutes": duration,
                      "resting_reference_bpm": state["resting_bpm"],
                      "threshold_bpm": state["threshold_bpm"],
                      "heart_confidence":
                          f">={gl.value('intraday_hr_confidence'):.0f}",
                      "logged_exercise": False},
            at=detected_at, scope="intraday")
    return None

def _resting_hr(p: Profile, day: dt.date) -> Signal | None:
    hr = p.resting_hr_position(day)
    if not hr or hr["quantile"] < gl.value("hr_alert_quantile"):
        return None
    return Signal(day, "resting_hr",
                  f"resting heart rate {hr['value']:.1f} bpm, "
                  f"{hr['value'] - hr['baseline_median']:+.1f} bpm from your median of "
                  f"{hr['baseline_median']:.1f}; {hr_position(hr)} your own last "
                  f"{hr['baseline_days']} days",
                  severity=hr["quantile"], evidence={"resting_hr": hr})


def _readiness_run(p: Profile, day: dt.date) -> Signal | None:
    floor = gl.value("readiness_floor")
    run = 0
    for r in reversed([r for r in p.reports if r.date <= day]):
        if r.readiness is not None and r.readiness < floor:
            run += 1
        else:
            break
    if run < LOW_READINESS_RUN:
        return None
    return Signal(day, "readiness",
                  f"you have reported low or very low readiness to train on "
                  f"{run} reports running",
                  severity=min(1.0, run / 7),
                  evidence={"streak_days": run,
                            "trend": p.readiness_trend(upto=day)})


def _sleep_debt(p: Profile, day: dt.date) -> Signal | None:
    d = p.sleep_debt(days=7, upto=day)
    if not d or d["debt_hours"] < gl.value("sleep_debt_hours"):
        return None
    return Signal(day, "sleep_debt",
                  f"you are {d['debt_hours']:.1f}h short of your own usual "
                  f"sleep over {d['nights']} nights",
                  severity=min(1.0, d["debt_hours"] / 8), evidence={"sleep": d})


def _load_spike(p: Profile, day: dt.date) -> Signal | None:
    load = p.training_load(day)
    if load["chronic_28d_per_week"] < gl.value("min_chronic_load"):
        return None                      # ratio has no meaning down here
    if load["acwr"] is None or load["acwr"] <= gl.value("acwr_ceiling"):
        return None
    return Signal(day, "load_spike",
                  f"your last 7 days carry {load['acwr']}x your 28-day "
                  f"average",
                  severity=min(1.0, load["acwr"] / 3), evidence={"load": load})


def _inactivity(p: Profile, day: dt.date) -> Signal | None:
    """The signal a purely personal monitor structurally cannot raise."""
    a = p.activity_adequacy(weeks=INACTIVE_WEEKS, upto=day)
    if not a or a["verdict"] != "below" or a["weeks_scored"] < 3:
        return None
    return Signal(day, "under_active",
                  f"over {a['weeks_scored']} weeks your middle week had "
                  f"{a['median_mvpa_minutes']} minutes of moderate-to-vigorous "
                  f"activity (the median), against the {a['floor']:.0f} a week "
                  f"the WHO recommends",
                  severity=1 - min(1.0, a["median_mvpa_minutes"] / a["floor"]),
                  evidence={"adequacy": a})


def _quiet(p: Profile, day: dt.date) -> Signal | None:
    """Nothing recorded for a while. Needs no baseline, so it always works.

    Worth having precisely because every other detector goes silent when
    the data stops - and a silent monitor and a healthy user look
    identical from outside.
    """
    seen = ([r.date for r in p.reports] + [n.date for n in p.nights]
            + list(p.resting_hr) + list(p.active))
    last = max((d for d in seen if d <= day), default=None)
    if last is None:
        return None
    gap = (day - last).days
    if gap < QUIET_DAYS:
        return None
    return Signal(day, "quiet",
                  f"nothing has been recorded for {gap} days, so there is "
                  f"little I can say about this stretch",
                  severity=min(1.0, gap / 30),
                  evidence={"last_record": str(last), "gap_days": gap})


DETECTORS = (_intraday_hr, _resting_hr, _readiness_run, _sleep_debt, _load_spike,
             _inactivity, _quiet)

# kind -> the detector that produces it, so persistence can re-fire one
# detector back through time without re-running every detector.
BY_KIND = {"intraday_hr": _intraday_hr, "resting_hr": _resting_hr,
           "readiness": _readiness_run,
           "sleep_debt": _sleep_debt, "load_spike": _load_spike,
           "under_active": _inactivity, "quiet": _quiet}

METRICS = {
    "intraday_hr": {"label": "Intraday heart rate", "unit": "bpm",
                    "threshold": INTRADAY_HR_DELTA, "direction": "above",
                    "ceiling": 180, "scope": "intraday",
                    "rule": f"{INTRADAY_RUN_BUCKETS * INTRADAY_BUCKET_MINUTES} "
                            f"minutes at least {INTRADAY_HR_DELTA:.0f} bpm above "
                            "the personal resting reference, low steps, no "
                            "logged exercise"},
    "resting_hr": {"label": "Resting HR position", "unit": "personal percentile",
                   "threshold": gl.value("hr_alert_quantile") * 100,
                   "direction": "above", "ceiling": 100,
                   "rule": "top 10% of the personal 28-day window"},
    "readiness": {"label": "Readiness", "unit": "0-10 score",
                  "threshold": gl.value("readiness_floor"),
                  "direction": "below", "ceiling": 10,
                  "rule": "3 consecutive reports below 4"},
    "sleep_debt": {"label": "Seven-day sleep debt", "unit": "hours",
                   "threshold": gl.value("sleep_debt_hours"),
                   "direction": "above", "ceiling": 8,
                   "rule": "3 or more hours below personal usual sleep"},
    "load_spike": {"label": "Training load ratio", "unit": "ACWR",
                   "threshold": gl.value("acwr_ceiling"),
                   "direction": "above", "ceiling": 3,
                   "rule": "above 1.5 only when chronic load is sufficient"},
    "under_active": {"label": "Weekly activity", "unit": "MVPA min/week",
                     "threshold": gl.value("who_weekly_mvpa"),
                     "direction": "below", "ceiling": 300,
                     "rule": "four-week median below the WHO 150-minute floor"},
    "quiet": {"label": "Days since last record", "unit": "days",
              "threshold": QUIET_DAYS, "direction": "above", "ceiling": 14,
              "rule": "7 days without a new record"},
}


def _metric_value(p: Profile, day: dt.date, kind: str) -> tuple[float | None, str]:
    """The plotted value behind one detector, with a human-readable raw value."""
    if kind == "resting_hr":
        hr = p.resting_hr_position(day)
        return ((hr["quantile"] * 100, f"{hr['value']:.0f} bpm")
                if hr else (None, "no resting HR"))
    if kind == "readiness":
        report = next((r for r in p.reports if r.date == day), None)
        value = report.readiness if report else None
        return value, (wellness_words("readiness", value) if value is not None else "no report")
    if kind == "sleep_debt":
        debt = p.sleep_debt(days=7, upto=day)
        value = debt["debt_hours"] if debt else None
        if value is None:
            return value, "insufficient"
        # A negative debt is surplus sleep; say so rather than "-4.5 hours".
        return value, (f"{value:.1f} h less than usual" if value > 0 else
                       f"{-value:.1f} h more than usual" if value < 0 else "same as usual")
    if kind == "load_spike":
        load = p.training_load(day)
        eligible = load["chronic_28d_per_week"] >= gl.value("min_chronic_load")
        value = load["acwr"] if eligible else None
        return value, (f"{value:.2f} ACWR" if value is not None
                       else "chronic load too low")
    if kind == "under_active":
        activity = p.activity_adequacy(weeks=INACTIVE_WEEKS, upto=day)
        value = (activity["median_mvpa_minutes"] if activity and
                 activity["weeks_scored"] >= 3 else None)
        return value, (f"{value:.0f} min/week" if value is not None
                       else "fewer than 3 scored weeks")
    if kind == "quiet":
        seen = ([r.date for r in p.reports] + [n.date for n in p.nights]
                + list(p.resting_hr) + list(p.active))
        last = max((d for d in seen if d <= day), default=None)
        value = (day - last).days if last else None
        return value, (f"{value} days" if value is not None else "no prior record")
    raise ValueError(f"unknown monitoring metric: {kind}")


def metric_series(p: Profile, kind: str, upto: dt.date, days: int = 42,
                  until: dt.datetime | None = None) -> dict:
    """A chart-ready series sealed at `upto`, using the detector's own inputs."""
    if kind not in METRICS:
        raise ValueError(f"kind must be one of: {', '.join(METRICS)}")
    if not p.first_day <= upto <= p.today:
        raise ValueError(f"series date must be between {p.first_day} and {p.today}")
    if kind == "intraday_hr":
        if until is not None and until.date() != upto:
            raise ValueError("intraday series timestamp must be on the series date")
        state = _intraday_state(p, upto, until)
        spec = METRICS[kind]
        if not state:
            return {"kind": kind, **spec, "as_of": str(upto),
                    "window": {"start": str(upto), "end": str(upto)},
                    "points": []}
        points, run = [], 0
        for point in state["points"]:
            run = run + 1 if point["qualifies"] else 0
            firing = run >= INTRADAY_RUN_BUCKETS
            points.append({
                "date": point["at"], "value": point["heart_bpm"],
                "raw": (f"{point['heart_bpm']} bpm, "
                        f"{point['steps'] if point['steps'] is not None else '?'} steps"),
                "firing": firing,
                "summary": "sustained high heart rate with low recorded steps"
                if firing else ""})
        return {"kind": kind, **spec, "threshold": state["threshold_bpm"],
                "as_of": str(upto),
                "window": {"start": points[0]["date"] if points else str(upto),
                           "end": points[-1]["date"] if points else str(upto)},
                "points": points,
                "resting_reference_bpm": state["resting_bpm"]}
    start = max(p.first_day, upto - dt.timedelta(days=max(14, min(days, 90)) - 1))
    points, day = [], start
    while day <= upto:
        cut = p.snapshot(day)
        value, raw = _metric_value(cut, day, kind)
        signal = BY_KIND[kind](cut, day)
        points.append({"date": str(day), "value": value, "raw": raw,
                       "firing": signal is not None,
                       "summary": signal.summary if signal else ""})
        day += dt.timedelta(days=1)
    return {"kind": kind, **METRICS[kind], "as_of": str(upto),
            "window": {"start": str(start), "end": str(upto)},
            "points": points}


# =====================================================================
# Scanning
# =====================================================================

def signals_on(p: Profile, day: dt.date) -> list[Signal]:
    """Everything layer one notices about one day. No budget applied."""
    found = [s for s in (d(p, day) for d in DETECTORS) if s is not None]
    for signal in found:
        if signal.at is None:
            signal.at = dt.datetime.combine(day, dt.time(23, 59, 59))
    return found


def scan(p: Profile, days: int = 28,
         upto: dt.date | None = None) -> tuple[list[Signal], dict]:
    """Walk a window and return what is worth passing to the coach.

    Sustained conditions - a sleep deficit, chronic under-activity - fire
    on every day they hold, so the raw output is dominated by repeats of
    one finding. Only the first day of each run is kept; both numbers are
    reported. Whether any of them reaches the person is the coach's call.
    """
    hi = upto or p.today
    if hi is None:
        return [], {"scanned": 0}
    days_list = [hi - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]
    days_list = [d for d in days_list if d >= p.first_day]

    raw, seen_run, first_of_run = [], {}, []
    for d in days_list:
        found = signals_on(p, d)
        raw += found
        kinds = {s.kind for s in found}
        for s in found:
            if not seen_run.get(s.kind):
                first_of_run.append(s)
            seen_run[s.kind] = True
        for k in list(seen_run):
            if k not in kinds:
                seen_run[k] = False

    return first_of_run, {"scanned": len(days_list), "raw": len(raw),
                          "distinct_runs": len(first_of_run)}


# =====================================================================
# What the coach is told about a signal before it decides
# =====================================================================
# Code no longer decides whether a reviewed signal becomes an Alert: the
# coach does, from the record and these facts. They are facts, not cut
# points - a brief, mild-looking heart-rate rise can matter more than a
# long, slow trend, and only a reading of the record can tell.

def persistence(p: Profile, signal: "Signal") -> int:
    """How many consecutive days up to the signal's day this kind fired.

    Re-fires one detector back through time. A one-off and a three-week
    run look identical after de-duplication; persistence tells them
    apart, and it reads only days on or before the signal, so it cannot
    be informed by what came after.
    """
    det = BY_KIND.get(signal.kind)
    if det is None:
        return 1
    run, day = 0, signal.day
    while day >= p.first_day and det(p, day) is not None:
        run += 1
        day -= dt.timedelta(days=1)
    return run


def measure(p: Profile, signal: "Signal") -> str:
    """The signal's own level, in words a person can compare between two days.

    A detector's summary can grow while nothing worsens - "low readiness on
    7 reports running" becomes "on 10" with every unchanged answer - so a
    repeat is judged on the level itself, beside the level at the last Alert.
    """
    e = signal.evidence
    if signal.kind == "resting_hr":
        hr = e["resting_hr"]
        return f"{hr['value']:.1f} bpm, {hr['value'] - hr['baseline_median']:+.1f} from the median"
    if signal.kind == "readiness":
        run = [r for r in p.reports if r.date <= signal.day and r.readiness is not None][-e["streak_days"]:]
        latest = wellness_words("readiness", run[-1].readiness)
        lowest = wellness_words("readiness", min(r.readiness for r in run))
        return f"latest answer '{latest}', lowest in the run '{lowest}', {len(run)} low answers running"
    if signal.kind == "load_spike":
        load = e["load"]
        return f"{load['acwr']}x the 28-day average ({load['acute_7d']:.0f} sRPE in 7 days)"
    if signal.kind == "sleep_debt":
        return f"{e['sleep']['debt_hours']:.1f} h short over {e['sleep']['nights']} nights"
    if signal.kind == "under_active":
        return f"median week {e['adequacy']['median_mvpa_minutes']} min of moderate-to-vigorous activity"
    if signal.kind == "intraday_hr":
        return (f"{e['mean_hr_bpm']:.0f} bpm for {e['duration_minutes']} min, "
                f"{e['mean_hr_bpm'] - e['resting_reference_bpm']:+.0f} over the resting reference, "
                f"{e['steps']} steps")
    if signal.kind == "quiet":
        return f"{e['gap_days']} days without a record"
    return signal.summary


def is_event(signal: "Signal") -> bool:
    """An intraday signal is a one-off event; a daily one is a condition that
    holds for as long as its window says so."""
    return signal.scope == "intraday"


def condition_facts(p: Profile, signal: "Signal") -> dict:
    """A continuing condition's history, as facts: when it started and how it
    looked then, against how it looks now."""
    days = persistence(p, signal)
    started = signal.day - dt.timedelta(days=days - 1)
    first = next((s for s in signals_on(p.snapshot(started), started)
                  if s.kind == signal.kind), None)
    return {"started": str(started), "days_running": days,
            "at_start": first.summary if first else signal.summary,
            "now": signal.summary}


def signal_facts(signal: "Signal", coverage: str, profile: Profile) -> dict:
    """The facts the coach weighs, and the audit keeps, for one signal."""
    return {"severity": round(signal.severity, 2),
            "persistence_days": persistence(profile, signal),
            "coverage": coverage}


def main() -> None:
    from profile import PARTICIPANTS
    print("Layer one over the last 28 days\n")
    for pid in PARTICIPANTS:
        p = Profile(pid)
        if p.today is None:
            continue
        kept, stats = scan(p)
        print(f"{pid}  {stats['raw']:>3} raw -> {stats['distinct_runs']:>2} runs")
        for s in kept:
            print(f"      {s}")


if __name__ == "__main__":
    main()
