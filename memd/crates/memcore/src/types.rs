//! Shared core memory entry types and text-normalization primitives.

use std::collections::HashMap;
use std::fmt;
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Deserializer, Serialize, Serializer};

fn new_memory_id() -> String {
    let ts = Utc::now().timestamp_millis();
    let rand: u64 = rand::random();
    format!("mem_{ts}_{rand}")
}

/// Error returned when parsing an empty memory category string.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseCategoryError;

impl fmt::Display for ParseCategoryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("memory category cannot be empty")
    }
}

impl std::error::Error for ParseCategoryError {}

/// Classification of an extracted memory entry.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum MemoryCategory {
    Fact,
    Preference,
    Entity,
    Correction,
    Custom(String),
}

impl MemoryCategory {
    pub fn as_str(&self) -> &str {
        match self {
            Self::Fact => "fact",
            Self::Preference => "preference",
            Self::Entity => "entity",
            Self::Correction => "correction",
            Self::Custom(value) => value.as_str(),
        }
    }

    /// Map model-extracted category aliases into canonical variants.
    pub fn from_extracted(raw: &str) -> Self {
        match raw.trim().to_ascii_lowercase().as_str() {
            "fact" | "facts" | "observation" | "lesson" | "learning" => Self::Fact,
            "preference" | "preferences" | "pref" => Self::Preference,
            "correction" | "corrections" | "fix" | "bug" => Self::Correction,
            "entity" | "entities" => Self::Entity,
            _ => Self::Fact,
        }
    }
}

impl fmt::Display for MemoryCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for MemoryCategory {
    type Err = ParseCategoryError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let trimmed = s.trim();
        if trimmed.is_empty() {
            return Err(ParseCategoryError);
        }
        Ok(match trimmed.to_ascii_lowercase().as_str() {
            "fact" => Self::Fact,
            "preference" => Self::Preference,
            "entity" => Self::Entity,
            "correction" => Self::Correction,
            _ => Self::Custom(trimmed.to_string()),
        })
    }
}

impl Serialize for MemoryCategory {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for MemoryCategory {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let raw = String::deserialize(deserializer)?;
        raw.parse().map_err(serde::de::Error::custom)
    }
}

/// Core extracted memory record representing a durable fact, preference,
/// entity, or correction.
///
/// `content`, `tags`, and `search_text` are encapsulated so every construction
/// and mutation preserves the invariant that `search_text` stays pre-normalized
/// and synchronized with `content` and `tags`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryEntry {
    pub id: String,
    content: String,
    pub category: MemoryCategory,
    tags: Vec<String>,
    // Always serialized and required on disk deserialization; no empty/default fallback.
    search_text: String,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub metadata: HashMap<String, String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl MemoryEntry {
    pub fn new(
        content: impl Into<String>,
        category: MemoryCategory,
        tags: Vec<String>,
    ) -> Self {
        let now = Utc::now();
        let content = content.into();
        let search_text = normalize_memory_search_text(&content, &tags);
        Self {
            id: new_memory_id(),
            content,
            category,
            tags,
            search_text,
            metadata: HashMap::new(),
            created_at: now,
            updated_at: now,
        }
    }

    pub fn with_id(mut self, id: impl Into<String>) -> Self {
        self.id = id.into();
        self
    }

    pub fn with_tags(mut self, tags: Vec<String>) -> Self {
        self.tags = tags;
        self.refresh_search_text();
        self
    }

    pub fn with_metadata(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.metadata.insert(key.into(), value.into());
        self
    }

    pub fn with_timestamps(
        mut self,
        created_at: DateTime<Utc>,
        updated_at: DateTime<Utc>,
    ) -> Self {
        self.created_at = created_at;
        self.updated_at = updated_at;
        self
    }

    pub fn content(&self) -> &str {
        &self.content
    }

    pub fn tags(&self) -> &[String] {
        &self.tags
    }

    /// Borrow the pre-normalized search text without allocation.
    pub fn searchable_text(&self) -> &str {
        &self.search_text
    }

    pub fn set_content(&mut self, content: impl Into<String>) {
        self.content = content.into();
        self.refresh_search_text();
        self.updated_at = Utc::now();
    }

    pub fn set_tags(&mut self, tags: Vec<String>) {
        self.tags = tags;
        self.refresh_search_text();
        self.updated_at = Utc::now();
    }

    pub fn add_tag(&mut self, tag: impl Into<String>) -> bool {
        let tag = tag.into();
        if self.tags.contains(&tag) {
            return false;
        }
        self.tags.push(tag);
        self.refresh_search_text();
        self.updated_at = Utc::now();
        true
    }

    pub fn remove_tag(&mut self, tag: &str) -> bool {
        let before = self.tags.len();
        self.tags.retain(|existing| existing != tag);
        if self.tags.len() == before {
            return false;
        }
        self.refresh_search_text();
        self.updated_at = Utc::now();
        true
    }

    fn refresh_search_text(&mut self) {
        self.search_text = normalize_memory_search_text(&self.content, &self.tags);
    }
}

