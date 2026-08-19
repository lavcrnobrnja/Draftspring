"""Email service. Mock in test mode, real Resend in production.

Design philosophy: Modern, minimal transactional emails inspired by
Notion, Linear, Vercel, Slack. Table-based layout for email client
compatibility. Centered card on light background, prominent CTA button,
muted footer with security note.

EMAIL TEMPLATE RULES (enforced in code — do not override):
─────────────────────────────────────────────────────────
1. Article review emails (CP2):
   - Title appears ONCE in the header card (with optional 120×120 thumbnail).
   - The leading H1 and cover image are STRIPPED from the article body to
     prevent duplication — the draft markdown always starts with both.
   - All <img> tags get max-width:100%;width:100%;height:auto;display:block
     via _postprocess_article_images() to prevent horizontal overflow.
   - Article body wrapped in overflow:hidden + word-break:break-word.
   - Card uses table-layout:fixed at 600px max-width.
   - Cover shown as 120×120 rounded thumbnail beside title, never full-width.

2. All emails:
   - Table-based layout (no CSS grid/flexbox — email clients don't support it).
   - Max card width 480px (simple emails) or 600px (article review).
   - Inline styles only — no <style> blocks except MSO conditionals.
   - Light background (#f4f4f5), white card, green (#16a34a) accent/CTAs.
   - Must render correctly on both desktop and mobile (Gmail, Apple Mail, Outlook).

These rules are baked into the template functions below. If you change
email rendering, re-test on desktop AND mobile before deploying.
"""

import re

import httpx

# In-memory store for test mode
_sent_emails: list[dict] = []


def get_sent_emails() -> list[dict]:
    """Get all sent emails (test mode only)."""
    return _sent_emails


def clear_sent_emails() -> None:
    """Clear sent emails (test mode only)."""
    _sent_emails.clear()


# ── Brand tokens ──────────────────────────────────────────────────────
_BRAND_GREEN = "#16a34a"
_BRAND_GREEN_HOVER = "#15803d"
_BG_COLOR = "#f4f4f5"
_CARD_BG = "#ffffff"
_TEXT_PRIMARY = "#18181b"
_TEXT_SECONDARY = "#71717a"
_TEXT_MUTED = "#a1a1aa"
_BORDER_COLOR = "#e4e4e7"


def _base_template(
    preheader: str,
    heading: str,
    body_html: str,
    cta_url: str,
    cta_label: str,
    footer_html: str,
) -> str:
    """Render the shared email template. Table-based for max compatibility."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{heading}</title>
<!--[if mso]>
<style>table,td {{font-family:Arial,sans-serif;}}</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:{_BG_COLOR};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<!-- Preheader (hidden text for inbox preview) -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheader}</div>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:{_BG_COLOR};">
<tr><td align="center" style="padding:40px 16px;">

  <!-- Card -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480" style="max-width:480px;width:100%;background-color:{_CARD_BG};border-radius:12px;border:1px solid {_BORDER_COLOR};box-shadow:0 1px 3px rgba(0,0,0,0.04);">

    <!-- Logo -->
    <tr><td align="center" style="padding:32px 40px 0 40px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-size:24px;font-weight:700;color:{_BRAND_GREEN};letter-spacing:-0.5px;">
            &#x1f331; DraftSpring
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- Heading -->
    <tr><td style="padding:24px 40px 0 40px;">
      <h1 style="margin:0;font-size:20px;font-weight:600;color:{_TEXT_PRIMARY};text-align:center;line-height:1.4;">
        {heading}
      </h1>
    </td></tr>

    <!-- Body -->
    <tr><td style="padding:16px 40px 0 40px;font-size:15px;line-height:1.6;color:{_TEXT_SECONDARY};text-align:center;">
      {body_html}
    </td></tr>

    <!-- CTA Button -->
    <tr><td align="center" style="padding:28px 40px 0 40px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0">
        <tr><td align="center" style="border-radius:8px;background-color:{_BRAND_GREEN};">
          <a href="{cta_url}" target="_blank" style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;mso-padding-alt:0;">
            <!--[if mso]><i style="mso-text-raise:21pt;">&nbsp;</i><![endif]-->
            {cta_label}
            <!--[if mso]><i style="mso-text-raise:21pt;">&nbsp;</i><![endif]-->
          </a>
        </td></tr>
      </table>
    </td></tr>

    <!-- Alternate link -->
    <tr><td style="padding:16px 40px 0 40px;text-align:center;">
      <p style="margin:0;font-size:12px;color:{_TEXT_MUTED};word-break:break-all;">
        Or copy this link: <a href="{cta_url}" style="color:{_TEXT_MUTED};">{cta_url}</a>
      </p>
    </td></tr>

    <!-- Divider -->
    <tr><td style="padding:28px 40px 0 40px;">
      <hr style="border:none;border-top:1px solid {_BORDER_COLOR};margin:0;">
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 40px 32px 40px;text-align:center;font-size:12px;line-height:1.5;color:{_TEXT_MUTED};">
      {footer_html}
    </td></tr>

  </table>
  <!-- /Card -->

  <!-- Sub-footer -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480" style="max-width:480px;width:100%;">
    <tr><td style="padding:20px 40px 0 40px;text-align:center;font-size:11px;color:{_TEXT_MUTED};">
      DraftSpring &mdash; Content automation for your Ghost blog<br>
      <a href="https://draftspring.io" style="color:{_TEXT_MUTED};text-decoration:underline;">draftspring.io</a>
    </td></tr>
  </table>

</td></tr>
</table>
</body>
</html>"""


