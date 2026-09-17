# 日志数据标注平台 V1

数据库驱动的日志数据标注平台。

当前版本已经完成 2026-06-01 redesign 的 Phase1 到 Phase7，并在 2026-06-15 增量交付了
LLM 辅助、待标注数据管理、K8s 导出修复等功能，提供：

- 原始压缩包上传与异步导入
- `source_log_files + source_log_lines` 原始日志双层入库
- `slice_tasks + slice_windows + slice_window_lines` 数据库驱动切片
- 窗口详情聚合、树结构浏览、cursor 日志分页、当前窗口内搜索
- 窗口级 + 文件级多标注、待标注工作台、导出、Dashboard 聚合
- 窗口细分（父子窗口）与叶子窗口导航 / 统计
- 故障类型字典 + LLM 推荐（两步流程，可发现新故障类型并落到建议队列）
- AI 多错误分析（逐文件建议 + 建议子窗口长度）
- 待标注列表筛选与「查看 / 删除空切片」管理
- Docker Compose 与 K8s 部署，导出文件下载在反代后正确工作

## 项目简介

平台面向结构化日志标注场景，核心目标是把上传、导入、切片、浏览、标注、导出全部统一到数据库驱动架构中，不再依赖磁盘解压目录或切片目录作为主查询源。

设计约束：

- 原始压缩包保留在磁盘
- 原始 `data/` 日志入数据库
- 切片结果入数据库
- 导入异步化
- 窗口日志使用 cursor 分页
- 搜索仅限当前窗口
- 标注只保留最新状态，不保留历史版本
- 故障类型字典是标注 / 推荐的硬约束，新故障类型必须经人工审核后才纳入字典
- 切片窗口长度可以从 1s 到 3600s，不限于固定档位

## 技术栈

- Backend: FastAPI, SQLAlchemy 2.x, Alembic
- Database: PostgreSQL
- Frontend: Vue 3, TypeScript, Pinia, Vue Router, Element Plus
- Testing: Pytest, Vitest
- Deployment: Docker Compose

## 系统架构

主链路：

```text
压缩包
↓
dataset_packages
↓
import_tasks
↓
source_log_files
↓
source_log_lines
↓
slice_tasks
↓
slice_windows
↓
slice_window_lines
↓
annotations
```

LLM 辅助分支：

```text
slice_windows
├─ annotation_recommendations         # 大模型推荐（不自动写 annotations）
└─ fault_type_suggestions             # 大模型在已定义字典外提出的新故障类型
                                      # 经人工审核后写入 fault_types
```

主要表：

- `dataset_packages`
- `import_tasks`
- `source_log_files`
- `source_log_lines`
- `slice_tasks`
- `slice_windows`
- `slice_window_lines`
- `annotations`
- `fault_types`
- `annotation_recommendations`
- `fault_type_suggestions`

关键语义：

- `dataset_packages` 保存压缩包元数据、导入状态、统计字段
- `import_tasks` 跟踪异步导入任务状态
- `source_log_files` 保存原始日志文件维度信息
- `source_log_lines` 保存原始日志行内容
- `slice_tasks` 保存切片任务与统计；`window_seconds` 范围 `[1, 3600]`
- `slice_windows` 保存窗口摘要，支持 `parent_window_id + has_children` 父子窗口细分
- `slice_window_lines` 物化窗口与原始日志行关系
- `annotations` 只保留最新状态；每个窗口最多 1 条整窗标注，且每个源文件最多 1 条文件级标注；`anomaly_type` 必须命中 `fault_types`
- `fault_types` 用户维护的故障类型字典（含初始化种子）
- `annotation_recommendations` LLM 推荐缓存，单窗口唯一行，状态 `pending / success / failed`
- `fault_type_suggestions` LLM 在两步推荐中提议的新故障类型，状态 `pending / accepted / rejected`，
  采纳后回填 `accepted_fault_type_id` 指向新建的 `fault_types` 行

## 页面说明

一级导航固定为四个页面：

1. 首页
   - Dashboard 聚合统计
   - 最近上传包
   - 最近切片任务
   - 最近标注记录
2. 数据包管理
   - 上传压缩包
   - 搜索、分页、状态展示
   - 导入状态轮询
   - 数据包详情与危险删除
3. 数据切片
   - 选择已导入数据包
   - 创建切片任务（窗口长度 1s ~ 3600s 任意整数）
   - 浏览窗口摘要
   - 进入窗口浏览 / 树结构浏览 / 细分窗口
