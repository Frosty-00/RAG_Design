"""Vertex Gemini LLM client.

Inner client used directly by RAG pipeline / query understanding /
evaluation judge — no LlamaIndex coupling. A LlamaIndex `CustomLLM` adapter
will be added separately when Layer 10 needs `llama_index.core.evaluation`.

Token usage is recorded back to Redis (`usage:user:*`, `usage:session:*`,
`usage:daily:*`) on every call so Layer 9 can enforce limits + Prometheus
can scrape them.
"""
from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator
from typing import Any, ClassVar, TypeVar

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.redis_repo import RedisRepository

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMUsage(BaseModel):
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class VertexGeminiClient:
    """Async Gemini client backed by Vertex AI.

    All public methods are coroutines. Token usage on each call is logged and
    optionally recorded into Redis (when `user_id` / `session_id` provided).
    """

    _instance: ClassVar["VertexGeminiClient | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        project: str | None = None,
        location: str | None = None,
        gen_model: str | None = None,
        judge_model: str | None = None,
        credentials_path: str | None = None,
    ) -> None:
        creds = credentials_path or settings.google_application_credentials
        if creds:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds

        self.project = project or settings.vertex_project
        self.location = location or settings.vertex_location
        self.gen_model = gen_model or settings.vertex_generation_model
        self.judge_model = judge_model or settings.vertex_judge_model

        if not self.project:
            raise RuntimeError(
                "VERTEX_PROJECT not set; LLM client cannot be instantiated."
            )

        self.client = genai.Client(
            vertexai=True, project=self.project, location=self.location,
        )
        log.info("llm.ready", project=self.project, location=self.location,
                 model=self.gen_model)

    # ----------------------------------------------------------- singleton

    @classmethod
    def get(cls) -> "VertexGeminiClient":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ----------------------------------------------------------- core API

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, LLMUsage]:
        """Non-streaming text generation. Returns (text, usage)."""
        m = model or self.gen_model
        cfg = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        resp = await self.client.aio.models.generate_content(
            model=m, contents=prompt, config=cfg,
        )
        text = (resp.text or "").strip()
        usage = self._extract_usage(m, resp)
        await self._record_usage(usage, user_id=user_id, session_id=session_id)
        return text, usage

    async def stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream text chunks. Final chunk is followed by an internal usage
        recording — caller can read `last_usage` after iteration."""
        m = model or self.gen_model
        cfg = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        prompt_tokens = 0
        output_tokens = 0
        total_tokens = 0

        stream = await self.client.aio.models.generate_content_stream(
            model=m, contents=prompt, config=cfg,
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
            if chunk.usage_metadata:
                prompt_tokens = chunk.usage_metadata.prompt_token_count or prompt_tokens
                output_tokens = chunk.usage_metadata.candidates_token_count or output_tokens
                total_tokens = chunk.usage_metadata.total_token_count or total_tokens

        usage = LLMUsage(
            model=m,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        await self._record_usage(usage, user_id=user_id, session_id=session_id)
        # stash for tests / observability
        self._last_usage = usage

    async def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[T, LLMUsage]:
        """Force the model to return JSON conforming to `schema` (pydantic).
        Used for query understanding + evaluation judge.
        """
        m = model or self.gen_model
        cfg = self._build_config(
            system_instruction=system_instruction,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        resp = await self.client.aio.models.generate_content(
            model=m, contents=prompt, config=cfg,
        )
        usage = self._extract_usage(m, resp)
        await self._record_usage(usage, user_id=user_id, session_id=session_id)

        parsed = resp.parsed
        if parsed is None:
            # SDK didn't auto-parse; fall back to manual JSON load
            import json
            parsed = schema.model_validate(json.loads(resp.text))
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed)
        return parsed, usage

    # ----------------------------------------------------------- helpers

    def _build_config(
        self,
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_mime_type: str | None = None,
        response_schema: Any = None,
    ) -> genai_types.GenerateContentConfig:
        kwargs: dict[str, Any] = {}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if response_mime_type:
            kwargs["response_mime_type"] = response_mime_type
        if response_schema is not None:
            kwargs["response_schema"] = response_schema
        return genai_types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _extract_usage(model: str, resp) -> LLMUsage:
        meta = getattr(resp, "usage_metadata", None)
        if meta is None:
            return LLMUsage(model=model)
        return LLMUsage(
            model=model,
            prompt_tokens=meta.prompt_token_count or 0,
            output_tokens=meta.candidates_token_count or 0,
            total_tokens=meta.total_token_count or 0,
        )

    @staticmethod
    async def _record_usage(
        usage: LLMUsage,
        *,
        user_id: str | None,
        session_id: str | None,
    ) -> None:
        if not (user_id or session_id) or usage.total_tokens == 0:
            return
        redis = RedisRepository()
        try:
            await redis.incr_usage(
                user_id=user_id or "anonymous",
                session_id=session_id,
                tokens=usage.total_tokens,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("llm.usage.record_failed", err=str(e))
        finally:
            await redis.close()


# ─────────────────────────────────────────────────────────────────────
# Provider factory
# ─────────────────────────────────────────────────────────────────────


def get_llm_client():
    """Return the active LLM client per `settings.llm_provider`.

    Both clients (Vertex / DeepSeek) implement the same async surface:
        generate(prompt, ...)            → (text, LLMUsage)
        stream(prompt, ...)              → AsyncIterator[str]
        generate_structured(prompt, S)   → (S instance, LLMUsage)
    plus `gen_model` and `judge_model` attributes.
    """
    if settings.llm_provider == "deepseek":
        from app.services.llm_deepseek import DeepSeekClient
        return DeepSeekClient.get()
    return VertexGeminiClient.get()
