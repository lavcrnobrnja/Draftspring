"""Subscription gating helpers."""

from fastapi import HTTPException

ACTIVE_STATUSES = {"active", "trialing"}


def require_active_subscription(user: dict) -> None:
    """Raise 403 if user doesn't have an active or trialing subscription.

    Call this in any route that requires a paid/trial subscription.
    Read-only routes (dashboard, articles list) should NOT be gated.
    Write routes (create batch, approve checkpoints) SHOULD be gated.
    """
    status = (user.get("subscription_status") or "none").lower()
    if status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "subscription_required",
                "message": "An active subscription is required to perform this action.",
                "subscription_status": status,
            },
        )
