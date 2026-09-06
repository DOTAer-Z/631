from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from pymongo import ASCENDING, MongoClient

try:
    import faiss
except Exception as e:
    faiss = None
    _faiss_err = e
else:
    _faiss_err = None

try:
    from sentence_transformers import SentenceTransformer
except Exception as e:
    SentenceTransformer = None
    _st_err = e
else:
    _st_err = None

from config import (
    MONGO_URI,
    MONGO_DB,
    ensure_dirs,
    print_config,
)

ensure_dirs()
print_config()


def die(msg: str, code: int = 1) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr)
    raise SystemExit(code)


def connect_mongo():
    client = MongoClient(MONGO_URI)
    return client, client[MONGO_DB]


def ensure_deps() -> None:
    if faiss is None:
        die(f"faiss not installed or import failed: {_faiss_err}")
    if SentenceTransformer is None:
        die(f"sentence-transformers not installed or import failed: {_st_err}")


def load_model(model_path: str, device: str):
    print(f"[INFO] Loading embedding model from: {model_path}")
    print(f"[INFO] Using device: {device}")
    return SentenceTransformer(model_path, device=device)


def get_label_field(doc: Dict[str, Any], field_name: str):
    """
    兼容两种来源：
    1. 顶层字段：doc[field_name]
    2. metadata 子字段：doc["metadata"][field_name]
    """
    if field_name in doc and doc.get(field_name) is not None:
        return doc.get(field_name)

    meta = doc.get("metadata") or {}
    if isinstance(meta, dict):
        return meta.get(field_name)

    return None


def safe_list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def classify_window(doc: Dict[str, Any]) -> str:
    """
    向量库准入规则：
    1. text 非空
    2. is_fault == True
    3. stats.error_events > 0 或 len(key_events) > 0
    """
    text = (doc.get("text") or "").strip()
    if not text:
        return "skip_empty_text"

    if not doc.get("is_fault", False):
        return "skip_non_fault"

    stats = doc.get("stats") or {}
    error_events = int(stats.get("error_events", 0) or 0)
    key_event_count = safe_list_len(doc.get("key_events"))

    if error_events == 0 and key_event_count == 0:
        return "skip_quiet_fault"

    return "keep"


