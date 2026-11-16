CREATE TABLE embedding_space (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    model_id TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK(dimension > 0)
);
CREATE TABLE memories (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL,
    access_scope TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('semantic', 'episodic')),
    normalized_text TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    expires_at INTEGER,
    payload TEXT NOT NULL,
    UNIQUE(namespace, access_scope, agent_id, kind, normalized_text)
);
CREATE INDEX memories_scope ON memories(namespace, access_scope, agent_id, kind);
CREATE INDEX memories_session ON memories(session_id);
CREATE VIRTUAL TABLE memories_fts USING fts5(content, tags, content='memories', content_rowid='rowid');
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags) VALUES(new.rowid, new.content, new.tags);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES('delete', old.rowid, old.content, old.tags);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES('delete', old.rowid, old.content, old.tags);
    INSERT INTO memories_fts(rowid, content, tags) VALUES(new.rowid, new.content, new.tags);
END;
PRAGMA user_version = 1;
