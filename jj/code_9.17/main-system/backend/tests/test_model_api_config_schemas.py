import unittest

from pydantic import ValidationError

from app.schemas.model_api_config import ModelApiConfigCreate, ModelApiConfigUpdate


class ModelApiConfigSchemaTests(unittest.TestCase):
    def test_create_accepts_frontend_contract(self):
        payload = ModelApiConfigCreate(
            name="Cluster vLLM",
            provider="vllm",
            base_url="http://qwen-vllm:8000/v1",
            api_key="dummy",
            model="qwen3.5-9b-sft",
            timeout_seconds=60,
            max_output_tokens=1024,
        )
        self.assertEqual(payload.provider, "vllm")

    def test_update_may_omit_api_key_but_may_not_be_empty(self):
        payload = ModelApiConfigUpdate(model="qwen-plus")
        self.assertEqual(payload.model, "qwen-plus")
        self.assertNotIn("api_key", payload.model_fields_set)
        with self.assertRaises(ValidationError):
            ModelApiConfigUpdate(api_key="")
        with self.assertRaises(ValidationError):
            ModelApiConfigUpdate()

    def test_update_rejects_explicit_null_for_every_field(self):
        fields = (
            "name",
            "provider",
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "max_output_tokens",
        )

        for field in fields:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ModelApiConfigUpdate(**{field: None})

    def test_rejects_unknown_provider_and_non_http_url(self):
        with self.assertRaises(ValidationError):
            ModelApiConfigCreate(
                name="bad", provider="unknown", base_url="file:///tmp/model",
                api_key="secret", model="qwen", timeout_seconds=60,
                max_output_tokens=1024,
            )


if __name__ == "__main__":
    unittest.main()
