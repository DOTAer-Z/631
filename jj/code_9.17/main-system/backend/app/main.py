from datetime import datetime
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import knowledge_base, diagnosis, prediction, graph, overview, log_analysis, data_processing, data_import, internal_llm_gateway, model_api_config, model_training, model_training_adapters
from app.api.v1 import test_data_flow
from app.api.v1 import log_pipeline
from app.api.v1 import embedding as embedding_api
from app.config import settings
from app.database import SessionLocal
from app.services.adapter_import_service import reconcile_adapter_imports
from app.services.log_parse_task_service import recover_interrupted_tasks

logger = logging.getLogger(__name__)

app = FastAPI(title="故障定位系统 API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(RequestValidationError)
async def redact_api_key_validation_errors(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = []
    for error in exc.errors():
        sanitized_error = dict(error)
        is_internal_llm = request.url.path.startswith("/api/v1/internal/llm/")
        if is_internal_llm or "api_key" in sanitized_error.get("loc", ()):
            sanitized_error.pop("input", None)
        errors.append(sanitized_error)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge_base.router, prefix="/api/v1", tags=["知识库管理"])
app.include_router(diagnosis.router, prefix="/api/v1", tags=["故障诊断"])
app.include_router(prediction.router, prefix="/api/v1", tags=["故障预测"])
app.include_router(graph.router, prefix="/api/v1", tags=["知识图谱"])
app.include_router(overview.router, prefix="/api/v1", tags=["系统概况"])
app.include_router(log_analysis.router, prefix="/api/v1", tags=["日志分析"])
app.include_router(data_processing.router, prefix="/api/v1", tags=["数据处理"])
app.include_router(data_import.router, prefix="/api/v1", tags=["数据导入"])
app.include_router(test_data_flow.router, prefix="/api", tags=["调试"])
app.include_router(log_pipeline.router,  prefix="/api", tags=["调试"])
app.include_router(embedding_api.router, prefix="/api/v1", tags=["Embedding"])
app.include_router(
    internal_llm_gateway.router,
    prefix="/api/v1",
    tags=["内部大模型网关"],
)
app.include_router(model_api_config.router, prefix="/api/v1", tags=["模型 API 管理"])
app.include_router(model_training.router, prefix="/api/v1", tags=["模型训练"])
app.include_router(model_training_adapters.router, prefix="/api/v1", tags=["训练 Adapter"])


@app.on_event("startup")
def recover_adapter_imports() -> None:
    with SessionLocal() as db:
        try:
            reconcile_adapter_imports(
                db,
                settings.TRAINING_OUTPUT_ROOT,
                datetime.utcnow(),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.error("adapter_import_recovery_failed")


@app.on_event("startup")
def recover_log_parse_tasks() -> None:
    with SessionLocal() as db:
        recover_interrupted_tasks(db)


@app.on_event("shutdown")
def shutdown_log_parse_runner() -> None:
    log_analysis.log_parse_task_runner.shutdown(wait=False)


@app.get("/health")
def health_check():
    return {"status": "ok"}
