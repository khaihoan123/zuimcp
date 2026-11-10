"""Odoo connector: the client against a fake Odoo (both API styles and version
detection), the tools, account resolution and the connect endpoint.

The fake speaks the request formats checked against the Odoo sources:
JSON-2 in odoo/addons/rpc/controllers/json2.py (19), classic JSON-RPC via
odoo/service/{common,model}.py and odoo.api.call_kw (17/18).
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api import connections
from core.accounts import list_user_accounts, resolve_account
from core.db import SessionLocal
from core.net import UnsafeHostError
from core.security import decrypt_credentials, encrypt_credentials
from main import app
from models import Connection, Permission
from services.odoo import client as odoo_client
from services.odoo import service
from services.odoo.client import (
    OdooAuthError,
    OdooClient,
    OdooError,
    OdooVersionError,
    normalize_url,
)
from tests.conftest import make_connection, make_session_cookie, make_user

URL = "https://odoo.example.com"
DB = "shop"
LOGIN = "admin@example.com"
KEY = "api-key-123"
ODOO_CAPABILITIES = ["odoo.read", "odoo.create", "odoo.write", "odoo.delete"]


class FakeOdoo:
    """Minimal Odoo server. Records every ORM call as (model, method, ids, args, kwargs)."""

    def __init__(self, major: int):
        self.major = major
        self.calls = []
        self.requests = []
        self.search_result = [{"id": 1, "display_name": "Azure Interior"}]
        self.fields = {
            "name": {"type": "char", "string": "Name", "required": True, "relation": False},
            "image_1920": {"type": "binary", "string": "Image"},
            "child_ids": {"type": "one2many", "string": "Contacts", "relation": "res.partner"},
            "country_id": {"type": "many2one", "string": "Country", "relation": "res.country"},
        }
        self.error = None  # (status, json body) to answer every ORM call with

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}")
        path = request.url.path
        if path == "/web/webclient/version_info":
            return self._rpc_result(body, {
                "server_version": f"{self.major}.0",
                "server_version_info": [self.major, 0, 0, "final", 0, "e"],
                "server_serie": f"{self.major}.0",
                "protocol_version": 1,
            })
        if path == "/jsonrpc":  # 19 still serves it (deprecated); 17/18 only have this
            return self._classic(body)
        if path.startswith("/json/2/") and self.major >= 19:
            return self._json2(request, body)
        return httpx.Response(404, text="<html>Not Found</html>")

    # --- JSON-2 ---------------------------------------------------------------

    def _json2(self, request, body):
        if request.headers.get("Authorization") != f"bearer {KEY}":
            return httpx.Response(401, json={"name": "werkzeug.exceptions.Unauthorized",
                                             "message": "Invalid apikey"})
        assert request.headers["X-Odoo-Database"] == DB
        _, _, _, model, method = request.url.path.split("/")
        ids = body.pop("ids", None)
        self.calls.append((model, method, ids, [], body))
        if self.error:
            return httpx.Response(self.error[0], json=self.error[1])
        return httpx.Response(200, json=self._answer(model, method, ids))

    # --- classic JSON-RPC -------------------------------------------------------

    def _classic(self, body):
        params = body["params"]
        if params["service"] == "common" and params["method"] == "authenticate":
            db, login, key, _env = params["args"]
            return self._rpc_result(body, 7 if (db, login, key) == (DB, LOGIN, KEY) else False)
        assert (params["service"], params["method"]) == ("object", "execute_kw")
        db, uid, key, model, method, args, kwargs = params["args"]
        if (db, uid, key) != (DB, 7, KEY):
            return self._rpc_error(body, "odoo.exceptions.AccessDenied", "Access Denied")
        # call_kw: record methods take the ids as args[0], @api.model ones don't.
        ids = args[0] if method in ("read", "write", "unlink") else None
        self.calls.append((model, method, ids, args, kwargs))
        if self.error:
            return self._rpc_error(body, self.error[1]["name"], self.error[1]["message"])
        return self._rpc_result(body, self._answer(model, method, ids))

    @staticmethod
    def _rpc_result(body, result):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    @staticmethod
    def _rpc_error(body, name, message):
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body.get("id"),
            "error": {"code": 200, "message": "Odoo Server Error",
                      "data": {"name": name, "message": message, "debug": "Traceback SECRET"}},
        })

    def _answer(self, model, method, ids):
        if (model, method) == ("res.users", "context_get"):
            return {"lang": "en_US", "tz": "Europe/London", "uid": 7}
        if (model, method) == ("res.users", "read"):
            return [{"id": 7, "login": LOGIN}]
        return {
            "search_read": self.search_result,
            "read": [{"id": i, "name": f"Record {i}"} for i in ids or []],
            "fields_get": self.fields,
            "create": [42],
            "write": True,
            "unlink": True,
            "search_count": 7,
            # Odoo 19: keys are the specs asked for, plus __count / __extra_domain.
            "formatted_read_group": [
                {"partner_id": [3, "Azure"], "amount_total:sum": 100.0, "__count": 2,
                 "__extra_domain": [["partner_id", "=", 3]]},
            ],
            # Odoo 12-18 read_group(lazy=False): aggregates under their alias;
            # 8-11 only take plain field names, so the key is the field.
            "read_group": [
                {"partner_id": [3, "Azure"], "__count": 2, "__domain": [["partner_id", "=", 3]],
                 ("amount_total__sum" if self.major >= 12 else "amount_total"): 100.0},
            ],
        }[method]


@pytest.fixture
def no_dns(monkeypatch):
    """odoo.example.com isn't real: skip the public-host DNS check."""
    monkeypatch.setattr(odoo_client, "ensure_public_host", lambda host, port, what="": None)
    monkeypatch.setattr(connections, "ensure_public_host", lambda host, port, what="": None)


