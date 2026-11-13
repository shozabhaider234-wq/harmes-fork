# Team A — Rust Memory, Prime-Inspired Context Formation & Learning

**Project:** Shared-Memory, Self-Evaluating Agent Harness (Hermes fork)  
**Owner:** Team A (two members: A1 and A2)  
**Duration:** 9 build weeks + 1 spare week  
**Status:** Standalone implementation plan; revise at weekly syncs

> **Finalized decision:** Team A independently builds (1) a RAM-efficient, shared Rust memory engine inspired by jcode, (2) a token-budgeted, Prime-inspired context-formation layer, and (3) a Prime-inspired learning provider. Team B independently builds an NVIDIA AVO-inspired learning provider. **Team A does not depend on Team B** for implementation, testing, evaluation, or demonstration. Final integration may expose `prime` and `avo` as alternative learning providers; they are not required to run together.

---

## 1. Thesis, objectives and scope

Keep the Hermes agent loop, model access, tools, and task execution. Replace its memory and context delivery path with a shared Rust service and a context builder that selects *what the model actually sees*. Add a separate Prime-inspired learning mechanism that refines reusable behavioral guidance based on experience, while keeping learning records distinct from ordinary task memories.

**Claims to test independently:**

1. **RAM:** A single shared Rust service reduces duplicated memory infrastructure as the number of agents grows.
2. **Tokens:** Selecting, compressing and budgeting relevant context reduces prompt tokens without harming task completion.
3. **Learning:** Versioned refinements to instructions/skills or procedural guidance improve future task performance under repeatable evaluation.

The three claims need separate baselines, measurements and ablations. None is assumed true until measured.

### In scope

- Durable session transcripts and compact long-term memory records
- Rust storage, indexing, retrieval, ranking, deduplication, lifecycle and multi-agent sharing
- Thin Python client and versioned daemon protocol
- Prime-inspired context formation: working context, relevance, priority, token budgeting, and optional recursive exploration of large retrieved material
- Prime-inspired learning: experience review, candidate refinement, validation, versioning and rollback
- Hermes integration adapters and standalone tests
- RAM, token, retrieval, latency, learning-quality and cost measurements

### Out of scope

- Team B's AVO variation agent, lineage strategy and benchmark implementation
- Porting Hermes' entire Python agent loop into Rust
- Model-weight training/fine-tuning
- Guaranteeing that recursive context exploration is cheaper than direct retrieval
- Removing upstream files before replacements are validated

**Source/inspiration boundary:** jcode and Prime Agent are architectural references, not exact implementations. Prime Agent's actual context and learning mechanisms must be verified against the pinned source before attributing any specific behavior to it. The design below is **Team A's proposed adaptation**.

---

## 2. Team boundaries and independence

| Area | Team A owns | Team B owns |
|---|---|---|
| Memory | Rust engine, transcripts, indexes, extraction and retrieval | No dependency |
| Context | Prime-inspired context selection and budgeted assembly | Its own fixed task context |
| Learning | Prime-inspired refinement of behavioral artifacts | AVO-inspired recursive evaluation and variation |
| Persistence | Memory database, context traces, versioned learning artifacts | Independent lineage and results |
| Evaluation | Team A's standalone baselines and measurements | Team B's standalone baselines and measurements |
| Final integration | Optional `prime` learning adapter | Optional `avo` learning adapter |

**Hard rule:** Team A's tests, demo, persistence and learning loop must work without importing `/evolve`, `/bench` or Team B APIs. A small shared provider-selection contract may be added later.

---

## 3. Architecture

```text
                  Hermes Agent (existing Python core)
                  agent loop / models / tools / tasks
                                  |
                    thin integration adapters
                                  |
              +-------------------+------------------+
              |                                      |
              v                                      v
      Context Formation                         Learning Hooks
      (Prime-inspired)                          (Prime-inspired)
      - task & intent                            - capture experience
      - context budget                           - identify reusable lesson
      - retrieve/select                          - propose refinement
      - optional recursive                       - validate/approve
        exploration                              - version/rollback
              |                                      |
              +-------------------+------------------+
                                  |
                            Python client
                                  |
                     versioned JSON RPC / IPC
                                  |
                     Shared Rust memory daemon
                  - transcripts / memory / artifacts
                  - embeddings & search indexes
                  - dedup / lifecycle / permissions
                                  |
                         durable local storage
```

### 3.1 Three distinct responsibilities

