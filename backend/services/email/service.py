"""Email tools. Registered on the shared server in core.mcp_server.

Every tool works through an EmailProvider (services/email/providers), chosen by
the account's backend (IMAP/SMTP, Gmail, ...), so the tools, their arguments
and their results are the same whatever kind of mailbox the user connected.
"""

import base64
import datetime
import email.utils
import json
from email.mime.text import MIMEText

from fastmcp import Context
from mcp.types import ImageContent, TextContent

from core.mcp_auth import get_user_id as _get_user_id
from core.mcp_server import service_tool
from services.email.providers import EmailProvider, MessageFilter, open_provider


def _provider(ctx: Context, account: str, capability: str) -> EmailProvider:
    return open_provider(_get_user_id(ctx), account, capability)


def _date(value: str) -> datetime.date | None:
    return datetime.date.fromisoformat(value) if value else None


# Gated on email.read: folder names are user data (clients, projects),
# not neutral metadata, and listing them is only useful before a read.
@service_tool(capability="email.read")
def email_list_folders(ctx: Context, account: str = "") -> list[str]:
    """List all folders of a mailbox (e.g. INBOX, INBOX.Drafts; labels for Gmail). Use this to find the exact folder name for other email tools.

    Args:
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
    """
    with _provider(ctx, account, "email.read") as provider:
        return provider.list_folders()

@service_tool(capability="email.read")
def email_list(ctx: Context, account: str = "", folder: str = "INBOX", limit: int = 20, offset: int = 0,
                unseen_only: bool = False, with_snippet: bool = False,
                since: str = "", before: str = "") -> list[dict]:
    """List emails from a folder, newest first.

    Args:
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
        folder: folder name (see email_list_folders). For Gmail, a label name.
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
    f = MessageFilter(folder=folder, limit=limit, offset=offset, unseen_only=unseen_only,
                      since=_date(since), before=_date(before), with_snippet=with_snippet)
    with _provider(ctx, account, "email.read") as provider:
        return provider.list_messages(f)

@service_tool(capability="email.read")
def email_read(ctx: Context, uid: str, folder: str = "INBOX", account: str = "") -> dict:
    """Read a single email by UID. Returns full headers, plain text body, and attachment names.

    Args:
        uid: the email's uid, from email_list or email_search.
        folder: the folder it is in.
        account: which mailbox to use — an `account` value or name from list_accounts.
            Required when the user has more than one mailbox.
    """
    with _provider(ctx, account, "email.read") as provider:
        details = provider.read_message(uid, folder)
    if details is None:
        return {"error": f"No email found with uid={uid} in {folder}"}
    return details

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
    with _provider(ctx, account, "email.read") as provider:
        attachments = provider.get_attachments(uid, folder)
    if attachments is None:
        return {"error": f"No email found with uid={uid} in {folder}"}
    if not attachments:
        return {"error": "Email has no attachments"}

    target = None
    if content_id:
        for a in attachments:
            if a.content_id == content_id:
                target = a
                break
    elif index >= 0 and index < len(attachments):
        target = attachments[index]

    if not target and (index >= 0 or content_id):
        return {"error": f"Attachment not found (index={index}, content_id={content_id})"}

    targets = [target] if target else attachments
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
    f = MessageFilter(folder=folder, limit=limit, offset=offset, unseen_only=unseen_only,
                      since=_date(since), before=_date(before), query=query, subject=subject,
                      sender=sender, with_snippet=with_snippet)
    with _provider(ctx, account, "email.search") as provider:
        return provider.list_messages(f)

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
    # One provider for both the From header and the mailbox written to, so they
    # are always the same account.
    with _provider(ctx, account, "email.draft") as provider:
        msg = _build_message(provider.address, to, subject, body, cc)
        drafts_folder = provider.save_draft(msg)

    return {
        "status": "draft_saved",
        "account": provider.account_name,
        "from": provider.address,
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
    with _provider(ctx, account, "email.send") as provider:
        msg = _build_message(provider.address, to, subject, body, cc)
        provider.send(msg)

    return {
        "status": "sent",
        "account": provider.account_name,
        "from": provider.address,
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
    with _provider(ctx, account, "email.mark_seen") as provider:
        provider.mark_seen(uids, folder, seen)

    return {
        "status": "success",
        "account": provider.account_name,
        "uids": uids,
        "folder": folder,
        "seen": seen,
    }
