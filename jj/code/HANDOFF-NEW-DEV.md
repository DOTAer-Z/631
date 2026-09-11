# 交接文档：在 631_9.8 上开发新能力

> 本文件写给接手 631_9.8 源码、要**新增能力**的开发者。假定你已经跑通部署，需要理解
> 「在哪个目录、按什么分层、加一个功能要动哪些文件」。
>
> 与本仓库根目录 `README.md`、`main-system/CLAUDE.md`、`annotation-app/CLAUDE.md` 配合阅读。
> 那些文件讲「现状是什么」；本文件更像「从现状出发怎么改」。

---

## 1. 一句话定位

`631_9.8/` 是**集成方案 A 的方案 3（单库版）**：故障定位主系统（`main-system/`）+ 日志数据标注
子系统（`annotation-app/`），**源码级融合**成一个 Docker Compose 栈、共用一个 PostgreSQL 实例、
共用逻辑库 `fault_diagnosis`。

关键事实（必须先记住，否则改错地方）：

- **只有一个数据库**（`fault_diagnosis`），主系统表 + 标注 `ann_*` 表共存。
- **只有一个共享的权威表**：`fault_types`，由主系统创建 + seed，标注只通过 FK 引用它。
- **两侧 Alembic 版本表分开**：主系统 `alembic_version`，标注 `alembic_version_annotate`。
- **标注前端已源码级并入主前端**（不是 iframe）：标注的 13 个 view 作为主系统
  `data-governance/annotation` 的子路由挂载，编译进同一个主前端 bundle。
- 标注**前端**与主前端 **同 bundle**；标注**后端**仍是独立容器，只共享数据库 + `fault_types`。

---

## 2. 目录地图

```
631_9.8/
├── docker-compose.yml              统一 compose：postgres/backend/training-worker/frontend-main/backend-annotate/frontend-annotate
├── docker-compose.override-jj.yml  junjiezuo 测试栈 override（隔离镜像 tag / 端口 / 项目名 jj-test）
├── .env.example                    （缺失真实 LLM key 时复制参考用；真实 .env 含密钥，勿提交）
├── README.md                       集成方案 3 说明（含「方案 3 做了什么」表格）
├── HANDOFF.md                      历史交接（37KB，旧 MySQL + 真 Mongo + /home/yenan 路径，已过时）
├── k8s-deploy/                     9.7 参考的镜像 + K8s 部署包（不含源码）
├── release_20260906/               发布快照
├── main-system/                    主系统（有自己的 CLAUDE.md，先读它）
│   ├── backend/                    FastAPI + SQLAlchemy 2.0 + Alembic
│   │   ├── app/main.py             挂载所有 router 到 /api/v1（+ /api 调试路由）
│   │   ├── app/config.py           Settings（环境变量驱动）
│   │   ├── app/database.py         SessionLocal / get_db
│   │   ├── app/models/             ORM 模型（runs/cases/fault_types/mongo_docs...）
│   │   ├── app/schemas/            Pydantic 请求/响应模型
│   │   ├── app/api/v1/*.py         router（每域一文件）
│   │   ├── app/services/*.py       业务逻辑
│   │   ├── app/mongo_compat.py     PG-JSONB MongoDB 兼容层（log_entries/log_analysis_results 用）
│   │   └── docker-entrypoint.sh    等 PG → create_tables → seed → (AUTO_INGEST) → uvicorn
│   ├── frontend/                   Vue 3 SPA（被主 frontend Dockerfile 构建时也消费）
│   └── *.md                        部署说明 / 大模型接口说明 / model-training-runbook 等
└── annotation-app/                 标注子系统（有自己的 CLAUDE.md）
    ├── backend/
    │   ├── app/main.py             FastAPI，挂载 api_router 到 settings.api_v1_prefix（默认 /api/v1）
    │   ├── app/core/config.py      Settings
    │   ├── app/db/                 engine/session + models/（每表一文件）+ base.py
    │   ├── app/api/v1/*.py         router（annotations/dashboard/packages/slice_*/recommendations/fault_*）
    │   ├── app/services/*.py       业务逻辑（import/slice/recommend/annotation_push/main_system_client/...）
    │   ├── alembic/versions/20260905_0001_merged_annotation_schema.py  单一初始迁移
    │   └── docker-entrypoint.sh    等 PG → alembic upgrade head → uvicorn
    └── frontend/                   Vue 3 + TS + Pinia（被主前端 @annotation 别名直接 import）
```

