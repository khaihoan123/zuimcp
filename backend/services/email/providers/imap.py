import json

from imap_tools import MailBox
from sqlalchemy import text

from core.config import config, LOCAL_EMAIL, LOCAL_PASS
from core.db import SessionLocal
from core.net import MAIL_CONNECT_TIMEOUT, ensure_public_host
from core.security import decrypt_credentials

def _resolve_connection(user_id: str, account: str | None, capability: str | None = None) -> dict:
    """Looks up one connection's decrypted credentials.

    `capability`, when given, restricts the lookup to connections that have
    that capability granted — used for actions like sending mail, where
    unlike the read-only tools an unpermitted call must not fall through to
    a mailbox the agent isn't supposed to touch.
    """
    capability_join = (
        "JOIN permissions p ON p.connection_id = c.id "
        "AND p.capability = :capability AND p.allowed = true"
        if capability
        else ""
    )
    params = {"user_id": user_id}
    if capability:
        params["capability"] = capability

    db = SessionLocal()
    try:
        if account:
            row = db.execute(
                text(
                    f"SELECT c.credentials_encrypted FROM connections c {capability_join} "
                    "WHERE c.user_id = :user_id AND c.status = 'active' "
                    "AND (c.id::text = :account OR c.display_name = :account)"
                ),
                {**params, "account": account},
            ).first()
            if not row:
                detail = f" with '{capability}' permission granted" if capability else ""
                raise ValueError(f"No active connection matching '{account}'{detail} for this user")
            return json.loads(decrypt_credentials(row[0]))

        rows = db.execute(
            text(
                f"SELECT c.id, c.display_name, c.credentials_encrypted FROM connections c "
                f"{capability_join} WHERE c.user_id = :user_id AND c.status = 'active'"
            ),
            params,
        ).fetchall()
    finally:
        db.close()

    if not rows:
        detail = f" with '{capability}' permission granted" if capability else ""
        raise ValueError(f"No connected email accounts{detail} for this user")
    if len(rows) == 1:
        return json.loads(decrypt_credentials(rows[0].credentials_encrypted))

    options = "\n".join(f"  - {r.display_name} — account: {r.id}" for r in rows)
    raise ValueError(
        f"Multiple accounts are connected, so `account` is required. Retry "
        f"this same tool with one of the account values below — you do not "
        f"need to call any other tool first:\n{options}"
    )

def get_mailbox(
    user_id: str | None = None, account: str | None = None, capability: str | None = None
) -> MailBox:
    if user_id:
        creds = _resolve_connection(user_id, account, capability)
        ensure_public_host(creds["host"], creds["port"])
    else:
        if not LOCAL_EMAIL or not LOCAL_PASS:
            raise ValueError("Local credentials missing in .env (stdio mode)")
        creds = {
            "host": config["IMAP_HOST"],
            "port": config.get("IMAP_PORT", 993),
            "email": LOCAL_EMAIL,
            "password": LOCAL_PASS,
        }
    return MailBox(creds["host"], creds["port"], timeout=MAIL_CONNECT_TIMEOUT).login(
        creds["email"], creds["password"]
    )


def get_connected_email(
    user_id: str | None = None, account: str | None = None, capability: str | None = None
) -> str:
    """Address of the connected mailbox. Callers pass the same `capability`
    they used for get_mailbox — otherwise, for a user with several accounts,
    this could resolve to a different connection than the one being written
    to and put the wrong address in the From header."""
    if user_id:
        return _resolve_connection(user_id, account, capability)["email"]
    return LOCAL_EMAIL