"""The "Connect with Google" flow: /start and /callback, against a fake Google."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from api import gmail_oauth
from core.config import config
from core.security import encrypt_credentials
from main import app
from models import Connection
from tests.conftest import make_connection, make_session_cookie, make_user
from tests.test_email_providers import GMAIL, TOKEN_URL, google, make_gmail_connection, stored  # noqa: F401

START = "/api/connections/gmail/oauth/start"
CALLBACK = "/api/connections/gmail/oauth/callback"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def browser(user_id: str) -> TestClient:
    """A logged-in browser that doesn't follow redirects, so we can inspect them."""
    return TestClient(app, cookies=make_session_cookie(user_id), follow_redirects=False)


def query_of(response) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(response.headers["location"]).query).items()}


@pytest.fixture
def google_ok(google):
    google.route("POST", TOKEN_URL, httpx.Response(200, json={
        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3599,
        "scope": GMAIL_SCOPE, "token_type": "Bearer"}))
    google.route("GET", f"{GMAIL}/profile", httpx.Response(200, json={"emailAddress": "me@gmail.com"}))
    return google


def connect(client: TestClient, **callback_params):
    """Runs /start, then comes back to /callback the way Google would."""
    started = client.get(START)
    assert started.status_code == 303
    params = {"code": "auth-code", "state": query_of(started)["state"], **callback_params}
    return started, client.get(CALLBACK, params=params)


def gmail_rows(db, user_id):
    db.expire_all()
    return db.query(Connection).filter_by(user_id=user_id, service_type="gmail").all()


# --- /start ------------------------------------------------------------------


def test_start_without_configuration_says_so(monkeypatch):
    monkeypatch.setitem(config, "GMAIL_OAUTH_CLIENT_ID", "")
    user = make_user("alice")

    res = browser(user).get(START)

    assert res.status_code == 303
    assert query_of(res) == {"gmail_error": "not_configured"}


def test_start_requires_login(google):
    res = TestClient(app, follow_redirects=False).get(START)
    assert query_of(res) == {"gmail_error": "not_logged_in"}


