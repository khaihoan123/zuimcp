from fastapi import FastAPI
import uvicorn
from fastmcp import FastMCP, Context
from fastmcp.server.auth.providers.jwt import JWTVerifier
from imap_tools import MailBox, AND, MailMessageFlags
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re
import datetime
import base64
from dotenv import load_dotenv
import yaml

# 1. Load local env vars (only matters for local testing)
load_dotenv()
LOCAL_EMAIL = os.environ.get("EMAIL_ADDRESS")
LOCAL_PASS = os.environ.get("EMAIL_PASSWORD")

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
with open(config_path, "r") as f:
    config = yaml.safe_load(f) or {}

auth = JWTVerifier(
    jwks_uri=config.get("AUTH_JWKS_URI", "https://placeholder/.well-known/jwks.json"),
    issuer=config.get("AUTH_ISSUER", "placeholder"),
    audience="mcp-imap-server"
)
    
mcp = FastMCP("IMAP_Connector", auth=auth)

def _get_user_id(ctx: Context) -> str | None:
    """Extract user_id from the FastMCP request context if auth is enabled."""
    if hasattr(ctx, "request_context") and ctx.request_context and hasattr(ctx.request_context, "auth") and ctx.request_context.auth:
        return ctx.request_context.auth.subject
    return None


def get_mailbox(user_id: str = None) -> MailBox:
    """Connects via DB if user_id is provided, otherwise falls back to local .env"""
    imap_host = config.get("IMAP_HOST")
    imap_port = config.get("IMAP_PORT", 993)

    if user_id:
        # REMOTE SAAS MODE: Fetch this specific user's credentials from PostgreSQL
        # user_creds = db.execute("SELECT email, password FROM users WHERE id = %s", (user_id,))
        # email = user_creds.email
        # password = user_creds.decrypted_password
        raise NotImplementedError("SAAS database integration is pending.")
    else:
        # LOCAL DEV MODE: Use your personal credentials
        if not LOCAL_EMAIL or not LOCAL_PASS:
            raise ValueError("Local credentials missing in .env")
        email = LOCAL_EMAIL
        password = LOCAL_PASS
        
    return MailBox(imap_host, imap_port).login(email, password)

def get_smtp_client(user_id: str = None) -> smtplib.SMTP:
    smtp_host = config.get("SMTP_HOST")
    smtp_port = config.get("SMTP_PORT", 587)

    if user_id:
        # email = user_creds.email
        # password = user_creds.decrypted_password
        raise NotImplementedError("SAAS database integration is pending.")
    else:
        email = LOCAL_EMAIL
        password = LOCAL_PASS
        
    client = smtplib.SMTP(smtp_host, smtp_port)
    client.starttls()
    client.login(email, password)
    return client

    
def _snippet(msg, length: int = 200) -> str:
    """Plain-text preview of a message body, HTML stripped and whitespace collapsed."""
    text = msg.text or msg.html or ""
    text = re.sub(r"<[^>]+>", " ", text)      # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text[:length]


def _fetch_messages(user_id: str | None, folder: str, criteria, limit=20, offset=0,
                    with_snippet=False) -> list[dict]:
    """Shared fetch used by list_emails and search_emails.

    Returns newest-first. Uses UID search to get all matching UIDs, 
    applies offset and limit to the UIDs, and then fetches only the 
    needed messages to avoid downloading unnecessary data.
    """
    results = []
    with get_mailbox(user_id) as mailbox:
        mailbox.folder.set(folder)
        
        criteria_str = str(criteria)
        if criteria_str == "(ALL)":
            # O(1) optimization for unfiltered lists: use sequence numbers directly
            total = mailbox.folder.status(folder)["MESSAGES"]
            end_seq = total - offset
            start_seq = max(1, end_seq - limit + 1)
            
            if end_seq < 1:
                selected_uids = []
            else:
                uids = mailbox.uids(f"{start_seq}:{end_seq}")
                uids.reverse()  # newest-first
                selected_uids = uids
        else:
            # For filtered searches, IMAP must evaluate everything and return all UIDs
            uids = mailbox.uids(criteria)
            uids.reverse()  # newest-first
            selected_uids = uids[offset : offset + limit]
        
        if not selected_uids:
            return []
            
        # 3. Fetch only the selected UIDs
        uid_criteria = AND(uid=",".join(selected_uids))
        fetched = mailbox.fetch(
            uid_criteria,
            reverse=True,        # newest first
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
                          with_snippet=with_snippet)


