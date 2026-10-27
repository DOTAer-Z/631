"""Explicit, idempotent Euler imports into the online log dataset (not training)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, engine, get_mongo_db
from app.models import Case, DatasetImport, EulerArtifactImport, Run, System
from app.mongo_compat import PgMongoDatabase
from app.services.euler_fault_agent_client import EulerFaultAgentClient
from app.services.log_ingest_service import RunMeta, log_ingest_service
from app.services.log_window_service import log_window_service
from app.services.preprocessing_service import preprocessing_service

logger = logging.getLogger(__name__)
FAULT_LABELS = {
    "F1101": "硬件运行参数越限(CPU 核心温度超限)",
    "F1206": "任务/线程运行与调度异常(进程/线程调度异常)",
    "F2301": "虚拟资源或隔离异常(CPU 配额限流)",
    "F3409": "设备访问与驱动运行异常(硬中断线数量激增)",
    "F4512": "任务/线程运行与调度异常(系统长期高负载)",
    "F4620": "系统资源与核心服务异常(可用内存枯竭)",
    "F5106": "输入数据有效性异常(UDP 校验和错误)",
    "F5203": "应用逻辑/状态机异常(监听端口数量异常增长)",
    "F5313": "功能输出异常/功能失效(TCP 建连失败)",
    "F5402": "功能时序与性能退化异常(已建立 TCP 连接数量异常增长)",
}
EXECUTED_FAULT_LABELS = FAULT_LABELS
# Historical task identities remain readable; new jobs and imports require
# requested_fault_type == executed_fault_type and never use this mapping.
FALLBACKS = {"F2305": "F2301", "F5201": "F5203", "F5403": "F5402"}
_REQUIRED_LOGS = ("faults/fault_events.log", "foreground_wl/workload.log",
                  "monitor/system_metrics.log", "euler/qemu_console.log")


def import_identity(agent_url: str, job_id: str) -> str:
    return "euler-" + hashlib.sha256(f"{agent_url.rstrip('/')}\n{job_id}".encode()).hexdigest()[:40]


def _lock_key(import_id: str) -> int:
    return int.from_bytes(hashlib.sha256(import_id.encode()).digest()[:8], "big", signed=True)


def shape_import(row: EulerArtifactImport) -> dict:
    return {"import_id": row.import_id, "job_id": row.job_id, "state": row.state,
            "error_message": row.error_message, "result": row.result_json,
            "created_at": row.created_at, "updated_at": row.updated_at}


def request_import(db: Session, client: EulerFaultAgentClient, job_id: str) -> tuple[dict, bool]:
    identity = import_identity(client.base_url, job_id)
    if not db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                      {"key": _lock_key(identity)}).scalar():
        raise ValueError("该任务正在保存，请稍后刷新")
    row = db.get(EulerArtifactImport, identity)
    if row and row.state in {"pending", "saving", "success"}:
        return shape_import(row), False
    # A GET never schedules an import; only this explicit POST creates a row.
    record = client.get_job(job_id)
    validate_job(record, job_id)
    if row is None:
        row = EulerArtifactImport(import_id=identity, agent_url=client.base_url, job_id=job_id)
        db.add(row)
    row.state = "pending"
    row.error_message = None
    db.commit()
    db.refresh(row)
    return shape_import(row), True


def list_imports(db: Session, agent_url: str) -> dict:
    rows = db.query(EulerArtifactImport).filter(EulerArtifactImport.agent_url == agent_url).all()
    return {"imports": [shape_import(row) for row in rows]}


def validate_job(record: dict, job_id: str) -> str:
    job = record.get("job") or {}
    if record.get("job_id") != job_id or record.get("status") != "succeeded":
        raise ValueError("只有成功生成的任务可以保存到数据库")
    if record.get("artifact_error"):
        raise ValueError("Agent 产物尚不可用：" + str(record["artifact_error"]))
    if job.get("sample_id") or job.get("execution_mode") != "baseline":
        raise ValueError("此入口仅保存数据扩充产物，不保存加速复现演示")
    options = job.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError("任务选项格式错误")
    summary = record.get("summary") or {}
    raw_code = job.get("fault_type")
    if raw_code not in FAULT_LABELS:
        raise ValueError("不支持的 Euler 故障类型")
    requested = (record.get("requested_fault_type") or summary.get("requested_fault_type")
                 or job.get("requested_fault_type") or options.get("requested_fault_type"))
    executed = (record.get("executed_fault_type") or summary.get("executed_fault_type")
                or job.get("executed_fault_type"))
    if not requested or not executed:
        raise ValueError("Agent 产物必须明确提供请求和实际执行故障类型")
    if requested != raw_code or executed != raw_code:
        raise ValueError("新任务的请求、任务和执行故障类型必须完全一致")
    if any(key in options for key in ("fallback_fault_type", "fallback_reason")):
        raise ValueError("新任务不允许包含 fallback 身份字段")
    if job.get("task_kind") == "augmentation" and job.get("sample_count") != 15:
        raise ValueError("数据扩充任务必须正好包含 15 个样本")
    reason = record.get("fallback_reason") or summary.get("fallback_reason") or job.get("fallback_reason")
    if reason is not None:
        raise ValueError("新任务不应包含 fallback_reason")
    for metadata in (record, summary, job, options):
        if metadata.get("fallback_reason") is not None:
            raise ValueError("新任务不应包含 fallback_reason")
        for key, value in (("fault_type", raw_code), ("requested_fault_type", requested), ("executed_fault_type", executed)):
            supplied = metadata.get(key)
            if supplied is not None and supplied != value:
                raise ValueError("请求和执行故障类型不匹配")
    if raw_code != requested or raw_code != executed:
        raise ValueError("任务故障类型与请求/执行类型不匹配")
    return executed


def _record_provenance(record: dict) -> tuple[str, str, str | None]:
    job = record.get("job") or {}
    summary = record.get("summary") or {}
    options = job.get("options") or {}
    requested = (record.get("requested_fault_type") or summary.get("requested_fault_type")
                 or job.get("requested_fault_type") or options.get("requested_fault_type")
                 or job.get("fault_type"))
    executed = (record.get("executed_fault_type") or summary.get("executed_fault_type")
                or job.get("executed_fault_type") or job.get("fault_type"))
    reason = (record.get("fallback_reason") or summary.get("fallback_reason")
              or job.get("fallback_reason") or options.get("fallback_reason"))
    return requested, executed, reason


def _validate_provenance(value: dict, expected: tuple[str, str, str | None]) -> None:
    requested, executed, reason = expected
    if (value.get("fault_type") != executed
            or value.get("requested_fault_type") != requested
            or value.get("executed_fault_type") != executed
            or value.get("fallback_reason") != reason
            or not isinstance(value.get("primary_fault_signal"), str)
            or not value.get("primary_fault_signal")):
        raise ValueError("产物中的故障执行来源不一致")


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"产物元数据格式错误：{path.name}")
    return value


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_archive(archive: Path, work: Path, record: dict, max_bytes: int) -> list[tuple[Path, dict]]:
    """Reject links, traversal, duplicates, oversized archives and foreign samples."""
    job_id = record["job_id"]
    code = validate_job(record, job_id)
    requested, executed, fallback_reason = _record_provenance(record)
    seen = set()
    total = 0
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in member.name
                    or not relative.parts or relative.parts[0] not in {"EulerRT", "jobs"}
                    or not (member.isdir() or member.isfile()) or relative.as_posix() in seen):
                raise ValueError("产物归档包含不安全或重复的路径")
            seen.add(relative.as_posix())
            total += member.size
            if len(seen) > 10000 or total > max_bytes or member.size > 32 * 1024 * 1024:
                raise ValueError("产物解压大小或文件数量超过限制")
            destination = work.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(member) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
    summary = _load_json(work / "jobs" / job_id / "summary.json")
    names = summary.get("published_samples") or []
    if (summary.get("job_id") != job_id or summary.get("status") != "succeeded"
            or not names or len(names) > 100 or len(set(names)) != len(names)
            or len(names) != summary.get("sample_succeeded")
            or len(names) != summary.get("sample_total") or summary.get("sample_failed") != 0
            or names != (record.get("summary") or {}).get("published_samples")):
        raise ValueError("产物清单与成功任务不一致")
    for key in ("fault_type", "requested_fault_type", "executed_fault_type"):
        if summary.get(key) is not None and summary[key] != code:
            raise ValueError("归档任务故障类型与请求不一致")
    if summary.get("fallback_reason") is not None:
        raise ValueError("新任务归档不应包含 fallback_reason")
    if ((record.get("job") or {}).get("task_kind") == "augmentation"
            and (summary.get("sample_total") != 15 or len(names) != 15)):
        raise ValueError("数据扩充归档必须包含 15 个完整发布样本")
    roots = work / "EulerRT"
    if {p.name for p in roots.iterdir()} != set(names):
        raise ValueError("产物包含其他任务的样本")
    samples = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"Test_[0-9]+", name):
            raise ValueError("无效的样本名称")
        directory = roots / name
        ground = _load_json(directory / "ground_truth.json")
        if (ground.get("source_session_id") != f"{job_id}:{name}"
                or ground.get("fault_type") != executed or ground.get("sample_id")
                or ground.get("execution_mode") != "baseline"
                or ground.get("evidence_mode") not in {"simulated", "observed"}
                or not isinstance(ground.get("injection_performed"), bool)
                or (ground.get("evidence_mode") == "simulated" and ground.get("injection_performed"))):
            raise ValueError("样本来源、故障类型或模拟标记不一致")
        if executed in {"F2301", "F5203", "F5402"} and (
                ground.get("evidence_mode") != "observed" or not ground.get("injection_performed")):
            raise ValueError("F2301/F5203/F5402 必须包含真实注入证据")
        _validate_provenance(ground, (requested, executed, fallback_reason))
        manifest = _load_json(directory / "artifact_manifest.json")
        files = manifest.get("files") or []
        digest_payload = {"test_name": name, "files": files, "status": "published"}
        manifest_hash = hashlib.sha256(json.dumps(digest_payload, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":")).encode()).hexdigest()
        if manifest.get("status") != "published" or manifest.get("test_name") != name or manifest.get("sha256") != manifest_hash:
            raise ValueError("样本 manifest 校验失败")
        listed = set()
        for item in files:
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or item["path"] in listed:
                raise ValueError("样本 manifest 包含无效路径")
            listed.add(item["path"])
            source = directory.joinpath(*path.parts)
            if source.stat().st_size != item["size_bytes"] or _digest(source) != item["sha256"]:
                raise ValueError("样本文件 SHA256 校验失败")
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*")
                  if p.is_file() and p.name != "artifact_manifest.json"}
        required = {"ground_truth.json", "fip_info.data", "audit/action_results.json"} | {
            f"logs/round_{round_no}/{path}" for round_no in (1, 2) for path in _REQUIRED_LOGS}
        if listed != actual or not required.issubset(listed):
            raise ValueError("样本日志不完整或未被 manifest 覆盖")
        fip = (directory / "fip_info.data").read_text(encoding="utf-8")
        if not fip.startswith(f"FAULT_TYPE: {executed}\n"):
            raise ValueError("fip_info 与实际执行故障类型不一致")
        for round_no in (1, 2):
            for relative in _REQUIRED_LOGS:
                for line in (directory / "logs" / f"round_{round_no}" / relative).read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line.split("[- - -] ", 1)[1])
                    except (IndexError, json.JSONDecodeError) as exc:
                        raise ValueError("样本日志不是受支持的结构化格式") from exc
                    _validate_provenance(payload, (requested, executed, fallback_reason))
        action_results = _load_json(directory / "audit" / "action_results.json")
        for key in ("fault", "baseline"):
            action = action_results.get(key)
            if not isinstance(action, dict):
                raise ValueError("样本 action output 不完整")
            _validate_provenance(action, (requested, executed, fallback_reason))
        samples.append((directory, ground))
    return samples


class _SessionEngine:
    """Let existing PG document services join the caller's transaction."""
    def __init__(self, db: Session):
        self.db = db

    @contextmanager
    def connect(self):
        yield self.db.connection()

    begin = connect


