from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.slice_window import SliceWindow


class AnnotationRecommendation(Base):
    """LLM-produced fault-type suggestion for a window. Pure reference cache —
    never written into `annotations` automatically (one-annotation-per-window rule)."""

    __tablename__ = "ann_annotation_recommendations"
    __table_args__ = (
        UniqueConstraint("slice_window_id", name="uq_annotation_recommendations_window_id"),
        CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="ck_annotation_recommendations_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    recommended_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommended_anomaly_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    slice_window: Mapped["SliceWindow"] = relationship()
