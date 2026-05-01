"""FastAPI application entry point.

Routes mounted:
  /healthz, /readyz, /metrics
  /api/v1/documents           upload / list / detail / delete
  /api/v1/chat                SSE streaming RAG
  /api/v1/admin/*             token mgmt + DLQ (admin token required)
  /api/v1/debug/*             retrieval debug — dev-only

Lifespan:
  - Configure structlog
  - Bootstrap admin token (idempotent)
  - Ensure Milvus collection + MinIO bucket
  - Build RAGPipeline singleton (loads BGE-M3 / reranker eagerly)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.deps import bootstrap_admin_token
from app.api.v1 import admin as admin_router
from app.api.v1 import chat as chat_router
from app.api.v1 import debug as debug_router
from app.api.v1 import documents as documents_router
from app.api.v1 import eval as eval_router
from app.core.config import settings
from app.core.health import run_probes
from app.core.logger import configure_logging, get_logger, set_request_id
from app.repositories.milvus import MilvusRepository
from app.repositories.minio_repo import MinioRepository
from app.services.rag import RAGPipeline

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup.begin", env=settings.env, port=settings.api_port)

    # 1. Bootstrap admin token (no-op if already exists)
    try:
        await bootstrap_admin_token()
    except Exception as e:  # noqa: BLE001
        log.warning("startup.admin_token_skipped", err=str(e))

    # 2. Ensure storage primitives
    try:
        MilvusRepository().ensure_collection()
        MinioRepository().ensure_bucket()
    except Exception as e:  # noqa: BLE001
        log.warning("startup.storage_init_failed", err=str(e))

    # 3. Build RAGPipeline (heavy: loads embedder + reranker)
    try:
        app.state.pipeline = RAGPipeline()
        log.info("startup.pipeline_ready")
    except Exception as e:  # noqa: BLE001
        log.warning("startup.pipeline_init_failed", err=str(e))
        app.state.pipeline = None

    log.info("startup.done")
    yield

    # Shutdown
    pipeline = getattr(app.state, "pipeline", None)
    if pipeline:
        try:
            await pipeline.aclose()
        except Exception:  # noqa: BLE001
            pass
    log.info("shutdown")


app = FastAPI(
    title="self-rag",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    set_request_id(rid)
    try:
        response = await call_next(request)
    finally:
        set_request_id(None)
    response.headers["x-request-id"] = rid
    return response


# ─── Health ──────────────────────────────────────────────────────────


@app.get("/healthz", tags=["health"])
async def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
async def readyz():
    results = await run_probes()
    failing = [
        {"component": r.component, "detail": r.detail}
        for r in results if not r.ok
    ]
    body = {
        "status": "ok" if not failing else "degraded",
        "components": {r.component: r.ok for r in results},
    }
    if failing:
        body["failing"] = failing
        return JSONResponse(body, status_code=503)
    return body


# ─── /metrics (Prometheus) ───────────────────────────────────────────
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


# ─── API v1 routers ──────────────────────────────────────────────────
app.include_router(documents_router.router, prefix="/api/v1")
app.include_router(chat_router.router, prefix="/api/v1")
app.include_router(admin_router.router, prefix="/api/v1")
app.include_router(eval_router.router, prefix="/api/v1")

if settings.is_dev:
    app.include_router(debug_router.router, prefix="/api/v1")
    log.info("startup.debug_routes_enabled")
