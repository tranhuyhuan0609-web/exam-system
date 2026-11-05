from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # AI import (Gemini). Models are tried in order; later ones are fallbacks when one is overloaded.
    gemini_api_key: str = ""
    gemini_models: str = "gemini-3.5-flash,gemini-3.8-flash,gemini-3.6-flash"
    # LibreOffice binary used to turn .docx (MathType, Word shapes) into PDF.
    soffice_path: str = "soffice"

    # Question crawler (app/pipeline): Kafka carries raw pages, MongoDB keeps them (Bronze).
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_prefix: str = "exam"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "exam_bronze"
    crawler_configs_dir: str = "configs/sites"
    crawler_user_agent: str = "ExamSystemCrawler/0.1"
    gemini_embedding_model: str = "gemini-embedding-001"
    semantic_duplicate_threshold: float = 0.92  # cosine similarity at which a crawled question is a duplicate
    # AI fallbacks for crawled questions when Gemini is out of quota (OpenAI-compatible APIs, free tiers).
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # pick a ":free" model on openrouter.ai/models
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Calls per provider and day (0 = no limit of ours; the provider still has its own).
    ai_daily_limits: str = "gemini:55,groq:900,openrouter:45"  # Gemini free tier: ~20/day per model

    # Questions per AI call: Groq's free tier allows ~8K tokens a minute, so its batches are small.
    ai_batch_sizes: str = "gemini:15,groq:6,openrouter:8"

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
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
