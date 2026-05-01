"""Evaluation REST API — admin only.

Endpoints:
    POST   /api/v1/eval/runs                   start a new eval (Celery)
    GET    /api/v1/eval/runs                   list past runs (newest first)
    GET    /api/v1/eval/runs/{id}              run meta + full report
    DELETE /api/v1/eval/runs/{id}              hard-delete index + reports
    POST   /api/v1/eval/diff                   diff two runs

    GET    /api/v1/eval/datasets               list available .jsonl
    POST   /api/v1/eval/datasets/generate      synthesize dataset from
                                               indexed chunks
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.core.logger import get_logger
from app.evaluation.schema import EvalRun
from app.repositories.milvus import Requester
from app.repositories.redis_repo import RedisRepository
from app.workers.tasks.eval import run_eval_task

log = get_logger(__name__)

router = APIRouter(prefix="/eval", tags=["eval"])


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────


class StartRunRequest(BaseModel):
    dataset: str = Field(..., description="Path to JSONL eval dataset")
    mode: Literal["retrieval_only", "full"] = "full"
    limit: int | None = Field(default=None, ge=1, le=2000)
    note: str = ""


class StartRunResponse(BaseModel):
    run_id: str
    status: str
    dataset: str
    mode: str


class RunMeta(BaseModel):
    run_id: str
    status: str
    dataset: str | None = None
    mode: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    n_samples: int | None = None
    metrics: dict[str, float] | None = None
    prompt_versions: dict[str, int] | None = None
    judge_model: str | None = None
    n_bad_cases: int | None = None
    report_path: str | None = None
    error: str | None = None


class RunDetail(BaseModel):
    meta: RunMeta
    report: dict | None = None


class DiffRequest(BaseModel):
    baseline_id: str
    candidate_id: str


# ─────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────


def _make_run_id() -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{ts}-{uuid.uuid4().hex[:6]}"


@router.post("/runs", response_model=StartRunResponse)
async def start_run(
    body: StartRunRequest,
    _admin: Requester = Depends(require_admin),
) -> StartRunResponse:
    if not Path(body.dataset).exists():
        raise HTTPException(status_code=404, detail="dataset_not_found")

    run_id = _make_run_id()
    run_eval_task.apply_async(kwargs={
        "run_id": run_id,
        "dataset_path": body.dataset,
        "mode": body.mode,
        "limit": body.limit,
    })
    log.info("eval.api.start", run_id=run_id, dataset=body.dataset, mode=body.mode)
    return StartRunResponse(
        run_id=run_id, status="queued",
        dataset=body.dataset, mode=body.mode,
    )


@router.get("/runs", response_model=list[RunMeta])
async def list_runs(
    _admin: Requester = Depends(require_admin),
) -> list[RunMeta]:
    redis = RedisRepository()
    try:
        ids = await redis.client.smembers("eval:runs")
        runs: list[dict] = []
        for rid in ids:
            meta = await redis._get_json(f"eval:run:{rid}")
            if meta:
                runs.append(meta)
    finally:
        await redis.close()
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return [RunMeta(**r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    _admin: Requester = Depends(require_admin),
) -> RunDetail:
    redis = RedisRepository()
    try:
        meta_dict = await redis._get_json(f"eval:run:{run_id}")
    finally:
        await redis.close()
    if not meta_dict:
        raise HTTPException(status_code=404, detail="not_found")

    report: dict | None = None
    if meta_dict.get("status") == "done" and (path := meta_dict.get("report_path")):
        try:
            report = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("eval.api.report_missing", run_id=run_id, path=path)
    return RunDetail(meta=RunMeta(**meta_dict), report=report)


@router.delete("/runs/{run_id}", response_model=dict)
async def delete_run(
    run_id: str,
    _admin: Requester = Depends(require_admin),
) -> dict:
    """Hard-delete a run: index entry + on-disk JSON & Markdown reports."""
    redis = RedisRepository()
    try:
        meta = await redis._get_json(f"eval:run:{run_id}")
        if not meta:
            raise HTTPException(status_code=404, detail="not_found")

        # Remove on-disk reports if any
        report_path = meta.get("report_path")
        deleted_files: list[str] = []
        if report_path:
            base = Path(report_path)
            for ext in (".json", ".md"):
                f = base.with_suffix(ext)
                if f.exists():
                    f.unlink()
                    deleted_files.append(str(f))

        # Drop the index entry + meta
        await redis.client.srem("eval:runs", run_id)
        await redis.client.delete(f"eval:run:{run_id}")
    finally:
        await redis.close()

    log.info("eval.api.deleted", run_id=run_id, files=deleted_files)
    return {"run_id": run_id, "deleted_files": deleted_files}


def _scan_dataset_file(path: Path) -> dict[str, Any]:
    """Read one JSONL eval dataset; return summary (samples, per-doc breakdown,
    a tiny question preview). Robust to malformed lines."""
    n_samples = 0
    per_doc: dict[str, int] = {}
    first_question: str | None = None
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                n_samples += 1
                if first_question is None:
                    q = obj.get("question")
                    if isinstance(q, str):
                        first_question = q[:80]
                for cid in obj.get("ground_truth_chunks") or []:
                    # chunk_id format: `<doc_id>:v<N>:c<NNNN>`
                    doc_id = cid.split(":", 1)[0] if ":" in cid else cid
                    per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
    except Exception:  # noqa: BLE001
        pass
    return {
        "samples": n_samples,
        "per_doc": per_doc,
        "preview_question": first_question,
    }


@router.get("/datasets", response_model=list[dict])
async def list_datasets(
    _admin: Requester = Depends(require_admin),
) -> list[dict[str, Any]]:
    """List `.jsonl` files under `eval/datasets/` with summary metadata.

    Includes a per-doc breakdown so the UI can show *which* documents a
    dataset was synthesized from — useful when several `synthetic-*.jsonl`
    coexist and you need to remember which is which.
    """
    root = Path("eval/datasets")
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(root.glob("*.jsonl")):
        summary = _scan_dataset_file(f)
        out.append({
            "path": str(f).replace("\\", "/"),
            "name": f.name,
            "samples": summary["samples"],
            "per_doc": summary["per_doc"],
            "preview_question": summary["preview_question"],
            "size_bytes": f.stat().st_size,
            "modified": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(f.stat().st_mtime),
            ),
        })
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


@router.delete("/datasets/{name}", response_model=dict)
async def delete_dataset(
    name: str,
    _admin: Requester = Depends(require_admin),
) -> dict:
    """Delete one JSONL dataset file. Path-traversal proof — `name` must be
    a plain filename ending in `.jsonl`."""
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="invalid_name")
    target = Path("eval/datasets") / name
    if not target.exists():
        raise HTTPException(status_code=404, detail="not_found")
    target.unlink()
    log.info("eval.api.dataset_deleted", name=name)
    return {"deleted": name}


class GenerateDatasetRequest(BaseModel):
    count: int = Field(10, ge=1, le=200)
    doc_ids: list[str] | None = None
    output_name: str | None = None  # default: synthetic-<ts>.jsonl


class GenerateDatasetResponse(BaseModel):
    path: str
    samples: int
    failures: int
    doc_ids: list[str] | None = None
    per_doc: dict[str, int] = {}    # doc_id → number of samples
    overwrote_existing: bool = False


@router.post("/datasets/generate", response_model=GenerateDatasetResponse)
async def generate_dataset_endpoint(
    body: GenerateDatasetRequest,
    _admin: Requester = Depends(require_admin),
) -> GenerateDatasetResponse:
    """Synthesize a JSONL eval dataset from already-indexed chunks."""
    from app.evaluation.synth import default_synthetic_path, generate_dataset

    out_path = (Path("eval/datasets") / body.output_name) \
        if body.output_name else default_synthetic_path()
    overwrote = out_path.exists()
    try:
        result = await generate_dataset(
            output_path=out_path,
            count=body.count,
            doc_ids=body.doc_ids,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return GenerateDatasetResponse(
        path=result["path"],
        samples=result["samples"],
        failures=result["failures"],
        doc_ids=result.get("doc_ids"),
        per_doc=result.get("per_doc", {}),
        overwrote_existing=overwrote,
    )


@router.post("/diff", response_model=dict)
async def diff_endpoint(
    body: DiffRequest,
    _admin: Requester = Depends(require_admin),
) -> dict[str, Any]:
    """Compute metric / bad-case / prompt diff between two completed runs."""
    redis = RedisRepository()
    try:
        b_meta = await redis._get_json(f"eval:run:{body.baseline_id}")
        c_meta = await redis._get_json(f"eval:run:{body.candidate_id}")
    finally:
        await redis.close()

    for slot, meta in (("baseline", b_meta), ("candidate", c_meta)):
        if not meta:
            raise HTTPException(status_code=404, detail=f"{slot}_not_found")
        if meta.get("status") != "done":
            raise HTTPException(status_code=400,
                                detail=f"{slot}_not_done (status={meta.get('status')})")

    # Lazy import so the route module stays light
    from scripts.eval_diff import diff_runs

    baseline = EvalRun.model_validate_json(
        Path(b_meta["report_path"]).read_text(encoding="utf-8")
    )
    candidate = EvalRun.model_validate_json(
        Path(c_meta["report_path"]).read_text(encoding="utf-8")
    )
    return diff_runs(baseline, candidate)
