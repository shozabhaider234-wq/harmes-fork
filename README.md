# Team A — Shared Rust Memory, Agent-Directed Context & Prime-Inspired Learning

**Project:** Shared-Memory, Self-Evaluating Agent Harness (Hermes fork)  
**Owner:** Team A (A1 and A2)  
**Timeline:** 9 build weeks + 1 spare week  
**Status:** Proposed architecture and implementation plan; distinguish planned work from implemented work.

> **Architecture decision:** Hermes is the primary decision-maker for **when and what to retrieve**. It calls explicit memory tools backed by the shared Rust memory daemon. The daemon searches and returns scoped, ranked evidence; the context layer enforces budgets and progressive disclosure. A small deterministic bootstrap may load essential session/project context, but passive top-k retrieval on **every turn is not the default**. Team A also independently builds a Prime-inspired learning provider; Team B independently builds an NVIDIA AVO-inspired learning provider. Neither team's work requires the other.

## 1. Objectives and boundaries

Keep Hermes' existing agent loop, model access and tool ecosystem. Replace the memory backend with a shared, durable Rust service; expose retrieval as tools that Hermes can choose to call; return compact, provenance-bearing results; and add a separately evaluated learning/refinement provider.

Test three independent hypotheses:

1. **RAM efficiency:** sharing a daemon, indexes, and caches reduces duplicate infrastructure across concurrent agents/sessions.
2. **Token efficiency:** agent-directed retrieval plus budgeted, progressive results lowers total model tokens per completed task without harming success.
3. **Learning:** evaluated, versioned procedural refinements improve performance on repeatable tasks.

Do **not** assume any of these gains. Measure them against unmodified Hermes and alternative retrieval strategies. Count all model input/output tokens, including tool continuations, optional recursive exploration, extraction, and learning.

**In scope:** durable transcripts, structured long-term memory, scope/permissions, Rust indexing and retrieval, Python client and Hermes tools, bounded context delivery, optional recursive exploration, Prime-inspired learning, integration, measurements, rollback.

**Out of scope:** rewriting Hermes' whole Python agent loop in Rust, training model weights, Team B's AVO implementation, mandatory LLM calls to decide retrieval, and removing upstream files before migration is verified.

**Source boundary:** Prime Agent and jcode are inspirations. Agent-directed tool use and RLM-style programmatic exploration motivate this design; the exact persistent-memory retrieval policy of upstream Prime Agent has **not** been established here. The tool names, routing rules, memory schema, and learning provider below are **our proposed implementation**, not claims that Prime ships these exact features.

## 2. Team independence and ownership

| Area | Team A | Team B |
|---|---|---|
| Memory | Shared Rust memory daemon, records, indexes, transcripts | No dependency |
| Context | Hermes memory tools, bounded results, optional RLM exploration | Independent context strategy |
| Learning | Prime-inspired artifact refinement and evaluation | NVIDIA AVO-inspired variation/evaluation |
| Storage | Memory and accepted learning artifacts | Separate lineage/evaluation artifacts |
| Evaluation | Standalone baselines, token/RAM/retrieval/learning tests | Standalone AVO tests |
| Final integration | Optional `prime` learning provider | Optional `avo` learning provider |

Team A must build, run, test, and demonstrate without importing Team B modules. Provider selection (`prime` or `avo`) can be integrated later; neither provider is required to call the other.

### Member split

- **A1 — Rust engine:** durable schemas, scoped storage, transcript indexing, deduplication, search/ranking, memory lifecycle, access enforcement, performance.
- **A2 — Runtime and intelligence integration:** daemon protocol, Python client, Hermes tool registration, bounded context/progressive disclosure, optional recursive exploration, Prime-inspired learning hooks, benchmarks.

Both members jointly freeze the wire contract, evaluation fixtures, and migration plan before integration.

## 3. Target architecture

```text
 User turn
    |
    v
 Hermes agent / existing LLM call
    |  \
    |   \-- enough current context? --> continue without memory search
    |
    \-- agent chooses memory_search / session_search / memory_get
                 |
           Hermes Python tool adapter (A2)
                 |
           MemdClient: versioned JSON IPC
                 |
         Shared Rust daemon (A1/A2)
                 |
         Rust memory core (A1)
         - scope/permission filtering
         - keyword + optional vector retrieval
         - ranking, dedup, provenance
         - durable transcripts and memory
                 |
           bounded results to tool adapter
                 |
         Context delivery (A2)
         - summaries first, source references
         - token caps, truncation and validation
         - detail fetch only on demand
                 |
         Hermes LLM continuation

 Separate post-task path:
 completed task/outcome -> Prime-inspired learner -> evaluate candidate
 -> accept/reject/version/rollback -> applicable guidance for future tasks

 Team B: independent AVO-inspired learning provider (not a dependency)
```

