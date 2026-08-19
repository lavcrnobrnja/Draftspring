"""Dashboard frontend tests: SPA serving, API response shapes for React Dashboard,
article state → column mapping, SEO/image data shapes, batch polling.

These tests hit the production instance at https://app.draftspring.io using
the authenticated session cookie. They verify the server-side contract that
the React frontend depends on.

Set DRAFTSPRING_SESSION env var to a valid session_id to run authenticated tests.
"""

import json
import os

import httpx
import pytest

BASE_URL = "https://app.draftspring.io"
SESSION_COOKIE = os.environ.get("DRAFTSPRING_SESSION", "01KM1JN8NWZ3D72RH2SFWV8HK9")

# Pre-flight: check if the session is valid. Skip authenticated tests if not.
def _session_is_valid():
    try:
        resp = httpx.get(
            f"{BASE_URL}/api/settings",
            cookies={"session_id": SESSION_COOKIE},
            timeout=10.0,
            follow_redirects=True,
        )
        return resp.status_code == 200
    except Exception:
        return False

_SESSION_VALID = _session_is_valid()
requires_session = pytest.mark.skipif(
    not _SESSION_VALID,
    reason="No valid production session (set DRAFTSPRING_SESSION env var)",
)

# Valid article states the frontend expects
VALID_STATES = {
    "OUTLINING", "DRAFTING", "HUMANIZING", "EDIT_REVIEW", "MEDIA_ASSEMBLY",
    "WAITING_CHECKPOINT_2", "REVISION", "READY_TO_PUBLISH", "PUBLISHING",
    "PUBLISHED", "FAILED", "ARCHIVED",
}

# Column mapping the frontend uses for kanban board
COLUMN_MAP = {
    "OUTLINING": "in_production",
    "DRAFTING": "in_production",
    "HUMANIZING": "in_production",
    "EDIT_REVIEW": "in_production",
    "MEDIA_ASSEMBLY": "in_production",
    "WAITING_CHECKPOINT_2": "in_review",
    "REVISION": "in_production",
    "READY_TO_PUBLISH": "scheduled",
    "PUBLISHING": "scheduled",
    "PUBLISHED": "published",
    "FAILED": "in_production",
    "ARCHIVED": "archived",
}

VALID_COLUMNS = {"in_production", "in_review", "scheduled", "published", "archived"}


@pytest.fixture
def auth_cookies():
    """Return authenticated session cookie dict."""
    return {"session_id": SESSION_COOKIE}


@pytest.fixture
def client():
    """Authenticated HTTP client for production tests."""
    return httpx.Client(
        base_url=BASE_URL,
        timeout=15.0,
        follow_redirects=True,
        cookies={"session_id": SESSION_COOKIE},
    )


@pytest.fixture
def anon_client():
    """Unauthenticated HTTP client for auth enforcement tests."""
    return httpx.Client(base_url=BASE_URL, timeout=15.0, follow_redirects=True)


# ── SPA Serving ──

