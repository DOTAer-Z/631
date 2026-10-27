from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import FaultType


# Tests use ``Base.metadata.create_all`` instead of Alembic, so the migration's
# fault-type seed never runs. Seed the canonical names used across test fixtures
# here so abnormal annotations pass the new "must be a defined fault type" rule.
_TEST_FAULT_TYPE_NAMES = (
    "cpu",
    "memory",
    "io",
    "softlockup",
    "disk_jitter",
    "kernel_panic",
    "hot",
    "deadlock",
    "network",
    "scheduler",
    "unknown",
    "内存泄漏",
    "看门狗复位/超时",
)


@pytest.fixture
def phase2_settings(tmp_path: Path) -> Generator[object, None, None]:
    settings = get_settings()
    original_storage_root = settings.storage_root
    original_max_upload = settings.max_upload_bytes
    original_auto_push = getattr(settings, "annotation_auto_push_to_main", None)

    settings.storage_root = tmp_path / "storage"
    settings.max_upload_bytes = 10 * 1024 * 1024
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    # 测试环境关闭「标注保存后自动推送主系统」，避免 pytest 触发对主后端的 HTTP 调用。
    settings.annotation_auto_push_to_main = False

    try:
        yield settings
    finally:
        settings.storage_root = original_storage_root
        settings.max_upload_bytes = original_max_upload
        if original_auto_push is not None:
            settings.annotation_auto_push_to_main = original_auto_push


@pytest.fixture
def db_session(phase2_settings: object) -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    for name in _TEST_FAULT_TYPE_NAMES:
        session.add(FaultType(name=name, description=f"test fault type: {name}"))
    session.commit()

    try:
        yield session
    finally:
        session.close()
