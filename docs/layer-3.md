# Layer 3 — 模型层（Embedding + Reranker）

## 产出

| 文件 | 内容 |
|---|---|
| `app/services/embedding.py` | `BGEM3Embedder` 单例：dense (1024-dim) + sparse (`dict[int, float]`)，一次推理双输出，Milvus 兼容 |
| `app/services/reranker.py` | `BGEReranker` 单例：bge-reranker-v2-m3 cross-encoder，`rerank(query, items, k)` 返回 `RerankResult[T]` 列表 |
| `tests/integration/test_models.py` | 11 个集成测试 |

## 关键设计

- **单例 + 双锁初始化**：模型权重 ~2.5 GB / 加载 30s，进程内只加载一次
- **不继承 LlamaIndex `BaseEmbedding`**：BaseEmbedding 契约只能输出 dense；我们直接暴露 `encode(...) → {dense, sparse}` 同步两种向量。Layer 4 在 IngestionPipeline 中通过 `add(nodes, embeddings=...)` 直接传入向量，无需 `BaseEmbedding` 接口适配
- **Reranker 泛型 `T`**：`rerank(query, items, text_of=...)` 接受任意结构（str / Node / Chunk），由 `text_of` 提取要打分的文本 → 上层不必先把 chunk 摊平成 str

## 验证结果（11/11 PASS）

```
$ pytest tests/integration/test_models.py -v -s
TestBGEM3Embedder::test_dense_shape ............. PASSED   ← shape=(N, 1024)
TestBGEM3Embedder::test_sparse_shape_and_types .. PASSED   ← dict[int, float] 非空
TestBGEM3Embedder::test_zh_en_mixed ............. PASSED   ← 中英文混合
TestBGEM3Embedder::test_encode_query_helper ..... PASSED
TestBGEM3Embedder::test_singleton_returns_same_instance PASSED
TestBGEM3Embedder::test_throughput_smoke ........ PASSED   ← 50 docs / 0.66s = 76 docs/s
TestBGEReranker::test_relevant_outranks_irrelevant PASSED  ← pizza < ML 文档
TestBGEReranker::test_chinese_relevance ......... PASSED   ← 公司年假 > 天气
TestBGEReranker::test_k_clipping ................ PASSED
TestBGEReranker::test_empty_input ............... PASSED
TestBGEReranker::test_score_normalized .......... PASSED   ← scores ∈ [0, 1]

11 passed in 64.33s
```

性能（CPU torch，AMD 笔记本）：
- BGE-M3 加载（首次下载 + 初始化）：~34 s
- Reranker 加载（首次下载 + 初始化）：~20 s
- BGE-M3 编码吞吐：**76 docs/s**（50 短文档 / 0.66 s），夹带 dense + sparse
- 单 query encode：< 100 ms

## DoD 满足
- [x] dense.shape == (N, 1024)
- [x] sparse `dict[int, float]` 与 Milvus 兼容（int key、float val）
- [x] 中英文混合可推理
- [x] reranker 区分相关/不相关：top1 必为相关文档，分数严格 > 不相关
- [x] 模型走本地缓存（`./.model_cache`，已加 .gitignore）
- [x] 首次冷启动 < 30 s（实测 BGE-M3: 34 s 含下载 28s）
- [x] 100 条 batch 吞吐记录入文档（76 docs/s）

## 注意 / 后续 layer

- **Windows 缺符号链接权限**：huggingface_hub 警告"caching files in degraded mode"。不影响功能，仅磁盘多用一份。要消除可启 Developer Mode 或以管理员跑
- **`hf_xet` 优化未启用**：日志提示装 `huggingface_hub[hf_xet]` 可加速下载。不必装（一次性）
- **Layer 4 用法预告**：`IngestionPipeline` 不会用 LlamaIndex 的 `embed_model` 字段；改在 transformations 末尾插一个自定义 `EmbedTransformation` 直接调 `BGEM3Embedder.encode(texts)` 写入 `node.embedding` 与 `node.metadata["sparse"]`
- **Layer 6 用法预告**：`Retriever.embed_query()` 直接调 `BGEM3Embedder.get().encode_query(query)`
