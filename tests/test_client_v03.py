"""Offline client regressions from the v0.3 review; all XML is synthetic.

These tests do not authenticate with the provider or claim live field coverage.
"""
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.molit import MolitError, parse_transactions


FIXTURES = Path(__file__).parent / "fixtures"
QUERY = dict(kind="trade", lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=20)


def fixture(name="molit_trade.xml"):
    return ET.fromstring((FIXTURES / name).read_bytes())


def parse(root, **overrides):
    return parse_transactions(ET.tostring(root), **(QUERY | overrides))


@pytest.mark.parametrize("code, expected", [
    ("30", "MOLIT_AUTH_ERROR"),
    ("22", "MOLIT_RATE_LIMIT"),
    ("05", "MOLIT_TIMEOUT"),
    ("04", "MOLIT_UPSTREAM_ERROR"),
    ("99", "MOLIT_UPSTREAM_ERROR"),
])
@pytest.mark.parametrize("shape", ["header", "gateway"])
def test_no_data_message_cannot_mask_explicit_provider_error(code, expected, shape):
    if shape == "gateway":
        root = ET.fromstring(
            f"<OpenAPI_ServiceResponse><cmmMsgHeader>"
            f"<returnReasonCode>{code}</returnReasonCode>"
            "<returnAuthMsg>NODATA_ERROR</returnAuthMsg>"
            "</cmmMsgHeader></OpenAPI_ServiceResponse>"
        )
    else:
        root = ET.fromstring(
            f"<response><header><resultCode>{code}</resultCode>"
            "<resultMsg>NODATA_ERROR</resultMsg></header></response>"
        )
    with pytest.raises(MolitError) as caught:
        parse(root)
    assert caught.value.code == expected


@pytest.mark.parametrize("code, message", [("03", "NODATA_ERROR"), ("003", "NO_DATA"), ("000", "NODATA_ERROR")])
def test_unambiguous_no_data_reply_remains_successful(code, message):
    root = ET.fromstring(
        f"<response><header><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></header></response>"
    )
    result = parse(root)
    assert result["total_count"] == result["returned_count"] == result["skipped_count"] == 0
    assert result["items"] == []
    assert result["has_next"] is False


@pytest.mark.parametrize("total", [0, 20, 21])
def test_rows_cannot_extend_beyond_provider_total_on_later_page(total):
    root = fixture()
    root.find("body/pageNo").text = "2"
    root.find("body/totalCount").text = str(total)
    # There are two rows, but page 2 has at most one position left.
    with pytest.raises(MolitError) as caught:
        parse(root, page_no=2)
    assert caught.value.code == "MOLIT_INVALID_RESPONSE"


@pytest.mark.parametrize("total", [21, 45])
def test_empty_later_page_with_remaining_records_is_not_success(total):
    root = fixture("molit_empty.xml")
    root.find("body/pageNo").text = "2"
    root.find("body/totalCount").text = str(total)
    with pytest.raises(MolitError) as caught:
        parse(root, page_no=2)
    assert caught.value.code == "MOLIT_INVALID_RESPONSE"


def test_final_page_fits_remaining_rows_and_retains_total_and_indices():
    root = fixture()
    root.find("body/pageNo").text = "2"
    root.find("body/totalCount").text = "22"
    result = parse(root, page_no=2)
    assert result["total_count"] == 22
    assert result["returned_count"] == 2
    assert result["skipped_count"] == 0
    assert result["has_next"] is False
    assert result["items"][0]["id"].endswith("-20")
    assert result["items"][1]["id"].endswith("-21")


@pytest.mark.parametrize("total", [0, 2, 20])
def test_empty_page_beyond_end_remains_successful(total):
    root = fixture("molit_empty.xml")
    root.find("body/pageNo").text = "2"
    root.find("body/totalCount").text = str(total)
    result = parse(root, page_no=2)
    assert result["total_count"] == total
    assert result["returned_count"] == result["skipped_count"] == 0
    assert result["items"] == []
    assert result["has_next"] is False


@pytest.mark.parametrize("raw_date", ["26.10.15", "20261015", "2026-10-15", "2026.10.15"])
def test_cancellation_date_alone_preserves_cancellation_evidence(raw_date):
    root = fixture()
    item = root.find("body/items/item")
    item.find("cdealType").text = None
    item.find("cdealDay").text = raw_date
    result = parse(root)["items"][0]
    assert result["cancelled"] is True
    assert result["cancelled_date"] == "2026-10-15"
    assert result["cancellation_status_available"] is True


def test_cancellation_flag_without_date_stays_cancelled():
    root = fixture()
    item = root.find("body/items/item")
    item.find("cdealType").text = " O "
    item.find("cdealDay").text = " "
    result = parse(root)["items"][0]
    assert result["cancelled"] is True
    assert result["cancelled_date"] is None


def test_rent_decimal_amounts_preserve_won_and_do_not_inherit_trade_price():
    root = fixture("molit_rent.xml")
    item = root.find("body/items/item")
    item.find("deposit").text = " 1,000.0001 "
    item.find("monthlyRent").text = " 0.0001 "
    # A provider-added trade-shaped field must not replace the rent amounts.
    ET.SubElement(item, "dealAmount").text = "999,999"
    result = parse(root, kind="rent")["items"][0]
    assert result["deposit_krw"] == 10_000_001
    assert result["monthly_rent_krw"] == 1
    assert result["price_krw"] is None
    assert result["lease_type"] == "monthly_rent"
    assert result["cancellation_status_available"] is False
