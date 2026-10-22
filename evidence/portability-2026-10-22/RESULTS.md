# Fresh-install verification, 22 October 2026

The original submission ZIP was extracted into a new project directory and
installed into isolated virtual environments. Its server failed on both macOS
and Linux with `ModuleNotFoundError: No module named 'rdflib'`. The dataset
request also failed with `CERTIFICATE_VERIFY_FAILED` because the upstream host
omitted the intermediate corresponding to its new certificate.

## Changes

- Added the missing pinned runtime dependency, `rdflib==7.6.0`.
- Added `run.py` to create a local environment, install dependencies, check data
  and launch the server. `--check` stops before serving or making model calls.
- Included all 268 required dataset files and 64 sensor indexes; excluded food
  images. Each raw file's SHA-256 was compared with the original dataset ZIP.
- Added the issuer's public intermediate to the downloader while retaining
  system-root and hostname verification, plus resume, retry and ZIP CRC checks.
- Replaced the directory-only readiness check with a prepared file-size manifest.
- Escaped permission-profile paths as TOML strings. Windows-style paths formerly
  raised a TOML parse error. Linux additionally needs the bundled Codex executable
  readable inside the sandbox because it re-executes there.
- Removed macOS-specific path and error-text assumptions from sandbox tests.
  The network test now verifies curl actually runs and tests against a local
  listener, so a missing curl or an offline internet connection cannot pass it.
- Added OpenRouter provider configuration, a dedicated `OPENROUTER_API_KEY`
  route and full model slugs. The actual Codex app-server accepts the provider;
  the credential-routing regression test verifies no OpenAI login is attempted.

## Results

macOS (Python 3.13.5) and Debian Linux (Python 3.12, aarch64 container) each pass
all eight existing offline suites and the five distribution regression tests.
Both serve the main page, participants, record snapshots and recorded Replay.
A real macOS HTTP/SSE conversation read records and returned an accepted,
cited answer with no error events, using the machine's Codex login.

The new data-inclusive ZIP was then extracted into another directory containing
spaces and Chinese characters. A new environment was created with `run.py
--check`, all packaged manifest hashes were verified, and the server
returned all 16 participants. The Coach page was also inspected in a browser;
see `fresh-package-ui.png`. Data did not need to be downloaded again.

The repaired downloader also completed a verified 1 MiB HTTPS Range request
from Linux. A full repeat network download was unnecessary because the
validated dataset is now included.

## Boundaries

The Linux container needed permission to create user namespaces: Docker's
outer seccomp/AppArmor restrictions were relaxed for this isolated test
container; the application's own Codex sandbox stayed enabled and its read,
write and network boundaries passed. A host that prohibits user namespaces
still needs its administrator to enable the sandbox prerequisites.

Native Windows and WSL2 on a Windows machine have not been executed. The README
now states this and recommends the Linux workflow under WSL2. Passing a
Windows-path parser regression is not a native-Windows runtime certification.

OpenRouter live verification passed with `openai/gpt-5.6-terra` on both platforms.
The relocated macOS package read the last seven days of records and returned
an accepted, cited answer, visible in `openrouter-live-ui.png`. Linux read the
history, wrote a three-day walking draft and saved version 1 through the HTTP
plan checkpoint. Both turns completed without error events. The synthetic
planning inputs were test reports, not new participant observations.

Replay was also exercised in the browser, including advancing to the next
recorded review; see `fresh-package-replay.png`. No model call is needed for it.

No credential is in the distribution. The separately provided demo credential
was checked at the provider for its requested expiry and total spending cap.
Recorded report scores describe the original evaluation, not a complete new
OpenRouter evaluation. Live smoke-test output: `openrouter-live-summary.json`.

Machine-readable results: `checks.json`.
