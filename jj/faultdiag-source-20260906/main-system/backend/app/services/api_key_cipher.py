from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class ApiKeyCipherUnavailable(RuntimeError):
    pass


class ApiKeyCipher:
    def __init__(self, key: str):
        self._fernet = None
        self._key = (key or "").strip()

    @classmethod
    def from_settings(cls) -> "ApiKeyCipher":
        return cls(settings.MODEL_API_ENCRYPTION_KEY)

    def _require_fernet(self) -> Fernet:
        if self._fernet is None:
            if not self._key:
                raise ApiKeyCipherUnavailable(
                    "MODEL_API_ENCRYPTION_KEY is not configured"
                )
            try:
                self._fernet = Fernet(self._key.encode("ascii"))
            except (UnicodeEncodeError, ValueError):
                pass
            if self._fernet is None:
                raise ApiKeyCipherUnavailable(
                    "MODEL_API_ENCRYPTION_KEY is not a valid Fernet key"
                ) from None
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        return self._require_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._require_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError):
            pass
        raise ApiKeyCipherUnavailable("stored API key cannot be decrypted") from None
