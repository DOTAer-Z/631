from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(64), unique=True, index=True, nullable=False)
    task_id = Column(String(64), index=True, nullable=True)
    source_type = Column(String(32), nullable=False)  # dataset|upload|api
    system_id = Column(String(64), index=True, nullable=True)
    subsystem = Column(String(256), nullable=True)
    case_id = Column(String(64), index=True, nullable=True)
    is_fault = Column(Boolean, nullable=True)
    test_name = Column(String(256), nullable=True)
    test_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    round_no = Column(Integer, nullable=True)
    line_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    failure_count = Column(Integer, nullable=False, default=0)
    log_type = Column(String(32), nullable=False, default="other")  # alert|error|crash|other
    # 数据集导入流程（data_pipeline / log_dataset_service）所需的扩展字段
    run_name = Column(String(256), nullable=True)
    error_logs = Column(Integer, nullable=False, default=0)
    critical_logs = Column(Integer, nullable=False, default=0)
    window_count = Column(Integer, nullable=False, default=0)
    stats_json = Column(JSON, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

