import asyncio
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import struct
import tarfile
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from safetensors.numpy import save as save_safetensors
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.training_task import TrainingArtifact, TrainingTask
from app.services import adapter_import_service as service
from app.services.adapter_import_service import (
    AdapterImportError,
    ReceivedAdapterArchive,
    ValidatedAdapter,
    receive_adapter_archive,
    validate_and_normalize_adapter,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = BACKEND_ROOT / "requirements.txt"
PROD_REQUIREMENTS = BACKEND_ROOT / "requirements.prod.txt"


class _ChunkedUpload:
    def __init__(self, filename: str, payload: bytes, *, chunk_size: int = 17, cancel_after=None):
        self.filename = filename
        self.payload = payload
        self.chunk_size = chunk_size
        self.cancel_after = cancel_after
        self.offset = 0
        self.read_sizes = []
        self.closed = False

    async def read(self, size: int):
        self.read_sizes.append(size)
        await asyncio.sleep(0)
        if self.cancel_after is not None and self.offset >= self.cancel_after:
            raise asyncio.CancelledError
        chunk = self.payload[self.offset:self.offset + min(size, self.chunk_size)]
        self.offset += len(chunk)
        return chunk

    async def close(self):
        self.closed = True


@pytest.fixture
def artifact_db():
    engine = create_engine("sqlite:///:memory:")
    TrainingTask.__table__.create(engine)
    TrainingArtifact.__table__.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _config(**overrides) -> bytes:
    payload = {
        "base_model_name_or_path": "/old/machine/models/Qwen3.5-9B",
        "bias": "none",
        "lora_alpha": 4,
        "lora_dropout": 0.05,
        "peft_type": "LORA",
        "r": 2,
        "target_modules": ["q_proj"],
        "task_type": "CAUSAL_LM",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _weights(*, name_prefix="base_model.model.model.layers.0.self_attn.q_proj", dtype=np.float32):
    return save_safetensors({
        f"{name_prefix}.lora_A.weight": np.zeros((2, 3), dtype=dtype),
        f"{name_prefix}.lora_B.weight": np.zeros((4, 2), dtype=dtype),
    })


def _valid_entries(*, prefix="", config=None, weights=None):
    return [
        (f"{prefix}adapter_config.json", config or _config()),
        (f"{prefix}adapter_model.safetensors", weights or _weights()),
        (f"{prefix}tokenizer_config.json", b'{"padding_side":"right"}'),
        (f"{prefix}README.md", b"private path: /old/machine/models\n"),
    ]


def _write_archive(path: Path, archive_format: str, entries) -> Path:
    if archive_format == "zip":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
        return path
    mode = "w:gz" if archive_format == "tar.gz" else "w"
    with tarfile.open(path, mode) as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, BytesIO(content))
    return path


def _source_archive(tmp_path: Path, archive_format: str, entries, name="adapter") -> Path:
    suffix = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz"}[archive_format]
    return _write_archive(tmp_path / f"{name}{suffix}", archive_format, entries)


def _receive_and_validate(
    tmp_path: Path,
    archive_format: str,
    entries,
    *,
    name="adapter",
    stage="sft",
    base_model_id=service.SUPPORTED_BASE_MODEL_ID,
):
    source = _source_archive(tmp_path, archive_format, entries, name=name)
    received = receive_adapter_archive(source, tmp_path / f"staging-{name}")
    validated = validate_and_normalize_adapter(
        received,
        adapter_stage=stage,
        base_model_id=base_model_id,
    )
    return received, validated


@pytest.mark.parametrize("archive_format", ["zip", "tar", "tar.gz"])
@pytest.mark.parametrize("prefix", ["", "best_adapter/"])
def test_accepts_flat_or_one_outer_directory_and_normalizes(archive_format, prefix, tmp_path):
    received, validated = _receive_and_validate(
        tmp_path,
        archive_format,
        _valid_entries(prefix=prefix),
        name=f"{archive_format.replace('.', '-')}-{bool(prefix)}",
    )

    assert isinstance(received, ReceivedAdapterArchive)
    assert isinstance(validated, ValidatedAdapter)
    assert received.archive_format == archive_format
    assert received.upload_sha256 == hashlib.sha256(received.staging_path.read_bytes()).hexdigest()
    assert validated.adapter_stage == "sft"
    assert validated.base_model_id == service.SUPPORTED_BASE_MODEL_ID
    assert validated.peft_type == "LORA"
    assert validated.rank == 2
    assert validated.target_modules == ("q_proj",)
    assert validated.normalized_sha256 == hashlib.sha256(
        validated.normalized_tar_path.read_bytes()
    ).hexdigest()
    with tarfile.open(validated.normalized_tar_path, "r:") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == [
            "adapter_config.json",
            "adapter_model.safetensors",
            "tokenizer_config.json",
        ]
        assert all(
            (member.uid, member.gid, member.uname, member.gname, member.mtime, member.mode)
            == (0, 0, "", "", 0, 0o644)
            for member in members
        )
        config = json.load(archive.extractfile("adapter_config.json"))
        assert config["base_model_name_or_path"] == service.NORMALIZED_BASE_MODEL
        assert config["bias"] == "none"


def test_normalized_tar_is_deterministic_across_archive_formats_and_metadata(tmp_path):
    outputs = []
    for index, archive_format in enumerate(("zip", "tar", "tar.gz")):
        entries = list(reversed(_valid_entries(prefix="bundle/" if index else "")))
        _received, validated = _receive_and_validate(
            tmp_path,
            archive_format,
            entries,
            name=f"deterministic-{index}",
            stage="cpt",
        )
        outputs.append((validated.normalized_sha256, validated.normalized_tar_path.read_bytes()))
    assert len({digest for digest, _content in outputs}) == 1
    assert outputs[0][1] == outputs[1][1] == outputs[2][1]


def test_receive_streams_to_exclusive_staging_and_enforces_format_and_size(tmp_path, monkeypatch):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    expected = source.read_bytes()
    received = receive_adapter_archive(source, tmp_path / "received")
    assert received.size_bytes == len(expected)
    assert received.upload_sha256 == hashlib.sha256(expected).hexdigest()
    assert received.staging_path.read_bytes() == expected
    with pytest.raises(AdapterImportError):
        receive_adapter_archive(source, tmp_path / "received")

    unsupported = tmp_path / "adapter.tgz"
    unsupported.write_bytes(expected)
    with pytest.raises(AdapterImportError):
        receive_adapter_archive(unsupported, tmp_path / "unsupported")

    monkeypatch.setattr(service, "MAX_ARCHIVE_BYTES", len(expected) - 1)
    with pytest.raises(AdapterImportError):
        receive_adapter_archive(source, tmp_path / "too-large")


def test_receive_rejects_symlink_source_without_disclosing_path(tmp_path):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    linked = tmp_path / "private-adapter.zip"
    linked.symlink_to(source)
    with pytest.raises(AdapterImportError) as raised:
        receive_adapter_archive(linked, tmp_path / "linked-staging")
    assert raised.value.code == "unsafe_archive_source"
    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../adapter_config.json",
        "/adapter_config.json",
        "C:/adapter_config.json",
        "outer\\adapter_config.json",
        "outer/./adapter_config.json",
        "outer//adapter_config.json",
        "outer/deeper/adapter_config.json",
    ],
)
def test_rejects_unsafe_or_too_deep_paths(unsafe_name, tmp_path):
    entries = _valid_entries()
    entries[0] = (unsafe_name, entries[0][1])
    source = _source_archive(tmp_path, "zip", entries)
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )


