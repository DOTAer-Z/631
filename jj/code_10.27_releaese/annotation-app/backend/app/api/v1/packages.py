from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.v1.deps import get_db
from app.core.config import get_settings
from app.schemas.package import (
    PackageListResponse,
    PackageResponse,
    PackageUpdateRequest,
    PackageUploadAcceptedResponse,
)
from app.services.archive_service import validate_archive
from app.services.import_service import ImportService
from app.services.main_system_client import MainSystemClient
from app.services.package_service import PackageService
from app.services.storage_service import StorageService


class FromDataImportRequest(BaseModel):
    """从主系统「数据导入」拉取归档并导入为数据包。

    filename 用于确定扩展名并复用归档校验；未传时按 content 探测。
    """
    import_id: str | int
    filename: str | None = None
    name: str | None = None
    description: str | None = None

router = APIRouter(prefix="/packages", tags=["packages"])


def _filename_hint_from_main(client: "MainSystemClient", import_id: str | int) -> str | None:
    """从主系统拉该数据导入的 original_filename，用作文件名/扩展名 hint。

    主系统单条接口 GET /data-imports/{import_id} 返回 original_filename（含扩展名，
    如 test_data.zip）。拉不到时返回 None，由调用方兜底。
    """
    try:
        item = client.get_data_import(import_id)
    except Exception:  # noqa: BLE001 — hint 拉不到不阻断主流程
        return None
    original = (item or {}).get("original_filename") or ""
    return original.strip() or None


def _to_response(pkg, *, has_abnormal: bool = False) -> PackageResponse:
    slice_task_count = len(getattr(pkg, "slice_tasks", []) or [])
    return PackageResponse(
        id=pkg.id,
        name=pkg.name,
        archive_type=pkg.archive_type,
        stored_path=pkg.stored_path,
        file_size=pkg.file_size,
        sha256=pkg.sha256,
        description=pkg.description,
        import_status=pkg.import_status,
        import_error_message=pkg.import_error_message,
        data_kind=getattr(pkg, "data_kind", "semi_structured") or "semi_structured",
        source_file_count=pkg.source_file_count,
        source_line_count=pkg.source_line_count,
        cpu_count=pkg.cpu_count,
        module_count=pkg.module_count,
        earliest_timestamp=pkg.earliest_timestamp,
        latest_timestamp=pkg.latest_timestamp,
        created_at=pkg.created_at,
        updated_at=pkg.updated_at,
        slice_task_count=slice_task_count,
        has_abnormal=has_abnormal,
    )


def _name_from_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".tar.gz"):
        base = filename[: -len(".tar.gz")]
        return base or "dataset"
    stem = Path(filename).stem
    return stem or "dataset"


