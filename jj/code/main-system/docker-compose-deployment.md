# Fault 项目 Docker Compose 部署文档

## 适用范围

本文档用于把当前 `fault` 项目部署到另一台电脑上运行，基于当前仓库中的以下文件：

- `deploy/docker-compose.db.v3.yaml`
- `deploy/docker-compose.app.yaml`
- `backend/`
- `frontend/`

本文档严格基于当前主线仓库现状编写，不假设额外的部署脚本。

## 先说结论

当前仓库 **不能直接在别的电脑上原样执行 `docker compose up`**，必须先处理两类内容：

- 把 `deploy/docker-compose.app.yaml` 里的 **绝对路径挂载** 改成目标机器上的实际路径。
- 准备运行时依赖的外部文件：
  - 向量索引：`outputs/vector_store`
  - 知识图谱：`outputs/kg`
  - 本地 embedding 模型：`all-MiniLM-L6-v2`
  - 数据导入目录：`data/imports`

如果这些内容缺失，服务可以启动，但相关功能会退化或不可用：

- 缺少 `outputs/kg`：前端知识图谱页面显示空数据
- 缺少 `outputs/vector_store`：FAISS 检索相关功能不可用
- 缺少 `all-MiniLM-L6-v2`：embedding 功能不可用

如果目标机器没有现成的 `outputs/` 产物和基础数据，还需要执行 `data_pipeline/` 里的离线脚本，先生成：

- `outputs/vector_store`
- `outputs/kg`
- MySQL 元数据
- MongoDB 日志证据数据

## 部署建议

- 推荐目标机器：Linux 主机，或者与 Docker daemon 处于 **同一文件系统视角** 的 WSL/Linux 环境
- 不推荐直接使用路径视角不一致的 Docker Desktop 绑定宿主目录，否则容易出现：
  - 宿主机目录有文件
  - 容器挂载路径为空目录

## 目标机器前置要求

- 已安装 `git`
- 已安装 `docker`
- 已安装 Docker Compose v2（命令为 `docker compose`）
- 目标机器可以构建镜像并访问 Docker Hub / 镜像代理
- 预留端口：
  - `8080`：前端
  - `5000`：后端
  - `3308`：MySQL
  - `27019`：MongoDB

## 需要准备的内容

### 1. 代码仓库

将仓库放到目标机器，例如：

```bash
git clone <your-repo-url> /srv/fault
cd /srv/fault
```

以下示例都假设项目根目录为：

```bash
/srv/fault
```

### 2. 外部运行时文件

目标机器必须准备以下目录和文件：

```text
/srv/fault/outputs/vector_store/
  faiss.index
  metadata.jsonl
  build_info.json

/srv/fault/outputs/kg/
  kg_nodes.jsonl
  kg_edges.jsonl
  kg_summary.json

/srv/fault/data/imports/

/srv/fault/assets/models/all-MiniLM-L6-v2/
  config.json
  tokenizer.json
  pytorch_model.bin / model.safetensors / 其他模型文件
```

当前项目中实际参考体量：

- `outputs/vector_store`：约 `69M`
- `outputs/kg`：约 `1.1M`
- `all-MiniLM-L6-v2`：约 `932M`

如果你手上没有这些现成文件，可以跳到后文的：

- `使用 data_pipeline 构建离线产物`

### 3. 环境变量文件

项目根目录需要有 `.env`，至少包含以下内容：

```env
# =========================
# MySQL
# =========================
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=faultuser
MYSQL_PASSWORD=faultpass
MYSQL_DB=fault_diagnosis
MYSQL_CHARSET=utf8mb4

# =========================
# MongoDB
# =========================
MONGO_URI=mongodb://mongo:27017
MONGO_DB=fault_diagnosis

# =========================
# Embedding
# =========================
ENABLE_EMBEDDING=true
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
LOCAL_MODEL_PATH=/app/models/all-MiniLM-L6-v2
EMBEDDING_DEVICE=cpu
EMBEDDING_DIM=384

# =========================
# LLM
# =========================
ENABLE_LLM=true
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=<replace-with-real-key>
LLM_TIMEOUT=60

# =========================
# Vector Store
# =========================
VECTOR_STORE_DIR=/app/outputs/vector_store
VECTOR_INDEX_PATH=/app/outputs/vector_store/faiss.index
VECTOR_METADATA_PATH=/app/outputs/vector_store/metadata.jsonl

# =========================
# Knowledge Graph
# =========================
KG_DIR=/app/outputs/kg

# =========================
# FastAPI
# =========================
HOST=0.0.0.0
PORT=8000
DEBUG=true

DATA_IMPORT_ROOT=/app/data/imports
DATA_IMPORT_MAX_FILE_SIZE=536870912
DATA_IMPORT_ALLOWED_EXTS=zip,tar,tar.gz,tgz
```

