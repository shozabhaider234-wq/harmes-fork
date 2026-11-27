# Harness completion progress

Updated 2026-11-27. The updated user scope explicitly defers benchmarking and
optimization. Benchmark scripts and existing results are preserved; no benchmark
has been run or extended during this work.

## Phase 0 — Audit

- Completed a source/doc status audit in [HARNESS_COMPLETION_AUDIT.md](HARNESS_COMPLETION_AUDIT.md).
- Added architecture, configuration, Prime API, attribution, test report, and remaining-blocker
  documentation for the work completed here. Benchmark result files remain unchanged.
- Recorded the current branch and commit, and preserved the pre-existing dirty worktree.
- Codebase graph tools were not available; exact-source search was used as fallback.
- Remaining: validate current code where practical and revise the audit from test evidence.

## Phase 1 — Rust memory engine and daemon

- Existing implementation and historical validation are recorded in `memd/VALIDATION.md`.
- No source changes made in this phase.
- Current-tree Rust suite rerun later in this work: 38 passed, 0 failed, 0 ignored.

## Phase 2 — Hermes memory integration and persistence

- Existing provider, review scheduler and migration behavior are described in
  `memd/INTEGRATION.md` and `memd/NATIVE_REVIEW.md`.
- No source changes made in this phase; current-tree session/restart and review recovery
  checks remain to be executed.

## Phase 3 — Multi-agent memory and delegation

- Existing deterministic test coverage and the historical parent-child receipt are
  recorded in `memd/PARENT_CHILD_E2E_RESULT.md`.
- A later six-stage synchronous live rerun passed with clean namespace cleanup; genuine
  asynchronous parent delivery remains open.

## Phase 4 — Context formation

- Existing context builder and core profile work are present in the user-modified tree.
- Do not optimize or benchmark context formation. Focus remaining checks on correctness,
  provenance, isolation, and prompt safety.

## Phase 5 — Prime-inspired learning (initial phase notes; status superseded below)

- Extended `memd/clients/python/prime_learning.py` with durable experience capture,
  deduplication, a structured procedural-candidate boundary, fixture-based acceptance,
  regression rejection, failure state, activation, rollback, and deactivation.
- Added `none`/`prime` provider selection. Unknown providers fail explicitly; AVO is not
  implemented.
- Added `memd/clients/python/task_harness.py`: it passes active guidance to an execution
  callback and records objective outcome, trajectory/tool refs, memory refs, model config,
  and applied artifact versions without copying large transcripts.
- Added `memd/tests/test_prime_learning.py` for task execution, persistence, rejection, error handling,
  unknown outcomes, provider selection, and concurrent writes.
- Test command: `rtk proxy memd/.tmp-build/python-tests/Scripts/python.exe -m unittest memd.tests.test_prime_learning -v`
- Result: 9 passed, 0 failed, including cross-process artifact updates, no-refinement
  handling, and failure/credential handling coverage.
- Remaining: connect real Hermes task/trajectory capture, add runtime config selection,
  execute controlled candidate tasks through isolated baseline/candidate execution, and
  load accepted guidance into normal Hermes tasks.

## Historical functional validation before runtime continuation

- `rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File memd/scripts/windows.ps1 -Action test`
  — passed: 38 Rust tests across `memcore`, SQLite storage and daemon protocol; 0 failed,
  0 ignored. Includes actual local MiniLM inference and SQLite retrieval.
- Windows Python unittest discovery for `memd/tests` with the workspace daemon/model/runtime
  configured — 32 passed, 0 failed, 2 native Unix socket skips on Windows.
- `rtk proxy ...python.exe -m py_compile` for Prime/task harness sources — passed.
- `rtk proxy git diff --check` — passed.
- Attempted required Hermes regression command through `scripts/run_tests.sh`. It could not
  start because the runner tried sourcing the checkout's CRLF `activate` script and Bash
  reported a syntax error at line 27. An escalated retry had the same parse failure. The
  Hermes regression suite is therefore unverified in this task; no workaround modified
  activation files.

## Benchmark boundary

- All benchmark implementation, execution, scoring, performance measurement, and memory
  quality experimentation are deferred. Existing scripts and result files are untouched.
- Controlled deterministic functional fixtures are allowed and remain mandatory.

## Runtime integration continuation (2026-11-25)

- Added an opt-in `learning.provider: none|prime` configuration with structure validation;
  Prime's trigger is manual and its store is derived from the active profile home.
