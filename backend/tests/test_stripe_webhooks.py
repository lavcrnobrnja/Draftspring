"""Stripe webhook tests — uses stripe.Webhook.construct_event (mocked and real)."""

import json
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.services.email import clear_sent_emails


@pytest.fixture
def app(config):
    return create_app(config)


@pytest_asyncio.fixture
async def webhook_client(app, config):
    """Client for webhook testing."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        clear_sent_emails()
        yield ac


def _build_event(event_type, data_object, event_id="evt_test"):
    """Build a Stripe event dict."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


async def _post_webhook(client, event, mock_construct=True):
    """Post a webhook event. By default mocks construct_event to skip signature."""
    payload = json.dumps(event)
    if mock_construct:
        with patch("stripe.Webhook.construct_event", return_value=event):
            return await client.post(
                "/webhooks/stripe",
                content=payload,
                headers={"stripe-signature": "t=123,v1=mocked", "content-type": "application/json"},
            )
    else:
        return await client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": "t=123,v1=badsig", "content-type": "application/json"},
        )


class TestWebhookSignatureVerification:
    """Stripe signature verification via construct_event."""

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_400(self, webhook_client):
        """Bad signature → 400 Invalid signature."""
        import stripe
        with patch("stripe.Webhook.construct_event", side_effect=stripe.SignatureVerificationError("bad", "sig")):
            resp = await webhook_client.post(
                "/webhooks/stripe",
                content='{}',
                headers={"stripe-signature": "t=1,v1=bad", "content-type": "application/json"},
            )
        assert resp.status_code == 400
        assert "Invalid signature" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_400(self, webhook_client):
        """Malformed payload → 400 Invalid payload."""
        with patch("stripe.Webhook.construct_event", side_effect=ValueError("bad json")):
            resp = await webhook_client.post(
                "/webhooks/stripe",
                content='not json',
                headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"},
            )
        assert resp.status_code == 400
        assert "Invalid payload" in resp.json()["detail"]


class TestCheckoutCompleted:
    """checkout.session.completed webhook handling."""

    @pytest.mark.asyncio
    async def test_sets_customer_and_subscription(self, webhook_client, config):
        """Links Stripe customer + subscription to user."""
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "checkout@test.com")
            user_id = user["id"]

        event = _build_event("checkout.session.completed", {
            "customer": "cus_test_001",
            "subscription": "sub_test_001",
            "client_reference_id": user_id,
        })

        # Mock Subscription.retrieve to return trialing status
        mock_sub = MagicMock()
        mock_sub.status = "trialing"

        with patch("stripe.Subscription.retrieve", return_value=mock_sub):
            resp = await _post_webhook(webhook_client, event)

        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT stripe_customer_id, stripe_subscription_id, subscription_status FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            assert row[0] == "cus_test_001"
            assert row[1] == "sub_test_001"
            assert row[2] == "trialing"  # NOT hardcoded "active"

    @pytest.mark.asyncio
    async def test_uses_metadata_user_id_fallback(self, webhook_client, config):
        """Falls back to metadata.user_id if client_reference_id missing."""
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "meta@test.com")
            user_id = user["id"]

        event = _build_event("checkout.session.completed", {
            "customer": "cus_meta_001",
            "subscription": "sub_meta_001",
            "client_reference_id": None,
            "metadata": {"user_id": user_id},
        })

        mock_sub = MagicMock()
        mock_sub.status = "active"

        with patch("stripe.Subscription.retrieve", return_value=mock_sub):
            resp = await _post_webhook(webhook_client, event)

        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT stripe_customer_id FROM users WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            assert row[0] == "cus_meta_001"

    @pytest.mark.asyncio
    async def test_handles_subscription_retrieve_failure(self, webhook_client, config):
        """If Subscription.retrieve fails, defaults to 'active'."""
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "fail@test.com")
            user_id = user["id"]

        event = _build_event("checkout.session.completed", {
            "customer": "cus_fail_001",
            "subscription": "sub_fail_001",
            "client_reference_id": user_id,
        })

        with patch("stripe.Subscription.retrieve", side_effect=Exception("API error")):
            resp = await _post_webhook(webhook_client, event)

        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT subscription_status FROM users WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            assert row[0] == "active"  # Fallback


class TestSubscriptionCreated:
    """customer.subscription.created webhook handling."""

    @pytest.mark.asyncio
    async def test_sets_trialing_status(self, webhook_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "subcreated@test.com")
            await update_user(db, user["id"], stripe_customer_id="cus_sub_001")

        event = _build_event("customer.subscription.created", {
            "id": "sub_new_001",
            "status": "trialing",
            "customer": "cus_sub_001",
        })

        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT stripe_subscription_id, subscription_status FROM users WHERE id = ?",
                (user["id"],),
            )
            row = await cursor.fetchone()
            assert row[0] == "sub_new_001"
            assert row[1] == "trialing"


class TestSubscriptionUpdated:
    """customer.subscription.updated webhook handling."""

    @pytest.mark.asyncio
    async def test_updates_status_to_active(self, webhook_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "subupdate@test.com")
            await update_user(db, user["id"],
                              stripe_subscription_id="sub_upd_001",
                              subscription_status="trialing")

        event = _build_event("customer.subscription.updated", {
            "id": "sub_upd_001",
            "status": "active",
        })

        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT subscription_status FROM users WHERE id = ?", (user["id"],)
            )
            row = await cursor.fetchone()
            assert row[0] == "active"

    @pytest.mark.asyncio
    async def test_updates_status_to_past_due(self, webhook_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "pastdue@test.com")
            await update_user(db, user["id"],
                              stripe_subscription_id="sub_pd_001",
                              subscription_status="active")

        event = _build_event("customer.subscription.updated", {
            "id": "sub_pd_001",
            "status": "past_due",
        })

        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT subscription_status FROM users WHERE id = ?", (user["id"],)
            )
            row = await cursor.fetchone()
            assert row[0] == "past_due"


class TestSubscriptionDeleted:
    """customer.subscription.deleted webhook handling."""

    @pytest.mark.asyncio
    async def test_sets_canceled_status(self, webhook_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "subdelete@test.com")
            await update_user(db, user["id"],
                              stripe_subscription_id="sub_del_001",
                              subscription_status="active")

        event = _build_event("customer.subscription.deleted", {
            "id": "sub_del_001",
        })

        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT subscription_status FROM users WHERE id = ?", (user["id"],)
            )
            row = await cursor.fetchone()
            assert row[0] == "canceled"


class TestInvoicePaymentFailed:
    """invoice.payment_failed webhook handling."""

    @pytest.mark.asyncio
    async def test_sets_past_due(self, webhook_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            user = await create_user(db, "invoicefail@test.com")
            await update_user(db, user["id"],
                              stripe_subscription_id="sub_inv_001",
                              subscription_status="active")

        event = _build_event("invoice.payment_failed", {
            "subscription": "sub_inv_001",
        })

        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT subscription_status FROM users WHERE id = ?", (user["id"],)
            )
            row = await cursor.fetchone()
            assert row[0] == "past_due"


class TestUnknownEvent:
    """Unknown/unhandled event types."""

    @pytest.mark.asyncio
    async def test_unknown_event_returns_200(self, webhook_client):
        event = _build_event("some.unknown.event", {})
        resp = await _post_webhook(webhook_client, event)
        assert resp.status_code == 200
