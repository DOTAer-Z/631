from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import SliceTaskNotAllowedError
from app.db.models import DatasetPackage, SliceTask
from app.services.slice_engine import parse_window_seconds
from app.services.slice_engine.task_runner import TaskRunner


class SliceTaskService:
    """Coordinates slice task CRUD and synchronous execution."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_and_run(
        self,
        *,
        package_id: int,
        name: str,
        window_seconds: int | None,
    ) -> SliceTask | None:
        package = self.db.get(DatasetPackage, package_id)
        if package is None:
            return None

        if package.import_status != "imported":
            raise SliceTaskNotAllowedError("Package import_status must be imported before slicing")

        parsed_window_seconds = parse_window_seconds(window_seconds)
        task = SliceTask(
            package_id=package_id,
            name=name,
            window_seconds=parsed_window_seconds,
            status="pending",
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        runner = TaskRunner(self.db)
        return runner.run(task=task, package=package)

    def list_for_package(self, *, package_id: int) -> tuple[DatasetPackage | None, list[SliceTask]]:
        package = self.db.get(DatasetPackage, package_id)
        if package is None:
            return None, []

        stmt = (
            select(SliceTask)
            .where(SliceTask.package_id == package_id)
            .options(selectinload(SliceTask.windows))
            .order_by(desc(SliceTask.created_at), desc(SliceTask.id))
        )
        tasks = list(self.db.scalars(stmt).all())
        return package, tasks

    def get_by_id(self, *, task_id: int) -> SliceTask | None:
        stmt = select(SliceTask).where(SliceTask.id == task_id).options(selectinload(SliceTask.windows))
        return self.db.scalar(stmt)

    def delete(self, *, task_id: int) -> SliceTask | None:
        task = self.db.get(SliceTask, task_id)
        if task is None:
            return None

        self.db.delete(task)
        self.db.commit()
        return task
