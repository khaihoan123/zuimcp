import json
import logging
import smtplib
import ssl

from fastapi import APIRouter, Depends, HTTPException
from imap_tools import MailboxLoginError
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from core.auth import get_current_user_id
from core.db import get_db
from core.net import PublicMailBox, PublicSMTP, UnsafeHostError, ensure_public_host
from core.accounts import SERVICE_TYPES, connection_identity
from core.audit import record_account_event
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


# Certificates are verified (core.net.tls_context). Shared hosting often has a
# certificate for the provider's server name rather than mail.<your domain>.
CERT_MISMATCH = (
    "The {kind} server's security certificate isn't valid for '{host}'. "
    "Use the server name your mail provider gives (often their own host name, "
    "e.g. server123.provider.com), not a custom one."
)


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    capability: str
    allowed: bool


# Credential fields the dashboard may show and prefill in the edit form. Never
# passwords, API keys or tokens.
EDITABLE_SETTINGS = {
    "imap": ("host", "port", "email", "smtp_host", "smtp_port"),
    "odoo": ("url", "database", "login"),
}


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    service_type: str
    display_name: str
    status: str
    identity: str | None = None
    # The non-secret login settings, for the edit form (EDITABLE_SETTINGS).
    settings: dict = {}
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
            "settings": {
                k: creds[k] for k in EDITABLE_SETTINGS.get(data.service_type, ()) if k in creds
            },
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
    status: str | None = None  # 'active' (resume) | 'disabled' (pause)


class UpdatePermissionsIn(BaseModel):
    permissions: dict[str, bool]  # {"email.draft": true, ...}


def _get_owned_connection(
    db: Session, connection_id: int, user_id: str, service_type: str | None = None
) -> Connection:
    """Loads a connection only if it belongs to the calling user — never let
    someone reach another user's connection just by guessing an id."""
    connection = (
        db.query(Connection)
        .filter(Connection.id == connection_id, Connection.user_id == user_id)
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    if service_type is not None and connection.service_type != service_type:
        raise HTTPException(status_code=400, detail=f"This is not an {service_type} account")
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


def _verify_mail_login(host: str, port: int, smtp_host: str, smtp_port: int, email: str, password: str) -> None:
    """Logs in to IMAP and SMTP, or raises a 400 saying which one failed."""
    # Fail fast — verify both protocols actually work before ever storing
    # credentials. IMAP first since it's required; SMTP failing after IMAP
    # succeeds still means nothing gets saved. Errors shown to the user stay
    # generic: echoing the raw exception would leak server banners and make
    # the form a handy network-probing tool. The real cause goes to the log.
    try:
        with PublicMailBox(host, port).login(email, password):
            pass
    except MailboxLoginError:
        raise HTTPException(status_code=400, detail="IMAP login failed — check the email and password")
    except ssl.SSLCertVerificationError:
        raise HTTPException(status_code=400, detail=CERT_MISMATCH.format(kind="IMAP", host=host))
    except Exception:
        logger.warning("IMAP connection test failed for %s:%s", host, port, exc_info=True)
        raise HTTPException(status_code=400, detail="Could not connect to the IMAP server — check the host and port")

    try:
        with PublicSMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(email, password)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=400, detail="SMTP login failed — check the email and password")
    except ssl.SSLCertVerificationError:
        raise HTTPException(status_code=400, detail=CERT_MISMATCH.format(kind="SMTP", host=smtp_host))
    except Exception:
        logger.warning("SMTP connection test failed for %s:%s", smtp_host, smtp_port, exc_info=True)
        raise HTTPException(status_code=400, detail="Could not connect to the SMTP server — check the host and port")


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

    _verify_mail_login(body.host, body.port, body.smtp_host, body.smtp_port, body.email, body.password)

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

    record_account_event(db, connection, "account.connected")
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
    if body.status is not None and body.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status must be 'active' or 'disabled'")

    new_name = (body.display_name or "").strip()
    if new_name and new_name != connection.display_name:
        old_name, connection.display_name = connection.display_name, new_name
        record_account_event(db, connection, "account.renamed", f'"{old_name}" → "{new_name}"')

    if body.status == "disabled" and connection.status != "disabled":
        connection.status = "disabled"
        record_account_event(db, connection, "account.paused")
    elif body.status == "active" and connection.status == "disabled":
        connection.status = "active"
        record_account_event(db, connection, "account.resumed")
    elif body.status == "active" and connection.status == "needs_reauth":
        raise HTTPException(
            status_code=400, detail="This account's login was refused: update its login instead."
        )

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
    # Logged first: deleting the account empties the entry's link (SET NULL),
    # and the entry keeps the account's name and target.
    record_account_event(db, connection, "account.disconnected")
    db.flush()
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
        if existing[capability].allowed != allowed:
            existing[capability].allowed = allowed
            record_account_event(
                db, connection, "permission.on" if allowed else "permission.off", capability=capability
            )
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


