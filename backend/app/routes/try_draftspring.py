"""Try DraftSpring — free demo tool endpoints.

Users enter a Ghost blog URL + email, and we:
1. Scan their blog
2. Analyze writing style
3. Generate a custom article in their voice
4. Email it + show a preview on-page
"""

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from app.config import Config
from app.database import get_connection
from app.services.blog_analyzer import BlogAnalyzer, BlogAnalyzerError
from app.utils.url_validation import validate_url, is_private_ip, check_ghost, UA

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

# ── Rate limiter (5 req/hr/IP) ────────────────────────────────────────

class _RateLimiter:
    def __init__(self, max_requests: int = 5, window_seconds: int = 3600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        self._requests[ip] = [t for t in self._requests[ip] if t > window_start]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True

    def clear(self):
        self._requests.clear()


_rate_limiter = _RateLimiter()

# ── Request model ─────────────────────────────────────────────────────

async def _verify_turnstile(secret_key: str, token: str, ip: str) -> bool:
    """Verify Cloudflare Turnstile token."""
    if not secret_key or not token:
        return not secret_key  # Skip in dev (no key configured), fail if key exists but no token
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret_key, "response": token, "remoteip": ip},
        )
        result = resp.json()
        return result.get("success", False)


class TryDraftSpringRequest(BaseModel):
    url: str
    email: EmailStr
    cf_turnstile_token: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/try-draftspring")
