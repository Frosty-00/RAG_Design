"""Shared pytest fixtures.

Layer 2 tests need real Docker services up (milvus / redis / minio).
We use dedicated test resources so production state isn't touched:

  - Milvus collection : `test_kb_chunks`
  - MinIO bucket      : `test-rag-bucket`
  - **Redis DB        : 15**  ← isolated from prod (DB 0). Critical:
                              avoids wiping the prod `auth:token:*` /
                              `docs:meta:*` namespaces during test runs.

The session-scoped autouse fixture below does an additional sweep
inside DB 15 just to keep test-to-test bleed-through clean.
"""
from __future__ import annotations

import os
import random

# Isolation BEFORE any app import (so settings/lru_cache picks these up)
os.environ.setdefault("MILVUS_COLLECTION", "test_kb_chunks")
os.environ.setdefault("MINIO_BUCKET", "test-rag-bucket")
os.environ["REDIS_DB"] = "15"  # always, never share with prod

import pytest
import redis as sync_redis

from app.core.config import settings
from app.repositories.milvus import MilvusRepository
from app.repositories.minio_repo import MinioRepository
from app.repositories.redis_repo import RedisRepository

random.seed(42)


# Prefixes touched by integration tests. Any key matching these is fair
# game for cleanup at session start AND end.
_TEST_REDIS_PREFIXES = (
    "task:",            # Layer 5 ingest / cascade task statuses
    "docs:meta:",       # Layer 9 doc-meta index (testing DocumentService)
    "docs:owned:",      # Layer 9 owner-set index
    "session:",         # Layer 9 chat sessions
    "auth:token:",      # Layer 9 token store
    "auth:user:",
    "usage:",           # LLM token counters
    "dlq:tasks:",       # Layer 5 DLQ
    "idem:",            # Idempotency keys
    "ret:",             # Layer 6 retrieval cache
    "emb:",             # Layer 6 embedding cache
    "eval:run:",        # Layer 11 eval runs
    "rl:",              # Layer 15 rate limit counters
)


def _purge_test_keys() -> int:
    """Synchronous Redis purge of all test-touched namespaces."""
    c = sync_redis.Redis(
        host=settings.redis_host, port=settings.redis_port, db=settings.redis_db,
    )
    n = 0
    try:
        for prefix in _TEST_REDIS_PREFIXES:
            for key in c.scan_iter(match=f"{prefix}*"):
                c.delete(key)
                n += 1
        # eval:runs is a set, drop the whole thing
        c.delete("eval:runs")
    finally:
        c.close()
    return n


@pytest.fixture(scope="session", autouse=True)
def _purge_redis_around_session():
    """Drop test-touched Redis keys before AND after the test session.

    Critical: prevents tests from leaking ghost documents into the
    production UI (we share the same Redis instance for dev + tests).
    """
    _purge_test_keys()
    yield
    _purge_test_keys()


@pytest.fixture
def milvus_repo() -> MilvusRepository:
    repo = MilvusRepository()
    repo.drop_collection()
    repo.ensure_collection()
    yield repo
    repo.drop_collection()


@pytest.fixture
def minio_repo() -> MinioRepository:
    repo = MinioRepository()
    repo.ensure_bucket()
    # clean any leftover test objects
    repo.delete_prefix("")
    yield repo
    repo.delete_prefix("")


@pytest.fixture
async def redis_repo() -> RedisRepository:
    # Per-test cleanup of the prefixes we know we mutate. Belt-and-suspenders
    # alongside the session-scoped purge above.
    repo = RedisRepository()
    yield repo
    for prefix in _TEST_REDIS_PREFIXES:
        async for key in repo.client.scan_iter(match=f"{prefix}*"):
            await repo.client.delete(key)
    await repo.close()
