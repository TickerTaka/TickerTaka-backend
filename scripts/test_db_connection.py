from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import engine


def main() -> None:
    try:
        with engine.connect() as conn:
            now = conn.execute(text("select now()")).scalar()
            version = conn.execute(text("select version()")).scalar()
        print("DB connection OK")
        print(f"Server time: {now}")
        print(f"PostgreSQL: {version}")
    except SQLAlchemyError as exc:
        print("DB connection FAILED")
        print(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