@mcp.tool()
def read_email(ctx: Context, uid: str, folder: str = "INBOX") -> dict:
    """Read a single email by UID. Returns full headers, plain text body, and attachment names."""
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id) as mailbox:
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


import json
from mcp.types import ImageContent, TextContent

@mcp.tool()
def get_attachment(ctx: Context, uid: str, folder: str = "INBOX", index: int = -1, content_id: str = ""):
    """Get an attachment (or all attachments) from an email.
    Pass `content_id` or `index` to select one attachment.
    Pass neither to return ALL attachments at once.
    Images are returned natively so the AI can see them. Non-images return base64 text.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id) as mailbox:
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
def list_folders(ctx: Context) -> list[str]:
    """List all available IMAP folders (e.g., INBOX, INBOX.Drafts, etc.). Use this to find the exact folder name to use for other tools."""
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id) as mailbox:
        return [f.name for f in mailbox.folder.list()]


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
                          with_snippet=with_snippet)


def _resolve_drafts_folder(mailbox) -> str:
    """Find the Drafts folder via its \\Drafts special-use flag, falling back
    to a name match, then to INBOX.Drafts."""
    fallback = None
    for f in mailbox.folder.list():
        flags = [fl.lower() for fl in f.flags]
        if r"\drafts" in flags:        # special-use flag — most reliable
            return f.name
        if "draft" in f.name.lower():  # name-based fallback
            fallback = f.name
    return fallback or "INBOX.Drafts"


@mcp.tool()
def draft_email(ctx: Context, to: str, subject: str, body: str, cc: str = "") -> dict:
    """Save a draft email to the Drafts folder. SMTP submission is skipped — draft is appended to the Drafts IMAP folder."""
    import email.utils

    user_id = _get_user_id(ctx)
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = LOCAL_EMAIL
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    with get_mailbox(user_id) as mailbox:
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
def mark_emails_seen(ctx: Context, uids: list[str], folder: str = "INBOX", seen: bool = True) -> dict:
    """Mark multiple emails as read (seen) or unread.

    Args:
        uids: A list of email UIDs to modify.
        folder: The IMAP folder where the emails are located (default: INBOX).
        seen: True to mark as read, False to mark as unread.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id) as mailbox:
        mailbox.folder.set(folder)
        if uids:
            mailbox.flag(uids, MailMessageFlags.SEEN, seen)
        
    return {
        "status": "success",
        "uids": uids,
        "folder": folder,
        "seen": seen
    }


app = FastAPI()

# 2. Add the OAuth discovery route so Claude knows where to log in
@app.get("/.well-known/oauth-protected-resource")
def oauth_discovery():
    return {
        # Make sure to replace this with your actual Supabase URL
        "authorization_server": "https://abcdefgh123456.supabase.co" 
    }

# 3. Mount the MCP server's SSE app into the FastAPI application at the /mcp path
app.mount("/mcp", mcp.http_app())

import sys

def main():
    # If "stdio" is passed as an argument, run the MCP stdio server
    if len(sys.argv) > 1 and sys.argv[1] == "stdio":
        # Remove the argument so FastMCP doesn't get confused parsing it
        sys.argv.pop(1)
        mcp.run()
    else:
        # Otherwise, run the combined FastAPI and MCP application using Uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()