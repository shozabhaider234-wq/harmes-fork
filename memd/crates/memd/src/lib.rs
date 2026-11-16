//! Versioned local IPC server for Hermes memory clients.

use memcore::{
    EmbeddingBackend, MemoryCategory, MemoryEntry, MemoryRecord, RetrievalMode, RetrievalScope,
    SqliteConfig, SqliteStore,
};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::thread;

const PROTOCOL_VERSION: u64 = 1;
const MAX_REQUEST_BYTES: usize = 1024 * 1024;

#[cfg(windows)]
mod windows_pipe;
#[cfg(windows)]
pub use windows_pipe::serve_named_pipe;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
#[cfg(unix)]
use std::os::unix::net::UnixListener;

#[derive(Debug)]
pub struct MemoryHit {
    pub id: String,
    pub entry: Value,
    pub score: f32,
    pub source_ref: Option<String>,
}

/// Shared engine boundary for the JSONL protocol and its mock/durable backends.
pub trait MemoryEngine: Send + Sync {
    fn inventory(&self, _scope: RetrievalScope) -> Result<Vec<MemoryRecord>, String> {
        Err("inventory unsupported by this backend".into())
    }
    fn compare_exchange(&self, _scope: RetrievalScope, _expected: Vec<MemoryRecord>, _desired: Vec<MemoryRecord>) -> Result<bool, String> {
        Err("conditional batch unsupported by this backend".into())
    }
    fn storage_stats(&self) -> Result<Value, String> {
        Err("storage statistics unsupported by this backend".into())
    }
    fn store(&self, entry: Value) -> Result<String, String>;
    fn retrieve(&self, query: &str, k: usize) -> Result<Vec<MemoryHit>, String>;
    fn search_sessions(&self, query: &str) -> Result<Vec<Value>, String>;
    fn maybe_extract(&self, turn_context: &str) -> Result<Vec<Value>, String>;
    fn update_memory(&self, _entry: Value) -> Result<String, String> {
        Err("update unsupported by this backend".into())
    }
    fn retrieve_scoped(
        &self,
        query: &str,
        k: usize,
        scope: RetrievalScope,
        mode: RetrievalMode,
    ) -> Result<Vec<MemoryHit>, String> {
        let _ = (query, k, scope, mode);
        Err("scoped retrieval is unsupported by this backend".into())
    }
    fn get_memory(&self, _id: &str) -> Result<Option<Value>, String> {
        Err("lookup unsupported by this backend".into())
    }
    fn delete_memory(&self, _id: &str) -> Result<bool, String> {
        Err("delete unsupported by this backend".into())
    }
}

/// Durable SQLite/FTS5/sqlite-vec engine accepting already-extracted memories.
pub struct MemCoreEngine<B> {
    store: SqliteStore,
    backend: B,
}

impl<B: EmbeddingBackend> MemCoreEngine<B> {
    pub fn open(path: impl Into<std::path::PathBuf>, backend: B) -> Result<Self, String> {
        Self::open_with_config(path, backend, SqliteConfig::default())
    }
    pub fn open_with_config(
        path: impl Into<std::path::PathBuf>,
        backend: B,
        config: SqliteConfig,
    ) -> Result<Self, String> {
        let store = SqliteStore::open(path.into(), backend.model_id(), backend.dim(), config)
            .map_err(|error| error.to_string())?;
        Ok(Self { store, backend })
    }
}

