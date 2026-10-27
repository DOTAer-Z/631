#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_build_vector_index_faiss.py
目的：从 MongoDB log_windows 读取文本，生成 embedding，并写入本地 FAISS 向量库。

输出目录结构：
  <out-dir>/
    faiss.index
    metadata.jsonl
    build_info.json

说明：
- 这是“独立向量库”版本，替代原来 MongoDB embeddings 集合。
- metadata.jsonl 保存每个向量对应的 window/task/case/run 信息，便于后续检索回源。
- 默认使用 cosine 检索：先 normalize，再用 IndexFlatIP。
- 为避免 Mongo 长时间 cursor 超时，采用按 _id 递增分页拉取，而不是长 cursor 迭代。

本次修改：
- 增加 fault_type / root_cause / root_cause_type 的读取与写入
- 兼容标签字段既可能在顶层，也可能在 metadata 子字段内
"""

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

    with metadata_path.open("w", encoding="utf-8") as meta_f:
        batch_no = 0

        for batch_docs in fetch_windows_batch(col, q, args.mongo_read_batch):
            batch_no += 1
            print(f"[INFO] reading batch={batch_no} size={len(batch_docs)}")

            keep_docs = [d for d in batch_docs if (d.get("text") or "").strip()]
            if not keep_docs:
                print(f"[INFO] batch={batch_no} skipped (all empty text)")
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
                }
                meta_f.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                written += 1

            print(
                f"[INFO] batch={batch_no} indexed={len(keep_docs)} total_written={written} "
                f"fault_type_non_null={label_stats['fault_type_non_null']} "
                f"root_cause_non_null={label_stats['root_cause_non_null']} "
                f"root_cause_type_non_null={label_stats['root_cause_type_non_null']}"
            )

    if written == 0:
        mongo_client.close()
        die("No vectors written. Please check whether log_windows.text is empty.")

    faiss.write_index(index, str(index_path))

    loaded_index = faiss.read_index(str(index_path))
    print(f"[INFO] Reloaded FAISS index: ntotal={loaded_index.ntotal}")

    build_info = {
        "built_at": datetime.utcnow().isoformat() + "Z",
        "model_name": args.model_name,
        "model_path": args.model_path,
        "dim": args.dim,
        "total_vectors": written,
        "metric": "cosine_via_inner_product",
        "index_type": "IndexFlatIP",
        "query": q,
        "label_stats": label_stats,
        "files": {
            "index": str(index_path),
            "metadata": str(metadata_path),
        },
    }

    info_path.write_text(
        json.dumps(build_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    mongo_client.close()
    print(f"[OK] FAISS index built: vectors={written} out_dir={out_dir}")
    print(f"[OK] label_stats={label_stats}")


if __name__ == "__main__":
    main()
