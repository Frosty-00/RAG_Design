"""MinIO (S3-compatible) repository.

Object key convention:
    docs/{doc_id}/v{version}/{filename}

Cascade delete uses prefix `docs/{doc_id}/` to wipe all versions.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta

from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


def doc_key(
    doc_id: str,
    version: int,
    filename: str,
    *,
    salt: str | None = None,
) -> str:
    """Build the MinIO object key for a single uploaded file.

    `salt` is an optional per-upload unique token. Without it, deleting a
    doc and immediately re-uploading the same content collides on the
    *exact same key* (same content hash → same doc_id, version resets to
    1) and the deferred cascade_delete task wipes the freshly-written
    file. With a salt, every upload writes to a physically distinct path
    so the deferred cleanup can only ever target the keys it captured at
    decision time.
    """
    seg = f"v{version}-{salt}" if salt else f"v{version}"
    return f"docs/{doc_id}/{seg}/{filename}"


def doc_prefix(doc_id: str) -> str:
    return f"docs/{doc_id}/"


class MinioRepository:
    """Synchronous MinIO client. Most callers run inside Celery worker threads
    or FastAPI threadpool, so async wrapping isn't worth it here."""

    def __init__(self, bucket: str | None = None) -> None:
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_use_ssl,
        )
        self._bucket = bucket or settings.minio_bucket

    @property
    def client(self) -> Minio:
        return self._client

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            log.info("minio.bucket.created", bucket=self._bucket)

    # ---------------------------------------------------------------- objects

    def put_object(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Returns the etag of the stored object."""
        result = self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        log.info("minio.put", key=key, size=len(data))
        return result.etag

    def get_object(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat(self, key: str) -> dict | None:
        try:
            s = self._client.stat_object(self._bucket, key)
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise
        return {"size": s.size, "etag": s.etag, "content_type": s.content_type,
                "last_modified": s.last_modified}

    def delete_object(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)
        log.info("minio.delete", key=key)

    def list_prefix(self, prefix: str) -> Iterator[str]:
        for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
            yield obj.object_name

    def delete_prefix(self, prefix: str) -> int:
        """Cascade delete: remove every object under `prefix`. Used by
        DocumentService.delete to wipe all versions of a document."""
        keys = list(self.list_prefix(prefix))
        if not keys:
            return 0
        errors = list(self._client.remove_objects(
            self._bucket, (DeleteObject(k) for k in keys)
        ))
        for err in errors:
            log.error("minio.delete_prefix.error", key=err.object_name, err=str(err.error_message))
        log.info("minio.delete_prefix", prefix=prefix, count=len(keys))
        return len(keys)

    def delete_keys(self, keys: list[str]) -> int:
        """Delete an explicit list of keys — race-free alternative to
        `delete_prefix`. The caller is expected to enumerate keys at the
        moment of decision (e.g. inside the DELETE handler) so a re-upload
        landing on the same `doc_id` after the queue dispatch does NOT get
        scrubbed by the deferred cascade task. See
        `app/api/v1/documents.py::delete_document`.
        """
        if not keys:
            return 0
        errors = list(self._client.remove_objects(
            self._bucket, (DeleteObject(k) for k in keys),
        ))
        for err in errors:
            log.error("minio.delete_keys.error",
                      key=err.object_name, err=str(err.error_message))
        log.info("minio.delete_keys", count=len(keys))
        return len(keys)

    def presign_get(self, key: str, *, expiry_seconds: int = 3600) -> str:
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=expiry_seconds)
        )
