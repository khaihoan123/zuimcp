"""Capability enforcement: the core promise that an agent can only do what the
user granted, on the user's own accounts."""

import pytest

from core.net import UnsafeHostError
from services.email.providers import imap
from services.email.providers.imap import _resolve_connection, get_mailbox
from tests.conftest import make_connection, make_user


def test_granted_capability_returns_credentials():
    user = make_user("alice")
    make_connection(user, granted=("email.read",))

    creds = _resolve_connection(user, None, "email.read")

    assert creds["email"] == "work@example.com"


def test_ungranted_capability_is_refused():
    user = make_user("alice")
    make_connection(user, granted=("email.read",))

    with pytest.raises(ValueError, match="email.send"):
        _resolve_connection(user, None, "email.send")


def test_cannot_reach_another_users_connection():
    alice = make_user("alice")
    bob = make_user("bob")
    bob_conn = make_connection(bob, name="Bob", granted=("email.read",))

    with pytest.raises(ValueError):
        _resolve_connection(alice, None, "email.read")
    with pytest.raises(ValueError):
        _resolve_connection(alice, str(bob_conn), "email.read")
    with pytest.raises(ValueError):
        _resolve_connection(alice, "Bob", "email.read")


def test_disabled_connection_is_not_used():
    user = make_user("alice")
    make_connection(user, granted=("email.read",), status="disabled")

    with pytest.raises(ValueError):
        _resolve_connection(user, None, "email.read")


def test_multiple_accounts_require_account_argument():
    user = make_user("alice")
    work = make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.read",))

    with pytest.raises(ValueError, match="`account` is required"):
        _resolve_connection(user, None, "email.read")

    assert _resolve_connection(user, str(work), "email.read")["email"] == "work@example.com"
    assert _resolve_connection(user, "Home", "email.read")["email"] == "home@example.com"


def test_capability_filter_picks_the_permitted_account():
    # Only one of two accounts may send, so no `account` argument is needed —
    # and the send must never fall through to the read-only account.
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.read", "email.send"))

    assert _resolve_connection(user, None, "email.send")["email"] == "home@example.com"


def test_get_mailbox_rechecks_host_before_connecting(monkeypatch):
    # A stored host that now points at a private address (DNS rebinding, or an
    # account saved before the SSRF guard) must be refused at connect time.
    def must_not_connect(*args, **kwargs):
        raise AssertionError("connected to a private address")

    monkeypatch.setattr(imap, "MailBox", must_not_connect)
    user = make_user("alice")
    make_connection(user, granted=("email.read",), host="127.0.0.1")

    with pytest.raises(UnsafeHostError):
        get_mailbox(user, None, "email.read")
