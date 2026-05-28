# 공시(Filing) RAG 품질 고도화 설계

> 작성일: 2026-05-27  
> 대상 파일:
> - `app/external/dart/client.py`
> - `app/domain/evidence_indexing.py`
> - `app/repositories/filing_cache_repository.py`
> - `scripts/reset_filing_collection.py`

---

## 1. 한 줄 요약

현재 토론 에이전트는 `news`와 `filing` Chroma 컬렉션을 검색해 RAG 근거로 사용한다.

다만 `filing` 쪽은 공시 본문을 가져와 임베딩하긴 하지만, HTML 표 구조가 `get_text()`로 깨지고 공시 1건이 벡터 1개로 저장되어 검색 품질이 낮다.

이번 고도화는 `filing_cache` 메타데이터 저장 구조는 유지하고, `filing` Chroma 컬렉션에 들어가는 공시 본문 텍스트를 구조 보존 + 섹션별 청크 방식으로 개선하는 작업이다.

---

## 2. 왜 이 작업이 필요한가

### 2-1. 현재 토론 RAG 구성

토론은 `bull`, `bear`, `moderator`가 직접 DB를 뒤지는 구조가 아니다.

토론 시작 시 `data_agent`가 먼저 근거를 모으고, 그 결과를 토론 에이전트들에게 context로 넘긴다.

```text
DebateExecutionService
    ↓
LangGraph debate_graph
    ↓
data_agent
    ↓
가격/재무 DB context 조회
    +
EvidenceRetrievalService.search_symbol_evidence()
    ↓
news Chroma 컬렉션 검색
filing Chroma 컬렉션 검색
    ↓
evidence_context 생성
    ↓
bull / bear / moderator 발언에 사용
```

즉 토론 에이전트가 공시를 제대로 참고하려면, `filing_cache`에 공시 row가 있는 것만으로는 부족하다.  
공시 본문이 `filing` Chroma 컬렉션에 검색 가능한 형태로 임베딩되어 있어야 한다.

### 2-2. 현재 RAG에서 `filing_cache`와 Chroma의 역할

`filing_cache`는 PostgreSQL 메타데이터 저장소다.

```text
filing_cache
    - 어떤 종목의 어떤 공시인지 저장
    - DART 접수번호 저장
    - source_url 저장
    - disclosed_at 저장
```

`filing` Chroma 컬렉션은 실제 RAG 검색 저장소다.

```text
filing Chroma
    - DART 공시 본문 텍스트 저장
    - embedding vector 저장
    - symbol/source_id metadata 저장
```

토론 RAG는 이 둘을 같이 쓴다.

```text
Chroma에서 query와 가까운 공시 본문 chunk 검색
    ↓
metadata.source_id 또는 Chroma id로 filing_cache row 조회
    ↓
제목, URL, 공시 유형, excerpt를 합쳐 evidence_context 생성
```

따라서 `filing_cache`는 "공시가 존재한다"는 기준 데이터이고, `filing` Chroma는 "공시 본문을 의미 기반으로 검색하는 데이터"다.

### 2-3. 지금도 동작은 하지만 품질이 낮은 이유

현재도 `EvidenceIndexingService.reindex_filing_for_symbol()`이 `filing_cache.dart_receipt_no`로 DART 본문을 가져와 `filing` Chroma에 넣는다.

그러나 현재 방식은 다음과 같다.

```text
DART document.xml
    ↓
BeautifulSoup.get_text()
    ↓
표 구조가 깨진 긴 텍스트
    ↓
공시 1건 = ChromaDocument 1개
    ↓
벡터 1개
```

이 구조에서는 공시 본문이 들어가 있더라도 RAG가 제대로 작동하기 어렵다.

예를 들어 사용자가 "영업이익이 왜 줄었는지" 묻거나 bear agent가 리스크 근거를 찾을 때, Chroma에는 다음 같은 텍스트가 들어가 있다.

```text
구분
당기
전기
영업이익
6,567억
43,376억
```

이 텍스트는 숫자와 의미의 연결이 약하다.  
또한 수십 페이지 분기보고서 전체가 벡터 하나로 압축되면, 특정 표/섹션의 정보가 검색 점수에 잘 반영되지 않는다.

