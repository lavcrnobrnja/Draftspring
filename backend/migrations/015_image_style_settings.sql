-- DraftSpring #374: profile image style defaults and Content Brief override metadata

ALTER TABLE users ADD COLUMN image_style TEXT NOT NULL DEFAULT 'photography';
ALTER TABLE users ADD COLUMN image_substyle TEXT NOT NULL DEFAULT 'editorial_documentary';

ALTER TABLE seed_batches ADD COLUMN image_style TEXT;
ALTER TABLE seed_batches ADD COLUMN image_substyle TEXT;
