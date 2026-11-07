"""Check the AI providers declared in .env: which keys work and which models they can use.

    python -m app.ai.check            # ping every configured model, list what each key offers
    python -m app.ai.check --all      # also list every model name the provider serves

Keys are never printed.
"""

import argparse
import asyncio
import sys

import httpx

from app.config import ProviderConfig, get_settings

PING = {"messages": [{"role": "user", "content": "Trả lời đúng một từ: OK"}], "max_tokens": 16}


def configured() -> list[ProviderConfig]:
    s = get_settings()
    out = []
    if s.groq_api_key_list:
        for idx, k in enumerate(s.groq_api_key_list, start=1):
            lbl = f"groq-{idx}" if len(s.groq_api_key_list) > 1 else "groq"
            out.append(ProviderConfig(lbl, s.groq_base_url, k, s._names(s.groq_model)))
    if s.openrouter_api_key:
        out.append(
            ProviderConfig(
                "openrouter",
                s.openrouter_base_url,
                s.openrouter_api_key,
                s._names(s.openrouter_model),
                s._names(s.openrouter_vision_models),
            )
        )
    return out + s.declared_providers()


async def check(provider: ProviderConfig, list_all: bool) -> list[str]:
    lines = [f"[{provider.name}] {provider.base_url}"]
    if not provider.api_key:
        return [*lines, f"  chưa có khoá: thêm {provider.name.upper()}_API_KEY vào .env"]
    url = provider.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=60, headers=provider.headers) as client:
        try:
            response = await client.get(f"{url}/models")
            names = sorted(m.get("id", "") for m in response.json().get("data", [])) if response.is_success else []
            if response.status_code in (401, 403):
                return [*lines, f"  khoá bị từ chối ({response.status_code})"]
        except (httpx.HTTPError, ValueError):
            names = []
        if names:
            lines.append(f"  {len(names)} model khả dụng")
            if list_all:
                lines += [f"    {n}" for n in names]
        for model in dict.fromkeys(provider.models + provider.vision_models):
            try:
                response = await client.post(f"{url}/chat/completions", json={"model": model, **PING})
                if response.is_success:
                    lines.append(f"  OK    {model}")
                else:
                    lines.append(f"  LỖI   {model}: {response.status_code} {response.text[:120]}")
            except httpx.HTTPError as exc:
                lines.append(f"  LỖI   {model}: {exc.__class__.__name__}")
    return lines


async def run(list_all: bool) -> None:
    providers = configured()
    if not providers:
        print("Chưa khai báo nhà cung cấp nào (GROQ_API_KEY, OPENROUTER_API_KEY, AI_PROVIDERS…)")
        return
    for lines in await asyncio.gather(*(check(p, list_all) for p in providers)):
        print("\n".join(lines))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m app.ai.check")
    parser.add_argument("--all", action="store_true", help="list every model each provider serves")
    asyncio.run(run(parser.parse_args().all))


if __name__ == "__main__":
    main()
