import io
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.api.v1 import data_import
from app.models.run import Run
from app.models.training_data import (
    TrainingImportItem,
    TrainingTest,
    TrainingTestLog,
    TrainingTestVersion,
)
from app.services import data_import_ingest_service as ingest_service


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "training_test" / "Test_1"


class _BackgroundDb:
    def __init__(self, row):
        self.row = row
        self.commit_count = 0
        self.closed = False

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self.row

    def all(self):
        return []

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class TrainingDataImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE dataset_imports (import_id VARCHAR(64) PRIMARY KEY)")
            )
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTestLog.__table__,
            TrainingImportItem.__table__,
            Run.__table__,
        ):
            table.create(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _add_import(self, import_id="import-1"):
        session = self.Session()
        try:
            session.execute(
                text("INSERT INTO dataset_imports (import_id) VALUES (:import_id)"),
                {"import_id": import_id},
            )
            session.commit()
        finally:
            session.close()

    def _zip_archive(self, *test_names: str, incomplete=False) -> Path:
        archive = self.root / "dataset.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for test_name in test_names:
                for source in FIXTURE_DIR.rglob("*"):
                    if not source.is_file():
                        continue
                    relative_path = source.relative_to(FIXTURE_DIR)
                    if incomplete and relative_path == Path("logs/round_2/monitor/system_metrics.log"):
                        continue
                    zf.writestr(f"{test_name}/{relative_path.as_posix()}", source.read_bytes())
        return archive

    def _run_pipeline(
        self,
        archive,
        parser_side_effect=None,
        session=None,
        import_id="import-1",
    ):
        session = session or self.Session()
        dataset_module = SimpleNamespace(
            ensure_system=mock.Mock(),
            ingest_test_dir=mock.Mock(side_effect=parser_side_effect or self._parsed_summary),
        )
        with (
            mock.patch("app.database.SessionLocal", return_value=session),
            mock.patch("app.database.get_mongo_db", return_value=mock.Mock()),
            mock.patch.dict(sys.modules, {"ingest_dataset": dataset_module}),
        ):
            result = ingest_service.run_ingest_pipeline(
                archive,
                self.root / "extract",
                import_id,
            )
        return result, dataset_module

    @staticmethod
    def _parsed_summary(*_args, **kwargs):
        return {
            "case_id": "nuttx_Test_1",
            "case_is_new": True,
            "runs": [],
        }

    def _reopen(self):
        return self.Session()

    def test_pipeline_commits_complete_version_before_parser_and_forwards_version_id(self):
        self._add_import()
        archive = self._zip_archive("Test_1")

        result, dataset_module = self._run_pipeline(archive)

        self.assertEqual(result.training_complete_count, 1)
        self.assertEqual(result.test_results[0]["parse_status"], "parsed")
        version_id = result.test_results[0]["test_version_id"]
        self.assertEqual(dataset_module.ingest_test_dir.call_args.kwargs["test_version_id"], version_id)
        session = self._reopen()
        try:
            version = session.get(TrainingTestVersion, version_id)
            item = session.query(TrainingImportItem).filter_by(import_id="import-1").one()
            self.assertIsNotNone(version)
            self.assertEqual(item.parse_status, "parsed")
            self.assertIsNone(item.error_message)
        finally:
            session.close()

    def test_incomplete_version_and_skipped_parse_status_survive_pipeline_return(self):
        self._add_import()
        archive = self._zip_archive("Test_incomplete", incomplete=True)

        result, dataset_module = self._run_pipeline(archive)

        self.assertEqual(result.training_incomplete_count, 1)
        self.assertEqual(result.test_results[0]["parse_status"], "skipped_incomplete")
        dataset_module.ingest_test_dir.assert_not_called()
        session = self._reopen()
        try:
            version = session.query(TrainingTestVersion).one()
            item = session.query(TrainingImportItem).filter_by(import_id="import-1").one()
            self.assertEqual(version.completeness, "incomplete")
            self.assertEqual(version.round_1_parse_status, "skipped_incomplete")
            self.assertEqual(version.round_2_parse_status, "skipped_incomplete")
            self.assertEqual(item.parse_status, "skipped_incomplete")
            self.assertIsNone(item.error_message)
        finally:
            session.close()

    def test_complete_version_and_failed_parse_outcome_survive_parser_error(self):
        self._add_import()
        archive = self._zip_archive("Test_1")

        def parse_first_round_then_fail(db, _mongo_db, **kwargs):
            db.add(
                Run(
                    run_id="nuttx_Test_1_round_1",
                    source_type="dataset",
                    test_version_id=kwargs["test_version_id"],
                    round_no=1,
                )
            )
            db.commit()
            raise RuntimeError("parser exploded")

        result, dataset_module = self._run_pipeline(
            archive,
            parser_side_effect=parse_first_round_then_fail,
        )

        self.assertEqual(result.training_complete_count, 1)
        self.assertEqual(result.training_parse_failed_count, 1)
        self.assertEqual(result.archive_status, "partial_success")
        dataset_module.ingest_test_dir.assert_called_once()
        session = self._reopen()
        try:
            version = session.query(TrainingTestVersion).one()
            item = session.query(TrainingImportItem).filter_by(import_id="import-1").one()
            self.assertEqual(version.completeness, "complete")
            self.assertEqual(version.round_1_parse_status, "parsed")
            self.assertEqual(version.round_2_parse_status, "failed")
            self.assertEqual(item.parse_status, "failed")
            self.assertIn("parser exploded", item.error_message)
        finally:
            session.close()

    def test_sqlalchemy_error_from_versioning_propagates_without_partial_result(self):
        self._add_import()
        archive = self._zip_archive("Test_1")
        session = self.Session()

        with mock.patch.object(
            ingest_service,
            "import_test_version",
            side_effect=SQLAlchemyError("version database unavailable"),
        ):
            with self.assertRaisesRegex(SQLAlchemyError, "version database unavailable"):
                self._run_pipeline(archive, session=session)

        session = self._reopen()
        try:
            self.assertEqual(session.query(TrainingTestVersion).count(), 0)
        finally:
            session.close()

    def test_sqlalchemy_error_from_parser_propagates_after_version_commit(self):
        self._add_import()
        archive = self._zip_archive("Test_1")

        with self.assertRaisesRegex(SQLAlchemyError, "parser database unavailable"):
            self._run_pipeline(
                archive,
                parser_side_effect=SQLAlchemyError("parser database unavailable"),
            )

        session = self._reopen()
        try:
            version = session.query(TrainingTestVersion).one()
            item = session.query(TrainingImportItem).filter_by(import_id="import-1").one()
            self.assertEqual(version.completeness, "complete")
            self.assertIsNone(item.parse_status)
        finally:
            session.close()

    def test_version_boundary_commit_failure_rolls_back_without_persisting_and_later_import_works(self):
        self._add_import()
        self._add_import("import-after-commit-failure")
        archive = self._zip_archive("Test_1")
        session = self.Session()
        rollback_count = 0
        should_fail = True

        def fail_first_commit(_session):
            nonlocal should_fail
            if should_fail:
                should_fail = False
                raise SQLAlchemyError("commit failed")

        def count_rollback(_session):
            nonlocal rollback_count
            rollback_count += 1

        event.listen(session, "before_commit", fail_first_commit)
        event.listen(session, "after_rollback", count_rollback)
        try:
            with self.assertRaisesRegex(SQLAlchemyError, "commit failed"):
                self._run_pipeline(archive, session=session)
        finally:
            event.remove(session, "before_commit", fail_first_commit)
            event.remove(session, "after_rollback", count_rollback)
            session.close()

        self.assertGreaterEqual(rollback_count, 1)
        session = self._reopen()
        try:
            self.assertEqual(session.query(TrainingTestVersion).count(), 0)
            self.assertEqual(session.query(TrainingImportItem).count(), 0)
        finally:
            session.close()

        result, _ = self._run_pipeline(
            archive,
            import_id="import-after-commit-failure",
        )
        self.assertEqual(result.training_complete_count, 1)
        session = self._reopen()
        try:
            self.assertEqual(session.query(TrainingTestVersion).count(), 1)
            self.assertEqual(
                session.query(TrainingImportItem)
                .filter_by(import_id="import-after-commit-failure")
                .count(),
                1,
            )
        finally:
            session.close()

    def test_zip_symlink_is_rejected_before_extracting_members(self):
        archive = self.root / "unsafe.zip"
        link = zipfile.ZipInfo("Test_1/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(link, "target")

        destination = self.root / "zip-extract"
        with self.assertRaisesRegex(ingest_service.IngestPipelineError, "符号链接"):
            ingest_service.extract_archive(archive, destination)

        self.assertEqual(list(destination.iterdir()), [])

    def test_tar_special_file_is_rejected_before_extracting_members(self):
        archive = self.root / "unsafe.tar"
        with tarfile.open(archive, "w") as tf:
            fifo = tarfile.TarInfo("Test_1/pipe")
            fifo.type = tarfile.FIFOTYPE
            tf.addfile(fifo)

        destination = self.root / "tar-extract"
        with self.assertRaisesRegex(ingest_service.IngestPipelineError, "特殊文件"):
            ingest_service.extract_archive(archive, destination)

        self.assertEqual(list(destination.iterdir()), [])

    def test_tar_extracts_valid_members_with_python_311_extractall_signature(self):
        archive = self.root / "valid.tar"
        payload = b"valid tar payload\n"
        member = tarfile.TarInfo("Test_1/ground_truth.json")
        member.size = len(payload)
        with tarfile.open(archive, "w") as tf:
            tf.addfile(member, fileobj=io.BytesIO(payload))

        original_extractall = tarfile.TarFile.extractall

        def extractall_python_311(self, path=".", members=None, *, numeric_owner=False):
            return original_extractall(
                self,
                path,
                members=members,
                numeric_owner=numeric_owner,
            )

        destination = self.root / "tar-extract"
        with mock.patch.object(
            tarfile.TarFile,
            "extractall",
            new=extractall_python_311,
        ):
            ingest_service.extract_archive(archive, destination)

        self.assertEqual(
            (destination / "Test_1" / "ground_truth.json").read_bytes(),
            payload,
        )

    def test_background_task_persists_partial_success_and_training_counters(self):
        row = self._background_row()
        result = ingest_service.IngestResult(
            case_ids=[], run_count=0, entry_count=0, window_count=0,
            training_complete_count=1, training_parse_failed_count=1,
        )
        db = _BackgroundDb(row)

        with (
            mock.patch.object(data_import, "SessionLocal", return_value=db),
            mock.patch.object(data_import, "run_ingest_pipeline", return_value=result),
        ):
            data_import._run_ingest_in_background("import-1", str(self.root / "dataset.zip"))

        self.assertEqual(row.ingest_status, "partial_success")
        self.assertEqual(row.training_complete_count, 1)
        self.assertEqual(row.training_parse_failed_count, 1)
        self.assertGreaterEqual(db.commit_count, 2)
        self.assertTrue(db.closed)

    def test_background_task_marks_sqlalchemy_error_as_failed(self):
        row = self._background_row()
        db = _BackgroundDb(row)

        with (
            mock.patch.object(data_import, "SessionLocal", return_value=db),
            mock.patch.object(
                data_import,
                "run_ingest_pipeline",
                side_effect=SQLAlchemyError("database unavailable"),
            ),
        ):
            data_import._run_ingest_in_background("import-1", str(self.root / "dataset.zip"))

        self.assertEqual(row.ingest_status, "failed")
        self.assertIn("database unavailable", row.ingest_error)
        self.assertTrue(db.closed)

    @staticmethod
    def _background_row():
        return SimpleNamespace(
            import_id="import-1",
            original_filename="dataset.zip",
            display_name=None,
            description=None,
            tags_json=[],
            ingest_status="pending",
            ingest_error=None,
            ingested_at=None,
            ingested_case_ids=None,
            ingested_run_count=0,
            ingested_entry_count=0,
            ingested_new_case_count=0,
            ingested_updated_case_count=0,
            ingested_new_run_count=0,
            ingested_updated_run_count=0,
            training_complete_count=0,
            training_incomplete_count=0,
            training_duplicate_count=0,
            training_failed_count=0,
            training_parse_failed_count=0,
        )


if __name__ == "__main__":
    unittest.main()
