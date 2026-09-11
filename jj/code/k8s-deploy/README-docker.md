# 故障定位 + 日志标注 集成系统 · Docker 部署手册（631_9.7 6 镜像版）

> 本文件是 **Docker Compose 单机部署** 的完整手册，可独立阅读。K8s 部署见 `README-k8s.md`。
>
> **本版为 6 镜像精简包（不含 training-worker 训练镜像）**。训练镜像体积大（~9GB，pytorch-cuda 基础），
> 需要时再手动构建并追加（见「四、附：手动追加训练镜像」），不影响本包部署。

## 版本

`631_9.7`（2026-09）新代码重打镜像。在 631(7.6) 集成基线上：
- **修复标注导入对二进制/元数据文件的误解析**：`import_worker` 新增 `_is_not_source_file`，
  排除 `__MACOSX/`、`._*`、`fip_info.data`、`ground_truth.json` 及含 NUL 字节的二进制文件，
  避免整包导入被 PostgreSQL TEXT 拒绝。
- **LLM 网关打通**：标注子系统的 `INTERNAL_LLM_GATEWAY_TOKEN` 与主系统共用同一值，
  大模型推荐 / 窗口分析 / 时间戳推断才能走通（见「三、启用 LLM」）。

## 包内容

```
631_9.7/k8s-deploy/
├── docker-compose.yaml         image-only，6 服务
├── .env.example                环境变量样例（DB / LLM / 网关 token / 加密Key）
├── save-images.sh / load-images.sh
├── images-integrated.tar.gz.00.part   6 镜像单分片（927M，<1G）
├── images-integrated.tar.gz.sha256    合并后校验用
├── README-docker.md            本文件
└── README-k8s.md
```

## 服务与镜像

| Compose 服务 | 镜像 | 说明 |
|---|---|---|
| postgres | `faultdiag-postgres:16` | 主系统库 |
| backend | `faultdiag-backend:latest` | 主后端（BGE + 内置数据集；挂 `backend_outputs` 向量库/KG） |
| frontend-main | `faultdiag-frontend:integrated` | 主前端 + nginx 入口(:8080) |
| postgres-annotate | `faultdiag-postgres-annotate:16-alpine` | 标注库 |
| backend-annotate | `faultdiag-backend-annotate:latest` | 标注后端 |
| frontend-annotate | `faultdiag-frontend-annotate:latest` | 标注前端 |

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

# 一、部署

目标机要求：Docker + compose v2。

## 1. 加载镜像 + 配置

```bash
cd /opt/faultdiag-integrated          # 本目录 + 全部 images-integrated.tar.gz.*.part 分片
sh load-images.sh                     # 自动合并分片 → 校验 → docker load（权限不足加 sudo）
cp -n .env.example .env               # 按需改：DB 密码 / LLM / 网关 token
```

`.env` 要点：
```env
# DB 强密码（部署前改，与旧库一致才能保留旧数据）
POSTGRES_PASSWORD=secret
ANNOTATE_PASSWORD=data_bj
# 用「模型 API 管理」加密凭据功能时填（生成见文件内注释）；否则可留空
MODEL_API_ENCRYPTION_KEY=
```

## 2. 启动

```bash
docker compose up -d
docker compose ps                     # 等到 healthy/running
```

## 3. 访问

| URL | 用途 |
|---|---|
| `http://<host>:8080` | 主系统首页 |
| `http://<host>:8080/#/data-processing/preprocess` | 数据标注（iframe） |
| `http://<host>:5000/api/docs` | 主后端 Swagger |
| `http://<host>:8080/annotate-api/v1/health` | 标注后端健康检查 |

## 4. 启用 LLM

`.env` 填 LLM 三件套 + 网关 token，然后重启两个后端：
```env
ENABLE_LLM=True
LLM_BASE_URL=https://api.deepseek.com        # OpenAI 兼容协议均可
LLM_API_KEY=sk-xxxx
LLM_MODEL=deepseek-chat
# 主/标注网关共用（生成: openssl rand -hex 32），两端必须同值
INTERNAL_LLM_GATEWAY_TOKEN=xxxxxxxx
```
```bash
docker compose up -d backend backend-annotate
```
未启用时所有大模型功能自动降级到规则逻辑，不报错（仅标注侧推荐卡提示「LLM 未配置」）。

