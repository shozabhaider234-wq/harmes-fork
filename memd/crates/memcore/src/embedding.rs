//! Embedding generation, vector-space identity, and normalized similarity primitives.

use std::fmt;
#[cfg(feature = "local-fastembed")]
use std::fs;
#[cfg(feature = "local-fastembed")]
use std::path::Path;
#[cfg(feature = "local-fastembed")]
use std::sync::Mutex;

use serde::{Deserialize, Deserializer, Serialize};

use crate::types::{MemoryCategory, MemoryEntry};

#[cfg(feature = "local-fastembed")]
use fastembed::{
    InitOptionsUserDefined, Pooling, TextEmbedding, TokenizerFiles, UserDefinedEmbeddingModel,
};

const UNIT_NORM_TOLERANCE: f32 = 1e-3;

/// Role played by a text during retrieval so asymmetric models can apply
/// distinct query vs. passage formatting without a raw unformatted bypass.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EmbedRole {
    Query,
    Passage,
}

/// Errors produced when validating configs, loading local models, generating
/// embeddings, or comparing incompatible vector spaces.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmbeddingError {
    InvalidConfig(String),
    ModelLoad(String),
    Inference(String),
    EmptyModelId,
    EmptyVector,
    NonFiniteValue,
    ZeroNorm,
    DimensionMismatch { expected: usize, actual: usize },
    ModelMismatch { expected: String, actual: String },
}

impl fmt::Display for EmbeddingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidConfig(msg) => write!(f, "invalid embedding config: {msg}"),
            Self::ModelLoad(msg) => write!(f, "failed to load local embedding model: {msg}"),
            Self::Inference(msg) => write!(f, "embedding inference failed: {msg}"),
            Self::EmptyModelId => f.write_str("embedding model_id cannot be empty"),
            Self::EmptyVector => f.write_str("embedding vector cannot be empty"),
            Self::NonFiniteValue => f.write_str("embedding vector contains NaN or infinite values"),
            Self::ZeroNorm => f.write_str("embedding vector cannot have zero L2 norm"),
            Self::DimensionMismatch { expected, actual } => {
                write!(f, "embedding dimension mismatch: expected {expected}, got {actual}")
            }
            Self::ModelMismatch { expected, actual } => {
                write!(f, "embedding model mismatch: expected '{expected}', got '{actual}'")
            }
        }
    }
}

impl std::error::Error for EmbeddingError {}

/// Validated, L2-normalized embedding vector bound to its generating `model_id`.
///
/// Normalization happens once at generation time so downstream similarity
/// scoring executes pure dot products with zero runtime norm checks.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Embedding {
    model_id: String,
    vector: Vec<f32>,
}

