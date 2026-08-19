-- Migration 004: Fix publish_days column default from '["monday","thursday"]' to '[]'
-- SQLite doesn't support ALTER COLUMN, so we update any existing users who still
-- have the old default and haven't explicitly set a schedule.
-- The application layer (create_user) now explicitly sets '[]' for new users.

-- Update existing users who have the old default and no batches (never used the schedule)
UPDATE users SET publish_days = '[]'
WHERE publish_days = '["monday","thursday"]'
  AND id NOT IN (SELECT DISTINCT user_id FROM seed_batches);
