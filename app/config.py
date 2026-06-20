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
    
    analysis_provider: str = Field(default="local_hf", alias="ANALYSIS_PROVIDER")
    analysis_model: str = Field(default="snunlp/KR-FinBert-SC", alias="ANALYSIS_MODEL")
    analysis_enabled: bool = Field(default=True, alias="ANALYSIS_ENABLED")
    analysis_max_chars: int = Field(default=6000, alias="ANALYSIS_MAX_CHARS")
    analysis_prompt_version: str = Field(default="evidence-analysis-v2", alias="ANALYSIS_PROMPT_VERSION")
    analysis_summary_provider: str = Field(default="extractive", alias="ANALYSIS_SUMMARY_PROVIDER")
    analysis_generation_model: str | None = Field(default=None, alias="ANALYSIS_GENERATION_MODEL")
    # Qwen 서빙 백엔드: transformers(인-프로세스 model.generate) | remote(OpenAI 호환 서빙).
    # remote 면 Ollama(http://localhost:11434/v1) 또는 vLLM 을 base_url 로 가리킨다. 둘 다 OpenAI 호환이라
    # 코드는 동일하고 URL 만 다르다(운영 GPU 확보 시 vLLM URL 로 교체, 코드 변경 없음).
    analysis_generation_backend: str = Field(default="transformers", alias="ANALYSIS_GENERATION_BACKEND")
    analysis_generation_base_url: str | None = Field(default=None, alias="ANALYSIS_GENERATION_BASE_URL")
    analysis_generation_api_key: str = Field(default="EMPTY", alias="ANALYSIS_GENERATION_API_KEY")
    # 비동기 Qwen 보강 워커
    analysis_async_enabled: bool = Field(default=True, alias="ANALYSIS_ASYNC_ENABLED")
    analysis_worker_poll_interval: float = Field(default=2.0, alias="ANALYSIS_WORKER_POLL_INTERVAL")
    analysis_worker_batch_size: int = Field(default=4, alias="ANALYSIS_WORKER_BATCH_SIZE")
    analysis_worker_max_attempts: int = Field(default=3, alias="ANALYSIS_WORKER_MAX_ATTEMPTS")
    
    # 뉴스 Qwen 게이팅: FinBERT 비-neutral 또는 |impact|>=임계일 때만 보강
    analysis_news_qwen_min_impact: int = Field(default=1, alias="ANALYSIS_NEWS_QWEN_MIN_IMPACT")
    
    # Langfuse 트레이싱 (sLLM 분석 경로 관측). 키 2개 + 토글 모두 있어야 활성, 아니면 no-op.
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_BASE_URL")
    langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_TRACING_ENABLED")
    
    rag_hybrid_enabled: bool = Field(default=True, alias="RAG_HYBRID_ENABLED")
    rag_rrf_k: int = Field(default=60, alias="RAG_RRF_K")
    rag_lexical_candidate_limit: int = Field(default=40, alias="RAG_LEXICAL_CANDIDATE_LIMIT")
    
    # Reranker는 opt-in. 코드 기본값은 False로 두고 환경별 .env에서만 켠다.
    # 이유: cross-encoder(약 2.27GB)는 cold start/메모리/latency 비용이 크고,
    # before/after 정량 개선(context_precision 등)이 검증된 뒤에 운영 전환할 것.
    # 검증 방법: scripts/eval_reranker_ab.py (off/on A/B; --ragas/--judge로 품질 비교).
    # 실측(006360, CPU): 4/4 재정렬·정성상 더 on-topic이나 steady +7.6~13.2s → 인라인 부적합.
    # 켤 경우 GPU 또는 오프라인 배치 한정. 상세: memo/results/2026-06-13-eval-track6b-...md
    rag_reranker_enabled: bool = Field(default=False, alias="RAG_RERANKER_ENABLED")
    rag_reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RAG_RERANKER_MODEL")
    rag_reranker_top_n: int = Field(default=8, alias="RAG_RERANKER_TOP_N")

    # OpenRouter (RAGAS eval + 선택적 debate LLM 프로바이더)
    openrouter_api_key:  str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )

    # Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # 토론 LLM 프로바이더 선택: "openai" | "openrouter" | "anthropic"
    debate_llm_provider: str = Field(default="openai", alias="DEBATE_LLM_PROVIDER")

    # 모델 ID — OpenAI
    bull_model:      str = Field(default="gpt-4o-mini", alias="BULL_MODEL")
    bear_model:      str = Field(default="gpt-4o-mini", alias="BEAR_MODEL")
    moderator_model: str = Field(default="gpt-4o-mini", alias="MODERATOR_MODEL")
    fallback_model:  str = Field(default="gpt-4o-mini", alias="FALLBACK_MODEL")

    # 모델 ID — OpenRouter (DEBATE_LLM_PROVIDER=openrouter 일 때 사용)
    # 기본값: openrouter/owl-alpha (Finance #3, 무료)
    bull_model_openrouter:      str = Field(default="openrouter/owl-alpha", alias="BULL_MODEL_OPENROUTER")
    bear_model_openrouter:      str = Field(default="openrouter/owl-alpha", alias="BEAR_MODEL_OPENROUTER")
    moderator_model_openrouter: str = Field(default="openrouter/owl-alpha", alias="MODERATOR_MODEL_OPENROUTER")
    fallback_model_openrouter:  str = Field(default="google/gemma-4-31b-it:free", alias="FALLBACK_MODEL_OPENROUTER")

    # 모델 ID — Anthropic (DEBATE_LLM_PROVIDER=anthropic 일 때 사용)
    bull_model_anthropic:      str = Field(default="claude-sonnet-4-6", alias="BULL_MODEL_ANTHROPIC")
    bear_model_anthropic:      str = Field(default="claude-sonnet-4-6", alias="BEAR_MODEL_ANTHROPIC")
    moderator_model_anthropic: str = Field(default="claude-sonnet-4-6", alias="MODERATOR_MODEL_ANTHROPIC")
    fallback_model_anthropic:  str = Field(default="claude-haiku-4-5-20251001", alias="FALLBACK_MODEL_ANTHROPIC")
    judge_llm_model_anthropic: str = Field(default="claude-sonnet-4-6", alias="JUDGE_LLM_MODEL_ANTHROPIC")
    
    llm_cache_enabled: bool = Field(default=True, alias="LLM_CACHE_ENABLED")
    llm_cache_ttl_seconds: int = Field(default=86400, alias="LLM_CACHE_TTL_SECONDS")
    
    max_tokens_per_user_per_day: int = Field(default=1000000, alias="MAX_TOKENS_PER_USER_PER_DAY")
    max_debates_per_user_per_day: int = Field(default=20, alias="MAX_DEBATES_PER_USER_PER_DAY")
    
    debate_active_ttl_seconds: int = Field(default=1800, alias="DEBATE_ACTIVE_TTL_SECONDS")
    debate_graph_recursion_limit: int = Field(default=64, alias="DEBATE_GRAPH_RECURSION_LIMIT")
    # 토론 그래프 전체 실행 데드라인(초). 노드 hang/누적 지연 방어 — 초과 시 TimeoutError 로
    # 중단되어 기존 fail-soft(세션 failed 마킹 + 락 해제 + SSE error)로 처리. 0 이면 비활성.
    debate_timeout_seconds: int = Field(default=300, alias="DEBATE_TIMEOUT_SECONDS")
    default_estimated_tokens_per_debate: int = Field(default=12000, alias="DEFAULT_ESTIMATED_TOKENS_PER_DEBATE")
    
    estimated_tokens_financial: int = Field(default=12000, alias="ESTIMATED_TOKENS_FINANCIAL")
    estimated_tokens_technical: int = Field(default=10000, alias="ESTIMATED_TOKENS_TECHNICAL")
    estimated_tokens_market: int = Field(default=9000, alias="ESTIMATED_TOKENS_MARKET")
    estimated_tokens_macro: int = Field(default=9000, alias="ESTIMATED_TOKENS_MACRO")
    estimated_tokens_synthesis: int = Field(default=15000, alias="ESTIMATED_TOKENS_SYNTHESIS")
    
    default_llm_model: str = Field(default="openai/gpt-4o-mini", alias="DEFAULT_LLM_MODEL")
    judge_llm_model:            str = Field(default="gpt-4o-mini",           alias="JUDGE_LLM_MODEL")
    judge_llm_model_openrouter: str = Field(default="openrouter/owl-alpha", alias="JUDGE_LLM_MODEL_OPENROUTER")

    # MCP / Notion publish
    notion_token: str = Field(default="", alias="NOTION_TOKEN")
    notion_database_id: str = Field(default="", alias="NOTION_DATABASE_ID")
    notion_mcp_server_command: str = Field(default="", alias="NOTION_MCP_SERVER_COMMAND")
    notion_mcp_server_args: str = Field(default="", alias="NOTION_MCP_SERVER_ARGS")
    notion_mcp_tool_name: str = Field(default="API-post-page", alias="NOTION_MCP_TOOL_NAME")
    notion_mcp_timeout_seconds: int = Field(default=30, alias="NOTION_MCP_TIMEOUT_SECONDS")

    # 외부 API
    dart_api_key:             str = Field(default="", alias="DART_API_KEY")
    filing_initial_lookback_days: int = Field(default=365, gt=0, alias="FILING_INITIAL_LOOKBACK_DAYS")
    filing_refresh_lookback_days: int = Field(default=30, gt=0, alias="FILING_REFRESH_LOOKBACK_DAYS")
    naver_news_client_id:     str = Field(default="", alias="NAVER_NEWS_CLIENT_ID")
    naver_news_client_secret: str = Field(default="", alias="NAVER_NEWS_CLIENT_SECRET")

    # 관심종목 새로고침 throttle — 같은 종목을 N초 내 재수집하지 않도록 막는다(비용/중복 방어).
    watchlist_refresh_throttle_seconds: int = Field(
        default=600, ge=0, alias="WATCHLIST_REFRESH_THROTTLE_SECONDS"
    )

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
