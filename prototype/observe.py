"""
The deep interface: Visible History turned into Semantic Observations.

`profile.py` answers twenty small questions, each a real computation over
one person's record. That layer is kept - it is where the arithmetic
lives and where the non-wear, duplicate-source and no-future-leak fixes
were made. What changed is the surface the agent sees. Twenty read-only
questions asked the model to be its own analyst: pick a metric, read a
raw number, decide what it means. This module is the analyst. It reads
those computations and returns Semantic Observations - a coach-meaningful
statement with its window, its comparison reference, its evidence with
units and provenance, and an honest account of whether the record could
support it at all.

Entry points:

    read_record(days, end)         what the coach reads: every domain over
                                   one window against the window before,
                                   the state at its end, findings and data
                                   quality, in one call
    snapshot(as_of)                a ranked overview (the page, and the
                                   findings in read_record)
    inspect(domain, start, end,    one domain against a reference period,
            compare_to)            as Semantic Observations (the page)

Two rules the whole file obeys.

**Missing data is insufficient, never normal.** A window with too little
recorded returns `status="insufficient"`, not `status="within_baseline"`.
Silence about a new user is honest; reassurance is not.

**Nothing reads past the record it was handed.** Callers pass a snapshot
sealed at the simulation date, and every window is clamped to it, so no
observation can be informed by a day that has not happened.

Run:  ../.venv/bin/python observe.py
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter

import guidelines as gl
import realtime
from profile import BASELINE_WINDOW, MODALITY, WELLNESS_NORMAL, Profile, hr_position, wellness_summary

# --------------------------------------------------------------------- #
# The observation shape
# --------------------------------------------------------------------- #

# The fields every Semantic Observation carries, in a fixed order so a
# test (and a reviewer) can check the contract by name rather than by eye.
OBSERVATION_FIELDS = ("topic", "window", "status", "summary", "reference",
                      "evidence", "data_quality", "limitations")

# A status is one of these and nothing else. `insufficient` is first
# because it is the one the rest of the system must never quietly drop.
STATUSES = ("insufficient", "observed", "within_baseline", "meets_guideline",
            "notable", "reduced", "elevated", "below_guideline")

# How loudly a finding asks to be surfaced in an overview. Ranking only;
# it is not part of the serialised contract a caller depends on.
SALIENCE = {"observed": 0, "insufficient": 1, "within_baseline": 0, "meets_guideline": 0,
            "notable": 2, "reduced": 3, "elevated": 3, "below_guideline": 3}

# Where a number came from, so an evidence item is never a bare figure.
SELF = "self-report"        # pmsys srpe / wellness
DEVICE = "device"           # fitbit
REPORTED = "reported"       # googledocs reporting
DERIVED = "derived"         # computed from the above
GUIDELINE = "guideline"     # an external standard
OVERVIEW = "participant-overview"   # the validated workbook metadata
_OVERVIEW_FIELD = {"run5k_min": "5 km run time (minutes)", "max_hr": "maximum heart rate",
                   "run5k_date": "5 km run date"}

# The unit and provenance of every scalar a period builder emits, so a
# drill-down never ships a numeric evidence item with an empty unit. A
# key missing here would fall back, but the check in test_agent.py fails
# if any real field does, which keeps this map honest.
FIELD_META = {
    "training": {
        "sessions": ("count", DERIVED), "per_week": ("sessions/week", DERIVED),
        "load": ("sRPE", DERIVED), "median_minutes": ("minutes/session", SELF),
        "effort": ("answers, in words", SELF), "self_reported": ("count", SELF),
        "device_only": ("count", DEVICE), "matched": ("count", DERIVED),
        "median_avg_hr": ("bpm (session average)", DEVICE), "median_steps": ("steps/session", DEVICE),
        "median_calories": ("kcal/session", DEVICE)},
    "sleep": {
        "nights": ("count", DEVICE), "median_hours": ("hours", DEVICE),
        "median_efficiency": ("percent", DEVICE),
        "median_deep_min": ("minutes", DEVICE),
        "median_rem_min": ("minutes", DEVICE), "scored": ("count", DEVICE)},
    "recovery": {
        "days": ("count", SELF), "readiness": ("answers, in words", SELF),
        "fatigue": ("answers, in words", SELF), "soreness": ("answers, in words", SELF),
        "stress": ("answers, in words", SELF), "mood": ("answers, in words", SELF),
        "sleep_quality": ("answers, in words", SELF),
        "low_readiness_reports": ("count", DERIVED),
        "soreness_area_days": ("count", SELF)},
    "physiology": {
        "resting_hr_days": ("count", DEVICE),
        "median_resting_hr": ("bpm", DEVICE), "worn_days": ("count", DEVICE),
        "median_sedentary_min": ("minutes", DEVICE),
        "median_active_min": ("minutes", DEVICE),
        "median_cardio_peak_min": ("minutes", DEVICE),
        "zone_days": ("count", DEVICE),
        "intraday_heart_days": ("days", DEVICE),
        "median_intraday_mean_hr": ("bpm", DEVICE),
        "median_intraday_p95_hr": ("bpm", DEVICE),
        "median_heart_covered_min": ("minutes/day", DEVICE),
        "intraday_step_days": ("days", DEVICE),
        "median_daily_steps": ("steps/day", DEVICE),
        "total_distance_km": ("km", DEVICE),
        "total_calories": ("kcal", DEVICE)},
    "lifestyle": {
        "days": ("count", REPORTED), "weight_days": ("count", REPORTED),
        "weight_change_kg": ("kg", REPORTED),
        "latest_weight_kg": ("kg", REPORTED),
        "median_glasses": ("glasses", REPORTED),
        "alcohol_days": ("count", REPORTED), "median_meals": ("count", REPORTED)},
    "injury": {"count": ("count", SELF)},
}


def _d(x) -> str:
    return x.isoformat() if isinstance(x, dt.date) else str(x)


def ev(name, value, unit, source) -> dict:
    """One piece of evidence: a named value, its unit, and its provenance."""
    return {"name": name, "value": value, "unit": unit, "source": source}


def observe(topic, start, end, status, summary, *, reference=None,
            evidence=None, coverage=None, sufficiency=None,
            limitations=None) -> dict:
    """Assemble one Semantic Observation.

    `status="insufficient"` forces `sufficiency="insufficient"` - the two
    cannot disagree, because that disagreement is exactly the bug this
    layer exists to prevent.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if sufficiency is None:
        sufficiency = ("insufficient" if status == "insufficient" else
                       "not_assessed" if status == "observed" else "sufficient")
    return {
        "topic": topic,
        "window": {"start": _d(start), "end": _d(end)},
        "status": status,
        "summary": summary,
        "reference": reference,
        "evidence": evidence or [],
        "data_quality": {"coverage": coverage, "sufficiency": sufficiency},
        "limitations": limitations or [],
        "salience": SALIENCE[status],
    }


