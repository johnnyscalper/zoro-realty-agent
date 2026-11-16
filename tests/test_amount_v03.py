"""Offline amount-boundary regressions; all source records are synthetic."""
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.molit import parse_transactions


FIXTURES = Path(__file__).parent / "fixtures"
QUERY = dict(lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=20)
FIELDS = [
    pytest.param("trade", "dealAmount", "price_krw", id="sale-price"),
    pytest.param("rent", "deposit", "deposit_krw", id="deposit"),
    pytest.param("rent", "monthlyRent", "monthly_rent_krw", id="monthly-rent"),
]


def amount_page(kind, source_field, value, *, all_rows=False):
    root = ET.fromstring((FIXTURES / f"molit_{kind}.xml").read_bytes())
    rows = root.findall("body/items/item")
    for row in rows if all_rows else rows[:1]:
        row.find(source_field).text = value
    return parse_transactions(ET.tostring(root), kind=kind, **QUERY)


@pytest.mark.parametrize("kind, source_field, output_field", FIELDS)
@pytest.mark.parametrize("source_amount, expected_krw", [
    ("0.0001", 1),
    ("123,456.0001", 1_234_560_001),
    ("900719925474.0990", 9_007_199_254_740_990),
    ("900719925474.0991", 9_007_199_254_740_991),
    ("900,719,925,474.0991", 9_007_199_254_740_991),
])
def test_valid_money_preserves_exact_won_through_json_number(kind, source_field, output_field, source_amount, expected_krw):
    page = amount_page(kind, source_field, source_amount)

    assert page["total_count"] == page["returned_count"] == 2
    assert page["skipped_count"] == 0
    amount = page["items"][0][output_field]
    assert type(amount) is int
    assert amount == expected_krw
    # JSON integers parsed as IEEE-754 binary64 model browser Number parsing.
    browser_page = json.loads(json.dumps(page), parse_int=float)
    assert int(browser_page["items"][0][output_field]) == expected_krw


@pytest.mark.parametrize("kind, source_field, output_field", FIELDS)
@pytest.mark.parametrize("source_amount", [
    "900719925474.0992",  # First integer above Number.MAX_SAFE_INTEGER.
    "900,719,925,474.0993",  # The next integer would round during JSON parsing.
    "10000000000000",  # Formerly permitted 10**17 KRW upper bound.
])
def test_unsafe_money_skips_only_affected_record_and_preserves_source_total(kind, source_field, output_field, source_amount):
    page = amount_page(kind, source_field, source_amount)

    assert page["total_count"] == 2
    assert page["returned_count"] == page["skipped_count"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["id"].endswith("-1")
    assert 0 <= page["items"][0][output_field] <= 9_007_199_254_740_991
    assert any("1건" in warning for warning in page["warnings"])


@pytest.mark.parametrize("kind, source_field, output_field", FIELDS)
def test_all_unsafe_amounts_remain_explicitly_skipped_records(kind, source_field, output_field):
    page = amount_page(kind, source_field, "900719925474.0993", all_rows=True)

    assert page["total_count"] == page["skipped_count"] == 2
    assert page["returned_count"] == 0
    assert page["items"] == []
    assert any("2건" in warning for warning in page["warnings"])


@pytest.mark.parametrize("kind, source_field, output_field", FIELDS)
def test_zero_keeps_existing_sale_and_rent_rules(kind, source_field, output_field):
    page = amount_page(kind, source_field, "0")

    if kind == "trade":
        assert page["returned_count"] == page["skipped_count"] == 1
    else:
        assert page["returned_count"] == 2
        assert page["skipped_count"] == 0
        assert page["items"][0][output_field] == 0
        assert page["items"][0]["lease_type"] == "jeonse"
