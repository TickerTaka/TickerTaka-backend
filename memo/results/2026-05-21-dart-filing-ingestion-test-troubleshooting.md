# DART 공시 적재 테스트 실패 원인 정리

## 상황

DART 공시 캐시 적재 검증을 위해 아래 스크립트를 실행하려고 했다.

```bash
python3 -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

목표는 `DART_API_KEY`를 사용해 DART 공시 목록을 가져오고, `filing_cache`에 다음 값을 저장하는지 확인하는 것이었다.

```text
symbol
filing_title
filing_type
dart_receipt_no
source_url
disclosed_at
ttl_until
```

하지만 실제 DART API 호출과 DB 저장까지 도달하지 못했다. 원인은 API 키 문제가 아니라 로컬 Python 실행 환경과 설정 로딩 문제였다.

## 발생한 문제들

### 1. 기본 `python` 명령 없음

처음 문법 검증을 아래처럼 실행했다.

```bash
python -m compileall app scripts
```

결과:

```text
zsh:1: command not found: python
```

현재 macOS 환경에서는 `python` 명령이 없고 `python3`만 있었다.

대응:

```bash
python3 -m compileall app scripts
```

로 다시 시도했다.

## 2. 시스템 `python3`에 프로젝트 의존성 없음

검증 스크립트를 시스템 `python3`로 실행했을 때 실패했다.

```bash
python3 -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

에러:

```text
ModuleNotFoundError: No module named 'sqlalchemy'
```

원인:

- `/usr/bin/python3`에는 프로젝트 의존성이 설치되어 있지 않음
- `sqlalchemy`, `pydantic-settings` 등이 없는 상태

## 3. `compileall` 캐시 경로 권한 문제

시스템 `python3`로 `compileall` 실행 시 `~/Library/Caches` 아래에 pycache를 쓰려다가 권한 오류가 났다.

에러:

```text
PermissionError: [Errno 1] Operation not permitted:
'/Users/ohheungchan/Library/Caches/com.apple.python/...'
```

대응:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/tickertaka_pycache python3 -m compileall app scripts
```

결과:

```text
compileall 통과
```

즉 문법 수준에서는 현재 코드가 컴파일된다.

## 4. `tf_env`에도 핵심 의존성 일부 없음

사용자에게 `tf_env` conda 환경이 있다고 해서 아래로 실행했다.

```bash
conda run -n tf_env python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

초기 에러:

```text
ModuleNotFoundError: No module named 'sqlalchemy'
```

확인 결과:

```text
requests: ok
pydantic: ok
pydantic_settings: missing
psycopg2: ok
sqlalchemy: missing
```

대응:

```bash
conda run -n tf_env python -m pip install SQLAlchemy==2.0.35 pydantic-settings==2.5.2
```

설치 후 `sqlalchemy`, `pydantic-settings` 문제는 통과했다.

## 5. `.env.example` 기반 `.env`와 `Settings` 불일치

`.env.example`을 기반으로 `.env`를 만들었는데, `.env.example`에는 아래 키가 있다.

```env
DEFAULT_LLM_MODEL=openai/gpt-4o-mini
JUDGE_LLM_MODEL=anthropic/claude-haiku-4-5
```

하지만 현재 `app/config.py`의 `Settings`에는 이 필드가 없었다.

에러:

```text
pydantic_core._pydantic_core.ValidationError
DEFAULT_LLM_MODEL
  Extra inputs are not permitted
JUDGE_LLM_MODEL
  Extra inputs are not permitted
```

대응:

`app/config.py`에 아래 설정을 추가했다.

```python
extra="ignore"
```

의미:

- `.env`에 현재 코드가 사용하지 않는 키가 있어도 설정 로딩에서 실패하지 않음
- `.env.example`을 복사해 `.env`를 만드는 운영 방식과 잘 맞음

## 6. `tf_env`가 Python 3.9라서 `datetime.UTC` 사용 불가

다음 에러:

```text
ImportError: cannot import name 'UTC' from 'datetime'
```

원인:

- `datetime.UTC`는 Python 3.11에서 추가됨
- `tf_env`는 Python 3.9.21

확인:

```bash
conda run -n tf_env python --version
```

결과:

```text
Python 3.9.21
```

대응:

`app/domain/filing_ingestion.py`에서 아래처럼 변경했다.

