from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from caseclerk_clio_export import export

CONTACT: dict[str, Any] = {
    "id": 42,
    "type": "Person",
    "name": "Ada Lovelace",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "company": {"id": 7, "name": "Analytical Engines LLC"},
    "is_client": True,
    "primary_email_address": "ada@example.com",
    "email_addresses": [
        {"address": "ada@example.com"},
        {"address": "ada@work.example.com"},
    ],
    "phone_numbers": [{"number": "555-0100"}],
    "addresses": [
        {
            "street": "1 Engine Way",
            "city": "Springfield",
            "province": "FL",
            "postal_code": "32301",
            "country": "United States",
        }
    ],
    "custom_field_values": [
        {"field_name": "Referral Source", "value": "Website", "soft_deleted": False},
        {"field_name": "Old Field", "value": "x", "soft_deleted": True},
    ],
    "created_at": "2020-01-01T00:00:00Z",
    "updated_at": "2020-06-01T00:00:00Z",
}

MATTER: dict[str, Any] = {
    "id": 9,
    "display_number": "00003-Lovelace",
    "description": "Estate planning",
    "status": "open",
    "client": {"id": 42, "name": "Ada Lovelace"},
    "practice_area": {"id": 1, "name": "Estate"},
    "responsible_attorney": {"id": 2, "name": "J. Smith"},
    "open_date": "2020-02-02",
}


def test_flatten_contact() -> None:
    row = export.flatten_contact(CONTACT)
    assert row["company"] == "Analytical Engines LLC"
    assert row["email_addresses"] == "ada@example.com | ada@work.example.com"
    assert row["addresses"] == "1 Engine Way, Springfield, FL, 32301, United States"
    assert row["custom_fields"] == "Referral Source=Website"  # soft-deleted skipped


def test_flatten_matter() -> None:
    row = export.flatten_matter(MATTER)
    assert row["client"] == "Ada Lovelace"
    assert row["practice_area"] == "Estate"
    assert row["responsible_attorney"] == "J. Smith"
    assert row["close_date"] is None


def test_flatteners_tolerate_empty_records() -> None:
    assert export.flatten_contact({})["id"] is None
    assert export.flatten_matter({})["client"] is None


def test_write_exports(tmp_path: Path) -> None:
    manifest = export.write_exports(tmp_path, {"contacts": [CONTACT], "matters": [MATTER]})

    assert manifest["counts"] == {"contacts": 1, "matters": 1}
    saved = json.loads((tmp_path / "contacts.json").read_text())
    assert saved[0]["name"] == "Ada Lovelace"
    with (tmp_path / "matters.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["display_number"] == "00003-Lovelace"
    assert json.loads((tmp_path / "manifest.json").read_text())["counts"]["matters"] == 1


def test_write_exports_empty_still_writes_csv_header(tmp_path: Path) -> None:
    export.write_exports(tmp_path, {"contacts": []})
    with (tmp_path / "contacts.csv").open() as fh:
        header = fh.readline()
    assert header.startswith("id,")
