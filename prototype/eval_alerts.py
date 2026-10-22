"""Were the coach's Alerts right? An evaluation of the recorded replay.

PMData has no clinical labels, so nothing says which day a person should
have been alerted. This measures what can be measured:

    pack     For every recorded review: (1) grounding - each number the coach
             stated in its observations, note and Alert reason is looked up in
             the outputs of the very tool calls it made, regenerated from the
             record (and in the facts the monitor handed it); (2) a blinded
             case for independent adjudicators - the same facts, a standard
             evidence pack read by code (14-day record, and the sensor hour
             before an intraday event), and none of the coach's own findings,
             verdict or decision.
    score    With the adjudicators' labels: precision, recall, agreement.

    ../.venv/bin/python eval_alerts.py pack
    ../.venv/bin/python eval_alerts.py score

Output: ../evidence/monitoring/evaluation-r1/
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import re
import sys
import textwrap

import observe
import realtime
from profile import Profile

ROOT = pathlib.Path(__file__).resolve().parent.parent / "evidence"
REPLAY = pathlib.Path(os.environ.get("ALERT_REPLAY_OUT") or ROOT / "monitoring" / "replay-r1")
OUT = pathlib.Path(os.environ.get("ALERT_EVAL_OUT") or ROOT / "monitoring" / "evaluation-r1")
PIDS = ("p05", "p12", "p16")
FACTS_HEAD = "What the monitor knows about it, for you to weigh, not thresholds:\n"

# A number as written: 1,745 / 33.0 / -0.39. Dates and clock times are
# removed first; they are checked against the window, not looked up.
NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
DATE_OR_TIME = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b(?:Nov|Dec|Jan|Feb|Mar)\w* \d{1,2}\b")


def reviews(pid: str) -> list[dict]:
    rows = [json.loads(x) for x in (REPLAY / f"{pid}.jsonl").read_text().splitlines()]
    audits = [json.loads(x) for x in (REPLAY / "state" / pid / "audit" / f"{pid}.jsonl").read_text().splitlines()]
    out = []
    for i, (row, entry) in enumerate(zip(rows, audits, strict=True)):
        if not entry["input"].startswith(f"[monitoring event, {row['at']}]"):
            raise ValueError(f"replay and audit disagree at {row['at']}")
        sealed = re.search(r"Daily semantic history is sealed at (\S+?)\.", entry["input"]).group(1)
        out.append({"id": f"{pid}-{i + 1:02d}", "pid": pid, **row, "sources": entry["source"],
                    "sealed": sealed,
                    "facts": entry["input"].split(FACTS_HEAD, 1)[1].split("\n\n", 1)[0]})
    return out


def tool_outputs(r: dict, full: Profile) -> list:
    """The coach's own tool calls, run again on the record as it was sealed."""
    sealed = full.snapshot(dt.date.fromisoformat(r["sealed"]))
    out = []
    for s in r["sources"]:
        if m := re.match(r"read_record (\d+)d as of (\S+)", s):
            out.append(observe.read_record(sealed, int(m[1]), dt.date.fromisoformat(m[2])))
        elif m := re.match(r"inspect_sensor_window (.+) to (.+)$", s):
            a, b = dt.datetime.fromisoformat(m[1]), dt.datetime.fromisoformat(m[2])
            # The bucket size is not in the audit; every size the tool allows is a source.
            out += [realtime.sensor_window(r["pid"], a, b, k) for k in (1, 2, 5, 10, 15)]
            out.append([{"start": x.start, "minutes": x.minutes, "avg_hr": x.avg_hr}
                        for x in full.sessions if x.start and a - dt.timedelta(hours=2) <= x.start <= b])
    return out


CITATION = re.compile(r"\[[0-9a-f]{8}-[\w-]+\]")


def numbers(text: str) -> list[float]:
    text = DATE_OR_TIME.sub(" ", CITATION.sub(" ", text))
    return [float(n.replace(",", "")) for n in NUMBER.findall(text)]


