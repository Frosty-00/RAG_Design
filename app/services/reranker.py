"""bge-reranker-v2-m3 — cross-encoder for re-ordering top-k retrieval results.

Used by `Retriever` (Layer 6) after Hybrid+RRF gives ~20 candidates: the
reranker scores each (query, chunk) pair and we keep the top `rerank_k`.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import ClassVar, Generic, TypeVar

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

T = TypeVar("T")


@dataclass
class RerankResult(Generic[T]):
    item: T
    score: float


class BGEReranker:
    """Wraps `FlagEmbedding.FlagReranker`. Singleton (heavy weights)."""

    _instance: ClassVar["BGEReranker | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        model_name: str | None = None,
        use_fp16: bool = False,
        cache_dir: str | None = None,
    ) -> None:
        from FlagEmbedding import FlagReranker

        cache_dir = cache_dir or settings.model_cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        os.environ.setdefault("HF_HOME", cache_dir)

        name = model_name or settings.reranker_model
        log.info("reranker.loading", model=name, cache_dir=cache_dir, fp16=use_fp16)
        self.model = FlagReranker(
            name,
            use_fp16=use_fp16,
            cache_dir=cache_dir,
        )
        self.model_name = name
        log.info("reranker.loaded", model=name)

    # ------------------------------------------------------------------- public

    def rerank(
        self,
        query: str,
        items: list[T],
        *,
        text_of: callable = None,  # type: ignore[assignment]
        k: int | None = None,
        normalize: bool = True,
    ) -> list[RerankResult[T]]:
        """Score each `(query, text_of(item))` pair, return items sorted desc.

        - `items` may be raw strings or richer objects; `text_of(item)` extracts
          the string the reranker scores against. Defaults to identity for str.
        - `k` clips the result to top-k (None = all).
        - `normalize=True` maps logits → [0, 1] sigmoid (easier to threshold).
        """
        if not items:
            return []

        text_fn = text_of or (lambda x: x if isinstance(x, str) else str(x))
        pairs = [[query, text_fn(it)] for it in items]

        scores = self.model.compute_score(pairs, normalize=normalize)
        # FlagReranker returns float for single pair, list for multi
        if isinstance(scores, (int, float)):
            scores = [float(scores)]
        else:
            scores = [float(s) for s in scores]

        ranked = sorted(
            (RerankResult(item=it, score=s) for it, s in zip(items, scores)),
            key=lambda r: r.score,
            reverse=True,
        )
        if k is not None:
            ranked = ranked[:k]
        return ranked

    # ----------------------------------------------------------------- singleton

    @classmethod
    def get(cls, **kwargs) -> "BGEReranker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None
