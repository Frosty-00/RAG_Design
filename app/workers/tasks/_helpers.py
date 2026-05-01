"""Shared helpers for Celery tasks.

Tasks are sync (Celery worker thread); we run their internal coroutines
on a one-shot loop. When eager mode is used inside a FastAPI async test
(or any caller already in a loop), `asyncio.run()` raises — fall back to
running on a fresh thread with its own loop.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """Run a coroutine to completion synchronously, no matter the caller."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — fast path
        return asyncio.run(coro)

    # Already in a loop (FastAPI tests in eager mode). Spin up a worker
    # thread with its own event loop so we don't nest asyncio.run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