def grounded(value: float, pool: set[float]) -> bool:
    """Found as written, or as a percentile of a stated quantile; a sleep
    surplus is stored as a negative debt, so the sign is not compared."""
    return any(abs(abs(value) - abs(q)) <= max(0.051, abs(q) * 0.005)
               for p in pool for q in (p, p * 100))


def evidence_pack(r: dict, full: Profile) -> dict:
    """What an adjudicator reads: the code's own reading, not the coach's."""
    day = dt.date.fromisoformat(r["sealed"])
    rec = observe.read_record(full.snapshot(day), 14, day)
    pack = {k: rec[k] for k in ("findings", "state_at_window_end", "data_quality", "limitations")}
    if r["kind"] == "intraday_hr":
        at = dt.datetime.fromisoformat(r["at"])
        w = realtime.sensor_window(r["pid"], at - dt.timedelta(minutes=90), at, 10)
        pack["sensor_90_minutes_before"] = [
            {k: p[k] for k in ("at", "heart_bpm", "steps", "distance_km") if k in p} for p in w["points"]]
        pack["logged_exercise_that_day_up_to_event"] = [
            {"start": str(x.start), "activity": x.modality, "minutes": x.minutes}
            for x in full.sessions if x.start and x.start.date() == at.date() and x.start <= at]
    return pack


def render(c: dict) -> str:
    def flat(x, path=""):
        if isinstance(x, dict):
            for k, v in x.items():
                yield from flat(v, f"{path}.{k}" if path else k)
        elif isinstance(x, list) and x and all(isinstance(i, (dict, list)) for i in x):
            for n, v in enumerate(x):
                yield from flat(v, f"{path}[{n}]")
        else:
            yield path, x

    def wrap(text, indent="  "):
        return "\n".join(textwrap.wrap(str(text), 150, initial_indent=indent,
                                       subsequent_indent=indent + "  ")) or indent
    e = c["evidence"]
    out = [f"CASE {c['id']}  participant {c['participant']}  detector {c['kind']}  why reviewed: {c['due']}  at {c['at']}",
           "", "SIGNAL", wrap(c["signal"]), "",
           "MONITOR FACTS (what the monitor knew, incl. earlier reviews and Alerts already sent)",
           *(wrap(line) for line in c["monitor_facts"].splitlines()),
           "", "EVIDENCE: the record read by code over the 14 days before (sealed, no later data)", "Findings:",
           *(wrap(f"- {f}") for f in e["findings"]),
           "State at the window end:", *(wrap(f"{k}: {v}") for k, v in flat(e["state_at_window_end"])),
           "Data quality:", *(wrap(f"{k}: {v}") for k, v in flat(e["data_quality"])),
           "Limitations:", *(wrap(f"- {x}") for x in e["limitations"])]
    if "sensor_90_minutes_before" in e:
        out += ["Sensor, 90 minutes before the event (10-minute buckets: time, heart bpm, steps, km):",
                *(f"  {p.get('at', '')[11:16]}  {p.get('heart_bpm')}  {p.get('steps')}  {p.get('distance_km')}"
                  for p in e["sensor_90_minutes_before"]),
                "Exercise logged that day up to the event:",
                *([wrap(json.dumps(x)) for x in e["logged_exercise_that_day_up_to_event"]] or ["  none"])]
    return "\n".join(out) + "\n"


