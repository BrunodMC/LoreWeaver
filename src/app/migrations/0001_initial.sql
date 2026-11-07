CREATE TABLE entities (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  type        TEXT NOT NULL,
  name        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'canon' CHECK (status IN ('draft','canon','archived')),
  fields      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(fields)),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_entities_type_status ON entities(type, status);

CREATE TABLE links (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  from_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  to_entity_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation_label  TEXT NOT NULL COLLATE NOCASE,
  CHECK (from_entity_id <> to_entity_id),
  UNIQUE (from_entity_id, to_entity_id, relation_label)
);
CREATE INDEX idx_links_to ON links(to_entity_id);
