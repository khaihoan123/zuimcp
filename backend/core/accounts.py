"""Picking which connected account an MCP tool call acts on.

Every service (email, Odoo, ...) goes through resolve_account(), so the
"which account?" rule is the same everywhere:

- `account` given: use exactly that account, or fail. Never fall back to another.
- `account` empty: allowed only when the user has ONE account for the
  service. With several, the call fails and lists the choices, so an agent can
  never silently act on (e.g. send from) an account it didn't name.

An account that needs reconnecting (status 'needs_reauth', e.g. a revoked
Gmail login) still counts here: it can't be used, but it must not make the
other one look like the only choice. Accounts the user disabled don't count.

Either way the account must have the requested capability granted.
"""

import json
from dataclasses import dataclass

from sqlalchemy import text

from core.db import SessionLocal
from core.security import decrypt_credentials

# Service family -> the connections.service_type values that belong to it.
# Outlook joins "email" here once its adapter exists.
SERVICE_TYPES: dict[str, tuple[str, ...]] = {
    "email": ("imap", "gmail"),
}
# Every Odoo version is "odoo"; which API a server speaks is in its credentials.
SERVICE_TYPES["odoo"] = ("odoo",)


@dataclass
class Account:
    id: int
    name: str
    service_type: str
    credentials: dict


def _service_types(service: str) -> tuple[str, ...]:
    try:
        return SERVICE_TYPES[service]
    except KeyError:
        raise ValueError(f"Unknown service '{service}'") from None


def _needs_reauth_error(service: str, name: str) -> ValueError:
    return ValueError(
        f"The {service} account '{name}' needs to be reconnected: the provider no longer "
        f"accepts its saved login. The user can reconnect it in the ZuiMCP dashboard."
    )


def resolve_account(user_id: str, service: str, account: str | None, capability: str) -> Account:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT c.id, c.display_name, c.service_type, c.credentials_encrypted, c.status, "
                "COALESCE(p.allowed, false) AS allowed "
                "FROM connections c "
                "LEFT JOIN permissions p ON p.connection_id = c.id AND p.capability = :capability "
                "WHERE c.user_id = :user_id AND c.status IN ('active', 'needs_reauth') "
                "AND c.service_type = ANY(:service_types) "
                "ORDER BY c.id"
            ),
            {
                "user_id": user_id,
                "capability": capability,
                "service_types": list(_service_types(service)),
            },
        ).fetchall()
    finally:
        db.close()

    if not rows:
        raise ValueError(f"No active {service} accounts are connected for this user")

    if account:
        wanted = account.strip()
        matches = [r for r in rows if str(r.id) == wanted]
        if not matches:
            matches = [r for r in rows if r.display_name.casefold() == wanted.casefold()]
        if not matches:
            raise ValueError(
                f"No active {service} account matches '{account}'. "
                f"Call list_accounts to see the valid values."
            )
        if len(matches) > 1:
            ids = ", ".join(str(r.id) for r in matches)
            raise ValueError(
                f"Several {service} accounts are named '{account}'. "
                f"Pass the account id instead: {ids}"
            )
        row = matches[0]
    elif len(rows) == 1:
        row = rows[0]
    else:
        permitted = [r for r in rows if r.allowed]
        if not permitted:
            raise ValueError(f"None of this user's {service} accounts allow '{capability}'")
        options = "\n".join(
            f"  - {r.display_name} (account: {r.id})"
            + (" — needs reconnecting, can't be used now" if r.status == "needs_reauth" else "")
            for r in permitted
        )
        raise ValueError(
            f"This user has several {service} accounts, so `account` is required — it is "
            f"never guessed. Ask the user which one to use if it isn't clear, then retry "
            f"with one of these (accounts that allow '{capability}'):\n{options}"
        )

    if row.status == "needs_reauth":
        raise _needs_reauth_error(service, row.display_name)

    if not row.allowed:
        raise ValueError(
            f"The account '{row.display_name}' does not allow '{capability}'. "
            f"The user can grant it in the ZuiMCP dashboard."
        )

    return Account(
        id=row.id,
        name=row.display_name,
        service_type=row.service_type,
        credentials=json.loads(decrypt_credentials(row.credentials_encrypted)),
    )


def list_user_accounts(user_id: str) -> list[dict]:
    """Every active account the user connected, with the capabilities granted on it."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT c.id, c.display_name, c.service_type, c.credentials_encrypted, "
                "COALESCE(array_agg(p.capability ORDER BY p.capability) "
                "FILTER (WHERE p.allowed), '{}') AS allowed "
                "FROM connections c LEFT JOIN permissions p ON p.connection_id = c.id "
                "WHERE c.user_id = :user_id AND c.status = 'active' "
                "GROUP BY c.id ORDER BY c.id"
            ),
            {"user_id": user_id},
        ).fetchall()
    finally:
        db.close()

    service_of = {st: svc for svc, types in SERVICE_TYPES.items() for st in types}
    accounts = []
    for r in rows:
        service = service_of.get(r.service_type)
        if service is None:
            continue  # a connector this server has no tools for yet
        item = {
            "account": str(r.id),
            "name": r.display_name,
            "service": service,
            "provider": r.service_type,
            "allowed": list(r.allowed),
        }
        if service == "odoo":
            # Where it points, never the API key.
            creds = json.loads(decrypt_credentials(r.credentials_encrypted))
            item.update(
                url=creds.get("url"),
                database=creds.get("database"),
                login=creds.get("login"),
                version=creds.get("version"),
            )
        if service == "email":
            item["address"] = json.loads(decrypt_credentials(r.credentials_encrypted)).get("email")
        accounts.append(item)
    return accounts


def granted_capabilities(user_id: str) -> set[str]:
    """Capabilities granted on at least one of the user's active accounts."""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT DISTINCT p.capability FROM permissions p "
                "JOIN connections c ON c.id = p.connection_id "
                "WHERE c.user_id = :user_id AND c.status = 'active' AND p.allowed"
            ),
            {"user_id": user_id},
        ).fetchall()
    finally:
        db.close()
    return {r.capability for r in rows}
