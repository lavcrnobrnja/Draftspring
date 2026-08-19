"""State machine dispatcher: maps article state → handler."""

import aiosqlite

from app.llm.base import LLMProvider
from app.storage.base import StorageProvider


async def dispatch_article(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
    storage: StorageProvider | None = None,
) -> dict:
    """Dispatch an article to its appropriate handler based on current state."""
    cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
    row = await cursor.fetchone()
    if not row:
        return {"success": False, "error": "Article not found"}

    state = row["state"]

    if state == "OUTLINING":
        from app.pipeline.transitions.t3_outlining import run_outlining
        return await run_outlining(db, config, article_id, llm)

    elif state == "DRAFTING":
        from app.pipeline.transitions.t4_drafting import run_drafting
        return await run_drafting(db, config, article_id, llm)

    elif state == "HUMANIZING":
        from app.pipeline.transitions.t5_humanizing import run_humanizing
        return await run_humanizing(db, config, article_id, llm)

    elif state == "EDIT_REVIEW":
        from app.pipeline.transitions.t6_edit_review import run_edit_review
        return await run_edit_review(db, config, article_id, llm)

    elif state == "MEDIA_ASSEMBLY":
        from app.pipeline.transitions.t7_media_assembly import run_media_assembly
        return await run_media_assembly(db, config, article_id, llm, storage=storage)

    elif state == "REVISION":
        from app.pipeline.transitions.t10_revision import run_revision
        return await run_revision(db, config, article_id, llm)

    elif state == "READY_TO_PUBLISH":
        from app.pipeline.transitions.t11_publishing import run_publishing
        return await run_publishing(db, config, article_id, llm)

    elif state == "PUBLISHING":
        from app.pipeline.transitions.t11_publishing import run_publishing
        return await run_publishing(db, config, article_id, llm)

    else:
        return {"success": False, "error": f"No handler for state: {state}"}
