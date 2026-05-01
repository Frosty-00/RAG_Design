"""Cascade delete: wipe doc across Milvus + MinIO + Redis caches.

Best-effort cross-system delete — failures in any one step are logged but
don't block the other steps from running, ensuring no system is left
with orphan data.
"""
from __future__ import annotations

from app.core.logger import get_logger
from app.repositories.milvus import MilvusRepository
from app.repositories.minio_repo import MinioRepository, doc_prefix
from app.repositories.redis_repo import RedisRepository
from app.workers.celery_app import celery_app
from app.workers.tasks._helpers import now_iso, run_async

log = get_logger(__name__)


@celery_app.task(name="cascade_delete", bind=True)
def cascade_delete(
    self,
    *,
    doc_id: str,
    task_id: str,
    minio_keys: list[str] | None = None,
) -> dict:
    """Delete a doc across Milvus + MinIO + Redis.

    `minio_keys` is the snapshot of object keys captured by the API DELETE
    handler at decision time. When provided we delete *exactly those keys*
    rather than scanning by prefix — this is the only way to make
    delete-then-re-upload-with-same-content-hash safe (otherwise the
    deferred task would scrub the freshly-re-uploaded v1).

    Falls back to prefix scan if the caller didn't pass a snapshot
    (e.g. older callers / tests).
    """
    log.info("cascade_delete.start", task_id=task_id, doc_id=doc_id,
             pinned_keys=len(minio_keys) if minio_keys is not None else None)
    return run_async(_cascade_delete(
        doc_id=doc_id, task_id=task_id, minio_keys=minio_keys,
    ))


async def _cascade_delete(
    *,
    doc_id: str,
    task_id: str,
    minio_keys: list[str] | None = None,
) -> dict:
    redis = RedisRepository()
    summary: dict = {
        "task_id": task_id,
        "doc_id": doc_id,
        "milvus": "pending", "minio": "pending", "cache": "pending",
        "milvus_count": 0, "minio_count": 0,
        "errors": [],
    }
    await redis.set_task(task_id, {**summary, "updated_at": now_iso(),
                                   "status": "deleting"})

    # --- 1. Milvus ----------------------------------------------------------
    try:
        milvus = MilvusRepository()
        n = milvus.delete_by_doc(doc_id)
        milvus.client.flush(milvus.collection)
        summary["milvus"] = "ok"
        summary["milvus_count"] = n
    except Exception as e:  # noqa: BLE001
        log.error("cascade_delete.milvus_failed", doc_id=doc_id, err=str(e))
        summary["milvus"] = "failed"
        summary["errors"].append(f"milvus: {e}")

    # --- 2. MinIO -----------------------------------------------------------
    # Prefer the explicit key snapshot from the API handler — this is what
    # makes delete-then-re-upload-same-content safe. Without the snapshot a
    # blanket prefix scrub would wipe the new upload too. Fall back to
    # prefix only when the caller didn't provide one (legacy/tests).
    try:
        minio = MinioRepository()
        if minio_keys is not None:
            n = minio.delete_keys(minio_keys)
        else:
            n = minio.delete_prefix(doc_prefix(doc_id))
        summary["minio"] = "ok"
        summary["minio_count"] = n
    except Exception as e:  # noqa: BLE001
        log.error("cascade_delete.minio_failed", doc_id=doc_id, err=str(e))
        summary["minio"] = "failed"
        summary["errors"].append(f"minio: {e}")

    # --- 3. Redis caches ----------------------------------------------------
    # Retrieval caches are TTL-bound (30min) and keyed by query hash, not
    # doc_id, so they self-heal. Embedding caches are content-keyed too.
    # We only need to clear task records & per-doc state.
    try:
        # remove doc meta + owner index (so it disappears from list)
        await redis.delete_doc_meta(doc_id)
        # purge any DLQ entries for this doc (best-effort scan)
        async for key in redis.client.scan_iter(match=f"dlq:tasks:*"):
            payload = await redis._get_json(key)
            if payload and payload.get("kwargs", {}).get("doc_id") == doc_id:
                await redis.client.delete(key)
        # invalidate retrieval cache (Layer 6 — content-addressed, scrub all)
        async for key in redis.client.scan_iter(match="ret:*"):
            await redis.client.delete(key)
        summary["cache"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.error("cascade_delete.cache_failed", doc_id=doc_id, err=str(e))
        summary["cache"] = "failed"
        summary["errors"].append(f"cache: {e}")

    # --- final status -------------------------------------------------------
    final_status = "done" if not summary["errors"] else "partial"
    await redis.set_task(task_id, {**summary, "status": final_status,
                                   "updated_at": now_iso()})
    await redis.close()
    log.info("cascade_delete.done", task_id=task_id, doc_id=doc_id,
             status=final_status, milvus=summary["milvus_count"],
             minio=summary["minio_count"])
    return summary
