-- Composite encounters: a workspace that grows a premise (the "spine"), then a pool of candidate cast
-- members, then an encounter draft with its member drafts. Kept in the database, not in memory, so a
-- page refresh or a server restart doesn't throw away a minute of generation.
CREATE TABLE composites (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  brief        TEXT NOT NULL DEFAULT '',
  anchors      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(anchors)),   -- entity ids the encounter must link to
  pcs          INTEGER NOT NULL DEFAULT 4 CHECK (pcs BETWEEN 1 AND 12),
  spines       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(spines)),    -- [{title, premise, why}]
  spine        TEXT NOT NULL DEFAULT '',                                 -- the chosen premise, as edited
  pool         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pool)),      -- candidate cast members
  budget       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(budget)),    -- the GM's Battle Point toggles
  stage        TEXT NOT NULL DEFAULT 'spines' CHECK (stage IN ('spines', 'pool', 'drafting', 'done')),
  encounter_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Drafts made for a composite remember it, so regenerating one still sees the encounter sheet.
ALTER TABLE generations ADD COLUMN composite_id INTEGER REFERENCES composites(id) ON DELETE SET NULL;
