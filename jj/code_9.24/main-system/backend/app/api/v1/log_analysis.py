import re
import uuid
import logging
import json
import time
from typing import Optional, Tuple, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.api.deps import get_db
from app.config import settings
from app.database import get_mongo_db
from app.models.fault_type import FaultType
from app.models.log_entry import LogEntry
from app.services.vector_store import VectorStoreService
from app.services.preprocessing_service import preprocessing_service
from app.services.log_ingest_service import log_ingest_service, RunMeta
from app.services.log_window_service import log_window_service
from app.services.log_analysis_service import fault_scorer
from app.services.annotated_run_service import import_annotated_run
from app.services.llm_service import LLMService
from app.services.log_parse_errors import LogParseCancelled, LogParseTimedOut
from app.services import log_dataset_service
from app.schemas.log_analysis import (
    AnalyzeTextRequest, ManualScoreAnalyzeRequest, AnalyzeLogRequest, AnalyzeLogOut, LogLevelStats,
    VectorizeRequest, VectorizeOut,
    BatchVectorizeRequest, BatchVectorizeOut,
    UnclassifiedLogListOut, UnclassifiedLogOut,
    ClassifyLogRequest, BatchClassifyOut,
    AnalyzeLogResult,
    DatasetRunListOut, DatasetRunDetailOut, DatasetRunEntriesOut, DatasetRunWindowsOut,
    UpdateRunRequest, UpdateRunOut,
    BatchDeleteRunsRequest, BatchDeleteRunsOut, DeleteRunOut,
    LogParseTaskCreate, LogParseTaskOut,
    AnnotatedRunImportRequest, AnnotatedRunImportOut,
)
from app.services.log_parse_task_service import (
    LogParseTaskConflict,
    LogParseTaskNotFound,
    LogParseTaskRunner,
    create_task as create_log_parse_task_record,
    get_task as get_log_parse_task_record,
    request_cancel as request_log_parse_cancel,
)
from app.utils.cache import cache_delete, GRAPH_CACHE_KEY

logger = logging.getLogger(__name__)

router = APIRouter()
log_parse_task_runner = LogParseTaskRunner()


def _ensure_upload_size(content_bytes: bytes) -> None:
    """日志文件上传大小上限（settings.MAX_FILE_SIZE，默认 100MB）。超限给友好中文提示。"""
    if len(content_bytes) > settings.MAX_FILE_SIZE:
        mb = settings.MAX_FILE_SIZE // 1024 // 1024
        raise HTTPException(status_code=413, detail=f"文件超过上限（{mb}MB），请拆分后再上传")


# ── LLM 单例（仅本路由内使用，复用 services/llm_service.LLMService） ──────────
# 在导入期不实例化，避免没装 openai 包或配置缺失时阻塞 import；首次调用时才构造。
_llm_for_analysis: Optional[LLMService] = None


def _get_llm_for_analysis() -> LLMService:
    """日志解析路由专用的 LLM 单例。未配置时由 LLMService._runtime 抛 RuntimeError。"""
    global _llm_for_analysis
    if _llm_for_analysis is None:
        _llm_for_analysis = LLMService()
    return _llm_for_analysis


# ── 私有：从原始文本提取旧字段（timestamps / error_codes / stack_traces） ────────
def _extract_legacy_fields(
    text: str,
) -> Tuple[List[str], List[str], List[str], str, str, str, str, str]:
    """从原始文本正则提取三类旧字段，供 _analyze_text 填充 AnalyzeLogOut 旧字段。"""
    lines = text.splitlines()

    # 提取时间戳（常见格式，最多 5 个）
    ts_pattern = re.compile(
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?'
    )
    timestamps: List[str] = []
    for line in lines:
        m = ts_pattern.search(line)
        if m:
            timestamps.append(m.group())
        if len(timestamps) >= 5:
            break

    # 提取错误码（如 E1234, ORA-12345, errno 28 等，最多 10 个去重）
    code_pattern = re.compile(r'\b(?:[A-Z]{1,5}-\d{3,6}|E\d{3,6}|errno\s+\d+)\b')
    error_codes = list(dict.fromkeys(code_pattern.findall(text)))[:10]

    # 提取堆栈痕迹（含 Traceback/Exception/Error: 的连续块，最多 3 条）
    stack_traces: List[str] = []
    in_trace = False
    current_trace: List[str] = []
    for line in lines:
        if re.search(r'Traceback|Exception:|Error:', line):
            in_trace = True
            current_trace = [line]
        elif in_trace:
            if line.strip().startswith(("at ", "File ", "\t")):
                current_trace.append(line)
            else:
                if current_trace:
                    stack_traces.append("\n".join(current_trace))
                in_trace = False
                current_trace = []
        if len(stack_traces) >= 3:
            break
    if current_trace:
        stack_traces.append("\n".join(current_trace))
    
    # 提取设备ID（如 device_id, host, hostname 等）
    device_id = ""
    device_patterns = [
        r'device[_-]?id[:=]\s*([\w-]+)',
        r'host[:=]\s*([\w-]+)',
        r'hostname[:=]\s*([\w-]+)',
        r'server[:=]\s*([\w-]+)'
    ]
    for pattern in device_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            device_id = match.group(1)
            break
    
    # 提取模块名称（如 module, component, service 等）
    module_name = ""
    module_patterns = [
        r'module[:=]\s*([\w-]+)',
        r'component[:=]\s*([\w-]+)',
        r'service[:=]\s*([\w-]+)',
        r'class\s+([\w]+)',
        r'function\s+([\w]+)'
    ]
    for pattern in module_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            module_name = match.group(1)
            break
    
    # 提取异常描述特征（从错误信息中提取）
    exception_description = ""
    if stack_traces:
        # 从堆栈痕迹的第一行提取异常描述
        first_trace = stack_traces[0]
        match = re.search(r'(Exception|Error):\s*(.*)', first_trace)
        if match:
            exception_description = match.group(2)
    
    # 提取日志级别
    log_level = ""
    level_patterns = [
        r'\b(ERROR|WARN|WARNING|INFO|DEBUG|TRACE)\b',
        r'\[ERROR\]|\[WARN\]|\[WARNING\]|\[INFO\]|\[DEBUG\]|\[TRACE\]'
    ]
    for pattern in level_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            log_level = match.group(1).upper()
            break
    
    # 判断日志类型
    log_type = "正常"
    if "ERROR" in log_level or "WARN" in log_level or "WARNING" in log_level:
        if stack_traces:
            log_type = "故障"
        elif error_codes:
            log_type = "报错"
        else:
            log_type = "告警"

    return timestamps, error_codes, stack_traces[:3], device_id, module_name, exception_description, log_level, log_type


# ── 私有：完整分析链路 ────────────────────────────────────────────────────────


class _DirectLogParseControl:
    def __init__(self, timeout_seconds: int = settings.LOG_PARSE_TIMEOUT_SECONDS):
        self.deadline = time.monotonic() + timeout_seconds

    def checkpoint(self, stage: str, progress: int) -> None:
        if time.monotonic() >= self.deadline:
            raise LogParseTimedOut(
                f"日志解析超过 {settings.LOG_PARSE_TIMEOUT_SECONDS} 秒"
            )

    def begin_persisting(self, progress: int = 95) -> None:
        self.checkpoint("persisting", progress)

    def should_cancel(self) -> bool:
        return False

    def on_llm_progress(self, received: int, budget: int) -> None:
        self.checkpoint("llm", 25)

