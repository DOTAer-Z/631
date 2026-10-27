"""merged annotation schema (single-database deploy)

Revision ID: 20260905_0001
Revises: None
Create Date: 2026-09-05

This is the consolidated initial migration for the annotation subsystem under
the single-database deployment. The previous 17-revision chain
(20260531_0001 … 20260905_0017) is deliberately collapsed into this one
migration: the old ``data_bj`` database and its data are discarded, so the
history carries no value and the in-place renames of the old chain (which used
``batch_alter_table`` / drop-and-recreate) would have been error-prone.

Two consequences of the same-origin merge:

* Every annotation table is namespaced with an ``ann_`` prefix to keep it
  disjoint from the main system's ``cases`` / ``runs`` / ``log_entries`` /
  ``diagnosis_records`` / ``systems`` tables.
* ``fault_types`` is NOT created here. It is owned by the main system, which
  builds it via ``create_all`` (``fault_diagnosis`` database) before this
  migration runs (``backend-annotate`` depends on ``backend`` being healthy).
  The annotation subsystem only references it through the FK on
  ``ann_fault_type_suggestions.accepted_fault_type_id``.

This migration runs against its own Alembic version table
(``alembic_version_annotate``, see ``alembic/env.py``) so it never collides
with the main system's ``alembic_version``.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260905_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- ann_dataset_packages ---
    op.create_table(
        "ann_dataset_packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("archive_type", sa.String(length=20), nullable=False),
        sa.Column("stored_path", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("import_status", sa.String(length=20), server_default="uploaded", nullable=False),
        sa.Column("import_error_message", sa.Text(), nullable=True),
        sa.Column("import_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_line_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cpu_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("module_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("earliest_timestamp", sa.Float(), nullable=True),
        sa.Column("latest_timestamp", sa.Float(), nullable=True),
        sa.Column(
            "data_kind", sa.String(length=20), server_default="semi_structured", nullable=False
        ),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("external_run_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "import_status IN ('uploaded', 'importing', 'imported', 'failed')",
            name="ck_dataset_packages_import_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ann_dataset_packages_name", "ann_dataset_packages", ["name"])
    op.create_index("ix_ann_dataset_packages_created_at", "ann_dataset_packages", ["created_at"])
    op.create_unique_constraint(
        "uq_ann_dataset_packages_stored_path", "ann_dataset_packages", ["stored_path"]
    )

    # --- ann_import_tasks ---
    op.create_table(
        "ann_import_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_import_tasks_status",
        ),
        sa.ForeignKeyConstraint(["package_id"], ["ann_dataset_packages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- ann_source_log_files ---
    op.create_table(
        "ann_source_log_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("cpu_name", sa.String(length=255), nullable=False),
        sa.Column("module_path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("logical_path", sa.String(length=2048), nullable=False),
        sa.Column(
            "data_kind", sa.String(length=20), server_default="semi_structured", nullable=False
        ),
        sa.Column("line_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("earliest_timestamp", sa.Float(), nullable=True),
        sa.Column("latest_timestamp", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["package_id"], ["ann_dataset_packages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- ann_source_log_lines ---
    op.create_table(
        "ann_source_log_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_file_id"], ["ann_source_log_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_source_log_lines_timestamp", "ann_source_log_lines", ["timestamp"])
    op.create_index(
        "idx_source_log_lines_source_file_id_line_no",
        "ann_source_log_lines",
        ["source_file_id", "line_no"],
    )

    # --- ann_slice_tasks ---
    op.create_table(
        "ann_slice_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("total_files", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_windows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_slice_tasks_status",
        ),
        sa.ForeignKeyConstraint(["package_id"], ["ann_dataset_packages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- ann_slice_windows ---
    op.create_table(
        "ann_slice_windows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_task_id", sa.Integer(), nullable=False),
        sa.Column("parent_window_id", sa.Integer(), nullable=True),
        sa.Column("has_children", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("window_start_ts", sa.Float(), nullable=False),
        sa.Column("window_end_ts", sa.Float(), nullable=False),
        sa.Column("segment_title", sa.String(length=512), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cpu_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("module_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["slice_task_id"], ["ann_slice_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_slice_windows_parent", "ann_slice_windows", ["parent_window_id"])

    # --- ann_slice_window_lines ---
    op.create_table(
        "ann_slice_window_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_window_id", sa.Integer(), nullable=False),
        sa.Column("source_line_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["slice_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_line_id"], ["ann_source_log_lines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "slice_window_id", "source_line_id", name="uq_slice_window_lines_window_line"
        ),
    )
    op.create_index(
        "idx_slice_window_lines_slice_window_id", "ann_slice_window_lines", ["slice_window_id"]
    )
    op.create_index(
        "idx_slice_window_lines_source_line_id", "ann_slice_window_lines", ["source_line_id"]
    )

    # --- ann_annotations ---
    op.create_table(
        "ann_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_window_id", sa.Integer(), nullable=False),
        sa.Column("source_log_file_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("anomaly_type", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("label IN ('normal', 'abnormal')", name="ck_annotations_label"),
        sa.CheckConstraint(
            "(label = 'normal' AND anomaly_type IS NULL) OR "
            "(label = 'abnormal' AND anomaly_type IS NOT NULL)",
            name="ck_annotations_label_anomaly_type",
        ),
        sa.ForeignKeyConstraint(["slice_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_log_file_id"], ["ann_source_log_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_annotations_window_file",
        "ann_annotations",
        ["slice_window_id", "source_log_file_id"],
        unique=True,
        postgresql_where=sa.text("source_log_file_id IS NOT NULL"),
        sqlite_where=sa.text("source_log_file_id IS NOT NULL"),
    )
    op.create_index(
        "uq_annotations_window_whole",
        "ann_annotations",
        ["slice_window_id"],
        unique=True,
        postgresql_where=sa.text("source_log_file_id IS NULL"),
        sqlite_where=sa.text("source_log_file_id IS NULL"),
    )
    op.create_index("idx_annotations_label", "ann_annotations", ["label"])
    op.create_index("idx_annotations_anomaly_type", "ann_annotations", ["anomaly_type"])

    # --- ann_annotation_recommendations ---
    op.create_table(
        "ann_annotation_recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_window_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("recommended_label", sa.String(length=20), nullable=True),
        sa.Column("recommended_anomaly_type", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="ck_annotation_recommendations_status",
        ),
        sa.ForeignKeyConstraint(["slice_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slice_window_id", name="uq_annotation_recommendations_window_id"),
    )

    # --- ann_fault_type_suggestions ---
    op.create_table(
        "ann_fault_type_suggestions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_window_id", sa.Integer(), nullable=False),
        sa.Column("suggested_name", sa.String(length=128), nullable=False),
        sa.Column("suggested_description", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("accepted_fault_type_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_fault_type_suggestions_status",
        ),
        # Shared table owned by the main system (fault_diagnosis database).
        sa.ForeignKeyConstraint(
            ["slice_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["accepted_fault_type_id"], ["fault_types.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- ann_window_analyses ---
    op.create_table(
        "ann_window_analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slice_window_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("multiple_faults", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("suggest_subdivide", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("suggested_window_seconds", sa.Integer(), nullable=True),
        sa.Column("files_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed')", name="ck_window_analyses_status"
        ),
        sa.ForeignKeyConstraint(["slice_window_id"], ["ann_slice_windows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slice_window_id", name="uq_window_analyses_window_id"),
    )


def downgrade() -> None:
    # Reverse order of creation.
    op.drop_table("ann_window_analyses")
    op.drop_table("ann_fault_type_suggestions")
    op.drop_table("ann_annotation_recommendations")
    op.drop_table("ann_annotations")
    op.drop_table("ann_slice_window_lines")
    op.drop_table("ann_slice_windows")
    op.drop_table("ann_slice_tasks")
    op.drop_table("ann_source_log_lines")
    op.drop_table("ann_source_log_files")
    op.drop_table("ann_import_tasks")
    op.drop_table("ann_dataset_packages")
