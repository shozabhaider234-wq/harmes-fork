# `memcore` API Reference

Public Rust API and implemented capabilities for `memcore` (`memd/crates/memcore`). Everything documented below matches the current code in [lib.rs](src/lib.rs), [types.rs](src/types.rs), [embedding.rs](src/embedding.rs), and [storage.rs](src/storage.rs); see [DESIGN.md](DESIGN.md) for design rationale and invariants.

---

## What You Can Do Right Now

1. **Create and normalize memory entries (`memcore::types`):** Construct categorized, tagged, timestamped `MemoryEntry` records with pre-normalized `search_text` kept synchronized across mutations.
2. **Generate validated embeddings (`memcore::embedding`):** Run inference through an offline local `FastEmbedBackend` (`all-MiniLM-L6-v2` preset or custom `LocalModelConfig`) or any custom `EmbeddingBackend`, producing L2-normalized, `model_id`-tagged `Embedding` vectors.
3. **Bind entries to required embeddings (`EmbeddedMemory`):** Construct or update memories with atomic content re-embedding so unembedded or stale-vector entries are prevented at compile time.
4. **Rank memories by dense semantic similarity (`find_similar_memories`):** Score candidates with pure dot product, gating strictly on matching `model_id` and dimensionality, filtering by threshold, and returning top `limit` hits.
5. **Persist and query records durably (`memcore::storage`):** Store, batch-upsert, fetch by ID, delete, reload, and atomically compact `EmbeddedMemory` records in a versioned append-only JSONL file (`JsonlStore`) or an ephemeral `InMemoryStore`.

6. **Persistent scoped retrieval (`memcore::sqlite`):** `SqliteStore` owns a separate SQLite/FTS5/sqlite-vec database with typed `MemoryRecord`, model-space validation, provenance, exact dedup, CRUD, vector/keyword/hybrid search, expiry and metadata filtering. This is the normal daemon backend. The JSONL APIs below remain supported library alternatives, not the runtime corpus-loading path.

> **Not yet implemented:** graph edges/cascade retrieval, automatic LLM extraction and real session-history search. Session search and turn extraction remain empty engine hooks (see [protocol.md](../../protocol.md)). Learning artifacts and original Hermes transcripts are not owned by this store.

---

## Crate Setup & Cargo Features

Declared in [Cargo.toml](Cargo.toml):

| Feature | Default | Effect |
|---|---|---|
| `local-fastembed` | Yes | Enables offline `FastEmbedBackend`, with `ort-load-dynamic` and explicit runtime library selection; no build-time runtime download. |
| `--no-default-features` | No | No ONNX runtime; includes all storage APIs and `SyntheticEmbedding`. Bundled SQLite and sqlite-vec require a C compiler/linker. |

All public symbols are re-exported at the crate root (`use memcore::*;` or `use memcore::{...};`), in addition to module paths `memcore::types`, `memcore::embedding`, and `memcore::storage`.

For real models, select a compatible runtime explicitly using
`FastEmbedBackend::load_with_runtime(model_dir, config, library_path)`.
`load_from_dir` reads `ORT_DYLIB_PATH` or expects the platform library in the model
directory; it never searches for an arbitrary system DLL. Incompatible/missing
libraries return `EmbeddingError::ModelLoad`. Telemetry is disabled. See
[INTEGRATION.md](../../INTEGRATION.md) for the working Windows helper and real tests.

---

## Persistent SQLite example

```rust
use memcore::{EmbeddingBackend, MemoryCategory, MemoryEntry, MemoryRecord,
    RetrievalMode, RetrievalScope, SqliteConfig, SqliteStore, SyntheticEmbedding};

# fn example() -> memcore::sqlite::Result<()> {
// Synthetic provider is an offline fixture, not production semantic retrieval.
let provider = SyntheticEmbedding::new(128)?;
let store = SqliteStore::open("extracted-memories.db", provider.model_id(),
    provider.dim(), SqliteConfig::default())?;
let mut record = MemoryRecord::new(MemoryEntry::new("Prefers Rust examples",
    MemoryCategory::Preference, vec!["rust".into()]));
record.session_id = Some("session-42".into());
record.source_ref = Some("hermes://profile-a/sessions/session-42/messages/uid-7".into());
let id = store.ingest(&record, &provider)?;
let hits = store.retrieve("Rust examples", &RetrievalScope::default(), 5,
    RetrievalMode::Hybrid, &provider)?;
assert_eq!(hits[0].record.entry.id, id);
record.entry.set_content("Prefers concise Rust examples");
store.update(&record, &provider)?;
assert!(store.source(&id)?.is_some());
store.delete(&id)?;
# Ok(()) }
```

