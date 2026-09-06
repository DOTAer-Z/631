from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


TASK_TYPES = ("cpt", "sft")
JOB_KINDS = ("training", "evaluation")
TASK_STATES = (
    "queued", "preparing_data", "training", "evaluating", "cancelling",
    "cancelled", "succeeded", "failed", "interrupted",
)
SPLIT_NAMES = ("train", "validation", "test")
ARTIFACT_TYPES = (
    "final_adapter", "checkpoint", "tokenizer", "config", "split",
    "dataset", "log", "evaluation_report",
)
ARTIFACT_DELETION_STATES = ("staged", "pending_cleanup", "recovery_required", "cleaned")
TASK_CLEANUP_STATES = ("pending", "cleaned")


def _uuid_string() -> str:
    return str(uuid4())


class TrainingTask(Base):
    __tablename__ = "training_tasks"
    __table_args__ = (
        CheckConstraint("task_type IN ('cpt', 'sft')", name="ck_training_tasks_task_type"),
        CheckConstraint("job_kind IN ('training', 'evaluation')", name="ck_training_tasks_job_kind"),
        CheckConstraint(
            "state IN ('queued', 'preparing_data', 'training', 'evaluating', 'cancelling', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_training_tasks_state",
        ),
        CheckConstraint(
            "cleanup_state IS NULL OR cleanup_state IN ('pending', 'cleaned')",
            name="ck_training_tasks_cleanup_state",
        ),
        CheckConstraint(
            "(cleanup_state IS NULL AND cleanup_paths IS NULL AND cleanup_updated_at IS NULL) OR "
            "(cleanup_state IN ('pending', 'cleaned') AND cleanup_paths IS NOT NULL AND cleanup_updated_at IS NOT NULL)",
            name="ck_training_tasks_cleanup_journal",
        ),
        Index("idx_training_tasks_state_queued_at", "state", "queued_at"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    name = Column(String(255), nullable=False)
    task_type = Column(String(16), nullable=False)
    job_kind = Column(String(16), nullable=False, default="training")
    state = Column(String(32), nullable=False, default="queued")
    model_id = Column(String(128), nullable=False)
    cpt_adapter_artifact_id = Column(
        String(36),
        ForeignKey("training_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    resume_checkpoint_artifact_id = Column(
        String(36),
        ForeignKey("training_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    config_snapshot = Column(JSON, nullable=False)
    split_seed = Column(Integer, nullable=True)
    worker_id = Column(String(128), nullable=True)
    progress = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    queued_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    cancel_requested_at = Column(DateTime, nullable=True)
    cleanup_state = Column(String(32), nullable=True)
    cleanup_paths = Column(JSON, nullable=True)
    cleanup_updated_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)


class TrainingTaskTest(Base):
    __tablename__ = "training_task_tests"
    __table_args__ = (
        UniqueConstraint("task_id", "test_version_id", name="uq_training_task_test_version"),
        CheckConstraint(
            "split_name IN ('train', 'validation', 'test')",
            name="ck_training_task_tests_split_name",
        ),
        Index("idx_training_task_tests_test_version_id", "test_version_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    split_name = Column(String(16), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrainingEvaluation(Base):
    __tablename__ = "training_evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'preparing_data', 'training', 'evaluating', 'cancelling', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_training_evaluations_status",
        ),
        CheckConstraint(
            "(source_sft_task_id IS NOT NULL AND source_sft_artifact_id IS NULL) OR "
            "(source_sft_task_id IS NULL AND source_sft_artifact_id IS NOT NULL)",
            name="ck_training_evaluations_exactly_one_source",
        ),
        Index(
            "idx_training_evaluations_source_sft_artifact_id",
            "source_sft_artifact_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    source_sft_task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_sft_artifact_id = Column(
        String(36),
        ForeignKey("training_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status = Column(String(32), nullable=False, default="queued")
    summary = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrainingMetric(Base):
    __tablename__ = "training_metrics"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "stream_offset",
            name="uq_training_metrics_task_stream_offset",
        ),
        Index("idx_training_metrics_task_id", "task_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    stream_offset = Column(BigInteger, nullable=False)
    step = Column(Integer, nullable=True)
    epoch = Column(Float, nullable=True)
    loss = Column(Float, nullable=True)
    eval_loss = Column(Float, nullable=True)
    learning_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TrainingArtifact(Base):
    __tablename__ = "training_artifacts"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('final_adapter', 'checkpoint', 'tokenizer', 'config', 'split', "
            "'dataset', 'log', 'evaluation_report')",
            name="ck_training_artifacts_artifact_type",
        ),
        CheckConstraint("relative_path NOT LIKE '/%'", name="ck_training_artifacts_relative_path_root"),
        CheckConstraint("relative_path NOT LIKE '../%'", name="ck_training_artifacts_relative_path_parent"),
        CheckConstraint(
            "deletion_state IS NULL OR deletion_state IN ('staged', 'pending_cleanup', 'recovery_required', 'cleaned')",
            name="ck_training_artifacts_deletion_state",
        ),
        CheckConstraint(
            "(deletion_state IS NULL AND quarantine_name IS NULL AND deletion_updated_at IS NULL) OR "
            "(deletion_state = 'recovery_required' AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
            "(deletion_state IN ('staged', 'pending_cleanup') AND deleted_at IS NOT NULL AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
            "(deletion_state = 'cleaned' AND deleted_at IS NOT NULL AND quarantine_name IS NULL AND deletion_updated_at IS NOT NULL)",
            name="ck_training_artifacts_deletion_journal",
        ),
        CheckConstraint(
            "quarantine_name IS NULL OR (quarantine_name NOT LIKE '%/%' AND quarantine_name NOT IN ('', '.', '..'))",
            name="ck_training_artifacts_quarantine_name",
        ),
        Index("idx_training_artifacts_task_id", "task_id"),
    )

    id = Column(String(36), primary_key=True, default=_uuid_string)
    task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_type = Column(String(32), nullable=False)
    relative_path = Column(String(1024), nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    deletion_state = Column(String(32), nullable=True)
    quarantine_name = Column(String(128), nullable=True)
    deletion_updated_at = Column(DateTime, nullable=True)


class TrainingWorker(Base):
    __tablename__ = "training_workers"

    id = Column(String(128), primary_key=True)
    status = Column(String(32), nullable=False, default="offline")
    current_task_id = Column(
        String(36),
        ForeignKey("training_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_status = Column(String(32), nullable=True)
    gpu_snapshot = Column(JSON, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
