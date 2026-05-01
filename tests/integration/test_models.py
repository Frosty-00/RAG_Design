"""Layer 3 model tests.

Note: first run downloads ~2.5 GB of weights into MODEL_CACHE_DIR. Subsequent
runs are fast (~30s for embedder, ~15s for reranker to load from cache).

Run:
    pytest tests/integration/test_models.py -v -s
"""
from __future__ import annotations

import time

import pytest

from app.services.embedding import BGEM3Embedder
from app.services.reranker import BGEReranker


# Session-scoped fixtures so the heavy weights load only once across all tests.
@pytest.fixture(scope="session")
def embedder() -> BGEM3Embedder:
    BGEM3Embedder.reset_instance()  # ensure clean load when run alone
    return BGEM3Embedder.get()


@pytest.fixture(scope="session")
def reranker() -> BGEReranker:
    BGEReranker.reset_instance()
    return BGEReranker.get()


# ─────────────────────────────────────────────────────────────────────
# Embedder
# ─────────────────────────────────────────────────────────────────────


class TestBGEM3Embedder:
    def test_dense_shape(self, embedder: BGEM3Embedder):
        out = embedder.encode(
            ["hello world", "你好，世界"],
            return_sparse=False,
        )
        assert "dense" in out
        assert len(out["dense"]) == 2
        assert len(out["dense"][0]) == BGEM3Embedder.DIM
        assert len(out["dense"][1]) == BGEM3Embedder.DIM
        # values are plain floats (not numpy)
        assert all(isinstance(v, float) for v in out["dense"][0][:5])

    def test_sparse_shape_and_types(self, embedder: BGEM3Embedder):
        out = embedder.encode(
            ["The quick brown fox jumps over the lazy dog."],
            return_dense=False,
        )
        assert "sparse" in out
        assert len(out["sparse"]) == 1
        sp = out["sparse"][0]
        # non-empty (a real sentence has >0 lexical weights)
        assert len(sp) > 0
        # Milvus requires keys to be Python ints, values python floats
        for k, v in list(sp.items())[:5]:
            assert isinstance(k, int)
            assert isinstance(v, float)
            assert v > 0.0

    def test_zh_en_mixed(self, embedder: BGEM3Embedder):
        out = embedder.encode(
            ["公司年假天数怎么计算？(How is annual leave calculated?)"],
        )
        assert len(out["dense"][0]) == BGEM3Embedder.DIM
        assert len(out["sparse"][0]) > 0

    def test_encode_query_helper(self, embedder: BGEM3Embedder):
        q = embedder.encode_query("机器学习是什么？")
        assert isinstance(q["dense"], list)
        assert len(q["dense"]) == BGEM3Embedder.DIM
        assert isinstance(q["sparse"], dict)
        assert len(q["sparse"]) > 0

    def test_singleton_returns_same_instance(self, embedder: BGEM3Embedder):
        another = BGEM3Embedder.get()
        assert another is embedder

    def test_throughput_smoke(self, embedder: BGEM3Embedder):
        """Smoke check: 50 short docs in one batch finish reasonably.

        We don't fail on speed (CPU machines vary widely) — just record.
        """
        docs = [f"document number {i} with some sample text" for i in range(50)]
        t0 = time.perf_counter()
        out = embedder.encode(docs, batch_size=16)
        dt = time.perf_counter() - t0
        assert len(out["dense"]) == 50
        assert len(out["sparse"]) == 50
        print(f"\n[throughput] 50 docs encoded in {dt:.2f}s "
              f"({50 / dt:.1f} docs/s)")


# ─────────────────────────────────────────────────────────────────────
# Reranker
# ─────────────────────────────────────────────────────────────────────


class TestBGEReranker:
    def test_relevant_outranks_irrelevant(self, reranker: BGEReranker):
        query = "How do machine learning models learn from data?"
        docs = [
            "Machine learning models learn patterns by minimizing a loss function over training data.",
            "I had pizza for lunch and it was tasty.",
            "Deep neural networks adjust weights via backpropagation during training.",
        ]
        results = reranker.rerank(query, docs)
        assert len(results) == 3
        # The pizza doc should be the lowest-scored
        bottom = results[-1]
        assert bottom.item == docs[1]
        # And the top-1 should be one of the relevant ones
        assert results[0].item in (docs[0], docs[2])
        # Strict ordering: top score > pizza score
        assert results[0].score > bottom.score

    def test_chinese_relevance(self, reranker: BGEReranker):
        query = "公司年假怎么算？"
        docs = [
            "公司员工每年享有 10 天带薪年假，按入职月数比例计算。",  # relevant
            "今天天气不错，适合出去散步。",  # irrelevant
        ]
        results = reranker.rerank(query, docs)
        assert results[0].item == docs[0]
        assert results[0].score > results[1].score

    def test_k_clipping(self, reranker: BGEReranker):
        query = "anything"
        docs = ["apple", "banana", "cherry", "durian"]
        out = reranker.rerank(query, docs, k=2)
        assert len(out) == 2

    def test_empty_input(self, reranker: BGEReranker):
        assert reranker.rerank("q", []) == []

    def test_score_normalized(self, reranker: BGEReranker):
        """With normalize=True, scores fall in [0, 1] (sigmoid)."""
        out = reranker.rerank("q", ["doc1", "doc2"], normalize=True)
        for r in out:
            assert 0.0 <= r.score <= 1.0