def _analyze_text(
    text: str,
    filename: str,
    *,
    source_type: Optional[str] = None,
    api_url: Optional[str] = None,
    run_id: Optional[str] = None,
    control=None,
) -> AnalyzeLogOut:
    """
    完整分析链：preprocess → ingest → build_windows → score → LLM 增强 → AnalyzeLogOut。
    被 /analyze 和 /analyze/file 等 6 处共同调用，避免重复逻辑。

    Args:
        text:        原始日志文本（必填）
        filename:    展示用文件名（必填）
        source_type: 来源标识（"text" / "file" / "api" / 等），仅用于结构化数据元信息，
                     当前规则流水线不区分来源，传递只是为了让 8 处调用点不再 TypeError。
        api_url:     如果是从外部接口拉来的日志，记录拉取地址，写进 structured_data。
    """
    control = control or _DirectLogParseControl()
    # 复用调用方传入的 run_id（分析"已存在的 run"时），保证 log_entries/log_windows/DB5 全程同一 run_id；
    # 未传则生成新 uuid（纯文本/文件一次性分析）。
    reuse_run_id = run_id is not None
    run_id = run_id or str(uuid.uuid4())

    try:
        # ── Step 1：预处理 ────────────────────────────────────────────────────────
        control.checkpoint("preprocessing", 10)
        pp = preprocessing_service.preprocess(
            text,
            source_type="analyze",
            filename=filename,
        )

        # ── Step 2：写入 MongoDB log_entries ──────────────────────────────────────
        mongo_db = get_mongo_db()
        # 复用既有 run_id 时，先清掉该 run 的旧条目/窗口，避免重复插入（ingest 不自带去重）
        if reuse_run_id:
            mongo_db["log_entries"].delete_many({"run_id": run_id})
            mongo_db["log_windows"].delete_many({"run_id": run_id})
        run_meta = RunMeta(run_id=run_id, source_type="analyze")
        ingest_result = log_ingest_service.ingest(pp, run_meta, mongo_db)
        control.checkpoint("preprocessing", 15)

        # ── Step 3：构建时间滑动窗口 ──────────────────────────────────────────────
        log_window_service.build_windows(
            run_meta=run_meta,
            mongo_db=mongo_db,
            window_size_s=60,
            stride_s=30,
            min_entries=1,
        )
        control.checkpoint("preprocessing", 20)

        # ── Step 4：查询当前 run_id 的异常窗口（只取必要字段） ───────────────────
        error_windows = list(
            mongo_db["log_windows"].find(
                {"run_id": run_id, "stats.error_events": {"$gt": 0}},
                {"stats": 1, "key_events": 1, "window_id": 1, "_id": 0},
            )
        )

        # ── Step 5：提取旧字段（正则，不依赖 ingest 结果） ────────────────────────
        timestamps, error_codes, stack_traces, device_id, module_name, exception_description, log_level, log_type = _extract_legacy_fields(pp.text)

        # ── Step 6：故障评分 ──────────────────────────────────────────────────────
        score_result = fault_scorer.score(
            ingest_result=ingest_result,
            raw_text=pp.text,
            stack_traces=stack_traces,
            error_windows=error_windows,
        )
        control.checkpoint("scoring", 25)

        # ── Step 7：组装旧字段 LogLevelStats（从 ingest_result.level_distribution） ─
        ld = ingest_result.level_distribution
        log_level_stats = LogLevelStats(
            error_count=ld.get("ERROR", 0),
            warning_count=ld.get("WARNING", 0) + ld.get("WARN", 0),
            info_count=ld.get("INFO", 0),
            debug_count=ld.get("DEBUG", 0),
            total_lines=ingest_result.total_lines,
        )

        # ── Step 8：组装完整响应 ──────────────────────────────────────────────────
        # 构建结构化数据
        structured_data = {
            "log_timestamp": timestamps[0] if timestamps else "",
            "log_level": log_level,
            "device_id": device_id,
            "module_name": module_name,
            "error_codes": error_codes,
            "exception_description": exception_description,
            "log_type": log_type
        }

        # 来源元信息（如果调用方传入），便于前端区分 text/file/api
        if source_type:
            structured_data["source_type"] = source_type
        if api_url:
            structured_data["api_url"] = api_url

        # ── Step 8.5：LLM 语义增强（可选，未配置 / 失败时静默降级）────────────────
        # 在规则提取（timestamps / error_codes / stack_traces）之上叠加：
        #   summary / severity / parsed_events / fault_signals /
        #   suggested_fault_type / confidence / root_cause_analysis / recovery_hint
        # 结果落到 structured_data["llm_insight"]，前端按存在与否决定是否展示。
        try:
            control.checkpoint("llm", 25)
            llm_insight = _get_llm_for_analysis().parse_log(
                pp.text,
                should_cancel=control.should_cancel,
                on_progress=control.on_llm_progress,
                deadline=control.deadline,
            )
            structured_data["llm_insight"] = llm_insight
            # 同步把 LLM 推断的故障类型补进顶层，便于前端列表用现成字段展示
            if llm_insight.get("suggested_fault_type") and not structured_data.get("suggested_fault_type"):
                structured_data["suggested_fault_type"] = llm_insight["suggested_fault_type"]
        except (LogParseCancelled, LogParseTimedOut):
            raise
        except RuntimeError as exc:
            # ENABLE_LLM=False / 配置不全 → 已是降级路径，记一次即可
            logger.info("[analyze] LLM 未启用，跳过语义增强: %s", exc)
            structured_data["llm_insight"] = None
        except Exception as exc:
            # 网络异常 / 模型超时 / 解析失败 → 不阻断主流程
            logger.warning("[analyze] LLM 语义增强失败，已降级为纯规则: %s", exc)
            structured_data["llm_insight"] = None

        control.checkpoint("scoring", 90)

        # 检查是否有有效特征
        if not any([timestamps, error_codes, stack_traces, log_level]):
            structured_data["log_type"] = "无效日志"
            score_result.summary = "当前日志无有效解析特征，标记为无效日志"

        # ── Step 8.6：LLM 可用时以语义判定为主（降级时保留规则结果）──────────────
        # 用户要求「日志解析使用大模型」：当 LLM 成功返回 llm_insight 时，
        # 用其 severity / confidence 覆盖 has_fault / fault_score / confidence / summary；
        # 否则（未配置/失败/截断）沿用上面改进后的规则结果，保证优雅降级。
        final_has_fault = score_result.has_fault
        final_fault_score = score_result.fault_score
        final_confidence = score_result.confidence
        final_summary = score_result.summary
        final_next_action = score_result.next_action

        _llm_insight = structured_data.get("llm_insight")
        if _llm_insight and not _llm_insight.get("_truncated"):
            _sev = str(_llm_insight.get("severity") or "").lower()
            _sev_map = {
                "critical": (9.5, 0.95, True),
                "high":     (8.0, 0.85, True),
                "medium":   (5.5, 0.60, True),   # 中等严重度即判「异常」→ 进故障诊断
                "low":      (2.0, 0.20, False),
            }
            if _sev in _sev_map:
                _fs, _base_conf, _sev_fault = _sev_map[_sev]
                _llm_conf = _llm_insight.get("confidence")
                # 与 LLM 自报置信度融合，避免输出恒定值
                if isinstance(_llm_conf, (int, float)) and _llm_conf > 0:
                    _conf = round((_base_conf + float(_llm_conf)) / 2.0, 3)
                else:
                    _conf = _base_conf
                final_fault_score = _fs
                final_confidence = _conf
                final_has_fault = _sev_fault
                final_next_action = "fault_location" if _sev_fault else "warning_forecast"
                if _llm_insight.get("summary"):
                    final_summary = _llm_insight["summary"]

        # 安全钳制：无论规则还是 LLM 路径，故障评分上限为 10
        final_fault_score = min(final_fault_score, 10.0)

        # 解析置信度下限 50%：日志解析的置信度不应低于 0.5（产品要求），
        # 规则路径 / 低 severity 路径可能给出 <0.5，这里统一取下限并封顶 1.0。
        try:
            final_confidence = min(max(float(final_confidence), 0.5), 1.0)
        except (TypeError, ValueError):
            final_confidence = 0.5

        return AnalyzeLogOut(
            # 旧字段
            filename=filename,
            log_level_stats=log_level_stats,
            extracted_timestamps=timestamps,
            extracted_error_codes=error_codes,
            extracted_stack_traces=stack_traces,
            raw_content=pp.text[:5000],
            line_count=ingest_result.total_lines,
            # 新字段（LLM 可用时为大模型判定，否则为改进规则判定）
            has_fault=final_has_fault,
            fault_score=final_fault_score,
            confidence=final_confidence,
            summary=final_summary,
            evidence=score_result.evidence,
            next_action=final_next_action,
            run_id=run_id,
            score_breakdown=score_result.score_breakdown,
            # 管道元信息
            detected_format=pp.detected_format,
            encoding=pp.encoding,
            # 结构化数据
            structured_data=structured_data
        )
    except Exception as e:
        # 重新抛出异常，让调用者处理
        raise