4. 数据标注
   - 统计卡片
   - 待标注列表（支持按 package / task / 行数区间 / 关键字筛选；可按时间或行数升降排序）
   - 待标注列表行内「查看 / 删除」（删除走 `DELETE /slice-windows/{id}`，已标注窗口禁止删除）
   - 连续标注工作台（含 LLM 智能推荐、一键采纳、逐文件采纳）
   - 已标注记录与导出
   - 推荐卡识别到「新故障类型建议」时直接给出审核入口
   - AI 多错误分析可建议逐文件标注或进一步细分窗口

隐藏页面：

- `/health`
- `/packages/:id`
- `/packages/:id/slice-tasks`
- `/packages/:id/slice-tasks/:taskId/windows`
- `/annotation/records`
- `/annotation/fault-types`
- `/annotation/fault-type-suggestions`

## API 概览

所有业务接口前缀为 `/api/v1`。

### Health

- `GET /health`

### Dashboard

- `GET /dashboard/summary`
- `GET /dashboard/recent-packages`
- `GET /dashboard/recent-slice-tasks`
- `GET /dashboard/recent-annotations`

### Packages

- `POST /packages`
- `GET /packages`
- `GET /packages/{id}`
- `PATCH /packages/{id}`
- `DELETE /packages/{id}`

### Import Tasks

- `GET /import-tasks/{task_id}`

### Slice Tasks

- `POST /packages/{package_id}/slice-tasks`
- `GET /packages/{package_id}/slice-tasks`
- `GET /slice-tasks/{task_id}`
- `DELETE /slice-tasks/{task_id}`

### Slice Windows

- `GET /slice-tasks/{task_id}/windows`
- `GET /slice-windows/{window_id}`
- `GET /slice-windows/{window_id}/tree`
- `GET /slice-windows/{window_id}/full`
- `GET /slice-windows/{window_id}/logs`
- `DELETE /slice-windows/{window_id}` — 删除未标注的空切片窗口；已存在 annotation 时返回 `WINDOW_HAS_ANNOTATION` 409
- `POST /slice-windows/{window_id}/subdivide` — 细分窗口，保留父窗口并创建子窗口

### Annotations

- `GET /slice-windows/{window_id}/annotation`
- `POST /slice-windows/{window_id}/annotation`
- `PATCH /annotations/{annotation_id}`
- `DELETE /annotations/{annotation_id}`
- `GET /annotations`
- `GET /annotations/stats`
- `GET /annotations/pending` — 支持 `min_line_count / max_line_count / keyword / sort_by / sort_order`
- `GET /annotations/workbench`
- `GET /annotations/export`
- `GET /annotations/export/download` — 导出文件下载端点（返回相对路径，由前端拼至当前域名，反代友好）

### Fault Types

- `GET /fault-types`
- `POST /fault-types`
- `GET /fault-types/{id}`
- `PATCH /fault-types/{id}`
- `DELETE /fault-types/{id}`

### Recommendations

- `POST /slice-windows/{window_id}/recommendation` — 单窗口推荐（两步流程，未匹配字典时进入 pass 2）
- `GET /slice-windows/{window_id}/recommendation`
- `POST /slice-windows/{window_id}/recommendation/analyze` — 多错误分析，返回逐文件建议与建议细分秒数
- `POST /slice-tasks/{task_id}/recommendations/batch`
- `GET /slice-tasks/{task_id}/recommendations/batch`

### Fault Type Suggestions

- `GET /fault-type-suggestions?status=pending|accepted|rejected`
- `GET /fault-type-suggestions/{id}`
- `POST /fault-type-suggestions/{id}/accept` — 可提交 `{name?, description?}` 覆盖 LLM 原文，事务内创建 `fault_types`
- `POST /fault-type-suggestions/{id}/reject`
- `DELETE /fault-type-suggestions/{id}`

## 本地启动

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm ci
npm run dev
```

默认地址：

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`

## LLM 配置

LLM 推荐 / 时间戳推断两类功能由 OpenAI 兼容协议驱动，配置不全时**整体静默降级**，
不影响主链路。环境变量：

| 变量 | 说明 |
| - | - |
| `LLM_BASE_URL` | 例如 `https://api.deepseek.com/v1`、`http://ollama:11434/v1` |
| `LLM_API_KEY` | 鉴权 token（缺失即视为未启用） |
| `LLM_MODEL` | 模型名，例如 `deepseek-chat` |
| `LLM_TIMEOUT_SECONDS` | 单次请求超时秒数，默认 30 |
| `LLM_MAX_OUTPUT_TOKENS` | 输出 token 上限，默认 1024 |

