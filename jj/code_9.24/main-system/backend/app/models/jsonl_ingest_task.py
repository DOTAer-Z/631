"""
jsonl_ingest_task.py — 带标签 JSONL 导入任务（进度/取消的状态载体）。

用于记录数据导入里「识别为 jsonl 并分流到导入器」的后台任务进度，
前端轮询 /data-imports/{import_id}/jsonl-task 显示进度条、点取消。

state 取值：
    queued      已创建未开始
    running     导入中
    success     全部完成（可含部分失败，若 records_failed>0 则前端显示部分成功）
    cancelled   用户取消
    error       意外异常
"""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

from app.database import Base


class JsonlIngestTask(Base):
    __tablename__ = "jsonl_ingest_tasks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, unique=True)
    import_id = Column(String(64), nullable=False, index=True)
    format = Column(String(32), nullable=True)  # structured / segment / "structured, segment"
    state = Column(String(32), nullable=False, default="queued")
    total_records = Column(Integer, nullable=True, default=0)
    processed_records = Column(Integer, nullable=False, default=0)
    ok_records = Column(Integer, nullable=False, default=0)
    failed_records = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
