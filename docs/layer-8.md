# Layer 8 — RAG 编排（SSE 流 + 投机检索 + 引用 + ACL 透传）

## 产出

| 文件 | 内容 |
|---|---|
| `app/services/rag.py` | `RAGPipeline.answer_stream(query, history, requester, session_id)` 异步生成器，发出 `ChatChunk` 序列；含投机检索、chitchat 短路、引用回填 |
| `tests/integration/test_rag.py` | 5 个 live 集成测试（VERTEX_PROJECT 缺时自动 skip） |

## SSE 事件序列（保证不变量）

| 路径 | 事件序列 |
|---|---|
| **chitchat** | `ack(accepted)` → `ack(generating)` → `token×N` → `citations(empty, meta.path=chitchat)` |
| **RAG (有 chunk)** | `ack(accepted)` → `ack(retrieving)` → `ack(generating)` → `token×N` → `citations(N, meta.path=rag)` |
| **RAG (空 chunk)** | `ack(accepted)` → `ack(retrieving)` → `ack(generating)` → `token("未在知识库中找到相关内容。")` → `citations(empty, meta.path=rag_empty)` |
| **错误** | `ack(accepted)` → `error(...)` |

`citations.meta` 含：`path`、`speculative_used`、`elapsed_ms`、`prompt_versions`（评估关联用）、`understanding`（debug）。

## 投机检索（Layer 6.7 §A）

```
                   ┌── understanding (~250ms) ──┐
ack(accepted)  ──┤                                ├── compare orig vs resolved
                   └── retrieve(orig query) ─────┘
                                                    │
            speculative_used → reuse this result    │
            否则 → cancel + 重新 retrieve(resolved + multi)
```

判定 `_is_speculative_reusable`：
- 无 multi-query rewrites
- `original.lower().strip() == resolved.lower().strip()`

简单字符串相等代替 cosine 相似度——计算便宜，false negative（不必要的重检索）成本只是多发一次 hybrid_search，远低于阈值判错的体感。

## ACL 透传

`Requester` 一路透传：API → RAGPipeline → Retriever → Milvus expr。Layer 6 已经在 ACL filter 里实施；Layer 8 只是不去拦截或重写它。`test_acl_filters_private_doc` 验证 bob 看不到 hr-only 的 doc-private 内容。

## 关键修复 / 工程细节

1. **取消投机任务**：用 `contextlib.suppress(CancelledError, Exception)` 静默吞，避免 unhandled task exception 污染日志
2. **空召回用本地 token 而非调 LLM**：直接 yield `"未在知识库中找到相关内容。"` token，省一次 LLM 调用 + 杜绝幻觉
3. **fixture teardown 异常吞噬**：`asyncio.run(pipeline.aclose())` 跨 loop 关 redis async 连接会触发 "Event loop is closed"——GC 会兜底真实清理，try/except 抑制噪音
4. **Token 用量自动记录**：LLM stream 时 `user_id` / `session_id` 透传，每次 LLM 调用结束写 Redis（Layer 9 做限流）

## 验证结果（5/5 PASS）

```
$ pytest tests/integration/test_rag.py -v
TestChitchatPath::test_keyword_chitchat_skips_retrieval ........ PASSED
   ↳ "你好" → 无 retrieving 事件，仅 generating + token + 空 citations
TestRetrievalPath::test_full_event_sequence .................... PASSED
   ↳ 完整序列；citations 至少含 doc-rag；indices 1..N
TestRetrievalPath::test_answer_mentions_retrieved_content ...... PASSED
   ↳ 答案中出现 "embedding/vector/nearest"
TestRetrievalPath::test_acl_filters_private_doc ................ PASSED
   ↳ bob 不在 hr 组 → citations 中无 doc-private
TestEmptyRetrieval::test_anonymous_unrelated_query_falls_back .. PASSED
   ↳ anon 问无关问题 → tokens 非空，citations 中无 doc-private

5 passed in 172s (含模型加载)
```

全套回归 `pytest tests/` → **83 passed in 258s**。

DoD 核对：
- [x] 两条路径（chitchat / RAG）都通
- [x] 答案中 `[1][2]` 引用编号映射正确（Citation.index 从 1 起，连续）
- [x] history 拼接正确（_format_history 复用 Layer 7 helper）
- [x] 空召回返回兜底文本而非 LLM 幻觉
- [x] SSE 序列正确：accepted → (retrieving) → generating → token... → citations
- [x] ACL 全链路过滤生效
- [x] meta 含 prompt_versions / speculative_used / elapsed_ms

## 注意 / 后续 layer

- **冷路径 TTFT 实测**：当前测试关注**正确性**而非数字；冷路径首 token ~3-5s（含 understanding LLM call ~3s + retrieval ~50ms + LLM TTFT ~600ms）。关键词 chitchat 路径只 ~600ms（去 understanding）。Layer 9 做完后用 `curl -N` 跑实际 TTFT 测量
- **Multi-Query 默认关**：`settings.feature_multi_query=False`，understanding 即使开了，pipeline 内 `_do_retrieval` 也只用 `resolved_query`
- **session_id 已透传**：但 history 装载（Redis 取最近 N 轮）由 Layer 9 handler 负责，pipeline 只接收已就位的 history
- **错误事件未在测试覆盖**：测试假设 LLM/检索都成功；error 路径在生产环境通过日志报警，本期不强测
