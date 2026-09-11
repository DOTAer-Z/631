from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel


class PredictionRequest(BaseModel):
    log_text: str
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None
    temperature: Optional[float] = None


class GradeRequest(BaseModel):
    """对接入报告(report_id)或已接入日志(run_id)做大模型分级。

    优先 report_id（报告整体分级）；仅传 run_id 时按窗口/条目兜底。
    """
    report_id: Optional[str] = None
    run_id: Optional[str] = None


class PredictionOut(BaseModel):
    id: int
    run_id: Optional[str] = None
    health_status: str
    risk_summary: Optional[str]
    risk_details: Optional[Any]
    cpu_usage: Optional[float]
    memory_usage: Optional[float]
    disk_usage: Optional[float]
    temperature: Optional[float]
    created_at: datetime

    # ── 显示用：文件名 + 上传时间（按 run_id 反查 log_uploads）──
    filename: Optional[str] = None
    uploaded_at: Optional[str] = None
    source: Optional[str] = None   # "upload"=用户上传 / "dataset"=从数据库(DB1)选择

    class Config:
        from_attributes = True


class PredictionListOut(BaseModel):
    total: int
    items: list[PredictionOut]
