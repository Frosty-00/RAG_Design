"""Celery application — single instance shared by all task modules.

Run a worker (in conda env `self_RAG_2`):

    celery -A app.workers.celery_app worker -l info -P solo

Routing:
  - Default tasks → "default" queue
  - Tasks that exhausted retries are written to Redis under
    `dlq:tasks:{task_id}` via `Task.on_failure`. There's no separate
    Celery DLQ queue (we don't need re-execution from there — admin API
    re-invokes the original task signature in Layer 9).
"""
from __future__ import annotations

from celery import Celery
from celery.signals import task_postrun, task_prerun

from app.core.config import settings
from app.core.logger import set_request_id

celery_app = Celery(
    "self_rag",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.ingest",
        "app.workers.tasks.cascade_delete",
        "app.workers.tasks.eval",
    ],
)

celery_app.conf.update(
    # Reliability ---------------------------------------------------------
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,

    # Serialization -------------------------------------------------------
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=60 * 60 * 24,  # 1 day

    # Time / locale -------------------------------------------------------
    timezone="UTC",
    enable_utc=True,

    # Routing -------------------------------------------------------------
    task_default_queue="default",
)


# ─── request_id propagation ─────────────────────────────────────────
# API logs include the Celery `task_id` returned by apply_async, and worker
# logs are tagged with the same id via these signal handlers — so a single
# `grep <task_id>` covers both sides without needing to pipe headers around.

@task_prerun.connect
def _set_rid_on_task_start(task_id=None, task=None, **_):  # noqa: D401
    rid: str | None = None
    try:
        headers = getattr(task.request, "headers", None) or {}
        rid = headers.get("request_id") if isinstance(headers, dict) else None
    except Exception:  # noqa: BLE001
        rid = None
    set_request_id(rid or (task_id or "")[:16] or None)


@task_postrun.connect
def _clear_rid_on_task_end(**_):
    set_request_id(None)
