from fastmcp import Context


def get_user_id(ctx: Context) -> str | None:
    """Extract the Better Auth user id (JWT `sub` claim) from the request context.

    Shared by MCP tools (services/*/service.py) and core/middleware.py, which
    both need to identify the caller from the same OAuth-verified context.
    """
    if hasattr(ctx, "request_context") and ctx.request_context and hasattr(ctx.request_context, "auth") and ctx.request_context.auth:
        return ctx.request_context.auth.claims.get("sub")
    return None
