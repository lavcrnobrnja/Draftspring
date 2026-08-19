#!/usr/bin/env python3
"""Web server entry point.

Usage: python scripts/run_web.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn

from app.config import Config
from app.logging_config import setup_logging


def main():
    config = Config()
    is_prod = config.APP_ENV == "production"

    setup_logging(json_format=is_prod)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
        reload=not is_prod,
        log_level="info" if is_prod else "debug",
    )


if __name__ == "__main__":
    main()
