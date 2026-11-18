-- Audits: a model reads some text against the bible and flags passages for the GM to rule on.
-- findings is the flagged list; state records the GM's ruling per finding (accepted / rejected / failed).
CREATE TABLE audits (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,                                   -- e.g. 'secrecy'
  entity_id   INTEGER REFERENCES entities(id) ON DELETE CASCADE,
  findings    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(findings)),
  state       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state)),
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  model       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_audits_entity ON audits(entity_id, kind, id);
