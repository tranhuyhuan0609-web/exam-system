import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# OpenAI-compatible providers with a free tier (or free credits): AI_PROVIDERS=<name> and <NAME>_API_KEY are enough.
# Model names change often: the defaults are a starting point; `python -m app.ai.check` lists what a key can use,
# and <NAME>_MODELS / <NAME>_VISION_MODELS override them. A model a provider no longer serves is skipped.
PRESETS: dict[str, dict[str, str]] = {
    # Free "Experiment" plan: small/medium are often rate limited, ministral-8b answers (MISTRAL_VISION_MODELS:
    # e.g. pixtral-12b-latest reads images; off by default, free readers misread scans)
    "mistral": {"base_url": "https://api.mistral.ai/v1", "models": "mistral-small-latest,mistral-medium-latest"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "models": "gpt-oss-120b,qwen-3.8-27b"},
    "sambanova": {"base_url": "https://api.sambanova.ai/v1", "models": "Meta-Llama-3.3-70B-Instruct"},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1", "models": "openai/gpt-oss-120b"},
    "cohere": {"base_url": "https://api.cohere.ai/compatibility/v1", "models": "command-a-03-2025"},
    "huggingface": {"base_url": "https://router.huggingface.co/v1", "models": "openai/gpt-oss-120b"},
    "cloudflare": {
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",  # CLOUDFLARE_ACCOUNT_ID
        "models": "@cf/openai/gpt-oss-120b",
    },
    "azure": {"auth": "api-key"},  # AZURE_BASE_URL=https://<resource>.openai.azure.com/openai/v1
}