```python
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
```

## 7. `tf_env`가 Python 3.9라서 `dataclass(slots=True)` 사용 불가

다음 에러:

```text
TypeError: dataclass() got an unexpected keyword argument 'slots'
```

원인:

- `dataclass(slots=True)`는 Python 3.10+ 기능
- `tf_env`는 Python 3.9.21

대응:

새로 추가한 DART 관련 dataclass에서 `slots=True`를 제거했다.

대상:

- `app/external/dart.py`
- `app/domain/filing_ingestion.py`

## 8. 프로젝트 전체 모델이 Python 3.10+ 타입 문법 사용

마지막으로 막힌 에러:

```text
sqlalchemy.exc.ArgumentError:
Could not resolve all types within mapped annotation: "Mapped[float | None]"
```

원인:

- `app/models/cache.py` 등 기존 모델이 `float | None`, `str | None` 같은 PEP 604 타입 문법을 사용
- 이 문법은 Python 3.10+에서 정상 지원
- 현재 `tf_env`는 Python 3.9라 SQLAlchemy가 문자열 annotation을 평가하다가 실패

중요:

이 문제는 DART 코드만의 문제가 아니다. 프로젝트 기존 모델 전체가 Python 3.10+ 문법을 전제로 작성되어 있다.

따라서 `tf_env` Python 3.9에서는 DART 검증 스크립트뿐 아니라 SQLAlchemy 모델을 import하는 다른 스크립트도 같은 류의 문제를 만날 수 있다.

## 현재 결론

실패 원인은 DART API 키나 DART API 응답 문제가 아니다.

아직 실제 DART API 호출 단계까지 도달하지 못했다.

현재 차단 원인:

```text
tf_env Python 버전이 3.9라서 프로젝트 SQLAlchemy 모델 타입 힌트와 호환되지 않음
```

## 권장 해결 방법

### 권장안 1. Python 3.11 환경 새로 만들기

프로젝트 README와 기존 코드 스타일상 Python 3.11 이상 환경이 가장 적합하다.

예시:

```bash
conda create -n tickertaka python=3.11
conda activate tickertaka
pip install -r requirements.txt
python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

### 권장안 2. 기존 `tf_env`를 Python 3.10+로 올리기

가능은 하지만 TensorFlow 환경이면 다른 패키지와 충돌할 수 있다.

따라서 DART/백엔드 검증용 환경은 별도 conda env로 분리하는 편이 안전하다.

### 권장안 3. 프로젝트 모델 타입 힌트를 Python 3.9 호환으로 전부 변경

예:

```python
float | None
```

를

```python
Optional[float]
```

로 바꾸는 방식.

하지만 기존 모델 전체에 영향이 크고, 프로젝트가 이미 Python 3.10+ 스타일로 작성되어 있으므로 추천하지 않는다.

## 다음 테스트 절차

Python 3.11 환경에서 아래 순서로 재시도한다.

```bash
python --version
pip install -r requirements.txt
python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

성공 시 확인할 것:

- DART `corpCode.xml` 다운로드 성공
- `005930 -> corp_code` 매핑 성공
- DART `list.json` 조회 성공
- `filing_cache`에 row insert 또는 update
- `source_url`이 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...` 형태로 저장
- 같은 스크립트를 두 번 실행해도 중복 row가 생기지 않음

## 부가 메모

- 테스트 중 `.venv`가 한 번 생성되었지만 `.gitignore` 대상이라 커밋에는 포함되지 않는다.
- `.env`도 `.gitignore` 대상이다.
- `OPENROUTER_API_KEY` 미설정 warning은 이번 DART 검증과 직접 관련 없다.

---

## Claude의 해결방안

### 현재 상태 요약 (2차 Codex 피드백 반영)

| 항목 | 상태 |
|---|---|
| `tf_env` Python 3.11 in-place 업그레이드 | 실패 — TF 2.9 의존성 충돌 |
| `tickertaka311` Python 3.11 환경 생성 | **완료** |
| `requirements.txt` 설치 | **완료** |
| SQLAlchemy 모델 import (`Mapped[float \| None]` 에러) | **해결됨** |
| `DATABASE_URL` 설정 | **미완료** — 예시값 상태, 실제 DB 접속 정보 필요 |
| DART API 호출 | 아직 도달 못함 |
| `filing_cache` DB 저장 검증 | 아직 도달 못함 |

Python 환경 문제는 끝났다. 지금 막혀 있는 건 `.env`의 `DATABASE_URL` 하나다.

---

### 지금 당장 해야 할 것: `.env` 수정

프로젝트 루트 `.env` 파일을 열어서 `DATABASE_URL`을 실제 DB 접속 정보로 바꾼다.

```env
# 현재 (예시값 상태)
DATABASE_URL=postgresql://USERNAME:PASSWORD@HOST:5432/DBNAME?sslmode=require

