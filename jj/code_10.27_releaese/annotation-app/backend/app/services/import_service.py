from __future__ import annotations

import logging
import threading

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DatasetPackage, ImportTask
from app.services.import_worker import ImportWorker
from app.services.storage_service import StoredFile

logger = logging.getLogger(__name__)


class ImportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_upload(
        self,
        *,
        name: str,
        archive_type: str,
        stored_path: str,
        file_size: int,
        sha256: str,
        description: str | None,
    ) -> tuple[DatasetPackage, ImportTask]:
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
        self.db.flush()

        task = ImportTask(
            package_id=pkg.id,
            status="pending",
            error_message=None,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(pkg)
        self.db.refresh(task)
        return pkg, task

    def get_task(self, *, task_id: int) -> ImportTask | None:
        return self.db.get(ImportTask, task_id)

    def run_import_task(self, *, task_id: int) -> ImportTask:
        """同步跑完导入任务（测试 / 线程 runner 共用）。"""
        worker = ImportWorker(self.db)
        return worker.run(import_task_id=task_id)

    def create_from_data_import(
        self,
        *,
        import_id: str | int,
        name: str,
        stored: StoredFile,
        archive_type: str,
        description: str | None,
    ) -> tuple[DatasetPackage, ImportTask]:
        """从主系统「数据导入」建包 + 导入任务。

        归档字节已由调用方下载、校验并落盘为本地文件（StoredFile），本方法构造
        DatasetPackage + 待处理 ImportTask。provenance 沿用 source_type="main_system"
        + external_run_id=主系统数据导入 id。
        """
        pkg = DatasetPackage(
            name=name,
            archive_type=archive_type,
            stored_path=stored.stored_path,
            file_size=stored.file_size,
            sha256=stored.sha256,
            description=description,
            import_status="uploaded",
            import_error_message=None,
            source_file_count=0,
            source_line_count=0,
            cpu_count=0,
            module_count=0,
            earliest_timestamp=None,
            latest_timestamp=None,
            source_type="main_system",
            external_run_id=str(import_id),
        )
        self.db.add(pkg)
        self.db.flush()

        task = ImportTask(package_id=pkg.id, status="pending", error_message=None)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(pkg)
        self.db.refresh(task)
        return pkg, task

    def start_import_task_async(self, *, task_id: int, bind: Engine | None = None) -> None:
        engine = bind or self.db.get_bind()
        if engine.dialect.name == "sqlite" and engine.url.database in (None, "", ":memory:"):
            return
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

        def _runner() -> None:
            session = session_factory()
            try:
                try:
                    ImportService(session).run_import_task(task_id=task_id)
                except Exception:
                    logger.exception("import task %s failed in background thread", task_id)
            finally:
                session.close()

        thread = threading.Thread(
            target=_runner,
            name=f"import-task-{task_id}",
            daemon=True,
        )
        thread.start()
