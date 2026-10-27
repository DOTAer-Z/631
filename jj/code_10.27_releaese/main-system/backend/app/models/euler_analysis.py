from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from app.database import Base


class EulerAnalysisBatch(Base):
    __tablename__ = "euler_analysis_batches"

    id = Column(String(64), primary_key=True)
    state = Column(String(16), nullable=False, default="queued")
    run_ids = Column(JSON, nullable=False)
    processed = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    model = Column(String(256), nullable=True)
    model_source = Column(String(32), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class EulerAnalysisResult(Base):
    __tablename__ = "euler_analysis_results"

    run_id = Column(String(64), primary_key=True)
    batch_id = Column(String(64), nullable=False, index=True)
    state = Column(String(16), nullable=False)
    prediction = Column(JSON, nullable=True)
    model = Column(String(256), nullable=True)
    model_source = Column(String(32), nullable=True)
    diagnosis_id = Column(Integer, nullable=True)
    input_info = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
