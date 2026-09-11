from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


CONFIG_FILE = Path(__file__).resolve()
PROJECT_ROOT = CONFIG_FILE.parents[2]


def _resolve_env_path(raw_path: str) -> Path:
    env_path = Path(raw_path)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path
    return env_path.resolve()


def _load_env() -> None:
    primary_env = _resolve_env_path(os.getenv("FAULT_ENV_FILE", ".env.local"))
    fallback_env = PROJECT_ROOT / ".env"

    for env_path in (primary_env, fallback_env):
        if env_path.exists():
            load_dotenv(env_path)
            print(f"[INFO] Loaded env file: {env_path}")
            return

    print(f"[WARN] No env file found. Tried: {primary_env}, {fallback_env}")


_load_env()


# =========================
# 工具函数
# =========================
def _get_str(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else str(value)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid int for env {name}: {raw}") from exc


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_path(name: str, default: str) -> Path:
    raw = _get_str(name, default)
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


# =========================
# MySQL 配置
# =========================
MYSQL_HOST = _get_str("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _get_int("MYSQL_PORT", 3306)
MYSQL_USER = _get_str("MYSQL_USER", "root")
MYSQL_PASSWORD = _get_str("MYSQL_PASSWORD", "")
MYSQL_DB = _get_str("MYSQL_DB", "fault_diagnosis")
MYSQL_CHARSET = _get_str("MYSQL_CHARSET", "utf8mb4")


# =========================
# MongoDB 配置
# =========================
MONGO_URI = _get_str("MONGO_URI", "mongodb://127.0.0.1:27017")
MONGO_DB = _get_str("MONGO_DB", "fault_diagnosis")


# =========================
# 数据与输出目录
# =========================
DATA_ROOT = _get_path("DATA_ROOT", "data/raw")
MANIFEST_PATH = _get_path("MANIFEST_PATH", "data/manifest/manifest.jsonl")

OUTPUTS_DIR = _get_path("OUTPUTS_DIR", "outputs")
VECTOR_OUT_DIR = _get_path("VECTOR_OUT_DIR", "outputs/vector_store")
KG_OUT_DIR = _get_path("KG_OUT_DIR", "outputs/kg")
LOG_DIR = _get_path("LOG_DIR", "logs")
BACKUP_DIR = _get_path("BACKUP_DIR", "backups")


# =========================
# 向量相关配置
# =========================
EMBEDDING_DIM = _get_int("EMBEDDING_DIM", 384)
EMBEDDING_DEVICE = _get_str("EMBEDDING_DEVICE", "cpu")
EMBEDDING_MODEL_NAME = _get_str(
    "EMBEDDING_MODEL_NAME",
    "sentence-transformers/all-MiniLM-L6-v2",
)
LOCAL_MODEL_PATH = _get_str("LOCAL_MODEL_PATH", "")


# =========================
# 运行开关
# =========================
DEBUG = _get_bool("DEBUG", True)


# =========================
# 常用辅助函数
# =========================
def ensure_dirs() -> None:
    """创建项目常用目录。"""
    for p in [
        DATA_ROOT,
        MANIFEST_PATH.parent,
        OUTPUTS_DIR,
        VECTOR_OUT_DIR,
        KG_OUT_DIR,
        LOG_DIR,
        BACKUP_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


def mysql_dict() -> dict:
    """返回 MySQL 连接参数字典。"""
    return {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "database": MYSQL_DB,
        "charset": MYSQL_CHARSET,
    }


def mongo_dict() -> dict:
    """返回 MongoDB 连接参数字典。"""
    return {
        "uri": MONGO_URI,
        "db": MONGO_DB,
    }


def print_config() -> None:
    """打印关键配置，方便确认脚本连的是哪套环境。"""
    print("[CONFIG] PROJECT_ROOT =", PROJECT_ROOT)
    print(f"[CONFIG] MySQL      = {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
    print(f"[CONFIG] Mongo      = {MONGO_URI}/{MONGO_DB}")
    print(f"[CONFIG] DATA_ROOT  = {DATA_ROOT}")
    print(f"[CONFIG] MANIFEST   = {MANIFEST_PATH}")
    print(f"[CONFIG] VECTOR_OUT = {VECTOR_OUT_DIR}")
    print(f"[CONFIG] KG_OUT     = {KG_OUT_DIR}")
    print(f"[CONFIG] DEBUG      = {DEBUG}")


if __name__ == "__main__":
    ensure_dirs()
    print_config()
