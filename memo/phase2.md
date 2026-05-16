# TickerTaka — Day 2 작업

## 이전 상태
- ~/projects/TickerTaka-backend
- Day 1 완료 (디렉터리, docker-compose, requirements)
- .env 채워둠, Docker 컨테이너 healthy

## 이번 세션 작업
첨부한 DDL SQL을 기반으로 SQLAlchemy 모델 + Alembic 마이그레이션을 작성한다.

[여기에 위 SQL 통째로 첨부]

### 작업 순서
1. python venv + requirements 설치
2. app/config.py — Pydantic Settings (이미 정의된 .env 변수들)
3. SQLAlchemy 2.0 스타일 모델 작성 (Mapped[], mapped_column)
   - 첨부 SQL의 CREATE TABLE/CREATE TYPE/COMMENT를 정확히 매핑
   - COMMENT는 모델 클래스 docstring으로 변환
   - app/models/base.py, user.py, ticker.py, watchlist.py, debate.py, cache.py로 분리
   - app/models/__init__.py에 __all__ 정의
4. Alembic 초기화 + env.py에서 .env 로드 + Base.metadata 연결
5. alembic revision --autogenerate -m "initial schema: 13 tables"
   - 생성된 파일 검토 후 보여주기
6. alembic upgrade head
7. 검증: \dt로 13개 테이블 확인, downgrade/upgrade 양방향 테스트
8. git commit + push

## 주의사항
- 모든 PK는 uuid (default=uuid.uuid4)
- timestamptz 사용
- ENUM은 PostgreSQL ENUM 타입 사용 (sqlalchemy.Enum + name 명시)
- FK는 DEFERRABLE INITIALLY IMMEDIATE 옵션 적용
- 첨부 SQL의 인덱스/UNIQUE 제약 모두 포함
- 첨부 SQL과 1:1 매핑되도록 (필드 추가/변경 금지, 의문점은 질문)