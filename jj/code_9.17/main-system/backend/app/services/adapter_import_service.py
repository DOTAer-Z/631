from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import stat
import struct
import tarfile
from typing import BinaryIO
import inspect
from uuid import UUID, uuid4
import zipfile

from safetensors import safe_open

from app.models.training_task import TrainingArtifact
from app.services.training_artifact_service import (
    acquire_artifact_registry_lock,
    register_artifact,
)


MAX_ARCHIVE_BYTES = 1024**3
MAX_EXPANDED_BYTES = 2 * 1024**3
MAX_FILES = 64
MAX_ARCHIVE_MEMBERS = 128
MAX_METADATA_BYTES = 4 * 1024**2
MAX_TOKENIZER_JSON_BYTES = 64 * 1024**2
ALLOWED_STAGES = {"cpt", "sft"}
REQUIRED_FILES = {"adapter_config.json", "adapter_model.safetensors"}
SUPPORTED_BASE_MODEL_ID = "qwen-qwen3.5-9b"
NORMALIZED_BASE_MODEL = "Qwen/Qwen3.5-9B"

_CHUNK_BYTES = 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
_STAGING_NAMESPACE = ".adapter-import-staging"
_IMPORT_NAMESPACE = "imports"
_JOURNAL_NAME = "import-state.json"
_JOURNAL_FIELDS = {
    "import_id",
    "artifact_id",
    "relative_destination",
    "phase",
    "created_at",
    "updated_at",
}
_STAGING_MAX_AGE = timedelta(hours=24)
_ALLOWED_FILES = REQUIRED_FILES | {
    "tokenizer.json",
    "tokenizer_config.json",
    "chat_template.jinja",
    "special_tokens_map.json",
    "added_tokens.json",
    "generation_config.json",
    "preprocessor_config.json",
    "README.md",
}
_QWEN_TARGET_MODULES = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}
_TENSOR_NAME = re.compile(
    r"^(?P<prefix>base_model\.model\.model\.(?:language_model\.)?"
    r"layers\.(?:0|[1-9][0-9]*)\.(?P<block>self_attn|mlp))\."
    r"(?P<module>q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)\."
    r"lora_(?P<side>A|B)\.weight$"
)
_ALLOWED_TENSOR_DTYPES = {"F16", "BF16", "F32"}
_ARCHIVE_SUFFIXES = {
    "zip": ".zip",
    "tar": ".tar",
    "tar.gz": ".tar.gz",
}


class AdapterImportError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _AdapterImportCommitUncertain(AdapterImportError):
    def __init__(self):
        super().__init__("adapter_import_commit_uncertain")


@dataclass(frozen=True)
class ReceivedAdapterArchive:
    staging_path: Path
    upload_sha256: str
    archive_format: str
    size_bytes: int


@dataclass(frozen=True)
class ValidatedAdapter:
    staging_path: Path
    upload_sha256: str
    archive_format: str
    normalized_tar_path: Path
    normalized_sha256: str
    adapter_stage: str
    base_model_id: str
    rank: int
    peft_type: str
    target_modules: tuple[str, ...]


@dataclass(frozen=True)
class AdapterImportResult:
    artifact: TrainingArtifact
    deduplicated: bool


@dataclass
class _UploadLifecycle:
    receive_started: bool = False


@dataclass(frozen=True)
class _Entry:
    source: object
    name: str
    size: int
    mode: int
    is_directory: bool


class _DuplicateJsonKey(ValueError):
    pass


def _format_from_name(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".tar.gz"):
        return "tar.gz"
    if lowered.endswith(".tar"):
        return "tar"
    if lowered.endswith(".zip"):
        return "zip"
    raise AdapterImportError("unsupported_archive_format")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _remove_private_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(path)


def _cleanup_directory(path: Path, primary: AdapterImportError | None = None) -> None:
    try:
        _remove_private_directory(path)
    except Exception:
        if primary is not None:
            primary.add_note("staging_cleanup_failed")
            return
        raise AdapterImportError("staging_cleanup_failed") from None


def _cleanup_file(path: Path, primary: AdapterImportError | None = None) -> None:
    try:
        if os.path.lexists(path):
            metadata = os.lstat(path)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("not a regular staging file")
            os.unlink(path)
    except Exception:
        if primary is not None:
            primary.add_note("staging_cleanup_failed")
            return
        raise AdapterImportError("staging_cleanup_failed") from None


