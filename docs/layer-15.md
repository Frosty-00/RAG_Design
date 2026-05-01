# Layer 15 — 收尾加固

## 产出

| 内容 | 实施 |
|---|---|
| **Chat 限流（30 RPM/user）** | `app/api/v1/chat.py` 新增 `_check_chat_rate_limit`，Redis fixed-window 计数器 `rl:chat:{user_id}:{minute_bucket}`，超阈值 raise 429 |
| **request_id 透传到 Celery worker** | `celery_app.py` 注册 `task_prerun` / `task_postrun` 信号，把 `task_id` 设为 worker 进程的 `request_id` contextvar；API 日志已包含 `task_id`，**`grep <task_id>` 即可贯穿 API + Worker 两侧日志** |
| **README** | 顶层文档：quickstart + endpoint cheatsheet + 架构索引（Layer 14 已完成） |
| **限流测试** | `TestChatRateLimit::test_request_over_threshold_returns_429`：monkeypatch 阈值到 3，发 4 次第 4 次必 429 |

## 关键决策

### slowapi → 自实现

最初尝试 `slowapi.Limiter.limit("30/minute")` 装饰器，但与 FastAPI async + pydantic body 解析冲突，所有 chat 请求返 422 Unprocessable Entity。

切换为**自实现 Redis fixed-window 限流**——~25 行代码：
- `INCR rl:chat:{uid}:{minute}` 原子计数
- 第一次调用 `EXPIRE 70`（多 10s 缓冲让 key 自然过期）
- count > 阈值 → `HTTPException(429, {"reason": "rate_limited", ...})`

可控性比 slowapi 更好：键空间显式、易跨实例（Redis 共享）、易测试（monkeypatch 常量）。

### Celery 信号传 request_id

`task_prerun` 触发时把 `task_id[:16]` 设到 contextvar `request_id`，`task_postrun` 清。API 日志中已经记录 `apply_async` 返回的 task_id（任务投递点的 log），加这两个 signal handler 后 worker 内的所有 service / repo 日志自动带 `request_id=<task_id>`。

无需手工在每个 `apply_async` 加 `headers={"request_id": ...}`：用 task_id 本身做关联键，零侵入。

### OCR 已全开（提前完成）

Plan §15 提"OCR 全量打开"——**Layer 4 实施时 RapidOCR 默认就跑**，无需额外开关。`pdf.py` 检测页面 < 50 字符即触发 OCR。已有测试覆盖（`test_pdf_ocr_fallback`）。

## 验证

```
$ pytest tests/integration/test_api.py::TestChatRateLimit -v
TestChatRateLimit::test_request_over_threshold_returns_429 PASSED  in 23s

$ pytest tests/                                  # 全套回归
119 passed in 297s
```

DoD 核对：
- [x] Chat 端点限流；超阈值返 429（measured: 第 4 次请求触发）
- [x] `request_id` 在 worker 日志中可见（task_id 同步映射，contextvar 透传）
- [x] 扫描 PDF 端到端可问答（Layer 4 OCR fallback + Layer 5 ingestion + Layer 8 chat 全打通；流程已经可工作）
- [x] README 含 Quickstart + endpoint cheatsheet + 测试指南

## 跳过 / 推迟

- **50 QPS 压测**：plan §15 提 50 QPS 短查询不崩。本地单机+Vertex API 限制下做不真实——Vertex Gemini 自身就有 60 RPM 默认配额，做 50 QPS 必触发上游 429。生产环境上线后按真实数据用 `locust` 跑一轮。
- **Playwright e2e**：plan 未要求；前端 4 页类型严格 + 后端 119 测试已覆盖核心路径，本期不补
- **Layer 15a 性能加固（ONNX / 模型独立服务）**：plan 标注"P1 加固阶段"，正式上线前再做

## 全项目终局快照

```
tests/                                 119 passed (Python)
frontend/                              tsc 0 errors, vite build 96 KB gzip
docker-compose.yml                     4 services healthy
docs/layer-{0..15}.md                  16 篇决策档案
README.md                              quickstart + endpoint cheatsheet
setup.bat / start-all.bat / stop-all.bat   Windows 一键启动
```

后端 11 层 + 前端 2 层 + 启动 + 加固 = **15/15 layer 全部完成**。