def fake_odoo(monkeypatch, major: int) -> FakeOdoo:
    fake = FakeOdoo(major)
    monkeypatch.setattr(odoo_client, "TRANSPORT", fake.transport())
    return fake


def credentials(major: int) -> dict:
    return {
        "url": URL, "database": DB, "login": LOGIN, "api_key": KEY, "uid": 7,
        "version": f"{major}.0", "major": major, "api_style": "json2" if major >= 19 else "jsonrpc",
    }


def make_odoo_connection(user_id, name="Shop", granted=(), major=19, url=URL) -> int:
    session = SessionLocal()
    try:
        connection = Connection(
            user_id=user_id,
            service_type="odoo",
            display_name=name,
            credentials_encrypted=encrypt_credentials(json.dumps({**credentials(major), "url": url})),
            status="active",
        )
        session.add(connection)
        session.flush()
        for capability in ODOO_CAPABILITIES:
            session.add(Permission(connection_id=connection.id, capability=capability,
                                   allowed=capability in granted))
        session.commit()
        return connection.id
    finally:
        session.close()


# --- version detection and login --------------------------------------------


@pytest.mark.parametrize("major, style", [
    (8, "jsonrpc"), (11, "jsonrpc"), (12, "jsonrpc"), (13, "jsonrpc"), (14, "jsonrpc"),
    (17, "jsonrpc"), (18, "jsonrpc"), (19, "json2"), (20, "json2"),
])
def test_connect_picks_api_style_from_version(monkeypatch, no_dns, major, style):
    fake_odoo(monkeypatch, major)

    client, creds = OdooClient.connect(URL + "/", DB, LOGIN, KEY)

    assert client.uid == 7
    assert creds == {**credentials(major), "version": f"{major}.0", "api_style": style}


def test_old_odoo_is_refused(monkeypatch, no_dns):
    fake_odoo(monkeypatch, 7)  # OpenERP 7 and older have a different API
    with pytest.raises(OdooVersionError, match="7.0 is not supported"):
        OdooClient.connect(URL, DB, LOGIN, KEY)


