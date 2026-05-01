"""Layer 5: Celery ingest + cascade_delete + DLQ + idempotency + versioning.

Uses Celery eager mode so tasks run synchronously inside the test process.
Real Milvus / MinIO / Redis are required (docker compose up).

Note on async/sync handling:
  Each test's helpers create a fresh `RedisRepository` inside its own
  `asyncio.run()` call. Reusing an async client across multiple
  `asyncio.run()` invocations triggers "Event loop is closed" because the
  redis-py async client binds its connection pool to the loop where it
  first ran.

Run:
    pytest tests/integration/test_workers.py -v
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Awaitable, TypeVar

import pytest

from app.repositories.milvus import MilvusRepository, Requester
from app.repositories.minio_repo import MinioRepository, doc_key, doc_prefix
from app.repositories.redis_repo import RedisRepository
from app.workers.celery_app import celery_app
from app.workers.tasks.cascade_delete import cascade_delete
from app.workers.tasks.ingest import ingest_document

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────
# Eager mode for tests — tasks run synchronously
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _eager_celery():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _with_redis(coro_factory) -> object:
    """Run an async coroutine that needs a fresh RedisRepository.

    The factory takes a `RedisRepository` and returns a coroutine. We close
    the repo deterministically, all in a single `asyncio.run` call.
    """
    async def runner():
        r = RedisRepository()
        try:
            return await coro_factory(r)
        finally:
            await r.close()
    return asyncio.run(runner())


def _upload_sample_md(minio_repo: MinioRepository, doc_id: str, version: int = 1) -> str:
    content = (
        "# Test Doc\n\n"
        "This is the introduction.\n\n"
        "## Section A\n\n"
        "Content of section A about machine learning fundamentals and tasks.\n\n"
        "## Section B\n\n"
        "Content of section B about retrieval augmented generation systems.\n"
    ).encode("utf-8")
    key = doc_key(doc_id, version, "test.md")
    minio_repo.put_object(key, content, content_type="text/markdown")
    return key


# ─────────────────────────────────────────────────────────────────────
# Ingest happy-path
# ─────────────────────────────────────────────────────────────────────


class TestIngest:
    def test_ingest_creates_chunks_in_milvus(
        self,
        milvus_repo: MilvusRepository,
        minio_repo: MinioRepository,
    ):
        doc_id = "doc-ingest-1"
        task_id = "task-" + uuid.uuid4().hex[:8]
        key = _upload_sample_md(minio_repo, doc_id, 1)

        result = ingest_document.apply(kwargs={
            "task_id": task_id,
            "doc_id": doc_id,
            "version": 1,
            "file_key": key,
            "filename": "test.md",
            "owner_id": "alice",
            "acl": {"public": False, "users": [], "groups": []},
        }).get()

        assert result["status"] == "done"
        assert result["n_chunks"] >= 1

        milvus_repo.client.flush(milvus_repo.collection)
        assert milvus_repo.count(doc_id=doc_id, only_latest=True) >= 1

        # Owner can retrieve via hybrid search
        from app.services.embedding import BGEM3Embedder
        q = BGEM3Embedder.get().encode_query("machine learning")
        results = milvus_repo.hybrid_search(
            dense=q["dense"], sparse=q["sparse"], top_k=10,
            requester=Requester(user_id="alice"),
        )
        assert doc_id in {hit["entity"]["doc_id"] for hit in results[0]}

    def test_ingest_idempotency_blocks_concurrent(
        self,
        milvus_repo: MilvusRepository,
        minio_repo: MinioRepository,
    ):
        doc_id = "doc-idem-1"
        task_id = "task-" + uuid.uuid4().hex[:8]
        key = _upload_sample_md(minio_repo, doc_id, 1)
        idem_key = f"ingest:{doc_id}:v1"

        # Pre-acquire the lock from a totally separate redis call
        acquired = _with_redis(lambda r: r.acquire_idempotency(idem_key, ttl=120))
        assert acquired

        try:
            result = ingest_document.apply(kwargs={
                "task_id": task_id, "doc_id": doc_id, "version": 1,
                "file_key": key, "filename": "test.md",
                "owner_id": "alice",
                "acl": {"public": False, "users": [], "groups": []},
            }).get()
            assert result["status"] == "skipped_idempotent"

            milvus_repo.client.flush(milvus_repo.collection)
            assert milvus_repo.count(doc_id=doc_id) == 0
        finally:
            _with_redis(lambda r: r.release_idempotency(idem_key))

    def test_ingest_versioning_demotes_v1(
        self,
        milvus_repo: MilvusRepository,
        minio_repo: MinioRepository,
    ):
        doc_id = "doc-ver-1"
        # v1
        ingest_document.apply(kwargs={
            "task_id": "t1", "doc_id": doc_id, "version": 1,
            "file_key": _upload_sample_md(minio_repo, doc_id, 1),
            "filename": "test.md",
            "owner_id": "alice",
            "acl": {"public": False, "users": [], "groups": []},
        }).get()
        milvus_repo.client.flush(milvus_repo.collection)

        # v2
        ingest_document.apply(kwargs={
            "task_id": "t2", "doc_id": doc_id, "version": 2,
            "file_key": _upload_sample_md(minio_repo, doc_id, 2),
            "filename": "test.md",
            "owner_id": "alice",
            "acl": {"public": False, "users": [], "groups": []},
        }).get()
        milvus_repo.client.flush(milvus_repo.collection)

        total = milvus_repo.count(doc_id=doc_id)
        latest = milvus_repo.count(doc_id=doc_id, only_latest=True)
        assert total > 0
        assert latest > 0
        assert latest <= total

        # Search must only return v2
        from app.services.embedding import BGEM3Embedder
        q = BGEM3Embedder.get().encode_query("retrieval augmented generation")
        results = milvus_repo.hybrid_search(
            dense=q["dense"], sparse=q["sparse"], top_k=10,
            requester=Requester(user_id="root", is_admin=True),
            extra_filter=f'doc_id == "{doc_id}"',
        )
        versions = {hit["entity"].get("doc_version") for hit in results[0]}
        assert versions == {2}

    def test_dlq_writes_when_retries_exhausted(self):
        task_id = "task-fail-" + uuid.uuid4().hex[:8]
        ingest_document.push_request(
            id=task_id, retries=ingest_document.max_retries
        )
        try:
            ingest_document.on_failure(
                RuntimeError("parser exploded"),
                task_id, args=[], kwargs={"doc_id": "doc-x", "version": 1},
                einfo=None,
            )
        finally:
            ingest_document.pop_request()

        dlq_payload = _with_redis(lambda r: r._get_json(f"dlq:tasks:{task_id}"))
        assert dlq_payload is not None
        assert dlq_payload["task"] == "ingest_document"
        assert "parser exploded" in dlq_payload["error"]
        assert dlq_payload["kwargs"]["doc_id"] == "doc-x"
        # cleanup
        _with_redis(lambda r: r.client.delete(f"dlq:tasks:{task_id}"))

    def test_dlq_skipped_when_not_yet_exhausted(self):
        task_id = "task-noret-" + uuid.uuid4().hex[:8]
        ingest_document.push_request(id=task_id, retries=0)
        try:
            ingest_document.on_failure(
                RuntimeError("transient"),
                task_id, [], {"doc_id": "doc-x"}, einfo=None,
            )
        finally:
            ingest_document.pop_request()

        dlq_payload = _with_redis(lambda r: r._get_json(f"dlq:tasks:{task_id}"))
        assert dlq_payload is None


# ─────────────────────────────────────────────────────────────────────
# Cascade delete
# ─────────────────────────────────────────────────────────────────────


class TestCascadeDelete:
    def test_cascade_clears_milvus_and_minio(
        self,
        milvus_repo: MilvusRepository,
        minio_repo: MinioRepository,
    ):
        doc_id = "doc-cascade-1"
        # 1. ingest
        ingest_document.apply(kwargs={
            "task_id": "ti-cascade", "doc_id": doc_id, "version": 1,
            "file_key": _upload_sample_md(minio_repo, doc_id, 1),
            "filename": "test.md",
            "owner_id": "alice",
            "acl": {"public": False, "users": [], "groups": []},
        }).get()
        milvus_repo.client.flush(milvus_repo.collection)
        assert milvus_repo.count(doc_id=doc_id) >= 1
        assert list(minio_repo.list_prefix(doc_prefix(doc_id))) != []

        # 2. cascade delete
        result = cascade_delete.apply(kwargs={
            "doc_id": doc_id, "task_id": "td-cascade",
        }).get()
        milvus_repo.client.flush(milvus_repo.collection)

        assert result["milvus"] == "ok"
        assert result["minio"] == "ok"
        assert result["cache"] == "ok"
        assert result["milvus_count"] >= 1
        assert result["minio_count"] >= 1

        assert milvus_repo.count(doc_id=doc_id) == 0
        assert list(minio_repo.list_prefix(doc_prefix(doc_id))) == []

        task_status = _with_redis(lambda r: r.get_task("td-cascade"))
        assert task_status is not None
        assert task_status["status"] == "done"
