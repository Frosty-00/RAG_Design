# Layer 7 — LLM + Prompt 管理 + Query Understanding

## 产出

| 文件 | 内容 |
|---|---|
| `app/services/llm.py` | `VertexGeminiClient` 单例：`generate / stream / generate_structured`，自动 token 用量上报 Redis |
| `app/services/prompts.py` | `PromptManager` 单例：扫 `app/prompts/*.yaml`，按 `name → version` 索引；`render(prompt_name, version=..., **vars)` 返回 `RenderedPrompt(text, name, version)` |
| `app/prompts/chat_rag.v1.yaml` | 主 RAG 答题 prompt（含 `[n]` 引用约束） |
| `app/prompts/chat_chitchat.v1.yaml` | 闲聊 fallback prompt |
| `app/prompts/query_understanding.v1.yaml` | 三合一 understanding prompt（structured JSON 输出） |
| `app/services/query_understanding/schema.py` | `UnderstandingResult` pydantic（intent / resolved_query / rewrites） |
| `app/services/query_understanding/pipeline.py` | `QueryUnderstandingPipeline.run(query, history, *, enable_multi_query)`，含关键词降级 + LLM 失败 fallback |

## 关键设计

### LLM 客户端边界
- 直接用 `google-genai` 原生 SDK（Vertex backend），**不**包装 LlamaIndex `LLM`
- 三个核心方法对外暴露：`generate`（非流）/ `stream`（流式）/ `generate_structured`（pydantic schema 强制）
- token 用量在每次成功调用后写 Redis（`incr_usage`），可选 `user_id` / `session_id` 两级累加
- LlamaIndex `CustomLLM` 适配层延后到 Layer 10（评估模块 `llama_index.core.evaluation` 真要 LlamaIndex `LLM` 时再加）

### Prompt 版本化（`{prompt_name}.v{N}.yaml`）
- 文件名模式只是约定；真正生效的是 YAML 内的 `name + version` 字段
- 同名多版本共存；`get(name, "latest")` 返回最高版本
- 渲染输出含 `(name, version)` 元数据，Layer 10 评估报告记录 `prompt_versions: {chat_rag: 1, ...}` 关联实验
- API 第一个参数特意叫 `prompt_name`（不是 `name`），避免与 template 中的 `{name}` 变量冲突

### Query Understanding：三件事一次 LLM 调用
- prompt 里通过 `enable_multi_query` 字符串开关让模型条件输出 `rewrites`
- structured output 用 `UnderstandingResult` pydantic 直接 schema-force
- **降级 1（关键词 chitchat）**：`你好/hi/thanks/再见…` 等，长度 ≤ 8 → 直接返回 `intent=chitchat`，不调 LLM（< 5ms）
- **降级 2（LLM 失败）**：异常时 fallback 为 `intent=needs_retrieval, rewrites=[]`，让主链不挂
- **后置防御**：即使 prompt 让模型在闲聊时返 rewrites，pipeline 也会强制清空；`feature_multi_query=False` 时也会清空

## 验证结果（22 新增 / 78 累计 PASS）

```
$ pytest tests/unit/test_prompts.py tests/integration/test_llm.py tests/integration/test_query_understanding.py -v
TestPromptManager::test_loads_all_yaml_files .................. PASSED
TestPromptManager::test_get_latest_returns_highest_version .... PASSED
TestPromptManager::test_get_specific_version .................. PASSED
TestPromptManager::test_render_substitutes_vars ............... PASSED
TestPromptManager::test_missing_variable_raises ............... PASSED
TestPromptManager::test_unknown_name_raises ................... PASSED
TestPromptManager::test_unknown_version_raises ................ PASSED
TestPromptManager::test_default_root_loads_real_prompts ....... PASSED
TestGenerate::test_simple_generate ............................ PASSED  (Vertex live)
TestGenerate::test_streaming_yields_chunks .................... PASSED  (Vertex live)
TestStructuredOutput::test_structured_returns_valid_pydantic .. PASSED  (Vertex live)
TestKeywordFastPath::test_chitchat_keywords[你好/hi/...] ...... PASSED ×6
TestKeywordFastPath::test_long_query_not_keyword_match ........ PASSED  (Vertex live)
TestLiveUnderstanding::test_factual_question_intent ........... PASSED  (Vertex live)
TestLiveUnderstanding::test_coreference_resolved_with_history . PASSED  (Vertex live)
TestLiveUnderstanding::test_multi_query_off_no_rewrites ....... PASSED  (Vertex live)
TestLiveUnderstanding::test_multi_query_on_yields_rewrites .... PASSED  (Vertex live)

22 passed
```

完整回归 `pytest tests/` → **78 passed in 181s**。

DoD 核对：
- [x] Vertex Gemini 流式 + 非流式 + structured 三种调用全通
- [x] structured output 解析失败有 fallback（json.loads + pydantic 重建）
- [x] PromptManager 启动注册成功；缺 var 抛 `MissingPromptVariable`；同名多版本共存
- [x] Query Understanding 三件事一次 LLM call；coref + intent + multi-query 都正确
- [x] 关键词命中 chitchat 不调 LLM（实测 < 5ms 完成）
- [x] Multi-Query feature flag：默认关，开启时模型返 ≥1 条改写
- [x] LLM 失败时降级为 needs_retrieval

## 注意 / 后续 layer

- **token 用量统计已埋点**：`generate / stream / generate_structured` 全部在 Redis `usage:user:* / usage:session:*` 累加；Layer 9 暴露为 Prometheus `llm_tokens_total` 并加 429 限流
- **LlamaIndex CustomLLM adapter 留到 Layer 10**：当且仅当 `ragas` 或 `llama_index.core.evaluation` 真需要 LlamaIndex `LLM` 接口时才加，避免给业务路径引入冗余抽象
- **`generate_structured` 的兜底解析**：SDK `response.parsed` 偶发为 `None` 时手工 `json.loads + pydantic.model_validate`，已覆盖
- **Vertex 凭据由 `.env` 提供**：`VERTEX_PROJECT` / `VERTEX_LOCATION` / `GOOGLE_APPLICATION_CREDENTIALS`；空值时 LLM live 测试自动 skip