class TestSPAServing:
    """Verify the SPA is served correctly for frontend routes."""

    def test_root_serves_landing_page(self, client):
        """GET / serves the landing page (HTML)."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_dashboard_serves_spa_html(self, client):
        """GET /dashboard serves the SPA HTML shell."""
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/html" in content_type
        # SPA shell should contain a root element and script tag
        body = resp.text
        assert '<div id="root">' in body or '<div id="app">' in body or "<!doctype html>" in body.lower()

    def test_spa_fallback_for_client_routes(self, client):
        """GET /dashboard/some-nested-route serves SPA HTML (client-side routing)."""
        resp = client.get("/dashboard/some-nested-route")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_review_route_serves_spa(self, client):
        """GET /review/some-article-id serves SPA HTML."""
        resp = client.get("/review/fake-article-id")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_settings_route_serves_spa(self, client):
        """GET /settings serves SPA HTML."""
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_static_assets_not_spa_fallback(self, client):
        """Static asset paths (JS/CSS) return actual files, not SPA HTML."""
        # The SPA should have at least one JS bundle
        resp = client.get("/")
        body = resp.text
        # Find a script src if available
        import re
        scripts = re.findall(r'src="(/assets/[^"]+\.js)"', body)
        if scripts:
            asset_resp = client.get(scripts[0])
            assert asset_resp.status_code == 200
            assert "javascript" in asset_resp.headers.get("content-type", "")


# ── Articles API Shape (Frontend Contract) ──

@requires_session
class TestArticlesAPIShape:
    """Verify the articles API returns the shape the Dashboard React component expects."""

    def test_articles_response_structure(self, client):
        """GET /api/articles returns {articles: [...]} with expected fields per card."""
        resp = client.get("/api/articles")
        assert resp.status_code == 200
        data = resp.json()
        assert "articles" in data
        assert isinstance(data["articles"], list)

        for article in data["articles"]:
            # Fields the Dashboard card component renders
            assert "id" in article, "Missing id field"
            assert "title" in article, "Missing title field"
            assert "state" in article, "Missing state field"
            assert "column" in article, "Missing column field"
            assert "state_label" in article, "Missing state_label field"
            assert "has_seo" in article, "Missing has_seo field"
            assert "image_count" in article, "Missing image_count field"
            assert "valid_image_count" in article, "Missing valid_image_count field"
            assert "keyword" in article, "Missing keyword field"
            assert "created_at" in article, "Missing created_at field"

    def test_articles_state_values_are_valid(self, client):
        """All article states are from the known set."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            assert article["state"] in VALID_STATES, \
                f"Unknown state: {article['state']}"

    def test_articles_column_values_are_valid(self, client):
        """All article columns are from the valid column set."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            assert article["column"] in VALID_COLUMNS, \
                f"Unknown column: {article['column']}"

    def test_articles_state_column_mapping_correct(self, client):
        """Each article's column matches expected mapping from its state."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            expected_col = COLUMN_MAP.get(article["state"])
            if expected_col:
                assert article["column"] == expected_col, \
                    f"State {article['state']} should map to column {expected_col}, got {article['column']}"

    def test_articles_seo_data_shape(self, client):
        """has_seo is boolean, keyword is string."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            assert isinstance(article["has_seo"], bool), \
                f"has_seo should be bool, got {type(article['has_seo'])}"
            assert isinstance(article["keyword"], str), \
                f"keyword should be str, got {type(article['keyword'])}"

    def test_articles_image_data_shape(self, client):
        """image_count and valid_image_count are non-negative integers."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            assert isinstance(article["image_count"], int) and article["image_count"] >= 0, \
                f"image_count should be non-negative int, got {article['image_count']}"
            assert isinstance(article["valid_image_count"], int) and article["valid_image_count"] >= 0, \
                f"valid_image_count should be non-negative int, got {article['valid_image_count']}"
            assert article["valid_image_count"] <= article["image_count"], \
                "valid_image_count should not exceed image_count"

    def test_articles_state_label_is_string(self, client):
        """state_label is a non-empty string for dashboard display."""
        resp = client.get("/api/articles")
        for article in resp.json().get("articles", []):
            assert isinstance(article["state_label"], str) and len(article["state_label"]) > 0, \
                f"state_label should be non-empty string, got '{article['state_label']}'"


# ── Batches API Shape (Status Polling) ──

@requires_session
class TestBatchesAPIShape:
    """Verify batches API for status banner polling (every 30s on Dashboard)."""

    def test_batches_response_structure(self, client):
        """GET /api/batches returns {batches: [...]} with status and counts."""
        resp = client.get("/api/batches")
        assert resp.status_code == 200
        data = resp.json()
        assert "batches" in data
        assert isinstance(data["batches"], list)

        for batch in data["batches"]:
            assert "id" in batch, "Missing batch id"
            assert "status" in batch, "Missing batch status"
            assert "seed_count" in batch, "Missing seed_count"
            assert "created_at" in batch, "Missing created_at"

    def test_batches_status_values(self, client):
        """Batch statuses are from expected set."""
        valid_statuses = {
            "pending_ideation", "ideating", "waiting_approval",
            "processed", "expired", "failed",
        }
        resp = client.get("/api/batches")
        for batch in resp.json().get("batches", []):
            assert batch["status"] in valid_statuses, \
                f"Unknown batch status: {batch['status']}"

    def test_batches_max_10(self, client):
        """GET /api/batches returns at most 10 batches."""
        resp = client.get("/api/batches")
        assert len(resp.json().get("batches", [])) <= 10

    def test_batches_seed_count_non_negative(self, client):
        """seed_count is always a non-negative integer."""
        resp = client.get("/api/batches")
        for batch in resp.json().get("batches", []):
            assert isinstance(batch["seed_count"], int) and batch["seed_count"] >= 0


# ── Pending Ideas API Shape ──

@requires_session
class TestPendingIdeasAPIShape:
    """Verify pending ideas response for the Dashboard notification badge."""

    def test_pending_ideas_response_structure(self, client):
        """GET /api/pending-ideas returns {ideas: [...]}."""
        resp = client.get("/api/pending-ideas")
        assert resp.status_code == 200
        data = resp.json()
        assert "ideas" in data
        assert isinstance(data["ideas"], list)

        for idea in data["ideas"]:
            assert "id" in idea
            assert "title" in idea
            assert "batch_id" in idea
            assert "angle" in idea
            assert "target_keyword" in idea


# ── Settings API Shape ──

