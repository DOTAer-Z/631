from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class LogEntry(Base):
    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True, index=True)
    fault_type_id = Column(Integer, ForeignKey("fault_types.id", ondelete="SET NULL"), nullable=True)
    filename = Column(String(256), nullable=False)
    raw_content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    chroma_doc_id = Column(String(64), unique=True, nullable=True)
    is_indexed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    fault_type = relationship("FaultType", back_populates="log_entries")
