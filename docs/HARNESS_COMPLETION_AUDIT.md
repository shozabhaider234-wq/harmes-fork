# Harness completion audit

Audit began 2026-11-25 from checkout `5a4880a14d` on branch
`codex/b2-memory-subagent`. The checkout has extensive uncommitted user work; it is
preserved. The MCP code graph was unavailable in this session, so code references
were found with repository search and checked in source. This is an implementation
status audit, not a fresh test receipt.

## Scope adjustment

Memory benchmarks, quality metrics (including Recall@k/MRR), storage and retrieval
performance, RAM/token comparisons, core-profile comparisons, scalability experiments,
and memory ablations are deferred by the user's updated scope. Existing benchmark
scripts, result directories, and reports are preserved and were not run or extended.
Controlled deterministic fixtures remain in scope for correctness and Prime candidate
accept/reject decisions.

## Status by subsystem

| Subsystem | Audit classification | Evidence and remaining work |
|---|---|---|
| Rust memory engine and daemon | Implemented; previously tested on Windows GNU | SQLite/FTS/vector storage, protocol, migrations, daemon and Python client coverage are described in [memd/VALIDATION.md](../memd/VALIDATION.md). The recorded suites are historical; this task has not rerun the Rust workspace. MSVC and native Unix certification remain unavailable in the recorded environment. |
| Hermes memory integration | Implemented; partially integration-tested | The opt-in provider and native background-review routing are described in [memd/INTEGRATION.md](../memd/INTEGRATION.md) and [memd/NATIVE_REVIEW.md](../memd/NATIVE_REVIEW.md). A new-session live persistence pass is not part of this audit. |
| Persistent memory across restarts | Implemented; unit/integration evidence exists | Rust SQLite persistence and daemon restart checks are recorded in `memd/VALIDATION.md`; real normal-session restart evidence should be refreshed after the current working-tree changes. |
| Memory isolation and parent-child access | Implemented; partially verified | Profile hashing, namespace filtering, and child read-only routing have source/tests in `memd/clients/python/hermes_bridge.py`, `tests/tools/test_delegate_memd_access.py`, and `tests/tools/test_memd_subagent_readonly.py`. The recorded live run is synchronous and documents a cleanup failure plus unverified asynchronous delivery in `memd/PARENT_CHILD_E2E_RESULT.md`. |
| Delegation | Hermes sync/async mechanisms implemented; this harness's complete lifecycle is not proven | Existing sync/async tests live under `tests/tools/`, `tests/gateway/`, `tests/hermes_cli/`, and `tests/tui_gateway/`. The memd parent-child live receipt did not exercise background delivery or a subsequent parent turn. |
| Context formation | Implemented; current branch work remains unverified here | `memd/clients/python/context_builder.py`, `initial_context.py`, and `core_profile.py` provide the implementation. Several are uncommitted changes and corresponding tests exist in `tests/agent/`; they must be preserved and revalidated. No further optimization is in scope. |
| Native background review | Implemented with durable scheduling; partially verified | Detailed lifecycle and scheduling evidence is in `memd/NATIVE_REVIEW.md`; current behavior should be tested against the changed tree. |
| Prime experience/artifact persistence | Partially implemented and tested | `memd/clients/python/prime_learning.py` persists experiences and candidates, evaluates paired fixture scores, rejects regressions, activates accepted versions, and supports rollback. `memd/clients/python/task_harness.py` captures callback execution, trajectory/tool references, outcome and applied artifact versions. Nine tests in `memd/tests/test_prime_learning.py` pass. The harness is not yet attached to normal Hermes session configuration or automatically invoked after completed Hermes turns. |
| Prime candidate generation/evaluation | Partial | Candidate generation is an injected callable and evaluation is a controlled evaluator callback. The store rejects unknown outcomes and validates structured candidate shape. A real reviewer call, isolated execution of baseline/candidate guidance by the Hermes model, validation-set configuration, and actual Hermes outcome capture remain to be integrated. |
| Learning-provider abstraction | Partial | The Prime module exposes `LearningProvider`, `none`, and `prime`; unknown names including `avo` fail clearly. There is not yet a Hermes-owned configuration path or future-provider registration seam in the task execution harness. No AVO implementation exists. |
| Unified task execution harness | Partially implemented | `memd/clients/python/task_harness.py` provides a provider-neutral execution callback with scoped active guidance and experience recording. It is a functional component but is not yet wired into Hermes' task/session lifecycle. Benchmark adapters and scored tasks are deferred. |
| Logging, recovery, and reproducibility | Partial | Daemon logging, plugin errors, review job persistence and experiment/benchmark artifacts exist in their respective subsystems. A unified non-benchmark run record and end-to-end recovery report are not yet wired. |
| Documentation | Partial | Memory-specific docs contain implementation details and historical receipts. The audit and rolling progress document are being added; Prime/runtime usage docs and a current consolidated functional test report remain. |

## User-state and evidence limits

- The existing worktree contains user changes in agent, context, tests, and docs; none
  were reset or reverted.
- This audit does not claim current-tree pass status from historical receipts.
- No memory benchmark, benchmark adapter, benchmark score, or performance experiment
  was executed or changed.
- No real-model result is claimed in this audit.
- Current-tree verification run: Rust Windows GNU workspace tests, 38 passed; Python
  `memd/tests`, 32 passed and 2 native-Unix tests skipped. The canonical Hermes suite
  could not start because Bash cannot parse the CRLF `activate` script.
