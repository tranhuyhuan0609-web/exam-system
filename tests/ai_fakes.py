"""An AI solver that answers from a script, over an offline httpx transport (no key, no network leaves the test).

`fake_solver({"Dao ham": {"answer": "A", ...}})` answers the first scripted reply whose needle is in the prompt, in the
Gemini `generateContent` shape; a prompt no needle matches gets `{"answer": null}`. `calls` records (key, prompt).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.infrastructure.ai_solver import AiSolver


def ai_settings(**overrides: Any) -> Settings:
    """The app settings with only the fake Gemini keys as providers (Groq / OpenRouter off)."""
    base = {
        "gemini_api_key": "fake-gemini-key-1",
        "gemini_api_keys": "",
        "gemini_models": "gemini-fake",
        "groq_api_key": "",
        "groq_api_keys": "",
        "openrouter_api_key": "",
        "openrouter_model": "",
    }
    return get_settings().model_copy(update={**base, **overrides})


def no_ai_settings() -> Settings:
    return ai_settings(gemini_api_key="")


class TxWatch:
    """Counts the open `tenant_transaction`s, so a test can prove none is open while the AI or the web runs."""

    def __init__(self, real: Callable[..., Any]) -> None:
        self.real = real
        self.open = 0
        self.opened = 0
        self.io_inside_transaction = 0

    def probe(self) -> None:
        """Called by a fake network / AI on every request."""
        if self.open:
            self.io_inside_transaction += 1

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def watched() -> Any:
            async with self.real(*args, **kwargs) as session:
                self.open += 1
                self.opened += 1
                try:
                    yield session
                finally:
                    self.open -= 1

        return watched()


def watch_transactions(monkeypatch: Any, *modules: Any) -> TxWatch:
    """Wrap `tenant_transaction` as imported by each module (its internal service, usually)."""
    from app.platform import tenancy

    watch = TxWatch(tenancy.tenant_transaction)
    for module in modules:
        monkeypatch.setattr(module, "tenant_transaction", watch)
    return watch


class FakeGemini:
    def __init__(self, script: Mapping[str, Mapping[str, Any]], *, refuse_keys: tuple[str, ...] = ()) -> None:
        self.script = dict(script)
        self.refuse_keys = refuse_keys
        self.calls: list[tuple[str, str]] = []
        self.image_counts: list[int] = []  # pictures sent with each call
        self.watch: TxWatch | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.watch is not None:
            self.watch.probe()
        key = request.url.params.get("key", "")
        parts = json.loads(request.content)["contents"][0]["parts"]
        prompt = parts[0]["text"]
        self.image_counts.append(len(parts) - 1)
        self.calls.append((key, prompt))
        if key in self.refuse_keys:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        reply: Mapping[str, Any] = {"answer": None}
        for needle, scripted in self.script.items():
            if needle in prompt:
                reply = scripted
                break
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]})


def fake_solver(
    script: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    settings: Settings | None = None,
    refuse_keys: tuple[str, ...] = (),
    numeric_checker: Callable[[str, str], Any] | None = None,
) -> tuple[AiSolver, FakeGemini]:
    fake = FakeGemini(script or {}, refuse_keys=refuse_keys)
    solver = AiSolver(settings or ai_settings(), transport=httpx.MockTransport(fake), numeric_checker=numeric_checker)
    return solver, fake
