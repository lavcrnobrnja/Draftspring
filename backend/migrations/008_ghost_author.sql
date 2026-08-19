-- Ghost author selection: store which staff user to publish as
ALTER TABLE users ADD COLUMN ghost_author_id TEXT;
ALTER TABLE users ADD COLUMN ghost_author_name TEXT;
