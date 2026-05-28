# Filing Chroma 테스트 결과 보고서

> 작성일: 2026-05-27  
> 테스트 대상: 공시 본문 파싱 v2, filing Chroma 청크 인덱싱, RAG 검색 가능성  
> 테스트 종목: `000270` 기아  
> 테스트 환경:
>
> ```text
> PostgreSQL: 원격 stock_debate DB
> ChromaDB: Docker Compose chroma 서비스
> Chroma URL: http://127.0.0.1:8080
> Chroma image: chromadb/chroma:0.5.23
> Chroma storage: docker-compose chromadata volume
> Embedding model: jhgan/ko-sroberta-multitask
> Embedding dimension: 768
> ```

---

## 1. 테스트 목적

이번 테스트의 목적은 단순히 `filing_cache`에 공시 row가 저장되는지 확인하는 것이 아니었다.

검증 목표는 다음 end-to-end 흐름이 실제로 동작하는지 확인하는 것이었다.

```text
워치리스트 종목 추가
  -> DART 공시 메타데이터 수집
  -> DART document.xml 다운로드
  -> 공시 HTML/XML 본문 파싱
  -> 표 구조 보존 텍스트 변환
  -> 섹션별 chunk 생성
  -> 768차원 embedding 생성
  -> Docker Chroma filing 컬렉션 저장
  -> symbol/query 기반 검색
```

즉 토론 RAG가 공시 제목만 보는 상태가 아니라, 실제 공시 본문 chunk를 검색할 수 있는지 확인하는 것이 핵심이었다.

---

## 2. 테스트 전 상태

Docker Chroma는 `docker compose up -d chroma`로 실행했다.

컨테이너 상태:

```text
NAME                IMAGE                    SERVICE   STATUS          PORTS
tickertaka-chroma   chromadb/chroma:0.5.23   chroma    Up              0.0.0.0:8080->8000/tcp
```

heartbeat 확인:

```text
GET http://127.0.0.1:8080/api/v2/heartbeat
```

응답:

```json
{"nanosecond heartbeat":1779867550011531714}
```

브라우저에서 `http://127.0.0.1:8080/`로 접속하면 아래 응답이 나온다.

```json
{"detail":"Not Found"}
```

이는 오류가 아니다. Chroma는 루트 웹 UI를 제공하지 않고 API 서버로 동작한다. 정상 확인 경로는 `/api/v2/heartbeat` 또는 `/docs`다.

테스트 전 `000270`은 워치리스트와 Chroma filing 컬렉션에 없었다.

```text
symbol: 000270
ticker: 기아
watchlist: 없음
Chroma filing 문서: 0개
```

---

## 3. 코드 변경 검증 상태

이번 테스트 전에 아래 코드 변경이 적용된 상태였다.

| 파일 | 변경 내용 |
|---|---|
| `app/external/dart/client.py` | `extract_document_text_v2()`, 표 grid 확장, 표 직렬화, 각주 처리, `build_filing_chunks()` 추가 |
| `app/repositories/filing_cache_repository.py` | `update_summary()` 추가 |
| `app/domain/evidence_indexing.py` | 공시 1건을 Chroma 문서 1개가 아니라 섹션별 N개 chunk로 인덱싱 |
| `app/domain/evidence_retrieval.py` | Chroma chunk id가 아니라 metadata `source_id`로 `filing_cache` row join |
| `scripts/reset_filing_collection.py` | `filing` 컬렉션 삭제 스크립트 |
| `scripts/validate_filing_evidence_retrieval.py` | 새 공시 chunk 구조에 맞춘 검증 스크립트 |

사전 검증 스크립트 결과:

```json
{
  "indexed_rows": 1,
  "failed_rows": 0,
  "fetched_id": "UUID:s0:c0"
}
```

이 결과는 fake DART ZIP을 이용한 단위 end-to-end 검증이다.

---

## 4. 실제 종목 테스트 절차

