from __future__ import annotations

import asyncio
from pathlib import Path

from app.api.v1.packages import create_package
from app.api.v1.slice_tasks import create_slice_task
from app.api.v1.slice_windows import list_windows, get_window_full, SliceWindowSubdivideRequest, subdivide_slice_window
from app.schemas.slice_task import SliceTaskCreateRequest
from app.services.import_service import ImportService

TESTDATA = Path(__file__).resolve().parents[3] / "testdata"


class _Upload:
    def __init__(self, path: Path) -> None:
        self.filename = path.name
        self.content_type = "application/zip"
        self._data = path.read_bytes()

    async def read(self) -> bytes:
        return self._data


def _import_and_slice(db_session, zip_name: str, window_seconds: int = 300):
    pkg = asyncio.run(
        create_package(
            file=_Upload(TESTDATA / zip_name),
            name=zip_name,
            description=None,
            db=db_session,
        )
    )
    ImportService(db_session).run_import_task(task_id=pkg.import_task_id)
    task = create_slice_task(
        package_id=pkg.id,
        payload=SliceTaskCreateRequest(name=f"{zip_name}-task", window_seconds=window_seconds),
        db=db_session,
    )
    windows = list_windows(
        task_id=task.id, page=1, page_size=50, sort_by="window_start_ts",
        sort_order="asc", start_ts=None, end_ts=None, db=db_session,
    )
    return pkg, task, windows


def test_pkg01_basic_imports_and_makes_multiple_windows(db_session, phase2_settings):
    # create_slice_task raises unless the package reached import_status='imported',
    # so a returned window list already proves the import succeeded.
    _, _, windows = _import_and_slice(db_session, "01_basic_normal.zip")
    assert windows.total >= 4  # ~5 windows of heartbeat


def test_pkg02_single_window_has_multiple_files(db_session, phase2_settings):
    _, _, windows = _import_and_slice(db_session, "02_multi_fault_same_window.zip")
    # All three files fall into the same first window.
    assert windows.total == 1
    full = get_window_full(window_id=windows.items[0].window_id, db=db_session)

    def count_files(nodes):
        n = 0
        for node in nodes:
            if node.type == "file":
                n += 1
            n += count_files(node.children)
        return n

    assert count_files(full.tree.root) == 3  # oom.log + lockup.log + service.log


def test_pkg03_subdivides_into_separate_bursts(db_session, phase2_settings):
    _, _, windows = _import_and_slice(db_session, "03_subdivide.zip")
    assert windows.total == 1
    parent_id = windows.items[0].window_id
    children = subdivide_slice_window(
        window_id=parent_id, payload=SliceWindowSubdivideRequest(window_seconds=60), db=db_session
    )
    # burst1 ~[0,60), quiet ~[120,180), burst2 ~[180,240) -> >=2 non-empty children
    assert len(children) >= 2


def test_pkg04_multicpu_iso_imports(db_session, phase2_settings):
    from app.services.package_service import PackageService

    pkg, _, windows = _import_and_slice(db_session, "04_iso_multicpu_abnormal.zip")
    fresh = PackageService(db_session).get_by_id(pkg.id)
    assert fresh.import_status == "imported"
    assert fresh.cpu_count == 2  # cpu0 + cpu1
    assert windows.total >= 1
