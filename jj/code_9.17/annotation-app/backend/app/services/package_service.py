from __future__ import annotations

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.core.errors import PackageNameConfirmMismatchError
from app.db.models import Annotation, DatasetPackage, SliceTask, SliceWindow
from app.schemas.package import PackageUpdateRequest


class PackageService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        archive_type: str,
        stored_path: str,
        file_size: int,
        sha256: str,
        description: str | None,
    ) -> DatasetPackage:
        pkg = DatasetPackage(
            name=name,
            archive_type=archive_type,
            stored_path=stored_path,
            file_size=file_size,
            sha256=sha256,
            description=description,
            import_status="uploaded",
            import_error_message=None,
            source_file_count=0,
            source_line_count=0,
            cpu_count=0,
            module_count=0,
            earliest_timestamp=None,
            latest_timestamp=None,
        )
        self.db.add(pkg)
        self.db.commit()
        self.db.refresh(pkg)
        return pkg

    def list(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[DatasetPackage], int]:
        stmt = select(DatasetPackage)
        count_stmt = select(func.count(DatasetPackage.id))

        if query:
            like_expr = f"%{query}%"
            filter_expr = DatasetPackage.name.ilike(like_expr) | DatasetPackage.description.ilike(like_expr)
            stmt = stmt.where(filter_expr)
            count_stmt = count_stmt.where(filter_expr)

        sort_column = DatasetPackage.created_at if sort_by == "created_at" else DatasetPackage.name
        order_expr = desc(sort_column) if sort_order == "desc" else asc(sort_column)

        stmt = stmt.order_by(order_expr).offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.scalars(stmt).all())
        total = int(self.db.scalar(count_stmt) or 0)
        return items, total

    def get_by_id(self, package_id: int) -> DatasetPackage | None:
        return self.db.get(DatasetPackage, package_id)

    def abnormal_package_ids(self, package_ids: list[int]) -> set[int]:
        """Return the subset of package ids that have at least one leaf window
        annotated as `abnormal`. Computed in one query to avoid N+1.

        Leaf-scoped (has_children == False): a window that was subdivided is not
        itself the annotation target — its children are.
        """
        if not package_ids:
            return set()
        rows = self.db.execute(
            select(SliceTask.package_id)
            .join(SliceWindow, SliceWindow.slice_task_id == SliceTask.id)
            .join(Annotation, Annotation.slice_window_id == SliceWindow.id)
            .where(SliceTask.package_id.in_(package_ids))
            .where(SliceWindow.has_children.is_(False))
            .where(Annotation.label == "abnormal")
            .distinct()
        ).all()
        return {int(row[0]) for row in rows}

    def update_description(self, package_id: int, payload: PackageUpdateRequest) -> DatasetPackage | None:
        pkg = self.db.get(DatasetPackage, package_id)
        if pkg is None:
            return None

        pkg.description = payload.description
        self.db.add(pkg)
        self.db.commit()
        self.db.refresh(pkg)
        return pkg

    def delete(self, package_id: int, *, package_name_confirm: str) -> DatasetPackage | None:
        pkg = self.db.get(DatasetPackage, package_id)
        if pkg is None:
            return None

        if pkg.name.strip().lower() != package_name_confirm.strip().lower():
            raise PackageNameConfirmMismatchError()

        self.db.delete(pkg)
        self.db.commit()
        return pkg
