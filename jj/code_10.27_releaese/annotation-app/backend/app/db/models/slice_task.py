from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.core.config import get_settings

if TYPE_CHECKING:
    from app.db.models.dataset_package import DatasetPackage
    from app.db.models.slice_window import SliceWindow


class SliceTask(Base):
    __tablename__ = "ann_slice_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_slice_tasks_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_dataset_packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    total_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    total_lines: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    total_windows: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    package: Mapped["DatasetPackage"] = relationship(back_populates="slice_tasks")
    windows: Mapped[list["SliceWindow"]] = relationship(
        back_populates="slice_task",
        cascade="all, delete-orphan",
    )

    @property
    def extracted_path(self) -> str:
        if hasattr(self, "_extracted_path_override"):
            return str(self._extracted_path_override)
        settings = get_settings()
        return str(settings.extracted_dir / f"package_{self.package_id}" / f"task_{self.id}")

    @extracted_path.setter
    def extracted_path(self, value: str | None) -> None:
        self._extracted_path_override = value or ""

    @property
    def output_path(self) -> str:
        if hasattr(self, "_output_path_override"):
            return str(self._output_path_override)
        settings = get_settings()
        return str(settings.slices_dir / f"task_{self.id}")

    @output_path.setter
    def output_path(self, value: str | None) -> None:
        self._output_path_override = value or ""

    @property
    def retry_count(self) -> int:
        if hasattr(self, "_retry_count_override"):
            return int(self._retry_count_override)
        return 0

    @retry_count.setter
    def retry_count(self, value: int) -> None:
        self._retry_count_override = int(value)
