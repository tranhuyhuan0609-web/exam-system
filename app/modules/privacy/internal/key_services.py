"""Key services that survive a restart: a fixed master key from the environment (pilot) and Vault Transit (production).

`InMemoryKeyEncryptionService` (the default in dev and tests) loses every key when the process ends, so a user whose
PII was enrolled through it can never be read again. The two services here keep the same `KeyEncryptionService`
protocol:

* `EnvKeyEncryptionService` derives the per-user KEK from one master key given in `PII_MASTER_KEY` (HKDF-SHA256, a fresh
  random salt per wrapped DEK, the key id as associated data). It is meant for a pilot on one server. Erasure still
  nulls the wrapped DEK in PostgreSQL, but the master key can decrypt an old backup of that column, so it does NOT
  give crypto-shredding against backups (`shreds_backups` is False): move to Vault before real student data.
* `VaultTransitKeyEncryptionService` keeps every KEK inside HashiCorp Vault (Transit engine); erasure deletes the
  transit key, which does shred the data in old backups. It talks to Vault over HTTP with a token.

`configure_key_service(settings)` picks one from `KEY_SERVICE_BACKEND` (`memory` | `env` | `vault`).
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.modules.privacy.internal.crypto_shredding import (
    InMemoryKeyEncryptionService,
    KeyEncryptionService,
    KeyShreddedError,
    set_key_service,
)
from app.ops.pdp_compliance import validate_vault_secret_ref

_FORMAT = b"\x02"  # wrapped-DEK layout: 1 byte version | 16 byte salt | 12 byte nonce | AES-GCM output
_SALT_BYTES = 16
_NONCE_BYTES = 12
_MIN_MASTER_KEY_BYTES = 32


class KeyServiceConfigError(ValueError):
    """The key service named in the settings cannot be built: the application must not start."""


def decode_master_key(raw: str) -> bytes:
    """`PII_MASTER_KEY`: base64 (or base64url) of at least 32 random bytes, e.g. `openssl rand -base64 32`."""
    text = raw.strip()
    if not text:
        raise KeyServiceConfigError("PII_MASTER_KEY is empty: set it to `openssl rand -base64 32`")
    padded = text + "=" * (-len(text) % 4)
    try:
        key = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeyServiceConfigError("PII_MASTER_KEY is not valid base64") from exc
    if len(key) < _MIN_MASTER_KEY_BYTES:
        raise KeyServiceConfigError(f"PII_MASTER_KEY must hold at least {_MIN_MASTER_KEY_BYTES} random bytes")
    return key


class EnvKeyEncryptionService:
    """KEKs derived from one master key of the environment. Pilot only: see the module docstring."""

    shreds_backups = False

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < _MIN_MASTER_KEY_BYTES:
            raise KeyServiceConfigError(f"the master key must hold at least {_MIN_MASTER_KEY_BYTES} bytes")
        self._master = master_key
        self._destroyed: set[str] = set()

    def _kek(self, key_id: str, salt: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"ulp-pii-kek|" + key_id.encode()).derive(
            self._master
        )

    def create_key(self, user_id: uuid.UUID) -> str:
        return validate_vault_secret_ref(f"vault://transit/keys/user-{user_id}")

    def wrap(self, key_id: str, dek: bytes) -> bytes:
        self._check(key_id)
        salt, nonce = os.urandom(_SALT_BYTES), os.urandom(_NONCE_BYTES)
        return _FORMAT + salt + nonce + AESGCM(self._kek(key_id, salt)).encrypt(nonce, dek, key_id.encode())

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        self._check(key_id)
        header = 1 + _SALT_BYTES + _NONCE_BYTES
        if len(wrapped) <= header or wrapped[:1] != _FORMAT:
            raise KeyShreddedError(f"key {key_id} cannot open this wrapped key")
        salt, nonce = wrapped[1 : 1 + _SALT_BYTES], wrapped[1 + _SALT_BYTES : header]
        try:
            return AESGCM(self._kek(key_id, salt)).decrypt(nonce, wrapped[header:], key_id.encode())
        except InvalidTag as exc:
            raise KeyShreddedError(f"key {key_id} cannot open this wrapped key") from exc

    def destroy(self, key_id: str) -> None:
        """Refuses further use in this process; the durable effect is the erased wrapped DEK in PostgreSQL."""
        self._destroyed.add(key_id)

    def _check(self, key_id: str) -> None:
        if key_id in self._destroyed:
            raise KeyShreddedError(f"key {key_id} does not exist or was destroyed")


class VaultTransitKeyEncryptionService:
    """One Transit key per user in HashiCorp Vault; the KEK never leaves Vault, deleting the key shreds the data."""

    shreds_backups = True

    def __init__(
        self,
        addr: str,
        token: str,
        *,
        mount: str = "transit",
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not token:
            raise KeyServiceConfigError("VAULT_TOKEN is empty")
        self._mount = mount.strip("/")
        self._http = client or httpx.Client(base_url=addr.rstrip("/"), timeout=timeout)
        self._headers = {"X-Vault-Token": token}

    @staticmethod
    def _name(key_id: str) -> str:
        return validate_vault_secret_ref(key_id).rsplit("/", 1)[-1]

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return self._http.request(method, f"/v1/{self._mount}/{path}", json=body, headers=self._headers)

    def create_key(self, user_id: uuid.UUID) -> str:
        key_id = validate_vault_secret_ref(f"vault://transit/keys/user-{user_id}")
        name = self._name(key_id)
        self._call("POST", f"keys/{name}", {"type": "aes256-gcm96"}).raise_for_status()
        # a key can only be deleted (erasure) when it allows it
        self._call("POST", f"keys/{name}/config", {"deletion_allowed": True}).raise_for_status()
        return key_id

    def wrap(self, key_id: str, dek: bytes) -> bytes:
        name = self._name(key_id)
        # Transit `encrypt` creates a missing key on the fly: a destroyed key must never come back that way
        exists = self._call("GET", f"keys/{name}")
        if exists.status_code in (400, 404):
            raise KeyShreddedError(f"key {key_id} does not exist or was destroyed")
        exists.raise_for_status()
        response = self._call("POST", f"encrypt/{name}", {"plaintext": base64.b64encode(dek).decode()})
        if response.status_code in (400, 404):
            raise KeyShreddedError(f"key {key_id} does not exist or was destroyed")
        response.raise_for_status()
        return str(response.json()["data"]["ciphertext"]).encode()

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        response = self._call("POST", f"decrypt/{self._name(key_id)}", {"ciphertext": wrapped.decode()})
        if response.status_code in (400, 404):
            raise KeyShreddedError(f"key {key_id} does not exist or was destroyed")
        response.raise_for_status()
        return base64.b64decode(response.json()["data"]["plaintext"])

    def destroy(self, key_id: str) -> None:
        response = self._call("DELETE", f"keys/{self._name(key_id)}")
        if response.status_code in (200, 204, 404):
            return
        if response.status_code == 400 and "not found" in response.text:
            return  # Vault answers 400 "could not delete key; not found": already gone, destroying twice is harmless
        response.raise_for_status()


def build_key_service(settings: Any) -> KeyEncryptionService:
    """The key service the settings name; anything unknown or half configured raises (fail closed)."""
    backend = str(settings.key_service_backend).strip().lower()
    if backend == "memory":
        return InMemoryKeyEncryptionService()
    if backend == "env":
        return EnvKeyEncryptionService(decode_master_key(settings.pii_master_key))
    if backend == "vault":
        return VaultTransitKeyEncryptionService(
            settings.vault_addr, settings.vault_token, mount=settings.vault_transit_mount
        )
    raise KeyServiceConfigError(f"KEY_SERVICE_BACKEND must be memory, env or vault, not {backend!r}")


def configure_key_service(settings: Any) -> KeyEncryptionService:
    """Install the configured key service for the whole process (called once when the application starts)."""
    service = build_key_service(settings)
    set_key_service(service)
    return service
