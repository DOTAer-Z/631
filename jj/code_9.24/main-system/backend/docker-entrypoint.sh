#!/bin/sh
# 后端容器入口：等待 Postgres 就绪 → 建表 → seed → （可选）导入数据集 → 启动 API。
# 复用仓库已有脚本：create_tables.py / seed.py / ingest_dataset.py。
set -e

echo "[entrypoint] 等待 PostgreSQL ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432} ..."
python - <<'PY'
import os, time, psycopg2
host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
user = os.environ.get("POSTGRES_USER", "postgres")
pw   = os.environ.get("POSTGRES_PASSWORD", "secret")
db   = os.environ.get("POSTGRES_DB", "fault_diagnosis")
for i in range(60):
    try:
        psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=db).close()
        print("[entrypoint] PostgreSQL 就绪"); break
    except Exception as e:
        print(f"[entrypoint] 等待中 ({i+1}/60): {e}"); time.sleep(2)
else:
    raise SystemExit("[entrypoint] PostgreSQL 连接超时")
PY

echo "[entrypoint] 建表 create_tables.py"
python create_tables.py

echo "[entrypoint] 注入基础数据 seed.py"
python seed.py || echo "[entrypoint] seed 跳过/失败（继续）"

if [ "${AUTO_INGEST:-1}" = "1" ] && [ -d /app/data ]; then
    echo "[entrypoint] 后台导入内置数据集（不阻塞启动，日志见 /tmp/ingest.log）"
    ( python ingest_dataset.py --data-root /app/data --skip-if-present >/tmp/ingest.log 2>&1; \
      echo "[ingest] exit=$?" >>/tmp/ingest.log ) &
fi

echo "[entrypoint] 启动后端 uvicorn :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
