"""DeepSeek LLM client — same interface as `VertexGeminiClient`.

DeepSeek's API is OpenAI-compatible, so we use the `openai` SDK pointed
at https://api.deepseek.com.

Model mapping vs Vertex:
    deepseek-chat       ↔ gemini-2.5-flash      (generation)
    deepseek-reasoner   ↔ gemini-2.5-pro        (judge)

Differences vs `VertexGeminiClient`:
    - No native `response_schema`. We achieve structured output by
      injecting the JSON schema into the prompt + `response_format=json_object`,
      then validating with the supplied pydantic class. Stripping any
      ```json ...``` code fence is included.
    - No context caching (generation API doesn't expose it). The token
      counter still records usage to Redis.
"""
from __future__ import annotations

import json
import re
import threading
from collections.abc import AsyncIterator
from typing import ClassVar, TypeVar

from pydantic import BaseModel

from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.redis_repo import RedisRepository
from app.services.llm import LLMUsage  # re-use the existing dataclass

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fence(s: str) -> str:
    m = _FENCE_RE.match(s)
    return m.group(1) if m else s


class DeepSeekClient:
    """OpenAI-compatible client targeting DeepSeek."""

    _instance: ClassVar["DeepSeekClient | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        gen_model: str | None = None,
        judge_model: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        key = api_key or settings.deepseek_api_key
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY not set")
        self.client = AsyncOpenAI(
            api_key=key,
            base_url=base_url or settings.deepseek_base_url,
        )
        self.gen_model = gen_model or settings.deepseek_chat_model
        self.judge_model = judge_model or settings.deepseek_judge_model
        log.info("llm.deepseek.ready", base=settings.deepseek_base_url,
                 gen=self.gen_model, judge=self.judge_model)

    # ----------------------------------------------------------- singleton

    @classmethod
    def get(cls) -> "DeepSeekClient":
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
        m = model or self.gen_model
        msgs = self._messages(system_instruction, prompt)
        kwargs = self._completion_kwargs(temperature, max_output_tokens)
        resp = await self.client.chat.completions.create(model=m, messages=msgs, **kwargs)
        text = (resp.choices[0].message.content or "").strip()
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
        m = model or self.gen_model
        msgs = self._messages(system_instruction, prompt)
        kwargs = self._completion_kwargs(temperature, max_output_tokens)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        prompt_tokens = output_tokens = total_tokens = 0
        stream = await self.client.chat.completions.create(
            model=m, messages=msgs, **kwargs,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if getattr(chunk, "usage", None):
                u = chunk.usage
                prompt_tokens = u.prompt_tokens or prompt_tokens
                output_tokens = u.completion_tokens or output_tokens
                total_tokens = u.total_tokens or total_tokens
        usage = LLMUsage(
            model=m, prompt_tokens=prompt_tokens,
            output_tokens=output_tokens, total_tokens=total_tokens,
        )
        await self._record_usage(usage, user_id=user_id, session_id=session_id)
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
        m = model or self.gen_model
        # DeepSeek requires the prompt to mention "json" when using json_object mode
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        full_prompt = (
            f"{prompt}\n\n"
            "IMPORTANT: respond with a single JSON object that matches this schema. "
            "Do not include explanations, markdown, or code fences.\n\n"
            f"{schema_json}"
        )
        msgs = self._messages(system_instruction, full_prompt)
        kwargs = self._completion_kwargs(temperature, None)
        kwargs["response_format"] = {"type": "json_object"}

        resp = await self.client.chat.completions.create(
            model=m, messages=msgs, **kwargs,
        )
        text = _strip_code_fence((resp.choices[0].message.content or "").strip())
        usage = self._extract_usage(m, resp)
        await self._record_usage(usage, user_id=user_id, session_id=session_id)

        try:
            parsed = schema.model_validate_json(text)
        except Exception as e:
            log.warning("deepseek.structured_parse_failed", err=str(e), raw=text[:200])
            # Last-ditch: try ast / json.loads
            parsed = schema.model_validate(json.loads(text))
        return parsed, usage

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _messages(system_instruction: str | None, prompt: str) -> list[dict]:
        msgs = []
        if system_instruction:
            msgs.append({"role": "system", "content": system_instruction})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    @staticmethod
    def _completion_kwargs(
        temperature: float | None, max_output_tokens: int | None,
    ) -> dict:
        kw: dict = {}
        if temperature is not None:
            kw["temperature"] = temperature
        if max_output_tokens is not None:
            kw["max_tokens"] = max_output_tokens
        return kw

    @staticmethod
    def _extract_usage(model: str, resp) -> LLMUsage:
        u = getattr(resp, "usage", None)
        if u is None:
            return LLMUsage(model=model)
        return LLMUsage(
            model=model,
            prompt_tokens=u.prompt_tokens or 0,
            output_tokens=u.completion_tokens or 0,
            total_tokens=u.total_tokens or 0,
        )

    @staticmethod
    async def _record_usage(
        usage: LLMUsage, *, user_id: str | None, session_id: str | None,
    ) -> None:
        if not (user_id or session_id) or usage.total_tokens == 0:
            return
        redis = RedisRepository()
        try:
            await redis.incr_usage(
                user_id=user_id or "anonymous", session_id=session_id,
                tokens=usage.total_tokens,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("deepseek.usage.record_failed", err=str(e))
        finally:
            await redis.close()
