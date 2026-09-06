"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fault_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color_tag", sa.String(16), nullable=True, server_default="red"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_fault_types_id", "fault_types", ["id"])

    op.create_table(
        "log_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fault_type_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("chroma_doc_id", sa.String(64), nullable=True),
        sa.Column("is_indexed", sa.Boolean(), nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["fault_type_id"], ["fault_types.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chroma_doc_id"),
    )
    op.create_index("ix_log_entries_id", "log_entries", ["id"])

    op.create_table(
        "diagnosis_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("input_log", sa.Text(), nullable=False),
        sa.Column("channel_used", sa.String(8), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("matched_log_id", sa.Integer(), nullable=True),
        sa.Column("fault_type_id", sa.Integer(), nullable=True),
        sa.Column("fault_type_name", sa.String(128), nullable=True),
        sa.Column("llm_reasoning", sa.Text(), nullable=True),
        sa.Column("is_fault", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["matched_log_id"], ["log_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["fault_type_id"], ["fault_types.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnosis_records_id", "diagnosis_records", ["id"])

    op.create_table(
        "prediction_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("input_log", sa.Text(), nullable=False),
        sa.Column("cpu_usage", sa.Float(), nullable=True),
        sa.Column("memory_usage", sa.Float(), nullable=True),
        sa.Column("disk_usage", sa.Float(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("health_status", sa.String(8), nullable=False),
        sa.Column("risk_summary", sa.Text(), nullable=True),
        sa.Column("risk_details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prediction_records_id", "prediction_records", ["id"])


def downgrade() -> None:
    op.drop_table("prediction_records")
    op.drop_table("diagnosis_records")
    op.drop_table("log_entries")
    op.drop_table("fault_types")
