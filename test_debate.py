# test_debate.py
"""
에이전트 단독 실행 테스트
터미널 실행 예시 python test_debate.py 005930 삼성전자 financial
"""
import warnings
warnings.filterwarnings("ignore", message="Field .* has conflict with protected namespace")
import asyncio
import uuid
import sys
import os
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["CHROMA_TELEMETRY_ENABLED"] = "false"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
from dotenv import load_dotenv
load_dotenv(".env")
load_dotenv(".env.local", override=True)

# --provider 플래그: get_settings() lru_cache 호출 전에 env 주입
# 사용 예) --provider openrouter  또는  --provider openai
_PROVIDER_IDX = next((i for i, a in enumerate(sys.argv) if a == "--provider"), None)
if _PROVIDER_IDX is not None and _PROVIDER_IDX + 1 < len(sys.argv):
    _provider_val = sys.argv[_PROVIDER_IDX + 1]
    if _provider_val not in ("openai", "openrouter", "anthropic"):
        print(f"[error] --provider 값은 'openai', 'openrouter', 'anthropic' 중 하나이어야 합니다. (받은 값: {_provider_val})")
        sys.exit(1)
    os.environ["DEBATE_LLM_PROVIDER"] = _provider_val
    sys.argv.pop(_PROVIDER_IDX)  # "--provider"
    sys.argv.pop(_PROVIDER_IDX)  # 값
    print(f"[provider] {_provider_val} 모드로 실행")
import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.CRITICAL)

if "--help" in sys.argv or "-h" in sys.argv:
    print(
        "Usage: python test_debate.py [symbol] [symbol_name] [category] [session_id] [user_id] "
        "[--no-db] [--moderator|--judge] [--wait-eval] [--provider openai|openrouter|anthropic]"
    )
    sys.exit(0)

# --no-db 플래그: DB 저장 없이 에이전트 로직만 테스트
NO_DB = "--no-db" in sys.argv
if NO_DB:
    sys.argv.remove("--no-db")
    
    # DB 저장 함수를 모두 no-op으로 패치
    import unittest.mock as mock
    _noop = mock.AsyncMock(return_value=None)
    import app.repositories.debate_repo as _repo
    _repo.save_statement      = mock.AsyncMock(return_value=1)
    _repo.save_evidence       = _noop
    _repo.save_moderator_summary = _noop
    _repo.update_session_status  = _noop
    _repo.fail_session_if_running = _noop
    
    # RAGAS eval DB 저장도 skip
    import app.domain.debate_evaluation as _eval
    _orig_summary_eval  = _eval.evaluate_summary_async
    _orig_evidence_eval = _eval.evaluate_evidence_async
    async def _eval_summary_no_db(session_id, statements, summary, agenda=None, save=True):
        return await _orig_summary_eval(session_id, statements, summary, agenda, save=False)
    async def _eval_evidence_no_db(session_id, evidences, query, agenda=None, save=True):
        return await _orig_evidence_eval(session_id, evidences, query, agenda, save=False)
    _eval.evaluate_summary_async  = _eval_summary_no_db
    _eval.evaluate_evidence_async = _eval_evidence_no_db

JUDGE_MODE = "--judge" in sys.argv
if JUDGE_MODE:
    sys.argv.remove("--judge")
MODERATOR_MODE = "--moderator" in sys.argv
if MODERATOR_MODE:
    sys.argv.remove("--moderator")
WAIT_EVAL = "--wait-eval" in sys.argv
if WAIT_EVAL:
    sys.argv.remove("--wait-eval")
SAVE_LOG = "--save-log" in sys.argv
if SAVE_LOG:
    sys.argv.remove("--save-log")

import io
import subprocess
from pathlib import Path

from app.agents.debate_checkpoint import load_checkpoint, merge_state, save_checkpoint
from app.agents.debate_graph import debate_graph
from app.agents.state import DebateState
from app.config import get_settings
from app.core.debate_runtime_guard import get_tracker


class _Tee:
    """stdout을 콘솔과 파일에 동시에 출력."""
    def __init__(self, file):
        self._file = file
        self._stdout = sys.stdout

    def write(self, data):
        self._stdout.write(data)
        self._file.write(data)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, *_):
        sys.stdout = self._stdout
        self._file.close()


def _make_log_path(provider: str, session_id: str) -> Path:
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        sha = "unknown"
    sid = session_id[:8]
    return reports / f"debate-{provider}-{sha}-{sid}.log"


