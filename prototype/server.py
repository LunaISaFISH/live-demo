"""
The web front end's server. Standard library only.

The brief asks a prototype to make seven things observable, and most of
them are things a terminal transcript flattens into scrollback:

    user input          a way to start the workflow
    agent workflow      planning, step transitions, state changes, tool calls
    grounding           retrieval from a record
    human checkpoint    save the agent's plan draft as the plan, or discard it
    safety response     a stop, warning, escalation or refusal
    audit log           input, action, source, timestamp, output, decision
    evaluation          test cases, results, error examples

The point of a page rather than a console is that *the workflow happens
in front of you*. A turn takes eight to thirty seconds and makes five to
a dozen tool calls; printed afterwards that is a wall of text, and
watching it arrive is the difference between being told the agent
retrieved something and seeing which questions it chose to ask.

So this streams. Every gate, tool call and decision goes out
over SSE the moment it happens.

No framework: `http.server` and `json`. The agent is the interesting
part; the transport should not add a dependency to look serious.

Run:  ../.venv/bin/python server.py     then open http://localhost:8765
"""

from __future__ import annotations

import functools
import itertools
import json
import os
import pathlib
import queue
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asyncio
import datetime as dt


def _load_dotenv(path: pathlib.Path) -> None:
    """KEY=VALUE lines from the project's .env; the shell environment wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Before importing the agent, which reads FITCOACH_MODEL at import time.
_load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

import agent as coach
import audit
import monitor
import observe
import knowledge
import skills
import watch
import workspace
from profile import ROOT, Profile

# One event loop for every conversation. A Codex conversation keeps a live
# app-server subprocess per session, and an object with a life span needs a loop
# with one - the previous design ran `anyio.run` per turn on a worker
# thread, which is exactly why every turn was an amnesiac restart.
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True).start()

HERE = pathlib.Path(__file__).parent
WEB = HERE / "web"
PORT = int(os.environ.get("FITCOACH_PORT", "8765"))

# One live session per participant, so a conversation and its memory
# survive across requests the way they would for a real user.
SESSIONS: dict[str, coach.Session] = {}
LOCK = threading.RLock()        # re-entrant: routes hold it around session()

# A turn in flight publishes here; the browser reads it over SSE. One
# queue per turn rather than one per session, so a reconnecting client
# cannot pick up half of somebody else's stream.
STREAMS: dict[str, queue.Queue] = {}

# Every participant id the server accepts. Ids are joined onto file paths
# (memory, plans, audit, raw data), so anything else is refused up front.
PIDS = frozenset(d.name for d in ROOT.glob("p??") if d.is_dir())


class BadRequest(ValueError):
    pass


def known_pid(value) -> str:
    if value not in PIDS:
        raise BadRequest(f"unknown participant {value!r}")
    return value

def parse_day(value, profile: Profile) -> dt.date:
    try:
        day = dt.date.fromisoformat(str(value)) if value else profile.today
    except ValueError as exc:
        raise ValueError("simulation date must be YYYY-MM-DD") from exc
    if profile.first_day is None or profile.today is None or not (
            profile.first_day <= day <= profile.today):
        raise ValueError(
            f"simulation date must be between {profile.first_day} and {profile.today}")
    return day


# The recorded whole-record monitoring run (experiment_alerts.py), which the
# replay page plays back without calling the model.
REPLAY = HERE.parent / "evidence" / "monitoring" / "replay-r1"
FACTS_HEAD = "What the monitor knows about it, for you to weigh, not thresholds:\n"


@functools.lru_cache(maxsize=None)
def replay_run(pid: str) -> dict:
    """Each day's holding detectors, and each recorded review: what the coach
    was handed, what it read, what it found and what it decided."""
    rows_file = REPLAY / f"{pid}.jsonl"
    if not rows_file.exists():
        raise BadRequest(f"no recorded monitoring run for {pid}")
    rows = [json.loads(line) for line in rows_file.read_text().splitlines()]
    audits = [json.loads(line) for line in
              (REPLAY / "state" / pid / "audit" / f"{pid}.jsonl").read_text().splitlines()]
    reviews = []
    for row, entry in zip(rows, audits, strict=True):   # one audit entry per review, in order
        if not entry["input"].startswith(f"[monitoring event, {row['at']}]"):
            raise ValueError(f"replay and audit disagree at {row['at']}")
        reviews.append({**{k: row[k] for k in ("day", "at", "kind", "due", "severity", "summary",
                                               "seconds", "review", "notification", "reply")},
                        "facts": entry["input"].split(FACTS_HEAD, 1)[1].split("\n\n", 1)[0],
                        "sources": entry["source"], "tokens": entry["tokens"],
                        "model": entry["execution"]["model"], "recorded": entry["timestamp"][:10]})
    full = Profile(pid)
    days = [full.first_day + dt.timedelta(days=i) for i in range((full.today - full.first_day).days + 1)]
    return {"pid": pid,
            "labels": {k: v["label"] for k, v in monitor.METRICS.items()},
            "days": [{"day": str(d), "kinds": sorted({x.kind for x in monitor.signals_on(full.snapshot(d), d)})}
                     for d in days],
            "reviews": reviews}


def signal_state(signal: monitor.Signal) -> dict:
    return {"kind": signal.kind, "summary": signal.summary,
            "key": watch.signal_key(signal),
            "severity": round(signal.severity, 2),
            "at": signal.at.isoformat(sep=" ") if signal.at else None}


def session(pid: str, clock: dt.date | None = None) -> coach.Session:
    with LOCK:
        if pid not in SESSIONS:
            p = Profile(pid)
            SESSIONS[pid] = coach.Session(
                pid, p, clock=clock or p.today).load()
        elif clock:
            SESSIONS[pid].set_clock(clock)
        return SESSIONS[pid]


def profile_state(pid: str, raw_day=None) -> dict:
    full = Profile(pid)
    day = parse_day(raw_day, full)
    p = full.snapshot(day)
    adq = p.activity_adequacy()
    habits = p.training_habits()
    hidden_days = (full.today - day).days
    return {
        "pid": pid, "from": str(p.first_day), "as_of": str(day),
        "dataset_to": str(full.today), "hidden_days": hidden_days,
        "sessions": len(p.sessions), "nights": len(p.nights),
        "reports": len(p.reports), "injuries": len(p.injuries),
        "top": next(iter(habits.values()))["label"] if habits else "-",
        "mvpa": adq["median_mvpa_minutes"] if adq else None,
        "verdict": adq["verdict"] if adq else None,
        "active_plan": workspace.active(workspace.home(pid)),
        # Time cannot move back during a live conversation; the page greys it out.
        "conversation_active": bool(SESSIONS.get(pid) and SESSIONS[pid].in_conversation),
    }


def participants() -> list[dict]:
    out = []
    for d in sorted(ROOT.glob("p??")):
        p = Profile(d.name)
        if p.today is None:
            continue
        adq = p.activity_adequacy()
        habits = p.training_habits()
        out.append({
            "pid": d.name,
            "from": str(p.first_day), "to": str(p.today),
            "sessions": len(p.sessions), "nights": len(p.nights),
            "reports": len(p.reports), "injuries": len(p.injuries),
            "top": next(iter(habits.values()))["label"] if habits else "-",
            "mvpa": adq["median_mvpa_minutes"] if adq else None,
            "verdict": adq["verdict"] if adq else None,
        })
    return out


# =====================================================================
# Running a turn, with its steps streamed
# =====================================================================

def run_turn(pid: str, message: str, turn_id: str,
             clock: dt.date, kind: str = "user", signal=None) -> None:
    """Schedule one turn on the shared loop, streaming as it goes.

    Every event is stamped with a sequence number and wall-clock time on
    the way out, so the transcript's ordering is asserted by tests rather
    than assumed from queue behaviour.
    """
    s = session(pid, clock)
    s._busy = True
    q = STREAMS[turn_id]
    seq = itertools.count(1)

    def emit(ev: dict) -> None:
        q.put({"seq": next(seq),
               "t": dt.datetime.now().strftime("%H:%M:%S"), **ev})

    async def drive():
        try:
            out = (await coach.review_signal(s, signal, on_event=emit)
                   if signal else
                   await coach.turn(s, message, kind=kind, on_event=emit))
            emit({"kind": "reply", "text": out["reply"],
                  "turns": out["turns"], "tokens": out["tokens"], "usage": out.get("usage"),
                  "stop": out.get("stop"), "outcome": out.get("outcome"),
                  "human_decision": out["human_decision"],
                  "workspace": out.get("workspace"),
                  "data_results": out.get("data_results", []),
                  "summary_status": out.get("summary_status")})
            # The trail ends on why the turn stopped, as the status line does.
            state = {"answered": "complete"}.get(out.get("stop"), out.get("stop") or "complete")
            emit({"kind": "state", "state": state, "text": f"state {state}"})
        except Exception as exc:                 # surfaced, not swallowed
            # The exception type stays in the audit row; the page gets plain words.
            emit({"kind": "error", "text": f"This turn failed and nothing was saved: {exc}"})
        finally:
            s._busy = False
            emit({"kind": "done"})

    asyncio.run_coroutine_threadsafe(drive(), LOOP)


# The offline suites the page runs (Evidence, Tests), with what each one establishes.
SUITES = (
    ("test_agent.py", "Core properties: time sealing, observation contract, workspace and plan saving, monitoring policy, audit, HTTP views"),
    ("test_hardening.py", "Request guard, participant ids, memory, audit hash chain, sensor boundary, guideline retrieval"),
    ("test_behavior.py", "Behaviour contracts: exact audit records, failed runs, statistics wording"),
    ("test_calculations.py", "Computed statistics, self-report wording, and the data fixes: young-record load, device sessions, repeated nights, WHO coverage"),
    ("test_asking.py", "Asking before planning: a question waits for the person, no plan rides on it, warning symptoms stop"),
    ("test_evidence.py", "Checked final answers: citations from this turn, corrections, missing records"),
    ("test_summary.py", "Delivery over HTTP/SSE: tables from tools, drafts kept in the audit, the plan draft table"),
    ("test_codex_request.py", "What the model receives through Codex: only the coach instructions, tools and messages"),
)
EVIDENCE_DIR = HERE.parent / "evidence"


def _parse_suite(stdout: str, stderr: str, code: int) -> list[dict]:
    groups, current = [], {"name": "checks", "checks": []}
    for line in stdout.splitlines():
        text = line.strip()
        if text.startswith("[") and "]" in text[:5]:
            current = {"name": text, "checks": []}
            groups.append(current)
        elif text.startswith(("PASS", "FAIL")):
            if current not in groups:
                groups.append(current)
            current["checks"].append({"ok": text.startswith("PASS"),
                                      "name": text[4:].strip()})
    if code != 0 and not any(not c["ok"] for g in groups for c in g["checks"]):
        # An assertion that stopped the suite before it printed a FAIL line.
        last = (stderr.strip().splitlines() or ["exited with an error"])[-1]
        groups.append({"name": "stopped", "checks": [{"ok": False, "name": last[:200]}]})
    return groups


def run_suites() -> dict:
    """Every offline suite, in parallel; no model calls."""
    import subprocess
    import sys
    procs = [(f, title, subprocess.Popen([sys.executable, f], cwd=HERE, text=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE))
             for f, title in SUITES]
    suites = []
    for f, title, proc in procs:
        try:
            out, err = proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        groups = _parse_suite(out, err, proc.returncode)
        checks = [c for g in groups for c in g["checks"]]
        suites.append({"file": f, "title": title, "exit": proc.returncode,
                       "passed": sum(c["ok"] for c in checks),
                       "failed": sum(not c["ok"] for c in checks), "groups": groups})
    passed = sum(x["passed"] for x in suites)
    failed = sum(x["failed"] for x in suites)
    return {"suites": suites,
            "groups": [g for x in suites for g in x["groups"]],
            "summary": f"{passed} passed, {failed} failed across {len(suites)} suites",
            "exit": 0 if all(x["exit"] == 0 for x in suites) else 1}


def live_evidence() -> dict:
    """The latest passing live-model E2E run written by e2e.py, if any."""
    def read(name, default):
        f = EVIDENCE_DIR / name
        return json.loads(f.read_text()) if f.exists() else default
    return {"summary": read("e2e-live-summary.json", None),
            "checks": read("e2e-checks.json", []),
            "cases": read("e2e-cases.json", []),
            "errors": read("e2e-errors.json", [])}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):                      # quiet
        pass

    # -- helpers ------------------------------------------------------
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Revalidate every time: a cached app.js outliving a server update
        # would run old page code against the new API.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False,
                                    default=str).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or "{}")
        except ValueError as exc:
            raise BadRequest("body must be JSON") from exc
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        if "pid" in body:
            known_pid(body["pid"])
        return body

    def _same_origin(self) -> bool:
        """Refuse requests another website makes from the user's browser.

        Binding to 127.0.0.1 keeps other machines out, not other tabs. Every
        API call can spend model credit or change state, so a cross-site
        request is rejected, and POSTs must be JSON, which a plain HTML form
        cannot send without a CORS preflight this server never approves.
        """
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        return not origin or urllib.parse.urlparse(origin).netloc == self.headers.get("Host")

    # -- routes -------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        if path.startswith("/api/"):
            if not self._same_origin():
                return self._json({"error": "cross-site request refused"}, 403)
            try:
                if "pid" in qs:
                    known_pid(qs["pid"][0])
                return self._get(path, qs)
            except (BadRequest, KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)
        return self._get(path, qs)

    def _get(self, path, qs):
        if path in ("/", "/index.html"):
            return self._send(200, (WEB / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path in ("/replay", "/replay.html"):
            return self._send(200, (WEB / "replay.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path == "/full-replay":
            return self._send(200, (HERE.parent / "Offline Demo.html").read_bytes(),
                              "text/html; charset=utf-8")
        for name, ctype in (("app.js", "text/javascript"),
                            ("replay.js", "text/javascript"),
                            ("style.css", "text/css")):
            if path == f"/{name}":
                return self._send(200, (WEB / name).read_bytes(),
                                  f"{ctype}; charset=utf-8")

        if path == "/api/participants":
            return self._json(participants())

        if path == "/api/profile":
            try:
                return self._json(profile_state(
                    qs["pid"][0], (qs.get("as_of") or [None])[0]))
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/about":
            return self._json({
                "semantic_tools": list(coach.SEMANTIC_TOOLS),
                "knowledge_tools": list(coach.KNOWLEDGE_TOOLS),
                "control_tools": list(coach.CONTROL_TOOLS),
                "skills": [s["name"] for s in skills.catalogue()],
                "tools": len(coach.tool_names()),
                "workspace": [workspace.MEMORY, workspace.DRAFT, workspace.VERSIONS + "/"],
                "model": coach.MODEL,
                "guidelines": knowledge.catalogue(),
                "monitor_metrics": monitor.METRICS})

        if path == "/api/snapshot":
            full = Profile(qs["pid"][0])
            day = parse_day((qs.get("as_of") or [None])[0], full)
            return self._json(observe.snapshot(full.snapshot(day), day))

        if path == "/api/inspect":
            include_statistics = (qs.get("include_statistics") or ["false"])[0]
            if include_statistics not in ("true", "false"):
                return self._json({"error": "include_statistics must be true or false"}, 400)
            full = Profile(qs["pid"][0])
            day = parse_day((qs.get("as_of") or [None])[0], full)
            p = full.snapshot(day)
            end = parse_day((qs.get("end") or [str(day)])[0], full)
            # Default to a real recent window ending at the simulation date,
            # not the dataset end - the previous default parsed a missing
            # start as `today` and collapsed to a one-day window.
            raw_start = (qs.get("start") or [None])[0]
            start = (parse_day(raw_start, full) if raw_start
                     else max(p.first_day, end - dt.timedelta(days=27)))
            if start > end:
                return self._json(
                    {"error": "start must not be after end"}, 400)
            cs = (qs.get("compare_to_start") or [None])[0]
            ce = (qs.get("compare_to_end") or [None])[0]
            if bool(cs) != bool(ce):
                return self._json(
                    {"error": "compare_to needs both a start and an end"}, 400)
            compare = (parse_day(cs, full), parse_day(ce, full)) if cs else None
            if compare and compare[0] > compare[1]:
                return self._json(
                    {"error": "compare_to start must not be after end"}, 400)
            try:
                return self._json(observe.inspect(
                    p, (qs.get("domain") or ["training"])[0],
                    start, end, compare,
                    include_statistics=include_statistics == "true"))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/memory":
            try:
                pid = qs["pid"][0]
                p = Profile(pid)
                day = parse_day((qs.get("as_of") or [None])[0], p)
                return self._json(workspace.memory(workspace.home(pid), day))
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/plan":
            try:
                pid = qs["pid"][0]
                day = parse_day((qs.get("as_of") or [None])[0], Profile(pid))
                return self._json(workspace.summary(workspace.home(pid), day))
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/audit":
            return self._json(audit.read(qs["pid"][0])[-40:])

        if path == "/api/audit/verify":
            pid = qs["pid"][0]
            return self._json({"problems": audit.verify(pid),
                               "entries": len(audit.read(pid))})

        if path == "/api/signals":
            # What the person can see at any time: every condition holding
            # today, with its history and the coach's reviews of it, and the
            # one-off events of the last 28 days.
            pid = qs["pid"][0]
            full = Profile(pid)
            day = parse_day((qs.get("as_of") or [None])[0], full)
            p = full.snapshot(day)
            state = watch.run_state(pid)
            conditions = []
            for x in monitor.signals_on(p, day):
                if monitor.is_event(x):
                    continue
                facts = monitor.condition_facts(p, x)
                mine = state["conditions"].get(x.kind)
                mine = mine if mine and mine["started"] == facts["started"] else {}
                conditions.append({**signal_state(x), **facts,
                                   "reviews": mine.get("reviews", []),
                                   "next_review": mine.get("next_review")})
            events = [signal_state(x)
                      for d in (day - dt.timedelta(days=i) for i in range(27, -1, -1))
                      if d >= p.first_day
                      for x in monitor.signals_on(p.snapshot(d), d) if monitor.is_event(x)]
            return self._json({"run": watch.summary(state), "reviewed": state["reviewed"],
                               "as_of": str(day), "conditions": conditions, "events": events})

        if path == "/api/next-signal":
            try:
                pid = qs["pid"][0]
                full = Profile(pid)
                day = parse_day((qs.get("as_of") or [None])[0], full)
                # Join the run that covers this day, or start one here, before
                # asking what is due, so a new run does not inherit old reviews.
                state = watch.extend_run(pid, day, day)
                nxt, due = watch.next_due(full, day, state)
                landing = nxt or full.today
                # Every day this click opened is part of the run, whether or
                # not a signal fired. Passing the interval (not a count)
                # keeps a retried GET from inflating the run and a new start
                # from inheriting it.
                run = watch.summary(watch.extend_run(pid, day, landing))
                return self._json({
                    "done": nxt is None,
                    "from": str(day),
                    "day": str(landing),
                    "dataset_to": str(full.today),
                    "advanced_days": (landing - day).days,
                    "run": run,
                    "signals": [{**signal_state(x), "due": why} for why, x in due],
                })
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/monitor-series":
            try:
                full = Profile(qs["pid"][0])
                day = parse_day((qs.get("as_of") or [None])[0], full)
                kind = (qs.get("kind") or ["resting_hr"])[0]
                days = int((qs.get("days") or ["42"])[0])
                raw_at = (qs.get("at") or [None])[0]
                until = dt.datetime.fromisoformat(raw_at) if raw_at else None
                return self._json(monitor.metric_series(
                    full.snapshot(day), kind, day, days, until))
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

        if path == "/api/replay":
            return self._json(replay_run(qs["pid"][0]))

        if path == "/api/tests":
            return self._json(run_suites())

        if path == "/api/transcript":
            # The live conversation's turns, for a reload or athlete switch.
            # Loads a conversation left before a restart, too.
            s = session(qs["pid"][0])
            # A conversation the next turn cannot resume is not shown as remembered.
            if s._client is None and not s.resumable():
                return self._json({"turns": [], "clock": None})
            return self._json({"turns": s.transcript, "clock": str(s.clock)})

        if path == "/api/evidence":
            return self._json(live_evidence())

        if path == "/api/stream":
            return self.stream(qs["turn"][0])

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._same_origin():
            return self._json({"error": "cross-site request refused"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 415)
        try:
            return self._post(path, self._body())
        except (BadRequest, KeyError) as exc:
            return self._json({"error": f"bad request: {exc}"}, 400)

    def _post(self, path, body):
        if path == "/api/turn":
            turn_id = str(body["turn"])
            message = str(body["message"])
            try:
                p = Profile(body["pid"])
                clock = parse_day(body.get("as_of"), p)
                with LOCK:
                    if turn_id in STREAMS:
                        raise ValueError("that turn id is already streaming")
                    s = session(body["pid"], clock)
                    if s._busy:
                        raise ValueError("a turn is already running for this participant")
                    STREAMS[turn_id] = queue.Queue()
                    run_turn(body["pid"], message, turn_id, clock)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)
            return self._json({"ok": True, "turn": turn_id})

        if path == "/api/clock":
            try:
                p = Profile(body["pid"])
                clock = parse_day(body.get("as_of"), p)
                session(body["pid"], clock)
                return self._json({"ok": True, "as_of": str(clock)})
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)

        if path == "/api/signal":
            turn_id = str(body["turn"])
            try:
                full = Profile(body["pid"])
                clock = parse_day(body.get("as_of"), full)
                day = parse_day(body.get("day"), full)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            if day > clock:
                return self._json({"error": "that signal is after the simulation date"}, 400)
            # The same detector call the background simulator made, on a
            # record ending that day, so a signal it found is always found.
            sig = next((x for x in monitor.signals_on(full.snapshot(day), day)
                        if x.kind == body["kind"]), None)
            if sig is None:
                return self._json({"error": "signal not found"}, 404)
            done = watch.run_state(body["pid"])["reviewed"].get(watch.signal_key(sig))
            if done is not None:
                return self._json({"error": "this signal was already reviewed in this monitoring run "
                                   f"({'Alert raised' if done['notify'] else 'no Alert'}); "
                                   "Reset starts a new run"}, 409)
            try:
                with LOCK:
                    if turn_id in STREAMS:
                        raise ValueError("that turn id is already streaming")
                    s = session(body["pid"], clock)
                    if s._busy:
                        raise ValueError("a turn is already running for this participant")
                    STREAMS[turn_id] = queue.Queue()
                    run_turn(body["pid"], sig.summary, turn_id, clock,
                             "monitor", signal=sig)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 409)
            return self._json({"ok": True, "turn": turn_id})

        if path == "/api/plan":
            # The one human checkpoint: the person saves the agent's draft as
            # their plan, or discards it. The draft is read from the
            # workspace, so the client cannot post a plan in as the coach's.
            pid = body["pid"]
            with LOCK:
                live = SESSIONS.get(pid)
            if live is not None and live._busy:
                return self._json(
                    {"error": "a turn is running; decide after it finishes"}, 409)
            home = workspace.home(pid)
            if workspace.draft(home) is None:
                return self._json({"error": "there is no plan draft to decide on"}, 409)
            day = live.knowledge_day if live is not None else parse_day(body.get("as_of"), Profile(pid))
            decision = body.get("decision")
            if decision == "save":
                try:
                    version = workspace.save_draft(home, day)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 409)
                audit.append(audit.from_plan_decision(pid, "plan_saved", version, str(day)))
                return self._json({"ok": True, "version": version})
            if decision == "discard":
                plan = workspace.draft(home)
                workspace.discard_draft(home)
                audit.append(audit.from_plan_decision(pid, "draft_discarded", plan, str(day)))
                return self._json({"ok": True})
            return self._json({"error": "decision must be save or discard"}, 400)

        if path == "/api/reset":
            # "keep": "workspace" starts a new conversation only; the coach's
            # memory and plans, the monitoring run and the audit log stay.
            pid, keep = body["pid"], body.get("keep")
            if keep not in (None, "workspace"):
                return self._json({"error": "keep must be workspace or absent"}, 400)
            with LOCK:
                stale = SESSIONS.get(pid)
                if stale is not None and stale._busy:
                    return self._json({"error": "A turn is running; wait for it to finish before resetting."}, 409)
                SESSIONS.pop(pid, None)
            if stale is not None:
                # Stop the live Codex process before its files are removed.
                asyncio.run_coroutine_threadsafe(stale.close(), LOOP).result(timeout=30)
            # This person's Codex thread and conversation state; others keep theirs.
            shutil.rmtree(coach.SESSIONS_DIR / pid, ignore_errors=True)
            if keep is None:
                shutil.rmtree(workspace.home(pid), ignore_errors=True)
                watch.reset_run(pid)
                audit.archive(pid)
            return self._json({"ok": True})

        return self._send(404, b"not found", "text/plain")

    def stream(self, turn_id: str):
        q = STREAMS.get(turn_id)
        if q is None:
            return self._send(404, b"no such turn", "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while True:
            try:
                ev = q.get(timeout=240)
            except queue.Empty:
                break
            try:
                self.wfile.write(
                    f"data: {json.dumps(ev, ensure_ascii=False, default=str)}"
                    f"\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            if ev.get("kind") == "done":
                break
        STREAMS.pop(turn_id, None)


def main() -> None:
    if not PIDS:
        raise SystemExit("PMData is missing. Run python run.py from the project root, "
                         "or extract the data-inclusive distribution again.")
    print(f"FitCoach on http://localhost:{PORT}")
    print(f"  model {coach.MODEL} via {coach.PROVIDER} / Codex, effort {coach.REASONING_EFFORT}, "
          f"turn limit {coach.MAX_MODEL_REQUESTS} model requests / {coach.TURN_TIMEOUT_S:.0f} s")
    if coach.PROVIDER == "openrouter":
        print("  OpenRouter key: " + ("configured" if os.environ.get("OPENROUTER_API_KEY")
                                       else "missing; Replay works, live chat needs OPENROUTER_API_KEY"))
    elif not os.environ.get("OPENAI_API_KEY"):
        live = coach.chatgpt_credentials()
        print("  OPENAI_API_KEY is not set: Codex borrows this machine's ChatGPT login "
              + ("(found)" if live else "- none is live; open Codex once or set a key"))
    print(f"  {len(participants())} participants, "
          f"{len(coach.SEMANTIC_TOOLS)} semantic + {len(coach.KNOWLEDGE_TOOLS)} "
          f"guideline + {len(coach.CONTROL_TOOLS)} control tools, {len(skills.catalogue())} skills, "
          f"{len(knowledge.catalogue())} guideline documents")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
