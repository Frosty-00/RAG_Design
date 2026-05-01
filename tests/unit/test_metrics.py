"""Layer 10 — pure retrieval metrics (no LLM, no Milvus)."""
from __future__ import annotations

from app.evaluation.metrics import (
    aggregate,
    hit_at_k,
    mean_reciprocal_rank,
    recall_at_k,
)


class TestHitAtK:
    def test_top1_hit(self):
        assert hit_at_k(["a", "b", "c"], ["a"], 5) == 1.0

    def test_outside_k(self):
        assert hit_at_k(["a", "b", "c", "d", "e", "f"], ["f"], 5) == 0.0

    def test_no_truth(self):
        assert hit_at_k(["a"], [], 5) == 0.0


class TestRecallAtK:
    def test_full_recall(self):
        assert recall_at_k(["a", "b", "c"], ["a", "b"], 5) == 1.0

    def test_partial(self):
        assert recall_at_k(["a", "x", "y"], ["a", "b"], 5) == 0.5

    def test_zero(self):
        assert recall_at_k(["x", "y"], ["a", "b"], 5) == 0.0

    def test_k_clipping(self):
        # ground truth at position 6, k=5 → miss
        assert recall_at_k(["x"] * 5 + ["a"], ["a"], 5) == 0.0


class TestMRR:
    def test_first_position(self):
        assert mean_reciprocal_rank(["a", "b"], ["a"]) == 1.0

    def test_third_position(self):
        assert mean_reciprocal_rank(["x", "y", "a"], ["a"]) == 1.0 / 3

    def test_no_hit(self):
        assert mean_reciprocal_rank(["x", "y"], ["a"]) == 0.0


class TestAggregate:
    def test_mean(self):
        assert aggregate([0.0, 0.5, 1.0]) == 0.5

    def test_empty(self):
        assert aggregate([]) == 0.0
