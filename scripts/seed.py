from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.db import session_scope
from app.models import AppUser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a test app_user row.")
    parser.add_argument("--email", default="phase2-test-user@example.com")
    parser.add_argument("--name", default="Phase2 Test User")
    parser.add_argument("--password-hash", default="phase2-test-hash")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with session_scope() as session:
        existing = session.scalar(select(AppUser).where(AppUser.email == args.email))
        if existing is not None:
            print(
                {
                    "status": "exists",
                    "id": str(existing.id),
                    "email": existing.email,
                    "name": existing.name,
                }
            )
            return

        user = AppUser(
            email=args.email,
            password_hash=args.password_hash,
            name=args.name,
        )
        session.add(user)
        session.flush()
        print(
            {
                "status": "created",
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
            }
        )


if __name__ == "__main__":
    main()
