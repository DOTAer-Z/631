import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text

# PostgreSQL 的 LIKE 默认转义符即反斜杠（与 _escape_like 一致），无需显式 ESCAPE 子句。
# 历史 MySQL 写法 ESCAPE '\\' 在 PG 下会因转义符非单字符而报错，故置空。
MYSQL_LIKE_ESCAPE_SQL = ""


def _escape_like(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", r"\%")
        .replace("_", r"\_")
    )


def parse_stats_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value

    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    return {}


def build_run_filters(
    *,
    run_id: Optional[str],
    case_id: Optional[str],
    test_name: Optional[str],
    fault_status: Optional[str],
    system_id: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    clauses = []
    params: Dict[str, Any] = {}

    if system_id:
        clauses.append("r.system_id = :system_id")
        params["system_id"] = system_id

    if run_id:
        clauses.append(f"r.run_id LIKE :run_id {MYSQL_LIKE_ESCAPE_SQL}")
        params["run_id"] = f"%{_escape_like(run_id)}%"

    if case_id:
        clauses.append(f"r.case_id LIKE :case_id {MYSQL_LIKE_ESCAPE_SQL}")
        params["case_id"] = f"%{_escape_like(case_id)}%"

    if test_name:
        clauses.append(f"c.test_name LIKE :test_name {MYSQL_LIKE_ESCAPE_SQL}")
        params["test_name"] = f"%{_escape_like(test_name)}%"

    if fault_status == "fault":
        clauses.append("r.is_fault IS TRUE")
    elif fault_status == "normal":
        clauses.append("r.is_fault IS FALSE")

    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def shape_run_row(row: Dict[str, Any]) -> Dict[str, Any]:
    stats = parse_stats_json(row.get("stats_json"))
    import_meta = stats.get("dataset_import_meta") or {}
    if not isinstance(import_meta, dict):
        import_meta = {}

    raw_tags = import_meta.get("tags") or []
    if isinstance(raw_tags, list):
        tags = [str(t) for t in raw_tags if str(t).strip()]
    else:
        tags = []

    return {
        "run_id": row["run_id"],
        "case_id": row.get("case_id"),
        "run_name": row.get("run_name"),
        "case_name": row.get("case_name"),
        "test_name": row.get("test_name"),
        "system_id": row.get("system_id"),
        "round_no": row.get("round_no"),
        "fault_type": row.get("fault_type"),
        "fault_status": "fault" if row.get("is_fault") else "normal",
        "parsed_lines": int(stats.get("parsed_lines") or 0),
        "total_lines": int(stats.get("total_lines") or stats.get("parsed_lines") or 0),
        "error_logs": int(row.get("error_logs") or 0),
        "critical_logs": int(row.get("critical_logs") or 0),
        "window_count": int(row.get("window_count") or 0),
        "created_at": row.get("created_at"),
        # ── 数据导入透传字段（旧数据为空字符串/[] / null，前端统一用 fallback 显示「-」）──
        "import_display_name": import_meta.get("display_name"),
        "import_description": import_meta.get("description"),
        "import_tags": tags,
        "import_created_by": import_meta.get("created_by"),
        "import_filename": import_meta.get("original_filename"),
        "import_id": import_meta.get("import_id"),
    }


def shape_window_doc(doc: Dict[str, Any], preview_length: int = 240) -> Dict[str, Any]:
    stats = doc.get("stats") or {}
    text = doc.get("text") or ""
    if len(text) > preview_length:
        text_preview = f"{text[: preview_length - 3]}..."
    else:
        text_preview = text

    return {
        "window_id": doc["window_id"],
        "start_time": doc.get("start_time"),
        "end_time": doc.get("end_time"),
        "strategy": doc.get("strategy"),
        "entry_count": int(stats.get("n_entries") or 0),
        "error_events": int(stats.get("error_events") or 0),
        "key_events": doc.get("key_events") or [],
        "text_preview": text_preview,
    }


def list_runs(
    db,
    *,
    page: int,
    page_size: int,
    run_id: Optional[str],
    case_id: Optional[str],
    test_name: Optional[str],
    fault_status: Optional[str],
    system_id: Optional[str],
) -> Dict[str, Any]:
    base_where, base_params = build_run_filters(
        run_id=run_id,
        case_id=case_id,
        test_name=test_name,
        fault_status=None,
        system_id=system_id,
    )

    list_where, list_params = build_run_filters(
        run_id=run_id,
        case_id=case_id,
        test_name=test_name,
        fault_status=fault_status,
        system_id=system_id,
    )
    list_params.update({"limit": page_size, "offset": (page - 1) * page_size})

    total = db.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM runs r
            LEFT JOIN cases c ON c.case_id = r.case_id
            {list_where}
            """
        ),
        list_params,
    ).scalar() or 0

    fault_count = db.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM runs r
            LEFT JOIN cases c ON c.case_id = r.case_id
            {base_where}{' AND' if base_where else ' WHERE'} r.is_fault IS TRUE
            """
        ),
        base_params,
    ).scalar() or 0

    normal_count = db.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM runs r
            LEFT JOIN cases c ON c.case_id = r.case_id
            {base_where}{' AND' if base_where else ' WHERE'} r.is_fault IS FALSE
            """
        ),
        base_params,
    ).scalar() or 0

    rows = db.execute(
        text(
            f"""
            SELECT
                r.run_id,
                r.case_id,
                r.run_name,
                r.system_id,
                r.round_no,
                r.is_fault,
                r.error_logs,
                r.critical_logs,
                r.window_count,
                r.stats_json,
                r.created_at,
                c.case_name,
                c.test_name,
                c.fault_type
            FROM runs r
            LEFT JOIN cases c ON c.case_id = r.case_id
            {list_where}
            ORDER BY r.created_at DESC, r.run_id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        list_params,
    ).mappings().all()

    return {
        "items": [shape_run_row(dict(row)) for row in rows],
        "total": int(total),
        "fault_count": int(fault_count),
        "normal_count": int(normal_count),
    }


