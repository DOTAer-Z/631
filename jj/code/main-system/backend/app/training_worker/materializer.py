from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from hashlib import sha256
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux workers always provide fcntl.
    fcntl = None

from embedded_fault_diag.build_cpt import build_cpt_rows
from embedded_fault_diag.build_sft_open import build_sft_open_rows
from embedded_fault_diag.data import TestSample
from embedded_fault_diag.fault_registry import fault_type_id

from app.models.training_data import TrainingTest, TrainingTestLog, TrainingTestVersion
from app.models.training_task import TrainingTask, TrainingTaskTest
from app.services.training_config_service import (
    ALLOWED_OVERRIDES,
    TrainingConfigError,
    build_training_config,
    normalized_config_snapshot,
)


MAX_LOG_CHARS = 20000
SPLITS = ("train", "validation", "test")
REQUIRED_LOG_KEYS = (
    (1, "qemu_console"),
    (1, "fault_events"),
    (1, "system_metrics"),
    (2, "qemu_console"),
    (2, "fault_events"),
    (2, "system_metrics"),
)
_PREPARING_PREFIX = ".preparing-"
_PREVIOUS_DATA_PREFIX = ".previous-"
_LEGACY_PREPARING_DIR = ".preparing"
_DATA_DIR = "data"
_LOCK_FILE = ".materialize.lock"
_MANIFEST_FILE = ".materialization-manifest.json"
_PAYLOAD_FILES = ("split.json", "train.jsonl", "validation.jsonl", "test.jsonl")


class MaterializationError(ValueError):
    """Raised when a frozen training version cannot be reconstructed safely."""


@dataclass(frozen=True)
class MaterializedTask:
    split_path: Path
    train_file: Path
    validation_file: Path
    test_file: Path


def write_executable_training_config(
    task: TrainingTask,
    materialized: MaterializedTask,
    task_dir: Path,
    *,
    base_model_path: Path,
    cpt_adapter_path: Path | None = None,
) -> Path:
    """Rebuild and atomically write YAML from a normalized, path-free snapshot."""
    snapshot = task.config_snapshot
    if not isinstance(snapshot, dict) or set(snapshot) != {"preset", "qlora", "training"}:
        raise MaterializationError("training config snapshot is not normalized")
    preset = snapshot.get("preset")
    qlora = snapshot.get("qlora")
    training = snapshot.get("training")
    if not isinstance(preset, str) or not isinstance(qlora, dict) or not isinstance(training, dict):
        raise MaterializationError("training config snapshot is not normalized")

    overrides = {
        key: value
        for section in (qlora, training)
        for key, value in section.items()
        if key in ALLOWED_OVERRIDES
    }
    try:
        expected = normalized_config_snapshot(task.task_type, preset, overrides)
    except TrainingConfigError as exc:
        raise MaterializationError(str(exc)) from exc
    if expected != snapshot:
        raise MaterializationError("training config snapshot does not match a trusted preset")

    run_dir = _inside_task_root(task_dir, Path(task_dir) / "run")
    config = build_training_config(
        task.task_type,
        preset,
        overrides,
        {
            "model_name_or_path": str(Path(base_model_path).resolve(strict=True)),
            "train_file": str(materialized.train_file.resolve(strict=True)),
            "validation_file": str(materialized.validation_file.resolve(strict=True)),
            "output_dir": str(run_dir),
        },
        str(cpt_adapter_path.resolve(strict=True)) if cpt_adapter_path is not None else None,
    )
    config_path = _inside_task_root(task_dir, Path(task_dir) / "training.yaml")
    _write_yaml_atomic(config_path, config)
    return config_path


def _write_yaml_atomic(path: Path, payload: dict) -> None:
    import yaml

    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_text(temporary, yaml.safe_dump(payload, sort_keys=False))
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _inside_task_root(task_dir: Path, path: Path) -> Path:
    root = Path(task_dir).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MaterializationError("generated path escapes the task root") from exc
    return resolved


