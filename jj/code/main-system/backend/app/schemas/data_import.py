from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_serializer


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """把 DB 读出的 naïve UTC 时刻补成带时区的 UTC，使 JSON 带 `Z` 后缀。

    dataset_imports.created_at 等列是 DateTime(无 timezone) + datetime.utcnow() 写入，
    值虽表示 UTC 时刻但序列化出来没有时区标记；前端 new Date(...) 会把无 `Z` 的字符串
    当浏览器本地时区解析，导致展示少 8 小时。此函数统一补上 UTC 时区后，
    前端 toLocaleString 才能正确换算成本地时间。字段为 NULL 时原样返回。
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

class DataImportUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class DataImportItem(BaseModel):
    import_id: str
    original_filename: str
    file_ext: str
    size_bytes: int
    status: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # ── 自动摄入流水线（方案 1） ──
    ingest_status: Optional[str] = None  # pending / ingesting / success / partial_success / failed / no_test_dirs
    ingest_error: Optional[str] = None
    ingested_at: Optional[datetime] = None
    ingested_run_count: Optional[int] = 0
    ingested_entry_count: Optional[int] = 0
    ingested_case_count: Optional[int] = 0
    # ── 数据源格式（识别到的带标签 JSONL 类型） ──
    format: Optional[str] = None  # structured / segment / None(NuttX 管线)
    # ── 区分新增/覆盖（重复上传同一 Test_*** 时 run_id 走 upsert，总数不变） ──
    ingested_new_case_count: Optional[int] = 0
    ingested_updated_case_count: Optional[int] = 0
    ingested_new_run_count: Optional[int] = 0
    ingested_updated_run_count: Optional[int] = 0
    training_complete_count: Optional[int] = 0
    training_incomplete_count: Optional[int] = 0
    training_duplicate_count: Optional[int] = 0
    training_failed_count: Optional[int] = 0
    training_parse_failed_count: Optional[int] = 0

    @field_serializer("created_at", "updated_at", "ingested_at")
    def _ser_dt(self, value: datetime) -> datetime:
        return _as_utc(value)


class DataImportListOut(BaseModel):
    items: List[DataImportItem]
    total: int
    page: int
    page_size: int


class DataImportDeleteOut(BaseModel):
    import_id: str
    deleted: bool
    file_deleted: bool


class DataImportPreviewOut(BaseModel):
    import_id: str
    original_filename: str
    file_ext: str
    size_bytes: int
    mime_type: Optional[str] = None
    status: str
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    file_exists: bool
    created_at: datetime
    updated_at: datetime
    # ── 自动摄入流水线（方案 1） ──
    ingest_status: Optional[str] = None
    ingest_error: Optional[str] = None
    ingested_at: Optional[datetime] = None
    ingested_run_count: Optional[int] = 0
    ingested_entry_count: Optional[int] = 0
    ingested_case_ids: Optional[List[str]] = None
    training_complete_count: Optional[int] = 0
    training_incomplete_count: Optional[int] = 0
    training_duplicate_count: Optional[int] = 0
    training_failed_count: Optional[int] = 0
    training_parse_failed_count: Optional[int] = 0

    @field_serializer("created_at", "updated_at", "ingested_at")
    def _ser_dt(self, value: datetime) -> datetime:
        return _as_utc(value)