def test_non_odoo_server_is_refused(monkeypatch, no_dns):
    monkeypatch.setattr(odoo_client, "TRANSPORT",
                        httpx.MockTransport(lambda r: httpx.Response(200, text="<html>hi</html>")))
    with pytest.raises(OdooError):
        OdooClient.connect(URL, DB, LOGIN, KEY)


@pytest.mark.parametrize("major", [18, 19])
def test_wrong_api_key_is_an_auth_error(monkeypatch, no_dns, major):
    fake_odoo(monkeypatch, major)
    with pytest.raises(OdooAuthError):
        OdooClient.connect(URL, DB, LOGIN, "wrong-key")


def test_json2_keeps_the_login_odoo_reports(monkeypatch, no_dns):
    # JSON-2 has no login step: the key alone says who the user is.
    fake_odoo(monkeypatch, 19)
    _, creds = OdooClient.connect(URL, DB, "typo@example.com", KEY)
    assert creds["login"] == LOGIN


# --- request formats -----------------------------------------------------------


def test_json2_request_format(monkeypatch, no_dns):
    fake = fake_odoo(monkeypatch, 19)
    client = OdooClient.from_credentials(credentials(19))

    client.search_read("res.partner", [["name", "ilike", "a"]], ["name"], limit=5, order="name")
    client.read("res.partner", [1, 2], ["name"])
    client.create("res.partner", {"name": "Jane"})
    client.write("res.partner", [3], {"name": "Joan"})
    client.unlink("res.partner", [4])

    request = fake.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{URL}/json/2/res.partner/search_read"
    assert request.headers["Authorization"] == f"bearer {KEY}"
    assert request.headers["X-Odoo-Database"] == DB
    assert fake.calls == [
        ("res.partner", "search_read", None, [],
         {"domain": [["name", "ilike", "a"]], "fields": ["name"], "offset": 0, "limit": 5, "order": "name"}),
        ("res.partner", "read", [1, 2], [], {"fields": ["name"]}),
        ("res.partner", "create", None, [], {"vals_list": [{"name": "Jane"}]}),
        ("res.partner", "write", [3], [], {"vals": {"name": "Joan"}}),
        ("res.partner", "unlink", [4], [], {}),
    ]


def test_classic_request_format(monkeypatch, no_dns):
    fake = fake_odoo(monkeypatch, 18)
    client = OdooClient.from_credentials(credentials(18))

    client.search_read("res.partner", [], ["name"], limit=5)
    client.read("res.partner", [1, 2], ["name"])
    assert client.create("res.partner", {"name": "Jane"}) == 42
    client.write("res.partner", [3], {"name": "Joan"})
    client.unlink("res.partner", [4])

    assert str(fake.requests[0].url) == f"{URL}/jsonrpc"
    assert "Authorization" not in fake.requests[0].headers
    assert fake.calls == [
        ("res.partner", "search_read", None, [], {"domain": [], "fields": ["name"], "offset": 0, "limit": 5}),
        ("res.partner", "read", [1, 2], [[1, 2]], {"fields": ["name"]}),
        # One dict, positionally: works on every version (lists only from 12).
        ("res.partner", "create", None, [{"name": "Jane"}], {}),
        ("res.partner", "write", [3], [[3], {"name": "Joan"}], {}),
        ("res.partner", "unlink", [4], [[4]], {}),
    ]


