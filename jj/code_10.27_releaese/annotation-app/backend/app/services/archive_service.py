from __future__ import annotations

import io
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import InvalidArchiveTypeError

_ALLOWED_EXTENSIONS = (".zip", ".tar", ".tar.gz")
_ALLOWED_MIME = {
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
}
_GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True)
class ArchiveValidationResult:
    archive_type: str
    extension: str


def _extension_from_name(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".tar.gz"):
        return ".tar.gz"
    if lower.endswith(".tar"):
        return ".tar"
    if lower.endswith(".zip"):
        return ".zip"
    return ""


def _archive_type_from_extension(ext: str) -> str:
    if ext == ".zip":
        return "zip"
    if ext == ".tar":
        return "tar"
    if ext == ".tar.gz":
        return "tar.gz"
    raise InvalidArchiveTypeError()


def _is_valid_archive_by_content(ext: str, file_bytes: bytes) -> bool:
    buffer = io.BytesIO(file_bytes)

    if ext == ".zip":
        return zipfile.is_zipfile(buffer)

    if ext == ".tar":
        if file_bytes.startswith(_GZIP_MAGIC):
            return False
        try:
            with tarfile.open(fileobj=buffer, mode="r:"):
                return True
        except tarfile.TarError:
            return False

    if ext == ".tar.gz":
        if not file_bytes.startswith(_GZIP_MAGIC):
            return False
        try:
            with tarfile.open(fileobj=buffer, mode="r:gz"):
                return True
        except tarfile.TarError:
            return False

    return False


def validate_archive(filename: str, content_type: str | None, file_bytes: bytes) -> ArchiveValidationResult:
    ext = _extension_from_name(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise InvalidArchiveTypeError()

    # MIME is treated as advisory because browser/client values may be unreliable.
    mime_known = content_type in _ALLOWED_MIME if content_type else False
    content_valid = _is_valid_archive_by_content(ext, file_bytes)

    if not content_valid:
        raise InvalidArchiveTypeError()

    # If MIME is unknown we still accept, relying on content validation.
    _ = mime_known
    return ArchiveValidationResult(archive_type=_archive_type_from_extension(ext), extension=ext)


def _matches_root_marker(name: str, markers: list[str]) -> bool:
    """A dir is a parse-root candidate if its lowercased name equals a marker, or
    starts with ``<marker>_`` / ``<marker>-`` (so ``data``, ``data_sample``,
    ``data-2024`` match, but ``dataset`` does not)."""
    lowered = name.lower()
    for marker in markers:
        m = marker.lower()
        if lowered == m or lowered.startswith(f"{m}_") or lowered.startswith(f"{m}-"):
            return True
    return False


def find_data_root(extracted_root: Path, markers: list[str] | None = None) -> Path:
    """Locate the directory whose direct children are the cpu-level folders.

    Strategy (folder names are no longer required to look like ``cpuN``):
    1. Prefer a directory matching a configured name marker (``data*``); shallowest wins.
    2. Otherwise strip single-child wrapper directories (e.g. ``archive-name/``)
       until a level has files or multiple subdirectories.

    The caller (import worker) still maps: parts[0]=cpu_name, parts[1:-1]=module_path
    (multi-level, may be empty), parts[-1]=filename — relative to the returned root.
    """
    if markers is None:
        markers = get_settings().import_data_root_markers

    candidates = [
        path
        for path in [extracted_root, *extracted_root.rglob("*")]
        if path.is_dir() and _matches_root_marker(path.name, markers)
    ]
    if candidates:
        candidates.sort(key=lambda path: (len(path.parts), str(path)))
        return candidates[0]

    current = extracted_root
    while True:
        entries = list(current.iterdir())
        subdirs = [entry for entry in entries if entry.is_dir()]
        files = [entry for entry in entries if entry.is_file()]
        if len(subdirs) == 1 and not files:
            current = subdirs[0]
            continue
        break
    return current
