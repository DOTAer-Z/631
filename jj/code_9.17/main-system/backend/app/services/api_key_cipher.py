from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


logger = logging.getLogger(__name__)


class ApiKeyCipherUnavailable(RuntimeError):
    pass


class ApiKeyCipher:
    def __init__(self, key: str):
        self._fernet = None
        self._key = (key or "").strip()

    @classmethod
    def from_settings(cls) -> "ApiKeyCipher":
        key = settings.MODEL_API_ENCRYPTION_KEY
        if not key:
            # 零-yaml 部署：环境变量未配置时，从持久卷读取自动生成的 key；
            # 卷里也没有则生成一个并写盘，保证重启后 key 稳定（否则已存的 API Key
            # 会因 key 漂移而解不开）。持久卷在 k8s/03-backend.yaml 挂载为 /app/outputs。
            key = _load_or_create_persisted_key(Path(settings.MODEL_API_ENCRYPTION_KEY_FILE))
        return cls(key)

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


def _load_or_create_persisted_key(path: Path) -> str:
    """读取持久化 key；不存在则生成并写盘。写盘失败时仍返回新 key（进程内可用），
    但记录警告——仅当持久卷不可写且重启场景下已存 key 才可能丢失。"""
    if path.exists():
        try:
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
        except OSError:
            pass

    key = Fernet.generate_key().decode("ascii")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 权限：key 属敏感材料，仅本进程用户可读写
        path.write_text(key, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.warning(
            "无法持久化 MODEL_API_ENCRYPTION_KEY 到 %s（%s）；本次将仅在内存中使用",
            path,
            exc,
        )
    return key
