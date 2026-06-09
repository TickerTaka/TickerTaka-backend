# #4 Dockerise 1차 구현 보고서 (2026-06-09)

## 1. 목적

평가 항목 5(Dockerise) 대응을 위해, 현재 프로젝트의 **실제 실행 환경**에 맞는 Docker 실행 경로를 정리한다.

이번 트랙의 목표는 "모든 인프라를 컨테이너로 옮긴다"가 아니라 다음을 만족하는 것이다.

- FastAPI app을 컨테이너로 재현 가능하게 실행
- Redis / ChromaDB를 로컬 Docker로 함께 기동
- PostgreSQL은 기존처럼 **NCP 원격 DB**를 그대로 사용
- `docker compose up --build app` 기준으로 `/health` 및 최소 API smoke가 가능

## 2. 현재 환경 전제

현재 팀 환경은 아래와 같다.

| 컴포넌트 | 실제 위치 | 비고 |
|---|---|---|
| PostgreSQL | NCP 원격 | 단일 SOT, 로컬 compose postgres를 기본 사용하지 않음 |
| Redis | 로컬 Docker | lock / checkpoint / quote cache |
| ChromaDB | 로컬 Docker | evidence vector index |
| FastAPI app | 개발자 로컬 또는 Docker | 이번 트랙에서 Docker 실행 경로 추가 |

중요:
- `.env`의 `REDIS_URL=redis://localhost:6379/0`, `CHROMA_URL=http://localhost:8080`은 **호스트에서 직접 백엔드를 띄울 때** 맞는 값이다.
- 반대로 app이 **컨테이너 안에서 실행될 때**는 `localhost`가 컨테이너 자신을 가리키므로, compose 내부에서는
  - `REDIS_URL=redis://redis:6379/0`
  - `CHROMA_URL=http://chroma:8000`
  로 override되어야 한다.

## 3. 구현 범위

### 3-1. `Dockerfile` 추가

신규 파일:
- `Dockerfile`

역할:
- `python:3.12-slim` 기반 app 이미지 빌드
- `requirements.txt` 설치
- `app/` 복사
- `alembic/`, `alembic.ini`도 포함해 컨테이너 안에서 migration 실행 가능하게 준비
- 기본 실행 명령:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3-2. compose에 `app` 서비스 추가

수정 파일:
- `docker-compose.yml`

추가 내용:
- `app` 서비스
- `depends_on`:
  - `redis`
  - `chroma`
- app healthcheck:
  - `GET /health`
- HuggingFace 캐시 볼륨:
  - `hfcache:/root/.cache/huggingface`
- `HF_HOME=/root/.cache/huggingface`

환경 주입 정책:
- `.env`는 그대로 읽되,
- compose 안에서만 아래 값을 override:

```yaml
REDIS_URL: redis://redis:6379/0
CHROMA_URL: http://chroma:8000
```

즉:
- **호스트 실행용 `.env`는 유지**
- **컨테이너 실행용 주소는 compose에서만 덮어씀**

### 3-3. Chroma healthcheck 추가

`docker-compose.yml`의 `chroma` 서비스에 heartbeat healthcheck를 추가했다.

```text
http://localhost:8000/api/v1/heartbeat
```

이를 통해 `app`이 evidence retrieval 대상 vector store 준비 전 먼저 뜨는 상황을 줄인다.

### 3-4. 선택 postgres를 profile로 분리

`docker-compose.yml`의 `postgres` 서비스는 기본 검증 경로에서 사용하지 않으므로:

```yaml
profiles: ["local-db"]
```

를 부여했다.

의미:
- `docker compose up` 기본 경로에서는 postgres가 뜨지 않음
- 로컬 대체 DB가 필요할 때만:

```bash
docker compose --profile local-db up postgres
```

처럼 명시적으로 올린다.

### 3-5. HF 캐시 영속화

`sentence-transformers` / HuggingFace 모델은 첫 호출 시 다운로드되므로, app 컨테이너에 아래를 추가했다.

```yaml
environment:
  HF_HOME: /root/.cache/huggingface
volumes:
  - hfcache:/root/.cache/huggingface
```

의도:
- 컨테이너 재생성마다 `jhgan/ko-sroberta-multitask`를 다시 받지 않도록 방지
- 첫 검색/인덱싱 이후 재기동 비용 절감

### 3-6. `.dockerignore` 추가

