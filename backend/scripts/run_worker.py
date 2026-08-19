#!/usr/bin/env python3
"""Worker process entry point.

Usage: python scripts/run_worker.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.logging_config import setup_logging, get_logger


async def main():
    config = Config()
    is_prod = config.APP_ENV == "production"
    setup_logging(json_format=is_prod)
    log = get_logger("worker")

    log.info("worker_starting", worker_id=config.WORKER_ID)

    from app.pipeline.worker import run_worker_loop
    try:
        await run_worker_loop(config)
    except KeyboardInterrupt:
        log.info("worker_stopped", worker_id=config.WORKER_ID)
    except Exception as e:
        log.error("worker_crashed", worker_id=config.WORKER_ID, error=str(e))
        # Send admin alert
        from app.services.alerts import alert_worker_down
        await alert_worker_down(config, config.WORKER_ID, str(e))
        raise


if __name__ == "__main__":
    asyncio.run(main())