def load_frozen_samples(db, task_id: str, split: str) -> list[TestSample]:
    """Rebuild a split exclusively from its task-linked immutable versions."""
    if split not in SPLITS:
        raise MaterializationError(f"Unknown training split: {split}")

    rows = (
        db.query(TrainingTaskTest, TrainingTestVersion, TrainingTest, TrainingTestLog)
        .join(TrainingTestVersion, TrainingTaskTest.test_version_id == TrainingTestVersion.id)
        .join(TrainingTest, TrainingTestVersion.test_id == TrainingTest.id)
        .outerjoin(TrainingTestLog, TrainingTestLog.test_version_id == TrainingTestVersion.id)
        .filter(TrainingTaskTest.task_id == task_id, TrainingTaskTest.split_name == split)
        .order_by(TrainingTest.test_name, TrainingTest.platform, TrainingTestVersion.id)
        .all()
    )

    grouped: dict[int, tuple[TrainingTestVersion, TrainingTest, list[TrainingTestLog]]] = {}
    for _task_test, version, test, log in rows:
        if version.id not in grouped:
            grouped[version.id] = (version, test, [])
        if log is not None:
            grouped[version.id][2].append(log)

    return [
        _sample_from_version(version, test.platform, test.test_name, logs)
        for version, test, logs in grouped.values()
    ]


def materialize_task(db, task: TrainingTask, task_dir: Path) -> MaterializedTask:
    """Write immutable inputs under a lock that protects writers and recovery.

    Consumers must only read the data directory after this function returns;
    publication deliberately does not use symlink indirection.
    """
    if task.task_type not in {"cpt", "sft"}:
        raise MaterializationError(f"Unsupported task type: {task.task_type}")

    task_root = Path(task_dir)
    task_root.mkdir(parents=True, exist_ok=True)
    data_dir = task_root / _DATA_DIR
    with _exclusive_task_lock(task_root):
        _recover_interrupted_publication(task_root, data_dir)
        if published := _published_materialization(data_dir, task.id, task.task_type):
            return published
        samples_by_split = {
            split: load_frozen_samples(db, task.id, split)
            for split in SPLITS
        }
        attempt_id = uuid.uuid4().hex
        staging_dir = task_root / f"{_PREPARING_PREFIX}{attempt_id}"
        previous_dir = task_root / f"{_PREVIOUS_DATA_PREFIX}{attempt_id}"
        staging_dir.mkdir()
        try:
            split_path = staging_dir / "split.json"
            _write_json(split_path, {split: [sample.test_id for sample in samples_by_split[split]] for split in SPLITS})

            if task.task_type == "cpt":
                rows_by_split = {
                    split: build_cpt_rows(samples_by_split[split], split, MAX_LOG_CHARS)
                    for split in SPLITS
                }
            else:
                known_fault_types = _known_fault_types(samples_by_split)
                rows_by_split = {
                    split: build_sft_open_rows(
                        samples_by_split[split],
                        split,
                        MAX_LOG_CHARS,
                        known_fault_types=known_fault_types,
                        scenario_registry=None,
                    )
                    for split in SPLITS
                }

            file_paths = {
                split: staging_dir / f"{split}.jsonl"
                for split in SPLITS
            }
            for split, path in file_paths.items():
                _write_jsonl(path, rows_by_split[split])
            _write_completion_manifest(staging_dir, task.id, task.task_type)
            _fsync_directory(staging_dir)
            _replace_data_directory(task_root, staging_dir, data_dir, previous_dir)
        finally:
            _remove_generated_directory(task_root, staging_dir)

    return _materialized_paths(data_dir)


