from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.training_data import (
    TrainingImportItem,
    TrainingTest,
    TrainingTestLog,
    TrainingTestVersion,
)


REQUIRED_LABEL_PATHS = ("ground_truth.json", "fip_info.data")
REQUIRED_LOG_PATHS = {
    (1, "qemu_console"): "logs/round_1/nuttx/qemu_console.log",
    (1, "fault_events"): "logs/round_1/faults/fault_events.log",
    (1, "system_metrics"): "logs/round_1/monitor/system_metrics.log",
    (2, "qemu_console"): "logs/round_2/nuttx/qemu_console.log",
    (2, "fault_events"): "logs/round_2/faults/fault_events.log",
    (2, "system_metrics"): "logs/round_2/monitor/system_metrics.log",
}


class TestDataValidationError(ValueError):
    """A Test directory contains malformed required training data."""


@dataclass(frozen=True)
class TestValidationResult:
    test_dir: Path
    test_name: str
    ground_truth: dict[str, Any]
    fip_info: dict[str, str]
    available_files: dict[str, Path]
    available_logs: dict[tuple[int, str], Path]
    missing_files: list[str]

    @property
    def completeness(self) -> str:
        return "complete" if not self.missing_files else "incomplete"


@dataclass(frozen=True)
class ImportedTestVersion:
    status: str
    version_id: int
    version_number: int
    completeness: str


def validate_test_dir(path: Path) -> TestValidationResult:
    """Read one Test directory without considering unneeded workload files."""
    test_dir = Path(path)
    if not test_dir.is_dir():
        raise TestDataValidationError(f"Test directory does not exist: {test_dir}")

    missing_files: list[str] = []
    available_files: dict[str, Path] = {}

    ground_truth: dict[str, Any] = {}
    fip_info: dict[str, str] = {}
    for relative_path in REQUIRED_LABEL_PATHS:
        label_path = test_dir / relative_path
        if not label_path.is_file():
            missing_files.append(relative_path)
            continue

        if relative_path == "ground_truth.json":
            ground_truth = _read_ground_truth(label_path)
        elif relative_path == "fip_info.data":
            fip_info = _read_fip_info(label_path)
        available_files[relative_path] = label_path

    available_logs: dict[tuple[int, str], Path] = {}
    for key, relative_path in REQUIRED_LOG_PATHS.items():
        log_path = test_dir / relative_path
        if log_path.is_file():
            available_logs[key] = log_path
            available_files[relative_path] = log_path
        else:
            missing_files.append(relative_path)

    return TestValidationResult(
        test_dir=test_dir,
        test_name=test_dir.name,
        ground_truth=ground_truth,
        fip_info=fip_info,
        available_files=available_files,
        available_logs=available_logs,
        missing_files=sorted(missing_files),
    )