def get_run_detail(db, mongo_db, *, run_id: str) -> Dict[str, Any]:
    row = db.execute(
        text(
            """
            SELECT
                r.run_id,
                r.case_id,
                r.run_name,
                r.system_id,
                r.round_no,
                r.is_fault,
                r.start_time,
                r.end_time,
                r.error_logs,
                r.critical_logs,
                r.window_count,
                r.stats_json,
                r.created_at,
                c.case_name,
                c.test_name,
                c.fault_type
            FROM runs r
            LEFT JOIN cases c ON c.case_id = r.case_id
            WHERE r.run_id = :run_id
            LIMIT 1
            """
        ),
        {"run_id": run_id},
    ).mappings().first()

    if row is None:
        raise LookupError(run_id)

    row_dict = dict(row)
    stats = parse_stats_json(row_dict.get("stats_json"))
    first_entry = mongo_db["log_entries"].find_one(
        {"run_id": run_id},
        {"_id": 0, "subsystem": 1},
    ) or {}
    entry_count = mongo_db["log_entries"].count_documents({"run_id": run_id})
    windows_total = mongo_db["log_windows"].count_documents({"run_id": run_id})

    return {
        "run_id": row_dict["run_id"],
        "case_id": row_dict.get("case_id"),
        "run_name": row_dict.get("run_name"),
        "case_name": row_dict.get("case_name"),
        "test_name": row_dict.get("test_name"),
        "system_id": row_dict.get("system_id"),
        "subsystem": first_entry.get("subsystem"),
        "round_no": row_dict.get("round_no"),
        "fault_type": row_dict.get("fault_type"),
        "fault_status": "fault" if row_dict.get("is_fault") else "normal",
        "start_time": row_dict.get("start_time"),
        "end_time": row_dict.get("end_time"),
        "parsed_lines": int(stats.get("parsed_lines") or 0),
        "total_lines": int(stats.get("total_lines") or stats.get("parsed_lines") or 0),
        "error_logs": int(row_dict.get("error_logs") or 0),
        "critical_logs": int(row_dict.get("critical_logs") or 0),
        "window_count": int(row_dict.get("window_count") or 0),
        "entry_count": int(entry_count),
        "windows_total": int(windows_total),
        "level_distribution": stats.get("level_distribution") or {},
        "top_modules": stats.get("top_modules") or [],
        "created_at": row_dict.get("created_at"),
    }


