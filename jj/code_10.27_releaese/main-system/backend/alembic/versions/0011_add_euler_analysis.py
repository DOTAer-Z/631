"""add opt-in analysis for explicitly saved Euler artifacts

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "euler_analysis_batches",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("model_source", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "euler_analysis_results",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("prediction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("model_source", sa.String(length=32), nullable=True),
        sa.Column("diagnosis_id", sa.Integer(), nullable=True),
        sa.Column("input_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_euler_analysis_results_batch_id", "euler_analysis_results", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_euler_analysis_results_batch_id", table_name="euler_analysis_results")
    op.drop_table("euler_analysis_results")
    op.drop_table("euler_analysis_batches")
