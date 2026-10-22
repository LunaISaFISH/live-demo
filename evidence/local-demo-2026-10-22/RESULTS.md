# Local presentation validation — 22 October 2026

Timezone: Asia/Singapore. Machine: this Mac (Apple Silicon).

## Verified outcomes

- Extracted the user-supplied data-inclusive ZIP. All 333 packaged data files matched the original SHA-256 manifest; all 16 participant folders and 64 sensor indexes are present.
- Isolated real-model HTTP/SSE E2E run: **72 passed, 0 failed**, `gpt-5.6-terra`, existing local Codex login, Python 3.12.14. Evidence is saved alongside this file. No login tokens were copied into the repository.
- Eight offline suites: **331 passed, 0 failed**. Rerun successfully with the final independent Homebrew Python **3.13.17** environment; see `offline-python313.txt`.
- Final Python 3.13 distribution checks: **5 passed**.
- Added data-recovery regression checks: **9 passed** (integrity, path safety, missing archive, download handling).
- Added standalone-replay regression checks: **4 passed** (complete chain, faithful replies, no raw execution, embedded content, HTTP route). Coverage for the two new Python modules is in `coverage.txt`.
- Actual browser operation on Python 3.13: selected p16 / 2020-03-01; submitted personal constraints; generated the seven-day draft; revised it to one Friday walking entry; clicked Save as my plan; triggered Advance to next review; verified the monitoring Alert and the intact four-entry audit chain. See `browser-audit.json` and `browser-audit.png`.
- Original monitoring Replay: Next review reached recorded sensor review 1 of 66 for p16, with the recorded date and model displayed.
- Full workflow replay: all 12 navigation entries rendered correctly; the seven-day draft, revision, actual recorded saved plan, monitoring review and audit were checked. See `full-replay.png`.
- The standalone `Offline Demo.html` contains its CSS, JavaScript and recorded data; no external assets, fetch calls or model dependencies. Its bytes match `/full-replay`. Browser interaction was tested over local HTTP; direct `file:` navigation is blocked by the in-app browser's policy, so opening the file by double-click was not automated.
- With the application server stopped and port 8765 closed, the already-open complete replay still switched to the monitoring step and displayed the recorded result without error.
- The launcher was exercised against the prepared environment; it checks dependencies/data, starts the localhost server and opens the browser. Control+C released the port; a subsequent cold start succeeded. The demo listens only on 127.0.0.1.

- Fresh public GitHub clone downloaded the release asset successfully, verified its SHA-256, restored all 16 participants and sensor indexes, and passed data readiness. A new Python 3.13 virtual environment was then installed by `run.py --check`; dependencies, data and imports all passed from that fresh clone. GitHub reports the same SHA-256 for the uploaded 152,701,133-byte asset.

## Scope and interpretation

The original monitoring replay is historical evidence from 5 October. The new standalone replay is built from the successful 22 October E2E run. Both are explicitly recorded playback, not new live inference.

E2E checks cover clarification, memory, planning, natural-language revision, human approval, safety replies, injection refusal, monitoring, audit and reset/persistence. The raw trace can include rejected intermediate tool attempts or corrections; a successful test does not mean the model never needed correction.

These are successful runs under the tested account, model and environment, not a guarantee of future network availability, model access, quota or identical wording. Keep the standalone HTML open as the presentation fallback.

The browser rehearsal participant was reset after evidence capture, so the live demo can start cleanly. Interactive state and credentials are ignored by Git.
