# Shared-Memory, Self-Evaluating Agent Harness

A semester project built on a fork of Hermes Agent.

## Project thesis

We fork Hermes for its existing agentic structure. We replace its memory architecture with a Rust-based shared memory/runtime architecture inspired by jcode. We replace its passive, LLM-driven self-learning review with an AVO-inspired recursive evaluation-and-improvement architecture. We then measure whether these changes (1) reduce token consumption, (2) reduce RAM overhead in multi-agent execution, and (3) enable persistent, measurable learning.

- **Duration:** 9 weeks of build plus 1 spare week
- **Team:** 4 people in two pairs (Team A and Team B), each evaluated independently
- **Status:** living document. Update it at each weekly sync.

This is not a generic RAG system, a generic vector database, or a generic evolutionary agent. The contribution is the architectural replacement and the two independent team contributions.

---

## 1. Summary

Hermes Agent (Nous Research) is a Python agent with a built-in learning loop. Three things in its memory and learning design are what we want to change:

1. **Memory-related LLM calls.** Its passive learning review is a complete second agent turn run in the background, restricted to memory and skill tools.
2. **Duplicated infrastructure per agent (hypothesis, to be measured in week 3).** Each agent process carries its own memory stack. We believe this hurts multi-agent runs, but we do not yet know how large the memory subsystem is relative to the rest of a Hermes process.
3. **Learning without a measured signal.** An LLM is asked what to remember, and nothing checks whether the result helped.

### What stays and what changes

| Part | Decision |
|---|---|
| Agent loop, model interaction, tools, task execution, conversation, orchestration | **Kept from Hermes** |
| Memory storage, embeddings, indexing, retrieval, extraction, sharing, lifecycle | **Replaced** by the new Rust memory architecture (Team A) |
| Trajectory evaluation, learning signal, artifact variation, lineage, regression protection | **Replaced** by the new recursive learning architecture (Team B) |
| Hermes hooks and interfaces | **Kept only as integration boundaries** (see 3.5). They are the seam where the new systems connect, not part of the new architecture |

### Goals

- Lower tokens per task than unmodified Hermes
- Lower memory-subsystem RAM per added agent than unmodified Hermes
- A learning loop in which changes are accepted only when a measured score improves, with history stored persistently

### Non-goals

- Porting the Hermes agent loop, gateway or messaging integrations to Rust
- Population-level evolution or archive branching
- Evolving more than one kind of artifact
- Training or fine-tuning any model
- Using an LLM to judge outcomes where a deterministic check is possible

---

## 2. Background and verification status

### Hermes Agent
- Open source, MIT licensed, from Nous Research.
- Has a closed learning loop: persistent memory, Markdown skills written by the agent, FTS5 session search with LLM summarization.
- Its architecture docs list `agent/memory_provider.py` (provider interface), `agent/memory_manager.py` (orchestration) and `plugins/memory/` (provider plugins). This gives us a clean integration seam.
- Its passive review is a full second agent turn in a background thread, up to 16 iterations, with tools limited to memory and skills.

### jcode
- Open source, MIT licensed, Rust coding-agent harness.
- Architectural ideas we take from it:
  - one long-lived server shared by multiple clients over a Unix socket
  - centralized embedding and similarity-based retrieval, injected into the prompt without an agent tool call
  - memories extracted at defined moments (semantic drift, K turns, session end) rather than continuously
  - thin per-session clients
- Published figures (for reference, not for us to assume): about 27.8 MB for one session with local embeddings off, about 9.9 MB per added session, about 167 MB with local embeddings on.

### NVIDIA AVO (arXiv:2603.24517)
- Replaces a fixed "sample a parent, then generate a child" pipeline with one autonomous agent run that reads the full lineage of solutions and scores, a knowledge base, and a scoring function.
- In the paper the artifacts are CUDA attention kernels, scored on correctness and throughput. Candidates that fail correctness score zero.
- The paper evaluates a single lineage only.
- **We do not implement AVO.** We adopt its structure (autonomous variation, evaluation against a scorer, persistent lineage, iterative improvement) for a different domain: agent skills and policies.

### Verification status (important)

The claims above come from documentation, the repository layout and paper summaries, not from reading source code. Week 1 includes verification tasks (section 6) so that no architectural claim in this document is relied on until it has been checked against the code or paper.

