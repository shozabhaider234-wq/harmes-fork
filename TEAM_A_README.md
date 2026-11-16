# Team A: persistent memory and context architecture

## Native extraction integration

The native Hermes background reviewer now persists through the selected Rust
provider's native-store adapter. See [audit, migration and validation](memd/NATIVE_REVIEW.md).
The native `memory` tool, explicit `memd_memory` remember, approvals, Journey and
reset consumers share Rust-backed records. USER and MEMORY use distinct
profile-derived namespaces. No additional extractor, timer, model or learning
step is introduced. Context formation remains separate. This supersedes the
older "future extraction" statements in the stage history below.

## Finalized architecture

Team A extends Hermes at the memory boundary. It does **not** replace the agent
loop or acquire ownership of raw conversation history. Team B's AVO-inspired
provider remains independent and unchanged.

```text
Hermes conversation / original session persistence (Hermes owns this)
       |
       | explicit memory or native Hermes background review
       v
Already-extracted semantic / episodic record + source reference
       |  Python MemdClient / versioned JSON Lines v1
       v
Rust memd runtime (Unix socket; current-user Windows named pipe; development TCP)
       |
       v
memcore::SqliteStore <--- EmbeddingBackend (offline FastEmbed or explicit synthetic fixture)
       |
       +--- SQLite: records, metadata, scopes, provenance, migrations
       +--- sqlite-vec: normalized float32 embeddings, cosine distance
       +--- FTS5: keyword candidates / BM25
       |
       v
Scoped vector / keyword / hybrid retrieval -> ranked records + provenance
       |
       v
A2 ContextBuilder -> temporary, token-budgeted context package -> Hermes caller

A2 Prime-inspired provider -> separately validated/versioned learning artifacts
Team B AVO-inspired provider -> independent learning implementation
```

SQLite with sqlite-vec is the selected persistent memory backend. There is no
external vector database or cloud requirement. sqlite-vec stores/searches vectors;
the embedding provider generates them. Performance must be benchmarked at the
target dimension, model, data size and concurrency before scalability claims.

## Data categories, owners and lifecycles

| Category | Owner / storage | Lifecycle |
|---|---|---|
| Raw sessions and messages | Existing Hermes persistence | Existing Hermes policy, format and locations, unchanged |
| Semantic memories | A1 / separate SQLite database | Explicit ingest, update, expiry filtering, delete |
| Episodic memories | A1 / same memory database | Extracted task/error/solution/outcome, same lifecycle |
| Memory embeddings | A1 / sqlite-vec virtual table | Atomic insert/replace/delete with memory |
| Metadata and provenance | A1 / memory rows | Persisted with memory; references only |
| Validated Prime learning artifacts | A2 / separate versioned artifact store | Proposal, validation, promotion, rollback; not memory facts |
| Context packages | A2 / generated on demand | Temporary, token-budgeted, not transcript or learning storage |
| AVO learning | Team B / its existing implementation | Independent; untouched by this task |

### Original session storage is strictly preserved

Inspection found that this fork **already uses SQLite for original transcripts**:
`hermes_state.py` resolves profile-aware `get_hermes_home()/state.db`;
`hermes_state_messages.py` persists session/message records;
`agent/session_persistence.py` owns agent transcript flushing and trajectory export.
`hermes_state.py::divert_session_transcript_jsonl` also supports the existing
`sessions/<session_id>.jsonl` recovery path. None of those paths or formats changes.
The new SQLite database is a **different file** containing extracted memories only.
No transcript migration, duplication, relocation, deletion or new transcript writer
is implemented.

Hermes already provides stable session IDs and `messages.message_uid`.
`agent/message_metadata.py::MESSAGE_UID` documents that logical UID as preserved
across restarts, compaction copies, rotations and rewrites. Physical message row IDs
can change and should not be the preferred reference.

Recommended opaque provenance format (URI components must be percent-encoded):

```text
hermes://<profile-identity>/sessions/<session-id>/messages/<message_uid>
```

The extractor/caller supplies `session_id` and `source_ref`; memcore never opens,
validates, resolves or copies the referenced transcript. Preserve a profile identity
because different Hermes profiles are isolated islands. For legacy exports lacking
UIDs, a caller may reference a transcript locator + SHA-256 of an immutable snapshot
+ message ordinal (optionally a message-content digest). This identifies a version
and occurrence; content hashes alone cannot distinguish repeated messages. Such a
legacy snapshot resolver remains planned. The optional Python bridge implements
read-only session/message source lookup using current durable UIDs, including
copies/rotations, without changing the original transcript storage.

Do not configure `--store` to point at Hermes `state.db`, any transcript, Prime
artifact, or an existing JSONL memory file. Legacy JSONL support remains available
as a library API; automatic import/migration is deliberately absent. Back up and
explicitly export/import extracted records if a later migration is required.

