use memcore::*;
use rusqlite::Connection;
use std::sync::Arc;

#[test]
fn native_inventory_batch_is_atomic_and_preserves_ids_and_indexes() {
    let f = Fixture::new();
    f.put("a", "Cargo workspace");
    f.put("b", "Rust compiler");
    let scope = RetrievalScope::default();
    let original = f.db().inventory(&scope).unwrap();
    let mut desired = original.clone();
    desired[0].entry.set_content("Rust compiler");
    desired[1].entry.set_content("Cargo workspace");
    assert!(f.db().compare_exchange(&scope, &original, &desired, &f.provider).unwrap());
    let edited = f.db().inventory(&scope).unwrap();
    assert_eq!(edited[0].entry.id, "a");
    assert_eq!(edited[0].entry.created_at, original[0].entry.created_at);
    assert_eq!(f.find("Cargo", RetrievalMode::Keyword)[0].record.entry.id, "b");
    assert!(!f.db().compare_exchange(&scope, &original, &[], &f.provider).unwrap());
    let mut collision = edited.clone();
    collision.push(Fixture::record("duplicate", "Cargo workspace"));
    assert!(f.db().compare_exchange(&scope, &edited, &collision, &f.provider).is_err());
    assert_eq!(f.db().inventory(&scope).unwrap().len(), 2);
    let mut foreign = Fixture::record("foreign", "private other profile");
    foreign.namespace = "other".into();
    f.db().ingest(&foreign, &f.provider).unwrap();
    let mut stolen = foreign.clone();
    stolen.namespace = scope.namespace.clone();
    assert!(f.db().compare_exchange(&scope, &edited, &[stolen], &f.provider).is_err());
    assert!(f.db().compare_exchange(&scope, &edited, &[], &f.provider).unwrap());
    assert!(f.find("Cargo", RetrievalMode::Hybrid).is_empty());
    assert!(f.db().get("foreign").unwrap().is_some());
}

#[test]
fn native_inventory_concurrent_writers_cannot_lose_a_commit() {
    let f = Fixture::new();
    let scope = RetrievalScope::default();
    let snapshot = f.db().inventory(&scope).unwrap();
    let barrier = std::sync::Barrier::new(2);
    let committed = std::thread::scope(|threads| {
        let workers: Vec<_> = (0..2).map(|i| {
            let (f, barrier, scope, snapshot) = (&f, &barrier, &scope, &snapshot);
            threads.spawn(move || {
                barrier.wait();
                f.db().compare_exchange(scope, snapshot, &[Fixture::record(&format!("writer-{i}"), &format!("memory {i}"))], &f.provider).unwrap()
            })
        }).collect();
        workers.into_iter().map(|worker| worker.join().unwrap()).filter(|committed| *committed).count()
    });
    assert_eq!(committed, 1);
    assert_eq!(f.db().inventory(&scope).unwrap().len(), 1);
}

struct Fixture {
    path: std::path::PathBuf,
    store: Option<SqliteStore>,
    provider: SyntheticEmbedding,
}
impl Fixture {
    fn new() -> Self {
        let path =
            std::env::temp_dir().join(format!("memcore-sqlite-{}.db", rand::random::<u64>()));
        let provider = SyntheticEmbedding::new(64).unwrap();
        let store = Some(
            SqliteStore::open(
                &path,
                provider.model_id(),
                provider.dim(),
                SqliteConfig::default(),
            )
            .unwrap(),
        );
        Self {
            path,
            store,
            provider,
        }
    }
    fn db(&self) -> &SqliteStore {
        self.store.as_ref().unwrap()
    }
    fn record(id: &str, text: &str) -> MemoryRecord {
        MemoryRecord::new(
            MemoryEntry::new(text, MemoryCategory::Fact, vec!["rust".into()]).with_id(id),
        )
    }
    fn put(&self, id: &str, text: &str) {
        self.db()
            .ingest(&Self::record(id, text), &self.provider)
            .unwrap();
    }
    fn find(&self, query: &str, mode: RetrievalMode) -> Vec<RankedMemory> {
        self.db()
            .retrieve(query, &RetrievalScope::default(), 5, mode, &self.provider)
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.store.take();
        if let Err(error) = std::fs::remove_file(&self.path) {
            eprintln!("temporary database cleanup failed: {error}");
        }
    }
}