| Claim | Status |
|---|---|
| Hermes memory is pluggable via `memory_provider.py` | From docs. Verify in code |
| Built-in Hermes memory can be disabled so only one memory system is active | Unverified |
| Where the background review/nudge is triggered | Not located yet |
| Whether Hermes subagents share a process or run separately | Unverified. Matters for the RAM claim |
| jcode retrieval and extraction behavior | From docs. Verify in code |
| AVO loop details and scoring structure | From paper summary. Re-read the paper |

---

## 3. Architecture

### 3.1 Overview

```
                  YOUR HARNESS

        +------------------------------+
        |     Hermes Agent Core        |
        |   (kept from Hermes)         |
        |  loop, models, tools, tasks  |
        +--------------+---------------+
                       |
           integration boundary (hooks only)
                       |
          +------------+-------------+
          |                          |
          v                          v
 +--------------------+    +-----------------------+
 | Rust Memory        |    | Recursive Learning    |
 | Architecture       |    | Architecture          |
 | (jcode-inspired)   |    | (AVO-inspired)        |
 | Team A             |    | Team B                |
 +--------------------+    +-----------------------+
```

### 3.2 Ownership boundaries

| Owner | Responsible for |
|---|---|
| **Hermes (kept)** | Agent loop, model interaction, tools, task execution, conversation, orchestration |
| **Team A: Rust memory architecture** | Memory storage, embeddings, indexing, retrieval, memory extraction, shared memory infrastructure, cross-agent memory access |
| **Team B: Recursive learning architecture** | Trajectory collection, evaluation, learning signal, artifact modification, lineage, regression protection |

### 3.3 Why a shared service reduces duplicated infrastructure

```
              HERMES (per-agent stack)             THIS HARNESS (shared service)

   Agent 1 -- own memory infrastructure       Agent 1 --+
   Agent 2 -- own memory infrastructure       Agent 2 --+--> Rust Memory Daemon
   Agent 3 -- own memory infrastructure       Agent 3 --+      - memory store
   Agent 4 -- own memory infrastructure       Agent 4 --+      - embedding model
                                                                - index
                                                                - retrieval engine
```

The intended mechanism is shared infrastructure instead of expensive infrastructure duplicated per process. Two honest limits on this claim:

- The agent processes themselves are still Python. Their baseline RAM may dominate. We therefore measure **memory-subsystem RAM** separately from **total process RAM**, and report both.
- If Hermes' own memory stack turns out to be small (it may use keyword search rather than a local embedding model), the saving against Hermes will be small too. The week 3 baseline decides how we frame this result. A secondary comparison is a naive "one embedding model per process" design, labeled clearly as such.

### 3.4 Interface contract

Both teams write this together in weeks 1 and 2. After the freeze, changes need both teams' agreement.

| Interface | Purpose | Implemented by |
|---|---|---|
| `MemoryService` | `store(entry)`, `retrieve(query, k)`, `search_sessions(query)` over a Unix socket | Team A |
| `TrajectoryLog` | Append-only record of each task run: steps, tool calls, tokens, outcome | Team B writes the schema |
| `LineageStore` | Candidate id, parent id, diff, score, timestamp, accepted or rejected | Team B |

Until Team A's daemon is ready, Team B uses a stub `MemoryService`. Until Team B's loop is ready, Team A tests on recorded Hermes trajectories.

### 3.5 Fork cleanup plan

**Principle:** keep only the minimum Hermes interfaces and hooks needed to connect the new systems. The memory manager is kept as a seam, not as an architectural component. The actual storage, retrieval, extraction, sharing and lifecycle belong to the Rust system.

**Before deleting anything:** keep an untouched copy of upstream Hermes (separate clone or pinned tag). It is the baseline for every comparison.

#### Integration seam (keep, modify minimally)

| Path | Role |
|---|---|
| `agent/memory_provider.py` | Interface the Rust daemon's Python client implements |
| `agent/memory_manager.py` | Calls into the new provider. Reduce to a thin pass-through. Check how it triggers the background review |
| `plugins/memory/` | Location of the new plugin. Remove bundled providers (such as Honcho) once the new one works |
| `agent/prompt_builder.py`, `prompt_caching.py` | Where retrieved memories are injected |

#### Replace (owned by the new architectures)

| Path | Action |
|---|---|
| `hermes_state_search.py` and the FTS5 session search path (including `native/fts5_cjk`) | Reroute session recall to the new `search_sessions`. Keep the files until the swap works |
| Code behind periodic memory nudges and the background skill/memory review | Replace with trigger-based extraction (Team A) and the evaluated loop (Team B). Not located yet: search `agent/` and `tools/` for `memory`, `skill_manage`, `review`, `nudge` |
| Built-in Hermes memory (memory tool and its stored files) | Disable or bypass so only one memory system is active. Verify how it interacts with external providers |
| `agent/trajectory.py`, `trajectory_compressor.py` | Reuse as the base for `TrajectoryLog` |

