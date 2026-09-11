# 故障定位 + 日志标注 集成系统 · Kubernetes 部署手册（631_9.7 6 镜像版）

> 完整流程：生成镜像 → 全新部署 → 升级保留数据 → 故障排查。单机 Docker 部署见 `README-docker.md`。
>
> **本版为 6 镜像精简包（不含 training-worker 训练镜像）**。训练镜像体积大（~9GB，pytorch-cuda 基础），
> 需要时再手动构建并追加（见「附：手动追加训练镜像」），不影响本包部署。

## 版本

`631_9.7`（2026-09）新代码重打镜像。在 631(7.6) 集成基线上：
- **修复标注导入对二进制/元数据文件的误解析**：`import_worker` 新增 `_is_not_source_file`，
  排除 `__MACOSX/`、`._*`、`fip_info.data`、`ground_truth.json` 及含 NUL 字节的二进制文件——
  否则这类文件会被当文本日志解析，NUL 字节被 PostgreSQL TEXT 列拒绝导致整包导入失败。
- **LLM 网关打通**：标注子系统的 `INTERNAL_LLM_GATEWAY_TOKEN` 与主系统共用同一值，
  大模型推荐 / 窗口分析 / 时间戳推断才能走通（见「三、启用 LLM」）。

## 包内容（K8s 相关）

```
631_9.7/k8s-deploy/
├── k8s/
│   ├── 00-namespace.yaml
│   ├── 01-config.yaml            ConfigMap + Secret（DB / LLM / 网关 token / 加密 key）
│   ├── 02-postgres.yaml          主库（PVC faultdiag-pgdata）
│   ├── 03-postgres-annotate.yaml 标注库（PVC faultdiag-pgdata-annotate）
│   ├── 04-backend.yaml           主后端（PVC backend-outputs）
│   ├── 05-backend-annotate.yaml  标注后端
│   ├── 06-frontend-annotate.yaml 标注前端
│   └── 07-frontend-main.yaml     主前端 + NodePort 30080
├── save-images.sh / load-images.sh
├── images-integrated.tar.gz.00.part  6 镜像单分片（927M，<1G；如需更大分片再 split）
├── images-integrated.tar.gz.sha256
├── README-k8s.md                 本文件
└── README-docker.md
```

## 服务拓扑

```
浏览器 ─ NodePort :30080 ─▶ faultdiag-frontend-main ─┬─ / 主SPA
                                                    ├─ /api/ backend（主后端）
                                                    ├─ /annotate/ frontend-annotate
                                                    └─ /annotate-api/v1/ backend-annotate
DB：postgres(fault_diagnosis, PVC pgdata) / postgres-annotate(data_bj, PVC pgdata-annotate)
```

| Deployment | Service | 镜像 |
|---|---|---|
| postgres | postgres | `faultdiag-postgres:16` |
| faultdiag-backend | backend | `faultdiag-backend:latest` |
| faultdiag-frontend-main | faultdiag-frontend | `faultdiag-frontend:integrated`（NodePort 30080） |
| postgres-annotate | postgres-annotate | `faultdiag-postgres-annotate:16-alpine` |
| faultdiag-backend-annotate | backend-annotate | `faultdiag-backend-annotate:latest` |
| faultdiag-frontend-annotate | frontend-annotate | `faultdiag-frontend-annotate:latest` |

> 建表自动化：主后端入口 `create_tables.py` 自动 `alembic upgrade head` / `create_all + stamp`，**无需手工迁移**。

---

# 零、生成镜像包（源机，一次）

```bash
# 1) 构建 4 个核心业务镜像
cd /home/junjiezuo/631-fault/631_9.7
docker compose build backend frontend-main backend-annotate frontend-annotate

# 2) 基础库镜像
docker pull postgres:16 ; docker pull postgres:16-alpine

# 3) 打包（脚本给 postgres 打集成别名再 save）
cd k8s-deploy
sh save-images.sh          # 输出 images-integrated.tar.gz（6 镜像，压缩后约 927M）
#    自动按 <1G 分片: images-integrated.tar.gz.00.part ...
```

> **镜像分包**：整包 927M 单分片即可；若以后镜像更大，`split -b 1024m -d -a 2 images-integrated.tar.gz images-integrated.tar.gz.` 重切，
> `load-images.sh` 自动合并 `.*.part`。
> **离线搬运**：拷贝**全部分片 + `.sha256`** 到目标机同目录，`sh load-images.sh` 自动合并+校验+load。
> 手动合并：`cat images-integrated.tar.gz.*.part > images-integrated.tar.gz`；校验 `sha256sum -c images-integrated.tar.gz.sha256`。

---

# 一、全新部署

## 1. 前置