def ingest_samples(db: Session, row: EulerArtifactImport, samples: list, archive: Path) -> dict:
    # Documents and Case/Run/DatasetImport records commit together; no partial datasets.
    mongo = PgMongoDatabase(_SessionEngine(db))
    db.execute(insert(System).values(system_id="euler", name="Euler 虚拟样机")
               .on_conflict_do_nothing(index_elements=[System.system_id]))
    system = db.query(System).filter(System.system_id == "euler").one()
    code = samples[0][1]["fault_type"]
    requested = samples[0][1].get("requested_fault_type", code)
    fallback_reason = samples[0][1].get("fallback_reason")
    title = EXECUTED_FAULT_LABELS[code]
    requested_title = FAULT_LABELS.get(requested, requested)
    display_title = requested_title if requested == code else f"{requested_title} → {title}"
    meta = {"import_id": row.import_id, "original_filename": f"{row.job_id}.tar.gz",
            "display_name": f"Euler · {display_title} · {row.job_id}",
            "description": "手动保存的数据扩充产物；包含故障轮及正常基线轮",
            "tags": ["Euler", "半结构化", requested, code], "created_by": "Euler 数据扩充"}
    run_ids, case_ids = [], []
    entry_count = window_count = 0
    for directory, ground in samples:
        suffix = hashlib.sha256(f"{row.import_id}:{directory.name}".encode()).hexdigest()[:32]
        case_id = f"euler_{suffix}"
        simulated = ground["evidence_mode"] == "simulated"
        name = f"{title} · {directory.name}" + (" · 模拟" if simulated else "")
        db.add(Case(case_id=case_id, name=name, case_name=name, test_name=directory.name,
                    system_id=system.id, is_fault=True, fault_type_label=code,
                    description=ground.get("root_cause_text")))
        case_ids.append(case_id)
        for round_no in (1, 2):
            run_id = f"{case_id}_round_{round_no}"
            is_fault = round_no == 1
            raw = b"\n".join(p.read_bytes() for p in sorted((directory / "logs" / f"round_{round_no}").rglob("*"))
                              if p.is_file() and p.suffix in {".log", ".txt", ".out"})
            if not raw.strip():
                raise ValueError("样本轮次没有可解析日志")
            run_meta = RunMeta(run_id=run_id, task_id=row.job_id, source_type="dataset",
                               system_id="euler", subsystem="EULER_RT", case_id=case_id,
                               is_fault=is_fault, test_name=directory.name, round_no=round_no)
            pre = preprocessing_service.preprocess(raw_log=raw, source_type="dataset", filename=f"{run_id}.log")
            parsed = log_ingest_service.ingest(pre, run_meta, mongo)
            windows = log_window_service.build_windows(run_meta, mongo)
            if (not parsed.inserted_count or not parsed.parsed_ok or not windows.window_count
                    or parsed.inserted_count != parsed.total_lines - parsed.skipped):
                raise ValueError("日志解析或窗口构建失败，未发布数据库文件")
            provenance = {"evidence_mode": ground["evidence_mode"], "injection_performed": ground["injection_performed"],
                          "source_session_id": ground["source_session_id"], "euler_job_id": row.job_id,
                          "data_category": "semi", "fault_type": code if is_fault else None,
                          "requested_fault_type": requested if is_fault else None,
                          "executed_fault_type": code if is_fault else None,
                          "fallback_reason": fallback_reason if is_fault else None,
                          "simulation_reason": ground.get("simulation_reason"),
                          "missing_capabilities": ground.get("missing_capabilities", []),
                          "primary_fault_signal": ground.get("primary_fault_signal") if is_fault else None}
            db.execute(text("UPDATE mongo_docs SET doc = doc || CAST(:meta AS jsonb) "
                            "WHERE run_id = :run_id AND collection IN ('log_entries', 'log_windows')"),
                       {"meta": json.dumps(provenance), "run_id": run_id})
            stats = {"parsed_lines": parsed.parsed_ok, "total_lines": parsed.total_lines,
                     "level_distribution": parsed.level_distribution, "data_category": "semi",
                     "dataset_import_meta": meta, "euler_provenance": ground, "euler_job_id": row.job_id}
            levels = parsed.level_distribution
            db.add(Run(run_id=run_id, task_id=row.job_id, case_id=case_id, system_id="euler",
                       source_type="dataset", subsystem="EULER_RT", is_fault=is_fault,
                       test_name=directory.name, round_no=round_no,
                       run_name=f"{name} · {'故障轮' if is_fault else '正常基线轮'}",
                       line_count=parsed.total_lines, stats_json=stats,
                       error_logs=sum(n for level, n in levels.items() if level in {"ERROR", "CRITICAL", "FATAL", "ALERT"}),
                       critical_logs=sum(n for level, n in levels.items() if level in {"CRITICAL", "FATAL"}),
                       window_count=windows.window_count, start_time=parsed.start_time, end_time=parsed.end_time))
            run_ids.append(run_id)
            entry_count += parsed.inserted_count
            window_count += windows.window_count
    digest = _digest(archive)
    db.add(DatasetImport(import_id=row.import_id, original_filename=meta["original_filename"],
                         stored_filename=archive.name, storage_path=str(archive), file_ext=".tar.gz",
                         mime_type="application/gzip", size_bytes=archive.stat().st_size, sha256=digest,
                         display_name=meta["display_name"], description=meta["description"], tags_json=meta["tags"],
                         created_by=meta["created_by"], status="uploaded", format="semi",
                         ingest_status="success", ingested_at=datetime.utcnow(), ingested_case_ids=case_ids,
                         ingested_run_count=len(run_ids), ingested_entry_count=entry_count,
                         ingested_new_case_count=len(case_ids), ingested_new_run_count=len(run_ids)))
    return {"sample_count": len(samples), "run_count": len(run_ids), "entry_count": entry_count,
            "window_count": window_count, "run_ids": run_ids, "sha256": digest,
            "fault_type": code, "requested_fault_type": requested,
            "executed_fault_type": code, "fallback_reason": fallback_reason,
            "evidence_modes": sorted({ground["evidence_mode"] for _, ground in samples})}