def compute_test_sha256(validation: TestValidationResult) -> str:
    """Hash only the immutable label and required-log training inputs."""
    digest = hashlib.sha256()
    for relative_path in sorted(validation.available_files):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(validation.available_files[relative_path].read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def import_test_version(
    db: Session,
    *,
    test_dir: Path,
    import_id: str,
    platform: str,
) -> ImportedTestVersion:
    """Persist one immutable Test version in a caller-owned transaction."""
    test_name = Path(test_dir).name
    try:
        validation = validate_test_dir(test_dir)
        content_sha256 = compute_test_sha256(validation)
    except (TestDataValidationError, OSError, UnicodeDecodeError) as exc:
        _record_import_item(
            db,
            import_id=import_id,
            platform=platform,
            test_name=test_name,
            status="failed",
            error_message=str(exc),
        )
        raise

    try:
        with db.begin_nested():
            _acquire_test_advisory_lock(db, platform, validation.test_name)
            training_test = (
                db.query(TrainingTest)
                .filter_by(platform=platform, test_name=validation.test_name)
                .with_for_update()
                .one_or_none()
            )
            if training_test is None:
                training_test = TrainingTest(platform=platform, test_name=validation.test_name)
                db.add(training_test)
                db.flush()
                training_test = (
                    db.query(TrainingTest)
                    .filter_by(id=training_test.id)
                    .with_for_update()
                    .one()
                )

            existing_version = (
                db.query(TrainingTestVersion)
                .filter_by(test_id=training_test.id, content_sha256=content_sha256)
                .one_or_none()
            )
            if existing_version is not None:
                _record_import_item(
                    db,
                    import_id=import_id,
                    platform=platform,
                    test_name=validation.test_name,
                    status="duplicate",
                    test_version_id=existing_version.id,
                )
                return ImportedTestVersion(
                    status="duplicate",
                    version_id=existing_version.id,
                    version_number=existing_version.version_number,
                    completeness=existing_version.completeness,
                )

            next_version_number = (
                db.query(func.max(TrainingTestVersion.version_number))
                .filter_by(test_id=training_test.id)
                .scalar()
                or 0
            ) + 1
            version = TrainingTestVersion(
                test_id=training_test.id,
                version_number=next_version_number,
                content_sha256=content_sha256,
                ground_truth=validation.ground_truth,
                fip_info=validation.fip_info,
                completeness=validation.completeness,
                missing_files=validation.missing_files or None,
                import_id=import_id,
                sample_class=_optional_text(validation.ground_truth.get("sample_class")),
                domain=_optional_text(validation.ground_truth.get("domain")),
                fault_type=_optional_text(
                    validation.ground_truth.get("fault_type")
                    or validation.fip_info.get("FAULT_TYPE")
                ),
            )
            db.add(version)
            db.flush()

            for (round_no, log_type), log_path in validation.available_logs.items():
                raw_log = log_path.read_bytes()
                db.add(
                    TrainingTestLog(
                        test_version_id=version.id,
                        round_no=round_no,
                        log_type=log_type,
                        content=raw_log.decode("utf-8"),
                        byte_count=len(raw_log),
                    )
                )

            status = "imported" if validation.completeness == "complete" else "incomplete"
            _record_import_item(
                db,
                import_id=import_id,
                platform=platform,
                test_name=validation.test_name,
                status=status,
                test_version_id=version.id,
            )
            db.flush()
            training_test.latest_version_id = version.id
            db.flush()
            return ImportedTestVersion(
                status=status,
                version_id=version.id,
                version_number=version.version_number,
                completeness=version.completeness,
            )
    except (SQLAlchemyError, OSError, UnicodeDecodeError):
        _record_import_item(
            db,
            import_id=import_id,
            platform=platform,
            test_name=validation.test_name,
            status="failed",
            error_message="Unable to persist Test version",
        )
        raise


def _read_ground_truth(path: Path) -> dict[str, Any]:
    try:
        parsed_ground_truth = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TestDataValidationError(f"Invalid ground_truth.json: {exc}") from exc
    if not isinstance(parsed_ground_truth, dict):
        raise TestDataValidationError("Invalid ground_truth.json: expected a JSON object")
    return parsed_ground_truth


def _read_fip_info(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise TestDataValidationError(f"Invalid fip_info.data: {exc}") from exc

    fip_info: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        if separator:
            fip_info[key.strip()] = value.strip()
    return fip_info


def _acquire_test_advisory_lock(db: Session, platform: str, test_name: str) -> None:
    if _is_postgresql(db):
        db.execute(select(func.pg_advisory_xact_lock(_advisory_lock_key(platform, test_name))))


def _is_postgresql(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _advisory_lock_key(platform: str, test_name: str) -> int:
    identity = f"{platform}\0{test_name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(identity).digest()[:8], byteorder="big", signed=True)


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _record_import_item(
    db: Session,
    *,
    import_id: str,
    platform: str,
    test_name: str,
    status: str,
    test_version_id: int | None = None,
    error_message: str | None = None,
) -> None:
    item = (
        db.query(TrainingImportItem)
        .filter_by(import_id=import_id, platform=platform, test_name=test_name)
        .one_or_none()
    )
    if item is None:
        item = TrainingImportItem(
            import_id=import_id,
            platform=platform,
            test_name=test_name,
            status=status,
            test_version_id=test_version_id,
            error_message=error_message,
        )
        db.add(item)
    else:
        item.status = status
        item.test_version_id = test_version_id
        item.error_message = error_message
    db.flush()