### Responsibility separation

| Component | Responsible for | Not responsible for |
|---|---|---|
| Hermes agent | Deciding whether it needs historical information and formulating search queries | Searching databases itself |
| Rust memory engine | Enforcing scope, finding/ranking records, retrieving excerpts | Choosing the agent's next action |
| Context delivery | Enforcing response token limits, safe formatting, dedup, provenance | Automatically injecting every memory into every prompt |
| Prime-inspired learner | Proposing and validating reusable behavioral improvements | Treating every memory hit as a trusted instruction |

**Important:** tool calls may cause an additional LLM continuation. Agent-directed retrieval avoids a dedicated *pre-turn retrieval-decision LLM*, but does **not** guarantee fewer model calls. Benchmark total cost.

## 4. Memory model and isolation

A **shared daemon** means one service/index/cache infrastructure can serve multiple concurrent top-level chats and subagents. It does **not** mean all agents may read all memories.

Proposed logical records (not yet Week 2 protocol fields):

```text
MemoryEntry {
  id, namespace, agent_id?, session_id?, kind,
  text, source_ref?, created_at, updated_at,
  importance?, confidence?, tags[], embedding_ref?,
  access_scope, expires_at?
}
SessionTurn { id, session_id, turn_index, role, content_ref, timestamp, token_count? }
LearningArtifact { id, version, parent_version?, scope, body_ref, status, evidence_refs[], created_at }
ContextTrace { task_id, tool_name?, query?, candidate_ids[], selected_ids[], token_budget, token_used, reason_codes[] }
```

- **Session transcripts:** full durable source history, not automatically injected.
- **Episodic memories:** concise past tasks, outcomes, errors, resolutions.
- **Semantic memories:** stable facts, preferences, project configuration, with provenance.
- **Procedural guidance:** versioned, validated instructions/skills; distinct from untrusted retrieved text.
- **Context traces:** diagnostics on search, selection, budgets and outcomes.

Access rules: server-side enforcement of `namespace`, session/agent identity and `access_scope`; the model must not be able to escalate permissions by passing arbitrary scope strings. Use authenticated/trusted adapter metadata for identity, not unverified model arguments. Treat retrieved text as data, not higher-priority instructions. Do not mix tenants or unrelated users.

Preserve existing `MEMORY.md`/`USER.md` and session files during migration. Export/migrate validated contents, verify rollback, and only then disable duplicate active memory paths.

## 5. Primary retrieval: agent-directed tools

### 5.1 Tool contracts (proposed Hermes-facing API)

```python
memory_search(query: str, k: int = 5) -> SearchResults
session_search(query: str, k: int = 5) -> SessionResults
memory_get(memory_id: str, max_tokens: int = 1200) -> MemoryDetail
```

- `memory_search`: compact ranked episodic/semantic hits with IDs, summaries, timestamps and provenance.
- `session_search`: matching past sessions or transcript segments; returns references and short snippets, **not entire logs**.
- `memory_get`: fetch a specific authorized record or bounded source excerpt after the agent chooses it.

`k` and `max_tokens` are clamped server-side. Scope is derived from the authenticated session/agent context, not chosen freely by the model. Define stable structured errors for unavailable memory, permission denial, stale references, timeouts and over-budget requests.

### 5.2 Expected flow

1. Hermes receives the task and available recent context.
2. If history/project-specific evidence is needed, the **existing Hermes LLM** calls a memory tool.
3. The Python tool adapter uses `MemdClient` and the versioned daemon protocol.
4. Rust filters by access scope, retrieves and ranks records, and returns bounded hits.
5. The context layer sends concise results with provenance to Hermes.
6. Hermes may call `memory_get` for details, or proceed with the task.

**Do not** automatically embed, search top-k, and inject results on every turn as the default strategy. Avoid a separate LLM classifier whose sole purpose is deciding retrieval.

### 5.3 Agent guidance

Tool descriptions should instruct the agent to search when a task depends on previous sessions, decisions, project-specific facts, earlier errors or missing evidence; to avoid redundant searches when context already contains sufficient information; and to fetch detailed records only when needed. Tool descriptions are guidance, not a reliability guarantee.

