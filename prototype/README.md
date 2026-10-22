# FitCoach prototype

FitCoach simulates a personal coach over the completed PMData dataset. The
date selected in the browser is the last completed day, ending at 23:59:59:
records after that boundary have not happened and cannot be read.

## Participants

The page offers three of the 16 PMData participants, chosen so that together
they reach every workflow path (`PARTICIPANTS` in `web/app.js`). Tests,
evaluations and module demos use the same three.

| Participant | Why | Paths it reaches |
| --- | --- | --- |
| p05 | Five of the seven monitoring detectors fire; three ankle injuries in March | Clarification, a plan with recently logged ankle injuries, revising and saving the draft, intraday heart-rate review |
| p12 | Sleep almost unrecorded (8 nights), a recent shoulder injury, records end 2020-03-27 | Insufficient data stated as unknown, fatigue clarification, partial and declined answers, knee-pain question |
| p16 | The only participant consistently below the WHO activity range, 19 sessions | Activity adequacy against WHO, preferences kept in memory, warning symptom, prompt injection, Alerts, under-active signal |

The "days since the last record" detector never fires for any PMData
participant, so only the offline suite covers it.

## Run

For a fresh installation, use `python3 run.py` from the repository root. The
data-inclusive distribution already contains the required records and indexes.
See the root README for platform limitations and authentication. The manual
commands below are for macOS/Linux with Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The Agent runs on the OpenAI Codex SDK (`openai-codex`, pinned), which starts
a local Codex app-server per conversation. Each participant has one
persistent Codex thread in their own `.sessions/<pid>/`: its Codex home (never
the machine's `~/.codex`, so no local configuration, MCP servers or skills
load) and `conversation.json` with the thread id, the page transcript, the
person's messages. Switching participant
switches conversation, and after a server restart the next message resumes
the same thread with `thread_resume`. If the system prompt or the tool
catalogue changed, or the thread cannot be resumed, a new thread starts and
the stale transcript is dropped. Codex's shell snapshot is switched off so the process environment
is never written to that directory.

Model access is the only external service. Authentication options:

- Set `OPENROUTER_API_KEY` in the root `.env` to use OpenRouter, with default
  model `openai/gpt-5.6-terra`. This does not require a Codex account login.
- Set `OPENAI_API_KEY` in the shell or in a `.env` file in the project root
  (copy `.env.example`). OpenAI recommends this for automation.
- Or, on a machine where the Codex CLI is logged in with a ChatGPT account
  (`codex login`), set nothing. FitCoach reads the access token from
  `~/.codex/auth.json`, passes it to Codex over its pipe and never writes it.
  The refresh token is not copied, so that login stays valid; if the token
  has expired, open Codex once to refresh it.

The account needs access to the model. Optional:
`FITCOACH_MODEL` (default `gpt-5.6-terra`), `FITCOACH_REASONING_EFFORT`
(default `medium`) and `FITCOACH_TURN_TIMEOUT` (default 300 seconds). The
offline suites make no model calls and need no login.

```bash
cd prototype
../.venv/bin/python server.py
```

Open `http://localhost:8765`.

```bash
../.venv/bin/python test_agent.py          # offline properties
../.venv/bin/python test_hardening.py      # security, safety, audit and regression checks
../.venv/bin/python test_agent.py --live   # real-model conversation
../.venv/bin/python e2e.py                 # real HTTP/SSE workflow
```

## Responsibility split

Code computes facts and enforces hard safety. The Agent makes coaching
decisions.

The Agent reads the record through two tools:

- `read_record(days, end)`: every domain (training, sleep, recovery,
  physiology, lifestyle, injury) over the last `days` days to `end` (the
  simulation date by default), each against the window of the same length
  just before, with record coverage and changes computed in code; the state
  at the window's end (intensity ceiling and its reasons, load, sleep debt,
  resting heart rate position, readiness, activity against WHO, habits,
  recent injuries); notable findings; data quality; the limits stated once.
  About 10,000 to 15,000 characters. The page shows a table only for the
  domains the answer cites.
- `inspect_sensor_window`: bounded 1 to 15 minute heart-rate, steps, calories,
  distance and logged-exercise context, capped at the simulated event time,
  for an intraday monitoring signal

One tool retrieves documents:

- `search_guidelines`: BM25 retrieval over the local guideline library in
  `knowledge/` (WHO 2020 physical activity, AASM/SRS adult sleep duration, ACSM
  2015 warning signs, training-load monitoring and the limits of load ratios,
  NHS sprains and strains). Only current guidance is kept: a source superseded
  by a newer one on the same question is removed, not stored alongside it. Each passage carries its publisher,
  citation and URL, is shown to the person as "Guidance retrieved", and becomes
  a `knowledge` evidence card. Once a turn has read the person's records, an
  answer citing only guidance is returned for correction.

Self-reported answers reach the Agent as words, never scores: readiness
("low readiness to train"), fatigue, soreness, stress, mood, sleep quality
("more tired than normal", "more sore than normal") and session effort
("hard effort"). `profile.py` maps each scale once, in the direction the data
shows (a low soreness score is a sorer day), and treats an answer left at one
end of its scale for 14 reports running as not answered (one participant left
readiness at 0 for 48 reports while every other answer varied). Code rules
use the same mapping.

Each carded item in a tool result carries its `card_id`, and the result ends
with a short card list; the full card stays server-side for the checks and
the page, so the model reads each fact once.

One control:

- `submit_signal_review`: records the Agent's monitoring verdict, with the
  supporting observations and limitations that ground it

Memory and plans are not tools. The Agent has its own workspace
(`workspace.py`), a folder it reads and writes with Codex's shell and
`apply_patch`; the system prompt carries its index (`workspace.INDEX`):

| File | Written by | What it holds |
| --- | --- | --- |
| `memory.md` | the Agent | What the person said that matters later (preferences, goals, constraints, a change in an old injury), one line per fact with their exact words: `- 2020-03-01 \| preference \| dislikes swimming \| "I hate swimming"`. Not how they feel today |
| `plans/draft.json` | the Agent | The plan being worked on, `{"days": [{date, activity, minutes, intensity}]}`; the page shows it live |
| `plans/versions/NNN.json` | the server, when the person saves | Saved plans, numbered, each with its changes from the one before; read-only to the Agent |

Each person's committed workspace is `.workspace/<pid>/`. A turn copies it
into the Codex working directory (`workspace.stage`; memory dated after the
simulation date is left out), the Agent edits the copy, and once the final
answer passes its checks, `workspace.check` looks at what changed:

- only `memory.md` and `plans/draft.json` may exist besides the saved
  versions; anything else is removed, and a changed saved version is restored;
- a new memory entry is dated the simulation date, has a known kind, at most
  300 characters, and a quote of at most 120 characters copied exactly from
  one of the person's messages;
- a monitoring turn changes nothing (memory is restored, a draft change undone);
- the draft changes only in a user turn whose outcome is `answer`, and must be
  in the page's format: dates after today and unique, activity from the
  catalogue, intensity rest/easy/moderate/hard, 0 to 300 minutes, a rest day
  at 0 minutes.

Errors go back to the Agent with the final-answer errors. Only an accepted
turn is committed (`workspace.commit`), with later-dated memory kept; a
failed turn commits nothing. The workspace as the turn found it also gets
evidence cards (`<token>-memory`, `<token>-plans`) the answer can cite; the
prompt lists them by file, and the Agent reads the files itself.

Each task's procedure is a Codex skill, read by Codex's own mechanism:
`skills/<name>/SKILL.md` with `name` and `description` frontmatter
(`check-in`, `planning`, `monitoring`), and shared detail in
`skills/references/` (how to ask the person, how to read the record).
Every conversation gets a copy in its temporary working directory's
`.agents/skills`; Codex lists the skills (name, description, file), and the
model opens a SKILL.md with Codex's shell (`exec_command`) when a task matches,
then only the references it needs. The system prompt keeps only what holds on
every turn (role, evidence and safety rules, warning symptoms, dates,
answering and citations).

The shell and `apply_patch` exist for that and are confined by a Codex
permission profile (`agent.sandbox_overrides`): they can read the skills
folder and the system basics a shell needs, and write the workspace except its
saved plans, and nothing else - not the raw records, not other participants'
sessions, not the code, not login files - with no network and no inherited
environment. Each writable folder is listed under its path as given and its
resolved path (`/var` is `/private/var` on macOS): `apply_patch` matches the
first, the shell the second. Codex's macOS sandbox also leaves `/tmp` and the
system temporary folder writable whatever the profile says; nothing written
there is committed or read back. The profile also names denied reads
(the dataset, `~/.codex`, `~/.pi`): under approval policy `never`, Codex runs a
command that asks for `require_escalated` outside the sandbox unless some read
is denied (`codex-rs/core/src/tools/sandboxing.rs`), and the named denials keep
it inside. Codex runs with an empty HOME, so this machine's personal skills and
user configuration never load. Every shell command and its result go to the
stream and the audit. `test_codex_request.py` captures the request Codex sends
and runs Codex's own sandbox against the profile to check all of this.
Every `apply_patch` file change goes to the stream and the audit as well.

