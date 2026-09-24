import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.case import Case
from app.models.fault_type import FaultType
from app.models.run import Run
from app.models.training_data import TrainingTest, TrainingTestVersion
from app.services.log_dataset_service import (
    build_run_filters,
    parse_stats_json,
    shape_run_row,
    shape_window_doc,
    update_run,
)


class LogDatasetServiceTests(unittest.TestCase):
    def test_build_run_filters_escapes_like_patterns_and_fault_status(self):
        where_sql, params = build_run_filters(
            run_id="nuttx_NuttX_Test_1030_round_2%raw",
            case_id="nuttx_NuttX_Test_1030_100%",
            test_name="Test_1030_%",
            fault_status="fault",
            system_id="nuttx",
        )

        self.assertIn("r.system_id = :system_id", where_sql)
        self.assertIn("r.run_id LIKE :run_id ESCAPE '\\\\'", where_sql)
        self.assertIn("r.case_id LIKE :case_id ESCAPE '\\\\'", where_sql)
        self.assertIn("c.test_name LIKE :test_name ESCAPE '\\\\'", where_sql)
        self.assertNotIn("ESCAPE '\\'", where_sql)
        self.assertIn("r.is_fault = 1", where_sql)
        self.assertEqual(params["system_id"], "nuttx")
        self.assertEqual(
            params["run_id"],
            r"%nuttx\_NuttX\_Test\_1030\_round\_2\%raw%",
        )
        self.assertEqual(
            params["case_id"],
            r"%nuttx\_NuttX\_Test\_1030\_100\%%",
        )
        self.assertEqual(params["test_name"], r"%Test\_1030\_\%%")

    def test_parse_stats_json_handles_string_payload(self):
        stats = parse_stats_json(
            '{"parsed_lines": 240, "level_distribution": {"ERROR": 12}}'
        )

        self.assertEqual(stats["parsed_lines"], 240)
        self.assertEqual(stats["level_distribution"]["ERROR"], 12)

    def test_shape_run_row_exposes_dataset_browser_fields(self):
        item = shape_run_row(
            {
                "run_id": "nuttx_NuttX_Test_1030_round_2",
                "case_id": "nuttx_NuttX_Test_1030",
                "run_name": "nuttx_NuttX_Test_1030_round_2",
                "system_id": "nuttx",
                "test_name": "Test_1030",
                "case_name": "nuttx_NuttX_Test_1030",
                "fault_type": "deadlock",
                "round_no": 2,
                "is_fault": 1,
                "error_logs": 12,
                "critical_logs": 3,
                "window_count": 5,
                "created_at": "2026-05-25T00:00:00",
                "stats_json": '{"parsed_lines": 240, "total_lines": 300}',
            }
        )

        self.assertEqual(item["run_id"], "nuttx_NuttX_Test_1030_round_2")
        self.assertEqual(item["test_name"], "Test_1030")
        self.assertEqual(item["fault_status"], "fault")
        self.assertEqual(item["parsed_lines"], 240)
        self.assertEqual(item["window_count"], 5)

    def test_shape_window_doc_truncates_preview_and_keeps_error_count(self):
        item = shape_window_doc(
            {
                "window_id": "nuttx_w_1",
                "start_time": "2026-05-25T00:00:00",
                "end_time": "2026-05-25T00:01:00",
                "strategy": "error",
                "text": "A" * 500,
                "stats": {"n_entries": 18, "error_events": 2},
                "key_events": [{"message": "panic: scheduler lock held"}],
            }
        )

        self.assertEqual(item["window_id"], "nuttx_w_1")
        self.assertEqual(item["entry_count"], 18)
        self.assertEqual(item["error_events"], 2)
        self.assertTrue(item["text_preview"].endswith("..."))


class LogDatasetTrainingLabelSyncTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for table in (
            FaultType.__table__,
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            Case.__table__,
            Run.__table__,
        ):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _version(self, platform: str, test_name: str) -> TrainingTestVersion:
        test = TrainingTest(platform=platform, test_name=test_name)
        self.db.add(test)
        self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=1,
            content_sha256=(platform + test_name).encode().hex().ljust(64, "0")[:64],
            ground_truth={"sample_class": "fault", "fault_type": None},
            fip_info={},
            completeness="complete",
            import_id=f"import-{platform}-{test_name}",
            sample_class="fault",
            domain="runtime",
            fault_type=None,
        )
        self.db.add(version)
        self.db.flush()
        test.latest_version_id = version.id
        return version

    def test_file_browser_fault_type_syncs_structured_training_version(self):
        fault_type = FaultType(name="scheduler_deadlock", color_tag="red")
        version = self._version("nuttx", "Test_50005")
        case = Case(case_id="nuttx_Test_50005", name="Test_50005", is_fault=True)
        run = Run(
            run_id="Test_50005:round_1:qemu_console",
            source_type="dataset",
            system_id="nuttx",
            case_id=case.case_id,
            test_name="Test_50005",
            round_no=1,
            is_fault=True,
        )
        self.db.add_all([fault_type, case, run])
        self.db.commit()

        result = update_run(
            self.db,
            run_id=run.run_id,
            payload={"fault_type_id": fault_type.id},
        )

        self.db.refresh(version)
        self.assertIn("training_label", result["updated_fields"])
        self.assertEqual(version.fault_type, "scheduler_deadlock")
        self.assertEqual(version.ground_truth["fault_type"], "scheduler_deadlock")
        self.assertEqual(version.fip_info["FAULT_TYPE"], "scheduler_deadlock")
        self.assertEqual(run.test_version_id, version.id)

    def test_file_browser_edit_does_not_mutate_legacy_semi_structured_version(self):
        fault_type = FaultType(name="new_label", color_tag="red")
        version = self._version("NuttX", "Test_legacy")
        version.fault_type = "original_label"
        version.ground_truth = {"sample_class": "fault", "fault_type": "original_label"}
        case = Case(case_id="legacy_case", name="legacy", is_fault=True)
        run = Run(
            run_id="legacy_run",
            source_type="dataset",
            system_id="nuttx",
            case_id=case.case_id,
            test_name="Test_legacy",
            test_version_id=version.id,
            is_fault=True,
        )
        self.db.add_all([fault_type, case, run])
        self.db.commit()

        result = update_run(
            self.db,
            run_id=run.run_id,
            payload={"fault_type_id": fault_type.id},
        )

        self.db.refresh(version)
        self.assertNotIn("training_label", result["updated_fields"])
        self.assertEqual(version.fault_type, "original_label")
        self.assertEqual(version.ground_truth["fault_type"], "original_label")


if __name__ == "__main__":
    unittest.main()