def run_import(import_id: str) -> None:
    # Session advisory lock survives commits and also prevents concurrent retries/replicas.
    with engine.connect() as connection:
        key = _lock_key(import_id)
        locked = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        connection.commit()
        if not locked:
            return
        try:
            with Session(bind=connection) as db:
                row = db.get(EulerArtifactImport, import_id)
                if row is None or row.state != "pending":
                    return
                row.state = "saving"
                db.commit()
                try:
                    client = EulerFaultAgentClient(row.agent_url)
                    record = client.get_job(row.job_id)
                    validate_job(record, row.job_id)
                    root = Path(settings.DATA_IMPORT_ROOT) / "euler"
                    root.mkdir(parents=True, exist_ok=True)
                    max_bytes = settings.DATA_IMPORT_MAX_FILE_SIZE
                    with tempfile.TemporaryDirectory(prefix=f"{import_id}-", dir=root) as temporary:
                        work = Path(temporary)
                        download = work / "artifact.tar.gz"
                        client.save_artifact(row.job_id, download, max_bytes)
                        samples = validate_archive(download, work / "extracted", record, max_bytes)
                        archive = root / f"{import_id}.tar.gz"
                        os.replace(download, archive)
                        # Ensure PG JSONB schema before entering the atomic dataset transaction.
                        get_mongo_db()
                        row.result_json = ingest_samples(db, row, samples, archive)
                        row.state = "success"
                        row.error_message = None
                        db.commit()
                except Exception as exc:
                    db.rollback()
                    row = db.get(EulerArtifactImport, import_id)
                    row.state = "failed"
                    row.error_message = str(exc)[:2000]
                    db.commit()
                    logger.exception("Euler artifact import failed: %s", import_id)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def recover_interrupted_imports() -> None:
    with SessionLocal() as db:
        rows = db.query(EulerArtifactImport).filter(EulerArtifactImport.state.in_(["pending", "saving"])).all()
        for row in rows:
            if db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                          {"key": _lock_key(row.import_id)}).scalar():
                db.refresh(row)
                if row.state in {"pending", "saving"}:
                    row.state = "failed"
                    row.error_message = "后端重启中断保存，请点击重试保存；已有文件保留"
        db.commit()
