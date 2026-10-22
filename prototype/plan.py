"""
The deterministic side of planning: facts in, never a plan.

The agent authors the training plan; this module never does.
**planning_context** computes everything the record can contribute to a
training decision - today's capacity ceiling with its reasons, the
person's actual habits with recovery windows and the joints each loads,
load trend, sleep debt, resting heart rate, WHO adequacy, recent logged
injuries, data quality - plus an honest list of what only the person can
supply. It hands the agent facts, not a schedule, and nothing checks the
plan against them: the plan experiments in history/evidence-2026-10-04/plan-experiment found
that rule checks mostly made plans over-cautious.

Every health-related threshold it branches on comes from `guidelines.py`, so
each carries its source and evidence strength.

Run:  ../.venv/bin/python plan.py
"""

from __future__ import annotations

import datetime as dt

import guidelines as gl
import realtime
from profile import MODALITY, Profile, hr_position, wellness_words

# Intensity is ordered so a ceiling can be compared, not enumerated.
INTENSITY = ["rest", "easy", "moderate", "hard"]

ACWR_CEILING = gl.value("acwr_ceiling")
MIN_CHRONIC_LOAD = gl.value("min_chronic_load")
READINESS_FLOOR = gl.value("readiness_floor")
LOW_READINESS_STREAK = gl.value("low_readiness_streak")
SORENESS_FLOOR = gl.value("soreness_floor")
HR_ALERT_QUANTILE = gl.value("hr_alert_quantile")
SLEEP_DEBT_HOURS = gl.value("sleep_debt_hours")
RECENT_INJURY_DAYS = gl.value("recent_injury_days")

# The names a plan's activities use: the catalogue labels, plus rest.
ACTIVITY_NAMES = sorted(v["label"] for v in MODALITY.values())
MAX_SESSION_MINUTES = 300     # a structural input limit, not coaching guidance


# =====================================================================
# How much capacity is there today? Every domain gets a vote.
# =====================================================================

def daily_capacity(p: Profile,
                   day: dt.date | None = None) -> tuple[str, list[str], list[str]]:
    """Return (intensity ceiling, reasons, cautions).

    Reasons are what forced the ceiling down. Cautions are things worth
    saying that did not change the answer - a coach mentions the missed
    breakfasts even when they do not alter today's session.
    """
    day = day or p.today
    reasons, cautions = [], []
    ceiling = "hard"

    def cap(level: str, why: str) -> None:
        nonlocal ceiling
        if INTENSITY.index(level) < INTENSITY.index(ceiling):
            ceiling = level
        reasons.append(why)

    # --- self-reported state ----------------------------------------
    rep = next((r for r in p.reports if r.date == day), None)
    streak = 0
    for r in reversed([r for r in p.reports if r.date <= day]):
        if r.readiness is not None and r.readiness < READINESS_FLOOR:
            streak += 1
        else:
            break
    if streak >= LOW_READINESS_STREAK:
        cap("rest", f"low or very low readiness to train on {streak} "
                    "reports running")
    elif rep and rep.readiness is not None and rep.readiness < READINESS_FLOOR:
        cap("easy", f"today's report: {wellness_words('readiness', rep.readiness)}")
    if rep and rep.soreness is not None and rep.soreness < SORENESS_FLOOR:
        cap("easy", f"today's report: {wellness_words('soreness', rep.soreness)}")

    # --- sleep -------------------------------------------------------
    debt = p.sleep_debt(days=7, upto=day)
    if debt and debt["debt_hours"] >= SLEEP_DEBT_HOURS:
        cap("moderate", f"you are {debt['debt_hours']:.1f}h short of your own "
                        f"usual sleep over the last {debt['nights']} nights")
    elif debt and debt["debt_hours"] >= SLEEP_DEBT_HOURS / 2:
        cautions.append(f"sleep is running {debt['debt_hours']:.1f}h below your "
                        "usual for the week")

    # --- physiology --------------------------------------------------
    hr = p.resting_hr_position(day)
    if hr and hr["quantile"] >= HR_ALERT_QUANTILE:
        cap("easy", f"resting heart rate is {hr_position(hr)} your own last "
                    f"{hr['baseline_days']} days")

    # --- training load -----------------------------------------------
    load = p.training_load(day)
    # The ratio is only applied where its denominator means something.
    # Without this guard the rule inverts for exactly the people who
    # most need to move: p16 trains 17 minutes a week, so any session
    # at all scores 2.3x and the coach forbids it. That is the
    # mathematical-coupling problem the ACWR literature documents,
    # arriving in our own data - see guidelines.py["min_chronic_load"].
    if load["chronic_28d_per_week"] < MIN_CHRONIC_LOAD:
        cautions.append("your recent training is too light for a load "
                        "ratio to say anything, so it was not applied")
    elif load["acwr"] is not None and load["acwr"] > ACWR_CEILING:
        cap("moderate", f"your 7-day load is {load['acwr']}x your 28-day "
                        f"average, past the {ACWR_CEILING} tripwire")

    # --- injury recency ----------------------------------------------
    # Said, not enforced: the person's own account of current pain decides.
    # Capping on it made plans over-cautious for people who said they no
    # longer hurt (history/evidence-2026-10-04/ablation-experiment).
    inj = p.injury_history(upto=day)
    if inj["days_since_last"] is not None and \
            inj["days_since_last"] <= RECENT_INJURY_DAYS:
        cautions.append(f"you reported an injury ({inj['last_location']}) "
                        f"{inj['days_since_last']} days ago")

    # --- intake: mentioned, never used to cap on its own -------------
    # Eating and drinking are reported here because a coach
    # would say them out loud, not because the link to today's capacity
    # is established. Promoting either to a ceiling would be asserting a
    # causal claim this data cannot support.
    eat = p.eating_pattern(days=14, upto=day)
    if eat and eat["breakfast_share"] < 0.5:
        cautions.append(f"you have had breakfast on only "
                        f"{eat['breakfast_share']:.0%} of the last "
                        f"{eat['days']} recorded days")
    if eat and eat["median_meals_per_day"] < 3:
        cautions.append(f"a median of {eat['median_meals_per_day']:.0f} meals a "
                        "day is low if you are training regularly")
    drink = p.drinking_pattern(days=14, upto=day)
    if drink and drink["alcohol_share"] and drink["alcohol_share"] > 0.3:
        cautions.append(f"alcohol on {drink['alcohol_share']:.0%} of recorded "
                        "days; this does not establish an effect on your sleep")

    # --- body --------------------------------------------------------
    w = p.weight_trend(upto=day)
    if w and w["direction"] != "stable" and abs(w["kg_per_week"] or 0) > 0.5:
        cautions.append(f"weight is trending {w['direction']} at "
                        f"{abs(w['kg_per_week']):.1f} kg/week")

    if not reasons:
        reasons.append("no encoded recovery rule lowered the intensity ceiling; "
                       "this does not establish that all recovery markers are normal")
    return ceiling, reasons, cautions


