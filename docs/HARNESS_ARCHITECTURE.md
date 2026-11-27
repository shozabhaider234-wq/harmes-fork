# Harness architecture status

This document records the implemented runtime paths and the validation still outstanding.
The harness is not declared complete while real-model candidate activation has no live
evidence.

## Memory path

Hermes can select the opt-in `hermes-memd` MemoryProvider. Its Python bridge manages the
Rust daemon client, derives profile-specific namespaces, keeps child access read-only, and
routes explicit memory and source/history operations. Rust owns SQLite persistence,
vector/keyword/hybrid search, filtering, and transactions. The existing Hermes session
database remains the transcript authority. The integration and limitations are documented
in [memd/INTEGRATION.md](../memd/INTEGRATION.md).

ContextBuilder formats retrieved records with provenance and bounded output. The optional
core profile is an existing experimental prompt path. This scope preserves those safeguards
and adds no retrieval or token optimization.

## Learning path currently wired

`TaskHarness` receives a task, an execution callback, and an optional objective evaluator.
Its `run_hermes` entry point constructs a fresh `AIAgent` with current profile routing,
injects active procedural artifacts through the construction-time ephemeral prompt, and
records trajectory/tool/memory references, safe model-routing fields, the outcome (or
unknown), and applied artifact versions through `LearningProvider`. The lower-level callback
entry point remains for controlled executor integrations. Hermes turn finalization captures
normal foreground turn metadata through the memd plugin.

`PrimeLearner` stores experiences, proposals, validation evidence, and versioned artifacts in
a separate profile-local JSON store. Updates are atomic and serialized across processes.
The registered auxiliary reviewer generates a procedural candidate. Validation invokes
actual Hermes leaf subagents with baseline/candidate guidance and local deterministic checks;
it never uses an LLM judge to score whether a candidate helped. A candidate activates only
after no task regresses and objective outcomes improve. A changed baseline prevents
activation. Failed/rejected candidates stay inactive. Accepted guidance is rendered once per
new session and can be rolled back or deactivated. Automatic triggers are opt-in and require
known outcomes; stored claims prevent duplicate queued jobs.

The isolated E: runtime passes PM install and doctor and imports the current checkout. Its
profile now reports `openai-codex: logged in` and runs real `gpt-6-luna` Hermes sessions.
Live tests verified Rust memory across sessions and a daemon restart, native background
memory review with persisted retrieval, the synchronous parent-child path, and genuine
asynchronous child execution with later parent processing. The Prime auxiliary reviewer ran
through the real route and returned no refinement. Therefore real candidate baseline/candidate
execution, guidance activation after restart, and live rollback remain unverified; deterministic
tests cover activation and rollback. See [remaining blockers](REMAINING_BLOCKERS.md).

## Boundaries

- Learning artifacts are procedural instructions, separate from semantic/episodic memory.
- The learner cannot modify source, credentials, or system/developer instructions.
- Objective outcomes are supplied by a task evaluator. Fluency/completion is not treated as
  proof of success.
- The validation API accepts fixed local objective checks; the Hermes integration executes
  actual leaf subagents for baseline/candidate runs. Deterministic tests fake that lifecycle
  boundary; no live-model learning improvement is claimed.
- `none` performs no learning persistence. `prime` must be explicitly constructed with a
  store path. No AVO provider is registered.
- Benchmarking, benchmark adapters, and performance/quality experiments are deferred by the
  updated scope.
