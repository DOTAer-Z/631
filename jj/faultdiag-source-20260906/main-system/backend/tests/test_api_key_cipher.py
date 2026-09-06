import unittest

from cryptography.fernet import Fernet

from app.services.api_key_cipher import ApiKeyCipher, ApiKeyCipherUnavailable


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
