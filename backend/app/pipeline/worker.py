"""Worker: finds work, locks, dispatches, unlocks."""

import asyncio

import aiosqlite
import structlog

from app.pipeline.locking import acquire_lock, release_lock, cleanup_stale_locks

logger = structlog.get_logger()


# States that the worker should process (not checkpoints, not terminal)
PROCESSABLE_STATES = (
    "OUTLINING", "DRAFTING", "HUMANIZING", "EDIT_REVIEW",
    "MEDIA_ASSEMBLY", "REVISION",
    "READY_TO_PUBLISH", "PUBLISHING",
)

# States where articles are waiting on user action — don't block the user's queue
NON_BLOCKING_STATES = (
    "WAITING_CHECKPOINT_2", "READY_TO_PUBLISH",
    "PUBLISHED", "FAILED", "ARCHIVED",
)


async def find_pending_batches(db: aiosqlite.Connection) -> list[dict]:
    """Find seed batches ready for ideation, filtering out inactive subscriptions."""
    cursor = await db.execute(
        """SELECT sb.* FROM seed_batches sb
           JOIN users u ON sb.user_id = u.id
           WHERE sb.status = 'pending_ideation'
             AND u.subscription_status IN ('active', 'trialing')
           ORDER BY sb.created_at ASC""",
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def find_processable_articles(
    db: aiosqlite.Connection, max_concurrent: int = 10
) -> list[dict]:
    """Find articles ready for processing.
    
    Rules:
    - Only processable states
    - Not locked by another worker
    - Per-user sequential: one active article at a time (checkpointed/terminal don't block)
    - Global concurrency cap
    - Only active/trialing subscriptions
    - READY_TO_PUBLISH articles only when scheduled_publish_at has passed
    """
    from app.utils.time import utc_now
    now = utc_now()

    cursor = await db.execute(
        """SELECT a.* FROM articles a
           JOIN users u ON a.user_id = u.id
           WHERE a.state IN ({})
             AND a.locked_by IS NULL
             AND u.subscription_status IN ('active', 'trialing')
             AND (a.state != 'READY_TO_PUBLISH' OR a.scheduled_publish_at IS NULL OR a.scheduled_publish_at <= ?)
           ORDER BY a.created_at ASC""".format(
            ",".join("?" for _ in PROCESSABLE_STATES)
        ),
        (*PROCESSABLE_STATES, now),
    )
    candidates = [dict(r) for r in await cursor.fetchall()]

    cursor = await db.execute(
        """SELECT DISTINCT user_id FROM articles
           WHERE state IN ({}) AND locked_by IS NOT NULL""".format(
            ",".join("?" for _ in PROCESSABLE_STATES)
        ),
        PROCESSABLE_STATES,
    )
    busy_users = {row["user_id"] for row in await cursor.fetchall()}

    selected = []
    seen_users = set()
    for article in candidates:
        uid = article["user_id"]
        if uid in busy_users or uid in seen_users:
            continue
        seen_users.add(uid)
        selected.append(article)
        if len(selected) >= max_concurrent:
            break

    return selected


def _get_storage(config):
    """Create the appropriate storage provider based on config."""
    from app.providers import create_storage_provider
    return create_storage_provider(config)


def _get_llm(config):
    """Create the appropriate LLM provider based on environment."""
    if config.APP_ENV == "test":
        from app.llm.mock import MockLLM
        return MockLLM()

    has_keys = bool(config.OPENAI_API_KEY and config.GEMINI_API_KEY)
    has_anthropic = bool(config.ANTHROPIC_API_KEY or config.ANTHROPIC_BASE_URL)

    if has_keys and has_anthropic:
        from app.llm.live import LiveLLM
        return LiveLLM(config)
    else:
        import logging
        logging.getLogger(__name__).warning(
            "Missing API keys, falling back to MockLLM. "
            f"OpenAI={'yes' if config.OPENAI_API_KEY else 'no'}, "
            f"Gemini={'yes' if config.GEMINI_API_KEY else 'no'}, "
            f"Anthropic={'yes' if has_anthropic else 'no'}"
        )
        from app.llm.mock import MockLLM
        return MockLLM()


async def _process_batch(config, batch: dict) -> bool:
    """Process a single batch in its own DB connection."""
    from app.database import get_connection
    batch_id = batch["id"]
    logger.info("processing_batch", batch_id=batch_id)

    # Atomically claim this batch to prevent double-processing
    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "UPDATE seed_batches SET status = 'processing_ideation' WHERE id = ? AND status = 'pending_ideation'",
            (batch_id,),
        )
        await db.commit()
        if cursor.rowcount == 0:
            logger.info("batch_already_claimed", batch_id=batch_id)
            return False

    try:
        async with get_connection(config.DATABASE_PATH) as db:
            from app.pipeline.transitions.t1_ideation import run_ideation
            llm = _get_llm(config)
            result = await run_ideation(db, config, batch_id, llm)
            logger.info("batch_processed", batch_id=batch_id, result=result)
            return True
    except Exception as e:
        logger.error("batch_processing_failed", batch_id=batch_id, error=str(e))
        try:
            async with get_connection(config.DATABASE_PATH) as db:
                await db.execute(
                    "UPDATE seed_batches SET status = 'expired' WHERE id = ?",
                    (batch_id,),
                )
                await db.commit()
        except Exception:
            pass
        return False


