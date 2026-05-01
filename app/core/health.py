"""Readiness probes for downstream dependencies.

Each probe returns (component_name, ok, detail). `/readyz` aggregates them
and returns 503 if any are down — with the failing component named in the body.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class ProbeResult:
    component: str
    ok: bool
    detail: str = ""


async def _probe_redis() -> ProbeResult:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        try:
            pong = await client.ping()
            return ProbeResult("redis", bool(pong))
        finally:
            await client.aclose()
    except Exception as e:
        return ProbeResult("redis", False, str(e))


async def _probe_milvus() -> ProbeResult:
    """Probe Milvus health endpoint via HTTP (port 9091).

    pymilvus's connection ping is heavier; the HTTP /healthz is what the
    Milvus container's own healthcheck uses, so we mirror that.
    """
    try:
        import httpx

        url = f"http://{settings.milvus_host}:9091/healthz"
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            ok = resp.status_code == 200 and resp.text.strip() == "OK"
            return ProbeResult("milvus", ok, "" if ok else f"HTTP {resp.status_code}")
    except Exception as e:
        return ProbeResult("milvus", False, str(e))


async def _probe_minio() -> ProbeResult:
    try:
        import httpx

        scheme = "https" if settings.minio_use_ssl else "http"
        url = f"{scheme}://{settings.minio_endpoint}/minio/health/live"
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            return ProbeResult("minio", resp.status_code == 200,
                               "" if resp.status_code == 200 else f"HTTP {resp.status_code}")
    except Exception as e:
        return ProbeResult("minio", False, str(e))


_PROBES: list[Callable[[], Awaitable[ProbeResult]]] = [
    _probe_redis,
    _probe_milvus,
    _probe_minio,
]


async def run_probes() -> list[ProbeResult]:
    results = await asyncio.gather(*(p() for p in _PROBES), return_exceptions=False)
    return list(results)