## 5. 启停

```bash
docker compose down                   # 停容器，保留数据卷
docker compose down -v                # ⚠ 连数据卷一起删（清空所有库 + 向量库）
```

---

# 二、升级既有部署（保留旧数据）

铁律：业务数据在 PostgreSQL（`pgdata` / `pgdata_annotate` 卷），只要不 `down -v`、凭据与旧库一致，原地覆盖即保留。

```bash
cd /opt/faultdiag-integrated
docker compose exec postgres pg_dump -U postgres fault_diagnosis | gzip > fd_$(date +%F).sql.gz   # 备份
docker compose down                   # ⚠ 不加 -v
# 用新包文件覆盖本目录（保留你的 .env），放入新的 images-integrated.tar.gz
sh load-images.sh
docker compose up -d
```

> **网关 token**：若之前未填 `INTERNAL_LLM_GATEWAY_TOKEN`，本次升级补填（新旧同值即可，主/标注共用），否则标注侧大模型功能不可用。

---

# 三、故障排查

| 症状 | 检查 |
|---|---|
| 容器启动失败 | `docker compose logs <service>` |
| 标注导入失败（二进制/元数据文件） | 本版已修复：`_is_not_source_file` 排除 `__MACOSX` / `._*` / `fip_info.data` / `ground_truth.json` / 含 NUL 二进制。若仍失败，`docker compose logs backend-annotate` 看具体异常 |
| 标注侧「LLM 未配置」 | `INTERNAL_LLM_GATEWAY_TOKEN` 未填或主/标注不一致 → 生成同值填入 .env 并重启两端；或主后端未启用 LLM（网关 503） |
| 大模型不响应 | `.env` 的 LLM 未启用/未填全；未启用为预期降级 |
| 模型 API 管理保存报 `ENCRYPTION_KEY` | `.env` 的 `MODEL_API_ENCRYPTION_KEY` 未填/非法；生成 Fernet key 填入后 `docker compose up -d backend`。库里已有加密行时不要改此值 |
| 故障诊断相似度「-」 | 知识库 Chroma 为空——先在「知识库 → 日志管理」上传/向量化案例 |
| 8080/5000 端口被占 | 改 `docker-compose.yaml` ports 左侧主机端口 |

## 资源占用参考

| 服务 | 镜像大小 | 内存 |
|---|---|---|
| backend (主) | ~1.93 GB | 1-2 GB |
| backend-annotate | ~210 MB | 200-400 MB |
| frontend ×2 | ~65 MB 各 | 30 MB 各 |
| postgres ×2 | 451+294 MB | 各 100-300 MB |

---

# 四、附：手动追加训练镜像（可选，非本包范围）

本包不含训练镜像（体积 ~9GB）。后续需要训练能力时，在源机：

```bash
# 构建上下文是 631_9.7 的【上级目录】，依赖同级 model_train/
cd /home/junjiezuo/631-fault
docker build -f 631_9.7/main-system/backend/Dockerfile.training -t faultdiag-training-worker:latest .
# 打 tag 推送后，再从旧版 631(modeldeploy)/k8s-deploy/ 拷贝 training-worker 服务块
# 追加到本目录 docker-compose.yaml 的 services: 下（含 training_outputs 卷 + GPU deploy 段）
```

> 本包 compose 未附带 training-worker 服务块；需要时从旧版 `631(modeldeploy)` 目录复制并核对命名。

---

# 五、修订记录

## 631_9.7（2026-09：训练构建逻辑修复 + 新代码重打镜像）
- **标注导入修复**：`import_worker._is_not_source_file` 排除平台元数据/打包残留/二进制（NUL）文件，避免整包导入被 PostgreSQL TEXT 拒绝。
- **LLM 网关打通**：两端共用 `INTERNAL_LLM_GATEWAY_TOKEN`；标注侧推荐/窗口分析可用。
- **6 镜像精简包**：移除 training-worker；整包 927M 单分片 <1G。

## 上一轮（631(7.6) 第九轮）
- 方案 A 快通道生效；run_id 碎片化修复；跨系统导入桥；来源/去处列、历史可回放、三处删除联通；prompt 优化；未配 LLM 全程降级。
