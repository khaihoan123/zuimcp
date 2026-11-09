"""Email tools (IMAP/SMTP today). Registered on the shared server in core.mcp_server."""

import base64
import datetime
import email.utils
import json
import re
from email.mime.text import MIMEText

from fastmcp import Context
from imap_tools import AND, MailMessageFlags
from mcp.types import ImageContent, TextContent

from core.mcp_auth import get_user_id as _get_user_id
from core.mcp_server import service_tool
from services.email.providers.imap import get_email_credentials, get_mailbox, open_mailbox
from services.email.providers.smtp import send_message


def _snippet(msg, length: int = 200) -> str:
    """Plain-text preview of a message body, HTML stripped and whitespace collapsed."""
    text = msg.text or msg.html or ""
    text = re.sub(r"<[^>]+>", " ", text)      # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text[:length]

def _fetch_messages(user_id: str | None, account: str, folder: str, criteria, limit=20, offset=0,
                    with_snippet=False, *, capability: str) -> list[dict]:
    """Shared fetch used by email_list and email_search.

    Returns newest-first. Uses UID search to get all matching UIDs,
    applies offset and limit to the UIDs, and then fetches only the
    needed messages to avoid downloading unnecessary data.

    `capability` differs per caller (list vs search), so it has no default —
    forgetting it should be an error, not a silently ungated read.
    """
    results = []
    with get_mailbox(user_id, account, capability) as mailbox:
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



# Gated on email.read: folder names are user data (clients, projects),
# not neutral metadata, and listing them is only useful before a read.
@service_tool(capability="email.read")
def email_list_folders(ctx: Context, account: str = "") -> list[str]:
    """List all folders of a mailbox (e.g. INBOX, INBOX.Drafts). Use this to find the exact folder name for other email tools.

    Args:
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id, account, "email.read") as mailbox:
        return [f.name for f in mailbox.folder.list()]

@service_tool(capability="email.read")
def email_list(ctx: Context, account: str = "", folder: str = "INBOX", limit: int = 20, offset: int = 0,
                unseen_only: bool = False, with_snippet: bool = False,
                since: str = "", before: str = "") -> list[dict]:
    """List emails from a folder, newest first.

    Args:
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
        folder: IMAP folder name (see email_list_folders).
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

    return _fetch_messages(user_id, account, folder, criteria, limit=limit, offset=offset,
                          with_snippet=with_snippet, capability="email.read")

@service_tool(capability="email.read")
def email_read(ctx: Context, uid: str, folder: str = "INBOX", account: str = "") -> dict:
    """Read a single email by UID. Returns full headers, plain text body, and attachment names.

    Args:
        uid: the email's uid, from email_list or email_search.
        folder: the folder it is in.
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id, account, "email.read") as mailbox:
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

# No separate attachments capability exists yet, so this rides on read.
@service_tool(capability="email.read")
def email_get_attachment(ctx: Context, uid: str, folder: str = "INBOX", index: int = -1,
                         content_id: str = "", account: str = ""):
    """Get an attachment (or all attachments) from an email.
    Pass `content_id` or `index` to select one attachment.
    Pass neither to return ALL attachments at once.
    Images are returned natively so the AI can see them. Non-images return base64 text.
    `account` works as in email_read.
    """
    user_id = _get_user_id(ctx)
    with get_mailbox(user_id, account, "email.read") as mailbox:
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

@service_tool(capability="email.search")
def email_search(ctx: Context, account: str = "", query: str = "", subject: str = "", sender: str = "",
                  folder: str = "INBOX", limit: int = 20, offset: int = 0,
                  unseen_only: bool = False, with_snippet: bool = False,
                  since: str = "", before: str = "") -> list[dict]:
    """Search emails by free text, subject, or sender, newest first.

    Args:
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
        query: free-text search across the message.
        subject: match within the subject.
        sender: match the From address.
        folder, limit, offset, unseen_only, with_snippet, since, before:
            same meaning as in email_list.

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

    return _fetch_messages(user_id, account, folder, criteria, limit=limit, offset=offset,
                          with_snippet=with_snippet, capability="email.search")

def _build_message(sender: str, to: str, subject: str, body: str, cc: str) -> MIMEText:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    return msg


@service_tool(capability="email.draft")
def email_draft(ctx: Context, to: str, subject: str, body: str, cc: str = "", account: str = "") -> dict:
    """Save a draft email in the mailbox's Drafts folder. Nothing is sent.

    Args:
        account: which mailbox to save the draft in — an `account` value or name
            from list_accounts. Required when the user has more than one mailbox.
    """
    # One lookup for both the From header and the mailbox written to, so they
    # are always the same account.
    creds = get_email_credentials(_get_user_id(ctx), account, "email.draft")
    msg = _build_message(creds["email"], to, subject, body, cc)

    with open_mailbox(creds) as mailbox:
        drafts_folder = _resolve_drafts_folder(mailbox)
        mailbox.append(
            msg.as_bytes(),
            drafts_folder,
            dt=datetime.datetime.now(datetime.timezone.utc),
            flag_set=[MailMessageFlags.DRAFT],
        )

    return {
        "status": "draft_saved",
        "account": creds["account_name"],
        "from": creds["email"],
        "folder": drafts_folder,
        "to": to,
        "subject": subject,
    }


@service_tool(capability="email.send")
def email_send(ctx: Context, to: str, subject: str, body: str, cc: str = "", account: str = "") -> dict:
    """Send an email immediately. This cannot be undone.

    Args:
        account: which mailbox to send FROM — an `account` value or name from
            list_accounts. Required when the user has more than one mailbox. It is
            never guessed, so confirm with the user if it isn't clear.
    """
    creds = get_email_credentials(_get_user_id(ctx), account, "email.send")
    msg = _build_message(creds["email"], to, subject, body, cc)
    send_message(creds, msg)

    return {
        "status": "sent",
        "account": creds["account_name"],
        "from": creds["email"],
        "to": to,
        "subject": subject,
    }


@service_tool(capability="email.mark_seen")
def email_mark_seen(ctx: Context, uids: list[str], folder: str = "INBOX", seen: bool = True,
                    account: str = "") -> dict:
    """Mark multiple emails as read (seen) or unread.

    Args:
        uids: A list of email UIDs to modify.
        folder: The folder where the emails are located (default: INBOX).
        seen: True to mark as read, False to mark as unread.
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
    """
    creds = get_email_credentials(_get_user_id(ctx), account, "email.mark_seen")
    with open_mailbox(creds) as mailbox:
        mailbox.folder.set(folder)
        if uids:
            mailbox.flag(uids, MailMessageFlags.SEEN, seen)

    return {
        "status": "success",
        "account": creds["account_name"],
        "uids": uids,
        "folder": folder,
        "seen": seen,
    }
