import ipaddress
import socket

from core.config import config

# Seconds to wait when connecting to a user-supplied mail server, so an
# unreachable host can't hang a request or a tool call indefinitely.
MAIL_CONNECT_TIMEOUT = 15


class UnsafeHostError(ValueError):
    pass


def ensure_public_host(host: str, port: int, what: str = "Mail server") -> None:
    """Refuses servers (mail, Odoo) that resolve to private, loopback, link-local
    or other non-public addresses (SSRF guard). Users choose the host, so without
    this anyone who signs up could make the backend probe the internal network
    (e.g. `db:5432`) or cloud metadata endpoints.

    Every resolved address must be public, otherwise a hostname with one public
    and one private record would slip through. Checked again at each connect,
    not only when the account is saved, because DNS can change afterwards.

    Self-hosters whose mail or Odoo server lives on their own LAN can set
    ALLOW_PRIVATE_MAIL_HOSTS to true (the name predates Odoo; it covers both).

    `what` only names the server in the error message.
    """
    if config.get("ALLOW_PRIVATE_MAIL_HOSTS", False):
        return
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise UnsafeHostError(f"Could not resolve {what.lower()} '{host}'")
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise UnsafeHostError(f"{what} '{host}' is not a public address")
