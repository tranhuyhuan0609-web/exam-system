"""PII key services that survive a restart: the env master key (pilot) and Vault Transit (production)."""

from __future__ import annotations

import base64
import json
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.modules.privacy.public import (
    EnvKeyEncryptionService,
    InMemoryKeyEncryptionService,
    KeyServiceConfigError,
    KeyShreddedError,
    VaultTransitKeyEncryptionService,
    build_key_service,
    decode_master_key,
)

MASTER = base64.b64encode(os.urandom(32)).decode()


def test_the_master_key_must_be_real_base64_of_enough_bytes():
    assert len(decode_master_key(MASTER)) == 32
    assert len(decode_master_key(MASTER.replace("+", "-").replace("/", "_").rstrip("="))) == 32  # base64url
    for bad in ("", "   ", "not base64!!", base64.b64encode(b"short").decode()):
        with pytest.raises(KeyServiceConfigError):
            decode_master_key(bad)


def test_env_service_wraps_and_unwraps_across_instances_so_a_restart_loses_nothing():
    user, dek = uuid.uuid4(), os.urandom(32)
    first = EnvKeyEncryptionService(decode_master_key(MASTER))
    key_id = first.create_key(user)
    wrapped = first.wrap(key_id, dek)
    assert dek not in wrapped
    restarted = EnvKeyEncryptionService(decode_master_key(MASTER))  # a new process, same environment
    assert restarted.unwrap(key_id, wrapped) == dek
    assert first.wrap(key_id, dek) != wrapped, "a fresh salt and nonce every time"


def test_env_service_binds_a_wrapped_key_to_its_user_and_master_key():
    dek = os.urandom(32)
    service = EnvKeyEncryptionService(decode_master_key(MASTER))
    mine, other = service.create_key(uuid.uuid4()), service.create_key(uuid.uuid4())
    wrapped = service.wrap(mine, dek)
    with pytest.raises(KeyShreddedError):
        service.unwrap(other, wrapped)  # moved to another user
    with pytest.raises(KeyShreddedError):
        EnvKeyEncryptionService(os.urandom(32)).unwrap(mine, wrapped)  # another master key
    with pytest.raises(KeyShreddedError):
        service.unwrap(mine, wrapped[:-1] + bytes([wrapped[-1] ^ 1]))  # tampered
    with pytest.raises(KeyShreddedError):
        service.unwrap(mine, b"")


def test_env_service_refuses_a_key_after_destroy_and_says_it_does_not_shred_backups():
    service = EnvKeyEncryptionService(decode_master_key(MASTER))
    key_id = service.create_key(uuid.uuid4())
    wrapped = service.wrap(key_id, os.urandom(32))
    service.destroy(key_id)
    with pytest.raises(KeyShreddedError):
        service.unwrap(key_id, wrapped)
    with pytest.raises(KeyShreddedError):
        service.wrap(key_id, os.urandom(32))
    assert EnvKeyEncryptionService.shreds_backups is False
    assert VaultTransitKeyEncryptionService.shreds_backups is True


class FakeVault:
    """The few Transit endpoints the service uses, with real destroy semantics."""

    def __init__(self) -> None:
        self.keys: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "s.test"
        path = request.url.path.removeprefix("/v1/transit/")
        self.calls.append((request.method, path))
        body = json.loads(request.content) if request.content else {}
        kind, _, rest = path.partition("/")
        if kind == "keys" and request.method == "GET":
            return httpx.Response(200, json={"data": {"name": rest}}) if rest in self.keys else httpx.Response(404)
        if kind == "keys" and request.method == "POST" and rest.endswith("/config"):
            self.keys[rest.removesuffix("/config")]["deletion_allowed"] = body["deletion_allowed"]
            return httpx.Response(204)
        if kind == "keys" and request.method == "POST":
            self.keys[rest] = {"type": body["type"], "deletion_allowed": False}
            return httpx.Response(204)
        if kind == "keys" and request.method == "DELETE":
            if rest not in self.keys:
                return httpx.Response(
                    400, json={"errors": [f"error deleting policy {rest}: could not delete key; not found"]}
                )
            if not self.keys[rest]["deletion_allowed"]:
                return httpx.Response(400, json={"errors": ["deletion is not allowed"]})
            del self.keys[rest]
            return httpx.Response(204)
        if kind == "decrypt" and rest not in self.keys:
            return httpx.Response(400, json={"errors": ["encryption key not found"]})
        if kind == "encrypt":
            self.keys.setdefault(rest, {"type": "aes256-gcm96", "deletion_allowed": False})  # real Transit upserts
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v1:{rest}:{body['plaintext']}"}})
        if kind == "decrypt":
            plaintext = body["ciphertext"].split(":", 3)[3]
            return httpx.Response(200, json={"data": {"plaintext": plaintext}})
        return httpx.Response(500)


