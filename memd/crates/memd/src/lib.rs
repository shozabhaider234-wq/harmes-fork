//! Versioned local IPC server for Hermes memory clients.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;

const PROTOCOL_VERSION: u64 = 1;
const MAX_REQUEST_BYTES: usize = 1024 * 1024;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
#[cfg(unix)]
use std::os::unix::net::UnixListener;

#[derive(Debug)]
pub struct MemoryHit {
    pub id: String,
    pub entry: Value,
    pub score: f32,
}

/// Internal engine boundary. Replace with A1's `memcore::MemoryEngine` when its
/// entry schema is frozen; the wire contract does not depend on this trait.
pub trait MemoryEngine: Send + Sync {
    fn store(&self, entry: Value) -> String;
    fn retrieve(&self, query: &str, k: usize) -> Vec<MemoryHit>;
    fn search_sessions(&self, query: &str) -> Vec<Value>;
    fn maybe_extract(&self, turn_context: &str) -> Vec<Value>;
}

/// In-memory stand-in; retrieval is case-insensitive substring matching.
#[derive(Clone, Default)]
pub struct MockEngine {
    entries: Arc<Mutex<Vec<(String, Value, String)>>>,
}

impl MemoryEngine for MockEngine {
    fn store(&self, entry: Value) -> String {
        let searchable = entry
            .get("content")
            .or_else(|| entry.get("text"))
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| entry.to_string());
        let mut entries = self.entries.lock().expect("mock engine lock poisoned");
        let id = format!("mock-{}", entries.len() + 1);
        entries.push((id.clone(), entry, searchable));
        id
    }

    fn retrieve(&self, query: &str, k: usize) -> Vec<MemoryHit> {
        let query = query.to_lowercase();
        if query.is_empty() || k == 0 {
            return Vec::new();
        }
        self.entries
            .lock()
            .expect("mock engine lock poisoned")
            .iter()
            .filter(|(_, _, text)| text.to_lowercase().contains(&query))
            .take(k.min(100))
            .map(|(id, entry, _)| MemoryHit {
                id: id.clone(),
                entry: entry.clone(),
                score: 1.0,
            })
            .collect()
    }

    fn search_sessions(&self, _query: &str) -> Vec<Value> {
        Vec::new()
    }

    fn maybe_extract(&self, _turn_context: &str) -> Vec<Value> {
        Vec::new()
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
        "ping" => json!({"status": "ok"}),
        "store" => {
            let Some(entry) = request.get("entry") else {
                return error_response(id, "invalid_request", "entry is required");
            };
            if !entry.is_object() {
                return error_response(id, "invalid_request", "entry must be a JSON object");
            }
            json!({"id": engine.store(entry.clone())})
        }
        "retrieve" => {
            let Some(query) = request.get("query").and_then(Value::as_str) else {
                return error_response(id, "invalid_request", "query must be a string");
            };
            let Some(k) = request.get("k").and_then(Value::as_u64) else {
                return error_response(id, "invalid_request", "k must be a non-negative integer");
            };
            let hits = engine.retrieve(query, k.min(100) as usize);
            let hits: Vec<Value> = hits
                .into_iter()
                .map(|hit| json!({"id": hit.id, "entry": hit.entry, "score": hit.score}))
                .collect();
            json!({"hits": hits})
        }
        "search_sessions" => {
            let Some(query) = request.get("query").and_then(Value::as_str) else {
                return error_response(id, "invalid_request", "query must be a string");
            };
            json!({"hits": engine.search_sessions(query)})
        }
        _ => {
            return error_response(
                id,
                "unknown_operation",
                "supported ops: ping, store, retrieve, search_sessions",
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
        serde_json::to_writer(reader.get_mut(), &response)?;
        reader.get_mut().write_all(b"\n")?;
        reader.get_mut().flush()?;
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
