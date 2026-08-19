-- Migration 014: Record which state an article was in when it failed.
--
-- Used by the user-facing retry endpoint (POST /api/articles/{id}/retry)
-- to resume from the exact step that failed, preserving work done before
-- the failure (outline, drafts, images, etc.).
--
-- Backfill: existing FAILED articles have NULL here. Retry endpoint treats
-- NULL as OUTLINING (safe fallback — re-runs the whole pipeline).

ALTER TABLE articles ADD COLUMN failed_from_state TEXT;