def _vault() -> tuple[VaultTransitKeyEncryptionService, FakeVault]:
    fake = FakeVault()
    client = httpx.Client(base_url="http://vault:8200", transport=httpx.MockTransport(fake.handler))
    return VaultTransitKeyEncryptionService("http://vault:8200", "s.test", client=client), fake


def test_vault_service_creates_a_deletable_key_wraps_and_unwraps():
    service, fake = _vault()
    user, dek = uuid.uuid4(), os.urandom(32)
    key_id = service.create_key(user)
    assert key_id == f"vault://transit/keys/user-{user}"
    assert fake.keys[f"user-{user}"] == {"type": "aes256-gcm96", "deletion_allowed": True}
    wrapped = service.wrap(key_id, dek)
    assert service.unwrap(key_id, wrapped) == dek


def test_vault_service_destroy_shreds_and_the_data_is_gone_for_good():
    service, fake = _vault()
    key_id = service.create_key(uuid.uuid4())
    wrapped = service.wrap(key_id, os.urandom(32))
    service.destroy(key_id)
    assert not fake.keys
    with pytest.raises(KeyShreddedError):
        service.unwrap(key_id, wrapped)
    with pytest.raises(KeyShreddedError):
        service.wrap(key_id, os.urandom(32))
    assert not fake.keys, "wrapping with a destroyed key must not bring the key back"
    service.destroy(key_id)  # destroying twice is harmless


def test_vault_service_needs_a_token_and_a_valid_key_reference():
    with pytest.raises(KeyServiceConfigError):
        VaultTransitKeyEncryptionService("http://vault:8200", "")
    service, _ = _vault()
    with pytest.raises(Exception):  # noqa: B017  (a plaintext secret is rejected as a key reference)
        service.wrap("not-a-vault-uri", os.urandom(32))


def test_the_backend_is_chosen_by_the_settings_and_a_half_configured_one_fails_closed():
    def cfg(**kw):
        return SimpleNamespace(
            key_service_backend=kw.get("backend", "memory"),
            pii_master_key=kw.get("master", ""),
            vault_addr="http://vault:8200",
            vault_token=kw.get("token", ""),
            vault_transit_mount="transit",
        )

    assert isinstance(build_key_service(cfg()), InMemoryKeyEncryptionService)
    assert isinstance(build_key_service(cfg(backend="env", master=MASTER)), EnvKeyEncryptionService)
    assert isinstance(build_key_service(cfg(backend="vault", token="s.x")), VaultTransitKeyEncryptionService)
    with pytest.raises(KeyServiceConfigError):
        build_key_service(cfg(backend="env"))  # no master key
    with pytest.raises(KeyServiceConfigError):
        build_key_service(cfg(backend="vault"))  # no token
    with pytest.raises(KeyServiceConfigError):
        build_key_service(cfg(backend="kms"))


PROD = {"app_env": "production", "secret_key": "x" * 48}


def test_production_refuses_to_start_with_keys_that_vanish_on_restart():
    with pytest.raises(ValueError, match="KEY_SERVICE_BACKEND=memory"):
        Settings(_env_file=None, **PROD)
    env_ok = Settings(_env_file=None, key_service_backend="env", pii_master_key=MASTER, **PROD)
    assert env_ok.key_service_backend == "env"
    assert Settings(_env_file=None, key_service_backend="vault", **PROD).key_service_backend == "vault"
    with pytest.raises(ValueError, match="key_service_backend must be"):
        Settings(_env_file=None, key_service_backend="kms", app_env="dev")
    assert Settings(_env_file=None, app_env="dev").key_service_backend == "memory"