async def try_draftspring(req: TryDraftSpringRequest, request: Request):
    """Submit a blog URL + email to generate a sample article."""
    config: Config = request.app.state.config

    # Turnstile captcha verification
    if config.TURNSTILE_SECRET_KEY:
        ip = request.client.host if request.client else "unknown"
        if not await _verify_turnstile(config.TURNSTILE_SECRET_KEY, req.cf_turnstile_token, ip):
            return JSONResponse(status_code=400, content={"error": "Captcha verification failed. Please try again."})

    # Rate limit
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.is_allowed(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please try again in a few minutes."},
        )

    # Validate URL
    clean_url = validate_url(req.url)
    if not clean_url:
        return JSONResponse(
            status_code=400,
            content={"detail": "Please enter a publicly accessible URL."},
        )

    # Check email uniqueness
    async with get_connection(config.DATABASE_PATH) as db:
        existing = await db.execute_fetchall(
            "SELECT id, task_status FROM demo_articles WHERE email = ?",
            (req.email,),
        )
        if existing:
            record = existing[0]
            status = record["task_status"]
            if status == "failed":
                # Previous attempt errored out — delete the old record and allow retry
                await db.execute(
                    "DELETE FROM demo_articles WHERE id = ?",
                    (record["id"],),
                )
                await db.commit()
            elif status == "complete":
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "You've already generated a sample article. Check your inbox!",
                        "task_id": record["id"],
                        "status": status,
                    },
                )
            else:
                # pending, scanning, analyzing, drafting, ideating, imaging, sending, etc.
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "Your article is already being generated. Please wait for it to complete.",
                        "task_id": record["id"],
                        "status": status,
                    },
                )

    # Create task
    task_id = str(uuid.uuid4())

    async with get_connection(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO demo_articles (id, email, task_status, stage_message)
               VALUES (?, ?, 'pending', 'Starting...')""",
            (task_id, req.email),
        )
        await db.commit()

    # Kick off background task
    asyncio.create_task(
        _run_pipeline(config, task_id, clean_url, req.email)
    )

    logger.info("try_draftspring_started", task_id=task_id, url=clean_url)
    return {"task_id": task_id}


@router.get("/try-draftspring/{task_id}/status")
async def try_draftspring_status(task_id: str, request: Request):
    """Poll task status."""
    config: Config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            """SELECT task_status, stage_message, idea_title, article_preview,
                      cover_image_url, error_message
               FROM demo_articles WHERE id = ?""",
            (task_id,),
        )
        if not rows:
            return JSONResponse(status_code=404, content={"detail": "Task not found."})

        row = rows[0]
        result = None
        if row["task_status"] == "complete":
            result = {
                "title": row["idea_title"],
                "preview": row["article_preview"],
                "cover_image_url": row["cover_image_url"],
            }

        return {
            "status": row["task_status"],
            "stage_message": row["stage_message"],
            "result": result,
            "error": row["error_message"] if row["task_status"] == "failed" else None,
        }


# ── Background pipeline ──────────────────────────────────────────────

async def _update_status(
    config: Config, task_id: str, status: str, stage_message: str, **extra
):
    """Update task status in DB."""
    set_clauses = ["task_status = ?", "stage_message = ?"]
    params = [status, stage_message]
    for key, value in extra.items():
        set_clauses.append(f"{key} = ?")
        params.append(value)
    params.append(task_id)

    async with get_connection(config.DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE demo_articles SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        await db.commit()


async def _run_pipeline(config: Config, task_id: str, url: str, email: str):
    """Full pipeline: scan → analyze → ideate → outline → draft → humanize → image → email."""
    try:
        # Step 1: Scanning — validate Ghost
        await _update_status(config, task_id, "scanning", "Scanning your blog...")
        is_ghost = await _validate_ghost(url)
        if not is_ghost:
            await _update_status(
                config,
                task_id,
                "failed",
                "Not a Ghost blog",
                error_message="This doesn't appear to be a Ghost blog. DraftSpring currently supports Ghost blogs only.",
            )
            return

        # Step 2: Analyzing — blog profile
        await _update_status(config, task_id, "analyzing", "Analyzing your writing style...")
        analyzer = BlogAnalyzer(config)
        try:
            profile = await analyzer.get_or_analyze(url)
        except BlogAnalyzerError as e:
            await _update_status(
                config, task_id, "failed", "Analysis failed", error_message=str(e)
            )
            return

        # Update blog_profile_id now that we have a real one
        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                "UPDATE demo_articles SET blog_profile_id = ? WHERE id = ?",
                (profile.id, task_id),
            )
            await db.commit()

        # Step 3: Ideating — generate ideas, pick top one
        await _update_status(config, task_id, "ideating", "Choosing a topic...")
        try:
            ideas = await analyzer.generate_ideas(profile, count=3)
        except BlogAnalyzerError as e:
            await _update_status(
                config, task_id, "failed", "Ideation failed", error_message=str(e)
            )
            return

        if not ideas:
            await _update_status(
                config,
                task_id,
                "failed",
                "No ideas generated",
                error_message="We couldn't generate article ideas for your blog. Please try again.",
            )
            return

        idea = ideas[0]
        await _update_status(
            config,
            task_id,
            "ideating",
            f"Writing about: {idea.title}",
            idea_title=idea.title,
            idea_angle=idea.angle,
        )

        # Step 4: Outlining
        await _update_status(config, task_id, "drafting", "Writing your article...")
        from app.llm.live import LiveLLM

        llm = LiveLLM(config)
        try:
            outline = await llm.generate_outline(
                idea={
                    "title": idea.title,
                    "target_keyword": idea.angle,
                    "search_intent": idea.reasoning,
                },
                blog_context={
                    "brand_voice": profile.style_guide,
                    "default_word_count": 1200,
                },
                target_word_count=1200,
            )

            # Step 5: Drafting
            seo_meta = outline.get("seo_block", {})
            draft = await llm.draft_article(
                outline=outline,
                seo_meta=seo_meta,
                brand_voice=profile.style_guide,
                focus_keyword=seo_meta.get("focus_keyword", idea.angle),
                article_title=idea.title,
                target_word_count=1200,
            )

            # Step 6: Humanizing
            humanized = await llm.humanize(
                draft_md=draft,
                brand_voice=profile.style_guide,
                focus_keyword=seo_meta.get("focus_keyword", idea.angle),
                article_title=idea.title,
            )
        except Exception as e:
            logger.error("try_draftspring_llm_error", task_id=task_id, error=str(e))
            await _update_status(
                config,
                task_id,
                "failed",
                "Article generation failed",
                error_message="Something went wrong generating your article. Please try again.",
            )
            await llm.close()
            return

        # Step 7: Cover image
        await _update_status(config, task_id, "imaging", "Generating cover image...")
        cover_image_url = None
        try:
            image_prompt = f"A professional blog cover image for an article titled '{idea.title}'. Modern, clean, editorial style. No text in the image."
            image_bytes = await llm.generate_image(image_prompt)

            # Upload to S3
            from app.providers import create_storage_provider

            storage = create_storage_provider(config)
            image_key = f"demo-articles/{task_id}/cover.png"
            cover_image_url = await storage.upload(
                image_key, image_bytes, "image/png"
            )
        except Exception as e:
            logger.warning(
                "try_draftspring_image_error", task_id=task_id, error=str(e)
            )
            # Image failure is non-fatal — continue without cover image

        await llm.close()

        # Convert markdown to HTML for email
        article_html = _markdown_to_html(humanized)

        # Strip leading H1 — the email template already renders the title
        import re as _re
        article_html = _re.sub(r"^\s*<h1>.*?</h1>\s*", "", article_html, count=1)

        # Extract preview (first paragraph)
        article_preview = _extract_preview(humanized)

        # Update with article content
        await _update_status(
            config,
            task_id,
            "sending",
            "Sending to your inbox...",
            article_html=article_html,
            article_preview=article_preview,
            cover_image_url=cover_image_url or "",
        )

        # Step 8: Mailchimp subscribe + Resend email
        try:
            await _subscribe_to_mailchimp(email, url)
        except Exception as e:
            logger.warning("try_draftspring_mailchimp_error", error=str(e))

        try:
            await _send_article_email(
                config, email, idea.title, article_html, cover_image_url
            )
        except Exception as e:
            logger.error("try_draftspring_email_error", task_id=task_id, error=str(e))
            await _update_status(
                config,
                task_id,
                "failed",
                "Email send failed",
                error_message="We generated your article but couldn't send the email. Please try again.",
            )
            return

        # Step 9: Complete
        await _update_status(config, task_id, "complete", "Done!")

        logger.info(
            "try_draftspring_completed",
            task_id=task_id,
            url=url,
            title=idea.title,
        )

    except Exception as e:
        logger.error(
            "try_draftspring_pipeline_error",
            task_id=task_id,
            error=str(e),
            exc_info=True,
        )
        await _update_status(
            config,
            task_id,
            "failed",
            "Pipeline error",
            error_message="Something went wrong generating your article. Please try again.",
        )


# ── Ghost validation ─────────────────────────────────────────────────

async def _validate_ghost(url: str) -> bool:
    """Check if URL is a Ghost blog by fetching and checking meta generator tag."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": UA})
            if resp.status_code != 200:
                return False
            return check_ghost(resp.text)
    except Exception:
        return False


