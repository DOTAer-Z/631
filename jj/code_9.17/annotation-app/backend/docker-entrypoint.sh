#!/bin/sh
# 标注子系统后端入口：等待 Postgres → 运行 Alembic 迁移 → 启动 uvicorn
# 参考主系统 backend/docker-entrypoint.sh 的设计，但标注后端不需 seed/ingest 步骤。
set -e

echo "[entrypoint] 等待 PostgreSQL ${DATABASE_URL} ..."

# 从 DATABASE_URL 提取主机/端口/用户/密码/数据库（psycopg 格式）
# 示例：postgresql+psycopg://postgres:secret@postgres:5432/fault_diagnosis
# 简化提取：假设 DATABASE_URL 格式合法，解析出 host 和 port 做健康等待。
# 生产环境 depends_on + healthcheck 已保证就绪，这里做二次确认。

python - <<'PY'
import os
import time
import psycopg

# SQLAlchemy 风格的 driver 后缀（如 postgresql+psycopg://）libpq 不认，
# 必须剥成纯 postgresql:// 才能传给 psycopg.connect。SQLAlchemy/Alembic
# 那边继续读环境变量 DATABASE_URL（含后缀），不受影响。
url = os.environ.get("DATABASE_URL", "")
libpq_url = url.replace("postgresql+psycopg://", "postgresql://", 1) \
               .replace("postgresql+psycopg2://", "postgresql://", 1)

for i in range(60):
    try:
        conn = psycopg.connect(libpq_url, connect_timeout=3)
        conn.close()
        print("[entrypoint] PostgreSQL 就绪")
        break
    except Exception as e:
        print(f"[entrypoint] 等待中 ({i+1}/60): {e}")
        time.sleep(2)
else:
    raise SystemExit("[entrypoint] PostgreSQL 连接超时")
PY

echo "[entrypoint] 运行 Alembic 迁移（head: 20260905_0001）"
alembic upgrade head

echo "[entrypoint] 启动后端 uvicorn :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