## Implemented storage schema

`memcore::MemoryRecord` flattens the existing `MemoryEntry`, preserving its
`content`, `category`, `tags`, string metadata, ID and UTC timestamps. It adds
`namespace`, `agent_id`, `session_id`, `kind` (`semantic`/`episodic`), `source_ref`,
`importance`, `confidence`, `access_scope` and optional RFC3339 `expires_at`.
This preserves the completed entry/category API rather than renaming `content` to
`text`; the daemon continues accepting the existing `text` alias. Importance and
confidence are optional finite values in [0,1]. Existing categories are independent
of the broader semantic/episodic kind. Prime artifacts are not a memory kind.

| Table/index | Purpose / relationship |
|---|---|
| `embedding_space` | Singleton model identity (including caller-declared revision/configuration) and dimension for the whole DB |
| `memories` | Integer internal row ID, unique public string ID, indexed scope fields, normalized text, content/tags, expiry epoch, JSON payload with typed metadata |
| `memory_vectors` | `vec0` normalized float32 vectors keyed by the same internal row ID, configured dimension and cosine distance |
| `memories_fts` | External-content FTS5 index of content and tags, same row ID |
| `memories_scope`, `memories_session` | Scope/kind and session indexes |
| unique dedup index | `(namespace, access_scope, agent_id, kind, normalized_text)` |

Migration 001 creates records, identity, indexes and FTS synchronization triggers.
Migration 002 creates dimension-specific vec0 and the vector delete trigger.
`PRAGMA user_version` tracks the schema; migrations run within one immediate
transaction. Future schema versions are rejected. Vector virtual tables cannot
carry an ordinary foreign key: API transactions and a memory-delete trigger keep
the shared row-ID relationship consistent. Direct external database mutation is
unsupported. Model/dimension mismatch fails at open/insert/query, never silently
mixes spaces. Changing the space requires a separate DB or future explicit re-embed.

Exact duplicate detection lowercases and collapses whitespace **only**, within
the same namespace, access scope, agent and kind. It returns the existing ID without
merging metadata/provenance. An update colliding with another ID fails. It does not
merge semantically similar or contradictory facts. Expired rows remain stored and
are filtered during retrieval; explicit update/delete controls their lifecycle.

## Ingestion, embeddings and retrieval

`SqliteStore::ingest` accepts an already-extracted `MemoryRecord` and an
`EmbeddingBackend`; `store` also accepts a validated `Embedding` supplied by the
caller. IDs may be generated with `MemoryEntry::new` or explicitly supplied.
Invalid records/embeddings fail before committing. `update` replaces an existing
record and re-embeds it; `store` is the compatibility upsert. Creation time survives
updates. Every upsert currently regenerates the embedding, including metadata-only
updates. Embedding failure leaves the previous committed state intact.

The existing offline FastEmbed provider loads provisioned ONNX/tokenizer files;
no new heavyweight model runtime was added. Production daemon configuration uses
`--model-dir`, an explicit `--ort-dylib` library, and `--model-revision` (default `model-v1`, the existing provisioning
release). The persisted identity includes revision, mean pooling, no prefix and L2
normalization. Operators must change the revision when replacing artifacts; it is
not an automatic cryptographic artifact verifier. Custom providers must encode
weights/version, tokenization, pooling and role-prefix configuration in `model_id`.
Vectors are validated and L2-normalized by the existing `Embedding` API.

`SyntheticEmbedding` is deterministic FNV-1a token hashing with a configurable
dimension. `--synthetic` explicitly enables it without a model or API key. It is
only a test/benchmark fixture and **does not provide production semantic quality**.
There is no implicit fallback from a failed production model to synthetic vectors.

The follow-up fixed Windows loading its old system ONNX DLL instead of the supplied
library. Runtime initialization now uses a canonical explicit path and disables
telemetry; incompatible libraries fail cleanly. Both real-model Rust tests pass.
Full setup and current verification are in [INTEGRATION.md](memd/INTEGRATION.md).

Retrieval pipeline:

1. Validate request and cap top-k (100 at the protocol boundary).
2. For vector/hybrid queries, generate one query embedding in the same space.
3. Restrict namespace, access scope, exact agent ownership, optional session/kind/tag
   and expiry **before** vector candidate selection; use vec0 KNN cosine distance.
4. Keyword retrieval quotes user terms, uses FTS5/BM25, and applies the same filters.
5. Hybrid retrieval combines at most k candidates from each branch using reciprocal
   rank fusion: `sum(1/(rrf_constant + rank))`, rank starting at 1, default constant 60.
6. Sort deterministically by score then internal row ID, return at most k full records,
   score, optional vector distance and provenance. Both hybrid branches share a snapshot.

