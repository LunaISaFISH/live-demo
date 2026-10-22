"""
One person's history, as a set of questions a coach can ask.

This is the semantic layer. Every method reads as a question - *how do
they usually sleep? is today's resting heart rate unusual for them? when
did they last train hard?* - and behind each one is a real computation
over the raw record. The agent asks questions; it never touches a CSV.

Two design rules shape the file.

**Questions, not columns.** There is no `get_sleep_data()`. There is
`sleep_baseline()` and `sleep_debt()` and `sleep_agreement()`, because
those are the things a coach actually needs and each has a defensible
definition. A column accessor would push the definition back onto the
caller, which is exactly what this layer exists to prevent.

**Personal baselines throughout.** Nothing is compared to a population
norm. "Unusual" always means unusual *for this person*, measured against
their own preceding window, and every method returns `None` rather than
a guess when the history is too short to say.

Coverage across the 16 PMData participants:

  training sessions    783 self-reported + 2,440 device-logged
  wellness self-report 1,747 days (readiness, fatigue, soreness, mood, stress)
  sleep                1,942 nights staged, 1,836 scored
  resting heart rate   1,803 days (14 of 16 participants)
  heart-rate zones     2,178 days
  body weight          1,295 days (15 of 16)
  meals and drink      1,569 days (15 of 16)
  injuries             225 reports

Run:  ../.venv/bin/python profile.py
"""

from __future__ import annotations

import ast
import copy
import csv
import datetime as dt
import json
import os
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import guidelines as gl
import overview as overview_lib

ROOT = Path(__file__).parent.parent / "data" / "pmdata"
# The three representative participants that tests, evaluations and demos
# use; together they reach every workflow path. web/app.js offers the same
# three on the page.
PARTICIPANTS = ("p05", "p12", "p16")
MODALITY_TTL = Path(__file__).parent / "modality.ttl"

# Every branching number is declared in guidelines.py with its source
# and how strong that source is. Deriving them here rather than writing
# literals keeps call sites readable while making an invented number
# distinguishable from a cited one - previously they looked identical.
ACUTE_DAYS = int(gl.value("acute_days"))
CHRONIC_DAYS = int(gl.value("chronic_days"))
BASELINE_WINDOW = CHRONIC_DAYS   # a personal baseline spans the chronic window
BASELINE_MIN = int(gl.value("baseline_min_days"))
REPERTOIRE_MIN = 3        # times before an activity counts as a habit
WEAR_MIN = int(gl.value("wear_minutes_min"))

# A missing day in the Fitbit exports is a zero with a null date, not an
# absent record. Filtering on None matches nothing and silently admits
# 145 zero-bpm readings, 8% of the resting-HR file.
FITBIT_MISSING = 0.0

# PMSys wellness answers. The dataset describes fatigue, mood, sleep quality,
# soreness and stress on 1-5, where 3 is normal, 1-2 below normal and 4-5
# above normal, and readiness to train on 0-10. "Above normal" is the better
# state: reports naming a sore area average 1.9 soreness against 3.1 without,
# and readiness rises with the fatigue score (test_calculations checks both
# on the data). Tools show these words, never the numbers, so no reader has
# to know which end of a scale is good.
WELLNESS_NORMAL = 3
WELLNESS_WORDS = {
    "fatigue": ("much more tired than normal", "more tired than normal", "normal energy",
                "fresher than normal", "much fresher than normal"),
    "soreness": ("much more sore than normal", "more sore than normal", "normal soreness",
                 "less sore than normal", "much less sore than normal"),
    "stress": ("much more stressed than normal", "more stressed than normal", "normal stress",
               "more relaxed than normal", "much more relaxed than normal"),
    "mood": ("much worse mood than normal", "worse mood than normal", "normal mood",
             "better mood than normal", "much better mood than normal"),
    "sleep_quality": ("much worse sleep than normal", "worse sleep than normal",
                      "normal sleep quality", "better sleep than normal",
                      "much better sleep than normal"),
}
# Readiness to train, 0-10, in bands; below 4 is a low report (readiness_floor).
READINESS_WORDS = ((1, "very low readiness to train"), (3, "low readiness to train"),
                   (6, "moderate readiness to train"), (8, "high readiness to train"),
                   (10, "very high readiness to train"))
# Session effort on the Borg CR10 scale the dataset's sRPE uses, with its own anchors.
EFFORT_WORDS = ((0, "rest"), (1, "very easy effort"), (2, "easy effort"), (3, "moderate effort"),
                (4, "somewhat hard effort"), (6, "hard effort"), (8, "very hard effort"),
                (10, "maximal effort"))
WELLNESS_RANGE = {"readiness": (0, 10), **{k: (1, 5) for k in WELLNESS_WORDS}}
UNRATED_RUN = int(gl.value("unrated_run_reports"))


def hr_position(hr: dict) -> str:
    """Where a resting heart rate sits in the person's own recent days, in words.

    Percentages alone read wrongly at the ends ("in the top 0%") and for
    low readings ("in the top 70%").
    """
    q = hr["quantile"]
    if q >= 0.995:
        return "higher than on any other day in"
    if q <= 0.005:
        return "lower than on any other day in"
    return f"in the top {1 - q:.0%} of" if q >= 0.5 else f"in the bottom {q:.0%} of"


