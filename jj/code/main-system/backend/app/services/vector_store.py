"""
vector_store.py — Chroma 向量库封装（本地 PersistentClient + 本地 BGE 嵌入）

历史背景
-------
- 旧实现走 `chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)` + DashScope
  在线嵌入。在没有外部 Chroma 服务、也没有 DashScope key 的部署里，初始化阶段
  会向 `localhost:8000`（其实是 backend 自身的 FastAPI）发请求,404 后抛
  `Could not connect to tenant default_tenant`,导致 `/logs/batch-index` 等
  接口直接 500。
- 现在切换到 **本地嵌入向量库**：
  - Chroma 用 PersistentClient,落盘路径 `settings.CHROMA_PERSIST_PATH`
  - 嵌入函数复用项目自带的 `LocalEmbeddingService`(bge-small-zh-v1.5)
  - 不再依赖任何外部网络服务,离线环境直接可用

公共 API 保持不变：
    add_document(doc_id, text, metadata)
    query(query_text, n_results=5) -> dict
    delete_document(doc_id)
    count() -> int
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

from app.config import settings
from app.services.local_embedding_service import (
    LocalEmbeddingError,
    get_local_embedding_service,
)

logger = logging.getLogger(__name__)


class LocalBgeEmbeddingFunction(EmbeddingFunction):
    """ChromaDB 兼容嵌入函数,底层走本地 SentenceTransformer (bge-small-zh-v1.5)。"""

    def __init__(self) -> None:
        self._svc = get_local_embedding_service()

    def __call__(self, input: Documents) -> Embeddings:
        # Chroma 0.5 的 EmbeddingFunction 协议：传入 List[str],返回 List[List[float]]
        if not input:
            return []
        try:
            arr = self._svc.encode(list(input), normalize=True)
        except LocalEmbeddingError as exc:
            raise RuntimeError(f"本地嵌入失败: {exc}") from exc
        return arr.tolist()


# ─────────────────────────────────────────────────────────────────────────────
# 进程内单例：PersistentClient 加载一次后复用,避免每次 new VectorStoreService()
# 都重新打开数据库文件 / 重新加载嵌入模型
# ─────────────────────────────────────────────────────────────────────────────
_singleton: Optional["VectorStoreService"] = None
_singleton_lock = threading.Lock()


class VectorStoreService:
    COLLECTION_NAME = "fault_logs"

    def __init__(self) -> None:
        persist_path = Path(
            getattr(settings, "CHROMA_PERSIST_PATH", "/app/outputs/vector_store/chroma")
        )
        persist_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"初始化 Chroma PersistentClient: path={persist_path}")
        self.client = chromadb.PersistentClient(path=str(persist_path))
        self.ef = LocalBgeEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # 公开接口(沿用原签名)
    # ------------------------------------------------------------------
    def add_document(self, doc_id: str, text: str, metadata: dict) -> None:
        self.collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def add_documents(
        self,
        doc_ids: List[str],
        texts: List[str],
        metadatas: List[dict],
    ) -> None:
        """批量写入(一次嵌入多条,比逐条 add 快)。"""
        if not doc_ids:
            return
        self.collection.add(ids=doc_ids, documents=texts, metadatas=metadatas)

    def query(self, query_text: str, n_results: int = 5) -> dict:
        count = self.collection.count()
        if count == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        actual_n = min(n_results, count)
        return self.collection.query(
            query_texts=[query_text],
            n_results=actual_n,
            include=["documents", "metadatas", "distances"],
        )

    def delete_document(self, doc_id: str) -> None:
        self.collection.delete(ids=[doc_id])

    def count(self) -> int:
        return self.collection.count()


def get_vector_store() -> VectorStoreService:
    """全局复用的 VectorStoreService,第一次调用时初始化,线程安全。"""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = VectorStoreService()
    return _singleton
