"""
jsonl_training_registry.py — 把带标签 JSONL(结构化/非结构化)登记为可训练样本。

背景:
    训练链路(模型训练页 /training/tests)只认 TrainingTest / TrainingTestVersion /
    TrainingTestLog(源自 NuttX Test_* 目录)。结构化 Test_xxx 与非结构化 seg_xxx
    由 jsonl_ingest 直接写 runs,从不进这三张表,因此训练页看不到、无法选择。

    本模块在 jsonl 导入 run 的同时,把该 run 登记成一条可训练的 TrainingTest 版本,
    使三类数据都能出现在训练文件列表并可被拆分/训练。

映射:
    结构化(Test_xxx:round_x):
      test_name  = Test_xxx  ,platform = "NuttX"
      ground_truth = { sample_class: fault|normal(按 round), domain: log_type, fault_type: 无 }
      round = round(1|2), log_type = qemu_console, content = 提取的日志正文
      round_1 与 round_2 是两个 run → 合并进同一个 Test 版本(two TrainingTestLog)
    非结构化(seg_xxx):
      test_name = 段名      ,platform = "synthetic"
      ground_truth = { sample_class: fault|normal(按 ground_truth.label), fault_type }
      round = 1, log_type = qemu_console, content = 段文本
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LOG_TYPE = "qemu_console"


def _optional_text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def register_training_for_jsonl(
    db: Session,
    *,
    test_name: str,
    platform: str,
    sample_class: str | None,
    domain: str | None,
    fault_type: str | None,
    round_no: int,
    log_content: str,
    import_id: str,
) -> None:
    """把一个 jsonl run 登记/合并到 TrainingTest 可训练记录。

    幂等:同 (test_name, content_sha256) 已存在时更新日志;否则新建 version。
    结构化同 Test 的 round_1/round_2 两次调用会合并(each round 一条 TrainingTestLog)。
    """
    from app.models.training_data import TrainingTest, TrainingTestLog, TrainingTestVersion

    content_bytes = (log_content or "").encode("utf-8")
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()

    test = db.query(TrainingTest).filter_by(platform=platform, test_name=test_name).first()
    if test is None:
        test = TrainingTest(platform=platform, test_name=test_name)
        db.add(test)
        db.flush()

    # 只登记 latest 版本所在的 training record；若已有同 test 版本则复用并追加该 round。
    version = db.query(TrainingTestVersion).filter_by(
        test_id=test.id, import_id=import_id, completeness="complete"
    ).order_by(TrainingTestVersion.version_number.desc()).first()
    if version is None:
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=(db.query(TrainingTestVersion)
                            .filter_by(test_id=test.id)
                            .count() or 0) + 1,
            content_sha256=content_sha256,
            ground_truth={
                "sample_class": sample_class,
                "domain": domain,
                "fault_type": fault_type,
                "label": sample_class,
            },
            fip_info={"FAULT_TYPE": fault_type} if fault_type else {},
            completeness="complete",
            missing_files=None,
            import_id=import_id,
            sample_class=sample_class,
            domain=domain,
            fault_type=fault_type,
        )
        db.add(version)
        db.flush()
        test.latest_version_id = version.id
    else:
        # 已有 complete 版本：只是追加另一个 round，更新合并后的 sha(round_1+round_2)
        pass

    # 写/更新该 round 的 TrainingTestLog(同 round+log_type 唯一)
    existing_log = db.query(TrainingTestLog).filter_by(
        test_version_id=version.id, round_no=round_no, log_type=_LOG_TYPE
    ).first()
    if existing_log is not None:
        existing_log.content = log_content or ""
        existing_log.byte_count = len(content_bytes)
    else:
        db.add(TrainingTestLog(
            test_version_id=version.id,
            round_no=round_no,
            log_type=_LOG_TYPE,
            content=log_content or "",
            byte_count=len(content_bytes),
        ))

    # 合并 round 后的最新 content_sha(round_1+round_2)，使 latest 版本 content 反映完整
    all_logs = db.query(TrainingTestLog).filter_by(test_version_id=version.id).all()
    combined = "".join(sorted((lg.content or "") for lg in all_logs)).encode("utf-8")
    version.content_sha256 = hashlib.sha256(combined).hexdigest()

    test.latest_version_id = version.id
    db.flush()
