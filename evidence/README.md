# Final evaluation evidence

Start with [the test case table](TEST_CASES.md), then the two results
summaries. This folder records evaluation of one system version;
`replay-r1`, `replay-r2` and `replay-r3` are repeats, not different versions.

| Evidence | Purpose |
| --- | --- |
| [TEST_CASES.md](TEST_CASES.md) | 43 cases, expected and actual behavior; 41 fully met expectations |
| [monitoring/RESULTS.md](monitoring/RESULTS.md) | Final monitoring summary, accuracy and limitations |
| [safety-probe/RESULTS.md](safety-probe/RESULTS.md) | 20-message safety probe, with translation provenance |
| `e2e-checks.json`, `e2e-cases.json` | 72 recorded HTTP end-to-end checks and their inputs |
| `e2e-tests.json` | Recorded results of 330 offline checks |
| `e2e-errors.json`, `e2e-audit.json` | Corrections and exact recorded execution |
| `behavior/`, `clarification/` | Scenario results and dialogue checks |
| `screenshots/` | Final report figures from the application; the Check-in figure and `06-checkin-audit.jsonl` are from the English demonstration recorded on 9 October 2026. Files `01` and `10` to `13` were captured again on 11 October from a copy of the recorded state, in a narrower window so the text can be read in print; `04-plan-panel` and the right half of `fig-plan-draft` were captured again on 14 October in the same way, after the panel's change marker was reworded |
| `monitoring/replay-r*/` | Recorded reviews and their audit evidence |
| `monitoring/evaluation-r*/`, `monitoring/factcheck/`, `monitoring/label-audit/` | Labels and checks supporting monitoring scores |

Recorded live results date from 5 October 2026. English translations retain
those outcomes; they do not establish performance on newly translated inputs.

To reproduce the English safety-probe presentation without model calls:

```bash
python3 evidence/safety-probe/translate.py
```

To recalculate a monitoring score from saved labels, from the project root
(with the Python dependencies installed):

```bash
ALERT_REPLAY_OUT=evidence/monitoring/replay-r1 \
ALERT_EVAL_OUT=evidence/monitoring/evaluation-r1 \
.venv/bin/python prototype/eval_alerts.py score
```

Repeat with `r2` and `r3` for the other runs. Full live rerun commands are in
[TEST_CASES.md](TEST_CASES.md#re-running). Rerunning live suites can overwrite
recorded evidence; use a separate project copy when checking the submission.
The submission package retains replay audit files, but excludes Codex session
caches and mutable workspace/watch state.