| Component | Core question | Output |
|---|---|---|
| **Rust memory engine** | What is stored, and what relevant records can be retrieved cheaply? | Ranked records, transcript references, metadata |
| **Context formation** | Which subset of available information should this task/model see, and in what form? | Bounded, attributed prompt context |
| **Prime-inspired learning** | What reusable behavioral guidance should change after experience? | Validated, versioned refinement artifact |

**Why retrieval alone is insufficient:** The Rust engine can return relevant hits but cannot, by itself, decide the best combination of recent turns, task requirements, retrieved memories, skills, conflicts, freshness and remaining token budget. Context formation makes those choices. It must **not** run an expensive LLM recursively by default: start with deterministic ranking and packing; use recursive exploration only when large or ambiguous source material justifies its cost.

**Why learning is not just storing memory:** Ordinary memory preserves facts and experiences. Learning proposes a reusable change to *how the agent behaves* (e.g. a task-specific procedure or instruction), validates it, and versions the accepted result. A memory hit is not automatically an approved instruction.

### 3.2 Data categories and lifecycles

| Category | Examples | Storage | Injection rule |
|---|---|---|---|
| Session transcript | User/assistant turns, tool outputs, timestamps | Durable append-only session store | Only recent or selected segments |
| Episodic memory | A completed task, outcome, error and fix | Indexed structured memory | On relevant task match |
| Semantic memory | Stable facts and preferences | Indexed structured memory | On relevance, with scope/provenance |
| Procedural guidance | Validated reusable workflow/skill | Versioned learning artifact store | Only applicable accepted versions |
| Context trace | Which records were selected, rejected, compressed | Lightweight diagnostic log | Not normally injected |

Long transcripts are **not** loaded wholesale into every prompt. They remain searchable source records, with references and selective excerpts. Replacing `MEMORY.md`/`USER.md` means migrating useful contents into structured entries and avoiding duplicate active memory systems; it does **not** imply deleting all Hermes session logs or losing durable chat history. Preserve rollback and export paths.

### 3.3 Proposed Rust storage schema

```text
MemoryEntry {
  id, namespace, agent_id?, session_id?, kind,
  text, source_ref?, created_at, updated_at,
  importance?, confidence?, tags[], embedding_ref?,
  access_scope, expires_at?
}
SessionTurn { id, session_id, turn_index, role, content_ref, timestamp, token_count? }
LearningArtifact { id, version, parent_version?, scope, body_ref, status, evidence_refs[], created_at }
ContextTrace { task_id, candidate_ids[], selected_ids[], token_budget, token_used, reason_codes[] }
```

These are **proposed logical fields**, not a claim about the existing Week 2 wire protocol. Add migrations and compatibility checks before changing an existing schema.

### 3.4 Retrieval and context pipeline

```text
Incoming task + recent conversation
          |
  Determine intent, scope, available budget
          |
  Rust search: recent turns + relevant episodes/facts + accepted skills
          |
  Filter by permission, freshness, duplicates, confidence
          |
  Rank and resolve conflicting evidence
          |
  Pack bounded context: instructions > task > recent turns > evidence
          |
  [Optional] recursively inspect source references if evidence is insufficient
          |
  Return prompt sections + provenance + context trace
          |
       Hermes model call
```

**Default:** one bounded retrieval pass with deterministic filtering and packing. **Escalation:** only when needed, explore source excerpts in small batches, summarize with citations/references, stop on evidence sufficiency or a hard token/call budget. Compare recursive and nonrecursive paths experimentally; no assumed savings.

### 3.5 Prime-inspired learning pipeline

```text
Task/trajectory + outcome
          |
   Detect potential reusable lesson
          |
   Draft a small candidate refinement
          |
   Check scope, conflicts, safety, and duplicates
          |
   Evaluate on a fixed task/fixture set
          |
   Accept / reject / request review
          |
   Persist version + evidence + decision
          |
   Future context formation selects accepted version
```

A candidate refinement might be a corrected troubleshooting checklist or a task-specific skill. Store both the **artifact** and its **evidence/decision metadata**; do not overwrite a single unversioned markdown file. This is Team A's design, **not a verified description of Prime Agent internals**.

**Learning policy:** Prefer deterministic checks when available. For tasks without an objective scorer, use human review or clearly labeled heuristic checks; do not claim measured improvement solely because an LLM approved its own edit. Do not allow learned text to override system or developer instructions.

