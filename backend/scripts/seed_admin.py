#!/usr/bin/env python3
"""
Seed the default admin user on first boot.

Idempotent: does nothing if a user with the admin email already
exists. Generates a real bcrypt hash via the actual bcrypt library
(rather than a hardcoded hash baked into a SQL file, which can't be
verified at write time) and prints the generated password to stdout
exactly once -- it is never stored in plaintext anywhere.

Run automatically by the api container's entrypoint on startup (see
docker/entrypoint.sh), or manually:

    python scripts/seed_admin.py
    python scripts/seed_admin.py --email ops@company.com --password 'a-real-password'
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

sys.path.insert(0, "/app")  # so `from app.core... ` resolves when run inside the container

from app.core.config import get_settings
from app.security.rbac import hash_password


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    parser = argparse.ArgumentParser(description="Seed the default admin user")
    parser.add_argument("--email",     default=os.environ.get("ADMIN_EMAIL",    "admin@cpgplatform.com"))
    parser.add_argument("--password",  default=os.environ.get("ADMIN_PASSWORD", None), help="If omitted, a secure random password is generated and printed once")
    parser.add_argument("--full-name", default="Default Admin")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)

    try:
        with engine.connect() as conn:
            existing = conn.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": args.email}).first()
            if existing:
                print(f"Admin user '{args.email}' already exists -- nothing to do.")
                return

            password = args.password or generate_password()
            role_id = conn.execute(text("SELECT role_id FROM roles WHERE role_name = 'admin'")).scalar()
            if role_id is None:
                print("ERROR: 'admin' role not found. Has schema_security.sql run yet?", file=sys.stderr)
                sys.exit(1)

            conn.execute(
                text("""
                    INSERT INTO users (email, hashed_password, full_name, role_id)
                    VALUES (:email, :pw, :name, :role_id)
                """),
                {
                    "email": args.email,
                    "pw": hash_password(password),
                    "name": args.full_name,
                    "role_id": role_id,
                },
            )
            conn.commit()
    except ProgrammingError:
        # users/roles tables don't exist yet -- schema init hasn't run
        # or hasn't finished. Not an error: the caller (wait-for-postgres.sh)
        # retries this script for a bounded number of attempts.
        print("Users/roles tables not ready yet -- will retry.", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Admin user created.")
    print(f"  Email:    {args.email}")
    if args.password is None:
        print(f"  Password: {password}")
        print()
        print("  This password is shown ONCE and is not stored anywhere")
        print("  in plaintext. Save it now and change it after first login.")
    print("=" * 60)


if __name__ == "__main__":
    main()
