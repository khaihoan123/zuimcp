"""Odoo tools. Registered on the shared server in core.mcp_server.

Generic read/create/update/delete on any model the Odoo user can access —
Odoo's own access rights still apply on top, since every call runs as the
user whose API key was connected. There is no "call any method" tool.

Outputs are kept small on purpose: Odoo records can have hundreds of fields
and megabytes of HTML or base64 images, so searches return few fields by
default, limits are capped, and long values are cut.
"""

from typing import Any

from fastmcp import Context

from core.accounts import resolve_account
from core.mcp_auth import get_user_id
from core.mcp_server import service_tool
from services.odoo.client import OdooClient

DEFAULT_SEARCH_LIMIT = 20
MAX_LIMIT = 200
MAX_READ_IDS = 100
MAX_WRITE_IDS = 100
# Longer string values are cut (HTML descriptions, base64 images, ...).
MAX_VALUE_CHARS = 2000
# Field attributes odoo_get_fields returns; `help` and the rest are too wordy.
FIELD_ATTRIBUTES = ["string", "type", "required", "readonly", "relation", "selection"]
# Field types left out when odoo_read is not told which fields to return:
# binary is base64 data, and x2many lists can hold thousands of ids.
DEFAULT_READ_SKIPPED_TYPES = {"binary", "one2many", "many2many"}


def _client(ctx: Context, account: str, capability: str) -> tuple[OdooClient, str]:
    user_id = get_user_id(ctx)
    if not user_id:
        raise ValueError("Odoo tools need a signed-in ZuiMCP user; they are not available in local stdio mode")
    acct = resolve_account(user_id, "odoo", account, capability)
    return OdooClient.from_credentials(acct.credentials), acct.name


def _limit(value: int, default: int) -> int:
    if value <= 0:
        return default
    return min(value, MAX_LIMIT)


def _check_ids(ids: list[int], maximum: int) -> list[int]:
    if not ids:
        raise ValueError("Pass at least one record id")
    if len(ids) > maximum:
        raise ValueError(f"At most {maximum} ids per call")
    return ids


def _compact(records: list[dict]) -> list[dict]:
    for record in records:
        for key, value in record.items():
            if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
                record[key] = value[:MAX_VALUE_CHARS] + f"… [cut, {len(value)} characters in total]"
    return records


@service_tool(capability="odoo.read")
def odoo_list_models(ctx: Context, search: str = "", limit: int = 50, offset: int = 0,
                     account: str = "") -> dict:
    """List the Odoo models (tables) this account can see, e.g. res.partner, sale.order.

    Args:
        search: only models whose technical name or label contains this text,
            e.g. "invoice" or "sale". Leave empty to list all.
        limit: max models to return (at most 200).
        offset: skip this many, for paging.
        account: which Odoo account to use — an `account` value or name from
            list_accounts. Required when the user has more than one Odoo account.

    Returns the technical `model` name (use it in the other odoo_ tools) and its label.
    """
    client, name = _client(ctx, account, "odoo.read")
    limit = _limit(limit, 50)
    rows = client.list_models(search, offset=max(offset, 0), limit=limit + 1)
    return {
        "account": name,
        "models": [{"model": r["model"], "name": r["name"]} for r in rows[:limit]],
        "has_more": len(rows) > limit,
    }


@service_tool(capability="odoo.read")
def odoo_get_fields(ctx: Context, model: str, fields: list[str] | None = None,
                    account: str = "") -> dict:
    """Describe the fields of an Odoo model: label, type, required, readonly,
    related model (for many2one/one2many/many2many) and choices (for selection).

    Call this before searching, creating or updating, to use the right field names.

    Args:
        model: technical model name, e.g. res.partner (see odoo_list_models).
        fields: only describe these fields. Leave empty for all of them.
        account: which Odoo account to use — see odoo_list_models.
    """
    client, name = _client(ctx, account, "odoo.read")
    described = client.fields_get(model, FIELD_ATTRIBUTES)
    if fields:
        described = {k: v for k, v in described.items() if k in fields}
    # Drop empty attributes (no relation, no selection, ...) to keep it short.
    return {
        "account": name,
        "model": model,
        "fields": {
            field: {k: v for k, v in attrs.items() if v not in (None, False, "", [])}
            for field, attrs in sorted(described.items())
        },
    }


@service_tool(capability="odoo.read")
def odoo_search(ctx: Context, model: str, domain: list[Any] | None = None,
                fields: list[str] | None = None, limit: int = DEFAULT_SEARCH_LIMIT,
                offset: int = 0, order: str = "", account: str = "") -> dict:
    """Search records of an Odoo model and return chosen fields.

    Args:
        model: technical model name, e.g. res.partner, sale.order.
        domain: Odoo search domain, a list of [field, operator, value] conditions
            (joined with AND unless you add "|" / "!" prefix operators), e.g.
            [["customer_rank", ">", 0], ["country_id.code", "=", "GB"]].
            Leave empty to match every record.
        fields: fields to return. Default is only `display_name`, to keep the
            answer small — name the fields you need (see odoo_get_fields).
            Many2one values come back as [id, "name"].
        limit: max records (default 20, at most 200).
        offset: skip this many, for paging.
        order: sort order, e.g. "date_order desc, id desc".
        account: which Odoo account to use — see odoo_list_models.

    `has_more` is true when more records match beyond this page. Text longer
    than 2000 characters is cut.
    """
    client, name = _client(ctx, account, "odoo.read")
    limit = _limit(limit, DEFAULT_SEARCH_LIMIT)
    rows = client.search_read(model, domain or [], fields or ["display_name"],
                              offset=max(offset, 0), limit=limit + 1, order=order or None)
    return {
        "account": name,
        "model": model,
        "records": _compact(rows[:limit]),
        "has_more": len(rows) > limit,
    }


