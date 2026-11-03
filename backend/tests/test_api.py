import json

import pytest
from fastapi.testclient import TestClient

from api import connections
from core.security import decrypt_credentials
from main import app
from models import AuditLog, Connection
from tests.conftest import make_connection, make_session_cookie, make_user

client = TestClient(app)

NEW_ACCOUNT = {
    "display_name": "Work",
    "host": "imap.example.com",
    "port": 993,
    "email": "me@example.com",
    "password": "app-password",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
}


class FakeMailBox:
    def __init__(self, *args, **kwargs):
        pass

    def login(self, *args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSMTP(FakeMailBox):
    def starttls(self):
        pass


@pytest.fixture
def fake_mail_servers(monkeypatch):
    monkeypatch.setattr(connections, "ensure_public_host", lambda host, port: None)
    monkeypatch.setattr(connections, "MailBox", FakeMailBox)
    monkeypatch.setattr(connections.smtplib, "SMTP", FakeSMTP)


# --- authentication --------------------------------------------------------


def test_requires_login():
    assert client.get("/api/connections").status_code == 401


def test_expired_session_is_rejected():
    user = make_user("alice")
    cookies = make_session_cookie(user, expired=True)
    assert client.get("/api/connections", cookies=cookies).status_code == 401


def test_unknown_session_is_rejected():
    cookies = {"better-auth.session_token": "forged.signature"}
    assert client.get("/api/connections", cookies=cookies).status_code == 401


# --- ownership -------------------------------------------------------------


def test_lists_only_own_connections():
    alice = make_user("alice")
    bob = make_user("bob")
    make_connection(alice, name="Alice")
    make_connection(bob, name="Bob")

    res = client.get("/api/connections", cookies=make_session_cookie(alice))

    assert res.status_code == 200
    assert [c["display_name"] for c in res.json()] == ["Alice"]


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("PATCH", "/api/connections/{id}", {"display_name": "pwned"}),
        ("PATCH", "/api/connections/{id}/permissions", {"permissions": {"email.send": True}}),
        ("DELETE", "/api/connections/{id}", None),
    ],
)
def test_cannot_modify_another_users_connection(db, method, path, body):
    alice = make_user("alice")
    bob = make_user("bob")
    bob_conn = make_connection(bob, name="Bob")

    res = client.request(
        method, path.format(id=bob_conn), json=body, cookies=make_session_cookie(alice)
    )

    assert res.status_code == 404
    connection = db.get(Connection, bob_conn)
    assert connection.display_name == "Bob"
    assert not any(p.allowed for p in connection.permissions)


def test_unknown_capability_is_rejected():
    alice = make_user("alice")
    conn = make_connection(alice)

    res = client.patch(
        f"/api/connections/{conn}/permissions",
        json={"permissions": {"email.delete_everything": True}},
        cookies=make_session_cookie(alice),
    )

    assert res.status_code == 400


# --- creating a connection -------------------------------------------------


def test_new_connection_grants_nothing_and_encrypts_credentials(db, fake_mail_servers):
    alice = make_user("alice")

    res = client.post("/api/connections/imap", json=NEW_ACCOUNT, cookies=make_session_cookie(alice))

    assert res.status_code == 201
    assert not any(p["allowed"] for p in res.json()["permissions"])
    stored = db.get(Connection, res.json()["id"])
    assert "app-password" not in stored.credentials_encrypted
    assert json.loads(decrypt_credentials(stored.credentials_encrypted))["password"] == "app-password"


def test_private_mail_host_is_refused_before_connecting(monkeypatch):
    def must_not_connect(*args, **kwargs):
        raise AssertionError("connected to a private address")

    monkeypatch.setattr(connections, "MailBox", must_not_connect)
    monkeypatch.setattr(connections.smtplib, "SMTP", must_not_connect)
    alice = make_user("alice")

    res = client.post(
        "/api/connections/imap",
        json={**NEW_ACCOUNT, "host": "127.0.0.1"},
        cookies=make_session_cookie(alice),
    )

    assert res.status_code == 400
    assert "not a public address" in res.json()["detail"]


def test_connection_errors_do_not_leak_server_details(monkeypatch):
    class Unreachable(FakeMailBox):
        def login(self, *args):
            raise OSError("banner: PostgreSQL 17.2 on db:5432")

    monkeypatch.setattr(connections, "ensure_public_host", lambda host, port: None)
    monkeypatch.setattr(connections, "MailBox", Unreachable)
    alice = make_user("alice")

    res = client.post("/api/connections/imap", json=NEW_ACCOUNT, cookies=make_session_cookie(alice))

    assert res.status_code == 400
    assert "PostgreSQL" not in res.json()["detail"]


# --- audit log -------------------------------------------------------------


def test_audit_log_shows_only_own_entries(db):
    alice = make_user("alice")
    bob = make_user("bob")
    db.add_all(
        [
            AuditLog(user_id=alice, tool_name="list_emails", status="success"),
            AuditLog(user_id=bob, tool_name="send_email", status="success"),
        ]
    )
    db.commit()

    res = client.get("/api/audit-log", cookies=make_session_cookie(alice))

    assert res.status_code == 200
    assert [e["tool_name"] for e in res.json()] == ["list_emails"]


@pytest.mark.parametrize("limit, expected", [(-5, 1), (0, 1), (2, 2), (1000, 3)])
def test_audit_log_limit_is_clamped(db, limit, expected):
    alice = make_user("alice")
    db.add_all([AuditLog(user_id=alice, tool_name="list_emails", status="success") for _ in range(3)])
    db.commit()

    res = client.get(f"/api/audit-log?limit={limit}", cookies=make_session_cookie(alice))

    assert res.status_code == 200
    assert len(res.json()) == expected
