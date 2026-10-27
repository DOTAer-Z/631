import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.training_data import (
    TrainingImportItem,
    TrainingTest,
    TrainingTestLog,
    TrainingTestVersion,
)
from app.services import training_data_service as service


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "training_test" / "Test_1"


class TrainingDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name) / "Test_1"
        shutil.copytree(FIXTURE_DIR, self.test_dir)

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
        ):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _add_import(self, import_id: str) -> None:
        self.db.execute(
            text("INSERT INTO dataset_imports (import_id) VALUES (:import_id)"),
            {"import_id": import_id},
        )

    def test_complete_import_persists_labels_and_exactly_six_required_logs(self):
        self._add_import("import-1")

        result = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-1",
            platform="NuttX",
        )

        self.assertEqual(result.status, "imported")
        self.assertEqual(result.completeness, "complete")
        version = self.db.get(TrainingTestVersion, result.version_id)
        self.assertEqual(version.ground_truth["sample_class"], "fault")
        self.assertEqual(version.fip_info["FAULT_TYPE"], "watchdog_timeout")
        self.assertEqual(version.missing_files, None)
        logs = self.db.query(TrainingTestLog).filter_by(test_version_id=version.id).all()
        self.assertEqual(len(logs), 6)
        self.assertEqual(
            {(log.round_no, log.log_type) for log in logs},
            set(service.REQUIRED_LOG_PATHS),
        )
        training_test = self.db.get(TrainingTest, version.test_id)
        self.assertEqual(training_test.latest_version_id, version.id)

    def test_missing_required_file_creates_incomplete_version_with_available_data(self):
        self._add_import("import-2")
        missing_path = self.test_dir / service.REQUIRED_LOG_PATHS[(2, "system_metrics")]
        missing_path.unlink()

        result = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-2",
            platform="NuttX",
        )

        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.completeness, "incomplete")
        version = self.db.get(TrainingTestVersion, result.version_id)
        self.assertEqual(version.missing_files, ["logs/round_2/monitor/system_metrics.log"])
        self.assertEqual(
            self.db.query(TrainingTestLog).filter_by(test_version_id=version.id).count(),
            5,
        )

    def test_invalid_ground_truth_json_records_failed_item_and_raises_validation_error(self):
        self._add_import("import-3")
        (self.test_dir / "ground_truth.json").write_text(
            '{"sample_class": NaN}', encoding="utf-8"
        )

        with self.assertRaises(service.TestDataValidationError):
            service.import_test_version(
                self.db,
                test_dir=self.test_dir,
                import_id="import-3",
                platform="NuttX",
            )

        item = self.db.query(TrainingImportItem).filter_by(import_id="import-3").one()
        self.assertEqual(item.status, "failed")
        self.assertIn("ground_truth.json", item.error_message)
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 0)

    def test_fip_info_uses_only_key_value_lines(self):
        fip_info = self.test_dir / "fip_info.data"
        fip_info.write_text(
            "FAULT_TYPE: watchdog_timeout\nthis line is ignored\n",
            encoding="utf-8",
        )

        validation = service.validate_test_dir(self.test_dir)

        self.assertEqual(validation.fip_info, {"FAULT_TYPE": "watchdog_timeout"})

    def test_validation_uses_required_label_paths_as_the_label_file_source(self):
        with mock.patch.object(service, "REQUIRED_LABEL_PATHS", ("ground_truth.json",)):
            validation = service.validate_test_dir(self.test_dir)

        self.assertEqual(
            set(validation.available_files),
            {"ground_truth.json", *service.REQUIRED_LOG_PATHS.values()},
        )
        self.assertNotIn("fip_info.data", validation.available_files)
        self.assertNotIn("fip_info.data", validation.missing_files)
        self.assertEqual(validation.fip_info, {})

    def test_same_content_reuses_existing_version_and_records_duplicate_item(self):
        self._add_import("import-4")
        self._add_import("import-5")
        first = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-4",
            platform="NuttX",
        )
        second = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-5",
            platform="NuttX",
        )

        self.assertEqual(second.status, "duplicate")
        self.assertEqual(second.version_id, first.version_id)
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 1)
        self.assertEqual(self.db.query(TrainingTest).count(), 1)
        item = self.db.query(TrainingImportItem).filter_by(import_id="import-5").one()
        self.assertEqual(item.status, "duplicate")
        self.assertEqual(item.test_version_id, first.version_id)

    def test_postgresql_path_acquires_advisory_lock_before_test_lookup(self):
        self._add_import("import-advisory")
        executed_statements = []
        original_execute = self.db.execute

        def execute(statement, *args, **kwargs):
            executed_statements.append(statement)
            if "pg_advisory_xact_lock" in str(statement):
                return mock.Mock()
            return original_execute(statement, *args, **kwargs)

        with (
            mock.patch.object(service, "_is_postgresql", create=True, return_value=True),
            mock.patch.object(self.db, "execute", side_effect=execute),
        ):
            result = service.import_test_version(
                self.db,
                test_dir=self.test_dir,
                import_id="import-advisory",
                platform="NuttX",
            )

        self.assertEqual(result.status, "imported")
        self.assertIn("pg_advisory_xact_lock", str(executed_statements[0]))
        self.assertIn(
            str(service._advisory_lock_key("NuttX", "Test_1")),
            str(executed_statements[0].compile(compile_kwargs={"literal_binds": True})),
        )

    def test_hash_read_failure_records_failed_item_without_persisting_version(self):
        self._add_import("import-hash-read-failure")

        with (
            mock.patch.object(
                service,
                "validate_test_dir",
                wraps=service.validate_test_dir,
            ) as validate,
            mock.patch.object(Path, "read_bytes", side_effect=OSError("disk unavailable")),
            self.assertRaisesRegex(OSError, "disk unavailable"),
        ):
            service.import_test_version(
                self.db,
                test_dir=self.test_dir,
                import_id="import-hash-read-failure",
                platform="NuttX",
            )

        validate.assert_called_once_with(self.test_dir)
        item = (
            self.db.query(TrainingImportItem)
            .filter_by(import_id="import-hash-read-failure")
            .one()
        )
        self.assertEqual(item.status, "failed")
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 0)
        self.assertEqual(self.db.query(TrainingTestLog).count(), 0)

    def test_invalid_log_encoding_records_failed_item_without_partial_rows(self):
        self._add_import("import-log-decode-failure")
        (self.test_dir / service.REQUIRED_LOG_PATHS[(1, "qemu_console")]).write_bytes(b"\xff")

        with self.assertRaises(UnicodeDecodeError):
            service.import_test_version(
                self.db,
                test_dir=self.test_dir,
                import_id="import-log-decode-failure",
                platform="NuttX",
            )

        item = (
            self.db.query(TrainingImportItem)
            .filter_by(import_id="import-log-decode-failure")
            .one()
        )
        self.assertEqual(item.status, "failed")
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 0)
        self.assertEqual(self.db.query(TrainingTestLog).count(), 0)

    def test_changed_content_creates_next_version_and_updates_latest_pointer(self):
        self._add_import("import-6")
        self._add_import("import-7")
        first = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-6",
            platform="NuttX",
        )
        target = self.test_dir / service.REQUIRED_LOG_PATHS[(1, "qemu_console")]
        target.write_text("changed console log\n", encoding="utf-8")

        second = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-7",
            platform="NuttX",
        )

        self.assertEqual(second.status, "imported")
        self.assertEqual(second.version_number, 2)
        training_test = self.db.query(TrainingTest).filter_by(test_name="Test_1").one()
        self.assertEqual(training_test.latest_version_id, second.version_id)
        self.assertNotEqual(second.version_id, first.version_id)

    def test_hash_excludes_foreground_workload_files(self):
        validation = service.validate_test_dir(self.test_dir)
        before = service.compute_test_sha256(validation)
        foreground = self.test_dir / "logs" / "round_1" / "foreground_wl" / "load.log"
        foreground.parent.mkdir(parents=True)
        foreground.write_text("not training data\n", encoding="utf-8")

        after = service.compute_test_sha256(service.validate_test_dir(self.test_dir))

        self.assertEqual(before, after)

    def test_flush_failure_rolls_back_savepoint_records_failed_item_and_keeps_session_usable(self):
        self._add_import("import-flush-failure")
        self._add_import("import-after-flush-failure")
        failed_dir = Path(self.temp_dir.name) / "Test_flush_failure"
        shutil.copytree(FIXTURE_DIR, failed_dir)

        def make_log_content_null(session, flush_context, instances):
            for instance in session.new:
                if isinstance(instance, TrainingTestLog):
                    instance.content = None

        event.listen(self.db, "before_flush", make_log_content_null)
        try:
            with self.assertRaises(IntegrityError):
                service.import_test_version(
                    self.db,
                    test_dir=failed_dir,
                    import_id="import-flush-failure",
                    platform="NuttX",
                )
        finally:
            event.remove(self.db, "before_flush", make_log_content_null)

        item = (
            self.db.query(TrainingImportItem)
            .filter_by(import_id="import-flush-failure")
            .one()
        )
        self.assertEqual(item.status, "failed")
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 0)
        self.assertEqual(self.db.query(TrainingTestLog).count(), 0)

        imported = service.import_test_version(
            self.db,
            test_dir=self.test_dir,
            import_id="import-after-flush-failure",
            platform="NuttX",
        )

        self.assertEqual(imported.status, "imported")
        self.assertEqual(self.db.query(TrainingTestVersion).count(), 1)


if __name__ == "__main__":
    unittest.main()
