import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.training_data import TrainingTest, TrainingTestVersion
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingTask,
    TrainingTaskTest,
)
from app.services import training_artifact_service as service


class TrainingArtifactServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "outputs"
        self.root.mkdir()
        self.engine = create_engine("sqlite://")
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTask.__table__,
            TrainingArtifact.__table__,
            TrainingEvaluation.__table__,
            TrainingTaskTest.__table__,
        ):
            table.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _task(self, *, task_type="cpt", state="succeeded", deleted_at=None):
        task = TrainingTask(
            name="task",
            task_type=task_type,
            job_kind="training",
            state=state,
            model_id="qwen-qwen3.5-9b",
            config_snapshot={"training": {}},
            deleted_at=deleted_at,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _artifact(self, task, relative_path, *, artifact_type="final_adapter", deleted_at=None):
        artifact = TrainingArtifact(
            task_id=task.id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            deleted_at=deleted_at,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def _write(self, relative_path, content="artifact"):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _version(self):
        test = TrainingTest(platform="NuttX", test_name="Test_1")
        self.db.add(test)
        self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=1,
            content_sha256="a" * 64,
            ground_truth={},
            fip_info={},
            completeness="complete",
            import_id="import-1",
        )
        self.db.add(version)
        self.db.flush()
        return version

    def test_obsolete_deletion_compatibility_apis_are_not_exposed(self):
        self.assertFalse(hasattr(service, "delete_artifact"))
        self.assertFalse(hasattr(service, "compensate_staged_deletion"))
        self.assertFalse(hasattr(service, "mark_cleanup_result"))

    def test_resolve_and_open_require_an_active_registered_safe_file(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        file_path = self._write(relative_path, "adapter")
        artifact = self._artifact(task, relative_path)

        self.assertEqual(service.resolve_artifact_path(self.db, artifact.id, self.root), file_path)
        opened = service.open_artifact_file(self.db, artifact.id, self.root)
        try:
            self.assertEqual(os.read(opened.fd, 32), b"adapter")
        finally:
            opened.close()

        for index, unsafe_path in enumerate(("/etc/passwd", "../outside", f"{task.id}/../adapter.bin", ".")):
            with self.subTest(relative_path=unsafe_path):
                bad = self._artifact(task, f"{task.id}/registered-{index}.bin")
                registered_path = bad.relative_path
                bad.relative_path = unsafe_path
                with self.db.no_autoflush:
                    with self.assertRaisesRegex(service.TrainingArtifactConflict, "safe"):
                        service.resolve_artifact_path(self.db, bad.id, self.root)
                bad.relative_path = registered_path

        artifact.deleted_at = datetime.utcnow()
        with self.assertRaisesRegex(service.TrainingArtifactNotFound, "not found"):
            service.open_artifact_file(self.db, artifact.id, self.root)

    def test_resolve_and_open_exclude_artifacts_with_an_active_deletion_journal(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        self._write(relative_path, "adapter")
        artifact = self._artifact(task, relative_path)
        artifact.deletion_state = "recovery_required"
        artifact.quarantine_name = f"{artifact.id}-{'a' * 32}"
        artifact.deletion_updated_at = datetime.utcnow()
        self.db.commit()

        with self.assertRaisesRegex(service.TrainingArtifactNotFound, "not found"):
            service.resolve_artifact_path(self.db, artifact.id, self.root)
        with self.assertRaisesRegex(service.TrainingArtifactNotFound, "not found"):
            service.open_artifact_file(self.db, artifact.id, self.root)

    def test_open_never_follows_a_symlink_swapped_after_registry_lookup(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path, "inside")
        outside = Path(self.temp_dir.name) / "outside.bin"
        outside.write_text("outside", encoding="utf-8")
        artifact = self._artifact(task, relative_path)
        original_open = os.open

        def swap_then_open(path, flags, *args, **kwargs):
            if path == "adapter.bin" and kwargs.get("dir_fd") is not None:
                artifact_path.unlink()
                artifact_path.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch("app.services.training_artifact_service.os.open", side_effect=swap_then_open):
            with self.assertRaises(service.TrainingArtifactConflict):
                service.open_artifact_file(self.db, artifact.id, self.root)

        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_open_relative_regular_file_rejects_symlinked_components(self):
        safe = self.root / "safe" / "public"
        safe.mkdir(parents=True)
        (safe / "training.log").write_text("safe log", encoding="utf-8")
        opened = service.open_relative_regular_file(
            self.root, "safe/public/training.log"
        )
        try:
            self.assertEqual(os.read(opened.fd, 32), b"safe log")
        finally:
            opened.close()

        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        (outside / "training.log").write_text("outside", encoding="utf-8")

        (self.root / "linked-task").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(service.TrainingArtifactConflict):
            service.open_relative_regular_file(
                self.root, "linked-task/training.log"
            )

        task = self.root / "linked-public"
        task.mkdir()
        (task / "public").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(service.TrainingArtifactConflict):
            service.open_relative_regular_file(
                self.root, "linked-public/public/training.log"
            )

        final = self.root / "linked-file" / "public"
        final.mkdir(parents=True)
        (final / "training.log").symlink_to(outside / "training.log")
        with self.assertRaises(service.TrainingArtifactConflict):
            service.open_relative_regular_file(
                self.root, "linked-file/public/training.log"
            )

    def test_register_validates_the_entry_flushes_and_never_owns_the_transaction(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        self._write(relative_path, "checkpoint")

        with (
            mock.patch.object(self.db, "commit", side_effect=AssertionError("service must not commit")),
            mock.patch.object(self.db, "rollback", side_effect=AssertionError("service must not roll back")),
            mock.patch.object(self.db, "flush", wraps=self.db.flush) as flush,
        ):
            artifact = service.register_artifact(
                self.db,
                self.root,
                task_id=task.id,
                artifact_type="checkpoint",
                relative_path=relative_path,
                size_bytes=10,
                sha256="b" * 64,
                metadata_json={"step": 4},
            )

        flush.assert_called_once_with()
        self.assertIsNotNone(artifact.id)
        self.assertEqual(artifact.relative_path, relative_path)
        self.assertIs(self.db.get(TrainingArtifact, artifact.id), artifact)

    def test_register_rejects_unsafe_symlink_and_overlapping_paths(self):
        task = self._task()
        bundle = self.root / task.id / "bundle"
        bundle.mkdir(parents=True)
        (bundle / "model.bin").write_text("model", encoding="utf-8")
        service.register_artifact(
            self.db,
            self.root,
            task_id=task.id,
            artifact_type="tokenizer",
            relative_path=f"{task.id}/bundle",
        )

        for relative_path in (f"{task.id}/bundle", f"{task.id}/bundle/model.bin"):
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(service.TrainingArtifactConflict, "overlaps"):
                    service.register_artifact(
                        self.db,
                        self.root,
                        task_id=task.id,
                        artifact_type="config",
                        relative_path=relative_path,
                    )

        outside = Path(self.temp_dir.name) / "outside.bin"
        outside.write_text("outside", encoding="utf-8")
        symlink_path = self.root / task.id / "symlink.bin"
        symlink_path.symlink_to(outside)
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "safe"):
            service.register_artifact(
                self.db,
                self.root,
                task_id=task.id,
                artifact_type="config",
                relative_path=f"{task.id}/symlink.bin",
            )

        for relative_path in ("/absolute", "../parent", f"{task.id}/../escape"):
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(service.TrainingArtifactConflict, "safe"):
                    service.register_artifact(
                        self.db,
                        self.root,
                        task_id=task.id,
                        artifact_type="config",
                        relative_path=relative_path,
                    )

    def test_register_and_stage_use_the_same_postgresql_advisory_lock_key_first(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        self._write(relative_path)
        original_execute = self.db.execute

        def capture_for(statements):
            def execute(statement, *args, **kwargs):
                statements.append(statement)
                if "pg_advisory_xact_lock" in str(statement):
                    return mock.Mock()
                return original_execute(statement, *args, **kwargs)

            return execute

        register_statements = []
        with (
            mock.patch.object(service, "_is_postgresql", return_value=True),
            mock.patch.object(self.db, "execute", side_effect=capture_for(register_statements)),
        ):
            artifact = service.register_artifact(
                self.db,
                self.root,
                task_id=task.id,
                artifact_type="checkpoint",
                relative_path=relative_path,
            )

        stage_statements = []
        with (
            mock.patch.object(service, "_is_postgresql", return_value=True),
            mock.patch.object(self.db, "execute", side_effect=capture_for(stage_statements)),
        ):
            staged = service.stage_artifact_deletion(self.db, artifact.id, self.root)

        register_sql = str(register_statements[0].compile(compile_kwargs={"literal_binds": True}))
        stage_sql = str(stage_statements[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("pg_advisory_xact_lock", register_sql)
        self.assertEqual(register_sql, stage_sql)
        self.assertIn(str(service._ARTIFACT_REGISTRY_LOCK_KEY), register_sql)
        self.db.rollback()
        staged.restore()

    def test_register_reserves_paths_held_by_unresolved_deletion_journals(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        self._write(relative_path, "replacement")
        old = self._artifact(task, relative_path, artifact_type="checkpoint")
        old.deleted_at = datetime.utcnow()
        old.deletion_state = "pending_cleanup"
        old.quarantine_name = f"{old.id}-{'a' * 32}"
        old.deletion_updated_at = datetime.utcnow()
        self.db.flush()

        for deletion_state in ("pending_cleanup", "staged", "recovery_required", None):
            with self.subTest(deletion_state=deletion_state):
                old.deletion_state = deletion_state
                self.db.flush()
                with self.assertRaisesRegex(service.TrainingArtifactConflict, "overlaps"):
                    service.register_artifact(
                        self.db,
                        self.root,
                        task_id=task.id,
                        artifact_type="checkpoint",
                        relative_path=relative_path,
                    )

        old.deletion_state = "cleaned"
        old.quarantine_name = None
        self.db.flush()
        registered = service.register_artifact(
            self.db,
            self.root,
            task_id=task.id,
            artifact_type="checkpoint",
            relative_path=relative_path,
        )
        self.assertEqual(registered.relative_path, relative_path)

    def test_stage_marks_the_journal_without_flush_commit_or_rollback(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path)
        artifact = self._artifact(task, relative_path)
        artifact_id = artifact.id
        self.db.commit()

        with (
            mock.patch.object(self.db, "flush", side_effect=AssertionError("stage must not flush")),
            mock.patch.object(self.db, "commit", side_effect=AssertionError("service must not commit")),
            mock.patch.object(self.db, "rollback", side_effect=AssertionError("service must not roll back")),
        ):
            staged = service.stage_artifact_deletion(self.db, artifact_id, self.root)

        self.assertFalse(artifact_path.exists())
        self.assertTrue(staged.quarantine_path.exists())
        self.assertIsNotNone(artifact.deleted_at)
        self.assertEqual(artifact.deletion_state, "staged")
        self.assertEqual(artifact.quarantine_name, staged.quarantine_name)
        self.assertIsNotNone(artifact.deletion_updated_at)

    def test_stage_rejects_references_and_active_registry_overlap(self):
        cpt = self._task(task_type="cpt")
        adapter_path = f"{cpt.id}/adapter"
        self._write(adapter_path)
        adapter = self._artifact(cpt, adapter_path)
        sft = self._task(task_type="sft")
        sft.cpt_adapter_artifact_id = adapter.id
        self.db.flush()

        with self.assertRaisesRegex(service.TrainingArtifactConflict, "referenced"):
            service.stage_artifact_deletion(self.db, adapter.id, self.root)

        uploaded_id = str(__import__("uuid").uuid4())
        uploaded_path = f"imports/{uploaded_id}/final_adapter.tar"
        self._write(uploaded_path)
        uploaded = TrainingArtifact(
            id=uploaded_id,
            task_id=None,
            artifact_type="final_adapter",
            relative_path=uploaded_path,
            metadata_json={
                "source": "uploaded",
                "adapter_stage": "cpt",
                "base_model_id": "qwen-qwen3.5-9b",
            },
        )
        self.db.add(uploaded)
        uploaded_sft = self._task(task_type="sft")
        uploaded_sft.cpt_adapter_artifact_id = uploaded.id
        self.db.flush()
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "referenced"):
            service.stage_artifact_deletion(self.db, uploaded.id, self.root)

        uploaded_sft.deleted_at = datetime.utcnow()
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "referenced"):
            service.stage_artifact_deletion(self.db, uploaded.id, self.root)

        sft.deleted_at = datetime.utcnow()
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "referenced"):
            service.stage_artifact_deletion(self.db, adapter.id, self.root)

        data_path = f"{cpt.id}/data.jsonl"
        self._write(data_path)
        dataset = self._artifact(cpt, data_path, artifact_type="dataset")
        self.db.add(TrainingTaskTest(task_id=cpt.id, test_version_id=self._version().id, split_name="train"))
        self.db.flush()
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "historical"):
            service.stage_artifact_deletion(self.db, dataset.id, self.root)

        bundle = self.root / cpt.id / "bundle"
        bundle.mkdir()
        (bundle / "model.bin").write_text("model", encoding="utf-8")
        directory_artifact = self._artifact(cpt, f"{cpt.id}/bundle", artifact_type="tokenizer")
        self._artifact(cpt, f"{cpt.id}/bundle/model.bin", artifact_type="config")
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "overlaps"):
            service.stage_artifact_deletion(self.db, directory_artifact.id, self.root)

    def test_stage_rejects_final_adapter_for_task_backed_evaluation_source(self):
        source = self._task(task_type="sft", state="succeeded")
        relative_path = f"{source.id}/final_adapter.tar"
        self._write(relative_path)
        adapter = self._artifact(source, relative_path)
        evaluation_task = self._task(task_type="sft", state="queued")
        evaluation_task.job_kind = "evaluation"
        self.db.add(
            TrainingEvaluation(
                task_id=evaluation_task.id,
                source_sft_task_id=source.id,
                source_sft_artifact_id=None,
                status="queued",
            )
        )
        self.db.flush()

        with self.assertRaisesRegex(service.TrainingArtifactConflict, "referenced"):
            service.stage_artifact_deletion(self.db, adapter.id, self.root)

    def test_route_owned_rollback_can_restore_a_staged_entry(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path)
        artifact = self._artifact(task, relative_path)
        artifact_id = artifact.id
        self.db.commit()

        staged = service.stage_artifact_deletion(self.db, artifact_id, self.root)
        self.db.rollback()
        staged.restore()

        restored = self.db.get(TrainingArtifact, artifact_id)
        self.assertTrue(artifact_path.exists())
        self.assertFalse(staged.quarantine_path.exists())
        self.assertIsNone(restored.deleted_at)
        self.assertIsNone(restored.deletion_state)

    def test_explicit_phase_helpers_only_mutate_journal_state(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        self._write(relative_path)
        artifact = self._artifact(task, relative_path)
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact.id, self.root)
        self.db.commit()

        with (
            mock.patch.object(self.db, "commit", side_effect=AssertionError("service must not commit")),
            mock.patch.object(self.db, "rollback", side_effect=AssertionError("service must not roll back")),
        ):
            service.mark_artifact_cleanup_pending(self.db, artifact.id)
            self.assertEqual(artifact.deletion_state, "pending_cleanup")
            service.mark_artifact_cleaned(self.db, artifact.id)
            self.assertEqual(artifact.deletion_state, "cleaned")
            self.assertIsNone(artifact.quarantine_name)

        self.db.rollback()
        active = self.db.get(TrainingArtifact, artifact.id)
        active.deleted_at = None
        active.deletion_state = None
        active.quarantine_name = None
        active.deletion_updated_at = None
        self.db.commit()
        service.mark_artifact_recovery_required(self.db, artifact.id, staged.quarantine_name)
        self.assertIsNone(active.deleted_at)
        self.assertEqual(active.deletion_state, "recovery_required")
        self.assertEqual(active.quarantine_name, staged.quarantine_name)

    def test_reconcile_retries_cleanup_and_keeps_failure_persistable(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        self._write(relative_path)
        artifact = self._artifact(task, relative_path, artifact_type="checkpoint")
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact.id, self.root)
        self.db.commit()
        service.mark_artifact_cleanup_pending(self.db, artifact.id)
        self.db.commit()

        with mock.patch.object(service, "_remove_quarantine_entry", side_effect=OSError("busy")):
            service.reconcile_artifact_deletions(self.db, self.root)
        self.db.commit()
        self.assertEqual(artifact.deletion_state, "pending_cleanup")
        self.assertEqual(artifact.quarantine_name, staged.quarantine_name)
        self.assertTrue(staged.quarantine_path.exists())

        service.reconcile_artifact_deletions(self.db, self.root)
        self.db.commit()
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)
        self.assertFalse(staged.quarantine_path.exists())

    def test_reconcile_restores_active_recovery_and_clears_its_journal(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path)
        artifact = self._artifact(task, relative_path)
        artifact_id = artifact.id
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact_id, self.root)
        self.db.rollback()
        service.mark_artifact_recovery_required(self.db, artifact_id, staged.quarantine_name)
        self.db.commit()

        service.reconcile_artifact_deletions(self.db, self.root)
        self.db.commit()

        recovered = self.db.get(TrainingArtifact, artifact_id)
        self.assertTrue(artifact_path.exists())
        self.assertFalse(staged.quarantine_path.exists())
        self.assertIsNone(recovered.deleted_at)
        self.assertIsNone(recovered.deletion_state)
        self.assertIsNone(recovered.quarantine_name)
        self.assertIsNone(recovered.deletion_updated_at)

    def test_reconcile_never_restores_a_symlink_substituted_in_quarantine(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path, "inside")
        artifact = self._artifact(task, relative_path)
        artifact_id = artifact.id
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact_id, self.root)
        self.db.rollback()
        outside = Path(self.temp_dir.name) / "outside.bin"
        outside.write_text("outside", encoding="utf-8")
        staged.quarantine_path.unlink()
        staged.quarantine_path.symlink_to(outside)
        service.mark_artifact_recovery_required(self.db, artifact_id, staged.quarantine_name)
        self.db.commit()

        service.reconcile_artifact_deletions(self.db, self.root)

        recovered = self.db.get(TrainingArtifact, artifact_id)
        self.assertFalse(artifact_path.exists())
        self.assertTrue(staged.quarantine_path.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")
        self.assertEqual(recovered.deletion_state, "recovery_required")
        self.assertEqual(recovered.quarantine_name, staged.quarantine_name)

    def test_reconcile_cleans_a_persisted_staged_deleted_entry(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        artifact_path = self._write(relative_path)
        artifact = self._artifact(task, relative_path, artifact_type="checkpoint")
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact.id, self.root)
        self.db.commit()

        service.reconcile_artifact_deletions(self.db, self.root)

        self.assertFalse(artifact_path.exists())
        self.assertFalse(staged.quarantine_path.exists())
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)

    def test_reconcile_deleted_metadata_cleans_quarantine_and_recreated_original(self):
        task = self._task()
        relative_path = f"{task.id}/checkpoint.bin"
        artifact_path = self._write(relative_path, "first")
        artifact = self._artifact(task, relative_path, artifact_type="checkpoint")
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact.id, self.root)
        self.db.commit()
        artifact_path.write_text("recreated", encoding="utf-8")

        service.reconcile_artifact_deletions(self.db, self.root)

        self.assertFalse(artifact_path.exists())
        self.assertFalse(staged.quarantine_path.exists())
        self.assertEqual(artifact.deletion_state, "cleaned")
        self.assertIsNone(artifact.quarantine_name)

    def test_reconcile_deleted_journal_never_touches_a_recreated_active_owner(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        original = self._write(relative_path, "old")
        old = self._artifact(task, relative_path)
        staged = service.stage_artifact_deletion(self.db, old.id, self.root)
        self.db.commit()

        original.write_text("active", encoding="utf-8")
        active = self._artifact(task, relative_path)
        self.db.commit()

        service.reconcile_artifact_deletions(self.db, self.root)

        self.assertEqual(original.read_text(encoding="utf-8"), "active")
        self.assertFalse(staged.quarantine_path.exists())
        self.assertEqual(old.deletion_state, "cleaned")
        self.assertIsNone(active.deleted_at)
        self.assertIsNone(active.deletion_state)

    def test_reconcile_restores_only_unambiguous_orphans(self):
        task = self._task()
        relative_path = f"{task.id}/adapter.bin"
        artifact_path = self._write(relative_path)
        artifact = self._artifact(task, relative_path)
        artifact_id = artifact.id
        self.db.commit()
        staged = service.stage_artifact_deletion(self.db, artifact_id, self.root)
        self.db.rollback()

        quarantine = self.root / service._QUARANTINE_DIRECTORY
        unknown = quarantine / "unknown-entry"
        unknown.write_text("unknown", encoding="utf-8")
        service.reconcile_artifact_deletions(self.db, self.root)
        self.assertTrue(artifact_path.exists())
        self.assertFalse(staged.quarantine_path.exists())
        self.assertTrue(unknown.exists())

        artifact_path.unlink()
        first = quarantine / f"{artifact_id}-{'1' * 32}"
        second = quarantine / f"{artifact_id}-{'2' * 32}"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        service.reconcile_artifact_deletions(self.db, self.root)
        self.assertFalse(artifact_path.exists())
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_reconcile_acquires_the_registry_lock_and_never_owns_the_transaction(self):
        statements = []

        def execute(statement, *args, **kwargs):
            statements.append(statement)
            if "pg_advisory_xact_lock" in str(statement):
                return mock.Mock()
            return original_execute(statement, *args, **kwargs)

        original_execute = self.db.execute
        with (
            mock.patch.object(service, "_is_postgresql", return_value=True),
            mock.patch.object(self.db, "execute", side_effect=execute),
            mock.patch.object(self.db, "commit", side_effect=AssertionError("service must not commit")),
            mock.patch.object(self.db, "rollback", side_effect=AssertionError("service must not roll back")),
        ):
            service.reconcile_artifact_deletions(self.db, self.root)

        sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertIn(str(service._ARTIFACT_REGISTRY_LOCK_KEY), sql)

    def test_soft_delete_task_allows_only_terminal_records_and_preserves_related_rows(self):
        active = self._task(state="training")
        with self.assertRaisesRegex(service.TrainingArtifactConflict, "terminal"):
            service.soft_delete_task(self.db, active.id)

        terminal = self._task(state="succeeded")
        artifact = self._artifact(terminal, f"{terminal.id}/adapter")
        version = self._version()
        frozen = TrainingTaskTest(task_id=terminal.id, test_version_id=version.id, split_name="test")
        self.db.add(frozen)
        self.db.flush()

        with (
            mock.patch.object(self.db, "commit", side_effect=AssertionError("service must not commit")),
            mock.patch.object(self.db, "rollback", side_effect=AssertionError("service must not roll back")),
        ):
            service.soft_delete_task(self.db, terminal.id)

        self.assertIsNotNone(terminal.deleted_at)
        self.assertIsNotNone(self.db.get(TrainingArtifact, artifact.id))
        self.assertIsNotNone(self.db.get(TrainingTaskTest, frozen.id))


if __name__ == "__main__":
    unittest.main()
