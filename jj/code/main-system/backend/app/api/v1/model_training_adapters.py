from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.models.training_task import TrainingArtifact, TrainingEvaluation, TrainingTask
from app.schemas.model_training import (
    AdapterImportOut,
    TrainingAdapterListOut,
    TrainingAdapterOut,
    TrainingTaskOut,
)
from app.services.adapter_import_service import (
    AdapterImportError,
    SUPPORTED_BASE_MODEL_ID,
    import_adapter,
)
from app.services.training_task_service import (
    TrainingTaskConflict,
    TrainingTaskNotFound,
    TrainingTaskValidationError,
    classify_uploaded_sft_adapter,
    create_external_adapter_evaluation,
)
from app.api.v1.model_training import _task_header_out


router = APIRouter(prefix="/model-training/adapters")

_ARCHIVE_TOO_LARGE_CODES = {
    "archive_too_large",
    "expanded_archive_too_large",
    "metadata_file_too_large",
}
_UNSUPPORTED_ARCHIVE_CODES = {
    "unsupported_archive_format",
    "unsupported_adapter_file",
}
_INCOMPATIBLE_ARCHIVE_CODES = {
    "unsupported_adapter_stage",
    "unsupported_base_model",
    "invalid_adapter_config",
    "invalid_json_metadata",
    "invalid_safetensors",
}
_INVALID_ARCHIVE_CODES = {
    "invalid_adapter_request",
    "invalid_archive",
    "invalid_archive_layout",
    "unsafe_archive_entry",
    "duplicate_archive_entry",
    "case_conflicting_archive_entry",
    "required_adapter_file_missing",
    "too_many_archive_files",
    "too_many_archive_members",
}
_CONFLICT_CODES = {
    "normalized_output_exists",
    "adapter_import_commit_uncertain",
}


@dataclass(frozen=True)
class _AdapterImportRequest:
    name: str
    adapter_stage: str
    base_model_id: str
    description: str | None


class _AdapterEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    test_version_ids: list[StrictInt]


