//! Core memory representation, indexing, and retrieval engine.

pub mod embedding;
pub mod types;

#[cfg(feature = "local-fastembed")]
pub use embedding::FastEmbedBackend;
pub use embedding::{
    cosine_similarity, dot_product, find_similar_memories, EmbedRole, EmbeddedMemory, Embedding,
    EmbeddingBackend, EmbeddingError, LocalModelConfig, PoolingStrategy, SimilarMemory,
};
pub use types::{
    MemoryCategory, MemoryEntry, ParseCategoryError, normalize_memory_search_text,
    normalize_search_text,
};
