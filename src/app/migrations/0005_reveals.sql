-- What the party learned and when: one row per reveal (a whole entry, or one ||secret|| passage).
-- A reveal is filed to a session entry when one dated that day exists; otherwise it waits, unfiled,
-- for the GM to file it after the game (often to a session entry written afterwards).
CREATE TABLE reveals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id   INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  entity_name TEXT NOT NULL,              -- kept, so the log still reads if the entry is deleted
  field       TEXT NOT NULL DEFAULT '',   -- the field's label; '' when the whole entry was revealed
  text        TEXT NOT NULL DEFAULT '',   -- the passage revealed; '' for a whole entry
  session_id  INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_reveals_session ON reveals(session_id, id);
