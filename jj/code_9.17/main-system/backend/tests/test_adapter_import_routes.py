import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.deps import get_db
from app.main import app
from app.models.training_data import TrainingTest, TrainingTestVersion
from app.models.training_task import (
    TrainingArtifact,
    TrainingEvaluation,
    TrainingMetric,
    TrainingTask,
    TrainingTaskTest,
)
from app.services.adapter_import_service import AdapterImportError


class AdapterImportRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        for table in (
            TrainingTest.__table__,
            TrainingTestVersion.__table__,
            TrainingTask.__table__,
            TrainingArtifact.__table__,
            TrainingTaskTest.__table__,
            TrainingEvaluation.__table__,
            TrainingMetric.__table__,
        ):
            table.create(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = self._get_db
        self.run_sync_patch = patch(
            "anyio.to_thread.run_sync",
            new=self._run_sync_inline,
        )
        self.run_sync_patch.start()
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.run_sync_patch.stop()
        app.dependency_overrides = self.original_overrides
        self.db.close()
        self.engine.dispose()

    async def _run_sync_inline(self, func, *args, **kwargs):
        kwargs.pop("abandon_on_cancel", None)
        kwargs.pop("cancellable", None)
        kwargs.pop("limiter", None)
        return func(*args, **kwargs)

    def _get_db(self):
        try:
            yield self.db
        finally:
            pass

    @staticmethod
    def _form(**overrides):
        values = {
            "name": "Imported SFT",
            "adapter_stage": "sft",
            "base_model_id": "qwen-qwen3.5-9b",
            "description": "public description",
        }
        values.update(overrides)
        return values

    @staticmethod
    def _uploaded_artifact(
        *,
        artifact_id=None,
        stage="sft",
        name="Imported SFT",
        base_model_id="qwen-qwen3.5-9b",
        metadata_overrides=None,
    ):
        artifact_id = artifact_id or str(uuid4())
        metadata = {
            "source": "uploaded",
            "adapter_stage": stage,
            "base_model_id": base_model_id,
            "display_name": name,
            "description": "public description",
            "uploaded_archive_sha256": "b" * 64,
            "peft_type": "LORA",
            "rank": 2,
            "target_modules": ["q_proj"],
            "original_archive_format": "zip",
        }
        if metadata_overrides:
            metadata.update(metadata_overrides)
        return TrainingArtifact(
            id=artifact_id,
            task_id=None,
            artifact_type="final_adapter",
            relative_path=f"imports/{artifact_id}/final_adapter.tar",
            size_bytes=321,
            sha256="a" * 64,
            metadata_json=metadata,
            created_at=datetime(2026, 7, 26, 8, 0, 0),
        )

    def _route_for(self, path, method):
        for route in app.routes:
            if getattr(route, "path", None) == path and method in getattr(
                route, "methods", set()
            ):
                return route
        self.fail(f"route not found: {method} {path}")

    async def test_routes_are_registered_with_exact_response_models(self):
        from app.schemas.model_training import (
            AdapterImportOut,
            TrainingAdapterListOut,
            TrainingTaskOut,
        )

        self.assertIs(
            self._route_for("/api/v1/model-training/adapters/import", "POST").response_model,
            AdapterImportOut,
        )
        self.assertIs(
            self._route_for("/api/v1/model-training/adapters", "GET").response_model,
            TrainingAdapterListOut,
        )
        self.assertIs(
            self._route_for(
                "/api/v1/model-training/adapters/{artifact_id}/evaluate",
                "POST",
            ).response_model,
            TrainingTaskOut,
        )

    def _version(self, *, completeness="complete"):
        test = TrainingTest(
            platform="qemu",
            test_name=f"External_{uuid4().hex}",
        )
        self.db.add(test)
        self.db.flush()
        version = TrainingTestVersion(
            test_id=test.id,
            version_number=1,
            content_sha256=uuid4().hex.ljust(64, "0"),
            ground_truth={},
            fip_info={},
            completeness=completeness,
            import_id=f"import-{uuid4().hex}",
            sample_class="fault",
            domain="kernel",
            fault_type="watchdog",
        )
        self.db.add(version)
        self.db.flush()
        return version

    async def test_uploaded_sft_evaluation_freezes_tests_and_retry_preserves_xor_source(self):
        artifact = self._uploaded_artifact()
        versions = [self._version(), self._version()]
        self.db.add(artifact)
        self.db.commit()

        response = await self.client.post(
            f"/api/v1/model-training/adapters/{artifact.id}/evaluate",
            json={
                "name": "  External SFT evaluation  ",
                "test_version_ids": [version.id for version in versions],
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "External SFT evaluation")
        self.assertEqual(body["task_type"], "sft")
        self.assertEqual(body["job_kind"], "evaluation")
        self.assertEqual(body["state"], "queued")
        self.assertIsNone(body["parent_task_id"])
        self.assertIsNone(body["cpt_adapter_artifact_id"])
        task = self.db.get(TrainingTask, body["id"])
        evaluation = self.db.query(TrainingEvaluation).filter_by(task_id=task.id).one()
        self.assertIsNone(evaluation.source_sft_task_id)
        self.assertEqual(evaluation.source_sft_artifact_id, artifact.id)
        self.assertEqual(
            [
                (row.test_version_id, row.split_name)
                for row in self.db.query(TrainingTaskTest)
                .filter_by(task_id=task.id)
                .order_by(TrainingTaskTest.id)
            ],
            [(version.id, "test") for version in versions],
        )

        task.state = "failed"
        self.db.commit()
        retried = await self.client.post(f"/api/v1/model-training/tasks/{task.id}/retry")

        self.assertEqual(retried.status_code, 200)
        retry = self.db.get(TrainingTask, retried.json()["id"])
        retry_evaluation = (
            self.db.query(TrainingEvaluation).filter_by(task_id=retry.id).one()
        )
        self.assertEqual(retry.parent_task_id, task.id)
        self.assertIsNone(retry_evaluation.source_sft_task_id)
        self.assertEqual(retry_evaluation.source_sft_artifact_id, artifact.id)
        self.assertEqual(
            [row.test_version_id for row in self.db.query(TrainingTaskTest)
             .filter_by(task_id=retry.id).order_by(TrainingTaskTest.id)],
            [version.id for version in versions],
        )

        cancelled = await self.client.post(
            f"/api/v1/model-training/tasks/{retry.id}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertEqual(
            self.db.query(TrainingEvaluation).filter_by(task_id=retry.id).one().status,
            "cancelled",
        )

    async def test_uploaded_sft_evaluation_rejects_untrusted_sources_and_invalid_tests(self):
        complete = self._version()
        incomplete = self._version(completeness="incomplete")
        valid = self._uploaded_artifact()
        uploaded_cpt = self._uploaded_artifact(stage="cpt")
        wrong_model = self._uploaded_artifact(base_model_id="private-model")
        malformed = self._uploaded_artifact(metadata_overrides={"rank": True})
        deleted = self._uploaded_artifact()
        deleted.deleted_at = datetime.utcnow()
        recovery = self._uploaded_artifact()
        recovery.deletion_state = "recovery_required"
        recovery.quarantine_name = f"{recovery.id}-{'a' * 32}"
        recovery.deletion_updated_at = datetime.utcnow()
        trained_task = TrainingTask(
            name="trained SFT",
            task_type="sft",
            job_kind="training",
            state="succeeded",
            model_id="qwen-qwen3.5-9b",
            config_snapshot={},
        )
        self.db.add(trained_task)
        self.db.flush()
        trained = TrainingArtifact(
            task_id=trained_task.id,
            artifact_type="final_adapter",
            relative_path=f"{trained_task.id}/final_adapter.tar",
            size_bytes=100,
            sha256="c" * 64,
            metadata_json={},
        )
        self.db.add_all([
            valid,
            uploaded_cpt,
            wrong_model,
            malformed,
            deleted,
            recovery,
            trained,
        ])
        self.db.commit()
        request = {
            "name": "external evaluation",
            "test_version_ids": [complete.id],
        }

        source_cases = (
            ("missing-adapter", 404),
            (deleted.id, 404),
            (recovery.id, 404),
            (uploaded_cpt.id, 409),
            (wrong_model.id, 409),
            (malformed.id, 409),
            (trained.id, 409),
        )
        for artifact_id, status_code in source_cases:
            with self.subTest(artifact_id=artifact_id):
                response = await self.client.post(
                    f"/api/v1/model-training/adapters/{artifact_id}/evaluate",
                    json=request,
                )
                self.assertEqual(response.status_code, status_code)
                self.assertNotIn("private", response.text)

        test_cases = (
            ({"name": "   ", "test_version_ids": [complete.id]}, 422),
            ({"name": "evaluation", "test_version_ids": []}, 422),
            ({"name": "evaluation", "test_version_ids": [complete.id, complete.id]}, 422),
            ({"name": "evaluation", "test_version_ids": [999999]}, 404),
            ({"name": "evaluation", "test_version_ids": [incomplete.id]}, 409),
        )
        for payload, status_code in test_cases:
            with self.subTest(payload=payload):
                response = await self.client.post(
                    f"/api/v1/model-training/adapters/{valid.id}/evaluate",
                    json=payload,
                )
                self.assertEqual(response.status_code, status_code)

    async def test_uploaded_sft_evaluation_owns_transaction_and_registry_lock_order(self):
        from sqlalchemy.orm import Query

        from app.services import training_task_service as service

        artifact = self._uploaded_artifact()
        version = self._version()
        self.db.add(artifact)
        self.db.commit()
        payload = {"name": "ordered evaluation", "test_version_ids": [version.id]}
        events = []
        original_first = Query.first
        original_all = Query.all

        def capture_first(query):
            if query.column_descriptions[0].get("entity") is TrainingArtifact:
                events.append("artifact_row")
            return original_first(query)

        def capture_all(query):
            if query.column_descriptions[0].get("entity") is TrainingTestVersion:
                events.append("version_rows")
            return original_all(query)

        with (
            patch.object(
                service,
                "acquire_artifact_registry_lock",
                side_effect=lambda _db: events.append("registry_lock"),
            ),
            patch.object(Query, "first", new=capture_first),
            patch.object(Query, "all", new=capture_all),
        ):
            created = await self.client.post(
                f"/api/v1/model-training/adapters/{artifact.id}/evaluate",
                json=payload,
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(events[:3], ["registry_lock", "artifact_row", "version_rows"])

        before = (
            self.db.query(TrainingTask).count(),
            self.db.query(TrainingEvaluation).count(),
            self.db.query(TrainingTaskTest).count(),
        )
        with patch.object(self.db, "commit", side_effect=RuntimeError("commit failed")):
            failed = await self.client.post(
                f"/api/v1/model-training/adapters/{artifact.id}/evaluate",
                json={"name": "rolled back", "test_version_ids": [version.id]},
            )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(
            (
                self.db.query(TrainingTask).count(),
                self.db.query(TrainingEvaluation).count(),
                self.db.query(TrainingTaskTest).count(),
            ),
            before,
        )

    async def test_import_returns_201_or_200_and_closes_multipart_upload(self):
        captured_uploads = []
        calls = 0

        async def fake_import(_db, upload, request, _root):
            nonlocal calls
            calls += 1
            captured_uploads.append(upload)
            self.assertEqual(upload.filename, "customer-secret.zip")
            self.assertEqual(request.name, "Imported SFT")
            self.assertEqual(request.adapter_stage, "sft")
            await upload.close()
            return SimpleNamespace(
                artifact=self._uploaded_artifact(),
                deduplicated=calls == 2,
            )

        with patch(
            "app.api.v1.model_training_adapters.import_adapter",
            new=fake_import,
        ):
            created = await self.client.post(
                "/api/v1/model-training/adapters/import",
                data=self._form(),
                files={"file": ("customer-secret.zip", b"archive", "application/zip")},
            )
            duplicate = await self.client.post(
                "/api/v1/model-training/adapters/import",
                data=self._form(),
                files={"file": ("customer-secret.zip", b"archive", "application/zip")},
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(created.json()["deduplicated"])
        self.assertTrue(duplicate.json()["deduplicated"])
        self.assertEqual(
            set(created.json()["adapter"]),
            {
                "id",
                "name",
                "adapter_stage",
                "source",
                "base_model_id",
                "size_bytes",
                "sha256",
                "deletable",
                "usable_for_sft",
                "evaluable",
            },
        )
        self.assertNotIn("private", created.text)
        self.assertTrue(all(upload.file.closed for upload in captured_uploads))

    async def test_import_validates_form_fields_before_calling_service(self):
        cases = (
            self._form(name="   "),
            self._form(adapter_stage="other"),
            self._form(base_model_id="private-model"),
            self._form(description="x" * 1001),
        )

        async def should_not_run(*_args, **_kwargs):
            self.fail("import service must not run for invalid fields")

        with patch(
            "app.api.v1.model_training_adapters.import_adapter",
            new=should_not_run,
        ):
            for data in cases:
                with self.subTest(data={key: len(value) for key, value in data.items()}):
                    response = await self.client.post(
                        "/api/v1/model-training/adapters/import",
                        data=data,
                        files={"file": ("adapter.zip", b"archive", "application/zip")},
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["detail"]["code"], "invalid_adapter_request")

        missing = await self.client.post(
            "/api/v1/model-training/adapters/import",
            data={"adapter_stage": "sft", "base_model_id": "qwen-qwen3.5-9b"},
            files={"file": ("adapter.zip", b"archive", "application/zip")},
        )
        self.assertEqual(missing.status_code, 422)

    async def test_invalid_form_closes_framework_multipart_upload(self):
        closed_uploads = []
        real_close = StarletteUploadFile.close

        async def record_close(upload):
            closed_uploads.append(upload)
            await real_close(upload)

        with patch.object(StarletteUploadFile, "close", new=record_close):
            response = await self.client.post(
                "/api/v1/model-training/adapters/import",
                data=self._form(name="   "),
                files={"file": ("adapter.zip", b"archive", "application/zip")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(closed_uploads)
        self.assertTrue(all(upload.file.closed for upload in closed_uploads))

    async def test_import_maps_typed_errors_without_leaking_internal_details(self):
        cases = (
            ("archive_too_large", 413, "adapter_archive_too_large"),
            ("unsupported_archive_format", 415, "unsupported_adapter_archive"),
            ("invalid_adapter_config", 422, "incompatible_adapter_archive"),
            ("invalid_json_metadata", 422, "incompatible_adapter_archive"),
            ("adapter_import_commit_uncertain", 409, "adapter_import_commit_uncertain"),
            ("adapter_import_failed", 500, "adapter_import_failed"),
        )

        for service_code, status_code, public_code in cases:
            async def fail_import(_db, upload, _request, _root, code=service_code):
                await upload.close()
                error = AdapterImportError(code)
                error.add_note("/private/customer/archive")
                raise error

            with self.subTest(service_code=service_code), patch(
                "app.api.v1.model_training_adapters.import_adapter",
                new=fail_import,
            ):
                response = await self.client.post(
                    "/api/v1/model-training/adapters/import",
                    data=self._form(),
                    files={"file": ("private.zip", b"archive", "application/zip")},
                )
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.json()["detail"]["code"], public_code)
                self.assertNotIn("private", response.text)
                self.assertNotIn("customer", response.text)

        async def unexpected(_db, upload, _request, _root):
            await upload.close()
            raise RuntimeError("/private/raw parser error")

        with patch(
            "app.api.v1.model_training_adapters.import_adapter",
            new=unexpected,
        ):
            response = await self.client.post(
                "/api/v1/model-training/adapters/import",
                data=self._form(),
                files={"file": ("private.zip", b"archive", "application/zip")},
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"]["code"], "adapter_import_failed")
        self.assertNotIn("private", response.text)


class TrainingAdapterListRouteTests(AdapterImportRouteTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._seed_adapters()

    def _task(
        self,
        *,
        name,
        task_type,
        state="succeeded",
        deleted_at=None,
        cpt_adapter_artifact_id=None,
        queued_at=None,
    ):
        task = TrainingTask(
            name=name,
            task_type=task_type,
            job_kind="training",
            state=state,
            model_id="qwen-qwen3.5-9b",
            cpt_adapter_artifact_id=cpt_adapter_artifact_id,
            config_snapshot={},
            queued_at=queued_at or datetime(2026, 7, 26, 8, 0, 0),
            deleted_at=deleted_at,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _artifact(
        self,
        *,
        task=None,
        metadata=None,
        created_at=None,
        deleted_at=None,
        deletion_state=None,
    ):
        artifact_id = str(uuid4())
        artifact = TrainingArtifact(
            id=artifact_id,
            task_id=task.id if task else None,
            artifact_type="final_adapter",
            relative_path=(
                f"{task.id}/final_adapter" if task else f"imports/{artifact_id}/final_adapter.tar"
            ),
            size_bytes=100 + len(self.db.new),
            sha256=(artifact_id.replace("-", "") * 2)[:64],
            metadata_json=metadata,
            created_at=created_at or datetime(2026, 7, 26, 8, 0, 0),
            deleted_at=deleted_at,
            deletion_state=deletion_state,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    @staticmethod
    def _uploaded_metadata(*, name, stage="sft", base_model_id="qwen-qwen3.5-9b"):
        return {
            "source": "uploaded",
            "adapter_stage": stage,
            "base_model_id": base_model_id,
            "display_name": name,
            "description": "safe",
            "uploaded_archive_sha256": "b" * 64,
            "peft_type": "LORA",
            "rank": 2,
            "target_modules": ["q_proj"],
            "original_archive_format": "zip",
        }

    def _seed_adapters(self):
        trained_task = self._task(name="Alpha Trained CPT", task_type="cpt")
        self.trained = self._artifact(
            task=trained_task,
            metadata={
                "adapter_stage": "sft",
                "display_name": "Metadata Lie",
                "server_path": "/private/not-for-api",
            },
            created_at=datetime(2026, 7, 26, 8, 0, 0),
        )
        self.uploaded_cpt = self._artifact(
            metadata=self._uploaded_metadata(name="Beta Uploaded CPT", stage="cpt"),
            created_at=datetime(2026, 7, 26, 8, 1, 0),
        )
        self.uploaded_sft = self._artifact(
            metadata=self._uploaded_metadata(name="Gamma Uploaded SFT"),
            created_at=datetime(2026, 7, 26, 8, 2, 0),
        )
        self._task(
            name="Dependent SFT",
            task_type="sft",
            state="queued",
            cpt_adapter_artifact_id=self.uploaded_cpt.id,
        )
        self._artifact(
            metadata=self._uploaded_metadata(name="Wrong Stage", stage="other"),
            created_at=datetime(2026, 7, 26, 8, 3, 0),
        )
        self._artifact(
            metadata=self._uploaded_metadata(
                name="Wrong Model",
                base_model_id="private-model",
            ),
            created_at=datetime(2026, 7, 26, 8, 4, 0),
        )
        deleted_task = self._task(
            name="Deleted Task Adapter",
            task_type="sft",
            deleted_at=datetime(2026, 7, 26, 9, 0, 0),
        )
        self._artifact(task=deleted_task)
        self._artifact(
            metadata=self._uploaded_metadata(name="Deleted Upload"),
            deleted_at=datetime(2026, 7, 26, 9, 0, 0),
        )
        self.db.commit()

    async def test_list_derives_trained_and_uploaded_fields_and_redacts_metadata(self):
        response = await self.client.get("/api/v1/model-training/adapters")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        by_id = {item["id"]: item for item in body["items"]}
        self.assertEqual(set(by_id), {self.trained.id, self.uploaded_cpt.id, self.uploaded_sft.id})
        self.assertEqual(
            (by_id[self.trained.id]["name"], by_id[self.trained.id]["adapter_stage"]),
            ("Alpha Trained CPT", "cpt"),
        )
        self.assertEqual(by_id[self.trained.id]["source"], "training")
        self.assertTrue(by_id[self.trained.id]["usable_for_sft"])
        self.assertFalse(by_id[self.trained.id]["evaluable"])
        self.assertFalse(by_id[self.uploaded_cpt.id]["deletable"])
        self.assertTrue(by_id[self.uploaded_sft.id]["deletable"])
        self.assertTrue(by_id[self.uploaded_sft.id]["evaluable"])
        expected_fields = {
            "id",
            "name",
            "adapter_stage",
            "source",
            "base_model_id",
            "size_bytes",
            "sha256",
            "deletable",
            "usable_for_sft",
            "evaluable",
        }
        self.assertTrue(all(set(item) == expected_fields for item in body["items"]))
        self.assertNotIn("private", response.text)
        self.assertNotIn("not-for-api", response.text)
        self.assertNotIn("Metadata Lie", response.text)

    async def test_list_supports_search_stage_source_and_paging_filters(self):
        cases = (
            ({"source": "uploaded"}, {self.uploaded_cpt.id, self.uploaded_sft.id}),
            ({"source": "training"}, {self.trained.id}),
            ({"adapter_stage": "cpt"}, {self.trained.id, self.uploaded_cpt.id}),
            ({"adapter_stage": "sft"}, {self.uploaded_sft.id}),
            ({"search": "gamma"}, {self.uploaded_sft.id}),
            ({"search": "TRAINED"}, {self.trained.id}),
        )
        for params, expected_ids in cases:
            with self.subTest(params=params):
                response = await self.client.get(
                    "/api/v1/model-training/adapters",
                    params=params,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {item["id"] for item in response.json()["items"]},
                    expected_ids,
                )
                self.assertEqual(response.json()["total"], len(expected_ids))

        page = await self.client.get(
            "/api/v1/model-training/adapters",
            params={"page": 2, "page_size": 1},
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 3)
        self.assertEqual((page.json()["page"], page.json()["page_size"]), (2, 1))
        self.assertEqual(len(page.json()["items"]), 1)

        for params in ({"source": "other"}, {"adapter_stage": "other"}):
            invalid = await self.client.get(
                "/api/v1/model-training/adapters",
                params=params,
            )
            self.assertEqual(invalid.status_code, 422)

    async def test_list_search_treats_percent_underscore_and_escape_as_literals(self):
        literal_rows = (
            ("Percent 100% Ready", "Percent 100X Ready", "100%"),
            ("Build_Name", "BuildXName", "Build_"),
            (r"Path\Adapter", "PathXAdapter", "Path\\"),
        )
        expected = {}
        for literal_name, decoy_name, search in literal_rows:
            literal = self._artifact(
                metadata=self._uploaded_metadata(name=literal_name),
            )
            self._artifact(metadata=self._uploaded_metadata(name=decoy_name))
            expected[search] = literal.id
        self.db.commit()

        statements = []

        def capture_search_sql(_connection, _cursor, statement, parameters, *_args):
            if "LIKE" in statement.upper():
                statements.append((statement, parameters))

        event.listen(self.engine, "before_cursor_execute", capture_search_sql)
        try:
            for search, artifact_id in expected.items():
                with self.subTest(search=search):
                    response = await self.client.get(
                        "/api/v1/model-training/adapters",
                        params={"search": search},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        {item["id"] for item in response.json()["items"]},
                        {artifact_id},
                    )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_search_sql)

        self.assertTrue(statements)
        self.assertTrue(all(" ESCAPE " in statement.upper() for statement, _ in statements))
        bound_values = [
            value
            for _statement, parameters in statements
            for value in parameters
            if isinstance(value, str)
        ]
        self.assertIn(r"%100\%%", bound_values)
        self.assertIn(r"%build\_%", bound_values)
        self.assertIn(r"%path\\%", bound_values)

    async def test_list_deletability_counts_all_sft_but_not_non_sft_references(self):
        non_sft_only = self._artifact(
            metadata=self._uploaded_metadata(name="Non-SFT Referenced CPT", stage="cpt"),
        )
        self._task(
            name="Non-SFT Reference",
            task_type="cpt",
            cpt_adapter_artifact_id=non_sft_only.id,
        )
        self._task(
            name="Deleted SFT Reference",
            task_type="sft",
            cpt_adapter_artifact_id=self.uploaded_sft.id,
            deleted_at=datetime(2026, 7, 26, 10, 0, 0),
        )
        evaluation_only = self._artifact(
            metadata=self._uploaded_metadata(name="Evaluation-only SFT"),
        )
        evaluation_task = self._task(
            name="External evaluation",
            task_type="sft",
            state="failed",
        )
        evaluation_task.job_kind = "evaluation"
        self.db.add(
            TrainingEvaluation(
                task_id=evaluation_task.id,
                source_sft_task_id=None,
                source_sft_artifact_id=evaluation_only.id,
                status="failed",
            )
        )
        trained_sft_task = self._task(
            name="Task-backed SFT",
            task_type="sft",
        )
        trained_sft = self._artifact(task=trained_sft_task)
        task_backed_evaluation = self._task(
            name="Task-backed evaluation",
            task_type="sft",
            state="queued",
        )
        task_backed_evaluation.job_kind = "evaluation"
        self.db.add(
            TrainingEvaluation(
                task_id=task_backed_evaluation.id,
                source_sft_task_id=trained_sft_task.id,
                source_sft_artifact_id=None,
                status="queued",
            )
        )
        self.db.commit()

        response = await self.client.get("/api/v1/model-training/adapters")

        self.assertEqual(response.status_code, 200)
        by_id = {item["id"]: item for item in response.json()["items"]}
        self.assertFalse(by_id[self.uploaded_cpt.id]["deletable"])
        self.assertFalse(by_id[self.uploaded_sft.id]["deletable"])
        self.assertFalse(by_id[evaluation_only.id]["deletable"])
        self.assertFalse(by_id[trained_sft.id]["deletable"])
        self.assertTrue(by_id[non_sft_only.id]["deletable"])

    async def test_list_does_not_advertise_untrusted_uploaded_sft_as_evaluable(self):
        metadata = self._uploaded_metadata(name="Malformed Uploaded SFT")
        metadata["unexpected_private_field"] = "not-for-api"
        malformed = self._artifact(metadata=metadata)
        self.db.commit()

        response = await self.client.get("/api/v1/model-training/adapters")

        self.assertEqual(response.status_code, 200)
        by_id = {item["id"]: item for item in response.json()["items"]}
        self.assertIn(malformed.id, by_id)
        self.assertFalse(by_id[malformed.id]["evaluable"])
        self.assertNotIn("not-for-api", response.text)

    async def test_list_uses_batched_sql_without_per_adapter_queries(self):
        selects = []

        def count_selects(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(self.engine, "before_cursor_execute", count_selects)
        try:
            response = await self.client.get("/api/v1/model-training/adapters")
        finally:
            event.remove(self.engine, "before_cursor_execute", count_selects)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(selects), 3)