# 바꿔야 할 형태 (팀 공유 DB 또는 로컬 DB 실제 접속 정보)
DATABASE_URL=postgresql://실제유저:실제비밀번호@실제호스트:5432/실제DB명?sslmode=require
```

`DART_API_KEY`는 이미 채워져 있는 상태이므로 건드리지 않아도 된다.

---

### `DATABASE_URL` 수정 후 실행 순서

**1단계: DART 검증 스크립트 실행**

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

확인할 것:
- `corpCode.xml` 다운로드 성공
- `005930` → `corp_code` 매핑 성공
- DART `list.json` 조회 성공
- `filing_cache` insert/update 발생

**2단계: watchlist API flow 검증**

```bash
conda run -n tickertaka311 python -m scripts.validate_watchlist_api
```

확인할 것:
- watchlist 생성 시 `sync_watchlist_news`, `sync_watchlist_filings` 두 background task가 모두 등록되는지

**3단계: 서버 구동 후 end-to-end 확인**

```bash
conda activate tickertaka311
uvicorn app.main:app --reload
```

별도 터미널에서:

```bash
curl -X POST http://localhost:8000/api/watchlists \
  -H "Content-Type: application/json" \
  -d '{"user_id": "실제유저ID", "symbol": "005930"}'
```

이후 DB에서 직접 확인:

```sql
SELECT symbol, filing_title, source_url, disclosed_at, ttl_until
FROM filing_cache
WHERE symbol = '005930'
ORDER BY disclosed_at DESC
LIMIT 5;
```

---

### 최종 성공 기준 체크리스트

- [x] Python 3.11 환경(`tickertaka311`) 준비
- [x] `requirements.txt` 설치
- [x] SQLAlchemy 모델 import 통과
- [ ] `DATABASE_URL` 실제 DB 접속 정보로 수정
- [ ] `fetched_count` 1 이상
- [ ] `inserted_count` 또는 `updated_count` 1 이상
- [ ] `source_url`이 `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...` 형태로 저장
- [ ] 같은 명령 재실행 시 `inserted_count=0`, `updated_count` 증가 (중복 방지 검증)
- [ ] watchlist 생성 시 filing sync background task 등록 확인

---

## 코덱스의 실행후 피드백

### 실행 요약

Claude의 해결방안 중 **기존 `tf_env`를 유지한 채 Python만 3.11로 올리는 방법**을 실제로 시도했다.

결론은 실패다.

```text
tf_env in-place Python 3.11 업그레이드
-> conda UnsatisfiableError
```

실패 후 다시 확인한 결과, `tf_env`는 여전히 Python 3.9.21 상태다.

```bash
conda run -n tf_env python --version
```

```text
Python 3.9.21
```

따라서 DART API 호출과 `filing_cache` DB 저장 검증은 아직 실행 가능한 단계까지 도달하지 못했다.

### 실제로 실행한 작업

먼저 현재 `tf_env` 상태를 확인했다.

```bash
conda run -n tf_env python --version
```

결과:

```text
Python 3.9.21
```

그 다음 업그레이드 전에 환경 백업을 만들었다.

```bash
conda env export -n tf_env --file tf_env-backup-before-upgrade.yml
```

결과:

```text
exit code 0
```

백업 파일은 생성되었다.

```text
tf_env-backup-before-upgrade.yml
```

다만 export 중에 `conda-meta` 일부 파일을 정리하지 못했다는 warning이 있었다.

```text
Could not remove or rename .../conda-meta/numpy-base-...
Could not remove or rename .../conda-meta/wheel-...
Could not remove or rename .../conda-meta/pip-...
Could not remove or rename .../conda-meta/setuptools-...
```

백업 파일 생성 자체는 성공했지만, 현재 `tf_env`가 꽤 복잡한 상태라는 신호로 볼 수 있다.

이후 Python 3.11 업그레이드를 시도했다.

```bash
conda run -n tf_env conda install python=3.11 -y
```

결과:

```text
UnsatisfiableError
```

### 실패 원인

`tf_env`는 단순 백엔드용 Python 환경이 아니라 TensorFlow 2.9 계열 패키지가 들어 있는 환경이다.

충돌에 관련된 핵심 패키지는 다음과 같다.

```text
tensorflow-deps=2.9.0
tensorflow-macos==2.9.0
tensorflow-metal==0.5.0
h5py
numpy
numpy-base
grpcio
libgrpc
libprotobuf
hdf5
zlib
zstd
```

현재 구조는 이렇다.

```text
백엔드 프로젝트: Python 3.10+ 문법 사용
현재 tf_env: TensorFlow 2.9 + Python 3.9 기반
업그레이드 시도: TensorFlow 계열 의존성과 Python 3.11이 충돌
```

그래서 `tf_env`를 그대로 둔 채 Python만 3.11로 올리는 방식은 conda solver 단계에서 막혔다.

### 왜 DART 검증까지 못 갔는가

DART 검증 스크립트의 기존 차단 지점은 아래 에러였다.

```text
sqlalchemy.exc.ArgumentError:
Could not resolve all types within mapped annotation: "Mapped[float | None]"
```

이 에러는 DART API 키 문제가 아니다.

프로젝트 SQLAlchemy 모델이 `float | None`, `str | None` 같은 Python 3.10+ 타입 문법을 사용하고 있는데, 현재 `tf_env`는 Python 3.9라 모델 import 단계에서 멈춘다.

즉 실제 진행 흐름은 여기서 끊긴다.

```text
Settings 로딩
-> SQLAlchemy 모델 import
-> 여기서 실패
-> DB 연결 못 감
-> DART corpCode.xml 호출 못 감
-> DART list.json 호출 못 감
-> filing_cache upsert 못 감
```

이번에도 Python 3.11 업그레이드가 실패했기 때문에, 같은 오류가 반복될 것이 확실해서 DART 검증 스크립트는 재실행하지 않았다.

### 실행하지 않은 작업

아래 작업은 실행하지 않았다.

```bash
conda remove -n tf_env --all -y
conda create -n tf_env python=3.11 -y
```

이 방법은 기존 `tf_env`를 완전히 삭제하고 같은 이름으로 다시 만드는 작업이다.

기존 TensorFlow 환경과 설치 패키지가 사라지는 destructive 작업이라 사용자 명시 승인 없이 진행하지 않았다.

### 현재 상태

현재 정리하면 다음과 같다.

```text
tf_env: Python 3.9.21 유지
tf_env in-place Python 3.11 업그레이드: 실패
DART API 호출: 아직 도달 못함
filing_cache DB 저장 검증: 아직 도달 못함
```

또한 생성된 백업 파일이 있다.

```text
tf_env-backup-before-upgrade.yml
```

이 파일은 로컬 conda 환경 백업 파일이라 Git 커밋 대상에 포함하지 않는 것이 좋다.

### 다음 선택지

선택지는 두 개다.

#### 선택지 1. `tf_env`를 삭제하고 Python 3.11 환경으로 재생성

`tf_env` 이름을 반드시 유지해야 하고, 기존 TensorFlow 환경을 버려도 된다면 이 방법을 쓴다.

```bash
conda env export -n tf_env > tf_env-backup-before-recreate.yml
conda remove -n tf_env --all -y
conda create -n tf_env python=3.11 -y
conda activate tf_env
pip install -r requirements.txt
python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

