from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Completeness = Literal["complete", "incomplete"]


class TrainingLabelSummary(BaseModel):
    sample_class: str | None = None
    domain: str | None = None
    fault_type: str | None = None


class TrainingTestCatalogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    test_id: int
    test_name: str
    platform: str
    version_id: int
    version_number: int
    label_summary: TrainingLabelSummary
    round_1_parse_status: str | None = None
    round_2_parse_status: str | None = None
    total_training_log_bytes: int
    completeness: Completeness
    missing_files: list[str] = Field(default_factory=list)
    import_id: str
    created_at: datetime


class TrainingTestCatalogOut(BaseModel):
    items: list[TrainingTestCatalogItem]
    total: int
    page: int
    page_size: int


class TrainingSplitPreviewRequest(BaseModel):
    test_version_ids: list[int] = Field(min_length=1)
    train_ratio: float
    validation_ratio: float
    test_ratio: float
    seed: int

    @model_validator(mode="after")
    def validate_split_request(self):
        if len(set(self.test_version_ids)) != len(self.test_version_ids):
            raise ValueError("test_version_ids must not contain duplicates")
        ratios = (self.train_ratio, self.validation_ratio, self.test_ratio)
        if any(not math.isfinite(ratio) or ratio < 0.0 for ratio in ratios):
            raise ValueError("Split ratios must be finite non-negative numbers")
        if not math.isclose(sum(ratios), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("Split ratios must sum to 1.0")
        return self


class TrainingSplitCounts(BaseModel):
    train: int
    validation: int
    test: int


class TrainingSplitStratum(BaseModel):
    sample_class: str
    domain: str
    fault_type: str
    counts: TrainingSplitCounts


class TrainingSplitPreviewOut(BaseModel):
    train_ids: list[int]
    validation_ids: list[int]
    test_ids: list[int]
    counts: TrainingSplitCounts
    strata: list[TrainingSplitStratum]


TrainingTaskType = Literal["cpt", "sft"]
TrainingPreset = Literal["quick", "formal"]


class TrainingTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    task_type: TrainingTaskType
    test_version_ids: list[int] = Field(min_length=1)
    preset: TrainingPreset
    overrides: dict[str, Any] = Field(default_factory=dict)
    train_ratio: float
    validation_ratio: float
    test_ratio: float
    seed: int
    cpt_adapter_artifact_id: str | None = Field(default=None, max_length=36)
    # 网页端选的基座模型路径（展示流程用；为空则 worker 用 settings 默认）
    base_model_path: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_task_create(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name must not be empty")
        if len(set(self.test_version_ids)) != len(self.test_version_ids):
            raise ValueError("test_version_ids must not contain duplicates")
        ratios = (self.train_ratio, self.validation_ratio, self.test_ratio)
        if any(not math.isfinite(ratio) or ratio < 0.0 for ratio in ratios):
            raise ValueError("Split ratios must be finite non-negative numbers")
        if not math.isclose(sum(ratios), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("Split ratios must sum to 1.0")
        if self.task_type == "cpt" and self.cpt_adapter_artifact_id is not None:
            raise ValueError("cpt_adapter_artifact_id is only valid for SFT tasks")
        return self


class TrainingTaskRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_checkpoint_artifact_id: str | None = Field(default=None, max_length=36)


class TrainingMetricOut(BaseModel):
    step: int | None = None
    epoch: float | None = None
    loss: float | None = None
    eval_loss: float | None = None
    learning_rate: float | None = None
    created_at: datetime


class TrainingArtifactOut(BaseModel):
    id: str
    task_id: str | None = None
    artifact_type: str
    size_bytes: int | None = None
    sha256: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class TrainingArtifactListOut(BaseModel):
    items: list[TrainingArtifactOut]
    total: int
    page: int
    page_size: int


class TrainingAdapterOut(BaseModel):
    id: str
    name: str
    adapter_stage: Literal["cpt", "sft"]
    source: Literal["training", "uploaded"]
    base_model_id: str
    size_bytes: int
    sha256: str
    deletable: bool
    usable_for_sft: bool
    evaluable: bool


class AdapterImportOut(BaseModel):
    adapter: TrainingAdapterOut
    deduplicated: bool


class TrainingAdapterListOut(BaseModel):
    items: list[TrainingAdapterOut]
    total: int
    page: int
    page_size: int


class TrainingTaskOut(BaseModel):
    id: str
    name: str
    task_type: TrainingTaskType
    job_kind: Literal["training", "evaluation"]
    state: str
    model_id: str
    parent_task_id: str | None = None
    cpt_adapter_artifact_id: str | None = None
    # 网页端选的基座模型路径
    base_model_path: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    progress: float | None = None
    queue_position: int | None = None
    latest_metric: TrainingMetricOut | None = None


class TrainingTaskListOut(BaseModel):
    items: list[TrainingTaskOut]
    total: int
    page: int
    page_size: int


class TrainingTimelineEvent(BaseModel):
    name: str
    at: datetime


class TrainingEvaluationOut(BaseModel):
    status: str
    source_sft_task_id: str | None = None
    source_sft_artifact_id: str | None = None
    summary: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TrainingTaskDetailOut(TrainingTaskOut):
    config: dict[str, Any]
    split_ids: dict[str, list[int]]
    split_counts: TrainingSplitCounts
    timeline: list[TrainingTimelineEvent]
    metrics: list[TrainingMetricOut]
    artifacts: list[TrainingArtifactOut]
    evaluation: TrainingEvaluationOut | None = None


class TrainingLogsOut(BaseModel):
    lines: list[str]
    next_after_line: int
    has_more: bool


class TrainingWorkerOut(BaseModel):
    id: str | None = None
    status: str
    current_task_id: str | None = None
    model_status: str | None = None
    last_heartbeat_at: datetime | None = None
    device_type: Literal["cuda", "cpu"] | None = None
    profile_name: str | None = None
    device: str | None = None
    gpu: dict[str, Any] | None = None
    queue_length: int


class TrainingDeleteOut(BaseModel):
    deleted: bool
