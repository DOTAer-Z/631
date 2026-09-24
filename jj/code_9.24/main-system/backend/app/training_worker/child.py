from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ChildSpecError(ValueError):
    pass


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_QLORA_KEYS = {
    "load_in_4bit",
    "bnb_4bit_quant_type",
    "bnb_4bit_compute_dtype",
    "bnb_4bit_use_double_quant",
    "lora_r",
    "lora_alpha",
    "lora_dropout",
}
_TRAINING_REQUIRED_KEYS = {
    "max_seq_length",
    "learning_rate",
    "num_train_epochs",
    "gradient_accumulation_steps",
    "per_device_train_batch_size",
    "per_device_eval_batch_size",
    "warmup_ratio",
    "lr_scheduler_type",
    "logging_steps",
    "save_steps",
    "eval_steps",
    "save_total_limit",
    "bf16",
    "report_to",
    "output_dir",
}
_TRAINING_OPTIONAL_KEYS = {"eval_strategy"}
_PROFILE_FIELDS = {
    "cuda_qlora_bf16": ("cuda", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_bf16": ("cpu", "nf4_4bit", "bf16"),
    "cpu_qlora_4bit_fp32": ("cpu", "nf4_4bit", "fp32"),
    "cpu_lora_bf16": ("cpu", "none", "bf16"),
    "cpu_lora_fp32": ("cpu", "none", "fp32"),
}


@dataclass(frozen=True)
class ChildTaskSpec:
    schema_version: int
    task_id: str
    task_type: str
    job_kind: str
    output_root: str
    task_root: str
    config_path: str | None
    config_sha256: str | None
    test_file: str | None
    base_model_path: str
    cpt_adapter_path: str | None
    sft_adapter_path: str | None
    resume_checkpoint: str | None
    metrics_path: str
    result_path: str
    log_path: str
    evaluation_output_dir: str | None
    evaluation_checkpoint: str | None
    requested_device: str
    required_device_profile: str | None
    profile_path: str

    @classmethod
    def load(cls, spec_path: str | Path) -> "ChildTaskSpec":
        path = Path(spec_path)
        try:
            payload = json.loads(
                _read_regular_file(path, "task spec").decode("utf-8"),
                object_pairs_hook=_unique_object,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ChildSpecError("task spec is not valid JSON") from exc
        expected = {field.name for field in fields(cls)}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ChildSpecError("task spec fields do not match the internal schema")
        spec = cls(**payload)
        spec.validate(path)
        return spec

    def validate(self, spec_path: Path) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ChildSpecError("task spec schema version is invalid")
        required_strings = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "job_kind": self.job_kind,
            "output_root": self.output_root,
            "task_root": self.task_root,
            "base_model_path": self.base_model_path,
            "metrics_path": self.metrics_path,
            "result_path": self.result_path,
            "log_path": self.log_path,
            "requested_device": self.requested_device,
            "profile_path": self.profile_path,
        }
        optional_strings = {
            "config_path": self.config_path,
            "config_sha256": self.config_sha256,
            "test_file": self.test_file,
            "cpt_adapter_path": self.cpt_adapter_path,
            "sft_adapter_path": self.sft_adapter_path,
            "resume_checkpoint": self.resume_checkpoint,
            "evaluation_output_dir": self.evaluation_output_dir,
            "evaluation_checkpoint": self.evaluation_checkpoint,
            "required_device_profile": self.required_device_profile,
        }
        if any(not isinstance(value, str) or not value for value in required_strings.values()):
            raise ChildSpecError("task spec required fields must be non-empty strings")
        if any(value is not None and (not isinstance(value, str) or not value)
               for value in optional_strings.values()):
            raise ChildSpecError("task spec optional fields must be null or non-empty strings")
        if (not isinstance(self.task_id, str) or not self.task_id
                or Path(self.task_id).name != self.task_id
                or self.task_type not in {"cpt", "sft"}):
            raise ChildSpecError("task spec identity is invalid")
        if self.job_kind not in {"training", "evaluation"}:
            raise ChildSpecError("task spec job kind is invalid")
        if self.requested_device not in {"auto", "cuda", "cpu"}:
            raise ChildSpecError("requested training device is invalid")
        if (
            self.required_device_profile is not None
            and self.required_device_profile not in _PROFILE_FIELDS
        ):
            raise ChildSpecError("required device profile is invalid")
        output_root = _existing_directory(self.output_root, "output root")
        root = _existing_directory(self.task_root, "task root")
        expected_root = _existing_directory(output_root / self.task_id, "task root")
        if root != expected_root:
            raise ChildSpecError("task root is not canonical")
        _exact_existing_file(spec_path, root / "task-spec.json", "task spec")
        _exact_sink(self.metrics_path, root / "metrics.jsonl", "metrics path")
        _exact_sink(self.result_path, root / "result.json", "result path")
        _exact_sink(self.log_path, root / "child.log", "log path")
        _exact_sink(
            self.profile_path,
            root / "effective-device-profile.json",
            "device profile path",
        )
        _existing_directory(self.base_model_path, "base model")
        if self.job_kind == "training":
            if (self.config_path is None or self.config_sha256 is None
                    or _SHA256_PATTERN.fullmatch(self.config_sha256) is None
                    or self.test_file is not None or self.evaluation_output_dir is not None):
                raise ChildSpecError("training task spec fields are inconsistent")
            _exact_existing_file(self.config_path, root / "training.yaml", "config path")
            _existing_directory(root / "run", "training output")
            if self.evaluation_checkpoint is not None or self.sft_adapter_path is not None:
                raise ChildSpecError("training task spec contains evaluation fields")
            if self.task_type == "cpt" and self.cpt_adapter_path is not None:
                raise ChildSpecError("CPT training cannot use a CPT adapter")
        else:
            if (self.task_type != "sft" or self.config_path is not None
                    or self.config_sha256 is not None or self.test_file is None
                    or self.sft_adapter_path is None or self.evaluation_output_dir is None
                    or self.evaluation_checkpoint != "sft" or self.resume_checkpoint is not None
                    or self.cpt_adapter_path is not None):
                raise ChildSpecError("evaluation task spec fields are inconsistent")
            _exact_sink(
                self.evaluation_output_dir,
                root / "evaluation",
                "evaluation output",
                directory=True,
            )
            _exact_existing_file(
                self.test_file,
                root / "inputs" / "test.jsonl",
                "test file",
            )
        for value, expected, label in (
            (self.cpt_adapter_path, root / "inputs" / "cpt-adapter", "CPT adapter"),
            (self.sft_adapter_path, root / "inputs" / "sft-adapter", "SFT adapter"),
            (self.resume_checkpoint, root / "inputs" / "resume-checkpoint", "resume checkpoint"),
        ):
            if value is not None:
                trusted = _existing_directory(value, label)
                if trusted != _existing_directory(expected, label):
                    raise ChildSpecError(f"{label} is not the canonical task input")


class JsonlMetricCallback:
    """Transformers-compatible callback that emits only typed JSONL records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def on_log(self, args=None, state=None, control=None, logs=None, **_kwargs):
        values = logs or {}
        loss = values.get("loss")
        if loss is None:
            loss = values.get("train_loss")
        record = {
            "step": _optional_int(getattr(state, "global_step", None)),
            "epoch": _optional_number(values.get("epoch", getattr(state, "epoch", None))),
            "loss": _optional_number(loss),
            "eval_loss": _optional_number(values.get("eval_loss")),
            "learning_rate": _optional_number(values.get("learning_rate")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return control


def run_spec(spec: ChildTaskSpec) -> dict[str, Any]:
    callback = JsonlMetricCallback(spec.metrics_path)
    selected_profile: dict[str, str] | None = None

    def publish_profile(profile: Any) -> None:
        nonlocal selected_profile
        payload = _safe_profile_payload(profile)
        if (
            spec.requested_device != "auto"
            and payload["device_type"] != spec.requested_device
        ):
            raise ChildSpecError("effective device profile violates the task contract")
        if (
            spec.required_device_profile is not None
            and payload["profile_name"] != spec.required_device_profile
        ):
            raise ChildSpecError("effective device profile violates the task contract")
        if selected_profile is not None and selected_profile != payload:
            raise ChildSpecError("effective device profile changed after selection")
        try:
            _atomic_json(Path(spec.profile_path), payload)
        except Exception as exc:
            raise ChildSpecError(
                "effective device profile publication failed"
            ) from exc
        selected_profile = payload

    def success_result(result: dict[str, Any]) -> dict[str, Any]:
        if selected_profile is None:
            raise ChildSpecError("effective device profile was not selected")
        return result | selected_profile

    if spec.job_kind == "training":
        config = _load_training_config(spec)
        if spec.task_type == "cpt":
            from embedded_fault_diag.train_cpt import run_training
        else:
            from embedded_fault_diag.train_sft import run_training
        adapter = run_training(
            config,
            resume_from_checkpoint=spec.resume_checkpoint,
            callbacks=[callback],
            requested_device=spec.requested_device,
            required_profile=spec.required_device_profile,
            on_profile_selected=publish_profile,
        )
        final_adapter = _existing_directory(adapter, "final adapter")
        run_root = _existing_directory(Path(spec.task_root) / "run", "training output")
        _require_inside(run_root, final_adapter, "final adapter")
        return success_result(
            {"status": "succeeded", "final_adapter": str(final_adapter)}
        )

    from argparse import Namespace
    from embedded_fault_diag.eval_open_set import load_samples, run_generation

    output = _ensure_directory(Path(spec.evaluation_output_dir), "evaluation output")
    args = Namespace(
        input=Path(spec.test_file), base_model=spec.base_model_path,
        cpt_adapter=None, sft_adapter=Path(spec.sft_adapter_path), checkpoint=["sft"],
        out=output, limit=0, task_type=[], hide_fault_type=[],
        max_input_tokens=4096, max_new_tokens=1024, dry_run=False,
    )
    samples = load_samples(Path(spec.test_file), limit=0, task_types=set())
    summary = run_generation(
        args,
        samples,
        requested_device=spec.requested_device,
        required_profile=spec.required_device_profile,
        on_profile_selected=publish_profile,
    )
    summary_path = output / "summary.json"
    _atomic_json(summary_path, summary)
    report = _exact_existing_file(summary_path, output / "summary.json", "evaluation summary")
    return success_result(
        {"status": "succeeded", "evaluation_summary": str(report)}
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-spec", required=True)
    args = parser.parse_args()
    spec: ChildTaskSpec | None = None
    try:
        spec = ChildTaskSpec.load(args.task_spec)
        result = run_spec(spec)
        _atomic_json(Path(spec.result_path), result)
    except Exception as exc:
        if spec is not None:
            _atomic_json(Path(spec.result_path), {"status": "failed", "error": str(exc)})
        raise


def _load_training_config(spec: ChildTaskSpec) -> dict[str, Any]:
    import yaml

    raw = _read_regular_file(Path(spec.config_path), "config path")
    if hashlib.sha256(raw).hexdigest() != spec.config_sha256:
        raise ChildSpecError("training config digest does not match the task spec")
    try:
        config = _strict_yaml_load(raw, yaml)
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise ChildSpecError("training config is not valid YAML") from exc
    expected_top = {"model", "data", "qlora", "training"}
    if spec.cpt_adapter_path is not None:
        expected_top.add("adapter")
    if not isinstance(config, dict) or set(config) != expected_top:
        raise ChildSpecError("training config fields do not match the internal schema")
    model = _exact_mapping(config.get("model"), {"model_name_or_path"}, "model config")
    data = _exact_mapping(config.get("data"), {"train_file", "validation_file"}, "data config")
    qlora = _exact_mapping(config.get("qlora"), _QLORA_KEYS, "QLoRA config")
    training = config.get("training")
    if (not isinstance(training, dict)
            or not _TRAINING_REQUIRED_KEYS <= set(training)
            or set(training) - _TRAINING_REQUIRED_KEYS - _TRAINING_OPTIONAL_KEYS):
        raise ChildSpecError("training fields do not match the internal schema")
    if not qlora:
        raise ChildSpecError("QLoRA config is empty")

    root = _existing_directory(spec.task_root, "task root")
    expected_paths = {
        "model_name_or_path": _existing_directory(spec.base_model_path, "base model"),
        "train_file": _exact_existing_file(
            root / "data" / "train.jsonl", root / "data" / "train.jsonl", "train file"
        ),
        "validation_file": _exact_existing_file(
            root / "data" / "validation.jsonl",
            root / "data" / "validation.jsonl",
            "validation file",
        ),
        "output_dir": _existing_directory(root / "run", "training output"),
    }
    actual_paths = {
        "model_name_or_path": model["model_name_or_path"],
        "train_file": data["train_file"],
        "validation_file": data["validation_file"],
        "output_dir": training["output_dir"],
    }
    for key, expected in expected_paths.items():
        if not isinstance(actual_paths[key], str) or actual_paths[key] != str(expected):
            raise ChildSpecError(f"training config {key} is not canonical")
    if spec.cpt_adapter_path is not None:
        adapter = _exact_mapping(config.get("adapter"), {"cpt_adapter_path"}, "adapter config")
        expected_adapter = _existing_directory(spec.cpt_adapter_path, "CPT adapter")
        if adapter["cpt_adapter_path"] != str(expected_adapter):
            raise ChildSpecError("training config CPT adapter is not canonical")
    return config


def _strict_yaml_load(raw: bytes, yaml_module) -> Any:
    class StrictSafeLoader(yaml_module.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError("duplicate YAML mapping key")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    StrictSafeLoader.add_constructor(
        yaml_module.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    return yaml_module.load(raw, Loader=StrictSafeLoader)


def _exact_mapping(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ChildSpecError(f"{label} fields do not match the internal schema")
    return value


def _existing_directory(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ChildSpecError(f"{label} must be absolute")
    _reject_symlink_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ChildSpecError(f"{label} is unavailable") from exc
    if not resolved.is_dir():
        raise ChildSpecError(f"{label} is not a directory")
    return resolved


def _exact_existing_file(value: str | Path, expected: Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ChildSpecError(f"{label} must be absolute")
    _read_regular_file(path, label)
    resolved = path.resolve(strict=True)
    expected_resolved = Path(expected).resolve(strict=True)
    if resolved != expected_resolved:
        raise ChildSpecError(f"{label} is not canonical")
    return resolved


def _exact_sink(value: str | Path, expected: Path, label: str, *, directory: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ChildSpecError(f"{label} must be absolute")
    _reject_symlink_components(path, label)
    try:
        parent = path.parent.resolve(strict=True)
        expected_parent = Path(expected).parent.resolve(strict=True)
    except OSError as exc:
        raise ChildSpecError(f"{label} parent is unavailable") from exc
    canonical = parent / path.name
    if canonical != expected_parent / Path(expected).name:
        raise ChildSpecError(f"{label} is not canonical")
    if path.exists() and ((directory and not path.is_dir()) or (not directory and not path.is_file())):
        raise ChildSpecError(f"{label} has the wrong file type")
    return canonical


def _ensure_directory(path: Path, label: str) -> Path:
    _exact_sink(path, path, label, directory=True)
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ChildSpecError(f"{label} is unavailable") from exc
    return _existing_directory(path, label)


def _read_regular_file(path: Path, label: str) -> bytes:
    path = Path(path)
    if not path.is_absolute():
        raise ChildSpecError(f"{label} must be absolute")
    _reject_symlink_components(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ChildSpecError(f"{label} is not a regular file") from exc


def _reject_symlink_components(path: Path, label: str) -> None:
    for component in reversed((path, *path.parents)):
        try:
            if component.is_symlink():
                raise ChildSpecError(f"{label} contains a symlink")
        except OSError as exc:
            raise ChildSpecError(f"{label} is unavailable") from exc


def _require_inside(root: Path, value: Path, label: str) -> Path:
    if not value.is_absolute():
        raise ChildSpecError(f"{label} must be absolute")
    _reject_symlink_components(value, label)
    try:
        resolved = value.resolve(strict=True)
    except OSError as exc:
        raise ChildSpecError(f"{label} is unavailable") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ChildSpecError(f"{label} escapes the task root") from exc
    return resolved


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_profile_payload(profile: Any) -> dict[str, str]:
    payload = {
        "device_type": getattr(profile, "device_type", None),
        "profile_name": getattr(profile, "name", None),
        "quantization": getattr(profile, "quantization", None),
        "compute_dtype": getattr(profile, "compute_dtype", None),
    }
    if any(not isinstance(value, str) for value in payload.values()):
        raise ChildSpecError("effective device profile is invalid")
    expected = _PROFILE_FIELDS.get(payload["profile_name"])
    if expected != (
        payload["device_type"],
        payload["quantization"],
        payload["compute_dtype"],
    ):
        raise ChildSpecError("effective device profile is invalid")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    parent_fd = _open_absolute_directory(path.parent, "JSON output parent")
    temporary_name: str | None = None
    replaced = False
    try:
        target_name = path.name
        if not target_name or target_name in {".", ".."}:
            raise ChildSpecError("JSON output path is invalid")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(128):
            candidate = f".{target_name}.{secrets.token_hex(16)}.tmp"
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=parent_fd)
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        else:
            raise ChildSpecError("temporary JSON output is unavailable")

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            current = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not (
            stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)
        ):
            raise ChildSpecError("JSON output has the wrong file type")

        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        replaced = True
        final = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(final.st_mode) or stat.S_IMODE(final.st_mode) != 0o600:
            raise ChildSpecError("JSON output is not a private regular file")
        os.fsync(parent_fd)
    finally:
        if temporary_name is not None and not replaced:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def _open_absolute_directory(path: Path, label: str) -> int:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ChildSpecError(f"{label} must be an absolute canonical path")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    current_fd = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


if __name__ == "__main__":
    main()
