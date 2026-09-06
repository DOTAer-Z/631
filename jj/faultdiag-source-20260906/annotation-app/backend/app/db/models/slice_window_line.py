from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SliceWindowLine(Base):
    __tablename__ = "ann_slice_window_lines"
    __table_args__ = (
        UniqueConstraint(
            "slice_window_id",
            "source_line_id",
            name="uq_slice_window_lines_window_line",
        ),
        Index("idx_slice_window_lines_slice_window_id", "slice_window_id"),
        Index("idx_slice_window_lines_source_line_id", "source_line_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_line_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_source_log_lines.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)

    slice_window = relationship("SliceWindow", back_populates="window_lines")
    source_line = relationship("SourceLogLine", back_populates="window_links")
