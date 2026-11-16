# Persistent memory validation receipt

The native Hermes background-review integration adds a separate current receipt
in [NATIVE_REVIEW.md](NATIVE_REVIEW.md#validation-and-measurements-2026-11-16),
including exact test results, known Windows test failures and storage-only
measurements. The prior backend measurements below remain historical evidence.

## Current follow-up status (2026-11-15)

The ONNX blocker is **fixed**. Directly probing the supplied DLL returns 1.31.1
and a valid API-24 pointer. Windows was loading its own System32 1.17.1 DLL when
the application imported the generic library name; the earlier diagnosis that
the supplied DLL's folder name was misleading was incorrect.

FastEmbed now uses dynamic loading with an explicit canonical path, a declared
existing `ort` dependency and telemetry disabled. Nothing was replaced in Windows
or the existing runtime/model directories. Missing/incompatible libraries fail
cleanly; regression tests verify no panic and no memory DB creation on failed startup.

- **35 Rust tests passed**, zero ignored, including both real MiniLM tests and
  the Windows current-user DACL test. Run `memd/scripts/windows.ps1 -Action test`;
  it sets `ORT_DYLIB_PATH` explicitly and uses the workspace's x64 GNU toolchain.
- **10 Python integration tests passed, 2 native-Unix tests skipped** on Windows,
  with real-model opt-ins enabled. This covers TCP and named pipes, restart,
  concurrency, oversized requests, pipe deadlines/cancellation, first-instance
  protection, invalid runtimes, real plugin discovery, MemoryManager tool dispatch,
  A→B→A scope isolation and read-only transcript/source lookup.
- The **canonical Python wrapper now works** using a PM-built test environment
  and an explicit `/usr/bin:/bin` shell PATH. Its synthetic integration run has
  **5 passed, 0 failed, 7 skipped** (5 model opt-ins plus 2 native-Unix tests);
  real-model opt-ins are deliberately removed by the wrapper's clean environment.

The provider is shipped as an opt-in directory plugin, not activated in a real
profile. Ordinary `sync_turn` remains a no-op; recall is a tool result, the system
prompt stays empty/unchanged, and original transcript writers remain untouched.
Source lookup accesses only the active profile's original session DB in read-only
mode; the logical UID can follow copies/rotations. No Prime/AVO changes.

Real MiniLM evaluation uses a hand-labeled **10-memory, 9-query smoke set**, including
semantic/episodic examples and a distinct obsolete preference. Initial results:

| Retrieval | Recall@3 | MRR@3 |
|---|---:|---:|
| Vector | 1.000 | 1.000 |
| Keyword | 0.556 | 0.556 |
| Hybrid | 1.000 | 0.926 |

The initial debug run reports startup RSS 138,489,856 bytes and sampled peak RSS
247,873,536 bytes, including real weights/inference. This is substantially higher
than synthetic storage-only RSS. Compilation overlapped this diagnostic run, so
its timings are not a controlled comparison. The small set does not certify
production accuracy, conflict resolution, broad recall or task/token savings.
Raw results remain in `benchmark-results/real-model-20261115T162544Z-552d190b`.
The optimized repeat (no competing build) is retained at
`benchmark-results/real-model-20261115T164105Z-c17586fa/report.json`, with the exact
evaluated executable archived beside it. It produces the same quality scores,
535.3 ms process/model startup, 8.37 ms median insertion, 135,495,680-byte startup
RSS and **228,425,728-byte sampled peak RSS**. Nine queries are far too few to claim
representative production accuracy or task/token savings.

### Updated 100k performance measurement

```text
python memd/scripts/benchmark_storage.py --binary memd/target-gnu/release/memd.exe --sizes 100000 --seed-database memd/benchmark-results/20261115T153814Z-a6147a4f/synthetic-100000.db --queries 30 --clients 4 --build-label "x64 GNU Rust 1.100 release; dynamic ONNX feature compiled, synthetic provider; no overlapping build"
```

The script takes a page-wise SQLite backup into a new run directory; the original
database/results remain intact. This measures retrieval on 100k existing synthetic
128-dimensional memories, not insertion or semantic quality. Report/exact executable:
`benchmark-results/20261115T163217Z-0b7b5e1a/`. No build overlapped this run, but the
host is a shared machine and OS caches remain warm. The build/architecture differs
from the older run, so this is not a controlled before/after speedup claim.

Median of three trial statistics (not pooled p95):

| Operation | Median ms | p95 ms |
|---|---:|---:|
| Vector | 588.18 | 712.17 |
| Keyword | 335.11 | 414.57 |
| Hybrid | 864.84 | 944.38 |
| Four-client hybrid | 3,676.41 | 3,987.13 |

Concurrent hybrid throughput was 1.06–1.16 RPC/s. Populated process restart was
524–537 ms; first vector query after restart 219–459 ms. Peak sampled retrieval
RSS was **17,158,144 bytes**, main DB 156,827,648 bytes, vectors 53,686,272 bytes
including 2,486,272 bytes overhead beyond raw float32 data. Fresh-copy WAL size
was zero at final measurement. Insertion was not repeated (`insertion: null`);
the initial-open RSS field in this seeded report does not measure insertion.
This confirms bounded application memory in the fixture while exposing a real
large-corpus latency/serialization limitation; further profiling is needed before
scalability claims. No custom index or allocator was introduced to hide that limit.

See [INTEGRATION.md](INTEGRATION.md) for usable commands, plugin setup and current
platform limits. Native MSVC and Unix proof still require those environments;
`wsl --list --quiet` returned no installed distributions. No host OS was faked.

## Historical first implementation receipt

The following records the earlier measurements and failures. ONNX, named-pipe and
canonical-wrapper blockers described there are superseded by the follow-up above.

Run date: 2026-11-15. Platform: Windows 11 (10.0.26200).

## Inspection and preserved work

The repository already contained `memcore` entry types, normalized model-tagged
embeddings, optional offline FastEmbed, JSONL/InMemoryStore, the `MemoryEngine`
trait, `MockEngine`, durable JSONL adapter, Unix/loopback TCP JSON Lines v1 server,
Python client and `scripts/measure.py`. `search_sessions` and `maybe_extract` were
empty hooks. `TEAM_A_README.md` was absent. Existing uncommitted changes to Cargo,
daemon and documentation were retained; the A2 context/Prime prototypes were left
unchanged. No Team B or original Hermes persistence source was edited.

## Passing checks

### Deterministic standalone Rust suite

```text
rtk proxy cargo +1.92.0-i686-pc-windows-gnu test --manifest-path memd/Cargo.toml --workspace --no-default-features
```

**31 passed**: 20 preserved entry/embedding/legacy-storage tests, 9 SQLite integration
tests and 2 daemon/mock compatibility tests. This uses the installed 32-bit MinGW
compiler and Rust 1.92.0 GNU host. No model, API key, Python agent loop, context
builder, Prime or AVO provider is required.

### Default-feature x64 suite

The existing workspace-local x64 GNU toolchain and ONNX import library permit the
default-feature suite to compile and run non-inference tests:

```powershell
$taskToolchain = (Resolve-Path memd/.rustup-test/toolchains/stable-x86_64-pc-windows-gnu/bin).Path
$taskOrt = (Resolve-Path memd/onnxruntime/onnxruntime-win-x64-1.31.1/lib).Path
$env:PATH = (Resolve-Path memd/msys2/mingw64/bin).Path + ";" + $taskOrt + ";" + $env:PATH
$env:RUSTC = Join-Path $taskToolchain rustc.exe
$env:RUSTDOC = Join-Path $taskToolchain rustdoc.exe
$env:CARGO_TARGET_DIR = (Resolve-Path memd/target-gnu).Path
$env:ORT_LIB_LOCATION = $taskOrt
$env:ORT_PREFER_DYNAMIC_LINK = "1"
& "$taskToolchain/cargo.exe" test --manifest-path memd/Cargo.toml --workspace
```

**32 passed, 2 ignored** (the existing MiniLM inference test and new real-model
SQLite ingestion test). Rust reports 1.100.0 for this pre-existing local toolchain.
This proves SQLite/vec0 operation on x64 GNU Windows as well as i686 GNU Windows;
it does not certify MSVC, Unix or production model inference.

### Real daemon, Python client and context builder

```text
rtk proxy cargo +1.92.0-i686-pc-windows-gnu build --manifest-path memd/Cargo.toml -p memd --no-default-features
rtk proxy C:/Python315/python.exe -m unittest discover -s memd/tests -v
```

**2 passed**, using the real process and TCP transport. Also passed against the
final optimized executable after rebuilding with `--release`, setting `MEMD_BIN`
to `memd/target/release/memd.exe`. Tests include concurrent independent clients,
process termination/restart, existing and extended v1 operations, malformed JSON,
version errors/request IDs, the 1 MiB limit, structured memory ingestion, source
lookup, update/delete, model-storage statistics, and actual `ContextBuilder.build`
consumption of retrieved records within a supplied counting budget. This is an
integration smoke test, not proof of a production tokenizer or Hermes-loop wiring.

The daemon reports **SQLite 3.48.0 and sqlite-vec v0.1.6**. sqlite-vec is statically
linked/registered; tests execute real vec0 queries, not mocked SQL.

### Coverage map

| Required behavior | Passing evidence |
|---|---|
| Initialization and migration | `initialization_migration_and_model_identity` creates v2 and upgrades a v1 fixture |
| Semantic and episodic inserts, metadata | `semantic_episodic_metadata_provenance_and_restart` round-trips complete typed payload |
| Vector insertion/dimensions/model validation | `dimensions_models_invalid_records_and_atomic_failures` |
| Vector, FTS5 and hybrid ranking | `vector_keyword_hybrid_ranking_and_limits` |
| Update/vector consistency and delete cleanup | `updates_delete_and_vector_fts_consistency`, actual vector row counts |
| Conservative deduplication | `conservative_deduplication_respects_ownership_and_kind` |
| Namespace/access scope/agent/session/kind/tag/expiry | `scopes_filter_before_top_k_and_expiry_filters_every_mode` |
| Persistence across restart | Rust reopen tests and Python process restart test |
| Concurrent operations | Four Rust workers with separate connections; four real Python clients |
| JSON RPC / Python compatibility | Both daemon tests and Python client tests |
| Transcript preservation | Byte-identical JSONL and session-DB fixtures; unrelated DB refusal; no source edits |

`rustfmt --edition 2021 --config skip_children=true --check` passes on changed Rust
source/test files. Whole-workspace formatting was inspected but not applied to
previously unformatted unrelated files. `git diff --check` passes, with only Git's
line-ending warnings. Python AST parsing passed for the client, benchmark and tests.

## Failures and environment limitations

- Native default MSVC build initially failed: **`link.exe` not found**. GNU builds
  above provide native Windows storage verification without changing the core design.
- i686 default-feature check fails because `ort-sys` has **no prebuilt binaries for
  `i686-pc-windows-gnu`**. The feature-free suite works; x64 default features compile.
- Both explicitly enabled real-model tests fail at runtime: **requested ORT API 24,
  available APIs 1 and 17, runtime reports ONNX Runtime 1.17.1**. The existing folder
  is named `onnxruntime-win-x64-1.31.1`, but that name does not match the loaded DLL's
  reported API. The existing MiniLM semantic-ranking test fails, as does:

  ```text
  cargo test --manifest-path memd/Cargo.toml -p memcore --test sqlite_storage offline_model_ingestion_and_sqlite_retrieval -- --include-ignored
  ```

  A compatible ONNX Runtime DLL/import library is needed before production inference
  can be certified. No DLL/model file was replaced and no synthetic result is
  presented as semantic-quality proof. The failures occur during model initialization,
  before SQLite ingestion.
- `scripts/run_tests.sh memd/tests/test_persistent_client.py` was attempted through
  Git Bash, but the available shell PATH could not find `dirname`, causing its
  activation-script lookup to fail. Neither available Python interpreter has pytest.
  The independent stdlib `unittest` suite above passes; the full Hermes suite was
  not run. No bare pytest or raw pip/uv environment modification was used.
- Optional i686 `cargo clippy ... -D warnings` could not run because that minimal
  toolchain has no Clippy component installed. This is not a test failure.
- The first storage benchmark attempt hit **host Python SQLite lacking `dbstat`**.
  Measurement now uses the daemon's bundled SQLite through `storage_stats`. The
  initial dataset/logs are retained in `benchmark-results/20261115T153633Z-e877f885`.

## Actual synthetic benchmark

```text
rtk proxy C:/Python315/python.exe memd/scripts/benchmark_storage.py --binary memd/target/release/memd.exe --sizes 1000 10000 100000 --queries 30 --clients 4
```

All three sizes completed. Build: optimized Rust 1.92.0 i686 GNU, no default features,
synthetic 128-dimensional token-hash vectors, top-k 10, 4 MiB page cache, WAL/FULL,
default RRF 60. Insertion is single-client, committing each record. Each of three
retrieval trials restarts the process, measures one first vector query, then 30 warm
queries per mode and 120 hybrid requests across four concurrent clients. RSS is
sampled every 20 ms; peaks can miss shorter-lived allocations.

These are **exploratory timings on a shared machine**. Other Rust compilation
overlapped part of the run. OS page caches were not flushed. The large first-trial
outlier is retained. Do not treat this as a controlled baseline comparison, disk-cold
benchmark, production semantic-quality evaluation, or scalability guarantee.

Results: [raw report](benchmark-results/20261115T153814Z-a6147a4f/report.json),
[build/environment receipt](benchmark-results/20261115T153814Z-a6147a4f/environment.json).
Every database, log and per-size report is retained. The exact benchmark executable
is archived beside them with its SHA-256. Later source changes add foreign-DB refusal
and version statistics without changing the retrieval algorithm. Runtime binaries
and raw benchmark artifacts are ignored by Git; this receipt records their findings.

| Memories | Main DB bytes | Vector allocated bytes | Raw float32 bytes | Vector overhead bytes | Peak sampled retrieval RSS bytes | Insertion median / p95 ms |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 1,482,752 | 561,152 | 512,000 | 49,152 | 10,608,640 | 4.97 / 13.31 |
| 10,000 | 15,618,048 | 5,488,640 | 5,120,000 | 368,640 | 14,258,176 | 5.24 / 15.47 |
| 100,000 | 156,803,072 | 53,686,272 | 51,200,000 | 2,486,272 | 17,969,152 | 4.55 / 9.79 |

The following are medians **of the three trial statistics**, not a pooled p95:

| Memories | Vector median / p95 ms | Keyword median / p95 ms | Hybrid median / p95 ms | 4-client hybrid median / p95 ms |
|---:|---:|---:|---:|---:|
| 1,000 | 5.51 / 8.78 | 5.12 / 8.33 | 8.15 / 12.32 | 28.45 / 35.97 |
| 10,000 | 60.19 / 69.09 | 44.49 / 110.38 | 107.89 / 158.50 | 251.30 / 503.44 |
| 100,000 | 267.54 / 341.27 | 139.93 / 153.74 | 381.62 / 420.47 | 1,619.74 / 2,285.50 |

| Memories | Empty DB startup ms | Populated process restart range ms | First vector query after restart range ms |
|---:|---:|---:|---:|
| 1,000 | 1,064.4 | 602.4–798.6 | 11.7–23.0 |
| 10,000 | 645.8 | 512.5–553.1 | 59.9–97.6 |
| 100,000 | 521.7 | 511.1–678.1 | 221.3–6,986.4 |

At 100k, four-client hybrid throughput was 1.86–2.55 RPC/s across trials. The first
vector trial had median 1,695.1 ms and p95 4,991.7 ms; later trials had medians
267.5 and 250.4 ms. WAL files remained about 4.2–4.7 MB, plus 32,768-byte SHM files;
main DB size above excludes those sidecars. This implementation keeps application
results bounded and startup RSS low in this fixture, but retrieval latency and
serialized concurrency need performance work before large-scale claims.

## Remaining work

Provide a compatible production ONNX Runtime and rerun both ignored model tests;
validate on native MSVC and Unix; implement production Windows named pipes/ACLs;
measure production-model quality and inference RSS; repeat performance measurements
under controlled load and real access patterns. Selective Hermes extraction triggers,
session-history lookup, source navigation and full Hermes-loop context integration
remain planned. No Prime learning algorithm, Team B edits, transcript migration or
automatic message extraction is claimed.
