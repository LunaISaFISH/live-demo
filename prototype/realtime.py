"""High-frequency Fitbit streams, indexed by day and read only on demand.

The PMData JSON files are single-line arrays. Loading one participant's whole
heart-rate stream creates more than a million Python objects, so this module
keeps the raw files as the source of truth and builds tiny byte-offset indexes.
Callers learn only three interfaces: one day summary, a period summary, or a
bounded minute-bucket window. Raw samples never enter an Agent prompt.

Run once after extracting the four raw files:

    ../.venv/bin/python realtime.py --index-all
"""

from __future__ import annotations

import datetime as dt
import json
import mmap
import pathlib
import statistics
from functools import lru_cache

import guidelines as gl

ROOT = pathlib.Path(__file__).parent.parent / "data" / "pmdata"
KINDS = ("heart_rate", "steps", "calories", "distance")
MAX_WINDOW_HOURS = 24
# Fitbit per-sample confidence below this is excluded everywhere; the
# value and its provenance live in the threshold registry.
HR_CONFIDENCE_MIN = int(gl.value("intraday_hr_confidence"))


def _source(pid: str, kind: str) -> pathlib.Path:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    path = ROOT / pid / "fitbit" / f"{kind}.json"
    if not path.exists():
        raise FileNotFoundError(f"missing raw stream: {path}")
    return path


def _index_path(source: pathlib.Path) -> pathlib.Path:
    return source.with_suffix(".day-index.json")


def build_index(source: pathlib.Path) -> dict:
    """Index each recorded day without parsing the multi-gigabyte archive."""
    needle = b'{"dateTime": "'
    with source.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        first_pos = mm.find(needle)
        last_pos = mm.rfind(needle)
        if first_pos < 0 or last_pos < 0:
            raise ValueError(f"no dated samples in {source}")
        first = dt.date.fromisoformat(mm[first_pos + len(needle):first_pos + len(needle) + 10].decode())
        last = dt.date.fromisoformat(mm[last_pos + len(needle):last_pos + len(needle) + 10].decode())
        starts, day, cursor = {}, first, first_pos
        while day <= last:
            pos = mm.find(needle + str(day).encode(), cursor)
            if pos >= 0:
                starts[str(day)] = pos
                cursor = pos
            day += dt.timedelta(days=1)
        ordered = sorted(starts.items())
        array_end = mm.rfind(b"]")
        ranges = {date: [start, ordered[i + 1][1] if i + 1 < len(ordered)
                                else array_end]
                  for i, (date, start) in enumerate(ordered)}

    index = {"source_size": source.stat().st_size,
             "first": str(first), "last": str(last), "days": ranges}
    target = _index_path(source)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")))
    tmp.replace(target)
    _load_index.cache_clear()
    return index


@lru_cache(maxsize=64)
def _load_index(source: pathlib.Path) -> dict:
    target = _index_path(source)
    if not target.exists():
        return build_index(source)
    index = json.loads(target.read_text())
    if index.get("source_size") != source.stat().st_size:
        return build_index(source)
    return index


@lru_cache(maxsize=8)
def _day_rows(pid: str, kind: str, day: dt.date) -> tuple[dict, ...]:
    source = _source(pid, kind)
    bounds = _load_index(source)["days"].get(str(day))
    if not bounds:
        return ()
    with source.open("rb") as fh:
        fh.seek(bounds[0])
        raw = fh.read(bounds[1] - bounds[0]).rstrip(b", \r\n")
    return tuple(json.loads(b"[" + raw + b"]"))


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * q)]


