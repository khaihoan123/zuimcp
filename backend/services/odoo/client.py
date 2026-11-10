"""Talking to an Odoo server over HTTP, for Odoo 8 to 19+.

Two API styles, picked from the server version when the account is connected:

- "json2" (Odoo 19+): POST /json/2/<model>/<method>, the API key as a bearer
  token, the database in the X-Odoo-Database header, and the method's keyword
  arguments as the JSON body ("ids" for record methods). See
  odoo/addons/rpc/controllers/json2.py in Odoo 19.
- "jsonrpc" (Odoo 8 to 18): the classic external API, POST /jsonrpc with
  common.authenticate once (login + API key -> uid), then object.execute_kw.
  Odoo 19 still has it but deprecates it (removal planned for Odoo 22).
  API keys exist since Odoo 14; on 8-13 the "key" is the user's password.

Old versions differ in a few calls, handled below with `self.major`:
create() takes one dict everywhere (a list only works from 12),
search_count() takes the domain by position (named `args` on 8-11),
read_group() has "field:agg" aggregates only from 12, and ir.model's
"is transient" field is `osv_memory` on 8/9. Checked against the 8.0-17.0
sources on github.com/odoo/odoo and the local 18/19 sources.

Only the handful of ORM methods the tools need are exposed here — there is
deliberately no "call any method" entry point.

Every request re-checks that the Odoo host is public (SSRF guard, like email)
and never follows redirects, since a redirect could point at an internal host.
"""

import itertools
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.config import config
from core.net import MAIL_CONNECT_TIMEOUT, ensure_public_host

# Odoo 19 is the first release with the JSON-2 API.
JSON2_MIN_VERSION = 19
# Odoo 8 is the first with /jsonrpc and search_read; 7 and older (OpenERP) differ.
OLDEST_SUPPORTED_VERSION = 8
# Before 12, read_group can't pick the aggregate function per field.
READ_GROUP_AGG_MIN_VERSION = 12

# Seconds. Connecting is quick or broken; a big search_read can take a while.
TIMEOUT = httpx.Timeout(60, connect=MAIL_CONNECT_TIMEOUT)

# Tests swap this for an httpx.MockTransport. None = real network.
TRANSPORT: httpx.BaseTransport | None = None

# Odoo model names are dotted lowercase identifiers (res.partner, sale.order).
# Checked because the model becomes part of the URL path in JSON-2: anything
# else ("../web/...") could reach a different endpoint.
MODEL_NAME = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
# read_group specs: "partner_id", "date_order:month" / "amount_total:sum".
GROUPBY_SPEC = re.compile(r"^[a-z0-9_]+(:(day|week|month|quarter|year))?$")
AGGREGATE_SPEC = re.compile(r"^[a-z0-9_]+:(sum|avg|min|max|count|count_distinct)$")

# Since Odoo 18 API keys expire, and Odoo's "New API Key" dialog preselects
# 1 day — so an expired key is the most likely reason a working account stops.
AUTH_FAILED = (
    "Odoo refused the API key or password. Keys can expire (Odoo 18 and newer): "
    "create a new one in Odoo and reconnect this account in the ZuiMCP dashboard."
)

_request_ids = itertools.count(1)


class OdooError(Exception):
    """An error safe to show to the agent (Odoo's own message, no traceback)."""


class OdooAuthError(OdooError):
    """The database, login or API key was refused."""


class OdooVersionError(OdooError):
    """The server runs an Odoo version older than OLDEST_SUPPORTED_VERSION."""


def normalize_url(url: str) -> str:
    """'https://shop.example.com/' -> 'https://shop.example.com'.

    Plain http is only accepted when private hosts are allowed (self-hosting on
    a LAN): otherwise the API key would cross the internet unencrypted.
    """
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    allowed_schemes = ("https", "http") if config.get("ALLOW_PRIVATE_MAIL_HOSTS", False) else ("https",)
    if parts.scheme not in allowed_schemes:
        raise ValueError("The Odoo URL must start with https://")
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Enter the Odoo URL only, like https://mycompany.odoo.com")
    try:
        parts.port  # raises on a non-numeric port
    except ValueError:
        raise ValueError("The Odoo URL has an invalid port") from None
    return url


