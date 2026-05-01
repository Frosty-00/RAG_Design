"""RAGPipeline — orchestrates Layers 6/7 + LLM stream into a single
async generator of `ChatChunk`s suitable for SSE encoding (Layer 9).

Flow:
    -1. yield ack(accepted)            ← user-perceived "I heard you"
     0. start understanding + speculative retrieval in parallel
     1. await understanding
        if chitchat:
            cancel speculative; render chat_chitchat; stream LLM tokens
        else:
     2. yield ack(retrieving)
        if speculative is reusable (resolved_query == original, no rewrites):
            chunks = await speculative
        else:
            cancel speculative; run real retrieval (with optional multi-query)
     3. yield ack(generating)
        if no chunks:
            yield "未在知识库中找到相关内容。" + empty citations
        else:
            render chat_rag prompt; stream LLM tokens; yield citations at end
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.milvus import Requester
from app.services.llm import VertexGeminiClient, get_llm_client
from app.services.prompts import PromptManager
from app.services.query_understanding import (
    QueryUnderstandingPipeline,
    UnderstandingResult,
)
from app.services.query_understanding.pipeline import _format_history
from app.services.retrieval import Retriever, ScoredChunk

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Public output type
# ─────────────────────────────────────────────────────────────────────


@dataclass
class Citation:
    index: int
    chunk_id: str
    doc_id: str
    page: int | None = None
    breadcrumbs: list[str] = field(default_factory=list)
    text_preview: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "page": self.page,
            "breadcrumbs": self.breadcrumbs,
            "text_preview": self.text_preview,
        }


@dataclass
class ChatChunk:
    """One streaming event from RAGPipeline."""

    event: Literal["ack", "token", "citations", "error"]
    phase: str | None = None         # for "ack": accepted | retrieving | generating
    token: str | None = None         # for "token"
    citations: list[Citation] | None = None
    meta: dict[str, Any] | None = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _format_context(chunks: list[ScoredChunk]) -> str:
    """Render retrieved chunks as labelled passages for the LLM.

    Sorts chunks globally by (doc_id, chunk_index) before grouping so any
    set of chunks that are adjacent in the source document collapse into
    a single `[N]` block — even when they arrived from different rerank
    positions. The earlier behaviour (group-by-emission-order) failed
    when, e.g., rerank put c0000 first and c0001 fifth: those two are
    physically adjacent in the doc but never grouped, so the LLM saw a
    fragmented [1]/[5] split that should have been one continuous passage.
    Cluster ordering follows the smallest chunk_index in each cluster, i.e.
    document narrative order — which is also how a human would read it.
    """
    if not chunks:
        return ""

    # Stable sort by (doc_id, chunk_index). Falls back gracefully if
    # chunk_index is missing — shouldn't happen, but defensive.
    sorted_chunks = sorted(
        chunks,
        key=lambda c: (
            c.doc_id or "",
            (c.metadata or {}).get("chunk_index") or 0,
        ),
    )

    # 1. Build groups of consecutive chunks
    groups: list[list[ScoredChunk]] = []
    for c in sorted_chunks:
        idx = (c.metadata or {}).get("chunk_index")
        if not groups:
            groups.append([c])
            continue
        last = groups[-1][-1]
        last_idx = (last.metadata or {}).get("chunk_index")
        if (
            isinstance(idx, int)
            and isinstance(last_idx, int)
            and c.doc_id == last.doc_id
            and idx == last_idx + 1
        ):
            groups[-1].append(c)
        else:
            groups.append([c])

    # 2. Render
    lines: list[str] = []
    for n, group in enumerate(groups, start=1):
        head = group[0]
        crumbs = (head.metadata or {}).get("breadcrumbs")
        crumb_str = " > ".join(crumbs) if crumbs else ""
        header = f"[{n}]" + (f" {crumb_str}" if crumb_str else "")
        # Join contiguous chunk texts with a single newline (they were
        # split arbitrarily by the splitter, not by a paragraph boundary)
        body = "\n".join(c.text for c in group)
        lines.append(f"{header}\n{body}")
    return "\n\n".join(lines)


def _build_citations(chunks: list[ScoredChunk]) -> list[Citation]:
    """Build the citation list shown in the UI side panel.

    Mirrors the grouping in `_format_context` (sort by doc/chunk_index
    then collapse consecutive runs) so the `[N]` numbers in the LLM
    answer line up 1-to-1 with the citation panel.
    """
    if not chunks:
        return []

    sorted_chunks = sorted(
        chunks,
        key=lambda c: (
            c.doc_id or "",
            (c.metadata or {}).get("chunk_index") or 0,
        ),
    )

    groups: list[list[ScoredChunk]] = []
    for c in sorted_chunks:
        idx = (c.metadata or {}).get("chunk_index")
        if not groups:
            groups.append([c])
            continue
        last = groups[-1][-1]
        last_idx = (last.metadata or {}).get("chunk_index")
        if (
            isinstance(idx, int)
            and isinstance(last_idx, int)
            and c.doc_id == last.doc_id
            and idx == last_idx + 1
        ):
            groups[-1].append(c)
        else:
            groups.append([c])

    out: list[Citation] = []
    for i, group in enumerate(groups, start=1):
        # Use the highest-ranked chunk (i.e. the original retrieval hit;
        # neighbors are appended after their parent) as the canonical
        # source for chunk_id / page / breadcrumbs.
        head = group[0]
        md = head.metadata or {}
        joined_text = "\n".join(c.text for c in group)
        preview = (joined_text[:200] + "...") if len(joined_text) > 200 else joined_text
        out.append(Citation(
            index=i,
            chunk_id=head.chunk_id,
            doc_id=head.doc_id,
            page=md.get("page"),
            breadcrumbs=list(md.get("breadcrumbs") or []),
            text_preview=preview,
        ))
    return out


def _is_speculative_reusable(
    original: str, understanding: UnderstandingResult
) -> bool:
    """Whether the original-query retrieval result can be reused as-is."""
    if understanding.rewrites:
        return False  # multi-query → must do parallel fan-out
    return original.strip().lower() == understanding.resolved_query.strip().lower()


# ─────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────


class RAGPipeline:
    def __init__(
        self,
        retriever: Retriever | None = None,
        understanding: QueryUnderstandingPipeline | None = None,
        llm=None,  # any LLMClient-shape (Vertex / DeepSeek)
        prompts: PromptManager | None = None,
        *,
        deterministic: bool = False,
    ) -> None:
        self.retriever = retriever or Retriever()
        self.understanding = understanding or QueryUnderstandingPipeline(llm=llm)
        self.llm = llm or get_llm_client()
        self.prompts = prompts or PromptManager.get_instance()
        # When True, force temperature=0 on the answer-generation LLM so
        # repeated eval runs over the same dataset produce stable scores.
        # Query Understanding is intentionally NOT forced — evaluation must
        # exercise the same stochastic path production users hit.
        self.deterministic = deterministic

    async def aclose(self) -> None:
        await self.retriever.aclose()

    async def answer_stream(
        self,
        query: str,
        history: list[dict[str, Any]] | None = None,
        *,
        requester: Requester | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        history = history or []
        t0 = time.perf_counter()

        # 0. SSE: instant ack
        yield ChatChunk(event="ack", phase="accepted")

        # 1. Run Understanding + speculative retrieval in parallel
        u_task = asyncio.create_task(self.understanding.run(
            query, history,
            user_id=requester.user_id if requester else None,
            session_id=session_id,
        ))
        spec_task = asyncio.create_task(self.retriever.retrieve(
            query, requester=requester,
            top_k=settings.retrieval_top_k,
            rerank_k=settings.rerank_top_k,
        ))

        try:
            understanding = await u_task
        except Exception as e:  # noqa: BLE001
            log.error("rag.understanding_failed", err=str(e))
            spec_task.cancel()
            yield ChatChunk(event="error", error="query understanding failed")
            return

        # ── chitchat short-circuit ───────────────────────────────────
        if understanding.is_chitchat:
            spec_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await spec_task
            yield ChatChunk(event="ack", phase="generating")
            rendered = self.prompts.render(
                "chat_chitchat",
                history=_format_history(history),
                question=query,
            )
            async for tok in self.llm.stream(
                rendered.text,
                user_id=requester.user_id if requester else None,
                session_id=session_id,
                temperature=0.0 if self.deterministic else None,
            ):
                yield ChatChunk(event="token", token=tok)
            elapsed = (time.perf_counter() - t0) * 1000
            yield ChatChunk(
                event="citations",
                citations=[],
                meta={"path": "chitchat",
                      "elapsed_ms": round(elapsed, 1),
                      "prompt_versions": {
                          "chat_chitchat": rendered.version,
                          "query_understanding": 1,
                      }},
            )
            return

        # ── retrieval path ──────────────────────────────────────────
        yield ChatChunk(event="ack", phase="retrieving")

        speculative_used = False
        try:
            if _is_speculative_reusable(query, understanding):
                chunks = (await spec_task).chunks
                speculative_used = True
            else:
                spec_task.cancel()
                with contextlib_suppress():
                    await spec_task
                chunks = await self._do_retrieval(understanding, requester)
        except Exception as e:  # noqa: BLE001
            log.error("rag.retrieval_failed", err=str(e))
            yield ChatChunk(event="error", error=f"retrieval failed: {e}")
            return

        yield ChatChunk(event="ack", phase="generating")

        # Empty retrieval → fallback message, no LLM call
        if not chunks:
            fallback = "未在知识库中找到相关内容。"
            yield ChatChunk(event="token", token=fallback)
            elapsed = (time.perf_counter() - t0) * 1000
            yield ChatChunk(
                event="citations",
                citations=[],
                meta={"path": "rag_empty",
                      "speculative_used": speculative_used,
                      "elapsed_ms": round(elapsed, 1)},
            )
            return

        # Render & stream
        context_text = _format_context(chunks)
        rendered = self.prompts.render(
            "chat_rag",
            context=context_text,
            history=_format_history(history),
            question=understanding.resolved_query,
        )
        async for tok in self.llm.stream(
            rendered.text,
            user_id=requester.user_id if requester else None,
            session_id=session_id,
            temperature=0.0 if self.deterministic else None,
        ):
            yield ChatChunk(event="token", token=tok)

        citations = _build_citations(chunks)
        elapsed = (time.perf_counter() - t0) * 1000
        yield ChatChunk(
            event="citations",
            citations=citations,
            meta={
                "path": "rag",
                "speculative_used": speculative_used,
                "n_chunks": len(chunks),
                "elapsed_ms": round(elapsed, 1),
                "prompt_versions": {
                    "chat_rag": rendered.version,
                    "query_understanding": 1,
                },
                "understanding": understanding.model_dump(),
            },
        )

    # ----------------------------------------------------------- internals

    async def _do_retrieval(
        self,
        understanding: UnderstandingResult,
        requester: Requester | None,
    ) -> list[ScoredChunk]:
        """Run actual retrieval based on understanding output."""
        queries = understanding.all_queries() \
            if settings.feature_multi_query and understanding.rewrites \
            else [understanding.resolved_query]
        if len(queries) == 1:
            res = await self.retriever.retrieve(
                queries[0], requester=requester,
                top_k=settings.retrieval_top_k,
                rerank_k=settings.rerank_top_k,
            )
        else:
            res = await self.retriever.retrieve_multi(
                queries, requester=requester,
                top_k=settings.retrieval_top_k,
                rerank_k=settings.rerank_top_k,
                rerank_query=understanding.resolved_query,
            )
        return res.chunks


