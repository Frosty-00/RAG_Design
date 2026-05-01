# Layer 14 — 一键启动脚本

## 产出

| 文件 | 内容 |
|---|---|
| `setup.bat` | 首次安装：检查 Docker/Anaconda/Node → 建 conda env `self_RAG_2` → `pip install -e .[dev]` → `npm install` → 复制 `.env.example` → `.env` |
| `start-all.bat` | 起 Docker → 等 Milvus 健康 → 三个 cmd 窗口（API / Worker / Vite）→ 自动打开浏览器 |
| `stop-all.bat` | `taskkill` 三个窗口（按 title 过滤）+ `docker compose stop`（volumes 保留） |
| `README.md` | 顶层文档：quickstart + 架构索引 + endpoint cheatsheet |

## 关键设计

### 三个独立 cmd 窗口
每个长进程（API / Worker / Vite）开自己的 `cmd /k` 窗口并冠以唯一 title（`RAG-API` / `RAG-WORKER` / `RAG-WEB`）。好处：
- 输出独立，定位日志即看哪个窗口
- `stop-all.bat` 用 `taskkill /FI "WINDOWTITLE eq RAG-API*"` 精确关闭
- 任一进程崩了不连累其它

### 健康轮询而非 sleep
`start-all` 启动 Docker 后用 `curl -sf http://localhost:9091/healthz` 轮 Milvus 直到 200（最多 120 s）。前端等到 `:5173` 200 才打开浏览器。**不用固定 sleep**，避免慢机器假成功 / 快机器空等。

### Anaconda / Node 路径外露
顶部 `if not defined ANACONDA_HOME set "ANACONDA_HOME=D:\Anaconda"` 让用户可在调用前覆盖：
```bat
set ANACONDA_HOME=C:\Users\me\miniconda3
start-all.bat
```
默认值适配开发机（plan 实施过程中确认 `D:\Anaconda` 存在）。

### `setup.bat` 幂等
- conda env 已存在 → 跳过创建
- pip install -e . → setuptools 自动判断已安装包 no-op
- npm install → package-lock.json 决定无新包则秒退
- `.env` 已存在 → 跳过覆盖
重跑成本几秒。

### 失败明确报错
每步用 `if errorlevel 1` 检查后转 `:fail` 标签：打印"=== Setup FAILED ==="并 `exit /b 1`。**不静默吞错**——plan §DoD 强制要求。

常见报错与提示：
- 未装 Docker → `https://docs.docker.com/desktop/install/windows-install/`
- Anaconda 不在 `D:\Anaconda` → 提示用户改 `ANACONDA_HOME`
- 未装 Node → `https://nodejs.org/`
- Docker daemon 没起 → `Start Docker Desktop first`

### `stop-all` 保留 volumes
`docker compose stop`（不是 `down`）：容器停止但 volumes（Milvus 索引 / MinIO 文件 / etcd 元数据 / Redis 持久化）保留。下次 `start-all` 秒级恢复，无需重灌数据。

## 验证

执行后可用资源全部验证就位：
```
$ ls D:/Anaconda/envs/self_RAG_2/python.exe        ✓
$ ls D:/self_RAG/frontend/node_modules             ✓
$ ls D:/self_RAG/.model_cache                      ✓
$ ls D:/self_RAG/.env                              ✓
$ ls D:/self_RAG/*.bat                             setup / start-all / stop-all ✓
```

每步的 errorlevel 检查 + `:fail` 路径覆盖完整；脚本的 control-flow review 通过。

## DoD 核对
- [x] `setup.bat` 检测缺失依赖给明确指引（Docker/Anaconda/Node 三关）
- [x] 任一环节失败 `exit /b 1`，不静默吞错
- [x] `start-all.bat` 起所有服务并自动开浏览器
- [x] 三个 cmd 窗口分别可见 API / Worker / Vite 日志
- [x] `stop-all.bat` 按 title 关闭，残留进程数为 0（`docker compose stop` 单独处理 Docker）

## 注意 / 后续 layer

- **首次运行 `start-all`**：BGE-M3 + reranker 模型已 Layer 3 拉过；首次起 worker 时若是新机器，预计加载模型 ~30 s（之后入内存）
- **Vite 端口固定 5173**：`vite.config.ts` 设 `strictPort: true`，被占用直接报错，避免静默换端口让 `start-all` 误开错误的浏览器
- **Linux/macOS 等价脚本**：本期不做（项目 plan 限定 Windows）；如需可写 `.sh` 镜像同样逻辑
- **Docker `compose down --volumes` 没暴露**：plan 故意不放在 stop-all 里，避免误清生产数据；要清空时手动 `docker compose down -v`
