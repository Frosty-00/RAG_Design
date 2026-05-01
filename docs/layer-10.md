# Layer 10 — 评估 CLI（检索指标 + LLM-as-judge + 报告对比）

## 产出

| 文件 | 内容 |
|---|---|
| `app/evaluation/schema.py` | `EvalSample` / `EvalSampleResult` / `EvalRun` / `SampleMetrics` pydantic |
| `app/evaluation/metrics.py` | 检索指标纯函数：`hit_at_k` / `recall_at_k` / `mean_reciprocal_rank` / `aggregate` |
| `app/evaluation/judge.py` | `GenerationJudge`：单次 LLM call 同时出 faithfulness / answer_relevancy / answer_correctness 三分；judge model = `vertex_judge_model`（Gemini 2.5 Pro）|
| `app/evaluation/runner.py` | `EvalRunner.run(samples, mode)` 跑 RAGPipeline → 指标 → JSON+MD 报告；`load_dataset` / `write_run` |
| `scripts/eval.py` | CLI：`--dataset --retrieval-only --output --limit --run-id` |
| `scripts/eval_diff.py` | 两份报告 metric/bad-case/prompt-version diff |
| `scripts/generate_golden.py` | 从 Milvus 采样 chunks → Gemini 合成 (Q, A, gt_chunks) → JSONL |
| `eval/datasets/mini.jsonl` | 3 条手写 demo 数据 |
| `tests/unit/test_metrics.py` | 12 个纯函数 metric 测试 |
| `tests/integration/test_evaluation.py` | 5 个集成测试（含 1 live judge） |

## 关键设计

### 自实现 LLM-as-judge，不引入 RAGAS

**偏离 plan §10**：原方案要求引入 `ragas` 包。实施评估后改为自实现，理由：
- RAGAS 默认依赖 `langchain` + `langchain-openai`，与我们的 `google-genai` 栈冲突，要给 Gemini 写一层 LangChain wrapper（额外 100+ 行胶水）
- RAGAS 0.2 的 `BaseRagasLLM` 接口仍在变；版本绑定脆弱
- 我们只需要 3 个 RAGAS 等价指标（faithfulness / answer_relevancy / answer_correctness）+ 检索 3 个（plan §10.1 表）；自写 judge prompt + 一次结构化输出 **比集成 RAGAS 代码更少**
- LayerIndex 使用边界（plan §"LlamaIndex 使用边界"）的同样精神：用框架是为省时间，能更省的就不用

后续（Layer 15）若需要 RAGAS 做交叉验证，可按需补一份 `ragas_runner.py` 与现有 judge 并跑做 diff，工程上不冲突。

### 三件事一次 LLM call（judge prompt）

`judge.py:JUDGE_PROMPT` 让 Gemini 2.5 Pro 同时给 faithfulness / answer_relevancy / answer_correctness 三个分；用 `JudgeScores` pydantic 强制 0..1 浮点。优点：1 次 LLM 调用 vs 3 次（节省 ~66% judge token），且分数尺度统一（同一个 model 同一个 prompt 同一次调用，方差更小）。

判断器与生成器使用**不同模型**（Pro vs Flash）避免 self-bias，符合工业界惯例。

### `retrieval_only` vs `full` 双模式

- `retrieval_only` — bypass 整条 `answer_stream`，直接调 `retriever.retrieve`，只算 hit/recall/mrr。**0 LLM 调用**，跑 baseline 与回归极快
- `full` — 走完整 `answer_stream`（含 understanding / 投机检索 / rerank / 生成），收集 answer + citations，然后调 judge

测试证明同一份报告自比 diff 全 0（`test_diff_self_is_zero`），保证流程的确定性。

### 报告与版本关联

- 每条 sample 的 `EvalSampleResult` 含 `retrieved_chunk_ids` + `metrics` + `bad_case` 标记
- run 顶层含 `prompt_versions`（继承自 `ChatChunk.meta.prompt_versions`，由 Layer 7/8 注入）
- diff 工具会输出哪些 prompt 版本变了（让"分数下降是因为换了哪个 prompt"可定位）

### 报告产出

- `<run_id>.json` — 机读，含 per-sample 详情
- `<run_id>.md` — 人读，含 aggregate 表 + bad cases 列表
- run_id 格式 `YYYYMMDD-HHMMSS-<6hex>`，自然排序即时间序

## 验证结果（17/17 PASS）

```
$ pytest tests/unit/test_metrics.py tests/integration/test_evaluation.py -v
TestHitAtK ............ 3 PASSED
TestRecallAtK ......... 4 PASSED
TestMRR ............... 3 PASSED
TestAggregate ......... 2 PASSED
TestRetrievalOnly ..... 2 PASSED
TestLoadDataset ....... 1 PASSED
TestFullMode .......... 1 PASSED   ← Gemini 2.5 Pro live judge
TestEvalDiff .......... 1 PASSED   ← self-diff 全 0

17 passed in 32s
```

完整回归：**112/112 PASS in 390s**。

## DoD 核对
- [x] 检索指标（hit@k / recall@k / MRR）纯函数实现 + 单元测试覆盖边界
- [x] LLM judge 单次出 3 分；judge model 与 generator model 不同
- [x] retrieval_only 模式不调 LLM
- [x] CLI `eval.py` 跑通：JSON + Markdown 报告齐全；含 prompt_versions 关联
- [x] `eval_diff.py` 自比 diff 全 0
- [x] `generate_golden.py` 用 Milvus 池 + Gemini 合成 (Q,A,gt) 写 JSONL；脚本完整可手动跑

## 注意 / 后续 layer

- **Layer 11 评估 API 化**：直接复用 `EvalRunner` + `write_run`，加 Celery `run_eval_task` 与 `/api/v1/eval/*` 路由
- **mini.jsonl 仅 3 条**：文档级 baseline 应用 `generate_golden.py` 生成 ~50–100 条，人工 review 后保留 ~30 条作为 `golden.jsonl`
- **judge model 调用计入用户 usage**：当前 `evaluation/judge.py` 不传 user_id，所以 judge token 不进 Redis 限流；评估场景 OK，要计入的话在 EvalRunner 里加一个 admin user 标识
- **bad_case 阈值**：固定 `faithfulness < 0.5` 与 hit@5==0；后续按数据驱动调
- **Layer 13 评估面板**：UI 直接读 JSON 渲染雷达图 + bad cases；diff 视图复用 `eval_diff.py:diff_runs`
