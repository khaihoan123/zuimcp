"""Test setup. Runs against a real Postgres (the permission queries use
Postgres-only SQL), in a database whose name must end in `_test` because every
test session drops and recreates all tables.

The database comes from TEST_DATABASE_URL, or — for local runs — from
DATABASE_URL in backend/.env with `_test` appended to the database name.
"""

import datetime
import json
import os
import re
import secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        dev_url = dotenv_values(Path(__file__).resolve().parent.parent / ".env").get("DATABASE_URL")
        if not dev_url:
            pytest.exit("Set TEST_DATABASE_URL (or DATABASE_URL in backend/.env) to run the tests", 2)
        url = re.sub(r"/(\w+)$", r"/\1_test", dev_url)
    if not re.search(r"/\w+_test$", url):
        pytest.exit("Refusing to run: the test database name must end in '_test'", 2)
    return url


# Must happen before any app module is imported: core.db and core.security
# read these at import time, and load_dotenv() never overrides variables
# that are already set.
os.environ["DATABASE_URL"] = _test_database_url()
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from sqlalchemy import text  # noqa: E402

from core.db import Base, SessionLocal, engine  # noqa: E402
from core.security import encrypt_credentials  # noqa: E402
from models import Connection, Permission  # noqa: E402

CAPABILITIES = ["email.read", "email.search", "email.mark_seen", "email.draft", "email.send"]


def _drop_all():
    # session references "user", so it has to go first.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS session"))
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session", autouse=True)
def schema():
    _drop_all()
    Base.metadata.create_all(engine)
    # Better Auth owns the real `session` table; this is the subset core/auth.py reads.
    with engine.begin() as conn:
        conn.execute(
            text(
                'CREATE TABLE session (id text PRIMARY KEY, token text UNIQUE NOT NULL, '
                '"userId" varchar(255) NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, '
                '"expiresAt" timestamptz NOT NULL)'
            )
        )
    yield
    _drop_all()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE "user" CASCADE'))


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def make_user(user_id: str) -> str:
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO "user" (id) VALUES (:id)'), {"id": user_id})
    return user_id


def make_session_cookie(user_id: str, expired: bool = False) -> dict:
    """Returns the cookies a browser logged in as `user_id` would send."""
    token = secrets.token_hex(16)
    delta = datetime.timedelta(hours=-1 if expired else 1)
    with engine.begin() as conn:
        conn.execute(
            text('INSERT INTO session (id, token, "userId", "expiresAt") VALUES (:id, :token, :uid, :exp)'),
            {
                "id": secrets.token_hex(8),
                "token": token,
                "uid": user_id,
                "exp": datetime.datetime.now(datetime.timezone.utc) + delta,
            },
        )
    return {"better-auth.session_token": f"{token}.signature"}


def make_connection(
    user_id: str,
    name: str = "Work",
    granted: tuple[str, ...] = (),
    status: str = "active",
    host: str = "imap.example.com",
) -> int:
    credentials = {
        "host": host,
        "port": 993,
        "email": f"{name.lower()}@example.com",
        "password": "secret",
        "smtp_host": host,
        "smtp_port": 587,
    }
    session = SessionLocal()
    try:
        connection = Connection(
            user_id=user_id,
            service_type="imap",
            display_name=name,
            credentials_encrypted=encrypt_credentials(json.dumps(credentials)),
            status=status,
        )
        session.add(connection)
        session.flush()
        for capability in CAPABILITIES:
            session.add(
                Permission(
                    connection_id=connection.id,
                    capability=capability,
                    allowed=capability in granted,
                )
            )
        session.commit()
        return connection.id
    finally:
        session.close()
