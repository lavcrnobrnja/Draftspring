-- Add source field to seed_batches to distinguish brief vs analysis origin
ALTER TABLE seed_batches ADD COLUMN source TEXT DEFAULT 'brief';
