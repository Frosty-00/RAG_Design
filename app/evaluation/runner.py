"""EvalRunner — orchestrates dataset → predictions → metrics → report.

Reuses the live `RAGPipeline` so what we measure is exactly what production
serves. For `mode='retrieval_only'` we bypass LLM generation, computing
retrieval metrics from the same Retriever the pipeline uses.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from app.core.config import settings
from app.core.logger import get_logger
from app.evaluation.judge import GenerationJudge
from app.evaluation.metrics import aggregate, hit_at_k, mean_reciprocal_rank, recall_at_k
from app.evaluation.schema import (
    EvalRun,
    EvalSample,
    EvalSampleResult,
    SampleMetrics,
)
from app.repositories.milvus import Requester
from app.services.rag import ChatChunk, Citation, RAGPipeline

log = get_logger(__name__)


def load_dataset(path: str | Path) -> list[EvalSample]:
    """One JSON object per line."""
    path = Path(path)
    samples: list[EvalSample] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(EvalSample.model_validate_json(line))
    return samples


def write_run(run: EvalRun, out_dir: str | Path) -> Path:
    """Write report as JSON + a Markdown sidecar."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{run.run_id}.json"
    md_path = out_dir / f"{run.run_id}.md"

    json_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(run), encoding="utf-8")
    return json_path


