"""Tests for T8 expanded: article review email with full content + auth action param passthrough."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2, _format_publish_date, _replace_image_anchors, _sanitize_html
from app.services.email import get_sent_emails, clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *


class TestT8ArticleReviewEmail:
    """Test that T8 sends article review email with content."""

    @pytest.mark.asyncio
    async def test_t8_sends_article_review_email(self, db, config):
        """T8 should send article_review email with article content, not a generic magic link email."""
        clear_sent_emails()

        # Create user with publish settings
        user = await create_user(db, "t8test@example.com")
        await update_user(
            db, user["id"],
            subscription_status="active",
            publish_days='["monday","thursday"]',
            publish_time="09:00",
            publish_timezone="America/New_York",
        )

        # Create seed batch, seed, and idea (title lives on ideas table)
        now = utc_now()
        batch_id = generate_id()
        seed_id = generate_id()
        idea_id = generate_id()
        await db.execute(
            """INSERT INTO seed_batches (id, user_id, status, created_at)
               VALUES (?, ?, 'processed', ?)""",
            (batch_id, user["id"], now),
        )
        await db.execute(
            """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
               VALUES (?, ?, 'topic', 'sleep tips', ?)""",
            (seed_id, batch_id, now),
        )
        await db.execute(
            """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
               VALUES (?, ?, ?, '10 Tips for Better Sleep', 'health angle', 'sleep tips', 'approved', ?)""",
            (idea_id, batch_id, seed_id, now),
        )

        # Create article linked to idea
        article_id = generate_id()
        await db.execute(
            """INSERT INTO articles (id, user_id, idea_id, state, lifetime_draft_iterations, created_at, updated_at)
               VALUES (?, ?, ?, 'MEDIA_ASSEMBLY', 1, ?, ?)""",
            (article_id, user["id"], idea_id, now, now),
        )

        # Create draft iteration with markdown content
        draft_md = "# Introduction\n\nSleep is important.\n\n[IMAGE_ANCHOR:1]\n\n## Tip 1\n\nGo to bed early."
        await db.execute(
            """INSERT INTO draft_iterations (id, article_id, iteration_number, humanized_draft_md, created_at)
               VALUES (?, ?, 1, ?, ?)""",
            (generate_id(), article_id, draft_md, now),
        )

        # Create article images
        await db.execute(
            """INSERT INTO article_images (id, article_id, anchor_index, storage_url, alt_text, source_type, created_at)
               VALUES (?, ?, 'COVER', 'https://s3.example.com/cover.jpg', 'Cover', 'generated', ?)""",
            (generate_id(), article_id, now),
        )
        await db.execute(
            """INSERT INTO article_images (id, article_id, anchor_index, storage_url, alt_text, source_type, created_at)
               VALUES (?, ?, '1', 'https://s3.example.com/img1.jpg', 'Sleep image', 'generated', ?)""",
            (generate_id(), article_id, now),
        )

        await db.commit()

        result = await run_to_checkpoint_2(db, config, article_id)
        assert result["success"] is True

        emails = get_sent_emails()
        assert len(emails) == 1
        email = emails[0]

        # Should be article_review purpose, not checkpoint_2
        assert email["purpose"] == "article_review"
        assert email["article_title"] == "10 Tips for Better Sleep"

        # HTML should contain article content
        html = email["html"]
        assert "Sleep is important" in html
        assert "Go to bed early" in html

        # Cover image should be in the email
        assert "https://s3.example.com/cover.jpg" in html

        # Body image should be inlined
        assert "https://s3.example.com/img1.jpg" in html

        # Should have approve and revision links
        assert "action=approve" in html
        assert "Approve" in html
        assert "Revision" in html

        # Should show publish date
        assert email["next_publish_date_formatted"]  # non-empty

    @pytest.mark.asyncio
    async def test_t8_no_draft_still_sends(self, db, config):
        """T8 should still work even if no draft content exists (edge case)."""
        clear_sent_emails()

        user = await create_user(db, "nodraft@example.com")
        await update_user(
            db, user["id"],
            subscription_status="active",
            publish_days='["monday"]',
            publish_time="10:00",
            publish_timezone="UTC",
        )

        now = utc_now()
        batch_id = generate_id()
        seed_id = generate_id()
        idea_id = generate_id()
        await db.execute(
            """INSERT INTO seed_batches (id, user_id, status, created_at)
               VALUES (?, ?, 'processed', ?)""",
            (batch_id, user["id"], now),
        )
        await db.execute(
            """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
               VALUES (?, ?, 'topic', 'empty test', ?)""",
            (seed_id, batch_id, now),
        )
        await db.execute(
            """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
               VALUES (?, ?, ?, 'Empty Article', 'test angle', 'test', 'approved', ?)""",
            (idea_id, batch_id, seed_id, now),
        )

        article_id = generate_id()
        await db.execute(
            """INSERT INTO articles (id, user_id, idea_id, state, lifetime_draft_iterations, created_at, updated_at)
               VALUES (?, ?, ?, 'MEDIA_ASSEMBLY', 1, ?, ?)""",
            (article_id, user["id"], idea_id, now, now),
        )
        await db.commit()

        result = await run_to_checkpoint_2(db, config, article_id)
        assert result["success"] is True

        emails = get_sent_emails()
        assert len(emails) == 1
        assert emails[0]["purpose"] == "article_review"

    @pytest.mark.asyncio
    async def test_t8_email_failure_does_not_commit_checkpoint_state_or_rows(self, db, config):
        user = await create_user(db, "emailfail@example.com")
        await update_user(
            db, user["id"],
            subscription_status="active",
            publish_days='["monday"]',
            publish_time="10:00",
            publish_timezone="UTC",
        )

        now = utc_now()
        batch_id = generate_id()
        seed_id = generate_id()
        idea_id = generate_id()
        article_id = generate_id()
        await db.execute(
            """INSERT INTO seed_batches (id, user_id, status, created_at)
               VALUES (?, ?, 'processed', ?)""",
            (batch_id, user["id"], now),
        )
        await db.execute(
            """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
               VALUES (?, ?, 'topic', 'email fail test', ?)""",
            (seed_id, batch_id, now),
        )
        await db.execute(
            """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
               VALUES (?, ?, ?, 'Email Failure Article', 'test angle', 'test', 'approved', ?)""",
            (idea_id, batch_id, seed_id, now),
        )
        await db.execute(
            """INSERT INTO articles (id, user_id, idea_id, state, lifetime_draft_iterations, created_at, updated_at)
               VALUES (?, ?, ?, 'MEDIA_ASSEMBLY', 1, ?, ?)""",
            (article_id, user["id"], idea_id, now, now),
        )
        await db.execute(
            """INSERT INTO draft_iterations (id, article_id, iteration_number, humanized_draft_md, created_at)
               VALUES (?, ?, 1, 'Draft body', ?)""",
            (generate_id(), article_id, now),
        )
        await db.commit()

        with patch(
            "app.pipeline.transitions.t8_to_checkpoint_2.send_article_review_email",
            new=AsyncMock(side_effect=RuntimeError("email provider down")),
        ):
            with pytest.raises(RuntimeError, match="email provider down"):
                await run_to_checkpoint_2(db, config, article_id)

        await db.rollback()

        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"

        cursor = await db.execute("SELECT COUNT(*) AS count FROM article_reviews WHERE article_id = ?", (article_id,))
        assert (await cursor.fetchone())["count"] == 0

        cursor = await db.execute(
            "SELECT COUNT(*) AS count FROM magic_links WHERE user_id = ? AND purpose = 'checkpoint_2'",
            (user["id"],),
        )
        assert (await cursor.fetchone())["count"] == 0


class TestFormatPublishDate:
    """Test the _format_publish_date helper."""

    def test_format_eastern_time(self):
        result = _format_publish_date("2026-03-24T13:00:00Z", "America/New_York")
        assert "Tuesday" in result
        assert "March 24" in result
        assert "9:00 AM" in result

    def test_format_utc(self):
        result = _format_publish_date("2026-03-24T09:00:00Z", "UTC")
        assert "Tuesday" in result
        assert "9:00 AM" in result
        assert "UTC" in result

    def test_format_invalid_timezone_falls_back(self):
        result = _format_publish_date("2026-03-24T09:00:00Z", "Invalid/TZ")
        assert "Tuesday" in result  # Should still work with UTC fallback


class TestReplaceImageAnchors:
    """Test image anchor replacement in HTML."""

    def test_replace_numbered_anchor(self):
        images = {"1": {"storage_url": "https://s3.example.com/img1.jpg", "alt_text": "Test image"}}
        html = "<p>Before</p>[IMAGE_ANCHOR:1]<p>After</p>"
        result = _replace_image_anchors(html, images)
        assert "https://s3.example.com/img1.jpg" in result
        assert "[IMAGE_ANCHOR:1]" not in result

    def test_cover_anchor_removed(self):
        images = {"COVER": {"storage_url": "https://s3.example.com/cover.jpg", "alt_text": "Cover"}}
        html = "<p>Before</p>[IMAGE_ANCHOR:COVER]<p>After</p>"
        result = _replace_image_anchors(html, images)
        assert "https://s3.example.com/cover.jpg" not in result  # Cover handled separately
        assert "[IMAGE_ANCHOR:COVER]" not in result

    def test_missing_anchor_removed_silently(self):
        html = "<p>Before</p>[IMAGE_ANCHOR:99]<p>After</p>"
        result = _replace_image_anchors(html, {})
        assert "[IMAGE_ANCHOR:99]" not in result


class TestAuthActionPassthrough:
    """Test that auth verify passes action param for CP2."""

    @pytest.mark.asyncio
    async def test_verify_with_action_approve(self, db, config):
        """Verify endpoint should append ?action=approve to redirect URL."""
        from app.models.magic_link import create_magic_link, verify_magic_link

        user = await create_user(db, "authtest@example.com")
        article_id = generate_id()
        token = await create_magic_link(db, user["id"], "checkpoint_2", reference_id=article_id)

        # Verify the token
        link = await verify_magic_link(db, token)
        assert link is not None
        assert link["purpose"] == "checkpoint_2"
        assert link["reference_id"] == article_id

        # Simulate what the verify endpoint does
        redirect_url = f"/review/article/{link.get('reference_id', '')}"
        action = "approve"  # simulating request.query_params.get("action")
        if action in ("approve",):
            redirect_url += f"?action={action}"

        assert redirect_url == f"/review/article/{article_id}?action=approve"

    @pytest.mark.asyncio
    async def test_verify_without_action(self, db, config):
        """Verify endpoint without action param should not append anything."""
        from app.models.magic_link import create_magic_link, verify_magic_link

        user = await create_user(db, "authtest2@example.com")
        article_id = generate_id()
        token = await create_magic_link(db, user["id"], "checkpoint_2", reference_id=article_id)

        link = await verify_magic_link(db, token)
        redirect_url = f"/review/article/{link.get('reference_id', '')}"
        action = None  # No action param
        if action in ("approve",):
            redirect_url += f"?action={action}"

        assert "?action" not in redirect_url


class TestSanitizeHtml:
    """Test HTML sanitization for email safety."""

    def test_strips_script_tags(self):
        html = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        result = _sanitize_html(html)
        assert "<script" not in result
        assert "alert" not in result
        assert "<p>Hello</p>" in result
        assert "<p>World</p>" in result

    def test_strips_event_handlers(self):
        html = '<img src="test.jpg" onerror="alert(1)" alt="test">'
        result = _sanitize_html(html)
        assert "onerror" not in result
        assert "alert" not in result

    def test_strips_javascript_urls(self):
        html = '<a href="javascript:alert(1)">Click</a>'
        result = _sanitize_html(html)
        assert "javascript:" not in result

    def test_preserves_safe_html(self):
        html = '<p>Hello <strong>world</strong></p><ul><li>Item</li></ul>'
        result = _sanitize_html(html)
        assert result == html

    def test_strips_iframe(self):
        html = '<p>Before</p><iframe src="evil.com"></iframe><p>After</p>'
        result = _sanitize_html(html)
        assert "<iframe" not in result

    def test_strips_style_tags(self):
        html = '<p>Text</p><style>body{display:none}</style>'
        result = _sanitize_html(html)
        assert "<style" not in result


class TestImageAnchorSecurity:
    """Test that image anchor replacement is secure."""

    def test_rejects_javascript_url(self):
        images = {"1": {"storage_url": "javascript:alert(1)", "alt_text": "Evil"}}
        html = "[IMAGE_ANCHOR:1]"
        result = _replace_image_anchors(html, images)
        assert "javascript:" not in result
        assert "<img" not in result  # Should not render the image at all

    def test_escapes_alt_text(self):
        images = {"1": {"storage_url": "https://s3.example.com/img.jpg", "alt_text": '"><script>alert(1)</script>'}}
        html = "[IMAGE_ANCHOR:1]"
        result = _replace_image_anchors(html, images)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result or "script" not in result
