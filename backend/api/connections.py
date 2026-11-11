import json
import logging
import smtplib

from fastapi import APIRouter, Depends, HTTPException
from imap_tools import MailBox, MailboxLoginError
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from core.auth import get_current_user_id
from core.db import get_db
from core.net import MAIL_CONNECT_TIMEOUT, UnsafeHostError, ensure_public_host
from core.accounts import SERVICE_TYPES
from core.security import decrypt_credentials, encrypt_credentials
from models import Connection, Permission
from services.odoo.client import OdooAuthError, OdooClient, OdooVersionError, host_and_port, normalize_url

logger = logging.getLogger(__name__)

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
    # Only set when creating: something the user should know, e.g. that the
    # same address is also connected through another provider.
    warning: str | None = None


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


# How each mailbox provider is named in messages to the user.
EMAIL_PROVIDER_LABELS = {"imap": "IMAP", "gmail": "Gmail"}


def mailboxes_with_address(db: Session, user_id: str, address: str) -> list[tuple[Connection, dict]]:
    """The user's mailboxes (any provider, any status) whose address equals
    `address`, ignoring case, with their decrypted credentials."""
    rows = (
        db.query(Connection)
        .filter(Connection.user_id == user_id, Connection.service_type.in_(SERVICE_TYPES["email"]))
        .order_by(Connection.id)
        .all()
    )
    wanted = address.strip().casefold()
    found = []
    for connection in rows:
        creds = json.loads(decrypt_credentials(connection.credentials_encrypted))
        if creds.get("email", "").strip().casefold() == wanted:
            found.append((connection, creds))
    return found


def same_address_warning(others: list[Connection]) -> str | None:
    """Same mailbox through two providers is allowed (handy while switching from
    IMAP to Gmail), but the agent then sees it twice, so tell the user."""
    if not others:
        return None
    where = ", ".join(
        f"{EMAIL_PROVIDER_LABELS.get(c.service_type, c.service_type)} as '{c.display_name}'" for c in others
    )
    return (
        f"This address is also connected with {where}. Your agent will see both; "
        f"you may want to disconnect one."
    )


@router.get("", response_model=list[ConnectionOut])
def list_connections(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    return db.query(Connection).filter(Connection.user_id == user_id).all()


@router.post("/imap", response_model=ConnectionOut, status_code=201)
def create_imap_connection(
    body: CreateImapConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        ensure_public_host(body.host, body.port)
        ensure_public_host(body.smtp_host, body.smtp_port)
    except UnsafeHostError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # The same mailbox twice is never useful: the agent would have to ask
    # "which one?" every time, for two names of one inbox. Checked before
    # logging in, so a duplicate fails fast.
    same_address = mailboxes_with_address(db, user_id, body.email)
    for existing, creds in same_address:
        if existing.service_type == "imap" and creds.get("host", "").strip().casefold() == body.host.strip().casefold():
            raise HTTPException(
                status_code=409,
                detail=f"This mailbox is already connected as '{existing.display_name}'.",
            )

    # Fail fast — verify both protocols actually work before ever storing
    # credentials. IMAP first since it's required; SMTP failing after IMAP
    # succeeds still means nothing gets saved. Errors shown to the user stay
    # generic: echoing the raw exception would leak server banners and make
    # the form a handy network-probing tool. The real cause goes to the log.
    try:
        with MailBox(body.host, body.port, timeout=MAIL_CONNECT_TIMEOUT).login(
            body.email, body.password
        ):
            pass
    except MailboxLoginError:
        raise HTTPException(status_code=400, detail="IMAP login failed — check the email and password")
    except Exception:
        logger.warning("IMAP connection test failed for %s:%s", body.host, body.port, exc_info=True)
        raise HTTPException(status_code=400, detail="Could not connect to the IMAP server — check the host and port")

    try:
        with smtplib.SMTP(body.smtp_host, body.smtp_port, timeout=MAIL_CONNECT_TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(body.email, body.password)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=400, detail="SMTP login failed — check the email and password")
    except Exception:
        logger.warning("SMTP connection test failed for %s:%s", body.smtp_host, body.smtp_port, exc_info=True)
        raise HTTPException(status_code=400, detail="Could not connect to the SMTP server — check the host and port")

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
    out = ConnectionOut.model_validate(connection)
    # Same address on another IMAP server is another mailbox: no warning.
    out.warning = same_address_warning([c for c, _ in same_address if c.service_type != "imap"])
    return out


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


# --- Odoo ------------------------------------------------------------------

DEFAULT_ODOO_CAPABILITIES = ["odoo.read", "odoo.create", "odoo.write", "odoo.delete"]


class CreateOdooConnectionIn(BaseModel):
    display_name: str = ""
    url: str  # https://mycompany.odoo.com
    database: str
    login: str
    api_key: str


@router.post("/odoo", response_model=ConnectionOut, status_code=201)
def create_odoo_connection(
    body: CreateOdooConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        url = normalize_url(body.url)
        ensure_public_host(*host_and_port(url), what="Odoo server")
    except ValueError as e:  # includes UnsafeHostError
        raise HTTPException(status_code=400, detail=str(e))

    database, login, api_key = body.database.strip(), body.login.strip(), body.api_key.strip()
    if not (database and login and api_key):
        raise HTTPException(status_code=400, detail="Database, login and API key are all required")

    # Detects the Odoo version (8-18 -> classic JSON-RPC, 19+ -> JSON-2) and
    # checks the login before anything is stored. As with IMAP, errors shown to
    # the user stay generic so the form can't be used to probe other servers;
    # the real cause goes to the log.
    try:
        _, credentials = OdooClient.connect(url, database, login, api_key)
    except OdooVersionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OdooAuthError:
        raise HTTPException(
            status_code=400,
            detail="Odoo login failed — check the database, login and API key (and that the key hasn't expired)",
        )
    except Exception:
        logger.warning("Odoo connection test failed for %s", url, exc_info=True)
        raise HTTPException(
            status_code=400, detail="Could not connect to Odoo — check the URL and the database name"
        )

    connection = Connection(
        user_id=user_id,
        service_type="odoo",
        display_name=body.display_name.strip() or f"{database} ({credentials['login']})",
        credentials_encrypted=encrypt_credentials(json.dumps(credentials)),
        status="active",
    )
    db.add(connection)
    db.flush()  # assigns connection.id before we create Permission rows

    for capability in DEFAULT_ODOO_CAPABILITIES:
        db.add(Permission(connection_id=connection.id, capability=capability, allowed=False))

    db.commit()
    db.refresh(connection)
    return connection
