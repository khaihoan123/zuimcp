import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.auth import get_current_user_id
from core.db import get_db
from models import AuditLog

router = APIRouter(prefix="/api/audit-log", tags=["audit-log"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    # 'agent' (a tool call), 'user' (a dashboard change) or 'system' (ZuiMCP).
    actor: str
    # Dashboard/system changes only, e.g. 'permission.on' (core.audit.ACTIONS).
    action: str | None
    # Agent calls only.
    tool_name: str | None
    capability: str | None
    # The account the entry is about, copied at the time; connection_id is only
    # a link, emptied when the account is disconnected.
    connection_id: int | None
    account_name: str | None
    service_type: str | None
    account_target: str | None
    # The account was filled in afterwards, not recorded at the time.
    inferred: bool
    status: str
    detail: str | None
    created_at: datetime.datetime


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    limit: int = 50,
    before_id: int | None = None,
    connection_id: int | None = None,
    actor: Literal["agent", "user", "system"] | None = None,
    errors_only: bool = False,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """The user's activity, newest first, a page at a time: pass the last id of
    a page as `before_id` to get the next one."""
    query = db.query(AuditLog).filter(AuditLog.user_id == user_id)
    if before_id is not None:
        query = query.filter(AuditLog.id < before_id)
    if connection_id is not None:
        query = query.filter(AuditLog.connection_id == connection_id)
    if actor is not None:
        query = query.filter(AuditLog.actor == actor)
    if errors_only:
        query = query.filter(AuditLog.status != "success")
    return (
        query
        .order_by(AuditLog.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
