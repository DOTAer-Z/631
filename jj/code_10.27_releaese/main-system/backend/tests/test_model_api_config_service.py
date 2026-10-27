import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.models.model_api_config import ModelApiConfig
from app.schemas.model_api_config import ModelApiConfigCreate, ModelApiConfigUpdate
from app.services.api_key_cipher import ApiKeyCipher, ApiKeyCipherUnavailable
from app.services.model_api_config_service import (
    ActiveModelApiConfigDeleteForbidden,
    ModelApiConfigConflict,
    ModelApiConfigNotFound,
    ModelApiConfigStorageError,
    ModelApiConfigService,
)


class LockTrackingQuery:
    def __init__(self, session, *, rows=None, row=None):
        self.session = session
        self.rows = rows or []
        self.row = row

    def filter(self, *_args):
        return self

    def with_for_update(self):
        self.session.lock_acquisitions += 1
        self.session.lock_held = True
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.row


class LockTrackingSession:
    def __init__(self, *, rows=None, row=None):
        self.query_result = LockTrackingQuery(self, rows=rows, row=row)
        self.lock_acquisitions = 0
        self.lock_held = False
        self.rollback_count = 0

    def query(self, _model):
        return self.query_result

    def rollback(self):
        self.rollback_count += 1
        self.lock_held = False


class FailingWriteSession:
    def __init__(self, operation, error):
        self.operation = operation
        self.error = error
        self.rollback_count = 0

    def flush(self):
        if self.operation == "flush":
            raise self.error

    def commit(self):
        if self.operation == "commit":
            raise self.error

    def rollback(self):
        self.rollback_count += 1


class ModelApiConfigServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        ModelApiConfig.__table__.create(bind=self.engine)
        self.db = sessionmaker(bind=self.engine)()
        key = Fernet.generate_key().decode("ascii")
        self.service = ModelApiConfigService(self.db, ApiKeyCipher(key))

    def tearDown(self):
        self.db.close()
        ModelApiConfig.__table__.drop(bind=self.engine)
        self.engine.dispose()

    def create_payload(self, name: str) -> ModelApiConfigCreate:
        return ModelApiConfigCreate(
            name=name,
            provider="vllm",
            base_url="http://vllm:8000/v1",
            api_key="sk-secret",
            model="qwen",
            timeout_seconds=60,
            max_output_tokens=1024,
        )

    def test_create_encrypts_key_and_response_is_secret_free(self):
        row = self.service.create_config(self.create_payload(name="Primary"))
        output = self.service.to_out(row)

        self.assertNotEqual(row.api_key_ciphertext, "sk-secret")
        self.assertTrue(output.api_key_configured)
        self.assertFalse(hasattr(output, "api_key"))

    def test_patch_without_key_preserves_ciphertext(self):
        row = self.service.create_config(self.create_payload(name="Primary"))
        ciphertext = row.api_key_ciphertext

        updated = self.service.update_config(
            row.id, ModelApiConfigUpdate(model="qwen-plus")
        )

        self.assertEqual(updated.api_key_ciphertext, ciphertext)

    def test_patch_with_unavailable_cipher_does_not_mutate_other_fields(self):
        row = self.service.create_config(self.create_payload(name="Primary"))
        original_model = row.model
        original_ciphertext = row.api_key_ciphertext
        unavailable_service = ModelApiConfigService(self.db, ApiKeyCipher(""))

        with self.assertRaises(ApiKeyCipherUnavailable):
            unavailable_service.update_config(
                row.id,
                ModelApiConfigUpdate(model="qwen-plus", api_key="sk-replacement"),
            )

        self.db.commit()
        self.db.expire_all()
        reloaded = self.service.get_config(row.id)
        self.assertEqual(reloaded.model, original_model)
        self.assertEqual(reloaded.api_key_ciphertext, original_ciphertext)

    def test_duplicate_name_is_rejected_without_database_details(self):
        self.service.create_config(self.create_payload(name="Primary"))

        with self.assertRaises(ModelApiConfigConflict) as context:
            self.service.create_config(self.create_payload(name="Primary"))

        self.assertNotIn("UNIQUE constraint", str(context.exception))

    def test_write_helpers_raise_unchained_secret_free_domain_errors(self):
        secret = "sk-bound-parameter-secret"
        ciphertext = "gAAAAA-bound-parameter-ciphertext"
        cases = (
            (
                "flush",
                IntegrityError(
                    "UPDATE model_api_configs",
                    {"api_key_ciphertext": ciphertext},
                    RuntimeError(secret),
                ),
                ModelApiConfigConflict,
                "configuration name already exists",
            ),
            (
                "commit",
                IntegrityError(
                    "INSERT INTO model_api_configs",
                    {"api_key_ciphertext": ciphertext},
                    RuntimeError(secret),
                ),
                ModelApiConfigConflict,
                "configuration name already exists",
            ),
            (
                "flush",
                SQLAlchemyError(f"flush failed for {secret} and {ciphertext}"),
                ModelApiConfigStorageError,
                "model API configuration storage is unavailable",
            ),
            (
                "commit",
                SQLAlchemyError(f"commit failed for {secret} and {ciphertext}"),
                ModelApiConfigStorageError,
                "model API configuration storage is unavailable",
            ),
        )

        for operation, error, expected_type, expected_message in cases:
            with self.subTest(operation=operation, error_type=type(error).__name__):
                db = FailingWriteSession(operation, error)
                service = ModelApiConfigService(db, MagicMock(spec=ApiKeyCipher))
                helper = getattr(service, f"_{operation}_or_conflict")

                with self.assertRaises(expected_type) as context:
                    helper("configuration name already exists")

                raised = context.exception
                rendered = f"{str(raised)} {repr(raised)}"
                self.assertEqual(str(raised), expected_message)
                self.assertNotIn(secret, rendered)
                self.assertNotIn(ciphertext, rendered)
                self.assertIsNone(raised.__cause__)
                self.assertIsNone(raised.__context__)
                self.assertEqual(db.rollback_count, 1)

    def test_renaming_to_an_existing_name_is_rejected(self):
        first = self.service.create_config(self.create_payload(name="First"))
        second = self.service.create_config(self.create_payload(name="Second"))

        with self.assertRaises(ModelApiConfigConflict):
            self.service.update_config(second.id, ModelApiConfigUpdate(name=first.name))

    def test_activation_is_exclusive_and_active_delete_is_rejected(self):
        first = self.service.create_config(self.create_payload(name="First"))
        second = self.service.create_config(self.create_payload(name="Second"))

        self.service.activate_config(first.id)
        self.service.activate_config(second.id)

        self.assertFalse(self.service.get_config(first.id).is_active)
        self.assertTrue(self.service.get_config(second.id).is_active)
        self.service.activate_config(first.id)

        self.assertTrue(self.service.get_config(first.id).is_active)
        self.assertFalse(self.service.get_config(second.id).is_active)
        with self.assertRaises(ActiveModelApiConfigDeleteForbidden):
            self.service.delete_config(first.id)

    def test_delete_locks_target_before_checking_active_state(self):
        row = self.service.create_config(self.create_payload(name="Primary"))
        query = MagicMock()
        query.filter.return_value.with_for_update.return_value.first.return_value = row

        with patch.object(self.db, "query", return_value=query):
            self.service.delete_config(row.id)

        query.filter.return_value.with_for_update.assert_called_once_with()

    def test_activation_rejects_undecryptable_credential_before_switching(self):
        active = self.service.create_config(self.create_payload(name="Active"))
        invalid = self.service.create_config(self.create_payload(name="Invalid"))
        self.service.activate_config(active.id)
        invalid.api_key_ciphertext = "not-a-fernet-token"
        self.db.commit()

        with self.assertRaises(ApiKeyCipherUnavailable):
            self.service.activate_config(invalid.id)

        self.assertTrue(self.service.get_config(active.id).is_active)
        self.assertFalse(self.service.get_config(invalid.id).is_active)

    def test_missing_cipher_key_is_not_used_until_activation(self):
        row = self.service.create_config(self.create_payload(name="Primary"))
        unavailable_service = ModelApiConfigService(self.db, ApiKeyCipher(""))

        self.assertEqual(unavailable_service.list_configs(), [row])
        with self.assertRaises(ApiKeyCipherUnavailable):
            unavailable_service.activate_config(row.id)

    def test_activation_missing_target_rolls_back_and_releases_lock(self):
        db = LockTrackingSession(rows=[])
        service = ModelApiConfigService(db, MagicMock(spec=ApiKeyCipher))

        with self.assertRaises(ModelApiConfigNotFound):
            service.activate_config(999)

        self.assertEqual(db.lock_acquisitions, 1)
        self.assertEqual(db.rollback_count, 1)
        self.assertFalse(db.lock_held)

    def test_activation_decrypt_failure_rolls_back_and_releases_lock(self):
        row = SimpleNamespace(id=1, api_key_ciphertext="not-a-fernet-token")
        db = LockTrackingSession(rows=[row])
        cipher = MagicMock(spec=ApiKeyCipher)
        cipher.decrypt.side_effect = ApiKeyCipherUnavailable(
            "stored API key cannot be decrypted"
        )
        service = ModelApiConfigService(db, cipher)

        with self.assertRaises(ApiKeyCipherUnavailable):
            service.activate_config(row.id)

        self.assertEqual(db.lock_acquisitions, 1)
        self.assertEqual(db.rollback_count, 1)
        self.assertFalse(db.lock_held)

    def test_active_delete_rejection_rolls_back_and_releases_lock(self):
        row = SimpleNamespace(id=1, is_active=True)
        db = LockTrackingSession(row=row)
        service = ModelApiConfigService(db, MagicMock(spec=ApiKeyCipher))

        with self.assertRaises(ActiveModelApiConfigDeleteForbidden):
            service.delete_config(row.id)

        self.assertEqual(db.lock_acquisitions, 1)
        self.assertEqual(db.rollback_count, 1)
        self.assertFalse(db.lock_held)

    def test_locked_delete_missing_target_rolls_back_and_releases_lock(self):
        db = LockTrackingSession(row=None)
        service = ModelApiConfigService(db, MagicMock(spec=ApiKeyCipher))

        with self.assertRaises(ModelApiConfigNotFound):
            service.delete_config(999)

        self.assertEqual(db.lock_acquisitions, 1)
        self.assertEqual(db.rollback_count, 1)
        self.assertFalse(db.lock_held)

    def test_get_missing_config_raises_not_found(self):
        with self.assertRaises(ModelApiConfigNotFound):
            self.service.get_config(999)


if __name__ == "__main__":
    unittest.main()
