# `memd`

Local Rust memory core (`crates/memcore`) and IPC daemon (`crates/memd`).

## Embedding Model Setup

**Prerequisite:** Rust `>= 1.92` (`rustup update stable`, or install via `https://sh.rustup.rs`).
On the first Cargo build with default features, `ort` automatically downloads prebuilt ONNX Runtime binaries—no manual ONNX installation is required.

1. **Download the model (`all-MiniLM-L6-v2`, ~80 MB archive -> `memd/models/all-MiniLM-L6-v2/`):**
   ```bash
   ./memd/scripts/fetch_models.sh
   ```
   *(From inside `memd/`, run `./scripts/fetch_models.sh`. On Windows, run in Git Bash or WSL.)*

2. **Verify offline inference:**
   ```bash
   cargo test --manifest-path memd/Cargo.toml -- --include-ignored
   ```

`memcore` loads `model.onnx`, `tokenizer.json`, `config.json`, `special_tokens_map.json`, and `tokenizer_config.json` strictly offline from disk via `FastEmbedBackend::load_from_dir` (no runtime Hugging Face downloads).

### Embedding Similarity Example

Run [crates/memcore/examples/embedding_similarity.rs](crates/memcore/examples/embedding_similarity.rs) to embed sample memories, check unit L2 norms, and print cosine similarity rankings:

```bash
cargo run --manifest-path memd/Cargo.toml -p memcore --example embedding_similarity

# Or score a custom query against the sample memories:
cargo run --manifest-path memd/Cargo.toml -p memcore --example embedding_similarity -- "your query here"
```

### Options

- **`MEMD_MODEL_DIR`**: Override the model directory used by tests, examples, or custom setups (default: `memd/models/all-MiniLM-L6-v2`).
- **`MEMD_MODEL_REPO`**: Override the GitHub `owner/repo` release source (default: `hammaddaftab/sematic-search`; optional first script arg selects tag, default `model-v1`).
- **Pure-Rust build (no ONNX/C++ runtime):** `cargo test --manifest-path memd/Cargo.toml --no-default-features -p memcore`

### Publishing the Model Asset (Maintainers)

If mirroring the release asset to another repository's GitHub Releases:

```bash
tar -czvf all-MiniLM-L6-v2.tar.gz -C memd/models/all-MiniLM-L6-v2 .
# Upload all-MiniLM-L6-v2.tar.gz under release tag `model-v1`
```

## Documentation

- [crates/memcore/API.md](crates/memcore/API.md) — Public `memcore` API and examples.
- [crates/memcore/DESIGN.md](crates/memcore/DESIGN.md) — Stage-by-stage design invariants.
- [protocol.md](protocol.md) — `memd` IPC protocol v1 and measurement guide.
