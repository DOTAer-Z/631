from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


TEST_COMPLETENESS = ("complete", "incomplete")
IMPORT_ITEM_STATUS = ("imported", "duplicate", "incomplete", "failed")


class TrainingTest(Base):
    __tablename__ = "training_tests"
    __table_args__ = (
        UniqueConstraint("platform", "test_name", name="uq_training_test_platform_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(64), nullable=False)
    test_name = Column(String(256), nullable=False)
    latest_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainingTestVersion(Base):
    __tablename__ = "training_test_versions"
    __table_args__ = (
        UniqueConstraint("test_id", "version_number", name="uq_training_test_version_number"),
        UniqueConstraint("test_id", "content_sha256", name="uq_training_test_content"),
        CheckConstraint(
            "completeness IN ('complete', 'incomplete')",
            name="ck_training_test_versions_completeness",
        ),
        Index("idx_training_test_versions_test_id", "test_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_id = Column(
        Integer,
        ForeignKey("training_tests.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number = Column(Integer, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    ground_truth = Column(JSON, nullable=False)
    fip_info = Column(JSON, nullable=False)
    completeness = Column(String(16), nullable=False)
    missing_files = Column(JSON, nullable=True)
    import_id = Column(String(64), nullable=False)
    sample_class = Column(String(64), nullable=True)
    domain = Column(String(128), nullable=True)
    fault_type = Column(String(256), nullable=True)
    round_1_parse_status = Column(String(32), nullable=True)
    round_2_parse_status = Column(String(32), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrainingTestLog(Base):
    __tablename__ = "training_test_logs"
    __table_args__ = (
        UniqueConstraint(
            "test_version_id",
            "round_no",
            "log_type",
            name="uq_training_test_log_round_type",
        ),
        CheckConstraint("round_no IN (1, 2)", name="ck_training_test_logs_round_no"),
        CheckConstraint(
            "log_type IN ('qemu_console', 'fault_events', 'system_metrics')",
            name="ck_training_test_logs_log_type",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_no = Column(Integer, nullable=False)
    log_type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    byte_count = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrainingImportItem(Base):
    __tablename__ = "training_import_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('imported', 'duplicate', 'incomplete', 'failed')",
            name="ck_training_import_items_status",
        ),
        UniqueConstraint("import_id", "platform", "test_name", name="uq_training_import_item_test"),
        Index("idx_training_import_items_import_id", "import_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(
        String(64),
        ForeignKey("dataset_imports.import_id", ondelete="CASCADE"),
        nullable=False,
    )
    test_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform = Column(String(64), nullable=False)
    test_name = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    parse_status = Column(String(32), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
