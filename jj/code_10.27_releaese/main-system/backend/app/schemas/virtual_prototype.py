from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FAULT_TYPES = (
    "F1101", "F1206", "F2301", "F3409", "F4512",
    "F4620", "F5106", "F5203", "F5313", "F5402",
)

FaultType = Literal["F1101", "F1206", "F2301", "F3409", "F4512", "F4620", "F5106", "F5203", "F5313", "F5402"]
SampleClass = Literal["fault", "normal"]
TaskKind = Literal["augmentation", "reproduction", "batch"]


class VirtualPrototypeJobOptions(BaseModel):
    """Allow-listed Agent options; arbitrary guest parameters are rejected."""

    model_config = ConfigDict(extra="forbid")

    evidence_mode: Literal["observed", "simulated"] | None = None
    capability_policy: Literal["native_only", "simulate_if_unavailable"] | None = None
    prepare_timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    interface: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,15}$")
    workers: int | None = Field(default=None, ge=1, le=256)
    memory_mb: int | None = Field(default=None, ge=1, le=65536)
    attempt_count: int | None = Field(default=None, ge=1, le=10000)
    delay_ms: int | None = Field(default=None, ge=0, le=60000)
    loss_percent: float | None = Field(default=None, ge=0, le=100)
    process_count: int | None = Field(default=None, ge=1, le=10000)
    java_profile: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    port_count: int | None = Field(default=None, ge=1, le=1024)
    connection_count: int | None = Field(default=None, ge=1, le=1024)


class VirtualPrototypeJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_type: FaultType
    sample_class: SampleClass = "fault"
    task_kind: TaskKind = "batch"
    sample_count: int = Field(default=1, ge=1, le=100)
    test_id_start: int | None = Field(default=None, ge=0)
    seed: int = 20261008
    duration_seconds: int | None = Field(default=None, ge=1, le=60)
    checkpoint_phase: str = Field(default="observe", min_length=1, max_length=32)
    options: VirtualPrototypeJobOptions | None = None

    @field_validator("checkpoint_phase")
    @classmethod
    def strip_checkpoint_phase(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("checkpoint_phase must not be empty")
        return value

    @model_validator(mode="after")
    def validate_task_contract(self):
        if self.task_kind == "augmentation" and self.sample_count != 15:
            raise ValueError("augmentation requires exactly 15 samples")
        if self.task_kind == "reproduction" and self.sample_count != 1:
            raise ValueError("reproduction requires exactly one sample")
        options = self.options
        evidence = options.evidence_mode if options else None
        if self.fault_type in {"F2301", "F5203", "F5402"} and evidence == "simulated":
            raise ValueError("F2301/F5203/F5402 require real observed injection")
        if self.fault_type in {"F2301", "F5203", "F5402"}:
            if options is None:
                options = self.options = VirtualPrototypeJobOptions()
            options.evidence_mode = "observed"
            if options.capability_policy is None:
                options.capability_policy = "native_only"
        if options and options.capability_policy == "simulate_if_unavailable":
            raise ValueError("new tasks cannot automatically downgrade to simulated execution")
        if self.fault_type == "F1206" and options and options.workers not in (None, 1):
            raise ValueError("F1206 requires exactly one worker")
        if self.fault_type == "F4512" and options and options.workers is not None and options.workers > 8:
            raise ValueError("F4512 allows at most eight workers")
        return self



class VirtualPrototypeReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: Literal[1] = 1
    seed: int | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=60)
    execution_mode: Literal["accelerated_replay", "checkpoint_demo"] = "accelerated_replay"


class VirtualPrototypeSampleReproduceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reproduction_mode: Literal["normal", "accelerated", "checkpoint_demo"]
    duration_seconds: int | None = Field(default=None, ge=1, le=60)
    seed: int | None = None
    evidence_mode: Literal["simulated"] | None = None


class VirtualPrototypeOptionsOut(BaseModel):
    fault_types: list[str]
    fault_catalog: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: dict[str, list[dict[str, Any]]]
    alternatives: dict[str, dict[str, Any]] = Field(default_factory=dict)
    modes: list[str]
