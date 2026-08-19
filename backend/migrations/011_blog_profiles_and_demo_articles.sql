-- Blog profiles (reusable product table) and demo articles (marketing)

CREATE TABLE IF NOT EXISTS blog_profiles (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    site_name TEXT,
    is_ghost BOOLEAN DEFAULT 1,
    profile_data JSON NOT NULL,
    analyzed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS demo_articles (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    blog_profile_id TEXT REFERENCES blog_profiles(id),
    task_status TEXT DEFAULT 'pending',
    stage_message TEXT,
    idea_title TEXT,
    idea_angle TEXT,
    article_html TEXT,
    article_preview TEXT,
    cover_image_url TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
