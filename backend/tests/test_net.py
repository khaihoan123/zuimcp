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
    ],
)
def test_blocks_non_public_addresses(host):
    with pytest.raises(UnsafeHostError):
        ensure_public_host(host, 993)


def test_allows_public_address():
    ensure_public_host("8.8.8.8", 993)


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
