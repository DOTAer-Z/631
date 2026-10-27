from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import init_logging

settings = get_settings()

init_logging()

app = FastAPI(title=settings.app_name, version=settings.app_version)
# CORS:与主系统后端保持一致(allow_origins=["*"])。
#
# 为什么必须放开:wujie 微前端把子应用 JS 跑在**门户 origin** 的沙箱 iframe 里
# (门户 172.30.6.59:30082),而标注 API 在集成入口(172.30.6.63:30080),因此
# 浏览器视之为跨域。请求带自定义头 X-Token → 必然触发 OPTIONS 预检;若 origin
# 不在白名单,Starlette 的 CORSMiddleware 对预检直接返回 400,标注全部接口被拦。
# 原白名单只有 localhost:5173(本地 dev),集成环境必然落空。
#
# 为什么用 "*" 而不是白名单:门户/入口的 IP 与 NodePort 都可能变更,写死 origin
# 会在换端口时再次静默失效。鉴权靠 X-Token(不依赖 Cookie),故 allow_credentials
# 保持 False —— 浏览器也不允许 "*" 与 credentials 并用。
#
# 注意:CORS 头只能有**一个**来源。此处由后端统一签发,nginx 的 /annotate-api/
# 与 /api/ 一律**不得**再 add_header Access-Control-*,否则响应出现两个
# Access-Control-Allow-Origin,浏览器直接拒绝。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)


@app.exception_handler(HTTPException)
async def _handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": "HTTP_ERROR",
            "message": detail,
            "detail": detail,
        },
    )

app.include_router(api_router, prefix=settings.api_v1_prefix)
