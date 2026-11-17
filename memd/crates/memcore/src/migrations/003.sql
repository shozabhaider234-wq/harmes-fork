ALTER TABLE memories ADD COLUMN superseded_by TEXT;
CREATE INDEX memories_superseded_by ON memories(superseded_by) WHERE superseded_by IS NOT NULL;
PRAGMA user_version=3;
