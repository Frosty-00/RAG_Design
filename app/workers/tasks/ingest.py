"""Document ingestion task.

Pipeline (single Celery task, sequential):
  1. acquire idempotency lock by (doc_id, version)
  2. update task status: downloading → parsing → embedding → inserting → done
  3. demote prior versions (`mark_old_versions_inactive`)
  4. release idempotency lock
On exhausted retries, `IngestTask.on_failure` writes to Redis DLQ.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from celery import Task

from app.core.logger import get_logger
from app.repositories.milvus import Chunk, MilvusRepository
from app.repositories.minio_repo import MinioRepository
from app.repositories.redis_repo import RedisRepository
from app.services.chunking import chunk_documents
from app.services.embedding import BGEM3Embedder
from app.services.parsing import parse_file
from app.workers.celery_app import celery_app
from app.workers.tasks._helpers import now_iso, run_async

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Task class with DLQ on exhausted retries
# ─────────────────────────────────────────────────────────────────────


class IngestTask(Task):
    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: D401
        """Final failure (after retries exhausted) → write to Redis DLQ."""
        if self.request.retries < self.max_retries:
            return  # not yet exhausted; will retry
        try:
            run_async(_write_dlq(task_id, exc, args, kwargs, einfo))
        except Exception as dlq_err:  # noqa: BLE001
            log.error("dlq.write_failed", task_id=task_id, err=str(dlq_err))


async def _write_dlq(task_id, exc, args, kwargs, einfo):
    redis = RedisRepository()
    try:
        await redis.push_dlq(task_id, {
            "task": "ingest_document",
            "args": list(args) if args else [],
            "kwargs": kwargs or {},
            "error": str(exc),
            "traceback": (einfo.traceback if einfo else None),
            "failed_at": now_iso(),
        })
    finally:
        await redis.close()


# ─────────────────────────────────────────────────────────────────────
# Task entrypoint
# ─────────────────────────────────────────────────────────────────────


@celery_app.task(base=IngestTask, name="ingest_document", bind=True)
def ingest_document(
    self,
    *,
    task_id: str,
    doc_id: str,
    version: int,
    file_key: str,
    filename: str,
    owner_id: str,
    acl: dict[str, Any] | None = None,
) -> dict:
    """Synchronous wrapper — actual work in `_ingest_async`."""
    log.info("ingest.start", task_id=task_id, doc_id=doc_id, version=version)
    return run_async(_ingest_async(
        task_id=task_id,
        doc_id=doc_id,
        version=version,
        file_key=file_key,
        filename=filename,
        owner_id=owner_id,
        acl=acl or {"public": False, "users": [], "groups": []},
    ))


async def _ingest_async(
    *,
    task_id: str,
    doc_id: str,
    version: int,
    file_key: str,
    filename: str,
    owner_id: str,
    acl: dict[str, Any],
) -> dict:
    redis = RedisRepository()
    idem_key = f"ingest:{doc_id}:v{version}"
    acquired = False
    n_chunks = 0
    try:
        # ----- idempotency lock --------------------------------------------------
        acquired = await redis.acquire_idempotency(idem_key, ttl=3600)
        if not acquired:
            log.info("ingest.skip_idempotent", task_id=task_id, doc_id=doc_id)
            return {"task_id": task_id, "status": "skipped_idempotent",
                    "doc_id": doc_id}

        await _set_status(redis, task_id, doc_id, version, "pending")

        # ----- download from MinIO ----------------------------------------------
        await _set_status(redis, task_id, doc_id, version, "downloading")
        minio = MinioRepository()
        data = minio.get_object(file_key)

        # write to a temp file (parsers operate on paths)
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td) / filename
            tmp_path.write_bytes(data)

            # ----- parse --------------------------------------------------------
            await _set_status(redis, task_id, doc_id, version, "parsing")
            docs = parse_file(tmp_path)
            for d in docs:
                d.metadata["doc_id"] = doc_id
                d.metadata["doc_version"] = version

            # ----- chunk --------------------------------------------------------
            nodes = chunk_documents(docs)
            if not nodes:
                raise RuntimeError(f"no chunks produced for {filename}")

            # ----- embed --------------------------------------------------------
            await _set_status(redis, task_id, doc_id, version, "embedding")
            embedder = BGEM3Embedder.get()
            texts = [n.text for n in nodes]
            emb = embedder.encode(texts, batch_size=16)

            # ----- build Chunks -------------------------------------------------
            chunks: list[Chunk] = []
            for n, dense, sparse in zip(nodes, emb["dense"], emb["sparse"]):
                stripped_md = {
                    k: v for k, v in n.metadata.items()
                    if k not in {"chunk_id", "doc_id", "doc_version"}
                }
                chunks.append(Chunk(
                    chunk_id=n.metadata["chunk_id"],
                    doc_id=doc_id,
                    doc_version=version,
                    is_latest=True,
                    text=n.text,
                    owner_id=owner_id,
                    acl=acl,
                    dense=dense,
                    sparse=sparse,
                    metadata=stripped_md,
                ))

            # ----- insert + version demote --------------------------------------
            await _set_status(redis, task_id, doc_id, version, "inserting")
            milvus = MilvusRepository()
            milvus.ensure_collection()
            milvus.insert(chunks)
            milvus.client.flush(milvus.collection)
            if version > 1:
                milvus.mark_old_versions_inactive(doc_id, keep_version=version)
                milvus.client.flush(milvus.collection)
            n_chunks = len(chunks)

        # ----- success -----------------------------------------------------------
        await _set_status(redis, task_id, doc_id, version, "done", n_chunks=n_chunks)
        # Persist doc-meta + owner index so the API can list/look up by owner
        await redis.set_doc_meta(doc_id, {
            "doc_id": doc_id,
            "filename": filename,
            "owner_id": owner_id,
            "acl": acl,
            "latest_version": version,
            "latest_status": "done",
            "n_chunks": n_chunks,
            "latest_task_id": task_id,
            "updated_at": now_iso(),
        })
        log.info("ingest.done", task_id=task_id, doc_id=doc_id, n_chunks=n_chunks)
        return {"task_id": task_id, "status": "done",
                "doc_id": doc_id, "version": version, "n_chunks": n_chunks}

    except Exception as e:  # noqa: BLE001
        await _set_status(redis, task_id, doc_id, version, "failed", error=str(e))
        # Persist a `failed` doc-meta row so the UI lists it (with a red
        # status badge + error string) instead of silently dropping it.
        try:
            await redis.set_doc_meta(doc_id, {
                "doc_id": doc_id,
                "filename": filename,
                "owner_id": owner_id,
                "acl": acl,
                "latest_version": version,
                "latest_status": "failed",
                "n_chunks": n_chunks,  # 0 if we never got that far
                "latest_task_id": task_id,
                "error": str(e),
                "updated_at": now_iso(),
            })
        except Exception as meta_err:  # noqa: BLE001
            log.error("ingest.meta_write_failed",
                      task_id=task_id, err=str(meta_err))
        log.error("ingest.failed", task_id=task_id, doc_id=doc_id, err=str(e))
        raise
    finally:
        if acquired:
            await redis.release_idempotency(idem_key)
        await redis.close()


async def _set_status(
    redis: RedisRepository,
    task_id: str,
    doc_id: str,
    version: int,
    status: str,
    *,
    n_chunks: int | None = None,
    error: str | None = None,
) -> None:
    payload = {
        "task_id": task_id,
        "doc_id": doc_id,
        "version": version,
        "status": status,
        "updated_at": now_iso(),
    }
    if n_chunks is not None:
        payload["n_chunks"] = n_chunks
    if error is not None:
        payload["error"] = error
    await redis.set_task(task_id, payload)