@pytest.mark.parametrize(
    "member_type",
    [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE],
)
def test_rejects_tar_links_fifo_and_devices(member_type, tmp_path):
    source = tmp_path / f"special-{member_type!r}.tar"
    with tarfile.open(source, "w") as archive:
        for name, content in _valid_entries():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
        special = tarfile.TarInfo("unsafe-entry")
        special.type = member_type
        special.linkname = "adapter_config.json"
        special.devmajor = 1
        special.devminor = 1
        archive.addfile(special)
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )


def test_rejects_zip_symlink_and_executable_mode(tmp_path):
    for name, mode in (("unsafe-link", stat.S_IFLNK | 0o777), ("tokenizer.json", stat.S_IFREG | 0o755)):
        source = tmp_path / f"mode-{mode}.zip"
        with zipfile.ZipFile(source, "w") as archive:
            for entry_name, content in _valid_entries():
                archive.writestr(entry_name, content)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, b"adapter_config.json" if stat.S_ISLNK(mode) else b"{}")
        received = receive_adapter_archive(source, tmp_path / f"staging-{mode}")
        with pytest.raises(AdapterImportError):
            validate_and_normalize_adapter(
                received,
                adapter_stage="sft",
                base_model_id=service.SUPPORTED_BASE_MODEL_ID,
            )


def test_rejects_duplicate_case_conflicting_and_multiple_root_paths(tmp_path):
    matrices = [
        _valid_entries() + [("adapter_config.json", _config())],
        _valid_entries() + [("Tokenizer_Config.json", b"{}")],
        [
            ("first/adapter_config.json", _config()),
            ("second/adapter_model.safetensors", _weights()),
        ],
    ]
    for index, entries in enumerate(matrices):
        source = _source_archive(tmp_path, "zip", entries, name=f"duplicate-{index}")
        received = receive_adapter_archive(source, tmp_path / f"staging-{index}")
        with pytest.raises(AdapterImportError):
            validate_and_normalize_adapter(
                received,
                adapter_stage="sft",
                base_model_id=service.SUPPORTED_BASE_MODEL_ID,
            )


def test_rejects_file_count_expanded_size_and_metadata_size_limits(tmp_path, monkeypatch):
    too_many = [(f"file-{index}.json", b"{}") for index in range(65)]
    source = _source_archive(tmp_path, "zip", too_many, name="too-many")
    received = receive_adapter_archive(source, tmp_path / "staging-many")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )

    monkeypatch.setattr(service, "MAX_EXPANDED_BYTES", 32)
    source = _source_archive(tmp_path, "zip", _valid_entries(), name="expanded")
    received = receive_adapter_archive(source, tmp_path / "staging-expanded")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )

    monkeypatch.setattr(service, "MAX_EXPANDED_BYTES", 2 * 1024**3)
    monkeypatch.setattr(service, "MAX_METADATA_BYTES", 8)
    source = _source_archive(tmp_path, "zip", _valid_entries(), name="metadata")
    received = receive_adapter_archive(source, tmp_path / "staging-metadata")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )


