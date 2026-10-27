"""
annotation_push_service.py

职责（集成方案 A / Approach 1：两库分离 + HTTP 桥接）：
    把标注子系统的「某一已标注窗口」组装成主系统要求的推送载荷，经 MainSystemClient
    调用主系统 POST /log-analysis/annotated-run，落成一条 run + 一批 log_entries，
    使该窗口能出现在主系统「文件选择」并被诊断 / 预测 / RAG 分析。

数据来源：
    - 窗口行：SliceWindowLine (slice_window_id) → SourceLogLine → SourceLogFile.logical_path。
      与 recommendation_service._load_window_logs_by_file 同一 join，但这里还要 line_no/timestamp。
    - 标注：Annotation.slice_window_id == window_id。整窗标注（source_log_file_id=NULL）或
      按文件标注（source_log_file_id 非空）都可；任一 abnormal 即 is_fault=True，
      anomaly_type 取非空的一个。

设计约定（与主系统 import_annotated_run 对齐）：
    - run_id = "annot_pkg{package_id}_win{window_id}"（幂等：主系统侧先删后写，重复推送=覆盖）。
    - test_name 优先取 slice_task.name，回退 DatasetPackage.name。
    - 时间戳：UTC 秒级 epoch float 直接透传，主系统侧换算成 UTC naive datetime。
    - 推送失败（主系统不可达 / 校验拒绝）统一抛 WindowPushError，由端点转成业务错误。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError, WindowNotFoundError
from app.db.models import (
    Annotation,
    DatasetPackage,
    SliceTask,
    SliceWindow,
    SliceWindowLine,
    SourceLogFile,
    SourceLogLine,
)
from app.services.main_system_client import MainSystemClient

logger = logging.getLogger(__name__)


class WindowPushError(AppError):
    """推送到主系统失败（主系统不可达 / 校验拒绝 / 其它异常）。"""

    def __init__(self, message: str = "推送标注窗口到主系统失败") -> None:
        super().__init__(
            error_code="WINDOW_PUSH_FAILED",
            message=message,
            status_code=502,
        )


class WindowNoTimestampError(AppError):
    """窗口内的日志行全部没有有效时间戳，主系统无法为其构建窗口 / 诊断。"""

    def __init__(self, message: str = "窗口内没有带有效时间戳的日志行") -> None:
        super().__init__(
            error_code="WINDOW_NO_TIMESTAMP",
            message=message,
            status_code=422,
        )


class AnnotationPushService:
    def __init__(self, db: Session, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings
        self._client: MainSystemClient | None = None

    def push_window_to_main(self, *, window_id: int, allow_no_timestamp: bool = False) -> dict:
        """把一个已标注窗口组装并推送到主系统，返回主系统的导入结果 dict。

        allow_no_timestamp：True 时放宽「必须有有效时间戳」的预检。主系统的
        import_annotated_run 对空时间戳行也能落库（start/end_time=None、window_count=0），
        因此无时间戳窗口（非结构化 .pdf/.md、纯报告文本）也能进入主系统「文件选择」并被
        人工/LLM 诊断——只是不做时间窗口型分析。手动推送默认 False（保留可读的 422 提示，
        让用户知道该窗口无法走时间窗分析）；自动推送传 True，确保标注成功即进入文件选择。
        """
        window = self._require_window(window_id)
        payload = self._build_payload(window)
        if not payload["lines"]:
            raise WindowPushError(f"窗口 {window_id} 内没有可推送的日志行")
        # 预检：主系统的 log_window_service 只对带有效时间戳的条目建窗口。
        # 全文无时间戳的窗口（如纯报告文档）推上去会得到「数据缺失」的空诊断，
        # 这里提前拦截，给出可读错误而不是让主系统判定为空。
        if (
            not allow_no_timestamp
            and not any(
                line.get("timestamp") is not None
                for file_block in payload["lines"]
                for line in file_block["lines"]
            )
        ):
            raise WindowNoTimestampError(
                f"窗口 {window_id} 内没有带有效时间戳的日志行，主系统无法对其分析"
            )
        result = self._get_client().push_annotated_run(payload)
        return result

    def auto_push_window_to_main(self, *, window_id: int) -> dict | None:
        """标注保存/更新后自动推送（集成方案 A 反向桥）。

        - 受设置 annotation_auto_push_to_main 开关控制；
        - 用短超时（annotation_auto_push_timeout_seconds），主系统暂不可达不阻断标注保存；
        - 失败只记日志并返回 None，标注本身仍算成功。
        这样「标注成功 → 立即出现在主系统文件选择列表」是一条尽力而为的自动链路，
        且任何一方故障都不会破坏标注子系统的核心功能。
        """
        if not self.settings:
            self.settings = get_settings()
        if not self.settings.annotation_auto_push_to_main:
            logger.info("自动推送已关闭（annotation_auto_push_to_main=False），跳过窗口 %s", window_id)
            return None
        # 整个「组装 + 推送」都包在 try 里：后台任务绝不能因任何异常冒泡而打断标注保存，
        # 窗口并发被删、主系统校验拒绝、网络超时等一律记日志并返回 None（可稍后手动补推）。
        try:
            window = self._require_window(window_id)
            payload = self._build_payload(window)
        except Exception as exc:  # noqa: BLE001
            logger.warning("组装窗口 %s 推送载荷失败（标注已保存，可稍后手动补推）: %s", window_id, exc)
            return None
        if not payload["lines"]:
            logger.warning("窗口 %s 内没有可推送的日志行，跳过自动推送", window_id)
            return None
        try:
            return self._get_client(timeout=self._auto_push_timeout()).push_annotated_run(
                payload, timeout=self._auto_push_timeout()
            )
        except Exception as exc:  # noqa: BLE001 —— 自动推送不阻断标注保存
            logger.warning(
                "自动推送窗口 %s 到主系统失败（标注已保存，可稍后手动补推）: %s",
                window_id,
                exc,
            )
            return None

    # ----- payload 组装 -----

    def _build_payload(self, window: SliceWindow) -> dict[str, Any]:
        task = self.db.get(SliceTask, window.slice_task_id)
        package = self.db.get(DatasetPackage, task.package_id) if task else None

        files = self._collect_file_lines(window.id)
        is_fault, fault_type, note = self._collect_annotations(window.id)

        test_name = task.name if task else (package.name if package else f"window_{window.id}")
        run_name = None

        return {
            "run_id": f"annot_pkg{package.id if package else 0}_win{window.id}",
            "source_type": "annotation",
            "system_id": None,
            "subsystem": None,
            "case_id": None,
            "test_name": test_name,
            "run_name": run_name,
            "is_fault": is_fault,
            "fault_type": fault_type,
            "description": note,
            "lines": files,
        }

    def _collect_file_lines(self, window_id: int) -> list[dict[str, Any]]:
        """按源文件分组取出窗口内的行（logical_path + line_no + content + timestamp）。"""
        rows = self.db.execute(
            select(
                SourceLogFile.logical_path,
                SourceLogLine.line_no,
                SourceLogLine.content,
                SourceLogLine.timestamp,
            )
            .join(SourceLogLine, SourceLogLine.source_file_id == SourceLogFile.id)
            .join(SliceWindowLine, SliceWindowLine.source_line_id == SourceLogLine.id)
            .where(SliceWindowLine.slice_window_id == window_id)
            .order_by(SourceLogFile.logical_path.asc(), SourceLogLine.line_no.asc(), SourceLogLine.id.asc())
        ).all()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for logical_path, line_no, content, timestamp in rows:
            lines = grouped.setdefault(str(logical_path), [])
            lines.append(
                {
                    "line_no": int(line_no),
                    "content": str(content),
                    "timestamp": float(timestamp) if timestamp is not None else None,
                }
            )
        return [
            {"logical_path": path, "lines": lines} for path, lines in grouped.items()
        ]

    def _collect_annotations(self, window_id: int) -> tuple[bool, str | None, str | None]:
        """读取窗口上的标注，返回 (is_fault, fault_type, note)。"""
        annotations = self.db.scalars(
            select(Annotation).where(Annotation.slice_window_id == window_id)
        ).all()
        is_fault = False
        fault_type: str | None = None
        notes: list[str] = []
        for ann in annotations:
            if ann.note:
                notes.append(ann.note)
            if ann.label == "abnormal":
                is_fault = True
                if ann.anomaly_type and not fault_type:
                    fault_type = ann.anomaly_type
        note = "\n".join(notes) if notes else None
        return is_fault, fault_type, note

    # ----- helpers -----

    def _get_client(self, *, timeout: float | None = None) -> MainSystemClient:
        if self._client is None:
            self.settings = self.settings or get_settings()
            self._client = MainSystemClient(self.settings)
        return self._client

    def _auto_push_timeout(self) -> float | None:
        if not self.settings:
            self.settings = get_settings()
        return self.settings.annotation_auto_push_timeout_seconds

    def _require_window(self, window_id: int) -> SliceWindow:
        window = self.db.get(SliceWindow, window_id)
        if window is None:
            raise WindowNotFoundError()
        return window
