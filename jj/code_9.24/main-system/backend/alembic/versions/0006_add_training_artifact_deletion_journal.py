"""add durable training artifact deletion journal

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_artifacts", sa.Column("deletion_state", sa.String(length=32), nullable=True))
    op.add_column("training_artifacts", sa.Column("quarantine_name", sa.String(length=128), nullable=True))
    op.add_column("training_artifacts", sa.Column("deletion_updated_at", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_training_artifacts_deletion_state",
        "training_artifacts",
        "deletion_state IS NULL OR deletion_state IN ('staged', 'pending_cleanup', 'recovery_required', 'cleaned')",
    )
    op.create_check_constraint(
        "ck_training_artifacts_deletion_journal",
        "training_artifacts",
        "(deletion_state IS NULL AND quarantine_name IS NULL AND deletion_updated_at IS NULL) OR "
        "(deletion_state = 'recovery_required' AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
        "(deletion_state IN ('staged', 'pending_cleanup') AND deleted_at IS NOT NULL AND quarantine_name IS NOT NULL AND deletion_updated_at IS NOT NULL) OR "
        "(deletion_state = 'cleaned' AND deleted_at IS NOT NULL AND quarantine_name IS NULL AND deletion_updated_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_training_artifacts_quarantine_name",
        "training_artifacts",
        "quarantine_name IS NULL OR (quarantine_name NOT LIKE '%/%' AND quarantine_name NOT IN ('', '.', '..'))",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM training_artifacts
                WHERE deletion_state IN ('staged', 'pending_cleanup', 'recovery_required')
            ) THEN
                RAISE EXCEPTION 'cannot downgrade with unresolved training artifact deletions';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint("ck_training_artifacts_quarantine_name", "training_artifacts", type_="check")
    op.drop_constraint("ck_training_artifacts_deletion_journal", "training_artifacts", type_="check")
    op.drop_constraint("ck_training_artifacts_deletion_state", "training_artifacts", type_="check")
    op.drop_column("training_artifacts", "deletion_updated_at")
    op.drop_column("training_artifacts", "quarantine_name")
    op.drop_column("training_artifacts", "deletion_state")
