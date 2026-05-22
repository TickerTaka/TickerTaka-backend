# DART 공시 본문 저장 구현 계획

## 목적

현재 DART 공시 적재는 `filing_cache`에 공시 목록 메타데이터와 DART 이동 URL만 저장한다.

다음 단계에서는 DART 공시 원문을 가져와 `filing_cache.content`에 저장한다.

목표는 다음과 같다.

```text
장바구니 종목 추가
-> DART 공시 목록 조회
-> filing_cache metadata upsert
-> 각 공시의 원문 document 조회
-> 본문 텍스트 추출
-> filing_cache.content 저장
```

이번 계획의 범위는 **본문 원문 텍스트 저장**까지다.

`summary` 생성, pgvector 저장, RAG chunking, embedding 생성은 이번 범위가 아니다. 추후 RAG 담당 단계에서 `content`를 기반으로 별도 처리한다.

## 현재 구현 상태

현재 구현은 이미 클라우드 DB 기준으로 동작 확인됐다.

검증 결과:

```text
symbol=005930 fetched=10 inserted=10 updated=0 skipped=0
재실행 시 inserted=0 updated=10
watchlist API smoke test 통과
```

현재 구현된 파일:

| 파일 | 현재 역할 |
| --- | --- |
| `app/external/dart.py` | DART `corpCode.xml`, `list.json` 호출, DART viewer URL 생성 |
| `app/domain/filing_ingestion.py` | symbol 기준 공시 목록 조회 및 저장 orchestration |
| `app/repositories/filing_cache_repository.py` | `dart_receipt_no` 기준 `filing_cache` upsert |
| `app/domain/watchlist_service.py` | 장바구니 추가 후 공시 sync background task 실행 |
| `scripts/validate_dart_filing_ingestion.py` | 단일 종목 공시 적재 검증 |

현재 `filing_cache` 저장 방식:

| column | 현재 저장 값 |
| --- | --- |
| `symbol` | DART `stock_code`, 없으면 요청 symbol |
| `filing_title` | DART `report_nm` |
| `filing_type` | DART `pblntf_ty` 또는 `pblntf_detail_ty`, 없으면 `NULL` |
| `content` | 항상 `NULL` |
| `summary` | 항상 `NULL` |
| `dart_receipt_no` | DART `rcept_no` |
| `source_url` | `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}` |
| `disclosed_at` | DART `rcept_dt`를 KST timestamptz로 변환 |
| `retrieved_at` | DB `now()` |
| `ttl_until` | 수집 시점 + 7일 |

현재 `content`, `summary`가 `NULL`인 이유:

```python
content=None,
summary=None,
```

현재 repository에서 명시적으로 위처럼 저장하고 있다.

## 구현 목표

### 1. DART 원문 조회 기능 추가

DART 공시 원문은 `rcept_no`를 기준으로 조회한다.

우선 공식 API인 `document.xml`을 사용한다.

```text
GET https://opendart.fss.or.kr/api/document.xml
```

파라미터:

```text
crtfc_key={DART_API_KEY}
rcept_no={dart_receipt_no}
```

응답은 ZIP binary 형태로 내려오며, 내부에 XML 문서가 들어 있다.

추가할 메서드:

```python
class DartClient:
    def fetch_document_text(self, receipt_no: str, *, timeout: float = 20.0) -> str | None:
        ...
```

처리 흐름:

```text
1. document.xml 호출
2. ZIP 압축 해제
3. 내부 XML 파일 읽기
4. XML/HTML 태그 제거
5. 사람이 읽을 수 있는 plain text로 정규화
6. 빈 문자열이면 None 반환
```

### 2. 본문 텍스트 정제 로직

공시 원문 XML은 공시 종류마다 구조가 다를 수 있다.

초기 구현은 완벽한 문단 구조 복원보다 **검색/RAG에 쓸 수 있는 텍스트 확보**를 우선한다.

정제 규칙:

