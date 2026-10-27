from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.core.config import get_settings

if TYPE_CHECKING:
    from app.db.models.annotation import Annotation
    from app.db.models.slice_task import SliceTask
    from app.db.models.slice_window_line import SliceWindowLine


class SliceWindow(Base):
    __tablename__ = "ann_slice_windows"
    __table_args__ = (
        Index("idx_slice_windows_parent", "parent_window_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slice_task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_slice_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Self-referential: a sub-window points at the window it was subdivided from.
    parent_window_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("ann_slice_windows.id", ondelete="CASCADE"),
        nullable=True,
    )
    # True once this window has been subdivided. Leaf windows (has_children=False)
    # are the unit counted by the dashboard / pending list / package badge.
    has_children: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    window_start_ts: Mapped[float] = mapped_column(Float, nullable=False)
    window_end_ts: Mapped[float] = mapped_column(Float, nullable=False)
    # 非结构化数据的语义段标题(如 "## 基本信息")。非空即表示该窗口是「语义段」而非时间窗口，
    # 此时 window_start_ts/window_end_ts 仅为段序号(i, i+1)，不应按时间戳渲染。
    segment_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    module_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    slice_task: Mapped["SliceTask"] = relationship(back_populates="windows")
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="slice_window",
        cascade="all, delete-orphan",
    )
    # (back_populates matches Annotation.slice_window; do not rename to ann_*)
    window_lines: Mapped[list["SliceWindowLine"]] = relationship(
        back_populates="slice_window",
        cascade="all, delete-orphan",
    )

    @property
    def record_count(self) -> int:
        return int(self.line_count)

    @record_count.setter
    def record_count(self, value: int) -> None:
        self.line_count = int(value)

    @property
    def invalid_line_count(self) -> int:
        if hasattr(self, "_invalid_line_count_override"):
            return int(self._invalid_line_count_override)
        manifest = Path(self.manifest_path)
        if manifest.exists():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            return int(payload.get("invalid_line_count", 0))
        return 0

    @invalid_line_count.setter
    def invalid_line_count(self, value: int) -> None:
        self._invalid_line_count_override = int(value)

    def _format_window_part(self, value: float) -> str:
        return str(int(value)) if float(value).is_integer() else str(value)

    @property
    def window_path(self) -> str:
        if hasattr(self, "_window_path_override"):
            return str(self._window_path_override)
        settings = get_settings()
        start = self._format_window_part(self.window_start_ts)
        end = self._format_window_part(self.window_end_ts)
        return str(settings.slices_dir / f"task_{self.slice_task_id}" / f"window_{start}_{end}")

    @window_path.setter
    def window_path(self, value: str) -> None:
        self._window_path_override = value

    @property
    def manifest_path(self) -> str:
        if hasattr(self, "_manifest_path_override"):
            return str(self._manifest_path_override)
        return str(Path(self.window_path) / "manifest.json")

    @manifest_path.setter
    def manifest_path(self, value: str) -> None:
        self._manifest_path_override = value
