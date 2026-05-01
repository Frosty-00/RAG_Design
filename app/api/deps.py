"""FastAPI dependencies — auth + shared singletons."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.milvus import Requester
from app.repositories.redis_repo import RedisRepository
from app.services.rag import RAGPipeline

log = get_logger(__name__)


async def get_requester(
    authorization: str | None = Header(default=None),
) -> Requester:
    """Validate `Authorization: Bearer <token>` against Redis token store."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer_token")
    raw = authorization[7:].strip()
    if not raw:
        raise HTTPException(status_code=401, detail="empty_token")

    redis = RedisRepository()
    try:
        info = await redis.lookup_token(raw)
    finally:
        await redis.close()
    if not info:
        raise HTTPException(status_code=401, detail="invalid_token")

    return Requester(
        user_id=info["user_id"],
        groups=list(info.get("groups", []) or []),
        is_admin=info.get("role") == "admin",
    )


async def require_admin(
    requester: Requester = Depends(get_requester),
) -> Requester:
    if not requester.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")
    return requester


def get_pipeline(request: Request) -> RAGPipeline:
    """Pull the per-process RAGPipeline from app.state (set in lifespan)."""
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="pipeline_not_ready")
    return pipeline


# ─────────────────────────────────────────────────────────────────────
# Token management bootstrap
# ─────────────────────────────────────────────────────────────────────


async def bootstrap_admin_token() -> None:
    """Idempotent: ensure `settings.admin_token` exists in Redis as admin role."""
    if not settings.admin_token:
        return
    redis = RedisRepository()
    try:
        existing = await redis.lookup_token(settings.admin_token)
        if existing:
            return
        await redis.store_token(
            settings.admin_token,
            user_id="admin",
            groups=["admin"],
            role="admin",
        )
        log.info("auth.admin_bootstrapped")
    finally:
        await redis.close()