## 部署前必须修改的文件

### 修改 `deploy/docker-compose.app.yaml`

当前文件中的 backend 挂载路径是开发机专用绝对路径，不能直接拿到别的电脑使用。

当前仓库中的原始挂载：

```yaml
volumes:
  - /home/yenan/projects/fault/outputs/vector_store:/app/outputs/vector_store:ro
  - /home/yenan/projects/fault/outputs/kg:/app/outputs/kg:ro
  - /home/yenan/projects/volume/models/all-MiniLM-L6-v2:/app/models/all-MiniLM-L6-v2:ro
  - /home/yenan/projects/fault/data/imports:/app/data/imports
```

在目标机器上，改成实际路径，例如：

```yaml
volumes:
  - /srv/fault/outputs/vector_store:/app/outputs/vector_store:ro
  - /srv/fault/outputs/kg:/app/outputs/kg:ro
  - /srv/fault/assets/models/all-MiniLM-L6-v2:/app/models/all-MiniLM-L6-v2:ro
  - /srv/fault/data/imports:/app/data/imports
```

如果项目放在别的目录，必须同步替换成对应绝对路径。

### `deploy/docker-compose.db.v3.yaml` 的注意事项

该文件当前只启动 MySQL 和 MongoDB，没有显式持久化 volume：

- 适合功能部署和联调
- 不适合作为正式生产数据库方案

如果执行删除容器并重建，数据库数据有丢失风险。正式环境建议后续补充 named volume 或宿主机目录挂载。

## 一次性初始化命令

在目标机器执行：

```bash
cd /srv/fault

mkdir -p /srv/fault/outputs/vector_store
mkdir -p /srv/fault/outputs/kg
mkdir -p /srv/fault/data/imports
mkdir -p /srv/fault/assets/models

chmod 750 /srv/fault/data/imports
docker network create faultdiag_net_v3 || true
```

然后把以下内容拷贝到目标路径：

- 向量索引文件到 `/srv/fault/outputs/vector_store`
- KG 文件到 `/srv/fault/outputs/kg`
- 模型目录到 `/srv/fault/assets/models/all-MiniLM-L6-v2`

## 启动步骤

### 1. 启动数据库

```bash
cd /srv/fault/deploy
docker compose -f docker-compose.db.v3.yaml up -d
```

### 2. 构建并启动 backend

```bash
cd /srv/fault/deploy
docker compose -f docker-compose.app.yaml build backend
docker compose -f docker-compose.app.yaml up -d backend
```

### 3. 执行数据库 migration

当前 backend 不会自动执行 Alembic migration，必须手工执行：

```bash
docker exec faultdiag-backend-v3 alembic upgrade head
```

如果需要确认版本：

```bash
docker exec faultdiag-backend-v3 alembic current
docker exec faultdiag-backend-v3 alembic heads
docker exec faultdiag-backend-v3 alembic history
```

### 4. 启动 frontend

```bash
cd /srv/fault/deploy
docker compose -f docker-compose.app.yaml build frontend
docker compose -f docker-compose.app.yaml up -d frontend
```

### 5. 查看容器状态

```bash
docker ps
docker logs faultdiag-backend-v3 --tail 200
docker logs faultdiag-frontend-v3 --tail 100
```

## 使用 data_pipeline 构建离线产物

### 适用场景

当部署方只有：

- 代码仓库
- 原始数据集

但没有现成的：

- `outputs/vector_store`
- `outputs/kg`
- 已初始化的 MySQL / MongoDB 数据

就需要先执行 `data_pipeline/scripts/` 下的离线脚本。

### 重要说明

`data_pipeline` 不是 Docker Compose 自动启动的一部分。它是离线数据准备链路，建议直接在宿主机 Python 环境中执行。

推荐顺序是：

1. 先启动 MySQL 和 MongoDB
2. 在宿主机运行 `data_pipeline/scripts/*.py`
3. 生成 `outputs/vector_store` 和 `outputs/kg`
4. 再启动 backend / frontend

### data_pipeline 输入与输出

输入通常包括：

- 原始数据集目录，例如 `data/dataset`
- embedding 模型目录
- MySQL
- MongoDB

输出包括：

- MySQL：`systems / subsystems / components / cases / runs ...`
- MongoDB：`log_entries / log_windows / evidence_inputs ...`
- 向量库：`outputs/vector_store/faiss.index` 等
- 知识图谱：`outputs/kg/kg_nodes.jsonl` 等

### data_pipeline 配置方式