def fetch_windows_batch(col, query: Dict, batch_size: int):
    """
    按 _id 递增分页拉取，避免长时间持有 Mongo cursor 导致 CursorNotFound。
    每次只取一页，处理完再查下一页。
    """
    projection = {
        "_id": 1,
        "window_id": 1,
        "task_id": 1,
        "run_id": 1,
        "case_id": 1,
        "system_id": 1,
        "subsystem": 1,
        "round_no": 1,
        "is_fault": 1,
        "strategy": 1,
        "text": 1,
        "start_time": 1,
        "end_time": 1,
        "key_events": 1,
        "stats": 1,
        # 新增：标签字段
        "fault_type": 1,
        "root_cause": 1,
        "root_cause_type": 1,
        # 兼容 metadata 嵌套
        "metadata": 1,
    }

    last_id: Optional[object] = None

    while True:
        page_query = dict(query)
        if last_id is not None:
            page_query["_id"] = {"$gt": last_id}

        docs = list(
            col.find(page_query, projection=projection)
            .sort([("_id", ASCENDING)])
            .limit(batch_size)
        )

        if not docs:
            break

        yield docs
        last_id = docs[-1]["_id"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build local FAISS vector index from MongoDB log_windows."
    )
    ap.add_argument("--model-path", required=True, help="本地 embedding 模型目录")
    ap.add_argument("--model-name", default="all-MiniLM-L6-v2")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--batch-size", type=int, default=32, help="embedding batch size")
    ap.add_argument(
        "--mongo-read-batch",
        type=int,
        default=100,
        help="每次从 Mongo 分页读取多少条",
    )
    ap.add_argument("--only-run-id", default="")
    ap.add_argument("--only-system", default="")
    ap.add_argument("--only-subsystem", default="")
    ap.add_argument("--strategy", default="")
    args = ap.parse_args()

    ensure_deps()
    model = load_model(args.model_path, args.device)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = out_dir / "metadata.jsonl"
    index_path = out_dir / "faiss.index"
    info_path = out_dir / "build_info.json"

    mongo_client, db = connect_mongo()
    col = db["log_windows"]

    q: Dict = {}
    if args.only_run_id:
        q["run_id"] = args.only_run_id
    if args.only_system:
        q["system_id"] = args.only_system
    if args.only_subsystem:
        q["subsystem"] = args.only_subsystem
    if args.strategy:
        q["strategy"] = args.strategy

    total = col.count_documents(q)
    print(f"[INFO] matched_windows={total}")
    if total == 0:
        mongo_client.close()
        die("No windows matched query.")

    index = faiss.IndexFlatIP(args.dim)
    written = 0
    dim_checked = False

    label_stats = {
        "fault_type_non_null": 0,
        "root_cause_non_null": 0,
        "root_cause_type_non_null": 0,
    }

    filter_stats = {
        "seen": 0,
        "kept": 0,
        "skip_empty_text": 0,
        "skip_non_fault": 0,
        "skip_quiet_fault": 0,
    }

    with metadata_path.open("w", encoding="utf-8") as meta_f:
        batch_no = 0

        for batch_docs in fetch_windows_batch(col, q, args.mongo_read_batch):
            batch_no += 1
            print(f"[INFO] reading batch={batch_no} size={len(batch_docs)}")

            keep_docs = []
            for doc in batch_docs:
                filter_stats["seen"] += 1
                result = classify_window(doc)
                if result == "keep":
                    keep_docs.append(doc)
                    filter_stats["kept"] += 1
                else:
                    filter_stats[result] += 1

            if not keep_docs:
                print(
                    f"[INFO] batch={batch_no} skipped "
                    f"(seen={len(batch_docs)}, kept=0, "
                    f"skip_empty_text={filter_stats['skip_empty_text']}, "
                    f"skip_non_fault={filter_stats['skip_non_fault']}, "
                    f"skip_quiet_fault={filter_stats['skip_quiet_fault']})"
                )
                continue

            texts = [(d.get("text") or "").strip() for d in keep_docs]

            vectors = model.encode(
                texts,
                batch_size=args.batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )

            if len(vectors) != len(keep_docs):
                mongo_client.close()
                die("Embedding output size mismatch.")

            arr = np.asarray(vectors, dtype="float32")

            if arr.ndim != 2:
                mongo_client.close()
                die(f"Embedding output shape is invalid: shape={arr.shape}")

            if arr.shape[1] != args.dim:
                mongo_client.close()
                die(f"Embedding dim mismatch: expected {args.dim}, got {arr.shape[1]}")

            if not dim_checked:
                dim_checked = True
                print(f"[INFO] first embedding batch shape={arr.shape}, dtype={arr.dtype}")

            index.add(arr)

            for doc in keep_docs:
                fault_type = get_label_field(doc, "fault_type")
                root_cause = get_label_field(doc, "root_cause")
                root_cause_type = get_label_field(doc, "root_cause_type")

                if fault_type is not None:
                    label_stats["fault_type_non_null"] += 1
                if root_cause is not None:
                    label_stats["root_cause_non_null"] += 1
                if root_cause_type is not None:
                    label_stats["root_cause_type_non_null"] += 1

                metadata = {
                    "faiss_id": written,
                    "window_id": doc.get("window_id"),
                    "task_id": doc.get("task_id"),
                    "run_id": doc.get("run_id"),
                    "case_id": doc.get("case_id"),
                    "system_id": doc.get("system_id"),
                    "subsystem": doc.get("subsystem"),
                    "round_no": doc.get("round_no"),
                    "is_fault": doc.get("is_fault"),
                    "strategy": doc.get("strategy"),
                    "start_time": doc.get("start_time").isoformat() if doc.get("start_time") else None,
                    "end_time": doc.get("end_time").isoformat() if doc.get("end_time") else None,
                    "fault_type": fault_type,
                    "root_cause": root_cause,
                    "root_cause_type": root_cause_type,
                    "text": doc.get("text"),
                    "error_events": int((doc.get("stats") or {}).get("error_events", 0) or 0),
                    "key_event_count": safe_list_len(doc.get("key_events")),
                }
                meta_f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                written += 1

            print(
                f"[INFO] batch={batch_no} kept={len(keep_docs)} total_written={written} "
                f"seen={filter_stats['seen']} kept_total={filter_stats['kept']} "
                f"skip_empty_text={filter_stats['skip_empty_text']} "
                f"skip_non_fault={filter_stats['skip_non_fault']} "
                f"skip_quiet_fault={filter_stats['skip_quiet_fault']}"
            )

    if written == 0:
        mongo_client.close()
        die(
            "No valid windows were indexed after filtering. "
            "Please check strategy / query / fault-window rules."
        )

    faiss.write_index(index, str(index_path))
    print(f"[INFO] wrote index: {index_path}")

    build_info = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "mongo_uri": MONGO_URI,
        "mongo_db": MONGO_DB,
        "query": q,
        "model_name": args.model_name,
        "model_path": args.model_path,
        "device": args.device,
        "dim": args.dim,
        "batch_size": args.batch_size,
        "mongo_read_batch": args.mongo_read_batch,
        "count_vectors": written,
        "index_type": "IndexFlatIP",
        "normalized": True,
        "label_stats": label_stats,
        "filter_stats": filter_stats,
        "filter_rule": {
            "require_is_fault": True,
            "require_non_empty_text": True,
            "require_error_or_key_event": True,
            "expression": "is_fault == True AND text.strip() != '' AND (stats.error_events > 0 OR len(key_events) > 0)",
        },
        "files": {
            "faiss_index": str(index_path.name),
            "metadata_jsonl": str(metadata_path.name),
        },
    }

    info_path.write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] wrote build info: {info_path}")
    print(f"[INFO] label stats: {label_stats}")
    print(f"[INFO] filter stats: {filter_stats}")
    mongo_client.close()


if __name__ == "__main__":
    main()
