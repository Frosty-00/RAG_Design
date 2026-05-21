"""Admin-only endpoints — token management + DLQ inspection/retry."""
from __future__ import annotations

import secrets
from typing import Any, Literal

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
# Tokens / Users
# ─────────────────────────────────────────────────────────────────────


class TokenIssueRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64,
                         pattern=r"^[A-Za-z0-9_\-]+$")
    groups: list[str] = []
    role: Literal["user", "admin"] = "user"
    # When True (default), the issued token is the predictable string
    # `{user_id}-dev-token` — great for local demos so admin can hand
    # someone a memorable token. Set False in production to fall back
    # to a cryptographically random 32-byte token.
    predictable: bool = True


class TokenIssueResponse(BaseModel):
    token: str
    user_id: str
    role: str
    groups: list[str] = []


def _predictable_token(user_id: str) -> str:
    """Stable, human-readable token for local/demo use.
    Mirrors the existing ADMIN_TOKEN naming convention (`admin-dev-token`)."""
    return f"{user_id}-dev-token"


@router.post("/tokens", response_model=TokenIssueResponse)
async def issue_token(
    body: TokenIssueRequest,
    _admin: Requester = Depends(require_admin),
) -> TokenIssueResponse:
    """Issue a token for a user. Idempotent in predictable mode — re-issuing
    for the same user_id overwrites their previous token (same string)."""
    raw = (
        _predictable_token(body.user_id)
        if body.predictable
        else secrets.token_urlsafe(32)
    )
    redis = RedisRepository()
    try:
        await redis.store_token(
            raw, user_id=body.user_id, groups=body.groups, role=body.role,
        )
    finally:
        await redis.close()
    log.info("admin.token_issued", user=body.user_id, role=body.role,
             predictable=body.predictable)
    return TokenIssueResponse(
        token=raw, user_id=body.user_id, role=body.role, groups=body.groups,
    )


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


class UserInfo(BaseModel):
    user_id: str
    role: str
    groups: list[str] = []
    n_tokens: int
    # The most recently-issued predictable token, if it still exists.
    # Lets the admin UI offer one-click "switch to this user".
    predictable_token: str | None = None


@router.get("/users", response_model=list[UserInfo])
async def list_users(
    _admin: Requester = Depends(require_admin),
) -> list[UserInfo]:
    """Enumerate every known user (anyone who has at least one live token)."""
    redis = RedisRepository()
    try:
        users = await redis.list_all_users()
    finally:
        await redis.close()
    out: list[UserInfo] = []
    for u in users:
        predictable = _predictable_token(u["user_id"])
        out.append(UserInfo(
            user_id=u["user_id"],
            role=u.get("role", "user"),
            groups=u.get("groups", []),
            n_tokens=u.get("n_tokens", 0),
            # Only surface the predictable token if it actually resolves;
            # avoids handing out a guess that's been revoked.
            predictable_token=predictable if u.get("has_predictable") else None,
        ))
    # Stable ordering: admin first, then alphabetical
    out.sort(key=lambda u: (u.role != "admin", u.user_id))
    return out


@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: str,
    admin: Requester = Depends(require_admin),
) -> dict:
    """Revoke every token belonging to `user_id`. Refuses to delete the
    current admin (locking yourself out is rarely intended)."""
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=400,
            detail="cannot_delete_self — sign in as another admin first",
        )
    redis = RedisRepository()
    try:
        n = await redis.revoke_all_user_tokens(user_id)
    finally:
        await redis.close()
    log.info("admin.user_deleted", user=user_id, tokens_removed=n)
    return {"user_id": user_id, "tokens_removed": n}


# ─────────────────────────────────────────────────────────────────────
# Current-user inspection (any authenticated caller)
# ─────────────────────────────────────────────────────────────────────


from app.api.deps import get_requester  # noqa: E402  — keep grouping clean


class MeResponse(BaseModel):
    user_id: str
    role: str
    groups: list[str] = []
    is_admin: bool


# Mount under /admin/me to keep all auth-related endpoints together; the
# auth check is `get_requester` (not require_admin) so any signed-in user
# can ask "who am I" — the Nav uses this to render the current-user badge.
@router.get("/me", response_model=MeResponse)
async def me(
    requester: Requester = Depends(get_requester),
) -> MeResponse:
    return MeResponse(
        user_id=requester.user_id,
        role="admin" if requester.is_admin else "user",
        groups=list(requester.groups),
        is_admin=requester.is_admin,
    )


class PickerUser(BaseModel):
    user_id: str
    role: str
    groups: list[str] = []


@router.get("/picker/users", response_model=list[PickerUser])
async def picker_users(
    requester: Requester = Depends(get_requester),
) -> list[PickerUser]:
    """Suggestions source for ACL pickers (upload form, etc).

    Visibility rules:
      * admin caller → every NON-admin user
      * non-admin    → users sharing at least one group with caller

    Admin users are **excluded from picker results entirely** — they
    can already see every uploaded doc by role, so adding them to ACL
    is redundant. Cleaner UX too: the picker only shows user_ids it's
    meaningful to grant access to.

    The caller themself is also excluded (owners auto-have access via
    `owner_id` in the ACL match).
    """
    redis = RedisRepository()
    try:
        users = await redis.list_all_users()
    finally:
        await redis.close()
    caller_groups = set(requester.groups)
    out: list[PickerUser] = []
    for u in users:
        if u.get("role") == "admin":
            continue  # admins are global-readers, granting to them is a no-op
        if u["user_id"] == requester.user_id:
            continue  # owner already has implicit access
        u_groups = set(u.get("groups", []))
        if requester.is_admin or (caller_groups & u_groups):
            out.append(PickerUser(
                user_id=u["user_id"],
                role=u.get("role", "user"),
                groups=u.get("groups", []),
            ))
    out.sort(key=lambda u: u.user_id)
    return out


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
