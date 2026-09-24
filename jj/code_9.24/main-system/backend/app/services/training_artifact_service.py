from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import hexdigits
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingTask,
    TrainingTaskTest,
)


_TERMINAL_TASK_STATES = {"cancelled", "succeeded", "failed", "interrupted"}
_HISTORICAL_DATA_ARTIFACT_TYPES = {"config", "split", "dataset"}
_QUARANTINE_DIRECTORY = ".training-quarantine"
_ARTIFACT_REGISTRY_LOCK_KEY = 0x545241494E415254
_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_DIRECTORY_FLAGS = _OPEN_FLAGS | os.O_DIRECTORY
_UNRESOLVED_DELETION_STATES = {"staged", "pending_cleanup", "recovery_required"}


class TrainingArtifactNotFound(LookupError):
    pass


class TrainingArtifactConflict(RuntimeError):
    pass


@dataclass
class OpenedArtifactFile:
    fd: int

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass(frozen=True)
class StagedArtifactDeletion:
    artifact_id: str
    root: Path
    components: tuple[str, ...]
    quarantine_name: str

    @property
    def quarantine_path(self) -> Path:
        return self.root / _QUARANTINE_DIRECTORY / self.quarantine_name

    def restore(self) -> None:
        _restore_quarantine_entry(self.root, self.components, self.quarantine_name)

    def cleanup(self) -> None:
        _remove_quarantine_entry(self.root, self.quarantine_name)


def resolve_artifact_path(db: Session, artifact_id: str, output_root: str | Path) -> Path:
    """Return the trusted lexical path after descriptor-relative validation."""
    artifact = _locked_artifact(db, artifact_id)
    root = _trusted_root(output_root)
    components = _path_components(artifact.relative_path)
    _assert_entry_safe(root, components)
    return root.joinpath(*components)


def open_artifact_file(db: Session, artifact_id: str, output_root: str | Path) -> OpenedArtifactFile:
    artifact = _locked_artifact(db, artifact_id)
    root = _trusted_root(output_root)
    components = _path_components(artifact.relative_path)
    return _open_regular_file(root, components)


def open_relative_regular_file(
    output_root: str | Path, relative_path: str
) -> OpenedArtifactFile:
    root = _trusted_root(output_root)
    components = _path_components(relative_path)
    return _open_regular_file(root, components)


