# Swagger 확인을 위한 서버 실행 트러블슈팅 보고서

> 작성일: 2026-05-28  
> 대상: FastAPI 서버 실행 실패 원인과 해결 내역  
> 기준 경로: `/Users/ohheungchan/내 드라이브/pocat/final/backend/TickerTaka-backend`

---

## 1. 결론

초기에는 원래 가상환경인 `.venv`에서 서버가 바로 실행되지 않았다. 이후 `.venv`를 Python 3.13 기반으로 재구성하고, 충돌 패키지 핀을 조정해 `app.main` import까지 성공했다.

현재 해결 상태:

```text
.venv Python: 3.13.11
.python-version: 3.13.0
import app.main: 성공
fastapi: 0.115.0
uvicorn: 0.32.0
```

현재 서버 실행 명령:

```bash
.venv/bin/uvicorn app.main:app --reload
```

Swagger 확인 URL:

```text
http://127.0.0.1:8000/docs
```

초기 실패 원인은 두 가지였다.

```text
1. 기존 .venv의 Python 버전이 프로젝트 요구 버전과 다름
2. 기존 .venv에 FastAPI/uvicorn 등 서버 실행 필수 패키지가 설치되어 있지 않음
```

초기 확인 상태:

```text
당시 .python-version: 3.12.10
당시 .venv Python: 3.9.6
당시 .venv fastapi: 설치 안 됨
당시 .venv uvicorn: 설치 안 됨
```

---

## 2. 기존 가상환경 상태

초기 기존 가상환경:

```text
.venv
```

확인한 Python 버전:

```bash
.venv/bin/python --version
```

결과:

```text
Python 3.9.6
```

당시 프로젝트가 기대하는 Python 버전:

```bash
cat .python-version
```

결과:

```text
3.12.10
```

`pyproject.toml`에도 Python 요구 조건이 있다.

```toml
requires-python = ">=3.11"
```

즉 당시 `.venv`는 프로젝트 요구사항인 Python `>=3.11`을 만족하지 않았다.

---

## 3. 기존 `.venv`에서 확인된 패키지 문제

실행한 명령:

```bash
.venv/bin/python -m pip show fastapi uvicorn
```

결과:

```text
WARNING: Package(s) not found: fastapi, uvicorn
```

의미:

- FastAPI 앱 실행에 필요한 `fastapi`가 없다.
- ASGI 서버 실행에 필요한 `uvicorn`도 없다.
- 따라서 `.venv/bin/uvicorn` 명령도 사용할 수 없다.

이 상태에서는 Swagger 확인용 서버 실행도 불가능하다.

---

## 4. `requirements.txt` 전체 설치 시도에서 발생한 문제

처음에는 기존 `.venv`에 전체 의존성을 설치하려고 했다.

실행한 명령:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

진행 중 아래 에러로 실패했다.

```text
ERROR: Could not find a version that satisfies the requirement redis==7.4.0
ERROR: No matching distribution found for redis==7.4.0
```

원인:

- 현재 `.venv`는 Python `3.9.6`이다.
- 프로젝트는 Python `>=3.11`을 요구한다.
- `requirements.txt`의 일부 최신 패키지 조합이 Python 3.9 환경과 맞지 않는다.
- 특히 `redis==7.4.0` 설치 가능 버전을 찾지 못해 전체 설치가 중단됐다.

중요:

이 문제는 단순히 `redis`만의 문제가 아니라, 가상환경 Python 버전이 프로젝트 기준과 어긋난 상태에서 전체 의존성을 설치하려 한 것이 근본 원인이다.

---

## 5. `python` 명령 문제

처음 문법 체크를 위해 아래 명령을 실행했다.

```bash
python -m compileall app/api app/schemas app/main.py
```

결과:

```text
zsh:1: command not found: python
```

원인:

- 현재 쉘에는 `python` 명령이 없고 `python3`만 있었다.

조치:

```bash
python3 -m compileall app/api app/schemas app/main.py
```

---

## 6. Python cache 권한 문제

`python3`로 컴파일 체크를 실행했을 때 아래 오류가 발생했다.

```bash
python3 -m compileall app/api app/schemas app/main.py
```

결과:

```text
PermissionError: [Errno 1] Operation not permitted:
'/Users/ohheungchan/Library/Caches/com.apple.python/...'
```

원인:

- macOS 기본 Python이 bytecode cache를 `~/Library/Caches/com.apple.python/...` 아래에 쓰려고 했다.
- 현재 작업 환경은 workspace-write 샌드박스라 워크스페이스 밖인 `~/Library/Caches`에 쓰기 권한이 없다.

조치:

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -m compileall app/api app/schemas app/main.py
```

결과:

```text
compileall 성공
```

이때 생긴 `.pycache`는 임시 컴파일 캐시라 삭제했다.

```bash
rm -rf .pycache
```

삭제한 것은 앱 코드가 아니라 임시 Python bytecode cache다.

---

## 7. 임시 `.venv311`에서 확인했던 추가 문제

서버 실행 가능성을 확인하기 위해 임시로 Python 3.11 가상환경을 만들었다.

```text
.venv311
```

이 가상환경은 이후 사용자 요청에 따라 삭제 완료했다.

임시 환경에서 최소 의존성을 설치한 뒤 앱 import를 시도했을 때 아래 오류가 발생했다.

```text
ModuleNotFoundError: No module named 'bs4'
```

발생 경로:

```text
app.main
→ app.api.watchlist
→ app.domain.watchlist_service
→ app.domain.filing_ingestion
→ app.external.dart.client
→ from bs4 import BeautifulSoup, Tag
```

의미:

- 서버는 API 라우터 등록 단계에서 watchlist 라우터도 import한다.
- watchlist 라우터는 공시 수집 서비스까지 import한다.
- 공시 수집 서비스는 DART HTML 파싱을 위해 `beautifulsoup4`가 필요하다.
- 따라서 Swagger만 확인하려고 해도 앱 전체 import에 필요한 패키지가 설치되어 있어야 한다.

주의:

- 이 문제는 기존 `.venv`에서는 `fastapi`가 없어서 그 이전 단계에서 막힌다.
- Python 3.11+ 환경에 FastAPI만 설치해도, 다음 단계에서 `bs4` 같은 도메인 의존성이 필요하다는 것을 확인한 것이다.

---

## 8. 초기 서버 실행 실패 지점 요약

초기 기존 `.venv` 기준으로 서버 실행이 안 되던 순서는 아래와 같다.

```text
1. .venv Python이 3.9.6
2. 프로젝트 요구사항은 Python >=3.11
3. .venv에 fastapi/uvicorn 없음
4. requirements.txt 전체 설치 시 redis==7.4.0에서 실패
5. 따라서 uvicorn으로 app.main 실행 불가
```

Python 3.11+ 환경에서 최소 패키지만 설치했을 때 추가로 드러난 문제:

```text
1. app.main import
2. watchlist router import
3. filing_ingestion import
4. dart/client.py import
5. bs4 없음
6. ModuleNotFoundError
```

---

## 9. 실제 해결 방법

### 9-1. 적용된 해결안: Python 3.13 가상환경 재구성

실제 해결은 Python 3.13 기반으로 `.venv`를 재구성하고, Python 3.13 및 LangChain/Celery 의존성에 맞게 패키지 핀을 조정하는 방식으로 진행됐다.

적용 결과:

```text
.venv Python: 3.13.11
.python-version: 3.13.0
import app.main: 성공
```

수정된 패키지:

| 패키지 | 전 | 후 | 이유 |
|---|---:|---:|---|
| `tenacity` | `9.0.0` | `8.5.0` | `langchain-core==0.3.10`이 `tenacity 9.x`를 허용하지 않음 |
| `redis` | `7.4.0` | `5.3.1` | `celery==5.4.0`이 `redis<6.0` 요구 |
| `psycopg2-binary` | `2.9.9` | `2.9.12` | Python 3.13 wheel 호환 |
| `asyncpg` | `0.29.0` | `0.31.0` | Python 3.13 wheel 호환 |

### 9-2. 이전 권장안과 달라진 점

초기에는 `.python-version`이 `3.12.10`이었기 때문에 Python 3.12 기준 재구성을 권장했다. 이후 실제 환경은 Python 3.13으로 정리되었고, `.python-version`도 `3.13.0`으로 업데이트됐다.

---

## 10. 서버 실행 명령

의존성이 정상 설치된 뒤 실행 명령은 아래다.

```bash
.venv/bin/uvicorn app.main:app --reload
```

또는:

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

기본 접속:

```text
Swagger UI: http://127.0.0.1:8000/docs
OpenAPI JSON: http://127.0.0.1:8000/openapi.json
Health check: http://127.0.0.1:8000/health
```

---

## 11. 이번 작업에서 삭제한 것

삭제한 것:

```text
.pycache
.venv311
```

설명:

- `.pycache`: 컴파일 검증 중 생긴 임시 Python bytecode cache
- `.venv311`: 서버 실행 테스트를 위해 임시로 만든 Python 3.11 가상환경

삭제하지 않은 것:

```text
.venv
```

기존 가상환경 `.venv`는 사용자 요청에 따라 보존했다.

---

## 12. 충돌 원인 분석 및 `.venv` 재구성 결과

### 12-1. 확인된 충돌 원인

**충돌 1: Python 버전 불일치**

```text
.venv Python 버전:     3.9.6  (초기)
pyproject.toml 요구:   requires-python >=3.11
.python-version 기준:  3.12.10  (초기)
```

Python 버전은 venv 생성 시 고정된다. 안에서 바꿀 수 없으므로, `.venv`를 삭제하고 올바른 Python으로 다시 만드는 것이 유일한 방법이다.

**충돌 2: requirements.txt 내부 패키지 버전 충돌**

```text
langchain-core==0.3.10  →  tenacity!=8.4.0,<9.0.0,>=8.1.0 요구
requirements.txt         →  tenacity==9.0.0 핀
```

`langchain-core==0.3.10`은 `tenacity 9.x`를 허용하지 않는다.

해결:

```text
tenacity==9.0.0  →  tenacity==8.5.0
```

**충돌 3: Celery와 Redis 클라이언트 버전 충돌**

```text
celery==5.4.0  →  redis<6.0 요구
requirements.txt → redis==7.4.0 핀
```

해결:

```text
redis==7.4.0  →  redis==5.3.1
```

**충돌 4: Python 3.13 wheel 호환**

Python 3.13 환경에서 DB 드라이버 wheel 호환을 위해 아래 패키지를 올렸다.

```text
psycopg2-binary==2.9.9  →  psycopg2-binary==2.9.12
asyncpg==0.29.0         →  asyncpg==0.31.0
```

---

### 12-2. `.venv` 재구성 결과

현재 적용된 상태:

```text
.venv Python: 3.13.11
.python-version: 3.13.0
requirements.txt 설치 가능 상태
import app.main: 성공
```

재구성 명령 흐름:

```bash
# 1. 기존 .venv 삭제
rm -rf .venv