async def _process_article(config, article: dict, worker_id: str) -> bool:
    """Process a single article in its own DB connection."""
    from app.database import get_connection
    article_id = article["id"]

    # Acquire lock
    async with get_connection(config.DATABASE_PATH) as db:
        locked = await acquire_lock(db, article_id, worker_id)
        if not locked:
            logger.info("lock_failed", article_id=article_id)
            return False

    try:
        async with get_connection(config.DATABASE_PATH) as db:
            llm = _get_llm(config)
            storage = _get_storage(config)
            from app.pipeline.state_machine import dispatch_article
            result = await dispatch_article(db, config, article_id, llm, storage=storage)
            logger.info("article_processed", article_id=article_id, state=article["state"], result=result)

            if not result.get("success"):
                from app.utils.time import utc_now
                now = utc_now()
                # Record failed_from_state so the user-facing retry endpoint
                # can resume from the exact step that failed.
                await db.execute(
                    "UPDATE articles SET state = 'FAILED', failed_at = ?, failure_reason = ?, failed_from_state = ?, updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
                    (now, result.get("error", "Unknown error"), article["state"], now, article_id),
                )
                await db.commit()
    except Exception as e:
        import traceback
        logger.error("article_processing_failed", article_id=article_id, error=str(e), tb=traceback.format_exc())
        try:
            async with get_connection(config.DATABASE_PATH) as db:
                from app.utils.time import utc_now
                now = utc_now()
                await db.execute(
                    "UPDATE articles SET state = 'FAILED', failed_at = ?, failure_reason = ?, failed_from_state = ?, updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
                    (now, str(e), article["state"], now, article_id),
                )
                await db.commit()
        except Exception:
            pass
    finally:
        try:
            async with get_connection(config.DATABASE_PATH) as db:
                await release_lock(db, article_id, worker_id)
        except Exception:
            pass

    return True


async def find_and_process_work(db: aiosqlite.Connection, config) -> bool:
    """Find and process work. Batches and articles run concurrently.
    
    Priority:
    1. Clean up stale locks
    2. Find pending batches and processable articles
    3. Process both concurrently (each with its own DB connection)
    
    Returns True if any work was done.
    """
    worker_id = getattr(config, "WORKER_ID", "worker-1")

    # Clean stale locks (quick, uses shared connection)
    cleaned = await cleanup_stale_locks(db)
    if cleaned:
        logger.info("stale_locks_cleaned", count=cleaned)

    # Find work (reads only, uses shared connection)
    batches = await find_pending_batches(db)
    articles = await find_processable_articles(db)

    if not batches and not articles:
        return False

    # Build concurrent tasks
    tasks = []
    if batches:
        tasks.append(_process_batch(config, batches[0]))
    if articles:
        tasks.append(_process_article(config, articles[0], worker_id))

    # Run concurrently — each task manages its own DB connection
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("concurrent_task_exception", error=str(result), task_index=i)

    return True