# --------------------------------------------------------------------- #
# Window helpers
# --------------------------------------------------------------------- #

def _span_days(start: dt.date, end: dt.date) -> int:
    return (end - start).days + 1


def _coverage(p: Profile, start: dt.date, end: dt.date) -> tuple[float, set]:
    """Fraction of days in the window with any record at all, and which.

    Used both to report data quality and to decide sufficiency: a domain
    cannot be judged over a window the device barely saw.
    """
    days = {r.date for r in p.reports if start <= r.date <= end}
    days |= {n.date for n in p.nights if start <= n.date <= end}
    days |= {d for d in p.resting_hr if start <= d <= end}
    days |= {d for d in p.active if start <= d <= end}
    days |= {i.date for i in p.intake if start <= i.date <= end}
    return round(len(days) / _span_days(start, end), 2), days


def _clamp(p: Profile, start, end) -> tuple[dt.date, dt.date]:
    """Keep a requested window inside the record, and never past the clock.

    `p.today` is the simulation date on a sealed snapshot, so clamping to
    it is what stops a drill-down window reaching into hidden future data.
    """
    lo = p.first_day if start is None else max(start, p.first_day)
    hi = p.today if end is None else min(end, p.today)
    if lo > hi:
        lo = hi
    return lo, hi


def _domain_days(p: Profile, domain: str, start, end) -> set:
    """The days in the window that carry a record of THIS domain."""
    inw = lambda d: start <= d <= end
    if domain == "training":
        return {s.date for s in p.sessions if inw(s.date)}
    if domain == "sleep":
        return {n.date for n in p.nights if inw(n.date)}
    if domain == "recovery":
        return {r.date for r in p.reports if inw(r.date)}
    if domain == "physiology":
        return ({d for d in p.resting_hr if inw(d)}
                | {d for d in p.active if inw(d)}
                | {d for d in p.zones if inw(d)})
    if domain == "lifestyle":
        return {i.date for i in p.intake if inw(i.date)}
    if domain == "injury":
        return {x.date for x in p.injuries if inw(x.date)}
    return set()


def _domain_coverage(p: Profile, domain: str, start, end) -> float:
    """Fraction of the window carrying this domain's own records.

    Domain-specific on purpose: a window dense with wellness reports still
    has zero *sleep* coverage if no night was logged, and reporting the
    overall any-record density there would overstate what is known.
    """
    return round(len(_domain_days(p, domain, start, end))
                 / _span_days(start, end), 2)


def _zone_minutes(z: dict) -> dict[str, float]:
    """Fitbit daily zone buckets, named plainly. Cardio+peak is 'elevated'."""
    return {"out": z.get("BELOW_DEFAULT_ZONE_1", 0.0),
            "fat_burn": z.get("IN_DEFAULT_ZONE_1", 0.0),
            "cardio": z.get("IN_DEFAULT_ZONE_2", 0.0),
            "peak": z.get("IN_DEFAULT_ZONE_3", 0.0)}


# --------------------------------------------------------------------- #
# Domain builders - one real period, reused by snapshot and inspect
# --------------------------------------------------------------------- #

def _med(vals):
    return int(statistics.median(vals)) if vals else None


def _training_period(p: Profile, start, end) -> dict:
    sess = [s for s in p.sessions if start <= s.date <= end]
    weeks = max(_span_days(start, end) / 7, 1e-9)
    durations = [s.minutes for s in sess]
    hrs = [s.avg_hr for s in sess if s.avg_hr]             # device evidence
    steps = [s.steps for s in sess if s.steps]
    cals = [s.calories for s in sess if s.calories]
    by_mod = Counter(s.modality for s in sess)
    return {
        "sessions": len(sess),
        "per_week": round(len(sess) / weeks, 1),
        "load": round(sum(s.load for s in sess if s.load is not None), 0),
        "unrated_device_minutes": sum(s.minutes for s in sess if s.load is None),
        "median_minutes": _med(durations),
        "provenance": {"median_minutes": "+".join(sorted({
            s.source for s in sess})) or "unavailable"},
        "effort": wellness_summary([s for s in sess if s.rpe is not None], "rpe"),
        "self_reported": sum(1 for s in sess if SELF in s.sources),
        "device_only": sum(1 for s in sess if s.sources == (DEVICE,)),
        "matched": sum(1 for s in sess if SELF in s.sources and DEVICE in s.sources),
        # Device evidence: describes the sessions, never feeds the plan.
        "median_avg_hr": _med(hrs),
        "median_steps": _med(steps),
        "median_calories": _med(cals),
        "modalities": {MODALITY.get(m, {}).get("label", m): n
                       for m, n in by_mod.most_common()},
    }


def _sleep_period(p: Profile, start, end) -> dict:
    nights = [n for n in p.nights if start <= n.date <= end]
    if not nights:
        return {"nights": 0}
    return {
        "nights": len(nights),
        "median_hours": round(statistics.median(n.asleep_min for n in nights) / 60, 2),
        "median_efficiency": statistics.median(n.efficiency for n in nights),
        "median_deep_min": statistics.median(n.deep for n in nights),
        "median_rem_min": statistics.median(n.rem for n in nights),
        "scored": sum(1 for n in nights if n.score is not None),
    }


