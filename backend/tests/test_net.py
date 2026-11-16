import re
from pathlib import Path

import pytest

from core import net
from core.net import UnsafeHostError, ensure_public_host


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata endpoint
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "fd00::1",  # IPv6 unique local
        # IPv6 forms wrapping an internal IPv4 address, which Python's
        # is_global calls global (see Mastodon CVE-2026-47389 / -72916):
        "::127.0.0.1",  # IPv4-compatible loopback
        "::a9fe:a9fe",  # IPv4-compatible 169.254.169.254
        "64:ff9b::7f00:1",  # NAT64 loopback
        "64:ff9b::a9fe:a9fe",  # NAT64 metadata endpoint
        "2002:a9fe:a9fe::",  # 6to4 metadata endpoint
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    ],
)
def test_blocks_non_public_addresses(host):
    with pytest.raises(UnsafeHostError):
        ensure_public_host(host, 993)


@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700::1111", "64:ff9b::808:808"])
def test_allows_public_address(host):
    assert ensure_public_host(host, 993) == host


def test_blocks_hostname_with_any_private_record(monkeypatch):
    # One public and one private A record must still be refused.
    def fake_getaddrinfo(host, port, proto=0):
        return [(2, 1, 6, "", ("93.184.216.34", port)), (2, 1, 6, "", ("10.0.0.7", port))]

    monkeypatch.setattr(net.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeHostError):
        ensure_public_host("mixed.example", 993)


def test_unresolvable_host_is_refused(monkeypatch):
    def fake_getaddrinfo(host, port, proto=0):
        raise net.socket.gaierror("not found")

    monkeypatch.setattr(net.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeHostError, match="Could not resolve"):
        ensure_public_host("nope.invalid", 993)


def test_opt_out_allows_private_addresses(monkeypatch):
    monkeypatch.setitem(net.config, "ALLOW_PRIVATE_MAIL_HOSTS", True)
    ensure_public_host("192.168.1.10", 993)


# --- every connection to a user-supplied server goes through core.net ---------

BACKEND = Path(__file__).resolve().parents[1]
# Code that opens connections itself. Anything else must use core.net.
ALLOWED = {
    "core/net.py": "the one place that checks, pins and verifies",
    # Fixed Google hosts (oauth2.googleapis.com, gmail.googleapis.com), never user input.
    "services/email/providers/google_oauth.py": "Google only",
    "services/email/providers/gmail.py": "Google only",
    # httpx client whose every request goes through core.net.public_http_target.
    "services/odoo/client.py": "pinned via public_http_target",
}
DIRECT_CONNECTION = re.compile(
    r"(?<!\w)MailBox\(|IMAP4(_SSL)?\(|smtplib\.SMTP(_SSL)?\(|socket\.create_connection\("
    r"|httpx\.(Async)?Client\(|httpx\.(get|post|put|patch|delete|request|stream)\("
)


def test_connections_to_user_servers_go_through_core_net():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/", "alembic/")) or rel in ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT_CONNECTION.search(line):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, (
        "Connect to user-supplied servers with core.net (connect_public, PublicMailBox, "
        "PublicSMTP, public_http_target), or add the file to ALLOWED with a reason:\n"
        + "\n".join(offenders)
    )


def test_odoo_client_pins_every_request():
    source = (BACKEND / "services/odoo/client.py").read_text(encoding="utf-8")
    assert source.count("httpx.Client(") == 1 and "public_http_target(" in source
