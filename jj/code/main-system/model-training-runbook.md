# 模型训练运维手册

本文适用于当前单 Worker、单 GPU 的 CPT/SFT 训练架构。所有命令从包含
`docker-compose.yml` 的部署目录执行。不要把数据库口令、模型仓库令牌或 API
密钥写入本文、任务配置或工单；通过部署环境的 Secret 或 `.env` 注入。

## 1. 上线前检查

### 基础模型

在主机上准备完整的 `Qwen/Qwen3.5-9B` 快照，并把管理员选择的目录写入：

```text
TRAINING_BASE_MODEL_HOST_PATH=/srv/models/Qwen3.5-9B
```

该目录以只读方式挂载到 Worker 的 `/models/Qwen3.5-9B`。启动前至少确认
`config.json`、tokenizer/processor 文件和全部 safetensor 分片存在；下载必须在
应用外完成。Worker 配置了 Hugging Face/Transformers 离线模式，不会在任务执行时
访问网络或补下载文件。

```bash
docker compose config
docker compose run --rm --no-deps -T training-worker python - <<'PY'
import json
from pathlib import Path

root = Path("/models/Qwen3.5-9B")
required = [root / "config.json"]
index = root / "model.safetensors.index.json"
if index.is_file():
    weight_map = json.loads(index.read_text(encoding="utf-8")).get("weight_map", {})
    required.extend(root / name for name in sorted(set(weight_map.values())))
else:
    required.append(root / "model.safetensors")
missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
if not any(root.glob("tokenizer*")) or not any(root.glob("*processor*")):
    missing.append("tokenizer/processor files")
if missing:
    raise SystemExit("missing model files: " + ", ".join(missing))
print(f"model snapshot ready: {len(required) - 1} weight file(s)")
PY
```

非零退出码会列出 index 引用但缺失的分片或 tokenizer/processor 文件。不要用空文件
绕过该检查；管理员应从同一模型 revision 重新同步完整快照。

### NVIDIA Container Toolkit

主机必须已安装匹配驱动和 NVIDIA Container Toolkit，且 Docker runtime 能分配 GPU：

```bash
nvidia-smi
docker info | sed -n '/Runtimes/,+2p'
docker compose run --rm --no-deps training-worker \
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
```

当前 Compose 只映射主机 GPU 1。容器内应只看到一张卡，索引为 `0`；不要把容器
索引改回 `1`，也不要为显存不足静默增加多卡并行。

### 启动与健康

```bash
docker compose up -d --build
docker compose ps
docker compose exec -T training-worker python -m app.training_worker.main --check
curl -fsS http://localhost:8080/api/v1/model-training/worker
```

`--check` 只检查数据库、输出目录、模型 marker 和 GPU，不注册 Worker 或领取任务。
其 JSON 结果必须为 `ok: true`。Worker API 应报告 `idle` 或 `busy`、
`model_status: ready`、近期心跳、单卡公开信息和队列长度。容器反复重启时先查看：

```bash
docker compose logs --tail=200 training-worker
docker compose exec -T training-worker env | sed -n '/^\(CUDA\|NVIDIA\|TRAINING_\)/p'
```

环境应保持 `NVIDIA_VISIBLE_DEVICES=1`、`CUDA_VISIBLE_DEVICES=0`，输出目录为
`/training/outputs`，模型目录为 `/models/Qwen3.5-9B`。

如果常驻 Worker 在容器 stop/start 后报告 `Failed to initialize NVML: Unknown Error`，
不要继续提交训练任务。临时 `docker compose run --rm training-worker ... --check`
成功并不能证明常驻容器的设备句柄有效；应重新创建 Worker 以重新挂载 GPU：

```bash
docker compose up -d --force-recreate training-worker
docker compose exec -T training-worker \
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
docker compose exec -T training-worker python -m app.training_worker.main --check
```

三条命令均成功且 `--check` 返回 `ok: true` 后再显式重试失败任务。重建期间的活动任务
应进入 `interrupted`，不会自动重排。

## 2. 任务操作

任务页位于 `http://localhost:8080/#/model-management/training`。API 根路径为
`/api/v1/model-training`。

| 状态 | 含义 | 运维动作 |
| --- | --- | --- |
| `queued` | 已冻结 Test 版本和拆分，等待唯一 Worker | 检查 Worker、模型状态和队列位置 |
| `preparing_data` | 正在重建只读训练输入并做资源预检 | 检查磁盘、模型和 adapter/checkpoint 引用 |
| `training` | CPT/SFT 子进程运行中 | 观察心跳、指标、GPU 和容器日志 |
| `evaluating` | 只使用源 SFT 冻结的 test IDs 评估 | 不要修改源任务或删除 adapter |
| `cancelling` | 已请求取消，等待子进程组退出 | 等待进入终态；不要强删容器或输出目录 |
| `succeeded` | 终态，产物已登记 | 校验 artifact ID 后再下载或备份 |
| `failed` | 终态，错误已持久化 | 查看错误、日志和保留的最新有效 checkpoint |
| `cancelled` | 终态，人工取消完成 | 可用保留 checkpoint 显式重试 |
| `interrupted` | Worker 心跳过期或退出，不自动重排 | 排除根因后由操作员显式重试 |

常用只读检查：

```bash
curl -fsS 'http://localhost:8080/api/v1/model-training/tasks?page=1&page_size=20'
curl -fsS 'http://localhost:8080/api/v1/model-training/tasks/TASK_ID'
curl -fsS 'http://localhost:8080/api/v1/model-training/tasks/TASK_ID/logs?after_line=0'
curl -fsS 'http://localhost:8080/api/v1/model-training/artifacts?task_id=TASK_ID&page_size=100'
```

