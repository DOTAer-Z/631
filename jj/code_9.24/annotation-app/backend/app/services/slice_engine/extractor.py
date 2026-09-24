from __future__ import annotations

import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


class ExtractionSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedArchive:
    extracted_path: str


def _resolve_within(base_dir: Path, member_name: str) -> Path:
    target = (base_dir / member_name).resolve()
    base_resolved = base_dir.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise ExtractionSafetyError(f"path traversal detected: {member_name}") from exc
    return target


def _safe_extract_zip(archive_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(archive_path, mode="r") as zf:
        for member in zf.infolist():
            _ = _resolve_within(dest_dir, member.filename)
        zf.extractall(dest_dir)


def _safe_extract_tar(archive_path: Path, dest_dir: Path, mode: str) -> None:
    with tarfile.open(archive_path, mode=mode) as tf:
        for member in tf.getmembers():
            _ = _resolve_within(dest_dir, member.name)
        tf.extractall(dest_dir)


def extract_package_archive(*, package_id: int, task_id: int, stored_path: str) -> ExtractedArchive:
    settings = get_settings()
    extracted_root = settings.extracted_dir / f"package_{package_id}" / f"task_{task_id}"
    if extracted_root.exists():
        shutil.rmtree(extracted_root)
    extracted_root.mkdir(parents=True, exist_ok=True)

    archive_path = Path(stored_path)
    lower = archive_path.name.lower()

    if lower.endswith(".zip"):
        _safe_extract_zip(archive_path, extracted_root)
    elif lower.endswith(".tar.gz"):
        _safe_extract_tar(archive_path, extracted_root, mode="r:gz")
    elif lower.endswith(".tar"):
        _safe_extract_tar(archive_path, extracted_root, mode="r:")
    else:
        raise ExtractionSafetyError(f"unsupported archive extension: {archive_path.name}")

    return ExtractedArchive(extracted_path=str(extracted_root))
