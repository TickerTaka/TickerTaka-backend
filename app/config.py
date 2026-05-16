from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    app_name: str = "TickerTaka"
    app_env: str = Field(default="development", alias="APP_ENV")

    database_url: str = Field(
        default="postgresql://dev:devpass@localhost:5432/tickertaka",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    chroma_url: str = Field(default="http://localhost:8000", alias="CHROMA_URL")

    dart_api_key: str = Field(default="", alias="DART_API_KEY")
    naver_news_client_id: str = Field(default="", alias="NAVER_NEWS_CLIENT_ID")
    naver_news_client_secret: str = Field(
        default="",
        alias="NAVER_NEWS_CLIENT_SECRET",
    )

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )
    default_llm_model: str = Field(
        default="openai/gpt-4o-mini",
        alias="DEFAULT_LLM_MODEL",
    )
    judge_llm_model: str = Field(
        default="anthropic/claude-haiku-4-5",
        alias="JUDGE_LLM_MODEL",
    )

    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_expire_hours: int = Field(default=24, alias="JWT_EXPIRE_HOURS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