def wellness_words(field: str, value: float | None) -> str | None:
    """A self-report answer as the words a person would use for it."""
    if value is None:
        return None
    if field in ("readiness", "rpe"):
        bands = READINESS_WORDS if field == "readiness" else EFFORT_WORDS
        return next(words for top, words in bands if value <= top)
    return WELLNESS_WORDS[field][min(5, max(1, round(value))) - 1]


def wellness_summary(reports, field: str) -> str | None:
    """How often each answer was given, most common first, and the latest."""
    values = [getattr(r, field) for r in reports if getattr(r, field) is not None]
    if not values:
        return None
    counts = Counter(wellness_words(field, v) for v in values)
    shares = ", ".join(f"{words} {n}" for words, n in counts.most_common())
    return f"{shares} (of {len(values)} reports); latest: {wellness_words(field, values[-1])}"


def _unrated(reports: list) -> list:
    """Blank answers left at one end of a scale, unchanged, for weeks.

    One person answered readiness 0 on 48 reports running while every
    other answer varied, then 5 almost every time: an untouched slider,
    not a rating. Long runs of the middle answer are common and kept.
    """
    out = list(reports)
    for field, (low, high) in WELLNESS_RANGE.items():
        start = 0
        for i in range(1, len(out) + 1):
            if i < len(out) and getattr(out[i], field) == getattr(out[start], field):
                continue
            value = getattr(out[start], field)
            if value in (low, high) and i - start >= UNRATED_RUN:
                for j in range(start, i):
                    out[j] = replace(out[j], **{field: None})
            start = i
    return out


# =====================================================================
# Modality vocabulary, read from the TTL so there is one source of truth
# =====================================================================

def _load_modalities() -> tuple[dict[str, str], dict[str, dict]]:
    """Return (raw label -> modality, modality -> properties).

    Parsed with rdflib, not with string splitting. The first attempt
    split the file on blank lines and took the first token of each
    block, which silently named `Running` as `#` because a comment
    header sits directly above it - and a modality called `#` still
    counts sessions, so nothing crashed.

    No reasoner is involved. The TTL is a readable data file that keeps
    one source of truth for these definitions, and leaves the door open
    to putting inference back on top later.
    """
    from rdflib import Graph, Namespace, RDF
    from rdflib.namespace import RDFS

    FC = Namespace("http://ca6117.example/fitcoach#")
    g = Graph().parse(MODALITY_TTL, format="turtle")
    short = lambda u: str(u).split("#")[-1]

    raw2mod: dict[str, str] = {}
    props: dict[str, dict] = {}
    # Match on rdf:type specifically. A wildcard predicate also picks up
    # the `rdfs:domain fc:Modality` on every property declaration, which
    # yielded seven phantom modalities named `impact`, `rawName` and so on.
    for m in g.subjects(RDF.type, FC.Modality):
        name = short(m)
        props[name] = {
            "label": str(g.value(m, RDFS.label) or name),
            "impact": str(g.value(m, FC.impact) or ""),
            "demand": str(g.value(m, FC.demand) or ""),
            "recoveryHours": int(g.value(m, FC.recoveryHours) or 0),
            "loadsJoint": sorted(short(j) for j in g.objects(m, FC.loadsJoint)),
            "sparesJoint": sorted(short(j) for j in g.objects(m, FC.sparesJoint)),
        }
        for raw in g.objects(m, FC.rawName):
            raw2mod[str(raw).lower()] = name
        raw2mod.setdefault(name.lower(), name)
    return raw2mod, props


RAW2MOD, MODALITY = _load_modalities()

# The injury log records a body location and a severity, not a joint -
# {'right_knee': 'minor'}. Reading only the date (as the first version
# did) throws away exactly the thing that decides which activities are
# unsafe. Locations fold onto the canonical joint the modality table
# loads against; the few that are not training joints (chest, head/neck,
# stomach) map to None, so they are recorded but contraindicate nothing.
INJURY_LOCATION = {
    "right_knee": "Knee", "left_knee": "Knee",
    "right_foot": "Ankle", "left_foot": "Ankle",
    "right_leg": "Knee", "left_leg": "Knee", "right_thigh": "Knee",
    "groin_hip": "Hip",
    "right_shoulder": "Shoulder", "left_shoulder": "Shoulder",
    "right_elbow": "Elbow", "left_elbow": "Elbow",
    "right_hand": "Wrist", "left_hand": "Wrist",
    "stomach_back": "LumbarSpine",
    "chest": None, "head_neck": None, "stomach": None,
}


# =====================================================================
# Records
# =====================================================================

@dataclass(frozen=True)
class Session:
    date: dt.date
    modality: str
    minutes: int
    rpe: int | None
    load: float | None     # sRPE: rating x minutes; None without a rating
    source: str            # self-report | device | plan
    # Device evidence, present when Fitbit logged the session (either on
    # its own or merged onto a matched self-report). Kept as optional
    # provenance-tagged detail, never promoted into the load or a plan.
    sources: tuple = ()    # every source that fed this record
    avg_hr: int | None = None
    steps: int | None = None
    calories: int | None = None
    zones: tuple = ()      # ((zone_name, minutes), ...) from the exercise log
    start: dt.datetime | None = None


