"""Storage and browser boundary for the standalone diagnosis service."""

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import Request

SESSION_TTL = 24 * 60 * 60


class CredentialsKeyError(ValueError):
    pass


CredentialsDecryptError = InvalidToken


class CredentialsCipher:
    def __init__(self, keys: list[Fernet]):
        self._cipher = MultiFernet(keys)

    @classmethod
    def from_keys(cls, keys: list[bytes]):
        return cls([Fernet(key) for key in keys])

    def encrypt_text(self, value: str) -> str:
        return self._cipher.encrypt(value.encode()).decode()

    def decrypt_text(self, value: str) -> str:
        return self._cipher.decrypt(value.encode()).decode()


def get_credentials_cipher() -> CredentialsCipher:
    key = os.getenv("CI_TRACE_CREDENTIALS_KEY", "").strip()
    try:
        if not key and (key_file := os.getenv("CI_TRACE_CREDENTIALS_KEY_FILE")):
            key = _read_or_create_key(Path(key_file))
        return CredentialsCipher.from_keys([key.encode()])
    except (OSError, ValueError) as error:
        raise CredentialsKeyError("Configure a valid CI_TRACE_CREDENTIALS_KEY") from error


def _read_or_create_key(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(Fernet.generate_key())
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            pass
        finally:
            Path(temporary.name).unlink()
        return path.read_text(encoding="ascii").strip()


def database_path() -> Path:
    return Path(os.getenv("CI_TRACE_DATABASE", ".data/ci-trace.sqlite3")).expanduser()


def is_allowed_auth_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True
    configured = os.getenv("CI_TRACE_ORIGIN", "").rstrip("/")
    local_origins = {"http://localhost:3000", "http://127.0.0.1:3000"}
    return origin == configured if configured else origin in local_origins or origin == str(request.base_url).rstrip("/")


def is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https" or os.getenv("CI_TRACE_ORIGIN", "").startswith("https://")


def is_loopback_origin(request: Request) -> bool:
    configured = os.getenv("CI_TRACE_ORIGIN")
    hostname = urlsplit(configured).hostname if configured else request.url.hostname
    return hostname in {"localhost", "127.0.0.1", "::1"}
