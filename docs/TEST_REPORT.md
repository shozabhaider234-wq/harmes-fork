# Functional test report

Updated 2026-11-27. No benchmark or optimization work was implemented or executed. Current
receipts below supersede stale blockers in the historical notes later in this document.

## Latest live validation

- The isolated E: profile reports `openai-codex: logged in`, configured for
  `openai-codex` / `gpt-6-luna`. Hermes `--help`, `--version`, model routing, basic agent
  initialization, and a real one-shot task ran from the PM environment whose source is
  `E:\AI_Project_Sem3\harmes-fork`.
- Rust memory stored a synthetic marker, returned it in a new Hermes session, and returned it
  again after the isolated daemon restarted on the same database. The native background
  memory reviewer ran with real model execution, completed without error, reported a memory
  update, and a new session retrieved the synthetic fact. Its resolved route was
  `openai-codex` / `gpt-6-luna`.
- The corrected synchronous parent-child verifier passed all six stages and cleaned its
  synthetic namespace. A separate asynchronous test observed the child running in the
  background, the parent completing another turn, the child succeeding with authorized Rust
  memory, delivery of the result, and parent processing in a later turn.
- Real Prime TaskHarness tasks persisted objectively successful experiences. The configured
  Prime reviewer resolved to `openai-codex` / `gpt-6-luna` and returned a legitimate
  no-refinement result. No candidate was produced; live baseline/candidate execution,
  accepted guidance after restart, and live rollback are therefore not claimed. Deterministic
  tests separately cover candidate acceptance, persistence, guidance injection, and rollback.
  The isolated store contains nine experiences, two persisted `no_candidate` runs, no
  proposals, and no active artifact. The `/memd-learn status` handler was invoked from the
  registered plugin command, and Hermes's built-in `/learn` still resolves normally.

## Latest functional regression run

- Rust functional tests, through `memd/scripts/windows.ps1 -Action test` on E: with the local
  MiniLM/ONNX assets: **38 passed, 0 failed, 0 ignored** (22 core, 13 SQLite, 3 daemon).
- `python -m unittest discover -s memd/tests -v` in the PM dev/test venv with local model
  assets: **46 passed, 0 failed, 2 skipped**. Both skips are native Unix-socket tests on
  Windows. Prime tests are included in this total.
- The canonical runner covered 17 disjoint Hermes files across profile OAuth persistence,
  Rust-memory delegation, asynchronous delegation, lifecycle, background review, config,
  learning capture, prompt/guidance injection, and provider routing: final per-file result
  set **234 passed, 0 failed, 3 skipped**. One lifecycle test double was updated to isolate
  the scheduler, then that affected file reran 4/4. The batch's prompt test fixture now
  includes the real diagnostic sink needed when this workspace's AGENTS.md truncation notice
  is emitted.
- Current distinct functional grouping: **318 passed, 0 failed, 5 skipped** (Rust 38 + memd
  Python 46 + Hermes 234; no files overlap within this grouping). Earlier larger receipts are
  historical and are not added to these counts because they overlap the current Hermes files.
- The separate fresh eight-file delegation-boundary run listed below passed **118, 0 failed,
  0 skipped**. Those eight files do not overlap the current 17 Hermes files, so the combined
  unique functional total is **436 passed, 0 failed, 5 skipped**. Older 275/393 totals are
  excluded because those selections overlap these current receipts.

The canonical Hermes command used `scripts/run_tests.sh` with `HERMES_PYTHON` set to
`memd/.tmp-build/harness-final-tests/Scripts/python.exe`, `-j 2`, and a 180-second per-file
limit. The final per-file result set includes the 16-file successful batch plus the corrected
four-test lifecycle file rerun; the earlier failed result for that file is superseded.

```powershell
$env:HERMES_PYTHON = 'E:/AI_Project_Sem3/harmes-fork/memd/.tmp-build/harness-final-tests/Scripts/python.exe'
$env:TEMP = 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e\temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
& 'C:\Program Files\Git\bin\bash.exe' scripts/run_tests.sh `
  tests/hermes_cli/test_auth_profile_fallback.py `
  tests/tools/test_delegate_memd_access.py tests/tools/test_memd_subagent_readonly.py `
  tests/tools/test_async_delegation.py tests/tools/test_async_delegation_retirement.py `
  tests/hermes_cli/test_cli_async_delegation_delivery.py `
  tests/tui_gateway/test_delegation_session_lifecycle.py `
  tests/agent/test_subagent_lifecycle.py tests/agent/test_background_review.py `
  tests/agent/test_background_review_memory_scope.py `
  tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_provider_config_validation.py `
  tests/agent/test_turn_finalizer_learning_capture.py tests/agent/test_plugin_prompt_sections.py `
  tests/agent/test_plugin_llm_task_routing.py tests/agent/test_system_prompt.py `
  tests/hermes_cli/test_resolve_ephemeral_system_prompt.py -j 2 --file-timeout 180 --file-retries 0
