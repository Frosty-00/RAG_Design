"""Evaluation data types — input dataset rows + per-sample / aggregate results."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# Dataset row (input)
# ─────────────────────────────────────────────────────────────────────


class EvalSample(BaseModel):
    sample_id: str
    question: str
    expected_answer: str = ""
    ground_truth_chunks: list[str] = Field(
        default_factory=list,
        description="Chunk IDs that should appear in retrieval.",
    )
    note: str = ""


# ─────────────────────────────────────────────────────────────────────
# Per-sample result
# ─────────────────────────────────────────────────────────────────────


class SampleMetrics(BaseModel):
    # retrieval (always computed)
    hit_at_5: float = 0.0
    recall_at_5: float = 0.0
    mrr: float = 0.0
    # generation (only in 'full' mode)
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    answer_correctness: float | None = None


class EvalSampleResult(BaseModel):
    sample_id: str
    question: str
    expected_answer: str
    ground_truth_chunk_ids: list[str]
    retrieved_chunk_ids: list[str]
    answer: str = ""
    metrics: SampleMetrics
    bad_case: bool = False
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Aggregate run
# ─────────────────────────────────────────────────────────────────────


class EvalRun(BaseModel):
    run_id: str
    created_at: str
    dataset: str                                # path or name
    mode: Literal["retrieval_only", "full"]
    judge_model: str = ""
    prompt_versions: dict[str, int] = Field(default_factory=dict)
    n_samples: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    samples: list[EvalSampleResult] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)

    def bad_cases(self, faithfulness_threshold: float = 0.5) -> list[EvalSampleResult]:
        return [
            s for s in self.samples
            if s.bad_case
            or s.error is not None
            or (s.metrics.hit_at_5 == 0.0 and s.ground_truth_chunk_ids)
            or (s.metrics.faithfulness is not None
                and s.metrics.faithfulness < faithfulness_threshold)
        ]