/// Normalize text for lexical matching: trim, lowercase, and collapse
/// whitespace plus path/identifier delimiters (`-`, `_`, `/`, `\`, `.`, `:`)
/// into single spaces.
pub fn normalize_search_text(text: &str) -> String {
    let lowered = text.trim().to_lowercase();
    let mut normalized = String::with_capacity(lowered.len());
    let mut last_was_space = true;

    for ch in lowered.chars() {
        let mapped = if ch.is_whitespace() || matches!(ch, '-' | '_' | '/' | '\\' | '.' | ':') {
            ' '
        } else {
            ch
        };

        if mapped == ' ' {
            if !last_was_space {
                normalized.push(' ');
                last_was_space = true;
            }
        } else {
            normalized.push(mapped);
            last_was_space = false;
        }
    }

    if normalized.ends_with(' ') {
        normalized.pop();
    }
    normalized
}

/// Combine normalized `content` and `tags` into one space-separated search string.
pub fn normalize_memory_search_text(content: &str, tags: &[String]) -> String {
    let normalized_content = normalize_search_text(content);
    let normalized_tags: Vec<String> = tags
        .iter()
        .map(|tag| normalize_search_text(tag))
        .filter(|tag| !tag.is_empty())
        .collect();

    if normalized_tags.is_empty() {
        return normalized_content;
    }
    if normalized_content.is_empty() {
        return normalized_tags.join(" ");
    }
    format!("{} {}", normalized_content, normalized_tags.join(" "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    #[test]
    fn new_populates_id_timestamps_and_normalized_search_text() {
        let entry_a = MemoryEntry::new(
            "Use path: crates/memcore-types/lib.rs",
            MemoryCategory::Fact,
            vec!["Rust_2021".into(), "cargo:workspace".into()],
        );
        let entry_b = MemoryEntry::new(
            "Use path: crates/memcore-types/lib.rs",
            MemoryCategory::Fact,
            vec![],
        );

        assert!(entry_a.id.starts_with("mem_"));
        assert_ne!(entry_a.id, entry_b.id);
        assert_eq!(entry_a.content(), "Use path: crates/memcore-types/lib.rs");
        assert_eq!(entry_a.category, MemoryCategory::Fact);
        assert_eq!(
            entry_a.tags(),
            &["Rust_2021".to_string(), "cargo:workspace".to_string()]
        );
        assert_eq!(
            entry_a.searchable_text(),
            "use path crates memcore types lib rs rust 2021 cargo workspace"
        );
        assert!(entry_a.updated_at >= entry_a.created_at);
    }

    #[test]
    fn builders_override_id_tags_metadata_and_timestamps() {
        let t0 = Utc::now() - Duration::hours(2);
        let t1 = Utc::now() - Duration::hours(1);

        let entry = MemoryEntry::new("Prefer TypeScript strict mode", MemoryCategory::Preference, vec![])
            .with_id("mem-custom-1")
            .with_tags(vec!["coding".into(), "type-script".into()])
            .with_metadata("source", "session-42")
            .with_timestamps(t0, t1);

        assert_eq!(entry.id, "mem-custom-1");
        assert_eq!(entry.metadata.get("source").map(String::as_str), Some("session-42"));
        assert_eq!(entry.created_at, t0);
        assert_eq!(entry.updated_at, t1);
        assert_eq!(
            entry.searchable_text(),
            "prefer typescript strict mode coding type script"
        );
    }

    #[test]
    fn mutators_keep_search_text_synchronized_and_bump_updated_at() {
        let old = Utc::now() - Duration::minutes(10);
        let mut entry = MemoryEntry::new("initial fact", MemoryCategory::Fact, vec!["tag_a".into()])
            .with_timestamps(old, old);

        assert!(!entry.add_tag("tag_a"));
        assert_eq!(entry.updated_at, old);

        assert!(entry.add_tag("tag-b"));
        assert!(entry.updated_at > old);
        assert_eq!(entry.searchable_text(), "initial fact tag a tag b");

        let after_add = entry.updated_at;
        entry.set_content("Updated: src/main.rs");
        assert!(entry.updated_at >= after_add);
        assert_eq!(entry.content(), "Updated: src/main.rs");
        assert_eq!(
            entry.searchable_text(),
            "updated src main rs tag a tag b"
        );

        assert!(entry.remove_tag("tag_a"));
        assert!(!entry.remove_tag("nonexistent"));
        assert_eq!(entry.searchable_text(), "updated src main rs tag b");

        entry.set_tags(vec!["rust/core".into()]);
        assert_eq!(entry.searchable_text(), "updated src main rs rust core");
    }

    #[test]
    fn category_parses_maps_extracted_and_serializes_flat() {
        assert_eq!("Preference".parse::<MemoryCategory>(), Ok(MemoryCategory::Preference));
        assert_eq!(
            "architecture".parse::<MemoryCategory>(),
            Ok(MemoryCategory::Custom("architecture".into()))
        );
        assert_eq!("   ".parse::<MemoryCategory>(), Err(ParseCategoryError));

        assert_eq!(MemoryCategory::from_extracted("fix"), MemoryCategory::Correction);
        assert_eq!(MemoryCategory::from_extracted("prefs"), MemoryCategory::Fact);
        assert_eq!(MemoryCategory::from_extracted("pref"), MemoryCategory::Preference);
        assert_eq!(MemoryCategory::from_extracted("entities"), MemoryCategory::Entity);

        let custom_json = serde_json::to_string(&MemoryCategory::Custom("workflow".into())).unwrap();
        assert_eq!(custom_json, "\"workflow\"");
        let parsed: MemoryCategory = serde_json::from_str(&custom_json).unwrap();
        assert_eq!(parsed, MemoryCategory::Custom("workflow".into()));
    }

    #[test]
    fn serde_round_trips_full_entry_and_rejects_missing_search_text() {
        let entry = MemoryEntry::new(
            "Do not bypass memd socket permissions",
            MemoryCategory::Correction,
            vec!["security".into()],
        )
        .with_id("mem-serde-1")
        .with_metadata("session_id", "s1");

        let json = serde_json::to_string(&entry).unwrap();
        let value: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(
            value.get("search_text").and_then(serde_json::Value::as_str),
            Some("do not bypass memd socket permissions security")
        );

        let decoded: MemoryEntry = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded, entry);

        // Enforce strict invariant: internal disk records missing `search_text` fail loudly.
        let missing_search_text = serde_json::json!({
            "id": "mem-bad-1",
            "content": "hello world",
            "category": "fact",
            "tags": [],
            "created_at": entry.created_at,
            "updated_at": entry.updated_at
        });
        assert!(serde_json::from_value::<MemoryEntry>(missing_search_text).is_err());
    }
}
