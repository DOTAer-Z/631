from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.schemas.fault_type import FaultTypeResponse
from app.schemas.fault_type_suggestion import (
    FaultTypeSuggestionAcceptRequest,
    FaultTypeSuggestionAcceptResponse,
    FaultTypeSuggestionListResponse,
    FaultTypeSuggestionResponse,
)
from app.services.fault_type_suggestion_service import FaultTypeSuggestionService

router = APIRouter(tags=["fault-type-suggestions"])


def _to_response(row) -> FaultTypeSuggestionResponse:
    return FaultTypeSuggestionResponse(
        id=row.id,
        slice_window_id=row.slice_window_id,
        suggested_name=row.suggested_name,
        suggested_description=row.suggested_description,
        reason=row.reason,
        model=row.model,
        status=row.status,
        accepted_fault_type_id=row.accepted_fault_type_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _fault_type_to_response(row) -> FaultTypeResponse:
    return FaultTypeResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/fault-type-suggestions", response_model=FaultTypeSuggestionListResponse)
def list_fault_type_suggestions(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> FaultTypeSuggestionListResponse:
    service = FaultTypeSuggestionService(db)
    rows, total = service.list_(status=status_filter, page=page, page_size=page_size)
    return FaultTypeSuggestionListResponse(
        items=[_to_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/fault-type-suggestions/{suggestion_id}",
    response_model=FaultTypeSuggestionResponse,
)
def get_fault_type_suggestion(suggestion_id: int, db: Session = Depends(get_db)) -> FaultTypeSuggestionResponse:
    return _to_response(FaultTypeSuggestionService(db).get(suggestion_id=suggestion_id))


@router.post(
    "/fault-type-suggestions/{suggestion_id}/accept",
    response_model=FaultTypeSuggestionAcceptResponse,
)
def accept_fault_type_suggestion(
    suggestion_id: int,
    payload: FaultTypeSuggestionAcceptRequest,
    db: Session = Depends(get_db),
) -> FaultTypeSuggestionAcceptResponse:
    service = FaultTypeSuggestionService(db)
    suggestion, fault_type = service.accept(suggestion_id=suggestion_id, payload=payload)
    return FaultTypeSuggestionAcceptResponse(
        suggestion=_to_response(suggestion),
        fault_type=_fault_type_to_response(fault_type),
    )


@router.post(
    "/fault-type-suggestions/{suggestion_id}/reject",
    response_model=FaultTypeSuggestionResponse,
)
def reject_fault_type_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> FaultTypeSuggestionResponse:
    return _to_response(FaultTypeSuggestionService(db).reject(suggestion_id=suggestion_id))


@router.delete(
    "/fault-type-suggestions/{suggestion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_fault_type_suggestion(suggestion_id: int, db: Session = Depends(get_db)) -> None:
    FaultTypeSuggestionService(db).delete(suggestion_id=suggestion_id)
    return None