async def run(
    symbol:      str = "005930",
    symbol_name: str = "삼성전자",
    category:    str = "financial",
    session_id:  str | None = None,
    user_id:     str = "00000000-0000-0000-0000-000000000001",
    decision_agent: str = "moderator",
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
        "session_id":          session_id,
        "user_id":             user_id,
        "symbol":              symbol,
        "symbol_name":         symbol_name,
        "category":            category,
        "decision_agent":      decision_agent,
        "current_round":       "claim",
        "round_order":         0,
        "max_rounds":          12,  # 3 주제 × 4 턴
        "current_topic_index": 0,
        "current_turn":        1,
        "agenda":              [],
        "price_context":       "",
        "financial_context":   "",
        "evidence_context":    "",
        "news_chunks":         [],
        "initial_evidences":   [],
        "statements":          [],
        "bull_corrections":    [],
        "bear_corrections":    [],
        "moderator_flag":           "ok",
        "intervention_note":        "",
        "corrected_fact":           "",
        "hallucination_count":      0,
        "bull_hallucination_count": 0,
        "bear_hallucination_count": 0,
        "debate_ended_by":          "",
        "debate_end_reason":   "",
        "judge_result":        {},
        "summary_content":     "",
        "key_points":          [],
    }

    icons = {"bull": "📈", "bear": "📉", "moderator": "⚖️"}
    import time
    total_start = time.time()
    node_times  = {}

    try:
        with tracker.bind_context(user_id=user_id, symbol=symbol, session_id=session_id):
            settings = get_settings()
            # Langfuse v4: CallbackHandler를 graph config로 주입, metadata로 session 묶음
            graph_config: dict = {"recursion_limit": settings.debate_graph_recursion_limit}
            try:
                from app.core.tracing import get_langfuse
                if get_langfuse() is not None:
                    from langfuse.langchain import CallbackHandler
                    graph_config["callbacks"] = [CallbackHandler()]
                    graph_config["metadata"] = {
                        "langfuse_session_id": session_id,
                        "langfuse_user_id": user_id,
                        "langfuse_tags": ["debate", "test"],
                    }
            except Exception:
                pass
            async for chunk in debate_graph.astream(state, graph_config):
                node = list(chunk.keys())[0]
                data = chunk[node]
                elapsed = time.time() - total_start
                node_times[node] = node_times.get(node, 0) + 1
                print(f"⏱  [{node}] +{elapsed:.1f}s")

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
                    topic = stmt.get("topic_index")
                    topic_str = f"주제{topic+1} / " if topic is not None else ""
                    print(f"{icon} [{role.upper()} / {topic_str}{stmt['round'].upper()}]")
                    print(stmt["content"])
                    if stmt.get("evidences"):
                        print(f"  └─ 근거 {len(stmt['evidences'])}개")
                    print()

                if data.get("debate_ended_by"):
                    role = data["debate_ended_by"].upper()
                    print(f"⛔ 토론 중단: {role} 발언 검증 문제")
                    if data.get("debate_end_reason"):
                        print(f"  └─ {data['debate_end_reason']}")
                    print()

                if data.get("judge_result"):
                    judge = data["judge_result"]
                    print("🧑‍⚖️ [JUDGE 판정]")
                    print(f"  방향: {judge.get('leaning', 'mixed')} / 확신도: {judge.get('confidence', 'low')}")
                    print(f"  이유: {judge.get('rationale', '')}")
                    print(f"  판단 가이드: {judge.get('action_guidance', '')}")
                    print()

                if data.get("summary_content"):
                    print(f"{'='*60}\n📝 최종 요약\n{'='*60}")
                    print(data["summary_content"])
                    for pt in data.get("key_points", []):
                        print(f"  • {pt}")
    finally:
        tracker.end_session(user_id=user_id, symbol=symbol, session_id=session_id)

    total = time.time() - total_start
    print(f"\n✅ 완료 — 총 소요시간: {total:.1f}s")

    if not WAIT_EVAL:
        tasks = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
        if tasks:
            print(f"\nℹ️ RAGAS 평가 태스크 {len(tasks)}개는 기다리지 않고 종료합니다. 평가까지 보려면 --wait-eval을 붙이세요.")
        return

    # RAGAS 백그라운드 태스크가 완료될 때까지 대기
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
    if tasks:
        print(f"\n⏳ RAGAS 평가 대기 중... ({len(tasks)}개 태스크)")
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    import sys
    symbol      = sys.argv[1] if len(sys.argv) > 1 else "005930"
    symbol_name = sys.argv[2] if len(sys.argv) > 2 else "삼성전자"
    category    = sys.argv[3] if len(sys.argv) > 3 else "financial"
    session_id  = sys.argv[4] if len(sys.argv) > 4 else None
    user_id     = sys.argv[5] if len(sys.argv) > 5 else "00000000-0000-0000-0000-000000000001"
    decision_agent = "judge" if JUDGE_MODE else "moderator"

    _session_id = session_id or str(uuid.uuid4())
    if SAVE_LOG:
        provider = os.environ.get("DEBATE_LLM_PROVIDER", "openai")
        log_path = _make_log_path(provider, _session_id)
        print(f"[save-log] → {log_path}")
        with _Tee(open(log_path, "w", encoding="utf-8")) as _tee:
            asyncio.run(run(symbol, symbol_name, category, _session_id, user_id, decision_agent))
        print(f"[save-log] 저장 완료: {log_path}")
    else:
        asyncio.run(run(symbol, symbol_name, category, _session_id, user_id, decision_agent))
