# Running the completed local memory integration

Native Hermes background memory review is now supported through the same selected
provider. Read [NATIVE_REVIEW.md](NATIVE_REVIEW.md) before switching an existing
profile: it documents exact review behavior, explicit import/export and rollback.
There is no automatic Markdown import, dual-write, or custom extraction trigger.
Rebuild the daemon and update the installed plugin together: the adapter requires
the backward-compatible v1 `inventory` and `compare_exchange` additions.

## Explicit ONNX Runtime loading

The previous failure was a **DLL search-order bug**: the supplied workspace DLL
really is ONNX Runtime 1.31.1 and supports API 24. Windows selected its older
System32 runtime (1.17.1) when executables imported the generic DLL name. Merely
adding the correct directory to PATH did not fix that search order.

FastEmbed now uses `ort-load-dynamic` and the existing `ort` dependency is declared
directly to initialize an explicitly selected, canonical library path. It checks
runtime compatibility before session initialization and disables telemetry. One
library/environment is shared per process; changing its path requires a restart.
There is no automatic binary download during builds. Missing/incompatible libraries
return errors, with no synthetic fallback and no system DLL replacement.

Use `--ort-dylib PATH` on the daemon. Library users can call
`FastEmbedBackend::load_with_runtime(model_dir, config, library_path)`;
`load_from_dir` uses existing `ORT_DYLIB_PATH`, or a library inside the model directory.
Use an architecture-matched library (`onnxruntime.dll`, `libonnxruntime.so`, or
`libonnxruntime.dylib`) providing API >=24. Model revision/configuration remains a
separate embedding-space identity. Inference uses two intra-op threads to bound CPU
usage; weights/runtime still dominate real-model RSS.

On this workspace's Windows setup:

```powershell
# Uses the existing local x64 GNU compiler/toolchain and actual workspace DLL.
./memd/scripts/windows.ps1 -Action test
./memd/scripts/windows.ps1 -Action build -Release

./memd/scripts/windows.ps1 -Action run -Release -DaemonArgs @(
  '--pipe', 'hermes-memd',
  '--store', 'memd/data/real-memories.db',
  '--model-dir', 'memd/models/all-MiniLM-L6-v2',
  '--model-revision', 'model-v1'
)
```

The helper temporarily sets process-local compiler/runtime variables and restores
them afterward. Override `-RuntimeLibrary` / `-ToolchainBin` for another installation.
The general cross-platform invocation is:

```text
cargo run --manifest-path memd/Cargo.toml -p memd -- --socket /tmp/hermes-memd.sock --store /path/to/extracted.db --model-dir /path/to/model --ort-dylib /path/to/libonnxruntime.so
```

Always use a separate extracted-memory database, not Hermes `state.db` or artifacts.

## Windows named pipes

`--pipe NAME` serves the same v1 JSON Lines byte stream. The protected DACL grants
access to the current process user's SID only; remote clients are rejected. The
first-instance flag prevents an existing pipe from being silently taken over.
The server caps instances at 32 and waits for capacity. Connections use 8 KiB
buffers and preserve request IDs, structured errors and the 1 MiB request limit.

```python
from memd.clients.python.memory_client import MemdClient
with MemdClient(pipe_name="hermes-memd", timeout=5) as client:
    print(client.ping())
```

Python uses overlapped I/O with cancellation/deadlines, including timeout cleanup.
Unix sockets remain supported; loopback TCP remains useful for development and
measurement. No remote TCP interface is enabled.

## Opt-in Hermes plugin

`memd/clients/python/` is a self-contained directory plugin. Install **that directory's
contents** into `<active HERMES_HOME>/plugins/hermes-memd/` using your normal plugin
deployment workflow. It implements Hermes' existing `MemoryProvider` ABC and
`register(ctx)` contract; no files under core `plugins/memory/` or the agent loop
are changed. Keep the installed copy updated when changing this checkout.

Then, from the Hermes checkout, choose `hermes-memd` in `hermes memory setup`.
Setup builds the daemon from the checkout, installs the existing local model and
runtime into `<HERMES_HOME>/memd/`, and writes the default profile configuration.
The next Hermes session starts the daemon in the background. Later sessions need
only the usual Hermes launch; no manual endpoint entry or daemon terminal is needed.
Setup preserves an existing namespace, retrieval budget, endpoint and configured
`store_path` when rerun. It copies runtime assets, never the memory database; a new
profile defaults to `<HERMES_HOME>/memd/memories.db`. If you previously launched
memd with a database outside that profile, set its path in `memd.json` before
switching so the new managed daemon continues using that database. The first build
requires the local Rust toolchain; model/runtime assets come from the checkout and
are not downloaded.

