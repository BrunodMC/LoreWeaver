-- Small campaign-wide settings the DM edits in the app (e.g. the shared style for portrait prompts).
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