def _recovery_period(p: Profile, start, end) -> dict:
    reps = [r for r in p.reports if start <= r.date <= end]
    # The raw SNOMED soreness-area codes are preserved unmapped: the repo
    # holds no authoritative code->anatomy mapping, so they are surfaced
    # as codes and cannot constrain a modality.
    codes = sorted({c for r in reps for c in r.soreness_area_codes})
    return {
        "days": len(reps),
        **{field: wellness_summary(reps, field)
           for field in ("readiness", "fatigue", "soreness", "stress", "mood", "sleep_quality")},
        "low_readiness_reports": sum(1 for r in reps if r.readiness is not None
                                     and r.readiness < gl.value("readiness_floor")),
        "soreness_area_days": sum(1 for r in reps if r.soreness_area_codes),
        "soreness_area_codes": codes,
    }


def _physiology_period(p: Profile, start, end) -> dict:
    rhr = [v for d, v in p.resting_hr.items() if start <= d <= end]
    active = [r for d, r in p.active.items() if start <= d <= end]
    zones = [_zone_minutes(z) for d, z in p.zones.items() if start <= d <= end]

    def med_bucket(rows, k):
        vals = [r[k] for r in rows]
        return int(statistics.median(vals)) if vals else None

    stream = realtime.period_summary(p.pid, start, end)["summary"]
    return {
        "resting_hr_days": len(rhr),
        "median_resting_hr": round(statistics.median(rhr), 1) if rhr else None,
        "worn_days": len(active),
        "median_sedentary_min": med_bucket(active, "sedentary"),
        "median_active_min": (
            int(statistics.median(r["very"] + r["moderate"] + r["light"]
                                  for r in active)) if active else None),
        # Daily heart-rate zones: time spent working, from the watch.
        "zone_days": len(zones),
        "median_cardio_peak_min": (
            int(statistics.median(z["cardio"] + z["peak"] for z in zones))
            if zones else None),
        "intraday_heart_days": stream["heart_days"],
        "median_intraday_mean_hr": stream["median_mean_hr_bpm"],
        "median_intraday_p95_hr": stream["median_p95_hr_bpm"],
        "median_heart_covered_min": stream["median_heart_covered_minutes"],
        "intraday_step_days": stream["step_days"],
        "median_daily_steps": stream["median_daily_steps"],
        "total_distance_km": stream["total_distance_km"],
        "total_calories": stream["total_calories"],
    }


def _lifestyle_period(p: Profile, start, end) -> dict:
    intake = [i for i in p.intake if start <= i.date <= end]
    weights = [(i.date, i.weight) for i in intake if i.weight]
    fluid = [i.fluid for i in intake if i.fluid]
    alcohol = [i.alcohol for i in intake if i.alcohol is not None]
    meals = [len(i.meals) for i in intake]
    return {
        "days": len(intake),
        "weight_days": len(weights),
        "weight_change_kg": (round(weights[-1][1] - weights[0][1], 2)
                             if len(weights) >= 2 else None),
        "latest_weight_kg": weights[-1][1] if weights else None,
        "median_glasses": statistics.median(fluid) if fluid else None,
        "alcohol_days": sum(1 for a in alcohol if a),
        "median_meals": statistics.median(meals) if meals else None,
    }


def _injury_period(p: Profile, start, end) -> dict:
    rel = [x for x in p.injuries if start <= x.date <= end]
    return {
        "count": len(rel),
        "by_joint": dict(Counter(x.joint for x in rel if x.joint)),
        "severities": dict(Counter(x.severity for x in rel)),
        "reports": [{"date": _d(x.date), "location": x.location,
                     "joint": x.joint, "severity": x.severity} for x in rel],
    }


# --------------------------------------------------------------------- #
# Snapshot - the compact ranked overview, from native personal baselines
# --------------------------------------------------------------------- #
# These reuse profile.py's as_of questions, which already compare the
# person to their own preceding window and return None when the history
# is too thin. Each becomes one observation; the None case becomes
# `insufficient`, never a reassuring silence.

def _snap_training(p: Profile, day: dt.date) -> list[dict]:
    out = []
    load = p.training_load(day)
    lo = day - dt.timedelta(days=28)
    cov = _domain_coverage(p, "training", lo, day)
    # The same floor the intensity ceiling and the load detector apply: below
    # it a ratio of two small numbers means nothing, so it is not reported.
    if load["acwr"] is None or load["chronic_28d_per_week"] < gl.value("min_chronic_load"):
        out.append(observe(
            "training.load", lo, day, "insufficient",
            "Not enough recent training load to judge a 7-day to 28-day "
            "ratio - the baseline is too light for it to mean anything.",
            reference="acute:chronic workload ratio",
            evidence=[ev("acute_7d", load["acute_7d"], "sRPE", DERIVED),
                      ev("chronic_28d_per_week", load["chronic_28d_per_week"],
                         "sRPE/week", DERIVED)],
            coverage=cov,
            limitations=["ratio withheld below the min-chronic-load floor"]))
    else:
        spike = load["acwr"] > gl.value("acwr_ceiling")
        out.append(observe(
            "training.load", lo, day, "elevated" if spike else "within_baseline",
            (f"Last 7 days carry {load['acwr']}x the 28-day average, past the "
             f"{gl.value('acwr_ceiling')} tripwire."
             if spike else
             f"Training load sits at {load['acwr']}x the 28-day average, "
             "inside the usual range."),
            reference="your own 28-day rolling load",
            evidence=[ev("acwr", load["acwr"], "ratio", DERIVED),
                      ev("sessions_7d", load["sessions_7d"], "count", DERIVED)],
            coverage=cov))

    adq = p.activity_adequacy(upto=day)
    if adq is None:
        out.append(observe(
            "training.adequacy", day - dt.timedelta(days=28), day,
            "insufficient",
            "Not enough scored activity weeks to compare against the WHO "
            "aerobic floor.", reference="WHO aerobic guideline",
            coverage=cov,
            limitations=["needs weeks with 5+ worn days each"]))
    else:
        status = {"below": "below_guideline", "meets": "meets_guideline",
                  "exceeds": "meets_guideline"}[adq["verdict"]]
        start = day - dt.timedelta(days=adq["weeks_scored"] * 7)
        # The comparison rests on daily activity minutes, not logged sessions.
        worn = round(sum(1 for d in p.active if start <= d <= day)
                     / _span_days(start, day), 2)
        out.append(observe(
            "training.adequacy", start,
            day, status,
            (f"About {adq['median_mvpa_minutes']} min/week of "
             f"moderate-to-vigorous activity against the {adq['floor']:.0f} "
             f"the WHO recommends - {adq['verdict']}."),
            reference=f"WHO aerobic floor {adq['floor']:.0f} min/week",
            evidence=[ev("median_mvpa", adq["median_mvpa_minutes"],
                         "min/week", DERIVED),
                      ev("floor", adq["floor"], "min/week", GUIDELINE),
                      ev("weeks_scored", adq["weeks_scored"], "weeks", DERIVED)],
            coverage=worn))
    return out


