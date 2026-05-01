"""Integration tests for Layer 2 repositories.

Run requirements: docker compose deps must be up
    docker compose ps   # milvus + redis + minio all healthy

Run:
    pytest tests/integration/test_repositories.py -v
"""
from __future__ import annotations

import hashlib
import os
import random
import uuid

import pytest

from app.repositories.milvus import (
    DENSE_DIM,
    Chunk,
    MilvusRepository,
    Requester,
)
from app.repositories.minio_repo import MinioRepository, doc_key, doc_prefix
from app.repositories.redis_repo import RedisRepository


# ─────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────

def _rand_dense() -> list[float]:
    return [random.gauss(0, 1) for _ in range(DENSE_DIM)]


def _rand_sparse(n: int = 5) -> dict[int, float]:
    return {random.randint(1, 50_000): random.random() for _ in range(n)}


def _make_chunk(
    *,
    doc_id: str,
    version: int = 1,
    chunk_idx: int = 0,
    is_latest: bool = True,
    owner_id: str = "alice",
    acl_users: list[str] | None = None,
    acl_groups: list[str] | None = None,
    public: bool = False,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}:v{version}:c{chunk_idx}",
        doc_id=doc_id,
        doc_version=version,
        is_latest=is_latest,
        text=text or f"chunk text for {doc_id} v{version} c{chunk_idx}",
        owner_id=owner_id,
        acl={
            "public": public,
            "users": acl_users or [],
            "groups": acl_groups or [],
        },
        dense=_rand_dense(),
        sparse=_rand_sparse(),
        metadata={"page": chunk_idx + 1},
    )


# ─────────────────────────────────────────────────────────────────────
# Milvus tests
# ─────────────────────────────────────────────────────────────────────

class TestMilvusRepository:
    def test_ensure_collection_idempotent(self, milvus_repo: MilvusRepository):
        # Calling twice must not error
        milvus_repo.ensure_collection()
        milvus_repo.ensure_collection()
        assert milvus_repo.client.has_collection(milvus_repo.collection)

    def test_insert_and_count(self, milvus_repo: MilvusRepository):
        chunks = [_make_chunk(doc_id="doc-A", chunk_idx=i) for i in range(5)]
        milvus_repo.insert(chunks)
        milvus_repo.client.flush(milvus_repo.collection)

        assert milvus_repo.count(doc_id="doc-A") == 5
        assert milvus_repo.count(doc_id="doc-A", only_latest=True) == 5
        assert milvus_repo.count(doc_id="doc-missing") == 0

    def test_acl_filter_visibility(self, milvus_repo: MilvusRepository):
        # Seed: 3 documents with different ACL profiles
        chunks = [
            _make_chunk(doc_id="pub",   chunk_idx=0, owner_id="bob",   public=True),
            _make_chunk(doc_id="alice", chunk_idx=0, owner_id="alice", public=False),
            _make_chunk(doc_id="qa-grp", chunk_idx=0, owner_id="bob", acl_groups=["qa"]),
        ]
        milvus_repo.insert(chunks)
        milvus_repo.client.flush(milvus_repo.collection)

        # alice sees: pub (public) + alice (owner) — NOT qa-grp
        alice = Requester(user_id="alice", groups=[])
        results = milvus_repo.hybrid_search(
            dense=_rand_dense(), sparse=_rand_sparse(), top_k=10, requester=alice,
        )
        seen_doc_ids = {hit["entity"]["doc_id"] for hit in results[0]}
        assert "pub" in seen_doc_ids
        assert "alice" in seen_doc_ids
        assert "qa-grp" not in seen_doc_ids

        # carol in qa group sees: pub + qa-grp — NOT alice
        carol = Requester(user_id="carol", groups=["qa"])
        results = milvus_repo.hybrid_search(
            dense=_rand_dense(), sparse=_rand_sparse(), top_k=10, requester=carol,
        )
        seen_doc_ids = {hit["entity"]["doc_id"] for hit in results[0]}
        assert "pub" in seen_doc_ids
        assert "qa-grp" in seen_doc_ids
        assert "alice" not in seen_doc_ids

        # admin sees everything
        admin = Requester(user_id="root", is_admin=True)
        results = milvus_repo.hybrid_search(
            dense=_rand_dense(), sparse=_rand_sparse(), top_k=10, requester=admin,
        )
        seen_doc_ids = {hit["entity"]["doc_id"] for hit in results[0]}
        assert seen_doc_ids == {"pub", "alice", "qa-grp"}

        # anonymous sees only public
        anon_results = milvus_repo.hybrid_search(
            dense=_rand_dense(), sparse=_rand_sparse(), top_k=10, requester=None,
        )
        seen = {hit["entity"]["doc_id"] for hit in anon_results[0]}
        assert seen == {"pub"}

    def test_doc_versioning(self, milvus_repo: MilvusRepository):
        # v1: 3 chunks
        v1 = [_make_chunk(doc_id="rev", version=1, chunk_idx=i) for i in range(3)]
        milvus_repo.insert(v1)
        milvus_repo.client.flush(milvus_repo.collection)
        assert milvus_repo.count(doc_id="rev", only_latest=True) == 3

        # v2: 2 new chunks (new content) — caller responsibility:
        # 1. insert new version with is_latest=true
        # 2. demote prior versions
        v2 = [_make_chunk(doc_id="rev", version=2, chunk_idx=i) for i in range(2)]
        milvus_repo.insert(v2)
        milvus_repo.mark_old_versions_inactive("rev", keep_version=2)
        milvus_repo.client.flush(milvus_repo.collection)

        # Now: v1 chunks still exist but is_latest=false; v2 is the latest
        assert milvus_repo.count(doc_id="rev") == 5
        assert milvus_repo.count(doc_id="rev", only_latest=True) == 2

        # Retrieval (admin) only returns is_latest=true
        admin = Requester(user_id="root", is_admin=True)
        results = milvus_repo.hybrid_search(
            dense=_rand_dense(), sparse=_rand_sparse(), top_k=10, requester=admin,
            extra_filter='doc_id == "rev"',
        )
        versions = {hit["entity"]["doc_version"] for hit in results[0]}
        assert versions == {2}

    def test_delete_by_doc_cascades_all_versions(self, milvus_repo: MilvusRepository):
        # seed v1 + v2
        milvus_repo.insert([
            _make_chunk(doc_id="rm", version=1, chunk_idx=0, is_latest=False),
            _make_chunk(doc_id="rm", version=2, chunk_idx=0),
            _make_chunk(doc_id="keep", version=1, chunk_idx=0),
        ])
        milvus_repo.client.flush(milvus_repo.collection)
        assert milvus_repo.count(doc_id="rm") == 2

        deleted = milvus_repo.delete_by_doc("rm")
        milvus_repo.client.flush(milvus_repo.collection)

        assert deleted == 2
        assert milvus_repo.count(doc_id="rm") == 0
        assert milvus_repo.count(doc_id="keep") == 1


