from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class StatsOut(BaseModel):
    """系统统计数字卡片"""
    fault_type_count: int
    log_total: int
    log_indexed: int
    diagnosis_total: int
    prediction_total: int


class RecentDiagnosisItem(BaseModel):
    id: int
    is_fault: bool
    fault_type_name: Optional[str]
    channel_used: str          # 'fast' | 'slow'
    confidence: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True


class RecentPredictionItem(BaseModel):
    id: int
    health_status: str         # 'green' | 'yellow' | 'red'
    risk_summary: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class DiagnosisChartOut(BaseModel):
    """诊断分布图数据"""
    fault_count: int           # is_fault=True
    normal_count: int          # is_fault=False
    fast_channel_count: int
    slow_channel_count: int


class PredictionChartOut(BaseModel):
    """健康状态分布图数据"""
    green_count: int
    yellow_count: int
    red_count: int


class FaultTypeCountItem(BaseModel):
    """单个故障类型的计数（用于数据集故障类型分布）"""
    fault_type: str
    count: int


class DatasetStatsOut(BaseModel):
    """已导入数据集（Test/run）的统计概况"""
    case_total: int            # cases 数（= Test_*** 数）
    run_total: int             # runs 数（每个 Test 的故障轮 + 正常轮）
    fault_run_count: int       # 故障轮数（is_fault=True）
    normal_run_count: int      # 正常/基线轮数（is_fault=False）
    log_entry_total: int       # log_entries 文档数
    log_window_total: int      # log_windows 文档数
    fault_type_distribution: List[FaultTypeCountItem]


class OverviewOut(BaseModel):
    stats: StatsOut
    recent_diagnoses: List[RecentDiagnosisItem]
    recent_predictions: List[RecentPredictionItem]
    diagnosis_chart: DiagnosisChartOut
    prediction_chart: PredictionChartOut
    dataset: DatasetStatsOut
