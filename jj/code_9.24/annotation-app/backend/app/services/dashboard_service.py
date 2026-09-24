from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Annotation, DatasetPackage, SliceTask, SliceWindow
from app.schemas.dashboard import (
    DashboardSummaryResponse,
    RecentAnnotationItem,
    RecentPackageItem,
    RecentSliceTaskItem,
)


class DashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_summary(self) -> DashboardSummaryResponse:
        total_packages = int(self.db.scalar(select(func.count(DatasetPackage.id))) or 0)
        total_slice_tasks = int(self.db.scalar(select(func.count(SliceTask.id))) or 0)
        # Counts are leaf-scoped: a subdivided (parent) window is no longer an
        # annotation target, so it is excluded everywhere here.
        total_windows = int(
            self.db.scalar(
                select(func.count(SliceWindow.id)).where(SliceWindow.has_children.is_(False))
            )
            or 0
        )
        # A leaf window counts as "annotated" if it has >=1 annotation (a window
        # may now hold several, so count distinct windows, not annotation rows).
        annotated_windows = int(
            self.db.scalar(
                select(func.count(func.distinct(Annotation.slice_window_id)))
                .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
                .where(SliceWindow.has_children.is_(False))
            )
            or 0
        )
        # normal_count / abnormal_count are per-annotation-row tallies over leaf
        # windows (matches the "one annotation per export row" model).
        normal_count = int(
            self.db.scalar(
                select(func.count(Annotation.id))
                .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
                .where(SliceWindow.has_children.is_(False))
                .where(Annotation.label == "normal")
            )
            or 0
        )
        abnormal_count = int(
            self.db.scalar(
                select(func.count(Annotation.id))
                .join(SliceWindow, SliceWindow.id == Annotation.slice_window_id)
                .where(SliceWindow.has_children.is_(False))
                .where(Annotation.label == "abnormal")
            )
            or 0
        )
        # 按数据种类拆三桶：数据包直接按 data_kind；窗口(叶子)经 SliceTask→DatasetPackage 取包的 data_kind。
        unstructured_packages = int(
            self.db.scalar(
                select(func.count(DatasetPackage.id)).where(DatasetPackage.data_kind == "unstructured")
            )
            or 0
        )
        semi_structured_packages = int(
            self.db.scalar(
                select(func.count(DatasetPackage.id)).where(DatasetPackage.data_kind == "semi_structured")
            )
            or 0
        )
        structured_packages = int(
            self.db.scalar(
                select(func.count(DatasetPackage.id)).where(DatasetPackage.data_kind == "structured")
            )
            or 0
        )
        unstructured_windows = int(
            self.db.scalar(
                select(func.count(SliceWindow.id))
                .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
                .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
                .where(SliceWindow.has_children.is_(False))
                .where(DatasetPackage.data_kind == "unstructured")
            )
            or 0
        )
        semi_structured_windows = int(
            self.db.scalar(
                select(func.count(SliceWindow.id))
                .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
                .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
                .where(SliceWindow.has_children.is_(False))
                .where(DatasetPackage.data_kind == "semi_structured")
            )
            or 0
        )
        structured_windows = int(
            self.db.scalar(
                select(func.count(SliceWindow.id))
                .join(SliceTask, SliceTask.id == SliceWindow.slice_task_id)
                .join(DatasetPackage, DatasetPackage.id == SliceTask.package_id)
                .where(SliceWindow.has_children.is_(False))
                .where(DatasetPackage.data_kind == "structured")
            )
            or 0
        )
        return DashboardSummaryResponse(
            total_packages=total_packages,
            total_slice_tasks=total_slice_tasks,
            total_windows=total_windows,
            annotated_windows=annotated_windows,
            pending_windows=max(total_windows - annotated_windows, 0),
            normal_count=normal_count,
            abnormal_count=abnormal_count,
            structured_packages=structured_packages,
            semi_structured_packages=semi_structured_packages,
            unstructured_packages=unstructured_packages,
            structured_windows=structured_windows,
            semi_structured_windows=semi_structured_windows,
            unstructured_windows=unstructured_windows,
        )

    def get_recent_packages(self, *, limit: int) -> list[RecentPackageItem]:
        rows = list(
            self.db.scalars(
                select(DatasetPackage).order_by(DatasetPackage.created_at.desc(), DatasetPackage.id.desc()).limit(limit)
            ).all()
        )
        return [
            RecentPackageItem(
                id=row.id,
                name=row.name,
                import_status=row.import_status,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_recent_slice_tasks(self, *, limit: int) -> list[RecentSliceTaskItem]:
        rows = list(
            self.db.scalars(
                select(SliceTask).order_by(SliceTask.created_at.desc(), SliceTask.id.desc()).limit(limit)
            ).all()
        )
        return [
            RecentSliceTaskItem(
                id=row.id,
                package_id=row.package_id,
                name=row.name,
                status=row.status,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_recent_annotations(self, *, limit: int) -> list[RecentAnnotationItem]:
        rows = list(
            self.db.scalars(
                select(Annotation).order_by(Annotation.updated_at.desc(), Annotation.id.desc()).limit(limit)
            ).all()
        )
        return [
            RecentAnnotationItem(
                id=row.id,
                slice_window_id=row.slice_window_id,
                label=row.label,
                anomaly_type=row.anomaly_type,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
