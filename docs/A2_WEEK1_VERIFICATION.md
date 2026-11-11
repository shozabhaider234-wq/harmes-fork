# Team A2: Week 1 source verification

## Hermes memory integration

- `agent/memory_provider.py` defines the `MemoryProvider` ABC. The provider owns its backend-specific behavior; the interface includes `initialize`, `prefetch`, `sync_turn`, tool schemas/dispatch, lifecycle hooks, and `shutdown`.
- `agent/memory_manager.py` registers the built-in provider and at most one external provider. It fans out recall, completed-turn sync, tool calls, session-boundary hooks, and shutdown. External turn sync is queued on a serialized background worker.
- `plugins/memory/__init__.py` discovers bundled, user, project-opt-in, and Python entry-point providers. `memory.provider` selects one external plugin. Bundled providers take precedence over a same-named user/project provider.
- `agent/agent_init.py::_init_memory` initializes the built-in file store and external provider independently. The built-in store can coexist with one external provider; setting `skip_memory` skips provider initialization.
- `tools/memory_tool.py` describes built-in `MEMORY.md` and `USER.md` as persistent, curated notes loaded at session start. Hermes conversation transcripts are separately persisted in the session database; provider `sync_turn` may also send turns to a provider, so backend retention varies.

## Review/nudge trigger

- `agent/turn_context.py::_tick_memory_nudge` advances a configurable turn counter (default 10) only when the built-in memory store is active and the `memory` tool is available.
- `agent/turn_finalizer.py` starts background review only after a completed response when the memory-review or skill-review trigger fired. `agent/background_review.py` runs the review on a separate agent fork, limited to the requested review work.
- The Codex app-server path mirrors this in `agent/codex_runtime.py::_finish_codex_turn`.

## Subagent process model

- `tools/delegate_tool.py` creates a separate `AIAgent` object for each child and gives it a dedicated child `SessionDB` handle.
- This is an in-process subagent, not a new OS process. The child is initialized with `platform="subagent"` and `skip_memory=True`; memory writes are intentionally disabled for child sessions.

## Baseline and implementation status

- Baseline is already pinned at `f42f579cf8bac4918ac9599bece71618afadd846` (`baseline-hermes`). `docs/BASELINE.md` records the untouched checkout at `../hermes-baseline` and verifies the matching commit.
- The Rust workspace and `MockEngine` under `memd/` are the initial A2 scaffold. The engine contract remains subject to A1's final `memcore` entry and hit types.
- Week 2 protocol and measurement details are recorded in `memd/protocol.md`; the Python client and measurement script live under `memd/clients/python/` and `memd/scripts/`.