def test_accepts_valid_tokenizer_json_between_4mib_and_64mib(tmp_path):
    tokenizer = b" " * (service.MAX_METADATA_BYTES + 1) + b"{}"
    entries = _valid_entries() + [("tokenizer.json", tokenizer)]

    _received, validated = _receive_and_validate(
        tmp_path,
        "zip",
        entries,
        name="large-valid-tokenizer",
    )

    with tarfile.open(validated.normalized_tar_path, "r:") as archive:
        member = archive.getmember("tokenizer.json")
        assert member.size == len(tokenizer)


def test_rejects_tokenizer_json_over_64mib_during_entry_planning():
    entries = [
        service._Entry(None, "adapter_config.json", 2, 0o644, False),
        service._Entry(None, "adapter_model.safetensors", 1, 0o644, False),
        service._Entry(None, "tokenizer.json", 64 * 1024**2 + 1, 0o644, False),
    ]

    with pytest.raises(AdapterImportError) as error:
        service._plan_entries(entries)

    assert error.value.code == "metadata_file_too_large"


def test_other_metadata_remains_limited_to_4mib_during_entry_planning():
    entries = [
        service._Entry(None, "adapter_config.json", 2, 0o644, False),
        service._Entry(None, "adapter_model.safetensors", 1, 0o644, False),
        service._Entry(
            None,
            "tokenizer_config.json",
            service.MAX_METADATA_BYTES + 1,
            0o644,
            False,
        ),
    ]

    with pytest.raises(AdapterImportError) as error:
        service._plan_entries(entries)

    assert error.value.code == "metadata_file_too_large"


def test_zip_preflight_rejects_total_member_bomb_before_zipfile(tmp_path, monkeypatch):
    source = tmp_path / "directory-bomb.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for name, content in _valid_entries():
            archive.writestr(name, content)
        for index in range(129):
            info = zipfile.ZipInfo(f"empty-{index}/")
            info.create_system = 3
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
    received = receive_adapter_archive(source, tmp_path / "staging")

    def forbidden_zipfile(*_args, **_kwargs):
        raise AssertionError("ZipFile must not be constructed for an oversized directory")

    monkeypatch.setattr(service.zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "too_many_archive_members"


def test_zip_preflight_rejects_zip64_sentinel_without_locator(tmp_path, monkeypatch):
    source = tmp_path / "sentinel.zip"
    source.write_bytes(
        struct.pack(
            "<4s4H2LH",
            b"PK\x05\x06",
            0,
            0,
            0xFFFF,
            0xFFFF,
            0,
            0,
            0,
        )
    )
    received = receive_adapter_archive(source, tmp_path / "staging")

    constructor_called = False

    def forbidden_zipfile(*_args, **_kwargs):
        nonlocal constructor_called
        constructor_called = True
        raise AssertionError("ZipFile must not parse an invalid Zip64 sentinel")

    monkeypatch.setattr(service.zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_archive"
    assert not constructor_called


@pytest.mark.parametrize(
    "attack",
    ["patched-count", "forged-comment-eocd", "malformed-central", "trailing-central", "multi-disk"],
)
def test_zip_preflight_fully_validates_central_directory_before_zipfile(
    attack,
    tmp_path,
    monkeypatch,
):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    payload = bytearray(source.read_bytes())
    eocd = payload.rfind(b"PK\x05\x06")
    assert eocd >= 0
    if attack == "patched-count":
        struct.pack_into("<HH", payload, eocd + 8, 1, 1)
    elif attack == "forged-comment-eocd":
        forged_offset = len(payload)
        struct.pack_into("<H", payload, eocd + 20, 22)
        payload.extend(
            struct.pack(
                "<4s4H2LH",
                b"PK\x05\x06",
                0,
                0,
                0,
                0,
                0,
                forged_offset,
                0,
            )
        )
    elif attack == "malformed-central":
        central_offset = struct.unpack_from("<L", payload, eocd + 16)[0]
        payload[central_offset:central_offset + 4] = b"JUNK"
    elif attack == "trailing-central":
        central_size = struct.unpack_from("<L", payload, eocd + 12)[0]
        payload[eocd:eocd] = b"JUNK"
        eocd += 4
        struct.pack_into("<L", payload, eocd + 12, central_size + 4)
    else:
        struct.pack_into("<H", payload, eocd + 4, 1)
    source.write_bytes(payload)
    received = receive_adapter_archive(source, tmp_path / "staging")
    constructor_called = False

    def forbidden_zipfile(*_args, **_kwargs):
        nonlocal constructor_called
        constructor_called = True
        raise AssertionError("ZipFile must not parse an invalid central directory")

    monkeypatch.setattr(service.zipfile, "ZipFile", forbidden_zipfile)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_archive"
    assert not constructor_called


def test_tar_directory_bomb_stops_incrementally_without_getmembers(tmp_path, monkeypatch):
    source = tmp_path / "directory-bomb.tar"
    with tarfile.open(source, "w") as archive:
        for name, content in _valid_entries():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
        for index in range(129):
            info = tarfile.TarInfo(f"empty-{index}/")
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
    received = receive_adapter_archive(source, tmp_path / "staging")

    def forbidden_getmembers(_archive):
        raise AssertionError("TAR members must be enumerated incrementally")

    monkeypatch.setattr(service.tarfile.TarFile, "getmembers", forbidden_getmembers)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "too_many_archive_members"


@pytest.mark.parametrize(
    ("archive_format", "prefix", "directory_name"),
    [
        ("zip", "", "unrelated/"),
        ("tar", "bundle/", "unrelated/"),
        ("zip", "", "checkpoint-1/"),
        ("tar.gz", "checkpoint-1/", "checkpoint-1/"),
    ],
)
def test_directory_entries_participate_in_layout_and_checkpoint_rejection(
    archive_format,
    prefix,
    directory_name,
    tmp_path,
):
    source = tmp_path / ("adapter." + archive_format)
    entries = _valid_entries(prefix=prefix)
    if archive_format == "zip":
        with zipfile.ZipFile(source, "w") as archive:
            for name, content in entries:
                archive.writestr(name, content)
            info = zipfile.ZipInfo(directory_name)
            info.create_system = 3
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
    else:
        mode = "w:gz" if archive_format == "tar.gz" else "w"
        with tarfile.open(source, mode) as archive:
            for name, content in entries:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, BytesIO(content))
            info = tarfile.TarInfo(directory_name)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )

