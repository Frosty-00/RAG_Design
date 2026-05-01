"""Retrieval service — wraps Milvus hybrid search + reranker + 2-tier cache.

Public API:
    Retriever.retrieve(query, *, requester, top_k, rerank_k)
    Retriever.retrieve_multi(queries, *, requester, top_k, rerank_k)
    Retriever.invalidate_cache_for_doc(doc_id)   # called by cascade_delete

Cache (Redis):
    emb:{md5(text)}                                          → {dense, sparse}
    ret:{md5(queries|top_k|rerank_k|acl_scope|index_v)}      → [chunk_id...]

ACL scope hash composes (user_id, sorted(groups), is_admin) so two requesters
with different visibility can never share retrieval cache. `index_version`
(settings.milvus_index_version) lets us bump on schema/embedding change.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.repositories.milvus import MilvusRepository, Requester
from app.repositories.redis_repo import RedisRepository
from app.services.embedding import BGEM3Embedder
from app.services.reranker import BGEReranker

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ScoredChunk:
    chunk_id: str
    doc_id: str
    doc_version: int
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalStats:
    queries: int = 0
    embedding_cache_hits: int = 0
    embedding_cache_misses: int = 0
    retrieval_cache_hits: int = 0
    retrieval_cache_misses: int = 0
    reranked: bool = False


@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    stats: RetrievalStats


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _acl_scope(requester: Requester | None) -> str:
    if requester is None:
        return "anon"
    if requester.is_admin:
        return "admin"
    return _md5(f"{requester.user_id}|{','.join(sorted(requester.groups))}")[:16]


# ─────────────────────────────────────────────────────────────────────
# Retriever
# ─────────────────────────────────────────────────────────────────────


class Retriever:
    """Stateless orchestrator. Holds references to singleton models + repos."""

    def __init__(
        self,
        *,
        milvus: MilvusRepository | None = None,
        embedder: BGEM3Embedder | None = None,
        reranker: BGEReranker | None = None,
        redis: RedisRepository | None = None,
    ) -> None:
        self.milvus = milvus or MilvusRepository()
        self.embedder = embedder or BGEM3Embedder.get()
        self.reranker = reranker or BGEReranker.get()
        # Redis is per-instance: callers wanting to share connection pass one in.
        self.redis = redis or RedisRepository()
        self._owns_redis = redis is None

    async def aclose(self) -> None:
        if self._owns_redis:
            await self.redis.close()

    # ----------------------------------------------------------- single query

    async def retrieve(
        self,
        query: str,
        *,
        requester: Requester | None,
        top_k: int | None = None,
        rerank_k: int | None = None,
        use_cache: bool = True,
        rerank: bool = True,
    ) -> RetrievalResult:
        top_k = top_k if top_k is not None else settings.retrieval_top_k
        rerank_k = rerank_k if rerank_k is not None else settings.rerank_top_k
        stats = RetrievalStats(queries=1)

        # 1. embed query (with cache)
        emb = await self._embed_with_cache([query], stats=stats, use_cache=use_cache)
        dense, sparse = emb[0]["dense"], emb[0]["sparse"]

        # 2. retrieval cache lookup
        ret_key = self._retrieval_cache_key(
            queries=[query], top_k=top_k, rerank_k=rerank_k, requester=requester,
        )
        cached_ids: list[str] | None = None
        if use_cache:
            cached_ids = await self.redis._get_json(ret_key)

        if cached_ids:
            stats.retrieval_cache_hits += 1
            chunks = self._chunks_by_ids(cached_ids)
            # rerank against cached candidates if requested (still useful in case
            # the user asked for a different rerank_k than the cached one)
            if rerank and chunks:
                chunks = self._rerank(query, chunks, rerank_k)
                stats.reranked = True
            chunks = self._expand_neighbors(chunks)
            return RetrievalResult(chunks=chunks, stats=stats)

        stats.retrieval_cache_misses += 1

        # 3. Milvus hybrid search
        raw = await asyncio.to_thread(
            self.milvus.hybrid_search,
            dense=dense, sparse=sparse, top_k=top_k, requester=requester,
        )
        chunks = self._hits_to_chunks(raw)

        # 4. rerank (top_k → rerank_k)
        if rerank and chunks:
            chunks = self._rerank(query, chunks, rerank_k)
            stats.reranked = True

        # 5. cache write (chunk_ids only) — store the *post-rerank, pre-expand*
        # set. Cache hits then re-expand fresh, so a tunable that grows the
        # window doesn't require flushing the cache.
        if use_cache and chunks:
            await self.redis._set_json(
                ret_key, [c.chunk_id for c in chunks], ttl=settings.ret_cache_ttl,
            )

        # 6. neighbor expansion (cheap fix for "definition split across
        # adjacent chunks" — see _expand_neighbors docstring)
        chunks = self._expand_neighbors(chunks)

        return RetrievalResult(chunks=chunks, stats=stats)

    # ----------------------------------------------------------- multi query

    async def retrieve_multi(
        self,
        queries: list[str],
        *,
        requester: Requester | None,
        top_k: int | None = None,
        rerank_k: int | None = None,
        rerank_query: str | None = None,
        use_cache: bool = True,
    ) -> RetrievalResult:
        """Run several queries in parallel; merge by chunk_id (max score) and
        rerank against `rerank_query` (defaults to queries[0]).
        Used by Layer 8 for Multi-Query feature flag.
        """
        if not queries:
            return RetrievalResult(chunks=[], stats=RetrievalStats())
        if len(queries) == 1:
            return await self.retrieve(
                queries[0], requester=requester, top_k=top_k,
                rerank_k=rerank_k, use_cache=use_cache,
            )

        top_k = top_k if top_k is not None else settings.retrieval_top_k
        rerank_k = rerank_k if rerank_k is not None else settings.rerank_top_k
        rerank_query = rerank_query or queries[0]
        stats = RetrievalStats(queries=len(queries))

        # 1. retrieval-cache check on the combined queries set
        ret_key = self._retrieval_cache_key(
            queries=queries, top_k=top_k, rerank_k=rerank_k, requester=requester,
        )
        if use_cache:
            cached_ids = await self.redis._get_json(ret_key)
            if cached_ids:
                stats.retrieval_cache_hits += 1
                chunks = self._chunks_by_ids(cached_ids)
                chunks = self._rerank(rerank_query, chunks, rerank_k)
                stats.reranked = True
                chunks = self._expand_neighbors(chunks)
                return RetrievalResult(chunks=chunks, stats=stats)
            stats.retrieval_cache_misses += 1

        # 2. embed all queries (with cache)
        embs = await self._embed_with_cache(queries, stats=stats, use_cache=use_cache)

        # 3. parallel hybrid_search
        async def one(emb):
            return await asyncio.to_thread(
                self.milvus.hybrid_search,
                dense=emb["dense"], sparse=emb["sparse"], top_k=top_k,
                requester=requester,
            )

        raw_results = await asyncio.gather(*(one(e) for e in embs))

        # 4. dedupe by chunk_id, keeping max score
        merged: dict[str, ScoredChunk] = {}
        for raw in raw_results:
            for c in self._hits_to_chunks(raw):
                cur = merged.get(c.chunk_id)
                if cur is None or c.score > cur.score:
                    merged[c.chunk_id] = c
        chunks = list(merged.values())

        # 5. rerank against the primary query
        if chunks:
            chunks = self._rerank(rerank_query, chunks, rerank_k)
            stats.reranked = True

        # 6. cache (pre-expansion)
        if use_cache and chunks:
            await self.redis._set_json(
                ret_key, [c.chunk_id for c in chunks], ttl=settings.ret_cache_ttl,
            )

        # 7. neighbor expansion
        chunks = self._expand_neighbors(chunks)

        return RetrievalResult(chunks=chunks, stats=stats)

    # ----------------------------------------------------------- cache invalidation

    async def invalidate_cache_for_doc(self, doc_id: str) -> int:
        """Best-effort retrieval-cache scrub.

        Retrieval keys are content-addressed (md5 of queries + scope), so we
        can't target one doc. We simply purge ALL `ret:*` keys when a doc is
        deleted — small steady-state cost, conservative correctness. Embedding
        cache is content-keyed by text, untouched.
        """
        deleted = 0
        async for key in self.redis.client.scan_iter(match="ret:*"):
            deleted += await self.redis.client.delete(key)
        if deleted:
            log.info("retrieval.cache.invalidated", doc_id=doc_id, keys=deleted)
        return deleted

    # ----------------------------------------------------------- internals

    async def _embed_with_cache(
        self,
        texts: list[str],
        *,
        stats: RetrievalStats,
        use_cache: bool,
    ) -> list[dict]:
        """Returns list of {"dense": [...], "sparse": {...}} aligned to texts."""
        out: list[dict | None] = [None] * len(texts)
        to_compute: list[tuple[int, str]] = []

        if use_cache:
            for i, t in enumerate(texts):
                key = f"emb:{_md5(t)}"
                cached = await self.redis._get_json(key)
                if cached is not None:
                    # JSON serialization turns sparse dict keys to strings; restore
                    cached["sparse"] = {int(k): float(v) for k, v in cached["sparse"].items()}
                    out[i] = cached
                    stats.embedding_cache_hits += 1
                else:
                    stats.embedding_cache_misses += 1
                    to_compute.append((i, t))
        else:
            to_compute = list(enumerate(texts))
            stats.embedding_cache_misses += len(texts)

        if to_compute:
            indices, texts_to_run = zip(*to_compute, strict=True)
            computed = await asyncio.to_thread(self.embedder.encode, list(texts_to_run))
            for k, idx in enumerate(indices):
                emb = {"dense": computed["dense"][k], "sparse": computed["sparse"][k]}
                out[idx] = emb
                if use_cache:
                    await self.redis._set_json(
                        f"emb:{_md5(texts[idx])}", emb,
                        ttl=settings.emb_cache_ttl,
                    )

        return out  # type: ignore[return-value]

    def _retrieval_cache_key(
        self,
        *,
        queries: list[str],
        top_k: int,
        rerank_k: int,
        requester: Requester | None,
    ) -> str:
        scope = _acl_scope(requester)
        payload = json.dumps({
            "q": sorted(queries),
            "top_k": top_k,
            "rerank_k": rerank_k,
            "scope": scope,
            "index_v": settings.milvus_index_version,
        }, sort_keys=True)
        return f"ret:{_md5(payload)}"

    def _hits_to_chunks(self, raw: list[list[dict]]) -> list[ScoredChunk]:
        chunks: list[ScoredChunk] = []
        seen: set[str] = set()
        for hit in raw[0] if raw else []:
            entity = hit.get("entity") or {}
            chunk_id = entity.get("chunk_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunks.append(ScoredChunk(
                chunk_id=chunk_id,
                doc_id=entity.get("doc_id", ""),
                doc_version=int(entity.get("doc_version") or 0),
                text=entity.get("text", ""),
                score=float(hit.get("distance", hit.get("score", 0.0))),
                metadata=entity.get("metadata", {}) or {},
            ))
        return chunks

    def _chunks_by_ids(self, ids: list[str]) -> list[ScoredChunk]:
        rows = self.milvus.get_by_chunk_ids(ids)
        # preserve original ordering as given by `ids`
        by_id = {r["chunk_id"]: r for r in rows}
        out: list[ScoredChunk] = []
        for cid in ids:
            r = by_id.get(cid)
            if not r:
                continue  # row deleted between cache write and lookup
            out.append(ScoredChunk(
                chunk_id=r["chunk_id"],
                doc_id=r.get("doc_id", ""),
                doc_version=int(r.get("doc_version") or 0),
                text=r.get("text", ""),
                score=0.0,  # cache-only path doesn't carry scores
                metadata=r.get("metadata", {}) or {},
            ))
        return out

    def _expand_neighbors(
        self,
        chunks: list[ScoredChunk],
        *,
        window: int | None = None,
    ) -> list[ScoredChunk]:
        """Expand each retrieved chunk with its `window` neighbors on each
        side. Cheap fix for term definitions / table rows that got split
        across consecutive chunks.

        Implementation note: chunk_id encodes the chunk_index in its
        suffix (`{doc_id}:v{N}:c{NNNN}`), so we can compute neighbor IDs
        arithmetically without walking `prev_chunk_id`/`next_chunk_id`
        pointers (which only support 1-hop). This lets `window=2+` work
        in a single Milvus round-trip.

        Returns chunks ordered: original retrieved chunk first per
        cluster, then sorted by chunk_index within the cluster (so the
        downstream context-formatter can detect the consecutive run and
        merge them into a single `[N]` block for the LLM).
        """
        if not chunks:
            return chunks
        w = window if window is not None else settings.retrieval_neighbor_window
        if w <= 0:
            return chunks

        # 1. Collect every neighbor chunk_id we want (deduped against
        #    already-retrieved chunks)
        retrieved_ids = {c.chunk_id for c in chunks}
        wanted: list[str] = []
        wanted_set: set[str] = set()
        parent_of: dict[str, ScoredChunk] = {}
        for c in chunks:
            for hop in range(1, w + 1):
                for nid in (self._neighbor_id(c.chunk_id, -hop),
                            self._neighbor_id(c.chunk_id, +hop)):
                    if nid and nid not in retrieved_ids and nid not in wanted_set:
                        wanted.append(nid)
                        wanted_set.add(nid)
                        parent_of[nid] = c

        if not wanted:
            log.info("retrieval.expand.no_neighbors_wanted",
                     parent_chunks=[c.chunk_id for c in chunks],
                     reason="every parent's prev/next was None or already in the retrieved set")
            return chunks

        # 2. Single Milvus lookup for all neighbors
        rows = self.milvus.get_by_chunk_ids(wanted)
        by_id = {r["chunk_id"]: r for r in rows}
        log.info("retrieval.expand.neighbors_fetched",
                 parents=len(chunks), wanted=len(wanted), got=len(rows),
                 missing=[w for w in wanted if w not in by_id])

        # 3. Build neighbor ScoredChunks; group by parent
        neighbors_by_parent: dict[str, list[ScoredChunk]] = {}
        for nid in wanted:
            row = by_id.get(nid)
            if not row:
                continue  # neighbor was deleted / different doc version
            parent = parent_of[nid]
            neighbor = ScoredChunk(
                chunk_id=row["chunk_id"],
                doc_id=row.get("doc_id", ""),
                doc_version=int(row.get("doc_version") or 0),
                text=row.get("text", ""),
                # Slightly below parent so any score-based sort keeps the
                # parent on top but the neighbor right next to it.
                score=max(parent.score - 0.001, 0.0),
                metadata=row.get("metadata", {}) or {},
            )
            neighbors_by_parent.setdefault(parent.chunk_id, []).append(neighbor)

        # 4. Stitch: for each retrieved chunk, emit its whole cluster
        #    (prev + parent + next) sorted by chunk_index so adjacent
        #    chunks come out in document order [N-1, N, N+1]. Without
        #    this sort, downstream context-merging code can't tell that
        #    [parent, prev] are actually consecutive in the source doc
        #    — it sees [50, 49] which fails the `idx == last+1` test
        #    and renders them as two separate `[N]` blocks to the LLM.
        out: list[ScoredChunk] = []
        emitted: set[str] = set()
        for c in chunks:
            cluster = list(neighbors_by_parent.get(c.chunk_id, []))
            cluster.append(c)
            cluster.sort(key=lambda x: int((x.metadata or {}).get("chunk_index", 0)))
            for cc in cluster:
                if cc.chunk_id not in emitted:
                    out.append(cc)
                    emitted.add(cc.chunk_id)
        return out

    @staticmethod
    def _neighbor_id(chunk_id: str, offset: int) -> str | None:
        """Compute a neighbor chunk_id by index arithmetic.

        chunk_id format is `{doc_id}:v{N}:c{NNNN}` (LayerEnricher in
        chunking.py). Splitting the suffix gives us the chunk_index;
        adding the offset yields the neighbor's id. Returns None if the
        format doesn't match (defensive — shouldn't happen) or the
        result would be negative (no chunk before c0000).
        """
        sep = chunk_id.rfind(":c")
        if sep < 0:
            return None
        prefix = chunk_id[: sep + 2]   # "...:c"
        try:
            idx = int(chunk_id[sep + 2:])
        except ValueError:
            return None
        nidx = idx + offset
        if nidx < 0:
            return None
        # Preserve original zero-padding width so the result actually
        # exists in Milvus (chunk_ids written with %04d).
        width = len(chunk_id) - (sep + 2)
        return f"{prefix}{nidx:0{width}d}"

    def _rerank(self, query: str, chunks: list[ScoredChunk], k: int) -> list[ScoredChunk]:
        if not chunks:
            return chunks
        results = self.reranker.rerank(
            query, chunks, text_of=lambda c: c.text, k=k,
        )
        return [
            ScoredChunk(
                chunk_id=r.item.chunk_id,
                doc_id=r.item.doc_id,
                doc_version=r.item.doc_version,
                text=r.item.text,
                score=r.score,
                metadata=r.item.metadata,
            )
            for r in results
        ]
