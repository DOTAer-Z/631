"""
test_data_flow.py

仅用于验证新数据链路是否正常，不接前端、不写入数据库。

GET /api/test/data-flow
  1. 从 MongoDB log_windows 取一条记录
  2. 用 FAISS + 本地 embedding 模型检索 top-k 相似案例
  3. 从知识图谱查询 root_cause
  4. 返回完整链路结果 + debug 日志
"""

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.database import get_mongo_db
from app.services.vector_retrieval_service import vector_retrieval_service
from app.services.kg_service_v2 import kg_service_v2

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/test/data-flow",
    summary="[调试] 验证 Mongo → FAISS → KG 数据链路",
    tags=["调试"],
)
def test_data_flow(
    top_k: int = Query(default=3, ge=1, le=20, description="FAISS 返回相似案例数"),
    only_fault: bool = Query(default=False, description="是否只检索 is_fault=True 的案例"),
    window_id: Optional[str] = Query(default=None, description="指定 window_id；不传则取第一条"),
) -> Dict[str, Any]:
    """
    数据链路验证接口，依次执行：

    1. **Mongo**：从 log_windows 集合取一条记录
    2. **FAISS**：对 window.text 做 embedding，检索 top-k 相似案例
    3. **KG**：根据相似案例中的 fault_type 查询根因

    返回完整中间结果，方便逐层排查问题。
    """
    t0 = time.time()
    debug_steps: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Step 1: 从 MongoDB 取 log_window
    # ------------------------------------------------------------------
    step_t = time.time()
    logger.debug("[data-flow] Step1: querying MongoDB log_windows ...")

    try:
        mongo_db = get_mongo_db()
        col = mongo_db["log_windows"]

        if window_id:
            doc = col.find_one({"window_id": window_id})
            if doc is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"window_id={window_id!r} not found in MongoDB log_windows",
                )
        else:
            doc = col.find_one({}, sort=[("_id", 1)])
            if doc is None:
                raise HTTPException(
                    status_code=503,
                    detail="MongoDB log_windows collection is empty or unreachable",
                )

        # MongoDB ObjectId 不可 JSON 序列化，转为字符串
        doc["_id"] = str(doc["_id"])
        # 日期类型也做转换
        for k in ("start_time", "end_time"):
            if k in doc and hasattr(doc[k], "isoformat"):
                doc[k] = doc[k].isoformat()

        log_text: str = (doc.get("text") or "").strip()
        elapsed_mongo = round(time.time() - step_t, 3)
        logger.debug(
            "[data-flow] Step1 OK window_id=%s text_len=%d elapsed=%.3fs",
            doc.get("window_id"),
            len(log_text),
            elapsed_mongo,
        )
        debug_steps.append({
            "step": 1,
            "name": "mongo_fetch",
            "status": "ok",
            "elapsed_s": elapsed_mongo,
            "window_id": doc.get("window_id"),
            "text_len": len(log_text),
        })

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[data-flow] Step1 FAILED: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"MongoDB error: {exc}") from exc

    if not log_text:
        raise HTTPException(
            status_code=422,
            detail=f"log_window text is empty (window_id={doc.get('window_id')})",
        )

    # ------------------------------------------------------------------
    # Step 2: FAISS 向量检索
    # ------------------------------------------------------------------
    step_t = time.time()
    logger.debug(
        "[data-flow] Step2: FAISS retrieval top_k=%d only_fault=%s ...",
        top_k,
        only_fault,
    )

    try:
        similar_cases = vector_retrieval_service.query(
            text=log_text,
            top_k=top_k,
            only_fault=only_fault,
        )
        elapsed_faiss = round(time.time() - step_t, 3)
        logger.debug(
            "[data-flow] Step2 OK returned=%d elapsed=%.3fs",
            len(similar_cases),
            elapsed_faiss,
        )
        debug_steps.append({
            "step": 2,
            "name": "faiss_retrieval",
            "status": "ok",
            "elapsed_s": elapsed_faiss,
            "returned": len(similar_cases),
            "top_similarity": similar_cases[0]["similarity"] if similar_cases else None,
        })
    except Exception as exc:
        logger.error("[data-flow] Step2 FAILED: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"FAISS error: {exc}") from exc

    # ------------------------------------------------------------------
    # Step 3: KG 根因查询
    # 取相似案例中 similarity 最高且有 fault_type 的那条做查询
    # ------------------------------------------------------------------
    step_t = time.time()
    logger.debug("[data-flow] Step3: KG root_cause lookup ...")

    kg_result: Dict[str, Any] = {}
    kg_query_key: Optional[str] = None

    # 优先用相似案例的 fault_type；其次用 case_id；再用当前 window 的 fault_type
    for case in similar_cases:
        if case.get("fault_type"):
            kg_query_key = case["fault_type"]
            break
    if not kg_query_key:
        kg_query_key = doc.get("fault_type") or doc.get("metadata", {}).get("fault_type")

    try:
        if kg_query_key:
            root_causes = kg_service_v2.get_root_cause_by_fault_type(kg_query_key)
            fault_info = kg_service_v2.get_fault_type_info(kg_query_key)
            affected_components = kg_service_v2.get_affected_components(kg_query_key)
            kg_result = {
                "queried_fault_type": kg_query_key,
                "fault_info": fault_info,
                "root_causes": root_causes,
                "affected_components": affected_components,
            }
            logger.debug(
                "[data-flow] Step3 OK fault_type=%s root_causes=%d",
                kg_query_key,
                len(root_causes),
            )
        else:
            kg_result = {
                "queried_fault_type": None,
                "note": "No fault_type available in similar cases or current window; KG query skipped.",
            }
            logger.debug("[data-flow] Step3 SKIPPED: no fault_type to query")

        elapsed_kg = round(time.time() - step_t, 3)
        debug_steps.append({
            "step": 3,
            "name": "kg_lookup",
            "status": "ok" if kg_query_key else "skipped",
            "elapsed_s": elapsed_kg,
            "queried_fault_type": kg_query_key,
            "root_cause_count": len(kg_result.get("root_causes") or []),
        })
    except Exception as exc:
        logger.error("[data-flow] Step3 FAILED: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail=f"KG error: {exc}") from exc

    # ------------------------------------------------------------------
    # 汇总返回
    # ------------------------------------------------------------------
    total_elapsed = round(time.time() - t0, 3)
    logger.info(
        "[data-flow] DONE total_elapsed=%.3fs mongo=%.3fs faiss=%.3fs kg=%.3fs",
        total_elapsed,
        debug_steps[0]["elapsed_s"],
        debug_steps[1]["elapsed_s"],
        debug_steps[2]["elapsed_s"],
    )

    # ------------------------------------------------------------------
    # 裁剪返回体，避免大文本撑爆 Swagger
    # ------------------------------------------------------------------
    def _preview(text: Optional[str], limit: int) -> Optional[str]:
        if not text:
            return text
        return text[:limit] + ("..." if len(text) > limit else "")

    slim_cases = [
        {
            "rank": c.get("rank"),
            "similarity": round(c.get("similarity", 0), 4),
            "case_id": c.get("case_id"),
            "subsystem": c.get("subsystem"),
            "fault_type": c.get("fault_type"),
            "root_cause": c.get("root_cause"),
            "snippet_preview": _preview(c.get("text"), 250),
        }
        for c in similar_cases
    ]

    slim_kg = {
        "queried_fault_type": kg_result.get("queried_fault_type"),
        "root_causes": kg_result.get("root_causes"),        # 结构已经很精简
        "affected_components": kg_result.get("affected_components"),
        "note": kg_result.get("note"),
    }

    return {
        "log": {
            "window_id": doc.get("window_id"),
            "case_id": doc.get("case_id"),
            "run_id": doc.get("run_id"),
            "subsystem": doc.get("subsystem"),
            "is_fault": doc.get("is_fault"),
            "fault_type": doc.get("fault_type"),
            "text_preview": _preview(log_text, 400),
            "text_len": len(log_text),
        },
        "similar_cases": slim_cases,
        "kg_result": slim_kg,
        "_debug": {
            "total_elapsed_s": total_elapsed,
            "steps": debug_steps,
            "params": {
                "top_k": top_k,
                "only_fault": only_fault,
                "window_id_param": window_id,
            },
        },
    }
