"""annotated_run_service 的纯逻辑单测：schema 校验契约 + 时间戳换算。

不依赖数据库 / 外部 LLM / 运行中的容器。Epoch→UTC 换算与 log_ingest_service
一致（UTC naive datetime），这里固定断言，防止桥接层时间表达漂移。
"""

import unittest
from datetime import datetime, timezone

from app.schemas.log_analysis import (
    AnnotatedRunImportRequest,
    AnnotatedRunFile,
    AnnotatedRunLine,
)
from app.services.annotated_run_service import _epoch_to_utc, _is_error_like


class AnnotatedRunSchemaTests(unittest.TestCase):
    def test_request_validates_and_normalizes_defaults(self):
        req = AnnotatedRunImportRequest(
            run_id="annot_pkg1_win5",
            test_name="pkg-name",
            lines=[
                AnnotatedRunFile(
                    logical_path="cpu0/app.log",
                    lines=[
                        AnnotatedRunLine(line_no=1, content="INFO boot", timestamp=1735689600.0),
                        AnnotatedRunLine(line_no=2, content="ERROR boom", timestamp=None),
                    ],
                )
            ],
        )
        self.assertEqual(req.source_type, "annotation")
        self.assertEqual(req.source_type or "annotation", "annotation")
        self.assertIsNone(req.is_fault)
        # 未传字段都有默认
        self.assertIsNone(req.fault_type)

    def test_request_rejects_missing_required_fields(self):
        # 缺必填字段（run_id / test_name / lines）由 pydantic 抛 ValidationError
        with self.assertRaises(Exception):
            AnnotatedRunImportRequest(run_id="only_id")

    def test_service_rejects_blank_run_id(self):
        # 空字符串 pydantic 放行，但 service 层负责兜底（与 import_annotated_run 一致）
        from app.services.annotated_run_service import import_annotated_run

        req = AnnotatedRunImportRequest(run_id="   ", test_name="t", lines=[])
        with self.assertRaises(ValueError):
            import_annotated_run(db=None, mongo_db=None, payload=req)


class AnnotatedRunTimeTests(unittest.TestCase):
    def test_epoch_to_utc_converts_and_strips_tz(self):
        ts = 1735689600.0  # 2025-01-01 00:00:00 UTC
        dt = _epoch_to_utc(ts)
        self.assertEqual(dt, datetime(2025, 1, 1, 0, 0, 0))
        self.assertIsNone(dt.tzinfo)  # 与 log_ingest_service 一致：UTC naive

    def test_epoch_to_utc_handles_none_and_bad_value(self):
        self.assertIsNone(_epoch_to_utc(None))
        self.assertIsNone(_epoch_to_utc(float("nan")))
        self.assertIsNone(_epoch_to_utc(1e300))  # OverflowError -> None

    def test_is_error_like_uses_keyword_fallback(self):
        self.assertTrue(_is_error_like("EXCEPTION at 0x007f"))
        self.assertTrue(_is_error_like("connection refused"))
        self.assertTrue(_is_error_like("task timeout"))
        self.assertFalse(_is_error_like("INFO starting normally"))


if __name__ == "__main__":
    unittest.main()
