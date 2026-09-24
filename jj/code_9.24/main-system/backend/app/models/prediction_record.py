from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, JSON
from app.database import Base


class PredictionRecord(Base):
    __tablename__ = "prediction_records"

    id = Column(Integer, primary_key=True, index=True)
    input_log = Column(Text, nullable=False)
    run_id = Column(String(128), nullable=True, index=True)  # 关联日志运行；分级后可跳转故障诊断
    cpu_usage = Column(Float, nullable=True)
    memory_usage = Column(Float, nullable=True)
    disk_usage = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    health_status = Column(String(8), nullable=False)  # 'green', 'yellow', 'red'
    risk_summary = Column(Text, nullable=True)
    risk_details = Column(JSON, nullable=True)   # 原 JSONB（PostgreSQL专属）→ JSON（MySQL兼容）
    created_at = Column(DateTime, default=datetime.utcnow)