def pack() -> None:
    OUT.mkdir(exist_ok=True)
    cases, ground, checks = [], [], []
    for pid in PIDS:
        full = Profile(pid)
        earlier: set[float] = set()      # one conversation: earlier reviews' outputs are in it
        for r in reviews(pid):
            own = {n for o in tool_outputs(r, full) for n in numbers(json.dumps(o, default=str))}
            pool = own | set(numbers(r["facts"])) | set(numbers(r["summary"]))
            rv = r["review"]
            claims = [*rv["observations"], rv["note"], r["notification"]["reason"]]
            missing = sorted({n for c in claims for n in numbers(c) if not grounded(n, pool)})
            ground.append({"id": r["id"], "alert": r["notification"]["notify"], "kind": r["kind"],
                           "stated": sum(len(numbers(c)) for c in claims),
                           "not_found": missing,
                           "only_in_earlier_reviews": [n for n in missing if grounded(n, earlier)],
                           "claims": claims})
            earlier |= own
            if r["notification"]["notify"]:       # for the fact-checker: an Alert beside its own tool outputs
                calls = []
                for src in r["sources"]:
                    if src.startswith(("read_record", "inspect_sensor_window")):
                        o = tool_outputs({**r, "sources": [src]}, full)
                        calls.append({"call": src, "output": o[0] if src.startswith("read_record")
                                      else {"5_minute_buckets": o[2], "logged_sessions_nearby": o[-1]}})
                checks.append({"id": r["id"], "kind": r["kind"], "at": r["at"], "monitor_facts": r["facts"],
                               "statements": {"observations": rv["observations"], "verdict": rv["verdict"],
                                              "note": rv["note"], "alert_reason": r["notification"]["reason"],
                                              "message_to_person": r["reply"]},
                               "tool_calls": calls})
            cases.append({"id": r["id"], "participant": pid, "kind": r["kind"], "due": r["due"],
                          "at": r["at"], "signal": r["summary"], "monitor_facts": r["facts"],
                          "evidence": evidence_pack(r, full)})
    (OUT / "cases.jsonl").write_text("".join(json.dumps(c, default=str, ensure_ascii=False) + "\n" for c in cases))
    # What the adjudicators read: one short-lined text file per case, so no
    # reader truncates a long line; ids in batches of 13.
    (OUT / "cases").mkdir(exist_ok=True)
    for c in cases:
        (OUT / "cases" / f"{c['id']}.txt").write_text(render(json.loads(json.dumps(c, default=str))))
    (OUT / "batches").mkdir(exist_ok=True)
    for n in range(0, len(cases), 13):
        (OUT / "batches" / f"ids-{n // 13 + 1:02d}").write_text("".join(c["id"] + "\n" for c in cases[n:n + 13]))
    (OUT / "factcheck-alerts.jsonl").write_text(
        "".join(json.dumps(x, default=str, ensure_ascii=False) + "\n" for x in checks))
    (OUT / "grounding.jsonl").write_text("".join(json.dumps(g, ensure_ascii=False) + "\n" for g in ground))
    stated = sum(g["stated"] for g in ground)
    nf = sum(len(g["not_found"]) for g in ground)
    print(f"{len(cases)} cases; {stated} numbers stated; {nf} (distinct per review) not in that review's own "
          f"tool outputs or facts, in {sum(1 for g in ground if g['not_found'])} reviews - each checked by hand "
          f"in RESULTS.md; {sum(len(g['only_in_earlier_reviews']) for g in ground)} of them are in an earlier "
          f"review's outputs, in the same conversation")


