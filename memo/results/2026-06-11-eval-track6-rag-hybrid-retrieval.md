# 2026-06-11 Eval Track6 — RAG Hybrid Retrieval 1차 구현

## 1. 목적

평가 기준의 `RAG 고도화` 항목 대응으로, 기존 **Chroma vector-only retrieval** 경로를 **BM25 + vector + RRF** 기반의 hybrid retrieval로 보강했다.

목표는 다음과 같다.

- 뉴스/공시 근거 검색의 관련도 향상
- score 스케일이 다른 retrieval 결과를 직접 합산하지 않고 rank-based fusion으로 결합
- 추후 reranker를 얹을 수 있는 구조까지 확보

## 2. 변경 범위

### 2-1. retrieval 본체

수정 파일:
- [app/domain/evidence_retrieval.py](/home/syt07203/TickerTaka-backend/app/domain/evidence_retrieval.py:1)

주요 변경:
- 기존 `search_symbol_evidence()`는
  - news vector hit
  - filing vector hit
  를 Chroma distance 기준으로 단순 merge/sort 했다.
- 현재는 아래 순서로 동작한다.
  1. news vector retrieval
  2. filing vector retrieval
  3. news lexical retrieval(BM25)
  4. filing lexical retrieval(BM25)
  5. `RRF`로 네 랭킹을 rank-based fusion
  6. 필요 시 reranker 적용

### 2-2. lexical 후보 생성

lexical retrieval은 PostgreSQL cache row를 직접 사용한다.

- 뉴스: `title + summary`
- 공시: `filing_title + summary + content`

토큰화는 한글/영문/숫자 기준의 단순 regex tokenizer로 처리했다.

### 2-3. reranker 준비

기본값은 `off`지만, 설정만으로 켤 수 있게 준비했다.

- `sentence-transformers`는 이미 의존성에 존재
- `CrossEncoder` 기반 reranker를 선택적으로 사용
- 실패 시 자동으로 RRF 결과로 폴백
- 모델 로딩은 `lru_cache` 싱글톤으로 묶어 **호출마다 재로딩하지 않도록** 정리

## 3. 설정값

수정 파일:
- [app/config.py](/home/syt07203/TickerTaka-backend/app/config.py:1)
- [.env.example](/home/syt07203/TickerTaka-backend/.env.example:1)

추가된 env:

```env
RAG_HYBRID_ENABLED=true
RAG_RRF_K=60
RAG_LEXICAL_CANDIDATE_LIMIT=40
RAG_RERANKER_ENABLED=false
RAG_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RAG_RERANKER_TOP_N=8
```

현재 권장 기본값:
- hybrid: `on`
- reranker: `off`

즉 현재 운영 기본은 **BM25 + vector + RRF**이고, reranker는 후속 고도화 단계에서 켜는 방식이다.

## 4. 관련 수정

수정 파일:
- [app/agents/tools/evidence_tools.py](/home/syt07203/TickerTaka-backend/app/agents/tools/evidence_tools.py:1)
- [scripts/validate_evidence_retrieval.py](/home/syt07203/TickerTaka-backend/scripts/validate_evidence_retrieval.py:1)

정리:
- evidence tool 설명을 hybrid retrieval 기준으로 수정
- retrieval 검증 스크립트는 hybrid 결과를 기준으로
  - `NEWS`, `DART` 양쪽 hit 확인
  - `rank`, `score_type` 확인
  - score 출력
  하도록 보강

### 4-1. SSE 경로와의 정합 보강

수정 파일:
- [app/agents/nodes/data_node.py](/home/syt07203/TickerTaka-backend/app/agents/nodes/data_node.py:1)

정리:
- `fetch_price_context`, `fetch_financial_context`, `fetch_news_context`, `fetch_filing_context`, `fetch_event_timeline`
  5개를 `asyncio.gather(...)`로 병렬화
- hybrid retrieval 본체인 `search_evidence_for_symbol(...)`는
  `await asyncio.to_thread(...)`로 분리

이로써 hybrid retrieval이 SSE 이벤트 루프를 직접 블로킹하지 않도록 보강했다.

## 5. 검증

### 5-1. 정적 검증

실행:

```bash
python3 -m py_compile \
  app/domain/evidence_retrieval.py \
  app/agents/tools/evidence_tools.py \
  app/config.py \
  scripts/validate_evidence_retrieval.py
```

결과:
- 통과

### 5-2. retrieval 스모크 검증

실행:

```bash
python -m scripts.validate_evidence_retrieval
```

사용자 확인 결과:

```text
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
{'symbol': '000020', 'hit_count': 2, 'source_types': ['DART', 'NEWS'], 'top_titles': ['테스트 뉴스 제목', '분기보고서 제출'], 'scores': [0.016393, 0.016393]}
```

