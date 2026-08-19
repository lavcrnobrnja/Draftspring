"""Stripe webhook handler."""

import logging

import stripe
from fastapi import APIRouter, Request, HTTPException

from app.database import get_connection
from app.models.user import update_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events.

    Uses stripe.Webhook.construct_event() for signature verification
    (handles timestamp tolerance, multiple signatures, replay attacks).
    """
    config = request.app.state.config
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    # Use Stripe's official signature verification
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, config.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data_object = event["data"]["object"]

    logger.info("Stripe webhook: %s", event_type)

    async with get_connection(config.DATABASE_PATH) as db:

        if event_type == "checkout.session.completed":
            # Checkout completed — link Stripe customer + subscription to our user.
            # Don't hardcode status: the subscription might be trialing or active.
            # We read the actual subscription status from Stripe.
            user_id = data_object.get("client_reference_id")
            if not user_id:
                user_id = data_object.get("metadata", {}).get("user_id")

            if user_id:
                sub_id = data_object.get("subscription")
                customer_id = data_object.get("customer")

                # Fetch the actual subscription status from Stripe
                sub_status = "active"
                if sub_id:
                    try:
                        stripe.api_key = config.STRIPE_SECRET_KEY
                        sub = stripe.Subscription.retrieve(sub_id)
                        sub_status = sub.status  # "trialing", "active", etc.
                    except Exception as e:
                        logger.warning("Could not fetch subscription %s: %s", sub_id, e)

                await update_user(
                    db, user_id,
                    stripe_customer_id=customer_id or "",
                    stripe_subscription_id=sub_id or "",
                    subscription_status=sub_status,
                )
                logger.info("checkout.session.completed: user=%s status=%s", user_id, sub_status)

        elif event_type == "customer.subscription.created":
            # New subscription created — may be trialing or active.
            sub_id = data_object.get("id", "")
            status = data_object.get("status", "active")
            customer_id = data_object.get("customer", "")
            if customer_id:
                cursor = await db.execute(
                    "SELECT id FROM users WHERE stripe_customer_id = ?",
                    (customer_id,),
                )
                row = await cursor.fetchone()
                if row:
                    await update_user(
                        db, row[0],
                        stripe_subscription_id=sub_id,
                        subscription_status=status,
                    )
                    logger.info("subscription.created: user=%s status=%s", row[0], status)

        elif event_type == "customer.subscription.updated":
            # Status change — trialing→active, active→past_due, etc.
            sub_id = data_object.get("id", "")
            status = data_object.get("status", "")
            if sub_id and status:
                cursor = await db.execute(
                    "SELECT id FROM users WHERE stripe_subscription_id = ?",
                    (sub_id,),
                )
                row = await cursor.fetchone()
                if row:
                    await update_user(db, row[0], subscription_status=status)
                    logger.info("subscription.updated: user=%s status=%s", row[0], status)

        elif event_type == "customer.subscription.deleted":
            # Subscription canceled/ended
            sub_id = data_object.get("id", "")
            if sub_id:
                cursor = await db.execute(
                    "SELECT id FROM users WHERE stripe_subscription_id = ?",
                    (sub_id,),
                )
                row = await cursor.fetchone()
                if row:
                    await update_user(db, row[0], subscription_status="canceled")
                    logger.info("subscription.deleted: user=%s", row[0])

        elif event_type == "invoice.payment_failed":
            # Payment failed — mark as past_due so gating kicks in
            sub_id = data_object.get("subscription", "")
            if sub_id:
                cursor = await db.execute(
                    "SELECT id FROM users WHERE stripe_subscription_id = ?",
                    (sub_id,),
                )
                row = await cursor.fetchone()
                if row:
                    await update_user(db, row[0], subscription_status="past_due")
                    logger.info("invoice.payment_failed: user=%s → past_due", row[0])

    return {"received": True}