def _render_markdown(run: EvalRun) -> str:
    lines = [
        f"# Eval Run `{run.run_id}`",
        f"- created_at: {run.created_at}",
        f"- dataset: `{run.dataset}`  ({run.n_samples} samples)",
        f"- mode: **{run.mode}**",
        f"- judge: `{run.judge_model or '-'}`",
        f"- prompt_versions: `{json.dumps(run.prompt_versions, sort_keys=True)}`",
        "",
        "## Aggregate metrics",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k, v in sorted(run.metrics.items()):
        lines.append(f"| `{k}` | {v:.4f} |")
    bad = run.bad_cases()
    lines += [
        "",
        f"## Bad cases ({len(bad)} / {run.n_samples})",
        "",
    ]
    for s in bad[:20]:
        lines.append(f"- **{s.sample_id}** — {s.question[:80]} "
                     f"(hit@5={s.metrics.hit_at_5:.0f}, "
                     f"faithful={s.metrics.faithfulness or 0.0:.2f})")
        if s.error:
            lines.append(f"   - error: {s.error}")
    if run.errors:
        lines.append("")
        lines.append("## Pipeline errors")
        for e in run.errors[:20]:
            lines.append(f"- {e}")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────


class EvalRunner:
    def __init__(
        self,
        pipeline: RAGPipeline | None = None,
        judge: GenerationJudge | None = None,
        *,
        requester: Requester | None = None,
        deterministic: bool = True,
    ) -> None:
        # `deterministic=True` forces temperature=0 on the answer LLM so
        # rerunning the same dataset gives reproducible scores. Judge is
        # already temp=0; Query Understanding stays stochastic on purpose
        # (we evaluate the production path, not a fictional system).
        self.pipeline = pipeline or RAGPipeline(deterministic=deterministic)
        if pipeline is not None:
            # Caller-supplied pipeline still gets the flag toggled, so the
            # convenience default isn't silently bypassed.
            self.pipeline.deterministic = deterministic
        self.judge = judge  # lazy: only required for full mode
        # Default requester is admin — evaluation must see the entire corpus
        self.requester = requester or Requester(user_id="eval", is_admin=True)

    async def aclose(self) -> None:
        try:
            await self.pipeline.aclose()
        except Exception:  # noqa: BLE001
            pass

    async def run(
        self,
        samples: list[EvalSample],
        *,
        mode: Literal["retrieval_only", "full"] = "full",
        dataset_name: str = "(unknown)",
        run_id: str | None = None,
    ) -> EvalRun:
        if mode == "full" and self.judge is None:
            self.judge = GenerationJudge()

        run_id = run_id or _make_run_id()
        run = EvalRun(
            run_id=run_id,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            dataset=dataset_name,
            mode=mode,
            n_samples=len(samples),
            judge_model=settings.vertex_judge_model if mode == "full" else "",
        )

        prompt_versions: Counter[str] = Counter()

        for s in samples:
            try:
                sample_result, run_prompt_versions = await self._run_one(s, mode)
                run.samples.append(sample_result)
                for k, v in run_prompt_versions.items():
                    prompt_versions[k] = v  # last writer wins (versions stable)
            except Exception as e:  # noqa: BLE001
                log.error("eval.sample_failed", sid=s.sample_id, err=str(e))
                run.errors.append({"sample_id": s.sample_id, "error": str(e)})
                run.samples.append(EvalSampleResult(
                    sample_id=s.sample_id, question=s.question,
                    expected_answer=s.expected_answer,
                    ground_truth_chunk_ids=s.ground_truth_chunks,
                    retrieved_chunk_ids=[], answer="",
                    metrics=SampleMetrics(),
                    bad_case=True, error=str(e),
                ))

        # aggregates
        run.prompt_versions = dict(prompt_versions)
        run.metrics = self._aggregate(run.samples, mode)
        return run

    # ----------------------------------------------------------- per-sample

    async def _run_one(
        self, sample: EvalSample, mode: Literal["retrieval_only", "full"]
    ) -> tuple[EvalSampleResult, dict[str, int]]:
        retrieved_ids: list[str] = []
        answer_buf: list[str] = []
        citations: list[Citation] = []
        meta: dict = {}
        prompt_versions: dict[str, int] = {}

        if mode == "retrieval_only":
            # bypass full pipeline: ask retriever directly
            res = await self.pipeline.retriever.retrieve(
                sample.question, requester=self.requester,
            )
            retrieved_ids = [c.chunk_id for c in res.chunks]
            citations = [Citation(
                index=i + 1, chunk_id=c.chunk_id, doc_id=c.doc_id,
                page=(c.metadata or {}).get("page"),
                breadcrumbs=list((c.metadata or {}).get("breadcrumbs") or []),
                text_preview=c.text[:200],
            ) for i, c in enumerate(res.chunks)]
            # context preview for judge (unused in retrieval_only)
        else:
            async for ev in self.pipeline.answer_stream(
                sample.question, history=[],
                requester=self.requester,
            ):
                if ev.event == "token" and ev.token:
                    answer_buf.append(ev.token)
                elif ev.event == "citations":
                    citations = ev.citations or []
                    if ev.meta:
                        meta = ev.meta
                        prompt_versions = ev.meta.get("prompt_versions", {}) or {}
            retrieved_ids = [c.chunk_id for c in citations]

        answer = "".join(answer_buf).strip()
        context = self._format_context(citations)

        # Retrieval metrics
        gt = sample.ground_truth_chunks
        m = SampleMetrics(
            hit_at_5=hit_at_k(retrieved_ids, gt, 5),
            recall_at_5=recall_at_k(retrieved_ids, gt, 5),
            mrr=mean_reciprocal_rank(retrieved_ids, gt),
        )

        # Generation metrics
        if mode == "full":
            scores = await self.judge.score(
                question=sample.question,
                expected=sample.expected_answer,
                context=context,
                answer=answer,
            )
            m.faithfulness = scores.faithfulness
            m.answer_relevancy = scores.answer_relevancy
            m.answer_correctness = scores.answer_correctness

        bad = self._is_bad_case(m, gt)
        result = EvalSampleResult(
            sample_id=sample.sample_id,
            question=sample.question,
            expected_answer=sample.expected_answer,
            ground_truth_chunk_ids=gt,
            retrieved_chunk_ids=retrieved_ids,
            answer=answer,
            metrics=m,
            bad_case=bad,
        )
        return result, prompt_versions

    @staticmethod
    def _format_context(citations: list[Citation]) -> str:
        return "\n\n".join(
            f"[{c.index}] {c.text_preview}" for c in citations
        )

    @staticmethod
    def _is_bad_case(m: SampleMetrics, gt: list[str]) -> bool:
        if gt and m.hit_at_5 == 0.0:
            return True
        if m.faithfulness is not None and m.faithfulness < 0.5:
            return True
        return False

    @staticmethod
    def _aggregate(
        samples: list[EvalSampleResult],
        mode: Literal["retrieval_only", "full"],
    ) -> dict[str, float]:
        out = {
            "hit_at_5": aggregate([s.metrics.hit_at_5 for s in samples]),
            "recall_at_5": aggregate([s.metrics.recall_at_5 for s in samples]),
            "mrr": aggregate([s.metrics.mrr for s in samples]),
        }
        if mode == "full":
            f = [s.metrics.faithfulness for s in samples if s.metrics.faithfulness is not None]
            r = [s.metrics.answer_relevancy for s in samples if s.metrics.answer_relevancy is not None]
            c = [s.metrics.answer_correctness for s in samples if s.metrics.answer_correctness is not None]
            out["faithfulness"] = aggregate(f)
            out["answer_relevancy"] = aggregate(r)
            out["answer_correctness"] = aggregate(c)
        return out


def _make_run_id() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{ts}-{uuid.uuid4().hex[:6]}"
