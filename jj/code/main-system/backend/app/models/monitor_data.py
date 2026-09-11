from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class MonitorData(Base):
    __tablename__ = "monitor_data"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    cpu_usage = Column(Float, nullable=True)
    memory_usage = Column(Float, nullable=True)
    disk_usage = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    network_in = Column(Float, nullable=True)
    network_out = Column(Float, nullable=True)
    other_metrics = Column(JSON, nullable=True)
    source = Column(String(32), nullable=False)  # api 或 file
    status = Column(String(16), default="normal")  # normal, warning, error
    created_at = Column(DateTime, default=datetime.utcnow)
