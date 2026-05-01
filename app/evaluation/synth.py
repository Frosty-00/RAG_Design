"""Shared QA synthesis helper — used by both `scripts/generate_golden.py`
(CLI) and the eval REST API."""
from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.logger import get_logger
from app.repositories.milvus import MilvusRepository
from app.services.llm import get_llm_client

log = get_logger(__name__)


SYNTH_PROMPT = """\
You are creating an evaluation question for a retrieval-augmented QA system.

Given the chunk of text below, write a single specific factual question
that can be answered using ONLY this chunk, plus a concise reference
answer (1-3 sentences) drawn from the chunk.

Constraints:
- Question must be answerable from the chunk alone, no outside knowledge.
- Question should be the kind a real user might ask.
- Answer must be self-contained and grounded.
- Use the same language as the chunk text (Chinese chunk → Chinese Q/A).
- Output JSON, no extra prose.

CHUNK TEXT:
\"\"\"
{text}
\"\"\"
"""


class GeneratedQA(BaseModel):
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)


async def synthesize_one(client, text: str) -> GeneratedQA:
    qa, _ = await client.generate_structured(
        SYNTH_PROMPT.format(text=text[:1500]),
        GeneratedQA,
        temperature=0.7,
    )
    return qa


async def generate_dataset(
    *,
    output_path: Path,
    count: int,
    pool_size: int = 500,
    doc_ids: list[str] | None = None,
    seed: int = 42,
) -> dict:
    """Sample `count` chunks (optionally restricted to `doc_ids`),
    synthesize (Q, A) pairs via the active LLM, write JSONL to `output_path`.

    Returns: {"path", "samples", "failures", "doc_ids", "count_requested"}.
    """
    rng = random.Random(seed)
    repo = MilvusRepository()
    if not repo.client.has_collection(repo.collection):
        raise RuntimeError(f"collection {repo.collection!r} does not exist")

    expr = "is_latest == true"
    if doc_ids:
        # Quote each doc_id; use Milvus `in [...]` semantics
        quoted = ", ".join('"' + d.replace('"', '\\"') + '"' for d in doc_ids)
        expr += f" and doc_id in [{quoted}]"

    rows = repo.client.query(
        repo.collection, filter=expr,
        output_fields=["chunk_id", "doc_id", "text"],
        limit=pool_size,
    )
    if not rows:
        raise RuntimeError("no chunks matched — upload documents first")

    rng.shuffle(rows)
    sampled = rows[:count]
    log.info("synth.start", from_pool=len(rows), count=len(sampled),
             doc_ids=doc_ids, output=str(output_path))

    client = get_llm_client()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = failures = 0
    per_doc: dict[str, int] = {}
    with output_path.open("w", encoding="utf-8") as f:
        for row in sampled:
            try:
                qa = await synthesize_one(client, row["text"])
            except Exception as e:  # noqa: BLE001
                failures += 1
                log.warning("synth.fail", chunk_id=row["chunk_id"], err=str(e))
                continue
            doc_id = row.get("doc_id") or "unknown"
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            f.write(json.dumps({
                "sample_id": f"syn-{uuid.uuid4().hex[:8]}",
                "question": qa.question,
                "expected_answer": qa.expected_answer,
                "ground_truth_chunks": [row["chunk_id"]],
                "note": f"synthesized from {row['chunk_id']}",
            }, ensure_ascii=False) + "\n")
            written += 1

    return {
        "path": str(output_path),
        "samples": written,
        "failures": failures,
        "doc_ids": doc_ids,
        "count_requested": count,
        "per_doc": per_doc,
        "ts": int(time.time()),
    }


def default_synthetic_path() -> Path:
    """Fixed file — every generate overwrites it. Avoids accumulating
    near-duplicate datasets when a user accidentally double-clicks Generate.
    Save a copy elsewhere if you want to preserve a baseline."""
    return Path("eval/datasets") / "synthetic-latest.jsonl"