#### Keep unchanged (agent core)

- `run_agent.py`, `agent/conversation_loop.py`, `agent/turn_*.py`
- `model_tools.py`, `toolsets.py`, `tools/registry.py` and the core tools (file, terminal, code execution)
- `hermes_cli/`, `cli.py`
- `agent/anthropic_adapter.py`, `runtime_provider.py`, `providers/` for the model(s) used
- `hermes_state*.py` (session storage)
- `tools/delegate_tool.py` (subagents, relevant to the multi-agent claim)
- `agent/context_engine.py`, `context_compressor.py` (a separate token cost, a possible later optimization)
- `evals/`, `tests/`, `LICENSE`, `README.md`, `AGENTS.md`, `pyproject.toml`, `uv.lock`

#### Safe to remove (outside the project's scope)

Remove in a separate branch, in small commits, with tests passing after each commit.

| Path | Reason |
|---|---|
| `website/`, `apps/`, `assets/`, `locales/`, `contributors/` | Docs site, desktop apps, translations, banners |
| Translated READMEs and policy files (`README.es.md`, `README.zh-CN.md`, `README.ur-pk.md`, `SECURITY.es.md`, `CONTRIBUTING.es.md`) | Translations |
| `plugins/platforms/` and most of `gateway/platforms/` | Messaging adapters. Evaluation uses the CLI |
| `ui-tui/`, `tui_gateway/`, `tests-js/`, `package*.json`, `.npmrc`, `.nvmrc`, `eslint.config.shared.mjs`, `.prettier*` | JS terminal UI and tooling |
| `nix/`, `flake.*`, `docker/`, `Dockerfile`, `docker-compose*.yml`, `constraints-termux.txt`, `.hadolint.yaml`, `.envrc` | Packaging for other environments |
| `cron/` | Scheduler, unless Team B uses it to run evolution |
| `acp_adapter/` | IDE integration |
| `datagen-config-examples/`, `mcp-research-data/`, `optional-mcps/`, `optional-skills/`, `mini_swe_runner.py` | Research and training-data extras |
| `batch_runner.py` | Check first: Team B may use it to run benchmarks |
| Browser, voice and vision tools under `tools/` | Not needed for coding benchmarks |

**Be careful with `gateway/`.** It also contains session routing. Remove it last, if at all.

#### Rules for deleting

1. Tools register at import time. Deleting a file that another module imports breaks startup.
2. The test suite is large (about 25,000 tests). Delete tests for deleted features in the same commit and run the rest.
3. Disable in config before deleting. Same runtime effect, no breakage risk.
4. Deleting is not the contribution. Unused code costs nothing; a broken build costs weeks.
5. Never remove anything under "Keep unchanged."

### 3.6 Repository layout (proposed)

```
/hermes-core/   forked Hermes Python code (integration hooks only)
/memd/          Rust workspace (Team A)
/evolve/        learning loop, variation agent, lineage store (Team B)
/bench/         benchmark tasks and scorer (Team B), measurement scripts (shared)
/interfaces/    interface specs and schemas (shared, frozen after week 2)
/docs/          reports, measurement protocols, attribution, this document
```

---

## 4. Team A: Rust Memory and Runtime Architecture (jcode-inspired)

### Thesis

A shared Rust memory/runtime architecture reduces duplicated per-agent infrastructure and reduces unnecessary memory-related LLM calls.

These are two different mechanisms, measured separately:

| Benefit | Mechanism | Measured as |
|---|---|---|
| Lower RAM per added agent | Shared daemon: one embedding model, index and store serve all agents through thin clients | Memory-subsystem RAM and total RAM at 1, 5 and 10 agents |
| Lower token use | Passive retrieval (no recall tool calls) plus trigger-based extraction (fewer, better-timed LLM calls than a per-conversation review) | Tokens per task, and LLM calls per session for extraction |

One mechanism does not explain the other, and the report must not suggest that it does.

### Components (kept separate on purpose)

| Component | Question it answers |
|---|---|
| Memory representation | What is a memory entry, and how are entries related (for example, a graph of entries)? |
| Memory retrieval | Given the current turn, which entries are relevant and how many are injected? |
| Embedding infrastructure | Where does the embedding model live, and how is it shared? |
| Shared runtime | What process owns state and how do agents attach and detach? |
| Inter-process communication | Unix socket protocol, request and response format, concurrency |
| Per-agent state | What stays in each agent client versus the daemon? |
| Extraction | When are new memories created, and by what trigger? |

