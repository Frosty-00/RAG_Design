# Layer 9 — API 层（路由 + 鉴权 + Prometheus + cascade delete）

## 产出

| 文件 | 内容 |
|---|---|
| `app/api/deps.py` | `get_requester` (Bearer auth) / `require_admin` / `get_pipeline` / `bootstrap_admin_token` |
| `app/api/v1/documents.py` | `POST /documents` 上传（multipart）+ dedup、`GET` 列表、`GET /{id}`、`GET /{id}/task`、`DELETE /{id}` cascade |
| `app/api/v1/chat.py` | `POST /chat` SSE 流（含 token 用量预检 + 会话历史读写） |
| `app/api/v1/admin.py` | token 颁发/撤销 + DLQ 列表/详情/重试 |
| `app/api/v1/debug.py` | `POST /debug/retrieve`（仅 dev 环境注册） |
| `app/main.py` | lifespan：bootstrap admin / 建桶 / 建库 / 建 RAGPipeline；mount routers + `/metrics` |
| `app/core/metrics.py` | Prometheus Counter/Histogram 定义（query/cache/llm/celery） |
| `tests/integration/test_api.py` | 12 个集成测试 |

修改的既有文件：
- `app/repositories/redis_repo.py` — 新增 `set_doc_meta` / `get_doc_meta` / `delete_doc_meta` / `list_owned_docs` / `list_all_docs`
- `app/workers/tasks/ingest.py` — done 时写 `docs:meta:*` + 加入 `docs:owned:{owner}` set
- `app/workers/tasks/cascade_delete.py` — 同步清理 doc_meta + owner set + 全量 retrieval cache
- `app/workers/tasks/_helpers.py` — `run_async` 检测当前 loop，已在 loop 中则在 worker 线程跑（修复 FastAPI async + Celery eager 嵌套）

## 关键设计

### 鉴权：Bearer Token + Redis 哈希存储
- `auth:token:{sha256}` → `{user_id, groups, role}`，注册时返回明文（仅一次）
- 每次 API 调用 `Depends(get_requester)`：解析 header → 查 Redis → 注入 `Requester(user_id, groups, is_admin)`
- 启动时 `bootstrap_admin_token`：把 `.env` 中的 `ADMIN_TOKEN` 哈希入 Redis，role=admin（幂等）

### 文档元数据 = Redis（不是 Milvus 二次扫描）
- Redis `docs:meta:{doc_id}`（owner/filename/version/status/n_chunks）
- Redis `docs:owned:{owner_id}` set 加快 `GET /documents` 列表查询
- ingest task `done` 写入；cascade_delete 同步清理 → 列表自动反映状态

### Cascade Delete 跨系统协调
- DELETE 路由只投递任务，立即返回 task_id（前端轮询）
- Celery 任务依次清：Milvus chunks → MinIO 对象 → Redis（doc_meta + owner set + DLQ + 全量 ret cache）
- 任一步失败记录到 task status 但不阻塞其它步骤

### SSE 流
- `text/event-stream` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`（Nginx 兜底）
- 每个 `ChatChunk` 序列化成 `event: <type>\ndata: <json>\n\n`
- 流结束后异步把 `(user_query, assistant_full_text)` 写入 `session:{sid}`

### 429 Token 限流
- `POST /chat` 进入时查 `usage:user:{uid}:{date}`，超 `LLM_DAILY_USER_TOKEN_LIMIT` 直接 429
- 用量在 LLM 客户端流结束后累计到 Redis（Layer 7）

### Prometheus
- `prometheus_fastapi_instrumentator` 提供 `/metrics`（HTTP 请求计数/时延）
- `app/core/metrics.py` 定义业务指标（rag_query_total / llm_tokens_total / celery_task_total 等）

## 关键修复

发现并修一个隐蔽 bug：**FastAPI 的 async 路由 + Celery eager mode → `_ingest_async` 中 `asyncio.run()` 撞上已运行 loop**。

修复策略（`app/workers/tasks/_helpers.py:run_async`）：
```python
def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # already in loop → spin up worker thread
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()
```

生产环境 worker 进程是 sync，永远走 fast path；只有 FastAPI 测试 + eager mode 才走线程隔离。

## 验证结果（12/12 PASS）

```
$ pytest tests/integration/test_api.py -v
TestHealth::test_healthz ......................................... PASSED
TestHealth::test_readyz_all_up ................................... PASSED
TestHealth::test_metrics_exposed ................................. PASSED  ← /metrics 暴露
TestAuth::test_documents_requires_auth ........................... PASSED  ← 401
TestAuth::test_invalid_token_rejected ............................ PASSED  ← 401
TestAuth::test_admin_endpoint_blocks_user ........................ PASSED  ← 403
TestDocuments::test_upload_list_get_delete_lifecycle ............. PASSED  ← E2E 上传/列表/详情/删除
TestDocuments::test_upload_dedup_returns_existing ................ PASSED  ← 同 hash → already_exists
TestDocuments::test_cross_user_acl_403 ........................... PASSED  ← bob 看不到/删不掉 alice 私文
TestChat::test_chitchat_sse ...................................... PASSED  (Vertex live)
TestDlqAdmin::test_dlq_get_404_when_missing ...................... PASSED
TestDlqAdmin::test_dlq_listing_includes_seeded ................... PASSED

12 passed in 70s
```

全套回归 `pytest tests/` → **95 passed in 259s**。

DoD 核对：
- [x] 所有业务路由 401 无 token / 403 无权限 / 200 通过
- [x] 上传 → 列表 → 详情 → 删除 端到端通；cascade 后 Milvus 0 chunks + MinIO 0 objects + 列表无残留
- [x] 同 hash 重传返回 `already_exists`（不再起 ingest）
- [x] 跨用户 ACL：bob 看不到/删不掉 alice 的私文（403）
- [x] SSE 在 `text/event-stream` 下逐 token 流式
- [x] `/metrics` 暴露 Prometheus 文本
- [x] DLQ 列表/详情/重试三接口可用
- [x] `/debug/*` 仅 dev 环境注册（生产 build 不可达）

## 注意 / 后续 layer

- **TestClient 的 `iter_text()` vs 真实 SSE**：测试中拿到完整文本断言事件类型，生产前端用 `fetch + ReadableStream` 逐块读取。`X-Accel-Buffering: no` 头让 Nginx 不缓冲
- **token 用量预检在 chat 入口**：超阈值直接 429，避免起 LLM 后再失败
- **session 历史装载**：当前 chat handler 自己读 Redis；Layer 13 前端会带 `session_id`，handler 已经处理
- **Layer 11 评估 API 与 admin 路由共用** `require_admin` 装饰器
- **限流策略**：本期只做 token 总量限流；Layer 15 加 `slowapi` 对 IP / session 做 RPS 控制