impl Embedding {
    /// Validate a freshly generated vector and L2-normalize it once.
    pub fn from_raw(
        model_id: impl Into<String>,
        mut raw: Vec<f32>,
        expected_dim: usize,
    ) -> Result<Self, EmbeddingError> {
        let model_id = validate_model_id(model_id.into())?;
        if expected_dim == 0 {
            return Err(EmbeddingError::InvalidConfig(
                "expected_dim must be greater than zero".into(),
            ));
        }
        if raw.is_empty() {
            return Err(EmbeddingError::EmptyVector);
        }
        if raw.len() != expected_dim {
            return Err(EmbeddingError::DimensionMismatch {
                expected: expected_dim,
                actual: raw.len(),
            });
        }

        let norm_sq = finite_norm_sq(&raw)?;
        if norm_sq == 0.0 || !norm_sq.is_finite() {
            return Err(EmbeddingError::ZeroNorm);
        }

        let inv_norm = 1.0 / norm_sq.sqrt();
        for value in &mut raw {
            *value *= inv_norm;
        }

        Ok(Self {
            model_id,
            vector: raw,
        })
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    pub fn dim(&self) -> usize {
        self.vector.len()
    }

    pub fn as_slice(&self) -> &[f32] {
        &self.vector
    }

    pub fn into_vec(self) -> Vec<f32> {
        self.vector
    }

    pub fn matches_model(&self, model_id: &str) -> bool {
        self.model_id == model_id
    }

    /// Compute cosine similarity via pure dot product, relying on the
    /// generation-time unit-norm invariant and gating on matching `model_id`.
    pub fn similarity(&self, other: &Self) -> Result<f32, EmbeddingError> {
        if self.model_id != other.model_id {
            return Err(EmbeddingError::ModelMismatch {
                expected: self.model_id.clone(),
                actual: other.model_id.clone(),
            });
        }
        if self.vector.len() != other.vector.len() {
            return Err(EmbeddingError::DimensionMismatch {
                expected: self.vector.len(),
                actual: other.vector.len(),
            });
        }
        Ok(dot_product(&self.vector, &other.vector))
    }
}

#[derive(Deserialize)]
struct RawPersistedEmbedding {
    model_id: String,
    vector: Vec<f32>,
}

impl<'de> Deserialize<'de> for Embedding {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw = RawPersistedEmbedding::deserialize(deserializer)?;
        let model_id = validate_model_id(raw.model_id).map_err(serde::de::Error::custom)?;
        if raw.vector.is_empty() {
            return Err(serde::de::Error::custom(EmbeddingError::EmptyVector));
        }
        let norm_sq = finite_norm_sq(&raw.vector).map_err(serde::de::Error::custom)?;
        if norm_sq == 0.0 || !norm_sq.is_finite() {
            return Err(serde::de::Error::custom(EmbeddingError::ZeroNorm));
        }
        // Fail loudly on corrupt/unnormalized disk vectors instead of silently re-normalizing.
        if (norm_sq - 1.0).abs() > UNIT_NORM_TOLERANCE {
            return Err(serde::de::Error::custom(format!(
                "persisted embedding vector is not L2-normalized (norm^2 = {norm_sq:.6})"
            )));
        }
        Ok(Self {
            model_id,
            vector: raw.vector,
        })
    }
}

fn validate_model_id(model_id: String) -> Result<String, EmbeddingError> {
    let trimmed = model_id.trim();
    if trimmed.is_empty() {
        return Err(EmbeddingError::EmptyModelId);
    }
    Ok(trimmed.to_string())
}

fn finite_norm_sq(values: &[f32]) -> Result<f32, EmbeddingError> {
    let mut sum = 0.0f32;
    for &v in values {
        if !v.is_finite() {
            return Err(EmbeddingError::NonFiniteValue);
        }
        sum += v * v;
    }
    Ok(sum)
}

/// Pure dot product between two vectors.
///
/// Assumes callers supply equal-length, L2-normalized slices from the same
/// embedding model; performs no norm checks or divisions.
pub fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Cosine similarity between two model-gated, generation-time-normalized vectors.
pub fn cosine_similarity(a: &Embedding, b: &Embedding) -> Result<f32, EmbeddingError> {
    a.similarity(b)
}

/// Runtime-agnostic embedding backend contract.
pub trait EmbeddingBackend: Send + Sync {
    fn model_id(&self) -> &str;
    fn dim(&self) -> usize;

    /// Embed one input under an explicit retrieval role.
    fn embed(&self, role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError>;

    fn embed_query(&self, text: &str) -> Result<Embedding, EmbeddingError> {
        self.embed(EmbedRole::Query, text)
    }

    fn embed_passage(&self, text: &str) -> Result<Embedding, EmbeddingError> {
        self.embed(EmbedRole::Passage, text)
    }

    /// Embed multiple passages via sequential single-item calls.
    fn embed_passages(&self, texts: &[&str]) -> Result<Vec<Embedding>, EmbeddingError> {
        texts.iter().map(|text| self.embed_passage(text)).collect()
    }
}

/// Stage 2 progressive wrapper pairing a pure `MemoryEntry` with a required,
/// non-optional `Embedding` so unembedded or stale memories fail at compile time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EmbeddedMemory {
    entry: MemoryEntry,
    embedding: Embedding,
}

impl EmbeddedMemory {
    pub fn new(
        content: impl Into<String>,
        category: MemoryCategory,
        tags: Vec<String>,
        backend: &dyn EmbeddingBackend,
    ) -> Result<Self, EmbeddingError> {
        let entry = MemoryEntry::new(content, category, tags);
        Self::from_entry(entry, backend)
    }

