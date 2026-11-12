"""Object storage behind one small async interface: local disk (dev) or any S3-compatible store.

Keys are relative paths with a prefix that says who may read them:

    raw/<token>.<ext>            original uploads (admin only, never served directly)
    pages/<job_id>/page-001.png  rendered pages of an upload (admin only, proxied by the app)
    figures/<name>.png           question figures (public: students see them while taking exams)
"""

import asyncio
import io
import json
import shutil
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

PUBLIC_PREFIX = "figures/"
PREVIEW_PREFIX = "previews/"


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def iter_stream(self, key: str, chunk_size: int = 64 * 1024): ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    def public_url(self, key: str) -> str: ...

    def presigned_url(
        self, key: str, expires_seconds: int = 60, download_filename: str | None = None
    ) -> str | None: ...


class StorageError(Exception):
    pass


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


class LocalStorage:
    """Files under <data_dir>/media. Only figures/ is mounted publicly at /media/figures."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        return self.root / _check_key(key)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self.path(key).read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"Not found: {key}") from exc

    async def iter_stream(self, key: str, chunk_size: int = 64 * 1024):
        path = self.path(key)
        if not path.is_file():
            raise StorageError(f"Not found: {key}")
        with open(path, "rb") as f:
            while True:
                chunk = await asyncio.to_thread(f.read, chunk_size)
                if not chunk:
                    break
                yield chunk

    async def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    async def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    async def delete_prefix(self, prefix: str) -> None:
        target = self.path(prefix.rstrip("/"))
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target, True)

    def public_url(self, key: str) -> str:
        return "/media/" + _check_key(key)

    def presigned_url(
        self, key: str, expires_seconds: int = 60, download_filename: str | None = None
    ) -> str | None:
        return None

    def iter_keys(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                yield path.relative_to(self.root).as_posix()


class S3Storage:
    """S3-compatible store (RustFS, MinIO, AWS S3, R2...) via the minio client library.

    The bucket policy allows anonymous reads of figures/* only.
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool, public_url: str):
        from minio import Minio

        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        self.public_base = public_url.rstrip("/")
        self._ready = False

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [
                        f"arn:aws:s3:::{self.bucket}/{PUBLIC_PREFIX}*",
                        f"arn:aws:s3:::{self.bucket}/{PREVIEW_PREFIX}*",
                    ],
                }
            ],
        }
        self.client.set_bucket_policy(self.bucket, json.dumps(policy))
        self._ready = True

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        def _put():
            self._ensure_bucket()
            self.client.put_object(self.bucket, _check_key(key), io.BytesIO(data), len(data), content_type=content_type)

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        from minio.error import S3Error

        def _get():
            self._ensure_bucket()
            response = self.client.get_object(self.bucket, _check_key(key))
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(_get)
        except S3Error as exc:
            raise StorageError(f"Not found: {key}") from exc

    async def iter_stream(self, key: str, chunk_size: int = 64 * 1024):
        from minio.error import S3Error
        self._ensure_bucket()
        try:
            response = self.client.get_object(self.bucket, _check_key(key))
        except S3Error as exc:
            raise StorageError(f"Not found: {key}") from exc

        try:
            while True:
                chunk = await asyncio.to_thread(response.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            response.close()
            response.release_conn()

    async def exists(self, key: str) -> bool:
        from minio.error import S3Error

        def _stat():
            self._ensure_bucket()
            self.client.stat_object(self.bucket, _check_key(key))

        try:
            await asyncio.to_thread(_stat)
            return True
        except S3Error:
            return False

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, _check_key(key))

    async def delete_prefix(self, prefix: str) -> None:
        def _delete_all():
            for obj in self.client.list_objects(self.bucket, prefix=_check_key(prefix), recursive=True):
                self.client.remove_object(self.bucket, obj.object_name)

        await asyncio.to_thread(_delete_all)

    def public_url(self, key: str) -> str:
        return f"{self.public_base}/{self.bucket}/{_check_key(key)}"

    def presigned_url(
        self, key: str, expires_seconds: int = 60, download_filename: str | None = None
    ) -> str | None:
        """Short-lived (default 60s) presigned S3 GET URL for private vault objects (exam-pro-vault / bronze-raw)."""
        self._ensure_bucket()
        headers = {}
        if download_filename:
            headers["response-content-disposition"] = f'attachment; filename="{download_filename}"'
        return self.client.presigned_get_object(
            self.bucket,
            _check_key(key),
            expires=timedelta(seconds=max(10, expires_seconds)),
            response_headers=headers or None,
        )


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            secure=settings.s3_secure,
            public_url=settings.s3_public_url,
        )
    return LocalStorage(Path(settings.data_dir) / "media")


@lru_cache
def get_bronze_storage() -> Storage:
    """Bronze layer of the crawler: original PDF / Word / image files, never served (bucket bronze-raw)."""
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bronze_bucket,
            secure=settings.s3_secure,
            public_url=settings.s3_public_url,
        )
    return LocalStorage(Path(settings.data_dir) / "bronze")


def media_url(key: str | None) -> str | None:
    """Public URL of a figure key (None stays None); used by templates and the paper API."""
    return get_storage().public_url(key) if key else None