### 3.6 Memory and learning integration

- Accepted learning artifacts are separately indexed, versioned and explicitly labeled as guidance.
- A task can retrieve ordinary memories without triggering learning.
- A learning run can use transcript references without copying every transcript into a prompt.
- Failed proposals remain in audit history but are not active guidance.
- Disable or bypass Hermes' legacy memory/review paths only after validating the replacement; avoid double injection and duplicate review calls.

---

## 4. Existing implementation and compatibility

The following **Week 1–2 progress was reported previously**; confirm against the current repository before marking it tested.

| Area | Reported state | Next step |
|---|---|---|
| Rust workspace | `/memd` and a provisional `MemoryEngine` trait | Implement persistent `memcore` |
| Daemon | `memd/crates/memd/src/lib.rs`, `main.rs`; concurrent clients | Connect real engine, measure concurrency |
| Transport | Versioned newline-delimited JSON; 1 MiB request cap; Unix sockets and Windows loopback TCP | Preserve versioning, add context/learning ops carefully |
| Backend | `MockEngine` in-memory stand-in | Replace with persistent retrieval |
| Python client | `memd/clients/python/memory_client.py` | Extend with backwards-compatible operations |
| Measurement | `memd/measure.py` for RPC latency/RSS and optional tokens | Add end-to-end context and learning metrics |
| Documentation | `memd/protocol.md` | Freeze interfaces and document errors |

### Existing provisional engine contract

```rust
trait MemoryEngine {
    fn store(&self, entry: String) -> String;
    fn retrieve(&self, query: &str, k: usize) -> Vec<(String, String, f32)>;
    fn search_sessions(&self, query: &str) -> Vec<String>;
    fn maybe_extract(&self, turn_context: &str) -> Vec<String>;
}
```

This is the **reported provisional** interface, not the final design. Keep a compatibility adapter while introducing typed records, scope filters and persistent storage.

### Proposed new operations (not yet implemented)

| Operation | Purpose |
|---|---|
| `get_context_candidates` | Search across relevant record types with metadata |
| `get_session_excerpt` | Fetch a bounded transcript span by reference |
| `list_accepted_artifacts` | Retrieve active versioned guidance for a task |
| `record_context_trace` | Save prompt selection and token accounting |
| `propose_refinement` | Persist candidate and its evidence |
| `record_refinement_decision` | Approve/reject with validation results |

Context formation and learning may initially be Python-side orchestration over Rust storage. Move hot paths into Rust only when profiling justifies it. Do not expand the protocol before documenting request/response schemas, version negotiation and error handling.

---

## 5. Repository layout (proposed)

```text
/memd/
  crates/
    memcore/              # A1: storage, embeddings, index, retrieval, extraction
    memd/                 # A2: shared daemon, concurrency, IPC, lifecycle
  clients/python/         # A2: RPC client and Hermes-facing adapters
  protocol.md             # jointly specified versioned contract
  measure.py              # A2: RAM and RPC measurements
/context/
  policy/                 # A2: relevance reranking, context budgets and packing
  recursive/              # A2: optional bounded source exploration
  tests/                  # A2 owns, A1 supplies retrieval fixtures
/prime_learning/
  engine/                 # A2: experience review, candidate refinement
  artifacts/              # A2: versioned guidance and rollback
  tests/                  # A2 owns, A1 supplies memory-backed fixtures
/integrations/hermes/     # A2: minimal hooks, provider adapters
/bench/team_a/            # shared quality benchmarks; A2 measurement runner
/interfaces/              # jointly frozen contracts and mock implementations
/docs/team_a/             # individual design and evaluation reports
```

Directory names beyond existing `/memd` are **proposals**. Existing Week 1–2 daemon, client and measurement code should be retained, not rebuilt.

---

## 6. Division of work within Team A — revised equal engineering ownership

Both members build **core system functionality**. A1 owns the *memory data plane* (what is stored and how it is found). A2 owns the *memory consumption and improvement plane* (which retrieved evidence reaches the LLM, and how reusable guidance is refined), **plus** the shared runtime. Context formation is part of the practical memory system, not a mere Hermes adapter.

