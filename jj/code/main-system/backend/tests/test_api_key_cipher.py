import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from app.services.api_key_cipher import (
    ApiKeyCipher,
    ApiKeyCipherUnavailable,
    _load_or_create_persisted_key,
)


class ApiKeyCipherAutoGenerateTests(unittest.TestCase):
    """零-yaml：MODEL_API_ENCRYPTION_KEY 为空时自动生成并持久化到文件。"""

    def test_from_settings_generates_and_persists_key_when_env_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyfile = Path(tmp) / "model_api_encryption_key"
            with mock.patch(
                "app.services.api_key_cipher.settings.MODEL_API_ENCRYPTION_KEY",
                "",
            ), mock.patch(
                "app.services.api_key_cipher.settings.MODEL_API_ENCRYPTION_KEY_FILE",
                str(keyfile),
            ):
                cipher = ApiKeyCipher.from_settings()
                keyfile_content = keyfile.read_text(encoding="utf-8").strip()

            self.assertTrue(cipher.encrypt("sk-secret"))
            # key 已写盘且只写一次
            self.assertEqual(keyfile_content, keyfile.read_text(encoding="utf-8").strip())
            self.assertEqual(cipher.decrypt(cipher.encrypt("x")), "x")

    def test_from_settings_uses_env_key_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyfile = Path(tmp) / "model_api_encryption_key"
            env_key = Fernet.generate_key().decode("ascii")
            with mock.patch(
                "app.services.api_key_cipher.settings.MODEL_API_ENCRYPTION_KEY",
                env_key,
            ), mock.patch(
                "app.services.api_key_cipher.settings.MODEL_API_ENCRYPTION_KEY_FILE",
                str(keyfile),
            ):
                cipher = ApiKeyCipher.from_settings()

            self.assertEqual(cipher.decrypt(cipher.encrypt("v")), "v")
            self.assertFalse(keyfile.exists())

    def test_load_or_create_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyfile = Path(tmp) / "k"
            first = _load_or_create_persisted_key(keyfile)
            second = _load_or_create_persisted_key(keyfile)
            self.assertTrue(first)
            self.assertEqual(first, second)

    def test_load_or_create_returns_unusable_path_key(self):
        # 父目录是不可写文件 → 生成失败仍返回 key（进程内可用），不抛异常
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            key = _load_or_create_persisted_key(blocker / "sub" / "k")
            self.assertTrue(key)


class ApiKeyCipherTests(unittest.TestCase):
    def test_round_trip_does_not_store_plaintext(self):
        key = Fernet.generate_key().decode("ascii")
        cipher = ApiKeyCipher(key)

        encrypted = cipher.encrypt("sk-secret-value")

        self.assertNotIn("sk-secret-value", encrypted)
        self.assertEqual(cipher.decrypt(encrypted), "sk-secret-value")

    def test_missing_key_is_reported_only_when_secret_is_used(self):
        cipher = ApiKeyCipher("")

        with self.assertRaises(ApiKeyCipherUnavailable) as raised:
            cipher.encrypt("sk-secret-value")

        self.assertEqual(
            str(raised.exception),
            "MODEL_API_ENCRYPTION_KEY is not configured",
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_invalid_key_never_echoes_key_material(self):
        invalid_key = "not-a-fernet-key"
        cipher = ApiKeyCipher(invalid_key)

        with self.assertRaises(ApiKeyCipherUnavailable) as raised:
            cipher.encrypt("sk-secret-value")

        self.assertEqual(
            str(raised.exception),
            "MODEL_API_ENCRYPTION_KEY is not a valid Fernet key",
        )
        self.assertNotIn(invalid_key, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_undecryptable_ciphertext_uses_fixed_unchained_error(self):
        cipher = ApiKeyCipher(Fernet.generate_key().decode("ascii"))
        ciphertext = "stored-ciphertext-that-must-not-leak"

        with self.assertRaises(ApiKeyCipherUnavailable) as raised:
            cipher.decrypt(ciphertext)

        self.assertEqual(
            str(raised.exception),
            "stored API key cannot be decrypted",
        )
        self.assertNotIn(ciphertext, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)


if __name__ == "__main__":
    unittest.main()
