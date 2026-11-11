"""Gmail mailboxes through the Gmail REST API (plain httpx, no Google libraries).

Mapping to the shared email vocabulary:
- folder -> Gmail label. Matched by label name (case-insensitive) or label id;
  system labels (INBOX, SENT, DRAFT, ...) have the same name and id.
- uid -> Gmail message id. A message id works in any label, so `folder` is
  not needed to find a message, only to list.

Access tokens last an hour. They are refreshed lazily, a few minutes before
they expire, and the new token is saved back (encrypted) on the connection.
If Google refuses the refresh token (revoked by the user, or expired — which
happens after 7 days while the Google app is in "Testing" mode), the
connection is set to `needs_reauth` and the user has to reconnect it.
"""

import base64
import datetime
import email.utils
import html
import json
import logging
import re
import time
from email.header import decode_header, make_header
from email.message import Message

import httpx
from imap_tools import MailMessage

from core.db import SessionLocal
from services.email.providers.google_oauth import (
    GoogleOAuthError,
    expires_at,
    gmail_oauth_settings,
    http_client,
    refresh_access_token,
)
from core.security import encrypt_credentials
from models import Connection
from services.email.providers.base import EmailProvider, MessageFilter

logger = logging.getLogger(__name__)

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Refresh this many seconds before the access token expires, so a token never
# runs out in the middle of a tool call.
REFRESH_MARGIN = 300

