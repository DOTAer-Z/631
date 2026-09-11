from typing import Optional, List
from pydantic import BaseModel


# ── 请求 Schema ───────────────────────────────────────────────────────────────

class PreprocessRequest(BaseModel):
    """LLM 辅助预处理请求：纯文本方式"""
    raw_text: str


class SaveProcessedLogRequest(BaseModel):
    """保存处理后日志到 log_entries 表"""
    raw_text: str                          # 原始文本
    cleaned_text: str                      # 清洗后文本（实际入库的 raw_content）
    fault_type_id: int                     # 必须选择一个故障类型
    summary: Optional[str] = None          # LLM 提取的摘要（用户可编辑）
    filename: Optional[str] = "手动输入"   # 文件名
    auto_vectorize: bool = True            # 是否保存后立即向量化


# ── 响应 Schema ───────────────────────────────────────────────────────────────

class PreprocessOut(BaseModel):
    """LLM 预处理结果"""
    cleaned_text: str                           # 清洗/标准化后的文本
    suggested_fault_type: Optional[str] = None  # 建议的故障类型名称
    summary: Optional[str] = None               # LLM 提取的摘要
    extracted_keywords: List[str] = []           # 关键词列表


class SaveProcessedLogOut(BaseModel):
    """保存结果"""
    id: int
    filename: str
    fault_type_id: int
    fault_type_name: Optional[str] = None  # SQLAlchemy 模型上没这一列，由 handler 后填
    summary: Optional[str] = None
    is_indexed: bool
    chroma_doc_id: Optional[str] = None

    class Config:
        from_attributes = True
