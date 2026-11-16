# memd IPC protocol v1

## Native-store additions (backward-compatible v1)

`inventory` takes an explicit `scope` (namespace, access_scope, exact agent_id)
and returns `result.entries`: complete MemoryRecords in stable insertion order.
Session/kind/tag search filters are rejected. This is the bounded curated-store
inventory, not top-k retrieval; expired records remain editable. Client response
and transport request limits remain 1 MiB. Oversized inventories fail visibly;
do not use this interface as an unbounded transcript store.

`compare_exchange` takes the same scope plus `expected` and `desired` arrays of
complete MemoryRecords (the existing serialized record schema). It returns
`result.committed: true` only after one durable SQLite transaction commits.
`false` means another writer changed the inventory; nothing was written. Invalid
records, ownership mismatches, duplicate IDs/content and embedding/storage errors
return structured errors. IDs outside the inventory cannot be overwritten.
Existing IDs retain creation timestamps; edited content is normalized/re-embedded
in Rust, and updates receive a new timestamp. Unchanged records retain embeddings.
Deletes remove row/vector/FTS data atomically. Embedding failure or any batch error
leaves the inventory unchanged. MockEngine deliberately reports unsupported.

These operations add no extractor or scheduler. Native Python MemoryStore
validates the existing tool operation, then conditionally commits its result.
Local transport clients remain trusted; ownership scopes are not authentication.

## Transport decision (locked for v1)

Windows is a supported target. The local IPC endpoints are Unix domain sockets on
Unix and native Win32 byte-stream named pipes on Windows. JSON Lines v1 remains the
same on both platforms. Named pipes use a protected current-user-SID DACL, reject
remote clients, and prevent first-instance takeover. The Python client uses the
same byte stream with cancellable deadlines. Native Win32 APIs reuse `windows-sys`
rather than adding a second IPC abstraction library.

Loopback TCP is development-only and is not a supported production transport because it has no per-user access control. Do not freeze or depend on TCP-specific protocol behavior.

Each request and response is one UTF-8 JSON object followed by `\n` (JSON Lines). A client may send multiple requests on one connection; each receives one response in order. Maximum request size is 1 MiB. The required request `id` is a string or number and is echoed unchanged.

Every request has `v: 1`, an `id` (string or number, echoed in the response), and an `op`. Success is `{ "v": 1, "id": ..., "ok": true, "result": ... }`. Failure is `{ "v": 1, "id": ..., "ok": false, "error": { "code": "...", "message": "..." } }`. Invalid JSON has a null id. The server keeps serving after a malformed request, but closes a connection after an oversized request.

## Operations

- `ping`: `{ "v": 1, "id": "r1", "op": "ping" }` returns `{ "status": "ok" }`.
- `store`: `{ "v": 1, "id": "r2", "op": "store", "entry": { "content": "..." } }` returns `{ "id": "..." }`. `entry` is an opaque JSON object; its fields are owned by the shared A1/A2 entry contract.
- `retrieve`: `{ "v": 1, "id": "r3", "op": "retrieve", "query": "...", "k": 5 }` returns `{"hits":[{"id":"...","entry":{...},"score":0.0,"source_ref":"..."}]}`; `source_ref` is optional. `k` is clamped to 100.
- `search_sessions`: `{ "v": 1, "id": "r4", "op": "search_sessions", "query": "..." }` returns `{"hits":[...]}`. Hit shape will be aligned with A1's shared contract.

`maybe_extract` is an engine-side hook, not exposed as an IPC operation in v1; turn extraction is triggered by service integration rather than arbitrary client requests.

Errors use `unsupported_version`, `invalid_request`, `unknown_operation`, `invalid_json`, `request_too_large`, or `engine_error` when storage or embedding fails. One request fails independently; the connection stays open except for oversized input or transport errors.

## Measurement protocol (initial)

- Keep machine, Rust build, task data, model, settings, and warm/cold state the same for baseline and new-system runs.
- Record client count and completed operations. The supplied script runs concurrent clients, with each iteration storing an entry and retrieving it.
- Record per-RPC latency and report median and p95. Sample daemon RSS during the run and report peak RSS and delta from the starting sample. Start a fresh daemon for each run, do one warm-up, then at least three measured repetitions for reported results.
- The normal `MemCoreEngine` uses a local embedding provider and a separate SQLite/sqlite-vec/FTS5 memory database. `--mock` uses an in-memory substring engine for protocol development only; `--synthetic` explicitly uses deterministic test vectors with real SQLite persistence. For Hermes runs, supply JSONL usage records shaped as `{"total_tokens": N}`; the script sums them. Compare runs on the same tasks and report token count separately from IPC measurements.