```text
XML 파싱 가능하면 ElementTree.itertext()로 전체 text 추출
XML 파싱 실패 시 BeautifulSoup 또는 정규식 fallback 검토
연속 공백은 하나로 축소
빈 줄은 과도하게 남기지 않음
너무 짧은 텍스트는 저장하지 않음
```

초기 helper 예시:

```python
def _extract_text_from_document_xml(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    text = "\n".join(part.strip() for part in root.itertext() if part.strip())
    return normalize_whitespace(text)
```

주의:

- 표 구조는 초기에는 plain text로만 보존한다.
- 이미지, 첨부 PDF, XBRL 세부 구조까지 해석하지 않는다.
- 공시 원문 전체가 너무 클 수 있으므로 최대 길이 제한을 둘지 검토한다.

### 3. Repository upsert 확장

현재 `upsert_filing()`은 metadata만 받는다.

본문 저장을 위해 `content` 인자를 추가한다.

변경 전:

```python
def upsert_filing(..., ttl_until: datetime) -> UUID | None:
```

변경 후:

```python
def upsert_filing(
    ...,
    ttl_until: datetime,
    content: str | None = None,
) -> UUID | None:
```

insert 시:

```python
content=content
summary=None
```

update 시 정책:

```text
content가 새로 추출되었으면 content 업데이트
content 추출 실패 또는 None이면 기존 content를 덮어쓰지 않음
summary는 건드리지 않음
```

이유:

- 한번 성공적으로 저장된 본문을 나중의 일시적 API 실패로 `NULL` 덮어쓰기 하면 안 된다.
- `summary`는 향후 요약/RAG 파이프라인 소유가 될 수 있으므로 공시 수집 단계에서 덮어쓰지 않는다.

update 정책 예시:

```python
set_={
    "symbol": stmt.excluded.symbol,
    "filing_title": stmt.excluded.filing_title,
    "filing_type": stmt.excluded.filing_type,
    "source_url": stmt.excluded.source_url,
    "disclosed_at": stmt.excluded.disclosed_at,
    "retrieved_at": text("now()"),
    "ttl_until": stmt.excluded.ttl_until,
    "content": case(
        (stmt.excluded.content.is_not(None), stmt.excluded.content),
        else_=FilingCache.content,
    ),
}
```

### 4. Ingestion service 확장

현재 `FilingIngestionService.sync_filings_for_ticker()`는 공시 목록을 가져온 뒤 바로 metadata를 upsert한다.

변경 후에는 각 valid item마다 본문 조회를 시도한다.

흐름:

```text
for item in valid_items:
    content = None
    try:
        content = dart_client.fetch_document_text(item.receipt_no)
    except DartApiError:
        로그만 남기고 metadata 저장은 계속 진행

    repo.upsert_filing(..., content=content)
```

정책:

- 본문 조회 실패가 전체 공시 목록 저장 실패로 이어지면 안 된다.
- `filing_title`, `source_url`, `dart_receipt_no`는 본문 실패와 관계없이 저장한다.
- content 실패 건수는 결과 객체에 별도 카운트로 남긴다.

`SyncFilingResult` 확장:

```python
@dataclass
class SyncFilingResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    content_fetched_count: int = 0
    content_failed_count: int = 0
    elapsed_ms: int = 0
```

### 5. 기존 content 재조회 방지

처음에는 단순하게 모든 valid item에 대해 document.xml을 호출할 수 있다.

하지만 장기적으로는 DART API 호출량과 속도를 줄이기 위해 기존 content가 있는 경우 재조회하지 않는 정책이 필요하다.

권장 정책:

```text
기존 row가 있고 content가 이미 있으면 document.xml 재조회 생략
기존 row가 없거나 content가 NULL이면 document.xml 조회
force=True 옵션이면 content가 있어도 재조회
```

이를 위해 기존 row 조회 결과에서 `content` 유무를 확인한다.