@lru_cache(maxsize=1024)
def day_summary(pid: str, day: dt.date) -> dict:
    """Compact evidence for questions and planning, derived from raw samples."""
    heart = [r for r in _day_rows(pid, "heart_rate", day)
             if int(r["value"].get("confidence", 0)) >= HR_CONFIDENCE_MIN]
    bpms = [int(r["value"]["bpm"]) for r in heart]
    heart_minutes = {r["dateTime"][:16] for r in heart}
    steps = [int(float(r["value"])) for r in _day_rows(pid, "steps", day)]
    calories = [float(r["value"]) for r in _day_rows(pid, "calories", day)]
    distance = [float(r["value"]) for r in _day_rows(pid, "distance", day)]
    return {
        "date": str(day),
        "heart": {"samples": len(bpms), "covered_minutes": len(heart_minutes),
                  "mean_bpm": round(statistics.mean(bpms), 1) if bpms else None,
                  "min_bpm": min(bpms) if bpms else None,
                  "max_bpm": max(bpms) if bpms else None,
                  "p95_bpm": _percentile(bpms, .95),
                  "confidence_filter": f">={HR_CONFIDENCE_MIN}"},
        "movement": {"steps": sum(steps) if steps else None,
                     "active_minutes": sum(1 for x in steps if x > 0),
                     "distance_km": round(sum(distance) / 100_000, 3)
                                    if distance else None,
                     "calories": round(sum(calories), 1) if calories else None},
    }


def period_summary(pid: str, start: dt.date, end: dt.date) -> dict:
    if end < start or (end - start).days > 365:
        raise ValueError("raw-stream period must be 1 to 365 days")
    days, day = [], start
    while day <= end:
        days.append(day_summary(pid, day))
        day += dt.timedelta(days=1)
    with_hr = [d for d in days if d["heart"]["samples"]]
    with_steps = [d for d in days if d["movement"]["steps"] is not None]
    return {
        "person": pid, "window": {"start": str(start), "end": str(end)},
        "days": days,
        "summary": {
            "days_requested": len(days), "heart_days": len(with_hr),
            "step_days": len(with_steps),
            "median_mean_hr_bpm": (round(statistics.median(
                d["heart"]["mean_bpm"] for d in with_hr), 1) if with_hr else None),
            "median_p95_hr_bpm": (round(statistics.median(
                d["heart"]["p95_bpm"] for d in with_hr), 1) if with_hr else None),
            "median_heart_covered_minutes": (round(statistics.median(
                d["heart"]["covered_minutes"] for d in with_hr)) if with_hr else None),
            "median_daily_steps": (round(statistics.median(
                d["movement"]["steps"] for d in with_steps)) if with_steps else None),
            "total_distance_km": round(sum(
                d["movement"]["distance_km"] or 0 for d in days), 2),
            "total_calories": round(sum(
                d["movement"]["calories"] or 0 for d in days), 1),
        },
        "limitations": [
            "consumer wearable measurements are contextual evidence, not diagnosis",
            "calories and distance are device estimates",
            "missing samples are not treated as zero",
        ],
    }


# Fitbit's per-minute calorie estimate occasionally reads at running level
# while no step is recorded and the heart rate is low (PMData p05 shows ~17
# kcal/min at 0 steps and ~80 bpm). Flag it instead of letting it read as
# real effort. Simplification: fixed cutoffs; a per-person BMR model is the upgrade.
IMPLAUSIBLE_KCAL_PER_MIN = 8.0
IMPLAUSIBLE_BELOW_BPM = 100


def _implausible_calories(point: dict, bucket_minutes: int) -> bool:
    kcal, steps, bpm = point["calories"], point["steps"], point["heart_bpm"]
    return (kcal is not None and kcal / bucket_minutes > IMPLAUSIBLE_KCAL_PER_MIN
            and not steps and (bpm is None or bpm < IMPLAUSIBLE_BELOW_BPM))


