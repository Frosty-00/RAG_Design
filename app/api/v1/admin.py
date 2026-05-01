"""Admin-only endpoints — token management + DLQ inspection/retry."""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.core.logger import get_logger
from app.repositories.milvus import Requester
from app.repositories.redis_repo import RedisRepository
from app.workers.celery_app import celery_app

log = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ─────────────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────────────


class TokenIssueRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    groups: list[str] = []
    role: str = Field(default="user", pattern="^(user|admin)$")


class TokenIssueResponse(BaseModel):
    token: str
    user_id: str
    role: str


@router.post("/tokens", response_model=TokenIssueResponse)
async def issue_token(
    body: TokenIssueRequest,
    _admin: Requester = Depends(require_admin),
) -> TokenIssueResponse:
    raw = secrets.token_urlsafe(32)
    redis = RedisRepository()
    try:
        await redis.store_token(
            raw, user_id=body.user_id, groups=body.groups, role=body.role,
        )
    finally:
        await redis.close()
    log.info("admin.token_issued", user=body.user_id, role=body.role)
    return TokenIssueResponse(token=raw, user_id=body.user_id, role=body.role)


@router.delete("/tokens")
async def revoke_token(
    token: str,
    _admin: Requester = Depends(require_admin),
) -> dict:
    redis = RedisRepository()
    try:
        deleted = await redis.revoke_token(token)
    finally:
        await redis.close()
    return {"deleted": int(deleted)}


# ─────────────────────────────────────────────────────────────────────
# DLQ
# ─────────────────────────────────────────────────────────────────────


@router.get("/dlq", response_model=list[str])
async def list_dlq(
    _admin: Requester = Depends(require_admin),
    limit: int = 100,
) -> list[str]:
    redis = RedisRepository()
    try:
        return await redis.list_dlq(limit=limit)
    finally:
        await redis.close()


@router.get("/dlq/{task_id}", response_model=dict)
async def get_dlq_entry(
    task_id: str,
    _admin: Requester = Depends(require_admin),
) -> dict[str, Any]:
    redis = RedisRepository()
    try:
        payload = await redis._get_json(f"dlq:tasks:{task_id}")
    finally:
        await redis.close()
    if not payload:
        raise HTTPException(status_code=404, detail="not_in_dlq")
    return payload


@router.post("/dlq/{task_id}/retry", response_model=dict)
async def retry_dlq(
    task_id: str,
    _admin: Requester = Depends(require_admin),
) -> dict[str, Any]:
    """Re-dispatch the original task from its stored signature, then drop
    the DLQ entry."""
    redis = RedisRepository()
    try:
        payload = await redis._get_json(f"dlq:tasks:{task_id}")
        if not payload:
            raise HTTPException(status_code=404, detail="not_in_dlq")
        kwargs = dict(payload.get("kwargs") or {})
        # Override task_id so the retry has a fresh status row
        new_task_id = "retry-" + task_id
        kwargs["task_id"] = new_task_id

        task_name = payload.get("task")
        if task_name not in {"ingest_document"}:
            raise HTTPException(status_code=400, detail="unknown_task_for_retry")

        celery_app.send_task(task_name, kwargs=kwargs)
        await redis.client.delete(f"dlq:tasks:{task_id}")
        log.info("admin.dlq.retried", original=task_id, new=new_task_id)
        return {"original_task_id": task_id, "new_task_id": new_task_id,
                "task": task_name}
    finally:
        await redis.close()
