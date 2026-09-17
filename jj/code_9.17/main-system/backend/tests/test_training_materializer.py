import json
import shutil
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.training_data import TrainingImportItem, TrainingTest, TrainingTestLog, TrainingTestVersion
from app.models.training_task import TrainingArtifact, TrainingTask, TrainingTaskTest
from app.services.training_data_service import import_test_version
from app.training_worker import materializer
from app.training_worker.materializer import (
    MaterializationError,
    load_frozen_samples,
    materialize_task,
)
from embedded_fault_diag.build_cpt import build_cpt_rows
from embedded_fault_diag.build_sft_open import build_sft_open_rows
from embedded_fault_diag.data import load_sample


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "training_test" / "Test_1"


class TrainingMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE dataset_imports (import_id VARCHAR(64) PRIMARY KEY)"))
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTestLog.__table__,
            TrainingArtifact.__table__,
            TrainingTask.__table__,
            TrainingTaskTest.__table__,
            TrainingImportItem.__table__,
        ):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _import_fixture(self, test_name: str, *, fault_type: str | None = None) -> tuple[int, Path]:
        test_dir = self.root / test_name
        shutil.copytree(FIXTURE_DIR, test_dir)
        if fault_type is not None:
            ground_truth_path = test_dir / "ground_truth.json"
            ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
            ground_truth["fault_type"] = fault_type
            ground_truth_path.write_text(json.dumps(ground_truth), encoding="utf-8")
            (test_dir / "fip_info.data").write_text(
                f"FAULT_TYPE: {fault_type}\n", encoding="utf-8"
            )
        import_id = f"import-{test_name}"
        self.db.execute(
            text("INSERT INTO dataset_imports (import_id) VALUES (:import_id)"),
            {"import_id": import_id},
        )
        imported = import_test_version(
            self.db,
            test_dir=test_dir,
            import_id=import_id,
            platform="NuttX",
        )
        self.db.commit()
        return imported.version_id, test_dir

    def _task(self, task_type: str, splits: dict[str, int]) -> TrainingTask:
        task = TrainingTask(
            id=f"{task_type}-task",
            name=f"{task_type} materialization",
            task_type=task_type,
            job_kind="training",
            state="preparing_data",
            model_id="qwen-qwen3.5-9b",
            config_snapshot={"api_key": "do-not-write-this"},
        )
        self.db.add(task)
        for split_name, version_id in splits.items():
            self.db.add(
                TrainingTaskTest(
                    task_id=task.id,
                    test_version_id=version_id,
                    split_name=split_name,
                )
            )
        self.db.commit()
        return task

    def test_load_frozen_samples_matches_filesystem_builder_rows(self):
        version_id, fixture = self._import_fixture("Test_1")
        task = self._task("cpt", {"train": version_id})

        db_samples = load_frozen_samples(self.db, task.id, "train")
        fs_samples = [replace(load_sample(fixture), path=Path("NuttX") / "Test_1")]

        self.assertEqual(
            build_cpt_rows(db_samples, "train", 20000),
            build_cpt_rows(fs_samples, "train", 20000),
        )
        self.assertEqual(
            build_sft_open_rows(db_samples, "train", 20000),
            build_sft_open_rows(fs_samples, "train", 20000),
        )

    def test_load_frozen_samples_uses_task_version_not_latest_version(self):
        version_id, fixture = self._import_fixture("Test_1")
        task = self._task("cpt", {"train": version_id})
        changed_log = fixture / "logs" / "round_1" / "nuttx" / "qemu_console.log"
        changed_log.write_text("new latest version log\n", encoding="utf-8")
        self.db.execute(
            text("INSERT INTO dataset_imports (import_id) VALUES ('import-newer')")
        )
        newer = import_test_version(
            self.db,
            test_dir=fixture,
            import_id="import-newer",
            platform="NuttX",
        )
        self.db.commit()

        samples = load_frozen_samples(self.db, task.id, "train")

        self.assertNotEqual(newer.version_id, version_id)
        self.assertNotIn("new latest version log", samples[0].round1_qemu)
        self.assertEqual(samples[0].path, Path("NuttX") / "Test_1")

    def test_materialize_cpt_writes_all_split_files_atomically_without_secrets(self):
        train_id, _ = self._import_fixture("Test_1")
        validation_id, _ = self._import_fixture("Test_2", fault_type="scheduler_deadlock")
        test_id, _ = self._import_fixture("Test_3", fault_type="memory_corruption")
        task = self._task(
            "cpt",
            {"train": train_id, "validation": validation_id, "test": test_id},
        )
        task_dir = self.root / "task-output"
        (task_dir / "data").mkdir(parents=True)
        (task_dir / "data" / "obsolete.txt").write_text("replace me", encoding="utf-8")
        (task_dir / ".preparing").mkdir()
        (task_dir / ".preparing" / "stale.txt").write_text("stale", encoding="utf-8")

        materialized = materialize_task(self.db, task, task_dir)

        self.assertEqual(materialized.split_path, task_dir / "data" / "split.json")
        self.assertEqual(materialized.train_file, task_dir / "data" / "train.jsonl")
        self.assertEqual(materialized.validation_file, task_dir / "data" / "validation.jsonl")
        self.assertEqual(materialized.test_file, task_dir / "data" / "test.jsonl")
        self.assertFalse((task_dir / ".preparing").exists())
        self.assertFalse((task_dir / "data" / "obsolete.txt").exists())
        self.assertEqual(
            json.loads(materialized.split_path.read_text(encoding="utf-8")),
            {"train": ["Test_1"], "validation": ["Test_2"], "test": ["Test_3"]},
        )
        payload = "".join(path.read_text(encoding="utf-8") for path in (
            materialized.train_file,
            materialized.validation_file,
            materialized.test_file,
        ))
        self.assertNotIn("do-not-write-this", payload)
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("/home/", payload)

    def test_materialize_sft_uses_one_task_wide_registry_for_every_split(self):
        train_id, _ = self._import_fixture("Test_1", fault_type="watchdog_timeout")
        validation_id, _ = self._import_fixture("Test_2", fault_type="scheduler_deadlock")
        test_id, _ = self._import_fixture("Test_3", fault_type="memory_corruption")
        task = self._task(
            "sft",
            {"train": train_id, "validation": validation_id, "test": test_id},
        )

        materialized = materialize_task(self.db, task, self.root / "sft-output")

        for path in (materialized.train_file, materialized.validation_file, materialized.test_file):
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            user_content = rows[0]["messages"][1]["content"]
            self.assertIn("WATCHDOG_TIMEOUT", user_content)
            self.assertIn("SCHEDULER_DEADLOCK", user_content)
            self.assertIn("MEMORY_CORRUPTION", user_content)

    def test_materializer_rejects_incomplete_or_corrupt_log_sets_before_writing(self):
        version_id, _ = self._import_fixture("Test_1")
        task = self._task("cpt", {"train": version_id})
        self.db.query(TrainingTestLog).filter_by(
            test_version_id=version_id, round_no=2, log_type="system_metrics"
        ).delete()
        self.db.commit()

        with self.assertRaisesRegex(MaterializationError, "missing"):
            materialize_task(self.db, task, self.root / "broken-output")

        version = self.db.get(TrainingTestVersion, version_id)
        fake_logs = [
            type("Log", (), {"round_no": 1, "log_type": "qemu_console", "content": "one"})(),
            type("Log", (), {"round_no": 1, "log_type": "qemu_console", "content": "two"})(),
        ]
        with self.assertRaisesRegex(MaterializationError, "duplicate"):
            materializer._sample_from_version(version, "NuttX", "Test_1", fake_logs)

        complete_logs = [
            type("Log", (), {"round_no": round_no, "log_type": log_type, "content": "log"})()
            for round_no, log_type in materializer.REQUIRED_LOG_KEYS
        ]
        complete_logs.append(
            type("Log", (), {"round_no": 3, "log_type": "unexpected", "content": "extra"})()
        )
        with self.assertRaisesRegex(MaterializationError, "extra"):
            materializer._sample_from_version(version, "NuttX", "Test_1", complete_logs)

    def test_stale_backup_is_restored_before_a_later_build_failure(self):
        task_dir = self.root / "recovery-output"
        backup_dir = task_dir / ".previous-old-attempt"
        backup_dir.mkdir(parents=True)
        (backup_dir / "old.txt").write_text("old data", encoding="utf-8")
        task = SimpleNamespace(id="recovery-task", task_type="cpt")

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", side_effect=RuntimeError("build failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                materialize_task(self.db, task, task_dir)

        self.assertEqual((task_dir / "data" / "old.txt").read_text(encoding="utf-8"), "old data")
        self.assertFalse(backup_dir.exists())

    def test_second_publish_rename_failure_restores_old_data(self):
        task_dir = self.root / "rename-failure-output"
        data_dir = task_dir / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "old.txt").write_text("old data", encoding="utf-8")
        task = SimpleNamespace(id="rename-failure-task", task_type="cpt")
        original_replace = materializer.os.replace

        def fail_staging_publish(source, destination):
            if Path(source).name.startswith(".preparing-") and Path(destination).name == "data":
                raise OSError("staging publish failed")
            return original_replace(source, destination)

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer.os, "replace", side_effect=fail_staging_publish
        ):
            with self.assertRaisesRegex(OSError, "staging publish failed"):
                materialize_task(self.db, task, task_dir)

        self.assertEqual((data_dir / "old.txt").read_text(encoding="utf-8"), "old data")
        self.assertFalse(list(task_dir.glob(".previous-*")))

    def test_failed_rollback_leaves_backup_for_next_call_to_restore(self):
        task_dir = self.root / "rollback-failure-output"
        data_dir = task_dir / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "old.txt").write_text("old data", encoding="utf-8")
        task = SimpleNamespace(id="rollback-failure-task", task_type="cpt")
        original_replace = materializer.os.replace

        def fail_publish_and_rollback(source, destination):
            source_name = Path(source).name
            if source_name.startswith(".preparing-") and Path(destination).name == "data":
                raise OSError("staging publish failed")
            if source_name.startswith(".previous-") and Path(destination).name == "data":
                raise OSError("rollback failed")
            return original_replace(source, destination)

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer.os, "replace", side_effect=fail_publish_and_rollback
        ):
            with self.assertRaisesRegex(OSError, "staging publish failed"):
                materialize_task(self.db, task, task_dir)

        backups = list(task_dir.glob(".previous-*"))
        self.assertFalse(data_dir.exists())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "old data")

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", side_effect=RuntimeError("build failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                materialize_task(self.db, task, task_dir)

        self.assertEqual((data_dir / "old.txt").read_text(encoding="utf-8"), "old data")
        self.assertFalse(list(task_dir.glob(".previous-*")))

    def test_same_task_materializations_serialize_the_full_attempt(self):
        task_dir = self.root / "concurrent-output"
        task = SimpleNamespace(id="concurrent-task", task_type="cpt")
        first_entered = threading.Event()
        release_first = threading.Event()
        calls: list[int] = []
        calls_lock = threading.Lock()
        failures: list[BaseException] = []

        def load_samples(_db, _task_id, _split):
            with calls_lock:
                call_number = len(calls)
                calls.append(call_number)
            if call_number == 0:
                first_entered.set()
                self.assertTrue(release_first.wait(timeout=5))
            return []

        def run_materialization():
            try:
                materialize_task(self.db, task, task_dir)
            except BaseException as error:
                failures.append(error)

        with patch.object(materializer, "load_frozen_samples", side_effect=load_samples):
            first = threading.Thread(target=run_materialization)
            second = threading.Thread(target=run_materialization)
            first.start()
            self.assertTrue(first_entered.wait(timeout=5))
            second.start()
            self.assertEqual(calls, [0])
            release_first.set()
            first.join(timeout=5)
            second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(calls, list(range(3)))
        self.assertTrue((task_dir / "data" / "split.json").exists())
        self.assertFalse(list(task_dir.glob(".preparing-*")))

    def test_completed_generation_is_not_republished_while_a_consumer_reads(self):
        task_dir = self.root / "stable-generation-output"
        task = SimpleNamespace(id="stable-generation-task", task_type="cpt")

        with patch.object(materializer, "load_frozen_samples", return_value=[]):
            first = materialize_task(self.db, task, task_dir)

        expected_train = first.train_file.read_text(encoding="utf-8")
        generation_inode = first.split_path.parent.stat().st_ino
        with first.train_file.open(encoding="utf-8") as consumer, patch.object(
            materializer.os, "replace", side_effect=AssertionError("published data was renamed")
        ) as replace:
            second = materialize_task(self.db, task, task_dir)
            self.assertEqual(consumer.read(), expected_train)

        self.assertEqual(replace.call_count, 0)
        self.assertEqual(second, first)
        self.assertEqual(second.split_path.parent.stat().st_ino, generation_inode)

    def test_stale_staging_is_removed_when_no_data_or_backup_exists(self):
        task_dir = self.root / "stale-staging-only-output"
        stale_staging = task_dir / ".preparing-abandoned-attempt"
        stale_staging.mkdir(parents=True)
        (stale_staging / "partial.jsonl").write_text("partial", encoding="utf-8")
        task = SimpleNamespace(id="stale-staging-task", task_type="cpt")

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", side_effect=RuntimeError("build failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                materialize_task(self.db, task, task_dir)

        self.assertFalse(stale_staging.exists())
        self.assertFalse((task_dir / "data").exists())

    def test_empty_jsonl_that_does_not_match_manifest_is_rebuilt(self):
        task_dir = self.root / "empty-jsonl-output"
        task = SimpleNamespace(id="empty-jsonl-task", task_type="cpt")
        rows = [{"messages": [{"role": "user", "content": "complete row"}]}]

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", return_value=rows
        ) as build_rows:
            first = materialize_task(self.db, task, task_dir)
            first.train_file.write_text("", encoding="utf-8")
            second = materialize_task(self.db, task, task_dir)

        self.assertEqual(build_rows.call_count, 6)
        self.assertEqual(second.train_file.read_text(encoding="utf-8"), json.dumps(rows[0], sort_keys=True) + "\n")

    def test_row_boundary_truncated_jsonl_is_rebuilt(self):
        task_dir = self.root / "truncated-jsonl-output"
        task = SimpleNamespace(id="truncated-jsonl-task", task_type="cpt")
        rows = [
            {"messages": [{"role": "user", "content": "first row"}]},
            {"messages": [{"role": "user", "content": "second row"}]},
        ]

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", return_value=rows
        ) as build_rows:
            first = materialize_task(self.db, task, task_dir)
            first.train_file.write_text(json.dumps(rows[0], sort_keys=True) + "\n", encoding="utf-8")
            second = materialize_task(self.db, task, task_dir)

        self.assertEqual(build_rows.call_count, 6)
        self.assertEqual(
            second.train_file.read_text(encoding="utf-8"),
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        )

    def test_unmanifested_payloads_are_never_reused(self):
        task_dir = self.root / "unmanifested-output"
        data_dir = task_dir / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "split.json").write_text(
            json.dumps({"train": [], "validation": [], "test": []}) + "\n",
            encoding="utf-8",
        )
        for split in materializer.SPLITS:
            (data_dir / f"{split}.jsonl").write_text("", encoding="utf-8")
        task = SimpleNamespace(id="unmanifested-task", task_type="cpt")

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", side_effect=RuntimeError("rebuild attempted")
        ):
            with self.assertRaisesRegex(RuntimeError, "rebuild attempted"):
                materialize_task(self.db, task, task_dir)

    def test_manifest_binds_generation_and_corruption_forces_rebuild(self):
        task_dir = self.root / "manifest-output"
        task = SimpleNamespace(id="manifest-task", task_type="cpt")
        rows = [{"messages": [{"role": "user", "content": "complete row"}]}]

        with patch.object(materializer, "load_frozen_samples", return_value=[]), patch.object(
            materializer, "build_cpt_rows", return_value=rows
        ) as build_rows:
            first = materialize_task(self.db, task, task_dir)
            manifest_path = first.split_path.parent / ".materialization-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["task_id"], task.id)
            self.assertEqual(manifest["task_type"], task.task_type)
            self.assertEqual({entry["filename"] for entry in manifest["files"]}, {
                "split.json", "train.jsonl", "validation.jsonl", "test.jsonl",
            })
            next(entry for entry in manifest["files"] if entry["filename"] == "train.jsonl")["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            second = materialize_task(self.db, task, task_dir)

        self.assertEqual(build_rows.call_count, 6)
        self.assertEqual(second.train_file.read_text(encoding="utf-8"), json.dumps(rows[0], sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
