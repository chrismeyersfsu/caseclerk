"""Pull Clio resources and write JSON/CSV exports."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .api import ClioClient
from .fields import CONTACT_FIELDS, MATTER_FIELDS

PAGE_LIMIT = 200

RESOURCES = {
    "contacts": {"path": "contacts.json", "fields": CONTACT_FIELDS},
    "matters": {"path": "matters.json", "fields": MATTER_FIELDS},
}


def pull_records(client: ClioClient, resource: str, updated_since: str | None = None) -> list[dict[str, Any]]:
    spec = RESOURCES[resource]
    params: dict[str, Any] = {
        "fields": spec["fields"],
        "limit": PAGE_LIMIT,
        "order": "id(asc)",  # required for cursor pagination
    }
    if updated_since:
        params["updated_since"] = updated_since
    return list(client.paginate(spec["path"], params))


def _join(parts: Iterable[str | None], sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


def _format_address(addr: dict[str, Any]) -> str:
    return _join(
        [
            addr.get("street"),
            addr.get("city"),
            addr.get("province"),
            addr.get("postal_code"),
            addr.get("country"),
        ]
    )


def _custom_fields(record: dict[str, Any]) -> str:
    pairs = []
    for cf in record.get("custom_field_values") or []:
        if cf.get("soft_deleted"):
            continue
        value = cf.get("value")
        pairs.append(f"{cf.get('field_name')}={'' if value is None else value}")
    return "; ".join(pairs)


def flatten_contact(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "type": c.get("type"),
        "name": c.get("name"),
        "first_name": c.get("first_name"),
        "last_name": c.get("last_name"),
        "company": (c.get("company") or {}).get("name"),
        "is_client": c.get("is_client"),
        "primary_email_address": c.get("primary_email_address"),
        "primary_phone_number": c.get("primary_phone_number"),
        "email_addresses": " | ".join(e.get("address") or "" for e in (c.get("email_addresses") or [])),
        "phone_numbers": " | ".join(p.get("number") or "" for p in (c.get("phone_numbers") or [])),
        "addresses": " | ".join(_format_address(a) for a in (c.get("addresses") or [])),
        "custom_fields": _custom_fields(c),
        "created_at": c.get("created_at"),
        "updated_at": c.get("updated_at"),
    }


def flatten_matter(m: dict[str, Any]) -> dict[str, Any]:
    def name_of(key: str) -> Any:
        return (m.get(key) or {}).get("name")

    return {
        "id": m.get("id"),
        "display_number": m.get("display_number"),
        "custom_number": m.get("custom_number"),
        "description": m.get("description"),
        "status": m.get("status"),
        "client": name_of("client"),
        "practice_area": name_of("practice_area"),
        "matter_stage": name_of("matter_stage"),
        "responsible_attorney": name_of("responsible_attorney"),
        "originating_attorney": name_of("originating_attorney"),
        "open_date": m.get("open_date"),
        "close_date": m.get("close_date"),
        "pending_date": m.get("pending_date"),
        "billable": m.get("billable"),
        "billing_method": m.get("billing_method"),
        "location": m.get("location"),
        "last_activity_date": m.get("last_activity_date"),
        "custom_fields": _custom_fields(m),
        "created_at": m.get("created_at"),
        "updated_at": m.get("updated_at"),
    }


FLATTENERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "contacts": flatten_contact,
    "matters": flatten_matter,
}


def write_csv_file(resource: str, records: list[dict[str, Any]], path: Path) -> None:
    flatten = FLATTENERS[resource]
    fieldnames = list(flatten({}).keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flatten(r) for r in records)


def write_exports(
    out_dir: Path, results: dict[str, list[dict[str, Any]]], write_csv: bool = True
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": {},
    }
    for name, records in results.items():
        (out_dir / f"{name}.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if write_csv:
            write_csv_file(name, records, out_dir / f"{name}.csv")
        manifest["counts"][name] = len(records)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
