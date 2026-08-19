"""Tests for Stripe Checkout, Billing Portal, and Landing page."""

import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.config import Config
from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.middleware.auth_middleware import create_session


@pytest.fixture
def checkout_config(config):
    """Config with Stripe keys set."""
    config.STRIPE_SECRET_KEY = "sk_test_fake_key"
    config.STRIPE_PRICE_ID = "price_test_123"
    return config


@pytest.fixture
def app(checkout_config):
    return create_app(checkout_config)


@pytest_asyncio.fixture
async def auth_client(app, checkout_config):
    """Authenticated client."""
    async with get_connection(checkout_config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, "user@test.com")
        session_id = await create_session(db, user["id"], "full")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def stripe_customer_client(app, checkout_config):
    """Authenticated client with Stripe customer ID."""
    async with get_connection(checkout_config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, "paying@test.com")
        await update_user(db, user["id"], stripe_customer_id="cus_test_123")
        session_id = await create_session(db, user["id"], "full")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def unauthed_client(app):
    """Unauthenticated client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestCheckoutSession:
    """GET /api/checkout/session creates Stripe Checkout session."""

    @pytest.mark.asyncio
    async def test_creates_checkout_url(self, auth_client):
        """Checkout session returns a valid URL (mocked)."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/c/pay_test_123"
        mock_session.id = "cs_test_123"

        with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
            resp = await auth_client.get("/api/checkout/session")
            assert resp.status_code == 200
            data = resp.json()
            assert data["url"] == "https://checkout.stripe.com/c/pay_test_123"
            assert data["session_id"] == "cs_test_123"

            # Verify correct params
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["mode"] == "subscription"
            assert call_kwargs["client_reference_id"] is not None
            assert call_kwargs["customer_email"] == "user@test.com"

    @pytest.mark.asyncio
    async def test_uses_existing_customer_id(self, stripe_customer_client):
        """If user has Stripe customer ID, uses it instead of email."""
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/c/pay_test_456"
        mock_session.id = "cs_test_456"

        with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
            resp = await stripe_customer_client.get("/api/checkout/session")
            assert resp.status_code == 200

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["customer"] == "cus_test_123"
            assert "customer_email" not in call_kwargs

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, unauthed_client):
        resp = await unauthed_client.get("/api/checkout/session")
        assert resp.status_code == 401


class TestBillingPortal:
    """POST /api/billing/portal creates Stripe Customer Portal session."""

    @pytest.mark.asyncio
    async def test_portal_returns_url(self, stripe_customer_client):
        """Portal session returns a valid URL."""
        mock_portal = MagicMock()
        mock_portal.url = "https://billing.stripe.com/p/session_test_123"

        with patch("stripe.billing_portal.Session.create", return_value=mock_portal):
            resp = await stripe_customer_client.post("/api/billing/portal")
            assert resp.status_code == 200
            data = resp.json()
            assert data["url"] == "https://billing.stripe.com/p/session_test_123"

    @pytest.mark.asyncio
    async def test_no_customer_id_returns_400(self, auth_client):
        """User without Stripe customer ID gets 400."""
        resp = await auth_client.post("/api/billing/portal")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, unauthed_client):
        resp = await unauthed_client.post("/api/billing/portal")
        assert resp.status_code == 401


class TestLandingPage:
    """GET / serves landing or redirects to dashboard."""

    @pytest.mark.asyncio
    async def test_landing_renders_for_unauthenticated(self, unauthed_client):
        """Unauthenticated visitors see landing page."""
        resp = await unauthed_client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "DraftSpring" in resp.text
        assert "$9" in resp.text
        assert "Start Free Trial" in resp.text

    @pytest.mark.asyncio
    async def test_authenticated_redirects_to_dashboard(self, auth_client):
        """Authenticated users redirect to /dashboard."""
        resp = await auth_client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert "/dashboard" in resp.headers.get("location", "")
