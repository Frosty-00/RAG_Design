"""Documents API — upload / list / detail / delete."""
from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_requester
from app.core.logger import get_logger
from app.repositories.milvus import Requester
from app.repositories.minio_repo import MinioRepository, doc_key
from app.repositories.redis_repo import RedisRepository
from app.workers.tasks.cascade_delete import cascade_delete
from app.workers.tasks.ingest import ingest_document

log = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


class UploadResponse(BaseModel):
    doc_id: str
    version: int
    task_id: str
    status: str
    filename: str


class DocumentMeta(BaseModel):
    doc_id: str
    filename: str
    owner_id: str
    latest_version: int
    latest_status: str
    n_chunks: int | None = None
    updated_at: str | None = None
    acl: dict[str, Any] | None = None
    error: str | None = None  # set when latest_status == "failed"


class TaskRef(BaseModel):
    task_id: str
    doc_id: str


def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _build_acl(public: bool, users: list[str], groups: list[str]) -> dict:
    return {"public": bool(public), "users": users, "groups": groups}


@router.post("", response_model=UploadResponse)
async def upload(
    file: Annotated[UploadFile, File(...)],
    public: Annotated[bool, Form()] = False,
    users: Annotated[str, Form()] = "",
    groups: Annotated[str, Form()] = "",
    requester: Requester = Depends(get_requester),
) -> UploadResponse:
    """Multipart upload → MinIO + dispatch ingest task. Same content (sha256)
    re-uploaded returns the existing doc_id without re-ingesting."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    if file.filename is None:
        raise HTTPException(status_code=400, detail="missing_filename")

    doc_id = hashlib.sha256(content).hexdigest()[:16]
    acl = _build_acl(public, _parse_csv(users), _parse_csv(groups))

    redis = RedisRepository()
    try:
        existing = await redis.get_doc_meta(doc_id)
        if existing and existing.get("latest_status") == "done":
            return UploadResponse(
                doc_id=doc_id,
                version=existing["latest_version"],
                task_id=existing.get("latest_task_id", ""),
                status="already_exists",
                filename=existing["filename"],
            )

        # Filename-level dedupe (per owner). Two distinct files happening to
        # share a filename almost always means "I'm uploading the wrong file"
        # — better to surface that as a 409 than silently coexist as two
        # different doc_ids both labelled `report.pdf` in the table.
        # Skipped when this exact content already has a meta row (the doc_id
        # match path above), so re-uploading the *same* file under the same
        # name still works for the failed/pending recovery flow.
        if existing is None:
            owned = await redis.list_owned_docs(requester.user_id)
            for d in owned:
                if d.get("filename") == file.filename:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "filename_exists",
                            "message": (
                                f"A document named {file.filename!r} already "
                                f"exists in your library. Delete it first or "
                                f"rename this file before uploading."
                            ),
                            "existing_doc_id": d.get("doc_id"),
                            "existing_status": d.get("latest_status"),
                        },
                    )

        new_version = (existing or {}).get("latest_version", 0) + 1
    finally:
        await redis.close()

    # Per-upload salt: makes the MinIO key globally unique even when
    # version resets after a delete. Without this, delete-then-re-upload
    # of the same content lands on the same key the deferred cascade
    # cleanup is about to wipe — the user-visible NoSuchKey bug.
    upload_salt = uuid.uuid4().hex[:8]
    key = doc_key(doc_id, new_version, file.filename, salt=upload_salt)
    MinioRepository().put_object(key, content, content_type=file.content_type or "application/octet-stream")

    task_id = "ing-" + uuid.uuid4().hex[:10]
    ingest_document.apply_async(kwargs={
        "task_id": task_id,
        "doc_id": doc_id,
        "version": new_version,
        "file_key": key,
        "filename": file.filename,
        "owner_id": requester.user_id,
        "acl": acl,
    })
    log.info("api.upload.queued", doc_id=doc_id, version=new_version,
             owner=requester.user_id, filename=file.filename, size=len(content))
    return UploadResponse(
        doc_id=doc_id, version=new_version, task_id=task_id,
        status="queued", filename=file.filename,
    )


@router.get("", response_model=list[DocumentMeta])
async def list_documents(
    requester: Requester = Depends(get_requester),
) -> list[DocumentMeta]:
    redis = RedisRepository()
    try:
        if requester.is_admin:
            docs = await redis.list_all_docs()
        else:
            docs = await redis.list_owned_docs(requester.user_id)
    finally:
        await redis.close()
    docs.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return [DocumentMeta(**d) for d in docs]


@router.get("/{doc_id}", response_model=DocumentMeta)
async def get_document(
    doc_id: str,
    requester: Requester = Depends(get_requester),
) -> DocumentMeta:
    redis = RedisRepository()
    try:
        meta = await redis.get_doc_meta(doc_id)
    finally:
        await redis.close()
    if not meta:
        raise HTTPException(status_code=404, detail="not_found")
    if not requester.is_admin and meta["owner_id"] != requester.user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return DocumentMeta(**meta)


class ChunkPreview(BaseModel):
    chunk_id: str
    text: str
    page: int | None = None
    breadcrumbs: list[str] = []
    chunk_index: int | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None


class ChunksResponse(BaseModel):
    doc_id: str
    filename: str
    n_chunks: int
    chunks: list[ChunkPreview]


@router.get("/{doc_id}/chunks", response_model=ChunksResponse)
async def list_doc_chunks(
    doc_id: str,
    requester: Requester = Depends(get_requester),
) -> ChunksResponse:
    """Diagnostic: return what the parser actually captured for this doc.
    Useful when retrieval misses content the user *knows* is in the file —
    if the chunks here don't contain the text you expected, the issue is at
    parsing, not retrieval.
    """
    from app.repositories.milvus import MilvusRepository

    redis = RedisRepository()
    try:
        meta = await redis.get_doc_meta(doc_id)
    finally:
        await redis.close()
    if not meta:
        raise HTTPException(status_code=404, detail="not_found")
    if not requester.is_admin and meta["owner_id"] != requester.user_id:
        raise HTTPException(status_code=403, detail="forbidden")

    rows = MilvusRepository().list_chunks_by_doc(doc_id)
    chunks: list[ChunkPreview] = []
    for r in rows:
        md = r.get("metadata") or {}
        chunks.append(ChunkPreview(
            chunk_id=r.get("chunk_id", ""),
            text=r.get("text", ""),
            page=md.get("page"),
            breadcrumbs=list(md.get("breadcrumbs") or []),
            chunk_index=md.get("chunk_index"),
            prev_chunk_id=md.get("prev_chunk_id"),
            next_chunk_id=md.get("next_chunk_id"),
        ))
    return ChunksResponse(
        doc_id=doc_id,
        filename=meta["filename"],
        n_chunks=len(chunks),
        chunks=chunks,
    )


@router.get("/{doc_id}/task", response_model=dict)
async def get_doc_task_status(
    doc_id: str,
    requester: Requester = Depends(get_requester),
) -> dict:
    """Return latest task status for a doc (ingest or delete)."""
    redis = RedisRepository()
    try:
        meta = await redis.get_doc_meta(doc_id)
        if not meta:
            raise HTTPException(status_code=404, detail="not_found")
        if not requester.is_admin and meta["owner_id"] != requester.user_id:
            raise HTTPException(status_code=403, detail="forbidden")
        tid = meta.get("latest_task_id")
        if not tid:
            return {"status": "unknown"}
        status = await redis.get_task(tid)
        return status or {"status": "unknown", "task_id": tid}
    finally:
        await redis.close()


@router.delete("/{doc_id}", response_model=TaskRef)
async def delete_document(
    doc_id: str,
    requester: Requester = Depends(get_requester),
) -> TaskRef:
    """Cascade delete with no race window vs retrieval.

    The query-blocking parts (Milvus chunks, retrieval cache, doc-meta
    index) run **synchronously** inside this handler — by the time we
    return 200 the next chat query is guaranteed to miss this doc. MinIO
    object cleanup is deferred to a Celery task; it doesn't affect search.
    """
    from app.repositories.milvus import MilvusRepository

    redis = RedisRepository()
    try:
        meta = await redis.get_doc_meta(doc_id)
        if not meta:
            raise HTTPException(status_code=404, detail="not_found")
        if not requester.is_admin and meta["owner_id"] != requester.user_id:
            raise HTTPException(status_code=403, detail="forbidden")

        # ── 1. Milvus: drop all chunks (all versions) of this doc ──────
        milvus = MilvusRepository()
        n_chunks = milvus.delete_by_doc(doc_id)
        milvus.client.flush(milvus.collection)

        # ── 2. Retrieval cache: blow away ALL `ret:*` keys ─────────────
        # ret cache keys are content-addressed (md5(queries+top_k+scope+
        # index_version)) so we can't target one doc; safest is to purge
        # all retrieval entries. Embedding cache (`emb:*`) is keyed on
        # text content, not doc, so it stays.
        cache_cleared = 0
        async for key in redis.client.scan_iter(match="ret:*"):
            cache_cleared += await redis.client.delete(key)

        # ── 3. Redis doc-meta + owner index ───────────────────────────
        await redis.delete_doc_meta(doc_id)
    finally:
        await redis.close()

    # ── 4. Snapshot MinIO keys NOW (before queueing the deferred task)
    # so a re-upload landing on the same doc_id between this handler
    # returning and `cascade_delete` running can't be scrubbed by a
    # blanket `delete_prefix("docs/{doc_id}/")`. We pin the exact keys
    # we intend to delete; anything written later survives.
    from app.repositories.minio_repo import MinioRepository, doc_prefix
    snapshot_keys = list(MinioRepository().list_prefix(doc_prefix(doc_id)))

    # ── 5. MinIO + DLQ scrub goes async — file cleanup doesn't gate search
    task_id = "del-" + uuid.uuid4().hex[:10]
    cascade_delete.apply_async(kwargs={
        "doc_id": doc_id,
        "task_id": task_id,
        "minio_keys": snapshot_keys,
    })

    log.info("api.delete.synced", doc_id=doc_id, task_id=task_id,
             owner=requester.user_id, milvus_chunks=n_chunks,
             ret_keys_cleared=cache_cleared,
             minio_keys_pinned=len(snapshot_keys))
    return TaskRef(task_id=task_id, doc_id=doc_id)