- K8s 集群 + `kubectl` + 一个镜像仓库（下例 `easzlab.io.local:5000`）。
- 各节点能拉取镜像；离线环境则先在任一节点 `sh load-images.sh` 导入（仅 K8s 节点需要 Docker/containerd 能访问该镜像）。

## 2. 变量

```bash
REGISTRY=easzlab.io.local:5000
NS=faultdiag-integrated
TAG=release-2026-08-19
```

> 建议用唯一 TAG，不要复用 `latest`（`IfNotPresent` 会命中旧缓存）。

## 3. 打标签 + 推送

```bash
docker login $REGISTRY
for pair in \
  "faultdiag-backend:latest|faultdiag-backend:$TAG" \
  "faultdiag-frontend:integrated|faultdiag-frontend:$TAG" \
  "faultdiag-backend-annotate:latest|faultdiag-backend-annotate:$TAG" \
  "faultdiag-frontend-annotate:latest|faultdiag-frontend-annotate:$TAG" \
  "faultdiag-postgres:16|faultdiag-postgres:16" \
  "faultdiag-postgres-annotate:16-alpine|faultdiag-postgres-annotate:16-alpine" ; do
    docker tag "${pair%%|*}" "$REGISTRY/${pair##*|}" && docker push "$REGISTRY/${pair##*|}"
done
```

## 4. 替换清单占位符

```bash
cd k8s
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-backend:latest|$REGISTRY/faultdiag-backend:$TAG|g" 04-backend.yaml
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-frontend:integrated|$REGISTRY/faultdiag-frontend:$TAG|g" 07-frontend-main.yaml
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-backend-annotate:latest|$REGISTRY/faultdiag-backend-annotate:$TAG|g" 05-backend-annotate.yaml
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-frontend-annotate:latest|$REGISTRY/faultdiag-frontend-annotate:$TAG|g" 06-frontend-annotate.yaml
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-postgres:16|$REGISTRY/faultdiag-postgres:16|g" 02-postgres.yaml
sed -i "s|REGISTRY_PLACEHOLDER/faultdiag-postgres-annotate:16-alpine|$REGISTRY/faultdiag-postgres-annotate:16-alpine|g" 03-postgres-annotate.yaml
grep -n "image:" *.yaml      # 核对
```

## 5. 按需改配置

- `01-config.yaml`：
  - 改 DB 强密码（`POSTGRES_PASSWORD` / `ANNOTATE_PASSWORD`）。
  - 开 LLM：`ENABLE_LLM=True` + `LLM_BASE_URL` + `LLM_MODEL` + Secret `LLM_API_KEY`。
  - **网关 token（重要）**：主后端与标注后端通过 `INTERNAL_LLM_GATEWAY_TOKEN` 校验网关调用。
    **主系统与标注子系统必须填同一个值**，否则标注侧「大模型推荐」显示「LLM 未配置」。
    生成：`openssl rand -hex 32`，填进 `01-config.yaml` 的 Secret。
  - 用「模型 API 管理」加密功能则在 Secret 填 `MODEL_API_ENCRYPTION_KEY`（Fernet key，生成见文件注释）。

## 6. 应用

```bash
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-config.yaml
kubectl apply -f 02-postgres.yaml
kubectl apply -f 03-postgres-annotate.yaml
kubectl -n $NS rollout status deploy/postgres
kubectl -n $NS rollout status deploy/postgres-annotate
kubectl apply -f 04-backend.yaml
kubectl apply -f 05-backend-annotate.yaml
kubectl apply -f 06-frontend-annotate.yaml
kubectl apply -f 07-frontend-main.yaml
kubectl -n $NS get pods -w
```

访问 `http://<任意节点IP>:30080/`（标注页：`/#/data-processing/preprocess`）。

---

# 二、升级既有部署（保留旧数据）

要点：

0. **先对齐 NS**：`kubectl get ns | grep faultdiag` 查真名；若非 `faultdiag-integrated`，把 `k8s/` 下所有 yaml 的 `metadata.namespace` sed 成旧真名，否则会 apply 到错误 NS 起空库。
1. **数据靠 PVC**：`faultdiag-pgdata` / `faultdiag-pgdata-annotate` / `faultdiag-backend-outputs` 不删不改名即保留。
2. **网关 token**：若之前未填 `INTERNAL_LLM_GATEWAY_TOKEN`，本次升级补填（新旧同值即可，主/标注共用）。

