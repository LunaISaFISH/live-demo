# FitCoach - start here

## This GitHub live-demo copy

On the prepared Mac, double-click **Start Demo.command**, then keep its Terminal
window open. The app opens at <http://localhost:8765>.

**Presentation guide:** [完整现场操作手册](DEMO_GUIDE.md).
**Emergency fallback:** double-click [Offline Demo.html](Offline%20Demo.html).
This standalone file replays the complete verified workflow without the server,
network or model login. The existing `/replay` page covers monitoring only;
`/full-replay` serves the complete recorded workflow while the server is running.

Code, reports, slides and evidence are tracked here. The complete original
146 MB ZIP, including all 16 participants, is in the
[data release](https://github.com/LunaISaFISH/live-demo/releases/tag/data-2026-10-22).
`run.py` now restores that smaller packaged dataset via `restore_data.py`,
checks its SHA-256, then starts the original app. `download_pmdata.py` remains
available for recovery from the upstream 1.4 GB archive.

The original `MANIFEST.txt` describes the original ZIP; changes in this copy
are recorded in Git. Credentials, local conversation state, dependencies and
unpacked data are excluded from Git.

[Local validation results](evidence/local-demo-2026-10-22/RESULTS.md): 72 live
HTTP/SSE E2E checks and 331 offline checks passed. Python 3.13 and the actual
browser workflow were also checked on this Mac. Model/network/login availability
can change; keep the standalone replay open before presenting.

---

CA6117 group project: a personal coaching agent using PMData records, with
check-in conversations, training plans approved by the user, and monitoring.

解压后在 `FitCoach` 文件夹运行 `python3 run.py` 即可安装依赖并启动。
数据已包含，无需另行下载。若收到单独的 `FitCoach-demo.env`，将它复制到
此文件夹并改名为 `.env`，即可使用其中的 OpenRouter 配置。Windows 请按下文
使用 WSL2；原生 Windows 尚未实测。

## Reading order

| What you need | Open |
| --- | --- |
| Read the final report | [Final PDF](report/FitCoach_Report.pdf) |
| See the presentation | [Slides](presentation/FitCoach_Presentation.pptx), with speaker notes |
| Edit the report text | [Word copy](report/FitCoach_Report.docx) |
| Understand the implementation or run the demo | [Prototype guide](prototype/README.md) |
| Check what was tested and what failed | [Evidence index](evidence/README.md) |

The final PDF is the main review document.

## Project structure

```text
FitCoach/
  README.md                 Start here
  report/                   Final report, PDF and Word
  presentation/             Slides with speaker notes
  prototype/                Application, web UI, skills and tests
  evidence/                 Final evaluation, results and screenshots
  history/                  Original translation sources in the submission package
  requirements.txt          Pinned Python dependencies
  .env.example              Optional local settings, without credentials
  run.py                    Install dependencies, check data, start the app
  data/pmdata/              Required PMData records and sensor indexes
  download_pmdata.py        Optional data recovery/download
```

The data-inclusive distribution contains the records for all 16 participants
and the 64 sensor indexes, about 1.8 GiB extracted. Unused food images and the
original 1.4 GB archive are excluded. Credentials and `.venv/` are not included.

## Run the demo

Install Python 3.11 or newer (3.13 recommended), fully extract the ZIP to a
normal folder outside `/tmp`, then run this from the extracted `FitCoach` folder:

```bash
python3 run.py
```

The launcher creates a local `.venv`, installs the three pinned direct
dependencies (`anyio`, `openai-codex`, `rdflib`), verifies the packaged data and
starts the server. On subsequent runs, installed dependencies and complete data
are reused. Open <http://localhost:8765>. Keep the terminal running; Ctrl+C stops
it. `python3 run.py --check` prepares and checks the installation without starting
the server or spending model credits.

The launcher uses its own location, so the project can be moved or renamed.
If another copy already uses port 8765, set `FITCOACH_PORT=8766` in `.env`.

### Model access

**Replay and the local record views need no paid API key and make no model
calls.** Live Coach conversations, planning and new signal reviews require one
of these authentication methods. No credential is embedded in the ZIP.

Copy `.env.example` to `.env` in the extracted `FitCoach` folder, then choose one:

- **OpenRouter:** set `OPENROUTER_API_KEY`. It automatically selects OpenRouter
  and defaults to `openai/gpt-5.6-terra`. No OpenAI key, Pi or Codex account login
  is required. If given a separate `.env` for this demo, put that file here.
- **OpenAI API:** set `OPENAI_API_KEY`; leave `OPENROUTER_API_KEY` empty.
- **Existing Codex ChatGPT login:** leave both keys empty. The current code reads
  `~/.codex/auth.json`, not the OS credential store. Log in on the same machine
  and OS user with `codex -c 'cli_auth_credentials_store="file"' login`.
  If the access token expires, use Codex to refresh it or log in again.

Optional `FITCOACH_PROVIDER=openai` or `openrouter` explicitly chooses a provider.
`FITCOACH_MODEL` overrides the model; OpenRouter needs its full provider/model
slug. Restart the server after changing `.env`. Shell variables take precedence.
A key must have credit and access to the selected model; expired or exhausted
keys cannot run live conversations. Installing Codex alone does not grant model
access. The Python dependency includes the Codex runtime, so an additional CLI
installation is only needed if choosing the Codex-login method.

### Platforms

Fresh-install and live OpenRouter verification: [results](evidence/portability-2026-10-22/RESULTS.md).

- **macOS:** clean installation and local checks are tested with Python 3.13.
- **Linux:** clean installation and local checks are tested with Python 3.12 in
  a Debian container. Live shell tools require the OS to permit Codex's sandbox
  (user namespaces / bubblewrap). A container's default seccomp policy may block
  this even when the web page starts. Do not disable the application's sandbox.
- **Windows:** use WSL2 with Ubuntu and run the Linux commands inside WSL, keeping
  the project under the Linux home directory. Native Windows has not been
  validated; the earlier claim that changing the Python executable path alone
  was enough was incorrect. WSL must also provide the sandbox prerequisites.

First installation needs internet for Python packages. On Debian/Ubuntu, install
`python3-venv` if Python reports that `ensurepip` is unavailable. Reading the PDF,
slides and recorded evidence needs no Python installation.

### Optional data download / recovery

The data-inclusive ZIP needs no dataset download. If using a source-only copy,
`run.py` invokes `restore_data.py` when the data is missing or incomplete.
Alternatively run `python3 download_pmdata.py` yourself. It verifies HTTPS,
resumes partial downloads, extracts the required files and creates the sensor
indexes. The bundled public intermediate certificate repairs the dataset host's
incomplete chain; TLS verification stays enabled. Network speed depends on the
remote host and connection. You may instead place an already downloaded
`pmdata.zip` under `data/` and rerun the script.

`python3 download_pmdata.py --check` validates the prepared file-size manifest,
including indexes. The distribution's `MANIFEST.txt` additionally records SHA-256
hashes of all packaged files. Neither check makes model requests.

## Check the results

The report describes the recorded 5 October 2026 live evaluation. Its 330
offline checks were also rerun successfully on 15 October, after five
guideline passages were corrected against their sources, the page's styles
were touched up and the plan panel's change marker was reworded. The English
presentation of four safety-probe inputs and replies is a translation of
that original run, not a new English-only evaluation.

With dependencies and data installed, run the distribution checks and offline
suites from the root. The sandbox test also requires `curl` (on Debian/Ubuntu,
`sudo apt-get install curl`):

```bash
.venv/bin/python test_distribution.py
for t in agent hardening behavior codex_request calculations evidence asking summary; do
  .venv/bin/python prototype/test_$t.py || break
done
```

Keep the project outside `/tmp`: Codex's sandbox allows reads there, so the
check that the shell cannot read the project's code fails.

Live checks require model access. `prototype/e2e.py` runs against a server
and resets its test participant, so use a separate test copy. The full
commands and interpretation are in [the evidence index](evidence/README.md).

## What is not included

Unused food images, the original dataset ZIP, installed dependencies,
credentials and the application's interactive state are not included. The
recorded monitoring audits that Replay and the scores need are in `evidence/`,
and `history/source-evidence-2026-10-05/` keeps the original sources of the
translated material. `MANIFEST.txt` lists every file with its SHA-256 hash.