def _open_regular_file(
    root: Path, components: tuple[str, ...]
) -> OpenedArtifactFile:
    root_fd = _open_root_fd(root)
    try:
        parent_fd, name = _open_parent_fd(root_fd, components)
        try:
            fd = os.open(name, _OPEN_FLAGS, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        raise _filesystem_error(exc) from exc
    finally:
        os.close(root_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TrainingArtifactConflict("artifact is not a regular file")
        return OpenedArtifactFile(fd=fd)
    except Exception:
        os.close(fd)
        raise


def register_artifact(
    db: Session,
    output_root: str | Path,
    *,
    task_id: str | None,
    artifact_type: str,
    relative_path: str,
    size_bytes: int | None = None,
    sha256: str | None = None,
    metadata_json: dict | None = None,
    artifact_id: str | None = None,
) -> TrainingArtifact:
    """Register an existing trusted-root entry without owning the transaction."""
    with db.no_autoflush:
        acquire_artifact_registry_lock(db)
        root = _trusted_root(output_root)
        components = _path_components(relative_path)
        _assert_entry_safe(root, components)
        _assert_path_available(db, components)

    values = {
        "task_id": task_id,
        "artifact_type": artifact_type,
        "relative_path": relative_path,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "metadata_json": metadata_json,
    }
    if artifact_id is not None:
        values["id"] = artifact_id
    artifact = TrainingArtifact(**values)
    db.add(artifact)
    db.flush()
    return artifact


def stage_artifact_deletion(
    db: Session, artifact_id: str, output_root: str | Path
) -> StagedArtifactDeletion:
    with db.no_autoflush:
        acquire_artifact_registry_lock(db)
        artifact = _locked_artifact(db, artifact_id)
        root = _trusted_root(output_root)
        components = _path_components(artifact.relative_path)
        _assert_entry_safe(root, components)
        _assert_deletable(db, artifact)
        _assert_no_registry_overlap(db, artifact)

        quarantine_name = f"{artifact.id}-{uuid4().hex}"
        _move_entry_to_quarantine(root, components, quarantine_name)
        now = datetime.utcnow()
        artifact.deleted_at = now
        artifact.deletion_state = "staged"
        artifact.quarantine_name = quarantine_name
        artifact.deletion_updated_at = now
    return StagedArtifactDeletion(
        artifact_id=artifact.id,
        root=root,
        components=components,
        quarantine_name=quarantine_name,
    )


def mark_artifact_cleanup_pending(db: Session, artifact_id: str) -> TrainingArtifact:
    artifact = _journal_artifact(db, artifact_id)
    if artifact.deleted_at is None or not artifact.quarantine_name:
        raise TrainingArtifactConflict("artifact deletion is not staged")
    artifact.deletion_state = "pending_cleanup"
    artifact.deletion_updated_at = datetime.utcnow()
    return artifact


def mark_artifact_cleaned(db: Session, artifact_id: str) -> TrainingArtifact:
    artifact = _journal_artifact(db, artifact_id)
    if artifact.deleted_at is None:
        raise TrainingArtifactConflict("active artifact cannot be marked cleaned")
    artifact.deletion_state = "cleaned"
    artifact.quarantine_name = None
    artifact.deletion_updated_at = datetime.utcnow()
    return artifact


def mark_artifact_recovery_required(
    db: Session, artifact_id: str, quarantine_name: str
) -> TrainingArtifact:
    artifact = _journal_artifact(db, artifact_id)
    _validate_quarantine_name(quarantine_name)
    artifact.deletion_state = "recovery_required"
    artifact.quarantine_name = quarantine_name
    artifact.deletion_updated_at = datetime.utcnow()
    return artifact


def reconcile_artifact_deletions(db: Session, output_root: str | Path) -> None:
    """Repair durable deletion journals and unambiguous quarantine orphans."""
    acquire_artifact_registry_lock(db)
    root = _trusted_root(output_root)
    quarantine_names = set(_list_quarantine_entries(root))
    journal_rows = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.deletion_state.in_(
                _UNRESOLVED_DELETION_STATES
            )
        )
        .with_for_update()
        .all()
    )

    for artifact in journal_rows:
        quarantine_name = artifact.quarantine_name
        if not quarantine_name:
            continue
        _validate_quarantine_name(quarantine_name)
        quarantine_exists = quarantine_name in quarantine_names
        original_exists = _entry_exists(root, _path_components(artifact.relative_path))
        if artifact.deleted_at is None:
            _reconcile_active_artifact(
                artifact,
                root,
                original_exists=original_exists,
                quarantine_exists=quarantine_exists,
            )
        else:
            _reconcile_deleted_artifact(
                db,
                artifact,
                root,
                original_exists=original_exists,
                quarantine_exists=quarantine_exists,
            )
        quarantine_names.discard(quarantine_name)

    artifacts = db.query(TrainingArtifact).with_for_update().all()
    by_id = {artifact.id: artifact for artifact in artifacts}
    orphan_groups: dict[str, list[str]] = {}
    for quarantine_name in quarantine_names:
        artifact_id = _orphan_artifact_id(quarantine_name, by_id)
        if artifact_id is not None:
            orphan_groups.setdefault(artifact_id, []).append(quarantine_name)

    for artifact_id, names in orphan_groups.items():
        if len(names) != 1:
            continue
        artifact = by_id[artifact_id]
        components = _path_components(artifact.relative_path)
        original_exists = _entry_exists(root, components)
        quarantine_name = names[0]
        if artifact.deleted_at is None:
            if original_exists:
                continue
            try:
                _restore_quarantine_entry(root, components, quarantine_name)
            except (OSError, TrainingArtifactConflict, TrainingArtifactNotFound):
                _set_recovery_required(artifact, quarantine_name)
            else:
                _clear_deletion_journal(artifact)
        else:
            _set_cleanup_pending(artifact, quarantine_name)
            _reconcile_deleted_artifact(
                db,
                artifact,
                root,
                original_exists=original_exists,
                quarantine_exists=True,
            )


def soft_delete_task(db: Session, task_id: str) -> TrainingTask:
    task = (
        db.query(TrainingTask)
        .filter(TrainingTask.id == task_id, TrainingTask.deleted_at.is_(None))
        .with_for_update()
        .first()
    )
    if task is None:
        raise TrainingArtifactNotFound("training task not found")
    if task.state not in _TERMINAL_TASK_STATES:
        raise TrainingArtifactConflict("only terminal training tasks can be deleted")
    task.deleted_at = datetime.utcnow()
    db.flush()
    return task