```bash
NS=<你的旧NS真名>
kubectl -n $NS exec deploy/postgres -- pg_dump -U postgres fault_diagnosis | gzip > fd_$(date +%F).sql.gz  # 备份
kubectl apply -f 01-config.yaml      # 确认 DB 凭据与旧库一致；补网关 token
kubectl apply -f 04-backend.yaml     # Recreate
kubectl apply -f 05-backend-annotate.yaml
kubectl apply -f 06-frontend-annotate.yaml
kubectl apply -f 07-frontend-main.yaml
kubectl -n $NS rollout status deploy/faultdiag-backend
kubectl -n $NS exec deploy/postgres -- psql -U postgres -d fault_diagnosis -c "select count(*) from mongo_docs;"  # 验证旧数据
```

---

# 三、启用 LLM

标注子系统的大模型功能（推荐/窗口分析/时间戳推断）走**主系统网关**：标注后端 `LLM_GATEWAY_URL → 主后端 /api/v1/internal/llm/chat`，
需要 **主后端也启用 LLM**（否则网关 503 `llm_gateway_not_configured`）。

- `01-config.yaml`：`ENABLE_LLM=True`、`LLM_BASE_URL`、`LLM_MODEL`、Secret `LLM_API_KEY` 填好。
- **`INTERNAL_LLM_GATEWAY_TOKEN` 两端一致**（主后端 + 标注后端都从同一 Secret 取）。
- 主后端可用「模型 API 管理」（页面）配置模型来源（DB `model_api_configs` 优先于 env）。

未启用 LLM 时，全部大模型功能自动降级到规则逻辑，不报错（仅标注侧推荐卡提示「LLM 未配置」）。

---

# 四、故障排查

| 症状 | 检查 |
|---|---|
| apply 后行为没变 | 镜像缓存——复用了旧 TAG；用唯一新 TAG 或节点 `docker rmi` |
| 标注导入失败（二进制/元数据文件） | 本版已修复：`_is_not_source_file` 排除 `__MACOSX` / `._*` / `fip_info.data` / `ground_truth.json` / 含 NUL 二进制。若仍失败，`kubectl logs deploy/faultdiag-backend-annotate` 看具体异常 |
| 标注侧「LLM 未配置」 | `INTERNAL_LLM_GATEWAY_TOKEN` 未填或主/标注不一致 → 生成同值填入 01-config 并重启两端；或主后端未启用 LLM（网关 503） |
| 大模型不响应 | ConfigMap `ENABLE_LLM=False` 即未启用；填 LLM 三件套 + Secret LLM_API_KEY |
| 30080 访问 502 | `get svc faultdiag-frontend` 看 NodePort；`get endpoints` 看后端是否就绪 |
| backend CrashLoop `password authentication failed` | DB 凭据与旧库不符（升级），对齐 Secret/ConfigMap |
| PV 起不来 | 无默认 StorageClass——PVC 里取消注释 `storageClassName` 填实际名 |
| 故障诊断相似度「-」 | 知识库 Chroma 为空——先在「知识库 → 日志管理」上传/向量化案例 |

## 资源占用参考

| 服务 | 镜像大小 | 内存 |
|---|---|---|
| backend (主) | ~1.93 GB | 1-2 GB |
| backend-annotate | ~210 MB | 200-400 MB |
| frontend ×2 | ~65 MB 各 | 30 MB 各 |
| postgres ×2 | 451+294 MB | 各 100-300 MB |

---

# 五、附：手动追加训练镜像（可选，非本包范围）

本包不含训练镜像（体积 ~9GB）。后续需要训练能力时，在源机：

```bash
# 构建上下文是 631_9.7 的【上级目录】，依赖同级 model_train/
cd /home/junjiezuo/631-fault
docker build -f 631_9.7/main-system/backend/Dockerfile.training -t faultdiag-training-worker:latest .
# 打 tag 推送后，再 apply 训练清单 k8s/08-training-worker.yaml / 09（从旧 631(modeldeploy) 目录拷贝）
```

> 本包 `k8s/` 未附带 08/09 训练清单；需要时从旧版 `631(modeldeploy)/k8s-deploy/k8s/` 复制并按本包 namespace 对齐。

---

# 六、修订记录

## 631_9.7（2026-09：训练构建逻辑修复 + 新代码重打镜像）
- **标注导入修复**：`import_worker._is_not_source_file` 排除平台元数据/打包残留/二进制（NUL）文件，避免整包导入被 PostgreSQL TEXT 拒绝。
- **LLM 网关打通**：两端共用 `INTERNAL_LLM_GATEWAY_TOKEN`；标注侧推荐/窗口分析可用。
- **6 镜像精简包**：移除 training-worker；整包 927M 单分片 <1G。

## 上一轮（631(7.6) 第九轮）
- 方案 A 快通道；跨系统导入桥；run_id 修复；来源/去处列、历史回放、三处删除联通；prompt 优化；未配 LLM 全程降级。
