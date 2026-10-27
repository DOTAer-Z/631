from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.models.fault_type import FaultType
from app.models.log_entry import LogEntry
from app.services.llm_service import LLMService
from app.services.vector_store import get_vector_store
from app.schemas.data_processing import (
    PreprocessRequest, PreprocessOut,
    SaveProcessedLogRequest, SaveProcessedLogOut,
)
from app.utils.cache import cache_delete, GRAPH_CACHE_KEY

router = APIRouter()
_llm_service: Optional[LLMService] = None


def get_llm() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


# ── POST /data-processing/preprocess（文本输入） ───────────────────────────────
@router.post("/data-processing/preprocess", response_model=PreprocessOut)
def preprocess_text(body: PreprocessRequest):
    """LLM 辅助：清洗文本 + 提取摘要、关键词、建议故障类型"""
    try:
        llm = get_llm()
        result = llm.preprocess(body.raw_text)
        return PreprocessOut(**result)
    except RuntimeError as exc:
        # LLMService._runtime 抛 RuntimeError 表示未配置/已关闭，
        # 给前端 503 + 明确提示，而不是 500。
        raise HTTPException(status_code=503, detail=str(exc))


# ── POST /data-processing/preprocess/file（文件输入） ─────────────────────────
@router.post("/data-processing/preprocess/file", response_model=PreprocessOut)
async def preprocess_file(file: UploadFile = File(...)):
    """从上传文件读取内容后 LLM 预处理"""
    content_bytes = await file.read()
    try:
        raw_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = content_bytes.decode("gbk", errors="ignore")

    try:
        llm = get_llm()
        result = llm.preprocess(raw_text)
        return PreprocessOut(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── POST /data-processing/save ────────────────────────────────────────────────
@router.post("/data-processing/save", response_model=SaveProcessedLogOut)
def save_processed_log(body: SaveProcessedLogRequest, db: Session = Depends(get_db)):
    """保存处理后的日志到 log_entries，可选立即向量化"""
    ft = db.query(FaultType).filter(FaultType.id == body.fault_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")

    log_entry = LogEntry(
        fault_type_id=body.fault_type_id,
        filename=body.filename or "手动输入",
        raw_content=body.cleaned_text,
        summary=body.summary,
        is_indexed=False,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    # 可选：立即向量化
    if body.auto_vectorize:
        try:
            vs = get_vector_store()
            doc_id = f"log_entry_{log_entry.id}"
            vs.add_document(
                doc_id=doc_id,
                text=log_entry.raw_content,
                metadata={
                    "log_entry_id": log_entry.id,
                    "fault_type_name": ft.name,
                    "fault_type_id": ft.id,
                },
            )
            log_entry.chroma_doc_id = doc_id
            log_entry.is_indexed = True
            db.commit()
            db.refresh(log_entry)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"log_entry {log_entry.id} 自动入库失败: {e}"
            )

    cache_delete(GRAPH_CACHE_KEY)

    out = SaveProcessedLogOut.model_validate(log_entry)
    out.fault_type_name = ft.name
    return out
