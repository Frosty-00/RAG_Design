# Layer 5 — 异步入库 (Celery + DLQ + 幂等 + 版本化 + 级联删除)

## 产出

| 文件 | 内容 |
|---|---|
| `app/workers/celery_app.py` | Celery 应用：broker/backend、`task_acks_late`、`reject_on_worker_lost` 等可靠性配置 |
| `app/workers/tasks/_helpers.py` | `run_async()` — 让 Celery sync task 跑 async 协程；每次新建 event loop |
| `app/workers/tasks/ingest.py` | `IngestTask`（带 retry + DLQ on_failure）+ `ingest_document` 任务函数；流程：idem→download→parse→chunk→embed→insert→demote 旧版本 |
| `app/workers/tasks/cascade_delete.py` | 跨 Milvus + MinIO + Redis 的级联删除；任一系统失败不阻塞其它 |
| `tests/integration/test_workers.py` | 6 个集成测试：happy path / idempotency / versioning / DLQ × 2 / cascade |

## 关键修复

实现过程中发现并修掉两个 bug：

1. **chunk_id 不含版本号**：`MetadataEnricher` 原本生成 `{doc_id}:c{idx:04d}`，v2 入库会和 v1 主键冲突 → `mark_old_versions_inactive` 错误失效。修复：当 `metadata["doc_version"]` 存在时改为 `{doc_id}:v{ver}:c{idx:04d}`，单元测试无版本依然兼容
2. **幂等 finally 误释放**：`_ingest_async` 的 finally 无条件 `release_idempotency`，会把别人持有的锁删掉。修复：跟踪 `acquired` 标志，只在自己拿到锁时释放

## 关键设计

### DLQ 走 `Task.on_failure`，不开新队列

不在 Celery 里开 dedicated DLQ 队列（plan 提过两种做法），原因：失败任务的恢复逻辑——是手动 retry 还是放弃——属于业务决策；用 Redis kv (`dlq:tasks:{id}`) 存元数据，admin API（Layer 9）按需重新投递原任务签名。逻辑简单、无重复消费风险。

`on_failure` 中通过 `self.request.retries >= self.max_retries` 区分"还能重试"与"耗尽" — 测试覆盖两条路径。

### 幂等键 `ingest:{doc_id}:v{version}` (Redis SETNX, TTL 1h)

- 同 doc + 同版本并发投递只会有一个真正执行；其余收到 `status="skipped_idempotent"`
- 1 小时 TTL 防止任务死锁不释放
- Layer 9 投递任务时也会先 SETNX，但 worker 内的二次检查覆盖"投递时未持有锁但 worker 间空隙也不能重入"的边界

### 跨进程 `RedisRepository` 复用问题

测试早期一版用 module-scoped redis fixture + `asyncio.run(...)`，触发 "Event loop is closed"——`redis.asyncio.Redis` 的连接池绑到首次 await 的 loop，后续 `asyncio.run` 新 loop 时连接已废。最终方案：测试中每次需要 redis 操作时，在 `_with_redis(...)` helper 内现场实例化、协程结束 close。生产代码无此问题（worker 内每个任务一个 loop）。

## 验证结果（43/43 PASS — 全套回归）

```
$ pytest tests/ -v
tests/integration/test_models.py ........... 11 PASSED
tests/integration/test_repositories.py ..... 14 PASSED
tests/integration/test_workers.py ..........  6 PASSED  ← Layer 5
tests/unit/test_parsing.py .................. 12 PASSED

43 passed in 145.95s
```

Layer 5 关键 case：
- `test_ingest_creates_chunks_in_milvus`     → end-to-end 入库 + 检索可达
- `test_ingest_idempotency_blocks_concurrent`→ 预占锁 → 任务跳过 → milvus 0 chunks
- `test_ingest_versioning_demotes_v1`        → v1 + v2 后，hybrid_search 仅返回 v2
- `test_dlq_writes_when_retries_exhausted`   → 模拟耗尽，DLQ 写入 `dlq:tasks:*`
- `test_dlq_skipped_when_not_yet_exhausted`  → 未耗尽时不写
- `test_cascade_clears_milvus_and_minio`     → 三系统全清；status=done

## DoD 满足
- [x] sample.md 入库后 Milvus chunk_count > 0，状态流转 pending→...→done
- [x] 同 doc_id + 同 version 重投只跑一次（幂等键生效）
- [x] 同 doc_id 不同 version → v1 自动 `is_latest=false`，检索仅返回 latest
- [x] 任务失败超 retries 后写入 Redis DLQ；DLQ 含原 task name / kwargs / error
- [x] cascade_delete：Milvus 0 chunks + MinIO 0 objects + Redis 任务状态归位

## 注意 / 后续 layer

- **eager mode 不真正循环 retry**：DLQ 测试通过手动调 `on_failure` 验证，与生产一致（生产中由 Celery 调度器循环）
- **Worker 启动命令**（手动起）：`celery -A app.workers.celery_app worker -l info -P solo`（Windows 必须 `-P solo`，prefork 不支持）
- **模型加载冗余**：API 进程与 worker 进程各自加载一份 BGE-M3（~2 GB）。Layer 15a 可拆独立模型服务消除
- **检索缓存失效**：cascade_delete 没主动清 retrieval 缓存（Layer 6 引入），靠 30 min TTL 自然过期；ACL/index_version 进缓存键，schema 变更时全失效
