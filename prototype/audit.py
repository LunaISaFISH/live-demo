"""
The audit log.

The brief asks for six fields - input, action, source, timestamp,
output, human decision - and they are not an arbitrary list. Together
they answer the question that matters after the fact: *why did this
system tell this person to do that?* A turn's trace records the
semantic context the agent read and what it changed in its workspace
(memory entries, the plan draft); its own `human_decision` is
`not_required` because advice and a draft commit nothing. The one real
commit action is the person's: saving the draft as their plan, or
discarding it. That decision is appended as its own entry, so the log
stays append-only instead of rewriting the turn it judges.

Run:  ../.venv/bin/python audit.py            # show the log
      ../.venv/bin/python audit.py --verify   # check it is well-formed
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
from dataclasses import asdict, dataclass, field

LOG_DIR = pathlib.Path(__file__).parent / ".audit"

# The six fields the brief names, in order, so a reader can check the
# log against the requirement without a mapping table.
FIELDS = ("timestamp", "input", "source", "action", "output",
          "human_decision")


@dataclass
class Entry:
    """One turn, complete enough to reconstruct why it said what it said."""
    timestamp: str
    pid: str
    input: str                       # what arrived, and from where
    input_kind: str                  # "user" | "monitor" | "human"
    source: list[str] = field(default_factory=list)   # tools that answered
    action: list[str] = field(default_factory=list)   # what was done
    output: str = ""
    human_decision: str = "not_required"
    # not_required        a turn: advice and a draft commit nothing
    # plan_saved          the person saved the draft as their plan
    # draft_discarded     the person discarded the draft
    note: str = ""
    turns: int = 0
    tokens: int | None = None         # tokens newly processed this turn
    usage: dict | None = None         # requests, new/cached input, output, reasoning, context
    status: str = "completed"         # a turn that failed is "failed"; it committed nothing
    stop: str | None = None           # why the turn ended: answered, awaiting_user,
                                      # safety_advice, refused, limit_reached or failed
    # Monitoring turns only; None everywhere else, and absent from rows
    # written before these fields existed - readers must not require them.
    review: dict | None = None        # verdict, note, observations, limitations
    notification: dict | None = None  # the Alert decision, its reason, who made it, the facts given
    execution: dict | None = None     # exact public messages and tool boundary records
    data_results: list[dict] = field(default_factory=list)
    summary_status: str | None = None
    plan: dict | None = None          # plan decisions: the exact plan saved or discarded
    prev_hash: str = ""               # sha256 of the previous line; set by append

    def line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def path(pid: str) -> pathlib.Path:
    return LOG_DIR / f"{pid}.jsonl"


def append(entry: Entry) -> None:
    """Append-only, one JSON object per line.

    Append-only on purpose: a log a later turn can rewrite is not
    evidence of anything. One line per turn keeps it readable with `tail`
    and parseable without a library.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Each line carries the hash of the one before it, so an edited or
    # deleted earlier line breaks the chain that verify() recomputes.
    lines = path(entry.pid).read_text().splitlines() if path(entry.pid).exists() else []
    entry.prev_hash = _digest(lines[-1]) if lines else ""
    with path(entry.pid).open("a") as f:
        f.write(entry.line() + "\n")


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode()).hexdigest()


