"""BGE-M3 embedder — produces both dense (1024-dim) and sparse vectors in one call.

Why BGE-M3:
  - Multilingual, strong on Chinese + English (matches our enterprise KB scenario).
  - Outputs lexical (sparse) weights for free → enables Milvus hybrid search
    without a separate BM25 pass.

The class is exposed as a process-level singleton (`BGEM3Embedder.get()`),
because loading weights is ~2 GB / 30s — we never want to do that twice in
the same Python process. Celery worker and FastAPI are different processes
so each loads once (Layer 15a will move them behind a shared service).
"""
from __future__ import annotations

import os
import threading
from typing import ClassVar

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


class BGEM3Embedder:
    """Wraps `FlagEmbedding.BGEM3FlagModel` with our own thin interface.

    We deliberately do NOT inherit `llama_index.core.embeddings.BaseEmbedding`
    here because (a) BaseEmbedding's contract is dense-only and (b) we use
    `MilvusVectorStore` from LlamaIndex via direct `add(nodes, embeddings=...)`
    in Layer 4, not via the `embed_model` field. A LlamaIndex adapter (only
    needed for evaluation modules) lives in `embedding_li_adapter.py` (later).
    """

    _instance: ClassVar["BGEM3Embedder | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    DIM: ClassVar[int] = 1024  # BGE-M3 dense dim

    def __init__(
        self,
        model_name: str | None = None,
        use_fp16: bool = False,
        cache_dir: str | None = None,
    ) -> None:
        from FlagEmbedding import BGEM3FlagModel

        cache_dir = cache_dir or settings.model_cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        # HF_HOME also picked up by tokenizers / transformers
        os.environ.setdefault("HF_HOME", cache_dir)

        name = model_name or settings.embedding_model
        log.info("embedder.loading", model=name, cache_dir=cache_dir, fp16=use_fp16)
        self.model = BGEM3FlagModel(
            name,
            use_fp16=use_fp16,
            cache_dir=cache_dir,
        )
        self.model_name = name
        log.info("embedder.loaded", model=name)

    # ------------------------------------------------------------------- public

    def encode(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
        max_length: int = 8192,
        return_dense: bool = True,
        return_sparse: bool = True,
    ) -> dict:
        """Encode one or more texts; returns `{"dense": [...], "sparse": [...]}`.

        - dense: list[list[float]], shape (N, 1024)
        - sparse: list[dict[int, float]] — Milvus-ready (int token_id → weight)
        """
        if isinstance(texts, str):
            texts = [texts]

        out = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=return_dense,
            return_sparse=return_sparse,
            return_colbert_vecs=False,
        )

        result: dict = {}
        if return_dense:
            result["dense"] = [v.tolist() for v in out["dense_vecs"]]
        if return_sparse:
            # FlagEmbedding returns dict[str, np.float32]; Milvus needs dict[int, float]
            result["sparse"] = [
                {int(k): float(v) for k, v in d.items()}
                for d in out["lexical_weights"]
            ]
        return result

    def encode_query(self, query: str) -> dict:
        """Single-query convenience: returns `{"dense": [...1024], "sparse": {...}}`.

        BGE-M3 doesn't need a separate query template (unlike e.g. instructor),
        so this is just a thin shape adapter over `encode([query])`.
        """
        out = self.encode([query], batch_size=1)
        return {
            "dense": out["dense"][0],
            "sparse": out["sparse"][0],
        }

    # ----------------------------------------------------------------- singleton

    @classmethod
    def get(cls, **kwargs) -> "BGEM3Embedder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # double-check
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Test-only: drop the singleton so a new model can be loaded."""
        with cls._lock:
            cls._instance = None
