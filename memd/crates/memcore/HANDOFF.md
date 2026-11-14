# `memcore` Progressive Development Log

## Working Model & Guardrails
- **Scope:** `./jcode` (read-only reference), `./memd` (active workspace), and external semantic-search repo (`/home/hammad/code/rust`). Team A, member `a0` (`README.md`).
- **Role of `memd/crates/memd/PLAN.md`:** Rough end-state sketch to evaluate critically—NOT a rigid spec to copy blindly, and NOT to be edited.
- **Strict progressive rule:**
  1. We build one module at a time inside `memd/crates/memcore`.
  2. Read ONLY the reference files/lines specified in the user's prompt for the active step. Do NOT scan `./jcode` or design modules for future steps.
  3. First answer the active step's `PENDING` questions from first principles and align with the user on design trade-offs before writing code.
  4. When a step's code + tests pass, finalize its `ANSWER`, `INVARIANT`s, and divergences under `## Stages`.
- **Divergence convention:** Mark intentional departures inline with `[!= jcode]`, `[!= PLAN]`, or `[!= jcode, PLAN]` (do NOT revert).

---

## Stages

### 1. P0: What is a memory entry? (`memcore::types` — [types.rs](src/types.rs), [lib.rs](src/lib.rs)) — Done
- **QUESTION:** What is a memory entry?
- **ANSWER:** Model-extracted characteristics of useful facts (`id`, `content`, `category`, `tags`, `search_text`, `metadata`, `created_at`, `updated_at`).
- **RESOLVED (`search_text` lifecycle & invariants):**
  - *Is `search_text` populated on every consumption, or stored normalized?* Stored pre-normalized (`normalize_memory_search_text(&content, &tags)`) once on creation (`MemoryEntry::new`) and refreshed automatically on mutation; borrowed via `searchable_text(&self) -> &str` with zero allocation.
  - *Why keep both `content` and `search_text`?* `content` preserves original casing/punctuation/delimiters for display, prompts, and embeddings; `search_text` lowercases and collapses whitespace + code/path delimiters (`-`, `_`, `/`, `\`, `.`, `:`) for lexical matching.
- **Intentional Divergences from `PLAN.md` and `jcode` (Do NOT revert):**
  1. **Separate crate:** Implemented in `memd/crates/memcore` (`memcore::types`), decoupled from the `memd` daemon binary crate.
  2. **Encapsulation for invariant safety:** Unlike `PLAN.md` and `jcode` (where all fields are `pub`), `content`, `tags`, and `search_text` are private and mutated only through `set_content`, `set_tags`, `add_tag`, `remove_tag`, and `with_tags` so `search_text` and `updated_at` can never drift out of sync. (`id`, `category`, `metadata`, `created_at`, `updated_at` remain `pub`.)
  3. **`MemoryCategory` enum instead of raw `String`:** Uses `MemoryCategory { Fact, Preference, Entity, Correction, Custom(String) }` with flat string serde, `FromStr` (rejecting empty strings), and `from_extracted` alias mapping.
  4. **Strict greenfield persistence invariant:** `search_text` is required on deserialization (no `#[serde(default)]`, no `skip_serializing_if`, no read-time `Cow` fallback, and no load-time repair pass). Future external IPC `store` requests will deserialize into a separate input DTO and construct via `MemoryEntry::new`.
  5. **Step 1 boundary:** Deferred fields (`embedding`, `embedding_model`, `active`, `superseded_by`) are intentionally absent until their respective stages decide whether and how they belong.

---

### 2. P0: Introducing embeddings (`memcore::embedding` — [embedding.rs](src/embedding.rs), [lib.rs](src/lib.rs), [Cargo.toml](Cargo.toml)) — Done
- **QUESTION:** How should embeddings represent and find similarity among memories, and what backend abstraction should `memcore` use?
- **ANSWER:** A runtime-agnostic `EmbeddingBackend` trait producing validated, model-tagged `Embedding` vectors, a progressive `EmbeddedMemory` wrapper promoting `MemoryEntry` with a required vector, pure dot-product similarity ranking (`find_similar_memories`), and an offline `FastEmbedBackend` configured via `LocalModelConfig`.
- **INVARIANT (Generation-time normalization) `[!= jcode, PLAN]`:** Validate (non-empty, finite, non-zero norm, expected dim) and L2-normalize vectors once at generation time so all similarity scoring uses a single dot product with zero downstream norm checks or calculations (fixing `jcode`/`PLAN`'s repeated query-time normalization and unvalidated raw-slice scoring).
- **INVARIANT (Vector identity & roles) `[!= jcode, PLAN]`:** Every vector binds a required `model_id` (strict greenfield, no guessed fallback for untagged vectors) and requires a `Query`/`Passage` role on every embed call so incompatible vector spaces never mix and model prefixes cannot be bypassed (fixing `jcode`/`PLAN`'s legacy model fallback and unformatted public raw-embed path).
- **PROGRESSIVE INLINE WRAPPER (`EmbeddedMemory`) `[!= jcode, PLAN]`:** Following progressive development, keep Stage 1's `MemoryEntry` pure and untouched, and promote it in Stage 2 into an encapsulated `EmbeddedMemory` wrapper pairing `MemoryEntry` with a required, non-optional `Embedding` (fixing `jcode`/`PLAN`'s nullable, uncoordinated fields on `MemoryEntry`, which allow unembedded entries or stale vectors after edits—here, unembedded memories fail at compile time and content updates re-embed atomically); `README.md`'s `embedding_ref` indirection is avoided as a premature optimization since IPC carries generated context rather than entries and no 1,000+ memory-entry search scenario yet justifies separating vectors from entries.
- **PROVISIONAL RUNTIME & PROVISIONING `[!= jcode, PLAN]`:** Start with `fastembed` (which handles ONNX input wiring, tokenization, and pooling out of the box) behind a default-on Cargo feature gate so pure-Rust builds stay runtime-free, loaded strictly offline from a local `model_dir` + explicit model config behind a runtime-agnostic trait for modularity and model switching (avoiding `jcode`'s hidden runtime downloads, global singletons, and `jcode`/`PLAN`'s manual ONNX tensor/pooling code).
- **AVOIDED NOW — FURTHER RESEARCH (implement only if evaluation shows a substantial difference):** (1) embedding caching, (2) native batching (use a simple single-item loop for now), and (3) final model runtime (`fastembed` is provisional; compare `fastembed`, `tract-onnx`, direct `ort`, and remote backends).
