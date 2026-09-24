from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any


class TrainingConfigError(ValueError):
    pass


ALLOWED_OVERRIDES = {
    "learning_rate",
    "num_train_epochs",
    "max_seq_length",
    "lora_r",
    "lora_alpha",
    "lora_dropout",
    "per_device_train_batch_size",
    "gradient_accumulation_steps",
    "logging_steps",
    "eval_steps",
    "save_steps",
}


_PRESETS: dict[tuple[str, str], dict[str, Any]] = {
    ("cpt", "quick"): {
        "qlora": {"lora_r": 32, "lora_alpha": 64, "lora_dropout": 0.05},
        "training": {
            "max_seq_length": 2048,
            "learning_rate": 0.00005,
            "num_train_epochs": 0.1,
            "gradient_accumulation_steps": 16,
            "per_device_train_batch_size": 1,
            "per_device_eval_batch_size": 1,
            "warmup_ratio": 0.03,
            "lr_scheduler_type": "cosine",
            "eval_strategy": "no",
            "logging_steps": 5,
            "save_steps": 100,
            "eval_steps": 100,
            "save_total_limit": 2,
            "bf16": True,
            "report_to": "none",
        },
    },
    ("cpt", "formal"): {
        "qlora": {"lora_r": 32, "lora_alpha": 64, "lora_dropout": 0.05},
        "training": {
            "max_seq_length": 4096,
            "learning_rate": 0.00005,
            "num_train_epochs": 1,
            "gradient_accumulation_steps": 16,
            "per_device_train_batch_size": 1,
            "per_device_eval_batch_size": 1,
            "warmup_ratio": 0.03,
            "lr_scheduler_type": "cosine",
            "logging_steps": 10,
            "save_steps": 500,
            "eval_steps": 500,
            "save_total_limit": 3,
            "bf16": True,
            "report_to": "none",
        },
    },
    ("sft", "quick"): {
        "qlora": {"lora_r": 32, "lora_alpha": 64, "lora_dropout": 0.05},
        "training": {
            "max_seq_length": 2048,
            "learning_rate": 0.0001,
            "num_train_epochs": 0.1,
            "gradient_accumulation_steps": 16,
            "per_device_train_batch_size": 1,
            "per_device_eval_batch_size": 1,
            "warmup_ratio": 0.05,
            "lr_scheduler_type": "cosine",
            "eval_strategy": "no",
            "logging_steps": 5,
            "save_steps": 250,
            "eval_steps": 100,
            "save_total_limit": 2,
            "bf16": True,
            "report_to": "none",
        },
    },
    ("sft", "formal"): {
        "qlora": {"lora_r": 32, "lora_alpha": 64, "lora_dropout": 0.05},
        "training": {
            "max_seq_length": 4096,
            "learning_rate": 0.0001,
            "num_train_epochs": 1.0,
            "gradient_accumulation_steps": 16,
            "per_device_train_batch_size": 1,
            "per_device_eval_batch_size": 1,
            "warmup_ratio": 0.05,
            "lr_scheduler_type": "cosine",
            "eval_strategy": "steps",
            "logging_steps": 10,
            "save_steps": 1000,
            "eval_steps": 1000,
            "save_total_limit": 3,
            "bf16": True,
            "report_to": "none",
        },
    },
}


_FIXED_QLORA = {
    "load_in_4bit": True,
    "bnb_4bit_quant_type": "nf4",
    "bnb_4bit_compute_dtype": "bfloat16",
    "bnb_4bit_use_double_quant": True,
}
_PATH_KEYS = ("model_name_or_path", "train_file", "validation_file", "output_dir")