---

## 3. 数据模型（`fault_diagnosis` 单库）

### 3.1 主系统表（`main-system/backend/app/models/`）

主要模型：`Run`、`Case`、`FaultType`、`DiagnosisRecord`、`PredictionRecord`、`DatasetImport`、
`ModelApiConfig`、`LogParseTask`、`TrainingTask`/`TrainingData`、`System`、`MonitorData` 等。

**两个高层语义要记住：**

- **`test_name` 在 `cases` 表上，不在 `runs` 上**。列表查询必须 `LEFT JOIN cases` 才能拿到
  `test_name`（见 `services/log_dataset_service.py`）。`runs` 表一个 `run` = 一次测试执行。
- **`mongo_docs` 是一张 JSONB 兼容表，不是真 Mongo**。`mongo_compat.py` 提供
  pymongo 风格的 `PgMongoClient`；`log_entries`、`log_analysis_results` 等文档集合实际存在
  `mongo_docs` 表（`collection` + `doc_id` + `run_id` + `doc` 列），datetime 以
  `{"__dt__": iso}` 形式封进 JSONB，读时还原。**不要**新增真 Mongo 依赖。

### 3.2 标注子系统表（11 张 `ann_*` 表，`annotation-app/backend/app/db/models/`）

线性主链路：

```
ann_dataset_packages → ann_import_tasks → ann_source_log_files → ann_source_log_lines
   → ann_slice_tasks → ann_slice_windows → ann_slice_window_lines → ann_annotations
```

扩展表：`ann_annotation_recommendations`（LLM 推荐缓存）、`ann_fault_type_suggestions`（LLM
提议的新故障类型，审核队列）、`ann_window_analyses`。

跨表共享的唯一一张表是 `fault_types`（**主系统所有**）。标注侧 `FaultType` 模型
（`app/db/models/fault_type.py`）只是这张共享表的只读映射，`ann_fault_type_suggestions`
通过 `accepted_fault_type_id → fault_types(id) ON DELETE SET NULL` 引用它。

### 3.3 硬约束（改模型时必须遵守）

| 约束 | 说明 | 触发文件 |
|---|---|---|
| 数据库是查询唯一事实源 | 磁盘只留原始压缩包 + 导出文件；切片/窗口/标注全在库 | 全局 |
| 时间戳是 **UTC epoch float**（秒） | 不是 datetime；窗口数学依赖它 | 标注全局 |
| 未解析出时间戳的行 `timestamp=None` | **排除在切片之外** | `import_worker` |
| 标注只留最新态，无历史版本 | `POST .../annotation` 按 `(slice_window_id, source_log_file_id)` upsert | `annotation_service` |
| 每窗口最多 1 整窗标注 + 每文件最多 1 文件级标注 | 由两个 **partial 唯一索引**强制（`postgresql_where` + `sqlite_where` 都要写，否则 create_all 测试与 Alembic 不一致） | `annotation.py` 模型 |
| `window_seconds` ∈ [1, 3600]，默认 300 | 切片 + 子窗口长度 | `slice_task` / `recommendation_service` |
| 标注 `anomaly_type` 必须命中 `fault_types` | `AnnotationService._normalize_anomaly_type` 拒绝字典外值 | `annotation_service` |
| LLM 推荐的故障类型两步流程 | 第一步强制匹配 `fault_types`；字典外名称进第二步（「是否真需新增」），确认后写 `ann_fault_type_suggestions`（status=pending），**不自动改 `fault_types`** | `recommendation_service` |
| 叶子窗口计数 | 所有统计/导航/dashboard 都按 `has_children == False`（叶子）计数 | `slice_window_service` / 各统计 |

---

## 4. 服务拓扑与路由分流

### 4.1 容器（`docker-compose.yml`）

