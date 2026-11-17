"""WAVE-8 hardening: H-04 error codes and the webhook secret policy."""

import pytest

from app.config import get_settings
from app.modules.billing import router as billing_router
from app.platform.errors import _ERROR_HTTP_SPEC, PlatformErrorCode


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (PlatformErrorCode.ATTEMPT_DEADLINE_EXPIRED, 403),
        (PlatformErrorCode.ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED, 409),
        (PlatformErrorCode.OPTIMISTIC_VERSION_CONFLICT, 409),
    ],
)
def test_h04_codes_are_in_the_catalogue(code: PlatformErrorCode, status: int) -> None:
    assert _ERROR_HTTP_SPEC[code][0] == status


def test_every_error_code_has_an_http_spec() -> None:
    assert set(PlatformErrorCode) == set(_ERROR_HTTP_SPEC)


def test_webhook_secret_never_falls_back_to_secret_key_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(settings, "payment_webhook_secret", None, raising=False)
    monkeypatch.setattr(settings, "app_env", "production")
    assert billing_router._resolve_webhook_secret() is None
    monkeypatch.setattr(settings, "app_env", "staging")
    assert billing_router._resolve_webhook_secret() is None
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "dedicated")
    assert billing_router._resolve_webhook_secret() == "dedicated"
    monkeypatch.delenv("PAYMENT_WEBHOOK_SECRET")
    monkeypatch.setattr(settings, "app_env", "dev")
    assert billing_router._resolve_webhook_secret() == settings.secret_key
