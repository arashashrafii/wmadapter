from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path


class CredentialStoreError(RuntimeError):
    pass


def default_key_path() -> Path:
    return Path(os.getenv("WMADAPTER_KEY_FILE", "~/.config/wmadapter/key")).expanduser()


def default_store_path() -> Path:
    return Path(os.getenv("WMADAPTER_CREDENTIAL_FILE", "~/.local/share/wmadapter/credentials.json")).expanduser()


def _load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return base64.b64decode(path.read_text().strip())
    key = os.urandom(32)
    path.write_text(base64.b64encode(key).decode("ascii"))
    path.chmod(0o600)
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(output[:length])


def _crypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
    return bytes(byte ^ stream for byte, stream in zip(data, _keystream(key, nonce, len(data))))


class CredentialStore:
    def __init__(self, store_path: Path | None = None, key_path: Path | None = None):
        self.store_path = store_path or default_store_path()
        self.key_path = key_path or default_key_path()

    def load(self) -> tuple[str, str] | None:
        if not self.store_path.exists():
            return None
        key = _load_or_create_key(self.key_path)
        try:
            payload = json.loads(self.store_path.read_text())
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            tag = base64.b64decode(payload["tag"])
        except Exception as exc:
            raise CredentialStoreError("Credential file is invalid") from exc

        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise CredentialStoreError("Credential file authentication failed")
        data = json.loads(_crypt(key, nonce, ciphertext).decode("utf-8"))
        return data["email"], data["password"]

    def save(self, email: str, password: str) -> None:
        if not email or not password:
            raise CredentialStoreError("Email and password are required")
        key = _load_or_create_key(self.key_path)
        nonce = os.urandom(16)
        plaintext = json.dumps({"email": email, "password": password}, separators=(",", ":")).encode("utf-8")
        ciphertext = _crypt(key, nonce, plaintext)
        tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps({
            "version": 1,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        }, indent=2))
        self.store_path.chmod(0o600)


def save_credentials_interactive() -> None:
    email = input("DeepSeek email: ").strip()
    password = getpass.getpass("DeepSeek password: ")
    CredentialStore().save(email, password)
    print(f"Saved encrypted credentials to {default_store_path()}")
