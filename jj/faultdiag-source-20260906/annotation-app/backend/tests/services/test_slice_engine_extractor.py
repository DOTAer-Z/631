from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.services.slice_engine.extractor import ExtractionSafetyError, extract_package_archive


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _tar_bytes(files: dict[str, bytes], mode: str = "w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_zip_happy_path(phase2_settings, tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.zip"
    archive_path.write_bytes(_zip_bytes({"cpu0/a.log": b"1 ok\n"}))

    out = extract_package_archive(package_id=11, task_id=21, stored_path=str(archive_path))

    extracted = Path(out.extracted_path) / "cpu0/a.log"
    assert extracted.exists()
    assert extracted.read_bytes() == b"1 ok\n"


def test_extract_tar_happy_path(phase2_settings, tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.tar"
    archive_path.write_bytes(_tar_bytes({"cpu0/a.log": b"1 ok\n"}, mode="w"))

    out = extract_package_archive(package_id=11, task_id=22, stored_path=str(archive_path))

    extracted = Path(out.extracted_path) / "cpu0/a.log"
    assert extracted.exists()


def test_extract_targz_happy_path(phase2_settings, tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.tar.gz"
    archive_path.write_bytes(_tar_bytes({"cpu0/a.log": b"1 ok\n"}, mode="w:gz"))

    out = extract_package_archive(package_id=11, task_id=23, stored_path=str(archive_path))

    extracted = Path(out.extracted_path) / "cpu0/a.log"
    assert extracted.exists()


@pytest.mark.parametrize(
    "archive_name, archive_bytes",
    [
        (
            "bad.zip",
            _zip_bytes({"../escape.log": b"x"}),
        ),
        (
            "bad.tar",
            _tar_bytes({"../escape.log": b"x"}, mode="w"),
        ),
        (
            "bad.tar.gz",
            _tar_bytes({"../escape.log": b"x"}, mode="w:gz"),
        ),
    ],
)
def test_extract_rejects_path_traversal(
    phase2_settings,
    tmp_path: Path,
    archive_name: str,
    archive_bytes: bytes,
) -> None:
    archive_path = tmp_path / archive_name
    archive_path.write_bytes(archive_bytes)

    with pytest.raises(ExtractionSafetyError):
        extract_package_archive(package_id=12, task_id=24, stored_path=str(archive_path))
