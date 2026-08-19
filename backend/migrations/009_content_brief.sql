-- Content Brief: Add image_role and description to seed_images, content_brief to articles

-- Recreate seed_images with new columns (SQLite doesn't support ADD COLUMN well for complex cases)
CREATE TABLE IF NOT EXISTS seed_images_new (
    id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL REFERENCES seeds(id),
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    image_role TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO seed_images_new (id, seed_id, filename, storage_path, mime_type, image_role, description, created_at)
    SELECT id, seed_id, filename, storage_path, mime_type, NULL, NULL, created_at FROM seed_images;

DROP TABLE IF EXISTS seed_images;
ALTER TABLE seed_images_new RENAME TO seed_images;

CREATE INDEX IF NOT EXISTS idx_seed_images_seed ON seed_images(seed_id);

-- Add content_brief column to articles
ALTER TABLE articles ADD COLUMN content_brief TEXT;
