# memd IPC protocol v1

## Transport decision (locked for v1)

Windows is a supported target. The production endpoint is a local IPC stream exposed through the `interprocess` crate: Unix domain sockets on Unix and named pipes on Windows. JSON Lines v1 is transport-independent and remains the same on both platforms. The Windows named pipe must be created with a DACL limited to the current user; the crate's default named-pipe security is not sufficient. The Python client must use the same byte-stream endpoint on Windows before the daemon is considered production-ready.

Loopback TCP is development-only and is not a supported production transport because it has no per-user access control. Do not freeze or depend on TCP-specific protocol behavior.

Each request and response is one UTF-8 JSON object followed by `\n` (JSON Lines). A client may send multiple requests on one connection; each receives one response in order. Maximum request size is 1 MiB. The required request `id` is a string or number and is echoed unchanged.

Every request has `v: 1`, an `id` (string or number, echoed in the response), and an `op`. Success is `{ "v": 1, "id": ..., "ok": true, "result": ... }`. Failure is `{ "v": 1, "id": ..., "ok": false, "error": { "code": "...", "message": "..." } }`. Invalid JSON has a null id. The server keeps serving after a malformed request, but closes a connection after an oversized request.

## Operations

- `ping`: `{ "v": 1, "id": "r1", "op": "ping" }` returns `{ "status": "ok" }`.
- `store`: `{ "v": 1, "id": "r2", "op": "store", "entry": { "content": "..." } }` returns `{ "id": "..." }`. `entry` is an opaque JSON object; its fields are owned by the shared A1/A2 entry contract.
- `retrieve`: `{ "v": 1, "id": "r3", "op": "retrieve", "query": "...", "k": 5 }` returns `{"hits":[{"id":"...","entry":{...},"score":0.0}]}`. `k` is clamped to 100.
- `search_sessions`: `{ "v": 1, "id": "r4", "op": "search_sessions", "query": "..." }` returns `{"hits":[...]}`. Hit shape will be aligned with A1's shared contract.

`maybe_extract` is an engine-side hook, not exposed as an IPC operation in v1; turn extraction is triggered by service integration rather than arbitrary client requests.

Errors use `unsupported_version`, `invalid_request`, `unknown_operation`, `invalid_json`, or `request_too_large`. One request fails independently; the connection stays open except for oversized input or transport errors.

## Measurement protocol (initial)

- Keep machine, Rust build, task data, model, settings, and warm/cold state the same for baseline and new-system runs.
- Record client count and completed operations. The supplied script runs concurrent clients, with each iteration storing an entry and retrieving it.
- Record per-RPC latency and report median and p95. Sample daemon RSS during the run and report peak RSS and delta from the starting sample. Start a fresh daemon for each run, do one warm-up, then at least three measured repetitions for reported results.
- The MockEngine has no model calls, so it produces no meaningful token metric. For Hermes runs, supply JSONL usage records shaped as `{"total_tokens": N}`; the script sums them. Compare runs on the same tasks and report token count separately from IPC measurements.

## Run locally

The current prototype's loopback TCP mode is for local development only. Production launch commands and the Windows Python named-pipe client will be documented after the `interprocess` transport and current-user pipe DACL are implemented.
