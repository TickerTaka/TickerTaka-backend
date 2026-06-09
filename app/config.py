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
        default="postgresql+asyncpg://USERNAME:PASSWORD@HOST:5432/DBNAME",
        alias="DATABASE_URL",
    )
    redis_url:   str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    chroma_url:  str = Field(default="http://localhost:8080",    alias="CHROMA_URL")
    chroma_token: str = Field(default="",                        alias="CHROMA_TOKEN")
    embedding_provider: str = Field(default="huggingface", alias="EMBEDDING_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    embedding_model: str = Field(default="jhgan/ko-sroberta-multitask", alias="EMBEDDING_MODEL")

    # OpenRouter (하위 호환용 — 더 이상 LLM 호출에 사용하지 않음)
    openrouter_api_key:  str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )

    # 모델 ID (GPT-4o-mini)
    bull_model:      str = Field(default="gpt-4o-mini", alias="BULL_MODEL")
    bear_model:      str = Field(default="gpt-4o-mini", alias="BEAR_MODEL")
    moderator_model: str = Field(default="gpt-4o-mini", alias="MODERATOR_MODEL")
    fallback_model:  str = Field(default="gpt-4o-mini", alias="FALLBACK_MODEL")
    llm_cache_enabled: bool = Field(default=True, alias="LLM_CACHE_ENABLED")
    llm_cache_ttl_seconds: int = Field(default=86400, alias="LLM_CACHE_TTL_SECONDS")
    max_tokens_per_user_per_day: int = Field(default=1000000, alias="MAX_TOKENS_PER_USER_PER_DAY")
    max_debates_per_user_per_day: int = Field(default=20, alias="MAX_DEBATES_PER_USER_PER_DAY")
    debate_active_ttl_seconds: int = Field(default=1800, alias="DEBATE_ACTIVE_TTL_SECONDS")
    debate_graph_recursion_limit: int = Field(default=64, alias="DEBATE_GRAPH_RECURSION_LIMIT")
    default_estimated_tokens_per_debate: int = Field(default=12000, alias="DEFAULT_ESTIMATED_TOKENS_PER_DEBATE")
    estimated_tokens_financial: int = Field(default=12000, alias="ESTIMATED_TOKENS_FINANCIAL")
    estimated_tokens_technical: int = Field(default=10000, alias="ESTIMATED_TOKENS_TECHNICAL")
    estimated_tokens_market: int = Field(default=9000, alias="ESTIMATED_TOKENS_MARKET")
    estimated_tokens_macro: int = Field(default=9000, alias="ESTIMATED_TOKENS_MACRO")
    estimated_tokens_synthesis: int = Field(default=15000, alias="ESTIMATED_TOKENS_SYNTHESIS")
    default_llm_model: str = Field(default="openai/gpt-4o-mini", alias="DEFAULT_LLM_MODEL")
    judge_llm_model: str = Field(default="anthropic/claude-haiku-4-5", alias="JUDGE_LLM_MODEL")

    # MCP / Notion publish
    notion_token: str = Field(default="", alias="NOTION_TOKEN")
    notion_database_id: str = Field(default="", alias="NOTION_DATABASE_ID")
    notion_mcp_server_command: str = Field(default="", alias="NOTION_MCP_SERVER_COMMAND")
    notion_mcp_server_args: str = Field(default="", alias="NOTION_MCP_SERVER_ARGS")
    notion_mcp_tool_name: str = Field(default="API-post-page", alias="NOTION_MCP_TOOL_NAME")
    notion_mcp_timeout_seconds: int = Field(default=30, alias="NOTION_MCP_TIMEOUT_SECONDS")

    # 외부 API
    dart_api_key:             str = Field(default="", alias="DART_API_KEY")
    naver_news_client_id:     str = Field(default="", alias="NAVER_NEWS_CLIENT_ID")
    naver_news_client_secret: str = Field(default="", alias="NAVER_NEWS_CLIENT_SECRET")

    # 인증
    jwt_secret:       str = Field(default="dev-secret-key-min-32-characters!!", alias="JWT_SECRET")
    jwt_expire_hours: int = Field(default=24, alias="JWT_EXPIRE_HOURS")

    @field_validator("openai_api_key")
    @classmethod
    def warn_missing_key(cls, v):
        if not v:
            import warnings
            warnings.warn("OPENAI_API_KEY 미설정 — LLM 기능 비활성화됩니다")
        return v

    def estimated_tokens_for_category(self, category: str) -> int:
        mapping = {
            "financial": self.estimated_tokens_financial,
            "technical": self.estimated_tokens_technical,
            "market": self.estimated_tokens_market,
            "macro": self.estimated_tokens_macro,
            "synthesis": self.estimated_tokens_synthesis,
        }
        return mapping.get(category, self.default_estimated_tokens_per_debate)


@lru_cache
def get_settings() -> Settings:
    return Settings()
