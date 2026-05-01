"""Application settings loaded from environment / .env file.

Single source of truth for runtime configuration. Imported as `settings`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime ---
    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Auth (Layer 9) ---
    api_token_secret: str = "change-me"
    admin_token: str = "admin-dev-token"

    # --- Milvus ---
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "kb_chunks"
    milvus_index_version: int = 1

    # --- MinIO ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "rag-documents"
    minio_use_ssl: bool = False

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- LLM provider switch ---
    # "vertex" → Vertex Gemini ; "deepseek" → DeepSeek (OpenAI-compatible)
    llm_provider: Literal["vertex", "deepseek"] = "vertex"

    # --- Vertex AI ---
    vertex_project: str = ""
    vertex_location: str = "us-central1"
    vertex_generation_model: str = "gemini-2.5-flash"
    vertex_judge_model: str = "gemini-2.5-pro"
    google_application_credentials: str = ""

    # --- DeepSeek (OpenAI-compatible) ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_judge_model: str = "deepseek-reasoner"

    # --- Local models ---
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    model_cache_dir: str = "./.model_cache"

    # --- Retrieval ---
    retrieval_top_k: int = 20
    rerank_top_k: int = 5
    retrieval_min_score: float = 0.0
    # When True, every reranked chunk is expanded with its prev/next siblings
    # (within the same doc). Cheap fix for the "definition split across two
    # chunks" problem — pulling N±window along with N gives the LLM the
    # full context. Default 2 catches cases where the chapter heading +
    # definition body live 2 hops away from the retrieval hit. Set 0 to
    # disable; 1 / 3 / 4 also valid.
    retrieval_neighbor_window: int = 2

    # --- Cache TTL ---
    emb_cache_ttl: int = 86400
    ret_cache_ttl: int = 1800

    # --- Feature flags ---
    feature_multi_query: bool = False

    # --- Cost limits ---
    llm_daily_user_token_limit: int = 200_000
    llm_daily_session_token_limit: int = 50_000

    # --- Chunking ---
    chunk_size: int = 512
    chunk_overlap: int = 50

    # --- PDF parsing ---
    # Force OCR on every PDF page, bypassing PyMuPDF text extraction.
    # Slower (~5-10x) but works around PDFs where text extraction silently
    # drops content (e.g. bullet lists with bold custom-font headers — the
    # extracted text reads coherent but a key line is missing). When False
    # we still OCR pages with low useful-char count automatically.
    pdf_force_ocr: bool = False

    # --- LlamaParse (premium PDF parser) ---
    # When set, PDFs are sent to LlamaParse first (Markdown output preserves
    # structure, headings, tables — way better than PyMuPDF/OCR for any
    # non-trivial layout). Falls back to local PyMuPDF+OCR on error or
    # when this is empty.
    llama_cloud_api_key: str = ""
    # `markdown` keeps headings/tables; `text` if you only want plain text.
    llamaparse_result_type: Literal["markdown", "text"] = "markdown"
    # Tells LlamaParse the doc language for OCR fallback. `ch_sim` for
    # Simplified Chinese; LlamaParse auto-detects but giving a hint helps
    # on documents with mixed CN/EN.
    llamaparse_language: str = "ch_sim"

    # --- Versioning ---
    doc_versions_keep: int = 3

    # --- Session ---
    session_history_turns: int = 5
    session_ttl: int = 86400

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
