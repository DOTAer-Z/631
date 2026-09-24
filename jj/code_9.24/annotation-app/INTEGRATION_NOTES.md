# 集成注意事项

这份文件给接收方使用，说明把当前日志标注子功能集成到更大的前后端项目时需要特别注意的点。

## 1. 集成范围

建议把以下内容作为一个完整子功能接收：

- `backend/`
- `frontend/`
- `CLAUDE.md`
- `README.md`
- `INTEGRATION_HANDOFF.md`
- `HANDOFF_ENV_TEMPLATE.md`
- `HANDOFF_API_SUMMARY.md`
- `deploy/.env.example`

如果对方只拆模块，不整仓接入，至少要同时迁移后端的 API、service、schema、db models、Alembic migration，不能只拷贝 controller/API 文件。

## 2. 数据库迁移必须先跑到最新

当前 Alembic head：

```text
20260615_0011
```

必须执行：

```bash
cd backend
alembic upgrade head
alembic current
```

如果没有跑到 `20260615_0011`，智能推荐里涉及新故障类型建议时会缺少 `fault_type_suggestions` 表并报错。

相关新增表：

- `annotation_recommendations`
- `fault_types`
- `fault_type_suggestions`

## 3. API 前缀

后端所有业务接口默认挂在：

```text
/api/v1
```

如果集成方已有网关或统一后端路由，需要确认是否保留 `/api/v1`，以及前端 `VITE_API_BASE_URL` 是否同步调整。

## 4. 数据存储约束

本功能的设计约束是：

- 数据库是查询唯一事实来源
- 磁盘只保留原始压缩包和导出文件
- 原始日志行、切片窗口、窗口行关系、标注、推荐结果都在数据库里

后端需要一个可持久化的 `STORAGE_ROOT` 目录，用于保存上传压缩包和导出文件。

## 5. Postgres 是部署目标

测试中会使用 SQLite，但正式部署目标是 Postgres。

注意：

- 部分历史迁移包含 SQLite 不完全兼容的 batch 操作
- 对方正式集成时应以 Postgres 跑完整 Alembic 链

## 6. LLM 配置

LLM 使用 OpenAI-compatible chat completions 协议。

必需配置：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

可选配置：

```env
LLM_TIMEOUT_SECONDS=30
LLM_MAX_OUTPUT_TOKENS=1024
```

代码实际请求：

```text
{LLM_BASE_URL}/chat/completions
```

示例：

```env
LLM_BASE_URL=http://172.30.4.43:8001/v1
LLM_MODEL=dsqwen32b
```

则实际请求为：

```text
http://172.30.4.43:8001/v1/chat/completions
```

如果 LLM 未配置，功能会静默降级，不影响上传、导入、切片、浏览、标注、导出主流程。

## 7. 故障类型和智能推荐的关系

当前推荐逻辑不是自由文本直接写入标注。

规则：

- `fault_types` 是用户维护的故障类型字典
- 标注的 `anomaly_type` 必须来自 `fault_types`
- LLM 推荐第一步只能从 `fault_types` 中选择
- 如果 LLM 认为需要新增类型，会写入 `fault_type_suggestions`
- 用户审核通过后，建议才会转成正式 `fault_types`
- 推荐结果不会自动写入 `annotations`

所以集成方需要同时接入：

- 故障类型管理页
- 故障类型建议审核页
- 标注工作台里的推荐卡片

## 8. 前端路由接入

当前前端使用 Vue Router，核心页面：

- `/dashboard`
- `/packages`
- `/slicing`
- `/annotation`

隐藏页面：

- `/health`
- `/packages/:id`
- `/packages/:id/slice-tasks`
- `/packages/:id/slice-tasks/:taskId/windows`
- `/annotation/records`
- `/annotation/fault-types`
- `/annotation/fault-type-suggestions`

如果集成到对方已有项目，通常需要把这些页面挂到对方现有 layout、菜单和权限系统下。

## 9. 异步任务

导入、切片、批量推荐都不是 FastAPI BackgroundTasks，而是 daemon `threading.Thread`。

状态通过数据库轮询：

- `import_tasks`
- `slice_tasks`
- `annotation_recommendations`

如果对方项目已有任务队列，可以后续替换，但初次集成建议先保持现状。

## 10. 上传和代理限制

默认上传限制：

```env
MAX_UPLOAD_BYTES=1073741824
```

如果对方前面有 Nginx、Ingress、API Gateway，也要同步放开请求体大小，否则后端配置再大也会被前置代理拦截。

## 11. 最小验收路径

集成后建议至少验证：

1. `GET /api/v1/health`
2. 上传压缩日志包
3. 导入任务完成
4. 创建切片任务，`window_seconds` 在 `[1, 3600]`
5. 浏览窗口树和日志
6. 维护故障类型
7. 触发智能推荐
8. 如果出现新故障类型建议，可以进入建议页 accept/reject
9. 保存标注
10. 导出标注

## 12. 不要交付真实运行数据和密钥

不要把以下内容交给对方，除非明确需要：

- `storage/`
- `tmp/`
- `.pytest_cache/`
- `deploy/images/`
- `.env`
- 真实 LLM API Key
- 真实数据库密码