# Labels whose id equals their name, so they need no labels.list lookup.
SYSTEM_LABELS = {"INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "UNREAD"}

# messages.batchModify takes at most 1000 ids per call.
BATCH_MODIFY_MAX = 1000

# Gmail message ids are short alphanumeric strings (in practice hex).
MESSAGE_ID = re.compile(r"[0-9a-zA-Z]+")


def _message_id(uid: str) -> str:
    """Rejects anything that isn't a Gmail message id. The id comes from the
    agent and goes into the URL path, so without this "../settings/filters"
    would reach another Gmail endpoint, and "x?format=..." would add a query."""
    if not isinstance(uid, str) or not MESSAGE_ID.fullmatch(uid):
        raise ValueError(
            f"'{uid}' is not a valid Gmail message id. Use the `uid` from email_list or email_search."
        )
    return uid


def save_credentials(connection_id: int, credentials: dict) -> None:
    db = SessionLocal()
    try:
        connection = db.get(Connection, connection_id)
        if connection is not None:
            connection.credentials_encrypted = encrypt_credentials(json.dumps(credentials))
            db.commit()
    finally:
        db.close()


def mark_needs_reauth(connection_id: int) -> None:
    db = SessionLocal()
    try:
        connection = db.get(Connection, connection_id)
        if connection is not None:
            connection.status = "needs_reauth"
            db.commit()
    finally:
        db.close()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _decode_header(value: str) -> str:
    """Decodes RFC 2047 words (=?utf-8?...?=) if Gmail left any in a header."""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _utc_midnight(day: datetime.date) -> int:
    return int(datetime.datetime.combine(day, datetime.time(), tzinfo=datetime.timezone.utc).timestamp())


def _quote(value: str) -> str:
    """A value for a Gmail search operator (subject:"..."). Gmail search has no
    escape for a double quote inside quotes, so those are dropped."""
    return '"' + value.replace('"', " ").strip() + '"'


def search_query(f: MessageFilter) -> str:
    """The Gmail `q=` string for a filter. The folder goes in labelIds instead."""
    parts = []
    if f.unseen_only:
        parts.append("is:unread")
    # Gmail reads a plain date (after:2026/01/31) in US Pacific time, so pass
    # Unix seconds at UTC midnight instead. `after:` is exclusive, hence the -1.
    if f.since:
        parts.append(f"after:{_utc_midnight(f.since) - 1}")
    if f.before:
        parts.append(f"before:{_utc_midnight(f.before)}")
    if f.subject:
        parts.append(f"subject:{_quote(f.subject)}")
    if f.sender:
        parts.append(f"from:{_quote(f.sender)}")
    if f.query:
        # Free text is passed as is, so Gmail's own operators keep working.
        parts.append(f.query)
    return " ".join(parts)


class GmailProvider(EmailProvider):
    def __init__(self, connection_id: int, account_name: str, credentials: dict,
                 client: httpx.Client | None = None):
        super().__init__(account_name, credentials["email"])
        self._connection_id = connection_id
        self._creds = dict(credentials)
        self._client = client or http_client()
        self._labels: list[dict] | None = None

    def close(self) -> None:
        self._client.close()

    # --- tokens and requests ------------------------------------------------

    def _refresh(self) -> None:
        settings = gmail_oauth_settings()
        if settings is None:
            raise ValueError("Gmail is not configured on this server (GMAIL_OAUTH_CLIENT_ID is not set)")
        try:
            token = refresh_access_token(self._client, settings, self._creds["refresh_token"])
        except GoogleOAuthError as e:
            if e.error_code == "invalid_grant":
                mark_needs_reauth(self._connection_id)
                raise ValueError(
                    f"The Gmail account '{self.account_name}' needs to be reconnected: Google no "
                    f"longer accepts its saved login (access was removed, or it expired). The "
                    f"user can reconnect it in the ZuiMCP dashboard."
                ) from None
            logger.warning("Gmail token refresh failed for connection %s: %s", self._connection_id, e)
            raise ValueError("Could not refresh the Gmail login right now. Try again shortly.") from None
        self._creds["access_token"] = token["access_token"]
        self._creds["expires_at"] = expires_at(token)
        if token.get("scope"):
            self._creds["scope"] = token["scope"]
        # Google does not usually rotate refresh tokens, but keep a new one if sent.
        if token.get("refresh_token"):
            self._creds["refresh_token"] = token["refresh_token"]
        save_credentials(self._connection_id, self._creds)

    def _access_token(self) -> str:
        if self._creds.get("expires_at", 0) - time.time() < REFRESH_MARGIN:
            self._refresh()
        return self._creds["access_token"]

    def _request(self, method: str, path: str, *, allow_404: bool = False, **kwargs):
        """Calls the Gmail API and returns the decoded JSON (None for an empty
        body, or for a 404 when allow_404 is set)."""
        res = None
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            try:
                res = self._client.request(method, API_BASE + path, headers=headers, **kwargs)
            except httpx.HTTPError as e:
                raise ValueError(f"Could not reach Gmail: {e.__class__.__name__}") from None
            if res.status_code == 401 and attempt == 0:
                # The token was rejected before its expiry time (e.g. revoked):
                # refresh once and retry. A refused refresh marks needs_reauth.
                self._creds["expires_at"] = 0
                continue
            break
        if res.status_code == 404 and allow_404:
            return None
        if res.is_error:
            try:
                message = res.json()["error"]["message"]
            except Exception:
                message = res.reason_phrase
            raise ValueError(f"Gmail API error {res.status_code}: {message}")
        return res.json() if res.content else None

    # --- labels -------------------------------------------------------------

    def _all_labels(self) -> list[dict]:
        if self._labels is None:
            self._labels = self._request("GET", "/labels").get("labels", [])
        return self._labels

    def _label_id(self, folder: str) -> str:
        if folder.upper() in SYSTEM_LABELS:
            return folder.upper()
        for label in self._all_labels():
            if label["id"] == folder or label["name"].casefold() == folder.casefold():
                return label["id"]
        raise ValueError(
            f"This Gmail account has no label named '{folder}'. "
            f"Call email_list_folders to see the label names."
        )

    def list_folders(self) -> list[str]:
        labels = sorted(
            self._all_labels(),
            key=lambda l: (l["id"] != "INBOX", l.get("type") != "system", l["name"].casefold()),
        )
        return [l["name"] for l in labels]

    # --- messages -----------------------------------------------------------

    def list_messages(self, f: MessageFilter) -> list[dict]:
        if f.limit <= 0:
            return []
        label = self._label_id(f.folder)
        q = search_query(f)
        wanted = f.offset + f.limit

        # Gmail pages with tokens, not offsets: collect ids up to offset+limit,
        # then skip the first `offset`. Results come newest first.
        ids: list[str] = []
        page_token = None
        while len(ids) < wanted:
            params = {"labelIds": label, "maxResults": min(500, wanted - len(ids))}
            if label in ("SPAM", "TRASH"):
                params["includeSpamTrash"] = "true"
            if q:
                params["q"] = q
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", "/messages", params=params)
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        results = []
        for message_id in ids[f.offset : wanted]:
            m = self._request(
                "GET",
                f"/messages/{_message_id(message_id)}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                allow_404=True,
            )
            if m is None:
                continue  # deleted between list and get
            results.append(self._summary(m, f.with_snippet))
        return results

    @staticmethod
    def _summary(m: dict, with_snippet: bool) -> dict:
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        date = None
        if headers.get("date"):
            try:
                date = email.utils.parsedate_to_datetime(headers["date"]).isoformat()
            except (TypeError, ValueError):
                date = None
        item = {
            "uid": m["id"],
            # Address only, like IMAP's `from` (imap_tools' MailMessage.from_).
            "from": email.utils.parseaddr(headers.get("from", ""))[1],
            "subject": _decode_header(headers.get("subject", "")),
            "date": date,
            "seen": "UNREAD" not in m.get("labelIds", []),
        }
        if with_snippet:
            # Gmail computes a short plain-text preview itself, so this costs no
            # body download. It comes HTML-escaped (&#39; etc.).
            item["snippet"] = html.unescape(m.get("snippet", ""))[:200]
        return item

    def fetch_message(self, uid: str, folder: str) -> MailMessage | None:
        m = self._request("GET", f"/messages/{_message_id(uid)}", params={"format": "raw"},
                          allow_404=True)
        if m is None:
            return None
        # Parsing the raw message with the same parser as IMAP keeps email_read
        # and email_get_attachment identical across providers.
        return MailMessage.from_bytes(_b64url_decode(m["raw"]))

    def save_draft(self, msg: Message) -> str:
        self._request("POST", "/drafts", json={"message": {"raw": _b64url_encode(msg.as_bytes())}})
        return "DRAFT"

    def send(self, msg: Message) -> None:
        self._request("POST", "/messages/send", json={"raw": _b64url_encode(msg.as_bytes())})

    def mark_seen(self, uids: list[str], folder: str, seen: bool) -> None:
        change = "removeLabelIds" if seen else "addLabelIds"
        uids = [_message_id(u) for u in uids]  # all checked before any change is made
        for i in range(0, len(uids), BATCH_MODIFY_MAX):
            self._request(
                "POST",
                "/messages/batchModify",
                json={"ids": uids[i : i + BATCH_MODIFY_MAX], change: ["UNREAD"]},
            )
