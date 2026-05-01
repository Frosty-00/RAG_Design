"""Retrieval metrics — pure compute, no LLM."""
from __future__ import annotations

from collections.abc import Sequence


def hit_at_k(retrieved: Sequence[str], ground_truth: Sequence[str], k: int) -> float:
    """1.0 if any ground-truth id appears in retrieved[:k], else 0.0."""
    if not ground_truth:
        return 0.0  # cannot score without truth
    head = list(retrieved)[:k]
    return float(any(g in head for g in ground_truth))


def recall_at_k(retrieved: Sequence[str], ground_truth: Sequence[str], k: int) -> float:
    """Fraction of ground_truth ids that appear in retrieved[:k]."""
    if not ground_truth:
        return 0.0
    head = set(list(retrieved)[:k])
    hits = sum(1 for g in ground_truth if g in head)
    return hits / len(ground_truth)


def mean_reciprocal_rank(
    retrieved: Sequence[str], ground_truth: Sequence[str]
) -> float:
    """Reciprocal rank of the first ground-truth hit (1-based). 0 if none hit."""
    if not ground_truth:
        return 0.0
    truth_set = set(ground_truth)
    for i, c in enumerate(retrieved, start=1):
        if c in truth_set:
            return 1.0 / i
    return 0.0


def aggregate(values: list[float]) -> float:
    """Mean — empty list returns 0.0 to keep aggregate keys present."""
    return sum(values) / len(values) if values else 0.0