def score() -> None:
    coach = {r["id"]: r for pid in PIDS for r in reviews(pid)}
    labels = {}
    for f in sorted(OUT.glob("labels-*.jsonl")):
        labels[f.stem.removeprefix("labels-")] = {
            x["id"]: x for x in map(json.loads, f.read_text().splitlines())}

    def table(truth: dict[str, bool], ids) -> dict:
        tp = sum(coach[i]["notification"]["notify"] and truth[i] for i in ids)
        fp = sum(coach[i]["notification"]["notify"] and not truth[i] for i in ids)
        fn = sum(not coach[i]["notification"]["notify"] and truth[i] for i in ids)
        tn = len(ids) - tp - fp - fn
        p = tp / (tp + fp) if tp + fp else float("nan")
        r = tp / (tp + fn) if tp + fn else float("nan")
        return {"n": len(ids), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r,
                "f1": 2 * p * r / (p + r) if p + r else float("nan"), "accuracy": (tp + tn) / len(ids)}

    def kappa(a: dict[str, bool], b: dict[str, bool]) -> float:
        ids = list(a)
        po = sum(a[i] == b[i] for i in ids) / len(ids)
        pa, pb = sum(a.values()) / len(ids), sum(b[i] for i in ids) / len(ids)
        pe = pa * pb + (1 - pa) * (1 - pb)
        return (po - pe) / (1 - pe) if pe < 1 else 1.0

    fmt = lambda t: (f"n={t['n']:>3}  TP {t['tp']:>2} FP {t['fp']:>2} FN {t['fn']:>2} TN {t['tn']:>2}  "
                     f"precision {t['precision']:.2f}  recall {t['recall']:.2f}  F1 {t['f1']:.2f}  accuracy {t['accuracy']:.2f}")
    said = {i: r["notification"]["notify"] for i, r in coach.items()}
    for name, lab in labels.items():
        truth = {i: lab[i]["should_alert"] for i in coach}
        print(f"\ncoach vs {name}: {fmt(table(truth, list(coach)))}  kappa {kappa(said, truth):.2f}")
    if len(labels) >= 2:
        names = list(labels)
        a, b = ({i: labels[n][i]["should_alert"] for i in coach} for n in names[:2])
        print(f"\n{names[0]} vs {names[1]}: agree on {sum(a[i] == b[i] for i in coach)} of {len(coach)}, kappa {kappa(a, b):.2f}")
        # Where the two agree is the reference; where they do not, the case is
        # uncertain and left out rather than settled by someone who has seen
        # the coach's decision.
        agreed = [i for i in coach if a[i] == b[i]]
        print(f"\ncoach vs the {len(agreed)} cases both raters agree on: {fmt(table(a, agreed))}"
              f"  kappa {kappa({i: said[i] for i in agreed}, {i: a[i] for i in agreed}):.2f}")
        by = collections.defaultdict(list)
        for i in agreed:
            r = coach[i]
            by[("event" if r["kind"] == "intraday_hr" else "condition", r["due"])].append(i)
            by[(r["pid"],)].append(i)
        for k in sorted(by):
            print(f"  {' '.join(k):<20} {fmt(table(a, by[k]))}")
        # The audited reference: the label audit (../label-audit) flips an
        # agreed label it found wrong and settles a split it could resolve;
        # what it called ambiguous stays out.
        audited = {i: a[i] for i in agreed}
        found = [x for f in sorted((OUT.parent / "label-audit").glob("results-*.jsonl"))
                 for x in map(json.loads, f.read_text().splitlines()) if x["packet"].split("/")[0] == OUT.name]
        for x in found:
            i = x["packet"].split("/")[1]
            if x["verdict"] == "ambiguous":
                audited.pop(i, None)
            elif x["final_should_alert"] is not None and x["verdict"] in ("label wrong", "resolved", "label stands"):
                audited[i] = bool(x["final_should_alert"])
        if found:
            ids = list(audited)
            print(f"\ncoach vs the audited reference ({len(found)} packets audited; "
                  f"{sum(x['verdict'] == 'label wrong' for x in found)} agreed labels overturned, "
                  f"{sum(x['verdict'] == 'resolved' for x in found)} splits resolved, "
                  f"{sum(x['verdict'] == 'ambiguous' for x in found)} ambiguous): {fmt(table(audited, ids))}")
            byk = collections.defaultdict(list)
            for i in ids:
                byk["event" if coach[i]["kind"] == "intraday_hr" else "condition"].append(i)
            for k in sorted(byk):
                print(f"  {k:<20} {fmt(table(audited, byk[k]))}")
        split = [i for i in coach if a[i] != b[i]]
        print(f"\nuncertain (raters split) {len(split)}: coach alerted on {sum(said[i] for i in split)}")
        for i in agreed:
            if said[i] != a[i]:
                print(f"  {'FP' if said[i] else 'FN'} {i} {coach[i]['kind']}/{coach[i]['due']}: "
                      f"coach: {coach[i]['notification']['reason'][:110]} | raters: {labels[names[0]][i]['reason']}")

