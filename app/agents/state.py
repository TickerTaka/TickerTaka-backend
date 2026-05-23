# app/agents/state.py
from __future__ import annotations
from typing import Annotated
import operator
from typing_extensions import TypedDict


class Statement(TypedDict):
    agent_role:  str        # bull | bear | moderator | system
    round:       str        # opening | rebuttal | closing | summary
    round_order: int
    content:     str
    model_used:  str
    evidences:   list[dict]


class DebateState(TypedDict):
    # 세션 메타
    session_id:     str
    symbol:         str
    symbol_name:    str
    category:       str       # technical | financial | market | macro | synthesis
    user_portfolio: dict      # {avg_price: float} 또는 {}

    # 라운드 제어
    current_round:  str       # opening | rebuttal | closing | summary
    round_order:    int
    max_rounds:     int

    # Data Agent가 채우는 컨텍스트
    agenda:            list[str]
    price_context:     str
    financial_context: str
    evidence_context:  str
    news_chunks:       list[str]

    # 발언 누적 (add 리듀서)
    statements: Annotated[list[Statement], operator.add]

    # 사회자 제어 신호
    moderator_flag:      str    # ok | intervene | end
    intervention_note:   str
    hallucination_count: int

    # 최종 결과
    summary_content: str
    key_points:      list[str]