@dataclass
class ProviderConfig:
    """An OpenAI-compatible chat API declared in .env (AI_PROVIDERS)."""

    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    vision_models: list[str] = field(default_factory=list)
    auth: str = "bearer"  # "api-key" (Azure) or "bearer"

    @property
    def headers(self) -> dict[str, str]:
        if self.auth == "api-key":
            return {"api-key": self.api_key, "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}


class Settings(BaseSettings):
    # extra="allow": the variables of the providers listed in AI_PROVIDERS (AZURE_API_KEY …) are read too
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    app_env: str = "dev"
    database_url: str = "postgresql+asyncpg://exam:exam@localhost:5432/exam"
    secret_key: str = "change-me"
    # Set to true in production (HTTPS) so the session cookie is never sent over plain HTTP.
    session_https_only: bool = False
    session_max_age_seconds: int = 7 * 24 * 3600

    # Files: uploads, rendered pages and cropped figures.
    data_dir: str = "data"
    # "local" keeps files under <data_dir>/media; "s3" uses any S3-compatible store
    # (RustFS in docker-compose, MinIO, AWS S3, Cloudflare R2...). See app/infrastructure/storage.py.
    storage_backend: str = "local"
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = "rustfsadmin"
    s3_secret_key: str = "rustfsadmin"
    s3_bucket: str = "exam-media"
    s3_bronze_bucket: str = "bronze-raw"  # crawler originals (PDF / Word / images), private
    s3_secure: bool = False
    # Base URL browsers use to fetch public figures, e.g. https://media.example.com
    s3_public_url: str = "http://localhost:9000"

    # AI import (Gemini). Supports multiple keys for rotation (comma/semicolon/newline-separated).
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    gemini_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash"
    # Reading exam pages (transcription): cheaper models first. Free quotas are per model, so several also
    # mean more free requests a day. Thinking is billed as output: little of it for transcription.
    gemini_extract_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash"
    gemini_extract_thinking: str = ""
    # LibreOffice binary used to turn .docx (MathType, Word shapes) into PDF.
    soffice_path: str = "soffice"

    @property
    def effective_soffice_path(self) -> str:
        from app.ai.extract_engine import find_soffice
        return find_soffice(self.soffice_path)
    tessdata_dir: str = ""  # Tesseract language data (vie): enables OCR of scanned library PDFs

    # Question crawler (app/pipeline): Kafka carries raw pages, MongoDB keeps them (Bronze).
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_prefix: str = "exam"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "exam_bronze"
    crawler_configs_dir: str = "configs/sites"
    drive_configs_dir: str = "configs/drive"
    google_drive_api_key: str = ""  # Google Cloud API key restricted to the Drive API (public folders)
    # Free accounts (see app/plans.py); Pro and admins are unlimited.
    free_exams_per_day: int = 3
    free_docs_per_day: int = 3
    crawler_user_agent: str = "ExamSystemCrawler/0.1"
    gemini_embedding_model: str = "gemini-embedding-001"
    semantic_duplicate_threshold: float = 0.92  # cosine similarity at which a crawled question is a duplicate
    # Embeddings at quota (large crawls): publish with exact dedup only instead of waiting; vectors come later
    semantic_dedup_optional: bool = True
    # AI fallbacks for crawled questions when Gemini is out of quota (OpenAI-compatible APIs, free tiers).
    groq_api_key: str = ""
    groq_api_keys: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # text models, tried in order: ":free" ones on openrouter.ai/models
    # Models that read images (exam pages, figures), tried in order before Gemini. Off by default: in a trial
    # a free model misread a scanned question ("phần ảo" as "phần thực"). Free lists change; a model that
    # disappears is skipped. E.g. "google/gemma-4-31b-it:free,nvidia/nemotron-3.5-super-vl:free".
    openrouter_vision_models: str = ""
    openrouter_min_interval: float = 3.2  # seconds between calls: free models allow 20 a minute
    ai_providers: str = ""  # more OpenAI-compatible providers (Azure for Students …), see Settings.extra_providers
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Calls per provider and day (0 = no limit of ours; the provider still has its own).
    ai_daily_limits: str = "gemini:55,groq:900,openrouter:45"  # Gemini free tier: ~20/day per model

    # Questions per AI call: Groq's free tier allows ~8K tokens a minute, so its batches are small.
    ai_batch_sizes: str = "gemini:12,groq:3,openrouter:6"

    @property
    def ai_batch_size_map(self) -> dict[str, int]:
        return self._pairs(self.ai_batch_sizes)

    @property
    def ai_daily_limit_map(self) -> dict[str, int]:
        return self._pairs(self.ai_daily_limits)

    @staticmethod
    def _pairs(value: str) -> dict[str, int]:
        out = {}
        for part in value.split(","):
            name, _, number = part.partition(":")
            if name.strip() and number.strip().isdigit():
                out[name.strip()] = int(number)
        return out

    @property
    def gemini_model_list(self) -> list[str]:
        return self._names(self.gemini_models)

    @property
    def gemini_api_key_list(self) -> list[str]:
        raw = f"{self.gemini_api_key},{self.gemini_api_keys}"
        tokens = [k.strip() for k in re.split(r"[,;\s\n]+", raw) if k.strip()]
        return list(dict.fromkeys(tokens))

    @property
    def groq_api_key_list(self) -> list[str]:
        raw = f"{self.groq_api_key},{self.groq_api_keys}"
        tokens = [k.strip() for k in re.split(r"[,;\s\n]+", raw) if k.strip()]
        return list(dict.fromkeys(tokens))

    @property
    def extra_providers(self) -> list["ProviderConfig"]:
        """Providers ready to use: a key and at least one model."""
        return [p for p in self.declared_providers() if p.api_key and (p.models or p.vision_models)]

    def declared_providers(self) -> list["ProviderConfig"]:
        """AI_PROVIDERS=mistral,cerebras,…: any OpenAI-compatible API, declared in .env without code. For each name:
        <NAME>_API_KEY, and optionally <NAME>_BASE_URL, <NAME>_MODELS (text, tried in order), <NAME>_VISION_MODELS
        (they read images), <NAME>_AUTH (bearer, the default; api-key for Azure). Known names (PRESETS) need only the
        key; `python -m app.ai.check` lists the models a key can use. Daily budgets: AI_DAILY_LIMITS=mistral:500."""
        extra = {k.lower(): str(v) for k, v in (self.model_extra or {}).items()}
        out = []
        for name in self._names(self.ai_providers):
            preset = PRESETS.get(name.lower(), {})

            def get(key: str, name: str = name) -> str:
                variable = f"{name}_{key}"
                return os.environ.get(variable.upper()) or extra.get(variable.lower(), "")

            base_url = get("base_url") or preset.get("base_url", "")
            if "{account}" in base_url:  # Cloudflare: the account id is part of the URL
                base_url = base_url.replace("{account}", get("account_id"))
            out.append(
                ProviderConfig(
                    name=name.lower(),
                    base_url=base_url,
                    api_key=get("api_key"),
                    models=self._names(get("models") or preset.get("models", "")),
                    vision_models=self._names(get("vision_models") or preset.get("vision_models", "")),
                    auth=(get("auth") or preset.get("auth", "bearer")).lower(),
                )
            )
        return [p for p in out if p.base_url]

    @staticmethod
    def _names(value: str) -> list[str]:
        return [m.strip() for m in value.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
