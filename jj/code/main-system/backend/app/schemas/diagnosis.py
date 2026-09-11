from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel


# ── Debug Schema ───────────────────────────────────────────────────────────────

class DebugInfo(BaseModel):
    # 路由决策
    decision_path: str          # "fast_similarity_summary" | "slow_llm_analysis" | "fallback_llm_infer"
    channel: str = ""           # "fast" | "slow"
    reason: str = ""            # [ROUTE] 行摘要，说明为何走此路径
    # LLM 调用信息
    llm_called: bool = True     # 三条路径均调用 LLM，故恒为 True
    llm_mode: str = ""          # "fast_summary" | "detailed_analysis" | "fallback_infer"
    # 关键分值
    similarity_score: Optional[float] = None
    final_confidence: Optional[float] = None
    # 检索统计
    n_candidates: int = 0
    n_windows: int = 0
    # 耗时（ms）
    embedding_time_ms: float = 0.0   # 包含在 retrieval_time_ms 内
    retrieval_time_ms: float = 0.0
    llm_time_ms: float = 0.0
    total_time_ms: float = 0.0


# ── 请求 Schema ────────────────────────────────────────────────────────────────

class DiagnosisRequest(BaseModel):
    log_text: str
    run_id: Optional[str] = None    # 来自 /analyze，可选；传入时复用已有 log_windows
    top_k: int = 5                  # FAISS 检索数量，默认 5
    force_refresh: bool = False     # 传入 run_id 时：True 强制重诊断，False 命中历史则直接复用
    skip_gate: bool = True          # 入口已收口为「软件状态预测 严重/紧急 跳转」，信任等级、跳过二次门控


# ── 相似案例（输出子结构）─────────────────────────────────────────────────────

class SimilarCase(BaseModel):
    rank: int
    similarity: float
    fault_type: Optional[str] = None
    root_cause: Optional[str] = None
    root_cause_type: Optional[str] = None
    case_id: Optional[Any] = None
    window_id: Optional[str] = None


# ── 响应 Schema ────────────────────────────────────────────────────────────────

class DiagnosisOut(BaseModel):
    # ── 旧字段（前端依赖，不可删改）────────────────────────────────────────────
    id: int
    is_fault: bool
    channel_used: str               # "fast" | "slow"
    fault_type_name: Optional[str]
    similarity_score: Optional[float]
    confidence: Optional[float]
    llm_reasoning: Optional[str]
    created_at: datetime

    # ── 新增可选字段（前端不感知，不会报错）────────────────────────────────────
    run_id: Optional[str] = None
    root_cause: Optional[str] = None
    root_cause_type: Optional[str] = None
    recovery_hint: Optional[str] = None
    affected_components: Optional[List[str]] = None
    similar_cases: Optional[List[SimilarCase]] = None
    reasoning_path: Optional[List[str]] = None
    n_windows_analyzed: Optional[int] = None   # 调试用：本次分析的窗口数
    debug: Optional["DebugInfo"] = None

    # ── 显示用：文件名 + 上传时间（按 run_id 反查 log_uploads，前端优先展示，取代长 run_id）──
    filename: Optional[str] = None
    uploaded_at: Optional[str] = None
    source: Optional[str] = None   # "upload"=用户上传 / "dataset"=从数据库(DB1)选择

    class Config:
        from_attributes = True


class DiagnosisListOut(BaseModel):
    total: int
    items: list[DiagnosisOut]
