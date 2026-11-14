-- Phase 1: AI generation.

-- Canon always feeds generation; drafts only when the DM opts them in (e.g. a to-do list of open threads).
ALTER TABLE entities ADD COLUMN ai_context INTEGER NOT NULL DEFAULT 0 CHECK (ai_context IN (0, 1));

-- One row per version of a generated draft: an AI attempt or a snapshot of the DM's hand edits.
-- lineage_id groups a draft's versions (it's the id of the lineage's first row). entity_id goes NULL
-- when the draft is discarded, so the log of what was tried outlives the entity.
CREATE TABLE generations (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  lineage_id   INTEGER REFERENCES generations(id),  -- set to the row's own id in the same transaction
  entity_id    INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  entity_type  TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('generated', 'edit')),
  brief        TEXT NOT NULL DEFAULT '',
  notes        TEXT NOT NULL DEFAULT '',  -- regeneration notes ("keep the stat block, redo the hook")
  anchors      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(anchors)),  -- entity ids the draft must link to
  output       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(output)),   -- name, fields, links, threads, notes
  model        TEXT,
  usage        TEXT CHECK (usage IS NULL OR json_valid(usage)),
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_generations_lineage ON generations(lineage_id, id);
CREATE INDEX idx_generations_entity ON generations(entity_id);
