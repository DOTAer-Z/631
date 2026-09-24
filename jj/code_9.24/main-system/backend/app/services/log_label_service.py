"""log_label_service.py — 按 run_id 反查「上传文件名 + 上传时间」。

用于故障诊断 / 日志分析 / 预测预警三页统一显示「文件名 · 上传时间」而非长 run_id。
数据源：log_uploads（mongo_compat shim）。找不到的 run_id 不返回（前端回退显示 run_id）。
"""
from __future__ import annotations

from typing import Dict, List

from app.database import get_mongo_db


def resolve_upload_labels(run_ids: List[str]) -> Dict[str, dict]:
    """返回 {run_id: {"filename": str|None, "uploaded_at": iso|None}}（仅命中的 run_id）。"""
    ids = [r for r in (run_ids or []) if r]
    if not ids:
        return {}
    try:
        mongo_db = get_mongo_db()
        docs = mongo_db["log_uploads"].find(
            {"run_id": {"$in": ids}},
            {"run_id": 1, "filename": 1, "ingested_at": 1, "_id": 0},
        )
        out: Dict[str, dict] = {}
        for d in docs:
            rid = d.get("run_id")
            if not rid:
                continue
            ts = d.get("ingested_at")
            out[rid] = {
                "filename": d.get("filename"),
                "uploaded_at": ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None),
            }
        return out
    except Exception:
        return {}
