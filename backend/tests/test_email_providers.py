"""The email tools on both backends: IMAP/SMTP (fake mailbox) and Gmail (fake
Google, on an httpx.MockTransport). Nothing here reaches a real server, and no
real email is ever sent."""

import base64
import datetime
import email
import json
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

import httpx
import pytest
from imap_tools import MailMessageFlags

from core.accounts import Account, list_user_accounts, resolve_account
from core.db import SessionLocal
from core.security import decrypt_credentials, encrypt_credentials
from models import Connection, Permission
from services.email import service
from services.email.providers import gmail, imap, open_provider, provider_for
from services.email.providers.gmail import GmailProvider, search_query
from services.email.providers.imap import ImapProvider
from services.email.providers.base import MessageFilter
from tests.conftest import CAPABILITIES, make_connection, make_user

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def make_gmail_connection(
    user_id: str,
    address: str = "me@gmail.com",
    granted: tuple[str, ...] = CAPABILITIES,
    status: str = "active",
    expires_in: int = 3600,
    name: str | None = None,
) -> int:
    credentials = {
        "email": address,
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_at": int(time.time()) + expires_in,
        "scope": "https://www.googleapis.com/auth/gmail.modify",
    }
    session = SessionLocal()
    try:
        connection = Connection(
            user_id=user_id,
            service_type="gmail",
            display_name=name or address,
            credentials_encrypted=encrypt_credentials(json.dumps(credentials)),
            status=status,
        )
        session.add(connection)
        session.flush()
        for capability in CAPABILITIES:
            session.add(
                Permission(connection_id=connection.id, capability=capability, allowed=capability in granted)
            )
        session.commit()
        return connection.id
    finally:
        session.close()


def stored(connection_id: int) -> tuple[str, dict]:
    session = SessionLocal()
    try:
        c = session.get(Connection, connection_id)
        return c.status, json.loads(decrypt_credentials(c.credentials_encrypted))
    finally:
        session.close()


@pytest.fixture
def as_user(monkeypatch):
    """Makes the tools see `user_id` as the caller (normally the OAuth token's sub)."""

    def set_user(user_id):
        monkeypatch.setattr(service, "_get_user_id", lambda ctx: user_id)
        return SimpleNamespace()  # stands in for the fastmcp Context

    return set_user


# --- fake Google -------------------------------------------------------------


