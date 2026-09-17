"""add durable training task output cleanup journal

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "training_tasks",
        sa.Column("cleanup_state", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "training_tasks",
        sa.Column("cleanup_paths", sa.JSON(), nullable=True),
    )
    op.add_column(
        "training_tasks",
        sa.Column("cleanup_updated_at", sa.DateTime(), nullable=True),
    )
    op.create_check_constraint(
        "ck_training_tasks_cleanup_state",
        "training_tasks",
        "cleanup_state IS NULL OR cleanup_state IN ('pending', 'cleaned')",
    )
    op.create_check_constraint(
        "ck_training_tasks_cleanup_journal",
        "training_tasks",
        "(cleanup_state IS NULL AND cleanup_paths IS NULL AND cleanup_updated_at IS NULL) OR "
        "(cleanup_state IN ('pending', 'cleaned') AND cleanup_paths IS NOT NULL AND cleanup_updated_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM training_tasks WHERE cleanup_state = 'pending'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade with pending training task cleanup';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint(
        "ck_training_tasks_cleanup_journal", "training_tasks", type_="check"
    )
    op.drop_constraint(
        "ck_training_tasks_cleanup_state", "training_tasks", type_="check"
    )
    op.drop_column("training_tasks", "cleanup_updated_at")
    op.drop_column("training_tasks", "cleanup_paths")
    op.drop_column("training_tasks", "cleanup_state")
