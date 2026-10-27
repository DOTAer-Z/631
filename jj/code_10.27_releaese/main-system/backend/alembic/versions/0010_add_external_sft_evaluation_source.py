"""add external SFT evaluation source

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


_XOR_SQL = (
    "(source_sft_task_id IS NOT NULL AND source_sft_artifact_id IS NULL) OR "
    "(source_sft_task_id IS NULL AND source_sft_artifact_id IS NOT NULL)"
)
_DOWNGRADE_ERROR = "cannot downgrade while external SFT evaluations exist"


def upgrade() -> None:
    op.alter_column(
        "training_evaluations",
        "source_sft_task_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    op.add_column(
        "training_evaluations",
        sa.Column("source_sft_artifact_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_training_evaluations_source_sft_artifact",
        "training_evaluations",
        "training_artifacts",
        ["source_sft_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_training_evaluations_source_sft_artifact_id",
        "training_evaluations",
        ["source_sft_artifact_id"],
    )
    op.create_check_constraint(
        "ck_training_evaluations_exactly_one_source",
        "training_evaluations",
        _XOR_SQL,
    )


def _assert_downgrade_is_safe() -> None:
    if context.is_offline_mode():
        op.execute(
            sa.text(
                """
DO $external_sft_downgrade$
DECLARE
    external_evaluation_count bigint;
BEGIN
    SELECT count(*) INTO external_evaluation_count
    FROM training_evaluations
    WHERE source_sft_artifact_id IS NOT NULL;
    IF external_evaluation_count <> 0 THEN
        RAISE EXCEPTION 'cannot downgrade while external SFT evaluations exist';
    END IF;
END
$external_sft_downgrade$
"""
            )
        )
        return

    external_evaluation_count = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM training_evaluations "
            "WHERE source_sft_artifact_id IS NOT NULL"
        )
    ).scalar_one()
    if external_evaluation_count:
        raise RuntimeError(_DOWNGRADE_ERROR)


def downgrade() -> None:
    _assert_downgrade_is_safe()
    op.drop_constraint(
        "ck_training_evaluations_exactly_one_source",
        "training_evaluations",
        type_="check",
    )
    op.drop_index(
        "idx_training_evaluations_source_sft_artifact_id",
        table_name="training_evaluations",
    )
    op.drop_constraint(
        "fk_training_evaluations_source_sft_artifact",
        "training_evaluations",
        type_="foreignkey",
    )
    op.drop_column("training_evaluations", "source_sft_artifact_id")
    op.alter_column(
        "training_evaluations",
        "source_sft_task_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
