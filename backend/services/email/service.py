import base64
import datetime
import email.utils
import json
import re
from email.mime.text import MIMEText

from fastmcp import Context, FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from imap_tools import AND, MailMessageFlags
from mcp.types import ImageContent, TextContent

from core.config import config
from core.mcp_auth import get_user_id as _get_user_id
from core.middleware import AuditLoggingMiddleware
from services.email.providers.imap import get_mailbox, get_connected_email
from services.email.providers.smtp import send_message

MCP_PATH = "/mcp/email"
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
mcp = FastMCP("mail_mcp", auth=auth)

# Added first so it's outermost and also observes/logs RateLimitError raised by
# the rate limiter below (FastMCP.add_middleware appends; first added = outermost).
mcp.add_middleware(AuditLoggingMiddleware())
mcp.add_middleware(
    SlidingWindowRateLimitingMiddleware(
        max_requests=config.get("MCP_RATE_LIMIT_PER_MINUTE", 60),
        window_minutes=1,
        get_client_id=lambda ctx: _get_user_id(ctx.fastmcp_context) or "anonymous",
    )
)


def _snippet(msg, length: int = 200) -> str:
    """Plain-text preview of a message body, HTML stripped and whitespace collapsed."""
    text = msg.text or msg.html or ""
    text = re.sub(r"<[^>]+>", " ", text)      # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text[:length]

def _fetch_messages(user_id: str | None, folder: str, criteria, limit=20, offset=0,
                    with_snippet=False, *, capability: str) -> list[dict]:
    """Shared fetch used by list_emails and search_emails.

    Returns newest-first. Uses UID search to get all matching UIDs,
    applies offset and limit to the UIDs, and then fetches only the
    needed messages to avoid downloading unnecessary data.

    `capability` differs per caller (list vs search), so it has no default —
    forgetting it should be an error, not a silently ungated read.
    """
    results = []
    with get_mailbox(user_id, capability=capability) as mailbox:
        mailbox.folder.set(folder)
        
        criteria_str = str(criteria)
        if criteria_str == "(ALL)":
            total = mailbox.folder.status(folder)["MESSAGES"]
            end_seq = total - offset
            start_seq = max(1, end_seq - limit + 1)
            
            if end_seq < 1:
                selected_uids = []
            else:
                uids = mailbox.uids(f"{start_seq}:{end_seq}")
                uids.reverse()
                selected_uids = uids
        else:
            uids = mailbox.uids(criteria)
            uids.reverse()
            selected_uids = uids[offset : offset + limit]
        
        if not selected_uids:
            return []
            
        uid_criteria = AND(uid=",".join(selected_uids))
        fetched = mailbox.fetch(
            uid_criteria,
            reverse=True,
            mark_seen=False,
            bulk=True,
        )
        for msg in fetched:
            item = {
                "uid": msg.uid,
                "from": msg.from_,
                "subject": msg.subject,
                "date": msg.date.isoformat() if msg.date else None,
                "seen": MailMessageFlags.SEEN in msg.flags,
            }
            if with_snippet:
                item["snippet"] = _snippet(msg)
            results.append(item)
    return results

def _resolve_drafts_folder(mailbox) -> str:
    """Find the Drafts folder via its \\Drafts special-use flag, falling back
    to a name match, then to INBOX.Drafts."""
    fallback = None
    for f in mailbox.folder.list():
        flags = [fl.lower() for fl in f.flags]
        if r"\drafts" in flags:
            return f.name
        if "draft" in f.name.lower():
            fallback = f.name
    return fallback or "INBOX.Drafts"



@mcp.tool()
def list_folders(ctx: Context) -> list[str]:
    """List all available IMAP folders (e.g., INBOX, INBOX.Drafts, etc.). Use this to find the exact folder name to use for other tools."""
    user_id = _get_user_id(ctx)
    # Gated on email.read: folder names are user data (clients, projects),
    # not neutral metadata, and listing them is only useful before a read.
    with get_mailbox(user_id, capability="email.read") as mailbox:
        return [f.name for f in mailbox.folder.list()]

@mcp.tool()
def list_emails(ctx: Context, folder: str = "INBOX", limit: int = 20, offset: int = 0,
                unseen_only: bool = False, with_snippet: bool = False,
                since: str = "", before: str = "") -> list[dict]:
    """List emails from a folder, newest first.

    Args:
        folder: IMAP folder name (see list_folders).
        limit: max messages to return.
        offset: skip this many of the newest messages, for paging past `limit`
            (e.g. limit=20, offset=20 returns the next 20).
        unseen_only: only unread messages.
        with_snippet: include a ~200 char plain-text body preview. Costs a full
            body fetch per message, so leave off for plain listing and turn on
            only when triaging content.
        since: only messages on/after this date, 'YYYY-MM-DD'.
        before: only messages strictly before this date, 'YYYY-MM-DD'.

    Returns sender, subject, date, uid, seen status, and (optionally) a snippet.
    """
    user_id = _get_user_id(ctx)
    conditions = {}
    if unseen_only:
        conditions["seen"] = False
    if since:
        conditions["date_gte"] = datetime.date.fromisoformat(since)
    if before:
        conditions["date_lt"] = datetime.date.fromisoformat(before)
    criteria = AND(**conditions) if conditions else AND(all=True)

    return _fetch_messages(user_id, folder, criteria, limit=limit, offset=offset,
                          with_snippet=with_snippet, capability="email.read")

