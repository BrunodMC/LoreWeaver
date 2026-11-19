-- Proposals: an AI call suggests changes to an EXISTING entry (unlike generations, which make drafts).
-- Nothing changes until the GM ticks fields and applies. `base` keeps each changed field as it was when
-- proposed, so an edit made in between is caught rather than overwritten, and the old text survives.
CREATE TABLE proposals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,                                   -- e.g. 'time_passes'
  entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  request     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(request)),   -- what the GM asked (elapsed, events…)
  summary     TEXT NOT NULL DEFAULT '',
  changes     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(changes)),   -- field key → proposed text
  base        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(base)),      -- field key → text when proposed
  reasons     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(reasons)),   -- field key → why
  links       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(links)),     -- proposed new links
  dm_notes    TEXT NOT NULL DEFAULT '',
  state       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(state)),     -- per item: applied / rejected / conflict
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  model       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_proposals_entity ON proposals(entity_id, status, id);
