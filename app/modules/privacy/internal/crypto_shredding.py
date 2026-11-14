"""F04 envelope encryption and crypto-shredding of personal data (AUD-006, spec algorithm 6.8).

* Every user gets a random 256-bit DEK. PII (`{"email", "display_name", "phone"}`) is encrypted with it
  (AES-256-GCM, the user id as associated data, so a ciphertext cannot be moved to another user).
* The DEK is stored only wrapped by a per-user KEK held by a key service outside PostgreSQL and outside
  the database backups (`KeyEncryptionService`). The database keeps the wrapped DEK and the KEK id.
* Erasure destroys the KEK and nulls the wrapped DEK: the PII ciphertext, also in old backups, can no
  longer be decrypted. The certificate digest comes from `app.ops.pdp_compliance`.

`InMemoryKeyEncryptionService` is the development and test implementation (keys live in the process).
Production plugs in a Vault Transit implementation of the same protocol (open question #4 of the plan).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.ops.pdp_compliance import (
    ErasureComplianceResult,
    evaluate_crypto_shredding_erasure,
    validate_vault_secret_ref,
)

DEK_BYTES = 32
NONCE_BYTES = 12
_FORMAT = b"\x01"  # ciphertext layout version: 1 byte version | 12 byte nonce | AES-GCM output
PII_FIELDS = ("email", "display_name", "phone")
BACKUP_RETENTION_DAYS = 35


class KeyShreddedError(LookupError):
    """The KEK was destroyed: the data it protected is gone for good."""


class KeyEncryptionService(Protocol):
    """A KMS/Vault Transit-like service; KEKs never leave it."""

    def create_key(self, user_id: uuid.UUID) -> str: ...

    def wrap(self, key_id: str, dek: bytes) -> bytes: ...

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes: ...

    def destroy(self, key_id: str) -> None: ...


class InMemoryKeyEncryptionService:
    """Development/test KEK store. A destroyed key is gone; unknown and destroyed keys look the same."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def create_key(self, user_id: uuid.UUID) -> str:
        key_id = validate_vault_secret_ref(f"vault://transit/keys/user-{user_id}")
        self._keys.setdefault(key_id, AESGCM.generate_key(bit_length=256))
        return key_id

    def wrap(self, key_id: str, dek: bytes) -> bytes:
        return _seal(self._key(key_id), dek, key_id.encode())

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        return _open(self._key(key_id), wrapped, key_id.encode())

    def destroy(self, key_id: str) -> None:
        self._keys.pop(key_id, None)

    def has_key(self, key_id: str) -> bool:
        return key_id in self._keys

    def _key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise KeyShreddedError(f"key {key_id} does not exist or was destroyed") from exc


_default_service: KeyEncryptionService = InMemoryKeyEncryptionService()


def key_service() -> KeyEncryptionService:
    return _default_service


def set_key_service(service: KeyEncryptionService) -> KeyEncryptionService:
    """Install the key service (Vault in production, a fresh in-memory one in tests); returns the old one."""
    global _default_service
    previous, _default_service = _default_service, service
    return previous


def new_dek() -> bytes:
    return AESGCM.generate_key(bit_length=DEK_BYTES * 8)


def _seal(key: bytes, plaintext: bytes, associated: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return _FORMAT + nonce + AESGCM(key).encrypt(nonce, plaintext, associated)


def _open(key: bytes, sealed: bytes, associated: bytes) -> bytes:
    if len(sealed) < 1 + NONCE_BYTES + 16 or sealed[:1] != _FORMAT:
        raise ValueError("not a sealed value of this format")
    return AESGCM(key).decrypt(sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :], associated)


def encrypt_pii(dek: bytes, user_id: uuid.UUID, pii: Mapping[str, Any]) -> bytes:
    """AES-256-GCM of the canonical JSON of the known PII fields, bound to `user_id`."""
    unknown = set(pii) - set(PII_FIELDS)
    if unknown:
        raise ValueError(f"unknown PII fields: {', '.join(sorted(unknown))}")
    payload = json.dumps({k: pii[k] for k in sorted(pii)}, ensure_ascii=False, separators=(",", ":")).encode()
    return _seal(dek, payload, user_id.bytes)


def decrypt_pii(dek: bytes, user_id: uuid.UUID, ciphertext: bytes) -> dict[str, Any]:
    return json.loads(_open(dek, ciphertext, user_id.bytes))


def encrypt_value(dek: bytes, user_id: uuid.UUID, value: str, purpose: str) -> bytes:
    """Encrypt one field (e.g. a guardian e-mail) under the user's DEK; `purpose` separates contexts."""
    return _seal(dek, value.encode(), user_id.bytes + purpose.encode())


def decrypt_value(dek: bytes, user_id: uuid.UUID, ciphertext: bytes, purpose: str) -> str:
    return _open(dek, ciphertext, user_id.bytes + purpose.encode()).decode()


def blind_index_pepper(secret_key: str) -> bytes:
    """A 32-byte pepper for e-mail blind indexes, derived from the application secret.

    A dedicated cluster pepper setting (kept apart from the session secret) is the better long-term home.
    """
    return hmac.new(secret_key.encode(), b"f04:email-blind-index:v1", hashlib.sha256).digest()


def erasure_certificate(*, tenant_id: uuid.UUID, subject_user_id: uuid.UUID, key_destroyed: bool,
                        remaining_plaintext_pii_fields: tuple[str, ...] = ()) -> ErasureComplianceResult:  # fmt: skip
    """The deterministic certificate digest; `compliant` is False unless the key is gone and no PII is left."""
    return evaluate_crypto_shredding_erasure(
        tenant_id=str(tenant_id),
        subject_id=str(subject_user_id),
        vault_key_destroyed=key_destroyed,
        remaining_plaintext_pii_fields=remaining_plaintext_pii_fields,
        audit_trail_preserved=True,
    )
