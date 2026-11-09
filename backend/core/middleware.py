import logging

import anyio
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitError

from core.accounts import granted_capabilities
from core.db import SessionLocal
from core.mcp_auth import get_user_id
from models import AuditLog

logger = logging.getLogger(__name__)

# Tool name -> the capability it needs (None = needs none, e.g. list_accounts).
# Filled by core.mcp_server.service_tool() as each tool is registered, so a
# tool's capability is declared once, next to the tool itself.
TOOL_CAPABILITY: dict[str, str | None] = {}


class ToolFilterMiddleware(Middleware):
    """Shows each user only the tools at least one of their accounts allows.

    If no account may send, the agent never sees email_send at all. This is
    about a clean tool list, not security: a hidden tool that still gets called
    is refused by core.accounts.resolve_account() anyway. MCP clients cache the
    list, so a permission change shows up after the client reconnects.
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
        try:
            return await call_next(context)
        except RateLimitError as e:
            status, detail = "rate_limited", str(e)
            raise
        except Exception as e:
            status, detail = "error", str(e)[:500]
            raise
        finally:
            if user_id:
                # Off the event loop, and never allowed to raise: an exception here
                # would replace the tool's real result (e.g. an email_send that
                # already went out would look failed, and the agent might resend).
                try:
                    await anyio.to_thread.run_sync(
                        self._record, user_id, tool_name, status, detail
                    )
                except Exception:
                    logger.exception("Failed to write audit log for tool %s", tool_name)

    def _record(self, user_id: str, tool_name: str, status: str, detail: str | None) -> None:
        db = SessionLocal()
        try:
            db.add(
                AuditLog(
                    user_id=user_id,
                    tool_name=tool_name,
                    capability=TOOL_CAPABILITY.get(tool_name),
                    status=status,
                    detail=detail,
                )
            )
            db.commit()
        finally:
            db.close()