    pub fn from_entry(
        entry: MemoryEntry,
        backend: &dyn EmbeddingBackend,
    ) -> Result<Self, EmbeddingError> {
        let embedding = backend.embed_passage(entry.content())?;
        Ok(Self { entry, embedding })
    }

    pub fn entry(&self) -> &MemoryEntry {
        &self.entry
    }

    pub fn embedding(&self) -> &Embedding {
        &self.embedding
    }

    pub fn into_entry(self) -> MemoryEntry {
        self.entry
    }

    /// Update memory content and refresh its embedding atomically; on backend
    /// error, both the previous content and previous embedding remain intact.
    pub fn set_content(
        &mut self,
        content: impl Into<String>,
        backend: &dyn EmbeddingBackend,
    ) -> Result<(), EmbeddingError> {
        let content = content.into();
        let next_embedding = backend.embed_passage(&content)?;
        self.entry.set_content(content);
        self.embedding = next_embedding;
        Ok(())
    }

    pub fn set_tags(&mut self, tags: Vec<String>) {
        self.entry.set_tags(tags);
    }

    pub fn add_tag(&mut self, tag: impl Into<String>) -> bool {
        self.entry.add_tag(tag)
    }

    pub fn remove_tag(&mut self, tag: &str) -> bool {
        self.entry.remove_tag(tag)
    }

    /// Re-compute the embedding against `backend` (for example after a model change).
    pub fn reembed(&mut self, backend: &dyn EmbeddingBackend) -> Result<(), EmbeddingError> {
        self.embedding = backend.embed_passage(self.entry.content())?;
        Ok(())
    }
}

/// Ranked memory hit returned by similarity search.
#[derive(Debug, Clone, PartialEq)]
pub struct SimilarMemory<'a> {
    pub memory: &'a EmbeddedMemory,
    pub score: f32,
}

/// Find the top `limit` memories matching `query` with score `>= min_score`.
///
/// Only compares candidates whose `model_id` and dimension match `query` so
/// vectors from different embedding spaces are never mixed.
pub fn find_similar_memories<'a>(
    query: &Embedding,
    candidates: &'a [EmbeddedMemory],
    min_score: f32,
    limit: usize,
) -> Vec<SimilarMemory<'a>> {
    if limit == 0 {
        return Vec::new();
    }

    let query_vec = query.as_slice();
    let mut matches: Vec<SimilarMemory<'a>> = candidates
        .iter()
        .filter(|candidate| {
            let emb = candidate.embedding();
            emb.matches_model(query.model_id()) && emb.dim() == query.dim()
        })
        .filter_map(|candidate| {
            let score = dot_product(query_vec, candidate.embedding().as_slice());
            (score >= min_score).then_some(SimilarMemory {
                memory: candidate,
                score,
            })
        })
        .collect();

    matches.sort_by(|a, b| b.score.total_cmp(&a.score));
    matches.truncate(limit);
    matches
}

/// Token pooling strategy for local ONNX embedding models.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PoolingStrategy {
    Mean,
    Cls,
}

/// Declarative configuration for a locally provisioned embedding model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalModelConfig {
    pub model_id: String,
    pub dim: usize,
    pub pooling: PoolingStrategy,
    pub query_prefix: Option<String>,
    pub passage_prefix: Option<String>,
}

impl LocalModelConfig {
    pub fn new(model_id: impl Into<String>, dim: usize, pooling: PoolingStrategy) -> Self {
        Self {
            model_id: model_id.into(),
            dim,
            pooling,
            query_prefix: None,
            passage_prefix: None,
        }
    }

    pub fn with_prefixes(
        mut self,
        query_prefix: Option<String>,
        passage_prefix: Option<String>,
    ) -> Self {
        self.query_prefix = query_prefix;
        self.passage_prefix = passage_prefix;
        self
    }

    /// Default configuration for `sentence-transformers/all-MiniLM-L6-v2` (384-d, mean pooling).
    pub fn all_minilm_l6_v2() -> Self {
        Self::new("all-MiniLM-L6-v2", 384, PoolingStrategy::Mean)
    }

