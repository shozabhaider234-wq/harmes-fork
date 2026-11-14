//! Durable and in-memory persistence backends for embedded memories.

use std::collections::BTreeMap;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, RwLock};

use serde::{Deserialize, Serialize};

use crate::embedding::EmbeddedMemory;

pub const STORAGE_FORMAT_VERSION: u32 = 1;
const DEFAULT_MIN_COMPACT_LINES: usize = 64;
const DEFAULT_COMPACT_RATIO: usize = 2;

/// Controls fsync behavior on durable writes and compactions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SyncMode {
    /// Sync appended records / compacted snapshots to disk before returning.
    #[default]
    Durable,
    /// Flush userspace buffers and perform atomic renames without per-write fsync.
    Fast,
}

/// Errors produced when configuring, loading, validating, or mutating storage.
#[derive(Debug)]
pub enum StorageError {
    Io(std::io::Error),
    InvalidConfig(String),
    EmptyPath,
    InvalidId,
    UnsupportedFormatVersion { expected: u32, actual: u32 },
    CorruptRecord { line: usize, message: String },
    LockPoisoned,
}

impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(err) => write!(f, "storage I/O error: {err}"),
            Self::InvalidConfig(msg) => write!(f, "invalid storage config: {msg}"),
            Self::EmptyPath => f.write_str("storage file path must include a non-empty file name"),
            Self::InvalidId => f.write_str("memory id cannot be empty"),
            Self::UnsupportedFormatVersion { expected, actual } => write!(
                f,
                "unsupported storage format version {actual} (expected {expected})"
            ),
            Self::CorruptRecord { line, message } => {
                write!(f, "corrupt storage record at line {line}: {message}")
            }
            Self::LockPoisoned => f.write_str("storage lock poisoned"),
        }
    }
}

impl std::error::Error for StorageError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(err) => Some(err),
            _ => None,
        }
    }
}

impl From<std::io::Error> for StorageError {
    fn from(err: std::io::Error) -> Self {
        Self::Io(err)
    }
}

/// Record-oriented persistence contract decoupling retrieval/engine callers
/// from the backing storage representation.
pub trait MemoryStore: Send + Sync {
    fn load_all(&self) -> Result<Vec<EmbeddedMemory>, StorageError>;
    fn get(&self, id: &str) -> Result<Option<EmbeddedMemory>, StorageError>;
    fn upsert(&self, memory: &EmbeddedMemory) -> Result<(), StorageError>;
    fn upsert_batch(&self, memories: &[EmbeddedMemory]) -> Result<(), StorageError>;
    fn delete(&self, id: &str) -> Result<bool, StorageError>;
    fn len(&self) -> Result<usize, StorageError>;

    fn is_empty(&self) -> Result<bool, StorageError> {
        Ok(self.len()? == 0)
    }
}

/// In-memory `MemoryStore` for tests and ephemeral sessions.
#[derive(Debug, Default)]
pub struct InMemoryStore {
    entries: RwLock<BTreeMap<String, EmbeddedMemory>>,
}

impl InMemoryStore {
    pub fn new() -> Self {
        Self::default()
    }
}

impl MemoryStore for InMemoryStore {
    fn load_all(&self) -> Result<Vec<EmbeddedMemory>, StorageError> {
        let entries = self.entries.read().map_err(|_| StorageError::LockPoisoned)?;
        Ok(entries.values().cloned().collect())
    }

    fn get(&self, id: &str) -> Result<Option<EmbeddedMemory>, StorageError> {
        validate_id(id)?;
        let entries = self.entries.read().map_err(|_| StorageError::LockPoisoned)?;
        Ok(entries.get(id).cloned())
    }

    fn upsert(&self, memory: &EmbeddedMemory) -> Result<(), StorageError> {
        validate_id(&memory.entry().id)?;
        let mut entries = self
            .entries
            .write()
            .map_err(|_| StorageError::LockPoisoned)?;
        entries.insert(memory.entry().id.clone(), memory.clone());
        Ok(())
    }

