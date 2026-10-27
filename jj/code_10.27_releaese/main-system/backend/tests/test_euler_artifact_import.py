import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from app.services.euler_artifact_import_service import (
    FAULT_LABELS, _REQUIRED_LOGS, import_identity, validate_archive, validate_job,
)
from app.services.log_dataset_service import build_run_filters, infer_data_category, shape_run_row


def fixture_archive(archive, job_id="job-savefixture", code="F1101", *, mutate=None, extra=None):
    """Small contract fixture; live validation separately uses the real exporter."""
    name = "Test_200001"
    summary = {"job_id": job_id, "status": "succeeded", "sample_succeeded": 1,
               "sample_total": 1, "sample_failed": 0, "published_samples": [name],
               "fault_type": code, "requested_fault_type": code, "executed_fault_type": code}
    ground = {"version": "v3", "source_session_id": f"{job_id}:{name}", "fault_type": code,
              "requested_fault_type": code, "executed_fault_type": code, "fallback_reason": None,
              "primary_fault_signal": "fixture_signal", "execution_mode": "baseline",
              "evidence_mode": "observed" if code in {"F2301", "F5203", "F5402"} else "simulated",
              "injection_performed": code in {"F2301", "F5203", "F5402"}}
    if mutate:
        mutate(ground)
    provenance = {key: ground[key] for key in ("fault_type", "requested_fault_type", "executed_fault_type",
                                                "fallback_reason", "primary_fault_signal", "evidence_mode")}
    action = {**provenance, "injection_performed": ground["injection_performed"]}
    files = {"ground_truth.json": json.dumps(ground).encode(), "fip_info.data": f"FAULT_TYPE: {code}\n".encode(),
             "audit/action_results.json": json.dumps({"fault": action, "baseline": action}).encode()}
    for round_no in (1, 2):
        for log in _REQUIRED_LOGS:
            payload = json.dumps(provenance, separators=(",", ":"))
            files[f"logs/round_{round_no}/{log}"] = f"2026-10-09T01:00:00Z euler 123 INFO fault.injector [- - -] {payload}\n".encode()
    entries = [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for path, data in sorted(files.items())]
    manifest = {"test_name": name, "status": "published", "files": entries}
    manifest["sha256"] = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    files["artifact_manifest.json"] = json.dumps(manifest).encode()
    with tarfile.open(archive, "w:gz") as handle:
        for path, data in files.items():
            member = tarfile.TarInfo(f"EulerRT/{name}/{path}")
            member.size = len(data)
            handle.addfile(member, io.BytesIO(data))
        data = json.dumps(summary).encode()
        member = tarfile.TarInfo(f"jobs/{job_id}/summary.json")
        member.size = len(data)
        handle.addfile(member, io.BytesIO(data))
        if extra:
            handle.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    return {"job_id": job_id, "status": "succeeded", "job": {"execution_mode": "baseline", "fault_type": code}, "summary": summary}


class EulerImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self, **kwargs):
        archive = self.root / "archive.tar.gz"
        record = fixture_archive(archive, **kwargs)
        return validate_archive(archive, self.root / "extracted", record, 1024 * 1024)

    def test_complete_simulated_archive_preserves_provenance(self):
        samples = self.validate()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][1]["evidence_mode"], "simulated")
        self.assertFalse(samples[0][1]["injection_performed"])

    def test_all_ten_fault_codes_use_the_same_import_contract(self):
        for index, code in enumerate(FAULT_LABELS):
            with self.subTest(code=code):
                archive = self.root / f"{code}.tar.gz"
                record = fixture_archive(archive, code=code)
                samples = validate_archive(archive, self.root / str(index), record, 1024 * 1024)
                self.assertEqual(samples[0][1]["fault_type"], code)

    def test_identity_is_stable_but_separates_agents_and_jobs(self):
        self.assertEqual(import_identity("http://agent:8080/", "job-a"), import_identity("http://agent:8080", "job-a"))
        self.assertNotEqual(import_identity("http://agent:8080", "job-a"), import_identity("http://agent:8080", "job-b"))
        self.assertNotEqual(import_identity("http://agent:8080", "job-a"), import_identity("http://other:8080", "job-a"))

    def test_replacement_codes_reject_simulated_artifacts(self):
        for index, code in enumerate(("F2301", "F5203", "F5402")):
            archive = self.root / f"simulated-{code}.tar.gz"
            record = fixture_archive(archive, code=code, mutate=lambda ground: ground.update(
                evidence_mode="simulated", injection_performed=False))
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "真实注入"):
                validate_archive(archive, self.root / f"sim-{index}", record, 1024 * 1024)

    def test_metadata_identity_disagreement_is_rejected(self):
        for field in ("fault_type", "requested_fault_type", "executed_fault_type"):
            archive = self.root / "identity.tar.gz"
            record = fixture_archive(archive, code="F5203")
            record[field] = "F5201"
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_job(record, record["job_id"])

    def test_each_manifest_file_is_verified(self):
        archive = self.root / "original.tar.gz"
        record = fixture_archive(archive)
        with tarfile.open(archive) as handle:
            contents = {member.name: handle.extractfile(member).read() for member in handle if member.isfile()}
        paths = [name for name in contents if name.startswith("EulerRT/")
                 and not name.endswith("artifact_manifest.json")]
        for index, path in enumerate(paths):
            damaged = self.root / f"damaged-{index}.tar.gz"
            with tarfile.open(damaged, "w:gz") as handle:
                for name, original in contents.items():
                    # JSON whitespace/log trailing whitespace keeps content parseable.
                    data = original + b" " if name == path else original
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    handle.addfile(member, io.BytesIO(data))
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "SHA256"):
                validate_archive(damaged, self.root / f"bad-{index}", record, 1024 * 1024)

    def test_missing_manifest_and_unlisted_files_are_rejected(self):
        archive = self.root / "original.tar.gz"
        record = fixture_archive(archive)
        with tarfile.open(archive) as handle:
            contents = {member.name: handle.extractfile(member).read() for member in handle if member.isfile()}
        for index, missing in enumerate((True, False)):
            changed = dict(contents)
            if missing:
                del changed["EulerRT/Test_200001/artifact_manifest.json"]
            else:
                changed["EulerRT/Test_200001/unlisted.log"] = b"unlisted"
            with tarfile.open(archive, "w:gz") as handle:
                for name, data in changed.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    handle.addfile(member, io.BytesIO(data))
            with self.subTest(missing=missing), self.assertRaises((ValueError, FileNotFoundError)):
                validate_archive(archive, self.root / f"missing-{index}", record, 1024 * 1024)

    def test_traversal_rejected_without_writing_outside_extraction(self):
        member = tarfile.TarInfo("../escape.txt")
        member.size = 1
        with self.assertRaises(ValueError):
            self.validate(extra=member)
        self.assertFalse((self.root / "escape.txt").exists())

    def test_links_rejected(self):
        member = tarfile.TarInfo("EulerRT/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        with self.assertRaises(ValueError):
            self.validate(extra=member)

    def test_foreign_job_sample_rejected(self):
        with self.assertRaisesRegex(ValueError, "来源"):
            self.validate(mutate=lambda ground: ground.update(source_session_id="job-other:Test_200001"))

    def test_simulated_injection_claim_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(mutate=lambda ground: ground.update(injection_performed=True))

    def test_sample_replay_not_accepted_as_data_expansion(self):
        record = {"job_id": "job-demo", "status": "succeeded", "job": {"sample_id": "sample-f4512-high-load", "execution_mode": "baseline", "fault_type": "F4512"}}
        with self.assertRaisesRegex(ValueError, "演示"):
            validate_job(record, "job-demo")

    def test_unsuccessful_job_is_not_importable(self):
        with self.assertRaises(ValueError):
            validate_job({"job_id": "job-a", "status": "failed"}, "job-a")

    def test_old_fallback_identities_are_not_importable(self):
        for requested, executed in (("F2305", "F2301"), ("F5201", "F5203"), ("F5403", "F5402")):
            with self.subTest(requested=requested):
                record = {
                    "job_id": "job-fallback",
                    "status": "succeeded",
                    "requested_fault_type": requested,
                    "executed_fault_type": executed,
                    "fallback_reason": "explicit fixed alternative",
                    "job": {
                        "fault_type": requested,
                        "task_kind": "augmentation",
                        "sample_count": 15,
                        "execution_mode": "baseline",
                        "options": {"fallback_fault_type": executed},
                    },
                    "summary": {"sample_total": 15},
                }
                with self.assertRaises(ValueError):
                    validate_job(record, record["job_id"])

    def test_unknown_execution_type_is_rejected_even_when_requested_type_is_known(self):
        record = {
            "job_id": "job-fallback",
            "status": "succeeded",
            "requested_fault_type": "F5201",
            "executed_fault_type": "F5402",
            "fallback_reason": "forged",
            "job": {"fault_type": "F5201", "task_kind": "augmentation", "sample_count": 15,
                    "execution_mode": "baseline"},
        }
        with self.assertRaisesRegex(ValueError, "匹配|类型"):
            validate_job(record, record["job_id"])

    def test_manifest_corruption_and_size_limit_rejected(self):
        archive = self.root / "archive.tar.gz"
        record = fixture_archive(archive)
        with self.assertRaisesRegex(ValueError, "超过限制"):
            validate_archive(archive, self.root / "oversized", record, 10)
        # Existing fixture validator must not trust manifest digests supplied by a caller.
        with tarfile.open(archive) as handle:
            contents = {member.name: handle.extractfile(member).read() for member in handle if member.isfile()}
        path = "EulerRT/Test_200001/logs/round_1/monitor/system_metrics.log"
        contents[path] += b"tampered"
        with tarfile.open(archive, "w:gz") as handle:
            for name, data in contents.items():
                member = tarfile.TarInfo(name); member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            validate_archive(archive, self.root / "tampered", record, 1024 * 1024)

    def test_euler_category_and_source_are_exposed_without_changing_old_categories(self):
        self.assertEqual(infer_data_category({"system_id": "euler", "run_id": "euler_abc_round_1"}), "semi")
        self.assertEqual(infer_data_category({"system_id": "nuttx", "run_id": "nuttx_Test_1_round_1"}), "semi")
        self.assertEqual(infer_data_category({"run_id": "Test_1:round_1"}), "structured")
        self.assertEqual(infer_data_category({"run_id": "seg_1"}), "segment")
        where, params = build_run_filters(run_id=None, case_id=None, test_name=None, fault_status=None, system_id=None, data_category="semi")
        self.assertIn("r.system_id = :dc_euler", where)
        self.assertEqual(params["dc_euler"], "euler")
        shaped = shape_run_row({"run_id": "euler_abc_round_1", "system_id": "euler", "stats_json": {"euler_provenance": {"evidence_mode": "simulated", "injection_performed": False}}})
        self.assertEqual(shaped["evidence_mode"], "simulated")
        self.assertFalse(shaped["injection_performed"])


if __name__ == "__main__":
    unittest.main()