### 2-4. 이 개선을 붙여야 제대로 작동하는 지점

이번 개선의 핵심은 `filing` Chroma에 들어가는 문서를 다음처럼 바꾸는 것이다.

Before:

```text
공시 1건 전체
    → 깨진 plain text
    → Chroma vector 1개
```

After:

```text
공시 1건
    → 표 구조 보존 텍스트
    → 섹션별 chunk N개
    → Chroma vector N개
```

예:

```text
[분기보고서 - 재무에 관한 사항]
구분: 매출액 | 당기(2023): 267,627억 | 전기(2022): 302,231억
구분: 영업이익 | 당기(2023): 6,567억 | 전기(2022): 43,376억
```

이렇게 들어가야 `bull`, `bear`, `moderator`가 사용하는 `evidence_context`에 실제 공시 근거가 의미 있게 올라온다.

정리하면 이 작업은 단순히 "공시 파싱을 예쁘게 하는 작업"이 아니다.

```text
토론 에이전트가 공시 기반 근거를 말하게 만드는 RAG 품질 작업
```

이다.

---

## 3. 현재 구조

### 3-1. 저장과 검색 흐름

```text
DART list.json
    ↓
FilingIngestionService.sync_filings_for_ticker()
    ↓
filing_cache 테이블
    - symbol
    - filing_title
    - filing_type
    - dart_receipt_no
    - source_url
    - disclosed_at
    - content = NULL
    - summary = NULL

    ↓ watchlist background task 또는 reindex script

EvidenceIndexingService.reindex_filing_for_symbol()
    ↓
DART document.xml
    ↓
DartClient.fetch_filing_text()
    ↓
filing Chroma 컬렉션
```

### 3-2. `filing_cache row만 있음`의 의미

`filing_cache` row만 있다는 것은 PostgreSQL에 "이 종목에 이런 공시가 있다"는 메타데이터만 저장된 상태다.

예:

```text
symbol: 005930
filing_title: 분기보고서
dart_receipt_no: 20240516001234
source_url: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...
disclosed_at: 2024-05-16
content: NULL
summary: NULL
```

이 상태만으로는 토론 RAG가 공시 본문을 의미 있게 검색할 수 없다.  
토론 RAG가 실제 공시 내용을 보려면 다음 단계가 필요하다.

```text
filing_cache.dart_receipt_no
    ↓
DART document.xml 본문 다운로드
    ↓
본문 텍스트 추출
    ↓
임베딩 생성
    ↓
filing Chroma 컬렉션 저장
```

현재 코드는 이 Chroma 인덱싱 단계까지 있긴 하다. 문제는 인덱싱 품질이다.

---

## 4. 현재 문제

### 문제 1. 표 구조가 깨진다

`DartClient.extract_document_text()`는 HTML 전체를 BeautifulSoup `get_text()`로 변환한다.

```python
extracted = soup.get_text(separator="\n")
```

HTML 표가 아래처럼 들어오면:

```html
<table>
  <tr><th>구분</th><th>당기(2023)</th><th>전기(2022)</th></tr>
  <tr><td>매출액</td><td>267,627억</td><td>302,231억</td></tr>
  <tr><td>영업이익</td><td>6,567억</td><td>43,376억</td></tr>
</table>
```

현재 추출 결과는 다음처럼 된다.

```text
구분
당기(2023)
전기(2022)
매출액
267,627억
302,231억
영업이익
6,567억
43,376억
```

숫자가 어떤 항목의 값인지 약해져서 임베딩 품질이 떨어진다.

### 문제 2. 병합 셀이 있는 표는 관계가 끊긴다

DART 공시에는 `rowspan`, `colspan`이 있는 표가 많다.

```html
<table>
  <tr>
    <td rowspan="2">1. 일시 및 장소</td>
    <td>일시</td>
    <td>2026-05-18</td>
    <td>10:25</td>
  </tr>
  <tr>
    <td>장소</td>
    <td colspan="2">The Westin Boston Seaport District</td>
  </tr>
</table>
```

단순 행 순회로 처리하면 두 번째 행에서 `1. 일시 및 장소`가 사라진다.

원하는 결과:

```text
1. 일시 및 장소: 일시 | 값: 2026-05-18 | 값: 10:25
1. 일시 및 장소: 장소 | 값: The Westin Boston Seaport District
```

