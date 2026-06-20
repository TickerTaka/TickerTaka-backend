# app/agents/state.py
from __future__ import annotations
from typing import Annotated
import operator
from typing_extensions import NotRequired, TypedDict


class Statement(TypedDict):
    agent_role:  str        # bull | bear | moderator | system
    round:       str        # claim | rebuttal | counter_rebuttal | summary
    round_order: int
    topic_index: int        # 0-2 (어느 주제의 발언인지)
    content:     str
    model_used:  str
    evidences:   list[dict]


class DebateState(TypedDict):
    # 세션 메타
    session_id:     str
    user_id:        str
    symbol:         str
    symbol_name:    str
    category:       str       # technical | financial | market | macro | synthesis
    decision_agent: NotRequired[str]  # moderator | judge

    # 라운드 제어
    current_round:       str   # claim | rebuttal | counter_rebuttal | summary
    round_order:         int
    max_rounds:          int
    current_topic_index: int   # 0-2 (현재 토론 중인 주제 인덱스)
    current_turn:        int   # 1=bull주장, 2=bear반박, 3=bull재반박

    # Data Agent가 채우는 컨텍스트
    agenda:             list[str]
    price_context:      str
    financial_context:  str
    evidence_context:   str
    news_chunks:        list[str]
    initial_evidences:  list[dict]   # data_agent가 검색한 raw evidences (RAGAS 평가용)

    # 발언 누적 (add 리듀서)
    statements: Annotated[list[Statement], operator.add]

    # 에이전트별 교정 이력 (add 리듀서 — 세션 전체 누적)
    bull_corrections: Annotated[list[str], operator.add]
    bear_corrections: Annotated[list[str], operator.add]

    # 사회자 제어 신호
    moderator_flag:           str    # ok | intervene | end
    intervention_note:        str
    corrected_fact:           str    # 환각 탐지 시 올바른 수치 (재발언 교정용)
    hallucination_count:      int    # 총 누적 (Langfuse 로깅용)
    bull_hallucination_count: int    # Bull 누적 (종료 판단용)
    bear_hallucination_count: int    # Bear 누적 (종료 판단용)
    debate_ended_by:          NotRequired[str]  # bull | bear | ""
    debate_end_reason:   NotRequired[str]
    judge_result:        NotRequired[dict]

    # 최종 결과
    summary_content: str
    key_points:      list[str]
