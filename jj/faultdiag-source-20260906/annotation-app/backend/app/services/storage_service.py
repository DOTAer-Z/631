from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import FileTooLargeError

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    stored_path: str
    file_size: int
    sha256: str


class StorageService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def ensure_size_limit(self, file_size: int) -> None:
        if file_size > self.settings.max_upload_bytes:
            raise FileTooLargeError()

    def _ensure_packages_dir(self) -> Path:
        packages_dir = self.settings.packages_dir
        packages_dir.mkdir(parents=True, exist_ok=True)
        return packages_dir

    def save_package_bytes(self, file_bytes: bytes, extension: str) -> StoredFile:
        file_size = len(file_bytes)
        self.ensure_size_limit(file_size)

        packages_dir = self._ensure_packages_dir()
        filename = f"{uuid.uuid4().hex}{extension}"
        file_path = packages_dir / filename

        # UUID filename makes collisions extremely unlikely; retry once if needed.
        if file_path.exists():
            filename = f"{uuid.uuid4().hex}{extension}"
            file_path = packages_dir / filename

        file_path.write_bytes(file_bytes)

        sha256 = hashlib.sha256(file_bytes).hexdigest()

        return StoredFile(
            stored_path=str(file_path),
            file_size=file_size,
            sha256=sha256,
        )

    def delete_file_if_exists(self, stored_path: str) -> None:
        path = Path(stored_path)
        if path.exists() and path.is_file():
            path.unlink()

    # ── 文件夹上传支持（集成方案 A 扩展）──────────────────────────────────────
    # 浏览器以 multipart 形式上传整个文件夹的若干文件（每个文件带相对路径）。
    # 这里把它们落盘到一个临时 staging 目录（流式，避免大文件占满内存），再原样
    # 打成 .tar.gz 存入 packages_dir，之后复用既有的「解压→找 data 根→入库」流水线。
    @staticmethod
    def _safe_relpath(rel: str) -> Path | None:
        rel = (rel or "").replace("\\", "/")
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        if not parts:
            return None
        return Path(*parts)

    @staticmethod
    def _sha256_of_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def save_package_from_uploads(
        self, files: Sequence[UploadFile], rel_paths: Sequence[str]
    ) -> tuple[StoredFile, str]:
        """把整个文件夹的多文件上传暂存并打包为 .tar.gz，返回 (StoredFile, 推断包名)。"""
        packages_dir = self._ensure_packages_dir()
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="folder_upload_", dir=str(self.settings.storage_root)))
        total = 0
        root_name = ""
        try:
            for upload, rel in zip(files, rel_paths):
                safe = self._safe_relpath(rel or (upload.filename or ""))
                if safe is None:
                    continue
                if not root_name:
                    root_name = safe.parts[0]
                dest = staging / safe
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as out:
                    while True:
                        chunk = await upload.read(_CHUNK)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.settings.max_upload_bytes:
                            raise FileTooLargeError()
                        out.write(chunk)

            if total == 0:
                raise FileTooLargeError()  # 没有任何有效内容；交由上层转 4xx

            filename = f"{uuid.uuid4().hex}.tar.gz"
            archive_path = packages_dir / filename
            with tarfile.open(archive_path, "w:gz") as tar:
                for child in sorted(staging.iterdir()):
                    tar.add(str(child), arcname=child.name)

            stored = StoredFile(
                stored_path=str(archive_path),
                file_size=archive_path.stat().st_size,
                sha256=self._sha256_of_file(archive_path),
            )
            return stored, (root_name or "dataset")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
