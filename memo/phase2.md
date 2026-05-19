# TickerTaka — Day 2 작업

## 이전 상태
- ~/TickerTaka-backend (실제 경로 기준)
- Day 1 완료 (디렉터리, docker-compose, requirements)
- .env 채워둠 (클라우드 DB 접속 정보 포함)
- 브랜치: `uc` (main 직접 커밋 회피용 작업 브랜치, 완료 후 main으로 merge)

## 현재 진행 상황
- [x] venv 가상환경 생성
- [x] `requirements.txt` 설치
- [x] 클라우드 DB 구성 + 원격 접속 가능 (`.env`의 `DATABASE_URL` 사용)
- [x] IDE extension으로 클라우드 DB 스키마 확인 가능
- [ ] SQLAlchemy 모델 작성 (이번 세션의 핵심)

## 방향 결정: Alembic은 보류
팀 전체가 클라우드 DB **하나**를 공유하고 있어 마이그레이션 동기화가 필요 없다.
스키마 변경이 필요하면 클라우드 DB에 직접 DDL을 적용하면 모두에게 반영된다.

→ 이번 phase에서는 **SQLAlchemy 모델 작성만 진행**한다.
→ Alembic은 다음 트리거가 발생하면 그때 도입한다 (아래 "Alembic 도입 시점" 참고).

## 사전 정리 체크리스트 (이번 작업 전)
Phase 1 검증에서 발견된 보완사항.

- [ ] `docker-compose.yml`의 redis 볼륨 정책 결정
  - 영속화 필요 → compose에 `redisdata` 볼륨 추가
  - 불필요 → `.gitignore`에서 `redisdata/` 라인 제거
- [ ] `scripts/.gitkeep` 제거 (`scripts/seed.py`가 이미 있어 중복)
- [ ] `pyproject.toml`의 dependencies 정책 결정
  - requirements.txt 단일 관리로 갈지, 양쪽 동기화할지
- [ ] `README.md`의 디렉터리 트리에 `alembic/versions/` 반영
- [ ] phase1/phase2 메모의 경로 표기 통일 (`~/TickerTaka-backend` 기준)
- [ ] `requirements.txt`에 `trafilatura` 추가 (news-cache-plan에서 본문 추출에 사용)

## 이번 세션 작업
클라우드 DB의 실제 스키마를 기준으로 SQLAlchemy 2.0 스타일 모델을 작성한다.

### 작업 순서
1. ~~python venv + requirements 설치~~ (완료)
2. `app/config.py` 검토
   - Day 1에서 이미 정의됨, DB 모델 작업에 추가 필드 있는지만 확인
3. SQLAlchemy 2.0 스타일 모델 작성 (`Mapped[]`, `mapped_column`)
   - 기준: **클라우드 DB의 실제 테이블 정의**
   - IDE extension에서 각 테이블의 CREATE 문 또는 컬럼 정보 확인 후 1:1 매핑
   - COMMENT는 모델 클래스 docstring으로 변환
   - 도메인별 파일 분리: `app/models/base.py`, `user.py`, `ticker.py`, `watchlist.py`, `debate.py`, `cache.py`
   - `app/models/__init__.py`에 `__all__` 정의
4. 모델 정합성 검증
   - 간단한 스크립트 또는 REPL로 각 테이블에 대해 `session.query(Model).first()` 실행
   - 에러 없이 row를 가져오면 모델-DB 매핑 OK
   - 컬럼 누락/타입 불일치는 여기서 잡힘
5. git commit + push (uc 브랜치로)

## 모델 작성 주의사항
- 모든 PK는 uuid
  - 클라우드 DB가 `gen_random_uuid()`로 설정되어 있으면 모델도 `server_default=text("gen_random_uuid()")` 사용
  - Python 측 `default=uuid.uuid4`는 DB default와 충돌 가능 → DB default 우선
- `timestamptz` 사용
- ENUM은 PostgreSQL ENUM 타입 사용 (`sqlalchemy.Enum` + `name` 명시)
  - 모델만 작성하는 phase라 `create_type=False` 설정 (이미 DB에 존재)
- FK는 클라우드 DB에 적용된 DEFERRABLE 옵션과 일치시킬 것
  - `ForeignKey(..., deferrable=True, initially="IMMEDIATE")` 형태
- 클라우드 DB 스키마와 1:1 매핑 (필드 추가/변경 금지, 의문점은 질문)
- 인덱스/UNIQUE 제약은 모델에 표기는 하되, **마이그레이션을 돌리지 않으므로 실제 DB 적용 책임은 클라우드 DB 측에 있음**

## Alembic 도입 시점
다음 중 하나가 발생하면 그때 도입한다. 도입 시 `alembic stamp head`로 현재 DB 상태를 기준선으로 표기한 뒤 변경분부터 마이그레이션으로 관리.

- [ ] 운영(prod) 환경이 dev와 별도로 분리될 때
- [ ] 팀원이 로컬 DB로 작업하기 시작할 때
- [ ] CI/CD 파이프라인에서 fresh DB 띄울 때
- [ ] 스키마 변경 이력 추적이 필요해질 때

도입 시 검토할 항목 (autogenerate 한계):
- 테이블/컬럼 COMMENT
- FK의 DEFERRABLE INITIALLY IMMEDIATE 옵션
- ENUM 타입 생성/삭제 순서 (테이블보다 먼저 생성, 나중 삭제)
- uuid default 표현 방식 (DDL과 동일한지)

## 다음 단계: news-cache 적재 구현
모델 작성이 끝나면 `memo/plans/news-cache-ingestion-plan.md`에 따라 news_cache 적재 도메인 로직 구현으로 넘어간다.

선행 산출물 (이번 phase에서 만들어져야 함):
- `app/models/cache.py`의 `NewsCache` 모델
- `app/models/ticker.py`의 `TickerMetadata` 모델
- `app/models/watchlist.py`의 `Watchlist` 모델

## 마무리 (5번 단계 이후)
- [ ] uc 브랜치 푸시
- [ ] main으로 PR 생성 또는 merge 시점 결정 (선택)
- [ ] news-cache-plan 구현 phase로 전환