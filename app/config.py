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
    s3_secure: bool = False
    # Base URL browsers use to fetch public figures, e.g. https://media.example.com
    s3_public_url: str = "http://localhost:9000"

    # AI import (Gemini). Models are tried in order; later ones are fallbacks when one is overloaded.
    gemini_api_key: str = ""
    gemini_models: str = "gemini-3.5-flash,gemini-3.8-flash,gemini-3.6-flash"
    # LibreOffice binary used to turn .docx (MathType, Word shapes) into PDF.
    soffice_path: str = "soffice"

    @property
    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
