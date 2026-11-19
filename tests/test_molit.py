"""No external requests: source fixtures are invented and labeled as such."""
import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.molit import MAX_RESPONSE_BYTES, MolitError, fetch_transactions, parse_transactions


FIXTURES = Path(__file__).parent / "fixtures"
QUERY = dict(kind="trade", lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=20)
TRADE = (FIXTURES / "molit_trade.xml").read_bytes()
RENT = (FIXTURES / "molit_rent.xml").read_bytes()
EMPTY = (FIXTURES / "molit_empty.xml").read_bytes()
FAKE_KEY = "OFFLINE+test/key=="


def parse(xml=TRADE, **override):
    return parse_transactions(xml, **(QUERY | override))


def fetch(handler, *, key=FAKE_KEY, **override):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_transactions(**(QUERY | override), api_key=key, client=client)
    return asyncio.run(run())


def test_trade_units_cancellation_and_provenance():
    page = parse()
    first, cancelled = page["items"]
    assert first["price_krw"] == 1234560000
    assert first["area_m2"] == 84.95
    assert first["contract_date"] == "2026-10-08"
    assert cancelled["cancelled"] is True
    assert cancelled["cancelled_date"] == "2026-10-15"
    assert first["deposit_krw"] is None
    assert page["returned_count"] == page["total_count"] == 2
    assert page["skipped_count"] == 0
    assert page["has_next"] is False
    assert page["source"] == "molit"
    assert "현재 거래 가능한 매물" in page["warnings"][0]


def test_rent_jeonse_monthly_distinction_and_unknown_build_year():
    page = parse(RENT, kind="rent")
    jeonse, monthly = page["items"]
    assert jeonse["deposit_krw"] == 800000000
    assert jeonse["monthly_rent_krw"] == 0
    assert jeonse["lease_type"] == "jeonse"
    assert monthly["monthly_rent_krw"] == 1500000
    assert monthly["lease_type"] == "monthly_rent"
    assert monthly["build_year"] is None
    assert jeonse["cancellation_status_available"] is False
    assert any("해제 여부" in text for text in page["warnings"])


def test_money_uses_decimal_not_binary_float():
    page = parse(TRADE.replace(b"123,456", b"123456.0001"))
    assert page["items"][0]["price_krw"] == 1234560001


def test_empty_and_beyond_last_page_are_not_parse_errors():
    assert parse(EMPTY)["items"] == []
    later = EMPTY.replace(b"<pageNo>1</pageNo>", b"<pageNo>2</pageNo>").replace(b"<totalCount>0</totalCount>", b"<totalCount>2</totalCount>")
    page = parse(later, page_no=2)
    assert page["total_count"] == 2 and not page["has_next"]


def test_pagination_retains_provider_total():
    page = parse(TRADE.replace(b"<totalCount>2</totalCount>", b"<totalCount>25</totalCount>"))
    assert page["total_count"] == 25 and page["has_next"]


@pytest.mark.parametrize("old,new", [
    (b"<dealDay>8</dealDay>", b"<dealDay>32</dealDay>"),
    (b"<dealMonth>10</dealMonth>", b"<dealMonth>9</dealMonth>"),
    (b"<excluUseAr>84.95</excluUseAr>", b"<excluUseAr>NaN</excluUseAr>"),
    (b"<dealAmount>123,456</dealAmount>", b"<dealAmount/>"),
    (b"<dealAmount>123,456</dealAmount>", b"<dealAmount>12,34</dealAmount>"),
    (b"<sggCd>11680</sggCd>", b"<sggCd>11110</sggCd>"),
])
def test_malformed_rows_explicitly_counted_not_fabricated(old, new):
    page = parse(TRADE.replace(old, new, 1))
    assert page["total_count"] == 2
    assert page["returned_count"] == 1
    assert page["skipped_count"] == 1
    assert any("1건" in warning for warning in page["warnings"])


