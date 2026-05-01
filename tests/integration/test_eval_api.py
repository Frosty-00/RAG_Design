"""Layer 11 — Evaluation REST API + Celery task integration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.repositories.milvus import Chunk, MilvusRepository
from app.repositories.minio_repo import MinioRepository
from app.repositories.redis_repo import RedisRepository
from app.services.embedding import BGEM3Embedder
from app.workers.celery_app import celery_app


@pytest.fixture(scope="module", autouse=True)
def _eager_celery():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False


@pytest.fixture(scope="module")
def seeded_milvus_for_eval():
    """Seed test_kb_chunks with the same 3 docs as Layer 10 tests so the
    mini.jsonl-style ground-truth chunk IDs match."""
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


@pytest.fixture(scope="module")
def app_client(seeded_milvus_for_eval):
    MinioRepository().ensure_bucket()
    from app.main import app
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_token}"}


@pytest.fixture(scope="module")
def alice_token(app_client: TestClient, admin_headers: dict) -> str:
    r = app_client.post(
        "/api/v1/admin/tokens",
        headers=admin_headers,
        json={"user_id": "alice-eval", "groups": [], "role": "user"},
    )
    return r.json()["token"]


@pytest.fixture(scope="module")
def mini_dataset(tmp_path_factory) -> Path:
    """Build a tiny golden dataset whose ground_truth_chunks match the seeded chunks."""
    p = tmp_path_factory.mktemp("eval") / "mini.jsonl"
    rows = [
        {"sample_id": "s1", "question": "What is RAG?",
         "expected_answer": "Retrieval-Augmented Generation combines retrieval and generation.",
         "ground_truth_chunks": ["doc-rag:v1:c0000"], "note": ""},
        {"sample_id": "s2", "question": "Why does RAG reduce hallucinations?",
         "expected_answer": "It grounds answers in retrieved evidence.",
         "ground_truth_chunks": ["doc-rag:v1:c0001"], "note": ""},
        {"sample_id": "s3", "question": "What does a vector database store?",
         "expected_answer": "Embeddings, high-dimensional numeric text representations.",
         "ground_truth_chunks": ["doc-vector:v1:c0000"], "note": ""},
    ]
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_eval_requires_admin(
        self, app_client: TestClient, alice_token: str, mini_dataset: Path,
    ):
        r = app_client.post("/api/v1/eval/runs",
                            headers=_auth(alice_token),
                            json={"dataset": str(mini_dataset),
                                  "mode": "retrieval_only"})
        assert r.status_code == 403

    def test_list_requires_admin(
        self, app_client: TestClient, alice_token: str,
    ):
        r = app_client.get("/api/v1/eval/runs", headers=_auth(alice_token))
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────


class TestEvalLifecycle:
    def test_dataset_not_found_404(
        self, app_client: TestClient, admin_headers: dict,
    ):
        r = app_client.post(
            "/api/v1/eval/runs",
            headers=admin_headers,
            json={"dataset": "nope/missing.jsonl", "mode": "retrieval_only"},
        )
        assert r.status_code == 404

    def test_full_lifecycle_retrieval_only(
        self,
        app_client: TestClient, admin_headers: dict, mini_dataset: Path,
    ):
        # ── start ──────────────────────────────────────────────────
        r = app_client.post(
            "/api/v1/eval/runs",
            headers=admin_headers,
            json={"dataset": str(mini_dataset), "mode": "retrieval_only"},
        )
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]

        # eager Celery → task ran inline
        # ── detail ─────────────────────────────────────────────────
        r = app_client.get(f"/api/v1/eval/runs/{run_id}", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["status"] == "done"
        assert body["meta"]["mode"] == "retrieval_only"
        assert body["meta"]["n_samples"] == 3
        assert body["meta"]["metrics"]["hit_at_5"] == 1.0  # all 3 ground truth in seed
        assert body["report"] is not None
        assert body["report"]["n_samples"] == 3

        # ── list contains it ───────────────────────────────────────
        r = app_client.get("/api/v1/eval/runs", headers=admin_headers)
        assert r.status_code == 200
        listed = r.json()
        assert any(rec["run_id"] == run_id for rec in listed)

        # ── diff self vs self → all zero ───────────────────────────
        r = app_client.post("/api/v1/eval/diff",
                            headers=admin_headers,
                            json={"baseline_id": run_id, "candidate_id": run_id})
        assert r.status_code == 200
        diff = r.json()
        for k, v in diff["metric_diffs"].items():
            assert v["delta"] == 0.0, f"{k} should diff to 0"
        assert diff["newly_bad"] == []
        assert diff["newly_good"] == []

    def test_diff_run_not_found(
        self, app_client: TestClient, admin_headers: dict,
    ):
        r = app_client.post("/api/v1/eval/diff",
                            headers=admin_headers,
                            json={"baseline_id": "nope-1",
                                  "candidate_id": "nope-2"})
        assert r.status_code == 404

    def test_get_run_404(
        self, app_client: TestClient, admin_headers: dict,
    ):
        r = app_client.get("/api/v1/eval/runs/no-such-run",
                            headers=admin_headers)
        assert r.status_code == 404