def test_count_and_read_group_request_formats(monkeypatch, no_dns):
    json2 = fake_odoo(monkeypatch, 19)
    client = OdooClient.from_credentials(credentials(19))
    assert client.search_count("sale.order", [["state", "=", "sale"]]) == 7
    groups19 = client.read_group("sale.order", [], ["partner_id"], ["amount_total:sum"],
                                 limit=10, order="amount_total:sum desc")
    assert json2.calls == [
        ("sale.order", "search_count", None, [], {"domain": [["state", "=", "sale"]]}),
        ("sale.order", "formatted_read_group", None, [],
         {"domain": [], "groupby": ["partner_id"], "aggregates": ["amount_total:sum", "__count"],
          "limit": 10, "order": "amount_total:sum desc"}),
    ]

    classic = fake_odoo(monkeypatch, 18)
    client = OdooClient.from_credentials(credentials(18))
    assert client.search_count("sale.order", [["state", "=", "sale"]]) == 7
    groups18 = client.read_group("sale.order", [], ["partner_id"], ["amount_total:sum"],
                                 limit=10, order="amount_total:sum desc")
    assert classic.calls == [
        # By position: the argument is named `args`, not `domain`, on Odoo 8-11.
        ("sale.order", "search_count", None, [[["state", "=", "sale"]]], {}),
        # "alias:agg(field)" so the total comes back under a key we chose.
        ("sale.order", "read_group", None, [],
         {"domain": [], "fields": ["amount_total__sum:sum(amount_total)"], "groupby": ["partner_id"],
          "limit": 10, "orderby": "amount_total__sum desc", "lazy": False}),
    ]

    # Same answer shape on every version.
    expected = [{"partner_id": [3, "Azure"], "count": 2, "amount_total:sum": 100.0}]
    assert groups19 == groups18 == expected


@pytest.mark.parametrize("major", [8, 11])
def test_read_group_on_odoo_8_to_11_sums_plain_fields(monkeypatch, no_dns, major):
    fake = fake_odoo(monkeypatch, major)
    client = OdooClient.from_credentials(credentials(major))

    groups = client.read_group("sale.order", [], ["partner_id"], ["amount_total:sum"],
                               order="amount_total:sum desc")

    assert fake.calls[-1][4]["fields"] == ["amount_total"]
    assert fake.calls[-1][4]["orderby"] == "amount_total desc"
    assert groups == [{"partner_id": [3, "Azure"], "count": 2, "amount_total:sum": 100.0}]
    with pytest.raises(OdooError, match="can only sum"):
        client.read_group("sale.order", [], ["partner_id"], ["amount_total:avg"])


@pytest.mark.parametrize("major, field", [(8, "osv_memory"), (9, "osv_memory"), (10, "transient"), (19, "transient")])
def test_list_models_uses_the_versions_transient_field(monkeypatch, no_dns, major, field):
    fake = fake_odoo(monkeypatch, major)
    OdooClient.from_credentials(credentials(major)).list_models("sale", offset=0, limit=10)
    model, method, _, _, kwargs = fake.calls[-1]
    assert (model, method) == ("ir.model", "search_read")
    assert kwargs["domain"][1] == [field, "=", False]


def test_stored_accounts_without_major_still_work(monkeypatch, no_dns):
    # Credentials saved before `major` was stored.
    fake_odoo(monkeypatch, 18)
    creds = {k: v for k, v in credentials(18).items() if k != "major"}
    assert OdooClient.from_credentials(creds).create("res.partner", {"name": "Jane"}) == 42


@pytest.mark.parametrize("groupby, aggregates", [
    (["partner_id:hour"], []),
    (["partner_id desc"], []),
    ([], ["amount_total"]),
    ([], ["amount_total:median"]),
    ([], ["x:sum(amount_total)"]),
])
def test_read_group_rejects_bad_specs_before_any_request(monkeypatch, no_dns, groupby, aggregates):
    fake = fake_odoo(monkeypatch, 18)
    client = OdooClient.from_credentials(credentials(18))
    with pytest.raises(OdooError, match="Invalid"):
        client.read_group("sale.order", [], groupby, aggregates)
    assert fake.requests == []


@pytest.mark.parametrize("major", [18, 19])
def test_expired_key_says_so(monkeypatch, no_dns, major):
    fake_odoo(monkeypatch, major)
    client = OdooClient.from_credentials({**credentials(major), "api_key": "expired"})
    with pytest.raises(OdooAuthError, match="expire"):
        client.read("res.partner", [1], ["name"])


