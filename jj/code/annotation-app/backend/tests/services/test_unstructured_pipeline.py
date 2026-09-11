from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from app.db.models import DatasetPackage, SliceWindow, SourceLogFile
from app.schemas.annotation import AnnotationCreateRequest
from app.services.annotation_service import AnnotationService
from app.services.archive_service import validate_archive
from app.services.import_service import ImportService
from app.services.import_worker import ImportWorker
from app.services.slice_task_service import SliceTaskService
from app.services.source_log_query_service import SourceLogQueryService
from app.services.storage_service import StorageService
from tests.fakes import FakeLLMClient


def _report(idx: int) -> str:
    return (
        f"# 嵌入式机载软件维护/故障报告\n\n"
        f"## 基本信息\n报告编号: R-{idx:04d}\n机型: X-100\n\n"
        f"## 故障现象与现场证据\n现象: 系统重启\n证据: 看门狗超时\n\n"
        f"## 分析与根本原因\n根本原因: 任务死锁\n\n"
        f"## 处理与恢复结果\n处理: 修复调度\n结果: 恢复正常\n"
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _import_reports(db_session, files: dict[str, str], name: str):
    file_bytes = _zip_bytes(files)
    validated = validate_archive(f"{name}.zip", "application/zip", file_bytes)
    stored = StorageService().save_package_bytes(file_bytes, validated.extension)
    service = ImportService(db_session)
    pkg, task = service.create_upload(
        name=name,
        archive_type=validated.archive_type,
        stored_path=stored.stored_path,
        file_size=stored.file_size,
        sha256=stored.sha256,
        description=None,
    )
    ImportWorker(db_session, llm_client=FakeLLMClient(configured=False)).run(import_task_id=task.id)
    return pkg


def test_unstructured_import_slice_browse_annotate_end_to_end(db_session, phase2_settings) -> None:
    files = {
        "data/reports/R-0001.txt": _report(1),
        "data/reports/R-0002.txt": _report(2),
    }
    pkg = _import_reports(db_session, files, "unstructured-pkg")

    # 导入阶段自动判定为非结构化,且逐文件同步。
    db_session.refresh(pkg)
    assert pkg.import_status == "imported"
    assert pkg.data_kind == "unstructured"
    src_files = list(
        db_session.scalars(
            select(SourceLogFile).where(SourceLogFile.package_id == pkg.id)
        ).all()
    )
    assert len(src_files) == 2
    assert all(f.data_kind == "unstructured" for f in src_files)
    # 完整正文逐行落库(时间戳全 None)。
    assert all(f.earliest_timestamp is None for f in src_files)

    # 切片:每份报告 4 段 -> 共 8 个窗口,每个窗口带 segment_title。
    task = SliceTaskService(db_session).create_and_run(
        package_id=pkg.id, name="seg", window_seconds=300
    )
    assert task is not None
    assert task.status == "success"
    assert task.total_windows == 8

    windows = list(
        db_session.scalars(
            select(SliceWindow).where(SliceWindow.slice_task_id == task.id)
        ).all()
    )
    assert len(windows) == 8
    assert all(w.segment_title for w in windows)
    titles = {w.segment_title for w in windows}
    assert titles == {"基本信息", "故障现象与现场证据", "分析与根本原因", "处理与恢复结果"}

    # 浏览:窗口详情/树/日志分页应可用(全 NULL 时间戳不再报错)。
    query = SourceLogQueryService(db_session)
    first = sorted(windows, key=lambda w: (w.window_start_ts, w.id))[0]
    full = query.get_window_full(window_id=first.id)
    assert full.window.segment_title == "基本信息"
    file_id = full.tree.root[0].children[0].children[0].source_file_id
    logs = query.get_window_logs(
        window_id=first.id, source_file_id=file_id, cursor=None, limit=200, keyword=None
    )
    assert logs.items
    assert all(item.timestamp is None for item in logs.items)
    assert logs.has_more is False

    # 标注:整窗标注应可保存。
    ann = AnnotationService(db_session).save_for_window(
        window_id=first.id,
        payload=AnnotationCreateRequest(label="abnormal", anomaly_type="deadlock", note="死锁"),
    )
    assert ann.id is not None
    assert ann.slice_window_id == first.id


def test_semi_structured_package_still_slices_by_time_window(db_session, phase2_settings) -> None:
    # 回归:日志包(半结构化)仍按时间窗口切片,data_kind 记为 semi_structured。
    files = {"data/cpu0/app.log": "1.0 a\n2.0 b\n301.0 c\n"}
    pkg = _import_reports(db_session, files, "semi-structured-pkg")
    db_session.refresh(pkg)
    assert pkg.data_kind == "semi_structured"

    task = SliceTaskService(db_session).create_and_run(
        package_id=pkg.id, name="time", window_seconds=300
    )
    assert task is not None
    assert task.status == "success"
    windows = list(
        db_session.scalars(
            select(SliceWindow).where(SliceWindow.slice_task_id == task.id)
        ).all()
    )
    # 两个时间窗口(0-300 含前两行,300-600 含第三行),均无 segment_title。
    assert len(windows) == 2
    assert all(w.segment_title is None for w in windows)
