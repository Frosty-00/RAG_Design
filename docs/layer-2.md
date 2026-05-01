# Layer 2 — Repositories（数据访问层）

## 产出

| 文件 | 内容 |
|---|---|
| `app/repositories/milvus.py` | `MilvusRepository` + `Chunk` + `Requester` + `_build_acl_expr`；schema/索引/CRUD/hybrid_search/版本化/cascade delete |
| `app/repositories/redis_repo.py` | `RedisRepository`：task / session / token / usage / DLQ / idempotency 全部命名空间隔离 |
| `app/repositories/minio_repo.py` | `MinioRepository`：put/get/stat/delete + `delete_prefix` 用于 cascade |
| `tests/conftest.py` | 测试 fixture（独立 collection / bucket / 自动清理 redis 测试键） |
| `tests/integration/test_repositories.py` | 14 个集成测试 |

## Schema 决策（与 plan §4 的差异）

| 字段 | plan §4 | 实施 | 原因 |
|---|---|---|---|
| primary key | `id INT64 auto_id` + `chunk_id VARCHAR` | **`chunk_id VARCHAR(128)` 作 primary** | upsert 必须按主键；`chunk_id` 由 app 生成（`{doc_id}:v{N}:c{i}`），稳定可控，让"标 v1 为 is_latest=false"通过 upsert 实现，无需先 delete 旧 id |

其余字段 100% 与 plan 对齐：`doc_id` / `doc_version` / `is_latest` / `text` / `owner_id` / `acl(JSON)` / `dense(1024)` / `sparse` / `metadata(JSON)`。

索引：`dense` HNSW (M=16, efConstruction=200, COSINE) / `sparse` SPARSE_INVERTED_INDEX (IP)。

## ACL 过滤表达式

`_build_acl_expr(requester) → str` 生成 Milvus filter expr：

- `requester=None`（匿名）：`acl["public"] == true and is_latest == true`
- 普通用户：`(public OR owner_id==uid OR json_contains(acl["users"], uid) OR json_contains_any(acl["groups"], [...])) and is_latest == true`
- admin：仅 `is_latest == true`，绕开 ACL

`hybrid_search()` 把这个 expr 同时注入 dense 和 sparse 两路 `AnnSearchRequest.expr`，`extra_filter`（可选）再 AND 上去。

## 版本化语义

- `insert(chunks_v_new)` 写入 v_new（is_latest=true）
- `mark_old_versions_inactive(doc_id, keep_version=v_new)` 把同 doc_id 的旧 chunks `is_latest` 翻成 `false`
- 检索默认带 `is_latest == true`，老版本不会出现在结果里
- `delete_by_doc(doc_id)` 级联删全部版本

## DoD 验证（14/14 PASS）

```
$ pytest tests/integration/test_repositories.py -v
test_ensure_collection_idempotent ............ PASSED
test_insert_and_count ........................ PASSED
test_acl_filter_visibility ................... PASSED   ← 4 种 requester 全覆盖
test_doc_versioning .......................... PASSED   ← v1→v2 后 v1 is_latest=false
test_delete_by_doc_cascades_all_versions ..... PASSED   ← 删 v1+v2 共 2 条
test_ping (Redis) ............................ PASSED
test_task_set_get_delete ..................... PASSED
test_session_history ......................... PASSED
test_token_lifecycle ......................... PASSED   ← store / lookup / revoke
test_usage_counters .......................... PASSED   ← user/session/daily 都累
test_idempotency ............................. PASSED   ← SETNX 抢占 + 释放
test_ensure_bucket_idempotent ................ PASSED
test_put_get_hash_consistency ................ PASSED   ← 1 MiB md5 round-trip
test_delete_prefix_cascade ................... PASSED   ← 3 versions 级联删

14 passed in 36.90s
```

DoD 项目核对：
- [x] Milvus 建库幂等
- [x] 5 条假向量含 owner_id/acl/doc_version/is_latest 字段插入成功
- [x] hybrid_search 带 ACL expr 过滤跨 4 种 requester 全部正确
- [x] 同 doc v2 写入后 v1 自动 is_latest=false
- [x] delete_by_doc 删除含历史版本，count 归零
- [x] Redis：task/session/token/usage 全套
- [x] MinIO：1 MiB 文件 md5 一致；delete_prefix 级联

## 注意 / 后续 layer

- **LlamaIndex MilvusVectorStore 适配器尚未实现**：留到 Layer 4（IngestionPipeline 真正用它时）一起做。pymilvus + LlamaIndex 共用同一 collection（schema 一致即可）
- **`setuptools<81` 锁定**：pymilvus 2.4 仍 import `pkg_resources`，setuptools 81 已移除。已在 `pyproject.toml` 锁
- 检索缓存键里需要 `acl_scope`（requester 权限指纹）；Layer 6 实现时直接哈希 `(user_id, sorted(groups), is_admin)`