이 방법은 기존 `tf_env`를 삭제하므로 실행 전 사용자 승인이 필요하다.

#### 선택지 2. `tf_env`는 보존하고 백엔드용 Python 3.11 환경을 따로 생성

기존 TensorFlow 환경을 살리고 싶다면 이 방법이 더 안전하다.

```bash
conda create -n tickertaka311 python=3.11 -y
conda activate tickertaka311
pip install -r requirements.txt
python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

이 방법은 `tf_env`를 건드리지 않는다.

### 코덱스 판단

현재 확인된 사실만 보면 `tf_env`를 그대로 업그레이드하는 방법은 실패했다.

```text
방법 A: tf_env 유지 + Python만 3.11 업그레이드
-> 실패 확인
```

따라서 앞으로는 둘 중 하나를 선택해야 한다.

```text
tf_env 이름을 꼭 써야 함 -> 기존 tf_env 삭제 후 재생성
기존 tf_env를 살려야 함 -> 백엔드용 새 Python 3.11 환경 생성
```

결정 전까지는 DART 검증 스크립트 실행은 보류한다.

---

## 코덱스의 실행후 피드백 2차

### 실행 요약

업데이트된 Claude 해결방안 기준으로 **선택지 2: `tf_env`는 보존하고 백엔드용 Python 3.11 환경을 따로 생성**하는 방향을 실제로 실행했다.

이번에는 Python 3.11 환경 생성과 의존성 설치까지 성공했다.

```text
tickertaka311 생성: 성공
requirements.txt 설치: 성공
Python 버전: Python 3.11.15
```

그리고 DART 검증 스크립트와 watchlist API 검증 스크립트를 실행해봤다.

결론:

```text
기존 Python 3.9 / SQLAlchemy 타입 힌트 문제는 해결됨
새 차단점은 .env의 DATABASE_URL이 아직 예시값이라는 점
```

### 실제 실행한 작업

먼저 기존 `tf_env`에서 DART 검증을 다시 실행해 현재 실패 지점을 재확인했다.

```bash
conda run -n tf_env python --version
conda run -n tf_env python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

