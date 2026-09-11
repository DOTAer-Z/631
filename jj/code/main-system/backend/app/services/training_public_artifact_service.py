from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4


_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_LIVE_PUBLIC_LOG_BYTES = 8 * 1024 * 1024


def path_metadata(path: str | Path) -> tuple[int, str]:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("registered artifact must be a regular file")
    digest = hashlib.sha256()
    size = 0
    with candidate.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def write_sanitized_log(
    source: str | Path,
    destination: str | Path,
    services: Any,
    task_root: str | Path,
    *,
    include_partial: bool = True,
    skip_source_size: int | None = None,
) -> int:
    source_path = Path(source)
    destination_path = Path(destination)
    task_root_path = Path(task_root)
    try:
        source_parts = source_path.relative_to(task_root_path).parts
        destination_parts = destination_path.relative_to(task_root_path).parts
    except ValueError as exc:
        raise ValueError("log paths must remain inside the task root") from exc
    if source_parts != ("child.log",):
        raise ValueError("log source path is not safe")
    if destination_parts not in {
        ("public", "training.log"),
        ("public", "evaluation.log"),
    }:
        raise ValueError("log destination path is not safe")
    roots = {
        str(Path(services.output_root).resolve()),
        str(Path(services.base_model_path).resolve()),
        str(task_root_path.absolute()),
    }
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        task_fd = os.open(task_root_path, directory_flags)
    except OSError as exc:
        raise ValueError("log task root is not safe") from exc
    source_fd = -1
    public_fd = -1
    try:
        try:
            source_fd = os.open("child.log", file_flags, dir_fd=task_fd)
        except FileNotFoundError:
            source_size = 0
            text = ""
        except OSError as exc:
            raise ValueError("log source path is not safe") from exc
        else:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError("log source must be a regular file")
            source_size = source_stat.st_size
            if skip_source_size is not None and source_size == skip_source_size:
                return source_size
            if (
                not include_partial
                and source_size > _MAX_LIVE_PUBLIC_LOG_BYTES
                and skip_source_size is not None
                and skip_source_size >= _MAX_LIVE_PUBLIC_LOG_BYTES
            ):
                return source_size
            with os.fdopen(
                source_fd, "rb"
            ) as source_handle:
                source_fd = -1
                raw = (
                    source_handle.read()
                    if include_partial
                    else source_handle.read(_MAX_LIVE_PUBLIC_LOG_BYTES)
                )
        try:
            public_fd = os.open("public", directory_flags, dir_fd=task_fd)
        except OSError as exc:
            raise ValueError("log destination directory is not safe") from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        os.close(task_fd)
    if source_size == 0:
        raw = b""
    if not include_partial and raw and not raw.endswith(b"\n"):
        final_newline = raw.rfind(b"\n")
        raw = raw[: final_newline + 1] if final_newline >= 0 else b""
    text = raw.decode("utf-8", errors="replace")
    for root in sorted(roots, key=len, reverse=True):
        text = text.replace(root, "[REDACTED_PATH]")
    text = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_KEY]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s]+",
        r"\1=[REDACTED]",
        text,
    )
    temporary_name = f".{destination_path.name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(
            os.open(temporary_name, flags, 0o600, dir_fd=public_fd),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            destination_path.name,
            src_dir_fd=public_fd,
            dst_dir_fd=public_fd,
        )
    finally:
        try:
            os.unlink(temporary_name, dir_fd=public_fd)
        except FileNotFoundError:
            pass
        os.close(public_fd)
    return source_size
