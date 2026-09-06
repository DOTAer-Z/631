import hmac
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
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


bearer = HTTPBearer(auto_error=False)


def validate_internal_gateway_token(
    credentials: HTTPAuthorizationCredentials | None,
    expected: str,
) -> None:
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "llm_gateway_not_configured",
                "message": "内部大模型网关未配置",
            },
        )
    supplied = credentials.credentials if credentials is not None else ""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_internal_token",
                "message": "内部服务认证失败",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


class InternalGatewayRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        route_handler = super().get_route_handler()

        async def authenticated_route_handler(request: Request) -> Response:
            credentials = await bearer(request)
            validate_internal_gateway_token(
                credentials, settings.INTERNAL_LLM_GATEWAY_TOKEN
            )
            return await route_handler(request)

        return authenticated_route_handler


router = APIRouter(prefix="/internal/llm", route_class=InternalGatewayRoute)


def require_internal_gateway_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    validate_internal_gateway_token(credentials, settings.INTERNAL_LLM_GATEWAY_TOKEN)


def get_internal_llm_gateway_service() -> InternalLLMGatewayService:
    return InternalLLMGatewayService()


@router.post("/chat", response_model=InternalLLMChatResponse)
def chat(
    request: InternalLLMChatRequest,
    _: None = Depends(require_internal_gateway_token),
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
