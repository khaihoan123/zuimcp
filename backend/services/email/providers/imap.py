import json

from imap_tools import MailBox
from sqlalchemy import text

from core.config import config, LOCAL_EMAIL, LOCAL_PASS
from core.db import SessionLocal
from core.security import decrypt_credentials

def _resolve_connection(user_id: str, account: str | None) -> dict:
    db = SessionLocal()
    try:
        if account:
            row = db.execute(
                text(
                    "SELECT credentials_encrypted FROM connections "
                    "WHERE user_id = :user_id AND status = 'active' "
                    "AND (id::text = :account OR display_name = :account)"
                ),
                {"user_id": user_id, "account": account},
            ).first()
            if not row:
                raise ValueError(f"No active connection matching '{account}' for this user")
            return json.loads(decrypt_credentials(row[0]))

        rows = db.execute(
            text(
                "SELECT id, display_name, credentials_encrypted FROM connections "
                "WHERE user_id = :user_id AND status = 'active'"
            ),
            {"user_id": user_id},
        ).fetchall()
    finally:
        db.close()

    if not rows:
        raise ValueError("No connected email accounts for this user")
    if len(rows) == 1:
        return json.loads(decrypt_credentials(rows[0].credentials_encrypted))

    options = "\n".join(f"  - {r.display_name} — account: {r.id}" for r in rows)
    raise ValueError(
        f"Multiple accounts are connected, so `account` is required. Retry "
        f"this same tool with one of the account values below — you do not "
        f"need to call any other tool first:\n{options}"
    )

def get_mailbox(user_id: str | None = None, account: str | None = None) -> MailBox:
    if user_id:
        creds = _resolve_connection(user_id, account)
    else:
        if not LOCAL_EMAIL or not LOCAL_PASS:
            raise ValueError("Local credentials missing in .env (stdio mode)")
        creds = {
            "host": config["IMAP_HOST"],
            "port": config.get("IMAP_PORT", 993),
            "email": LOCAL_EMAIL,
            "password": LOCAL_PASS,
        }
    return MailBox(creds["host"], creds["port"]).login(creds["email"], creds["password"])


def get_connected_email(user_id: str | None = None, account: str | None = None) -> str:
    if user_id:
        return _resolve_connection(user_id, account)["email"]
    return LOCAL_EMAIL