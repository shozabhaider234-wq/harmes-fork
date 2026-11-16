# Native background review integration

## Audit (this fork, before implementation)

- `agent/turn_context.py::_tick_memory_nudge` runs in turn preparation, once per
  user turn. It increments only with interval > 0, `memory` in valid tool names,
  and a non-null native store. At >= interval it resets to zero and returns true
  (`_should_review_memory`). Init reads `memory.nudge_interval`, default 10.
  Restored history hydrates a zero counter with prior user turns modulo interval.
- `agent/tool_executor.py` resets the counter when an admitted `memory` call
  starts, before execution, including failed/staged operations. Blocked dispatch
  does not reset it. The external `memd_memory` tool does not reset it.
- Finalization requires a final response, no interruption, no
  `skip_background_review`, and memory or skill review due. Counter reset happens
  at the threshold even if finalization suppresses spawning. Delegate-depth > 0
  and disabled `auxiliary.background_review.enabled` suppress ordinary reviews.
  `/refine` focus/explicit handling has its own existing gates; explicit avoids
  idle deferral, not all eligibility gates. No `agent/review_signals.py` exists.
- `run_agent.py::_spawn_background_review` structurally clones nested history.
  Managed-local automatic reviews can defer to the idle queue. The immediate path
  installs a unique cancellation token and starts a profile-context-propagating
  daemon thread. A new live turn cancels/preempts review, with a bounded 2-second
  acknowledgement wait; local deferred reviews can requeue up to three times.
- `agent/background_review.py` forks AIAgent with inherited live runtime and
  credentials (including OAuth), detached transcript persistence, and shared
  native `_memory_store`. Same-model review inherits the exact cached system
  prompt, reasoning settings, tool schemas and cache scope. It receives the full
  cloned conversation, including tool calls/results, not ten turns or 24 messages.
- Different-model routing reuses `_digest_history`: retain the last 24 messages
  verbatim, extending backwards if the first retained row is a tool result. Older
  users contribute up to 300 characters each, assistant text up to 200, assistant
  tool names are retained, older tool outputs omitted. One synthetic user digest
  precedes the tail. This is deterministic, with no summarizer call.
- Existing sanitization and detached in-place compaction remain active. First
  request compaction is deferred for warm cache reuse. Review has 16 iterations
  and the existing configurable aggregate input-token budget (context-derived
  default capped at 600k). No additional extraction/refinement model is needed.
- Existing memory/combined prompts choose USER for persona/preferences and MEMORY
  for environment/project facts, one target per fact, and permit nothing-to-save.
  The model chooses useful facts; native tool decisions are add, whole-entry
  replace, remove, or atomic batch. There is no separate classification algorithm.
- Dispatch whitelist includes memory only for memory-triggered reviews with
  enabled stores, skills and read-only file inspection; configured extra tools
  must already be advertised. Unattended destructive memory operations are staged
  for human approval; explicit attended review retains native operation access.
- `tools/memory_tool_store.py` parses section-sign-delimited Markdown, locks and
  rereads before mutation, validates threats and budgets, matches exact entries
  before unique substrings, and atomically writes files. Prompt snapshots freeze
  at load. `tools/memory_tool.py` owns approval, schemas and result contracts.
  `load_on_disk_store` supplies CLI/gateway/onboarding and pending-write consumers.
- External MemoryProvider lifecycle alone cannot replace native persistence:
  providers run alongside the native store, and the review fork skips their
  initialization. A post-write mirror creates two authorities and hides failures.
  The Rust `remember` action is actually `memd_memory(action="remember")`, not a
  standalone tool. It is not executable by the default reviewer.
- Team A already implements SqliteStore, SQLite/vec0/FTS5, transactional CRUD,
  provenance, profile-derived namespaces, dedup and restart tests. MockEngine is
  opt-in, not production. Existing v1 RPC already has store/get/update/delete and
  scoped retrieval. Missing for native semantics: complete scoped inventory and
  atomic compare-and-swap of a batch. Top-k search cannot supply an inventory.

## Integration plan

Expose an optional native-store factory on MemoryProvider and select it through
the common native-store constructor. Reuse native MemoryStore validation,
matching, budgets, result formatting and frozen snapshot; replace only load and
mutation persistence. Add v1 inventory and conditional batch commit backed by a
single Rust transaction. Preserve IDs/timestamps on edits and source metadata.
Route explicit remember through this same store; context recall remains a separate
budgeted tool operation. Never silently fall back to Markdown on daemon failure.

The root README is a proposal and contradicts the completed persistent backend
documented in TEAM_A_README.md. The latter also describes autonomous extraction as
future work. This integration supersedes that statement using native review,
without adopting the root proposal's speculative custom extraction triggers.

## Setup and migration

1. Install the `memd/clients/python` directory plugin into
   `<active HERMES_HOME>/plugins/hermes-memd/`.