### 4-1. 워치리스트 추가

테스트 사용자:

```text
user_id: 92042f2b-9950-457c-8092-b43d79dda768
email: phase2-test-user@example.com
```

추가한 종목:

```text
symbol: 000270
name_kr: 기아
```

결과:

```text
watchlist_id: 743ed95c-6cfc-4981-8668-4a87096d04fd
```

### 4-2. 테스트 전 Chroma 상태 확인

`000270`에 대한 기존 Chroma filing 문서 수:

```text
0개
```

따라서 이번 테스트에서 생성되는 Chroma 문서가 새로 들어가는지 명확히 확인할 수 있었다.

### 4-3. DART 공시 메타데이터 수집

실행 서비스:

```text
FilingIngestionService.sync_filings_for_ticker("000270")
```

결과:

```text
SyncFilingResult(
  fetched_count=60,
  inserted_count=60,
  updated_count=0,
  skipped_count=0,
  elapsed_ms=1137
)
```

의미:

```text
DART list API에서 최근 공시 60건을 가져왔고,
60건 모두 filing_cache에 신규 저장됐다.
```

### 4-4. 공시 본문 Chroma 재색인

실행 서비스:

```text
EvidenceIndexingService.reindex_filing_for_symbol("000270")
```

결과:

```text
ReindexFilingResult(
  symbol="000270",
  scanned_rows=60,
  indexed_rows=60,
  skipped_rows=0,
  failed_rows=0
)
```

의미:

```text
filing_cache의 60개 공시 row를 스캔했고,
60개 모두 DART document.xml 본문 추출과 Chroma 인덱싱에 성공했다.
실패나 skip은 없었다.
```

---

## 5. PostgreSQL 저장 결과

최종 DB 확인 결과:

```text
watchlist count for 000270 = 1
filing_cache count for 000270 = 60
```

즉 PostgreSQL에는 아래 데이터가 들어갔다.

```text
watchlist
  - 000270 기아 1건

filing_cache
  - 000270 공시 메타데이터 60건
```

`filing_cache`는 공시 원장 역할이다.

주요 컬럼:

```text
id
symbol
filing_title
filing_type
dart_receipt_no
source_url
disclosed_at
retrieved_at
ttl_until
summary
content
```

이번 변경 이후 `EvidenceIndexingService`가 공시 본문 앞부분으로 `summary`도 업데이트한다.

---

## 6. Chroma 저장 결과

Chroma collection:

```text
filing
```

샘플 조회:

```text
where={"symbol": "000270"}
limit=5
```

조회된 sample ids:

```text
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c0
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c1
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c2
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c3
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c4
```

문서 id 형식:

```text
{filing_cache.id}:s{section_index}:c{chunk_index}
```

예:

```text
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c0
```

metadata 예시:

```json
{
  "chunk_index": 0,
  "disclosed_at": "2026-05-21T00:00:00+09:00",
  "filing_title": "[기재정정]타법인주식및출자증권취득결정",
  "section": "[기재정정]타법인주식및출자증권취득결정",
  "source_id": "7dfe274e-e89a-4be6-bb2e-a47e4b5538d5",
  "source_type": "filing",
  "symbol": "000270"
}
```

핵심 연결 구조:

```text
Chroma id:
  7dfe274e-...:s0:c0

metadata.source_id:
  7dfe274e-...

PostgreSQL filing_cache.id:
  7dfe274e-...
```

따라서 Chroma 검색 결과가 chunk 단위로 나오더라도, `source_id`를 통해 원본 `filing_cache` row와 연결할 수 있다.

---

## 7. Chroma 본문 저장 내용 확인

샘플 document:

```text
[기재정정]타법인주식및출자증권취득결정
[기재정정]타법인주식및출자증권취득결정

정정일자 | 2026-05-21
1. 정정관련 공시서류: 2. 정정관련 공시서류제출일 | 타법인주식및출자증권취득결정: 2026년 4월 24일 | 타법인주식및출자증권취득결정: 2026년 4월 24일
1. 정정관련 공시서류: 3. 정정사유 | 타법인주식및출자증권취득결정: 합작투자계약 체결에 따른 본문 정정 | 타법인주식및출자증권취득결정: 합작투자계약 체결에 따른 본문 정정
1. 정정관련 공시서류: 4. 정정사항 | 타법인주식및출자증권취득결정: 4. 정정사항 | 타법인주식및출자증권취득결정: 4. 정정사항
```

표 구조가 아래처럼 보존되어 저장됐다.

```text
1. 발행회사: 2. 취득내역 | 회사명: 취득금액(원) | 에이치엠지퓨처콤플렉스 주식회사(예정): 2,363,450,000,000
1. 발행회사: 2. 취득내역 | 회사명: 자기자본(원) | 에이치엠지퓨처콤플렉스 주식회사(예정): 61,190,464,000,000
1. 발행회사: 2. 취득내역 | 회사명: 자기자본대비(%) | 에이치엠지퓨처콤플렉스 주식회사(예정): 3.9
1. 발행회사: 4. 취득방법 | 회사명: 4. 취득방법 | 에이치엠지퓨처콤플렉스 주식회사(예정): 현금 취득
1. 발행회사: 5. 취득목적 | 회사명: 5. 취득목적 | 에이치엠지퓨처콤플렉스 주식회사(예정): 발행회사를 통한 신규 연구 및 업무 거점 확보
```

기존 `BeautifulSoup.get_text()` 방식처럼 숫자와 레이블이 분리되지 않고, `헤더: 값` 형태로 저장되는 것을 확인했다.

---

## 8. Embedding 저장 확인

Chroma에서 embedding까지 include해서 조회했다.

확인 결과:

```text
embedding_dim: 768
```

embedding 앞 10개 값 예시:

```text
[-0.027025, -0.024289, 0.021143, -0.031126, 0.028509,
 -0.064144, -0.008110, 0.005396, -0.004395, -0.061039]
```

즉 저장된 document는 단순 텍스트만 있는 것이 아니라, 768차원 벡터와 함께 Chroma에 저장되어 있다.

---

## 9. 실제 검색 테스트

사용한 query:

```text
기아 타법인 주식 출자증권 취득 결정
```

검색 조건:

```json
{"symbol": "000270"}
```

사용한 embedding:

```text
provider: huggingface
model: jhgan/ko-sroberta-multitask
dimension: 768
```

검색 결과 top 3 ids:

```text
7dfe274e-e89a-4be6-bb2e-a47e4b5538d5:s0:c5
dd073a4e-651f-4214-b662-953e3e77bae6:s0:c49
dd073a4e-651f-4214-b662-953e3e77bae6:s0:c67
```

top 1 metadata:

```json
{
  "chunk_index": 5,
  "disclosed_at": "2026-05-21T00:00:00+09:00",
  "filing_title": "[기재정정]타법인주식및출자증권취득결정",
  "section": "[기재정정]타법인주식및출자증권취득결정",
  "source_id": "7dfe274e-e89a-4be6-bb2e-a47e4b5538d5",
  "source_type": "filing",
  "symbol": "000270"
}
```

top 1 document preview:

```text
[기재정정]타법인주식및출자증권취득결정
[기재정정]타법인주식및출자증권취득결정

1. 발행회사: 13. 기타 투자판단과 관련한 중요사항 | 회사명: 13. 기타 투자판단과 관련한 중요사항 | 에이치엠지퓨처콤플렉스 주식회사(예정): - 본 거래는 미래 사업 선도를 위한 복합 연구 및 업무 거점 확보를 위해 현대자동차 그룹 내 계열회사 등이 신설 예정 법인에 신규 출자하는 건임.
```

검색 distance:

```text
0.6677431464195251
0.7048460841178894
0.7815942168235779
```

결과 해석:

```text
query가 "타법인 주식 출자증권 취득 결정"이었고,
top 1 결과가 실제 "[기재정정]타법인주식및출자증권취득결정" 공시 chunk였다.
```

따라서 RAG 검색 관점에서 정상 동작을 확인했다.

---

## 10. 추가 확인: 기존 000990도 검색 성공

기존 테스트 종목 `000990` DB하이텍도 Docker Chroma `filing` 컬렉션에 이미 저장되어 있었다.

검색 query:

```text
DB하이텍 분기보고서 배당 집중투표
```

결과 top 문서:

```text
분기보고서 (2026.03)
```

문서 preview:

```text
행사자: DB하이텍 소액주주연대(2025) | 소수주주권 내용(안건): 주주제안권(2025.02.13)
- 정관 일부변경(분기배당 허용)의 건
- 정관 일부변경(자기주식 소각 추가)의 건
- 정관 일부변경(기업설명회 정례화)의 건
- 정관 일부변경(소액주주 보호 명문화)의 건
- 정관 일부변경(집중투표제 도입)의 건
```

이로써 신규 종목뿐 아니라 기존 종목도 운영 Chroma에서 검색 가능한 상태임을 확인했다.

---

## 11. 테스트 중 관찰된 경고

### 11-1. OpenRouter API Key 경고

```text
OPENROUTER_API_KEY 미설정 — LLM 기능 비활성화됩니다
```

이번 테스트는 LLM 호출이 아니라 공시 Chroma 인덱싱/검색 테스트였으므로 영향 없음.

### 11-2. Chroma telemetry 경고

```text
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
```

Chroma/Posthog telemetry 관련 경고로 보이며, upsert/query 동작에는 영향 없음.

### 11-3. XMLParsedAsHTMLWarning

```text
XMLParsedAsHTMLWarning
It looks like you're parsing an XML document using an HTML parser.
```

DART `document.xml`을 BeautifulSoup `html.parser`로 파싱하면서 발생한 경고다.

현재 테스트에서는 본문 추출과 표 직렬화가 정상 동작했다. 다만 장기적으로는 XML 문서에 대해 `lxml` parser 또는 XML parser fallback을 검토할 수 있다.

---

## 12. 최종 판정

| 항목 | 결과 |
|---|---|
| Docker Chroma 실행 | 통과 |
| Chroma heartbeat | 통과 |
| 신규 워치리스트 추가 | 통과 |
| DART 공시 메타데이터 수집 | 통과 |
| DART 공시 본문 다운로드 | 통과 |
| 표 구조 보존 파싱 | 통과 |
| 섹션/chunk 생성 | 통과 |
| 768차원 embedding 저장 | 통과 |
| Chroma metadata 저장 | 통과 |
| source_id 기반 연결 구조 | 통과 |
| symbol filter 검색 | 통과 |
| 실제 query 검색 결과 품질 | 통과 |

최종 결론:

```text
공시 RAG용 filing Chroma 파이프라인은 실제 Docker Chroma 환경에서 정상 동작한다.
000270 신규 종목 기준으로 워치리스트 추가부터 공시 본문 chunk 검색까지 end-to-end 통과했다.
```

---

## 13. 남은 작업

현재 기능 검증은 통과했지만, 운영 품질을 위해 다음 작업은 남아 있다.

```text
1. 뉴스 API 키 설정 후 news_cache/news Chroma도 동일하게 확인
2. 토론 에이전트 실행 시 EvidenceRetrievalService가 filing 결과를 실제 prompt에 넣는지 확인
3. DART XML 파싱 경고를 줄이기 위한 parser fallback 검토
4. 중복 chunk upsert/삭제 정책 정리
5. reindex_all_filings.py 실행 시 진행률/실패 row 로그 강화
6. Chroma Docker volume 백업/복구 정책 정리
```