def receive_adapter_archive(
    source_path: Path,
    staging_directory: Path,
) -> ReceivedAdapterArchive:
    source_path = Path(source_path)
    staging_directory = Path(staging_directory)
    archive_format = _format_from_name(source_path.name)
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_descriptor = os.open(source_path, source_flags)
    except OSError:
        raise AdapterImportError("unsafe_archive_source") from None
    created_staging = False
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            raise AdapterImportError("unsafe_archive_source")
        if source_metadata.st_size > MAX_ARCHIVE_BYTES:
            raise AdapterImportError("archive_too_large")
        try:
            os.mkdir(staging_directory, 0o700)
            created_staging = True
        except OSError:
            raise AdapterImportError("staging_unavailable") from None
        staging_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        staging_descriptor = os.open(staging_directory, staging_flags)
        destination_name = "received" + _ARCHIVE_SUFFIXES[archive_format]
        destination_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        destination_descriptor = os.open(
            destination_name,
            destination_flags,
            0o600,
            dir_fd=staging_descriptor,
        )
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            while True:
                chunk = os.read(source_descriptor, _CHUNK_BYTES)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_ARCHIVE_BYTES:
                    raise AdapterImportError("archive_too_large")
                digest.update(chunk)
                _write_all(destination_descriptor, chunk)
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
            os.close(staging_descriptor)
        return ReceivedAdapterArchive(
            staging_path=staging_directory / destination_name,
            upload_sha256=digest.hexdigest(),
            archive_format=archive_format,
            size_bytes=size_bytes,
        )
    except AdapterImportError as error:
        if created_staging:
            _cleanup_directory(staging_directory, error)
        raise
    except Exception:
        error = AdapterImportError("archive_receive_failed")
        if created_staging:
            _cleanup_directory(staging_directory, error)
        raise error from None
    finally:
        os.close(source_descriptor)


def _safe_parts(name: str, *, directory: bool) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise AdapterImportError("unsafe_archive_entry")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise AdapterImportError("unsafe_archive_entry")
    candidate = name[:-1] if directory and name.endswith("/") else name
    if not candidate or candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        raise AdapterImportError("unsafe_archive_entry")
    raw_parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise AdapterImportError("unsafe_archive_entry")
    path = PurePosixPath(candidate)
    if path.is_absolute() or tuple(path.parts) != tuple(raw_parts) or len(raw_parts) > 2:
        raise AdapterImportError("unsafe_archive_entry")
    return tuple(raw_parts)


def _forbidden_component(parts: tuple[str, ...]) -> bool:
    return any(
        part.casefold() in {"checkpoint", "checkpoints"}
        or part.casefold().startswith("checkpoint-")
        for part in parts
    )


def _plan_entries(entries: list[_Entry]) -> list[tuple[_Entry, str]]:
    files: list[tuple[_Entry, tuple[str, ...]]] = []
    directories: list[tuple[str, ...]] = []
    raw_names: set[str] = set()
    expanded_size = 0
    for entry in entries:
        parts = _safe_parts(entry.name, directory=entry.is_directory)
        if _forbidden_component(parts):
            raise AdapterImportError("unsupported_adapter_file")
        if entry.is_directory:
            directories.append(parts)
            continue
        if entry.size < 0 or entry.mode & 0o111:
            raise AdapterImportError("unsafe_archive_entry")
        folded = "/".join(parts).casefold()
        if folded in raw_names:
            raise AdapterImportError("duplicate_archive_entry")
        raw_names.add(folded)
        files.append((entry, parts))
        expanded_size += entry.size
        if len(files) > MAX_FILES:
            raise AdapterImportError("too_many_archive_files")
        if expanded_size > MAX_EXPANDED_BYTES:
            raise AdapterImportError("expanded_archive_too_large")
    if not files:
        raise AdapterImportError("invalid_archive_layout")

    depths = {len(parts) for _entry, parts in files}
    if depths == {1}:
        outer_directory = None
    elif depths == {2} and len({parts[0].casefold() for _entry, parts in files}) == 1:
        outer_directory = files[0][1][0]
    else:
        raise AdapterImportError("invalid_archive_layout")

    if outer_directory is None:
        if directories:
            raise AdapterImportError("invalid_archive_layout")
    elif any(parts != (outer_directory,) for parts in directories):
        raise AdapterImportError("invalid_archive_layout")

    planned: list[tuple[_Entry, str]] = []
    normalized_names: set[str] = set()
    for entry, parts in files:
        if outer_directory is not None and parts[0] != outer_directory:
            raise AdapterImportError("case_conflicting_archive_entry")
        normalized = parts[-1]
        folded = normalized.casefold()
        if folded in normalized_names:
            raise AdapterImportError("duplicate_archive_entry")
        normalized_names.add(folded)
        if normalized not in _ALLOWED_FILES:
            raise AdapterImportError("unsupported_adapter_file")
        metadata_limit = (
            MAX_TOKENIZER_JSON_BYTES
            if normalized == "tokenizer.json"
            else MAX_METADATA_BYTES
        )
        if normalized != "adapter_model.safetensors" and entry.size > metadata_limit:
            raise AdapterImportError("metadata_file_too_large")
        planned.append((entry, normalized))
    if not REQUIRED_FILES.issubset({name for _entry, name in planned}):
        raise AdapterImportError("required_adapter_file_missing")
    return planned


