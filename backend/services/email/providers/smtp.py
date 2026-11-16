from email.message import Message

from core.net import PublicSMTP


# TODO: SMTP logs in with the same email/password as IMAP, which fits cPanel-style
# hosting (one login for both). Later, add an optional separate SMTP username and
# password (connect form + api/connections.py + here) so users can send through
# another server, e.g. Gmail SMTP, Amazon SES, SendGrid, while reading over IMAP.
def send_message(creds: dict, msg: Message) -> None:
    """Send through the account in `creds` — the same credentials the caller
    used for the From header, so sender and mailbox can never disagree."""
    smtp_port = creds.get("smtp_port", 587)
    # Checked and pinned at every connect (core.net), not just when the account
    # was saved: DNS can change after the fact.
    with PublicSMTP(creds["smtp_host"], smtp_port, check_public=not creds["local"]) as smtp:
        smtp.starttls()
        smtp.login(creds["email"], creds["password"])
        smtp.send_message(msg)
