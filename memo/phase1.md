# TickerTaka 프로젝트 컨텍스트

## 프로젝트 개요
- 이름: TickerTaka (티커타카)
- 목적: AI 종목 토론 대시보드 — 사용자가 관심 종목을 등록하면 종목별 뉴스/공시/재무/기술지표를 확인하고, 카테고리(기술적/재무/시장)별로 Bull·Bear·Judge AI 에이전트의 3-라운드 토론을 통해 투자 의사결정을 보조받는 서비스
- 팀: 5인 / 기간: 4주
- 현재 1주차, 백엔드 작업 중

## 기술 스택
- Backend: Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic
- DB: PostgreSQL 15 (Docker), Redis (Docker), ChromaDB (Docker)
- LLM: Openrouter API 경유 (GPT-4o-mini 메인, Claude Haiku Judge용)
- 오케스트레이션: LangGraph
- 외부 API: DART OpenAPI, 네이버 뉴스 API, yfinance

## 핵심 설계 원칙
- 3개 도메인: ① 관심 종목 대시보드, ② 종목 상세 분석, ③ 카테고리형 토론
- 토론은 카테고리 3개 고정 (technical, financial, market), 3라운드 고정 (opening, rebuttal, closing)
- 에이전트는 Bull / Bear / Judge 3개 (카테고리별 세분화 안 함, 카테고리는 시스템 메시지 변수로 주입)
- 기본 모드는 Judge 모드 / Moderator 모드는 고급 기능
- 모든 발언에 출처(evidence) 1개 이상 강제 — 시스템 레벨에서 검증
- 마이데이터 미사용, 보유 수량·평단가 입력 받지 않음 (관심 종목 + 메모만)

## 현재 진행 상태
- WSL Ubuntu 환경
- GitHub 레포 클론 완료: ~/TickerTaka-backend
- Git SSH 인증 설정 완료
- Git user.name, user.email 설정 완료
- 다음 작업: 디렉터리 구조 생성부터 첫 푸시까지

## 이번 세션 작업 (Day 1 마무리)
다음을 순서대로 진행해줘:

1. 디렉터리 구조 생성
   - app/{api, core, domain, repositories, models, schemas, agents/nodes, agents/prompts, agents/tools, external}
   - alembic/versions
   - seeds, tests/{test_api, test_repositories, test_agents}, scripts, docs
   - 각 빈 폴더에 .gitkeep
   - 기본 파일: README.md, .env.example, .gitignore, docker-compose.yml, requirements.txt, pyproject.toml
   - app/__init__.py, app/main.py, app/config.py

2. .gitignore 작성
   - .env, __pycache__, .venv, pgdata/, chromadata/, IDE 파일들 등 표준 항목

3. .env.example 작성 (실제 .env는 사용자가 채워야 하므로 만들지 말 것)
   포함 항목:
   - DATABASE_URL=postgresql://dev:devpass@localhost:5432/tickertaka
   - REDIS_URL, CHROMA_URL
   - DART_API_KEY, NAVER_NEWS_CLIENT_ID, NAVER_NEWS_CLIENT_SECRET
   - OPENROUTER_API_KEY, OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
   - DEFAULT_LLM_MODEL=openai/gpt-4o-mini
   - JUDGE_LLM_MODEL=anthropic/claude-haiku-4-5
   - JWT_SECRET, JWT_EXPIRE_HOURS=24

4. docker-compose.yml 작성
   - postgres:15-alpine (DB: tickertaka, user: dev, pw: devpass, port: 5432, healthcheck 포함)
   - redis:7-alpine (port: 6379, healthcheck 포함)
   - chromadb/chroma:latest (port: 8000)
   - volumes로 pgdata, chromadata 분리
   - 컨테이너 이름은 tickertaka-postgres, tickertaka-redis, tickertaka-chroma
   - restart: unless-stopped

5. requirements.txt 작성
   - fastapi==0.115.0, uvicorn[standard]==0.32.0
   - sqlalchemy==2.0.35, alembic==1.13.3, psycopg2-binary==2.9.9
   - pydantic==2.9.2, pydantic-settings==2.5.2, python-dotenv==1.0.1
   - requests==2.32.3, yfinance==0.2.43, beautifulsoup4==4.12.3, pandas==2.2.3
   - python-jose[cryptography]==3.3.0, passlib[bcrypt]==1.7.4, python-multipart==0.0.12
   - langgraph==0.2.40, langchain-core==0.3.10
   - pytest==8.3.3, pytest-asyncio==0.24.0, httpx==0.27.2

6. README.md 초안 작성
   - 프로젝트 한 줄 소개
   - 빠른 시작 가이드: .env 복사 → docker compose up → venv → pip install → alembic upgrade → seeds → uvicorn
   - 필요한 API 키 안내 (DART, 네이버 뉴스, Openrouter)
   - 디렉터리 구조 설명

7. 작업 완료 후
   - git status 결과를 보여줘
   - git add . 후 다음 메시지로 커밋: "chore: 프로젝트 초기 셋업 - Docker Compose, 디렉터리 구조, 의존성"
   - main 브랜치로 정리 (필요 시 git branch -M main)
   - git push -u origin main

## 작업 시 주의사항
- 파일 생성 전에 항상 현재 상태(ls, git status) 먼저 확인
- 명령어 실행 결과를 보여줘서 검증 가능하게
- Docker 컨테이너는 이 단계에서 띄우지 말 것 (다음 세션에서 .env 채운 후 시작)
- WSL 환경임을 인지하고 경로는 ~/TickerTaka-backend 기준