```

The Rust command kept `CARGO_TARGET_DIR` under `memd/target-gnu`; temp files were on E:.

## Successful deterministic suites

- Rust functional suite:
  `rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File memd/scripts/windows.ps1 -Action test`
  — **38 passed, 0 failed, 0 ignored** (22 memcore, 13 SQLite storage, 3 daemon). This run
  used the existing local MiniLM model and ONNX runtime for offline embedding/retrieval tests.
- memd Python suite, with `MEMD_BIN`, `MEMD_TEST_MODEL_DIR`, and `MEMD_TEST_ORT_DYLIB` set to
  the existing local files:
  `python -m unittest discover -s memd/tests -v`
  — **44 passed, 0 failed, 2 skipped**. Both skips are native Unix-socket tests on Windows.
  The three configured real-local-model client cases ran.
- Canonical Hermes delegation/memory selection, six files via `scripts/run_tests.sh`:
  `tests/tools/test_delegate_memd_access.py`, `tests/tools/test_memd_subagent_readonly.py`,
  `tests/tools/test_async_delegation.py`,
  `tests/tools/test_async_delegation_retirement.py`,
  `tests/hermes_cli/test_cli_async_delegation_delivery.py`, and
  `tests/tui_gateway/test_delegation_session_lifecycle.py` — **65 passed, 0 failed, 2
  platform skips** (macOS and POSIX-only tests on Windows).
- Canonical Hermes config/learning/prompt/provider selection, seven files via
  `scripts/run_tests.sh`: config validation, provider config validation, turn-finalizer
  learning capture, plugin prompt sections, plugin LLM task routing, system prompt, and
  ephemeral prompt resolution — **128 passed, 0 failed, 1 platform skip**.

Combined distinct deterministic result before the delegation-boundary rerun:
**275 passed, 0 failed, 5 skipped**. The live parent-child receipt below is functional
evidence, not a unit-test count.

An earlier eight-file canonical run of `test_delegate.py`,
`test_delegate_parallel_interrupt_join.py`, `test_delegate_failure_copy.py`,
`test_async_delegation_failed_surface.py`, `test_delegate_batch_validation.py`,
`test_delegate_depth_prompt.py`, `test_delegate_late_child_inherits_stop.py`, and
`test_delegate_capacity_interrupt.py` exercised nested delegation, cancellation, failure
propagation, batch validation, and concurrent child lifecycle. The first run printed 118
passing tests but timed out on two files after their pytest summaries. That historical run is
superseded by the fresh canonical rerun below.
The timeout occurred at the runner's per-file subprocess wall-clock boundary after pytest had
reported success; no assertion failed. The initial hang did not reproduce, so no test was
weakened or edited to hide it.

The fresh canonical rerun used the same eight files with a 90-second per-file limit and
completed **118 passed, 0 failed, 0 skipped** in 117.2 seconds. In particular,
`test_delegate_depth_prompt.py` completed in 33.0 seconds and
`test_delegate_failure_copy.py` in 8.4 seconds. The individual two-file confirmation also
passed 15/15; those 15 results are included in the eight-file total and are not counted again.

Command (with `HERMES_PYTHON` set to the E: dev/test venv and `TEMP`/`TMP` on E:):

```powershell
& 'C:\Program Files\Git\bin\bash.exe' scripts/run_tests.sh `
  tests/tools/test_delegate.py `
  tests/tools/test_delegate_parallel_interrupt_join.py `
  tests/tools/test_delegate_failure_copy.py `
  tests/tools/test_async_delegation_failed_surface.py `
  tests/tools/test_delegate_batch_validation.py `
  tests/tools/test_delegate_depth_prompt.py `
  tests/tools/test_delegate_late_child_inherits_stop.py `
  tests/tools/test_delegate_capacity_interrupt.py -j 2 --file-timeout 90 --file-retries 0
```

Updated distinct deterministic total: **393 passed, 0 failed, 5 skipped**. This combines the
275 prior non-overlapping tests with the eight-file 118-test selection; none of those eight
files overlap the prior groups.

The suites used a new isolated test venv built from the tracked lock with the supported PM
command (existing environments were not removed):

```powershell
rtk proxy C:\Python315\python.exe -m pm.build_env --source . `
  --out memd/.tmp-build/harness-final-tests --group dev --group test
```

The memd run used these existing local assets:

