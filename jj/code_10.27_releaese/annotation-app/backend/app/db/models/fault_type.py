from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FaultType(Base):
    """User-managed fault-type dictionary. The single source of truth for the
    anomaly types selectable during annotation and recommendable by the LLM.

    This is the SHARED table owned by the main system (``fault_diagnosis``).
    Both subsystems point at the same ``fault_types`` table in the merged
    single-database deployment, so the schema here is the union superset:
    main's ``color_tag`` is tolerated, and ``description`` is nullable (the
    main system permits NULL). Note: only ``fault_types`` is shared — every
    other annotation table is namespaced with an ``ann_`` prefix.
    """

    __tablename__ = "fault_types"
    __table_args__ = (UniqueConstraint("name", name="uq_fault_types_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_tag: Mapped[str] = mapped_column(String(16), nullable=False, server_default="red", default="red")
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
