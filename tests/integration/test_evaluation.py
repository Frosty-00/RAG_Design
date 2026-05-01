"""Layer 10 — EvalRunner integration tests.

retrieval_only mode runs without Vertex; full mode (judge) skips when
VERTEX_PROJECT is empty.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.evaluation import EvalRunner
from app.evaluation.runner import load_dataset, write_run
from app.evaluation.schema import EvalSample
from app.repositories.milvus import Chunk, MilvusRepository
from app.services.embedding import BGEM3Embedder
from app.services.rag import RAGPipeline
from app.services.retrieval import Retriever

LIVE = bool(settings.vertex_project)


@pytest.fixture(scope="module")
def seeded_for_eval():
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
    }
    chunks: list[Chunk] = []
    for doc_id, texts in seeds.items():
        emb = embedder.encode(texts)
        for i, t in enumerate(texts):
            chunks.append(Chunk(
                chunk_id=f"{doc_id}:v1:c{i:04d}",
                doc_id=doc_id, doc_version=1, is_latest=True,
                text=t, owner_id="alice",
                acl={"public": True, "users": [], "groups": []},
                dense=emb["dense"][i], sparse=emb["sparse"][i],
                metadata={"page": i + 1, "breadcrumbs": ["KB", doc_id]},
            ))
    repo.insert(chunks)
    repo.client.flush(repo.collection)
    yield repo
    repo.drop_collection()


@pytest.fixture
def runner(seeded_for_eval):
    pipeline = RAGPipeline(retriever=Retriever(milvus=seeded_for_eval))
    r = EvalRunner(pipeline=pipeline)
    yield r
    try:
        asyncio.run(r.aclose())
    except RuntimeError:
        pass


SAMPLES = [
    EvalSample(
        sample_id="s1",
        question="What is Retrieval-Augmented Generation?",
        expected_answer="RAG combines retrieval with a generator to ground answers.",
        ground_truth_chunks=["doc-rag:v1:c0000"],
    ),
    EvalSample(
        sample_id="s2",
        question="Why does RAG reduce hallucinations?",
        expected_answer="It grounds the LM in retrieved evidence.",
        ground_truth_chunks=["doc-rag:v1:c0001"],
    ),
    EvalSample(
        sample_id="s3",
        question="What does a vector database store?",
        expected_answer="High-dimensional embeddings supporting nearest-neighbor search.",
        ground_truth_chunks=["doc-vector:v1:c0000"],
    ),
]


# ─────────────────────────────────────────────────────────────────────
# retrieval_only — no LLM
# ─────────────────────────────────────────────────────────────────────


class TestRetrievalOnly:
    def test_run_produces_metrics(self, runner: EvalRunner):
        run = asyncio.run(runner.run(SAMPLES, mode="retrieval_only",
                                       dataset_name="test"))
        assert run.n_samples == 3
        assert run.mode == "retrieval_only"
        assert "hit_at_5" in run.metrics
        assert "recall_at_5" in run.metrics
        assert "mrr" in run.metrics
        # generation metrics absent in retrieval_only
        assert "faithfulness" not in run.metrics

        # All three samples have ground-truth in seeded data → hit@5 should == 1
        assert run.metrics["hit_at_5"] == 1.0
        # Per-sample sanity
        for s in run.samples:
            assert s.metrics.hit_at_5 == 1.0
            assert s.error is None

    def test_writes_json_and_md(self, runner: EvalRunner, tmp_path: Path):
        run = asyncio.run(runner.run(SAMPLES, mode="retrieval_only",
                                       dataset_name="test"))
        out = write_run(run, tmp_path)
        assert out.exists()
        assert out.suffix == ".json"
        # MD sidecar
        md = out.with_suffix(".md")
        assert md.exists()
        content = md.read_text(encoding="utf-8")
        assert "Aggregate metrics" in content


# ─────────────────────────────────────────────────────────────────────
# load_dataset
# ─────────────────────────────────────────────────────────────────────


class TestLoadDataset:
    def test_load_jsonl(self, tmp_path: Path):
        path = tmp_path / "tiny.jsonl"
        path.write_text(
            json.dumps({
                "sample_id": "x1", "question": "Q?",
                "expected_answer": "A.",
                "ground_truth_chunks": ["c1"], "note": "",
            }) + "\n",
            encoding="utf-8",
        )
        samples = load_dataset(path)
        assert len(samples) == 1
        assert samples[0].sample_id == "x1"
        assert samples[0].ground_truth_chunks == ["c1"]


# ─────────────────────────────────────────────────────────────────────
# Live (judge) mode
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not LIVE, reason="requires VERTEX_PROJECT")
class TestFullMode:
    def test_full_mode_with_judge(self, runner: EvalRunner):
        # Just 1 sample — keep judge cost minimal
        run = asyncio.run(runner.run(SAMPLES[:1], mode="full",
                                       dataset_name="test"))
        assert run.n_samples == 1
        # All metrics present
        for k in ("hit_at_5", "recall_at_5", "mrr",
                  "faithfulness", "answer_relevancy", "answer_correctness"):
            assert k in run.metrics, f"missing metric {k}"
        # judge_model recorded
        assert run.judge_model == settings.vertex_judge_model
        # answer non-empty
        assert run.samples[0].answer.strip()


# ─────────────────────────────────────────────────────────────────────
# eval_diff
# ─────────────────────────────────────────────────────────────────────


class TestEvalDiff:
    def test_diff_self_is_zero(self, runner: EvalRunner):
        from scripts.eval_diff import diff_runs

        run = asyncio.run(runner.run(SAMPLES, mode="retrieval_only",
                                       dataset_name="test"))
        diff = diff_runs(run, run)
        for k, v in diff["metric_diffs"].items():
            assert v["delta"] == 0.0, f"{k} should be 0"
        assert diff["newly_bad"] == []
        assert diff["newly_good"] == []
