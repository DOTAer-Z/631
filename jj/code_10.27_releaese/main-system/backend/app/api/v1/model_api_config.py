from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.model_api_config import (
    ModelApiConfigCreate,
    ModelApiConfigDeleteOut,
    ModelApiConfigListOut,
    ModelApiConfigOut,
    ModelApiConfigUpdate,
    ModelApiTestResult,
)
from app.services.api_key_cipher import ApiKeyCipher, ApiKeyCipherUnavailable
from app.services.model_api_config_service import (
    ActiveModelApiConfigDeleteForbidden,
    ModelApiConfigConflict,
    ModelApiConfigNotFound,
    ModelApiConfigService,
    ModelApiConfigStorageError,
)
from app.services.model_api_connection import ModelApiConnectionTester

router = APIRouter(prefix="/model-api/configs")


def get_model_api_config_service(
    db: Session = Depends(get_db),
) -> ModelApiConfigService:
    try:
        cipher = ApiKeyCipher.from_settings()
    except ApiKeyCipherUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ModelApiConfigService(db, cipher)


def get_model_api_connection_tester(
    service: ModelApiConfigService = Depends(get_model_api_config_service),
) -> ModelApiConnectionTester:
    return ModelApiConnectionTester(service.cipher)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ModelApiConfigNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ModelApiConfigConflict, ActiveModelApiConfigDeleteForbidden)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ApiKeyCipherUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, ModelApiConfigStorageError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=ModelApiConfigListOut)
def list_configs(
    service: ModelApiConfigService = Depends(get_model_api_config_service),
):
    return ModelApiConfigListOut(
        items=[service.to_out(row) for row in service.list_configs()]
    )


@router.post(
    "", response_model=ModelApiConfigOut, status_code=status.HTTP_201_CREATED
)
def create_config(
    payload: ModelApiConfigCreate,
    service: ModelApiConfigService = Depends(get_model_api_config_service),
):
    try:
        return service.to_out(service.create_config(payload))
    except Exception as exc:
        _raise_http(exc)


@router.patch("/{config_id}", response_model=ModelApiConfigOut)
def update_config(
    config_id: int,
    payload: ModelApiConfigUpdate,
    service: ModelApiConfigService = Depends(get_model_api_config_service),
):
    try:
        return service.to_out(service.update_config(config_id, payload))
    except Exception as exc:
        _raise_http(exc)


@router.delete("/{config_id}", response_model=ModelApiConfigDeleteOut)
def delete_config(
    config_id: int,
    service: ModelApiConfigService = Depends(get_model_api_config_service),
):
    try:
        service.delete_config(config_id)
        return ModelApiConfigDeleteOut(deleted=True)
    except Exception as exc:
        _raise_http(exc)


@router.post("/{config_id}/activate", response_model=ModelApiConfigOut)
def activate_config(
    config_id: int,
    service: ModelApiConfigService = Depends(get_model_api_config_service),
):
    try:
        return service.to_out(service.activate_config(config_id))
    except Exception as exc:
        _raise_http(exc)


@router.post("/{config_id}/test", response_model=ModelApiTestResult)
def test_config(
    config_id: int,
    service: ModelApiConfigService = Depends(get_model_api_config_service),
    tester: ModelApiConnectionTester = Depends(get_model_api_connection_tester),
):
    try:
        snapshot = service.get_connection_test_snapshot(config_id)
        return tester.test(snapshot)
    except Exception as exc:
        _raise_http(exc)
