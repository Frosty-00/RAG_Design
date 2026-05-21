# self-rag

Enterprise knowledge-base RAG system: **FastAPI + Milvus 2.4 + LlamaIndex + BGE-M3 + bge-reranker-v2-m3 + Vertex Gemini 2.5**, with a **React + TypeScript** frontend and a built-in **evaluation framework** (LLM-as-judge with Gemini 2.5 Pro).

## 📺 Demo

Scan to watch a short walkthrough on any phone (native player on iOS / Android):

<p align="center">
  <a href="https://frosty-00.github.io/RAG_Design/">
    <img src="docs/video.png" alt="Scan to watch the demo video" width="200" />
  </a>
  <br />
  <em>Or open directly:</em>
  <a href="https://frosty-00.github.io/RAG_Design/">frosty-00.github.io/RAG_Design</a>
</p>

## Quickstart (Windows)

**Just one script — `run.bat`.** Double-click it; it handles everything:

| Phase | What `run.bat` does |
|---|---|
| 1 | Verifies Docker / Anaconda / Node, creates conda env `self_RAG_2` and installs Python + frontend deps **on first run only**. Copies `.env.example` → `.env` if missing. |
| 2 | Brings up Docker (milvus / etcd / minio / redis), then launches FastAPI / Celery / Vite as three minimized taskbar windows. |
| 3 | Opens <http://localhost:5173>, then waits in a console for `[S]` (stop everything) or `[R]` (restart only the app processes, keeping Docker up). |

**Closing the `run.bat` console window** does NOT stop the children — press `[S]` for clean shutdown.

First sign-in to the UI: paste your `ADMIN_TOKEN` (default `admin-dev-token`, defined in `.env`).

### Logs

Each app process runs in its own minimized window with a known title — click the
taskbar icon to view live logs:

| Window | Process | What's there |
|---|---|---|
| `RAG-API`    | uvicorn (FastAPI) | structlog → stdout |
| `RAG-WORKER` | Celery worker     | task lifecycle |
| `RAG-WEB`    | Vite dev server   | HMR + build info |

If something looks broken, restore the relevant window to read the error.

## Manual operation

```powershell
# Backend (in conda env self_RAG_2)
docker compose up -d milvus etcd minio redis
python -m uvicorn app.main:app --reload --port 8000
celery -A app.workers.celery_app worker -l info -P solo

# Frontend
cd frontend && npm run dev
```

## Tests

```powershell
pytest tests/                              # 118 tests, ~5 min (model loads + live LLM)
pytest tests/unit                          # fast, pure-compute
pytest -m "not live"                       # skip Vertex calls
```

## Architecture

15-layer build, each layer documented in `docs/layer-N.md`:

| Layer | Topic |
|---|---|
| 0 | Docker compose (milvus / etcd / minio / redis) |
| 1 | FastAPI skeleton + structlog + healthz / readyz |
| 2 | Milvus / Redis / MinIO repositories — schema, ACL, versioning |
| 3 | BGE-M3 (dense + sparse) + bge-reranker-v2-m3 |
| 4 | PDF / DOCX / Markdown / OCR readers + chunking pipeline |
| 5 | Celery ingestion + DLQ + idempotency + cascade delete |
| 6 | Hybrid retrieval + 2-tier cache + ACL scope |
| 7 | Vertex Gemini client + YAML prompt registry + query understanding |
| 8 | RAG orchestration (SSE, speculative retrieval, citations) |
| 9 | REST API + Bearer auth + Prometheus + admin / DLQ |
| 10 | Evaluation CLI (retrieval + LLM-as-judge metrics) |
| 11 | Evaluation REST API (admin only) |
| 12 | Frontend skeleton (Vite + React 18 + Tailwind + shadcn) |
| 13 | Frontend pages (chat / documents / eval / debug-dev) |
| 14 | One-click .bat launchers |
| 15 | Hardening, README, e2e |

Plan & rationale live at `C:\Users\USER\.claude\plans\ai-agent-rag-fastapi-milvus-async-sparrow.md`.

## Endpoint cheatsheet

```
POST   /api/v1/documents          (multipart) upload + queue ingest
GET    /api/v1/documents          list (owner-scoped)
GET    /api/v1/documents/{id}     metadata
DELETE /api/v1/documents/{id}     cascade delete (Celery task)

POST   /api/v1/chat               SSE stream (text/event-stream)
                                    events: ack, token, citations, error

POST   /api/v1/admin/tokens       issue bearer token (admin)
DELETE /api/v1/admin/tokens?token=...
GET    /api/v1/admin/dlq          list DLQ task ids
POST   /api/v1/admin/dlq/{id}/retry

POST   /api/v1/eval/runs          start evaluation (admin)
GET    /api/v1/eval/runs          history list
GET    /api/v1/eval/runs/{id}     run meta + report
POST   /api/v1/eval/diff          {baseline_id, candidate_id}

POST   /api/v1/debug/retrieve     retrieval inspector (dev only)
GET    /metrics                   Prometheus
GET    /healthz, /readyz
```

## Environment

Default conda env: **`self_RAG_2`** (Python 3.11). Anaconda assumed at `D:\Anaconda` — override via `ANACONDA_HOME` env-var before running `setup.bat` / `start-all.bat`.

## License

Internal project. No license attached — adjust before publishing.