### 5.4 Minimal automatic bootstrap (optional)

On new session/project activation, deterministic code may load a **small, authorized, budgeted** set of essential instructions and project metadata. Do not equate this with full memory retrieval. Evaluate bootstrap-on vs bootstrap-off. Optional invalidation on project switch or changed record versions is allowed; it should not override agent-directed search as the primary path.

### 5.5 Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Agent fails to search despite needing history | Clear tool guidance; optional tiny bootstrap; evaluate missed-memory cases |
| Agent searches repeatedly | Per-session query/result cache, duplicate-query detection, budgets |
| Search returns too much | Summary-first results, strict token cap, `memory_get` on demand |
| Search returns wrong/stale memory | Scope/freshness metadata, provenance, ranking and conflict checks |
| Agent follows malicious instructions in memory | Treat retrieved text as untrusted evidence; preserve instruction hierarchy |
| Tool round trips cost more than saved tokens | Compare total tokens, latency and task success against passive retrieval |

## 6. Context formation: bounded delivery, not default retrieval orchestration

The Prime-inspired context layer no longer decides whether to query Rust before every turn. Its primary duties are **formatting and limiting what a memory tool returns**, and optionally composing a small session bootstrap.

Context delivery steps:

1. Validate tool request and authorized scope.
2. Accept ranked records and provenance from Rust.
3. Remove duplicates, stale/incompatible records, and low-confidence evidence where appropriate.
4. Fit selected summaries/excerpts into a configurable tool-response token budget.
5. Return IDs and source references so the agent can request details.
6. Log selected/omitted records and token counts for evaluation.

Use **progressive disclosure**: first a compact search-result list, then `memory_get` for specific detail. Large transcripts should remain durable source material, not default prompt contents.

### Optional RLM-style recursive exploration

For unusually large or ambiguous sources, Hermes may invoke programmatic inspection or delegate a focused subtask, with strict limits on depth, tokens, tool calls and wall time. This is an optional escalation, **not** a recursive LLM call for every turn. Do not claim that this exact memory workflow is implemented by upstream Prime Agent without source verification.

### Illustrative search result

```json
{
  "results": [
    {"id": "mem_501", "summary": "Prior Prisma connection failure and fix", "score": 0.91, "source_ref": "session_12:turn_40"},
    {"id": "mem_502", "summary": "Neon connection configuration", "score": 0.86, "source_ref": "session_18:turn_9"}
  ],
  "truncated": false,
  "token_budget": 900
}
```

This is a **target response shape**, not the current daemon response. Scores are illustrative.

## 7. Rust backend and IPC

### Existing Week 1–2 foundations (reported, verify in repository)

- `memd` Rust workspace and provisional `MemoryEngine` trait.
- `MockEngine` for development, **not durable production memory**.
- Versioned newline-delimited JSON requests with IDs and structured errors; 1 MiB request limit.
- Concurrent Rust daemon over Unix socket; loopback TCP for Windows development.
- Python `MemdClient` supporting `ping`, `store`, `retrieve`, `search_sessions`.
- `memd/measure.py` for RPC latency, RSS and load measurements.

### Required changes

- Adapt existing `retrieve` and `search_sessions` to Hermes tool semantics; do not break existing clients.
- Add an authorized fetch-by-ID / bounded-excerpt operation for `memory_get`.
- Add identity/scope metadata via trusted adapter or authenticated connection.
- Implement durable transcript and structured memory stores, indexes and rankers.
- Add server-side caps, pagination, provenance, error handling, observability and tests.
- Version protocol additions; document compatibility and migration.

The Python tool layer is intentional: Hermes is Python-based, while Rust owns memory-heavy indexing/storage. Keep tool adapters thin; avoid replicating indexes or large transcript caches per agent.

## 8. Prime-inspired learning — independent of retrieval

Memory retrieval answers **what happened or what is known**. Learning answers **what reusable behavior should change**.

Proposed loop:

```text
Completed task + outcome + trajectory
        |
  Identify a reusable failure or improvement
        |
  Propose a small instruction/skill/procedure refinement
        |
  Check scope, conflicts, safety and provenance
        |
  Evaluate on repeatable fixtures and regression tests
        |
  Accept / reject / review
        |
  Version accepted artifact with evidence and rollback
        |
  Apply only when relevant to future tasks
```

Learning artifacts are not automatically promoted from raw memory hits. Accepted guidance must be versioned and auditable; rejected candidates remain recorded for evaluation. Define one initial artifact type and a measurable acceptance rule before implementing multiple refinement strategies.

