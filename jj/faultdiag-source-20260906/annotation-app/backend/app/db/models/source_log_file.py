from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.dataset_package import DatasetPackage
    from app.db.models.source_log_line import SourceLogLine


class SourceLogFile(Base):
    __tablename__ = "ann_source_log_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ann_dataset_packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    cpu_name: Mapped[str] = mapped_column(String(255), nullable=False)
    module_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    logical_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    # 'semi_structured'=日志（时间窗切片）；'unstructured'=非结构化报告(按 `## ` 分段)。
    # 'structured'=预留（监控/追踪等固定 schema 数据）。随包判定同步。
    data_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="semi_structured",
        server_default="semi_structured",
    )
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    earliest_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    package: Mapped["DatasetPackage"] = relationship(back_populates="source_log_files")
    lines: Mapped[list["SourceLogLine"]] = relationship(
        back_populates="source_file",
        cascade="all, delete-orphan",
    )
