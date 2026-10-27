from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String, Text

from app.database import Base


class EulerArtifactImport(Base):
    """Created only by an explicit save request, never by job generation."""

    __tablename__ = "euler_artifact_imports"

    import_id = Column(String(64), primary_key=True)
    agent_url = Column(String(1024), nullable=False)
    job_id = Column(String(64), nullable=False, index=True)
    state = Column(String(16), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
