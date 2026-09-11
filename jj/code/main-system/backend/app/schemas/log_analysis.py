from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── 请求 Schema ───────────────────────────────────────────────────────────────

class AnalyzeTextRequest(BaseModel):
    """分析日志（不触发诊断），纯文本方式"""
    log_text: str
    filename: Optional[str] = "手动输入"


class AnalyzeApiRequest(BaseModel):
    """分析日志（不触发诊断），接口导入方式"""
    url: str
    method: str = "GET"
    params: Dict[str, Any] = {}
    headers: Dict[str, str] = {}


class ClassifyLogRequest(BaseModel):
    """对已有 LogEntry 批量设置故障类型"""
    log_ids: List[int]
    fault_type_id: int


class ManualScoreAnalyzeRequest(BaseModel):
    """手动评分请求"""
    log_id: str
    rule: str
    weighted_score: float


AnalyzeLogRequest = AnalyzeTextRequest


# ── 响应 Schema ───────────────────────────────────────────────────────────────

class LogLevelStats(BaseModel):
    error_count: int
    warning_count: int
    info_count: int
    debug_count: int
    total_lines: int


class EvidenceItem(BaseModel):
    """单条诊断证据（最多返回 5 条，按 score 降序）"""
    source: str    # "level_stats" | "keyword" | "stack_trace" | "window"
    detail: str    # 简短描述，不超过 80 字符
    score: float   # 该证据对总分的贡献值


class AnalyzeLogOut(BaseModel):
    """日志分析结果（兼容旧字段 + 新增诊断字段）"""

    # ── 旧字段（前端依赖，不可删改）────────────────────────────────────────────
    filename: str
    log_level_stats: LogLevelStats
    extracted_timestamps: List[str]    # 前 5 个时间戳（展示用）
    extracted_error_codes: List[str]   # 提取到的错误码
    extracted_stack_traces: List[str]  # 提取到的异常栈（前 3 条）
    raw_content: str                   # 原始内容（供预览，截断至 5000 字符）
    line_count: int

    # ── 新增诊断字段 ─────────────────────────────────────────────────────────
    has_fault: bool
    fault_score: float
    confidence: float
    summary: str
    evidence: List[EvidenceItem]                                 # 最多 5 条
    next_action: Literal["fault_location", "warning_forecast"]
    run_id: str                                                  # 必填，供 fault-location 复用
    score_breakdown: Dict[str, Any]                              # 调试用分项得分

    # ── 管道元信息（可选）────────────────────────────────────────────────────
    detected_format: Optional[str] = None
    encoding: Optional[str] = None
    parse_quality: Optional[Dict[str, int]] = None
    
    # ── 结构化数据（新增）────────────────────────────────────────────────────
    structured_data: Optional[Dict[str, Any]] = None


