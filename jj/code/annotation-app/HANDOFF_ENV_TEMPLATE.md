# 交付环境变量模板

## 后端必需

```env
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
STORAGE_ROOT=/data/storage
MAX_UPLOAD_BYTES=1073741824
```

## 后端可选

```env
APP_ENV=development
LOG_LEVEL=INFO
IMPORT_LINE_BATCH_SIZE=5000
IMPORT_DEFAULT_TIMEZONE=Asia/Shanghai
MANIFEST_CACHE_TTL_SECONDS=30
LOG_PAGE_DEFAULT_LIMIT=100
LOG_PAGE_MAX_LIMIT=500
WINDOW_BROWSER_LOGS_MAX_LIMIT=500
WINDOW_BROWSER_MANIFEST_CACHE_TTL_SECONDS=5
```

## LLM 可选

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=30
LLM_MAX_OUTPUT_TOKENS=1024
```

说明：

- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 三者同时存在时才启用 LLM
- 未配置时，LLM 功能静默降级，不影响主流程
- `LLM_BASE_URL` 是 OpenAI-compatible base URL，代码会请求 `{LLM_BASE_URL}/chat/completions`
- 示例：`LLM_BASE_URL=http://172.30.4.43:8001/v1` 会请求 `http://172.30.4.43:8001/v1/chat/completions`

## 推荐功能相关可选项

```env
RECOMMENDATION_MAX_LOG_LINES=200
SAMPLE_LINES_PER_FILE=5
SAMPLE_LINES_PER_CPU_CAP=50
TIMESTAMP_CONVERGENCE_THRESHOLD=3
TIMESTAMP_INFER_MIN_VALID_RATIO=0.6
```

## 前端

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

## 数据库迁移要求

```bash
cd backend
alembic upgrade head
alembic current
```

期望 head：

```text
20260615_0011
```
