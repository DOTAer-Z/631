"""
vector_retrieval_service.py

职责：
- 加载离线预构建的 FAISS 向量索引（faiss.index）和对应的元数据文件（metadata.jsonl）
- 加载本地 SentenceTransformer embedding 模型（all-MiniLM-L6-v2）
- 提供 query(text, top_k) 接口：输入 log_window.text，输出 top-k 相似案例

索引说明（来自 05_build_vector_index_faiss.py）：
- 索引类型：IndexFlatIP（内积），向量在写入前已 L2 归一化
- 因此内积分数 = cosine similarity，范围 [-1, 1]，越高越相似
- metadata.jsonl 每行对应一个向量，字段包括：
    faiss_id, window_id, task_id, run_id, case_id, system_id, subsystem,
    round_no, is_fault, strategy, start_time, end_time,
    fault_type, root_cause, root_cause_type, text
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.config import settings


class VectorRetrievalService:
    """
    懒加载 FAISS 索引 + embedding 模型。
    首次调用 query() 时才真正加载，之后复用（线程安全）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index = None          # faiss.Index
        self._metadata: List[Dict[str, Any]] = []
        self._model = None          # SentenceTransformer
        self._loaded = False

    # ------------------------------------------------------------------
    # 内部：加载
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """加载 FAISS index、metadata、embedding 模型（幂等）。"""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            try:
                import faiss
            except ImportError as exc:
                raise RuntimeError(
                    "faiss-cpu is not installed. "
                    "Add `faiss-cpu` to requirements.txt and rebuild."
                ) from exc

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed. "
                    "Add it to requirements.txt and rebuild."
                ) from exc

            index_path = Path(settings.VECTOR_INDEX_PATH)
            metadata_path = Path(settings.VECTOR_METADATA_PATH)
            model_path = settings.LOCAL_MODEL_PATH

            if not index_path.exists():
                raise FileNotFoundError(
                    f"FAISS index not found: {index_path}. "
                    "Run 05_build_vector_index_faiss.py first and mount the output directory."
                )
            if not metadata_path.exists():
                raise FileNotFoundError(
                    f"FAISS metadata not found: {metadata_path}."
                )

            self._index = faiss.read_index(str(index_path))

            with metadata_path.open("r", encoding="utf-8") as f:
                self._metadata = [json.loads(line) for line in f if line.strip()]

            if self._index.ntotal != len(self._metadata):
                raise ValueError(
                    f"FAISS index has {self._index.ntotal} vectors "
                    f"but metadata has {len(self._metadata)} entries. "
                    "Index and metadata may be out of sync."
                )

            self._model = SentenceTransformer(
                model_path,
                device=settings.EMBEDDING_DEVICE,
            )

            self._loaded = True

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def query(
        self,
        text: str,
        top_k: int = 5,
        only_fault: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        检索与输入文本最相似的 log window 案例。

        参数
        ----
        text       : 待查询文本（通常是 log_window.text）
        top_k      : 返回前 k 个结果
        only_fault : 若为 True，仅返回 is_fault=True 的案例

        返回
        ----
        List[dict]，每个元素为 metadata 字段 + 额外字段：
            similarity  : float，cosine 相似度（越接近 1 越相似）
            rank        : int，排名（从 1 开始）
        """
        self._load()

        vec = self._model.encode(
            [text],
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype("float32")

        # IndexFlatIP：搜索结果已按分数降序排列
        fetch_k = top_k * 3 if only_fault else top_k
        scores, indices = self._index.search(vec, fetch_k)

        results: List[Dict[str, Any]] = []
        rank = 0
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            meta = dict(self._metadata[idx])
            if only_fault and not meta.get("is_fault"):
                continue
            rank += 1
            meta["similarity"] = float(score)
            meta["rank"] = rank
            results.append(meta)
            if len(results) >= top_k:
                break

        return results

    def health_check(self) -> Dict[str, Any]:
        """返回索引基本信息，用于调试或 /health 端点扩展。"""
        try:
            self._load()
            return {
                "status": "ok",
                "index_total": self._index.ntotal,
                "metadata_total": len(self._metadata),
                "embedding_model": settings.LOCAL_MODEL_PATH,
                "embedding_device": settings.EMBEDDING_DEVICE,
                "embedding_dim": settings.EMBEDDING_DIM,
            }
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}


# 全局单例
vector_retrieval_service = VectorRetrievalService()
