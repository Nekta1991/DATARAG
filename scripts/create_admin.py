"""Create the single admin account for the dashboard.

    python scripts/create_admin.py you@example.com

Run it yourself in a terminal: the password is typed at a hidden prompt, so it
never appears in a transcript, a log, or shell history.

1. Registers the account with Neon Auth (email + password sign-up), so the
   password is hashed by Neon Auth itself, never by this script.
2. Sets role = 'admin' directly in neon_auth."user". The API (rag/api.py)
   accepts only that role, so any other sign-up gets 403.

Cost: $0. No Anthropic call.
"""

from __future__ import annotations

import getpass
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
import psycopg

from rag import config  # noqa: F401  - loads .env


def main():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit("usage: python scripts/create_admin.py you@example.com")
    email = sys.argv[1].strip().lower()
    base = os.environ["NEON_AUTH_BASE_URL"].rstrip("/")
    dsn = os.environ["DATABASE_URL"]

    with psycopg.connect(dsn) as conn:
        admins = conn.execute(
            """SELECT email FROM neon_auth."user" WHERE role LIKE '%%admin%%'""").fetchall()
        exists = conn.execute('SELECT id FROM neon_auth."user" WHERE lower(email) = %s',
                              (email,)).fetchone()
    if admins and not exists:
        sys.exit(f"an admin already exists ({admins[0][0]}); only one is intended")

    if not exists:
        pw = getpass.getpass("New admin password (min 8 chars): ")
        if len(pw) < 8 or pw != getpass.getpass("Repeat password: "):
            sys.exit("passwords too short or do not match; nothing created")
        # Neon Auth trusts localhost origins by default (allow_localhost).
        r = httpx.post(f"{base}/sign-up/email", timeout=30,
                       headers={"Origin": "http://localhost:3000"},
                       json={"email": email, "password": pw, "name": "admin"})
        del pw
        if r.status_code >= 400:
            sys.exit(f"sign-up failed: HTTP {r.status_code} {r.text[:300]}")
        print("account registered with Neon Auth")

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            """UPDATE neon_auth."user" SET role = 'admin', "updatedAt" = now()
               WHERE lower(email) = %s RETURNING id, email, role""", (email,)).fetchone()
        conn.commit()
    if row is None:
        sys.exit("user not found after sign-up; nothing changed")
    print(f"admin ready: {row[1]}  role={row[2]}  id={row[0]}")


if __name__ == "__main__":
    main()
