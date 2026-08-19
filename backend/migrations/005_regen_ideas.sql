-- Migration 005: Add idea regeneration support to seed_batches
-- Adds regen_count (max 3 regenerations) and regen_feedback (user's feedback text)

ALTER TABLE seed_batches ADD COLUMN regen_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE seed_batches ADD COLUMN regen_feedback TEXT;
