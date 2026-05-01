"""Query Understanding pipeline.

Three things in one LLM call:
  1. Coreference resolution
  2. Intent classification (needs_retrieval | chitchat)
  3. Multi-query rewriting (if `enable_multi_query` is true)

Fast paths (no LLM):
  - keyword chitchat hit: query stripped lower-cased ∈ CHITCHAT_KEYWORDS and
    len ≤ 8 → return chitchat immediately (~5ms)
  - LLM call failure → graceful fallback to needs_retrieval with empty rewrites,
    so a Vertex outage doesn't kill chat.
"""
from __future__ import annotations

import time
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.services.llm import VertexGeminiClient, get_llm_client
from app.services.prompts import PromptManager
from app.services.query_understanding.schema import UnderstandingResult

log = get_logger(__name__)

CHITCHAT_KEYWORDS: tuple[str, ...] = (
    # english
    "hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye", "ok", "okay",
    # 中文
    "你好", "您好", "嗨", "哈喽", "谢谢", "多谢", "再见", "拜拜",
)


def _format_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "(no prior turns)"
    lines = []
    for turn in history[-6:]:  # last 3 user+assistant pairs
        role = turn.get("role", "user")
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no prior turns)"


def _is_chitchat_keyword(query: str) -> bool:
    q = query.strip().lower()
    if len(q) > 8:
        return False
    return q in CHITCHAT_KEYWORDS


class QueryUnderstandingPipeline:
    def __init__(
        self,
        llm: VertexGeminiClient | None = None,
        prompts: PromptManager | None = None,
    ) -> None:
        self.llm = llm  # lazy: don't construct unless needed (live cred required)
        self.prompts = prompts or PromptManager.get_instance()

    async def run(
        self,
        query: str,
        history: list[dict[str, Any]] | None = None,
        *,
        enable_multi_query: bool | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> UnderstandingResult:
        t0 = time.perf_counter()
        history = history or []

        # ─── fast path 1: keyword chitchat ───
        if _is_chitchat_keyword(query):
            log.info("understanding.fastpath_chitchat", query=query[:32])
            return UnderstandingResult(
                intent="chitchat", resolved_query=query.strip(), rewrites=[],
            )

        multi = settings.feature_multi_query if enable_multi_query is None \
            else enable_multi_query

        rendered = self.prompts.render(
            "query_understanding",
            query=query,
            history=_format_history(history),
            enable_multi_query="true" if multi else "false",
        )

        # Lazy LLM init — pick provider per settings.llm_provider
        llm = self.llm or get_llm_client()

        try:
            result, _usage = await llm.generate_structured(
                rendered.text,
                UnderstandingResult,
                temperature=0.0,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("understanding.fallback", err=str(e))
            return UnderstandingResult(
                intent="needs_retrieval", resolved_query=query.strip(), rewrites=[],
            )

        # Enforce flag at output: if disabled, drop any rewrites the model produced
        if not multi:
            result.rewrites = []
        # Defensive: if intent is chitchat, rewrites must be empty
        if result.is_chitchat:
            result.rewrites = []
        # Defensive: resolved_query must be non-empty
        if not result.resolved_query.strip():
            result.resolved_query = query.strip()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info("understanding.done", intent=result.intent,
                 multi=multi, n_rewrites=len(result.rewrites),
                 elapsed_ms=round(elapsed_ms, 1))
        return result
