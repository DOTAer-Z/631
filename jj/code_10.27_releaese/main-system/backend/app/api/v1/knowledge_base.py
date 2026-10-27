from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.models.fault_type import FaultType
from app.models.log_entry import LogEntry
from app.schemas.fault_type import FaultTypeCreate, FaultTypeUpdate, FaultTypeOut
from app.schemas.log_entry import LogEntryOut, LogEntryListOut
from app.services.vector_store import VectorStoreService, get_vector_store
from app.utils.cache import cache_delete, GRAPH_CACHE_KEY

router = APIRouter()


class KnowledgeCaseCreate(BaseModel):
    """新增知识案例入参（结构化，无需上传文件）。"""
    fault_type_id: int
    root_cause: str
    solution: Optional[str] = None
    sample_log: Optional[str] = None

# ─── 故障类型 CRUD ──────────────────────────────────────────────────────────────

@router.get("/fault-types", response_model=list[FaultTypeOut])
def list_fault_types(db: Session = Depends(get_db)):
    return db.query(FaultType).order_by(FaultType.created_at.desc()).all()


@router.post("/fault-types", response_model=FaultTypeOut)
def create_fault_type(body: FaultTypeCreate, db: Session = Depends(get_db)):
    if db.query(FaultType).filter(FaultType.name == body.name).first():
        raise HTTPException(status_code=400, detail="故障类型名称已存在")
    ft = FaultType(**body.model_dump())
    db.add(ft)
    db.commit()
    db.refresh(ft)
    cache_delete(GRAPH_CACHE_KEY)
    return ft


@router.put("/fault-types/{ft_id}", response_model=FaultTypeOut)
def update_fault_type(ft_id: int, body: FaultTypeUpdate, db: Session = Depends(get_db)):
    ft = db.query(FaultType).filter(FaultType.id == ft_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ft, k, v)
    db.commit()
    db.refresh(ft)
    cache_delete(GRAPH_CACHE_KEY)
    return ft