Defaults in profile-local `memd.json` (non-secret configuration):

```json
{"endpoint":"pipe:hermes-memd","namespace":"hermes","top_k":10,"token_budget":4000,"safe_available_tokens":1000,"detail_budgets":{"brief":300,"normal":1000,"deep":2000,"exhaustive":4000},"minimum_utility_per_token":0.0}
```

On Unix the default endpoint is a profile-local Unix socket. Other supported
endpoints remain `unix:/absolute/path.sock` and development-only
`tcp:127.0.0.1:7777`. The plugin still needs to be installed once before it can
appear in the provider picker. Activate it for the **next session** so the
prompt/toolset prefix remains stable.

The `memd_memory` provider tool supports:

- `remember`: ingest an already-selected fact/episode via structured `record`.
  Optional `target: user` stores persona/preferences; the default `memory` target
  stores environment/project facts. Matching native/explicit saves in the same
  target reuse the existing record ID. Native character budgets apply.
- `recall`: return a temporary budgeted context package as a tool result.
  Optional `detail` values are `brief`, `normal` (the default), `deep`, and
  `exhaustive`, with preferred budgets of 300, 1,000, 2,000, and 4,000 tokens.
  `detail_budgets` configures those values; `token_budget` is the hard cap and
  `safe_available_tokens` is the fallback ceiling. In the Hermes tool path, the
  bridge supplies cached prompt occupancy and context-window metadata when available;
  otherwise it conservatively falls back to 1,000 tokens. The Python builder accepts
  a caller-supplied counter and verifies the finished formatted package against its
  budget. Hermes does not expose an active provider tokenizer at this boundary, so
  the bridge uses Hermes' shared rough estimator with 25% padding and reports
  `hermes_rough_estimate_padded`; the builder uses the same estimator by default.
  Package diagnostics include the effective budget, selected IDs, provenance,
  retrieval/selection/omission/clipping counts, truncation, and an estimated
  candidate baseline/token-saved count; these fields stay outside LLM context text.
  Configure `minimum_utility_per_token` above zero to omit weak candidates and
  allow unused budget. Zero disables the cutoff. Hybrid retrieval uses RRF scores,
  which are treated as ranks rather than probabilities. Exact duplicate summaries
  are removed; explicit `superseded_by` metadata is honored. Expiry filtering occurs
  in Rust and timestamps alone do not discard historical facts. No semantic
  contradiction detector runs.
- `source`: resolve a returned `source_ref` against the current profile's original
  Hermes session DB, read-only, returning a bounded original excerpt.
- `history`: search original session messages read-only; never memory facts.

Programmatic extraction/checkpoint callers can use:

```python
provider.ingest_memory(
    {"content":"User prefers concise Rust examples","kind":"semantic","tags":["rust"]},
    session_id="original-session-id", message_uid="original-durable-message-uid",
)
package = provider.recall("Rust preferences", token_budget=1000)
```

If a tool caller lacks the current message UID, provenance references the original
session rather than inventing a message ID. Source resolution prefers the original
session and can follow the same durable message UID into a copied/rotated session.
Profiles use independent hashed namespaces and are resolved at each call, including
an A→B→A multiplexer cycle. Caller-provided IDs are rejected by the bridge so they
cannot overwrite a memory in another namespace; trusted daemon administration still
supports updates by ID. Writes are disabled for non-primary agent contexts.

`sync_turn` remains a no-op: ordinary conversation is neither copied nor embedded.
The static system prompt block is empty. Context appears only in an explicit tool
result; the bridge never mutates the prompt or injects conversation messages.
The ContextBuilder uses Hermes' shared rough token estimate with 25% safety padding
unless a caller supplies a counter. This is not an always-on LLM extractor or Prime algorithm.
Selection is deterministic and bounded by the retrieved top-k. Optional utility
thresholding and freshness checks add no model calls or embeddings. Raising the
fallback ceiling can return more memory on models with smaller context windows;
choose it only when the caller knows adequate room is available.

The daemon's legacy `search_sessions` hook remains empty for backward compatibility;
the optional Python bridge owns read-only access to the user's original session DB.
No original transcript writes, migrations or lifecycle changes are introduced.

## Live context budget and historical retrieval

