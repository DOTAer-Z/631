from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    is_fault = Column(Boolean, default=False)
    fault_type_id = Column(Integer, ForeignKey("fault_types.id", ondelete="SET NULL"), nullable=True)
    system_id = Column(Integer, ForeignKey("systems.id", ondelete="CASCADE"), nullable=True)
    # 数据集导入流程（data_pipeline / log_dataset_service）所需的扩展字段。
    # 注意：DB 列名 fault_type 与下方 relationship "fault_type" 同名，
    # 故 Python 属性用 fault_type_label，显式映射到列 "fault_type"。
    case_name = Column(String(256), nullable=True)
    test_name = Column(String(256), nullable=True)
    test_version_id = Column(
        Integer,
        ForeignKey("training_test_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    fault_type_label = Column("fault_type", String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    fault_type = relationship("FaultType", back_populates="cases")
    system = relationship("System", back_populates="cases")