| | **A1: Rust memory storage, extraction and retrieval** | **A2: Context formation, Prime learning and runtime** |
|---|---|---|
| Main question | How do we persist, index and retrieve the right memories efficiently? | How do we turn those memories into minimal useful context and validated improvements? |
| Core algorithms | Embeddings, hybrid search, reranking at retrieval stage, extraction, deduplication, memory lifecycle | Context selection, token budgeting, optional recursive exploration, experience review, refinement, validation and rollback |
| Rust ownership | `memcore` crate, storage and indexing | `memd` daemon, plus Rust context-policy modules where appropriate |
| Other ownership | Retrieval fixtures, storage/retrieval evaluation | Python client, Hermes hooks, Prime-learning module, measurement harness |
| Independently demonstrable result | Durable memory and retrieval benchmark using a CLI/test harness without Hermes | Bounded context and learning refinement against mocked memory responses, plus multi-client daemon |
| Report sections | Memory representation, extraction, retrieval accuracy and storage performance | Context efficiency, learning quality, shared runtime, token/RAM and integration performance |

**Workload note:** A2 has more separate integration surfaces, so its learning scope must remain deliberately small: one artifact type, one deterministic validation workflow and no open-ended agent self-modification. A1 should own the substantive retrieval experiments, not only CRUD. Reassess workload at weekly syncs.

### A1 implementation responsibilities

1. Design durable schemas for session transcripts, compact memory entries, metadata, provenance, scopes and permissions.
2. Implement Rust storage and indexing, embedding interface, semantic/keyword or hybrid retrieval, and configurable top-k search.
3. Implement memory extraction triggers, deduplication, lifecycle operations and source-excerpt lookup.
4. Expose a stable `MemoryBackend` trait consumed by A2; provide an in-process CLI/test harness.
5. Benchmark retrieval quality, persistence correctness, indexing cost and engine-only RAM/latency.

### A2 implementation responsibilities

1. Maintain and extend the existing Rust daemon, versioned IPC, concurrent client handling and Python client.
2. Implement **context formation as a real memory component**: take A1's scored records, select and rerank evidence, allocate token budgets, deduplicate snippets, retain citations/provenance, and emit a `ContextPackage`.
3. Implement optional bounded Prime-inspired recursive exploration for cases where a short retrieval result cannot answer the task; measure its extra LLM cost separately.
4. Implement **Prime-inspired learning**: inspect recorded experience, propose a reusable instruction/skill refinement, validate it against fixed tasks, version accepted artifacts, and support rollback. Keep learned artifacts distinct from episodic memories.
5. Wire the daemon, context builder and learning provider into Hermes with minimal hooks; run multi-agent RAM, prompt-token, latency and learning-quality comparisons.

### Contract-first parallel development

Freeze the interface during the first two days of the next implementation phase. A1 implements the backend against unit tests. A2 implements context and learning against a `MockMemoryBackend` returning representative scored entries. The daemon already has a provisional `MockEngine`; extend it as necessary rather than waiting for A1. A1 can test all core operations without running Hermes; A2 can demonstrate bounded context and a learning cycle without a real index.

### Internal interface proposal

```text
# Implemented by A1
MemoryBackend.store(record) -> id
MemoryBackend.search(query, scope, k) -> scored records
MemoryBackend.fetch_excerpt(source_ref, limit) -> excerpt
MemoryBackend.extract(turn_or_session_event) -> candidate records

# Implemented by A2
ContextBuilder.build(task, recent_turns, scored_records, token_budget) -> ContextPackage
PrimeLearner.observe(trajectory, outcome, context_refs) -> candidate?
PrimeLearner.validate(candidate, fixtures) -> ValidationReport
PrimeLearner.activate(candidate, report) -> version | rejection

# Runtime/protocol owned by A2, jointly versioned
DaemonClient.store/search/fetch_excerpt(...)
```

A `ContextPackage` contains rendered context, selected memory IDs, token count and provenance. `ValidationReport` contains test IDs, scores, failure reasons and cost. These are **proposed contracts**, not APIs already present in the fork. A2 may implement the context builder in Rust or Python, but its token policy and selection algorithm must be a separately testable component, not hardcoded inside a Hermes prompt hook.

---

## 7. Week-by-week implementation roadmap — revised ownership

This plan retains completed Week 1–2 daemon/client work and reallocates **new** core implementation evenly. Both members work in parallel using mocks and frozen interfaces.

