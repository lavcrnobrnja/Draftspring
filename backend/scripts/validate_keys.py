#!/usr/bin/env python3
"""Validate all API keys on startup. Exit with error if any fail.

Usage: python scripts/validate_keys.py
"""

import asyncio
import sys
import os

import httpx

# Add backend dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config


async def validate_openai(config: Config) -> tuple[bool, str]:
    """Test OpenAI API key with a minimal request."""
    if not config.OPENAI_API_KEY:
        return False, "OPENAI_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{config.OPENAI_BASE_URL}/models",
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


async def validate_gemini(config: Config) -> tuple[bool, str]:
    """Test Gemini API key."""
    if not config.GEMINI_API_KEY:
        return False, "GEMINI_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={config.GEMINI_API_KEY}",
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


async def validate_anthropic(config: Config) -> tuple[bool, str]:
    """Test Anthropic API key with a minimal request."""
    if not config.ANTHROPIC_API_KEY:
        return False, "ANTHROPIC_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.ANTHROPIC_MODEL_ID,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            if resp.status_code in (200, 201):
                return True, "OK"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


async def validate_resend(config: Config) -> tuple[bool, str]:
    """Test Resend API key."""
    if not config.RESEND_API_KEY:
        return False, "RESEND_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.resend.com/api-keys",
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


async def validate_stripe(config: Config) -> tuple[bool, str]:
    """Test Stripe API key."""
    if not config.STRIPE_SECRET_KEY:
        return False, "STRIPE_SECRET_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.stripe.com/v1/balance",
                headers={"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"},
            )
            if resp.status_code == 200:
                return True, "OK"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, str(e)


async def validate_storage(config: Config) -> tuple[bool, str]:
    """Test storage configuration."""
    if config.STORAGE_PROVIDER == "local":
        return True, "OK (local storage)"
    
    # S3 can use IAM roles (no explicit keys needed on EC2)
    if not config.S3_ENDPOINT_URL or not config.S3_BUCKET_NAME:
        return False, "S3 endpoint or bucket not configured"
    
    try:
        from app.storage.s3 import S3Storage
        storage = S3Storage(
            endpoint_url=config.S3_ENDPOINT_URL,
            access_key_id=config.S3_ACCESS_KEY_ID,
            secret_access_key=config.S3_SECRET_ACCESS_KEY,
            bucket_name=config.S3_BUCKET_NAME,
            public_url_prefix=config.S3_PUBLIC_URL_PREFIX,
        )
        # Try a harmless exists check
        await storage.exists("__healthcheck__")
        return True, "OK"
    except Exception as e:
        return False, str(e)


async def validate_image_gen(config: Config) -> tuple[bool, str]:
    """Test image generation availability (uses Gemini API key)."""
    if not config.GEMINI_API_KEY:
        return False, "GEMINI_API_KEY not set (needed for image gen)"
    try:
        from google import genai  # noqa: F401
        return True, "OK (google-genai available, uses Gemini key)"
    except ImportError:
        return False, "google-genai package not installed"


def validate_keys(config: Config) -> None:
    """Validate API keys on startup. Raises on critical failures.
    
    Called by main.py during production startup.
    Non-critical failures (Stripe, Nano Banana) are logged as warnings.
    Critical failures (OpenAI, Gemini, Anthropic, Resend) raise exceptions.
    """
    import logging
    log = logging.getLogger(__name__)
    
    critical_keys = {
        "OpenAI": bool(config.OPENAI_API_KEY),
        "Gemini": bool(config.GEMINI_API_KEY),
        "Anthropic": bool(config.ANTHROPIC_API_KEY or config.ANTHROPIC_BASE_URL),
        "Resend": bool(config.RESEND_API_KEY),
    }
    
    warnings_keys = {
        "Stripe": bool(config.STRIPE_SECRET_KEY),
    }
    
    missing_critical = [k for k, v in critical_keys.items() if not v]
    missing_warnings = [k for k, v in warnings_keys.items() if not v]
    
    for k in missing_warnings:
        log.warning(f"API key not configured: {k} (non-critical)")
    
    if missing_critical:
        raise RuntimeError(f"Missing critical API keys: {', '.join(missing_critical)}")
    
    log.info(f"Key validation passed. Critical: all present. Warnings: {missing_warnings or 'none'}")


async def main():
    config = Config()
    print("🔑 Validating API keys...\n")

    validators = [
        ("OpenAI (GPT-5.4)", validate_openai),
        ("Gemini (Deep Research)", validate_gemini),
        ("Anthropic (Claude Sonnet 4.6)", validate_anthropic),
        ("Nano Banana 2 (Image Gen)", validate_nano_banana),
        ("Resend (Email)", validate_resend),
        ("Stripe (Payments)", validate_stripe),
        ("Storage", validate_storage),
    ]

    results = []
    for name, validator in validators:
        ok, msg = await validator(config)
        status = "✅" if ok else "❌"
        print(f"  {status} {name}: {msg}")
        results.append((name, ok, msg))

    failed = [r for r in results if not r[1]]
    print()
    if failed:
        print(f"❌ {len(failed)} service(s) failed validation. Server must not start.")
        sys.exit(1)
    else:
        print("✅ All API keys validated successfully.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