@pytest.mark.parametrize("major", [18, 19])
def test_odoo_errors_pass_the_message_but_not_the_traceback(monkeypatch, no_dns, major):
    fake = fake_odoo(monkeypatch, major)
    fake.error = (403, {"name": "odoo.exceptions.AccessError",
                        "message": "You are not allowed to access 'Contact'",
                        "debug": "Traceback SECRET"})
    client = OdooClient.from_credentials(credentials(major))

    with pytest.raises(OdooError, match="not allowed") as err:
        client.read("res.partner", [1], ["name"])
    assert "SECRET" not in str(err.value)


@pytest.mark.parametrize("model", ["../web/database/drop", "res.partner/unlink", "Res.Partner", ""])
def test_invalid_model_names_are_refused_before_any_request(monkeypatch, no_dns, model):
    fake = fake_odoo(monkeypatch, 19)
    client = OdooClient.from_credentials(credentials(19))
    with pytest.raises(OdooError, match="not a valid Odoo model"):
        client.read(model, [1], ["name"])
    assert fake.requests == []


def test_redirects_are_not_followed(monkeypatch, no_dns):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(303, headers={"Location": "http://169.254.169.254/latest"})

    monkeypatch.setattr(odoo_client, "TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(OdooError, match="HTTP 303"):
        OdooClient.from_credentials(credentials(19)).read("res.partner", [1], ["name"])
    assert seen == ["odoo.example.com"]


def test_every_call_rechecks_the_host(monkeypatch):
    # A stored URL that now resolves to a private address must be refused
    # before any request is sent (DNS rebinding).
    fake = fake_odoo(monkeypatch, 19)
    client = OdooClient.from_credentials({**credentials(19), "url": "https://127.0.0.1"})
    with pytest.raises(UnsafeHostError, match="Odoo server"):
        client.read("res.partner", [1], ["name"])
    assert fake.requests == []


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://shop.odoo.com/", "https://shop.odoo.com"),
        ("  https://shop.example.com:8443 ", "https://shop.example.com:8443"),
        ("http://shop.example.com", ValueError),  # API key would travel unencrypted
        ("shop.odoo.com", ValueError),
        ("https://user:pw@shop.odoo.com", ValueError),
        ("https://shop.odoo.com/odoo?debug=1", ValueError),
        ("ftp://shop.odoo.com", ValueError),
    ],
)
def test_normalize_url(url, expected):
    if expected is ValueError:
        with pytest.raises(ValueError):
            normalize_url(url)
    else:
        assert normalize_url(url) == expected


def test_http_allowed_when_private_hosts_are(monkeypatch):
    monkeypatch.setitem(odoo_client.config, "ALLOW_PRIVATE_MAIL_HOSTS", True)
    assert normalize_url("http://odoo.lan:8069") == "http://odoo.lan:8069"


# --- accounts ---------------------------------------------------------------


def test_odoo_and_email_accounts_never_mix():
    user = make_user("alice")
    make_connection(user, name="Work", granted=("email.read",))
    shop = make_odoo_connection(user, name="Shop", granted=("odoo.read",))

    assert resolve_account(user, "odoo", None, "odoo.read").id == shop
    assert resolve_account(user, "email", None, "email.read").name == "Work"
    with pytest.raises(ValueError, match="No active odoo account matches"):
        resolve_account(user, "odoo", "Work", "odoo.read")


def test_several_odoo_accounts_require_the_account_argument():
    user = make_user("alice")
    make_odoo_connection(user, name="Live", granted=("odoo.read",))
    test_db = make_odoo_connection(user, name="Test", granted=("odoo.read", "odoo.write"))

    with pytest.raises(ValueError, match="`account` is required") as err:
        resolve_account(user, "odoo", None, "odoo.write")
    assert "Test" in str(err.value) and "Live" not in str(err.value)
    with pytest.raises(ValueError, match="'Live' does not allow 'odoo.write'"):
        resolve_account(user, "odoo", "Live", "odoo.write")
    assert resolve_account(user, "odoo", "test", "odoo.write").id == test_db


