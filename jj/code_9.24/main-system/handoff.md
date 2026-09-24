# Handoff

## 0. 本次 Session 更新（2026-05-26）

本次会话在 `feature/nuttx-log-browser` 分支上，完成了前端路由归属重组的 **Phase 1**（仅前端，最小侵入）。

### 0.1 已完成内容

1. 将“日志运行列表/详情”入口从 `log-analysis` 归属迁移到 `data-processing` 菜单下。
2. `data-processing` 改为父菜单，新增子入口：
   - `日志预处理`
   - `日志列表`
3. 新前端 URL 已生效：
   - `#/data-processing/preprocess`
   - `#/data-processing/logs`
   - `#/data-processing/logs/:runId`
4. 旧 URL 保留兼容并自动跳转：
   - `#/log-analysis/analysis` -> `#/data-processing/logs`
   - `#/log-analysis/analysis/:runId` -> `#/data-processing/logs/:runId`
5. 详情页返回行为已固定为回到“日志列表”（不是浏览器 history back）。
6. 左侧菜单高亮已归属到 `data-processing/logs`。

### 0.2 本次修改文件

- `frontend/src/router/index.js`
- `frontend/src/components/Layout/AppLayout.vue`
- `frontend/src/views/LogAnalysis/LogAnalysisDetail.vue`

### 0.3 明确未改动范围

- 未修改后端代码（`backend` 无变更）
- 未修改 API schema
- 未修改 store
- 未重构页面业务逻辑
- 未迁移 `views/LogAnalysis/*` 文件目录（仅路由归属迁移）

### 0.4 当前前端路由事实（迁移后）

- `data-processing` 是父路由，复用 `views/LogAnalysis/index.vue` 作为容器 `router-view`。
- `LogAnalysisMain` 路由名现在对应 `data-processing/logs`。
- `LogAnalysisDetail` 路由名现在对应 `data-processing/logs/:runId`。
- `log-analysis` 模块仍保留 `upload/parse`，仅 `analysis*` 改为 redirect 兼容入口。

### 0.5 验证结论

已完成的本地验证：

- `cd /home/yenan/projects/fault/frontend && npm run build` 通过
- `cd /home/yenan/projects/fault/frontend && node --test tests/logAnalysisTransforms.test.mjs` 通过
- 路由与菜单代码检查确认：
  - `activeMenu` 已走 `route.meta?.activeMenu || route.path`
  - `analysis` 和 `analysis/:runId` 已配置 redirect 到新路径

### 0.6 使用时注意（避免误判）

1. 打开 `#/data-processing` 时会默认重定向到 `#/data-processing/preprocess`，这是预期行为。
2. 如果看到还是旧菜单结构，通常是浏览器缓存未刷新，先做一次强刷：
   - Windows/Linux: `Ctrl + Shift + R` 或 `Ctrl + F5`
3. 可直接访问 `http://localhost:8080/#/data-processing/logs` 验证“日志列表”新入口。

### 0.7 已完成的数据查询功能（请作为新会话基线）

> 这部分是本轮之前阶段已经做完并可用的功能，后续不要重复实现。

后端（真实 MySQL + Mongo，非 mock）：

- 已实现并接通：
  - `GET /api/v1/log-analysis/logs`
  - `GET /api/v1/log-analysis/logs/{run_id}`
  - `GET /api/v1/log-analysis/logs/{run_id}/entries`
  - `GET /api/v1/log-analysis/logs/{run_id}/windows`
- 路由位置：
  - `backend/app/api/v1/log_analysis.py`
- 数据查询服务：
  - `backend/app/services/log_dataset_service.py`

已确认的关键修复：

1. `test_name` 来源修正为 `cases.test_name`（不是 `runs.test_name`）。
2. `LIKE` 查询转义修复（避免 `Test_1030` 这类过滤触发 SQL 500）：
   - 使用 `ESCAPE '\\'` 兼容写法
   - 相关实现见 `log_dataset_service.py` 中 `MYSQL_LIKE_ESCAPE_SQL` 和 `build_run_filters`

前端（已接入后端真实接口）：

- API 封装在：
  - `frontend/src/api/logAnalysis.js`
  - `listDatasetRuns`
  - `getDatasetRunDetail`
  - `listDatasetRunEntries`
  - `listDatasetRunWindows`