@dataclass(frozen=True)
class Night:
    date: dt.date
    asleep_min: int
    in_bed_min: int
    efficiency: int
    deep: int
    light: int
    rem: int
    awake: int
    score: int | None


@dataclass(frozen=True)
class DayReport:
    date: dt.date
    readiness: float | None
    fatigue: float | None
    soreness: float | None
    mood: float | None
    stress: float | None
    sleep_hours_reported: float | None
    sleep_quality: float | None = None      # 1-5 self-rating, distinct from duration
    soreness_area_codes: tuple = ()          # raw SNOMED codes, kept unmapped


@dataclass(frozen=True)
class Injury:
    """One injury report, with the location and severity the log carries."""
    date: dt.date
    location: str            # raw, e.g. "right_knee"
    joint: str | None        # canonical training joint, or None if not one
    severity: str            # "minor" | "major" | "unknown"


@dataclass(frozen=True)
class Intake:
    date: dt.date
    meals: list[str]
    weight: float | None
    fluid: int | None
    alcohol: bool | None


# =====================================================================
# Loading
# =====================================================================

def _rows(path) -> list[dict]:
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def _json(path):
    return json.load(open(path)) if os.path.exists(path) else []


def _num(x):
    try:
        return float(str(x).strip())
    except (ValueError, TypeError):
        return None


def _overlap(a_start, a_min, b_start, b_min) -> dt.timedelta:
    """Overlap between two sessions given start times and minute lengths."""
    if a_start is None or b_start is None:
        return dt.timedelta(0)
    a_end = a_start + dt.timedelta(minutes=a_min)
    b_end = b_start + dt.timedelta(minutes=b_min)
    return max(dt.timedelta(0),
               min(a_end, b_end) - max(a_start, b_start))


