//! Persistent extracted memories only. This module never opens Hermes transcript storage.
use crate::{Embedding, EmbeddingBackend, MemoryEntry};
use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{Mutex, Once},
    time::Duration,
};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
fn invalid(message: &str) -> Box<dyn std::error::Error + Send + Sync> {
    std::io::Error::new(std::io::ErrorKind::InvalidInput, message).into()
}
fn default_namespace() -> String {
    "default".into()
}
fn default_scope() -> String {
    "private".into()
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryKind {
    #[default]
    Semantic,
    Episodic,
}

/// Keeps the original entry/category contract while adding typed retrieval boundaries.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryRecord {
    #[serde(flatten)]
    pub entry: MemoryEntry,
    #[serde(default = "default_namespace")]
    pub namespace: String,
    #[serde(default = "default_scope")]
    pub access_scope: String,
    #[serde(default)]
    pub kind: MemoryKind,
    pub agent_id: Option<String>,
    pub session_id: Option<String>,
    pub source_ref: Option<String>,
    pub importance: Option<f32>,
    pub confidence: Option<f32>,
    pub expires_at: Option<DateTime<Utc>>,
}
impl MemoryRecord {
    pub fn new(entry: MemoryEntry) -> Self {
        Self {
            entry,
            namespace: default_namespace(),
            access_scope: default_scope(),
            kind: MemoryKind::Semantic,
            agent_id: None,
            session_id: None,
            source_ref: None,
            importance: None,
            confidence: None,
            expires_at: None,
        }
    }
    fn validate(&self) -> Result<()> {
        if [
            &self.entry.id,
            &self.namespace,
            &self.access_scope,
            self.entry.content(),
        ]
        .iter()
        .any(|s| s.trim().is_empty())
        {
            return Err(invalid(
                "id, namespace, access_scope and content must be non-empty",
            ));
        }
        if self.agent_id.as_ref().is_some_and(|s| s.trim().is_empty()) {
            return Err(invalid("agent_id must be non-empty when provided"));
        }
        if [self.importance, self.confidence]
            .iter()
            .flatten()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
        {
            return Err(invalid(
                "importance and confidence must be finite numbers in [0,1]",
            ));
        }
        if self.entry.updated_at < self.entry.created_at {
            return Err(invalid("updated_at precedes created_at"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct RetrievalScope {
    pub namespace: String,
    pub access_scope: String,
    pub agent_id: Option<String>,
    pub session_id: Option<String>,
    pub kind: Option<MemoryKind>,
    pub tag: Option<String>,
}
impl Default for RetrievalScope {
    fn default() -> Self {
        Self {
            namespace: default_namespace(),
            access_scope: default_scope(),
            agent_id: None,
            session_id: None,
            kind: None,
            tag: None,
        }
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetrievalMode {
    Vector,
    Keyword,
    Hybrid,
}
#[derive(Debug, Clone, Serialize)]
pub struct RankedMemory {
    pub record: MemoryRecord,
    pub score: f64,
    pub distance: Option<f64>,
}
#[derive(Debug, Serialize)]
pub struct StorageStats {
    pub sqlite_version: String,
    pub vec_version: String,
    pub memory_count: u64,
    pub dimension: usize,
    pub model_id: String,
    pub database_allocated_bytes: u64,
    pub vector_allocated_bytes: u64,
    pub raw_float32_embedding_bytes: u64,
}

#[derive(Debug, Clone)]
pub struct SqliteConfig {
    pub cache_kib: u32,
    pub busy_timeout_ms: u64,
    pub max_top_k: usize,
    pub rrf_constant: f64,
}
impl Default for SqliteConfig {
    fn default() -> Self {
        Self {
            cache_kib: 4096,
            busy_timeout_ms: 5000,
            max_top_k: 100,
            rrf_constant: 60.0,
        }
    }
}

pub struct SqliteStore {
    // ponytail: serialize one daemon's DB work; use a bounded read pool only if measured contention warrants it.
    connection: Mutex<Connection>,
    model_id: String,
    dimension: usize,
    config: SqliteConfig,
}
const FILTER: &str = "m.namespace=:ns AND m.access_scope=:scope AND m.agent_id=:agent
 AND (:session IS NULL OR m.session_id=:session) AND (:kind IS NULL OR m.kind=:kind)
 AND (:tag IS NULL OR EXISTS(SELECT 1 FROM json_each(m.payload, '$.tags') WHERE value=:tag))
 AND (m.expires_at IS NULL OR m.expires_at>:now)";
fn kind_name(kind: MemoryKind) -> &'static str {
    match kind {
        MemoryKind::Semantic => "semantic",
        MemoryKind::Episodic => "episodic",
    }
}
fn vector_blob(embedding: &Embedding) -> Vec<u8> {
    embedding
        .as_slice()
        .iter()
        .flat_map(|f| f.to_le_bytes())
        .collect()
}

impl SqliteStore {
    pub fn stats(&self) -> Result<StorageStats> {
        let db = self.db()?;
        let count: u64 = db.query_row("SELECT count(*) FROM memories", [], |r| r.get(0))?;
        let pages: u64 = db.query_row("PRAGMA page_count", [], |r| r.get(0))?;
        let page_size: u64 = db.query_row("PRAGMA page_size", [], |r| r.get(0))?;
        let vector_bytes = db.query_row(
            "SELECT coalesce(sum(pgsize),0) FROM dbstat WHERE name LIKE 'memory_vectors%'",
            [],
            |r| r.get(0),
        )?;
        Ok(StorageStats {
            sqlite_version: rusqlite::version().to_owned(),
            vec_version: db.query_row("SELECT vec_version()", [], |r| r.get(0))?,
            memory_count: count,
            dimension: self.dimension,
            model_id: self.model_id.clone(),
            database_allocated_bytes: pages * page_size,
            vector_allocated_bytes: vector_bytes,
            raw_float32_embedding_bytes: count * self.dimension as u64 * 4,
        })
    }
    pub fn open(
        path: impl AsRef<Path>,
        model_id: &str,
        dimension: usize,
        config: SqliteConfig,
    ) -> Result<Self> {
        if model_id.trim().is_empty()
            || dimension == 0
            || dimension > 8192
            || config.cache_kib == 0
            || config.max_top_k == 0
            || config.max_top_k > 4096
            || !config.rrf_constant.is_finite()
            || config.rrf_constant <= 0.0
        {
            return Err(invalid(
                "invalid model, dimension (1..8192), cache, top-k (1..4096), or RRF config",
            ));
        }
        static REGISTER: Once = Once::new();
        REGISTER.call_once(|| unsafe {
            // sqlite-vec's documented static entrypoint has SQLite's extension-init ABI.
            rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute::<
                *const (),
                unsafe extern "C" fn(
                    *mut rusqlite::ffi::sqlite3,
                    *mut *mut std::ffi::c_char,
                    *const rusqlite::ffi::sqlite3_api_routines,
                ) -> std::ffi::c_int,
            >(
                sqlite_vec::sqlite3_vec_init as *const (),
            )));
        });
        let path = path.as_ref();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let mut db = Connection::open(path)?;
        // Refuse unrelated SQLite databases before changing journaling or schema.
        // In particular a mistaken --store path must not initialize inside Hermes state.db.
        let unrelated: bool = db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%')
            AND NOT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='embedding_space')", [], |r| r.get(0))?;
        if unrelated {
            return Err(invalid("refusing to modify an unrelated SQLite database; choose a separate memory database"));
        }
        let version: u32 = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 2 {
            return Err(invalid("database schema is newer than this engine"));
        }
        db.busy_timeout(Duration::from_millis(config.busy_timeout_ms))?;
        db.execute_batch(&format!(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
            PRAGMA cache_size=-{}; PRAGMA mmap_size=0; PRAGMA temp_store=FILE;",
            config.cache_kib
        ))?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let version: u32 = tx.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 2 {
            return Err(invalid("database schema is newer than this engine"));
        }
        if version == 0 {
            tx.execute_batch(include_str!("migrations/001.sql"))?;
        }
        tx.execute(
            "INSERT OR IGNORE INTO embedding_space VALUES(1,?1,?2)",
            params![model_id, dimension],
        )?;
        let identity: (String, usize) =
            tx.query_row("SELECT model_id,dimension FROM embedding_space", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })?;
        if identity != (model_id.to_owned(), dimension) {
            return Err(invalid(
                "embedding space mismatch; use a separate database or explicitly re-embed offline",
            ));
        }
        if version < 2 {
            tx.execute_batch(&format!("CREATE VIRTUAL TABLE memory_vectors USING vec0(embedding float[{dimension}] distance_metric=cosine);
                CREATE TRIGGER memories_vectors_ad AFTER DELETE ON memories BEGIN DELETE FROM memory_vectors WHERE rowid=old.rowid; END;
                PRAGMA user_version=2;"))?;
        }
        tx.commit()?;
        Ok(Self {
            connection: Mutex::new(db),
            model_id: model_id.into(),
            dimension,
            config,
        })
    }
    fn check_embedding(&self, e: &Embedding) -> Result<()> {
        if e.model_id() != self.model_id || e.dim() != self.dimension {
            return Err(invalid("embedding model or dimension mismatch"));
        }
        Ok(())
    }
    fn db(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.connection
            .lock()
            .map_err(|_| invalid("database lock poisoned"))
    }
    /// Atomic upsert. Exact duplicates in the same ownership/kind boundary return the existing ID.
    /// Duplicate provenance/metadata is deliberately not merged or overwritten.
    pub fn store(&self, record: &MemoryRecord, embedding: &Embedding) -> Result<String> {
        self.save(record, embedding, false)
    }
    fn save(
        &self,
        record: &MemoryRecord,
        embedding: &Embedding,
        require_existing: bool,
    ) -> Result<String> {
        record.validate()?;
        self.check_embedding(embedding)?;
        let normalized = record
            .entry
            .content()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase();
        let mut db = self.db()?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let id = self.save_in_transaction(&tx, record, embedding, require_existing, &normalized)?;
        tx.commit()?;
        Ok(id)
    }
    fn save_in_transaction(
        &self, tx: &Connection, record: &MemoryRecord, embedding: &Embedding,
        require_existing: bool, normalized: &str,
    ) -> Result<String> {
        if require_existing
            && !tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM memories WHERE id=?1)",
                [&record.entry.id],
                |r| r.get::<_, bool>(0),
            )?
        {
            return Err(
                std::io::Error::new(std::io::ErrorKind::NotFound, "memory does not exist").into(),
            );
        }
        let agent = record.agent_id.as_deref().unwrap_or("");
        let duplicate: Option<String> = tx.query_row("SELECT id FROM memories WHERE namespace=?1 AND access_scope=?2 AND agent_id=?3 AND kind=?4 AND normalized_text=?5 AND id<>?6",
            params![record.namespace, record.access_scope, agent, kind_name(record.kind), normalized, record.entry.id], |r| r.get(0)).optional()?;
        if let Some(id) = duplicate {
            if tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM memories WHERE id=?1)",
                [&record.entry.id],
                |r| r.get::<_, bool>(0),
            )? {
                return Err(invalid(
                    "update would collide with a different existing memory",
                ));
            }
            return Ok(id);
        }
        let mut record = record.clone();
        if let Some(payload) = tx
            .query_row(
                "SELECT payload FROM memories WHERE id=?1",
                [&record.entry.id],
                |r| r.get::<_, String>(0),
            )
            .optional()?
        {
            let old: MemoryRecord = serde_json::from_str(&payload)?;
            record.entry.created_at = old.entry.created_at;
            record.entry.updated_at = Utc::now();
        }
        tx.execute("INSERT INTO memories(id,namespace,access_scope,agent_id,session_id,kind,normalized_text,content,tags,expires_at,payload)
            VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11) ON CONFLICT(id) DO UPDATE SET namespace=excluded.namespace,
            access_scope=excluded.access_scope,agent_id=excluded.agent_id,session_id=excluded.session_id,kind=excluded.kind,
            normalized_text=excluded.normalized_text,content=excluded.content,tags=excluded.tags,expires_at=excluded.expires_at,payload=excluded.payload",
            params![record.entry.id, record.namespace, record.access_scope, agent, record.session_id, kind_name(record.kind), normalized,
                record.entry.content(), record.entry.tags().join(" "), record.expires_at.map(|t| t.timestamp()), serde_json::to_string(&record)?])?;
        let rowid: i64 = tx.query_row(
            "SELECT rowid FROM memories WHERE id=?1",
            [&record.entry.id],
            |r| r.get(0),
        )?;
        tx.execute("DELETE FROM memory_vectors WHERE rowid=?1", [rowid])?;
        tx.execute(
            "INSERT INTO memory_vectors(rowid,embedding) VALUES(?1,?2)",
            params![rowid, vector_blob(embedding)],
        )?;
        Ok(record.entry.id.clone())
    }
    fn inventory_in(db: &Connection, scope: &RetrievalScope) -> Result<Vec<MemoryRecord>> {
        if scope.namespace.trim().is_empty() || scope.access_scope.trim().is_empty()
            || scope.session_id.is_some() || scope.kind.is_some() || scope.tag.is_some() {
            return Err(invalid("inventory requires an ownership scope without search filters"));
        }
        let mut statement = db.prepare("SELECT payload FROM memories WHERE namespace=?1 AND access_scope=?2 AND agent_id=?3 ORDER BY rowid")?;
        let rows = statement.query_map(params![scope.namespace, scope.access_scope, scope.agent_id.as_deref().unwrap_or("")], |row| row.get::<_, String>(0))?;
        rows.map(|row| Ok(serde_json::from_str(&row?)?)).collect()
    }
    pub fn inventory(&self, scope: &RetrievalScope) -> Result<Vec<MemoryRecord>> {
        Self::inventory_in(&*self.db()?, scope)
    }
    /// Optimistic native-store batch: compare the complete inventory before any write.
    /// Embeddings are prepared outside the lock; conflicts never lose another writer's work.
    pub fn compare_exchange(
        &self, scope: &RetrievalScope, expected: &[MemoryRecord], desired: &[MemoryRecord],
        provider: &dyn EmbeddingBackend,
    ) -> Result<bool> {
        let mut ids = std::collections::HashSet::new();
        let mut prepared = Vec::new();
        for record in desired {
            record.validate()?;
            if record.namespace != scope.namespace || record.access_scope != scope.access_scope
                || record.agent_id != scope.agent_id || !ids.insert(&record.entry.id) {
                return Err(invalid("batch record outside ownership scope or duplicate ID"));
            }
            let previous = expected.iter().find(|old| old.entry.id == record.entry.id);
            if previous.map(serde_json::to_value).transpose()? != Some(serde_json::to_value(record)?) {
                let mut record = record.clone();
                record.entry.set_content(record.entry.content().to_owned());
                if let Some(old) = previous {
                    record.entry.created_at = old.entry.created_at;
                    record.entry.updated_at = Utc::now();
                }
                let embedding = provider.embed_passage(record.entry.content())?;
                self.check_embedding(&embedding)?;
                prepared.push((record, embedding));
            }
        }
        let mut db = self.db()?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current = Self::inventory_in(&tx, scope)?;
        if serde_json::to_value(&current)? != serde_json::to_value(expected)? {
            return Ok(false);
        }
        // Reject IDs belonging to any other inventory, even on trusted local transport.
        for record in desired {
            if !expected.iter().any(|old| old.entry.id == record.entry.id)
                && tx.query_row("SELECT EXISTS(SELECT 1 FROM memories WHERE id=?1)", [&record.entry.id], |row| row.get::<_, bool>(0))? {
                return Err(invalid("batch ID already exists outside inventory"));
            }
        }
        for old in expected {
            if !ids.contains(&old.entry.id) {
                tx.execute("DELETE FROM memories WHERE id=?1", [&old.entry.id])?;
            } else if prepared.iter().any(|(r, _)| r.entry.id == old.entry.id) {
                // Vacate dedup keys during a batch (e.g. two entries exchanging text).
                // Keep rowids, FTS relationships and public IDs stable.
                tx.execute("UPDATE memories SET normalized_text=char(0)||id WHERE id=?1", [&old.entry.id])?;
            }
        }
        for (record, embedding) in prepared {
            let normalized = record.entry.content().split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
            if self.save_in_transaction(&tx, &record, &embedding, false, &normalized)? != record.entry.id {
                return Err(invalid("batch contains duplicate normalized content"));
            }
        }
        tx.commit()?;
        Ok(true)
    }
    pub fn ingest(&self, record: &MemoryRecord, provider: &dyn EmbeddingBackend) -> Result<String> {
        record.validate()?;
        self.store(record, &provider.embed_passage(record.entry.content())?)
    }
    pub fn update(&self, record: &MemoryRecord, provider: &dyn EmbeddingBackend) -> Result<String> {
        record.validate()?;
        self.save(
            record,
            &provider.embed_passage(record.entry.content())?,
            true,
        )
    }
    pub fn get(&self, id: &str) -> Result<Option<MemoryRecord>> {
        self.db()?
            .query_row("SELECT payload FROM memories WHERE id=?1", [id], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
            .map(|s| serde_json::from_str(&s).map_err(Into::into))
            .transpose()
    }
    pub fn source(&self, id: &str) -> Result<Option<String>> {
        Ok(self.get(id)?.and_then(|r| r.source_ref))
    }
    pub fn delete(&self, id: &str) -> Result<bool> {
        let mut db = self.db()?;
        let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let removed = tx.execute("DELETE FROM memories WHERE id=?1", [id])? > 0;
        tx.commit()?;
        Ok(removed)
    }
    pub fn retrieve(
        &self,
        query: &str,
        scope: &RetrievalScope,
        k: usize,
        mode: RetrievalMode,
        provider: &dyn EmbeddingBackend,
    ) -> Result<Vec<RankedMemory>> {
        if k == 0 || query.trim().is_empty() {
            return Ok(Vec::new());
        }
        let k = k.min(self.config.max_top_k);
        let embedding = if matches!(mode, RetrievalMode::Keyword) {
            None
        } else {
            let e = provider.embed_query(query)?;
            self.check_embedding(&e)?;
            Some(e)
        };
        let lexical = query
            .split_whitespace()
            .map(|t| format!("\"{}\"", t.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(" OR ");
        let mut db = self.db()?;
        let tx = db.transaction()?; // One snapshot for both hybrid branches and payload reads.
        let mut hits: BTreeMap<i64, (f64, Option<f64>)> = BTreeMap::new();
        for vector in [true, false] {
            if (vector && embedding.is_none()) || (!vector && matches!(mode, RetrievalMode::Vector))
            {
                continue;
            }
            let sql = if vector {
                format!("WITH candidates AS MATERIALIZED (SELECT rowid,distance FROM memory_vectors WHERE embedding MATCH :query AND k=:k
                    AND rowid IN (SELECT m.rowid FROM memories m WHERE {FILTER}) ORDER BY distance)
                    SELECT rowid,distance FROM candidates ORDER BY distance,rowid")
            } else {
                format!("SELECT m.rowid,bm25(memories_fts) FROM memories_fts JOIN memories m ON m.rowid=memories_fts.rowid
                    WHERE memories_fts MATCH :query AND {FILTER} ORDER BY bm25(memories_fts),m.rowid LIMIT :k")
            };
            let query_value = if vector {
                rusqlite::types::Value::Blob(vector_blob(embedding.as_ref().unwrap()))
            } else {
                rusqlite::types::Value::Text(lexical.clone())
            };
            let mut statement = tx.prepare(&sql)?;
            let rows = statement.query_map(rusqlite::named_params! {
                ":query":query_value, ":k":k, ":ns":scope.namespace, ":scope":scope.access_scope,
                ":agent":scope.agent_id.as_deref().unwrap_or(""), ":session":scope.session_id,
                ":kind":scope.kind.map(kind_name), ":tag":scope.tag, ":now":Utc::now().timestamp()
            }, |r| Ok((r.get::<_, i64>(0)?, r.get::<_, f64>(1)?)))?;
            for (rank, row) in rows.enumerate() {
                let (id, raw) = row?;
                let entry = hits.entry(id).or_insert((0.0, None));
                entry.0 += if matches!(mode, RetrievalMode::Hybrid) {
                    1.0 / (self.config.rrf_constant + rank as f64 + 1.0)
                } else if vector {
                    1.0 - raw
                } else {
                    -raw
                };
                if vector {
                    entry.1 = Some(raw);
                }
            }
        }
        let mut ranked = hits.into_iter().collect::<Vec<_>>();
        ranked.sort_by(|a, b| b.1 .0.total_cmp(&a.1 .0).then(a.0.cmp(&b.0)));
        let mut results = Vec::with_capacity(k);
        for (id, (score, distance)) in ranked.into_iter().take(k) {
            let payload: String =
                tx.query_row("SELECT payload FROM memories WHERE rowid=?1", [id], |r| {
                    r.get(0)
                })?;
            results.push(RankedMemory {
                record: serde_json::from_str(&payload)?,
                score,
                distance,
            });
        }
        tx.commit()?;
        Ok(results)
    }
}

/// Synthetic token hashing for offline tests/benchmarks, NOT a semantic model.
pub struct SyntheticEmbedding {
    dimension: usize,
}
impl SyntheticEmbedding {
    pub fn new(dimension: usize) -> Result<Self> {
        if dimension == 0 || dimension > 8192 {
            return Err(invalid("dimension must be 1..8192"));
        }
        Ok(Self { dimension })
    }
}
impl EmbeddingBackend for SyntheticEmbedding {
    fn model_id(&self) -> &str {
        "synthetic-fnv1a-token-v1"
    }
    fn dim(&self) -> usize {
        self.dimension
    }
    fn embed(
        &self,
        _: crate::EmbedRole,
        text: &str,
    ) -> std::result::Result<Embedding, crate::EmbeddingError> {
        let mut v = vec![0.0; self.dimension];
        for token in text.to_lowercase().split_whitespace() {
            let hash = token.bytes().fold(14695981039346656037u64, |h, b| {
                (h ^ b as u64).wrapping_mul(1099511628211)
            });
            v[hash as usize % self.dimension] += 1.0;
        }
        Embedding::from_raw(self.model_id(), v, self.dimension)
    }
}