故障类型推荐采用两步流程：第一轮严格约束在 `fault_types` 字典内；当 LLM 给出字典外名称时，
第二轮单独询问「是否真的需要新增类型」，确认后落到 `fault_type_suggestions` 待人工审核，**不会
自动改写 `fault_types` 字典**。

## Docker 启动

### 准备环境变量

```bash
cp -n deploy/.env.example deploy/.env
```

### 初始化存储目录

```bash
bash scripts/init_storage.sh
```

### 启动服务

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

### 查看状态

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
```

## Alembic 迁移

本地执行：

```bash
cd backend
alembic upgrade head
alembic current
```

Docker 内执行：

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec backend alembic upgrade head
```

当前 head revision：

- `20260905_0001`（方案 3 单库合并：全部历史迁移折叠为一个初始迁移
  `alembic/versions/20260905_0001_merged_annotation_schema.py`，建 11 张 `ann_*` 表 + 指向
  `fault_types` 的外键；版本表独立为 `alembic_version_annotate`）。该迁移**不**建
  `fault_types` —— 该表在单库部署里由主系统「构建 + 种子」负责，标注侧仅经
  `ann_fault_type_suggestions.accepted_fault_type_id` 引用。

## 测试执行

### 后端测试

```bash
cd backend
python -m pytest -q
```

### 前端测试

```bash
cd frontend
npm test
```

### 前端构建验证

```bash
cd frontend
npm run build
```

### Compose 快速检查

```bash
bash scripts/smoke_test.sh
```

## Docker 验证结果

Phase7 已完成以下验证：

- `docker compose up -d --build` 成功
- 容器内 `alembic upgrade head` 成功
- 健康检查通过
- 手工验证完整链路通过：
  - 上传压缩包
  - 创建并完成导入任务
  - 查看数据包详情
  - 创建切片任务
  - 查看窗口列表、树结构、日志、窗口内搜索
  - 创建标注、保存下一窗口
  - 查看标注工作台
  - 导出标注
  - 删除切片任务并验证原始数据保留
  - 删除数据包并验证数据库与磁盘清理

## K8s 部署注意事项

`deploy/k8s/` 提供了 namespace / configmap / secret / postgres / backend / frontend / gateway
完整清单。以下是几个容易踩坑的点：

- **导出文件下载**：`/api/v1/annotations/export` 返回的 `download_url` 是相对路径，前端会拼到当前
  浏览器域名上访问，不会泄露集群内部 service 名。`gateway.yaml` 已经把 `location /api/`
  打开了 `proxy_buffering off / proxy_request_buffering off / proxy_read_timeout 300s` 以支持
  大文件流式下载。
- **LLM 凭据**：通过 `secret.yaml` 注入 `LLM_API_KEY`，`configmap.yaml` 注入 `LLM_BASE_URL` /
  `LLM_MODEL` / `LLM_TIMEOUT_SECONDS` / `LLM_MAX_OUTPUT_TOKENS`。三者任一缺失则 LLM 功能静默禁用。
- **存储 PVC**：`backend.yaml` 申请 50Gi 的 `data-bj-storage-pvc`，挂到 `/data/storage`，对应
  `STORAGE_ROOT`。原始压缩包、导出文件均落在这里，PVC 销毁会同时丢失。

## 目录说明

- `backend/` 后端服务、Alembic、测试
- `frontend/` 前端应用、测试、Dockerfile
- `deploy/` Docker Compose 与环境变量示例
- `scripts/` 初始化与 smoke test 脚本
- `storage/`
  - `packages/` 原始压缩包
  - `annotations/` 标注导出文件

## 当前交付状态

当前项目已达到可交付状态，基础 V1 链路和后续增强能力均已落地：

- 后端测试通过
- 前端测试通过
- 前端构建通过
- Docker Compose 启动通过
- 容器内 Alembic 迁移通过
- 上传 / 导入 / 切片 / 浏览 / 标注 / 导出 / 删除链路通过
- 故障类型字典、推荐建议审核、多标注、窗口细分、多错误分析已接入主流程

标注：

- 当前实现以数据库为唯一查询事实源，兼容层清理未做额外破坏性删除，优先保证稳定性
