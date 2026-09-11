# 集成方案 A（方案 3 单库版）：故障定位主系统 + 日志数据标注子系统

> 本文件夹 `631_9.7/` 在集成方案 A 的基础上，把主系统与标注子系统的**两个数据库合并为一个**（方案 3）。
> 与 `631_9.4`（双库基线）相比，唯一改动是数据库拓扑 + `fault_types` 统一 + 标注表 `ann_` 前缀；
> 其余一切功能、页面、API 路由均不变。

## 这是什么

把 `main-system/`（主系统）和 `annotation-app/`（标注子系统）放到同一份 compose 里，让标注子系统通过 iframe 嵌入主系统的「数据预处理」入口。二者共用同一个 PostgreSQL 实例、同一个逻辑库 `fault_diagnosis`（方案 3）。

```
631_9.7/
├── docker-compose.yml              统一 compose（5 个服务）
├── docker-compose.override-jj.yml  junjiezuo 测试栈 override（隔离镜像 tag / 端口 / 项目名）
├── .env.example                    共用环境变量样例
├── README.md                       本文件
├── main-system/                    主系统（fault-6.5）
└── annotation-app/                 标注子系统
```

## 方案 3 做了什么（相对 631_9.4 双库基线）

| 改动 | 说明 |
|---|---|
| **单一 PostgreSQL 实例** | 删除 `postgres-annotate` 服务与其 `pgdata_annotate` 卷；主/标注共用 `postgres`，库名 `fault_diagnosis` |
| **`fault_types` 唯一化** | 表由主系统 `create_all` 建（`color_tag`、可空 `description`、`name` 唯一）并由主系统 seed；标注只通过 FK `ann_fault_type_suggestions.accepted_fault_type_id → fault_types(id)` 引用，不建、不种、不迁移该表 |
| **标注表 `ann_` 前缀** | 11 张标注表全部改名（`ann_dataset_packages` / `ann_import_tasks` / `ann_source_log_files` / `ann_source_log_lines` / `ann_slice_tasks` / `ann_slice_windows` / `ann_slice_window_lines` / `ann_annotations` / `ann_annotation_recommendations` / `ann_fault_type_suggestions` / `ann_window_analyses`），避免与主系统撞名 |
| **标注 schema 聚合** | 标注 17 个历史迁移合并为**一个**初始迁移 `20260905_0001_merged_annotation_schema.py`（`down_revision=None`）；旧迁移文件已删 |
| **Alembic 版本表隔离** | 标注用 `alembic_version_annotate`（`env.py` 的 `version_table`），主系统用 `alembic_version`，互不覆盖 |
| **启动顺序修正** | `backend-annotate` `depends_on`：`backend`（healthy）+ `postgres`（healthy）；给主 `backend` 加了 healthcheck（curl `/health`），从而保证标注迁移执行前 `fault_types` 一定已建好 |

### 数据结构三分类（本次一并理清）

标注子系统的上传数据按 `data_kind` 分成三类，落在 `DatasetPackage` / `SourceLogFile` 表上：

- **`structured`**（结构化）：结构规整、可索引的内容（预留，通常不会走日志导入）
- **`semi_structured`**（半结构化，默认）：日志文本，带时间戳/级别/字段，可切片成时间窗并逐窗标注
- **`unstructured`**（非结构化）：报告/说明等自由文本（预留）

## 服务拓扑

```
浏览器
  │ :8081  ──▶ frontend-main (nginx) ─┬─ /                   主系统 SPA
  │                                   ├─ /api/                backend (主)
  │                                   ├─ /annotate/           frontend-annotate
  │                                   └─ /annotate-api/v1/    backend-annotate
  │ :5001  ──▶ backend (主系统 FastAPI，可直接打开 /api/docs)

数据库：
  postgres  唯一实例，主/标注共用逻辑库 fault_diagnosis
            主系统表（fault_types 等） + 标注 ann_* 表 + 两张 Alembic 版本表
            （alembic_version / alembic_version_annotate）
```

`fault_types` 由主系统独占；标注侧以 `ann_` 前缀隔离自己的表。API 路径 `/api/v1/*`（主）与 `/annotate-api/v1/*`（标注）完全分开。

## 启动（jj 测试栈）