- The memd memory provider now registers a frozen prompt section and the existing
  `on_session_end` hook. Durable foreground turns are captured once with task text,
  response, status, tool/trajectory references, and the guidance version recorded when
  the prompt was first rendered. Outcomes remain unknown until explicitly labeled.
- Added Prime review/status/label/rollback controls. The command is `/memd-learn` because
  Hermes reserves `/learn` for its built-in skill-learning command. Review
  uses Hermes's host-managed auxiliary LLM route and evaluates the same explicitly
  labeled task cases for baseline and candidate guidance. A candidate activates only if
  each fixture does not regress and mean score strictly improves.
- Fixed `activate` checkout line endings by adding an exact `.gitattributes` rule for the
  extensionless script; normalized only that file. The focused Prime suite now passes
  16/16 and py_compile passes for the integration/config/finalizer files.
- The canonical Hermes regression runner passes the 16-file memory/delegation selection:
  159 passed, 0 failed, 1 platform skip. A second canonical run passes the five-file
  learning/config/prompt selection: 120 passed, 0 failed.
- The current memd Python suite passes 39 tests, with 7 expected skips for unavailable
  optional local model assets and Unix-only sockets. A transient Windows `os.replace`
  denial during concurrent writes was fixed with bounded retry; the full suite then passed.
- Rust functional coverage remains at 38 passing tests from the earlier current-tree run.
- No live OpenAI Codex/OAuth task was run. Controlled deterministic review fixtures passed,
  but real-model Prime review and a real Hermes task remain unverified.

## Historical blockers before AppContainer/runtime investigation

- Remaining evidence gap: the controlled live route was attempted but Hermes reported that
  no Codex OAuth token is available. After `hermes model` authentication is completed, run a
  controlled real task and Prime review, then verify accepted guidance in a later session
  and verify rollback removes it in a subsequent session.
- Existing test coverage proves sync/async delegation behavior and memory profile/scope
  integration; real-model parent/child memory sharing across restarts still needs a live
  receipt.

## Runtime and Prime continuation (2026-11-25)

- Exact runtime issue: both user-home and AppContainer PM state contain links into the
  AppContainer install. The checkout PM process resolves those links outside its expected
  install root and correctly refuses to select them. The `recorded dependency lock is
  missing` repair message describes that ownership mismatch; the linked generation lock
  exists, and tracked `uv.lock` / `pm/lock.json` remain present. A locked PM install was run;
  it refreshed a generation but retained the link topology. The public repair command was
  not rerun because it invokes the generation collector.
- With the AppContainer home explicitly selected, checkout `hermes --help` and `--version`
  execute, but still print the dependency-repair retry-limit warning. Runtime restoration is
  incomplete. No full agent session was initialized.
- Prime review now validates fixed configured tasks through actual Hermes leaf-subagent
  execution, comparing baseline and candidate guidance with local objective checks. Checks
  and expected values are not sent to the child or reviewer. Evidence records model/provider,
  execution state, check outcomes, and response hashes. Deterministic integration tests use a
  fake lifecycle service and are not live-model evidence.
- Automatic trigger modes now include `disabled`, `manual`, `after_objective`, and
  `after_n_experiences`; automatic jobs require known explicit outcomes and use durable
  one-shot claims. Validation turns carry a marker that keeps them out of normal experience
  capture.
- Prime suite now passes 18/18 after the latest evaluator and trigger edits. A first canonical
  config test attempt failed at collection due to indentation; the code was corrected, and a
  fresh canonical run is pending. A previous test run passed before those latest edits.
- No Hermes-owned Codex OAuth token was found through the auxiliary route. No live memory,
  Prime learning, native review, or parent-child model task ran. These remain blockers.

### Superseding verification update (2026-11-25)

This update supersedes the OAuth and test-count statements immediately above:

- The missing OAuth observation came from the wrong Hermes home. In the AppContainer home,
  `hermes auth status openai-codex` reports logged in; configured provider/model are
  `openai-codex` / `gpt-6-luna`. No secret was exposed. The AppContainer install still emits
  PM's dependency-repair retry warning, so full CLI runtime initialization remains open.
- Current distinct deterministic receipts are Rust 38 passed; memd Python 46 passed and 7
  skipped; canonical Hermes config/turn-finalizer 16 passed. See `TEST_REPORT.md` for exact
  commands and skip reasons. The 23 Prime tests are included in the 46 memd tests.