class LogParseTaskCreate(BaseModel):
    run_ids: List[str] = Field(min_length=1, max_length=100)

    @field_validator("run_ids")
    @classmethod
    def validate_run_ids(cls, values: List[str]) -> List[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("run_ids must not contain blank values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("run_ids must be unique")
        return normalized


class LogParseFailureOut(BaseModel):
    run_id: str
    code: str
    message: str


class LogParseTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: Literal[
        "queued", "running", "cancelling", "cancelled", "succeeded", "failed"
    ]
    progress: int
    stage: str
    run_ids: List[str]
    current_run_id: Optional[str] = None
    completed_count: int
    success_count: int
    fail_count: int
    results: Dict[str, AnalyzeLogOut]
    fail_details: List[LogParseFailureOut]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    cancel_requested_at: Optional[datetime] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class DatasetRunListItem(BaseModel):
    run_id: str
    case_id: Optional[str] = None
    run_name: Optional[str] = None
    case_name: Optional[str] = None
    test_name: Optional[str] = None
    system_id: Optional[str] = None
    round_no: Optional[int] = None
    fault_type: Optional[str] = None
    fault_status: Literal["fault", "normal"]
    data_category: Optional[str] = None  # structured / segment / semi
    parsed_lines: int
    total_lines: int
    error_logs: int
    critical_logs: int
    window_count: int
    created_at: Optional[datetime] = None
    # ── 数据导入透传字段（缺失时全部为 None / [] ，前端用 fallback）──
    import_display_name: Optional[str] = None
    import_description: Optional[str] = None
    import_tags: List[str] = []
    import_created_by: Optional[str] = None
    import_filename: Optional[str] = None
    import_id: Optional[str] = None


class DatasetRunListOut(BaseModel):
    items: List[DatasetRunListItem]
    total: int
    fault_count: int
    normal_count: int


class DatasetRunDetailOut(BaseModel):
    run_id: str
    case_id: Optional[str] = None
    run_name: Optional[str] = None
    case_name: Optional[str] = None
    test_name: Optional[str] = None
    system_id: Optional[str] = None
    subsystem: Optional[str] = None
    round_no: Optional[int] = None
    fault_type: Optional[str] = None
    fault_status: Literal["fault", "normal"]
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    parsed_lines: int
    total_lines: int
    error_logs: int
    critical_logs: int
    window_count: int
    entry_count: int
    windows_total: int
    level_distribution: Dict[str, int] = {}
    top_modules: List[Dict[str, Any]] = []
    created_at: Optional[datetime] = None


class DatasetLogEntryOut(BaseModel):
    timestamp: Optional[datetime] = None
    level: Optional[str] = None
    module: Optional[str] = None
    message: str
    file_path: Optional[str] = None
    line_no: Optional[int] = None


class DatasetRunEntriesOut(BaseModel):
    items: List[DatasetLogEntryOut]
    total: int
    page: int
    page_size: int


class DatasetLogWindowOut(BaseModel):
    window_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    strategy: Optional[str] = None
    entry_count: int
    error_events: int
    key_events: List[Dict[str, Any]]
    text_preview: str


class DatasetRunWindowsOut(BaseModel):
    items: List[DatasetLogWindowOut]
    total: int
    page: int
    page_size: int


# ── Stub 接口 Schema（迁移中，加 message 字段标记状态）────────────────────────

class VectorizeRequest(BaseModel):
    """向量化一条日志（[迁移中] 待接入 MongoDB + FAISS）"""
    log_text: str
    fault_type_id: Optional[int] = None
    summary: Optional[str] = None
    filename: Optional[str] = "手动输入"


class VectorizeOut(BaseModel):
    log_entry_id: int
    chroma_doc_id: Optional[str]
    is_indexed: bool
    fault_type_name: Optional[str]
    message: Optional[str] = None    # stub 期间携带迁移说明


class BatchVectorizeRequest(BaseModel):
    """批量向量化（[迁移中]）"""
    items: List[VectorizeRequest]


class BatchVectorizeOut(BaseModel):
    success_count: int
    fail_count: int
    results: List[VectorizeOut]
    message: Optional[str] = None


class UnclassifiedLogOut(BaseModel):
    id: int
    filename: str
    summary: Optional[str]
    is_indexed: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UnclassifiedLogListOut(BaseModel):
    total: int
    items: List[UnclassifiedLogOut]
    message: Optional[str] = None


class BatchClassifyOut(BaseModel):
    updated_count: int
    message: Optional[str] = None


class AnalyzeLogResult(BaseModel):
    """分析日志结果"""
    score: float
    risk_level: str
    confidence: float
    analysis_time: str


# ── 「日志列表」CRUD：编辑/删除请求与响应 ─────────────────────────────────────

class UpdateRunRequest(BaseModel):
    """编辑一条 run 的元信息（编辑标签弹窗的载荷）。

    所有字段都是可选的，传 None 表示不修改。前端只发用户改了的字段。
    fault_type_id 走 cases 的外键，是把数据集"原料"标注成训练样本的关键字段。
    """

    # case 维度的字段（一条 case 下的所有 run 共享）
    fault_type_id: Optional[int] = None       # 关联 fault_types.id；传 0 或 -1 表示清空
    is_fault: Optional[bool] = None            # 是否为故障样本
    description: Optional[str] = None          # case.description

    # run 维度的字段
    run_name: Optional[str] = None             # 仅改本条 run 的展示名


class UpdateRunOut(BaseModel):
    run_id: str
    case_id: Optional[str] = None
    updated_fields: List[str]
    message: Optional[str] = None


class BatchDeleteRunsRequest(BaseModel):
    run_ids: List[str]
    cleanup_empty_cases: bool = True   # 删完后该 case 下没有 run 了，是否一并清理 case


class DeleteRunOut(BaseModel):
    run_id: str
    deleted_log_entries: int
    deleted_log_windows: int
    deleted_chroma_docs: int
    deleted_case: bool                 # 关联 case 因没有任何 run 已被一并清理
    message: Optional[str] = None


class BatchDeleteRunsOut(BaseModel):
    requested: int
    deleted: int
    not_found: List[str]
    items: List[DeleteRunOut]


# ── 标注子系统 → 主系统「按窗口推送」的请求（Approach 1：两库分离，HTTP 桥接） ──
# 标注子系统把一个已标注时间窗的内容 + 人工标签推给主系统，主系统据此写出一条
# runs 记录 + 一批 log_entries，从而使该窗口能像 nuttx_Test_2000_round_2 一样被
# 诊断/预测/RAG 分析。

class AnnotatedRunLine(BaseModel):
    """窗口内一行原始日志。content 为原文；timestamp 为 UTC 秒级 epoch float（可空）。"""
    line_no: int
    content: str
    timestamp: Optional[float] = None

class AnnotatedRunFile(BaseModel):
    """窗口内的一个源文件：logical_path（如 cpu0/xxx.log）+ 其包含的行。"""
    logical_path: str
    lines: List[AnnotatedRunLine]

class AnnotatedRunImportRequest(BaseModel):
    """从标注子系统导入一个已标注窗口的请求体。"""
    run_id: str                       # 约定 annot_pkg{package_id}_win{window_id}
    source_type: Optional[str] = "annotation"   # 标记来源；存量三值为 dataset|upload|api
    system_id: Optional[str] = None   # 主系统多机组归组用；不传则用例名兜底
    subsystem: Optional[str] = None
    case_id: Optional[str] = None
    test_name: str                    # 通常会复用 DatasetPackage.name / slice_task.name
    run_name: Optional[str] = None
    is_fault: Optional[bool] = None   # 人工标注：label='abnormal' -> True
    fault_type: Optional[str] = None  # 人工标注 anomaly_type
    description: Optional[str] = None # 人工标注 note / 窗口说明
    lines: List[AnnotatedRunFile]     # 窗口内的全部日志行

class AnnotatedRunImportOut(BaseModel):
    run_id: str
    case_id: Optional[str] = None
    is_new: bool
    entries: int
    message: Optional[str] = None
