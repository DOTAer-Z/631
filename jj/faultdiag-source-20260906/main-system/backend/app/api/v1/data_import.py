import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.database import SessionLocal
from app.models.dataset_import import DatasetImport
from app.models.run import Run
from sqlalchemy.orm.attributes import flag_modified
from app.schemas.data_import import (
    DataImportDeleteOut,
    DataImportItem,
    DataImportListOut,
    DataImportPreviewOut,
    DataImportUpdateRequest,
)
from app.services.data_import_ingest_service import (
    IngestPipelineError,
    run_ingest_pipeline,
)
from app.services.data_import_service import (
    detect_archive_extension,
    ensure_safe_storage_path,
    finalize_atomic_move,
    remove_file_if_exists,
    write_upload_to_temp,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _ingested_case_ids(record: DatasetImport) -> List[str]:
    raw = record.ingested_case_ids
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _compute_sha256_from_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _allowed_extensions() -> set[str]:
    return {
        token.strip().lower()
        for token in settings.DATA_IMPORT_ALLOWED_EXTS.split(",")
        if token.strip()
    }


def _normalize_tags(tags_raw: Optional[str]) -> List[str]:
    if tags_raw is None:
        return []
    trimmed = tags_raw.strip()
    if not trimmed:
        return []
    if trimmed.startswith("["):
        try:
            values = json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise ValueError("tags must be a JSON array or comma-separated string") from exc
        if not isinstance(values, list):
            raise ValueError("tags JSON must be an array")
        return [str(value).strip() for value in values if str(value).strip()]
    return [part.strip() for part in trimmed.split(",") if part.strip()]


def _record_tags(record: DatasetImport) -> List[str]:
    raw = record.tags_json
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [str(tag) for tag in raw if str(tag).strip()]
    return []


def _to_item(record: DatasetImport) -> DataImportItem:
    case_ids = _ingested_case_ids(record)
    return DataImportItem(
        import_id=record.import_id,
        original_filename=record.original_filename,
        file_ext=record.file_ext,
        size_bytes=record.size_bytes,
        status=record.status,
        display_name=record.display_name,
        description=record.description,
        tags=_record_tags(record),
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
        ingest_status=record.ingest_status,
        ingest_error=record.ingest_error,
        ingested_at=record.ingested_at,
        ingested_run_count=record.ingested_run_count or 0,
        ingested_entry_count=record.ingested_entry_count or 0,
        ingested_case_count=len(case_ids),
        ingested_new_case_count=record.ingested_new_case_count or 0,
        ingested_updated_case_count=record.ingested_updated_case_count or 0,
        ingested_new_run_count=record.ingested_new_run_count or 0,
        ingested_updated_run_count=record.ingested_updated_run_count or 0,
        training_complete_count=record.training_complete_count or 0,
        training_incomplete_count=record.training_incomplete_count or 0,
        training_duplicate_count=record.training_duplicate_count or 0,
        training_failed_count=record.training_failed_count or 0,
        training_parse_failed_count=record.training_parse_failed_count or 0,
    )


def _get_active_or_404(db: Session, import_id: str) -> DatasetImport:
    row = (
        db.query(DatasetImport)
        .filter(
            DatasetImport.import_id == import_id,
            DatasetImport.is_deleted.is_(False),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="data import not found")
    return row


# ---------------------------------------------------------------------------
# 后台摄入任务（方案 1：上传完成 → 立即在后台线程解压 + 摄入 → 写状态）
# ---------------------------------------------------------------------------
def _run_ingest_in_background(import_id: str, archive_path: str) -> None:
    """供 BackgroundTasks 调度的同步函数；不能直接拿 Depends，自己开 Session。"""
    db = SessionLocal()
    try:
        row = (
            db.query(DatasetImport)
            .filter(DatasetImport.import_id == import_id)
            .first()
        )
        if row is None:
            logger.warning("[ingest_bg] dataset_import not found: %s", import_id)
            return

        row.ingest_status = "ingesting"
        row.ingest_error = None
        db.commit()

        try:
            result = run_ingest_pipeline(
                archive_path=Path(archive_path),
                extract_root=Path(settings.DATA_IMPORT_EXTRACT_ROOT),
                import_id=import_id,
            )
            row.ingest_status = result.archive_status
            row.ingest_error = None
            row.ingested_at = datetime.utcnow()
            row.ingested_case_ids = result.case_ids
            row.ingested_run_count = result.run_count
            row.ingested_entry_count = result.entry_count
            row.ingested_new_case_count = result.new_case_count
            row.ingested_updated_case_count = result.updated_case_count
            row.ingested_new_run_count = result.new_run_count
            row.ingested_updated_run_count = result.updated_run_count
            row.training_complete_count = result.training_complete_count
            row.training_incomplete_count = result.training_incomplete_count
            row.training_duplicate_count = result.training_duplicate_count
            row.training_failed_count = result.training_failed_count
            row.training_parse_failed_count = result.training_parse_failed_count
            db.commit()

            # 把上传时填的元数据透传到本次摄入产生的 runs.stats_json["dataset_import_meta"]，
            # 让「数据列表」页能直接展示用户填的「显示名称 / 描述 / 标签 / 创建人」。
            try:
                meta = {
                    "import_id": row.import_id,
                    "original_filename": row.original_filename,
                    "display_name": row.display_name,
                    "description": row.description,
                    "tags": _record_tags(row),
                    "created_by": row.created_by,
                }
                runs_to_patch = (
                    db.query(Run)
                    .filter(Run.case_id.in_(result.case_ids))
                    .all()
                )
                for run in runs_to_patch:
                    stats = dict(run.stats_json or {})
                    stats["dataset_import_meta"] = meta
                    run.stats_json = stats
                    flag_modified(run, "stats_json")
                db.commit()
            except Exception:  # pragma: no cover - 仅做装饰，失败不影响主流程
                db.rollback()
                logger.exception("[ingest_bg] %s 元数据透传失败（不影响摄入主流程）", import_id)

            logger.info(
                "[ingest_bg] %s 摄入完成：cases=%d runs=%d entries=%d",
                import_id, len(result.case_ids), result.run_count, result.entry_count,
            )
        except IngestPipelineError as exc:
            row.ingest_status = "failed"
            row.ingest_error = str(exc)
            db.commit()
            logger.warning("[ingest_bg] %s 摄入失败：%s", import_id, exc)
        except Exception as exc:  # pragma: no cover - 兜底
            row.ingest_status = "failed"
            row.ingest_error = f"unexpected error: {exc!r}"
            db.commit()
            logger.exception("[ingest_bg] %s 未预期异常", import_id)
    finally:
        db.close()


@router.post("/data-imports", response_model=DataImportItem)
async def upload_data_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    display_name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    tags: Optional[str] = Form(default=None),
    created_by: Optional[str] = Form(default=None),
    auto_ingest: Optional[bool] = Form(default=None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")

    ext = detect_archive_extension(file.filename)
    if ext is None or ext not in _allowed_extensions():
        raise HTTPException(
            status_code=400,
            detail="unsupported file extension; allowed: zip, tar, tar.gz, tgz",
        )

    try:
        normalized_tags = _normalize_tags(tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duplicate_name = (
        db.query(DatasetImport)
        .filter(
            DatasetImport.original_filename == file.filename,
            DatasetImport.is_deleted.is_(False),
        )
        .first()
    )
    if duplicate_name is not None:
        raise HTTPException(status_code=409, detail=f"已存在同名且未删除的导入「{file.filename}」；如需重新导入请先在数据列表删除旧记录，或改名后再传。")

    root = Path(settings.DATA_IMPORT_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    import_id = uuid.uuid4().hex
    suffix = ".tar.gz" if ext == "tar.gz" else f".{ext}"
    stored_filename = f"{import_id}{suffix}"
    temp_filename = f".{import_id}.uploading"

    final_path = ensure_safe_storage_path(root, stored_filename)
    temp_path = ensure_safe_storage_path(root, temp_filename)
    if final_path.exists():
        raise HTTPException(status_code=409, detail="stored filename already exists")

    try:
        size_bytes = write_upload_to_temp(
            upload_file=file,
            temp_path=temp_path,
            max_size=settings.DATA_IMPORT_MAX_FILE_SIZE,
        )
    except ValueError as exc:
        if str(exc) == "file too large":
            raise HTTPException(status_code=413, detail="file exceeds max upload size") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sha256 = _compute_sha256_from_file(temp_path)
    duplicate_content = (
        db.query(DatasetImport)
        .filter(
            DatasetImport.sha256 == sha256,
            DatasetImport.is_deleted.is_(False),
        )
        .first()
    )
    if duplicate_content is not None:
        remove_file_if_exists(temp_path)
        raise HTTPException(status_code=409, detail=f"该压缩包内容与已导入记录「{duplicate_content.original_filename}」完全相同（重复上传），无需重复导入；如确需覆盖请先删除旧记录。")

    try:
        finalize_atomic_move(temp_path=temp_path, final_path=final_path)
        # 是否自动摄入：请求显式传 auto_ingest 优先，否则用全局开关
        do_ingest = auto_ingest if auto_ingest is not None else settings.DATA_IMPORT_AUTO_INGEST
        row = DatasetImport(
            import_id=import_id,
            original_filename=file.filename,
            stored_filename=stored_filename,
            storage_path=str(final_path),
            file_ext=ext,
            mime_type=file.content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            display_name=display_name,
            description=description,
            tags_json=normalized_tags,
            status="uploaded",
            is_deleted=False,
            created_by=created_by,
            ingest_status="pending" if do_ingest else "skipped",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        # 调度后台自动摄入
        if do_ingest:
            background_tasks.add_task(
                _run_ingest_in_background, import_id, str(final_path)
            )
        return _to_item(row)
    except IntegrityError as exc:
        db.rollback()
        remove_file_if_exists(final_path)
        raise HTTPException(status_code=409, detail="导入元数据冲突（可能重复上传），请刷新数据列表确认后重试。") from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        remove_file_if_exists(final_path)
        raise HTTPException(status_code=500, detail="failed to save uploaded file") from exc


@router.get("/data-imports", response_model=DataImportListOut)
def list_data_imports(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    status: Optional[str] = Query(default=None),
    file_ext: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(DatasetImport).filter(DatasetImport.is_deleted.is_(False))
    if status:
        query = query.filter(DatasetImport.status == status)
    if file_ext:
        query = query.filter(DatasetImport.file_ext == file_ext.lower())
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                DatasetImport.original_filename.like(like),
                DatasetImport.display_name.like(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(DatasetImport.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return DataImportListOut(
        items=[_to_item(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/data-imports/{import_id}", response_model=DataImportItem)
def get_data_import(import_id: str, db: Session = Depends(get_db)):
    row = _get_active_or_404(db, import_id)
    return _to_item(row)


@router.patch("/data-imports/{import_id}", response_model=DataImportItem)
def update_data_import(
    import_id: str,
    body: DataImportUpdateRequest,
    db: Session = Depends(get_db),
):
    row = _get_active_or_404(db, import_id)

    if body.display_name is not None:
        row.display_name = body.display_name
    if body.description is not None:
        row.description = body.description
    if body.tags is not None:
        row.tags_json = [str(tag).strip() for tag in body.tags if str(tag).strip()]

    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="conflict while updating metadata") from exc

    return _to_item(row)


@router.delete("/data-imports/{import_id}", response_model=DataImportDeleteOut)
def delete_data_import(import_id: str, db: Session = Depends(get_db)):
    row = _get_active_or_404(db, import_id)
    file_deleted = remove_file_if_exists(row.storage_path)

    row.is_deleted = True
    row.status = "deleted"
    row.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(row)

    return DataImportDeleteOut(
        import_id=import_id,
        deleted=True,
        file_deleted=file_deleted,
    )


@router.get("/data-imports/{import_id}/download")
def download_data_import(import_id: str, db: Session = Depends(get_db)):
    row = _get_active_or_404(db, import_id)
    storage_path = Path(row.storage_path)
    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="stored file does not exist")
    return FileResponse(
        path=storage_path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.original_filename,
    )


@router.get("/data-imports/{import_id}/preview", response_model=DataImportPreviewOut)
def preview_data_import(import_id: str, db: Session = Depends(get_db)):
    row = _get_active_or_404(db, import_id)
    storage_path = Path(row.storage_path)
    return DataImportPreviewOut(
        import_id=row.import_id,
        original_filename=row.original_filename,
        file_ext=row.file_ext,
        size_bytes=row.size_bytes,
        mime_type=row.mime_type,
        status=row.status,
        tags=_record_tags(row),
        description=row.description,
        file_exists=storage_path.exists(),
        created_at=row.created_at,
        updated_at=row.updated_at,
        ingest_status=row.ingest_status,
        ingest_error=row.ingest_error,
        ingested_at=row.ingested_at,
        ingested_run_count=row.ingested_run_count or 0,
        ingested_entry_count=row.ingested_entry_count or 0,
        ingested_case_ids=_ingested_case_ids(row),
        training_complete_count=row.training_complete_count or 0,
        training_incomplete_count=row.training_incomplete_count or 0,
        training_duplicate_count=row.training_duplicate_count or 0,
        training_failed_count=row.training_failed_count or 0,
        training_parse_failed_count=row.training_parse_failed_count or 0,
    )


@router.post("/data-imports/{import_id}/ingest", response_model=DataImportItem)
def trigger_ingest(
    import_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    手动触发/重新触发摄入。
    - 用于上传时跳过自动摄入、之后改主意
    - 用于上次摄入失败、修复数据后重试
    幂等：ingest_test_dir 内部按 case_id 主键 upsert，重复调用不会产生重复行。
    """
    row = _get_active_or_404(db, import_id)

    storage_path = Path(row.storage_path)
    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="stored archive missing on disk")
    if row.ingest_status == "ingesting":
        raise HTTPException(status_code=409, detail="ingest already in progress")

    row.ingest_status = "pending"
    row.ingest_error = None
    db.commit()
    db.refresh(row)

    background_tasks.add_task(
        _run_ingest_in_background, row.import_id, str(storage_path)
    )
    return _to_item(row)
