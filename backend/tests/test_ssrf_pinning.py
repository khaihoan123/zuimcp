"""DNS rebinding: Odoo requests must go to the exact address the SSRF guard
checked, not to whatever a second DNS lookup returns."""

import socket

import httpx
import pytest

from core import net
from services.odoo import client as odoo_client
from core.net import pin_to_address


def fake_dns(monkeypatch, *addresses):
    monkeypatch.setitem(net.config, "ALLOW_PRIVATE_MAIL_HOSTS", False)
    monkeypatch.setattr(
        net.socket, "getaddrinfo",
        lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (a, port)) for a in addresses],
    )


def test_guard_returns_the_checked_address(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34")
    assert net.ensure_public_host("odoo.example.com", 443) == "93.184.216.34"


def test_guard_still_refuses_any_private_answer(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34", "10.0.0.5")
    with pytest.raises(net.UnsafeHostError):
        net.ensure_public_host("odoo.example.com", 443)


@pytest.mark.parametrize("url, address, expected_url, expected_host", [
    ("https://odoo.example.com", "93.184.216.34", "https://93.184.216.34", "odoo.example.com"),
    ("https://odoo.example.com:8443/jsonrpc", "93.184.216.34", "https://93.184.216.34:8443/jsonrpc",
     "odoo.example.com:8443"),
    ("https://odoo.example.com/jsonrpc", "2606:2800:220:1::1", "https://[2606:2800:220:1::1]/jsonrpc",
     "odoo.example.com"),
])
def test_pin_to_address(url, address, expected_url, expected_host):
    pinned_url, headers, extensions = pin_to_address(url, address, {"X-Odoo-Database": "shop"})
    assert pinned_url == expected_url
    assert headers == {"X-Odoo-Database": "shop", "Host": expected_host}
    assert extensions == {"sni_hostname": "odoo.example.com"}


def test_no_pinning_when_private_hosts_are_allowed():
    assert pin_to_address("https://odoo.lan", None, None) == ("https://odoo.lan", {}, {})


def test_odoo_request_connects_to_the_checked_address(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": True})

    monkeypatch.setattr(net, "ensure_public_host", lambda host, port, what="": "93.184.216.34")
    monkeypatch.setattr(odoo_client, "TRANSPORT", httpx.MockTransport(handler))

    odoo_client._post("https://odoo.example.com/jsonrpc", {"x": 1})

    request = seen[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "odoo.example.com"
    assert request.extensions["sni_hostname"] == "odoo.example.com"