def _snap_recovery(p: Profile, day: dt.date) -> list[dict]:
    out = []
    lo = day - dt.timedelta(days=14)
    cov = _domain_coverage(p, "recovery", lo, day)
    trend = p.readiness_trend(upto=day)
    if trend is None:
        out.append(observe("recovery.readiness", lo, day, "insufficient",
                           "Not enough self-reported readiness to read a trend.",
                           reference="your own readiness over 14 days",
                           coverage=cov))
    else:
        falling = trend["direction"] == "falling"
        out.append(observe(
            "recovery.readiness", lo, day,
            "reduced" if falling else "observed",
            (f"Self-rated readiness to train: {trend['answers']}. Over the "
             "window it is " + {"falling": "falling", "rising": "rising", "flat": "steady",
                                "not_assessed": "without a readable trend"}[trend["direction"]] + "."),
            reference=f"{trend['record_count']} reports over {trend['calendar_days']} calendar days",
            evidence=[ev("answers", trend["answers"], "answers, in words", SELF),
                      ev("direction", trend["direction"], "trend", DERIVED)],
            coverage=cov))

    hr = p.resting_hr_position(day)
    if hr is None:
        out.append(observe("recovery.resting_hr", day, day, "insufficient",
                           "No resting heart rate available for today against a "
                           "usable personal baseline.",
                           reference="your own last 28 days",
                           coverage=cov))
    else:
        high = hr["quantile"] >= gl.value("hr_alert_quantile")
        out.append(observe(
            "recovery.resting_hr", day, day,
            "elevated" if high else "within_baseline",
            (f"Resting heart rate {hr['value']:.0f} bpm is {hr_position(hr)} "
             f"the last {hr['baseline_days']} days."),
            reference=f"personal median {hr['baseline_median']} bpm",
            evidence=[ev("value", hr["value"], "bpm", DEVICE),
                      ev("quantile", hr["quantile"], "percentile", DERIVED),
                      ev("baseline_median", hr["baseline_median"], "bpm",
                         DERIVED)],
            coverage=cov))

    # Fatigue, soreness, stress and sleep quality in the person's own
    # words: one observation that flags which are mostly below normal.
    rec = _recovery_period(p, lo, day)
    reps = [r for r in p.reports if lo <= r.date <= day]
    items = ("fatigue", "soreness", "stress", "sleep_quality")
    if rec["days"] == 0:
        out.append(observe("recovery.strain", lo, day, "insufficient",
                           "No wellness self-reports over the last 14 days.",
                           reference="your own self-reports", coverage=cov))
    else:
        below = [f.replace("_", " ") for f in items
                 if (vals := [getattr(r, f) for r in reps if getattr(r, f) is not None])
                 and statistics.median(vals) < WELLNESS_NORMAL]
        parts = [f"{f.replace('_', ' ')}: {rec[f]}" for f in items if rec[f]]
        out.append(observe(
            "recovery.strain", lo, day,
            "elevated" if below else "observed",
            (f"Mostly below normal: {', '.join(below)}. " if below
             else "No self-reported item is mostly below normal. ") + "; ".join(parts) + ".",
            reference="the questionnaire's own normal answer, not a personal baseline",
            evidence=_evidence("recovery", rec), coverage=cov))
    return out


def _snap_physiology(p: Profile, day: dt.date) -> list[dict]:
    lo = day - dt.timedelta(days=28)
    cov = _domain_coverage(p, "physiology", lo, day)
    phys = _physiology_period(p, lo, day)
    if phys["worn_days"] == 0 and phys["zone_days"] == 0 \
            and phys["resting_hr_days"] == 0:
        return [observe("physiology.activity", lo, day, "insufficient",
                        "The device recorded no usable resting HR, zone or "
                        "activity days over the window.",
                        reference="Fitbit zones and intensity minutes",
                        coverage=cov)]
    ev_items = _evidence("physiology", phys)
    # A valid maximum heart rate from the overview contextualises the
    # zones; an invalid or missing one is noted, not used.
    meta = p.overview.get("metadata", {})
    warn = p.overview.get("warnings", {})
    limits = []
    if meta.get("max_hr"):
        ev_items.append(ev("max_hr", meta["max_hr"], "bpm", OVERVIEW))
    elif "max_hr" in warn:
        limits.append(f"maximum heart rate {warn['max_hr']}; zones shown "
                      "without a percent-of-max context")
    summary = (
        f"Typical wearable-day activity was {phys['median_daily_steps']} steps, "
        f"with heart rate sampled for about {phys['median_heart_covered_min']} "
        f"minutes/day across {phys['intraday_heart_days']} days. "
        if phys["median_daily_steps"] is not None and
        phys["median_heart_covered_min"] is not None else "")
    summary += (
        f"Around {phys['median_cardio_peak_min']} min/day in cardio or peak "
        f"zones and {phys['median_active_min']} active minutes across "
        f"{phys['worn_days']} worn days."
        if phys["median_cardio_peak_min"] is not None else
        f"{phys['resting_hr_days']} resting-HR days recorded; zone detail "
        "is thin over this window.")
    return [observe(
        "physiology.activity", lo, day, "observed",
        summary,
        reference="your own device activity",
        evidence=ev_items, coverage=cov, limitations=limits)]


