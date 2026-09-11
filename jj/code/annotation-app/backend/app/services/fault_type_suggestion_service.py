from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    FaultTypeNameConflictError,
    FaultTypeSuggestionInvalidStateError,
    FaultTypeSuggestionNotFoundError,
)
from app.db.models import FaultType, FaultTypeSuggestion
from app.schemas.fault_type import FaultTypeCreateRequest
from app.schemas.fault_type_suggestion import FaultTypeSuggestionAcceptRequest
from app.services.fault_type_service import FaultTypeService


class FaultTypeSuggestionService:
    """Manages the LLM-proposed fault-type queue. Suggestions only mutate the
    canonical `fault_types` dictionary when a user explicitly accepts them."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ----- queries -----

    def list_(self, *, status: str | None, page: int, page_size: int) -> tuple[list[FaultTypeSuggestion], int]:
        stmt = select(FaultTypeSuggestion)
        count_stmt = select(func.count(FaultTypeSuggestion.id))
        if status:
            stmt = stmt.where(FaultTypeSuggestion.status == status)
            count_stmt = count_stmt.where(FaultTypeSuggestion.status == status)
        rows = list(
            self.db.scalars(
                stmt.order_by(desc(FaultTypeSuggestion.created_at), desc(FaultTypeSuggestion.id))
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        total = int(self.db.scalar(count_stmt) or 0)
        return rows, total

    def get(self, *, suggestion_id: int) -> FaultTypeSuggestion:
        row = self.db.get(FaultTypeSuggestion, suggestion_id)
        if row is None:
            raise FaultTypeSuggestionNotFoundError()
        return row

    # ----- mutations -----

    def record(
        self,
        *,
        slice_window_id: int,
        suggested_name: str,
        suggested_description: str,
        reason: str | None,
        model: str | None,
    ) -> FaultTypeSuggestion:
        """Persist a new suggestion. Idempotent on (slice_window_id, suggested_name)
        for the pending bucket — repeated runs against the same window with the
        same proposal just refresh the existing pending row."""
        existing = self.db.scalar(
            select(FaultTypeSuggestion)
            .where(FaultTypeSuggestion.slice_window_id == slice_window_id)
            .where(FaultTypeSuggestion.suggested_name == suggested_name)
            .where(FaultTypeSuggestion.status == "pending")
        )
        if existing is not None:
            existing.suggested_description = suggested_description
            existing.reason = reason
            existing.model = model
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        row = FaultTypeSuggestion(
            slice_window_id=slice_window_id,
            suggested_name=suggested_name,
            suggested_description=suggested_description,
            reason=reason,
            model=model,
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def accept(
        self,
        *,
        suggestion_id: int,
        payload: FaultTypeSuggestionAcceptRequest,
    ) -> tuple[FaultTypeSuggestion, FaultType]:
        """Promote a pending suggestion to a real fault_type. The user can edit
        name/description before accepting; defaults fall back to the LLM proposal."""
        row = self.get(suggestion_id=suggestion_id)
        if row.status != "pending":
            raise FaultTypeSuggestionInvalidStateError()

        name = (payload.name or row.suggested_name).strip()
        description = (payload.description or row.suggested_description).strip()
        if not name or not description:
            raise FaultTypeNameConflictError("故障类型名称和定义不能为空")

        fault_type = FaultTypeService(self.db).create(
            payload=FaultTypeCreateRequest(name=name, description=description)
        )

        row.status = "accepted"
        row.accepted_fault_type_id = fault_type.id
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row, fault_type

    def reject(self, *, suggestion_id: int) -> FaultTypeSuggestion:
        row = self.get(suggestion_id=suggestion_id)
        if row.status != "pending":
            raise FaultTypeSuggestionInvalidStateError()
        row.status = "rejected"
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, *, suggestion_id: int) -> None:
        row = self.get(suggestion_id=suggestion_id)
        self.db.delete(row)
        self.db.commit()

    def pending_count(self) -> int:
        return int(
            self.db.scalar(
                select(func.count(FaultTypeSuggestion.id)).where(
                    FaultTypeSuggestion.status == "pending"
                )
            )
            or 0
        )

    def find_pending_by_window(self, *, slice_window_id: int) -> FaultTypeSuggestion | None:
        return self.db.scalar(
            select(FaultTypeSuggestion)
            .where(FaultTypeSuggestion.slice_window_id == slice_window_id)
            .where(FaultTypeSuggestion.status == "pending")
            .order_by(desc(FaultTypeSuggestion.created_at), desc(FaultTypeSuggestion.id))
        )
