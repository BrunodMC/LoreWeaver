ALTER TABLE entities ADD COLUMN known_to_party INTEGER NOT NULL DEFAULT 0 CHECK (known_to_party IN (0, 1));
ALTER TABLE links ADD COLUMN known_to_party INTEGER NOT NULL DEFAULT 0 CHECK (known_to_party IN (0, 1));