@pytest.mark.parametrize(
    "unknown_name",
    ["train.py", "pytorch_model.bin", "optimizer.pt", "checkpoint-1.json"],
)
def test_rejects_scripts_pickle_bin_checkpoints_and_unknown_files(unknown_name, tmp_path):
    source = _source_archive(
        tmp_path,
        "zip",
        _valid_entries() + [(unknown_name, b"unsafe")],
    )
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )


@pytest.mark.parametrize(
    "config_bytes",
    [
        b'{"peft_type":"LORA","peft_type":"LORA"}',
        b"[]",
        _config(peft_type="IA3"),
        _config(task_type="SEQ_CLS"),
        _config(r=True),
        _config(r=0),
        _config(r=1025),
        _config(lora_alpha=0),
        _config(lora_dropout=1.1),
        _config(target_modules=[]),
        _config(target_modules=["q_proj", "evil_proj"]),
        _config(auto_mapping={"base_model_class": "RemoteModel"}),
        _config(modules_to_save=["lm_head"]),
    ],
)
def test_rejects_invalid_or_remote_code_adapter_config(config_bytes, tmp_path):
    source = _source_archive(
        tmp_path,
        "zip",
        _valid_entries(config=config_bytes),
    )
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_adapter_config"
    assert str(tmp_path) not in str(raised.value)


def test_rejects_duplicate_keys_in_optional_json_metadata(tmp_path):
    entries = _valid_entries()
    entries[2] = ("tokenizer_config.json", b'{"a":1,"a":2}')
    source = _source_archive(tmp_path, "zip", entries)
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_json_metadata"


@pytest.mark.parametrize(
    "weights",
    [
        b"not safetensors",
        _weights(name_prefix="untrusted.model.q_proj"),
        _weights(name_prefix="base_model.model.model.layers.0.self_attn.evil_proj"),
        _weights(dtype=np.int64),
        save_safetensors({
            "base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": np.zeros((2, 0), dtype=np.float32),
            "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight": np.zeros((4, 2), dtype=np.float32),
        }),
    ],
)
def test_rejects_invalid_safetensors_header_names_dtypes_and_shapes(weights, tmp_path):
    source = _source_archive(
        tmp_path,
        "zip",
        _valid_entries(weights=weights),
    )
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_safetensors"
    assert str(tmp_path) not in str(raised.value)


@pytest.mark.parametrize(
    ("stage", "base_model_id"),
    [("unknown", service.SUPPORTED_BASE_MODEL_ID), ("sft", "other-model")],
)
def test_rejects_unsupported_declared_stage_or_base_model(stage, base_model_id, tmp_path):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError):
        validate_and_normalize_adapter(
            received,
            adapter_stage=stage,
            base_model_id=base_model_id,
        )


def test_corrupt_parser_errors_are_sanitized_and_partial_outputs_are_removed(tmp_path):
    source = tmp_path / "secret-customer-name.zip"
    source.write_bytes(b"not a zip archive")
    received = receive_adapter_archive(source, tmp_path / "staging")
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_archive"
    assert str(tmp_path) not in str(raised.value)
    assert not (received.staging_path.parent / "extracted").exists()
    assert not (received.staging_path.parent / "final_adapter.tar").exists()


def test_primary_parser_error_is_not_masked_by_cleanup_failure(tmp_path, monkeypatch):
    source = tmp_path / "private-customer.zip"
    source.write_bytes(b"not a zip archive")
    received = receive_adapter_archive(source, tmp_path / "staging")

    def failing_cleanup(path):
        raise OSError(f"private cleanup path: {path}")

    monkeypatch.setattr(service, "_remove_private_directory", failing_cleanup)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "invalid_archive"
    assert getattr(raised.value, "__notes__", []) == ["staging_cleanup_failed"]
    assert str(tmp_path) not in str(raised.value)


