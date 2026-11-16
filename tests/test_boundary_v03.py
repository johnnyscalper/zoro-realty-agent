"""No-data boundary regressions using synthetic XML and fake credentials only.

The client/parser/report chain stays offline and never reads local settings.
Passing these cases is not evidence of authenticated MOLIT access.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.molit import MolitError, fetch_transactions, parse_transactions
from scripts import check_api


QUERY = dict(kind="trade", lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=5)
NO_DATA_HEADERS = [("03", "NODATA_ERROR"), ("000", "NO_DATA")]


@pytest.fixture(autouse=True)
def forbid_external_state(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Boundary tests must remain offline")

    def no_local_settings(*args, **kwargs):
        raise AssertionError("Boundary tests must not read local configuration")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    monkeypatch.setattr(Settings, "from_env", no_local_settings)


def response_xml(code, message, body=""):
    return (
        f"<response><header><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></header>{body}</response>"
    )


@pytest.mark.parametrize("code,message", NO_DATA_HEADERS)
@pytest.mark.parametrize("body", [
    "<body><items><item/></items><totalCount>2</totalCount></body>",
    "<body><items><item/></items><totalCount>0</totalCount></body>",
    "<body><items/><totalCount>2</totalCount></body>",
    "<body><items/><totalCount>unknown</totalCount></body>",
    "<body><items/><totalCount>-1</totalCount></body>",
    "<body><items/><totalCount/></body>",
])
def test_no_data_header_cannot_discard_contradictory_body(code, message, body):
    with pytest.raises(MolitError) as caught:
        parse_transactions(response_xml(code, message, body), **QUERY)
    assert caught.value.code == "MOLIT_INVALID_RESPONSE"


@pytest.mark.parametrize("code,message", NO_DATA_HEADERS + [("03", "NORMAL_SERVICE")])
@pytest.mark.parametrize("body", ["", "<body><items/><totalCount>0</totalCount></body>"])
def test_header_only_and_consistent_zero_no_data_replies_remain_supported(code, message, body):
    page = parse_transactions(response_xml(code, message, body), **QUERY)
    assert page["total_count"] == page["returned_count"] == page["skipped_count"] == 0
    assert page["items"] == []
    assert page["has_next"] is False


@pytest.mark.parametrize("code", ["03", "000"])
@pytest.mark.parametrize("error_field", ["returnAuthMsg", "errMsg"])
@pytest.mark.parametrize("token,expected", [
    ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "MOLIT_AUTH_ERROR"),
    ("LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "MOLIT_RATE_LIMIT"),
    ("SERVICETIMEOUT_ERROR", "MOLIT_TIMEOUT"),
])
def test_gateway_error_in_either_message_field_wins_over_no_data(code, error_field, token, expected):
    no_data_field = "errMsg" if error_field == "returnAuthMsg" else "returnAuthMsg"
    xml = (
        f"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>{code}</returnReasonCode>"
        f"<{error_field}>{token}</{error_field}>"
        f"<{no_data_field}>NODATA_ERROR</{no_data_field}>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    with pytest.raises(MolitError) as caught:
        parse_transactions(xml, **QUERY)
    assert caught.value.code == expected


@pytest.mark.parametrize("xml,expected", [
    (
        response_xml("03", "NODATA_ERROR", "<body><items><item/></items><totalCount>2</totalCount></body>"),
        "MOLIT_INVALID_RESPONSE",
    ),
    (
        "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>03</returnReasonCode>"
        "<returnAuthMsg>NODATA_ERROR</returnAuthMsg>"
        "<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>",
        "MOLIT_AUTH_ERROR",
    ),
])
def test_contradictory_no_data_never_verifies_both_apis(tmp_path, monkeypatch, capsys, xml, expected):
    requests = []

    def fake_provider(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=xml)

    async def offline_fetch(**request):
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_provider), trust_env=False) as client:
            return await fetch_transactions(**request, client=client)

    monkeypatch.setattr(check_api, "fetch_transactions", offline_fetch)
    report_path = tmp_path / "boundary-report.json"
    exit_code = asyncio.run(check_api.check(
        argparse.Namespace(kind="both", lawd_cd="11680", month="202610"),
        settings=Settings(molit_trade_api_key="offline-boundary-trade", molit_rent_api_key="offline-boundary-rent"),
        report_path=report_path,
    ))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(requests) == 2
    assert exit_code == 1
    assert report["status"] == "FAILED"
    assert report["both_apis_verified"] is False
    for entry in report["services"].values():
        assert entry["status"] == "FAILED"
        assert entry["code"] == expected
        assert entry["total_count"] is None
        assert entry["returned_count"] is None
    assert "매매·전월세 조회에 모두 정상 응답" not in capsys.readouterr().out
