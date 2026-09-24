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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.fault_type import FaultType
    from app.db.models.slice_window import SliceWindow


class FaultTypeSuggestion(Base):
    """LLM-proposed new fault type that does not match the existing `fault_types`
    dictionary. Created by the recommendation pipeline's two-step fallback when
    none of the defined names fit the window. Strictly a queue of candidates —
    `fault_types` is updated only when a human accepts a row here."""

    __tablename__ = "ann_fault_type_suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_fault_type_suggestions_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_window_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=False,
    )
    suggested_name: Mapped[str] = mapped_column(String(128), nullable=False)
    suggested_description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    accepted_fault_type_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("fault_types.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    accepted_fault_type: Mapped["FaultType | None"] = relationship()