def test_primary_receive_error_is_not_masked_by_cleanup_failure(tmp_path, monkeypatch):
    source = _source_archive(tmp_path, "zip", _valid_entries())

    def failing_copy(*_args, **_kwargs):
        raise AdapterImportError("archive_too_large")

    def failing_cleanup(path):
        raise OSError(f"private cleanup path: {path}")

    monkeypatch.setattr(service, "_write_all", failing_copy)
    monkeypatch.setattr(service, "_remove_private_directory", failing_cleanup)
    with pytest.raises(AdapterImportError) as raised:
        receive_adapter_archive(source, tmp_path / "staging")
    assert raised.value.code == "archive_too_large"
    assert getattr(raised.value, "__notes__", []) == ["staging_cleanup_failed"]
    assert str(tmp_path) not in str(raised.value)


def test_cleanup_failure_without_primary_has_stable_error(tmp_path, monkeypatch):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    received = receive_adapter_archive(source, tmp_path / "staging")

    def failing_cleanup(path):
        raise OSError(f"private cleanup path: {path}")

    monkeypatch.setattr(service, "_remove_private_directory", failing_cleanup)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "staging_cleanup_failed"
    assert str(tmp_path) not in str(raised.value)


def test_final_normalized_hash_failure_is_sanitized(tmp_path, monkeypatch):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    received = receive_adapter_archive(source, tmp_path / "staging")

    def failing_sha256(*_args, **_kwargs):
        raise OSError(f"private hash path: {tmp_path}")

    monkeypatch.setattr(service.hashlib, "sha256", failing_sha256)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "normalization_hash_failed"
    assert str(tmp_path) not in str(raised.value)


def test_normalization_failure_is_not_masked_by_temp_unlink_failure(tmp_path, monkeypatch):
    source = _source_archive(tmp_path, "zip", _valid_entries())
    received = receive_adapter_archive(source, tmp_path / "staging")
    real_tar_open = service.tarfile.open
    real_unlink = service.os.unlink

    def failing_tar_open(path, *args, **kwargs):
        if Path(path).name == ".final_adapter.tar.tmp":
            Path(path).write_bytes(b"partial")
            raise OSError(f"private tar path: {path}")
        return real_tar_open(path, *args, **kwargs)

    def failing_temp_unlink(path, *args, **kwargs):
        if str(path).endswith(".final_adapter.tar.tmp"):
            raise OSError(f"private unlink path: {path}")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(service.tarfile, "open", failing_tar_open)
    monkeypatch.setattr(service.os, "unlink", failing_temp_unlink)
    with pytest.raises(AdapterImportError) as raised:
        validate_and_normalize_adapter(
            received,
            adapter_stage="sft",
            base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        )
    assert raised.value.code == "normalization_failed"
    assert getattr(raised.value, "__notes__", []) == ["staging_cleanup_failed"]
    assert str(tmp_path) not in str(raised.value)


def test_source_never_uses_extractall_and_dependencies_pin_safetensors():
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "extractall" not in source
    assert ".read_bytes()" not in source
    assert REQUIREMENTS.read_text(encoding="utf-8").splitlines().count("safetensors==0.7.0") == 1
    assert PROD_REQUIREMENTS.read_text(encoding="utf-8").splitlines().count("safetensors==0.7.0") == 1


def _import_request(stage="sft"):
    return SimpleNamespace(
        name="  Imported Adapter  ",
        adapter_stage=stage,
        base_model_id=service.SUPPORTED_BASE_MODEL_ID,
        description="test adapter",
    )


def _archive_payload(tmp_path: Path) -> bytes:
    return _source_archive(tmp_path, "zip", _valid_entries()).read_bytes()


def test_import_streams_bounded_closes_publishes_and_registers(tmp_path, artifact_db):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    payload = _archive_payload(tmp_path)
    upload = _ChunkedUpload("customer-secret.zip", payload)

    result = asyncio.run(
        service.import_adapter(artifact_db, upload, _import_request(), output_root)
    )

    assert not result.deduplicated
    assert upload.closed
    assert upload.read_sizes and set(upload.read_sizes) == {service.UPLOAD_CHUNK_BYTES}
    artifact = result.artifact
    assert artifact.task_id is None
    assert artifact.artifact_type == "final_adapter"
    assert artifact.relative_path == f"imports/{artifact.id}/final_adapter.tar"
    assert (output_root / artifact.relative_path).is_file()
    assert artifact.sha256 == hashlib.sha256(
        (output_root / artifact.relative_path).read_bytes()
    ).hexdigest()
    assert set(artifact.metadata_json) == {
        "source",
        "adapter_stage",
        "base_model_id",
        "display_name",
        "description",
        "uploaded_archive_sha256",
        "peft_type",
        "rank",
        "target_modules",
        "original_archive_format",
    }
    assert artifact.metadata_json["display_name"] == "Imported Adapter"
    staging_root = output_root / ".adapter-import-staging"
    assert not staging_root.exists() or not list(staging_root.iterdir())