For `memd_memory`, Hermes passes cached context-window metadata plus provider-reported
prompt and completion usage from the immediately preceding request. The bridge
subtracts that occupancy, the configured next-response reserve, a 1,024-token safety
margin, and bounded room for tool-result wrappers/other tool results. It divides
remaining room across parallel memory calls and applies the requested detail budget
and `hard_token_cap`. The old `safe_available_tokens` value (default 1,000) remains
the fallback only when reliable occupancy is unavailable. Unknown model limits do
not get inferred from model names or trigger metadata network probes.

Before sending, Hermes checks the complete API-copy prompt, including messages and
tool schemas, against cached context metadata. If needed, it trims only the
`memd_memory` result text; the transcript and other tool results are unchanged.
Hermes' existing pressure/compaction flow remains authoritative. This is a
conservative estimate: tool schemas and next-call wrappers are estimated, and exact
future content is unknowable before all tools return. No memory text is inserted
twice.

The Hermes boundary currently has no provider-selected tokenizer. Counts use
`agent.model_metadata.estimate_tokens_rough` and are padded in the ContextBuilder;
diagnostics identify this as an estimate, not exact tokenizer output. A direct
caller can supply its own counter, which is applied to final JSON formatting, IDs,
provenance and separators. Each retrieved candidate is serialized and counted once;
retrieval is bounded to at most 100 per namespace.

Rust stores optional `superseded_by` in the record payload and indexed column
(migration 003). Links must resolve inside the same namespace, access scope and
agent; self-links, missing targets, cross-scope links and cycles are rejected in the
write transaction. Default retrieval omits superseded records. Historical requests
may set `include_superseded: true`; returned historical context retains the link and
source provenance. Deleting a replacement clears its incoming links, preserving
older facts without claiming they are current. Supersession is explicit; timestamps
alone never remove stable historical facts.

Utility cutoff calibration remains opt-in (`minimum_utility_per_token: 0`). RRF is
rank-normalized and never treated as a probability. The synthetic comparison below
measures packaging cost only, not semantic quality or task success. Keep the cutoff
disabled unless a manually labeled, held-out query set shows required evidence
remains covered while reducing tokens.

Latest local 100-run synthetic receipt (120 identical candidates across variants,
10 manually designated required IDs and 21 relevant IDs; estimates use UTF-8 bytes
/ 4): fixed 1,000-token package returned 1,000 tokens, selected 20, retained 5/10
required (0.50 required coverage; 0.381 relevant recall), 18.183 ms median and
216,128-byte Python traced peak. A 700-token ceiling returned 695 tokens (30.5%
fewer than fixed), selected 14, retained 4/10 required (0.40 coverage; 0.286
relevant recall), 141.590 ms and 185,472 bytes. Adding a 0.002 utility cutoff
returned the same 695 tokens and evidence (124.642 ms, 187,633 bytes). This fixture
therefore shows a token/RAM reduction from a smaller ceiling with lost labeled
evidence; the cutoff added no measured reduction. It does not justify enabling the
cutoff or claim semantic quality. Timing varies on a shared Windows host; Python
tracemalloc excludes Rust and whole-process RSS. Run
`python memd/scripts/benchmark_context_builder.py` to reproduce.

## Reproducing follow-up checks

```powershell
./memd/scripts/windows.ps1 -Action test
$env:MEMD_BIN = (Resolve-Path memd/target-gnu/debug/memd.exe).Path
$env:MEMD_TEST_MODEL_DIR = (Resolve-Path memd/models/all-MiniLM-L6-v2).Path
$env:MEMD_TEST_ORT_DYLIB = (Resolve-Path memd/onnxruntime/onnxruntime-win-x64-1.31.1/lib/onnxruntime.dll).Path
memd/.tmp-build/python-tests/Scripts/python.exe -m unittest discover -s memd/tests -v
```

The test environment was created with Hermes PM (`python -m pm.build_env`), not raw
pip/uv. To run the canonical wrapper in Git Bash, explicitly include its Unix tools:

```bash
export PATH=/usr/bin:/bin:$PATH
export HERMES_PYTHON="$PWD/memd/.tmp-build/python-tests/Scripts/python.exe"
bash scripts/run_tests.sh memd/tests/test_hermes_bridge.py memd/tests/test_persistent_client.py -j 2
```

The wrapper deliberately scrubs real-model opt-in variables, so its synthetic
integration run complements the explicitly enabled real-model unittest run.
Native Unix tests are included but skip on Windows; no WSL distribution is installed
on this machine. Native MSVC/Unix proof still requires those respective environments.
