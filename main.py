from fastmcp import FastMCP
from imap_tools import MailBox, AND, MailMessageFlags
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re
import datetime

IMAP_HOST = "mail.sunslifestyle.com"
IMAP_PORT = 993
SMTP_HOST = "mail.sunslifestyle.com"
SMTP_PORT = 587
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
PASSWORD = os.environ.get("EMAIL_PASSWORD")

mcp = FastMCP("IMAP_Connector")


def get_mailbox() -> MailBox:
    return MailBox(IMAP_HOST, IMAP_PORT).login(EMAIL_ADDRESS, PASSWORD)


def _snippet(msg, length: int = 200) -> str:
    """Plain-text preview of a message body, HTML stripped and whitespace collapsed."""
    text = msg.text or msg.html or ""
    text = re.sub(r"<[^>]+>", " ", text)      # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text[:length]


def _fetch_messages(folder, criteria, limit=20, offset=0,
                    with_snippet=False) -> list[dict]:
    """Shared fetch used by list_emails and search_emails.

    Returns newest-first. Uses UID search to get all matching UIDs, 
    applies offset and limit to the UIDs, and then fetches only the 
    needed messages to avoid downloading unnecessary data.
    """
    results = []
    with get_mailbox() as mailbox:
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
def list_emails(folder: str = "INBOX", limit: int = 20, offset: int = 0,
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
    conditions = {}
    if unseen_only:
        conditions["seen"] = False
    if since:
        conditions["date_gte"] = datetime.date.fromisoformat(since)
    if before:
        conditions["date_lt"] = datetime.date.fromisoformat(before)
    criteria = AND(**conditions) if conditions else AND(all=True)

    return _fetch_messages(folder, criteria, limit=limit, offset=offset,
                          with_snippet=with_snippet)


@mcp.tool()
def read_email(uid: str, folder: str = "INBOX") -> dict:
    """Read a single email by UID. Returns full headers, plain text body, and attachment names."""
    with get_mailbox() as mailbox:
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
            "attachments": [a.filename for a in msg.attachments],
        }


@mcp.tool()
def list_folders() -> list[str]:
    """List all available IMAP folders (e.g., INBOX, INBOX.Drafts, etc.). Use this to find the exact folder name to use for other tools."""
    with get_mailbox() as mailbox:
        return [f.name for f in mailbox.folder.list()]


@mcp.tool()
def search_emails(query: str = "", subject: str = "", sender: str = "",
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

    return _fetch_messages(folder, criteria, limit=limit, offset=offset,
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
def draft_email(to: str, subject: str, body: str, cc: str = "") -> dict:
    """Save a draft email to the Drafts folder. SMTP submission is skipped — draft is appended to the Drafts IMAP folder."""
    import email.utils

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    with get_mailbox() as mailbox:
        drafts_folder = _resolve_drafts_folder(mailbox)
        mailbox.append(
            msg.as_bytes(),
            drafts_folder,
            dt=datetime.datetime.now(),
            flag_set=[MailMessageFlags.DRAFT],
        )

    return {
        "status": "draft_saved",
        "folder": drafts_folder,
        "to": to,
        "subject": subject,
    }


def main():
    mcp.run()

if __name__ == "__main__":
    main()