The Agent authors the plan and writes it to its workspace draft: one entry
per day (date, activity, minutes, intensity). Code checks the draft's format
and when it may change (above); the planning skill asks the Agent to respect
what the person said and to weigh their record itself.

Before planning, the Agent reads the record (the last 28 days and 90 days of
history), then asks in its own words what the record cannot show. The final
answer contract lists five things to know before a plan: goal, availability,
contraindications (pain, injuries, conditions), current state (asked in a
question of its own, even when an injury is also being asked about) and
equipment and preferences. A turn that asks ends with outcome
`clarification` and leaves the draft as it was. No tool gates the plan and
no code writes the questions.
Plans start after today; plans beyond seven days are provisional and must be
revised as the simulation clock advances.

A draft is still only advice. The page's plan panel shows it next to the
saved plan, marking the days that differ (a draft that starts after the saved
plan ends continues it rather than changing it). The person asks for changes
in the chat and the Agent rewrites the draft; the one real commit action is
the human checkpoint on the panel: **Save as my plan** makes the draft the
next saved version, and **Discard draft** drops it (`POST /api/plan`). Both
are appended to the audit log as their own entries. A draft whose first day
has passed cannot be saved until the Agent updates it. **New chat** starts a
new conversation that keeps the workspace, so memory and saved plans carry
into it; **Reset** clears it. There is no approval step on merely viewing
advice.

## Three workflows

1. Check-in conversation (Coach view, Check in mode): the person asks about their
   current state or past records -> the coach asks first when the answer
   depends on something only the person knows -> the record over the window the
   question needs, guideline passages where a standard applies -> an answer that
   cites its evidence inline. A described warning symptom (chest pain,
   fainting, breathlessness at rest) is answered in that reply with advice to
   stop and seek medical care, without diagnosis; the turn ends as a safety
   stop, and nothing is locked for later turns.
2. Planning (Coach view, Plan mode): memory, saved plans and history, then the
   coach's own questions -> the Agent writes its plan draft, shown on the
   plan panel, and explains it -> changes asked for in the chat revise the
   draft -> human checkpoint (save, or discard).
3. Monitoring (Monitor): deterministic detector raises a Candidate Signal ->
   Agent Signal Review (explained/unexplained/unclear, grounded in submitted
   observations and limitations) and the Agent's Alert decision, with its
   reason. An Alert is a prompt to pay attention, not a diagnosis.

Signals come in two kinds. An Event (the intraday heart-rate detector) is a
one-off at its own timestamp and is reviewed when it happens. A Condition
(the six daily detectors) holds for as long as its window says so - weeks
below the WHO range can hold for months - and is listed in Monitor mode,
and counted in a "conditions holding" button by the clock, for as long as it
holds: when it started, how it looked then, how it looks now, the coach's
reviews of it and when it will look again. The coach reviews a Condition when
it starts, or when a run first sees it if it began earlier, and again on the
day it chose with `recheck_in_days`; each review shows it that history. No
rule in code judges whether a Condition got better or worse.

**Replay** (`/replay`) plays back the recorded whole-record run
(`experiment_alerts.py`) without calling the model: the detector lanes fill in
day by day, and at each review the side panel shows, step by step, what the
monitor handed the coach, what it read, what it found, its verdict and its
Alert decision with the day it will look again.

The time bar at the top of the page carries a background-time simulator. Starting
from the current Simulation Date, **Advance to next review** opens one day at a time, runs the
detectors against a record physically truncated to that day, pauses on the
first day with something for the coach (an Event, a new Condition, or one due
to be looked at again), and automatically starts the Agent review.

Whether a signal becomes an Alert is the Agent's decision. The monitoring
prompt gives it the signal's severity on the detector's own 0-1 scale, how
many consecutive days it has run, the day's record coverage, the Alerts
already raised in the run and, for a Condition, its history and the other
Conditions holding, as facts to weigh rather than cut-offs, and the
monitoring skill says what counts: something other than the signal's own
measure must account for it before it is explained; a brief, steep
heart-rate rise at rest with nothing around it is worth an Alert on the day,
with advice to seek urgent care if it came with warning symptoms; a
weeks-long shortfall is worth one Alert; an ordinary explanation or a repeat
of an Alert already raised is not. Code checks only that the review is
complete (verdict, note, observations, limitations, `alert`,
`alert_reason` and `recheck_in_days`) and records the decision as given,
with those facts. A
signal the Agent never reviews is shown, marked as code's fallback, rather
than hidden.

