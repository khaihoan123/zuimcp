"""Who is calling: the user must come from the verified access token, and a
missing user must never fall back to the local stdio mailbox over HTTP."""

from types import SimpleNamespace

import pytest

from core import mcp_auth


def with_token(monkeypatch, claims):
    token = SimpleNamespace(claims=claims) if claims is not None else None
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)


def test_user_comes_from_token_sub(monkeypatch):
    with_token(monkeypatch, {"sub": "user-123"})
    assert mcp_auth.get_user_id() == "user-123"


@pytest.mark.parametrize("claims", [None, {}, {"sub": ""}])
def test_no_user_over_http_is_refused(monkeypatch, claims):
    with_token(monkeypatch, claims)
    monkeypatch.setattr(mcp_auth, "STDIO_MODE", False)
    with pytest.raises(PermissionError, match="Not signed in"):
        mcp_auth.get_user_id()


def test_no_user_is_local_only_in_stdio_mode(monkeypatch):
    with_token(monkeypatch, None)
    monkeypatch.setattr(mcp_auth, "STDIO_MODE", True)
    assert mcp_auth.get_user_id() is None


def test_http_call_without_user_never_opens_the_env_mailbox(monkeypatch):
    # The failure this guards against: an HTTP caller whose user couldn't be
    # read was treated as the single stdio user and got the .env mailbox.
    from services.email import service

    with_token(monkeypatch, None)
    monkeypatch.setattr(mcp_auth, "STDIO_MODE", False)
    monkeypatch.setattr(
        "services.email.providers.imap.get_email_credentials",
        lambda *a, **k: pytest.fail("fell back to the .env mailbox"),
    )
    with pytest.raises(PermissionError):
        service._provider(None, None, "email.read")
