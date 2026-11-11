"""Google OAuth for connecting mailboxes (Gmail), with plain httpx.

This is a different Google OAuth client from Better Auth's "Sign in with
Google" (GOOGLE_CLIENT_ID): that one only proves who the user is, this one
asks for access to a mailbox. Keeping them apart means the sign-in screen never
asks for mail access, and the Gmail client can go through Google's app
verification on its own.
"""

import time

import httpx

from core.config import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# The one scope we ask for. It covers list/search (with q=), reading messages
# and attachments, labels, marking read/unread, drafts and sending — but not
# permanent deletion. Narrower scopes don't fit: marking a message read
# (messages.modify) accepts only gmail.modify or full mail.google.com access.
# It is a Google "restricted" scope: see "Gmail mailboxes" in DEPLOY.md.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

HTTP_TIMEOUT = 20


class GoogleOAuthError(Exception):
    """A token request Google refused, or that never reached Google."""

    def __init__(self, message: str, error_code: str = ""):
        super().__init__(message)
        # Google's `error` field, e.g. "invalid_grant" for a revoked or
        # expired refresh token. Empty for network errors.
        self.error_code = error_code


def gmail_oauth_settings() -> dict | None:
    """client_id, client_secret and redirect_uri, or None if Gmail isn't set up."""
    client_id = config.get("GMAIL_OAUTH_CLIENT_ID") or ""
    client_secret = config.get("GMAIL_OAUTH_CLIENT_SECRET") or ""
    redirect_uri = config.get("GMAIL_OAUTH_REDIRECT_URI") or ""
    if not (client_id and client_secret and redirect_uri):
        return None
    return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


def http_client() -> httpx.Client:
    """The HTTP client for every Google call. Tests replace this with one on an
    httpx.MockTransport, so no test ever reaches Google."""
    return httpx.Client(timeout=HTTP_TIMEOUT)


def authorization_url(settings: dict, state: str, code_challenge: str, login_hint: str = "") -> str:
    params = {
        "client_id": settings["client_id"],
        "redirect_uri": settings["redirect_uri"],
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        # offline + consent: Google only returns a refresh token on a consent
        # screen, and without one the connection would die within the hour.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return str(httpx.URL(AUTH_URL, params=params))


def _token_request(client: httpx.Client, data: dict) -> dict:
    try:
        res = client.post(TOKEN_URL, data=data)
    except httpx.HTTPError as e:
        raise GoogleOAuthError(f"Could not reach Google: {e.__class__.__name__}") from e
    try:
        body = res.json()
    except ValueError:
        body = {}
    if res.status_code != 200 or "access_token" not in body:
        code = body.get("error", "") if isinstance(body, dict) else ""
        raise GoogleOAuthError(f"Google token request failed ({res.status_code} {code})", code)
    return body


def exchange_code(client: httpx.Client, settings: dict, code: str, code_verifier: str) -> dict:
    return _token_request(
        client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
            "redirect_uri": settings["redirect_uri"],
        },
    )


def refresh_access_token(client: httpx.Client, settings: dict, refresh_token: str) -> dict:
    return _token_request(
        client,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
        },
    )


def expires_at(token_response: dict) -> int:
    """Unix time the access token in a token response expires."""
    return int(time.time()) + int(token_response.get("expires_in", 3600))
