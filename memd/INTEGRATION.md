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
{"endpoint":"pipe:hermes-memd","namespace":"hermes","top_k":10,"token_budget":1000}
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
The existing ContextBuilder uses a conservative byte-based token estimate unless a
caller supplies a tokenizer. This is not an always-on LLM extractor or Prime algorithm.

The daemon's legacy `search_sessions` hook remains empty for backward compatibility;
the optional Python bridge owns read-only access to the user's original session DB.
No original transcript writes, migrations or lifecycle changes are introduced.

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
