"""CLI: synthesize a golden dataset from already-indexed Milvus chunks.

Wraps `app.evaluation.synth.generate_dataset` with arg parsing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import settings
from app.core.logger import configure_logging, get_logger
from app.evaluation.synth import generate_dataset

configure_logging()
log = get_logger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="eval/datasets/synthetic.jsonl")
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--pool-size", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--doc-id", action="append", default=None,
                    help="Restrict source chunks to these doc_ids (repeatable).")
    args = ap.parse_args()
    if not (settings.vertex_project or settings.deepseek_api_key):
        print("No LLM provider configured (VERTEX_PROJECT or DEEPSEEK_API_KEY).",
              file=sys.stderr)
        return 2

    result = asyncio.run(generate_dataset(
        output_path=Path(args.output),
        count=args.count,
        pool_size=args.pool_size,
        doc_ids=args.doc_id,
        seed=args.seed,
    ))
    print(f"\nWrote {result['samples']} samples to {result['path']}  "
          f"(failures: {result['failures']})")
    print("Next: hand-review and copy approved rows to "
          "eval/datasets/golden.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
