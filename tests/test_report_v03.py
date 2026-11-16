"""Report evidence regressions using offline fixtures and fake credentials only.

These tests neither read local configuration nor contact a real provider. A
passing fixture-based report is not evidence of authenticated MOLIT access.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.molit import fetch_transactions
from scripts import check_api


FAKE_KEY = "offline-report-v03-key"
SENSITIVE_INVALID_ARGUMENT = "do-not-persist-invalid-query-input"


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Report regression tests must remain offline")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)

    def no_environment(*args, **kwargs):
        raise AssertionError("Report tests must not read local configuration")

    monkeypatch.setattr(Settings, "from_env", no_environment)


def run_report(tmp_path, args, settings):
    report_path = tmp_path / "report.json"
    exit_code = asyncio.run(check_api.check(args, settings=settings, report_path=report_path))
    return exit_code, json.loads(report_path.read_text(encoding="utf-8"))


def test_success_evidence_preserves_nondefault_query_and_narrow_live_scope(tmp_path, monkeypatch):
    args = argparse.Namespace(kind="both", lawd_cd="11110", month="202501")
    calls = []

    async def offline_page(**request):
        calls.append(request)
        return {
            "source": "molit", "synthetic": False, "kind": request["kind"],
            "lawd_cd": request["lawd_cd"], "deal_ymd": request["deal_ymd"],
            "page_no": request["page_no"], "num_of_rows": request["num_of_rows"],
            "total_count": 9, "returned_count": 5, "skipped_count": 0,
        }

    monkeypatch.setattr(check_api, "fetch_transactions", offline_page)
    exit_code, report = run_report(
        tmp_path, args, Settings(molit_trade_api_key=FAKE_KEY, molit_rent_api_key=FAKE_KEY),
    )

    assert exit_code == 0
    assert report["schema_version"] == "1.1"
    assert report["query"] == {
        "kind": "both", "lawd_cd": "11110", "deal_ymd": "202501", "page_no": 1, "num_of_rows": 5,
    }
    assert {request["kind"] for request in calls} == {"trade", "rent"}
    for request in calls:
        assert {key: request[key] for key in report["query"] if key != "kind"} == {
            key: value for key, value in report["query"].items() if key != "kind"
        }
    assert report["provenance"] == {
        "requested_mode": "live", "provider": "molit", "synthetic_fallback": False,
        "verification_scope": "single_page_authenticated_response",
    }
    assert FAKE_KEY not in json.dumps(report)


@pytest.mark.parametrize("invalid_field", ["kind", "lawd_cd", "month"])
def test_invalid_query_never_enters_shareable_evidence(tmp_path, monkeypatch, capsys, invalid_field):
    args = argparse.Namespace(kind="both", lawd_cd="11680", month="202610")
    setattr(args, invalid_field, SENSITIVE_INVALID_ARGUMENT)

    async def forbidden(**request):
        pytest.fail("An invalid query must never start a request")

    monkeypatch.setattr(check_api, "fetch_transactions", forbidden)
    exit_code, report = run_report(
        tmp_path, args, Settings(molit_trade_api_key=FAKE_KEY, molit_rent_api_key=FAKE_KEY),
    )

    assert exit_code == 1
    assert report["query"] is None
    assert report["status"] == "NOT_RUN"
    assert report["both_apis_verified"] is False
    assert SENSITIVE_INVALID_ARGUMENT not in json.dumps(report) + capsys.readouterr().out


def test_unattempted_query_records_intent_without_claiming_verification(tmp_path, monkeypatch):
    args = argparse.Namespace(kind="rent", lawd_cd="11110", month="202501")

    async def forbidden(**request):
        pytest.fail("Missing keys must not start a request")

    monkeypatch.setattr(check_api, "fetch_transactions", forbidden)
    exit_code, report = run_report(tmp_path, args, Settings())

    assert exit_code == 1
    assert report["query"]["kind"] == "rent"
    assert report["provenance"]["requested_mode"] == "live"
    assert report["status"] == "NOT_RUN"
    assert report["both_apis_verified"] is False
    assert report["services"]["trade"]["status"] == "NOT_REQUESTED"
    assert report["services"]["rent"]["status"] == "NOT_RUN"
    assert report["services"]["rent"]["total_count"] is None


@pytest.mark.parametrize("error_xml", [
    "<response><header><resultCode>30</resultCode><resultMsg>NODATA_ERROR</resultMsg></header></response>",
    "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode>"
    "<returnAuthMsg>NO_DATA</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>",
])
def test_provider_auth_error_with_no_data_message_cannot_verify_both_apis(tmp_path, monkeypatch, error_xml):
    """Exercise the real HTTP client/parser/report chain over an offline transport."""
    args = argparse.Namespace(kind="both", lawd_cd="11680", month="202610")
    requests = []

    def fake_provider(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=error_xml, headers={"Content-Type": "application/xml"})

    async def offline_fetch(**request):
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_provider)) as client:
            return await fetch_transactions(**request, client=client)

    monkeypatch.setattr(check_api, "fetch_transactions", offline_fetch)
    exit_code, report = run_report(
        tmp_path, args, Settings(molit_trade_api_key=FAKE_KEY, molit_rent_api_key=FAKE_KEY),
    )

    assert len(requests) == 2
    assert exit_code == 1
    assert report["status"] == "FAILED"
    assert report["both_apis_verified"] is False
    for result in report["services"].values():
        assert result["status"] == "FAILED"
        assert result["code"] == "MOLIT_AUTH_ERROR"
        assert result["total_count"] is None
