from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import FaultTypeNameConflictError, FaultTypeNotFoundError
from app.db.models import Annotation, FaultType
from app.schemas.fault_type import FaultTypeCreateRequest, FaultTypeUpdateRequest


class FaultTypeService:
    """CRUD for the fault-type dictionary. The list of names is the allowed set
    enforced during annotation and recommendation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[FaultType]:
        stmt = select(FaultType).order_by(FaultType.name.asc(), FaultType.id.asc())
        return list(self.db.scalars(stmt).all())

    def get(self, *, fault_type_id: int) -> FaultType:
        row = self.db.get(FaultType, fault_type_id)
        if row is None:
            raise FaultTypeNotFoundError()
        return row

    def create(self, *, payload: FaultTypeCreateRequest) -> FaultType:
        if self._name_exists(payload.name):
            raise FaultTypeNameConflictError(f"故障类型名称已存在: {payload.name}")
        row = FaultType(name=payload.name, description=payload.description)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, *, fault_type_id: int, payload: FaultTypeUpdateRequest) -> FaultType:
        row = self.get(fault_type_id=fault_type_id)
        if payload.name != row.name and self._name_exists(payload.name):
            raise FaultTypeNameConflictError(f"故障类型名称已存在: {payload.name}")
        row.name = payload.name
        row.description = payload.description
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, *, fault_type_id: int) -> tuple[str, int]:
        """Delete a fault type. Historical annotations keep their string value;
        we only report how many reference it so the caller can warn the user.
        Returns (deleted_name, referenced_count)."""
        row = self.get(fault_type_id=fault_type_id)
        referenced_count = int(
            self.db.scalar(
                select(func.count(Annotation.id)).where(Annotation.anomaly_type == row.name)
            )
            or 0
        )
        name = row.name
        self.db.delete(row)
        self.db.commit()
        return name, referenced_count

    def allowed_names(self) -> set[str]:
        return set(self.db.scalars(select(FaultType.name)).all())

    def _name_exists(self, name: str) -> bool:
        return self.db.scalar(select(FaultType.id).where(FaultType.name == name)) is not None
