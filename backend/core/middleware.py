import logging
import re
import smtplib

import anyio
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitError
from imap_tools import MailboxLoginError

from core.accounts import granted_capabilities, track_account_used
from core.audit import mark_login_refused
from core.db import SessionLocal
from core.mcp_auth import get_user_id
from models import AuditLog
from services.odoo.client import OdooAuthError

logger = logging.getLogger(__name__)

# Tool name -> the capability it needs (None = needs none, e.g. list_accounts).
# Filled by core.mcp_server.service_tool() as each tool is registered, so a
# tool's capability is declared once, next to the tool itself.
TOOL_CAPABILITY: dict[str, str | None] = {}


class ToolFilterMiddleware(Middleware):
    """Shows each user only the tools at least one of their accounts allows.

    Off by default (MCP_HIDE_UNGRANTED_TOOLS). If no account may send, the
    agent never sees email_send at all. This is about a clean tool list, not
    security: a hidden tool that still gets called is refused by
    core.accounts.resolve_account() anyway. MCP clients cache the list, so with
    this on, a permission change only shows up after the client reconnects.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        user_id = get_user_id(context.fastmcp_context)
        if not user_id:
            return tools  # local stdio mode: one user, every tool
        granted = await anyio.to_thread.run_sync(granted_capabilities, user_id)
        return [
            t for t in tools
            if TOOL_CAPABILITY.get(t.name) is None or TOOL_CAPABILITY[t.name] in granted
        ]


# FastMCP prefixes a tool's error with this; the log already shows the tool.
_TOOL_ERROR_PREFIX = re.compile(r"^Error calling tool '[^']*': ")


def clean_error(error: BaseException) -> str:
    return _TOOL_ERROR_PREFIX.sub("", str(error))[:1000]


class AuditLoggingMiddleware(Middleware):
    """Records one AuditLog row per MCP tool call.

    Must be added to the FastMCP server BEFORE any rate-limiting middleware
    (FastMCP.add_middleware appends, and the first middleware added ends up
    outermost) so it also observes and logs RateLimitError raised downstream.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name
        user_id = get_user_id(context.fastmcp_context)
        status, detail = "success", None
        with track_account_used() as used:
            try:
                return await call_next(context)
            except RateLimitError as e:
                status, detail = "rate_limited", str(e)
                raise
            except Exception as e:
                status, detail = "error", clean_error(e)
                raise
            finally:
                if user_id:
                    # Off the event loop, and never allowed to raise: an exception here
                    # would replace the tool's real result (e.g. an email_send that
                    # already went out would look failed, and the agent might resend).
                    try:
                        await anyio.to_thread.run_sync(
                            self._record, user_id, tool_name, status, detail, dict(used)
                        )
                    except Exception:
                        logger.exception("Failed to write audit log for tool %s", tool_name)

    def _record(
        self, user_id: str, tool_name: str, status: str, detail: str | None, account: dict
    ) -> None:
        db = SessionLocal()
        try:
            db.add(
                AuditLog(
                    user_id=user_id,
                    tool_name=tool_name,
                    capability=TOOL_CAPABILITY.get(tool_name),
                    actor="agent",
                    connection_id=account.get("id"),
                    account_name=account.get("name"),
                    service_type=account.get("service_type"),
                    account_target=account.get("target"),
                    status=status,
                    detail=detail,
                )
            )
            db.commit()
        finally:
            db.close()


# A provider refusing the SAVED login (not a network error or a missing
# permission): the account can't work until the user updates it. Gmail flags
# itself when Google refuses its refresh token (providers/gmail.py).
LOGIN_REFUSED = (MailboxLoginError, smtplib.SMTPAuthenticationError, OdooAuthError)


def _login_refused(error: BaseException | None) -> bool:
    seen = 0
    while error is not None and seen < 5:  # FastMCP may wrap the tool's error
        if isinstance(error, LOGIN_REFUSED):
            return True
        error, seen = error.__cause__ or error.__context__, seen + 1
    return False


class LoginRefusedMiddleware(Middleware):
    """Flags the account a tool call used when its provider refused the saved
    login (status 'needs_reauth'), so the dashboard shows a warning instead of
    "active", and tells the agent what the user must do."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        with track_account_used() as used:
            try:
                return await call_next(context)
            except Exception as e:
                if not (used.get("id") and _login_refused(e)):
                    raise
                await anyio.to_thread.run_sync(
                    mark_login_refused, used["id"], f"Refused during an AI call to {context.message.name}"
                )
                raise ValueError(
                    f"The server refused the saved login of the account '{used['name']}' "
                    f"(the password changed, or the API key expired or was removed). Tell the "
                    f"user to update its login in the ZuiMCP dashboard (Connected accounts), "
                    f"then retry; no need to reconnect this client."
                ) from None