```
浏览器
  │ :8081 ──► frontend-main (nginx)
  │              ├── /               主系统 SPA（含源码级融合的标注 view）
  │              ├── /api/           backend:8000（主系统）
  │              └── /annotate-api/v1/  backend-annotate:8000/api/v1/（nginx 前缀替换）
  │ :5001 ──► backend（主系统 FastAPI，直接 Swagger /api/docs）

数据库：
  postgres  唯一实例，逻辑库 fault_diagnosis
            主系统表 + 标注 ann_* 表 + alembic_version / alembic_version_annotate
```

- **前端融合**：标注前端不再经 `/annotate/` 独立站。nginx 已移除 `/annotate/` 反代块；
  主系统 nginx 只保留 `/annotate-api/` 反代到 `backend-annotate`（路径重写
  `/annotate-api/v1/` → `/api/v1/`）。
- **标注前端 API base**：`VITE_API_BASE_URL=/annotate-api/v1`（标注 frontend Dockerfile 固化）。
- **只有一个 SQLAlchemy engine 连接 PG**：主系统经 `POSTGRES_HOST/PORT/USER/PASSWORD/DB`，
  标注后端经 `DATABASE_URL`（`postgresql+psycopg://user:pass@postgres:5432/fault_diagnosis`）。
- **主前端 build context = 仓库根**：因为 vite `@annotation` 别名指向
  `../../annotation-app/frontend/src`（在 `main-system/frontend` 之外）。改标注前端源码必须重建
  **主系统**前端镜像，而不是标注前端镜像（`frontend-annotate` 已不承载融合 UI）。

### 4.2 API 路由（两边完全分开，互不感知）

| 子系统 | 浏览器前缀（经 nginx） | 后端实际路径 | Swagger |
|---|---|---|---|
| 主系统 | `/api/v1/*` | `/api/v1/*` | `:5001/api/docs` |
| 标注子系统 | `/annotate-api/v1/*` | `/api/v1/*`（前缀被 nginx 剥掉） | `:8081/annotate-api/v1/...` |

两边后端唯一真正的接触点是 `fault_types` 这一张表（主写、标注只读/只 FK 引用），以及通过
**HTTP 内部调用**做跨系统桥接（见第 6 节）。

### 4.3 主系统 router 清单（`main-system/backend/app/api/v1/`）

- `diagnosis` `prediction` `knowledge_base` `graph` `overview` `log_analysis`
  `data_processing` `data_import` `embedding` `internal_llm_gateway` `model_api_config`
  `model_training` `model_training_adapters`（业务）
- `test_data_flow` `log_pipeline`（调试，挂在 `/api` 下，不用 `/api/v1`）

`log_analysis.py` 是最大的一块（日志列表 CRUD、上传、解析、分析、batch、导出、
`POST /log-analysis/annotated-run` 反向桥）。`knowledge_base.py` 同时托管
`/fault-types` CRUD 与知识库 `/logs`、`/knowledge-cases`、annotation 导入相关。

### 4.4 标注子系统 router 清单（`annotation-app/backend/app/api/v1/`）

`annotations` `dashboard` `packages` `import_tasks` `slice_tasks` `slice_windows`
`recommendations` `fault_types` `fault_type_suggestions` `health`。
统一经 `router.py` 的 `api_router` 挂到 `/api/v1`。

标注 routes 详细清单（`GET/POST...`）见 `annotation-app/README.md` 的「API 概览」。

---

## 5. 前后端分层约定（新增能力要照此走）

### 5.1 主系统后端

```
app/api/v1/<域>.py        薄 router：Depends(get_db) 拿 Session，调 service，转 schema
app/services/<域>_service.py   业务逻辑（构造查询、调 embedding/LLM/vector store）
app/models/<表>.py        SQLAlchemy ORM
app/schemas/<域>.py       Pydantic 请求/响应
app/config.py             Settings（env 驱动）
```

