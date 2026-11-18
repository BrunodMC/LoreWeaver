-- The name the party knows an entry by, when its real name is a secret ('' = they know the real one).
-- "The voice in the night bloom" for a god they haven't met; "the hooded ambusher" for a man they killed.
ALTER TABLE entities ADD COLUMN party_name TEXT NOT NULL DEFAULT '';
