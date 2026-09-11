from types import SimpleNamespace
import time
import unittest
from unittest import mock

from app.api.v1.log_analysis import _analyze_text, _parse_stored_log
from app.config import settings
from app.services.log_parse_errors import LogParseTimedOut


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_calls = []
        self.delete_calls = []

    def find(self, *args, **kwargs):
        return FakeCursor(self.rows)

    def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update, upsert))

    def delete_many(self, filt):
        self.delete_calls.append(filt)


class FakeDocumentDatabase(dict):
    def __getitem__(self, name):
        return super().__getitem__(name)


class FakeControl:
    def __init__(self):
        self.deadline = time.monotonic() + 60
        self.checkpoints = []
        self.persisting = []
        self.llm_progress = []

    def checkpoint(self, stage, progress):
        self.checkpoints.append((stage, progress))

    def begin_persisting(self, progress=95):
        self.persisting.append(progress)

    def should_cancel(self):
        return False

    def on_llm_progress(self, received, budget):
        self.llm_progress.append((received, budget))


def stored_database():
    return FakeDocumentDatabase(
        log_entries=FakeCollection(
            [{"raw_line": "ERROR panic", "file_path": "/logs/system.log"}]
        ),
        log_analysis_results=FakeCollection(
            [{"run_id": "run-1", "result": {"summary": "previous success"}}]
        ),
        log_uploads=FakeCollection([{"run_id": "run-1", "analyzed": True}]),
    )


class LogParseExecutionTests(unittest.TestCase):
    def test_timeout_configuration_is_sixty_seconds(self):
        self.assertEqual(settings.LOG_PARSE_TIMEOUT_SECONDS, 60)
        self.assertEqual(settings.LOG_PARSE_WORKERS, 1)

    def test_analyze_text_does_not_downgrade_cancellation_or_timeout(self):
        document_db = FakeDocumentDatabase(
            log_entries=FakeCollection(),
            log_windows=FakeCollection(),
        )
        preprocess = SimpleNamespace(
            text="ERROR panic",
            detected_format="plain",
            encoding="utf-8",
        )
        ingest = SimpleNamespace(level_distribution={"ERROR": 1}, total_lines=1)
        score = SimpleNamespace(
            has_fault=True,
            fault_score=8.0,
            confidence=0.8,
            summary="rule",
            next_action="fault_location",
            evidence=[],
            score_breakdown={},
        )
        llm = mock.Mock()
        llm.parse_log.side_effect = LogParseTimedOut("日志解析超过 60 秒")

        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=document_db),
            mock.patch(
                "app.api.v1.log_analysis.preprocessing_service.preprocess",
                return_value=preprocess,
            ),
            mock.patch(
                "app.api.v1.log_analysis.log_ingest_service.ingest",
                return_value=ingest,
            ),
            mock.patch("app.api.v1.log_analysis.log_window_service.build_windows"),
            mock.patch(
                "app.api.v1.log_analysis.fault_scorer.score", return_value=score
            ),
            mock.patch("app.api.v1.log_analysis._get_llm_for_analysis", return_value=llm),
        ):
            with self.assertRaises(LogParseTimedOut):
                _analyze_text(
                    "ERROR panic",
                    "system.log",
                    run_id="run-1",
                    control=FakeControl(),
                )

    def test_failed_reparse_keeps_previous_successful_result(self):
        document_db = stored_database()
        control = FakeControl()
        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=document_db),
            mock.patch(
                "app.api.v1.log_analysis._analyze_text",
                side_effect=LogParseTimedOut("日志解析超过 60 秒"),
            ),
        ):
            with self.assertRaises(LogParseTimedOut):
                _parse_stored_log("run-1", control)

        self.assertEqual(
            document_db["log_analysis_results"].rows[0]["result"]["summary"],
            "previous success",
        )
        self.assertEqual(document_db["log_analysis_results"].update_calls, [])
        self.assertEqual(control.persisting, [])

    def test_successful_reparse_upserts_latest_result_after_commit_boundary(self):
        document_db = stored_database()
        control = FakeControl()
        result = SimpleNamespace(
            run_id="run-1",
            has_fault=True,
            fault_score=9.0,
            model_dump=lambda: {"run_id": "run-1", "summary": "new success"},
        )
        with (
            mock.patch("app.api.v1.log_analysis.get_mongo_db", return_value=document_db),
            mock.patch("app.api.v1.log_analysis._analyze_text", return_value=result),
        ):
            returned = _parse_stored_log("run-1", control)

        self.assertIs(returned, result)
        self.assertEqual(control.persisting, [95])
        result_update = document_db["log_analysis_results"].update_calls[0]
        self.assertEqual(result_update[0], {"run_id": "run-1"})
        self.assertTrue(result_update[2])
        self.assertEqual(
            result_update[1]["$set"]["result"]["summary"], "new success"
        )
        self.assertTrue(
            document_db["log_uploads"].update_calls[0][1]["$set"]["analyzed"]
        )


if __name__ == "__main__":
    unittest.main()
