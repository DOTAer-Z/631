"""
embedding.py — Embedding 服务的 HTTP 端点

暴露三个端点：
  GET  /api/v1/embedding/info         本地+远程 embedding 服务的状态摘要
  POST /api/v1/embedding/encode       用本地模型编码 N 条文本，返回向量列表
  POST /api/v1/embedding/encode/remote  用远程 OpenAI 兼容服务编码（如已配置）

主要用途：
- 部署后立即验证 embedding 是否可用（不必走完整的 RAG 流水线）
- 给前端「知识图谱 / 知识库管理」之后的 RAG 模块铺路
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.embedding_client import EmbeddingError, get_embedding_client
from app.services.local_embedding_service import (
    LocalEmbeddingError,
    get_local_embedding_service,
)

router = APIRouter(prefix="/embedding", tags=["Embedding"])


class EncodeRequest(BaseModel):
    texts: List[str] = Field(..., min_length=1, description="待编码的文本列表")
    normalize: bool = Field(True, description="是否 L2 归一化（用于余弦相似度）")


class EncodeResponse(BaseModel):
    model: str
    dim: int
    count: int
    vectors: List[List[float]]


@router.get("/info")
def embedding_info():
    """返回本地与远程 embedding 服务的当前状态。"""
    local = get_local_embedding_service()
    remote = get_embedding_client()
    return {
        "local": local.info(),
        "remote": {
            "configured": remote.is_configured(),
            "base_url": remote.base_url,
            "model": remote.model,
        },
    }


@router.post("/encode", response_model=EncodeResponse)
def encode_local(req: EncodeRequest):
    """用本地 sentence-transformers 模型编码文本。"""
    svc = get_local_embedding_service()
    try:
        arr = svc.encode(req.texts, normalize=req.normalize)
    except LocalEmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EncodeResponse(
        model=svc.info().get("model_path", ""),
        dim=int(arr.shape[1]) if arr.size else 0,
        count=int(arr.shape[0]),
        vectors=arr.tolist(),
    )


@router.post("/encode/remote", response_model=EncodeResponse)
def encode_remote(req: EncodeRequest):
    """用远程 OpenAI 兼容服务编码文本（需先配置 EMBEDDING_API_BASE/MODEL）。"""
    client = get_embedding_client()
    if not client.is_configured():
        raise HTTPException(
            status_code=503,
            detail="远程 Embedding 未配置：请设置 EMBEDDING_API_BASE 与 EMBEDDING_MODEL。",
        )
    try:
        vecs = client.embed(req.texts)
    except EmbeddingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return EncodeResponse(
        model=client.model,
        dim=len(vecs[0]) if vecs else 0,
        count=len(vecs),
        vectors=vecs,
    )
