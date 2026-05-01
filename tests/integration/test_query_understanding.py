"""Layer 7c — Query Understanding integration tests.

Live LLM tests skip if VERTEX_PROJECT not set; keyword fast-path test
runs unconditionally (no LLM call).
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services.query_understanding import (
    QueryUnderstandingPipeline,
    UnderstandingResult,
)

LIVE = bool(settings.vertex_project)


# ─────────────────────────────────────────────────────────────────────
# Fast-path (no LLM) — runs without credentials
# ─────────────────────────────────────────────────────────────────────


class TestKeywordFastPath:
    @pytest.mark.parametrize("q", ["你好", "hi", "hello", "thanks", "再见", "ok"])
    def test_chitchat_keywords(self, q: str):
        pipe = QueryUnderstandingPipeline()
        # Should NOT call LLM — even without credentials this must work
        result = asyncio.run(pipe.run(q))
        assert isinstance(result, UnderstandingResult)
        assert result.is_chitchat
        assert result.rewrites == []
        assert result.resolved_query == q.strip()

    def test_long_query_not_keyword_match(self):
        # Same word but long → not a keyword chitchat; would normally call LLM.
        # Skip if no live creds; otherwise assert it is NOT chitchat.
        if not LIVE:
            pytest.skip("requires LLM")
        pipe = QueryUnderstandingPipeline()
        result = asyncio.run(pipe.run(
            "你好，请问公司年假怎么计算？",  # > 8 chars; not a fast-path hit
        ))
        # this query is a real KB question so model should say needs_retrieval
        assert result.intent == "needs_retrieval"


# ─────────────────────────────────────────────────────────────────────
# Live LLM tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not LIVE, reason="requires VERTEX_PROJECT")
class TestLiveUnderstanding:
    def test_factual_question_intent(self):
        pipe = QueryUnderstandingPipeline()
        result = asyncio.run(pipe.run("How does machine learning work?"))
        assert result.intent == "needs_retrieval"
        assert result.resolved_query.lower().strip() == "how does machine learning work?"

    def test_coreference_resolved_with_history(self):
        pipe = QueryUnderstandingPipeline()
        history = [
            {"role": "user", "content": "公司有几种年假？"},
            {"role": "assistant", "content": "包括法定年假、福利年假和病假。"},
        ]
        result = asyncio.run(pipe.run(
            "它的天数怎么算的？", history=history,
        ))
        # resolved_query should mention 年假
        assert "年假" in result.resolved_query
        assert result.intent == "needs_retrieval"

    def test_multi_query_off_no_rewrites(self):
        pipe = QueryUnderstandingPipeline()
        result = asyncio.run(pipe.run(
            "How does retrieval-augmented generation reduce hallucinations?",
            enable_multi_query=False,
        ))
        assert result.rewrites == []

    def test_multi_query_on_yields_rewrites(self):
        pipe = QueryUnderstandingPipeline()
        result = asyncio.run(pipe.run(
            "How does retrieval-augmented generation reduce hallucinations?",
            enable_multi_query=True,
        ))
        assert result.intent == "needs_retrieval"
        # Most providers will return 2+; tolerate 1 if model is conservative
        assert len(result.rewrites) >= 1
        # rewrites != original (or close to)
        assert not all(r.lower() == result.resolved_query.lower()
                       for r in result.rewrites)