This is **Team A's Prime-inspired learning proposal**, not a verified exact reproduction of Prime Agent's upstream learning system.

## 9. Team B's separate AVO-inspired provider

Team B develops its variation/evaluation loop, lineage and scorer independently. Team A does not depend on it. At final integration, an optional selection contract may choose `learning_provider = prime` or `learning_provider = avo`; do not require simultaneous operation or shared internal schemas.

## 10. Evaluation and ablations

Compare identical tasks, model resolution, hardware and test conditions:

| Variant | Memory behavior | Purpose |
|---|---|---|
| V0 | Unmodified Hermes | Baseline |
| V1 | Rust memory with passive top-k each turn | Retrieval-frequency baseline |
| V2 | Rust memory with rules/cache gating | Cheap-controller baseline |
| V3 | **Agent-directed memory tools** + bounded results | **Primary Team A design** |
| V4 | V3 + small automatic bootstrap | Measure missed-memory mitigation |
| V5 | V3/V4 + optional recursive exploration | Measure escalation value |
| V6 | Best retrieval variant + Prime learning | Isolate learning gains |

Metrics: completed-task success, relevant-memory recall, missed retrieval opportunities, unnecessary searches, tool calls per task, total model calls, **all input/output tokens**, tool-result tokens, cost, p50/p95 latency, daemon RSS, per-agent marginal RAM, isolation/security failures, and learning acceptance/regression rates.

Record model/provider identity at runtime. Target configuration is `openai-codex` OAuth and `gpt-6-luna` as requested; verify the resolved model rather than assuming a configured name proves usage.

## 11. Implementation schedule (9 weeks + 1 buffer)

| Week | A1 — Rust core | A2 — runtime/context/learning |
|---|---|---|
| 1 | Freeze engine interfaces, mock | Workspace and integration contract |
| 2 | Stabilize mock/protocol compatibility | Daemon, Python client, measurement harness |
| 3 | Durable transcripts, scoped records | Hermes tool registration and end-to-end smoke tests |
| 4 | Keyword search, indexing, provenance | `memory_search`, `session_search`, response budgets |
| 5 | Ranking, dedup, fetch-by-ID, permissions | `memory_get`, progressive disclosure, cache/telemetry |
| 6 | Retrieval performance and isolation tests | Context budget and retrieval ablations |
| 7 | Memory lifecycle, extraction experiments | Prime learner candidate generation and validation |
| 8 | Storage/retrieval hardening | Learning versioning, rollback, integration tests |
| 9 | Performance and reliability fixes | Full benchmarks, report, demonstration |
| 10 | Buffer | Buffer |

Weeks 1–2 represent reported foundations, not evidence that later features are already complete. Adjust schedule based on verified repository state.

## 12. Definition of done

- A new Hermes session can search and retrieve **durable**, appropriately scoped Rust memory through registered agent tools.
- Hermes can continue without retrieval when current context suffices.
- Search results are compact, attributable and budgeted; full detail requires explicit follow-up.
- Multiple sessions and parent/subagents share daemon infrastructure without crossing access boundaries.
- Context traces, all token costs, retrieval misses and daemon RAM are measurable.
- Prime-inspired learning can propose, evaluate, accept/reject, version and roll back a procedural artifact.
- Team A tests and demos work without Team B.
- A baseline comparison demonstrates tradeoffs, including cases where agent-directed retrieval is worse.
- Migration/rollback are verified before retiring upstream memory files.

## 13. Open decisions

| Decision | Owner | Status |
|---|---|---|
| Confirm exact upstream Prime Agent behavior in pinned source | A1/A2 | Not yet verified |
| Inspect Hermes tool registry and identify stable integration hook | A2 | Verify |
| Freeze authorized namespace/scope model | A1/A2 | Proposed |
| Choose SQLite/index/embedding approach from measurements | A1 | Open |
| Define tool-response token caps and result schema | A2 | Proposed |
| Define bootstrap policy and retrieval-miss fixtures | A2 | Open |
| Define one learning artifact and acceptance evaluator | A2 | Open |
| Freeze protocol extension for `memory_get` | A1/A2 | Open |
| Optional provider selection (`prime` / `avo`) | Both teams, later | Non-blocking |

**Project principle:** the LLM determines when it needs memory; Rust retrieves it efficiently; the context layer controls how much evidence reaches the LLM; learning changes validated reusable behavior. Each contribution must be independently measured.