- Session 经 `get_db()`（`app/database.py`）；service 通常 `__init__` 接收 session。
- 文档型集合（log_entries 等）走 `mongo_compat`（`get_mongo_db()`），**不要**直接写 SQL。
- 向量检索走本地 Chroma（`services/vector_store.py`）+ `LocalEmbeddingService`（bge-small-zh-v1.5）。
  无外部 Chroma server。

### 5.2 标注子系统后端

```
app/api/v1/<resource>.py  薄 router（Depends(get_db) 在 api/v1/deps.py），构造 *Service
app/services/<域>_service.py  业务逻辑，service __init__ 收 session 并自己 commit
app/schemas/<域>.py        Pydantic
app/db/models/<表>.py      ORM（已在 db/models/__init__.py 注册进 metadata）
app/core/config.py         Settings
```

**异步任务**：用 **daemon `threading.Thread`**（不是 FastAPI `BackgroundTasks`，不是 Celery）。
见 `ImportService.start_import_task_async` / 切片类似：线程内**新建一个绑定同一 engine 的 session**。
注意：**in-memory SQLite 下异步路径被跳过**（per-connection 内存库别的线程看不到），所以测试里
worker 同步跑。

### 5.3 标注子系统前端（作为主系统子路由）

- **路由**：`annotation-app/frontend/src/router/index.ts` 导出 `annotationRoutes`（13 个叶子路由）。
  它们作为主系统 `data-governance/annotation` 的 `children` 被挂载。
  - `meta.activeMenu` —— 标注**页内**水平导航（`AnnotationSectionShell.vue`）高亮用；
  - `meta.mainActiveMenu` —— 主系统**侧边栏**高亮用，固定 `/data-governance/annotation`；
  - `meta.hidden` —— 标注页内导航不显示（详情/列表等内页）。
- **页内导航**：`main-system/frontend/src/views/DataProcessing/AnnotationSectionShell.vue`，
  水平 `el-menu` + `<router-view/>`，用 `annotationRoutes` 中非 hidden 项生成菜单。
- **API 封装**：`annotation-app/frontend/src/api/http.ts` axios 实例，base URL 来自
  `VITE_API_BASE_URL`（融合态为 `/annotate-api/v1`）。**LLM 调用单独设 `timeout`**（例如
  推荐/分析接口 `timeout=180000`），别用全局 10s。
- **类型**：`@/types/<域>.ts` 定义前后端接口类型；`npm run build` 前置 `vue-tsc --noEmit`，
  **类型错误直接 build 失败**。改标注前端后跑 `vue-tsc --noEmit` 确保类型绿。
- **新增一个标注内页**：在 `annotationRoutes` 加一条（相对路径 + name 全局唯一 + meta 三件套），
  无需改主系统 router——children 展开时自动纳入。但如果这个页面要在主系统侧边栏出现，注意
  `mainActiveMenu` 统一指向 `data-governance/annotation`。

### 5.4 主系统前端（宿主 shell）

- `main-system/frontend/src/router/index.js` 是主系统路由树。
  `data-governance/annotation` 就是融合入口；标注内页在 `annotationRoutes` 里加，不在此文件加。
- 菜单模块：Overview / KnowledgeBase / FineTuning / KnowledgeGraph / Diagnosis / Prediction /
  DataImport / DataProcessing / LogAnalysis（见源码 router）。API 走 `@/utils/request.js`
  （basePath 运行时从 `window.__APP_CONFIG__.basePath` 解析，哈希路由，token 走 `X-Token`）。
- 前端单测：`node --test tests/logAnalysisTransforms.test.mjs`。

---

## 6. 跨系统桥接（最容易踩坑的一层）

两侧后端各自独立，只通过 HTTP + 共享 `fault_types` 协作。以下是**全部**桥接点：

### 6.1 主系统 → 标注子系统（「从标注典型案例导入」）

- 主系统 `services/annotate_client.py` 调标注后端，base = `ANNOTATE_BACKEND_URL`
  （compose 默认 `http://backend-annotate:8000/api/v1`，config 里 `ANNOTATE_TIMEOUT=60`）。
- 只读：拉取标注 `ann_annotations` 案例到主系统知识库（`knowlede-base/import-annotation-cases`）。

### 6.2 标注 → 主系统 1：LLM 网关

