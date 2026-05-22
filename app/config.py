# app/config.py
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_name: str = Field(default="TickerTaka Backend", alias="APP_NAME")

    # DB
    database_url: str = Field(
        default="postgresql+asyncpg://stock_user:tickertaka@101.79.19.53:5432/stock_debate",
        alias="DATABASE_URL",
    )
    redis_url:   str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    chroma_url:  str = Field(default="http://localhost:8080",    alias="CHROMA_URL")
    chroma_token: str = Field(default="",                        alias="CHROMA_TOKEN")
    embedding_provider: str = Field(default="huggingface", alias="EMBEDDING_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    embedding_model: str = Field(default="jhgan/ko-sroberta-multitask", alias="EMBEDDING_MODEL")

    # OpenRouter
    openrouter_api_key:  str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )

    # 무료 모델 ID
    bull_model:      str = Field(default="meta-llama/llama-3.3-70b-instruct:free", alias="BULL_MODEL")
    bear_model:      str = Field(default="meta-llama/llama-3.3-70b-instruct:free", alias="BEAR_MODEL")
    moderator_model: str = Field(default="deepseek/deepseek-r1:free",              alias="MODERATOR_MODEL")
    fallback_model:  str = Field(default="openrouter/free",                        alias="FALLBACK_MODEL")

    # 외부 API
    dart_api_key:             str = Field(default="", alias="DART_API_KEY")
    naver_news_client_id:     str = Field(default="", alias="NAVER_NEWS_CLIENT_ID")
    naver_news_client_secret: str = Field(default="", alias="NAVER_NEWS_CLIENT_SECRET")

    # 인증
    jwt_secret:       str = Field(default="dev-secret-key-min-32-characters!!", alias="JWT_SECRET")
    jwt_expire_hours: int = Field(default=24, alias="JWT_EXPIRE_HOURS")

    @field_validator("openrouter_api_key")
    @classmethod
    def warn_missing_key(cls, v):
        if not v:
            import warnings
            warnings.warn("OPENROUTER_API_KEY 미설정 — LLM 기능 비활성화됩니다")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
