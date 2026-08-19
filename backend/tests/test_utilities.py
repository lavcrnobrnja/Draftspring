"""Task 1.2: Utility function tests."""

import time
from datetime import datetime, timezone, timedelta

import pytest

from app.utils.ulid import generate_id
from app.utils.time import utc_now, is_expired
from app.services.encryption import encrypt, decrypt


class TestGenerateId:
    def test_returns_string(self):
        assert isinstance(generate_id(), str)

    def test_unique(self):
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100

    def test_length(self):
        # ULID is 26 chars
        assert len(generate_id()) == 26

    def test_sortable(self):
        id1 = generate_id()
        time.sleep(0.002)
        id2 = generate_id()
        assert id2 > id1


class TestUtcNow:
    def test_returns_iso_string(self):
        result = utc_now()
        # Should parse without error
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None or result.endswith("Z")

    def test_is_utc(self):
        result = utc_now()
        assert result.endswith("Z") or "+00:00" in result

    def test_recent(self):
        result = utc_now()
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        assert abs((now - dt).total_seconds()) < 2


class TestIsExpired:
    def test_past_is_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert is_expired(past) is True

    def test_future_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert is_expired(future) is False

    def test_none_not_expired(self):
        """None expiry means no expiry (never expires)."""
        assert is_expired(None) is False


class TestEncryption:
    def test_round_trip(self):
        plaintext = "my-secret-api-key-12345"
        ciphertext = encrypt(plaintext)
        assert decrypt(ciphertext) == plaintext

    def test_ciphertext_differs_from_plaintext(self):
        plaintext = "my-secret"
        ciphertext = encrypt(plaintext)
        assert ciphertext != plaintext

    def test_different_encryptions_differ(self):
        """Fernet uses random IV so same plaintext produces different ciphertext."""
        ct1 = encrypt("same")
        ct2 = encrypt("same")
        assert ct1 != ct2

    def test_decrypt_wrong_data_raises(self):
        with pytest.raises(Exception):
            decrypt("not-valid-ciphertext")