```python
existing = repo.get_by_receipt_nos(receipt_nos)

should_fetch_content = (
    item.receipt_no not in existing
    or not existing[item.receipt_no].content
    or force_content_refresh
)
```

### 6. 검증 스크립트 확장

`scripts/validate_dart_filing_ingestion.py`에 content 검증 출력을 추가한다.

추가 출력:

```text
[RESULT] symbol=005930 fetched=10 inserted=... updated=... content_fetched=... content_failed=...
- receipt_no title content_len=12345 source_url=...
```

확인할 것:

- `content_len > 0`인 row가 생기는지
- 같은 명령 재실행 시 기존 content가 유지되는지
- 본문 실패가 있어도 metadata 저장은 되는지

추가 옵션:

```text
--with-content / --no-content
--force-content-refresh
--content-limit
```

초기에는 기본값을 `--with-content`로 할지 신중히 정한다.

추천 초기값:

```text
수동 검증 스크립트: 기본 with-content
watchlist background sync: 기본 with-content, 단 limit/timeout 보수적으로
```

### 7. DB 저장 정책

`filing_cache.content`에는 정제된 plain text를 저장한다.

저장 예시:

| column | 값 |
| --- | --- |
| `content` | document.xml에서 추출한 본문 텍스트 |
| `summary` | 계속 `NULL` |
| `source_url` | DART viewer URL 유지 |

`summary`가 계속 `NULL`인 이유:

```text
summary는 단순 수집 단계에서 만들지 않는다.
향후 LLM 요약 또는 RAG 파이프라인에서 별도 생성한다.
```

## 실패 처리 정책

### document.xml 호출 실패

처리:

```text
로그 기록
content_failed_count 증가
metadata 저장은 계속 진행
content는 기존 값 유지 또는 NULL
```

### document.xml 응답이 ZIP이 아닌 경우

처리:

```text
DartApiError로 감싸서 상위 service에서 처리
metadata 저장은 계속 진행
```

### XML 파싱 실패

처리:

```text
가능하면 HTML/text fallback 시도
fallback도 실패하면 content 저장 생략
```

### 본문이 너무 큰 경우

초기 정책 후보:

```text
최대 1~2MB 텍스트까지만 저장
초과 시 앞부분 또는 주요 텍스트만 저장
로그에 truncated 표시
```

단, DB `text` 타입 자체는 큰 텍스트 저장이 가능하므로 실제 제한은 성능과 RAG 활용 방식에 맞춰 정한다.

## 구현 순서

### Phase 0. 주가 영향 공시 유형 필터링 추가

본문 조회(`document.xml`)는 공시 건당 API 호출 1번이다. 필터 없이 30일치 100건을 모두 처리하면 API 호출량과 처리 시간이 불필요하게 커진다. 본문 저장 전에 먼저 주가 영향이 큰 공시만 남기는 필터를 추가한다.

#### 주가 영향 기준 공시 유형

DART API 응답의 `pblntf_ty` 필드 기준으로 분류한다.

| `pblntf_ty` | 유형명 | 포함 공시 예시 |
|---|---|---|
| `A` | 정기공시 | 사업보고서, 반기보고서, 분기보고서 |
| `B` | 주요사항보고 | 유상증자, 무상증자, 합병, 분할, CB/BW 발행, 자기주식 취득·처분, 영업양수도 |
| `I` | 거래소공시 | 공정공시 (실적 전망, 수주 등), 주요경영사항 |

제외 대상:

| `pblntf_ty` | 유형명 | 제외 이유 |
|---|---|---|
| `D` | 지분공시 | 5% 이상 보유·변동 보고, 주가 영향 간접적 |
| `E` | 기타공시 | 잡다한 행정 공시 포함 |
| `F` | 외부감사관련 | 감사인 선임 등 루틴 공시 |
| `G` | 펀드공시 | 해당 없음 |
| `H` | 자산유동화 | 해당 없음 |
| `J` | 공정위공시 | 해당 없음 |

