# TickerTaka — Day 2 작업

## 이전 상태
- ~/TickerTaka-backend (실제 경로 기준)
- Day 1 완료 (디렉터리, docker-compose, requirements)
- .env 채워둠, Docker 컨테이너 healthy
- 브랜치: `uc` (main 직접 커밋 회피용 작업 브랜치, 완료 후 main으로 merge)

## 사전 정리 체크리스트 (Day 2 시작 전)
Phase 1 검증에서 발견된 보완사항. 작업 시작 전에 한 번에 정리한다.

- [ ] 아래 "첨부 SQL" 자리표시자(`[여기에 위 SQL 통째로 첨부]`)에 실제 DDL 채워넣기
- [ ] `docker-compose.yml`의 redis 볼륨 정책 결정
  - 영속화 필요 → compose에 `redisdata` 볼륨 추가
  - 불필요 → `.gitignore`에서 `redisdata/` 라인 제거
- [ ] `scripts/.gitkeep` 제거 (`scripts/seed.py`가 이미 있어 중복)
- [ ] `pyproject.toml`의 dependencies 정책 결정
  - requirements.txt 단일 관리로 갈지, 양쪽 동기화할지
- [ ] `README.md`의 디렉터리 트리에 `alembic/versions/` 반영
- [ ] phase1/phase2 메모의 경로 표기 통일 (`~/TickerTaka-backend` 기준)

## 이번 세션 작업
첨부한 DDL SQL을 기반으로 SQLAlchemy 모델 + Alembic 마이그레이션을 작성한다.

[여기에 위 SQL 통째로 첨부]

### 작업 순서
1. python venv + requirements 설치
2. app/config.py 검토 (Day 1에서 이미 정의됨)
   - DB 모델 작업에 추가로 필요한 필드 있는지만 확인
3. SQLAlchemy 2.0 스타일 모델 작성 (Mapped[], mapped_column)
   - 첨부 SQL의 CREATE TABLE/CREATE TYPE/COMMENT를 정확히 매핑
   - COMMENT는 모델 클래스 docstring으로 변환
   - app/models/base.py, user.py, ticker.py, watchlist.py, debate.py, cache.py로 분리
   - app/models/__init__.py에 __all__ 정의
4. Alembic 초기화 + env.py에서 .env 로드 + Base.metadata 연결
5. alembic revision --autogenerate -m "initial schema: 13 tables"
   - 생성된 파일 검토 후 보여주기
   - autogenerate 한계 항목 수동 보정 (아래 주의사항 참고)
6. alembic upgrade head
7. 검증: \dt로 13개 테이블 확인, downgrade/upgrade 양방향 테스트
8. git commit + push (uc 브랜치로)

## 주의사항
- 모든 PK는 uuid (default=uuid.uuid4)
  - Python 측 `default=uuid.uuid4` vs DB 측 `server_default=text("gen_random_uuid()")` 중 DDL과 일치시킬 것
- timestamptz 사용
- ENUM은 PostgreSQL ENUM 타입 사용 (sqlalchemy.Enum + name 명시)
  - `create_type=True`까지 명시 (autogenerate가 ENUM을 깔끔하게 잡도록)
- FK는 DEFERRABLE INITIALLY IMMEDIATE 옵션 적용
  - `ForeignKey(..., deferrable=True, initially="IMMEDIATE")` 형태로 명시
- 첨부 SQL의 인덱스/UNIQUE 제약 모두 포함
- 첨부 SQL과 1:1 매핑되도록 (필드 추가/변경 금지, 의문점은 질문)

## Alembic autogenerate 한계 (5번 단계 후 수동 검토)
autogenerate가 잘 못 잡는 항목 — 생성된 마이그레이션 파일에서 직접 보강한다.
- [ ] 테이블/컬럼 COMMENT
- [ ] FK의 DEFERRABLE INITIALLY IMMEDIATE 옵션
- [ ] ENUM 타입 생성/삭제 순서 (테이블보다 먼저 생성, 나중 삭제)
- [ ] uuid default 표현 방식 (DDL과 동일한지)

## 마무리 (8번 단계 이후)
- [ ] uc 브랜치 푸시
- [ ] main으로 PR 생성 또는 merge 시점 결정
- [ ] phase3 메모 초안 작성