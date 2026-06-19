# TickerTaka-backend

AI 종목 **토론 · 감성분석 대시보드**를 위한 FastAPI 백엔드입니다.

## 주요 기능

- **AI 종목 토론** — LangGraph 기반 bull/bear/moderator 멀티에이전트 토론. 일괄 반환(`POST /api/debates`)과 **실시간 SSE 스트리밍**(`POST /api/debates/sessions` → `GET /api/debates/{id}/stream`) 둘 다 제공. 근거(evidence) 검색·검증·요약까지 수행.
- **RAG 고도화 검색** — 근거 검색은 **BM25 + 벡터 + RRF 하이브리드**(`evidence_retrieval.py`). cross-encoder reranker는 opt-in(`RAG_RERANKER_ENABLED`).
- **토론 품질 평가(RAGAS)** — 요약 faithfulness/answer_relevancy, 근거 context_precision를 사후 평가하고 `debate_eval_result`에 영속화. 배치(`run_ragas_eval.py`) + golden set(10건) + 회귀 테스트 포함.
- **관측성(Langfuse)** — 감성분석 sLLM(Qwen) 분석 경로를 Langfuse로 단계별 trace(`app/core/tracing.py`). 키 2개 + `LANGFUSE_TRACING_ENABLED` 모두 있을 때만 활성, 없으면 자동 no-op.
- **MCP (양방향)** — ① **클라이언트**: 완료된 토론을 Notion MCP 서버의 `API-post-page`로 발행(`POST /api/debates/{id}/publish/notion`). ② **서버**: 공식 `mcp` SDK(FastMCP)로 도메인 기능(종목상세·피드·토론조회/실행)을 tool로 노출(`app/mcp_server.py`) → Claude Desktop 등 외부 MCP 클라이언트가 호출.
- **뉴스/공시 감성·투자분석** — 동기 FinBERT(`snunlp/KR-FinBert-SC`) baseline + 비동기 **Qwen** 보강 워커. Qwen은 **transformers 직접 로드(기본)** 또는 **Ollama/vLLM 원격 서빙**(`ANALYSIS_GENERATION_BACKEND=remote`) 선택. 결과는 관심종목 피드(`WatchlistFeedItem`)에 노출.
- **관심종목 · 시장데이터** — 종목 검색/상세, 가격·재무·기술지표·뉴스·공시 수집(DART / PyKRX / yfinance / Naver News), background sync.

## 아키텍처 개요

| 레이어 | 내용 |
|---|---|
| API | FastAPI (`app/api`) — watchlist / market_data / debate |
| Domain | 수집·인덱싱·토론 서비스·감성분석 (`app/domain`) |
| Agent | LangGraph 토론 그래프 (`app/agents`) |
| Worker | 감성분석 Qwen 비동기 워커 (`app/workers/analysis_worker.py`, 별도 프로세스) |
| Integration | Notion MCP **client** (`app/integrations/notion_mcp.py`) + TickerTaka MCP **server** (`app/mcp_server.py`, FastMCP) |
| Persistence | PostgreSQL(단일 SOT) + SQLAlchemy / Alembic |
| Infra | Redis(lock/checkpoint), ChromaDB(vector index) |

> 설계 문서: `docs/design/` (use-case / component / interface / sequence / ERD)

## 실행 환경 전제

- **PostgreSQL**: NCP 원격 매니지드 DB (`DATABASE_URL`)
- **Redis / ChromaDB**: 개발자 로컬 Docker
- **Notion MCP 서버**: 호스트 로컬 설치(`.notion-mcp/`) — 컨테이너에서는 비활성

## Quick Start (호스트 직접 실행)

```bash
cp .env.example .env          # 값 채우기 (아래 키 참고)
docker compose up -d redis chroma
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head          # NCP DB에 스키마 적용
uvicorn app.main:app --reload
```

감성분석 Qwen 보강 워커(선택, 별도 프로세스):

```bash
python -m app.workers.analysis_worker
```

> 워커가 없어도 FinBERT baseline 감성은 인덱싱 시 즉시 제공됩니다. Qwen 구조화 보강만 워커가 후처리합니다.

## Docker (app 컨테이너)

```bash
docker compose up -d redis chroma
docker compose up --build app
docker compose ps
curl http://127.0.0.1:8000/health     # {"status":"ok"}
```

- 컨테이너에서는 `REDIS_URL` / `CHROMA_URL`을 서비스명으로 override(`redis:6379`, `chroma:8000`).
- `DATABASE_URL`은 컨테이너에서도 NCP 원격을 그대로 사용.
- 로컬 대체 postgres는 `--profile local-db`일 때만 기동(`docker compose --profile local-db up postgres`).
- 컨테이너에서는 Notion MCP publish를 의도적으로 비활성(호스트 로컬 경로 의존).

