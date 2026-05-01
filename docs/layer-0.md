# Layer 0 — Docker 基础设施

## 产出
- `docker-compose.yml`：4 个服务（etcd / minio / milvus / redis）+ healthcheck + named volumes + `rag-net` 网络
- `.env.example`：所有运行时配置项模板
- `.gitignore`：排除 `volumes/` / `.env` / `__pycache__` / 模型缓存等

## 镜像版本
| 服务 | 镜像 | 用途 |
|---|---|---|
| etcd | `quay.io/coreos/etcd:v3.5.5` | Milvus 元数据 |
| MinIO | `minio/minio:RELEASE.2024-08-17T01-24-54Z` | Milvus 后端存储 + 业务原文件 |
| Milvus | `milvusdb/milvus:v2.4.15` | 向量库（hybrid search 支持 dense+sparse） |
| Redis | `redis:7-alpine` | Celery broker + 业务缓存 + 会话 |

## 端口
- 19530 / 9091：Milvus gRPC / health
- 9000 / 9001：MinIO API / Console
- 6379：Redis
- 2379-2380：etcd（不暴露到宿主）

## 验证结果（首次 `docker compose up -d` 后 ~30s）

```
$ docker compose ps
rag-etcd     quay.io/coreos/etcd:v3.5.5                 ... Up (healthy)
rag-milvus   milvusdb/milvus:v2.4.15                    ... Up (healthy)
rag-minio    minio/minio:RELEASE.2024-08-17T01-24-54Z   ... Up (healthy)
rag-redis    redis:7-alpine                             ... Up (healthy)

$ curl -sf http://localhost:9091/healthz   → OK
$ curl -sf http://localhost:9000/minio/health/live → 200
$ docker exec rag-redis redis-cli ping     → PONG
$ docker exec rag-etcd etcdctl endpoint health → healthy
```

## DoD 满足
- [x] 四个服务全部 `healthy`
- [x] `docker compose ps` 全 `Up`
- [x] healthcheck 探针配置正确（业务可用 = 容器 healthy）
- [x] volumes 持久化，重启宿主后 `docker compose start` 可恢复（数据卷在 `./volumes/`）

## 注意
- MinIO 目前用默认 `minioadmin/minioadmin`，**生产前必须改 `.env` 中的密钥**
- Milvus 启动 `start_period=90s`，宿主负载高时可能需要更久；以 healthcheck 状态为准
- 所有数据落 `./volumes/`，已加入 `.gitignore`