标注后端**不直接连 LLM**，而是调主系统的内部网关：

- 标注 config：`llm_gateway_url`（`LLM_GATEWAY_URL`，默认
  `http://backend:8000/api/v1/internal/llm/chat`）+ `internal_llm_gateway_token`。
- 主系统 `internal_llm_gateway.py` 收 `InternalLLMChatRequest`，走 `LLMRuntimeConfigProvider`
  （见 6.4），返回 `InternalLLMChatResponse`。
- **token 必须两端一致**（`INTERNAL_LLM_GATEWAY_TOKEN`），否则标注侧 LLM 功能拿不到响应。
- config 里标注的 `llm_enabled` 由 `llm_gateway_url` + token 决定（**不是** `ENABLE_LLM`）。

### 6.3 标注 → 主系统 2：标注推送（自动 push 到「文件选择」）

- 标注保存/更新/删除窗口标注后，`annotation_push_service.py` 经 `MainSystemClient.push_annotated_run`
  调主系统 `POST /log-analysis/annotated-run`。
- `main-system/.../annotated_run_service.py` 把该窗口落成一条 `runs` + 一批 `log_entries`
  （mongo_compat），使窗口能出现在主系统「文件选择」并参与诊断/预测/RAG。
- `run_id = "annot_pkg{package_id}_win{window_id}"`（幂等：主侧先删后写，重复推送覆盖）。
- 自动推送开关：`ANNOTATION_AUTO_PUSH_MAIN=true`（默认开），`ANNOTATION_AUTO_PUSH_TIMEOUT_SECONDS=10`。
  **自动推送失败不阻断标注保存**（标注仍成功），只是该窗口暂时缺席文件选择，可手动「推送到主系统分析」。

### 6.4 LLM 运行时配置（主系统侧，**很重要**）

主系统 LLM 由 `services/llm_runtime_config.py` 的 `LLMRuntimeConfigProvider.resolve()` 决定：

1. **优先读数据库** `model_api_configs` 表中 `active` 的那一行（含 base_url/api_key/model/
   **max_output_tokens**）；API key 用 `MODEL_API_ENCRYPTION_KEY`（Fernet）解密，列表接口不返回明文。
