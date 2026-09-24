from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.slice_window import SliceWindow


class Annotation(Base):
    __tablename__ = "ann_annotations"
    __table_args__ = (
        CheckConstraint("label IN ('normal', 'abnormal')", name="ck_annotations_label"),
        CheckConstraint(
            "("
            "(label = 'normal' AND anomaly_type IS NULL) OR "
            "(label = 'abnormal' AND anomaly_type IS NOT NULL)"
            ")",
            name="ck_annotations_label_anomaly_type",
        ),
        # A window may carry many annotations: at most one whole-window row
        # (source_log_file_id IS NULL) plus at most one row per bound file.
        # Composite UNIQUE alone cannot enforce "one whole-window row" because
        # NULL never compares equal to NULL in either Postgres or SQLite, so we
        # use two partial unique indexes. Both `postgresql_where` and
        # `sqlite_where` are required: `create_all` (tests) honors sqlite_where,
        # Alembic (Postgres) honors postgresql_where.
        Index(
            "uq_annotations_window_file",
            "slice_window_id",
            "source_log_file_id",
            unique=True,
            postgresql_where=text("source_log_file_id IS NOT NULL"),
            sqlite_where=text("source_log_file_id IS NOT NULL"),
        ),
        Index(
            "uq_annotations_window_whole",
            "slice_window_id",
            unique=True,
            postgresql_where=text("source_log_file_id IS NULL"),
            sqlite_where=text("source_log_file_id IS NULL"),
        ),
        Index("idx_annotations_label", "label"),
        Index("idx_annotations_anomaly_type", "anomaly_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL => whole-window annotation; non-NULL => bound to a specific source file.
    source_log_file_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ann_source_log_files.id", ondelete="CASCADE"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    anomaly_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    slice_window: Mapped["SliceWindow"] = relationship(back_populates="annotations")
