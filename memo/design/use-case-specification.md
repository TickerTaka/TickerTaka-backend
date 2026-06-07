# 유스케이스 명세서

## 문서 목적

TickerTaka 백엔드의 핵심 사용자 흐름을 평가/발표 기준으로 설명한다.  
기준 코드는 `app/api/`, `app/domain/`, `app/agents/` 이다.

## 액터

- 일반 사용자
- 프론트엔드 클라이언트
- 백엔드 API
- 외부 데이터 소스
  - DART
  - PyKRX / yfinance
  - 뉴스 소스
  - 토론 LLM (settings 기반 모델, 기본 `gpt-4o-mini` / 현재 OpenAI 직접)
  - RAGAS 평가 LLM (현재 OpenRouter sLLM `gpt-oss-120b:free` — 1차 구현, 변경 가능)
- 보조 인프라
  - PostgreSQL
  - Redis
  - ChromaDB

## UC-01. 종목 검색

- 목표: 사용자가 종목코드 또는 종목명을 검색한다.
- 진입점: `GET /api/tickers`
- 주요 입력: `q`, `limit`
- 성공 결과: 검색 결과 목록 반환
- 관련 코드:
  - `app/api/market_data.py`
  - `app/models/ticker.py`

## UC-02. 관심종목 추가

- 목표: 사용자가 관심종목을 추가한다.
- 진입점: `POST /api/watchlists`
- 주요 입력: `user_id`, `symbol`, `memo`
- 성공 결과:
  - watchlist row 생성
  - background sync enqueue
- 후속 처리:
  - 뉴스 수집
  - 재무 수집
  - 가격/기술지표 수집
  - 공시 수집 및 인덱싱
  - valuation 보정
- 관련 코드:
  - `app/api/watchlist.py`
  - `app/domain/watchlist_service.py`
  - `app/domain/news_ingestion.py`
  - `app/domain/financial_ingestion.py`
  - `app/domain/price_ingestion.py`
  - `app/domain/filing_ingestion.py`

## UC-03. 관심종목 피드 조회

- 목표: 사용자가 관심종목 기반 뉴스/공시 피드를 본다.
- 진입점: `GET /api/watchlists/{user_id}/feed`
- 성공 결과: 뉴스와 공시를 통합 정렬한 피드 반환
- 관련 코드:
  - `app/api/watchlist.py`
  - `app/models/cache.py`

## UC-04. 종목 상세 조회

- 목표: 사용자가 개별 종목의 가격/재무/기술지표를 확인한다.
- 진입점: `GET /api/stocks/{symbol}`
- 성공 결과:
  - 최신 가격
  - 최신 재무 스냅샷
  - 최신 기술지표
- 관련 코드:
  - `app/api/market_data.py`
  - `app/models/cache.py`

## UC-05. 개별 종목 뉴스/공시 조회

- 목표: 사용자가 종목별 최신 뉴스와 공시를 확인한다.
- 진입점:
  - `GET /api/stocks/{symbol}/news`
  - `GET /api/stocks/{symbol}/filings`
- 성공 결과: 저장된 캐시 기반 목록 반환
- 관련 코드:
  - `app/api/market_data.py`
  - `app/models/cache.py`

## UC-06. AI 토론 시작

- 목표: 사용자가 특정 종목에 대해 AI 토론을 실행한다.
- 진입점: `POST /api/debates`
- 주요 입력:
  - `user_id`
  - `symbol`
  - `category`
  - `avg_price` (선택)
- 성공 결과:
  - debate session 생성
  - bull / bear / moderator 토론 수행
  - statements 및 summary 저장
- 관련 코드:
  - `app/api/debate.py`
  - `app/domain/debate_service.py`
  - `app/agents/debate_graph.py`
  - `app/agents/nodes/*.py`

## UC-07. AI 토론 결과 조회

- 목표: 사용자가 토론 결과를 다시 본다.
- 진입점:
  - `GET /api/debates`
  - `GET /api/debates/{session_id}`
- 성공 결과:
  - 세션 메타데이터
  - summary
  - statements
- 관련 코드:
  - `app/api/debate.py`
  - `app/models/debate.py`

## UC-08. 토론 품질 사후 평가

- 목표: 토론 종료 후 요약/근거 품질을 사후 평가한다.
- 트리거: moderator summary 저장 직후 백그라운드 태스크
- 현재 구현 (**RAGAS 1차 구현 — 메트릭/모델/영속화 변경 가능**):
  - summary faithfulness
  - evidence context precision
  - ※ 결과는 현재 로그만(영속화·배치·리포트 미완)
- 관련 코드:
  - `app/agents/nodes/moderator_node.py`
  - `app/domain/debate_evaluation.py`

## 비기능 요구

- Redis 장애 시 핵심 토론 경로는 fail-soft 유지
- ChromaDB 장애 시 일부 evidence retrieval은 PG fallback 허용
- PostgreSQL은 단일 SOT
- Redis/Chroma는 재생성 가능한 보조 저장소
- 토론 API는 최종적으로 일관된 session 저장 결과를 반환해야 함