# ── Markdown to HTML ──────────────────────────────────────────────────

def _markdown_to_html(md: str) -> str:
    """Convert markdown to clean HTML for email."""
    import re

    html_parts = []
    lines = md.split("\n")
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue

        # Headers
        if stripped.startswith("### "):
            html_parts.append(f"<h3>{_inline_md(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            html_parts.append(f"<h2>{_inline_md(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            html_parts.append(f"<h1>{_inline_md(stripped[2:])}</h1>")
        # List items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline_md(stripped[2:])}</li>")
        # Image anchors — skip them in email
        elif stripped.startswith("[IMAGE_ANCHOR"):
            continue
        # Regular paragraph
        else:
            html_parts.append(f"<p>{_inline_md(stripped)}</p>")

    if in_list:
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    import re

    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Links
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    return text


def _extract_preview(md: str) -> str:
    """Extract first meaningful paragraph as preview text."""
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("[IMAGE_ANCHOR"):
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            continue
        # Found a paragraph
        # Strip markdown formatting
        import re

        text = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        return text[:500]
    return ""


# ── Email ─────────────────────────────────────────────────────────────

async def _send_article_email(
    config: Config,
    to: str,
    title: str,
    article_html: str,
    cover_image_url: str | None,
):
    """Send the generated article via Resend."""
    cta_url = "https://app.draftspring.io/login?utm_source=try-draftspring&utm_medium=email&utm_campaign=lead-magnet"

    cover_section = ""
    if cover_image_url:
        cover_section = f"""
        <tr><td style="padding:0;">
          <img src="{cover_image_url}" alt="Cover image" style="width:100%;max-width:600px;height:auto;display:block;border-radius:8px 8px 0 0;" />
        </td></tr>"""

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;">Your custom article from DraftSpring — written in your blog's voice.</div>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#f9fafb;">
<tr><td align="center" style="padding:40px 16px;">

  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:8px;border:1px solid #e5e7eb;overflow:hidden;">

    {cover_section}

    <!-- Title -->
    <tr><td style="padding:32px 32px 0 32px;">
      <h1 style="margin:0;font-size:28px;font-weight:700;color:#111827;line-height:1.3;">
        {title}
      </h1>
    </td></tr>

    <!-- Article body -->
    <tr><td style="padding:24px 32px;font-size:16px;line-height:1.7;color:#374151;">
      {article_html}
    </td></tr>

    <!-- Divider -->
    <tr><td style="padding:0 32px;">
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:0;">
    </td></tr>

    <!-- Footer CTA -->
    <tr><td style="padding:24px 32px 16px 32px;text-align:center;">
      <p style="margin:0 0 16px 0;font-size:14px;color:#6b7280;line-height:1.5;">
        This article was generated by DraftSpring based on your blog's writing style.<br>
        Want 8 articles like this every month?
      </p>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
        <tr><td style="border-radius:8px;background-color:#16a34a;">
          <a href="{cta_url}" target="_blank" style="display:inline-block;padding:14px 28px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;">
            Try DraftSpring for $9/mo →
          </a>
        </td></tr>
      </table>
    </td></tr>

    <!-- Sub-footer -->
    <tr><td style="padding:16px 32px 24px 32px;text-align:center;">
      <p style="margin:0;font-size:12px;color:#9ca3af;">
        🌱 DraftSpring · Content automation for Ghost blogs
      </p>
    </td></tr>

  </table>

</td></tr>
</table>
</body>
</html>"""

    # Send directly via Resend (avoid mutating shared config object)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "DraftSpring <noreply@draftspring.io>",
                "to": [to],
                "subject": f"Your custom article: {title}",
                "html": html,
            },
        )
        if resp.status_code != 200:
            logger.error("try_draftspring_resend_failed", status=resp.status_code, body=resp.text[:200])
            raise Exception(f"Resend email send failed: {resp.status_code}")


# ── Mailchimp ─────────────────────────────────────────────────────────

async def _subscribe_to_mailchimp(email: str, blog_url: str):
    """Subscribe email to Mailchimp with try-draftspring-lead tag."""
    mailchimp_api_key = os.environ.get("MAILCHIMP_API_KEY", "")
    if not mailchimp_api_key:
        logger.warning("mailchimp_not_configured")
        return

    dc = mailchimp_api_key.split("-")[-1] if "-" in mailchimp_api_key else "us18"
    list_id = "ff1f9dd9eb"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://{dc}.api.mailchimp.com/3.0/lists/{list_id}/members",
                auth=("anystring", mailchimp_api_key),
                json={
                    "email_address": email,
                    "status": "subscribed",
                    "tags": ["try-draftspring-lead"],
                    "merge_fields": {
                        "DOMAIN": blog_url[:50],
                    },
                },
            )
            if resp.status_code not in (200, 201) and "already a list member" not in resp.text.lower():
                logger.warning(
                    "mailchimp_subscribe_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
    except Exception as e:
        logger.warning("mailchimp_error", error=str(e))
