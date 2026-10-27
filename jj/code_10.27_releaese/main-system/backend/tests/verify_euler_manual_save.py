"""Opt-in integration check against the preview PostgreSQL database.

Creates explicitly simulated Euler records, keeps them for review, and never
deletes existing records. Mount the packaged guest source at /euler to run.
This is a manual verification script, not part of automatic unit discovery.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, "/euler/guest")
from euler_fault_agent import create_server

from app.api.v1 import virtual_prototype, log_analysis
from app.database import engine, SessionLocal, get_mongo_db
from app.models import EulerArtifactImport
from app.services import euler_artifact_import_service as imports
from app.services.euler_fault_agent_client import EulerFaultAgentClient


def snapshot():
    with engine.connect() as conn:
        # Full record hashes verify existing rows, not just counts or IDs.
        return {table: dict(conn.execute(text(
            f"SELECT {key}, md5(row_to_json(t)::text) FROM {table} t"
        )).all()) for table, key in (
            ("cases", "id"), ("runs", "id"), ("dataset_imports", "id"),
            ("mongo_docs", "pk"), ("training_tasks", "id"))}


def main():
    assert "--write-preview" in sys.argv, "Explicit --write-preview is required"
    EulerArtifactImport.__table__.create(engine, checkfirst=True)
    get_mongo_db()
    before = snapshot()
    app = FastAPI()
    app.include_router(virtual_prototype.router, prefix="/api/v1")
    app.include_router(log_analysis.router, prefix="/api/v1")
    results = []
    with tempfile.TemporaryDirectory(prefix="euler-manual-save-verification-") as temporary:
        server = create_server("127.0.0.1", 0, Path(temporary))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        agent = EulerFaultAgentClient(f"http://127.0.0.1:{server.server_port}")
        try:
            with patch.object(virtual_prototype, "_client", return_value=agent), TestClient(app) as api:
                def generate(code):
                    job = agent.create_job({"fault_type": code, "execution_mode": "baseline",
                        "sample_count": 1, "duration_seconds": 1, "seed": 20261009,
                        "options": {"evidence_mode": "simulated", "work_iterations": 1000}})
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        job = agent.get_job(job["job_id"])
                        if job["status"] not in {"queued", "running", "finalizing"}:
                            break
                        time.sleep(0.1)
                    assert job["status"] == "succeeded", job
                    assert job.get("artifact_path") and not job.get("artifact_error"), job
                    return job

                def state(job):
                    response = api.get(f"/api/v1/virtual-prototype/jobs/{job['job_id']}/database-import")
                    assert response.status_code == 200, response.text
                    return response.json()

                def save(job):
                    response = api.post(f"/api/v1/virtual-prototype/jobs/{job['job_id']}/database-import")
                    assert response.status_code == 202, response.text
                    return state(job)

                for code in imports.FAULT_LABELS:
                    job = generate(code)
                    pre_save = snapshot()
                    assert pre_save == (before if not results else after_save), "Generation wrote dataset records"
                    assert state(job)["state"] == "not_saved"
                    assert api.get(f"/api/v1/virtual-prototype/jobs/{job['job_id']}/artifact").status_code == 200
                    assert snapshot() == pre_save, "Downloading wrote dataset records"
                    saved = save(job)
                    assert saved["state"] == "success", saved
                    assert saved["result"]["run_count"] == 2, saved
                    assert saved["result"]["evidence_modes"] == ["simulated"]
                    rows = api.get("/api/v1/log-analysis/logs", params={"data_category": "semi",
                        "run_id": saved["result"]["run_ids"][0].rsplit("_round_", 1)[0]}).json()
                    assert rows["total"] == 2, rows
                    assert {item["fault_status"] for item in rows["items"]} == {"fault", "normal"}
                    for item in rows["items"]:
                        assert item["system_id"] == "euler" and item["data_category"] == "semi"
                        assert item["evidence_mode"] == "simulated" and item["injection_performed"] is False
                        assert item["parsed_lines"] > 0 and item["window_count"] > 0
                        for endpoint in ("entries", "windows"):
                            response = api.get(f"/api/v1/log-analysis/logs/{item['run_id']}/{endpoint}")
                            assert response.status_code == 200 and response.json()["total"] > 0, response.text
                    after_save = snapshot()
                    # Even when Agent is offline, a successful import is idempotent.
                    with patch.object(EulerFaultAgentClient, "get_job", side_effect=RuntimeError("offline")):
                        assert save(job)["state"] == "success"
                    assert snapshot() == after_save, "Repeated save duplicated records"
                    results.append({"job_id": job["job_id"], **saved["result"]})

                # A failed transaction must remove both SQL records and JSONB logs.
                job = generate("F4512")
                original_ingest = imports.ingest_samples
                def fail_after_ingest(*args, **kwargs):
                    original_ingest(*args, **kwargs)
                    raise RuntimeError("verification: rollback after parsed logs and metadata")
                with patch.object(imports, "ingest_samples", side_effect=fail_after_ingest):
                    assert save(job)["state"] == "failed"
                assert snapshot() == after_save, "Failed import left partial dataset records"
                assert "rollback" in state(job)["error_message"]
                # Download failure remains retryable, with zero dataset writes.
                with patch.object(EulerFaultAgentClient, "save_artifact", side_effect=RuntimeError("verification: download interrupted")):
                    assert save(job)["state"] == "failed"
                assert snapshot() == after_save
                # Concurrent retries: only one successful dataset publication.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda _: api.post(
                        f"/api/v1/virtual-prototype/jobs/{job['job_id']}/database-import"), range(2)))
                assert all(response.status_code in {202, 409} for response in responses)
                saved = state(job)
                assert saved["state"] == "success", saved
                results.append({"job_id": job["job_id"], **saved["result"]})
                all_ids = [rid for result in results for rid in result["run_ids"]]
                assert len(set(all_ids)) == 22
                with SessionLocal() as db:
                    assert db.execute(text("SELECT count(*) FROM runs WHERE run_id = ANY(:ids)"), {"ids": all_ids}).scalar() == 22
                    interrupted_id = imports.import_identity(agent.base_url, "job-interruptedverification")
                    db.add(EulerArtifactImport(import_id=interrupted_id, job_id="job-interruptedverification",
                        agent_url=agent.base_url, state="saving"))
                    db.commit()
                imports.recover_interrupted_imports()
                with SessionLocal() as db:
                    assert db.get(EulerArtifactImport, interrupted_id).state == "failed"

                after = snapshot()
                for table, old in before.items():
                    assert all(after[table].get(key) == value for key, value in old.items()), f"Existing {table} data changed"
                summary = {"checked_at": datetime.now().isoformat(), "source": "isolated simulated HTTP agent using packaged exporter",
                    "actual_vm_injection": False, "database": "existing preview PostgreSQL", "successful_jobs": len(results),
                    "cases_added": len(after["cases"]) - len(before["cases"]), "runs_added": len(after["runs"]) - len(before["runs"]),
                    "entries": sum(result["entry_count"] for result in results), "windows": sum(result["window_count"] for result in results),
                    "old_records_unchanged": True, "checks": ["all ten types", "no automatic save", "download does not save",
                    "online semi list and log/window reads", "offline repeated save", "atomic rollback", "download failure retry",
                    "concurrent save", "restart recovery"], "jobs": results}
                report = Path("/app/data/imports/euler/manual-save-verification.json")
                report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                print("VERIFICATION_RESULT " + json.dumps(summary, ensure_ascii=False), flush=True)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
