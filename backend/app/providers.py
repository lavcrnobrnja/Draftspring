"""Provider factory — creates mock or live providers based on APP_ENV."""

from app.config import Config


def create_llm_provider(config: Config):
    """Create the appropriate LLM provider based on environment.
    
    Returns MockLLM for test/development, LiveLLM for production.
    """
    if config.APP_ENV in ("test", "development"):
        from app.llm.mock import MockLLM
        return MockLLM()
    else:
        from app.llm.live import LiveLLM
        return LiveLLM(config)


def create_storage_provider(config: Config):
    """Create the appropriate storage provider based on config.
    
    Returns LocalStorage for local, S3Storage for s3.
    """
    if config.STORAGE_PROVIDER == "s3":
        from app.storage.s3 import S3Storage
        return S3Storage(
            endpoint_url=config.S3_ENDPOINT_URL,
            access_key_id=config.S3_ACCESS_KEY_ID,
            secret_access_key=config.S3_SECRET_ACCESS_KEY,
            bucket_name=config.S3_BUCKET_NAME,
            public_url_prefix=config.S3_PUBLIC_URL_PREFIX,
        )
    else:
        from app.storage.local import LocalStorage
        return LocalStorage(base_path=config.LOCAL_STORAGE_PATH)


def create_email_service(config: Config):
    """Return the email service module.
    
    The email service uses config.APP_ENV internally to decide
    between test mode (in-memory) and production (Resend).
    """
    from app.services import email
    return email


def create_all_providers(config: Config) -> dict:
    """Create all providers as a dict for easy dependency injection.
    
    Returns:
        {
            "llm": LLMProvider instance,
            "storage": StorageProvider instance,
            "email": email module,
            "config": Config instance,
        }
    """
    return {
        "llm": create_llm_provider(config),
        "storage": create_storage_provider(config),
        "email": create_email_service(config),
        "config": config,
    }