def host_and_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def check_model_name(model: str) -> str:
    if not MODEL_NAME.match(model or ""):
        raise OdooError(f"'{model}' is not a valid Odoo model name (expected e.g. res.partner)")
    return model


def _post(url: str, payload: dict, headers: dict | None = None) -> httpx.Response:
    """One POST, after re-checking the host. Network errors become OdooError
    with a generic message: the raw exception can carry internal details."""
    ensure_public_host(*host_and_port(url), what="Odoo server")
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=TRANSPORT) as http:
            return http.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise OdooError("Odoo did not answer in time") from None
    except httpx.HTTPError:
        raise OdooError("Could not reach the Odoo server") from None


_NOT_JSON = object()


def _json_body(response: httpx.Response):
    """The decoded body, or _NOT_JSON (e.g. an HTML page from a proxy). A
    sentinel, not None, because `null` is a valid JSON answer."""
    try:
        return response.json()
    except ValueError:
        return _NOT_JSON


def _jsonrpc(base_url: str, path: str, params: dict):
    """A JSON-RPC 2.0 call (classic /jsonrpc, and /web/webclient/version_info)."""
    response = _post(
        base_url + path,
        {"jsonrpc": "2.0", "method": "call", "params": params, "id": next(_request_ids)},
    )
    body = _json_body(response)
    if response.status_code != 200 or not isinstance(body, dict):
        raise OdooError(f"Odoo answered with HTTP {response.status_code}")
    if "error" in body:
        error = body["error"] or {}
        data = error.get("data") or {}
        name = data.get("name") or ""
        message = data.get("message") or error.get("message") or "Odoo returned an error"
        if name.endswith("AccessDenied"):
            raise OdooAuthError(AUTH_FAILED)
        raise OdooError(_short(message))
    return body.get("result")


def _short(message: str, limit: int = 500) -> str:
    message = str(message).strip()
    return message if len(message) <= limit else message[:limit] + "…"


def detect_version(base_url: str) -> tuple[int, str]:
    """(major, version string) of the server, e.g. (19, '19.0').

    /web/webclient/version_info is served by the always-installed `web` module
    with auth="none" on every version from 8 to 19, so it needs neither a database nor a
    login. Its server_version_info is release.version_info, e.g.
    [19, 0, 0, 'final', 0, 'e']; Odoo Online "saas~18.3" reports major 18.
    """
    info = _jsonrpc(base_url, "/web/webclient/version_info", {})
    try:
        major = int(info["server_version_info"][0])
        version = str(info.get("server_version") or major)
    except (TypeError, KeyError, IndexError, ValueError):
        raise OdooError("This does not look like an Odoo server") from None
    return major, version