- 页面联动在：
  - `frontend/src/views/LogAnalysis/LogAnalysis.vue`（列表 + 筛选 + 分页）
  - `frontend/src/views/LogAnalysis/LogAnalysisDetail.vue`（详情 + entries/windows 分页）

当前事实说明：

- 后端现有主可用路径是 `/log-analysis/logs*`。
- 早前计划里提过 `/log-analysis/runs*`，但主线代码并未做这套别名路由；新会话不要默认依赖 `/runs*`。

## 1. 当前主线

这个项目当前真正使用的主线是：

- 前端：`/home/yenan/projects/fault/frontend`
- 后端：`/home/yenan/projects/fault/backend`
- 数据处理脚本：`/home/yenan/projects/fault/data_pipeline/scripts`
- 部署文件：
  - `deploy/docker-compose.db.v3.yaml`
  - `deploy/docker-compose.app.yaml`

不是主线的目录：

- `new_backend`
  - 目前不是 Compose 实际启动的后端
  - 不应当作为当前联调、排障、交付的主代码目录
- `new_pipeline`
  - 当前不作为主数据处理入口
  - 当前真正使用并验证过的是 `data_pipeline/scripts`

一句话结论：

- 项目主服务看 `backend`
- 数据导入和处理看 `data_pipeline/scripts`

## 1.1 下一步目标

下一步主目标是：

- 把新生成的嵌入式系统软件故障日志数据通过 `data_pipeline/scripts` 导入当前主数据库
- 在现有 `backend` 和 `frontend` 上补齐一套“可查询、可展示”这批嵌入式日志数据的功能

这件事不是走 `new_backend` 或 `new_pipeline`，而是继续基于：

- `backend`
- `frontend`
- `data_pipeline/scripts`
- 当前主库 `fault_diagnosis`

目标拆解为三部分：

1. 数据层
   - 让新生成的嵌入式日志数据能稳定进入 MySQL 和 MongoDB
   - 至少完成 `01 -> 02 -> 03_ingest_logs_to_mongo_v2_tz.py -> 04_new.py`
   - 如需检索和图谱能力，再继续跑 `05` 和 `06`
2. 后端
   - 基于 `backend` 提供针对这批嵌入式数据的查询接口
   - 至少保证前端能按 run / case / test_name / fault 状态查询列表
   - 必要时补充详情接口，返回 `log_entries`、`log_windows`、统计信息
3. 前端
   - 在现有页面中增加这批嵌入式数据的列表展示和查询入口
   - 至少支持分页、筛选、查看单条 run 的基础信息和日志分析结果

## 2. 当前运行方式

### 2.1 Docker 容器

当前部署结构：

- MySQL：`faultdiag-mysql-v3`
- MongoDB：`faultdiag-mongo-v3`
- Backend：`faultdiag-backend-v3`
- Frontend：`faultdiag-frontend-v3`

其中 `deploy/docker-compose.app.yaml` 明确构建并启动的是：

- `../backend`
- 不是 `../new_backend`

### 2.2 访问地址

- 前端：`http://localhost:8080`
- 后端：`http://localhost:5000`
- Swagger：`http://localhost:5000/api/docs`
- 健康检查：`http://localhost:5000/health`

## 3. 环境变量约定

### 3.1 Docker 容器内

根目录 `.env` 给容器内服务使用，容器内地址是：

- `MYSQL_HOST=mysql`
- `MYSQL_PORT=3306`
- `MONGO_URI=mongodb://mongo:27017`

这套配置对应：

- `deploy/docker-compose.app.yaml`
- `deploy/docker-compose.db.v3.yaml`

### 3.2 宿主机本地跑数据脚本

`data_pipeline/scripts/config.py` 当前会优先加载：

1. `FAULT_ENV_FILE` 指定的 env 文件
2. 默认 `.env.local`
3. 找不到再退回 `.env`

所以本地跑数据脚本时，应使用：

- `.env.local`

当前本地脚本应连接宿主机映射端口：

- MySQL：`127.0.0.1:3308`
- MongoDB：`127.0.0.1:27019`

## 4. 当前数据库和数据层职责

### 4.1 MySQL

当前主库：`fault_diagnosis`

主要承载：

- `fault_types`
- `diagnosis_records`
- `prediction_records`
- `cases`
- `runs`
- `diagnosis_tasks`
- `diagnosis_results`
- `task_feature_summaries`