def test_import_size_validation_and_cancellation_close_and_cleanup(tmp_path, artifact_db, monkeypatch):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    payload = _archive_payload(tmp_path)
    monkeypatch.setattr(service, "MAX_ARCHIVE_BYTES", len(payload) - 1)
    too_large = _ChunkedUpload("adapter.zip", payload)
    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(artifact_db, too_large, _import_request(), output_root))
    assert raised.value.code == "archive_too_large"
    assert too_large.closed
    assert not list((output_root / ".adapter-import-staging").iterdir())

    monkeypatch.setattr(service, "MAX_ARCHIVE_BYTES", 1024**3)
    cancelled = _ChunkedUpload("adapter.zip", payload, cancel_after=1)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.import_adapter(artifact_db, cancelled, _import_request(), output_root))
    assert cancelled.closed
    assert not list((output_root / ".adapter-import-staging").iterdir())


def test_import_closes_upload_before_streaming_when_request_or_format_is_invalid(
    tmp_path,
    artifact_db,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    payload = _archive_payload(tmp_path)

    invalid_request = _import_request()
    invalid_request.name = "   "
    request_upload = _ChunkedUpload("adapter.zip", payload)
    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(
            artifact_db,
            request_upload,
            invalid_request,
            output_root,
        ))
    assert raised.value.code == "invalid_adapter_request"
    assert request_upload.closed

    format_upload = _ChunkedUpload("adapter.rar", payload)
    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(
            artifact_db,
            format_upload,
            _import_request(),
            output_root,
        ))
    assert raised.value.code == "unsupported_archive_format"
    assert format_upload.closed
    assert not list((output_root / ".adapter-import-staging").iterdir())


def test_import_validation_and_precommit_database_failures_remove_all_partial_state(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    invalid = _ChunkedUpload("adapter.zip", b"not a zip")
    with pytest.raises(AdapterImportError):
        asyncio.run(service.import_adapter(artifact_db, invalid, _import_request(), output_root))
    assert invalid.closed
    assert not list((output_root / ".adapter-import-staging").iterdir())

    payload = _archive_payload(tmp_path)
    upload = _ChunkedUpload("adapter.zip", payload)
    monkeypatch.setattr(
        service,
        "register_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db secret")),
    )
    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(artifact_db, upload, _import_request(), output_root))
    assert raised.value.code == "adapter_import_failed"
    assert not list((output_root / ".adapter-import-staging").iterdir())
    imports = output_root / "imports"
    assert not imports.exists() or not list(imports.iterdir())


def test_import_commit_failure_preserves_publication_until_reconciliation(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    real_commit = artifact_db.commit
    monkeypatch.setattr(
        artifact_db,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("commit acknowledgement lost")),
    )

    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(
            artifact_db,
            _ChunkedUpload("adapter.zip", _archive_payload(tmp_path)),
            _import_request(),
            output_root,
        ))

    assert raised.value.code == "adapter_import_commit_uncertain"
    staging_directories = list((output_root / ".adapter-import-staging").iterdir())
    assert len(staging_directories) == 1
    journal = json.loads(
        (staging_directories[0] / "import-state.json").read_text(encoding="utf-8")
    )
    published = output_root / journal["relative_destination"]
    assert journal["phase"] == "published" and published.is_file()

    monkeypatch.setattr(artifact_db, "commit", real_commit)
    service.reconcile_adapter_imports(artifact_db, output_root, datetime.utcnow())
    artifact_db.commit()
    assert not published.exists() and not staging_directories[0].exists()