- Prime validation now executes configured baseline and candidate variants as actual Hermes
  leaf subagents and evaluates local objective checks. Tests use a fake lifecycle service;
  a real-model validation receipt and any real improvement remain unclaimed.
- Trigger settings are `disabled`, `manual`, `after_objective`, and `after_n_experiences`.
  Only explicit known outcomes qualify, threshold jobs are batched, and durable trigger claims
  prevent duplicate jobs. Ordinary turns do not imply success; validation runs are excluded
  from capture.
- Runtime root cause is a foreign linked PM generation path rather than a deleted tracked
  lockfile. `pm install --trust-recorded` used locked dependencies but retained the link
  mismatch. Public repair was not run because its collector can remove existing environments.
- Remaining live receipts are full runtime/agent initialization, isolated synthetic-profile
  memory persistence and retrieval, native model review, real Prime review/evaluation and
  restart/rollback, plus genuine asynchronous delegation. Deterministic suites do not
  substitute for these.
- The synchronous parent-child live rerun has since passed 6/6 stages through Rust memory,
  with actual `openai-codex` / `gpt-6-luna`, clean temporary namespace cleanup, and disposable
  profile removal. It used finite-session synchronous fallback and does not prove async
  delivery. Details and evidence paths are in `memd/PARENT_CHILD_E2E_RESULT.md`.

## Final validation update (2026-11-25)

- A fresh PM dev/test environment was built from the tracked lock at
  `memd/.tmp-build/harness-final-tests`; no pre-existing environment was removed.
- Current suites: Rust **38/0/0** (pass/fail/ignored); memd Python with available local model
  assets **44/0/2** (pass/fail/skip); canonical delegation/memory **65/0/2**; canonical
  config/learning/prompt/provider **128/0/1**. Total **275 passed, 0 failed, 5 skipped**.
  Prime tests are counted inside the memd suite. See `TEST_REPORT.md` for exact commands.
- A separate eight-file delegation-boundary run printed 118 passing tests and no test
  failures, but the canonical wrapper timed out two files at 300 seconds after pytest printed
  all 15 tests passing. The run is non-green and is excluded from the total above.
- The canonical test runner initially failed because the reduced memd venv lacked Hermes
  runtime packages. Building the isolated dev/test environment through PM fixed those test
  imports; the successful suites then completed.
- `hermes --help`, `hermes --version`, and interactive `hermes model` were exercised through
  the fresh entrypoint; the picker exited via `Leave unchanged` / `No change.` The installed
  runtime still emits PM's repair retry warning. The recorded AppContainer entrypoint also
  has stale copied source, and `hermes pm doctor` could not provision pinned `uv` with about
  21 MB free on C:. The installed runtime is not considered restored.
- AppContainer OAuth is present and resolves to `openai-codex` / `gpt-6-luna`. The historical
  live parent-child run passed six synchronous stages, but its verifier copied `auth.json`
  into a disposable profile, contrary to the no-copy constraint. No secret was printed; the
  source file was not changed and the disposable profile was removed. The verifier now
  requires an isolated `harmes-harness` profile with its own Hermes login and never copies or
  imports credentials. A compliant rerun and genuine async validation remain blocked until
  that profile is authenticated.

## E: runtime continuation (2026-11-26)

- Created an isolated runtime under `memd/.tmp-build/harness-runtime-e`; no global
  environment settings, existing profiles, environments, locks, or user data were changed.
  The process used E: for `TEMP`/`TMP`/`TMPDIR`, `UV_CACHE_DIR`,
  `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`, `CARGO_TARGET_DIR`, `HERMES_HOME`,
  `HERMES_RUNTIME_DIR`, PM generations, managed tools, and the application venv.
- The reported missing lock was a PM root-selection/ownership mismatch, not a missing
  tracked lockfile. `uv.lock` and `pm/lock.json` remain present. With
  `HERMES_HOME=<E runtime>\profiles\harmes-harness`, PM resolves dependency state beneath
  the E runtime root; `HERMES_RUNTIME_DIR=<E runtime>\tools` places the package store there.
- `C:\Python315\python.exe -m pm.cli install --trust-recorded` completed with PM receipt
  `outcome: ok`, `exit_code: 0`, and `venv_rebuild.ok: true`. `pm doctor` passed every
  installed package, including pinned `uv 0.12.3`; platform-inapplicable packages were
  skipped and optional `bws`, `gh`, `git`, and local llama.cpp packages were absent.