@mcp.tool()
def read_email(ctx: Context, uid: str, folder: str = "INBOX") -> dict:
    """Read a single email by UID. Returns full headers, plain text body, and attachment names."""
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id, capability="email.read") as mailbox:
        mailbox.folder.set(folder)
        msgs = list(mailbox.fetch(AND(uid=uid), mark_seen=False))
        if not msgs:
            return {"error": f"No email found with uid={uid} in {folder}"}
        msg = msgs[0]
        return {
            "uid": msg.uid,
            "from": msg.from_,
            "to": msg.to,
            "cc": msg.cc,
            "subject": msg.subject,
            "date": msg.date.isoformat() if msg.date else None,
            "body_text": msg.text,
            "body_html": msg.html,
            "attachments": [
                {
                    "filename": a.filename,
                    "content_id": a.content_id,
                    "content_type": a.content_type,
                    "size": a.size
                }
                for a in msg.attachments
            ],
        }

@mcp.tool()
def get_attachment(ctx: Context, uid: str, folder: str = "INBOX", index: int = -1, content_id: str = ""):
    """Get an attachment (or all attachments) from an email.
    Pass `content_id` or `index` to select one attachment.
    Pass neither to return ALL attachments at once.
    Images are returned natively so the AI can see them. Non-images return base64 text.
    """
    user_id = _get_user_id(ctx)
    # No separate attachments capability exists yet, so this rides on read.
    with get_mailbox(user_id, capability="email.read") as mailbox:
        mailbox.folder.set(folder)
        msgs = list(mailbox.fetch(AND(uid=uid), mark_seen=False))
        if not msgs:
            return {"error": f"No email found with uid={uid} in {folder}"}

        msg = msgs[0]
        if not msg.attachments:
            return {"error": "Email has no attachments"}

        target = None
        if content_id:
            for a in msg.attachments:
                if a.content_id == content_id:
                    target = a
                    break
        elif index >= 0 and index < len(msg.attachments):
            target = msg.attachments[index]

        if not target and (index >= 0 or content_id):
            return {"error": f"Attachment not found (index={index}, content_id={content_id})"}

        targets = [target] if target else msg.attachments
        results = []

        for t in targets:
            ctype = (t.content_type or "").lower()
            if ctype.startswith("image/"):
                results.append(ImageContent(
                    type="image",
                    data=base64.b64encode(t.payload).decode("utf-8"),
                    mimeType=ctype
                ))
            else:
                meta = {
                    "filename": t.filename,
                    "content_type": t.content_type,
                    "content_id": t.content_id,
                    "size": len(t.payload),
                    "data_base64": base64.b64encode(t.payload).decode("utf-8"),
                    "warning": "DO NOT print this base64 data or write it using bash commands."
                }
                results.append(TextContent(type="text", text=json.dumps(meta)))

        return results if len(results) > 1 or not target else results[0]

@mcp.tool()
def search_emails(ctx: Context, query: str = "", subject: str = "", sender: str = "",
                  folder: str = "INBOX", limit: int = 20, offset: int = 0,
                  unseen_only: bool = False, with_snippet: bool = False,
                  since: str = "", before: str = "") -> list[dict]:
    """Search emails by free text, subject, or sender, newest first.

    Args:
        query: free-text search across the message.
        subject: match within the subject.
        sender: match the From address.
        folder, limit, offset, unseen_only, with_snippet, since, before:
            same meaning as in list_emails.

    Returns sender, subject, date, uid, seen status, and (optionally) a snippet.
    """
    user_id = _get_user_id(ctx)
    kwargs = {}
    if query:
        kwargs["text"] = query
    if subject:
        kwargs["subject"] = subject
    if sender:
        kwargs["from_"] = sender
    if unseen_only:
        kwargs["seen"] = False
    if since:
        kwargs["date_gte"] = datetime.date.fromisoformat(since)
    if before:
        kwargs["date_lt"] = datetime.date.fromisoformat(before)
    criteria = AND(**kwargs) if kwargs else AND(all=True)

    return _fetch_messages(user_id, folder, criteria, limit=limit, offset=offset,
                          with_snippet=with_snippet, capability="email.search")

@mcp.tool()
def draft_email(ctx: Context, to: str, subject: str, body: str, cc: str = "") -> dict:
    """Save a draft email to the Drafts folder. SMTP submission is skipped — draft is appended to the Drafts IMAP folder."""
    user_id = _get_user_id(ctx)
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = get_connected_email(user_id, capability="email.draft")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    with get_mailbox(user_id, capability="email.draft") as mailbox:
        drafts_folder = _resolve_drafts_folder(mailbox)
        mailbox.append(
            msg.as_bytes(),
            drafts_folder,
            dt=datetime.datetime.now(datetime.timezone.utc),
            flag_set=[MailMessageFlags.DRAFT],
        )

    return {
        "status": "draft_saved",
        "folder": drafts_folder,
        "to": to,
        "subject": subject,
    }

@mcp.tool()
def send_email(ctx: Context, to: str, subject: str, body: str, cc: str = "") -> dict:
    """Send an email immediately via SMTP. Requires the email.send capability
    on the connected account — unlike the other tools here, this is
    irreversible, so an account without that capability granted raises
    instead of silently falling back to one that has it."""
    user_id = _get_user_id(ctx)
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = get_connected_email(user_id, capability="email.send")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    send_message(user_id, msg)

    return {
        "status": "sent",
        "to": to,
        "subject": subject,
    }

@mcp.tool()
def mark_emails_seen(ctx: Context, uids: list[str], folder: str = "INBOX", seen: bool = True) -> dict:
    """Mark multiple emails as read (seen) or unread.

    Args:
        uids: A list of email UIDs to modify.
        folder: The IMAP folder where the emails are located (default: INBOX).
        seen: True to mark as read, False to mark as unread.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id, capability="email.mark_seen") as mailbox:
        mailbox.folder.set(folder)
        if uids:
            mailbox.flag(uids, MailMessageFlags.SEEN, seen)
        
    return {
        "status": "success",
        "uids": uids,
        "folder": folder,
        "seen": seen
    }