#[test]
fn initialization_migration_and_model_identity() {
    let f = Fixture::new();
    let db = Connection::open(&f.path).unwrap();
    assert_eq!(
        db.query_row("PRAGMA user_version", [], |r| r.get::<_, u32>(0))
            .unwrap(),
        2
    );
    assert!(SqliteStore::open(&f.path, "other-model", 64, SqliteConfig::default()).is_err());
    assert!(
        SqliteStore::open(&f.path, f.provider.model_id(), 32, SqliteConfig::default()).is_err()
    );
    assert!(SqliteStore::open(&f.path, "", 0, SqliteConfig::default()).is_err());
    drop(db);
    // A released migration remains runnable independently of current initialization.
    let path = std::env::temp_dir().join(format!("memcore-migrate-{}.db", rand::random::<u64>()));
    let db = Connection::open(&path).unwrap();
    db.execute_batch(include_str!("../src/migrations/001.sql"))
        .unwrap();
    drop(db);
    let upgraded =
        SqliteStore::open(&path, f.provider.model_id(), 64, SqliteConfig::default()).unwrap();
    upgraded
        .ingest(
            &Fixture::record("migrated", "schema migration"),
            &f.provider,
        )
        .unwrap();
    assert!(upgraded.get("migrated").unwrap().is_some());
    drop(upgraded);
    std::fs::remove_file(path).unwrap();
}

#[test]
fn semantic_episodic_metadata_provenance_and_restart() {
    let mut f = Fixture::new();
    f.put("semantic", "prefers Rust");
    let mut record = Fixture::record("episode", "fixed Cargo build failure");
    record.kind = MemoryKind::Episodic;
    record.session_id = Some("session-42".into());
    record.source_ref = Some("hermes://profile-A/sessions/session-42/messages/uid-7".into());
    record.importance = Some(0.9);
    record.confidence = Some(0.8);
    record.entry = record.entry.with_metadata("project", "Hermes");
    f.db().ingest(&record, &f.provider).unwrap();
    let expected = serde_json::to_value(&record).unwrap();
    f.store.take();
    f.store = Some(
        SqliteStore::open(&f.path, f.provider.model_id(), 64, SqliteConfig::default()).unwrap(),
    );
    assert_eq!(
        serde_json::to_value(f.db().get("episode").unwrap().unwrap()).unwrap(),
        expected
    );
    assert_eq!(f.db().source("episode").unwrap(), record.source_ref);
    assert_eq!(
        f.db().get("semantic").unwrap().unwrap().kind,
        MemoryKind::Semantic
    );
    assert_eq!(
        f.find("fixed Cargo build failure", RetrievalMode::Vector)[0]
            .record
            .entry
            .id,
        "episode"
    );
}

#[test]
fn dimensions_models_invalid_records_and_atomic_failures() {
    let f = Fixture::new();
    let record = Fixture::record("one", "Rust compiler");
    let wrong_dim = Embedding::from_raw(f.provider.model_id(), vec![1.0; 3], 3).unwrap();
    let wrong_model = Embedding::from_raw("other", vec![1.0; 64], 64).unwrap();
    assert!(f.db().store(&record, &wrong_dim).is_err());
    assert!(f.db().store(&record, &wrong_model).is_err());
    assert!(f.db().get("one").unwrap().is_none());
    assert!(f
        .db()
        .retrieve(
            "Rust",
            &RetrievalScope::default(),
            5,
            RetrievalMode::Vector,
            &SyntheticEmbedding::new(32).unwrap()
        )
        .is_err());
    let mut bad = record.clone();
    bad.confidence = Some(2.0);
    assert!(f.db().ingest(&bad, &f.provider).is_err());
    bad = record;
    bad.entry.set_content("  ");
    assert!(f.db().ingest(&bad, &f.provider).is_err());
}

#[test]
fn vector_keyword_hybrid_ranking_and_limits() {
    let f = Fixture::new();
    f.put("cargo", "Rust Cargo compiler");
    f.put("garden", "tomatoes sunlight water");
    for mode in [
        RetrievalMode::Vector,
        RetrievalMode::Keyword,
        RetrievalMode::Hybrid,
    ] {
        let hits = f.find("Rust Cargo compiler", mode);
        assert_eq!(hits[0].record.entry.id, "cargo");
        assert!(hits[0].score > 0.0);
        assert!(
            f.db()
                .retrieve("Cargo", &RetrievalScope::default(), 1, mode, &f.provider)
                .unwrap()
                .len()
                <= 1
        );
    }
    assert!(f.find("Cargo", RetrievalMode::Vector)[0].distance.is_some());
    assert!(f.find("\" OR * (", RetrievalMode::Keyword).is_empty());
    assert!(f
        .db()
        .retrieve(
            "Cargo",
            &RetrievalScope::default(),
            0,
            RetrievalMode::Hybrid,
            &f.provider
        )
        .unwrap()
        .is_empty());
}

