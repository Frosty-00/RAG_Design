# Layer 1 — Python 项目骨架

## 产出
- `pyproject.toml`：setuptools build backend，依赖锁版（fastapi/pydantic/structlog/redis/pymilvus/minio 等），dev extras 含 pytest+ruff
- `app/__init__.py` + `app/core/__init__.py`：包标记
- `app/core/config.py`：`Settings` (pydantic-settings)，单一配置入口，含 `redis_url` / `is_dev` 派生属性
- `app/core/logger.py`：structlog 配置，`request_id` 用 contextvars 透传到所有 log
- `app/core/health.py`：`/readyz` 用的三个 probe（Redis ping / Milvus 9091 healthz / MinIO 9000 health/live），并行 `asyncio.gather`
- `app/main.py`：FastAPI app，`x-request-id` 中间件、`/healthz` `/readyz` 两端点、lifespan 占位

## 环境
- conda 环境：**`self_RAG_2`** (Python 3.11.15)
- 安装：`pip install -e ".[dev]"`（在该环境内）
- 运行：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`

## 验证结果

```
$ curl http://127.0.0.1:8000/healthz
{"status":"ok"}                                 # HTTP 200

$ curl http://127.0.0.1:8000/readyz             # 依赖全在
{"status":"ok","components":{"redis":true,"milvus":true,"minio":true}}
                                                # HTTP 200

$ docker compose stop redis
$ curl http://127.0.0.1:8000/readyz             # redis 挂了
{"status":"degraded","components":{"redis":false,"milvus":true,"minio":true},
 "failing":[{"component":"redis","detail":"Timeout connecting to server"}]}
                                                # HTTP 503

$ docker compose start redis
$ curl http://127.0.0.1:8000/readyz             # 恢复
{"status":"ok","components":{"redis":true,"milvus":true,"minio":true}}
                                                # HTTP 200
```

## DoD 满足
- [x] `/healthz` 不依赖外部服务，永远 200（liveness 语义）
- [x] `/readyz` 任一依赖挂掉返回 503，body 中 `failing` 列表明确标出问题组件与原因
- [x] 三个 probe 并行执行（gather），单个超时不阻塞其它
- [x] structlog 输出结构化日志；`x-request-id` 由中间件分配并透传

## 注意 / 后续 layer 关注
- 使用 conda 环境 `self_RAG_2`，**所有运行命令必须经由 `D:\Anaconda\envs\self_RAG_2\python.exe`** 或 `conda activate self_RAG_2`；本期不使用 uv
- `Settings` 当前默认值即可在本地开发跑通；生产部署前必改 `API_TOKEN_SECRET`、MinIO 凭据、Vertex 项目
- Layer 2 将拓展 readyz：补上 collection 是否存在、桶是否存在的检查