fn parse_record(value: Value) -> Result<MemoryRecord, String> {
    let content = value
        .get("content")
        .or_else(|| value.get("text"))
        .and_then(Value::as_str)
        .ok_or("entry.content must be a string")?;
    let id = match value.get("id") {
        None => None,
        Some(Value::String(id)) if !id.trim().is_empty() => Some(id.clone()),
        Some(_) => return Err("entry.id must be a non-empty string".into()),
    };
    let category = value
        .get("category")
        .map(|category| category.as_str().ok_or("entry.category must be a string"))
        .transpose()?
        .unwrap_or("fact");
    let category = MemoryCategory::from_str(category).map_err(|error| error.to_string())?;
    let tags = match value.get("tags") {
        None => Vec::new(),
        Some(Value::Array(tags)) => tags
            .iter()
            .map(|tag| {
                tag.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| "entry.tags must contain only strings".to_owned())
            })
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err("entry.tags must be an array".into()),
    };
    let mut entry = MemoryEntry::new(content, category, tags);
    if let Some(id) = id {
        entry = entry.with_id(id);
    }
    if let Some(metadata) = value.get("metadata") {
        let metadata = metadata
            .as_object()
            .ok_or("entry.metadata must be an object of strings")?;
        for (key, value) in metadata {
            let value = value
                .as_str()
                .ok_or("entry.metadata values must be strings")?;
            entry = entry.with_metadata(key.as_str(), value);
        }
    }
    if let Some(source_ref) = value.get("source_ref") {
        let source_ref = source_ref
            .as_str()
            .ok_or("entry.source_ref must be a string")?;
        entry = entry.with_metadata("source_ref", source_ref);
    }
    // Preserve the v1 content/category/tags/metadata shape; add typed fields without
    // accepting caller-supplied internal search_text or rewriting legacy metadata.
    let mut typed = serde_json::to_value(&entry).map_err(|e| e.to_string())?;
    for key in [
        "namespace",
        "access_scope",
        "kind",
        "agent_id",
        "session_id",
        "source_ref",
        "importance",
        "confidence",
        "expires_at",
    ] {
        if let Some(field) = value.get(key) {
            typed[key] = field.clone();
        }
    }
    if typed.get("source_ref").is_none() {
        if let Some(source) = entry.metadata.get("source_ref") {
            typed["source_ref"] = json!(source);
        }
    }
    if typed.get("session_id").is_none() {
        if let Some(session) = entry.metadata.get("session_id") {
            typed["session_id"] = json!(session);
        }
    }
    serde_json::from_value(typed).map_err(|e| e.to_string())
}

impl<B: EmbeddingBackend> MemoryEngine for MemCoreEngine<B> {
    fn inventory(&self, scope: RetrievalScope) -> Result<Vec<MemoryRecord>, String> {
        self.store.inventory(&scope).map_err(|e| e.to_string())
    }
    fn compare_exchange(&self, scope: RetrievalScope, expected: Vec<MemoryRecord>, desired: Vec<MemoryRecord>) -> Result<bool, String> {
        self.store.compare_exchange(&scope, &expected, &desired, &self.backend).map_err(|e| e.to_string())
    }
    fn storage_stats(&self) -> Result<Value, String> {
        serde_json::to_value(self.store.stats().map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())
    }
    fn store(&self, value: Value) -> Result<String, String> {
        self.store
            .ingest(&parse_record(value)?, &self.backend)
            .map_err(|e| e.to_string())
    }
    fn update_memory(&self, value: Value) -> Result<String, String> {
        self.store
            .update(&parse_record(value)?, &self.backend)
            .map_err(|e| e.to_string())
    }

    fn retrieve(&self, query: &str, k: usize) -> Result<Vec<MemoryHit>, String> {
        Ok(self
            .retrieve_scoped(query, k, RetrievalScope::default(), RetrievalMode::Vector)?
            .into_iter()
            .filter(|hit| hit.score >= 0.3)
            .collect())
    }