def _verify_odoo_login(url: str, database: str, login: str, api_key: str) -> dict:
    """Checks the Odoo login and returns the credentials to store, or raises a 400."""
    try:
        url = normalize_url(url)
        ensure_public_host(*host_and_port(url), what="Odoo server")
    except ValueError as e:  # includes UnsafeHostError
        raise HTTPException(status_code=400, detail=str(e))

    database, login, api_key = database.strip(), login.strip(), api_key.strip()
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
    return credentials


@router.post("/odoo", response_model=ConnectionOut, status_code=201)
def create_odoo_connection(
    body: CreateOdooConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    credentials = _verify_odoo_login(body.url, body.database, body.login, body.api_key)

    connection = Connection(
        user_id=user_id,
        service_type="odoo",
        display_name=body.display_name.strip() or f"{credentials['database']} ({credentials['login']})",
        credentials_encrypted=encrypt_credentials(json.dumps(credentials)),
        status="active",
    )
    db.add(connection)
    db.flush()  # assigns connection.id before we create Permission rows

    for capability in DEFAULT_ODOO_CAPABILITIES:
        db.add(Permission(connection_id=connection.id, capability=capability, allowed=False))

    record_account_event(db, connection, "account.connected")
    db.commit()
    db.refresh(connection)
    return connection


# --- editing a saved login ----------------------------------------------------


# (credentials key, label) compared for the log when a login is edited.
LOGIN_FIELDS = {
    "imap": [("host", "IMAP host"), ("port", "IMAP port"), ("smtp_host", "SMTP host"),
             ("smtp_port", "SMTP port")],
    "odoo": [("url", "URL"), ("database", "database"), ("login", "login"), ("version", "Odoo version")],
}
# Secrets: the log says they were replaced, never what they are.
SECRET_FIELDS = {"imap": ("password", "password"), "odoo": ("api_key", "API key")}


def _login_changes(service_type: str, old: dict, new: dict) -> list[str]:
    changes = [
        f"{label} {old.get(key)} → {new.get(key)}"
        for key, label in LOGIN_FIELDS[service_type]
        if old.get(key) != new.get(key)
    ]
    key, label = SECRET_FIELDS[service_type]
    if old.get(key) != new.get(key):
        changes.append(f"{label} replaced")
    return changes


def _save_login(db: Session, connection: Connection, credentials: dict) -> None:
    """Stores credentials that were just verified, and logs what changed. An
    account flagged because its login was refused works again; a paused one
    stays paused."""
    old = json.loads(decrypt_credentials(connection.credentials_encrypted))
    changes = _login_changes(connection.service_type, old, credentials)
    connection.credentials_encrypted = encrypt_credentials(json.dumps(credentials))
    if connection.status == "needs_reauth":
        connection.status = "active"
        changes.append("works again")
    if changes:
        record_account_event(db, connection, "account.login_updated", "; ".join(changes))
    db.commit()
    db.refresh(connection)


class EditImapConnectionIn(BaseModel):
    host: str
    port: int = 993
    smtp_host: str
    smtp_port: int = 587
    password: str = ""  # blank = keep the saved one


@router.put("/{connection_id}/imap", response_model=ConnectionOut)
def edit_imap_connection(
    connection_id: int,
    body: EditImapConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Changes an IMAP/SMTP account's servers or password. The address stays:
    another address is another mailbox, so it's a new account. Checked like a
    new account; on success the account works again."""
    connection = _get_owned_connection(db, connection_id, user_id, "imap")
    stored = json.loads(decrypt_credentials(connection.credentials_encrypted))
    email = stored["email"]
    try:
        ensure_public_host(body.host, body.port)
        ensure_public_host(body.smtp_host, body.smtp_port)
    except UnsafeHostError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Moving to another server must not make it a copy of another account.
    for existing, creds in mailboxes_with_address(db, user_id, email):
        if existing.id != connection.id and same_mailbox(
            "imap", {"host": body.host}, existing.service_type, creds
        ):
            raise HTTPException(
                status_code=409,
                detail=f"This mailbox is already connected as '{existing.display_name}'.",
            )

    password = body.password or stored.get("password", "")
    _verify_mail_login(body.host, body.port, body.smtp_host, body.smtp_port, email, password)
    _save_login(db, connection, {
        **stored,
        "host": body.host,
        "port": body.port,
        "smtp_host": body.smtp_host,
        "smtp_port": body.smtp_port,
        "password": password,
    })
    return connection


class EditOdooConnectionIn(BaseModel):
    url: str
    database: str
    login: str
    api_key: str = ""  # blank = keep the saved one


@router.put("/{connection_id}/odoo", response_model=ConnectionOut)
def edit_odoo_connection(
    connection_id: int,
    body: EditOdooConnectionIn,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Changes an Odoo account's login, e.g. a new API key after the old one
    expired. Checked like a new account; on success the account works again."""
    connection = _get_owned_connection(db, connection_id, user_id, "odoo")
    stored = json.loads(decrypt_credentials(connection.credentials_encrypted))
    api_key = body.api_key.strip() or stored.get("api_key", "")
    _save_login(db, connection, _verify_odoo_login(body.url, body.database, body.login, api_key))
    return connection
