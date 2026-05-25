# test_debate.py
"""에이전트 단독 실행 테스트"""
import asyncio
import uuid
from dotenv import load_dotenv
load_dotenv(".env.local")

from app.agents.debate_checkpoint import load_checkpoint, merge_state, save_checkpoint
from app.agents.debate_graph import debate_graph
from app.agents.state import DebateState
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker


async def run(
    symbol:      str = "005930.KS",
    symbol_name: str = "삼성전자",
    category:    str = "financial",
    session_id:  str | None = None,
    user_id:     str = "demo-user",
):
    session_id = session_id or str(uuid.uuid4())
    print(f"\n{'='*60}")
    print(f"  {symbol_name} ({symbol}) — {category} 토론")
    print(f"  session_id: {session_id}")
    print(f"{'='*60}\n")

    tracker = get_tracker()
    start_result = tracker.try_start_session(
        user_id=user_id,
        symbol=symbol,
        session_id=session_id,
        estimated_tokens=0,
    )
    if not start_result.allowed:
        print(f"⛔ 토론 시작 불가: {start_result.reason}")
        return

    state: DebateState = load_checkpoint(session_id) or {
        "session_id":         session_id,
        "user_id":            user_id,
        "symbol":             symbol,
        "symbol_name":        symbol_name,
        "category":           category,
        "user_portfolio":     {},
        "current_round":      "opening",
        "round_order":        0,
        "max_rounds":         3,
        "agenda":             [],
        "price_context":      "",
        "financial_context":  "",
        "evidence_context":   "",
        "news_chunks":        [],
        "statements":         [],
        "moderator_flag":     "ok",
        "intervention_note":  "",
        "hallucination_count": 0,
        "summary_content":    "",
        "key_points":         [],
    }

    icons = {"bull": "📈", "bear": "📉", "moderator": "⚖️"}

    try:
        with tracker.bind_context(user_id=user_id, symbol=symbol, session_id=session_id):
            settings = get_settings()
            async for chunk in debate_graph.astream(
                state,
                {"recursion_limit": settings.debate_graph_recursion_limit},
            ):
                node = list(chunk.keys())[0]
                data = chunk[node]
                state = merge_state(state, data)
                save_checkpoint(state)

                if "agenda" in data and data["agenda"]:
                    print("📋 [의제]")
                    for i, a in enumerate(data["agenda"], 1):
                        print(f"  {i}. {a}")
                    print()

                for stmt in data.get("statements", []):
                    role  = stmt["agent_role"]
                    icon  = icons.get(role, "🤖")
                    print(f"{icon} [{role.upper()} / {stmt['round'].upper()}]")
                    print(stmt["content"])
                    if stmt.get("evidences"):
                        print(f"  └─ 근거 {len(stmt['evidences'])}개")
                    print()

                if data.get("summary_content"):
                    print(f"{'='*60}\n📝 최종 요약\n{'='*60}")
                    print(data["summary_content"])
                    for pt in data.get("key_points", []):
                        print(f"  • {pt}")
    finally:
        tracker.end_session(user_id=user_id, symbol=symbol, session_id=session_id)

    print("\n✅ 완료")


if __name__ == "__main__":
    import sys
    symbol      = sys.argv[1] if len(sys.argv) > 1 else "005930.KS"
    symbol_name = sys.argv[2] if len(sys.argv) > 2 else "삼성전자"
    category    = sys.argv[3] if len(sys.argv) > 3 else "financial"
    session_id  = sys.argv[4] if len(sys.argv) > 4 else None
    user_id     = sys.argv[5] if len(sys.argv) > 5 else "demo-user"
    asyncio.run(run(symbol, symbol_name, category, session_id, user_id))