def test_missing_monthly_rent_does_not_become_jeonse():
    page = parse(RENT.replace(b"<monthlyRent>0</monthlyRent>", b"<monthlyRent/>"), kind="rent")
    assert page["skipped_count"] == 1
    assert all(i["lease_type"] != "jeonse" for i in page["items"])


def test_all_bad_rows_not_disguised_as_empty_result():
    page = parse(TRADE.replace(b"<sggCd>11680</sggCd>", b"<sggCd>11110</sggCd>"))
    assert page["items"] == []
    assert page["skipped_count"] == page["total_count"] == 2
    assert any("2건" in warning for warning in page["warnings"])


@pytest.mark.parametrize("xml", [b"<html>maintenance</html>", b"", b"<response/>", b"not XML", b"<response><header><resultCode>0</resultCode></header></response>"])
def test_malformed_response_is_not_empty_success(xml):
    with pytest.raises(MolitError) as failure:
        parse(xml)
    assert failure.value.code == "MOLIT_INVALID_RESPONSE"


def test_external_entities_and_dtd_are_rejected():
    xml = b'<!DOCTYPE response [<!ENTITY secret SYSTEM "file:///etc/passwd">]><response><header><resultCode>0</resultCode></header><body>&secret;</body></response>'
    with pytest.raises(MolitError) as failure:
        parse(xml)
    assert failure.value.code == "MOLIT_INVALID_RESPONSE"


@pytest.mark.parametrize("old,new", [
    (b"<totalCount>2</totalCount>", b"<totalCount>0</totalCount>"),
    (b"<pageNo>1</pageNo>", b"<pageNo>2</pageNo>"),
    (b"<numOfRows>20</numOfRows>", b"<numOfRows>10</numOfRows>"),
    (b"<totalCount>2</totalCount>", b""),
])
def test_missing_or_inconsistent_pagination_is_reported(old, new):
    with pytest.raises(MolitError):
        parse(TRADE.replace(old, new))


@pytest.mark.parametrize("code,expected,retryable", [
    ("20", "MOLIT_AUTH_ERROR", False), ("29", "MOLIT_AUTH_ERROR", False),
    ("30", "MOLIT_AUTH_ERROR", False), ("31", "MOLIT_AUTH_ERROR", False),
    ("22", "MOLIT_RATE_LIMIT", False), ("23", "MOLIT_RATE_LIMIT", False),
    ("05", "MOLIT_TIMEOUT", True), ("10", "MOLIT_REQUEST_REJECTED", False),
    ("12", "MOLIT_REQUEST_REJECTED", False), ("99", "MOLIT_UPSTREAM_ERROR", True),
])
def test_provider_codes_have_safe_errors(code, expected, retryable):
    xml = f"<response><header><resultCode>{code}</resultCode><resultMsg>{FAKE_KEY}</resultMsg></header></response>"
    with pytest.raises(MolitError) as failure:
        parse(xml)
    assert failure.value.code == expected
    assert failure.value.retryable is retryable
    assert FAKE_KEY not in str(failure.value)


