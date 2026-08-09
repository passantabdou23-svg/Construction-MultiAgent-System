"""Environment-driven application settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CONSTRUCTION_",
        extra="ignore",
    )

    database_path: str = "construction_mas.db"
    ollama_model: str = "llama3.1"
    rag_collection_name: str = "construction-standards"
    maximum_site_note_characters: int = Field(default=4_000, ge=100, le=20_000)


settings = AppSettings()
