from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.database import Base


class ModelApiConfig(Base):
    __tablename__ = "model_api_configs"
    __table_args__ = (
        UniqueConstraint("name", name="uq_model_api_configs_name"),
        CheckConstraint(
            "provider IN ('openai-compatible', 'dashscope', 'vllm')",
            name="ck_model_api_configs_provider",
        ),
        Index(
            "uq_model_api_configs_one_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active IS TRUE"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    provider = Column(String(32), nullable=False)
    base_url = Column(String(1024), nullable=False)
    model = Column(String(255), nullable=False)
    api_key_ciphertext = Column(Text, nullable=False)
    timeout_seconds = Column(Integer, nullable=False, default=60)
    max_output_tokens = Column(Integer, nullable=False, default=8192)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
