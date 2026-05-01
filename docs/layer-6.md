# Layer 6 — 检索（Hybrid + Rerank + 二级缓存 + ACL scope）

## 产出

| 文件 | 内容 |
|---|---|
| `app/services/retrieval.py` | `Retriever` 类 + `ScoredChunk` / `RetrievalStats` / `RetrievalResult` 数据类型；`retrieve` / `retrieve_multi` / `invalidate_cache_for_doc` |
| `app/repositories/milvus.py` | 新增 `get_by_chunk_ids(ids)` 用于缓存命中时按 PK 回查 |
| `tests/integration/test_retrieval.py` | 13 个集成测试 |

## 关键设计

### 二级缓存（无 QA 全缓存）

| 缓存 | Key | 内容 | TTL |
|---|---|---|---|
| Embedding | `emb:{md5(text)}` | `{dense, sparse}` JSON | 24h（`emb_cache_ttl`） |
| Retrieval | `ret:{md5(payload)}` | `[chunk_id...]` JSON | 30min（`ret_cache_ttl`） |

`payload` = `json.dumps({queries: sorted, top_k, rerank_k, scope, index_v}, sort_keys=True)`

- `scope` = `_acl_scope(requester)`：anon / admin / 或 `md5(user_id|sorted(groups))[:16]`
- `index_v` = `settings.milvus_index_version`，schema 升级时手动 bump → 缓存全废

**没有 QA 全缓存**（plan 决策）：retrieval 缓存只存 chunk_ids，命中时 Milvus 按 PK 回查全字段。这把"用户 query → 答案"这一长链拆开，使缓存粒度可控、ACL 不会跨用户泄漏、prompt 版本变化不污染数据缓存。

### `retrieve_multi` (Multi-Query feature flag)

`asyncio.gather` 并行 N 个 hybrid_search，结果按 `chunk_id` 去重保留 max score，最后用 `rerank_query`（默认 queries[0]）跑一次 reranker。

延迟约等于单次检索（并行），但召回多样化——Layer 8 通过 `settings.feature_multi_query` 开关启用。

### Cache 失效策略

`Retriever.invalidate_cache_for_doc(doc_id)`：检索 cache 是 content-addressed（无法定向到单 doc），删除时全清 `ret:*`。Layer 9 的 `cascade_delete` API 调用此方法；embedding cache 是 content-keyed（`md5(text)`），文档内容不变就不需要清。

### ACL 透传

`Requester` 自 Layer 2 起就是 Milvus 过滤的入参；Retriever 只透传不解释。`_acl_scope(requester)` 函数化，便于 Layer 8 复用做缓存键。

## 验证结果（13/13 PASS）

```
$ pytest tests/integration/test_retrieval.py -v
TestRetrieve::test_top_chunk_is_relevant ........................... PASSED
TestRetrieve::test_acl_filters_results ............................. PASSED   ← bob 看不到 alice 私文
TestRetrieve::test_anon_sees_only_public ........................... PASSED
TestEmbeddingCache::test_second_query_hits_emb_cache ............... PASSED   ← stats.embedding_cache_hits 增长
TestRetrievalCache::test_same_requester_hits_cache ................. PASSED
TestRetrievalCache::test_different_requesters_isolated ............. PASSED   ← acl_scope 进键名
TestRetrievalCache::test_index_version_invalidates ................. PASSED   ← bump version → miss
TestAclScope ××4 ................................................... PASSED
TestRetrieveMulti::test_merges_and_dedupes ......................... PASSED   ← 3 query 合并去重
TestCacheInvalidation::test_invalidate_cache_for_doc_clears_ret_keys PASSED

13 passed in 19.11s
```

全套回归 56/56 PASS（Layer 1-6 累计），约 155 秒。

DoD 核对：
- [x] 三份内容差异显著的文档，对应 query 命中正确 doc
- [x] 第二次相同 query → embedding 缓存 + retrieval 缓存命中
- [x] 不同 `Requester` 的检索缓存互不干扰（acl_scope 进键）
- [x] index_version 翻新 → 缓存命中失效
- [x] retrieve_multi 并行三 query 后去重，top1 正确
- [x] cascade delete 触发 retrieval 缓存清空

## 注意 / 后续 layer

- **`retrieve_with_understanding`（投机检索）**：Layer 8 依赖 `UnderstandingResult` 类型（Layer 7 定义），故未在本层实现；当前 `retrieve_multi` 已具备并行 + 去重能力，Layer 8 直接组合
- **缓存健康指标**：`RetrievalStats` 已埋点命中/失败次数；Layer 9 暴露为 Prometheus `rag_cache_hit_total{tier="emb|ret"}`
- **rerank 跳过策略（P1）**：Layer 15a 才做"top-1/top-5 分差大时跳 rerank"；当前固定 rerank
- **本期不调 Multi-Query**：`feature_multi_query=False` 默认（Layer 8 据 settings 决定调 retrieve / retrieve_multi）
