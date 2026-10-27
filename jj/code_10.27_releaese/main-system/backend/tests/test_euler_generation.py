import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from app.schemas.virtual_prototype import FAULT_TYPES, VirtualPrototypeJobCreate
from app.services.euler_generation_service import select_execution
from app.services.euler_fault_agent_client import EulerFaultAgentError
from app.api.v1.virtual_prototype import create_job, get_job, list_jobs, download_artifact


class DirectGenerationTests(unittest.TestCase):
    def test_supported_capabilities_select_the_same_code(self):
        for code in FAULT_TYPES:
            catalog = {"capabilities": {code: [{"status": "supported"}]}}
            self.assertEqual(select_execution(code, catalog), (code, {}))

    def test_unknown_or_unsupported_capability_is_rejected(self):
        for checks in ([], [{"status": "unknown"}], [{"status": "unsupported"}]):
            with self.assertRaises(ValueError):
                select_execution("F4512", {"capabilities": {"F4512": checks}})

    def test_all_ten_routes_forward_direct_identity_and_no_fallback(self):
        for code in FAULT_TYPES:
            client = Mock()
            payload = VirtualPrototypeJobCreate(fault_type=code, task_kind="augmentation", sample_count=15)
            with patch("app.api.v1.virtual_prototype._client", return_value=client):
                create_job(payload)
            body = client.create_job.call_args.args[0]
            self.assertEqual(body["fault_type"], code)
            self.assertEqual(body["execution_mode"], "baseline")
            self.assertEqual(body["sample_count"], 15)
            self.assertNotIn("duration_seconds", body)
            for key in ("fallback_fault_type", "fallback_reason", "auto_select_execution",
                        "expected_executed_fault_type", "sample_class"):
                self.assertNotIn(key, body)
                self.assertNotIn(key, body.get("options", {}))
            client.capabilities.assert_not_called()

    def test_agent_failure_is_preserved_without_retry_or_simulation(self):
        client = Mock()
        client.create_job.side_effect = EulerFaultAgentError("CPU capability unavailable", 409)
        payload = VirtualPrototypeJobCreate(fault_type="F2301")
        with patch("app.api.v1.virtual_prototype._client", return_value=client), self.assertRaises(HTTPException) as caught:
            create_job(payload)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("CPU capability unavailable", caught.exception.detail)
        client.create_job.assert_called_once()

    def test_history_routes_preserve_old_identity(self):
        client = Mock()
        old = {"job_id": "job-old", "status": "succeeded", "job": {"fault_type": "F5201"}}
        client.get_job.return_value = old
        client.list_jobs.return_value = {"jobs": [old]}
        with patch("app.api.v1.virtual_prototype._client", return_value=client):
            self.assertEqual(get_job("job-old"), old)
            self.assertEqual(list_jobs(50, 0)["jobs"], [old])

    def test_replay_download_does_not_reject_low_speedup(self):
        client = Mock()
        client.artifact.return_value.content = b"verified upstream archive"
        with patch("app.api.v1.virtual_prototype._client", return_value=client):
            result = download_artifact("job-replay")
        self.assertEqual(result.body, b"verified upstream archive")
        client.artifact.assert_called_once_with("job-replay")

    def test_upstream_unavailable_archive_stays_unavailable(self):
        client = Mock()
        client.artifact.side_effect = EulerFaultAgentError("manifest SHA256 mismatch", 409)
        with patch("app.api.v1.virtual_prototype._client", return_value=client), self.assertRaises(HTTPException) as caught:
            download_artifact("job-replay")
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