def _snap_sleep(p: Profile, day: dt.date) -> list[dict]:
    lo = day - dt.timedelta(days=6)
    cov = _domain_coverage(p, "sleep", lo, day)
    debt = p.sleep_debt(days=7, upto=day)
    if debt is None:
        return [observe("sleep.debt", lo, day, "insufficient",
                        "Not enough nights logged to compare this week's sleep "
                        "with your own usual.",
                        reference="your own sleep baseline", coverage=cov)]
    short = debt["debt_hours"] >= gl.value("sleep_debt_hours")
    direction = "below" if debt["debt_hours"] > 0 else "above"
    summary = (f"Recorded sleep total is {abs(debt['debt_hours']):.1f} hours {direction} "
               f"the rolling baseline expectation across {debt['nights']} sleep records."
               if debt["debt_hours"] else
               f"Recorded sleep total matches the rolling baseline expectation across {debt['nights']} sleep records.")
    out = [observe(
        "sleep.debt", lo, day, "reduced" if short else "within_baseline",
        summary,
        reference="your own median nightly sleep",
        evidence=[ev("debt_hours", debt["debt_hours"], "hours", DERIVED),
                  ev("actual_hours", debt["actual_hours"], "hours", DEVICE),
                  ev("expected_hours", debt["expected_hours"], "hours", DERIVED)],
        coverage=cov,
        limitations=["Positive debt_hours means less recorded sleep, not more.",
                     "Missing days are not counted as zero sleep; this is not a diagnosis."])]

    # Reconcile the two sleep sources: self-report against the watch. A
    # persistent gap is itself worth surfacing, and averaging it away
    # would hide it - so it is stated, never silently trusted.
    agree = p.sleep_agreement(upto=day)
    if agree and agree["over_1h_share"] >= 0.25:
        out.append(observe(
            "sleep.agreement", day - dt.timedelta(days=28), day, "notable",
            (f"Reported and measured sleep disagree by over an hour on "
             f"{agree['over_1h_share']:.0%} of nights; you "
             f"{agree['direction'].replace('-reports', '-report')} by "
             f"{abs(agree['median_diff_hours']):.1f}h at the median."),
            reference="self-reported hours vs the watch",
            evidence=[ev("over_1h_share", agree["over_1h_share"], "fraction",
                         DERIVED),
                      ev("median_diff_hours", agree["median_diff_hours"],
                         "hours", DERIVED),
                      ev("nights", agree["nights"], "count", DERIVED)],
            coverage=cov,
            limitations=["a disagreement is not an error in either source"]))
    return out


def _snap_lifestyle(p: Profile, day: dt.date) -> list[dict]:
    lo = day - dt.timedelta(days=28)
    cov = _domain_coverage(p, "lifestyle", lo, day)
    w = p.weight_trend(upto=day)
    out = []
    if w is None:
        out.append(observe("lifestyle.weight", lo, day, "insufficient",
                           "Not enough weigh-ins to read a trend.",
                           reference="your own weight over 28 days",
                           coverage=cov,
                           limitations=["a lifestyle pattern is context, "
                                        "never a plan constraint"]))
    else:
        moving = w["direction"] != "stable" and abs(w["kg_per_week"] or 0) > 0.2
        out.append(observe(
            "lifestyle.weight", lo, day,
            "notable" if moving else "within_baseline",
            (f"Body weight is trending {w['direction']} at "
             f"{abs(w['kg_per_week'] or 0):.2f} kg/week ({w['change_kg']:+.1f} kg "
             "over the window)." if moving else
             f"Body weight is stable ({w['change_kg']:+.1f} kg over the window)."),
            reference="your own weight over 28 days",
            evidence=[ev("kg_per_week", w["kg_per_week"], "kg/week", DERIVED),
                      ev("latest_kg", w["latest_kg"], "kg", REPORTED)],
            coverage=cov,
            limitations=["a lifestyle pattern is context, never a plan "
                         "constraint"]))

    # Meal regularity, hydration and alcohol - patterns worth noting, never
    # a plan constraint.
    eat = p.eating_pattern(upto=day)
    drink = p.drinking_pattern(upto=day)
    if eat is None and drink is None:
        out.append(observe("lifestyle.intake", lo, day, "insufficient",
                           "Not enough logged meals or drinks to describe a "
                           "pattern.", reference="the reporting log",
                           coverage=cov,
                           limitations=["a lifestyle pattern is context, "
                                        "never a plan constraint"]))
    else:
        skips = eat and (eat["breakfast_share"] < 0.5
                         or eat["median_meals_per_day"] < 3)
        drinks = drink and (drink["alcohol_share"] or 0) > 0.3
        evs = []
        if eat:
            evs += [ev("median_meals_per_day", eat["median_meals_per_day"],
                       "count", REPORTED),
                    ev("breakfast_share", eat["breakfast_share"], "fraction",
                       REPORTED)]
        if drink:
            evs += [ev("median_glasses", drink["median_glasses"], "glasses",
                       REPORTED),
                    ev("alcohol_share", drink["alcohol_share"], "fraction",
                       REPORTED)]
        out.append(observe(
            "lifestyle.intake", lo, day,
            "notable" if (skips or drinks) else "observed",
            ("Meals are irregular or alcohol is frequent."
             if (skips or drinks) else
             "Meals, hydration and alcohol look unremarkable."),
            reference="your own 28-day intake",
            evidence=[e for e in evs if e["value"] is not None], coverage=cov,
            limitations=["a lifestyle pattern is context, never a plan "
                         "constraint"]))
    return out


def _snap_injury(p: Profile, day: dt.date) -> list[dict]:
    inj = p.injury_history(upto=day)
    joints = p.recent_injured_joints(within_days=gl.value("recent_injury_days"),
                                     upto=day)
    lo = day - dt.timedelta(days=28)
    if inj["count"] == 0:
        return [observe("injury.recent", lo, day, "insufficient",
                        "No injuries reported.", reference="the injury log")]
    recent = inj["days_since_last"] is not None and \
        inj["days_since_last"] <= gl.value("recent_injury_days")
    return [observe(
        "injury.recent", lo, day, "notable" if recent else "observed",
        (f"Last injury {inj['days_since_last']} days ago ({inj['last_location']})"
         + (f"; in the last {gl.value('recent_injury_days'):.0f} days: "
            + ", ".join(f"{j} {inj['days_since_by_joint'][j]} days ago" for j in joints)
            if joints else "")
         + f"; {inj['count']} report{'' if inj['count'] == 1 else 's'} in the whole record so far."),
        reference="the injury log",
        evidence=[ev("days_since_last", inj["days_since_last"], "days", SELF),
                  ev("recent_joints", ", ".join(joints) or "none", "joint",
                     SELF)],
        limitations=(["recent injury caps intensity in a plan"] if recent
                     else []))]