    fn upsert_batch(&self, memories: &[EmbeddedMemory]) -> Result<(), StorageError> {
        for memory in memories {
            validate_id(&memory.entry().id)?;
        }
        if memories.is_empty() {
            return Ok(());
        }
        let mut entries = self
            .entries
            .write()
            .map_err(|_| StorageError::LockPoisoned)?;
        for memory in memories {
            entries.insert(memory.entry().id.clone(), memory.clone());
        }
        Ok(())
    }

    fn delete(&self, id: &str) -> Result<bool, StorageError> {
        validate_id(id)?;
        let mut entries = self
            .entries
            .write()
            .map_err(|_| StorageError::LockPoisoned)?;
        Ok(entries.remove(id).is_some())
    }

    fn len(&self) -> Result<usize, StorageError> {
        let entries = self.entries.read().map_err(|_| StorageError::LockPoisoned)?;
        Ok(entries.len())
    }
}

/// Configuration for `JsonlStore`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct JsonlStoreConfig {
    pub sync_mode: SyncMode,
    /// Minimum total log lines required before automatic compaction triggers.
    pub min_compact_lines: usize,
    /// Compact when `total_log_lines >= max(min_compact_lines, live_entries * compact_ratio)`.
    pub compact_ratio: usize,
}

impl Default for JsonlStoreConfig {
    fn default() -> Self {
        Self {
            sync_mode: SyncMode::Durable,
            min_compact_lines: DEFAULT_MIN_COMPACT_LINES,
            compact_ratio: DEFAULT_COMPACT_RATIO,
        }
    }
}

impl JsonlStoreConfig {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_sync_mode(mut self, sync_mode: SyncMode) -> Self {
        self.sync_mode = sync_mode;
        self
    }

    pub fn with_compaction(
        mut self,
        min_compact_lines: usize,
        compact_ratio: usize,
    ) -> Self {
        self.min_compact_lines = min_compact_lines;
        self.compact_ratio = compact_ratio;
        self
    }

