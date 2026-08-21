import smtplib
from email.message import Message

from core.config import LOCAL_EMAIL, LOCAL_PASS, config
from services.email.providers.imap import _resolve_connection


def _resolve_smtp_credentials(user_id: str | None, account: str | None, capability: str) -> dict:
    if user_id:
        return _resolve_connection(user_id, account, capability)
    if not LOCAL_EMAIL or not LOCAL_PASS:
        raise ValueError("Local credentials missing in .env (stdio mode)")
    return {
        "smtp_host": config["SMTP_HOST"],
        "smtp_port": config.get("SMTP_PORT", 587),
        "email": LOCAL_EMAIL,
        "password": LOCAL_PASS,
    }


def send_message(user_id: str | None, msg: Message, account: str | None = None) -> None:
    creds = _resolve_smtp_credentials(user_id, account, capability="email.send")
    with smtplib.SMTP(creds["smtp_host"], creds.get("smtp_port", 587)) as smtp:
        smtp.starttls()
        smtp.login(creds["email"], creds["password"])
        smtp.send_message(msg)