def test_start_redirects_to_google_with_pkce_and_offline_access(google):
    user = make_user("alice")

    res = browser(user).get(START)

    url = urlparse(res.headers["location"])
    params = query_of(res)
    assert (url.scheme, url.netloc, url.path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
    assert params["client_id"] == "client-id"
    assert params["redirect_uri"] == "http://testserver/api/connections/gmail/oauth/callback"
    assert params["scope"] == GMAIL_SCOPE
    assert (params["access_type"], params["prompt"]) == ("offline", "consent")
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32
    assert "login_hint" not in params
    cookie = res.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Path=/api/connections/gmail/oauth" in cookie
    assert params["state"] not in cookie  # encrypted, not readable


def test_reconnect_start_hints_the_google_account(google):
    user = make_user("alice")
    conn = make_gmail_connection(user, address="me@gmail.com", status="needs_reauth")

    res = browser(user).get(START, params={"account": conn})

    assert query_of(res)["login_hint"] == "me@gmail.com"


def test_start_never_hints_another_users_account(google):
    alice = make_user("alice")
    bob = make_user("bob")
    bobs = make_gmail_connection(bob, address="bob@gmail.com")

    res = browser(alice).get(START, params={"account": bobs})

    assert "login_hint" not in query_of(res)


# --- /callback ---------------------------------------------------------------


def test_callback_creates_connection_with_nothing_granted(db, google_ok):
    user = make_user("alice")
    client = browser(user)

    started, res = connect(client)

    assert res.status_code == 303
    assert res.headers["location"].startswith("/dashboard/connections?")
    assert query_of(res) == {"gmail_connected": "me@gmail.com"}
    [conn] = gmail_rows(db, user)
    assert conn.status == "active"
    assert sorted(p.capability for p in conn.permissions) == [
        "email.draft", "email.mark_seen", "email.read", "email.search", "email.send"]
    assert not any(p.allowed for p in conn.permissions)
    assert "new-refresh" not in conn.credentials_encrypted
    creds = stored(conn.id)[1]
    assert (creds["email"], creds["access_token"], creds["refresh_token"], creds["scope"]) == (
        "me@gmail.com", "new-access", "new-refresh", GMAIL_SCOPE)
    assert creds["expires_at"] > time.time() + 3000

    # PKCE: the verifier sent to Google matches the challenge from /start.
    sent = {k: v[0] for k, v in parse_qs(google_ok.calls("POST", TOKEN_URL)[0].content.decode()).items()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == query_of(started)["code_challenge"]
    assert (sent["code"], sent["grant_type"]) == ("auth-code", "authorization_code")
    # The one-time state cookie is cleared.
    assert 'zui_gmail_oauth=""' in res.headers["set-cookie"] or "Max-Age=0" in res.headers["set-cookie"]


def test_callback_rejects_wrong_state(db, google_ok):
    user = make_user("alice")

    _, res = connect(browser(user), state="forged")

    assert query_of(res) == {"gmail_error": "invalid_state"}
    assert google_ok.calls("POST", TOKEN_URL) == []
    assert gmail_rows(db, user) == []


def test_callback_without_start_cookie_is_rejected(db, google_ok):
    user = make_user("alice")

    res = browser(user).get(CALLBACK, params={"code": "auth-code", "state": "anything"})

    assert query_of(res) == {"gmail_error": "invalid_state"}
    assert google_ok.calls("POST", TOKEN_URL) == []


def test_callback_rejects_flow_started_by_another_user(db, google_ok):
    # Alice's browser started the flow; then the session belongs to Bob
    # (e.g. a link with Alice's state planted in Bob's browser).
    alice = make_user("alice")
    bob = make_user("bob")
    client = browser(alice)
    started = client.get(START)
    client.cookies.update(make_session_cookie(bob))

    res = client.get(CALLBACK, params={"code": "c", "state": query_of(started)["state"]})

    assert query_of(res) == {"gmail_error": "invalid_state"}
    assert gmail_rows(db, bob) == [] and gmail_rows(db, alice) == []


@pytest.mark.parametrize("age, expected", [(-60, {"gmail_connected": "me@gmail.com"}),
                                           (1, {"gmail_error": "invalid_state"})])
def test_callback_rejects_expired_state(db, google_ok, age, expected):
    # The same hand-made cookie works while fresh, so the expired case really
    # fails on the expiry and not on the cookie itself.
    user = make_user("alice")
    client = browser(user)
    cookie = encrypt_credentials(json.dumps(
        {"state": "s", "verifier": "v" * 50, "user_id": user, "exp": int(time.time()) - age}))
    client.cookies.set(gmail_oauth.STATE_COOKIE, cookie, path=gmail_oauth.PREFIX)

    res = client.get(CALLBACK, params={"code": "c", "state": "s"})

    assert query_of(res) == expected


def test_user_cancelling_on_google(db, google_ok):
    user = make_user("alice")

    _, res = connect(browser(user), error="access_denied", code="")

    assert query_of(res) == {"gmail_error": "access_denied"}
    assert gmail_rows(db, user) == []


def test_callback_requires_the_gmail_scope(db, google):
    google.route("POST", TOKEN_URL, httpx.Response(200, json={
        "access_token": "a", "refresh_token": "r", "expires_in": 3599, "scope": "openid"}))
    user = make_user("alice")

    _, res = connect(browser(user))

    assert query_of(res) == {"gmail_error": "missing_scope"}
    assert gmail_rows(db, user) == []


def test_failed_code_exchange(db, google):
    google.route("POST", TOKEN_URL, httpx.Response(400, json={"error": "invalid_grant"}))
    user = make_user("alice")

    _, res = connect(browser(user))

    assert query_of(res) == {"gmail_error": "token_failed"}


def test_same_address_again_updates_instead_of_duplicating(db, google_ok):
    user = make_user("alice")
    existing = make_gmail_connection(user, address="Me@Gmail.com", granted=("email.read",),
                                     status="needs_reauth", name="Personal")

    _, res = connect(browser(user))

    assert query_of(res) == {"gmail_connected": "Personal", "reconnected": "1"}
    [conn] = gmail_rows(db, user)
    assert conn.id == existing
    assert conn.status == "active"
    assert {p.capability for p in conn.permissions if p.allowed} == {"email.read"}  # grants kept
    assert stored(existing)[1]["refresh_token"] == "new-refresh"


def test_same_address_for_another_user_is_a_separate_connection(db, google_ok):
    alice = make_user("alice")
    bob = make_user("bob")
    make_gmail_connection(bob, address="me@gmail.com")

    connect(browser(alice))

    assert len(gmail_rows(db, alice)) == 1
    assert len(gmail_rows(db, bob)) == 1


def test_same_address_already_on_imap_connects_with_a_warning(db, google_ok):
    user = make_user("alice")
    make_connection(user, name="Me")  # IMAP, address me@example.com
    google_ok.route("GET", f"{GMAIL}/profile", httpx.Response(200, json={"emailAddress": "ME@example.com"}))

    _, res = connect(browser(user))

    assert query_of(res) == {"gmail_connected": "ME@example.com", "also_imap": "Me"}
    assert len(gmail_rows(db, user)) == 1