    pub fn validate(&self) -> Result<(), StorageError> {
        if self.min_compact_lines == 0 {
            return Err(StorageError::InvalidConfig(
                "min_compact_lines must be greater than zero".into(),
            ));
        }
        if self.compact_ratio == 0 {
            return Err(StorageError::InvalidConfig(
                "compact_ratio must be greater than zero".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Serialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum JournalRecordRef<'a> {
    Upsert {
        v: u32,
        memory: &'a EmbeddedMemory,
    },
    Delete {
        v: u32,
        id: &'a str,
    },
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum JournalRecordOwned {
    Upsert {
        v: u32,
        memory: Box<EmbeddedMemory>,
    },
    Delete {
        v: u32,
        id: String,
    },
}

#[derive(Deserialize)]
struct LineVersionProbe {
    v: u32,
}

#[derive(Debug)]
struct JsonlState {
    entries: BTreeMap<String, EmbeddedMemory>,
    total_lines: usize,
    temp_seq: u64,
}

/// Durable append-only JSONL memory store with automatic atomic compaction.
///
/// Mutating operations append single-line JSON records ($O(1)$ per item) instead
/// of rewriting the full corpus on every write, compacting atomically via a
/// sibling temporary file once superseded lines or tombstones pass threshold.
#[derive(Debug)]
pub struct JsonlStore {
    path: PathBuf,
    config: JsonlStoreConfig,
    state: Mutex<JsonlState>,
}

impl JsonlStore {
    /// Open or initialize a durable JSONL store at caller-supplied `path`.
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, StorageError> {
        Self::open_with_config(path, JsonlStoreConfig::default())
    }

    /// Open or initialize a JSONL store at `path` with explicit sync and compaction policy.
    pub fn open_with_config(
        path: impl Into<PathBuf>,
        config: JsonlStoreConfig,
    ) -> Result<Self, StorageError> {
        config.validate()?;
        let path = path.into();
        validate_store_path(&path)?;
        ensure_parent_dir(&path)?;
        let (entries, total_lines) = read_journal_state(&path)?;

        Ok(Self {
            path,
            config,
            state: Mutex::new(JsonlState {
                entries,
                total_lines,
                temp_seq: 0,
            }),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn config(&self) -> JsonlStoreConfig {
        self.config
    }

    pub fn total_log_lines(&self) -> Result<usize, StorageError> {
        let state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        Ok(state.total_lines)
    }

    /// Reload live state cleanly from disk (rejecting any corrupt records).
    pub fn reload(&self) -> Result<(), StorageError> {
        let (entries, total_lines) = read_journal_state(&self.path)?;
        let mut state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        state.entries = entries;
        state.total_lines = total_lines;
        Ok(())
    }

    /// Rewrite the log atomically so it contains only the currently live entries.
    pub fn compact(&self) -> Result<(), StorageError> {
        let mut state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        let seq = state.temp_seq;
        state.temp_seq = state.temp_seq.wrapping_add(1);
        let live_count = state.entries.len();
        {
            let live_entries: Vec<&EmbeddedMemory> = state.entries.values().collect();
            write_compacted_snapshot(&self.path, seq, &live_entries, self.config.sync_mode)?;
        }
        state.total_lines = live_count;
        Ok(())
    }

    fn should_compact(&self, projected_total_lines: usize, projected_live_entries: usize) -> bool {
        let threshold = self
            .config
            .min_compact_lines
            .max(projected_live_entries.saturating_mul(self.config.compact_ratio));
        projected_total_lines >= threshold
    }

    fn commit_compacted(
        &self,
        state: &mut JsonlState,
        next_entries: BTreeMap<String, EmbeddedMemory>,
    ) -> Result<(), StorageError> {
        let seq = state.temp_seq;
        state.temp_seq = state.temp_seq.wrapping_add(1);
        let live_count = next_entries.len();
        {
            let live_refs: Vec<&EmbeddedMemory> = next_entries.values().collect();
            write_compacted_snapshot(&self.path, seq, &live_refs, self.config.sync_mode)?;
        }
        state.entries = next_entries;
        state.total_lines = live_count;
        Ok(())
    }
}

impl MemoryStore for JsonlStore {
    fn load_all(&self) -> Result<Vec<EmbeddedMemory>, StorageError> {
        let state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        Ok(state.entries.values().cloned().collect())
    }

    fn get(&self, id: &str) -> Result<Option<EmbeddedMemory>, StorageError> {
        validate_id(id)?;
        let state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        Ok(state.entries.get(id).cloned())
    }

    fn upsert(&self, memory: &EmbeddedMemory) -> Result<(), StorageError> {
        self.upsert_batch(std::slice::from_ref(memory))
    }

    fn upsert_batch(&self, memories: &[EmbeddedMemory]) -> Result<(), StorageError> {
        for memory in memories {
            validate_id(&memory.entry().id)?;
        }
        if memories.is_empty() {
            return Ok(());
        }

        let mut state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        let new_live_count = {
            let mut next = BTreeMap::new();
            let mut added_unique = 0usize;
            for memory in memories {
                let id = memory.entry().id.as_str();
                if !state.entries.contains_key(id) && next.insert(id, ()).is_none() {
                    added_unique += 1;
                }
            }
            state.entries.len() + added_unique
        };
        let projected_lines = state.total_lines + memories.len();

        if self.should_compact(projected_lines, new_live_count) {
            let mut next_entries = state.entries.clone();
            for memory in memories {
                next_entries.insert(memory.entry().id.clone(), memory.clone());
            }
            return self.commit_compacted(&mut state, next_entries);
        }

        let mut payload = Vec::new();
        for memory in memories {
            encode_journal_line(
                &mut payload,
                &JournalRecordRef::Upsert {
                    v: STORAGE_FORMAT_VERSION,
                    memory,
                },
            )?;
        }
        append_raw_lines(&self.path, &payload, self.config.sync_mode)?;

        for memory in memories {
            state
                .entries
                .insert(memory.entry().id.clone(), memory.clone());
        }
        state.total_lines = projected_lines;
        Ok(())
    }

    fn delete(&self, id: &str) -> Result<bool, StorageError> {
        validate_id(id)?;
        let mut state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        if !state.entries.contains_key(id) {
            return Ok(false);
        }

        let projected_live = state.entries.len().saturating_sub(1);
        let projected_lines = state.total_lines + 1;

        if self.should_compact(projected_lines, projected_live) {
            let mut next_entries = state.entries.clone();
            next_entries.remove(id);
            self.commit_compacted(&mut state, next_entries)?;
            return Ok(true);
        }

        let mut payload = Vec::new();
        encode_journal_line(
            &mut payload,
            &JournalRecordRef::Delete {
                v: STORAGE_FORMAT_VERSION,
                id,
            },
        )?;
        append_raw_lines(&self.path, &payload, self.config.sync_mode)?;

        state.entries.remove(id);
        state.total_lines = projected_lines;
        Ok(true)
    }

    fn len(&self) -> Result<usize, StorageError> {
        let state = self.state.lock().map_err(|_| StorageError::LockPoisoned)?;
        Ok(state.entries.len())
    }
}

fn validate_id(id: &str) -> Result<(), StorageError> {
    if id.trim().is_empty() {
        return Err(StorageError::InvalidId);
    }
    Ok(())
}

fn validate_store_path(path: &Path) -> Result<(), StorageError> {
    if path.as_os_str().is_empty() || path.file_name().is_none() {
        return Err(StorageError::EmptyPath);
    }
    if let Ok(meta) = fs::symlink_metadata(path) {
        if meta.is_dir() {
            return Err(StorageError::InvalidConfig(format!(
                "storage path {} is a directory",
                path.display()
            )));
        }
    }
    Ok(())
}

fn ensure_parent_dir(path: &Path) -> Result<(), StorageError> {
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    if parent.as_os_str().is_empty() || parent.exists() {
        return Ok(());
    }

    // Collect only the missing suffix of ancestors so each newly created
    // directory is owner-only without altering pre-existing dirs like `/tmp`.
    let mut missing = Vec::new();
    let mut cur = Some(parent);
    while let Some(dir) = cur {
        if dir.as_os_str().is_empty() || dir.exists() {
            break;
        }
        missing.push(dir);
        cur = dir.parent();
    }

    for dir in missing.into_iter().rev() {
        match fs::create_dir(dir) {
            Ok(()) => set_dir_owner_only(dir)?,
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(err) => return Err(err.into()),
        }
    }
    Ok(())
}

fn read_journal_state(
    path: &Path,
) -> Result<(BTreeMap<String, EmbeddedMemory>, usize), StorageError> {
    if !path.exists() {
        return Ok((BTreeMap::new(), 0));
    }

    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut entries = BTreeMap::new();
    let mut total_lines = 0usize;

    for (idx, line_res) in reader.lines().enumerate() {
        let line_no = idx + 1;
        let line = line_res?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        total_lines += 1;

        let record: JournalRecordOwned = match serde_json::from_str(trimmed) {
            Ok(rec) => rec,
            Err(parse_err) => {
                if let Ok(probe) = serde_json::from_str::<LineVersionProbe>(trimmed) {
                    if probe.v != STORAGE_FORMAT_VERSION {
                        return Err(StorageError::UnsupportedFormatVersion {
                            expected: STORAGE_FORMAT_VERSION,
                            actual: probe.v,
                        });
                    }
                }
                return Err(StorageError::CorruptRecord {
                    line: line_no,
                    message: parse_err.to_string(),
                });
            }
        };

        match record {
            JournalRecordOwned::Upsert { v, memory } => {
                if v != STORAGE_FORMAT_VERSION {
                    return Err(StorageError::UnsupportedFormatVersion {
                        expected: STORAGE_FORMAT_VERSION,
                        actual: v,
                    });
                }
                if memory.entry().id.trim().is_empty() {
                    return Err(StorageError::CorruptRecord {
                        line: line_no,
                        message: "memory entry id cannot be empty".into(),
                    });
                }
                entries.insert(memory.entry().id.clone(), *memory);
            }
            JournalRecordOwned::Delete { v, id } => {
                if v != STORAGE_FORMAT_VERSION {
                    return Err(StorageError::UnsupportedFormatVersion {
                        expected: STORAGE_FORMAT_VERSION,
                        actual: v,
                    });
                }
                if id.trim().is_empty() {
                    return Err(StorageError::CorruptRecord {
                        line: line_no,
                        message: "delete record id cannot be empty".into(),
                    });
                }
                entries.remove(&id);
            }
        }
    }

    Ok((entries, total_lines))
}

fn encode_journal_line(buf: &mut Vec<u8>, record: &JournalRecordRef<'_>) -> Result<(), StorageError> {
    serde_json::to_writer(&mut *buf, record).map_err(|err| StorageError::InvalidConfig(format!("failed to serialize storage record: {err}")))?;
    buf.push(b'\n');
    Ok(())
}

fn open_append_owner_only(path: &Path) -> Result<(File, bool), StorageError> {
    let newly_created = !path.exists();
    let mut opts = OpenOptions::new();
    opts.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let file = opts.open(path)?;
    if newly_created {
        set_file_owner_only(path)?;
    }
    Ok((file, newly_created))
}

fn append_raw_lines(path: &Path, bytes: &[u8], sync_mode: SyncMode) -> Result<(), StorageError> {
    ensure_parent_dir(path)?;
    let (mut file, newly_created) = open_append_owner_only(path)?;
    file.write_all(bytes)?;
    if sync_mode == SyncMode::Durable {
        file.sync_data()?;
        if newly_created {
            sync_parent_dir(path);
        }
    }
    Ok(())
}

fn temp_sibling_path(path: &Path, seq: u64) -> PathBuf {
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("memcore_store");
    let nonce: u64 = rand::random();
    path.with_file_name(format!(
        ".{file_name}.tmp.{}.{seq}.{nonce}",
        std::process::id()
    ))
}

fn write_compacted_snapshot(
    path: &Path,
    seq: u64,
    live_entries: &[&EmbeddedMemory],
    sync_mode: SyncMode,
) -> Result<(), StorageError> {
    ensure_parent_dir(path)?;
    let tmp_path = temp_sibling_path(path, seq);

    let result = (|| -> Result<(), StorageError> {
        let mut opts = OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let file = opts.open(&tmp_path)?;
        set_file_owner_only(&tmp_path)?;

        let mut writer = BufWriter::new(file);
        let mut line_buf = Vec::new();
        for memory in live_entries {
            line_buf.clear();
            encode_journal_line(
                &mut line_buf,
                &JournalRecordRef::Upsert {
                    v: STORAGE_FORMAT_VERSION,
                    memory,
                },
            )?;
            writer.write_all(&line_buf)?;
        }

        let file = writer
            .into_inner()
            .map_err(|err| StorageError::Io(err.into_error()))?;
        if sync_mode == SyncMode::Durable {
            file.sync_all()?;
        }

        fs::rename(&tmp_path, path)?;
        if sync_mode == SyncMode::Durable {
            sync_parent_dir(path);
        }
        Ok(())
    })();

    if result.is_err() {
        let _ = fs::remove_file(&tmp_path);
    }

    result
}

fn sync_parent_dir(path: &Path) {
    let Some(parent) = path.parent() else {
        return;
    };
    if parent.as_os_str().is_empty() {
        return;
    }
    if let Ok(dir) = File::open(parent) {
        let _ = dir.sync_all();
    }
}

fn set_dir_owner_only(path: &Path) -> Result<(), StorageError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn set_file_owner_only(path: &Path) -> Result<(), StorageError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embedding::{EmbedRole, Embedding, EmbeddingBackend, EmbeddingError};
    use crate::types::{MemoryCategory, MemoryEntry};
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU64, Ordering};

    static TEST_DIR_SEQ: AtomicU64 = AtomicU64::new(0);

    struct TestDir {
        path: PathBuf,
    }

    impl TestDir {
        fn new(label: &str) -> Self {
            let seq = TEST_DIR_SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "memcore_storage_test_{label}_{}_{seq}",
                std::process::id()
            ));
            fs::create_dir_all(&path).expect("create temp test dir");
            Self { path }
        }

        fn child(&self, name: &str) -> PathBuf {
            self.path.join(name)
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    struct StubBackend;

    impl EmbeddingBackend for StubBackend {
        fn model_id(&self) -> &str {
            "stub-v1"
        }

        fn dim(&self) -> usize {
            3
        }

        fn embed(&self, _role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError> {
            let lower = text.to_ascii_lowercase();
            let raw = vec![
                if lower.contains("rust") { 4.0 } else { 1.0 },
                if lower.contains("jsonl") { 3.0 } else { 0.5 },
                if lower.contains("sqlite") { 2.0 } else { 0.25 },
            ];
            Embedding::from_raw(self.model_id(), raw, 3)
        }
    }

    fn make_memory(id: &str, content: &str, tags: Vec<&str>) -> EmbeddedMemory {
        let entry = MemoryEntry::new(
            content,
            MemoryCategory::Fact,
            tags.into_iter().map(str::to_string).collect(),
        )
        .with_id(id);
        EmbeddedMemory::from_entry(entry, &StubBackend).expect("embed memory")
    }

    fn assert_store_contract(store: &dyn MemoryStore) {
        assert!(store.is_empty().unwrap());
        let m1 = make_memory("mem-1", "Use Rust workspace", vec!["rust", "cargo"]);
        let m2 = make_memory("mem-2", "Use JSONL WAL storage", vec!["storage"]);

        store.upsert(&m1).unwrap();
        store.upsert(&m2).unwrap();
        assert_eq!(store.len().unwrap(), 2);
        assert_eq!(store.get("mem-1").unwrap(), Some(m1.clone()));
        assert_eq!(store.get("missing").unwrap(), None);

        let mut updated = m1.clone();
        updated.set_content("Prefer Rust 2021 edition", &StubBackend).unwrap();
        store.upsert(&updated).unwrap();
        assert_eq!(store.len().unwrap(), 2);
        assert_eq!(store.get("mem-1").unwrap(), Some(updated.clone()));

        assert!(store.delete("mem-2").unwrap());
        assert!(!store.delete("mem-2").unwrap());
        assert_eq!(store.len().unwrap(), 1);

        let all = store.load_all().unwrap();
        assert_eq!(all, vec![updated]);
    }

    #[test]
    fn in_memory_store_satisfies_contract_and_rejects_empty_ids() {
        let store = InMemoryStore::new();
        assert_store_contract(&store);

        assert!(matches!(store.get("  "), Err(StorageError::InvalidId)));
        assert!(matches!(store.delete(""), Err(StorageError::InvalidId)));

        let bad_mem = make_memory("   ", "bad", vec![]);
        assert!(matches!(store.upsert(&bad_mem), Err(StorageError::InvalidId)));

        let good = make_memory("ok-1", "good", vec![]);
        assert!(matches!(
            store.upsert_batch(&[good.clone(), bad_mem]),
            Err(StorageError::InvalidId)
        ));
        assert!(store.get("ok-1").unwrap().is_none());
    }

    #[test]
    fn jsonl_store_persists_reopens_and_preserves_updates_and_deletes() {
        let dir = TestDir::new("roundtrip");
        let file_path = dir.child("nested/memories.jsonl");

        let m1 = make_memory("mem-a", "Use Rust workspace", vec!["rust"]);
        let mut m2 = make_memory("mem-b", "Initial note", vec!["notes"]);
        let m3 = make_memory("mem-c", "Ephemeral memory", vec![]);

        {
            let store = JsonlStore::open(&file_path).unwrap();
            assert_store_contract(&store);

            // Start fresh on a clean second store for explicit restart checks.
            let _ = store.delete("mem-1").unwrap();
        }

        let path2 = dir.child("nested/reopen.jsonl");
        {
            let store = JsonlStore::open(&path2).unwrap();
            store.upsert_batch(&[m1.clone(), m2.clone(), m3.clone()]).unwrap();
            m2.set_content("Updated Rust note", &StubBackend).unwrap();
            store.upsert(&m2).unwrap();
            assert!(store.delete("mem-c").unwrap());
            assert_eq!(store.total_log_lines().unwrap(), 5);
        }

        let reopened = JsonlStore::open(&path2).unwrap();
        assert_eq!(reopened.len().unwrap(), 2);
        assert_eq!(reopened.get("mem-a").unwrap(), Some(m1.clone()));
        assert_eq!(reopened.get("mem-b").unwrap(), Some(m2.clone()));
        assert_eq!(reopened.get("mem-c").unwrap(), None);
        assert_eq!(reopened.load_all().unwrap(), vec![m1, m2]);
    }

    #[test]
    fn jsonl_store_auto_compacts_atomically_without_losing_live_entries() {
        let dir = TestDir::new("compact");
        let path = dir.child("memories.jsonl");
        let cfg = JsonlStoreConfig::new()
            .with_sync_mode(SyncMode::Fast)
            .with_compaction(6, 2);

        let store = JsonlStore::open_with_config(&path, cfg).unwrap();
        let mut live = make_memory("mem-1", "version 0", vec!["tag1"]);
        for i in 0..6 {
            live.set_content(format!("version {i}"), &StubBackend).unwrap();
            store.upsert(&live).unwrap();
        }

        // Compaction ran on the 6th line because total lines (6) >= min(6) and 6 >= 1*2.
        assert_eq!(store.total_log_lines().unwrap(), 1);
        assert_eq!(store.get("mem-1").unwrap(), Some(live.clone()));

        let contents = fs::read_to_string(&path).unwrap();
        assert_eq!(contents.lines().count(), 1);

        let reopened = JsonlStore::open_with_config(&path, cfg).unwrap();
        assert_eq!(reopened.load_all().unwrap(), vec![live]);
    }

    #[test]
    fn jsonl_store_compact_to_empty_leaves_zero_lines_and_reloads_empty() {
        let dir = TestDir::new("compact_empty");
        let path = dir.child("memories.jsonl");
        let store = JsonlStore::open(&path).unwrap();
        let m1 = make_memory("mem-only", "temporary", vec![]);
        store.upsert(&m1).unwrap();
        assert!(store.delete("mem-only").unwrap());
        assert_eq!(store.total_log_lines().unwrap(), 2);

        store.compact().unwrap();
        assert_eq!(store.total_log_lines().unwrap(), 0);
        assert_eq!(fs::read_to_string(&path).unwrap(), "");

        let reopened = JsonlStore::open(&path).unwrap();
        assert!(reopened.is_empty().unwrap());
    }

    #[test]
    fn jsonl_store_fails_loudly_on_corrupt_or_unsupported_version_records() {
        let dir = TestDir::new("strict");

        // 1. Missing `search_text` inside entry violates Stage 1 invariant.
        let missing_search_path = dir.child("missing_search.jsonl");
        let bad_entry = serde_json::json!({
            "op": "upsert",
            "v": 1,
            "memory": {
                "entry": {
                    "id": "mem-1",
                    "content": "hello",
                    "category": "fact",
                    "tags": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z"
                },
                "embedding": {
                    "model_id": "stub-v1",
                    "vector": [1.0, 0.0, 0.0]
                }
            }
        });
        fs::write(&missing_search_path, format!("{bad_entry}\n")).unwrap();
        assert!(matches!(
            JsonlStore::open(&missing_search_path),
            Err(StorageError::CorruptRecord { line: 1, .. })
        ));

        // 2. Unnormalized vector violates Stage 2 invariant.
        let unnorm_path = dir.child("unnormalized.jsonl");
        let bad_vec = serde_json::json!({
            "op": "upsert",
            "v": 1,
            "memory": {
                "entry": {
                    "id": "mem-1",
                    "content": "hello",
                    "category": "fact",
                    "tags": [],
                    "search_text": "hello",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z"
                },
                "embedding": {
                    "model_id": "stub-v1",
                    "vector": [3.0, 4.0, 0.0]
                }
            }
        });
        fs::write(&unnorm_path, format!("{bad_vec}\n")).unwrap();
        assert!(matches!(
            JsonlStore::open(&unnorm_path),
            Err(StorageError::CorruptRecord { line: 1, .. })
        ));

        // 3. Unsupported format version fails loudly with explicit error.
        let bad_ver_path = dir.child("future_ver.jsonl");
        fs::write(
            &bad_ver_path,
            "{\"op\":\"future_op\",\"v\":99,\"payload\":{}}\n",
        )
        .unwrap();
        assert!(matches!(
            JsonlStore::open(&bad_ver_path),
            Err(StorageError::UnsupportedFormatVersion {
                expected: 1,
                actual: 99
            })
        ));

        // 4. Truncated/torn tail line fails loudly instead of silently dropping records.
        let torn_path = dir.child("torn.jsonl");
        let valid = make_memory("mem-ok", "valid record", vec![]);
        let s = JsonlStore::open(&torn_path).unwrap();
        s.upsert(&valid).unwrap();
        drop(s);
        let mut raw = fs::read_to_string(&torn_path).unwrap();
        raw.push_str("{\"op\":\"upsert\",\"v\":1,\"memory\":");
        fs::write(&torn_path, raw).unwrap();
        assert!(matches!(
            JsonlStore::open(&torn_path),
            Err(StorageError::CorruptRecord { line: 2, .. })
        ));
    }

    #[cfg(unix)]
    #[test]
    fn jsonl_store_sets_owner_only_permissions_for_created_dir_and_file() {
        use std::os::unix::fs::PermissionsExt;

        let dir = TestDir::new("perms");
        let nested_dir = dir.child("secure_store");
        let file_path = nested_dir.join("memories.jsonl");

        let store = JsonlStore::open(&file_path).unwrap();
        let mem = make_memory("mem-sec", "Secret-free project fact", vec![]);
        store.upsert(&mem).unwrap();

        let dir_mode = fs::metadata(&nested_dir).unwrap().permissions().mode() & 0o777;
        let file_mode = fs::metadata(&file_path).unwrap().permissions().mode() & 0o777;
        assert_eq!(dir_mode, 0o700);
        assert_eq!(file_mode, 0o600);

        store.compact().unwrap();
        let compacted_mode = fs::metadata(&file_path).unwrap().permissions().mode() & 0o777;
        assert_eq!(compacted_mode, 0o600);
    }

    #[test]
    fn jsonl_store_serializes_concurrent_writes_safely() {
        let dir = TestDir::new("concurrent");
        let path = dir.child("concurrent.jsonl");
        let store = Arc::new(
            JsonlStore::open_with_config(
                &path,
                JsonlStoreConfig::new()
                    .with_sync_mode(SyncMode::Fast)
                    .with_compaction(10, 2),
            )
            .unwrap(),
        );

        std::thread::scope(|scope| {
            for t in 0..4 {
                let store = Arc::clone(&store);
                scope.spawn(move || {
                    for i in 0..10 {
                        let mem = make_memory(
                            &format!("t{t}-m{i}"),
                            &format!("Thread {t} memory {i} using Rust JSONL"),
                            vec!["concurrent"],
                        );
                        store.upsert(&mem).unwrap();
                    }
                });
            }
        });

        assert_eq!(store.len().unwrap(), 40);
        let reopened = JsonlStore::open(&path).unwrap();
        assert_eq!(reopened.len().unwrap(), 40);
    }

    #[test]
    fn config_and_path_validation_reject_invalid_inputs() {
        let dir = TestDir::new("bad_path");
        assert!(matches!(
            JsonlStore::open(&dir.path),
            Err(StorageError::InvalidConfig(_))
        ));
        assert!(matches!(
            JsonlStore::open(""),
            Err(StorageError::EmptyPath)
        ));
        assert!(matches!(
            JsonlStoreConfig::new()
                .with_compaction(0, 2)
                .validate(),
            Err(StorageError::InvalidConfig(_))
        ));
        assert!(matches!(
            JsonlStoreConfig::new()
                .with_compaction(10, 0)
                .validate(),
            Err(StorageError::InvalidConfig(_))
        ));
    }

    #[test]
    fn persisted_memories_reload_and_rank_with_similarity_search() {
        let dir = TestDir::new("search_roundtrip");
        let path = dir.child("a/b/memories.jsonl");

        let m_rust = make_memory("mem-rust", "Cargo builds Rust crates cleanly", vec!["rust"]);
        let m_sql = make_memory("mem-sql", "SQLite uses WAL checkpoints", vec!["db"]);

        {
            let store = JsonlStore::open(&path).unwrap();
            store.upsert_batch(&[m_rust.clone(), m_sql.clone()]).unwrap();
        }

        let reopened = JsonlStore::open(&path).unwrap();
        let loaded = reopened.load_all().unwrap();
        let query = StubBackend.embed_query("rust cargo").unwrap();
        let hits = crate::embedding::find_similar_memories(&query, &loaded, 0.5, 5);
        assert!(!hits.is_empty());
        assert_eq!(hits[0].memory.entry().id, "mem-rust");

        // External write followed by reload picks up new state cleanly.
        let writer2 = JsonlStore::open(&path).unwrap();
        writer2.delete("mem-sql").unwrap();
        assert_eq!(reopened.len().unwrap(), 2);
        reopened.reload().unwrap();
        assert_eq!(reopened.len().unwrap(), 1);
        assert_eq!(reopened.get("mem-sql").unwrap(), None);
    }
}