단, `pblntf_ty`가 `NULL`인 공시는 일단 통과시킨다. 유형 정보가 없어도 `report_nm`과 `rcept_no`가 있으면 metadata 저장은 유지한다.

#### 구현 위치

`app/domain/filing_ingestion.py`의 `FilingIngestionService`에 허용 유형 집합을 추가하고, `_is_valid_item`에서 필터한다.

변경 전:

```python
@staticmethod
def _is_valid_item(item: DartFilingItem) -> bool:
    return bool(item.receipt_no and item.report_name)
```

변경 후:

```python
ALLOWED_PBLNTF_TYPES: frozenset[str] = frozenset({"A", "B", "I"})

@classmethod
def _is_valid_item(cls, item: DartFilingItem) -> bool:
    if not (item.receipt_no and item.report_name):
        return False
    if item.filing_type and item.filing_type not in cls.ALLOWED_PBLNTF_TYPES:
        return False
    return True
```

`filing_type`이 `None`이면 필터를 통과시키는 이유: DART 응답에서 유형 필드가 빠진 경우도 있어 해당 케이스를 버리지 않는다.

#### 검증

필터 적용 전후로 `skipped_count` 값 변화를 확인한다.

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 100
```

기대:

```text
fetched=N  skipped=M  (M이 필터된 비주요 공시 수)
inserted/updated 건수는 fetched - skipped 와 일치
```

---

### Phase 1. DART document.xml client 추가

작업:

- `DartClient.fetch_document_text(receipt_no)` 추가
- ZIP 해제 helper 추가
- XML text 추출 helper 추가
- 단위성 검증 또는 수동 실행 스크립트로 receipt_no 하나 테스트

검증:

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_document_fetch --receipt-no 20260515002181
```

필요하면 위 전용 스크립트를 새로 만든다.

### Phase 2. Repository content upsert 지원

작업:

- `upsert_filing(content=...)` 인자 추가
- insert 시 content 저장
- update 시 content가 있을 때만 덮어쓰기
- summary는 계속 보존

검증:

```text
content 있는 insert 성공
content 있는 update 성공
content=None update 시 기존 content 유지
```

### Phase 3. Ingestion service에 본문 조회 연결

작업:

- valid item마다 document.xml 조회
- content fetch 성공/실패 카운트 추가
- 기존 content가 있는 경우 재조회 생략 정책 적용
- 실패해도 metadata 저장 계속 진행

검증:

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 3
```

기대:

```text
fetched=3
content_fetched >= 1
filing_cache.content 일부 row에 값 저장
```

### Phase 4. watchlist background sync 검증

작업:

- 장바구니 생성 시 기존 background sync 흐름 유지
- 공시 목록 + 본문까지 저장되는지 확인

검증:

```bash
conda run -n tickertaka311 python -m scripts.validate_watchlist_api
```

그리고 DB에서 확인:

```sql
SELECT
    symbol,
    filing_title,
    dart_receipt_no,
    length(content) AS content_len,
    source_url,
    disclosed_at
FROM filing_cache
WHERE symbol = '005930'
ORDER BY disclosed_at DESC
LIMIT 10;
```

## 최종 성공 기준

- [ ] `document.xml`로 receipt_no 기준 원문 조회 가능
- [ ] XML/HTML에서 plain text 추출 가능
- [ ] `filing_cache.content`에 본문 저장
- [ ] 같은 공시 재수집 시 content 중복 조회를 피하거나 기존 content 유지
- [ ] 본문 조회 실패 시에도 metadata 저장은 계속됨
- [ ] `summary`는 기존 값 보존 또는 `NULL` 유지
- [ ] watchlist 추가 background sync에서 목록 + 본문 저장까지 동작

## document.xml 실측 결과 (2026-05-22)

실제 DART API를 호출해 ZIP 구조와 파싱 방식을 확인했다.

### 발견 1. ZIP 구조

항상 파일 하나, 이름은 `{rcept_no}.xml`로 고정된다.

```text
20260515002181.zip
└── 20260515002181.xml   (4,652,615 bytes — 분기보고서)

