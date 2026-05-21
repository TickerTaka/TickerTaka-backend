from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    inspector = inspect(engine)

    table_name = "news_cache"
    tables = inspector.get_table_names()

    print("[tables]")
    for table in tables:
        print(f"- {table}")

    if table_name not in tables:
        print(f"\n[error] table not found: {table_name}")
        raise SystemExit(1)

    print(f"\n[columns:{table_name}]")
    for col in inspector.get_columns(table_name):
        print(
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col["nullable"],
                "default": col.get("default"),
            }
        )

    print(f"\n[primary_key:{table_name}]")
    print(inspector.get_pk_constraint(table_name))

    print(f"\n[unique_constraints:{table_name}]")
    for item in inspector.get_unique_constraints(table_name):
        print(item)

    print(f"\n[indexes:{table_name}]")
    for item in inspector.get_indexes(table_name):
        print(item)

    print(f"\n[foreign_keys:{table_name}]")
    for item in inspector.get_foreign_keys(table_name):
        print(item)

    print(f"\n[sample_rows:{table_name}]")
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 5"))
        for row in rows:
            print(dict(row._mapping))


if __name__ == "__main__":
    try:
        main()
    except SQLAlchemyError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1) from exc