    fn retrieve_scoped(
        &self,
        query: &str,
        k: usize,
        scope: RetrievalScope,
        mode: RetrievalMode,
    ) -> Result<Vec<MemoryHit>, String> {
        self.store
            .retrieve(query, &scope, k, mode, &self.backend)
            .map_err(|e| e.to_string())?
            .into_iter()
            .map(|hit| {
                Ok(MemoryHit {
                    id: hit.record.entry.id.clone(),
                    source_ref: hit.record.source_ref.clone(),
                    score: hit.score as f32,
                    entry: {
                        let mut value =
                            serde_json::to_value(&hit.record).map_err(|e| e.to_string())?;
                        if let Some(distance) = hit.distance {
                            value["vector_distance"] = json!(distance);
                        }
                        value
                    },
                })
            })
            .collect()
    }
    fn get_memory(&self, id: &str) -> Result<Option<Value>, String> {
        self.store
            .get(id)
            .map_err(|e| e.to_string())?
            .map(|r| serde_json::to_value(r).map_err(|e| e.to_string()))
            .transpose()
    }
    fn delete_memory(&self, id: &str) -> Result<bool, String> {
        self.store.delete(id).map_err(|e| e.to_string())
    }

    fn search_sessions(&self, _query: &str) -> Result<Vec<Value>, String> {
        Ok(Vec::new())
    }
    fn maybe_extract(&self, _turn_context: &str) -> Result<Vec<Value>, String> {
        Ok(Vec::new())
    }
}

/// In-memory stand-in; retrieval is case-insensitive substring matching.
#[derive(Clone, Default)]
pub struct MockEngine {
    entries: Arc<Mutex<Vec<(String, Value, String)>>>,
}

impl MemoryEngine for MockEngine {
    fn store(&self, entry: Value) -> Result<String, String> {
        let searchable = entry
            .get("content")
            .or_else(|| entry.get("text"))
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| entry.to_string());
        let mut entries = self.entries.lock().expect("mock engine lock poisoned");
        let id = format!("mock-{}", entries.len() + 1);
        entries.push((id.clone(), entry, searchable));
        Ok(id)
    }

    fn retrieve(&self, query: &str, k: usize) -> Result<Vec<MemoryHit>, String> {
        let query = query.to_lowercase();
        if query.is_empty() || k == 0 {
            return Ok(Vec::new());
        }
        Ok(self
            .entries
            .lock()
            .expect("mock engine lock poisoned")
            .iter()
            .filter(|(_, _, text)| text.to_lowercase().contains(&query))
            .take(k.min(100))
            .map(|(id, entry, _)| MemoryHit {
                id: id.clone(),
                source_ref: entry
                    .get("source_ref")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                entry: entry.clone(),
                score: 1.0,
            })
            .collect())
    }

    fn search_sessions(&self, _query: &str) -> Result<Vec<Value>, String> {
        Ok(Vec::new())
    }

    fn maybe_extract(&self, _turn_context: &str) -> Result<Vec<Value>, String> {
        Ok(Vec::new())
    }
}

pub fn mock_engine() -> Arc<dyn MemoryEngine> {
    Arc::new(MockEngine::default())
}

fn error_response(id: Value, code: &str, message: &str) -> Value {
    json!({"v": PROTOCOL_VERSION, "id": id, "ok": false,
        "error": {"code": code, "message": message}})
}

