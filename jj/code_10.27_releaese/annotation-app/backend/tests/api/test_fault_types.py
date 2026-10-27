from __future__ import annotations

import pytest

from app.api.v1.fault_types import (
    create_fault_type,
    delete_fault_type,
    get_fault_type,
    list_fault_types,
    update_fault_type,
)
from app.core.errors import FaultTypeNameConflictError, FaultTypeNotFoundError
from app.schemas.fault_type import FaultTypeCreateRequest, FaultTypeUpdateRequest


def test_fault_types_api_list_get(db_session, phase2_settings) -> None:
    listed = list_fault_types(db=db_session)
    assert listed.total > 0
    seed_ids = {item.id for item in listed.items}

    created = create_fault_type(
        payload=FaultTypeCreateRequest(name="API测试", description="接口测试"),
        db=db_session,
    )
    assert created.name == "API测试"

    fetched = get_fault_type(fault_type_id=created.id, db=db_session)
    assert fetched.id == created.id
    assert fetched.name == "API测试"

    listed2 = list_fault_types(db=db_session)
    assert listed2.total == listed.total + 1
    assert created.id not in seed_ids


def test_fault_types_api_create_update_delete(db_session, phase2_settings) -> None:
    created = create_fault_type(
        payload=FaultTypeCreateRequest(name="临时", description="d"),
        db=db_session,
    )

    updated = update_fault_type(
        fault_type_id=created.id,
        payload=FaultTypeUpdateRequest(name="临时改", description="dd"),
        db=db_session,
    )
    assert updated.name == "临时改"

    deleted = delete_fault_type(fault_type_id=created.id, db=db_session)
    assert deleted.id == created.id
    assert deleted.name == "临时改"
    assert deleted.referenced_count == 0

    with pytest.raises(FaultTypeNotFoundError):
        get_fault_type(fault_type_id=created.id, db=db_session)


def test_fault_types_api_conflict(db_session, phase2_settings) -> None:
    create_fault_type(
        payload=FaultTypeCreateRequest(name="冲突-A", description="d"),
        db=db_session,
    )
    with pytest.raises(FaultTypeNameConflictError):
        create_fault_type(
            payload=FaultTypeCreateRequest(name="冲突-A", description="dup"),
            db=db_session,
        )
