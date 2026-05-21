"""Async Redis repository — single Redis client wraps all use cases.

Namespaces (key prefixes — keep them documented; ad-hoc keys forbidden):
  task:{task_id}                 → JSON {status, doc_id, error, ...}
  session:{sid}                  → JSON list of {role, content}
  auth:token:{sha256}            → JSON {user_id, groups, role}
  auth:user:{user_id}            → set of token-hashes (reverse lookup)
  usage:user:{user_id}:{YYYYMMDD}  → INT (total tokens)
  usage:session:{sid}:{YYYYMMDD}   → INT
  usage:daily:{YYYYMMDD}         → INT (rollup)
  dlq:tasks:{task_id}            → JSON
  emb:{md5(text)}                → JSON {dense, sparse}      (Layer 6)
  ret:{md5(...)}                 → JSON [chunk_id...]        (Layer 6)
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _yyyymmdd(ts: float | None = None) -> str:
    return time.strftime("%Y%m%d", time.gmtime(ts) if ts is not None else time.gmtime())


def _doc_visible_to(meta: dict, user_id: str, user_groups: set[str]) -> bool:
    """ACL match identical to the Milvus `_build_acl_expr` semantics.
    Used by `list_visible_docs` to filter the Redis doc-meta index so the
    Documents page sees exactly what retrieval would surface — no more,
    no less."""
    if meta.get("owner_id") == user_id:
        return True
    acl = meta.get("acl") or {}
    if acl.get("public"):
        return True
    if user_id in (acl.get("users") or []):
        return True
    doc_groups = set(acl.get("groups") or [])
    if doc_groups & user_groups:
        return True
    return False


class RedisRepository:
    """Thin async wrapper around redis.asyncio for namespace discipline."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis_url
        self._client: aioredis.Redis = aioredis.from_url(
            self._url, decode_responses=True
        )

    @property
    def client(self) -> aioredis.Redis:
        return self._client

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    # ---------------------------------------------------------------- low-level helpers

    async def _set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if ttl:
            await self._client.set(key, payload, ex=ttl)
        else:
            await self._client.set(key, payload)

    async def _get_json(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    # ---------------------------------------------------------------- task status

    @staticmethod
    def _task_key(task_id: str) -> str:
        return f"task:{task_id}"

    async def set_task(self, task_id: str, payload: dict[str, Any], ttl: int = 86400) -> None:
        await self._set_json(self._task_key(task_id), payload, ttl=ttl)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        return await self._get_json(self._task_key(task_id))

    async def delete_task(self, task_id: str) -> int:
        return int(await self._client.delete(self._task_key(task_id)))

    # ---------------------------------------------------------------- session

    @staticmethod
    def _session_key(sid: str) -> str:
        return f"session:{sid}"

    async def append_session_turn(
        self, sid: str, role: str, content: str, max_turns: int | None = None
    ) -> None:
        max_turns = max_turns or settings.session_history_turns
        history = await self._get_json(self._session_key(sid)) or []
        history.append({"role": role, "content": content, "ts": time.time()})
        # truncate keeping last N user+assistant pairs (≈ 2N turns)
        if len(history) > max_turns * 2:
            history = history[-max_turns * 2:]
        await self._set_json(self._session_key(sid), history, ttl=settings.session_ttl)

    async def get_session(self, sid: str) -> list[dict[str, Any]]:
        return await self._get_json(self._session_key(sid)) or []

    async def clear_session(self, sid: str) -> int:
        return int(await self._client.delete(self._session_key(sid)))

    # ---------------------------------------------------------------- auth tokens

    @staticmethod
    def _token_key(token_hash: str) -> str:
        return f"auth:token:{token_hash}"

    @staticmethod
    def _user_tokens_key(user_id: str) -> str:
        return f"auth:user:{user_id}"

    async def store_token(
        self, raw_token: str, *, user_id: str, groups: list[str], role: str = "user"
    ) -> str:
        """Hashes the raw token, persists role/groups under the hash. Returns hash."""
        h = sha256_hex(raw_token)
        await self._set_json(
            self._token_key(h),
            {"user_id": user_id, "groups": groups, "role": role,
             "created_at": time.time()},
        )
        await self._client.sadd(self._user_tokens_key(user_id), h)
        return h

    async def lookup_token(self, raw_token: str) -> dict[str, Any] | None:
        h = sha256_hex(raw_token)
        return await self._get_json(self._token_key(h))

    async def revoke_token(self, raw_token: str) -> int:
        h = sha256_hex(raw_token)
        info = await self._get_json(self._token_key(h))
        deleted = int(await self._client.delete(self._token_key(h)))
        if info and deleted:
            await self._client.srem(self._user_tokens_key(info["user_id"]), h)
        return deleted

    async def list_all_users(self) -> list[dict[str, Any]]:
        """Enumerate every user that has at least one stored token.

        Returns one entry per user (deduped across multiple tokens for the
        same user_id) with role / groups taken from the most recent token.
        `has_predictable` flags whether the canonical `{user_id}-dev-token`
        is currently a valid token — lets the admin UI offer one-click
        "switch to this user" without first re-issuing.
        """
        out: dict[str, dict[str, Any]] = {}
        async for key in self._client.scan_iter(match="auth:user:*"):
            user_id = key.split(":", 2)[-1]
            hashes = await self._client.smembers(key)
            if not hashes:
                continue
            # Resolve each hash to its token-info; keep newest as canonical
            infos = []
            for h in hashes:
                info = await self._get_json(self._token_key(h))
                if info:
                    infos.append(info)
            if not infos:
                continue
            latest = max(infos, key=lambda i: i.get("created_at", 0))
            predictable_hash = sha256_hex(f"{user_id}-dev-token")
            has_predictable = predictable_hash in hashes
            out[user_id] = {
                "user_id": user_id,
                "role": latest.get("role", "user"),
                "groups": latest.get("groups", []),
                "n_tokens": len(hashes),
                "has_predictable": has_predictable,
            }
        return list(out.values())

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """Delete every token belonging to `user_id`. Returns count removed.
        Used by `DELETE /admin/users/{user_id}` for user removal."""
        hashes = await self._client.smembers(self._user_tokens_key(user_id))
        if not hashes:
            return 0
        n = 0
        for h in hashes:
            n += int(await self._client.delete(self._token_key(h)))
        await self._client.delete(self._user_tokens_key(user_id))
        return n

    # ---------------------------------------------------------------- usage / cost

    async def incr_usage(
        self, *, user_id: str, session_id: str | None, tokens: int
    ) -> tuple[int, int]:
        """Increments daily counters; returns (user_total, session_total)."""
        date = _yyyymmdd()
        ukey = f"usage:user:{user_id}:{date}"
        await self._client.incrby(ukey, tokens)
        await self._client.expire(ukey, 60 * 60 * 24 * 7)  # keep 7 days
        user_total = int(await self._client.get(ukey) or 0)

        session_total = 0
        if session_id:
            skey = f"usage:session:{session_id}:{date}"
            await self._client.incrby(skey, tokens)
            await self._client.expire(skey, 60 * 60 * 24 * 7)
            session_total = int(await self._client.get(skey) or 0)

        await self._client.incrby(f"usage:daily:{date}", tokens)
        return user_total, session_total

    async def get_user_usage(self, user_id: str, *, date: str | None = None) -> int:
        date = date or _yyyymmdd()
        return int(await self._client.get(f"usage:user:{user_id}:{date}") or 0)

    # ---------------------------------------------------------------- doc meta + ownership index

    @staticmethod
    def _doc_meta_key(doc_id: str) -> str:
        return f"docs:meta:{doc_id}"

    @staticmethod
    def _owner_set_key(owner_id: str) -> str:
        return f"docs:owned:{owner_id}"

    async def set_doc_meta(self, doc_id: str, meta: dict[str, Any]) -> None:
        await self._set_json(self._doc_meta_key(doc_id), meta)
        owner = meta.get("owner_id")
        if owner:
            await self._client.sadd(self._owner_set_key(owner), doc_id)

    async def get_doc_meta(self, doc_id: str) -> dict[str, Any] | None:
        return await self._get_json(self._doc_meta_key(doc_id))

    async def delete_doc_meta(self, doc_id: str) -> int:
        meta = await self.get_doc_meta(doc_id)
        if meta and (owner := meta.get("owner_id")):
            await self._client.srem(self._owner_set_key(owner), doc_id)
        return int(await self._client.delete(self._doc_meta_key(doc_id)))

    async def list_owned_docs(self, owner_id: str) -> list[dict[str, Any]]:
        ids = await self._client.smembers(self._owner_set_key(owner_id))
        out: list[dict[str, Any]] = []
        for did in ids:
            meta = await self.get_doc_meta(did)
            if meta:
                out.append(meta)
        return out

    async def list_all_docs(self) -> list[dict[str, Any]]:
        """Admin-only: scan all doc metadata."""
        out: list[dict[str, Any]] = []
        async for key in self._client.scan_iter(match="docs:meta:*"):
            meta = await self._get_json(key)
            if meta:
                out.append(meta)
        return out

    async def list_visible_docs(
        self, user_id: str, groups: list[str],
    ) -> list[dict[str, Any]]:
        """All docs the caller can see — owner OR ACL.users OR ACL.groups OR public.

        Mirrors the Milvus ACL expr (see `_build_acl_expr`) but operates on
        the Redis docs:meta index, so the Documents page list matches what
        the Chat retrieval would actually surface. Without this, `list_owned_docs`
        alone makes the Documents page misleading — a non-admin couldn't see
        a doc admin had explicitly granted them access to.
        """
        user_groups = set(groups or [])
        out: list[dict[str, Any]] = []
        async for key in self._client.scan_iter(match="docs:meta:*"):
            meta = await self._get_json(key)
            if not meta:
                continue
            if _doc_visible_to(meta, user_id, user_groups):
                out.append(meta)
        return out

    # ---------------------------------------------------------------- DLQ

    async def push_dlq(self, task_id: str, payload: dict[str, Any]) -> None:
        await self._set_json(f"dlq:tasks:{task_id}", payload)

    async def list_dlq(self, limit: int = 100) -> list[str]:
        keys: list[str] = []
        async for key in self._client.scan_iter(match="dlq:tasks:*", count=limit):
            keys.append(key.split(":", 2)[-1])
            if len(keys) >= limit:
                break
        return keys

    # ---------------------------------------------------------------- idempotency

    async def acquire_idempotency(self, key: str, ttl: int = 3600) -> bool:
        """SETNX with TTL — returns True if we won the slot."""
        return bool(await self._client.set(f"idem:{key}", "1", ex=ttl, nx=True))

    async def release_idempotency(self, key: str) -> int:
        return int(await self._client.delete(f"idem:{key}"))