/// Execute one versioned JSON request against an engine.
pub fn handle_request(request: Value, engine: &dyn MemoryEngine) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    if !id.is_string() && !id.is_number() {
        return error_response(
            Value::Null,
            "invalid_request",
            "id must be a string or number",
        );
    }
    if request.get("v").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return error_response(id, "unsupported_version", "request v must be 1");
    }
    let Some(op) = request.get("op").and_then(Value::as_str) else {
        return error_response(id, "invalid_request", "op must be a string");
    };

    let result = match op {
        "inventory" | "compare_exchange" => {
            let scope = match request.get("scope").cloned().map(serde_json::from_value::<RetrievalScope>) {
                Some(Ok(scope)) => scope,
                _ => return error_response(id, "invalid_request", "valid scope is required"),
            };
            if op == "inventory" {
                match engine.inventory(scope) {
                    Ok(entries) => json!({"entries":entries}),
                    Err(e) => return error_response(id, "engine_error", &e),
                }
            } else {
                let expected = request.get("expected").cloned().map(serde_json::from_value::<Vec<MemoryRecord>>);
                let desired = request.get("desired").cloned().map(serde_json::from_value::<Vec<MemoryRecord>>);
                let (Some(Ok(expected)), Some(Ok(desired))) = (expected, desired) else {
                    return error_response(id, "invalid_request", "expected and desired must be complete record arrays");
                };
                match engine.compare_exchange(scope, expected, desired) {
                    Ok(committed) => json!({"committed":committed}),
                    Err(e) => return error_response(id, "engine_error", &e),
                }
            }
        }
        "ping" => json!({"status": "ok"}),
        "storage_stats" => match engine.storage_stats() {
            Ok(stats) => stats, Err(e) => return error_response(id, "engine_error", &e),
        },
        "store" | "update_memory" => {
            let Some(entry) = request.get("entry") else {
                return error_response(id, "invalid_request", "entry is required");
            };
            if !entry.is_object() {
                return error_response(id, "invalid_request", "entry must be a JSON object");
            }
            if op == "update_memory" {
                let Some(memory_id) = entry.get("id").and_then(Value::as_str) else {
                    return error_response(id, "invalid_request", "update_memory requires entry.id");
                };
                match engine.get_memory(memory_id) {
                    Ok(Some(_)) => (),
                    Ok(None) => return error_response(id, "not_found", "memory does not exist"),
                    Err(e) => return error_response(id, "engine_error", &e),
                }
            }
            let stored = if op == "update_memory" { engine.update_memory(entry.clone()) } else { engine.store(entry.clone()) };
            match stored {
                Ok(id) => json!({"id": id}),
                Err(error) => return error_response(id, "engine_error", &error),
            }
        }
        "retrieve" => {
            let Some(query) = request.get("query").and_then(Value::as_str) else {
                return error_response(id, "invalid_request", "query must be a string");
            };
            let Some(k) = request.get("k").and_then(Value::as_u64) else {
                return error_response(id, "invalid_request", "k must be a non-negative integer");
            };
            let result = if request.get("scope").is_some() || request.get("mode").is_some() {
                let scope = match serde_json::from_value(
                    request.get("scope").cloned().unwrap_or(json!({})),
                ) {
                    Ok(scope) => scope,
                    Err(e) => return error_response(id, "invalid_request", &e.to_string()),
                };
                let mode = match serde_json::from_value(
                    request.get("mode").cloned().unwrap_or(json!("vector")),
                ) {
                    Ok(mode) => mode,
                    Err(e) => return error_response(id, "invalid_request", &e.to_string()),
                };
                engine.retrieve_scoped(query, k.min(100) as usize, scope, mode)
            } else {
                engine.retrieve(query, k.min(100) as usize)
            };
            let hits = match result {
                Ok(hits) => hits,
                Err(error) => return error_response(id, "engine_error", &error),
            };
            let hits: Vec<Value> = hits
                .into_iter()
                .map(|hit| {
                    let mut row = json!({"id": hit.id, "entry": hit.entry, "score": hit.score});
                    if let Some(source_ref) = hit.source_ref {
                        row["source_ref"] = json!(source_ref);
                    }
                    row
                })
                .collect();
            json!({"hits": hits})
        }
        "search_sessions" => {
            let Some(query) = request.get("query").and_then(Value::as_str) else {
                return error_response(id, "invalid_request", "query must be a string");
            };
            match engine.search_sessions(query) {
                Ok(hits) => json!({"hits": hits}),
                Err(error) => return error_response(id, "engine_error", &error),
            }
        }
        "get_memory" | "source_lookup" | "delete_memory" => {
            let Some(memory_id) = request.get("memory_id").and_then(Value::as_str) else {
                return error_response(id, "invalid_request", "memory_id must be a string");
            };
            if op == "delete_memory" {
                match engine.delete_memory(memory_id) {
                    Ok(deleted) => json!({"deleted":deleted}),
                    Err(e) => return error_response(id, "engine_error", &e),
                }
            } else {
                match engine.get_memory(memory_id) {
                    Ok(entry) if op == "source_lookup" => {
                        json!({"source_ref":entry.as_ref().and_then(|e| e.get("source_ref")),
                        "session_id":entry.as_ref().and_then(|e| e.get("session_id"))})
                    }
                    Ok(entry) => json!({"entry":entry}),
                    Err(e) => return error_response(id, "engine_error", &e),
                }
            }
        }
        _ => {
            return error_response(
                id,
                "unknown_operation",
                "supported ops: ping, store, retrieve, search_sessions, update_memory, get_memory, source_lookup, delete_memory",
            )
        }
    };

    json!({"v": PROTOCOL_VERSION, "id": id, "ok": true, "result": result})
}

