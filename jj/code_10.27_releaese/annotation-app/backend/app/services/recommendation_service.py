from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import RecommendationNotFoundError, WindowNotFoundError
from app.db.models import (
    AnnotationRecommendation,
    FaultType,
    SliceWindow,
    SliceWindowLine,
    SourceLogFile,
    SourceLogLine,
    WindowAnalysis,
)
from app.services.llm import LLMError, get_llm_client
from app.services.slice_engine.windowing import MAX_WINDOW_SECONDS, MIN_WINDOW_SECONDS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是嵌入式系统故障诊断专家。给定一个时间窗口内的日志，判断该窗口是正常还是异常；"
    "若异常，给出最可能的故障类型与简要理由。"
    "anomaly_type 优先从用户提供的【已定义故障类型】清单中选择完全一致的名称；"
    "只有当清单中确无匹配时，才自拟一个简短的中文故障类型名。"
    "reason 需简要且有据（引用关键日志现象）。只返回一个 JSON 对象。"
)


class RecommendationService:
    def __init__(self, db: Session, llm_client=None) -> None:
        self.db = db
        self.settings = get_settings()
        self._llm = llm_client if llm_client is not None else get_llm_client()

    # ----- read -----

    def get_for_window(self, *, window_id: int) -> AnnotationRecommendation:
        self._require_window(window_id)
        row = self._get_row(window_id)
        if row is None:
            raise RecommendationNotFoundError()
        return row

    # ----- single window -----

    def recommend_for_window(self, *, window_id: int, force_refresh: bool = False) -> AnnotationRecommendation:
        self._require_window(window_id)
        existing = self._get_row(window_id)
        if existing is not None and existing.status == "success" and not force_refresh:
            return existing
        return self._generate(window_id=window_id)

    def _generate(self, *, window_id: int) -> AnnotationRecommendation:
        if self._llm is None or not self._llm.is_configured():
            return self._save(
                window_id=window_id,
                status="failed",
                error_message="LLM 未配置，无法生成推荐",
            )

        window = self.db.get(SliceWindow, window_id)
        fault_types = self._load_fault_types()
        allowed_names = {name for name, _ in fault_types}
        if not allowed_names:
            return self._save(
                window_id=window_id,
                status="failed",
                error_message="请先在故障类型管理中定义故障类型后再生成推荐",
            )

        logs = self._load_window_logs_text(window_id)
        messages = self._build_messages(window=window, logs=logs, fault_types=fault_types)

        try:
            payload = self._chat_json_with_repair(messages, expected_schema="recommendation")
            label, raw_anomaly_type, reason = self._parse_pass1_payload(payload)
        except (LLMError, ValueError, json.JSONDecodeError) as exc:
            return self._save(window_id=window_id, status="failed", error_message=str(exc)[:2000])
        except Exception as exc:  # noqa: BLE001
            return self._save(window_id=window_id, status="failed", error_message=str(exc)[:2000])

        if label == "normal":
            return self._save(
                window_id=window_id,
                status="success",
                recommended_label="normal",
                recommended_anomaly_type=None,
                reason=reason,
                model=getattr(self._llm, "model", None),
            )

        matched = self._match_allowed(raw_anomaly_type, allowed_names)
        if matched is not None:
            return self._save(
                window_id=window_id,
                status="success",
                recommended_label="abnormal",
                recommended_anomaly_type=matched,
                reason=reason,
                model=getattr(self._llm, "model", None),
            )

        # Pass 2: the model picked an anomaly_type outside the allowed dictionary.
        # Ask once whether this looks like a genuinely new fault type. The
        # allow_new prompt only fires here, never on the primary path.
        suggestion_note = self._maybe_record_suggestion(
            window=window,
            logs=logs,
            existing_fault_types=fault_types,
            pass1_anomaly_type=raw_anomaly_type,
            pass1_reason=reason,
        )
        if suggestion_note is None:
            return self._save(
                window_id=window_id,
                status="failed",
                error_message="推荐的故障类型不在已定义列表中",
            )

        composed_reason = self._compose_reason_with_suggestion(reason, suggestion_note)
        return self._save(
            window_id=window_id,
            status="success",
            recommended_label="abnormal",
            recommended_anomaly_type=None,
            reason=composed_reason,
            model=getattr(self._llm, "model", None),
        )

    # ----- batch -----

    def start_batch(self, *, task_id: int, bind: Engine | None = None) -> dict:
        """Create pending rows for un-recommended windows, then process them
        (inline for in-memory SQLite / tests, in a daemon thread otherwise)."""
        window_ids = self._prepare_pending(task_id=task_id)

        engine = bind or self.db.get_bind()
        is_memory_sqlite = engine.dialect.name == "sqlite" and engine.url.database in (None, "", ":memory:")

        if is_memory_sqlite or not window_ids:
            self._process_windows(window_ids)
            return self.batch_progress(task_id=task_id)

        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

        def _runner() -> None:
            session = session_factory()
            try:
                RecommendationService(session)._process_windows(window_ids)
            except Exception:  # noqa: BLE001
                logger.exception("batch recommendation runner failed for task %s", task_id)
            finally:
                session.close()

        thread = threading.Thread(target=_runner, name=f"recommend-batch-{task_id}", daemon=True)
        thread.start()
        return self.batch_progress(task_id=task_id)

    def batch_recommend_for_task(self, *, task_id: int) -> dict:
        """Synchronous batch (used by tests and the thread runner)."""
        window_ids = self._prepare_pending(task_id=task_id)
        self._process_windows(window_ids)
        return self.batch_progress(task_id=task_id)

    def batch_progress(self, *, task_id: int) -> dict:
        total = int(
            self.db.scalar(
                select(func.count(SliceWindow.id)).where(SliceWindow.slice_task_id == task_id)
            )
            or 0
        )
        status_counts: dict[str, int] = {"pending": 0, "success": 0, "failed": 0}
        rows = self.db.execute(
            select(AnnotationRecommendation.status, func.count(AnnotationRecommendation.id))
            .join(SliceWindow, SliceWindow.id == AnnotationRecommendation.slice_window_id)
            .where(SliceWindow.slice_task_id == task_id)
            .group_by(AnnotationRecommendation.status)
        ).all()
        for status, count in rows:
            status_counts[str(status)] = int(count)

        recommended_total = sum(status_counts.values())
        return {
            "task_id": task_id,
            "total_windows": total,
            "success_count": status_counts["success"],
            "failed_count": status_counts["failed"],
            "pending_count": status_counts["pending"],
            "not_started_count": max(total - recommended_total, 0),
        }

    def _prepare_pending(self, *, task_id: int) -> list[int]:
        existing_success = set(
            self.db.scalars(
                select(AnnotationRecommendation.slice_window_id)
                .join(SliceWindow, SliceWindow.id == AnnotationRecommendation.slice_window_id)
                .where(SliceWindow.slice_task_id == task_id)
                .where(AnnotationRecommendation.status == "success")
            ).all()
        )
        window_ids = list(
            self.db.scalars(
                select(SliceWindow.id).where(SliceWindow.slice_task_id == task_id).order_by(SliceWindow.id)
            ).all()
        )
        targets = [wid for wid in window_ids if wid not in existing_success]
        for wid in targets:
            row = self._get_row(wid)
            if row is None:
                self.db.add(AnnotationRecommendation(slice_window_id=wid, status="pending"))
            else:
                row.status = "pending"
                row.error_message = None
                self.db.add(row)
        self.db.commit()
        return targets

    def _process_windows(self, window_ids: list[int]) -> None:
        for wid in window_ids:
            self._generate(window_id=wid)

    # ----- multi-fault analysis (on-demand) -----

    def analyze_and_save_window_multi(self, *, window_id: int) -> dict:
        """Run the multi-fault analysis and persist a successful result so the
        window can be re-opened later without re-invoking the LLM."""
        result = self.analyze_window_multi(window_id=window_id)
        if result.get("status") == "success":
            self._save_analysis(result)
        return result

    def get_stored_analysis(self, *, window_id: int) -> dict | None:
        """Return the last persisted analysis for a window, or None if absent."""
        self._require_window(window_id)
        row = self.db.execute(
            select(WindowAnalysis).where(WindowAnalysis.slice_window_id == window_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            files = json.loads(row.files_json) if row.files_json else []
        except (TypeError, ValueError):
            files = []
        return {
            "window_id": row.slice_window_id,
            "status": row.status,
            "multiple_faults": row.multiple_faults,
            "suggest_subdivide": row.suggest_subdivide,
            "suggested_window_seconds": row.suggested_window_seconds,
            "files": files,
            "model": row.model,
            "error_message": row.error_message,
        }

    def _save_analysis(self, result: dict) -> WindowAnalysis:
        window_id = int(result["window_id"])
        row = self.db.execute(
            select(WindowAnalysis).where(WindowAnalysis.slice_window_id == window_id)
        ).scalar_one_or_none()
        if row is None:
            row = WindowAnalysis(slice_window_id=window_id)
            self.db.add(row)
        row.status = result.get("status", "success")
        row.multiple_faults = bool(result.get("multiple_faults"))
        row.suggest_subdivide = bool(result.get("suggest_subdivide"))
        row.suggested_window_seconds = result.get("suggested_window_seconds")
        row.files_json = json.dumps(result.get("files", []), ensure_ascii=False)
        row.model = result.get("model")
        row.error_message = result.get("error_message")
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return row

    def analyze_window_multi(self, *, window_id: int) -> dict:
        """Analyze a window for *multiple distinct* faults across its files.

        Returns a suggestion structure (never writes annotations / recommendations):
        whether the window holds multiple faults, whether it should be subdivided,
        and a per-file label/anomaly_type/reason list. anomaly_type is snapped to
        the allowed fault-type dictionary; unmatched names degrade to None.
        """
        self._require_window(window_id)
        window = self.db.get(SliceWindow, window_id)

        if self._llm is None or not self._llm.is_configured():
            return self._empty_analysis(window_id, error="LLM 未配置，无法分析")

        fault_types = self._load_fault_types()
        allowed_names = {name for name, _ in fault_types}
        if not allowed_names:
            return self._empty_analysis(window_id, error="请先在故障类型管理中定义故障类型")

        per_file_logs = self._load_window_logs_by_file(window_id)
        if not per_file_logs:
            return self._empty_analysis(window_id, error="窗口内无日志可分析")

        messages = self._build_multi_messages(
            window=window, per_file_logs=per_file_logs, fault_types=fault_types
        )
        try:
            payload = self._chat_json_with_repair(messages, expected_schema="multi_analysis")
        except (LLMError, ValueError, json.JSONDecodeError) as exc:
            return self._empty_analysis(window_id, error=str(exc)[:2000])
        except Exception as exc:  # noqa: BLE001
            return self._empty_analysis(window_id, error=str(exc)[:2000])

        path_by_id = {fid: path for fid, path, _ in per_file_logs}
        files: list[dict] = []
        for entry in payload.get("per_file", []) or []:
            try:
                file_id = int(entry.get("source_log_file_id"))
            except (TypeError, ValueError):
                continue
            if file_id not in path_by_id:
                continue
            label = str(entry.get("label", "")).strip().lower()
            if label not in {"normal", "abnormal"}:
                continue
            reason = entry.get("reason")
            reason_text = str(reason).strip() if reason is not None else None
            anomaly_type: str | None = None
            if label == "abnormal":
                matched = self._match_allowed(
                    str(entry.get("anomaly_type") or "").strip(), allowed_names
                )
                anomaly_type = matched
                if matched is None:
                    note = f"[未匹配到已定义故障类型: {entry.get('anomaly_type')}]"
                    reason_text = f"{reason_text}\n{note}" if reason_text else note
            files.append(
                {
                    "source_log_file_id": file_id,
                    "logical_path": path_by_id[file_id],
                    "label": label,
                    "anomaly_type": anomaly_type,
                    "reason": reason_text,
                }
            )

        suggest_subdivide = bool(payload.get("suggest_subdivide"))
        suggested_window_seconds = self._validate_suggested_seconds(
            payload.get("suggested_window_seconds"), window=window
        )
        return {
            "window_id": window_id,
            "status": "success",
            "multiple_faults": bool(payload.get("multiple_faults")),
            "suggest_subdivide": suggest_subdivide,
            "suggested_window_seconds": suggested_window_seconds if suggest_subdivide else None,
            "files": files,
            "model": getattr(self._llm, "model", None),
            "error_message": None,
        }

    def _validate_suggested_seconds(self, value: object, *, window: SliceWindow) -> int | None:
        """Clamp/reject the LLM's suggested sub-window length: must be a positive
        integer strictly smaller than the window's own span (so it actually
        subdivides), and within the global [1, 3600] bound."""
        try:
            seconds = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        span = int(float(window.window_end_ts) - float(window.window_start_ts))
        upper = min(MAX_WINDOW_SECONDS, max(MIN_WINDOW_SECONDS, span - 1)) if span > 1 else MIN_WINDOW_SECONDS
        if seconds < MIN_WINDOW_SECONDS or seconds >= span:
            return None
        return min(seconds, upper)

    @staticmethod
    def _empty_analysis(window_id: int, *, error: str) -> dict:
        return {
            "window_id": window_id,
            "status": "failed",
            "multiple_faults": False,
            "suggest_subdivide": False,
            "suggested_window_seconds": None,
            "files": [],
            "model": None,
            "error_message": error,
        }

    def _load_window_logs_by_file(self, window_id: int) -> list[tuple[int, str, str]]:
        """Return [(source_file_id, logical_path, joined_log_text), ...] for a window."""
        limit = self.settings.recommendation_max_log_lines
        rows = self.db.execute(
            select(
                SourceLogFile.id,
                SourceLogFile.logical_path,
                SourceLogLine.content,
            )
            .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .order_by(SourceLogFile.logical_path.asc(), SourceLogLine.line_no.asc(), SourceLogLine.id.asc())
            .limit(limit)
        ).all()
        grouped: dict[int, dict] = {}
        for file_id, logical_path, content in rows:
            entry = grouped.setdefault(int(file_id), {"path": str(logical_path), "lines": []})
            entry["lines"].append(str(content))
        return [(fid, entry["path"], "\n".join(entry["lines"])) for fid, entry in grouped.items()]

    def _build_multi_messages(
        self,
        *,
        window: SliceWindow,
        per_file_logs: list[tuple[int, str, str]],
        fault_types: list[tuple[str, str]],
    ) -> list[dict[str, str]]:
        catalog = "\n".join(f"- {name}：{description}" for name, description in fault_types)
        blocks = "\n\n".join(
            f"[file_id={fid}] {path}\n{text}" for fid, path, text in per_file_logs
        )
        user = (
            f"窗口时间范围(UTC epoch): {window.window_start_ts} ~ {window.window_end_ts}; "
            f"文件数: {len(per_file_logs)}.\n\n"
            f"该窗口时间跨度约 {int(float(window.window_end_ts) - float(window.window_start_ts))} 秒。\n\n"
            f"已定义故障类型(anomaly_type 只能从以下名称中精确选取):\n{catalog}\n\n"
            "下面按文件分组给出该窗口内的日志。请判断该窗口内是否同时存在多个不同的故障，"
            "以及是否建议对该窗口进一步细分切片；若建议细分，请给出建议的子窗口长度(秒，"
            "必须小于该窗口跨度，使不同故障能落入不同子窗口)；并为每个出现异常的文件分别给出标注建议。\n\n"
            "请返回 JSON 对象，字段如下:\n"
            '- "multiple_faults": true/false（窗口内是否存在多个不同故障）\n'
            '- "suggest_subdivide": true/false（是否建议细分该时间窗口）\n'
            '- "suggested_window_seconds": 整数或 null（建议的子窗口长度，单位秒；'
            "suggest_subdivide=false 时为 null）\n"
            '- "per_file": 数组，元素为 {"source_log_file_id": 整数, "label": "normal|abnormal", '
            '"anomaly_type": 故障类型名称(label=normal 时为 null), "reason": 中文简要理由}\n\n'
            f"各文件日志:\n{blocks}\n\nJSON:"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    # ----- helpers -----

    def _build_messages(
        self,
        *,
        window: SliceWindow,
        logs: str,
        fault_types: list[tuple[str, str]],
    ) -> list[dict[str, str]]:
        catalog = "\n".join(f"- {name}：{description}" for name, description in fault_types)
        meta = (
            f"窗口时间范围(UTC epoch): {window.window_start_ts} ~ {window.window_end_ts}; "
            f"日志行数: {window.line_count}; 文件数: {window.file_count}; "
            f"CPU 数: {window.cpu_count}; 模块数: {window.module_count}."
        )
        user = (
            f"窗口信息: {meta}\n\n"
            f"已定义故障类型(anomaly_type 只能从以下名称中精确选取，不得新增、改写或翻译):\n{catalog}\n\n"
            "请返回 JSON 对象，字段如下:\n"
            '- "label": "normal" 或 "abnormal"\n'
            '- "anomaly_type": 故障类型名称(label=normal 时为 null; label=abnormal 时必填，'
            "且必须与上面列表中的某个名称完全一致)\n"
            '- "reason": 推荐理由(中文，简要)\n\n'
            f"日志内容(最多 {self.settings.recommendation_max_log_lines} 行):\n{logs}\n\nJSON:"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    def _load_fault_types(self) -> list[tuple[str, str]]:
        rows = self.db.execute(
            select(FaultType.name, FaultType.description).order_by(FaultType.name.asc(), FaultType.id.asc())
        ).all()
        return [(str(name), str(description)) for name, description in rows]

    def _load_window_logs_text(self, window_id: int) -> str:
        limit = self.settings.recommendation_max_log_lines
        rows = self.db.execute(
            select(
                SourceLogFile.logical_path,
                SourceLogLine.line_no,
                SourceLogLine.content,
            )
            .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .order_by(SourceLogFile.logical_path.asc(), SourceLogLine.line_no.asc(), SourceLogLine.id.asc())
            .limit(limit)
        ).all()
        return "\n".join(f"[{path}] {content}" for path, _line_no, content in rows)

    def _parse_pass1_payload(self, payload: dict) -> tuple[str, str, str | None]:
        """Parse the constrained first-pass response. Returns (label, raw_anomaly_type, reason).
        Does not enforce anomaly_type membership — the caller decides whether to fall back."""
        label = str(payload.get("label", "")).strip().lower()
        if label not in {"normal", "abnormal"}:
            raise ValueError(f"invalid label in recommendation: {label!r}")
        reason = payload.get("reason")
        reason = str(reason).strip() if reason is not None else None
        anomaly_type_raw = payload.get("anomaly_type")
        anomaly_type = str(anomaly_type_raw).strip() if anomaly_type_raw is not None else ""
        return label, anomaly_type, reason

    def _normalize_payload(self, payload: dict, allowed: set[str]) -> tuple[str, str | None, str | None]:
        """Strict pass-1 parsing kept for tests / external callers. Pass 2 lives in _generate."""
        label, anomaly_type, reason = self._parse_pass1_payload(payload)
        if label == "normal":
            return label, None, reason
        matched = self._match_allowed(anomaly_type, allowed)
        if matched is None:
            raise ValueError("推荐的故障类型不在已定义列表中")
        return label, matched, reason

    def _maybe_record_suggestion(
        self,
        *,
        window: SliceWindow,
        logs: str,
        existing_fault_types: list[tuple[str, str]],
        pass1_anomaly_type: str,
        pass1_reason: str | None,
    ) -> str | None:
        """Pass 2: ask the LLM whether the unmatched anomaly_type is a genuinely new
        fault type. Persist a pending suggestion when so. Returns a short human-readable
        note describing the recorded suggestion (for inclusion in the recommendation
        reason), or None if no suggestion should be recorded."""
        try:
            messages = self._build_pass2_messages(
                window=window,
                logs=logs,
                existing_fault_types=existing_fault_types,
                pass1_anomaly_type=pass1_anomaly_type,
                pass1_reason=pass1_reason,
            )
            payload = self._chat_json_with_repair(messages, expected_schema="fault_type_suggestion")
        except (LLMError, ValueError, json.JSONDecodeError):
            return None
        except Exception:  # noqa: BLE001
            return None

        should_create = bool(payload.get("should_create_new"))
        if not should_create:
            return None

        name = str(payload.get("suggested_name") or pass1_anomaly_type or "").strip()
        description = str(payload.get("suggested_description") or "").strip()
        reason = payload.get("reason")
        reason_text = str(reason).strip() if reason is not None else None
        if not name or not description:
            return None
        # Don't suggest a name that already exists — that would mean the model
        # contradicted itself; treat as no suggestion.
        existing_names = {item[0] for item in existing_fault_types}
        if self._match_allowed(name, existing_names) is not None:
            return None

        from app.services.fault_type_suggestion_service import FaultTypeSuggestionService

        suggestion = FaultTypeSuggestionService(self.db).record(
            slice_window_id=window.id,
            suggested_name=name,
            suggested_description=description,
            reason=reason_text,
            model=getattr(self._llm, "model", None),
        )
        return f"已生成新故障类型建议: {suggestion.suggested_name} (suggestion #{suggestion.id})"

    def _build_pass2_messages(
        self,
        *,
        window: SliceWindow,
        logs: str,
        existing_fault_types: list[tuple[str, str]],
        pass1_anomaly_type: str,
        pass1_reason: str | None,
    ) -> list[dict[str, str]]:
        catalog = "\n".join(f"- {name}：{description}" for name, description in existing_fault_types)
        meta = (
            f"窗口时间范围(UTC epoch): {window.window_start_ts} ~ {window.window_end_ts}; "
            f"日志行数: {window.line_count}; 文件数: {window.file_count}; "
            f"CPU 数: {window.cpu_count}; 模块数: {window.module_count}."
        )
        user = (
            f"窗口信息: {meta}\n\n"
            f"已定义故障类型(参考):\n{catalog}\n\n"
            f"先前模型基于此窗口给出的故障类型: {pass1_anomaly_type or '(空)'}; 理由: {pass1_reason or '(无)'}\n\n"
            "你需要严格判断: 该故障在已定义类型中是否确实没有对应项? 若仅是同义改写或翻译, "
            "应判定为不需要新增 (should_create_new=false)。只有当现有定义无法覆盖、确实需要"
            "扩展字典时才返回 true。\n\n"
            "请返回 JSON 对象，字段如下:\n"
            '- "should_create_new": true 或 false\n'
            '- "suggested_name": 当 should_create_new=true 时给出建议名称(简体中文，简短，不与上面列表重复); 否则为 null\n'
            '- "suggested_description": 当 should_create_new=true 时给出建议定义(中文，1-3 句话); 否则为 null\n'
            '- "reason": 解释为什么需要(或不需要)新增。中文简要。\n\n'
            f"日志内容(最多 {self.settings.recommendation_max_log_lines} 行):\n{logs}\n\nJSON:"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    @staticmethod
    def _compose_reason_with_suggestion(reason: str | None, note: str) -> str:
        if reason:
            return f"{reason}\n\n[{note}]"
        return f"[{note}]"

    @staticmethod
    def _match_allowed(value: str, allowed: set[str]) -> str | None:
        if not value:
            return None
        if value in allowed:
            return value
        lowered = value.lower()
        for name in allowed:
            if name.lower() == lowered:
                return name
        return None

    def _extract_json(self, text: str) -> dict:
        cleaned = text.strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no JSON object found in LLM response")
        return json.loads(cleaned[start : end + 1])

    def _chat_json_with_repair(self, messages: list[dict[str, str]], *, expected_schema: str) -> dict:
        purpose = (
            "annotation_window_analysis"
            if expected_schema == "multi_analysis"
            else "annotation_recommendation"
        )
        raw = self._llm.chat(
            messages,
            purpose=purpose,
            json_mode=True,
            temperature=0.0,
        )
        try:
            return self._extract_json(raw)
        except (ValueError, json.JSONDecodeError):
            repair_messages = self._build_json_repair_messages(raw=raw, expected_schema=expected_schema)
            repaired = self._llm.chat(
                repair_messages,
                purpose=purpose,
                json_mode=True,
                temperature=0.0,
            )
            return self._extract_json(repaired)

    @staticmethod
    def _build_json_repair_messages(*, raw: str, expected_schema: str) -> list[dict[str, str]]:
        if expected_schema == "fault_type_suggestion":
            schema = (
                '{"should_create_new": true 或 false, '
                '"suggested_name": "建议名称或 null", '
                '"suggested_description": "建议定义或 null", '
                '"reason": "中文简要理由"}'
            )
        elif expected_schema == "multi_analysis":
            schema = (
                '{"multiple_faults": true 或 false, '
                '"suggest_subdivide": true 或 false, '
                '"suggested_window_seconds": 整数或 null, '
                '"per_file": [{"source_log_file_id": 整数, "label": "normal 或 abnormal", '
                '"anomaly_type": "故障类型名称或 null", "reason": "中文简要理由"}]}'
            )
        else:
            schema = (
                '{"label": "normal 或 abnormal", '
                '"anomaly_type": "故障类型名称或 null", '
                '"reason": "中文简要理由"}'
            )
        return [
            {
                "role": "system",
                "content": "你是 JSON 格式修复器。只返回一个合法 JSON 对象，不要输出解释、Markdown 或代码块。",
            },
            {
                "role": "user",
                "content": (
                    "下面的大模型回答不是合法 JSON。请保留原意，按指定 schema 转换，"
                    "只返回一个合法 JSON 对象。\n\n"
                    f"schema: {schema}\n\n原始回答:\n{raw}"
                ),
            },
        ]

    def _save(
        self,
        *,
        window_id: int,
        status: str,
        recommended_label: str | None = None,
        recommended_anomaly_type: str | None = None,
        reason: str | None = None,
        model: str | None = None,
        error_message: str | None = None,
    ) -> AnnotationRecommendation:
        row = self._get_row(window_id)
        if row is None:
            row = AnnotationRecommendation(slice_window_id=window_id)
        row.status = status
        row.recommended_label = recommended_label
        row.recommended_anomaly_type = recommended_anomaly_type
        row.reason = reason
        row.model = model
        row.error_message = error_message
        row.updated_at = datetime.now(timezone.utc)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _get_row(self, window_id: int) -> AnnotationRecommendation | None:
        return self.db.scalar(
            select(AnnotationRecommendation).where(AnnotationRecommendation.slice_window_id == window_id)
        )

    def _require_window(self, window_id: int) -> SliceWindow:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()
        return window