class Profile:
    """Everything recorded about one participant, and the questions it answers."""

    def __init__(self, pid: str, root: Path | None = None):
        self.pid = pid
        base = (root or ROOT) / pid
        self.sessions = self._sessions(base)
        self.nights = self._nights(base)
        self.reports = self._reports(base)
        self.intake = self._intake(base)
        self.resting_hr = self._resting_hr(base)
        self.zones = self._zones(base)
        self.active = self._active_minutes(base)
        self.injuries = self._injuries(base)
        # Validated participant-overview metadata (context, never a plan
        # rule) with its field-level quality warnings preserved.
        self.overview = overview_lib.for_pid(pid, (root or ROOT))
        dates = ([s.date for s in self.sessions] + [r.date for r in self.reports]
                 + list(self.resting_hr))
        self.today = max(dates) if dates else None
        self.first_day = min(dates) if dates else None

    def snapshot(self, day: dt.date) -> Profile:
        """Return the record exactly as it existed at the simulation day.

        PMData contains the whole study, but a simulated agent placed on
        `day` must not see what the dataset already knows about tomorrow.
        The lists are copied before filtering, so planners may append
        hypothetical sessions without changing the source record.
        """
        if self.first_day is None or self.today is None:
            raise ValueError("cannot snapshot an empty record")
        if not self.first_day <= day <= self.today:
            raise ValueError(
                f"snapshot day must be between {self.first_day} and {self.today}")

        out = copy.copy(self)
        out.sessions = [x for x in self.sessions if x.date <= day]
        out.nights = [x for x in self.nights if x.date <= day]
        out.reports = [x for x in self.reports if x.date <= day]
        out.intake = [x for x in self.intake if x.date <= day]
        out.injuries = [x for x in self.injuries if x.date <= day]
        out.resting_hr = {k: v for k, v in self.resting_hr.items() if k <= day}
        out.zones = {k: v for k, v in self.zones.items() if k <= day}
        out.active = {k: v for k, v in self.active.items() if k <= day}
        out.today = day
        return out

    # -- loaders ------------------------------------------------------
    def _sessions(self, base) -> list[Session]:
        """Reconcile self-report and Fitbit sessions on their timestamps.

        The old rule discarded every Fitbit activity sharing a day and
        modality with any self-report, which threw away distinct same-day
        sessions (a logged evening run and a separate lunchtime walk) and
        merged nothing. Self-report carries the end time and a duration,
        so a self-report spans [end - duration, end]; a Fitbit entry spans
        [start, start + activeDuration]. Same modality plus overlapping
        intervals is the same session: the device evidence is merged onto
        the self-report and both provenances are kept. A Fitbit entry that
        matches nothing survives as its own device session; a self-report
        with no device match keeps its self-reported detail alone.
        """
        selfrep = self._self_reports(base)
        device = self._device_sessions(base)

        used = [False] * len(device)
        merged = []
        for s in selfrep:
            best, best_ov = None, dt.timedelta(0)
            for i, d in enumerate(device):
                if used[i] or d.modality != s.modality:
                    continue
                ov = _overlap(s.start, s.minutes, d.start, d.minutes)
                if ov > best_ov:
                    best, best_ov = i, ov
            if best is not None and best_ov > dt.timedelta(0):
                d = device[best]
                used[best] = True
                merged.append(replace(
                    s, sources=("self-report", "device"), avg_hr=d.avg_hr,
                    steps=d.steps, calories=d.calories, zones=d.zones))
            else:
                merged.append(s)
        merged += [d for i, d in enumerate(device) if not used[i]]
        return sorted(merged, key=lambda s: (s.date, s.start or dt.datetime.min))

    def _self_reports(self, base) -> list[Session]:
        out = []
        for r in _rows(base / "pmsys" / "srpe.csv"):
            try:
                tags = ast.literal_eval(r.get("activity_names") or "[]")
                rpe, mins = int(r["perceived_exertion"]), int(r["duration_min"])
                end = dt.datetime.fromisoformat(r["end_date_time"][:19])
            except (ValueError, SyntaxError, KeyError):
                continue
            mod = next((RAW2MOD[t.lower()] for t in tags if t.lower() in RAW2MOD),
                       None)
            if mod:
                out.append(Session(end.date(), mod, mins, rpe, float(rpe * mins),
                                   "self-report", sources=("self-report",),
                                   start=end - dt.timedelta(minutes=mins)))
        return out

    def _device_sessions(self, base) -> list[Session]:
        out = []
        for r in _json(base / "fitbit" / "exercise.json"):
            name = (r.get("activityName") or "").lower()
            mins = int((r.get("activeDuration") or 0) / 60000)
            if name not in RAW2MOD or mins <= 0:
                continue
            try:
                start = dt.datetime.fromisoformat(r["startTime"][:19])
            except (ValueError, KeyError):
                continue
            zones = tuple((z.get("name"), z.get("minutes", 0))
                          for z in (r.get("heartRateZones") or [])
                          if z.get("minutes"))
            # No effort rating from the device, so no load: a nominal rating
            # made nine tenths of one person's "training load" invented.
            out.append(Session(
                start.date(), RAW2MOD[name], mins, None, None,
                "device", sources=("device",),
                avg_hr=r.get("averageHeartRate"), steps=r.get("steps"),
                calories=r.get("calories"), zones=zones, start=start))
        return out

    def _nights(self, base) -> list[Night]:
        scores = {}
        for r in _rows(base / "fitbit" / "sleep_score.csv"):
            try:
                scores[dt.date.fromisoformat(r["timestamp"][:10])] = \
                    int(r["overall_score"])
            except (ValueError, KeyError):
                pass
        out, seen = [], set()
        for s in _json(base / "fitbit" / "sleep.json"):
            # The export repeats a night at each monthly file boundary, same
            # logId: counted twice it made eight nights in a week.
            if not s.get("mainSleep") or s.get("logId") in seen:
                continue
            seen.add(s.get("logId"))
            try:
                day = dt.date.fromisoformat(s["dateOfSleep"])
            except (ValueError, KeyError):
                continue
            sm = (s.get("levels") or {}).get("summary") or {}
            g = lambda k: int((sm.get(k) or {}).get("minutes") or 0)
            out.append(Night(day, s.get("minutesAsleep") or 0,
                             s.get("timeInBed") or 0, s.get("efficiency") or 0,
                             g("deep"), g("light"), g("rem"), g("wake"),
                             scores.get(day)))
        return sorted(out, key=lambda n: n.date)

    def _reports(self, base) -> list[DayReport]:
        out = []
        for r in _rows(base / "pmsys" / "wellness.csv"):
            try:
                day = dt.date.fromisoformat(r["effective_time_frame"][:10])
            except (ValueError, KeyError):
                continue
            codes = ()
            raw_area = (r.get("soreness_area") or "").strip()
            if raw_area and raw_area not in ("[]", "{}"):
                try:            # a raw SNOMED code list; kept, never mapped
                    parsed = ast.literal_eval(raw_area)
                    codes = tuple(str(c) for c in parsed) \
                        if isinstance(parsed, (list, tuple)) else (str(parsed),)
                except (ValueError, SyntaxError):
                    codes = ()
            out.append(DayReport(day, _num(r.get("readiness")),
                                 _num(r.get("fatigue")), _num(r.get("soreness")),
                                 _num(r.get("mood")), _num(r.get("stress")),
                                 _num(r.get("sleep_duration_h")),
                                 _num(r.get("sleep_quality")), codes))
        return _unrated(sorted(out, key=lambda r: r.date))

    def _intake(self, base) -> list[Intake]:
        out = []
        for r in _rows(base / "googledocs" / "reporting.csv"):
            raw = (r.get("date") or "").strip()
            day = None
            for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
                try:
                    day = dt.datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if day is None:
                continue
            alcohol = {"yes": True, "no": False}.get(
                (r.get("alcohol_consumed") or "").strip().lower())
            meals = [m.strip() for m in (r.get("meals") or "").split(",")
                     if m.strip()]
            out.append(Intake(day, meals, _num(r.get("weight")),
                              int(_num(r.get("glasses_of_fluid")) or 0) or None,
                              alcohol))
        return sorted(out, key=lambda x: x.date)

    def _resting_hr(self, base) -> dict[dt.date, float]:
        out = {}
        for r in _json(base / "fitbit" / "resting_heart_rate.json"):
            v = (r.get("value") or {}).get("value")
            if v is None or v == FITBIT_MISSING:
                continue
            out[dt.date.fromisoformat(r["dateTime"][:10])] = float(v)
        return out

    def _zones(self, base) -> dict[dt.date, dict[str, float]]:
        out = {}
        for r in _json(base / "fitbit" / "time_in_heart_rate_zones.json"):
            z = ((r.get("value") or {}).get("valuesInZones") or {})
            if z:
                out[dt.date.fromisoformat(r["dateTime"][:10])] = \
                    {k: float(v) for k, v in z.items()}
        return out

    def _active_minutes(self, base) -> dict[dt.date, dict[str, int]]:
        """Fitbit's intensity buckets, with non-wear days dropped.

        The same trap as the resting-HR file, in a new costume. On a day
        the device was not worn Fitbit still emits a record: sedentary
        1440, everything else 0. Taken at face value that reads as a
        person who lay perfectly still for 24 hours, and across the 16
        participants there are 242 such days - 10% of the file, and 46%
        of one participant's. Counting them would manufacture inactivity
        rather than measure it.

        A day survives only if the device registered some movement and
        the four buckets account for at least the accepted wear-time
        minimum.
        """
        names = {"very": "very_active_minutes",
                 "moderate": "moderately_active_minutes",
                 "light": "lightly_active_minutes",
                 "sedentary": "sedentary_minutes"}
        cols: dict[str, dict[dt.date, int]] = {}
        for short, fname in names.items():
            col = {}
            for r in _json(base / "fitbit" / f"{fname}.json"):
                try:
                    col[dt.date.fromisoformat(r["dateTime"][:10])] = \
                        int(r["value"])
                except (ValueError, KeyError, TypeError):
                    continue
            cols[short] = col

        out = {}
        for day in cols.get("very", {}):
            rec = {k: cols[k].get(day, 0) for k in names}
            moving = rec["very"] + rec["moderate"] + rec["light"]
            if moving == 0 or sum(rec.values()) < WEAR_MIN:
                continue           # not worn, or worn too little to score
            out[day] = rec
        return out

    def _injuries(self, base) -> list[Injury]:
        """Parse each report into its locations and severities.

        The raw cell is a Python-literal dict, e.g. {'right_knee': 'minor',
        'left_foot': 'major'}. One report can name several places, so a
        report becomes one Injury per location rather than one date.
        """
        out = []
        for r in _rows(base / "pmsys" / "injury.csv"):
            v = (r.get("injuries") or "").strip()
            if not v or v in ("{}", ""):
                continue
            try:
                day = dt.date.fromisoformat(r["effective_time_frame"][:10])
                parsed = ast.literal_eval(v)
            except (ValueError, SyntaxError, KeyError):
                continue
            if not isinstance(parsed, dict):
                continue
            for loc, sev in parsed.items():
                loc = str(loc).strip().lower()
                sev = str(sev).strip().lower() or "unknown"
                out.append(Injury(day, loc,
                                  INJURY_LOCATION.get(loc, None), sev))
        return sorted(out, key=lambda x: x.date)

    # =================================================================
    # Shared helpers - the arithmetic every question leans on
    # =================================================================

    def _window(self, days: int, upto: dt.date | None = None):
        hi = upto or self.today
        return hi - dt.timedelta(days=days), hi

    @staticmethod
    def _quantile(past: list[float], value: float) -> float | None:
        """Where `value` falls inside `past`. None when past is too thin.

        Callers must treat None as *no opinion*, never as *normal*: for
        someone two weeks into using the system, silence is honest and
        reassurance is not.
        """
        if len(past) < BASELINE_MIN:
            return None
        below = sum(1 for p in past if p < value)
        ties = sum(1 for p in past if p == value)
        return (below + 0.5 * ties) / len(past)

    @staticmethod
    def _trend(pairs: list[tuple[dt.date, float]]) -> float | None:
        """Least-squares slope per day. None if fewer than a week of points."""
        if len(pairs) < 7:
            return None
        x0 = pairs[0][0]
        xs = [(d - x0).days for d, _ in pairs]
        ys = [v for _, v in pairs]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        den = sum((x - mx) ** 2 for x in xs)
        return None if den == 0 else \
            sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den

    # =================================================================
    # TRAINING
    # =================================================================

    def training_habits(self, upto: dt.date | None = None) -> dict:
        """What do they actually do, how often, and for how long?

        The habit model. An activity counts once it has been performed
        REPERTOIRE_MIN times - one logged yoga session does not make
        somebody a yoga practitioner, and recommending on that basis
        would be inventing a habit rather than observing one.
        """
        hi = upto or self.today
        rel = [s for s in self.sessions if s.date <= hi]
        counts = Counter(s.modality for s in rel)
        out = {}
        for mod, n in counts.items():
            if n < REPERTOIRE_MIN:
                continue
            days = sorted({s.date for s in rel if s.modality == mod})
            gaps = [(b - a).days for a, b in zip(days, days[1:])
                    if (b - a).days > 0]
            mins = [s.minutes for s in rel if s.modality == mod]
            out[mod] = {
                "label": MODALITY.get(mod, {}).get("label", mod),
                "sessions": n,
                "share": round(n / len(rel), 3),
                "typical_gap_days": statistics.median(gaps) if gaps else None,
                "typical_minutes": int(statistics.median(mins)),
                "last_done": (hi - days[-1]).days if days else None,
            }
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["sessions"]))

    def last_done(self, modality: str, upto: dt.date | None = None) -> int | None:
        hi = upto or self.today
        past = [s.date for s in self.sessions
                if s.modality == modality and s.date < hi]
        return (hi - max(past)).days if past else None

    def training_load(self, upto: dt.date | None = None) -> dict:
        """How hard has the last week been, against their usual month?"""
        hi = upto or self.today
        rated = [s for s in self.sessions if s.load is not None]
        acute = sum(s.load for s in rated
                    if hi - dt.timedelta(days=ACUTE_DAYS) < s.date <= hi)
        # A record younger than the chronic window is averaged over the weeks
        # it covers, not over four: dividing 11 days of load by four weeks
        # made the ratio about 3.6 where it was about 1.4.
        covered = CHRONIC_DAYS if self.first_day is None else \
            max(ACUTE_DAYS, min(CHRONIC_DAYS, (hi - self.first_day).days + 1))
        chronic = sum(s.load for s in rated
                      if hi - dt.timedelta(days=CHRONIC_DAYS) < s.date <= hi) \
            / (covered / ACUTE_DAYS)
        week = [s for s in self.sessions if hi - dt.timedelta(days=ACUTE_DAYS) < s.date <= hi]
        return {"units": {"acute_7d": "sRPE", "chronic_28d_per_week": "sRPE/week",
                          "acwr": "ratio", "sessions_7d": "sessions",
                          "rated_sessions_7d": "sessions",
                          "unrated_device_minutes_7d": "minutes"},
                "load_is": "the person's own effort rating x minutes; device-detected "
                           "sessions have no rating and carry no load",
                "rated_sessions_7d": sum(1 for s in week if s.load is not None),
                "unrated_device_minutes_7d": sum(s.minutes for s in week if s.load is None),
                "acute_7d": round(acute, 1),
                "chronic_28d_per_week": round(chronic, 1),
                "acwr": round(acute / chronic, 2) if chronic > 0 else None,
                "sessions_7d": sum(1 for s in self.sessions
                                   if hi - dt.timedelta(days=7) < s.date <= hi)}

    def training_rhythm(self, upto: dt.date | None = None) -> dict[str, int]:
        """Which days of the week do they train? The shape of their week."""
        hi = upto or self.today
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        c = Counter(s.date.weekday() for s in self.sessions
                    if hi is None or s.date <= hi)
        return {names[i]: c.get(i, 0) for i in range(7)}

    def hard_days(self, rpe_min: int = 7,
                  upto: dt.date | None = None) -> list[dt.date]:
        """Days they went hard, by their own rating, on or before `upto`."""
        hi = upto or self.today
        return sorted({s.date for s in self.sessions
                       if s.rpe is not None and s.rpe >= rpe_min
                       and (hi is None or s.date <= hi)})

    # =================================================================
    # SLEEP
    # =================================================================

    def sleep_baseline(self, upto: dt.date | None = None) -> dict | None:
        """How do they normally sleep? Their own numbers, not guidelines."""
        lo, hi = self._window(BASELINE_WINDOW, upto)
        rel = [n for n in self.nights if lo < n.date <= hi]
        if len(rel) < BASELINE_MIN:
            return None
        return {
            "nights": len(rel),
            "median_hours": round(statistics.median(
                n.asleep_min for n in rel) / 60, 2),
            "median_efficiency": statistics.median(n.efficiency for n in rel),
            "median_deep_min": statistics.median(n.deep for n in rel),
            "median_rem_min": statistics.median(n.rem for n in rel),
        }

    def sleep_debt(self, days: int = 7, upto: dt.date | None = None) -> dict | None:
        """Hours short of their own usual, over the last `days`."""
        base = self.sleep_baseline(upto)
        if base is None:
            return None
        lo, hi = self._window(days, upto)
        rel = [n for n in self.nights if lo < n.date <= hi]
        if not rel:
            return None
        actual = sum(n.asleep_min for n in rel) / 60
        expected = base["median_hours"] * len(rel)
        return {"nights": len(rel), "actual_hours": round(actual, 1),
                "expected_hours": round(expected, 1),
                "debt_hours": round(expected - actual, 1)}

    def sleep_agreement(self, upto: dt.date | None = None) -> dict | None:
        """Does what they say about their sleep match what the watch saw?

        Both sources are kept because they disagree often enough to
        matter: across the cohort the medians differ by only 0.13 h, but
        one night in six differs by more than an hour. That gap is
        itself a signal, and averaging it away would discard it.
        """
        hi = upto or self.today
        measured = {n.date: n.asleep_min / 60 for n in self.nights
                    if hi is None or n.date <= hi}
        pairs = [(r.sleep_hours_reported, measured[r.date])
                 for r in self.reports
                 if r.sleep_hours_reported is not None and r.date in measured
                 and (hi is None or r.date <= hi)]
        if len(pairs) < BASELINE_MIN:
            return None
        diffs = [a - b for a, b in pairs]
        return {"nights": len(pairs),
                "median_diff_hours": round(statistics.median(diffs), 2),
                "over_1h_share": round(
                    sum(1 for d in diffs if abs(d) > 1) / len(diffs), 3),
                "direction": "over-reports" if statistics.median(diffs) > 0
                             else "under-reports"}

    # =================================================================
    # RECOVERY
    # =================================================================

    def resting_hr_position(self, day: dt.date | None = None) -> dict | None:
        """Is today's resting heart rate unusual - for them?"""
        day = day or self.today
        if day not in self.resting_hr:
            return None
        lo = day - dt.timedelta(days=BASELINE_WINDOW)
        past = [v for d, v in self.resting_hr.items() if lo <= d < day]
        q = self._quantile(past, self.resting_hr[day])
        if q is None:
            return None
        return {"value": self.resting_hr[day], "quantile": round(q, 3),
                "baseline_median": round(statistics.median(past), 1),
                "baseline_days": len(past)}

    def readiness_trend(self, days: int = 14,
                        upto: dt.date | None = None) -> dict | None:
        lo, hi = self._window(days, upto)
        pts = [(r.date, r.readiness) for r in self.reports
               if lo < r.date <= hi and r.readiness is not None]
        if len(pts) < 5:
            return None
        slope = self._trend(pts)
        reports = [r for r in self.reports if lo < r.date <= hi and r.readiness is not None]
        return {"record_count": len(pts), "recorded_days": len({d for d, _ in pts}),
                "calendar_days": (hi - lo).days,
                "window": {"start": str(lo + dt.timedelta(days=1)), "end": str(hi)},
                "answers": wellness_summary(reports, "readiness"),
                "latest": wellness_words("readiness", pts[-1][1]),
                "direction": ("not_assessed" if slope is None else
                              "falling" if slope < -0.05 else
                              "rising" if slope > 0.05 else "flat")}

    # =================================================================
    # BODY
    # =================================================================

    def activity_adequacy(self, weeks: int = 4, upto: dt.date | None = None):
        """Are they active enough - against WHO, not against themselves?

        The only question in this file that consults an absolute
        standard, and it exists because every other one is structurally
        unable to ask it. A personal baseline answers "is today unusual
        for you". It cannot answer "is this enough", because someone who
        has always been inactive has a perfectly unremarkable baseline.
        Run against p16 the rest of this file reports nothing wrong and
        the coach recommends rest, while their median week holds 42
        minutes of moderate-equivalent activity against a floor of 150.

        WHO counts a vigorous minute as two moderate ones, so the
        comparison is made in moderate-equivalent minutes. Weeks with
        fewer than five scorable days are dropped rather than scaled up,
        because a part-measured week understates activity and the error
        would fall on the side of telling someone they are inactive.
        """
        if not self.active:
            return None
        hi = upto or self.today
        lo = hi - dt.timedelta(days=weeks * 7)
        buckets: dict[tuple, list[int]] = {}
        for day, rec in self.active.items():
            if lo <= day <= hi:
                mvpa = rec["moderate"] + gl.VIGOROUS_WEIGHT * rec["very"]
                buckets.setdefault(day.isocalendar()[:2], []).append(mvpa)
        full = [sum(v) for v in buckets.values() if len(v) >= 5]
        if not full:
            return None

        floor = gl.value("who_weekly_mvpa")
        upper = gl.value("who_weekly_mvpa_upper")
        median = statistics.median(full)
        # A count, as the name says: the share once read to the model as "0.25 weeks".
        met = sum(1 for w in full if w >= floor)
        return {"weeks_scored": len(full),
                "median_mvpa_minutes": round(median),
                "weeks_meeting_floor": met,
                "floor": floor, "upper": upper,
                "verdict": ("below" if median < floor else
                            "meets" if median < upper else "exceeds"),
                "guideline": gl.GUIDELINES["who_aerobic"].says,
                "source": gl.GUIDELINES["who_aerobic"].source}

    def sedentary_load(self, days: int = 28, upto: dt.date | None = None):
        """How much of a measured day is spent sitting still?

        Reported, never acted on. WHO's sedentary advice is directional
        ("limit it") with no threshold to branch on, so turning this
        into a constraint would mean inventing the number WHO declined
        to state.
        """
        if not self.active:
            return None
        lo, hi = self._window(days, upto)
        vals = [r["sedentary"] / 60 for d, r in self.active.items()
                if lo <= d <= hi]
        if len(vals) < BASELINE_MIN:
            return None
        return {"days": len(vals),
                "median_hours": round(statistics.median(vals), 1),
                "guideline": gl.GUIDELINES["who_sedentary"].says}

    def weight_trend(self, days: int = 28,
                     upto: dt.date | None = None) -> dict | None:
        """Which way is body weight going, and how fast?"""
        lo, hi = self._window(days, upto)
        pts = [(i.date, i.weight) for i in self.intake
               if lo < i.date <= hi and i.weight]
        if len(pts) < 7:
            return None
        slope = self._trend(pts)
        return {"days": len(pts), "latest_kg": pts[-1][1],
                "change_kg": round(pts[-1][1] - pts[0][1], 2),
                "kg_per_week": round(slope * 7, 3) if slope is not None else None,
                "direction": "down" if slope and slope < -0.005
                             else "up" if slope and slope > 0.005 else "stable"}

    # =================================================================
    # INTAKE
    # =================================================================

    def eating_pattern(self, days: int = 28,
                       upto: dt.date | None = None) -> dict | None:
        """How regular are their meals, and how often do they skip?"""
        lo, hi = self._window(days, upto)
        rel = [i for i in self.intake if lo < i.date <= hi]
        if len(rel) < 7:
            return None
        counts = Counter(m for i in rel for m in i.meals)
        return {"days": len(rel),
                "median_meals_per_day": statistics.median(
                    len(i.meals) for i in rel),
                "breakfast_share": round(counts["Breakfast"] / len(rel), 2),
                "evening_snack_share": round(counts["Evening"] / len(rel), 2),
                "days_under_three_meals": sum(1 for i in rel if len(i.meals) < 3)}

    def drinking_pattern(self, days: int = 28,
                         upto: dt.date | None = None) -> dict | None:
        """Fluid intake and how often alcohol appears."""
        lo, hi = self._window(days, upto)
        rel = [i for i in self.intake if lo < i.date <= hi]
        fluid = [i.fluid for i in rel if i.fluid]
        alc = [i.alcohol for i in rel if i.alcohol is not None]
        if not rel:
            return None
        return {"days": len(rel),
                "median_glasses": statistics.median(fluid) if fluid else None,
                "alcohol_days": sum(1 for a in alc if a),
                "alcohol_share": round(sum(1 for a in alc if a) / len(alc), 2)
                                 if alc else None}

    # =================================================================
    # EVENTS
    # =================================================================

    def injury_history(self, upto: dt.date | None = None) -> dict:
        """Injuries reported on or before `upto`.

        This used to hardcode `self.today` and ignore the day it was
        asked about, which made it a source of lookahead rather than a
        question: asked on day 30 of one record it reported an injury
        from day 126 and a `days_since_last` measured from the end of
        the file. `daily_capacity` caps intensity on that number, so a
        walk-forward evaluation would have been advised by injuries that
        had not happened yet.
        """
        hi = upto or self.today
        rel = [x for x in self.injuries if hi is None or x.date <= hi]
        by_joint = Counter(x.joint for x in rel if x.joint)
        last_by_joint = {x.joint: x.date for x in rel if x.joint}   # rel is date-ordered
        return {"count": len(rel),
                "dates": sorted({str(x.date) for x in rel}),
                "reports": [{"date": str(x.date), "location": x.location,
                             "joint": x.joint, "severity": x.severity}
                            for x in rel],
                "by_joint": dict(by_joint),
                "days_since_last": (hi - rel[-1].date).days if rel and hi
                                   else None,
                # The last report and a joint's last report are often not the
                # same one: naming them together once read "2 days ago,
                # affecting Knee" for a head report 12 days after the knee.
                "last_location": rel[-1].location.replace("_", " ") if rel else None,
                "days_since_by_joint": {j: (hi - d).days for j, d in sorted(last_by_joint.items())}
                                       if hi else {}}

    def recent_injured_joints(self, within_days: int = 14,
                              upto: dt.date | None = None) -> dict[str, str]:
        """Canonical joints named in injury reports within the window.

        Returns joint -> worst severity seen, so the planner can treat a
        recently reported knee the same way it treats one the person
        mentions in conversation. Only training joints appear; a chest or
        head/neck report is recorded elsewhere but caps no activity.
        """
        hi = upto or self.today
        if hi is None:
            return {}
        lo = hi - dt.timedelta(days=within_days)
        rank = {"major": 2, "minor": 1, "unknown": 1}
        worst: dict[str, str] = {}
        for x in self.injuries:
            if x.joint and lo <= x.date <= hi:
                if rank.get(x.severity, 1) >= rank.get(worst.get(x.joint, ""), 0):
                    worst[x.joint] = x.severity
        return worst

    # The cross-cutting views live in `observe.py`. The coach reads the
    # record through observe.read_record, one call for every domain: the
    # traces of 180 experiment turns showed it always read to the
    # simulation date, over 7 to 29 days, against the window just before,
    # through two to three calls whose layered output was about ten times
    # the values it carried. A related-days search went unused.


