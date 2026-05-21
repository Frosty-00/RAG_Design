"""Milvus repository (pymilvus-native, Layer 2 scope).

Owns:
  - Collection schema with ACL + version fields (see plan §4)
  - Idempotent collection / index creation
  - Insert / upsert / hybrid_search / delete_by_doc / count
  - Version handling: same `doc_id` re-uploaded → previous chunks marked
    is_latest=false, new chunks inserted with bumped doc_version
  - ACL filter expression builder (public OR owner OR users OR groups)

Intentional design choices (see docs/layer-2.md):
  - Primary key = `chunk_id` (VARCHAR, app-generated) so upsert can flip
    is_latest without rewriting from auto-id. This deviates slightly from
    the plan's §4 schema (which used auto_id INT64 + chunk_id) — chunk_id
    becomes the single primary identifier.
  - LlamaIndex `MilvusVectorStore` adapter is NOT in Layer 2; it's added
    in Layer 4 when IngestionPipeline is wired in. Layer 2 stays pure
    pymilvus to keep schema control single-sourced.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    RRFRanker,
)

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

DENSE_DIM = 1024  # BGE-M3 dense
DENSE_INDEX_PARAMS = {
    "index_type": "HNSW",
    "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200},
}
SPARSE_INDEX_PARAMS = {
    "index_type": "SPARSE_INVERTED_INDEX",
    "metric_type": "IP",
    "params": {},
}


@dataclass
class Chunk:
    """In-memory representation of a chunk row before/after Milvus."""

    chunk_id: str
    doc_id: str
    doc_version: int
    is_latest: bool
    text: str
    owner_id: str
    acl: dict[str, Any]
    dense: list[float]
    sparse: dict[int, float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Requester:
    user_id: str
    groups: list[str] = field(default_factory=list)
    is_admin: bool = False
    # Departments this user *manages* (READ-only privilege). A manager of
    # `hr` sees every doc any HR member can see, even if the doc was
    # individually granted to a specific HR person via ACL.users. This
    # mirrors org reality: "an employee has access to a doc → their
    # manager logically also has access". Manager scope NEVER grants
    # write/upload/delete privileges; those still flow through ownership
    # or actual group membership.
    managed_groups: list[str] = field(default_factory=list)
    # Cached "every user_id that belongs to any of `managed_groups`" —
    # populated by the request-context builder so retrieval ACL filters
    # can include `owner_id in [...]` without re-scanning Redis per query.
    # Empty when the caller manages no groups.
    managed_user_ids: list[str] = field(default_factory=list)


def _build_schema() -> CollectionSchema:
    fields = [
        FieldSchema("chunk_id", DataType.VARCHAR, max_length=128, is_primary=True),
        FieldSchema("doc_id", DataType.VARCHAR, max_length=64),
        FieldSchema("doc_version", DataType.INT64),
        FieldSchema("is_latest", DataType.BOOL),
        FieldSchema("text", DataType.VARCHAR, max_length=8192),
        FieldSchema("owner_id", DataType.VARCHAR, max_length=64),
        FieldSchema("acl", DataType.JSON),
        FieldSchema("dense", DataType.FLOAT_VECTOR, dim=DENSE_DIM),
        FieldSchema("sparse", DataType.SPARSE_FLOAT_VECTOR),
        FieldSchema("metadata", DataType.JSON),
    ]
    return CollectionSchema(
        fields=fields,
        description="self-rag knowledge base chunks (dense + sparse + ACL)",
        enable_dynamic_field=False,
    )


def _quote(s: str) -> str:
    """Quote string for Milvus expr — escape backslash and double-quote."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_acl_expr(requester: Requester | None) -> str:
    """ACL filter for retrieval. Always intersected with `is_latest == true`.

    requester=None means "no auth context" — only public docs visible.
    Admin sees everything (returns just is_latest filter).

    Manager scope: if requester.managed_groups is non-empty, the filter
    also matches docs whose acl.groups overlaps managed_groups OR whose
    owner_id is in managed_user_ids (anyone in those groups). Members of
    a managed department's specifically-granted private docs are also
    visible via the `acl.users` overlap with managed_user_ids.
    """
    if requester is not None and requester.is_admin:
        return "is_latest == true"

    public_clause = 'acl["public"] == true'

    if requester is None:
        return f"({public_clause}) and is_latest == true"

    uid = _quote(requester.user_id)
    clauses = [
        public_clause,
        f"owner_id == {uid}",
        f"json_contains(acl[\"users\"], {uid})",
    ]
    if requester.groups:
        groups_json = json.dumps(requester.groups)
        clauses.append(f"json_contains_any(acl[\"groups\"], {groups_json})")

    # Manager extension — three additional ways a managed-group leader
    # can reach a doc, each mirroring how a regular member would.
    if requester.managed_groups:
        mg_json = json.dumps(requester.managed_groups)
        clauses.append(f"json_contains_any(acl[\"groups\"], {mg_json})")
        if requester.managed_user_ids:
            # owner is one of my reports
            owners_json = json.dumps(requester.managed_user_ids)
            clauses.append(f"owner_id in {owners_json}")
            # doc privately shared with one of my reports
            clauses.append(
                f"json_contains_any(acl[\"users\"], {owners_json})"
            )

    or_clause = " or ".join(clauses)
    return f"({or_clause}) and is_latest == true"


