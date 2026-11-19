-- A death: '' (alive), 'secret' (the party doesn't know) or 'known'. death_note says how, for the GM
-- (shown to the party only once the death is known). A proposal's cause_id groups a death's ripple.
ALTER TABLE entities ADD COLUMN deceased TEXT NOT NULL DEFAULT '' CHECK (deceased IN ('', 'secret', 'known'));
ALTER TABLE entities ADD COLUMN death_note TEXT NOT NULL DEFAULT '';
ALTER TABLE proposals ADD COLUMN cause_id INTEGER REFERENCES entities(id) ON DELETE SET NULL;
