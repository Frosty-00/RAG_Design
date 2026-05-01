# Layer 12 — 前端骨架（Vite + React 18 + Tailwind + shadcn）

## 产出

| 文件 | 内容 |
|---|---|
| `frontend/package.json` | deps: react 18, react-router 6, @tanstack/react-query, zustand, tailwind, @radix-ui/*, lucide-react |
| `frontend/vite.config.ts` | dev proxy: `/api` `/healthz` `/readyz` → `localhost:8000`；alias `@/*` → `src/*` |
| `frontend/tsconfig.json` | strict TS + 路径别名 + bundler resolution |
| `frontend/tailwind.config.ts` | shadcn 主题 token (`--primary` 等)；container max 1400 |
| `frontend/postcss.config.js` | tailwind + autoprefixer |
| `frontend/src/index.css` | shadcn CSS variables（light + dark） |
| `frontend/src/main.tsx` | StrictMode + QueryClient + BrowserRouter |
| `frontend/src/App.tsx` | Auth gate → Nav + Routes（4 路由，`/debug` 仅 dev 注册） |
| `frontend/src/lib/utils.ts` | `cn()` Tailwind class merger |
| `frontend/src/lib/auth.ts` | bearer token localStorage 存取 |
| `frontend/src/lib/api.ts` | `fetch` 封装：`get/post/del/upload/streamPost`；401 自动清 token + 抛 `ApiError` |
| `frontend/src/lib/sse.ts` | `readSSE(resp)` async iterator：fetch+ReadableStream 解 `event:`/`data:` 块 |
| `frontend/src/lib/types.ts` | 后端 pydantic shape 的前端镜像（DocumentMeta / ChatEvent / EvalRunMeta / ...） |
| `frontend/src/components/ui/{button,input,card}.tsx` | shadcn 风格原子组件（手抄复制式） |
| `frontend/src/components/auth-gate.tsx` | 首屏 token 输入 → localStorage |
| `frontend/src/components/nav.tsx` | 顶栏：4 个 NavLink + 后端就绪小绿点 + 退出按钮 |
| `frontend/src/hooks/use-health.ts` | 10s 轮询 `/readyz` |
| `frontend/src/pages/{chat,documents,eval,debug}.tsx` | Layer 13 占位卡片 |

## 关键设计

### 不用 shadcn CLI，手抄原子组件
`Button` / `Input` / `Card` 直接复制 shadcn 标准实现到 `components/ui/`，不引入 shadcn CLI。理由：
- shadcn CLI 会写一份本地 `components.json` + 拉网络模板，CI/无网环境痛
- 手抄三个原子组件 ~80 行；后续 Layer 13 需要 Dialog/Toast 时按需追加
- 仍保持"组件复制进项目"的 shadcn 哲学（无运行时依赖、可改）

### 类型同步：手维护 + 工具兜底
- `src/lib/types.ts` 手抄后端 pydantic shape（精简 + 注释清晰）
- `package.json` 留 `gen:types` script：`openapi-typescript http://localhost:8000/openapi.json -o src/lib/openapi.ts`
- Layer 15 在 README 钉住"改后端 schema 必跑 `gen:types` 同步前端"

### 401 自动清 token
`api.ts` 中 401 → `clearToken()` + 抛 `ApiError(401)`。AuthGate 监听 token 缺失自动回登录页。这避免"token 过期但 UI 还显示假数据"的诡异状态。

### dev-only `/debug` 路由
```tsx
{import.meta.env.DEV && <Route path="/debug" element={<DebugPage />} />}
```
prod build 中该路由不进 bundle（Vite tree-shake `import.meta.env.DEV` 为 false 后整段 dead-code 消除）。Nav 链接同样在 `import.meta.env.DEV` guard 中。

### 后端就绪指示
Nav 上一个小绿点（`bg-emerald-500` / `bg-rose-500`），由 `useHealth()` 轮 `/readyz` 决定。前端不依赖后端就能起，但视觉上立即知道后端是否在跑。

## 验证

```
$ npm install         → 222 packages, 20s
$ npm run lint        → tsc --noEmit (0 errors)
$ npm run build       → 1638 modules, 1.4s, 235.56 KB JS / 10.50 KB CSS
                        gzipped: 75.88 KB JS / 2.87 KB CSS
$ npm run dev         → Vite 5.4 起在 5173；index.html 含 React refresh + Vite HMR 客户端
```

DoD 核对：
- [x] Vite + React + TS + Tailwind + shadcn 骨架起来
- [x] 4 个路由占位（chat / documents / eval / debug-dev-only）
- [x] dev proxy `/api` → 8000；类型检查 0 error
- [x] AuthGate：未登录显示 token 输入；登录后渲染 Nav + 内容
- [x] `useHealth` 后端状态指示 + 错误恢复
- [x] `lib/api.ts` 含 401 拦截；`lib/sse.ts` 含完整 SSE 解析器
- [x] prod build 体积 < 100 KB gzipped JS

## 注意 / 后续 layer

- **Node 24 LTS**（机器原本无 Node，本期用 winget 装 OpenJS.NodeJS.LTS）；docs/layer-14 启动脚本会写检测逻辑
- **shadcn 组件按需补**：Layer 13 需要 Dialog / Toast / Select 时直接抄 shadcn 文档对应文件到 `components/ui/`
- **Recharts 暂未装**：Layer 13d 评估面板需要雷达/趋势图时再 `npm i recharts`
- **OpenAPI 类型生成**：后端启动时 OpenAPI schema 才暴露在 `/openapi.json`；运行 `npm run gen:types` 前需要后端在跑
