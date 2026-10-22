"""
External knowledge, and where every threshold came from.

Two things live here, and they live together on purpose.

**Guidelines** are published recommendations the system may cite back to
the user. They are the only place absolute standards enter: everywhere
else the system compares a person to their own history, which is the
right reference for "is today unusual" and the wrong one for "is this
enough". A person who has always been inactive has an unremarkable
personal baseline, so a purely personal system will never tell them
they are under-active. That blind spot is structural, not a bug, and
this file is the fix.

**Thresholds** are the numbers the decision layer actually branches on.
Each one carries its source and how strong that source is, because in
the previous version they were bare module constants and an invented
number was indistinguishable from a cited one. `strength` is the honest
part:

    guideline   a published recommendation from a standards body
    literature  a peer-reviewed finding in general use
    contested   in general use, but with substantial published criticism
    heuristic   we chose it; no source claims this number

Nothing here is load-bearing for safety on its own. Contraindication
checks do not consult this file. Sources were checked against
primary papers and official publications on 2026-09-01. Device-specific
adaptations remain labelled heuristic even where a source motivated them.

Run:  ../.venv/bin/python guidelines.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Guideline:
    """A published recommendation, quotable in an explanation."""
    key: str
    says: str
    source: str
    strength: str


@dataclass(frozen=True)
class Threshold:
    """A number the decision layer branches on, plus where it came from."""
    key: str
    value: float
    unit: str
    why: str
    source: str
    strength: str


# =====================================================================
# Guidelines - the absolute standards
# =====================================================================

GUIDELINES = {g.key: g for g in [
    Guideline(
        "who_aerobic",
        "Adults should do 150-300 minutes of moderate-intensity aerobic "
        "activity per week, or 75-150 minutes of vigorous-intensity "
        "activity, or an equivalent combination.",
        "WHO (2020), Guidelines on Physical Activity and Sedentary Behaviour",
        "guideline"),
    Guideline(
        "who_strength",
        "Adults should also do muscle-strengthening activity at "
        "moderate or greater intensity on 2 or more days a week.",
        "WHO (2020), Guidelines on Physical Activity and Sedentary Behaviour",
        "guideline"),
    Guideline(
        "who_sedentary",
        "Adults should limit the amount of time spent being sedentary, "
        "and replacing sedentary time with activity of any intensity "
        "provides health benefits.",
        "WHO (2020), Guidelines on Physical Activity and Sedentary Behaviour",
        "guideline"),
    Guideline(
        "sleep_duration",
        "Adults should sleep 7 or more hours per night on a regular "
        "basis to promote optimal health.",
        "Watson et al. (2015), AASM/SRS consensus; Hirshkowitz et al. "
        "(2015), National Sleep Foundation",
        "guideline"),
    Guideline(
        "acsm_recovery",
        "FitCoach conservatively separates higher-intensity resistance work "
        "for the same muscle group by 48 hours.",
        "Project guardrail informed by ACSM (2019), A Road Map to Effective "
        "Muscle Recovery; not established by the ACSM Position Stand",
        "heuristic"),
]}

# WHO counts a vigorous minute as worth two moderate ones (150-300
# moderate is stated as equivalent to 75-150 vigorous). Applying that
# equivalence to Fitbit's named buckets is a device-dependent project
# assumption, not a WHO validation of Fitbit classification.
VIGOROUS_WEIGHT = 2


# =====================================================================
# Thresholds - what the decision layer branches on
# =====================================================================

THRESHOLDS = {t.key: t for t in [
    Threshold(
        "acwr_ceiling", 1.5, "ratio",
        "Acute:chronic workload above this is treated as a load spike "
        "and caps intensity.",
        "Gabbett (2016), Br J Sports Med 50(5):273-280. Substantial "
        "later criticism: Impellizzeri et al. (2020) on conceptual and "
        "statistical pitfalls, and Lolli et al. (2019) on mathematical "
        "coupling between numerator and denominator.",
        "contested"),
    Threshold(
        "acute_days", 7, "days",
        "Project window for acute load.",
        "Project policy informed by common ACWR formulations; not a universal "
        "or clinically validated window.",
        "heuristic"),
    Threshold(
        "chronic_days", 28, "days",
        "Project window for chronic load.",
        "Project policy informed by common ACWR formulations; not a universal "
        "or clinically validated window.",
        "heuristic"),
    Threshold(
        "readiness_floor", 4, "0-10 scale",
        "Self-rated readiness below this counts as a low day.",
        "PMSys wellness instrument; the cut point is ours. The "
        "instrument descends from Hooper & Mackinnon (1995).",
        "heuristic"),
    Threshold(
        "low_readiness_streak", 3, "days",
        "Consecutive low-readiness days that force a full deload; the "
        "monitor's readiness detector fires on the same streak.",
        "Ours. Chosen so a single bad night does not trigger a deload.",
        "heuristic"),
    Threshold(
        "soreness_floor", 3, "1-5 scale, 3 is normal",
        "Self-rated soreness below this (sorer than normal) caps intensity.",
        "PMSys wellness instrument; 3 is its normal and 1-2 below normal, "
        "where below normal means sorer. Capping on a below-normal answer "
        "is ours.",
        "heuristic"),
    Threshold(
        "unrated_run_reports", 14, "consecutive reports",
        "A wellness answer left at one end of its scale for this many "
        "reports running is treated as not answered.",
        "Ours, from the data: one participant answered readiness 0 on 48 "
        "reports running while every other answer varied. No other end-of-"
        "scale run in PMData reaches 14; runs of the middle answer do and "
        "are kept.",
        "heuristic"),
    Threshold(
        "hr_alert_quantile", 0.90, "quantile",
        "Resting heart rate this high within the person's own recent "
        "window is treated as unusual for them.",
        "Ours. No published threshold exists for a personal quantile; "
        "the alternative was a fixed bpm offset, which is meaningless "
        "across people whose resting rates differ by 40 bpm.",
        "heuristic"),
    Threshold(
        "sleep_debt_hours", 3.0, "hours",
        "Cumulative shortfall against the person's own baseline over a "
        "week that caps intensity.",
        "Ours. The 7-hour guideline is population-level; this is a "
        "personal-deficit rule and no source states this number.",
        "heuristic"),
    Threshold(
        "recent_injury_days", 14, "days",
        "An injury reported within this window is mentioned when planning; "
        "the person's own account of current pain decides.",
        "Ours.",
        "heuristic"),
    Threshold(
        "baseline_min_days", 14, "days",
        "Below this much history the system returns no opinion rather "
        "than a default.",
        "Ours. Deliberately conservative: silence is honest for a new "
        "user where reassurance would not be.",
        "heuristic"),
    Threshold(
        "who_weekly_mvpa", 150, "minutes/week",
        "Lower bound of the WHO aerobic recommendation, in "
        "moderate-equivalent minutes.",
        "WHO (2020). See GUIDELINES['who_aerobic'].",
        "guideline"),
    Threshold(
        "who_weekly_mvpa_upper", 300, "minutes/week",
        "Upper bound of the WHO aerobic range; meeting it is the "
        "stronger recommendation.",
        "WHO (2020). See GUIDELINES['who_aerobic'].",
        "guideline"),
    Threshold(
        "min_chronic_load", 300, "sRPE/week",
        "Below this much chronic load the acute:chronic ratio is not "
        "applied at all, because the denominator is too small for the "
        "ratio to mean anything.",
        "Ours, but it exists because of the published criticism of "
        "ACWR: with a near-zero denominator any activity registers as a "
        "spike. Measured on PMData the two least-active participants "
        "sit at 92 sRPE/week, where an ordinary walk scores 2.3x and "
        "the untreated rule forbids all activity. 300 is roughly two "
        "typical sessions.",
        "heuristic"),
    Threshold(
        "quiet_days", 7, "days",
        "Days without any new record before the monitor reports the "
        "silence itself; a silent monitor and a healthy user otherwise "
        "look identical from outside.",
        "Ours.",
        "heuristic"),
    Threshold(
        "inactive_weeks", 4, "weeks",
        "Weeks below the WHO activity floor before under-activity is "
        "raised; one adequacy window, since a single low week is noise.",
        "Ours; the floor itself is WHO (2020), the window is not.",
        "heuristic"),
    Threshold(
        "intraday_bucket_minutes", 5, "minutes",
        "Bucket width the intraday heart-rate detector aggregates raw "
        "samples into.",
        "Ours. Small enough to see a short episode, large enough that a "
        "single spurious sample cannot fill a bucket.",
        "heuristic"),
    Threshold(
        "intraday_hr_delta", 30, "bpm",
        "Bucket-mean heart rate this far above the personal daily resting "
        "reference counts as elevated.",
        "Ours. An absolute personal offset rather than a population zone; "
        "no source states this number for wrist-worn data.",
        "heuristic"),
    Threshold(
        "intraday_low_steps", 5, "steps/bucket",
        "At or below this many recorded steps a bucket counts as low "
        "movement. Low recorded steps are only a proxy for rest, not "
        "proof of it - unrecorded movement, cycling or isometric effort "
        "leave the counter still.",
        "Ours. Zero would miss counter jitter; a larger value would admit "
        "real walking.",
        "heuristic"),
    Threshold(
        "intraday_run_buckets", 2, "consecutive buckets",
        "Consecutive qualifying buckets before the intraday detector "
        "fires; one bucket alone is noise.",
        "Ours.",
        "heuristic"),
    Threshold(
        "intraday_hr_confidence", 2, "Fitbit confidence 0-3",
        "Raw heart-rate samples below this device confidence are excluded "
        "from every intraday read.",
        "Project adaptation of Fitbit's own per-sample confidence field; "
        "Fitbit does not publish a recommended cutoff.",
        "heuristic"),
    Threshold(
        "wear_minutes_min", 600, "minutes/day",
        "Device wear time below this makes a day's activity totals "
        "unusable.",
        "Project adaptation informed by Troiano et al. (2008), whose 10-hour "
        "criterion used waist-worn ActiGraph data, not summed Fitbit buckets.",
        "heuristic"),
]}


def value(key: str) -> float:
    """The number, for call sites that only need the number."""
    return THRESHOLDS[key].value


def cite(key: str) -> str:
    """A short attribution suitable for showing to a user."""
    g = GUIDELINES[key]
    return f"{g.says} ({g.source.split(',')[0]})"


def provenance_report() -> list[str]:
    """Every threshold with its evidence class, worst first."""
    order = {"heuristic": 0, "contested": 1, "literature": 2, "guideline": 3}
    rows = sorted(THRESHOLDS.values(), key=lambda t: (order[t.strength], t.key))
    return [f"{t.strength:>10}  {t.key:<24} {t.value:>6} {t.unit}"
            for t in rows]


def main() -> None:
    print("Thresholds by evidence strength\n")
    for line in provenance_report():
        print(" ", line)

    counts: dict[str, int] = {}
    for t in THRESHOLDS.values():
        counts[t.strength] = counts.get(t.strength, 0) + 1
    print(f"\n  {len(THRESHOLDS)} thresholds: "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print("\n  Of these, the heuristics are the ones a reader should "
          "discount.\n  test_agent.py sweeps them to show the "
          "conclusions do not\n  hinge on the exact values.")

    print(f"\nGuidelines ({len(GUIDELINES)})\n")
    for g in GUIDELINES.values():
        print(f"  {g.key}\n    {g.says}\n    -- {g.source}\n")


if __name__ == "__main__":
    main()
