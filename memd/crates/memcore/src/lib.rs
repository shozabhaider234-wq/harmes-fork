//! Core memory representation, indexing, and retrieval engine.

pub mod types;

pub use types::{
    MemoryCategory, MemoryEntry, ParseCategoryError, normalize_memory_search_text,
    normalize_search_text,
};
