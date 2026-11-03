import datetime

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
    tool_name: str
    capability: str | None
    status: str
    detail: str | None
    created_at: datetime.datetime


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    return (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