### 문제 3. 공시 1건이 Chroma 문서 1개로 저장된다

현재 `EvidenceIndexingService.reindex_filing_for_symbol()`은 공시 1건을 하나의 `ChromaDocument`로 만든다.

```text
분기보고서 전체 본문 수십 페이지
    ↓
ChromaDocument 1개
    ↓
벡터 1개
```

긴 사업보고서/분기보고서의 세부 정보가 하나의 벡터로 뭉개져서, "영업이익 감소", "유상증자 일정", "부채비율" 같은 구체 쿼리에 약하다.

### 문제 4. `summary`가 비어 토론 컨텍스트 fallback 품질이 낮다

토론 데이터 노드는 Chroma 검색 결과를 우선 사용하지만, 결과가 없으면 DB의 `news_cache`, `filing_cache.summary`도 fallback처럼 사용한다.

현재 `filing_cache.summary`가 `NULL`이면 공시 제목만 남는다.

```text
분기보고서
주요사항보고서(유상증자결정)
```

---

## 5. 목표

| 목표 | 설명 |
|---|---|
| `FilingIngestionService` 역할 유지 | DART list.json 기반 PG 메타데이터 저장만 담당 |
| 표 구조 보존 | `rowspan`, `colspan`, 단위, 각주를 보존한 텍스트 생성 |
| 섹션별 청킹 | 공시 1건을 여러 Chroma 문서로 분할 |
| summary 보강 | 추출 텍스트 기반으로 `filing_cache.summary` 업데이트 |
| Chroma 차원 충돌 해소 | 기존 64차원 `filing` 컬렉션 삭제 후 실제 embedding 차원으로 재생성 |

이번 작업에서 `filing_cache.content` 저장은 필수 범위로 보지 않는다.  
공시 본문 원문은 DART `document.xml`에서 재생성 가능하므로, 우선 Chroma 인덱싱과 summary 개선에 집중한다.

---

## 6. 개선 후 구조

```text
관심종목 등록
    ↓
FilingIngestionService.sync_filings_for_ticker()
    ↓
filing_cache 테이블
    - 공시 메타데이터 저장
    - content는 NULL 유지 가능
    - summary는 indexing 단계에서 업데이트

    ↓

EvidenceIndexingService.reindex_filing_for_symbol()
    ↓
DartClient.fetch_document_xml()
    ↓
DartClient.extract_document_text_v2()
    - HTML zip 추출
    - table grid 확장
    - 표/본문 구조 보존 텍스트 생성
    ↓
DartClient.build_filing_chunks()
    - 섹션별 분할
    - 긴 섹션은 max chars 기준 재분할
    ↓
FilingCacheRepository.update_summary()
    ↓
ChromaClient.upsert(FILING_COLLECTION_NAME, chunks)
```

토론 RAG 사용 흐름:

```text
data_agent
    ↓
EvidenceRetrievalService.search_symbol_evidence()
    ↓
news Chroma 검색 + filing Chroma 검색
    ↓
evidence_context 생성
    ↓
bull / bear / moderator 발언 근거로 사용
```

---

## 7. 구현 설계

### 7-1. DART zip HTML 추출 helper

현재 `extract_document_text()` 안에 들어 있는 zip 처리 로직을 helper로 분리한다.

```python
def _extract_document_html(self, zip_bytes: bytes) -> str:
    """DART document.xml zip에서 가장 큰 HTML/XML 문서를 문자열로 추출."""
```

동작:

1. zip 파일 열기
2. `.html`, `.htm`, `.xml` 후보 선택
3. HTML 파일 우선, 없으면 XML 사용
4. 가장 큰 파일 선택
5. `_decode_document_bytes()`로 `utf-8`, `euc-kr`, `cp949` 폴백 디코딩

### 7-2. 표 grid 확장

`rowspan`, `colspan`을 실제 2차원 표로 펼친다.

```python
def _expand_table_to_grid(table) -> list[list[str]]:
    grid: dict[tuple[int, int], str] = {}
    row_idx = 0

    for tr in table.find_all("tr", recursive=False):
        col_idx = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (row_idx, col_idx) in grid:
                col_idx += 1

            text = " ".join(cell.get_text(" ", strip=True).split())
            rowspan = int(cell.get("rowspan", 1) or 1)
            colspan = int(cell.get("colspan", 1) or 1)

            for r in range(rowspan):
                for c in range(colspan):
                    grid[(row_idx + r, col_idx + c)] = text

            col_idx += colspan
        row_idx += 1
```

