-- GhostWriter Initial Schema

-- USERS
CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,
    email                   TEXT UNIQUE NOT NULL,
    ghost_url               TEXT,
    ghost_admin_api_key_enc TEXT,
    ghost_site_title        TEXT,
    ghost_version           TEXT,
    ghost_key_valid         INTEGER DEFAULT 0,
    ghost_key_checked_at    TEXT,
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    subscription_status     TEXT DEFAULT 'none',
    publish_days            TEXT DEFAULT '[]',
    publish_time            TEXT DEFAULT '09:00',
    publish_timezone        TEXT DEFAULT 'America/New_York',
    articles_per_cycle_limit INTEGER DEFAULT 8,
    brand_voice             TEXT DEFAULT 'Professional but conversational. Write for a smart general audience.',
    default_word_count      INTEGER DEFAULT 1500,
    email_bounce            INTEGER DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- SESSIONS
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    scope       TEXT NOT NULL DEFAULT 'full'
                CHECK(scope IN ('full','checkpoint_1','checkpoint_2','admin')),
    scope_ref   TEXT,
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- MAGIC LINKS
CREATE TABLE IF NOT EXISTS magic_links (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id),
    token_hash    TEXT UNIQUE NOT NULL,
    purpose       TEXT NOT NULL CHECK(purpose IN ('login','checkpoint_1','checkpoint_2','admin')),
    reference_id  TEXT,
    expires_at    TEXT,
    consumed_at   TEXT,
    created_at    TEXT NOT NULL
);

-- SEED BATCHES
CREATE TABLE IF NOT EXISTS seed_batches (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    status      TEXT NOT NULL DEFAULT 'pending_ideation'
                CHECK(status IN ('pending_ideation','ideation_complete','waiting_approval','processed','expired')),
    created_at  TEXT NOT NULL,
    expires_at  TEXT
);

-- SEEDS
CREATE TABLE IF NOT EXISTS seeds (
    id          TEXT PRIMARY KEY,
    batch_id    TEXT NOT NULL REFERENCES seed_batches(id),
    seed_type   TEXT NOT NULL CHECK(seed_type IN ('topic','url')),
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- IDEAS
CREATE TABLE IF NOT EXISTS ideas (
    id               TEXT PRIMARY KEY,
    batch_id         TEXT NOT NULL REFERENCES seed_batches(id),
    seed_id          TEXT NOT NULL REFERENCES seeds(id),
    title            TEXT NOT NULL,
    angle            TEXT NOT NULL,
    target_keyword   TEXT NOT NULL,
    estimated_volume TEXT DEFAULT 'low' CHECK(estimated_volume IN ('low','medium','high')),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','approved','rejected','expired')),
    approved_at      TEXT,
    created_at       TEXT NOT NULL
);

-- ARTICLES (state machine cursor)
CREATE TABLE IF NOT EXISTS articles (
    id                          TEXT PRIMARY KEY,
    user_id                     TEXT NOT NULL REFERENCES users(id),
    idea_id                     TEXT UNIQUE NOT NULL REFERENCES ideas(id),
    state                       TEXT NOT NULL DEFAULT 'OUTLINING'
                                CHECK(state IN (
                                    'OUTLINING','DRAFTING','HUMANIZING','EDIT_REVIEW',
                                    'MEDIA_ASSEMBLY','WAITING_CHECKPOINT_2','REVISION',
                                    'READY_TO_PUBLISH','PUBLISHING','PUBLISHED',
                                    'FAILED','ARCHIVED'
                                )),
    lifetime_draft_iterations   INTEGER DEFAULT 0,
    seo_meta                    TEXT,
    visible_tags                TEXT,
    outline_json                TEXT,
    ghost_post_id               TEXT,
    ghost_post_url              TEXT,
    scheduled_publish_at        TEXT,
    published_at                TEXT,
    failed_at                   TEXT,
    failure_reason              TEXT,
    locked_by                   TEXT,
    locked_at                   TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

-- DRAFT ITERATIONS
CREATE TABLE IF NOT EXISTS draft_iterations (
    id                  TEXT PRIMARY KEY,
    article_id          TEXT NOT NULL REFERENCES articles(id),
    iteration_number    INTEGER NOT NULL,
    raw_draft_md        TEXT,
    humanized_draft_md  TEXT,
    critique_json       TEXT,
    critique_verdict    TEXT CHECK(critique_verdict IN ('approved','revision_needed')),
    created_at          TEXT NOT NULL,
    UNIQUE(article_id, iteration_number)
);

-- ARTICLE IMAGES
CREATE TABLE IF NOT EXISTS article_images (
    id                TEXT PRIMARY KEY,
    article_id        TEXT NOT NULL REFERENCES articles(id),
    anchor_index      INTEGER NOT NULL,
    source_type       TEXT NOT NULL CHECK(source_type IN ('vault','generated')),
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

-- VAULT IMAGES
CREATE TABLE IF NOT EXISTS vault_images (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    filename     TEXT NOT NULL,
    storage_url  TEXT NOT NULL,
    mime_type    TEXT NOT NULL,
    description  TEXT,
    tags         TEXT DEFAULT '[]',
    used_count   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL
);

-- ARTICLE REVIEWS
CREATE TABLE IF NOT EXISTS article_reviews (
    id              TEXT PRIMARY KEY,
    article_id      TEXT NOT NULL REFERENCES articles(id),
    review_number   INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','approved','revision_requested')),
    revision_notes  TEXT,
    reviewed_at     TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(article_id, review_number)
);

-- PIPELINE EVENTS (append-only audit log)
CREATE TABLE IF NOT EXISTS pipeline_events (
    id          TEXT PRIMARY KEY,
    article_id  TEXT REFERENCES articles(id),
    batch_id    TEXT REFERENCES seed_batches(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    event_type  TEXT NOT NULL CHECK(event_type IN (
                    'state_transition','llm_call','image_generation',
                    'email_sent','error','retry','manual_intervention'
                )),
    from_state  TEXT,
    to_state    TEXT,
    payload     TEXT,
    created_at  TEXT NOT NULL
);

-- USAGE LEDGER
CREATE TABLE IF NOT EXISTS usage_ledger (
    id                    TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL REFERENCES users(id),
    billing_cycle_start   TEXT NOT NULL,
    billing_cycle_end     TEXT NOT NULL,
    articles_started      INTEGER DEFAULT 0,
    articles_published    INTEGER DEFAULT 0,
    llm_input_tokens      INTEGER DEFAULT 0,
    llm_output_tokens     INTEGER DEFAULT 0,
    image_generations     INTEGER DEFAULT 0,
    estimated_cost_cents  INTEGER DEFAULT 0,
    updated_at            TEXT NOT NULL,
    UNIQUE(user_id, billing_cycle_start)
);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_articles_user_state ON articles(user_id, state);
CREATE INDEX IF NOT EXISTS idx_articles_scheduled ON articles(state, scheduled_publish_at);
CREATE INDEX IF NOT EXISTS idx_articles_locked ON articles(locked_by, locked_at);
CREATE INDEX IF NOT EXISTS idx_ideas_batch_status ON ideas(batch_id, status);
CREATE INDEX IF NOT EXISTS idx_magic_links_token ON magic_links(token_hash);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_article ON pipeline_events(article_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_ledger_user_cycle ON usage_ledger(user_id, billing_cycle_start);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_seed_batches_user ON seed_batches(user_id, status);
CREATE INDEX IF NOT EXISTS idx_vault_images_user ON vault_images(user_id);