The simulated run is kept per participant (`.watch/<pid>.json`), identified by
its start day and scanned-through cursor (a retried request changes nothing,
and starting from a date outside the run begins a fresh one): the days
scanned, each signal reviewed (once per run), the Alerts raised and each
Condition's reviews and next review. The page shows it and Reset clears it.

Every Candidate Signal has an explicit availability time. Intraday signals use
their real trigger timestamp. A daily aggregate is available only at 23:59:59,
after that day is complete, and the Agent is told never to describe it as a
morning or intraday reading.

The same tab renders a native SVG timeline for all seven detectors: intraday
heart rate, resting-heart-rate position, readiness, seven-day sleep debt,
training-load ratio, weekly activity and days since the last record. The
intraday detector combines confidence-filtered heart rate with low recorded
steps and logged exercise boundaries. The chart stops at the exact simulated
event time, shows genuine missing-sample gaps, and marks the event timestamp.

## High-frequency data

The raw PMData archive is the source of truth. `heart_rate.json`, `steps.json`,
`calories.json` and `distance.json` are extracted for all 16 participants. Tiny
`.day-index.json` sidecars map dates to byte ranges, so the application reads
only the requested day instead of loading a multi-gigabyte export or copying it
into another database.

```bash
cd ..
.venv/bin/python download_pmdata.py
```

The data-inclusive distribution contains the required records and indexes; no
download is needed. The original 1.4 GB archive and unused food images remain
excluded. The script can restore missing data from the Simula archive, with
verified HTTPS, retry and resume support.

Daily semantic views also retain sleep stages, exercise sessions, heart-rate
zones, activity minutes, wellness and sRPE reports, injuries, meals, hydration,
alcohol and weight. Food photos remain excluded because their contents are not
structured and none of the three workflows currently needs image analysis.

## Live transcript

The Agent transcript panel streams the public events of a turn as each step
completes: derived workflow states (receiving, reading_skill,
using_workspace, observing, investigating, consulting_guidelines,
drafting_training_plan once the draft file is written, responding), the agent's
public messages as bounded previews, shell commands as the agent wrote them
with the whole script and its output behind "script and output", file
changes, tool calls with concise parameters and their effective simulation
date, each tool result as one readable line (the windows and findings a
record read returned, the guideline sections retrieved) with the formatted
result behind "detail", every error a final answer was returned for, and
what the turn committed to memory and the plan draft. A failed command is
marked. The trail ends on why the turn stopped (complete, waiting for you,
stopped: safety advice, stopped: request declined, stopped: limit reached,
failed), as the status line does. The last
turn's trail is kept with the conversation, so a reload or an athlete switch
shows it again. It is not chain-of-thought: the stream carries no private
reasoning, and the states are derived from the same public events the panel
displays.

## Safety and time

- `Session.visible()` seals every read at the simulation date.
- An intraday monitoring review binds sensor reads to the signal timestamp and
  daily history to the previous completed day.
- There is no conversation-level safety lock: a described warning symptom
  gets care advice in that reply only.
- A memory entry is kept only with an exact quote from the person's own
  messages. The system prompt treats the memory file as untrusted data, like
  every tool result. Retrieved text can never extend the registered-tool
  allow-list.
- Sensor windows flag calorie readings above 8 kcal/min with no steps and a
  heart rate under 100 bpm as a likely device artefact. During an intraday
  review, a logged session still running at the event time shows no totals.
- The server accepts only known participant ids, refuses cross-site requests,
  and requires JSON bodies. The clock, Reset, New chat and saving or
  discarding the draft are refused while a turn is scheduled or running.
- Each turn is bounded twice: at most 24 model requests (`MAX_MODEL_REQUESTS`,
  a runaway guard: a plan turn uses about 10) and a
  wall-clock budget (`FITCOACH_TURN_TIMEOUT`). A turn over its time budget is
  stopped, audited as failed, and the conversation restarts.
- A failed turn commits nothing: its workspace changes are dropped, and its
  audit row carries `status: failed` with the reason.
  Token usage is recorded per model request (new input, cached input,
  output, reasoning, context size), so a failure never spills into the next
  turn's figures.
