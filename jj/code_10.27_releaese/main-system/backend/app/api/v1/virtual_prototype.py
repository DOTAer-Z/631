from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EulerArtifactImport
from app.services import euler_artifact_import_service as artifact_imports
from app.services import euler_analysis_service as analysis

from app.schemas.virtual_prototype import (
    VirtualPrototypeJobCreate,
    VirtualPrototypeOptionsOut,
    VirtualPrototypeReplayRequest,
    VirtualPrototypeSampleReproduceRequest,
)
from app.services.euler_fault_agent_client import EulerFaultAgentClient, EulerFaultAgentError


router = APIRouter(prefix="/virtual-prototype")
_JOB_ID = re.compile(r"^job-[A-Za-z0-9]+$")


def _client() -> EulerFaultAgentClient:
    return EulerFaultAgentClient()


def _raise_agent_error(exc: EulerFaultAgentError) -> None:
    code = exc.status_code if exc.status_code in {400, 404, 409, 502, 503} else 502
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _validate_job_id(job_id: str) -> str:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="invalid Euler job id")
    return job_id


@router.get("/health")
def health():
    try:
        return _client().health()
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/capabilities", response_model=VirtualPrototypeOptionsOut)
def capabilities():
    try:
        return _client().capabilities()
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    try:
        return _client().list_jobs(limit=limit, offset=offset)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/artifact-imports")
def list_artifact_imports(db: Session = Depends(get_db)):
    return artifact_imports.list_imports(db, _client().base_url)


@router.get("/analysis")
def analysis_status(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    db: Session = Depends(get_db)):
    return analysis.snapshot(db, page=page, page_size=page_size)


@router.post("/analysis", status_code=status.HTTP_202_ACCEPTED)
def start_analysis(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        batch_id, scheduled = analysis.request_batch(db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="无法创建分析任务，请检查数据库及模型 API 配置") from None
    if scheduled:
        background_tasks.add_task(analysis.run_batch, batch_id)
    return {"batch_id": batch_id, "scheduled": scheduled}


@router.get("/jobs/{job_id}/database-import")
def get_artifact_import(job_id: str, db: Session = Depends(get_db)):
    _validate_job_id(job_id)
    row = db.get(EulerArtifactImport, artifact_imports.import_identity(_client().base_url, job_id))
    return artifact_imports.shape_import(row) if row else {"job_id": job_id, "state": "not_saved"}


@router.post("/jobs/{job_id}/database-import", status_code=status.HTTP_202_ACCEPTED)
def save_artifact_to_database(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _validate_job_id(job_id)
    try:
        result, scheduled = artifact_imports.request_import(db, _client(), job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if scheduled:
        background_tasks.add_task(artifact_imports.run_import, result["import_id"])
    return result


@router.get("/samples")
def samples():
    try:
        return _client().samples()
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/samples/{sample_id}/preview")
def sample_preview(sample_id: str):
    if not re.fullmatch(r"sample-[a-z0-9-]+", sample_id):
        raise HTTPException(status_code=400, detail="invalid sample id")
    try:
        return _client().sample_preview(sample_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.post("/samples/{sample_id}/reproduce", status_code=status.HTTP_202_ACCEPTED)
def reproduce_sample(sample_id: str, payload: VirtualPrototypeSampleReproduceRequest):
    if not re.fullmatch(r"sample-[a-z0-9-]+", sample_id):
        raise HTTPException(status_code=400, detail="invalid sample id")
    body = payload.model_dump(exclude_none=True)
    if payload.reproduction_mode == "normal":
        body.setdefault("evidence_mode", "simulated")
    try:
        return _client().reproduce_sample(sample_id, body)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_job(payload: VirtualPrototypeJobCreate):
    body = payload.model_dump(exclude_none=True)
    # sample_class is a UI grouping field; the Agent accepts only its
    # restricted fault-job contract and does not receive arbitrary labels.
    body.pop("sample_class", None)
    body["execution_mode"] = "baseline"
    try:
        return _client().create_job(body)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    _validate_job_id(job_id)
    try:
        return _client().get_job(job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/jobs/{job_id}/events")
def get_events(job_id: str):
    _validate_job_id(job_id)
    try:
        return _client().get_events(job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/jobs/{job_id}/checkpoints")
def get_checkpoints(job_id: str):
    _validate_job_id(job_id)
    try:
        return _client().get_checkpoints(job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.post("/jobs/{job_id}/replay", status_code=status.HTTP_202_ACCEPTED)
def replay_job(job_id: str, payload: VirtualPrototypeReplayRequest):
    _validate_job_id(job_id)
    try:
        return _client().replay(job_id, payload.model_dump(exclude_none=True))
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(job_id: str):
    _validate_job_id(job_id)
    try:
        return _client().cancel(job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)


@router.get("/jobs/{job_id}/artifact")
def download_artifact(job_id: str):
    _validate_job_id(job_id)
    try:
        upstream = _client().artifact(job_id)
    except EulerFaultAgentError as exc:
        _raise_agent_error(exc)
    return Response(
        content=upstream.content,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.tar.gz"'},
    )