任务详情返回冻结拆分、时间线、指标和 artifact ID。日志 API 分页读取已清洗的公开日志；
容器日志用于 Worker/子进程故障定位。下载必须使用登记 ID：

```bash
curl -fS -o artifact.bin \
  'http://localhost:8080/api/v1/model-training/artifacts/ARTIFACT_ID/download'
```

不要把数据库中的 `relative_path` 拼接为宿主机路径，也不要直接修改
`training_outputs` 卷。artifact ID 是操作、审计和备份核对的稳定标识。

## 3. 取消、重试与删除

```bash
curl -fS -X POST \
  'http://localhost:8080/api/v1/model-training/tasks/TASK_ID/cancel'
curl -fS -X POST -H 'Content-Type: application/json' \
  -d '{"resume_checkpoint_artifact_id":"CHECKPOINT_ARTIFACT_ID"}' \
  'http://localhost:8080/api/v1/model-training/tasks/TASK_ID/retry'
```

取消是协作式的：先等待任务进入 `cancelled`，再操作容器。Worker 重启期间的活动任务
会进入 `interrupted`，不会自动重排。重试会创建新任务并保留原任务的冻结数据谱系。

仅终态任务可软删除。以下 artifact 受保护并应返回 HTTP 409：

- 被任何 SFT 引用的 CPT final adapter；
- 仍与历史任务 Test 关联的 config、split 和 dataset；
- 与活动或未解决删除日志存在路径重叠的 artifact。

应先确认依赖任务和审计/备份保留策略，再通过 API 删除；不要在卷内使用 `rm`：

```bash
curl -fS -X DELETE \
  'http://localhost:8080/api/v1/model-training/artifacts/ARTIFACT_ID'
curl -fS -X DELETE \
  'http://localhost:8080/api/v1/model-training/tasks/TASK_ID'
```

成功训练会自动清理中间 checkpoints；失败、取消或中断只保留最新有效 checkpoint。
删除失败时保留 `.training-quarantine` 和数据库删除日志，让 Worker 启动协调器恢复；
不要手工清空 quarantine。

## 4. 容量与备份

Worker 预检要求至少 20 GiB 可用磁盘和 18,000 MiB 可用显存。定期检查：

```bash
docker system df
docker compose exec -T training-worker df -h /training/outputs /models/Qwen3.5-9B
curl -fsS 'http://localhost:8080/api/v1/model-training/artifacts?page=1&page_size=100'
```

清理顺序应为：确认无活动任务，按保留策略通过 API 删除可删除 artifact，再清理无关
Docker build cache。不要删除 `training_outputs` 卷，也不要用 Docker prune 影响运行中的卷。

数据库保存任务、冻结 Test IDs、artifact ID/相对路径和删除日志；卷保存实际文件。两者必须
作为同一恢复点备份。进入只读维护窗口后，先确认没有导入、训练、评估、删除或清理正在
执行，再同时停止训练 Worker 和可写主 API；frontend 可以继续显示维护页，annotation
子系统使用独立数据库，不参与该恢复点：

```bash
docker compose stop training-worker backend
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > postgres-training.dump
docker compose run --rm --no-deps -T training-worker \
  tar -C /training/outputs -czf - . > training-outputs.tar.gz
sha256sum postgres-training.dump training-outputs.tar.gz > training-backup.sha256
docker compose start backend training-worker
```

变量在 PostgreSQL 容器内展开，因此仅由 Compose `.env` 注入也可工作。两个写入者停止后，
数据库 dump 和卷归档之间没有应用写入，二者构成同一维护窗口恢复点；任一命令失败都不要
重启写入者或发布不完整备份。恢复演练必须在隔离环境中同时恢复数据库与输出卷，校验
artifact ID 可下载、任务详情可读取，然后再接受新任务。命令历史和备份不得包含明文口令。

## 5. Kubernetes 迁移

迁移保持当前单 GPU 架构：

- Worker 使用独立 Deployment，`replicas: 1`、`strategy: Recreate`，请求
  `nvidia.com/gpu: 1`；由 NVIDIA device plugin 设置容器可见设备，不在 Pod 内硬编码
  主机 GPU 编号。
- 模型通过只读 PVC 或只读节点卷挂载到 `/models/Qwen3.5-9B`；训练输出使用独立
  ReadWriteOnce PVC 挂载到 `/training/outputs`。
- 数据库使用受管 PostgreSQL 或独立 PostgreSQL PVC；连接信息和密钥全部放入 Secret。
- 设置 GPU 节点的 label/taint、Worker 的 node selector/toleration 和足够的 CPU、内存、
  ephemeral-storage；不要通过增加 Worker 副本实现并行训练。
- `--check` 可作为启动探针；存活性同时观察进程、数据库 Worker 心跳和任务心跳，避免
  仅凭 GPU 可见性判定健康。

迁移前停止新任务并等待活动任务进入终态，成对备份 PostgreSQL 与
`training_outputs`，复制完整模型快照。迁移后先恢复数据库和输出 PVC，运行数据库迁移，
再部署单 Worker；依次验证模型 marker、只读挂载、单 GPU、Worker API、artifact 下载，
最后才开放队列。Compose 中主机 GPU 1 到容器 GPU 0 的约定不应直接翻译为 Kubernetes
设备编号；Kubernetes 分配的唯一可见 GPU 在容器内仍应为索引 `0`。