```powershell
$env:MEMD_BIN = (Resolve-Path memd/target-gnu/debug/memd.exe).Path
$env:MEMD_TEST_MODEL_DIR = (Resolve-Path memd/models/all-MiniLM-L6-v2).Path
$env:MEMD_TEST_ORT_DYLIB = (Resolve-Path memd/onnxruntime/onnxruntime-win-x64-1.31.1/lib/onnxruntime.dll).Path
& memd/.tmp-build/harness-final-tests/Scripts/python.exe -m unittest discover -s memd/tests -v
```

The canonical test wrapper used `HERMES_PYTHON` pointing at that venv and Git Bash:

```powershell
$env:HERMES_PYTHON = 'E:/AI_Project_Sem3/harmes-fork/memd/.tmp-build/harness-final-tests/Scripts/python.exe'
& 'C:\Program Files\Git\bin\bash.exe' -c 'export PATH=/usr/bin:/bin:$PATH; bash scripts/run_tests.sh tests/tools/test_delegate_memd_access.py tests/tools/test_memd_subagent_readonly.py tests/tools/test_async_delegation.py tests/tools/test_async_delegation_retirement.py tests/hermes_cli/test_cli_async_delegation_delivery.py tests/tui_gateway/test_delegation_session_lifecycle.py -j 2'
& 'C:\Program Files\Git\bin\bash.exe' -c 'export PATH=/usr/bin:/bin:$PATH; bash scripts/run_tests.sh tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_provider_config_validation.py tests/agent/test_turn_finalizer_learning_capture.py tests/agent/test_plugin_prompt_sections.py tests/agent/test_plugin_llm_task_routing.py tests/agent/test_system_prompt.py tests/hermes_cli/test_resolve_ephemeral_system_prompt.py -j 2'
```

Logs are in `memd/.tmp-build/harness-final-tests/`.

## Earlier AppContainer live receipt (superseded)

The recorded AppContainer venv ran a real parent-child memory task with `openai-codex` /
`gpt-6-luna`; the six synchronous stages passed and cleanup verified the synthetic namespace
empty. However, the verifier version used for that historical run copied `auth.json` into a
disposable profile, contrary to the explicit no-copy requirement. No secret was printed, the
source file was not modified, and the disposable profile was removed. The verifier has since
been changed to require the isolated `harmes-harness` profile's own login and never copy or
import credentials. The receipt does not prove detached delivery and is not a compliant final
auth setup. See [`PARENT_CHILD_E2E_RESULT.md`](../memd/PARENT_CHILD_E2E_RESULT.md).

## E: runtime provisioning record (initial snapshot; current status above)

The bullets below preserve the repair timeline. Their initial logged-out and failed-agent
observations were resolved by the OAuth persistence fix and subsequent live validation above.

- At the beginning of the E: repair, `E:` had about 11.30 GB free. A process-local runtime
  was created at `memd/.tmp-build/harness-runtime-e` with `cache`, `temp`, `uv-cache`,
  `uv-python`, `pip-cache`, `build/cargo`, `tools`, `installs`, and the isolated profile
  directory. C: had about 9.05 GB free; insufficient C: space was not used as a reason to
  stop.