Legacy `retrieve(query,k)` retains its vector/cosine score contract and 0.3 threshold
in the default namespace/private/unowned scope. Explicit `scope` or `mode` selects
the new retrieval interface without the legacy threshold. Scope defaults are
`namespace=default`, `access_scope=private`, `agent_id=None`; missing agent means
unowned records only, not all agents. Callers choose a shared namespace/access scope
and omit agent ownership to share records deliberately. These are query boundaries,
not authenticated per-user authorization; local transport access must be trusted.

`search_sessions` remains the existing empty session-history hook; it **does not**
search memory facts. Real Hermes session-history lookup remains planned.
`maybe_extract` remains an empty hook, not an automatic extraction pipeline. The
opt-in Python provider has separate read-only original-session history/source
operations; the daemon hook retains its compatibility behavior.

### Example already-extracted inputs

```json
{"v":1,"id":"s1","op":"store","entry":{"content":"User prefers concise Rust examples","kind":"semantic","category":"preference","namespace":"project-a","access_scope":"team","agent_id":"assistant-a","session_id":"session-42","source_ref":"hermes://profile-a/sessions/session-42/messages/uid-7","importance":0.8,"confidence":0.95,"tags":["rust","preferences"]}}
{"v":1,"id":"s2","op":"store","entry":{"content":"Cargo build failed because link.exe was absent; a GNU toolchain enabled the storage tests","kind":"episodic","namespace":"project-a","access_scope":"team","agent_id":"assistant-a","tags":["build","solution"]}}
{"v":1,"id":"r1","op":"retrieve","query":"Rust build solution","k":5,"mode":"hybrid","scope":{"namespace":"project-a","access_scope":"team","agent_id":"assistant-a"}}
```

This accepts memories someone has already selected/extracted. It does not read
every turn, embed every message, invoke an LLM extractor, or claim autonomous learning.
Automatic extraction uses only Hermes' existing review cadence and prompts.
Integration must preserve
Hermes's cached prompt prefix and role alternation; do not rebuild system prompts
mid-conversation or inject synthetic user messages.

## RAM and concurrency strategy

Startup opens SQLite and applies migrations; it never loads the memory corpus or
all embeddings into Rust collections. One mutex-protected connection serializes
DB operations per engine; embedding generation happens before acquiring that lock.
SQLite WAL plus a configurable busy timeout coordinates separate connections/processes.
Writes use immediate transactions and `synchronous=FULL`; hybrid reads use a single
snapshot. WAL does not make this one connection execute parallel reads.

Defaults: 4 MiB SQLite page cache, mmap disabled, temp storage on disk, max top-k 100,
busy timeout 5000 ms. CLI flags: `--cache-kib`, `--max-top-k`, `--busy-timeout-ms`,
`--rrf-constant`. Rust callers use `SqliteConfig`. Returned candidates occupy O(k)
application memory (hybrid at most 2k), plus records/one query vector. SQLite/vec0
still scans vectors and may allocate internal candidate/filter state; cache size
is **not** a hard RSS cap. The local model's runtime/weights add memory separately.
The existing per-client thread transport remains; very high connection counts need
their own resource/throughput evaluation before deployment.

## Repository layout and team ownership

```text
TEAM_A_README.md
memd/
  Cargo.toml, Cargo.lock              existing Rust workspace
  crates/memcore/src/
    types.rs, embedding.rs           existing entry/model interfaces (preserved)
    storage.rs                       existing JSONL and in-memory APIs (preserved)
    sqlite.rs                        persistent typed memory/retrieval engine
    migrations/001.sql               initial relational + FTS schema
  crates/memcore/tests/sqlite_storage.rs
  crates/memd/src/lib.rs, main.rs     existing IPC + SQLite runtime adapter
  clients/python/memory_client.py    backward-compatible v1 client extensions
  clients/python/context_builder.py existing A2 context prototype, unchanged
  clients/python/prime_learning.py  existing A2 artifact prototype, unchanged
  tests/test_persistent_client.py    real-daemon Python/client/transport tests
  scripts/measure.py                existing IPC/RSS tool, preserved
  scripts/benchmark_storage.py      synthetic storage/latency/RSS benchmark
  protocol.md, README.md
```

**A1 owns** SQLite storage/migrations, embeddings, indexing, scoped retrieval,
memory ingestion, provenance and storage benchmarks. **A2 owns** daemon runtime,
context formation, Prime learning artifacts and Hermes integration. Their shared
boundary is structured memory and ranked records over versioned JSON Lines, not
a dependency on an agent loop. Team B owns AVO independently. A1 can run Rust tests
with no Hermes, Prime, context builder or AVO provider.

## Roadmap and evidence status

