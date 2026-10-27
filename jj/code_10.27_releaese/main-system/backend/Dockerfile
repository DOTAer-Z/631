# =============================================================================
# 后端镜像 —— 故障定位系统 FastAPI 服务
#
# 注意：本 Dockerfile 的构建上下文是「仓库根目录」（fault-6.5/），
#       以便同时拷贝 backend/ 源码、data/ 数据集、models/ 本地权重。
#       构建命令见 deploy/docker-compose.prod.yaml（context: ..）。
#
# 自 2026-06-14 起：将 BAAI/bge-small-zh-v1.5 模型权重打包进 /models，
# 安装 sentence-transformers + torch CPU 版 + faiss-cpu，使本地 Embedding 即装即用。
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# 系统依赖：curl 供 healthcheck；libpq5 供 psycopg2 运行时；libgomp1 供 faiss
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libpq5 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 docker 层缓存）
COPY backend/requirements.prod.txt ./requirements.prod.txt
RUN pip install --no-cache-dir -r requirements.prod.txt

# 拷贝本地 Embedding 模型权重（约 92MB）
COPY models/bge-small-zh-v1.5 /models/bge-small-zh-v1.5

# 拷贝后端源码与内置数据集
COPY backend/ /app/
COPY data/ /app/data/

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=10 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 入口：等待 PG → 建表 → seed → 导入数据集 → 启动 uvicorn
ENTRYPOINT ["/app/docker-entrypoint.sh"]
