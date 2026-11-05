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

    # Files: uploads, rendered pages and cropped figures. Mounted as a Docker volume in production.
    data_dir: str = "data"

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
