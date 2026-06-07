# 평가 대응 2차 완료 보고 — 에러핸들링 보강 (2026-06-07)

> 평가 대응 후속 계획(`memo/process/2026-06-06-eval-followup-plan.md`)의 **우선순위 #2(에러핸들링 보강)** 완료 기록.
> 목표는 “retry 추가”가 아니라, **moderator SPOF 제거 + 보조 기능 실패 비전파**였다.

## 1. 맥락

- 평가 계획서의 항목2 핵심 지적은 `moderator_node._call()` 예외가 그래프 전체를 죽이는 **SPOF**라는 점이었다.
- bull/bear는 이미 `try/except`로 graceful 처리하고 있었지만, moderator는
  - 의제 설계
  - 발언 검증
  - 최종 요약
  중 특히 `pre`/`summary`에서 LLM 실패 시 전체 토론이 실패할 수 있었다.

## 2. 이번에 보강한 코드

대상 파일:
- `app/agents/nodes/moderator_node.py`

핵심 변경:

1. **`moderator_pre` fail-soft**
- 의제 설계 LLM 호출 실패 시 `["쟁점1", "쟁점2", "쟁점3"]` 기본 의제로 진행
- agenda 파싱 결과가 비정상이면 `_coerce_agenda()`로 기본 3개 의제로 보정

2. **`moderator_summary` fail-soft**
- 최종 요약 LLM 호출 실패 시 `_build_summary_fallback()`으로
  - fallback summary
  - fallback key_points
  생성 후 저장 경로를 계속 진행
- fallback summary가 사용된 경우, **의미 없는 RAGAS summary 평가는 건너뛰고** evidence 평가만 유지

3. **근거 저장 실패 비전파**
- `save_evidence()` 실패는 warning만 남기고
  - statement 저장
  - summary 저장
  - session 완료
  는 그대로 진행

4. **RAGAS 백그라운드 태스크 안전화**
- `_schedule_background_task()`를 추가해 `asyncio.create_task()` 등록 후 done callback에서 예외 로깅
- RAGAS summary/evidence 평가 실패는 본체 토론 완료 경로에 전파되지 않음

## 3. 현재 닫힌 범위

| 항목 | 상태 | 설명 |
|---|---|---|
| `moderator_pre` LLM 실패 | ✅ | 기본 의제로 계속 진행 |
| `moderator_summary` LLM 실패 | ✅ | fallback summary 저장 후 완료 처리 |
| `moderator_check` LLM 실패 | ✅ | 기존처럼 `ok` 처리로 계속 진행 |
| evidence 저장 실패 | ✅ | statement/summary/session 완료 경로 유지 |
| RAGAS 등록/실행 실패 | ✅ | 본체 토론 성공 경로 비전파 |

## 4. 검증

- `python -m py_compile app/agents/nodes/moderator_node.py` 통과
- 코드상 확인 포인트:
  - `moderator_pre` 경고 로그 + 기본 agenda
  - `moderator_summary` 경고 로그 + fallback summary
  - `_schedule_background_task` 등록 및 done callback 예외 로깅
  - `save_evidence()` per-item warning 처리
  - fallback summary 사용 시 summary RAGAS 평가 skip

## 5. 남은 것 / 범위 밖

- **MCP publish fail-soft**는 아직 MCP 트랙 미착수라 이번 범위 밖
- `debate_service` 자체를 더 세분화한 복구/상태전이 튜닝은 가능하지만, 이번 #2 목표였던 moderator SPOF 완화에는 필수 아님
- `save_statement` / `save_moderator_summary` / `update_session_status` 같은 **핵심 DB 저장 실패는 여전히 본체 실패**로 본다. 이 경계는 의도된 스코프이며, "fallback summary 저장"은 **DB가 정상일 때**만 성립한다.

## 6. 결론

- **#2 에러핸들링 보강은 사실상 종료**로 봐도 된다.
- 이번 보강으로 토론 본체는
  - moderator LLM 순간 실패
  - evidence 일부 저장 실패
  - RAGAS 사후평가 실패
  에도 **완료 결과를 남길 가능성**이 커졌다.
- 다음 우선순위는 계획서 고정순서대로 **#3 MCP 도입**이다.

---

# 검증 (코드 대조 · 호환성 점검, 2026-06-07)

`git diff`와 소스 직접 대조로 "무엇이 바뀌었나 / 기존·팀원 작업과 호환되나 / 누락은 없나"를 점검했다.

## A. 변경 범위 — 무엇이 바뀌었나

