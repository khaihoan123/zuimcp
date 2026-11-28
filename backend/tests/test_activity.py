"""The activity log's account changes (core.audit), paused accounts, and the
log API's pages and filters."""

import json

import pytest

from core.accounts import resolve_account
from core.audit import mark_login_refused
from models import AuditLog, Connection
from tests.conftest import make_connection, make_user
from tests.test_api import EDIT_IMAP, fake_mail_servers, logged_in  # noqa: F401  fixture

pytestmark = pytest.mark.usefixtures("db")


def events(db, user_id):
    db.expire_all()
    return [
        (e.actor, e.action, e.capability, e.detail)
        for e in db.query(AuditLog).filter(AuditLog.user_id == user_id).order_by(AuditLog.id)
    ]


# --- account changes are logged ------------------------------------------------


def test_permission_changes_are_logged_only_when_something_changes(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")
    client = logged_in(alice)

    client.patch(f"/api/connections/{cid}/permissions", json={"permissions": {"email.send": True}})
    client.patch(f"/api/connections/{cid}/permissions", json={"permissions": {"email.send": True}})
    client.patch(f"/api/connections/{cid}/permissions", json={"permissions": {"email.send": False}})

    assert events(db, alice) == [
        ("user", "permission.on", "email.send", None),
        ("user", "permission.off", "email.send", None),
    ]


def test_rename_is_logged_with_old_and_new_name(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")

    logged_in(alice).patch(f"/api/connections/{cid}", json={"display_name": "Office"})

    assert events(db, alice) == [("user", "account.renamed", None, '"Work" → "Office"')]
    entry = db.query(AuditLog).one()
    assert (entry.account_name, entry.account_target) == ("Office", "work@example.com")


def test_login_edit_logs_what_changed_but_never_the_password(db, fake_mail_servers):
    alice = make_user("alice")
    cid = make_connection(alice, "Work", status="needs_reauth")

    logged_in(alice).put(
        f"/api/connections/{cid}/imap", json={**EDIT_IMAP, "password": "hunter2-new"}
    )

    [(actor, action, _, detail)] = events(db, alice)
    assert (actor, action) == ("user", "account.login_updated")
    assert "SMTP host imap.example.com → smtp.example.com" in detail
    assert "password replaced" in detail and "works again" in detail
    assert "hunter2" not in detail and "secret" not in detail


def test_saving_an_unchanged_login_logs_nothing(db, fake_mail_servers):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")
    same = {**EDIT_IMAP, "smtp_host": "imap.example.com"}

    logged_in(alice).put(f"/api/connections/{cid}/imap", json=same)

    assert events(db, alice) == []


def test_disconnect_is_logged_and_the_entry_outlives_the_account(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")

    assert logged_in(alice).delete(f"/api/connections/{cid}").status_code == 204

    db.expire_all()
    entry = db.query(AuditLog).one()
    assert entry.action == "account.disconnected"
    assert (entry.connection_id, entry.account_name, entry.account_target) == (None, "Work", "work@example.com")


def test_connecting_an_account_is_logged(db, fake_mail_servers):
    from tests.test_api import NEW_ACCOUNT

    alice = make_user("alice")
    res = logged_in(alice).post("/api/connections/imap", json=NEW_ACCOUNT)

    db.expire_all()
    entry = db.query(AuditLog).one()
    assert (entry.action, entry.connection_id, entry.account_target) == (
        "account.connected", res.json()["id"], "me@example.com"
    )


def test_a_refused_login_is_logged_once_by_zuimcp(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")

    assert mark_login_refused(cid, "Refused during an AI call to email_read") is True
    assert mark_login_refused(cid, "again") is False  # already flagged: no second entry

    assert events(db, alice) == [
        ("system", "account.login_refused", None, "Refused during an AI call to email_read")
    ]


# --- pause / resume --------------------------------------------------------------


def test_pause_and_resume_are_logged(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")
    client = logged_in(alice)

    assert client.patch(f"/api/connections/{cid}", json={"status": "disabled"}).json()["status"] == "disabled"
    assert client.patch(f"/api/connections/{cid}", json={"status": "active"}).json()["status"] == "active"

    assert [e[1] for e in events(db, alice)] == ["account.paused", "account.resumed"]


@pytest.mark.parametrize("status", ["needs_reauth", "deleted", ""])
def test_only_pause_and_resume_can_be_set(db, status):
    alice = make_user("alice")
    cid = make_connection(alice, "Work")

    assert logged_in(alice).patch(f"/api/connections/{cid}", json={"status": status}).status_code == 400


def test_resume_cannot_hide_a_refused_login(db):
    alice = make_user("alice")
    cid = make_connection(alice, "Work", status="needs_reauth")

    res = logged_in(alice).patch(f"/api/connections/{cid}", json={"status": "active"})

    assert res.status_code == 400
    db.expire_all()
    assert db.get(Connection, cid).status == "needs_reauth"


def test_paused_account_is_refused_by_name_and_skipped_otherwise(db):
    alice = make_user("alice")
    make_connection(alice, "Work", granted=("email.read",), status="disabled")
    make_connection(alice, "Home", granted=("email.read",))

    with pytest.raises(ValueError, match="'Work' is paused"):
        resolve_account(alice, "email", "Work", "email.read")
    # With Work paused, Home is the only account: no need to name it.
    assert resolve_account(alice, "email", None, "email.read").name == "Home"


def test_all_accounts_paused_says_so(db):
    alice = make_user("alice")
    make_connection(alice, "Work", granted=("email.read",), status="disabled")

    with pytest.raises(ValueError, match="paused"):
        resolve_account(alice, "email", None, "email.read")


# --- the log API -------------------------------------------------------------------


def test_pages_and_filters(db):
    alice, bob = make_user("alice"), make_user("bob")
    cid = make_connection(alice, "Work")
    db.add_all(
        [AuditLog(user_id=alice, tool_name="email_list", status="success", connection_id=cid) for _ in range(3)]
        + [
            AuditLog(user_id=alice, tool_name="email_send", status="error", connection_id=cid),
            AuditLog(user_id=alice, actor="user", action="account.paused", status="success", connection_id=cid),
            AuditLog(user_id=bob, tool_name="email_read", status="error"),
        ]
    )
    db.commit()
    client = logged_in(alice)

    first = client.get("/api/audit-log?limit=2").json()
    assert [e["action"] or e["tool_name"] for e in first] == ["account.paused", "email_send"]
    rest = client.get(f"/api/audit-log?limit=10&before_id={first[-1]['id']}").json()
    assert [e["tool_name"] for e in rest] == ["email_list"] * 3

    assert [e["tool_name"] for e in client.get("/api/audit-log?errors_only=true").json()] == ["email_send"]
    assert [e["action"] for e in client.get("/api/audit-log?actor=user").json()] == ["account.paused"]
    assert client.get("/api/audit-log?actor=robot").status_code == 422


def test_agent_call_records_the_target_it_touched(db, monkeypatch):
    from tests.test_middleware import call_tool

    alice = make_user("alice")
    make_connection(alice, "Work", granted=("email.read",))

    async def tool(context):
        resolve_account(alice, "email", "Work", "email.read")
        return "ok"

    call_tool("email_read", tool, user_id=alice, monkeypatch=monkeypatch)

    db.expire_all()
    entry = db.query(AuditLog).one()
    assert (entry.actor, entry.account_name, entry.account_target, entry.inferred) == (
        "agent", "Work", "work@example.com", False
    )
    assert json.dumps(entry.detail) == "null"
