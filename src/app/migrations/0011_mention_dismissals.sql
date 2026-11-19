-- Unlinked mentions the GM ruled out: a pair that isn't a relationship, or (source_id NULL) an entry
-- mentioned so widely (a city, a realm) that it should never be suggested as a link target.
CREATE TABLE mention_dismissals (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id  INTEGER REFERENCES entities(id) ON DELETE CASCADE,
  target_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  UNIQUE (source_id, target_id)
);