주의:

- `recursive=False`를 우선 사용해 중첩 테이블의 셀 중복 수집을 줄인다.
- 비정상 `rowspan`, `colspan` 값은 `1`로 fallback한다.

### 7-3. grid 직렬화

```text
구분: 매출액 | 당기(2023): 267,627억 | 전기(2022): 302,231억
구분: 영업이익 | 당기(2023): 6,567억 | 전기(2022): 43,376억
```

처리 규칙:

- 첫 행이 전체 동일 값이면 단위/제목 행으로 보고 prefix로 보존
- 헤더가 비어 있으면 `값` 또는 이전 의미 있는 헤더로 보완
- `-`, 빈 문자열은 제외
- 너무 짧은 표는 버리지 말고 가능한 텍스트로 반환

### 7-4. 각주 추출

테이블 직후 형제 노드에서 각주를 수집한다.

인식 패턴:

```text
(*)
(*1)
(주1)
※
* 
```

결과:

```text
[각주]
(*) 스무디킹코리아(주)는 ...
```

### 7-5. 구조 보존 텍스트 추출

새 메서드:

```python
def extract_document_text_v2(self, zip_bytes: bytes) -> str:
    """DART 공시 HTML을 RAG 친화적인 구조 보존 텍스트로 변환."""
```

처리:

1. `_extract_document_html()`로 HTML 문자열 추출
2. `script`, `style`, `head` 제거
3. 제목/문단/표를 순서대로 순회
4. 표는 `serialize_table_full()` 사용
5. 중첩 테이블 중복 처리 방지
6. 빈 줄 정리 후 반환

기존 `fetch_filing_text()`는 내부에서 v2를 사용하도록 변경한다.

```python
def fetch_filing_text(self, receipt_no: str) -> str:
    zip_bytes = self.fetch_document_xml(receipt_no)
    text = self.extract_document_text_v2(zip_bytes)
    ...
```

### 7-6. 섹션별 Chroma chunk 생성

새 메서드:

```python
def build_filing_chunks(
    self,
    zip_bytes: bytes,
    *,
    symbol: str,
    filing_id: str,
    filing_title: str,
    disclosed_at: str,
    max_chunk_chars: int = 1200,
) -> list[ChromaDocument]:
```

문서 ID:

```text
{filing_id}:s{section_index}:c{chunk_index}
```

metadata:

```json
{
  "symbol": "005930",
  "source_type": "filing",
  "source_id": "filing_cache.id",
  "filing_title": "분기보고서",
  "section": "재무에 관한 사항",
  "chunk_index": 0,
  "disclosed_at": "2024-05-16T00:00:00+09:00"
}
```

중요:

현재 `EvidenceRetrievalService._search_filings()`는 Chroma hit id로 `filing_cache` row를 찾는다.  
청크 ID가 `{filing_id}:s0:c0`처럼 바뀌면 그대로는 row 조회가 실패한다.

따라서 둘 중 하나를 적용해야 한다.

1. retrieval에서 metadata의 `source_id`를 우선 사용한다.
2. Chroma document id는 `filing_id`를 유지하고, 청크별 id 중복 문제를 다른 방식으로 피한다.

권장안은 1번이다. Chroma id는 chunk 고유 ID로 두고, row join은 metadata `source_id`를 사용한다.

---

## 8. EvidenceIndexingService 변경

기존:

```python
filing_text = self.dart_client.fetch_filing_text(row.dart_receipt_no)
documents.append(self.build_filing_document(row, content=filing_text))
```

변경:

```python
zip_bytes = self.dart_client.fetch_document_xml(row.dart_receipt_no)
filing_text = self.dart_client.extract_document_text_v2(zip_bytes)
summary = _build_summary(filing_text)
self.filing_repo.update_summary(dart_receipt_no=row.dart_receipt_no, summary=summary)
documents.extend(
    self.dart_client.build_filing_chunks(
        zip_bytes,
        symbol=row.symbol,
        filing_id=str(row.id),
        filing_title=row.filing_title,
        disclosed_at=row.disclosed_at.isoformat() if row.disclosed_at else "",
    )
)
```