@lru_cache(maxsize=64)
def sensor_window(pid: str, start: dt.datetime, end: dt.datetime,
                  bucket_minutes: int = 1) -> dict:
    """Minute-bucket raw streams for a bounded intraday investigation."""
    if end <= start or end - start > dt.timedelta(hours=MAX_WINDOW_HOURS):
        raise ValueError(f"sensor window must be over 0 to {MAX_WINDOW_HOURS} hours")
    bucket_minutes = max(1, min(int(bucket_minutes), 15))
    buckets: dict[str, dict] = {}

    def key(raw: str) -> str:
        moment = dt.datetime.fromisoformat(raw)
        moment = moment.replace(minute=(moment.minute // bucket_minutes) * bucket_minutes,
                                second=0, microsecond=0)
        return moment.isoformat(sep=" ")

    day = start.date()
    while day <= end.date():
        for kind in KINDS:
            for row in _day_rows(pid, kind, day):
                moment = dt.datetime.fromisoformat(row["dateTime"])
                if not start <= moment <= end:
                    continue
                b = buckets.setdefault(key(row["dateTime"]), {
                    "heart": [], "confidence": [], "steps": [],
                    "calories": [], "distance": []})
                if kind == "heart_rate":
                    confidence = int(row["value"].get("confidence", 0))
                    if confidence >= HR_CONFIDENCE_MIN:
                        b["heart"].append(int(row["value"]["bpm"]))
                        b["confidence"].append(confidence)
                else:
                    b[kind].append(float(row["value"]))
        day += dt.timedelta(days=1)

    points = []
    for at, b in sorted(buckets.items()):
        heart = b["heart"]
        points.append({
            "at": at,
            "heart_bpm": round(statistics.mean(heart), 1) if heart else None,
            "heart_min": min(heart) if heart else None,
            "heart_max": max(heart) if heart else None,
            "heart_samples": len(heart),
            "mean_confidence": (round(statistics.mean(b["confidence"]), 2)
                                if b["confidence"] else None),
            "steps": round(sum(b["steps"])) if b["steps"] else None,
            "calories": round(sum(b["calories"]), 3) if b["calories"] else None,
            "distance_km": (round(sum(b["distance"]) / 100_000, 6)
                            if b["distance"] else None),
        })
    implausible = 0
    for point in points:
        if _implausible_calories(point, bucket_minutes):
            point["calories_implausible"] = True
            implausible += 1
    heart = [p["heart_bpm"] for p in points if p["heart_bpm"] is not None]
    limitations = [f"heart-rate samples with confidence below "
                   f"{HR_CONFIDENCE_MIN} were excluded",
                   "zero steps does not prove the person was physiologically at rest"]
    if implausible:
        limitations.append(
            f"{implausible} bucket(s) report over {IMPLAUSIBLE_KCAL_PER_MIN:g} "
            f"kcal/min with no steps and heart rate under {IMPLAUSIBLE_BELOW_BPM} "
            "bpm; treat those calorie estimates as a likely device artefact, "
            "not as evidence of effort")
    return {"person": pid, "window": {"start": start.isoformat(sep=" "),
                                        "end": end.isoformat(sep=" ")},
            "bucket_minutes": bucket_minutes,
            "bucket_at": f"each point's `at` is the start of its bucket, which covers "
                         f"`at` up to {bucket_minutes} minutes later",
            "points": points,
            "summary": {"buckets": len(points), "heart_buckets": len(heart),
                        "mean_hr_bpm": (round(statistics.mean(heart), 1)
                                        if heart else None),
                        "max_hr_bpm": max(heart) if heart else None,
                        "steps": sum(p["steps"] or 0 for p in points),
                        "distance_km": round(sum(
                            p["distance_km"] or 0 for p in points), 3),
                        "calories": round(sum(
                            p["calories"] or 0 for p in points), 1),
                        "implausible_calorie_buckets": implausible},
            "provenance": [f"fitbit/{kind}.json" for kind in KINDS],
            "limitations": limitations}


def day_window(pid: str, day: dt.date, bucket_minutes: int = 5) -> dict:
    return sensor_window(pid, dt.datetime.combine(day, dt.time.min),
                         dt.datetime.combine(day, dt.time.max), bucket_minutes)


def build_all() -> None:
    for participant in sorted(p.name for p in ROOT.glob("p??")):
        for kind in KINDS:
            source = _source(participant, kind)
            index = build_index(source)
            print(f"{participant} {kind:<10} {len(index['days']):>3} days")


if __name__ == "__main__":
    import sys
    if "--index-all" in sys.argv:
        build_all()
    else:
        sample = day_summary("p16", dt.date(2020, 3, 1))
        assert sample["heart"]["samples"] > 1000
        assert sample["movement"]["calories"] is not None
        print(json.dumps(sample, indent=2))