Designing and justifying these separately is the architectural contribution. A vector database alone is not the contribution.

### Deliverables

1. Persistent memory store with vector retrieval, implemented in Rust
2. Passive per-turn retrieval injected into the prompt with no agent tool call
3. Trigger-based extraction (semantic drift, every K turns, session end)
4. Shared daemon with multiple clients over a Unix socket
5. Python client used by the Hermes integration seam
6. Stretch (cut first): relevance-verifying sideagent, cross-agent file-change notifications

### Storage and index decision

Persistent storage and index technology are **not committed up front**. The default starting point is SQLite plus a simple vector index behind a trait, so it can be swapped. In weeks 2 and 3, run a short benchmark (latency, RAM, retrieval quality at the expected store size) and keep the default unless the numbers say otherwise. Do not spend more than a few days on this.

### Suggested split inside the pair

| Member | Focus |
|---|---|
| A1 | Representation, retrieval, extraction triggers, storage benchmark |
| A2 | Daemon, socket protocol, Python client, measurement scripts (tokens, RAM, latency) |

### Success criterion (independent of Team B)

> The memory/runtime architecture reduces memory overhead and/or token consumption compared with unmodified Hermes, on the same tasks, with repeated runs.

### Metrics

| Metric | Compared against |
|---|---|
| Tokens per task | Unmodified Hermes |
| Memory-subsystem RAM and total RAM per added agent (1, 5, 10) | Unmodified Hermes, plus a labeled naive per-process-embedding design |
| Retrieval precision@k and recall on a labeled set | Hermes' keyword (FTS5) recall |
| LLM calls for extraction per session | Hermes' review loop |
| Added latency per turn | Hermes baseline |

---

## 5. Team B: Recursive Learning Architecture (AVO-inspired)

### Thesis

The harness improves measurably across iterations from persisted, evaluated outcomes. The learning signal comes from task evaluation, not from an LLM deciding what to remember.

### What "recursive learning" means here

```
Agent executes task
        |
Trajectory + outcome
        |
Evaluation (deterministic where possible)
        |
What worked / what failed?
        |
Persistent learning record  (lineage + failure patterns + artifact versions)
        |
Future agent run uses the improved artifact
        |
Evaluate again
```

### What we take from AVO

| AVO concept | Our version |
|---|---|
| Autonomous variation agent | An agent that proposes edits to the evolving artifact |
| Scoring function `f` | Our benchmark scorer (pass/fail, tokens, steps, time) |
| Lineage of solutions and scores | `LineageStore`, including rejected attempts |
| Knowledge base | Task descriptions, failed trajectories, prior lineage |
| Iterative improvement | Repeated propose, evaluate, accept/reject cycles |
| Artifact being optimized (CUDA kernel) | **One** agent artifact: skill files or retrieval/extraction policy config |

### Comparison with Hermes' learning

| Hermes | This architecture |
|---|---|
| A conversation happens | A task executes |
| An LLM reviews the conversation | The system evaluates the outcome |
| The LLM decides what to remember | The evaluation produces the learning signal |
| Memory or skills are updated | A persistent learning record is updated |
| Future conversations may retrieve it | Future runs use the accepted artifact version |
| Review consumes tokens during normal use | Evaluation is largely deterministic |

### Evaluator rules

- The scorer is a central Team B contribution, built and run on unmodified Hermes by the end of week 2 to give the baseline.
- Prefer deterministic, measurable checks (tests pass, output matches, command succeeds) over asking an LLM whether the result was good.
- If an LLM judge is ever used for a task, it is flagged in the report and excluded from the headline results.
- The benchmark is split into a training set and a held-out set. The variation agent never sees held-out tasks.

### Honest limits

- The variation agent is still an LLM, so learning is not token-free. The claim is **learning cost per accepted improvement, measured**, not "cheaper by definition." The cost happens in batch evaluation rather than after every conversation. Whether the total is lower than Hermes' review loop is a result to report either way.
- Deterministic scoring only works on tasks with a checkable outcome. The loop is scoped to such tasks.

### Deliverables

1. Benchmark suite (15 to 20 tasks, automatic checks, training and held-out split)
2. Scorer and trajectory logging
3. Lineage store
4. Variation agent
5. Regression guard that rejects edits lowering the score on previously passing tasks

### Suggested split inside the pair

