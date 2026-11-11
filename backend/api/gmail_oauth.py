"""The "Connect with Google" OAuth flow that links a Gmail mailbox.

The browser is sent to /start, which redirects to Google; Google sends it back
to /callback with a one-time code, which we swap for tokens. Both endpoints are
browser navigations, not fetch() calls, so they never answer with JSON errors:
they redirect back to the dashboard with `gmail_error=<code>` (or
`gmail_connected=<name>`), and the dashboard shows the message.

Security:
- `state` is random, and is kept with the PKCE code_verifier and the user id in
  a short-lived, encrypted, HttpOnly cookie. The callback only accepts a
  `state` equal to the one in that cookie (CSRF), and only for the same
  logged-in user that started the flow.
- PKCE (S256) means a stolen authorization code is useless on its own.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from api.connections import DEFAULT_IMAP_CAPABILITIES, mailboxes_with_address
from core.auth import get_current_user_id
from core.db import get_db
from services.email.providers.google_oauth import (
    GMAIL_SCOPE,
    GoogleOAuthError,
    authorization_url,
    exchange_code,
    expires_at,
    gmail_oauth_settings,
    http_client,
)
from core.security import decrypt_credentials, encrypt_credentials
from models import Connection, Permission

logger = logging.getLogger(__name__)

PREFIX = "/api/connections/gmail/oauth"
router = APIRouter(prefix=PREFIX, tags=["connections"])

STATE_COOKIE = "zui_gmail_oauth"
STATE_TTL = 600  # seconds the user has to finish Google's consent screen
DASHBOARD_PATH = "/dashboard/connections"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

# Same capabilities as an IMAP mailbox, so the tools work identically.
GMAIL_CAPABILITIES = DEFAULT_IMAP_CAPABILITIES


def _back_to_dashboard(**params: str) -> RedirectResponse:
    # Relative on purpose: /api is served from the dashboard's own origin
    # (Vite proxy in dev, Caddy in production).
    response = RedirectResponse(f"{DASHBOARD_PATH}?{urlencode(params)}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path=PREFIX)
    return response


def _logged_in_user(request: Request) -> str | None:
    try:
        return get_current_user_id(request)
    except HTTPException:
        return None


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@router.get("/start")
def start(request: Request, account: int | None = None, db: Session = Depends(get_db)):
    """Sends the browser to Google's consent screen. `account` (a connection id)
    pre-selects that Google account when reconnecting it."""
    user_id = _logged_in_user(request)
    if not user_id:
        return _back_to_dashboard(gmail_error="not_logged_in")
    settings = gmail_oauth_settings()
    if settings is None:
        return _back_to_dashboard(gmail_error="not_configured")

    login_hint = ""
    if account is not None:
        # Only the user's own Gmail connection: never hint someone else's address.
        connection = (
            db.query(Connection)
            .filter(Connection.id == account, Connection.user_id == user_id,
                    Connection.service_type == "gmail")
            .first()
        )
        if connection:
            login_hint = json.loads(decrypt_credentials(connection.credentials_encrypted)).get("email", "")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 chars; PKCE allows 43-128
    cookie = encrypt_credentials(
        json.dumps(
            {"state": state, "verifier": verifier, "user_id": user_id, "exp": int(time.time()) + STATE_TTL}
        )
    )

    response = RedirectResponse(
        authorization_url(settings, state, _pkce_challenge(verifier), login_hint), status_code=303
    )
    response.set_cookie(
        STATE_COOKIE,
        cookie,
        max_age=STATE_TTL,
        path=PREFIX,
        httponly=True,
        # Lax still sends the cookie on Google's top-level redirect back to us.
        samesite="lax",
        secure=settings["redirect_uri"].startswith("https://"),
    )
    return response


def _read_state_cookie(request: Request) -> dict | None:
    raw = request.cookies.get(STATE_COOKIE)
    if not raw:
        return None
    try:
        data = json.loads(decrypt_credentials(raw))
    except (InvalidToken, ValueError):
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


@router.get("/callback")
def callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    user_id = _logged_in_user(request)
    if not user_id:
        return _back_to_dashboard(gmail_error="not_logged_in")

    saved = _read_state_cookie(request)
    if (
        saved is None
        or not state
        or not hmac.compare_digest(str(saved.get("state", "")), state)
        or saved.get("user_id") != user_id
    ):
        return _back_to_dashboard(gmail_error="invalid_state")

    if error:
        # access_denied when the user pressed Cancel on Google's screen.
        return _back_to_dashboard(gmail_error="access_denied" if error == "access_denied" else "google_error")
    if not code:
        return _back_to_dashboard(gmail_error="google_error")

    settings = gmail_oauth_settings()
    if settings is None:
        return _back_to_dashboard(gmail_error="not_configured")

    with http_client() as client:
        try:
            token = exchange_code(client, settings, code, saved["verifier"])
        except GoogleOAuthError as e:
            logger.warning("Gmail code exchange failed: %s", e)
            return _back_to_dashboard(gmail_error="token_failed")

        # Google's consent screen lets the user untick scopes, so check what
        # was actually granted rather than what we asked for.
        if GMAIL_SCOPE not in token.get("scope", "").split():
            return _back_to_dashboard(gmail_error="missing_scope")

        try:
            res = client.get(
                GMAIL_PROFILE_URL, headers={"Authorization": f"Bearer {token['access_token']}"}
            )
            res.raise_for_status()
            address = res.json()["emailAddress"]
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning("Gmail profile lookup failed", exc_info=True)
            return _back_to_dashboard(gmail_error="profile_failed")

    credentials = {
        "email": address,
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token", ""),
        "expires_at": expires_at(token),
        "scope": token.get("scope", ""),
    }

    same_address = mailboxes_with_address(db, user_id, address)

    # The same Google address connected again (e.g. "Reconnect") updates the
    # existing connection — keeping its name and permissions — instead of
    # creating a duplicate.
    existing = next(((c, creds) for c, creds in same_address if c.service_type == "gmail"), None)
    if existing:
        connection, old = existing
        if not credentials["refresh_token"]:
            credentials["refresh_token"] = old.get("refresh_token", "")
        if not credentials["refresh_token"]:
            return _back_to_dashboard(gmail_error="no_refresh_token")
        connection.credentials_encrypted = encrypt_credentials(json.dumps(credentials))
        connection.status = "active"
        db.commit()
        return _back_to_dashboard(gmail_connected=connection.display_name, reconnected="1")

    if not credentials["refresh_token"]:
        return _back_to_dashboard(gmail_error="no_refresh_token")

    connection = Connection(
        user_id=user_id,
        service_type="gmail",
        display_name=address,
        credentials_encrypted=encrypt_credentials(json.dumps(credentials)),
        status="active",
    )
    db.add(connection)
    db.flush()  # assigns connection.id before we create Permission rows
    for capability in GMAIL_CAPABILITIES:
        db.add(Permission(connection_id=connection.id, capability=capability, allowed=False))
    db.commit()

    result = {"gmail_connected": connection.display_name}
    # Allowed (handy while moving from IMAP to Gmail), but the agent now sees
    # the mailbox twice, so the dashboard warns about it.
    imap_twin = next((c for c, _ in same_address if c.service_type == "imap"), None)
    if imap_twin:
        result["also_imap"] = imap_twin.display_name
    return _back_to_dashboard(**result)
