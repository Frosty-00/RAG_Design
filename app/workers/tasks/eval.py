"""Celery task: run an evaluation against a dataset; persist meta to Redis.

Status flow (`eval:run:{run_id}` JSON):
    pending  → running  → done | failed

Meta on `done` includes aggregate metrics + path to JSON+MD report on disk.
"""
from __future__ import annotations

from pathlib import Path

from celery import Task

from app.core.logger import get_logger
from app.evaluation.runner import EvalRunner, load_dataset, write_run
from app.repositories.redis_repo import RedisRepository
from app.workers.celery_app import celery_app
from app.workers.tasks._helpers import now_iso, run_async

log = get_logger(__name__)


class EvalTask(Task):
    autoretry_for = ()      # don't retry: evaluation is expensive + deterministic
    max_retries = 0


@celery_app.task(base=EvalTask, name="run_eval", bind=True)
def run_eval_task(
    self,
    *,
    run_id: str,
    dataset_path: str,
    mode: str = "full",
    limit: int | None = None,
    output_dir: str = "eval/reports",
) -> dict:
    log.info("eval.task.start", run_id=run_id, dataset=dataset_path, mode=mode)
    return run_async(_run_eval_async(
        run_id=run_id, dataset_path=dataset_path,
        mode=mode, limit=limit, output_dir=output_dir,
    ))


async def _run_eval_async(
    *, run_id: str, dataset_path: str,
    mode: str, limit: int | None, output_dir: str,
) -> dict:
    redis = RedisRepository()
    started_at = now_iso()
    try:
        # ── mark running ─────────────────────────────────────────────
        await redis._set_json(f"eval:run:{run_id}", {
            "run_id": run_id,
            "status": "running",
            "dataset": dataset_path,
            "mode": mode,
            "started_at": started_at,
            "limit": limit,
        })
        await redis.client.sadd("eval:runs", run_id)

        # ── load + run ───────────────────────────────────────────────
        samples = load_dataset(dataset_path)
        if limit:
            samples = samples[: limit]

        runner = EvalRunner()
        try:
            run = await runner.run(
                samples,
                mode=mode,  # type: ignore[arg-type]
                dataset_name=Path(dataset_path).name,
                run_id=run_id,
            )
        finally:
            await runner.aclose()

        report_path = write_run(run, output_dir)

        # ── mark done ────────────────────────────────────────────────
        meta = {
            "run_id": run_id,
            "status": "done",
            "dataset": dataset_path,
            "mode": mode,
            "started_at": started_at,
            "finished_at": now_iso(),
            "n_samples": run.n_samples,
            "metrics": run.metrics,
            "prompt_versions": run.prompt_versions,
            "judge_model": run.judge_model,
            "n_bad_cases": len(run.bad_cases()),
            "report_path": str(report_path),
        }
        await redis._set_json(f"eval:run:{run_id}", meta)
        log.info("eval.task.done", run_id=run_id, metrics=run.metrics)
        return meta

    except Exception as e:  # noqa: BLE001
        log.error("eval.task.failed", run_id=run_id, err=str(e))
        await redis._set_json(f"eval:run:{run_id}", {
            "run_id": run_id,
            "status": "failed",
            "dataset": dataset_path,
            "mode": mode,
            "started_at": started_at,
            "finished_at": now_iso(),
            "error": str(e),
        })
        raise
    finally:
        await redis.close()
