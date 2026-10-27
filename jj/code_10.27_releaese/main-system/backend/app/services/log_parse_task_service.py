from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
import logging
import time
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.log_parse_task import LogParseTask
from app.config import settings
from app.database import SessionLocal
from app.services.log_parse_errors import LogParseCancelled, LogParseTimedOut


logger = logging.getLogger(__name__)


ACTIVE_STATES = {"queued", "running", "cancelling"}
TERMINAL_STATES = {"cancelled", "succeeded", "failed"}
_TASK_CREATION_LOCK_ID = 631_2026_0723


class LogParseTaskNotFound(LookupError):
    pass


class LogParseTaskConflict(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _lock_task_creation(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _TASK_CREATION_LOCK_ID},
        )


def _task(db: Session, task_id: str, *, lock: bool = False) -> LogParseTask:
    task = db.get(LogParseTask, task_id, with_for_update=lock)
    if task is None:
        raise LogParseTaskNotFound(f"log parse task {task_id} was not found")
    return task


def get_task(db: Session, task_id: str) -> LogParseTask:
    return _task(db, task_id)


def create_task(db: Session, run_ids: Iterable[str]) -> LogParseTask:
    normalized = list(run_ids)
    _lock_task_creation(db)
    active = (
        db.query(LogParseTask)
        .filter(LogParseTask.state.in_(ACTIVE_STATES))
        .with_for_update()
        .all()
    )
    requested = set(normalized)
    for task in active:
        overlap = requested.intersection(task.run_ids or [])
        if overlap:
            run_id = sorted(overlap)[0]
            raise LogParseTaskConflict(f"日志 {run_id} 已有进行中的解析任务")

    task = LogParseTask(
        run_ids=normalized,
        state="queued",
        stage="queued",
        progress=0,
        completed_count=0,
        success_count=0,
        fail_count=0,
        results={},
        fail_details=[],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def request_cancel(db: Session, task_id: str) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    now = _now()
    if task.stage == "persisting":
        raise LogParseTaskConflict("解析结果正在提交，无法取消")
    if task.state == "queued":
        task.state = "cancelled"
        task.stage = "cancelled"
        task.cancel_requested_at = now
        task.finished_at = now
    elif task.state == "running":
        task.state = "cancelling"
        task.cancel_requested_at = task.cancel_requested_at or now
    elif task.state == "cancelling":
        pass
    else:
        raise LogParseTaskConflict(f"task in state {task.state} cannot be cancelled")
    db.commit()
    db.refresh(task)
    return task


def mark_running(db: Session, task_id: str) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    if task.state != "queued":
        raise LogParseTaskConflict(f"task in state {task.state} cannot start")
    task.state = "running"
    task.stage = "loading"
    task.started_at = _now()
    db.commit()
    db.refresh(task)
    return task


def _raise_if_cancelled(task: LogParseTask) -> None:
    if task.state in {"cancelling", "cancelled"}:
        raise LogParseCancelled("日志解析已取消")


def update_progress(
    db: Session,
    task_id: str,
    *,
    stage: str,
    progress: int,
    current_run_id: str | None = None,
) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    _raise_if_cancelled(task)
    if task.state != "running":
        raise LogParseTaskConflict(f"task in state {task.state} cannot report progress")
    task.progress = max(task.progress, min(99, max(0, int(progress))))
    task.stage = stage
    task.current_run_id = current_run_id
    db.commit()
    db.refresh(task)
    return task


def begin_persisting(
    db: Session,
    task_id: str,
    *,
    progress: int = 95,
) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    _raise_if_cancelled(task)
    if task.state != "running":
        raise LogParseTaskConflict(f"task in state {task.state} cannot persist")
    task.stage = "persisting"
    task.progress = max(task.progress, min(99, max(0, int(progress))))
    db.commit()
    db.refresh(task)
    return task


def record_success(
    db: Session,
    task_id: str,
    run_id: str,
    result: dict[str, Any],
) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    _raise_if_cancelled(task)
    if task.state != "running":
        raise LogParseTaskConflict(f"task in state {task.state} cannot record success")
    results = dict(task.results or {})
    results[run_id] = result
    task.results = results
    task.success_count += 1
    task.completed_count += 1
    task.current_run_id = None
    task.stage = "running"
    db.commit()
    db.refresh(task)
    return task


def record_failure(
    db: Session,
    task_id: str,
    run_id: str,
    *,
    code: str,
    message: str,
) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    _raise_if_cancelled(task)
    if task.state != "running":
        raise LogParseTaskConflict(f"task in state {task.state} cannot record failure")
    failures = list(task.fail_details or [])
    failures.append({"run_id": run_id, "code": code, "message": message})
    task.fail_details = failures
    task.fail_count += 1
    task.completed_count += 1
    task.current_run_id = None
    task.stage = "running"
    db.commit()
    db.refresh(task)
    return task


def mark_cancelled(db: Session, task_id: str) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    if task.state not in {"running", "cancelling", "queued"}:
        raise LogParseTaskConflict(f"task in state {task.state} cannot be cancelled")
    task.state = "cancelled"
    task.stage = "cancelled"
    task.cancel_requested_at = task.cancel_requested_at or _now()
    task.finished_at = _now()
    task.current_run_id = None
    db.commit()
    db.refresh(task)
    return task


def mark_failed(
    db: Session,
    task_id: str,
    *,
    code: str,
    message: str,
) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    if task.state in TERMINAL_STATES:
        raise LogParseTaskConflict(f"task in state {task.state} cannot fail")
    task.state = "failed"
    task.stage = "failed"
    task.error_code = code
    task.error_message = message
    task.finished_at = _now()
    task.current_run_id = None
    db.commit()
    db.refresh(task)
    return task


def finish_from_counts(db: Session, task_id: str) -> LogParseTask:
    task = _task(db, task_id, lock=True)
    if task.state == "cancelling":
        task.state = "cancelled"
        task.stage = "cancelled"
        task.finished_at = _now()
    elif task.state != "running":
        raise LogParseTaskConflict(f"task in state {task.state} cannot finish")
    elif task.success_count > 0:
        task.state = "succeeded"
        task.stage = "completed"
        task.progress = 100
        task.finished_at = _now()
    else:
        task.state = "failed"
        task.stage = "failed"
        task.error_code = "all_items_failed"
        task.error_message = "所有日志均解析失败"
        task.finished_at = _now()
    task.current_run_id = None
    db.commit()
    db.refresh(task)
    return task


def recover_interrupted_tasks(db: Session) -> int:
    tasks = (
        db.query(LogParseTask)
        .filter(LogParseTask.state.in_(ACTIVE_STATES))
        .with_for_update()
        .all()
    )
    now = _now()
    for task in tasks:
        task.state = "failed"
        task.stage = "failed"
        task.error_code = "backend_restarted"
        task.error_message = "Backend 重启，解析任务已中断"
        task.finished_at = now
        task.current_run_id = None
    db.commit()
    return len(tasks)


def is_cancel_requested(db: Session, task_id: str) -> bool:
    task = _task(db, task_id)
    return task.state in {"cancelling", "cancelled"}


class LogParseControl:
    def __init__(
        self,
        task_id: str,
        item_index: int,
        item_count: int,
        session_factory: Callable = SessionLocal,
        timeout_seconds: int = settings.LOG_PARSE_TIMEOUT_SECONDS,
    ) -> None:
        self.task_id = task_id
        self.item_index = item_index
        self.item_count = max(1, item_count)
        self.session_factory = session_factory
        self.deadline = time.monotonic() + timeout_seconds
        self._last_llm_progress = -1
        self._last_cancel_check = 0.0
        self._cancelled = False

    def _overall_progress(self, item_progress: int) -> int:
        bounded = min(99, max(0, int(item_progress)))
        return min(
            99,
            round(
                (
                    self.item_index
                    + bounded / 100.0
                )
                / self.item_count
                * 100
            ),
        )

    def checkpoint(self, stage: str, progress: int) -> None:
        if time.monotonic() >= self.deadline:
            raise LogParseTimedOut(
                f"日志解析超过 {settings.LOG_PARSE_TIMEOUT_SECONDS} 秒"
            )
        with self.session_factory() as db:
            update_progress(
                db,
                self.task_id,
                stage=stage,
                progress=self._overall_progress(progress),
                current_run_id=get_task(db, self.task_id).run_ids[self.item_index],
            )

    def begin_persisting(self, progress: int = 95) -> None:
        if time.monotonic() >= self.deadline:
            raise LogParseTimedOut(
                f"日志解析超过 {settings.LOG_PARSE_TIMEOUT_SECONDS} 秒"
            )
        with self.session_factory() as db:
            begin_persisting(
                db,
                self.task_id,
                progress=self._overall_progress(progress),
            )

    def should_cancel(self) -> bool:
        now = time.monotonic()
        if now - self._last_cancel_check < 0.2:
            return self._cancelled
        self._last_cancel_check = now
        with self.session_factory() as db:
            self._cancelled = is_cancel_requested(db, self.task_id)
        return self._cancelled

    def on_llm_progress(self, received: int, budget: int) -> None:
        fraction = min(1.0, received / max(1, budget))
        item_progress = 25 + round(65 * fraction)
        if item_progress <= self._last_llm_progress:
            return
        self._last_llm_progress = item_progress
        self.checkpoint("llm", item_progress)


def _failure_payload(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, LogParseTimedOut):
        return "timeout", f"日志解析超过 {settings.LOG_PARSE_TIMEOUT_SECONDS} 秒"
    status_code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if status_code is not None and detail:
        return f"http_{status_code}", str(detail)[:500]
    if isinstance(exc, RuntimeError):
        return "model_error", str(exc)[:500] or "模型解析失败"
    return "internal_error", "日志解析失败"


class LogParseTaskRunner:
    def __init__(
        self,
        *,
        session_factory: Callable = SessionLocal,
        max_workers: int = settings.LOG_PARSE_WORKERS,
    ) -> None:
        self.session_factory = session_factory
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="log-parse",
        )

    def submit(self, task_id: str, execute_one: Callable) -> None:
        self._executor.submit(self._run, task_id, execute_one)

    def run_now(self, task_id: str, execute_one: Callable) -> None:
        self._run(task_id, execute_one)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _run(self, task_id: str, execute_one: Callable) -> None:
        try:
            with self.session_factory() as db:
                task = get_task(db, task_id)
                if task.state == "cancelled":
                    return
                task = mark_running(db, task_id)
                run_ids = list(task.run_ids)

            for index, run_id in enumerate(run_ids):
                control = LogParseControl(
                    task_id,
                    index,
                    len(run_ids),
                    self.session_factory,
                )
                try:
                    result = execute_one(run_id, control)
                    with self.session_factory() as db:
                        record_success(
                            db,
                            task_id,
                            run_id,
                            result.model_dump(),
                        )
                except LogParseCancelled:
                    with self.session_factory() as db:
                        mark_cancelled(db, task_id)
                    return
                except Exception as exc:
                    code, message = _failure_payload(exc)
                    logger.warning(
                        "日志解析任务单项失败 task_id=%s run_id=%s code=%s",
                        task_id,
                        run_id,
                        code,
                    )
                    try:
                        with self.session_factory() as db:
                            record_failure(
                                db,
                                task_id,
                                run_id,
                                code=code,
                                message=message,
                            )
                    except LogParseCancelled:
                        with self.session_factory() as db:
                            mark_cancelled(db, task_id)
                        return

                with self.session_factory() as db:
                    if is_cancel_requested(db, task_id):
                        mark_cancelled(db, task_id)
                        return

            with self.session_factory() as db:
                finish_from_counts(db, task_id)
        except (LogParseTaskNotFound, LogParseTaskConflict):
            logger.info("日志解析任务未执行 task_id=%s", task_id)
        except Exception:
            logger.exception("日志解析任务执行器失败 task_id=%s", task_id)
            try:
                with self.session_factory() as db:
                    task = get_task(db, task_id)
                    if task.state not in TERMINAL_STATES:
                        mark_failed(
                            db,
                            task_id,
                            code="runner_error",
                            message="日志解析任务执行失败",
                        )
            except Exception:
                logger.exception("无法记录日志解析任务失败 task_id=%s", task_id)