@requires_session
class TestSettingsAPIShape:
    """Verify settings API response for the Settings page."""

    def test_settings_response_structure(self, client):
        """GET /api/settings returns expected fields without secrets."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        # Expected fields for settings page
        assert "email" in data
        assert "ghost_key_valid" in data
        assert "subscription_status" in data
        assert "publish_days" in data
        assert "publish_time" in data
        assert "publish_timezone" in data
        # Secrets must be absent
        assert "ghost_admin_api_key" not in data
        assert "password" not in data


# ── Usage API Shape ──

@requires_session
class TestUsageAPIShape:
    """Verify usage API response for the Usage section in Dashboard."""

    def test_usage_response_structure(self, client):
        """GET /api/usage returns timeline format {cycle, current_cycle, articles, previous_cycles}."""
        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "cycle" in data
        assert "current_cycle" in data
        assert "articles" in data
        assert "previous_cycles" in data
        assert isinstance(data["articles"], list)
        assert isinstance(data["previous_cycles"], list)

        assert "start" in data["cycle"]
        assert "end" in data["cycle"]
        assert "days_left" in data["cycle"]
        assert "articles_limit" in data["cycle"]

        assert "published" in data["current_cycle"]
        assert "failed" in data["current_cycle"]
        assert "in_progress" in data["current_cycle"]
        assert "available" in data["current_cycle"]


# ── Auth Enforcement (Frontend-facing) ──

class TestFrontendAuthEnforcement:
    """Verify API endpoints reject unauthenticated requests (important for SPA)."""

    PROTECTED_ENDPOINTS = [
        "/api/articles",
        "/api/batches",
        "/api/pending-ideas",
        "/api/settings",
        "/api/usage",
    ]

    @pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
    def test_api_returns_401_without_cookie(self, anon_client, endpoint):
        """Protected API endpoints return 401 without session cookie."""
        resp = anon_client.get(endpoint)
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    def test_api_returns_401_with_invalid_cookie(self, anon_client):
        """API returns 401 with invalid session cookie."""
        c = httpx.Client(
            base_url=BASE_URL,
            timeout=15.0,
            follow_redirects=True,
            cookies={"session_id": "invalid_session_id"},
        )
        resp = c.get("/api/articles")
        assert resp.status_code == 401

    def test_api_returns_json_on_401(self, anon_client):
        """401 responses are JSON (not HTML redirect) so SPA can handle them."""
        resp = anon_client.get("/api/articles")
        assert resp.status_code == 401
        assert "application/json" in resp.headers.get("content-type", "")


# ── Batch Detail API Shape ──

@requires_session
class TestBatchDetailAPIShape:
    """Verify single batch detail for the batch view."""

    def test_batch_detail_with_valid_id(self, client):
        """If batches exist, fetch first one's detail and verify shape."""
        # First get list to find a batch ID
        resp = client.get("/api/batches")
        batches = resp.json().get("batches", [])
        if not batches:
            pytest.skip("No batches available for detail test")

        batch_id = batches[0]["id"]
        detail_resp = client.get(f"/api/batches/{batch_id}")
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert data["id"] == batch_id
        assert "status" in data
        assert "seeds" in data
        assert isinstance(data["seeds"], list)
        assert "idea_count" in data
        assert isinstance(data["idea_count"], int)

    def test_batch_detail_404_for_invalid_id(self, client):
        """GET /api/batches/{invalid} returns 404."""
        resp = client.get("/api/batches/nonexistent_batch_id_12345")
        assert resp.status_code == 404


# ── Checkpoint Article Preview Shape ──

@requires_session
class TestCheckpointArticleShape:
    """Verify article preview shape for the ArticleReview page."""

    def test_article_preview_with_valid_id(self, client):
        """If articles in review exist, verify preview shape."""
        resp = client.get("/api/articles")
        articles = resp.json().get("articles", [])
        # Find any article to test preview
        if not articles:
            pytest.skip("No articles available for preview test")

        article_id = articles[0]["id"]
        preview_resp = client.get(f"/api/checkpoints/article/{article_id}")
        assert preview_resp.status_code == 200
        data = preview_resp.json()
        assert "article_id" in data
        assert "state" in data
        assert "read_only" in data
        assert isinstance(data["read_only"], bool)
        assert "draft_html" in data
        assert "images" in data
        assert "seo" in data
        assert "review_history" in data
        assert "budget_remaining" in data

    def test_article_preview_404_for_invalid_id(self, client):
        """GET /api/checkpoints/article/{invalid} returns 404."""
        resp = client.get("/api/checkpoints/article/nonexistent_id_12345")
        assert resp.status_code == 404


# ── Response Performance ──

@requires_session
class TestResponsePerformance:
    """Basic performance checks for dashboard-critical endpoints."""

    def test_articles_responds_within_5_seconds(self, client):
        """GET /api/articles responds within 5s (dashboard load time)."""
        import time
        start = time.monotonic()
        resp = client.get("/api/articles")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 5.0, f"Articles endpoint took {elapsed:.2f}s (>5s)"

    def test_batches_responds_within_3_seconds(self, client):
        """GET /api/batches responds within 3s (status polling every 30s)."""
        import time
        start = time.monotonic()
        resp = client.get("/api/batches")
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 3.0, f"Batches endpoint took {elapsed:.2f}s (>3s)"