def _copy_member(
    stream: BinaryIO,
    destination_descriptor: int,
    name: str,
    expected_size: int,
    expanded_so_far: int,
) -> int:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=destination_descriptor)
    copied = 0
    try:
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > expected_size or expanded_so_far + copied > MAX_EXPANDED_BYTES:
                raise AdapterImportError("expanded_archive_too_large")
            _write_all(descriptor, chunk)
        if copied != expected_size:
            raise AdapterImportError("invalid_archive")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return expanded_so_far + copied


def _zip_entries(archive: zipfile.ZipFile) -> list[_Entry]:
    entries = []
    for info in archive.infolist():
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir() or file_type == stat.S_IFDIR
        if info.flag_bits & 0x1:
            raise AdapterImportError("unsafe_archive_entry")
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise AdapterImportError("unsafe_archive_entry")
        entries.append(_Entry(info, info.filename, info.file_size, mode, is_directory))
    return entries


def _zip_preflight_member_count(archive_path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(archive_path, flags)
    try:
        size = os.fstat(descriptor).st_size
        tail_size = min(size, 22 + 65535)
        tail_base = size - tail_size
        tail = os.pread(descriptor, tail_size, tail_base)
        candidates = []
        for offset in range(max(0, len(tail) - 22 + 1)):
            if tail[offset:offset + 4] != b"PK\x05\x06":
                continue
            if offset + 22 > len(tail):
                continue
            comment_length = struct.unpack_from("<H", tail, offset + 20)[0]
            if offset + 22 + comment_length == len(tail):
                candidates.append(offset)
        if len(candidates) != 1:
            raise AdapterImportError("invalid_archive")
        eocd_offset = candidates[0]
        fields = struct.unpack_from("<4s4H2LH", tail, eocd_offset)
        (
            _signature,
            disk,
            central_disk,
            disk_entries,
            total_entries,
            central_size,
            central_offset,
            _comment_length,
        ) = fields
        if (
            disk != 0
            or central_disk != 0
            or disk_entries != total_entries
            or total_entries == 0xFFFF
            or central_size == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF
        ):
            raise AdapterImportError("invalid_archive")
        if total_entries > MAX_ARCHIVE_MEMBERS:
            raise AdapterImportError("too_many_archive_members")
        absolute_eocd = tail_base + eocd_offset
        central_end = central_offset + central_size
        if central_end != absolute_eocd or central_offset > absolute_eocd:
            raise AdapterImportError("invalid_archive")

        parsed = 0
        position = central_offset
        while position < central_end:
            if parsed >= MAX_ARCHIVE_MEMBERS:
                raise AdapterImportError("too_many_archive_members")
            fixed = os.pread(descriptor, 46, position)
            if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
                raise AdapterImportError("invalid_archive")
            header = struct.unpack("<4s6H3L5H2L", fixed)
            compressed_size = header[8]
            uncompressed_size = header[9]
            name_length = header[10]
            extra_length = header[11]
            comment_length = header[12]
            disk_start = header[13]
            local_offset = header[16]
            record_length = 46 + name_length + extra_length + comment_length
            if (
                name_length == 0
                or position + record_length > central_end
                or disk_start != 0
                or compressed_size == 0xFFFFFFFF
                or uncompressed_size == 0xFFFFFFFF
                or local_offset == 0xFFFFFFFF
                or local_offset + 30 > central_offset
            ):
                raise AdapterImportError("invalid_archive")
            extra = os.pread(descriptor, extra_length, position + 46 + name_length)
            if len(extra) != extra_length:
                raise AdapterImportError("invalid_archive")
            extra_position = 0
            while extra_position < len(extra):
                if extra_position + 4 > len(extra):
                    raise AdapterImportError("invalid_archive")
                extra_id, data_length = struct.unpack_from("<HH", extra, extra_position)
                extra_position += 4
                if extra_position + data_length > len(extra) or extra_id == 0x0001:
                    raise AdapterImportError("invalid_archive")
                extra_position += data_length
            local = os.pread(descriptor, 30, local_offset)
            if len(local) != 30 or local[:4] != b"PK\x03\x04":
                raise AdapterImportError("invalid_archive")
            local_name_length, local_extra_length = struct.unpack_from("<HH", local, 26)
            data_offset = local_offset + 30 + local_name_length + local_extra_length
            if data_offset + compressed_size > central_offset:
                raise AdapterImportError("invalid_archive")
            parsed += 1
            position += record_length
        if position != central_end or parsed != disk_entries or parsed != total_entries:
            raise AdapterImportError("invalid_archive")
        return parsed
    except AdapterImportError:
        raise
    except Exception:
        raise AdapterImportError("invalid_archive") from None
    finally:
        os.close(descriptor)


def _extract_zip(archive_path: Path, destination_descriptor: int) -> None:
    try:
        expected_members = _zip_preflight_member_count(archive_path)
        with zipfile.ZipFile(archive_path, "r") as archive:
            entries = _zip_entries(archive)
            if len(entries) != expected_members or len(entries) > MAX_ARCHIVE_MEMBERS:
                raise AdapterImportError("invalid_archive")
            planned = _plan_entries(entries)
            expanded = 0
            for entry, normalized in planned:
                with archive.open(entry.source, "r") as stream:
                    expanded = _copy_member(
                        stream,
                        destination_descriptor,
                        normalized,
                        entry.size,
                        expanded,
                    )
    except AdapterImportError:
        raise
    except Exception:
        raise AdapterImportError("invalid_archive") from None


def _tar_entries(archive: tarfile.TarFile) -> list[_Entry]:
    entries = []
    for member in archive:
        if len(entries) >= MAX_ARCHIVE_MEMBERS:
            raise AdapterImportError("too_many_archive_members")
        if not member.isdir() and not member.isreg():
            raise AdapterImportError("unsafe_archive_entry")
        entries.append(_Entry(member, member.name, member.size, member.mode, member.isdir()))
    return entries


def _extract_tar(
    archive_path: Path,
    archive_format: str,
    destination_descriptor: int,
) -> None:
    mode = "r:gz" if archive_format == "tar.gz" else "r:"
    try:
        with tarfile.open(archive_path, mode) as archive:
            planned = _plan_entries(_tar_entries(archive))
            expanded = 0
            for entry, normalized in planned:
                stream = archive.extractfile(entry.source)
                if stream is None:
                    raise AdapterImportError("invalid_archive")
                with stream:
                    expanded = _copy_member(
                        stream,
                        destination_descriptor,
                        normalized,
                        entry.size,
                        expanded,
                    )
    except AdapterImportError:
        raise
    except Exception:
        raise AdapterImportError("invalid_archive") from None


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _load_json(path: Path, error_code: str):
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonKey):
        raise AdapterImportError(error_code) from None