- Process-local variables were set to the E: runtime during provisioning:
  `TEMP`, `TMP`, `TMPDIR`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`,
  `CARGO_TARGET_DIR`, `HERMES_HOME=<runtime>\profiles\harmes-harness`, and
  `HERMES_RUNTIME_DIR=<runtime>\tools`. No global variables were written.
- Supported repair command:

  ```powershell
  $runtime = 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e'
  $env:HERMES_HOME = Join-Path $runtime 'profiles\harmes-harness'
  $env:HERMES_RUNTIME_DIR = Join-Path $runtime 'tools'
  $env:TEMP = Join-Path $runtime 'temp'
  $env:TMP = $env:TEMP
  $env:TMPDIR = $env:TEMP
  $env:UV_CACHE_DIR = Join-Path $runtime 'uv-cache'
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'uv-python'
  $env:PIP_CACHE_DIR = Join-Path $runtime 'pip-cache'
  $env:CARGO_TARGET_DIR = Join-Path $runtime 'build\cargo'
  & 'C:\Python315\python.exe' -m pm.cli install --trust-recorded
  & 'C:\Python315\python.exe' -m pm.cli doctor
  ```

- PM `status` returned `outcome: ok`, `exit_code: 0`, and `venv_rebuild.ok: true`.
  `pm doctor` verified pinned `uv 0.12.3` and every installed package. `uv.lock` and
  `pm/lock.json` both remain present. `HERMES_HOME` selects PM generations under the E:
  runtime's `installs` directory; changing caches alone would not have moved them.
- The E: venv's `hermes --help` and `hermes --version` passed. The version names
  `E:\AI_Project_Sem3\harmes-fork`; `hermes_cli.__file__` also resolves inside this
  checkout. `hermes model` opened and exited with `No change.` An actual fixture-backed
  `AIAgent` construction still encounters `Access is denied` when a nested PM Python
  process is launched; full agent initialization is not claimed.
- The isolated profile's `hermes auth status openai-codex` says logged out. No credential
  was copied or printed. Exact interactive login command (from PowerShell):

  ```powershell
  $runtime = 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e'
  $env:HERMES_HOME = Join-Path $runtime 'profiles\harmes-harness'
  $env:HERMES_RUNTIME_DIR = Join-Path $runtime 'tools'
  $env:TEMP = Join-Path $runtime 'temp'
  $env:TMP = $env:TEMP
  $env:TMPDIR = $env:TEMP
  $env:PYTHONPATH = 'E:\AI_Project_Sem3\harmes-fork'
  & 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e\installs\da256e215b88f355\environments\8a58c307afe149acaea69cedc75d5437\venv\Scripts\hermes.exe' -p harmes-harness auth add openai-codex --type oauth
  & 'E:\AI_Project_Sem3\harmes-fork\memd\.tmp-build\harness-runtime-e\installs\da256e215b88f355\environments\8a58c307afe149acaea69cedc75d5437\venv\Scripts\hermes.exe' -p harmes-harness auth status openai-codex
  ```

  On 2026-11-27, env-only and explicit-profile device flows printed `Added ... OAuth
  credential #1`, but the E: profile auth store remained 131 bytes with no `credential_pool`.
  Source tracing found that the first single-use provider credential in a named profile with
  no local row was routed to the root's update-only write-through path, which cannot insert
  new credentials. The pool now persists a new profile-owned Codex entry locally. The
  canonical regression run `scripts/run_tests.sh tests/hermes_cli/test_auth_profile_fallback.py -q`
  passed **12/12**. OAuth must be completed again after the fix; no credential contents were
  read or copied during diagnosis.

  Expected final status: `openai-codex: logged in`. The model picker must then be set to
  `openai-codex` / `gpt-6-luna` for the live verification runs.

## Historical C: runtime and authentication observations

- `uv.lock` and `pm/lock.json` are tracked and were not deleted in Git history. The reported
  missing lock is PM refusing a recorded generation whose linked path resolves outside its
  expected install root; the linked generation lock exists. `pm install --trust-recorded`
  rebuilt locked dependencies but kept the link topology.
- The AppContainer Hermes home reports `openai-codex: logged in`; configured provider/model
  are `openai-codex` / `gpt-6-luna`. No OAuth value was printed. A synthetic isolated profile
  still needs its own login. Run `hermes -p harmes-harness auth add openai-codex`; expected
  confirmation: `Login successful!`.
- With the PM-built source/test entrypoint, `hermes --help`, `hermes --version`, and the
  interactive `hermes model` picker were exercised. The picker was exited with “Leave
  unchanged” and returned “No change.” All still print `automatic dependency repair retry
  limit reached`. The older recorded AppContainer entrypoint imports a stale workspace copy
  with an `IndentationError` in its config module.
- A previous `hermes pm doctor` stopped while provisioning pinned `uv` at the C: cache when
  C: had about 21 MB free. That failure is superseded for the new isolated E: runtime by the
  successful E: PM receipt and doctor run above; it does not establish repair of the old
  AppContainer installation.

## Superseded test-environment diagnostic

Before building the complete locked test venv, a delegation selection ran with the reduced
memd-only interpreter: **35 passed, 9 failed, 2 skipped, plus 3 files with collection/import
errors**. The failures/errors involved missing `rich`, `requests`, and `python-dotenv` and
subprocess timeouts from that incomplete interpreter. The same selection passed in the
complete PM-built dev/test environment with the counts above. An earlier activation attempt
also stopped before tests because PM reported `test-environment: selected environment
escapes its root`.

## Remaining live evidence gap

- The live Prime reviewer returned no refinement, so actual baseline-versus-candidate Hermes
  executions did not run. No live candidate activation, restart injection, or rollback is
  claimed. The deterministic acceptance/rollback and prompt-injection tests passed.
- A later real-model Prime run with additional eligible, objectively evaluated experiences
  may generate a candidate; if so, the actual isolated Hermes candidate evaluator can be
  verified without changing its acceptance policy.

No memory/general benchmark, retrieval metric, latency/storage/RAM/token measurement, scaling
test, or ablation was run.