- Missing data is `insufficient`, never silently normal.
- The model receives only the coach's instructions, Codex's list of the coach's skills, its four tools and the
  turn's messages. Codex's own additions (working directory, real date and
  timezone, sandbox text, installed skills, multi-agent instructions,
  web, plugins, MCP servers and extra tools) are switched off in
  `agent.CODEX_OVERRIDES`. Most of those keys appear in Codex's config
  schema but not its prose docs, so the version is pinned and
  `test_codex_request.py` captures the request Codex sends and checks it.
  gpt-5.6 models call tools through `exec`, a JavaScript tool host; it is
  the one Codex tool left, and it reaches only the coach tools, the sandboxed
  shell and `apply_patch`.
- Every tool call goes through `agent.call_tool`, which refuses any name
  outside the catalogue; any other Codex request (approval for a command or
  a file change) is declined, and any item other than messages, commands,
  file changes, tool calls and Codex compacting a long conversation fails the
  turn. Compaction is shown in the trail ("context compacted"), which lets a
  long conversation such as the whole-record monitoring replay continue.
- Every turn is appended to `.audit/<pid>.jsonl` with timestamp, input, source,
  action, output and human decision. A turn itself records
  `human_decision=not_required`; memory entries added or removed and the
  draft created or revised appear in the action trace, and each checkpoint
  decision (`plan_saved` or `draft_discarded`) is appended as its own entry
  with the exact plan and cutoff. A
  monitoring turn's single entry carries both the Agent's verdict (with its
  observations and limitations) and its Alert decision ("alert" or "no
  alert", with the reason and the facts it was given), because the entry is
  written after the decision, not before it.
- Each audit line stores the SHA-256 of the line before it, so
  `audit.verify` detects an edited or removed entry. Checkpoint entries keep
  the exact plan that was saved or discarded.
- Reset clears that participant's conversation (their `.sessions/<pid>/`
  thread and state), workspace (memory, draft and saved plans) and the
  monitoring run's state, and moves the audit log to `.audit/archive/` rather
  than deleting it. Other participants keep theirs.

## Files

| File | Role |
| --- | --- |
| `agent.py` | Codex conversation and its sandbox, four tools, the every-turn system prompt, tool gate, final-answer check and the turn's workspace stage/check/commit |
| `workspace.py` | The Agent's workspace: memory and plan files, the per-turn check, commit, and saving a draft |
| `skills.py`, `skills/` | Codex skills (check-in, planning, monitoring) and their references, installed per conversation |
| `knowledge.py`, `knowledge/` | Guideline library and BM25 retrieval behind `search_guidelines` |
| `realtime.py` | On-demand indexes and semantic windows over raw Fitbit streams |
| `observe.py` | `read_record`, and the semantic observations the page shows |
| `plan.py` | Planning context (intensity ceiling, habits, load) and the activity and intensity vocabulary |
| `profile.py` | Reconciled PMData record and derived measures |
| `monitor.py` | Deterministic candidate detection and the facts given to the Agent for each signal |
| `watch.py` | Day-by-day replay, what is due for review each day, the monitoring run's state |
| `server.py` | Standard-library HTTP/SSE server, plan panel and checkpoint endpoints |
| `web/` | Simulation, conversation, live transcript and evidence UI |
| `test_agent.py` | Offline property suite |
| `test_hardening.py` | Request guard, audit chain, sensor boundary, guideline retrieval |
| `e2e.py` | Real HTTP/SSE end-to-end verification |

The project deliberately has no raw-data tools, no per-metric tool explosion,
no deterministic plan generator, and no approval ritual on merely viewing
advice - the only human checkpoint sits on the one action that commits
state, saving a plan draft.

## Behavior evaluation and complete evidence

The behavior checks:

```bash
# From the project root, no model calls:
.venv/bin/python prototype/test_behavior.py
.venv/bin/python prototype/behavior_eval.py
# Actual model runs, isolated from the application's workspaces and audit:
.venv/bin/python prototype/behavior_eval.py --live --case sparse_recovery
.venv/bin/python prototype/behavior_eval.py --live --repeat 3
```

`behavior_eval.py` prepares or runs 15 cases covering goal changes, participant
and date changes, sparse records, sleep comparisons, missing context, plan
revision, future requests, a mixed override request and fresh monitoring sessions.
`safety_probe.py` sends 20 labelled messages, each in a fresh conversation, to
measure warning-symptom recognition. Every run
gets a new directory under `evidence/behavior/` with case prompts, expected
local data, exact execution records and machine checks.
The default command only prepares evidence; it does not report model success.