20260318001062.zip
└── 20260318001062.xml   (35,044 bytes — 자기주식 취득 결정)
```

**해결방안**

파일 선택 로직을 단순하게 짤 수 있다. `namelist()[0]`으로 첫 번째 파일을 가져오되, `.xml`로 끝나는지 확인해 예외를 잡는다.

```python
with zipfile.ZipFile(io.BytesIO(content)) as z:
    names = [n for n in z.namelist() if n.lower().endswith(".xml")]
    if not names:
        raise DartApiError("document.xml ZIP에 XML 파일 없음")
    xml_bytes = z.read(names[0])
```

복수 파일이 포함될 경우를 대비해 `namelist()[0]` 대신 `.xml` 필터를 거친 뒤 첫 번째를 선택한다.

---

### 발견 2. `pblntf_ty` 응답에 없음 — Phase 0 전략 수정 필요

DART `list.json` 응답 item에 `pblntf_ty`, `pblntf_detail_ty` 필드가 **포함되지 않는다**.

실제 응답 item 키:

```text
corp_code, corp_name, stock_code, corp_cls, report_nm, rcept_no, flr_nm, rcept_dt, rm
```

`pblntf_ty` 파라미터는 API 요청 필터로는 동작하지만, 응답에 해당 값이 내려오지 않는다. 따라서 현재 코드의 `filing_type`은 항상 `None`이 되고, Phase 0에서 설계한 `_is_valid_item` 기반 post-processing 필터는 동작하지 않는다.

**해결방안: API 파라미터 필터로 전환**

list.json 요청 시 `pblntf_ty` 파라미터를 사용해 A, B, I 유형을 각각 조회한 뒤 합친다.

`app/external/dart.py` — `list_filings` 시그니처 변경:

```python
def list_filings(
    self,
    corp_code: str,
    *,
    begin_date: date,
    end_date: date,
    pblntf_types: list[str] | None = None,  # None이면 전체 조회
    page_count: int = 100,
    last_report_only: bool = False,
    timeout: float = 10.0,
) -> list[DartFilingItem]:
    if pblntf_types:
        results: list[DartFilingItem] = []
        for ty in pblntf_types:
            results += self._fetch_list_page(
                corp_code, begin_date=begin_date, end_date=end_date,
                pblntf_ty=ty, page_count=page_count,
                last_report_only=last_report_only, timeout=timeout,
            )
        return results
    return self._fetch_list_page(
        corp_code, begin_date=begin_date, end_date=end_date,
        page_count=page_count, last_report_only=last_report_only, timeout=timeout,
    )
```

기존 HTTP 요청 로직은 `_fetch_list_page()`로 분리한다.

`app/domain/filing_ingestion.py` — 호출 시 유형 지정:

```python
FILING_TYPES_TO_SYNC = ["A", "B", "I"]

items = self.dart_client.list_filings(
    corp_code.corp_code,
    begin_date=begin_date,
    end_date=today,
    pblntf_types=self.FILING_TYPES_TO_SYNC,
    page_count=self.PAGE_COUNT,
)
```

이 방식은 API 호출이 3번으로 늘지만, 응답 건수가 줄어 이후 content fetch 횟수도 감소한다. 유형별 독립 호출이라 에러 처리도 유형 단위로 분리 가능하다.

`_is_valid_item`의 `ALLOWED_PBLNTF_TYPES` 기반 필터는 이제 필요 없으므로 원래대로 유지한다:

```python
@staticmethod
def _is_valid_item(item: DartFilingItem) -> bool:
    return bool(item.receipt_no and item.report_name)
