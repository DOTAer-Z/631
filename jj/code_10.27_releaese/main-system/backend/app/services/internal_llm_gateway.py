import logging
import time

from openai import APITimeoutError

from app.schemas.internal_llm_gateway import (
    InternalLLMChatRequest,
    InternalLLMChatResponse,
)
from app.services.llm_runtime_config import (
    LLMRuntimeConfigProvider,
    llm_runtime_config_provider,
)


logger = logging.getLogger(__name__)


class ModelApiNotConfigured(RuntimeError):
    pass


class ModelApiGatewayTimedOut(RuntimeError):
    pass


class ModelApiGatewayUnavailable(RuntimeError):
    pass


class InternalLLMGatewayService:
    def __init__(
        self,
        runtime_provider: LLMRuntimeConfigProvider = llm_runtime_config_provider,
    ) -> None:
        self.runtime_provider = runtime_provider

    def chat(self, request: InternalLLMChatRequest) -> InternalLLMChatResponse:
        started = time.perf_counter()
        runtime = None
        status = "failed"
        model = "unavailable"
        translated_error = None
        try:
            runtime = self.runtime_provider.resolve(
                # 页面「模型 API 管理」配置(DB active 行)优先；页面未配时回退环境变量。
                # 这样部署时只需填 LLM_BASE_URL/LLM_MODEL/LLM_API_KEY(在 01-config 的
                # ENABLE_LLM=True 下)，主系统预测预警 + 标注子系统的大模型推荐都能用；
                # 一旦页面启用了 active 行，则以页面为准(最高级不变)。
                allow_environment_fallback=True
            )
            if runtime is None:
                raise ModelApiNotConfigured("大模型 API 未配置")
            model = runtime.model
            client = runtime.client.with_options(max_retries=0)
            payload = {
                "model": runtime.model,
                "messages": [message.model_dump() for message in request.messages],
                "temperature": request.temperature,
                "max_tokens": runtime.max_output_tokens,
            }
            if request.json_mode:
                payload["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**payload)
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ModelApiGatewayUnavailable("大模型 API 返回无效响应")
            content = getattr(choices[0].message, "content", None) or ""
            content = runtime.redact(content) or ""
            if not content:
                raise ModelApiGatewayUnavailable("大模型 API 返回空响应")
            status = "succeeded"
            return InternalLLMChatResponse(content=content, model=runtime.model)
        except ModelApiNotConfigured:
            raise
        except (APITimeoutError, TimeoutError):
            translated_error = ModelApiGatewayTimedOut("大模型 API 请求超时")
        except ModelApiGatewayUnavailable:
            raise
        except Exception:
            translated_error = ModelApiGatewayUnavailable("大模型 API 请求失败")
        finally:
            logger.info(
                "internal_llm_gateway purpose=%s model=%s status=%s elapsed_ms=%d",
                request.purpose,
                model,
                status,
                round((time.perf_counter() - started) * 1000),
            )
        raise translated_error
