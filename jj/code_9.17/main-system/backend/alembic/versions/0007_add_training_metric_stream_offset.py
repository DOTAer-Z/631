"""add durable training metric stream offsets

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "training_metrics",
        sa.Column(
            "stream_offset",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.execute(
        """
        WITH offsets AS (
            SELECT id, -ROW_NUMBER() OVER (
                PARTITION BY task_id ORDER BY created_at, id
            ) AS stream_offset
            FROM training_metrics
        )
        UPDATE training_metrics AS metric
        SET stream_offset = offsets.stream_offset
        FROM offsets
        WHERE metric.id = offsets.id
        """
    )
    op.alter_column(
        "training_metrics",
        "stream_offset",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_training_metrics_task_stream_offset",
        "training_metrics",
        ["task_id", "stream_offset"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_training_metrics_task_stream_offset",
        "training_metrics",
        type_="unique",
    )
    op.drop_column("training_metrics", "stream_offset")
