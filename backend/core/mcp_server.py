"""The single ZuiMCP server. Every service registers its tools on `mcp`, so an
agent connects to one URL (MCP_PATH) and sees all the tools its user allows."""

from fastmcp import Context, FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from mcp.types import Icon, ToolAnnotations

from core.accounts import list_user_accounts
from core.config import config
from core.mcp_auth import get_user_id
from core.middleware import (
    TOOL_CAPABILITY,
    AuditLoggingMiddleware,
    LoginRefusedMiddleware,
    ToolFilterMiddleware,
)

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

# The dashboard's origin (Better Auth's issuer is <app origin>/auth). It serves
# the logo, which MCP clients may show next to the connector (serverInfo.icons).
APP_URL = BETTER_AUTH_ISSUER.removesuffix("/auth")

mcp = FastMCP(
    "ZuiMCP",
    auth=auth,
    website_url=APP_URL,
    icons=[
        Icon(src=f"{APP_URL}/icon-512.png", mimeType="image/png", sizes=["512x512"]),
        Icon(src=f"{APP_URL}/favicon-32.png", mimeType="image/png", sizes=["32x32"]),
    ],
    instructions=(
        "Tools are named <service>_<action> (email_send, ...). A user can connect "
        "several accounts per service: call list_accounts to see them. When a user "
        "has more than one account for a service, pass `account` on every call — "
        "it is never guessed, so ask the user if it isn't clear which one they mean. "
        "list_accounts also shows what each account allows. A tool the user hasn't "
        "allowed on any account is refused; tell the user they can turn it on in the "
        "ZuiMCP dashboard, then retry — no need to reconnect."
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
# Inside the audit log, so the log records the clear message it raises.
mcp.add_middleware(LoginRefusedMiddleware())
if config.get("MCP_HIDE_UNGRANTED_TOOLS", False):
    mcp.add_middleware(ToolFilterMiddleware())


# MCP tool hints, by capability. Clients use them to group tools (Claude shows
# "Read-only" and "Write/delete" sections, each with its own allow/ask setting)
# and to decide when to ask the user first. Hints only: permissions are still
# enforced by resolve_account(). destructive = can't be undone or overwrites data.
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
CAPABILITY_HINTS: dict[str, ToolAnnotations] = {
    "email.read": _READ_ONLY,
    "email.search": _READ_ONLY,
    "email.draft": ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
    "email.mark_seen": ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
    "email.send": ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True),
    "odoo.read": _READ_ONLY,
    "odoo.create": ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
    "odoo.write": ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
    "odoo.delete": ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
}


def service_tool(capability: str | None):
    """Registers an MCP tool together with the capability it needs.

    The capability drives the audit log and the tool's hints (CAPABILITY_HINTS).
    The tool itself must still pass the same capability to resolve_account(),
    which is what actually enforces it.
    """
    if capability is not None and capability not in CAPABILITY_HINTS:
        raise ValueError(f"No tool hints defined for capability '{capability}'")

    def decorator(fn):
        TOOL_CAPABILITY[fn.__name__] = capability
        hints = CAPABILITY_HINTS[capability] if capability else ToolAnnotations(read_only_hint=True, open_world_hint=False)
        return mcp.tool(annotations=hints)(fn)

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
