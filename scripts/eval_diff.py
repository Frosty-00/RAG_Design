"""Compare two evaluation reports — metric deltas + bad-case diff.

Usage:
    python -m scripts.eval_diff --baseline eval/reports/A.json --candidate eval/reports/B.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.schema import EvalRun


def _load(p: str | Path) -> EvalRun:
    return EvalRun.model_validate_json(Path(p).read_text(encoding="utf-8"))


def diff_runs(baseline: EvalRun, candidate: EvalRun) -> dict:
    keys = sorted(set(baseline.metrics) | set(candidate.metrics))
    metric_diffs = {}
    for k in keys:
        b = baseline.metrics.get(k, 0.0)
        c = candidate.metrics.get(k, 0.0)
        metric_diffs[k] = {
            "baseline": b, "candidate": c, "delta": c - b,
            "pct": (c - b) / b if b else 0.0,
        }

    base_bad = {s.sample_id for s in baseline.bad_cases()}
    cand_bad = {s.sample_id for s in candidate.bad_cases()}
    return {
        "baseline_id": baseline.run_id,
        "candidate_id": candidate.run_id,
        "baseline_dataset": baseline.dataset,
        "candidate_dataset": candidate.dataset,
        "metric_diffs": metric_diffs,
        "newly_bad": sorted(cand_bad - base_bad),
        "newly_good": sorted(base_bad - cand_bad),
        "prompt_changes": _prompt_diff(baseline.prompt_versions, candidate.prompt_versions),
    }


def _prompt_diff(a: dict, b: dict) -> dict:
    keys = sorted(set(a) | set(b))
    return {k: {"baseline": a.get(k), "candidate": b.get(k)}
            for k in keys if a.get(k) != b.get(k)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--json", action="store_true",
                    help="Print machine-readable JSON only")
    args = ap.parse_args()

    b = _load(args.baseline)
    c = _load(args.candidate)
    out = diff_runs(b, c)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"baseline:  {b.run_id}  dataset={b.dataset}  n={b.n_samples}")
    print(f"candidate: {c.run_id}  dataset={c.dataset}  n={c.n_samples}")
    print()
    print(f"{'metric':24s} {'baseline':>10s} {'candidate':>10s} {'delta':>10s} {'pct':>7s}")
    print("-" * 64)
    for k, v in out["metric_diffs"].items():
        sign = "+" if v["delta"] >= 0 else ""
        pct = f"{sign}{v['pct'] * 100:6.1f}%" if v["baseline"] else "    --"
        print(f"{k:24s} {v['baseline']:>10.4f} {v['candidate']:>10.4f} "
              f"{sign}{v['delta']:>+9.4f} {pct:>7s}")

    if out["prompt_changes"]:
        print("\nPrompt versions changed:")
        for k, v in out["prompt_changes"].items():
            print(f"  {k}: {v['baseline']} → {v['candidate']}")

    print(f"\nNewly bad ({len(out['newly_bad'])}): {out['newly_bad'][:10]}")
    print(f"Newly good ({len(out['newly_good'])}): {out['newly_good'][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