```

---

### 발견 3. XML 파싱 — ElementTree 실패, lxml 필요

| 공시 유형 | 압축 해제 크기 | ElementTree | BeautifulSoup `features="xml"` (lxml) |
|---|---|---|---|
| 분기보고서 (A) | 4.6 MB | **실패** — not well-formed | 미테스트 |
| 자기주식취득 (B) | 35 KB | 미테스트 | **성공** — 2,786자 추출 |

`ElementTree`는 well-formed XML만 허용하는데, DART 분기보고서 XML은 특수문자나 인코딩 문제로 파싱이 실패한다.

**해결방안: lxml 우선, HTML fallback**

`app/external/dart.py`에 추가할 텍스트 추출 로직:

```python
import re
from bs4 import BeautifulSoup

@staticmethod
def _extract_text(xml_bytes: bytes) -> str:
    # 1차: lxml XML 파서 (엄격하지만 빠름)
    try:
        soup = BeautifulSoup(xml_bytes, features="xml")
    except Exception:
        # 2차: lxml HTML 파서 (관대하게 파싱)
        soup = BeautifulSoup(xml_bytes, features="lxml")
    text = re.sub(r"\s+", " ", soup.get_text()).strip()
    return text
```

`lxml`은 `requirements.txt`에 이미 포함되어 있어야 한다. 없으면 추가한다:

```bash
pip install lxml
```

---

### 발견 4. 크기 이슈 — A타입 본문 저장 정책 필요

| 유형 | 예시 | 압축 해제 크기 |
|---|---|---|
| A — 분기보고서 | 20260515002181 | **4.6 MB** |
| B — 자기주식취득 | 20260318001062 | 35 KB |

분기보고서는 재무제표 전체가 포함된 문서라 텍스트 추출 시 수십만 자가 나온다. 전체를 DB에 저장하면 row 크기가 과도하고 RAG 청킹 전에도 처리 부담이 크다.

**해결방안: 최대 길이 cap + truncation 표시**

```python
MAX_CONTENT_CHARS = 200_000  # 약 200KB 텍스트

text = self._extract_text(xml_bytes)
if len(text) > MAX_CONTENT_CHARS:
    text = text[:MAX_CONTENT_CHARS] + "\n[truncated]"
```

`[truncated]` 마커를 붙여두면 나중에 RAG 파이프라인이 본문이 잘렸다는 사실을 알 수 있다.

A타입 정기공시는 분량이 많아 초기 단계에서는 **content fetch 대상에서 제외**하는 것도 검토할 수 있다. `pblntf_types=["B", "I"]`로만 content를 가져오고, A타입은 metadata(source_url)만 저장하는 방식이다. 실제 RAG에서 분기보고서가 필요해질 때 별도 파이프라인으로 처리하는 편이 나을 수 있다.

---

## 파일별 수정 명세

### `app/external/dart.py` — Phase 1

**추가할 것:**

```python
def fetch_document_text(self, receipt_no: str, *, timeout: float = 20.0) -> str | None:
    """document.xml ZIP을 받아 plain text를 반환. 실패 시 None."""
```

- `GET /api/document.xml?crtfc_key=...&rcept_no=...` 호출
- 응답이 ZIP이 아니면 `DartApiError` raise
- ZIP 안에서 XML 파일 선택 기준 필요 (→ ZIP 구조 먼저 확인 후 결정)
- XML에서 `ElementTree.itertext()`로 텍스트 추출
- XML 파싱 실패 시 BeautifulSoup fallback
- 결과가 빈 문자열이면 `None` 반환

**추가할 private 메서드:**

```python
@staticmethod
def _parse_document_zip(content: bytes) -> str | None: ...

@staticmethod
def _extract_text_from_xml(xml_bytes: bytes) -> str: ...
```

---

### `app/domain/filing_ingestion.py` — Phase 0, Phase 3

**Phase 0: `_is_valid_item` 변경**

```python
# 추가
ALLOWED_PBLNTF_TYPES: frozenset[str] = frozenset({"A", "B", "I"})

