"""IMAP mailboxes (reading, drafts, flags). Sending goes through smtp.py, which
stays a separate file so SMTP can later get its own login (see the TODO there)."""

import datetime
from email.message import Message

from imap_tools import AND, MailBox, MailMessage, MailMessageFlags

from core.accounts import resolve_account
from core.config import LOCAL_EMAIL, LOCAL_PASS, config
from core.net import MAIL_CONNECT_TIMEOUT, ensure_public_host
from services.email.providers.base import EmailProvider, MessageFilter, snippet_of
from services.email.providers.smtp import send_message


def get_email_credentials(user_id: str | None, account: str | None, capability: str) -> dict:
    """Credentials of the one mailbox this call may use (see core.accounts for the
    rules), plus `account_name` so tools can say which account they acted on.

    Without a user_id (local stdio mode) it's the single mailbox from .env.
    """
    if user_id:
        acct = resolve_account(user_id, "email", account, capability)
        return imap_credentials(acct.credentials, acct.name)
    if not LOCAL_EMAIL or not LOCAL_PASS:
        raise ValueError("Local credentials missing in .env (stdio mode)")
    return {
        "host": config["IMAP_HOST"],
        "port": config.get("IMAP_PORT", 993),
        "smtp_host": config["SMTP_HOST"],
        "smtp_port": config.get("SMTP_PORT", 587),
        "email": LOCAL_EMAIL,
        "password": LOCAL_PASS,
        "account_name": LOCAL_EMAIL,
        "local": True,
    }


def imap_credentials(stored: dict, account_name: str) -> dict:
    """A connected account's stored credentials, in the shape open_mailbox wants."""
    return {**stored, "account_name": account_name, "local": False}


def open_mailbox(creds: dict) -> MailBox:
    if not creds["local"]:
        # Re-checked at every connect, not just when the account was saved:
        # DNS can change after the fact (rebinding).
        ensure_public_host(creds["host"], creds["port"])
    return MailBox(creds["host"], creds["port"], timeout=MAIL_CONNECT_TIMEOUT).login(
        creds["email"], creds["password"]
    )


def get_mailbox(user_id: str | None, account: str | None, capability: str) -> MailBox:
    return open_mailbox(get_email_credentials(user_id, account, capability))


def _criteria(f: MessageFilter):
    conditions = {}
    if f.query:
        conditions["text"] = f.query
    if f.subject:
        conditions["subject"] = f.subject
    if f.sender:
        conditions["from_"] = f.sender
    if f.unseen_only:
        conditions["seen"] = False
    if f.since:
        conditions["date_gte"] = f.since
    if f.before:
        conditions["date_lt"] = f.before
    return AND(**conditions) if conditions else AND(all=True)


def _resolve_drafts_folder(mailbox: MailBox) -> str:
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


class ImapProvider(EmailProvider):
    """IMAP for reading/drafts/flags, SMTP for sending, one login for both.

    The IMAP connection opens on first use, so sending (SMTP only) never
    logs in to IMAP.
    """

    def __init__(self, creds: dict):
        super().__init__(creds["account_name"], creds["email"])
        self._creds = creds
        self._mailbox: MailBox | None = None

    @property
    def mailbox(self) -> MailBox:
        if self._mailbox is None:
            self._mailbox = open_mailbox(self._creds)
        return self._mailbox

    def close(self) -> None:
        if self._mailbox is not None:
            mailbox, self._mailbox = self._mailbox, None
            mailbox.logout()

    def list_folders(self) -> list[str]:
        return [f.name for f in self.mailbox.folder.list()]

    def list_messages(self, f: MessageFilter) -> list[dict]:
        """Uses UID search to get all matching UIDs, applies offset and limit to
        the UIDs, then fetches only those messages, to avoid downloading
        unnecessary data."""
        mailbox = self.mailbox
        mailbox.folder.set(f.folder)

        criteria = _criteria(f)
        if str(criteria) == "(ALL)":
            total = mailbox.folder.status(f.folder)["MESSAGES"]
            end_seq = total - f.offset
            start_seq = max(1, end_seq - f.limit + 1)

            if end_seq < 1:
                selected_uids = []
            else:
                uids = mailbox.uids(f"{start_seq}:{end_seq}")
                uids.reverse()
                selected_uids = uids
        else:
            uids = mailbox.uids(criteria)
            uids.reverse()
            selected_uids = uids[f.offset : f.offset + f.limit]

        if not selected_uids:
            return []

        results = []
        fetched = mailbox.fetch(
            AND(uid=",".join(selected_uids)),
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
            if f.with_snippet:
                item["snippet"] = snippet_of(msg)
            results.append(item)
        return results

    def fetch_message(self, uid: str, folder: str) -> MailMessage | None:
        self.mailbox.folder.set(folder)
        msgs = list(self.mailbox.fetch(AND(uid=uid), mark_seen=False))
        return msgs[0] if msgs else None

    def save_draft(self, msg: Message) -> str:
        drafts_folder = _resolve_drafts_folder(self.mailbox)
        self.mailbox.append(
            msg.as_bytes(),
            drafts_folder,
            dt=datetime.datetime.now(datetime.timezone.utc),
            flag_set=[MailMessageFlags.DRAFT],
        )
        return drafts_folder

    def send(self, msg: Message) -> None:
        send_message(self._creds, msg)

    def mark_seen(self, uids: list[str], folder: str, seen: bool) -> None:
        self.mailbox.folder.set(folder)
        if uids:
            self.mailbox.flag(uids, MailMessageFlags.SEEN, seen)