def build_training_config(
    task_type: str,
    preset: str,
    overrides: Mapping[str, Any],
    paths: Mapping[str, str],
    cpt_adapter_path: str | None = None,
) -> dict[str, Any]:
    _validate_task_and_preset(task_type, preset)
    _validate_paths(paths)
    normalized_overrides = _validate_overrides(overrides)
    if task_type == "cpt" and cpt_adapter_path is not None:
        raise TrainingConfigError("CPT configuration cannot include a CPT adapter")

    values = copy.deepcopy(_PRESETS[(task_type, preset)])
    qlora = {**_FIXED_QLORA, **values["qlora"]}
    training = values["training"]
    for name, value in normalized_overrides.items():
        target = qlora if name in {"lora_r", "lora_alpha", "lora_dropout"} else training
        target[name] = value
    training["output_dir"] = paths["output_dir"]

    config: dict[str, Any] = {
        "model": {"model_name_or_path": paths["model_name_or_path"]},
        "data": {
            "train_file": paths["train_file"],
            "validation_file": paths["validation_file"],
        },
        "qlora": qlora,
        "training": training,
    }
    if task_type == "sft" and cpt_adapter_path is not None:
        if not isinstance(cpt_adapter_path, str) or not cpt_adapter_path.strip():
            raise TrainingConfigError("cpt_adapter_path must be a non-empty string")
        config["adapter"] = {"cpt_adapter_path": cpt_adapter_path}
    return config


def normalized_config_snapshot(
    task_type: str, preset: str, overrides: Mapping[str, Any]
) -> dict[str, Any]:
    config = build_training_config(
        task_type,
        preset,
        overrides,
        {
            "model_name_or_path": "model",
            "train_file": "train.jsonl",
            "validation_file": "validation.jsonl",
            "output_dir": "output",
        },
    )
    return {
        "preset": preset,
        "qlora": copy.deepcopy(config["qlora"]),
        "training": {
            key: value
            for key, value in config["training"].items()
            if key != "output_dir"
        },
    }


def _validate_task_and_preset(task_type: str, preset: str) -> None:
    if task_type not in {"cpt", "sft"}:
        raise TrainingConfigError(f"unsupported task type: {task_type}")
    if preset not in {"quick", "formal"}:
        raise TrainingConfigError(f"unsupported preset: {preset}")


def _validate_paths(paths: Mapping[str, str]) -> None:
    for key in _PATH_KEYS:
        value = paths.get(key)
        if not isinstance(value, str) or not value:
            raise TrainingConfigError(f"paths.{key} must be a non-empty string")


def _validate_overrides(overrides: Mapping[str, Any]) -> dict[str, int | float]:
    if not isinstance(overrides, Mapping):
        raise TrainingConfigError("overrides must be a mapping")
    unknown = sorted(set(overrides) - ALLOWED_OVERRIDES)
    if unknown:
        raise TrainingConfigError(f"unsupported parameter: {unknown[0]}")
    validated: dict[str, int | float] = {}
    for name, value in overrides.items():
        if name in {"max_seq_length", "lora_r", "lora_alpha", "per_device_train_batch_size", "gradient_accumulation_steps", "logging_steps", "eval_steps", "save_steps"}:
            validated[name] = _integer(name, value)
        else:
            validated[name] = _finite_number(name, value)

    _bounded(validated, "max_seq_length", 512, 8192)
    _bounded(validated, "lora_r", 4, 256)
    _bounded(validated, "lora_dropout", 0, 0.5)
    _bounded(validated, "per_device_train_batch_size", 1, 8)
    for name in ("gradient_accumulation_steps", "logging_steps", "eval_steps", "save_steps"):
        if name in validated and validated[name] <= 0:
            raise TrainingConfigError(f"{name} must be positive")
    if "learning_rate" in validated and not 0 < validated["learning_rate"] <= 0.01:
        raise TrainingConfigError("learning_rate must be in (0, 0.01]")
    if "num_train_epochs" in validated and not 0 < validated["num_train_epochs"] <= 100:
        raise TrainingConfigError("num_train_epochs must be in (0, 100]")
    return validated


def _integer(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainingConfigError(f"{name} must be an integer")
    return value


def _finite_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TrainingConfigError(f"{name} must be a finite number")
    return value


def _bounded(values: Mapping[str, int | float], name: str, lower: int | float, upper: int | float) -> None:
    if name in values and not lower <= values[name] <= upper:
        raise TrainingConfigError(f"{name} must be between {lower} and {upper}")
