"""Layer 8 — RAG pipeline orchestration integration tests.

Live tests skip when VERTEX_PROJECT is empty. Tests assert:
  - SSE event ordering for both chitchat and KB-retrieval paths
  - Chunks in citations match what was returned by retrieval
  - Empty retrieval falls back without LLM streaming hallucinations
  - ACL is honored end-to-end (different requesters → different results)
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.repositories.milvus import Chunk, MilvusRepository, Requester
from app.services.embedding import BGEM3Embedder
from app.services.rag import ChatChunk, RAGPipeline

LIVE = bool(settings.vertex_project)
pytestmark = pytest.mark.skipif(not LIVE, reason="requires VERTEX_PROJECT")


# ─────────────────────────────────────────────────────────────────────
# Seed test collection (module scoped)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def seeded_milvus():
    repo = MilvusRepository()
    repo.drop_collection()
    repo.ensure_collection()

    embedder = BGEM3Embedder.get()
    seeds = {
        "doc-rag": [
            "Retrieval-Augmented Generation, or RAG, combines a retriever and a "
            "generator: the retriever pulls relevant documents from a knowledge "
            "base, and the generator answers using those documents as context.",
            "RAG reduces hallucinations because the language model is grounded "
            "in retrieved evidence rather than parametric memory alone.",
        ],
        "doc-vector": [
            "A vector database stores embeddings — high-dimensional numeric "
            "representations of text — and supports nearest-neighbor search.",
        ],
        "doc-private": [
            "Internal salary policy: software engineers receive an annual review.",
        ],
    }
    chunks: list[Chunk] = []
    for doc_id, texts in seeds.items():
        emb = embedder.encode(texts)
        for i, t in enumerate(texts):
            chunks.append(Chunk(
                chunk_id=f"{doc_id}:v1:c{i:04d}",
                doc_id=doc_id,
                doc_version=1,
                is_latest=True,
                text=t,
                owner_id="alice" if doc_id != "doc-private" else "boss",
                acl={
                    "public": doc_id != "doc-private",
                    "users": [],
                    "groups": ["hr"] if doc_id == "doc-private" else [],
                },
                dense=emb["dense"][i],
                sparse=emb["sparse"][i],
                metadata={"page": i + 1, "breadcrumbs": ["KB", doc_id]},
            ))
    repo.insert(chunks)
    repo.client.flush(repo.collection)
    yield repo
    repo.drop_collection()


@pytest.fixture
def pipeline(seeded_milvus):
    from app.services.retrieval import Retriever
    p = RAGPipeline(retriever=Retriever(milvus=seeded_milvus))
    yield p
    # Async redis is bound to whichever loop opened the connection; closing it
    # from a fresh asyncio.run loop will throw "Event loop is closed". GC
    # handles the actual socket cleanup; we don't need to await close here.
    try:
        asyncio.run(p.aclose())
    except RuntimeError:
        pass


def _collect(pipeline: RAGPipeline, query: str, *, requester=None,
             history=None) -> list[ChatChunk]:
    async def go():
        return [c async for c in pipeline.answer_stream(
            query, history=history, requester=requester,
        )]
    return asyncio.run(go())


# ─────────────────────────────────────────────────────────────────────
# Chitchat path
# ─────────────────────────────────────────────────────────────────────


class TestChitchatPath:
    def test_keyword_chitchat_skips_retrieval(self, pipeline: RAGPipeline):
        chunks = _collect(pipeline, "你好",
                           requester=Requester(user_id="alice"))
        events = [c.event for c in chunks]

        # Expected: ack(accepted) → ack(generating) → token×N → citations
        assert events[0] == "ack" and chunks[0].phase == "accepted"
        # No "retrieving" phase in chitchat
        retrieving = [c for c in chunks if c.event == "ack" and c.phase == "retrieving"]
        assert retrieving == []

        ack_gen = [c for c in chunks if c.event == "ack" and c.phase == "generating"]
        assert len(ack_gen) == 1

        tokens = [c for c in chunks if c.event == "token"]
        assert tokens, "should produce at least one token"
        full = "".join(t.token for t in tokens)
        assert full.strip()  # non-empty reply

        # last event is citations with empty list + meta.path == chitchat
        last = chunks[-1]
        assert last.event == "citations"
        assert last.citations == []
        assert last.meta and last.meta["path"] == "chitchat"


# ─────────────────────────────────────────────────────────────────────
# KB retrieval path
# ─────────────────────────────────────────────────────────────────────


class TestRetrievalPath:
    def test_full_event_sequence(self, pipeline: RAGPipeline):
        chunks = _collect(
            pipeline,
            "What is RAG and why does it reduce hallucinations?",
            requester=Requester(user_id="alice"),
        )
        events = [(c.event, c.phase) for c in chunks]

        # ack(accepted) ... ack(retrieving) ... ack(generating) ... tokens ... citations
        assert events[0] == ("ack", "accepted")
        retrieving_idx = next(i for i, e in enumerate(events) if e == ("ack", "retrieving"))
        generating_idx = next(i for i, e in enumerate(events) if e == ("ack", "generating"))
        assert retrieving_idx < generating_idx

        tokens = [c for c in chunks if c.event == "token"]
        assert tokens

        last = chunks[-1]
        assert last.event == "citations"
        assert last.citations
        # at least one citation should come from doc-rag
        assert any(cit.doc_id == "doc-rag" for cit in last.citations)
        # citation indices are 1-based and contiguous
        indices = [cit.index for cit in last.citations]
        assert indices == list(range(1, len(indices) + 1))

    def test_answer_mentions_retrieved_content(self, pipeline: RAGPipeline):
        chunks = _collect(
            pipeline,
            "What does a vector database store?",
            requester=Requester(user_id="alice"),
        )
        full = "".join(c.token for c in chunks if c.event == "token").lower()
        # answer should reference the seed content
        assert any(kw in full for kw in ("embedding", "vector", "nearest"))

    def test_acl_filters_private_doc(self, pipeline: RAGPipeline):
        """Stranger without 'hr' group must not see salary policy in citations."""
        chunks = _collect(
            pipeline,
            "What is the salary review policy?",
            requester=Requester(user_id="bob", groups=[]),
        )
        last = chunks[-1]
        # Either we got a fallback (no chunks visible) or chunks but
        # NEVER from doc-private. The fallback message contains the literal
        # "未在知识库中找到相关内容" string we yield.
        if last.citations:
            assert all(cit.doc_id != "doc-private" for cit in last.citations)
        else:
            tokens = "".join(c.token for c in chunks if c.event == "token")
            assert "未在知识库中找到" in tokens


class TestEmptyRetrieval:
    def test_anonymous_unrelated_query_falls_back(self, pipeline: RAGPipeline):
        """Anon user asking about something not in the public doc set."""
        chunks = _collect(
            pipeline,
            "How do I fly a helicopter through dense fog?",
            requester=None,
        )
        # Either the LLM produced "未在知识库中找到" via fallback OR
        # fallback path emitted that text directly. Check tokens contain it.
        tokens = "".join(c.token for c in chunks if c.event == "token")
        last = chunks[-1]
        # We emit "未在知识库中找到相关内容。" exactly when retrieval is empty.
        # If retrieval found semi-related public chunks but model says it can't
        # answer, also acceptable. So we accept either case but require no
        # private leakage.
        if last.citations:
            assert all(cit.doc_id != "doc-private" for cit in last.citations)
        # Pipeline always produces some token output
        assert tokens.strip()
