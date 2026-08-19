"""Tests for storage providers (Task 3.2)."""

import os
import tempfile

import pytest
import pytest_asyncio

from app.storage.base import StorageProvider
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


class TestLocalStorage:
    @pytest.fixture
    def storage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LocalStorage(base_path=tmpdir)

    @pytest.mark.asyncio
    async def test_upload_and_download(self, storage):
        data = b"hello world"
        url = await storage.upload("test/file.txt", data, content_type="text/plain")
        assert url is not None
        assert "test/file.txt" in url

        downloaded = await storage.download("test/file.txt")
        assert downloaded == data

    @pytest.mark.asyncio
    async def test_exists(self, storage):
        assert await storage.exists("nonexistent.txt") is False
        await storage.upload("exists.txt", b"data", content_type="text/plain")
        assert await storage.exists("exists.txt") is True

    @pytest.mark.asyncio
    async def test_delete(self, storage):
        await storage.upload("to-delete.txt", b"data", content_type="text/plain")
        assert await storage.exists("to-delete.txt") is True
        await storage.delete("to-delete.txt")
        assert await storage.exists("to-delete.txt") is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self, storage):
        # Should not raise
        await storage.delete("nope.txt")

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_none(self, storage):
        result = await storage.download("nope.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_nested_directories(self, storage):
        await storage.upload("a/b/c/deep.txt", b"deep", content_type="text/plain")
        assert await storage.exists("a/b/c/deep.txt") is True
        data = await storage.download("a/b/c/deep.txt")
        assert data == b"deep"

    @pytest.mark.asyncio
    async def test_binary_data(self, storage):
        binary = bytes(range(256))
        await storage.upload("binary.bin", binary, content_type="application/octet-stream")
        result = await storage.download("binary.bin")
        assert result == binary

    @pytest.mark.asyncio
    async def test_url_format(self, storage):
        url = await storage.upload("img.webp", b"img", content_type="image/webp")
        assert url.startswith("file://") or url.startswith("/")


class TestS3StorageInterface:
    """Verify S3Storage implements the interface correctly (no real S3 needed)."""

    def test_is_storage_provider(self):
        assert issubclass(S3Storage, StorageProvider)

    def test_has_required_methods(self):
        methods = ["upload", "download", "delete", "exists"]
        for method in methods:
            assert hasattr(S3Storage, method), f"Missing method: {method}"