2. 若**无 active 数据库行**，才回退到 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` 环境变量（受
   `ENABLE_LLM` 门控，且 `allow_environment_fallback`）。
3. 修改/启用数据库配置后**下一次请求即生效，无需重启后端**；启用中的配置不能删。

> **max_tokens 陷阱**：推理模型（如 deepseek-reasoner）若返回空 content，多半是
> `model_api_configs` 行的 `max_output_tokens` 太小。提高的是**数据库行**里的值，
> 不是 `LLM_MAX_OUTPUT_TOKENS` env（那个只在回退路径生效）。

内部全量：`MLM_API_KEY` 加密字段 `api_key_ciphertext`；网关会 `redact` 响应里出现的 key。

---

## 7. LLM 功能如何「静默降级」

| 子系统 | 启用条件 | 降级表现 |
|---|---|---|
| 主系统 | `ENABLE_LLM=True` 且 `LLM_BASE_URL/API_KEY/MODEL` 非空，**或无 active DB 配置** | LLM 辅助功能（日志解析/诊断 RAG 兜底/预处理/预测）静默回退到规则或返回「未配置」错误（数据预处理返回 503 + 明确 message） |
| 标注子系统 | `LLM_GATEWAY_URL` + `INTERNAL_LLM_GATEWAY_TOKEN` 都非空（`llm_enabled`） | 时间戳推断用规则解析器；故障推荐/多错误分析返回可用的降级结果；**不报错** |

设计原则：LLM 是**增强**，不是主链路的依赖。新增的 LLM 辅助能力必须同样做到「未配置时静默降级，
不阻塞主链路」。

---

## 8. 新增一个能力（端到端清单）

以「给标注子系统加一个新资源 /new-capability」为例，克隆一个现有域（如 `packages` 或
`recommendations`）的骨架即可。**顺序建议**：

**后端（标注子系统）**

1. **模型**：`app/db/models/<新表>.py` 定义 ORM；`db/models/__init__.py` 里 import 注册进
   `Base.metadata`（**否则测试 `create_all` 看不到**）。
2. **迁移**：在 `alembic/versions/` 新增一个文件，`down_revision = "20260905_0001"`。
   注意：**测试用 SQLite `create_all`，不用 Alembic**；所以模型 + 迁移**都要有**，两者一致。
   - 标注侧建表务必加 `ann_` 前缀，避免与主系统撞名。
   - 若要引用 `fault_types`，用 FK `→ fault_types(id)`，并理解它由主系统建好（见启动顺序）。
3. **schema**：`app/schemas/<新域>.py` 定义请求/响应 Pydantic 模型。
4. **service**：`app/services/<新域>_service.py`，构造函数收 session 并自管 commit；
   业务逻辑放这里，router 保持薄。
5. **router**：`app/api/v1/<新域>.py` 定义 `APIRouter()` + 端点；在 `router.py` 的 `api_router`
   `include_router(...)`。
6. **测试**：`backend/tests/` 加用例（SQLite in-memory `create_all`，手造 Fixture，注入
   `tests/fakes.FakeLLMClient` 别打真网络）。跑 `python -m pytest -q`。

**后端（主系统）** —— 若新能力影响主系统：模型 → `app/models/`；业务在
`app/services/<域>_service.py`；router 在 `app/api/v1/<域>.py` 并在 `main.py`
`include_router(..., prefix="/api/v1", tags=[...])`。文档型数据走 `mongo_compat`，向量走
`vector_store.py`。

**前端（标注子系统）**

7. **API**：`frontend/src/api/<新域>.ts` 封装 axios 调用。
8. **类型**：`frontend/src/types/<新域>.ts`。
9. **Store**：`frontend/src/stores/<新域>Store.ts`（Pinia）。
10. **View**：`frontend/src/views/<新域>/<Xxx>View.vue`。
11. **路由**：在 `annotationRoutes`（`frontend/src/router/index.ts`）加一条相对路径 + 全局唯一
    `name` + `meta`（`activeMenu` / `mainActiveMenu` / 视需要 `hidden`）。
    **不需要动主系统 router。**
12. **测试**：`__tests__/*.spec.ts`（vitest + `@vue/test-utils` + jsdom；Element Plus 用
    `src/test-utils/elementStubs.ts` stub）。`npm test`。**`npm run build` 会跑 vue-tsc，类型必须绿。**

**最终验证**

- 后端测试全绿、前端测试全绿、前端 `npm run build` 通过。
- 用 jj 测试栈起全栈（见第 9 节），手工走一遍新链路。

---

## 9. 开发 / 测试 / 部署命令

### 9.1 用 jj 测试栈起全栈（**不要**动生产 `631/` 栈）

```bash
cd /home/junjiezuo/631-fault/631_9.8
# 务必同时加载 override，否则覆盖生产栈（同名镜像/端口/项目名）
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml \
  up -d --build postgres backend frontend-main backend-annotate frontend-annotate
docker compose -f docker-compose.yml -f docker-compose.override-jj.yml ps
```

访问：

- 主系统首页：`http://localhost:8081`
- 标注（融合入口）：`http://localhost:8081/#/data-governance/annotation`
- 主系统 Swagger：`http://localhost:5001/api/docs`
- 标注后端健康（经主 nginx 同源）：`http://localhost:8081/annotate-api/v1/health`

**启动顺序**：`postgres`(healthy) → `backend`(healthy，`create_all` 建 `fault_types` + seed) →
`backend-annotate`（跑 `alembic upgrade head` 建 11 张 `ann_*` 表 + FK）。标注依赖主后端健康是
**因为** `ann_fault_type_suggestions` 的 FK 指向主系统建的 `fault_types`。任一步失败，看
`docker compose logs backend-annotate`。

### 9.2 单测

```bash
# 主系统后端
cd main-system/backend && python -m pytest tests/
# 单个
python -m pytest tests/test_log_dataset_service.py::test_name -q
# 主系统前端单测
cd main-system/frontend && node --test tests/logAnalysisTransforms.test.mjs

# 标注后端
cd annotation-app/backend && pip install -e '.[dev]' && python -m pytest -q
# 标注前端
cd annotation-app/frontend && npm ci && npm test && npm run build   # build 含 vue-tsc
```

### 9.3 本地 dev（不打包）

```bash
# 主系统后端（需要可达的 PG，配 env）
cd main-system/backend && uvicorn app.main:app --reload --port 8000
# 标注后端（默认 sqlite:///./app.db，先 alembic upgrade head）
cd annotation-app/backend && uvicorn app.main:app --reload
# 标注前端 dev（若只看标注，独立跑；融合态主前端才带它）
cd annotation-app/frontend && npm run dev          # :5173，VITE_API_BASE_URL 默认 /api
# 主前端 dev（深融合会 import 标注源码，需要 @annotation 别名可达）
cd main-system/frontend && npm install && npm run dev   # :5173，proxy /api → :8000
```

> 注意：本地 dev 时标注前端独立起，它的 API base 默认 `/api`（指向主后端 8000），
> 与融合态 `/annotate-api/v1` 不同。若在本地调试标注并要打标注后端，按需设
> `VITE_API_BASE_URL`。

### 9.4 部署（K8s / 镜像包）

见 `k8s-deploy/README.md`、`k8s-deploy/README-docker.md`、`k8s-deploy/README-k8s.md`。
对应 9.7 的镜像 + K8s 包；如为 6 服务 K8s 包（含 `postgres-annotate`）是**双库版**，已被 9.8
单库版取代。若目标是 9.8 单库部署，用与 `docker-compose.yml` 相同的单 postgres 拓扑。

---

## 10. 环境变量清单（两个后端合并视角）

### 10.1 主系统（`main-system/backend/app/config.py`）

| 变量 | 用途 / 默认 |
|---|---|
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | 主库；默认 `127.0.0.1:5433/postgres/secret/fault_diagnosis`。compose 里设 `postgres:5432`。若设 `DATABASE_URL` 优先 |
| `MONGO_DB` | JSONB 兼容层逻辑库名，指向同一 `fault_diagnosis` |
| `ENABLE_EMBEDDING` / `LOCAL_MODEL_PATH` / `EMBEDDING_DEVICE` | 本地 embedding（bge-small-zh-v1.5）。远程备选 `EMBEDDING_API_BASE/API_KEY/MODEL` |
| `SIMILARITY_THRESHOLD` | 默认 0.8，RAG「快速通道」阈值 |
| `CHROMA_PERSIST_PATH` | 本地 Chroma 落盘目录，默认 `/app/outputs/vector_store/chroma`。挂到 `backend_outputs` 卷 |
| `ENABLE_LLM` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | LLM 环境回退，默认 `ENABLE_LLM=True`，compose 显式 `False`；三者齐才启用 |
| `LLM_TIMEOUT` / `LLM_MAX_OUTPUT_TOKENS` | 仅环境回退路径；**DB active 行优先**（见 6.4） |
| `MODEL_API_ENCRYPTION_KEY` | Fernet key，加密 DB 里的 API key。**生成一次别换**（否则解不开） |
| `INTERNAL_LLM_GATEWAY_TOKEN` | 内部 LLM 网关共享 token，**两端一致** |
| `ANNOTATE_BACKEND_URL` / `ANNOTATE_TIMEOUT` | 主→标注（从标注典型案例导入），默认 `http://backend-annotate:8000/api/v1` |
| `DATA_IMPORT_ROOT/MAX_FILE_SIZE/ALLOWED_EXTS/AUTO_INGEST` | 数据导入；解析后入库 `runs/cases/log_entries` |
| `TRAINING_*` | GPU 训练 worker 相关 |

### 10.2 标注子系统（`annotation-app/backend/app/core/config.py`）

| 变量 | 用途 / 默认 |
|---|---|
| `DATABASE_URL`（接受 `DB_URL`/`db_url` 别名） | 单库连接串，默认 `sqlite:///./app.db` |
| `STORAGE_ROOT` | 原始包/导出文件根，默认 `/data/storage`（挂 `annotate_storage` 卷） |
| `MAX_UPLOAD_BYTES` | 上传上限，默认 20GB（compose 里 21474836480）；主 nginx `/annotate-api/` 配 `client_max_body_size 20480m` |
| `APP_ENV` / `LOG_LEVEL` | 环境/日志级别 |
| `LLM_GATEWAY_URL` / `INTERNAL_LLM_GATEWAY_TOKEN` | 标注→主系统 LLM 网关；`llm_enabled` 取决于二者 |
| `LLM_GATEWAY_TIMEOUT_SECONDS` | 默认 180 |
| `MAIN_SYSTEM_BACKEND_URL` | 标注→主系统（「从主系统导入」拉数据导入列表/下载），默认 `http://backend:8000/api/v1` |
| `ANNOTATION_AUTO_PUSH_MAIN` / `ANNOTATION_AUTO_PUSH_TIMEOUT_SECONDS` | 标注保存后自动推主系统，默认 true / 10s |
| `import_data_root_markers` | 找 data 根目录的 marker，默认 `["data"]` |
| `recommendation_max_log_lines` / `recommendation_reference_fault_types` | 推荐采样行数与参考故障类型 |
| `sample_lines_per_file/cpu_cap`、`timestamp_convergence_threshold` | 时间戳推断收敛参数 |

> 注：`annotation-app/.env.example` / `core/config.py` 中可能残留旧双库变量名
> （`ANNOTATE_DB`/`ANNOTATE_USER` 等）。**单库版忽略它们**，以 `DATABASE_URL` 为准。

### 10.3 `.env`（仓库根，真实密钥）

`631_9.8/.env` 含真实 `LLM_API_KEY`、`LLM_BASE_URL`、`MODEL_API_ENCRYPTION_KEY`、
`INTERNAL_LLM_GATEWAY_TOKEN`、`POSTGRES_PASSWORD`——**不要提交到 GitHub / 不要打包进交付物**。
交付 `.env.example` 用占位符。

---

## 11. 历史与「别看」的东西

- **`HANDOFF.md`（37KB）**：旧 MySQL + 真实 Mongo + `/home/yenan/...` 路径 + 独立
  `data_pipeline/scripts` 编号 ETL，**已过时**。看 `config.py` / `database.py` / 本文件 / CLAUDE.md。
- **`main-system/handoff.md`**：同样历史。别当成现行架构。
- **`main-system/.../Dockerfile.training`**：训练 worker 镜像，其 compose build context 指向
  `631_9.7/main-system/backend/Dockerfile.training`（**过期路径**），训练 worker 在 jj 测试栈**不启动**
  （与生产栈 GPU device 冲突）。开发不碰它。
- **`annotation-app/INTEGRATION_HANDOFF.md`、`HANDOFF_API_SUMMARY.md`**：旧双库时期写给「集成到别的
  项目」的，其迁移链 head `20260615_0011` 与 `ann_` 前缀**均已过时**（9.8 已折叠为 `20260905_0001`）。
- **`dist-deploy/` 9.7 双库包**：是 9.7 双库版的部署包（含 `postgres-annotate`），不是 9.8 单库。
  9.8 已成型的单库部署包在 `631_9.8depoly/`（含 k8s manifests，单 postgres）。

---

## 12. 新能力开发速查

**改标注前端** → `annotation-app/frontend/...`，但**重建主系统前端镜像** `frontend-main`
（@annotation 别名把它编进主 bundle）；`frontend-annotate` 镜像不承载融合 UI。

**改标注后端** → `annotation-app/backend/...`，rebuild `backend-annotate`。

**改主系统** → `main-system/backend` 或 `main-system/frontend`，rebuild `backend` / `frontend-main`。

**跨库共享表 `fault_types`** → 只能在主系统侧改 `app/models/fault_type.py` + `seed.py`。
标注侧只读映射，不要建/改/迁移它。

**所有时间戳** → 存 UTC epoch float（秒）。前端比大小/排序直接比数值。

**LLM 辅助** → 必须「未配置静默降级」；标注侧走网关；主系统优先读 DB 行。
