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
    rag_documents_path: str = "rag_documents"
    rag_data_path: str = "rag_data"
    rag_chunks_path: str = "rag_data/chunks.jsonl"
    rag_index_path: str = "rag_data/chroma"
    rag_top_k: int = Field(default=3, ge=1, le=20)
    rag_minimum_similarity: float = Field(default=0.45, ge=0, le=1)
    rag_semantic_weight: float = Field(default=0.75, ge=0, le=1)
    rag_lexical_weight: float = Field(default=0.25, ge=0, le=1)
    maximum_site_note_characters: int = Field(default=4_000, ge=100, le=20_000)


settings = AppSettings()