# =====================================================================
# Everything the record can contribute to a plan, in one read
# =====================================================================

def planning_context(p: Profile, as_of: dt.date | None = None) -> dict:
    """Decision-ready planning facts for the agent, sealed at `as_of`.

    Facts, trends, uncertainty and provenance - never a schedule. The
    final key states plainly what the record cannot know, which is the
    agent's cue to ask rather than assume.
    """
    day = as_of or p.today
    ceiling, reasons, cautions = daily_capacity(p, day)
    logged = sorted(p.recent_injured_joints(
        within_days=int(RECENT_INJURY_DAYS), upto=day))

    habits = {}
    for mod, h in p.training_habits(day).items():
        m = MODALITY.get(mod, {})
        habits[mod] = {**h, "recovery_hours": m.get("recoveryHours", 0),
                       "loads_joints": m.get("loadsJoint", []),
                       "spares_joints": m.get("sparesJoint", [])}

    import observe
    dq = observe._snap_data_quality(p, day)
    raw_start = max(p.first_day, day - dt.timedelta(days=27))
    ambient = realtime.period_summary(p.pid, raw_start, day)["summary"]

    return {
        "person": p.pid,
        "as_of": str(day),
        "capacity_today": {"ceiling": ceiling, "reasons": reasons,
                           "cautions": cautions,
                           # Plans read it as a cap on every day without this.
                           "applies_to": "today and tomorrow; it does not cap later days, "
                                         "which depend on how the person recovers"},
        "intensity_levels": INTENSITY,
        "habits": habits,
        "weekday_rhythm": p.training_rhythm(day),
        "training_load": p.training_load(day),
        "sleep_debt_7d": p.sleep_debt(days=7, upto=day),
        "resting_hr": p.resting_hr_position(day),
        "readiness_trend": p.readiness_trend(upto=day),
        "activity_vs_who": p.activity_adequacy(upto=day),
        "ambient_activity": {
            "window": {"start": str(raw_start), "end": str(day)},
            "heart_days": ambient["heart_days"],
            "median_heart_covered_minutes": ambient["median_heart_covered_minutes"],
            "median_daily_steps": ambient["median_daily_steps"],
            "total_distance_km": ambient["total_distance_km"],
            "limitations": ["distance and calories are device estimates",
                            "missing samples are not treated as zero"],
        },
        "recent_injuries": {"joints_from_log": logged,
                            **p.injury_history(upto=day)},
        "modality_catalogue": {
            m: {"label": v["label"], "recovery_hours": v["recoveryHours"],
                "loads_joints": v["loadsJoint"],
                "spares_joints": v["sparesJoint"]}
            for m, v in MODALITY.items()},
        "data_quality": dq,
        "only_the_person_knows": [
            "current pain or niggles, today",
            "their goal for this plan",
            "which weekdays they can or cannot train",
            "equipment access and activities they dislike",
        ],
    }


# =====================================================================
# Demo
# =====================================================================

def demo() -> None:
    p = Profile("p16")
    ctx = planning_context(p)
    print(f"{p.pid} as of {ctx['as_of']}: ceiling {ctx['capacity_today']['ceiling']}, "
          f"habits {list(ctx['habits'])}")
    assert ctx["capacity_today"]["ceiling"] in INTENSITY
    assert "rest" in ACTIVITY_NAMES and "running" in ACTIVITY_NAMES


if __name__ == "__main__":
    demo()
