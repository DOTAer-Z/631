# 日志标注子功能集成交付说明

这份文档用于把当前仓库中的“日志数据标注子功能”交付给另一位开发者，供其集成到现有前后端项目中。

## 1. 你需要交付给对方的内容

### 1.1 源码

你已经计划直接提交以下两部分源码，这个方向是对的：

- `backend/`
- `frontend/`

这两部分已经包含：

- 后端 API、服务层、数据库模型、Pydantic schema、Alembic 迁移
- 前端页面、路由、Pinia store、API 请求封装、类型定义、测试

### 1.2 数据库迁移包

数据库迁移包不要只给“某一个最新 migration 文件”，而要把整个 Alembic 链条一起交付。建议直接交付：

- `backend/alembic/`
- `backend/alembic.ini`
- `backend/app/db/models/`
- `backend/app/db/base.py`
- `backend/app/db/session.py`

原因：

- `backend/alembic/versions/` 里保存的是完整迁移历史，目标项目需要能从旧库升级到当前结构。
- `alembic.ini` 是 Alembic 入口配置，别人本地/测试/CI 跑迁移时要用。
- `app/db/models/` 和 `app/db/base.py` 是当前 ORM 模型定义，便于对方理解每张表结构和关系。
- `app/db/session.py` 说明当前 SQLAlchemy 会话与 engine 的接法，便于对方接入自己的工程。

当前迁移链如下：

- `20260531_0001_baseline.py`
- `20260531_0002_dataset_packages.py`
- `20260531_0003_slice_tasks_windows.py`
- `20260601_0004_annotations.py`
- `20260601_0005_redesign_log_annotation_platform.py`
- `20260602_0006_import_task_status_alignment.py`
- `20260602_0007_annotation_anomaly_type_free_text.py`
- `20260606_0008_annotation_recommendations.py`
- `20260613_0009_fault_types.py`
- `20260613_0010_window_seconds.py`
- `20260615_0011_fault_type_suggestions.py`

当前 Alembic head：

```text
20260615_0011
```

### 1.3 建议一并交付的说明文件

建议把以下说明文件也一起交付：

- `CLAUDE.md`
- `README.md`
- `deploy/.env.example`
- 本文件 `INTEGRATION_HANDOFF.md`

说明：

- `CLAUDE.md` 比当前 `README.md` 更新，更接近最新真实实现。
- `README.md` 适合让对方快速理解项目目标和基础启动方式。
- `deploy/.env.example` 可作为环境变量样例。

## 2. 这个子功能的边界

这部分功能不是一个简单页面，而是一条完整的前后端业务链路。

后端核心能力：

- 上传压缩日志包
- 异步导入原始日志到数据库
- 基于时间窗口切片
- 浏览窗口详情 / 树结构 / 分页日志 / 窗口内搜索
- 单窗口唯一标注
- 已标注记录编辑 / 删除 / 导出
- 故障类型字典管理
- LLM 故障推荐
- LLM 提出的新故障类型建议审核
- LLM 自适应时间戳推断

前端核心页面：

- 首页 Dashboard
- 数据包管理
- 数据切片工作台 / 切片任务列表 / 窗口浏览
- 数据标注工作台 / 已标注记录
- 故障类型管理
- 故障类型建议审核

## 3. 数据模型与核心约束

主数据链路：

```text
dataset_packages
-> import_tasks
-> source_log_files
-> source_log_lines
-> slice_tasks
-> slice_windows
-> slice_window_lines
-> annotations
```

扩展表：

- `annotation_recommendations`
- `fault_types`
- `fault_type_suggestions`

必须明确告诉对方的硬约束：

- 数据库是查询唯一事实来源，磁盘只保留原始压缩包和导出文件
- 标注是“每窗口一条最新记录”，没有历史版本
- 时间戳统一存储为 UTC epoch float
- 未解析出时间戳的原始行不会参与切片
- LLM 功能未配置时会静默降级，不阻塞主链路
- 标注里的 `anomaly_type` 现在受 `fault_types` 表约束
- LLM 推荐分两步：先强制匹配 `fault_types`；如果模型提出未定义类型，再写入 `fault_type_suggestions` 等待人工审核
- 切片窗口大小使用 `window_seconds`，范围 `[1, 3600]`，默认 300 秒