def archive(pid: str) -> pathlib.Path | None:
    """Move a person's log aside on Reset. The trail is kept, never deleted."""
    src = path(pid)
    if not src.exists():
        return None
    dest = LOG_DIR / "archive" / (
        f"{pid}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return dest


def read(pid: str) -> list[dict]:
    p = path(pid)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def from_turn(pid: str, message: str, out: dict, *, input_kind: str = "user",
              human_decision: str = "not_required", note: str = "") -> Entry:
    """Turn one agent turn into an entry.

    `source` and `action` are split deliberately. A reviewer asking
    "where did this number come from" wants the tools; one asking "what
    did it do" wants the decisions and the denials. Both come out of the
    same trace, and separating them here is what makes either question
    answerable without reading prose.
    """
    trace = out.get("trace", [])
    return Entry(
        timestamp=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        pid=pid,
        input=message,
        input_kind=input_kind,
        source=[t[5:] for t in trace if t.startswith("tool ")]
               + [t for t in trace if t.startswith("shell ")],
        # "verdict" is the Agent's signal review; "alert"/"no alert" is its
        # decision on telling the person - both must survive into a
        # monitoring turn's audit row.
        action=[t for t in trace
                if t.startswith(("plan draft", "remember", "forget", "DENIED", "safety_advice",
                                 "refused", "final answer returned",
                                 "verdict", "alert", "no alert"))],
        output=out.get("reply", ""),
        human_decision=human_decision,
        note=note,
        turns=out.get("turns", 0),
        tokens=out.get("tokens"),
        usage=out.get("usage"),
        status=out.get("status", "completed"),
        stop=out.get("stop"),
        review=out.get("review"),
        notification=out.get("notification"),
        execution=out.get("execution"),
        data_results=out.get("data_results", []),
        summary_status=out.get("summary_status"),
    )


def from_plan_decision(pid: str, decision: str, plan: dict, cutoff: str) -> Entry:
    """The person saving or discarding the plan draft, as its own entry.

    Appending a fresh entry keeps the log append-only; the turns that wrote
    the draft are never rewritten. `plan` is the saved version, or the
    discarded draft.
    """
    days = plan.get("days") or []
    label = f"version {plan['version']}" if "version" in plan else "the draft"
    return Entry(
        timestamp=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        pid=pid,
        input=f"the person decided on {label}",
        input_kind="human",
        action=[decision.replace("_", " ")],
        output=f"{len(days)} planned day{'' if len(days) == 1 else 's'}, knowledge cutoff {cutoff}",
        human_decision=decision,
        plan=plan,
    )


# =====================================================================
# Reading it back
# =====================================================================

def verify(pid: str) -> list[str]:
    """Structural checks a reviewer would otherwise have to do by eye."""
    problems = []
    rows = read(pid)
    if not rows:
        return [f"{pid}: no log"]
    lines = [x for x in path(pid).read_text().splitlines() if x]
    for i, r in enumerate(rows):
        expected = _digest(lines[i - 1]) if i else ""
        if r.get("prev_hash", expected) != expected:
            problems.append(f"{pid}[{i}]: hash chain broken - an earlier "
                            "entry was edited or removed")
    prev = None
    for i, r in enumerate(rows):
        missing = [f for f in FIELDS if f not in r]
        if missing:
            problems.append(f"{pid}[{i}]: missing {missing}")
        try:
            when = dt.datetime.fromisoformat(r["timestamp"])
        except (KeyError, ValueError):
            problems.append(f"{pid}[{i}]: unreadable timestamp")
            continue
        if prev and when < prev:
            problems.append(f"{pid}[{i}]: timestamp goes backwards")
        prev = when
        if r.get("human_decision") not in (
                "not_required", "plan_saved", "draft_discarded"):
            problems.append(f"{pid}[{i}]: bad decision "
                            f"{r.get('human_decision')!r}")
    return problems


def main() -> None:
    import sys
    pids = sorted({p.stem for p in LOG_DIR.glob("*.jsonl")}) \
        if LOG_DIR.exists() else []
    if not pids:
        print("no audit log yet - run agent.py or test_agent.py --live")
        return

    if "--verify" in sys.argv:
        bad = [x for pid in pids for x in verify(pid)]
        for x in bad:
            print("  ", x)
        print(f"\n{sum(len(read(p)) for p in pids)} entries across "
              f"{len(pids)} people, {len(bad)} problem(s)")
        return

    for pid in pids:
        print(f"=== {pid} ===")
        for r in read(pid):
            print(f"{r['timestamp']}  [{r['input_kind']}] {r['input'][:52]}")
            print(f"   source  {', '.join(r['source']) or '-'}")
            print(f"   action  {', '.join(r['action']) or '-'}")
            print(f"   human   {r['human_decision']}"
                  + (f" ({r['note']})" if r.get("note") else ""))
            print(f"   output  {r['output'][:80]}")
            print(f"   {r['turns']} model requests, {r.get('tokens') or 0} tokens\n")


if __name__ == "__main__":
    main()