카운팅 기준:

- `scanned_rows`: 조회한 filing_cache row 수
- `indexed_rows`: Chroma에 청크가 1개 이상 생성된 filing row 수
- `skipped_rows`: receipt_no 없음, 본문 너무 짧음, 청크 없음
- `failed_rows`: DART fetch/parsing 실패

---

## 9. EvidenceRetrievalService 변경

청크 ID를 도입하면 Chroma result id가 `filing_cache.id`와 다를 수 있다.

현재 로직:

```python
ids = result["ids"][0]
rows = self.filing_repo.get_by_ids(ids)
```

변경 방향:

```python
source_ids = [
    metadata.get("source_id") or item_id
    for item_id, metadata in zip(ids, metadatas, strict=False)
]
rows = self.filing_repo.get_by_ids(source_ids)
```

그리고 hit 생성 시 `document`는 청크 본문을 그대로 excerpt로 사용한다.

이 변경이 없으면 청크 인덱싱은 성공해도 토론 RAG에서 filing hit가 row join 실패로 버려질 수 있다.

---

## 10. Repository 변경

`FilingCacheRepository`에 summary update 메서드를 추가한다.

```python
def update_summary(self, *, dart_receipt_no: str, summary: str) -> None:
    self.session.execute(
        update(FilingCache)
        .where(FilingCache.dart_receipt_no == dart_receipt_no)
        .values(summary=summary, retrieved_at=func.now())
    )
    self.session.flush()
```

필요 import:

```python
from sqlalchemy import func, update
```

---

## 11. Chroma 컬렉션 재설정

기존 `filing` 컬렉션이 테스트용 64차원 임베딩으로 만들어졌다면 실제 embedding 차원과 충돌한다.

실행:

```bash
python scripts/reset_filing_collection.py
```

이후 재색인:

```bash
python scripts/reindex_all_filings.py
```

검증:

```bash
python scripts/validate_filing_evidence_retrieval.py
python scripts/validate_chroma_connection.py
```

---

## 12. 구현 순서

1. `DartClient`에 `_extract_document_html()` 추가
2. `DartClient`에 table grid 직렬화 유틸 추가
3. `extract_document_text()` 또는 `fetch_filing_text()`가 v2 추출을 사용하도록 변경
4. `DartClient.build_filing_chunks()` 추가
5. `FilingCacheRepository.update_summary()` 추가
6. `EvidenceIndexingService.reindex_filing_for_symbol()`을 청크 인덱싱 방식으로 변경
7. `EvidenceRetrievalService._search_filings()`가 metadata `source_id`로 row를 찾도록 변경
8. `filing` Chroma 컬렉션 reset
9. filing reindex 실행
10. 토론 RAG 검증

---

## 13. 기대 효과

Before:

```text
[DART] 분기보고서
- 구분 당기 전기 매출액 267,627억 302,231억 ...
```

After:

```text
[DART] 분기보고서 (재무에 관한 사항)
- 구분: 매출액 | 당기(2023): 267,627억 | 전기(2022): 302,231억
- 구분: 영업이익 | 당기(2023): 6,567억 | 전기(2022): 43,376억
```

토론 에이전트 관점:

- `bull`: 실적 개선/자산/성장 근거를 더 정확히 검색
- `bear`: 부채/손실/희석/리스크 근거를 더 정확히 검색
- `moderator`: 양쪽 주장의 근거 출처와 excerpt를 더 잘 검증

---

## 14. 주의사항

- DART `document.xml`은 공시 1건당 API 호출 1회를 소비한다.
- 대형 사업보고서는 chunk 수가 많을 수 있으므로 batch upsert가 필요할 수 있다.
- 이미지로 된 표는 이 방식으로 추출할 수 없다.
- HTML table이 아닌 div 기반 표는 추가 parser가 필요할 수 있다.
- summary는 임시로 앞 300~500자 규칙 기반 생성 후, 추후 LLM 요약으로 교체 가능하다.
- chunk ID 도입 시 retrieval join을 metadata `source_id` 기준으로 바꾸는 작업이 반드시 같이 들어가야 한다.