#[test]
fn updates_delete_and_vector_fts_consistency() {
    let f = Fixture::new();
    assert!(f
        .db()
        .update(&Fixture::record("missing", "missing memory"), &f.provider)
        .is_err());
    f.put("one", "Cargo original");
    let mut changed = f.db().get("one").unwrap().unwrap();
    let created = changed.entry.created_at;
    changed.entry.set_content("garden flowers");
    changed.confidence = Some(0.7);
    let wrong_dim = Embedding::from_raw(f.provider.model_id(), vec![1.0; 3], 3).unwrap();
    assert!(f.db().store(&changed, &wrong_dim).is_err());
    assert_eq!(f.find("Cargo", RetrievalMode::Keyword).len(), 1);
    f.db().update(&changed, &f.provider).unwrap();
    assert!(f.find("Cargo", RetrievalMode::Keyword).is_empty());
    assert_eq!(
        f.find("garden flowers", RetrievalMode::Vector)[0]
            .distance
            .unwrap()
            .abs()
            < 1e-5,
        true
    );
    assert_eq!(
        f.db().get("one").unwrap().unwrap().entry.created_at,
        created
    );
    assert!(f.db().delete("one").unwrap());
    assert!(!f.db().delete("one").unwrap());
    assert_eq!(f.db().stats().unwrap().memory_count, 0);
    assert!(f.find("garden", RetrievalMode::Hybrid).is_empty());
    let db = Connection::open(&f.path).unwrap();
    assert_eq!(
        db.query_row("SELECT count(*) FROM memory_vectors", [], |r| r
            .get::<_, usize>(0))
            .unwrap(),
        0
    );
}

#[test]
fn conservative_deduplication_respects_ownership_and_kind() {
    let f = Fixture::new();
    f.put("one", "Prefer Rust");
    let dup = Fixture::record("two", "  prefer   RUST ");
    assert_eq!(f.db().ingest(&dup, &f.provider).unwrap(), "one");
    assert!(f.db().get("two").unwrap().is_none());
    let mut different = dup;
    different.kind = MemoryKind::Episodic;
    assert_eq!(f.db().ingest(&different, &f.provider).unwrap(), "two");
    different.entry.set_content("Prefer Rust");
    different.kind = MemoryKind::Semantic;
    assert!(f.db().ingest(&different, &f.provider).is_err());
    assert_eq!(
        f.db().get("two").unwrap().unwrap().kind,
        MemoryKind::Episodic
    );
}

#[test]
fn scopes_filter_before_top_k_and_expiry_filters_every_mode() {
    let f = Fixture::new();
    f.put("public-default", "Cargo compiler");
    let mut private = Fixture::record("tenant", "Cargo compiler");
    private.namespace = "team-A".into();
    private.agent_id = Some("agent-1".into());
    private.access_scope = "project".into();
    private.session_id = Some("s1".into());
    private.kind = MemoryKind::Episodic;
    f.db().ingest(&private, &f.provider).unwrap();
    let scope = RetrievalScope {
        namespace: "team-A".into(),
        access_scope: "project".into(),
        agent_id: Some("agent-1".into()),
        session_id: Some("s1".into()),
        kind: Some(MemoryKind::Episodic),
        tag: Some("rust".into()),
    };
    for mode in [
        RetrievalMode::Vector,
        RetrievalMode::Keyword,
        RetrievalMode::Hybrid,
    ] {
        let hits = f
            .db()
            .retrieve("Cargo compiler", &scope, 1, mode, &f.provider)
            .unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].record.entry.id, "tenant");
        assert_eq!(
            f.find("Cargo compiler", mode)[0].record.entry.id,
            "public-default"
        );
        let mut other = scope.clone();
        other.agent_id = None;
        assert!(f
            .db()
            .retrieve("Cargo", &other, 5, mode, &f.provider)
            .unwrap()
            .is_empty());
        other = scope.clone();
        other.tag = Some("absent".into());
        assert!(f
            .db()
            .retrieve("Cargo", &other, 5, mode, &f.provider)
            .unwrap()
            .is_empty());
    }
    private.expires_at = Some(chrono::Utc::now() - chrono::Duration::seconds(5));
    f.db().ingest(&private, &f.provider).unwrap();
    for mode in [
        RetrievalMode::Vector,
        RetrievalMode::Keyword,
        RetrievalMode::Hybrid,
    ] {
        assert!(f
            .db()
            .retrieve("Cargo", &scope, 5, mode, &f.provider)
            .unwrap()
            .is_empty());
    }
}