def list_run_entries(
    mongo_db,
    *,
    run_id: str,
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    query = {"run_id": run_id}
    total = mongo_db["log_entries"].count_documents(query)
    cursor = (
        mongo_db["log_entries"]
        .find(
            query,
            {
                "_id": 0,
                "timestamp": 1,
                "level": 1,
                "module": 1,
                "message": 1,
                "raw_line": 1,
                "file_path": 1,
                "line_no": 1,
            },
        )
        .sort([("timestamp", 1), ("line_no", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [
            {
                "timestamp": doc.get("timestamp"),
                "level": doc.get("level"),
                "module": doc.get("module"),
                "message": doc.get("message") or doc.get("raw_line") or "",
                "file_path": doc.get("file_path"),
                "line_no": doc.get("line_no"),
            }
            for doc in cursor
        ],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


def list_run_windows(
    mongo_db,
    *,
    run_id: str,
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    query = {"run_id": run_id}
    total = mongo_db["log_windows"].count_documents(query)
    cursor = (
        mongo_db["log_windows"]
        .find(
            query,
            {
                "_id": 0,
                "window_id": 1,
                "start_time": 1,
                "end_time": 1,
                "strategy": 1,
                "stats": 1,
                "key_events": 1,
                "text": 1,
            },
        )
        .sort([("start_time", 1), ("window_id", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [shape_window_doc(doc) for doc in cursor],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


# ════════════════════════════════════════════════════════════════════════════
# 「日志列表」CRUD：编辑（PATCH） + 级联删除
# ════════════════════════════════════════════════════════════════════════════

# 删 run 时要级联清理的文档集合
_DOC_COLLECTIONS_BY_RUN = ("log_entries", "log_windows", "log_analysis_results")


def update_run(db, *, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """编辑一条 run 的元信息。

    payload 中只处理以下键，其它键被忽略：
        - fault_type_id : Optional[int]   传 0 / 负数 / None 视为"清空"标签
        - is_fault      : Optional[bool]
        - description   : Optional[str]   写入 cases.description
        - run_name      : Optional[str]   只改本条 run

    case 维度的字段（fault_type_id / is_fault / description）会同时更新该 run
    所属的 cases 行，因为同一 case 下所有 run 共享标签。

    返回 {run_id, case_id, updated_fields}；run 不存在时抛 LookupError。
    """
    from app.models.run import Run
    from app.models.case import Case
    from app.models.fault_type import FaultType

    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise LookupError(run_id)

    case: Optional[Case] = None
    if run.case_id:
        case = db.query(Case).filter(Case.case_id == run.case_id).first()

    updated: list[str] = []

    # ── case 级字段（同步到 cases 表 + 当前 run 的 is_fault） ──
    if "fault_type_id" in payload:
        ft_id = payload.get("fault_type_id")
        # 标签为空：传 None / 0 / 负数都视为清除
        if ft_id is None or (isinstance(ft_id, int) and ft_id <= 0):
            if case is not None:
                case.fault_type_id = None
                case.fault_type_label = None
                updated.append("fault_type_id")
        else:
            ft = db.query(FaultType).filter(FaultType.id == ft_id).first()
            if ft is None:
                raise ValueError(f"fault_type_id {ft_id} 不存在")
            if case is not None:
                case.fault_type_id = ft.id
                case.fault_type_label = ft.name
                # 打了故障类型 → 默认认定为故障样本（如果用户没显式传 is_fault）
                if "is_fault" not in payload and case.is_fault is not True:
                    case.is_fault = True
                    run.is_fault = True
                updated.append("fault_type_id")

    if "is_fault" in payload and payload["is_fault"] is not None:
        flag = bool(payload["is_fault"])
        run.is_fault = flag
        if case is not None:
            case.is_fault = flag
        updated.append("is_fault")

    if "description" in payload and payload["description"] is not None:
        if case is not None:
            case.description = payload["description"]
            updated.append("description")

    # ── run 级字段 ──
    if "run_name" in payload and payload["run_name"] is not None:
        run.run_name = payload["run_name"]
        updated.append("run_name")

    if updated:
        from datetime import datetime as _dt
        # 编辑也算"动了一下"，刷新 run.created_at 让它在按时间排序的列表里靠前
        run.created_at = _dt.utcnow()
        db.commit()

    return {
        "run_id": run.run_id,
        "case_id": run.case_id,
        "updated_fields": updated,
    }


def delete_run(
    db,
    mongo_db,
    *,
    run_id: str,
    cleanup_empty_case: bool = True,
) -> Dict[str, Any]:
    """级联删除一条 run。

    步骤：
        1. 删 PG-Mongo 兼容层里所有以 run_id 关联的 doc
           (log_entries / log_windows / log_analysis_results)
        2. 删 Chroma 中 metadata.run_id == run_id 的所有向量
        3. 删 runs 表行
        4. （可选）该 case 名下没有任何 run 了，连 case 一并删

    返回每一步的统计；run 不存在时抛 LookupError。
    """
    from app.models.run import Run
    from app.models.case import Case

    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise LookupError(run_id)

    case_id = run.case_id

    # 1. 文档存储（PG mongo_compat 集合）
    deleted_entries = 0
    deleted_windows = 0
    deleted_analyses = 0
    for coll, var in (
        ("log_entries", "deleted_entries"),
        ("log_windows", "deleted_windows"),
        ("log_analysis_results", "deleted_analyses"),
    ):
        try:
            res = mongo_db[coll].delete_many({"run_id": run_id})
            n = getattr(res, "deleted_count", 0) or 0
            if coll == "log_entries":
                deleted_entries = n
            elif coll == "log_windows":
                deleted_windows = n
            else:
                deleted_analyses = n
        except Exception:
            pass

    # 2. Chroma：按 metadata.run_id 删除（如果索引时携带了 run_id 的 doc）
    deleted_chroma = 0
    try:
        from app.services.vector_store import get_vector_store

        vs = get_vector_store()
        got = vs.collection.get(where={"run_id": run_id}, include=["metadatas"])
        ids = got.get("ids") or []
        if ids:
            vs.collection.delete(ids=ids)
            deleted_chroma = len(ids)
    except Exception:
        # 向量库失败不阻塞主删除流程，但记录到返回结果里
        deleted_chroma = -1

    # 3. runs 表
    db.delete(run)

    # 4. 可选：清理孤儿 case
    deleted_case = False
    if cleanup_empty_case and case_id:
        # 注意：此时 run 已被 db.delete()，但还没 commit；要在 commit 前判断
        db.flush()
        remaining = (
            db.query(Run)
            .filter(Run.case_id == case_id)
            .count()
        )
        if remaining == 0:
            case = db.query(Case).filter(Case.case_id == case_id).first()
            if case is not None:
                db.delete(case)
                deleted_case = True

    db.commit()

    return {
        "run_id": run_id,
        "deleted_log_entries": deleted_entries,
        "deleted_log_windows": deleted_windows,
        "deleted_chroma_docs": max(deleted_chroma, 0),
        "deleted_case": deleted_case,
    }


def batch_delete_runs(
    db,
    mongo_db,
    *,
    run_ids: list,
    cleanup_empty_cases: bool = True,
) -> Dict[str, Any]:
    """批量删除：内部循环调 delete_run，逐条事务，失败不影响其他条目。"""
    items = []
    not_found: list[str] = []
    for rid in run_ids:
        try:
            res = delete_run(
                db,
                mongo_db,
                run_id=rid,
                cleanup_empty_case=cleanup_empty_cases,
            )
            items.append(res)
        except LookupError:
            not_found.append(rid)
        except Exception as exc:  # noqa: BLE001
            # 单条失败也算"未找到/未删除"，但记录到 message
            not_found.append(rid)
            items.append({
                "run_id": rid,
                "deleted_log_entries": 0,
                "deleted_log_windows": 0,
                "deleted_chroma_docs": 0,
                "deleted_case": False,
                "message": f"删除失败：{exc}",
            })

    return {
        "requested": len(run_ids),
        "deleted": len(items) - len([i for i in items if i.get("message")]),
        "not_found": not_found,
        "items": items,
    }

