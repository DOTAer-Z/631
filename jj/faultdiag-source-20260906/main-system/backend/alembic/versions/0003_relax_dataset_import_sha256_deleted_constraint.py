"""relax dataset_import sha256/is_deleted uniqueness

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28
"""

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_dataset_imports_sha256_active", "dataset_imports", type_="unique")
    op.create_index(
        "idx_dataset_imports_sha256_is_deleted",
        "dataset_imports",
        ["sha256", "is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("idx_dataset_imports_sha256_is_deleted", table_name="dataset_imports")
    op.create_unique_constraint(
        "uq_dataset_imports_sha256_active",
        "dataset_imports",
        ["sha256", "is_deleted"],
    )
