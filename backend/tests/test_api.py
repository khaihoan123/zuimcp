import json

import pytest
from fastapi.testclient import TestClient

from api import connections
from core.security import decrypt_credentials
from main import app
from models import AuditLog, Connection
from tests.conftest import make_connection, make_session_cookie, make_user

client = TestClient(app)


def logged_in(user_id: str, expired: bool = False) -> TestClient:
    """A client carrying `user_id`'s session cookie, like their browser would."""
    return TestClient(app, cookies=make_session_cookie(user_id, expired=expired))

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
    assert logged_in(user, expired=True).get("/api/connections").status_code == 401


def test_unknown_session_is_rejected():
    forged = TestClient(app, cookies={"better-auth.session_token": "forged.signature"})
    assert forged.get("/api/connections").status_code == 401


# --- ownership -------------------------------------------------------------


def test_lists_only_own_connections():
    alice = make_user("alice")
    bob = make_user("bob")
    make_connection(alice, name="Alice")
    make_connection(bob, name="Bob")

    res = logged_in(alice).get("/api/connections")

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

    res = logged_in(alice).request(method, path.format(id=bob_conn), json=body)

    assert res.status_code == 404
    connection = db.get(Connection, bob_conn)
    assert connection.display_name == "Bob"
    assert not any(p.allowed for p in connection.permissions)


def test_unknown_capability_is_rejected():
    alice = make_user("alice")
    conn = make_connection(alice)

    res = logged_in(alice).patch(
        f"/api/connections/{conn}/permissions",
        json={"permissions": {"email.delete_everything": True}},
    )

    assert res.status_code == 400


# --- creating a connection -------------------------------------------------


def test_new_connection_grants_nothing_and_encrypts_credentials(db, fake_mail_servers):
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/imap", json=NEW_ACCOUNT)

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

    res = logged_in(alice).post("/api/connections/imap", json={**NEW_ACCOUNT, "host": "127.0.0.1"})

    assert res.status_code == 400
    assert "not a public address" in res.json()["detail"]


def test_connection_errors_do_not_leak_server_details(monkeypatch):
    class Unreachable(FakeMailBox):
        def login(self, *args):
            raise OSError("banner: PostgreSQL 17.2 on db:5432")

    monkeypatch.setattr(connections, "ensure_public_host", lambda host, port: None)
    monkeypatch.setattr(connections, "MailBox", Unreachable)
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/imap", json=NEW_ACCOUNT)

    assert res.status_code == 400
    assert "PostgreSQL" not in res.json()["detail"]


# --- the same mailbox twice -----------------------------------------------


def imap_rows(db, user_id):
    db.expire_all()
    return db.query(Connection).filter_by(user_id=user_id, service_type="imap").all()


@pytest.mark.parametrize("again", [
    {},
    {"email": "ME@Example.com", "host": "IMAP.example.com", "display_name": "Other name"},
])
def test_same_imap_mailbox_twice_is_refused(db, fake_mail_servers, again):
    alice = make_user("alice")
    client = logged_in(alice)
    assert client.post("/api/connections/imap", json=NEW_ACCOUNT).status_code == 201

    res = client.post("/api/connections/imap", json={**NEW_ACCOUNT, **again})

    assert res.status_code == 409
    assert res.json()["detail"] == "This mailbox is already connected as 'Work'."
    assert len(imap_rows(db, alice)) == 1


def test_duplicate_is_refused_before_logging_in(monkeypatch):
    alice = make_user("alice")
    make_connection(alice, name="Work")  # work@example.com on imap.example.com

    def must_not_connect(*args, **kwargs):
        raise AssertionError("logged in to check a duplicate")

    monkeypatch.setattr(connections, "ensure_public_host", lambda host, port: None)
    monkeypatch.setattr(connections, "MailBox", must_not_connect)

    res = logged_in(alice).post(
        "/api/connections/imap", json={**NEW_ACCOUNT, "email": "work@example.com"}
    )

    assert res.status_code == 409


def test_same_address_on_another_server_or_for_another_user_is_allowed(db, fake_mail_servers):
    alice = make_user("alice")
    bob = make_user("bob")
    logged_in(alice).post("/api/connections/imap", json=NEW_ACCOUNT)

    other_server = logged_in(alice).post("/api/connections/imap", json={**NEW_ACCOUNT, "host": "imap.other.com"})
    other_user = logged_in(bob).post("/api/connections/imap", json=NEW_ACCOUNT)

    assert (other_server.status_code, other_user.status_code) == (201, 201)
    # Same address, different server: allowed, and reported so the dashboard can warn.
    assert other_server.json()["same_address_as"] == [imap_rows(db, alice)[0].id]
    assert other_user.json()["same_address_as"] == []


@pytest.mark.parametrize("host", ["imap.gmail.com", "imap.googlemail.com"])
def test_google_imap_for_a_connected_gmail_is_refused(db, fake_mail_servers, host):
    # IMAP on Google's own server is the same mailbox as the Gmail account.
    from tests.test_email_providers import make_gmail_connection

    alice = make_user("alice")
    make_gmail_connection(alice, address="Me@Example.com", name="Personal Gmail")

    res = logged_in(alice).post("/api/connections/imap", json={**NEW_ACCOUNT, "host": host})

    assert res.status_code == 409
    assert res.json()["detail"] == (
        "This address is already connected with Gmail as 'Personal Gmail'. "
        f"{host} is Google's own IMAP server, so it is the same mailbox. "
        "Keep using the Gmail one, or disconnect it first."
    )
    assert imap_rows(db, alice) == []


def test_other_imap_server_for_a_connected_gmail_is_allowed(db, fake_mail_servers):
    # E.g. the old cPanel mailbox during a move to Gmail: a different mailbox.
    from tests.test_email_providers import make_gmail_connection

    alice = make_user("alice")
    gmail_id = make_gmail_connection(alice, address="me@example.com")

    res = logged_in(alice).post("/api/connections/imap", json=NEW_ACCOUNT)  # imap.example.com

    assert res.status_code == 201
    assert res.json()["same_address_as"] == [gmail_id]


# --- audit log -------------------------------------------------------------


def test_audit_log_shows_only_own_entries(db):
    alice = make_user("alice")
    bob = make_user("bob")
    db.add_all(
        [
            AuditLog(user_id=alice, tool_name="email_list", status="success"),
            AuditLog(user_id=bob, tool_name="email_send", status="success"),
        ]
    )
    db.commit()

    res = logged_in(alice).get("/api/audit-log")

    assert res.status_code == 200
    assert [e["tool_name"] for e in res.json()] == ["email_list"]


@pytest.mark.parametrize("limit, expected", [(-5, 1), (0, 1), (2, 2), (1000, 3)])
def test_audit_log_limit_is_clamped(db, limit, expected):
    alice = make_user("alice")
    db.add_all([AuditLog(user_id=alice, tool_name="email_list", status="success") for _ in range(3)])
    db.commit()

    res = logged_in(alice).get(f"/api/audit-log?limit={limit}")

    assert res.status_code == 200
    assert len(res.json()) == expected