def _snap_data_quality(p: Profile, day: dt.date) -> dict:
    lo = day - dt.timedelta(days=28)
    cov, days = _coverage(p, lo, day)
    last = max((d for d in days), default=None)
    gap = (day - last).days if last else None
    quiet = gap is not None and gap >= 7
    if last is None:
        status, summary = "insufficient", \
            "Nothing recorded in the last 28 days."
    elif quiet:
        status, summary = "insufficient", \
            f"Nothing recorded for {gap} days - little can be said about this " \
            "stretch."
    elif cov < 0.5:
        status, summary = "insufficient", \
            f"Only {cov:.0%} of the last 28 days carry any record."
    else:
        status, summary = "within_baseline", \
            f"{cov:.0%} of the last 28 days carry a record."
    warn = p.overview.get("warnings", {})
    limits = ([("the study's participant sheet has values that were ignored: "
                + ", ".join(f"{_OVERVIEW_FIELD.get(k, k.replace('_', ' '))} {v}"
                            for k, v in warn.items()))]
              if warn else [])
    return observe(
        "data_quality.coverage", lo, day, status, summary,
        reference="days with any record over 28 days",
        evidence=[ev("coverage", cov, "fraction", DERIVED),
                  ev("days_since_last_record", gap, "days", DERIVED)],
        coverage=cov, limitations=limits)


SNAP_DOMAINS = {
    "training": _snap_training,
    "recovery": _snap_recovery,
    "physiology": _snap_physiology,
    "sleep": _snap_sleep,
    "lifestyle": _snap_lifestyle,
    "injury": _snap_injury,
}


def snapshot(p: Profile, as_of: dt.date | None = None, top: int = 6) -> dict:
    """A ranked overview across domains, plus where to look closer.

    Not a dump: every domain contributes its own summary line and its
    status, but only the highest-salience findings are promoted to
    `top_findings`. Data quality is always reported separately, because a
    thin record is the one thing a coach must not read as calm.
    """
    day = as_of or p.today
    if day is None:
        raise ValueError("cannot summarise an empty record")

    per_domain, all_obs = {}, []
    for name, fn in SNAP_DOMAINS.items():
        obs = fn(p, day)
        all_obs += obs
        lead = max(obs, key=lambda o: o["salience"])
        per_domain[name] = {
            "status": lead["status"],
            "headline": lead["summary"],
            "salience": lead["salience"],
            "observations": len(obs),
        }

    dq = _snap_data_quality(p, day)
    ranked = sorted(all_obs, key=lambda o: -o["salience"])
    top_findings = [o for o in ranked if o["salience"] >= 2][:top]

    return {
        "person": p.pid,
        "as_of": _d(day),
        "recorded_from": _d(p.first_day),
        "top_findings": top_findings,
        "domains": per_domain,
        "data_quality": dq,
    }


# --------------------------------------------------------------------- #
# Inspect - one domain, a real period, a real reference period
# --------------------------------------------------------------------- #

INSPECT_DOMAINS = {
    "training": (_training_period, "training.period"),
    "sleep": (_sleep_period, "sleep.period"),
    "recovery": (_recovery_period, "recovery.period"),
    "physiology": (_physiology_period, "physiology.period"),
    "lifestyle": (_lifestyle_period, "lifestyle.period"),
    "injury": (_injury_period, "injury.period"),
}


def _evidence(domain: str, cur: dict) -> list[dict]:
    """Scalar fields as evidence, each with a real unit and provenance.

    Every numeric value gets its unit and source from FIELD_META, so no
    drill-down ships a bare figure. Lists (modalities, soreness codes) are
    included but tagged 'list' so a consumer can tell them from a scalar.
    """
    meta = FIELD_META.get(domain, {})
    out = []
    for k, v in cur.items():
        if v is None or isinstance(v, dict):
            continue
        if isinstance(v, list):
            out.append(ev(k, v, "list", DERIVED))
            continue
        unit, source = meta.get(k, ("value", DERIVED))
        source = cur.get("provenance", {}).get(k, source)
        out.append(ev(k, v, unit, source))
    return out


def _delta_summary(domain: str, cur: dict, ref: dict) -> tuple[str, str]:
    """A plain comparison line and a status for two periods of one domain.

    Comparative, never causal: it reports that a number moved between two
    windows the caller chose, and by how much. It does not say why.
    """
    field, label, unit = {
        "training": ("per_week", "Sessions per week", ""),
        "sleep": ("median_hours", "Median sleep", " h"),
        "recovery": ("low_readiness_reports", "Reports of low or very low readiness to train", ""),
        "physiology": ("median_resting_hr", "Median resting heart rate", " bpm"),
        "lifestyle": ("latest_weight_kg", "Latest weight", " kg"),
        "injury": ("count", "Injury reports", "")}[domain]
    a, b = cur.get(field), ref.get(field)
    if a is None or b is None:
        return (f"{label}: this period {a}, reference {b} - not directly "
                "comparable.", "notable")
    diff = round(a - b, 2)
    if diff == 0:
        return f"{label}: unchanged at {a}{unit} between the two periods.", \
            "within_baseline"
    direction = "higher" if diff > 0 else "lower"
    # Resting HR and injury counts read the opposite way, but the layer
    # states the movement and its reference, not a value judgement.
    return (f"{label}: {a}{unit} this period against {b}{unit} in the "
            f"reference, {abs(diff)}{unit} {direction}.", "notable")


def _period_quality(p: Profile, domain: str, start, end, detail: dict) -> dict:
    recorded_days = len(_domain_days(p, domain, start, end))
    calendar_days = _span_days(start, end)
    count_key = {"training": "sessions", "sleep": "nights", "recovery": "days",
                 "lifestyle": "days", "injury": "count"}.get(domain)
    return {
        "calendar_days": calendar_days,
        "recorded_days": recorded_days,
        "record_count": detail.get(count_key) if count_key else None,
        "record_unit": {"training": "sessions", "sleep": "sleep_records",
                        "recovery": "wellness_reports", "lifestyle": "intake_records",
                        "injury": "injury_reports"}.get(domain, "multiple_streams"),
        "coverage": round(recorded_days / calendar_days, 2),
        "coverage_basis": ("days_with_recorded_events / calendar_days"
                           if domain in ("training", "injury") else
                           "days_with_domain_records / calendar_days"),
        "sufficiency": "not_assessed" if recorded_days else "insufficient",
        "assessment_scope": "descriptive records only; explanatory sufficiency is not assessed",
    }


