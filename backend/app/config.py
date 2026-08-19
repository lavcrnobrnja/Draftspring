"""Application configuration using pydantic-settings."""

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # App
    APP_ENV: str = "development"
    APP_BASE_URL: str = "http://localhost:8223"
    ENCRYPTION_KEY: str = ""
    WORKER_ID: str = "worker-01"

    # Database
    DATABASE_PATH: str = "./data/ghostwriter.db"

    # LLM — OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL_ID: str = "gpt-5.4"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # LLM — Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_ID: str = "gemini-2.5-pro"

    # LLM — Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL_ID: str = "claude-sonnet-4-6"
    ANTHROPIC_BASE_URL: str = ""  # If set, use OpenAI-compatible proxy (e.g. claude-max-api-proxy)

    # Image Generation
    # Image gen uses GEMINI_API_KEY via google-genai SDK (Gemini 3 Pro Image)
    # No separate key needed — reuses GEMINI_API_KEY

    # Storage
    STORAGE_PROVIDER: str = "local"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET_NAME: str = ""
    S3_PUBLIC_URL_PREFIX: str = ""
    LOCAL_STORAGE_PATH: str = "./data/storage"

    # Email
    EMAIL_PROVIDER: str = "resend"
    RESEND_API_KEY: str = ""
    EMAIL_FROM_ADDRESS: str = "content@example.com"
    EMAIL_FROM_NAME: str = "DraftSpring"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID: str = ""
    STRIPE_TRIAL_DAYS: int = 7

    # Cloudflare Turnstile
    TURNSTILE_SITE_KEY: str = ""
    TURNSTILE_SECRET_KEY: str = ""

    # Ghost defaults (fallback when user row has no ghost_url / ghost_admin_api_key_enc)
    GHOST_URL: str = ""
    GHOST_ADMIN_API_KEY: str = ""




    # Admin
    ADMIN_EMAILS: str = ""

    # Cost controls
    PER_ARTICLE_COST_CEILING_CENTS: int = 250
    MAX_CONCURRENT_ARTICLES: int = 10
    DAILY_IMAGE_GENERATION_CAP: int = 50

    # Security
    COOKIE_DOMAIN: str = "localhost"
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173"

    # Logging
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "extra": "ignore"}


def get_config() -> Config:
    """Get the application config singleton."""
    return Config()