결과:

```text
Python 3.9.21
sqlalchemy.exc.ArgumentError:
Could not resolve all types within mapped annotation: "Mapped[float | None]"
```

즉 기존 `tf_env`는 여전히 Python 3.9라서 사용할 수 없다.

그 다음 새 Python 3.11 환경을 생성했다.

```bash
conda create -n tickertaka311 python=3.11 -y
```

결과:

```text
environment location: /Users/ohheungchan/anaconda3/envs/tickertaka311
Python 3.11.15
```

프로젝트 의존성도 설치했다.

```bash
conda run -n tickertaka311 python -m pip install -r requirements.txt
```

결과:

```text
Successfully installed ...
```

설치 과정에서 pip dependency resolver가 오래 걸렸지만 최종적으로 실패 없이 끝났다.

### DART 검증 스크립트 실행 결과

Python 3.11 환경에서 DART 검증 스크립트를 실행했다.

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

이번에는 기존 차단점이던 아래 에러가 발생하지 않았다.

```text
Mapped[float | None]
```

즉 Python 3.11 환경에서는 SQLAlchemy 모델 import 문제가 해결됐다.

하지만 다음 단계인 DB 연결에서 실패했다.

에러:

```text
psycopg2.OperationalError:
could not translate host name "HOST" to address:
nodename nor servname provided, or not known
```

원인:

```text
.env의 DATABASE_URL이 실제 DB 주소가 아니라 .env.example의 예시값 상태임
```

현재 흐름은 여기까지 진행됐다.

```text
Settings 로딩
-> SQLAlchemy 모델 import 성공
-> DB 연결 시도
-> DATABASE_URL의 HOST 해석 실패
-> DART corpCode.xml 호출 전 중단
```

따라서 이번에는 DART API 키 검증까지도 아직 도달하지 못했다.

### watchlist API 검증 결과

Claude 해결방안의 3단계였던 watchlist API flow 검증도 같은 Python 3.11 환경에서 실행했다.

```bash
conda run -n tickertaka311 python -m scripts.validate_watchlist_api
```

결과는 DART 검증과 동일하게 DB 연결 단계에서 실패했다.

에러:

```text
psycopg2.OperationalError:
could not translate host name "HOST" to address
```

원인도 동일하다.

```text
validate_watchlist_api.py가 seed user를 조회하려고 DB에 연결하는데,
DATABASE_URL의 host가 실제 DB host가 아니라 HOST 예시 문자열이라 실패함
```

### `.env` 상태 확인

`.env`에는 아래 항목들이 존재한다.

```text
DART_API_KEY 존재
DATABASE_URL 존재
```

하지만 `DATABASE_URL`은 아직 실제 접속 가능한 DB 주소가 아니라 예시값 형태다.

