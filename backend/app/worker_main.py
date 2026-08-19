"""Standalone worker process with graceful shutdown."""

import asyncio
import signal
import sys

from dotenv import load_dotenv
load_dotenv()

import structlog

from app.config import get_config
from app.database import get_connection, run_migrations
from app.pipeline.worker import find_and_process_work

logger = structlog.get_logger()

_shutdown = False


def _handle_signal(signum, frame):
    """Handle shutdown signals gracefully."""
    global _shutdown
    logger.info("shutdown_signal_received", signal=signum)
    _shutdown = True


async def run_worker():
    """Main worker loop: find work, process, sleep, repeat."""
    config = get_config()

    # Configure logging
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if config.APP_ENV == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Run migrations
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)

    logger.info("worker_started", worker_id=config.WORKER_ID, env=config.APP_ENV)

    # Register signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    poll_interval = 5  # seconds

    while not _shutdown:
        try:
            async with get_connection(config.DATABASE_PATH) as db:
                processed = await find_and_process_work(db, config)
                if processed:
                    logger.info("work_processed", worker_id=config.WORKER_ID)
        except Exception as e:
            logger.error("worker_error", error=str(e), worker_id=config.WORKER_ID)

        # Sleep in small increments so we can catch shutdown quickly
        for _ in range(poll_interval * 10):
            if _shutdown:
                break
            await asyncio.sleep(0.1)

    logger.info("worker_stopped", worker_id=config.WORKER_ID)


def main():
    """Entry point for the worker process."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
