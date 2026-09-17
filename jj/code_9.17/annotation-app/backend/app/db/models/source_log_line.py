from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.slice_window_line import SliceWindowLine
    from app.db.models.source_log_file import SourceLogFile


class SourceLogLine(Base):
    __tablename__ = "ann_source_log_lines"
    __table_args__ = (
        Index("idx_source_log_lines_timestamp", "timestamp"),
        Index("idx_source_log_lines_source_file_id_line_no", "source_file_id", "line_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_source_log_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source_file: Mapped["SourceLogFile"] = relationship(back_populates="lines")
    window_links: Mapped[list["SliceWindowLine"]] = relationship(
        back_populates="source_line",
        cascade="all, delete-orphan",
    )