| Week | A1 — memory data plane | A2 — context, learning and runtime | Handoff / checkpoint |
|---|---|---|---|
| **1** | Verify jcode memory mechanisms; specify entry schema, embeddings and retrieval contract | Verify Prime context/learning mechanisms; audit existing daemon and Hermes hooks; define context and learning schemas | Joint interface and fixtures frozen |
| **2** | Implement persistent Rust store and first indexed retrieval; test persistence | Validate existing IPC/client; implement context selection with mock scored records and token budget | Independent store and mock-context demos |
| **3** | Hybrid retrieval, source references and retrieval-quality fixtures | Integrate context builder into daemon/Hermes with mock backend; build repeatable measurement harness | A2 swaps mock retrieval for A1's engine |
| **4** | Extraction triggers, deduplication and transcript linking | Context packing, provenance, prompt injection and baseline token comparisons | Real memory-to-context path works |
| **5** | Improve ranking, index latency and lifecycle; retrieval ablations | Prime learning v1: trajectory review, candidate generation and versioned artifacts; daemon concurrency | Learning uses fixtures; retrieval independently benchmarked |
| **6** | Memory reliability, multi-agent scope tests, quality tuning | Validate/rollback learning candidates; connect live outcomes and context to learning; optional bounded recursion | Complete independent Team A demonstration |
| **7** | Retrieval recall/precision, persistence and storage/RAM measurements | Multi-agent RAM, token efficiency, latency and held-out learning checks | Baseline-vs-new comparisons |
| **8** | Retrieval/extraction ablations and repeat runs | Context/learning ablations, learning-token cost, repeat runs | Results frozen; no new features |
| **9** | Final memory report and retrieval demo | Final context/learning/runtime report and integrated demo | Independent submission ready |
| **10** | Spare: fixes and reruns | Spare: fixes and reruns | Optional provider integration |

### Critical milestones

- **End Week 2:** A1 has durable store/retrieval; A2 has mock-backed bounded context; contracts frozen.
- **End Week 4:** Real Rust retrieval drives context injected into a live Hermes task.
- **End Week 6:** Team A's own memory + context + Prime-learning pipeline completes without Team B.
- **End Week 8:** Repeated baseline comparisons and ablations collected; stop feature expansion.
- **Week 9:** Individual reports and demonstration; Week 10 is contingency.

### Scope cuts if late

1. Drop optional recursive LLM context exploration; keep deterministic context selection and packing.
2. Drop advanced memory graph relations and complex decay; keep persistent indexed retrieval and basic deduplication.
3. Restrict Prime learning to one versioned instruction/skill artifact and a small fixed validation set.
4. Reduce scaling beyond five agents while keeping repeated one-agent and multi-agent measurements.
5. Do **not** cut the persistent Rust store, bounded context builder, basic validated learning cycle or standalone Team A demonstration.

---

## 8. Evaluation design

**Baseline:** A pinned, unmodified Hermes fork on identical tasks, model settings, machine and tooling. For the project's intended provider, record the actual resolved `openai-codex` OAuth model ID (target: `gpt-6-luna`) from runtime logs rather than assuming it is active. Record temperature where configurable, context size, tool permissions and task order. Run at least three repeats and report mean and spread.

| Claim | Primary metrics | Comparisons / ablations |
|---|---|---|
| RAM efficiency | Daemon RSS, total process-tree RSS, incremental RAM per agent | Hermes; 1, 5, 10 agents; optional labeled naive per-process design |
| Retrieval | Recall@k, precision@k, retrieval latency | Hermes session search, keyword-only, vector/hybrid |
| Context efficiency | Prompt tokens/task, context assembly latency, task success | Full/recent context, raw top-k retrieval, budgeted selection |
| Extraction | Calls and tokens per session; memory quality | Hermes passive review vs triggered extraction |
| Learning | Task success before/after, regression count, accepted revisions | Fixed version 0 vs refined artifact, with/without learning |
| Overall | Total tokens/task, task completion, wall-clock, extra learning cost | Unmodified Hermes vs Team A full stack |

### Fairness and leakage controls

- Separate development tasks from held-out tasks; never tune using held-out outcomes.
- Count *all* LLM calls: main agent, extraction, recursive exploration and learning.
- Measure cold/warm starts and the daemon separately from Python process RAM.
- Distinguish one-time learning investment from per-task inference costs.
- Use exact same tasks and permitted tools across conditions; log failures, not only successes.
- Report negative results, including if recursive context formation increases cost.

### Minimum ablation matrix