| Member | Focus |
|---|---|
| B1 | Benchmark tasks, scorer, trajectory logging, held-out evaluation |
| B2 | Lineage store, variation agent, regression guard, cost accounting |

### Success criterion (independent of Team A)

> The recursive learning architecture improves task performance over successive iterations while recording persistent lineage, and the improvement holds on held-out tasks.

### Metrics

| Metric | Compared against |
|---|---|
| Score across generations (training set) | Version 0 (unmodified Hermes behavior) |
| Score on held-out tasks | Version 0 |
| Learning tokens per accepted improvement | Hermes' review loop |
| Share of proposed edits rejected | Itself, across generations |
| One ablation (with and without lineage) | Itself |

---

## 6. Weekly plan

Integration combines the two contributions. Neither team's evaluation depends on it succeeding.

| Week | Team A | Team B |
|---|---|---|
| **1** | Verification tasks (below). Write the interface spec with Team B. Choose the embedding approach (one local model loaded once in the daemon, or a remote API). Cargo workspace and Python client skeleton | Verification tasks (below). Choose the evolving artifact. Draft benchmark tasks with automatic checks. Same interface spec |
| **2** | Store and retrieve working, called from Python. Short storage/index benchmark. **Interface freeze** | Scorer running on unmodified Hermes (baseline). Trajectory logging. **Interface freeze** |
| **3** | Passive retrieval injected per turn. Baseline of unmodified Hermes: tokens, memory-subsystem RAM and total RAM | Lineage store. Variation agent v1 can propose and score one edit |
| **4** | Trigger-based extraction. Compare LLM calls per session against Hermes' review loop | Variation agent completes a propose, test, commit-or-discard cycle unattended |
| **5** | Daemon with multiple clients over a Unix socket. First RAM-per-session measurement | First multi-generation run. First plot of score vs. generation |
| **6** | **Joint integration milestone** | |
| **7** | Scaling tests at 1, 5 and 10 agents. Retrieval precision@k | Learning cost accounting. Held-out regression check |
| **8** | Ablations (retrieval on/off, trigger variants). Optimization | Ablation (lineage vs. no lineage). Longer evolution run |
| **9** | Final measurements, report, demo | Final measurements, report, demo |
| **10** | Spare week | Spare week |

### Week 1 verification tasks

| Task | Owner |
|---|---|
| Read `memory_provider.py`, `memory_manager.py`, `plugins/memory/`. Confirm how a new provider is selected and whether built-in memory can be turned off | A |
| Locate the background review/nudge trigger and what it calls | A and B |
| Determine whether subagents share a process or run separately | A |
| Read jcode's memory code. Confirm retrieval flow, extraction triggers and how clients attach | A |
| Re-read the AVO paper. Confirm what the scorer and lineage contain and what is single-lineage | B |
| Confirm the Hermes commit to pin and the model and settings for all measurements | Both |

### Checkpoints

| When | Must be true | If not |
|---|---|---|
| End of week 2 | Baseline exists, scorer works, interfaces frozen | Stop adding features until done |
| End of week 4 | Team A retrieves memories inside a live Hermes run. Team B's loop completes a cycle | Apply the cut list |
| Week 6 | Integration happens, even if partial | Record what works, continue with stubs |
| End of week 8 | All measurements collected | Week 9 is writing only |

### Cut list (in order)

1. Team A: relevance-verifying sideagent and file-change notifications
2. Team B: extra ablations and regression-guard extras
3. Team A: scaling tests beyond 5 agents
4. Team B: reduce the benchmark to 12 tasks, keeping a held-out set

---

### 6.1 Task division inside each team (per member, week by week)

This expands the "suggested split" in sections 4 and 5. Each member owns a separate set of directories, a separate deliverable and a separate part of the report. Inside each pair, the two members work in parallel against a small written contract and mock the other person's part until the real one lands.

#### Principles for parallel work inside a pair

1. **Contract first, in days 1 and 2 of week 1.** The pair writes down the interface between its two members (below) and commits it. After that, nobody waits for the other person.
2. **Mock the other side.** Each member builds a simple stand-in for their partner's component, so they can test their own work from week 1.
3. **Separate directories.** Each member owns specific directories. Conflicts in the same file should be rare.
4. **Few, planned handoffs.** Real integration between the two members happens at fixed points (named in the tables), not continuously.
5. **Equal weight.** Both members get core engineering work, measurement work and a section of the report.
6. **Never block on a partner.** If the partner's part is late, keep using the mock and tell the team at the weekly sync.

---

#### Team A: Memory and Runtime