def looks() -> None:
    """How far back the coach read before each heart-rate event, and whether
    activity it never saw preceded the rise. Needs no labels."""
    rows = []
    for pid in PIDS:
        f = REPLAY / f"{pid}.jsonl"
        if not f.exists():
            continue
        full = Profile(pid)
        for r in reviews(pid):
            if r["kind"] != "intraday_hr":
                continue
            onset = dt.datetime.strptime(f"{r['at'][:10]} {re.search(r'from (\d\d:\d\d)', r['summary'])[1]}",
                                         "%Y-%m-%d %H:%M")
            starts = [dt.datetime.fromisoformat(m[1]) for s in r["sources"]
                      if (m := re.match(r"inspect_sensor_window (.+) to (.+)$", s))]
            back = (onset - min(starts)).total_seconds() / 60 if starts else None
            w = realtime.sensor_window(pid, onset - dt.timedelta(minutes=90), onset, 10)
            active = [dt.datetime.fromisoformat(p["at"]) for p in w["points"] if (p["steps"] or 0) >= 300]
            active += [x.start + dt.timedelta(minutes=x.minutes) for x in full.sessions if x.start and
                       onset - dt.timedelta(minutes=90) <= x.start + dt.timedelta(minutes=x.minutes) <= onset]
            rows.append({"id": r["id"], "back": back, "calls": len(starts), "activity": bool(active),
                         "unseen": bool(active) and starts and all(a < min(starts) for a in active),
                         "verdict": r["review"]["verdict"], "alert": r["notification"]["notify"]})
    backs = sorted(x["back"] for x in rows if x["back"] is not None)
    print(f"{REPLAY.name}: {len(rows)} heart-rate events; looked back median {backs[len(backs) // 2]:.0f} min "
          f"(range {backs[0]:.0f}-{backs[-1]:.0f}); more than one sensor read in "
          f"{sum(x['calls'] > 1 for x in rows)}; no sensor read in {sum(x['back'] is None for x in rows)}")
    print("  minutes looked back:", dict(sorted(collections.Counter(int(b // 30 * 30) for b in backs).items())))
    for name, group in (("activity before the rise, all of it unseen", [x for x in rows if x["unseen"]]),
                        ("activity before the rise, seen", [x for x in rows if x["activity"] and not x["unseen"]]),
                        ("no activity in the 90 minutes before", [x for x in rows if not x["activity"]])):
        print(f"  {name}: {len(group)}; verdicts {dict(collections.Counter(x['verdict'] for x in group))}; "
              f"Alerts {sum(x['alert'] for x in group)}")


def audit() -> None:
    """Packets for re-checking the reference where it is in doubt: every case
    the coach decided against the raters' agreed label, and every case the
    raters split on. Each packet adds, to what the raters saw, both raters'
    reasons, the coach's full reasoning and the outputs of the tools the
    coach called, so an auditor can check each side's facts against the data."""
    labels = {k: {x["id"]: x for x in map(json.loads, (OUT / f"labels-rater{k}.jsonl").read_text().splitlines())}
              for k in "AB"}
    (OUT / "audit").mkdir(exist_ok=True)
    picked = []
    for pid in PIDS:
        full = Profile(pid)
        for r in reviews(pid):
            a, b = labels["A"][r["id"]], labels["B"][r["id"]]
            said = r["notification"]["notify"]
            if a["should_alert"] == b["should_alert"] == said:
                continue
            why = "raters split" if a["should_alert"] != b["should_alert"] else "coach against agreed label"
            rv = r["review"]
            out = [f"AUDIT {r['id']}: {why}", "",
                   "=== WHAT THE RATERS SAW ===", (OUT / "cases" / f"{r['id']}.txt").read_text(),
                   "=== RATER LABELS ===",
                   *(f"rater {k}: should_alert={labels[k][r['id']]['should_alert']} ({labels[k][r['id']]['confidence']}): "
                     f"{labels[k][r['id']]['reason']}" for k in "AB"), "",
                   "=== THE COACH'S REVIEW ===",
                   f"decision: {'ALERT' if said else 'no Alert'}; verdict: {rv['verdict']}; recheck_in_days: {rv.get('recheck_in_days')}",
                   f"alert_reason: {r['notification']['reason']}", f"note: {rv['note']}", "observations:",
                   *(f"  - {o}" for o in rv["observations"]), "limitations:", *(f"  - {o}" for o in rv["limitations"]),
                   f"message to the person: {CITATION.sub('', r['reply'])}", f"tools it called: {r['sources']}", "",
                   "=== OUTPUTS OF THE COACH'S TOOL CALLS (regenerated from the record) ==="]
            for src in r["sources"]:
                if src.startswith("read_record"):
                    o = tool_outputs({**r, "sources": [src]}, full)[0]
                    out += [f"--- {src}: findings, state, data quality ---",
                            *(f"  - {f}" for f in o["findings"]),
                            *(f"  {k}: {v}" for k, v in _flat(o["state_at_window_end"])),
                            *(f"  {k}: {v}" for k, v in _flat(o["domains"]))]
                elif src.startswith("inspect_sensor_window"):
                    o = tool_outputs({**r, "sources": [src]}, full)
                    out += [f"--- {src}: 5-minute buckets (time, heart bpm, samples, steps, km) ---",
                            *(f"  {p['at'][11:16]}  {p.get('heart_bpm')}  {p.get('heart_samples')}  {p.get('steps')}  {p.get('distance_km')}"
                              for p in o[2]["points"]),
                            "logged sessions starting up to 2 hours before the window:",
                            *(f"  {x}" for x in o[-1])]
            (OUT / "audit" / f"{r['id']}.txt").write_text("\n".join(textwrap.fill(line, 160, subsequent_indent="    ")
                                                                  if len(line) > 160 else line for line in out) + "\n")
            picked.append((r["id"], why))
    print(f"{len(picked)} audit packets: {sum(w == 'raters split' for _, w in picked)} split, "
          f"{sum(w != 'raters split' for _, w in picked)} coach against agreed label")


def replicas() -> None:
    """Runs of one version side by side: how far each read before heart-rate
    events, and how often they made the same Alert decision on the same event.
    The run folders are the arguments after `replicas`."""
    global REPLAY
    runs = {}
    for name in sys.argv[2:]:
        REPLAY = ROOT / name
        runs[name] = {(x["kind"], x["at"]): x for pid in PIDS if (REPLAY / f"{pid}.jsonl").exists()
                      for x in reviews(pid)}

    def back(x):
        onset = dt.datetime.strptime(f"{x['at'][:10]} {re.search(r'from (\d\d:\d\d)', x['summary'])[1]}",
                                     "%Y-%m-%d %H:%M")
        starts = [dt.datetime.fromisoformat(m[1]) for s in x["sources"]
                  if (m := re.match(r"inspect_sensor_window (.+) to (.+)$", s))]
        return (onset - min(starts)).total_seconds() / 60 if starts else 0.0
    for name, rs in runs.items():
        b = sorted(back(x) for k, x in rs.items() if k[0] == "intraday_hr")
        firsts = {pid: round(back(next(x for x in reviews_of(rs, pid) if x["kind"] == "intraday_hr")))
                  for pid in ("p05", "p16") if any(x["kind"] == "intraday_hr" for x in reviews_of(rs, pid))}
        print(f"{name:<28} Alerts {sum(x['notification']['notify'] for x in rs.values()):>2}; minutes read before an "
              f"event, quartiles {b[len(b) // 4]:.0f} / {b[len(b) // 2]:.0f} / {b[3 * len(b) // 4]:.0f}; first event {firsts}")
    ev = [k for k in next(iter(runs.values())) if k[0] == "intraday_hr" and all(k in r for r in runs.values())]
    said = [[r[k]["notification"]["notify"] for r in runs.values()] for k in ev]
    print(f"{len(ev)} heart-rate events in every run: same decision in all {sum(len(set(d)) == 1 for d in said)}; "
          f"alerted in at least one {sum(any(d) for d in said)}, in all {sum(all(d) for d in said)}")


def reviews_of(rs: dict, pid: str) -> list:
    return sorted((x for x in rs.values() if x["pid"] == pid), key=lambda x: x["at"])


def _flat(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _flat(v, f"{path}.{k}" if path else k)
    elif isinstance(x, list) and x and all(isinstance(i, (dict, list)) for i in x):
        for n, v in enumerate(x):
            yield from _flat(v, f"{path}[{n}]")
    else:
        yield path, x


if __name__ == "__main__":
    {"pack": pack, "score": score, "looks": looks, "audit": audit, "replicas": replicas}[sys.argv[1]]()
