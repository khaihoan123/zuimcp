import json
import smtplib

from fastapi import APIRouter, Depends, HTTPException
from imap_tools import MailBox
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.auth import get_current_user_id
from core.db import get_db
from core.security import encrypt_credentials
from models import Connection, Permission

router = APIRouter(prefix="/api/connections", tags=["connections"])

DEFAULT_IMAP_CAPABILITIES = [
    "email.read",
    "email.search",
    "email.mark_seen",
    "email.draft",
    "email.send",
]


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    capability: str
    allowed: bool


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    service_type: str
    display_name: str
    status: str
    permissions: list[PermissionOut]


class CreateImapConnectionIn(BaseModel):
    display_name: str
    host: str
    port: int = 993
    email: str
    password: str
    smtp_host: str
    smtp_port: int = 587


class UpdateConnectionIn(BaseModel):
    display_name: str | None = None
    status: str | None = None  # 'active' | 'disabled'


class UpdatePermissionsIn(BaseModel):
    permissions: dict[str, bool]  # {"email.draft": true, ...}


def _get_owned_connection(db: Session, connection_id: int, user_id: str) -> Connection:
    """Loads a connection only if it belongs to the calling user — never let
    someone reach another user's connection just by guessing an id."""
    connection = (
        db.query(Connection)
        .filter(Connection.id == connection_id, Connection.user_id == user_id)
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connection


@router.get("", response_model=list[ConnectionOut])
def list_connections(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    return db.query(Connection).filter(Connection.user_id == user_id).all()


@router.post("/imap", response_model=ConnectionOut, status_code=201)
def create_imap_connection(
    body: CreateImapConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    # Fail fast — verify both protocols actually work before ever storing
    # credentials. IMAP first since it's required; SMTP failing after IMAP
    # succeeds still means nothing gets saved.
    try:
        with MailBox(body.host, body.port).login(body.email, body.password):
            pass
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not connect to IMAP: {e}")

    try:
        with smtplib.SMTP(body.smtp_host, body.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(body.email, body.password)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not connect to SMTP: {e}")

    credentials = json.dumps(
        {
            "host": body.host,
            "port": body.port,
            "email": body.email,
            "password": body.password,
            "smtp_host": body.smtp_host,
            "smtp_port": body.smtp_port,
        }
    )
    connection = Connection(
        user_id=user_id,
        service_type="imap",
        display_name=body.display_name or body.email,
        credentials_encrypted=encrypt_credentials(credentials),
        status="active",
    )
    db.add(connection)
    db.flush()  # assigns connection.id before we create Permission rows

    for capability in DEFAULT_IMAP_CAPABILITIES:
        db.add(Permission(connection_id=connection.id, capability=capability, allowed=False))

    db.commit()
    db.refresh(connection)
    return connection


@router.patch("/{connection_id}", response_model=ConnectionOut)
def update_connection(
    connection_id: int,
    body: UpdateConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    connection = _get_owned_connection(db, connection_id, user_id)
    if body.display_name is not None:
        connection.display_name = body.display_name
    if body.status is not None:
        connection.status = body.status
    db.commit()
    db.refresh(connection)
    return connection


@router.delete("/{connection_id}", status_code=204)
def delete_connection(
    connection_id: int,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    connection = _get_owned_connection(db, connection_id, user_id)
    db.delete(connection)  # cascades to its permissions (ondelete=CASCADE)
    db.commit()


@router.patch("/{connection_id}/permissions", response_model=ConnectionOut)
def update_permissions(
    connection_id: int,
    body: UpdatePermissionsIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    connection = _get_owned_connection(db, connection_id, user_id)
    existing = {p.capability: p for p in connection.permissions}
    for capability, allowed in body.permissions.items():
        if capability not in existing:
            raise HTTPException(status_code=400, detail=f"Unknown capability '{capability}'")
        existing[capability].allowed = allowed
    db.commit()
    db.refresh(connection)
    return connection