# 변경 전
@staticmethod
def _is_valid_item(item: DartFilingItem) -> bool:
    return bool(item.receipt_no and item.report_name)

# 변경 후
@classmethod
def _is_valid_item(cls, item: DartFilingItem) -> bool:
    if not (item.receipt_no and item.report_name):
        return False
    if item.filing_type and item.filing_type not in cls.ALLOWED_PBLNTF_TYPES:
        return False
    return True
```

**Phase 3: `SyncFilingResult` 확장**

```python
# 추가할 필드
content_fetched_count: int = 0
content_failed_count: int = 0
```

**Phase 3: `sync_filings_for_ticker` 시그니처 변경**

```python
def sync_filings_for_ticker(
    self,
    symbol: str,
    *,
    lookback_days: int | None = None,
    limit: int | None = None,
    with_content: bool = True,       # 추가
    force_content_refresh: bool = False,  # 추가
) -> SyncFilingResult:
```

**Phase 3: 루프 내부 content 조회 추가**

```python
for item in valid_items:
    content = None
    should_fetch = (
        with_content
        and (
            item.receipt_no not in existing
            or not existing[item.receipt_no].content
            or force_content_refresh
        )
    )
    if should_fetch:
        try:
            content = self.dart_client.fetch_document_text(item.receipt_no)
            result.content_fetched_count += 1
        except DartApiError:
            logger.warning("content fetch failed: %s", item.receipt_no)
            result.content_failed_count += 1

    self.repo.upsert_filing(..., content=content)
```

---

### `app/repositories/filing_cache_repository.py` — Phase 2

**`upsert_filing` 시그니처 변경**

```python
def upsert_filing(
    self,
    *,
    symbol: str,
    filing_title: str,
    filing_type: str | None,
    dart_receipt_no: str,
    source_url: str,
    disclosed_at: datetime | None,
    ttl_until: datetime,
    content: str | None = None,   # 추가
) -> UUID | None:
```

**insert values 변경**

```python
# 변경 전
content=None,

# 변경 후
content=content,
```

**on_conflict update 변경**

```python
# content: None이면 기존 값 유지, 새 값이 있으면 덮어쓰기
"content": case(
    (stmt.excluded.content.is_not(None), stmt.excluded.content),
    else_=FilingCache.content,
),
```

`from sqlalchemy import case` import 추가 필요.

---

### `scripts/validate_dart_filing_ingestion.py` — Phase 3, 4

**추가할 CLI 옵션**

```python
parser.add_argument("--with-content", action="store_true", default=True)
parser.add_argument("--no-content", dest="with_content", action="store_false")
parser.add_argument("--force-content-refresh", action="store_true", default=False)
```

**출력 변경**

```python
# 변경 전
[RESULT] symbol=... fetched=... inserted=... updated=... skipped=...

# 변경 후
[RESULT] symbol=... fetched=... inserted=... updated=... skipped=... content_fetched=... content_failed=...
- receipt_no title content_len=12345 source_url=...
```

---

### `scripts/validate_dart_document_fetch.py` — Phase 1 (신규 파일)

Phase 1 완료 후 단독으로 `document.xml` 호출과 텍스트 추출을 검증하는 스크립트.

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_document_fetch \
  --receipt-no 20260515002181
```

출력 예시:

```text
receipt_no: 20260515002181
content_len: 8432
preview: 분기보고서 제출인 삼성전자 주식회사...
```

---

## 주의사항

- 본문 저장은 API 호출량과 처리 시간이 늘어난다.
- 공시마다 XML 구조가 달라 본문 품질이 균일하지 않을 수 있다.
- 장기적으로 RAG에 바로 쓰려면 `content` 원문 저장 이후 chunking/embedding 파이프라인이 별도로 필요하다.
- 현재 목적이 “DART 화면 리다이렉션”이면 metadata 저장만으로 충분하다.
- 본문 저장은 RAG, 검색, 요약 기능을 실제로 붙일 때 의미가 커진다.