- From the E: venv, `hermes --help`, `hermes --version`, and interactive `hermes model`
  were exercised. `hermes --version` reports install directory
  `E:\AI_Project_Sem3\harmes-fork`; `hermes_cli.__file__` resolves to that checkout.
  The model picker exited with `Leave unchanged` / `No change.` A fixture-backed
  `AIAgent` object construction still fails when its nested PM Python launch returns
  `Access is denied`; full agent initialization therefore remains unverified.
- The isolated E: profile reports `openai-codex: logged out`. No credentials were copied.
  Live memory, review, Prime, and parent/child model runs require the profile's own OAuth
  login. The exact command is in `REMAINING_BLOCKERS.md` and `TEST_REPORT.md`.
- The prior eight-file delegation boundary run is now green on a fresh canonical rerun:
  **118 passed, 0 failed, 0 skipped** in 117.2 seconds. The previously timed-out
  `test_delegate_depth_prompt.py` (33.0s) and `test_delegate_failure_copy.py` (8.4s)
  both completed normally. Including the earlier distinct suites, total deterministic
  coverage is **393 passed, 0 failed, 5 skipped**; no overlapping files were counted twice.
- Remaining live blockers are isolated-profile OAuth and the associated real-model
  validations. AVO, benchmarks, and efficiency optimization remain deferred.

## Codex OAuth profile persistence fix (2026-11-27)

- Reproduced the reported sign-in/status mismatch: the profile-scoped device flow printed
  `Added ... OAuth credential`, while the selected E: profile auth store remained empty and
  status said logged out. The runtime Python import and `_auth_file_path()` both resolve to
  the checkout and intended profile store.
- Root cause: when a named profile had no local row for a single-use OAuth provider, adding
  its first credential went through the root's update-only write-through path. That path
  correctly avoids copying a borrowed root grant, but cannot insert a new profile-owned row.
  The add path now writes new profile-owned credentials locally and continues omitting any
  borrowed root rows.
- Canonical regression run `scripts/run_tests.sh tests/hermes_cli/test_auth_profile_fallback.py -q`:
  **12 passed, 0 failed**. The isolated E: profile still needs a fresh OAuth login after the
  fix before real-model validations can proceed.

## Live validation continuation (2026-11-27)

- The named-profile OAuth write-through defect is fixed. The isolated E: profile now reports
  `openai-codex: logged in`; model configuration is `openai-codex` / `gpt-6-luna`. The E:
  venv imports `hermes_cli` from `E:\AI_Project_Sem3\harmes-fork`, and a real Hermes task
  returned the expected exact response.
- Real Hermes memory was written with the Rust provider, retrieved in a new session, and
  retrieved again after restarting the isolated daemon. A real native background memory
  review completed without error, reported a memory update, and its synthetic fact was
  retrieved by a fresh real Hermes session.
- The corrected synchronous parent-child verifier completed all six functional stages with
  the isolated profile and cleaned its synthetic namespace. A separate genuine async run
  observed child `RUNNING`, parent completion, child `SUCCEEDED` with authorized Rust memory,
  result delivery, and parent processing in a later turn.
- Real Prime TaskHarness executions persisted objective-success experiences. The configured
  reviewer route resolved to `openai-codex` / `gpt-6-luna` and returned a legitimate
  no-refinement decision on two eligible controlled experiences. No candidate was produced,
  so no live candidate evaluation, activation, restart injection, or rollback is claimed.
  Deterministic tests cover acceptance, artifact persistence, frozen guidance, and rollback.
- A command-name collision was fixed: the Prime review command now registers as
  `/memd-learn`; Hermes's built-in `/learn` remains untouched. Runtime registration was
  verified in the E: profile.
- A selected 17-file canonical functional suite finished with **234 passed, 0 failed,
  3 skipped** after rerunning the lifecycle file whose test stub required the scheduler's
  session context. The memd Python suite passed **46, 0 failed, 2 skipped**; the Rust
  functional suite passed **38, 0 failed, 0 ignored**, including local MiniLM retrieval.
  The updated totals are recorded in `TEST_REPORT.md`.
- The remaining live evidence gap is real candidate generation/evaluation and live accepted
  guidance across restart/rollback. The live reviewer made no candidate, so this is reported
  as unverified rather than substituting deterministic acceptance for model evidence.

## Scope boundary

No benchmark or optimization work was performed. Existing benchmark files, results, dirty
worktree content, user profiles, and environments were preserved.
