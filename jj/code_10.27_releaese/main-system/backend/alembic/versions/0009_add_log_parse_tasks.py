"""add cancellable log parse tasks

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "log_parse_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stage", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_run_id", sa.String(length=255), nullable=True),
        sa.Column("completed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fail_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fail_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed')",
            name="ck_log_parse_tasks_state",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_log_parse_tasks_progress",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_log_parse_tasks_state_created_at",
        "log_parse_tasks",
        ["state", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_log_parse_tasks_state_created_at",
        table_name="log_parse_tasks",
    )
    op.drop_table("log_parse_tasks")
