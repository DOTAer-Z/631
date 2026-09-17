'''
Author: sunpenggang sunpenggang@ourfuture.cn
Date: 2026-04-07 11:55:28
LastEditors: sunpenggang sunpenggang@ourfuture.cn
LastEditTime: 2026-04-14 10:43:50
FilePath: \631_fault\backend\app\config.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
from typing import Literal

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    # ── Chroma 向量库（本地 PersistentClient 模式） ──
    # 旧字段 CHROMA_HOST / CHROMA_PORT 仅为向前兼容保留，新版不再使用。
    # 实际生效的是 CHROMA_PERSIST_PATH：Chroma 把 sqlite + hnsw 索引落盘到此目录。
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000
    CHROMA_PERSIST_PATH: str = "/app/outputs/vector_store/chroma"
    DASHSCOPE_API_KEY: str = ""
    SIMILARITY_THRESHOLD: float = 0.8

    # =========================================================================
    # 主数据库：PostgreSQL（已由 MySQL 迁移而来）
    # 若设置 DATABASE_URL 则优先使用；否则由下列字段拼接连接串。
    # =========================================================================
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5433
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "secret"
    POSTGRES_DB: str = "fault_diagnosis"

    # =========================================================================
    # 文档存储：已由 MongoDB 迁移为 PostgreSQL JSONB 兼容层（app/mongo_compat.py）。
    # 不再连接真实 Mongo；MONGO_DB 仅作为兼容层的逻辑库名占位。
    # =========================================================================
    MONGO_DB: str = "fault_diagnosis"

    VECTOR_INDEX_PATH: str = "/app/outputs/vector_store/faiss.index"
    VECTOR_METADATA_PATH: str = "/app/outputs/vector_store/metadata.jsonl"

    ENABLE_EMBEDDING: bool = True
    EMBEDDING_MODEL_NAME: str = "bge-small-zh-v1.5"
    LOCAL_MODEL_PATH: str = "/models/bge-small-zh-v1.5"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIM: int = 512

    # ── OpenAI 兼容 Embedding 接口（预留，端点留空=未配置；见 embedding_client.py）──
    EMBEDDING_API_BASE: str = ""        # 例：http://172.30.4.43:8001/v1
    EMBEDDING_API_KEY: str = "dummy"
    EMBEDDING_MODEL: str = ""           # 例：bge-m3 / text-embedding-...

    KG_DIR: str = "/app/outputs/kg"

    # 标注子系统后端地址：用于「从标注典型案例导入」拉取 DB2 的标注案例。
    # docker 网络内默认指向标注后端；不可达时导入优雅失败、不影响其它功能。
    ANNOTATE_BACKEND_URL: str = "http://backend-annotate:8000/api/v1"
    ANNOTATE_TIMEOUT: int = 60

    # =========================================================================
    # 大模型（OpenAI 兼容协议；DashScope / 本地 vLLM 等均可）
    # An active database model_api_configs row is authoritative for runtime calls.
    # Without an active database configuration, LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
    # must all be set for the environment fallback to be enabled.
    # ENABLE_LLM only switches that environment fallback; it does not disable an
    # active database configuration.
    # =========================================================================
    ENABLE_LLM: bool = True
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_TIMEOUT: int = 60
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    LOG_PARSE_TIMEOUT_SECONDS: int = 60
    LOG_PARSE_WORKERS: int = 1

    # Fernet key used only for model_api_configs.api_key_ciphertext.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    MODEL_API_ENCRYPTION_KEY: str = ""
    # 零-yaml 部署：MODEL_API_ENCRYPTION_KEY 为空时，从该文件读取/生成持久化 key。
    # 必须落在持久卷（k8s/03-backend.yaml 挂载 /app/outputs），否则重建卷即丢 key。
    MODEL_API_ENCRYPTION_KEY_FILE: str = "/app/outputs/model_api_encryption_key"
    INTERNAL_LLM_GATEWAY_TOKEN: str = ""

    MAX_FILE_SIZE: int = 100 * 1024 * 1024   # 日志上传单文件上限 100MB
    MAX_LINES: int = 10000

    DATA_IMPORT_ROOT: str = "/app/data/imports"
    DATA_IMPORT_MAX_FILE_SIZE: int = 512 * 1024 * 1024
    DATA_IMPORT_ALLOWED_EXTS: str = "zip,tar,tar.gz,tgz"
    # 解压目录（数据导入「自动摄入」管线临时使用，结束后清理）
    DATA_IMPORT_EXTRACT_ROOT: str = "/app/data/imports_extract"
    # 上传后是否自动解压并摄入到 cases/runs/log_entries 表
    DATA_IMPORT_AUTO_INGEST: bool = True

    # The worker writes only inside this persistent container volume.
    TRAINING_OUTPUT_ROOT: str = "/training/outputs"
    TRAINING_BASE_MODEL_PATH: str = "/models/Qwen3.5-9B"
    TRAINING_BASE_MODEL_ID: str = "Qwen/Qwen3.5-9B"
    TRAINING_WORKER_ID: str = "gpu-worker-1"
    TRAINING_POLL_SECONDS: int = 3
    TRAINING_HEARTBEAT_SECONDS: int = 10
    TRAINING_STALE_SECONDS: int = 60
    TRAINING_MIN_FREE_DISK_GB: int = 20
    TRAINING_MIN_FREE_GPU_MB: int = 18000
    TRAINING_DEVICE: Literal["auto", "cuda", "cpu"] = "auto"
    TRAINING_CPU_PROFILE: str = "auto_low_memory"
    TRAINING_MIN_FREE_CPU_GB: PositiveInt = 24
    # 演示/流程展示：允许缺失基座模型。置 True 后网页端任选路径也能跑通
    # queued→running 流转（真实训练仍会因缺权重失败，但流程点可展示）。
    TRAINING_ALLOW_MISSING_MODEL: bool = False

    model_config = SettingsConfigDict(
        extra="ignore",
    )


settings = Settings()
