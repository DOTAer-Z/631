"""
local_embedding_service.py — 本地 SentenceTransformer Embedding 服务

设计目标：
- 跟 vector_retrieval_service 解耦：不依赖 FAISS 索引文件，加载即可用
- 懒加载 + 线程安全：首次调用 encode() 时才把模型读进内存
- 默认模型：BAAI/bge-small-zh-v1.5（中文场景），权重已在镜像构建期 COPY 到
  /models/bge-small-zh-v1.5（见 backend/Dockerfile + models/ 目录）
- 自动 L2 归一化（normalize_embeddings=True），方便后续做内积=余弦相似度

启用条件：settings.ENABLE_EMBEDDING=True 且 settings.LOCAL_MODEL_PATH 指向有效目录。

与 embedding_client.py 的关系：
- embedding_client.py 走 OpenAI 兼容 HTTP 协议（远程服务端点）
- 本文件走本地权重 + Python 进程内推理
- 业务侧若两者都配置，按"本地优先"或"远程优先"由调用方决定（见 RAG 接入文档）
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class LocalEmbeddingError(RuntimeError):
    pass


class LocalEmbeddingService:
    """懒加载本地 SentenceTransformer，进程内复用。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None  # SentenceTransformer
        self._model_path: Optional[str] = None
        self._device: str = "cpu"
        self._dim: int = 0

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    def is_enabled(self) -> bool:
        return bool(settings.ENABLE_EMBEDDING)

    def is_loaded(self) -> bool:
        return self._model is not None

    def info(self) -> dict:
        return {
            "enabled": self.is_enabled(),
            "loaded": self.is_loaded(),
            "model_path": self._model_path or settings.LOCAL_MODEL_PATH,
            "device": self._device,
            "dim": self._dim,
        }

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            if not self.is_enabled():
                raise LocalEmbeddingError(
                    "本地 Embedding 已禁用 (ENABLE_EMBEDDING=False)"
                )

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise LocalEmbeddingError(
                    "sentence-transformers 未安装，无法加载本地 Embedding。"
                ) from exc

            model_path = Path(settings.LOCAL_MODEL_PATH)
            if not model_path.exists():
                raise LocalEmbeddingError(
                    f"模型路径不存在: {model_path}。"
                    f"请确认镜像里包含 /models/bge-small-zh-v1.5 权重，"
                    f"或修改 settings.LOCAL_MODEL_PATH 指向有效目录。"
                )

            device = settings.EMBEDDING_DEVICE or "cpu"
            logger.info(f"加载本地 Embedding 模型: {model_path} (device={device})")
            model = SentenceTransformer(str(model_path), device=device)

            # 探测向量维度
            try:
                dim = model.get_sentence_embedding_dimension() or 0
            except Exception:
                dim = 0

            self._model = model
            self._model_path = str(model_path)
            self._device = device
            self._dim = dim
            logger.info(f"Embedding 模型加载完成: dim={dim}")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def encode(self, texts: List[str], *, normalize: bool = True) -> np.ndarray:
        """编码一批文本为向量矩阵 (N, dim)，默认 L2 归一化。"""
        if not texts:
            return np.zeros((0, self._dim or 1), dtype="float32")
        self._load()
        try:
            vecs = self._model.encode(
                texts,
                normalize_embeddings=normalize,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return np.asarray(vecs, dtype="float32")
        except Exception as exc:  # noqa: BLE001
            raise LocalEmbeddingError(f"Embedding 编码失败: {exc}") from exc

    def embed_one(self, text: str) -> List[float]:
        """编码单条文本，返回 List[float]（方便 JSON 序列化）。"""
        return self.encode([text])[0].tolist()


# 全局单例（首次 encode 时才真正加载模型）
_service: Optional[LocalEmbeddingService] = None
_service_lock = threading.Lock()


def get_local_embedding_service() -> LocalEmbeddingService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = LocalEmbeddingService()
    return _service
