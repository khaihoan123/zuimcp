import smtplib
from email.message import Message

from core.net import MAIL_CONNECT_TIMEOUT, ensure_public_host


# TODO: SMTP logs in with the same email/password as IMAP, which fits cPanel-style
# hosting (one login for both). Later, add an optional separate SMTP username and
# password (connect form + api/connections.py + here) so users can send through
# another server, e.g. Gmail SMTP, Amazon SES, SendGrid, while reading over IMAP.
def send_message(creds: dict, msg: Message) -> None:
    """Send through the account in `creds` — the same credentials the caller
    used for the From header, so sender and mailbox can never disagree."""
    smtp_port = creds.get("smtp_port", 587)
    if not creds["local"]:
        ensure_public_host(creds["smtp_host"], smtp_port)
    with smtplib.SMTP(creds["smtp_host"], smtp_port, timeout=MAIL_CONNECT_TIMEOUT) as smtp:
        smtp.starttls()
        smtp.login(creds["email"], creds["password"])
        smtp.send_message(msg)