# ─────────────────────────────────────────────────────────────────────
# Redis tests
# ─────────────────────────────────────────────────────────────────────

class TestRedisRepository:
    async def test_ping(self, redis_repo: RedisRepository):
        assert await redis_repo.ping() is True

    async def test_task_set_get_delete(self, redis_repo: RedisRepository):
        tid = "test-task-1"
        await redis_repo.set_task(tid, {"status": "pending", "doc_id": "doc-1"})
        got = await redis_repo.get_task(tid)
        assert got == {"status": "pending", "doc_id": "doc-1"}

        assert await redis_repo.delete_task(tid) == 1
        assert await redis_repo.get_task(tid) is None

    async def test_session_history(self, redis_repo: RedisRepository):
        sid = "test-sess-1"
        await redis_repo.append_session_turn(sid, "user", "hello")
        await redis_repo.append_session_turn(sid, "assistant", "hi there")
        history = await redis_repo.get_session(sid)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "hi there"

    async def test_token_lifecycle(self, redis_repo: RedisRepository):
        token = "test-secret-token-" + uuid.uuid4().hex
        h = await redis_repo.store_token(
            token, user_id="test-user-1", groups=["qa"], role="user"
        )
        assert len(h) == 64
        info = await redis_repo.lookup_token(token)
        assert info["user_id"] == "test-user-1"
        assert info["groups"] == ["qa"]
        assert info["role"] == "user"

        # revoke
        assert await redis_repo.revoke_token(token) == 1
        assert await redis_repo.lookup_token(token) is None

    async def test_usage_counters(self, redis_repo: RedisRepository):
        u, s = await redis_repo.incr_usage(
            user_id="test-user-1", session_id="test-sess-1", tokens=100
        )
        assert u == 100 and s == 100

        u, s = await redis_repo.incr_usage(
            user_id="test-user-1", session_id="test-sess-1", tokens=50
        )
        assert u == 150 and s == 150
        assert await redis_repo.get_user_usage("test-user-1") == 150

    async def test_idempotency(self, redis_repo: RedisRepository):
        key = "test-task:" + uuid.uuid4().hex
        assert await redis_repo.acquire_idempotency(key, ttl=60) is True
        # second attempt within TTL must fail
        assert await redis_repo.acquire_idempotency(key, ttl=60) is False
        # release → can re-acquire
        await redis_repo.release_idempotency(key)
        assert await redis_repo.acquire_idempotency(key, ttl=60) is True


# ─────────────────────────────────────────────────────────────────────
# MinIO tests
# ─────────────────────────────────────────────────────────────────────

class TestMinioRepository:
    def test_ensure_bucket_idempotent(self, minio_repo: MinioRepository):
        minio_repo.ensure_bucket()
        minio_repo.ensure_bucket()
        assert minio_repo.client.bucket_exists(minio_repo.bucket)

    def test_put_get_hash_consistency(self, minio_repo: MinioRepository):
        data = os.urandom(1024 * 1024)  # 1 MiB random
        digest_in = hashlib.md5(data).hexdigest()

        key = doc_key("doc-1", 1, "sample.bin")
        minio_repo.put_object(key, data, content_type="application/octet-stream")

        roundtrip = minio_repo.get_object(key)
        digest_out = hashlib.md5(roundtrip).hexdigest()
        assert digest_in == digest_out
        assert len(roundtrip) == len(data)

        # stat
        s = minio_repo.stat(key)
        assert s["size"] == len(data)
        assert minio_repo.stat("nope/missing") is None

        # delete
        minio_repo.delete_object(key)
        assert minio_repo.stat(key) is None

    def test_delete_prefix_cascade(self, minio_repo: MinioRepository):
        # 3 versions of the same doc + an unrelated one
        for v in (1, 2, 3):
            minio_repo.put_object(doc_key("doc-X", v, "f.bin"), b"v" + str(v).encode())
        minio_repo.put_object(doc_key("doc-Y", 1, "f.bin"), b"keep")

        deleted = minio_repo.delete_prefix(doc_prefix("doc-X"))
        assert deleted == 3
        # doc-X gone, doc-Y intact
        assert minio_repo.stat(doc_key("doc-Y", 1, "f.bin")) is not None
        assert list(minio_repo.list_prefix(doc_prefix("doc-X"))) == []
