# `memcore` Progressive Development Log (Completed Stages Only)

## Working Model & Guardrails
- **Scope:** `./jcode` (read-only reference) and `./memd` (active workspace). Team A, member `a0` (`README.md`).
- **Role of `memd/crates/memd/PLAN.md`:** Rough end-state sketch to evaluate critically—NOT a rigid spec to copy blindly, and NOT to be edited.
- **Strict progressive rule:**
  1. We build one module at a time inside `memd/crates/memcore`.
  2. Read ONLY the reference files/lines specified in the user's prompt for the active step. Do NOT scan `./jcode` or design modules for future steps.
  3. First answer the active step's `PENDING` questions from first principles and align with the user on design trade-offs before writing code.
  4. When the step's code + tests pass, append its resolved `ANSWER`, invariants, and intentional divergences from `PLAN.md` / `jcode` to this file.

---

## Completed Stages

### 1. P0: What is a memory entry? (`memcore::types` — [types.rs](src/types.rs), [lib.rs](src/lib.rs))
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