@router.post("", response_model=PackageUploadAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_package(
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> PackageUploadAcceptedResponse:
    filename = file.filename or "dataset.zip"
    file_bytes = await file.read()
    storage = StorageService()
    storage.ensure_size_limit(len(file_bytes))
    validated = validate_archive(filename, file.content_type, file_bytes)
    package_name = name.strip() if name else ""
    if not package_name:
        package_name = _name_from_filename(filename)

    stored = storage.save_package_bytes(file_bytes, validated.extension)

    service = ImportService(db)
    try:
        pkg, import_task = service.create_upload(
            name=package_name,
            archive_type=validated.archive_type,
            stored_path=stored.stored_path,
            file_size=stored.file_size,
            sha256=stored.sha256,
            description=description.strip() if description else None,
        )
    except Exception:
        storage.delete_file_if_exists(stored.stored_path)
        raise

    service.start_import_task_async(task_id=import_task.id, bind=db.get_bind())

    return PackageUploadAcceptedResponse(
        **_to_response(pkg).model_dump(),
        import_task_id=import_task.id,
    )


@router.post(
    "/folder",
    response_model=PackageUploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_package_from_folder(
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    paths: list[str] = Form(default=[]),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> PackageUploadAcceptedResponse:
    """上传整个文件夹（多文件 + 各自相对路径），服务端打包为 .tar.gz 后复用导入流水线。"""
    if not files:
        raise HTTPException(status_code=400, detail="未选择任何文件")
    if paths and len(paths) != len(files):
        raise HTTPException(status_code=400, detail="files 与 paths 数量不一致")

    rel_paths = paths if paths else [f.filename or "" for f in files]
    storage = StorageService()
    stored, inferred_name = await storage.save_package_from_uploads(files, rel_paths)

    package_name = (name.strip() if name else "") or inferred_name or "dataset"

    service = ImportService(db)
    try:
        pkg, import_task = service.create_upload(
            name=package_name,
            archive_type="tar.gz",
            stored_path=stored.stored_path,
            file_size=stored.file_size,
            sha256=stored.sha256,
            description=description.strip() if description else None,
        )
    except Exception:
        storage.delete_file_if_exists(stored.stored_path)
        raise

    service.start_import_task_async(task_id=import_task.id, bind=db.get_bind())

    return PackageUploadAcceptedResponse(
        **_to_response(pkg).model_dump(),
        import_task_id=import_task.id,
    )


@router.get("/main-system-data-imports")
def list_main_system_data_imports(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    """透传主系统（DB1）的「数据导入」列表，供标注侧选择要导入的压缩包。"""
    try:
        return MainSystemClient(get_settings()).list_data_imports(
            page=page, page_size=page_size, keyword=keyword, status=status
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"无法从主系统获取数据导入列表：{exc}")


@router.post(
    "/from-data-import",
    response_model=PackageUploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_package_from_data_import(
    body: FromDataImportRequest,
    db: Session = Depends(get_db),
) -> PackageUploadAcceptedResponse:
    """从主系统某个「数据导入」下载原始归档，导入为标注侧数据包（返回 202）。

    下载 → 校验归档（扩展名 + 内容）→ 落盘 packages_dir → 建包（provenance 标记
    source=main_system / external_run_id=主系统数据导入 id）→ 复用既有导入流水线。
    """
    import_id = body.import_id
    if import_id in (None, ""):
        raise HTTPException(status_code=400, detail="import_id 不能为空")

    client = MainSystemClient(get_settings())
    try:
        file_bytes = client.download_data_import(import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"无法从主系统下载数据导入：{exc}")

    storage = StorageService()
    storage.ensure_size_limit(len(file_bytes))  # FileTooLargeError → 413（全局 handler）

    # 文件名 hint 优先级：前端显式传 > 主系统 original_filename > 兜底串。
    # 直接拼 data_import_{id} 会因无扩展名触发 INVALID_ARCHIVE_TYPE，故先尝试带扩展名的 hint。
    filename = (body.filename or "").strip() or _filename_hint_from_main(client, import_id) or f"data_import_{import_id}"
    validated = validate_archive(filename, None, file_bytes)  # InvalidArchiveTypeError → 400

    stored = storage.save_package_bytes(file_bytes, validated.extension)

    package_name = (body.name or "").strip() or _name_from_filename(filename) or f"数据导入-{import_id}"

    service = ImportService(db)
    try:
        pkg, import_task = service.create_from_data_import(
            import_id=import_id,
            name=package_name,
            stored=stored,
            archive_type=validated.archive_type,
            description=body.description.strip() if body.description else None,
        )
    except Exception:
        storage.delete_file_if_exists(stored.stored_path)
        raise

    service.start_import_task_async(task_id=import_task.id, bind=db.get_bind())

    return PackageUploadAcceptedResponse(
        **_to_response(pkg).model_dump(),
        import_task_id=import_task.id,
    )


@router.post("/from-data-import/ingest", response_model=dict)
def ingest_data_import_to_knowledge_base(
    body: FromDataImportRequest,
) -> dict:
    """「从主系统导入 → 入知识库」：不建标注包，直接触发主系统侧摄入。

    主系统已有 POST /data-imports/{import_id}/ingest（后台异步跑 run_ingest_pipeline，
    写 runs/cases / RAG，幂等 upsert 不产生重复行）。这里只是转发触发，并把主系统
    返回的 DataImportItem（含 ingest_status / ingested_run_count 等）回传给前端，
    供其展示知识库入库进度与真实计数。
    """
    import_id = body.import_id
    if import_id in (None, ""):
        raise HTTPException(status_code=400, detail="import_id 不能为空")

    client = MainSystemClient(get_settings())
    try:
        return client.trigger_data_import_ingest(import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"无法从主系统触发知识库入库：{exc}")


@router.get("", response_model=PackageListResponse)
def list_packages(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    query: str | None = Query(default=None),
    sort_by: str = Query(default="created_at", pattern="^(created_at|name)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> PackageListResponse:
    service = PackageService(db)
    items, total = service.list(page=page, page_size=page_size, query=query, sort_by=sort_by, sort_order=sort_order)
    abnormal_ids = service.abnormal_package_ids([item.id for item in items])
    return PackageListResponse(
        items=[_to_response(item, has_abnormal=item.id in abnormal_ids) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{package_id}", response_model=PackageResponse)
def get_package(package_id: int, db: Session = Depends(get_db)) -> PackageResponse:
    service = PackageService(db)
    pkg = service.get_by_id(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    abnormal_ids = service.abnormal_package_ids([pkg.id])
    return _to_response(pkg, has_abnormal=pkg.id in abnormal_ids)


@router.patch("/{package_id}", response_model=PackageResponse)
def update_package(
    package_id: int,
    payload: PackageUpdateRequest,
    db: Session = Depends(get_db),
) -> PackageResponse:
    service = PackageService(db)
    pkg = service.update_description(package_id, payload)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return _to_response(pkg)


@router.delete("/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_package(
    package_id: int,
    package_name_confirm: str,
    db: Session = Depends(get_db),
) -> None:
    service = PackageService(db)
    pkg = service.get_by_id(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="Package not found")

    storage = StorageService()
    stored_path = pkg.stored_path
    service.delete(package_id, package_name_confirm=package_name_confirm)
    storage.delete_file_if_exists(stored_path)
    return None