# ── POST /log-analysis/analyze（文本输入，纯分析，不写 MySQL） ─────────────────
@router.post("/log-analysis/analyze", response_model=AnalyzeLogOut)
def analyze_text(body: AnalyzeTextRequest):
    return _analyze_text(body.log_text, body.filename or "手动输入")


# ── POST /log-analysis/analyze/file（文件上传，纯分析，不写 MySQL） ───────────
@router.post("/log-analysis/analyze/file", response_model=AnalyzeLogOut)
async def analyze_file(file: UploadFile = File(...)):
    # 检查文件格式
    valid_extensions = [".log", ".txt", ".csv", ".json"]
    file_ext = "." + file.filename.split(".")[-1].lower() if file.filename else ""
    if file_ext not in valid_extensions:
        raise HTTPException(status_code=400, detail="文件格式无效或已损坏，请上传合法.log、.txt、.csv 格式文件")
    
    content_bytes = await file.read()
    _ensure_upload_size(content_bytes)
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = content_bytes.decode("gbk", errors="ignore")
    return _analyze_text(text, file.filename or "上传文件")


# ── POST /log-analysis/vectorize（分析后向量化入库，不触发诊断） ──────────────
@router.post("/log-analysis/vectorize", response_model=VectorizeOut)
def vectorize_log(body: VectorizeRequest, db: Session = Depends(get_db)):
    """保存日志到 log_entries 并向量化，fault_type_id 可为 None（未分类）"""
    ft_name: Optional[str] = None
    if body.fault_type_id is not None:
        ft = db.query(FaultType).filter(FaultType.id == body.fault_type_id).first()
        if not ft:
            raise HTTPException(status_code=404, detail="故障类型不存在")
        ft_name = ft.name

    log_entry = LogEntry(
        fault_type_id=body.fault_type_id,
        filename=body.filename or "手动输入",
        raw_content=body.log_text,
        summary=body.summary,
        is_indexed=False,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    chroma_doc_id: Optional[str] = None
    try:
        vs = VectorStoreService()
        doc_id = f"log_entry_{log_entry.id}"
        vs.add_document(
            doc_id=doc_id,
            text=body.log_text,
            metadata={
                "log_entry_id": log_entry.id,
                "fault_type_name": ft_name or "未分类",
                "fault_type_id": body.fault_type_id or 0,
            },
        )
        log_entry.chroma_doc_id = doc_id
        log_entry.is_indexed = True
        chroma_doc_id = doc_id
        db.commit()
        db.refresh(log_entry)
    except Exception:
        pass

    cache_delete(GRAPH_CACHE_KEY)

    return VectorizeOut(
        log_entry_id=log_entry.id,
        chroma_doc_id=chroma_doc_id,
        is_indexed=log_entry.is_indexed,
        fault_type_name=ft_name,
    )


# ── POST /log-analysis/vectorize/batch（批量向量化） ─────────────────────────
@router.post("/log-analysis/vectorize/batch", response_model=BatchVectorizeOut)
def batch_vectorize(body: BatchVectorizeRequest, db: Session = Depends(get_db)):
    results = []
    success_count = 0
    fail_count = 0

    for item in body.items:
        ft_name: Optional[str] = None
        if item.fault_type_id is not None:
            ft = db.query(FaultType).filter(FaultType.id == item.fault_type_id).first()
            if ft:
                ft_name = ft.name

        log_entry = LogEntry(
            fault_type_id=item.fault_type_id,
            filename=item.filename or "手动输入",
            raw_content=item.log_text,
            summary=item.summary,
            is_indexed=False,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        chroma_doc_id: Optional[str] = None
        indexed = False
        try:
            vs = VectorStoreService()
            doc_id = f"log_entry_{log_entry.id}"
            vs.add_document(
                doc_id=doc_id,
                text=item.log_text,
                metadata={
                    "log_entry_id": log_entry.id,
                    "fault_type_name": ft_name or "未分类",
                    "fault_type_id": item.fault_type_id or 0,
                },
            )
            log_entry.chroma_doc_id = doc_id
            log_entry.is_indexed = True
            chroma_doc_id = doc_id
            indexed = True
            db.commit()
            db.refresh(log_entry)
            success_count += 1
        except Exception:
            fail_count += 1

        results.append(VectorizeOut(
            log_entry_id=log_entry.id,
            chroma_doc_id=chroma_doc_id,
            is_indexed=indexed,
            fault_type_name=ft_name,
        ))

    cache_delete(GRAPH_CACHE_KEY)
    return BatchVectorizeOut(
        success_count=success_count,
        fail_count=fail_count,
        results=results,
    )


# ── GET /log-analysis/unclassified（未分类日志列表） ──────────────────────────
@router.get("/log-analysis/unclassified", response_model=UnclassifiedLogListOut)
def list_unclassified(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(LogEntry).filter(LogEntry.fault_type_id == None)
    total = q.count()
    items = q.order_by(LogEntry.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return UnclassifiedLogListOut(
        total=total,
        items=[UnclassifiedLogOut.model_validate(i) for i in items],
    )


# ── POST /log-analysis/classify（批量分类未分类日志） ─────────────────────────
@router.post("/log-analysis/classify", response_model=BatchClassifyOut)
def batch_classify(body: ClassifyLogRequest, db: Session = Depends(get_db)):
    ft = db.query(FaultType).filter(FaultType.id == body.fault_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")

    logs = db.query(LogEntry).filter(LogEntry.id.in_(body.log_ids)).all()
    updated_count = 0
    for log in logs:
        log.fault_type_id = body.fault_type_id
        updated_count += 1
        if log.chroma_doc_id:
            try:
                vs = VectorStoreService()
                vs.collection.update(
                    ids=[log.chroma_doc_id],
                    metadatas=[{
                        "log_entry_id": log.id,
                        "fault_type_name": ft.name,
                        "fault_type_id": ft.id,
                    }],
                )
            except Exception:
                pass

    db.commit()
    cache_delete(GRAPH_CACHE_KEY)
    return BatchClassifyOut(updated_count=updated_count)


# ── 新增：日志上传相关 API（仅存入 MongoDB）────────────────────────────────────

@router.post("/log-analysis/upload")
def upload_log(data: Dict[str, Any] = Body(...)):
    """
    上传文本日志到 MongoDB。
    """
    log_text = data.get("log_text")
    filename = data.get("filename", "manual_input.log")
    
    if not log_text:
        raise HTTPException(status_code=400, detail="日志内容不能为空")
    
    try:
        import logging
        from datetime import datetime
        logger = logging.getLogger(__name__)
        from app.database import get_mongo_db
        from app.services.preprocessing_service import preprocessing_service
        from app.services.log_ingest_service import log_ingest_service, RunMeta
        
        run_id = str(uuid.uuid4())
        
        # 预处理日志数据
        pp = preprocessing_service.preprocess(
            log_text,
            source_type="text",
            filename=filename,
        )
        
        # 创建 RunMeta 对象
        run_meta = RunMeta(
            run_id=run_id,
            source_type="text",
            test_name=filename
        )
        
        # 存入 MongoDB
        mongo_db = get_mongo_db()
        ingest_result = log_ingest_service.ingest(pp, run_meta, mongo_db)
        
        # 在 MongoDB 中创建一个新的集合来存储日志的元信息和原始内容
        log_upload_record = {
            "run_id": run_id,
            "source_type": "text",
            "filename": filename,
            "raw_content": log_text,  # 存储原始日志内容
            "line_count": len(log_text.split('\n')),
            "char_count": len(log_text),
            "ingested_at": datetime.now(),
            "analyzed": False,  # 新增：解析状态
            "analyzed_at": None,  # 新增：解析时间
            "ingest_result": {
                "total_lines": ingest_result.total_lines,
                "parsed_ok": ingest_result.parsed_ok,
                "parse_failed": ingest_result.parse_failed,
                "skipped": ingest_result.skipped
            }
        }
        mongo_db["log_uploads"].insert_one(log_upload_record)
        
        return {"run_id": run_id, "filename": filename, "status": "success", "line_count": len(log_text.split('\n'))}
    except Exception as exc:
        logger.error(f"上传文本日志失败: {exc}")
        raise HTTPException(status_code=500, detail=f"日志存储失败: {str(exc)}")

@router.post("/log-analysis/upload/file")
async def upload_log_file(files: List[UploadFile] = File(...)):
    """
    上传日志文件到 MongoDB（支持多个文件）。
    """
    # 检查文件格式
    valid_extensions = [".log", ".txt", ".csv", ".json"]
    results = []
    
    try:
        import logging
        from datetime import datetime
        logger = logging.getLogger(__name__)
        from app.database import get_mongo_db
        from app.services.preprocessing_service import preprocessing_service
        from app.services.log_ingest_service import log_ingest_service, RunMeta
        
        mongo_db = get_mongo_db()
        
        for file in files:
            file_ext = "." + file.filename.split(".")[-1].lower() if file.filename else ""
            if file_ext not in valid_extensions:
                continue  # 跳过无效文件
            
            content_bytes = await file.read()
            _ensure_upload_size(content_bytes)
            try:
                text = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = content_bytes.decode("gbk", errors="ignore")
            
            run_id = str(uuid.uuid4())
            
            # 预处理日志数据
            pp = preprocessing_service.preprocess(
                content_bytes,
                source_type="file",
                filename=file.filename,
            )
            
            # 创建 RunMeta 对象
            run_meta = RunMeta(
                run_id=run_id,
                source_type="file",
                test_name=file.filename
            )
            
            # 存入 MongoDB
            ingest_result = log_ingest_service.ingest(pp, run_meta, mongo_db)
            
            # 在 MongoDB 中创建一个新的集合来存储日志的元信息和原始内容
            log_upload_record = {
                "run_id": run_id,
                "source_type": "file",
                "filename": file.filename,
                "raw_content": text,  # 存储原始日志内容
                "line_count": len(text.split('\n')),
                "char_count": len(content_bytes),
                "ingested_at": datetime.now(),
                "analyzed": False,  # 新增：解析状态
                "analyzed_at": None,  # 新增：解析时间
                "ingest_result": {
                    "total_lines": ingest_result.total_lines,
                    "parsed_ok": ingest_result.parsed_ok,
                    "parse_failed": ingest_result.parse_failed,
                    "skipped": ingest_result.skipped
                }
            }
            mongo_db["log_uploads"].insert_one(log_upload_record)
            
            results.append({
                "run_id": run_id,
                "filename": file.filename,
                "file_size": len(content_bytes),
                "status": "success"
            })
        
        return {"results": results, "status": "success"}
    except Exception as exc:
        logger.error(f"上传日志文件失败: {exc}")
        raise HTTPException(status_code=500, detail=f"日志存储失败: {str(exc)}")

# ── 新增：日志列表相关 API ─────────────────────────────────────────────────────

@router.get("/log-analysis/uploaded")
def list_uploaded_logs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), source_type: Optional[str] = Query(None), log_format: Optional[str] = Query(None), start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None)):
    """
    获取已上传的日志列表，从 MongoDB 的 log_uploads 集合中获取。
    """
    from app.database import get_mongo_db
    from datetime import datetime
    
    try:
        mongo_db = get_mongo_db()
        
        # 构建查询条件
        query = {}
        if source_type:
            query["source_type"] = source_type
        if log_format:
            query["detected_format"] = log_format
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                query["ingested_at"] = {"$gte": start_dt}
            except:
                pass
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                if "ingested_at" in query:
                    query["ingested_at"]["$lte"] = end_dt
                else:
                    query["ingested_at"] = {"$lte": end_dt}
            except:
                pass
        
        # 获取总数
        total = mongo_db["log_uploads"].count_documents(query)
        
        # 分页查询
        skips = (page - 1) * page_size
        cursor = mongo_db["log_uploads"].find(query).sort("ingested_at", -1).skip(skips).limit(page_size)

        docs = list(cursor)
        # 标记哪些日志已有持久化解析结果 + 其判定(has_fault)，前端据此显示「查看结果」与「去处」
        run_ids = [doc.get("run_id") for doc in docs if doc.get("run_id")]
        analyzed_ids = set()
        fault_map = {}
        if run_ids:
            for r in mongo_db["log_analysis_results"].find(
                {"run_id": {"$in": run_ids}}, {"run_id": 1, "has_fault": 1, "_id": 0}
            ):
                rid = r.get("run_id")
                analyzed_ids.add(rid)
                fault_map[rid] = bool(r.get("has_fault"))

        items = []
        for doc in docs:
            rid = doc.get("run_id")
            items.append({
                "id": rid,
                "filename": doc.get("filename"),
                "source_type": doc.get("source_type"),
                "log_format": doc.get("detected_format"),
                "line_count": doc.get("line_count", 0),
                "char_count": doc.get("char_count", 0),
                "analyzed": doc.get("analyzed", False),
                "analyzed_at": doc.get("analyzed_at"),
                "has_analysis": rid in analyzed_ids,
                "has_fault": fault_map.get(rid),  # 已分析: True/False；未分析: None → 前端显示「去处」
                "created_at": doc.get("ingested_at")
            })

        return {"items": items, "total": total}
    except Exception as exc:
        logger.error(f"获取上传日志列表失败: {exc}")
        raise HTTPException(status_code=500, detail=f"获取日志列表失败: {str(exc)}")


@router.get("/log-analysis/analysis/results")
def list_analysis_results(
    verdict: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    exclude_diagnosed: bool = Query(False),
    db: Session = Depends(get_db),
):
    """DB5：已分析文件列表（读 log_analysis_results 集合），按判定过滤。

    verdict=abnormal → has_fault=True（异常，送「故障诊断」）；
    verdict=normal   → has_fault=False（正常，送「预测预警」）；
    verdict 为空     → 全部。
    exclude_diagnosed=True → 剔除已诊断过的 run_id（用于「待诊断异常文件」列表，
    诊断后不再出现）。
    """
    from app.database import get_mongo_db

    try:
        mongo_db = get_mongo_db()

        query = {}
        if verdict == "abnormal":
            query["has_fault"] = True
        elif verdict == "normal":
            query["has_fault"] = False

        proj = {"run_id": 1, "filename": 1, "has_fault": 1, "fault_score": 1, "analyzed_at": 1, "_id": 0}
        coll = mongo_db["log_analysis_results"]

        if not exclude_diagnosed:
            # 常规路径：mongo 直接分页
            total = coll.count_documents(query)
            skips = (page - 1) * page_size
            docs = list(coll.find(query, proj).sort("analyzed_at", -1).skip(skips).limit(page_size))
        else:
            # 「待诊断」路径：先取已诊断的 run_id 集合，全量拉取后剔除再分页
            from app.models.diagnosis_record import DiagnosisRecord
            diagnosed_ids = {
                rid for (rid,) in db.query(DiagnosisRecord.run_id)
                .filter(DiagnosisRecord.run_id.isnot(None)).all()
            }
            all_docs = list(coll.find(query, proj).sort("analyzed_at", -1).limit(5000))
            filtered = [d for d in all_docs if d.get("run_id") not in diagnosed_ids]
            total = len(filtered)
            skips = (page - 1) * page_size
            docs = filtered[skips: skips + page_size]

        items = []
        for doc in docs:
            items.append({
                "run_id": doc.get("run_id"),
                "filename": doc.get("filename"),
                "has_fault": doc.get("has_fault", False),
                "fault_score": doc.get("fault_score"),
                "analyzed_at": doc.get("analyzed_at"),
            })

        # 统一「上传时间」：按 run_id 反查 log_uploads（三页显示一致）
        # 并据此判定来源：命中 log_uploads = 用户上传的文件；否则 = 从数据库(DB1/数据列表)选择的 run
        from app.services.log_label_service import resolve_upload_labels
        labels = resolve_upload_labels([it["run_id"] for it in items])
        for it in items:
            lb = labels.get(it["run_id"]) or {}
            it["uploaded_at"] = lb.get("uploaded_at")
            if not it.get("filename"):
                it["filename"] = lb.get("filename")
            it["source"] = "upload" if it["run_id"] in labels else "dataset"

        return {"items": items, "total": total}
    except Exception as exc:
        logger.error(f"获取分析结果列表失败: {exc}")
        raise HTTPException(status_code=500, detail=f"获取分析结果列表失败: {str(exc)}")


@router.get("/log-analysis/logs/{run_id}/export")
def export_run_logs(run_id: str):
    """导出某 run 的原始日志行（供标注子系统跨系统「从主系统导入」DB1 数据）。

    只读；返回 {run_id, filename, truncated, total, lines:[{line_no, ts(iso|null), level, file_path, content}]}。
    content 取 raw_line（原文，优于 message），ts 取 log_entries 的 timestamp。
    """
    from app.database import get_mongo_db

    MAX_LINES = 50000
    try:
        mongo_db = get_mongo_db()
        docs = list(
            mongo_db["log_entries"]
            .find(
                {"run_id": run_id},
                {"line_no": 1, "timestamp": 1, "level": 1, "file_path": 1, "raw_line": 1, "message": 1, "_id": 0},
            )
            .sort([("file_path", 1), ("line_no", 1)])
            .limit(MAX_LINES + 1)
        )
        truncated = len(docs) > MAX_LINES
        if truncated:
            docs = docs[:MAX_LINES]
        if not docs:
            raise HTTPException(status_code=404, detail="日志不存在或尚未入库")

        filename = run_id
        fp0 = docs[0].get("file_path")
        if fp0:
            filename = str(fp0).split("/")[-1] or run_id

        lines = []
        for d in docs:
            ts = d.get("timestamp")
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None)
            content = d.get("raw_line") or d.get("message") or ""
            lines.append({
                "line_no": d.get("line_no"),
                "ts": ts_str,
                "level": d.get("level"),
                "file_path": d.get("file_path"),
                "content": str(content),
            })
        return {"run_id": run_id, "filename": filename, "truncated": truncated, "total": len(lines), "lines": lines}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"导出 run 日志失败 run_id={run_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"导出日志失败: {str(exc)}")


@router.post("/log-analysis/annotated-run", response_model=AnnotatedRunImportOut)
def import_annotated_run_endpoint(
    body: AnnotatedRunImportRequest,
    db: Session = Depends(get_db),
):
    """接收标注子系统推来的一已标注窗口，落成主系统一条 run + log_entries。

    Approach 1（两库分离 + HTTP 桥接）：把标注产物喂给主系统的 LLM 诊断/预测/RAG，
    使窗口能像 nuttx_Test_2000_round_2 一样从「文件选择」进入分析。幂等：同 run_id
    重复推送 = 覆盖。此端点由 annotation-app 的 MainSystemClient.push_annotated_run 调用。
    """
    try:
        result = import_annotated_run(db, get_mongo_db(), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"导入标注 run 失败 run_id={body.run_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"导入标注 run 失败: {str(exc)}")
    return AnnotatedRunImportOut(**result)


@router.get("/log-analysis/logs", response_model=DatasetRunListOut)
def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    run_id: Optional[str] = Query(None),
    case_id: Optional[str] = Query(None),
    test_name: Optional[str] = Query(None),
    fault_status: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    system_id: Optional[str] = Query(None),
    data_category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return log_dataset_service.list_runs(
        db,
        page=page,
        page_size=page_size,
        run_id=run_id,
        case_id=case_id,
        test_name=test_name,
        fault_status=fault_status or status,
        system_id=system_id,
        data_category=data_category,
    )

@router.get("/log-analysis/logs/{run_id}", response_model=DatasetRunDetailOut)
def get_log_details(run_id: str, db: Session = Depends(get_db)):
    try:
        return log_dataset_service.get_run_detail(
            db,
            get_mongo_db(),
            run_id=run_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="run 不存在或尚未入库") from exc


@router.get("/log-analysis/logs/{run_id}/entries", response_model=DatasetRunEntriesOut)
def get_log_entries(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    return log_dataset_service.list_run_entries(
        get_mongo_db(),
        run_id=run_id,
        page=page,
        page_size=page_size,
    )


@router.get("/log-analysis/logs/{run_id}/windows", response_model=DatasetRunWindowsOut)
def get_log_windows(
    run_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    return log_dataset_service.list_run_windows(
        get_mongo_db(),
        run_id=run_id,
        page=page,
        page_size=page_size,
    )


# ── 「日志列表」CRUD：编辑 + 删除 ─────────────────────────────────────────────

@router.patch("/log-analysis/logs/{run_id}", response_model=UpdateRunOut)
def patch_log_run(
    run_id: str,
    body: UpdateRunRequest,
    db: Session = Depends(get_db),
):
    """
    编辑一条 run 的元信息：故障类型 / 是否故障 / 描述 / run_name。
    case 维度的字段会同时写到 cases 表（一条 case 下所有 run 共享）。
    """
    try:
        result = log_dataset_service.update_run(
            db,
            run_id=run_id,
            payload=body.model_dump(exclude_unset=True),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="run 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cache_delete(GRAPH_CACHE_KEY)
    return UpdateRunOut(
        run_id=result["run_id"],
        case_id=result.get("case_id"),
        updated_fields=result["updated_fields"],
        message=("无字段变更" if not result["updated_fields"] else None),
    )


@router.delete("/log-analysis/logs/{run_id}", response_model=DeleteRunOut)
def delete_log_run(
    run_id: str,
    cleanup_empty_case: bool = Query(True),
    db: Session = Depends(get_db),
):
    """
    级联删除一条 run：文档存储 + Chroma 向量 + runs 行；
    cleanup_empty_case=True 时该 case 名下没有 run 了一并清理。
    """
    try:
        result = log_dataset_service.delete_run(
            db,
            get_mongo_db(),
            run_id=run_id,
            cleanup_empty_case=cleanup_empty_case,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="run 不存在")

    cache_delete(GRAPH_CACHE_KEY)
    return DeleteRunOut(**result)


@router.delete("/log-analysis/analysis/{run_id}")
def delete_analysis_run(run_id: str, db: Session = Depends(get_db)):
    """统一级联删除「分析记录」整条 run 线（联通 日志分析 / 故障诊断 / 预测预警）。

    - 始终清「分析/诊断/预测产物」：DB5(log_analysis_results) + DiagnosisRecord + PredictionRecord。
    - 来源=上传文件（命中 log_uploads）：额外清 log_uploads/log_entries/log_windows，
      若 runs 有该 run 再走 delete_run 清 Chroma/case。
    - 来源=DB1（数据库选择）：**保留** runs/log_entries/log_windows/Chroma（数据列表原始数据不动），
      只从分析/诊断/预测里移除。
    """
    from app.models.diagnosis_record import DiagnosisRecord
    from app.models.prediction_record import PredictionRecord

    mongo_db = get_mongo_db()
    is_upload = mongo_db["log_uploads"].find_one({"run_id": run_id}, {"_id": 0, "run_id": 1}) is not None
    out = {"run_id": run_id, "source": "upload" if is_upload else "dataset"}

    # 1) 产物：DB5 + 诊断 + 预测（两类来源都清）
    out["deleted_analysis"] = getattr(mongo_db["log_analysis_results"].delete_many({"run_id": run_id}), "deleted_count", 0)
    out["deleted_diagnosis"] = db.query(DiagnosisRecord).filter(DiagnosisRecord.run_id == run_id).delete(synchronize_session=False)
    out["deleted_prediction"] = db.query(PredictionRecord).filter(PredictionRecord.run_id == run_id).delete(synchronize_session=False)
    db.commit()

    # 2) 仅上传文件才连原始一起清；DB1 原始数据保护
    if is_upload:
        mongo_db["log_uploads"].delete_many({"run_id": run_id})
        mongo_db["log_entries"].delete_many({"run_id": run_id})
        mongo_db["log_windows"].delete_many({"run_id": run_id})
        try:
            from app.models.run import Run
            if db.query(Run).filter(Run.run_id == run_id).first():
                log_dataset_service.delete_run(db, mongo_db, run_id=run_id, cleanup_empty_case=True)
        except Exception:
            pass

    cache_delete(GRAPH_CACHE_KEY)
    return out


@router.post("/log-analysis/logs/batch-delete", response_model=BatchDeleteRunsOut)
def batch_delete_log_runs(
    body: BatchDeleteRunsRequest,
    db: Session = Depends(get_db),
):
    """批量删除多条 run（仅 run_id 列表，逐条事务，互不影响）。"""
    if not body.run_ids:
        raise HTTPException(status_code=400, detail="run_ids 不能为空")

    result = log_dataset_service.batch_delete_runs(
        db,
        get_mongo_db(),
        run_ids=body.run_ids,
        cleanup_empty_cases=body.cleanup_empty_cases,
    )
    cache_delete(GRAPH_CACHE_KEY)
    return BatchDeleteRunsOut(
        requested=result["requested"],
        deleted=result["deleted"],
        not_found=result["not_found"],
        items=[DeleteRunOut(**i) for i in result["items"]],
    )


# ── 新增：日志解析相关 API ─────────────────────────────────────────────────────


def _parse_stored_log(log_id: str, control=None) -> AnalyzeLogOut:
    control = control or _DirectLogParseControl()
    control.checkpoint("loading", 5)
    document_db = get_mongo_db()
    docs = list(
        document_db["log_entries"].find(
            {"run_id": log_id},
            {"message": 1, "raw_line": 1, "file_path": 1, "_id": 0},
        ).sort([("file_path", 1), ("line_no", 1)]).limit(5000)
    )
    if not docs:
        raise HTTPException(status_code=404, detail="日志不存在或尚未入库")

    lines = [
        str(line)
        for doc in docs
        if (line := doc.get("raw_line") or doc.get("message"))
    ]
    if not lines:
        raise HTTPException(status_code=400, detail="日志内容为空")

    first_path = docs[0].get("file_path")
    filename = (
        str(first_path).split("/")[-1] or log_id
        if first_path
        else log_id
    )
    result = _analyze_text(
        "\n".join(lines),
        filename,
        run_id=log_id,
        control=control,
    )
    result.run_id = log_id
    control.begin_persisting(95)

    from datetime import datetime as _dt

    now = _dt.utcnow()
    document_db["log_analysis_results"].update_one(
        {"run_id": log_id},
        {"$set": {
            "run_id": log_id,
            "filename": filename,
            "has_fault": result.has_fault,
            "fault_score": result.fault_score,
            "result": result.model_dump(),
            "analyzed_at": now,
        }},
        upsert=True,
    )
    document_db["log_uploads"].update_one(
        {"run_id": log_id},
        {"$set": {"analyzed": True, "analyzed_at": now}},
    )
    return result


@router.post(
    "/log-analysis/parse-tasks",
    response_model=LogParseTaskOut,
    status_code=202,
)
def create_log_parse_task(
    body: LogParseTaskCreate,
    db: Session = Depends(get_db),
):
    try:
        task = create_log_parse_task_record(db, body.run_ids)
        log_parse_task_runner.submit(task.id, _parse_stored_log)
        return task
    except LogParseTaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get(
    "/log-analysis/parse-tasks/{task_id}",
    response_model=LogParseTaskOut,
)
def get_log_parse_task(task_id: str, db: Session = Depends(get_db)):
    try:
        return get_log_parse_task_record(db, task_id)
    except LogParseTaskNotFound:
        raise HTTPException(status_code=404, detail="解析任务不存在") from None


@router.post(
    "/log-analysis/parse-tasks/{task_id}/cancel",
    response_model=LogParseTaskOut,
)
def cancel_log_parse_task(task_id: str, db: Session = Depends(get_db)):
    try:
        return request_log_parse_cancel(db, task_id)
    except LogParseTaskNotFound:
        raise HTTPException(status_code=404, detail="解析任务不存在") from None
    except LogParseTaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

# 注意：批量路由必须注册在 parse/{log_id} 之前 —— 否则 FastAPI 会优先匹配
# 路径变量路由，把 "batch" 当成 log_id，导致永远走不到批量逻辑。
@router.post("/log-analysis/parse/batch")
def batch_parse_logs(data: Dict[str, Any] = Body(...)):
    """
    批量解析日志，提取结构化数据并存储到 MongoDB。

    返回结构（前端依赖）：
      {
        "success_count": int,
        "fail_count":    int,
        "results":       { "<log_id>": AnalyzeLogOut, ... },   # 仅成功项
        "fail_details":  [ {"log_id": str, "error": str}, ... ]
      }

    设计要点：results 用 dict 而非 list，前端按 log_id 缓存方便「查看解析结果」按钮回看。
    """
    log_ids = data.get("log_ids", [])
    if not log_ids:
        raise HTTPException(status_code=400, detail="请提供要解析的日志ID列表")

    success_count = 0
    fail_count = 0
    results: Dict[str, Any] = {}
    fail_details: List[Dict[str, str]] = []

    for log_id in log_ids:
        try:
            # parse_log 返回 AnalyzeLogOut（pydantic model），
            # FastAPI 序列化时会自动转 dict；这里手工 .model_dump() 保持显式。
            r = parse_log(log_id)
            results[str(log_id)] = r.model_dump() if hasattr(r, "model_dump") else r
            success_count += 1
        except HTTPException as exc:
            fail_count += 1
            fail_details.append({"log_id": str(log_id), "error": str(exc.detail)})
        except Exception as exc:
            fail_count += 1
            fail_details.append({"log_id": str(log_id), "error": str(exc)})

    return {
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results,
        "fail_details": fail_details,
    }


@router.post("/log-analysis/parse/{log_id}", response_model=AnalyzeLogOut)
def parse_log(log_id: str):
    """
    解析单个日志，提取结构化数据并存储到 MongoDB。
    """
    # 防御：FastAPI 路径变量路由优先级高于平级路由，"batch" / "" 等保留字
    # 在历史上会撞进这里被当成 run_id 去 mongo 查（404 → 被外层 try/except 转成 500）。
    # 现在批量路由已挪到上面，这里再保留一次防御，避免顺序回退后再次踩坑。
    if log_id in ("batch", "", None):
        raise HTTPException(status_code=400, detail=f"非法的日志ID: {log_id!r}")
    try:
        return _parse_stored_log(log_id)
    except HTTPException:
        # 透传：404/400 等显式抛出的状态码不应被下面 catch-all 重写成 500
        raise
    except Exception as exc:
        logger.exception("解析日志失败: log_id=%s", log_id)
        raise HTTPException(status_code=500, detail=f"解析日志失败: {str(exc)}")

# ── 新增：日志分析相关 API ─────────────────────────────────────────────────────

@router.post("/log-analysis/analyze/log/mixed", response_model=AnalyzeLogOut)
async def analyze_log(data: Dict[str, Any] = Body(...)):
    """
    分析日志，支持文本、文件和接口导入三种方式。
    """
    if "log_text" in data:
        # 文本输入方式
        log_text = data.get("log_text")
        if not log_text:
            raise HTTPException(status_code=400, detail="日志内容不能为空")
        return _analyze_text(log_text, "manual_input.log", source_type="text")
    elif "url" in data:
        # 接口导入方式
        url = data.get("url")
        method = data.get("method", "GET")
        params = data.get("params", {})
        headers = data.get("headers", {})
        
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                if method.upper() == "GET":
                    response = await client.get(url, params=params, headers=headers, timeout=30)
                else:
                    response = await client.post(url, json=params, headers=headers, timeout=30)
                response.raise_for_status()
                text = response.text
        except Exception as exc:
            raise HTTPException(status_code=400, detail="上传源连接失败，请检查地址、权限或网络状态")
        
        return _analyze_text(text, "api_import.log", source_type="api", api_url=url)
    else:
        raise HTTPException(status_code=400, detail="无效的请求参数")

@router.post("/log-analysis/analyze/log/file", response_model=List[AnalyzeLogOut])
async def analyze_log_file(files: List[UploadFile] = File(...)):
    """
    分析日志文件（支持多个文件）。
    """
    # 检查文件格式
    valid_extensions = [".log", ".txt", ".csv", ".json"]
    results = []
    
    for file in files:
        file_ext = "." + file.filename.split(".")[-1].lower() if file.filename else ""
        if file_ext not in valid_extensions:
            continue  # 跳过无效文件
        
        content_bytes = await file.read()
        _ensure_upload_size(content_bytes)
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = content_bytes.decode("gbk", errors="ignore")
        
        result = _analyze_text(text, file.filename or "uploaded_file.log", source_type="file")
        results.append(result)
    
    return results

@router.post("/log-analysis/analyze/log", response_model=AnalyzeLogResult)
def analyze_log_score(data: ManualScoreAnalyzeRequest):
    """
    分析日志，计算异常评分。
    """
    from app.database import SessionLocal
    from app.models import Run
    
    db = SessionLocal()
    try:
        # 查找日志
        run = db.query(Run).filter(Run.run_id == data.log_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="日志不存在")
        
        # 模拟千问3大模型和注意力机制模型联合计算
        # 实际实现中，这里应该调用真实的模型进行计算
        import random
        base_score = random.uniform(0, 10)
        final_score = (base_score + data.weighted_score) / 2
        final_score = round(final_score, 1)
        
        # 确定风险等级
        if final_score >= 8:
            risk_level = "高分（宕机状态）"
        elif final_score >= 4:
            risk_level = "中等分数（非宕机异常）"
        else:
            risk_level = "低分（正常状态）"
        
        # 更新数据库中的故障分数
        run.fault_score = final_score
        db.commit()
        
        # 生成评分分布统计
        score_distribution = {
            "0-2": random.randint(0, 10),
            "2-4": random.randint(0, 10),
            "4-6": random.randint(0, 10),
            "6-8": random.randint(0, 10),
            "8-10": random.randint(0, 10)
        }
        
        # 存储分析结果到MongoDB
        from app.database import get_mongo_db
        mongo_db = get_mongo_db()
        mongo_db.log_analysis_result.insert_one({
            "log_id": data.log_id,
            "score": final_score,
            "risk_level": risk_level,
            "confidence": 0.95,
            "analysis_time": datetime.now(),
            "score_distribution": score_distribution
        })
        
        return {
            "score": final_score,
            "risk_level": risk_level,
            "confidence": 0.95,
            "analysis_time": datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="分析失败: " + str(exc))
    finally:
        db.close()

# ── 新增：日志解析状态管理 API ─────────────────────────────────────────────────────

@router.post("/log-analysis/analyze/existing")
def analyze_existing_log(run_id: str = Body(..., embed=True)):
    """
    解析已上传的日志
    """
    try:
        from datetime import datetime
        
        mongo_db = get_mongo_db()
        
        # 查找已上传的日志
        log_record = mongo_db["log_uploads"].find_one({"run_id": run_id})
        if not log_record:
            raise HTTPException(status_code=404, detail="日志不存在")
        
        # 检查是否已解析
        if log_record.get("analyzed", False):
            raise HTTPException(status_code=400, detail="日志已解析")
        
        # 获取原始日志内容
        raw_content = log_record.get("raw_content")
        if not raw_content:
            raise HTTPException(status_code=400, detail="日志内容为空")
        
        # 调用完整分析链路
        analysis_result = _analyze_text(
            raw_content,
            log_record.get("filename", "unknown.log"),
            run_id=run_id,
        )
        
        # 更新日志状态为已解析
        try:
            mongo_db["log_uploads"].update_one(
                {"run_id": run_id},
                {"$set": {
                    "analyzed": True,
                    "analyzed_at": datetime.now(),
                    "has_fault": analysis_result.has_fault,
                    "fault_score": analysis_result.fault_score
                }}
            )
            
            # 存储解析结果到新集合
            analysis_result_doc = {
                "run_id": run_id,
                "source_type": log_record.get("source_type", "text"),
                "filename": log_record.get("filename", "unknown.log"),
                "api_url": log_record.get("api_url"),
                "analyzed_at": datetime.now(),
                "has_fault": analysis_result.has_fault,
                "fault_score": analysis_result.fault_score,
                "result": analysis_result.model_dump()
            }
            mongo_db["log_analysis_results"].insert_one(analysis_result_doc)
        except Exception as db_error:
            logger.error(f"数据库写入失败: {db_error}")
            raise HTTPException(status_code=500, detail="解析结果存储失败，请检查mongodb连接")
        
        return {"run_id": run_id, "status": "success", "message": "日志解析成功", "result": analysis_result.model_dump()}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"解析现有日志失败: {e}")
        # 处理特定异常
        if "超时" in str(e):
            raise HTTPException(status_code=500, detail="日志解析超时，建议分批次解析小批量日志")
        elif "无有效解析特征" in str(e):
            raise HTTPException(status_code=400, detail="当前日志无有效解析特征，标记为无效日志")
        else:
            raise HTTPException(status_code=500, detail="日志解析失败")

@router.get("/log-analysis/analysis/result/{run_id}")
def get_analysis_result(run_id: str):
    """
    获取日志解析结果
    """
    try:
        from bson import ObjectId
        from datetime import datetime
        
        def convert_mongo_types(obj):
            """递归转换 MongoDB 类型为 JSON 可序列化类型"""
            if isinstance(obj, ObjectId):
                return str(obj)
            elif isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: convert_mongo_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_mongo_types(item) for item in obj]
            else:
                return obj
        
        mongo_db = get_mongo_db()
        # 获取解析结果
        analysis_result = mongo_db["log_analysis_results"].find_one({"run_id": run_id}, {"_id": 0})
        
        if not analysis_result:
            raise HTTPException(status_code=404, detail="解析结果不存在")
        
        # 获取原始日志
        log_record = mongo_db["log_uploads"].find_one({"run_id": run_id}, {"_id": 0})
        
        if not log_record:
            raise HTTPException(status_code=404, detail="原始日志不存在")
        
        # 组合返回数据
        response_data = {
            "result": convert_mongo_types(analysis_result),
            "raw_log": log_record.get("raw_content", "")
        }
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取解析结果失败: {e}")
        raise HTTPException(status_code=500, detail="获取解析结果失败")

@router.post("/log-analysis/batch-analyze")
def batch_analyze_logs(data: Dict[str, Any] = Body(...)):
    """
    批量解析日志
    """
    try:
        from datetime import datetime
        
        run_ids = data.get("run_ids", [])
        if not run_ids:
            raise HTTPException(status_code=400, detail="请提供要解析的日志ID列表")
        
        mongo_db = get_mongo_db()
        success_count = 0
        fail_count = 0
        failed_ids = []
        
        for run_id in run_ids:
            try:
                # 查找已上传的日志
                log_record = mongo_db["log_uploads"].find_one({"run_id": run_id})
                if not log_record:
                    fail_count += 1
                    failed_ids.append(run_id)
                    continue
                
                # 检查是否已解析
                if log_record.get("analyzed", False):
                    fail_count += 1
                    failed_ids.append(run_id)
                    continue
                
                # 获取原始日志内容
                raw_content = log_record.get("raw_content")
                if not raw_content:
                    fail_count += 1
                    failed_ids.append(run_id)
                    continue
                
                # 调用完整分析链路
                analysis_result = _analyze_text(
                    raw_content,
                    log_record.get("filename", "unknown.log"),
                    run_id=run_id,
                )
                
                # 更新日志状态为已解析
                try:
                    mongo_db["log_uploads"].update_one(
                        {"run_id": run_id},
                        {"$set": {
                            "analyzed": True,
                            "analyzed_at": datetime.now(),
                            "has_fault": analysis_result.has_fault,
                            "fault_score": analysis_result.fault_score
                        }}
                    )
                    
                    # 存储解析结果到新集合
                    analysis_result_doc = {
                        "run_id": run_id,
                        "source_type": log_record.get("source_type", "text"),
                        "filename": log_record.get("filename", "unknown.log"),
                        "api_url": log_record.get("api_url"),
                        "analyzed_at": datetime.now(),
                        "has_fault": analysis_result.has_fault,
                        "fault_score": analysis_result.fault_score,
                        "result": analysis_result.model_dump()
                    }
                    mongo_db["log_analysis_results"].insert_one(analysis_result_doc)
                    
                    success_count += 1
                except Exception as db_error:
                    logger.error(f"数据库写入失败: {db_error}")
                    fail_count += 1
                    failed_ids.append(run_id)
            except Exception as e:
                logger.error(f"解析日志 {run_id} 失败: {e}")
                fail_count += 1
                failed_ids.append(run_id)
        
        return {
            "success_count": success_count,
            "fail_count": fail_count,
            "failed_ids": failed_ids,
            "message": f"批量解析完成：成功 {success_count} 条，失败 {fail_count} 条"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量解析失败: {e}")
        # 处理特定异常
        if "超时" in str(e):
            raise HTTPException(status_code=500, detail="日志解析超时，建议分批次解析小批量日志")
        else:
            raise HTTPException(status_code=500, detail="批量解析失败")