Record windows use `observed` when no baseline has been established.
Their data quality distinguishes calendar days, records, recorded days and
coverage basis; `not_assessed` does not certify explanatory sufficiency.
Training summaries label duration, steps and heart rate as session statistics,
with duration provenance derived from the records actually included.

New audit entries carry an `execution` object: exact project prompts and tool
schemas, the Codex overrides, public model messages and tool calls with their
IDs, complete handler inputs and outputs, business events and terminal
metadata. Model reasoning is dropped at the Codex boundary and never stored.
Full tool results are stored locally even when the UI only displays previews.
These records can contain participant data and memory entries. Failed and
truncated model runs are recorded as failures and commit no workspace change.

The live HTTP E2E (`e2e.py`) writes its evaluation evidence to `evidence/`
(or `FITCOACH_EVIDENCE_DIR`) only when every check passes: `e2e-checks.json`
(every check by section), `e2e-cases.json` (each input sent to the live Agent,
positive and negative, with what happened), `e2e-errors.json` (final
answers, workspace changes and calls the code rejected), `e2e-audit.json` and
`e2e-live-summary.json`. The page shows these under Evidence, Tests, and runs all eight
offline suites on demand. Use an isolated test server because the suite resets
its participant. `../history/source-evidence-2026-10-05/` keeps the original
sources of the translated material.

### Final answer, citations and stop conditions

Every turn ends with a structured final answer, enforced by the model API
through Codex's output schema (`evidence.SCHEMA`):

- `answer`: what the person reads, in plain language, citing evidence inline
  by card ID, e.g. `[3f2a9c1d-e4]`. Each tool result carries turn-local
  evidence cards; the page turns each citation into a link to its row in
  "Evidence sources". Tool statistics are also rendered as tables straight
  from the tools.
- `outcome`: answer, clarification, safety_advice or refusal - the model's
  judgement of what kind of reply this is.
- `reason`: the symptom recognised, what was declined, or what the answer
  rests on.

A plan is not part of the answer: it is the workspace draft (above). A turn
that changed the draft also shows a "Plan draft" table in its results,
marking what changed, and the plan panel refreshes.

The code checks provenance, not meaning: every cited ID must exist in this
turn, and once the person's records were read the answer must cite at least
one of them rather than only guidance. A failing answer goes back to the same
thread with the exact errors, at most twice (`MAX_CORRECTIONS`); still wrong,
the turn fails and commits nothing. The workspace check runs in the same
loop. Whether a statement follows from what it
cites is not checked in code; the monitoring fact-check measures it for Alerts. Model
drafts and returned answers stay in the audit, never on the page. Run
`../.venv/bin/python test_evidence.py` and `test_summary.py` for the HTTP/SSE
contract checks.

Each turn records why it stopped (`stop` in the reply and the audit row), and
the page's status line names it:

| Stop | Set by |
| --- | --- |
| `answered` | an accepted final answer |
| `awaiting_user` | the model asked the person something (`outcome: clarification`); the page shows its questions and waits |
| `safety_advice` | the model judged a warning symptom (`outcome: safety_advice`); code drops any plan from the turn, shows a safety banner and audits `safety_advice: <symptom>` |
| `refused` | the model declined a message whose only request was to break its rules (`outcome: refusal`); audited as `refused: <what and why>`. A message that mixes such a request with an ordinary one is answered, declining that part and naming it in the reason; advising against a requested change is an answer |
| `limit_reached` | the model-request cap or the wall-clock budget |
| `failed` | any other failure, including a final answer still failing its checks |

Recognising a symptom or an injection is a judgement about meaning, so the
model makes it in the structured answer and code enforces the consequence -
the same split as the monitoring review. Keyword lists were rejected: they
miss paraphrases and misfire on negations.

### Ask before personal advice

The coach asks, in its own words, what the record cannot show before a personal
interpretation or a plan; the rules are `skills/references/asking.md`, written into
the check-in and planning skills, and two checklists: the five-item plan checklist
in the final answer contract, and in the check-in skill what to know before
explaining why someone feels worse than usual (when it started, sleep and waking,
pain, and for tiredness or recovery unlogged exertion). After a reply it answers,
asking again only about what was left out entirely; if the person
declines, it answers from the records and keeps the uncertainty. Code does not
check whether the right things were asked; it guarantees that a turn that asks
waits for the person and carries no plan. Run
`../.venv/bin/python test_asking.py`, or the real model simulation
`../.venv/bin/python clarification_eval.py`, which reads how each turn ended.
