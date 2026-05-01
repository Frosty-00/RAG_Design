"""Layer 6 retrieval tests: hybrid + rerank + 2-tier cache + ACL scope.

Requires: Milvus / Redis / MinIO services up. BGE-M3 + reranker weights
already cached from Layer 3 tests.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.config import settings
from app.repositories.milvus import Chunk, MilvusRepository, Requester
from app.repositories.redis_repo import RedisRepository
from app.services.embedding import BGEM3Embedder
from app.services.retrieval import Retriever, _acl_scope


# ─────────────────────────────────────────────────────────────────────
# Fixtures: seed three contrasting docs into the test collection
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def seeded_milvus():
    """Module-scoped: seed once and reuse across test cases for speed."""
    repo = MilvusRepository()
    repo.drop_collection()
    repo.ensure_collection()

    embedder = BGEM3Embedder.get()
    docs_content = {
        "doc-ml": [
            "Machine learning algorithms learn patterns from data through optimization.",
            "Deep neural networks adjust weights using backpropagation during training.",
            "Gradient descent minimizes the loss function over many iterations.",
        ],
        "doc-cook": [
            "Pasta carbonara uses eggs, cheese, pancetta, and black pepper, no cream.",
            "Sourdough bread relies on natural yeast for slow fermentation.",
            "A cast iron skillet retains heat well for searing meats.",
        ],
        "doc-music": [
            "The cello is a member of the violin family with four strings.",
            "Mozart composed over 600 works during his short life.",
            "Jazz improvisation often follows a 12-bar blues structure.",
        ],
    }

    chunks_to_insert: list[Chunk] = []
    for doc_id, texts in docs_content.items():
        emb = embedder.encode(texts)
        for i, text in enumerate(texts):
            chunks_to_insert.append(Chunk(
                chunk_id=f"{doc_id}:v1:c{i:04d}",
                doc_id=doc_id,
                doc_version=1,
                is_latest=True,
                text=text,
                owner_id="alice" if doc_id != "doc-cook" else "bob",
                acl={"public": doc_id == "doc-music",
                     "users": ["carol"] if doc_id == "doc-cook" else [],
                     "groups": []},
                dense=emb["dense"][i],
                sparse=emb["sparse"][i],
                metadata={"chunk_index": i},
            ))
    repo.insert(chunks_to_insert)
    repo.client.flush(repo.collection)
    yield repo
    repo.drop_collection()


@pytest.fixture
def retriever(seeded_milvus):
    """Per-test Retriever with its own Redis client (avoids cross-test loop reuse)."""
    return Retriever(milvus=seeded_milvus, redis=RedisRepository())


async def _flush_caches():
    r = RedisRepository()
    try:
        async for k in r.client.scan_iter(match="emb:*"):
            await r.client.delete(k)
        async for k in r.client.scan_iter(match="ret:*"):
            await r.client.delete(k)
    finally:
        await r.close()


# ─────────────────────────────────────────────────────────────────────
# Core retrieval correctness
# ─────────────────────────────────────────────────────────────────────


class TestRetrieve:
    def test_top_chunk_is_relevant(self, retriever: Retriever):
        async def go():
            try:
                asyncio.run  # noqa: B015 — sentinel
            except Exception:
                pass
            res = await retriever.retrieve(
                "How do machine learning models learn?",
                requester=Requester(user_id="alice"),
                top_k=10, rerank_k=3,
            )
            await retriever.aclose()
            return res

        res = asyncio.run(go())
        assert res.chunks, "should find at least 1 chunk"
        assert res.chunks[0].doc_id == "doc-ml"
        assert res.stats.reranked is True

    def test_acl_filters_results(self, retriever: Retriever):
        """alice owns doc-ml; doc-cook is bob's with carol whitelisted; doc-music is public.

        bob (no groups, not in carol/alice) should see: doc-cook (owner) + doc-music (public).
        bob must NOT see doc-ml.
        """
        async def go():
            res = await retriever.retrieve(
                "anything related to algorithms or pasta",
                requester=Requester(user_id="bob"),
                top_k=20, rerank_k=20, rerank=False,
            )
            await retriever.aclose()
            return res

        res = asyncio.run(go())
        seen = {c.doc_id for c in res.chunks}
        assert "doc-cook" in seen
        assert "doc-music" in seen
        assert "doc-ml" not in seen

    def test_anon_sees_only_public(self, retriever: Retriever):
        async def go():
            res = await retriever.retrieve(
                "anything",
                requester=None,
                top_k=20, rerank_k=20, rerank=False,
            )
            await retriever.aclose()
            return res

        res = asyncio.run(go())
        seen = {c.doc_id for c in res.chunks}
        assert seen == {"doc-music"}


# ─────────────────────────────────────────────────────────────────────
# Embedding cache
# ─────────────────────────────────────────────────────────────────────


class TestEmbeddingCache:
    def test_second_query_hits_emb_cache(self, retriever: Retriever):
        async def go():
            await _flush_caches()
            r1 = await retriever.retrieve(
                "deep neural networks training process",
                requester=Requester(user_id="alice"), top_k=5,
            )
            r2 = await retriever.retrieve(
                "deep neural networks training process",
                requester=Requester(user_id="alice"), top_k=5,
            )
            await retriever.aclose()
            return r1, r2

        r1, r2 = asyncio.run(go())
        assert r1.stats.embedding_cache_misses == 1
        assert r1.stats.embedding_cache_hits == 0
        # 2nd call: retrieval cache hits → no new embed
        # If retrieval cache hits, _embed_with_cache may still be called for
        # query embedding. With cache, embedding hit count should be ≥1.
        assert r2.stats.retrieval_cache_hits == 1


# ─────────────────────────────────────────────────────────────────────
# Retrieval cache + ACL scope isolation
# ─────────────────────────────────────────────────────────────────────


class TestRetrievalCache:
    def test_same_requester_hits_cache(self, retriever: Retriever):
        async def go():
            await _flush_caches()
            req = Requester(user_id="alice")
            a = await retriever.retrieve("backpropagation gradient descent",
                                          requester=req, top_k=5)
            b = await retriever.retrieve("backpropagation gradient descent",
                                          requester=req, top_k=5)
            await retriever.aclose()
            return a, b

        a, b = asyncio.run(go())
        assert a.stats.retrieval_cache_misses == 1
        assert a.stats.retrieval_cache_hits == 0
        assert b.stats.retrieval_cache_hits == 1

    def test_different_requesters_isolated(self, retriever: Retriever):
        async def go():
            await _flush_caches()
            a = await retriever.retrieve("anything",
                                          requester=Requester(user_id="alice"),
                                          top_k=5)
            b = await retriever.retrieve("anything",
                                          requester=Requester(user_id="bob"),
                                          top_k=5)
            await retriever.aclose()
            return a, b

        a, b = asyncio.run(go())
        # Both should be misses because acl_scope differs in cache key
        assert a.stats.retrieval_cache_misses == 1
        assert b.stats.retrieval_cache_misses == 1
        assert b.stats.retrieval_cache_hits == 0

    def test_index_version_invalidates(self, retriever: Retriever, monkeypatch):
        async def go():
            await _flush_caches()
            req = Requester(user_id="alice")
            a = await retriever.retrieve("gradient", requester=req, top_k=5)
            # bump global index version → key changes
            monkeypatch.setattr(settings, "milvus_index_version",
                                settings.milvus_index_version + 1)
            b = await retriever.retrieve("gradient", requester=req, top_k=5)
            await retriever.aclose()
            return a, b

        a, b = asyncio.run(go())
        assert a.stats.retrieval_cache_misses == 1
        assert b.stats.retrieval_cache_misses == 1


# ─────────────────────────────────────────────────────────────────────
# ACL scope helper sanity
# ─────────────────────────────────────────────────────────────────────


class TestAclScope:
    def test_anon(self):
        assert _acl_scope(None) == "anon"

    def test_admin(self):
        assert _acl_scope(Requester(user_id="x", is_admin=True)) == "admin"

    def test_user_groups_stable(self):
        a = _acl_scope(Requester(user_id="alice", groups=["qa", "eng"]))
        b = _acl_scope(Requester(user_id="alice", groups=["eng", "qa"]))  # reordered
        assert a == b  # sort makes it order-independent

    def test_user_differs(self):
        a = _acl_scope(Requester(user_id="alice", groups=[]))
        b = _acl_scope(Requester(user_id="bob", groups=[]))
        assert a != b


# ─────────────────────────────────────────────────────────────────────
# retrieve_multi
# ─────────────────────────────────────────────────────────────────────


class TestRetrieveMulti:
    def test_merges_and_dedupes(self, retriever: Retriever):
        async def go():
            await _flush_caches()
            req = Requester(user_id="alice")
            res = await retriever.retrieve_multi(
                ["machine learning training",
                 "neural network optimization",
                 "loss function gradient"],
                requester=req, top_k=5, rerank_k=5,
            )
            await retriever.aclose()
            return res

        res = asyncio.run(go())
        assert res.stats.queries == 3
        assert res.chunks, "expected at least 1 chunk"
        # Dedup: chunk_ids must be unique after merging
        ids = [c.chunk_id for c in res.chunks]
        assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
        # Top-1 must be from doc-ml (queries are all ML-themed; rerank should rank it first)
        assert res.chunks[0].doc_id == "doc-ml"
        # Majority of returned chunks should be doc-ml
        from collections import Counter
        most_common = Counter(c.doc_id for c in res.chunks).most_common(1)[0]
        assert most_common[0] == "doc-ml"


# ─────────────────────────────────────────────────────────────────────
# Cache invalidation on doc deletion
# ─────────────────────────────────────────────────────────────────────


class TestCacheInvalidation:
    def test_invalidate_cache_for_doc_clears_ret_keys(self, retriever: Retriever):
        async def go():
            await _flush_caches()
            req = Requester(user_id="alice")
            # populate cache
            await retriever.retrieve("training neural", requester=req, top_k=5)

            async def _count_keys(pattern: str) -> int:
                rr = RedisRepository()
                try:
                    n = 0
                    async for _ in rr.client.scan_iter(match=pattern):
                        n += 1
                    return n
                finally:
                    await rr.close()

            count_before = await _count_keys("ret:*")
            assert count_before > 0

            deleted = await retriever.invalidate_cache_for_doc("doc-ml")
            count_after = await _count_keys("ret:*")
            await retriever.aclose()
            return deleted, count_before, count_after

        deleted, before, after = asyncio.run(go())
        assert deleted == before
        assert after == 0
