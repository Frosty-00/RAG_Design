"""LLM-as-judge for generation metrics.

Layer 10 deliberately avoids RAGAS (langchain-coupled) — see docs/layer-10.md.
Instead we ask Gemini 2.5 Pro for three RAGAS-equivalent scores in ONE call:

  - faithfulness:        answer's claims grounded in context (0..1)
  - answer_relevancy:    answer addresses the question (0..1)
  - answer_correctness:  answer aligns with the expected/reference answer (0..1)

Judge model is intentionally distinct from the generator (`vertex_judge_model`,
default Gemini 2.5 Pro) to avoid self-bias.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logger import get_logger
from app.services.llm import VertexGeminiClient, get_llm_client

log = get_logger(__name__)


JUDGE_PROMPT = """\
You are an evaluation judge for a retrieval-augmented QA system. Score the
candidate answer along three independent axes, each from 0.0 to 1.0:

  - faithfulness:       Are all factual claims in the answer supported by the context?
                         (1.0 = every claim grounded; 0.0 = mostly hallucinated)
  - answer_relevancy:   Does the answer actually address the user's question?
                         (1.0 = directly addresses it; 0.0 = unrelated)
  - answer_correctness: How well does the answer match the reference answer?
                         (1.0 = same meaning; 0.0 = contradicts or misses the point)

Be strict but fair. Output JSON only.

----- USER QUESTION -----
{question}
----- REFERENCE ANSWER (gold) -----
{expected}
----- RETRIEVED CONTEXT -----
{context}
----- CANDIDATE ANSWER -----
{answer}
"""


class JudgeScores(BaseModel):
    faithfulness: float = Field(ge=0.0, le=1.0)
    answer_relevancy: float = Field(ge=0.0, le=1.0)
    answer_correctness: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class GenerationJudge:
    """Single-call LLM judge using `settings.vertex_judge_model`."""

    def __init__(
        self,
        client=None,  # any LLMClient-shape (Vertex / DeepSeek)
        judge_model: str | None = None,
    ) -> None:
        self.client = client or get_llm_client()
        # Pick judge model from the active provider (vertex / deepseek)
        self.judge_model = (
            judge_model
            or getattr(self.client, "judge_model", None)
            or settings.vertex_judge_model
        )

    async def score(
        self,
        *,
        question: str,
        expected: str,
        context: str,
        answer: str,
    ) -> JudgeScores:
        prompt = JUDGE_PROMPT.format(
            question=question.strip() or "(empty)",
            expected=(expected or "(no reference provided)").strip(),
            context=(context or "(no retrieved context)").strip(),
            answer=(answer or "(empty answer)").strip(),
        )
        try:
            scores, _ = await self.client.generate_structured(
                prompt, JudgeScores,
                model=self.judge_model,
                temperature=0.0,
            )
            return scores
        except Exception as e:  # noqa: BLE001
            log.warning("judge.failed", err=str(e))
            # Conservative fallback: zero out so failures show up as bad cases
            return JudgeScores(
                faithfulness=0.0, answer_relevancy=0.0, answer_correctness=0.0,
                reasoning=f"judge_error: {e}",
            )
