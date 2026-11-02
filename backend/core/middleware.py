import logging

import anyio
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.rate_limiting import RateLimitError

from core.db import SessionLocal
from core.mcp_auth import get_user_id
from models import AuditLog

logger = logging.getLogger(__name__)

# Static per-tool capability mapping (mirrors the capability= argument each tool
# passes to get_mailbox()/get_connected_email() in services/email/service.py) —
# the middleware only sees the tool name and arguments, not which capability the
# tool internally checked, so this has to be kept in sync by hand if a tool's
# capability changes.
TOOL_CAPABILITY = {
    "list_folders": "email.read",
    "list_emails": "email.read",
    "read_email": "email.read",
    "get_attachment": "email.read",
    "search_emails": "email.search",
    "draft_email": "email.draft",
    "send_email": "email.send",
    "mark_emails_seen": "email.mark_seen",
}


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
                # would replace the tool's real result (e.g. a send_email that
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