| | **A1: Memory core (the "what")** | **A2: Runtime and integration (the "where and how")** |
|---|---|---|
| **Owns** | `/memd/crates/memcore`: memory entry representation, embedding interface, store, index, retrieval and ranking, extraction triggers, memory lifecycle | `/memd/crates/memd` (daemon), `/memd/clients` (Rust and Python clients), the Hermes provider plugin under `/hermes-core`, the measurement scripts |
| **Thinks about** | Is the right memory found, and is it created at the right moment? | Can many agents share one service cheaply, and what does it cost in RAM, tokens and latency? |
| **Report sections** | Memory representation, retrieval, extraction, retrieval quality results | Shared runtime, IPC, per-agent state, RAM and token results, scaling |

**Contract between A1 and A2** (written in week 1, days 1 and 2). A1 exposes a Rust trait `MemoryEngine`:

- `store(entry) -> id`
- `retrieve(query, k) -> [scored entries]`
- `search_sessions(query) -> [session hits]`
- `maybe_extract(turn_context) -> [new entries]`

A2 builds everything around that trait. Until A1's engine works, A2 uses `MockEngine`, an in-memory list with substring matching. A1 tests `memcore` with unit tests and recorded trajectories, with no daemon needed.

| Week | A1: Memory core | A2: Runtime and integration | Handoff point |
|---|---|---|---|
| **1** | Verify: read jcode's memory code (retrieval flow, extraction triggers, embedding). Write the `MemoryEngine` trait and entry schema. Pick the embedding approach with A2. Crate skeleton and unit-test setup | Verify: read `memory_provider.py`, `memory_manager.py`, `plugins/memory/`. Find the review/nudge trigger and check how subagents run. Cargo workspace, daemon skeleton, `MockEngine`. Pin the Hermes commit and set up an untouched baseline copy | Contract committed by end of day 2 |
| **2** | Real store and embedding working: write and read entries, cosine top-k. Short storage/index benchmark (latency, RAM, quality). **Interface freeze** | Socket protocol (request and response format) against `MockEngine`. Python client calling the daemon. Measurement scripts for tokens, RAM and latency, with the protocol written down. **Interface freeze** | None. Fully independent |
| **3** | Retrieval policy: top-k, similarity threshold, how entries are formatted for the prompt. Build a labeled set ("this query should find this memory") for quality measurement | Hermes provider plugin that injects retrieved memories each turn (using `MockEngine`). **Baseline runs of unmodified Hermes**: tokens, memory-subsystem RAM, total RAM, on the shared task set | A2 swaps `MockEngine` for A1's real engine at the end of the week |
| **4** | Extraction triggers (semantic drift, every K turns, session end) and deduplication of similar entries | Run the full loop inside a live Hermes session with the real engine. Count LLM calls and tokens per session. Compare with Hermes' review loop | A2 feeds back real trajectories and bugs to A1 |
| **5** | Memory relationships (for example, a graph of related entries) and lifecycle (merge, decay, removal). Improve retrieval using the live-run findings | Concurrency: many clients attach to one daemon, client attach and detach, error handling. First RAM-per-added-session measurement | None |
| **6** | **Integration.** Fix retrieval problems surfaced by the combined harness. Support Team B's use of the memory service | **Integration.** Wire the provider into the combined harness with Team B. Make sure the baseline and new runs use identical settings | Joint with Team B |
| **7** | Retrieval evaluation on the labeled set: precision@k and recall against Hermes' keyword recall. Tune thresholds on a dev split only | Scaling tests with 1, 5 and 10 agents: memory-subsystem RAM, total RAM, latency. Add the labeled naive per-process-embedding comparison | None |
| **8** | Ablations on extraction (trigger variants, with and without deduplication). Optimize hot paths in retrieval | Ablation: retrieval on and off. Latency and RAM optimization in the daemon. Re-run measurements with the fixed protocol (3 or more runs) | None |
| **9** | Final results for retrieval and extraction. Write report sections. Prepare the retrieval part of the demo | Final results for tokens, RAM and scaling. Write report sections. Prepare the runtime part of the demo and the combined demo | A1 and A2 merge their sections into one report |
| **10** | Spare: rerun measurements, fix issues, polish | Spare: rerun measurements, fix issues, polish | |

**If A1 is late:** A2 keeps measuring with a simple keyword engine, so the runtime results still exist.
**If A2 is late:** A1 evaluates retrieval and extraction offline on recorded trajectories, so the memory results still exist.

---

#### Team B: Recursive Learning and Evaluation

