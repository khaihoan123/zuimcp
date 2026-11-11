"""The interface every email backend implements (IMAP/SMTP, Gmail, ...).

The email_* tools in services/email/service.py only talk to an EmailProvider,
so they look the same to the AI whatever the account's backend is. Each
provider translates to its own protocol: IMAP search criteria, Gmail `q=`
strings, and so on. The query is not unified into one criteria object on
purpose — the backends search too differently for that to stay honest.

Shared vocabulary:
- `folder`: an IMAP folder name, or a Gmail label name.
- `uid`: the IMAP UID, or the Gmail message id.
"""

import datetime
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import Message

from imap_tools import MailAttachment, MailMessage


@dataclass
class MessageFilter:
    """Which messages email_list / email_search want, newest first."""

    folder: str = "INBOX"
    limit: int = 20
    offset: int = 0
    unseen_only: bool = False
    since: datetime.date | None = None  # on/after this date
    before: datetime.date | None = None  # strictly before this date
    query: str = ""  # free text
    subject: str = ""
    sender: str = ""
    with_snippet: bool = False


def snippet_of(msg: MailMessage, length: int = 200) -> str:
    """Plain-text preview of a message body, HTML stripped and whitespace collapsed."""
    text = msg.text or msg.html or ""
    text = re.sub(r"<[^>]+>", " ", text)  # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()  # collapse whitespace
    return text[:length]


def message_details(uid: str, msg: MailMessage) -> dict:
    """The email_read result for a parsed message, the same for every provider."""
    return {
        "uid": uid,
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
                "size": a.size,
            }
            for a in msg.attachments
        ],
    }


class EmailProvider(ABC):
    """One connected mailbox. Use it as a context manager so connections close.

    `account_name` and `address` let tools report which account they acted on.
    """

    def __init__(self, account_name: str, address: str):
        self.account_name = account_name
        self.address = address

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> None:
        """Releases any open connection. Safe to call when nothing is open."""

    @abstractmethod
    def list_folders(self) -> list[str]:
        """Folder (or label) names, usable as `folder` in the other methods."""

    @abstractmethod
    def list_messages(self, f: MessageFilter) -> list[dict]:
        """Message summaries, newest first: uid, from, subject, date, seen,
        and `snippet` when f.with_snippet is set."""

    @abstractmethod
    def fetch_message(self, uid: str, folder: str) -> MailMessage | None:
        """The full parsed message, without marking it read. None if not found."""

    def read_message(self, uid: str, folder: str) -> dict | None:
        msg = self.fetch_message(uid, folder)
        return message_details(uid, msg) if msg else None

    def get_attachments(self, uid: str, folder: str) -> list[MailAttachment] | None:
        """All attachments (with their bytes). None if the message isn't found."""
        msg = self.fetch_message(uid, folder)
        return list(msg.attachments) if msg else None

    @abstractmethod
    def save_draft(self, msg: Message) -> str:
        """Stores `msg` as a draft and returns the folder (or label) it is in."""

    @abstractmethod
    def send(self, msg: Message) -> None:
        """Sends `msg` now."""

    @abstractmethod
    def mark_seen(self, uids: list[str], folder: str, seen: bool) -> None:
        """Marks messages read (seen=True) or unread."""
