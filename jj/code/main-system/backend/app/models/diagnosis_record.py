from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, ForeignKey, DateTime
from app.database import Base


class DiagnosisRecord(Base):
    __tablename__ = "diagnosis_records"

    id = Column(Integer, primary_key=True, index=True)
    input_log = Column(Text, nullable=False)
    run_id = Column(String(128), nullable=True, index=True)  # 关联的日志运行；按日志复读诊断历史
    channel_used = Column(String(8), nullable=False)  # 'fast' or 'slow'
    similarity_score = Column(Float, nullable=True)
    matched_log_id = Column(Integer, ForeignKey("log_entries.id", ondelete="SET NULL"), nullable=True)
    fault_type_id = Column(Integer, ForeignKey("fault_types.id", ondelete="SET NULL"), nullable=True)
    fault_type_name = Column(String(128), nullable=True)
    llm_reasoning = Column(Text, nullable=True)
    is_fault = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=True)
    # 根因相关字段（持久化后可复读，避免 by-run 复看时丢根因）
    root_cause = Column(Text, nullable=True)
    root_cause_type = Column(String(128), nullable=True)
    recovery_hint = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