fn serve_connection<S: std::io::Read + Write>(
    stream: S,
    engine: Arc<dyn MemoryEngine>,
) -> std::io::Result<()> {
    let mut reader = BufReader::new(stream);
    let mut line = Vec::new();
    loop {
        line.clear();
        let mut complete_line = false;
        loop {
            let available = reader.fill_buf()?;
            if available.is_empty() {
                break;
            }
            let take = available
                .iter()
                .position(|byte| *byte == b'\n')
                .map_or(available.len(), |position| position + 1);
            if line.len() + take > MAX_REQUEST_BYTES {
                let response =
                    error_response(Value::Null, "request_too_large", "request exceeds 1 MiB");
                serde_json::to_writer(reader.get_mut(), &response)?;
                reader.get_mut().write_all(b"\n")?;
                return Ok(());
            }
            line.extend_from_slice(&available[..take]);
            reader.consume(take);
            if line.last() == Some(&b'\n') {
                complete_line = true;
                break;
            }
        }
        if line.is_empty() {
            return Ok(());
        }
        let response = match serde_json::from_slice::<Value>(&line) {
            Ok(request) => handle_request(request, engine.as_ref()),
            Err(_) => error_response(
                Value::Null,
                "invalid_json",
                "request line must contain valid JSON",
            ),
        };
        {
            // Batch serializer fragments into bounded writes on both stream transports.
            let mut writer = BufWriter::new(reader.get_mut());
            serde_json::to_writer(&mut writer, &response)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }
        if !complete_line {
            return Ok(());
        }
    }
}

fn serve_tcp_connection(stream: TcpStream, engine: Arc<dyn MemoryEngine>) {
    if let Err(error) = serve_connection(stream, engine) {
        eprintln!("memd client connection ended: {error}");
    }
}

/// Development transport for platforms without Unix domain socket support.
/// Binds loopback only; do not expose this listener to a network interface.
pub fn serve_tcp(address: &str, engine: Arc<dyn MemoryEngine>) -> std::io::Result<()> {
    let address: SocketAddr = address.parse().map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("invalid TCP address: {e}"),
        )
    })?;
    if !address.ip().is_loopback() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            "memd TCP mode must bind to a loopback address",
        ));
    }
    let listener = TcpListener::bind(address)?;
    eprintln!("memd listening on {}", listener.local_addr()?);
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let engine = Arc::clone(&engine);
                thread::spawn(move || serve_tcp_connection(stream, engine));
            }
            Err(error) => eprintln!("memd accept failed: {error}"),
        }
    }
    Ok(())
}

