import os
from pathlib import Path
from typing import Optional, Union

DEFAULT_CHUNK_SIZE = 1024 * 1024


def detect_archive_extension(filename: str) -> Optional[str]:
    lower = (filename or "").lower()
    if lower.endswith(".tar.gz"):
        return "tar.gz"
    if lower.endswith(".tgz"):
        return "tgz"
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".tar"):
        return "tar"
    return None


def ensure_safe_storage_path(root: Path, stored_filename: str) -> Path:
    if not stored_filename:
        raise ValueError("stored filename is required")
    if "/" in stored_filename or "\\" in stored_filename:
        raise ValueError("invalid storage path")

    filename_path = Path(stored_filename)
    if filename_path.is_absolute() or filename_path.name != stored_filename:
        raise ValueError("invalid storage path")

    root_resolved = root.resolve(strict=False)
    candidate = (root_resolved / stored_filename).resolve(strict=False)
    if root_resolved not in candidate.parents:
        raise ValueError("invalid storage path")
    return candidate


def validate_size(size_bytes: int, max_size: int) -> None:
    if max_size <= 0:
        raise ValueError("max_size must be positive")
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if size_bytes > max_size:
        raise ValueError("file too large")


def write_upload_to_temp(upload_file, temp_path: Union[str, Path], max_size: int) -> int:
    stream = getattr(upload_file, "file", upload_file)
    if not hasattr(stream, "read"):
        raise ValueError("upload_file must provide a readable stream")

    temp = Path(temp_path)
    temp.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0

    try:
        with temp.open("xb") as f:
            while True:
                chunk = stream.read(DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                bytes_written += len(chunk)
                validate_size(bytes_written, max_size)
                f.write(chunk)
    except Exception:
        remove_file_if_exists(temp)
        raise

    return bytes_written


def finalize_atomic_move(temp_path: Union[str, Path], final_path: Union[str, Path]) -> Path:
    temp = Path(temp_path)
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, final)
    return final


def remove_file_if_exists(path: Union[str, Path]) -> bool:
    target = Path(path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
