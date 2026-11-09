from imap_tools import MailBox

from core.accounts import resolve_account
from core.config import LOCAL_EMAIL, LOCAL_PASS, config
from core.net import MAIL_CONNECT_TIMEOUT, ensure_public_host


def get_email_credentials(user_id: str | None, account: str | None, capability: str) -> dict:
    """Credentials of the one mailbox this call may use (see core.accounts for the
    rules), plus `account_name` so tools can say which account they acted on.

    Without a user_id (local stdio mode) it's the single mailbox from .env.
    """
    if user_id:
        acct = resolve_account(user_id, "email", account, capability)
        return {**acct.credentials, "account_name": acct.name, "local": False}
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
