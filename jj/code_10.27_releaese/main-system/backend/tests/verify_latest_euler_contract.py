"""Run with latest guest on PYTHONPATH; no DB or real fault injection."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import euler_fault_dataset as dataset
from euler_fault_actions import ActionResult
from euler_fault_models import JobSpec
from app.schemas.virtual_prototype import VirtualPrototypeJobCreate
from app.services.euler_artifact_import_service import validate_archive


def verify(code):
    with tempfile.TemporaryDirectory(prefix="latest-euler-contract-") as tmp:
        root = Path(tmp) / "guest"
        payload = VirtualPrototypeJobCreate(
            fault_type=code,
            task_kind="augmentation",
            sample_count=15,
            options={"evidence_mode": "observed", "capability_policy": "native_only"},
        ).model_dump(exclude_none=True)
        payload.pop("sample_class")
        payload.update(job_id="job-contract", output_root=str(root), execution_mode="baseline")
        spec = JobSpec.from_mapping(payload)
        assert spec.duration_seconds == 1
        dataset._capability_probe = lambda _: {"python3": True, "dev_tcp": True, "loopback_tcp": True,
            "writable_cpu_cgroup": {"status": "supported", "detail": "stub only; no real cgroup action"}}
        def runner(plan, path, stage):
            signal = {"F4512": {"workers": 1}, "F5203": {"listening_ports_delta": 2},
                      "F5402": {"owned_established_count": 2}, "F2301": {"nr_throttled_delta": 1, "throttled_seconds_delta": 0.1}}
            return ActionResult("completed", 0, "native_bounded", plan.parameters, [signal[plan.fault_type]], True)
        # The runner is a local stub; no worker, socket or cgroup mutation is performed.
        with patch("euler_fault_cgroup_cpu.preflight_cpu_cgroup"), \
                patch("euler_fault_resources.recover_owned_resources", return_value=True):
            summary = dataset.run_job(spec, runner, root)
        assert summary.status == "succeeded", summary.to_mapping()
        ground = json.loads((root / "EulerRT" / summary.published_samples[0] / "ground_truth.json").read_text())
        record = json.loads(json.dumps({"job_id": spec.job_id, "job": spec.to_mapping(),
            "status": "succeeded", "summary": summary.to_mapping(),
            **{key: ground.get(key) for key in ("requested_fault_type", "executed_fault_type", "fallback_reason",
                                              "simulation_reason", "missing_capabilities")}}))
        archive = dataset._archive(root, spec.job_id, None)
        samples = validate_archive(archive, Path(tmp) / "extracted", record, 100_000_000)
        assert len(samples) == 15
        assert ground["fault_type"] == code
        assert ground["requested_fault_type"] == code
        assert ground["executed_fault_type"] == code
        assert ground["evidence_mode"] == "observed" and ground["injection_performed"]
        assert not ground.get("fallback_reason")
        print(code, "15 samples accepted", ground["evidence_mode"])


if __name__ == "__main__":
    for code in ("F4512", "F2301", "F5203", "F5402"):
        verify(code)