# 2. Python 3.13으로 새 venv 생성
/opt/homebrew/bin/python3.13 -m venv .venv

# 3. pip 업그레이드
.venv/bin/pip install --upgrade pip

# 4. requirements.txt 설치 (충돌 수정 후)
.venv/bin/pip install -r requirements.txt
```

`.python-version`도 실제 사용 버전으로 맞췄다.

```text
3.13.0
```

---

### 12-3. requirements.txt 수정 완료 항목

수정 완료된 항목:

| 패키지 | 전 | 후 | 이유 |
|---|---|---|---|
| `tenacity` | `9.0.0` | `8.5.0` | `langchain-core==0.3.10`이 9.x 허용 안 함 |
| `redis` | `7.4.0` | `5.3.1` | `celery==5.4.0`이 `redis<6.0` 요구 |
| `psycopg2-binary` | `2.9.9` | `2.9.12` | Python 3.13 wheel 호환 |
| `asyncpg` | `0.29.0` | `0.31.0` | Python 3.13 wheel 호환 |

---

### 12-4. 최종 검증

검증 명령:

```bash
PYTHONPYCACHEPREFIX=.pycache .venv/bin/python -c "from app.main import app; print('import app.main: ok'); print(len(app.routes))"
```

결과:

```text
import app.main: ok
17
```

`OPENROUTER_API_KEY` 미설정 경고는 출력되지만, 서버 import를 막는 오류는 아니다.