| Stage | Status / remaining deliverable |
|---|---|
| Weeks 1–2 foundations | Existing workspace, entry types, normalized embeddings, offline model loader, JSONL storage, IPC, mock backend, Python client and measurement tools preserved |
| Persistent-storage stage | Implemented: SQLite migrations, semantic/episodic ingestion, vec0, FTS5, hybrid ranking, metadata/scope/expiry filtering, updates/deletes, dedup, source lookup, persistence |
| Storage verification | Passing standalone deterministic Rust tests and real-daemon Python tests; see validation/benchmark receipt in `memd/VALIDATION.md` |
| Context formation | Existing A2 builder reused by the opt-in MemoryProvider; real plugin discovery and MemoryManager tool-result dispatch pass integration tests without prompt mutation |
| Prime learning | Existing separate artifact prototype unchanged; no Prime learning algorithm added or certified here |
| Hermes extraction/integration | Opt-in native background review through Rust persistence; native cadence, full same-model history and existing routed digest preserved; explicit remember/recall/source/history share storage |
| Production/platform proof | Windows GNU tests pass; real ONNX inference and named pipes/current-user ACL implemented and tested; native MSVC and Unix validation still require their respective hosts |
| Evaluation stage | Synthetic capacity/latency/RSS tooling implemented; real semantic-quality evaluation and larger/production-platform scale claims remain planned |

## Evaluation design

Keep correctness and semantic quality separate. Tests use temporary databases,
deterministic synthetic vectors, real imports and real SQLite/vec0. They cover
initialization/migration, both memory kinds, metadata, dimension/model validation,
vector/keyword/hybrid retrieval, atomic update/delete, duplicate handling, ownership
isolation, restart persistence, concurrent operations, protocol/Python compatibility
and byte-preserved original-transcript fixtures. No Hermes persistence code is edited.

`benchmark_storage.py` runs 1k/10k/100k synthetic records by default, records insertion
median/p95, three restart/warm retrieval trials, vector/keyword/hybrid median/p95,
concurrent hybrid throughput/latency, sampled RSS, main/WAL/SHM file sizes and vec0
allocated pages versus raw float32 bytes. Outputs have unique run directories and
are never deleted or overwritten. Restarts retain OS cache: they are process-cold,
**not** disk-cold. Synthetic vectors establish costs, not semantic recall/accuracy.
Use the same machine/build/dimension/settings for comparisons; report model RSS
and inference costs separately. Real quality requires a labeled semantic/episodic
query set with the production model, recall@k/MRR, conflicting-fact checks and
task-outcome/token-cost comparisons. Do not report these as measured until run.

The 1k/10k/100k synthetic runs have completed; [validation receipt](memd/VALIDATION.md)
records their actual measurements and shared-machine limitations. At 100k, the main
DB was 156.8 MB and peak sampled RSS 18.0 MB, while hybrid latency/concurrency still
show a need for performance work. These results do not establish semantic accuracy.

The follow-up also evaluates actual MiniLM embeddings on a 10-memory/9-query labeled
smoke set: vector recall@3/MRR@3 = 1.000/1.000; hybrid = 1.000/0.926. Real-model peak
sampled RSS is about 228 MB, including weights/inference. A fresh-copy x64 100k
synthetic retrieval repeat peaks near 17 MB RSS but shows about 865 ms hybrid median
and 1.06–1.16 concurrent hybrid RPC/s. These small/shared-machine evaluations expose
limitations rather than certify production quality or scale; full raw receipts are
in [VALIDATION.md](memd/VALIDATION.md).

## Decisions remaining, risks and safeguards

SQLite/sqlite-vec, transcript ownership, independent providers and A1/A2 ownership
are finalized. Remaining decisions: production embedding artifact/revision and
hardware budget; real retrieval-quality thresholds beyond the small labeled smoke
set; artifact promotion policy; per-agent authentication; whether
measured contention warrants a bounded read pool. No custom vector index or allocator.

Risks: brute-force vector cost, model-space drift if operators reuse revisions,
source disappearance under original retention policies, bounded-query but unbounded
client counts, WAL growth under long readers and prototype integration gaps. Safeguards:
identity checks, immutable revision discipline, transactions, conservative dedup,
exact ownership filters, provenance-only storage, explicit expiry, preserved mock/
legacy APIs, read-only original-source access and measurement before scaling claims.

## Final deliverables

The deliverable is an independently testable persistent memory engine and its daemon
adapter, backward-compatible client/protocol, migration and correctness tests,
synthetic/real-model evaluation tooling, secure Windows named pipes, an opt-in
Hermes provider and evidence-based documentation. A complete Prime algorithm,
Team B changes, transcript migration and representative
production semantic-quality certification remain outside this delivery.