`data_pipeline/scripts/config.py` 会按以下顺序加载环境：

1. `FAULT_ENV_FILE` 指定的文件
2. 项目根目录 `.env.local`
3. 项目根目录 `.env`

因此推荐在项目根目录创建一份专供离线脚本使用的 `.env.local`。

示例：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3308
MYSQL_USER=faultuser
MYSQL_PASSWORD=faultpass
MYSQL_DB=fault_diagnosis
MYSQL_CHARSET=utf8mb4

MONGO_URI=mongodb://127.0.0.1:27019
MONGO_DB=fault_diagnosis

DATA_ROOT=data/dataset
MANIFEST_PATH=data/manifest/manifest.jsonl

OUTPUTS_DIR=outputs
VECTOR_OUT_DIR=outputs/vector_store
KG_OUT_DIR=outputs/kg

EMBEDDING_DEVICE=cpu
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
LOCAL_MODEL_PATH=/srv/fault/assets/models/all-MiniLM-L6-v2
```

### data_pipeline 依赖

至少准备一个宿主机 Python 3.11 环境，并安装依赖：

```bash
cd /srv/fault
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pymysql pymongo python-dateutil sentence-transformers torch faiss-cpu python-dotenv numpy
```

### 推荐执行顺序

这是 `data_pipeline/scripts/README_NEW_PIPELINE.md` 中给出的推荐顺序，结合当前仓库路径整理后的可执行版本：

```bash
cd /srv/fault/data_pipeline/scripts

python3 00_init_storage.py

python3 01_scan_dataset.py \
  --dataset-root /srv/fault/data/dataset \
  --out /srv/fault/data/manifest/manifest.jsonl \
  --system openstack \
  --include-system-in-ids

python3 02_parse_fip_info_to_mysql.py \
  --manifest /srv/fault/data/manifest/manifest.jsonl

python3 03_ingest_logs_to_mongo_v2.py \
  --manifest /srv/fault/data/manifest/manifest.jsonl

python3 04_build_log_windows_v2.py \
  --strategy error

python3 05_build_vector_index_faiss.py \
  --model-path /srv/fault/assets/models/all-MiniLM-L6-v2 \
  --out-dir /srv/fault/outputs/vector_store

python3 06_build_knowledge_graph.py \
  --out-dir /srv/fault/outputs/kg
```

### 每个脚本的作用

- `00_init_storage.py`
  - 初始化 MySQL 表结构
  - 初始化 MongoDB 集合索引

- `01_scan_dataset.py`
  - 扫描原始数据集目录
  - 生成统一入口清单 `manifest.jsonl`

- `02_parse_fip_info_to_mysql.py`
  - 解析 `fip_info.data`
  - 写入 MySQL 中的系统、组件、案例、运行等元数据

- `03_ingest_logs_to_mongo_v2.py`
  - 逐行解析日志
  - 写入 MongoDB `log_entries`
  - 回写 MySQL `runs` 的日志统计

- `04_build_log_windows_v2.py`
  - 从 `log_entries` 聚合生成 `log_windows`
  - 同步更新 MySQL `runs.window_count`

- `05_build_vector_index_faiss.py`
  - 从 `log_windows.text` 生成 embedding
  - 输出到 `outputs/vector_store`

- `06_build_knowledge_graph.py`
  - 从 MySQL + MongoDB 导出图谱
  - 输出到 `outputs/kg`

### data_pipeline 执行后的检查

离线构建完成后，至少检查：

```bash
ls -lah /srv/fault/outputs/vector_store
ls -lah /srv/fault/outputs/kg
```

期望看到：

```text
outputs/vector_store/
  faiss.index
  metadata.jsonl
  build_info.json

outputs/kg/
  kg_nodes.jsonl
  kg_edges.jsonl
  kg_summary.json