def demo() -> None:
    p = Profile("p05")
    print(f"=== {p.pid}: {p.first_day} to {p.today} ===")
    print(f"  {len(p.sessions)} sessions · {len(p.nights)} nights · "
          f"{len(p.reports)} self-reports · {len(p.resting_hr)} resting-HR days · "
          f"{len(p.intake)} intake days · {len(p.injuries)} injury reports\n")

    def show(q, v):
        print(f"  {q}\n    {v}")

    show("training_habits()", {k: f"{v['sessions']}x every ~{v['typical_gap_days']}d, "
                                  f"~{v['typical_minutes']}min"
                               for k, v in p.training_habits().items()})
    show("training_load()", p.training_load())
    show("training_rhythm()", p.training_rhythm())
    show("sleep_baseline()", p.sleep_baseline())
    show("sleep_debt()", p.sleep_debt())
    show("sleep_agreement()", p.sleep_agreement())
    show("readiness_trend()", p.readiness_trend())
    show("resting_hr_position()", p.resting_hr_position())
    show("weight_trend()", p.weight_trend())
    show("eating_pattern()", p.eating_pattern())
    show("drinking_pattern()", p.drinking_pattern())
    show("sedentary_load()", p.sedentary_load())
    show("injury_history()", {k: v for k, v in p.injury_history().items()
                              if k not in ("dates", "reports")})


if __name__ == "__main__":
    demo()