2. From the Hermes checkout, run `hermes memory setup` and select `hermes-memd`.
   Setup builds the local daemon if needed, installs its model/runtime into the
   active profile, writes the default endpoint and selects the provider. On the
   next Hermes session, the provider starts the daemon in the background. Later
   sessions need no separate daemon window or endpoint entry. Keep
   `memory.memory_enabled` / `memory.user_profile_enabled` enabled as desired.
   No API key or paid service is required; existing model/OAuth routing is unchanged.
3. Close active sessions before switching persistence. Back up config.yaml,
   memd.json, the original memories directory and the daemon DB with SQLite's
   backup mechanism or with the daemon stopped (do not copy a live DB without WAL).
   Never point memd at Hermes state.db.
4. Import explicitly from the checkout with the intended profile active:
   `python -m memd.clients.python.native_migration import /path/to/original/memories`.
   It strictly parses UTF-8/BOM and section-sign delimiters, rejects content that
   would not round-trip, scans threats, and enforces native character budgets.
   Both targets are validated first; each target commits atomically. A concurrent
   failure on the second target can leave the first imported: the error says so,
   and retrying is idempotent. Originals are never modified or deleted.
5. Start a new session. General/project records use the existing namespace
   `<configured namespace>:<profile hash>`; user records add `:user`. Each record
   uses existing IDs, category, content, timestamps, ownership and provenance
   fields; `metadata.hermes_target` records the native target. Existing explicit
   records remain general memories; no automatic category inference moves them.

Rollback: stop sessions, export current Rust memory to a **new** directory with
`python -m memd.clients.python.native_migration export /path/to/new/export`, verify
the entries, then select `memory.provider: builtin` for the next session. Keep the
Rust DB and original backups. Deliberately copy the validated export into the
profile's memories directory only when ready to make Markdown authoritative again.
Switching directly without export restores the old Markdown snapshot and omits
later Rust changes. Export contains native curated text, not all structured
metadata; keep the DB backup for full-fidelity recovery.

Native character limits also apply to explicit remember so the same curated
inventory remains usable by both paths. Explicit remember accepts `target="user"`
for persona/preferences and `target="memory"` for environment/project facts (the
backward-compatible default). Use the same target for native and explicit saves:
exact repeated content returns its existing ID without a second write. Facts are
not classified or moved between targets by an extra model or heuristic.
Case/whitespace-normalized collisions
follow the Rust engine's existing conservative dedup policy; colliding updates
fail visibly rather than silently inserting or overwriting another ID.

## Boundaries and reliability

Native tool operation results, matching, threat scans, budgets, approvals and
consolidation failure limits are inherited from MemoryStore. Only persistence is
replaced. Conditional commits return an explicit conflict rather than adding a new
automatic extraction retry policy. Native scheduling/cancellation/requeue behavior
is untouched. Pending approvals still live in the existing approval store.

Prompt memory remains a frozen load-time snapshot; new memories do not mutate an
active conversation's prefix. A new session reads Rust. Explicit recall still
uses ContextBuilder budgets and provenance; neither extraction nor sync_turn
injects an extra context block. USER and general retrieval each query their scoped
namespace, then the existing ContextBuilder selects the bounded result.

Journey, CLI/dashboard reset, onboarding, native tool dispatch and pending approval
read through the common factory. Markdown imports, file-oriented profile cloning
and generic filesystem backups remain file-oriented administrative workflows;
they do not clone or back up the external daemon DB. Use explicit export/import
and DB backup when transferring profiles. A profile clone gets a distinct hashed
namespace. Preserved Markdown is a rollback artifact, never a runtime mirror.

The local daemon transport is the trust boundary; namespace filters do not replace
authentication. Native inventory is intentionally for curated, character-bounded
memory, not a large transcript database. No Unix/MSVC production certification or
extraction token savings are implied by Windows GNU tests.

## Validation and measurements (2026-11-16)

Executed on Windows x64/GNU, using the existing offline ONNX artifacts and
Hermes PM's isolated Python 3.14.7 dev/test environment. Commands below ran
through `rtk proxy`; Python tests used `scripts/run_tests.sh`, never bare pytest.
The test runner strips MEMD_BIN, so the freshly built daemon was copied to its
default `memd/target/debug/memd.exe` test location (build artifacts only).

- `powershell -NoProfile -ExecutionPolicy Bypass -File memd/scripts/windows.ps1 -Action test`:
  **37 Rust tests passed**, including offline real-model tests and two new native
  inventory/atomicity/concurrency tests. No mocks replace SQLite/vec0.
- `scripts/run_tests.sh memd/tests/test_native_review.py memd/tests/test_persistent_client.py tests/agent/test_skip_memory_store.py -j 1 --tb=short`:
  **10 passed, 7 skipped**. The four new parameter-expanded tests exercise full
  foreground turns, native review fork/tool dispatch with a deterministic fake
  model, Rust writes, save-nothing, history beyond 10 turns, native counter versus
  explicit-tool resets, stable IDs, restart, failures, explicit migration/export,
  rollback parsing, Journey mutations, reset and preserved original Markdown.
  The new test file was rerun after the counter assertions: **4 passed**.
