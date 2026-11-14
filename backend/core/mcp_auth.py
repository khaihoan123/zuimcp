from fastmcp import Context
from fastmcp.server.dependencies import get_access_token

# True only when the server was started as `python main.py stdio`: one local
# user, whose mailbox comes from .env. Over HTTP this stays False, so a missing
# user can never fall back to that mailbox.
STDIO_MODE = False


def enable_stdio_mode() -> None:
    global STDIO_MODE
    STDIO_MODE = True


def get_user_id(ctx: Context | None = None) -> str | None:
    """The Better Auth user id (JWT `sub` claim) of the verified access token.

    Shared by MCP tools (services/*/service.py) and core/middleware.py, which
    both need to identify the caller from the same OAuth-verified token.

    Returns None only in local stdio mode. Over HTTP, a call without a verified
    token raises instead: callers treat None as "the single local user", so it
    must never mean "we couldn't tell who this is".

    `ctx` is unused; FastMCP looks the token up from the current request.
    """
    token = get_access_token()
    user_id = token.claims.get("sub") if token is not None else None
    if user_id:
        return user_id
    if STDIO_MODE:
        return None
    raise PermissionError("Not signed in: this request has no verified ZuiMCP access token")
