"""POST /chat — RAG SSE endpoint."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_pipeline, get_requester
from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.milvus import Requester
from app.repositories.redis_repo import RedisRepository
from app.services.rag import ChatChunk, RAGPipeline

log = get_logger(__name__)

# Per-user-per-minute chat budget. Cheap fixed-window Redis counter — enough
# to shed sudden bursts. Token-bucket / sliding-window can be swapped in here.
CHAT_RPM_PER_USER = 30


async def _check_chat_rate_limit(requester: Requester) -> None:
    redis = RedisRepository()
    try:
        bucket = int(time.time() // 60)
        key = f"rl:chat:{requester.user_id}:{bucket}"
        count = await redis.client.incr(key)
        if count == 1:
            await redis.client.expire(key, 70)
        if count > CHAT_RPM_PER_USER:
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited",
                        "limit_per_minute": CHAT_RPM_PER_USER},
            )
    finally:
        await redis.close()


router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None


def _serialize(ev: ChatChunk) -> dict:
    """Public-facing JSON shape for one SSE event."""
    out: dict = {"event": ev.event}
    if ev.phase:
        out["phase"] = ev.phase
    if ev.token is not None:
        out["token"] = ev.token
    if ev.citations is not None:
        out["citations"] = [c.to_dict() for c in ev.citations]
    if ev.meta:
        out["meta"] = ev.meta
    if ev.error:
        out["error"] = ev.error
    return out


def _format_sse(event_name: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_name}\ndata: {payload}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    requester: Requester = Depends(get_requester),
    pipeline: RAGPipeline = Depends(get_pipeline),
) -> StreamingResponse:
    # ─── 0. RPM rate limit (Layer 15) ───
    await _check_chat_rate_limit(requester)

    # ─── 1. token-budget gate ───
    redis_check = RedisRepository()
    try:
        used_today = await redis_check.get_user_usage(requester.user_id)
    finally:
        await redis_check.close()
    if used_today >= settings.llm_daily_user_token_limit:
        raise HTTPException(
            status_code=429,
            detail={"reason": "daily_token_limit_exceeded",
                    "used": used_today,
                    "limit": settings.llm_daily_user_token_limit},
        )

    # ─── 2. load session history ───
    history: list[dict] = []
    if body.session_id:
        redis_h = RedisRepository()
        try:
            history = await redis_h.get_session(body.session_id)
        finally:
            await redis_h.close()

    # ─── 3. stream RAG events as SSE ───
    async def event_stream() -> AsyncIterator[bytes]:
        full_text: list[str] = []
        try:
            async for ev in pipeline.answer_stream(
                body.query, history,
                requester=requester,
                session_id=body.session_id,
            ):
                yield _format_sse(ev.event, _serialize(ev)).encode("utf-8")
                if ev.event == "token" and ev.token:
                    full_text.append(ev.token)
        except Exception as e:  # noqa: BLE001
            log.error("chat.stream_failed", err=str(e), user=requester.user_id)
            yield _format_sse("error", {"event": "error", "error": str(e)}).encode("utf-8")
            return
        # Persist conversation turn
        if body.session_id:
            r = RedisRepository()
            try:
                await r.append_session_turn(body.session_id, "user", body.query)
                await r.append_session_turn(
                    body.session_id, "assistant", "".join(full_text),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("chat.session_save_failed", err=str(e))
            finally:
                await r.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