- **코드 변경은 `app/agents/nodes/moderator_node.py` 단 1개 파일**(+128/−34). 그 외 코드 파일 변경 0건(`git diff --name-only` = moderator_node.py + 문서뿐).
- 추가된 것: 헬퍼 4개(`_default_agenda`, `_build_summary_fallback`, `_coerce_agenda`, `_schedule_background_task`) + `moderator_pre`·`moderator_summary`의 `_call` try/except + evidence 저장 per-item try/except + RAGAS 태스크를 `_schedule_background_task`로 교체(done-callback 예외 로깅).
- **import 추가 불필요** — `json`(L3)·`asyncio`(L5) 이미 존재. 신규 의존성 0.
- 컴파일 확인: `python`(Python 3.12) `py_compile`(doraise) **통과**.
  - ※ 보고서 4절의 `python3 -m compileall`은 이 Windows 셸에선 `python3`가 Store 스텁(exit 49)이라 무의미 — 실제 검증은 `python`으로 수행했고 통과.

## B. 기존 코드 / 팀원 작업 호환성 — 핵심 질문

**결론: 순수 additive이며, pull 후 기존 작업 변동 없이 호환된다.**

| 점검 | 결과 |
|---|---|
| 팀원 RAGAS 코드(`app/domain/debate_evaluation.py`) | **0 변경** — 호출부 래퍼만 `create_task`→`_schedule_background_task`로 교체, `_run_summary_eval`/`_run_evidence_eval` 동작 불변 |
| 함수 시그니처 | `moderator_pre_node(state)` / `moderator_summary_node(state)` **불변** |
| 반환 dict 키 | `agenda`/`moderator_flag`/`statements`/`summary_content`/`key_points`… **불변** (state 계약 유지) |
| 다른 노드·그래프·state·DB 스키마·prompts·repo | **무변경** |
| 제거된 import / 삭제된 함수 | 없음 |

→ **팀원이 구현한 RAGAS·다른 노드를 건드리지 않고, moderator 에러핸들링만 추가한 게 맞다.** 코드 호환 OK.

## C. SPOF 완전성 — 좋은 소식

평가서가 지적한 moderator `_call` SPOF의 **진입점 3곳이 전부 가드됨**:
- `moderator_pre` → 이번에 추가 ✅
- `moderator_summary` → 이번에 추가 ✅
- `moderator_check` → **기존 HEAD부터 try/except 존재**(L126–136, `verdict=ok`로 graceful) ✅ — 매 턴 도는 검증 노드까지 이미 보호됨.

즉 항목2의 SPOF 지적은 이번 작업으로 **완전 해소**.

## D. 단, 정직하게 짚을 2가지 (주의)

**D-1. [git 레벨] 공유 핫파일 머지 충돌 리스크.**
이번 수정은 **RAGAS 커밋이 최근 추가한 동일 구역**(`moderator_summary_node`의 RAGAS 스케줄링 L258–280)을 함께 건드렸다. `moderator_node.py`는 RAGAS 담당과 **공유 핫파일**이라, 팀원이 같은 노드를 또 수정해 들어오면 **텍스트 머지 충돌** 가능성이 있다. 기능 비호환은 아니지만 머지 시점 조율 필요(현재까지는 충돌 없음).

**D-2. [범위] 순수 "에러핸들링만"은 아닌 1곳.**
`_coerce_agenda`는 **성공 경로도 정규화**한다. 기존엔 LLM이 빈 agenda를 줘도 `[]` 그대로였는데, 이제 빈/비정상이면 `["쟁점1","쟁점2","쟁점3"]`로 강제. 방어적 개선이라 리스크는 낮지만, "에러핸들링만 추가"라기보단 **성공 시 동작도 미세 변화**가 있다(빈 agenda → 기본 의제). 의도된 변화면 OK.

## E. 계획 대비 누락 / 보완 권고