## Notion 발행(MCP) 설정 (호스트)

```bash
npm install --prefix .notion-mcp @notionhq/notion-mcp-server
```

`.env`:

```env
NOTION_TOKEN=ntn_...
NOTION_DATABASE_ID=<토론 기록 DB id>
NOTION_MCP_SERVER_COMMAND=<절대경로>/.notion-mcp/node_modules/.bin/notion-mcp-server
NOTION_MCP_SERVER_ARGS=
NOTION_MCP_TOOL_NAME=API-post-page
```

> Notion DB는 `Name`(Title) / `Session ID` / `Symbol` / `Category`(Select) / `Created At`(Date) / `Published At`(Date) 속성으로 만들고, integration을 해당 DB에 **연결(공유)** 해야 합니다.

## MCP 서버 (도구 제공, 선택)

도메인 기능을 MCP tool로 노출해 Claude Desktop 등 외부 MCP 클라이언트가 호출하게 한다(`app/mcp_server.py`, FastMCP).

```bash
python -m app.mcp_server          # stdio MCP 서버 기동
# 또는 GUI 점검:  mcp dev app/mcp_server.py   (MCP Inspector)
```

Claude Desktop 연결(`claude_desktop_config.json`, WSL 서버 spawn 예시):

```json
{
  "mcpServers": {
    "tickertaka": {
      "command": "wsl.exe",
      "args": ["bash", "-lc", "cd ~/TickerTaka-backend && source venv/bin/activate && exec python -m app.mcp_server"]
    }
  }
}
```

> tool: `list_available_symbols`(데이터 있는 종목) / `get_stock_detail` / `get_watchlist_feed` / `list_debates` / `get_debate` / `start_debate`. 토론·조회는 **수집된 종목만** 의미 있으므로 `list_available_symbols`로 먼저 확인.

## 환경 변수 (주요 키)

| 키 | 용도 |
|---|---|
| `DATABASE_URL` | NCP PostgreSQL |
| `REDIS_URL`, `CHROMA_URL` | 로컬 인프라(호스트 localhost / 컨테이너 서비스명) |
| `OPENAI_API_KEY` | 토론 LLM(bull/bear/moderator) |
| `OPENROUTER_API_KEY` | RAGAS 평가 sLLM |
| `DART_API_KEY` | 공시 |
| `NAVER_NEWS_CLIENT_ID/SECRET` | 뉴스 |
| `ANALYSIS_MODEL`, `ANALYSIS_GENERATION_MODEL` | 감성 baseline(FinBERT) / Qwen 보강 |
| `ANALYSIS_GENERATION_BACKEND`, `ANALYSIS_GENERATION_BASE_URL`, `ANALYSIS_GENERATION_API_KEY` | Qwen 서빙 백엔드(`transformers`\|`remote`). remote면 Ollama(`http://localhost:11434/v1`)/vLLM URL |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `LANGFUSE_TRACING_ENABLED` | Langfuse 트레이싱(선택, 없으면 no-op) |
| `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `NOTION_MCP_*` | Notion MCP 발행 |

전체 항목은 `.env.example` 참고.

## Directory Structure

```text
.
├── alembic/versions/        # DB 마이그레이션
├── app/
│   ├── agents/              # LangGraph 토론 그래프/노드
│   ├── api/                 # FastAPI 라우터
│   ├── core/                # db/redis/llm_factory/guard
│   ├── domain/              # 수집·인덱싱·토론·감성분석 서비스
│   ├── external/            # DART/KRX/Chroma/스크래퍼 클라이언트
│   ├── integrations/        # Notion MCP client
│   ├── models/              # SQLAlchemy 모델
│   ├── repositories/        # 데이터 접근
│   ├── schemas/             # Pydantic 스키마
│   └── workers/             # 감성분석 Qwen 비동기 워커
├── docs/  ·  memo/  ·  scripts/  ·  seeds/  ·  tests/
├── docker-compose.yml  ·  Dockerfile  ·  .dockerignore
└── run_ragas_eval.py        # RAGAS 배치 평가
```

## 테스트 / 평가

```bash
pytest                                            # 단위/통합
pytest tests/test_agents/test_ragas_regression.py # RAGAS 회귀(실 LLM 호출, 느림)
python run_ragas_eval.py                          # RAGAS 배치 → reports/ragas-<sha>.json
```
