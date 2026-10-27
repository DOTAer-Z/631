"""add dataset_imports

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("stored_filename", sa.String(length=512), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("file_ext", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", name="uq_dataset_imports_import_id"),
        sa.UniqueConstraint("sha256", "is_deleted", name="uq_dataset_imports_sha256_active"),
    )
    op.create_index("idx_dataset_imports_created_at", "dataset_imports", ["created_at"])
    op.create_index("idx_dataset_imports_status", "dataset_imports", ["status"])
    op.create_index("idx_dataset_imports_file_ext", "dataset_imports", ["file_ext"])


def downgrade() -> None:
    op.drop_index("idx_dataset_imports_file_ext", table_name="dataset_imports")
    op.drop_index("idx_dataset_imports_status", table_name="dataset_imports")
    op.drop_index("idx_dataset_imports_created_at", table_name="dataset_imports")
    op.drop_table("dataset_imports")
