-- Migration 003: Add 'seed' to article_images source_type CHECK constraint
-- SQLite cannot ALTER CHECK constraints, so we recreate the table

CREATE TABLE IF NOT EXISTS article_images_new (
    id                TEXT PRIMARY KEY,
    article_id        TEXT NOT NULL REFERENCES articles(id),
    anchor_index      INTEGER NOT NULL,
    source_type       TEXT NOT NULL CHECK(source_type IN ('vault','generated','seed')),
    vault_image_id    TEXT REFERENCES vault_images(id),
    generation_prompt TEXT,
    section_heading   TEXT,
    image_guidance    TEXT,
    storage_url       TEXT,
    ghost_image_url   TEXT,
    width             INTEGER,
    height            INTEGER,
    alt_text          TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE(article_id, anchor_index)
);

INSERT INTO article_images_new SELECT * FROM article_images;

DROP TABLE article_images;

ALTER TABLE article_images_new RENAME TO article_images;