`store(&record, &embedding)` also accepts validated supplied embeddings; callers
are responsible for binding them to the record's text. `update` requires an existing
ID, while `ingest`/`store` upsert. Updates replace complete metadata, preserve creation
time and atomically regenerate/replace the embedding. `get`/`source` are trusted
administrative lookups by ID, including expired records. `delete` removes the record,
vector and FTS index entry. `stats` reports actual allocated DB/vector pages.

`RetrievalScope` defaults to `default`/`private`/unowned. It optionally filters
agent/session/kind/tag. Ownership restrictions and expiry apply before KNN's top-k;
hybrid uses two bounded branches in a single read snapshot. `RankedMemory` returns
the record, score and optional cosine distance. RRF constant, cache size, timeout
and maximum top-k are in `SqliteConfig`. No corpus-level Rust vector cache exists.

SQLite migration version 1 is the relational/FTS schema; version 2 adds vec0 with
the configured dimension. Newer schemas or incompatible model spaces are rejected
on open. Existing JSONL files are not automatically imported, and unrelated SQLite
databases are rejected before changing schema/journaling. Model revision/config
must be part of the provider's model ID; use a separate database for a changed space.
See [Team A architecture](../../../../TEAM_A_README.md) for lifecycle and provenance.

## Legacy JSONL end-to-end example

```rust
use std::path::Path;
use memcore::{
    EmbeddedMemory, EmbeddingBackend, FastEmbedBackend, JsonlStore, LocalModelConfig,
    MemoryCategory, MemoryStore, find_similar_memories,
};

# fn example() -> Result<(), Box<dyn std::error::Error>> {
// 1. Load an offline ONNX embedding model from disk
//    (provisioned via ./memd/scripts/fetch_models.sh into memd/models/all-MiniLM-L6-v2).
let backend = FastEmbedBackend::load_from_dir(
    Path::new("memd/models/all-MiniLM-L6-v2"),
    LocalModelConfig::all_minilm_l6_v2(),
)?;

// 2. Open a durable JSONL store at a caller-chosen path.
let store = JsonlStore::open("/path/to/memories.jsonl")?;

// 3. Create an embedded memory and persist it.
let mut mem = EmbeddedMemory::new(
    "Prefer strict TypeScript checks in crates/web",
    MemoryCategory::Preference,
    vec!["typescript".into(), "linting".into()],
    &backend,
)?;
mem.add_tag("frontend");
store.upsert(&mem)?;

// 4. Reload from disk and rank against a query embedding.
let reopened = JsonlStore::open("/path/to/memories.jsonl")?;
let candidates = reopened.load_all()?;
let query = backend.embed_query("typescript compiler rules")?;
let hits = find_similar_memories(&query, &candidates, 0.3, 5);

for hit in hits {
    println!("{:.3}: {}", hit.score, hit.memory.entry().content());
}
# Ok(())
# }
```

---

## 1. `memcore::types` — Memory Entries & Normalization

Defined in [types.rs](src/types.rs).

### `MemoryCategory`

```rust
pub enum MemoryCategory {
    Fact,
    Preference,
    Entity,
    Correction,
    Custom(String),
}
```

- `MemoryCategory::as_str(&self) -> &str` — canonical lowercase name (`"fact"`, `"preference"`, `"entity"`, `"correction"`, or the custom string).
- `MemoryCategory::from_extracted(raw: &str) -> Self` — maps extractor aliases (`"facts"`, `"observation"`, `"lesson"`, `"learning"` -> `Fact`; `"preferences"`, `"pref"` -> `Preference`; `"corrections"`, `"fix"`, `"bug"` -> `Correction`; `"entities"` -> `Entity`; unknown -> `Fact`).
- Implements `Display`, `FromStr<Err = ParseCategoryError>` (trims input, rejects empty/whitespace strings, preserves unrecognized non-empty strings as `Custom`), and flat string `Serialize`/`Deserialize`.

### `MemoryEntry`

