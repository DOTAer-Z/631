"""
embedding_client.py —— OpenAI 兼容的 Embedding 客户端（预留接口）

设计目标：与大模型 LLM 客户端同风格（POST {base}/embeddings + Bearer token），
任何暴露 OpenAI 兼容 /v1/embeddings 的服务（本地 vLLM / TEI / BGE / DashScope 等）都能接。

当前状态：接口已就绪，但默认 **未配置端点**（settings.EMBEDDING_API_BASE 为空）。
填好 EMBEDDING_API_BASE / EMBEDDING_MODEL（及可选 EMBEDDING_API_KEY）后即可启用，
无需改动本文件。本文件**暂未接入**现有向量检索流程（vector_store / vector_retrieval_service），
作为后续 RAG 的统一 embedding 入口预留。
"""

from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Embedding 调用失败；调用方应优雅降级。"""


class EmbeddingClient:
    """Thin OpenAI-compatible embeddings client.

    用法：
        client = get_embedding_client()
        if client.is_configured():
            vecs = client.embed(["文本1", "文本2"])   # -> List[List[float]]
    """

    def __init__(
        self,
        *,
        base_url: Optional[str],
        api_key: Optional[str],
        model: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def embed(self, texts: List[str], *, timeout: Optional[float] = None) -> List[List[float]]:
        """对一批文本求 embedding，返回向量列表。未配置或失败抛 EmbeddingError。"""
        if not self.is_configured():
            raise EmbeddingError(
                "Embedding 未配置：请设置 EMBEDDING_API_BASE 与 EMBEDDING_MODEL。"
            )
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key or 'dummy'}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}
        try:
            resp = httpx.post(url, json=payload, headers=headers,
                              timeout=timeout or self.timeout_seconds)
            resp.raise_for_status()
            data = resp.json()
            # OpenAI 兼容：{"data":[{"embedding":[...]}, ...]}
            return [item["embedding"] for item in data["data"]]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Embedding 请求失败: {exc}") from exc


def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        base_url=settings.EMBEDDING_API_BASE,
        api_key=settings.EMBEDDING_API_KEY,
        model=settings.EMBEDDING_MODEL,
        timeout_seconds=settings.LLM_TIMEOUT,
    )