class OdooClient:
    """One Odoo account. Build it with connect() (new account) or
    from_credentials() (a stored one)."""

    def __init__(self, url: str, database: str, login: str, api_key: str, api_style: str,
                 uid: int | None = None, major: int | None = None):
        if api_style not in ("json2", "jsonrpc"):
            raise ValueError(f"Unknown Odoo API style '{api_style}'")
        self.url = url
        self.database = database
        self.login = login
        self.api_key = api_key
        self.api_style = api_style
        self.uid = uid
        # Unknown (older stored credentials) -> assume a current release.
        self.major = major or (JSON2_MIN_VERSION if api_style == "json2" else 18)

    @classmethod
    def from_credentials(cls, creds: dict) -> "OdooClient":
        return cls(
            url=creds["url"],
            database=creds["database"],
            login=creds["login"],
            api_key=creds["api_key"],
            api_style=creds["api_style"],
            uid=creds.get("uid"),
            major=creds.get("major"),
        )

    @classmethod
    def connect(cls, url: str, database: str, login: str, api_key: str) -> tuple["OdooClient", dict]:
        """Detects the version, checks the login works, and returns the client
        plus the credentials dict to store (encrypted) for later calls."""
        url = normalize_url(url)
        major, version = detect_version(url)
        if major < OLDEST_SUPPORTED_VERSION:
            raise OdooVersionError(f"Odoo {version} is not supported (Odoo {OLDEST_SUPPORTED_VERSION} or newer is needed)")
        api_style = "json2" if major >= JSON2_MIN_VERSION else "jsonrpc"
        client = cls(url, database, login, api_key, api_style, major=major)
        client.authenticate()
        creds = {
            "url": url,
            "database": database,
            "login": client.login,
            "api_key": api_key,
            "uid": client.uid,
            "version": version,
            "major": major,
            "api_style": api_style,
        }
        return client, creds

    # --- transport -----------------------------------------------------------

    def authenticate(self) -> int:
        """Checks the credentials and sets self.uid.

        JSON-2 has no login step: the API key alone identifies the user, so ask
        Odoo who that is (res.users.context_get returns the uid) and keep the
        login Odoo reports. Classic: common.authenticate(db, login, key).
        """
        if self.api_style == "json2":
            context = self._json2("res.users", "context_get")
            uid = context.get("uid") if isinstance(context, dict) else None
            if not uid:
                raise OdooAuthError("Odoo refused the API key")
            users = self._json2("res.users", "read", ids=[uid], fields=["login"])
            if users:
                self.login = users[0].get("login") or self.login
        else:
            uid = _jsonrpc(
                self.url, "/jsonrpc",
                {"service": "common", "method": "authenticate",
                 "args": [self.database, self.login, self.api_key, {}]},
            )
            if not uid:
                raise OdooAuthError("Odoo refused the login or API key")
        self.uid = uid
        return uid

    def _json2(self, model: str, method: str, **kwargs) -> Any:
        response = _post(
            f"{self.url}/json/2/{check_model_name(model)}/{method}",
            kwargs,
            headers={
                "Authorization": f"bearer {self.api_key}",
                "X-Odoo-Database": self.database,
            },
        )
        body = _json_body(response)
        if response.status_code == 200 and body is not _NOT_JSON:
            return body
        if response.status_code == 401:
            raise OdooAuthError(AUTH_FAILED)
        # Odoo's JSON-2 error body: {"name": "odoo.exceptions.AccessError",
        # "message": "...", "arguments": [...], "debug": "<traceback>"}.
        # Only the message is passed on, never the traceback.
        if isinstance(body, dict) and body.get("message"):
            raise OdooError(_short(body["message"]))
        raise OdooError(f"Odoo answered with HTTP {response.status_code}")

    def _execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        if self.uid is None:
            self.authenticate()
        return _jsonrpc(
            self.url, "/jsonrpc",
            {"service": "object", "method": "execute_kw",
             "args": [self.database, self.uid, self.api_key, check_model_name(model), method,
                      args, kwargs or {}]},
        )

    # --- the ORM methods the tools use ----------------------------------------
    # Classic execute_kw passes record ids as the first positional argument and
    # create()'s value list positionally too (odoo.api.call_kw inspects args[0]);
    # JSON-2 takes everything by keyword, ids included.

    def search_read(self, model: str, domain: list, fields: list[str], offset: int = 0,
                    limit: int | None = None, order: str | None = None) -> list[dict]:
        kwargs = {"domain": domain, "fields": fields, "offset": offset, "limit": limit}
        if order:
            kwargs["order"] = order
        if self.api_style == "json2":
            return self._json2(model, "search_read", **kwargs)
        return self._execute_kw(model, "search_read", [], kwargs)

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict]:
        if self.api_style == "json2":
            return self._json2(model, "read", ids=ids, fields=fields)
        return self._execute_kw(model, "read", [ids], {"fields": fields})

    def fields_get(self, model: str, attributes: list[str]) -> dict[str, dict]:
        if self.api_style == "json2":
            return self._json2(model, "fields_get", attributes=attributes)
        return self._execute_kw(model, "fields_get", [], {"attributes": attributes})

    def list_models(self, search: str, offset: int, limit: int) -> list[dict]:
        """Non-transient models, optionally matching `search` in name or label."""
        transient = "transient" if self.major >= 10 else "osv_memory"
        domain: list = [[transient, "=", False]]
        if search:
            domain = ["&", *domain, "|", ["model", "ilike", search], ["name", "ilike", search]]
        return self.search_read("ir.model", domain, ["model", "name"], offset=offset,
                                limit=limit, order="model")

    def create(self, model: str, values: dict) -> int:
        if self.api_style == "json2":
            ids = self._json2(model, "create", vals_list=[values])
        else:
            # One dict, not a list: lists only work from Odoo 12, and a dict
            # gives back the new id on every version.
            ids = self._execute_kw(model, "create", [values])
        return ids[0] if isinstance(ids, list) else ids

    def write(self, model: str, ids: list[int], values: dict) -> bool:
        if self.api_style == "json2":
            return self._json2(model, "write", ids=ids, vals=values)
        return self._execute_kw(model, "write", [ids, values])

    def unlink(self, model: str, ids: list[int]) -> bool:
        if self.api_style == "json2":
            return self._json2(model, "unlink", ids=ids)
        return self._execute_kw(model, "unlink", [ids])

    def search_count(self, model: str, domain: list) -> int:
        if self.api_style == "json2":
            return self._json2(model, "search_count", domain=domain)
        return self._execute_kw(model, "search_count", [domain])

    def read_group(self, model: str, domain: list, groupby: list[str], aggregates: list[str],
                   limit: int | None = None, order: str | None = None) -> list[dict]:
        """Groups with their record count and totals, the same shape on every version:
        [{"partner_id": [3, "Azure"], "count": 2, "amount_total:sum": 100.0}, ...]

        Odoo 19 has formatted_read_group (read_group still exists but is
        deprecated, and is gone in later versions). 12-18 only have read_group,
        whose aggregates are named "alias:agg(field)" so each comes back under
        a key we chose; lazy=False groups by every field at once. 8-11 only
        take plain field names and total each with the field's own function
        (a sum for amounts), so only ":sum" is accepted there.
        """
        for spec in groupby:
            if not GROUPBY_SPEC.match(spec):
                raise OdooError(f"Invalid group by '{spec}' (expected e.g. partner_id or date_order:month)")
        for spec in aggregates:
            if not AGGREGATE_SPEC.match(spec):
                raise OdooError(f"Invalid aggregate '{spec}' (expected e.g. amount_total:sum)")

        if self.api_style == "json2":
            kwargs = {"domain": domain, "groupby": groupby,
                      "aggregates": [*aggregates, "__count"], "limit": limit}
            if order:
                kwargs["order"] = order
            rows = self._json2(model, "formatted_read_group", **kwargs)
            keys = {spec: spec for spec in aggregates}
        else:
            if self.major < READ_GROUP_AGG_MIN_VERSION:
                for spec in aggregates:
                    if not spec.endswith(":sum"):
                        raise OdooError(f"Odoo {self.major} can only sum fields in groups, not '{spec}'")
                keys = {spec: spec.split(":")[0] for spec in aggregates}
                fields = list(keys.values())
            else:
                keys = {spec: spec.replace(":", "__") for spec in aggregates}
                fields = []
                for spec, alias in keys.items():
                    field, agg = spec.split(":")
                    fields.append(f"{alias}:{agg}({field})")
            for spec, key in keys.items():
                order = order.replace(spec, key) if order else order
            rows = self._execute_kw(
                model, "read_group", [],
                {"domain": domain, "fields": fields, "groupby": groupby, "limit": limit,
                 "orderby": order or False, "lazy": False},
            )
        return [
            {
                **{spec: row.get(spec) for spec in groupby},
                "count": row.get("__count"),
                **{spec: row.get(key) for spec, key in keys.items()},
            }
            for row in rows
        ]
