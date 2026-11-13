import json
import logging
import smtplib
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from imap_tools import MailBox, MailboxLoginError
from pydantic import BaseModel, ConfigDict, model_validator
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


def connection_identity(service_type: str, creds: dict) -> str | None:
    """Which mailbox or Odoo user an account is, for the dashboard. Built from a
    few safe fields only, never passwords, keys or tokens."""
    if service_type in SERVICE_TYPES["email"]:
        return creds.get("email") or None
    if service_type == "odoo":
        login, url, database = creds.get("login"), creds.get("url"), creds.get("database")
        if not (login and url and database):
            return None
        return f"{login} on {urlsplit(url).hostname or url} (database {database})"
    return None


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    service_type: str
    display_name: str
    status: str
    identity: str | None = None
    permissions: list[PermissionOut]

    @model_validator(mode="before")
    @classmethod
    def _from_connection(cls, data):
        if not isinstance(data, Connection):
            return data
        try:
            creds = json.loads(decrypt_credentials(data.credentials_encrypted))
        except Exception:
            creds = {}
        return {
            "id": data.id,
            "service_type": data.service_type,
            "display_name": data.display_name,
            "status": data.status,
            "identity": connection_identity(data.service_type, creds),
            "permissions": [PermissionOut.model_validate(p) for p in data.permissions],
        }


class CreatedConnectionOut(ConnectionOut):
    # Ids of the user's other mailboxes with the same address that are a
    # different mailbox (allowed, e.g. old cPanel + new Gmail during a
    # migration). The dashboard warns that the agent must then pick one.
    same_address_as: list[int] = []


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


def mailboxes_with_address(db: Session, user_id: str, address: str) -> list[tuple[Connection, dict]]:
    """The user's mailboxes (any provider, any status) whose address equals
    `address`, ignoring case, with their decrypted credentials. Use
    same_mailbox() to tell which of them is really the same mailbox."""
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


# IMAP servers that open a Gmail mailbox.
GOOGLE_IMAP_HOSTS = {"imap.gmail.com", "imap.googlemail.com"}


def _host(creds: dict) -> str:
    return creds.get("host", "").strip().rstrip(".").casefold()


def same_mailbox(type_a: str, creds_a: dict, type_b: str, creds_b: dict) -> bool:
    """Whether two connections WITH THE SAME ADDRESS open the same mailbox.

    Rule: one mailbox, one account. The same mailbox twice makes the agent ask
    "which one?" for nothing, and could repeat actions on it. But one address
    can be two real mailboxes for a while, e.g. during a cPanel -> Gmail
    migration: old mail stays on cPanel, new mail arrives in Gmail. So:
    - Gmail + Gmail: same mailbox.
    - Gmail + IMAP: same only if the IMAP server is Google's (imap.gmail.com).
    - IMAP + IMAP: same only on the same server.
    """
    if type_a == type_b == "gmail":
        return True
    if type_a == type_b == "imap":
        return _host(creds_a) == _host(creds_b)
    imap_creds = creds_a if type_a == "imap" else creds_b
    return _host(imap_creds) in GOOGLE_IMAP_HOSTS


@router.get("", response_model=list[ConnectionOut])
def list_connections(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    return db.query(Connection).filter(Connection.user_id == user_id).all()


@router.post("/imap", response_model=CreatedConnectionOut, status_code=201)
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

    # One mailbox, one account (see same_mailbox). Checked before logging in,
    # so a duplicate fails fast.
    same_address = mailboxes_with_address(db, user_id, body.email)
    for existing, creds in same_address:
        if not same_mailbox("imap", {"host": body.host}, existing.service_type, creds):
            continue
        if existing.service_type == "gmail":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This address is already connected with Gmail as '{existing.display_name}'. "
                    f"{body.host} is Google's own IMAP server, so it is the same mailbox. "
                    f"Keep using the Gmail one, or disconnect it first."
                ),
            )
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
    out = CreatedConnectionOut.model_validate(connection)
    out.same_address_as = [c.id for c, _ in same_address]
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
