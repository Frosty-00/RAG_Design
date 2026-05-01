"""Dev-only retrieval debug endpoint.

Mounted by `app/main.py` only when `settings.is_dev`. Output exposes raw
hybrid search hits + rerank scores, intended for `/debug` UI page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_pipeline, get_requester
from app.repositories.milvus import Requester
from app.services.query_understanding import (
    QueryUnderstandingPipeline,
    UnderstandingResult,
)
from app.services.rag import RAGPipeline
from app.services.retrieval import Retriever, ScoredChunk

router = APIRouter(prefix="/debug", tags=["debug"])


class DebugRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 20
    rerank_k: int = 5
    multi_query: bool = False


class DebugChunk(BaseModel):
    chunk_id: str
    doc_id: str
    score: float
    text_preview: str
    metadata: dict


class DebugResponse(BaseModel):
    understanding: dict
    chunks: list[DebugChunk]


@router.post("/retrieve", response_model=DebugResponse)
async def debug_retrieve(
    body: DebugRequest,
    requester: Requester = Depends(get_requester),
    pipeline: RAGPipeline = Depends(get_pipeline),
) -> DebugResponse:
    qu: QueryUnderstandingPipeline = pipeline.understanding
    retriever: Retriever = pipeline.retriever

    understanding: UnderstandingResult = await qu.run(
        body.query, history=[],
        enable_multi_query=body.multi_query,
    )

    queries = understanding.all_queries() if (
        body.multi_query and understanding.rewrites
    ) else [understanding.resolved_query]

    if len(queries) == 1:
        result = await retriever.retrieve(
            queries[0], requester=requester,
            top_k=body.top_k, rerank_k=body.rerank_k,
        )
    else:
        result = await retriever.retrieve_multi(
            queries, requester=requester,
            top_k=body.top_k, rerank_k=body.rerank_k,
            rerank_query=understanding.resolved_query,
        )

    chunks = [
        DebugChunk(
            chunk_id=c.chunk_id, doc_id=c.doc_id,
            score=float(c.score),
            text_preview=(c.text[:160] + "...") if len(c.text) > 160 else c.text,
            metadata=c.metadata or {},
        )
        for c in result.chunks
    ]
    return DebugResponse(
        understanding=understanding.model_dump(),
        chunks=chunks,
    )
