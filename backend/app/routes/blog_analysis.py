"""Blog Analysis routes — analyze blogs and generate content ideas."""

import asyncio
import json
import sqlite3
from dataclasses import asdict

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.middleware.subscription import require_active_subscription
from app.models.usage import get_articles_remaining
from app.pipeline.transitions.t2_idea_approval import approve_ideas
from app.services.blog_analyzer import BlogAnalyzer, BlogAnalyzerError
from app.utils.url_validation import validate_url

router = APIRouter(prefix="/api", tags=["blog-analysis"])

_SQLITE_LOCK_RETRY_DELAYS = (0.1, 0.25, 0.5)


class AnalyzeRequest(BaseModel):
    url: str


class GenerateIdeasRequest(BaseModel):
    profile_id: str
    count: int = 10


class IdeaInput(BaseModel):
    title: str
    angle: str = ""
    article_type: str = "how-to"


class FromAnalysisRequest(BaseModel):
    profile_id: str
    ideas: list[IdeaInput]


# ── Helpers ───────────────────────────────────────────────────────────

async def _auth_and_subscription(db, request, *, require_ghost: bool = False) -> dict:
    """Auth + subscription check. Returns session dict.
    
    If require_ghost=True, also verifies Ghost is connected (same logic as seeds.py).
    """
    session = await get_current_session(db, request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cursor = await db.execute(
        "SELECT subscription_status, ghost_key_valid FROM users WHERE id = ?",
        (session["user_id"],),
    )
    user = await cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    require_active_subscription(dict(user))

    if require_ghost:
        from app.config import get_config
        _cfg = get_config()
        if not user["ghost_key_valid"] and not (_cfg.GHOST_URL and _cfg.GHOST_ADMIN_API_KEY):
            raise HTTPException(
                status_code=400,
                detail="Ghost connection required. Connect your Ghost blog in Settings first.",
            )

    return session


def _is_sqlite_locked_error(exc: Exception) -> bool:
    """True only for transient SQLite database-lock OperationalErrors."""
    candidates = [exc]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        candidates.append(orig)

    return any(
        isinstance(e, sqlite3.OperationalError)
        and "database is locked" in str(e).lower()
        for e in candidates
    )


async def _retry_sqlite_locked(operation):
    """Retry a short user-facing DB write when SQLite is temporarily locked."""
    for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
        try:
            return await operation()
        except Exception as exc:
            if not _is_sqlite_locked_error(exc):
                raise
            if attempt >= len(_SQLITE_LOCK_RETRY_DELAYS):
                raise HTTPException(
                    status_code=503,
                    detail="Database is busy. Please try again in a moment.",
                ) from exc
            await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/blog-analysis/analyze")
async def analyze_blog(body: AnalyzeRequest, request: Request):
    """Analyze a blog URL and return its profile."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await _auth_and_subscription(db, request)

    # Validate URL
    clean_url = validate_url(body.url)
    if not clean_url:
        raise HTTPException(status_code=400, detail="Please provide a valid URL.")

    # Run analysis
    analyzer = BlogAnalyzer(config)
    try:
        profile = await analyzer.get_or_analyze(clean_url)
    except BlogAnalyzerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "profile": asdict(profile),
    }


@router.post("/blog-analysis/generate-ideas")
async def generate_ideas(body: GenerateIdeasRequest, request: Request):
    """Generate article ideas from a cached blog profile."""
    config = request.app.state.config

    if body.count < 1 or body.count > 20:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 20.")

    async with get_connection(config.DATABASE_PATH) as db:
        session = await _auth_and_subscription(db, request)

        # Fetch cached profile
        rows = await db.execute_fetchall(
            "SELECT * FROM blog_profiles WHERE id = ?", (body.profile_id,)
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Blog profile not found.")

    # Reconstruct profile from DB
    row = rows[0]
    from app.services.blog_analyzer import BlogProfile
    profile_data = json.loads(row["profile_data"])
    profile = BlogProfile(
        id=row["id"],
        url=row["url"],
        site_name=row["site_name"] or "",
        is_ghost=bool(row["is_ghost"]),
        topics=profile_data.get("topics", []),
        content_gaps=profile_data.get("content_gaps", []),
        style_guide=profile_data.get("style_guide", ""),
        example_sentences=profile_data.get("example_sentences", []),
        audience_description=profile_data.get("audience_description", ""),
        tone_keywords=profile_data.get("tone_keywords", []),
        strengths=profile_data.get("strengths", []),
        avg_word_count=profile_data.get("avg_word_count", 0),
        total_posts=profile_data.get("total_posts", 0),
        latest_post_date=profile_data.get("latest_post_date", ""),
        publishing_frequency=profile_data.get("publishing_frequency", ""),
        post_summaries=profile_data.get("post_summaries", []),
        analyzed_at=row["analyzed_at"],
    )

    # Generate ideas
    analyzer = BlogAnalyzer(config)
    try:
        ideas = await analyzer.generate_ideas(profile, count=body.count)
    except BlogAnalyzerError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "ideas": [asdict(idea) for idea in ideas],
    }


@router.post("/seeds/from-analysis", status_code=201)
async def seeds_from_analysis(body: FromAnalysisRequest, request: Request):
    """Create articles directly from selected blog analysis ideas.

    Persists submitted ideas as real `ideas` rows and invokes the existing
    `approve_ideas()` transition to create articles in OUTLINING state,
    bypassing T1 ideation entirely.
    """
    config = request.app.state.config

    if not body.ideas:
        raise HTTPException(status_code=400, detail="At least one idea is required.")

    if len(body.ideas) > 3:
        raise HTTPException(status_code=400, detail="Maximum 3 ideas per submission.")

    async def create_from_analysis():
        async with get_connection(config.DATABASE_PATH) as db:
            session = await _auth_and_subscription(db, request, require_ghost=True)
            user_id = session["user_id"]

            # Verify profile exists and get URL for context
            rows = await db.execute_fetchall(
                "SELECT id, url FROM blog_profiles WHERE id = ?", (body.profile_id,)
            )
            if not rows:
                raise HTTPException(status_code=404, detail="Blog profile not found.")

            remaining = await get_articles_remaining(db, user_id)
            requested = len(body.ideas)
            if requested > remaining:
                article_word = "article" if remaining == 1 else "articles"
                raise HTTPException(
                    status_code=409,
                    detail=f"You have {remaining} {article_word} remaining this cycle. No articles were created.",
                )

            profile_url = rows[0]["url"]

            from app.utils.ulid import generate_id
            from app.utils.time import utc_now

            batch_id = generate_id()
            now = utc_now()

            # Create batch with status='processed' so the worker skips it entirely
            # (approve_ideas will also update it, but set it upfront to match
            # our architectural intent and avoid any race with the worker).
            await db.execute(
                """INSERT INTO seed_batches (id, user_id, status, source, created_at)
                   VALUES (?, ?, 'processed', 'analysis', ?)""",
                (batch_id, user_id, now),
            )

            # Create one seed per idea (one-to-one) — ideas.seed_id is NOT NULL,
            # and _assemble_content_brief reads from seeds.
            idea_ids = []
            for idea in body.ideas:
                seed_id = generate_id()
                content = f"{idea.title}"
                if idea.angle:
                    content += f"\n\nAngle: {idea.angle}"
                content += f"\n\n[Analyzed from: {profile_url}]"

                await db.execute(
                    """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
                       VALUES (?, ?, 'topic', ?, ?)""",
                    (seed_id, batch_id, content, now),
                )

                idea_id = generate_id()
                idea_ids.append(idea_id)
                await db.execute(
                    """INSERT INTO ideas
                         (id, batch_id, seed_id, title, angle, target_keyword,
                          estimated_volume, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'low', 'pending', ?)""",
                    (
                        idea_id,
                        batch_id,
                        seed_id,
                        idea.title,
                        idea.angle or "",
                        (idea.title or "")[:200],
                        now,
                    ),
                )

            # Hand off to approve_ideas: it creates articles in OUTLINING, handles
            # budget, assembles content_brief, logs pipeline events, and commits.
            result = await approve_ideas(
                db,
                user_id,
                batch_id,
                [{"id": i, "title": None} for i in idea_ids],
            )

            return {
                "batch_id": batch_id,
                "articles_created": result["articles_created"],
                "budget_limited": result["budget_limited"],
                "seed_count": len(body.ideas),
            }

    return await _retry_sqlite_locked(create_from_analysis)