```

然后再执行前面的 Docker Compose 启动步骤。

## 启动后的验证命令

### 1. 后端健康检查

```bash
curl http://localhost:5000/health
```

期望返回：

```json
{"status":"ok"}
```

### 2. 前端可访问性

浏览器访问：

```text
http://<server-ip>:8080
```

### 3. 检查知识图谱文件是否真的挂载进容器

```bash
docker exec faultdiag-backend-v3 ls -lah /app/outputs/kg
```

期望能看到：

- `kg_nodes.jsonl`
- `kg_edges.jsonl`
- `kg_summary.json`

### 4. 检查向量索引是否真的挂载进容器

```bash
docker exec faultdiag-backend-v3 ls -lah /app/outputs/vector_store
```

期望能看到：

- `faiss.index`
- `metadata.jsonl`

### 5. 检查知识图谱 API

```bash
curl http://localhost:5000/api/v1/graph
```

如果返回的 `nodes` 为空，优先排查：

- `outputs/kg` 是否已准备
- `docker-compose.app.yaml` 中宿主机路径是否改成目标机器的真实路径
- Docker daemon 是否能看到该宿主机目录

## 常见问题

### 1. 前端知识图谱页面显示空数据

先检查：

```bash
docker exec faultdiag-backend-v3 ls -lah /app/outputs/kg
curl http://localhost:5000/api/v1/graph
```

如果容器内 `/app/outputs/kg` 是空目录，说明不是前端问题，而是 **宿主机路径挂载不可见**。

### 2. FAISS / 检索功能报错

通常是容器内缺少以下文件：

```text
/app/outputs/vector_store/faiss.index
/app/outputs/vector_store/metadata.jsonl
```

如果这些文件不存在，先执行 `data_pipeline` 的：

- `05_build_vector_index_faiss.py`

### 3. Alembic 命令在宿主机直接执行失败

优先在容器内执行：

```bash
docker exec faultdiag-backend-v3 alembic upgrade head
```

不要依赖宿主机本地 Python 环境去跑 migration，除非你明确配置过相同的 `.env` 和数据库连接。

### 4. Docker Desktop 下挂载路径明明存在，容器里却是空目录

这通常是 Docker daemon 与当前 shell 不在同一文件系统视角导致的。

排查命令：

```bash
docker inspect faultdiag-backend-v3 --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
docker exec faultdiag-backend-v3 ls -lah /app/outputs/kg
```

如果反复遇到这个问题，优先使用：

- Linux 主机部署
- 或确保 Docker daemon 与代码目录位于同一 WSL/Linux 文件系统环境

### 5. 有原始数据集，但前端图谱还是空

这通常说明你只部署了代码，但没有跑离线构建。

先检查：

```bash
ls -lah /srv/fault/outputs/kg
docker exec faultdiag-backend-v3 ls -lah /app/outputs/kg
```

如果宿主机和容器都没有 `kg_nodes.jsonl / kg_edges.jsonl`，先执行：

- `data_pipeline/scripts/06_build_knowledge_graph.py`

## 推荐的完整部署命令清单

```bash
git clone <your-repo-url> /srv/fault
cd /srv/fault

mkdir -p /srv/fault/outputs/vector_store
mkdir -p /srv/fault/outputs/kg
mkdir -p /srv/fault/data/imports
mkdir -p /srv/fault/assets/models
chmod 750 /srv/fault/data/imports

docker network create faultdiag_net_v3 || true

# 这里先手工修改 deploy/docker-compose.app.yaml 中的绝对路径

cd /srv/fault/deploy
docker compose -f docker-compose.db.v3.yaml up -d
docker compose -f docker-compose.app.yaml build backend frontend
docker compose -f docker-compose.app.yaml up -d backend
docker exec faultdiag-backend-v3 alembic upgrade head
docker compose -f docker-compose.app.yaml up -d frontend

docker ps
docker logs faultdiag-backend-v3 --tail 200
docker logs faultdiag-frontend-v3 --tail 100

curl http://localhost:5000/health
curl http://localhost:5000/api/v1/graph
docker exec faultdiag-backend-v3 ls -lah /app/outputs/kg
docker exec faultdiag-backend-v3 ls -lah /app/outputs/vector_store
```

## 两种交付方式

### 方式 A：交付“可直接部署产物”

适合部署方只负责启动服务，不负责重建数据。

你需要一并提供：

- 仓库代码
- 脱敏后的 `.env`
- `outputs/vector_store/`
- `outputs/kg/`
- `all-MiniLM-L6-v2/`

这时部署方不需要运行 `data_pipeline`。

### 方式 B：交付“代码 + 原始数据集”

适合部署方需要自己从原始日志重建整套数据。

你需要一并提供：

- 仓库代码
- 脱敏后的 `.env.local` 或 `.env`
- 原始数据集目录
- `all-MiniLM-L6-v2/`
- 本文档中 `data_pipeline` 的执行步骤

这时部署方需要先运行 `data_pipeline`，再启动 Compose。

## 交付给部署方时建议一并提供

- 代码仓库地址与分支名
- 一份脱敏后的 `.env`
- 如果没有预构建产物，还要提供 `.env.local` 或等价脚本环境说明
- `outputs/vector_store/`
- `outputs/kg/`
- `all-MiniLM-L6-v2/`
- 如果要求对方自行重建数据，还要提供原始数据集目录
- 目标机器上的部署根目录约定
- 端口占用要求
- 是否需要外网访问 `LLM_BASE_URL`
