"""Layer 9 — FastAPI routes + auth + cascade delete + Prometheus.

Uses FastAPI TestClient with Celery in eager mode so ingestion runs
synchronously. Live LLM tests skip when VERTEX_PROJECT is empty.
"""
from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.repositories.milvus import MilvusRepository
from app.repositories.minio_repo import MinioRepository
from app.repositories.redis_repo import RedisRepository
from app.workers.celery_app import celery_app

LIVE = bool(settings.vertex_project)


# ─────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def _eager_celery():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False


@pytest.fixture(scope="module")
def app_client():
    """Build app + TestClient. Skip lifespan errors that aren't fatal."""
    # Reset singletons so test-collection envs are picked up
    MilvusRepository().drop_collection()
    MinioRepository().ensure_bucket()
    # Wipe the test bucket
    MinioRepository().delete_prefix("")

    from app.main import app
    with TestClient(app) as client:
        yield client

    MilvusRepository().drop_collection()


@pytest.fixture(scope="module")
def admin_headers() -> dict:
    return {"Authorization": f"Bearer {settings.admin_token}"}


@pytest.fixture(scope="module")
def alice_token(app_client: TestClient, admin_headers: dict) -> str:
    """Issue a regular user token via admin endpoint."""
    r = app_client.post(
        "/api/v1/admin/tokens",
        headers=admin_headers,
        json={"user_id": "alice", "groups": [], "role": "user"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def bob_token(app_client: TestClient, admin_headers: dict) -> str:
    r = app_client.post(
        "/api/v1/admin/tokens",
        headers=admin_headers,
        json={"user_id": "bob", "groups": [], "role": "user"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────
# Health + Prometheus
# ─────────────────────────────────────────────────────────────────────


class TestHealth:
    def test_healthz(self, app_client: TestClient):
        r = app_client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_readyz_all_up(self, app_client: TestClient):
        r = app_client.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert all(body["components"].values())

    def test_metrics_exposed(self, app_client: TestClient):
        r = app_client.get("/metrics")
        assert r.status_code == 200
        body = r.text
        # FastAPI instrumentator default metrics
        assert "http_requests_total" in body or "http_request_duration_seconds" in body


# ─────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_documents_requires_auth(self, app_client: TestClient):
        r = app_client.get("/api/v1/documents")
        assert r.status_code == 401

    def test_invalid_token_rejected(self, app_client: TestClient):
        r = app_client.get("/api/v1/documents",
                           headers=_auth("nope-not-a-real-token"))
        assert r.status_code == 401

    def test_admin_endpoint_blocks_user(self, app_client: TestClient,
                                          alice_token: str):
        r = app_client.post(
            "/api/v1/admin/tokens",
            headers=_auth(alice_token),
            json={"user_id": "x", "groups": [], "role": "user"},
        )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# Documents lifecycle
# ─────────────────────────────────────────────────────────────────────


class TestDocuments:
    def test_upload_list_get_delete_lifecycle(
        self, app_client: TestClient, alice_token: str
    ):
        content = (
            "# Test API Doc\n\n"
            "## Overview\n\n"
            "A brief markdown document used by the API integration test.\n"
            "It should produce at least one chunk after parsing.\n"
        ).encode("utf-8")

        # 1. Upload
        files = {"file": ("apitest.md", io.BytesIO(content), "text/markdown")}
        data = {"public": "false", "users": "", "groups": ""}
        r = app_client.post(
            "/api/v1/documents",
            headers=_auth(alice_token),
            files=files, data=data,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        doc_id = body["doc_id"]
        assert body["version"] == 1
        assert body["status"] == "queued"

        # eager celery → ingest already ran. Validate state.
        # 2. List
        r = app_client.get("/api/v1/documents", headers=_auth(alice_token))
        assert r.status_code == 200
        listed = r.json()
        assert any(d["doc_id"] == doc_id for d in listed)

        # 3. Detail
        r = app_client.get(f"/api/v1/documents/{doc_id}",
                           headers=_auth(alice_token))
        assert r.status_code == 200
        meta = r.json()
        assert meta["latest_status"] == "done"
        assert meta["owner_id"] == "alice"
        assert meta["n_chunks"] >= 1

        # 4. Delete
        r = app_client.delete(f"/api/v1/documents/{doc_id}",
                              headers=_auth(alice_token))
        assert r.status_code == 200
        assert r.json()["doc_id"] == doc_id

        # 5. After cascade: not in list, detail 404
        r = app_client.get("/api/v1/documents", headers=_auth(alice_token))
        assert all(d["doc_id"] != doc_id for d in r.json())
        r = app_client.get(f"/api/v1/documents/{doc_id}",
                           headers=_auth(alice_token))
        assert r.status_code == 404

    def test_upload_dedup_returns_existing(
        self, app_client: TestClient, alice_token: str
    ):
        """Uploading the same content twice → second response is
        already_exists with v1 reused (no new ingestion)."""
        content = b"# Dedup Test\n\nUnique content about widgets.\n"
        files = {"file": ("dedup.md", io.BytesIO(content), "text/markdown")}

        r1 = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files=files, data={"public": "false"},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["status"] == "queued"

        # 2nd upload (need to reset the BytesIO position)
        files = {"file": ("dedup.md", io.BytesIO(content), "text/markdown")}
        r2 = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files=files, data={"public": "false"},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["status"] == "already_exists"
        assert body2["doc_id"] == body1["doc_id"]
        assert body2["version"] == 1

        # cleanup
        app_client.delete(f"/api/v1/documents/{body1['doc_id']}",
                           headers=_auth(alice_token))

    def test_upload_filename_conflict_409(
        self, app_client: TestClient, alice_token: str
    ):
        """Different *content* but same filename owned by the same user
        → 409 with structured `filename_exists` detail. Prevents the user
        from accidentally creating two `report.pdf` rows that look identical
        in the table but point to different docs."""
        content_a = b"# Version A\n\nFirst body.\n"
        content_b = b"# Version B\n\nCompletely different body.\n"

        # First upload succeeds
        r1 = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files={"file": ("clash.md", io.BytesIO(content_a), "text/markdown")},
            data={"public": "false"},
        )
        assert r1.status_code == 200
        doc_id = r1.json()["doc_id"]

        # Second upload — same filename, different bytes → blocked
        r2 = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files={"file": ("clash.md", io.BytesIO(content_b), "text/markdown")},
            data={"public": "false"},
        )
        assert r2.status_code == 409, r2.text
        detail = r2.json()["detail"]
        assert detail["code"] == "filename_exists"
        assert "clash.md" in detail["message"]
        assert detail["existing_doc_id"] == doc_id

        # cleanup
        app_client.delete(f"/api/v1/documents/{doc_id}",
                          headers=_auth(alice_token))

    def test_upload_filename_isolated_per_owner(
        self, app_client: TestClient,
        alice_token: str, bob_token: str,
    ):
        """alice's `report.pdf` does not block bob from uploading his own
        `report.pdf` — filename uniqueness is per-owner, not global."""
        files_alice = {"file": ("shared-name.md",
                                io.BytesIO(b"# Alice's content\n"),
                                "text/markdown")}
        r_a = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files=files_alice, data={"public": "false"},
        )
        assert r_a.status_code == 200
        doc_a = r_a.json()["doc_id"]

        files_bob = {"file": ("shared-name.md",
                              io.BytesIO(b"# Bob's content\n"),
                              "text/markdown")}
        r_b = app_client.post(
            "/api/v1/documents", headers=_auth(bob_token),
            files=files_bob, data={"public": "false"},
        )
        assert r_b.status_code == 200, r_b.text
        doc_b = r_b.json()["doc_id"]
        assert doc_a != doc_b

        app_client.delete(f"/api/v1/documents/{doc_a}", headers=_auth(alice_token))
        app_client.delete(f"/api/v1/documents/{doc_b}", headers=_auth(bob_token))

    def test_cross_user_acl_403(
        self, app_client: TestClient,
        alice_token: str, bob_token: str,
    ):
        """alice uploads private doc; bob can't see it or delete it."""
        content = b"# alice-private\n\ntop secret data.\n"
        files = {"file": ("alice.md", io.BytesIO(content), "text/markdown")}
        r = app_client.post(
            "/api/v1/documents", headers=_auth(alice_token),
            files=files, data={"public": "false"},
        )
        doc_id = r.json()["doc_id"]

        # bob list — alice's doc not there
        r = app_client.get("/api/v1/documents", headers=_auth(bob_token))
        assert all(d["doc_id"] != doc_id for d in r.json())

        # bob detail — 403
        r = app_client.get(f"/api/v1/documents/{doc_id}",
                           headers=_auth(bob_token))
        assert r.status_code == 403

        # bob delete — 403
        r = app_client.delete(f"/api/v1/documents/{doc_id}",
                              headers=_auth(bob_token))
        assert r.status_code == 403

        # cleanup as alice
        app_client.delete(f"/api/v1/documents/{doc_id}",
                           headers=_auth(alice_token))


# ─────────────────────────────────────────────────────────────────────
# Chat (SSE) — live LLM only
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not LIVE, reason="requires VERTEX_PROJECT")
class TestChat:
    def test_chitchat_sse(
        self, app_client: TestClient, alice_token: str,
    ):
        with app_client.stream(
            "POST", "/api/v1/chat",
            headers={**_auth(alice_token), "Accept": "text/event-stream"},
            json={"query": "你好"},
        ) as r:
            assert r.status_code == 200
            text = "".join(line for line in r.iter_text())
        # Expect at least: ack(accepted) + token + citations
        assert "event: ack" in text
        assert "event: token" in text
        assert "event: citations" in text
        # No "retrieving" phase for chitchat
        assert "\"phase\": \"retrieving\"" not in text


@pytest.mark.skipif(not LIVE, reason="rate limit test runs after the chat path")
class TestChatRateLimit:
    """Layer 15 — chat is rate-limited per user.

    We monkeypatch the threshold down to 3 so the test stays under the
    1-minute bucket boundary even on slower machines.
    """

    def test_request_over_threshold_returns_429(
        self,
        app_client: TestClient,
        admin_headers: dict,
        monkeypatch,
    ):
        from app.api.v1 import chat as chat_mod
        monkeypatch.setattr(chat_mod, "CHAT_RPM_PER_USER", 3)

        # Fresh user → its own bucket key
        r = app_client.post(
            "/api/v1/admin/tokens", headers=admin_headers,
            json={"user_id": f"rl-{uuid.uuid4().hex[:6]}",
                  "groups": [], "role": "user"},
        )
        assert r.status_code == 200
        token = r.json()["token"]

        statuses: list[int] = []
        for _ in range(4):
            with app_client.stream(
                "POST", "/api/v1/chat",
                headers={**_auth(token), "Accept": "text/event-stream"},
                json={"query": "ok"},
            ) as resp:
                statuses.append(resp.status_code)
                _ = list(resp.iter_text())  # drain

        # 4th request must be 429 (3 allowed + 1 over)
        assert statuses[-1] == 429, statuses
        # First 3 should not be 429
        assert all(s != 429 for s in statuses[:3]), statuses


# ─────────────────────────────────────────────────────────────────────
# DLQ admin
# ─────────────────────────────────────────────────────────────────────


class TestDlqAdmin:
    def test_dlq_get_404_when_missing(self, app_client: TestClient,
                                       admin_headers: dict):
        r = app_client.get("/api/v1/admin/dlq/no-such-task",
                           headers=admin_headers)
        assert r.status_code == 404

    def test_dlq_listing_includes_seeded(
        self, app_client: TestClient, admin_headers: dict,
    ):
        # Seed a fake DLQ entry directly
        async def seed():
            r = RedisRepository()
            try:
                await r.push_dlq("test-dlq-1", {
                    "task": "ingest_document",
                    "kwargs": {"doc_id": "x"},
                    "error": "boom",
                    "failed_at": "now",
                })
            finally:
                await r.close()

        async def cleanup():
            r = RedisRepository()
            try:
                await r.client.delete("dlq:tasks:test-dlq-1")
            finally:
                await r.close()

        asyncio.run(seed())
        try:
            r = app_client.get("/api/v1/admin/dlq", headers=admin_headers)
            assert r.status_code == 200
            assert "test-dlq-1" in r.json()
        finally:
            asyncio.run(cleanup())
