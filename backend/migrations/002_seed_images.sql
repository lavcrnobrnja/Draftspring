-- Seed images: allow users to attach images directly to seeds

CREATE TABLE IF NOT EXISTS seed_images (
    id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL REFERENCES seeds(id),
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seed_images_seed ON seed_images(seed_id);