```text
postgresql://USERNAME:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

따라서 다음 테스트를 진행하려면 `DATABASE_URL`을 실제 DB 접속 문자열로 바꿔야 한다.

### 현재 상태

현재 상태는 이전보다 한 단계 전진했다.

이전 차단점:

```text
Python 3.9라서 SQLAlchemy 모델 import 실패
```

현재 차단점:

```text
Python 3.11 환경은 준비됨
모델 import도 통과함
하지만 DATABASE_URL이 예시값이라 DB 연결 실패
```

즉 지금 문제는 더 이상 Python 버전 문제가 아니다.

### 다음 액션

다음으로 해야 할 일은 `.env`의 `DATABASE_URL`을 실제 DB 접속 정보로 바꾸는 것이다.

필요한 값:

```text
DB username
DB password
DB host
DB port
DB name
sslmode 필요 여부
```

실제 DB URL이 들어간 뒤 다시 실행할 명령:

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
conda run -n tickertaka311 python -m scripts.validate_watchlist_api
```

그때 확인할 것:

```text
DART corpCode.xml 호출 여부
005930 -> corp_code 매핑 여부
DART list.json 호출 여부
filing_cache insert/update 여부
watchlist 생성 시 news sync와 filing sync background task 등록 여부
```

---

## 코덱스의 실행후 피드백 3차

### 실행 요약

사용자가 실제 클라우드 DB 접속 정보를 제공해서 `.env`의 `DATABASE_URL`을 클라우드 PostgreSQL로 수정했다.

```text
DATABASE_URL: 클라우드 PostgreSQL stock_debate DB로 변경
```

그 뒤 `tickertaka311` Python 3.11 환경에서 DART 공시 적재 검증을 다시 실행했다.

이번에는 성공했다.

```text
DART API 호출: 성공
005930 공시 목록 조회: 성공
filing_cache insert: 성공
중복 방지 upsert: 성공
watchlist API smoke test: 성공
```

### DART 적재 1차 실행 결과

실행:

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

결과:

```text
[RESULT] symbol=005930 fetched=10 inserted=10 updated=0 skipped=0 elapsed_ms=2956
```

확인된 저장 데이터 예시:

```text
20260515002812 동일인등출자계열회사와의상품ㆍ용역거래변경
https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002812

20260515002181 분기보고서 (2026.03)
https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260515002181
```

의미:

```text
DART corpCode.xml 조회와 stock code -> corp_code 매핑이 성공했고,
DART list.json 조회 후 filing_cache에 10건이 insert됐다.
```

### DART 적재 2차 실행 결과

같은 명령을 한 번 더 실행했다.

```bash
conda run -n tickertaka311 python -m scripts.validate_dart_filing_ingestion --symbol 005930 --limit 10
```

결과:

```text
[RESULT] symbol=005930 fetched=10 inserted=0 updated=10 skipped=0 elapsed_ms=2842
```

의미:

```text
같은 dart_receipt_no를 가진 공시가 중복 insert되지 않고 update 처리됐다.
```

따라서 `dart_receipt_no` 기준 upsert / 중복 방지 동작도 확인됐다.

### watchlist API flow 검증 결과

실행:

```bash
conda run -n tickertaka311 python -m scripts.validate_watchlist_api
```

결과:

```text
ALL SMOKE TESTS PASSED
```

통과한 항목:

```text
health check 200
POST /api/watchlists 생성 201
생성 시 sync_watchlist_news, sync_watchlist_filings background task 등록 확인
GET /api/watchlists/{user_id} 조회 200
duplicate POST 409
unknown user 404
unknown symbol 404
missing field 422
테스트 watchlist row cleanup 성공
```

### 현재 최종 상태

현재 검증 기준으로 DART 공시 적재 기능은 동작 확인됐다.

```text
Python 3.11 환경 문제: 해결
DATABASE_URL 예시값 문제: 해결
DART API 호출: 성공
클라우드 DB filing_cache 저장: 성공
중복 방지 upsert: 성공
watchlist API background task 등록: 성공
```

남은 주의사항:

```text
OPENROUTER_API_KEY 미설정 warning은 계속 출력되지만 이번 DART 검증과 직접 관련 없음
tf_env-backup-before-upgrade.yml은 로컬 환경 백업 파일이라 커밋 대상에서 제외 권장
```