@router.delete("/fault-types/{ft_id}")
def delete_fault_type(ft_id: int, db: Session = Depends(get_db)):
    ft = db.query(FaultType).filter(FaultType.id == ft_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")
    db.delete(ft)
    db.commit()
    cache_delete(GRAPH_CACHE_KEY)
    return {"ok": True}


# ─── 日志管理 ────────────────────────────────────────────────────────────────────

@router.get("/logs", response_model=LogEntryListOut)
def list_logs(
    fault_type_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(LogEntry)
    if fault_type_id:
        q = q.filter(LogEntry.fault_type_id == fault_type_id)
    total = q.count()
    items = q.order_by(LogEntry.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    result = []
    for le in items:
        item = LogEntryOut.model_validate(le)
        if le.fault_type:
            item.fault_type_name = le.fault_type.name
        result.append(item)
    return LogEntryListOut(total=total, items=result)


@router.post("/logs/upload", response_model=LogEntryOut)
async def upload_log(
    file: UploadFile = File(...),
    fault_type_id: int = Form(...),
    summary: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    ft = db.query(FaultType).filter(FaultType.id == fault_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")

    content_bytes = await file.read()
    try:
        raw_content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw_content = content_bytes.decode("gbk", errors="ignore")

    log_entry = LogEntry(
        fault_type_id=fault_type_id,
        filename=file.filename,
        raw_content=raw_content,
        summary=summary,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    # 自动向量化入库
    try:
        vs = get_vector_store()
        doc_id = f"log_entry_{log_entry.id}"
        vs.add_document(
            doc_id=doc_id,
            text=raw_content,
            metadata={"log_entry_id": log_entry.id, "fault_type_name": ft.name, "fault_type_id": ft.id},
        )
        log_entry.chroma_doc_id = doc_id
        log_entry.is_indexed = True
        db.commit()
        db.refresh(log_entry)
    except Exception as e:
        # 单条上传时不阻塞主流程，但把失败原因记到日志，便于排查
        import logging
        logging.getLogger(__name__).warning(f"log_entry {log_entry.id} 自动入库失败: {e}")

    cache_delete(GRAPH_CACHE_KEY)

    out = LogEntryOut.model_validate(log_entry)
    out.fault_type_name = ft.name
    return out


@router.delete("/logs/{log_id}")
def delete_log(log_id: int, db: Session = Depends(get_db)):
    le = db.query(LogEntry).filter(LogEntry.id == log_id).first()
    if not le:
        raise HTTPException(status_code=404, detail="日志不存在")
    if le.chroma_doc_id:
        try:
            vs = get_vector_store()
            vs.delete_document(le.chroma_doc_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"chroma 删除 {le.chroma_doc_id} 失败: {e}")
    db.delete(le)
    db.commit()
    cache_delete(GRAPH_CACHE_KEY)
    return {"ok": True}


def get_or_create_fault_type(db: Session, name: str, description: Optional[str] = None) -> FaultType:
    """按名称取故障类型，不存在则创建（用于从标注 anomaly_type 映射）。返回 (ft, created)。"""
    name = (name or "").strip()
    ft = db.query(FaultType).filter(FaultType.name == name).first()
    if ft:
        return ft, False
    ft = FaultType(name=name, description=(description or None), color_tag="red")
    db.add(ft)
    db.commit()
    db.refresh(ft)
    return ft, True


def _persist_knowledge_case(
    db: Session,
    ft: FaultType,
    root_cause: str,
    solution: Optional[str],
    sample_log: Optional[str],
) -> LogEntry:
    """把「故障类型 + 根因 + 解决方案 + 样例日志」组装成知识文本，写 log_entries 并向量化进 Chroma。"""
    parts = [f"故障类型：{ft.name}"]
    if ft.description:
        parts.append(f"类型描述：{ft.description}")
    parts.append(f"根因：{root_cause.strip()}")
    if (solution or "").strip():
        parts.append(f"解决方案：{solution.strip()}")
    if (sample_log or "").strip():
        parts.append(f"样例日志：{sample_log.strip()}")
    raw_content = "\n".join(parts)

    log_entry = LogEntry(
        fault_type_id=ft.id,
        filename=f"知识案例：{ft.name}",
        raw_content=raw_content,
        summary=root_cause.strip(),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    try:
        vs = get_vector_store()
        doc_id = f"log_entry_{log_entry.id}"
        vs.add_document(
            doc_id=doc_id,
            text=raw_content,
            metadata={
                "log_entry_id": log_entry.id,
                "fault_type_name": ft.name,
                "fault_type_id": ft.id,
                "root_cause": root_cause.strip(),
            },
        )
        log_entry.chroma_doc_id = doc_id
        log_entry.is_indexed = True
        db.commit()
        db.refresh(log_entry)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"知识案例 {log_entry.id} 自动入库失败: {e}")

    return log_entry


@router.post("/knowledge-cases", response_model=LogEntryOut)
def create_knowledge_case(body: KnowledgeCaseCreate, db: Session = Depends(get_db)):
    """新增结构化知识案例（无需上传文件）。"""
    ft = db.query(FaultType).filter(FaultType.id == body.fault_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="故障类型不存在")
    if not (body.root_cause or "").strip():
        raise HTTPException(status_code=400, detail="根因不能为空")

    log_entry = _persist_knowledge_case(db, ft, body.root_cause, body.solution, body.sample_log)

    cache_delete(GRAPH_CACHE_KEY)
    out = LogEntryOut.model_validate(log_entry)
    out.fault_type_name = ft.name
    return out


class AnnotationCaseIn(BaseModel):
    """从标注子系统导出的一条典型案例（导入入参）。"""
    anomaly_type: str
    note: Optional[str] = None
    fault_type_description: Optional[str] = None
    log_text: Optional[str] = None
    window_id: Optional[int] = None
    package_name: Optional[str] = None
    external_run_id: Optional[str] = None


class ImportAnnotationCasesRequest(BaseModel):
    cases: list[AnnotationCaseIn]


@router.get("/knowledge-base/annotation-cases")
def list_annotation_cases(
    anomaly_type: Optional[str] = None,
    package_id: Optional[int] = None,
    limit: int = 200,
):
    """透传标注子系统的典型异常案例（DB2）给前端选择导入。"""
    from app.services import annotate_client
    try:
        return annotate_client.fetch_export_cases(
            anomaly_type=anomaly_type, package_id=package_id, limit=limit
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"无法从标注子系统获取案例：{exc}")


@router.get("/knowledge-base/annotation-summary")
def get_annotation_summary():
    """透传标注子系统(DB2 data_bj)的全局计数，供「文件选择」页展示。

    去标注的数据进入标注库、按语义段/时间窗口切片并标注，但不生成主系统 runs/cases，
    因此天然不出现在文件选择列表、不可被分析；此接口仅提供计数可见性。
    """
    from app.services import annotate_client
    try:
        return annotate_client.fetch_dashboard_summary()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"无法从标注子系统获取计数：{exc}")


@router.post("/knowledge-base/import-annotation-cases")
def import_annotation_cases(body: ImportAnnotationCasesRequest, db: Session = Depends(get_db)):
    """把选中的标注典型案例入知识库(RAG)并热更新知识图谱(KG)。单条失败不阻断整体。"""
    from app.services.kg_service_v2 import kg_service_v2

    imported = 0
    created_fault_types = 0
    kg_appended = 0
    errors: list[str] = []

    for c in body.cases:
        try:
            atype = (c.anomaly_type or "").strip()
            if not atype:
                errors.append("anomaly_type 为空，跳过")
                continue
            ft, created = get_or_create_fault_type(db, atype, c.fault_type_description)
            if created:
                created_fault_types += 1
            root_cause = (c.note or "").strip() or (c.fault_type_description or "").strip() or atype
            _persist_knowledge_case(db, ft, root_cause, None, c.log_text)
            imported += 1
            try:
                r = kg_service_v2.append_curated_case(
                    fault_type_name=atype,
                    root_cause=root_cause,
                    evidence=(c.external_run_id or c.package_name),
                )
                if r.get("appended"):
                    kg_appended += 1
            except Exception as kge:
                errors.append(f"KG 追加失败({atype}): {kge}")
        except Exception as e:
            errors.append(str(e))

    cache_delete(GRAPH_CACHE_KEY)
    return {
        "imported": imported,
        "created_fault_types": created_fault_types,
        "kg_appended": kg_appended,
        "errors": errors,
    }


@router.post("/logs/batch-index")
def batch_index(db: Session = Depends(get_db)):
    """
    一键入库：把 log_entries 中尚未向量化的全部条目批量写入 Chroma。

    - 复用 get_vector_store() 单例，避免每次重新加载 BGE 模型
    - 失败的逐条记录到 `failed_items`，不再静默吞异常
    - 整体初始化失败（例如模型加载错误）→ 直接 500 + 真实原因
    """
    import logging

    logger = logging.getLogger(__name__)

    unindexed = db.query(LogEntry).filter(LogEntry.is_indexed == False).all()  # noqa: E712
    if not unindexed:
        return {"indexed": 0, "total": 0, "failed": 0, "failed_items": []}

    try:
        vs = get_vector_store()
    except Exception as exc:
        logger.exception("初始化 VectorStoreService 失败")
        raise HTTPException(status_code=500, detail=f"向量库初始化失败: {exc}")

    success = 0
    failed_items: list[dict] = []

    for le in unindexed:
        try:
            ft_name = le.fault_type.name if le.fault_type else "未知"
            doc_id = f"log_entry_{le.id}"
            vs.add_document(
                doc_id=doc_id,
                text=le.raw_content or "",
                metadata={
                    "log_entry_id": le.id,
                    "fault_type_name": ft_name,
                    "fault_type_id": le.fault_type_id or 0,
                },
            )
            le.chroma_doc_id = doc_id
            le.is_indexed = True
            success += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"log_entry {le.id} 入库失败: {exc}")
            failed_items.append({"log_entry_id": le.id, "error": str(exc)})

    db.commit()
    return {
        "indexed": success,
        "total": len(unindexed),
        "failed": len(failed_items),
        "failed_items": failed_items[:20],  # 仅返回前 20 条样本，避免响应过大
    }


# ══════════════════════════════════════════════════════════════════════════════
# 数据导入的 run → 知识库(Chroma) + 知识图谱(KG)   ★ 9.16 新增
#
# 背景：9.10 之前所有向量化入口只认 SQL log_entries 表（demo 11 条），用户在
# 「数据导入」灌进 Postgres 的三类数据没有任何路径参与诊断检索 / 图谱。
# 下面两个端点把「文件选择」里的 run 按窗口灌进 Chroma + KG。
# ══════════════════════════════════════════════════════════════════════════════

class IndexRunsRequest(BaseModel):
    """批量入知识库入参。"""
    run_ids: List[str]
    window_scope: str = "suspicious"   # suspicious | all
    max_units_per_run: int = 60        # 每条 run 最多入多少窗口/条目，0 = 不限
    force: bool = False                # True = 已入库的也重算覆盖
    skip_kg: bool = False              # True = 只入向量库，不动知识图谱
    create_fault_types: bool = True    # 自动为新见到的故障类型名建行


class IndexRunsOut(BaseModel):
    indexed: int
    skipped: int
    failed: int
    runs_indexed: int
    runs_skipped: int
    kg_cases: int
    kg_new_nodes: int
    kg_new_edges: int
    created_fault_types: int
    details: List[dict]
    errors: List[str]


@router.post("/knowledge-base/index-runs", response_model=IndexRunsOut)
def index_runs_to_knowledge(
    body: IndexRunsRequest,
    db: Session = Depends(get_db),
):
    """把「文件选择」选中的 run 批量写入知识库(Chroma) + 知识图谱。

    - 单位是 log_window（诊断检索的粒度）；该 run 没有窗口时退化为整条 entry
    - 未标注故障类型的 run 会被 skip（检索候选落到未分类会拉低诊断置信度）
    - 重复调用幂等：doc_id 稳定 + Chroma upsert，不会产生重复向量
    - 单条 run 失败不阻断整体
    """
    from app.database import get_mongo_db  # 复用兼容层拿到 log_windows 集合
    from app.services.run_indexing_service import IndexingError, index_runs

    if not body.run_ids:
        raise HTTPException(status_code=400, detail="run_ids 不能为空")
    if body.window_scope not in ("suspicious", "all"):
        raise HTTPException(status_code=400, detail="window_scope 只能是 suspicious 或 all")
    if len(body.run_ids) > 200:
        raise HTTPException(status_code=400, detail="单次最多 200 条 run，请分批入库")

    try:
        mongo_db = get_mongo_db()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"文档库初始化失败: {exc}")

    try:
        return index_runs(
            db,
            mongo_db,
            run_ids=body.run_ids,
            window_scope=body.window_scope,
            max_units_per_run=body.max_units_per_run or 60,
            force=body.force,
            skip_kg=body.skip_kg,
            create_fault_types=body.create_fault_types,
        )
    except IndexingError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/knowledge-base/index-status")
def get_index_status(body: IndexRunsRequest, db: Session = Depends(get_db)):
    """查一批 run 的入库状态，供「文件选择」列表展示「已入知识库」列。

    复用 IndexRunsRequest 只为拿 run_ids；其余参数忽略。
    """
    from app.services.run_indexing_service import get_index_status as _status

    if not body.run_ids:
        return {"items": {}}
    return {"items": _status(body.run_ids)}


@router.post("/knowledge-base/remove-run-index")
def remove_run_index(body: IndexRunsRequest, db: Session = Depends(get_db)):
    """把若干 run 从知识库(Chroma) + 知识图谱(KG) 中移除。

    用于「误入库了未标注数据」或「改故障类型后要重算」的场景：
    先移除再重新 index-runs，即可干净重建。run 本身与日志文档不受影响。
    """
    from app.services.kg_service_v2 import kg_service_v2
    from app.services.vector_store import get_vector_store

    if not body.run_ids:
        raise HTTPException(status_code=400, detail="run_ids 不能为空")

    removed_vectors = 0
    try:
        vs = get_vector_store()
        for rid in body.run_ids:
            got = vs.collection.get(where={"run_id": rid}, include=[])
            ids = got.get("ids") or []
            if ids:
                vs.delete_documents(ids)
                removed_vectors += len(ids)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"清理向量失败: {exc}")

    kg_res = kg_service_v2.remove_run_cases(list(body.run_ids))

    cache_delete(GRAPH_CACHE_KEY)
    return {
        "removed_vectors": removed_vectors,
        "removed_kg_nodes": kg_res.get("removed_nodes", 0),
        "removed_kg_edges": kg_res.get("removed_edges", 0),
    }
