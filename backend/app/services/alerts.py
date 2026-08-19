"""Admin alert emails for failure conditions."""

from app.services.email import _send_via_resend, _sent_emails


async def send_admin_alert(config, subject: str, body: str) -> bool:
    """Send an alert email to all admin emails.
    
    In test mode, captures to the email store.
    In production, sends via Resend to each admin.
    """
    admin_emails = [
        e.strip() for e in (config.ADMIN_EMAILS or "").split(",") if e.strip()
    ]

    if not admin_emails:
        return False

    html = f"<div style='font-family:sans-serif'><h2>⚠️ DraftSpring Alert</h2><p><strong>{subject}</strong></p><p>{body}</p></div>"

    if config.APP_ENV == "test":
        _sent_emails.append({
            "to": admin_emails[0],
            "subject": f"[DraftSpring Alert] {subject}",
            "purpose": "admin_alert",
            "html": html,
            "body": body,
        })
        return True

    success = True
    for email in admin_emails:
        result = await _send_via_resend(
            config, email, f"[DraftSpring Alert] {subject}", html
        )
        if not result:
            success = False

    return success


async def alert_article_failed(config, article_id: str, title: str, reason: str):
    """Alert admins when an article fails."""
    await send_admin_alert(
        config,
        f"Article Failed: {title}",
        f"Article <code>{article_id}</code> failed with reason: {reason}",
    )


async def alert_worker_down(config, worker_id: str, error: str):
    """Alert admins when a worker goes down."""
    await send_admin_alert(
        config,
        f"Worker Down: {worker_id}",
        f"Worker <code>{worker_id}</code> encountered an error: {error}",
    )


async def alert_cost_ceiling(config, article_id: str, cost_cents: int, ceiling_cents: int):
    """Alert admins when cost ceiling is exceeded."""
    await send_admin_alert(
        config,
        f"Cost Ceiling Exceeded",
        f"Article <code>{article_id}</code> cost {cost_cents}¢ exceeds ceiling of {ceiling_cents}¢.",
    )


async def alert_ghost_key_failure(config, user_email: str, error: str):
    """Alert admins when a Ghost key fails."""
    await send_admin_alert(
        config,
        f"Ghost Key Failure: {user_email}",
        f"Ghost API key for {user_email} failed: {error}",
    )
