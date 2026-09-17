"""
建表 + 自动迁移脚本（在 docker-entrypoint.sh 启动时执行）。

策略：
  1. Base.metadata.create_all() 负责"全新部署"——按当前 ORM 模型把缺失的表建出来。
  2. 对已经存在的表，create_all 不会去补字段。所以这里再跑一段 ALTER TABLE
     IF NOT EXISTS，把"老数据卷 + 新代码"场景下缺的列补齐，保证 6.13 → 6.15
     之间任何一次升级，旧 PG 卷都能直接跑起来。

幂等约束：
  - 所有 ALTER 都用 IF NOT EXISTS，已经有的列不会被改动。
  - 不删除任何列、不改变已有列类型，跑完不会丢数据。
  - 默认值仅对新建列生效。
"""
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from app.database import engine, Base
from app.models import *  # noqa: F401,F403 —— 触发模型注册到 metadata


# 列出 6.13 之后陆续加进 ORM 的字段，给老卷补齐。
# 每条 (table, column, ddl_fragment)，DDL 片段不含 "ADD COLUMN IF NOT EXISTS"，由下面拼。
_PATCH_COLUMNS = [
    # dataset_imports —— 自动摄入流水线（6.14）+ 新增/覆盖差分（6.15）
    ("dataset_imports", "ingest_status",               "VARCHAR(32) DEFAULT 'pending'"),
    ("dataset_imports", "ingest_error",                "TEXT"),
    ("dataset_imports", "ingested_at",                 "TIMESTAMP WITHOUT TIME ZONE"),
    ("dataset_imports", "ingested_case_ids",           "JSONB"),
    ("dataset_imports", "ingested_run_count",          "INTEGER DEFAULT 0"),
    ("dataset_imports", "ingested_entry_count",        "INTEGER DEFAULT 0"),
    ("dataset_imports", "ingested_new_case_count",     "INTEGER DEFAULT 0"),
    ("dataset_imports", "ingested_updated_case_count", "INTEGER DEFAULT 0"),
    ("dataset_imports", "ingested_new_run_count",      "INTEGER DEFAULT 0"),
    ("dataset_imports", "ingested_updated_run_count",  "INTEGER DEFAULT 0"),
    # 数据源格式（jsonl 导入器 / NuttX 管线）
    ("dataset_imports", "format",                      "VARCHAR(32)"),
    # diagnosis_records —— 按日志(run_id)复读诊断历史（集成方案 A 扩展）
    ("diagnosis_records", "run_id",                     "VARCHAR(128)"),
    # diagnosis_records —— 根因持久化（6.24 改造：诊断结果含根因，可复读）
    ("diagnosis_records", "root_cause",                 "TEXT"),
    ("diagnosis_records", "root_cause_type",            "VARCHAR(128)"),
    ("diagnosis_records", "recovery_hint",              "TEXT"),
    # prediction_records —— 关联日志(run_id)，供软件状态分级跳转故障诊断
    ("prediction_records", "run_id",                    "VARCHAR(128)"),
    # training_tasks —— 网页端选的基座模型路径（空则 worker 用 settings 默认）
    ("training_tasks", "base_model_path",               "VARCHAR(512)"),
]


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"),
        {"t": f"public.{table_name}"},
    ).scalar() or False


def _patch_columns() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _PATCH_COLUMNS:
            if not _table_exists(conn, table):
                # 全新部署里这张表是 create_all 刚刚建的，列就是齐的，不用补。
                continue
            stmt = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}'
            conn.execute(text(stmt))


def _has_alembic_version_table() -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar()
        )


def _alembic_config() -> AlembicConfig:
    return AlembicConfig(str(Path(__file__).with_name("alembic.ini")))


def initialize_schema() -> None:
    versioned = _has_alembic_version_table()
    if versioned:
        command.upgrade(_alembic_config(), "head")

    Base.metadata.create_all(bind=engine)
    _patch_columns()

    if not versioned:
        command.stamp(_alembic_config(), "head")


if __name__ == "__main__":
    initialize_schema()
    print("数据库结构已更新到当前版本。")