# ── Send helper ───────────────────────────────────────────────────────

async def _send_via_resend(config, to: str, subject: str, html: str) -> bool:
    """Send email via Resend API."""
    import structlog
    logger = structlog.get_logger()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {config.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"{config.EMAIL_FROM_NAME} <{config.EMAIL_FROM_ADDRESS}>",
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            if resp.status_code != 200:
                logger.error("resend_email_failed", to=to, subject=subject, status=resp.status_code, body=resp.text[:200])
            return resp.status_code == 200
    except Exception as e:
        logger.error("resend_email_exception", to=to, subject=subject, error=str(e))
        return False


# ── Public email functions ────────────────────────────────────────────

async def send_magic_link_email(
    config,
    to: str,
    token: str,
    purpose: str,
    reference_id: str | None = None,
) -> bool:
    """Send a magic link email. Returns True on success."""

    url = f"{config.APP_BASE_URL}/auth/verify?token={token}"

    if purpose == "login":
        subject = "Sign in to DraftSpring"
        html = _base_template(
            preheader="Your sign-in link for DraftSpring — expires in 15 minutes",
            heading="Sign in to DraftSpring",
            body_html="Click the button below to sign in. No password needed.",
            cta_url=url,
            cta_label="Sign In",
            footer_html="This link expires in <strong>15 minutes</strong> and can only be used once.<br>If you didn't request this, you can safely ignore this email.",
        )
    elif purpose == "checkpoint_1":
        subject = "Your ideas are ready for review ✨"
        html = _base_template(
            preheader="DraftSpring generated article ideas from your seeds — review and approve them",
            heading="Your Ideas Are Ready",
            body_html="We've generated article ideas from your seeds.<br>Review them and pick the ones you'd like us to write.",
            cta_url=url,
            cta_label="Review Ideas",
            footer_html="This link expires in <strong>7 days</strong>.<br>You can revisit your ideas anytime from your dashboard.",
        )
    elif purpose == "admin":
        subject = "DraftSpring Admin Access"
        html = _base_template(
            preheader="Your admin sign-in link for DraftSpring — expires in 15 minutes",
            heading="Admin Sign In",
            body_html="Click the button below to access the DraftSpring admin panel.",
            cta_url=url,
            cta_label="Access Admin",
            footer_html="This link expires in <strong>15 minutes</strong> and can only be used once.<br>If you didn't request this, you can safely ignore this email.",
        )
    elif purpose == "checkpoint_2":
        subject = "Your article is ready for review 📝"
        html = _base_template(
            preheader="Your DraftSpring article is ready — review, approve, or request revisions",
            heading="Your Article Is Ready",
            body_html="Your article has been written, edited, and polished.<br>Review it and either approve for publishing or request changes.",
            cta_url=url,
            cta_label="Review Article",
            footer_html="You can approve, request revisions, or archive the article.<br>Take your time — the link doesn't expire.",
        )
    else:
        subject = "DraftSpring"
        html = _base_template(
            preheader="Action needed on DraftSpring",
            heading="Action Needed",
            body_html="Click below to continue.",
            cta_url=url,
            cta_label="Continue",
            footer_html="If you didn't expect this email, you can safely ignore it.",
        )

    if config.APP_ENV in ("test", "development"):
        _sent_emails.append({
            "to": to,
            "subject": subject,
            "token": token,
            "purpose": purpose,
            "reference_id": reference_id,
            "url": url,
        })
        if config.APP_ENV == "development":
            import structlog
            structlog.get_logger().info("magic_link_email", to=to, purpose=purpose, token=token[:12] + "...", url=url)
        return True

    return await _send_via_resend(config, to, subject, html)


async def send_revision_confirmation_email(
    config,
    to: str,
    article_title: str,
) -> bool:
    """Send revision confirmation email after user submits feedback."""
    subject = f"Revision received: {article_title}"
    html = _base_template(
        preheader=f"We're revising \"{article_title}\" based on your feedback",
        heading="Revision In Progress",
        body_html=f"We've received your notes for <strong>{article_title}</strong>.<br>We're revising the article now — you'll get a new review link when it's ready.",
        cta_url=f"{config.APP_BASE_URL}/dashboard",
        cta_label="Go to Dashboard",
        footer_html="No action needed right now. We'll email you when the revision is complete.",
    )

    if config.APP_ENV in ("test", "development"):
        _sent_emails.append({
            "to": to,
            "subject": subject,
            "purpose": "revision_confirmation",
            "article_title": article_title,
        })
        return True

    return await _send_via_resend(config, to, subject, html)


async def send_publish_notification_email(
    config,
    to: str,
    article_title: str,
    article_url: str,
) -> bool:
    """Send notification email when article is published."""
    subject = f"Published: {article_title} 🎉"
    html = _base_template(
        preheader=f"\"{article_title}\" is now live on your Ghost blog",
        heading="Your Article Is Live!",
        body_html=f"<strong>{article_title}</strong> has been published to your Ghost blog.",
        cta_url=article_url,
        cta_label="View Article",
        footer_html="The article is live and indexed. You can always manage it from your Ghost dashboard.",
    )

    if config.APP_ENV in ("test", "development"):
        _sent_emails.append({
            "to": to,
            "subject": subject,
            "purpose": "publish_notification",
            "article_title": article_title,
            "article_url": article_url,
        })
        return True

    return await _send_via_resend(config, to, subject, html)


def _postprocess_article_images(html: str) -> str:
    """Post-process article HTML to constrain all <img> tags.

    Adds max-width:100%;height:auto;display:block; to any <img> that
    doesn't already have those styles. This catches images from markdown
    conversion and any other source.
    """
    import re

    def fix_img(match):
        tag = match.group(0)
        # If it already has max-width in style, leave it alone
        if "max-width" in tag:
            return tag
        # If it has a style attribute, append to it
        if 'style="' in tag:
            return tag.replace('style="', 'style="max-width:100%;width:100%;height:auto;display:block;')
        # No style attribute — add one before the closing
        return tag.replace("<img ", '<img style="max-width:100%;width:100%;height:auto;display:block;" ')

    return re.sub(r'<img\s[^>]+>', fix_img, html, flags=re.IGNORECASE)


async def send_article_review_email(
    config,
    to: str,
    article_title: str,
    article_html: str,
    cover_image_url: str | None,
    magic_link_token: str,
    next_publish_date_formatted: str,
) -> bool:
    """Send full article review email for CP2. The user reads the article in their inbox."""
    from html import escape as html_escape
    from urllib.parse import quote

    base_url = config.APP_BASE_URL
    verify_url = f"{base_url}/auth/verify?token={quote(magic_link_token, safe='')}"
    approve_url = f"{verify_url}&action=approve"

    # Sanitize title for safe HTML embedding
    safe_title = html_escape(article_title, quote=True)

    # Validate cover image URL (only allow http/https)
    if cover_image_url and not cover_image_url.startswith(("https://", "http://")):
        cover_image_url = None

    subject = f"\U0001f4dd Your article is ready: {article_title}"  # Subject is plain text, no escaping needed
    preheader = "Read, approve, or request revisions \u2014 right from your inbox"

    # ── DEDUP: strip title + cover from article body ──────────────
    # The markdown draft always starts with "# Title\n![cover](url)".
    # The email header card already shows the title + thumbnail, so
    # we strip both from the body to avoid showing them twice.
    # See EMAIL TEMPLATE RULES in module docstring.
    article_html = re.sub(
        r'^\s*<h1[^>]*>.*?</h1>\s*',
        '',
        article_html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Strip duplicate cover image: remove the first <img> or <p><img></p> if its src
    # matches the cover_image_url (already shown as thumbnail in header)
    if cover_image_url:
        _esc_cover = re.escape(cover_image_url)
        # Try <p><img src="cover..."></p> first (markdown wraps images in <p>)
        article_html, n = re.subn(
            rf'<p>\s*<img\s[^>]*src="{_esc_cover}"[^>]*/?\s*>\s*</p>',
            '',
            article_html,
            count=1,
            flags=re.IGNORECASE,
        )
        if n == 0:
            # Bare <img> not wrapped in <p>
            article_html = re.sub(
                rf'<img\s[^>]*src="{_esc_cover}"[^>]*/?\s*>',
                '',
                article_html,
                count=1,
                flags=re.IGNORECASE,
            )

    # Post-process article HTML: constrain all images
    article_html = _postprocess_article_images(article_html)

    # Build cover image block — small thumbnail beside title, not full-width hero
    cover_block = ""
    if cover_image_url:
        safe_cover_url = html_escape(cover_image_url, quote=True)
        cover_block = f"""\
          <td width="120" style="width:120px;padding-right:20px;vertical-align:top;">
            <img src="{safe_cover_url}" alt="Cover" style="display:block;width:120px;max-width:120px;height:120px;object-fit:cover;border-radius:10px;" />
          </td>"""

    # Title block adapts based on whether cover image exists
    if cover_block:
        title_section = f"""\
    <!-- Title + Thumbnail -->
    <tr><td style="padding:28px 40px 0 40px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
{cover_block}
          <td style="vertical-align:top;">
            <h1 style="margin:0;font-size:24px;font-weight:700;color:{_TEXT_PRIMARY};line-height:1.3;">
              {safe_title}
            </h1>
            <p style="margin:8px 0 0 0;font-size:13px;color:{_TEXT_MUTED};">
              Scheduled for {html_escape(next_publish_date_formatted)}
            </p>
          </td>
        </tr>
      </table>
    </td></tr>"""
    else:
        title_section = f"""\
    <!-- Title (no cover) -->
    <tr><td style="padding:28px 40px 0 40px;">
      <h1 style="margin:0;font-size:24px;font-weight:700;color:{_TEXT_PRIMARY};line-height:1.3;">
        {safe_title}
      </h1>
      <p style="margin:8px 0 0 0;font-size:13px;color:{_TEXT_MUTED};">
        Scheduled for {html_escape(next_publish_date_formatted)}
      </p>
    </td></tr>"""

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{safe_title}</title>
<!--[if mso]>
<style>table,td {{font-family:Arial,sans-serif;}}</style>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:{_BG_COLOR};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<!-- Preheader -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheader}</div>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:{_BG_COLOR};">
<tr><td align="center" style="padding:40px 16px;">

  <!-- Card -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;background-color:{_CARD_BG};border-radius:12px;border:1px solid {_BORDER_COLOR};box-shadow:0 1px 3px rgba(0,0,0,0.04);table-layout:fixed;">

    <!-- Logo + View on Web link -->
    <tr><td align="center" style="padding:32px 40px 16px 40px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <td style="font-size:24px;font-weight:700;color:{_BRAND_GREEN};letter-spacing:-0.5px;">
            &#x1f331; DraftSpring
          </td>
          <td align="right" style="vertical-align:middle;">
            <a href="{verify_url}" target="_blank" style="font-size:13px;color:{_TEXT_MUTED};text-decoration:underline;">
              View on web &#x2197;
            </a>
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- Thin accent bar -->
    <tr><td style="padding:0 40px;">
      <div style="height:3px;background:linear-gradient(90deg,{_BRAND_GREEN},#22d3ee);border-radius:2px;"></div>
    </td></tr>

{title_section}

    <!-- Divider before article -->
    <tr><td style="padding:20px 40px 0 40px;">
      <hr style="border:none;border-top:1px solid {_BORDER_COLOR};margin:0;">
    </td></tr>

    <!-- Article Body -->
    <tr><td style="padding:20px 24px 0 24px;font-size:16px;line-height:1.7;color:{_TEXT_PRIMARY};overflow:hidden;word-break:break-word;">
      <div style="overflow:hidden;word-wrap:break-word;word-break:break-word;">
        {article_html}
      </div>
    </td></tr>

    <!-- Divider -->
    <tr><td style="padding:32px 40px 0 40px;">
      <hr style="border:none;border-top:1px solid {_BORDER_COLOR};margin:0;">
    </td></tr>

    <!-- Action label -->
    <tr><td align="center" style="padding:24px 40px 0 40px;">
      <p style="margin:0;font-size:14px;font-weight:600;color:{_TEXT_PRIMARY};">
        How does this look?
      </p>
    </td></tr>

    <!-- Action buttons: side by side -->
    <tr><td style="padding:16px 40px 0 40px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
        <tr>
          <!-- Request Revision (left) -->
          <td align="center" width="48%" style="vertical-align:middle;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr><td align="center" style="border-radius:8px;border:2px solid {_BORDER_COLOR};background-color:{_CARD_BG};">
                <a href="{verify_url}" target="_blank" style="display:inline-block;padding:12px 24px;font-size:14px;font-weight:600;color:{_TEXT_SECONDARY};text-decoration:none;border-radius:8px;white-space:nowrap;">
                  Request Revision
                </a>
              </td></tr>
            </table>
          </td>
          <!-- Spacer -->
          <td width="4%">&nbsp;</td>
          <!-- Approve & Publish (right) -->
          <td align="center" width="48%" style="vertical-align:middle;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr><td align="center" style="border-radius:8px;background-color:{_BRAND_GREEN};">
                <a href="{approve_url}" target="_blank" style="display:inline-block;padding:12px 24px;font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;white-space:nowrap;">
                  &#x2713; Approve &amp; Publish
                </a>
              </td></tr>
            </table>
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- Divider -->
    <tr><td style="padding:24px 40px 0 40px;">
      <hr style="border:none;border-top:1px solid {_BORDER_COLOR};margin:0;">
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 40px 32px 40px;text-align:center;font-size:12px;line-height:1.5;color:{_TEXT_MUTED};">
      You can approve, request revisions, or archive the article.<br>Take your time \u2014 the link doesn\u2019t expire.
    </td></tr>

  </table>
  <!-- /Card -->

  <!-- Sub-footer -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;">
    <tr><td style="padding:20px 40px 0 40px;text-align:center;font-size:11px;color:{_TEXT_MUTED};">
      DraftSpring &mdash; Content automation for your Ghost blog<br>
      <a href="https://draftspring.io" style="color:{_TEXT_MUTED};text-decoration:underline;">draftspring.io</a>
    </td></tr>
  </table>

</td></tr>
</table>
</body>
</html>"""

    if config.APP_ENV in ("test", "development"):
        _sent_emails.append({
            "to": to,
            "subject": subject,
            "purpose": "article_review",
            "article_title": article_title,
            "html": html,
            "token": magic_link_token,
            "verify_url": verify_url,
            "approve_url": approve_url,
            "next_publish_date_formatted": next_publish_date_formatted,
        })
        if config.APP_ENV == "development":
            import structlog
            structlog.get_logger().info(
                "article_review_email", to=to, article_title=article_title,
                token=magic_link_token[:12] + "...",
            )
        return True

    return await _send_via_resend(config, to, subject, html)


async def send_archive_notification_email(
    config,
    to: str,
    article_title: str,
) -> bool:
    """Send notification email when an article is archived by admin."""
    subject = f"Article archived: {article_title}"
    html = _base_template(
        preheader=f"\"{article_title}\" has been archived",
        heading="Article Archived",
        body_html=f"<strong>{article_title}</strong> has been archived and will not be published.",
        cta_url=f"{config.APP_BASE_URL}/dashboard",
        cta_label="Go to Dashboard",
        footer_html="If you believe this was done in error, please contact support.",
    )

    if config.APP_ENV in ("test", "development"):
        _sent_emails.append({
            "to": to,
            "subject": subject,
            "purpose": "archive_notification",
            "article_title": article_title,
        })
        return True

    return await _send_via_resend(config, to, subject, html)
