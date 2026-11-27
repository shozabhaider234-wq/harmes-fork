# Remaining harness blockers

Updated 2026-11-27. Benchmarking and optimization are explicitly deferred. The only
remaining live evidence gap is actual Prime candidate evaluation and accepted-guidance
restart/rollback; see the current status below.

## Current E: runtime and live-validation status

- Runtime, PM provisioning, and authentication are restored on E:. The process-local
  runtime is `memd/.tmp-build/harness-runtime-e`; caches, temp files, PM generations, tools,
  and builds stay on E:. Tracked `uv.lock` and `pm/lock.json` are present. Supported
  `pm install --trust-recorded` and `pm doctor` passed, including pinned `uv 0.12.3`.
- The PM venv reports the source and `hermes_cli` import under
  `E:\AI_Project_Sem3\harmes-fork`. `hermes --help`, `hermes --version`, `hermes model`,
  basic agent initialization, and a real one-shot `gpt-6-luna` task succeeded. The profile
  uses Hermes-owned OAuth and now reports `openai-codex: logged in`.
- The auth issue was the first single-use Codex credential in a named profile taking an
  update-only write-through path that could not insert a local row. The profile-owned write
  path is fixed; regression test: **12 passed**. No credentials were copied between profiles.
- Live Rust-memory checks passed across sessions and daemon restart. Native background memory
  review completed through the resolved `openai-codex` / `gpt-6-luna` route and a fresh Hermes
  session retrieved its synthetic write. Corrected synchronous parent-child verification
  passed six stages and cleanup. Genuine async delegation passed: child ran in background,
  parent continued, child read authorized Rust memory, completed, and the parent handled its
  result in a later turn.
- Real Prime tasks persisted objective-success experiences. The auxiliary reviewer resolved
  to `openai-codex` / `gpt-6-luna` and returned a legitimate no-refinement decision. No
  candidate was produced, so live paired baseline/candidate execution and live guidance
  activation across restart/rollback remain unverified. Deterministic acceptance, persistence,
  injection, and rollback fixtures pass.
- Latest functional grouping: Rust **38/0/0**, memd Python **46/0/2**, canonical Hermes
  **234/0/3** (passed/failed/skipped), totaling **318/0/5** without overlapping files in
  this grouping. The previously timed-out delegation files were rerun with the canonical
  runner and passed **118/0/0** across eight files disjoint from this grouping; the combined
  unique functional total is **436/0/5**. Older 275/393 totals are not added because their
  selections overlap current receipts.
- A real command conflict was fixed: Prime controls now register as `/memd-learn`, while
  Hermes's built-in `/learn` remains unchanged. Runtime registration was verified.

The remaining live evidence gap is a real Prime candidate. The reviewer did not propose one,
so the harness is not declared complete for live learning activation/rollback claims. All
memory, runtime, OAuth, synchronous delegation, asynchronous delegation, and deterministic
learning validation blockers listed in the old snapshots below have been resolved.

## Historical runtime and authentication note

The observations below describe the old C:/AppContainer repair attempts and are superseded
for the isolated E: runtime by the current status above. The old installation was preserved.

- Tracked `uv.lock` and `pm/lock.json` exist; Git history shows no deletion of either lock.
  PM facts in the normal Hermes home resolve through links into the Codex AppContainer
  install. PM rejects that generation because it resolves outside the checkout's install
  root. Its earlier `recorded dependency lock is missing` message is the foreign-generation
  safety refusal: the linked `workspace/uv.lock` exists, but is outside the owning install.
- `pm install --trust-recorded` rebuilt the locked dependency generation but left the linked
  root mismatch. Calling the public `hermes pm repair` path would also run generation cleanup;
  it was not used after discovering the links because existing environments must be preserved.
- With the AppContainer home selected, `hermes --help`, `hermes --version`, and `hermes model`
  run through the fresh PM-built entrypoint; the model picker was exited with `Leave unchanged`
  and confirmed `No change.` Startup still emits `automatic dependency repair retry limit
  reached`. The recorded AppContainer `hermes.exe` also imports a stale workspace copy whose `config.py`
  has an `IndentationError`; the fresh PM-built dev/test environment imports the checkout
  successfully, but does not repair the installed runtime state.
- `hermes pm doctor` was attempted from the configured profile; PM tried to prepare
  its pinned `uv` and stopped with `[Errno 28] No space left on device` while initializing
  `C:\Users\PMYLS\AppData\Local\uv\cache`. The observed C: free space was about 21 MB. The
  new isolated test environment resides under the E: workspace and was not an existing
  environment; no existing environment was deleted.
- In the AppContainer Hermes home, `hermes auth status openai-codex` reports logged in and
  the configured provider/model are `openai-codex` / `gpt-6-luna`. The earlier missing-token
  result came from inspecting the normal Hermes home while the active Codex OAuth state lives
  under the AppContainer home. No credential value was displayed or substituted during this
  status check. The historical verifier's separate credential-copy behavior is disclosed
  below; a synthetic profile still needs its own login.
- The parent-child verifier now refuses cross-profile auth and requires a login owned by the
  isolated `harmes-harness` profile. Run `hermes -p harmes-harness auth add openai-codex`
  interactively; expected confirmation: `Login successful!`. The historical six-stage live
  receipt used the previous verifier behavior, which copied `auth.json` into a disposable
  profile; no secret was printed, the source file was unchanged, and that disposable profile
  was removed. That receipt is evidence of runtime functionality, but not a compliant final
  auth setup.

## Remaining live validation

- The real Prime reviewer returned no refinement after two objectively successful controlled
  experiences. No actual candidate reached paired validation. If a later review produces one,
  record its real baseline/candidate Hermes runs, decision evidence, and any restart/rollback
  behavior before claiming live Prime activation.
- Deterministic fixtures already verify candidate acceptance, activation persistence, guidance
  injection into a new session, and rollback/deactivation. Keep those receipts separate from
  model-generated improvement claims.

The previous E: OAuth, runtime, memory, background review, delegation, and timed-out-test
blockers are resolved. Detailed receipts and commands are in [TEST_REPORT.md](TEST_REPORT.md).

## Current implementation coverage

- Prime integration now has opt-in `disabled`, `manual`, `after_objective`, and
  `after_n_experiences` trigger modes. It queues only for explicitly labeled known outcomes,
  never treats normal completion as success, and persists one-shot trigger claims.
- Prime candidate validation now calls the Hermes subagent lifecycle twice per fixed task,
  without and with candidate guidance. Local objective checks are withheld from the child and
  learner input; evidence stores resolved provider/model, execution state, check results, and
  response hashes. The live reviewer returned no candidate, so actual paired Hermes candidate
  execution remains untested; deterministic tests cover the evaluator policy.
- The profile-local store, capture hook, next-session guidance, activation, and rollback have
  deterministic functional tests. Live memory restart behavior passed; live accepted-guidance
  injection and rollback remain unverified because this run produced no candidate.
- The current canonical selection covers 17 files and finishes with 234 passed, 0 failed,
  3 skipped after rerunning the corrected lifecycle test. Rust functional tests pass 38/38 on
  Windows with the local MiniLM model; native MSVC and Unix Rust suites have not been run.

## Deferred by scope

- No memory/general benchmarks, retrieval metrics, latency/storage/RAM/token comparisons,
  scalability work, or ablations.
- No AVO implementation and no token/RAM-efficiency optimization.
