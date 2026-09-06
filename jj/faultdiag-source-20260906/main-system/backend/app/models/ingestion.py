from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.database import Base


class Ingestion(Base):
    __tablename__ = "ingestions"

    id = Column(Integer, primary_key=True, index=True)
    ingestion_id = Column(String(64), unique=True, index=True, nullable=False)
    source_type = Column(String(32), nullable=False)  # upload|api
    source_info = Column(Text, nullable=True)  # 存储源信息，如文件路径或API URL
    file_name = Column(String(256), nullable=True)
    file_size = Column(Integer, nullable=True)
    line_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    failure_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="pending")  # pending|success|failed
    error_message = Column(Text, nullable=True)
    log_type = Column(String(32), nullable=False, default="other")  # alert|error|crash|other
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
