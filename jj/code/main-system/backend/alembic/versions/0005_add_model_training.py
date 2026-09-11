"""add immutable model training schema

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_tests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "test_name", name="uq_training_test_platform_name"),
    )
    op.create_table(
        "training_test_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("test_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("ground_truth", sa.JSON(), nullable=False),
        sa.Column("fip_info", sa.JSON(), nullable=False),
        sa.Column("completeness", sa.String(length=16), nullable=False),
        sa.Column("missing_files", sa.JSON(), nullable=True),
        sa.Column("import_id", sa.String(length=64), nullable=False),
        sa.Column("sample_class", sa.String(length=64), nullable=True),
        sa.Column("domain", sa.String(length=128), nullable=True),
        sa.Column("fault_type", sa.String(length=256), nullable=True),
        sa.Column("round_1_parse_status", sa.String(length=32), nullable=True),
        sa.Column("round_2_parse_status", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "completeness IN ('complete', 'incomplete')",
            name="ck_training_test_versions_completeness",
        ),
        sa.ForeignKeyConstraint(["test_id"], ["training_tests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_id", "version_number", name="uq_training_test_version_number"),
        sa.UniqueConstraint("test_id", "content_sha256", name="uq_training_test_content"),
    )
    op.create_table(
        "training_test_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("test_version_id", sa.Integer(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("log_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("round_no IN (1, 2)", name="ck_training_test_logs_round_no"),
        sa.CheckConstraint(
            "log_type IN ('qemu_console', 'fault_events', 'system_metrics')",
            name="ck_training_test_logs_log_type",
        ),
        sa.ForeignKeyConstraint(["test_version_id"], ["training_test_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "test_version_id", "round_no", "log_type", name="uq_training_test_log_round_type"
        ),
    )
    op.create_table(
        "training_import_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.String(length=64), nullable=False),
        sa.Column("test_version_id", sa.Integer(), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("test_name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('imported', 'duplicate', 'incomplete', 'failed')",
            name="ck_training_import_items_status",
        ),
        sa.ForeignKeyConstraint(["import_id"], ["dataset_imports.import_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["test_version_id"], ["training_test_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", "platform", "test_name", name="uq_training_import_item_test"),
    )
    op.create_table(
        "training_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("task_type", sa.String(length=16), nullable=False),
        sa.Column("job_kind", sa.String(length=16), nullable=False, server_default="training"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("cpt_adapter_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("resume_checkpoint_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("split_seed", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("task_type IN ('cpt', 'sft')", name="ck_training_tasks_task_type"),
        sa.CheckConstraint("job_kind IN ('training', 'evaluation')", name="ck_training_tasks_job_kind"),
        sa.CheckConstraint(
            "state IN ('queued', 'preparing_data', 'training', 'evaluating', 'cancelling', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_training_tasks_state",
        ),
        sa.ForeignKeyConstraint(["parent_task_id"], ["training_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "training_task_tests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("test_version_id", sa.Integer(), nullable=False),
        sa.Column("split_name", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "split_name IN ('train', 'validation', 'test')",
            name="ck_training_task_tests_split_name",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["training_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["test_version_id"], ["training_test_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "test_version_id", name="uq_training_task_test_version"),
    )
    op.create_table(
        "training_evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("source_sft_task_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'preparing_data', 'training', 'evaluating', 'cancelling', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_training_evaluations_status",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["training_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_sft_task_id"], ["training_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_table(
        "training_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("step", sa.Integer(), nullable=True),
        sa.Column("epoch", sa.Float(), nullable=True),
        sa.Column("loss", sa.Float(), nullable=True),
        sa.Column("eval_loss", sa.Float(), nullable=True),
        sa.Column("learning_rate", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["task_id"], ["training_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "training_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "artifact_type IN ('final_adapter', 'checkpoint', 'tokenizer', 'config', 'split', "
            "'dataset', 'log', 'evaluation_report')",
            name="ck_training_artifacts_artifact_type",
        ),
        sa.CheckConstraint(
            "relative_path NOT LIKE '/%'", name="ck_training_artifacts_relative_path_root"
        ),
        sa.CheckConstraint(
            "relative_path NOT LIKE '../%'", name="ck_training_artifacts_relative_path_parent"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["training_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "training_workers",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="offline"),
        sa.Column("current_task_id", sa.String(length=36), nullable=True),
        sa.Column("model_status", sa.String(length=32), nullable=True),
        sa.Column("gpu_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["current_task_id"], ["training_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "training_tests", sa.Column("latest_version_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_training_tests_latest_version",
        "training_tests",
        "training_test_versions",
        ["latest_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_training_tasks_cpt_adapter_artifact",
        "training_tasks",
        "training_artifacts",
        ["cpt_adapter_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_training_tasks_resume_checkpoint_artifact",
        "training_tasks",
        "training_artifacts",
        ["resume_checkpoint_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "cases",
        sa.Column(
            "test_version_id",
            sa.Integer(),
            sa.ForeignKey("training_test_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "test_version_id",
            sa.Integer(),
            sa.ForeignKey("training_test_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    for counter in (
        "training_complete_count",
        "training_incomplete_count",
        "training_duplicate_count",
        "training_failed_count",
        "training_parse_failed_count",
    ):
        op.add_column(
            "dataset_imports",
            sa.Column(counter, sa.Integer(), nullable=False, server_default="0"),
        )

    op.create_index("idx_training_test_versions_test_id", "training_test_versions", ["test_id"])
    op.create_index("idx_training_import_items_import_id", "training_import_items", ["import_id"])
    op.create_index("idx_training_tasks_state_queued_at", "training_tasks", ["state", "queued_at"])
    op.create_index("idx_training_task_tests_test_version_id", "training_task_tests", ["test_version_id"])
    op.create_index("idx_training_metrics_task_id", "training_metrics", ["task_id"])
    op.create_index("idx_training_artifacts_task_id", "training_artifacts", ["task_id"])


def downgrade() -> None:
    op.drop_constraint("fk_training_tasks_resume_checkpoint_artifact", "training_tasks", type_="foreignkey")
    op.drop_constraint("fk_training_tasks_cpt_adapter_artifact", "training_tasks", type_="foreignkey")
    op.drop_constraint("fk_training_tests_latest_version", "training_tests", type_="foreignkey")
    op.drop_column("dataset_imports", "training_parse_failed_count")
    op.drop_column("dataset_imports", "training_failed_count")
    op.drop_column("dataset_imports", "training_duplicate_count")
    op.drop_column("dataset_imports", "training_incomplete_count")
    op.drop_column("dataset_imports", "training_complete_count")
    op.drop_column("runs", "test_version_id")
    op.drop_column("cases", "test_version_id")
    op.drop_column("training_tests", "latest_version_id")
    op.drop_table("training_workers")
    op.drop_table("training_artifacts")
    op.drop_table("training_metrics")
    op.drop_table("training_evaluations")
    op.drop_table("training_task_tests")
    op.drop_table("training_tasks")
    op.drop_table("training_import_items")
    op.drop_table("training_test_logs")
    op.drop_table("training_test_versions")
    op.drop_table("training_tests")
