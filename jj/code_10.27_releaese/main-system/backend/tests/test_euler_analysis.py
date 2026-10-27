import json
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import DiagnosisRecord, EulerArtifactImport, Run
from app.models.euler_analysis import EulerAnalysisBatch, EulerAnalysisResult
from app.services import euler_analysis_service as service


class FakeRuntime:
    model = "test-only-no-real-model"
    source = "test"
    max_output_tokens = 1024

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))

    def redact(self, message):
        return message.replace("secret-key", "***")

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        content = json.dumps({"fault_code": outcome, "confidence": 0.8,
                              "reasoning": "metric evidence", "root_cause": "test result"})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class EulerAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (Run, EulerArtifactImport, DiagnosisRecord, EulerAnalysisBatch, EulerAnalysisResult):
            model.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.db.execute(text("CREATE TABLE mongo_docs (pk INTEGER PRIMARY KEY, collection TEXT, run_id TEXT, doc JSON)"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def saved(self, key="one", code="F4512", mode="simulated"):
        ids = [f"euler_{key}_round_1", f"euler_{key}_round_2"]
        self.db.add(EulerArtifactImport(import_id=key, job_id=f"job-{key}", agent_url="http://old-agent",
                                       state="success", result_json={"run_ids": ids}))
        for number, run_id in enumerate(ids, 1):
            stats = {"dataset_import_meta": {"import_id": key}, "euler_job_id": f"job-{key}",
                     "euler_provenance": {"fault_type": code, "execution_mode": "baseline",
                                          "evidence_mode": mode, "root_cause_text": "hidden truth"}}
            self.db.add(Run(run_id=run_id, source_type="dataset", system_id="euler", round_no=number,
                            is_fault=number == 1, stats_json=stats, run_name=run_id))
            line = 'INFO monitor.host ' + json.dumps({"probe_snapshot": {"fault_type": code,
                    "cpu_usage": 95 if number == 1 else 10}, "timeline": {"fault_code": code},
                    "root_cause_text": "hidden truth", "parameters": {"fault_type": code}})
            self.db.execute(text("INSERT INTO mongo_docs(collection,run_id,doc) VALUES ('log_entries',:id,:doc)"),
                            {"id": run_id, "doc": json.dumps({"raw_line": line, "line_no": 1})})
        self.db.commit()
        return ids

    def start(self, runtime):
        with patch.object(service, "_try_lock", return_value=True), patch.object(
                service.llm_runtime_config_provider, "resolve", return_value=runtime):
            batch_id, scheduled = service.request_batch(self.db)
        self.assertTrue(scheduled)
        batch = self.db.get(EulerAnalysisBatch, batch_id)
        service.process_batch(self.db, batch, runtime)
        return batch

    def test_scope_excludes_uploaded_euler_and_failed_saves_and_replay(self):
        ids = self.saved()
        self.db.add(Run(run_id="uploaded", source_type="dataset", system_id="euler", round_no=1,
                        is_fault=True, stats_json={"euler_provenance": {"fault_type": "F4512"}}))
        other = self.saved("failed")
        self.db.get(EulerArtifactImport, "failed").state = "failed"
        replay = self.saved("replay")
        for run_id in replay:
            run = self.db.query(Run).filter_by(run_id=run_id).one()
            stats = dict(run.stats_json)
            stats["euler_provenance"] = {**stats["euler_provenance"], "sample_id": "sample-f4512"}
            run.stats_json = stats
        self.db.commit()
        self.assertEqual({run.run_id for run in service.eligible_runs(self.db)}, set(ids))
        self.assertEqual(service.snapshot(self.db)["total"], 2)

    def test_saved_all_ten_types_across_agent_histories(self):
        for index, code in enumerate(service.FAULT_LABELS):
            self.saved(str(index), code)
        self.assertEqual(service.snapshot(self.db)["total"], 20)

    def test_batch_scores_fault_and_normal_separately_and_does_not_resubmit(self):
        ids = self.saved()
        runtime = FakeRuntime(["F4512", "NORMAL"])
        batch = self.start(runtime)
        self.assertEqual(batch.state, "completed")
        view = service.snapshot(self.db)
        self.assertEqual(view["diagnosed"], 2)
        self.assertEqual(view["groups"][0]["fault"]["accuracy"], 100)
        self.assertEqual(view["groups"][0]["normal"]["accuracy"], 100)
        self.assertEqual(view["overall"], {"total": 2, "evaluated": 2, "correct": 2, "accuracy": 100})
        self.assertTrue(all(item["diagnosis_mode"] == "ground_truth_assisted" for item in view["items"]))
        self.assertEqual(self.db.query(DiagnosisRecord).count(), 2)
        prompt = json.loads(runtime.calls[0]["messages"][1]["content"])
        self.assertEqual(prompt["ground_truth_reference"]["true_code"], "F4512")
        self.assertEqual(prompt["ground_truth_reference"]["root_cause_text"], "hidden truth")
        self.assertNotIn("F4512", prompt["logs"])
        self.assertNotIn("hidden truth", prompt["logs"])
        self.assertIn("cpu_usage", prompt["logs"])
        baseline = json.loads(runtime.calls[1]["messages"][1]["content"])["ground_truth_reference"]
        self.assertEqual(baseline["true_code"], "NORMAL")
        self.assertNotIn("F4512", json.dumps(baseline))
        self.assertNotIn("root_cause_text", baseline)
        self.assertTrue(self.db.query(DiagnosisRecord).filter_by(run_id=ids[0]).first())
        with patch.object(service, "_try_lock", return_value=True):
            with self.assertRaisesRegex(ValueError, "没有待诊断"):
                service.request_batch(self.db)

    def test_historical_online_diagnoses_are_skipped_even_if_unmappable(self):
        ids = self.saved()
        for run_id in ids:
            self.db.add(DiagnosisRecord(run_id=run_id, input_log="old", channel_used="fast",
                                        is_fault=True, fault_type_name="无法确定的历史类型"))
        self.db.commit()
        view = service.snapshot(self.db)
        self.assertEqual(view["pending"], 0)
        self.assertEqual(view["unevaluable"], 2)
        self.assertIsNone(view["groups"][0]["fault"]["accuracy"])
        self.assertIsNone(view["overall"]["accuracy"])
        self.assertEqual(view["overall"]["evaluated"], 0)
        self.assertTrue(all(item["diagnosis_mode"] == "existing" for item in view["items"]))

    def test_failure_redacted_retry_only_failed_and_unknown_is_wrong(self):
        self.saved()
        batch = self.start(FakeRuntime([RuntimeError("timeout secret-key"), "NORMAL"]))
        self.assertEqual(batch.state, "partial_failed")
        view = service.snapshot(self.db)
        self.assertEqual(view["failed"], 1)
        self.assertNotIn("secret-key", json.dumps(view))
        retry = FakeRuntime(["UNKNOWN"])
        self.start(retry)
        self.assertEqual(len(retry.calls), 1)
        view = service.snapshot(self.db)
        self.assertEqual(view["diagnosed"], 2)
        self.assertEqual(view["failed"], 0)
        self.assertEqual(view["groups"][0]["fault"]["accuracy"], 0)

    def test_normal_uses_round_not_sample_fault_ground_truth(self):
        self.saved()
        self.start(FakeRuntime(["F4512", "F4512"]))
        view = service.snapshot(self.db)
        self.assertEqual(view["groups"][0]["fault"]["accuracy"], 100)
        self.assertEqual(view["groups"][0]["normal"]["accuracy"], 0)

    def test_simulated_and_observed_have_separate_metrics(self):
        self.saved("sim", mode="simulated")
        self.saved("real", mode="observed")
        self.start(FakeRuntime(["UNKNOWN", "NORMAL", "F4512", "NORMAL"]))
        groups = {item["evidence_mode"]: item for item in service.snapshot(self.db)["groups"]}
        self.assertEqual(groups["simulated"]["fault"]["accuracy"], 0)
        self.assertEqual(groups["observed"]["fault"]["accuracy"], 100)

    def test_active_batch_and_lock_prevent_duplicate_jobs(self):
        ids = self.saved()
        self.db.add(EulerAnalysisBatch(id="active", state="running", run_ids=ids))
        self.db.commit()
        with patch.object(service, "_try_lock", return_value=True):
            self.assertEqual(service.request_batch(self.db), ("active", False))
        with patch.object(service, "_try_lock", return_value=False):
            with self.assertRaisesRegex(ValueError, "正在执行"):
                service.request_batch(self.db)

    def test_newly_saved_files_wait_for_next_click(self):
        ids = self.saved()
        runtime = FakeRuntime(["F4512", "NORMAL"])
        with patch.object(service, "_try_lock", return_value=True), patch.object(
                service.llm_runtime_config_provider, "resolve", return_value=runtime):
            batch_id, _ = service.request_batch(self.db)
        self.saved("later")
        service.process_batch(self.db, self.db.get(EulerAnalysisBatch, batch_id), runtime)
        self.assertEqual(service.snapshot(self.db)["pending"], 2)

    def test_refreshed_online_result_is_used_without_reanalysis(self):
        ids = self.saved()
        self.start(FakeRuntime(["F4512", "NORMAL"]))
        self.db.add(DiagnosisRecord(run_id=ids[0], input_log="online refresh", channel_used="fast",
                                    is_fault=True, fault_type_name="F4620"))
        self.db.commit()
        view = service.snapshot(self.db)
        self.assertEqual(view["pending"], 0)
        self.assertEqual(view["items"][0]["source"], "online")
        self.assertFalse(view["items"][0]["correct"])
        self.assertEqual(view["items"][0]["diagnosis_mode"], "existing")
        self.assertIsNone(view["items"][0]["input_info"])

    def test_overall_metric_uses_all_pages_and_excludes_failed_pending_unmappable(self):
        self.saved("first")
        self.start(FakeRuntime(["UNKNOWN", "NORMAL"]))
        self.saved("failure")
        self.start(FakeRuntime([RuntimeError("test failure"), "NORMAL"]))
        pending = self.saved("pending")
        self.db.add(DiagnosisRecord(run_id=pending[0], input_log="old", channel_used="fast",
                                    is_fault=True, fault_type_name="unmapped"))
        self.db.commit()
        first = service.snapshot(self.db, page=1, page_size=1)
        second = service.snapshot(self.db, page=2, page_size=1)
        self.assertEqual(len(first["items"]), 1)
        self.assertFalse(first["items"][0]["correct"])
        self.assertEqual(first["overall"], {"total": 6, "evaluated": 3, "correct": 2, "accuracy": 66.67})
        self.assertEqual(first["overall"], second["overall"])

    def test_fallback_reference_preserves_requested_and_executed_identity(self):
        ids = self.saved(code="F5201")
        for run in self.db.query(Run).filter(Run.run_id.in_(ids)):
            stats = dict(run.stats_json)
            stats["euler_provenance"] = {**stats["euler_provenance"], "fault_type": "F5203",
                "requested_fault_type": "F5201", "executed_fault_type": "F5203",
                "fallback_reason": "explicit fallback"}
            run.stats_json = stats
        self.db.commit()
        runtime = FakeRuntime(["F5203", "NORMAL"])
        self.start(runtime)
        reference = json.loads(runtime.calls[0]["messages"][1]["content"])["ground_truth_reference"]
        self.assertEqual(reference["true_code"], "F5203")
        self.assertEqual(reference["executed_fault_type"], "F5203")
        self.assertEqual(reference["fallback_reason"], "explicit fallback")
        self.assertEqual(service.snapshot(self.db)["overall"]["accuracy"], 100)

    def test_cpu_alternative_is_scored_as_actual_type_and_skipped_after_diagnosis(self):
        ids = self.saved(code="F2305")
        for run in self.db.query(Run).filter(Run.run_id.in_(ids)):
            stats = dict(run.stats_json)
            stats["euler_provenance"] = {**stats["euler_provenance"], "fault_type": "F2301",
                "requested_fault_type": "F2305", "executed_fault_type": "F2301",
                "fallback_reason": "explicit fallback", "simulation_reason": "CPU controller unavailable"}
            run.stats_json = stats
        self.db.commit()
        runtime = FakeRuntime(["F2301", "NORMAL"])
        self.start(runtime)
        view = service.snapshot(self.db)
        self.assertEqual(view["overall"]["accuracy"], 100)
        self.assertEqual(view["pending"], 0)
        reference = json.loads(runtime.calls[0]["messages"][1]["content"])["ground_truth_reference"]
        self.assertEqual(reference["true_code"], "F2301")
        self.assertEqual(reference["requested_fault_type"], "F2305")
        self.assertIn("CPU controller", reference["simulation_reason"])
        # Predicting the requested type for the actual alternative is wrong.
        result = self.db.get(EulerAnalysisResult, ids[0])
        result.prediction = {**result.prediction, "fault_code": "F2305"}
        self.db.commit()
        self.assertEqual(service.snapshot(self.db)["overall"]["accuracy"], 50)

    def test_invalid_saved_identity_is_not_sent_to_model(self):
        ids = self.saved()
        run = self.db.query(Run).filter_by(run_id=ids[0]).one()
        stats = dict(run.stats_json)
        stats["euler_provenance"] = {**stats["euler_provenance"], "executed_fault_type": "F4620"}
        run.stats_json = stats
        self.db.commit()
        runtime = FakeRuntime(["NORMAL"])
        batch = self.start(runtime)
        self.assertEqual(batch.failed, 1)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(service.snapshot(self.db)["overall"]["evaluated"], 1)

    def test_assistance_does_not_replace_wrong_model_output_with_truth(self):
        self.saved()
        self.start(FakeRuntime(["F4620", "F4512"]))
        view = service.snapshot(self.db)
        self.assertEqual(view["overall"]["accuracy"], 0)
        self.assertEqual(view["items"][0]["prediction"]["fault_code"], "F4620")
        self.assertEqual(view["items"][1]["prediction"]["fault_code"], "F4512")

    def test_legacy_batch_result_retains_log_only_mode(self):
        ids = self.saved()
        self.start(FakeRuntime(["F4512", "NORMAL"]))
        row = self.db.get(EulerAnalysisResult, ids[0])
        row.input_info = {"line_count": 1, "input_characters": 100}
        self.db.commit()
        self.assertEqual(service.snapshot(self.db)["items"][0]["diagnosis_mode"], "logs_only")
        self.assertEqual(service.snapshot(self.db)["items"][1]["diagnosis_mode"], "ground_truth_assisted")

    def test_input_truncation_and_malformed_metadata_are_explicit(self):
        source = ["INFO monitor.host " + json.dumps({"probe_snapshot": {"cpu_usage": i,
                  "samples": ["测量值" * 100 for _ in range(40)]}, "status": "captured"}, ensure_ascii=False)
                  for i in range(100)]
        content, info = service.prepare_input(source)
        self.assertTrue(info["truncated"])
        self.assertLessEqual(len(content), 6000)
        self.assertLessEqual(info["estimated_tokens_upper_bound"], info["token_budget"])
        for line in content.splitlines():
            if line.startswith("INFO"):
                json.loads(line[line.index("{"):])
        compact = json.loads(content.splitlines()[0][content.splitlines()[0].index("{"):])
        self.assertIn("probe_snapshot", compact)
        self.assertIn("omitted", service.sanitize_line('INFO {"fault_type": "F4512"'))

    def test_augmentation_prompt_requests_json_and_bounds_output(self):
        runtime = FakeRuntime(["F4512"])
        info = {}
        prediction = service.predict(runtime, "INFO cpu_usage=95", {"true_code": "F4512"}, input_info=info)
        request = runtime.calls[0]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertLessEqual(request["max_tokens"], 1024)
        self.assertEqual(info["attempts"], 1)
        self.assertEqual(prediction["fault_code"], "F4512")

    def test_augmentation_retries_transient_timeout_once_and_classifies_output_errors(self):
        runtime = FakeRuntime([TimeoutError("temporary timeout"), "NORMAL"])
        info = {}
        service.predict(runtime, "INFO baseline", {"true_code": "NORMAL"}, input_info=info)
        self.assertEqual(len(runtime.calls), 2)
        self.assertEqual(info["attempts"], 2)
        bad = FakeRuntime([])
        with patch.object(bad.client.chat.completions, "create", return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))])):
            with self.assertRaises(service.AnalysisFailure) as raised:
                service.predict(bad, "INFO baseline", {"true_code": "NORMAL"})
        self.assertEqual(raised.exception.category, "output_json")

    def test_json_mode_compatibility_fallback_does_not_change_shared_client(self):
        error = RuntimeError("response_format json_object is not supported")
        error.status_code = 400
        runtime = FakeRuntime([error, "NORMAL"])
        info = {}
        service.predict(runtime, "INFO baseline", {"true_code": "NORMAL"}, input_info=info)
        self.assertIn("response_format", runtime.calls[0])
        self.assertNotIn("response_format", runtime.calls[1])
        self.assertTrue(info["json_mode_fallback"])

    def test_authentication_and_context_errors_are_not_retried(self):
        for status, detail, category in [(401, "bad key", "authentication"),
                                         (400, "maximum context length exceeded", "context_length")]:
            error = RuntimeError(detail)
            error.status_code = status
            runtime = FakeRuntime([error])
            with self.assertRaises(RuntimeError):
                service.predict(runtime, "INFO", {"true_code": "NORMAL"})
            self.assertEqual(len(runtime.calls), 1)
            self.assertEqual(service._error_kind(error), (category, False))

    def test_failed_file_retains_input_length_and_attempts_without_writing_diagnosis(self):
        self.saved()
        runtime = FakeRuntime([TimeoutError("timeout secret-key"), TimeoutError("timeout secret-key"), "NORMAL"])
        with patch.object(service.time, "sleep"):
            batch = self.start(runtime)
        self.assertEqual(batch.failed, 1)
        row = next(item for item in service.snapshot(self.db)["items"] if item["state"] == "failed")
        self.assertEqual(row["input_info"]["attempts"], 2)
        self.assertEqual(row["input_info"]["error_category"], "timeout")
        self.assertGreater(row["input_info"]["input_characters"], 0)
        self.assertNotIn("secret-key", row["error_message"])
        self.assertEqual(self.db.query(DiagnosisRecord).count(), 1)

    def test_empty_logs_do_not_create_fabricated_diagnosis(self):
        with self.assertRaisesRegex(ValueError, "没有可诊断日志"):
            service.prepare_input(["", "   "])

    def test_unconfigured_model_is_not_rule_fallback(self):
        self.saved()
        with patch.object(service, "_try_lock", return_value=True), patch.object(
                service.llm_runtime_config_provider, "resolve", return_value=None):
            with self.assertRaisesRegex(ValueError, "启用大模型"):
                service.request_batch(self.db)
        self.assertEqual(self.db.query(EulerAnalysisBatch).count(), 0)

    def test_invalid_predictions_fail_instead_of_falling_back(self):
        self.saved()
        runtime = FakeRuntime(["bad-code", "bad-code"])
        batch = self.start(runtime)
        self.assertEqual(batch.failed, 2)
        self.assertEqual(self.db.query(DiagnosisRecord).count(), 0)
        self.assertEqual(service.snapshot(self.db)["pending"], 2)

    def test_restart_recovery_preserves_completed_and_retries_remainder(self):
        ids = self.saved()
        runtime = FakeRuntime(["F4512", "NORMAL"])
        batch = self.start(runtime)
        batch.state = "running"
        result = self.db.get(EulerAnalysisResult, ids[1])
        result.state = "running"
        self.db.query(DiagnosisRecord).filter_by(run_id=ids[1]).delete()
        self.db.commit()

        @contextmanager
        def isolated_worker_session():
            yield self.db

        with patch.object(service, "worker_session", isolated_worker_session):
            service.recover_batches()
        self.db.refresh(batch)
        self.db.refresh(result)
        self.assertEqual(batch.state, "interrupted")
        self.assertEqual(result.state, "interrupted")
        self.assertEqual(self.db.get(EulerAnalysisResult, ids[0]).state, "success")
        self.assertEqual(service.snapshot(self.db)["pending"], 1)
        retry = FakeRuntime(["NORMAL"])
        self.start(retry)
        self.assertEqual(len(retry.calls), 1)
        self.assertEqual(service.snapshot(self.db)["diagnosed"], 2)


if __name__ == "__main__":
    unittest.main()
