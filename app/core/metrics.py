"""Prometheus metric definitions — single import surface.

Imported by FastAPI handlers + RAG pipeline + LLM client. Counters are
process-local; in multi-worker deployments use prometheus_multiproc_dir.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# RAG e2e
RAG_QUERY_TOTAL = Counter(
    "rag_query_total",
    "RAG queries dispatched",
    ["intent", "status"],
)
RAG_LATENCY = Histogram(
    "rag_latency_seconds",
    "RAG e2e latency by phase",
    ["phase"],  # understanding | retrieval | rerank | generation | total
)

# Cache (Layer 6)
RAG_CACHE_HIT = Counter(
    "rag_cache_hit_total",
    "Cache hits / misses",
    ["tier", "outcome"],  # tier: emb|ret  outcome: hit|miss
)

# LLM
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "LLM tokens consumed",
    ["model", "direction"],  # direction: input|output
)
LLM_CALL_TOTAL = Counter(
    "llm_call_total",
    "LLM calls",
    ["model", "kind", "status"],  # kind: generate|stream|structured
)

# Celery / ingest
CELERY_TASK_TOTAL = Counter(
    "celery_task_total",
    "Celery task executions",
    ["task", "status"],  # status: success|failed|dlq|skipped
)
