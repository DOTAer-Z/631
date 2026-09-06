from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class DatasetImport(Base):
    __tablename__ = "dataset_imports"
    __table_args__ = (
        UniqueConstraint("import_id", name="uq_dataset_imports_import_id"),
        Index("idx_dataset_imports_sha256_is_deleted", "sha256", "is_deleted"),
        Index("idx_dataset_imports_created_at", "created_at"),
        Index("idx_dataset_imports_status", "status"),
        Index("idx_dataset_imports_file_ext", "file_ext"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    import_id = Column(String(64), nullable=False)
    original_filename = Column(String(512), nullable=False)
    stored_filename = Column(String(512), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    file_ext = Column(String(16), nullable=False)
    mime_type = Column(String(128), nullable=True)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    tags_json = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="uploaded")
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

    # ── 自动摄入流水线追踪字段（方案 1：上传后自动解压 + 摄入到 cases / runs / log_entries） ──
    # ingest_status：pending / ingesting / success / failed / no_test_dirs
    ingest_status = Column(String(32), nullable=True, default="pending")
    ingest_error = Column(Text, nullable=True)
    ingested_at = Column(DateTime, nullable=True)
    ingested_case_ids = Column(JSONB, nullable=True)
    ingested_run_count = Column(Integer, nullable=True, default=0)
    ingested_entry_count = Column(Integer, nullable=True, default=0)
    # ── 区分本次摄入是新增还是覆盖（同一 Test_*** 重复上传时，run_id 走 upsert） ──
    ingested_new_case_count = Column(Integer, nullable=True, default=0)
    ingested_updated_case_count = Column(Integer, nullable=True, default=0)
    ingested_new_run_count = Column(Integer, nullable=True, default=0)
    ingested_updated_run_count = Column(Integer, nullable=True, default=0)
    training_complete_count = Column(Integer, nullable=False, default=0)
    training_incomplete_count = Column(Integer, nullable=False, default=0)
    training_duplicate_count = Column(Integer, nullable=False, default=0)
    training_failed_count = Column(Integer, nullable=False, default=0)
    training_parse_failed_count = Column(Integer, nullable=False, default=0)
