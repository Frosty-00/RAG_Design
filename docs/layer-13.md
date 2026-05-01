# Layer 13 — 前端四页面（chat / documents / eval / debug-dev）

## 产出

| 路径 | 内容 |
|---|---|
| `frontend/src/components/ui/{badge,textarea,dialog,toast,label}.tsx` | 增补 5 个 shadcn 风格原子组件 |
| `frontend/src/components/documents/upload-dropzone.tsx` | 拖拽上传 + 折叠 ACL 设置 |
| `frontend/src/components/documents/document-row.tsx` | 行渲染 + 删除二次确认 |
| `frontend/src/components/chat/{message-list,citation-panel}.tsx` | 流式消息列表 + 引用面板 |
| `frontend/src/components/eval/{start-run-form,diff-view}.tsx` | 启动评估表单 + diff 对话框 |
| `frontend/src/hooks/{use-documents,use-chat-stream,use-eval}.ts` | TanStack Query / SSE 钩子 |
| `frontend/src/pages/{chat,documents,eval,debug}.tsx` | 4 个完整页面（debug 仅 dev） |

## 关键设计

### 13a 文档管理 (`/documents`)
- **拖拽 + 多文件并行上传**：每个文件独立 mutation，结果用 toast 单条反馈
- **状态智能轮询**：列表查询 `refetchInterval` 检查是否有 doc 处于 non-terminal（pending/parsing/embedding…），有则 1.5s，无则 8s
- **删除二次确认**：Dialog 显式列出"将永久删除：向量索引（所有版本） / 对象存储 / 缓存"
- **Badge 状态色**：`done` 绿、`failed` 红、其它 secondary，前端纯靠 status 字符串映射

### 13b 对话 (`/chat`)
- **session_id 本地化**：localStorage 持久；"New chat" 按钮换新 session
- **SSE 流式**：`useChatStream` hook 用 `streamPost` + `readSSE` 读 `event:`/`data:` 块；按 event 类型 reducer 更新 messages 数组
- **进度可见**：assistant 消息渲染 phase badge（accepted / retrieving / generating），生成中尾随光标动画
- **`[N]` 自动转引用按钮**：`renderWithCitations` 用正则切 token 流，`[1]`-`[9]` 替换为可点击徽章 → 滚到右侧引用面板对应条目
- **AbortController + Stop 按钮**：流式中点击红色按钮 abort fetch；用户跳页时也通过 hook unmount cleanup
- **Enter 发送 / Shift+Enter 换行**

### 13c 检索调试 (`/debug`，仅 dev)
- 三参数：`top_k` / `rerank_k` / Multi-Query 开关
- 输出三块：Understanding（intent badge + resolved + rewrites）、Reranked chunks 表
- 路由仅 `import.meta.env.DEV` 注册——**生产构建产物中不含 debug page 代码**（已验证 dist size diff）

### 13d 评估面板 (`/eval`)
- **历史 run 表**：`refetchInterval` 在有 `pending/running` run 时切到 2s
- **多选 diff**：勾选两个 run（最多 2 个，超出时替换最早的）→ "Diff selected" 按钮 → Dialog 内显示 metric delta 表（绿/红着色） + prompt 版本变化 + newly-bad / newly-good 列表
- **Status badge 颜色**：done 绿 / running 黄 / failed 红
- **启动 run 表单**：仅 admin token 可调（后端返 403 → toast）

### 共享基础设施
- **Toast**：自实现（zustand list + 4s 自动消失），不引入 `@radix-ui/react-toast` 多余依赖
- **错误反馈**：`api.ts` 抛 `ApiError`，所有 mutation catch 后 toast；401 自动清 token + AuthGate 接管
- **`/debug` 路由生产消除**：`{import.meta.env.DEV && <Route ...>}` + nav 同样 guard，Vite 打包时该路由代码被 tree-shake

## 验证

```
$ npm run lint     → tsc --noEmit  0 errors
$ npm run build    → 1713 modules, 1.50s
                     dist/index.html               0.42 KB
                     dist/assets/index.css        17.54 KB │ gzip 4.32 KB
                     dist/assets/index.js         301.45 KB │ gzip 96.08 KB
```

页面结构核对（依靠类型 + 路由）：
- [x] `/chat` 流式 + 引用面板
- [x] `/documents` 上传 + 表格 + 删除对话框
- [x] `/eval` 启动表单 + 历史表 + diff 视图
- [x] `/debug` 三参数表单 + understanding + chunks 表，仅 dev 注册
- [x] AuthGate 包裹所有页面；未登录显示 token 输入
- [x] Nav 后端就绪小绿点 + 退出按钮

## 注意 / 后续 layer

- **bundle 96 KB gzip**：可接受。引入 recharts（约 +30 KB gzip）才能上雷达/趋势图——本期用 metric 表代替，Layer 15 视需求再加
- **手动测试缺失**：本期未跑浏览器端 e2e；后端 API 已在 Layer 9/11 通过 TestClient 验证，前端依赖类型对齐 + 编译通过保证集成正确性。Layer 15 可加 Playwright 一两条 happy-path
- **type 同步靠手维护**：`src/lib/types.ts` 与后端 pydantic 镜像。每改后端 schema 必须同步前端；`npm run gen:types` 工具已留好（需要后端在跑导出 openapi.json）
- **session 历史 vs 多会话**：当前 localStorage 只存一个 session_id；Layer 15 可扩展为多会话切换列表，需要后端加 "list sessions for user" API
- **删除进度未细分**：cascade_delete 任务 backend 已三系统分别记录 `milvus / minio / cache` 状态，前端目前只显示 toast；Layer 15 可加进度条订阅 task 状态
