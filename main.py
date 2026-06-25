from fastmcp import FastMCP
from imap_tools import MailBox, AND, MailMessageFlags
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

IMAP_HOST = "mail.sunslifestyle.com"
IMAP_PORT = 993
SMTP_HOST = "mail.sunslifestyle.com"
SMTP_PORT = 587
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
PASSWORD = os.environ.get("EMAIL_PASSWORD")

mcp = FastMCP("IMAP_Connector")


def get_mailbox() -> MailBox:
    return MailBox(IMAP_HOST, IMAP_PORT).login(EMAIL_ADDRESS, PASSWORD)


@mcp.tool()
def list_emails(folder: str = "INBOX", limit: int = 20, unseen_only: bool = False) -> list[dict]:
    """List emails from a folder. Returns sender, subject, date, uid, and seen status."""
    criteria = AND(seen=False) if unseen_only else AND(all=True)
    with get_mailbox() as mailbox:
        mailbox.folder.set(folder)
        messages = []
        for msg in mailbox.fetch(criteria, limit=limit, reverse=True, mark_seen=False):
            messages.append({
                "uid": msg.uid,
                "from": msg.from_,
                "subject": msg.subject,
                "date": msg.date.isoformat() if msg.date else None,
                "seen": MailMessageFlags.SEEN in msg.flags,
            })
    return messages


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
def search_emails(query: str = "", subject: str = "", sender: str = "", folder: str = "INBOX", limit: int = 20) -> list[dict]:
    """Search emails by free text query, subject, or sender. Returns sender, subject, date, uid."""
    with get_mailbox() as mailbox:
        mailbox.folder.set(folder)
        
        kwargs = {}
        if query:
            kwargs["text"] = query
        if subject:
            kwargs["subject"] = subject
        if sender:
            kwargs["from_"] = sender
            
        criteria = AND(**kwargs) if kwargs else AND(all=True)
        
        messages = []
        for msg in mailbox.fetch(criteria, limit=limit, reverse=True, mark_seen=False):
            messages.append({
                "uid": msg.uid,
                "from": msg.from_,
                "subject": msg.subject,
                "date": msg.date.isoformat() if msg.date else None,
                "seen": MailMessageFlags.SEEN in msg.flags,
            })
    return messages


@mcp.tool()
def draft_email(to: str, subject: str, body: str, cc: str = "") -> dict:
    """Save a draft email to the Drafts folder. SMTP submission is skipped — draft is appended to the Drafts IMAP folder."""
    import email.utils
    from email.mime.text import MIMEText
    import imaplib, time, re

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    raw = msg.as_bytes()

    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(EMAIL_ADDRESS, PASSWORD)

    # Resolve the Drafts folder. Prefer the \Drafts special-use flag,
    # then fall back to a name match. Parse the folder name as the last
    # quoted/bare token, regardless of the hierarchy delimiter.
    def parse_folder_line(line: str):
        decoded = line.decode() if isinstance(line, bytes) else line
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)$', decoded)
        if not m:
            return "", ""
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        return m.group("flags").lower(), name

    drafts_folder = None
    fallback = None
    _, folders = imap.list()
    for f in folders or []:
        flags, name = parse_folder_line(f)
        if not name:
            continue
        if r"\drafts" in flags:          # special-use flag — most reliable
            drafts_folder = name
            break
        if "draft" in name.lower():      # name-based fallback
            fallback = name

    drafts_folder = drafts_folder or fallback or "INBOX.Drafts"

    imap.append(drafts_folder, r"(\Draft)",
                imaplib.Time2Internaldate(time.time()), raw)
    imap.logout()

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
