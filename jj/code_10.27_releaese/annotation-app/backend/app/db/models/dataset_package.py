from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.import_task import ImportTask
    from app.db.models.slice_task import SliceTask
    from app.db.models.source_log_file import SourceLogFile


class DatasetPackage(Base):
    __tablename__ = "ann_dataset_packages"
    __table_args__ = (
        CheckConstraint(
            "import_status IN ('uploaded', 'importing', 'imported', 'failed')",
            name="ck_dataset_packages_import_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    archive_type: Mapped[str] = mapped_column(String(20), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="uploaded",
        server_default="uploaded",
    )
    import_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    import_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    import_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_file_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    source_line_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    cpu_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    module_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    earliest_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 数据种类（三值）：'semi_structured'=日志（时间窗切片，默认，即原 'structured' 的语义重命名）；
    # 'unstructured'=非结构化报告(txt/pdf/md，按 `## ` 二级标题分段标注)；'structured'=预留监控/追踪等
    # 固定 schema 数据（本期无导入链路）。导入阶段自动判定。
    data_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="semi_structured",
        server_default="semi_structured",
    )
    # 数据来源：None/"upload"=本地上传归档；"main_system"=从主系统 DB1 导入。
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # source_type="main_system" 时记录主系统的 run_id（溯源 / 去重）。
    external_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    import_tasks: Mapped[list["ImportTask"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    source_log_files: Mapped[list["SourceLogFile"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    slice_tasks: Mapped[list["SliceTask"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
