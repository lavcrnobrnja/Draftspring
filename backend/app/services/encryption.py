"""Encryption service using Fernet symmetric encryption."""

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    """Get Fernet instance from ENCRYPTION_KEY env var.
    
    Accepts any string as ENCRYPTION_KEY — derives a valid 32-byte
    Fernet key from it via SHA-256.
    """
    raw_key = os.environ.get("ENCRYPTION_KEY", "default-dev-key")
    # Derive a proper 32-byte key via SHA-256, then base64-encode for Fernet
    derived = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """Encrypt plaintext string, return base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt ciphertext string, return plaintext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