    pub fn validate(&self) -> Result<(), EmbeddingError> {
        if self.model_id.trim().is_empty() {
            return Err(EmbeddingError::InvalidConfig(
                "model_id cannot be empty".into(),
            ));
        }
        if self.dim == 0 {
            return Err(EmbeddingError::InvalidConfig(
                "dim must be greater than zero".into(),
            ));
        }
        Ok(())
    }

    /// Apply role-specific prefix formatting when configured.
    pub fn format_input(&self, role: EmbedRole, text: &str) -> String {
        let prefix = match role {
            EmbedRole::Query => self.query_prefix.as_deref(),
            EmbedRole::Passage => self.passage_prefix.as_deref(),
        };
        match prefix {
            Some(p) if !p.is_empty() => format!("{p}{text}"),
            _ => text.to_string(),
        }
    }
}

/// Offline `fastembed` backend loading model + tokenizer artifacts from disk.
#[cfg(feature = "local-fastembed")]
pub struct FastEmbedBackend {
    config: LocalModelConfig,
    model: Mutex<TextEmbedding>,
}

#[cfg(feature = "local-fastembed")]
impl FastEmbedBackend {
    /// Load an ONNX embedding model completely offline from `model_dir`.
    ///
    /// Expects `model.onnx`, `tokenizer.json`, `config.json`,
    /// `special_tokens_map.json`, and `tokenizer_config.json` in `model_dir`.
    pub fn load_from_dir(
        model_dir: &Path,
        mut config: LocalModelConfig,
    ) -> Result<Self, EmbeddingError> {
        config.validate()?;
        config.model_id = config.model_id.trim().to_string();

        let read_artifact = |filename: &str| -> Result<Vec<u8>, EmbeddingError> {
            let path = model_dir.join(filename);
            fs::read(&path).map_err(|err| {
                EmbeddingError::ModelLoad(format!("missing or unreadable {}: {err}", path.display()))
            })
        };

        let onnx_file = read_artifact("model.onnx")?;
        let tokenizer_files = TokenizerFiles {
            tokenizer_file: read_artifact("tokenizer.json")?,
            config_file: read_artifact("config.json")?,
            special_tokens_map_file: read_artifact("special_tokens_map.json")?,
            tokenizer_config_file: read_artifact("tokenizer_config.json")?,
        };

        let pooling = match config.pooling {
            PoolingStrategy::Mean => Pooling::Mean,
            PoolingStrategy::Cls => Pooling::Cls,
        };
        let user_model = UserDefinedEmbeddingModel::new(onnx_file, tokenizer_files)
            .with_pooling(pooling);

        let model = TextEmbedding::try_new_from_user_defined(
            user_model,
            InitOptionsUserDefined::default(),
        )
        .map_err(|err| EmbeddingError::ModelLoad(err.to_string()))?;

        Ok(Self {
            config,
            model: Mutex::new(model),
        })
    }

    pub fn config(&self) -> &LocalModelConfig {
        &self.config
    }
}

#[cfg(feature = "local-fastembed")]
impl EmbeddingBackend for FastEmbedBackend {
    fn model_id(&self) -> &str {
        &self.config.model_id
    }

    fn dim(&self) -> usize {
        self.config.dim
    }