| | **B1: Evaluation side (the "judge")** | **B2: Improvement side (the "learner")** |
|---|---|---|
| **Owns** | `/bench`: benchmark tasks, scorer, trajectory logging, train and held-out splits, statistics | `/evolve`: lineage store, artifact versioning, variation agent, regression guard, cost accounting |
| **Thinks about** | Is the measurement correct, repeatable and fair? | Does the system learn from the measurements, and at what cost? |
| **Report sections** | Benchmark design, scorer, baseline, held-out results, statistics | Learning loop, lineage, variation agent, learning cost, ablations |

**Contract between B1 and B2** (written in week 1, days 1 and 2):

- **`ScoreReport`**: a JSON record per task run with `task_id`, `passed`, `tokens`, `steps`, `time`, `failure_reason`, `trajectory_ref`, `candidate_id`, `split` (train or heldout).
- **Runner command**: `bench run --candidate <artifact path> --split train|heldout` writes one `ScoreReport` per task.
- **Artifact**: the one thing that evolves (skill files or policy config), stored as a versioned file or folder that the runner accepts as a path.

B2 builds against `MockScorer`, which returns seeded, fixed `ScoreReport`s from fixtures. B1 builds without lineage, by running version 0 (unmodified Hermes behavior) only.

| Week | B1: Evaluation side | B2: Improvement side | Handoff point |
|---|---|---|---|
| **1** | Verify: read how Hermes saves trajectories and where the review/nudge runs. Draft 20 candidate benchmark tasks with automatic pass/fail checks. Define the `ScoreReport` schema | Verify: re-read the AVO paper (scorer, lineage, single-lineage). Choose the evolving artifact (skills or policy config). Define the lineage schema. Write `MockScorer` | Contract committed by end of day 2 |
| **2** | Scorer running on unmodified Hermes (baseline). Finalize 15 to 20 tasks with a training and held-out split. Trajectory logging. **Interface freeze** | Lineage store (candidate, parent, diff, score, accepted or rejected). Artifact versioning: apply a diff, record it, roll back. **Interface freeze** | None. Fully independent |
| **3** | Baseline run of unmodified Hermes at least 3 times. Measure variation between runs. Remove or fix tasks that pass and fail randomly | Variation agent v1: reads fixture trajectories and `MockScorer` output, proposes one edit to the artifact, records it in the lineage | B2 gets the real baseline numbers at the end of the week |
| **4** | Candidate runner: run any artifact version on the training or held-out split reliably, including parallel runs and cached results. Structured failure summaries per task | Full propose, test, commit-or-discard cycle running against the **real** scorer without human steps | B2 switches from `MockScorer` to the real scorer early in the week |
| **5** | Held-out protocol: confirm the variation agent never sees held-out tasks or their results. Failure analysis tooling for the variation agent | First multi-generation run. Plot score against generation. Regression guard (reject an edit that lowers the score on tasks that previously passed) | B1's failure summaries feed B2's agent |
| **6** | **Integration.** Run the benchmark with Team A's memory enabled and disabled under identical settings | **Integration.** Use the real memory service for the loop's knowledge and learning records | Joint with Team A |
| **7** | Held-out regression check of the evolved artifact against version 0. Statistics: mean and spread over repeated runs | Learning cost accounting: tokens spent on learning per accepted improvement, compared with Hermes' review loop | B2 asks B1 for the measurements used in the cost comparison |
| **8** | Benchmark robustness checks and a longer final evaluation, with the protocol unchanged | Ablation: with and without the lineage. Longer evolution run | None |
| **9** | Final results for baseline, held-out and statistics. Write report sections. Prepare the evaluation part of the demo | Final results for learning curve, cost and ablation. Write report sections. Prepare the learning part of the demo | B1 and B2 merge their sections into one report |
| **10** | Spare: rerun measurements, fix issues, polish | Spare: rerun measurements, fix issues, polish | |

**If B1 is late:** B2 keeps working against `MockScorer`, but the real scorer is the week 2 checkpoint, so B1 should be protected from other work that week.
**If B2 is late:** B1's baseline, variance and held-out results still stand as a complete evaluation contribution.

---

#### Why this split maximizes parallel work

- **A1 and A2** share only one Rust trait. A2 can build the daemon, protocol, client, plugin and measurements without A1's engine existing.
- **B1 and B2** share only one JSON record and one command. B2 can build the lineage and variation agent without B1's real scorer existing.
- Each member's work is **visible and individually gradable**: separate directories, separate report sections and a contribution log (section 7).
- The real handoffs are few (weeks 3, 4 and 5 inside each pair, week 6 across pairs), and each one has a fallback so a late partner does not stop the other.

