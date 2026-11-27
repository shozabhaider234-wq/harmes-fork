# `memd`

Native background extraction integration: [audit and setup](NATIVE_REVIEW.md).
The reviewer retains Hermes' prompts, cadence and history; Rust replaces native
memory persistence. Existing Markdown is preserved for explicit migration/rollback.

Local Rust memory core (`crates/memcore`) and IPC daemon (`crates/memd`).

The existing opt-in Python Hermes bridge supports adaptive `brief`/`normal`/
`deep`/`exhaustive` recall ceilings and optional low-utility omission. See
[integration details](INTEGRATION.md#the-memd_memory-provider-tool-supports) for
configuration and the current conservative fallback limits. A reproducible
synthetic context-packing comparison is available at
`python memd/scripts/benchmark_context_builder.py`.
Hermes uses cached provider usage plus its cached model context limit for live
budgeting, then validates the API-copy prompt before send. Exact provider tokenizers
are not currently exposed to this integration, so diagnostics report a padded
Hermes estimate. Explicit supersession and historical retrieval are described in
[the integration guide](INTEGRATION.md#live-context-budget-and-historical-retrieval).

## Embedding Model Setup

**Prerequisite:** Rust `>= 1.92` (`rustup update stable`, or install via `https://sh.rustup.rs`).
Default builds use explicitly loaded ONNX Runtime. Provision an architecture-matched
library and pass `--ort-dylib PATH` (or `ORT_DYLIB_PATH` for library/tests). Builds do
not download runtime binaries. See [working integration instructions](INTEGRATION.md).

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

### Options

- **`MEMD_MODEL_DIR`**: Override the model directory used by tests, examples, or custom setups (default: `memd/models/all-MiniLM-L6-v2`).
- **`MEMD_MODEL_REPO`**: Override the GitHub `owner/repo` release source (default: `hammaddaftab/sematic-search`; optional first script arg selects tag, default `model-v1`).
- **Without ONNX:** `cargo test --manifest-path memd/Cargo.toml --workspace --no-default-features`. Bundled SQLite and sqlite-vec still require a C compiler/linker. No separately installed SQLite or dynamically loaded extension is needed.

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
- [PRIME_LEARNING.md](PRIME_LEARNING.md) — deterministic Prime-inspired artifact store,
  callback task harness, test command, and current Hermes runtime integration limits.

## Standalone daemon use

For standalone protocol work, run the durable `memcore` engine with an explicit local store:

```bash
cargo run --manifest-path memd/Cargo.toml -p memd -- --socket /tmp/hermes-memd.sock --store /path/to/memories.db --model-dir memd/models/all-MiniLM-L6-v2 --model-revision model-v1 --ort-dylib /path/to/libonnxruntime.so
```

Use `--pipe hermes-memd` on Windows: current-user-only DACL, local clients only,
and Python deadline/cancellation support. Use `--tcp 127.0.0.1:PORT` for development;
TCP is loopback-only. `--mock` selects the in-memory protocol stub.

For the Hermes integration, provider setup installs the local assets and sessions
start the daemon automatically; see [INTEGRATION.md](INTEGRATION.md). The provider
in `clients/python/` supplies explicit memory ingestion,
budgeted recall through a tool result, and read-only original session/source lookup.
Its discovery, MemoryManager dispatch and A→B→A profile isolation are integration-tested.
See [INTEGRATION.md](INTEGRATION.md) for installation, configuration and one-command
Windows checks (`./memd/scripts/windows.ps1 -Action test`).

The normal backend is persistent SQLite + sqlite-vec + FTS5. Choose a **new,
separate memory database**, never Hermes `state.db`, a transcript, existing JSONL
store or a learning-artifact file. Existing files are not migrated. The engine
rejects unrelated populated SQLite databases before changing their schema/journaling.
Hermes retains its original transcript storage, which already includes its own
SQLite session DB in this fork. Only extracted memories and opaque source references
belong in the new database.

No-model smoke test (synthetic hashing is not production semantic retrieval):

```bash
cargo run --manifest-path memd/Cargo.toml -p memd --no-default-features -- --tcp 127.0.0.1:7777 --store /path/to/synthetic.db --synthetic --dimension 128
```

`--model-revision` identifies immutable provisioned artifacts; change it when model
files change. The production preset includes pooling/prefix/normalization identity.
Dimensions/model identities are bound to one DB and mismatches fail. Library users
can provide any `EmbeddingBackend` without a paid API.

Configure `--cache-kib 4096`, `--busy-timeout-ms 5000`, `--max-top-k 100` and
`--rrf-constant 60` as needed. WAL/FULL transactions protect committed records;
one connection serializes DB operations per daemon. Retrieval is bounded and does
not load the entire corpus into Rust collections. SQLite/vec0 working memory and
model weights still need measurement; cache configuration is not an RSS guarantee.

After building the daemon, run the standalone Python client tests:

```bash
# Set MEMD_BIN if the executable is not memd/target/debug/memd[.exe].
python -m unittest discover -s memd/tests -v
python memd/scripts/benchmark_storage.py --binary memd/target/release/memd --sizes 1000 10000 100000
```

Benchmark runs retain unique database/log/report directories under
`memd/benchmark-results`; they use synthetic 128-dimensional vectors, three
restart/warm trials, and real sampled RSS. Process restart does not flush OS caches.
See [Team A architecture](../TEAM_A_README.md) and [validation receipt](VALIDATION.md).
