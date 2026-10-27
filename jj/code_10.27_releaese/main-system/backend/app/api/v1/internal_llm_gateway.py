from fastapi import APIRouter, Depends, HTTPException

from app.schemas.internal_llm_gateway import (
    InternalLLMChatRequest,
    InternalLLMChatResponse,
)
from app.services.internal_llm_gateway import (
    InternalLLMGatewayService,
    ModelApiGatewayTimedOut,
    ModelApiGatewayUnavailable,
    ModelApiNotConfigured,
)


# 零-yaml 部署（API 平台切换页为大模型唯一配置源）：
#
# 本网关是**集群内**端点——唯一调用方是标注子系统的后端，经 k8s 服务名
# `http://backend:8000/api/v1/internal/llm/chat` 直连，不经过对外 NodePort。
# 外部（浏览器/门户）访问由主前端 nginx 拦截：`/api/v1/internal/` 一律返回 404。
# 因此去掉共享 token 鉴权——token 是 yaml 里唯一还需要手工生成/同步的字段，
# 去掉它才能做到 k8s 部署时 LLM 相关 yaml 零填写。若未来与外网隔离收紧，
# 应通过 nginx/网络策略限制 `/internal/`，而非重新引入共享 token。
router = APIRouter(prefix="/internal/llm")


def get_internal_llm_gateway_service() -> InternalLLMGatewayService:
    return InternalLLMGatewayService()


@router.post("/chat", response_model=InternalLLMChatResponse)
def chat(
    request: InternalLLMChatRequest,
    service: InternalLLMGatewayService = Depends(get_internal_llm_gateway_service),
) -> InternalLLMChatResponse:
    try:
        return service.chat(request)
    except ModelApiNotConfigured:
        raise HTTPException(
            status_code=503,
            detail={"code": "model_api_not_configured", "message": "大模型 API 未配置"},
        ) from None
    except ModelApiGatewayTimedOut:
        raise HTTPException(
            status_code=504,
            detail={"code": "model_api_timeout", "message": "大模型 API 请求超时"},
        ) from None
    except ModelApiGatewayUnavailable:
        raise HTTPException(
            status_code=502,
            detail={"code": "model_api_unavailable", "message": "大模型 API 请求失败"},
        ) from None