class MilvusRepository:
    """Thin pymilvus wrapper. Single source of truth for collection schema."""

    def __init__(self, collection: str | None = None) -> None:
        uri = f"http://{settings.milvus_host}:{settings.milvus_port}"
        self._client = MilvusClient(uri=uri)
        self._collection = collection or settings.milvus_collection

    @property
    def client(self) -> MilvusClient:
        return self._client

    @property
    def collection(self) -> str:
        return self._collection

    # ---------------------------------------------------------------- schema

    def ensure_collection(self) -> None:
        """Idempotent: create collection + indexes + load if missing."""
        if self._client.has_collection(self._collection):
            log.debug("milvus.collection.exists", name=self._collection)
            self._client.load_collection(self._collection)
            return

        schema = _build_schema()
        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense",
            **DENSE_INDEX_PARAMS,
        )
        index_params.add_index(
            field_name="sparse",
            **SPARSE_INDEX_PARAMS,
        )
        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
        )
        self._client.load_collection(self._collection)
        log.info("milvus.collection.created", name=self._collection)

    def drop_collection(self) -> None:
        """Used by tests / admin scripts."""
        if self._client.has_collection(self._collection):
            self._client.drop_collection(self._collection)
            log.info("milvus.collection.dropped", name=self._collection)

    # ---------------------------------------------------------------- write

    def _to_row(self, c: Chunk) -> dict[str, Any]:
        return {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "doc_version": c.doc_version,
            "is_latest": c.is_latest,
            "text": c.text,
            "owner_id": c.owner_id,
            "acl": c.acl,
            "dense": c.dense,
            "sparse": c.sparse,
            "metadata": c.metadata,
        }

    def insert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        rows = [self._to_row(c) for c in chunks]
        result = self._client.insert(self._collection, rows)
        # MilvusClient.insert returns dict with insert_count
        return int(result.get("insert_count", len(chunks)))

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        rows = [self._to_row(c) for c in chunks]
        result = self._client.upsert(self._collection, rows)
        return int(result.get("upsert_count", len(chunks)))

    def update_acl_by_doc(self, doc_id: str, new_acl: dict) -> int:
        """Bulk-update the `acl` JSON field on every chunk of a doc.

        Used when a second uploader (different user / department) re-uploads
        the same content — we don't re-ingest, we extend the existing doc's
        ACL to union in the new caller's groups/users. All chunks for the
        doc share one ACL by construction; this method keeps them in sync.

        Returns number of rows updated.
        """
        rows = self._client.query(
            self._collection,
            filter=f'doc_id == {_quote(doc_id)}',
            output_fields=[
                "chunk_id", "doc_id", "doc_version", "text",
                "owner_id", "is_latest", "acl", "dense", "sparse", "metadata",
            ],
        )
        if not rows:
            return 0
        for row in rows:
            row["acl"] = new_acl
        self._client.upsert(self._collection, rows)
        log.info("milvus.acl.updated", doc_id=doc_id, count=len(rows))
        return len(rows)

    def mark_old_versions_inactive(self, doc_id: str, keep_version: int) -> int:
        """Flip is_latest=false for all chunks of `doc_id` whose version != keep_version.

        Used during a re-ingest: the new version is inserted with is_latest=true,
        then this method demotes prior versions in a single upsert batch.
        Returns number of rows updated.
        """
        expr = (
            f'doc_id == {_quote(doc_id)} '
            f'and doc_version != {keep_version} '
            f'and is_latest == true'
        )
        old = self._client.query(
            self._collection,
            filter=expr,
            output_fields=[
                "chunk_id", "doc_id", "doc_version", "text",
                "owner_id", "acl", "dense", "sparse", "metadata",
            ],
        )
        if not old:
            return 0

        for row in old:
            row["is_latest"] = False
        self._client.upsert(self._collection, old)
        log.info("milvus.versions.demoted", doc_id=doc_id, count=len(old), keep=keep_version)
        return len(old)

    def delete_by_doc(self, doc_id: str) -> int:
        """Cascade delete: remove all chunks (all versions) for a doc."""
        expr = f"doc_id == {_quote(doc_id)}"
        existing = self._client.query(
            self._collection, filter=expr, output_fields=["chunk_id"]
        )
        if not existing:
            return 0
        self._client.delete(self._collection, filter=expr)
        log.info("milvus.delete_by_doc", doc_id=doc_id, count=len(existing))
        return len(existing)

    # ---------------------------------------------------------------- read

    def count(self, doc_id: str | None = None, only_latest: bool = False) -> int:
        parts = []
        if doc_id is not None:
            parts.append(f"doc_id == {_quote(doc_id)}")
        if only_latest:
            parts.append("is_latest == true")
        expr = " and ".join(parts) if parts else ""
        result = self._client.query(
            self._collection,
            filter=expr,
            output_fields=["count(*)"],
        )
        if not result:
            return 0
        return int(result[0].get("count(*)", 0))

    def list_chunks_by_doc(
        self,
        doc_id: str,
        *,
        only_latest: bool = True,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return all chunks for a doc — diagnostic view used by the
        documents UI to answer "what did the parser actually capture?".
        Ordered by chunk_id (which encodes chunk_index)."""
        parts = [f"doc_id == {_quote(doc_id)}"]
        if only_latest:
            parts.append("is_latest == true")
        expr = " and ".join(parts)
        rows = self._client.query(
            self._collection,
            filter=expr,
            output_fields=["chunk_id", "doc_id", "doc_version",
                           "text", "metadata"],
            limit=limit,
        )
        rows.sort(key=lambda r: r.get("chunk_id", ""))
        return rows

    def get_by_chunk_ids(
        self,
        chunk_ids: list[str],
        *,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Look up chunks by primary key. Used when retrieval cache hits."""
        if not chunk_ids:
            return []
        # Build expr: chunk_id in ["a", "b", ...]
        quoted = ", ".join(_quote(cid) for cid in chunk_ids)
        expr = f"chunk_id in [{quoted}]"
        fields = output_fields or [
            "chunk_id", "doc_id", "doc_version", "text",
            "owner_id", "is_latest", "metadata",
        ]
        return self._client.query(
            self._collection, filter=expr, output_fields=fields,
        )

    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        top_k: int,
        requester: Requester | None,
        rrf_k: int = 60,
        extra_filter: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[list[dict[str, Any]]]:
        """Hybrid (dense + sparse) search with RRF fusion + ACL filter.

        Returns list-of-lists shaped per pymilvus hybrid_search:
        outer = batch (we pass single query → length 1), inner = hits.
        """
        acl_expr = _build_acl_expr(requester)
        expr = f"({acl_expr}) and ({extra_filter})" if extra_filter else acl_expr

        dense_req = AnnSearchRequest(
            data=[dense],
            anns_field="dense",
            param={"metric_type": "COSINE", "params": {"ef": 200}},
            limit=top_k,
            expr=expr,
        )
        sparse_req = AnnSearchRequest(
            data=[sparse],
            anns_field="sparse",
            param={"metric_type": "IP", "params": {}},
            limit=top_k,
            expr=expr,
        )
        fields = output_fields or [
            "chunk_id", "doc_id", "doc_version", "text", "owner_id", "metadata"
        ]
        results = self._client.hybrid_search(
            collection_name=self._collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(rrf_k),
            limit=top_k,
            output_fields=fields,
        )
        return results
