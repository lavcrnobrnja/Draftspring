-- Migration 013: Add 'processing_ideation' to seed_batches status CHECK constraint.
-- SQLite requires table recreation to modify CHECK constraints.

-- Step 1: Create new table with updated constraint
CREATE TABLE seed_batches_new (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    status      TEXT NOT NULL DEFAULT 'pending_ideation'
                CHECK(status IN ('pending_ideation','processing_ideation','ideation_complete','waiting_approval','processed','expired')),
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    regen_count INTEGER NOT NULL DEFAULT 0,
    regen_feedback TEXT,
    source TEXT DEFAULT 'brief'
);

-- Step 2: Copy data
INSERT INTO seed_batches_new SELECT * FROM seed_batches;

-- Step 3: Drop old table
DROP TABLE seed_batches;

-- Step 4: Rename new table
ALTER TABLE seed_batches_new RENAME TO seed_batches;

-- Step 5: Recreate index
CREATE INDEX idx_seed_batches_user ON seed_batches(user_id, status);
