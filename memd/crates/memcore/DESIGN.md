# `memcore` Progressive Development Log

**Progressive Approach:** [memcore](/memd/crates/memcore) is built incrementally, one cohesive module at a time. Each stage introduces only the types and invariants that the active layer requires—extending earlier data structures progressively (for example, wrapping `MemoryEntry` in `EmbeddedMemory` in Stage 2 rather than adding nullable future-stage fields prematurely)—and finalizes its contracts below once code and tests pass. Architectural references include [README.md](/README.md) (Team A design) and `jcode`; intentional departures from `jcode` are marked inline with `[!= jcode]` and should not be reverted.

---

## Stages

### 1. P0: What is a memory entry? (`memcore::types` — [types.rs](/memd/crates/memcore/src/types.rs), [lib.rs](/memd/crates/memcore/src/lib.rs)) — Done
- **QUESTION:** What is a memory entry?
- **ANSWER:** Model-extracted characteristics of useful facts (`id`, `content`, `category`, `tags`, `search_text`, `metadata`, `created_at`, `updated_at`).
- **RESOLVED (`search_text` lifecycle & invariants):**
  - *Is `search_text` populated on every consumption, or stored normalized?* Stored pre-normalized (`normalize_memory_search_text(&content, &tags)`) once on creation (`MemoryEntry::new`) and refreshed automatically on mutation; borrowed via `searchable_text(&self) -> &str` with zero allocation.
  - *Why keep both `content` and `search_text`?* `content` preserves original casing/punctuation/delimiters for display, prompts, and embeddings; `search_text` lowercases and collapses whitespace + code/path delimiters (`-`, `_`, `/`, `\`, `.`, `:`) for lexical matching.
- **Design Choices & Divergences:**
  1. **Separate core crate:** Implemented in [memcore](/memd/crates/memcore) (`memcore::types`), decoupled from the [memd](/memd/crates/memd) daemon binary crate.
  2. **Encapsulation for invariant safety `[!= jcode]`:** Unlike `jcode` (where all fields are `pub`), `content`, `tags`, and `search_text` are private and mutated only through `set_content`, `set_tags`, `add_tag`, `remove_tag`, and `with_tags` so `search_text` and `updated_at` can never drift out of sync. (`id`, `category`, `metadata`, `created_at`, `updated_at` remain `pub`.)
  3. **`MemoryCategory` enum:** Represents categories with `MemoryCategory { Fact, Preference, Entity, Correction, Custom(String) }` and `from_extracted` alias mapping; uses flat string serialization and trimmed non-empty `FromStr` validation (`ParseCategoryError`), unlike `jcode`'s externally tagged `Custom` serde and `Infallible` `FromStr` `[!= jcode]`.
  4. **Strict greenfield persistence invariant `[!= jcode]`:** `search_text` is required on deserialization (no `#[serde(default)]`, no `skip_serializing_if`, no read-time `Cow` fallback, and no load-time repair pass, unlike `jcode`). Future external IPC `store` requests will deserialize into a separate input DTO and construct via `MemoryEntry::new`.
  5. **Stage 1 scope boundary:** Later-stage fields (`active`, `superseded_by`, embeddings) are intentionally excluded from `MemoryEntry` until their respective stages determine whether and how they belong.

---

### 2. P0: Introducing embeddings (`memcore::embedding` — [embedding.rs](/memd/crates/memcore/src/embedding.rs), [lib.rs](/memd/crates/memcore/src/lib.rs), [Cargo.toml](/memd/crates/memcore/Cargo.toml)) — Done
- **INVARIANT (Generation-time normalization) `[!= jcode]`:** Validate (non-empty, finite, non-zero norm, expected dim) and L2-normalize vectors once at generation time so all similarity scoring uses a single dot product with zero downstream norm checks or calculations (fixing `jcode`'s repeated query-time normalization and unvalidated raw-slice scoring).
- **INVARIANT (Vector identity & roles) `[!= jcode]`:** Every vector binds a required `model_id` (strict greenfield, no guessed fallback for untagged vectors) and requires a `Query`/`Passage` role on every embed call so incompatible vector spaces never mix and model prefixes cannot be bypassed (fixing `jcode`'s legacy model fallback and unformatted public raw-embed path).
- **PROGRESSIVE INLINE WRAPPER (`EmbeddedMemory`) `[!= jcode]`:** Following progressive development, keep Stage 1's `MemoryEntry` pure and untouched, and promote it in Stage 2 into an encapsulated `EmbeddedMemory` wrapper pairing `MemoryEntry` with a required, non-optional `Embedding` (fixing `jcode`'s nullable, uncoordinated fields on `MemoryEntry`, which allow unembedded entries or stale vectors after edits—here, unembedded memories fail at compile time and content updates re-embed atomically); [README.md](/README.md)'s `embedding_ref` indirection is avoided as a premature optimization since IPC carries generated context rather than entries and no 1,000+ memory-entry search scenario yet justifies separating vectors from entries.
- **PROVISIONAL RUNTIME & PROVISIONING `[!= jcode]`:** Start with `fastembed` (which handles ONNX input wiring, tokenization, and pooling out of the box) behind a default-on Cargo feature gate so pure-Rust builds stay runtime-free, loaded strictly offline from a local `model_dir` + explicit model config behind a runtime-agnostic trait for modularity and model switching (avoiding `jcode`'s hidden runtime downloads, global singletons, and manual ONNX tensor/pooling code).
- **AVOIDED NOW — FURTHER RESEARCH (implement only if evaluation shows a substantial difference):** (1) embedding caching, (2) native batching (use a simple single-item loop for now), and (3) final model runtime (`fastembed` is provisional; compare `fastembed`, `tract-onnx`, direct `ort`, and remote backends).

---

### 3. P0: Persisting memories (`memcore::storage` — [storage.rs](/memd/crates/memcore/src/storage.rs), [lib.rs](/memd/crates/memcore/src/lib.rs), [Cargo.toml](/memd/crates/memcore/Cargo.toml)) — Done
> no extraordinary decisions from the standard