#[test]
fn concurrent_connections_and_shared_engine_preserve_commits() {
    let f = Fixture::new();
    let shared = Arc::new(
        SqliteStore::open(&f.path, f.provider.model_id(), 64, SqliteConfig::default()).unwrap(),
    );
    let workers = (0..4)
        .map(|worker| {
            let store = shared.clone();
            let path = f.path.clone();
            std::thread::spawn(move || {
                let provider = SyntheticEmbedding::new(64).unwrap();
                let other =
                    SqliteStore::open(path, provider.model_id(), 64, SqliteConfig::default())
                        .unwrap();
                for i in 0..10 {
                    let id = format!("{worker}-{i}");
                    store
                        .ingest(
                            &Fixture::record(&id, &format!("Cargo worker {id}")),
                            &provider,
                        )
                        .unwrap();
                    assert!(other.get(&id).unwrap().is_some());
                    assert!(!other
                        .retrieve(
                            "Cargo",
                            &RetrievalScope::default(),
                            2,
                            RetrievalMode::Hybrid,
                            &provider
                        )
                        .unwrap()
                        .is_empty());
                }
            })
        })
        .collect::<Vec<_>>();
    for worker in workers {
        worker.join().unwrap();
    }
    drop(shared);
    let db = Connection::open(&f.path).unwrap();
    assert_eq!(
        db.query_row("SELECT count(*) FROM memories", [], |r| r
            .get::<_, usize>(0))
            .unwrap(),
        40
    );
    assert_eq!(
        db.query_row("SELECT count(*) FROM memory_vectors", [], |r| r
            .get::<_, usize>(0))
            .unwrap(),
        40
    );
}

#[test]
fn hermes_transcripts_and_legacy_jsonl_are_never_opened_or_modified() {
    let f = Fixture::new();
    let original = f.path.with_extension("transcript.jsonl");
    let bytes = b"{\"role\":\"user\",\"content\":\"preserve this original\"}\n";
    std::fs::write(&original, bytes).unwrap();
    let mut record = Fixture::record("reference", "User requests transcript preservation");
    record.source_ref = Some(original.to_string_lossy().into());
    f.db().ingest(&record, &f.provider).unwrap();
    f.find("preservation", RetrievalMode::Hybrid);
    f.db().delete("reference").unwrap();
    assert_eq!(std::fs::read(&original).unwrap(), bytes);
    // Passing a legacy JSONL file as a database fails without migrating/overwriting it.
    assert!(SqliteStore::open(
        &original,
        f.provider.model_id(),
        64,
        SqliteConfig::default()
    )
    .is_err());
    assert_eq!(std::fs::read(&original).unwrap(), bytes);
    std::fs::remove_file(original).unwrap();
    // A misconfigured database path cannot add memory tables or change Hermes journaling.
    let transcript_db = f.path.with_extension("state.db");
    let original_db = Connection::open(&transcript_db).unwrap();
    original_db.execute_batch("CREATE TABLE sessions(id TEXT PRIMARY KEY); INSERT INTO sessions VALUES('original-session');").unwrap();
    drop(original_db);
    let original_bytes = std::fs::read(&transcript_db).unwrap();
    assert!(SqliteStore::open(
        &transcript_db,
        f.provider.model_id(),
        64,
        SqliteConfig::default()
    )
    .is_err());
    assert_eq!(std::fs::read(&transcript_db).unwrap(), original_bytes);
    std::fs::remove_file(transcript_db).unwrap();
}

#[cfg(feature = "local-fastembed")]
#[test]
#[ignore = "requires the provisioned local MiniLM model and ONNX Runtime"]
fn offline_model_ingestion_and_sqlite_retrieval() {
    let model_path = std::env::var_os("MEMD_MODEL_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../models/all-MiniLM-L6-v2")
        });
    let mut config = LocalModelConfig::all_minilm_l6_v2();
    config.model_id = "all-MiniLM-L6-v2@model-v1;mean;no-prefix;l2-v1".into();
    let provider = FastEmbedBackend::load_from_dir(&model_path, config).unwrap();
    let fixture = Fixture::new();
    let path = fixture.path.with_extension("onnx.db");
    let store = SqliteStore::open(
        &path,
        provider.model_id(),
        provider.dim(),
        SqliteConfig::default(),
    )
    .unwrap();
    store
        .ingest(
            &Fixture::record("rust", "The Rust compiler builds this project using Cargo"),
            &provider,
        )
        .unwrap();
    store
        .ingest(
            &Fixture::record("garden", "Tomatoes need sunlight and water in the garden"),
            &provider,
        )
        .unwrap();
    drop(store);
    let store = SqliteStore::open(
        &path,
        provider.model_id(),
        provider.dim(),
        SqliteConfig::default(),
    )
    .unwrap();
    let hits = store
        .retrieve(
            "Building the Rust project with Cargo",
            &RetrievalScope::default(),
            1,
            RetrievalMode::Vector,
            &provider,
        )
        .unwrap();
    assert_eq!(hits[0].record.entry.id, "rust");
    assert!(hits[0].distance.is_some());
    drop(store);
    std::fs::remove_file(path).unwrap();
}