### 4.2 MongoDB

当前主库：`fault_diagnosis`

主要承载：

- `log_entries`
- `log_windows`
- `evidence_inputs`
- 上传和日志分析相关集合

### 4.3 本地产物目录

- 向量库：`/home/yenan/projects/fault/outputs/vector_store`
- 知识图谱：`/home/yenan/projects/fault/outputs/kg`

Backend 容器会只读挂载这两个目录。

## 5. 当前已确认的后端事实

### 5.1 主后端是 `backend`

后端入口文件：

- `backend/app/main.py`

已挂载的主要模块：

- `/api/v1/overview`
- `/api/v1/fault-types`
- `/api/v1/logs`
- `/api/v1/diagnosis`
- `/api/v1/prediction`
- `/api/v1/graph`
- `/api/v1/log-analysis/*`
- `/api/v1/data-processing/*`

### 5.2 日志分析列表接口当前可用

已经实际确认过：

- `GET /api/v1/log-analysis/logs?page=1&page_size=20`

返回 `200 OK`，并且能读到已经导入的数据。

### 5.3 一个重要修正点

日志分析列表接口不能从 `runs.test_name` 取字段，因为：

- `runs` 表没有 `test_name`
- `test_name` 在 `cases` 表

因此正确查询应走：

- `runs r`
- `LEFT JOIN cases c ON c.case_id = r.case_id`
- 取 `c.test_name`

当前本地 `backend/app/api/v1/log_analysis.py` 已经是这个方向的兼容写法。

## 6. 当前主数据处理脚本

实际应使用目录：

- `/home/yenan/projects/fault/data_pipeline/scripts`

当前最重要的脚本是：

1. `00_init_storage.py`
2. `01_scan_dataset.py`
3. `02_parse_fip_info_to_mysql.py`
4. `03_ingest_logs_to_mongo_v2_tz.py`
5. `04_new.py`
6. `05_build_vector_index_faiss.py`
7. `06_build_knowledge_graph.py`

说明：

- `03_ingest_logs_to_mongo_v2_tz.py`
  - 对带 `Z` / `+00:00` / 时区偏移的 ISO 时间戳日志可解析
  - 当前 NuttX / 嵌入式这批数据应优先用它
- `04_new.py`
  - 当前实际验证过可用
  - 会从 `log_entries` 生成 `log_windows`
  - 会回写 `runs.window_count` 和 `task_feature_summaries`

旧脚本仍然存在，但当前主线联调时不应优先写成：

- `new_pipeline/*.py`

## 7. 当前推荐的数据导入顺序

在宿主机终端执行，默认使用 `.env.local`：

```bash
cd /home/yenan/projects/fault
```

### 7.1 生成 manifest

```bash
python3 data_pipeline/scripts/01_scan_dataset.py \
  --dataset-root /home/yenan/projects/fault/data/dataset \
  --out /home/yenan/projects/fault/data/manifest/manifest.jsonl \
  --summary-out /home/yenan/projects/fault/data/manifest/summary.json \
  --system openstack \
  --include-system-in-ids \
  --abs-path
```

### 7.2 写入 MySQL 元数据

```bash
python3 data_pipeline/scripts/02_parse_fip_info_to_mysql.py \
  --manifest /home/yenan/projects/fault/data/manifest/manifest.jsonl
```

### 7.3 写入 Mongo 日志

如果日志带时区时间戳，优先跑：

```bash
python3 data_pipeline/scripts/03_ingest_logs_to_mongo_v2_tz.py \
  --manifest /home/yenan/projects/fault/data/manifest/manifest.jsonl \
  --drop-run-first
```

### 7.4 构建日志窗口

如果只跑全量默认逻辑，可用：

```bash
python3 data_pipeline/scripts/04_new.py --strategy auto
```

如果只想重建某些 run，推荐逐个带：

```bash
python3 data_pipeline/scripts/04_new.py \
  --strategy auto \
  --drop-run-windows \
  --only-run-id <run_id>
```

### 7.5 构建 FAISS 向量库

```bash
python3 data_pipeline/scripts/05_build_vector_index_faiss.py \
  --model-path /home/yenan/projects/volume/models/all-MiniLM-L6-v2 \
  --out-dir /home/yenan/projects/fault/outputs/vector_store \
  --device cpu \
  --dim 384
```