---

## 7. Rules

### Working rules

1. **Weekly sync:** one joint 30-minute meeting (demo, blockers, interface questions, checkpoint review).
2. **Branches and review:** feature branches, PRs to `main`, one review from someone other than the author.
3. **Directory ownership:** Team A owns `/memd`, Team B owns `/evolve` and `/bench`. Changes to the other team's directory need that team's approval.
4. **Interface freeze:** after week 2, `/interfaces` changes need both teams' agreement, a note in the PR and a version bump.
5. **Minimal hooks in `/hermes-core`:** only integration seams. Keep them small so upstream stays mergeable.
6. **Stubs keep teams unblocked.** Never wait for the other team.
7. **Cut early:** if a checkpoint is missed, apply the cut list that week.

### Evaluation rules

1. **Independent reports:** each report uses its own metrics and results.
2. **Fair baselines:** unmodified Hermes on the same tasks, model, temperature and machine. Record the configuration in `/docs`.
3. **Fixed measurement protocol:** write down how tokens, RAM and latency are measured (tool, interval, warm vs. cold) before the first number, and do not change it.
4. **Repeat runs:** at least 3 per measurement, reporting mean and spread.
5. **Held-out data:** never used during evolution.
6. **Report negative results**, with the reason.
7. **No tuning to the test:** no threshold changes after seeing held-out results.

### Integrity and licensing

1. **Cite sources:** Hermes Agent and jcode (MIT), and the AVO paper (arXiv:2603.24517), in code headers where relevant and in both reports.
2. **Mark reused code** and list it in `/docs/ATTRIBUTION.md`. The graded contribution is what we design and build.
3. **Contribution log:** each member keeps a short log of what they built so each team's grade can be justified individually.
4. **Safety of self-modifying behavior:** the loop may only edit its designated artifact, inside its workspace, and every accepted change is recorded in the lineage so it can be rolled back.
5. **Do not describe the project as an implementation of AVO or a copy of jcode.** Use "AVO-inspired" and "jcode-inspired."

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Python agent processes dominate RAM, so memory-subsystem savings look small | Measure memory-subsystem RAM and total RAM separately. Decide the framing after the week 3 baseline |
| Hermes' memory stack is small, leaving little to save against | Report the honest result. Add the labeled naive per-process-embedding comparison |
| No scorer, so the learning loop cannot be evaluated | Scorer and baseline are the week 2 checkpoint. Nothing else in Team B starts before it |
| Learning cost is higher than Hermes' review | Measure learning tokens per accepted improvement. Report either outcome |
| Token savings smaller than expected because extraction still uses an LLM | Measure at week 4. Tune trigger frequency. Report the real number |
| Embedding model eats RAM savings | Load it once in the daemon, or use a remote embedding API. Decide in week 1 |
| Learned edits degrade behavior | Regression guard, held-out set, lineage with rollback |
| Architectural claims turn out wrong against the source | Week 1 verification tasks. Update this document |
| Integration slips | Stubs on both sides. Integration is a milestone, not a dependency of either report |
| Upstream Hermes changes break the fork | Pin a commit hash. Keep hooks minimal |

---

## 9. Final deliverables

**Shared:** integrated harness and demo; this document, updated; `/docs/ATTRIBUTION.md`.

**Team A:** Rust memory daemon and Python client; report with the section 4 metrics.

**Team B:** benchmark suite, scorer, lineage store and variation agent; report with the section 5 metrics.

### Definition of done

- Both teams have baseline-vs-new numbers on the same tasks, with repeated runs.
- The integrated system runs at least one benchmark pass end to end.
- Every accepted learning change is traceable in the lineage store.
- Attribution and licensing notes are complete.
- Every architectural claim in this document is marked verified or removed.

---

## 10. Future work (mention in reports, do not build)

- Port the Hermes agent loop and gateway to Rust
- Population-based evolution and archive management
- Evolving several artifact types together
- Cross-agent sharing of learned skills through the daemon
- Learning on tasks without a deterministic outcome check

---

## Open decisions (fill in at the first meeting)

| Decision | Owner | Decided |
|---|---|---|
| Which pair is Team A and which is Team B | All | |
| Evolving artifact: skills or retrieval policy | Team B | |
| Embedding approach: local model in daemon or remote API | Team A | |
| Storage/index choice after the short benchmark | Team A | |
| Hermes commit to pin | Both | |
| Model and temperature for all measurements | Both | |
| Names for A1, A2, B1, B2 | All | |