def test_list_accounts_shows_odoo_details_but_never_the_key():
    user = make_user("alice")
    shop = make_odoo_connection(user, name="Shop", granted=("odoo.read",))

    assert list_user_accounts(user) == [
        {
            "account": str(shop),
            "name": "Shop",
            "service": "odoo",
            "provider": "odoo",
            "allowed": ["odoo.read"],
            "url": URL,
            "database": DB,
            "login": LOGIN,
            "version": "19.0",
        }
    ]
    assert KEY not in json.dumps(list_user_accounts(user))


# --- tools ------------------------------------------------------------------


@pytest.fixture
def as_user(monkeypatch):
    def login(user_id):
        monkeypatch.setattr(service, "get_user_id", lambda ctx: user_id)
        return user_id
    return login


@pytest.mark.parametrize("major", [18, 19])
def test_search_tool_is_compact_and_pages(monkeypatch, no_dns, as_user, major):
    fake = fake_odoo(monkeypatch, major)
    fake.search_result = [{"id": i, "display_name": "x" * 5000} for i in range(4)]
    user = as_user(make_user("alice"))
    make_odoo_connection(user, granted=("odoo.read",), major=major)

    result = service.odoo_search(None, "res.partner", limit=3)

    assert result["account"] == "Shop"
    assert result["has_more"] is True and len(result["records"]) == 3
    assert len(result["records"][0]["display_name"]) < 2100
    model, method, _, _, kwargs = fake.calls[0]
    assert (model, method) == ("res.partner", "search_read")
    assert kwargs["fields"] == ["display_name"] and kwargs["limit"] == 4  # one extra, for has_more


def test_read_tool_skips_binary_and_x2many_by_default(monkeypatch, no_dns, as_user):
    fake = fake_odoo(monkeypatch, 19)
    user = as_user(make_user("alice"))
    make_odoo_connection(user, granted=("odoo.read",))

    service.odoo_read(None, "res.partner", [1])

    assert fake.calls[-1][4] == {"fields": ["country_id", "name"]}


def test_count_and_read_group_tools(monkeypatch, no_dns, as_user):
    fake = fake_odoo(monkeypatch, 19)
    user = as_user(make_user("alice"))
    make_odoo_connection(user, granted=("odoo.read",))

    assert service.odoo_count(None, "sale.order") == {"account": "Shop", "model": "sale.order", "count": 7}
    result = service.odoo_read_group(None, "sale.order", groupby=["partner_id"],
                                     aggregates=["amount_total:sum"], limit=5)
    assert result["groups"] == [{"partner_id": [3, "Azure"], "count": 2, "amount_total:sum": 100.0}]
    assert result["has_more"] is False
    assert fake.calls[-1][4]["limit"] == 6  # one extra, for has_more


def test_get_fields_tool_drops_empty_attributes(monkeypatch, no_dns, as_user):
    fake_odoo(monkeypatch, 19)
    user = as_user(make_user("alice"))
    make_odoo_connection(user, granted=("odoo.read",))

    result = service.odoo_get_fields(None, "res.partner", fields=["name"])

    assert result["fields"] == {"name": {"type": "char", "string": "Name", "required": True}}


@pytest.mark.parametrize("tool, args, capability", [
    (service.odoo_create, ("res.partner", {"name": "Jane"}), "odoo.create"),
    (service.odoo_update, ("res.partner", [1], {"name": "Jane"}), "odoo.write"),
    (service.odoo_delete, ("res.partner", [1]), "odoo.delete"),
])
def test_write_tools_need_their_own_capability(monkeypatch, no_dns, as_user, tool, args, capability):
    fake = fake_odoo(monkeypatch, 19)
    user = as_user(make_user("alice"))
    # Everything except the one capability this tool needs.
    make_odoo_connection(user, granted=tuple(c for c in ODOO_CAPABILITIES if c != capability))

    with pytest.raises(ValueError, match=capability):
        tool(None, *args)
    assert fake.requests == []


