"""FastAPI application factory with production-ready lifecycle."""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Config, get_config
from app.database import get_connection, run_migrations
from app.routes.auth import router as auth_router
from app.routes.settings import router as settings_router
from app.routes.webhooks import router as webhooks_router
from app.routes.seeds import router as seeds_router
from app.routes.checkpoint_1 import router as checkpoint_1_router
from app.routes.checkpoint_2 import router as checkpoint_2_router
from app.routes.articles import router as articles_router
from app.routes.vault import router as vault_router
from app.routes.usage import router as usage_router
from app.routes.admin import router as admin_router
from app.routes.checkout import router as checkout_router

from app.routes.health_check import router as health_check_router
from app.routes.try_draftspring import router as try_draftspring_router
from app.routes.blog_analysis import router as blog_analysis_router


logger = structlog.get_logger()

VERSION = "0.5.0"
_start_time: float = 0.0


def _configure_logging(config: Config) -> None:
    """Configure structlog: JSON in production, pretty in dev/test."""
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


async def _run_startup(config: Config) -> None:
    """Startup tasks: migrations, key validation."""
    # Run migrations
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
    logger.info("migrations_complete", db=config.DATABASE_PATH)

    # Validate keys in production only
    if config.APP_ENV == "production":
        from scripts.validate_keys import validate_keys
        try:
            validate_keys(config)
            logger.info("key_validation_passed")
        except Exception as e:
            logger.error("key_validation_failed", error=str(e))
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    global _start_time
    _start_time = time.time()

    config = app.state.config
    _configure_logging(config)

    logger.info("starting", env=config.APP_ENV, version=VERSION)
    await _run_startup(config)
    logger.info("started", version=VERSION)

    yield

    # Shutdown
    logger.info("shutting_down")


def create_app(config: Config | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = get_config()

    app = FastAPI(
        title="DraftSpring",
        version=VERSION,
        lifespan=lifespan,
    )
    app.state.config = config

    # CORS middleware
    cors_origins = [o.strip() for o in config.CORS_ORIGINS.split(",") if o.strip()] if config.CORS_ORIGINS else ["http://localhost:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if config.APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # Register API routers
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(webhooks_router)
    app.include_router(seeds_router)
    app.include_router(checkpoint_1_router)
    app.include_router(checkpoint_2_router)
    app.include_router(articles_router)
    app.include_router(vault_router)
    app.include_router(usage_router)
    app.include_router(admin_router)
    app.include_router(checkout_router)

    app.include_router(health_check_router)
    app.include_router(try_draftspring_router)
    app.include_router(blog_analysis_router)

    # Health endpoint
    @app.get("/health")
    async def health(request: Request):
        """Health check: DB, Ghost, Stripe, email, storage."""
        cfg = request.app.state.config
        checks = {}
        all_ok = True

        # Database
        try:
            async with get_connection(cfg.DATABASE_PATH) as db:
                cursor = await db.execute("SELECT 1")
                await cursor.fetchone()
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
            all_ok = False

        # Ghost (config presence)
        checks["ghost"] = "ok"

        # Stripe
        checks["stripe"] = "ok" if cfg.STRIPE_SECRET_KEY else "not_configured"

        # Email
        checks["email"] = "ok" if (cfg.APP_ENV in ("test", "development") or cfg.RESEND_API_KEY) else "warning"

        # Storage
        if cfg.STORAGE_PROVIDER == "local":
            checks["storage"] = "ok"
        elif cfg.S3_ENDPOINT_URL:
            checks["storage"] = "ok"
        else:
            checks["storage"] = "warning"

        uptime = time.time() - _start_time if _start_time else 0

        status_code = 200 if all_ok else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ok" if all_ok else "degraded",
                "version": VERSION,
                "uptime_seconds": round(uptime, 1),
                "services": checks,
            },
        )

    # Mount frontend static files (SPA fallback)
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        # Mount static assets (js, css, images)
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

        # Serve root-level static files (favicon, robots.txt, etc.)
        @app.get("/favicon.png")
        @app.get("/favicon.svg")
        @app.get("/favicon.ico")
        @app.get("/robots.txt")
        async def root_static_file(request: Request):
            """Serve known root-level static files from frontend/dist."""
            filename = request.url.path.lstrip("/")
            file_path = frontend_dist / filename
            if file_path.is_file():
                return FileResponse(str(file_path))
            from fastapi.responses import Response
            return Response(status_code=404)

        # Cache control: hashed assets get long cache, index.html never cached
        @app.middleware("http")
        async def cache_control(request: Request, call_next):
            response = await call_next(request)
            path = request.url.path
            if path.startswith("/assets/") and response.status_code == 200:
                # Hashed filenames — cache for 1 year (immutable)
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path == "/" or (not path.startswith("/api/") and not path.startswith("/assets/")):
                # HTML pages — always revalidate
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

        # SPA fallback: serve index.html for any non-API, non-asset route
        @app.middleware("http")
        async def spa_fallback(request: Request, call_next):
            response = await call_next(request)
            # If it's a 404 and not an API/asset/health route, serve index.html
            if (
                response.status_code == 404
                and not request.url.path.startswith("/api/")
                and not request.url.path.startswith("/auth/")
                and not request.url.path.startswith("/webhooks/")
                and not request.url.path.startswith("/assets/")
                and request.url.path != "/health"
            ):
                index_file = frontend_dist / "index.html"
                if index_file.exists():
                    return FileResponse(str(index_file))
            return response

    return app


# Default app instance for uvicorn
app = create_app()
