from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.model_api_config import ModelApiConfig
from app.schemas.model_api_config import (
    ModelApiConfigCreate,
    ModelApiConfigOut,
    ModelApiConfigUpdate,
)
from app.services.api_key_cipher import ApiKeyCipher


class ModelApiConfigNotFound(LookupError):
    pass


class ModelApiConfigConflict(RuntimeError):
    pass


class ModelApiConfigStorageError(RuntimeError):
    def __init__(self):
        super().__init__("model API configuration storage is unavailable")


class ActiveModelApiConfigDeleteForbidden(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelApiConnectionTestSnapshot:
    base_url: str
    model: str
    api_key_ciphertext: str = field(repr=False)
    timeout_seconds: int


class ModelApiConfigService:
    def __init__(self, db: Session, cipher: ApiKeyCipher):
        self.db = db
        self.cipher = cipher

    def list_configs(self) -> list[ModelApiConfig]:
        return (
            self.db.query(ModelApiConfig)
            .order_by(ModelApiConfig.created_at.desc())
            .all()
        )

    def get_config(self, config_id: int) -> ModelApiConfig:
        row = (
            self.db.query(ModelApiConfig)
            .filter(ModelApiConfig.id == config_id)
            .first()
        )
        if row is None:
            raise ModelApiConfigNotFound("model API configuration not found")
        return row

    def get_connection_test_snapshot(
        self, config_id: int
    ) -> ModelApiConnectionTestSnapshot:
        snapshot = None
        try:
            row = (
                self.db.query(ModelApiConfig)
                .filter(ModelApiConfig.id == config_id)
                .first()
            )
            if row is not None:
                snapshot = ModelApiConnectionTestSnapshot(
                    base_url=row.base_url,
                    model=row.model,
                    api_key_ciphertext=row.api_key_ciphertext,
                    timeout_seconds=row.timeout_seconds,
                )
        finally:
            self.db.rollback()

        if snapshot is None:
            raise ModelApiConfigNotFound("model API configuration not found")
        return snapshot

    def create_config(self, payload: ModelApiConfigCreate) -> ModelApiConfig:
        row = ModelApiConfig(
            name=payload.name,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            api_key_ciphertext=self.cipher.encrypt(payload.api_key),
            timeout_seconds=payload.timeout_seconds,
            max_output_tokens=payload.max_output_tokens,
            is_active=False,
        )
        self.db.add(row)
        self._commit_or_conflict("configuration name already exists")
        self.db.refresh(row)
        return row

    def update_config(
        self, config_id: int, payload: ModelApiConfigUpdate
    ) -> ModelApiConfig:
        row = self.get_config(config_id)
        changes = payload.model_dump(exclude_unset=True)
        api_key = changes.pop("api_key", None)
        ciphertext = self.cipher.encrypt(api_key) if api_key is not None else None
        for field, value in changes.items():
            setattr(row, field, value)
        if ciphertext is not None:
            row.api_key_ciphertext = ciphertext
        self._commit_or_conflict("configuration name already exists")
        self.db.refresh(row)
        return row

    def activate_config(self, config_id: int) -> ModelApiConfig:
        try:
            rows = self.db.query(ModelApiConfig).with_for_update().all()
            target = next((row for row in rows if row.id == config_id), None)
            if target is None:
                raise ModelApiConfigNotFound("model API configuration not found")

            self.cipher.decrypt(target.api_key_ciphertext)
            for row in rows:
                if row.is_active:
                    row.is_active = False
            self._flush_or_conflict("another configuration was activated concurrently")
            target.is_active = True
            self._commit_or_conflict("another configuration was activated concurrently")
            self.db.refresh(target)
            return target
        except (ModelApiConfigConflict, ModelApiConfigStorageError):
            raise
        except Exception:
            self.db.rollback()
            raise

    def delete_config(self, config_id: int) -> None:
        try:
            row = (
                self.db.query(ModelApiConfig)
                .filter(ModelApiConfig.id == config_id)
                .with_for_update()
                .first()
            )
            if row is None:
                raise ModelApiConfigNotFound("model API configuration not found")
            if row.is_active:
                raise ActiveModelApiConfigDeleteForbidden(
                    "active model API configuration cannot be deleted"
                )
            self.db.delete(row)
            self._commit_or_conflict("configuration could not be deleted")
        except (ModelApiConfigConflict, ModelApiConfigStorageError):
            raise
        except Exception:
            self.db.rollback()
            raise

    def to_out(self, row: ModelApiConfig) -> ModelApiConfigOut:
        return ModelApiConfigOut(
            id=row.id,
            name=row.name,
            provider=row.provider,
            base_url=row.base_url,
            model=row.model,
            timeout_seconds=row.timeout_seconds,
            max_output_tokens=row.max_output_tokens,
            api_key_configured=bool(row.api_key_ciphertext),
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _flush_or_conflict(self, message: str) -> None:
        domain_error = None
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            domain_error = ModelApiConfigConflict(message)
        except SQLAlchemyError:
            self.db.rollback()
            domain_error = ModelApiConfigStorageError()
        if domain_error is not None:
            raise domain_error from None

    def _commit_or_conflict(self, message: str) -> None:
        domain_error = None
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            domain_error = ModelApiConfigConflict(message)
        except SQLAlchemyError:
            self.db.rollback()
            domain_error = ModelApiConfigStorageError()
        if domain_error is not None:
            raise domain_error from None
