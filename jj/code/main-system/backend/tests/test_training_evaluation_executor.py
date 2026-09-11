import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.training_task import (
    TrainingArtifact, TrainingEvaluation, TrainingTask, TrainingWorker,
)
from app.training_worker.executor import WorkerServices, execute_task
from app.training_worker.materializer import MaterializedTask


EVALUATION_METRICS = {
    "json_valid": 1.0,
    "has_error_accuracy": 0.9,
    "fault_type_status_accuracy": 0.8,
    "known_fault_type_accuracy": 0.7,
    "new_candidate_detection": 0.6,
    "evidence_recall": 0.5,
    "affected_component_match": 0.4,
    "affected_function_match": 0.3,
    "repair_suggestion_presence": 0.2,
    "root_cause_keyword_overlap": 0.1,
}


class EvaluationContractTests(unittest.TestCase):
    def test_backend_score_keys_match_model_evaluator(self):
        from app.training_contracts import EVALUATION_SCORE_KEYS
        from embedded_fault_diag.eval_open_set import SCORE_KEYS

        self.assertEqual(EVALUATION_SCORE_KEYS, SCORE_KEYS)


def _write_safetensors(path: Path) -> None:
    header = json.dumps(
        {"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}},
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0\0\0\0")


def _evaluation_row() -> str:
    return json.dumps({
        "id": "sample-1",
        "task_type": "kernel",
        "messages": [
            {"role": "user", "content": "diagnose"},
            {"role": "assistant", "content": json.dumps({"has_error": False})},
        ],
    }) + "\n"


class EvaluationProcess:
    pid = 984
    returncode = None

    def __init__(self, spec_path, fail=False, metrics=None):
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        self.fail = fail
        Path(spec["log_path"]).write_text(
            f"evaluation started at {spec['task_root']} sk-private-evaluation\n",
            encoding="utf-8",
        )
        if not fail:
            summary = {"sft": {"metrics": metrics if metrics is not None else EVALUATION_METRICS, "records": [{
                "id": "sample-1",
                "prediction_path": "/private/evaluation/prediction.txt",
            }]}}
            summary_path = Path(spec["evaluation_output_dir"]) / "summary.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            Path(spec["result_path"]).write_text(
                json.dumps({"status": "succeeded", "evaluation_summary": str(summary_path)}),
                encoding="utf-8",
            )

    def poll(self):
        self.returncode = 1 if self.fail else 0
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class TrainingEvaluationExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "outputs"; self.root.mkdir()
        self.model = Path(self.temp_dir.name) / "model"; self.model.mkdir()
        (self.model / "config.json").write_text("{}", encoding="utf-8")
        self.engine = create_engine("sqlite://")
        for table in (TrainingTask.__table__, TrainingArtifact.__table__,
                      TrainingEvaluation.__table__, TrainingWorker.__table__):
            table.create(bind=self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.process_should_fail = False
        self.evaluation_metrics = EVALUATION_METRICS
        self.specs = []

    def tearDown(self):
        self.engine.dispose(); self.temp_dir.cleanup()

    def _setup_graph(self):
        with self.sessions.begin() as db:
            source = TrainingTask(name="sft", task_type="sft", job_kind="training",
                state="succeeded", model_id="qwen-qwen3.5-9b", config_snapshot={})
            db.add(source); db.flush()
            source_data = self.root / source.id / "data"; source_data.mkdir(parents=True)
            test_file = source_data / "test.jsonl"; test_file.write_text(_evaluation_row(), encoding="utf-8")
            adapter_dir = self.root / source.id / "run" / "best_adapter"; adapter_dir.mkdir(parents=True)
            (adapter_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
            _write_safetensors(adapter_dir / "adapter_model.safetensors")
            artifact_dir = self.root / source.id / "artifacts"
            artifact_dir.mkdir()
            adapter_archive = artifact_dir / "final_adapter.tar"
            with tarfile.open(adapter_archive, "w") as archive:
                archive.add(adapter_dir / "adapter_config.json", arcname="adapter_config.json")
                archive.add(adapter_dir / "adapter_model.safetensors", arcname="adapter_model.safetensors")
            adapter = TrainingArtifact(task_id=source.id, artifact_type="final_adapter",
                                       relative_path=f"{source.id}/artifacts/final_adapter.tar")
            db.add(adapter); db.flush()
            evaluation = TrainingTask(name="eval", task_type="sft", job_kind="evaluation",
                state="preparing_data", model_id="qwen-qwen3.5-9b", parent_task_id=source.id,
                worker_id="gpu-worker-1", config_snapshot={})
            db.add(evaluation); db.flush()
            db.add(TrainingEvaluation(task_id=evaluation.id, source_sft_task_id=source.id,
                                      status="preparing_data"))
            worker = db.get(TrainingWorker, "gpu-worker-1")
            if worker is None:
                worker = TrainingWorker(id="gpu-worker-1")
                db.add(worker)
            worker.status = "busy"
            worker.current_task_id = evaluation.id
            return source.id, evaluation.id, test_file, adapter_archive

    def _materialize(self, _db, task, task_dir):
        data = Path(task_dir) / "data"; data.mkdir(parents=True, exist_ok=True)
        paths = MaterializedTask(data / "split.json", data / "train.jsonl",
                                 data / "validation.jsonl", data / "test.jsonl")
        for path in paths.__dict__.values():
            Path(path).write_text(_evaluation_row() if Path(path).suffix == ".jsonl" else "{}\n", encoding="utf-8")
        return paths

    def _popen(self, argv, **_kwargs):
        self.specs.append(json.loads(Path(argv[-1]).read_text(encoding="utf-8")))
        return EvaluationProcess(
            argv[-1], self.process_should_fail, self.evaluation_metrics
        )

    def _services(self):
        return WorkerServices(session_factory=self.sessions, output_root=self.root,
            base_model_path=self.model, worker_id="gpu-worker-1", materialize=self._materialize,
            process_factory=self._popen, sleep=lambda _s: None, heartbeat_seconds=0,
            gpu_snapshot_provider=lambda: {"index": 0, "memory_free_mb": 22000},
            disk_usage=lambda _p: SimpleNamespace(free=100 * 1024**3), environ={"PATH": "/bin"})

    def test_manual_evaluation_uses_source_frozen_test_and_final_sft_only(self):
        source_id, evaluation_id, test_file, _adapter_archive = self._setup_graph()

        result = execute_task(evaluation_id, self._services())

        self.assertEqual(result.state, "succeeded")
        spec = self.specs[0]
        self.assertEqual(spec["evaluation_checkpoint"], "sft")
        self.assertIsNone(spec["config_sha256"])
        self.assertEqual(Path(spec["test_file"]), self.root / evaluation_id / "inputs" / "test.jsonl")
        self.assertEqual(Path(spec["sft_adapter_path"]), self.root / evaluation_id / "inputs" / "sft-adapter")
        self.assertEqual(Path(spec["test_file"]).read_bytes(), test_file.read_bytes())
        with self.sessions() as db:
            source = db.get(TrainingTask, source_id)
            evaluation = db.query(TrainingEvaluation).filter_by(task_id=evaluation_id).one()
            self.assertEqual(source.state, "succeeded")
            self.assertEqual(evaluation.status, "succeeded")
            self.assertEqual(evaluation.summary, EVALUATION_METRICS)
            report = db.query(TrainingArtifact).filter_by(task_id=evaluation_id,
                                                          artifact_type="evaluation_report").one()
            self.assertTrue(report.relative_path.endswith("summary.json"))
            public_report = json.loads((self.root / report.relative_path).read_text(encoding="utf-8"))
            self.assertEqual(public_report, {"metrics": EVALUATION_METRICS})
            self.assertNotIn("prediction_path", json.dumps(public_report))
            log = db.query(TrainingArtifact).filter_by(
                task_id=evaluation_id,
                artifact_type="log",
            ).one()
            public_log = (self.root / log.relative_path).read_text(encoding="utf-8")
            self.assertIn("evaluation started", public_log)
            self.assertIn("[REDACTED_PATH]", public_log)
            self.assertNotIn(str(self.root), public_log)
            self.assertNotIn("sk-private-evaluation", public_log)

    def test_evaluation_preserves_non_applicable_score_keys_as_null(self):
        _source_id, evaluation_id, _test_file, _adapter_archive = self._setup_graph()
        expected = dict(EVALUATION_METRICS)
        expected["new_candidate_detection"] = None
        self.evaluation_metrics = expected

        result = execute_task(evaluation_id, self._services())

        self.assertEqual(result.state, "succeeded")
        with self.sessions() as db:
            evaluation = db.query(TrainingEvaluation).filter_by(task_id=evaluation_id).one()
            self.assertEqual(evaluation.summary, expected)
            report = db.query(TrainingArtifact).filter_by(
                task_id=evaluation_id,
                artifact_type="evaluation_report",
            ).one()
            public_report = json.loads(
                (self.root / report.relative_path).read_text(encoding="utf-8")
            )
            self.assertEqual(public_report, {"metrics": expected})

    def test_evaluation_rejects_non_exact_or_empty_score_summaries(self):
        cases = {
            "missing": {
                key: value
                for key, value in EVALUATION_METRICS.items()
                if key != "json_valid"
            },
            "unknown": {**EVALUATION_METRICS, "private_metric": 1.0},
            "all_null": {key: None for key in EVALUATION_METRICS},
            "non_finite": {**EVALUATION_METRICS, "json_valid": float("nan")},
        }
        for name, metrics in cases.items():
            with self.subTest(name=name):
                _source_id, evaluation_id, _test_file, _adapter = self._setup_graph()
                self.evaluation_metrics = metrics

                result = execute_task(evaluation_id, self._services())

                self.assertEqual(result.state, "failed")
                with self.sessions() as db:
                    evaluation = db.query(TrainingEvaluation).filter_by(
                        task_id=evaluation_id
                    ).one()
                    self.assertEqual(evaluation.status, "failed")
                    self.assertIsNone(evaluation.summary)

    def test_evaluation_failure_does_not_change_source_sft_success(self):
        source_id, evaluation_id, _test_file, _adapter = self._setup_graph()
        self.process_should_fail = True

        result = execute_task(evaluation_id, self._services())

        self.assertEqual(result.state, "failed")
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, source_id).state, "succeeded")
            row = db.query(TrainingEvaluation).filter_by(task_id=evaluation_id).one()
            self.assertEqual(row.status, "failed")
            self.assertIsNotNone(row.error_message)

    def test_evaluation_rejects_an_incomplete_source_adapter_before_spawn(self):
        source_id, evaluation_id, _test_file, adapter_archive = self._setup_graph()
        adapter_archive.write_bytes(b"not a tar archive")

        result = execute_task(evaluation_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.specs, [])
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, source_id).state, "succeeded")
            row = db.query(TrainingEvaluation).filter_by(task_id=evaluation_id).one()
            self.assertEqual(row.status, "failed")

    def test_evaluation_rejects_a_symlinked_task_local_input_sink(self):
        source_id, evaluation_id, _test_file, _adapter_archive = self._setup_graph()
        task_root = self.root / evaluation_id
        task_root.mkdir()
        outside = Path(self.temp_dir.name) / "outside-inputs"
        outside.mkdir()
        (task_root / "inputs").symlink_to(outside, target_is_directory=True)

        result = execute_task(evaluation_id, self._services())

        self.assertEqual(result.state, "failed")
        self.assertEqual(self.specs, [])
        self.assertEqual(list(outside.iterdir()), [])
        with self.sessions() as db:
            self.assertEqual(db.get(TrainingTask, source_id).state, "succeeded")
            row = db.query(TrainingEvaluation).filter_by(task_id=evaluation_id).one()
            self.assertEqual(row.status, "failed")


if __name__ == "__main__":
    unittest.main()