- Existing reviewer/cache-parity/digest, inline alias, Journey and write-bridge
  files: **67 passed** across seven files. Additional memory scope, toolset,
  disabled-target and dashboard user-provider files passed (**37 tests**).
- The broader nine-file run was **not green**: 127 assertions passed, but two
  unchanged Markdown-lock POSIX-mode assertions fail on Windows (0666 versus
  0600), and `test_skip_background_review.py` reported three passed before its
  process exceeded the runner timeout. An earlier parallel collection MemoryError
  disappeared on the single-worker rerun. No claim of a green full suite is made.
- After fixing duplicate provider loading, this compatibility run was green:
  `scripts/run_tests.sh memd/tests/test_native_review.py tests/agent/test_memory_provider_init.py tests/agent/test_skip_memory_store.py tests/hermes_cli/test_memory_status_env_hint.py -j 1 --tb=short`:
  **13 passed**. The agent reuses the existing manager/provider without another
  load or initialize call. The native adapter was retested through real fake-model
  foreground/review loops after moving store construction behind provider setup.
- Final Rust-daemon/provider run:
  `scripts/run_tests.sh memd/tests/test_native_review.py memd/tests/test_hermes_bridge.py -j 1 --tb=short`:
  **5 passed**, including same-target explicit/native user-memory deduplication,
  structured malformed-request errors and recall across the two native targets.

To prepare this test interpreter, use Hermes PM's
`python -m pm.build_env --source . --out memd/.tmp-build/native-review-tests --group dev --group test`
with isolated HERMES_HOME/HERMES_RUNTIME_DIR as in the repository development
guide. In Git Bash on Windows, set `PATH=/usr/bin:/bin:$PATH` and
`HERMES_PYTHON=$PWD/memd/.tmp-build/native-review-tests/Scripts/python.exe` before
the canonical wrapper. Unix environments use their corresponding `bin/python`.

Storage-only benchmark:
`python memd/scripts/benchmark_native_store.py --binary memd/target-gnu/debug/memd.exe`.
Raw receipt: `memd/benchmark-results/native-20261116T070835Z-9a6efc1f/report.json`.
Ten synthetic 128-dimensional curated records, debug binary, loopback TCP, shared
machine: native add median/p95 **28.0/80.9 ms**; inventory RPC **3.33/5.15 ms**;
hybrid retrieval RPC **12.81/16.95 ms**; ping **0.63/1.10 ms**. Ten memory writes,
sampled peak RSS **15,613,952 bytes**, live DB+WAL+SHM **1,165,776 bytes**;
SQLite allocated pages **614,400 bytes**. The main DB alone remained 4096 bytes
after forced process termination: committed data also resides in the retained WAL,
so that main-file size is not the total persistent footprint.

No extraction model was invoked by that benchmark. The deterministic review test
starts one review at the threshold and makes two fake model requests when saving
(tool call + continuation), one when saving nothing. Real input/cached/uncached
token measurements are unavailable. Native `_snapshot_review_usage`,
`_record_review_usage_to_parent` and `_log_review_completion` remain the observation
points for real-model invocation/input/cache accounting; no second LLM call is
introduced for optimization. These measurements establish neither token savings
nor a speedup over Markdown.

## Files changed for this integration

Existing uncommitted Team A implementation was preserved; this list is the
integration's edits/additions, not the entire working-tree diff.

| Area | Files |
| --- | --- |
| Native adapter, migration | `memd/clients/python/native_store.py` (new), `native_migration.py` (new), `hermes_bridge.py`, `memory_client.py` |
| Rust engine/protocol | `memd/crates/memcore/src/sqlite.rs`, `memd/crates/memcore/tests/sqlite_storage.rs`, `memd/crates/memd/src/lib.rs`, `memd/protocol.md` |
| Provider integration | `agent/memory_provider.py`, `agent/agent_init.py`, `agent/inline_tool_executors.py`, `tools/memory_tool.py`, `tools/memory_tool_store.py` |
| Existing consumers | `agent/learning_graph.py`, `agent/learning_mutations.py` (Journey memory views only), `hermes_cli/main_agent_cmds.py`, `hermes_cli/web_routers/ops.py`, `hermes_cli/doctor_state.py`, `hermes_cli/memory_setup.py` |
| Verification | `memd/tests/test_native_review.py` (new), `memd/scripts/benchmark_native_store.py` (new) |
| Documentation | `README.md`, `TEAM_A_README.md`, `memd/README.md`, `memd/INTEGRATION.md`, `memd/VALIDATION.md`, this audit/setup/receipt (new) |

No Team B or Prime learning implementation, raw transcript persistence,
background-review prompts/scheduler, model selection or authentication code changed.