## 4. 后端需要集成的内容

### 4.1 路由入口

当前后端把 API 统一挂在：

```text
/api/v1
```

主入口文件：

- `backend/app/main.py`
- `backend/app/api/v1/router.py`

当前已接入路由模块：

- `health`
- `dashboard`
- `packages`
- `import-tasks`
- `slice-tasks`
- `slice-windows`
- `annotations`
- `recommendations`
- `fault-types`
- `fault-type-suggestions`

### 4.2 后端建议对方重点接入的目录

- `backend/app/api/`
- `backend/app/services/`
- `backend/app/schemas/`
- `backend/app/db/`
- `backend/app/core/`

如果对方不是整仓库接入，而是拆模块并入现有项目，至少要确保这些层一起迁移，不要只拿 API 文件。

## 5. 前端需要集成的内容

### 5.1 页面与路由

当前前端主页面和隐藏页面在：

- `frontend/src/router/index.ts`

公开主入口页面：

- `/dashboard`
- `/packages`
- `/slicing`
- `/annotation`

隐藏辅助页面：

- `/health`
- `/packages/:id`
- `/packages/:id/slice-tasks`
- `/packages/:id/slice-tasks/:taskId/windows`
- `/annotation/records`
- `/annotation/fault-types`
- `/annotation/fault-type-suggestions`

### 5.2 前端建议对方重点接入的目录

- `frontend/src/api/`
- `frontend/src/stores/`
- `frontend/src/types/`
- `frontend/src/views/`
- `frontend/src/router/`
- `frontend/src/layouts/`

说明：

- `api/` 是 axios 请求封装
- `stores/` 是 Pinia 状态管理
- `types/` 是前后端接口类型
- `views/` 是业务页面
- `router/` 决定这些页面如何并入对方现有导航

## 6. 环境变量清单

建议你把下面这份配置项说明直接交给对方。

### 6.1 后端必需

- `DATABASE_URL`
- `STORAGE_ROOT`
- `MAX_UPLOAD_BYTES`

说明：

- `DATABASE_URL`：Postgres 连接串
- `STORAGE_ROOT`：原始压缩包、导出文件等存储根目录
- `MAX_UPLOAD_BYTES`：上传大小限制

### 6.2 后端可选但建议说明

- `APP_ENV`
- `LOG_LEVEL`
- `DB_URL`
- `db_url`

说明：

- 当前代码兼容 `DATABASE_URL` / `DB_URL` / `db_url`

### 6.3 LLM 相关

- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_TIMEOUT_SECONDS`
- `LLM_MAX_OUTPUT_TOKENS`

说明：

- 只有 `LLM_BASE_URL + LLM_API_KEY + LLM_MODEL` 同时非空时，LLM 功能才会启用
- 未配置时，自适应时间戳和故障推荐会静默降级
- `LLM_BASE_URL` 必须是 OpenAI-compatible chat completions 的 base URL；代码会请求 `{LLM_BASE_URL}/chat/completions`

### 6.4 前端

- `VITE_API_BASE_URL`

说明：

- 前端所有 HTTP 请求都走这个 base URL
- 当前默认示例值是：

```text
http://localhost:8000/api/v1
```

## 7. 对方需要知道的 API 范围

下面是当前功能的主要接口组，方便对方做网关、权限和菜单整合。

### 7.1 Health

- `GET /api/v1/health`

### 7.2 Dashboard

- `GET /api/v1/dashboard/summary`
- `GET /api/v1/dashboard/recent-packages`
- `GET /api/v1/dashboard/recent-slice-tasks`
- `GET /api/v1/dashboard/recent-annotations`

### 7.3 Packages / Import

- `POST /api/v1/packages`
- `GET /api/v1/packages`
- `GET /api/v1/packages/{id}`
- `PATCH /api/v1/packages/{id}`
- `DELETE /api/v1/packages/{id}`
- `GET /api/v1/import-tasks/{task_id}`

### 7.4 Slice Tasks / Windows

- `POST /api/v1/packages/{package_id}/slice-tasks`
- `GET /api/v1/packages/{package_id}/slice-tasks`
- `GET /api/v1/slice-tasks/{task_id}`
- `DELETE /api/v1/slice-tasks/{task_id}`
- `GET /api/v1/slice-tasks/{task_id}/windows`
- `GET /api/v1/slice-windows/{window_id}`
- `GET /api/v1/slice-windows/{window_id}/tree`
- `GET /api/v1/slice-windows/{window_id}/full`
- `GET /api/v1/slice-windows/{window_id}/logs`
- `DELETE /api/v1/slice-windows/{window_id}`

### 7.5 Annotations

- `GET /api/v1/slice-windows/{window_id}/annotation`
- `POST /api/v1/slice-windows/{window_id}/annotation`
- `PATCH /api/v1/annotations/{annotation_id}`
- `DELETE /api/v1/annotations/{annotation_id}`
- `GET /api/v1/annotations`
- `GET /api/v1/annotations/stats`
- `GET /api/v1/annotations/pending`
- `GET /api/v1/annotations/workbench`
- `GET /api/v1/annotations/export`

### 7.6 Recommendations

- `POST /api/v1/slice-windows/{window_id}/recommendation`
- `GET /api/v1/slice-windows/{window_id}/recommendation`
- `POST /api/v1/slice-tasks/{task_id}/recommendations/batch`
- `GET /api/v1/slice-tasks/{task_id}/recommendations/batch`

### 7.7 Fault Types

- `GET /api/v1/fault-types`
- `POST /api/v1/fault-types`
- `GET /api/v1/fault-types/{fault_type_id}`
- `PATCH /api/v1/fault-types/{fault_type_id}`
- `DELETE /api/v1/fault-types/{fault_type_id}`

### 7.8 Fault Type Suggestions

- `GET /api/v1/fault-type-suggestions`
- `GET /api/v1/fault-type-suggestions/{suggestion_id}`
- `POST /api/v1/fault-type-suggestions/{suggestion_id}/accept`
- `POST /api/v1/fault-type-suggestions/{suggestion_id}/reject`
- `DELETE /api/v1/fault-type-suggestions/{suggestion_id}`

## 8. 推荐你发给对方的口径

你可以直接这样告诉对方：

```text
我交付的是一套可独立运行的“日志数据标注子功能”，包含 backend、frontend、数据库迁移链、运行配置说明和接口清单。

后端不是单个接口，而是完整的上传 -> 导入 -> 切片 -> 浏览 -> 标注 -> 导出链路，并且包含 fault types、fault type suggestions 和 LLM recommendation 扩展能力。

数据库迁移请直接接收 backend/alembic/ 全量版本链，当前 head 是 20260615_0011。
```

## 9. 交付后的验证方式

建议对方拿到代码后至少执行以下验证。

### 9.1 后端

```bash
cd backend
pip install -e '.[dev]'
alembic upgrade head
python -m pytest -q
uvicorn app.main:app --reload
```

### 9.2 前端

```bash
cd frontend
npm ci
npm test
npm run build
npm run dev
```

### 9.3 最小联调检查

- `GET /api/v1/health` 可访问
- 前端首页可打开
- 能上传压缩包
- 能创建切片任务
- 能进入窗口浏览
- 能保存标注
- 能触发智能推荐
- 如模型提出新故障类型，能在故障类型建议页审核

## 10. 不建议直接交付的内容

通常不建议把以下内容作为正式交付物的一部分：

- `storage/`
- `tmp/`
- `.pytest_cache/`
- `deploy/images/`

原因：

- 它们属于运行期产物、临时文件或部署产物，不是集成源代码本体

## 11. 最简交付清单

如果你只想给对方一个最简但足够完整的包，建议至少包含：

1. `backend/`
2. `frontend/`
3. `CLAUDE.md`
4. `README.md`
5. `deploy/.env.example`
6. `INTEGRATION_HANDOFF.md`
7. `HANDOFF_ENV_TEMPLATE.md`
8. `HANDOFF_API_SUMMARY.md`
9. `INTEGRATION_NOTES.md`