def acquire_artifact_registry_lock(db: Session) -> None:
    if _is_postgresql(db):
        db.execute(select(func.pg_advisory_xact_lock(_ARTIFACT_REGISTRY_LOCK_KEY)))


def _acquire_artifact_registry_lock(db: Session) -> None:
    acquire_artifact_registry_lock(db)


def _is_postgresql(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def _locked_artifact(db: Session, artifact_id: str) -> TrainingArtifact:
    artifact = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.id == artifact_id,
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .with_for_update()
        .first()
    )
    if artifact is None:
        raise TrainingArtifactNotFound("training artifact not found")
    return artifact


def _journal_artifact(db: Session, artifact_id: str) -> TrainingArtifact:
    artifact = (
        db.query(TrainingArtifact)
        .filter(TrainingArtifact.id == artifact_id)
        .with_for_update()
        .first()
    )
    if artifact is None:
        raise TrainingArtifactNotFound("training artifact not found")
    return artifact


def _trusted_root(output_root: str | Path) -> Path:
    try:
        root = Path(output_root).resolve(strict=True)
    except FileNotFoundError as exc:
        raise TrainingArtifactNotFound("training artifact output root is not available") from exc
    if not root.is_dir():
        raise TrainingArtifactConflict("training artifact output root is not safe")
    return root