```rust
pub struct MemoryEntry {
    pub id: String,
    // private: content
    pub category: MemoryCategory,
    // private: tags
    // private: search_text
    pub metadata: HashMap<String, String>, // omitted in JSON when empty
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}
```

- **Constructors & Builders**
  - `MemoryEntry::new(content: impl Into<String>, category: MemoryCategory, tags: Vec<String>) -> Self` — generates `"mem_<unix_ms>_<u64>"`, sets `created_at`/`updated_at` to `Utc::now()`, and computes `search_text`.
  - `.with_id(self, id: impl Into<String>) -> Self`
  - `.with_tags(self, tags: Vec<String>) -> Self` — replaces tags and refreshes `search_text`.
  - `.with_metadata(self, key: impl Into<String>, value: impl Into<String>) -> Self`
  - `.with_timestamps(self, created_at: DateTime<Utc>, updated_at: DateTime<Utc>) -> Self`
- **Accessors**
  - `.content(&self) -> &str`
  - `.tags(&self) -> &[String]`
  - `.searchable_text(&self) -> &str` — zero-allocation borrow of pre-normalized `search_text`.
- **Mutators** (automatically refresh `search_text` and bump `updated_at`)
  - `.set_content(&mut self, content: impl Into<String>)`
  - `.set_tags(&mut self, tags: Vec<String>)`
  - `.add_tag(&mut self, tag: impl Into<String>) -> bool` — no-op returning `false` if tag is already present.
  - `.remove_tag(&mut self, tag: &str) -> bool` — returns `false` if tag was absent.
- **Strict persistence invariant:** `search_text` is always serialized and required on deserialization; records missing `search_text` or carrying an empty `category` fail to deserialize.

### Text Normalization Functions