| Configuration | Memory | Context formation | Learning |
|---|---|---|---|
| H0: Hermes baseline | Hermes native | Hermes native | Hermes native |
| A1: Rust only | Rust | Simple top-k packing | Off |
| A2: Rust + context | Rust | Budgeted Prime-inspired policy | Off |
| A3: Full Team A | Rust | Budgeted Prime-inspired policy | Prime-inspired |
| A4: Optional recursion | Rust | Budgeted + bounded recursive exploration | Prime-inspired |

A1/A2/A3 isolate mechanisms. A4 is optional and should be retained only if results justify its cost.

---

## 9. Optional final learning-provider selection

The final harness can expose a small adapter, **without merging algorithms or storage**:

```text
learning.provider = "prime"  # Team A
# or
learning.provider = "avo"    # Team B
```

```text
LearningProvider.observe(trajectory, outcome)
LearningProvider.learn(scope) -> LearningRunReport
LearningProvider.list_active_artifacts(scope) -> artifacts
LearningProvider.rollback(artifact_id, version) -> status
```

This is a **proposed shared facade**, not an existing implementation. Each provider maintains its own state, rules, evaluator and versioning. Select one active learning provider for a given run; neither provider must import the other. The Rust memory/context stack may be used with either provider after integration, but **Team B must not need it for standalone operation**.

---

## 10. Risks and safeguards

| Risk | Mitigation |
|---|---|
| Rust daemon saves little RAM versus Hermes | Measure subsystem and total RAM; report the actual difference |
| Context builder adds tokens/latency | Strict budgets; deterministic default; recursion only as measured opt-in |
| Memory retrieval injects stale or conflicting information | Provenance, timestamps, scope checks and conflict handling |
| Learning turns incorrect experiences into instructions | Validation gate, human review when needed, rollback and artifact scopes |
| Learning data is confused with user facts | Separate types, permissions, indexes and injection policies |
| Legacy memory and new memory both run | Feature flags, one active path, integration tests |
| Week 2 protocol changes break clients | Versioned schema, compatibility adapter, protocol tests |
| One member or Team B is late | Mocks, separate ownership, standalone evaluation |
| Prime-inspired behavior is inaccurately attributed | Source audit; distinguish verified upstream behavior from local design |

### Engineering rules

1. Work in feature branches; require review for interface or schema changes.
2. Do not delete existing Hermes memory/session files until migration and rollback tests pass.
3. Record attribution for reused code and architectural sources in `/docs/ATTRIBUTION.md`.
4. Never let a retrieved document or learned skill override higher-priority instructions.
5. Store candidate edits and acceptance evidence for auditability.
6. Keep private/session-scoped memory from leaking across agents or users; test isolation.
7. Update this README at each weekly checkpoint with **implemented / tested / blocked** status.

---

## 11. Final deliverables and definition of done

### Deliverables

1. Durable Rust memory engine, daemon, client and documented protocol.
2. Searchable session history plus structured long-term memory with scope controls.
3. Budgeted Prime-inspired context builder with traceable selection.
4. Prime-inspired learning/refinement pipeline with versioning, validation and rollback.
5. Standalone Team A integration into Hermes; no Team B dependency.
6. Benchmark scripts, repeated measurements, ablations, report and demo.
7. Optional thin `prime` learning-provider adapter for later provider selection.

### Definition of done

- A new agent session can store and retrieve relevant durable memory.
- A live Hermes task receives selected context within a declared token budget.
- One learning candidate can be generated, checked, accepted or rejected, and rolled back.
- All three mechanisms (Rust memory, context formation, learning) have independent evaluation evidence.
- Tests and demo work with Team B absent.
- Results are reproducible, limitations documented and attribution complete.

---

## 12. Decisions to finalize / verify

| Decision | Owner | Status |
|---|---|---|
| Pin Hermes baseline commit and resolved model configuration | A2 | Verify |
| Confirm Prime Agent's actual context and learning design in pinned source | A1 | Verify; no unverified claims |
| Memory schema and session transcript migration | A1 | Proposed |
| SQLite/index/embedding strategy based on benchmarks | A1 | Open |
| Context budget defaults and escalation threshold | A1 | Open |
| One evolving learning artifact type and validation policy | A1 | Open |
| IPC schema additions and version compatibility | A2 | Open |
| Member assignments and benchmark task set | Team A | Open |
| Optional shared learning-provider interface | Both teams, later | Not a blocker |

**Project principle:** Team A must demonstrate that *shared memory*, *intelligent context formation* and *persistent learning* each contribute measurable value, without relying on Team B's AVO-inspired work.
