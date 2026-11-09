import asyncio
from types import SimpleNamespace

import pytest
from fastmcp.server.middleware.rate_limiting import RateLimitError

from core import middleware
from core.middleware import AuditLoggingMiddleware, ToolFilterMiddleware
import services.email.service  # noqa: F401  registers tools and their capabilities
from models import AuditLog
from tests.conftest import make_connection, make_user


def call_tool(tool_name, call_next, user_id="alice", monkeypatch=None):
    monkeypatch.setattr(middleware, "get_user_id", lambda ctx: user_id)
    context = SimpleNamespace(message=SimpleNamespace(name=tool_name), fastmcp_context=None)
    return asyncio.run(AuditLoggingMiddleware().on_call_tool(context, call_next))


def test_records_successful_call(db, monkeypatch):
    make_user("alice")

    async def ok(context):
        return "sent"

    assert call_tool("email_send", ok, monkeypatch=monkeypatch) == "sent"

    entry = db.query(AuditLog).one()
    assert (entry.tool_name, entry.capability, entry.status) == ("email_send", "email.send", "success")


def test_records_error_and_reraises(db, monkeypatch):
    make_user("alice")

    async def boom(context):
        raise ValueError("No active email accounts")

    with pytest.raises(ValueError):
        call_tool("email_list", boom, monkeypatch=monkeypatch)

    entry = db.query(AuditLog).one()
    assert entry.status == "error"
    assert "No active email accounts" in entry.detail


def test_records_rate_limited_call(db, monkeypatch):
    make_user("alice")

    async def limited(context):
        raise RateLimitError()

    with pytest.raises(RateLimitError):
        call_tool("email_list", limited, monkeypatch=monkeypatch)

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

    assert call_tool("email_send", ok, monkeypatch=monkeypatch) == "sent"


def test_anonymous_call_is_not_recorded(db, monkeypatch):
    async def ok(context):
        return "ok"

    call_tool("email_list_folders", ok, user_id=None, monkeypatch=monkeypatch)

    assert db.query(AuditLog).count() == 0


# --- per-user tool list ----------------------------------------------------


def visible_tools(user_id, monkeypatch):
    monkeypatch.setattr(middleware, "get_user_id", lambda ctx: user_id)
    all_tools = [SimpleNamespace(name=n) for n in middleware.TOOL_CAPABILITY]

    async def everything(context):
        return all_tools

    context = SimpleNamespace(fastmcp_context=None)
    tools = asyncio.run(ToolFilterMiddleware().on_list_tools(context, everything))
    return {t.name for t in tools}


def test_tool_list_shows_only_granted_capabilities(monkeypatch):
    user = make_user("alice")
    make_connection(user, granted=("email.read",))

    tools = visible_tools(user, monkeypatch)

    assert {"list_accounts", "email_list", "email_read", "email_list_folders"} <= tools
    assert "email_send" not in tools
    assert "email_search" not in tools


def test_tool_list_for_user_without_accounts_is_just_list_accounts(monkeypatch):
    user = make_user("alice")

    assert visible_tools(user, monkeypatch) == {"list_accounts"}


def test_disabled_account_grants_nothing(monkeypatch):
    user = make_user("alice")
    make_connection(user, granted=("email.send",), status="disabled")

    assert "email_send" not in visible_tools(user, monkeypatch)