- `normalize_search_text(text: &str) -> String` — trims, lowercases, and collapses whitespace plus identifier/path delimiters (`-`, `_`, `/`, `\`, `.`, `:`) into single ASCII spaces.
- `normalize_memory_search_text(content: &str, tags: &[String]) -> String` — normalizes `content` and non-empty `tags` and joins them with single spaces.

### `ParseCategoryError`

Unit error returned by `FromStr` when a category string is empty or whitespace-only.

---

## 2. `memcore::embedding` — Vectors, Backends & Similarity Search

Defined in [embedding.rs](src/embedding.rs).

### `EmbedRole` & `EmbeddingError`

```rust
pub enum EmbedRole {
    Query,
    Passage,
}

pub enum EmbeddingError {
    InvalidConfig(String),
    ModelLoad(String),
    Inference(String),
    EmptyModelId,
    EmptyVector,
    NonFiniteValue,
    ZeroNorm,
    DimensionMismatch { expected: usize, actual: usize },
    ModelMismatch { expected: String, actual: String },
}
```

### `Embedding`

Validated, L2-normalized vector bound to the generating `model_id`.

- `Embedding::from_raw(model_id: impl Into<String>, raw: Vec<f32>, expected_dim: usize) -> Result<Self, EmbeddingError>`
  - Validates trimmed non-empty `model_id`, `expected_dim > 0`, `raw.len() == expected_dim`, all finite values, and non-zero L2 norm, then normalizes once at creation time.
- `.model_id(&self) -> &str`
- `.dim(&self) -> usize`
- `.as_slice(&self) -> &[f32]`
- `.into_vec(self) -> Vec<f32>`
- `.matches_model(&self, model_id: &str) -> bool`
- `.similarity(&self, other: &Self) -> Result<f32, EmbeddingError>` — gates on matching `model_id` and dimension, then computes pure dot product.
- **Strict deserialization invariant:** Requires non-empty `model_id`, non-empty finite vector, and unit squared L2 norm within `1e-3`; rejects unnormalized or corrupt persisted vectors instead of silently re-normalizing.

### `EmbeddingBackend` Trait

```rust
pub trait EmbeddingBackend: Send + Sync {
    fn model_id(&self) -> &str;
    fn dim(&self) -> usize;
    fn embed(&self, role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError>;

    // Provided convenience methods:
    fn embed_query(&self, text: &str) -> Result<Embedding, EmbeddingError>;
    fn embed_passage(&self, text: &str) -> Result<Embedding, EmbeddingError>;
    fn embed_passages(&self, texts: &[&str]) -> Result<Vec<Embedding>, EmbeddingError>;
}
```

Requiring `EmbedRole` on `embed` ensures asymmetric models always apply query vs. passage formatting (`embed_passages` calls `embed_passage` sequentially).

### `EmbeddedMemory`

Stage 2 progressive wrapper pairing a `MemoryEntry` with a required `Embedding`:

- `EmbeddedMemory::new(content, category, tags, backend: &dyn EmbeddingBackend) -> Result<Self, EmbeddingError>`
- `EmbeddedMemory::from_entry(entry: MemoryEntry, backend: &dyn EmbeddingBackend) -> Result<Self, EmbeddingError>`
- `.entry(&self) -> &MemoryEntry`
- `.embedding(&self) -> &Embedding`
- `.into_entry(self) -> MemoryEntry`
- `.set_content(&mut self, content: impl Into<String>, backend: &dyn EmbeddingBackend) -> Result<(), EmbeddingError>` — computes the new passage embedding *before* mutating `entry`, so a backend error leaves both old content and old embedding intact.
- `.set_tags(&mut self, tags: Vec<String>)`, `.add_tag(&mut self, tag: impl Into<String>) -> bool`, `.remove_tag(&mut self, tag: &str) -> bool` — delegate to `MemoryEntry` (content embedding is preserved).
- `.reembed(&mut self, backend: &dyn EmbeddingBackend) -> Result<(), EmbeddingError>` — regenerates the vector under `backend`.

### Similarity Functions

- `dot_product(a: &[f32], b: &[f32]) -> f32` — raw slice dot product (no runtime norm checks).
- `cosine_similarity(a: &Embedding, b: &Embedding) -> Result<f32, EmbeddingError>` — delegates to `a.similarity(b)`.
- `find_similar_memories<'a>(query: &Embedding, candidates: &'a [EmbeddedMemory], min_score: f32, limit: usize) -> Vec<SimilarMemory<'a>>`
  - Filters candidates to `emb.matches_model(query.model_id()) && emb.dim() == query.dim()`, scores via dot product, keeps `score >= min_score`, sorts descending (`total_cmp`), and truncates to `limit`.
  - Returns `SimilarMemory<'a> { pub memory: &'a EmbeddedMemory, pub score: f32 }`.

### Local Provisioning (`LocalModelConfig`, `PoolingStrategy`, `FastEmbedBackend`)

```rust
pub enum PoolingStrategy {
    Mean,
    Cls,
}

pub struct LocalModelConfig {
    pub model_id: String,
    pub dim: usize,
    pub pooling: PoolingStrategy,
    pub query_prefix: Option<String>,
    pub passage_prefix: Option<String>,
}
```

- `LocalModelConfig::new(model_id, dim, pooling) -> Self`
- `.with_prefixes(self, query_prefix: Option<String>, passage_prefix: Option<String>) -> Self`
- `LocalModelConfig::all_minilm_l6_v2() -> Self` — preset `"all-MiniLM-L6-v2"`, `384` dims, `PoolingStrategy::Mean`, no prefixes.
- `.validate(&self) -> Result<(), EmbeddingError>`
- `.format_input(&self, role: EmbedRole, text: &str) -> String`
- **`FastEmbedBackend`** *(only with `#[cfg(feature = "local-fastembed")]`)*:
  - `FastEmbedBackend::load_from_dir(model_dir: &Path, config: LocalModelConfig) -> Result<Self, EmbeddingError>` — strictly offline; requires `model.onnx`, `tokenizer.json`, `config.json`, `special_tokens_map.json`, and `tokenizer_config.json` in `model_dir`.
  - `.config(&self) -> &LocalModelConfig`
  - Implements `EmbeddingBackend` (synchronizes `fastembed::TextEmbedding` via an internal `Mutex`).

---

## 3. `memcore::storage` — Durable & In-Memory Persistence

Defined in [storage.rs](src/storage.rs).

### Constants, Modes & Errors

- `pub const STORAGE_FORMAT_VERSION: u32 = 1;`
- `pub enum SyncMode { Durable /* default */, Fast }` — `Durable` syncs appended writes (`sync_data`) and compacted snapshots (`sync_all`) plus best-effort parent directory sync on file creation and atomic compaction; `Fast` flushes buffers and performs atomic renames without per-write `fsync`.
- `pub enum StorageError`:
  - `Io(std::io::Error)`
  - `InvalidConfig(String)`
  - `EmptyPath`
  - `InvalidId`
  - `UnsupportedFormatVersion { expected: u32, actual: u32 }`
  - `CorruptRecord { line: usize, message: String }`
  - `LockPoisoned`

### `MemoryStore` Trait

```rust
pub trait MemoryStore: Send + Sync {
    fn load_all(&self) -> Result<Vec<EmbeddedMemory>, StorageError>;
    fn get(&self, id: &str) -> Result<Option<EmbeddedMemory>, StorageError>;
    fn upsert(&self, memory: &EmbeddedMemory) -> Result<(), StorageError>;
    fn upsert_batch(&self, memories: &[EmbeddedMemory]) -> Result<(), StorageError>;
    fn delete(&self, id: &str) -> Result<bool, StorageError>;
    fn len(&self) -> Result<usize, StorageError>;
    fn is_empty(&self) -> Result<bool, StorageError>;
}
```

- All ID arguments (`get`, `delete`, `upsert`, `upsert_batch`) reject empty or whitespace-only IDs with `StorageError::InvalidId`.
- `upsert_batch` validates every ID before acquiring locks or performing any disk I/O.
- `load_all` returns live entries in deterministic `id` order (`BTreeMap`).

### `InMemoryStore`

- `InMemoryStore::new() -> Self` (and `Default`).
- Thread-safe `RwLock<BTreeMap<String, EmbeddedMemory>>` implementation of `MemoryStore`.

### `JsonlStoreConfig` & `JsonlStore`

```rust
pub struct JsonlStoreConfig {
    pub sync_mode: SyncMode,
    pub min_compact_lines: usize, // default: 64
    pub compact_ratio: usize,     // default: 2
}
```

- `JsonlStoreConfig::new() -> Self` (`Default`: `SyncMode::Durable`, `min_compact_lines: 64`, `compact_ratio: 2`)
- `.with_sync_mode(self, sync_mode: SyncMode) -> Self`
- `.with_compaction(self, min_compact_lines: usize, compact_ratio: usize) -> Self`
- `.validate(&self) -> Result<(), StorageError>` — requires `min_compact_lines > 0` and `compact_ratio > 0`.
- **`JsonlStore`** (caller-supplied path; per-instance `Mutex`; no global cache or hardcoded home path):
  - `JsonlStore::open(path: impl Into<PathBuf>) -> Result<Self, StorageError>`
  - `JsonlStore::open_with_config(path: impl Into<PathBuf>, config: JsonlStoreConfig) -> Result<Self, StorageError>`
  - `.path(&self) -> &Path`
  - `.config(&self) -> JsonlStoreConfig`
  - `.total_log_lines(&self) -> Result<usize, StorageError>`
  - `.reload(&self) -> Result<(), StorageError>` — re-reads and strictly re-validates the file from disk.
  - `.compact(&self) -> Result<(), StorageError>` — atomically rewrites the file with only live entries via a unique sibling temporary file (`.{file_name}.tmp.{pid}.{seq}.{nonce}` -> `rename`).
- **Disk format & guarantees:**
  - Single-line JSON records: `{"op":"upsert","v":1,"memory":{...}}` and `{"op":"delete","v":1,"id":"..."}`.
  - Automatic compaction runs when `projected_total_lines >= max(min_compact_lines, projected_live_entries * compact_ratio)`.
  - In-memory map updates only after disk writes succeed; missing parent directories are created with `0o700` and created/compacted store files use `0o600` on Unix.
  - Deserializing rejects unsupported `v` (`UnsupportedFormatVersion`), missing `search_text`, unnormalized/non-finite vectors, empty IDs, or truncated/corrupt lines (`CorruptRecord`) with no silent `.bak` rollback.

---

## 4. Workspace Context: `memd` IPC Adapter

The sibling daemon crate (`memd/crates/memd`) adapts the durable `MemCoreEngine` to the JSON Lines v1 protocol ([protocol.md](../../protocol.md)); `MockEngine` remains available with `--mock`. Requests are limited to 1 MiB (`{"v":1,"id":...,"op":"..."}`) and support four operations:

- `ping` — health check (`{"status":"ok"}`).
- `store` — accepts an opaque JSON `entry` object and returns an `id`.
- `retrieve` — accepts `query` and `k` (clamped to `100`) and returns `{"hits":[...]}`.
- `search_sessions` — accepts `query` and returns `{"hits":[...]}`.