신규 파일:
- `.dockerignore`

제외 대상:
- `.git`
- `venv`, `.venv`
- `.notion-mcp`
- `memo/`
- `scripts/`
- 캐시/로그 파일

의도:
- 빌드 컨텍스트 축소
- 로컬 MCP 설치물 / 문서 / 개발 보조 파일이 이미지에 불필요하게 포함되지 않도록 차단

### 3-7. `.env.example` 보강

수정 파일:
- `.env.example`

보강 내용:
- `REDIS_URL`, `CHROMA_URL`에 대해
  - **host-run backend**
  - **Docker Compose app container**
  환경 차이를 주석으로 명시
- `DATABASE_URL`은 compose에서도 **NCP/원격 값을 그대로 사용**하고, 로컬 postgres는 `local-db` profile일 때만 선택 사용함을 명시
- HuggingFace cache는 compose가 내부에서 `HF_HOME`과 named volume으로 처리함을 명시

예:

```env
# Host-run backend + local Docker: redis://localhost:6379/0
# Docker Compose app container:  redis://redis:6379/0
REDIS_URL=redis://localhost:6379/0
```

## 4. MCP와의 경계

이번 Docker 트랙에서는 **Notion MCP publish를 컨테이너 inside runtime에 포함하지 않았다.**

이유:
- 현재 MCP publish는 호스트 로컬에 설치한 `.notion-mcp/...` 바이너리 경로에 의존
- app 컨테이너 안에는 그 경로가 존재하지 않음
- Docker 트랙의 핵심은 평가 항목 5 대응용 **app 기동 재현성** 확보이지, MCP까지 컨테이너 내부로 옮기는 것이 아님

그래서 compose의 `app` 서비스에서는 아래 env를 명시적으로 비워 두었다.

```yaml
NOTION_MCP_SERVER_COMMAND: ""
NOTION_MCP_SERVER_ARGS: ""
```

정책:
- host-run backend에서는 기존 MCP publish 유지
- Docker app에서는 `/health`, watchlist, debate API 기본 경로를 우선 검증

## 5. 검증 결과

### 5-1. 정적 검증

실행:

```bash
python3 -m py_compile \
  app/main.py \
  app/core/db.py \
  app/core/database.py \
  app/core/redis.py \
  app/api/debate.py \
  app/integrations/notion_mcp.py
```

결과:
- 컴파일 통과

### 5-2. 런타임 검증 상태

실제 런타임 검증 결과:

1. `docker compose up -d redis chroma`
2. `docker compose up --build -d app`
3. `docker compose ps`
4. `curl http://127.0.0.1:8000/health`

확인 결과:
- `tickertaka-app` 컨테이너 생성 및 기동
- `tickertaka-app`, `tickertaka-chroma`, `tickertaka-redis` 모두 `healthy`
- `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`

참고:
- 최초 1회 `curl`에서 `Recv failure: Connection reset by peer`가 있었으나, 이는 app 컨테이너가 막 기동하며 소켓 전환 중 발생한 일시적 현상으로 판단된다.
- 최종적으로 동일 엔드포인트 재호출에서 정상 `200`/응답 바디를 확인했으므로 차단 결함으로 보지 않는다.

즉 현재 상태는:
- **구현 완료**
- **실기동 E2E(`/health`) 확인 완료**
- 남은 것은 선택적 API smoke 확장 검증

## 6. 남은 체크포인트

다음 순서로 보면 된다.

1. 필요 시 watchlist API smoke
2. 필요 시 debate API smoke
3. 필요 시 `/api/dashboard/stats` 확인

검증 시 주의:
- `DATABASE_URL`은 여전히 NCP 원격 DB를 가리켜야 함
- compose의 `postgres` 서비스는 기본 검증 경로에서 사용하지 않으며, 필요할 때만 `--profile local-db`로 올림

## 7. 판정

이번 1차 구현으로 Docker 트랙은 다음을 충족한다.

- app 컨테이너 실행 경로 추가
- Redis / Chroma 의존성 compose 연결
- host-run env와 container env 차이 문서화
- NCP Postgres 유지 정책 반영