def _sample_from_version(
    version: TrainingTestVersion,
    platform: str,
    test_name: str,
    logs: list[TrainingTestLog],
) -> TestSample:
    if version.completeness != "complete":
        raise MaterializationError(
            f"Frozen Test version {version.id} for {test_name} is not complete"
        )
    if not isinstance(version.ground_truth, dict) or not isinstance(version.fip_info, dict):
        raise MaterializationError(f"Frozen Test version {version.id} has invalid JSON labels")

    log_by_key: dict[tuple[int, str], str] = {}
    for log in logs:
        key = (log.round_no, log.log_type)
        if key in log_by_key:
            raise MaterializationError(
                f"Frozen Test version {version.id} has duplicate log {key}"
            )
        log_by_key[key] = log.content

    actual_keys = set(log_by_key)
    expected_keys = set(REQUIRED_LOG_KEYS)
    duplicates = len(logs) != len(log_by_key)
    if duplicates:
        raise MaterializationError(f"Frozen Test version {version.id} has duplicate logs")
    if missing := expected_keys - actual_keys:
        raise MaterializationError(f"Frozen Test version {version.id} is missing logs: {sorted(missing)}")
    if extra := actual_keys - expected_keys:
        raise MaterializationError(f"Frozen Test version {version.id} has extra logs: {sorted(extra)}")

    return TestSample(
        test_id=test_name,
        path=Path(platform) / test_name,
        ground_truth=dict(version.ground_truth),
        fip_info=dict(version.fip_info),
        round1_qemu=log_by_key[(1, "qemu_console")],
        round2_qemu=log_by_key[(2, "qemu_console")],
        round1_events=log_by_key[(1, "fault_events")],
        round2_events=log_by_key[(2, "fault_events")],
        round1_metrics=log_by_key[(1, "system_metrics")],
        round2_metrics=log_by_key[(2, "system_metrics")],
    )


