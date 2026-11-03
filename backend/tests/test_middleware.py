import asyncio
from types import SimpleNamespace

import pytest
from fastmcp.server.middleware.rate_limiting import RateLimitError

from core import middleware
from core.middleware import AuditLoggingMiddleware
from models import AuditLog
from tests.conftest import make_user


def call_tool(tool_name, call_next, user_id="alice", monkeypatch=None):
    monkeypatch.setattr(middleware, "get_user_id", lambda ctx: user_id)
    context = SimpleNamespace(message=SimpleNamespace(name=tool_name), fastmcp_context=None)
    return asyncio.run(AuditLoggingMiddleware().on_call_tool(context, call_next))


def test_records_successful_call(db, monkeypatch):
    make_user("alice")

    async def ok(context):
        return "sent"

    assert call_tool("send_email", ok, monkeypatch=monkeypatch) == "sent"

    entry = db.query(AuditLog).one()
    assert (entry.tool_name, entry.capability, entry.status) == ("send_email", "email.send", "success")


def test_records_error_and_reraises(db, monkeypatch):
    make_user("alice")

    async def boom(context):
        raise ValueError("No connected email accounts")

    with pytest.raises(ValueError):
        call_tool("list_emails", boom, monkeypatch=monkeypatch)

    entry = db.query(AuditLog).one()
    assert entry.status == "error"
    assert "No connected email accounts" in entry.detail


def test_records_rate_limited_call(db, monkeypatch):
    make_user("alice")

    async def limited(context):
        raise RateLimitError()

    with pytest.raises(RateLimitError):
        call_tool("list_emails", limited, monkeypatch=monkeypatch)

    assert db.query(AuditLog).one().status == "rate_limited"


def test_audit_failure_never_changes_the_tool_result(monkeypatch):
    # Regression: an exception while writing the audit row used to replace the
    # tool's result, so an email that was already sent looked failed and the
    # agent could send it again.
    def broken_record(self, *args):
        raise RuntimeError("database is down")

    monkeypatch.setattr(AuditLoggingMiddleware, "_record", broken_record)

    async def ok(context):
        return "sent"

    assert call_tool("send_email", ok, monkeypatch=monkeypatch) == "sent"


def test_anonymous_call_is_not_recorded(db, monkeypatch):
    async def ok(context):
        return "ok"

    call_tool("list_folders", ok, user_id=None, monkeypatch=monkeypatch)

    assert db.query(AuditLog).count() == 0