따라서 **#4 Dockerise는 `/health` 실기동 검증까지 완료된 상태**로 볼 수 있다.  
다음 단계는 선택적 watchlist / debate API smoke와, 우선순위상 다음 트랙(**#5 스트리밍 & 비동기 처리**) 착수다.

---

## 8. 검증 (Claude, 2026-06-09)

> 실기동 전, compose/Dockerfile/코드/이미지를 기준으로 정합성·차단 결함을 점검한 기록. 이어붙이는 형식.

### D-0. 검증 방법
- `Dockerfile` / `docker-compose.yml` / `.dockerignore` / `.env.example` / 보고서 정독.
- 코드 확인: `/health` 엔드포인트(`app/main.py:30`) 존재 ✓, chroma 클라이언트(`chroma_client.py` `HttpClient.heartbeat`) ✓.
- 버전 정합: requirements `chromadb==0.5.23` = compose 이미지 `chromadb/chroma:0.5.23` (client=server) ✓, `sentence-transformers==3.2.1` 존재.
- **이미지 실측**: `chromadb/chroma:0.5.23`에 `curl` 포함 여부를 컨테이너로 직접 확인 → **`HAS_CURL`**(python3도 존재).

### D-✓ 정상 확인 (green)
- **chroma 헬스체크 동작**: 이미지에 curl이 실재하므로 `["CMD","curl",...,"/api/v1/heartbeat"]`가 통과 → chroma가 `service_healthy`가 되어 `app.depends_on`이 풀린다. (이게 막히면 app이 영영 안 떴을 가장 큰 리스크였는데, 실측으로 해소.)
- **app 헬스체크 동작**: `/health` 존재 + Dockerfile이 `curl` 설치 → app 헬스체크도 유효.
- **env override 우선순위**: compose `environment:`(REDIS/CHROMA/NOTION_*)는 `env_file: .env`보다 **우선** → 컨테이너에서 `redis://redis:6379/0`, `http://chroma:8000`, 그리고 `NOTION_MCP_SERVER_COMMAND=""`가 정확히 덮어쓴다.
- **MCP 비활성 안전**: 컨테이너에서 `NOTION_MCP_SERVER_COMMAND=""` → `publish_debate`의 가드(`"NOTION_MCP_SERVER_COMMAND is not configured"`)가 **graceful 502**를 내고 크래시 아님. 토론 본체 경로는 무관.
- **DB 경계 정확**: `app.depends_on`은 redis/chroma만 — 로컬 `postgres`엔 의존하지 않음(NCP 원격 사용과 정합). NCP는 공인 IP라 컨테이너 egress로 도달 가능.
- **시크릿 안전**: Dockerfile은 `app/alembic/alembic.ini`만 COPY — `.env`는 이미지에 안 들어가고 런타임에 `env_file`로 주입 → 토큰이 이미지에 박히지 않음.

### D-1. [중·운영] 포트 8000 충돌 — 기동 전 호스트 백엔드 종료
호스트에서 직접 띄운 uvicorn(지금까지 MCP 테스트하던 그것)이 `:8000`을 잡고 있으면, app 컨테이너의 `8000:8000` 바인드가 충돌해 **컨테이너가 안 뜬다**. → `docker compose up --build app` 전에 **호스트 uvicorn을 먼저 종료**할 것(또는 app 포트를 `8001:8000`으로).

### D-2. [중→해소] 이미지 무게 + HF 모델 캐시 미영속
`sentence-transformers==3.2.1`(+torch)와 `build-essential`로 **이미지가 크고 첫 빌드가 느리다**는 점은 여전히 사실이다. 다만 재생성마다 임베딩 모델을 다시 받는 문제는 이번 라운드에서:

```yaml
environment:
  HF_HOME: /root/.cache/huggingface
volumes:
  - hfcache:/root/.cache/huggingface
```

를 compose `app`에 반영해 해소했다. → **캐시 영속화 완료.**

### D-3. [낮] app `start_period: 20s` 적정성
임베딩 모델 로딩/다운로드가 기동 시 일어나면 20s가 짧을 수 있다. 다만 `retries:5 × interval:10s`로 약 50s 창이 있어 대체로 흡수된다. 느린 네트워크에서 첫 빌드/기동 시 헬스가 늦게 green이 될 수 있음을 인지만.

### D-4. [낮] 마이그레이션 비자동 (의도된 경계)
Dockerfile이 `alembic`을 COPY하지만 `CMD`는 uvicorn만 실행 → **컨테이너 기동 시 `alembic upgrade head`를 돌리지 않는다**. 현재는 NCP DB를 **호스트가 이미 마이그레이션**했으므로 무방하나, "컨테이너가 스키마를 만들지 않는다"는 전제를 명시할 것. (원하면 entrypoint에 `alembic upgrade head` 옵션 추가 가능.)

### D-5. [낮] `.env` 존재 전제
`env_file: .env`라 **`.env`가 없으면 `docker compose up`이 에러**난다(저장소엔 `.env.example`만 커밋). 팀원/CI는 `.env`를 먼저 생성해야 함 — README/보고서에 한 줄 명시 권장.

### D-6. [낮→해소] `postgres` 서비스 기본 기동
기본 `docker compose up`에서 미사용 `postgres`까지 뜨는 문제는 `profiles: ["local-db"]`를 postgres 서비스에 부여해 해소했다. → **기본 경로에서는 postgres 비기동, 필요 시에만 명시 활성화.**

### D-7. [긍정] 호스트 OpenSSL import 이슈와 무관
앞서 호스트 WSL venv에서 `import app.api.debate`가 `OPENSSL_3.3.0` 링크 오류로 실패했는데, 이는 **호스트 환경 한정**이다. 깨끗한 `python:3.12-slim` 컨테이너에선 재현되지 않을 가능성이 높아, **Docker 경로가 오히려 import 안정성을 높이는** 부수효과가 기대된다(실기동에서 확인 포인트).

### D-8. 종합 판정
- **실기동을 막는 구조적 결함은 없다.** 최대 리스크였던 chroma 헬스체크의 curl 의존은 **실측으로 해소**(이미지에 curl 존재). env override·MCP 비활성·DB 경계·시크릿 처리 모두 정합.
- **실기동 전 1건만 처리**: D-1(호스트 :8000 종료).
- 나머지(D-3/4/5)는 인지/문서화 항목. → **`docker compose up --build app` 런타임 검증 green-light.**

## 9. 실기동 완료 기록 (2026-06-09)

실제 호스트에서 다음을 확인했다.

```bash
docker compose ps
curl http://127.0.0.1:8000/health
```

결과:
- `tickertaka-app` 상태: `Up ... (healthy)`
- `tickertaka-chroma` 상태: `Up ... (healthy)`
- `tickertaka-redis` 상태: `Up ... (healthy)`
- `/health` 응답: `{"status":"ok"}`

판정:
- D-1(호스트 8000 포트 점유) 해소 후 실기동 성공
- Docker 트랙의 핵심 닫힘 기준인 **app 컨테이너 기동 + `/health` 200** 충족

## 10. 보완 반영 재검증 (Claude, 2026-06-09)

> D-2(HF 캐시)·D-6(postgres profile) 보완분과 실기동 결과를 `docker-compose.yml` 실파일로 재확인.

- **D-2 해소 확인** ✓ — `app.environment.HF_HOME=/root/.cache/huggingface`(L76) + `app.volumes: hfcache:/root/.cache/huggingface`(L80) + 최상위 `volumes: hfcache`(L96) **3곳 모두 정합**(volume 선언 누락 없음). 컨테이너 재생성 시 `jhgan/ko-sroberta-multitask` 재다운로드 방지.
- **D-6 해소 확인** ✓ — `postgres.profiles: ["local-db"]`(L10)로 기본 `up`에서 제외. redis/chroma는 profile 미지정이라 `up -d redis chroma`·기본 `up` 모두 정상 기동(앱 의존성 깨지지 않음).
- **chroma 헬스체크** ✓ — 이미지 curl 실측 + 실기동에서 chroma `healthy` 도달로 이중 확인.
- **무회귀** — env override(REDIS/CHROMA/NOTION_*) 우선순위, `.env` 미빌드(런타임 주입), DB 경계(app↛local postgres) 변동 없음.
- **실기동 결과 정합** — `docker compose ps`상 app/chroma/redis 전부 `healthy`, `/health`=`{"status":"ok"}`. 최초 1회 `Connection reset`은 기동 전환 중 소켓 레이스로 양성(benign), 재호출 200으로 확인.

**판정: #4 Dockerise = `/health` 실기동 기준 닫힘. 보완분까지 정합, 회귀 없음.** 잔여 항목(D-3 start_period 인지 / D-4 마이그레이션 비자동 / D-5 `.env` 전제)은 저위험 문서화 항목으로, 트랙 종료에 지장 없음.