def test_import_commit_that_persists_then_raises_is_kept_by_fresh_reconciliation(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    real_commit = artifact_db.commit

    def persist_then_raise():
        real_commit()
        raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(artifact_db, "commit", persist_then_raise)
    with pytest.raises(AdapterImportError) as raised:
        asyncio.run(service.import_adapter(
            artifact_db,
            _ChunkedUpload("adapter.zip", _archive_payload(tmp_path)),
            _import_request(),
            output_root,
        ))
    assert raised.value.code == "adapter_import_commit_uncertain"

    staging_directory = next((output_root / ".adapter-import-staging").iterdir())
    journal = json.loads(
        (staging_directory / "import-state.json").read_text(encoding="utf-8")
    )
    published = output_root / journal["relative_destination"]
    assert published.is_file()

    fresh_db = sessionmaker(bind=artifact_db.get_bind(), expire_on_commit=False)()
    try:
        service.reconcile_adapter_imports(fresh_db, output_root, datetime.utcnow())
        fresh_db.commit()
        assert fresh_db.query(TrainingArtifact).filter_by(id=journal["artifact_id"]).one()
    finally:
        fresh_db.close()
    assert published.is_file() and not staging_directory.exists()


def test_crash_after_publish_rename_is_reconciled_from_publishing_journal(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    real_replace = service.os.replace

    class SimulatedCrash(BaseException):
        pass

    def crash_after_adapter_rename(source, destination, *args, **kwargs):
        destination = Path(destination)
        if (
            destination.name == "final_adapter.tar"
            and destination.parent.parent == output_root / "imports"
        ):
            staging_directory = next(
                (output_root / ".adapter-import-staging").iterdir()
            )
            journal = json.loads(
                (staging_directory / "import-state.json").read_text(encoding="utf-8")
            )
            artifact_id = Path(destination).parent.name
            assert journal["phase"] == "publishing"
            assert journal["artifact_id"] == artifact_id
            assert journal["relative_destination"] == (
                f"imports/{artifact_id}/final_adapter.tar"
            )
            real_replace(source, destination, *args, **kwargs)
            raise SimulatedCrash
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.os, "replace", crash_after_adapter_rename)
    with pytest.raises(SimulatedCrash):
        asyncio.run(service.import_adapter(
            artifact_db,
            _ChunkedUpload("adapter.zip", _archive_payload(tmp_path)),
            _import_request(),
            output_root,
        ))

    staging_directory = next((output_root / ".adapter-import-staging").iterdir())
    journal = json.loads(
        (staging_directory / "import-state.json").read_text(encoding="utf-8")
    )
    published = output_root / journal["relative_destination"]
    assert published.is_file()

    service.reconcile_adapter_imports(artifact_db, output_root, datetime.utcnow())
    artifact_db.commit()
    assert not published.exists() and not staging_directory.exists()


def test_import_deduplicates_under_registry_lock_but_keeps_stages_separate(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    payload = _archive_payload(tmp_path)
    lock_events = []
    original_lock = service.acquire_artifact_registry_lock

    def record_lock(db):
        lock_events.append("lock")
        return original_lock(db)

    monkeypatch.setattr(service, "acquire_artifact_registry_lock", record_lock)
    first = asyncio.run(service.import_adapter(
        artifact_db, _ChunkedUpload("a.zip", payload), _import_request("sft"), output_root
    ))
    duplicate = asyncio.run(service.import_adapter(
        artifact_db, _ChunkedUpload("b.zip", payload), _import_request("sft"), output_root
    ))
    cpt = asyncio.run(service.import_adapter(
        artifact_db, _ChunkedUpload("c.zip", payload), _import_request("cpt"), output_root
    ))

    assert duplicate.deduplicated
    assert duplicate.artifact.id == first.artifact.id
    assert not cpt.deduplicated and cpt.artifact.id != first.artifact.id
    assert artifact_db.query(TrainingArtifact).count() == 2
    assert len(lock_events) >= 3
    assert len(list((output_root / "imports").glob("*/final_adapter.tar"))) == 2


def test_import_journal_is_atomic_private_and_contains_only_safe_fields(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    payload = _archive_payload(tmp_path)
    captured = {}

    def stop_after_receive(received, **_kwargs):
        journal = received.staging_path.parent / "import-state.json"
        captured.update(json.loads(journal.read_text(encoding="utf-8")))
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
        assert not list(journal.parent.glob("*.tmp"))
        raise AdapterImportError("stop_after_receive")

    monkeypatch.setattr(service, "validate_and_normalize_adapter", stop_after_receive)
    with pytest.raises(AdapterImportError):
        asyncio.run(service.import_adapter(
            artifact_db,
            _ChunkedUpload("private-customer.zip", payload),
            _import_request(),
            output_root,
        ))
    assert set(captured) == {
        "import_id", "artifact_id", "relative_destination", "phase", "created_at", "updated_at"
    }
    assert captured["relative_destination"].startswith("imports/")
    serialized = json.dumps(captured)
    assert "private-customer" not in serialized and str(tmp_path) not in serialized


def _write_recovery_journal(
    output_root: Path,
    *,
    import_id: str,
    artifact_id: str,
    relative_destination: str,
    phase: str,
    updated_at: datetime,
):
    directory = output_root / ".adapter-import-staging" / import_id
    directory.mkdir(parents=True)
    payload = {
        "import_id": import_id,
        "artifact_id": artifact_id,
        "relative_destination": relative_destination,
        "phase": phase,
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }
    (directory / "import-state.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def test_reconcile_keeps_active_removes_published_orphan_and_only_old_staging(
    tmp_path,
    artifact_db,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    now = datetime(2026, 7, 26, 12, 0, 0)
    active_id = str(uuid4())
    active_relative = f"imports/{active_id}/final_adapter.tar"
    active_file = output_root / active_relative
    active_file.parent.mkdir(parents=True)
    active_file.write_bytes(b"active")
    artifact_db.add(TrainingArtifact(
        id=active_id,
        task_id=None,
        artifact_type="final_adapter",
        relative_path=active_relative,
        sha256="a" * 64,
        metadata_json={"source": "uploaded", "adapter_stage": "sft", "base_model_id": service.SUPPORTED_BASE_MODEL_ID},
    ))
    artifact_db.commit()
    active_stage = _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=active_id,
        relative_destination=active_relative,
        phase="published",
        updated_at=now,
    )
    orphan_id = str(uuid4())
    orphan_relative = f"imports/{orphan_id}/final_adapter.tar"
    orphan_file = output_root / orphan_relative
    orphan_file.parent.mkdir(parents=True)
    orphan_file.write_bytes(b"orphan")
    orphan_stage = _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=orphan_id,
        relative_destination=orphan_relative,
        phase="published",
        updated_at=now,
    )
    old_artifact_id = str(uuid4())
    old_stage = _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=old_artifact_id,
        relative_destination=f"imports/{old_artifact_id}/final_adapter.tar",
        phase="receiving",
        updated_at=now - timedelta(hours=24, seconds=1),
    )
    exact_artifact_id = str(uuid4())
    exact_stage = _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=exact_artifact_id,
        relative_destination=f"imports/{exact_artifact_id}/final_adapter.tar",
        phase="receiving",
        updated_at=now - timedelta(hours=24),
    )

    service.reconcile_adapter_imports(artifact_db, output_root, now)

    assert active_file.is_file() and not active_stage.exists()
    assert not orphan_file.exists() and not orphan_stage.exists()
    assert not old_stage.exists()
    assert exact_stage.exists()


