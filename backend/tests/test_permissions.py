"""Capability and account enforcement: the core promise that an agent can only
do what the user granted, on exactly the account it named."""

import json

import pytest

from core.accounts import granted_capabilities, list_user_accounts, resolve_account
from core.db import SessionLocal
from core.net import UnsafeHostError
from core.security import encrypt_credentials
from models import Connection
from services.email.providers import imap
from services.email.providers.imap import get_mailbox
from tests.conftest import make_connection, make_user


def email_of(user, account, capability):
    return resolve_account(user, "email", account, capability).credentials["email"]


def test_granted_capability_returns_credentials():
    user = make_user("alice")
    make_connection(user, granted=("email.read",))

    assert email_of(user, None, "email.read") == "work@example.com"


def test_ungranted_capability_is_refused():
    user = make_user("alice")
    make_connection(user, granted=("email.read",))

    with pytest.raises(ValueError, match="email.send"):
        resolve_account(user, "email", None, "email.send")


def test_cannot_reach_another_users_connection():
    alice = make_user("alice")
    bob = make_user("bob")
    bob_conn = make_connection(bob, name="Bob", granted=("email.read",))

    with pytest.raises(ValueError):
        resolve_account(alice, "email", None, "email.read")
    with pytest.raises(ValueError):
        resolve_account(alice, "email", str(bob_conn), "email.read")
    with pytest.raises(ValueError):
        resolve_account(alice, "email", "Bob", "email.read")


def test_disabled_connection_is_not_used():
    user = make_user("alice")
    make_connection(user, granted=("email.read",), status="disabled")

    with pytest.raises(ValueError):
        resolve_account(user, "email", None, "email.read")


def test_multiple_accounts_require_account_argument():
    user = make_user("alice")
    work = make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.read",))

    with pytest.raises(ValueError, match="`account` is required"):
        resolve_account(user, "email", None, "email.read")

    assert email_of(user, str(work), "email.read") == "work@example.com"
    assert email_of(user, "Home", "email.read") == "home@example.com"
    assert email_of(user, "home", "email.read") == "home@example.com"  # name is case-insensitive


def test_account_is_never_guessed_even_if_only_one_may_send():
    # Only Home may send, but the agent didn't say which account. It must ask,
    # not quietly send from Home — the user may have meant Work.
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.read", "email.send"))

    with pytest.raises(ValueError, match="`account` is required") as err:
        resolve_account(user, "email", None, "email.send")
    assert "Home" in str(err.value) and "Work" not in str(err.value)  # only valid choices listed


def test_named_account_without_capability_does_not_fall_back():
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.read", "email.send"))

    with pytest.raises(ValueError, match="'Work' does not allow 'email.send'"):
        resolve_account(user, "email", "Work", "email.send")


def test_duplicate_names_need_the_id():
    user = make_user("alice")
    first = make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Work", granted=("email.read",))

    with pytest.raises(ValueError, match="Pass the account id"):
        resolve_account(user, "email", "Work", "email.read")
    assert resolve_account(user, "email", str(first), "email.read").id == first


def test_other_services_accounts_are_ignored():
    # An Odoo (or any non-email) account must never be picked as a mailbox.
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    session = SessionLocal()
    try:
        session.add(
            Connection(
                user_id=user,
                service_type="odoo",
                display_name="Shop",
                credentials_encrypted=encrypt_credentials(json.dumps({})),
            )
        )
        session.commit()
    finally:
        session.close()

    # Still exactly one email account, so no `account` needed.
    assert email_of(user, None, "email.read") == "work@example.com"
    with pytest.raises(ValueError, match="No active email account matches"):
        resolve_account(user, "email", "Shop", "email.read")


def test_list_accounts_shows_grants_and_address():
    user = make_user("alice")
    work = make_connection(user, name="Work", granted=("email.read", "email.send"))
    make_connection(user, name="Old", granted=("email.read",), status="disabled")

    assert list_user_accounts(user) == [
        {
            "account": str(work),
            "name": "Work",
            "service": "email",
            "provider": "imap",
            "allowed": ["email.read", "email.send"],
            "address": "work@example.com",
        }
    ]


def test_granted_capabilities_unions_active_accounts():
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Home", granted=("email.send",))
    make_connection(user, name="Old", granted=("email.draft",), status="disabled")

    assert granted_capabilities(user) == {"email.read", "email.send"}


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


# --- accounts that need reconnecting -----------------------------------------


def set_status(connection_id: int, status: str) -> None:
    session = SessionLocal()
    try:
        session.get(Connection, connection_id).status = status
        session.commit()
    finally:
        session.close()


def test_broken_account_still_makes_account_required():
    # One of two mailboxes needs reconnecting. Without `account`, the working
    # one must not be picked silently: the user may have meant the broken one.
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.send",))
    home = make_connection(user, name="Home", granted=("email.send",))
    set_status(home, "needs_reauth")

    with pytest.raises(ValueError, match="`account` is required") as err:
        resolve_account(user, "email", None, "email.send")
    assert "Work (account: " in str(err.value)
    assert f"Home (account: {home}) — needs reconnecting" in str(err.value)
    assert email_of(user, "Work", "email.send") == "work@example.com"


def test_only_account_needing_reconnect_says_so():
    user = make_user("alice")
    work = make_connection(user, name="Work", granted=("email.read",))
    set_status(work, "needs_reauth")

    with pytest.raises(ValueError, match="'Work' needs to be reconnected"):
        resolve_account(user, "email", None, "email.read")


def test_disabled_account_still_does_not_count():
    # Disabled on purpose by the user: ignored, as before.
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_connection(user, name="Old", granted=("email.read",), status="disabled")

    assert email_of(user, None, "email.read") == "work@example.com"


def test_broken_odoo_account_also_makes_account_required():
    from tests.test_odoo import make_odoo_connection

    user = make_user("alice")
    make_odoo_connection(user, name="Shop", granted=("odoo.read",))
    test_db = make_odoo_connection(user, name="Test shop", granted=("odoo.read",))
    set_status(test_db, "needs_reauth")

    with pytest.raises(ValueError, match="`account` is required"):
        resolve_account(user, "odoo", None, "odoo.read")
    with pytest.raises(ValueError, match="'Test shop' needs to be reconnected"):
        resolve_account(user, "odoo", "Test shop", "odoo.read")
    assert resolve_account(user, "odoo", "Shop", "odoo.read").name == "Shop"

