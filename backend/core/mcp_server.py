"""The single ZuiMCP server. Every service registers its tools on `mcp`, so an
agent connects to one URL (MCP_PATH) and sees all the tools its user allows."""

from fastmcp import Context, FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware

from core.accounts import list_user_accounts
from core.config import config
from core.mcp_auth import get_user_id
from core.middleware import TOOL_CAPABILITY, AuditLoggingMiddleware, ToolFilterMiddleware

MCP_PATH = "/mcp"
MCP_RESOURCE_URL = config.get("MCP_RESOURCE_URL", "http://localhost:8000")
BETTER_AUTH_ISSUER = config.get("AUTH_ISSUER")

# The full protected-resource identifier (RFC 8707/9728) — what an MCP
# client sends as its "resource" param and gets back as the token's `aud`
# claim. Must exactly equal validAudiences in frontend/src/lib/server/auth.ts,
# or Better Auth rejects the token request ("requested resource invalid") —
# or worse, silently issues an opaque non-JWT access_token instead.
RESOURCE_INDICATOR = MCP_RESOURCE_URL.rstrip("/") + MCP_PATH

token_verifier = JWTVerifier(
    jwks_uri=config.get("AUTH_JWKS_URI"),
    issuer=BETTER_AUTH_ISSUER,
    audience=RESOURCE_INDICATOR,
    algorithm="RS256",
)

auth = RemoteAuthProvider(
    token_verifier=token_verifier,
    authorization_servers=[BETTER_AUTH_ISSUER],
    base_url=MCP_RESOURCE_URL,
)

mcp = FastMCP(
    "ZuiMCP",
    auth=auth,
    instructions=(
        "Tools are named <service>_<action> (email_send, ...). A user can connect "
        "several accounts per service: call list_accounts to see them. When a user "
        "has more than one account for a service, pass `account` on every call — "
        "it is never guessed, so ask the user if it isn't clear which one they mean."
    ),
)

# Added first so it's outermost and also observes/logs RateLimitError raised by
# the rate limiter below (FastMCP.add_middleware appends; first added = outermost).
mcp.add_middleware(AuditLoggingMiddleware())
mcp.add_middleware(
    SlidingWindowRateLimitingMiddleware(
        max_requests=config.get("MCP_RATE_LIMIT_PER_MINUTE", 60),
        window_minutes=1,
        get_client_id=lambda ctx: get_user_id(ctx.fastmcp_context) or "anonymous",
    )
)
mcp.add_middleware(ToolFilterMiddleware())


def service_tool(capability: str | None):
    """Registers an MCP tool together with the capability it needs.

    The capability drives the per-user tool list and the audit log. The tool
    itself must still pass the same capability to resolve_account(), which is
    what actually enforces it.
    """

    def decorator(fn):
        TOOL_CAPABILITY[fn.__name__] = capability
        return mcp.tool()(fn)

    return decorator


@service_tool(capability=None)
def list_accounts(ctx: Context) -> list[dict]:
    """List the accounts this user connected (mailboxes, ...), with what each one allows.

    Use the `account` value (or the name) as the `account` argument of other tools.
    `allowed` lists the capabilities the user granted on that account, e.g.
    email.read, email.send. Anything not listed will be refused.
    """
    user_id = get_user_id(ctx)
    if not user_id:
        return [{"account": "local", "name": "Local mailbox (stdio mode)", "service": "email"}]
    return list_user_accounts(user_id)