def _published_orphan_recovery_state(output_root: Path, now: datetime):
    artifact_id = str(uuid4())
    relative_destination = f"imports/{artifact_id}/final_adapter.tar"
    archive = output_root / relative_destination
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"orphan")
    staging_directory = _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=artifact_id,
        relative_destination=relative_destination,
        phase="published",
        updated_at=now,
    )
    return archive, staging_directory


def test_reconcile_retains_journal_when_archive_unlink_fails_then_retries(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    now = datetime(2026, 7, 26, 12, 0, 0)
    archive, staging_directory = _published_orphan_recovery_state(output_root, now)
    real_unlink = service.os.unlink

    def fail_archive_unlink(path, *args, **kwargs):
        if Path(path) == archive:
            raise OSError("private unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(service.os, "unlink", fail_archive_unlink)
    with pytest.raises(AdapterImportError) as raised:
        service.reconcile_adapter_imports(artifact_db, output_root, now)
    assert raised.value.code == "adapter_recovery_cleanup_failed"
    assert archive.is_file()
    assert (staging_directory / "import-state.json").is_file()

    artifact_db.rollback()
    monkeypatch.setattr(service.os, "unlink", real_unlink)
    service.reconcile_adapter_imports(artifact_db, output_root, now)
    artifact_db.commit()
    assert not archive.exists() and not staging_directory.exists()


def test_reconcile_retains_journal_when_orphan_directory_rmdir_fails_then_retries(
    tmp_path,
    artifact_db,
    monkeypatch,
):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    now = datetime(2026, 7, 26, 12, 0, 0)
    archive, staging_directory = _published_orphan_recovery_state(output_root, now)
    published_directory = archive.parent
    real_rmdir = service.os.rmdir

    def fail_published_rmdir(path, *args, **kwargs):
        if Path(path) == published_directory:
            raise OSError("private rmdir failure")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(service.os, "rmdir", fail_published_rmdir)
    with pytest.raises(AdapterImportError) as raised:
        service.reconcile_adapter_imports(artifact_db, output_root, now)
    assert raised.value.code == "adapter_recovery_cleanup_failed"
    assert not archive.exists() and published_directory.is_dir()
    assert (staging_directory / "import-state.json").is_file()

    artifact_db.rollback()
    monkeypatch.setattr(service.os, "rmdir", real_rmdir)
    service.reconcile_adapter_imports(artifact_db, output_root, now)
    artifact_db.commit()
    assert not published_directory.exists() and not staging_directory.exists()


def test_reconcile_ignores_spoofed_symlink_and_unrelated_namespaces(tmp_path, artifact_db):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    now = datetime(2026, 7, 26, 12, 0, 0)
    victim = output_root / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    spoof_id = str(uuid4())
    _write_recovery_journal(
        output_root,
        import_id=str(uuid4()),
        artifact_id=spoof_id,
        relative_destination="../victim.txt",
        phase="published",
        updated_at=now - timedelta(days=2),
    )
    staging_root = output_root / ".adapter-import-staging"
    (staging_root / str(uuid4())).symlink_to(victim)
    unrelated_import = output_root / "imports" / "unrelated"
    unrelated_import.mkdir(parents=True)
    (unrelated_import / "keep.txt").write_text("keep", encoding="utf-8")
    task_dir = output_root / str(uuid4())
    task_dir.mkdir()
    quarantine = output_root / ".training-quarantine"
    quarantine.mkdir()

    service.reconcile_adapter_imports(artifact_db, output_root, now)

    assert victim.read_text(encoding="utf-8") == "keep"
    assert (unrelated_import / "keep.txt").is_file()
    assert task_dir.is_dir() and quarantine.is_dir()


def test_main_startup_reconciles_adapter_imports_with_stable_error_logging(monkeypatch):
    from app import main as main_module

    events = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    monkeypatch.setattr(main_module, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        main_module,
        "reconcile_adapter_imports",
        lambda _db, _root, _now: events.append("reconcile"),
    )
    main_module.recover_adapter_imports()
    assert events == ["reconcile", "commit"]

    events.clear()
    monkeypatch.setattr(
        main_module,
        "reconcile_adapter_imports",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("private path")),
    )
    logged = []
    monkeypatch.setattr(main_module.logger, "error", lambda message: logged.append(message))
    main_module.recover_adapter_imports()
    assert events == ["rollback"]
    assert logged == ["adapter_import_recovery_failed"]