def test_write_tools_report_the_account_used(monkeypatch, no_dns, as_user):
    fake_odoo(monkeypatch, 19)
    user = as_user(make_user("alice"))
    make_odoo_connection(user, name="Live", granted=("odoo.read",))
    make_odoo_connection(user, name="Test", granted=tuple(ODOO_CAPABILITIES))

    with pytest.raises(ValueError, match="`account` is required"):
        service.odoo_create(None, "res.partner", {"name": "Jane"})

    assert service.odoo_create(None, "res.partner", {"name": "Jane"}, account="Test") == {
        "status": "created", "account": "Test", "model": "res.partner", "id": 42,
    }
    assert service.odoo_update(None, "res.partner", [1], {"name": "J"}, account="Test")["account"] == "Test"
    assert service.odoo_delete(None, "res.partner", [1], account="Test")["account"] == "Test"


def test_tools_refuse_local_stdio_mode(as_user):
    as_user(None)
    with pytest.raises(ValueError, match="signed-in"):
        service.odoo_search(None, "res.partner")


# --- connect endpoint ---------------------------------------------------------

NEW_ODOO = {"display_name": "Shop", "url": URL, "database": DB, "login": LOGIN, "api_key": KEY}


def logged_in(user_id: str) -> TestClient:
    return TestClient(app, cookies=make_session_cookie(user_id))


@pytest.mark.parametrize("major, style", [(18, "jsonrpc"), (19, "json2")])
def test_connect_endpoint_saves_encrypted_and_grants_nothing(db, monkeypatch, no_dns, major, style):
    fake_odoo(monkeypatch, major)
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/odoo", json=NEW_ODOO)

    assert res.status_code == 201
    assert res.json()["service_type"] == "odoo"
    assert sorted(p["capability"] for p in res.json()["permissions"]) == sorted(ODOO_CAPABILITIES)
    assert not any(p["allowed"] for p in res.json()["permissions"])
    stored = db.get(Connection, res.json()["id"])
    assert KEY not in stored.credentials_encrypted
    creds = json.loads(decrypt_credentials(stored.credentials_encrypted))
    assert creds["api_key"] == KEY and creds["api_style"] == style and creds["version"] == f"{major}.0"


def test_connect_endpoint_rejects_a_bad_key(db, monkeypatch, no_dns):
    fake_odoo(monkeypatch, 19)
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/odoo", json={**NEW_ODOO, "api_key": "nope"})

    assert res.status_code == 400
    assert "login failed" in res.json()["detail"]
    assert db.query(Connection).count() == 0


def test_connect_endpoint_refuses_private_hosts_before_connecting(monkeypatch):
    fake = fake_odoo(monkeypatch, 19)
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/odoo", json={**NEW_ODOO, "url": "https://127.0.0.1"})

    assert res.status_code == 400
    assert "not a public address" in res.json()["detail"]
    assert fake.requests == []


def test_connect_endpoint_does_not_leak_server_details(monkeypatch, no_dns):
    monkeypatch.setattr(odoo_client, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(500, text="banner: PostgreSQL 17.2 on db:5432")))
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/odoo", json=NEW_ODOO)

    assert res.status_code == 400
    assert "PostgreSQL" not in res.json()["detail"]


def test_connect_endpoint_requires_https(monkeypatch, no_dns):
    fake = fake_odoo(monkeypatch, 19)
    alice = make_user("alice")

    res = logged_in(alice).post("/api/connections/odoo", json={**NEW_ODOO, "url": "http://odoo.example.com"})

    assert res.status_code == 400
    assert "https" in res.json()["detail"]
    assert fake.requests == []
