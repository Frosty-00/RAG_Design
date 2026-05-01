"""Layer 7a — Vertex Gemini LLM client (live tests).

Skipped automatically when `VERTEX_PROJECT` is empty.
First run will hit the network; budget ~50 tokens per test.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.llm import VertexGeminiClient

pytestmark = pytest.mark.skipif(
    not settings.vertex_project,
    reason="VERTEX_PROJECT not configured; LLM live tests skipped",
)


@pytest.fixture(scope="module")
def llm() -> VertexGeminiClient:
    VertexGeminiClient.reset_instance()
    return VertexGeminiClient.get()


class TestGenerate:
    def test_simple_generate(self, llm: VertexGeminiClient):
        async def go():
            text, usage = await llm.generate(
                "Reply with exactly the single word: PONG",
                temperature=0.0, max_output_tokens=64,
            )
            return text, usage

        text, usage = asyncio.run(go())
        assert "PONG" in text.upper()
        assert usage.prompt_tokens > 0
        assert usage.output_tokens > 0
        assert usage.total_tokens >= usage.prompt_tokens + usage.output_tokens \
            or usage.total_tokens >= usage.prompt_tokens

    def test_streaming_yields_chunks(self, llm: VertexGeminiClient):
        async def go():
            chunks = []
            async for piece in llm.stream(
                "Reply with exactly: A B C D E",
                temperature=0.0, max_output_tokens=64,
            ):
                chunks.append(piece)
            return chunks

        chunks = asyncio.run(go())
        assert chunks, "stream produced no chunks"
        joined = "".join(chunks).upper()
        assert "A" in joined and "E" in joined


class _Sentiment(BaseModel):
    label: str = Field(description='one of: positive, negative, neutral')
    confidence: float = Field(description="0..1")


class TestStructuredOutput:
    def test_structured_returns_valid_pydantic(self, llm: VertexGeminiClient):
        async def go():
            obj, usage = await llm.generate_structured(
                'Classify the sentiment of: "I love this product, it works great."',
                _Sentiment,
                temperature=0.0,
            )
            return obj, usage

        obj, usage = asyncio.run(go())
        assert isinstance(obj, _Sentiment)
        assert obj.label.lower() in {"positive", "negative", "neutral"}
        assert 0.0 <= obj.confidence <= 1.0
        assert usage.total_tokens > 0