#[cfg(unix)]
pub fn serve_unix(path: &str, engine: Arc<dyn MemoryEngine>) -> std::io::Result<()> {
    let listener = UnixListener::bind(path)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    eprintln!("memd listening on {path}");
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let engine = Arc::clone(&engine);
                thread::spawn(move || {
                    if let Err(error) = serve_connection(stream, engine) {
                        eprintln!("memd client connection ended: {error}");
                    }
                });
            }
            Err(error) => eprintln!("memd accept failed: {error}"),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use memcore::{EmbedRole, Embedding, EmbeddingError};
    use std::hash::{Hash, Hasher};
    use std::io::{Read, Write};
    use std::net::{Shutdown, TcpListener, TcpStream};

    struct TestBackend;

    impl EmbeddingBackend for TestBackend {
        fn model_id(&self) -> &str {
            "memd-test"
        }
        fn dim(&self) -> usize {
            128
        }
        fn embed(&self, _role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError> {
            let mut vector = vec![0.0; self.dim()];
            for token in text.to_ascii_lowercase().split_whitespace() {
                let mut hasher = std::collections::hash_map::DefaultHasher::new();
                token.hash(&mut hasher);
                let bucket = (hasher.finish() as usize) % vector.len();
                vector[bucket] += 1.0;
            }
            Embedding::from_raw(self.model_id(), vector, self.dim())
        }
    }

    fn exchange(address: std::net::SocketAddr, requests: &[Value]) -> Vec<Value> {
        let mut stream = TcpStream::connect(address).unwrap();
        for request in requests {
            serde_json::to_writer(&mut stream, request).unwrap();
            stream.write_all(b"\n").unwrap();
        }
        stream.shutdown(Shutdown::Write).unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        response
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    fn serve_once(engine: Arc<dyn MemoryEngine>, requests: &[Value]) -> Vec<Value> {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            serve_connection(stream, engine).unwrap();
        });
        let responses = exchange(address, requests);
        server.join().unwrap();
        responses
    }

    fn request(id: &str, op: &str, fields: Value) -> Value {
        let mut request = fields.as_object().unwrap().clone();
        request.insert("v".into(), json!(1));
        request.insert("id".into(), json!(id));
        request.insert("op".into(), json!(op));
        Value::Object(request)
    }

    #[test]
    fn memcore_engine_serves_durable_ranked_hits_over_daemon_protocol() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("memd-e2e-{}-{nonce}", std::process::id()));
        let path = root.join("memories.db");
        let engine = Arc::new(MemCoreEngine::open(&path, TestBackend).unwrap());
        let stored = serve_once(
            engine.clone(),
            &[
                request(
                    "s1",
                    "store",
                    json!({"entry": {"id":"rust-1", "content":"Rust project builds with Cargo", "tags":["compiler"], "source_ref":"session_12:turn_40"}}),
                ),
                request(
                    "s2",
                    "store",
                    json!({"entry": {"id":"garden-1", "content":"Tomatoes need sunlight and water"}}),
                ),
                request(
                    "r1",
                    "retrieve",
                    json!({"query":"Cargo Rust project", "k":5}),
                ),
            ],
        );
        assert_eq!(stored[0]["result"]["id"], "rust-1");
        assert_eq!(stored[1]["result"]["id"], "garden-1");
        assert_eq!(stored[2]["result"]["hits"][0]["id"], "rust-1");
        assert_eq!(
            stored[2]["result"]["hits"][0]["source_ref"],
            "session_12:turn_40"
        );
        drop(engine);

        let reopened = Arc::new(MemCoreEngine::open(&path, TestBackend).unwrap());
        let after_restart = serve_once(
            reopened,
            &[request(
                "r2",
                "retrieve",
                json!({"query":"Cargo Rust project", "k":5}),
            )],
        );
        assert_eq!(after_restart[0]["result"]["hits"][0]["id"], "rust-1");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn mock_preserves_legacy_requests_and_ids() {
        let engine = MockEngine::default();
        assert_eq!(
            handle_request(json!({"v":1,"id":17,"op":"ping"}), &engine)["id"],
            17
        );
        assert!(handle_request(
            request("s", "store", json!({"entry":{"content":"Rust compiler"}})),
            &engine
        )["ok"]
            .as_bool()
            .unwrap());
        let result = handle_request(
            request("r", "retrieve", json!({"query":"Rust", "k":5})),
            &engine,
        );
        assert_eq!(result["result"]["hits"][0]["id"], "mock-1");
        assert_eq!(
            handle_request(
                request("h", "search_sessions", json!({"query":"Rust"})),
                &engine
            )["result"]["hits"],
            json!([])
        );
        assert_eq!(
            handle_request(
                request("bad", "retrieve", json!({"query":"Rust","k":-1})),
                &engine
            )["error"]["code"],
            "invalid_request"
        );
    }
}