class FakeGoogle:
    """Answers Gmail and token-endpoint calls; records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}

    def route(self, method: str, url: str, response):
        self.routes[(method, url)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url.copy_with(query=None))
        handler = self.routes.get((request.method, url))
        if handler is None:
            return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
        return handler(request) if callable(handler) else handler

    def calls(self, method: str, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and str(r.url.copy_with(query=None)) == url]


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(gmail, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    from api import gmail_oauth

    monkeypatch.setattr(gmail_oauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    from core.config import config

    monkeypatch.setitem(config, "GMAIL_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setitem(config, "GMAIL_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setitem(config, "GMAIL_OAUTH_REDIRECT_URI", "http://testserver/api/connections/gmail/oauth/callback")
    return fake


def sample_raw_message() -> bytes:
    msg = MIMEMultipart("mixed")
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "me@gmail.com"
    msg["Subject"] = "Quarterly report"
    msg["Date"] = "Mon, 02 Nov 2026 10:00:00 +0000"
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Hello there", "plain"))
    alt.attach(MIMEText("<p>Hello there</p>", "html"))
    msg.attach(alt)
    pdf = MIMEApplication(b"%PDF-1.4 fake", "pdf")
    pdf.add_header("Content-Disposition", "attachment", filename="report.pdf")
    msg.attach(pdf)
    return msg.as_bytes()


def metadata(message_id: str, subject: str, unread: bool = False) -> dict:
    return {
        "id": message_id,
        "labelIds": ["INBOX"] + (["UNREAD"] if unread else []),
        "snippet": "It&#39;s ready",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 02 Nov 2026 10:00:00 +0000"},
            ]
        },
    }


# --- provider selection ------------------------------------------------------


def test_provider_is_picked_by_service_type(google):
    user = make_user("alice")
    imap_id = make_connection(user, name="Work", granted=("email.read",))
    gmail_id = make_gmail_connection(user, granted=("email.read",))

    with open_provider(user, str(imap_id), "email.read") as p:
        assert isinstance(p, ImapProvider)
        assert p.address == "work@example.com"
    with open_provider(user, str(gmail_id), "email.read") as p:
        assert isinstance(p, GmailProvider)
        assert (p.account_name, p.address) == ("me@gmail.com", "me@gmail.com")


def test_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="not supported"):
        provider_for(Account(id=1, name="x", service_type="outlook", credentials={}))


def test_gmail_and_imap_accounts_count_together_for_the_account_rule():
    # One IMAP + one Gmail = two mailboxes, so `account` is required.
    user = make_user("alice")
    make_connection(user, granted=("email.send",))
    make_gmail_connection(user, granted=("email.send",))

    with pytest.raises(ValueError, match="`account` is required"):
        resolve_account(user, "email", None, "email.send")


def test_list_accounts_shows_gmail_address():
    user = make_user("alice")
    conn = make_gmail_connection(user, address="Me@Gmail.com", granted=("email.read",), name="Personal")

    assert list_user_accounts(user) == [
        {
            "account": str(conn),
            "name": "Personal",
            "service": "email",
            "provider": "gmail",
            "allowed": ["email.read"],
            "address": "Me@Gmail.com",
        }
    ]


# --- IMAP through the provider layer -----------------------------------------


class FakeImapMessage(SimpleNamespace):
    pass


class FakeMailBox:
    """Just enough of imap_tools.MailBox for the IMAP provider."""

    instances: list["FakeMailBox"] = []

    def __init__(self, *args, **kwargs):
        self.selected = None
        self.appended = []
        self.flagged = []
        self.logged_out = False
        self.messages = [
            FakeImapMessage(uid=str(i), from_=f"s{i}@example.com", subject=f"Subject {i}",
                            date=datetime.datetime(2026, 11, i), flags=(MailMessageFlags.SEEN,) if i % 2 else (),
                            text=f"Body {i}", html="")
            for i in range(1, 6)
        ]
        FakeMailBox.instances.append(self)
        outer = self

        class Folder:
            def set(self, name):
                outer.selected = name

            def status(self, name):
                return {"MESSAGES": len(outer.messages)}

            def list(self):
                return [SimpleNamespace(name="INBOX", flags=()),
                        SimpleNamespace(name="INBOX.Drafts", flags=("\\Drafts",))]

        self.folder = Folder()

    def login(self, *args):
        return self

    def logout(self):
        self.logged_out = True

    def uids(self, criteria):
        if isinstance(criteria, str):  # "start:end" sequence range
            start, end = (int(x) for x in criteria.split(":"))
            return [m.uid for m in self.messages[start - 1 : end]]
        return [m.uid for m in self.messages if MailMessageFlags.SEEN not in m.flags]

    def fetch(self, criteria, **kwargs):
        wanted = str(criteria).split("UID ")[1].rstrip(")").split(",")
        found = [m for m in self.messages if m.uid in wanted]
        return reversed(found) if kwargs.get("reverse") else found

    def append(self, data, folder, **kwargs):
        self.appended.append((data, folder, kwargs))

    def flag(self, uids, flag, value):
        self.flagged.append((list(uids), flag, value))


@pytest.fixture
def fake_imap(monkeypatch):
    FakeMailBox.instances = []
    monkeypatch.setattr(imap, "MailBox", FakeMailBox)
    monkeypatch.setattr(imap, "ensure_public_host", lambda host, port: None)
    sent = []
    monkeypatch.setattr(imap, "send_message", lambda creds, msg: sent.append((creds, msg)))
    return SimpleNamespace(sent=sent)


def test_imap_list_pages_newest_first(as_user, fake_imap):
    user = make_user("alice")
    make_connection(user, granted=("email.read",))
    ctx = as_user(user)

    page = service.email_list(ctx, limit=2, offset=1)

    assert [m["uid"] for m in page] == ["4", "3"]
    assert page[0] == {"uid": "4", "from": "s4@example.com", "subject": "Subject 4",
                       "date": "2026-11-04T00:00:00", "seen": False}
    assert FakeMailBox.instances[0].logged_out


def test_imap_search_uses_criteria(as_user, fake_imap):
    user = make_user("alice")
    make_connection(user, granted=("email.search",))
    ctx = as_user(user)

    found = service.email_search(ctx, unseen_only=True, with_snippet=True)

    assert [m["uid"] for m in found] == ["4", "2"]
    assert found[0]["snippet"] == "Body 4"


def test_imap_draft_goes_to_drafts_folder(as_user, fake_imap):
    user = make_user("alice")
    make_connection(user, granted=("email.draft",))
    ctx = as_user(user)

    result = service.email_draft(ctx, to="bob@example.com", subject="Hi", body="Hello")

    assert result["folder"] == "INBOX.Drafts"
    assert result["from"] == "work@example.com"
    data, folder, _ = FakeMailBox.instances[0].appended[0]
    assert email.message_from_bytes(data)["From"] == "work@example.com"


def test_imap_send_uses_smtp_without_opening_imap(as_user, fake_imap):
    user = make_user("alice")
    make_connection(user, granted=("email.send",))
    ctx = as_user(user)

    result = service.email_send(ctx, to="bob@example.com", subject="Hi", body="Hello")

    assert result["status"] == "sent"
    creds, msg = fake_imap.sent[0]
    assert creds["email"] == msg["From"] == "work@example.com"
    assert FakeMailBox.instances == []  # SMTP only


def test_imap_mark_seen(as_user, fake_imap):
    user = make_user("alice")
    make_connection(user, granted=("email.mark_seen",))
    ctx = as_user(user)

    service.email_mark_seen(ctx, uids=["2", "4"], folder="INBOX", seen=True)

    assert FakeMailBox.instances[0].flagged == [(["2", "4"], MailMessageFlags.SEEN, True)]


# --- Gmail through the provider layer ----------------------------------------


def test_gmail_search_query_translation():
    q = search_query(MessageFilter(unseen_only=True, since=datetime.date(2026, 11, 1),
                                   before=datetime.date(2026, 11, 2), subject='The "plan"',
                                   sender="alice@example.com", query="has:attachment"))
    assert q == ('is:unread after:1793491199 before:1793577600 subject:"The  plan" '
                 'from:"alice@example.com" has:attachment')


def test_gmail_list_folders(as_user, google):
    google.route("GET", f"{GMAIL}/labels", httpx.Response(200, json={"labels": [
        {"id": "Label_1", "name": "Clients", "type": "user"},
        {"id": "SENT", "name": "SENT", "type": "system"},
        {"id": "INBOX", "name": "INBOX", "type": "system"},
    ]}))
    user = make_user("alice")
    make_gmail_connection(user)

    assert service.email_list_folders(as_user(user)) == ["INBOX", "SENT", "Clients"]


def test_gmail_list_pages_and_summarises(as_user, google):
    pages = {
        None: {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "m3"}]},
    }
    google.route("GET", f"{GMAIL}/messages",
                 lambda r: httpx.Response(200, json=pages[r.url.params.get("pageToken")]))
    for i in (1, 2, 3):
        google.route("GET", f"{GMAIL}/messages/m{i}",
                     httpx.Response(200, json=metadata(f"m{i}", "=?utf-8?q?Caf=C3=A9?=", unread=i == 2)))
    user = make_user("alice")
    make_gmail_connection(user)

    result = service.email_list(as_user(user), limit=2, offset=1, unseen_only=True, with_snippet=True)

    assert [m["uid"] for m in result] == ["m2", "m3"]
    assert result[0] == {"uid": "m2", "from": "alice@example.com", "subject": "Café",
                         "date": "2026-11-02T10:00:00+00:00", "seen": False, "snippet": "It's ready"}
    first = google.calls("GET", f"{GMAIL}/messages")[0]
    assert first.url.params["labelIds"] == "INBOX"
    assert first.url.params["q"] == "is:unread"
    assert first.url.params["maxResults"] == "3"
    assert google.calls("GET", f"{GMAIL}/messages/m1") == []  # skipped by offset: not fetched


def test_gmail_search_by_user_label(as_user, google):
    google.route("GET", f"{GMAIL}/labels", httpx.Response(200, json={"labels": [
        {"id": "Label_7", "name": "Clients/Acme", "type": "user"}]}))
    google.route("GET", f"{GMAIL}/messages", httpx.Response(200, json={"resultSizeEstimate": 0}))
    user = make_user("alice")
    make_gmail_connection(user)

    assert service.email_search(as_user(user), subject="invoice", folder="clients/acme") == []
    listed = google.calls("GET", f"{GMAIL}/messages")[0]
    assert listed.url.params["labelIds"] == "Label_7"
    assert listed.url.params["q"] == 'subject:"invoice"'

    with pytest.raises(ValueError, match="no label named 'Nope'"):
        service.email_search(as_user(user), subject="x", folder="Nope")


def test_gmail_read_and_attachment(as_user, google):
    raw = base64.urlsafe_b64encode(sample_raw_message()).decode().rstrip("=")
    google.route("GET", f"{GMAIL}/messages/m1", httpx.Response(200, json={"id": "m1", "raw": raw}))
    user = make_user("alice")
    make_gmail_connection(user)
    ctx = as_user(user)

    read = service.email_read(ctx, uid="m1")
    assert read["uid"] == "m1"
    assert read["from"] == "alice@example.com"
    assert read["subject"] == "Quarterly report"
    assert read["body_text"].strip() == "Hello there"
    assert [a["filename"] for a in read["attachments"]] == ["report.pdf"]
    assert google.calls("GET", f"{GMAIL}/messages/m1")[0].url.params["format"] == "raw"

    content = service.email_get_attachment(ctx, uid="m1", index=0)
    meta = json.loads(content.text)
    assert base64.b64decode(meta["data_base64"]) == b"%PDF-1.4 fake"

    assert service.email_read(ctx, uid="gone") == {"error": "No email found with uid=gone in INBOX"}


def test_gmail_draft_and_send(as_user, google):
    google.route("POST", f"{GMAIL}/drafts", httpx.Response(200, json={"id": "d1"}))
    google.route("POST", f"{GMAIL}/messages/send", httpx.Response(200, json={"id": "s1"}))
    user = make_user("alice")
    make_gmail_connection(user, address="me@gmail.com")
    ctx = as_user(user)

    draft = service.email_draft(ctx, to="bob@example.com", subject="Hi", body="Hello")
    sent = service.email_send(ctx, to="bob@example.com", subject="Hi", body="Hello", cc="c@example.com")

    assert (draft["status"], draft["folder"], draft["from"]) == ("draft_saved", "DRAFT", "me@gmail.com")
    assert (sent["status"], sent["from"]) == ("sent", "me@gmail.com")
    draft_raw = json.loads(google.calls("POST", f"{GMAIL}/drafts")[0].content)["message"]["raw"]
    sent_raw = json.loads(google.calls("POST", f"{GMAIL}/messages/send")[0].content)["raw"]
    sent_msg = email.message_from_bytes(base64.urlsafe_b64decode(sent_raw))
    assert email.message_from_bytes(base64.urlsafe_b64decode(draft_raw))["To"] == "bob@example.com"
    assert (sent_msg["From"], sent_msg["Cc"]) == ("me@gmail.com", "c@example.com")


@pytest.mark.parametrize("seen, field", [(True, "removeLabelIds"), (False, "addLabelIds")])
def test_gmail_mark_seen(as_user, google, seen, field):
    google.route("POST", f"{GMAIL}/messages/batchModify", httpx.Response(204))
    user = make_user("alice")
    make_gmail_connection(user)

    service.email_mark_seen(as_user(user), uids=["m1", "m2"], seen=seen)

    body = json.loads(google.calls("POST", f"{GMAIL}/messages/batchModify")[0].content)
    assert body == {"ids": ["m1", "m2"], field: ["UNREAD"]}


@pytest.mark.parametrize("bad_id", ["../settings/filters", "x?format=minimal", "a/b", "", "id with space"])
def test_gmail_rejects_message_ids_that_could_change_the_url(as_user, google, bad_id):
    # The uid comes from the agent and goes into the URL path.
    user = make_user("alice")
    make_gmail_connection(user)
    ctx = as_user(user)

    for call in (lambda: service.email_read(ctx, uid=bad_id),
                 lambda: service.email_get_attachment(ctx, uid=bad_id),
                 lambda: service.email_mark_seen(ctx, uids=["abc123", bad_id])):
        with pytest.raises(ValueError, match="not a valid Gmail message id"):
            call()
    assert google.requests == []  # refused before anything reached Google


def test_gmail_capability_is_still_enforced(as_user, google):
    user = make_user("alice")
    make_gmail_connection(user, granted=("email.read",))

    with pytest.raises(ValueError, match="does not allow 'email.send'"):
        service.email_send(as_user(user), to="bob@example.com", subject="Hi", body="Hello")
    assert google.requests == []


# --- Gmail token refresh -------------------------------------------------------


def token_ok(request):
    return httpx.Response(200, json={"access_token": "access-2", "expires_in": 3599,
                                     "scope": "https://www.googleapis.com/auth/gmail.modify"})


def labels_ok(request):
    return httpx.Response(200, json={"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]})


def test_fresh_token_is_not_refreshed(as_user, google):
    google.route("GET", f"{GMAIL}/labels", labels_ok)
    user = make_user("alice")
    make_gmail_connection(user, expires_in=3600)

    service.email_list_folders(as_user(user))

    assert google.calls("POST", TOKEN_URL) == []
    assert google.calls("GET", f"{GMAIL}/labels")[0].headers["Authorization"] == "Bearer access-1"


def test_token_near_expiry_is_refreshed_and_saved(as_user, google):
    google.route("POST", TOKEN_URL, token_ok)
    google.route("GET", f"{GMAIL}/labels", labels_ok)
    user = make_user("alice")
    conn = make_gmail_connection(user, expires_in=120)  # inside the 5-minute margin

    service.email_list_folders(as_user(user))

    refresh = google.calls("POST", TOKEN_URL)[0]
    assert b"grant_type=refresh_token" in refresh.content and b"refresh_token=refresh-1" in refresh.content
    assert google.calls("GET", f"{GMAIL}/labels")[0].headers["Authorization"] == "Bearer access-2"
    status, creds = stored(conn)
    assert status == "active"
    assert creds["access_token"] == "access-2"
    assert creds["refresh_token"] == "refresh-1"
    assert creds["expires_at"] > time.time() + 3000


def test_rejected_token_is_refreshed_once_and_retried(as_user, google):
    google.route("POST", TOKEN_URL, token_ok)
    google.route("GET", f"{GMAIL}/labels", lambda r: labels_ok(r) if r.headers["Authorization"] == "Bearer access-2"
                 else httpx.Response(401, json={"error": {"message": "Invalid Credentials"}}))
    user = make_user("alice")
    make_gmail_connection(user)

    assert service.email_list_folders(as_user(user)) == ["INBOX"]
    assert len(google.calls("POST", TOKEN_URL)) == 1


def test_revoked_refresh_token_marks_needs_reauth(as_user, google):
    google.route("POST", TOKEN_URL, httpx.Response(400, json={"error": "invalid_grant",
                                                              "error_description": "Token has been expired or revoked."}))
    user = make_user("alice")
    conn = make_gmail_connection(user, expires_in=-10)
    ctx = as_user(user)

    with pytest.raises(ValueError, match="needs to be reconnected"):
        service.email_list_folders(ctx)

    assert stored(conn)[0] == "needs_reauth"
    assert google.calls("GET", f"{GMAIL}/labels") == []
    # No longer active, so it can't be used until the user reconnects — and
    # later calls say so, rather than "no account matches".
    with pytest.raises(ValueError, match="'me@gmail.com' needs to be reconnected"):
        service.email_list_folders(ctx)
    with pytest.raises(ValueError, match="needs to be reconnected"):
        service.email_list_folders(ctx, account=str(conn))


def test_named_account_needing_reauth_says_so_even_with_other_accounts():
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    make_gmail_connection(user, status="needs_reauth", name="Personal")

    with pytest.raises(ValueError, match="'Personal' needs to be reconnected"):
        resolve_account(user, "email", "personal", "email.read")
    with pytest.raises(ValueError, match="No active email account matches 'Other'"):
        resolve_account(user, "email", "Other", "email.read")
    # Not named: Work is the only usable one, but it is never guessed — the
    # user may have meant Personal.
    with pytest.raises(ValueError, match="`account` is required") as err:
        resolve_account(user, "email", None, "email.read")
    assert "Personal (account: " in str(err.value) and "needs reconnecting" in str(err.value)


def test_temporary_refresh_failure_keeps_the_connection_active(as_user, google):
    google.route("POST", TOKEN_URL, httpx.Response(503, text="unavailable"))
    user = make_user("alice")
    conn = make_gmail_connection(user, expires_in=-10)

    with pytest.raises(ValueError, match="Try again shortly"):
        service.email_list_folders(as_user(user))
    assert stored(conn)[0] == "active"