def _path_components(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        raise TrainingArtifactConflict("artifact path is not safe")
    if relative_path.startswith("/"):
        raise TrainingArtifactConflict("artifact path is not safe")
    components = tuple(relative_path.split("/"))
    if any(component in {"", ".", ".."} for component in components):
        raise TrainingArtifactConflict("artifact path is not safe")
    if components[0] == _QUARANTINE_DIRECTORY:
        raise TrainingArtifactConflict("artifact path is not safe")
    return components


def _validate_quarantine_name(quarantine_name: str) -> None:
    if (
        not quarantine_name
        or quarantine_name in {".", ".."}
        or "/" in quarantine_name
        or "\x00" in quarantine_name
    ):
        raise TrainingArtifactConflict("artifact quarantine name is not safe")


def _open_root_fd(root: Path) -> int:
    try:
        return os.open(root, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise _filesystem_error(exc) from exc


def _open_parent_fd(root_fd: int, components: tuple[str, ...]) -> tuple[int, str]:
    current_fd = os.dup(root_fd)
    try:
        for component in components[:-1]:
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, components[-1]
    except OSError as exc:
        os.close(current_fd)
        raise _filesystem_error(exc) from exc


def _open_quarantine_fd(root_fd: int, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(_QUARANTINE_DIRECTORY, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
    try:
        return os.open(_QUARANTINE_DIRECTORY, _DIRECTORY_FLAGS, dir_fd=root_fd)
    except OSError as exc:
        raise _filesystem_error(exc) from exc


def _assert_entry_safe(root: Path, components: tuple[str, ...]) -> os.stat_result:
    root_fd = _open_root_fd(root)
    try:
        parent_fd, name = _open_parent_fd(root_fd, components)
        try:
            item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise _filesystem_error(exc) from exc
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)
    if stat.S_ISLNK(item_stat.st_mode):
        raise TrainingArtifactConflict("artifact path is not safe")
    return item_stat


def _entry_exists(root: Path, components: tuple[str, ...]) -> bool:
    try:
        _assert_entry_safe(root, components)
        return True
    except TrainingArtifactNotFound:
        return False


def _move_entry_to_quarantine(
    root: Path, components: tuple[str, ...], quarantine_name: str
) -> None:
    _validate_quarantine_name(quarantine_name)
    root_fd = _open_root_fd(root)
    try:
        parent_fd, name = _open_parent_fd(root_fd, components)
        try:
            item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(item_stat.st_mode):
                raise TrainingArtifactConflict("artifact path is not safe")
            quarantine_fd = _open_quarantine_fd(root_fd, create=True)
            try:
                try:
                    os.stat(quarantine_name, dir_fd=quarantine_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise TrainingArtifactConflict("artifact quarantine entry already exists")
                os.rename(name, quarantine_name, src_dir_fd=parent_fd, dst_dir_fd=quarantine_fd)
            finally:
                os.close(quarantine_fd)
        except OSError as exc:
            raise _filesystem_error(exc) from exc
        finally:
            os.close(parent_fd)
    finally:
        os.close(root_fd)


def _restore_quarantine_entry(
    root: Path, components: tuple[str, ...], quarantine_name: str
) -> None:
    _validate_quarantine_name(quarantine_name)
    root_fd = _open_root_fd(root)
    try:
        quarantine_fd = _open_quarantine_fd(root_fd, create=False)
        try:
            quarantine_stat = os.stat(
                quarantine_name,
                dir_fd=quarantine_fd,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(quarantine_stat.st_mode):
                raise TrainingArtifactConflict("artifact quarantine entry is not safe")
            parent_fd, name = _open_parent_fd(root_fd, components)
            try:
                try:
                    os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise TrainingArtifactConflict("artifact restore target already exists")
                os.rename(
                    quarantine_name,
                    name,
                    src_dir_fd=quarantine_fd,
                    dst_dir_fd=parent_fd,
                )
            finally:
                os.close(parent_fd)
        finally:
            os.close(quarantine_fd)
    except OSError as exc:
        raise _filesystem_error(exc) from exc
    finally:
        os.close(root_fd)


def _list_quarantine_entries(root: Path) -> list[str]:
    root_fd = _open_root_fd(root)
    try:
        try:
            quarantine_fd = _open_quarantine_fd(root_fd, create=False)
        except TrainingArtifactNotFound:
            return []
        try:
            return os.listdir(quarantine_fd)
        finally:
            os.close(quarantine_fd)
    finally:
        os.close(root_fd)


def _remove_quarantine_entry(root: Path, quarantine_name: str) -> None:
    _validate_quarantine_name(quarantine_name)
    root_fd = _open_root_fd(root)
    try:
        quarantine_fd = _open_quarantine_fd(root_fd, create=False)
        try:
            _remove_entry_at(quarantine_fd, quarantine_name)
        finally:
            os.close(quarantine_fd)
    finally:
        os.close(root_fd)


def _remove_entry_at(parent_fd: int, name: str) -> None:
    item_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(item_stat.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return

    directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        for child_name in os.listdir(directory_fd):
            _remove_entry_at(directory_fd, child_name)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _filesystem_error(exc: OSError) -> TrainingArtifactConflict | TrainingArtifactNotFound:
    if exc.errno == errno.ENOENT:
        return TrainingArtifactNotFound("training artifact is not available")
    return TrainingArtifactConflict("artifact path is not safe")


def _assert_path_available(db: Session, components: tuple[str, ...]) -> None:
    artifacts = (
        db.query(TrainingArtifact)
        .filter(
            or_(
                TrainingArtifact.deleted_at.is_(None),
                TrainingArtifact.deletion_state.is_(None),
                TrainingArtifact.deletion_state != "cleaned",
            )
        )
        .with_for_update()
        .all()
    )
    for artifact in artifacts:
        if _components_overlap(components, _path_components(artifact.relative_path)):
            raise TrainingArtifactConflict("artifact path overlaps another active artifact")


def _assert_no_registry_overlap(db: Session, artifact: TrainingArtifact) -> None:
    target_components = _path_components(artifact.relative_path)
    others = (
        db.query(TrainingArtifact)
        .filter(TrainingArtifact.id != artifact.id, TrainingArtifact.deleted_at.is_(None))
        .with_for_update()
        .all()
    )
    for other in others:
        if _components_overlap(target_components, _path_components(other.relative_path)):
            raise TrainingArtifactConflict("artifact path overlaps another active artifact")


def _components_overlap(first: tuple[str, ...], second: tuple[str, ...]) -> bool:
    shared_length = min(len(first), len(second))
    return first[:shared_length] == second[:shared_length]


def _assert_deletable(db: Session, artifact: TrainingArtifact) -> None:
    dependent_sft_task = (
        db.query(TrainingTask.id)
        .filter(
            TrainingTask.cpt_adapter_artifact_id == artifact.id,
            TrainingTask.task_type == "sft",
        )
        .first()
    )
    if artifact.artifact_type == "final_adapter" and dependent_sft_task is not None:
        raise TrainingArtifactConflict("CPT adapter is referenced by an SFT task")
    evaluation_reference = TrainingEvaluation.source_sft_artifact_id == artifact.id
    if artifact.task_id is not None:
        evaluation_reference = or_(
            evaluation_reference,
            TrainingEvaluation.source_sft_task_id == artifact.task_id,
        )
    dependent_evaluation = (
        db.query(TrainingEvaluation.id)
        .filter(evaluation_reference)
        .first()
    )
    if artifact.artifact_type == "final_adapter" and dependent_evaluation is not None:
        raise TrainingArtifactConflict("SFT adapter is referenced by an evaluation task")
    if artifact.artifact_type in _HISTORICAL_DATA_ARTIFACT_TYPES and artifact.task_id is not None:
        historical_link = (
            db.query(TrainingTaskTest.id)
            .filter(TrainingTaskTest.task_id == artifact.task_id)
            .first()
        )
        if historical_link is not None:
            raise TrainingArtifactConflict("historical task data artifacts cannot be deleted")


def _reconcile_active_artifact(
    artifact: TrainingArtifact,
    root: Path,
    *,
    original_exists: bool,
    quarantine_exists: bool,
) -> None:
    if quarantine_exists and not original_exists:
        try:
            _restore_quarantine_entry(
                root,
                _path_components(artifact.relative_path),
                artifact.quarantine_name,
            )
        except (OSError, TrainingArtifactConflict, TrainingArtifactNotFound):
            _set_recovery_required(artifact, artifact.quarantine_name)
        else:
            _clear_deletion_journal(artifact)
    elif original_exists and not quarantine_exists:
        _clear_deletion_journal(artifact)
    else:
        _set_recovery_required(artifact, artifact.quarantine_name)


def _reconcile_deleted_artifact(
    db: Session,
    artifact: TrainingArtifact,
    root: Path,
    *,
    original_exists: bool,
    quarantine_exists: bool,
) -> None:
    quarantine_name = artifact.quarantine_name
    if quarantine_exists:
        try:
            _remove_quarantine_entry(root, quarantine_name)
        except (OSError, TrainingArtifactConflict, TrainingArtifactNotFound):
            _set_cleanup_pending(artifact, quarantine_name)
            return
    if original_exists:
        if _has_active_path_owner(db, artifact):
            _set_cleaned(artifact)
            return
        try:
            _move_entry_to_quarantine(
                root,
                _path_components(artifact.relative_path),
                quarantine_name,
            )
        except (OSError, TrainingArtifactConflict, TrainingArtifactNotFound):
            _set_recovery_required(artifact, quarantine_name)
            return
        try:
            _remove_quarantine_entry(root, quarantine_name)
        except (OSError, TrainingArtifactConflict, TrainingArtifactNotFound):
            _set_cleanup_pending(artifact, quarantine_name)
            return
    _set_cleaned(artifact)


def _has_active_path_owner(db: Session, artifact: TrainingArtifact) -> bool:
    target_components = _path_components(artifact.relative_path)
    active_artifacts = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.id != artifact.id,
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .with_for_update()
        .all()
    )
    return any(
        _components_overlap(target_components, _path_components(candidate.relative_path))
        for candidate in active_artifacts
    )


def _set_cleanup_pending(artifact: TrainingArtifact, quarantine_name: str) -> None:
    artifact.deletion_state = "pending_cleanup"
    artifact.quarantine_name = quarantine_name
    artifact.deletion_updated_at = datetime.utcnow()


def _set_recovery_required(artifact: TrainingArtifact, quarantine_name: str) -> None:
    artifact.deletion_state = "recovery_required"
    artifact.quarantine_name = quarantine_name
    artifact.deletion_updated_at = datetime.utcnow()


def _set_cleaned(artifact: TrainingArtifact) -> None:
    artifact.deletion_state = "cleaned"
    artifact.quarantine_name = None
    artifact.deletion_updated_at = datetime.utcnow()


def _clear_deletion_journal(artifact: TrainingArtifact) -> None:
    artifact.deletion_state = None
    artifact.quarantine_name = None
    artifact.deletion_updated_at = None


def _orphan_artifact_id(
    quarantine_name: str, artifacts_by_id: dict[str, TrainingArtifact]
) -> str | None:
    for artifact_id in artifacts_by_id:
        prefix = f"{artifact_id}-"
        if not quarantine_name.startswith(prefix):
            continue
        suffix = quarantine_name[len(prefix):]
        if len(suffix) == 32 and all(character in hexdigits for character in suffix):
            return artifact_id
    return None