## Run locally

Build and run the durable engine after provisioning the offline model (see [README.md](README.md#embedding-model-setup)):

```bash
cargo run --manifest-path memd/Cargo.toml -p memd -- --socket /tmp/hermes-memd.sock --store /path/to/memories.db --model-dir memd/models/all-MiniLM-L6-v2 --model-revision model-v1
```

Loopback TCP remains development-only (`--tcp 127.0.0.1:PORT`). Windows named pipes
are implemented as `--pipe NAME` / `MemdClient(pipe_name="NAME")` and tested with
synthetic and real-model storage. Unix transport is preserved; its new live-client
tests require a Unix host and skip on Windows.

## Additive v1 structured memory interface

Old operations, IDs, response envelopes and the request-size limit remain unchanged.
The transport is still JSON Lines: SQLite storage does not change the wire encoding.

`store.entry` still accepts `content` (or the existing `text` alias), optional `id`,
`category`, string-array `tags` and string-valued `metadata`. Additional typed fields:
`kind` (`semantic` default or `episodic`), `namespace` (`default`), `access_scope`
(`private`), optional `agent_id`, `session_id`, `source_ref`, `importance`/`confidence`
(finite [0,1]), `expires_at` (RFC3339). IDs generated by the engine remain strings.
The same ID upserts a complete record; omitted fields take defaults, not old values.
Normalized duplicates in the same namespace/scope/agent/kind return the existing ID
without merging provenance. Collisions when updating a different ID fail atomically.

Optional `retrieve.scope` accepts `namespace`, `access_scope`, `agent_id`, `session_id`,
`kind`, `tag`; unknown scope fields fail rather than silently weakening a filter.
Namespace/scope/agent match exactly; omitted agent only sees unowned records.
Optional `retrieve.mode` is `vector`, `keyword` or `hybrid`. Explicit scope/mode
selects the new interface; legacy requests retain cosine similarity and the 0.3
threshold in the default/private/unowned scope. Keyword scores are negative BM25
(higher is better); hybrid scores use RRF, default constant 60. Explicit vector
search returns cosine similarity without a threshold. `entry.vector_distance` is
present for vector candidates (cosine distance, lower is better). All modes filter
expired records. Results include the typed record and its original entry fields.

```json
{"v":1,"id":"r5","op":"retrieve","query":"Cargo fix","k":5,"mode":"hybrid","scope":{"namespace":"project-a","access_scope":"team","agent_id":"assistant-a"}}
```

New optional operations:

- `update_memory`, with complete `entry` including an existing `id`: replaces text
  and metadata, preserves creation time, regenerates the vector; missing ID gives
  `not_found`. The engine checks existence again inside the write transaction.
- `get_memory`, with `memory_id`: returns `{"entry":{...}}`, or `{"entry":null}`.
- `source_lookup`, with `memory_id`: returns `{"source_ref":...,"session_id":...}`,
  nulls if absent. Does not open the referenced session.
- `delete_memory`, with `memory_id`: returns `{"deleted":true|false}`. FTS/vector
  cleanup is transactional.
- `storage_stats`: returns count, dimension, model identity, database allocated
  bytes, allocated vector shadow-table bytes and theoretical raw float32 bytes.
  Primarily for measurement; it does not load vectors into Rust collections.

The local caller is trusted to choose its scopes; Windows pipe ACLs enforce OS-user
access, while this protocol does not implement per-agent authentication. Lookup/update/delete by ID are administrative operations
for that trusted caller. `MockEngine` retains legacy operations and reports an
engine error for unsupported extensions.

`search_sessions` retains its original **session-history** meaning and current
empty-hook behavior. It is never redirected to extracted memory search. No transcript
reader or automatic extractor is added. `store` is an ingestion boundary for already
extracted records, not a request to extract memories from raw conversation text.

The optional Hermes Python plugin implements profile-bound read-only original
session/source lookup through its `memd_memory` tool. This does not change the
daemon's legacy `search_sessions` hook or import session transcripts into memory.
See [INTEGRATION.md](INTEGRATION.md) for the opt-in provider and runtime setup.
