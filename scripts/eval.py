"""CLI: run an evaluation against a JSONL golden dataset.

Examples:
    python -m scripts.eval --dataset eval/datasets/golden.jsonl
    python -m scripts.eval --dataset eval/datasets/mini.jsonl --retrieval-only
    python -m scripts.eval --dataset eval/datasets/golden.jsonl --output eval/reports/

Output: writes `<run_id>.json` and `<run_id>.md` to the output directory and
prints aggregate metrics to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.logger import configure_logging, get_logger
from app.evaluation.runner import EvalRunner, load_dataset, write_run

configure_logging()
log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="self-rag evaluation runner")
    p.add_argument("--dataset", type=str, required=True,
                   help="Path to JSONL eval dataset")
    p.add_argument("--retrieval-only", action="store_true",
                   help="Skip generation + judge; only compute retrieval metrics")
    p.add_argument("--output", type=str, default="eval/reports",
                   help="Directory for JSON+MD report")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap number of samples (smoke run)")
    p.add_argument("--run-id", type=str, default=None)
    return p


async def _run(args) -> int:
    samples = load_dataset(args.dataset)
    if args.limit:
        samples = samples[: args.limit]
    log.info("eval.start", n=len(samples), mode=("retrieval_only" if args.retrieval_only else "full"))

    runner = EvalRunner()
    try:
        run = await runner.run(
            samples,
            mode=("retrieval_only" if args.retrieval_only else "full"),
            dataset_name=str(Path(args.dataset).name),
            run_id=args.run_id,
        )
    finally:
        await runner.aclose()

    out_path = write_run(run, args.output)
    log.info("eval.done", run_id=run.run_id, samples=run.n_samples,
             metrics=run.metrics, out=str(out_path))
    print(f"\n=== eval run {run.run_id} ===")
    print(f"dataset:  {run.dataset}")
    print(f"mode:     {run.mode}")
    print(f"samples:  {run.n_samples}")
    print(f"errors:   {len(run.errors)}")
    print(f"bad_cases:{len(run.bad_cases())}")
    print("metrics:")
    for k, v in sorted(run.metrics.items()):
        print(f"  {k:24s} {v:.4f}")
    print(f"\nReport written to: {out_path}")
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