해석:
- `hit_count=2` 확인
- `source_types=['DART','NEWS']` 확인
- news/filing 양쪽 retrieval path가 hybrid 결과에 반영됨
- `scores`는 RRF 이후 점수로 출력됨
- `rank`, `score_type='rrf'` 메타를 통해 점수 의미 혼선을 줄였다

telemetry warning은 검증 실패가 아니라 부가 로깅 문제로 보이며, retrieval 자체는 정상 동작했다.

## 6. 판정

이번 단계로 다음을 달성했다.

- vector-only retrieval에서 hybrid retrieval로 전환
- BM25 lexical 검색 실제 연결
- RRF 기반 fusion 적용
- `rank` / `score_type` 메타 추가로 점수 의미 혼선 완화
- SSE 경로의 retrieval 호출을 `to_thread`로 분리
- reranker 확장 지점 마련
- 검증 스크립트 기준 news + filing hit 확인

따라서 `#6 RAG 고도화`는 **1차 구현 완료**로 볼 수 있다.

## 7. 남은 후속

다음 고도화 후보:

1. reranker를 실제 운영 경로에 켜고 품질 비교
2. retrieval 결과에 대한 정성 평가 샘플 축적
3. category별 query template 정교화
4. lexical 후보군/쿼리 템플릿 튜닝

---

## 8. 검증 (Claude, 2026-06-11)

> `evidence_retrieval.py` 본체 + 소비측(`data_node`/`evidence_tools`/`format_evidence_context`) + 계획서 RAG 섹션(P2, L-3/L-4)과 대조해 정합·충돌·누락을 점검. 이어붙이는 형식.

### G-0. 정상 확인 (green)
- **RRF 정합**: `_fuse_rankings`가 `1/(rrf_k+index)` rank 기반 합산만 수행 — distance/BM25 raw score 직접 합산 없음 → 계획 L250–252("raw 합산 금지, RRF") 준수 ✓.
- **정렬 의미 보존(계획 L261)**: 반환 리스트는 두 모드 모두 **best-first**(vector-only=distance 오름차순, hybrid=RRF 내림차순). 그리고 **소비측이 score로 재정렬하지 않음** — `data_node`(format_evidence_context/news_chunks/initial_evidences 모두 순서대로 사용), `evidence_tools.search_evidence`(그대로 반환), `format_evidence_context`(순회만) → "기존 정렬 의미를 깨지 않음" 충족 ✓.
- **의존성 핀**: `rank-bm25==0.2.2`([[feedback_requirements_pinning]]) + config RAG 6종 타입/기본값 정상 ✓.
- **복원력 향상**: `_safe_query_collection`이 Chroma 실패 시 `{}` → 벡터 0건이어도 **lexical BM25(PG)가 hit 제공** → hybrid가 오히려 **Chroma 장애 graceful degradation**을 강화. `_safe_get_analyses`도 가드.
- **dedup**: `_hit_key`(news_cache_id/filing_cache_id)로 동일 문서가 벡터·lexical 양쪽에 떠도 1건으로 융합(+RRF 가점). reranker 기본 off + 예외 시 RRF 폴백.
- 컴파일 + 스모크(hit_count=2, `DART`+`NEWS`) 확인.

### G-1. [해소] SSE 경로 이벤트루프 블로킹
초기 구현은 `data_agent_node`가 hybrid retrieval 본체를 동기 호출해 SSE 이벤트 루프를 막을 수 있었다.

현재는:
- 5개 cache fetch를 `asyncio.gather(...)`로 병렬화
- `search_evidence_for_symbol(...)`를 `await asyncio.to_thread(...)`로 분리

따라서 hybrid retrieval이 SSE 스트림 하트비트/동시 요청을 직접 블로킹하는 구조는 해소됐다.

### G-2. [해소] `score` 의미 혼선 완화
반환 `score`가 **vector-only=Chroma distance(낮을수록 좋음)** / **hybrid=RRF(높을수록 좋음, ~0.016)** / **reranker on=CrossEncoder logit** 으로 **모드마다 의미·스케일이 다르다.** 현재 소비측은 score 값을 쓰지 않고 순서만 쓰므로 동작엔 문제없으나(=G-0 정렬 보존), 계획 L-3가 경고한 "score 의미 혼선"이 코드에 **잠복**한다.
- 현재는 각 hit에
  - `rank`
  - `score_type` (`distance` / `bm25` / `rrf` / `reranker`)
  를 함께 싣도록 바꿨다.
- 따라서 소비측은 score raw 값이 아니라 **rank 우선**, 필요 시 `score_type`을 보고 해석할 수 있다.

### G-3. [해소] reranker `CrossEncoder` 호출당 재로딩
초기 구현은 reranker를 켤 경우 `CrossEncoder(...)`를 retrieval마다 새로 생성할 수 있었다.

현재는:
- `_get_reranker(model_name)`를 `@lru_cache` 싱글톤으로 제공
- 동일 프로세스 내에서 모델을 재사용

기본 off 정책은 유지하지만, 켜더라도 호출당 모델 재로딩 문제는 제거됐다.

