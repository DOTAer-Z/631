import unittest

from app.services.log_dataset_service import (
    build_run_filters,
    parse_stats_json,
    shape_run_row,
    shape_window_doc,
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


if __name__ == "__main__":
    unittest.main()
