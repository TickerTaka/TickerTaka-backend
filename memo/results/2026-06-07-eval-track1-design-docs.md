# 평가 대응 1차 완료 보고 — 5대 설계문서 (2026-06-07)

> 평가 대응 후속 계획(`memo/process/2026-06-06-eval-followup-plan.md`)의 **우선순위 #1(5대 설계문서)** 트랙 완료 기록.
> 상세 검증 트레일은 plan 문서(A~DD 절)에 있으며, 본 보고서는 마일스톤 요약 + 포인터다.

## 1. 맥락

- 평가 리포트(`memo/eval/BDAI_Pocat_Team2-fc3f2b7.md`): **25/70 (F)**.
- 항목4(5대 설계문서)는 **5종 전부 부재 → "누락 상한 캡 1"** 적용 상태였다.
- 코드 정합성(ORM↔Alembic, API↔라우터)은 이미 상위였으나 **설계문서 "형식"이 0**이라, 도식화만 하면 캡 해제가 가능한 가성비 최상위 트랙으로 판단 → #1 우선 착수.

## 2. 산출물 — `docs/design/` 5종

| 문서 | 파일 | 기준 코드 |
|---|---|---|
| 유스케이스 명세서 | `use-case-specification.md` | `app/api/*`, `app/domain/*`, `app/agents/*` |
| 컴포넌트 설계서 | `component-design.md` | 6계층 + 배포 토폴로지(NCP PG / 로컬 Redis·Chroma) |
| 인터페이스 정의서 | `interface-definition.md` | `app/schemas/*.py` 요청/응답 1:1 |
| 시퀀스 다이어그램 | `sequence-diagram.md` | `debate_graph.py`, `debate_service.py`, `watchlist.py` |
| ERD | `erd.md` | `app/models/*.py` (15테이블 + enum 9종) |

→ **5종 전부 존재 + 코드 1:1 정합** → 항목4 캡 해제 요건 충족(형식·내용 양면).

## 3. 검증으로 잡은 코드 불일치 4건 (초안 → 보정)

초안을 실제 코드와 대조해 다음을 수정했다(상세: plan CC 절).

| # | 불일치 | 근거 | 보정 |
|---|---|---|---|
| 1 | `debate_note` 테이블 누락(14→15종) | `models/debate.py:206`, `(user_id,session_id)` unique | ERD·컴포넌트에 추가 |
| 2 | 토론 LLM을 "OpenRouter"로 오기 | `llm_factory.py:30–41` settings 기반·OpenAI 직접(기본 `gpt-4o-mini`) | 4개 문서에서 정정 |
| 3 | 시퀀스에 `moderator_pre`+`moderator_check` 검증 루프 누락 | `debate_graph.py:60–66`, `_router` | 4턴 구조·router 5단 분기 반영 |
| 4 | `GET /api/market/indexes` 누락 | `market_data.py:265` | 인터페이스에 추가 |

## 4. ERD·시퀀스·인터페이스 정밀화

- **ERD**: 속성 포함 mermaid(PK/FK/주요 컬럼), Enum 카탈로그 9종, unique 제약 10종 = [[infra-stage-policy]]의 "PG가 최종 중복 방어선" 구현 근거, `cache_key` 부분 unique + `cached_from_session_id` self-ref(토론 결과 캐시).
- **시퀀스**: RuntimeGuard 단일비행 락(409)·fail-open, checkpoint load/save(24h fail-soft), astream→`merge_state` 루프, `_router` 분기 우선순위, 실패 시 `update_session_status(failed)`.
- **인터페이스**: 모든 Pydantic 모델 필드·타입·제약(min/max·기본값·nullable) + 쿼리 파라미터 기본/상한까지 1:1.

## 5. RAGAS 재평가 반영

- `main` 병합(`97a55fa`)으로 RAGAS **1차 구현 병합** 확인(`debate_evaluation.py`: faithfulness + context_precision, 백그라운드 비차단).
- 단 **결과 영속화·배치·리포트·golden-set 미완** → 설계 문서의 RAGAS 표기를 전부 **"현재값·변경 가능(1차 구현)"**으로 정합화(모델 `gpt-oss-120b:free`·메트릭이 바뀌어도 문서가 거짓이 되지 않도록).
- 평가서의 "`ragas` 0건·정량평가 전무"는 현재 코드 기준 더 이상 사실 아님(부분 해소). 게이트 완전 해제는 증적 체인 완결 시점.

## 6. 닫힘 상태 / 다음

- **#1 (5대 설계문서): 종료.** 외부 검토 결과 "막는 수준 문제 없음, 캡 해제 판단 타당".
- 잔여 트랙(plan 고정순서): **#2 에러핸들링 → #3 MCP → #4 Docker → #5 스트리밍 → #6 RAG**.
- 별도 트랙(타 팀): RAGAS 증적 완결, sLLM+Langfuse(항목3), vLLM/Ollama(항목7).
- 관련 커밋: `f812fe6`(설계문서 + RAGAS 표기 정합), `676149d`(plan RAGAS 반영).

## 참고

- 작업 계획·검증 트레일: `memo/process/2026-06-06-eval-followup-plan.md`
- 평가 기준/리포트: `memo/eval/evaluation_criteria.md`, `memo/eval/BDAI_Pocat_Team2-fc3f2b7.md`
