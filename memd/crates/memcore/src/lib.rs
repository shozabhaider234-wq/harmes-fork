//! Core memory representation, indexing, and retrieval engine.

pub mod embedding;
pub mod sqlite;
pub mod storage;
pub mod types;
pub use sqlite::{
    MemoryKind, MemoryRecord, RankedMemory, RetrievalMode, RetrievalScope, SqliteConfig,
    SqliteStore, SyntheticEmbedding,
};

#[cfg(feature = "local-fastembed")]
pub use embedding::FastEmbedBackend;
pub use embedding::{
    cosine_similarity, dot_product, find_similar_memories, EmbedRole, EmbeddedMemory, Embedding,
    EmbeddingBackend, EmbeddingError, LocalModelConfig, PoolingStrategy, SimilarMemory,
};
pub use storage::{
    InMemoryStore, JsonlStore, JsonlStoreConfig, MemoryStore, StorageError, SyncMode,
    STORAGE_FORMAT_VERSION,
};
pub use types::{
    normalize_memory_search_text, normalize_search_text, MemoryCategory, MemoryEntry,
    ParseCategoryError,
};
