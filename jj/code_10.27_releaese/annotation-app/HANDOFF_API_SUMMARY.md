# 交付接口摘要

接口统一前缀：

```text
/api/v1
```

## Health

- `GET /health`

## Dashboard

- `GET /dashboard/summary`
- `GET /dashboard/recent-packages`
- `GET /dashboard/recent-slice-tasks`
- `GET /dashboard/recent-annotations`

## Packages / Import

- `POST /packages`
- `GET /packages`
- `GET /packages/{id}`
- `PATCH /packages/{id}`
- `DELETE /packages/{id}`
- `GET /import-tasks/{task_id}`

## Slice Tasks / Windows

- `POST /packages/{package_id}/slice-tasks`
- `GET /packages/{package_id}/slice-tasks`
- `GET /slice-tasks/{task_id}`
- `DELETE /slice-tasks/{task_id}`
- `GET /slice-tasks/{task_id}/windows`
- `GET /slice-windows/{window_id}`
- `GET /slice-windows/{window_id}/tree`
- `GET /slice-windows/{window_id}/full`
- `GET /slice-windows/{window_id}/logs`
- `DELETE /slice-windows/{window_id}`

## Annotations

- `GET /slice-windows/{window_id}/annotation`
- `POST /slice-windows/{window_id}/annotation`
- `PATCH /annotations/{annotation_id}`
- `DELETE /annotations/{annotation_id}`
- `GET /annotations`
- `GET /annotations/stats`
- `GET /annotations/pending`
- `GET /annotations/workbench`
- `GET /annotations/export`

## Recommendations

- `POST /slice-windows/{window_id}/recommendation`
- `GET /slice-windows/{window_id}/recommendation`
- `POST /slice-tasks/{task_id}/recommendations/batch`
- `GET /slice-tasks/{task_id}/recommendations/batch`

说明：

- 推荐不会自动写入正式标注
- 如果 LLM 提出未定义故障类型，响应里可能包含 `pending_suggestion_id`
- `pending_suggestion_id` 指向待人工审核的 `fault_type_suggestions` 记录

## Fault Types

- `GET /fault-types`
- `POST /fault-types`
- `GET /fault-types/{fault_type_id}`
- `PATCH /fault-types/{fault_type_id}`
- `DELETE /fault-types/{fault_type_id}`

## Fault Type Suggestions

- `GET /fault-type-suggestions`
- `GET /fault-type-suggestions/{suggestion_id}`
- `POST /fault-type-suggestions/{suggestion_id}/accept`
- `POST /fault-type-suggestions/{suggestion_id}/reject`
- `DELETE /fault-type-suggestions/{suggestion_id}`

说明：

- `GET /fault-type-suggestions` 支持查询参数：`status`、`page`、`page_size`
- `status` 可用值：`pending`、`accepted`、`rejected`
- accept 会创建或复用故障类型，并把建议标记为 accepted

## 前端路由参考

- `/dashboard`
- `/packages`
- `/packages/:id`
- `/slicing`
- `/packages/:id/slice-tasks`
- `/packages/:id/slice-tasks/:taskId/windows`
- `/annotation`
- `/annotation/records`
- `/annotation/fault-types`
- `/annotation/fault-type-suggestions`
