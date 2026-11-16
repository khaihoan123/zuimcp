"""Connecting to servers that users type in (mail hosts, Odoo URLs).

Every connection to such a server goes through this module, so the SSRF rules
live in one place:

1. Look the host up once and refuse it unless every address is public
   (ensure_public_host).
2. Connect to exactly that checked address, never to the name again. A second
   DNS lookup could return a private address (DNS rebinding).
3. Keep the real hostname for TLS, and verify the certificate against it.

Use connect_public / PublicMailBox / PublicSMTP / public_http_target rather
than opening sockets, MailBox, smtplib.SMTP or httpx clients to user servers
directly. tests/test_net.py fails if code outside this module does.
"""

import imaplib
import ipaddress
import smtplib
import socket
import ssl
from urllib.parse import urlsplit

from imap_tools import MailBox

from core.config import config

# Seconds to wait when connecting to a user-supplied mail server, so an
# unreachable host can't hang a request or a tool call indefinitely.
MAIL_CONNECT_TIMEOUT = 15


class UnsafeHostError(ValueError):
    pass


# IPv6 ranges that carry an IPv4 address in their last 32 bits. NAT64
# gateways (e.g. AWS IPv6-only subnets) forward them to that IPv4 address.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
# Deprecated "IPv4-compatible" form (::a.b.c.d); nothing public uses it.
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable addresses, including IPv6 forms that wrap
    an IPv4 address: Python's is_global calls ::127.0.0.1 or 64:ff9b::a9fe:a9fe
    (NAT64 for 169.254.169.254) global, so the inner IPv4 address is checked."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return is_public_address(address.ipv4_mapped)
        if address in _IPV4_COMPATIBLE:
            return False
        if any(address in net for net in _NAT64):
            return is_public_address(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        if address.sixtofour:
            return is_public_address(address.sixtofour)
        if address.teredo:
            return False
    return address.is_global


def ensure_public_host(host: str, port: int, what: str = "Mail server") -> str | None:
    """Refuses servers (mail, Odoo) that resolve to private, loopback, link-local
    or other non-public addresses (SSRF guard). Users choose the host, so without
    this anyone who signs up could make the backend probe the internal network
    (e.g. `db:5432`) or cloud metadata endpoints.

    Every resolved address must be public, otherwise a hostname with one public
    and one private record would slip through. Checked at every connect, not
    only when the account is saved, because DNS can change afterwards.

    Self-hosters whose mail or Odoo server lives on their own LAN can set
    ALLOW_PRIVATE_MAIL_HOSTS to true (the name predates Odoo; it covers both).

    `what` only names the server in the error message.

    Returns one of the checked addresses, to connect to instead of resolving
    the name again. None when private hosts are allowed: nothing to pin.
    """
    if config.get("ALLOW_PRIVATE_MAIL_HOSTS", False):
        return None
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise UnsafeHostError(f"Could not resolve {what.lower()} '{host}'")
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not is_public_address(address):
            raise UnsafeHostError(f"{what} '{host}' is not a public address")
        addresses.append(str(address))
    if not addresses:
        raise UnsafeHostError(f"Could not resolve {what.lower()} '{host}'")
    return addresses[0]


def tls_context() -> ssl.SSLContext:
    """Verifies the server certificate and hostname. imaplib and smtplib don't
    by default: without this, anyone on the network path could pose as the
    mail server and read the password."""
    return ssl.create_default_context()


def connect_public(host: str, port: int, timeout: float | None, what: str = "Mail server",
                   check_public: bool = True) -> socket.socket:
    """A TCP connection to `host`, made to the address ensure_public_host checked.

    check_public=False is only for local stdio mode, where the server comes from
    the operator's own .env rather than from a user.
    """
    address = ensure_public_host(host, port, what) if check_public else None
    return socket.create_connection((address or host, port), timeout)


class _PublicIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, *, ssl_context, timeout, check_public: bool):
        self._check_public = check_public  # before __init__, which connects
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = connect_public(self.host, self.port, timeout, check_public=self._check_public)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


class PublicMailBox(MailBox):
    """imap_tools.MailBox over IMAPS, pinned to the checked address and with
    the certificate verified."""

    def __init__(self, host: str, port: int = 993, timeout: float = MAIL_CONNECT_TIMEOUT,
                 check_public: bool = True):
        self._check_public = check_public
        super().__init__(host, port, timeout=timeout, ssl_context=tls_context())

    def _get_mailbox_client(self) -> imaplib.IMAP4:
        return _PublicIMAP4_SSL(self._host, self._port, ssl_context=self._ssl_context,
                                timeout=self._timeout, check_public=self._check_public)


class PublicSMTP(smtplib.SMTP):
    """smtplib.SMTP pinned to the checked address, with STARTTLS verifying the
    certificate against the hostname."""

    def __init__(self, host: str, port: int = 587, timeout: float = MAIL_CONNECT_TIMEOUT,
                 check_public: bool = True):
        self._check_public = check_public  # before __init__, which connects
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return connect_public(host, port, timeout, check_public=self._check_public)

    def starttls(self, *, context=None):
        return super().starttls(context=context or tls_context())


def host_and_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def pin_to_address(url: str, address: str | None, headers: dict | None) -> tuple[str, dict, dict]:
    """The request URL, headers and httpx extensions for connecting to `address`
    instead of resolving the hostname again.

    The hostname still goes in the Host header (Odoo picks the database and
    website from it) and in TLS SNI, and httpx still verifies the certificate
    against the hostname, not the IP.
    """
    headers = dict(headers or {})
    if address is None:
        return url, headers, {}
    parts = urlsplit(url)
    ip = f"[{address}]" if ":" in address else address
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    headers["Host"] = parts.netloc
    return parts._replace(netloc=netloc).geturl(), headers, {"sni_hostname": parts.hostname}


def public_http_target(url: str, headers: dict | None = None, what: str = "Server") -> tuple[str, dict, dict]:
    """Checks the URL's host and returns (url, headers, extensions) for an httpx
    request pinned to the checked address. Use with follow_redirects=False:
    a redirect would go to a host nobody checked."""
    address = ensure_public_host(*host_and_port(url), what=what)
    return pin_to_address(url, address, headers)
