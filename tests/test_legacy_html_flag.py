"""LEGACY_HTML_ENABLED=false mounts none of the server-rendered routers; /api/v1 and /health stay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = (
    "import json; from app.main import app; "
    "print(json.dumps(sorted({getattr(r, 'path', '') for r in app.routes} | "
    "set(app.openapi()['paths'])) if False else sorted(app.openapi()['paths'])))"
)


def _paths(flag: str) -> list[str]:
    env = {**os.environ, "LEGACY_HTML_ENABLED": flag, "APP_ENV": "dev"}
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_the_legacy_html_site_is_mounted_by_default_and_not_when_the_flag_is_off():
    on, off = _paths("true"), _paths("false")
    assert any(p.startswith("/admin") or p == "/login" for p in on), "the legacy site is there by default"
    assert not [p for p in off if p.startswith(("/admin", "/teacher", "/plans")) or p in ("/login", "/logout")]
    assert "/api/v1/questions" in off and "/health" in off, "the V11.3 API and the health check stay"
