"""Tests for the article review email (CP2 full article in email)."""

import pytest

from app.config import Config
from app.services.email import (
    send_article_review_email,
    get_sent_emails,
    clear_sent_emails,
)


@pytest.fixture
def test_config():
    return Config(
        APP_ENV="test",
        APP_BASE_URL="http://localhost:8000",
        RESEND_API_KEY="re_test_key",
        EMAIL_FROM_ADDRESS="content@example.com",
        EMAIL_FROM_NAME="DraftSpring",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
    )


@pytest.fixture(autouse=True)
def clean_emails():
    clear_sent_emails()
    yield


@pytest.mark.asyncio
async def test_article_review_email_stored_in_test_mode(test_config):
    """send_article_review_email stores email in _sent_emails in test mode."""
    result = await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="10 Tips for Better Sleep",
        article_html="<p>Here is the full article content.</p>",
        cover_image_url="https://s3.example.com/cover.jpg",
        magic_link_token="abc123token",
        next_publish_date_formatted="Tuesday, March 24 at 9:00 AM EST",
    )

    assert result is True
    emails = get_sent_emails()
    assert len(emails) == 1
    email = emails[0]
    assert email["to"] == "user@example.com"
    assert email["purpose"] == "article_review"
    assert email["article_title"] == "10 Tips for Better Sleep"
    assert email["token"] == "abc123token"
    assert "abc123token" in email["verify_url"]
    assert "action=approve" in email["approve_url"]


@pytest.mark.asyncio
async def test_article_review_email_contains_article_content(test_config):
    """Email HTML includes the article body, title, and images."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="My Great Article",
        article_html="<p>This is the <strong>full body</strong> of the article.</p>",
        cover_image_url="https://s3.example.com/cover.jpg",
        magic_link_token="token123",
        next_publish_date_formatted="Wednesday, March 25 at 10:00 AM EST",
    )

    email = get_sent_emails()[0]
    html = email["html"]

    # Article content is in the email
    assert "full body" in html
    assert "My Great Article" in html

    # Cover image is rendered
    assert "https://s3.example.com/cover.jpg" in html

    # Publish date shown
    assert "Wednesday, March 25 at 10:00 AM EST" in html


@pytest.mark.asyncio
async def test_article_review_email_has_approve_and_revision_links(test_config):
    """Email contains both approve and revision links."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="Test Article",
        article_html="<p>Content</p>",
        cover_image_url=None,
        magic_link_token="mytoken456",
        next_publish_date_formatted="Thursday, March 26 at 11:00 AM EST",
    )

    email = get_sent_emails()[0]
    html = email["html"]

    # Approve link with action param
    assert "auth/verify?token=mytoken456&amp;action=approve" in html or \
           "auth/verify?token=mytoken456&action=approve" in html

    # Regular verify link (for view on web and revision)
    assert "auth/verify?token=mytoken456" in html

    # Button labels
    assert "Approve" in html
    assert "Revision" in html


@pytest.mark.asyncio
async def test_article_review_email_no_cover_image(test_config):
    """Email works fine without a cover image."""
    result = await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="No Cover Article",
        article_html="<p>Just text.</p>",
        cover_image_url=None,
        magic_link_token="token789",
        next_publish_date_formatted="Friday, March 27 at 8:00 AM EST",
    )

    assert result is True
    email = get_sent_emails()[0]
    html = email["html"]
    # Should not have a broken img tag
    assert "Cover image" not in html


@pytest.mark.asyncio
async def test_article_review_email_subject_format(test_config):
    """Email subject includes the article title."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="Amazing Guide",
        article_html="<p>Body</p>",
        cover_image_url=None,
        magic_link_token="tok",
        next_publish_date_formatted="Monday, March 30 at 9:00 AM EST",
    )

    email = get_sent_emails()[0]
    assert "Amazing Guide" in email["subject"]
    assert "\U0001f4dd" in email["subject"]


@pytest.mark.asyncio
async def test_article_review_email_table_based_layout(test_config):
    """Email uses table-based layout, not flexbox/grid."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="Layout Test",
        article_html="<p>Content</p>",
        cover_image_url=None,
        magic_link_token="tok",
        next_publish_date_formatted="Monday, March 30 at 9:00 AM EST",
    )

    html = get_sent_emails()[0]["html"]
    assert "role=\"presentation\"" in html
    assert "display:flex" not in html
    assert "display:grid" not in html


@pytest.mark.asyncio
async def test_article_review_email_escapes_title(test_config):
    """Article title with HTML characters is properly escaped."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title='<script>alert("xss")</script>Bad Title',
        article_html="<p>Content</p>",
        cover_image_url=None,
        magic_link_token="tok",
        next_publish_date_formatted="Monday, March 30 at 9:00 AM EST",
    )

    html = get_sent_emails()[0]["html"]
    # Script tag should be escaped in the HTML body
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.asyncio
async def test_article_review_email_rejects_javascript_cover_url(test_config):
    """Cover image URL with javascript: protocol is rejected."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="Test",
        article_html="<p>Content</p>",
        cover_image_url="javascript:alert(1)",
        magic_link_token="tok",
        next_publish_date_formatted="Monday, March 30 at 9:00 AM EST",
    )

    html = get_sent_emails()[0]["html"]
    assert "javascript:" not in html


@pytest.mark.asyncio
async def test_article_review_email_wider_template(test_config):
    """Email uses 600px width (wider than standard 480px template)."""
    await send_article_review_email(
        config=test_config,
        to="user@example.com",
        article_title="Wide Test",
        article_html="<p>Content</p>",
        cover_image_url=None,
        magic_link_token="tok",
        next_publish_date_formatted="Monday, March 30 at 9:00 AM EST",
    )

    html = get_sent_emails()[0]["html"]
    assert "max-width:600px" in html
