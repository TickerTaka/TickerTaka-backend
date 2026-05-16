# TickerTaka-backend

AI 종목 토론 대시보드를 위한 FastAPI 백엔드입니다.

## Quick Start

1. 환경 변수 예시 파일을 확인하고 실제 값으로 `.env`를 작성합니다.
   - `cp .env.example .env`
2. Docker 인프라를 준비합니다.
   - `docker compose up -d`
3. 가상환경을 생성하고 활성화합니다.
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
4. 의존성을 설치합니다.
   - `pip install -r requirements.txt`
5. 데이터베이스 마이그레이션을 적용합니다.
   - `alembic upgrade head`
6. 시드 데이터를 적재합니다.
   - `python -m scripts.seed`
7. 개발 서버를 실행합니다.
   - `uvicorn app.main:app --reload`

## Required API Keys

- DART OpenAPI: `DART_API_KEY`
- Naver News API: `NAVER_NEWS_CLIENT_ID`, `NAVER_NEWS_CLIENT_SECRET`
- OpenRouter: `OPENROUTER_API_KEY`

## Directory Structure

```text
.
├── alembic/
├── app/
│   ├── agents/
│   │   ├── nodes/
│   │   ├── prompts/
│   │   └── tools/
│   ├── api/
│   ├── core/
│   ├── domain/
│   ├── external/
│   ├── models/
│   ├── repositories/
│   └── schemas/
├── docs/
├── memo/
├── scripts/
├── seeds/
└── tests/
    ├── test_agents/
    ├── test_api/
    └── test_repositories/
```

## Notes

- Docker 컨테이너는 PostgreSQL 15, Redis 7, ChromaDB를 사용합니다.
- 기본 애플리케이션 설정은 `app/config.py`에서 관리합니다.
- Day 2부터 SQLAlchemy 모델과 Alembic 설정을 추가합니다.
