import unittest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.model_api_config import ModelApiConfig


class ModelApiConfigModelTests(unittest.TestCase):
    def test_table_contains_secret_and_runtime_columns(self):
        columns = ModelApiConfig.__table__.columns
        self.assertEqual(ModelApiConfig.__tablename__, "model_api_configs")
        for name in [
            "name", "provider", "base_url", "model", "api_key_ciphertext",
            "timeout_seconds", "max_output_tokens", "is_active",
            "created_at", "updated_at",
        ]:
            self.assertIn(name, columns)

    def test_model_has_unique_name_and_postgresql_active_index(self):
        constraints = {constraint.name for constraint in ModelApiConfig.__table__.constraints}
        indexes = {index.name: index for index in ModelApiConfig.__table__.indexes}

        self.assertIn("uq_model_api_configs_name", constraints)
        active_index = indexes["uq_model_api_configs_one_active"]
        self.assertTrue(active_index.unique)
        self.assertIsNotNone(active_index.dialect_options["postgresql"]["where"])

    def test_sqlite_allows_inactive_configs_but_only_one_active_config(self):
        engine = create_engine("sqlite://")
        ModelApiConfig.__table__.create(bind=engine)
        session = sessionmaker(bind=engine)()

        def config(name: str, is_active: bool) -> ModelApiConfig:
            return ModelApiConfig(
                name=name,
                provider="vllm",
                base_url="http://model.example/v1",
                model="qwen",
                api_key_ciphertext="ciphertext",
                is_active=is_active,
            )

        try:
            session.add_all([config("inactive-one", False), config("inactive-two", False)])
            session.commit()

            session.add(config("active-one", True))
            session.commit()

            session.add(config("active-two", True))
            with self.assertRaises(IntegrityError):
                session.commit()
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