```bash
cd /home/junjiezuo/631-fault/631_9.7
cp -n .env.example .env       # 可选；compose 自带兜底
# 务必同时加载 override，否则会与生产 631/ 栈冲突（镜像 tag / 端口 / 项目名）
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml \
  up -d --build postgres backend frontend-main backend-annotate frontend-annotate
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml ps
```

打开浏览器：

- 主系统首页：`http://localhost:8081`
- **数据预处理**（iframe 嵌入标注子系统）：`http://localhost:8081/#/data-processing/preprocess`
- 直接访问标注子系统（调试）：`http://localhost:8081/annotate/`
- 主系统 Swagger：`http://localhost:5001/api/docs`
- 标注后端健康检查（同源）：`http://localhost:8081/annotate-api/v1/health`

首启顺序：`postgres`(healthy) → `backend`(healthy，`create_all` 建 `fault_types` + seed) → `backend-annotate`（跑 `alembic upgrade head` 建 11 张 `ann_*` 表 + FK）。任一步失败，`docker compose logs backend-annotate` 可看迁移报错。

## 模型 API 配置

进入 `模型管理 -> API管理` 可新增、编辑、测试和启用 OpenAI 兼容接口。API Key 只在提交时传输，后端使用 `MODEL_API_ENCRYPTION_KEY` 加密后保存，列表接口不会返回明文。

运行时优先读取数据库中唯一的启用配置；没有启用配置时才使用 `LLM_*` 环境变量。修改或启用配置后，下一次 LLM 请求生效，无需重启后端。启用中的配置不能删除。

首次启动前生成并持久保存 Fernet key：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 关键设计点

- **单库**：`fault_types` 主系统独占，标注 `ann_*` 前缀隔离，`ann_fault_type_suggestions` 通过 FK `ON DELETE SET NULL` 引用它。

- **同源**：iframe 与主页面同源（`http://localhost:8081`），不需要 CORS / 不会被 SameSite cookie 拦。标注后端的 `allow_origins` 列表保持原样（仅 `localhost:5173`）即可。

- **API 路径分流**：主系统 `/api/v1/*` 与标注 `/annotate-api/v1/*` 完全分开，两边后端都不感知对方（仅共享 `fault_types` 这一张表）。

- **静态构建 + base 路径**：Vite `base: '/annotate/'` 让产物 `<script src="/annotate/assets/...">` 自带前缀；vue-router `createWebHistory('/annotate/')` 让前端历史 API push 的也是 `/annotate/...`，nginx 端 SPA 回退到 `/annotate/index.html`。

- **iframe 高度**：`Preprocess.vue` 用 `calc(100vh - 60px)`，按 AppLayout 顶栏 60px 估算；如观感不准，调这一行。

- **上传体积**：标注子系统支持上传压缩日志包（默认 1GB），主前端 nginx 已在 `/annotate-api/` 配 `client_max_body_size 1024m`。

## 回滚

- **回滚到集成 A（双库）**：用 `631_9.4/` 的对应文件替换 `631_9.7/` 的 `docker-compose.yml` / `docker-compose.override-jj.yml`，并恢复标注历史迁移链。
- **仅回滚 iframe 入口**：把 `main-system/frontend/src/router/index.js:76` 的组件改回 `@/views/DataProcessing/index.vue`，标题改回 `日志预处理`，重新构建主前端镜像即可。

## 已知约束 / 待确认

1. **构建依赖网络**：标注前端 Dockerfile 用 `npm ci` + 淘宝镜像；标注后端 Dockerfile 用清华 PyPI；如内网无外联，需替换源或预构建镜像。
2. **iframe 顶栏估算**：60px 是按主系统 AppLayout 通常布局估的，若实际不同需要在 `Preprocess.vue` 调。
3. **没改主系统 `app/main.py` 的 CORS**：iframe 同源没问题；如未来允许跨主机访问 iframe，要在标注后端 `allow_origins` 加上主系统域名。
4. **标注子系统的初始数据**：第一次启动会自动跑 alembic 建表；**`fault_types` 由主系统 seed**，标注页的故障类型下拉读的就是它。
5. **training-worker**：base compose 里保留（GPU 训练用），jj 测试栈不启动它；其 `POSTGRES_*` 环境变量已指向单库 `postgres`/`fault_diagnosis`。
