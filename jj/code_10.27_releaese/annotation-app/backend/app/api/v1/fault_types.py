from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.fault_type import (
    FaultTypeCreateRequest,
    FaultTypeDeleteResponse,
    FaultTypeListResponse,
    FaultTypeResponse,
    FaultTypeUpdateRequest,
)
from app.services.fault_type_service import FaultTypeService

router = APIRouter(tags=["fault-types"])


def _to_response(row) -> FaultTypeResponse:
    return FaultTypeResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/fault-types", response_model=FaultTypeListResponse)
def list_fault_types(db: Session = Depends(get_db)) -> FaultTypeListResponse:
    service = FaultTypeService(db)
    rows = service.list_all()
    return FaultTypeListResponse(items=[_to_response(row) for row in rows], total=len(rows))


@router.post("/fault-types", response_model=FaultTypeResponse, status_code=status.HTTP_201_CREATED)
def create_fault_type(payload: FaultTypeCreateRequest, db: Session = Depends(get_db)) -> FaultTypeResponse:
    service = FaultTypeService(db)
    return _to_response(service.create(payload=payload))


@router.get("/fault-types/{fault_type_id}", response_model=FaultTypeResponse)
def get_fault_type(fault_type_id: int, db: Session = Depends(get_db)) -> FaultTypeResponse:
    service = FaultTypeService(db)
    return _to_response(service.get(fault_type_id=fault_type_id))


@router.patch("/fault-types/{fault_type_id}", response_model=FaultTypeResponse)
def update_fault_type(
    fault_type_id: int,
    payload: FaultTypeUpdateRequest,
    db: Session = Depends(get_db),
) -> FaultTypeResponse:
    service = FaultTypeService(db)
    return _to_response(service.update(fault_type_id=fault_type_id, payload=payload))


@router.delete("/fault-types/{fault_type_id}", response_model=FaultTypeDeleteResponse)
def delete_fault_type(fault_type_id: int, db: Session = Depends(get_db)) -> FaultTypeDeleteResponse:
    service = FaultTypeService(db)
    name, referenced_count = service.delete(fault_type_id=fault_type_id)
    return FaultTypeDeleteResponse(id=fault_type_id, name=name, referenced_count=referenced_count)