### G-4. [낮] BM25 후보가 `list_by_symbol(...)[:limit]` 슬라이스
lexical 후보는 종목별 상위 `limit`(기본 40)건 슬라이스라, **IDF가 슬라이스 한정**이고 후보 포함 여부가 `list_by_symbol` 정렬에 의존한다. 데이터 규모가 작아 졸프 범위 수용이나, 관련 오래된 문서가 40건 밖이면 누락될 수 있음을 인지.

### G-5. [낮] telemetry warning
`Failed to send telemetry event ... capture() takes 1 positional argument but 3` 는 **chromadb 클라이언트 posthog 버전 불일치**(임베디드 텔레메트리)로 retrieval과 무관 — 보고서 §5-2 해석 정확. 소음 제거하려면 클라이언트 `anonymized_telemetry=False`.

### G-6. [해소] 5종 `fetch_*` 순차 await
`data_node`의 price/financial/news/filing/event fetch는 `asyncio.gather(...)`로 병렬화했다.

### G-7. 종합 판정
- **RAG hybrid 1차 구현은 핵심(BM25+vector+RRF)·폴백·핀까지 정합**하고, 계획 L261(정렬 의미 보존)도 충족. 평가 항목9 "BM25 실연결 + RRF" 달성.
- 검증에서 지적된 핵심 보완사항 중
  - `G-1` 이벤트루프 블로킹
  - `G-2` score 의미 혼선
  - `G-3` reranker 재로딩
  - `G-6` 순차 await
  는 코드로 반영 완료했다.
- 남은 것은 G-4/G-5 수준의 저위험 튜닝/운영 메모다. 따라서 트랙 판정은 **"RAG hybrid 1차 구현 + 핵심 런타임 보완 완료"**로 보는 것이 정확하다.

---

## 9. 보완 반영 재검증 (Claude, 2026-06-11)

> G-1/G-2/G-3/G-6 수정분을 코드 + **런타임 실행**으로 재확인. (py_compile은 import 누락·NameError를 못 잡으므로 실제 import/호출로 검증.)

- **G-1 해소 확인** ✓ — `data_node.py`: 5종 fetch를 `asyncio.gather(...)`(L26–32), retrieval을 `await asyncio.to_thread(search_evidence_for_symbol, query=…, symbol=…, top_k=4)`(L38–43)로 분리. `import asyncio` 존재. `to_thread`의 keyword 전달이 함수의 keyword-only 시그니처와 정합. → async 노드가 더 이상 동기 retrieval로 이벤트루프를 막지 않음.
- **G-6 해소 확인** ✓ — 위 `gather`로 순차 await 제거(G-1과 동일 변경).
- **G-2 해소 확인(런타임)** ✓ — `RetrievedEvidence`에 `rank`/`score_type` 필드 추가, `to_dict`가 포함(None은 생략). `score_type`이 모드별로 정확히 부여됨: `distance`(벡터 hit) / `bm25`(lexical) / `rrf`(_fuse_rankings) / `reranker`(_maybe_rerank). `_assign_ranks`가 두 반환 경로(vector-only·hybrid) 모두에 적용돼 1..N rank 부여. **`_replace_hit`은 `dataclasses.replace` 기반이라 score/score_type/rank만 갱신하고 나머지(분석 메타·cache_id) 전부 보존** — 실제 실행으로 확인(기존 `_replace_score`의 수동 재구성보다 견고, rank 유실 위험 제거). 묵은 `_replace_score` 심볼 없음.
- **G-3 해소 확인** ✓ — `_get_reranker`가 `@lru_cache(maxsize=4)` 싱글톤(`cache_info` 존재 확인), `_maybe_rerank`가 이를 사용. reranker on이어도 프로세스당 1회 로드.
- **import 정합** ✓ — `from dataclasses import dataclass, replace`, `from functools import lru_cache` 모두 존재(py_compile 사각지대였던 부분 — 런타임 import로 NameError 없음 확정).
- **재컴파일** ✓ — `evidence_retrieval / data_node / evidence_tools / config / validate` 5종 `OK_COMPILE`.
- **스모크 재실행 메모**: 사용자 환경의 OpenSSL/httpx 이슈로 full `validate_evidence_retrieval` 재실행은 막혔으나, 위 **핵심 로직(rank/score_type 메타, _replace_hit 필드 보존, reranker 캐시)을 모듈 직접 실행으로 검증**했고, 직전 스모크(hit_count=2, DART+NEWS)가 retrieval 경로 자체는 이미 입증.

**재검증 판정**: G-1/G-2/G-3/G-6 **코드 + 런타임 정합 확인**. 신규 회귀 없음(세션 격리·gather 동시성 안전). 남은 G-4(BM25 후보 슬라이스)·G-5(telemetry 소음)는 저위험. → **#6 RAG hybrid = 1차 구현 + 핵심 런타임 보완 완료**로 닫힘 타당.