def period_statistics(p: Profile, domain: str, start, end, current: dict,
                      reference_window=None, reference=None) -> dict:
    """Arithmetic over recorded data only; no interpretation or clinical threshold."""
    def counts(lo, hi, values):
        quality = _period_quality(p, domain, lo, hi, values)
        return {**quality, "window": {"start": str(lo), "end": str(hi)},
                "missing_days": quality["calendar_days"] - quality["recorded_days"],
                "coverage_percent": round(100 * quality["recorded_days"] / quality["calendar_days"], 2)}
    result = {"current": counts(start, end, current),
              "comparison": None, "reference": None,
              "limitations": ["No causality or sufficiency threshold is inferred from these calculations."]}
    if reference_window is not None:
        r0, r1 = reference_window
        result["reference"] = counts(r0, r1, reference)
        result["overlap_calendar_days"] = max(0, (min(end, r1) - max(start, r0)).days + 1)
        result["comparison"] = {}
        for key, (unit, _) in FIELD_META[domain].items():
            if key not in current and key not in reference:
                continue
            a, b = current.get(key), reference.get(key)
            numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
            comparable = numeric and result["current"]["recorded_days"] > 0 and result["reference"]["recorded_days"] > 0
            item = {"current": a, "reference": b,
                    "difference": round(a - b, 2) if comparable else None,
                    "unit": "percentage_points" if unit == "percent" else unit,
                    "operation": "current minus reference"}
            if unit == "hours":
                item["difference_minutes"] = round((a - b) * 60, 2) if comparable else None
            result["comparison"][key] = item
    if domain == "sleep":
        baseline = p.sleep_baseline(end)
        balance = p.sleep_debt(days=_span_days(start, end), upto=end)
        result["sleep_balance"] = ({
            **balance, "baseline_median_hours": baseline["median_hours"],
            "baseline_window": {
                "start": str(max(p.first_day, end - dt.timedelta(days=BASELINE_WINDOW - 1))),
                "end": str(end)},
            "baseline_sleep_records": baseline["nights"],
            "unit": "hours", "positive_debt_means": "less recorded sleep than baseline expectation",
            "formula": "baseline median per sleep record * observed sleep record count - observed total hours",
            "limitations": ["Missing days are not imputed as zero sleep.",
                            "This rolling baseline includes the target period and differs from compare_to.",
                            "Sleep records may include multiple records on one date; this is not a clinical sleep requirement."]
        } if balance is not None and baseline is not None else None)
    return result


def inspect(p: Profile, domain: str, start=None, end=None,
            compare_to=None, *, include_statistics: bool = False) -> dict:
    """Domain detail over a real window, optionally against a reference one.

    Windows are clamped to the record and to the simulation date, so a
    drill-down cannot become a leak. When `compare_to` is given, both
    periods are computed over exactly the dates requested - never a
    cumulative as_of dressed up as a period.
    """
    if domain not in INSPECT_DOMAINS:
        raise ValueError(f"unknown domain {domain!r}; "
                         f"choose from {sorted(INSPECT_DOMAINS)}")
    fn, topic = INSPECT_DOMAINS[domain]
    start, end = _clamp(p, start, end)
    cur = fn(p, start, end)
    cov = _domain_coverage(p, domain, start, end)     # this domain, not any-record

    limitations = []
    if domain == "lifestyle":
        limitations.append("a lifestyle pattern is context, never a plan "
                           "constraint")
    if domain == "recovery" and cur.get("soreness_area_codes"):
        limitations.append("soreness-area codes are raw SNOMED and unmapped; "
                           "they cannot constrain a modality")

    quality = _period_quality(p, domain, start, end, cur)
    missing = quality["recorded_days"] == 0
    status = "insufficient" if missing else "observed"
    limitations.append("record counts are not calendar days; missing records do not establish normality")
    if domain in ("training", "injury"):
        limitations.append("event density is not reporting completeness; unrecorded events cannot be ruled out")
    if domain == "training":
        limitations.append("median_minutes, median_avg_hr, median_steps and median_calories describe recorded sessions, not daily totals; per_week is a mean over the calendar window")
    if compare_to is None:
        limitations.append("no reference period was compared; this is not a baseline assessment")
    summary = (f"{domain} over {_d(start)} to {_d(end)}: "
               f"{quality['calendar_days']} calendar days, "
               f"{quality['recorded_days']} day{'' if quality['recorded_days'] == 1 else 's'} with records. "
               "This describes recorded data, not a baseline assessment."
               if not missing else
               f"No {domain} records between {_d(start)} and {_d(end)} - "
               "nothing to characterise.")
    obs = [observe(topic, start, end, status, summary,
                   reference=None,
                   evidence=_evidence(domain, cur), coverage=cov,
                   sufficiency=quality["sufficiency"],
                   limitations=limitations)]
    obs[0]["data_quality"] = quality
    obs[0]["detail"] = cur

    if compare_to is not None:
        r0, r1 = _clamp(p, compare_to[0], compare_to[1])
        ref = fn(p, r0, r1)
        rcov = _domain_coverage(p, domain, r0, r1)
        ref_quality = _period_quality(p, domain, r0, r1, ref)
        line, st = _delta_summary(domain, cur, ref)
        if missing or ref_quality["recorded_days"] == 0:
            line, st = "A period lacks records; no change can be established.", "insufficient"
        cmp_obs = observe(
            f"{topic}.comparison", start, end, st, line,
            reference=f"period {_d(r0)} to {_d(r1)}",
            evidence=[ev("this_period", cur, "detail", DERIVED),
                      ev("reference_period", ref, "detail", DERIVED)],
            coverage=cov, sufficiency=("insufficient" if st == "insufficient"
                                      else "not_assessed"),
            limitations=["a comparison is not a cause",
                         "a reference period is not necessarily a representative personal baseline"])
        cmp_obs["data_quality"].update(quality)
        if st == "insufficient":
            cmp_obs["data_quality"]["sufficiency"] = "insufficient"
        cmp_obs["reference_data_quality"] = ref_quality
        cmp_obs["reference_window"] = {"start": _d(r0), "end": _d(r1)}
        cmp_obs["reference_detail"] = ref
        cmp_obs["reference_coverage"] = rcov
        obs.append(cmp_obs)

    result = {"domain": domain,
            "window": {"start": _d(start), "end": _d(end)},
            "compare_to": ({"start": _d(_clamp(p, *compare_to)[0]),
                            "end": _d(_clamp(p, *compare_to)[1])}
                           if compare_to else None),
            "observations": obs}
    if include_statistics:
        result["statistics"] = period_statistics(
            p, domain, start, end, cur,
            (r0, r1) if compare_to is not None else None,
            ref if compare_to is not None else None)
    return result


