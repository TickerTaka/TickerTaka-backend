"""Validate that SQLAlchemy ENUM values match the database ENUM definitions."""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import SessionLocal
from app.models.cache import RefreshJobStatus, RefreshJobType, SourceType
from app.models.debate import AgentRole, DebateCategory, DebateMode, DebateRound, DebateStatus
from app.models.ticker import MarketType

ENUM_MAP: dict[str, type] = {
    "market_type": MarketType,
    "source_type": SourceType,
    "refresh_job_type": RefreshJobType,
    "refresh_job_status": RefreshJobStatus,
    "debate_category": DebateCategory,
    "debate_mode": DebateMode,
    "debate_status": DebateStatus,
    "debate_round": DebateRound,
    "agent_role": AgentRole,
}


def main() -> None:
    query = text(
        """
        SELECT t.typname, e.enumlabel
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = ANY(:names)
        ORDER BY t.typname, e.enumsortorder
        """
    )
    with SessionLocal() as session:
        rows = session.execute(query, {"names": list(ENUM_MAP.keys())}).all()

    db_values: dict[str, list[str]] = {}
    for typname, label in rows:
        db_values.setdefault(typname, []).append(label)

    mismatch = 0
    for type_name, py_enum in ENUM_MAP.items():
        db_vals = set(db_values.get(type_name, []))
        py_vals = {member.value for member in py_enum}
        only_db = db_vals - py_vals
        only_py = py_vals - db_vals
        if only_db or only_py:
            mismatch += 1
            print(f"[MISMATCH] {type_name}")
            if only_db:
                print(f"  only in DB:    {sorted(only_db)}")
            if only_py:
                print(f"  only in model: {sorted(only_py)}")
        else:
            print(f"[OK] {type_name:25s} = {sorted(py_vals)}")
    if mismatch:
        raise SystemExit(f"ENUM mismatches: {mismatch}")


if __name__ == "__main__":
    main()
