# Layer 11 — 评估 API 化（Celery 任务 + REST 端点）

## 产出

| 文件 | 内容 |
|---|---|
| `app/workers/tasks/eval.py` | `run_eval_task` Celery task：写 Redis 状态机（`pending → running → done/failed`），调 `EvalRunner`，结果落 `eval/reports/{run_id}.{json,md}` |
| `app/api/v1/eval.py` | 4 端点：`POST /runs` / `GET /runs` / `GET /runs/{id}` / `POST /diff`（admin only） |
| `app/main.py` | mount `eval_router` |
| `app/workers/celery_app.py` | include 新任务模块 |
| `tests/integration/test_eval_api.py` | 6 个集成测试 |

## 设计

### 状态机（Redis `eval:run:{run_id}`）

```
POST /eval/runs
  ↓ apply_async
worker:
  1. set status=running, started_at, dataset, mode
  2. add to set eval:runs
  3. EvalRunner.run(...)
  4. write_run() → eval/reports/{run_id}.json + .md
  5. set status=done + metrics + report_path
  ↑↓ on exception:
  set status=failed + error
```

API 端立即返回 `run_id`，前端轮询 `GET /runs/{id}` 直到 `status=done`。

### 复用 `scripts/eval_diff.py`

`POST /eval/diff` 不重写 diff 逻辑：API 验证两个 run 都 `done` → 读两份 JSON → 直接调 `scripts.eval_diff.diff_runs(...)`。CLI 与 API 永远输出同一份计算结果。

### admin-only 设计

评估涉及大批 LLM judge token 消耗 + 业务全量数据访问，权限收紧为 admin。普通 user token 调任何 `/eval/*` 都返 403。

### 报告物理位置 vs 元数据位置

- 报告本体（含 per-sample 详情）：磁盘 `eval/reports/{run_id}.json` + `.md`（不挤压 Redis 内存）
- 元数据 + aggregate metrics：Redis `eval:run:{run_id}`（快查列表 / 状态轮询）
- `GET /runs/{id}` 返回 `{meta, report}`：meta 总有，report 仅在 `done` 时填充

## 验证结果（6/6 PASS）

```
$ pytest tests/integration/test_eval_api.py -v
TestAuth::test_eval_requires_admin ............... PASSED   ← 403 普通用户
TestAuth::test_list_requires_admin ............... PASSED   ← 403
TestEvalLifecycle::test_dataset_not_found_404 .... PASSED
TestEvalLifecycle::test_full_lifecycle_retrieval_only PASSED   ← 启动→详情→列表→自比 diff 全 0
TestEvalLifecycle::test_diff_run_not_found ....... PASSED   ← 404
TestEvalLifecycle::test_get_run_404 .............. PASSED   ← 404

6 passed in 21s
```

完整回归 `pytest tests/` → **118/118 PASS in 286s**。

## DoD 核对
- [x] `POST /eval/runs` 启动评估，立即返回 run_id（Celery 异步）
- [x] `GET /eval/runs` 历史报告列表（按 started_at 倒序）
- [x] `GET /eval/runs/{id}` 详情含 meta + 完整报告（done 时）
- [x] `POST /eval/diff` 复用 `scripts/eval_diff.py:diff_runs`，与 CLI 输出一致
- [x] admin-only 权限收紧；普通用户 403
- [x] CLI 与 API 共享同一份 `EvalRunner` 实现（无重复）

## 注意 / 后续 layer

- **任务进度可见性**：当前只有 `running / done / failed` 三态；需要更细粒度（如 "正在评估 12/30 sample"）的话，让 EvalRunner 在每条 sample 后写 progress 到 Redis
- **Report 路径与 docker volume**：生产部署时 `eval/reports/` 必须在容器与 host 挂载共享，否则 worker 写、API 进程读不到
- **判定结果稳定性**：retrieval_only 是确定性的；full 模式 judge 受 LLM temperature 影响，跑两次同 sample 分数会有 ±0.05 抖动——评估面板要显示置信带或 N=3 多采样均值
- **Layer 13 评估面板**：直接消费这套 API，无需新增后端