def _known_fault_types(samples_by_split: dict[str, list[TestSample]]) -> list[str]:
    return sorted(
        {
            fault_id
            for samples in samples_by_split.values()
            for sample in samples
            if sample.ground_truth.get("sample_class") != "normal"
            and sample.ground_truth.get("fault_type") != "NORMAL"
            for fault_id in [fault_type_id(sample.ground_truth.get("fault_type"))]
            if fault_id is not None
        }
    )


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    _write_text(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_data_directory(
    task_root: Path,
    staging_dir: Path,
    data_dir: Path,
    previous_dir: Path,
) -> None:
    had_data = data_dir.exists()
    if had_data:
        os.replace(data_dir, previous_dir)
    try:
        os.replace(staging_dir, data_dir)
    except Exception:
        if had_data and previous_dir.exists() and not data_dir.exists():
            try:
                os.replace(previous_dir, data_dir)
                _fsync_directory(task_root)
            except Exception:
                # The uniquely named backup remains for the next locked call.
                pass
        raise
    _fsync_directory(task_root)
    _remove_generated_directory(task_root, previous_dir)
    _fsync_directory(task_root)


@contextmanager
def _exclusive_task_lock(task_root: Path):
    if sys.platform != "linux" or fcntl is None:
        raise MaterializationError("Training materialization requires Linux fcntl.flock support")

    lock_path = task_root / _LOCK_FILE
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _recover_interrupted_publication(task_root: Path, data_dir: Path) -> None:
    backups = _attempt_directories(task_root, _PREVIOUS_DATA_PREFIX)
    if not data_dir.exists() and backups:
        newest_backup = max(backups, key=lambda path: path.stat().st_mtime_ns)
        os.replace(newest_backup, data_dir)
        _fsync_directory(task_root)

    if data_dir.exists():
        for path in _attempt_directories(task_root, _PREVIOUS_DATA_PREFIX):
            _remove_generated_directory(task_root, path)

    for path in _attempt_directories(task_root, _PREPARING_PREFIX):
        _remove_generated_directory(task_root, path)
    legacy_staging = task_root / _LEGACY_PREPARING_DIR
    if legacy_staging.exists() or legacy_staging.is_symlink():
        _remove_generated_directory(task_root, legacy_staging)


def _published_materialization(
    data_dir: Path,
    task_id: str,
    task_type: str,
) -> MaterializedTask | None:
    if not data_dir.is_dir() or data_dir.is_symlink():
        return None

    materialized = _materialized_paths(data_dir)
    try:
        manifest_path = data_dir / _MANIFEST_FILE
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not _manifest_matches(manifest, data_dir, task_id, task_type):
            return None
        split_data = json.loads(materialized.split_path.read_text(encoding="utf-8"))
        if (
            not isinstance(split_data, dict)
            or set(split_data) != set(SPLITS)
            or any(
                not isinstance(split_data[split], list)
                or any(not isinstance(test_id, str) for test_id in split_data[split])
                for split in SPLITS
            )
        ):
            return None
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None

    return materialized


def _write_completion_manifest(staging_dir: Path, task_id: str, task_type: str) -> None:
    entries = []
    for filename in _PAYLOAD_FILES:
        path = staging_dir / filename
        size, digest, row_count = _file_metadata(path, filename.endswith(".jsonl"))
        entry: dict[str, object] = {
            "filename": filename,
            "byte_size": size,
            "sha256": digest,
        }
        if row_count is not None:
            entry["row_count"] = row_count
        entries.append(entry)
    _write_json(
        staging_dir / _MANIFEST_FILE,
        {"task_id": task_id, "task_type": task_type, "files": entries},
    )


def _manifest_matches(manifest: object, data_dir: Path, task_id: str, task_type: str) -> bool:
    if not isinstance(manifest, dict) or set(manifest) != {"task_id", "task_type", "files"}:
        return False
    if (
        not isinstance(manifest["task_id"], str)
        or not isinstance(manifest["task_type"], str)
        or manifest["task_id"] != task_id
        or manifest["task_type"] != task_type
    ):
        return False
    entries = manifest["files"]
    if not isinstance(entries, list) or len(entries) != len(_PAYLOAD_FILES):
        return False

    by_filename: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            return False
        filename = entry["filename"]
        if filename in by_filename:
            return False
        by_filename[filename] = entry
    if set(by_filename) != set(_PAYLOAD_FILES):
        return False

    for filename in _PAYLOAD_FILES:
        entry = by_filename[filename]
        allowed_keys = {"filename", "byte_size", "sha256"}
        is_jsonl = filename.endswith(".jsonl")
        if is_jsonl:
            allowed_keys.add("row_count")
        if set(entry) != allowed_keys:
            return False
        if (
            not isinstance(entry["byte_size"], int)
            or isinstance(entry["byte_size"], bool)
            or entry["byte_size"] < 0
            or not isinstance(entry["sha256"], str)
            or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
        ):
            return False
        path = data_dir / filename
        if not path.is_file() or path.is_symlink():
            return False
        size, digest, row_count = _file_metadata(path, is_jsonl)
        if size != entry["byte_size"] or digest != entry["sha256"]:
            return False
        if is_jsonl and (
            not isinstance(entry["row_count"], int)
            or isinstance(entry["row_count"], bool)
            or entry["row_count"] < 0
            or row_count != entry["row_count"]
        ):
            return False
    return True


def _file_metadata(path: Path, is_jsonl: bool) -> tuple[int, str, int | None]:
    digest = sha256()
    byte_size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            byte_size += len(chunk)
            digest.update(chunk)

    if not is_jsonl:
        return byte_size, digest.hexdigest(), None

    row_count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not isinstance(json.loads(line), dict):
                raise ValueError(f"JSONL row in {path.name} is not an object")
            row_count += 1
    return byte_size, digest.hexdigest(), row_count


def _materialized_paths(data_dir: Path) -> MaterializedTask:
    return MaterializedTask(
        split_path=data_dir / "split.json",
        train_file=data_dir / "train.jsonl",
        validation_file=data_dir / "validation.jsonl",
        test_file=data_dir / "test.jsonl",
    )


def _attempt_directories(task_root: Path, prefix: str) -> list[Path]:
    return [
        path
        for path in task_root.iterdir()
        if path.name.startswith(prefix)
    ]


def _remove_generated_directory(task_root: Path, path: Path) -> None:
    is_attempt_directory = path.name.startswith((_PREPARING_PREFIX, _PREVIOUS_DATA_PREFIX))
    if path.parent != task_root or not (is_attempt_directory or path.name == _LEGACY_PREPARING_DIR):
        raise MaterializationError("Refusing to remove a path outside the task root")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
