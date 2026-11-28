"""Writing account changes to the activity log (models.AuditLog).

Tool calls are logged by core.middleware.AuditLoggingMiddleware. This module
logs everything else that happens to an account: what the user changes in the
dashboard (actor 'user') and what ZuiMCP itself does (actor 'system'), so the
log can answer "who allowed this, and when?".

Never put a password, API key or token in `detail`: say that it changed.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.accounts import account_target
from core.db import SessionLocal
from models import AuditLog, Connection

# action -> what it means. The dashboard has the matching labels.
ACTIONS = {
    "account.connected",
    "account.renamed",
    "account.login_updated",
    "account.login_refused",
    "account.paused",
    "account.resumed",
    "account.disconnected",
    "permission.on",
    "permission.off",
}


def record_account_event(
    db: Session,
    connection: Connection,
    action: str,
    detail: str | None = None,
    *,
    capability: str | None = None,
    actor: str = "user",
) -> None:
    """Adds a log entry about `connection` to the caller's transaction, so the
    change and its log entry are saved (or rolled back) together. Call it after
    the change, so the entry shows the account as it is now."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown account action '{action}'")
    db.add(
        AuditLog(
            user_id=connection.user_id,
            actor=actor,
            action=action,
            capability=capability,
            connection_id=connection.id,
            account_name=connection.display_name,
            service_type=connection.service_type,
            account_target=account_target(connection.service_type, connection.credentials_encrypted),
            status="success",
            detail=detail,
        )
    )


def mark_login_refused(connection_id: int, reason: str) -> bool:
    """Flags an active account whose saved login the provider refused (status
    'needs_reauth'): the dashboard warns, and calls fail clearly until the user
    updates it. Logged once, when the status actually changes."""
    db = SessionLocal()
    try:
        changed = db.execute(
            text("UPDATE connections SET status = 'needs_reauth' WHERE id = :id AND status = 'active'"),
            {"id": connection_id},
        ).rowcount
        if changed:
            connection = db.get(Connection, connection_id)
            record_account_event(db, connection, "account.login_refused", reason, actor="system")
        db.commit()
        return bool(changed)
    finally:
        db.close()