    fn embed(&self, role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError> {
        let formatted = self.config.format_input(role, text);
        let mut model = self
            .model
            .lock()
            .map_err(|_| EmbeddingError::Inference("embedder mutex poisoned".into()))?;
        let mut outputs = model
            .embed(vec![formatted], None)
            .map_err(|err| EmbeddingError::Inference(err.to_string()))?;
        if outputs.len() != 1 {
            return Err(EmbeddingError::Inference(format!(
                "expected 1 embedding vector, got {}",
                outputs.len()
            )));
        }
        let raw = outputs.pop().ok_or(EmbeddingError::EmptyVector)?;
        Embedding::from_raw(&self.config.model_id, raw, self.config.dim)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    struct KeywordAxisMockBackend {
        model_id: String,
        query_prefix: Option<String>,
        passage_prefix: Option<String>,
        fail_next: AtomicBool,
    }

    impl KeywordAxisMockBackend {
        fn new(model_id: impl Into<String>) -> Self {
            Self {
                model_id: model_id.into(),
                query_prefix: None,
                passage_prefix: None,
                fail_next: AtomicBool::new(false),
            }
        }

        fn with_prefixes(
            mut self,
            query_prefix: impl Into<String>,
            passage_prefix: impl Into<String>,
        ) -> Self {
            self.query_prefix = Some(query_prefix.into());
            self.passage_prefix = Some(passage_prefix.into());
            self
        }
    }

    impl EmbeddingBackend for KeywordAxisMockBackend {
        fn model_id(&self) -> &str {
            &self.model_id
        }

        fn dim(&self) -> usize {
            3
        }

        fn embed(&self, role: EmbedRole, text: &str) -> Result<Embedding, EmbeddingError> {
            if self.fail_next.swap(false, Ordering::SeqCst) {
                return Err(EmbeddingError::Inference("injected failure".into()));
            }
            let prefix = match role {
                EmbedRole::Query => self.query_prefix.as_deref(),
                EmbedRole::Passage => self.passage_prefix.as_deref(),
            };
            let formatted = match prefix {
                Some(p) => format!("{p}{text}"),
                None => text.to_string(),
            };
            let lower = formatted.to_ascii_lowercase();
            // Return intentionally unnormalized magnitudes to verify generation-time L2 normalization.
            let raw = vec![
                if lower.contains("rust") || lower.contains("cargo") {
                    4.0
                } else {
                    0.5
                },
                if lower.contains("editor") || lower.contains("neovim") {
                    3.0
                } else {
                    0.2
                },
                if lower.starts_with("query:") { 2.0 } else { 0.0 },
            ];
            Embedding::from_raw(&self.model_id, raw, 3)
        }
    }

    #[test]
    fn from_raw_validates_and_l2_normalizes_at_generation_time() {
        assert_eq!(
            Embedding::from_raw("   ", vec![1.0, 0.0], 2),
            Err(EmbeddingError::EmptyModelId)
        );
        assert_eq!(
            Embedding::from_raw("m1", vec![1.0], 0),
            Err(EmbeddingError::InvalidConfig(
                "expected_dim must be greater than zero".into()
            ))
        );
        assert_eq!(
            Embedding::from_raw("m1", vec![], 2),
            Err(EmbeddingError::EmptyVector)
        );
        assert_eq!(
            Embedding::from_raw("m1", vec![1.0, 2.0], 3),
            Err(EmbeddingError::DimensionMismatch {
                expected: 3,
                actual: 2
            })
        );
        assert_eq!(
            Embedding::from_raw("m1", vec![1.0, f32::NAN], 2),
            Err(EmbeddingError::NonFiniteValue)
        );
        assert_eq!(
            Embedding::from_raw("m1", vec![0.0, 0.0], 2),
            Err(EmbeddingError::ZeroNorm)
        );

        let emb = Embedding::from_raw("m1", vec![3.0, 4.0], 2).unwrap();
        assert_eq!(emb.model_id(), "m1");
        assert_eq!(emb.dim(), 2);
        let norm_sq: f32 = emb.as_slice().iter().map(|x| x * x).sum();
        assert!((norm_sq - 1.0).abs() < 1e-6);
        assert!((emb.as_slice()[0] - 0.6).abs() < 1e-6);
        assert!((emb.as_slice()[1] - 0.8).abs() < 1e-6);
    }

    #[test]
    fn similarity_uses_pure_dot_product_and_gates_on_model_id() {
        let a = Embedding::from_raw("model-a", vec![3.0, 4.0], 2).unwrap();
        let b = Embedding::from_raw("model-a", vec![3.0, 4.0], 2).unwrap();
        let ortho = Embedding::from_raw("model-a", vec![-4.0, 3.0], 2).unwrap();
        let other_model = Embedding::from_raw("model-b", vec![3.0, 4.0], 2).unwrap();

        let self_sim = cosine_similarity(&a, &b).unwrap();
        assert!((self_sim - 1.0).abs() < 1e-6);
        assert!(cosine_similarity(&a, &ortho).unwrap().abs() < 1e-6);
        assert_eq!(
            cosine_similarity(&a, &other_model),
            Err(EmbeddingError::ModelMismatch {
                expected: "model-a".into(),
                actual: "model-b".into()
            })
        );
    }

    #[test]
    fn serde_round_trips_and_rejects_corrupt_or_unnormalized_embeddings() {
        let emb = Embedding::from_raw("model-a", vec![3.0, 4.0], 2).unwrap();
        let json = serde_json::to_string(&emb).unwrap();
        let decoded: Embedding = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, emb);

        let unnormalized = serde_json::json!({
            "model_id": "model-a",
            "vector": [3.0, 4.0]
        });
        assert!(serde_json::from_value::<Embedding>(unnormalized).is_err());

        let empty_model = serde_json::json!({
            "model_id": "",
            "vector": [1.0, 0.0]
        });
        assert!(serde_json::from_value::<Embedding>(empty_model).is_err());
    }

    #[test]
    fn role_prefixes_differentiate_query_and_passage_embeddings() {
        let backend = KeywordAxisMockBackend::new("e5-mock").with_prefixes("query: ", "passage: ");
        let q = backend.embed_query("rust workspace").unwrap();
        let p = backend.embed_passage("rust workspace").unwrap();
        assert_ne!(q.as_slice(), p.as_slice());

        let batch = backend.embed_passages(&["rust", "editor"]).unwrap();
        assert_eq!(batch.len(), 2);
    }

    #[test]
    fn embedded_memory_protects_invariants_and_updates_atomically() {
        let backend = KeywordAxisMockBackend::new("mock-v1");
        let mut mem = EmbeddedMemory::new(
            "User prefers Neovim editor",
            MemoryCategory::Preference,
            vec!["tools".into()],
            &backend,
        )
        .unwrap();

        let initial_vec = mem.embedding().as_slice().to_vec();
        assert_eq!(mem.entry().content(), "User prefers Neovim editor");

        // Tag mutations do not invalidate or change content embeddings.
        assert!(mem.add_tag("ide"));
        assert_eq!(mem.embedding().as_slice(), initial_vec.as_slice());

        // Injected failure leaves content and embedding completely unchanged.
        backend.fail_next.store(true, Ordering::SeqCst);
        assert!(mem.set_content("Use Rust cargo workspace", &backend).is_err());
        assert_eq!(mem.entry().content(), "User prefers Neovim editor");
        assert_eq!(mem.embedding().as_slice(), initial_vec.as_slice());

        // Successful content mutation refreshes embedding atomically.
        mem.set_content("Use Rust cargo workspace", &backend).unwrap();
        assert_eq!(mem.entry().content(), "Use Rust cargo workspace");
        assert_ne!(mem.embedding().as_slice(), initial_vec.as_slice());

        // Round-trip serialization preserves entry + embedding together.
        let json = serde_json::to_string(&mem).unwrap();
        let decoded: EmbeddedMemory = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, mem);

        // Re-embedding under a new model updates the vector space tag.
        let backend_v2 = KeywordAxisMockBackend::new("mock-v2");
        mem.reembed(&backend_v2).unwrap();
        assert_eq!(mem.embedding().model_id(), "mock-v2");
    }