1. **[중] 핵심 DB 저장 경로는 미보호.** evidence 저장은 wrap했으나 `save_statement`(L244)·`save_moderator_summary`(L249)·`update_session_status`(L250)는 예외 전파 그대로다. 즉 **LLM 실패엔 graceful이지만 DB 저장 실패 시엔 여전히 `run_session` 실패**(→ `debate_service`가 'failed' 처리). 합리적 스코프(요약 자체를 저장 못 하면 진짜 실패)지만, **"fallback summary 저장"은 DB가 살아있을 때만 성립**함을 명시할 것.
2. **[소] 계획의 "bull/bear/moderator 공통 LLM 호출 래퍼 정리"(plan 본문)는 미실행** — 대신 호출부별 try/except. K-5 권고(얇은 try/except > tenacity 중첩)와 부합해 문제는 아니나, 계획 항목 기준 deviation으로 기록.
3. **[미세] fallback 후에도 RAGAS 평가 스케줄.** summary LLM 실패 시 fallback summary로 `_run_summary_eval` 실행 → 캔드 요약의 faithfulness를 채점(무해, 로그만). 의미는 약하므로 필요 시 fallback일 땐 eval skip 고려.
4. **[미세] `_build_summary_fallback`의 `if not key_points and non_moderator` 가지는 사실상 dead.** agenda가 coerce로 항상 ≥3 → key_points가 항상 채워져 이 분기는 도달 불가. 버그는 아님.
5. **[범위 밖·참고] 하드코딩 잔존**: `model_used="gpt-4o-mini"`(L247,286), 세션 upsert 테스트값(`password_hash='test'` 등 L208–218)은 이번 트랙 밖(전자=항목3/타 팀, 후자=기존 코드).

## F. 검증 결론

- **호환성: OK.** 단일 파일 additive + 팀원 RAGAS 무변경 → pull/머지 시 기존 작업 변동 없음(단 D-1 공유 핫파일 충돌만 머지 때 주의).
- **목표 달성:** moderator SPOF 3진입점 전부 닫힘 + 보조기능(evidence/RAGAS) 실패 비전파. 계획 #2 닫힘기준 충족.
- **권고:** "남은 것"에 **E-1(핵심 DB 저장 미보호)**를 한 줄 추가하면 fail-soft 경계가 정확해진다. 그 외는 #2 종료로 봐도 무방.

---

# 재검증 — 보완 반영 확인 (2026-06-07, 2차)

위 D~E 지적에 대한 보완을 다시 코드(`git diff`)와 대조했다. **반영 정확, 신규 회귀 없음.**

## G. 지적 → 반영 대조

| 이전 지적 | 반영 | 코드 확인 |
|---|---|---|
| **E-3** fallback 후에도 summary RAGAS 채점(의미 약함) | ✅ 해소 | `used_fallback_summary` 플래그(L168 init→L192 set) 추가, `if not used_fallback_summary:`일 때만 summary eval 스케줄·아니면 skip 로그(L258–265). **evidence eval은 무조건 유지** → 의도대로 |
| **E-4** `_build_summary_fallback` dead 분기 | ✅ 해소 | `if not key_points and non_moderator` 제거, `key_points`를 agenda 리스트 컴프리헨션으로 단일화. `non_moderator`는 요약문 `len(non_moderator)`에서 **여전히 사용**(L57) → 미사용 변수 아님 |
| **E-1** 핵심 DB 저장 미보호 경계 | ✅ 문서화 | 보고서 5절 + 계획서에 "`save_statement`/`save_moderator_summary`/`update_session_status` 실패는 본체 실패(의도된 스코프), fallback summary 저장은 DB 정상일 때만 성립" 명시 |
| 검증 명령 정정 | ✅ | 4절 `python -m py_compile ...`로 수정(Windows `python3` 스텁 회피) |

## H. 신규 회귀 점검

- **컴파일**: `python -m py_compile`(doraise) **통과**.
- **변경 범위 불변**: 여전히 코드 변경은 `moderator_node.py` 1개뿐(`debate_evaluation.py` 등 팀원 코드 0 변경) → B절 호환성 결론 유지.
- **`used_fallback_summary` 로직**: init=False, except에서만 True, 정상 경로엔 영향 없음. summary LLM 정상이면 기존과 동일하게 summary+evidence 둘 다 평가. **fail-soft 동작 보존**.
- **D-1(공유 핫파일)·D-2(`_coerce_agenda` 성공경로 정규화)**: 이번 보완으로 바뀐 바 없음(여전히 유효한 주의사항이나 둘 다 의도된 범위).

## I. 최종 판정 (2차)

- 보완 4건 **전부 정확히 반영**, 신규 결함 없음. 컴파일·호환성·fail-soft 경계 모두 확인.
- **#2 에러핸들링 트랙 종료 확정.** fail-soft 보호 범위(moderator 3노드 + evidence + RAGAS)와 의도된 본체-실패 경계(핵심 DB 저장)가 보고서에 명확히 분리 기록됨.
- 다음: 계획서 고정순서 **#3 MCP 도입**.