### 7.6 构建知识图谱

```bash
python3 data_pipeline/scripts/06_build_knowledge_graph.py \
  --out-dir /home/yenan/projects/fault/outputs/kg
```

## 8. 关于 `data_a`

### 8.1 当前目录结构

当前 `data_a` 目录是：

- `/home/yenan/projects/fault/data/data_a`

当前这一版数据放在：

- `data_a/NuttX/Test_1001` 到 `data_a/NuttX/Test_1030`

这是当前最新一批 `data_a` 测试子集。

### 8.2 最近一次导入结果

最近一次已确认完成的导入路径是：

1. 重新扫描 `data_a`
2. 跑 `02_parse_fip_info_to_mysql.py`
3. 跑 `03_ingest_logs_to_mongo_v2_tz.py`
4. 对这 60 个 run 单独跑 `04_new.py`

导入后，后端接口已经能看到这批数据，例如：

- `nuttx_NuttX_Test_1030_round_2`
- `nuttx_NuttX_Test_1030_round_1`
- `nuttx_NuttX_Test_1029_round_2`
- `nuttx_NuttX_Test_1029_round_1`

### 8.3 当前策略

对 `data_a` 的处理当前采用的是：

- 非破坏式导入

也就是：

- 新批次数据会写入主库
- 同名 run 会覆盖对应日志和窗口
- 旧的 NuttX 历史测试不会自动删除

如果后续需要清理旧 NuttX 批次，要显式执行清理，不要默认删。

## 9. Docker 启停命令

### 9.1 启动数据库

```bash
cd /home/yenan/projects/fault
docker network create faultdiag_net_v3 || true
cd deploy
docker compose -f docker-compose.db.v3.yaml up -d
```

### 9.2 启动前后端

```bash
cd /home/yenan/projects/fault/deploy
docker compose -f docker-compose.app.yaml up -d --build
```

### 9.3 停止前后端

```bash
cd /home/yenan/projects/fault/deploy
docker compose -f docker-compose.app.yaml down
```

### 9.4 停止数据库

```bash
cd /home/yenan/projects/fault/deploy
docker compose -f docker-compose.db.v3.yaml down
```

## 10. 已知运维注意点

### 10.1 不要默认重建 backend 镜像

`backend/Dockerfile` 里会重新安装：

- `torch`
- `cryptography`

完整 `docker compose up -d --build backend` 可能因为：

- 镜像源
- pip 网络

导致构建失败或很慢。

如果只是改了单个 Python 文件，热替换通常更快：

- `docker cp`
- `docker restart`

但这只适合临时修复，不适合长期发布。

### 10.2 本地脚本和容器不要混用 env

宿主机跑脚本时，优先确认：

- 加载的是 `.env.local`
- 不是容器内地址的 `.env`

## 11. 下一个会话优先事项

新会话继续时，优先按这个顺序确认：

1. 当前要导入的新嵌入式日志数据放在哪个目录
2. 这批数据是否需要先通过 `data_pipeline/scripts` 进入主库
3. 当前要修改的是 `backend` 还是 `frontend`
4. 前后端要展示的是 run 列表、case 列表，还是单条日志详情
5. 是否需要只刷新指定 run，而不是全量重跑 `04`
6. 是否需要重建 `05` 向量库和 `06` 图谱
7. 是否要清理旧的 NuttX 历史 run

如果没有特别说明，默认假设：

- 后端代码改 `backend`
- 数据脚本改 `data_pipeline/scripts`
- 当前数据库是 `fault_diagnosis`
- 当前 Mongo 库是 `fault_diagnosis`
- 当前阶段目标是“让新嵌入式日志数据可入库、可查询、可展示”

## 12. 新会话建议起点（承接本次变更）

建议新会话先按下面顺序做 5 分钟回归确认：

1. 访问 `http://localhost:8080/#/data-processing/logs`，确认列表可见、筛选分页正常。
2. 点击任意 run 进入详情，确认 URL 为 `#/data-processing/logs/:runId`。
3. 点击详情返回按钮，确认回到 `#/data-processing/logs`。
4. 访问旧地址 `#/log-analysis/analysis`，确认自动跳转到新地址。

若以上都正常，再继续后续阶段（例如：前端交互细节优化、`log-analysis` 菜单进一步收敛、下一阶段联调/回归）。