    #[test]
    fn find_similar_memories_ranks_and_gates_by_model_and_threshold() {
        let backend_v1: KeywordAxisMockBackend = KeywordAxisMockBackend::new("mock-v1");
        let backend_v2 = KeywordAxisMockBackend::new("mock-v2");

        let m_rust = EmbeddedMemory::new(
            "Run cargo check in Rust workspace",
            MemoryCategory::Fact,
            vec![],
            &backend_v1,
        )
        .unwrap();
        let m_editor = EmbeddedMemory::new(
            "User prefers Neovim editor",
            MemoryCategory::Preference,
            vec![],
            &backend_v1,
        )
        .unwrap();
        let m_other_model = EmbeddedMemory::new(
            "Run cargo test in Rust",
            MemoryCategory::Fact,
            vec![],
            &backend_v2,
        )
        .unwrap();

        let query = backend_v1.embed_query("cargo rust").unwrap();
        let candidates = vec![m_editor.clone(), m_other_model, m_rust.clone()];

        let hits = find_similar_memories(&query, &candidates, 0.5, 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].memory.entry().id, m_rust.entry().id);
        assert!((hits[0].score - 1.0).abs() < 1e-6);

        let all_hits = find_similar_memories(&query, &candidates, 0.0, 2);
        assert_eq!(all_hits.len(), 2);
        assert_eq!(all_hits[0].memory.entry().id, m_rust.entry().id);
        assert_eq!(all_hits[1].memory.entry().id, m_editor.entry().id);
    }

    #[test]
    fn local_model_config_validates_and_formats_prefixes() {
        let cfg = LocalModelConfig::all_minilm_l6_v2();
        assert!(cfg.validate().is_ok());
        assert_eq!(cfg.format_input(EmbedRole::Query, "hello"), "hello");
        assert_eq!(cfg.format_input(EmbedRole::Passage, "hello"), "hello");

        let e5 = LocalModelConfig::new("e5-small-v2", 384, PoolingStrategy::Mean)
            .with_prefixes(Some("query: ".into()), Some("passage: ".into()));
        assert_eq!(e5.format_input(EmbedRole::Query, "hello"), "query: hello");
        assert_eq!(
            e5.format_input(EmbedRole::Passage, "hello"),
            "passage: hello"
        );

        assert!(LocalModelConfig::new("", 384, PoolingStrategy::Mean)
            .validate()
            .is_err());
        assert!(LocalModelConfig::new("m1", 0, PoolingStrategy::Mean)
            .validate()
            .is_err());
    }

    #[cfg(feature = "local-fastembed")]
    #[test]
    fn fastembed_load_from_dir_fails_cleanly_when_offline_files_missing() {
        let missing = Path::new("/tmp/memcore_nonexistent_model_dir_12345");
        let err = FastEmbedBackend::load_from_dir(missing, LocalModelConfig::all_minilm_l6_v2())
            .err()
            .expect("missing model directory should fail offline load");
        assert!(matches!(err, EmbeddingError::ModelLoad(_)));
    }

    #[cfg(feature = "local-fastembed")]
    fn resolve_test_minilm_dir() -> std::path::PathBuf {
        if let Some(dir) = std::env::var_os("MEMD_MODEL_DIR").filter(|dir| !dir.is_empty()) {
            return std::path::PathBuf::from(dir);
        }
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../models/all-MiniLM-L6-v2")
    }

    #[cfg(feature = "local-fastembed")]
    #[test]
    #[ignore = "requires local MiniLM model files (run ./memd/scripts/fetch_models.sh or set MEMD_MODEL_DIR)"]
    fn fastembed_offline_minilm_ranks_semantically_related_memories() {
        let model_dir = resolve_test_minilm_dir();
        let backend =
            FastEmbedBackend::load_from_dir(&model_dir, LocalModelConfig::all_minilm_l6_v2())
                .unwrap_or_else(|err| {
                    panic!(
                        "failed to load MiniLM from {}: {err}. Run ./memd/scripts/fetch_models.sh (or set MEMD_MODEL_DIR).",
                        model_dir.display()
                    )
                });

        let m_cpu = EmbeddedMemory::new(
            "Efficient cache locality and avoiding false sharing between CPU threads maximizes throughput.",
            MemoryCategory::Fact,
            vec![],
            &backend,
        )
        .unwrap();
        let m_bread = EmbeddedMemory::new(
            "A recipe for baking sourdough bread at home with simple ingredients.",
            MemoryCategory::Fact,
            vec![],
            &backend,
        )
        .unwrap();

        let norm_sq: f32 = m_cpu.embedding().as_slice().iter().map(|x| x * x).sum();
        assert!((norm_sq - 1.0).abs() < 1e-4);

        let query = backend
            .embed_query("How do I optimize CPU performance for low-level systems programming?")
            .unwrap();
        let candidates = vec![m_bread, m_cpu.clone()];
        let hits = find_similar_memories(&query, &candidates, -1.0, 2);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].memory.entry().id, m_cpu.entry().id);
        assert!(hits[0].score > hits[1].score);
    }
}