def test_gateway_auth_shape_and_plain_token():
    for xml in [b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>", b"SERVICE_KEY_IS_NOT_REGISTERED_ERROR"]:
        with pytest.raises(MolitError) as failure:
            parse(xml)
        assert failure.value.code == "MOLIT_AUTH_ERROR"


@pytest.mark.parametrize("xml", [b"<response><header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header></response>", b"NODATA_ERROR"])
def test_explicit_no_data_error_becomes_empty_result(xml):
    assert parse(xml)["total_count"] == 0


@pytest.mark.parametrize("encoded", [False, True])
def test_key_plus_and_slash_encoded_exactly_once(encoded, caplog):
    seen = []
    caplog.set_level(logging.DEBUG)
    def handler(request):
        seen.append(request)
        assert request.url.params["serviceKey"] == FAKE_KEY
        assert "%252B" not in str(request.url)
        assert request.url.host == "apis.data.go.kr"
        assert request.url.scheme == "https"
        assert request.url.path.endswith("/getRTMSDataSvcAptTrade")
        assert request.url.params["LAWD_CD"] == "11680"
        assert request.url.params["DEAL_YMD"] == "202610"
        return httpx.Response(200, content=TRADE)
    page = fetch(handler, key=quote(FAKE_KEY, safe="") if encoded else FAKE_KEY)
    assert page["returned_count"] == 2 and len(seen) == 1
    assert FAKE_KEY not in caplog.text
    assert quote(FAKE_KEY, safe="") not in caplog.text


def test_rent_endpoint_selection():
    def handler(request):
        assert request.url.path.endswith("/getRTMSDataSvcAptRent")
        return httpx.Response(200, content=RENT)
    assert fetch(handler, kind="rent")["items"][0]["lease_type"] == "jeonse"


@pytest.mark.parametrize("override", [
    {"lawd_cd": "11680&serviceKey=bad"}, {"lawd_cd": "１１６８０"},
    {"deal_ymd": "202613"}, {"deal_ymd": "000010"}, {"kind": "other"},
    {"page_no": 0}, {"page_no": True}, {"num_of_rows": 101}, {"num_of_rows": 0},
])
def test_invalid_query_never_calls_upstream(override):
    def handler(request):
        pytest.fail("invalid query must not call transport")
    with pytest.raises(MolitError) as failure:
        fetch(handler, **override)
    assert failure.value.code == "INVALID_QUERY"


@pytest.mark.parametrize("key", ["", "   ", "unsafe\nkey", "double%252Bencoded"])
def test_absent_or_invalid_key_does_not_call_upstream(key):
    def handler(request):
        pytest.fail("invalid key must not call transport")
    with pytest.raises(MolitError):
        fetch(handler, key=key)


@pytest.mark.parametrize("status,expected", [
    (401, "MOLIT_AUTH_ERROR"), (403, "MOLIT_AUTH_ERROR"),
    (429, "MOLIT_RATE_LIMIT"), (503, "MOLIT_UPSTREAM_ERROR"),
    (504, "MOLIT_TIMEOUT"), (400, "MOLIT_HTTP_ERROR"),
])
def test_http_failures_no_automatic_retry_or_key_echo(status, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=FAKE_KEY.encode())
    with pytest.raises(MolitError) as failure:
        fetch(handler)
    assert failure.value.code == expected
    assert FAKE_KEY not in str(failure.value)
    assert len(calls) == 1


def test_redirect_never_forwards_credentials():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://untrusted.example/leak"})
    with pytest.raises(MolitError) as failure:
        fetch(handler)
    assert failure.value.code == "MOLIT_REDIRECT_REJECTED"
    assert len(calls) == 1


@pytest.mark.parametrize("error,expected", [(httpx.ReadTimeout, "MOLIT_TIMEOUT"), (httpx.ConnectError, "MOLIT_CONNECTION_ERROR")])
def test_transport_exceptions_suppress_secret_details(error, expected):
    def handler(request):
        raise error(f"failed URL with {FAKE_KEY}", request=request)
    with pytest.raises(MolitError) as failure:
        fetch(handler)
    assert failure.value.code == expected
    assert failure.value.__suppress_context__ is True
    assert FAKE_KEY not in str(failure.value)


@pytest.mark.parametrize("header", [False, True])
def test_response_size_is_bounded(header):
    def handler(request):
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1), headers={"content-length": str(MAX_RESPONSE_BYTES + 1)} if header else {})
    with pytest.raises(MolitError) as failure:
        fetch(handler)
    assert failure.value.code == "MOLIT_RESPONSE_TOO_LARGE"


def test_namespace_qualified_response():
    xml = TRADE.replace(b"<response>", b'<response xmlns="urn:molit:example">')
    assert parse(xml)["returned_count"] == 2