@router.post("/import", response_model=AdapterImportOut, status_code=201)
async def import_training_adapter(
    response: Response,
    file: UploadFile = File(...),
    name: str = Form(...),
    adapter_stage: str = Form(...),
    base_model_id: str = Form(...),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> AdapterImportOut:
    request = _validated_import_request(
        name=name,
        adapter_stage=adapter_stage,
        base_model_id=base_model_id,
        description=description,
    )
    try:
        result = await import_adapter(
            db,
            file,
            request,
            settings.TRAINING_OUTPUT_ROOT,
        )
    except AdapterImportError as error:
        raise _public_import_error(error.code) from None
    except Exception:
        raise _public_import_error("adapter_import_failed") from None

    response.status_code = 200 if result.deduplicated else 201
    return AdapterImportOut(
        adapter=_uploaded_adapter_out(db, result.artifact),
        deduplicated=result.deduplicated,
    )


@router.get("", response_model=TrainingAdapterListOut)
def list_training_adapters(
    search: str | None = Query(default=None, max_length=256),
    adapter_stage: Literal["cpt", "sft"] | None = None,
    source: Literal["training", "uploaded"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TrainingAdapterListOut:
    trained, uploaded, name_value, stage_value, source_value, model_value = (
        _adapter_query_values()
    )


    query = (
        db.query(
            TrainingArtifact,
            name_value.label("adapter_name"),
            stage_value.label("adapter_stage"),
            source_value.label("adapter_source"),
            model_value.label("base_model_id"),
        )
        .outerjoin(TrainingTask, TrainingTask.id == TrainingArtifact.task_id)
        .filter(
            TrainingArtifact.artifact_type == "final_adapter",
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
            TrainingArtifact.size_bytes.is_not(None),
            TrainingArtifact.size_bytes >= 0,
            TrainingArtifact.sha256.is_not(None),
            func.length(TrainingArtifact.sha256) == 64,
            or_(trained, uploaded),
        )
    )
    if source == "training":
        query = query.filter(trained)
    elif source == "uploaded":
        query = query.filter(uploaded)
    if adapter_stage is not None:
        query = query.filter(stage_value == adapter_stage)
    if search and search.strip():
        query = query.filter(
            func.lower(name_value).like(
                _literal_like_pattern(search.strip().lower()),
                escape="\\",
            )
        )

    total = query.order_by(None).count()
    rows = (
        query.order_by(TrainingArtifact.created_at.desc(), TrainingArtifact.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    artifact_ids = [artifact.id for artifact, *_values in rows]
    referenced = _referenced_adapter_ids(db, artifact_ids)
    return TrainingAdapterListOut(
        items=[
            _adapter_out(
                artifact,
                name=adapter_name,
                adapter_stage=row_stage,
                source=row_source,
                base_model_id=base_model_id,
                deletable=artifact.id not in referenced,
            )
            for artifact, adapter_name, row_stage, row_source, base_model_id in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{artifact_id}/evaluate", response_model=TrainingTaskOut)
def evaluate_uploaded_sft_adapter(
    artifact_id: str,
    payload: _AdapterEvaluationRequest,
    db: Session = Depends(get_db),
) -> TrainingTaskOut:
    try:
        task = create_external_adapter_evaluation(
            db,
            artifact_id,
            payload.name,
            list(payload.test_version_ids),
        )
        db.commit()
        return _task_header_out(db, task.id)
    except TrainingTaskNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TrainingTaskConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TrainingTaskValidationError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="unable to create adapter evaluation",
        ) from exc


def _validated_import_request(
    *,
    name: str,
    adapter_stage: str,
    base_model_id: str,
    description: str | None,
) -> _AdapterImportRequest:
    trimmed_name = name.strip() if isinstance(name, str) else ""
    if (
        not trimmed_name
        or len(trimmed_name) > 255
        or adapter_stage not in {"cpt", "sft"}
        or base_model_id != SUPPORTED_BASE_MODEL_ID
        or (
            description is not None
            and (not isinstance(description, str) or len(description) > 1000)
        )
    ):
        raise _public_import_error("invalid_adapter_request")
    return _AdapterImportRequest(
        name=trimmed_name,
        adapter_stage=adapter_stage,
        base_model_id=base_model_id,
        description=description,
    )


def _public_import_error(code: str) -> HTTPException:
    if code in _ARCHIVE_TOO_LARGE_CODES:
        status_code, public_code, message = (
            413,
            "adapter_archive_too_large",
            "Adapter archive exceeds the allowed size.",
        )
    elif code in _UNSUPPORTED_ARCHIVE_CODES:
        status_code, public_code, message = (
            415,
            "unsupported_adapter_archive",
            "Adapter archive format is not supported.",
        )
    elif code in _INCOMPATIBLE_ARCHIVE_CODES:
        status_code, public_code, message = (
            422,
            "incompatible_adapter_archive",
            "Adapter archive is not compatible with the selected model and stage.",
        )
    elif code in _INVALID_ARCHIVE_CODES:
        status_code, public_code, message = (
            400,
            "invalid_adapter_request",
            "Adapter request or archive is invalid.",
        )
    elif code in _CONFLICT_CODES:
        status_code = 409
        public_code = (
            "adapter_import_commit_uncertain"
            if code == "adapter_import_commit_uncertain"
            else "adapter_import_conflict"
        )
        message = "Adapter import state is unresolved. Refresh the Adapter list before retrying."
    else:
        status_code, public_code, message = (
            500,
            "adapter_import_failed",
            "Unable to import Adapter.",
        )
    return HTTPException(
        status_code=status_code,
        detail={"code": public_code, "message": message},
    )


def _adapter_query_values():
    metadata = TrainingArtifact.metadata_json
    trained = and_(
        TrainingArtifact.task_id.is_not(None),
        TrainingTask.id.is_not(None),
        TrainingTask.deleted_at.is_(None),
        TrainingTask.state == "succeeded",
        TrainingTask.job_kind == "training",
        TrainingTask.task_type.in_(("cpt", "sft")),
    )
    uploaded_name = metadata["display_name"].as_string()
    uploaded_stage = metadata["adapter_stage"].as_string()
    uploaded_model = metadata["base_model_id"].as_string()
    uploaded = and_(
        TrainingArtifact.task_id.is_(None),
        metadata["source"].as_string() == "uploaded",
        uploaded_stage.in_(("cpt", "sft")),
        uploaded_model == SUPPORTED_BASE_MODEL_ID,
        func.length(func.trim(uploaded_name)).between(1, 255),
    )
    return (
        trained,
        uploaded,
        case((trained, TrainingTask.name), else_=uploaded_name),
        case((trained, TrainingTask.task_type), else_=uploaded_stage),
        case((trained, "training"), else_="uploaded"),
        case((trained, TrainingTask.model_id), else_=uploaded_model),
    )


def _literal_like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _referenced_adapter_ids(db: Session, artifact_ids: list[str]) -> set[str]:
    if not artifact_ids:
        return set()
    task_references = db.query(
        TrainingTask.cpt_adapter_artifact_id.label("artifact_id")
    ).filter(
        TrainingTask.cpt_adapter_artifact_id.in_(artifact_ids),
        TrainingTask.task_type == "sft",
    )
    evaluation_references = db.query(
        TrainingEvaluation.source_sft_artifact_id.label("artifact_id")
    ).filter(
        TrainingEvaluation.source_sft_artifact_id.in_(artifact_ids)
    )
    task_backed_evaluation_references = (
        db.query(TrainingArtifact.id.label("artifact_id"))
        .join(
            TrainingEvaluation,
            TrainingEvaluation.source_sft_task_id == TrainingArtifact.task_id,
        )
        .filter(
            TrainingArtifact.id.in_(artifact_ids),
            TrainingArtifact.artifact_type == "final_adapter",
        )
    )
    return {
        artifact_id
        for artifact_id, in task_references.union(
            evaluation_references,
            task_backed_evaluation_references,
        ).all()
        if artifact_id is not None
    }


def _uploaded_adapter_out(db: Session, artifact: TrainingArtifact) -> TrainingAdapterOut:
    metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
    return _adapter_out(
        artifact,
        name=metadata.get("display_name"),
        adapter_stage=metadata.get("adapter_stage"),
        source="uploaded",
        base_model_id=metadata.get("base_model_id"),
        deletable=artifact.id not in _referenced_adapter_ids(db, [artifact.id]),
    )


def _adapter_out(
    artifact: TrainingArtifact,
    *,
    name: str,
    adapter_stage: Literal["cpt", "sft"],
    source: Literal["training", "uploaded"],
    base_model_id: str,
    deletable: bool,
) -> TrainingAdapterOut:
    evaluable = adapter_stage == "sft"
    if evaluable and source == "uploaded":
        try:
            classify_uploaded_sft_adapter(artifact, base_model_id)
        except (TrainingTaskConflict, TrainingTaskNotFound):
            evaluable = False
    return TrainingAdapterOut(
        id=artifact.id,
        name=name,
        adapter_stage=adapter_stage,
        source=source,
        base_model_id=base_model_id,
        size_bytes=int(artifact.size_bytes),
        sha256=artifact.sha256,
        deletable=deletable,
        usable_for_sft=adapter_stage == "cpt",
        evaluable=evaluable,
    )