def _finite_number(value, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def _validate_config(extracted: Path) -> tuple[dict, int, tuple[str, ...]]:
    config = _load_json(extracted / "adapter_config.json", "invalid_adapter_config")
    if not isinstance(config, dict):
        raise AdapterImportError("invalid_adapter_config")
    rank = config.get("r")
    alpha = config.get("lora_alpha")
    dropout = config.get("lora_dropout")
    target_modules = config.get("target_modules")
    remote_fields = ("auto_mapping", "modules_to_save", "target_parameters", "megatron_config")
    valid = (
        config.get("peft_type") == "LORA"
        and config.get("task_type") == "CAUSAL_LM"
        and isinstance(rank, int)
        and not isinstance(rank, bool)
        and 1 <= rank <= 1024
        and _finite_number(alpha, 1, 65536)
        and _finite_number(dropout, 0, 1)
        and isinstance(target_modules, list)
        and 1 <= len(target_modules) <= 32
        and all(isinstance(module, str) and module in _QWEN_TARGET_MODULES for module in target_modules)
        and len(set(target_modules)) == len(target_modules)
        and all(config.get(field) is None for field in remote_fields)
        and config.get("trust_remote_code") not in {True, 1}
    )
    if not valid:
        raise AdapterImportError("invalid_adapter_config")
    config["base_model_name_or_path"] = NORMALIZED_BASE_MODEL
    return config, rank, tuple(sorted(target_modules))


def _validate_optional_json(extracted: Path) -> None:
    for name in sorted(_ALLOWED_FILES):
        if name == "adapter_config.json" or not name.endswith(".json"):
            continue
        path = extracted / name
        if path.exists():
            _load_json(path, "invalid_json_metadata")


def _validate_safetensors(extracted: Path, rank: int, target_modules: tuple[str, ...]) -> None:
    path = extracted / "adapter_model.safetensors"
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise AdapterImportError("invalid_safetensors")
        with safe_open(str(path), framework="np") as weights:
            keys = list(weights.keys())
            if not keys or len(keys) > 10000:
                raise AdapterImportError("invalid_safetensors")
            pairs: dict[str, dict[str, tuple[int, ...]]] = {}
            seen_modules = set()
            for key in keys:
                match = _TENSOR_NAME.fullmatch(key)
                if match is None or match.group("module") not in target_modules:
                    raise AdapterImportError("invalid_safetensors")
                module = match.group("module")
                if (
                    match.group("block") == "self_attn"
                    and module not in {"q_proj", "k_proj", "v_proj", "o_proj"}
                ) or (
                    match.group("block") == "mlp"
                    and module not in {"gate_proj", "up_proj", "down_proj"}
                ):
                    raise AdapterImportError("invalid_safetensors")
                tensor_slice = weights.get_slice(key)
                shape = tuple(tensor_slice.get_shape())
                if (
                    tensor_slice.get_dtype() not in _ALLOWED_TENSOR_DTYPES
                    or len(shape) != 2
                    or any(not isinstance(size, int) or size <= 0 or size > 1048576 for size in shape)
                    or math.prod(shape) > 2**31
                ):
                    raise AdapterImportError("invalid_safetensors")
                if match.group("side") == "A" and shape[0] != rank:
                    raise AdapterImportError("invalid_safetensors")
                if match.group("side") == "B" and shape[1] != rank:
                    raise AdapterImportError("invalid_safetensors")
                pair_key = match.group("prefix") + "." + match.group("module")
                pair = pairs.setdefault(pair_key, {})
                pair[match.group("side")] = shape
                seen_modules.add(match.group("module"))
            if any(set(pair) != {"A", "B"} for pair in pairs.values()):
                raise AdapterImportError("invalid_safetensors")
            if seen_modules != set(target_modules):
                raise AdapterImportError("invalid_safetensors")
    except AdapterImportError:
        raise
    except Exception:
        raise AdapterImportError("invalid_safetensors") from None


def _normalized_config_bytes(config: dict) -> bytes:
    return (json.dumps(config, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _build_normalized_tar(extracted: Path, config: dict, destination: Path) -> str:
    temporary = destination.with_name(".final_adapter.tar.tmp")
    if os.path.lexists(destination) or os.path.lexists(temporary):
        raise AdapterImportError("normalized_output_exists")
    try:
        with tarfile.open(temporary, "x", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(path.name for path in extracted.iterdir() if path.name != "README.md"):
                path = extracted / name
                member = tarfile.TarInfo(name)
                member.mode = 0o644
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                if name == "adapter_config.json":
                    payload = _normalized_config_bytes(config)
                    member.size = len(payload)
                    archive.addfile(member, BytesIO(payload))
                else:
                    member.size = path.stat().st_size
                    with path.open("rb") as stream:
                        archive.addfile(member, stream)
        os.replace(temporary, destination)
    except AdapterImportError as error:
        _cleanup_file(temporary, error)
        raise
    except Exception:
        error = AdapterImportError("normalization_failed")
        _cleanup_file(temporary, error)
        raise error from None
    try:
        digest = hashlib.sha256()
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        raise AdapterImportError("normalization_hash_failed") from None


def validate_and_normalize_adapter(
    received: ReceivedAdapterArchive,
    *,
    adapter_stage: str,
    base_model_id: str,
) -> ValidatedAdapter:
    if adapter_stage not in ALLOWED_STAGES:
        raise AdapterImportError("unsupported_adapter_stage")
    if base_model_id != SUPPORTED_BASE_MODEL_ID:
        raise AdapterImportError("unsupported_base_model")
    archive_path = Path(received.staging_path)
    primary_error = None
    normalized_sha256 = None
    rank = None
    target_modules = None
    try:
        archive_metadata = os.lstat(archive_path)
    except OSError:
        raise AdapterImportError("unsafe_archive_source") from None
    if not stat.S_ISREG(archive_metadata.st_mode) or received.archive_format not in _ARCHIVE_SUFFIXES:
        raise AdapterImportError("unsafe_archive_source")

    staging_root = archive_path.parent
    extracted = staging_root / "extracted"
    normalized_tar = staging_root / "final_adapter.tar"
    try:
        os.mkdir(extracted, 0o700)
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        destination_descriptor = os.open(extracted, directory_flags)
        try:
            if received.archive_format == "zip":
                _extract_zip(archive_path, destination_descriptor)
            else:
                _extract_tar(archive_path, received.archive_format, destination_descriptor)
        finally:
            os.close(destination_descriptor)
        config, rank, target_modules = _validate_config(extracted)
        _validate_optional_json(extracted)
        _validate_safetensors(extracted, rank, target_modules)
        normalized_sha256 = _build_normalized_tar(extracted, config, normalized_tar)
    except AdapterImportError as error:
        primary_error = error
    except Exception:
        primary_error = AdapterImportError("invalid_archive")

    if primary_error is not None:
        _cleanup_file(normalized_tar, primary_error)
        _cleanup_directory(extracted, primary_error)
        raise primary_error

    _cleanup_directory(extracted)

    return ValidatedAdapter(
        staging_path=staging_root,
        upload_sha256=received.upload_sha256,
        archive_format=received.archive_format,
        normalized_tar_path=normalized_tar,
        normalized_sha256=normalized_sha256,
        adapter_stage=adapter_stage,
        base_model_id=base_model_id,
        rank=rank,
        peft_type="LORA",
        target_modules=target_modules,
    )


def _trusted_output_root(output_root: str | Path) -> Path:
    path = Path(output_root)
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        raise AdapterImportError("adapter_output_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not resolved.is_dir():
        raise AdapterImportError("adapter_output_unavailable")
    return resolved


def _ensure_private_directory(path: Path, *, exclusive: bool = False) -> None:
    try:
        if exclusive:
            os.mkdir(path, 0o700)
        else:
            os.makedirs(path, mode=0o700, exist_ok=True)
        metadata = os.lstat(path)
    except OSError:
        raise AdapterImportError("adapter_staging_failed") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise AdapterImportError("adapter_staging_failed")


def _journal_payload(
    import_id: str,
    artifact_id: str,
    relative_destination: str,
    phase: str,
    created_at: datetime,
    updated_at: datetime,
) -> dict:
    return {
        "import_id": import_id,
        "artifact_id": artifact_id,
        "relative_destination": relative_destination,
        "phase": phase,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _write_import_journal(staging_directory: Path, payload: dict) -> None:
    if set(payload) != _JOURNAL_FIELDS:
        raise AdapterImportError("adapter_journal_failed")
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(staging_directory, directory_flags)
    temporary_name = ".import-state.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    temporary_descriptor = -1
    try:
        temporary_descriptor = os.open(temporary_name, flags, 0o600, dir_fd=descriptor)
        serialized = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        _write_all(temporary_descriptor, serialized)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.replace(
            temporary_name,
            _JOURNAL_NAME,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        os.fsync(descriptor)
    except Exception:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=descriptor)
        except OSError:
            pass
        raise AdapterImportError("adapter_journal_failed") from None
    finally:
        os.close(descriptor)


async def _close_upload(upload, primary: BaseException | None) -> None:
    try:
        result = upload.close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        if primary is not None and hasattr(primary, "add_note"):
            primary.add_note("upload_close_failed")
            return
        raise AdapterImportError("upload_close_failed") from None


async def _receive_upload(upload, staging_directory: Path) -> ReceivedAdapterArchive:
    descriptor = -1
    primary: BaseException | None = None
    try:
        filename = getattr(upload, "filename", "")
        archive_format = _format_from_name(filename)
        destination = staging_directory / ("received" + _ARCHIVE_SUFFIXES[archive_format])
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(destination, flags, 0o600)
        except OSError:
            raise AdapterImportError("adapter_receive_failed") from None
        digest = hashlib.sha256()
        size_bytes = 0
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise AdapterImportError("adapter_receive_failed")
            size_bytes += len(chunk)
            if size_bytes > MAX_ARCHIVE_BYTES:
                raise AdapterImportError("archive_too_large")
            digest.update(chunk)
            _write_all(descriptor, chunk)
        os.fsync(descriptor)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        finally:
            await _close_upload(upload, primary)
    return ReceivedAdapterArchive(
        staging_path=destination,
        upload_sha256=digest.hexdigest(),
        archive_format=archive_format,
        size_bytes=size_bytes,
    )


def _request_values(request) -> tuple[str, str, str, str | None]:
    name = getattr(request, "name", None)
    adapter_stage = getattr(request, "adapter_stage", None)
    base_model_id = getattr(request, "base_model_id", None)
    description = getattr(request, "description", None)
    if not isinstance(name, str):
        raise AdapterImportError("invalid_adapter_request")
    name = name.strip()
    if (
        not name
        or len(name) > 255
        or adapter_stage not in ALLOWED_STAGES
        or base_model_id != SUPPORTED_BASE_MODEL_ID
        or (description is not None and (not isinstance(description, str) or len(description) > 1000))
    ):
        raise AdapterImportError("invalid_adapter_request")
    return name, adapter_stage, base_model_id, description


def _deduplicated_artifact(db, normalized_sha256: str, adapter_stage: str, base_model_id: str):
    candidates = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.task_id.is_(None),
            TrainingArtifact.artifact_type == "final_adapter",
            TrainingArtifact.sha256 == normalized_sha256,
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .with_for_update()
        .all()
    )
    for artifact in candidates:
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, dict) else {}
        if (
            metadata.get("source") == "uploaded"
            and metadata.get("adapter_stage") == adapter_stage
            and metadata.get("base_model_id") == base_model_id
        ):
            return artifact
    return None


def _safe_rollback(db) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def _commit_or_raise_uncertain(db) -> None:
    try:
        db.commit()
    except Exception:
        _safe_rollback(db)
        raise _AdapterImportCommitUncertain() from None


async def import_adapter(db, upload, request, output_root: str | Path) -> AdapterImportResult:
    lifecycle = _UploadLifecycle()
    primary: BaseException | None = None
    try:
        return await _import_adapter(db, upload, request, output_root, lifecycle)
    except BaseException as error:
        primary = error
        raise
    finally:
        if not lifecycle.receive_started:
            await _close_upload(upload, primary)


async def _import_adapter(
    db,
    upload,
    request,
    output_root: str | Path,
    lifecycle: _UploadLifecycle,
) -> AdapterImportResult:
    name, adapter_stage, base_model_id, description = _request_values(request)
    root = _trusted_output_root(output_root)
    staging_root = root / _STAGING_NAMESPACE
    imports_root = root / _IMPORT_NAMESPACE
    _ensure_private_directory(staging_root)
    _ensure_private_directory(imports_root)
    import_id = str(uuid4())
    artifact_id = str(uuid4())
    relative_destination = f"imports/{artifact_id}/final_adapter.tar"
    staging_directory = staging_root / import_id
    _ensure_private_directory(staging_directory, exclusive=True)
    created_at = datetime.utcnow()
    journal = _journal_payload(
        import_id,
        artifact_id,
        relative_destination,
        "receiving",
        created_at,
        created_at,
    )
    published_directory: Path | None = None
    try:
        _write_import_journal(staging_directory, journal)
        lifecycle.receive_started = True
        received = await _receive_upload(upload, staging_directory)
        validated = validate_and_normalize_adapter(
            received,
            adapter_stage=adapter_stage,
            base_model_id=base_model_id,
        )
        now = datetime.utcnow()
        journal = _journal_payload(
            import_id,
            artifact_id,
            relative_destination,
            "validated",
            created_at,
            now,
        )
        _write_import_journal(staging_directory, journal)

        acquire_artifact_registry_lock(db)
        duplicate = _deduplicated_artifact(
            db,
            validated.normalized_sha256,
            adapter_stage,
            base_model_id,
        )
        if duplicate is not None:
            _commit_or_raise_uncertain(db)
            try:
                _cleanup_directory(staging_directory)
            except AdapterImportError:
                pass
            return AdapterImportResult(artifact=duplicate, deduplicated=True)

        journal = _journal_payload(
            import_id,
            artifact_id,
            relative_destination,
            "publishing",
            created_at,
            datetime.utcnow(),
        )
        _write_import_journal(staging_directory, journal)
        published_directory = imports_root / artifact_id
        _ensure_private_directory(published_directory, exclusive=True)
        destination = published_directory / "final_adapter.tar"
        os.replace(validated.normalized_tar_path, destination)
        journal = _journal_payload(
            import_id,
            artifact_id,
            relative_destination,
            "published",
            created_at,
            datetime.utcnow(),
        )
        _write_import_journal(staging_directory, journal)
        metadata = {
            "source": "uploaded",
            "adapter_stage": adapter_stage,
            "base_model_id": base_model_id,
            "display_name": name,
            "description": description,
            "uploaded_archive_sha256": validated.upload_sha256,
            "peft_type": validated.peft_type,
            "rank": validated.rank,
            "target_modules": list(validated.target_modules),
            "original_archive_format": validated.archive_format,
        }
        artifact = register_artifact(
            db,
            root,
            task_id=None,
            artifact_type="final_adapter",
            relative_path=relative_destination,
            size_bytes=destination.stat().st_size,
            sha256=validated.normalized_sha256,
            metadata_json=metadata,
            artifact_id=artifact_id,
        )
        _commit_or_raise_uncertain(db)
        try:
            _cleanup_directory(staging_directory)
        except AdapterImportError:
            pass
        return AdapterImportResult(artifact=artifact, deduplicated=False)
    except asyncio.CancelledError:
        _safe_rollback(db)
        if published_directory is not None:
            try:
                _cleanup_directory(published_directory)
            except AdapterImportError:
                pass
        _cleanup_directory(staging_directory, asyncio.CancelledError())
        raise
    except _AdapterImportCommitUncertain:
        raise
    except AdapterImportError as error:
        _safe_rollback(db)
        if published_directory is not None:
            _cleanup_directory(published_directory, error)
        _cleanup_directory(staging_directory, error)
        raise
    except Exception:
        error = AdapterImportError("adapter_import_failed")
        _safe_rollback(db)
        if published_directory is not None:
            _cleanup_directory(published_directory, error)
        _cleanup_directory(staging_directory, error)
        raise error from None


def _canonical_uuid(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _read_recovery_journal(directory: Path):
    path = directory / _JOURNAL_NAME
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != _JOURNAL_FIELDS:
            return None
        if payload.get("import_id") != directory.name or not _canonical_uuid(directory.name):
            return None
        artifact_id = payload.get("artifact_id")
        if not _canonical_uuid(artifact_id):
            return None
        expected = f"imports/{artifact_id}/final_adapter.tar"
        if payload.get("relative_destination") != expected:
            return None
        if payload.get("phase") not in {
            "receiving",
            "validated",
            "publishing",
            "published",
        }:
            return None
        updated_at = datetime.fromisoformat(payload.get("updated_at"))
        datetime.fromisoformat(payload.get("created_at"))
        return payload, updated_at
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _remove_published_orphan(root: Path, artifact_id: str) -> bool:
    directory = root / _IMPORT_NAMESPACE / artifact_id
    path = directory / "final_adapter.tar"
    try:
        directory_metadata = os.lstat(directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
        directory_metadata.st_mode
    ):
        return False
    try:
        file_metadata = os.lstat(path)
    except FileNotFoundError:
        try:
            os.rmdir(directory)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
    except OSError:
        return False
    if stat.S_ISLNK(file_metadata.st_mode) or not stat.S_ISREG(
        file_metadata.st_mode
    ):
        return False
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    try:
        os.rmdir(directory)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def reconcile_adapter_imports(db, output_root: str | Path, now: datetime) -> None:
    root = _trusted_output_root(output_root)
    acquire_artifact_registry_lock(db)
    active_artifacts = (
        db.query(TrainingArtifact)
        .filter(
            TrainingArtifact.task_id.is_(None),
            TrainingArtifact.artifact_type == "final_adapter",
            TrainingArtifact.deleted_at.is_(None),
            TrainingArtifact.deletion_state.is_(None),
        )
        .with_for_update()
        .all()
    )
    active = {
        artifact.relative_path: artifact
        for artifact in active_artifacts
        if isinstance(artifact.metadata_json, dict)
        and artifact.metadata_json.get("source") == "uploaded"
    }
    staging_root = root / _STAGING_NAMESPACE
    try:
        staging_metadata = os.lstat(staging_root)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(staging_metadata.st_mode) or not stat.S_ISDIR(staging_metadata.st_mode):
        raise AdapterImportError("adapter_recovery_failed")
    for entry in os.scandir(staging_root):
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        directory = Path(entry.path)
        parsed = _read_recovery_journal(directory)
        if parsed is None:
            continue
        payload, updated_at = parsed
        relative_destination = payload["relative_destination"]
        if payload["phase"] in {"publishing", "published"}:
            if (
                relative_destination not in active
                and not _remove_published_orphan(root, payload["artifact_id"])
            ):
                raise AdapterImportError("adapter_recovery_cleanup_failed")
            try:
                _cleanup_directory(directory)
            except AdapterImportError:
                pass
        elif now - updated_at > _STAGING_MAX_AGE:
            try:
                _cleanup_directory(directory)
            except AdapterImportError:
                pass