@service_tool(capability="odoo.read")
def odoo_read(ctx: Context, model: str, ids: list[int], fields: list[str] | None = None,
              account: str = "") -> dict:
    """Read Odoo records by id.

    Args:
        model: technical model name, e.g. sale.order.
        ids: record ids (at most 100), e.g. from odoo_search.
        fields: fields to return. Default is every field except binary data
            (images, files) and one2many/many2many lists — name those
            explicitly if you need them.
        account: which Odoo account to use — see odoo_list_models.

    Text longer than 2000 characters is cut.
    """
    client, name = _client(ctx, account, "odoo.read")
    _check_ids(ids, MAX_READ_IDS)
    if not fields:
        types = client.fields_get(model, ["type"])
        fields = sorted(f for f, attrs in types.items()
                        if attrs.get("type") not in DEFAULT_READ_SKIPPED_TYPES)
    rows = client.read(model, ids, fields)
    return {"account": name, "model": model, "records": _compact(rows)}


@service_tool(capability="odoo.read")
def odoo_count(ctx: Context, model: str, domain: list[Any] | None = None,
               account: str = "") -> dict:
    """Count the records of an Odoo model that match a domain, without fetching them.

    Use this for "how many ...?" questions, or before odoo_search to see how
    big a result will be.

    Args:
        model: technical model name, e.g. sale.order.
        domain: Odoo search domain, as in odoo_search. Leave empty to count all.
        account: which Odoo account to use — see odoo_list_models.
    """
    client, name = _client(ctx, account, "odoo.read")
    return {"account": name, "model": model, "count": client.search_count(model, domain or [])}


@service_tool(capability="odoo.read")
def odoo_read_group(ctx: Context, model: str, groupby: list[str] | None = None,
                    aggregates: list[str] | None = None, domain: list[Any] | None = None,
                    limit: int = 50, order: str = "", account: str = "") -> dict:
    """Totals per group, like a pivot table: group the matching records and
    return each group's record count plus sums/averages/min/max of number fields.

    Example — sales per customer this year, biggest first:
        model="sale.order", domain=[["state", "=", "sale"], ["date_order", ">=", "2026-01-01"]],
        groupby=["partner_id"], aggregates=["amount_total:sum"], order="amount_total:sum desc"

    Args:
        model: technical model name.
        groupby: fields to group by, e.g. ["partner_id"]. Date fields take a
            period: "date_order:day", ":week", ":month", ":quarter" or ":year".
            Leave empty for one total over all matching records.
        aggregates: "<field>:<function>" with function sum, avg, min, max,
            count or count_distinct, e.g. ["amount_total:sum"]. Every group
            always gets `count` (its number of records).
        domain: Odoo search domain, as in odoo_search.
        limit: max groups (default 50, at most 200).
        order: sort the groups, e.g. "amount_total:sum desc" or "partner_id".
        account: which Odoo account to use — see odoo_list_models.

    Many2one group values come back as [id, "name"].
    """
    client, name = _client(ctx, account, "odoo.read")
    limit = _limit(limit, 50)
    groups = client.read_group(model, domain or [], groupby or [], aggregates or [],
                               limit=limit + 1, order=order or None)
    return {
        "account": name,
        "model": model,
        "groups": groups[:limit],
        "has_more": len(groups) > limit,
    }


@service_tool(capability="odoo.create")
def odoo_create(ctx: Context, model: str, values: dict[str, Any], account: str = "") -> dict:
    """Create one Odoo record.

    Args:
        model: technical model name, e.g. res.partner.
        values: field values, e.g. {"name": "Jane Doe", "email": "jane@example.com"}.
            Many2one fields take an id. Use odoo_get_fields for names and types.
        account: which Odoo account to create it in — an `account` value or name
            from list_accounts. Required when the user has more than one Odoo
            account; it is never guessed, so confirm with the user if unsure.
    """
    client, name = _client(ctx, account, "odoo.create")
    if not values:
        raise ValueError("Pass the field values for the new record")
    new_id = client.create(model, values)
    return {"status": "created", "account": name, "model": model, "id": new_id}


@service_tool(capability="odoo.write")
def odoo_update(ctx: Context, model: str, ids: list[int], values: dict[str, Any],
                account: str = "") -> dict:
    """Update Odoo records: set the same field values on every given record.

    Args:
        model: technical model name, e.g. res.partner.
        ids: ids of the records to change (at most 100).
        values: fields to set, e.g. {"phone": "+44 20 7946 0000"}.
        account: which Odoo account to use — an `account` value or name from
            list_accounts. Required when the user has more than one Odoo account;
            it is never guessed, so confirm with the user if unsure.
    """
    client, name = _client(ctx, account, "odoo.write")
    _check_ids(ids, MAX_WRITE_IDS)
    if not values:
        raise ValueError("Pass the field values to change")
    client.write(model, ids, values)
    return {"status": "updated", "account": name, "model": model, "ids": ids,
            "fields": sorted(values)}


@service_tool(capability="odoo.delete")
def odoo_delete(ctx: Context, model: str, ids: list[int], account: str = "") -> dict:
    """Delete Odoo records. This cannot be undone — consider archiving instead
    (odoo_update with {"active": false}) when the model has an `active` field.

    Args:
        model: technical model name.
        ids: ids of the records to delete (at most 100).
        account: which Odoo account to use — an `account` value or name from
            list_accounts. Required when the user has more than one Odoo account;
            it is never guessed, so confirm with the user if unsure.
    """
    client, name = _client(ctx, account, "odoo.delete")
    _check_ids(ids, MAX_WRITE_IDS)
    client.unlink(model, ids)
    return {"status": "deleted", "account": name, "model": model, "ids": ids}