# --------------------------------------------------------------------- #
# The record in one read - what the coach's read_record tool returns
# --------------------------------------------------------------------- #
# The coach used to assemble this from a snapshot, a planning context and
# one drill-down per domain, each wrapping a few values in several layers
# of observation, evidence, detail and statistics. Here every domain comes
# once, as values with units, against the window just before, and the
# limits are stated once.

RECORD_LIMITATIONS = [
    "Values describe recorded data only: a day without a record is unknown, not zero or normal.",
    "Record counts are not calendar days, and per-session values (training medians) are not daily totals.",
    "A difference between the two windows is a comparison, not a cause; the previous window "
    "is not necessarily a representative baseline.",
    "Self-reported answers are words relative to the person's own normal.",
    "Device distance and calories are estimates.",
]


def _window_values(p: Profile, domain: str, start, end) -> dict:
    fn, _ = INSPECT_DOMAINS[domain]
    values = {k: v for k, v in fn(p, start, end).items() if k != "provenance" and v not in (None, [], {})}
    return {"days_with_records": len(_domain_days(p, domain, start, end)),
            "calendar_days": _span_days(start, end), **values}


def read_record(p: Profile, days: int = 28, end: dt.date | None = None) -> dict:
    """Every domain over the last `days` days to `end`, against the window before.

    `p` is already sealed at the simulation date (or the event boundary), so
    nothing here can reach later data.
    """
    import plan
    if p.today is None:
        raise ValueError("cannot read an empty record")
    days = max(1, min(int(days), 120))
    end = min(end or p.today, p.today)
    start = max(p.first_day, end - dt.timedelta(days=days - 1))
    ref_end = start - dt.timedelta(days=1)
    ref_start = max(p.first_day, ref_end - dt.timedelta(days=days - 1))
    has_ref = ref_end >= p.first_day
    domains = {}
    for domain in INSPECT_DOMAINS:
        this = _window_values(p, domain, start, end)
        before = _window_values(p, domain, ref_start, ref_end) if has_ref else None
        block = {"this_window": this, "previous_window": before,
                 "units": {k: FIELD_META[domain][k][0] for k in (this | (before or {}))
                           if k in FIELD_META.get(domain, {})}}
        if before and this["days_with_records"] and before["days_with_records"]:
            block["change"] = {k: round(this[k] - before[k], 2) for k in this
                               if k in before and k not in ("days_with_records", "calendar_days")
                               and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                       for x in (this[k], before[k]))}
        domains[domain] = block
    balance = p.sleep_debt(days=_span_days(start, end), upto=end)
    if balance:
        domains["sleep"]["balance_against_own_usual"] = balance
    context = plan.planning_context(p, end)
    load = dict(context["training_load"])
    if load["chronic_28d_per_week"] < gl.value("min_chronic_load"):
        load["acwr"] = None
        load["acwr_withheld"] = (f"28-day load is below {gl.value('min_chronic_load'):.0f} sRPE/week, "
                                 "too light for a 7-to-28-day ratio to mean anything")
    context = {**context, "training_load": load}
    state = {k: context[k] for k in ("capacity_today", "habits", "weekday_rhythm", "training_load",
                                     "sleep_debt_7d", "resting_hr", "readiness_trend",
                                     "activity_vs_who", "ambient_activity", "recent_injuries")}
    # The full injury history is long for some people; the window's own reports
    # are under domains.injury, so the state keeps the summary.
    state["recent_injuries"] = {k: v for k, v in state["recent_injuries"].items()
                                if k not in ("dates", "reports")}
    quality = _snap_data_quality(p, end)
    return {"person": p.pid,
            "window": {"start": _d(start), "end": _d(end)},
            "previous_window": {"start": _d(ref_start), "end": _d(ref_end)} if has_ref else None,
            "findings": [o["summary"] for o in snapshot(p, end)["top_findings"]],
            "domains": domains,
            "state_at_window_end": {"as_of": _d(end), **state},
            "data_quality": {"summary": quality["summary"], "status": quality["status"],
                             **{e["name"]: e["value"] for e in quality["evidence"]}},
            "limitations": RECORD_LIMITATIONS}


def demo() -> None:
    import json
    p = Profile("p05")
    snap = snapshot(p)
    print(f"=== snapshot {p.pid} as of {snap['as_of']} ===")
    for name, d in snap["domains"].items():
        print(f"  {name:<11} [{d['status']}] {d['headline']}")
    print(f"  data_quality [{snap['data_quality']['status']}] "
          f"{snap['data_quality']['summary']}")
    print(f"\n  {len(snap['top_findings'])} top finding(s):")
    for o in snap["top_findings"]:
        print(f"    - {o['topic']}: {o['summary']}")

    print("\n=== inspect training, Jan vs Feb 2020 ===")
    r = inspect(p, "training",
                dt.date(2020, 1, 1), dt.date(2020, 1, 31),
                compare_to=(dt.date(2020, 2, 1), dt.date(2020, 2, 29)))
    for o in r["observations"]:
        print(f"  {o['topic']} [{o['status']}] {o['summary']}")

    print("\n=== read_record, last 28 days ===")
    rec = read_record(p)
    for name, block in rec["domains"].items():
        print(f"  {name:<11} {block['this_window']['days_with_records']} of "
              f"{block['this_window']['calendar_days']} days with records")

    print("\ncontract fields:", list(snap["data_quality"]))
    json.dumps(snap, default=str)      # must be serialisable


if __name__ == "__main__":
    demo()
